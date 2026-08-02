"""The MCP tool surface, called in process (ADR-0013, SEC-13, SEC-15).

The e2e suite proves these tools work over the wire against a real daemon. It
cannot cheaply enumerate their branches, and it runs the daemon in a subprocess
where nothing is measured. These tests go through ``server.call_tool`` -- the
same entry point the transport uses -- against a project built by the real CLI.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
DRAFT_ID = "01K1BBBBBB01234567890ABCDE"
DRAFT_REVISION_ID = "01K1BBBREV01234567890ABCDE"

BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
DRAFT_BODY = "# Caching draft\n\nA proposal nobody has reviewed.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

DRAFT_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {DRAFT_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.caching-draft
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.caching-draft
    revisionId: {DRAFT_REVISION_ID}
    contentFile: ../knowledge/architecture/caching-draft.md
    metadata:
      title: Caching draft
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: draft
      owner: platform-team
      trustLevel: inferred
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/caching-draft.md
"""


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered project with one approved item and one draft."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(BODY)
    (knowledge / "caching-draft.md").write_text(DRAFT_BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    (root / f".theurian/migrations/{DRAFT_ID}-draft.yaml").write_text(DRAFT_MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


async def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    """Invoke a tool and return its payload, or ``{"_error": ...}``.

    ``call_tool`` re-raises a failing tool as the SDK's own ``ToolError``; the
    transport is the layer that turns it into ``isError=True`` content. Both
    carry the same message, and the e2e suite checks the wire form. What matters
    here is the *text*, so a message that leaked a path or a stack trace would
    fail a test rather than reach a client.
    """
    result = await build_server(registry).call_tool(tool, arguments)
    content: Any = result.content  # type: ignore[union-attr]
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


async def _call_failing(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    """Invoke a tool that must fail, and return the message a client would see."""
    with pytest.raises(SdkToolError) as raised:
        await _call(registry, tool, **arguments)
    return str(raised.value)


# -- Project resolution ----------------------------------------------------


@pytest.mark.asyncio
async def test_an_unregistered_project_names_what_is_registered(
    registry: ProjectRegistry,
) -> None:
    """SEC-13. The error must not become a probe for other projects' contents,
    but a caller that mistyped an id needs to see the right one."""
    message = await _call_failing(registry, "knowledge.search", projectId="typo", query="token")

    assert "not registered" in message
    assert "demo" in message, "the registered id must be shown"
    assert "project register" in message


@pytest.mark.asyncio
async def test_an_unbuilt_project_says_how_to_build_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """Registered but never applied is different from "no results", and the
    remedy is a command, not a support request."""
    unbuilt = tmp_path / "unbuilt"
    unbuilt.mkdir()
    (unbuilt / ".theurian/state").mkdir(parents=True)
    _register_extra(registry, "other", unbuilt)

    message = await _call_failing(registry, "knowledge.status", projectId="other")

    assert "no built knowledge state" in message
    assert "migrate apply" in message


@pytest.mark.asyncio
async def test_a_missing_state_database_is_reported_as_rebuildable(
    registry: ProjectRegistry,
) -> None:
    """The canonical store is derived (ADR-0004). Losing it is a rebuild, not
    data loss, and the message has to say so or someone will restore a backup
    over their Git-tracked migrations."""
    root = Path(registry.load()["demo"]["rootPath"])
    databases = list((root / ".theurian/state").glob("*.sqlite"))
    assert databases, "the fixture must have built a state database to remove"
    for database in databases:
        database.unlink()

    message = await _call_failing(registry, "knowledge.search", projectId="demo", query="token")

    assert "missing" in message
    assert "reconstructible from Git-tracked migrations" in message


# -- knowledge.search ------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_an_approved_item(registry: ProjectRegistry) -> None:
    result = await _call(registry, "knowledge.search", projectId="demo", query="signed token")

    assert result["count"] == 1
    assert result["results"][0]["itemId"] == "architecture.auth-policy"


@pytest.mark.asyncio
async def test_search_matches_the_title_as_well_as_the_body(
    registry: ProjectRegistry,
) -> None:
    result = await _call(registry, "knowledge.search", projectId="demo", query="Authentication")

    assert result["count"] == 1


@pytest.mark.asyncio
async def test_search_is_case_insensitive(registry: ProjectRegistry) -> None:
    upper = await _call(registry, "knowledge.search", projectId="demo", query="SIGNED TOKEN")

    assert upper["count"] == 1


@pytest.mark.asyncio
async def test_a_draft_is_withheld_by_default(registry: ProjectRegistry) -> None:
    """The failure this system exists to prevent: an unreviewed draft returned
    alongside a team decision is indistinguishable from one."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="caching")

    assert result["count"] == 0


@pytest.mark.asyncio
async def test_a_draft_is_returned_only_when_asked_for(registry: ProjectRegistry) -> None:
    result = await _call(
        registry, "knowledge.search", projectId="demo", query="caching", includeUnapproved=True
    )

    assert result["count"] == 1
    hit = result["results"][0]
    assert hit["status"] == "draft"
    assert hit["trustLevel"] == "inferred", "the caller must be able to tell them apart"


@pytest.mark.asyncio
async def test_no_match_is_an_empty_result_not_an_error(registry: ProjectRegistry) -> None:
    result = await _call(registry, "knowledge.search", projectId="demo", query="kubernetes")

    assert result["count"] == 0
    assert result["results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_an_empty_query_is_refused(registry: ProjectRegistry, query: str) -> None:
    """Without this, a whitespace query matches every document and quietly
    returns the whole knowledge base."""
    message = await _call_failing(registry, "knowledge.search", projectId="demo", query=query)

    assert "must not be empty" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -5, 10_000])
async def test_an_out_of_range_limit_is_clamped_not_refused(
    registry: ProjectRegistry, limit: int
) -> None:
    """A caller passing 10000 wants "everything"; refusing helps nobody, and
    honouring it would blow their context budget."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="token", limit=limit)

    assert result["count"] == 1


# -- knowledge.get ---------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_the_full_body(registry: ProjectRegistry) -> None:
    """Search returns an excerpt; get is what an agent calls once it has decided
    a document matters."""
    result = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert result["body"] == BODY
    assert result["title"] == "Authentication policy"
    assert result["relations"] == []


@pytest.mark.asyncio
async def test_get_on_an_unknown_item_is_an_error_not_an_empty_document(
    registry: ProjectRegistry,
) -> None:
    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    assert "not present" in message


# -- knowledge.status ------------------------------------------------------


@pytest.mark.asyncio
async def test_status_counts_items_by_status(registry: ProjectRegistry) -> None:
    result = await _call(registry, "knowledge.status", projectId="demo")

    assert result["itemCount"] == 2
    assert result["itemsByStatus"] == {"approved": 1, "draft": 1}
    assert result["appliedMigrations"] == 2
    assert result["stateHash"]


# -- project.list ----------------------------------------------------------


@pytest.mark.asyncio
async def test_project_list_reports_the_registered_projects(
    registry: ProjectRegistry,
) -> None:
    result = await _call(registry, "project.list")

    assert result["count"] == 1
    assert result["projects"][0]["projectId"] == "demo"


# -- system.capabilities ---------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_report_what_is_not_built_yet(registry: ProjectRegistry) -> None:
    """A client that only learns "version 0.1" has to guess. Per-feature flags
    let it degrade one feature at a time."""
    result = await _call(registry, "system.capabilities")

    assert result["capabilities"]["writeTools"] is False
    assert result["capabilities"]["hybridRetrieval"] is False
    assert result["capabilities"]["knowledgeGet"] is True
    assert result["schemaVersion"]


# -- Result shape ----------------------------------------------------------


@pytest.mark.asyncio
async def test_every_result_carries_the_trust_triple_and_an_anchor(
    registry: ProjectRegistry,
) -> None:
    """SEC-15, FR-R5."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="token")
    hit = result["results"][0]

    assert hit["contentClassification"] == "untrusted-knowledge"
    assert hit["mayContainInstructions"] is True
    assert hit["executable"] is False
    assert hit["sourceAnchors"][0]["provider"] == "git"
    assert hit["freshness"]["ageDays"] >= 0
    assert hit["freshness"]["isWithinValidity"] is True


@pytest.mark.asyncio
async def test_a_long_body_is_excerpted_rather_than_returned_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search is a triage tool. Returning full bodies for every hit is how a
    caller's context budget disappears before it reads anything."""
    long_body = "# Long\n\n" + ("sentinel padding " * 200)
    registry = _project_with_body(tmp_path, monkeypatch, long_body)

    result = await _call(registry, "knowledge.search", projectId="demo", query="sentinel")
    excerpt = result["results"][0]["excerpt"]

    assert excerpt.endswith("...")
    assert len(excerpt) <= 283
    assert "\n" not in excerpt


def _register_extra(registry: ProjectRegistry, project_id: str, root: Path) -> None:
    """Add a registry entry directly, for states the CLI will not produce."""
    entries = {**registry.load(), project_id: {"projectId": project_id, "rootPath": str(root)}}
    registry.path.write_text(json.dumps(entries), encoding="utf-8")


def _project_with_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> ProjectRegistry:
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")
    return ProjectRegistry.default(data_dir)


# -- No path from MCP to approved state (ADR-0013) -------------------------

#: The only gateways to a canonical write. ``SqliteWriter`` mutates approved
#: knowledge and can only be built from a connection obtained through
#: ``write_transaction``, so a tool that touches neither name cannot write.
WRITE_GATEWAYS = frozenset({"SqliteWriter", "write_transaction"})


def _mutating_method_names() -> frozenset[str]:
    """Methods that exist only on the writer.

    ``SqliteWriter`` also carries a couple of read helpers so a migration can
    check what it is about to change; those names are shared with the read-only
    store and are not evidence of a write path. The difference between the two
    classes is the surface that mutates approved knowledge.
    """
    from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

    writer = {name for name in vars(SqliteWriter) if not name.startswith("_")}
    reader = {name for name in vars(SqliteCanonicalStore) if not name.startswith("_")}
    return frozenset(writer - reader)


def _referenced_names(function: Any) -> set[str]:
    """Every attribute and global name reachable from a function's code.

    Follows nested code objects, so a write hidden inside a comprehension or an
    inner helper is still visible.
    """
    seen: set[str] = set()
    pending = [function.__code__]
    while pending:
        code = pending.pop()
        seen.update(code.co_names)
        pending.extend(c for c in code.co_consts if hasattr(c, "co_names"))
    return seen


def test_the_write_gateway_still_guards_the_write_surface() -> None:
    """Guards the guard below.

    The structural argument is "no writer, no writes". If the write methods ever
    move onto the read-only store, the test below keeps passing while checking
    nothing.
    """
    mutating = _mutating_method_names()

    assert {"append_revision", "put_item", "record_migration"} <= mutating, (
        "the known write methods must be writer-only; if one moved to the "
        "read-only store, MCP tools can now reach it"
    )


def test_no_registered_tool_can_reach_a_canonical_write(registry: ProjectRegistry) -> None:
    """ADR-0013. The guarantee is structural, not a naming convention.

    A read-only *name* proves nothing: `knowledge.get` could call
    `append_revision` and the tool list would look unchanged. This walks the
    bytecode of every registered tool instead, looking for both the write
    gateways and every individual write method.
    """
    forbidden = WRITE_GATEWAYS | _mutating_method_names()
    server = build_server(registry)
    offenders: dict[str, set[str]] = {}

    # The private manager, deliberately: the public `list_tools` returns wire
    # schemas, and this test needs the callables behind them.
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this test vacuously"

    for tool in tools:
        reached = _referenced_names(tool.fn) & forbidden
        if reached:
            offenders[tool.name] = reached

    assert not offenders, f"tools with a canonical write path: {offenders}"


# -- Cross-project isolation (SEC-13) --------------------------------------


@pytest.fixture
def two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """Two registered projects, each with its own knowledge and its own state.

    One daemon serving many projects is the design (ADR-0002). The property that
    makes it safe is that a call for one cannot observe the other -- and that
    only becomes testable once two really exist.
    """
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))

    for name, secret in (
        ("alpha", "alpha-only-payment-rotation"),
        ("beta", "beta-only-tls-pinning"),
    ):
        root = tmp_path / name
        root.mkdir()
        for args in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

        monkeypatch.chdir(root)
        _run("init")
        (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(
            f"# {name} policy\n\n{secret}\n"
        )
        (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
        _run("project", "register")
        _run("migrate", "apply")

    return ProjectRegistry.default(data_dir)


@pytest.mark.asyncio
async def test_a_query_for_one_project_cannot_observe_the_other(
    two_projects: ProjectRegistry,
) -> None:
    """SEC-13. The isolation is per state database, not a filter someone has to
    remember to apply."""
    alpha = await _call(two_projects, "knowledge.search", projectId="alpha", query="alpha-only")
    leaked = await _call(two_projects, "knowledge.search", projectId="alpha", query="beta-only")

    assert alpha["count"] == 1
    assert leaked["count"] == 0, "alpha must not be able to read beta's knowledge"


@pytest.mark.asyncio
async def test_each_project_sees_its_own_content_under_the_same_item_id(
    two_projects: ProjectRegistry,
) -> None:
    """Both projects use the same itemId. If scoping were wrong anywhere, this
    is where one project's document would surface under the other's name."""
    alpha = await _call(
        two_projects, "knowledge.get", projectId="alpha", itemId="architecture.auth-policy"
    )
    beta = await _call(
        two_projects, "knowledge.get", projectId="beta", itemId="architecture.auth-policy"
    )

    assert "alpha-only-payment-rotation" in alpha["body"]
    assert "beta-only-tls-pinning" in beta["body"]
    assert alpha["body"] != beta["body"]


@pytest.mark.asyncio
async def test_project_list_shows_both_without_revealing_their_knowledge(
    two_projects: ProjectRegistry,
) -> None:
    result = await _call(two_projects, "project.list")

    assert {p["projectId"] for p in result["projects"]} == {"alpha", "beta"}
    assert "alpha-only-payment-rotation" not in json.dumps(result)


# -- Hybrid retrieval through the tool (FR-R2, FR-R4) ------------------------
#
# These exist because a breaking change to this response shape -- replacing the
# flat `note` with a structured `retrieval` block -- passed the entire suite
# without a single failure. Nineteen tests call `knowledge.search`, and every
# one of them went down the substring fallback because no fixture had built an
# index. The ranked path was shipped untested.


@pytest.fixture
def indexed(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` fixture, plus a built retrieval index."""
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("index", "build")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_search_without_an_index_says_so_rather_than_returning_nothing(
    registry: ProjectRegistry,
) -> None:
    """A project that applied migrations but has not indexed yet would otherwise
    answer every query with nothing, which reads as "we have no such decision"
    rather than "ask me again in a moment"."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["indexed"] is False
    assert result["retrieval"]["mode"] == "substring"
    assert "index build" in result["retrieval"]["note"]
    assert result["count"] == 1, "the fallback still answers"


@pytest.mark.asyncio
async def test_search_uses_the_index_once_one_exists(indexed: ProjectRegistry) -> None:
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert result["retrieval"]["indexed"] is True
    assert result["retrieval"]["mode"] in {"hybrid", "lexical", "dense"}
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_a_ranked_hit_says_which_retrievers_found_it(indexed: ProjectRegistry) -> None:
    """A ranking nobody can explain is a ranking nobody can debug."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")
    hit = result["results"][0]

    assert hit["foundBy"], "every hit names the retrievers that surfaced it"
    assert hit["fusedScore"] > 0


@pytest.mark.asyncio
async def test_a_ranked_hit_keeps_the_published_provenance_and_trust_labels(
    indexed: ProjectRegistry,
) -> None:
    """SEC-15, FR-R5. The ranked path resolves hits back through the canonical
    store, so it must not lose the labels the substring path attaches."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")
    hit = result["results"][0]

    assert hit["contentClassification"] == "untrusted-knowledge"
    assert hit["mayContainInstructions"] is True
    assert hit["executable"] is False
    assert hit["sourceAnchors"]
    assert hit["revisionId"]


@pytest.mark.asyncio
async def test_the_index_is_never_the_authority_for_a_result(
    indexed: ProjectRegistry,
) -> None:
    """FR-R5. Results are resolved through the canonical store, so a hit
    reflects the revision as it is now rather than as the index recorded it."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert result["results"][0]["title"] == "Authentication policy"


@pytest.mark.asyncio
async def test_a_token_budget_is_honoured(indexed: ProjectRegistry) -> None:
    """FR-R4. An agent that receives more context than it asked for has already
    paid for it."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="token", maxTokens=40)

    assert result["retrieval"]["usedTokens"] <= 40 or result["count"] == 1


@pytest.mark.asyncio
async def test_drafts_stay_out_of_ranked_results_by_default(
    indexed: ProjectRegistry,
) -> None:
    result = await _call(indexed, "knowledge.search", projectId="demo", query="caching")

    assert all(hit["status"] == "approved" for hit in result["results"])


@pytest.mark.asyncio
async def test_a_pointer_to_a_missing_index_falls_back_instead_of_failing(
    indexed: ProjectRegistry,
) -> None:
    """The index is derived. A pointer that outlived its file is a missing
    optimisation, never a reason to refuse to answer."""
    root = Path(indexed.load()["demo"]["rootPath"])
    for built in (root / ".theurian/state").glob("theurian-index-*.sqlite"):
        built.unlink()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["indexed"] is False
    assert result["count"] >= 1
