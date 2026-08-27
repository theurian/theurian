"""`derive_purged` against a well-founded reference, over 400 random graphs (ADR-0024, T-17a).

`index_purge._UNANCHORED_NODES` states, but does not itself prove, that its
reading diverges from a well-founded reference on **none** of 400 random
graphs -- and that the traversal it replaced diverged on **91**, then on
**11** once the schema's self-edge `CHECK` removed the smallest cycle from the
population (142 of the 400 graphs' generated edges). This file is what makes
that last number, the one the shipped code is graded on, re-derivable rather
than merely asserted in a comment.

**The two numbers this test can still move, and what would move them.** The
91 and the 11 belong to a traversal this codebase no longer has -- it seeded
on an unprovenanced node and walked forward from a withdrawn chunk, which is
not what `_DOOMED` computes today -- so they are recorded here as the
lineage's history, not re-derived; nothing in `src/` could run that reading to
check them against. The **0** is different: it is a property of the code this
file imports, checked below every time this file runs.

**The property, precisely.** A node survives a purge only if it is *grounded*:
every derivation path below it terminates at a surviving chunk in finitely
many steps. That is a least fixed point under a universal quantifier over
*every* declared source, which is exactly what `_DOOMED`'s recursive CTE
cannot express directly -- SQLite's recursion is row-at-a-time -- so
`_UNANCHORED_NODES` computes the complement instead: unanchored, and
everything built on top of it. :func:`reference_doomed` below computes the
positive property the other way, as an explicit least-fixed-point loop over
plain Python sets, so the two readings share no code and no SQL.

**The generator.** :data:`SEED` is stated rather than left to whatever
`random` defaults to, and a local `random.Random(SEED)` rather than the
global module, so this file's draw sequence cannot be perturbed by test
order or by another module seeding the same global generator. Each of the
:data:`GRAPH_COUNT` graphs draws 1-4 chunks (each stamped `revW`, the
revision this test withdraws, or `revOK`, one that survives), 0-4 nodes, and
0-2 source edges per node, each edge a coin flip between naming a chunk and
naming another node -- so cycles, self-references, dangling shapes (a source
naming nothing in the graph) and multi-parent diamonds all arise from the
same draw rather than from a hand-picked case each. A self-edge the schema's
`CHECK (source_node_id IS NULL OR source_node_id <> node_id)` refuses is
dropped from **both** the rows written to SQLite and the edge list handed to
:func:`reference_doomed`, and counted -- an edge the database never held must
not be part of what the reference is asked to agree with.

**What is asserted, over every graph.** Two things, independently: that the
set of chunk and node ids `derive_purged` removes equals what
:func:`reference_doomed` computes, and that `SqliteIndexStore.holds_any_
revision` -- the pre-check `withdrawal_purge` runs before paying for a purge
at all -- answers `True` exactly when that purge removed something. Both are
checked through the public `SqliteIndexStore` API, the same one a caller
uses, rather than by importing `index_purge`'s private SQL.

Measured: 400 graphs, 142 self-edges refused, 0 divergence in either
assertion, roughly 4 s end to end (each graph pays for a real `SqliteIndexStore.
create` and a real `derive_purged`, which copies a whole file). No `slow`
marker exists in this repository's convention (`pyproject.toml`'s `markers`
list has `unit`, `integration`, `contract`, `e2e` only), and 4 s does not
warrant inventing one.
"""

from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

#: Freezes the draw sequence against source edits elsewhere in this file --
#: see the module docstring. Any constant would do; kept small and explicit
#: rather than derived from anything.
SEED: Final = 7

#: The population size the lineage in the module docstring is measured over.
GRAPH_COUNT: Final = 400

WITHDRAWN_REVISION: Final = "revW"
SURVIVING_REVISION: Final = "revOK"

#: The self edges the schema's `CHECK` refuses, summed over all 400 graphs --
#: the same 142 `index_purge._UNANCHORED_NODES`'s own docstring cites as the
#: population the `CHECK` alone removes between the 91 and the 11.
EXPECTED_SELF_EDGES_REFUSED: Final = 142

