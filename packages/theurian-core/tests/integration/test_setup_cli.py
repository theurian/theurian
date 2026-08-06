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
import sys
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
from theurian.security.env_file import TOKEN_KEY

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


#: A directory name that names a client. Never appears in any layout's paths
#: except `data-dir-under-home`, so asserting its absence is safe everywhere and
#: meaningful in the one place it is built.
CLIENT_NAME = "northwind-acquisition"


def _repository_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    relative_to_home: str | None,
    data_dir: str = "default",
    symlinked_home: bool = False,
) -> tuple[Path, Path, Path]:
    """A sandbox whose working directory *is* a repository.

    `sandbox` chdirs somewhere with no `.git` above it, so `_repository_root`
    returns None and §6.2 rows 11-13 report NOT_APPLICABLE -- meaning every
    assertion in this module was once made against a payload those three steps
    were absent from, redaction included.

    Three knobs, each reaching an anchor nothing else reaches. None is a detail:
    `_redacted` substitutes plain substrings, so the arrangement *is* the code
    path, and every one of these was added after a mutation survived for want of
    the layout that shows it.

    ``relative_to_home`` puts the checkout inside the home directory or beside
    it. Beside, the repository anchor matches; inside, the home anchor reaches
    the string first and would eat the prefix if the anchors were not ordered
    longest-first. A fixture that only built the beside case reported the
    repository substitution as working while it was a no-op on every ordinary
    machine.

    ``data_dir`` selects which `THEURIAN_DATA_DIR` argument is being made.
    ``"outside-home"`` is a mount or a shared path, which `~` does not reach.
    ``"under-home"`` is the one the in-HOME exemption used to cover on the
    strength of an argument about `~/.theurian`: `~` is anonymous and a path
    under it is not. ``"in-repository"`` is what makes the repository's
    *unresolved* spelling reach the payload at all.

    ``symlinked_home`` is macOS `/var` and several Linux `/home` layouts. With
    it, `$HOME` and `Path.cwd().resolve()` disagree about how to spell the same
    directory, and an anchor that knows only one of them matches inside the
    other.

    A bare `.git` directory rather than `git init`: `_repository_root` tests for
    its existence and nothing else reads it, so initialising a repository here
    would be testing Git.
    """
    if symlinked_home:
        real = tmp_path / "real"
        real.mkdir()
        home = tmp_path / "home"
        home.symlink_to(real)
    else:
        home = tmp_path / "home"
        home.mkdir()

    repository = home / relative_to_home if relative_to_home else tmp_path / "api"
    (repository / ".git").mkdir(parents=True)

    directories = {
        "default": home / ".theurian",
        "outside-home": tmp_path / "elsewhere" / "store",
        "under-home": home / "clients" / CLIENT_NAME / "store",
        "in-repository": repository / ".theurian-data",
    }
    chosen = directories[data_dir]

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(chosen))
    monkeypatch.chdir(repository)
    return home, repository, chosen


