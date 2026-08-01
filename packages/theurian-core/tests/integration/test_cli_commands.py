"""CLI commands, invoked in-process.

The e2e suite runs the installed binary and proves packaging works. These run
the same commands through Typer's runner: faster, measurable by coverage, and
able to assert on the exact JSON a caller receives.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_STATE_ERROR = 4

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
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


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
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
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command and parse its JSON.

    ``mix_stderr=False`` matters: the CLI keeps stdout a clean machine channel
    and puts errors on stderr, and a test that merged them could not tell.
    """
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _write_migration(root: Path, migration: str = MIGRATION, body: str = BODY) -> None:
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(migration)


# -- init ------------------------------------------------------------------


def test_init_creates_the_layout(project: Path) -> None:
    code, payload = _invoke("init")
    assert code == 0
    assert payload["changed"]
    assert (project / ".theurian/migrations").is_dir()
    assert (project / ".theurian/knowledge/architecture").is_dir()


def test_init_is_idempotent(project: Path) -> None:
    _invoke("init")
    code, payload = _invoke("init")
    assert code == 0
    assert not payload["changed"]
    assert payload["createdPaths"] == []


def test_init_appends_the_gitignore_block_once(project: Path) -> None:
    """SEC-18: re-running rewrites only Theurian's own marked block."""
    (project / ".gitignore").write_text("# a rule the user wrote\n*.log\n")

    _invoke("init")
    _invoke("init")

    content = (project / ".gitignore").read_text()
    assert content.count("# >>> theurian >>>") == 1
    assert "*.log" in content, "the user's own rules must survive"


def test_init_outside_a_git_repository_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, payload = _invoke("init")
    assert code == 1
    assert "not inside a Git repository" in payload["error"]


# -- project ---------------------------------------------------------------


def test_register_then_list(project: Path) -> None:
    _invoke("init")
    code, registered = _invoke("project", "register")
    assert code == 0
    assert registered["projectId"] == "demo"

    _, listed = _invoke("project", "list")
    assert listed["count"] == 1


def test_register_is_idempotent(project: Path) -> None:
    """FR-L2. The first registration time is preserved, not refreshed."""
    _invoke("init")
    _invoke("project", "register")
    _, again = _invoke("project", "register")
    assert not again["changed"]


def test_unregister_reports_that_knowledge_survives(project: Path) -> None:
    _invoke("init")
    _invoke("project", "register")

    _, removed = _invoke("project", "unregister", "demo")
    assert removed["removed"]
    assert removed["knowledgePreserved"]

    _, again = _invoke("project", "unregister", "demo")
    assert not again["removed"], "removing a missing project is not an error"


