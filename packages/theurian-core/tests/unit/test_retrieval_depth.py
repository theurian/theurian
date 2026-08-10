"""How deep the retrievers are read, counted in passes (T-17, SEC-13).

Moving the visibility gate *inside* the ranking closed the content channel and
opened a smaller one. :meth:`RetrievalService._visible_ranking` asks a retriever
for :data:`FIRST_PASS_DEPTH` rows and asks again, twice as deep, when too few of
them survived the gate — so the number of SQL round-trips a request makes is a
function of how many withheld rows the query matched, and round-trips are
observable as latency.

:data:`FIRST_PASS_DEPTH` is the whole of the mitigation. With a first pass of
exactly :data:`CANDIDATE_DEPTH`, a *single* withheld row forces a second read;
doubling it moves the threshold to fifty. Measured on a 61-document Japanese
corpus over 400 interleaved calls: +2.09 ms (+17.8%, 91.6% single-call
classification) at a first pass of fifty, against +0.35 ms (+3.0%, 63.0%) at a
hundred — the latter indistinguishable from the 62.1% a pipeline with no depth
loop at all scores.

**Nothing else in the suite can see that constant.** The depth loop makes the
published results identical whichever value it holds, which is the point of it;
reverting ``FIRST_PASS_DEPTH`` to ``CANDIDATE_DEPTH`` was measured to pass the
entire suite — 1,246 tests, zero failures. Only the timing moves.

So the property is pinned as a **count of reads**, not a duration. A stopwatch
assertion in CI is flaky and gets muted; a request that reads each retriever
once cannot be told from another that reads it once, and that is the same claim
without a clock.

**Every quantity below is an absolute number, and that is not a style choice.**
This file used to derive its corpus and its threshold from ``CANDIDATE_DEPTH``
— ``VISIBLE_TAIL = CANDIDATE_DEPTH * 20``, ``(CANDIDATE_DEPTH, 1)``,
``(CANDIDATE_DEPTH + 1, 2)`` — so the fixture resized itself with the constant
and only the *ratio* ``FIRST_PASS_DEPTH == 2 * CANDIDATE_DEPTH`` was pinned.
``CANDIDATE_DEPTH = 5`` and ``CANDIDATE_DEPTH = 200`` both passed this file
unchanged, and at 5 the threshold T-17 records as "fifty withheld rows" had
silently become six. The quantity the measurement was taken on is the
*difference* ``FIRST_PASS_DEPTH - CANDIDATE_DEPTH``, so the difference is what
is asserted, in the units T-17 states it in: rows.

Pure: the index is a fake, the visibility is a fake, and no file is opened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import NamedTuple, final

import pytest
from fakes import truncating, whole

from theurian.application.retrieval_service import (
    RetrievalError,
    RetrievalService,
    SearchRequest,
)
from theurian.domain.chunking import IndexableChunk
from theurian.domain.ranking import Ranked, RetrieverPage

pytestmark = pytest.mark.unit

LEXICAL_READS = "search_lexical"
SUBSTRING_READS = "search_substring"

#: How many visible rows sit behind the withheld ones. Deep enough that no first
#: pass this file contemplates can reach the end of it, so "one pass" is never
#: one pass because the retriever ran out of rows to give.
#:
#: Absolute rather than a multiple of the constant under test — see the module
#: docstring. A corpus sized from ``CANDIDATE_DEPTH`` grows with it, so a first
#: pass that had been made twenty times deeper still saturated and every count
#: here stayed the same.
VISIBLE_TAIL = 4_000

#: How many withheld rows the mitigation promises to absorb in a single pass.
#:
#: **The number T-17 states, not a number this file computed.** The constant's
#: own docstring says doubling the first pass "moves the threshold from 'one
#: withheld row matched' to 'fifty did', which no probe for a single secret can
#: reach", and the 91.6%-versus-63.0% classification measurement was taken at
#: exactly that difference. Restating it as ``CANDIDATE_DEPTH`` would make the
#: assertion move with the implementation and pin nothing: what the threat model
#: publishes is fifty rows, so fifty rows is what is asserted.
ABSORBED_WITHHELD_ROWS = 50


class _Read(NamedTuple):
    """One retriever call: which retriever, how deep it was asked, what it gave."""

    retriever: str
    asked: int
    returned: int


def _row(number: int, item: str) -> Ranked:
    """A ranked row belonging to ``item``, distinct from every other row."""
    return Ranked(
        chunk_id=f"01K1{item.upper()}{number:04d}#0",
        item_id=f"{item}-{number:04d}",
        revision_id=f"01K1{item.upper()}{number:04d}",
    )


WITHHELD = "withheld"
VISIBLE = "visible"


def _corpus(withheld: int) -> tuple[Ranked, ...]:
    """A ranking whose top ``withheld`` rows the canonical store has withdrawn.

    Withheld rows first because that is the shape the depth loop exists for: a
    document retracted after the index was built still ranks where the index put
    it, and a query written to match it ranks it *high*. Rows further down cost
    nothing, so putting them at the top is the worst case rather than a
    contrived one.
    """
    return tuple(_row(number, WITHHELD) for number in range(withheld)) + tuple(
        _row(number, VISIBLE) for number in range(VISIBLE_TAIL)
    )


@final
class _CountingIndex:
    """One ranking, served to both retrievers, with every read recorded.

    **``limit`` is honoured the way SQL honours it, or ignored the way the scan
    below the trigram floor ignores it**, and which of the two is under test each
    time. That is not decoration: the depth loop reads three different answers
    off the same number, and a fake that could only give one of them would be
    measuring itself.

    ``honours_limit=True`` is a `LIMIT` on a lookup: it truncates, and it is
    exhausted only when the ranking did not fill the ask.
    ``honours_limit=False`` is
    ``SqliteIndexStore._scan_below_the_trigram_floor``, which has to score every
    matching row before it can name the best of them and therefore hands back its
    whole ranking, exhausted on the first call — asking it again would buy
    another full scan and no new rows.

    **Both answers used to be inferred from the row count, and that is what
    issue #16 changed.** The distinction survives the change and is the reason
    this flag still exists: the two retrievers differ in what they *do* with
    ``limit``, so a fake that modelled only one of them would be measuring
    itself. What no longer differs is how each states that it is finished.
    """

    def __init__(self, rows: tuple[Ranked, ...], *, honours_limit: bool = True) -> None:
        self._rows = rows
        self._honours_limit = honours_limit
        self.reads: list[_Read] = []

    def passes(self, retriever: str) -> int:
        return sum(1 for read in self.reads if read.retriever == retriever)

    def _serve(self, retriever: str, limit: int) -> RetrieverPage:
        page = truncating(self._rows, limit) if self._honours_limit else whole(self._rows)
        self.reads.append(_Read(retriever, asked=limit, returned=len(page.rows)))
        return page

    def search_lexical(
        self,
        query: str,  # noqa: ARG002 - named by the port; the corpus answers, not the query
        *,
        project_id: str,  # noqa: ARG002 - single-project fake
        limit: int,
        include_unapproved: bool,  # noqa: ARG002 - the index holds only approved rows here
    ) -> RetrieverPage:
        return self._serve(LEXICAL_READS, limit)

    def search_substring(
        self,
        query: str,  # noqa: ARG002 - as above
        *,
        project_id: str,  # noqa: ARG002 - as above
        limit: int,
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        return self._serve(SUBSTRING_READS, limit)

    def search_dense(
        self,
        query_vector: Sequence[float],  # noqa: ARG002 - unreachable without an embedder
        *,
        project_id: str,  # noqa: ARG002 - as above
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        # Deliberately not counted, and deliberately not raising. The dense
        # retriever is not depth-doubled -- it scores the whole index whatever it
        # is asked for -- so it has no passes to count. `RetrievalService` is
        # built below without an embedder, so this is never reached.
        return whole(())

    def chunk_texts(
        self,
        chunk_ids: Sequence[str],
        *,
        project_id: str,  # noqa: ARG002 - single-project fake
    ) -> Mapping[str, str]:
        return {chunk_id: f"passage of {chunk_id}" for chunk_id in chunk_ids}

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        raise NotImplementedError

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
    ) -> int:
        raise NotImplementedError

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        raise NotImplementedError

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        raise NotImplementedError

    def metadata(self) -> Mapping[str, object]:
        return {}


@final
class _WithoutTheWithheld:
    """The canonical store's answer once those documents have been withdrawn."""

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(row for row in ranked if not row.item_id.startswith(WITHHELD))


