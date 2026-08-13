"""What `theurian setup` does to an env file that is not only its own (#128).

The file's own header says "Sourced by your shell profile", which is an
invitation to add lines to it. Until this milestone every apply opened it
``O_TRUNC`` and rewrote the whole thing, and the probe reported ``Missing``
on *any* difference -- so a line somebody had added was destroyed with no diff,
no backup and no mention in ``changedPaths``, on every run of a command whose
own contract is that running it twice changes nothing (FR-L2). §6.2 row 7 had
required "rewrite the Theurian-owned block only" throughout.

`tests/unit/test_env_file_merge.py` pins the merge itself. This module drives
the **real** `SetupService` over real files, because the defect lived in the
seam rather than in the decision: the probe answering one question and the
apply performing a different write is exactly what shipped, and a merge with
perfect semantics behind a probe that never calls it is the same file destroyed.
So every case here goes through `plan()` and `run()` and then reads the bytes
back off the disk.

Fakes only where a real collaborator would touch this machine: a real
LaunchAgent registers in the developer's own login session, which no ``HOME``
redirection prevents.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.domain.setup import SetupState, SetupStep, StepId, StepOutcome, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.security.env_file import (
    ENV_BLOCK_END,
    ENV_BLOCK_START,
    env_block,
    legacy_env_file_contents,
)

pytestmark = pytest.mark.integration

PORT = 7419

#: Lines a person put in this file, on both sides of the block. Distinctive
#: enough that a substring assertion cannot pass by coincidence, and the trailing
#: whitespace is deliberate: "byte for byte" is the claim, not "line for line".
BEFORE = "export MY_OTHER_VAR=1\n# a comment of mine\n\n"
AFTER = "export AFTER_THE_BLOCK=2\n  trailing whitespace kept  \n"

#: Shaped like the reason this file is 0600. It stands for every byte of the
#: file that Theurian did not author, and `doctor --report` publishes step
#: details verbatim.
USER_SECRET = "export AWS_SECRET_ACCESS_KEY=SentinelEnvFileSecretZZZZ\n"  # noqa: S105 - a sentinel


def _context(tmp_path: Path, **overrides: Any) -> SetupContext:
    """A machine where nothing is set up yet, and no daemon is running."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_dir = home / ".theurian"
    service = FakeService()
    # 0755 and a real script: `probe_core` requires an absolute path that
    # resolves *and* can be started, and a 0644 file conflicts `core-present`,
    # which aborts the run before any apply (#49).
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    defaults: dict[str, Any] = {
        "home": home,
        "data_dir": data_dir,
        "port": PORT,
        "project_root": None,
        "connection": ConnectionSpec(port=PORT),
        "mcp_config": FakeMcpConfig(),
        "secrets": FileSecretStore(data_dir),
        "health": lambda: {"dataDir": str(data_dir)} if service.started else None,
        "service": service,
        "executable": str(executable),
    }
    return SetupContext(**{**defaults, **overrides})


def _seed(context: SetupContext, content: str) -> bytes:
    """Put a file at the env path and return its bytes, for comparing against."""
    context.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    context.data_dir.chmod(0o700)
    context.env_file.write_text(content, encoding="utf-8")
    return context.env_file.read_bytes()


def _env_step(context: SetupContext) -> SetupStep:
    """Re-probe: what setup says about this file *now*."""
    step = SetupService(context).plan().step(StepId.ENV_REFERENCE)
    assert step is not None, "the plan accounts for every step"
    return step


def _stale_block(context: SetupContext) -> str:
    """This machine's block as an older install left it: a different data dir."""
    return env_block(context.data_dir.parent / "moved-since" / ".theurian")


# -- A machine with nothing there yet ----------------------------------------


def test_a_fresh_install_writes_the_block_and_nothing_else(tmp_path: Path) -> None:
    """SEC-5, §6.2 row 7. The file a first run leaves behind, exactly.

    Asserted as the whole file rather than as a substring, because everything
    else in this module is a claim about what a *rewrite* preserves, and that
    claim is only readable against a known starting state. The mode is part of
    the subject: the file names the token's location, and its purpose is to be
    sourced by its owner alone.
    """
    context = _context(tmp_path)

    report = SetupService(context).run(SetupRequest())

    assert report.succeeded, report.warnings
    assert context.env_file.read_text(encoding="utf-8") == env_block(context.data_dir) + "\n"
    assert context.env_file.stat().st_mode & 0o777 == 0o600
    assert str(context.env_file) in report.changed_paths, "a file this run wrote is disclosed"
    assert _env_step(context).status is StepStatus.SATISFIED, "and the machine is now converged"


# -- A file that is not only setup's ----------------------------------------


