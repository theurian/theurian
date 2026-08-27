"""Schema-v4 hardening: constraints closed after `nodes`/`node_derivation` shipped (RAPTOR CL 3).

Split out of `test_index_store.py`, which this span had pushed past 2,000
lines -- these tests do not extend that file's FTS5/scan claims; they pin
constraints `index_schema.py` gained once the v4 node tables existed and a
review pass over them found what a `TEXT` primary key and a multi-column
`UNIQUE` index quietly do not enforce. A mechanical move: no assertion below
differs from the one `test_index_store.py` carried; only the prose describing
what was and was not fixed at the time has been brought into the present tense.

`nodes.node_id` and `chunks.chunk_id` are `TEXT PRIMARY KEY`, and SQLite's
rowid tables only imply `NOT NULL` on an *integer* primary key -- a `TEXT` one
silently admitted a single `NULL` row (the "SQLite TEXT PK quirk") before both
columns were given an explicit `NOT NULL`. That mattered because
`index_purge`'s dangling and orphan checks are `NOT EXISTS` subqueries today,
but were written as `x NOT IN (SELECT ...)` when the gap below was found, and
SQL's `NOT IN` against a set containing one `NULL` answers `NULL` -- never
true -- for every row, not merely the `NULL` one. `node_derivation` had a
matching pair of gaps: its exclusive-source `CHECK` said nothing about a self
edge, and its `UNIQUE` index was declared over three columns two of which are
always mutually exclusive, so SQLite's "a `NULL` never equals another `NULL`"
rule kept it from ever firing. All four are closed in `index_schema.py` now --
the self edge by a second `CHECK`, the duplicate edge by two partial unique
indexes (`node_derivation_chunk_edge`, `node_derivation_node_edge`) in place of
the three-column one. What follows pins each fix as a regression: a schema
that quietly reverts to the behaviour these tests replace must fail here.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.sqlite.index_store import IndexableChunk, SqliteIndexStore

pytestmark = pytest.mark.integration

#: The disclosure grant every retriever call in this file runs under: all four
#: levels, which is what "this deployment serves everything" means once the
#: retrievers take the axis as a WHERE predicate (#119 phase 4). Spelled out
#: rather than read from ``StaticAuthorizationProvider``'s shipped default, which
#: a later phase narrows -- a file that inherited it would start withholding its
#: own fixtures silently, turning these tests into tests of something else.
EVERY_SENSITIVITY = frozenset(Sensitivity)


def _indexable(  # noqa: PLR0913 - one keyword per canonical field the filters read
    chunk_id: str,
    text: str,
    *,
    item: str = "architecture.auth",
    project: str = "demo",
    status: str = "approved",
    heading: str = "",
) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=heading),
        project_id=project,
        item_id=item,
        revision_id=f"rev-{chunk_id}",
        served_content_sha256=f"body-of-rev-{chunk_id}",
        status=status,
        sensitivity="internal",
        trust_level="reviewed",
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteIndexStore:
    store = SqliteIndexStore(tmp_path / "index" / "theurian-index-01.sqlite")
    store.create(index_build_id="01K1DXAA", state_hash="abc123")
    return store


def _node_row(*, node_id: object, text: str = "a summary", level: object = 1) -> tuple[object, ...]:
    """The 17-column `nodes` insert tuple. `node_id` free to be anything -- including `None` --
    and `level` free to be out of ADR-0008 decision 2's three-level range, for the tests below.
    """
    return (
        node_id,
        "tree-abc",
        level,
        "document",
        text,
        "deadbeef",
        "",
        "",
        "",
        "",
        "",
        0,
        "",
        "build-abc",
        "demo",
        "internal",
        "approved",
    )


_NODE_INSERT = (
    "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
    "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
    "embedding_model_revision, embedding_dimension, source_revision_id, "
    "index_build_id, project_id, sensitivity, status) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def test_node_id_and_chunk_id_refuse_a_null_value(store: SqliteIndexStore) -> None:
    """`TEXT PRIMARY KEY` does not imply `NOT NULL`; a `NULL` id must be refused explicitly.

    Only an `INTEGER PRIMARY KEY` is a rowid alias SQLite refuses `NULL` for on
    its own. `chunks.chunk_id` and `nodes.node_id` are both `TEXT PRIMARY KEY`,
    so before the explicit `NOT NULL` pinned here, each admitted exactly one
    `NULL` row -- past which every dangling and orphan check in `index_purge`
    would have gone silently inert: each is a `NOT EXISTS` subquery today, but
    was written as `x NOT IN (SELECT ...)` when this gap was found, and SQL's
    `NOT IN` against a set containing one `NULL` answers `NULL` (falsy) for
    every row, not merely for the `NULL` one. Explicit `NOT NULL` is what a
    rowid alias would have given for free.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(_NODE_INSERT, _node_row(node_id=None))
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO chunks (chunk_id, project_id, item_id, revision_id, "
                "served_content_sha256, ordinal, heading, text, token_estimate, status, "
                "sensitivity, trust_level) "
                "VALUES (NULL, 'demo', 'a.x', 'x', 'body-hash', 0, '', 'text', 1, 'approved', "
                "'internal', 'reviewed')"
            )


