"""TEMPORARY security-review reproductions. Delete after review."""
from __future__ import annotations
import json, subprocess, time
from collections.abc import Iterator
from pathlib import Path
import pytest
from typer.testing import CliRunner
from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration
runner = CliRunner()
MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
BODY = ("# Authentication policy\n\nEvery call carries a signed token. " * 40)

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

def _run(*a): 
    r = runner.invoke(app, [*a, "--json"], catch_exceptions=False)
    assert r.exit_code == 0, r.stdout + (r.stderr or "")

async def _raw(registry, tool, **arguments):
    return await build_server(registry).call_tool(tool, arguments)

async def _call(registry, tool, **arguments):
    result = await _raw(registry, tool, **arguments)
    s = getattr(result, "structuredContent", None)
    return s if s is not None else json.loads(result.content[0].text)

@pytest.fixture
def project(tmp_path, monkeypatch) -> Iterator[tuple[ProjectRegistry, Path]]:
    root = tmp_path / "demo"; root.mkdir()
    for a in (["git","init","-q","-b","main"],["git","config","user.email","t@e.com"],["git","config","user.name","T"]):
        subprocess.run(a, cwd=root, check=True, capture_output=True)
    dd = tmp_path/"datadir"; monkeypatch.setenv("THEURIAN_DATA_DIR", str(dd)); monkeypatch.chdir(root)
    _run("init")
    (root/".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root/f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project","register"); _run("migrate","apply"); _run("index","build")
    yield ProjectRegistry.default(dd), root


@pytest.mark.asyncio
async def test_REPRO_unbounded_query_cost(project):
    registry, _ = project
    for n in (1, 50, 150, 300):
        q = " ".join(["token"]*n)
        t0 = time.time()
        await _call(registry, "knowledge.search", projectId="demo", query=q)
        print(f"\n  repeated-term query n={n:>4} bytes={len(q):>6} elapsed={time.time()-t0:.2f}s")

@pytest.mark.asyncio
async def test_REPRO_maxTokens_zero(project):
    registry, _ = project
    r = await _raw(registry, "knowledge.search", projectId="demo", query="token", maxTokens=0)
    print("\n  maxTokens=0 ->", r.content[0].text[:300])

@pytest.mark.asyncio
async def test_REPRO_pointer_traversal(project):
    registry, root = project
    pointer = root/".theurian/state/active-index.json"
    data = json.loads(pointer.read_text())
    outside = root.parent/"outside"; outside.mkdir(exist_ok=True)
    victim = outside/"theurian-index-planted.sqlite"
    victim.write_bytes(b"not a database")
    rel = "../../../outside/theurian-index-planted"
    pointer.write_text(json.dumps({**data, "indexBuildId": rel}))
    from theurian.application.project_service import ProjectPaths, read_active_index
    p = ProjectPaths.of(root)
    resolved = p.index_for(rel)
    print("\n  constructed path:", resolved)
    print("  resolves to     :", resolved.resolve())
    print("  escapes root    :", not str(resolved.resolve()).startswith(str(root.resolve())))
    print("  is_file()       :", resolved.is_file())
    r = await _raw(registry, "knowledge.search", projectId="demo", query="token")
    print("  tool response   :", r.content[0].text[:400])
