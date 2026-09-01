"""What a purged build stops *spending*, not what it stops returning (T-17, ADR-0024).

`test_index_purge.py`, `test_absence_proof.py` and `test_forest_purge_equality.py`
already pin that a purged build **answers** as if the withdrawn rows had never
been indexed. None of them pins a *quantity*. That gap is what
`docs/work-logs/2026-09-01-472-purged-build-re-measurement.md` found and did not
close: T-17's round-five, round-six and round-seven figures were all taken
against a build that still held the withdrawn rows, and every one of them is a
count -- canonical reads, retriever passes, peak memory -- that moves with **how
many rows were withheld**, which is the one quantity SEC-13 arranges the response
not to state.

The re-measurement's finding has one shape everywhere: *the purge does not make
these quantities smaller, it removes the term they are functions of.* On a purged
build `|ranking|` is the visible count at every withheld level, so there is
nothing left for a per-withheld-row rate to multiply. This file is that finding
turned into pins, so that a change putting the withheld term back goes RED here
rather than being rediscovered by a fourth review round.

**Every test carries a stale positive control, and the control is what makes the
flatness mean anything.** "The purged column is flat" is satisfied by a harness
that measures nothing -- a `fetch` that never runs, a corpus with no withheld
rows in it, a gate that is never asked. So each test asserts the *stale* build's
counts against exactly derived values that grow with the withheld count, and the
stale assertion is written first. If the control stops moving, the test fails
before it reaches the claim. This is `test_index_purge.py`'s own discipline
applied to costs instead of to rankings.

**The corpus is built the only way a published index ever comes to hold a
withdrawn row.** `theurian index build` filters on `may_surface`, so a withdrawn
document can only have arrived *before* its status moved: every document is
written `approved`, the index is built over all of them through the real
`IndexBuilder`, and only then are the withheld items moved to `deprecated` --
which `may_surface` refuses under every flag, so no `includeUnapproved` reaches
them. The index is untouched by that move, and **that state is what every
round-5/6/7 figure was taken on**. `derive_purged` then produces the second build
through the same library call `withdrawal_purge.publish_purge_for_withdrawal`
uses.

**Scale.** The work log swept to 5,950 withheld rows; the flat-versus-moving
shape is fully formed at 0/50/200, and this file stops there so it costs seconds
rather than minutes. Where a shape depends on a specific boundary -- the
`FIRST_PASS_DEPTH` edge -- the counts straddle it and a premise asserts that they
still do.

**The RED paths, run rather than argued** (2026-09-02, `tools/mutate.py
--prepare-tree`, one isolated copy per mutation, nothing applied to a live
checkout). Both halves of every test were taken RED, because a claim and its
control fail for different reasons and only one of them was ever in doubt:

- **The purge reports its own count and keeps the rows** -- `DELETE FROM chunks
  ... AND 0`, with `_verify`'s post-conditions emptied so the build is still
  handed back. All three **claims** RED: reads 10 / 60 / 210 against 10 / 10 /
  10, two passes and 200 reads at 51 withheld against one and 100, peaks 86.0 /
  160.3 / 363.3 KB against a single value.
- **The scan below the trigram floor gains a `LIMIT 50`.** The **controls** of
  the read-count and the peak-memory tests RED: the stale ranking stops growing
  at 50, and the stale peaks go flat at 86.1 / 85.6 / 85.6 KB.
- **`FIRST_PASS_DEPTH = CANDIDATE_DEPTH * 2` becomes `CANDIDATE_DEPTH + 1`.** The
  **control** of the pass-count test RED: the edge leaves 50 and the stale build
  steps at every level.

The first is the regression these pins exist for and the shape the review rounds
kept meeting: a purge that *says* it removed the rows. The second and third are
what say the controls are not decoration -- each takes a control RED while the
purged column stays green, so neither assertion is holding the other up.
"""

from __future__ import annotations

import itertools
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import ProjectPaths
from theurian.application.retrieval_service import (
    CANDIDATE_DEPTH,
    FIRST_PASS_DEPTH,
    RetrievalService,
    _deeper,
)
from theurian.application.visibility import CanonicalVisibility
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.ranking import RetrieverPage
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

