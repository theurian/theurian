"""Retrieval *through* the RAPTOR forest, and the disclosure surface it opens
(ADR-0008 decision 8, FR-R2, FR-R3, FR-R5, SEC-13, T-15, T-17a).

This is the CL that first connects a built forest to a response. Until it lands
a forest is written, purged and never read (``index build --raptor`` and the
withdrawal purge), and ``system.capabilities.raptor`` is ``False`` because no
answer path reads one. These tests define what "reads one" means:

- **Traversal (FR-R3).** A summary retriever matches ``nodes_fts`` and descends
  ``node_derivation`` to the *leaf* chunks under a matched node, contributing
  those leaves as candidates fused with the existing lexical/substring/dense
  retrievers. A summary node is a routing device: it may be traversed, and it is
  never itself a result row.
- **``raptorPath``.** A surfaced leaf carries its forest ancestry -- one
  ``{nodeId, level, title}`` segment per ancestor from the catalog root down to
  the leaf. ``title`` is node-derived free text (a summariser's output on the
  wire), so it is the new disclosure surface decision 8 names, together with the
  *routing* that decides which leaves are candidates at all.
- **The gate still holds (SEC-13).** Routing changes which leaves are candidates,
  never whether a gated row may surface. A withheld leaf reached through a summary
  is gated out exactly as one reached directly, contributes no result and no
  ``raptorPath``, and its ancestor summary's title never appears.

The corpus is built by the real CLI -- a Git tree, ``migrate apply``, and an
``index build --raptor`` -- and the tools are called through
``build_server(...).call_tool``, the same entry point the transport uses. The
node rows are read straight out of the published index file so the expected
``raptorPath`` is *the actual forest*, not a fixture's guess at one.

**Grounded before it was written.** The three approved documents below cluster
into one approved Domain node whose summary is dominated by ``gatewayx``; that
node's derivation reaches all three documents' leaves, and a leaf-only search for
``gatewayx`` returns only ``auth-policy`` today. The draft document forms its own
*draft-scope* Document node (scope carries status, so a draft and an approved
item are never in one tree) holding a routing token ``rotationx`` and a body-only
secret ``zephyrsecret``; a default search for ``rotationx`` returns nothing and
leaks neither token. Every precondition an assertion below rests on was confirmed
by building this corpus and reading its ``nodes`` table.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sqlite3
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from typer.testing import CliRunner

from theurian.application.project_service import (
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
)
from theurian.daemon.runner import build_server
from theurian.mcp.results import excerpt

pytestmark = pytest.mark.integration

runner = CliRunner()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCHEMAS = REPO_ROOT / "schemas"
RETRIEVAL_RESULT = "knowledge/retrieval-result.schema.json"

#: A leaf under the matched Domain node that does contain the query term.
AUTH_ITEM: Final = "architecture.auth-policy"
AUTH_REVISION: Final = "01K1AAAREV01234567890ABCDE"
#: Two leaves under the *same* Domain node that do not contain ``gatewayx`` and
#: so are unreachable by any leaf retriever for it -- only the forest routes to
#: them.
CACHE_ITEM: Final = "architecture.caching-policy"
QUEUE_ITEM: Final = "architecture.queue-policy"
#: A draft, in a different scope (status is a scope component), reachable through
#: its own draft-scope summary node by ``rotationx`` but withheld by default.
DRAFT_ITEM: Final = "architecture.rotation-draft"
DRAFT_REVISION: Final = "01K1DDDREV01234567890ABCDE"

#: A term that dominates the approved forest's Domain summary and appears in only
#: one leaf directly (``auth-policy``).
FOREST_TERM: Final = "gatewayx"
#: A term present only in the draft's summary node and leaves, used to route the
#: forest at the withheld document.
DRAFT_ROUTING_TERM: Final = "rotationx"
#: Body-only text of the draft that must never reach a default caller. It is not
#: any query below, so its absence from a response is a real disclosure check and
#: not an artefact of the echoed query.
DRAFT_SECRET: Final = "zephyrsecret"  # noqa: S105 - test fixture text, not a credential


def _section_body(title: str, sentence: str, sections: int = 8) -> str:
    """A document long enough to split into several chunks (>= three, so a
    Document node forms), every section repeating ``sentence`` so the extractive
    summariser keeps it."""
    body = "\n\n".join(f"## Section {i}\n\n{sentence} (part {i})." for i in range(sections))
    return f"# {title}\n\n{body}"


_DOCS: Final = {
    "auth": (
        "01K1AAAAAA01234567890ABCDE",
        AUTH_REVISION,
        AUTH_ITEM,
        "Gateway authentication policy",
        "approved",
        _section_body(
            "Gateway authentication policy",
            "The gatewayx verifies the gatewayx signature and gatewayx token before any "
            "gatewayx handler runs and the gatewayx audit records the gatewayx decision",
        ),
    ),
    "cache": (
        "01K1BBBBBB01234567890ABCDE",
        "01K1BBBREV01234567890ABCDE",
        CACHE_ITEM,
        "Caching policy",
        "approved",
        _section_body(
            "Caching policy",
            "The read-through memcache warms lazily on deploy and evicts stale memcache "
            "entries by size and a memcache miss falls through to the primary datastore",
        ),
    ),
    "queue": (
        "01K1CCCCCC01234567890ABCDE",
        "01K1CCCREV01234567890ABCDE",
        QUEUE_ITEM,
        "Queue policy",
        "approved",
        _section_body(
            "Queue policy",
            "The broker retries a poisoned message with exponential backoff and parks it "
            "in a dead-letter queue after the broker exhausts every backoff attempt",
        ),
    ),
    "draft": (
        "01K1DDDDDD01234567890ABCDE",
        DRAFT_REVISION,
        DRAFT_ITEM,
        "Unreviewed rotation draft",
        "draft",
        _section_body(
            "Unreviewed rotation draft",
            "The rotationx credential rotationx zephyrsecret rotates rotationx nightly and "
            "the rotationx vault rotationx stores zephyrsecret material for the rotationx job",
        ),
    ),
}


def _migration(  # noqa: PLR0913, PLR0917 - one argument per migration field
    mid: str, rid: str, item: str, slug: str, title: str, status: str
) -> str:
    return f"""apiVersion: theurian.dev/v1