def test_a_node_derivation_edge_naming_itself_is_refused(store: SqliteIndexStore) -> None:
    """A node cannot be its own source (RAPTOR CL 3 round 2).

    `node_derivation`'s exclusive-source `CHECK` only says a row names exactly
    one of a chunk or a node -- it says nothing about *which* node, so
    `('n1', NULL, 'n1')` satisfied it before `node_derivation` gained a second
    `CHECK` refusing a self edge outright. A self edge is the smallest cycle
    the well-founded traversal has to refuse: a node reachable only through
    itself is grounded in nothing, however many steps the walk is given.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute(_NODE_INSERT, _node_row(node_id="n1"))

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', NULL, 'n1')"
            )


def test_a_node_level_outside_the_three_tree_levels_is_refused(store: SqliteIndexStore) -> None:
    """ADR-0008 decision 2 builds three tree levels, and the schema now says so.

    Document Tree, Domain Tree and Global Catalog Tree are the three levels
    decision 2 names, numbered 1 through 3 in every fixture this suite writes.
    Until `CHECK (level BETWEEN 1 AND 3)` landed, `nodes.level` was a bare
    `INTEGER NOT NULL`, so 0 or 4 went in exactly like 1 and a writer could
    invent a tier the forest does not have.

    **It bounds the column, not the graph, and this test claims nothing more.**
    `index_purge._CYCLIC_NODES`'s cost argument wants a shallow *derivation*
    graph, and `level` does not supply one: nothing ties an edge's endpoints to a
    level difference, so 2,000 nodes all at level 1 chained 2,000 deep satisfy
    this `CHECK` and take that closure 3.6 s (measured). The shallow shape is a
    property of `application/forest_builder.py`, which builds each tier only from
    the one below it -- not of this column, and not of any row written by
    something else.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(_NODE_INSERT, _node_row(node_id="n0", level=0))
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(_NODE_INSERT, _node_row(node_id="n4", level=4))


def test_the_node_derivation_check_refuses_both_and_neither_source(
    store: SqliteIndexStore,
) -> None:
    """The exclusive-source `CHECK`'s other two shapes, pinned directly.

    `sources=[...]` / `node_sources=[...]` fixtures elsewhere in the suite only
    ever exercise the two shapes the `CHECK` accepts (chunk-only, node-only).
    Mutation showed the whole `CHECK` can be replaced with `CHECK (1)` and the
    suite stays green (`15-schema-drop-exclusive-null-check`) -- nothing had
    ever tried to write the two shapes it exists to refuse: naming both a chunk
    and a node in one row, and naming neither.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute(
                "INSERT INTO chunks (chunk_id, project_id, item_id, revision_id, "
                "served_content_sha256, ordinal, heading, text, token_estimate, status, "
                "sensitivity, trust_level) "
                "VALUES ('c1#0', 'demo', 'a.x', 'x', 'body-hash', 0, '', 'text', 1, 'approved', "
                "'internal', 'reviewed')"
            )
            connection.execute(_NODE_INSERT, _node_row(node_id="n1"))

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', 'c1#0', 'n1')"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', NULL, NULL)"
            )


def test_duplicate_derivation_edges_are_refused(store: SqliteIndexStore) -> None:
    """An exact-duplicate edge, of either shape, must be refused at the schema.

    Before `node_derivation_chunk_edge` and `node_derivation_node_edge`
    existed, duplicate-edge refusal was a single `UNIQUE` index declared over
    all three columns `(node_id, source_chunk_id, source_node_id)`, and exactly
    one of the last two is always `NULL` per row (the exclusive-source
    `CHECK`). SQL's `UNIQUE` treats `NULL` as distinct from every other `NULL`,
    including itself, so a 3-column index where one column is always `NULL`
    never actually compared equal to anything -- the index accepted an
    unbounded number of identical rows. Measured: mutating that `UNIQUE INDEX`
    to a plain `INDEX` left the whole suite green
    (`16-schema-drop-unique-edge-index`), which is the same defect from the
    other side. The fix is two partial unique indexes,
    `node_derivation_chunk_edge` and `node_derivation_node_edge`, each `WHERE`
    on the source column that is never `NULL` in the rows it covers, in place
    of the three-column one.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute(
                "INSERT INTO chunks (chunk_id, project_id, item_id, revision_id, "
                "served_content_sha256, ordinal, heading, text, token_estimate, status, "
                "sensitivity, trust_level) "
                "VALUES ('c1#0', 'demo', 'a.x', 'x', 'body-hash', 0, '', 'text', 1, 'approved', "
                "'internal', 'reviewed')"
            )
            connection.execute(_NODE_INSERT, _node_row(node_id="n1"))
            connection.execute(_NODE_INSERT, _node_row(node_id="n2"))
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', 'c1#0', NULL)"
            )
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', NULL, 'n2')"
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', 'c1#0', NULL)"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES ('n1', NULL, 'n2')"
            )


