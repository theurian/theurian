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
makes that choice load-bearing rather than incidental: it plants FTS5 tables that
no constant in the codebase names, and requires the purge to merge every one of
them. Measured 2026-09-03 by replacing the discovery with a tuple of the four
shipped names: the first test below goes RED, and `test_purged_build_structure.py`
stays green on all six of its own.

**Being in `sqlite_master` is not the same as being spelled the way the schema
spells it.** `USING "fts5"`, `USING [fts5]`, `` USING `fts5` `` and
`USING 'fts5'` are all accepted by SQLite and all name the same module (measured,
3.47.1), so :data:`QUOTED_MODULE` is a perfectly ordinary FTS5 table that a
reading keyed on the bare token walks straight past, leaving 44,409 bytes of
tombstones behind it. :data:`ODD_NAME` is the identifier half of the same point:
a name carrying a double quote, which reaches the merge only if it doubles the
quote when it builds the statement -- undoubled it is
`near "name_fts": syntax error`, not a silent mis-target.

**And the reading has to be narrow, because a purge is all-or-nothing: anything
it raises on unlinks the build.** Three decoys stand for three ways a loose
reading raises, each message measured on 3.47.1:

- :data:`VOCAB_DECOY`, an `fts5vocab` view, which a `LIKE '%fts5%'` reading takes
  and which is not writable -- `INSERT INTO v(v) VALUES ('optimize')` against one
  answers `table planted_vocab may not be modified`;
- :data:`NAME_DECOY`, a plain table whose *name* ends in `_fts`, which a
  name-keyed reading takes and which answers `table pretend_fts has no column
  named pretend_fts`;
- :data:`NAME_IN_QUOTES_DECOY`, an `fts5vocab` view *named* `x USING fts5`. This
  one is legal SQL and the more interesting of the three, because the pattern
  that shipped in this PR's first commit matched it -- on the table's own name,
  through a `.+?` that crossed into the quotes -- so a build carrying it was a
  build the purge destroyed. It is why the name portion is now `[^(]+?`.

Under each loose reading the purge raises instead of returning, so the purge
completing is itself the assertion; all three are checked intact afterwards,
since a merge that silently rewrote one would be worse than a merge that refused.

The last claim is *when* rather than which: a published purge must already be
merged, which is checked by merging it again and finding nothing to remove. That
is what makes the call's position in `purge_into` load-bearing rather than
arbitrary, and it is the one property the structural file cannot see -- the
restamp residue a mistimed merge leaves is bounded by the surviving node count,
and on that file's corpus it lands at 1.77x, under its two-sided 2.0x band.
Measured 2026-09-03 **on this file's own fixture**: with the merge issued before
`_restamp` instead of after, re-merging a published build takes `nodes_fts`
9,844 -> 4,929 bytes and `nodes_trigram` 94,831 -> 47,425; with the shipped
ordering every table is unchanged to the byte.

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

#: The same thing, declared `USING "fts5"`. A legal spelling of the same module,
#: and the one a reading keyed on the bare token misses.
QUOTED_MODULE: Final = "quoted_fts"

#: The same thing again, under a name containing a double quote. Reached only by a
#: merge that doubles the quote when it builds its statement.
ODD_NAME: Final = 'odd"name_fts'

#: The three planted tables that carry tombstones and must all come back merged.
#: Swept together, so a reading that handles one spelling and not another shows up
#: as the spelling it dropped rather than as a green run.
MUST_MERGE: Final = (PLANTED, QUOTED_MODULE, ODD_NAME)

#: Matches a `LIKE '%fts5%'` reading of `sqlite_master.sql` and is read-only, so
#: a merge that took it would refuse every build that carried one.
VOCAB_DECOY: Final = "planted_vocab"

#: An ordinary table whose name ends the way the shipped FTS5 tables' names do.
NAME_DECOY: Final = "pretend_fts"

#: An `fts5vocab` view whose *name* contains a module declaration. Legal SQL, and
#: the shape that a name-portion of `.+?` reads as a declaration of its own.
NAME_IN_QUOTES_DECOY: Final = "x USING fts5"

