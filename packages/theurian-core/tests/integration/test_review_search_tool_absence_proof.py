"""One query against two corpora, at the `review.search` **response** (ADR-0030 decision 6).

``test_review_search_absence_proof.py`` states this property at the *store*: a
store built while withholding a set of record keys answers every query the same
as a store built from a corpus that never held those records. That is the
artifact half. This module states the same property one layer up, over the thing
a caller actually receives -- the serialised MCP tool response -- and the gap
between the two is not rhetorical. Between a store hit and a wire response sit
``_resolve``, the ADR-0004/SEC-7 provenance check, the admission gate, the
refusal envelopes, ``build_query``'s bounds and vocabularies, ``probing``'s
``limit + 1`` read, ``review_search_payload``'s ``count``/``truncated`` and
``review_record``'s shaping. Each of those computes something, and a value
computed over rows a caller may not see is the family this project has met
five times.

The claim, in the words ADR-0030 decision 6 inherits from T-17a: *a corpus that
held the withheld records and a corpus that never did must answer identically*
-- and here, identically **on the wire**, refusals included.

Three deployments, and the third is why the first two mean anything
-------------------------------------------------------------------
``withholding``
    the whole corpus, built while withholding :data:`WITHHELD_KEYS`.
``never_held``
    the corpus **minus** those records, built withholding nothing.
``control``
    the whole corpus, built withholding nothing. The **positive control**: every
    query the pair must answer identically returns the planted rows here. Without
    it, an equality is satisfied by a build that wrote nothing, a query that
    matched nothing and a corpus whose plant was unreachable -- three ways for
    this file to hold vacuously, and :func:`test_the_battery_really_reaches_the_
    withheld_records` is what makes the reach a measured set rather than a hope.

Each is a **separate registry holding a project under the same id**, so the two
requests being compared are byte-identical down to ``projectId``: the differential
is the corpus and nothing else. ``ProjectRegistry.default`` takes its directory as
an argument rather than re-reading ``THEURIAN_DATA_DIR``, so three registries
coexist in one process without the environment deciding which one a call reaches.

The identity-normalization trap, and why nothing is normalised
--------------------------------------------------------------
Two deployments necessarily differ in something -- here their root paths, since
the project id is held equal by construction. If any of that reached the
response, a byte-equality would be unachievable for a reason that is not a leak,
and the tempting repair (mask the differing fields before comparing) would delete
exactly the channel this file exists to watch.

So the probe comes first and is a test in its own right:
:func:`test_a_served_response_carries_no_identity_of_the_deployment_that_served_it`
measures whether project id, root path, data directory or store path reach the
wire. Measured on this branch, **none does** -- ``review_record`` serves stored
record columns plus ``mcp/results.SAFETY``, and ``recordPath`` is
``sha256-<repository digest>/<kind>/<leaf>``, a function of the *repository* and
the record's provider id rather than of the project. Every comparison below is
therefore a **raw byte comparison of the serialised response**, with nothing
masked and no field named. If that probe ever reddens, the comparisons here stop
being achievable and the finding is the probe's, not theirs.

What is compared
----------------
The whole MCP result: ``isError``, the structured content, and every content
block's type and text, serialised into one string. A refusal is folded into that
same string, so *error distinguishability* is part of the comparison rather than a
separate claim -- a request refused against one corpus and answered against the
other separates here (SEC-13).

The adjacent pair
-----------------
One withheld thread and one visible thread are alike in **every** ordering key --
same repository, same pull request, same kind, same anchor file -- with leaves
that differ only where the order is decided, so the withheld row sits immediately
before the visible one in the served sequence. That is the configuration where
withholding could plausibly disturb a row the caller *may* read: its slot, its
excerpt, the position its excerpt was cut at. The visible neighbour's opening
comment straddles ``MAX_EXCERPT_CHARS`` on purpose, so the comparison covers
where the cut fell and not only which stored value survived.

What this module does not reach
-------------------------------
- **Durations and resource consumption.** Two members of the observable-family
  table, and neither is asserted: a per-row term of this size is far below what
  one process's wall clock separates from noise, so a timing assertion would read
  as covering the family while asserting nothing. They stay the round's lenses.
- **The store's own artifact.** "No byte of a withheld record survives in the
  file" is ``test_review_search_absence_proof.py``'s, and is not re-proved here.

The corpus below is a **third, independent construction**: it shares no fixture,
no vocabulary and no planted value with either of the two modules above. Two
corpora proving one property must be built independently, or one construction's
bug masks the same bug in the other.

Marked ``integration``: every case checks out a real git project, runs the real
CLI to register and migrate it, lands real evidence files and opens real SQLite
databases. Writes only under pytest's temporary directories.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st
from mcp.server import MCPServer
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    REVIEW_SEARCH_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.main import app
from theurian.cli.review_commands import evidence_entries
from theurian.daemon.runner import build_server
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore
from theurian.mcp.review_search import MAX_EXCERPT_CHARS

pytestmark = pytest.mark.integration

runner = CliRunner()

#: One project id, used by all three deployments. Kebab-case, as ``ProjectId``
#: requires, and distinctive enough that the identity probe can search a response
#: for it and mean something.
PROJECT_ID: Final = "twin-corpora"

PROVIDER: Final = "github"
REPOSITORY: Final = "acme/ledger-service"

#: Crockford base32: no ``I``, ``L``, ``O`` or ``U``.
#: ``tests/unit/test_test_fixtures.py`` is what catches a readable spelling that
#: forgets. One run for every deployment, so ``lastSeenRunId`` and ``lastSeenAt``
#: -- both published columns -- are equal by construction rather than by luck.
RUN: Final = IngestionRun("01K1TWNCRPRA00000000000000", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))

#: Every word a **visible** record's text is built from: letters ``a``--``o`` only.
#:
#: The split at ``o`` is load-bearing rather than decorative. ``LIKE`` folds the 26
#: ASCII letters, so "no visible record can spell the planted payload" has to hold
#: after folding, and splitting the alphabet gives that by construction rather than
#: by inspection of a word list.
#: :func:`test_no_visible_record_can_spell_a_withheld_value` is this file's guard on
#: its own constants, and it ranges over the *rendered corpus* rather than over this
#: tuple, so a body assembled from something outside it is caught too.
VOCABULARY: Final = (
    "medallion",
    "goldfinch",
    "biennial",
    "childhood",
    "declined",
    "enfold",
    "official",
    "hemlock",
    "abandoned",
    "kindled",
    "collide",
    "engine",
)

#: The other half of the split. Upper case only for legibility in a failure --
#: ``LIKE`` folds it to ``p``--``z``, which is what the disjointness rests on.
PAYLOAD_ALPHABET: Final = "PQRSTVWXYZ"

#: The string no visible record can spell, planted inside the withheld records.
PLANTED_PAYLOAD: Final = "VWXYZPRSTVWXYZ"

#: The withheld records' own distinguishing values, each of which is a filter a
#: caller can name: an author, a file path, a display name, a thread's node id and
#: a pull-request number. A deployment that answered any of them differently from
#: one that never held the records would be telling a caller the records exist.
PLANTED_AUTHOR: Final = "USER_ZYXWVT"
PLANTED_DISPLAY_NAME: Final = "Reviewer VWXYZ"
PLANTED_FILE_PATH: Final = "src/VWXYZPRST.py"
PLANTED_THREAD_ID: Final = "PRRT_VWXYZ"

#: The withheld pull request's number, chosen **below** every visible one so that
#: it sorts first under the store's total order (repository, pull request, kind,
#: path). That is what makes the ``limit``-contested arm able to fail: a build that
#: wrote the withheld rows and filtered them on the way out would spend the
#: caller's first slots on them.
PLANTED_NUMBER: Final = 3

#: The visible pull requests, every one numbered above the planted one.
VISIBLE_NUMBERS: Final = (20, 21, 22, 23)

#: What every record carries, visible and withheld alike, so one query reaches the
#: whole corpus -- which is what a displacement arm and a page-edge arm both need.
SHARED_TERM: Final = "medallion"

#: The anchor file the **adjacent pair** below shares, and the file path every
#: other visible thread already carried. A *visible* value on purpose: a filter
#: naming it reaches the withheld sibling in the control and only visible rows in
#: the pair, which is what makes the pair's arms able to bite.
SHARED_FILE_PATH: Final = "src/ledger.py"

#: The adjacent pair's node ids: one withheld thread and one visible thread that
#: are alike in **every** ordering key -- same repository, same pull request, same
#: kind -- and whose leaves differ only at the character that decides the order.
#: ``_1_`` sorts before ``_2``, so the withheld sibling sits *immediately before*
#: the visible one in the served sequence.
#:
#: This is family 2b: the case where withholding could plausibly move something
#: about a row the caller may read -- its slot, its excerpt, the position its
#: excerpt was cut at -- because the row nobody may see is its immediate
#: neighbour rather than somewhere else in the corpus. Adjacency is *measured*
#: rather than assumed by
#: :func:`test_a_visible_records_bytes_do_not_move_when_its_neighbour_is_withheld`;
#: neither id is a substring of the other, so the planted-value assertions cannot
#: fire on the visible one's own path.
ADJACENT_WITHHELD_THREAD_ID: Final = "PRRT_ENFOLD_1_VWXYZ"
ADJACENT_VISIBLE_THREAD_ID: Final = "PRRT_ENFOLD_2"

#: An author no record ever carried. The differential for "withheld" against
#: "never existed" (SEC-13).
ABSENT_AUTHOR: Final = "USER_NEVERLANDED"

BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""


# -- the corpus, built here and shared with nothing -----------------------------


def _participant(external_id: str, display_name: str) -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id=external_id, display_name=display_name)


def _anchor(uri: str) -> SourceAnchor:
    return SourceAnchor(provider=PROVIDER, source_uri=uri, repository=REPOSITORY)


def _pull_request(
    *,
    number: int,
    title: str,
    body: str,
    author: str = "USER_VISIBLE",
    display_name: str = "Reviewer One",
) -> EvidenceRecord:
    """One landed pull request. Its record key is its number (``records.py``)."""
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}"),
        payload=ReviewEvent(
            project_id=ProjectId(PROJECT_ID),
            provider=PROVIDER,
            repository=REPOSITORY,
            number=number,
            title=title,
            body=body,
            author=_participant(author, display_name),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{REPOSITORY}/pull/{number}",
            head_commit="b" * 40,
            base_commit="c" * 40,
            head_ref_name="fix/medallion",
            labels=("security",),
        ),
    )


def _submission(*, external_id: str, number: int, body: str) -> EvidenceRecord:
    """One landed review submission. Its record key is its provider node id."""
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(
            f"https://github.com/{REPOSITORY}/pull/{number}#pullrequestreview-{external_id}"
        ),
        payload=ReviewSubmission(
            external_id=external_id,
            project_id=ProjectId(PROJECT_ID),
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            author=_participant("USER_VISIBLE", "Reviewer One"),
            body=body,
            state="APPROVED",
        ),
    )


def _thread(  # noqa: PLR0913 - one keyword per field a search filters or matches on
    *,
    external_id: str,
    number: int,
    bodies: Sequence[str],
    state: ReviewThreadState,
    author: str = "USER_VISIBLE",
    display_name: str = "Reviewer One",
    file_path: str = SHARED_FILE_PATH,
) -> EvidenceRecord:
    """One landed review thread. Its record key is its provider node id."""
    participant = _participant(author, display_name)
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}#discussion_{external_id}"),
        payload=ReviewThread(
            external_id=external_id,
            project_id=ProjectId(PROJECT_ID),
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            file_path=file_path,
            comments=tuple(
                ReviewComment(
                    external_id=f"IC_{external_id}_{ordinal}",
                    author=participant,
                    body=body,
                    created_at=datetime(2026, 8, 1, 13, ordinal, tzinfo=UTC),
                )
                for ordinal, body in enumerate(bodies)
            ),
            state=state,
            resolution=(
                ReviewResolution(state=state, resolved_by=participant)
                if state is ReviewThreadState.RESOLVED
                else None
            ),
        ),
    )


#: The **visible neighbour**'s opening comment, and it is long on purpose.
#:
#: The serving surface cuts an excerpt at ``MAX_EXCERPT_CHARS`` and marks what it
#: cut, so a *short* fragment is compared as a whole stored value and says nothing
#: about **where** the cut fell. This one straddles the bound, so the two corpora
#: are compared on the cut position too -- the "which part of a row reached a
#: field" face of the observable-family table, which a value comparison alone does
#: not reach.
#:
#: Spelled from :data:`VOCABULARY` alone, so the alphabet guard still holds over
#: it: the letters ``p``--``z`` never appear in a visible record's matchable text.
ADJACENT_LONG_COMMENT: Final = " ".join((SHARED_TERM, *(VOCABULARY * 4)))

#: Everything both deployments hold. Seven records across all three kinds, every
#: one carrying :data:`SHARED_TERM` and every one spelled from :data:`VOCABULARY`,
#: so a single query reaches the whole corpus and the page-edge arm has a defined
#: edge.
VISIBLE_CORPUS: Final = (
    *(
        _pull_request(
            number=number,
            title=f"{SHARED_TERM} engine {number}",
            body=f"{SHARED_TERM} goldfinch biennial {number}.",
        )
        for number in VISIBLE_NUMBERS
    ),
    _submission(
        external_id="PRR_HEMLOCK",
        number=VISIBLE_NUMBERS[0],
        body=f"{SHARED_TERM} collide, official.",
    ),
    _thread(
        external_id="PRRT_HEMLOCK",
        number=VISIBLE_NUMBERS[0],
        bodies=(f"{SHARED_TERM} declined childhood.", "hemlock enfold."),
        state=ReviewThreadState.RESOLVED,
    ),
    # The visible half of the adjacent pair: the row whose bytes must not move.
    _thread(
        external_id=ADJACENT_VISIBLE_THREAD_ID,
        number=VISIBLE_NUMBERS[0],
        bodies=(ADJACENT_LONG_COMMENT, "collide official hemlock."),
        state=ReviewThreadState.RESOLVED,
        file_path=SHARED_FILE_PATH,
    ),
)

#: The records ``withholding`` is asked not to write and ``never_held`` never had.
#:
#: Two kinds and three keys, because the withheld set is matched on
#: ``EvidenceRecord.record_key`` and that property answers differently per kind: a
#: pull request is keyed by its **number**, a thread by its node id. A corpus that
#: withheld only one shape would leave the other's key untested.
#:
#: The threads are ``open`` while every visible thread is ``resolved``, so
#: ``threadState`` is a filter that reaches the withheld records and nothing else.
#:
#: The third record is the **withheld half of the adjacent pair**: it shares the
#: repository, the pull request, the kind and the anchor file with a visible
#: thread, and its leaf sorts immediately before that thread's. Everything else in
#: this corpus is withheld from a *different* pull request, which is a corpus a
#: build could get right while still letting a withheld row disturb the row beside
#: it.
WITHHELD_CORPUS: Final = (
    _pull_request(
        number=PLANTED_NUMBER,
        title=f"{SHARED_TERM} {PLANTED_PAYLOAD}",
        body=f"{SHARED_TERM} abandoned {PLANTED_PAYLOAD}.",
        author=PLANTED_AUTHOR,
        display_name=PLANTED_DISPLAY_NAME,
    ),
    _thread(
        external_id=PLANTED_THREAD_ID,
        number=PLANTED_NUMBER,
        bodies=(f"{SHARED_TERM} {PLANTED_PAYLOAD} kindled.", f"second {PLANTED_PAYLOAD}."),
        state=ReviewThreadState.OPEN,
        author=PLANTED_AUTHOR,
        display_name=PLANTED_DISPLAY_NAME,
        file_path=PLANTED_FILE_PATH,
    ),
    _thread(
        external_id=ADJACENT_WITHHELD_THREAD_ID,
        number=VISIBLE_NUMBERS[0],
        bodies=(f"{SHARED_TERM} {PLANTED_PAYLOAD} enfold.", f"{PLANTED_PAYLOAD} once more."),
        state=ReviewThreadState.OPEN,
        author=PLANTED_AUTHOR,
        display_name=PLANTED_DISPLAY_NAME,
        file_path=SHARED_FILE_PATH,
    ),
)

#: The keys ``withholding`` withholds, taken from the records' **own**
#: ``record_key`` property rather than spelled out here. Spelling them would be a
#: second definition of the same rule, and a withheld key that matched one spelling
#: and missed the other is exactly the drift this avoids.
WITHHELD_KEYS: Final = frozenset(record.record_key for record in WITHHELD_CORPUS)

#: Every planted value that must never appear in a ``withholding`` response, in one
#: place so no comparison below can forget one.
PLANTED_VALUES: Final = (
    PLANTED_PAYLOAD,
    PLANTED_AUTHOR,
    PLANTED_DISPLAY_NAME,
    PLANTED_FILE_PATH,
    PLANTED_THREAD_ID,
    # The adjacent sibling's own id, which reaches a response through its
    # `recordPath` rather than through any text a caller can match on.
    ADJACENT_WITHHELD_THREAD_ID,
)


def _matchable_text(records: Sequence[EvidenceRecord]) -> tuple[str, ...]:
    """Every fragment ``q`` can match in ``records``: titles, bodies and comments.

    Exactly the population the store's text table holds, which is what the alphabet
    guard has to range over: ``q`` is a substring test against a stored *fragment*,
    so a payload substring appearing in one of these is what would separate two
    corpora without anything leaking. A file path is deliberately not here -- it is
    matched by an equality filter, where a shared letter cannot produce a partial
    match -- and :func:`_authored_values` is what covers it instead.

    Walked off the payloads rather than collected while the corpus was built, so a
    guard ranging over it cannot fall out of step with the corpus it guards.
    """
    matchable: list[str] = []
    for record in records:
        payload = record.payload
        if isinstance(payload, ReviewEvent):
            matchable.extend((payload.title, payload.body or ""))
        elif isinstance(payload, ReviewSubmission):
            matchable.append(payload.body or "")
        elif isinstance(payload, ReviewThread):
            matchable.extend(comment.body for comment in payload.comments)
    return tuple(matchable)


def _authored_values(records: Sequence[EvidenceRecord]) -> tuple[str, ...]:
    """:func:`_matchable_text`, plus every other value a person chose.

    The file path and the display name are author-controlled and published, so the
    "no visible record spells a planted value" guard has to see them even though no
    substring filter reaches them.
    """
    extra: list[str] = []
    for record in records:
        payload = record.payload
        if isinstance(payload, ReviewThread):
            extra.append(payload.file_path or "")
        author = getattr(payload, "author", None)
        if author is not None:
            extra.extend((author.external_id, author.display_name))
    return (*_matchable_text(records), *extra)


# -- three deployments ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Deployment:
    """One registry, one project, one built review-search store, one server."""

    server: MCPServer
    registry: ProjectRegistry
    root: Path
    data_dir: Path
    store: Path


@dataclass(frozen=True, slots=True)
class _Corpora:
    """Two deployments that differ only in records no caller may be told about."""

    withholding: _Deployment
    never_held: _Deployment
    #: The positive control: the same corpus as :attr:`withholding`, built with
    #: nothing withheld. What says the planted records were reachable at all.
    control: _Deployment


def _cli(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _deploy(
    base: Path,
    name: str,
    records: Sequence[EvidenceRecord],
    *,
    withheld: frozenset[str],
    patched: pytest.MonkeyPatch,
) -> _Deployment:
    """One whole deployment: a checked-out project, its evidence and its store.

    Everything ``_resolve`` needs is real -- a registry entry, an active state
    pointer, the ADR-0004/SEC-7 provenance record on the canonical state -- so a
    call refused at the review-search gate is refused for that reason and not
    because the project would not resolve. ``evidence_entries`` is the composition
    root's own mapping, imported rather than re-implemented: a test that mapped
    the records itself would keep passing over a root that had stopped carrying a
    field.

    ``THEURIAN_DATA_DIR`` is redirected per deployment and the working directory is
    set in the same call that runs the CLI: ``theurian init`` writes into the
    process's working directory and takes no argument that says where.
    """
    data_dir = base / name / "datadir"
    root = base / name / PROJECT_ID
    root.mkdir(parents=True)
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)  # noqa: S603

    patched.setenv("THEURIAN_DATA_DIR", str(data_dir))
    patched.chdir(root)
    _cli("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _cli("project", "register")
    _cli("migrate", "apply")

    registry = ProjectRegistry.default(data_dir)
    paths = ProjectPaths.of(root)
    evidence = ReviewEvidenceStore(paths.review)
    evidence.write(tuple(records), run=RUN)
    store = SqliteReviewSearchStore(paths.review_search_for(REVIEW_SEARCH_STORE_ID))
    ReviewSearchBuilder(read_evidence=evidence_entries(evidence), write=store.replace_all).build(
        ReviewSearchBuildRequest(withheld_record_keys=withheld)
    )
    BuildProvenance.for_registry(registry).record_review(paths.root, REVIEW_SEARCH_STORE_ID)
    return _Deployment(
        server=build_server(registry),
        registry=registry,
        root=root,
        data_dir=data_dir,
        store=store.path,
    )


@pytest.fixture(scope="module")
def corpora(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Corpora]:
    """The three deployments, built once for the module.

    Module-scoped because each one runs ``git init``, three CLI commands and a
    real build, and every test below only *reads* them. Function-scoped would pay
    that cost per case and would make the generated arm below unusable --
    hypothesis reports a function-scoped fixture under ``@given`` as a health-check
    failure, which this suite promotes to an error (``filterwarnings = error``).

    ``HOME`` and ``THEURIAN_DATA_DIR`` are redirected for the fixture's whole life
    and the working directory is restored once the builds are done: ``theurian
    init`` writes into the process's working directory and takes no argument that
    says where, so the ``chdir`` is what contains it.
    """
    origin = Path.cwd()
    base = tmp_path_factory.mktemp("twin-corpora")
    home = base / "home"
    home.mkdir()
    whole = (*VISIBLE_CORPUS, *WITHHELD_CORPUS)
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv("HOME", str(home))
        try:
            built = _Corpora(
                withholding=_deploy(
                    base, "withholding", whole, withheld=WITHHELD_KEYS, patched=patched
                ),
                never_held=_deploy(
                    base, "never-held", VISIBLE_CORPUS, withheld=frozenset(), patched=patched
                ),
                control=_deploy(base, "control", whole, withheld=frozenset(), patched=patched),
            )
        finally:
            # `_deploy` chdirs into each checkout. `MonkeyPatch.chdir` undoes that
            # at teardown, but the tests below run before teardown, so the process
            # is put back on the entry directory now -- whether the builds
            # succeeded or not.
            os.chdir(origin)
        yield built


@pytest.fixture(scope="module")
def empty_deployment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Deployment]:
    """A fourth deployment: a real build over zero evidence records.

    Not the missing-store case
    ``test_a_project_with_no_review_search_store_is_refused_not_answered_empty``
    (``test_review_search_tool.py``) drives -- there the store file never
    exists. Here ``theurian review build`` has run through the same builder
    and the same store every other deployment in this module uses; the store
    file exists on disk and carries its stamp, and it simply has nothing to
    write, which is the state an operator is in immediately after a fresh
    install builds against a repository nothing has landed into yet.

    Built the same way :func:`corpora` builds its three, and separately from
    them: its own ``base``, its own ``HOME``, its own registry. Module-scoped
    for the same reason -- a real ``git init``, three CLI commands and a real
    build, read by every test below rather than rebuilt per case.
    """
    origin = Path.cwd()
    base = tmp_path_factory.mktemp("empty-corpus")
    home = base / "home"
    home.mkdir()
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv("HOME", str(home))
        try:
            deployment = _deploy(base, "empty", (), withheld=frozenset(), patched=patched)
        finally:
            os.chdir(origin)
        yield deployment


# -- what is compared -----------------------------------------------------------


async def _serve(deployment: _Deployment, arguments: dict[str, Any]) -> str:
    """One call's whole outcome as a single string, refusals included.

    The **whole MCP result** -- ``isError``, the structured content, and every
    content block's type and text -- rather than a field list, because the property
    is about everything published and a field list is what this shape exists to
    stop maintaining. Nothing is masked: the identity probe below is what says a
    raw comparison is achievable, and if it ever reddens the finding is there.

    A refusal serialises into the same string on purpose. "This request raised
    against one corpus and answered against the other" is the error-
    distinguishability face of the family (SEC-13), and folding it in means no
    comparison below can forget it. Deliberately every exception: what a caller
    observes is the refusal, whatever its Python type.
    """
    try:
        result = await deployment.server.call_tool(
            "review.search", {"projectId": PROJECT_ID, **arguments}
        )
    except Exception as exc:
        return json.dumps(
            {"refusal": f"{type(exc).__name__}: {exc}"}, sort_keys=True, ensure_ascii=False
        )
    content: Any = result.content  # type: ignore[union-attr]
    blocks = [[type(block).__name__, getattr(block, "text", None)] for block in content or ()]
    return json.dumps(
        {
            "isError": getattr(result, "isError", None),
            "structured": getattr(result, "structuredContent", None),
            "content": blocks,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _answer(deployment: _Deployment, arguments: dict[str, Any]) -> str:
    """:func:`_serve`, driven from a synchronous test.

    One event loop per call rather than a session-wide one, so a case that is
    ``@given``-generated (hypothesis cannot drive a coroutine test function) and a
    case that is not are compared through exactly the same path.
    """
    return asyncio.run(_serve(deployment, arguments))


def _records(answer: str) -> list[dict[str, Any]]:
    """The served rows inside a serialised answer, or ``[]`` for a refusal."""
    envelope = json.loads(answer)
    if "content" not in envelope:
        return []
    payload: dict[str, Any] = json.loads(envelope["content"][0][1])
    rows: list[dict[str, Any]] = payload["records"]
    return rows


def _payload(answer: str) -> dict[str, Any]:
    envelope = json.loads(answer)
    body: dict[str, Any] = json.loads(envelope["content"][0][1])
    return body


@dataclass(frozen=True, slots=True)
class _Arm:
    """One query in the battery, and whether it is expected to reach the plant."""

    name: str
    arguments: dict[str, Any]
    #: Whether the **control** answers this query differently from a corpus that
    #: never held the withheld records. ``True`` says this arm has bite; ``False``
    #: says it is here for a different reason -- an empty result, a refusal, or a
    #: filter that selects only visible rows -- and is not evidence of reach.
    #: :func:`test_the_battery_really_reaches_the_withheld_records` measures the
    #: whole column, so a corpus edit that quietly removed an arm's bite reddens.
    reaches_withheld: bool


#: The query battery. Each arm is here because it could separate the two corpora in
#: a different way: the text arms reach the planted payload and the shared term;
#: the structural arms name the withheld records' own author, file path, thread
#: state and pull-request number -- the values a caller uses to ask "does this
#: exist"; the tight ``limit`` arms contest the slots, which is the only way a
#: build that wrote the withheld rows and filtered them afterwards becomes
#: observable; the page-edge arm sits exactly on the visible count, where a leaked
#: row moves ``truncated`` without moving a single served field; and the refusal
#: arms check that a request refused against one corpus is refused identically
#: against the other.
BATTERY: Final[tuple[_Arm, ...]] = (
    _Arm("unfiltered", {}, True),
    _Arm("unfiltered-first-slot", {"limit": 1}, True),
    _Arm("unfiltered-two-slots", {"limit": 2}, True),
    _Arm("withheld-payload", {"q": PLANTED_PAYLOAD}, True),
    _Arm("withheld-payload-one-slot", {"q": PLANTED_PAYLOAD, "limit": 1}, True),
    _Arm("shared-term", {"q": SHARED_TERM}, True),
    _Arm("shared-term-contested-limit", {"q": SHARED_TERM, "limit": 2}, True),
    _Arm("shared-term-page-edge", {"q": SHARED_TERM, "limit": len(VISIBLE_CORPUS)}, True),
    _Arm("withheld-author", {"author": PLANTED_AUTHOR}, True),
    _Arm("withheld-file-path", {"filePath": PLANTED_FILE_PATH}, True),
    # The adjacent pair's own filter: a file path a *visible* thread carries and a
    # withheld one shares, so the pair sits side by side in the answer.
    _Arm("shared-file-path", {"filePath": SHARED_FILE_PATH}, True),
    _Arm("shared-file-path-first-slot", {"filePath": SHARED_FILE_PATH, "limit": 1}, True),
    _Arm("withheld-pull-request", {"pullRequest": PLANTED_NUMBER}, True),
    _Arm("withheld-thread-state", {"threadState": "open"}, True),
    _Arm("whole-repository", {"repository": REPOSITORY}, True),
    _Arm("visible-thread-state", {"threadState": "resolved"}, False),
    _Arm("absent-author", {"author": ABSENT_AUTHOR}, False),
    _Arm("empty-result", {"q": "VWXYZ-NOBODY-CARRIES-THIS"}, False),
    _Arm("other-repository", {"repository": "acme/nothing-here"}, False),
    _Arm("refused-limit-zero", {"limit": 0}, False),
    _Arm("refused-thread-state", {"threadState": "APPROVED"}, False),
    _Arm("refused-empty-filter", {"repository": ""}, False),
    _Arm("refused-nul", {"q": "with\x00nul"}, False),
)


# -- the identity probe, first ---------------------------------------------------


def test_a_served_response_carries_no_identity_of_the_deployment_that_served_it(
    corpora: _Corpora,
) -> None:
    """GHSA-97q9's family, and the premise every byte comparison below rests on.

    Two deployments necessarily differ somewhere. If any of that difference --
    the project id, the project root, the data directory, the store's own path --
    reached the wire, the comparisons below would be unachievable for a reason
    that is not a leak, and the tempting repair would be to mask the differing
    fields, deleting the channel this file watches.

    So the question is measured rather than assumed, and it is a claim worth
    holding on its own: an operator's machine layout is not this surface's to
    publish. Each token is asserted to be a **real, non-empty** identity string
    that really appears in the deployment's own store path first, so this cannot
    pass by searching a response for the empty string.
    """
    deployment = corpora.withholding
    answer = _answer(deployment, {})
    tokens = {
        "project id": PROJECT_ID,
        "project root": str(deployment.root),
        "data directory": str(deployment.data_dir),
        "store path": str(deployment.store),
        "store file name": deployment.store.name,
    }

    assert _records(answer), "the probe must range over a response that served rows"
    for label, token in tokens.items():
        assert token, f"the {label} token is empty, so searching for it proves nothing"
    assert str(deployment.root) in str(deployment.store), (
        "the store path must contain the project root, or these tokens are not the "
        "deployment identity they claim to be"
    )
    assert PROJECT_ID in str(deployment.store), (
        "the project id must appear somewhere in this deployment's own paths, or the "
        "search below is looking for a string this fixture never produces"
    )
    for label, token in tokens.items():
        assert token not in answer, (
            f"the served response carries the deployment's {label} ({token!r}). That is a "
            f"finding in its own right -- an operator's layout on a tool surface -- and it "
            f"also means the two-corpora comparisons in this module cannot be byte "
            f"comparisons; they would have to normalise it away, which is how a real "
            f"difference gets masked"
        )


# -- the property ----------------------------------------------------------------


def test_a_query_matching_a_withheld_record_answers_as_a_corpus_that_never_held_it(
    corpora: _Corpora,
) -> None:
    """ADR-0030 decision 6, T-17a, SEC-13. The mandatory case, stated explicitly.

    The query's text is the payload planted **inside the withheld records** -- the
    query a caller issues when it already suspects what is being withheld. The two
    deployments must answer it byte-identically on the wire: no marker, no shifted
    ``count``, no moved ``truncated`` bit, no distinguishable refusal, no excerpt
    of a fragment that is not there.

    The positive control is what makes that equality mean anything. A third
    deployment holds the same corpus with nothing withheld and returns the planted
    rows for this query, so the case cannot be satisfied by a query that matches
    nothing, a corpus whose plant was unreachable, or a build that wrote no rows at
    all. The three answers are asserted in the order that makes a failure readable:
    the control must differ, and the pair must not.
    """
    query = {"q": PLANTED_PAYLOAD}

    withholding = _answer(corpora.withholding, query)
    never_held = _answer(corpora.never_held, query)
    control = _answer(corpora.control, query)

    assert PLANTED_PAYLOAD in control, (
        "the positive control must return the planted rows for this query, or the equality "
        "below is between two deployments that were never asked anything"
    )
    assert control != withholding, (
        "the control and the withholding deployment must answer *differently* here, and they "
        "answered alike -- so nothing was withheld, and the equality below would pass over a "
        "build that did no filtering at all"
    )
    assert withholding.encode("utf-8") == never_held.encode("utf-8"), (
        "a deployment that withheld a record answered a query matching that record's own "
        "text differently from one that never held it"
    )
    for value in PLANTED_VALUES:
        assert value not in withholding, f"and no withheld {value!r} may reach the response"


@pytest.mark.parametrize("arm", BATTERY, ids=[arm.name for arm in BATTERY])
def test_every_query_in_the_battery_answers_identically_over_the_two_corpora(
    corpora: _Corpora, arm: _Arm
) -> None:
    """ADR-0030 decision 6, T-17a. The property over the whole published response.

    Nothing is masked and no field is named: the comparison is the serialised MCP
    result, so a difference in *which rows reached* a field separates here as
    loudly as a difference in a field's value -- the two faces Milestone 5 found
    were not one claim -- and a refusal that fires against one corpus and not the
    other separates too.

    Which arms have bite is not asserted here but in
    :func:`test_the_battery_really_reaches_the_withheld_records`, over the whole
    battery at once: per-arm, "the control differs" is only meaningful for the arms
    that name a withheld value, and asserting it case by case would either exclude
    the refusal arms from the property or manufacture a precondition they cannot
    meet.
    """
    withholding = _answer(corpora.withholding, arm.arguments)
    never_held = _answer(corpora.never_held, arm.arguments)

    assert withholding == never_held, (
        "a published value varies with a record the caller may not be told about"
    )
    for value in PLANTED_VALUES:
        assert value not in withholding, f"and no withheld {value!r} may reach the response"


def test_the_battery_really_reaches_the_withheld_records(corpora: _Corpora) -> None:
    """The guard on the battery: an equality over two silences proves nothing.

    Measured, not declared: every arm is answered against the control and against
    the corpus that never held the withheld records, and the arms whose answers
    differ are the arms that really reach the plant. That measured set is compared
    against :attr:`_Arm.reaches_withheld`, so **both** directions redden -- an arm
    that quietly stopped biting because the corpus was edited, and an arm marked as
    inert that has started returning withheld rows.

    Without this, ``test_every_query_in_the_battery_...`` above would keep passing
    over a corpus whose planted records had become unreachable, which is the way a
    query battery stops testing anything.
    """
    reaching = {
        arm.name
        for arm in BATTERY
        if _answer(corpora.control, arm.arguments) != _answer(corpora.never_held, arm.arguments)
    }
    declared = {arm.name for arm in BATTERY if arm.reaches_withheld}

    assert reaching == declared, (
        f"the battery's reach moved: arms that bite but are declared inert are "
        f"{sorted(reaching - declared)}, and arms declared to bite that no longer do are "
        f"{sorted(declared - reaching)}"
    )
    assert declared, "no arm in the battery reaches the withheld records at all"


def test_a_withheld_record_never_costs_a_visible_one_its_slot_in_the_response(
    corpora: _Corpora,
) -> None:
    """T-17's row-reach face, at the response: a slot spent on a row nobody may see.

    Equality of *values* does not settle this on its own, which is why the case is
    built rather than drawn. The withheld pull request is numbered below every
    visible one, so it sorts first; a build that wrote the withheld rows and
    removed them on the way out would hand a caller asking for two records either
    nothing or one, while a corpus that never held them returns two. The caller
    then reads off how many withheld records its query reached.

    The preconditions come first, in the order that makes a failure readable: the
    corpus must offer more matching records than the bound, and the control must
    really place a withheld record inside the contested window.
    """
    contested = {"q": SHARED_TERM, "limit": 2}
    wide = {"q": SHARED_TERM, "limit": 50}

    def paths(deployment: _Deployment, arguments: dict[str, Any]) -> list[str]:
        return [row["recordPath"] for row in _records(_answer(deployment, arguments))]

    assert len(paths(corpora.control, wide)) > 2, (
        "the corpus must offer more matching records than the bound, or no row can be "
        "displaced by anything"
    )
    assert set(paths(corpora.control, contested)) - set(paths(corpora.never_held, contested)), (
        "the control must place at least one withheld record inside the first two slots -- "
        "otherwise withholding removes nothing a visible row wanted and this case holds for "
        "any implementation"
    )
    assert paths(corpora.withholding, contested) == paths(corpora.never_held, contested), (
        "a withheld record cost a visible one its slot: the bound truncated a sequence that "
        "still counted rows the caller may not be told about"
    )
    assert len(paths(corpora.withholding, contested)) == 2, (
        "and the caller must get the records it asked for rather than a short page"
    )


def _row_for(deployment: _Deployment, arguments: dict[str, Any], leaf: str) -> dict[str, Any]:
    """The one served row whose ``recordPath`` names ``leaf``.

    Raises rather than returning ``None`` when the row is absent: every use below
    is a comparison *of* that row, and a comparison between two absences is the
    vacuous pass this whole module is written against.
    """
    rows = [row for row in _records(_answer(deployment, arguments)) if leaf in row["recordPath"]]
    assert len(rows) == 1, f"expected exactly one row naming {leaf!r} in {arguments}, got {rows}"
    return rows[0]


def test_a_visible_records_bytes_do_not_move_when_its_neighbour_is_withheld(
    corpora: _Corpora,
) -> None:
    """Family 2b: the visible row whose withheld sibling is its immediate neighbour.

    Every other case in this module compares whole responses, where a difference
    could sit anywhere. This one fixes on **one visible row** and asks whether
    anything about it moves when the row immediately beside it in the served
    sequence is withheld. The pair is alike in every ordering key -- same
    repository, same pull request, same kind, same anchor file -- and their leaves
    differ only where the order is decided, so the withheld sibling occupies the
    slot before the visible row rather than sitting somewhere else in the corpus.

    **The excerpt is compared at its cut**, which is why the visible neighbour's
    opening comment is longer than ``MAX_EXCERPT_CHARS``. A short fragment is
    served whole, so comparing it only asks whether a stored value survived; a cut
    one also carries *where the cut fell*, which is the "which part of a row
    reached a field" member of the observable-family table.

    Three premises before the property, each of which would make the equality
    hold for any implementation if it stopped being true: the **control** must
    serve both siblings, they must really be adjacent there, and the visible
    neighbour's excerpt must really arrive cut and marked. The cut premise is read
    off ``never_held`` rather than off the control on purpose -- it is the corpus
    the property compares against, so a defect that moved the cut for *both*
    corpora alike would still leave this case asserting something.
    """
    query = {"filePath": SHARED_FILE_PATH, "limit": 50}
    control_paths = [row["recordPath"] for row in _records(_answer(corpora.control, query))]
    positions = {
        leaf: index
        for index, path in enumerate(control_paths)
        for leaf in (ADJACENT_VISIBLE_THREAD_ID, ADJACENT_WITHHELD_THREAD_ID)
        if leaf in path
    }

    assert set(positions) == {ADJACENT_VISIBLE_THREAD_ID, ADJACENT_WITHHELD_THREAD_ID}, (
        f"the control must serve both halves of the pair for this filter; it served {control_paths}"
    )
    assert (
        abs(positions[ADJACENT_VISIBLE_THREAD_ID] - positions[ADJACENT_WITHHELD_THREAD_ID]) == 1
    ), (
        f"the two siblings are not adjacent in the control's served order ({control_paths}), so "
        f"this case is no longer about a withheld row sitting immediately beside a visible one"
    )

    reference = str(_row_for(corpora.never_held, query, ADJACENT_VISIBLE_THREAD_ID)["excerpt"])
    assert len(reference) == MAX_EXCERPT_CHARS + 3 and reference.endswith("..."), (
        f"the visible neighbour's excerpt is {len(reference)} characters and is not marked as "
        f"cut, so this case compares a whole stored value and says nothing about where a cut "
        f"fell"
    )

    for arguments in (query, {"filePath": SHARED_FILE_PATH}, {"q": SHARED_TERM, "limit": 50}):
        withholding = _row_for(corpora.withholding, arguments, ADJACENT_VISIBLE_THREAD_ID)
        never_held = _row_for(corpora.never_held, arguments, ADJACENT_VISIBLE_THREAD_ID)
        assert withholding == never_held, (
            f"a visible row's own published fields moved with the record beside it: {arguments} "
            f"served it as {withholding} against {never_held}"
        )
        assert withholding["excerpt"] == reference, (
            f"the cut fell somewhere else for {arguments} than it does over the whole page, so "
            f"what a caller reads of a visible comment depends on a record it may not be told "
            f"about"
        )


def test_the_page_boundary_bit_does_not_move_with_a_withheld_record(
    corpora: _Corpora,
) -> None:
    """``truncated`` is the one published value that is not a served field.

    It is computed from a read of ``limit + 1`` records, so a build that wrote the
    withheld rows and filtered them afterwards would flip it while every served
    field stayed identical -- one bit, on a page whose rows are all visible,
    answering "is there more" about content the caller may not see.

    The page is set exactly at the visible count, which is the only width where the
    bit can separate: below it both corpora truncate, above it neither does.
    """
    edge = {"q": SHARED_TERM, "limit": len(VISIBLE_CORPUS)}

    withholding = _payload(_answer(corpora.withholding, edge))
    never_held = _payload(_answer(corpora.never_held, edge))
    control = _payload(_answer(corpora.control, edge))

    assert (never_held["count"], never_held["truncated"]) == (len(VISIBLE_CORPUS), False), (
        "the page must sit exactly on the visible corpus, or the bit cannot separate"
    )
    assert control["truncated"] is True, (
        "the control must find a matching record past the same page, or this case would "
        "hold for an implementation that never sets the bit at all"
    )
    assert (withholding["count"], withholding["truncated"]) == (len(VISIBLE_CORPUS), False), (
        "the page-boundary bit moved with a withheld record: `truncated` answered a "
        "question about rows the caller may not be told about"
    )


def test_asking_by_a_withheld_records_own_values_refuses_nothing_and_reveals_nothing(
    corpora: _Corpora,
) -> None:
    """SEC-13. "Withheld" and "never existed" must not be two different events.

    A refusal that fires for one input and not another is a member of the
    observable family in its own right, and it is the one an equality over
    *returned rows* cannot see: a surface that answered "no such record" for a key
    nobody ever landed and refused with "you may not read that" for a withheld one
    would satisfy every row comparison while confirming the record exists.

    Four arms, and the last is the guard on the guard: a filter naming a value only
    a **visible** record carries must still return that record, or the three empty
    answers above are satisfied by a deployment that serves nothing.
    """
    withheld_author = {"author": PLANTED_AUTHOR}
    absent_author = {"author": ABSENT_AUTHOR}
    visible_author = {"author": "USER_VISIBLE"}

    assert _records(_answer(corpora.withholding, withheld_author)) == [], (
        "a filter naming a withheld record's own author must answer with no records rather "
        "than with a refusal that says the record is there"
    )
    assert _answer(corpora.withholding, withheld_author) == _answer(
        corpora.withholding, absent_author
    ), "and identically to a filter naming an author no record ever carried"
    assert _answer(corpora.withholding, withheld_author) == _answer(
        corpora.never_held, withheld_author
    ), "and identically to the same filter against a corpus that never held the record"
    assert _records(_answer(corpora.withholding, visible_author)), (
        "the guard on this guard: an author the caller may read must come back, or the three "
        "empty answers above agree because this deployment serves nothing"
    )


def test_the_control_serves_the_withheld_records_through_the_very_same_tool(
    corpora: _Corpora,
) -> None:
    """The positive control, stated once as a case rather than only as a precondition.

    Everything above is an equality, and an equality between two deployments that
    can serve nothing holds for any implementation. This is the case that says the
    planted records are ordinary, reachable review evidence: built with an empty
    withheld set, through the same builder, the same store and the same tool, every
    planted value comes back on the wire.

    It is also what fixes the *direction* of the property. Without it, a build that
    dropped every record would satisfy this file completely.
    """
    answer = _answer(corpora.control, {"q": PLANTED_PAYLOAD})
    rows = _records(answer)

    assert len(rows) == len(WITHHELD_CORPUS), (
        f"the control must serve every withheld record for this query; it served {len(rows)}"
    )
    for value in PLANTED_VALUES:
        assert value in answer, (
            f"the control's response does not carry {value!r}, so the assertions elsewhere "
            f"that it is absent are looking for a string this corpus never publishes"
        )
    assert {row["kind"] for row in rows} == {"pull-request", "review-thread"}, (
        "both withheld kinds must be reachable, or one of the two withheld record keys is "
        "never exercised"
    )


def test_an_empty_corpus_refuses_like_a_full_one_and_serves_a_well_formed_query(
    corpora: _Corpora, empty_deployment: _Deployment
) -> None:
    """The empty-corpus arm this module's own docstring records as undriven.

    ``mcp/review_search.py``'s claim is that nothing it says varies with a
    project's contents. The battery above drives that claim across two
    non-empty corpora (``withholding`` and ``never_held``); this drives it
    across the corpus size itself -- a deployment whose store was built from
    zero records, compared against :attr:`_Corpora.control`, the full corpus
    built with nothing withheld.

    First the premise: the empty-corpus store is a real build, not the
    missing-store case a different test already covers, or every comparison
    below would be re-measuring that refusal under a new name. Then the
    battery's own refusal arms -- bad filter shapes, an out-of-range limit --
    reused rather than restated, so this exercises exactly what the battery
    already calls a refusal: each must answer byte-identically whether the
    corpus behind it holds zero records or all of them.

    Last, the contrast that makes the equality mean something: a well-formed
    request against the empty corpus must come back as an ordinary served
    answer -- ``count`` zero, ``truncated`` false, no records -- rather than a
    refusal. Without it, the byte-equalities above would be satisfied just as
    well by a deployment that refuses everything, empty corpus or not.
    """
    assert empty_deployment.store.exists(), (
        "the empty-corpus deployment's store file must exist on disk -- this fixture "
        "stands for a real build over zero records, not the missing-store case "
        "`test_a_project_with_no_review_search_store_is_refused_not_answered_empty` "
        "already tests"
    )

    refusal_arms = [arm for arm in BATTERY if arm.name.startswith("refused-")]
    assert refusal_arms, "the battery carries no refusal arm, so nothing below is exercised"
    for arm in refusal_arms:
        empty = _answer(empty_deployment, arm.arguments)
        full = _answer(corpora.control, arm.arguments)
        assert empty == full, (
            f"the {arm.name!r} refusal differed between an empty-corpus deployment and a "
            f"full one, so the refusal text varies with what the corpus holds"
        )

    served = _payload(_answer(empty_deployment, {}))
    assert served["count"] == 0
    assert served["truncated"] is False
    assert served["records"] == []


# -- the generated arm ------------------------------------------------------------

#: ``deadline=None`` because one example is two real MCP tool calls against real
#: SQLite files; ``database=None`` because the default example database writes
#: ``.hypothesis/`` into whatever directory pytest was launched from, which for this
#: repository is the repository.
#:
#: ``derandomize`` and an explicit :data:`EXAMPLE_SEED` together, for the reason
#: ``test_absence_proof.py`` measured: ``derandomize`` alone derives its seed from
#: the test's source, so a prose-only docstring edit re-rolls every example and
#: silently changes what was checked.
_GENERATED = settings(deadline=None, derandomize=True, database=None)

#: Fixed so a docstring edit cannot silently change what was checked. Any constant
#: would do; this one is the issue number.
EXAMPLE_SEED: Final = 479


def _queries() -> st.SearchStrategy[dict[str, Any]]:
    """One generated ``review.search`` request.

    The **request** is generated rather than the corpus, which is the split this
    layer wants: the store-level proof already generates corpora, and what the tool
    adds above it is argument handling -- bounds, vocabularies, transportability,
    the page probe and the refusal envelopes. Every one of those is a place a
    response could start varying with a record the caller may not see, and each is
    reached by an *argument*.

    Out-of-range and untransportable values are in the pools on purpose: a refusal
    is an observable, and two corpora must refuse identically.
    """
    text = st.sampled_from(
        (
            *VOCABULARY,
            SHARED_TERM,
            PLANTED_PAYLOAD,
            PLANTED_PAYLOAD[:5],
            PLANTED_PAYLOAD.casefold(),
            PLANTED_DISPLAY_NAME,
            "%",
            "_",
            "100%",
            'a"b',
            "medallion OR kindled",
            "",
            "\x00",
            "\ud800",
            "z" * 401,
        )
    )
    return st.fixed_dictionaries(
        {},
        optional={
            "q": text,
            "limit": st.sampled_from((0, 1, 2, len(VISIBLE_CORPUS), 20, 50, 51, 10**500)),
            "repository": st.sampled_from((REPOSITORY, "acme/nothing-here", "")),
            "pullRequest": st.sampled_from((0, PLANTED_NUMBER, *VISIBLE_NUMBERS, 2**900)),
            "threadState": st.sampled_from(("open", "resolved", "outdated", "APPROVED", "")),
            "author": st.sampled_from((PLANTED_AUTHOR, "USER_VISIBLE", ABSENT_AUTHOR)),
            "filePath": st.sampled_from((PLANTED_FILE_PATH, "src/ledger.py", "src/absent.py")),
        },
    )


@seed(EXAMPLE_SEED)
@settings(_GENERATED, max_examples=60)
@given(arguments=_queries())
def test_no_generated_request_separates_the_two_corpora_at_the_tool(
    corpora: _Corpora, arguments: dict[str, Any]
) -> None:
    """ADR-0030 decision 6, T-17a. The property over requests nobody chose.

    The battery above covers the requests a person thought of. This covers the
    ones nobody did: which filters are present, how tight the bound is, whether the
    text is a payload substring, a LIKE metacharacter, an over-long value or one no
    UTF-8 encoder accepts. The whole serialised response -- refusals included --
    must still be equal.

    A generated example that reaches neither corpus is not discarded: an empty
    answer that is equal on both sides is still the property, and the battery's own
    reach is measured separately rather than re-asserted per example.
    """
    withholding = _answer(corpora.withholding, arguments)
    never_held = _answer(corpora.never_held, arguments)

    assert withholding == never_held, (
        "a published value varies with a record the caller may not be told about"
    )
    for value in PLANTED_VALUES:
        assert value not in withholding, f"and no withheld {value!r} may reach the response"


# -- guards on this file's own premises -------------------------------------------


def test_no_visible_record_can_spell_a_withheld_value() -> None:
    """The premise every equality above rests on, checked over the rendered corpus.

    ``LIKE`` folds the 26 ASCII letters, so a visible record whose text could
    contain a planted value would separate the two corpora for a reason that is not
    a leak -- and the obvious response to that failure is to relax the assertion
    that caught it.

    Two populations, because two different filters reach them. The alphabet claim
    ranges over :func:`_matchable_text` -- the fragments ``q`` substring-matches --
    where a shared letter really could produce a partial match. The
    "spells a planted value" claim ranges over :func:`_authored_values`, which adds
    the file path and the participant names: no substring filter reaches those, but
    a *whole* planted value appearing in one would separate the corpora just as
    surely.

    Both are computed from the **real visible corpus** rather than from
    :data:`VOCABULARY`, because a body assembled from something outside the
    vocabulary is exactly the drift a constant-only guard cannot see.
    """
    matchable = " ".join(_matchable_text(VISIBLE_CORPUS)).casefold()
    authored = " ".join(_authored_values(VISIBLE_CORPUS)).casefold()
    letters = {character for character in matchable if character.isalpha()}

    assert matchable, "the guard found no visible text at all, so it ranges over nothing"
    assert not letters & set(PAYLOAD_ALPHABET.casefold()), (
        f"the visible corpus uses {sorted(letters & set(PAYLOAD_ALPHABET.casefold()))} from the "
        f"payload alphabet; a visible record could then carry a planted substring and separate "
        f"the corpora without anything leaking"
    )
    for value in PLANTED_VALUES:
        assert value.casefold() not in authored, f"a visible record spells {value!r}"
    assert min(VISIBLE_NUMBERS) > PLANTED_NUMBER, (
        "the withheld pull request must sort before every visible one, or the "
        "limit-contested arm cannot show a slot being spent on it"
    )


def test_the_two_corpora_differ_by_exactly_the_withheld_records() -> None:
    """The fixture's own arithmetic, so a corpus edit cannot make the pair unequal.

    ``withholding`` is built from the visible corpus **plus** the withheld one with
    those keys withheld; ``never_held`` from the visible corpus alone. If the two
    ever stopped being the same set of kept records, every equality in this module
    would be comparing two different corpora and would fail for a reason that is
    not a leak.
    """
    kept = {record.record_key for record in (*VISIBLE_CORPUS, *WITHHELD_CORPUS)} - WITHHELD_KEYS

    assert {record.record_key for record in WITHHELD_CORPUS} == WITHHELD_KEYS
    assert kept == {record.record_key for record in VISIBLE_CORPUS}
    assert len(WITHHELD_KEYS) == len(WITHHELD_CORPUS) == 3, (
        "every withheld record must have a distinct key -- a pull request is keyed by its "
        "number and a thread by its node id, and a collision would leave one key untested"
    )
    assert {ADJACENT_WITHHELD_THREAD_ID, PLANTED_THREAD_ID} <= WITHHELD_KEYS, (
        "the two withheld threads are the pull-request-keyed record's counterpart and the "
        "adjacent sibling; losing either leaves one of the two key shapes, or the adjacency "
        "case's own plant, out of the withheld set"
    )
    assert ADJACENT_WITHHELD_THREAD_ID not in ADJACENT_VISIBLE_THREAD_ID, (
        "the visible neighbour's id contains the withheld sibling's, so every "
        "`planted value not in the response` assertion would fire on a row the caller is "
        "entitled to -- a red test with nothing wrong behind it"
    )
