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
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import state_hash_from_the_loader, unchecked_migrations
from typer.testing import CliRunner

from theurian.application.authorization import DEFAULT_CEILING, SERVING_PROFILE_FILENAME
from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.cli import setup_commands
from theurian.cli.main import app
from theurian.cli.setup_commands import _redaction_anchors
from theurian.domain.setup import SetupState
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.security.env_file import TOKEN_KEY

pytestmark = pytest.mark.integration

runner = CliRunner()

#: Another repository on this machine, as the registry would name it.
FOREIGN_PROJECT_ID = "acme-unreleased-merger-tooling"

#: A line below the block that assigns the same variable again. The value is
#: distinctive so that a payload carrying it cannot do so by coincidence.
SHADOWING_LINE = "export THEURIAN_MCP_TOKEN=SentinelShadowedValueZZZZ\n"


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


# -- A finding with no work attached (#128) ----------------------------------
#
# `env-reference` can be satisfied -- the block is current, so applying the step
# would write the same bytes -- and still have something to say, because a line
# below the block appears to assign the same variable and a shell keeps the last
# assignment it reads. `SetupService` turns that into a warning.
#
# Both commands here return *before* the apply: `doctor` is a dry run and so is
# `setup --dry-run`. The warning was built in the verification pass alone, so on
# the very machine `theurian setup` ended DEGRADED over, `theurian doctor --json`
# published `"warnings": []` and exited 0 -- the caveat sitting in the payload
# the whole time as the `detail` of a step whose status reads `satisfied`, which
# is where a reader stops.