@pytest.mark.parametrize(
    ("relative_to_home", "data_dir", "symlinked_home"),
    [
        ("work/api", "default", False),
        (None, "default", False),
        ("work/api", "outside-home", False),
        ("work/api", "under-home", False),
        ("work/api", "in-repository", True),
    ],
    ids=[
        "repository-under-home",
        "repository-beside-home",
        "data-dir-outside-home",
        "data-dir-under-home",
        "symlinked-home-with-data-dir-in-the-repository",
    ],
)
def test_the_report_mode_redacts_the_locations_the_project_steps_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_to_home: str | None,
    data_dir: str,
    symlinked_home: bool,
) -> None:
    """O-3, for the steps that only exist inside a repository.

    Rows 11-13 have no action and therefore no `paths`, so their summaries are
    the one place the registry file, the checked `.gitignore` and the repository
    itself are named -- and `doctor --report` output is what people paste into
    public issues.

    Five arrangements, because `_redacted` substitutes plain substrings and each
    one reaches an anchor the others do not. Each was added after something got
    through:

    - **repository under HOME**, which is where checkouts live. Deleting the
      `<repository>` substitution goes RED only with the repository *beside*
      HOME; under it, the home anchor had already consumed the prefix and the
      substitution had never done anything on an ordinary machine.
    - **repository beside HOME**, the arrangement that did work, kept so
      misordering the anchors is caught from both directions.
    - **`THEURIAN_DATA_DIR` outside HOME**, a mount or a shared path, which `~`
      does not reach.
    - **`THEURIAN_DATA_DIR` under HOME but not the default**, which the in-HOME
      exemption used to cover on the strength of an argument about
      `~/.theurian`. `~` is anonymous; `~/clients/<name>/store` is not.
    - **a symlinked `$HOME` with the data directory inside the repository**,
      which is what makes the repository's *unresolved* spelling reach the
      payload: `_repository_root` resolves, so `(p, p.resolve())` gave the same
      string twice and the operator's own spelling went out as
      `~/work/api/.theurian-data`.
    """
    home, repository, chosen_data_dir = _repository_sandbox(
        tmp_path,
        monkeypatch,
        relative_to_home=relative_to_home,
        data_dir=data_dir,
        symlinked_home=symlinked_home,
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
    assert str(chosen_data_dir) not in blob
    assert str(chosen_data_dir.resolve()) not in blob
    assert "<repository>" in blob, "the substitution has to have fired, not merely not leaked"
    assert repository.name not in blob, (
        "a bare directory name is not a path and no anchor catches it; the summaries "
        "name the repository by its whole path so that this one can"
    )
    # Vacuous in four of the five layouts and the point of the fifth. A path
    # under `~` is not anonymised by `~`, and the name of the directory it sits
    # in is the part that identifies someone.
    assert CLIENT_NAME not in blob, "a data directory under HOME still names where it is"
    # `_executable()` resolves to this virtualenv's `bin`, which on a real
    # machine is routinely inside a project directory.
    assert str(Path(sys.executable).parent) not in blob, "the install location names a directory"


def _context_for_anchors(
    home: Path, data_dir: Path, project_root: Path | None, executable: str = ""
) -> SetupContext:
    """The paths `_redaction_anchors` reads, and fakes for the rest."""
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
        executable=executable,
    )


def test_every_anchored_path_is_anchored_under_both_of_its_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim `_redaction_anchors`' docstring makes, over all of them.

    The previous version looked only at `home`, so an anchor that lost a
    spelling anywhere else was invisible -- the same shape as every other
    finding on this branch, where the fixture was the only place the subject
    existed. Asserted over every candidate rather than the one that broke,
    because the next candidate to arrive gets no test of its own by default.

    `project_root` satisfies this trivially and deliberately so: it comes from
    `_repository_root`, and `os.getcwd()` is fully resolved on POSIX, so it has
    one spelling. Were it ever to arrive unresolved, this loop demands the
    resolved form too and the existing `(p, p.resolve())` pass supplies it.
    """
    real = tmp_path / "real"
    (real / "work" / "api").mkdir(parents=True)
    (real / "bin").mkdir()
    linked_home = tmp_path / "home"
    linked_home.symlink_to(real)
    repository = linked_home / "work" / "api"
    monkeypatch.chdir(repository)
    context = _context_for_anchors(
        linked_home,
        repository / ".theurian-data",
        repository.resolve(),
        executable=str(linked_home / "bin" / "theurian"),
    )

    anchored = dict(_redaction_anchors(context))

    assert context.project_root is not None
    for name, path in {
        "home": linked_home,
        "data directory": context.data_dir,
        "token file": context.auth_dir / TOKEN_KEY,
        "executable": Path(context.executable),
        "repository": context.project_root,
    }.items():
        assert str(path) in anchored, f"{name} is not anchored as the operator spells it"
        assert str(path.resolve()) in anchored, f"{name} is not anchored as Python resolves it"


def test_only_the_default_data_directory_is_left_to_the_home_anchor(tmp_path: Path) -> None:
    """`~/.theurian` reads better than a placeholder and discloses nothing.

    That argument is about *one* path, and the guard used to be about every path
    under HOME -- so `THEURIAN_DATA_DIR=$HOME/clients/<name>/store` was published
    in full. `~` is anonymous; the directory it sits in is what identifies
    someone. The exemption now covers exactly the path the argument covers.
    """
    home = tmp_path / "home"
    home.mkdir()

    def anchors(data_dir: Path) -> dict[str, str]:
        return dict(_redaction_anchors(_context_for_anchors(home, data_dir, None)))

    default = home / ".theurian"
    under_home = home / "clients" / CLIENT_NAME / "store"
    outside = tmp_path / "elsewhere"

    assert "<data directory>" not in anchors(default).values(), "the one legible location"
    assert anchors(under_home)[str(under_home)] == "<data directory>"
    assert anchors(outside)[str(outside)] == "<data directory>"


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
