"""Answering ``knowledge.search`` (FR-R1..R5, SEC-13).

Two paths to one response shape:

- :func:`hybrid_answer` ranks against a published retrieval index;
- :func:`substring_answer` scans the canonical store when no index can answer.

The fallback is not a nicety. A project that has applied migrations but not yet
run ``theurian index build`` would otherwise answer every query with nothing,
which an agent reads as "we have no such decision" rather than "ask me again in
a moment". An index is derived (ADR-0004), so its absence is a missing
optimisation and never a reason to refuse to answer.

A composition root: this module names concrete adapters (ADR-0003).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from theurian.application.project_service import (
    ProjectPaths,
    read_active_index,
    read_active_state,
)
from theurian.application.retrieval_service import (
    RetrievalService,
    SearchOutcome,
    SearchRequest,
)
from theurian.domain.context import RequestContext
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import ItemId, ProjectId, RevisionId
from theurian.domain.ranking import Fused, estimate_tokens, take_within_budget
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.index_store import IndexBuildError, SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore
from theurian.mcp.results import excerpt, may_surface, result_payload

#: Why the ranked path stood aside. Machine-readable, so a client can react
#: without parsing prose — "rebuild your index" and "you asked for drafts an
#: approved-only index does not hold" call for different next actions.
NO_INDEX: Final = "no-index"
INDEX_POINTER_INVALID: Final = "index-pointer-invalid"
INDEX_FILE_MISSING: Final = "index-file-missing"
INDEX_SCHEMA_MISMATCH: Final = "index-schema-mismatch"
INDEX_UNREADABLE: Final = "index-unreadable"
UNAPPROVED_NOT_INDEXED: Final = "unapproved-not-indexed"


@dataclass(frozen=True, slots=True)
class Fallback:
    """Why an index could not answer, carried rather than inferred.

    Every reason below once produced the same sentence — "no retrieval index has
    been built for this project" — which is true of exactly one of them. The rest
    told a user to run a command they had already run, and said nothing about the
    one that would actually have helped.
    """

    reason: str
    note: str


#: Each reason's note names the command that resolves *that* reason. Kept beside
#: its code so the pair cannot drift, and so a reader can grep one string from a
#: transcript and land on the branch that produced it.
_NOT_BUILT = Fallback(
    NO_INDEX,
    "No retrieval index has been built for this project, so this is an unranked "
    "substring scan. Run `theurian index build` for ranked hybrid retrieval.",
)
_POINTER_INVALID = Fallback(
    INDEX_POINTER_INVALID,
    "This project's index pointer does not name a usable build, so this is an "
    "unranked substring scan. Delete `.theurian/state/active-index.json` and run "
    "`theurian index build`; the index is derived, so nothing is lost.",
)
_FILE_MISSING = Fallback(
    INDEX_FILE_MISSING,
    "The published index build is no longer on disk, so this is an unranked "
    "substring scan. Run `theurian index build` to rebuild it.",
)
_SCHEMA_MISMATCH = Fallback(
    INDEX_SCHEMA_MISMATCH,
    "This project's index was built with a different index schema than this "
    "build of Theurian searches, so it cannot be used and this is an unranked "
    "substring scan. Run `theurian index build` to rebuild it; the index is "
    "derived, so nothing is lost.",
)
_UNREADABLE = Fallback(
    INDEX_UNREADABLE,
    "This project's index could not be read, so this is an unranked substring "
    "scan. Run `theurian index build` to rebuild it; the index is derived, so "
    "nothing is lost.",
)
_NO_DRAFTS_INDEXED = Fallback(
    UNAPPROVED_NOT_INDEXED,
    "This project's index was built without `--include-unapproved`, so it holds "
    "no drafts and cannot answer this query; this is an unranked substring scan. "
    "Run `theurian index build --include-unapproved` for ranked retrieval over "
    "unapproved knowledge.",
)


@dataclass(frozen=True, slots=True)
class _PublishedIndex:
    """A usable index build, and what it was built from."""

    path: Path
    build_id: str
    state_hash: str
    indexes_unapproved: bool


def _published_index(
    paths: ProjectPaths, *, include_unapproved: bool
) -> _PublishedIndex | Fallback:
    """Locate the index this project publishes, or say why there is not one.

    Never raises. Every failure here is a missing optimisation, and the caller
    answers from the canonical store instead.
    """
    published = read_active_index(paths)
    if not published:
        return _NOT_BUILT

    build_id = str(published.get("indexBuildId", ""))
    try:
        path = paths.index_for(build_id)
    except TheurianError:
        # `index_for` refuses an id that resolves outside the state directory.
        # `active-index.json` is derived, git-ignored, and unsigned, so any local
        # process can put `../` in it, and SEC-7 covers every path rather than
        # only the ones that look like user input. Its message is not passed
        # through: it names an absolute path, and this reply goes to a client.
        return _POINTER_INVALID

    if not path.is_file():
        # The pointer outlived its file. `theurian index status` reports it; here
        # it is simply an index that cannot be read.
        return _FILE_MISSING

    if not SqliteIndexStore(path).is_searchable():
        # Checked here rather than discovered mid-query, because mid-query it
        # cannot be discovered at all: an older schema is missing tables, and a
        # query against a missing table looks exactly like a query that matched
        # nothing. That is not theoretical -- the index schema went to v2 to add
        # `chunks_trigram`, which is the only retriever that can segment
        # Japanese, so a v1 file left behind by an upgrade answered every
        # Japanese query with silence while still reporting `indexed: true`.
        #
        # Falling back rather than raising, and that is the deliberate choice.
        # The index is derived (ADR-0004): an unusable one is a missing
        # optimisation, and refusing to answer would take away the very fallback
        # that exists so a project without an index still gets an answer. What
        # the fallback must do -- and now does -- is *say* which of these
        # happened, so "rebuild your index" is never mistaken for "we have no
        # such decision".
        return _SCHEMA_MISMATCH

    indexes_unapproved = bool(published.get("indexesUnapproved", False))
    if include_unapproved and not indexes_unapproved:
        # An index built without `--include-unapproved` holds no drafts, so a
        # query asking for them cannot be answered from it. Falling back is the
        # honest answer; returning approved-only results would change the meaning
        # of a published parameter without saying so.
        return _NO_DRAFTS_INDEXED

    return _PublishedIndex(
        path=path,
        build_id=build_id,
        state_hash=str(published.get("stateHash", "")),
        indexes_unapproved=indexes_unapproved,
    )


def hybrid_answer(  # noqa: PLR0913 - one keyword per published tool parameter
    paths: ProjectPaths,
    database: Path,
    *,
    project_id: str,
    query: str,
    limit: int,
    include_unapproved: bool,
    budget_tokens: int,
    use_dense: bool,
) -> dict[str, Any] | Fallback:
    """Answer from the retrieval index, or say why it could not."""
    published = _published_index(paths, include_unapproved=include_unapproved)
    if isinstance(published, Fallback):
        return published

    active = read_active_state(paths)
    stale = published.state_hash != (str(active.state_hash) if active else "")

    index = SqliteIndexStore(published.path)
    try:
        outcome = RetrievalService(index, HashingEmbedding()).search(
            SearchRequest(
                query=query,
                project_id=project_id,
                budget_tokens=budget_tokens,
                limit=limit,
                include_unapproved=include_unapproved,
                use_dense=use_dense,
                # One chunk per document. This tool returns one result per
                # document, and the collapse has to happen here — before `limit`,
                # before the budget — rather than on the results. A second chunk
                # of the same document dropped afterwards has already taken a
                # result slot and already been charged tokens, so collapsing late
                # costs recall and reports a `usedTokens` the caller never
                # received.
                per_item=1,
            )
        )
        passages = index.chunk_texts(
            [candidate.chunk_id for candidate in outcome.candidates], project_id=project_id
        )
    except IndexBuildError:
        # The file passed the version check and then could not answer: a
        # truncated copy, a dropped table, a metadata row that outlived the
        # tables it describes. The version gate above is the check that should
        # catch this; this is what makes "never answer from a broken index"
        # true even when it does not.
        return _UNREADABLE

    results, superseded = _resolve_through_canonical(
        database,
        project_id=project_id,
        candidates=outcome.candidates,
        passages=passages,
        include_unapproved=include_unapproved,
    )

    return {
        "projectId": project_id,
        "query": query,
        "count": len(results),
        "results": results,
        "retrieval": _index_report(published, outcome, stale=stale, superseded=superseded),
    }


def _resolve_through_canonical(
    database: Path,
    *,
    project_id: str,
    candidates: Sequence[Fused],
    passages: Mapping[str, str],
    include_unapproved: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Turn ranked chunks into results, through the canonical store (FR-R5).

    The index is never authoritative. A result assembled from it alone could
    outlive the revision it describes, and both checks below are how a stale
    index returns *fewer* results rather than wrong ones.

    Returns:
        The results, and how many were withheld because the index still pins a
        revision that has since been replaced.
    """
    now = datetime.now(UTC)
    context = RequestContext(project_id=ProjectId(project_id))
    results: list[dict[str, Any]] = []
    superseded = 0

    with SqliteCanonicalStore(database) as store:
        for candidate in candidates:
            revision = store.get_revision(context, RevisionId(candidate.revision_id))
            if revision is None:
                continue
            item = store.get_item(context, ItemId(candidate.item_id))
            if item is None:  # pragma: no cover - the index mirrors the store
                continue
            # The index's `status` is a build-time snapshot; the canonical store
            # is the authority for what is approved *now*. Checked on both paths:
            # guarding this with `not include_unapproved` once let the opt-in
            # path skip status entirely, so an item retired after the build came
            # back labelled `deprecated` — or `rejected`, which is where the
            # secret that caused the rejection still lives.
            if not may_surface(item.status, include_unapproved=include_unapproved):
                continue
            # Likewise for *which revision* is current. Replacing a revision is
            # how a secret gets removed from approved knowledge, so serving the
            # pinned one would keep answering with the very text the team just
            # retracted, under the new revision's `approved` label.
            if item.current_revision_id != revision.revision_id:
                superseded += 1
                continue

            result = result_payload(revision, item.status, now)
            # The excerpt is the passage that actually matched, not the head of
            # the document. Chunking buys ranking precision; without this the
            # caller never sees the paragraph it bought.
            passage = passages.get(candidate.chunk_id, "")
            if passage:
                result["excerpt"] = excerpt(passage)
            result["fusedScore"] = round(candidate.fused_score, 6)
            result["foundBy"] = list(candidate.found_by)
            results.append(result)

    return results, superseded


