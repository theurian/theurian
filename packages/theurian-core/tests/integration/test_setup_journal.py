"""The record a stopped run leaves behind (#47, §6.4, SEC-6).

``~/.theurian/setup-journal.jsonl`` is appended to and never rewritten, because
nothing setup does has an inverse to replay: a run that stops halfway is
repaired by a person reading what had already been done. That gives the file
three properties this module holds, none of which the converged-run assertions
in ``test_setup_service.py`` can reach.

**It records the failure, not just the successes.** Every other fixture in the
suite fails *after* a step has applied, so the runner's "has anything been
journalled yet" flag is already true by the time the failing step reaches it --
which is why two mutations of that line survived the whole suite. One reordered
it so the failed entry is skipped once anything had been written; the other
dropped its return value, which only shows when the failing step is the first
one to apply. Both are shaped like the fixtures that were missing, so both are
driven here: a halt with something behind it, and a halt with nothing behind it.

**It is a file this run wrote, so ``changed_paths`` names it** -- and it belongs
to no step, so only the runner can. `theurian setup --help` names it as the one
write outside the seven steps for the same reason.

**It is created 0600, and it holds the local absolute paths and the verbatim
exception text of whatever stopped the run.** The arm that fails to tighten the
data directory is precisely the arm that leaves this file's parent 0755, so the
directory around it is not what protects it.

Real files under a temporary root, fake collaborators: registering a real
service would reach the developer's own login session, which redirecting
``HOME`` does not prevent.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.domain.setup import SetupState, StepId, StepOutcome
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import TOKEN_KEY, FileSecretStore
from theurian.security.tokens import MIN_TOKEN_LENGTH

pytestmark = pytest.mark.integration

PORT = 7419

#: ``RLIMIT_FSIZE`` does not exist on Windows. The seam below is skipped rather
#: than adapted where it would silently stop being one: a run that can append to
#: the journal anyway passes every assertion in it while testing nothing.
_NO_FILE_SIZE_LIMIT: Final = sys.platform == "win32"


def _context(tmp_path: Path) -> SetupContext:
    """A machine where nothing is set up yet, and no daemon is running.

    Local rather than shared, like every other setup module's: what each of
    these tests needs is a different *state on disk* before the run, and a
    fixture that also decided the failure would hide which state produced it.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_dir = home / ".theurian"
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    # 0755 and a real script: `probe_core` requires an absolute path that
    # resolves and can be started, and a 0644 file conflicts -- which aborts the
    # run before any apply, leaving every assertion below about a report that
    # never reached a step (#49).
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return SetupContext(
        home=home,
        data_dir=data_dir,
        port=PORT,
        project_root=None,
        connection=ConnectionSpec(port=PORT),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable=str(executable),
    )