def test_status_reports_an_unbuilt_state(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, status = _invoke("project", "status")
    assert status["migrationCount"] == 1
    assert not status["stateBuilt"]
    assert status["indexStale"]


def test_status_outside_a_repository_reports_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, status = _invoke("project", "status")
    assert code == 0, "status must report, not fail, outside a project"
    assert not status["registered"]


# -- migrate ---------------------------------------------------------------


def test_validate_reports_the_application_order(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, validated = _invoke("migrate", "validate")
    assert validated["valid"]
    assert validated["applicationOrder"] == [MIGRATION_ID]


def test_apply_then_status(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    code, applied = _invoke("migrate", "apply")
    assert code == 0
    assert applied["applied"] == [MIGRATION_ID]
    assert applied["operationsApplied"] == 2

    _, status = _invoke("migrate", "status")
    assert status["applied"] == 1
    assert status["pending"] == 0


def test_apply_is_idempotent(project: Path) -> None:
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    _, second = _invoke("migrate", "apply")
    assert second["applied"] == []
    assert not second["changed"]


def test_status_on_an_unbuilt_state_lists_everything_as_pending(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, status = _invoke("migrate", "status")
    assert not status["stateBuilt"]
    assert status["pending"] == 1
    assert status["pendingIds"] == [MIGRATION_ID]


def test_editing_an_applied_migration_is_fatal(project: Path) -> None:
    """ADR-0016: checked against the previously active state."""
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    path = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    path.write_text(path.read_text() + "  # edited after apply\n")

    code, error = _invoke("migrate", "status")
    assert code == EXIT_STATE_ERROR
    assert "never be edited" in error["error"]


def test_a_revision_conflict_is_reported_not_merged(project: Path) -> None:
    """ADR-0006. The remedy must say a human decides, not the tool."""
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    # No I, L, O, or U: those are excluded from Crockford base32.
    stale = "01K1STAAAA01234567890ABCDE"
    second = "01K1BBBBBB01234567890ABCDE"
    (project / f".theurian/migrations/{second}-conflict.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {second}
createdAt: 2026-08-02T11:00:00+09:00
author: other@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1BBBREV01234567890ABCDE
    expectedRevision: {stale}
    contentFile: ../knowledge/architecture/auth-policy.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""
    )

    code, error = _invoke("migrate", "apply")
    assert code == EXIT_STATE_ERROR
    assert "Revision conflict" in error["error"]
    assert "does not merge knowledge automatically" in error["remedy"]


def test_a_malformed_migration_names_the_offending_field(project: Path) -> None:
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-bad.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "is invalid at" in error["error"]


def test_a_naive_timestamp_is_rejected(project: Path) -> None:
    """A naive timestamp compares wrong across a DST boundary, and validity
    windows depend on those comparisons."""
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-naive.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: "2026-08-02T10:00:00"
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "offset" in error["error"] or "invalid at" in error["error"]


def test_an_unknown_api_version_is_rejected(project: Path) -> None:
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-future.yaml").write_text(
        f"""apiVersion: theurian.dev/v2
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "invalid at" in error["error"] or "apiVersion" in error["error"]


def test_an_empty_project_validates(project: Path) -> None:
    """A project with no migrations is valid, not broken."""
    _invoke("init")
    code, validated = _invoke("migrate", "validate")
    assert code == 0
    assert validated["migrationCount"] == 0


# ==========================================================================
# ingest
# ==========================================================================


MARKDOWN_DOC = """---
status: approved
reviewers: [alice]
---

# Authentication policy

Every call carries a signed token.
"""

OPENAPI_DOC = """openapi: 3.1.0
info:
  title: Orders API
  version: "1.0"
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        "200": {description: OK}
"""


def _write_sources(root: Path) -> None:
    (root / ".theurian/knowledge/architecture/auth.md").write_text(MARKDOWN_DOC)
    (root / ".theurian/specifications/api.yaml").write_text(OPENAPI_DOC)


def test_ingest_normalizes_every_format(project: Path) -> None:
    _invoke("init")
    _write_sources(project)

    code, report = _invoke("ingest")

    assert code == 0
    assert report["ingested"] == 2
    assert report["succeeded"]
    assert {d["parser"] for d in report["documents"]} == {"markdown", "openapi"}


def test_ingest_is_incremental(project: Path) -> None:
    """Touching a file without changing it costs one hash, not a reparse."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    code, second = _invoke("ingest")

    assert code == 0
    assert second["ingested"] == 0
    assert second["unchanged"] == 2


def test_ingest_reports_a_governed_front_matter_key(project: Path) -> None:
    """ADR-0019: a silently ignored `status: approved` is the case where an
    author believes something is approved and it is not."""
    _invoke("init")
    _write_sources(project)

    _, report = _invoke("ingest")

    codes = {w["code"] for w in report["warnings"]}
    assert codes == {"front-matter-governed-field"}


def test_a_parse_failure_is_reported_without_losing_the_rest(project: Path) -> None:
    _invoke("init")
    _write_sources(project)
    (project / ".theurian/specifications/broken.yaml").write_text("key: [unclosed\n")

    code, report = _invoke("ingest")

    assert code == EXIT_STATE_ERROR, "a partial run is not a clean run"
    assert report["ingested"] == 2, "the good documents still got in"
    assert report["failed"] == 1


def test_a_corrupt_manifest_costs_a_reparse_not_a_failure(project: Path) -> None:
    """The manifest is a derived cache. Refusing to run would let a disposable
    file block the command."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    (project / ".theurian/cache/ingestion.json").write_text("{ not json")

    code, report = _invoke("ingest")

    assert code == 0
    assert report["ingested"] == 2


def test_ingest_writes_a_manifest_under_the_derived_cache(project: Path) -> None:
    """ADR-0004: the manifest is derived, so it belongs somewhere git-ignored."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    manifest = project / ".theurian/cache/ingestion.json"
    assert manifest.is_file()
    assert ".theurian/cache/" in (project / ".gitignore").read_text()