def _index_report(
    published: _PublishedIndex, outcome: SearchOutcome, *, stale: bool, superseded: int
) -> dict[str, Any]:
    """How the answer was produced, for a client that has to act on it."""
    return {
        "mode": outcome.mode.value,
        "indexed": True,
        # Reported, because only the CLI knew this and the client is the one
        # acting on the answer. A stale index is a correctness problem wearing
        # the costume of a relevance problem.
        #
        # Named for what it compares. The index is checked against the
        # *database*, not against the repository's migrations — deriving the
        # latter means re-reading every migration on every search. `theurian
        # index status` does compare all three, and will report a database that
        # is itself behind, which this cannot.
        "stale": stale,
        "staleAgainst": "builtState",
        # Withheld because the index still points at a revision that has since
        # been replaced. Reported so a caller can tell "no such decision" from
        # "your index is behind".
        "withheldSuperseded": superseded,
        "indexesUnapproved": published.indexes_unapproved,
        "indexBuildId": published.build_id,
        "embeddingModel": outcome.embedding_model,
        "usedTokens": outcome.used_tokens,
        "droppedForBudget": outcome.dropped_for_budget,
        "note": (
            "This index was built from an earlier knowledge state. Run "
            "`theurian index build` to refresh it."
            if stale
            else "Ranked by reciprocal rank fusion over lexical and dense "
            "retrievers. `foundBy` names which retrievers surfaced each hit."
        ),
    }


