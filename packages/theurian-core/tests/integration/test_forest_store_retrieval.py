"""`search_summaries` ranking, paging and its second (leaf) gate, and the
`raptor_path` walk's own scope gate, all isolated at the store (ADR-0008
decision 8, FR-R3, FR-R5, FR-R7, SEC-13, T-15).

These call :meth:`SqliteIndexStore.search_summaries` and
:meth:`SqliteIndexStore.raptor_path` **directly**, not through the retrieval
pipeline. The pipeline re-clears every descended leaf through
``CanonicalVisibility`` and fuses on rank, so a defect in the summary SQL's own
ceiling, ordering, best-score aggregation or leaf gate is masked there -- a
downstream gate withholds the same rows a broken upstream one would, and RRF's
rank fusion hides a wrong score. The forest fixtures in
`test_forest_retrieval.py` mask them a second way: they are built by the real
`ForestBuilder`, which produces one matched summary node and fewer leaves than
the limit, so the ceiling, the ordering and the diamond aggregation are never
exercised and the four SQL mutations below survived every one of them.

Like `test_forest_node_scope.py`, the summary nodes and their ``node_derivation``
edges are written directly with SQL, the idiom `test_index_purge_nodes.py` uses
for shapes the real builder cannot produce: >limit leaves under one node, a leaf
reached by two summary nodes at once, a draft leaf under an approved node, and a
draft-scope ancestor above an approved leaf. ``SummaryNode.__post_init__`` refuses
the last two were they built through the domain layer -- which is exactly why the
gates that stand behind that invariant have no fixture that reaches them.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

#: The disclosure grant every retriever call in this file runs under: all four
#: levels, which is what "this deployment serves everything" means once the
#: retrievers take the axis as a WHERE predicate (#119 phase 4). Spelled out
#: rather than read from ``StaticAuthorizationProvider``'s shipped default, which
#: a later phase narrows -- a file that inherited it would start withholding its
#: own fixtures silently, turning these tests into tests of something else.
EVERY_SENSITIVITY = frozenset(Sensitivity)


PROJECT = "demo"

#: Present in every summary node these tests match on, absent from every leaf's
#: own text, so a surfaced leaf can only have arrived by routing through a node.
ROUTING_TERM = "forestroutingterm"

#: A node whose text repeats the routing term in a short body, so FTS5 scores it
#: better (a more negative ``bm25``) than ``_WEAK_SUMMARY`` below.
_STRONG_SUMMARY = (ROUTING_TERM + " ") * 20
#: The routing term once, buried in a long filler body -- a materially worse
#: ``bm25`` than ``_STRONG_SUMMARY`` for the same query.
_WEAK_SUMMARY = ROUTING_TERM + " " + "alpha beta gamma delta epsilon " * 40
#: Non-matching nodes, present only to move ``bm25``'s collection statistics off
#: the two-document degenerate case: with just the strong and weak nodes in the
#: table the inverse-document-frequency term is near zero and both scores collapse
#: to a few millionths, distinct but not legibly so. Eight unrelated rows give the
#: term a positive IDF and separate the two scores to ~2.58 against ~0.42, so the
#: ordering these tests pin is a wide gap rather than a float-epsilon accident.
_NOISE_SUMMARY = "unrelated summary about something else entirely and more filler words here"


def _indexable(
    chunk_id: str,
    text: str,
    *,
    revision: str,
    status: str = "approved",
    project: str = PROJECT,
) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=""),
        project_id=project,
        item_id=f"architecture.{revision}",
        revision_id=revision,
        status=status,
        sensitivity="internal",
        trust_level="reviewed",
    )


def _node(  # noqa: PLR0913 - a raw row helper, one keyword per column a test varies
    connection: sqlite3.Connection,
    node_id: str,
    *,
    text: str,
    level: int = 1,
    node_type: str = "document",
    status: str = "approved",
    project_id: str = PROJECT,
) -> None:
    """A raw `nodes` row, written the way RAPTOR would (ADR-0008 decision 5).

    Mirrors `test_forest_node_scope._node`; ``level`` and ``node_type`` are
    keywords here because these tests build two- and three-tier ancestries the
    node-scope tests never needed.
    """
    connection.execute(
        "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
        "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
        "embedding_model_revision, embedding_dimension, source_revision_id, "
        "index_build_id, project_id, sensitivity, status) "
        "VALUES (?, 'tree-abc', ?, ?, ?, 'deadbeef', '', '', '', '', '', 0, '', "
        "'01K1NSCOPE', ?, 'internal', ?)",
        (node_id, level, node_type, text, project_id, status),
    )


def _edge_chunk(connection: sqlite3.Connection, node_id: str, *, chunk: str) -> None:
    connection.execute(
        "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
        "VALUES (?, ?, NULL)",
        (node_id, chunk),
    )


def _edge_node(connection: sqlite3.Connection, node_id: str, *, child: str) -> None:
    connection.execute(
        "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
        "VALUES (?, NULL, ?)",
        (node_id, child),
    )


def _noise(connection: sqlite3.Connection, count: int) -> None:
    for i in range(count):
        _node(connection, f"noise-{i}", text=_NOISE_SUMMARY)


def _store(path: Path) -> SqliteIndexStore:
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1NSCOPE", state_hash="state-abc")
    return store


def _leaf_texts(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT chunk_id, text FROM chunks")
        }


def _chunk_status(path: Path, chunk_id: str) -> str:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT status FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
    assert row is not None, f"precondition: {chunk_id} was never written"
    return str(row[0])


def _node_status(path: Path, node_id: str) -> str:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT status FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
    assert row is not None, f"precondition: {node_id} was never written"
    return str(row[0])


# -- 1. LIMIT + 1 is a true exhaustion ceiling over routed leaves ------------


def test_search_summaries_limit_is_a_true_ceiling_over_routed_leaves(tmp_path: Path) -> None:
    """FR-R3, FR-R7. ``limit`` bounds the leaves a single matched summary routes
    to, and the page's ``exhausted`` flag distinguishes "this is the whole match
    set" from "there is more" -- which only holds if the SQL fetches one row past
    the ceiling (``LIMIT ? + 1``) and :func:`_page` drops it.

    RED against the ``LIMIT limit`` mutation (the ``+ 1`` removed): with exactly
    ``limit`` rows fetched, ``len(ranked) <= limit`` is always true, so every page
    reports ``exhausted=True`` -- a truncated result that lies about being
    complete. Sixty leaves descend from one summary node, so a ``limit`` of five
    is a genuine truncation and must say so; the existing forest fixtures route
    to fewer leaves than any limit and so can never take this branch.
    """
    path = tmp_path / "theurian-index-ceiling.sqlite"
    store = _store(path)
    total = 60
    store.add_chunks(
        [
            _indexable(f"leaf#{i:02d}", "an ordinary paragraph", revision=f"rev{i:03d}")
            for i in range(total)
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "domain#0", text=f"{ROUTING_TERM} summary over sixty leaves")
            for i in range(total):
                _edge_chunk(connection, "domain#0", chunk=f"leaf#{i:02d}")

    assert all(ROUTING_TERM not in text for text in _leaf_texts(path).values()), (
        "precondition: no leaf carries the routing term itself, so all sixty are "
        "reachable only by descending the one summary node -- otherwise a leaf "
        "retriever, not the forest, could account for them"
    )

    truncated = store.search_summaries(
        ROUTING_TERM, project_id=PROJECT, limit=5, visible_sensitivities=EVERY_SENSITIVITY
    )
    assert len(truncated.rows) == 5, "the ceiling must cap the page at exactly limit rows"
    assert truncated.exhausted is False, (
        "sixty leaves route from the matched node, so a page of five is a "
        "truncation and must report there is more -- which needs the LIMIT + 1 probe"
    )

    whole = store.search_summaries(
        ROUTING_TERM, project_id=PROJECT, limit=total, visible_sensitivities=EVERY_SENSITIVITY
    )
    assert len(whole.rows) == total, "the whole match set is sixty routed leaves"
    assert whole.exhausted is True, "a page holding every routed leaf is exhausted"


# -- 2. The leaf gate withholds a draft leaf under an approved node -----------


def test_search_summaries_withholds_a_draft_leaf_under_an_approved_node(tmp_path: Path) -> None:
    """SEC-13, T-15. `search_summaries` gates the descended *leaves* a second
    time (``_scope``), not only the summary nodes it matches (``_node_scope``).
    An approved, in-project summary node clears the node gate and is traversed;
    a draft leaf beneath it must still be withheld on a default query, because
    routing decides which leaves are candidates, never whether a gated row
    surfaces.

    RED against the leaf gate forced to ``include_unapproved=True`` (the
    ``leaf_scope = self._scope(project_id, True)`` mutation): the node gate lets
    the approved node through regardless, so only the leaf gate stands between the
    query and the draft leaf. `test_forest_node_scope.py` proves the *node* gate
    in isolation but places its leaked leaf under a *draft* node; here the node is
    approved, so a draft leaf that surfaces can only have come through the leaf
    gate being open. No fixture the real builder produces reaches this: a draft
    leaf never sits under an approved node (scope carries status).
    """
    path = tmp_path / "theurian-index-leaf-gate.sqlite"
    store = _store(path)
    store.add_chunks(
        [
            _indexable(
                "approved-leaf#0", "an ordinary approved paragraph", revision="approved-rev"
            ),
            _indexable(
                "draft-leaf#0",
                "a draft paragraph that must stay withheld",
                revision="draft-rev",
                status="draft",
            ),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "approved-node#0",
                text=f"{ROUTING_TERM} approved summary naming an approved and a draft leaf",
                status="approved",
            )
            _edge_chunk(connection, "approved-node#0", chunk="approved-leaf#0")
            _edge_chunk(connection, "approved-node#0", chunk="draft-leaf#0")

    assert _node_status(path, "approved-node#0") == "approved", (
        "precondition: the summary node must clear the node gate, or withholding "
        "the draft leaf would prove the node gate, not the leaf gate"
    )
    assert _chunk_status(path, "draft-leaf#0") == "draft", (
        "precondition: the withheld leaf must itself be draft, so only its own status can gate it"
    )

    page = store.search_summaries(
        ROUTING_TERM,
        project_id=PROJECT,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )

    surfaced = {row.chunk_id for row in page.rows}
    assert surfaced == {"approved-leaf#0"}, (
        f"a default query must descend the approved node to its approved leaf but "
        f"withhold the draft leaf beside it; got {sorted(surfaced)}"
    )


# -- 3. Leaves are ordered by the best summary score, best first -------------


def test_search_summaries_orders_leaves_best_summary_score_first(tmp_path: Path) -> None:
    """FR-R3, FR-R7. A leaf inherits the score of the summary that reached it, and
    the page is ordered best-first so ``limit`` keeps the strongest matches. The
    order is total and reproducible: ties break on ``chunk_id``.

    RED against the ``ORDER BY rank_score DESC`` mutation, which reverses it to
    worst-first. The strong summary scores materially better than the weak one, so
    its leaf must precede the weak summary's leaf; a descending order would swap
    them. Each leaf is reached by exactly one summary, so this test is blind to the
    ``min``/``max`` aggregation -- it isolates the sort direction.
    """
    path = tmp_path / "theurian-index-order.sqlite"
    store = _store(path)
    store.add_chunks(
        [
            _indexable("leaf-strong", "an ordinary paragraph", revision="rev-strong"),
            _indexable("leaf-weak", "an ordinary paragraph", revision="rev-weak"),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "node-strong", text=_STRONG_SUMMARY)
            _node(connection, "node-weak", text=_WEAK_SUMMARY)
            _noise(connection, 8)
            _edge_chunk(connection, "node-strong", chunk="leaf-strong")
            _edge_chunk(connection, "node-weak", chunk="leaf-weak")

    page = store.search_summaries(
        ROUTING_TERM, project_id=PROJECT, limit=50, visible_sensitivities=EVERY_SENSITIVITY
    )

    order = [row.chunk_id for row in page.rows]
    assert order == ["leaf-strong", "leaf-weak"], (
        f"the leaf under the better-scoring summary must rank first; got {order}"
    )
    by_id = {row.chunk_id: row.score for row in page.rows}
    assert by_id["leaf-strong"] > by_id["leaf-weak"], (
        "precondition: the two summaries must score differently, or 'best first' "
        "is not being tested"
    )


# -- 4. A leaf reached by two summaries takes the better score ---------------


def test_a_leaf_reached_by_two_summaries_takes_the_better_score(tmp_path: Path) -> None:
    """FR-R3. When a leaf is grounded in more than one matched summary node -- a
    diamond in the forest -- it inherits the *best* (``min`` raw ``bm25``, most
    negative) of the scores that reach it, not the worst.

    RED against the ``min(d.score)`` -> ``max(d.score)`` mutation. The diamond leaf
    is reached by both the strong and the weak summary; under ``min`` its score
    equals the strong summary's, under ``max`` it would collapse to the weak
    summary's. The assertion is a within-run equality against two solo control
    leaves -- one reached only by the strong summary, one only by the weak -- so it
    compares scores computed by the same ``bm25`` in the same file rather than a
    brittle absolute float, and it is blind to the sort direction the previous test
    pins. The real builder never grounds one leaf in two summary nodes at the same
    tier, so no existing fixture drives the aggregate at all.
    """
    path = tmp_path / "theurian-index-diamond.sqlite"
    store = _store(path)
    store.add_chunks(
        [
            _indexable("leaf-strongsolo", "an ordinary paragraph", revision="rev-strongsolo"),
            _indexable("leaf-weaksolo", "an ordinary paragraph", revision="rev-weaksolo"),
            _indexable("leaf-diamond", "an ordinary paragraph", revision="rev-diamond"),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(connection, "node-strong", text=_STRONG_SUMMARY)
            _node(connection, "node-weak", text=_WEAK_SUMMARY)
            _noise(connection, 8)
            _edge_chunk(connection, "node-strong", chunk="leaf-strongsolo")
            _edge_chunk(connection, "node-weak", chunk="leaf-weaksolo")
            _edge_chunk(connection, "node-strong", chunk="leaf-diamond")
            _edge_chunk(connection, "node-weak", chunk="leaf-diamond")

    page = store.search_summaries(
        ROUTING_TERM, project_id=PROJECT, limit=50, visible_sensitivities=EVERY_SENSITIVITY
    )
    score = {row.chunk_id: row.score for row in page.rows}

    assert score.keys() >= {"leaf-strongsolo", "leaf-weaksolo", "leaf-diamond"}, (
        f"precondition: all three leaves must surface; got {sorted(score)}"
    )
    assert score["leaf-strongsolo"] > score["leaf-weaksolo"], (
        "precondition: the two summaries must score their solo leaves differently, "
        "or min and max over the diamond would be indistinguishable"
    )
    assert score["leaf-diamond"] == score["leaf-strongsolo"], (
        "the diamond leaf must inherit the better (strong) summary's score, not "
        f"the weaker one's {score['leaf-weaksolo']!r}; got {score['leaf-diamond']!r}"
    )


# -- 5. The walk's own scope gate: an approved leaf's path drops a draft ------
#      ancestor (defense in depth beyond the builder invariant).


def test_an_approved_leafs_raptor_path_excludes_a_draft_scope_ancestor(tmp_path: Path) -> None:
    """SEC-13, T-15. `walk_raptor_path` publishes a summary node's title for every
    ancestor of a surfaced leaf. Its `nodes` lookup filters on the surfaced leaf's
    own project_id and status -- a second, independent gate, not a reliance on the
    builder's invariant that a node's children share its six-component scope,
    which is what makes an approved leaf's ancestors approved in every build the
    real builder can produce.

    This builds the shape that invariant forbids and no shipped build produces --
    an approved leaf whose Domain ancestor is a *draft* node holding a secret -- and
    asks for the approved leaf's path. The draft ancestor's title, and the secret
    in it, must not appear. With the leaf gate and node gate both cleared upstream,
    the walk's own scope predicate is the only thing standing between the query
    and this ancestor, and it drops it: defense in depth that holds even where the
    builder invariant does not, not a reliance on the builder alone.

    The approved intermediate ancestor must survive that same filter, so the path
    is shortened rather than emptied.
    """
    path = tmp_path / "theurian-index-walk-scope.sqlite"
    secret = "zephyrsecret"  # noqa: S105 - fixture text, not a credential
    store = _store(path)
    store.add_chunks(
        [_indexable("approved-leaf#0", "an ordinary approved paragraph", revision="approved-rev")]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "approved-doc#0",
                text="approved document summary",
                level=1,
                node_type="document",
                status="approved",
            )
            _node(
                connection,
                "draft-domain#0",
                text=f"draft domain summary holding {secret} material",
                level=2,
                node_type="domain",
                status="draft",
            )
            _edge_chunk(connection, "approved-doc#0", chunk="approved-leaf#0")
            _edge_node(connection, "draft-domain#0", child="approved-doc#0")

    assert _node_status(path, "draft-domain#0") == "draft", (
        "precondition: the ancestor must be draft-scope, or there is nothing for a "
        "scope gate to withhold"
    )
    assert _node_status(path, "approved-doc#0") == "approved", (
        "precondition: the intermediate ancestor is approved, so a correct filter "
        "keeps it while dropping only the draft one"
    )

    segments = store.raptor_path("approved-rev", project_id=PROJECT)

    node_ids = {segment.node_id for segment in segments}
    assert "approved-doc#0" in node_ids, (
        "the approved ancestor shares the leaf's scope and must remain in the path"
    )
    assert "draft-domain#0" not in node_ids, (
        "a draft-scope ancestor must not appear in an approved leaf's raptorPath"
    )
    joined = " ".join(segment.title for segment in segments).lower()
    assert secret not in joined, "the draft ancestor's summary text must not ride out on the path"
