"""What a purged build *holds* in its full-text index, and what it must not (T-17a, #499).

**Written RED, ahead of the fix these tests travel with**, in the idiom
`test_forest_purge_equality.py` established: every assertion below describes the
build ADR-0024 requires, and until `index_purge._merge_full_text` landed in this
same commit, the shipped purge did not produce it.

`test_index_purge.py` pins that a purged build **answers** as if the withdrawn
rows had never been indexed, and `test_purged_build_quantities.py` pins three
*costs* that stop moving with the withheld count. Neither pins what the file
still *contains*. FTS5's `'delete'` command does not remove a row's postings: it
writes a tombstone, and the postings stay in the segment structure until a merge.
`index_purge._delete` issues those deletes, and nothing in the module merged
afterwards -- so every purged build carried the postings of everything withdrawn
since the last full `theurian index build`.

The gap this file closes is that **the fix was invisible to the whole suite**.
Issue #499 records adding an `optimize` to the purge as a mutation and getting
SURVIVED back: the remediation changed nothing any test could see. Re-measured on
2026-09-03 against e2a950ef, the branch point this file was written at, the suite
ran 5,203 passed with or without that perturbation -- and this module was the
entire difference between the two runs. A change that fixed the residue and a
change that undid the fix were equally invisible. These tests are that missing
pin, and what now holds the fix in place.

**What reached a caller.** The responses were byte-identical: the residue did not
recover withheld *content*. What it carried was the withdrawn *count*, through
the clock. Issue #499's five-point calibration read the withdrawn count off query
duration at three of five points exactly (1.02x the never-held duration at zero
withdrawn, rising monotonically to 5.67x at 5,950). That is the duration face of
T-17a's root cause -- *the index still holds the withdrawn rows* -- and it is the
one member of the count-channel family that this project's other instruments
cannot see: `test_purged_build_quantities.py`'s own docstring records why
`tracemalloc`, canonical-read counts and retriever-pass counts are all blind to a
posting list walked inside SQLite's C code.

**So this file pins the mechanism, not the clock.** A wall-clock assertion in CI
is an instrument that lies -- a shared runner's scheduling noise is larger than
the effect at any corpus size the suite can afford. The structural quantity below
is upstream of the duration: the postings are what the scan walks, so a build
holding no more of them than a never-held one cannot take longer for a reason a
withdrawal caused.

**The structural key: `sum(length(block))` over each FTS5 table's `_data` shadow
table** (:func:`_posting_bytes`). That is the byte size of the segment structure a
query walks, which is the quantity the tombstones inflate and the quantity the
issue measured (it reports the same thing as block counts and megabytes). Block
*count* was rejected as the key for being a page-splitting artifact: measured on
2026-09-03, a 40-visible corpus's trigram block count runs 16 / 61 / 167 / 23
across 0 / 50 / 200 / 500 withdrawn, because an automerge fires between the last
two. **The byte total is not monotone in the withdrawn count either, for the same
reason**, which is why no assertion here claims monotonicity: every claim is
either *parity with a never-held build* or *flatness across a sweep*, both of
which survive an automerge landing wherever it lands.

**The corpus: 200 visible chunks, 400 withdrawn chunks at ten times the body
length, and 60 summary nodes.** Measured on this fixture (2026-09-03, SQLite
3.47.1), a single purge left 6.40x the `chunks_fts` posting bytes and 9.52x the
`chunks_trigram` posting bytes of a never-held build; with the merge that now
ships, 0.91x and 0.95x. Issue #499's own figure is 151x at 5,950 withdrawn; this
scale reproduces the shape with 3.2x of headroom against the 2x bound asserted
here, and the whole module runs in 1.7 s (median of seven runs on a quiet
machine; one 9.6 s outlier, which is the sandbox filesystem rather than the
fixture -- the byte counts it measures were identical on all seven).

**Two faces, two tables.** The chunk tables diverge on the `DELETE` the purge
issues. The node tables diverge on `_restamp`'s `UPDATE nodes SET index_build_id`,
which fires `nodes_fts_update` and `nodes_trigram_update` once per *surviving*
node and so writes a tombstone per node on **every** purge -- including one that
withdraws nothing. That face is invisible to a single purge (a single restamp's
writes automerge away: 0.75x and 0.89x measured, whether or not a merge runs) and
is what `test_repeated_purges_do_not_accumulate_full_text_structure` exists for:
over ten purges `nodes_fts` grew 7,290 -> 72,774 bytes, a 9.98x spread. A fix
applied to `chunks_fts` and `chunks_trigram` alone passes the first three tests
here and fails the fourth.

**Every claim carries a control that must move**, because "the purged build is
small" is satisfied by a fixture that never put the withdrawn rows in the index,
by a purge that deleted the visible rows too, and by a key that returns the same
number for every file. The controls are the stale build's own quantities, the
surviving row counts at every step, and the never-held build's quantities being
non-zero.

**The paths, run rather than argued** (2026-09-03, SQLite 3.47.1, `tools/mutate.py
--prepare-tree --with-git` from a plain clone -- one isolated copy per
perturbation, nothing applied to a live checkout). Three of the four tests here
were RED when they were written, against the tree as it stood before the fix they
now travel with, so the usual mutation question was inverted: what needed
demonstrating was not that they could fail, which was already on the record, but
that they were *passable*, and that their controls were not decoration.

- **A merge over all four tables, after `_restamp` and before `_verify`.** All
  six tests here GREEN, and the rest of the suite unchanged at 5,203 passed --
  so the remediation this file drives breaks nothing that exists. That shape is
  what `index_purge._merge_full_text` now ships. What this file settles is the
  remediation *class* and not its implementation: no test below names `optimize`
  or requires the merge to run in any particular place, and the two choices that
  leaves open -- which tables a merge finds, and where in `purge_into` it sits --
  are pinned by `test_purge_full_text_discovery.py` instead.
- **`_delete` reports `removed=0` while deleting normally.** The sweep fixture's
  own guard fires on all five of its tests, and the chain's first control fires
  (`the first purge reported 0 rows removed where 400 were withdrawn`). The
  count controls are live.
- **The withdrawn rows carry a one-character body.** The parity control and the
  sweep control both RED -- stale 30,949 against never-held 23,309 `chunks_fts`
  bytes, a 1.33x that no longer clears :data:`CONTROL_FACTOR`. So neither claim
  can pass on a fixture whose withdrawn rows carry no postings.
- **The ranking key rounds to zero decimals instead of ten.** The response
  equality's control RED on all three queries: at that precision a gated stale
  ranking already reproduces the never-held one, so the ten decimals are
  load-bearing rather than decorative.

Nothing here reads a clock, starts a daemon, registers a service or writes outside
`tmp_path`.
"""

