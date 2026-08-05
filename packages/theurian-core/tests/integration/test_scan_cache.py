"""A search costs one pass over the corpus, every time and only its own (T-17, SEC-13).

`SqliteIndexStore._scan_cache` is a **security mitigation, not an optimisation**,
and until this file existed nothing turned red when it was deleted.

**What it mitigates.**
:meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
has no exhaustion signal to read, so it infers one from a row count: a retriever
that hands back exactly as many rows as were asked for might have been truncated,
so it is asked again, deeper. The scan below the trigram floor has no ``LIMIT`` —
it must score every matching row before it can name the best of them — so it
answers with its entire ranking every time, and the ambiguous case is reached
exactly when that ranking totals :data:`FIRST_PASS_DEPTH` rows and fewer than
:data:`CANDIDATE_DEPTH` of them survive the canonical store. Driving
``_visible_ranking`` directly with the match count held at ``FIRST_PASS_DEPTH``
and only the withheld count varying:

======================= ==============
Rows the store withheld Scan port calls
======================= ==============
0 to 50                 1
51 to 99                2
======================= ==============

A step function of how many rows were withheld, with its edge at 51 of 100. The
number of calls is not something this file can change; what the cache changes is
what the second one *costs*. Without it that second call is a second full pass
over the corpus — 29.17 ms against 14.04 ms on a 6,000-row corpus shaped to sit
on this edge — so crossing from 50 withheld to 51 both doubles the work and tells
an observer with a stopwatch which side of the edge they are on. That is the
residual T-17 records, and the cache is what holds it shut.

**Why this file counts SQL executions and not port calls.** Counting calls to
``search_substring`` cannot see this cache at all: the call count is one or two
whether the cache is present or absent, because the cache does not change it. A
test built on a counting fake passes with ``self._scan_cache`` deleted and guards
nothing while looking like a guard. So the count taken here is how many times the
scan statement is executed *by SQLite*, read off a trace callback installed on
the real connection.

**Two properties, read off that one count, failing in opposite directions.**
Within a request, the second read of the scan retriever must cost nothing:
delete the cache and one request costs two scans. Across requests, one caller's
answer must cost the next caller nothing back: share the cache between stores —
one word, ``_scan_cache`` promoted to a class attribute — and two requests cost
one scan, because the second is answered out of what the first was told. The
second failure is the mitigation becoming the channel one level up, and neither
test rules out the other's failure.

**What the count can and cannot see.** The trace is installed by replacing
``index_store._connect``, so what it counts is statements executed on connections
opened *there*. Every read in this adapter opens its connection through
``_connect`` as things stand, which is what makes the count complete — a property
of this module today, not of SQLite, and therefore a precondition of the
measurement rather than a guarantee of it. A retriever that opened its own
connection would be invisible to both tests below, and both would go on passing
while measuring less than they claim.

**Both tests are expected to be deleted with the cache, not carried forward.**
The cache is temporary by its own docstring's admission: the real fix, tracked
as issue #16, gives ``IndexStore`` an explicit exhaustion signal, after which the
second read never happens and the cache is dead code. The two tests behave
differently on that day, and the difference is why this says *deleted* rather
than *revisited*. The first fails loudly and correctly — its precondition asserts
the retriever was read twice, and it will have been read once. The second goes on
passing: two requests still cost two scans when there is no cache at all, so it
would sit in the suite as a green test guarding nothing, which is the exact shape
this file exists to warn about. Neither is a reason to keep the cache alive. A
mitigation whose only remaining justification is a passing test is a mitigation
for nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import final

import pytest

from theurian.application.retrieval_service import (
    CANDIDATE_DEPTH,
    FIRST_PASS_DEPTH,
    RetrievalService,
    SearchRequest,
)
from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.ranking import Ranked
from theurian.infrastructure.sqlite import index_store as index_store_module
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT = "demo"

#: Two characters, so it is below the trigram floor and `search_substring` falls
#: through to the scan. Every other substring query in the suite is three
#: characters or more and is answered by the trigram lookup, which has a `LIMIT`,
#: no cache, and none of this file's behaviour.
QUERY = "認証"

#: How many chunks contain :data:`QUERY`. Exactly :data:`FIRST_PASS_DEPTH`,
#: because that is the one match count at which `_visible_ranking` cannot tell a
#: complete ranking from a truncated one and asks a second time.
MATCHING_CHUNKS = FIRST_PASS_DEPTH

#: Chunks that contain no term of the query. Present so that "how many rows
#: match" is a different number from "how many rows the corpus has": the
#: precondition below asserts the match count, and it would be satisfied by
#: accident if the two were the same.
UNMATCHED_CHUNKS = 20

#: How many of the matching chunks the canonical store has withdrawn since the
#: build. One past the edge — at :data:`CANDIDATE_DEPTH` withheld rows exactly
#: fifty remain visible, the loop is satisfied, and there is no second call for a
#: cache to answer.
WITHDRAWN_CHUNKS = CANDIDATE_DEPTH + 1

#: The item-id prefix a withdrawn document carries here.
WITHDRAWN = "withdrawn"

#: The alias `index_scan.scan_statement` gives its ordering key, and the one
#: fragment that identifies a scan in a trace of executed SQL. Nothing else this
#: module sends to SQLite carries it — not the trigram lookup, not `chunk_texts`,
#: not the PRAGMAs.
SCAN_MARKER = "AS matched_characters"


def _indexable(chunk_id: str, text: str, *, item: str) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=""),
        project_id=PROJECT,
        item_id=item,
        revision_id=f"rev-{chunk_id}",
        status="approved",
        sensitivity="internal",
        trust_level="reviewed",
    )


@pytest.fixture
def stale_index(tmp_path: Path) -> Path:
    """An index whose top :data:`WITHDRAWN_CHUNKS` matches are no longer current.

    Withdrawn rows rank first because that is the shape the depth loop exists
    for: a document retracted after the index was built still ranks where the
    index put it, and a query written to match it ranks it high. Every matching
    chunk carries the term once, so `matched_characters` ties across all of them
    and `chunk_id` decides the order — which makes the ranking, and therefore
    every count below, reproducible (FR-R7).
    """
    path = tmp_path / "index" / "theurian-index-01.sqlite"
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1DXAA", state_hash="abc123")
    store.add_chunks(
        [
            _indexable(
                f"hit-{number:03d}",
                f"認証は署名付きトークンで行う。段落{number}。",
                item=f"{WITHDRAWN if number < WITHDRAWN_CHUNKS else 'current'}-{number:03d}",
            )
            for number in range(MATCHING_CHUNKS)
        ]
        + [
            _indexable(
                f"miss-{number:03d}",
                f"決済は監査ログに記録する。段落{number}。",
                item=f"unrelated-{number:03d}",
            )
            for number in range(UNMATCHED_CHUNKS)
        ]
    )
    return path


@final
class _WithoutTheWithdrawn:
    """The canonical store's answer once those documents have been withdrawn.

    A stub rather than a :class:`~theurian.application.visibility.CanonicalVisibility`
    over a real state database: what is under test is how often the *index* is
    read, and standing up a second store to decide which rows are visible would
    add reads this file would then have to filter back out of its own trace.
    """

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(row for row in ranked if not row.item_id.startswith(WITHDRAWN))


@final
class _CountedStore:
    """One real :class:`SqliteIndexStore`, with its substring reads recorded.

    A delegate around the real store, never a fake in place of it. The cache
    under test lives inside `SqliteIndexStore`, so a fake standing in for it
    would be measuring itself — and the count kept here is the *port* count,
    which is precisely the number that does not move when the cache is deleted.
    It is recorded to prove the second call happened, not to detect the cache.
    """

    def __init__(self, inner: SqliteIndexStore) -> None:
        self._inner = inner
        #: The ``limit`` each substring read asked for, in order.
        self.substring_reads: list[int] = []

    def search_substring(
        self, query: str, *, project_id: str, limit: int, include_unapproved: bool
    ) -> tuple[Ranked, ...]:
        self.substring_reads.append(limit)
        return self._inner.search_substring(
            query, project_id=project_id, limit=limit, include_unapproved=include_unapproved
        )

    def search_lexical(
        self, query: str, *, project_id: str, limit: int, include_unapproved: bool
    ) -> tuple[Ranked, ...]:
        return self._inner.search_lexical(
            query, project_id=project_id, limit=limit, include_unapproved=include_unapproved
        )

    def search_dense(
        self, query_vector: Sequence[float], *, project_id: str, include_unapproved: bool
    ) -> tuple[Ranked, ...]:
        return self._inner.search_dense(
            query_vector, project_id=project_id, include_unapproved=include_unapproved
        )

    def chunk_texts(self, chunk_ids: Sequence[str], *, project_id: str) -> Mapping[str, str]:
        return self._inner.chunk_texts(chunk_ids, project_id=project_id)

    def metadata(self) -> Mapping[str, object]:
        return self._inner.metadata()

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        raise NotImplementedError

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        raise NotImplementedError

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        raise NotImplementedError


@contextmanager
def _statements_reaching_sqlite() -> Iterator[list[str]]:
    """Every statement the index store hands to SQLite, as SQLite receives it.

    A trace callback on the real connection, not a wrapper around
    ``search_substring``. SQLite invokes it when a prepared statement begins
    running, so what accumulates is *executions* — the quantity a cache removes —
    rather than calls to a Python method, which it does not.

    Installed by replacing the module's ``_connect``, because that is the single
    place every read in this adapter opens a connection; the PRAGMAs it runs
    before yielding are deliberately outside the trace.
    """
    executed: list[str] = []
    real_connect = index_store_module._connect

    @contextmanager
    def _traced(path: Path) -> Iterator[sqlite3.Connection]:
        with real_connect(path) as connection:
            connection.set_trace_callback(executed.append)
            yield connection

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(index_store_module, "_connect", _traced)
        yield executed


def test_one_search_scans_the_corpus_once_however_many_rows_were_withheld(
    stale_index: Path,
) -> None:
    """T-17. The second read of the scan retriever must cost nothing to answer.

    ``_visible_ranking`` reads this retriever twice here, and it does so *because*
    fifty-one rows were withheld — one fewer and it would read once. So the number
    of reads already carries a bit about what the caller may not see. What must
    not also move is the *work*: if the second read runs the scan again, a search
    that crossed the edge takes roughly twice as long as one that did not, and the
    step is measurable from outside with no access to the response at all.

    The cache is what makes the second read free, and it is the whole of the
    mitigation — there is no other guard. Deleting ``SqliteIndexStore._scan_cache``
    leaves the ranking, the results, the counts and the port call count all
    identical; only this assertion moves, from one scan to two.

    **Removed with the cache, not preserved alongside it.** Issue #16 replaces the
    row-count inference with an explicit exhaustion signal on ``IndexStore``, at
    which point the second read never happens and the cache is dead code. This
    test dies there too; it is not a reason to keep the cache.
    """
    matches = SqliteIndexStore(stale_index).search_substring(
        QUERY, project_id=PROJECT, limit=FIRST_PASS_DEPTH, include_unapproved=False
    )
    assert len(matches) == FIRST_PASS_DEPTH, (
        f"the corpus must match exactly {FIRST_PASS_DEPTH} rows for the loop to be unable "
        f"to tell a complete ranking from a truncated one; it matched {len(matches)}, so "
        f"there is no second read here and this test is exercising nothing"
    )
    visible = _WithoutTheWithdrawn()
    assert len(visible.cleared(matches)) < CANDIDATE_DEPTH, (
        f"fewer than {CANDIDATE_DEPTH} matches may be shown, or the first read satisfies "
        f"the loop and the branch under test is never reached"
    )
    store = _CountedStore(SqliteIndexStore(stale_index))

    with _statements_reaching_sqlite() as executed:
        RetrievalService(store).search(SearchRequest(query=QUERY, project_id=PROJECT), visible)

    assert store.substring_reads == [FIRST_PASS_DEPTH, FIRST_PASS_DEPTH * 2], (
        f"the two-call branch must be what ran, or the scan count below means nothing; "
        f"the retriever was asked at depths {store.substring_reads}"
    )
    scans = [statement for statement in executed if SCAN_MARKER in statement]
    assert len(scans) == 1, (
        f"one search must cost one pass over the corpus; SQLite ran {len(scans)}. "
        f"Two means the memoisation in `SqliteIndexStore._scan_cache` is gone and the "
        f"cost of a search now moves with how many rows the canonical store withheld "
        f"(T-17). Zero means no pass happened inside the measured window at all, which "
        f"has two causes worth telling apart: the memo has crossed instances and answered "
        f"out of the precondition read above — see "
        f"`test_one_callers_withheld_rows_never_make_another_callers_search_cheaper`, "
        f"whose subject that is — or {SCAN_MARKER!r} no longer identifies the scan "
        f"statement, in which case this test has stopped measuring anything and what "
        f"needs fixing is the marker rather than the number."
    )


#: How many searches the test below runs. Two, because the claim is about what a
#: *second* caller pays for a query a first caller has already asked.
REQUESTS = 2


def test_one_callers_withheld_rows_never_make_another_callers_search_cheaper(
    stale_index: Path,
) -> None:
    """SEC-13, T-17. The mitigation must not become the same channel one level up.

    What the cache holds is not a fact about the corpus. It is the answer to one
    query *as one stale index gave it*, and the reason the caller asked twice was
    that rows had been withheld. So a store that outlived its request would hand
    the next caller asking the same question a search that costs nothing — and
    latency would then report that somebody had asked before, and through which
    pass was skipped, something about what that earlier caller could not be shown.
    That is the withheld-row count reaching a stranger's stopwatch: precisely the
    observable the cache exists to close, reopened at the level of the process
    rather than the read.

    ``SqliteIndexStore.__init__`` states this as a requirement on callers — a
    fresh store per search, which ``mcp.search.hybrid_answer`` satisfies by
    building one per request — and until this test existed the requirement was a
    paragraph of prose with nothing behind it.

    **What the rest of the suite sees when the scope widens, measured in two
    steps, because the first step misleads.** Promote ``_scan_cache`` to a class
    attribute and thirteen tests elsewhere do fail — five in
    ``test_index_store.py``, eight in ``test_short_query_retrieval.py`` — but not
    for this reason, and not in a way that survives being looked at. The memo's
    key is ``(query, project_id, include_unapproved)`` with no index path in it,
    because an instance already *is* one file; share the dict and that key stops
    identifying an answer, so one test's index answers another test's query.
    Those failures read as test pollution, and the natural repair is to put the
    path back in the *key* rather than the cache back in the *instance*. Do
    exactly that — process-wide dict, path added to the key — and the suite goes
    silent: 1,284 passed, nothing failed, matching the unmutated baseline
    exactly, while a store now outlives every request the daemon serves. That is
    the configuration this test exists for, and it is the one a maintainer
    arrives at by fixing the visible symptom.

    Two things follow, and the second is not about timing. The per-instance scope
    is what holds the channel shut, *and* it is what makes the key correct — a
    process-wide memo without the path serves a second build of the same project,
    or the same project under another ``THEURIAN_DATA_DIR``, rows from an index
    it never read. So "construct a fresh store per search" is load-bearing for
    what a search returns as well as for what it costs.

    So the property asserted is the cost of a *request*: one pass over the corpus
    each time it is asked, not one pass per process. Two requests, one query, two
    scans — and the pair with the test above says the number is one per request,
    neither more (the cache does its job) nor fewer (it does not do it twice).

    **Deleted with the cache, like its neighbour, and this one will not say so
    itself.** Once issue #16 removes the cache, two requests still cost two scans
    and this test still passes while there is nothing left for it to be about. It
    has to be taken out deliberately rather than waited on to fail.
    """
    visible = _WithoutTheWithdrawn()
    request = SearchRequest(query=QUERY, project_id=PROJECT)

    with _statements_reaching_sqlite() as executed:
        # A store per search, as `hybrid_answer` builds one per request. Sharing
        # the instance here is the very thing the store's docstring forbids.
        outcomes = [
            RetrievalService(SqliteIndexStore(stale_index)).search(request, visible)
            for _ in range(REQUESTS)
        ]

    assert all(outcome.candidates for outcome in outcomes), (
        "both requests must have been answered from the scan; counting empty passes "
        "would satisfy the assertion below without the corpus ever being read"
    )
    scans = [statement for statement in executed if SCAN_MARKER in statement]
    assert len(scans) == REQUESTS, (
        f"each request must pay for its own pass over the corpus; {REQUESTS} requests ran "
        f"{len(scans)}. Fewer means the memo in `SqliteIndexStore._scan_cache` has outlived "
        f"the request that filled it, so one caller's search is now faster because another "
        f"caller's rows were withheld — the observable this cache exists to close, moved "
        f"from between two reads to between two callers (SEC-13, T-17). More means the "
        f"cache is gone, which is the neighbouring test's business."
    )