id: {mid}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: {rid}
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""


def _cli(*args: str) -> None:
    from theurian.cli.main import app

    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@dataclass(frozen=True)
class Built:
    """A registered, applied, indexed project, and the index file it published.

    ``search`` goes through the daemon's own tool surface. It is synchronous --
    ``asyncio.run`` under the hood -- because ``index build`` embeds through
    ``asyncio.run`` too and so cannot run inside an already-running loop, which is
    why every test in this module is a plain function rather than a coroutine.
    """

    registry: ProjectRegistry
    index_path: pathlib.Path

    def search(self, query: str, **arguments: Any) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            result = await build_server(self.registry).call_tool(
                "knowledge.search", {"projectId": "demo", "query": query, **arguments}
            )
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                payload: dict[str, Any] = structured
                return payload
            content: Any = result.content  # type: ignore[union-attr]
            loaded: dict[str, Any] = json.loads(content[0].text)
            return loaded

        return asyncio.run(call())

    def capabilities(self) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            result = await build_server(self.registry).call_tool("system.capabilities", {})
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                payload: dict[str, Any] = structured
                return payload
            content: Any = result.content  # type: ignore[union-attr]
            loaded: dict[str, Any] = json.loads(content[0].text)
            return loaded

        return asyncio.run(call())


def _build(where: pathlib.Path, *, raptor: bool, include_unapproved: bool) -> Built:
    root = where / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = where / "datadir"
    monkey = pytest.MonkeyPatch()
    # HOME redirected as well as the data dir: nothing here writes to
    # `~/.claude.json`, but the house rule is to never let a build touch the real
    # machine, and a redirected HOME makes that structural rather than trusted.
    monkey.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkey.setenv("HOME", str(where / "home"))
    (where / "home").mkdir()
    monkey.chdir(root)
    try:
        _cli("init")
        for slug, (mid, rid, item, title, status, body) in _DOCS.items():
            (root / f".theurian/knowledge/architecture/{slug}.md").write_text(body)
            (root / f".theurian/migrations/{mid}-{slug}.yaml").write_text(
                _migration(mid, rid, item, slug, title, status)
            )
        _cli("project", "register")
        _cli("migrate", "apply")
        build_args = ["index", "build"]
        if include_unapproved:
            build_args.append("--include-unapproved")
        if raptor:
            build_args.append("--raptor")
        _cli(*build_args)

        registry = ProjectRegistry.default(data_dir)
        paths = ProjectPaths.of(root)
        payload = read_active_index_pointer(paths).payload
        assert payload is not None, "the build must have published an index pointer"
        index_path = paths.index_for(str(payload["indexBuildId"]))
        assert index_path.is_file(), "the published index file must exist"
    finally:
        monkey.undo()
    return Built(registry, index_path)


