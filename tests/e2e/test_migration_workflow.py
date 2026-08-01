"""The Milestone 1 workflow, driven through the installed CLI.

These run the real binary against a real Git repository and a real SQLite file.
Everything below was found or confirmed by running it -- the packaging defect
that left the JSON Schemas out of the wheel, and the checksum check that
ADR-0016 had silently disabled, both surfaced here and nowhere else.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]

#: Exit code Core uses for a knowledge-state problem the user must resolve.
EXIT_STATE_ERROR = 4

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"

BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
description: Record the authentication policy.
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
def project(tmp_path: Path) -> Iterator[Path]:
    """A real Git repository with an isolated Theurian data directory."""
    root = tmp_path / "demo"
    root.mkdir()

    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    previous = os.environ.get("THEURIAN_DATA_DIR")
    os.environ["THEURIAN_DATA_DIR"] = str(tmp_path / "datadir")
    try:
        yield root
    finally:
        if previous is None:
            os.environ.pop("THEURIAN_DATA_DIR", None)
        else:
            os.environ["THEURIAN_DATA_DIR"] = previous


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert THEURIAN is not None
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [THEURIAN, *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def _write_migration(root: Path, body: str = BODY, migration: str = MIGRATION) -> None:
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(migration)


# -- The happy path --------------------------------------------------------


def test_init_creates_the_layout_and_is_idempotent(project: Path) -> None:
    first = _json(_run(project, "init", "--json"))
    assert first["changed"]
    assert first["createdPaths"]
    assert first["gitignoreUpdated"]
    assert (project / ".theurian/migrations").is_dir()

    second = _json(_run(project, "init", "--json"))
    assert not second["changed"], "a second init must change nothing"
    assert second["createdPaths"] == []


def test_gitignore_covers_every_derived_path(project: Path) -> None:
    """ADR-0004: derived artifacts must never be committed."""
    _run(project, "init", "--json")
    ignored = (project / ".gitignore").read_text()

    for entry in (".theurian/state/", ".theurian/cache/", ".theurian/runtime/", "*.sqlite"):
        assert entry in ignored


def test_full_workflow_from_init_to_applied_state(project: Path) -> None:
    _run(project, "init", "--json")
    _write_migration(project)

    validated = _json(_run(project, "migrate", "validate", "--json"))
    assert validated["valid"]
    assert validated["migrationCount"] == 1
    assert validated["contentFileCount"] == 1

    applied = _json(_run(project, "migrate", "apply", "--json"))
    assert applied["databaseCreated"]
    assert applied["applied"] == [MIGRATION_ID]
    assert applied["operationsApplied"] == 2
    assert applied["changed"]

    status = _json(_run(project, "migrate", "status", "--json"))
    assert status["applied"] == 1
    assert status["pending"] == 0

    databases = list((project / ".theurian/state").glob("*.sqlite"))
    assert len(databases) == 1
    assert databases[0].name == f"theurian-state-{applied['stateHash'][:12]}.sqlite"


def test_applying_twice_changes_nothing(project: Path) -> None:
    """FR-K8."""
    _run(project, "init", "--json")
    _write_migration(project)
    _run(project, "migrate", "apply", "--json")

    second = _json(_run(project, "migrate", "apply", "--json"))
    assert second["applied"] == []
    assert second["skipped"] == [MIGRATION_ID]
    assert not second["changed"]
    assert not second["databaseCreated"]


def test_register_and_list_a_project(project: Path) -> None:
    _run(project, "init", "--json")

    registered = _json(_run(project, "project", "register", "--json"))
    assert registered["projectId"] == "demo"
    assert registered["changed"]

    again = _json(_run(project, "project", "register", "--json"))
    assert not again["changed"], "re-registering an identical project must be a no-op"

    listed = _json(_run(project, "project", "list", "--json"))
    assert listed["count"] == 1
    assert listed["projects"][0]["projectId"] == "demo"


def test_unregister_states_that_knowledge_is_preserved(project: Path) -> None:
    _run(project, "init", "--json")
    _write_migration(project)
    _run(project, "project", "register", "--json")
    _run(project, "migrate", "apply", "--json")

    removed = _json(_run(project, "project", "unregister", "demo", "--json"))
    assert removed["removed"]
    assert removed["knowledgePreserved"]

    # The Git-tracked inputs are what matter, and they are untouched.
    assert (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").exists()
    assert (project / ".theurian/knowledge/architecture/auth-policy.md").exists()


# -- ADR-0016: what the state hash covers ----------------------------------


def test_editing_an_applied_migration_is_fatal(project: Path) -> None:
    """The regression that motivated the ADR-0016 amendment.

    Editing a migration changes the state hash, routing the next command to a
    fresh empty database where nothing looks wrong. Without checking the
    previously active state, this exits 0 and reports success.
    """
    _run(project, "init", "--json")
    _write_migration(project)
    _run(project, "migrate", "apply", "--json")

    path = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    original = path.read_text()
    path.write_text(original + "  # edited after apply\n")

    result = _run(project, "migrate", "status", "--json")
    assert result.returncode == EXIT_STATE_ERROR
    error = json.loads(result.stderr)
    assert "never be edited" in error["error"]
    assert "Restore the original" in error["remedy"]

    # Apply must refuse too, not just status.
    assert _run(project, "migrate", "apply", "--json").returncode == EXIT_STATE_ERROR

    # Restoring the file restores normal operation.
    path.write_text(original)
    assert _json(_run(project, "migrate", "status", "--json"))["pending"] == 0


def test_editing_a_content_file_forks_a_new_state(project: Path) -> None:
    """Not an error: a changed body should produce a new state (ADR-0016)."""
    _run(project, "init", "--json")
    _write_migration(project)
    _run(project, "migrate", "apply", "--json")

    before = _json(_run(project, "project", "status", "--json"))["stateHash"]

    body = project / ".theurian/knowledge/architecture/auth-policy.md"
    body.write_text(BODY + "\nTokens expire after 15 minutes.\n")

    after = _json(_run(project, "project", "status", "--json"))
    assert after["stateHash"] != before
    assert after["indexStale"], "a forked state is not yet built"


def test_the_state_hash_does_not_depend_on_the_project_path(tmp_path: Path) -> None:
    """No absolute path may enter the hash, or no two machines would agree."""
    hashes = []
    for name in ("first-location", "second-location"):
        root = tmp_path / name
        root.mkdir()
        for args in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

        env_backup = os.environ.get("THEURIAN_DATA_DIR")
        os.environ["THEURIAN_DATA_DIR"] = str(tmp_path / f"data-{name}")
        try:
            _run(root, "init", "--json")
            _write_migration(root)
            hashes.append(_json(_run(root, "project", "status", "--json"))["stateHash"])
        finally:
            if env_backup is None:
                os.environ.pop("THEURIAN_DATA_DIR", None)
            else:
                os.environ["THEURIAN_DATA_DIR"] = env_backup

    assert hashes[0] == hashes[1], "the state hash leaked an absolute path"


# -- Security --------------------------------------------------------------


def test_a_migration_cannot_read_outside_the_project(project: Path) -> None:
    """SEC-7, T-4, through the real CLI."""
    _run(project, "init", "--json")
    (project / ".theurian/migrations/01K1EVAAAA01234567890ABCDE-escape.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1EVAAAA01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: attacker@example.com
operations:
  - op: upsertRevision
    itemId: evil.leak
    revisionId: 01K1EVAREV01234567890ABCDE
    contentFile: ../../../../../../etc/passwd
    metadata:
      title: Leak
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: attacker
"""
    )

    result = _run(project, "migrate", "validate", "--json")
    assert result.returncode != 0
    assert "escapes the permitted root" in json.loads(result.stderr)["error"]