#: The disclosure grant every call in this file runs under: all four levels,
#: spelled out rather than read from ``StaticAuthorizationProvider``'s shipped
#: default, which a later phase narrows. A file that inherited it would start
#: withholding its own fixtures silently (`test_index_purge.py`'s note).
EVERY_SENSITIVITY: Final = frozenset(Sensitivity)

PROJECT: Final = "purge-quantities"

#: Fixed, because nothing here reads a clock and no assertion may depend on one.
CREATED: Final = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

MIGRATION: Final = MigrationId("01K1MGAAAA01234567890ABCDE")
STALE_BUILD_ID: Final = "01K1SDAAAA01234567890ABCDE"
PURGED_BUILD_ID: Final = "01K1PGAAAA01234567890ABCDE"
STATE_HASH: Final = "a" * 64

#: One body for every document, withheld and visible alike. **Identical, and
#: that is a fixture decision with two halves.**
#:
#: For the `FIRST_PASS_DEPTH` edge it is required: `search_lexical` orders by
#: ``rank_score, chunks.chunk_id``, so same-length bodies carrying each term the
#: same number of times make every `bm25` score tie and the chunk id break it --
#: which is what puts the withheld rows deterministically at the *top* of the
#: ranking, where they displace visible ones and drive the pass count. A corpus
#: with varied bodies would put them wherever the arithmetic landed, and the edge
#: would be reproduced by luck.
#:
#: For the ranking-*equality* tests it would be the wrong fixture -- a withheld
#: document of average length moves `avgdl` least, so same-length bodies quietly
#: stop exercising the channel T-17a is about. Those tests live in
#: `test_index_purge.py`, which builds withdrawn bodies ten times the corpus mean
#: for exactly that reason. Nothing here asserts a score or an order, only a
#: count, so the two fixture disciplines do not conflict; they answer different
#: questions.
#:
#: Shorter than `chunking.TARGET_CHARS`, so every document is exactly one chunk
#: and the distinct-item count of a ranking equals its row count. The read-count
#: arithmetic below rests on that.
BODY: Final = (
    "Retention and isolation are decided per namespace. The quarantine ledger "
    "records every attempt made against the boundary here."
)

#: Two characters, so `to_trigram_expression` yields nothing and
#: `search_substring` falls through to `_scan_below_the_trigram_floor` -- the one
#: shipped retriever whose statement carries no `LIMIT`, and therefore the real
#: analogue of round six and seven's "retriever that never truncates". On that
#: branch `page.rows` is the entire match set, so the canonical read count is the
#: visible count *plus the withheld count* on a stale build: the residual T-17
#: names, measured directly rather than argued.
#:
#: Present in :data:`BODY` (`Retention`, `records`, `every`), so every document
#: matches and the match set is the whole corpus.
SCAN_QUERY: Final = "re"

#: Long enough to form trigrams, so `search_lexical`'s word index answers it and
#: `limit` is a true ceiling -- the branch that truncates, which is where a pass
#: count can move at all.
LEXICAL_QUERY: Final = "quarantine ledger"

#: Round six's own shape (work log §F2/F1'): ten visible rows, withheld sweeping
#: up. Ten rather than fifty because the record's published table is ten, and the
#: purged column is what this file compares against it.
VISIBLE_READS: Final = 10

#: Round seven's own shape (work log §F3/F4): fifty visible rows.
VISIBLE_MEMORY: Final = 50

#: Enough visible rows that a stale build forced to a second pass still fills the
#: doubled ask: at 51 withheld the second pass asks for
#: ``_deeper(FIRST_PASS_DEPTH)`` rows and must receive exactly that many, or the
#: read count it reports would be the corpus size rather than the depth.
VISIBLE_EDGE: Final = 200

#: The withheld counts the flat-versus-moving shape is asserted over. Zero is the
#: identity case -- a purge that removed nothing -- and it is what makes "flat"
#: an equality against a *known* baseline rather than against whatever the first
#: measured level happened to be.
WITHHELD_LEVELS: Final = (0, 50, 200)

#: Straddling `CANDIDATE_DEPTH`. Asserted to still straddle it before the edge
#: test runs, so a change to the constant fails naming the cause instead of
#: quietly measuring four points on the same side of the edge.
EDGE_LEVELS: Final = (49, 50, 51, 52)