def _search(withheld: int, *, honours_limit: bool = True) -> _CountingIndex:
    """Run one ordinary search over a corpus withholding ``withheld`` top rows."""
    index = _CountingIndex(_corpus(withheld), honours_limit=honours_limit)
    service = RetrievalService(index)

    service.search(SearchRequest(query="gateway", project_id="demo"), _WithoutTheWithheld())

    return index


def _first_read_was_a_choice(index: _CountingIndex) -> bool:
    """Whether the first read stopped because it had enough, or because it ran out.

    Every pass count in this file is meaningless without this. A retriever with
    nothing more to give also stops at one pass, and would go on stopping at one
    however shallow — or however absurdly deep — the first pass became, so a
    saturated first read is what makes "one pass" a statement about the loop
    rather than about the corpus.
    """
    first = index.reads[0]
    return first.returned == first.asked


def test_a_single_withheld_row_does_not_cost_a_second_retrieval_pass() -> None:
    """T-17. The timing channel a caller can actually reach, and what closes it.

    An attacker probing for one secret matches *one* withheld document. If that
    alone forced a second read of both retrievers, latency would answer the
    question the response no longer does — 91.6% of single calls classified
    correctly, which is an extraction oracle of the same order as the content
    channel this milestone closed.

    The first read is checked for saturation because a count of one proves
    nothing on its own: a retriever with nothing more to give also stops at one,
    and would go on stopping at one however shallow the first pass became.
    """
    index = _search(withheld=1)

    assert _first_read_was_a_choice(index), (
        "one pass must be a choice, not the retriever running out of rows"
    )
    assert index.passes(LEXICAL_READS) == 1
    assert index.passes(SUBSTRING_READS) == 1


