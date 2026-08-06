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
from fakes.setup import FakeMcpConfig
from typer.testing import CliRunner

from theurian.application.setup_context import SetupContext
from theurian.cli.main import app
from theurian.cli.setup_commands import _redaction_anchors
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

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


def _repository_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    relative_to_home: str | None,
    data_dir_outside_home: bool = False,
) -> tuple[Path, Path, Path]:
    """A sandbox whose working directory *is* a repository.

    `sandbox` chdirs somewhere with no `.git` above it, so `_repository_root`
    returns None and §6.2 rows 11-13 report NOT_APPLICABLE -- meaning every
    assertion in this module has been made against a payload those three steps
    were absent from, redaction included.

    ``relative_to_home`` places the checkout inside the home directory or beside
    it, and it is not a detail. `_redacted` substitutes plain substrings, so the
    two arrangements exercise different code: beside HOME, the repository anchor
    matches; inside it, the home anchor reaches the string first and would eat
    the prefix if the anchors were not ordered longest-first. A fixture that
    only tested the beside case reported the repository substitution as working
    while it was a no-op on every ordinary machine.

    ``data_dir_outside_home`` is the other arrangement with no anchor of its
    own: `THEURIAN_DATA_DIR` pointed at a mount or a shared path is not covered
    by the `~` substitution, and the registry file it holds is named in
    `project-registered`'s summary.

    A bare `.git` directory rather than `git init`: `_repository_root` tests for
    its existence and nothing else reads it, so initialising a repository here
    would be testing Git.
    """
    home = tmp_path / "home"
    home.mkdir()
    repository = home / relative_to_home if relative_to_home else tmp_path / "api"
    (repository / ".git").mkdir(parents=True)
    data_dir = tmp_path / "elsewhere" / "theurian" if data_dir_outside_home else home / ".theurian"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(repository)
    return home, repository, data_dir


@pytest.mark.parametrize(
    ("relative_to_home", "data_dir_outside_home"),
    [("work/api", False), (None, False), ("work/api", True)],
    ids=[
        "repository-under-home",
        "repository-beside-home",
        "data-directory-outside-home",
    ],
)
def test_the_report_mode_redacts_the_locations_the_project_steps_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_to_home: str | None,
    data_dir_outside_home: bool,
) -> None:
    """O-3, for the steps that only exist inside a repository.

    Rows 11-13 have no action and therefore no `paths`, so their summaries are
    the one place the registry file, the checked `.gitignore` and the repository
    itself are named -- and `doctor --report` output is what people paste into
    public issues.

    Three arrangements, because `_redacted` substitutes plain substrings and
    each one reaches a different anchor:

    - **under HOME**, which is where checkouts live. This is the case that was
      missing: with the repository *beside* HOME, deleting the `<repository>`
      substitution goes RED, and with it under HOME the same deletion passed,
      because the home anchor had already consumed the prefix and the
      substitution had never been doing anything on an ordinary machine.
    - **beside HOME**, the arrangement that did work, kept so that ordering the
      anchors the other way round is caught from both directions.
    - **`THEURIAN_DATA_DIR` outside HOME**, which the `~` substitution does not
      reach and which holds the registry file named in `project-registered`.
    """
    home, repository, data_dir = _repository_sandbox(
        tmp_path,
        monkeypatch,
        relative_to_home=relative_to_home,
        data_dir_outside_home=data_dir_outside_home,
    )

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
    assert str(home.resolve()) not in blob
    assert str(repository) not in blob
    assert str(repository.resolve()) not in blob
    assert str(data_dir) not in blob
    assert "<repository>" in blob, "the substitution has to have fired, not merely not leaked"
    assert repository.name not in blob, (
        "a bare directory name is not a path and no anchor catches it; the summaries "
        "name the repository by its whole path so that this one can"
    )


def _context_for_anchors(home: Path, data_dir: Path, project_root: Path | None) -> SetupContext:
    """The four paths `_redaction_anchors` reads, and fakes for the rest."""
    return SetupContext(
        home=home,
        data_dir=data_dir,
        port=7419,
        project_root=project_root,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=None,
        executable="",
    )


def test_a_home_that_is_a_symlink_is_anchored_by_both_of_its_spellings(tmp_path: Path) -> None:
    """Asserted on the anchors rather than through `doctor --report`.

    Every string the report currently carries that holds a *resolved* path is
    also under the repository, whose anchor is longer and fires first -- so no
    end-to-end arrangement reaches this today and a mutation deleting the
    resolved spelling survives the suite. It is kept because the mismatch is
    real and measured: `context.home` is whatever `$HOME` says while
    `project_root` is `Path.cwd().resolve()`, and before the anchors were
    ordered the unresolved one matched *inside* the resolved path and published
    `/private~/work/api/…`. Testing the helper directly is what holds a guard
    whose subject the shipped report does not yet produce.
    """
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    linked_home = tmp_path / "home"
    linked_home.symlink_to(real_home)
    context = _context_for_anchors(linked_home, linked_home / ".theurian", None)

    anchored = dict(_redaction_anchors(context))

    assert anchored[str(linked_home)] == "~"
    assert anchored[str(real_home)] == "~", "the resolved spelling reaches strings the other misses"


def test_a_data_directory_inside_home_is_left_to_the_home_anchor(tmp_path: Path) -> None:
    """`~/.theurian` discloses nothing and reads better than a placeholder.

    The pair to the `data-directory-outside-home` case above: the anchor is
    added for the arrangement that needs it and withheld from the one that does
    not, so this pins the withholding.
    """
    home = tmp_path / "home"
    home.mkdir()

    inside = dict(_redaction_anchors(_context_for_anchors(home, home / ".theurian", None)))
    outside = dict(_redaction_anchors(_context_for_anchors(home, tmp_path / "elsewhere", None)))

    assert "<data directory>" not in inside.values()
    assert outside[str(tmp_path / "elsewhere")] == "<data directory>"


def test_the_anchors_are_ordered_longest_first(tmp_path: Path) -> None:
    """The whole correctness argument, stated where it can fail.

    Substring replacement is order-dependent, so a nested path has to be
    replaced before the directory containing it. Both real orderings -- token
    file inside the data directory, repository inside home -- are consequences
    of this one property rather than separate rules.
    """
    home = tmp_path / "home"
    home.mkdir()
    context = _context_for_anchors(home, home / ".theurian", home / "work" / "api")

    lengths = [len(needle) for needle, _ in _redaction_anchors(context)]

    assert lengths == sorted(lengths, reverse=True)


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
