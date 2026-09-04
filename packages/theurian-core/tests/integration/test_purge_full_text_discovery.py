"""Which tables the purge's full-text merge reaches, and which it must not (#499).

`test_purged_build_structure.py` pins that a purged build holds no more full-text
structure than one that never held the withdrawn rows. It measures the four FTS5
tables `index_schema.py` ships today, so it would stay green against a merge that
worked from a written-down list of exactly those four -- and that list is the
shape the residue comes back through. The schema carried two of these tables at
v3 and carries four at v4; issue #499's own sketch of the fix says "both FTS5
tables", which was the v3 count. A fifth table would be indexed, purged, and left
holding its tombstones, and nothing would say so.

So the merge reads the build's own `sqlite_master` instead, and this file is what
makes that choice load-bearing rather than incidental: it plants a fifth FTS5
table that no constant in the codebase names, and requires the purge to merge it.
Measured 2026-09-03 by replacing the discovery with a tuple of the four shipped
names: the first test below goes RED, and `test_purged_build_structure.py` stays
green on all six of its own.

The same reading has to be *narrow*, because a purge is all-or-nothing and
anything it raises on destroys the build. Two decoys stand for the two ways a
loose reading raises, each message measured on SQLite 3.47.1:

- an `fts5vocab` view, which matches a `LIKE '%fts5%'` reading of the schema and
  is not writable -- `INSERT INTO v(v) VALUES ('optimize')` against one answers
  `table planted_vocab may not be modified`;
- a plain table whose *name* ends in `_fts`, which matches a name-keyed reading
  and answers `table pretend_fts has no column named pretend_fts`.

Under either loose reading the purge below raises instead of returning, so the
decoys need no assertion of their own beyond the purge completing -- though both
are checked intact afterwards, since a merge that silently rewrote one would be
worse than a merge that refused.

The third claim is *when* rather than which: a published purge must already be
merged, which is checked by merging it again and finding nothing to remove. That
is what makes the call's position in `purge_into` load-bearing rather than
arbitrary, and it is the one property the structural file cannot see -- the
residue a mistimed merge leaves is bounded by the surviving node count, and on
that file's corpus it lands at 1.77x, under its two-sided 2.0x band. Measured
2026-09-03: with the merge issued before `_restamp` instead of after, re-merging
a published build takes `nodes_fts` 14,570 -> 7,294 bytes and `nodes_trigram`
141,527 -> 70,773; with the shipped ordering all four tables are unchanged to the
byte.

Nothing here reads a clock, starts a daemon, registers a service, or writes
outside `tmp_path`.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.schema import read_only_uri

pytestmark = pytest.mark.integration

PROJECT: Final = "demo"

#: An FTS5 table the schema does not ship and no constant in the tree names. The
#: whole point of the file: the merge has to reach it because it is *there*, not
#: because someone remembered to add it to a list.
PLANTED: Final = "planted_fts"

#: Matches a `LIKE '%fts5%'` reading of `sqlite_master.sql` and is read-only, so
#: a merge that took it would refuse every build that carried one.
VOCAB_DECOY: Final = "planted_vocab"

#: An ordinary table whose name ends the way the shipped FTS5 tables' names do.
NAME_DECOY: Final = "pretend_fts"

BODY: Final = (
    "Retention and isolation are decided per namespace. Authentication tokens "
    "rotate on restart. The quarantine ledger records every attempt. "
)

VISIBLE: Final = 60

#: Summary nodes, each grounded in one surviving chunk so it survives every purge
#: here. They exist for the third claim only: `_restamp` rewrites every one of
#: them, and that rewrite is the last full-text write a purge makes.
NODES: Final = 40

#: How far a re-merge of a published build may move a table's posting bytes.
#: Zero movement is what FTS5 does with an index that is already one segment, and
#: what the shipped ordering measures; the tolerance is for a future FTS5 that
#: re-packs a single segment slightly differently. What it must not admit is
#: residue, which is a fraction of the table rather than a rounding of it -- the
#: mistimed placement this rejects halves both node tables.
REMERGE_TOLERANCE: Final = 0.95

#: Rows written into :data:`PLANTED` and then deleted, leaving tombstones. Enough
#: that the postings they leave behind dominate the ones that survive, so
#: "the merge ran" and "the merge did not run" are not a rounding apart.
PLANTED_ROWS: Final = 400
PLANTED_SURVIVORS: Final = 40


def _live_rows(path: Path, table: str) -> int:
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return int(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 - module-owned literals
        )


def _vocabulary(path: Path) -> list[tuple[str, int, int]]:
    """What :data:`VOCAB_DECOY` reports: one row per distinct term, with its counts.

    An `fts5vocab` view reads the index directly rather than the content table, so
    it is the surface that would notice a merge changing what the index *means*
    rather than only how it is stored.
    """
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return [
            (str(term), int(doc), int(cnt))
            for term, doc, cnt in connection.execute(
                f"SELECT term, doc, cnt FROM {VOCAB_DECOY} ORDER BY term"  # noqa: S608 - module-owned literals
            )
        ]


def _posting_bytes(path: Path, table: str) -> int:
    """Bytes of FTS5 posting data in one table's `_data` shadow table.

    `mode=ro` for the reason `index_purge._copy` opens its source that way: a bare
    `sqlite3.connect` on a path that does not exist creates an empty database
    there, so a typo would read as a table with no postings rather than raise.
    """
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return int(
            connection.execute(
                f"SELECT coalesce(sum(length(block)), 0) FROM {table}_data"  # noqa: S608 - module-owned literals
            ).fetchone()[0]
        )


def _full_text_tables(path: Path) -> list[str]:
    """Every FTS5 table in the file, discovered the way the purge discovers them.

    Read here rather than imported from `index_purge`, so this file agrees with
    the production reading only when both are right about the same build. The
    predicate is deliberately the coarse one -- every virtual table whose
    declaration mentions `fts5` -- because a test that reused the production
    regex could not notice that regex going wrong.
    """
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return [
            str(name)
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL "
                "ORDER BY name"
            )
            if "fts5(" in str(sql).lower().replace(" ", "")
        ]


def _add_nodes(path: Path, count: int) -> None:
    """Write `count` summary nodes, each grounded in one surviving chunk.

    Raw SQL for the reason `test_index_purge.py`'s node section gives: what is
    under test is the purge, and a hand-written row reaches the state a real
    ``--raptor`` build leaves without paying for a summariser.
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
                    "'01K1SOURCE', ?, 'internal', 'approved')",
                    (
                        node_id,
                        f"{BODY * 4} node {ordinal}.",
                        hashlib.sha256(node_id.encode()).hexdigest(),
                        PROJECT,
                    ),
                )
                connection.execute(
                    "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                    "VALUES (?, ?, NULL)",
                    (node_id, f"keep-{ordinal:04d}#0"),
                )