@pytest.mark.parametrize(
    ("withheld", "expected_passes"),
    [(ABSORBED_WITHHELD_ROWS, 1), (ABSORBED_WITHHELD_ROWS + 1, 2)],
    ids=["fifty-withheld-rows-still-one-pass", "fifty-one-forces-the-second"],
)
def test_the_second_pass_arrives_at_fifty_withheld_rows_and_not_before(
    withheld: int, expected_passes: int
) -> None:
    """T-17. Where the threshold sits, asserted from both sides of it, in rows.

    ``FIRST_PASS_DEPTH`` is a mitigation with a stated reach: a query matching
    fifty withheld rows or fewer is answered in one read, and one matching more
    is not. Fifty is the number the threat model publishes and the number the
    91.6%-versus-63.0% measurement was taken at — it is stated here as fifty and
    not as :data:`CANDIDATE_DEPTH`, because an assertion written in terms of the
    implementation moves with it. Written that way, this test passed with the
    threshold at six.

    Both edges are asserted because only the pair pins the *difference*
    ``FIRST_PASS_DEPTH - CANDIDATE_DEPTH``: the inside edge fails if the margin
    narrows, the outside edge fails if it widens, and either change alters what
    T-17 promises a reader. Whether that difference is spelled as two constants
    or as one named ``FIRST_PASS_MARGIN`` is the implementation's business —
    this asserts the behaviour either spelling has to produce.

    The residual is real and recorded rather than tested away: an index
    withholding fifty-one rows that one query matches still pays for a second
    pass. No probe for a single secret can arrange that.
    """
    index = _search(withheld=withheld)

    assert _first_read_was_a_choice(index), (
        "the corpus must outlast the first pass, or this measures exhaustion"
    )
    assert index.passes(LEXICAL_READS) == expected_passes
    assert index.passes(SUBSTRING_READS) == expected_passes


#: Withheld counts, each twice the last, for the growth test below.
#:
#: Absolute like everything else here, and chosen so every rung needs several
#: passes: a ladder starting where one pass already suffices would compare two
#: ones and hold nothing. The top rung's deepest pass stays inside
#: :data:`VISIBLE_TAIL`, so no read on it is answered by the corpus running out.
WITHHELD_LADDER = (200, 400, 800, 1600)


def _asked_depths(index: _CountingIndex, retriever: str) -> list[int]:
    """How deep each pass of one retriever reached, in order."""
    return [read.asked for read in index.reads if read.retriever == retriever]


def _every_read_was_saturated(index: _CountingIndex) -> bool:
    """Whether every pass got as many rows as it asked for.

    The stronger form of :func:`_first_read_was_a_choice`, and the counts below
    need it: a retriever that runs out mid-ladder stops deepening for a reason
    that has nothing to do with ``_deeper``, and the pass count would then be
    describing the fixture.
    """
    return all(read.returned == read.asked for read in index.reads)


