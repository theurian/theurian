"""TEMPORARY security-review reproduction. Delete after review."""
from __future__ import annotations
import json, subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
import pytest
from typer.testing import CliRunner
from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration
runner = CliRunner()

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
DEPRECATE_ID = "01K1CCCCCC01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

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

DEPRECATE = f"""apiVersion: theurian.dev/v1
id: {DEPRECATE_ID}
createdAt: 2026-08-02T12:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.auth-policy
    reason: Withdrawn after review
"""

def _run(*args: str) -> None:
    r = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert r.exit_code == 0, r.stdout + (r.stderr or "")

async def _call(registry, tool, **arguments):
    result = await build_server(registry).call_tool(tool, arguments)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    return json.loads(result.content[0].text)

@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ProjectRegistry, Path]]:
    root = tmp_path / "demo"; root.mkdir()
    for args in (["git","init","-q","-b","main"],["git","config","user.email","t@e.com"],["git","config","user.name","T"]):
        subprocess.run(args, cwd=root, check=True, capture_output=True)
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project","register"); _run("migrate","apply"); _run("index","build")
    yield ProjectRegistry.default(data_dir), root


@pytest.mark.asyncio
async def test_REPRO_deprecated_item_still_returned_from_stale_index(project) -> None:
    registry, root = project
    before = await _call(registry, "knowledge.search", projectId="demo", query="signed token")
    assert before["retrieval"]["indexed"] is True
    assert before["count"] == 1

    # The team withdraws the decision. Migrations applied; index not rebuilt.
    (root / f".theurian/migrations/{DEPRECATE_ID}-deprecate.yaml").write_text(DEPRECATE)
    _run("migrate", "apply")

    after = await _call(registry, "knowledge.search", projectId="demo", query="signed token")
    print("\nRETRIEVAL:", after["retrieval"])
    print("STATUSES :", [h["status"] for h in after["results"]])
    assert after["count"] == 0, (
        f"BYPASS: includeUnapproved=False still returned "
        f"{[h['status'] for h in after['results']]}"
    )