#: How many times a peak is sampled before the smallest is taken. `tracemalloc`
#: traces Python allocations only, so a repeated call over identical rows
#: allocates identically -- but the *first* call through a path also pays for
#: whatever it warms (a compiled pattern, an interned string), and that one-off
#: lands on whichever build is measured first. A discarded warm-up plus the
#: minimum of the rest removes it without inventing a tolerance.
#:
#: Measured 2026-09-02 on this fixture, nine repeats per cell: every purged
#: sample was byte-identical, at 0, 50 and 200 withheld and on both the composite
#: and the retriever-only probe. Five is that with margin.
PEAK_REPEATS: Final = 5


@dataclass(frozen=True, slots=True)
class Document:
    """One knowledge item, as this module writes it."""

    item_id: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class Corpus:
    """One withheld count, measured on two builds and one canonical store.

    Both builds are read against the **same** state database, which is what makes
    the pair a comparison: the only difference between a `stale` measurement and
    a `purged` one is whether the index file still holds the withdrawn rows.
    """

    withheld: int
    visible: int
    database: Path
    stale: SqliteIndexStore
    purged: SqliteIndexStore


@dataclass(frozen=True, slots=True)
class Measured:
    """What one `_visible_ranking` call cost, in the units T-17 is about."""

    #: How many times the retriever was asked -- the depth-doubling loop's own
    #: round-trip count, observable as latency.
    passes: int
    #: ``len(page.rows)`` per pass, in order.
    rows_per_pass: tuple[int, ...]
    #: ``CanonicalReadSession.get_item`` calls. The *distinct item* count of the
    #: ranking, not ``len(ranked)``: `CanonicalVisibility` memoises per request,
    #: and this is the number a canonical store can observe.
    canonical_reads: int
    #: How many rows the caller was left with.
    returned: int

    @property
    def ranking_size(self) -> int:
        """``|ranking|`` -- the rows the last pass handed to the gate."""
        return self.rows_per_pass[-1]


class CountingReadSession:
    """A real `SqliteCanonicalStore` session with a tally on `get_item`.

    A decorator rather than a fake, so every answer the gate acts on comes from a
    real SQLite read of a real state database. The only thing added is the
    counter, and the counter is the instrument: T-17's residual is *how many
    times the canonical store was asked*, which no published field reports and
    no return value carries.
    """

    def __init__(self, inner: SqliteCanonicalStore) -> None:
        self._inner = inner
        self.get_item_calls = 0

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        return self._inner.list_items(context)

    def get_item(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        self.get_item_calls += 1
        return self._inner.get_item(context, item_id)

    def get_item_exact(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        return self._inner.get_item_exact(context, item_id)

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None:
        return self._inner.get_revision(context, revision_id)

    def __enter__(self) -> CountingReadSession:
        self._inner.__enter__()
        return self

    def __exit__(self, *details: object) -> None:
        self._inner.__exit__(*details)


def _revision_id(prefix: str, ordinal: int) -> str:
    """A ULID whose *prefix* decides where its chunk sorts.

    `search_lexical` and the trigram lookup both break `bm25` ties on
    ``chunks.chunk_id``, and a chunk id is ``f"{revision_id}#{ordinal}"``. Giving
    the withheld documents the earlier prefix is therefore what puts them at the
    top of a tied ranking -- deterministically, rather than by whatever order the
    rows happened to be inserted in.

    Crockford base32 has no I, L, O or U; ``A``/``B`` and the digits are all
    inside it.
    """
    return f"01K1{prefix}{ordinal:021d}"


def _documents(visible: int, withheld: int) -> tuple[tuple[Document, ...], tuple[Document, ...]]:
    """The withheld documents and the visible ones, withheld sorting first."""
    return (
        tuple(
            Document(f"architecture.gone-{n:04d}", _revision_id("A", n)) for n in range(withheld)
        ),
        tuple(Document(f"architecture.keep-{n:04d}", _revision_id("B", n)) for n in range(visible)),
    )


def _revision(document: Document) -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=RevisionId(document.revision_id),
        item_id=ItemId(document.item_id),
        project_id=ProjectId(PROJECT),
        migration_id=MIGRATION,
        title="Boundary policy",
        body=BODY,
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=CREATED),
        author="engineer@example.com",
        created_at=CREATED,
        source_anchors=(
            SourceAnchor(provider="git", source_uri=f"git://demo/{document.item_id}.md"),
        ),
    )