def test_a_symlink_cannot_escape_the_project(project: Path) -> None:
    """SEC-7, T-5. Lexically inside; only symlink resolution reveals otherwise."""
    _run(project, "init", "--json")
    (project / ".theurian/knowledge/leak.md").symlink_to("/etc/passwd")
    (project / ".theurian/migrations/01K1EVBBBB01234567890ABCDE-symlink.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1EVBBBB01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: attacker@example.com
operations:
  - op: upsertRevision
    itemId: evil.leak
    revisionId: 01K1EVBREV01234567890ABCDE
    contentFile: ../knowledge/leak.md
    metadata:
      title: Leak
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: attacker
"""
    )

    result = _run(project, "migrate", "validate", "--json")
    assert result.returncode != 0
    assert "escapes the permitted root" in json.loads(result.stderr)["error"]


def test_a_dependency_cycle_is_reported_with_the_cycle(project: Path) -> None:
    _run(project, "init", "--json")
    first, second = "01K1CYCAAA01234567890ABCDE", "01K1CYCBBB01234567890ABCDE"

    for this, other in ((first, second), (second, first)):
        (project / f".theurian/migrations/{this}-cycle.yaml").write_text(
            f"""apiVersion: theurian.dev/v1
id: {this}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
dependsOn: [{other}]
operations:
  - op: changeOwner
    itemId: architecture.auth-policy
    owner: someone
"""
        )

    result = _run(project, "migrate", "validate", "--json")
    assert result.returncode == EXIT_STATE_ERROR
    message = json.loads(result.stderr)["error"]
    assert first in message
    assert second in message
    assert "->" in message


# -- Packaging -------------------------------------------------------------


def test_the_installed_build_ships_its_schemas(project: Path) -> None:
    """The migration loader validates against the published schemas at runtime.

    A build without them installs a theurian that cannot read a migration at
    all -- which is exactly what shipped before this test existed.
    """
    _run(project, "init", "--json")
    _write_migration(project)
    assert _json(_run(project, "migrate", "validate", "--json"))["valid"]


def test_json_output_is_the_only_thing_on_stdout(project: Path) -> None:
    """A caller must be able to pipe stdout straight into a parser."""
    result = _run(project, "init", "--json")
    json.loads(result.stdout)


def test_errors_go_to_stderr_leaving_stdout_clean(project: Path) -> None:
    _run(project, "init", "--json")
    _write_migration(project)
    _run(project, "migrate", "apply", "--json")

    path = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    path.write_text(path.read_text() + "  # edited\n")

    result = _run(project, "migrate", "status", "--json")
    assert result.stdout == ""
    json.loads(result.stderr)
