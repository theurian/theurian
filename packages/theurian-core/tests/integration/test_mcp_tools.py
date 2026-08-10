"""The MCP tool surface, called in process (ADR-0013, SEC-13, SEC-15).

The e2e suite proves these tools work over the wire against a real daemon. It
cannot cheaply enumerate their branches, and it runs the daemon in a subprocess
where nothing is measured. These tests go through ``server.call_tool`` -- the
same entry point the transport uses -- against a project built by the real CLI.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, ProjectRegistry, read_active_state
from theurian.application.retrieval_service import CANDIDATE_DEPTH, FIRST_PASS_DEPTH
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.ranking import Ranked
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore
from theurian.mcp.tools import MAX_RESULTS

pytestmark = pytest.mark.integration

runner = CliRunner()


class _NothingWithheld:
    """A `Visibility` that withholds nothing.

    The tests below that use it are asserting a *precondition*: that the index
    still holds the retracted text and the retrievers still match it. That is
    what makes the response-level assertions mean something -- an answer that
    omits a secret because no retriever could find it proves nothing about the
    gate. Ranked through the real canonical store, these queries reach nothing,
    which is the fix; ranked through this, they reach the document, which is the
    threat the fix answers.
    """

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(ranked)


NOTHING_WITHHELD = _NothingWithheld()

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

#: A second and third approved item, so a budget has something to drop.
EXTRA_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1{letter}AAAAA01234567890ABCDE
createdAt: 2026-08-02T12:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: 01K1{letter}AAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
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


#: `createItem` with no `upsertRevision` beside it. A normal, supported shape:
#: an item may be declared in one migration and given content in a later one,
#: and it sits in the store with `currentRevisionId: null` until then.
PLACEHOLDER_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1HAAAAA01234567890ABCDE
createdAt: 2026-08-02T14:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.reserved-slot
    kind: architecture
    namespace: backend
    owner: platform-team
"""


