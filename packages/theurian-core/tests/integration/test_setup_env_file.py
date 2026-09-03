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
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import state_hash_from_the_loader, unchecked_migrations

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.domain.setup import (
    SetupReport,
    SetupState,
    SetupStep,
    StepId,
    StepOutcome,
    StepStatus,
)
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.security.env_file import (
    ENV_BLOCK_END,
    ENV_BLOCK_START,
    TOKEN_KEY,
    env_block,
    legacy_env_file_contents,
)
from theurian.security.tokens import TOKEN_ENV_VAR

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

#: A line below the block that assigns the same variable again -- what a shell
#: sourcing the file top to bottom actually ends up exporting. The value is
#: distinctive so that a report carrying it cannot do so by coincidence.
SHADOWING_LINE = "export THEURIAN_MCP_TOKEN=SentinelShadowedValueZZZZ\n"


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
        "check_migrations": unchecked_migrations,
        "current_state_hash": state_hash_from_the_loader,
    }
    return SetupContext(**{**defaults, **overrides})


def _seed(context: SetupContext, content: str) -> bytes:
    """Put a file at the env path and return its bytes, for comparing against.

    Written as *bytes*. ``write_text`` translates newlines on the way out, so a
    seed that is meant to carry ``\\r\\n`` would be at the mercy of the platform
    running the test -- and the line endings are the subject of several of the
    cases below, not their backdrop.
    """
    context.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    context.data_dir.chmod(0o700)
    context.env_file.write_bytes(content.encode("utf-8"))
    return context.env_file.read_bytes()


def _env_warnings(report: SetupReport) -> tuple[str, ...]:
    """The report's warnings about this step, which is what the operator reads."""
    return tuple(w for w in report.warnings if StepId.ENV_REFERENCE.value in w)


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


def test_a_second_start_marker_above_the_block_leaves_the_line_between_them_on_disk(
    tmp_path: Path,
) -> None:
    """The arrangement the first fix shipped past, driven end to end (#128).

    ``S``, a line the user wrote, ``S``, the block, ``E`` -- what a person leaves
    behind when they repair an unterminated block by pasting a fresh one under
    it. The guard counted a second start only in what followed the *end* marker,
    so this one was inside the span the first start opened: setup called the
    line in the middle its own, deleted it, and reported ``converged``.

    Asserted through the whole run rather than through the merge, because the
    merge refusing is worth nothing if the apply writes anyway. The sentinel is
    checked by name as well as by byte comparison: a reader of a failure here
    should see *which* line the run would have taken away.
    """
    context = _context(tmp_path)
    seeded = f"{ENV_BLOCK_START}\n{USER_SECRET}{env_block(context.data_dir)}\n"
    before = _seed(context, seeded)

    report = SetupService(context).run(SetupRequest())

    assert _env_step(context).status is StepStatus.CONFLICTING
    assert report.state is SetupState.AWAITING_CONSENT, "a conflict is a question, not a warning"
    assert context.env_file.read_bytes() == before
    assert USER_SECRET in context.env_file.read_text(encoding="utf-8")
    assert str(context.env_file) not in report.changed_paths


