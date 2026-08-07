"""Probes that reported a state they had not checked (#49).

The class: **a step asserts a state it did not check, and the flow acts on that
assertion.** A ``satisfied`` verdict is the one nobody re-reads, so a probe whose
check is smaller than its own status name spends its credibility on a state it
never looked at.

Two members were measured as surviving mutations over the whole suite, and
neither had the thing that kills the equivalent mutation for every step that
*is* covered: a test calling the probe directly and asserting on its result.

- ``probe_core`` accepted any truthy ``executable``. Reducing its check to
  ``if not context.executable:`` kept 1717 tests green on ``d6bc90e``.
- ``probe_project_layout`` accepted a partial layout. Reducing
  ``_REQUIRED_PROJECT_DIRS`` to ``("migrations",)`` kept the same 1717 green.

**Both were still green after #82**, which rewrote ``probe_core`` for the missing
``daemon`` extra without touching the arm this file pins. Re-measured with
``tools/mutate.py`` against ``d6bc90e`` before anything here was written.

What separates the two members is what the flow does with the answer.
``_blocking_conflicts`` consults only ``PLATFORM`` and ``CORE_PRESENT``, so
``core-present`` is one of the two verdicts that can abort a run -- setup acts on
it by *stopping*, and a wrong ``satisfied`` there is what lets
``apply_daemon_service`` write a unit invoking a path that will not run.
``project-layout`` gates nothing: it is report-only and non-critical, so the
thing acting on its assertion is the person reading the report, who is told the
repository is ready for an ``ingest`` that will walk an absent ``knowledge/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import (
    _REQUIRED_PROJECT_DIRS,
    probe_core,
    probe_project_layout,
)
from theurian.domain.setup import StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore


def _context(
    tmp_path: Path, *, executable: str = "", project_root: Path | None = None
) -> SetupContext:
    data_dir = tmp_path / "home" / ".theurian"
    return SetupContext(
        home=tmp_path / "home",
        data_dir=data_dir,
        port=7419,
        project_root=project_root,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable=executable,
    )


# -- core-present ------------------------------------------------------------


def test_an_executable_path_that_does_not_resolve_is_not_core_being_present(
    tmp_path: Path,
) -> None:
    """The surviving mutation, stated as the assertion it broke.

    ``if context.executable:`` is true of any non-empty string, and the string
    that reaches this probe is written into a launchd plist or a systemd unit.
    A service manager starts with a PATH that is not the user's shell's, which is
    the whole reason the step demands a path rather than a name -- so a unit
    pointing at a path that is not there fails at every start, with a setup
    report that said ``core-present: satisfied`` and nothing suggesting why.
    """
    absent = tmp_path / "bin" / "theurian"

    step = probe_core(_context(tmp_path, executable=str(absent)))

    assert step.status is StepStatus.CONFLICTING
    assert "absolute path" in step.detail


def test_a_relative_executable_is_not_core_being_present(tmp_path: Path) -> None:
    """The same assertion one notch further out, and the probe's own words.

    ``probe_core`` says the executable "has to be nameable by an absolute path".
    ``Path("theurian").exists()`` answers that question against the *current
    working directory*, so a checkout holding a file called ``theurian`` -- this
    repository's own ``packaging/`` tree has held such names -- satisfied a check
    written to reject exactly the case where the name only resolves because of
    where the caller happened to be standing.

    Not reachable from ``cli.setup_commands._executable``, which resolves before
    it returns. That is what makes this the gap a future caller turns into a
    defect rather than one a user can reach today, and why the check now states
    it instead of relying on its only current caller to.
    """
    monkeypatched_cwd = tmp_path / "cwd"
    monkeypatched_cwd.mkdir()
    (monkeypatched_cwd / "theurian").touch()

    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(monkeypatched_cwd)
        step = probe_core(_context(tmp_path, executable="theurian"))

    assert step.status is StepStatus.CONFLICTING, (
        "a name that only resolves from this directory is not an absolute path"
    )


def _runnable(path: Path) -> Path:
    """A file a service manager could actually exec.

    ``touch()`` leaves 0644. Six fixtures across five files built their fake
    Core that way and every one of them expected ``satisfied`` -- which is the
    measurement that says nothing was testing this predicate, rather than a
    detail of how they were written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_a_directory_at_the_executable_path_is_not_core_being_present(tmp_path: Path) -> None:
    """`exists()` is true of a directory, and `os.access(X_OK)` is true of one too.

    Measured, both of them, which is why the check needs ``is_file`` *and*
    ``X_OK`` and neither alone: a directory answers ``X_OK`` true because the
    bit means "searchable" there, and a 0644 regular file answers ``is_file``
    true. Each predicate admits exactly what the other rejects.
    """
    a_directory = tmp_path / "theurian"
    a_directory.mkdir()

    step = probe_core(_context(tmp_path, executable=str(a_directory)))

    assert step.status is StepStatus.CONFLICTING


