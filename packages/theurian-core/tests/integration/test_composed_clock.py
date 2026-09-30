"""The clock seams a composition root fills: build stamps and search freshness (ADR-0003).

``knowledge.search`` computes ``freshness`` against ``build_server``'s
``search_clock`` when its caller sends no ``asOf``, and ``migrate apply`` stamps
``validFrom`` from the clock ``composed_clock`` hands ``resolve_context``. The
eval harness fills both with one fixed clock so its byte-pinned baseline
(ADR-0036) does not move with the calendar.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes.clock import FrozenClock
from git_harness import commit_migrations
from mcp.types import CallToolResult
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, ProjectRegistry, read_active_state
from theurian.cli.context import composed_clock, resolve_context
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.ports.determinism import Clock
from theurian.infrastructure.determinism import SystemClock
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

pytestmark = pytest.mark.integration

runner = CliRunner()

ITEM_ID = "architecture.auth-policy"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
CREATED_AT = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
#: Not today, so a seam that fell back to the wall clock reports a different
#: ``ageDays`` and ``validFrom``.
BUILT_AT = datetime(2031, 1, 10, 12, 0, tzinfo=UTC)
AFTER_THE_BUILD = datetime(2032, 3, 4, 5, 6, tzinfo=UTC)
BEFORE_THE_BUILD = datetime(2031, 1, 1, tzinfo=UTC)

MIGRATION = f"""apiVersion: theurian.dev/v1
id: 01K1AAAAAA01234567890ABCDE
createdAt: {CREATED_AT.isoformat()}
author: engineer@example.com
operations:
  - op: createItem
    itemId: {ITEM_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {ITEM_ID}
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
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


def _cli(root: Path, *args: str) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as monkey:
        monkey.chdir(root)
        if args[:2] == ("migrate", "apply"):
            commit_migrations()
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One approved item, applied under a composed clock pinned at ``BUILT_AT``."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    with composed_clock(FrozenClock(BUILT_AT)):
        _cli(root, "init")
        (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
        (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
        (root / ".theurian/migrations/01K1AAAAAA01234567890ABCDE-auth.yaml").write_text(MIGRATION)
        _cli(root, "project", "register")
        _cli(root, "migrate", "apply")
    return root


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry.default(tmp_path / "datadir")


async def _search(registry: ProjectRegistry, clock: Clock | None) -> dict[str, Any]:
    result = await build_server(registry, search_clock=clock).call_tool(
        "knowledge.search", {"projectId": "demo", "query": "token"}
    )
    assert isinstance(result, CallToolResult), result
    structured: dict[str, Any] | None = result.structured_content
    assert structured is not None, result
    return structured


def test_a_built_revision_is_stamped_by_the_composed_clock(project: Path) -> None:
    paths = ProjectPaths.of(project)
    active = read_active_state(paths)
    assert active is not None
    context = RequestContext(project_id=ProjectId("demo"))
    with SqliteCanonicalStore(paths.database_for(active.state_hash)) as store:
        item = store.get_item(context, ItemId(ITEM_ID))
        assert item is not None
        revision = store.current_revision(context, item)

    assert item.validity.valid_from == BUILT_AT
    assert revision is not None
    assert revision.validity.valid_from == BUILT_AT


@pytest.fixture(params=[True, False], ids=["ranked", "substring-fallback"])
def indexed(request: pytest.FixtureRequest, project: Path) -> bool:
    """Built here rather than in the test: ``index build`` calls ``asyncio.run``,
    which raises inside the running loop of an async test."""
    built: bool = request.param
    if built:
        _cli(project, "index", "build")
    return built


@pytest.mark.parametrize(
    ("now", "within_validity"),
    [(AFTER_THE_BUILD, True), (BEFORE_THE_BUILD, False)],
    ids=["after-the-build", "before-the-build"],
)
@pytest.mark.asyncio
async def test_search_freshness_reads_the_composed_search_clock(
    registry: ProjectRegistry,
    indexed: bool,
    now: datetime,
    within_validity: bool,
) -> None:
    """Both answer paths. ``isWithinValidity`` compares the search clock with the
    build clock's ``validFrom``; ``ageDays`` reads the search clock alone."""
    response = await _search(registry, FrozenClock(now))

    assert response["retrieval"]["indexed"] is indexed
    (hit,) = response["results"]
    assert hit["freshness"]["ageDays"] == (now - CREATED_AT).days
    assert hit["freshness"]["isWithinValidity"] is within_validity


@pytest.mark.asyncio
async def test_no_search_clock_means_the_wall_clock(
    project: Path, registry: ProjectRegistry
) -> None:
    before = datetime.now(UTC)
    response = await _search(registry, None)
    after = datetime.now(UTC)

    (hit,) = response["results"]
    assert hit["freshness"]["ageDays"] in {(before - CREATED_AT).days, (after - CREATED_AT).days}


def test_the_composed_clock_holds_only_inside_its_block(project: Path) -> None:
    pinned = FrozenClock(BUILT_AT)

    with composed_clock(pinned):
        assert resolve_context(project).clock is pinned
    assert isinstance(resolve_context(project).clock, SystemClock)