def test_lines_around_a_stale_block_survive_it_being_rewritten(tmp_path: Path) -> None:
    """#128, the defect itself. A rewrite is of the block, not of the file.

    The seeded block names a data directory this machine no longer uses, which
    is the ordinary way to arrive here -- a moved ``THEURIAN_DATA_DIR``, a home
    directory restored under a different name. So the block genuinely has to be
    rewritten, and the question is what happens to the lines on either side of
    it.

    Asserted as the exact whole file. "The user's lines are still in there"
    passes on a file that reordered them, and order is meaning in a shell
    snippet: whichever assignment comes last is the one the shell keeps.
    """
    context = _context(tmp_path)
    _seed(context, BEFORE + _stale_block(context) + "\n" + AFTER)

    report = SetupService(context).run(SetupRequest())

    assert report.succeeded, report.warnings
    assert context.env_file.read_text(encoding="utf-8") == (
        BEFORE + env_block(context.data_dir) + "\n" + AFTER
    )
    assert _env_step(context).status is StepStatus.SATISFIED


def test_a_converged_block_with_user_lines_around_it_is_not_rewritten(tmp_path: Path) -> None:
    """#128's other half: the probe used to answer on the whole file.

    Comparing the file against a rendering reports ``Missing`` for every person
    who has ever appended a line to it, on a machine that is already converged
    -- and the apply that follows is what destroyed the line. So this is the
    arm where the correct behaviour is to do *nothing*, and it is asserted as
    nothing: identical bytes, no path claimed as changed, no step reporting a
    change (FR-L2).
    """
    context = _context(tmp_path)
    before = _seed(context, BEFORE + env_block(context.data_dir) + "\n" + AFTER)

    assert _env_step(context).status is StepStatus.SATISFIED, "this machine is already converged"

    report = SetupService(context).run(SetupRequest())

    env = report.step(StepId.ENV_REFERENCE)
    assert context.env_file.read_bytes() == before
    assert str(context.env_file) not in report.changed_paths
    assert env is not None and env.outcome is not StepOutcome.CHANGED


def test_a_second_run_does_not_reopen_the_env_file(tmp_path: Path) -> None:
    """FR-L2, §6.3. Convergence is measured on the file, not on the report.

    A run that rewrote identical bytes would satisfy every content assertion in
    this module while still being a rewrite -- and a rewrite is where the lines
    around the block are at risk, so "it came out the same this time" is not
    the property. The witness is the mtime, stamped to a fixed instant in the
    past first: a truncate-and-write moves it to now, and a comparison against
    ``now`` would depend on the filesystem's timestamp resolution rather than on
    the behaviour.
    """
    context = _context(tmp_path)
    first = SetupService(context).run(SetupRequest())
    assert first.succeeded, first.warnings
    stamped = 1_000_000_000
    os.utime(context.env_file, ns=(stamped, stamped))

    second = SetupService(context).run(SetupRequest())

    assert second.succeeded, second.warnings
    assert context.env_file.stat().st_mtime_ns == stamped, "the file was never opened for writing"
    assert second.changed_paths == (), "and a converged machine reports no changes"


# -- Machines set up by 0.1.0.dev0 through dev2 ------------------------------


def test_a_file_from_before_the_markers_is_upgraded_without_duplicating_its_exports(
    tmp_path: Path,
) -> None:
    """Every machine a released version set up carries this file, byte for byte.

    Appending the block beside the old rendering would leave two assignments of
    ``THEURIAN_MCP_TOKEN``, and after a data directory moves they name different
    paths -- with the shell keeping whichever comes last, while setup reports
    the machine converged. So the old rendering is *replaced*, and the count of
    export lines is what says so: a block assertion cannot see a duplicate.
    """
    context = _context(tmp_path)
    _seed(context, legacy_env_file_contents(context.data_dir))

    report = SetupService(context).run(SetupRequest())

    written = context.env_file.read_text(encoding="utf-8")
    assert report.succeeded, report.warnings
    assert written == env_block(context.data_dir) + "\n"
    assert written.count("export THEURIAN_MCP_TOKEN\n") == 1
    assert _env_step(context).status is StepStatus.SATISFIED


def test_upgrading_a_pre_marker_file_keeps_the_lines_added_to_it(tmp_path: Path) -> None:
    """The machine that has both: a dev2 file, and lines added to it since.

    This is the upgrade path #128 is really about -- nobody's env file is
    interesting until they have put something of their own in it. The old
    rendering is replaced where it stood, so a line written before the exports
    is still before them afterwards, and there is still exactly one of them.
    """
    context = _context(tmp_path)
    _seed(
        context, "export FIRST=1\n" + legacy_env_file_contents(context.data_dir) + "export LAST=2\n"
    )

    report = SetupService(context).run(SetupRequest())

    written = context.env_file.read_text(encoding="utf-8")
    assert report.succeeded, report.warnings
    assert written == "export FIRST=1\n" + env_block(context.data_dir) + "\nexport LAST=2\n"
    assert written.count("export THEURIAN_MCP_TOKEN\n") == 1