def test_each_pass_reaches_twice_as_far_as_the_one_before() -> None:
    """``_deeper``, pinned at the port where it is observable (T-17, SEC-13).

    The depth loop's cost profile is decided entirely by how the next depth is
    chosen from the current one, and nothing else in the suite can see that
    choice: the published results are identical whichever rule is used, which is
    the point of the loop. ``depth * 2`` replaced by ``depth + 50`` was measured
    to pass all 1,301 tests.

    What a caller can observe is the sequence of ``limit`` values the index is
    asked for — one per SQL round-trip, each round-trip visible as latency. So
    the ratio between consecutive asks is asserted directly, on a corpus stale
    enough to force several of them.

    **Half of a guard, and the half that pins the step.** A geometric step is
    what makes the *number* of round-trips logarithmic in how many rows were
    withheld;
    ``test_doubling_the_withheld_count_costs_one_more_pass_and_not_twice_as_many``
    asserts that consequence and this one asserts the mechanism, and neither
    covers the other's blind spot:

    - the growth test alone passes for a *large* fixed step — ``+ 1000`` costs 2,
      2, 2, 3 passes on its ladder and clears its bound — because a fixed step is
      only linear once the withheld count outruns it, which its ladder does not
      reach. This test fails on any fixed step at all, at the first pair;
    - this test alone says nothing about what the step buys. It would go on
      passing for a rule that doubled while charging a round-trip nobody needed,
      because it never counts passes against a corpus.

    So deleting one of the two removes half a guard rather than a duplicate. If
    they are ever merged, the merged test has to keep both a ratio over
    consecutive asks and a pass count over a ladder of withheld corpora.

    Saturation is checked on every read, not only the first: a pass answered by
    a corpus that ran out is not a pass ``_deeper`` chose the depth of.
    """
    index = _search(withheld=1_550)

    depths = _asked_depths(index, LEXICAL_READS)

    assert _every_read_was_saturated(index), (
        "the corpus must outlast the deepest pass, or the ladder is exhaustion"
    )
    assert len(depths) >= 3, (
        "fewer than three passes cannot show a ratio; deepen the withheld count"
    )
    ratios = [after / before for before, after in pairwise(depths)]
    assert ratios == [2.0] * (len(depths) - 1), (
        f"each pass must reach twice as far as the last, got {depths}"
    )
    assert _asked_depths(index, SUBSTRING_READS) == depths, "both retrievers, one rule"


def test_doubling_the_withheld_count_costs_one_more_pass_and_not_twice_as_many() -> None:
    """T-17. What the caller measures with a stopwatch, and how much it tells them.

    Round-trips are observable as latency, so the pass count is the quantity an
    attacker reads off a search. Under a geometric step it is ``log2`` of how
    many rows the query matched and could not be shown: doubling the withheld
    corpus adds *one* read, so the latency resolves the withheld count only to
    within a factor of two. Under a fixed step it is that count divided by the
    step — a linear scale, on which each additional 50 withheld rows announces
    itself as one more round-trip.

    Measured against the shipped loop and the ``+ 50`` mutant, over this ladder:
    3, 4, 5, 6 passes against 4, 8, 16, 32.

    Asserted as *at most* one extra pass per doubling rather than exactly one,
    because that is where the safety is. A coarser step than doubling costs
    fewer round-trips and leaks less; a finer one leaks more, and only the upper
    side is a promise.

    **Half of a guard**, and the weaker half read alone. This bound is satisfied
    by a loop that never deepens at all, and by a fixed step larger than the
    ladder spans — ``+ 1000`` costs 2, 2, 2, 3 passes here and passes. What rules
    both out is ``test_each_pass_reaches_twice_as_far_as_the_one_before``, which
    pins the step itself; that test in turn cannot say what the step buys,
    because it never counts passes against a corpus. Deleting either leaves the
    other looking sufficient and being not, which is the failure mode this
    milestone keeps meeting: a green suite over a property nothing holds.
    """
    passes = {}
    for withheld in WITHHELD_LADDER:
        index = _search(withheld=withheld)
        assert _every_read_was_saturated(index), (
            f"the corpus must outlast the deepest pass at {withheld} withheld rows"
        )
        passes[withheld] = index.passes(LEXICAL_READS)

    counts = [passes[withheld] for withheld in WITHHELD_LADDER]
    growth = [after - before for before, after in pairwise(counts)]

    assert counts[0] > 1, "the ladder must start where the loop already deepens"
    assert max(growth) <= 1, (
        f"a doubling of the withheld count may add at most one pass, got {counts}"
    )


