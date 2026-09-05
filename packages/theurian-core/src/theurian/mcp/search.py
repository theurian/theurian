"""Answering ``knowledge.search`` (FR-R1..R5, SEC-13).

Two paths to one response shape:

- :func:`hybrid_answer` ranks against a published retrieval index;
- :func:`substring_answer` scans the canonical store when no index can answer.

The fallback is not a nicety. A project that has applied migrations but not yet
run ``theurian index build`` would otherwise answer every query with nothing,
which an agent reads as "we have no such decision" rather than "ask me again in
a moment". An index is derived (ADR-0004), so its absence is a missing
optimisation and never a reason to refuse to answer.

**The invariant this module exists to hold (SEC-13, T-15): nothing derived from
withheld content may appear in the response — not a count, not a token total,
not a mode, not a score.**

It is not enough to leave withheld hits out of ``results``. A response saying
``count: 0, results: [], usedTokens: 46`` states that something matched and may
not be read, and the trigram retriever matches any substring of three characters
or more, so that statement is not existence detection but sequential extraction:
guess a character, ask, keep it if the number moves. Measured on this code, 257
ordinary ``knowledge.search`` calls — no ``includeUnapproved`` — recovered a
twenty-character credential from a document whose superseding revision had
redacted it. The precondition was only that the index was older than the
redaction, which is the normal state between ``migrate apply`` and ``index
build``: the window opened by performing the remediation is the window in which
the plaintext could be read back.

Closing it moved the accounting to this layer, and that was the wrong axis. The
caller's ``limit`` stayed on the candidate list, so a withheld document still
consumed a result slot: 203 calls, same recovery, through ``count`` instead.
Moving it back and re-fusing after the gate closed that one and left another —
the *rows* were still read fifty at a time before anyone asked who may see them,
so a withheld row took a slot and 442 calls recovered the credential again, this
time at the default budget.

What finally had to change was not which layer counts but *when* the canonical
store is asked. It is now asked while the retrievers are being read, so this
module never sees a candidate that was ranked against withheld content:
:class:`theurian.application.retrieval_service.ResultGate` hands its
:class:`~theurian.application.visibility.Visibility` to the retrieval it drives,
and hands back a :class:`~theurian.application.retrieval_service.Resolved`.
Everything this module publishes is read off that object, off the index, or off
the caller's own parameters.

``test_a_withheld_document_changes_nothing_a_caller_can_see``, in
``tests/integration/test_mcp_tools.py``, is what holds that claim, and it was
checked by breaking it rather than by reading it: ranking :func:`hybrid_answer`'s
candidates through a visibility that withholds nothing turns all twenty of its
parametrisations red, the first on a published hit carrying the retired
document's credential.

What is left here is the part that genuinely belongs to a tool surface: which
index may answer at all and what to say when none can, the wire shape of a hit,
and the wiring of concrete adapters into the application layer.

A composition root: this module names concrete adapters (ADR-0003).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from theurian.application.authorization import ProfileVerdict, recorded_flavor_verdict
from theurian.application.project_service import (
    INDEX_POINTER_REMEDY,
    BuildProvenance,
    ProjectPaths,
    read_active_index_pointer,
)
from theurian.application.retrieval_service import (
    Resolved,
    ResultGate,
    ResultRequest,
    ResultShaper,
    RetrievalService,
    SearchOutcome,
    SearchRequest,
    Surfaced,
    within_budget,
)
from theurian.application.visibility import Visibility
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeStatus, Sensitivity, may_surface
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import ProjectId
from theurian.domain.ranking import RetrievalMode, estimate_tokens, mode_of
from theurian.domain.state import ActiveState
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.index_store import IndexBuildError, SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore
from theurian.mcp.results import excerpt, result_payload

#: Why the ranked path stood aside. Machine-readable, so a client can react
#: without parsing prose — "rebuild your index" and "you asked for drafts an
#: approved-only index does not hold" call for different next actions.
NO_INDEX: Final = "no-index"
INDEX_POINTER_INVALID: Final = "index-pointer-invalid"
INDEX_FILE_MISSING: Final = "index-file-missing"
INDEX_SCHEMA_MISMATCH: Final = "index-schema-mismatch"
INDEX_UNREADABLE: Final = "index-unreadable"
INDEX_PROJECT_MISMATCH: Final = "index-project-mismatch"
INDEX_UNBUILT: Final = "index-unbuilt"
UNAPPROVED_NOT_INDEXED: Final = "unapproved-not-indexed"
#: Spelled `serving-profile-` and not `index-profile-`, which would sit one
#: letter from `index-project-mismatch` in every transcript and every client's
#: switch statement.
SERVING_PROFILE_MISMATCH: Final = "serving-profile-mismatch"
#: The published build still holds rows a withdrawal removed from canonical state,
#: because the purge that would have removed them from the index did not complete
#: (GHSA-97q9-xxfg-33r6, T-17a). Distinct from every reason above: this build is
#: this project's, of the right schema, built here, under the profile in force --
#: and unusable anyway, because serving it prices visible rows against withheld
#: text and a `--raptor` build carries that text verbatim in a sibling's path.
INDEX_PURGE_FAILED: Final = "index-purge-failed"


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
    # The cure is imported rather than retyped: `theurian index status` names
    # the same file for the same reason through
    # `application.project_service.INDEX_POINTER_REMEDY`, and a hand-written
    # copy here is exactly how the two surfaces drifted before -- one kept the
    # bare path, the other quoted it in backticks it did not share with
    # anything. `INDEX_POINTER_REMEDY` sets the formatting for both; this
    # framing sentence is the only part that may read differently, because an
    # agent holding a `note` and an operator reading `remedy` are different
    # readers of the same fact.
    "This project's index pointer does not name a usable build, so this is an "
    f"unranked substring scan. {INDEX_POINTER_REMEDY}",
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
#: Two notes, one reason code, because a client's next action is the same for
#: both — rebuild — while a person reading the transcript needs to know whether
#: an id changed under the index or was never recorded at all. Telling every user
#: upgrading from a build that predates `projectId` that their index "was built
#: for a different project id" would send them hunting for a rename that never
#: happened, which is the failure the reason codes exist to prevent.
#:
#: Neither names the *other* id. `theurian index status` prints it, because that
#: is an operator at their own terminal; this reply goes to an agent holding one
#: project's id, and the remedy is the same command either way.
_PROJECT_MISMATCH = Fallback(
    INDEX_PROJECT_MISMATCH,
    "This project's index was built for a different project id, so none of its "
    "content is in scope for this query and this is an unranked substring scan. "
    "Run `theurian index build` to rebuild it for this project.",
)
_PROJECT_UNVERIFIED = Fallback(
    INDEX_PROJECT_MISMATCH,
    "This project's index does not record which project it was built for, so it "
    "cannot be shown to hold this project's knowledge and this is an unranked "
    "substring scan. Run `theurian index build`; one rebuild records the id, and "
    "the index is derived, so nothing is lost.",
)
#: The index this pointer names was not built by this installation (ADR-0004,
#: SEC-7). Standing aside rather than refusing outright: the ranked path degrades
#: to the unranked canonical scan, which is itself provenance-gated at
#: `_resolve`, so a doctored index never serves its own bytes and a query on a
#: legitimately-built project whose *index* was tampered with still answers from
#: the trusted canonical store. Distinct from `index-project-mismatch`, which is
#: about *which project* an index that this install did build was built for.
_UNBUILT_INDEX = Fallback(
    INDEX_UNBUILT,
    "This project's retrieval index was not built by this Theurian installation, so "
    "it will not be used and this is an unranked substring scan over the canonical "
    "store. Run `theurian index build` to rebuild it locally; the index is derived, "
    "so nothing is lost (ADR-0004).",
)
_NO_DRAFTS_INDEXED = Fallback(
    UNAPPROVED_NOT_INDEXED,
    "This project's index was built without `--include-unapproved`, so it holds "
    "no drafts and cannot answer this query; this is an unranked substring scan. "
    "Run `theurian index build --include-unapproved` for ranked retrieval over "
    "unapproved knowledge.",
)
#: Two notes and one reason code, the arrangement `index-project-mismatch` is in
#: and for the same trade: the next action is `theurian index build` either way,
#: while a person reading the transcript needs to know whether the deployment's
#: profile moved under an index or was never recorded against it at all.
#:
#: Neither names a level -- not the one the build was made under and not the one
#: in force. The remedy does not depend on which, and this reply goes to a caller
#: that has no business learning the shape of the deployment's ceiling from a
#: degraded search. `theurian index status` is where an operator at their own
#: terminal *is* told, the same split `_PROJECT_MISMATCH` makes for ids: it
#: publishes `profileMismatch`, `profileUnrecorded`, and both level sets by name.
#:
#: That sentence was written before the command implemented it, and read as a
#: promise for one release: `index status` reported `stale: false` with an empty
#: remedy for a build every query here was degrading. Both surfaces now read one
#: answer (`application.authorization.recorded_flavor_verdict`), so the split is
#: a division of what each reader is told rather than of who bothered to check.
_PROFILE_MISMATCH = Fallback(
    SERVING_PROFILE_MISMATCH,
    "This project's index was built under a different disclosure profile than this "
    "deployment serves, so it cannot be used and this is an unranked substring "
    "scan. Run `theurian index build` to rebuild it under the profile in force "
    "now; the index is derived, so nothing is lost.",
)
_PROFILE_UNRECORDED = Fallback(
    SERVING_PROFILE_MISMATCH,
    "This project's index does not record which disclosure profile it was built "
    "under, so it cannot be shown to hold only what this deployment serves and "
    "this is an unranked substring scan. Run `theurian index build`; one rebuild "
    "records the profile, and the index is derived, so nothing is lost.",
)
#: The published build still holds rows a withdrawal removed from canonical state
#: because its purge did not complete (GHSA-97q9-xxfg-33r6, T-17a), so it is not
#: served: ranking against it prices visible rows on withheld text, and a
#: `--raptor` build carries that text verbatim into a visible sibling's path.
_PURGE_FAILED = Fallback(
    INDEX_PURGE_FAILED,
    "This project's index still holds rows a withdrawal removed from the "
    "knowledge state, because the purge that follows a withdrawal did not "
    "complete, so the build is not served and this is an unranked substring "
    "scan. Run `theurian index build` to produce a clean build; the index is "
    "derived, so nothing is lost.",
)


@dataclass(frozen=True, slots=True)
class _Retrieval:
    """The ``retrieval`` block. One shape, both answer paths.

    Every key appears on both, and one that cannot apply carries ``null`` rather
    than being absent. The two paths used to publish different key sets —
    ``stale`` / ``staleAgainst`` / ``indexBuildId`` / ``embeddingModel`` on the
    ranked one, ``fallbackReason`` on the other — which makes a client branch on
    key *presence*, and the branch a client does not exercise is the one that
    runs the first time an index goes missing in production. ``null`` is falsy,
    so ``if retrieval["stale"]`` and ``if retrieval["fallbackReason"]`` each read
    correctly on the path where the field does not apply.

    Written as a type rather than two dict literals for the same reason
    :func:`theurian.mcp.results.result_payload` is one: a shape that is
    constructed in two places drifts in one of them.
    """

    #: Which retrievers are behind the results being returned (never behind the
    #: candidates that produced them — see :func:`_retrievers_behind`).
    mode: str
    indexed: bool
    indexes_unapproved: bool
    used_tokens: int
    dropped_for_budget: int
    note: str
    #: Which canonical state answered (FR-R5) — the exact string
    #: ``knowledge.status`` already publishes as ``stateHash``, so a caller
    #: holding one can compare it against the other without a second call.
    #: Response scope, not per hit: every hit in one response was resolved
    #: through the same store (:class:`ResultGate` opens one connection per
    #: request), so a per-hit copy would only repeat one string once per
    #: result — the reasoning already applied to ``indexBuildId``.
    #:
    #: Query-independent by construction (SEC-13, T-15), which is what makes it
    #: safe to add here: it is the same value for every query against this
    #: state, so — unlike the per-query ``withheldSuperseded`` count this
    #: module removed — it can never answer "did this query match something
    #: withheld?" No default: every call site states it explicitly rather than
    #: inheriting one silently, the same bar the schema now holds every key on
    #: this block to.
    #:
    #: Never empty. It is the pointer the *tool* already resolved to choose the
    #: database, carried down rather than re-read, so it names the state these
    #: results actually came from. Re-reading admitted two failures a caller
    #: could not see: a pointer replaced by `migrate apply` mid-request made this
    #: field name a state the results did not come from, and a pointer deleted
    #: mid-request made it `null` — which the schema still permits, and no longer
    #: has to.
    snapshot_id: str
    stale: bool | None = None
    stale_against: str | None = None
    index_build_id: str | None = None
    embedding_model: str = ""
    fallback_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "indexed": self.indexed,
            "stale": self.stale,
            "staleAgainst": self.stale_against,
            "indexesUnapproved": self.indexes_unapproved,
            "indexBuildId": self.index_build_id,
            "embeddingModel": self.embedding_model,
            "fallbackReason": self.fallback_reason,
            "snapshotId": self.snapshot_id,
            "usedTokens": self.used_tokens,
            "droppedForBudget": self.dropped_for_budget,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class _PublishedIndex:
    """A usable index build, and what it was built from."""

    path: Path
    build_id: str
    state_hash: str
    indexes_unapproved: bool


def _published_index(  # noqa: PLR0911 - one return per distinguishable fallback
    paths: ProjectPaths,
    *,
    project_id: str,
    include_unapproved: bool,
    visible_sensitivities: frozenset[Sensitivity],
    provenance: BuildProvenance,
) -> _PublishedIndex | Fallback:
    """Locate the index this project publishes, or say why there is not one.

    Never raises. Every failure here is a missing optimisation, and the caller
    answers from the canonical store instead.

    ``visible_sensitivities`` is the deployment's grant, and it is checked against
    the *build* rather than against any row: which levels a build was allowed to
    write decides which rows exist in the file at all (#119 phase 3), and an index
    whose flavor disagrees with the grant in force is stood aside whole rather
    than filtered.
    """
    pointer = read_active_index_pointer(paths)
    if pointer.payload is None:
        # A pointer file that does not name a build — truncated, empty, a JSON
        # array, an object without `indexBuildId` — is reported apart from
        # "never built", because only one of the two remedies works. This one
        # has to delete the file first, and telling a user who *has* built an
        # index to build it again sends them round the loop that produced it.
        return _POINTER_INVALID if pointer.unreadable else _NOT_BUILT

    published = pointer.payload
    # Ahead of every other gate, unconditionally. A build whose withdrawal purge
    # failed still holds rows a migration removed from canonical state
    # (`mark_active_index_purge_failed`, GHSA-97q9-xxfg-33r6): the file is this
    # project's, of the right schema, provenanced and under the profile in force,
    # so every gate below would pass it -- and serving it prices visible rows
    # against withheld text (T-17a) while a `--raptor` build carries that text
    # verbatim into a visible sibling's `raptorPath`. There is no read-time filter
    # for either; the build must not answer at all, so this is checked before the
    # id, the file, provenance, project and flavor gates rather than after them.
    if published.get("purgeFailed"):
        return _PURGE_FAILED

    build_id = str(published.get("indexBuildId", ""))
    path = _searchable_file(paths, build_id)
    if isinstance(path, Fallback):
        return path

    # Ordered after `_searchable_file` so a *corrupt* pointer -- one naming a
    # build id that escapes the project, or no file at all -- still gets its own
    # structural diagnosis rather than this trust one. Reached only once the id
    # names a real, in-project, readable index; the remaining question is whether
    # this installation built it (ADR-0004, SEC-7). The index database carries
    # titles, bodies and excerpts, so a doctored one shipped in a cloned or
    # downloaded repository is a disclosure vector of its own, and the pointer
    # that names it is derived and unsigned -- its own `stateHash` cannot vouch
    # for it. Standing aside here degrades to the canonical scan, which
    # `_resolve` has already provenance-gated, so the doctored index never serves
    # its bytes.
    if not provenance.has_index(paths.root, build_id):
        return _UNBUILT_INDEX

    # Every chunk is stamped with the project id that built it and every
    # retrieval query scopes on that id, so an index built for another project
    # matches nothing while reporting itself healthy: `count: 0, indexed: true,
    # mode: none`, which an agent reads as "this team has made no such decision".
    # The ordinary way to reach it is a legitimate rename — `project unregister`,
    # re-register under a new id, no rebuild.
    #
    # A pointer that records no id at all predates the field, so which project
    # built it cannot be established, and it is treated the same way. `theurian
    # index status` calls that orphaned for the same reason, and the two surfaces
    # are deliberately consistent: a user must not be told the index is orphaned
    # by one and healthy by the other. One rebuild makes the claim checkable,
    # which is cheaper than asserting a freshness neither surface established.
    #
    # Checked ahead of the drafts gate below, because an index that is not this
    # project's cannot meaningfully be judged on what it holds.
    recorded = published.get("projectId")
    if recorded != project_id:
        # A blank or non-string id names no project either — the pointer is
        # unsigned and any local process can write it — so it takes the note
        # that does not claim a rename happened.
        names_a_project = isinstance(recorded, str) and bool(recorded)
        return _PROJECT_MISMATCH if names_a_project else _PROJECT_UNVERIFIED

    # The build's disclosure flavor against the one in force (#119, ADR-0025
    # part 1). Ahead of the drafts gate because it does not depend on what was
    # asked for: `includeUnapproved` is a request parameter a caller can stop
    # passing, while this build is the wrong build for this deployment on every
    # query it will ever be asked.
    #
    # The comparison itself lives in `application.authorization`, because
    # `theurian index status` has to report as stale exactly what this refuses --
    # and written out twice, the two disagreed: the status command answered
    # "fresh, nothing to do" for a build every query here was degrading. Its
    # docstring carries the reasoning for the equality and for both directions.
    verdict = recorded_flavor_verdict(
        published.get("indexedSensitivities"), served=visible_sensitivities
    )
    if verdict is ProfileVerdict.UNRECORDED:
        return _PROFILE_UNRECORDED
    if verdict is ProfileVerdict.MISMATCH:
        return _PROFILE_MISMATCH

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


def _searchable_file(paths: ProjectPaths, build_id: str) -> Path | Fallback:
    """The file this build id names, or why it cannot be searched at all.

    Split from its caller, which now asks two separable questions: *can this file
    be read*, answered here, and *is it the right index for this request* —
    project id, draft coverage — answered there. The first is a property of the
    file alone, so a reason from here holds no matter who is asking.
    """
    try:
        path = paths.index_for(build_id)
        present = path.is_file()
    except (TheurianError, OSError):
        # Two families, one answer, and the `OSError` is the half #388 measured
        # missing. `index_for` refuses an id that resolves outside the state
        # directory -- `active-index.json` is derived, git-ignored and unsigned,
        # so any local process can put `../` in it (SEC-7) -- but it cannot
        # refuse one that is merely too long, because `Path.resolve()` in
        # non-strict mode never stats. An `indexBuildId` of 234 characters or
        # more therefore came back from it as a `Path` whose `is_file()` raised
        # `ENAMETOOLONG`, and `mcp/tools.py`'s `except TheurianError` boundary
        # does not catch an `OSError`: measured, `knowledge.search` answered the
        # SDK's `UnexpectedToolError` -- "Error executing tool" with no remedy at
        # all -- for a pointer this file exists to fall back past.
        #
        # `_POINTER_INVALID` rather than `_FILE_MISSING`, because "the published
        # build is no longer on disk" is a claim about a build that was named,
        # and nothing here established that one was. The message from the
        # refusal is not passed through either: it names an absolute path, and
        # this reply goes to a client.
        return _POINTER_INVALID

    if not present:
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

    return path


def hybrid_answer(  # noqa: PLR0913 - one keyword per published tool parameter
    paths: ProjectPaths,
    database: Path,
    *,
    state: ActiveState,
    project_id: str,
    query: str,
    limit: int,
    include_unapproved: bool,
    visible_sensitivities: frozenset[Sensitivity],
    budget_tokens: int,
    use_dense: bool,
    as_of: datetime | None,
    provenance: BuildProvenance,
) -> dict[str, Any] | Fallback:
    """Answer from the retrieval index, or say why it could not.

    ``state`` is passed in rather than read here, and that is the whole of what
    makes ``snapshotId`` true. It is the pointer that chose ``database``, so the
    hash reported names the state the results actually came from even if
    ``migrate apply`` replaces the pointer mid-request.

    ``as_of`` is the parsed form of `knowledge.search`'s optional ``asOf``
    (FR-R1, #63 phase 2). It reaches two places for two different reasons: the
    gate below, where ``None`` means "no additional validity-window filter"
    and a moment is checked against ``item.validity``, once, on the far side
    of `CanonicalVisibility`'s own depth-doubling loop -- see
    :meth:`~theurian.application.visibility.Visibility.at_moment` for why it
    is never folded into the check that loop's exit condition watches; and
    :func:`_shaper`, where it is the moment every returned hit's ``freshness``
    is computed against -- ``datetime.now(UTC)`` when the caller pinned
    nothing, exactly as before this parameter existed.

    ``visible_sensitivities`` is the deployment's grant (#119), and it reaches
    three places that are not the same check. :func:`_published_index` compares it
    against the flavor the *build* was made under and stands the whole index aside
    on a mismatch; every retriever takes it as a WHERE predicate emitted with the
    match, so no above-ceiling row in the file is ranked (phase 4); and the
    canonical gate then re-checks each surviving candidate against the item's
    *current* level, which is what withholds a document reclassified upward since
    the build even though its chunk row was legitimately written. None subsumes
    another: the first decides which build may be read at all, the second which of
    its rows may be scored against each other, the third which of those may be
    returned. Only the third sees a reclassification, and only the first takes
    back what a wider build's collection statistics have already priced.
    """
    published = _published_index(
        paths,
        project_id=project_id,
        include_unapproved=include_unapproved,
        visible_sensitivities=visible_sensitivities,
        provenance=provenance,
    )
    if isinstance(published, Fallback):
        return published

    # Used twice: staleness compares the index's build-time hash against this,
    # and `snapshot_id` below is this unmodified, because it names the state the
    # *canonical store* answered from rather than the index. `substring_answer`
    # reports the same value for the same reason, and it must be identical to
    # `knowledge.status.stateHash` (query-independent, SEC-13/T-15 — see
    # `_Retrieval.snapshot_id`).
    current_state_hash = str(state.state_hash)
    stale = published.state_hash != current_state_hash

    # One store per request, and one *connection* per request inside it.
    #
    # The fresh-instance rule used to exist for `SqliteIndexStore._scan_cache`,
    # which would otherwise have leaked one caller's withheld-row count into
    # another caller's latency. That cache is gone (#16) and its reason with it;
    # `session()` below is what replaces the *reason* rather than only the rule.
    # It holds one read connection for the whole request, so the several index
    # reads a search makes cannot straddle a `theurian index gc` that unlinks the
    # build between them (ADR-0024 decision 7). Measured, one request of four
    # index reads with the unlink after the first: 1 of 4 answered with a
    # connection per call, 4 of 4 inside a session -- and the per-call path left
    # an empty database recreated at the reaped path, which is what made the
    # failure permanent rather than transient.
    index = SqliteIndexStore(published.path)
    service = RetrievalService(index, HashingEmbedding())
    search = SearchRequest(
        query=query,
        project_id=project_id,
        # The same grant `ResultRequest` below carries, and the third place it
        # reaches (#119 phase 4): `_published_index` judged the *build*, the
        # canonical gate re-checks each candidate's *current* level, and this is
        # the retrievers' own WHERE predicate over the rows in between.
        visible_sensitivities=visible_sensitivities,
        include_unapproved=include_unapproved,
        use_dense=use_dense,
        # One chunk per document. This tool returns one result per document, and
        # the collapse has to happen in the ranking rather than on the results: a
        # second chunk of the same document dropped afterwards has already taken
        # a result slot, so collapsing late costs recall.
        per_item=1,
    )

    def candidates(visible: Visibility) -> SearchOutcome:
        """Retrieval, run inside the gate's session and through its visibility.

        A closure rather than a list handed over, so the visibility exists at the
        point where candidates come into existence rather than after them.

        **That is a shape, not a guarantee, and this docstring used to claim the
        guarantee.** It said the gate *cannot* be given candidates ranked without
        asking who may see them, because there is nowhere to put such a list.
        There is: a source may ignore its parameter. Measured against a real
        project whose index still held a deprecated document —
        ``admit(request, lambda _visible: precomputed)`` published that document's
        credential, ``status: deprecated``, ``usedTokens: 220``, where the shipped
        path answered ``count: 0``.
        :data:`~theurian.application.retrieval_service.CandidateSource` records
        the same limit from the other side.

        What holds *this* call site to using its visibility is
        ``test_a_withheld_document_changes_nothing_a_caller_can_see``
        (``tests/integration/test_mcp_tools.py``), red in all twenty
        parametrisations when this function ranks through a visibility that
        withholds nothing.
        """
        return service.search(search, visible)

    # The session covers every index read in the block below. It does **not**
    # cover `_searchable_file`'s `is_searchable()`, which ran earlier on its own
    # connection -- so a request opens the index twice, once to decide whether it
    # is usable and once to read it, and only the second is held. That first open
    # is what leaves the window this nesting exists to survive.
    try:
        # Acquired *inside* the `try`, and the nesting is the whole of it.
        # Acquiring the session opens the file, which can fail: `theurian index
        # gc` may have unlinked the build between the check above and this line,
        # a window measured at 3-18 us. With the `with` outside, that
        # `IndexUnreadableError` escaped `except IndexBuildError` and reached the
        # agent as a tool error -- where the same window on the previous design
        # degraded cleanly to the substring scan. A change made for resilience
        # must not remove a fallback.
        with index.session():
            # Built before the results exist, because `limit` and the budget are
            # charged against it and the caller pays for it whether or not anything
            # matched. `mode` and the two token counts are filled in below; nothing
            # else here depends on what was found — `embeddingModel` included, which
            # is why it is asked of the service rather than read off an outcome.
            provisional = _index_report(
                published,
                embedding_model=service.embedding_model(use_dense=use_dense),
                stale=stale,
                snapshot_id=current_state_hash,
            )
            resolved = ResultGate(
                store_factory=SqliteCanonicalStore,
                shape=_shaper(as_of if as_of is not None else datetime.now(UTC)),
            ).admit(
                ResultRequest(
                    database=database,
                    project_id=project_id,
                    include_unapproved=include_unapproved,
                    visible_sensitivities=visible_sensitivities,
                    limit=limit,
                    budget_tokens=budget_tokens,
                    reserved_tokens=_envelope_tokens(project_id, query, provisional),
                    moment=as_of,
                ),
                candidates,
            )
    except IndexBuildError:
        # The file passed the version check and then could not answer: a
        # truncated copy, a dropped table, a metadata row that outlived the
        # tables it describes. The version gate above is the check that should
        # catch this; this is what makes "never answer from a broken index"
        # true even when it does not. It covers the retrieval inside `admit`
        # too, which is where the index is now read.
        return _UNREADABLE

    return _response(
        project_id=project_id,
        query=query,
        resolved=resolved,
        retrieval=replace(
            provisional,
            mode=mode_of(_retrievers_behind(resolved.results)).value,
            used_tokens=resolved.used_tokens,
            dropped_for_budget=resolved.dropped,
        ),
    )


def _shaper(now: datetime) -> ResultShaper:
    """The wire shape of one admitted hit.

    Passed *into* the gate rather than applied after it, so that nothing exists
    between the gate and the response that could be counted, and so the layer
    deciding what may be published is not the layer deciding what a publication
    looks like (ADR-0003).

    ``now`` is read once per request rather than once per result: a freshness age
    that ticked over mid-response would let two hits in one answer disagree about
    what day it is.
    """

    def shape(surfaced: Surfaced) -> dict[str, Any]:
        result = result_payload(
            surfaced.revision,
            surfaced.status,
            surfaced.sensitivity,
            now,
            raptor_path=surfaced.raptor_path,
        )
        # The excerpt is the passage that actually matched, not the head of the
        # document. Chunking buys ranking precision; without this the caller
        # never sees the paragraph it bought.
        if surfaced.passage:
            result["excerpt"] = excerpt(surfaced.passage)
        result["fusedScore"] = round(surfaced.candidate.fused_score, 6)
        result["foundBy"] = list(surfaced.candidate.found_by)
        return result

    return shape


def _response(
    *, project_id: str, query: str, resolved: Resolved, retrieval: _Retrieval
) -> dict[str, Any]:
    """The whole response, assembled in one place for both answer paths.

    One function, so the envelope that :func:`_envelope_tokens` prices is
    literally the envelope that is sent — a second construction site is how the
    ``retrieval`` block came to carry different key sets on the two paths.
    """
    return {
        "projectId": project_id,
        "query": query,
        "count": len(resolved.results),
        "results": list(resolved.results),
        "retrieval": retrieval.to_json(),
    }


def _envelope_tokens(project_id: str, query: str, retrieval: _Retrieval) -> int:
    """What this response costs before a single result is added to it (FR-R4).

    Charged against ``maxTokens``, because it is charged to the caller. The
    echoed query, the ids and the ``retrieval`` block — the ``note`` above all —
    measured 138 to 171 tokens and none of it was counted, so a caller asking for
    2,000 was sent 2,030 and told the answer had cost 1,860.

    Priced on the envelope as it will be sent, bar the three fields that cannot
    be known before the results are: ``mode``, ``usedTokens`` and
    ``droppedForBudget``. Their placeholders differ from the final values by a
    few characters — under one token at four characters per token, on an estimate
    that already errs high.

    Reserved from the budget rather than added to ``usedTokens``, which is
    published as the cost of the *results*. Changing what that number means is a
    wire-contract change and belongs in its own commit; charging honestly for the
    envelope does not have to wait for one.
    """
    empty = Resolved.empty()
    return estimate_tokens(
        json.dumps(
            _response(project_id=project_id, query=query, resolved=empty, retrieval=retrieval),
            ensure_ascii=False,
        )
    )


def _retrievers_behind(results: Sequence[Mapping[str, Any]]) -> Iterator[str]:
    """Which retrievers are named by the results being returned.

    Read off the results rather than off the rankings that produced them. That
    was the fix for a mode that moved when withheld content matched — one more
    bit per query, and two with ``useDense=true``. The rankings no longer hold
    withheld rows at all, so the two readings now agree; this one is kept because
    it is the one that stays true if they ever stop agreeing again.
    """
    for result in results:
        found_by: Sequence[str] = result["foundBy"]
        yield from found_by


def _index_report(
    published: _PublishedIndex,
    *,
    embedding_model: str,
    stale: bool,
    snapshot_id: str,
) -> _Retrieval:
    """How the answer was produced, for a client that has to act on it.

    Every argument is a property of the index, of the canonical state, or of the
    caller's own request — never of what one query matched. That is what makes it
    safe to build this *before* the results exist, which in turn is what lets the
    envelope be priced against the caller's budget.

    The three fields that do describe the results — ``mode``, ``usedTokens``,
    ``droppedForBudget`` — are left at their empty values here and replaced by
    the caller once :class:`Resolved` exists. They are the fields that must never
    be derived from a candidate that did not become a result, and they are
    unreachable from here.
    """
    return _Retrieval(
        mode=RetrievalMode.NONE.value,
        indexed=True,
        indexes_unapproved=published.indexes_unapproved,
        used_tokens=0,
        dropped_for_budget=0,
        note=_ranked_note(stale=stale),
        # The canonical store's own state, not the index's — see the call site
        # in `hybrid_answer`, which reads it once for both this and `stale`.
        snapshot_id=snapshot_id,
        # Reported, because only the CLI knew this and the client is the one
        # acting on the answer. A stale index is a correctness problem wearing
        # the costume of a relevance problem.
        #
        # Named for what it compares. The index is checked against the
        # *database*, not against the repository's migrations — deriving the
        # latter means re-reading every migration on every search. `theurian
        # index status` does compare all three, and will report a database that
        # is itself behind, which this cannot.
        #
        # Query-independent, which is what makes it a safe replacement for the
        # per-query `withheldSuperseded` it now stands in for: it says "your
        # index is behind, expect fewer results" without saying how many, for
        # this query, matched something you may not read.
        stale=stale,
        stale_against="builtState",
        index_build_id=published.build_id,
        embedding_model=embedding_model,
    )


def _ranked_note(*, stale: bool) -> str:
    """Prose for a human reading the transcript; the fields above are the API.

    The fresh-index note names all three retrievers because it once named two.
    It omitted `substring` — the retriever added in Milestone 5, and the only
    one that can segment a script without word boundaries — and it named
    `dense`, which is off unless a caller passes `useDense`. A note that
    describes a search the caller did not get is worse than no note.

    `substring` is described by what it is for, not by how it is built. It
    answers with a trigram lookup ordinarily, and with a scoped scan for a
    query too short to form a trigram (`SqliteIndexStore
    ._scan_below_the_trigram_floor`) — a choice made from the query alone, so
    it never varies with what the corpus holds. But it does vary between two
    otherwise-identical requests whose query differs only in length, and a
    note claiming "trigram" on a request the scan actually answered would name
    a mechanism that did not run. Naming the retriever rather than one of its
    strategies keeps the note true on both.
    """
    if stale:
        return (
            "This index was built from an earlier knowledge state. Run "
            "`theurian index build` to refresh it."
        )
    return (
        "Ranked by reciprocal rank fusion over `lexical` (word index), "
        "`substring` (matches scripts without word boundaries, such as "
        "Japanese, by trigram or, for a query too short to trigram, a scoped "
        "scan) and, when `useDense` is set, `dense` (vector similarity). "
        "`foundBy` names which of them surfaced each hit."
    )


def substring_answer(  # noqa: PLR0913 - one keyword per published tool parameter
    database: Path,
    *,
    state: ActiveState,
    project_id: str,
    query: str,
    limit: int,
    include_unapproved: bool,
    visible_sensitivities: frozenset[Sensitivity],
    budget_tokens: int,
    fallback: Fallback,
    as_of: datetime | None,
) -> dict[str, Any]:
    """Answer by scanning the canonical store, when no index can.

    Unranked, and bounded twice: by ``limit``, and by the caller's token budget.
    The second bound is not decoration. FR-R4 is a promise about every answer,
    and this path used to ignore it entirely — fifty results with their
    provenance and trust labels are several thousand tokens handed to a caller
    who asked for five hundred, and an agent that receives more context than it
    asked for has already paid for it.

    Takes no ``ProjectPaths``. It answers straight from ``database``, and the one
    thing it used them for — re-reading `active.json` — is exactly the read that
    could disagree with the one that chose ``database`` (SEC-13/T-15 — see
    `_Retrieval.snapshot_id`).

    ``as_of`` is FR-R1's validity axis (#63 phase 2). :func:`_scan` applies it
    in Python, through ``ValidityPeriod.contains`` -- the identical check the
    ranked path applies via ``CanonicalVisibility.at_moment``, and not through
    a SQL ``current_at`` filter :class:`~theurian.infrastructure.sqlite.store.
    SqliteCanonicalStore` no longer has (see its docstring): that filter
    compared a stored ``validFrom``/``validTo`` against ``as_of`` as SQLite
    TEXT, silently disagreeing with the ranked path whenever the two were
    authored in different UTC offsets (found in review round 1 of PR #112).
    """
    provisional = _Retrieval(
        # "substring" here names the unranked canonical scan, not the trigram
        # retriever that appears under the same name in a ranked hit's
        # `foundBy`. Two meanings for one word, and the word is on the wire in
        # both, so renaming either is a published-contract change rather than a
        # tidy-up.
        mode="substring",
        indexed=False,
        indexes_unapproved=False,
        used_tokens=0,
        dropped_for_budget=0,
        note=fallback.note,
        snapshot_id=str(state.state_hash),
        fallback_reason=fallback.reason,
    )
    # `_scan` gates on status and on the deployment's sensitivity grant before it
    # counts towards `limit`, so its output is already what this caller may see and
    # the budget may be applied straight to it. The envelope is reserved here for
    # the same reason as on the ranked path: `note` alone is a paragraph, and the
    # caller pays for it.
    resolved = within_budget(
        _scan(
            database,
            project_id=project_id,
            needle=query.strip().lower(),
            limit=limit,
            include_unapproved=include_unapproved,
            visible_sensitivities=visible_sensitivities,
            as_of=as_of,
        ),
        budget_tokens=budget_tokens,
        reserved_tokens=_envelope_tokens(project_id, query, provisional),
    )

    return _response(
        project_id=project_id,
        query=query,
        resolved=resolved,
        retrieval=replace(
            provisional,
            used_tokens=resolved.used_tokens,
            dropped_for_budget=resolved.dropped,
        ),
    )


def _scan(  # noqa: PLR0913 - one keyword per published tool parameter, plus `database`
    database: Path,
    *,
    project_id: str,
    needle: str,
    limit: int,
    include_unapproved: bool,
    visible_sensitivities: frozenset[Sensitivity],
    as_of: datetime | None,
) -> list[dict[str, Any]]:
    """Every current revision whose title or body contains ``needle``.

    Stops at ``limit`` so an unranked scan cannot walk a whole corpus to build
    an answer the budget will discard anyway.

    ``as_of=None`` skips the ``item.validity.contains`` check below entirely --
    this scan's behaviour when the caller pins nothing is unchanged from
    before ``asOf`` existed. A moment is checked in Python, against the
    ``KnowledgeItem`` this loop already holds: an earlier version passed
    ``current_at=as_of`` to ``list_items`` and let a now-deleted SQL clause do
    it, which compared the stored timestamp and ``as_of`` as SQLite TEXT and
    so disagreed with the ranked path whenever they were authored in
    different UTC offsets (found in review round 1 of PR #112). Filtering
    here instead costs nothing extra: every item was already being read to
    reach its status. This scan also has no depth-doubling loop for a
    caller-chosen moment to bias the pass count of -- unlike the ranked path
    (see ``CanonicalVisibility.at_moment``), it walks the whole corpus once
    whatever ``as_of`` excludes.
    """
    now = as_of if as_of is not None else datetime.now(UTC)
    context = RequestContext(project_id=ProjectId(project_id))
    matches: list[dict[str, Any]] = []

    # The status gate stays here, in the tool layer the security enumeration pins
    # it to (`test_gate_call_sites.py`, SEC-13/T-15). `_scan` resolves which
    # statuses are surfaceable under `include_unapproved` and hands the set to the
    # store, which filters in SQL without knowing what "surfaceable" means -- so the
    # visibility rule is consulted exactly once, where it is enumerated, and never
    # inside an adapter. That SQL filter is what closes #158's timing channel:
    # withheld rows are never materialised, so this scan's cost is independent of
    # the withheld count and a caller cannot recover that count by timing the
    # response (T-17). The result set is exactly what the old `may_surface` gate on
    # `list_items` returned here -- moving the filter into SQL drops no visible row.
    #
    # A status cell corrupt to a non-enum value fails the store's `IN` predicate and
    # so drops out. That is a change of failure mode, not a new leak: the old
    # `list_items` path this replaced materialised every row and raised `ValueError`
    # in `KnowledgeStatus(row["status"])` (store.py `_item_from_row`), so the whole
    # search errored with `StateDatabaseUnreadableError`. #158 converts that crash
    # into a silent under-report of the one corrupt row, and *this scan* still does
    # not detect it: detection would mean reading every row to inspect its status,
    # which is the O(withheld) scan this change exists to remove.
    #
    # **The scan stays blind; the response no longer is.** #30 PR2 compares the live
    # surfaceable-item count against the one `migrate apply` recorded, in the tool
    # layer above (`mcp/tools.py`, `_measure_integrity`), and a status that has
    # fallen out of `SURFACEABLE_STATUSES` moves that count. Measured against the
    # real tool over an unindexed project, which is what makes this fallback the
    # answering path: corrupt the approved row's status and the answer is `count: 0,
    # results: []` *with* `integrity` present; corrupt a `draft` row's and the
    # default answer is unchanged at `count: 1` and `integrity` is present anyway,
    # because both sides count `SURFACEABLE_STATUSES` rather than the set
    # `include_unapproved` resolved above; corrupt a `deprecated` row's and neither
    # count moves and no key appears -- a retired row is on neither side, so the
    # signal carries no bit about a row no flag surfaces (SEC-13, T-17).
    #
    # So this position is not in `UNDETECTED_UNDERREPORT`
    # (`tests/integration/test_canonical_store_corruption.py`), the exact set that
    # replaced `SILENTLY_EMPTIED` for "answers with less than the file holds and
    # discloses nothing" -- its one member is a corrupt `knowledge_items.item_id`,
    # which keeps the row inside both scopes and so moves neither count. Nor is it
    # in that file's disclosing set, because the sweep's corpus is indexed and
    # `knowledge.search` answers there through the ranked gate, where
    # `CanonicalVisibility._may_surface` fetches each candidate's item and
    # `_item_from_row` parses the cell: a corrupt status on a row the query ranks
    # refuses the whole search, and a refusal carries no field. Ranked refuses, this
    # fallback discloses, and `knowledge.status` is the sibling pinned as disclosing.
    #
    # The sensitivity axis (#119) arrives already resolved -- it is the deployment's
    # grant, not a rule this function applies -- so there is nothing to build here
    # and it is passed straight through below. The two axes are independent: a level
    # this deployment does not serve is withheld whatever `include_unapproved` says,
    # for the reason retired statuses are.
    surfaceable = frozenset(
        s for s in KnowledgeStatus if may_surface(s, include_unapproved=include_unapproved)
    )

    with SqliteCanonicalStore(database) as store:
        for item in store.list_items_by_status(
            context,
            statuses=surfaceable,
            # The deployment's grant, handed to the store as the second SQL
            # predicate rather than checked on the way past (#119). Same
            # discipline as `statuses` and for the same reason: an above-ceiling
            # row never crosses into Python, so it costs no `KnowledgeItem`, no
            # revision read and no body scan -- the dominant per-row work on this
            # path. `may_disclose` is therefore not called here and `_scan` is not
            # one of its enumerated sites; what the store cannot make flat is
            # recorded on `list_items_by_status` and measured there.
            sensitivities=visible_sensitivities,
        ):
            if as_of is not None and not item.validity.contains(as_of):
                continue
            if item.current_revision_id is None:
                continue
            # The guarded dereference, for the reason `knowledge.get` uses it: a
            # `current_revision_id` naming a sibling item's revision passes every
            # structural check the file has, and this loop would then excerpt
            # that revision's title and body under this item's status. Measured
            # before the guard existed, a `rejected` body came back here to a
            # default caller. See `SqliteCanonicalStore.current_revision`.
            revision = store.current_revision(context, item)
            if revision is None:  # pragma: no cover - a composite foreign key holds this (#24)
                continue
            if needle not in f"{revision.title}\n{revision.body}".lower():
                continue

            matches.append(result_payload(revision, item.status, item.sensitivity, now))
            if len(matches) >= limit:
                break

    return matches