#: The three that must come back untouched.
DECOYS: Final = (VOCAB_DECOY, NAME_DECOY, NAME_IN_QUOTES_DECOY)

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

#: How many times the survivors-only reference a *stale* plant must weigh before
#: any collapse below is allowed to mean anything. Measured 2026-09-03 at 15.40x
#: (44,409 bytes against 2,883), so four is decisive with 3.8x of headroom while
#: still failing outright on a fixture that planted no residue.
RESIDUE_FACTOR: Final = 4.0

#: How far a *merged* plant may sit from that same reference, either way. Measured
#: at 1.00x -- a merged single segment over the survivors is the same object the
#: reference is. Two-sided because a ceiling alone is satisfied by a merge that
#: dropped postings of rows that survive, which the live-row count cannot see.
MERGED_FACTOR: Final = 1.5


def _quoted(identifier: str) -> str:
    """A SQL identifier, quoted the way the merge under test quotes one.

    Used by this file's own readers as well as its plants, because
    :data:`ODD_NAME` is unreadable without it -- which is the same reason
    `_merge_full_text` needs it, arrived at from the other side.
    """
    return '"{}"'.format(identifier.replace('"', '""'))


def _live_rows(path: Path, table: str) -> int:
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        return int(
            connection.execute(f"SELECT count(*) FROM {_quoted(table)}").fetchone()[0]  # noqa: S608 - module-owned literals
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
                f"SELECT coalesce(sum(length(block)), 0) FROM {_quoted(table + '_data')}"  # noqa: S608 - module-owned literals
            ).fetchone()[0]
        )


def _full_text_tables(path: Path) -> list[str]:
    """Every FTS5 table in the file, found by its storage rather than by its DDL.

    Independent of the production reading on purpose, and independent in *kind*
    rather than merely in wording: a table is an FTS5 index here iff it owns the
    `<name>_data` and `<name>_config` shadow tables FTS5 creates for one. Nothing
    about that consults the `CREATE` statement, so it cannot inherit the blind
    spot a text pattern has -- which is not hypothetical, because the first
    version of this helper keyed on the substring `fts5(` and so missed
    :data:`QUOTED_MODULE` in exactly the way `_FTS5_DECLARATION` did. Two
    predicates sharing a blind spot agree with each other and with nothing else.

    `_data` and `_config` together rather than either alone: both are created for
    every FTS5 table whatever its options, where `_docsize` is absent under
    `columnsize=0` and `_content` under external content -- so this counts four
    shadow tables for the schema's own and five for a contentless plant.
    """
    with closing(sqlite3.connect(read_only_uri(path), uri=True)) as connection:
        names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    return sorted(name for name in names if f"{name}_data" in names and f"{name}_config" in names)


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


def _tombstone(connection: sqlite3.Connection, table: str, *, declaration: str) -> None:
    """Create `table`, fill it, and delete all but the survivors.

    Deleted rather than never inserted, because what the merge removes is a
    *tombstone*: FTS5 answers a `DELETE` by appending a delete marker and leaves
    the row's posting list in the segment structure until something merges it.

    `declaration` is how the module name is spelled, which is the variable these
    plants exist to sweep.
    """
    name = _quoted(table)
    with connection:
        connection.execute(f"CREATE VIRTUAL TABLE {name} USING {declaration}(text)")
        connection.executemany(
            f"INSERT INTO {name}(rowid, text) VALUES (?, ?)",  # noqa: S608 - module-owned literals
            [(n, f"{BODY} planted row {n}.") for n in range(PLANTED_ROWS)],
        )
    with connection:
        connection.executemany(
            f"DELETE FROM {name} WHERE rowid = ?",  # noqa: S608 - module-owned literals
            [(n,) for n in range(PLANTED_SURVIVORS, PLANTED_ROWS)],
        )