def test_the_deeper_first_pass_costs_nothing_when_nothing_is_withheld() -> None:
    """The healthy index — every project that has just run `index build`.

    A mitigation that charged an extra round-trip to every ordinary query would
    be paid for by everyone to protect against a corpus state most projects are
    never in. The loop's first exit is the common one, so a healthy index reads
    each retriever exactly once, at any first-pass depth.
    """
    index = _search(withheld=0)

    assert _first_read_was_a_choice(index), (
        "the corpus must outlast the first pass, or this measures exhaustion"
    )
    assert index.passes(LEXICAL_READS) == 1
    assert index.passes(SUBSTRING_READS) == 1


@pytest.mark.parametrize(
    ("withheld", "visible"),
    [
        (0, VISIBLE_TAIL),
        (1, VISIBLE_TAIL),
        (ABSORBED_WITHHELD_ROWS + 1, VISIBLE_TAIL),
        (VISIBLE_TAIL, ABSORBED_WITHHELD_ROWS - 1),
        (VISIBLE_TAIL, 0),
    ],
    ids=[
        "healthy",
        "one-withheld",
        "past-the-threshold",
        "too-few-survive-to-satisfy-the-loop",
        "nothing-survives",
    ],
)
def test_a_retriever_that_ignores_its_limit_is_read_once_however_much_is_withheld(
    withheld: int, visible: int
) -> None:
    """The branch every two-character CJK query takes, and what a pass costs there.

    ``FIRST_PASS_DEPTH`` is a mitigation sized in *round-trips*, on the reading
    that a round-trip is cheap. It is, on the trigram lookup: a `LIMIT` on an
    FTS5 query bounds the index walked. It is not on
    ``SqliteIndexStore._scan_below_the_trigram_floor``, where `ORDER BY
    matched_characters DESC` scores every matching row whatever the limit says.
    Measured on 6,000 chunks of 1,000 CJK characters, ``limit=100`` cost 0.055 s
    and ``limit=12,800`` cost 0.064 s — so each doubling bought a whole extra
    half-second and no bound at all. Six passes, 3.06 s, against 0.51 s for the
    one pass a healthy index needs, on the corpus state the codebase calls
    ordinary: the window between ``migrate apply`` and ``index build``.

    So that retriever hands back its whole ranking, and the loop reads *more rows
    than it asked for* as the retriever saying it never truncated. The pass count
    on this branch is then independent of the withheld count outright, rather
    than thresholded as it is on the lookup — which is why this is parametrised
    across the range: nothing here may move with ``withheld``.

    **The last two cases are the ones that can fail.** While fifty rows still
    survive the gate, the loop's *first* exit fires and the branch under test is
    never reached: written with a deep visible tail alone, this passes with the
    old ``len(ranked) < depth`` test in place, which asks a retriever that
    already gave everything for twice as much, seven times over. So the corpus
    has to be one where too little survives to satisfy the loop — which is
    exactly the stale index the depth loop exists for.

    ``_first_read_was_a_choice`` is inverted rather than reused. Here a first
    read that came back *saturated* would mean the fake truncated after all, and
    the count would be measuring the wrong retriever.
    """
    rows = tuple(_row(n, WITHHELD) for n in range(withheld)) + tuple(
        _row(n, VISIBLE) for n in range(visible)
    )
    index = _CountingIndex(rows, honours_limit=False)

    RetrievalService(index).search(
        SearchRequest(query="gateway", project_id="demo"), _WithoutTheWithheld()
    )

    first = index.reads[0]
    assert first.returned > first.asked, (
        "this retriever must be the kind that overshoots, or the test is the lookup again"
    )
    assert index.passes(LEXICAL_READS) == 1
    assert index.passes(SUBSTRING_READS) == 1


# -- The obligation the signal creates ---------------------------------------
#
# The loop believes what it is told, which is the point of it and also the one
# thing the row-count inference could not go wrong at: `len(ranked) != depth`
# terminated whatever an adapter claimed. `exhausted` does not, so the loop
# carries a liveness guard, and this is what holds it.