_CHUNK_COLUMNS = (
    "chunk_id, project_id, item_id, revision_id, served_content_sha256, ordinal, heading, text, "
    "token_estimate, status, sensitivity, trust_level"
)
_NODE_COLUMNS = (
    "node_id, tree_id, level, node_type, text, content_hash, summary_model, "
    "summary_model_revision, summary_prompt_hash, embedding_model, "
    "embedding_model_revision, embedding_dimension, source_revision_id, "
    "index_build_id, project_id, sensitivity, status"
)

Edge = tuple[str, str]  # ("chunk" | "node", target id)
Graph = tuple[dict[str, str], list[str], dict[str, list[Edge]]]


def reference_doomed(
    chunks: dict[str, str], nodes: list[str], edges: dict[str, list[Edge]], withdrawn: set[str]
) -> tuple[set[str], set[str]]:
    """The well-founded reading, computed independently of `_DOOMED`'s SQL.

    A node is added to `safe` only once every one of its declared sources is
    itself resolved -- a surviving chunk, or a node already in `safe` -- so the
    loop is a least fixed point over "grounded and clean" rather than a single
    forward walk. A node with a source that never resolves (a cycle, a
    dangling reference, a withdrawn chunk, or no sources at all) never enters
    `safe`, however many iterations the loop is given.
    """
    doomed_chunks = {chunk_id for chunk_id, revision in chunks.items() if revision in withdrawn}
    safe: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node_id in nodes:
            if node_id in safe:
                continue
            sources = edges.get(node_id, [])
            if not sources:
                continue
            grounded = True
            for kind, target in sources:
                if kind == "chunk":
                    if target in doomed_chunks or target not in chunks:
                        grounded = False
                        break
                elif target not in safe:
                    grounded = False
                    break
            if grounded:
                safe.add(node_id)
                changed = True
    return doomed_chunks, set(nodes) - safe


def _generate_graphs(seed: int, count: int) -> tuple[list[Graph], int]:
    """`count` random chunk/node/edge graphs, and the self edges the schema refuses.

    The generation algorithm the lineage in the module docstring was measured
    against: 1-4 chunks each stamped `revW` or `revOK`, 0-4 nodes, 0-2 source
    edges per node each naming a chunk or another node. Self edges are
    generated -- the draw does not know the schema will refuse them -- and
    dropped from the returned graph's edge list, because a graph handed to
    :func:`reference_doomed` must describe what the database actually holds,
    not what was drawn.
    """
    rng = random.Random(seed)  # noqa: S311 - a fixture generator, not a security control
    graphs: list[Graph] = []
    refused = 0
    for _ in range(count):
        chunk_count, node_count = rng.randint(1, 4), rng.randint(0, 4)
        chunks = {
            f"c{i}": rng.choice([WITHDRAWN_REVISION, SURVIVING_REVISION])
            for i in range(chunk_count)
        }
        nodes = [f"n{i}" for i in range(node_count)]
        edges: dict[str, list[Edge]] = {}
        for node_id in nodes:
            drawn: list[Edge] = []
            for _ in range(rng.randint(0, 2)):
                if rng.random() < 0.5 and chunks:
                    drawn.append(("chunk", rng.choice(list(chunks))))
                elif nodes:
                    drawn.append(("node", rng.choice(nodes)))
            kept = [edge for edge in sorted(set(drawn)) if edge != ("node", node_id)]
            refused += len(set(drawn)) - len(kept)
            edges[node_id] = kept
        graphs.append((chunks, nodes, edges))
    return graphs, refused