def _plant(path: Path) -> None:
    """Add the three tables the merge must reach and the three it must not touch."""
    with closing(sqlite3.connect(path)) as connection:
        # The three spellings, all naming the same module. `"fts5"` and the
        # quote-carrying name are the two a reading can be right about the schema
        # and still get wrong.
        _tombstone(connection, PLANTED, declaration="fts5")
        _tombstone(connection, QUOTED_MODULE, declaration='"fts5"')
        _tombstone(connection, ODD_NAME, declaration="fts5")
        with connection:
            connection.execute(
                f"CREATE VIRTUAL TABLE {VOCAB_DECOY} USING fts5vocab({PLANTED}, row)"
            )
            # Legal, and the shape a `.+?` name portion reads as a declaration:
            # the module text sits inside the table's own quoted name.
            connection.execute(
                f"CREATE VIRTUAL TABLE {_quoted(NAME_IN_QUOTES_DECOY)} "
                f"USING fts5vocab({PLANTED}, row)"
            )
            connection.execute(f"CREATE TABLE {NAME_DECOY} (id INTEGER PRIMARY KEY, note TEXT)")
            connection.execute(
                f"INSERT INTO {NAME_DECOY} (id, note) VALUES (1, 'untouched')"  # noqa: S608 - module-owned literals
            )


@pytest.fixture
def survivors_only(tmp_path: Path) -> int:
    """Posting bytes of a table built over the survivors and nothing else.

    **The reference a merged table is compared against**, and the reason the
    comparison means anything. The earlier version of this file compared a purged
    table against a fraction of the *stale* table's own size, which reduces to
    `before > before * 0.4` -- true of every file, including one with no residue
    in it at all. A separately-built table holding only the rows that survive is
    an independent quantity: it is what :data:`PLANTED` would weigh if the deleted
    rows had never been written, so a purged table matching it is the two-corpus
    equality the whole PR turns on, asked of one table.

    Measured 2026-09-03: 2,883 bytes, against 44,409 for the stale table.
    """
    path = tmp_path / "survivors-only.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"CREATE VIRTUAL TABLE {PLANTED} USING fts5(text)")
        connection.executemany(
            f"INSERT INTO {PLANTED}(rowid, text) VALUES (?, ?)",  # noqa: S608 - module-owned literals
            [(n, f"{BODY} planted row {n}.") for n in range(PLANTED_SURVIVORS)],
        )
    return _posting_bytes(path, PLANTED)