def test_the_schema_carries_the_node_embeddings_table(store: SqliteIndexStore) -> None:
    """`node_embeddings`, mirroring `embeddings` for the dense retriever over nodes.

    A RAPTOR summary needs a vector the same way a chunk does, and `embeddings`
    cannot hold it: it is keyed on `chunk_id REFERENCES chunks`, and a node id
    is not a chunk id. Without its own table a summary's vector has nowhere to
    live and dense search over the forest cannot exist -- and without `ON
    DELETE CASCADE` a purged node's vector would outlive the node exactly as an
    orphaned `embeddings` row would.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "node_embeddings" in tables, (
            "node_embeddings is missing -- a RAPTOR summary has nowhere to store a vector"
        )
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(node_embeddings)")}
        assert set(columns) == {"node_id", "dimension", "vector"}, (
            f"node_embeddings carries the wrong columns: {sorted(columns)}"
        )
        assert columns["node_id"][5] == 1, (
            "node_id must be the primary key, as chunk_id is on embeddings"
        )
        foreign_keys = connection.execute("PRAGMA foreign_key_list(node_embeddings)").fetchall()
        assert any(
            row[2] == "nodes" and row[3] == "node_id" and row[6] == "CASCADE"
            for row in foreign_keys
        ), (
            f"node_embeddings.node_id must REFERENCES nodes(node_id) ON DELETE CASCADE: "
            f"{foreign_keys}"
        )


def test_the_schema_carries_nodes_trigram(store: SqliteIndexStore) -> None:
    """`nodes_trigram`, mirroring `chunks_trigram` so Japanese node text is searchable.

    `chunks_trigram` is what makes substring matching work for a script
    `unicode61` cannot segment (module docstring, `index_schema.py`). A summary
    written in Japanese needs the same index, kept apart from `chunks_trigram`
    for the identical reason `nodes_fts` is kept apart from `chunks_fts`: a
    summary's text repeats its children's terms, and a shared external-content
    table would move the surviving leaves' trigram statistics too.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'nodes_trigram'"
        ).fetchone()
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'nodes'"
            )
        }
    assert ddl is not None, "nodes_trigram is missing -- a Japanese summary is invisible to search"
    assert "content='nodes'" in ddl[0], (
        f"nodes_trigram is not external-content over nodes: {ddl[0]}"
    )
    assert 'tokenize="trigram"' in ddl[0] or "tokenize='trigram'" in ddl[0], (
        f"nodes_trigram is not trigram-tokenized: {ddl[0]}"
    )
    assert {"nodes_trigram_insert", "nodes_trigram_delete", "nodes_trigram_update"} <= triggers, (
        f"nodes_trigram is missing a sync trigger, and will drift from nodes: {sorted(triggers)}"
    )