def substring_answer(  # noqa: PLR0913 - one keyword per published tool parameter
    database: Path,
    *,
    project_id: str,
    query: str,
    limit: int,
    include_unapproved: bool,
    budget_tokens: int,
    fallback: Fallback,
) -> dict[str, Any]:
    """Answer by scanning the canonical store, when no index can.

    Unranked, and bounded twice: by ``limit``, and by the caller's token budget.
    The second bound is not decoration. FR-R4 is a promise about every answer,
    and this path used to ignore it entirely — fifty results with their
    provenance and trust labels are several thousand tokens handed to a caller
    who asked for five hundred, and an agent that receives more context than it
    asked for has already paid for it.
    """
    matches = _scan(
        database,
        project_id=project_id,
        needle=query.strip().lower(),
        limit=limit,
        include_unapproved=include_unapproved,
    )
    kept, used = take_within_budget(
        [_payload_cost(match) for match in matches], budget_tokens=budget_tokens
    )
    results = matches[:kept]

    return {
        "projectId": project_id,
        "query": query,
        "count": len(results),
        "results": results,
        "retrieval": {
            "mode": "substring",
            "indexed": False,
            "indexesUnapproved": False,
            "fallbackReason": fallback.reason,
            "usedTokens": used,
            "droppedForBudget": len(matches) - kept,
            "note": fallback.note,
        },
    }


def _scan(
    database: Path,
    *,
    project_id: str,
    needle: str,
    limit: int,
    include_unapproved: bool,
) -> list[dict[str, Any]]:
    """Every current revision whose title or body contains ``needle``.

    Stops at ``limit`` so an unranked scan cannot walk a whole corpus to build
    an answer the budget will discard anyway.
    """
    now = datetime.now(UTC)
    context = RequestContext(project_id=ProjectId(project_id))
    matches: list[dict[str, Any]] = []

    with SqliteCanonicalStore(database) as store:
        for item in store.list_items(context):
            if not may_surface(item.status, include_unapproved=include_unapproved):
                continue
            if item.current_revision_id is None:
                continue
            revision = store.get_revision(context, item.current_revision_id)
            if revision is None:  # pragma: no cover - the pointer is a foreign key
                continue
            if needle not in f"{revision.title}\n{revision.body}".lower():
                continue

            matches.append(result_payload(revision, item.status, now))
            if len(matches) >= limit:
                break

    return matches


def _payload_cost(result: Mapping[str, Any]) -> int:
    """What one result will cost the caller, priced on what is actually sent.

    The whole serialised object, not the excerpt alone: provenance, trust labels,
    and source anchors travel with every hit and are a real share of a small
    budget. `estimate_tokens` errs high on top of that, which is the side to err
    on — exceeding a budget silently truncates the caller's own instructions.

    The ranked path prices a *chunk* instead, because a chunk is the unit it
    retrieves and pins. Both over-estimate; neither can under-charge.
    """
    return estimate_tokens(json.dumps(result, ensure_ascii=False))
