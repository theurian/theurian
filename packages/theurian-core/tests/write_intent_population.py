"""The key for "a registered write-intent tool", shared by every test that needs the set.

A tool is write-intent when its body closes over ``_draft_only_proposals``, the
per-call factory that hands it a ``DraftOnlyProposals`` facade (ADR-0032 decision
8). Measured at 6c3c2827: 3 tools, the 3 call sites of
``_draft_only_proposals(projectId)`` in ``mcp/tools.py`` (key:
``git grep -n -F '_draft_only_proposals(projectId)' 6c3c2827 --
packages/theurian-core/src/theurian/mcp/tools.py``).

Cannot see a tool that drafts without the facade.

Also the ``demo`` fixture the write-intent pins share: a registered, built project
with one approved item, made by the real CLI.
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Final

import pytest
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app

FACADE_FREEVAR = "_draft_only_proposals"


def closes_over_the_facade(fn: Callable[..., Any]) -> bool:
    body = getattr(fn, "__wrapped__", fn)
    return FACADE_FREEVAR in body.__code__.co_freevars


def write_intent_names(tools: Iterable[Any]) -> frozenset[str]:
    """Names of the tools in *tools* (FastMCP ``Tool`` objects) keyed as write-intent."""
    return frozenset(tool.name for tool in tools if closes_over_the_facade(tool.fn))


def registered_write_intent_names(server: Any) -> frozenset[str]:
    return write_intent_names(server._tool_manager.list_tools())


MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
MIGRATION: Final = f"""apiVersion: theurian.dev/v1
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


def _cli(*args: str) -> None:
    result = CliRunner().invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@pytest.fixture(name="demo")
def demo_project(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ProjectRegistry]:
    """A registered, built ``demo`` project with one approved item, via the real CLI."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)

    _cli("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _cli("project", "register")
    commit_migrations()
    _cli("migrate", "apply")

    yield ProjectRegistry.default(tmp_path / "datadir")