def _trigram_score(store: SqliteIndexStore, query: str, *, chunk_id: str) -> float:
    page = store.search_substring(
        query,
        project_id="demo",
        limit=10,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    matches = [row.score for row in page.rows if row.chunk_id == chunk_id]
    assert matches, f"{chunk_id!r} did not match {query!r} -- the fixture cannot show isolation"
    return matches[0]


def _add_node(store: SqliteIndexStore, node_id: str, *, text: str) -> None:
    """A minimal node row, written the way RAPTOR would (ADR-0008 decision 5).

    Local to this module rather than shared: `test_index_purge.py` and
    `test_withdrawal_purge.py` each already keep their own row-insertion helper
    local to their own file (`_add_node`, `_insert_unprovenanced_node`), and
    the two tests here need nothing from theirs -- neither touches
    `node_derivation` at all. Defined ahead of both call sites below rather
    than between them, so a reader meets it before either use.
    """
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
            "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
            "embedding_model_revision, embedding_dimension, source_revision_id, "
            "index_build_id, project_id, sensitivity, status) "
            "VALUES (?, 'tree-abc', 1, 'document', ?, 'deadbeef', '', '', '', '', '', 0, '', "
            "'build-abc', 'demo', 'internal', 'approved')",
            (node_id, text),
        )


def test_a_node_row_does_not_move_a_leaf_chunks_trigram_score(store: SqliteIndexStore) -> None:
    """The trigram mirror of `test_a_node_row_does_not_move_a_leaf_chunks_bm25_score`.

    `chunks_trigram` scores every visible row against collection statistics
    computed over *every* row in `chunks`, exactly as `chunks_fts` does. Once
    `nodes_trigram` exists, a node's text must land there and nowhere near
    `chunks_trigram`, or a RAPTOR summary would reweight ordinary substring
    search the same way it would reweight ordinary word search.
    """
    store.add_chunks(
        [
            _indexable("c1", "retention and isolation are decided per namespace"),
            _indexable("c2", "authentication tokens rotate on restart"),
        ]
    )
    before = _trigram_score(store, "retention isolation", chunk_id="c1")

    _add_node(
        store,
        "node-1",
        text="retention isolation retention isolation retention isolation " * 20,
    )

    after = _trigram_score(store, "retention isolation", chunk_id="c1")
    assert after == before, (
        "inserting a node moved a leaf chunk's trigram score -- nodes_trigram and "
        "chunks_trigram are not isolated"
    )


def _bm25_score(store: SqliteIndexStore, query: str, *, chunk_id: str) -> float:
    page = store.search_lexical(
        query,
        project_id="demo",
        limit=10,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    matches = [row.score for row in page.rows if row.chunk_id == chunk_id]
    assert matches, f"{chunk_id!r} did not match {query!r} -- the fixture cannot show isolation"
    return matches[0]


def test_a_node_row_does_not_move_a_leaf_chunks_bm25_score(store: SqliteIndexStore) -> None:
    """A first, narrow instance of ADR-0008's owed "a forest does not move leaf
    ranking" test (decision 5's amendment; Compliance section, same name).

    `chunks_fts` scores every visible row against collection statistics computed
    over *every* row in `chunks` -- `N`, `avgdl`, and the per-term document
    frequencies. A RAPTOR summary systematically repeats the terms of the
    children it summarises, so a derived row sharing that table would move all
    three under an ordinary leaf query the caller never asked a node about. v4
    puts node text in `nodes`/`nodes_fts`, a table `chunks_fts` never reads, so
    inserting a node -- one whose text is built almost entirely from the leaf
    query's own terms, to make a shared-table leak as visible as possible --
    must leave a fixed chunk's own bm25 score exactly where it was.

    Narrower than the equality the ADR names as owed for the closing CL, which
    compares `N`, `avgdl` and the per-term document frequencies directly "out of
    the FTS5 tables" -- the ADR leaves the mechanism open on purpose. `fts5vocab`
    is this docstring's own choice of how to read those statistics out, not
    something the ADR names. This is the first instance: one corpus, one
    inserted node, one query, read through the real `search_lexical` path before
    and after.
    """
    store.add_chunks(
        [
            _indexable("c1", "retention and isolation are decided per namespace"),
            _indexable("c2", "authentication tokens rotate on restart"),
        ]
    )
    before = _bm25_score(store, "retention isolation", chunk_id="c1")

    _add_node(
        store,
        "node-1",
        text="retention isolation retention isolation retention isolation " * 20,
    )

    after = _bm25_score(store, "retention isolation", chunk_id="c1")
    assert after == before, (
        "inserting a node moved a leaf chunk's bm25 score -- nodes_fts and chunks_fts are not "
        "isolated, so a RAPTOR summary's text would reweight ordinary leaf search results"
    )