@pytest.fixture
def with_a_contentless_item(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` project plus one item that has no revision yet."""
    root = Path(registry.load()["demo"]["rootPath"])
    (root / ".theurian/migrations/01K1HAAAAA01234567890ABCDE-reserve.yaml").write_text(
        PLACEHOLDER_MIGRATION
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_an_item_with_no_revision_yet_is_skipped_rather_than_crashing(
    with_a_contentless_item: ProjectRegistry,
) -> None:
    """`createItem` before `upsertRevision` leaves `currentRevisionId` null.

    Every result is built from a revision, so an item that has none has nothing
    to return — but it is still in `list_items`, still `draft`, and therefore
    still walked by the scan whenever a caller passes `includeUnapproved`. The
    guard has to be a skip: dereferencing the null pointer would take the whole
    search down for every caller, over an item nobody asked about.

    `includeUnapproved` is required to reach it at all, because `createItem`
    files the item as a draft.
    """
    result = await _call(
        with_a_contentless_item,
        "knowledge.search",
        projectId="demo",
        query="caching",
        includeUnapproved=True,
    )

    assert result["count"] == 1, "the search still answers"
    assert result["results"][0]["itemId"] == "architecture.caching-draft"


@pytest.mark.asyncio
async def test_the_contentless_item_really_is_in_the_store(
    with_a_contentless_item: ProjectRegistry,
) -> None:
    """Guards the guard. If the migration had not applied, the test above would
    be walking a two-item store and asserting nothing about the branch."""
    result = await _call(with_a_contentless_item, "knowledge.status", projectId="demo")

    assert result["itemCount"] == 3
    assert result["itemsByStatus"]["draft"] == 2, "the reserved slot is one of them"


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


#: One item in each status this tool refuses to count, plus the two the
#: `registry` fixture already holds.
#:
#: `knowledge.status` had no retired item anywhere in its fixtures, so the
#: ``status not in SURFACEABLE_STATUSES`` branch never fired and replacing it
#: with ``item.status.value == ""`` passed the whole suite. All three excluded
#: statuses are here rather than one, because they are excluded for one reason
#: -- `knowledge.get` answers "withheld" and "absent" identically (SEC-13), and
#: a count is the same answer as a number -- and a corpus holding one of them
#: could not tell whether the other two were still excluded.
#:
#: Each arrives by the operation that really produces it: `deprecateItem` for
#: `deprecated`, and a revision declaring the status for `superseded` and
#: `rejected`. Writing all three as revision metadata would have left
#: `deprecateItem`'s own path unrepresented.
RETIRED_MIGRATION_ID = "01K1DDDDDD01234567890ABCDE"
RETIRED_BODY = "# Retired\n\nContent nobody outside the team may be told exists.\n"

RETIRED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {RETIRED_MIGRATION_ID}
createdAt: 2026-08-02T16:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.retired-gateway
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.retired-gateway
    revisionId: 01K1DDDGWA01234567890ABCDE
    contentFile: ../knowledge/architecture/retired-gateway.md
    metadata:
      title: Retired gateway
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/retired-gateway.md
  - op: deprecateItem
    itemId: architecture.retired-gateway
    reason: replaced by the edge proxy
  - op: createItem
    itemId: architecture.superseded-sessions
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.superseded-sessions
    revisionId: 01K1DDDSSN01234567890ABCDE
    contentFile: ../knowledge/architecture/superseded-sessions.md
    metadata:
      title: Superseded sessions
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: superseded
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/superseded-sessions.md
  - op: createItem
    itemId: architecture.rejected-store
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.rejected-store
    revisionId: 01K1DDDRST01234567890ABCDE
    contentFile: ../knowledge/architecture/rejected-store.md
    metadata:
      title: Rejected store
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/rejected-store.md
"""


@pytest.fixture
def with_retired_items(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` project, plus one deprecated, one superseded, one rejected."""
    root = Path(registry.load()["demo"]["rootPath"])
    knowledge = root / ".theurian/knowledge/architecture"
    for slug in ("retired-gateway", "superseded-sessions", "rejected-store"):
        (knowledge / f"{slug}.md").write_text(RETIRED_BODY)
    (root / f".theurian/migrations/{RETIRED_MIGRATION_ID}-retire.yaml").write_text(
        RETIRED_MIGRATION
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


def _stored_statuses(registry: ProjectRegistry) -> dict[str, str]:
    """Every item in the canonical store, mapped to the status it really holds."""
    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    context = RequestContext(project_id=ProjectId("demo"))
    with SqliteCanonicalStore(paths.state / active.database_filename) as store:
        return {item.item_id.value: item.status.value for item in store.list_items(context)}


@pytest.mark.asyncio
async def test_the_retired_items_really_reach_the_canonical_store(
    with_retired_items: ProjectRegistry,
) -> None:
    """Guards the guard. Without this, a migration that stopped applying would
    turn the exclusion test below into an assertion about a two-item store --
    which is exactly the state that let the exclusion be deleted unnoticed."""
    stored = _stored_statuses(with_retired_items)

    assert stored == {
        "architecture.auth-policy": "approved",
        "architecture.caching-draft": "draft",
        "architecture.retired-gateway": "deprecated",
        "architecture.superseded-sessions": "superseded",
        "architecture.rejected-store": "rejected",
    }


@pytest.mark.asyncio
async def test_retired_items_are_absent_from_every_published_count(
    with_retired_items: ProjectRegistry,
) -> None:
    """SEC-13, T-17. A count is an answer to the question the error message refuses.

    `knowledge.get` deliberately says the same thing about a withheld id and an
    absent one, so a caller cannot confirm that a retired id exists. Counting the
    retired items here answers that question with a number instead, and does it
    without the caller needing to guess an id at all.

    Asserted as an exact mapping rather than as "deprecated is not a key": a
    breakdown that reported them under a different label, or collapsed them into
    an `other` bucket, would leak the same quantity.
    """
    result = await _call(with_retired_items, "knowledge.status", projectId="demo")

    assert result["itemsByStatus"] == {"approved": 1, "draft": 1}
    assert result["itemCount"] == 2


@pytest.mark.asyncio
async def test_the_item_count_is_the_sum_of_the_published_breakdown(
    with_retired_items: ProjectRegistry,
) -> None:
    """The subtraction the filtered breakdown would otherwise invite.

    Reporting the true total beside a filtered breakdown leaks the withheld count
    exactly, by subtraction -- so `itemCount` is defined as the sum of what is
    published and not as the store's size. Pinned separately from the exclusion
    itself because the two fail independently: this one holds while
    `itemsByStatus` is correct and `itemCount` is `len(items)`.
    """
    result = await _call(with_retired_items, "knowledge.status", projectId="demo")
    stored = _stored_statuses(with_retired_items)

    assert result["itemCount"] == sum(result["itemsByStatus"].values())
    assert result["itemCount"] < len(stored), (
        "the store must be larger than the published count, or this asserts nothing"
    )


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
async def test_capabilities_report_what_is_and_is_not_built(registry: ProjectRegistry) -> None:
    """A client that only learns "version 0.1" has to guess. Per-feature flags
    let it degrade one feature at a time.

    This assertion previously claimed `hybridRetrieval is False` and kept
    passing after hybrid retrieval shipped, which is how a capability
    declaration and its implementation drift apart unnoticed.
    """
    result = await _call(registry, "system.capabilities")

    assert result["capabilities"]["writeTools"] is False
    assert result["capabilities"]["hybridRetrieval"] is True
    assert result["capabilities"]["knowledgeGet"] is True
    assert result["capabilities"]["raptor"] is False, "not built yet, and says so"
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


@pytest.fixture(params=["indexed", "registry"], ids=["ranked", "fallback"])
def either_answer_path(request: pytest.FixtureRequest) -> tuple[ProjectRegistry, bool]:
    """A project with an index, and one without, and which of the two it is.

    A fixture rather than a boolean parameter because `theurian index build`
    embeds through `asyncio.run`, which raises inside an already-running loop —
    so an async test cannot build one in its own body.
    """
    chosen: ProjectRegistry = request.getfixturevalue(request.param)
    return chosen, request.param == "indexed"


@pytest.mark.asyncio
async def test_one_read_of_the_state_pointer_serves_the_whole_request(
    either_answer_path: tuple[ProjectRegistry, bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-R5. `snapshotId` must name the state the results actually came from.

    Every tool used to read `.theurian/state/active.json` twice: once to choose
    the database, and again to report which canonical state answered. `migrate
    apply` swaps that pointer atomically, so a request landing between the two
    read the old database and named the new hash — a false answer to the one
    question the field exists to answer. The second read could also find nothing
    at all, which is why `snapshotId` had to admit `null`.

    Proved by removing the pointer the instant the first read returns, rather
    than by counting calls: a counter only watches the name it was attached to,
    and the read that came back was reached through a different import. Nothing
    can read the file after this, on any path, so a non-empty `snapshotId` means
    the value was carried rather than re-fetched.

    Run on both answer paths. They report the field for the same reason and used
    to fetch it in two different places.
    """
    from theurian.application.project_service import read_active_state
    from theurian.mcp import tools

    registry, built = either_answer_path

    def read_and_remove(paths: Any) -> Any:
        state = read_active_state(paths)
        paths.active_pointer.unlink(missing_ok=True)
        return state

    monkeypatch.setattr(tools, "read_active_state", read_and_remove)

    result = await _call(registry, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["indexed"] is built, "both answer paths must be covered"
    assert result["retrieval"]["snapshotId"], "the state that answered, carried not re-read"


@pytest.mark.asyncio
async def test_search_and_status_name_the_same_canonical_state(
    indexed: ProjectRegistry,
) -> None:
    """The point of publishing `snapshotId` at all.

    A caller holding a `knowledge.status` response must be able to tell whether a
    search answered from that same state without a third call, so the two strings
    have to be the identical value rather than two renderings of one idea.
    """
    status = await _call(indexed, "knowledge.status", projectId="demo")
    search = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert search["retrieval"]["snapshotId"] == status["stateHash"]


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


@pytest.fixture
def indexed_corpus(registry: ProjectRegistry) -> ProjectRegistry:
    """`indexed`, plus two more approved items, then a build.

    A budget test needs something a budget can drop. The `registry` fixture
    holds one approved item, so `count` was always 1 -- which is what made the
    `or count == 1` arm of the assertion below absorb every failure.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        for letter, slug, title in (
            ("C", "retry-policy", "Retry policy"),
            ("D", "quota-policy", "Quota policy"),
        ):
            (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
                f"# {title}\n\nEvery call carries a signed token. This policy explains how "
                f"the token budget is spent, and what the gateway does when it runs out.\n"
            )
            (
                root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{slug}.yaml"
            ).write_text(EXTRA_MIGRATION.format(letter=letter, slug=slug, title=title))
        _run("migrate", "apply")
        _run("index", "build")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_a_token_budget_is_honoured(indexed_corpus: ProjectRegistry) -> None:
    """FR-R4. An agent that receives more context than it asked for has already
    paid for it.

    Asserted as a *difference* between two budgets rather than against a
    constant. `usedTokens <= 40 or count == 1` is satisfied by a `pack` that
    ignores the budget entirely, and by one that reports a used total it never
    enforced -- both of which passed this test.
    """
    generous = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=32_000
    )
    tight = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=40
    )

    assert generous["count"] > 1, "the fixture must offer more than one hit to drop"
    assert generous["retrieval"]["droppedForBudget"] == 0
    assert tight["count"] < generous["count"], "a tight budget must actually drop results"
    assert tight["count"] >= 1, "a budget below the best hit still returns it (pack's floor)"
    # Over budget only via that documented single-result floor.
    if tight["count"] > 1:
        assert tight["retrieval"]["usedTokens"] <= 40


@pytest.mark.asyncio
async def test_the_default_budget_answers_an_ordinary_query_in_full(
    indexed_corpus: ProjectRegistry,
) -> None:
    """FR-R4. The half of `DEFAULT_BUDGET_TOKENS` that bounds it from below.

    A default exists so that omitting the parameter is *safe*, not so that it
    silently truncates. Three short policy documents that all answer the query is
    an ordinary answer to an ordinary question, and a caller who states no budget
    has to receive it whole — otherwise the pipeline's second opinion, which
    `diversify` spends a per-item cap to make room for, is invisible to every
    caller who did not read the parameter list.

    Asserted against the generous answer rather than against a token count, so it
    survives any retuning of `estimate_tokens`: the claim is "the default did not
    cost this caller anything", which is a comparison and not a number.

    `DEFAULT_BUDGET_TOKENS` was moved from 2000 to 200 and the entire suite
    passed. Every other budget test here states its own `maxTokens`, and the
    published default — which is what an agent actually calls with — was checked
    by nothing.
    """
    default = await _call(indexed_corpus, "knowledge.search", projectId="demo", query="token")
    generous = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=32_000
    )

    assert generous["count"] > 1, "the fixture must offer more than one hit to lose"
    assert default["results"] == generous["results"], (
        "a caller who stated no budget must not be answered with less than the corpus holds"
    )
    assert default["retrieval"]["droppedForBudget"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [32_000, 200], ids=["generous", "tight"])
async def test_the_reported_cost_is_measured_on_the_payload_the_caller_receives(
    indexed_corpus: ProjectRegistry, max_tokens: int
) -> None:
    """FR-R4. A budget the caller cannot verify is a number, not a promise.

    The ranked path used to price a *chunk* — the unit it retrieves and pins —
    while sending a full result payload. The excerpt is capped at 280
    characters; provenance, the trust triple, `sourceAnchors` and SAFETY are
    not, and none of them was counted. Measured on a ten-document project,
    `maxTokens=500` reported 486 and sent 1,953: 9.2x over at the tightest
    budget. Every existing budget assertion compared the implementation's number
    against itself, so all of them held.

    Two assertions, deliberately different in kind. The first pins *what* is
    priced: the serialised result, exactly as the caller receives it. The second
    holds whatever `estimate_tokens` is later retuned to — a caller charged N
    tokens must not be sent more than `CHARS_PER_TOKEN * N` characters — so this
    test cannot be satisfied by changing the estimator to agree with a wrong
    total.

    Run at both ends of the budget because the tight case is the one that
    matters and the one that was worst: it is where truncation silently eats the
    caller's own instructions.
    """
    from theurian.domain.ranking import CHARS_PER_TOKEN, estimate_tokens

    result = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=max_tokens
    )
    sent = [json.dumps(hit, ensure_ascii=False) for hit in result["results"]]

    assert result["retrieval"]["indexed"] is True, "this must exercise the ranked path"
    assert sent, "a response with no results would satisfy any accounting"
    assert result["retrieval"]["usedTokens"] == sum(estimate_tokens(one) for one in sent)
    assert result["retrieval"]["usedTokens"] * CHARS_PER_TOKEN >= sum(len(one) for one in sent), (
        "the caller was charged for less than was sent to them"
    )


@pytest.mark.asyncio
async def test_the_budget_covers_the_whole_message_and_not_only_the_results(
    indexed_corpus: ProjectRegistry,
) -> None:
    """FR-R4. What arrives in the caller's window is the response, not the hits.

    `projectId`, the echoed `query`, `count` and the whole `retrieval` block —
    the `note` above all, which is a paragraph of prose — travel with every
    answer and were charged to nobody. Measured at 138 to 171 tokens of fixed
    overhead, so `maxTokens=100` sent 302 to 352.

    The budget is calibrated from the run itself rather than written as a
    constant: `maxTokens` is set to exactly what the results alone cost, which is
    the budget the previous accounting accepted in full while sending the
    envelope on top of it. A response that still fits that number is one that
    charged for the envelope.
    """
    from theurian.domain.ranking import estimate_tokens

    generous = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=32_000
    )
    results_alone = int(generous["retrieval"]["usedTokens"])
    tight = await _call(
        indexed_corpus,
        "knowledge.search",
        projectId="demo",
        query="token",
        maxTokens=results_alone,
    )

    assert generous["count"] > 1, "the fixture must offer more than one hit to drop"
    assert tight["retrieval"]["droppedForBudget"] >= 1, (
        "the results alone exactly filled this budget, so the envelope must displace one"
    )
    assert estimate_tokens(json.dumps(tight, ensure_ascii=False)) <= results_alone


@pytest.mark.asyncio
async def test_the_envelope_never_starves_the_answer_completely(
    indexed_corpus: ProjectRegistry,
) -> None:
    """The floor `take_within_budget` promises, held through the new reservation.

    A budget smaller than the envelope leaves nothing to spend, and the naive
    subtraction returns zero or negative — which would turn "your budget is
    small" into "we have no such decision", the failure the whole fallback exists
    to prevent. One over-long answer a caller can truncate beats an empty one
    they cannot act on.
    """
    starved = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=1
    )

    assert starved["count"] == 1
    assert starved["retrieval"]["usedTokens"] > 0


#: One document long enough to split into several chunks, every one of which
#: contains `gateway`. Without it the per-item cap cannot be observed at this
#: layer: every other document here yields exactly one chunk, so "no item appears
#: twice" is true whether or not the cap runs.
LONG_BODY = (
    "# Gateway runbook\n\nThis runbook records what the gateway does under load.\n\n"
) + "".join(
    f"## {section}\n\nThe gateway {section.lower()} procedure is rehearsed each quarter. "
    f"It states what the gateway requires, who owns the gateway path, and when the "
    f"gateway behaviour is next revisited. Nothing here changes without a migration.\n\n"
    for section in ("Escalation", "Retention", "Exceptions", "Review cadence")
)


@pytest.fixture
def indexed_long_document(registry: ProjectRegistry) -> ProjectRegistry:
    """`registry`, plus one multi-chunk document, then a build."""
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / ".theurian/knowledge/architecture/gateway-runbook.md").write_text(LONG_BODY)
        (root / ".theurian/migrations/01K1EAAAAA01234567890ABCDE-gateway.yaml").write_text(
            EXTRA_MIGRATION.format(letter="E", slug="gateway-runbook", title="Gateway runbook")
        )
        _run("migrate", "apply")
        _run("index", "build")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_one_result_per_document_however_many_chunks_matched(
    indexed_long_document: ProjectRegistry,
) -> None:
    """`knowledge.search` returns documents, so a document appears once.

    The cap lives in the `per_item=1` this tool passes to the ranking, and
    nothing at this layer noticed when it moved: raising it to 2 left 96 tests
    green while a second chunk of the same document appeared in `results` — two
    hits with one `itemId`, two shares of the caller's budget, and a `count` that
    no longer counts documents.
    """
    result = await _call(
        indexed_long_document, "knowledge.search", projectId="demo", query="gateway", limit=10
    )
    items = [hit["itemId"] for hit in result["results"]]

    assert "architecture.gateway-runbook" in items, "the multi-chunk document must be found"
    assert items.count("architecture.gateway-runbook") == 1, (
        "one result per document, not one per chunk"
    )
    assert len(items) == len(set(items))


def test_the_cap_is_the_only_reason_the_long_document_appears_once(
    indexed_long_document: ProjectRegistry,
) -> None:
    """Guards the guard above.

    "It appears once" is satisfied by a corpus that could only ever have produced
    one chunk of it. Asked for two per item, the same query against the same
    index must return the same document twice, or the assertion above is about
    the fixture rather than about the cap.

    **Synchronous**, and at the ranking layer: this is the state the tool must
    not publish, so it is measured one layer below the tool.
    """
    from theurian.application.retrieval_service import RetrievalService, SearchRequest
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(indexed_long_document.load()["demo"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    outcome = RetrievalService(SqliteIndexStore(built)).search(
        SearchRequest(query="gateway", project_id="demo", per_item=2), NOTHING_WITHHELD
    )

    items = [candidate.item_id for candidate in outcome.candidates]
    assert items.count("architecture.gateway-runbook") == 2, (
        "the index must hold a second matching chunk for the cap to have work to do"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["token\x00policy", "token\ud800policy"])
async def test_a_query_containing_an_untransportable_character_does_not_raise(
    indexed: ProjectRegistry, query: str
) -> None:
    """A search box must not raise at the user.

    `_to_match_expression` quotes every term, so the inputs that still reach the
    driver malformed are the ones that cannot become a NUL-terminated UTF-8
    string: a NUL, which ends the C string early and makes FTS5 report
    `unterminated string`; and a lone surrogate, which cannot be encoded at all
    and fails as a `UnicodeEncodeError` before SQLite is called. Both used to
    reach the client as a tool failure. JSON-RPC can carry either.
    """
    result = await _call(indexed, "knowledge.search", projectId="demo", query=query)

    assert "results" in result


#: A document whose answer is nowhere near its head.
#:
#: The first section is long enough to fill an excerpt on its own and never says
#: `quarantine`; the last section is the only place that word appears. That
#: separation is the whole point: with a corpus of short documents the head of
#: the body *is* the matched passage, so an excerpt taken from either place
#: reads the same and the replacement below cannot be observed.
HEAD_MARKER = "every inbound call carries a signed token"
TAIL_MARKER = "the gateway quarantines the tenant"
LAYERED_BODY = (
    f"# Authentication policy\n\nThe gateway is the only trust boundary, so "
    f"{HEAD_MARKER} and the signature is verified before any handler runs. "
    + (
        "This paragraph exists to fill the first passage with prose that does "
        "not answer the question being asked. " * 6
    )
    + f"\n\n## Compromise handling\n\nWhen a signing key leaks, {TAIL_MARKER} "
    f"and every credential minted under that key is revoked immediately.\n"
)


@pytest.fixture
def layered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """One document whose answer is in its last section, and a built index.

    **Synchronous**, and that is not a style choice. `theurian index build`
    embeds through `asyncio.run`, which raises inside an already-running loop,
    so an async test cannot build an index in its own body.
    """
    registry = _project_with_body(tmp_path, monkeypatch, LAYERED_BODY)
    _build_index(registry)
    return registry


@pytest.mark.asyncio
async def test_the_excerpt_is_the_passage_that_matched_not_the_head_of_the_document(
    layered: ProjectRegistry,
) -> None:
    """Chunking buys ranking precision; this is what delivers it to the caller.

    Retrieval ranks *chunks*, so a hit already knows which paragraph answered
    the query. Returning the head of the document instead throws that away: the
    caller sees an introduction, cannot tell why the document was returned, and
    pays a `knowledge.get` to find out — which is the whole cost chunking was
    supposed to avoid.

    Both halves are asserted. "The matched passage is present" alone is
    satisfied by returning the entire body; "the head is absent" alone is
    satisfied by returning nothing at all.
    """
    result = await _call(layered, "knowledge.search", projectId="demo", query="quarantines")
    excerpt = result["results"][0]["excerpt"]

    assert TAIL_MARKER in excerpt, "the caller must see the paragraph that matched"
    assert HEAD_MARKER not in excerpt, "not the head of the document it came from"


@pytest.mark.asyncio
async def test_the_unranked_fallback_still_excerpts_from_the_head(
    layered: ProjectRegistry,
) -> None:
    """The control, and a published difference between the two answer paths.

    Without an index there are no chunks, so the fallback has nothing but the
    document to excerpt from and says the head. That is what makes the ranked
    path's excerpt a real replacement rather than a coincidence of this corpus:
    the same document, the same query, a different excerpt.
    """
    root = Path(layered.load()["demo"]["rootPath"])
    (root / ".theurian/state/active-index.json").unlink()

    result = await _call(layered, "knowledge.search", projectId="demo", query="quarantines")
    excerpt = result["results"][0]["excerpt"]

    assert result["retrieval"]["indexed"] is False
    assert HEAD_MARKER in excerpt, "with no chunk to point at, the head is all there is"


def _build_index(registry: ProjectRegistry) -> None:
    """Run `theurian index build` inside a registered project's root."""
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("index", "build")
    finally:
        monkey.undo()


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


@pytest.mark.asyncio
async def test_a_deprecated_item_stops_being_returned_even_from_a_stale_index(
    indexed: ProjectRegistry,
) -> None:
    """The index's status is a build-time snapshot; the canonical store is the
    authority for what is approved *now*.

    Without a live re-check, deprecating a decision leaves it retrievable until
    someone remembers to rebuild — which is the system returning knowledge the
    team has explicitly retired.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    (root / ".theurian/migrations/01K1CAAAAA01234567890ABCDE-deprecate.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1CAAAAA01234567890ABCDE
createdAt: 2026-08-03T12:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.auth-policy
    reason: superseded by the new gateway design
"""
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert all(hit["status"] == "approved" for hit in result["results"])


@pytest.mark.asyncio
async def test_a_stale_index_says_so_in_the_response(indexed: ProjectRegistry) -> None:
    """Only the CLI knew about staleness, and the MCP client is the one acting
    on the answer.

    Applying a migration is what makes the index stale: editing a knowledge file
    alone leaves the *database* equally out of date, so the index still matches
    what the database holds.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    (root / ".theurian/knowledge/architecture/caching-draft.md").write_text(
        DRAFT_BODY + "\n\nAn additional paragraph that changes the state hash.\n"
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["stale"] is True
    assert "index build" in result["retrieval"]["note"]


@pytest.mark.asyncio
async def test_include_unapproved_falls_back_when_the_index_holds_no_drafts(
    indexed: ProjectRegistry,
) -> None:
    """An index built without `--include-unapproved` cannot answer this.

    Indexing everything and filtering at query time was tried and reverted: it
    made one boolean reach content the team decided must not be followed, and it
    removed the operator's ability to guarantee no draft is in the file. Falling
    back answers the question honestly instead of returning approved-only
    results under a parameter that asked for more.
    """
    result = await _call(
        indexed,
        "knowledge.search",
        projectId="demo",
        query="caching",
        includeUnapproved=True,
    )

    assert result["retrieval"]["indexed"] is False
    assert any(hit["status"] == "draft" for hit in result["results"])


@pytest.mark.asyncio
async def test_the_response_says_whether_the_index_holds_unapproved_content(
    indexed: ProjectRegistry,
) -> None:
    """So an empty answer to `includeUnapproved=True` is distinguishable from
    "there are no drafts"."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert result["retrieval"]["indexesUnapproved"] is False


@pytest.mark.asyncio
async def test_an_absurd_token_budget_is_clamped_rather_than_raised(
    indexed: ProjectRegistry,
) -> None:
    """A caller asking for a million tokens means "as much as you have".
    Answering with an exception that names an internal parameter helps nobody.

    **The second assertion is weaker than it reads, and is left as a floor
    rather than mistaken for a bound.** This corpus answers in 609 tokens, so
    `usedTokens <= 32_000` holds at any `MAX_BUDGET_TOKENS` above that — and
    holds with the clamp deleted outright. Measured: the constant was raised to
    10,000,000 and all 1,261 tests passed. Nothing in this suite can bind it,
    because `MAX_RESULTS` caps a response at fifty payloads of roughly 200
    tokens — an excerpt is capped at `results.EXCERPT_CHARS` — which is a third
    of the cap. Reaching it needs a corpus of fifty CJK documents, whose
    280-character excerpts cost about 420 tokens each; until one exists, the
    real bound on a response is `MAX_RESULTS`, which is pinned by
    `test_a_caller_may_have_the_fifty_results_the_tool_promises`.
    """
    zero = await _call(indexed, "knowledge.search", projectId="demo", query="token", maxTokens=0)
    huge = await _call(
        indexed, "knowledge.search", projectId="demo", query="token", maxTokens=99_000_000
    )

    assert zero["count"] >= 1
    assert huge["retrieval"]["usedTokens"] <= 32_000


# -- What a caller's own query may cost them (SEC-8, FR-R4) ------------------
#
# `mcp.tools.MAX_QUERY_CHARS` is a published contract value, not a knob: the
# response schema states `maxLength: 2000` on `query` with the reason, and
# `test_a_real_search_response_validates_against_its_published_schema
# [unranked-overlong-query]` fails if the constant moves. So the *number* is
# pinned, and nothing below restates it.
#
# What the schema cannot hold is the two claims that make the number safe, and
# neither was tested. Both were confirmed reachable by mutation:
#
#   - `infrastructure.sqlite.index_query.MAX_QUERY_CHARS` lowered to 500: a
#     query whose question sat 1,400 characters in came back `count: 0` while
#     the response echoed all 1,907 characters of it. The schema calls that
#     field "the string that was actually searched for"; it would have been a
#     string containing a term nobody searched for. 1,260 tests passed.
#   - `mcp.tools.MAX_QUERY_CHARS` raised to 8,000: at the default budget the
#     caller's own query displaced two of their three results, because the
#     echoed query is charged to them through `_envelope_tokens`.


@pytest.mark.asyncio
async def test_a_term_the_response_echoes_is_a_term_that_was_searched_for(
    indexed: ProjectRegistry,
) -> None:
    """SEC-8. Two bounds, one string — the invariant that keeps the echo honest.

    The query is truncated twice by two constants that happen to be equal: once
    at the tool boundary, which decides what is *echoed*, and again in
    `_query_terms`, which decides what is *searched*. If the retrieval one were
    ever the smaller, every response would keep saying "query" of a string it
    had only partly read, and a caller debugging a missing result would be
    reading the wrong evidence.

    **The probe is placed from the observed boundary, not from either
    constant.** One call with an absurd query reveals where the echo is cut;
    the real probe then puts its question just inside that point. That is what
    makes it catch the divergence from *either* side, verified both ways: the
    retrieval bound lowered to 500, and the tool bound raised to 8,000 — which
    moves the observed cut, so the probe follows it out past the retrieval one.

    The shape it cannot see is the two moving together. Both at 8,000 leaves
    this green, because the echo and the search still agree; what that breaks is
    the caller's budget, which is the test below, and the published `maxLength`,
    which is the wire-contract suite.
    """
    absurd = await _call(indexed, "knowledge.search", projectId="demo", query="z" * 50_000)
    cut = len(absurd["query"])
    probe = "z" * (cut - 20) + " signed"

    answer = await _call(indexed, "knowledge.search", projectId="demo", query=probe)

    assert cut < 50_000, "the boundary must truncate at all, or the probe measures nothing"
    assert answer["query"] == probe, "the whole probe must be echoed, including its question"
    assert [hit["itemId"] for hit in answer["results"]] == ["architecture.auth-policy"], (
        "a term the caller can read back in `query` must be one the search actually spent"
    )


@pytest.mark.asyncio
async def test_a_query_at_the_boundary_does_not_cost_the_caller_their_answer(
    indexed_corpus: ProjectRegistry,
) -> None:
    """FR-R4. The caller's own words are charged to the caller's own budget.

    `_envelope_tokens` prices the echoed query and reserves it from `maxTokens`,
    which is right — it is sent to them — but it means the cap on a query is
    also a cap on how much of a defaulting caller's answer their question may
    eat. At 2,000 characters that is about 500 tokens of the 2,000-token
    default, and all three results still fit. Measured: 4,000 still fits this
    corpus, 6,000 does not, and at 8,000 the same query returns one result and
    reports two dropped. So it is a band, and a corpus with more to say would
    narrow it — a caller's question is charged against their whole answer, not
    against a fixed share of it.

    So this is the constraint the schema's `maxLength` does not express — the
    query cap and `DEFAULT_BUDGET_TOKENS` are not independent, and raising the
    first is a change to what the second delivers. No `maxTokens` is passed,
    because the caller who is hurt is the one who never thought about it.

    The query is sent absurdly long rather than at the published length, so the
    boundary clamps it and the test needs no constant to know where that is.
    """
    short = await _call(indexed_corpus, "knowledge.search", projectId="demo", query="token")
    maximal = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token " + "z" * 50_000
    )

    assert short["count"] > 1, "the fixture must have an answer long enough to be cut into"
    assert len(maximal["query"]) < 50_000, "the boundary must clamp, or this measures nothing"
    assert [hit["itemId"] for hit in maximal["results"]] == [
        hit["itemId"] for hit in short["results"]
    ], "a question at the published maximum must not displace the answer to it"
    assert maximal["retrieval"]["droppedForBudget"] == 0


@pytest.mark.asyncio
async def test_a_superseded_revision_is_not_served_from_a_stale_index(
    indexed: ProjectRegistry,
) -> None:
    """The leak this guard exists for.

    Replacing a revision is *how* a secret gets removed from approved
    knowledge. The index pins a revision id at build time, so without this
    check a stale index keeps answering with the very text the team just
    retracted — and labels it with the new revision's `approved` status.

    A stale index therefore returns fewer results rather than wrong ones.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    (root / ".theurian/knowledge/architecture/auth-v2.md").write_text(
        "# Authentication policy\n\nThe key now lives in the secret store.\n"
    )
    (root / ".theurian/migrations/01K1EAAAAA01234567890ABCDE-replace.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1EAAAAA01234567890ABCDE
createdAt: 2026-08-03T14:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1EREVAA01234567890ABCDE
    expectedRevision: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-v2.md
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
          sourceUri: git://demo/auth-v2.md
"""
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert all(hit["revisionId"] != "01K1AAAREV01234567890ABCDE" for hit in result["results"]), (
        "the retracted revision must not be served"
    )
    assert result["retrieval"]["stale"] is True


@pytest.mark.asyncio
async def test_what_a_stale_index_withheld_is_not_reported(indexed: ProjectRegistry) -> None:
    """`withheldSuperseded` was a per-query count of documents that matched the
    caller's terms and that the caller was not allowed to read.

    It was added so zero results would not read as "we have no such decision".
    It bought that at the price of a truth oracle, and the trigram retriever
    matches any substring of three characters, so the oracle extracts rather
    than merely detects. `stale` says the same useful thing — your index is
    behind, expect fewer results — and says it identically for every query.
    """
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert "withheldSuperseded" not in result["retrieval"]
    assert result["retrieval"]["stale"] is False


# -- The extraction oracle, closed and kept closed (SEC-13, T-15, T-17) -------
#
# T-17 in `docs/security/threat-model.md` is the threat these tests discharge,
# and it is its own entry rather than a note on T-15 for a reason worth knowing
# while reading them: T-15 covers a secret being committed *into* approved
# knowledge, and this attacks T-15's own remediation. Superseding a revision is
# the documented way to get a secret out, and the window right after performing
# that redaction was the window the plaintext could be read back — through a
# different tool call, with no flag.
#
# T-17 records both extraction paths, re-measured against the pre-fix code:
# 257 ordinary `knowledge.search` calls recovered a 20-character credential
# through a superseding revision, and 215 recovered a 13-character one through
# `deprecateItem` — where `withheldSuperseded` never moved and `usedTokens` was
# the only channel. Both paths are parametrized below for exactly that reason:
# closing one says nothing about the other.
#
# The invariant asserted here is deliberately stronger than "extraction fails",
# which is a property of one attack strategy and can hold of a channel that is
# merely narrow. It is: **a query matching only withheld content must be
# indistinguishable, in the content of the response, from a query matching
# nothing at all.** The two `retrieval` blocks are compared whole, so every
# field is covered by construction and a field added later is covered the day it
# is added.
#
# **In content, and only in content.** T-17 records a residual these tests do
# not close and must not be read as closing: timing. A withheld candidate still
# costs a canonical-store lookup before it is dropped, so latency stays weakly
# correlated with whether a query matched withheld content. That is inherent to
# re-checking against the canonical store on every call — the same re-check that
# closes T-15's window — rather than something this fix chose to leave.

#: A string that appears in exactly one document and in no English word list,
#: so a hit is unambiguous and a miss is not a tokenizer accident.
LEAKED_CREDENTIAL = "ZQXJVBWKPLMNTRD9"

#: A query that matches nothing, in the same shape as `LEAKED_CREDENTIAL`. The comparison
#: is only meaningful if the control query is equally exotic -- a common word
#: would differ in `retrieval` for honest reasons.
NO_SUCH_TERM = "QQZZXNOSUCHVALUE7"

#: The dense retriever cannot score `LEAKED_CREDENTIAL`. Measured: the bundled embedder is
#: a hashed character n-gram vectoriser, and a random sixteen-character token
#: shares almost no n-gram mass with prose, so it sits below the 0.25 similarity
#: floor and the dense retriever contributes nothing at all. A probe that never
#: reaches the retriever cannot demonstrate anything about the field that
#: retriever reports, so the dense case gets a query the retriever can actually
#: score -- measured at 0.773 against the withheld chunk and below the floor for
#: everything else in the corpus.
DENSE_PROBE = "tenant quarantine playbook rehearsal"
#: Unrelated to every document here, on every retriever. The control has to be
#: as scoreable as the probe and match nothing, or the comparison is between two
#: different kinds of query rather than between two answers.
DENSE_CONTROL = "kubernetes scheduler bin packing"

#: ``(id, probe, control, useDense)``. The probe matches only the withheld
#: document; the control matches nothing.
PROBES = (
    ("the-secret-itself", LEAKED_CREDENTIAL, NO_SUCH_TERM, False),
    ("a-phrase-the-vector-retriever-can-score", DENSE_PROBE, DENSE_CONTROL, True),
)

RUNBOOK_BODY = (
    f"# Tenant quarantine playbook\n\nWhen the shared gateway credential "
    f"{LEAKED_CREDENTIAL} leaks, the responder rehearses this quarantine playbook, "
    f"isolates the affected tenant, and records the rehearsal in the "
    f"quarantine ledger before escalating.\n"
)

RUNBOOK_TITLE = "Tenant quarantine playbook"

#: The one document both withholding stories retire, so both write the same
#: migration. ``title`` is the only hole in it, and it is a hole because the
#: title is prepended to the body before chunking: an English title on the
#: Japanese depth corpus would hand the word index tokens no other document in
#: that corpus has, and the word index having nothing to say is the point of
#: that corpus.
RUNBOOK_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1FAAAAA01234567890ABCDE
createdAt: 2026-08-02T13:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.runbook
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.runbook
    revisionId: 01K1FAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/runbook.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/runbook.md
"""

#: The remediation itself: a new revision with the credential removed. The
#: window this opens is the ordinary state between `migrate apply` and `index
#: build` -- performing the fix is what makes the old text readable from the
#: index, which is why the oracle mattered.
REDACTION_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1GAAAAA01234567890ABCDE
createdAt: 2026-08-03T15:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.runbook
    revisionId: 01K1GREVAA01234567890ABCDE
    expectedRevision: 01K1FAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/runbook-v2.md
    metadata:
      title: Tenant quarantine playbook
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/runbook-v2.md
"""

#: The other way content leaves approved knowledge. Covered alongside the
#: redaction because they take different branches out of `ResultGate` -- one
#: fails the status gate, the other the current-revision check -- and closing one
#: says nothing about the other.
DEPRECATION_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1GAAAAA01234567890ABCDE
createdAt: 2026-08-03T15:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.runbook
    reason: the credential was pasted into it
"""


def _supersede(root: Path) -> None:
    (root / ".theurian/knowledge/architecture/runbook-v2.md").write_text(
        "# Tenant quarantine playbook\n\nThe gateway credential now lives in the secret store.\n"
    )
    (root / ".theurian/migrations/01K1GAAAAA01234567890ABCDE-redact.yaml").write_text(
        REDACTION_MIGRATION
    )


def _deprecate(root: Path) -> None:
    (root / ".theurian/migrations/01K1GAAAAA01234567890ABCDE-deprecate.yaml").write_text(
        DEPRECATION_MIGRATION
    )


@pytest.fixture(params=[_supersede, _deprecate], ids=["superseded", "deprecated"])
def withheld(registry: ProjectRegistry, request: pytest.FixtureRequest) -> ProjectRegistry:
    """A secret indexed while approved, then withheld, with the index left stale.

    **Synchronous**, because `theurian index build` embeds through
    `asyncio.run`, which raises inside an already-running loop.

    The index is deliberately *not* rebuilt. That is not a contrived state: it
    is where every project sits between applying the migration that redacts a
    secret and running the next `index build`.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / ".theurian/knowledge/architecture/runbook.md").write_text(RUNBOOK_BODY)
        (root / ".theurian/migrations/01K1FAAAAA01234567890ABCDE-runbook.yaml").write_text(
            RUNBOOK_MIGRATION.format(title=RUNBOOK_TITLE)
        )
        _run("migrate", "apply")
        _run("index", "build")

        request.param(root)
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


