"""`_node_scope`, isolated from the leaf-side gate it stands in front of
(ADR-0008 decision 8, SEC-13, T-15).

`search_summaries` (`index_store.py`) filters twice: `_node_scope` decides
which *summary nodes* a query may even match, before `node_derivation` is
descended to any leaf; `_scope` then filters the *leaves* that descent
reaches, bound to the same ``project_id``/``include_unapproved``/
``visible_sensitivities`` arguments.
Every disclosure test in `test_forest_retrieval.py` builds a forest the real
builder produces, where a node's own scope always equals its children's
(`SummaryNode.__post_init__`'s own invariant) -- so a withheld leaf is always
itself out of scope, `_scope` withholds it on its own, and `_node_scope` never
gets a chance to matter. Measured: deleting `_node_scope`'s status clause
left every one of those tests, and the two forest-routing ones in this
package, green.

The two tests below build node and edge rows directly, the idiom
`test_index_purge_nodes.py` uses for shapes the real builder cannot produce:
a summary node whose own ``status``/``project_id`` disagrees with the one
leaf chunk its `node_derivation` edge names. `SummaryNode` would refuse that
disagreement were it built through the domain layer -- these tests bypass it
on purpose, because it is exactly the shape that tells the two gates apart.
With the leaf's own scope left untouched (so `_scope` has nothing to
withhold on that leaf), only `_node_scope`'s own predicate decides whether
the query ever reaches it.
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
OTHER_PROJECT = "other-project"

#: Present in every summary node's text below, so a query for it is a
#: candidate match against *every* node the fixture writes -- which of them
#: is actually descended is exactly what `_node_scope` is supposed to decide.
ROUTING_TERM = "forestroutingterm"


def _indexable(
    chunk_id: str,
    text: str,
    *,
    revision: str,
    project: str = PROJECT,
    status: str = "approved",
) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=""),
        project_id=project,
        item_id=f"architecture.{revision}",
        revision_id=revision,
        served_content_sha256=f"body-of-{revision}",
        status=status,
        sensitivity="internal",
        trust_level="reviewed",
    )


def _node(  # noqa: PLR0913 - a raw row helper, one keyword per column the tests vary or need
    connection: sqlite3.Connection,
    node_id: str,
    *,
    text: str,
    project_id: str = PROJECT,
    status: str = "approved",
    sensitivity: str = "internal",
    index_build_id: str = "01K1NSCOPE",
) -> None:
    """A raw `nodes` row, written the way RAPTOR would (ADR-0008 decision 5).

    Not shared with `test_index_purge_nodes.py`'s `_node`: that helper
    hardcodes `project_id` and `status` to the one value every purge fixture
    wants, and the three columns varied here are exactly those two plus
    `sensitivity`, which `_node_scope` began filtering on in #119 phase 4.
    """
    connection.execute(
        "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
        "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
        "embedding_model_revision, embedding_dimension, source_revision_id, "
        "index_build_id, project_id, sensitivity, status) "
        "VALUES (?, 'tree-abc', 1, 'document', ?, 'deadbeef', '', '', '', '', '', 0, '', ?, ?, "
        "?, ?)",
        (node_id, text, index_build_id, project_id, sensitivity, status),
    )


def _edge(connection: sqlite3.Connection, node_id: str, *, chunk: str) -> None:
    connection.execute(
        "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
        "VALUES (?, ?, NULL)",
        (node_id, chunk),
    )


def _node_texts(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT node_id, text FROM nodes")
        }


def _chunk_sensitivity(path: Path, chunk_id: str) -> str:
    """A chunk's own class, read back after `add_chunks` (#119 phase 4)."""
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT sensitivity FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
    assert row is not None, f"precondition: {chunk_id} was never written"
    return str(row[0])