@final
class _NeverFinished:
    """A retriever that always has rows and never admits to running out.

    Not a shape any conforming adapter can take: every `IndexStore` method ranks
    best-first and counts `limit` from the top, so a retriever that hands back
    the same rows at twice the depth has nothing further by definition. It is the
    shape a *defective* adapter takes -- one that computed `exhausted` from a
    predicate that is never true -- and before the guard it hung the caller.

    `fakes.truncating` deliberately cannot produce it, which is why this
    constructs `RetrieverPage` directly.
    """

    def __init__(self) -> None:
        self.calls = 0

    def _serve(self, limit: int) -> RetrieverPage:  # noqa: ARG002 - the ask is ignored
        self.calls += 1
        # One row, so the page invariant is satisfied and the *loop* is what has
        # to catch this rather than the value object.
        return RetrieverPage(rows=(_row(0, WITHHELD),), exhausted=False)

    def search_lexical(
        self,
        query: str,  # noqa: ARG002 - the fake answers, not the query
        *,
        project_id: str,  # noqa: ARG002 - single-project fake
        limit: int,
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        return self._serve(limit)

    def search_substring(
        self,
        query: str,  # noqa: ARG002 - as above
        *,
        project_id: str,  # noqa: ARG002 - as above
        limit: int,
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        return self._serve(limit)

    def search_dense(
        self,
        query_vector: Sequence[float],  # noqa: ARG002 - unreachable without an embedder
        *,
        project_id: str,  # noqa: ARG002 - as above
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        return whole(())

    def chunk_texts(
        self,
        chunk_ids: Sequence[str],
        *,
        project_id: str,  # noqa: ARG002 - single-project fake
    ) -> Mapping[str, str]:
        return {chunk_id: f"passage of {chunk_id}" for chunk_id in chunk_ids}

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        raise NotImplementedError

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
    ) -> int:
        raise NotImplementedError

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        raise NotImplementedError

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        raise NotImplementedError

    def metadata(self) -> Mapping[str, object]:
        return {}


#: Passes before the liveness guard fires. Two, and the reason it cannot be one:
#: `served` starts at -1 so the first pass always makes "progress", and the
#: comparison needs a predecessor. Asserted exactly rather than as a bound --
#: `<= 3` was what stood here, and it would have passed a loop that deepened one
#: further time before noticing, which is one more doubling of `depth`.
GUARD_FIRES_AFTER = 2


def test_a_retriever_that_never_reports_exhaustion_is_refused_not_looped() -> None:
    """The failure mode the explicit signal introduced, and its bound.

    Without the progress check this test does not fail -- it does not finish.
    That is the whole reason the check exists: `_deeper` doubles with no ceiling,
    and an adapter whose `exhausted` is wrong in the safe-looking direction
    (always `False`) turns one search into an unbounded sequence of them.

    It refuses rather than returning what it has, because a silent truncation
    here is a visible ranking shorter than a conforming adapter would have given,
    with nothing in the response naming why -- the exact failure this change
    exists to make impossible.
    """
    index = _NeverFinished()

    with pytest.raises(RetrievalError, match="cannot make progress"):
        RetrievalService(index).search(
            SearchRequest(query="gateway", project_id="demo"), _WithoutTheWithheld()
        )

    assert index.calls == GUARD_FIRES_AFTER, (
        f"the guard must fire on the pass that first fails to make progress, which is the "
        f"second: the first has no predecessor to compare against, so `served` starts at -1 "
        f"and any row count beats it. The retriever was called {index.calls} times. More "
        f"means the loop deepened again before noticing, and each extra pass is a further "
        f"doubling of `depth` -- the cost this guard exists to bound."
    )


def test_the_guard_cannot_fire_for_a_retriever_that_keeps_finding_more() -> None:
    """The control. A guard that fired on honest deepening would cost recall.

    `_CountingIndex` with a corpus deeper than any pass here means every read
    fills its ask and the next read returns strictly more, which is what an
    honest un-exhausted retriever looks like. The search must complete.
    """
    index = _search(ABSORBED_WITHHELD_ROWS + 1)

    assert index.passes(LEXICAL_READS) >= 2, (
        "the fixture must actually deepen, or the guard is untested by this control"
    )