@pytest.mark.parametrize(
    ("probe", "control", "use_dense"), [p[1:] for p in PROBES], ids=[p[0] for p in PROBES]
)
def test_the_stale_index_still_ranks_the_withheld_document(
    withheld: ProjectRegistry, probe: str, control: str, use_dense: bool
) -> None:
    """Guards the guard: proves each probe reaches the branch under test.

    If the index no longer ranked the withheld document, "a query for it looks
    like a query for nothing" would be true because there was nothing to
    withhold — the fixture, not the fix, would be doing the work. That is not
    hypothetical here: the dense retriever cannot score `LEAKED_CREDENTIAL` at all, so the
    `useDense` case was silently vacuous until this fixed it.

    Asserted against the ranked candidate list, one layer below the canonical
    store that does the withholding, and with the same `useDense` the response
    test uses.
    """
    from theurian.application.retrieval_service import RetrievalService, SearchRequest
    from theurian.infrastructure.embedding import HashingEmbedding
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(withheld.load()["demo"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    service = RetrievalService(SqliteIndexStore(built), HashingEmbedding())

    found = service.search(
        SearchRequest(query=probe, project_id="demo", use_dense=use_dense), NOTHING_WITHHELD
    )
    nothing = service.search(
        SearchRequest(query=control, project_id="demo", use_dense=use_dense), NOTHING_WITHHELD
    )

    assert [c.item_id for c in found.candidates] == ["architecture.runbook"], (
        "the probe must still rank the retracted document, and only it"
    )
    assert nothing.candidates == (), "and the control must reach no candidate at all"
    if use_dense:
        assert "dense" in found.candidates[0].found_by, (
            "the dense retriever must be one of the retrievers that surfaced it, "
            "or `embeddingModel` is not a channel this probe can open"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe", "control", "use_dense"), [p[1:] for p in PROBES], ids=[p[0] for p in PROBES]
)
async def test_a_query_matching_only_withheld_content_is_indistinguishable_from_no_match(
    withheld: ProjectRegistry, probe: str, control: str, use_dense: bool
) -> None:
    """SEC-13, T-15, T-17. The whole `retrieval` block, compared field by field.

    Not "the secret is absent from the response" — it was already absent, and
    the leak was never the text. It was that ``count: 0, results: [],
    usedTokens: 46`` *states* that something matched and may not be read, which
    is the truth oracle sequential extraction needs.

    ``useDense=True`` is covered because ``embeddingModel`` was a fourth channel
    on top of the count, the token total and the mode: it used to be reported
    empty when the dense retriever found nothing, so it flipped on a withheld
    dense hit — one bit per query, and no flag required to read it.

    The claim is about the *content* of the response and nothing else. T-17
    records timing as a standing residual: a withheld candidate still costs a
    canonical-store lookup, so latency remains weakly correlated. Nothing here
    measures or asserts anything about how long the two calls took, and a reader
    must not take equality of these two blocks as a claim that they are
    indistinguishable in every respect.
    """
    withheld_only = await _call(
        withheld, "knowledge.search", projectId="demo", query=probe, useDense=use_dense
    )
    no_match = await _call(
        withheld, "knowledge.search", projectId="demo", query=control, useDense=use_dense
    )

    assert withheld_only["count"] == 0
    assert withheld_only["results"] == []
    assert withheld_only["retrieval"] == no_match["retrieval"], (
        "every field that differs here is a channel an attacker reads one bit at a time"
    )


@pytest.mark.asyncio
async def test_nothing_derived_from_the_withheld_document_is_reported(
    withheld: ProjectRegistry,
) -> None:
    """SEC-13, T-17, stated positively so a reader knows what the fields may say.

    Every number in `retrieval` is computed from the results the caller actually
    receives; everything else is a property of the index or of the caller's own
    parameters. `stale` is the deliberate exception and the reason
    `withheldSuperseded` could be deleted rather than merely hidden: it says
    "your index is behind, expect fewer results" identically for every query.

    `droppedForBudget` is asserted at zero for its own reason. A withheld hit is
    not "dropped for budget" — counting it there would move the leak one field
    to the left rather than close it.
    """
    result = await _call(withheld, "knowledge.search", projectId="demo", query=LEAKED_CREDENTIAL)
    retrieval = result["retrieval"]

    assert retrieval["usedTokens"] == 0, "nothing was sent, so nothing was charged"
    assert retrieval["droppedForBudget"] == 0, "a withheld hit is not 'dropped for budget'"
    assert retrieval["mode"] == "none", "the mode names the retrievers behind the *results*"
    assert "withheldSuperseded" not in retrieval
    assert retrieval["stale"] is True, "the query-independent half is still told"


# -- The same oracle, in the input space the probes above cannot reach --------
#
# Every probe above matches the withheld document *and nothing else*, so both
# sides of the comparison answer `count: 0` and every field agrees — which stayed
# true while the caller's `limit` was still being applied to *candidates*, and a
# withheld candidate was still taking a result slot from a visible one.
#
# The channel needs both halves: a query matching withheld content **and** at
# least `limit` documents the caller may see. Then the withheld hit displaces the
# last visible one, `count` drops by one, and the oracle is open again — measured
# at 203 ordinary `knowledge.search` calls to recover a sixteen-character
# credential, cheaper than the 257 the probes above were written against.
#
# The invariant is unchanged and the assertion is stronger: the **whole
# response** must be identical, not merely the `retrieval` block. `fusedScore`
# lives in `results`, and it was a second oracle of exactly the same shape — RRF
# scores `1 / (k + rank)`, so a withheld chunk ranked above a visible one pushed
# that visible one's published score from 0.032787 to 0.032258. Four visible
# hits, four numbers, all moving together: a finer read than `count`, because it
# also says which rank the withheld document took.

#: A control the same length as the probe. `retrieval` no longer depends on what
#: a query *matched*, but the response does echo the query and the caller is
#: charged for the envelope that carries it, so a control of a different length
#: would differ from the probe for an honest reason and blunt the comparison.
CROWD_CONTROL = "QQZZXNOSUCHVAL7X"

#: How many approved documents the crowding probe also matches. Enough that the
#: displacement is visible at several `limit` values rather than at one.
CROWD = 4


def _without_query(payload: dict[str, Any]) -> dict[str, Any]:
    """Everything a caller receives except the string they sent."""
    return {key: value for key, value in payload.items() if key != "query"}


@pytest.fixture(params=[_supersede, _deprecate], ids=["superseded", "deprecated"])
def crowded(registry: ProjectRegistry, request: pytest.FixtureRequest) -> ProjectRegistry:
    """`withheld`, plus enough approved documents to fill the caller's `limit`.

    Same shape as `withheld` and deliberately not layered on it: the crowd has to
    be in the index *before* the redaction, and rebuilding afterwards would
    remove the withheld revision from the index and leave nothing to withhold.

    **Synchronous**, because `theurian index build` embeds through `asyncio.run`,
    which raises inside an already-running loop.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / ".theurian/knowledge/architecture/runbook.md").write_text(RUNBOOK_BODY)
        (root / ".theurian/migrations/01K1FAAAAA01234567890ABCDE-runbook.yaml").write_text(
            RUNBOOK_MIGRATION.format(title=RUNBOOK_TITLE)
        )
        for index, letter in enumerate("HJKM"[:CROWD]):
            slug = f"gateway-note-{index}"
            (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
                f"# Gateway note {index}\n\nThe shared gateway meters every request for "
                f"tenant {index}, and the gateway rejects an unsigned one.\n"
            )
            (
                root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{slug}.yaml"
            ).write_text(
                EXTRA_MIGRATION.format(letter=letter, slug=slug, title=f"Gateway note {index}")
            )
        _run("migrate", "apply")
        _run("index", "build")

        request.param(root)
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


def test_the_crowding_probe_puts_the_withheld_document_among_visible_ones(
    crowded: ProjectRegistry,
) -> None:
    """Guards the guard: proves this fixture can violate the invariant.

    Two preconditions, both necessary. The withheld document must still be ranked
    — otherwise there is nothing to withhold and the fixture, not the fix, keeps
    the two responses equal. And it must rank *above* at least one visible
    document, or removing it changes nothing about which visible ones fit inside
    a small `limit`.

    Asserted one layer below the canonical store that does the withholding, with
    the same `per_item` the tool passes.
    """
    from theurian.application.retrieval_service import RetrievalService, SearchRequest
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(crowded.load()["demo"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    outcome = RetrievalService(SqliteIndexStore(built)).search(
        SearchRequest(query=f"gateway {LEAKED_CREDENTIAL}", project_id="demo", per_item=1),
        NOTHING_WITHHELD,
    )
    items = [candidate.item_id for candidate in outcome.candidates]

    assert "architecture.runbook" in items, "the probe must still reach the retracted document"
    assert items.index("architecture.runbook") < len(items) - 1, (
        "it must outrank at least one visible document, or its removal displaces nothing"
    )
    assert len([item for item in items if item != "architecture.runbook"]) >= CROWD, (
        "and the caller must have enough visible documents to fill a `limit`"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", range(1, CROWD + 2))
async def test_a_withheld_hit_never_costs_a_visible_one_its_place(
    crowded: ProjectRegistry, limit: int
) -> None:
    """SEC-13, T-15, T-17. The whole response, compared field by field.

    Run across every `limit` from one to one past the crowd, because the leak is
    a boundary effect: it appears exactly when the withheld candidate falls
    inside `limit` and disappears once `limit` exceeds everything that matched.
    A single `limit` would have been the one that passed.

    `maxTokens` is generous on purpose. This test is about the gate, not about
    the budget, and a budget tight enough to drop results would make the two
    calls differ over which of them the caller could afford rather than over what
    they were allowed to see.
    """
    probe = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {LEAKED_CREDENTIAL}",
        limit=limit,
        maxTokens=32_000,
    )
    control = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {CROWD_CONTROL}",
        limit=limit,
        maxTokens=32_000,
    )

    assert probe["count"] == min(limit, CROWD), "the caller gets every document they may read"
    assert _without_query(probe) == _without_query(control), (
        "anything that differs here is a bit an attacker reads per call"
    )


@pytest.mark.asyncio
async def test_a_withheld_hit_does_not_move_the_scores_of_the_visible_ones(
    crowded: ProjectRegistry,
) -> None:
    """The same oracle one field to the right, stated on its own so it stays shut.

    `count` is a single number and saturates: once `limit` is smaller than the
    number of visible matches it stops moving. `fusedScore` does not. It is
    published per hit to six decimal places and every one of them shifts by a
    rank when a withheld chunk ranks above it, so a saturated `count` and a full
    result set still carried the signal.

    The fix was first to re-fuse over what survived the gate, and is now to fuse
    only what the gate ever admitted — the retrievers are read through a
    `Visibility`, so the ranks that reach RRF are already the ranks an index
    without the withheld document would have produced. That is not an
    approximation of the number it replaces; it is the number.
    """
    probe = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {LEAKED_CREDENTIAL}",
        maxTokens=32_000,
    )
    control = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {CROWD_CONTROL}",
        maxTokens=32_000,
    )

    assert [hit["fusedScore"] for hit in probe["results"]] == [
        hit["fusedScore"] for hit in control["results"]
    ]
    assert [hit["itemId"] for hit in probe["results"]] == [
        hit["itemId"] for hit in control["results"]
    ], "nor may the order move, which is the same read one step less directly"


# -- One status authority, reached from every tool ---------------------------


@pytest.mark.asyncio
async def test_knowledge_get_will_not_hand_over_what_search_withheld(
    registry: ProjectRegistry,
) -> None:
    """The bypass that survived three separate fixes to `knowledge.search`.

    Closing every path through search achieved nothing while `get` had no gate:
    a caller reads an approved item, takes the `targetItemId` off its relation,
    and fetches the withheld body in one more call. No flag, no guessing.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    withheld = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.caching-draft"
    )

    assert "not present" in withheld


@pytest.mark.asyncio
async def test_the_withheld_message_does_not_confirm_the_item_exists(
    registry: ProjectRegistry,
) -> None:
    """SEC-13. A distinct message would tell a caller that an id they may not
    read is nonetheless a real id — the inference the isolation exists to
    prevent."""
    absent = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.no-such-thing"
    )
    withheld = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.caching-draft"
    )

    assert absent.replace("no-such-thing", "X") == withheld.replace("caching-draft", "X")