# -- Markers a hand edit left unresolvable (SEC-18) --------------------------


@pytest.mark.parametrize(
    ("shape", "seeded"),
    [
        ("an unterminated block", f"{ENV_BLOCK_START}\nexport THEURIAN_MCP_TOKEN=mine\n"),
        ("a second block", env_block(Path("/one")) + "\n" + env_block(Path("/two")) + "\n"),
    ],
    ids=["unterminated", "two-blocks"],
)
def test_markers_that_do_not_delimit_one_block_are_a_conflict_and_not_a_rewrite(
    tmp_path: Path, shape: str, seeded: str
) -> None:
    """SEC-18. Once the delimiters disagree, setup cannot tell which lines are its.

    Every way of guessing ends in editing lines a person wrote, which is what
    the block exists to prevent -- so this is reported and never applied. The
    second shape is the one that is easy to read as tidiness: two blocks means
    the shell keeps whichever comes last, so a rewrite of either would leave the
    machine exporting a token path setup did not choose while reporting it
    converged.

    ``paths`` is asserted empty because that field is what the plan publishes as
    "files setup would write", and `uninstall --dry-run` enumerates from it
    (NFR-12): naming a file it has just refused to touch would send someone to
    delete a file they wrote.
    """
    context = _context(tmp_path)
    before = _seed(context, "export MINE=1\n" + seeded)

    step = _env_step(context)

    assert step.status is StepStatus.CONFLICTING, shape
    assert step.paths == (), "setup declares no write it has decided not to perform"
    assert context.env_file.read_bytes() == before, "probing reads; it does not repair"


def test_an_undelimited_env_file_stops_the_run_before_anything_is_written(
    tmp_path: Path,
) -> None:
    """A conflict is a question, and silence is not agreement (SEC-18, ADR-0012).

    Without ``--approve-conflicts`` the run stops at consent, so the file is
    still exactly as its owner left it. Asserted on the bytes rather than on the
    state alone: a run that reached AWAITING_CONSENT after writing would satisfy
    a state assertion and still have destroyed the lines.
    """
    context = _context(tmp_path)
    before = _seed(context, USER_SECRET + f"{ENV_BLOCK_START}\n")

    report = SetupService(context).run(SetupRequest())

    assert report.state is SetupState.AWAITING_CONSENT
    assert context.env_file.read_bytes() == before
    assert str(context.env_file) not in report.changed_paths


def test_approving_the_conflict_buys_progress_and_never_an_overwrite(tmp_path: Path) -> None:
    """``--approve-conflicts`` is consent to proceed *past* a conflict.

    It is read as "yes, do it" often enough to be worth pinning as the opposite:
    the rest of the plan applies, and the file setup could not delimit is left
    byte-identical. The run is asserted to have gone on to write something else,
    because a run that halted immediately would pass the untouched-file
    assertion by never getting anywhere -- and the step is still reported
    unresolved, so the report cannot read as converged.
    """
    context = _context(tmp_path)
    before = _seed(context, USER_SECRET + f"{ENV_BLOCK_START}\n")

    report = SetupService(context).run(SetupRequest(approve_conflicts=True))

    assert context.env_file.read_bytes() == before
    assert str(context.env_file) not in report.changed_paths
    assert str(context.auth_dir / "mcp-token") in report.changed_paths, "the rest of the plan ran"
    assert report.state is SetupState.DEGRADED
    assert any(StepId.ENV_REFERENCE.value in warning for warning in report.warnings), (
        "and the operator is told which step is still unresolved"
    )


def test_the_conflict_detail_carries_the_markers_and_the_remedy_and_no_other_line(
    tmp_path: Path,
) -> None:
    """O-3, SEC-6. `doctor --report` publishes this detail, to be pasted publicly.

    Every byte of this file except the block was written by somebody else, and a
    detail built from what the probe *read* would carry it into a public issue --
    the shape of defect `test_setup_report_withholding.py` sweeps for, in an arm
    that sweep does not reach: it seeds a file with no markers, which takes the
    ``Missing`` branch rather than this one.

    So the detail is asserted both ways round. It has to be actionable -- the
    two marker strings the person must go and look for, the path they are in,
    and the command to re-run -- and it may not contain the line beside them.
    """
    context = _context(tmp_path)
    _seed(context, USER_SECRET + f"{ENV_BLOCK_START}\nexport SOMETHING=1\n")

    detail = _env_step(context).detail

    assert str(context.env_file) in detail
    assert ENV_BLOCK_START in detail and ENV_BLOCK_END in detail
    assert "theurian setup" in detail, "the remedy is the whole of what this file gets"
    assert "SentinelEnvFileSecretZZZZ" not in detail, "and nothing else out of somebody's file"