def _entries(journal: Path) -> list[dict[str, Any]]:
    """The journal as objects, so an assertion reads a field and not a substring."""
    return [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _halt_with_something_behind_it(tmp_path: Path) -> tuple[SetupContext, SetupService]:
    """A run that mints the token and then fails on the env file.

    ``DATA_DIRECTORY`` is pre-converged at 0700, so ``token`` and
    ``token-storage`` both apply and ``auth/mcp-token`` exists before the run
    reaches step 7. The env file is created as a *directory*, so
    ``apply_env_reference``'s ``os.open`` raises ``IsADirectoryError`` -- a real
    critical failure from a shipped step. Step 7 is ahead of ``daemon-service``,
    so the run halts before any service registration and the fake service is
    never even asked to install.
    """
    context = _context(tmp_path)
    context.data_dir.mkdir(parents=True)
    context.data_dir.chmod(0o700)
    context.env_file.mkdir()
    return context, SetupService(context)


def test_the_step_that_stopped_the_run_is_journalled_as_failed(tmp_path: Path) -> None:
    """§6.4. The record has to say what broke, or it is a record of a success.

    A halted run's journal is read by whoever repairs the machine, and the
    entry they need most is the one for the step that stopped. Measured as a
    mutation: reordering the append to ``journalled or self._journal(...)``
    short-circuits it away for every run where an earlier step had already
    applied -- which is every fixture in this suite -- so the file went on
    listing what succeeded and said nothing about the failure, and the whole
    suite stayed green.

    Asserted as the only failed entry, by step: a journal that recorded *every*
    step as failed would satisfy a membership check. The applied entry for the
    token is asserted alongside, because the two events are what make the file
    a sequence a person can follow, and a run that journalled only failures
    would be the same defect pointing the other way.
    """
    _, service = _halt_with_something_behind_it(tmp_path)

    report = service.run(SetupRequest())

    entries = _entries(service.journal_path)
    failed = [entry for entry in entries if entry["event"] == "failed"]
    assert report.state is SetupState.HALTED, "the fixture has to reach the halt path"
    assert [entry["step"] for entry in failed] == [StepId.ENV_REFERENCE.value], (
        "exactly the step that stopped the run is recorded as having failed"
    )
    assert "IsADirectoryError" in failed[0]["detail"], "with the reason it stopped"
    assert StepId.TOKEN.value in {
        entry["step"] for entry in entries if entry["event"] == "applied"
    }, "and the steps that did finish are still recorded as applied"


def test_the_journal_is_disclosed_when_the_first_step_to_apply_is_the_one_that_failed(
    tmp_path: Path,
) -> None:
    """#47. The run wrote exactly one file, and it is the runner's own.

    ``changed_paths`` names the journal only when an append reached the disk --
    `_journal` swallows its ``OSError``, and announcing a file that was never
    created is the same defect as hiding one that was. That "reached the disk"
    answer is the return value of the append, and dropping it survived the whole
    suite: every other fixture has a successful apply before the failure, so the
    flag was already true and nothing depended on the failed step's own answer.

    Here nothing precedes it. The data directory is already 0700, so it is
    ``SATISFIED`` and never applied; the token step is the first step to apply
    anything and it fails against a *directory* at ``auth/mcp-token``. Both are
    asserted by outcome, because the fixture is the whole test: if the data
    directory ever stopped being pre-satisfied, this would silently become one
    more run with something behind it.

    The equality is what makes the disclosure meaningful in both directions --
    the journal is named, and nothing else is, on a run that wrote nothing else.
    """
    context = _context(tmp_path)
    context.data_dir.mkdir(parents=True)
    context.data_dir.chmod(0o700)
    context.auth_dir.mkdir(parents=True, mode=0o700)
    (context.auth_dir / TOKEN_KEY).mkdir(mode=0o700)
    service = SetupService(context)

    report = service.run(SetupRequest())

    satisfied = report.step(StepId.DATA_DIRECTORY)
    failed = report.step(StepId.TOKEN)
    assert report.state is SetupState.HALTED, "the token step is critical"
    assert satisfied is not None and satisfied.outcome is StepOutcome.UNCHANGED, (
        "the data directory was already private, so nothing applied before the failure"
    )
    assert failed is not None and failed.outcome is StepOutcome.FAILED
    assert service.journal_path.is_file(), "the failure itself is journalled"
    assert report.changed_paths == (str(service.journal_path),), (
        "so the one file this run wrote is the one file it names"
    )


#: The soft ``RLIMIT_FSIZE`` in force while the run below executes. The limit is
#: process-wide for the length of that ``with``, so it has to sit far above every
#: other file the run touches -- the token is 43 bytes, the env file a couple of
#: hundred -- and above anything the interpreter itself might write in the same
#: window, such as a module it byte-compiles on first import. A mebibyte clears
#: all of it; the journal is padded up to just under the limit instead, so the
#: only write in the run with no room left is the append.
_FILE_SIZE_LIMIT: Final = 1 << 20

#: How much room the padding leaves. Not zero: a write with *no* room fails
#: outright with ``EFBIG``, and an implementation that reported the wrong answer
#: for a partial write would be caught by the wrong mechanism. Twenty bytes is
#: less than any record this journal can hold and more than none, which is
#: exactly the case ``write(2)`` reports as a short count rather than as an
#: error.
_ROOM_FOR_LESS_THAN_ONE_RECORD: Final = 20


def _one_record_of_exactly(size: int) -> str:
    """A single valid journal line ``size`` bytes long, newline included.

    Padding with one long record rather than many short ones keeps
    :func:`_entries` honest about what the run appended: after the truncated
    append the file holds two lines, and the second is the fragment.
    """
    fields = {"step": StepId.DATA_DIRECTORY.value, "event": "applied", "detail": ""}
    overhead = len(json.dumps(fields, sort_keys=True) + "\n")
    return json.dumps({**fields, "detail": "x" * (size - overhead)}, sort_keys=True) + "\n"


@contextlib.contextmanager
def _a_file_size_limit_of(size: int) -> Iterator[None]:
    """Refuse, for the length of this block, any write past ``size`` bytes.

    ``RLIMIT_FSIZE`` is how a full disk is arranged in a test: it is the same
    ``write(2)`` path as ``ENOSPC``, and it is the only one of the two that can
    be turned on and off again. Both the limit and the signal disposition are
    process-wide, so the block wraps the run and nothing else, and both are put
    back whether or not the body raised.

    ``SIGXFSZ`` is ignored first because its default disposition terminates the
    process -- the test would not fail, it would take the session with it.
    """
    # Imported here rather than at the top of the module: ``resource`` is
    # POSIX-only, and a module-level import would turn the skip above into a
    # collection error on Windows -- one test not running, against the whole
    # file not running.
    import resource
    import signal

    previous_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        signal.signal(signal.SIGXFSZ, previous_handler)


@pytest.mark.skipif(_NO_FILE_SIZE_LIMIT, reason="RLIMIT_FSIZE is POSIX")
def test_an_append_that_could_not_complete_leaves_the_journal_undisclosed(
    tmp_path: Path,
) -> None:
    """#47. ``changed_paths`` names files this run wrote, not files it opened.

    ``write(2)`` is permitted to write fewer bytes than it was handed and to
    report that count *without raising*, which is what a file-size limit and a
    full disk both produce. An ``os.write`` whose return value was discarded
    therefore left a half-record on disk and answered ``True``: measured as
    three half-lines run together into a single entry no reader can parse,
    announced in ``changed_paths`` as a file this run wrote, and pointed at by
    the plugin as the record an operator should read after a failure.
    :class:`io.BufferedWriter` loops until the buffer is empty and raises
    whatever the flush hit, which is the ``except OSError`` that answers
    ``False``.

    The fixture is a journal already padded to twenty bytes below the limit, so
    the run's first append is the one with nowhere to go. Every later append has
    no room at all and fails the same way, which is why the disclosure this
    asserts is about the whole run and not about one line.

    Three assertions guard it, and none is decoration. The credential is
    asserted *present* in ``changed_paths``, because a run that disclosed
    nothing at all would satisfy the prohibition while proving nothing. The
    line count and the unparseable tail are what show the short write really
    happened: with no fragment on disk the append failed outright, which is the
    arm the raw ``os.write`` already handled and not the one this test is for.
    """
    context, service = _halt_with_something_behind_it(tmp_path)
    service.journal_path.write_text(
        _one_record_of_exactly(_FILE_SIZE_LIMIT - _ROOM_FOR_LESS_THAN_ONE_RECORD),
        encoding="utf-8",
    )

    with _a_file_size_limit_of(_FILE_SIZE_LIMIT):
        report = service.run(SetupRequest())

    lines = service.journal_path.read_text(encoding="utf-8").splitlines()
    assert report.state is SetupState.HALTED, "the fixture has to reach the halt path"
    assert str(context.auth_dir / TOKEN_KEY) in report.changed_paths, (
        "the run did write the credential, and says so -- the prohibition below "
        "would pass on a run that wrote nothing"
    )
    assert len(lines) == 2, f"the padding plus the fragment of one append, not {len(lines)}"
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[-1])
    assert str(service.journal_path) not in report.changed_paths, (
        "a record no reader can parse is not a file this run wrote"
    )