@pytest.mark.asyncio
async def test_asking_for_unapproved_reaches_a_draft_through_get(
    registry: ProjectRegistry,
) -> None:
    """The flag widens which statuses are allowed. It is not a bypass, and it is
    also not a lock: a draft is work in progress an author has reason to read."""
    result = await _call(
        registry,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.caching-draft",
        includeUnapproved=True,
    )

    assert result["status"] == "draft"


#: A rejected item, an approved one, and one relation from `auth-policy` to each.
#:
#: The relation gate at `knowledge.get` had no corpus at all: no fixture in the
#: repository issued `addRelation`, so the test that named the gate asserted a
#: `not in` over an empty list and passed with the gate replaced by
#: `target.item_id is not None`. Both edges are needed, and for different
#: reasons -- the rejected one is the leak, and the approved one is what keeps
#: the assertion from going vacuous again if a later fixture change loses the
#: relations. `related_to` rather than a second `rejects` because it has no
#: inverse either, so both edges reach `knowledge.get` in the direction they
#: were written.
RELATION_MIGRATION_ID = "01K1RNNNNN01234567890ABCDE"
REJECTED_ID = "01K1RRRRRR01234567890ABCDE"
REJECTED_REVISION_ID = "01K1RRRREV01234567890ABCDE"
ROTATION_ID = "01K1TTTTTT01234567890ABCDE"
ROTATION_REVISION_ID = "01K1TTTREV01234567890ABCDE"

#: The note on the rejected edge. Published beside `targetItemId` by the same
#: comprehension, so it is a second field to check rather than decoration -- an
#: implementation that blanked the id and kept the note would leak the reason a
#: revision was rejected, which is the content SEC-13 withholds the body for.
REJECTED_RELATION_NOTE = "superseded by the signed-token design"

REJECTED_BODY = "# Plaintext token store\n\nTokens were kept unhashed in `sessions.token`.\n"
ROTATION_BODY = "# Token rotation\n\nRotating a credential invalidates the previous one.\n"

