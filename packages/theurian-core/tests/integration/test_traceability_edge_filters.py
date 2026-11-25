"""`list_traceability_edges`' filters, pinned against #1033's three inherited ACs.

A filter is an endpoint's type and id together, inside the caller's project (SEC-13).
`TraceNode` only refuses an empty id, so a code or test node's id is a free path; the
item-id grammar (`[a-z0-9.-]`) governs knowledge nodes alone. The ids below therefore use
CODE and TEST nodes, where `_`, `%`, case and the GLOB metacharacters are all legal.

The fixtures are a minimal copy of `test_canonical_store.py`'s (which is past the file-size
cap and must not grow); its helpers are not importable from a test module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from theurian.domain.context import RequestContext
from theurian.domain.enums import RelationType, TraceNodeType
from theurian.domain.identifiers import EdgeId, ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.project import Project
from theurian.domain.specification import TraceabilityEdge, TraceNode
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

PROJECT = ProjectId("demo")
OTHER_PROJECT = ProjectId("other-service")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
ANCHOR = SourceAnchor(
    provider="git",
    source_uri="git://demo/.theurian/knowledge/a.md",
    commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    file_path=".theurian/knowledge/a.md",
    line_start=1,
    line_end=10,
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "state" / "theurian-state-abc123.sqlite"
    create_database(path, state_hash="a" * 64, engine_version=1)
    return path


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "runtime" / "write.lock"


def _project(project_id: ProjectId) -> Project:
    return Project(
        project_id=project_id,
        root_path="/tmp/demo",  # noqa: S108 - a value, never opened
        repository_url="https://github.com/acme/demo",
        default_branch="main",
        knowledge_directory=PurePosixPath(".theurian"),
        registered_at=NOW,
    )


def _edge(
    serial: int, source: TraceNode, target: TraceNode, project_id: ProjectId = PROJECT
) -> TraceabilityEdge:
    return TraceabilityEdge(
        edge_id=EdgeId(f"01K1DEFEDG01234567890ABC{serial:02d}"),
        project_id=project_id,
        source=source,
        relation_type=RelationType.RELATED_TO,
        target=target,
        evidence=(ANCHOR,),
        confidence=0.75,
        created_at=NOW,
        source_commit="c" * 40,
    )


def _store(database: Path, lock: Path, *edges: TraceabilityEdge) -> SqliteCanonicalStore:
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project(PROJECT))
        writer.register_project(_project(OTHER_PROJECT))
        for edge in edges:
            writer.add_traceability_edge(edge)
    return SqliteCanonicalStore(database)


def _code(node_id: str) -> TraceNode:
    return TraceNode(TraceNodeType.CODE, node_id)


def _test(node_id: str) -> TraceNode:
    return TraceNode(TraceNodeType.TEST, node_id)


def test_a_foreign_projects_edge_between_the_same_endpoints_is_not_returned_under_both_filters(
    database: Path, lock: Path
) -> None:
    """SEC-13: a project's edge read must never carry another project's rows, including
    when both endpoint filters are given and both foreign endpoints match exactly."""
    mine = _edge(1, _code("services/auth/token.py"), _test("tests/test_token.py"))
    foreign = _edge(2, mine.source, mine.target, project_id=OTHER_PROJECT)

    with _store(database, lock, mine, foreign) as store:
        theirs = store.list_traceability_edges(
            RequestContext(project_id=OTHER_PROJECT), source=mine.source, target=mine.target
        )
        ours = store.list_traceability_edges(
            RequestContext(project_id=PROJECT), source=mine.source, target=mine.target
        )

    assert theirs == (foreign,), "the foreign edge is written, and its own project reads it"
    assert ours == (mine,)


#: Each id is a wildcard, a case variant, a prefix or a substring of another's, and
#: `svc_auth` is all of those to some other member.
_AWKWARD_IDS = (
    "svc_auth",
    "svcXauth",  # LIKE's `_` matches this
    "svc%",  # LIKE's `%`, and a prefix of every other `svc` id
    "SVC_AUTH",  # case-insensitive equal of `svc_auth`
    "svc_auth_v2",  # `svc_auth` is its prefix
    "lib/svc_auth",  # contains `svc_auth`
    "svc*",  # GLOB metacharacters from here
    "svc?auth",
    "svc[_]auth",
    "svc",
)


@pytest.mark.parametrize("side", ["source", "target"])
def test_an_endpoint_filter_matches_exactly_its_own_id_among_wildcard_case_and_prefix_ids(
    database: Path, lock: Path, side: str
) -> None:
    """SEC-13: an id is data. A `LIKE`, `GLOB`, `NOCASE`, prefix, substring or ordered
    comparison on it returns a neighbour's rows -- the edges of a different node."""
    edges = tuple(_edge(n, _code(i), _test(i)) for n, i in enumerate(_AWKWARD_IDS))
    make = _code if side == "source" else _test

    with _store(database, lock, *edges) as store:
        context = RequestContext(project_id=PROJECT)
        answers = {
            i: store.list_traceability_edges(context, **{side: make(i)}) for i in _AWKWARD_IDS
        }

    assert answers == {i: (edge,) for i, edge in zip(_AWKWARD_IDS, edges, strict=True)}