def _plant(path: Path) -> None:
    """Add the fifth FTS5 table and the two decoys, and tombstone most of the fifth.

    Deleted rather than never inserted, because what the merge removes is a
    *tombstone*: FTS5 answers a `DELETE` by appending a delete marker and leaves
    the row's posting list in the segment structure until something merges it.
    """
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(f"CREATE VIRTUAL TABLE {PLANTED} USING fts5(text)")
            connection.executemany(
                f"INSERT INTO {PLANTED}(rowid, text) VALUES (?, ?)",  # noqa: S608 - module-owned literals
                [(n, f"{BODY} planted row {n}.") for n in range(PLANTED_ROWS)],
            )
        with connection:
            connection.executemany(
                f"DELETE FROM {PLANTED} WHERE rowid = ?",  # noqa: S608 - module-owned literals
                [(n,) for n in range(PLANTED_SURVIVORS, PLANTED_ROWS)],
            )
        with connection:
            connection.execute(
                f"CREATE VIRTUAL TABLE {VOCAB_DECOY} USING fts5vocab({PLANTED}, row)"
            )
            connection.execute(f"CREATE TABLE {NAME_DECOY} (id INTEGER PRIMARY KEY, note TEXT)")
            connection.execute(
                f"INSERT INTO {NAME_DECOY} (id, note) VALUES (1, 'untouched')"  # noqa: S608 - module-owned literals
            )


@pytest.fixture
def planted_build(tmp_path: Path) -> Path:
    """A build carrying a tombstoned fifth FTS5 table and both decoys."""
    path = tmp_path / "source.sqlite"
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1SOURCE", state_hash="state-abc")
    store.add_chunks(
        [
            IndexableChunk(
                chunk=Chunk(chunk_id=f"keep-{n:04d}#0", ordinal=0, text=f"{BODY} {n}.", heading=""),
                project_id=PROJECT,
                item_id=f"architecture.keep-{n:04d}",
                revision_id=f"keep-{n:04d}",
                served_content_sha256=f"body-of-keep-{n:04d}",
                status="approved",
                sensitivity="internal",
                trust_level="reviewed",
            )
            for n in range(VISIBLE)
        ]
    )
    _add_nodes(path, NODES)
    _plant(path)
    return path