from __future__ import annotations

import hashlib
import itertools
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.schema import read_only_uri

pytestmark = pytest.mark.integration

#: The disclosure grant every retriever call in this file runs under: all four
#: levels, spelled out rather than read from ``StaticAuthorizationProvider``'s
#: shipped default, which a later phase narrows. A file that inherited it would
#: start withholding its own fixtures silently (`test_index_purge.py`'s note).
EVERY_SENSITIVITY: Final = frozenset(Sensitivity)

PROJECT: Final = "demo"

#: The four FTS5 tables an index build carries (`index_schema.py`). All four are
#: measured everywhere, so a fix applied to some of them is visible as the ones it
#: missed rather than as a green run.
FULL_TEXT_TABLES: Final = ("chunks_fts", "chunks_trigram", "nodes_fts", "nodes_trigram")

#: The two the `DELETE` reaches. Named separately because they are the tables the
#: parity claim has a moving control for: the stale build genuinely holds the
#: withdrawn chunks' postings, while it holds exactly the same nodes a never-held
#: build does, so a node-table parity claim would have no control at all.
CHUNK_TABLES: Final = ("chunks_fts", "chunks_trigram")

#: An ordinary document, and the ten-times-longer body the withdrawn ones carry.
#: Long, so each withdrawn row contributes enough postings for the divergence to
#: be decisive at a corpus size the suite can afford -- and long for the same
#: reason `test_index_purge.py` makes them long, so the two files' fixtures do not
#: drift apart for no reason.
ORDINARY_BODY: Final = (
    "Retention and isolation are decided per namespace. Authentication tokens "
    "rotate on restart. The quarantine ledger records every attempt. "
)
LONG_BODY: Final = ORDINARY_BODY * 10