def _item(document: Document, status: KnowledgeStatus) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=ItemId(document.item_id),
        project_id=ProjectId(PROJECT),
        namespace="backend",
        kind=KnowledgeKind.ARCHITECTURE,
        status=status,
        current_revision_id=RevisionId(document.revision_id),
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=CREATED),
    )


def _build_corpus(root: Path, *, visible: int, withheld: int) -> Corpus:
    """A real state database, a stale build, and the purge derived from it.

    The five steps of the work log's §"The ground truth", in order. Step 3 is the
    one that cannot be reordered: the index must be built while every document is
    still `approved`, because `IndexBuilder` filters on `may_surface` and would
    otherwise never write the withheld rows at all -- which is precisely the
    harness defect that would leave every "flat" column below flat for the wrong
    reason.
    """
    paths = ProjectPaths.of(root)
    paths.state.mkdir(parents=True, exist_ok=True)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    database = paths.state / "state.sqlite3"
    create_database(database, state_hash=STATE_HASH, engine_version=1)

    withheld_documents, visible_documents = _documents(visible, withheld)

    with write_transaction(database, paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(
            Project(
                project_id=ProjectId(PROJECT),
                root_path=str(root),
                repository_url=None,
                default_branch="main",
                knowledge_directory=PurePosixPath(".theurian"),
                registered_at=CREATED,
            )
        )
        for document in (*withheld_documents, *visible_documents):
            writer.append_revision(_revision(document))
            writer.put_item(_item(document, KnowledgeStatus.APPROVED))

    stale_path = root / "theurian-index-stale.sqlite"
    IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        # No embedder: nothing here reads a dense ranking, and a vector per
        # document would be corpus-sized work for a quantity no test asserts.
        embedder=None,
    ).build(
        IndexRequest(
            database=database,
            index_path=stale_path,
            project_id=PROJECT,
            state_hash=STATE_HASH,
            index_build_id=STALE_BUILD_ID,
            visible_sensitivities=EVERY_SENSITIVITY,
            include_unapproved=False,
        )
    )

    # The withdrawal the index never saw. `deprecated` rather than `draft`
    # because `may_surface` refuses it under *every* flag, so a caller cannot
    # reach these rows by passing `includeUnapproved` -- which is what makes them
    # withheld rather than merely off by default.
    with write_transaction(database, paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        for document in withheld_documents:
            writer.put_item(_item(document, KnowledgeStatus.DEPRECATED))

    stale = SqliteIndexStore(stale_path)
    purged_path = root / "theurian-index-purged.sqlite"
    removed = stale.derive_purged(
        purged_path,
        revision_ids=[document.revision_id for document in withheld_documents],
        index_build_id=PURGED_BUILD_ID,
        state_hash=STATE_HASH,
    )
    assert removed == withheld, (
        f"the purge removed {removed} rows where {withheld} were withdrawn, so the "
        f"two builds below do not differ by what this file says they differ by"
    )
    return Corpus(
        withheld=withheld,
        visible=visible,
        database=database,
        stale=stale,
        purged=SqliteIndexStore(purged_path),
    )


def _corpora(
    factory: pytest.TempPathFactory, name: str, *, visible: int, levels: tuple[int, ...]
) -> dict[int, Corpus]:
    return {
        withheld: _build_corpus(
            factory.mktemp(f"{name}-{withheld}"), visible=visible, withheld=withheld
        )
        for withheld in levels
    }


def _measure(corpus: Corpus, index: SqliteIndexStore, *, query: str, lexical: bool) -> Measured:
    """Drive `_visible_ranking` over one build and count what it spent.

    The gate is a real `CanonicalVisibility` over a real `SqliteCanonicalStore`
    session, so `get_item` is an actual SQLite read and the memoisation that
    turns ``len(ranked)`` into a *distinct item* count is the shipped one.
    """
    rows_per_pass: list[int] = []

    def fetch(depth: int) -> RetrieverPage:
        search = index.search_lexical if lexical else index.search_substring
        page = search(
            query,
            project_id=PROJECT,
            limit=depth,
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        )
        rows_per_pass.append(len(page.rows))
        return page

    with SqliteCanonicalStore(corpus.database) as inner:
        session = CountingReadSession(inner)
        visible = CanonicalVisibility(
            session,
            RequestContext(project_id=ProjectId(PROJECT)),
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        )
        ranked = RetrievalService._visible_ranking(fetch, visible)
        reads = session.get_item_calls

    return Measured(
        passes=len(rows_per_pass),
        rows_per_pass=tuple(rows_per_pass),
        canonical_reads=reads,
        returned=len(ranked),
    )


def _peak_bytes(corpus: Corpus, index: SqliteIndexStore, *, query: str) -> int:
    """The smallest traced peak of :data:`PEAK_REPEATS` runs, after a warm-up."""
    _measure(corpus, index, query=query, lexical=False)

    samples: list[int] = []
    for _ in range(PEAK_REPEATS):
        tracemalloc.start()
        try:
            _measure(corpus, index, query=query, lexical=False)
            samples.append(tracemalloc.get_traced_memory()[1])
        finally:
            tracemalloc.stop()
    return min(samples)


@pytest.fixture(scope="module")
def read_count_corpora(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Corpus]:
    """Round six's shape: ten visible rows, withheld sweeping 0 -> 200."""
    return _corpora(tmp_path_factory, "reads", visible=VISIBLE_READS, levels=WITHHELD_LEVELS)


@pytest.fixture(scope="module")
def edge_corpora(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Corpus]:
    """Round five's shape: withheld counts straddling `FIRST_PASS_DEPTH`'s edge."""
    return _corpora(tmp_path_factory, "edge", visible=VISIBLE_EDGE, levels=EDGE_LEVELS)


@pytest.fixture(scope="module")
def memory_corpora(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Corpus]:
    """Round seven's shape: fifty visible rows, withheld sweeping 0 -> 200."""
    return _corpora(tmp_path_factory, "memory", visible=VISIBLE_MEMORY, levels=WITHHELD_LEVELS)


def test_a_purged_build_reads_canonical_once_per_visible_row_however_many_were_withheld(
    read_count_corpora: dict[int, Corpus],
) -> None:
    """T-17's marginal canonical-read cost per withheld row, driven to zero (F2, F1').

    Round six published `canonical reads = |ranking| = visible + withheld` --
    10 / 11 / 60 / 210 / 6,000 -- against a build that still held the withdrawn
    rows, and round five's argument was that such a quantity "goes away only when
    the index stops holding withdrawn rows". This is that argument measured on the
    shipped purge: on the purged build `|ranking|` is the visible count at every
    withheld level, so the per-withheld-row rate has nothing left to multiply.

    The retriever is `search_substring`'s scan below the trigram floor, which is
    the branch that carries no `LIMIT` and therefore hands the gate the entire
    match set. That is where the residual is unbounded -- on the branches that
    truncate, the read count is `depth` whatever was withheld.

    **RED path.** The stale control is asserted first and against exact derived
    values, so this test cannot pass over a corpus whose index never held the
    withheld rows, over a `fetch` that was never called, or over a purge that
    removed nothing: each of those makes stale and purged agree, and the two
    assertions demand that they differ everywhere the withheld count is non-zero.
    Both halves were taken RED by mutation, and the module docstring's table says
    which mutation took which: a purge that keeps the rows while reporting its own
    count fails the claim at 10/60/210 reads, and a `LIMIT` on the scan below the
    trigram floor fails the control.
    """
    stale = {
        withheld: _measure(corpus, corpus.stale, query=SCAN_QUERY, lexical=False)
        for withheld, corpus in read_count_corpora.items()
    }
    purged = {
        withheld: _measure(corpus, corpus.purged, query=SCAN_QUERY, lexical=False)
        for withheld, corpus in read_count_corpora.items()
    }

    assert [
        (stale[withheld].ranking_size, stale[withheld].canonical_reads)
        for withheld in WITHHELD_LEVELS
    ] == [(VISIBLE_READS + withheld, VISIBLE_READS + withheld) for withheld in WITHHELD_LEVELS], (
        "the control must move: a stale build hands the gate every withdrawn row it "
        "still holds, so its ranking and its canonical read count are both "
        "`visible + withheld`. If this is flat, the fixture is not producing the "
        "state round six measured and the purged column below proves nothing"
    )

    assert [
        (purged[withheld].ranking_size, purged[withheld].canonical_reads)
        for withheld in WITHHELD_LEVELS
    ] == [(VISIBLE_READS, VISIBLE_READS) for _ in WITHHELD_LEVELS], (
        "a purged build's canonical read count moves with how many rows were "
        "withdrawn. That is T-17's residual back: the store can count the reads, so "
        "the withheld count reaches an observer the response withholds it from"
    )
    assert [
        (purged[withheld].passes, purged[withheld].returned) for withheld in WITHHELD_LEVELS
    ] == [(1, VISIBLE_READS) for _ in WITHHELD_LEVELS], (
        f"the purged build must answer the same {VISIBLE_READS} rows in one pass at "
        f"every withheld level; a second pass is a round trip a caller can time"
    )


def test_a_purged_build_stays_at_one_retriever_pass_across_the_first_pass_depth_edge(
    edge_corpora: dict[int, Corpus],
) -> None:
    """The pass-count staircase, and the fact that a purged build has no step (F7).

    `FIRST_PASS_DEPTH = CANDIDATE_DEPTH * 2` exists so that a *single* withheld
    row cannot move the number of times a retriever is asked: it takes
    `CANDIDATE_DEPTH + 1` of them. Round five priced that edge at +35 us for the
    gate pass alone; the work log priced it at +107% on the real retriever. Either
    way it is a step function of the withheld count, and a round-trip count is
    observable as latency.

    On a purged build there is no edge to cross, and the reason is structural
    rather than marginal: the withdrawn rows are not in the file, so nothing
    displaces a visible row out of the first pass.

    `search_lexical` here, not the scan: a pass count can only move on a branch
    that truncates, and the scan reports itself exhausted on its first and only
    call.

    **RED path.** The stale control crosses from one pass to two at exactly
    `CANDIDATE_DEPTH + 1` withheld rows. That crossing is what proves the fixture
    put the withheld rows at the *top* of the ranking -- if the tie-break were not
    deterministic, or the bodies not equal-weight, the step would land somewhere
    else or not at all, and this test would fail before reaching the purged claim.
    Both halves were taken RED by mutation (the module docstring's table): a purge
    that keeps the rows while reporting its own count puts the purged build at two
    passes and 200 reads at 51 withheld, and shrinking `FIRST_PASS_DEPTH`'s
    headroom to a single row moves the edge off the control's expectation.
    """
    assert min(EDGE_LEVELS) <= CANDIDATE_DEPTH < max(EDGE_LEVELS), (
        f"the withheld counts {EDGE_LEVELS} no longer straddle CANDIDATE_DEPTH "
        f"({CANDIDATE_DEPTH}), so this test measures four points on one side of an "
        f"edge it claims to cross"
    )
    assert FIRST_PASS_DEPTH > CANDIDATE_DEPTH, (
        "FIRST_PASS_DEPTH must exceed CANDIDATE_DEPTH or the first pass cannot "
        "absorb any withheld row at all"
    )
    assert _deeper(FIRST_PASS_DEPTH) <= VISIBLE_EDGE, (
        f"the visible corpus ({VISIBLE_EDGE}) is smaller than a second pass asks "
        f"for ({_deeper(FIRST_PASS_DEPTH)}), so the stale build's second-pass read "
        f"count would be the corpus size rather than the depth"
    )

    stale = {
        withheld: _measure(corpus, corpus.stale, query=LEXICAL_QUERY, lexical=True)
        for withheld, corpus in edge_corpora.items()
    }
    purged = {
        withheld: _measure(corpus, corpus.purged, query=LEXICAL_QUERY, lexical=True)
        for withheld, corpus in edge_corpora.items()
    }

    assert [(stale[w].passes, stale[w].canonical_reads) for w in EDGE_LEVELS] == [
        (1, FIRST_PASS_DEPTH) if withheld <= CANDIDATE_DEPTH else (2, _deeper(FIRST_PASS_DEPTH))
        for withheld in EDGE_LEVELS
    ], (
        "the control must step: a stale build whose withheld rows occupy the top of "
        "the ranking clears fewer than CANDIDATE_DEPTH rows once more than "
        "CANDIDATE_DEPTH of them are withheld, and asks the retriever again at twice "
        "the depth. A flat control means the withheld rows are not at the top and "
        "the purged column below is measuring nothing"
    )

    assert [(purged[w].passes, purged[w].canonical_reads) for w in EDGE_LEVELS] == [
        (1, FIRST_PASS_DEPTH) for _ in EDGE_LEVELS
    ], (
        "a purged build's retriever pass count moves with how many rows were "
        "withdrawn. The step is observable as latency, so the withheld count reaches "
        "a caller the response withholds it from (SEC-13, T-17)"
    )
    assert all(purged[w].returned == stale[w].returned == CANDIDATE_DEPTH for w in EDGE_LEVELS), (
        "both builds must answer the same number of rows at every level; a purge "
        "that changed the answer would make the cost comparison meaningless"
    )


def test_a_purged_builds_peak_memory_stops_moving_with_the_withheld_count(
    memory_corpora: dict[int, Corpus],
) -> None:
    """Round seven's memory-shaped member of the same class (F3, F4).

    Round seven measured 3.0 -> 640.3 KB of `tracemalloc` peak over 0 -> 5,950
    withheld and recorded, in its own evidence grade, that no absolute figure and
    no growth factor there is quotable -- three later harnesses each produced a
    different magnitude. **What reproduces is the sign and the direction**, so
    this test pins exactly that and no absolute: the purged peaks are equal to
    each other, and the stale peaks strictly increase.

    Equality rather than a tolerance, because the claim is structural.
    `tracemalloc` traces Python allocations, `|ranking|` is the visible count on
    every purged build, and the rows are the same objects -- so identical peaks
    are what "the term was removed" *means* here. A tolerance would be a number
    nobody could defend, and it would stay green against a residual smaller than
    itself.

    **Scope, stated rather than assumed.** The work log isolated a 4.3 KB step in
    the *composite* purged column that appears only above 200 withheld, is not
    monotone in the withheld count, and disappears when the harness is warmed on
    the purged build instead of the stale one -- the shape of allocator pool state,
    not of a channel. Its two isolations (`search_substring` alone at 25.4 KB, the
    gate walk alone at 58.9 KB, both flat to 0.1 KB across the full 0 -> 5,950
    sweep, nine repeats) are what settle it. This pin therefore holds equality over
    the counts where the composite is measured flat, and does not extend the
    equality claim past them.

    **RED path.** The stale control is asserted first and must strictly increase.
    A harness that measured nothing -- a `fetch` never called, a corpus with no
    withheld rows, a peak read outside the traced window -- makes the stale peaks
    equal and fails there. Both halves were taken RED by mutation (the module
    docstring's table): a purge that keeps the rows while reporting its own count
    puts the purged peaks at 86.0 / 160.3 / 363.3 KB, and a `LIMIT` on the scan
    below the trigram floor flattens the control at 86.1 / 85.6 / 85.6 KB.
    """
    stale = {
        withheld: _peak_bytes(corpus, corpus.stale, query=SCAN_QUERY)
        for withheld, corpus in memory_corpora.items()
    }
    purged = {
        withheld: _peak_bytes(corpus, corpus.purged, query=SCAN_QUERY)
        for withheld, corpus in memory_corpora.items()
    }

    growing = [stale[withheld] for withheld in WITHHELD_LEVELS]
    assert all(earlier < later for earlier, later in itertools.pairwise(growing)), (
        f"the control must grow: a stale build allocates a Ranked row per withdrawn "
        f"row it still holds and a KnowledgeItem per distinct document behind them. "
        f"Flat here means the fixture is not producing the state round seven "
        f"measured. Peaks: {growing}"
    )

    flat = [purged[withheld] for withheld in WITHHELD_LEVELS]
    assert len(set(flat)) == 1, (
        f"a purged build's peak memory moves with how many rows were withdrawn, over "
        f"counts the work log measured flat. Either the purge left something behind "
        f"or the retrieval path allocates per withdrawn row. Peaks: "
        f"{dict(zip(WITHHELD_LEVELS, flat, strict=True))}"
    )