def test_the_purge_merges_a_full_text_table_no_constant_in_the_tree_names(
    planted_build: Path, tmp_path: Path
) -> None:
    """The merge covers every FTS5 table in the file, not the ones someone listed.

    A purge with an empty withdrawal list, which is the cheapest way to reach the
    merge: it removes nothing, so any change in :data:`PLANTED`'s posting bytes is
    the merge and not the delete.

    The control is asserted first: the source must carry substantially more
    posting data than its surviving rows justify, or there was no residue to
    remove and the collapse below would be measuring nothing.
    """
    before = _posting_bytes(planted_build, PLANTED)
    surviving_share = before * PLANTED_SURVIVORS / PLANTED_ROWS

    target = tmp_path / "purged.sqlite"
    removed = SqliteIndexStore(planted_build).derive_purged(
        target, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert removed == 0, (
        f"this purge withdraws nothing, so any movement in {PLANTED} is the merge; "
        f"it reported {removed} rows removed"
    )
    assert before > surviving_share * 4, (
        f"the control must move: {PLANTED_ROWS - PLANTED_SURVIVORS} of {PLANTED_ROWS} rows were "
        f"deleted, so the source is supposed to be carrying their tombstoned postings. If it is "
        f"not, there is no residue here and the collapse below proves nothing. bytes={before}"
    )

    assert _live_rows(target, PLANTED) == PLANTED_SURVIVORS, (
        f"{PLANTED} must still hold its {PLANTED_SURVIVORS} live rows; a merge that shed them "
        f"would satisfy the collapse below by emptying the table"
    )

    after = _posting_bytes(target, PLANTED)
    assert after < before / 2, (
        f"the purge left {PLANTED} holding its tombstones. The merge works from a list of the "
        f"tables the schema shipped when it was written rather than from the build in front of "
        f"it, so a table added later keeps the postings of every row withdrawn from it. "
        f"before={before} after={after}"
    )


def test_the_purge_leaves_a_read_only_vocab_view_and_a_lookalike_table_alone(
    planted_build: Path, tmp_path: Path
) -> None:
    """The reading is narrow enough not to destroy the build it is merging.

    Both decoys raise when a merge issues `optimize` against them, and a purge
    that raises unlinks its output -- so `derive_purged` returning at all is the
    assertion. What follows checks that neither was quietly rewritten instead.

    The vocabulary is compared against the source rather than against a written
    count, which makes it the second claim this file carries: an `fts5vocab` view
    reads the index rather than the content table, so it sees what the index
    *means*. The merge changes how the postings are stored and must change
    nothing that view reports -- the structural counterpart of the response
    equality `test_purged_build_structure.py` holds over the same fix.
    """
    target = tmp_path / "purged.sqlite"
    before = _vocabulary(planted_build)
    SqliteIndexStore(planted_build).derive_purged(
        target, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    with closing(sqlite3.connect(read_only_uri(target), uri=True)) as connection:
        note = connection.execute(
            f"SELECT note FROM {NAME_DECOY} WHERE id = 1"  # noqa: S608 - module-owned literals
        ).fetchone()
    assert note is not None and note[0] == "untouched", (
        f"{NAME_DECOY} is an ordinary table whose name merely ends the way the shipped "
        f"full-text tables' names do; the merge must key on what a table declares itself to "
        f"be, not on what it is called"
    )

    assert before, (
        f"the control must move: {VOCAB_DECOY} reports nothing over the source build, so the "
        f"equality below is two empty lists and says nothing about the merge"
    )
    assert _vocabulary(target) == before, (
        f"the merge changed what {PLANTED}'s index reports about itself. Collapsing segments "
        f"is supposed to move posting data, not vocabulary: a term, its document count or its "
        f"instance count moving means the merge dropped or duplicated postings that survive"
    )


def test_a_published_purge_is_already_merged_so_re_merging_finds_nothing_to_do(
    planted_build: Path, tmp_path: Path
) -> None:
    """The merge is the *last* full-text write a purge makes (#499).

    `purge_into` writes to the full-text indexes three times: the delete, then
    any forest re-derivation, then `_restamp`'s `UPDATE nodes SET index_build_id`
    -- which fires `nodes_fts_update` and `nodes_trigram_update` once per
    surviving node, on every purge, including one that withdraws nothing. A merge
    placed before the last of those publishes a build carrying whatever the ones
    after it wrote, and issue #499 sketched exactly that placement (inside
    `_delete`).

    Stated as idempotence rather than as an ordering, so it holds against a
    `purge_into` rearranged in any way at all: merge the published build again
    and no table may give anything up. A step added between the merge and
    `os.replace` fails this the day it lands, which an assertion naming today's
    three writers would not.

    Both controls are asserted first. Every table must be carrying posting data,
    or "nothing to give up" is a table with nothing in it; and the four the schema
    ships plus the planted fifth must all be present, or the sweep is not sweeping
    what :func:`_full_text_tables` claims.
    """
    target = tmp_path / "purged.sqlite"
    SqliteIndexStore(planted_build).derive_purged(
        target, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    tables = _full_text_tables(target)
    assert set(tables) == {
        "chunks_fts",
        "chunks_trigram",
        "nodes_fts",
        "nodes_trigram",
        PLANTED,
    }, f"the sweep must cover every full-text table the build carries; it found {tables}"

    published = {table: _posting_bytes(target, table) for table in tables}
    assert all(published.values()), (
        f"the control must move: a table holding no postings gives nothing up on a re-merge "
        f"whatever the purge did, so it cannot fail this. bytes={published}"
    )

    with closing(sqlite3.connect(target)) as connection:
        for table in tables:
            with connection:
                connection.execute(
                    f'INSERT INTO "{table}"("{table}") VALUES (\'optimize\')'  # noqa: S608 - identifier from this build's own schema
                )

    remerged = {table: _posting_bytes(target, table) for table in tables}
    assert all(remerged[table] >= published[table] * REMERGE_TOLERANCE for table in tables), (
        f"re-merging the published build shrank a full-text index, so the purge published one "
        f"that was not fully merged: something wrote to it after the merge ran. "
        f"published={published} remerged={remerged}"
    )