RELATION_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {RELATION_MIGRATION_ID}
createdAt: 2026-08-02T15:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.plaintext-token-store
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.plaintext-token-store
    revisionId: {REJECTED_REVISION_ID}
    contentFile: ../knowledge/architecture/plaintext-token-store.md
    metadata:
      title: Plaintext token store
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/plaintext-token-store.md
  - op: createItem
    itemId: architecture.token-rotation
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.token-rotation
    revisionId: {ROTATION_REVISION_ID}
    contentFile: ../knowledge/architecture/token-rotation.md
    metadata:
      title: Token rotation
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/token-rotation.md
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: rejects
    targetItemId: architecture.plaintext-token-store
    note: {REJECTED_RELATION_NOTE}
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: related_to
    targetItemId: architecture.token-rotation
    note: the rotation procedure this policy assumes
"""


@pytest.fixture
def with_a_rejected_relation(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` project, plus the two edges above off `architecture.auth-policy`."""
    root = Path(registry.load()["demo"]["rootPath"])
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "plaintext-token-store.md").write_text(REJECTED_BODY)
    (knowledge / "token-rotation.md").write_text(ROTATION_BODY)
    (root / f".theurian/migrations/{RELATION_MIGRATION_ID}-relations.yaml").write_text(
        RELATION_MIGRATION
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


def _stored_relations(registry: ProjectRegistry, item_id: str) -> tuple[str, ...]:
    """The relation targets the canonical store actually holds for ``item_id``.

    Resolved through the active-state pointer, not by globbing: `migrate apply`
    leaves the previous state database beside the new one, so a glob picks one of
    two and the test would depend on filesystem ordering. This is the same
    database the tool under test reads.
    """
    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    context = RequestContext(project_id=ProjectId("demo"))
    with SqliteCanonicalStore(paths.state / active.database_filename) as store:
        return tuple(
            relation.target_item_id.value
            for relation in store.list_relations(context, ItemId(item_id))
        )


@pytest.mark.asyncio
async def test_the_rejected_relation_really_reaches_the_canonical_store(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """Guards the guard below, which is the whole reason this round exists.

    The gate can only be observed while there is something for it to withhold.
    With no `addRelation` anywhere in the suite, `knowledge.get` published an
    empty list and the assertion that named the gate held over nothing. This
    reads the store directly, so a migration that silently stopped applying
    fails here rather than turning the next test green for the wrong reason.
    """
    stored = _stored_relations(with_a_rejected_relation, "architecture.auth-policy")

    assert stored == (
        "architecture.plaintext-token-store",
        "architecture.token-rotation",
    ), "both edges are recorded, and the gate is the only thing that removes one"


@pytest.mark.asyncio
async def test_relations_to_withheld_items_are_not_published(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """Withholding a body while publishing the id it lives at withholds nothing
    that matters — the id is how the body was found in the first place.

    Asserted as the exact published list rather than as an absence, because an
    absence over an empty list is what this test used to be. The approved edge
    has to survive: a gate that published nothing would satisfy "the rejected id
    is gone" and break `knowledge.get` for every honest caller.
    """
    result = await _call(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.auth-policy",
    )

    assert [relation["targetItemId"] for relation in result["relations"]] == [
        "architecture.token-rotation"
    ]
    assert [relation["relationType"] for relation in result["relations"]] == ["related_to"]


@pytest.mark.asyncio
async def test_no_field_of_a_withheld_relation_survives_into_the_payload(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """`targetItemId` is not the only thing the comprehension publishes.

    `note` says *why* a revision was rejected, which is the same class of content
    as the body the id would have been used to fetch. Asserted over the whole
    serialised payload so a field added to the relation shape later is covered
    without this test being edited (SEC-13).
    """
    result = await _call(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.auth-policy",
    )

    serialised = json.dumps(result)
    assert result["relations"], "the visible edge must still be there, or this asserts nothing"
    assert "plaintext-token-store" not in serialised
    assert REJECTED_RELATION_NOTE not in serialised
    assert "rejects" not in serialised


@pytest.mark.asyncio
async def test_the_rejected_item_is_unreachable_through_the_flag_that_reaches_a_draft(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """`includeUnapproved` widens the allowed statuses; it does not disable them.

    A caller who has the rejected id from somewhere else must not be able to turn
    the relation gate's own opt-in into a bypass — `rejected` is outside
    `SURFACEABLE_STATUSES` and no flag adds it back.
    """
    result = await _call(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.auth-policy",
        includeUnapproved=True,
    )
    message = await _call_failing(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.plaintext-token-store",
        includeUnapproved=True,
    )

    assert [relation["targetItemId"] for relation in result["relations"]] == [
        "architecture.token-rotation"
    ]
    assert "not present" in message


# -- The same gate, in the direction nothing in this repository ever built ---
#
# `list_relations` returns every edge touching an item in *both* directions, and
# mirrors only the four types in `INVERSE_RELATIONS` on the way out. For every
# other type -- `rejects`, `related_to`, `contradicts`, `depends_on` -- an
# *incoming* edge comes back in the orientation it was stored in, so
# `target_item_id` is the item being fetched. The gate asked whether the target
# could be surfaced, which on those rows asks about the item the caller is
# already holding, and published the edge together with the note its withheld
# source had written. Measured on a real project before the fix:
#
#     {"relationType": "contradicts", "targetItemId": "architecture.auth-policy",
#      "note": "REJECTED BECAUSE sessions.token held raw bearer tokens until 2026-07"}
#
# `with_a_rejected_relation` above cannot show that, and neither can anything
# else here: both of its edges are outgoing, and no other fixture in the
# repository issues an `addRelation` at all. So the review round that went
# looking for this defect ran a suite in which every relation assertion agreed
# with it, and the fix landed with nothing going red.
#
# The corpus below is built so that cannot recur: every no-inverse type gets its
# own fetched item and its own incoming edge, and a second project holds the same
# visible content with the withheld item and its edges never written.

GATE_PROBE = "relations-probe"
GATE_ABSENT = "relations-absent"

#: The four relation types absent from `INVERSE_RELATIONS`, and therefore the
#: four whose incoming edges reach a reader unmirrored. Written out rather than
#: derived from the mapping, so this is a claim about which types matter rather
#: than a restatement of whatever the mapping currently holds. The premise --
#: that none of the four has an inverse -- is pinned in
#: `tests/unit/test_domain_invariants.py`, by
#: `test_the_four_types_that_reach_a_reader_unmirrored_have_no_inverse`.
NO_INVERSE_TYPES = ("rejects", "related_to", "contradicts", "depends_on")

#: The `rejected` item every withheld edge below hangs off.
WITHHELD_SOURCE = "architecture.plaintext-token-store"
#: A `draft` item. `rejected` is reachable through no flag, so a rejected source
#: cannot show that the gate *widens* correctly; a draft one can.
DRAFT_SOURCE = "architecture.session-cookies"
#: The approved item every fetched item keeps one publishable edge to, so no
#: assertion below is satisfied by an implementation that publishes nothing.
VISIBLE_PEER = "architecture.token-rotation"
#: The fetched item for the inverse-bearing type, reached from three directions.
INVERSE_TARGET = "architecture.gate-implements"
#: The fetched item the draft points at.
DRAFT_TARGET = "architecture.gate-draft-source"


def _gate_target(relation_type: str) -> str:
    """The approved item that receives an incoming edge of ``relation_type``.

    One fetched item per type rather than one item with four edges: with a single
    item every parameter would assert the same list, so a gate that handled
    `contradicts` and leaked `depends_on` would fail all four identically and
    name none of them.
    """
    return f"architecture.gate-{relation_type.replace('_', '-')}"


def _withheld_note(marker: str) -> str:
    """The note on an edge whose far end the caller may not see.

    A rejection rationale is where the secret that caused the rejection lives, so
    `note` is the field this defect actually leaked -- the withheld *id* never
    appeared. Tagged by the edge that carries it so a leak names its own route
    instead of only proving that some route is open.
    """
    return f"REJECTED BECAUSE sessions.token held raw bearer tokens ({marker})"


@dataclass(frozen=True, slots=True)
class _GateItem:
    """One item in the gate corpus. ``revision_id`` is fixed so both projects mint
    the same one -- the differential compares whole responses, and `revisionId`
    is published."""

    slug: str
    status: str
    revision_id: str

    @property
    def item_id(self) -> str:
        return f"architecture.{self.slug}"

    @property
    def title(self) -> str:
        return self.slug.replace("-", " ").capitalize()


#: Present in both projects.
GATE_SHARED_ITEMS = (
    _GateItem("token-rotation", "approved", "01K1SREV0101234567890ABCDE"),
    _GateItem("session-cookies", "draft", "01K1SREV0201234567890ABCDE"),
    _GateItem("gate-rejects", "approved", "01K1SREV0401234567890ABCDE"),
    _GateItem("gate-related-to", "approved", "01K1SREV0501234567890ABCDE"),
    _GateItem("gate-contradicts", "approved", "01K1SREV0601234567890ABCDE"),
    _GateItem("gate-depends-on", "approved", "01K1SREV0701234567890ABCDE"),
    _GateItem("gate-implements", "approved", "01K1SREV0801234567890ABCDE"),
    _GateItem("gate-draft-source", "approved", "01K1SREV0901234567890ABCDE"),
)

#: Written only into `relations-probe`.
GATE_WITHHELD_ITEM = _GateItem("plaintext-token-store", "rejected", "01K1SREV0301234567890ABCDE")

#: Edges both projects hold, byte for byte. Every one of them must be published.
GATE_VISIBLE_EDGES: tuple[tuple[str, str, str, str], ...] = (
    *(
        (_gate_target(relation_type), "related_to", VISIBLE_PEER, f"the peer for {relation_type}")
        for relation_type in NO_INVERSE_TYPES
    ),
    (INVERSE_TARGET, "related_to", VISIBLE_PEER, "the peer for implements"),
    (DRAFT_TARGET, "related_to", VISIBLE_PEER, "the peer for the draft source"),
    # A type *with* an inverse, both ways round. The store mirrors the incoming
    # one, which is a different code path from everything above and was pinned in
    # neither direction.
    (VISIBLE_PEER, "implements", INVERSE_TARGET, "incoming from a visible item, mirrored"),
    (INVERSE_TARGET, "implements", VISIBLE_PEER, "outgoing, a type that has an inverse"),
    # Incoming from a draft: withheld by default, published when widened.
    (DRAFT_SOURCE, "contradicts", DRAFT_TARGET, "incoming from a draft"),
)

#: Edges only `relations-probe` holds. None may be published under any flag.
GATE_WITHHELD_EDGES: tuple[tuple[str, str, str, str], ...] = (
    *(
        (WITHHELD_SOURCE, relation_type, _gate_target(relation_type), _withheld_note(relation_type))
        for relation_type in NO_INVERSE_TYPES
    ),
    (WITHHELD_SOURCE, "implements", INVERSE_TARGET, _withheld_note("implements")),
    # Outgoing to the withheld item: the direction the old gate did catch, kept
    # so the differential covers it too.
    (_gate_target("depends_on"), "related_to", WITHHELD_SOURCE, _withheld_note("outgoing")),
)

#: The items the two projects genuinely disagree about, and therefore the only
#: ones the differential can say anything with. `gate-draft-source` is left out
#: deliberately: the draft and its edge exist in *both* projects, so comparing
#: that item would compare two identical corpora and hold whatever the gate did.
GATE_DIFFERENTIAL_ITEMS = (
    *(_gate_target(relation_type) for relation_type in NO_INVERSE_TYPES),
    INVERSE_TARGET,
)

GATE_BASE_MIGRATION_ID = "01K1SAAAAA01234567890ABCDE"
GATE_WITHHELD_MIGRATION_ID = "01K1SBBBBB01234567890ABCDE"

GATE_ITEM_OPERATIONS = """  - op: createItem
    itemId: {item_id}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item_id}
    revisionId: {revision_id}
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
          sourceUri: git://gate/{slug}.md
"""


def _gate_migration(
    migration_id: str,
    created_at: str,
    items: Sequence[_GateItem],
    edges: Sequence[tuple[str, str, str, str]],
) -> str:
    operations = "".join(
        GATE_ITEM_OPERATIONS.format(
            item_id=item.item_id,
            revision_id=item.revision_id,
            slug=item.slug,
            title=item.title,
            status=item.status,
        )
        for item in items
    ) + "".join(
        f"  - op: addRelation\n"
        f"    sourceItemId: {source}\n"
        f"    relationType: {relation_type}\n"
        f"    targetItemId: {target}\n"
        f"    note: {note}\n"
        for source, relation_type, target, note in edges
    )
    return (
        f"apiVersion: theurian.dev/v1\n"
        f"id: {migration_id}\n"
        f"createdAt: {created_at}\n"
        f"author: engineer@example.com\n"
        f"operations:\n"
        f"{operations}"
    )


def _build_gate_project(root: Path, *, holds_the_withheld_source: bool) -> None:
    """One project of the pair, differing only in whether the withheld item exists.

    The shared content lives in one migration written identically into both, and
    the withheld item and its six edges live in a second migration only the probe
    receives. Applied in id order, so the edges land after both endpoints exist.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _run("init")
    _run("project", "register")
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"

    for item in GATE_SHARED_ITEMS:
        (knowledge / f"{item.slug}.md").write_text(f"# {item.title}\n\nBody for {item.item_id}.\n")
    (migrations / f"{GATE_BASE_MIGRATION_ID}-shared.yaml").write_text(
        _gate_migration(
            GATE_BASE_MIGRATION_ID,
            "2026-08-03T10:00:00+09:00",
            GATE_SHARED_ITEMS,
            GATE_VISIBLE_EDGES,
        )
    )

    if holds_the_withheld_source:
        item = GATE_WITHHELD_ITEM
        (knowledge / f"{item.slug}.md").write_text(f"# {item.title}\n\nBody for {item.item_id}.\n")
        (migrations / f"{GATE_WITHHELD_MIGRATION_ID}-withheld.yaml").write_text(
            _gate_migration(
                GATE_WITHHELD_MIGRATION_ID,
                "2026-08-03T11:00:00+09:00",
                (item,),
                GATE_WITHHELD_EDGES,
            )
        )

    _run("migrate", "apply")


@pytest.fixture(scope="module")
def gate_projects(tmp_path_factory: pytest.TempPathFactory) -> ProjectRegistry:
    """Two projects in one registry, differing in one thing.

    ``relations-probe``  holds the rejected item and the six edges touching it
    ``relations-absent`` was never told that item exists

    Everything else is held equal on purpose -- the item ids, the revision ids,
    the bodies, the notes and the visible edges -- because the differential below
    compares whole responses, and any honest difference between the two projects
    would have to be excluded from that comparison and would take a real leak
    with it.

    Module-scoped: two real projects through the real CLI, and every test here
    asks the same pair the same questions.
    """
    base = tmp_path_factory.mktemp("relation-gate")
    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(base / "datadir"))
    try:
        for project_id, holds in ((GATE_PROBE, True), (GATE_ABSENT, False)):
            root = base / project_id
            root.mkdir()
            monkey.chdir(root)
            _build_gate_project(root, holds_the_withheld_source=holds)
    finally:
        monkey.undo()
    return ProjectRegistry.default(base / "datadir")


def _published(result: dict[str, Any]) -> tuple[tuple[str, str, str | None], ...]:
    """The relations a caller receives, in wire order.

    Order is not incidental: `list_relations` sorts by source, type and target in
    SQL, so a published list is deterministic and an assertion may state it. A
    `set` here would hide a gate that published the right rows in an order driven
    by which ones were filtered out.
    """
    return tuple(
        (relation["relationType"], relation["targetItemId"], relation["note"])
        for relation in result["relations"]
    )


def _gate_relation_rows(
    registry: ProjectRegistry, project_id: str
) -> tuple[tuple[str, str, str], ...]:
    """Every edge in ``project_id``'s canonical store, read with SQL.

    Deliberately not through `list_relations`: that is the call the gate filters,
    and a corpus guard that used it could not tell "the gate withheld this row"
    from "this row was never stored". Counting rows in the table is the only
    reading that distinguishes them -- the original relation test evaluated
    `all(...)` over an empty list and passed for exactly that reason.

    Resolved through the active-state pointer rather than a glob, because
    `migrate apply` leaves the previous database beside the new one.
    """
    paths = ProjectPaths.of(Path(registry.load()[project_id]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, f"{project_id} must have built a canonical state"
    with contextlib.closing(sqlite3.connect(paths.state / active.database_filename)) as connection:
        rows = connection.execute(
            "SELECT source_item_id, relation_type, target_item_id FROM knowledge_relations "
            "WHERE project_id = ? ORDER BY source_item_id, relation_type, target_item_id",
            (project_id,),
        ).fetchall()
    return tuple((str(a), str(b), str(c)) for a, b, c in rows)


def _authored(edges: Sequence[tuple[str, str, str, str]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted((source, kind, target) for source, kind, target, _ in edges))


def test_the_incoming_edges_really_reach_the_canonical_store(
    gate_projects: ProjectRegistry,
) -> None:
    """Guards the guard. The gate can only be observed while it holds something.

    Every assertion below is an absence, and an absence over a corpus that was
    never written is free. This counts the rows in `knowledge_relations`
    directly, so a migration that silently stopped applying -- a renamed
    operation, a rejected id, an edge dropped for referencing an item that does
    not exist yet -- fails here instead of turning six tests green for the wrong
    reason.

    The five incoming edges are named individually rather than counted, because
    a count cannot say which direction they were stored in, and direction is the
    whole defect.
    """
    rows = _gate_relation_rows(gate_projects, GATE_PROBE)

    assert rows == _authored(GATE_VISIBLE_EDGES + GATE_WITHHELD_EDGES)
    assert tuple(row for row in rows if row[0] == WITHHELD_SOURCE) == (
        (WITHHELD_SOURCE, "contradicts", "architecture.gate-contradicts"),
        (WITHHELD_SOURCE, "depends_on", "architecture.gate-depends-on"),
        (WITHHELD_SOURCE, "implements", INVERSE_TARGET),
        (WITHHELD_SOURCE, "rejects", "architecture.gate-rejects"),
        (WITHHELD_SOURCE, "related_to", "architecture.gate-related-to"),
    ), "the withheld item must be the *source* of these, or nothing incoming is being tested"


def test_the_control_project_holds_the_visible_edges_and_nothing_else(
    gate_projects: ProjectRegistry,
) -> None:
    """Guards the differential's other half: `relations-absent` is a control, not
    an empty project.

    If it lost the visible edges too, the comparison below would hold between two
    answers that publish nothing, and a gate that withheld everything would pass.
    If it gained the withheld ones, there would be no difference left to detect.
    Both are stated as one exact row set.
    """
    rows = _gate_relation_rows(gate_projects, GATE_ABSENT)

    assert rows == _authored(GATE_VISIBLE_EDGES)
    assert not any(WITHHELD_SOURCE in row for row in rows), (
        "the control must never have heard of the withheld item"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_unapproved", [False, True], ids=["default", "widened"])
@pytest.mark.parametrize("item_id", GATE_DIFFERENTIAL_ITEMS)
async def test_a_withheld_relation_endpoint_changes_nothing_a_caller_can_see(
    gate_projects: ProjectRegistry, item_id: str, include_unapproved: bool
) -> None:
    """SEC-13. Milestone 5's closure argument, applied to `knowledge.get`.

    One request against two corpora: one whose canonical state holds an item the
    caller may not read together with every edge touching it, one that was never
    told the item exists. The **whole response** must be identical -- all sixteen
    published fields, `body`, `excerpt`, `revisionId`, `freshness`,
    `sourceAnchors` and the three trust labels among them, plus any field added
    to this payload after today.

    Field-by-field is what the previous round did, and it is how this defect
    survived: `targetItemId` was checked, and the leak was in `note`. A whole-
    response comparison also covers the family that is not a field value at all
    -- *which rows*, or which part of a row, reached the caller -- since a
    displaced or reordered relation changes the list without changing any field's
    domain.

    Nothing is excluded from the comparison, which is worth saying because the
    equivalent test for `knowledge.search` has to exclude two build identities.
    `knowledge.get` publishes no build identity and no count, so the two
    responses are required to be equal outright.

    Asserted under both flags. `includeUnapproved=True` is the one that widens
    which statuses may surface, and `rejected` is outside `SURFACEABLE_STATUSES`
    under every flag -- so the equality is required to survive the widening, not
    to depend on it.

    One field is wall-clock-derived and shared rather than pinned:
    `freshness.ageDays` is `now - revisionCreatedAt` in whole days, and the two
    calls below are milliseconds apart, so they agree except across the instant
    the day count ticks. If this ever fails on `ageDays` alone and on nothing
    else, that is the cause and not a leak -- `revisionCreatedAt` itself comes
    from the migration and is identical in both projects by construction.
    """
    arguments = {"itemId": item_id, "includeUnapproved": include_unapproved}
    probe = await _call(gate_projects, "knowledge.get", projectId=GATE_PROBE, **arguments)
    absent = await _call(gate_projects, "knowledge.get", projectId=GATE_ABSENT, **arguments)

    assert probe["relations"], "two empty relation lists would be equal and prove nothing"
    assert probe == absent


@pytest.mark.asyncio
@pytest.mark.parametrize("relation_type", NO_INVERSE_TYPES)
async def test_an_incoming_edge_from_a_withheld_item_is_not_published(
    gate_projects: ProjectRegistry, relation_type: str
) -> None:
    """SEC-13. The four types whose incoming edges reach a reader unmirrored.

    On each of these an incoming edge arrives with `target_item_id` set to the
    item being fetched, so a gate that asks only about the target asks whether
    the caller may see what they are already holding. It answered yes for all
    four and published the source's note.

    One fetched item per type, so a gate that closed `contradicts` and left
    `depends_on` open fails at the parameter that names it. Asserted as the exact
    published list rather than as the absence of a string: an absence is what the
    round-six test asserted, and the visible edge has to survive or a gate that
    withheld every relation would satisfy it.
    """
    result = await _call(
        gate_projects, "knowledge.get", projectId=GATE_PROBE, itemId=_gate_target(relation_type)
    )

    assert _published(result) == (("related_to", VISIBLE_PEER, f"the peer for {relation_type}"),)
    assert _withheld_note(relation_type) not in json.dumps(result), (
        "the note is the field that leaked; the withheld id never appeared"
    )


@pytest.mark.asyncio
async def test_a_type_with_an_inverse_is_gated_in_both_directions(
    gate_projects: ProjectRegistry,
) -> None:
    """`implements` reaches this item three ways, and only two may be published.

    The store mirrors an incoming edge of a type that has an inverse, which is a
    different code path from every type above and was pinned in neither
    direction. All three are asserted at once because the mirroring is what makes
    them hard to tell apart on the wire: the withheld source's edge and the
    visible peer's edge are the same shape until the gate runs, and the published
    `implemented_by` row proves the mirror still happens for the one that clears
    it. A gate that suppressed both would look identical to a correct one if only
    the withheld direction were checked.

    Order is the store's: source id, then type, then target.
    """
    result = await _call(
        gate_projects, "knowledge.get", projectId=GATE_PROBE, itemId=INVERSE_TARGET
    )

    assert _published(result) == (
        ("implements", VISIBLE_PEER, "outgoing, a type that has an inverse"),
        ("related_to", VISIBLE_PEER, "the peer for implements"),
        ("implemented_by", VISIBLE_PEER, "incoming from a visible item, mirrored"),
    )


@pytest.mark.asyncio
async def test_an_incoming_edge_from_a_draft_reappears_when_the_flag_widens(
    gate_projects: ProjectRegistry,
) -> None:
    """Over-blocking is a defect too, and this is the case that distinguishes them.

    A gate that withheld every incoming edge would satisfy every assertion above.
    `includeUnapproved` widens which statuses may surface, and a draft is work in
    progress its author has reason to read -- so the same edge that is correctly
    absent by default has to come back, note included.

    It comes back naming the *fetched* item as its `targetItemId`, because an
    incoming edge of a type with no inverse is published in the orientation it
    was stored in. That is the shape, not a leak: the gate has cleared both ends
    before this row is published. It is pinned here so a later change to the wire
    shape is a decision rather than an accident.
    """
    default = await _call(gate_projects, "knowledge.get", projectId=GATE_PROBE, itemId=DRAFT_TARGET)
    widened = await _call(
        gate_projects,
        "knowledge.get",
        projectId=GATE_PROBE,
        itemId=DRAFT_TARGET,
        includeUnapproved=True,
    )

    assert _published(default) == (("related_to", VISIBLE_PEER, "the peer for the draft source"),)
    assert _published(widened) == (
        ("related_to", VISIBLE_PEER, "the peer for the draft source"),
        ("contradicts", DRAFT_TARGET, "incoming from a draft"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_unapproved", [False, True], ids=["default", "widened"])
async def test_an_incoming_edge_from_a_rejected_item_survives_no_flag(
    gate_projects: ProjectRegistry, include_unapproved: bool
) -> None:
    """`includeUnapproved` widens the allowed statuses; `rejected` is not among
    them under either setting.

    Stated as the same exact list under both flags, and separately from the draft
    above, because the two are the pair that has to be told apart: a caller who
    can widen their way to a draft's note must not widen their way to a rejected
    revision's, which is where the reason for the rejection is written.
    """
    result = await _call(
        gate_projects,
        "knowledge.get",
        projectId=GATE_PROBE,
        itemId=_gate_target("contradicts"),
        includeUnapproved=include_unapproved,
    )

    assert _published(result) == (("related_to", VISIBLE_PEER, "the peer for contradicts"),)


# -- `itemId` that is not an id at all ---------------------------------------


@pytest.mark.asyncio
async def test_a_malformed_item_id_names_the_tool_that_finds_a_real_one(
    registry: ProjectRegistry,
) -> None:
    """FR-R6. An error without a next action is a support request.

    `InvalidIdentifierError` carries a format rule and no remedy, and the SDK
    re-raises whatever escapes a tool as `Error executing tool …: {exc}` --
    keeping `str(exc)` and dropping everything else. So a caller who passed a
    title where an id belongs was told what an id looks like and not how to find
    one.

    Naming `knowledge.search` discloses nothing SEC-13 protects: every stored id
    passed this same validation, so a string that fails it names no item,
    withheld or otherwise.
    """
    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="Authentication Policy"
    )

    assert "knowledge.search" in message, "the remedy must name the tool that returns real ids"
    assert "demo" in message, "and the project to run it against"


@pytest.mark.asyncio
async def test_an_over_long_item_id_is_not_echoed_back(registry: ProjectRegistry) -> None:
    """The failure `MAX_QUERY_CHARS` closes for `query`, closed here for `itemId`.

    A message built from an unbounded input is an amplifier: whatever reads the
    error -- a log line, an agent's context window, a bug report -- receives the
    whole of what the caller sent. `ItemId` checks length before it quotes the
    value, so the message reports the length and never the string.

    Bounded at 500 rather than pinned at the 183 characters measured today: the
    property is that the message does not grow with its input, and 183 is one
    wording of it.

    **`projectId` is a third member of this class and is known and open.**
    `mcp/tools.py`'s `_unresolvable` interpolates it with no bound: measured
    through this same entry point, 2,000,000 characters in produce a 2,000,141
    character message, against 2,000 for `query` and 185 for `itemId`. It is
    named here rather than left out, because a class carrying an unnamed member
    comes back a round later as another instance of the one that was closed. The
    reasoning for converting it rather than fixing it in Milestone 5, and the
    decision it is waiting on, are T-6 in the threat model and
    https://github.com/theurian/theurian/issues/17. Delete this paragraph in the
    change that closes it.
    """
    message = await _call_failing(registry, "knowledge.get", projectId="demo", itemId="a" * 20_000)

    assert "aaaa" not in message, "not even a truncated prefix of the identifier"
    assert "20000" in message, "the length is what the caller needs to see"
    assert len(message) < 500, f"the message grew with its input ({len(message)} characters)"


# -- The candidate depth itself, and the equality that closes the family ------
#
# The three sections above each closed one number by moving it past the gate:
# `usedTokens`, then `count`, then `fusedScore`. Two more were left, and neither
# is a number:
#
# - `CANDIDATE_DEPTH` rows were read from each retriever *before* anything asked
#   who may see them, so a withheld row took one of the fifty and the fiftieth
#   visible row fell off the end. Measured on the shipped code: 442 ordinary
#   `knowledge.search` calls recovered a sixteen-character credential, at the
#   **default** token budget, because `droppedForBudget` publishes the size of
#   the gate-cleared set. On a Japanese corpus the precondition is automatic —
#   `unicode61` cannot segment CJK, so the trigram retriever's fifty slots *are*
#   the candidate list.
# - `diversify(per_item=...)` also ran before the gate, so *which chunk* of a
#   document survived was decided by a ranking that included withheld rows.
#   Re-fusing afterwards cannot undo that: the discarded chunk is gone. Measured
#   over 20,000 random rank arrangements, chunk identity moved 9.1% of the time,
#   visible item order 3.4%, published `fusedScore` 3.6% — a different `excerpt`
#   for the same document, from two queries differing only in a token no visible
#   document contains.
#
# Both are the same defect, and it is not one a comparison of two *queries* can
# state fully: `count` saturates, `fusedScore` needs a tie, and under
# `useDense=True` two different query strings legitimately produce different
# dense ranks for visible documents, so a whole-response comparison would fail
# for an honest reason.
#
# So the property is stated against two *corpora* and one query: the response
# must equal what an index that never held the withheld document would return.
# Nothing about the query varies, which is what makes the comparison total —
# every field, `useDense` included.

#: Enough approved documents to fill every retriever's candidate depth. Smaller
#: and the boundary is unreachable: the withheld document has to take a slot a
#: visible one would otherwise have had, which cannot happen until the slots run
#: out.
#:
#: **An absolute number, not `CANDIDATE_DEPTH + 5`.** Written as an offset from
#: the constant, the crowd resized itself whenever the constant moved, so the
#: fixture saturated the pipeline at any depth and pinned none: `CANDIDATE_DEPTH`
#: was set to 5 and to 200 and this whole module passed. Fifty-five is fifty —
#: the depth the T-17 measurements were taken at — plus the five that make the
#: fiftieth visible row displaceable. If the pipeline's depth ever exceeds this,
#: `test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth`
#: fails, which is the correct outcome: the fixture would no longer reach the
#: boundary it exists to sit on.
DEPTH_CROWD = 55

#: The number of visible rows the pipeline is documented to read per retriever,
#: and the depth every T-17 and SEC-13 measurement in the threat model was taken
#: at. Stated here in its own right so the fixture describes a *corpus* rather
#: than restating whatever the implementation currently holds.
DOCUMENTED_DEPTH = 50

#: The two fields excluded from the comparisons below, and the only two.
#:
#: They name *which* build and *which* canonical state answered, and two
#: separately created projects cannot share either: `indexBuildId` is a fresh
#: ULID per build and `snapshotId` is a hash over content that differs by the
#: withheld document itself. Excluding a field is how a comparison quietly gets
#: narrower than the property it claims, so both are covered instead by
#: `test_the_build_identity_a_search_reports_does_not_vary_with_the_query`,
#: which shows they are the same for every query against one project and
#: therefore cannot answer "did this query match something withheld?".
BUILD_IDENTITY = ("indexBuildId", "snapshotId")

#: One template for the whole crowd. `EXTRA_MIGRATION` above encodes its ids as
#: a single letter, which runs out well before `CANDIDATE_DEPTH` documents.
DEPTH_NOTE_MIGRATION = """apiVersion: theurian.dev/v1
id: {mid}
createdAt: 2026-08-02T12:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: {rid}
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""

DEPTH_RUNBOOK_ID = "01K1FAAAAA01234567890ABCDE"
DEPTH_RETIRE_ID = "01K1GAAAAA01234567890ABCDE"
DEPTH_LATE_ID = "01K1HAAAAA01234567890ABCDE"


def _depth_ulid(tag: str, number: int) -> str:
    """A deterministic ULID for the crowd, so every project mints the same ones.

    Chunk ids are `<revision ULID>#<ordinal>` and every tie in this corpus of
    near-identical notes breaks on chunk id, so two projects that disagree about
    these ids would produce two different orders for honest reasons and the
    comparison would prove nothing.

    ``tag`` is Crockford base32, which has no ``I``, ``L``, ``O`` or ``U``.
    """
    return f"01K1{tag}{number:03d}".ljust(26, "0")[:26]


@dataclass(frozen=True, slots=True)
class _DepthCorpus:
    """One writing system's version of the crowd, and how retrieval behaves on it.

    The equality property was pinned against English only, and English is the
    easy case: `unicode61` splits it on word boundaries, so the word index ranks
    the whole crowd and the trigram index is a second opinion. Japanese is the
    case the trigram index exists for (ADR-0023), and the situation there is
    materially different — and worse. `unicode61` cannot segment CJK, so a run of
    Japanese between two punctuation marks is *one* token: the word index cannot
    match `ゲートウェイ` inside it and has nothing to contribute, which makes the
    trigram retriever's fifty candidate slots the entire candidate list. That is
    why the extraction attack needed no filler trick on a Japanese corpus — and
    why a property held only against English was held against the writing system
    that needs it least.

    Held equal across both: the ids, the crowd size, the query's shape (a common
    term the crowd matches plus the credential only the withheld document holds),
    and the staleness of every index.
    """

    #: The pytest parameter id, so a failure names the writing system.
    id: str
    #: Matches the crowd through its common term and the withheld document
    #: through the credential — the two halves the channel needs.
    query: str
    #: The crowd note body and title, by ordinal.
    note: str
    note_title: str
    #: The document that gets withheld, with and without the thing worth
    #: stealing. Both bodies match the crowd's own term, so in both projects the
    #: withheld document is a real candidate the retrievers rank. Only one of
    #: them can be reached by a query for the credential, which is the single
    #: difference the response is required not to notice.
    runbook: str
    runbook_secret_tail: str
    runbook_clean_tail: str
    runbook_title: str
    #: The unrelated item created after the build in the project that never held
    #: the runbook, so that project is left exactly as stale as the other two.
    unrelated_title: str
    unrelated_body: str
    #: How many rows the *word* index can offer for `query` at
    #: :data:`DOCUMENTED_DEPTH`, measured rather than reasoned about. This is the
    #: number that says the two corpora are different machines: fifty in English,
    #: and in Japanese exactly one — the withheld document, reached through the
    #: credential, which is the only part of the query `unicode61` can tokenize
    #: out of that corpus.
    word_index_rows: int

    def body(self, number: int) -> str:
        return self.note.format(number=number)

    def title(self, number: int) -> str:
        return self.note_title.format(number=number)

    def runbook_body(self, *, secret: bool) -> str:
        tail = self.runbook_secret_tail if secret else self.runbook_clean_tail
        return self.runbook.format(tail=tail)


ENGLISH_DEPTH = _DepthCorpus(
    id="english",
    query=f"gateway {LEAKED_CREDENTIAL}",
    note=(
        "# Gateway note {number}\n\nThe shared gateway meters every request for "
        "tenant {number}. The gateway rejects an unsigned request and records it "
        "in the ledger.\n"
    ),
    note_title="Gateway note {number}",
    runbook=(
        "# Tenant quarantine playbook\n\nWhen the shared gateway vault {tail}, the "
        "responder isolates the affected tenant and records the rehearsal in the ledger.\n"
    ),
    runbook_secret_tail=f"credential {LEAKED_CREDENTIAL} leaks",
    runbook_clean_tail="credential is rotated on schedule",
    runbook_title=RUNBOOK_TITLE,
    unrelated_title="Ledger retention",
    unrelated_body="# Ledger retention\n\nRecords are kept for seven years.\n",
    word_index_rows=DOCUMENTED_DEPTH,
)

JAPANESE_DEPTH = _DepthCorpus(
    id="japanese",
    query=f"ゲートウェイ {LEAKED_CREDENTIAL}",
    note=(
        "# ゲートウェイ運用メモ {number}\n\n共有ゲートウェイはテナント {number} の全リクエストを"
        "計測する。ゲートウェイは署名のないリクエストを拒否し、その記録を台帳に残す。\n"
    ),
    note_title="ゲートウェイ運用メモ {number}",
    runbook=(
        "# テナント隔離手順書\n\n共有ゲートウェイの金庫で {tail} 、対応者は影響を受けた"
        "テナントを隔離し、予行演習の結果を台帳に記録する。\n"
    ),
    runbook_secret_tail=f"認証情報 {LEAKED_CREDENTIAL} が漏洩した場合",
    runbook_clean_tail="認証情報が定期的に更新される場合",
    runbook_title="テナント隔離手順書",
    unrelated_title="台帳の保存期間",
    unrelated_body="# 台帳の保存期間\n\n記録は七年間保存する。\n",
    # Measured, not assumed: the word index returns the withheld runbook and
    # nothing else, because the credential is ASCII and the fifty-five Japanese
    # notes are one token each.
    word_index_rows=1,
)

DEPTH_CORPORA = (ENGLISH_DEPTH, JAPANESE_DEPTH)


def _build_depth_project(
    root: Path, corpus: _DepthCorpus, *, secret: bool, holds_the_document: bool
) -> None:
    """One project: a crowd, optionally a withheld document, and a stale index.

    The three projects differ in exactly one thing each, and everything else is
    held equal on purpose — the ids, the bodies of the crowd, and the *freshness*
    of the index. The last one matters more than it looks: `stale` changes the
    `note`, the `note` is priced into the envelope, and the envelope decides how
    many results fit a default budget. A control whose index was merely fresh
    differed from the probe by two results for that reason alone, which is an
    honest difference and a useless comparison. So the project that never held
    the document gets an unrelated item created after its build, and is left
    exactly as far behind as the other two.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _run("init")
    _run("project", "register")
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"

    for number in range(DEPTH_CROWD):
        slug = f"gateway-note-{number:02d}"
        (knowledge / f"{slug}.md").write_text(corpus.body(number))
        (migrations / f"{_depth_ulid('N', number)}-{slug}.yaml").write_text(
            DEPTH_NOTE_MIGRATION.format(
                mid=_depth_ulid("N", number),
                rid=_depth_ulid("R", number),
                slug=slug,
                title=corpus.title(number),
            )
        )
    if holds_the_document:
        (knowledge / "runbook.md").write_text(corpus.runbook_body(secret=secret))
        (migrations / f"{DEPTH_RUNBOOK_ID}-runbook.yaml").write_text(
            RUNBOOK_MIGRATION.format(title=corpus.runbook_title)
        )

    _run("migrate", "apply")
    _run("index", "build")

    if holds_the_document:
        (migrations / f"{DEPTH_RETIRE_ID}-deprecate.yaml").write_text(DEPRECATION_MIGRATION)
    else:
        (knowledge / "ledger-retention.md").write_text(corpus.unrelated_body)
        (migrations / f"{DEPTH_LATE_ID}-ledger.yaml").write_text(
            DEPTH_NOTE_MIGRATION.format(
                mid=DEPTH_LATE_ID,
                rid=_depth_ulid("P", 0),
                slug="ledger-retention",
                title=corpus.unrelated_title,
            )
        )
    _run("migrate", "apply")


@dataclass(frozen=True, slots=True)
class _DepthProjects:
    """Three projects built from one corpus, and the corpus that describes them."""

    registry: ProjectRegistry
    corpus: _DepthCorpus


@pytest.fixture(scope="module", params=DEPTH_CORPORA, ids=[c.id for c in DEPTH_CORPORA])
def three_indexes(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> _DepthProjects:
    """The same crowd, indexed three ways, in each writing system.

    ``depth-probe``      the withheld document holds the credential
    ``depth-same-shape`` the withheld document holds nothing worth stealing
    ``depth-absent``     the document was never written at all

    Each writing system gets its own data directory, so the three project ids
    are the same in both and every test below can name them without knowing
    which corpus it was handed.

    Module-scoped because it builds three real projects through the real CLI and
    every test below asks it the same question. Measured at 2.9 s per corpus.
    """
    corpus: _DepthCorpus = request.param
    base = tmp_path_factory.mktemp(f"depth-{corpus.id}")
    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(base / "datadir"))
    try:
        for name, secret, holds in (
            ("depth-probe", True, True),
            ("depth-same-shape", False, True),
            ("depth-absent", False, False),
        ):
            root = base / name
            root.mkdir()
            monkey.chdir(root)
            _build_depth_project(root, corpus, secret=secret, holds_the_document=holds)
    finally:
        monkey.undo()
    return _DepthProjects(registry=ProjectRegistry.default(base / "datadir"), corpus=corpus)


def test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth(
    three_indexes: _DepthProjects,
) -> None:
    """Guards the guard: proves this fixture can violate the invariant.

    Three preconditions. The withheld document must still be in the index and
    still be matched — otherwise the equality below holds because there is
    nothing to withhold. It must rank inside the depth the pipeline reads, or it
    takes no slot from anyone. And the crowd must be able to fill that depth, or
    the fiftieth visible row does not exist to be displaced.

    **The rows are counted at :data:`DOCUMENTED_DEPTH`, not at
    ``CANDIDATE_DEPTH``.** Asked at the constant's own value, every count here
    moved with it and the assertion held at a depth of 5 and of 200 — a guard
    that resizes itself guards nothing. Counting at fifty makes this a claim
    about the corpus: fifty-five near-identical notes really do offer fifty rows
    to the retriever that carries them.

    **What this corpus reaches, and what it does not.** The last two assertions
    are read off the implementation, and they say opposite things on purpose. The
    crowd must offer more visible rows than :data:`CANDIDATE_DEPTH` publishes, or
    no fiftieth visible row exists to be displaced and every equality below is
    vacuous. It must *not* fill :data:`FIRST_PASS_DEPTH`, and it does not: fifty-six
    chunks against a first pass of a hundred, so both retrievers are exhausted on
    that pass and ``cleared[:CANDIDATE_DEPTH]`` trims the two corpora identically.
    This fixture therefore cannot fail when that cut is deleted — measured,
    ``return cleared`` passed every one of the 1,407 tests that existed before
    ``tests/unit/test_candidate_cut.py`` was written — and the corpus that can
    fail is there, where the matching rows outnumber a first pass. An earlier
    wording of this paragraph called the asserted constant "the depth the pipeline
    actually reads", which is :data:`FIRST_PASS_DEPTH` and was not the constant
    asserted; that mismatch is where the gap lived.

    It also records *which* retriever is doing the work, because that is the
    whole difference between the two corpora and it is not visible from the
    response. English fills both retrievers. Japanese fills only the trigram
    one: `unicode61` treats an unbroken run of CJK as a single token, so the
    fifty-five notes are one token each and the word index cannot match
    `ゲートウェイ` inside any of them — the single row it does return is the
    withheld document, reached through the ASCII credential. Fifty trigram slots
    are therefore the entire candidate list, which is why the attack needed no
    filler trick there.

    Ranked through the index directly, one layer below the canonical store that
    does the withholding — which is what the retrievers saw before this
    milestone.
    """
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    corpus = three_indexes.corpus
    root = Path(three_indexes.registry.load()["depth-probe"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    index = SqliteIndexStore(built)

    words = index.search_lexical(
        corpus.query, project_id="depth-probe", limit=DOCUMENTED_DEPTH
    ).rows
    trigrams = index.search_substring(
        corpus.query, project_id="depth-probe", limit=DOCUMENTED_DEPTH
    ).rows

    assert (len(words), len(trigrams)) == (corpus.word_index_rows, DOCUMENTED_DEPTH), (
        "the crowd must fill the depth it is there to fill, on the retriever that carries it"
    )
    assert "architecture.runbook" in [row.item_id for row in trigrams], (
        "the withheld document must take one of the slots, or nothing is displaced"
    )
    assert "architecture.runbook" in [row.item_id for row in words], (
        "and it must be reachable through the word index too, or `dense` and "
        "`lexical` below prove nothing about it"
    )
    crowd = index.search_substring(
        corpus.query, project_id="depth-probe", limit=FIRST_PASS_DEPTH
    ).rows

    assert len(crowd) > CANDIDATE_DEPTH, (
        "the pipeline publishes every row this crowd can offer, so no visible row "
        "is displaced and every equality asserted below holds vacuously"
    )
    assert len(crowd) < FIRST_PASS_DEPTH, (
        "this corpus now outlasts a first pass, so it no longer describes what the "
        "comment above says it describes: re-read `tests/unit/test_candidate_cut.py`, "
        "which exists because this one is exhausted before the candidate cut bites"
    )


@pytest.mark.asyncio
async def test_the_three_projects_are_equally_behind_their_own_knowledge(
    three_indexes: _DepthProjects,
) -> None:
    """Guards the guard: the confound that makes an honest difference look like a leak.

    `stale` decides the `note`, the `note` is priced into the envelope, and the
    envelope decides how many results fit a default budget. A control whose index
    was merely *fresh* differed from the probe by two results for that reason
    alone — a real difference, nothing to do with the withheld document, and
    indistinguishable in the comparison below from the leak it is looking for.

    So `depth-absent` is given an unrelated item after its build, purely to leave
    it as far behind as the two that retired the runbook. That is a fixture
    detail nothing else would notice breaking: remove it and every equality
    assertion below starts failing for a reason that is not a security defect,
    and the obvious response is to relax the assertion. This says out loud which
    of the two it is.
    """
    projects = ("depth-probe", "depth-same-shape", "depth-absent")

    answers = [
        await _call(
            three_indexes.registry,
            "knowledge.search",
            projectId=project,
            query=three_indexes.corpus.query,
        )
        for project in projects
    ]

    assert [answer["retrieval"]["stale"] for answer in answers] == [True, True, True]


@pytest.mark.asyncio
async def test_a_caller_who_asks_for_the_published_maximum_receives_it(
    three_indexes: _DepthProjects,
) -> None:
    """FR-R4. What `CANDIDATE_DEPTH` owes the published contract.

    `MAX_RESULTS` is a number this product states: a caller may ask for fifty
    results and the tool clamps anything larger to it. A candidate depth below
    that quietly makes the published maximum unreachable — the retrievers are cut
    to their depth *before* fusion, so the answer runs out of candidates before
    it runs out of limit, and a caller asking for fifty over a corpus of
    fifty-six documents gets however many the depth happened to allow.

    Nothing said so. `CANDIDATE_DEPTH` was set to 5 and every test in this module
    passed, including the equality comparisons above — they compare two projects
    against each other, so a depth that truncates both truncates them equally.

    Asked against `depth-absent`, which withholds nothing: this is a claim about
    reach, and mixing it with a corpus that has something to hide would put two
    effects on one measurement.
    """
    answer = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-absent",
        query=three_indexes.corpus.query,
        limit=MAX_RESULTS,
        maxTokens=32_000,
    )

    assert answer["retrieval"]["indexed"] is True, "this must be the ranked path"
    assert answer["count"] == MAX_RESULTS, (
        "the corpus holds more matching documents than the published cap, so a "
        "caller who asks for the cap must receive it"
    )


@pytest.mark.asyncio
async def test_the_default_budget_does_not_hand_back_the_whole_ranking(
    three_indexes: _DepthProjects,
) -> None:
    """FR-R4. The half of `DEFAULT_BUDGET_TOKENS` that bounds it from above.

    "Small enough that a caller who forgot the parameter is not handed their
    whole window back" is the constant's own promise, and the caller it is
    written for is exactly this one: they set `limit` to the published maximum
    and left `maxTokens` alone. Fifty results is several thousand tokens of
    payload; a default that let all of them through would spend a context window
    the caller never agreed to spend.

    Paired with `test_the_default_budget_answers_an_ordinary_query_in_full`,
    which bounds it from below. Neither alone is a bound: the default was set to
    200 and the whole suite passed, because every budget assertion in this module
    states its own `maxTokens` and the one that does not was too small to notice.

    **A band, not a value.** The pair holds the default between roughly 800 and
    8,000 tokens — measured by mutation: 700 fails below, 12,000 fails above on
    the English corpus and 16,000 on both. 2,000 sits inside it, and nothing in
    the requirements fixes it more tightly than that, so nothing here pretends to.
    """
    answer = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-absent",
        query=three_indexes.corpus.query,
        limit=MAX_RESULTS,
    )

    assert answer["retrieval"]["droppedForBudget"] >= 1, (
        "fifty results must not fit a budget the caller never chose"
    )
    assert answer["count"] < MAX_RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["depth-same-shape", "depth-absent"])
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"limit": MAX_RESULTS},
        {"limit": MAX_RESULTS - 1, "maxTokens": 32_000},
        {"limit": MAX_RESULTS, "maxTokens": 32_000},
        {"limit": MAX_RESULTS, "maxTokens": 32_000, "useDense": True},
    ],
    ids=["defaults", "at-the-depth", "one-below", "generous", "dense"],
)
async def test_a_withheld_document_changes_nothing_a_caller_can_see(
    three_indexes: _DepthProjects, control: str, arguments: dict[str, Any]
) -> None:
    """SEC-13, T-15. The property, by construction rather than by comparison.

    One query, two corpora: one whose index holds a document the caller may not
    read, one whose does not. Every published value must be equal — `count`,
    `usedTokens`, `droppedForBudget`, every hit's `fusedScore`, `foundBy`,
    `excerpt` and position, and the whole `retrieval` block bar the two build
    identities named in `BUILD_IDENTITY`.

    Three earlier rounds compared a probe query against a control query and
    passed while a sibling channel stayed open, because a comparison of two
    queries is only as wide as the fields those two queries happen to move. This
    one has no such gap: the query is *identical*, so anything that differs
    differs because of the withheld document and nothing else.

    It is also the only shape in which the dense retriever can be asserted at
    all. Two different query strings legitimately produce different dense ranks
    for documents the caller *can* see, so a two-query comparison under
    `useDense=True` fails for an honest reason; one query against two corpora
    does not.

    **Both writing systems, and the Japanese one is not a formality.** Measured
    again, per parameter set, against a mutation that takes the depth loop off
    the trigram retriever alone:

    - English notices only at `maxTokens=32_000` — `generous` and `dense` — where
      the whole ranking is published and `usedTokens` moves.
    - Japanese notices at `at-the-depth`, which is `limit=50` at the **default**
      token budget: `droppedForBudget` reads 43 against the control's 44. That is
      the field and the budget the shipped extraction attack used, and the word
      index cannot cover for it — `unicode61` cannot segment CJK, so the trigram
      retriever's fifty slots are the entire candidate list.
    - Japanese also notices at `generous` and `dense`, through `count` and
      `usedTokens`.

    **`defaults` and `one-below` stay green under that mutation, in both writing
    systems, and that is not a gap in them.** With no parameters at all `limit`
    is 10 and the budget admits 6, so a row displaced at candidate position 50 is
    nowhere near the answer; `one-below` asks for 49 of a ranking that has 50, so
    the fiftieth is the only one that moves and it was not published either way.
    They are in the list for the *other* leaks — `defaults` is there because the
    version this test closes leaked with no parameters set at all, and a
    parameter set that no current mutation moves is still the one a future
    regression may.

    An earlier wording of this paragraph said "Japanese notices at the default
    budget" without naming the parameter set, which reads as `defaults` — the one
    case that does not. Stated by id here for that reason.
    """
    query = three_indexes.corpus.query
    probe = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-probe",
        query=query,
        **arguments,
    )
    other = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId=control,
        query=query,
        **arguments,
    )

    assert probe["count"] > 0, "a comparison of two empty answers proves nothing"
    assert probe["results"] == other["results"], (
        "every field of every hit, including which chunk of a document was excerpted"
    )
    assert probe["count"] == other["count"]
    assert {k: v for k, v in probe["retrieval"].items() if k not in BUILD_IDENTITY} == {
        k: v for k, v in other["retrieval"].items() if k not in BUILD_IDENTITY
    }


@pytest.mark.asyncio
async def test_the_build_identity_a_search_reports_does_not_vary_with_the_query(
    three_indexes: _DepthProjects,
) -> None:
    """What justifies excluding `indexBuildId` and `snapshotId` above.

    A field left out of a comparison is a field nothing checks, so the two that
    are left out are checked here instead, and by the only test that means
    anything for them: they must be the same for *every* query against one
    project. A value that cannot vary with the query cannot answer a question
    about what the query matched.
    """
    matching = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-probe",
        query=three_indexes.corpus.query,
    )
    unrelated = await _call(
        three_indexes.registry, "knowledge.search", projectId="depth-probe", query=NO_SUCH_TERM
    )

    assert {k: matching["retrieval"][k] for k in BUILD_IDENTITY} == {
        k: unrelated["retrieval"][k] for k in BUILD_IDENTITY
    }
    assert all(matching["retrieval"][key] for key in BUILD_IDENTITY), "and neither is empty"


@pytest.mark.asyncio
async def test_a_stale_index_still_names_the_canonical_state_that_answered(
    three_indexes: _DepthProjects,
) -> None:
    """FR-R5. `snapshotId` is the canonical state's hash, never the index's.

    The two are equal on a fresh index, so the test that pairs `snapshotId` with
    `knowledge.status` proved nothing about which of them is being reported —
    replacing the canonical hash with the index's build-time hash passed the
    whole suite. They differ exactly when the index is behind, which is the state
    this fixture is in and the state a caller most needs the answer for: an index
    build id is not a canonical state, and a caller comparing the two strings to
    decide whether their `knowledge.status` snapshot is still current would be
    told "yes" by an index that had not been rebuilt since.
    """
    status = await _call(three_indexes.registry, "knowledge.status", projectId="depth-probe")
    search = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-probe",
        query=three_indexes.corpus.query,
    )

    assert search["retrieval"]["stale"] is True, "an index that is not behind cannot show this"
    assert search["retrieval"]["snapshotId"] == status["stateHash"]


@pytest.mark.asyncio
async def test_a_gc_unlink_between_a_requests_reads_does_not_tear_it(
    indexed: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#103 item 4, pinned through `hybrid_answer` rather than through the store.

    A search makes several index reads -- `embedding_model`, the retrieval, then
    `chunk_texts`. `hybrid_answer` opens one read connection for the request
    (`SqliteIndexStore.session`), so a `theurian index gc` that unlinks the build
    between two of those reads does not tear the request: on POSIX the held
    descriptor keeps the inode readable after the name is gone.

    **This is the last load-bearing line of the PR that nothing pinned.** Making
    `session()` a no-op -- each read opening its own connection again -- left the
    whole suite green, because every other test constructs a store and reads it
    once. Here the second read, after the unlink, opens a path that is gone and
    the whole request degrades to the substring-scan fallback.

    The unlink is deterministic rather than timed: `metadata()` is the first
    index read inside the session, reached through `embedding_model` -- which is
    why `useDense=True`, the parameter that makes `embedding_model` consult the
    stored model. Wrapping it to unlink after it returns puts the unlink between
    the first read and every one that follows.
    """
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(indexed.load()["demo"]["rootPath"])
    build = next((root / ".theurian/state").glob("theurian-index-*.sqlite"))

    real_metadata = SqliteIndexStore.metadata
    fired = 0

    def unlink_after_the_first_read(self: SqliteIndexStore) -> dict[str, object]:
        nonlocal fired
        result = real_metadata(self)
        if fired == 0:
            build.unlink()
        fired += 1
        return result

    monkeypatch.setattr(SqliteIndexStore, "metadata", unlink_after_the_first_read)

    result = await _call(
        indexed, "knowledge.search", projectId="demo", query="token", useDense=True
    )

    assert fired >= 1, (
        "the hook never fired, so the unlink did not land mid-request and this test proved "
        "nothing -- `embedding_model` no longer reads `metadata()`, or `useDense` stopped "
        "reaching it"
    )
    assert result["retrieval"]["indexed"] is True, (
        "the request tore on a `gc` unlink between two of its reads. Without the held session "
        "connection the second read opened a path that was gone, and the whole request fell "
        "back to the substring scan -- `retrieval.fallbackReason` is "
        f"{result['retrieval'].get('fallbackReason')!r}"
    )
    assert result["count"] >= 1, "the index answered, so it must have returned the matching hit"
    assert not build.exists(), (
        "a read recreated the reaped build: `mode=ro` is what stops `sqlite3.connect` from "
        "conjuring an empty database at the deleted path"
    )
