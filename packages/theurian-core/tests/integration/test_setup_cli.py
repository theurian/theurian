"""`theurian setup`, `doctor`, and `uninstall` at the CLI boundary (FR-L1, O-3).

The plugin commands read these exact keys and exit codes, so the surface is a
contract with a Markdown file and a shell script rather than an implementation
detail.

Every test redirects HOME. Without it, `setup` would register a LaunchAgent in
the developer's own login session and add an entry to their real Claude Code
configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(home / ".theurian"))
    monkeypatch.chdir(tmp_path)
    return home


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout or result.stderr or ""
    return result.exit_code, json.loads(stream) if stream.strip() else {}


# -- setup ------------------------------------------------------------------


def test_a_dry_run_reports_a_plan_and_creates_nothing(sandbox: Path) -> None:
    """What `/theurian:setup` runs first, before asking for anything."""
    code, payload = _invoke("setup", "--dry-run")

    assert code == 0
    assert payload["dryRun"] is True
    assert payload["state"] == "plan-built"
    assert not (sandbox / ".theurian").exists()


def test_the_plan_carries_the_keys_the_plugin_command_reads(sandbox: Path) -> None:
    """`commands/setup.md` renders `steps` and branches on `serenaDetected`."""
    _, payload = _invoke("setup", "--dry-run")

    assert isinstance(payload["steps"], list)
    assert isinstance(payload["serenaDetected"], bool)
    assert {"id", "status", "summary", "action"} <= set(payload["steps"][0])


# -- doctor -----------------------------------------------------------------


def test_doctor_reports_problems_without_fixing_them(sandbox: Path) -> None:
    """A diagnostic that repairs things is one whose output you cannot trust."""
    code, payload = _invoke("doctor")

    assert code == 1
    assert payload["healthy"] is False
    assert payload["problemCount"] > 0
    assert not (sandbox / ".theurian").exists(), "doctor must change nothing"


def test_doctor_names_a_remedy_for_every_problem(sandbox: Path) -> None:
    """A problem with no stated remedy is a support request."""
    _, payload = _invoke("doctor")

    unresolved = [s for s in payload["steps"] if s["status"] == "missing"]
    assert unresolved
    assert all(step["action"] for step in unresolved)


def test_the_report_mode_redacts_the_home_directory(sandbox: Path) -> None:
    """O-3. This output is what people paste into public issues, so it is
    redacted by default rather than on request."""
    _, payload = _invoke("doctor", "--report")

    assert payload["redacted"] is True
    assert str(sandbox) not in json.dumps(payload)
    assert payload["platform"]
    assert payload["version"]


def test_the_report_mode_still_says_what_is_wrong(sandbox: Path) -> None:
    """Redaction that removed the diagnosis would defeat the purpose."""
    _, payload = _invoke("doctor", "--report")

    assert payload["problemCount"] > 0
    assert any(step["status"] == "missing" for step in payload["steps"])


@pytest.fixture
def repository_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A sandbox whose working directory *is* a repository.

    `sandbox` chdirs somewhere with no `.git` above it, so `_repository_root`
    returns None and §6.2 rows 11-13 report NOT_APPLICABLE -- meaning every
    assertion in this module has been made against a payload those three steps
    were absent from, redaction included.

    A bare `.git` directory rather than `git init`: `_repository_root` tests for
    its existence and nothing else reads it, so initialising a repository here
    would be testing Git.
    """
    home = tmp_path / "home"
    home.mkdir()
    repository = tmp_path / "api"
    (repository / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(home / ".theurian"))
    monkeypatch.chdir(repository)
    return home, repository


def test_the_report_mode_redacts_the_locations_the_project_steps_name(
    repository_sandbox: tuple[Path, Path],
) -> None:
    """O-3, for the steps that only exist inside a repository.

    Rows 11-13 have no action and therefore no `paths`, so their summaries are
    the one place the registry file and the checked `.gitignore` are named --
    and `doctor --report` output is what people paste into public issues.
    """
    home, repository = repository_sandbox

    _, payload = _invoke("doctor", "--report")

    project_steps = [
        step
        for step in payload["steps"]
        if step["id"] in {"project-registered", "project-layout", "gitignore"}
    ]
    assert len(project_steps) == 3
    assert all(step["status"] == "missing" for step in project_steps), (
        "the fixture has to reach the branch, or this redacts nothing"
    )
    assert all(step["summary"] for step in project_steps)

    blob = json.dumps(payload)
    assert str(home) not in blob
    assert str(repository) not in blob
    assert str(repository.resolve()) not in blob


# -- uninstall ---------------------------------------------------------------


def test_uninstall_dry_run_removes_nothing(sandbox: Path) -> None:
    code, payload = _invoke("uninstall", "--dry-run")

    assert code == 0
    assert payload["dryRun"] is True


def test_uninstall_never_offers_to_delete_knowledge(sandbox: Path) -> None:
    """FR-L5. Approved knowledge lives in Git inside the user's repository. It
    is not Theurian's to delete, and no flag here reaches it."""
    _, payload = _invoke("uninstall", "--dry-run")

    kept = " ".join(payload["kept"])
    assert "never deletes" in kept
    assert not any("knowledge" in entry.lower() for entry in payload["removed"])