@pytest.mark.parametrize(
    ("shape", "seeded"),
    [
        ("a start with no end after it", f"{ENV_BLOCK_START}\nexport SOMETHING=1\n"),
        (
            "a second start marker",
            f"{ENV_BLOCK_START}\nexport SOMETHING=1\n" + env_block(Path("/one")) + "\n",
        ),
    ],
    ids=["unterminated", "repeated-start"],
)
def test_the_conflict_detail_carries_the_markers_and_the_remedy_and_no_other_line(
    tmp_path: Path, shape: str, seeded: str
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

    Both faults, not whichever one a single seeded file happens to raise: the
    non-disclosure argument is about the type the message is built from, and a
    type has more than one member.
    """
    context = _context(tmp_path)
    _seed(context, USER_SECRET + seeded)

    detail = _env_step(context).detail

    assert str(context.env_file) in detail, shape
    assert ENV_BLOCK_START in detail and ENV_BLOCK_END in detail
    assert "theurian setup" in detail, "the remedy is the whole of what this file gets"
    assert "SentinelEnvFileSecretZZZZ" not in detail, "and nothing else out of somebody's file"


# -- A file edited on a machine that uses CRLF -------------------------------


def test_a_crlf_file_keeps_every_byte_outside_the_block(tmp_path: Path) -> None:
    """SEC-18 is a claim about bytes, and a line ending is a byte somebody chose.

    Reading this file with universal newlines on turns every ``\\r\\n`` into
    ``\\n`` before the merge sees it, so the rewrite hands back a file whose
    every line ending has changed -- on a run whose entire contract is to touch
    the lines between two markers. Measured through the real service: two
    ``\\r`` bytes in, zero out.

    The ``\\r`` inside the quoted value is the half that is not cosmetic.
    Translated to a newline it splits the assignment in two, and what is left on
    the second line is a command the shell will try to run.
    """
    context = _context(tmp_path)
    keep = b'export GREETING="hello\rworld"\r\n# a comment of mine\r\n'
    tail = b"export AFTER_THE_BLOCK=2\r\n"
    stale = _stale_block(context).replace("\n", "\r\n").encode("utf-8")
    _seed(context, (keep + stale + b"\r\n" + tail).decode("utf-8"))

    report = SetupService(context).run(SetupRequest())

    written = context.env_file.read_bytes()
    assert report.succeeded, report.warnings
    assert written == keep + env_block(context.data_dir).encode("utf-8") + b"\r\n" + tail
    assert written.startswith(keep), "the bytes before the block are the bytes that went in"
    assert written.endswith(tail)
    assert written.count(b"\r") == 5, "three line endings of theirs, the value's, and the tail"


def test_a_block_that_arrived_with_crlf_endings_is_normalised_exactly_once(
    tmp_path: Path,
) -> None:
    """The upgrade a CRLF machine makes once, and the loop it must not enter.

    The block is Theurian's own text and is written with ``\\n``, so a file
    whose block came back from a Windows editor is genuinely not current and is
    rewritten. What would be a defect is doing it again: the probe would report
    ``Missing`` on every run for ever, and each rewrite is another chance to
    lose the lines around it (FR-L2).

    So the sequence is the subject -- missing once, applied once, satisfied
    afterwards -- and the second run is measured on the file's mtime rather than
    on its contents, because a rewrite that produced identical bytes is still a
    rewrite.
    """
    context = _context(tmp_path)
    keep = "export MY_OTHER_VAR=1\r\n"
    _seed(context, keep + env_block(context.data_dir).replace("\n", "\r\n") + "\r\n")

    assert _env_step(context).status is StepStatus.MISSING, "a CRLF block is not this block"

    first = SetupService(context).run(SetupRequest())
    stamped = 1_000_000_000
    os.utime(context.env_file, ns=(stamped, stamped))
    second = SetupService(context).run(SetupRequest())

    assert first.succeeded, first.warnings
    assert context.env_file.read_bytes() == (keep + env_block(context.data_dir) + "\r\n").encode(
        "utf-8"
    ), "only the block is normalised; the line above it is untouched"
    assert _env_step(context).status is StepStatus.SATISFIED
    assert context.env_file.stat().st_mtime_ns == stamped, "the second run never opened it"
    assert str(context.env_file) not in second.changed_paths


# -- A line below the block that assigns the same variable -------------------


def test_an_assignment_below_the_block_is_reported_rather_than_edited_away(
    tmp_path: Path,
) -> None:
    """A current block and a true report are not the same thing (SEC-18).

    The probe's question is deliberately blind to everything outside the
    markers, and a shell keeps the *last* assignment it reads -- so a line
    pasted years ago below the block is what the machine actually exports while
    setup reports the file "exports THEURIAN_MCP_TOKEN by reference".

    Never repaired: that line belongs to whoever wrote it. Never a conflict
    either, because a conflict asks for consent to proceed and there is nothing
    here setup wants to do. Reported -- which means the run ends DEGRADED, and
    DEGRADED is success with warnings.
    """
    context = _context(tmp_path)
    before = _seed(context, env_block(context.data_dir) + "\n" + SHADOWING_LINE)

    report = SetupService(context).run(SetupRequest())

    assert report.succeeded, "a line setup does not own is not a failure"
    assert report.state is SetupState.DEGRADED, "but it is not convergence either"
    assert context.env_file.read_bytes() == before, "the line is reported, never edited away"
    assert str(context.env_file) not in report.changed_paths


def test_the_override_warning_names_the_variable_and_never_the_line_it_found(
    tmp_path: Path,
) -> None:
    """SEC-6, O-3. This warning is printed by `doctor --report`, to be pasted.

    The step is SATISFIED, so it does not go through the conflict path that
    ``test_setup_report_withholding.py`` sweeps -- a satisfied step carrying a
    detail is a channel that sweep was never pointed at. What the person needs
    is the path, the variable and the marker to move the line above; what they
    must not get is the line itself, because whatever is on the right of that
    ``=`` is a credential often enough to matter.

    Exactly one warning, because the same detail reaching the report twice is
    how a real one gets skimmed past.
    """
    context = _context(tmp_path)
    _seed(context, env_block(context.data_dir) + "\n" + SHADOWING_LINE)

    report = SetupService(context).run(SetupRequest())

    warnings = _env_warnings(report)
    assert len(warnings) == 1, report.warnings
    assert str(context.env_file) in warnings[0]
    assert TOKEN_ENV_VAR in warnings[0] and ENV_BLOCK_START in warnings[0]
    assert "SentinelShadowedValue" not in warnings[0], "the value on that line is not ours to print"


@pytest.mark.parametrize(
    ("shape", "line"),
    [
        ("a bare re-export of what the block just set", "export THEURIAN_MCP_TOKEN\n"),
        ("a commented-out assignment", "# THEURIAN_MCP_TOKEN=from-an-older-install\n"),
    ],
    ids=["bare-export", "comment"],
)
def test_a_line_that_only_mentions_the_token_leaves_the_run_converged(
    tmp_path: Path, shape: str, line: str
) -> None:
    """A warning that fires on a converged machine is a warning nobody reads.

    A bare ``export THEURIAN_MCP_TOKEN`` re-exports the value the block set one
    line earlier and changes nothing; a commented-out line is a comment. Both
    are ordinary contents of a file whose own header invites people to add to
    it, and reporting either would end every run DEGRADED on a machine that has
    nothing wrong with it.
    """
    context = _context(tmp_path)
    _seed(context, env_block(context.data_dir) + "\n" + line)

    report = SetupService(context).run(SetupRequest())

    assert _env_warnings(report) == (), shape
    assert _env_step(context).status is StepStatus.SATISFIED


def test_a_stale_block_with_a_later_assignment_is_rewritten_rather_than_reported(
    tmp_path: Path,
) -> None:
    """Currency is asked first, and the order is the property (#128, §6.2 row 7).

    Both things are true of this machine at once: the block names a data
    directory this install no longer uses, *and* a line below it assigns the
    variable again. The two arms answer different questions, and only one of
    them has work attached -- so the stale block is rewritten, and the override
    is reported afterwards by the re-probe.

    Fold the shadow test into the currency test -- ``if not current and not
    shadowed`` -- and this machine reports ``satisfied`` with a caveat instead:
    the block is never rewritten, so the env file goes on naming a token path
    that does not exist, and the reason a person is given is a line of their own
    that is not what is wrong with it. The run ends DEGRADED either way, which is
    what makes it invisible from the state alone.

    Asserted on the bytes, so the rewrite is measured rather than inferred from
    the status, and the shadowing line is asserted to have survived it: setup
    rewrites the block and never a line outside it (SEC-18).
    """
    context = _context(tmp_path)
    _seed(context, _stale_block(context) + "\n" + SHADOWING_LINE)

    assert _env_step(context).status is StepStatus.MISSING, "an out-of-date block is work to do"

    report = SetupService(context).run(SetupRequest())

    assert context.env_file.read_text(encoding="utf-8") == (
        env_block(context.data_dir) + "\n" + SHADOWING_LINE
    )
    assert _env_step(context).status is StepStatus.SATISFIED, "and the block is current afterwards"
    assert len(_env_warnings(report)) == 1, "with the override reported by the re-probe"


# -- The heuristic's boundary, as designed -----------------------------------
#
# `contains_shadowing_assignment` recognises the *direct* assignment forms and
# says so: its docstring tabulates four shapes that assign the variable and are
# answered ``False``, and one that does not and is answered ``True``. Extending
# it to cover them is refused rather than deferred -- what a line does is
# settled by the shell at run time, and a probe that runs somebody's shell
# profile is not a probe.
#
# The four below are therefore pinned as the *recorded* misses. A change that
# starts warning on any of them is not a bug fix that happens to break a test:
# it is a claim that a line-level rule can decide what these do, and it has to
# come here and say so.


def _bash() -> str:
    """The shell whose behaviour the heuristic's table was measured against."""
    found = shutil.which("bash")
    assert found, "these cases are about what a shell does, so they need one"
    return found


#: The value the evading shapes assign. Distinctive, so that finding it in what
#: the shell exported cannot be a coincidence.
EVADED_VALUE = "SentinelEvadedValueZZZZ"

#: Every shape :func:`~theurian.security.env_file._assigns_token` tabulates as
#: assigning the variable while answering ``False``, written as a line below the
#: block. Spelled here as the docstring spells them, because that table is what
#: this test holds in place.
EVADING_SHAPES = {
    "and-list": f'[ -n "$HOME" ] && THEURIAN_MCP_TOKEN={EVADED_VALUE}\n',
    "if-then": f'if [ -n "$HOME" ]; then THEURIAN_MCP_TOKEN={EVADED_VALUE}; fi\n',
    "group": f"{{ THEURIAN_MCP_TOKEN={EVADED_VALUE}; }}\n",
    "eval": f"eval 'THEURIAN_MCP_TOKEN={EVADED_VALUE}'\n",
}

#: An assignment inside a quoted heredoc *body*, which is not shell at all: the
#: shell hands those bytes to `cat` and exports nothing. The one shape the
#: heuristic answers ``True`` about wrongly.
HEREDOC_SHAPE = "cat <<'EOF' >/dev/null\nTHEURIAN_MCP_TOKEN=SentinelHeredocValueZZZZ\nEOF\n"


def _sourced_token(context: SetupContext) -> str:
    """What a shell that sources this file ends up exporting.

    The whole subject of these two tests is the gap between what the file *does*
    and what a line-level rule can see, so the first half is measured with a real
    shell rather than asserted from the same reading the code under test makes.

    The environment is built rather than inherited: ``HOME`` is what the
    ``and-list`` and ``if-then`` shapes branch on, and ``PATH`` is what finds the
    ``cat`` the block itself runs.
    """
    result = subprocess.run(  # noqa: S603 - an absolute path from `shutil.which`
        [_bash(), "-c", f'. "{context.env_file}"; printf "%s" "$THEURIAN_MCP_TOKEN"'],
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": str(context.home), "PATH": os.environ.get("PATH", "")},
    )
    return result.stdout


@pytest.mark.parametrize("shape", sorted(EVADING_SHAPES))
def test_a_shape_the_heuristic_does_not_recognise_leaves_the_run_silent(
    tmp_path: Path, shape: str
) -> None:
    """The recorded miss, measured rather than restated (SEC-18, #128).

    On each of these machines the shell exports the later line's value, and
    setup ends CONVERGED with nothing to say -- its ``env-reference`` summary
    still reading "exports THEURIAN_MCP_TOKEN by reference", which is false
    here. That is the residual the heuristic's docstring records and declines to
    close, and this is where it is written down as behaviour.

    The shell call is what stops this being a test of the implementation against
    itself: without it the assertions say only that a function answers ``False``
    for four strings, which is a restatement of the function. With it they say
    that four files really do export something else and Theurian really is
    silent about them.

    **Failing here is not a licence to update the expectation.** A heuristic that
    began catching one of these would be an improvement, and it would have to
    arrive with the argument the docstring says nobody has -- so the change is to
    that docstring and to this list, deliberately, in the same commit.
    """
    context = _context(tmp_path)
    _seed(context, env_block(context.data_dir) + "\n" + EVADING_SHAPES[shape])

    report = SetupService(context).run(SetupRequest())

    assert _sourced_token(context) == EVADED_VALUE, "the shell really does take the later line"
    assert report.state is SetupState.CONVERGED, report.warnings
    assert _env_warnings(report) == (), "and this is the miss the docstring records"


def test_the_sentence_about_a_line_it_cannot_read_claims_only_that_it_appears_to_assign(
    tmp_path: Path,
) -> None:
    """SEC-6, O-3. The published sentence is measured on a machine where it is wrong.

    An assignment inside a quoted heredoc body is not shell: those bytes go to
    `cat` and the shell keeps exporting the block's value. The heuristic answers
    ``True`` anyway -- it reads one line at a time and that line has the shape --
    so this run warns about a file that is doing exactly what setup wanted.

    Which is why every sentence built on that answer says the line *appears* to
    assign. Pinned here rather than beside a real override, because here the
    stronger wording would be provably false: the shell has just been asked, and
    it exported the block's token. A report that told this person their line
    overrides the block would send them to delete a line that does nothing.

    **Both sentences, because they are published separately.** The ``detail``
    reaches a person through the report's ``warnings``; the ``summary`` is what
    `doctor --json` prints for the step and what the plugin's `setup.md` renders,
    and a reader who stops at a status of ``satisfied`` sees only that one.
    Measured: dropping the hedge from the summary alone survived all 2442 tests
    while the detail's was held.

    The value on that line is asserted absent for the reason the test above this
    section gives -- `doctor --report` publishes these warnings, and whatever is
    to the right of an ``=`` is a credential often enough to matter.
    """
    context = _context(tmp_path)
    _seed(context, env_block(context.data_dir) + "\n" + HEREDOC_SHAPE)

    report = SetupService(context).run(SetupRequest())

    token = (context.auth_dir / TOKEN_KEY).read_text(encoding="utf-8").strip()
    assert token, "the block's own value has to exist, or the comparison below proves nothing"
    assert _sourced_token(context) == token, "the shell kept the block's value, not that line's"

    warnings = _env_warnings(report)
    assert len(warnings) == 1, report.warnings
    assert "appears" in warnings[0], "the sentence may not claim what only the shell decides"
    assert "SentinelHeredocValue" not in warnings[0], "and not the line it matched"

    step = _env_step(context)
    assert step.status is StepStatus.SATISFIED, "the fixture has to reach the arm that hedges"
    assert "appears" in step.summary, "the sentence a reader who stops at the status sees"


def test_the_env_file_is_private_however_permissive_the_umask_is(tmp_path: Path) -> None:
    """SEC-5, and a guarantee that must not depend on whoever is running the test.

    A process umask of ``0o000`` is unusual and entirely legal -- a daemon, a CI
    runner, a shell somebody set it in. The creation mode on the ``open`` is
    ANDed with the umask, so ``0600`` there is not on its own a promise; the
    ``chmod`` after the write is what closes it. Every other mode assertion in
    this module runs under whatever umask the developer has, which makes their
    *verdict* depend on it -- a ``0666`` creation reads as ``0600`` under the
    common ``0o077``. This one fixes the umask at its most permissive, so the
    answer is about the code.

    On a file this run *creates*, the two halves are redundant to each other:
    measured, dropping either one on its own still leaves 0600, and only
    dropping both is caught. The arm where the chmod is the sole guarantee is
    the one below, on a file that already exists.
    """
    context = _context(tmp_path)
    previous = os.umask(0o000)
    try:
        report = SetupService(context).run(SetupRequest())
    finally:
        os.umask(previous)

    assert report.succeeded, report.warnings
    assert context.env_file.stat().st_mode & 0o777 == 0o600


def test_an_env_file_left_group_readable_by_an_older_version_is_tightened(
    tmp_path: Path,
) -> None:
    """A creation mode does not apply to a file that is already there (SEC-5).

    Every machine set up by 0.1.0.dev0 through dev2 has this file already, and
    the mode it was given then is the mode it still has -- ``write_text`` under
    the usual ``0o022`` leaves 0644, so the file naming the token's location is
    readable by every account on the machine. The ``open`` that rewrites the
    block cannot fix that: its mode argument is consulted only on creation. The
    ``chmod`` after the write is the whole of the repair, and this is the arm
    that can tell, because here it is not shadowed by the opener.
    """
    context = _context(tmp_path)
    _seed(context, BEFORE + _stale_block(context) + "\n" + AFTER)
    context.env_file.chmod(0o644)

    report = SetupService(context).run(SetupRequest())

    assert report.succeeded, report.warnings
    assert context.env_file.stat().st_mode & 0o777 == 0o600
    assert context.env_file.read_text(encoding="utf-8") == (
        BEFORE + env_block(context.data_dir) + "\n" + AFTER
    ), "and the rewrite it came in for still happened"