#: A summary node's text. Four times the ordinary body, so the node tables carry
#: enough postings for the ten-purge spread to be a measurement rather than a
#: rounding artifact.
NODE_BODY: Final = ORDINARY_BODY * 4

VISIBLE: Final = 200
WITHDRAWN: Final = 400
NODES: Final = 60

#: The withheld counts the flatness claim sweeps. Zero is the identity case, and
#: it is what makes "flat" an equality against a build the purge did not change
#: rather than against whatever the first measured level happened to be.
WITHHELD_LEVELS: Final = (0, 100, WITHDRAWN)

#: How many purges the accumulation claim chains. Ten, per the issue: the residue
#: is bounded by everything withdrawn since the last full build and does not clear
#: by itself, so a purge of a purge of a purge keeps every earlier tombstone.
PURGE_CHAIN_LENGTH: Final = 10

#: How far a purged build's structure may sit from a never-held build's, in either
#: direction. **Two-sided on purpose**: a one-sided ceiling is satisfied by a build
#: that lost content, and the single segment the merge produces is legitimately
#: *smaller* than the several segments a fresh build leaves (0.91x and 0.95x
#: measured). Two is decisive against the 6.40x and 9.52x measured before the
#: merge with 3.2x of headroom, and against the 0.91x floor with 1.8x.
PARITY_FACTOR: Final = 2.0

#: How far the members of a sweep may sit from each other. Tighter than
#: :data:`PARITY_FACTOR` because this compares a build against builds over the
#: *same* visible corpus rather than against a differently-segmented fresh one:
#: with the residue gone the three levels collapse to the same postings. Measured
#: spreads with the merge in place are 1.01x and 1.00x; before it they were 6.40x
#: and 9.52x.
FLAT_FACTOR: Final = 1.5

#: How much bigger a stale build must be than a never-held one before any claim
#: below is allowed to mean anything. Measured 5.67x and 8.77x; two leaves room
#: for a differently-tuned FTS5 without letting a fixture that indexed no
#: withdrawn rows through.
CONTROL_FACTOR: Final = 2.0

#: Read by the sweep's control: each level's stale build must be this much bigger
#: than the previous one, so a sweep that stopped varying the withheld count fails
#: before any purged column is examined. Measured steps are 2.09x/2.71x
#: (`chunks_fts`) and 2.95x/2.97x (`chunks_trigram`).
CONTROL_STEP: Final = 1.25

QUERIES: Final = ("retention isolation", "authentication token", "quarantine ledger")


# -- the structural key ------------------------------------------------------


def _posting_bytes(path: Path) -> dict[str, int]:
    """Bytes of FTS5 posting data per full-text table, from the `_data` shadow tables.

    An FTS5 table keeps its segment b-tree in a shadow table named ``<name>_data``,
    one row per block. The sum of those blocks' lengths is the posting data a query
    walks, and it is what a tombstoned row keeps occupying: `'delete'` appends a
    delete marker rather than removing the postings, so the sum grows with every
    withdrawal and only a merge brings it back down.

    Opened ``mode=ro`` through :func:`read_only_uri` for the reason
    `index_purge._copy` does: a bare ``sqlite3.connect`` on a path that does not
    exist *creates* an empty database there, so a typo in a fixture path would be
    measured as an index with no postings instead of raising.
    """
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return {
            table: int(
                connection.execute(
                    f"SELECT coalesce(sum(length(block)), 0) FROM {table}_data"  # noqa: S608 - module-owned literals
                ).fetchone()[0]
            )
            for table in FULL_TEXT_TABLES
        }