def _chunk_scope(path: Path, chunk_id: str) -> tuple[str, str]:
    """A chunk's own ``(status, project_id)``, read back after `add_chunks`."""
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT status, project_id FROM chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
    assert row is not None, f"precondition: {chunk_id} was never written"
    return str(row[0]), str(row[1])


# -- 1. Node status isolation -------------------------------------------------


def test_search_summaries_does_not_descend_a_draft_status_node_by_default(
    tmp_path: Path,
) -> None:
    """A draft-*status* summary node must not be traversed on a default query,
    even when the one leaf it names is itself approved.

    RED against a `_node_scope` whose status clause is removed: with the
    fixture's leaked leaf carrying `status="approved"`, `_scope`'s own leaf
    filter (bound to `include_unapproved=False`) would let that leaf straight
    through once the node-match stage stopped excluding the draft node --
    which is what makes this test able to fail on `_node_scope` alone, unlike
    every fixture in `test_forest_retrieval.py`.
    """
    path = tmp_path / "theurian-index-node-status.sqlite"
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1NSCOPE", state_hash="state-abc")
    store.add_chunks(
        [
            _indexable(
                "approved-leaf#0", "an ordinary approved paragraph", revision="approved-rev"
            ),
            _indexable(
                "leaked-leaf#0",
                "a paragraph that must stay reachable only through a draft-status node",
                revision="leaked-rev",
            ),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "approved-node#0",
                text=f"{ROUTING_TERM} summary of the approved leaf",
                status="approved",
            )
            _edge(connection, "approved-node#0", chunk="approved-leaf#0")
            _node(
                connection,
                "draft-node#0",
                text=f"{ROUTING_TERM} summary that names an approved leaf from a draft node",
                status="draft",
            )
            _edge(connection, "draft-node#0", chunk="leaked-leaf#0")

    assert all(ROUTING_TERM in text.lower() for text in _node_texts(path).values()), (
        "precondition: the routing term must match every node's text, or a query for "
        "it would exclude one for a reason that has nothing to do with node scope"
    )
    leaked_status, leaked_project = _chunk_scope(path, "leaked-leaf#0")
    assert (leaked_status, leaked_project) == ("approved", PROJECT), (
        "precondition: the leaked leaf's own scope must already clear the leaf gate, "
        "or withholding it would prove nothing about _node_scope specifically"
    )

    page = store.search_summaries(
        ROUTING_TERM,
        project_id=PROJECT,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )

    surfaced = {row.chunk_id for row in page.rows}
    assert surfaced == {"approved-leaf#0"}, (
        f"a default query must not route through a draft-status summary node, even to "
        f"an approved leaf; got {sorted(surfaced)}"
    )


# -- 2. Node project isolation ------------------------------------------------


def test_search_summaries_does_not_descend_a_node_from_another_project(
    tmp_path: Path,
) -> None:
    """A summary node belonging to another project must not be traversed by a
    query scoped to this one, even when the one leaf it names belongs here.

    RED against a `_node_scope` whose project clause is removed: with the
    fixture's leaked leaf carrying `project_id=PROJECT`, `_scope`'s own leaf
    filter would let it through once the node-match stage stopped excluding
    the other project's node.
    """
    path = tmp_path / "theurian-index-node-project.sqlite"
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1NSCOPE", state_hash="state-abc")
    store.add_chunks(
        [
            _indexable(
                "approved-leaf#0", "an ordinary approved paragraph", revision="approved-rev"
            ),
            _indexable(
                "leaked-leaf#0",
                "a paragraph that must stay reachable only through another project's node",
                revision="leaked-rev",
            ),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "approved-node#0",
                text=f"{ROUTING_TERM} summary of the approved leaf",
                project_id=PROJECT,
            )
            _edge(connection, "approved-node#0", chunk="approved-leaf#0")
            _node(
                connection,
                "other-project-node#0",
                text=f"{ROUTING_TERM} summary belonging to another project",
                project_id=OTHER_PROJECT,
            )
            _edge(connection, "other-project-node#0", chunk="leaked-leaf#0")

    assert all(ROUTING_TERM in text.lower() for text in _node_texts(path).values()), (
        "precondition: the routing term must match every node's text, or a query for "
        "it would exclude one for a reason that has nothing to do with node scope"
    )
    leaked_status, leaked_project = _chunk_scope(path, "leaked-leaf#0")
    assert (leaked_status, leaked_project) == ("approved", PROJECT), (
        "precondition: the leaked leaf's own scope must already clear the leaf gate, "
        "or withholding it would prove nothing about _node_scope specifically"
    )

    page = store.search_summaries(
        ROUTING_TERM,
        project_id=PROJECT,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )

    surfaced = {row.chunk_id for row in page.rows}
    assert surfaced == {"approved-leaf#0"}, (
        f"a query scoped to {PROJECT!r} must not route through a summary node "
        f"belonging to {OTHER_PROJECT!r}, even to a leaf that belongs here; "
        f"got {sorted(surfaced)}"
    )


# -- 3. Node disclosure-class isolation (#119 phase 4) ------------------------


def test_search_summaries_does_not_descend_a_node_above_the_deployments_ceiling(
    tmp_path: Path,
) -> None:
    """A summary node above the grant must not be traversed, even to a leaf below it.

    The third axis, isolated the same way as the first two, and the reason
    ADR-0025 requires all four scoring surfaces rather than the leaf pair: a
    summary node's text is a paraphrase of its children, so a gate that covered
    `chunks` alone would still route on -- and hand a leaf the score of -- a
    summary of a document this deployment does not serve.

    The file this builds is a *wider* build, the shape `_published_index` stands
    aside whole in the shipped stack (`serving-profile-mismatch`); calling
    `search_summaries` directly with a narrow grant is the bypass that leaves the
    predicate as the only thing deciding.

    RED against a `_node_scope` whose sensitivity clause is removed: the leaked
    leaf is `internal`, which the grant admits, so `_scope` has nothing to
    withhold on it and it arrives through the restricted node.
    """
    path = tmp_path / "theurian-index-node-sensitivity.sqlite"
    store = SqliteIndexStore(path)
    store.create(index_build_id="01K1NSCOPE", state_hash="state-abc")
    store.add_chunks(
        [
            _indexable("served-leaf#0", "an ordinary internal paragraph", revision="served-rev"),
            _indexable(
                "leaked-leaf#0",
                "a paragraph that must stay reachable only through a restricted node",
                revision="leaked-rev",
            ),
        ]
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            _node(
                connection,
                "internal-node#0",
                text=f"{ROUTING_TERM} summary of the internal leaf",
                sensitivity="internal",
            )
            _edge(connection, "internal-node#0", chunk="served-leaf#0")
            _node(
                connection,
                "restricted-node#0",
                text=f"{ROUTING_TERM} summary that names an internal leaf from a restricted node",
                sensitivity="restricted",
            )
            _edge(connection, "restricted-node#0", chunk="leaked-leaf#0")

    assert all(ROUTING_TERM in text.lower() for text in _node_texts(path).values()), (
        "precondition: the routing term must match every node's text, or a query for "
        "it would exclude one for a reason that has nothing to do with node scope"
    )
    assert _chunk_sensitivity(path, "leaked-leaf#0") == Sensitivity.INTERNAL.value, (
        "precondition: the leaked leaf's own class must already clear the leaf gate, "
        "or withholding it would prove nothing about _node_scope specifically"
    )

    page = store.search_summaries(
        ROUTING_TERM,
        project_id=PROJECT,
        include_unapproved=False,
        visible_sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL}),
    )

    surfaced = {row.chunk_id for row in page.rows}
    assert surfaced == {"served-leaf#0"}, (
        f"an `internal` deployment must not route through a `restricted` summary node, "
        f"even to a leaf it does serve; got {sorted(surfaced)}"
    )