def _write_graph(
    connection: sqlite3.Connection,
    chunks: dict[str, str],
    nodes: list[str],
    edges: dict[str, list[Edge]],
) -> None:
    """The chunks, the nodes, and their derivation edges, in that order (FK order)."""
    connection.execute("PRAGMA foreign_keys = ON")
    with connection:
        connection.executemany(
            f"INSERT INTO chunks ({_CHUNK_COLUMNS}) "  # noqa: S608 - module-owned column list
            "VALUES (?, 'p', 'i', ?, 'body-hash', 0, 'h', 'text of a generated chunk', 4, "
            "'approved', 'internal', 'reviewed')",
            list(chunks.items()),
        )
        connection.executemany(
            f"INSERT INTO nodes ({_NODE_COLUMNS}) "  # noqa: S608 - module-owned column list
            "VALUES (?, 'tree', 1, 'document', 'text of a generated node', 'hash', '', '', "
            "'', '', '', 0, '', 'build', 'p', 'internal', 'approved')",
            [(node_id,) for node_id in nodes],
        )
        for node_id, sources in edges.items():
            for kind, target in sources:
                connection.execute(
                    "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                    "VALUES (?, ?, ?)",
                    (
                        node_id,
                        target if kind == "chunk" else None,
                        target if kind == "node" else None,
                    ),
                )


def _surviving_ids(path: Path) -> tuple[set[str], set[str]]:
    with closing(sqlite3.connect(path)) as connection:
        surviving_chunks = {row[0] for row in connection.execute("SELECT chunk_id FROM chunks")}
        surviving_nodes = {row[0] for row in connection.execute("SELECT node_id FROM nodes")}
    return surviving_chunks, surviving_nodes


def test_derive_purged_matches_a_well_founded_reference_over_400_random_graphs(
    tmp_path: Path,
) -> None:
    """Zero divergence, over 400 graphs, between the shipped purge and an independent reference.

    See the module docstring for the property, the generator, and the
    91/11/0 lineage this reproduces the last member of. A single test rather
    than 400 parametrised ones: the property is "over the whole population",
    and splitting it would let 399 green cases hide one red one behind
    `-x`-free CI output instead of failing the run.
    """
    graphs, refused_total = _generate_graphs(SEED, GRAPH_COUNT)
    assert refused_total == EXPECTED_SELF_EDGES_REFUSED, (
        f"the generator drew {refused_total} self edges, not {EXPECTED_SELF_EDGES_REFUSED} -- "
        f"the population the lineage in this file's docstring is measured over has changed"
    )

    divergences: list[str] = []
    for trial, (chunks, nodes, edges) in enumerate(graphs):
        stale_path = tmp_path / f"stale-{trial}.sqlite"
        store = SqliteIndexStore(stale_path)
        store.create(index_build_id="01K1STALE", state_hash="state-abc")
        with closing(sqlite3.connect(stale_path)) as connection:
            _write_graph(connection, chunks, nodes, edges)

        reference_chunks, reference_nodes = reference_doomed(
            chunks, nodes, edges, {WITHDRAWN_REVISION}
        )
        holds = store.holds_any_revision([WITHDRAWN_REVISION])
        purged_path = tmp_path / f"purged-{trial}.sqlite"
        removed = store.derive_purged(
            purged_path,
            revision_ids=[WITHDRAWN_REVISION],
            index_build_id="01K1PURGED",
            state_hash="state-abc",
        )

        surviving_chunks, surviving_nodes = _surviving_ids(purged_path)
        actual_doomed_chunks = set(chunks) - surviving_chunks
        actual_doomed_nodes = set(nodes) - surviving_nodes

        if actual_doomed_chunks != reference_chunks or actual_doomed_nodes != reference_nodes:
            divergences.append(
                f"trial {trial}: chunks={chunks} edges={edges} -- derive_purged doomed "
                f"chunks={sorted(actual_doomed_chunks)} nodes={sorted(actual_doomed_nodes)}, "
                f"reference doomed chunks={sorted(reference_chunks)} "
                f"nodes={sorted(reference_nodes)}"
            )
        if holds != (removed > 0):
            divergences.append(
                f"trial {trial}: holds_any_revision()={holds} but derive_purged() removed="
                f"{removed} -- chunks={chunks} edges={edges}"
            )

        stale_path.unlink()
        purged_path.unlink()

    assert not divergences, (
        f"{len(divergences)} of {GRAPH_COUNT} graphs diverged from the well-founded reference:\n"
        + "\n".join(divergences[:5])
    )