def _chunk_ids(path: Path) -> set[str]:
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return {str(row[0]) for row in connection.execute("SELECT chunk_id FROM chunks")}


def _node_ids(path: Path) -> set[str]:
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return {str(row[0]) for row in connection.execute("SELECT node_id FROM nodes")}


# -- the corpus --------------------------------------------------------------


def _indexable(chunk_id: str, text: str, *, revision: str) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=""),
        project_id=PROJECT,
        item_id=f"architecture.{revision}",
        revision_id=revision,
        served_content_sha256=f"body-of-{revision}",
        status="approved",
        sensitivity="internal",
        trust_level="reviewed",
    )


def _add_nodes(path: Path, count: int) -> None:
    """Write `count` summary nodes, each grounded in one surviving chunk.

    Raw SQL for the reason `test_index_purge.py`'s node section gives: what is
    under test is the purge, and a hand-written row reaches the state a real
    ``--raptor`` build leaves without paying for a summariser. Each node names a
    distinct ``keep-`` chunk, so every one of them survives every purge below --
    which is what makes the node tables' growth attributable to `_restamp` rather
    than to nodes coming and going.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            for ordinal in range(count):
                node_id = f"summary-{ordinal:04d}"
                connection.execute(
                    "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
                    "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
                    "embedding_model_revision, embedding_dimension, source_revision_id, "
                    "index_build_id, project_id, sensitivity, status) "
                    "VALUES (?, 'tree-abc', 1, 'document', ?, ?, '', '', '', '', '', 0, '', "
                    "'01K1SEED', ?, 'internal', 'approved')",
                    (
                        node_id,
                        f"{NODE_BODY} node {ordinal}.",
                        hashlib.sha256(node_id.encode()).hexdigest(),
                        PROJECT,
                    ),
                )
                connection.execute(
                    "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                    "VALUES (?, ?, NULL)",
                    (node_id, f"keep-{ordinal:05d}#0"),
                )


def _build(path: Path, *, withheld: int, build_id: str) -> tuple[SqliteIndexStore, list[str]]:
    """A build over :data:`VISIBLE` visible chunks plus `withheld` withdrawn ones.

    Interleaved by chunk id before insertion, so the visible rows land at the same
    ``chunks`` rowids in every build here. FTS5 keys on that rowid, so a different
    insertion order would make two builds differ for a reason that has nothing to
    do with the purge -- `test_index_purge.py`'s `_populate` makes the same
    arrangement for the same reason.
    """
    store = SqliteIndexStore(path)
    store.create(index_build_id=build_id, state_hash="state-abc")
    chunks = [
        _indexable(f"keep-{n:05d}#0", f"{ORDINARY_BODY} paragraph {n}.", revision=f"keep-{n:05d}")
        for n in range(VISIBLE)
    ]
    withdrawn: list[str] = []
    for n in range(withheld):
        revision = f"gone-{n:05d}"
        withdrawn.append(revision)
        chunks.append(_indexable(f"{revision}#0", f"{LONG_BODY} paragraph {n}.", revision=revision))
    store.add_chunks(sorted(chunks, key=lambda chunk: chunk.chunk.chunk_id))
    _add_nodes(path, NODES)
    return store, withdrawn


@dataclass(frozen=True, slots=True)
class Corpus:
    """One withheld count, as three builds over the same visible rows.

    `stale` holds the withdrawn rows, `purged` is what the purge made of it, and
    `never_held` was built over the visible rows alone. The equality ADR-0024 was
    accepted on is *one query against two corpora*, and the structural claim here
    is the same comparison asked of the file rather than of the response.
    """

    withheld: int
    stale: SqliteIndexStore
    purged: SqliteIndexStore
    never_held: SqliteIndexStore
    withdrawn: tuple[str, ...]


def _corpus(root: Path, *, withheld: int) -> Corpus:
    root.mkdir(parents=True, exist_ok=True)
    stale, withdrawn = _build(root / "stale.sqlite", withheld=withheld, build_id="01K1STALE")
    never_held, _ = _build(root / "fresh.sqlite", withheld=0, build_id="01K1FRESH")

    purged_path = root / "purged.sqlite"
    removed = stale.derive_purged(
        purged_path,
        revision_ids=withdrawn,
        index_build_id="01K1PURGED",
        state_hash="state-abc",
    )
    assert removed == withheld, (
        f"the purge removed {removed} rows where {withheld} were withdrawn, so the builds "
        f"compared below do not differ by what this module says they differ by"
    )
    return Corpus(
        withheld=withheld,
        stale=stale,
        purged=SqliteIndexStore(purged_path),
        never_held=never_held,
        withdrawn=tuple(withdrawn),
    )


@pytest.fixture(scope="module")
def swept_corpora(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Corpus]:
    """One `Corpus` per level of :data:`WITHHELD_LEVELS`, over the same visible rows."""
    return {
        withheld: _corpus(tmp_path_factory.mktemp(f"sweep-{withheld}"), withheld=withheld)
        for withheld in WITHHELD_LEVELS
    }


@dataclass(frozen=True, slots=True)
class Chain:
    """:data:`PURGE_CHAIN_LENGTH` purges, each taken from the one before it.

    `steps` is the posting-byte reading after each purge, in order; `removed` is
    what each purge reported. The first withdraws everything and the rest withdraw
    nothing, so the *visible* corpus is identical at every step and "the structure
    did not grow" is a claim about residue rather than about content leaving.
    """

    steps: tuple[dict[str, int], ...]
    removed: tuple[int, ...]
    surviving_chunks: tuple[int, ...]
    surviving_nodes: tuple[int, ...]
    never_held: dict[str, int]
    stale: dict[str, int]


@pytest.fixture(scope="module")
def purge_chain(tmp_path_factory: pytest.TempPathFactory) -> Chain:
    """Purge a purged build, ten times over."""
    root = tmp_path_factory.mktemp("chain")
    store, withdrawn = _build(root / "build-00.sqlite", withheld=WITHDRAWN, build_id="01K1B00")
    never_held, _ = _build(root / "fresh.sqlite", withheld=0, build_id="01K1FRESH")

    steps: list[dict[str, int]] = []
    removed: list[int] = []
    chunks: list[int] = []
    nodes: list[int] = []
    current = store
    for step in range(PURGE_CHAIN_LENGTH):
        target = root / f"build-{step + 1:02d}.sqlite"
        # Everything on the first purge; nothing on the rest. A purge with an
        # empty withdrawal list is an ordinary residue-cleanup purge, not a no-op
        # (`test_purging_nothing_is_a_faithful_copy`), and it is the one that
        # isolates `_restamp`'s per-node rewrite from the `DELETE`.
        removed.append(
            current.derive_purged(
                target,
                revision_ids=list(withdrawn) if step == 0 else [],
                index_build_id=f"01K1B{step + 1:02d}",
                state_hash="state-abc",
            )
        )
        steps.append(_posting_bytes(target))
        chunks.append(len(_chunk_ids(target)))
        nodes.append(len(_node_ids(target)))
        current = SqliteIndexStore(target)

    return Chain(
        steps=tuple(steps),
        removed=tuple(removed),
        surviving_chunks=tuple(chunks),
        surviving_nodes=tuple(nodes),
        never_held=_posting_bytes(never_held.path),
        stale=_posting_bytes(store.path),
    )


def _ranking(store: SqliteIndexStore, query: str) -> list[tuple[str, float]]:
    """The whole lexical ranking, ids and scores to ten decimals."""
    return [
        (row.chunk_id, round(row.score, 10))
        for row in store.search_lexical(
            query,
            project_id=PROJECT,
            limit=100_000,
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        ).rows
    ]


# -- the claims --------------------------------------------------------------


def test_a_purged_build_holds_no_more_full_text_structure_than_one_that_never_held_the_rows(
    swept_corpora: dict[int, Corpus],
) -> None:
    """The two-corpus equality ADR-0024 was accepted on, asked of the file (T-17a, #499).

    `test_index_purge.py` holds it for the *response*: a purged build answers
    identically to one that never held the withdrawn rows. That equality is
    satisfied by a file that still contains every withdrawn posting, because the
    postings a tombstone hides are excluded from the answer while remaining in the
    segment structure -- and what the caller can still observe about them is the
    time the scan spends walking past them. Issue #499 read the withdrawn count off
    that clock at three of five calibration points.

    So the same comparison is made of the file. Measured 2026-09-03 on this
    fixture, the purge left 6.40x the `chunks_fts` posting bytes and 9.52x the
    `chunks_trigram` posting bytes of a never-held build; with the merge that now
    follows the delete, 0.91x and 0.95x.

    The node tables are excluded here and not forgotten: a purge deletes no node
    that survives, so the stale build holds exactly the nodes a never-held build
    does and there is no control to make a node-table parity claim mean anything.
    `_restamp`'s per-node rewrite is what moves them, and
    `test_repeated_purges_do_not_accumulate_full_text_structure` is where that is
    measured.

    **RED before `_merge_full_text`, GREEN with it.** The control is asserted
    first: the stale build must carry at least :data:`CONTROL_FACTOR` times the
    never-held build's postings, which fails on a fixture whose index never held
    the withdrawn rows, on a purge that removed nothing, and on a key that answers
    the same number for every file.
    """
    corpus = swept_corpora[WITHDRAWN]
    stale = _posting_bytes(corpus.stale.path)
    purged = _posting_bytes(corpus.purged.path)
    never_held = _posting_bytes(corpus.never_held.path)

    assert all(never_held[table] > 0 for table in CHUNK_TABLES), (
        f"the never-held build has no postings at all in {CHUNK_TABLES}, so every ratio "
        f"below is undefined and the key is measuring the wrong thing: {never_held}"
    )
    assert all(stale[table] >= never_held[table] * CONTROL_FACTOR for table in CHUNK_TABLES), (
        f"the control must move: a build holding {WITHDRAWN} withdrawn rows at ten times "
        f"the ordinary body length carries far more posting data than one built over the "
        f"{VISIBLE} visible rows alone. If it does not, the fixture never indexed the "
        f"withdrawn rows and the purged column below proves nothing. "
        f"stale={stale} never_held={never_held}"
    )

    assert all(
        never_held[table] / PARITY_FACTOR <= purged[table] <= never_held[table] * PARITY_FACTOR
        for table in CHUNK_TABLES
    ), (
        f"a purged build still carries the withdrawn rows' postings. FTS5's 'delete' "
        f"writes a tombstone and leaves the posting list in the segment structure, so "
        f"the scan walks past every withdrawn row on every query -- which is how #499 "
        f"read the withdrawn count off the clock. "
        f"purged={purged} never_held={never_held}"
    )


@pytest.mark.parametrize("query", QUERIES)
def test_a_purged_build_answers_identically_at_the_scale_the_structure_is_measured_on(
    swept_corpora: dict[int, Corpus], query: str
) -> None:
    """The response equality, held over *this* file's corpus (ADR-0024, T-17).

    `test_index_purge.py::test_a_purged_build_answers_as_if_the_rows_were_never_
    indexed` already pins this property, over a 40-visible/6-withdrawn corpus.
    Repeating it here is not duplication but the guard on the fix: the remediation
    #499 asked for rewrites the segment structure the retrievers read, and the one
    thing it must not do is change an answer. A merge that dropped a live posting,
    or renumbered a rowid an external-content table keys on, would be invisible to
    every structural assertion in this module -- they all say the file got
    *smaller* -- and would show up here as a ranking that stopped matching.

    Ids and BM25 scores to ten decimals, not merely the same set of documents,
    because the channel T-17a is about is a *score* shift: FTS5 computes `bm25`
    against collection statistics taken over every row in the file, so a withdrawn
    row reweights the ones the caller may see.

    **GREEN before `_merge_full_text` and required to stay GREEN with it.** This
    is the one test here the fix had to leave alone rather than turn, which is
    what makes it the guard rather than a claim. The control is the
    stale build's own ranking, gated down to the visible ids: it must differ from
    the never-held ranking, or the withdrawn rows are not reweighting anything on
    this corpus and the equality below would hold for two builds that never had a
    channel between them.
    """
    corpus = swept_corpora[WITHDRAWN]
    visible = {chunk_id for chunk_id, _ in _ranking(corpus.never_held, query)}

    stale_gated = [row for row in _ranking(corpus.stale, query) if row[0] in visible]

    assert stale_gated != _ranking(corpus.never_held, query), (
        "the control must differ: the withdrawn rows are supposed to reweight the visible "
        "ones through BM25's collection statistics, and if gating them out of a stale "
        "ranking already reproduces the never-held one there is no channel on this corpus"
    )
    assert _ranking(corpus.purged, query) == _ranking(corpus.never_held, query), (
        "a purged build must answer identically to one that never held the withdrawn rows "
        "-- chunk ids and BM25 scores to ten decimals. A structural remediation that "
        "changed an answer would be trading one defect for a worse one"
    )


def test_the_full_text_structure_does_not_grow_with_the_number_of_rows_withdrawn(
    swept_corpora: dict[int, Corpus],
) -> None:
    """The count channel's structural face, standing in for the timing face (#499).

    Issue #499's finding is not that a purged build is large. It is that its query
    *duration* is monotone in how many rows were withdrawn -- 1.02x the never-held
    duration at zero, 5.67x at 5,950 -- and that a five-point calibration recovered
    the withdrawn count from that duration at three of five points exactly. The
    withdrawn count is precisely what SEC-13 arranges the response not to state, so
    a clock that reports it is the disclosure channel, whatever the response body
    says.

    **A committed test cannot pin the clock.** Wall-clock in CI is the instrument
    that lies: a shared runner's scheduling noise exceeds the effect at any corpus
    size a test suite can afford, so a timing assertion would either flake or be so
    loose it could not fail. What the clock is a function of *can* be pinned. The
    postings are what the scan walks; if the structure a query walks does not vary
    with the withdrawn count, no duration derived from it can either.

    So: three withheld counts over the same visible corpus, and the purged builds'
    structures must be within :data:`FLAT_FACTOR` of each other. Measured
    2026-09-03, the purge spread 6.40x (`chunks_fts`) and 9.52x
    (`chunks_trigram`) across 0 / 100 / 400 withdrawn; with the merge that now
    follows the delete, 1.01x and 1.00x.

    **Flatness rather than non-monotonicity**, and the difference is not
    cosmetic: FTS5's automerge fires on segment counts, so the byte total was not
    a monotone function of the withdrawn count even unmerged -- on a 40-visible
    corpus it ran 33.9 / 192.7 / 614.6 / 79.6 KB across 0 / 50 / 200 / 500
    withdrawn. An assertion phrased as "not monotone" would have been satisfied by
    that sawtooth while the channel was wide open. Flatness is not.

    **RED before `_merge_full_text`, GREEN with it.** The control is asserted
    first and demands
    that the *stale* builds grow by at least :data:`CONTROL_STEP` at every level,
    so a sweep that stopped varying the withheld count -- or a fixture that indexed
    none of it -- fails before the purged column is looked at.
    """
    stale = {level: _posting_bytes(swept_corpora[level].stale.path) for level in WITHHELD_LEVELS}
    purged = {level: _posting_bytes(swept_corpora[level].purged.path) for level in WITHHELD_LEVELS}

    assert all(
        stale[higher][table] >= stale[lower][table] * CONTROL_STEP
        for table in CHUNK_TABLES
        for lower, higher in itertools.pairwise(WITHHELD_LEVELS)
    ), (
        f"the control must grow at every step: a stale build carries the postings of every "
        f"withdrawn row it still holds, so sweeping the withheld count sweeps its structure. "
        f"Flat here means the sweep is not sweeping and the purged column below is measuring "
        f"one corpus three times. stale={stale}"
    )

    for table in CHUNK_TABLES:
        series = [purged[level][table] for level in WITHHELD_LEVELS]
        assert max(series) <= min(series) * FLAT_FACTOR, (
            f"a purged build's {table} structure grows with how many rows were withdrawn, so "
            f"the time a query spends walking it does too. That is the count channel #499 "
            f"calibrated: the response withholds the withdrawn count and the clock reports "
            f"it. bytes at {WITHHELD_LEVELS} withdrawn = {series}"
        )


def test_repeated_purges_do_not_accumulate_full_text_structure(purge_chain: Chain) -> None:
    """The residue does not clear by itself, so every purge must clear it (#499).

    A purge copies the build before it, so whatever the previous purge left behind
    is carried into the next one and added to. The issue states the bound: the
    residue is everything withdrawn since the last full `theurian index build`,
    and nothing short of that build removes it. A remediation that ran once, or
    ran only on the path that deletes something, would leave a project that purges
    weekly with a monotonically growing index between builds.

    **This is also where the node tables are pinned, and they are a distinct
    face.** `_restamp` issues `UPDATE nodes SET index_build_id = ?` so that a
    surviving summary names the build it is actually in, and that fires
    `nodes_fts_update` and `nodes_trigram_update` once per surviving node -- a
    tombstone plus a re-insert for every node, on **every** purge, including the
    nine here that withdraw nothing at all. Measured 2026-09-03 over these ten
    purges, `nodes_fts` ran 7,290 -> 72,774 bytes, a 9.98x spread, while
    `chunks_fts` and `chunks_trigram` stayed flat at the 6.40x and 9.52x the first
    purge left them at. So the two assertions below failed on different tables: the
    flatness one on the node tables, the parity one on the chunk tables. A fix
    applied to `chunks_fts` and `chunks_trigram` alone leaves this test RED, which
    is the point of measuring all four.

    **RED before `_merge_full_text`, GREEN with it.** The controls are asserted
    first and are
    what stop flatness from being achieved by content leaving: the first purge must
    report every withdrawn row removed, the remaining nine must report none, and
    every step must still hold the same visible chunks and the same surviving
    nodes. Without them a purge that emptied the build would pass both claims.
    """
    assert purge_chain.removed[0] == WITHDRAWN, (
        f"the first purge reported {purge_chain.removed[0]} rows removed where {WITHDRAWN} "
        f"were withdrawn, so this chain does not start from the state it claims to"
    )
    assert set(purge_chain.removed[1:]) == {0}, (
        f"the follow-up purges must be residue-cleanup purges with nothing left to remove, "
        f"or the structure legitimately shrinks and flatness claims nothing: "
        f"removed={purge_chain.removed}"
    )
    assert set(purge_chain.surviving_chunks) == {VISIBLE}, (
        f"every step must hold the same {VISIBLE} visible chunks; a chain that shed content "
        f"would satisfy both claims below by losing the corpus. "
        f"chunks={purge_chain.surviving_chunks}"
    )
    assert set(purge_chain.surviving_nodes) == {NODES}, (
        f"every step must hold the same {NODES} summary nodes, or the node tables' flatness "
        f"is nodes disappearing rather than residue being cleared. "
        f"nodes={purge_chain.surviving_nodes}"
    )

    for table in FULL_TEXT_TABLES:
        series = [step[table] for step in purge_chain.steps]
        assert max(series) <= min(series) * FLAT_FACTOR, (
            f"{table} grows with each successive purge over an unchanging corpus. Every purge "
            f"copies the previous build's residue and adds its own, so a project that purges "
            f"between full builds accumulates postings for rows nothing serves. "
            f"bytes per purge = {series}"
        )

    final = purge_chain.steps[-1]
    assert all(
        purge_chain.never_held[table] / PARITY_FACTOR
        <= final[table]
        <= purge_chain.never_held[table] * PARITY_FACTOR
        for table in FULL_TEXT_TABLES
    ), (
        f"after {PURGE_CHAIN_LENGTH} purges the build still holds more full-text structure "
        f"than one built over the same visible rows. The residue is bounded by everything "
        f"withdrawn since the last full build and does not clear, so it has to be cleared "
        f"by the purge. final={final} never_held={purge_chain.never_held}"
    )
