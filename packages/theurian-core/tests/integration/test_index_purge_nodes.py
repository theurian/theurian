"""Node-graph purge behaviour that needs its own DDL beyond a chunk's (RAPTOR CL 3, round 2).

Split out of `test_index_purge.py`, which already exceeds the 800-line house
guidance on its own -- these tests do not extend the chunk-purge property that
file's docstring describes; they are about shapes only a `node_derivation`
graph can take: a node reachable from nothing but itself, provenance that
outlives its own revision stamp, and storage (`node_embeddings`,
`nodes_trigram`) that mirrors the chunk tables but has its own cascade and
isolation obligations to prove.

**What "doomed" means here is well-founded reachability.** A node survives only
if *every* derivation path below it terminates at a surviving chunk in finitely
many steps; anything else -- no edges, an edge to a withdrawn or missing chunk,
an edge to a missing node, a provenance cycle, or a `source_revision_id` naming
a withdrawn revision -- goes with the withdrawal. The reading these tests were
written against seeded on an *unprovenanced* node and walked forward from a
withdrawn chunk, which left every one of those shapes standing: a node with
edges is never a seed, and a node reachable from nothing withdrawn is never
reached. Measured by a differential over 400 random graphs against a
well-founded reference: that reading diverged on 91 of them, every divergence
cycle-reachable, and on 11 once the schema's self-edge `CHECK` had removed the
smallest cycle from the population. The one under test diverges on none.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.infrastructure.sqlite.index_purge import IndexPurgeError, _verify
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT = "demo"


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


@pytest.fixture
def store(tmp_path: Path) -> SqliteIndexStore:
    """Two chunks: one that will be withdrawn, one that survives every test here."""
    built = SqliteIndexStore(tmp_path / "theurian-index-stale.sqlite")
    built.create(index_build_id="01K1STALE", state_hash="state-abc")
    built.add_chunks(
        [
            _indexable("keep#0", "an ordinary retention paragraph", revision="keep"),
            _indexable("gone#0", "the quarantine ledger names incident zeta", revision="gone"),
        ]
    )
    return built


def _node(
    connection: sqlite3.Connection,
    node_id: str,
    *,
    text: str = "a summary",
    source_revision_id: str = "",
    index_build_id: str = "01K1STALE",
) -> None:
    """A raw `nodes` row, written the way RAPTOR would (ADR-0008 decision 5).

    Local to this file rather than shared with `test_index_purge.py`'s
    `_add_node`: that helper writes its edges in the same call, and every test
    below needs to construct edge shapes (self edges, cycles, dangling
    references) `_add_node`'s `sources`/`node_sources` keywords cannot express.
    """
    connection.execute(
        "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
        "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
        "embedding_model_revision, embedding_dimension, source_revision_id, "
        "index_build_id, project_id, sensitivity, status) "
        "VALUES (?, 'tree-abc', 1, 'document', ?, 'deadbeef', '', '', '', '', '', 0, ?, ?, ?, "
        "'internal', 'approved')",
        (node_id, text, source_revision_id, index_build_id, PROJECT),
    )


def _edge(
    connection: sqlite3.Connection,
    node_id: str,
    *,
    chunk: str | None = None,
    node: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) VALUES (?, ?, ?)",
        (node_id, chunk, node),
    )


def _surviving_nodes(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        return {str(row[0]) for row in connection.execute("SELECT node_id FROM nodes")}


def _node_terms(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.nv USING fts5vocab('main', 'nodes_fts', 'row')"
        )
        return {
            str(row[0]): int(row[1]) for row in connection.execute("SELECT term, cnt FROM temp.nv")
        }


def _node_trigrams(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.ntv "
            "USING fts5vocab('main', 'nodes_trigram', 'row')"
        )
        return {
            str(row[0]): int(row[1]) for row in connection.execute("SELECT term, cnt FROM temp.ntv")
        }


# The numbers in the section markers below ("-- N. ...") are round-2 review
# finding ids, not sequence positions in this file: 2, 3, 4, 8 and 10 are real
# findings from that round, closed without a fixture of their own here, so
# their absence is deliberate and not a gap to fill.


# -- 1. Cycles die like never-held --------------------------------------------


def _build_two_cycle(connection: sqlite3.Connection) -> None:
    _node(connection, "a#0")
    _node(connection, "b#0")
    _edge(connection, "a#0", node="b#0")
    _edge(connection, "b#0", node="a#0")


def _build_cycle_anchored_to_a_surviving_chunk(connection: sqlite3.Connection) -> None:
    _node(connection, "a#0")
    _node(connection, "b#0")
    _edge(connection, "a#0", chunk="keep#0")
    _edge(connection, "a#0", node="b#0")
    _edge(connection, "b#0", node="a#0")


#: The smallest cycle -- a node naming itself as its own source -- is absent on
#: purpose: `node_derivation`'s `CHECK (source_node_id IS NULL OR source_node_id
#: <> node_id)` refuses that row outright, so the case cannot be constructed
#: here. What the schema now forbids is pinned by `test_index_schema_v4.py::
#: test_a_node_derivation_edge_naming_itself_is_refused` instead. The two shapes
#: below name no node as its own source and are unaffected by that `CHECK`.
_CYCLE_SHAPES: dict[str, tuple[Callable[[sqlite3.Connection], None], int]] = {
    "two-cycle": (_build_two_cycle, 2),
    "cycle-anchored-to-a-surviving-chunk": (_build_cycle_anchored_to_a_surviving_chunk, 2),
}


@pytest.mark.parametrize("label", list(_CYCLE_SHAPES))
def test_a_cycle_is_purged_like_a_node_never_indexed(
    tmp_path: Path,
    store: SqliteIndexStore,
    label: str,
) -> None:
    """A node reachable only through itself is grounded in nothing, however many steps a walk gets.

    Both shapes withdraw *nothing* -- `revision_ids=[]` -- because being
    ungrounded is not a consequence of any particular withdrawal; it is a
    property of the graph. The second shape is the one that separates this from
    a withdrawal-taint bug: `a#0` also derives from `keep#0`, a chunk that
    survives every purge in this file, and it must still go, because one of
    its declared sources (the cycle through `b#0`) can never be shown to
    terminate at a surviving chunk -- a summary cannot be partially grounded
    any more than it can be partially withdrawn.
    """
    build_edges, expected_removed = _CYCLE_SHAPES[label]
    build = tmp_path / f"theurian-index-{label}.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            build_edges(connection)

    purged = tmp_path / "theurian-index-purged.sqlite"
    removed = SqliteIndexStore(build).derive_purged(
        purged, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    surviving = _surviving_nodes(purged)
    assert not surviving, (
        f"a cycle with no well-founded path to a surviving chunk must be purged like a node "
        f"that was never indexed; these survived: {sorted(surviving)}"
    )
    assert removed == expected_removed, f"expected {expected_removed} row(s) removed, got {removed}"


# -- 5. `source_revision_id` is a seed of its own -----------------------------


def test_a_node_stamped_with_a_withdrawn_revision_is_doomed_even_when_grounded(
    tmp_path: Path, store: SqliteIndexStore
) -> None:
    """`nodes.source_revision_id` is read by two purge predicates now: `_UNANCHORED_NODES`'s
    first arm, which dooms the node, and `_verify`'s `_WITHDRAWN_ROWS` post-condition, which
    would refuse to publish a build that still held it.

    ADR-0008 decision 5's fourteen provenance columns exist so a summary whose
    build inputs have moved on is detectably stale; `source_revision_id` names
    the revision the node's *own content* was built against, independent of
    which chunk ids its `node_derivation` edges happen to still name. Measured
    before the stamp became an arm of `_UNANCHORED_NODES`: a node stamped with a
    withdrawn revision, whose only edge points at a chunk that survives, is
    neither unprovenanced nor reachable from a doomed chunk, so the seeded
    traversal kept it -- and its text, built against the withdrawn state,
    survived the purge intact and stayed findable through `nodes_fts`.
    """
    build = tmp_path / "theurian-index-stamped.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "stamped#0",
                text="a summary built against the withdrawn state",
                source_revision_id="gone",
            )
            _edge(connection, "stamped#0", chunk="keep#0")

    purged = tmp_path / "theurian-index-purged.sqlite"
    SqliteIndexStore(build).derive_purged(
        purged, revision_ids=["gone"], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert "stamped#0" not in _surviving_nodes(purged), (
        "a node built against a withdrawn revision must go with it, even when its "
        "node_derivation edges all still name surviving chunks"
    )


# -- 6. A dangling-only build is purged, not silently skipped -----------------


def test_a_dangling_only_build_is_purged_rather_than_refused(
    tmp_path: Path, store: SqliteIndexStore
) -> None:
    """A source whose only damage is a pre-existing dangling edge, purged directly.

    `test_withdrawal_purge.py::test_a_dangling_edge_is_seen_by_the_pre_check_
    and_purged` pins the pre-check half of this gap; this pins what `derive_
    purged` itself does when called directly, bypassing the pre-check. Under the
    seeded traversal this build had no answer at all: the node was not a seed and
    nothing reached it, so `_delete` left it and `_verify`'s dangling check then
    refused to publish the whole build over the one bad row -- measured, with the
    pre-check calling the same build clean. Under well-founded reachability a
    node with an edge that resolves to nothing is exactly as ungrounded as one in
    a cycle -- not reachable to a surviving chunk in finitely many steps -- so it
    is removed and the build publishes, like any other ungrounded node.
    """
    build = tmp_path / "theurian-index-dangling-only.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        with connection:
            _node(connection, "orphaned-edge#0", text="a summary whose source chunk is gone")
            _edge(connection, "orphaned-edge#0", chunk="ghost#0")

    purged = tmp_path / "theurian-index-purged.sqlite"
    removed = SqliteIndexStore(build).derive_purged(
        purged, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert removed == 1, "the ungrounded node must be swept and counted, not refused"
    assert "orphaned-edge#0" not in _surviving_nodes(purged)


# -- 7. `node_embeddings`, mirroring `embeddings`'s verify and cascade --------


def test_verify_refuses_a_build_that_still_holds_an_orphaned_node_embedding(
    tmp_path: Path, store: SqliteIndexStore
) -> None:
    """The node-table mirror of `test_the_post_condition_also_refuses_an_orphaned_embedding`.

    Constructed the way a pragma-less delete would leave the file: the node
    gone, its vector behind. Before the fix this was RED for lack of a table to
    insert into, not only for lack of a check -- `node_embeddings` did not exist,
    so a summary had nowhere to keep a vector at all.
    """
    build = tmp_path / "theurian-index-orphan-node-embedding.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO node_embeddings (node_id, dimension, vector) VALUES ('ghost#0', 3, X'00')"
        )

    with pytest.raises(IndexPurgeError, match="whose node is gone"):
        _verify(build, [])


def test_a_purge_cascades_node_embeddings_when_the_node_dies(
    tmp_path: Path, store: SqliteIndexStore
) -> None:
    """`node_embeddings.node_id` must `REFERENCES nodes(node_id) ON DELETE CASCADE`.

    Mirrors `test_a_purged_build_holds_no_embedding_of_a_withdrawn_chunk`: a
    doomed node's vector must not survive a purge that removes the node, or
    dense search over a forest would find the ghost of a summary whose text is
    gone.
    """
    build = tmp_path / "theurian-index-doomed-node-embedding.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "doomed#0")
            _edge(connection, "doomed#0", chunk="gone#0")
            connection.execute(
                "INSERT INTO node_embeddings (node_id, dimension, vector) VALUES "
                "('doomed#0', 1, X'0000803f')"
            )

    purged = tmp_path / "theurian-index-purged.sqlite"
    SqliteIndexStore(build).derive_purged(
        purged, revision_ids=["gone"], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    with closing(sqlite3.connect(purged)) as connection:
        remaining = connection.execute("SELECT count(*) FROM node_embeddings").fetchone()[0]
    assert remaining == 0, "a doomed node's embedding survived the purge that removed its node"


# -- 9. `_restamp` must reach surviving node rows, not only `index_metadata` --


def test_restamp_updates_survivors_index_build_id_too(
    tmp_path: Path, store: SqliteIndexStore
) -> None:
    """`_restamp` gives the purged build a new identity -- and so must its rows.

    Measured before `_restamp` reached the node table: it wrote the new build id
    into `index_metadata` only. `nodes.index_build_id` is one of ADR-0008
    decision 5's fourteen provenance columns, and a surviving node whose own row
    still names the build it was copied *from* disagrees with the pointer that
    now names the build it belongs to -- exactly the disagreement `_restamp`
    exists to prevent at the file level, one column short of covering the row.
    """
    build = tmp_path / "theurian-index-stale.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "survivor#0", index_build_id="01K1STALE")
            _edge(connection, "survivor#0", chunk="keep#0")

    purged = tmp_path / "theurian-index-purged.sqlite"
    SqliteIndexStore(build).derive_purged(
        purged, revision_ids=["gone"], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    with closing(sqlite3.connect(purged)) as connection:
        node_build_id = connection.execute(
            "SELECT index_build_id FROM nodes WHERE node_id = 'survivor#0'"
        ).fetchone()[0]
    assert node_build_id == "01K1PURGED", (
        f"the surviving node still names the build it was copied from: {node_build_id!r}"
    )


# -- 11. Dangling `source_node_id`, the mutation-11 pin -----------------------


def test_verify_refuses_a_build_whose_node_derivation_points_at_a_node_that_is_gone(
    tmp_path: Path, store: SqliteIndexStore
) -> None:
    """`_DANGLING_NODE_DERIVATION`'s other disjunct, for `source_node_id`.

    `test_index_purge.py::test_verify_refuses_a_build_whose_node_derivation_
    points_at_a_chunk_that_is_gone` covers `source_chunk_id`; mutation showed
    the `source_node_id` half survives the whole suite when dropped
    (`11-dangling-drop-source-node-half`) -- no test had ever put a node into
    the state that disjunct exists to catch. Constructed the same way: `PRAGMA
    foreign_keys` off, so `ON DELETE CASCADE` never removes the edge when the
    node it names is deleted.
    """
    build = tmp_path / "theurian-index-dangling-node.sqlite"
    build.write_bytes(store.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "parent#0")
            _node(connection, "child#0")
            _edge(connection, "parent#0", node="child#0")
            _edge(connection, "child#0", chunk="keep#0")

    with closing(sqlite3.connect(build)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM nodes WHERE node_id = 'child#0'")

    with pytest.raises(IndexPurgeError, match="whose source is gone"):
        _verify(build, [])


# -- 12. `nodes_fts` residue, the mutation-17 pin -----------------------------


def test_a_purged_builds_nodes_fts_leaves_no_residue(tmp_path: Path) -> None:
    """The node-table mirror of the module's own equality claim, over `nodes_fts`.

    `nodes_fts` is an external-content FTS5 table maintained by triggers,
    exactly like `chunks_fts` -- and nothing in `src/` queries it yet, so a
    broken `nodes_fts_delete` trigger is invisible to every other assertion
    this suite makes about nodes. Mutation showed it: dropping the trigger
    from the DDL leaves the whole suite green
    (`17-schema-drop-nodes-fts-delete-trigger`). Compared against a `fresh`
    build that never held the doomed node, term by term, rather than only
    checking the row is gone from `nodes` -- an FTS5 index can retain a
    deleted row's postings while the content table it is external to is clean.
    """
    stale = SqliteIndexStore(tmp_path / "theurian-index-stale.sqlite")
    stale.create(index_build_id="01K1STALE", state_hash="state-abc")
    stale.add_chunks(
        [
            _indexable("keep#0", "an ordinary retention paragraph", revision="keep"),
            _indexable("gone#0", "a paragraph about incident zeta", revision="gone"),
        ]
    )
    with closing(sqlite3.connect(stale.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "summary-of-gone#0",
                text="a summary mentioning quarantine ledger incident zeta",
            )
            _edge(connection, "summary-of-gone#0", chunk="gone#0")
            _node(connection, "summary-of-keep#0", text="a summary of the keeper")
            _edge(connection, "summary-of-keep#0", chunk="keep#0")

    fresh = SqliteIndexStore(tmp_path / "theurian-index-fresh.sqlite")
    fresh.create(index_build_id="01K1FRESH", state_hash="state-abc")
    fresh.add_chunks([_indexable("keep#0", "an ordinary retention paragraph", revision="keep")])
    with closing(sqlite3.connect(fresh.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "summary-of-keep#0", text="a summary of the keeper")
            _edge(connection, "summary-of-keep#0", chunk="keep#0")

    purged_path = tmp_path / "theurian-index-purged.sqlite"
    stale.derive_purged(
        purged_path, revision_ids=["gone"], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    purged_terms, fresh_terms = _node_terms(purged_path), _node_terms(fresh.path)
    assert purged_terms == fresh_terms, (
        f"nodes_fts in the purged build disagrees with one that never held the doomed node: "
        f"purged={purged_terms} fresh={fresh_terms}"
    )


def test_a_purged_builds_nodes_trigram_leaves_no_residue(tmp_path: Path) -> None:
    """The `nodes_trigram` mirror of `test_a_purged_builds_nodes_fts_leaves_no_residue`.

    `nodes_trigram` is a second external-content FTS5 table over `nodes`,
    maintained by its own trigger set (`nodes_trigram_insert`/`_delete`/
    `_update`, pinned to exist by `test_index_schema_v4.py::test_the_schema_
    carries_nodes_trigram`) -- entirely separate from `nodes_fts`'s. A correct
    `nodes_fts_delete` trigger says nothing about `nodes_trigram_delete`: they
    fire on the same `DELETE FROM nodes` but are two independent pieces of DDL,
    and nothing in `src/` queries `nodes_trigram` yet, so a broken delete
    trigger there is invisible to every other assertion this suite makes.
    Compared against a `fresh` build that never held the doomed node, trigram
    by trigram, for the same reason the `nodes_fts` version compares term by
    term: an FTS5 index can retain a deleted row's postings while the content
    table it is external to is clean.
    """
    stale = SqliteIndexStore(tmp_path / "theurian-index-stale.sqlite")
    stale.create(index_build_id="01K1STALE", state_hash="state-abc")
    stale.add_chunks(
        [
            _indexable("keep#0", "an ordinary retention paragraph", revision="keep"),
            _indexable("gone#0", "a paragraph about incident zeta", revision="gone"),
        ]
    )
    with closing(sqlite3.connect(stale.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "summary-of-gone#0",
                text="a summary mentioning quarantine ledger incident zeta",
            )
            _edge(connection, "summary-of-gone#0", chunk="gone#0")
            _node(connection, "summary-of-keep#0", text="a summary of the keeper")
            _edge(connection, "summary-of-keep#0", chunk="keep#0")

    fresh = SqliteIndexStore(tmp_path / "theurian-index-fresh.sqlite")
    fresh.create(index_build_id="01K1FRESH", state_hash="state-abc")
    fresh.add_chunks([_indexable("keep#0", "an ordinary retention paragraph", revision="keep")])
    with closing(sqlite3.connect(fresh.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "summary-of-keep#0", text="a summary of the keeper")
            _edge(connection, "summary-of-keep#0", chunk="keep#0")

    purged_path = tmp_path / "theurian-index-purged.sqlite"
    stale.derive_purged(
        purged_path, revision_ids=["gone"], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    purged_trigrams, fresh_trigrams = _node_trigrams(purged_path), _node_trigrams(fresh.path)
    assert purged_trigrams == fresh_trigrams, (
        f"nodes_trigram in the purged build disagrees with one that never held the doomed node: "
        f"purged={purged_trigrams} fresh={fresh_trigrams}"
    )


# -- 13. holds_any_revision agrees with whether a purge removes anything -----
#
# A deterministic, hand-enumerated set of graphs rather than a random
# generator: a random seed can miss a shape by luck, and each shape below
# exists to reach one specific branch of `_DOOMED` or `holds_any_revision`.
# **Each graph carries its own chunk corpus** rather than sharing one -- a
# shared corpus that always includes a `gone#0` chunk stamped `"gone"` would
# make the *chunk* clause of `holds_any_revision` answer `True` for every
# graph regardless of the node shape, which would make every case agree with
# `derive_purged` for the wrong reason and hide a broken node-arm entirely
# (found by mutation-testing this test itself: `18-holds-drops-the-node-
# union-arm` survived a first draft that shared one corpus across all ten
# cases). `edges` is `(node_id, "chunk"|"node", target)`.
_EQUIVALENCE_GRAPHS: dict[str, tuple[dict[str, str], list[str], list[tuple[str, str, str]]]] = {
    "no-chunks-no-nodes": ({}, [], []),
    "one-withdrawn-chunk-no-nodes": ({"gone#0": "gone"}, [], []),
    "node-from-a-withdrawn-chunk": (
        {"gone#0": "gone", "keep#0": "keep"},
        ["n0"],
        [("n0", "chunk", "gone#0")],
    ),
    "node-from-a-surviving-chunk-only": ({"keep#0": "keep"}, ["n0"], [("n0", "chunk", "keep#0")]),
    # No `gone#0` in this graph's own corpus at all: the only way
    # `holds_any_revision(["gone"])` can answer `True` here is the node
    # clause, which is exactly the branch `18-holds-drops-the-node-union-arm`
    # removes.
    "unprovenanced-zero-edges": ({"keep#0": "keep"}, ["n0"], []),
    "chain-below-an-unprovenanced-node": (
        {"keep#0": "keep"},
        ["n0", "n1"],
        [("n1", "node", "n0")],
    ),
    "mixed-withdrawn-and-surviving-parentage": (
        {"gone#0": "gone", "keep#0": "keep"},
        ["n0"],
        [("n0", "chunk", "gone#0"), ("n0", "chunk", "keep#0")],
    ),
    "diamond-from-a-withdrawn-chunk": (
        {"gone#0": "gone"},
        ["n0", "n1", "n2"],
        [
            ("n0", "chunk", "gone#0"),
            ("n1", "chunk", "gone#0"),
            ("n2", "node", "n0"),
            ("n2", "node", "n1"),
        ],
    ),
    # No withdrawn chunk and no unprovenanced node: the `False`/`removed == 0`
    # case that `19-holds-always-reports-true` breaks.
    "two-independent-healthy-roots": (
        {"keep#0": "keep", "keep1#0": "keep1"},
        ["n0", "n1"],
        [("n0", "chunk", "keep#0"), ("n1", "chunk", "keep1#0")],
    ),
    "long-chain-from-a-withdrawn-chunk": (
        {"gone#0": "gone"},
        [f"n{i}" for i in range(6)],
        [("n0", "chunk", "gone#0")] + [(f"n{i}", "node", f"n{i - 1}") for i in range(1, 6)],
    ),
}


def _build_graph(
    connection: sqlite3.Connection, nodes: list[str], edges: list[tuple[str, str, str]]
) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        for node_id in nodes:
            _node(connection, node_id)
        for node_id, kind, target in edges:
            if kind == "chunk":
                _edge(connection, node_id, chunk=target)
            else:
                _edge(connection, node_id, node=target)


@pytest.mark.parametrize("label", sorted(_EQUIVALENCE_GRAPHS))
def test_holds_any_revision_agrees_with_whether_a_purge_removes_anything(
    tmp_path: Path, label: str
) -> None:
    """`IndexStore.holds_any_revision`'s own docstring claim, pinned over ten graph shapes.

    `holds_any_revision`'s own docstring states that it runs `ANY_DOOMED_ROW`,
    built from the same `doomed_chunks` and `_UNANCHORED_NODES` literals
    `_DOOMED` is, so "would a purge remove anything" and "did a purge remove
    anything" cannot drift apart -- the property this test pins. None of these graphs
    contains a cycle or a dangling reference: those are the shapes the seeded
    traversal answered differently on the two sides, and each has its own test
    above (a dangling-only graph made `derive_purged` raise rather than answer at
    all, which is `test_a_dangling_only_build_is_purged_rather_than_refused`'s
    subject, not this one's). What this pins is the equivalence over the ten
    shapes where it held before and must go on holding, so a change that desyncs
    the two -- mutation showed `18-holds-drops-the-node-union-arm` and
    `19-holds-always-reports-true` do exactly that -- fails here immediately.
    """
    chunks, nodes, edges = _EQUIVALENCE_GRAPHS[label]
    path = tmp_path / f"theurian-index-{label}.sqlite"
    built = SqliteIndexStore(path)
    built.create(index_build_id="01K1B", state_hash="state-abc")
    if chunks:
        built.add_chunks(
            [
                _indexable(chunk_id, f"text of {chunk_id}", revision=revision)
                for chunk_id, revision in chunks.items()
            ]
        )
    with closing(sqlite3.connect(path)) as connection:
        _build_graph(connection, nodes, edges)

    holds = built.holds_any_revision(["gone"])
    removed = built.derive_purged(
        tmp_path / f"purged-{label}.sqlite",
        revision_ids=["gone"],
        index_build_id="01K1P",
        state_hash="state-abc",
    )

    assert holds == (removed > 0), (
        f"{label}: holds_any_revision()={holds} but derive_purged() removed={removed}"
    )
