"""When the gate's canonical session is acquired, relative to what it withholds.

A companion to :mod:`test_retrieval_depth`, which counts *retriever* reads. This
counts the reads the gate makes on the other side: acquiring the canonical
session it ranks through, and asking that session about rows.

:meth:`ResultGate.admit` shows the caller nothing of what the canonical store
withheld, so anything it does *only when something was found* answers the
question the response is refusing to answer. Acquiring the session lazily was
exactly that — ``CanonicalVisibility.cleared`` is a comprehension, so a query
matching no indexed chunk never calls ``get_item``, never opened the SQLite
connection, and skipped the 0.4 ms of connect, pragmas and schema check that
every other request paid.

Measured on a 61-document Japanese corpus before the fix: one ordinary
``knowledge.search`` classified a probe against a one-character-different control
88.3% of the time, and six characters of a credential no response contains came
back in 836 calls with the body never read. After: 57.8%, which is chance.

**The last two tests count something that is not a fix.**
:meth:`~theurian.application.visibility.CanonicalVisibility.cleared` is total
over its input, so the number of canonical reads is exactly ``len(ranked)``.
Where the retriever truncates, that is ``depth`` whatever was withheld; where it
does not — the scan below the trigram floor, whose statement carries no
``LIMIT`` — it is the visible rows *plus* the withheld ones, so one more withheld
row costs one more lookup, at one-row granularity, with no threshold anywhere.
That second shape is the **duration face of T-17a**
(``docs/security/threat-model.md``, issue #15) and an accepted residual, not a
property that makes anything safer: walking the whole ranking is never cheaper
than truncating, and is far worse where the withdrawn rows rank below the
fiftieth visible one. It is pinned for the reason the BM25 flip is pinned in
``tests/integration/test_retrieval_service.py`` — an accepted residual whose
scope must not move, in *either* direction, without whoever moved it re-arguing
the acceptance. The Milestone 6 purge is expected to move it, and those two tests
are then rewritten rather than deleted.

Pure: the store is a fake, the candidate source is a fake, and no file is opened.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, final

import pytest

from theurian.application.retrieval_service import (
    CANDIDATE_DEPTH,
    CandidateSource,
    ResultGate,
    ResultRequest,
    SearchOutcome,
    Surfaced,
)
from theurian.application.visibility import CanonicalVisibility, Visibility
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeItem, KnowledgeRevision
from theurian.domain.ranking import Ranked
from theurian.domain.values import ValidityPeriod

pytestmark = pytest.mark.unit

PROJECT = ProjectId("demo")
CONTEXT = RequestContext(project_id=PROJECT)

#: Frozen. Nothing here reads a clock, and a fixture that did would make the
#: read counts below depend on when the suite ran.
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ulid(number: int) -> str:
    """A distinct, valid 26-character Crockford-base32 ULID for row ``number``.

    Full-length rather than the eight characters this file used to carry: a row
    only *clears* if the canonical item behind it points at a ``RevisionId``
    equal to the row's, and ``RevisionId`` validates. A short id makes every row
    unclearable by construction, which is how the read-count test below came to
    be measuring a code path it never entered.
    """
    return f"01K1A{number:021d}"


def _row(number: int) -> Ranked:
    """One retriever row, with an item id and a revision id distinct to ``number``."""
    return Ranked(
        chunk_id=f"{_ulid(number)}#0",
        item_id=f"architecture.a{number:04d}",
        revision_id=_ulid(number),
    )


def _approved_item(row: Ranked) -> KnowledgeItem:
    """The canonical item behind ``row``, in the one state that lets it surface.

    Both halves are load-bearing to
    :class:`~theurian.application.visibility.CanonicalVisibility`: a status that
    is not approved, or a ``current_revision_id`` other than the one the index
    ranked, is withheld. An item built any other way makes a row *known* to the
    session and still not clear, which is a fixture that reaches the lookup but
    not the branch after it.
    """
    return KnowledgeItem(
        item_id=ItemId(row.item_id),
        project_id=PROJECT,
        namespace="architecture",
        kind=KnowledgeKind.ARCHITECTURE,
        status=KnowledgeStatus.APPROVED,
        current_revision_id=RevisionId(row.revision_id),
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=NOW),
    )


@final
class _RecordingSession:
    """A canonical read session that says when it was acquired and when it was read.

    Honours the port's ``__enter__`` contract — "acquire the handle here, not at
    the first read" — by recording the acquisition as the event it is.

    ``known`` is what makes a row surfaceable: the session hands back an approved
    item for those rows and ``None`` for every other, which is exactly how the
    real store withholds a document retired after the index was built. It
    defaults to nothing, and a session built that way withholds everything —
    correct for the three ordering tests, and *wrong* for anything measuring what
    a scan costs, because a branch that ends "once fifty rows have cleared"
    cannot be reached when none ever do.
    """

    def __init__(self, log: list[str], known: Sequence[Ranked] = ()) -> None:
        self._log = log
        self._known = {row.item_id: _approved_item(row) for row in known}

    def __enter__(self) -> _RecordingSession:
        self._log.append("acquired")
        return self

    def __exit__(self, *details: object) -> None:
        self._log.append("released")

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        raise NotImplementedError  # pragma: no cover - the gate never lists

    def get_item(
        self,
        context: RequestContext,  # noqa: ARG002 - named by the port; this fake is project-blind
        item_id: ItemId,
    ) -> KnowledgeItem | None:
        self._log.append("get_item")
        return self._known.get(item_id.value)

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None:
        raise NotImplementedError  # pragma: no cover - the gated requests withhold every row


def _shape(surfaced: Surfaced) -> dict[str, Any]:
    raise NotImplementedError  # pragma: no cover - the gated requests withhold every row


def _admit(log: list[str], source: CandidateSource) -> None:
    """One ordinary gated request, against a store that withholds everything."""
    ResultGate(store_factory=lambda _path: _RecordingSession(log), shape=_shape).admit(
        ResultRequest(
            database=Path("/nonexistent/state.sqlite"),
            project_id="demo",
            include_unapproved=False,
            limit=10,
            budget_tokens=2000,
        ),
        source,
    )


def test_the_canonical_session_is_acquired_before_the_retrievers_run() -> None:
    """Acquisition happens on the way in, not on the first row that needs judging.

    The order is the assertion. A session acquired at its first ``get_item``
    would appear *after* the source ran, and only when the source had produced
    something for the visibility to judge — which is the leak, stated as a
    sequence rather than as a stopwatch.
    """
    log: list[str] = []

    def source(visible: Visibility) -> SearchOutcome:
        log.append("retrieved")
        visible.cleared((_row(0),))
        return SearchOutcome(candidates=())

    _admit(log, source)

    assert log == ["acquired", "retrieved", "get_item", "released"]


def test_a_query_that_matches_nothing_still_pays_for_the_session() -> None:
    """The case the whole thing turns on, and the one no equality test can see.

    Nothing about the response changes: it was ``count: 0`` before and it is
    ``count: 0`` now. What changes is that the two requests cost the same, which
    is why this is asserted as a sequence of events rather than as a field of a
    payload. Reverting ``SqliteCanonicalStore.__enter__`` to ``return self``
    leaves every published value identical and fails here.
    """
    log: list[str] = []

    def source(visible: Visibility) -> SearchOutcome:
        log.append("retrieved")
        # No rows at all: the retriever matched nothing, so the visibility is
        # never asked about anything and the store is never read.
        assert visible.cleared(()) == ()
        return SearchOutcome(candidates=())

    _admit(log, source)

    assert "get_item" not in log, "nothing was judged, which is the precondition"
    assert log == ["acquired", "retrieved", "released"], (
        "and the session was still acquired, so the two requests cost the same"
    )


def test_the_session_is_released_even_when_the_source_raises() -> None:
    """Acquiring earlier must not make a failed search leak a handle.

    ``sqlite3.connect`` used as a context manager commits but does not close, and
    the gate is now the code that opens the connection rather than the code that
    happens to be first to read from it.
    """
    log: list[str] = []

    def source(visible: Visibility) -> SearchOutcome:
        msg = "the index could not be read"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="could not be read"):
        _admit(log, source)

    assert log == ["acquired", "released"]


# -- What a scan costs: the duration face of T-17a -------------------------

#: How many surfaceable rows every ranking measured below carries.
#:
#: More than :data:`CANDIDATE_DEPTH`, so a ``cleared`` that stopped once fifty
#: rows had passed would stop *inside* the ranking with rows left unread. At
#: fifty or fewer the break condition is never reached and the measurement
#: silently becomes a measurement of nothing — which is what the previous version
#: of this test did, with a session that withheld every row.
#:
#: Absolute rather than ``CANDIDATE_DEPTH + 10``, for the reason
#: ``tests/unit/test_retrieval_depth.py`` sets out: a fixture sized from the
#: constant it is measuring against resizes with it and stops reaching the
#: branch without failing. The relationship is asserted instead, in
#: ``test_the_measured_ranking_clears_more_rows_than_a_short_circuit_would_stop_at``.
VISIBLE_HEAD = 60

#: The withheld counts the same answer is priced at.
#:
#: Four points rather than one, because one point pins a constant and any
#: implementation that happened to read that many rows would satisfy it. Nought
#: is the baseline; **one** is the finest grain the channel has and the reason it
#: is worth stating at all; fifty is where a short circuit at
#: :data:`CANDIDATE_DEPTH` would put a threshold; two hundred is well past any
#: plausible cut.
WITHHELD_COUNTS = (0, 1, 50, 200)

#: Where the withdrawn rows sit in the ranking, by name.
#:
#: Both, because "a function of the ranking's length" is a claim about the
#: length *and nothing else*, and a short circuit satisfies neither arrangement
#: the same way: withheld-first it reads fifty plus the withdrawn rows above the
#: cut, visible-first it reads fifty however many were withdrawn. Measuring one
#: arrangement leaves the other free to be a different function.
WITHHELD_FIRST = "withheld ranked above the visible rows"
WITHHELD_LAST = "withheld ranked below the visible rows"


class _Measured(NamedTuple):
    """What one ``cleared`` call returned, and what it cost the canonical store."""

    #: How many rows the caller would have been shown.
    cleared: int
    #: How many times the canonical session was asked about an item.
    reads: int


def _measure(withheld: int, placement: str) -> _Measured:
    """Clear one ranking of :data:`VISIBLE_HEAD` surfaceable rows plus ``withheld`` withdrawn ones.

    Every row carries its own item id, so the per-item memoisation inside
    ``CanonicalVisibility`` collapses nothing here and a read count is a row
    count.
    """
    visible = tuple(_row(number) for number in range(VISIBLE_HEAD))
    withdrawn = tuple(_row(VISIBLE_HEAD + number) for number in range(withheld))
    ranking = withdrawn + visible if placement == WITHHELD_FIRST else visible + withdrawn

    log: list[str] = []
    session = _RecordingSession(log, known=visible)
    admitted = CanonicalVisibility(session, CONTEXT, include_unapproved=False).cleared(ranking)

    return _Measured(cleared=len(admitted), reads=log.count("get_item"))


def test_the_canonical_read_count_is_the_ranking_length_and_so_the_withheld_count() -> None:
    """One withheld row costs one canonical lookup — the duration face of T-17a.

    **This is a pin on an accepted residual, not on a safety property**, and
    reading it the other way is how the residual would be defended rather than
    closed. ``cleared`` is total over its input, so the canonical read count is
    ``len(ranked)`` and nothing else about the ranking — the same count whichever
    end the withdrawn rows sit at, which is why both placements are measured.
    ``cleared`` is driven here directly with a whole match set, which is the shape
    the scan below the trigram floor hands it — no ``LIMIT``, so the length *is*
    the visible rows plus the withheld ones and a request pays for documents its
    caller may not read. Measured at about 15 us per distinct document, and on
    this pipeline at 6.047 ms with 400 documents retired after the build against
    0.163 ms with none: linear, one row at a time, with no threshold in it. Finer
    than the fifty-row staircase
    :data:`~theurian.application.retrieval_service.FIRST_PASS_DEPTH` leaves one
    layer up, which is where the pass-count face of the same residual lives.

    Walking the whole ranking is not what makes any of this safe, and nothing
    here does. It is never cheaper than truncating and is far worse where the
    withdrawn rows rank below the fiftieth visible one — 3,000 visible rows with
    1,000 withheld below the cut cost 4,000 reads against the 50 a short circuit
    would cost. What totality buys, where it buys anything, is a *coarser*
    observable on the truncating branch rather than less work; on this branch it
    buys neither. ``visibility.py`` carries that argument, and this test carries
    the number it rests on.

    **Nothing that inspects a response can see this.** Every published value is
    identical across all eight measurements below, so no assertion over a payload
    moves; ``test_retrieval_depth.py`` counts retriever passes and
    ``test_scan_exhaustion.py`` counts SQLite executions, and a break at
    :data:`~theurian.application.retrieval_service.CANDIDATE_DEPTH` moves neither.
    Verified by running one: with that break in place, every unit test and every
    retrieval integration suite — 877 of them — leaves only this test and its
    guard below red.

    It closes when the index stops holding withdrawn rows: the Milestone 6 purge,
    issue #15, and nothing smaller. Whoever makes these numbers stop reproducing
    owes the T-17a acceptance a re-argument, in either direction.
    """
    measured = {
        (withheld, placement): _measure(withheld, placement).reads
        for withheld in WITHHELD_COUNTS
        for placement in (WITHHELD_FIRST, WITHHELD_LAST)
    }

    assert measured == {
        (withheld, placement): VISIBLE_HEAD + withheld
        for withheld in WITHHELD_COUNTS
        for placement in (WITHHELD_FIRST, WITHHELD_LAST)
    }, (
        "the canonical read count must be the ranking's length -- visible plus "
        "withheld -- at every withheld count and whichever way the ranking is "
        "ordered. A count that stopped tracking it means a short circuit landed "
        "in `CanonicalVisibility.cleared`, or the T-17a residual moved"
    )


def test_the_measured_ranking_clears_more_rows_than_a_short_circuit_would_stop_at() -> None:
    """Guards the measurement above, which a fixture can pass without performing.

    The test this replaced asserted two hundred ``get_item`` calls against a
    session that returned ``None`` for every item. Nothing cleared, so a
    ``cleared`` that broke once fifty rows had passed never reached its own break
    condition: the review that found it measured the whole suite at its exact
    baseline with that break in place, and reproducing the old shape against the
    break here passes too. The assertion was true, and it measured a code path it
    never entered.

    So the property the fixture has to have is stated as an assertion rather than
    left to a constant: more rows clear than a cut at
    :data:`~theurian.application.retrieval_service.CANDIDATE_DEPTH` would stop
    at. If ``CANDIDATE_DEPTH`` is ever raised past :data:`VISIBLE_HEAD` this
    fails here, loudly, instead of the measurement above quietly going hollow.
    """
    assert VISIBLE_HEAD > CANDIDATE_DEPTH, (
        f"the ranking carries {VISIBLE_HEAD} surfaceable rows and a short circuit "
        f"would stop at {CANDIDATE_DEPTH}; with the first no larger than the "
        f"second there is no cut for the measurement above to detect"
    )

    surviving = {
        placement: _measure(200, placement).cleared for placement in (WITHHELD_FIRST, WITHHELD_LAST)
    }

    assert surviving == {WITHHELD_FIRST: VISIBLE_HEAD, WITHHELD_LAST: VISIBLE_HEAD}, (
        f"every one of the {VISIBLE_HEAD} surfaceable rows must survive the gate, "
        f"wherever the withdrawn ones rank; fewer means `cleared` stopped early, "
        f"and the read counts above are measuring a scan that no longer happens"
    )


# -- An id that came out of the index (SEC-7, ADR-0004) -----------------------
#
# `Ranked.item_id` is a cell of a derived, unsigned, git-ignored file, and
# `CanonicalVisibility` builds an `ItemId` out of it. Validation refusing that
# string used to raise: an `InvalidIdentifierError` is a `DomainError`, so
# `hybrid_answer`'s `IndexBuildError` handler never saw it and it reached the
# agent as a bare tool failure naming no remedy -- measured in 3 of 400 fixtures
# with 1 to 40 random bytes corrupted past the first page.
#
# The argument for treating the field as untrusted was already written nineteen
# lines below it, for `revision_id` on the same row: an id that failed validation
# "would raise here rather than simply fail to match". This is that reasoning
# applied to the other id.

#: Two ids no rule this domain could adopt would accept, so this stays a test
#: about the *refusal* rather than about today's identifier grammar. The second
#: is the exact shape the corruption campaign produced.
REFUSED_ITEM_IDS = ("", "architecture.auth-poli\x06y")


@pytest.mark.parametrize("item_id", REFUSED_ITEM_IDS, ids=["empty", "control-character"])
def test_a_row_naming_an_id_the_domain_refuses_is_withheld_rather_than_raised(
    item_id: str,
) -> None:
    """A refusal is spent the way a mismatch is: the row names no item, so it goes.

    Failing towards *fewer* results is the only direction available to a file
    that is derived and unsigned (ADR-0004, SEC-7). The alternative on the table
    was to drop validation and hand the raw string to the canonical store, which
    trades a tool failure for an unvalidated identifier reaching a query.

    Both assertions are load-bearing and they fail for different implementations.
    An empty result is what a caller sees; that alone is also produced by passing
    the string through to a store that happens not to know it, which is the
    version this replaced. The read count is what says the domain refused it
    *here*, before anything was asked about it.
    """
    log: list[str] = []
    row = Ranked(chunk_id=f"{_ulid(1)}#0", item_id=item_id, revision_id=_ulid(1))

    cleared = CanonicalVisibility(
        _RecordingSession(log), CONTEXT, include_unapproved=False
    ).cleared((row,))

    assert cleared == (), "a row the domain will not name cannot be shown"
    assert log.count("get_item") == 0, "and the id is never handed to the canonical store"