@pytest.fixture(scope="module")
def raptor(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """Every document, built ``--include-unapproved --raptor``.

    Module-scoped: a Git tree, four migrations and a forest build cost more than
    the assertions over them. Querying with the default ``includeUnapproved`` sees
    only the approved forest; the draft is present in the file, in its own scope,
    which is exactly what the disclosure tests need.
    """
    return _build(tmp_path_factory.mktemp("raptor"), raptor=True, include_unapproved=True)


@pytest.fixture(scope="module")
def raptor_twin(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """A second, independent ``--raptor`` build of the identical corpus.

    Same project id (the directory is ``demo`` again) and the same rows, so a node
    id -- content-addressed over ``(tree, level, child hashes)`` -- and a node's
    text are the same in both. A different index build id and a different file, so
    a ``raptorPath`` that agreed only because it was read from one file would not
    agree here.
    """
    return _build(tmp_path_factory.mktemp("raptor-twin"), raptor=True, include_unapproved=True)


@pytest.fixture(scope="module")
def plain(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """The identical corpus with no forest -- a chunk-only ``index build``."""
    return _build(tmp_path_factory.mktemp("plain"), raptor=False, include_unapproved=True)


# -- Reading the published forest -------------------------------------------


@dataclass(frozen=True)
class NodeRow:
    node_id: str
    level: int
    text: str
    status: str


def _open(index_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _nodes(index_path: pathlib.Path) -> dict[str, NodeRow]:
    """Every ``nodes`` row of the published build, keyed by id."""
    for connection in _open(index_path):
        return {
            str(r["node_id"]): NodeRow(
                node_id=str(r["node_id"]),
                level=int(r["level"]),
                text=str(r["text"]),
                status=str(r["status"]),
            )
            for r in connection.execute("SELECT node_id, level, text, status FROM nodes")
        }
    raise AssertionError("unreachable")


def _expected_raptor_path(index_path: pathlib.Path, revision_id: str) -> list[dict[str, Any]]:
    """The forest ancestry of ``revision_id``'s leaves, root-to-leaf.

    Walks ``node_derivation`` upward from the revision's chunks to its Document
    node, then to that node's Domain parent, then to the Catalog -- the chain a
    surfaced leaf's ``raptorPath`` must reproduce. Built from the index rather than
    hard-coded, so it *is* the forest and moves with it. Root-to-leaf per
    ``RaptorPathSegment``'s own docstring ("from a catalog root down to a leaf").
    """
    nodes = _nodes(index_path)
    for connection in _open(index_path):
        chunk_ids = [
            str(r["chunk_id"])
            for r in connection.execute(
                "SELECT chunk_id FROM chunks WHERE revision_id = ?", (revision_id,)
            )
        ]
        assert chunk_ids, f"{revision_id} has no chunks to anchor a path"
        placeholders = ",".join("?" * len(chunk_ids))
        document = connection.execute(
            f"SELECT DISTINCT node_id FROM node_derivation "  # noqa: S608 - placeholders only
            f"WHERE source_chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        assert len(document) == 1, f"a leaf belongs to exactly one Document node, got {document}"

        leaf_to_root: list[str] = [str(document[0]["node_id"])]
        while True:
            parent = connection.execute(
                "SELECT node_id FROM node_derivation WHERE source_node_id = ?",
                (leaf_to_root[-1],),
            ).fetchone()
            if parent is None:
                break
            leaf_to_root.append(str(parent["node_id"]))

    return [
        {
            "nodeId": node_id,
            "level": nodes[node_id].level,
            "title": excerpt(nodes[node_id].text),
        }
        for node_id in reversed(leaf_to_root)
    ]


def _validator() -> Draft202012Validator:
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in (
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(SCHEMAS.rglob("*.schema.json"))
        )
    ]
    registry: Registry[Any] = Registry().with_resources(resources)
    schema: dict[str, Any] = json.loads((SCHEMAS / RETRIEVAL_RESULT).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


def _hit(response: dict[str, Any], item_id: str) -> dict[str, Any]:
    for result in response["results"]:
        if result["itemId"] == item_id:
            hit: dict[str, Any] = result
            return hit
    raise AssertionError(f"{item_id} not in {[r['itemId'] for r in response['results']]}")


# -- 1. Traversal: a summary match routes to leaves a leaf search misses -----


def test_a_summary_match_routes_to_sibling_leaves_a_leaf_search_misses(raptor: Built) -> None:
    """FR-R3. A term matching a Domain summary must reach the leaves *under* that
    summary, including siblings that never contain the term -- which is the whole
    of what "search may traverse a summary node" buys over leaf-only retrieval.

    The precondition is asserted from the forest itself, not assumed: the Domain
    node holds the term and neither sibling leaf -- ``caching-policy`` nor
    ``queue-policy`` -- contains it, so this cannot pass because a leaf happened to
    match some substring. Only a summary retriever that actually descends from the
    matched Domain node can route ``gatewayx`` to leaves that never contain it.
    """
    nodes = _nodes(raptor.index_path)
    domain = next(n for n in nodes.values() if n.level == 2 and n.status == "approved")
    assert FOREST_TERM in domain.text.lower(), "precondition: the Domain summary holds the term"

    for connection in _open(raptor.index_path):
        sibling_leaves = connection.execute(
            "SELECT item_id, lower(text) t FROM chunks WHERE item_id IN (?, ?)",
            (CACHE_ITEM, QUEUE_ITEM),
        ).fetchall()
    assert sibling_leaves, "precondition: the sibling documents were indexed"
    assert all(FOREST_TERM not in row["t"] for row in sibling_leaves), (
        "precondition: no sibling leaf contains the term, so only the forest can reach it"
    )

    response = raptor.search(FOREST_TERM)
    surfaced = {r["itemId"] for r in response["results"]}

    assert CACHE_ITEM in surfaced, (
        "the forest must route the Domain match down to its cache sibling"
    )
    assert QUEUE_ITEM in surfaced, "and to its queue sibling"


def test_a_summary_node_is_never_itself_a_result_row(raptor: Built) -> None:
    """ADR-0008 decision 8: summary nodes are routing-only. A response may be
    *shaped by* a traversed node, but a node id must never occupy the ``itemId`` or
    ``revisionId`` of a result -- only a gate-cleared leaf revision may.

    A node has no ``(item, current revision)`` pair for
    ``CanonicalVisibility._may_surface`` to clear, so publishing one would mean a
    second clearance rule beside the one every result goes through -- the standing
    invariant this test guards now that traversal reads the forest.
    """
    node_ids = set(_nodes(raptor.index_path))
    assert node_ids, "precondition: the forest must hold summary nodes for this to mean anything"
    response = raptor.search(FOREST_TERM)
    assert response["results"], "precondition: the query must surface something to inspect"

    for connection in _open(raptor.index_path):
        real_revisions = {
            str(r["revision_id"]) for r in connection.execute("SELECT revision_id FROM chunks")
        }

    for result in response["results"]:
        assert result["revisionId"] in real_revisions, "every result is a real chunk-backed leaf"
        assert result["revisionId"] not in node_ids
        assert result["itemId"] not in node_ids


# -- 2. raptorPath on a surfaced leaf ----------------------------------------


def test_a_surfaced_leaf_carries_its_forest_ancestry_as_raptor_path(raptor: Built) -> None:
    """FR-R5, ADR-0008 decision 8. A surfaced leaf must publish the summary path
    above it, one ``{nodeId, level, title}`` per ancestor from the catalog root
    down to the leaf, matching the actual ``nodes`` rows -- ``title`` being the
    node's text as a single bounded line (``excerpt``), the summariser's output on
    the wire.

    The expected path is reconstructed from the published forest, so this pins the
    ids, the levels, the titles *and* their order, not merely that a key is
    present.
    """
    expected = _expected_raptor_path(raptor.index_path, AUTH_REVISION)
    assert [seg["level"] for seg in expected] == [2, 1], (
        "the fixture must give auth a Domain->Document ancestry to make the ordering bite"
    )

    hit = _hit(raptor.search(FOREST_TERM), AUTH_ITEM)

    assert hit["raptorPath"] == expected


def test_a_surfaced_leaf_with_a_path_still_validates_against_the_published_schema(
    raptor: Built,
) -> None:
    """The result schema is ``additionalProperties: false`` (#123), so the real
    emitted hit and its published schema must stay in step: a payload emitting
    ``raptorPath`` while the schema forbids it, or the reverse, fails here.
    """
    hit = _hit(raptor.search(FOREST_TERM), AUTH_ITEM)
    assert "raptorPath" in hit, "the ranked forest hit must carry the field the schema now declares"

    _validator().validate(hit)


def test_a_chunk_only_index_carries_no_raptor_path(plain: Built) -> None:
    """``raptorPath`` is per-index: a build with no forest has no ancestry to
    publish, so no hit carries the key. Emitting an empty one instead would be a
    field a client learns to ignore -- and would say a forest was consulted when
    none exists.
    """
    response = plain.search(FOREST_TERM)
    assert response["results"], "the plain corpus must answer this query"

    for result in response["results"]:
        assert "raptorPath" not in result


# -- 3. The gate holds: a withheld sibling leaks no raptorPath ---------------


def test_a_withheld_documents_text_never_enters_a_surfaced_items_raptor_path(
    raptor: Built,
) -> None:
    """SEC-13, T-15. The disclosure closure: an approved item's ``raptorPath`` is
    built only from ancestors in its own six-component scope, and status is a scope
    component, so a draft sibling can never be an ancestor of an approved leaf. Its
    summary's title therefore cannot ride out on an approved item's path.

    Were the forest ever built ignoring the status-scope boundary -- a draft and an
    approved item sharing one Domain tree -- the approved Domain summary would hold
    the draft's ``rotationx``/``zephyrsecret`` and this would fail.
    """
    hit = _hit(raptor.search(FOREST_TERM), AUTH_ITEM)
    assert hit["raptorPath"], "an approved forest hit must carry a path for the closure to bind"

    joined = " ".join(segment["title"] for segment in hit["raptorPath"]).lower()
    assert DRAFT_ROUTING_TERM not in joined, (
        "a draft sibling's summary must not reach an approved path"
    )
    assert DRAFT_SECRET not in joined


# -- 6. Routing cannot resurrect withheld content ----------------------------


def test_routing_over_an_unapproved_forest_cannot_resurrect_a_withheld_leaf(
    raptor: Built,
) -> None:
    """SEC-13, T-15 -- the security spine of this CL. The forest was built
    ``--include-unapproved``, so it holds a draft-scope summary node over the
    withheld document; a default query routes to it, and the leaf below it must
    still be gated out. Routing changes which leaves are candidates, never whether
    a gated row surfaces.

    The precondition reads the forest to prove the scenario is real -- the draft's
    summary node holds the routing term and the secret, so a descent that skipped
    the gate would surface them.
    """
    nodes = _nodes(raptor.index_path)
    draft_node = next(
        (n for n in nodes.values() if n.status == "draft" and DRAFT_ROUTING_TERM in n.text.lower()),
        None,
    )
    assert draft_node is not None, "precondition: a draft-scope summary node routes on the term"
    assert DRAFT_SECRET in draft_node.text.lower(), "precondition: and it holds the secret to leak"

    response = raptor.search(DRAFT_ROUTING_TERM)
    serialized = json.dumps(response)

    assert all(r["itemId"] != DRAFT_ITEM for r in response["results"]), (
        "the withheld leaf must not surface"
    )
    assert DRAFT_SECRET not in serialized, "no draft body text may reach a default caller"
    assert DRAFT_REVISION not in serialized, "not even the withheld revision id"


def test_the_same_query_with_and_without_drafts_differs_only_by_the_draft(raptor: Built) -> None:
    """The other half of the gate: the draft *is* reachable, so the withholding is
    real and not an artefact of the term matching nothing. With ``includeUnapproved``
    the same routing term surfaces the draft; by default it does not. If both
    answered the same, the test above would be proving nothing.
    """
    default = {r["itemId"] for r in raptor.search(DRAFT_ROUTING_TERM)["results"]}
    with_drafts = {
        r["itemId"] for r in raptor.search(DRAFT_ROUTING_TERM, includeUnapproved=True)["results"]
    }

    assert DRAFT_ITEM not in default
    assert DRAFT_ITEM in with_drafts, "the draft must be reachable when the caller asks for drafts"


# -- 4. capabilities.raptor is true ------------------------------------------


def test_capabilities_reports_raptor_supported(raptor: Built) -> None:
    """A client learns per feature what it may ask for. Once an answer path reads
    the forest, ``system.capabilities.raptor`` must be ``True`` -- otherwise a
    client reading ``False`` never asks for a ``raptorPath`` the server now emits.

    The capability and the retrieval behaviour it advertises must never drift
    apart -- a stale ``True`` or ``False`` here is a lie a client trusts.
    """
    assert raptor.capabilities()["capabilities"]["raptor"] is True


def test_capabilities_raptor_is_a_server_property_not_a_per_index_one(plain: Built) -> None:
    """``raptor`` says what this *build of Core* supports, independent of whether a
    given project has a forest -- the same shape ``hybridRetrieval`` already has.
    A client degrades on the capability and discovers per response, through
    ``raptorPath``'s presence, whether the index it queried actually has one.

    Pinned against a chunk-only project so the two readings are told apart: the
    server supports RAPTOR even though this index has no forest.
    """
    assert plain.capabilities()["capabilities"]["raptor"] is True


# -- 5. title is deterministic across builds ---------------------------------


def test_raptor_path_is_identical_across_two_independent_builds(
    raptor: Built, raptor_twin: Built
) -> None:
    """ADR-0008 decision 9's two-corpus equality, seen from the wire. A node id is
    content-addressed and a node's text is a pure function of its children, so two
    independent ``--raptor`` builds of one corpus publish the *same* ``raptorPath``
    for the same leaf -- same ids, same titles, same order. A path that varied per
    build would be one more thing a caller has to watch move (SEC-13).

    Determinism holds across two independently-built indexes, not just within one,
    which is what rules out an accidental agreement from reading a single file.
    """
    here = _hit(raptor.search(FOREST_TERM), AUTH_ITEM)["raptorPath"]
    there = _hit(raptor_twin.search(FOREST_TERM), AUTH_ITEM)["raptorPath"]

    assert here == there
    assert here, "and it must be non-empty, or 'identical' is vacuous"