@pytest.fixture
def a_permissive_umask() -> Iterator[None]:
    """``0022`` -- the default on macOS and on every mainstream Linux.

    Pinned rather than inherited, because the assertion below is that the mode
    comes from the ``open`` and not from the environment: under a ``0077``
    umask a journal created 0644 arrives as 0600 anyway, and the test would pass
    for the wrong reason on the machine of whoever happens to run it that way.
    Restored on the way out; the process is shared with the rest of the suite.
    """
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def test_the_journal_is_created_private_inside_a_directory_that_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a_permissive_umask: None
) -> None:
    """SEC-6. The lines are local paths and verbatim exception text.

    ``changed_paths`` points every reader of a halted report straight at this
    file, and the arm that fails to tighten the data directory is exactly the
    arm that leaves its parent 0755 -- a refused ``chmod`` is what put the run
    there. So the directory around it is not what keeps it private, and at the
    process umask it would have been created 0644: readable by every local
    account, holding a record of the operator's filesystem.

    The parent's mode is asserted too. Without it the test would pass on a
    journal that was private only because its directory happened to be, which is
    the arrangement this scenario is chosen to deny.
    """
    context = _context(tmp_path)
    context.data_dir.mkdir(parents=True)
    context.data_dir.chmod(0o755)
    real_chmod = Path.chmod

    def refuse(self: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if self == context.data_dir:
            raise OSError(1, "Operation not permitted", str(self))
        real_chmod(self, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", refuse)
    service = SetupService(context)

    report = service.run(SetupRequest())

    assert report.state is SetupState.HALTED, "the data-directory step is critical"
    assert service.journal_path.is_file(), "the failure is journalled even so"
    assert service.journal_path.stat().st_mode & 0o777 == 0o600, (
        "the journal is created private, at the mode the open asks for"
    )
    assert context.data_dir.stat().st_mode & 0o777 == 0o755, (
        "and the directory around it is not what made it so -- the tighten is what failed"
    )


def test_the_journal_never_records_the_token_it_watched_being_minted(tmp_path: Path) -> None:
    """SEC-6, ADR-0011. The credential's path is disclosed; its value never is.

    The journal sits in the same directory as the token file and records every
    applied step, including the one that mints it. It is a plain file on disk
    that outlives the run, is created before the process knows whether the run
    will succeed, and is the first thing an operator is pointed at afterwards --
    so a step detail that ever carried the value would be the credential written
    twice, once where nobody looks for it.

    The positive assertion comes first, and it is what stops the prohibition
    passing on an empty file: the token step *is* recorded. The length guard is
    the other half -- a real ``token_urlsafe(32)`` is 43 characters of CSPRNG
    output and cannot coincidentally be absent, while an empty value would make
    ``not in`` false and fail this test rather than pass it.
    """
    context, service = _halt_with_something_behind_it(tmp_path)

    report = service.run(SetupRequest())

    token_value = (context.auth_dir / TOKEN_KEY).read_text(encoding="utf-8").strip()
    journal = service.journal_path.read_text(encoding="utf-8")
    assert report.state is SetupState.HALTED, "the fixture has to reach the halt path"
    assert len(token_value) >= MIN_TOKEN_LENGTH, (
        "a real credential must have been minted, or the absence below proves nothing"
    )
    assert StepId.TOKEN.value in {entry["step"] for entry in _entries(service.journal_path)}, (
        "the minting is recorded, so the file being searched is the file that records it"
    )
    assert token_value not in journal, "and what it records is the step, never the secret"