def _converged_machine(tmp_path: Path) -> SetupContext:
    """A machine `theurian setup` has already brought to CONVERGED.

    The data directory, the token and the env file are real files under
    ``tmp_path``. The service manager and Claude Code's configuration are fakes
    for the reason `fakes/setup.py` gives: a real LaunchAgent registers itself in
    the developer's own login session, which no ``HOME`` redirection prevents.

    Built here rather than by exporting one of the setup modules' fixtures,
    because what these tests need is a context `build_context` can be pointed at
    -- and `build_context` is the one function in the composition root that
    *cannot* be pointed at a temporary machine by environment alone.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_dir = home / ".theurian"
    service = FakeService()
    # 0755 and a real script: `probe_core` needs an absolute path that resolves
    # *and* can be started, or `core-present` conflicts and the run aborts (#49).
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    context = SetupContext(
        home=home,
        data_dir=data_dir,
        port=7419,
        project_root=None,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: {"dataDir": str(data_dir)} if service.started else None,
        service=service,
        executable=str(executable),
        check_migrations=unchecked_migrations,
        current_state_hash=state_hash_from_the_loader,
    )
    report = SetupService(context).run(SetupRequest())
    assert report.state is SetupState.CONVERGED, report.warnings
    return context


@pytest.fixture
def converged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupContext:
    """A converged machine, with both commands' composition root pointed at it.

    ``HOME`` is redirected as well as the context replaced, which is belt and
    braces on purpose: the substitution is what makes the machine converged,
    and the redirection is what keeps this module's opening claim true for the
    tests below -- nothing here may read the developer's own home directory even
    if a future edit reaches `build_context` by another route.
    """
    context = _converged_machine(tmp_path)
    monkeypatch.setenv("HOME", str(context.home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(context.data_dir))
    monkeypatch.setattr(setup_commands, "build_context", lambda **_: context)
    return context


@pytest.fixture
def shadowed(converged: SetupContext) -> SetupContext:
    """The same machine, with one line added below the block by its owner."""
    with converged.env_file.open("a", encoding="utf-8") as handle:
        handle.write(SHADOWING_LINE)
    return converged


def test_doctor_calls_a_line_it_will_not_touch_a_warning_and_not_a_problem(
    shadowed: SetupContext,
) -> None:
    """A reservation is a finding with no work attached, so it is not a problem.

    `doctor` counts what setup would change and what it would ask consent for,
    and exits 1 on either. A line below the block is neither: it belongs to
    whoever wrote it, setup will not edit it (SEC-18), and there is nothing to
    schedule. Counting it would exit 1 on a machine where no command Theurian
    ships can do anything about it -- and a non-zero exit that no action clears
    is how a health check stops being read.

    So it goes to ``warnings``, and this pins both halves of that split at once:
    the sentence is published, and the count and the exit code do not move.

    The value on the offending line is asserted absent because this is the same
    payload `--report` publishes, and whatever is to the right of an ``=`` is a
    credential often enough to matter.
    """
    code, payload = _invoke("doctor")

    warnings = [w for w in payload["warnings"] if "env-reference" in w]
    assert len(warnings) == 1, payload["warnings"]
    assert str(shadowed.env_file) in warnings[0]
    assert "SentinelShadowedValue" not in warnings[0], "not the line it matched"
    assert payload["healthy"] is True, "there is nothing here for setup to do"
    assert payload["problemCount"] == 0
    assert code == 0, "and an exit code no command can clear is one nobody reads"


def test_doctor_says_nothing_about_a_machine_with_nothing_below_the_block(
    converged: SetupContext,
) -> None:
    """The control the test above is worth nothing without.

    A `doctor` that warned unconditionally, or that turned every explained
    NOT_APPLICABLE step into a line, would satisfy every assertion up there. This
    is the same converged machine with the shadowing line left out, and the
    answer has to be silence.
    """
    code, payload = _invoke("doctor")

    assert payload["warnings"] == [], payload["warnings"]
    assert payload["healthy"] is True
    assert code == 0
    assert converged.env_file.is_file(), "the fixture converged; there is a block to shadow"


def test_the_plan_setup_prints_carries_the_same_reservation_doctor_does(
    shadowed: SetupContext,
) -> None:
    """One machine, two commands whose job is to say what is wrong, one answer.

    `/theurian:setup` renders `setup --dry-run` and a person runs `doctor`, and
    they reach the same `PLAN_BUILT` report by different routes. Divergence here
    is not a cosmetic difference: it is one command telling somebody their
    machine is fine while the other names the line that makes it not.

    Asserted as equality *and* on the content, because two empty lists are also
    equal -- which is exactly the state this pins the way out of.
    """
    _, plan = _invoke("setup", "--dry-run")
    _, diagnosis = _invoke("doctor")

    assert any("env-reference" in warning for warning in plan["warnings"]), plan["warnings"]
    assert plan["warnings"] == diagnosis["warnings"]
    assert plan["state"] == "plan-built" and plan["dryRun"] is True


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
        check_migrations=unchecked_migrations,
        current_state_hash=state_hash_from_the_loader,
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


# -- The deployment serving profile (#119, ADR-0025) --------------------------
#
# `doctor` had no step that read the ceiling at all, so the one security setting
# that decides what every `knowledge.search` may return was the only one absent
# from the health check. Three states, because they call for three different
# things from the reader: nothing, nothing, and a `chmod` or an edit.


def _declare_a_ceiling(data_dir: Path, contents: str, *, mode: int = 0o600) -> Path:
    """The profile file as an operator writes it, in a 0700 directory."""
    auth = data_dir / "auth"
    auth.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth.chmod(0o700)
    profile = auth / SERVING_PROFILE_FILENAME
    profile.write_text(contents, encoding="utf-8")
    profile.chmod(mode)
    return profile


def _step(payload: dict[str, Any], step_id: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [step for step in payload["steps"] if step["id"] == step_id]
    assert len(found) == 1, f"{step_id} is not in the report: {[s['id'] for s in payload['steps']]}"
    return found[0]


def test_doctor_names_the_ceiling_a_deployment_that_declared_none_serves(
    converged: SetupContext,
) -> None:
    """Undeclared is the ordinary state, and it is still worth being told.

    ``NOT_APPLICABLE`` rather than ``MISSING``: ``MISSING`` is ``would_change``,
    so it is counted a problem and `doctor` exits 1 -- forever, on every machine
    that never declares a ceiling, with no command that clears it. Declaring
    nothing is not a thing to fix; the default is the *restrictive* end, and
    ``test_doctor_names_a_remedy_for_every_problem`` would require an ``action``
    naming a command that does not exist.

    The summary still names the level in force, which is the whole reason the
    step exists: an operator who has never opened the file cannot otherwise find
    out what their deployment withholds.
    """
    code, payload = _invoke("doctor")

    step = _step(payload, "serving-profile")
    assert step["status"] == "not-applicable"
    assert DEFAULT_CEILING.value in step["summary"]
    assert code == 0, payload
    assert payload["healthy"] is True
    assert payload["problemCount"] == 0, (
        "an undeclared ceiling must not make a converged machine unhealthy"
    )


def test_doctor_names_a_declared_ceiling(converged: SetupContext) -> None:
    """The counterpart, so ``not-applicable`` above is not the only branch reached.

    The level is named on the operator's own terminal, which is exactly the split
    ``mcp/search.py``'s ``_PROFILE_MISMATCH`` note defers to: a degraded search
    tells an agent to rebuild and never which levels the deployment serves.
    """
    _declare_a_ceiling(converged.data_dir, "confidential\n")

    code, payload = _invoke("doctor")

    step = _step(payload, "serving-profile")
    assert step["status"] == "satisfied"
    assert "confidential" in step["summary"]
    assert code == 0, payload


def test_doctor_calls_a_profile_it_cannot_honour_a_problem(converged: SetupContext) -> None:
    """A ceiling the daemon will refuse to start on is a problem, not a warning.

    ``CONFLICTING`` and never ``MISSING``: the file is the operator's own and
    setup overwrites nothing it did not install (SEC-18), so what this earns is
    consent to proceed past it -- and the detail carries the refusal's own
    remedy, which is the only text that says what would have worked.
    """
    _declare_a_ceiling(converged.data_dir, "secret\n")

    code, payload = _invoke("doctor")

    step = _step(payload, "serving-profile")
    assert step["status"] == "conflicting"
    assert code == 1
    assert payload["healthy"] is False
    assert "public, internal, confidential, restricted" in step["detail"], (
        "a refusal that does not say what would have worked leaves the reader guessing"
    )


#: A word no valid profile can contain, distinctive enough that a payload
#: carrying it cannot do so by coincidence.
TYPO_IN_THE_PROFILE = "SentinelCeilingTypoZZZZ"


def test_report_mode_withholds_a_ceiling_word_theurian_did_not_write(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place a byte of that file enters a message (O-3, SEC-6).

    ``UnknownSensitivityCeilingError`` echoes the word deliberately -- an
    operator cannot fix a typo they cannot see -- and ``doctor --report`` exists
    to be pasted into a public issue. The word is whatever somebody typed into a
    file in their own data directory, so it is a value Theurian did not author
    and there is no anchor in ``_redacted`` that could reach it.
    """
    (tmp_path / ".git").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    _declare_a_ceiling(sandbox / ".theurian", f"{TYPO_IN_THE_PROFILE}\n")

    _, published = _invoke("doctor", "--report")
    _, private = _invoke("doctor")

    assert TYPO_IN_THE_PROFILE not in json.dumps(published)
    assert TYPO_IN_THE_PROFILE in json.dumps(private), (
        "the person who ran it is the reader who has to correct the word"
    )