@pytest.fixture
def planted_build(tmp_path: Path) -> Path:
    """A build carrying three tombstoned plants and three decoys."""
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
    planted_build: Path, survivors_only: int, tmp_path: Path
) -> None:
    """The merge covers every FTS5 table in the file, whatever it is called or how it is spelled.

    A purge with an empty withdrawal list, which is the cheapest way to reach the
    merge: it removes nothing, so any movement in a planted table's posting bytes
    is the merge and not the delete.

    Each of :data:`MUST_MERGE` is compared against `survivors_only` -- a table
    built over the surviving rows alone, in its own file. That is what makes the
    claim falsifiable: a purged table must come back weighing what it would have
    weighed if the deleted rows had never been written, which is a quantity this
    file cannot reach by rearranging the stale table's own size.

    The controls are asserted first. The stale table must be several times the
    reference, or there is no residue and the collapse proves nothing; and the
    live rows must survive, or the collapse is the table being emptied.
    """
    stale = {table: _posting_bytes(planted_build, table) for table in MUST_MERGE}

    target = tmp_path / "purged.sqlite"
    removed = SqliteIndexStore(planted_build).derive_purged(
        target, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert removed == 0, (
        f"this purge withdraws nothing, so any movement in {MUST_MERGE} is the merge; "
        f"it reported {removed} rows removed"
    )
    assert all(stale[table] >= survivors_only * RESIDUE_FACTOR for table in MUST_MERGE), (
        f"the control must move: {PLANTED_ROWS - PLANTED_SURVIVORS} of {PLANTED_ROWS} rows were "
        f"deleted from each plant, so each is supposed to be carrying their tombstoned postings "
        f"-- several times what the {PLANTED_SURVIVORS} survivors weigh on their own. If it is "
        f"not, there is no residue here and the collapse below proves nothing. "
        f"stale={stale} survivors_only={survivors_only}"
    )

    for table in MUST_MERGE:
        assert _live_rows(target, table) == PLANTED_SURVIVORS, (
            f"{table} must still hold its {PLANTED_SURVIVORS} live rows; a merge that shed them "
            f"would satisfy the collapse below by emptying the table"
        )

    purged = {table: _posting_bytes(target, table) for table in MUST_MERGE}
    assert all(
        survivors_only / MERGED_FACTOR <= purged[table] <= survivors_only * MERGED_FACTOR
        for table in MUST_MERGE
    ), (
        f"a purged build left a planted table holding its tombstones, so the merge did not reach "
        f"it. Either it works from a list of the tables the schema shipped when it was written "
        f"rather than from the build in front of it, or it reads the declaration in a way that "
        f"only one spelling of `fts5` satisfies. "
        f"purged={purged} survivors_only={survivors_only} stale={stale}"
    )


def test_the_purge_leaves_the_read_only_and_lookalike_tables_alone(
    planted_build: Path, tmp_path: Path
) -> None:
    """The reading is narrow enough not to destroy the build it is merging.

    Each of :data:`DECOYS` raises when a merge issues `optimize` against it, and a
    purge that raises unlinks its output -- so `derive_purged` returning at all is
    the assertion. What follows checks that none was quietly rewritten instead.

    :data:`NAME_IN_QUOTES_DECOY` is the one that was not hypothetical. It is a
    legal `fts5vocab` view whose name happens to contain a module declaration, and
    the pattern this PR first shipped matched it -- so the merge issued `optimize`
    against a read-only table, which raised, which unlinked the build. Measured
    2026-09-03 before the name portion was narrowed from `.+?` to `[^(]+?`.

    The vocabulary is compared against the source rather than against a written
    count, which makes it the second claim here: an `fts5vocab` view reads the
    index rather than the content table, so it sees what the index *means*. The
    merge changes how the postings are stored and must change nothing that view
    reports -- the structural counterpart of the response equality
    `test_purged_build_structure.py` holds over the same fix.
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
        survived = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert note is not None and note[0] == "untouched", (
        f"{NAME_DECOY} is an ordinary table whose name merely ends the way the shipped "
        f"full-text tables' names do; the merge must key on what a table declares itself to "
        f"be, not on what it is called"
    )
    assert set(DECOYS) <= survived, (
        f"a purge must carry every decoy through untouched, not merely decline to merge it: "
        f"missing {sorted(set(DECOYS) - survived)}"
    )
    assert not set(DECOYS) & set(_full_text_tables(target)), (
        "a decoy was counted as a full-text table by this file's own predicate, so the sweep "
        "and the production reading are being compared over the wrong population"
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

    Measured 2026-09-03 on this fixture with the merge issued before `_restamp`
    instead of after: `nodes_fts` gives up 9,844 -> 4,929 bytes on the re-merge
    and `nodes_trigram` 94,831 -> 47,425, while every other table is unchanged.
    With the shipped ordering nothing moves at all.

    Both controls are asserted first. Every table must be carrying posting data,
    or "nothing to give up" is a table with nothing in it; and the four the schema
    ships plus all three plants must be present, or the sweep is not sweeping what
    :func:`_full_text_tables` claims.
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
        *MUST_MERGE,
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
                    f"INSERT INTO {_quoted(table)}({_quoted(table)}) VALUES ('optimize')"  # noqa: S608 - identifier from this build's own schema
                )

    remerged = {table: _posting_bytes(target, table) for table in tables}
    assert all(remerged[table] >= published[table] * REMERGE_TOLERANCE for table in tables), (
        f"re-merging the published build shrank a full-text index, so the purge published one "
        f"that was not fully merged: something wrote to it after the merge ran. "
        f"published={published} remerged={remerged}"
    )
