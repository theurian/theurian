"""A search costs one pass over the corpus, and one call, whatever was withheld.

**This file replaces ``test_scan_cache.py``**, which held the same property by a
different means and said so: ``SqliteIndexStore._scan_cache`` was a security
mitigation rather than an optimisation, and its own docstring instructed its
deletion once ``IndexStore`` stated its own exhaustion (issue #16). It has, so
the memo is gone — and the property it was holding is not, which is why this file
exists rather than the two tests simply going with it.

**What changed underneath.**
:meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
used to infer exhaustion from a row count. The scan below the trigram floor has
no ``LIMIT`` — it must score every matching row before it can name the best of
them — so it answers with its entire ranking every time, and the ambiguous case
was reached exactly when that ranking totalled :data:`FIRST_PASS_DEPTH` rows and
fewer than :data:`CANDIDATE_DEPTH` of them survived the canonical store:

======================= ================== ===============
Rows the store withheld Port calls, before Port calls, now
======================= ================== ===============
0 to 50                 1                  1
51 to 99                2                  1
======================= ================== ===============

A step function of the withheld count with its edge at 51 of 100, flattened.
The cache could not flatten it — it made the second call cheap, it did not
prevent it — so what it bought was that the *cost* stopped moving while the call
count still did. An explicit exhaustion signal removes the call.

**Two counts, and this file asserts both**, because they fail in different
directions and neither implies the other. The port call count is what the
exhaustion signal fixes; it was 1 or 2 before and is 1 now. The count of scan
statements *SQLite actually ran* is what a reader with a stopwatch pays for, and
it is the number the cache used to hold at 1 while the call count moved. Assert
only the first and a retriever that answered twice from a memo would pass;
assert only the second and a retriever that ran one scan per call would look
identical to one that ran none.

**Why the count of executions is taken from a trace callback.** Counting calls to
``search_substring`` cannot see how many times SQLite ran anything. The trace is
installed by replacing ``index_store._connect``, so what it counts is statements
executed on connections opened *there*. Every read in this adapter opens its
connection through ``_connect`` as things stand, which is what makes the count
complete — a property of this module today, not of SQLite, and therefore a
precondition of the measurement rather than a guarantee of it. A retriever that
opened its own connection would be invisible here, and this file would go on
passing while measuring less than it claims.

**What went with the cache and is not replaced.**
``test_one_callers_withheld_rows_never_make_another_callers_search_cheaper``
asserted that a memo never outlived the request that filled it. There is no memo,
and that test would have gone on passing while guarding nothing — the shape its
own docstring warned about. What replaced it is smaller and structural:
``SqliteIndexStore.__init__`` now assigns one field, so there is nothing left for
one caller's query to leave behind for another's, and
``test_the_store_holds_no_state_between_searches`` below reads that off the
instance rather than off a stopwatch.
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
from theurian.domain.ranking import Ranked, RetrieverPage
from theurian.infrastructure.sqlite import index_store as index_store_module
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT = "demo"

#: Two characters, so it is below the trigram floor and `search_substring` falls
#: through to the scan. Every other substring query in the suite is three
#: characters or more and is answered by the trigram lookup, which has a `LIMIT`
#: and resolves its own exhaustion by fetching one row past it.
QUERY = "認証"

#: How many chunks contain :data:`QUERY`. Exactly :data:`FIRST_PASS_DEPTH`,
#: because that is the one match count at which a row count could not tell a
#: complete ranking from a truncated one. It is the configuration that used to
#: force the second call, kept precisely so that the second call's absence is
#: measured where it used to be present rather than somewhere it never was.
MATCHING_CHUNKS = FIRST_PASS_DEPTH

#: Chunks that contain no term of the query. Present so that "how many rows
#: match" is a different number from "how many rows the corpus has": the
#: precondition below asserts the match count, and it would be satisfied by
#: accident if the two were the same.
UNMATCHED_CHUNKS = 20

#: How many of the matching chunks the canonical store has withdrawn since the
#: build, per parametrisation. `CANDIDATE_DEPTH` is the old edge: at exactly
#: fifty withheld, fifty remain visible and the loop was satisfied on the first
#: call; at fifty-one it was not, and asked again. Both sides are exercised, and
#: so is a corpus with nothing withheld at all, because the claim is that the
#: counts no longer distinguish them.
WITHDRAWN_COUNTS = (0, CANDIDATE_DEPTH, CANDIDATE_DEPTH + 1, MATCHING_CHUNKS - 1)

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


def _stale_index(tmp_path: Path, withdrawn: int) -> Path:
    """An index whose top ``withdrawn`` matches are no longer current.

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
                item=f"{WITHDRAWN if number < withdrawn else 'current'}-{number:03d}",
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

    A delegate around the real store, never a fake in place of it. What is under
    test is the real adapter's answer to "have you anything further", so a fake
    standing in for it would be measuring itself.
    """

    def __init__(self, inner: SqliteIndexStore) -> None:
        self._inner = inner
        #: The ``limit`` each substring read asked for, in order.
        self.substring_reads: list[int] = []

    def search_substring(
        self, query: str, *, project_id: str, limit: int, include_unapproved: bool
    ) -> RetrieverPage:
        self.substring_reads.append(limit)
        return self._inner.search_substring(
            query, project_id=project_id, limit=limit, include_unapproved=include_unapproved
        )

    def search_lexical(
        self, query: str, *, project_id: str, limit: int, include_unapproved: bool
    ) -> RetrieverPage:
        return self._inner.search_lexical(
            query, project_id=project_id, limit=limit, include_unapproved=include_unapproved
        )

    def search_dense(
        self, query_vector: Sequence[float], *, project_id: str, include_unapproved: bool
    ) -> RetrieverPage:
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


@pytest.mark.parametrize("withdrawn", WITHDRAWN_COUNTS)
def test_one_search_reads_the_scan_once_however_many_rows_were_withheld(
    tmp_path: Path, withdrawn: int
) -> None:
    """T-17, SEC-13. Neither the call count nor the scan count may move with it.

    The corpus is held at exactly :data:`FIRST_PASS_DEPTH` matches — the one match
    count a row count could not interpret — and only the withheld count varies
    across the parametrisation. Before ``IndexStore`` stated its own exhaustion,
    ``withdrawn=51`` cost two port calls and ``withdrawn=50`` cost one, from
    outside, with no access to the response.

    Both numbers are asserted because they fail differently. A regression in the
    exhaustion signal moves the first; a memo reintroduced to paper over such a
    regression would hold the second still while the first moved, which is
    exactly the configuration this file replaced.
    """
    index = _stale_index(tmp_path, withdrawn)
    matches = SqliteIndexStore(index).search_substring(
        QUERY, project_id=PROJECT, limit=FIRST_PASS_DEPTH, include_unapproved=False
    )
    assert len(matches.rows) == FIRST_PASS_DEPTH, (
        f"the corpus must match exactly {FIRST_PASS_DEPTH} rows, which is the count at which "
        f"a row count could not tell a complete ranking from a truncated one; it matched "
        f"{len(matches.rows)}, so this parametrisation is exercising nothing"
    )
    assert matches.exhausted, (
        "the scan below the trigram floor must report itself exhausted on its first call; "
        "without that the loop asks again and the rest of this test measures the old shape"
    )
    visible = _WithoutTheWithdrawn()
    store = _CountedStore(SqliteIndexStore(index))

    with _statements_reaching_sqlite() as executed:
        RetrievalService(store).search(SearchRequest(query=QUERY, project_id=PROJECT), visible)

    assert store.substring_reads == [FIRST_PASS_DEPTH], (
        f"one search must read the scan retriever once whatever was withheld; with "
        f"{withdrawn} withheld it was asked at depths {store.substring_reads}. More than one "
        f"means `search_substring` is no longer reporting the scan branch exhausted, so the "
        f"number of round-trips a request makes has started moving with how many rows the "
        f"caller may not see (T-17)."
    )
    scans = [statement for statement in executed if SCAN_MARKER in statement]
    assert len(scans) == 1, (
        f"one search must cost one pass over the corpus; SQLite ran {len(scans)} with "
        f"{withdrawn} withheld. Zero means {SCAN_MARKER!r} no longer identifies the scan "
        f"statement, in which case this test has stopped measuring anything and what needs "
        f"fixing is the marker rather than the number."
    )


def test_the_store_holds_no_state_between_searches(tmp_path: Path) -> None:
    """SEC-13. What replaced the fresh-instance-per-search rule, read off the object.

    ``_scan_cache`` required callers to construct a fresh ``SqliteIndexStore``
    per search: a store that outlived its request would have handed the next
    caller asking the same question a search that cost nothing, and latency would
    then report that somebody had asked before — the withheld-row count reaching
    a stranger's stopwatch. That requirement was a paragraph of prose, and the
    test that stood behind it measured scans per request.

    It is now a property of the object instead, and this is the assertion that
    holds it: an instance carries a path and nothing else, so there is no
    per-instance state for one caller's query to leave behind. Adding a field
    here is not forbidden — it is a decision that has to be taken deliberately,
    against this test, rather than arrived at while making something faster.
    """
    store = SqliteIndexStore(tmp_path / "theurian-index-01.sqlite")

    assert vars(store) == {"_path": store.path}, (
        f"`SqliteIndexStore` must carry no state across searches; it holds "
        f"{sorted(vars(store))}. Anything else is per-request state on an object the "
        f"composition root may one day pool, which is how one caller's answer becomes "
        f"another caller's latency (SEC-13, T-17)."
    )