# -- What a report may say about things Theurian did not write ----------------


def _with_a_foreign_registration(sandbox: Path) -> None:
    """A registry entry naming no root, keyed by another repository's id.

    Chosen for this pair because it needs no external binary, no network and no
    particular platform: the whole state is one JSON file and one `.git`
    directory, so both directions are asserted wherever the suite runs.
    """
    (sandbox / ".theurian").mkdir(parents=True, exist_ok=True)
    (sandbox / ".theurian" / "projects.json").write_text(
        json.dumps({FOREIGN_PROJECT_ID: {"noRootPath": True}}), encoding="utf-8"
    )


def test_report_mode_withholds_what_theurian_did_not_write(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring, asserted on a value. A project id is derived from a
    repository's directory name, so the ids in this file name other people's
    work -- and a bare name is not a path, so nothing in the payload's path
    substitution can reach it (O-3, SEC-6)."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    _with_a_foreign_registration(sandbox)

    _, payload = _invoke("doctor", "--report")

    assert FOREIGN_PROJECT_ID not in json.dumps(payload)


def test_plain_doctor_withholds_nothing_from_the_person_who_ran_it(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--report` is what asks for publication. Without it the output is read by
    the operator, and the id is the argument `project unregister` takes."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    _with_a_foreign_registration(sandbox)

    _, payload = _invoke("doctor")

    assert FOREIGN_PROJECT_ID in json.dumps(payload)


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