def test_a_file_without_the_executable_bit_is_not_core_being_present(tmp_path: Path) -> None:
    """The step that decides whether setup may proceed approved a 0644 file.

    ``CORE_PRESENT`` is one of the two verdicts `_blocking_conflicts` reads, so
    a wrong ``satisfied`` here is what lets `apply_daemon_service` write a
    launchd plist whose ``ProgramArguments[0]`` cannot be executed -- a service
    that fails at every start, from the one step whose stated job is to notice
    exactly that.
    """
    not_executable = tmp_path / "bin" / "theurian"
    not_executable.parent.mkdir(parents=True)
    not_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    not_executable.chmod(0o644)

    step = probe_core(_context(tmp_path, executable=str(not_executable)))

    assert step.status is StepStatus.CONFLICTING


def test_an_absolute_runnable_executable_is_core_being_present(tmp_path: Path) -> None:
    """The other half, or every test above would pass by always conflicting."""
    step = probe_core(_context(tmp_path, executable=str(_runnable(tmp_path / "bin" / "theurian"))))

    assert step.status is StepStatus.SATISFIED


def test_a_symlink_to_a_runnable_executable_is_core_being_present(tmp_path: Path) -> None:
    """The shape a real install has, so the check cannot be tightened into it.

    `uv tool install` and `pipx` both put a symlink on `PATH` pointing into the
    tool's own virtualenv, and `shutil.which` returns that symlink. A check
    written with `lstat` semantics -- `is_symlink`, or `stat` without following
    -- would reject every genuine installation, so this is the case that keeps
    the fix from overshooting.
    """
    target = _runnable(tmp_path / "venv" / "bin" / "theurian")
    link = tmp_path / "bin" / "theurian"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    step = probe_core(_context(tmp_path, executable=str(link)))

    assert step.status is StepStatus.SATISFIED


def test_no_executable_at_all_is_not_core_being_present(tmp_path: Path) -> None:
    """The empty string, which is what `_executable()` returns when it fails."""
    step = probe_core(_context(tmp_path, executable=""))

    assert step.status is StepStatus.CONFLICTING


# -- project-layout ----------------------------------------------------------

#: The layout `theurian init` writes, spelled out rather than imported from the
#: constant under test. Deriving it from `_REQUIRED_PROJECT_DIRS` was measured to
#: not work: parametrising over the tuple means shrinking the tuple *deletes*
#: cases instead of failing them, so the `("migrations",)` mutation still
#: survived -- control 1728 passed, mutant 1726 passed, and pytest counts a case
#: that never ran as neither. A hand-written literal is the same choice
#: `INSTALLERS` in `test_setup_claims.py` makes, for the same reason: a test that
#: reads its expectation out of the thing it is checking is green for whatever
#: that thing says.
_EXPECTED_PROJECT_DIRS: Final = ("migrations", "knowledge", "state")


def test_the_required_layout_is_exactly_these_three_directories() -> None:
    """The tuple's contents, asserted directly.

    The kill that does not depend on any probe behaviour: an edit that drops an
    entry, adds one, or reorders it fails here with the diff in the message.
    """
    assert _REQUIRED_PROJECT_DIRS == _EXPECTED_PROJECT_DIRS


@pytest.mark.parametrize("absent", _EXPECTED_PROJECT_DIRS)
def test_every_required_directory_is_one_the_layout_is_not_complete_without(
    tmp_path: Path, absent: str
) -> None:
    """Each directory withheld in turn, against the literal above.

    The surviving mutation reduced ``_REQUIRED_PROJECT_DIRS`` to
    ``("migrations",)``. Nothing caught it because the states the suite probes
    hold either all three directories or none: with none, a one-element tuple
    still reports MISSING, so the *status* was identical and only the summary
    moved. Withholding one directory at a time is what makes the tuple's
    contents observable -- under that mutation the ``knowledge`` and ``state``
    cases find a layout the probe now calls complete, and go red.
    """
    root = tmp_path / "repo"
    theurian_dir = root / ".theurian"
    for name in _EXPECTED_PROJECT_DIRS:
        if name != absent:
            (theurian_dir / name).mkdir(parents=True)

    step = probe_project_layout(_context(tmp_path, project_root=root))

    assert step.status is StepStatus.MISSING, f"{absent} is missing and the layout is not complete"
    assert absent in step.summary


def test_the_whole_layout_is_what_satisfies_the_step(tmp_path: Path) -> None:
    """The complement, or the parametrised test passes by always reporting MISSING."""
    root = tmp_path / "repo"
    for name in _EXPECTED_PROJECT_DIRS:
        (root / ".theurian" / name).mkdir(parents=True)

    step = probe_project_layout(_context(tmp_path, project_root=root))

    assert step.status is StepStatus.SATISFIED


def test_a_file_is_not_a_directory_for_the_purpose_of_the_layout(tmp_path: Path) -> None:
    """`is_dir`, not `exists`: `ingest` walks these, and a file cannot be walked."""
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    for name in _EXPECTED_PROJECT_DIRS:
        (root / ".theurian" / name).touch()

    step = probe_project_layout(_context(tmp_path, project_root=root))

    assert step.status is StepStatus.MISSING
