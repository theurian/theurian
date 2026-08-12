"""What a run that stopped partway says it wrote (#47, FR-L2, §6.4).

``changed_paths`` is read as *the files this run wrote*, and the interesting
cases are the two either side of a step that raised. An apply is not atomic:
``FileSecretStore.set`` creates the token file with ``O_CREAT``, writes the
secret into it, and only then tightens its mode -- so a failure after the write
leaves a live credential on disk that the operator has to be told about. A
failure before the write leaves nothing, and naming the file anyway sends them
looking for something that is not there.

Listing a step's declared paths only when it *finished* gets the first case
wrong. Listing them unconditionally, before the apply is attempted, gets the
second one wrong -- and that is indistinguishable from the first mistake unless
a test drives both. So both are here, and neither is meaningful without the
other:

- a critical step that wrote its artefact and then raised, driven through the
  real ``FileSecretStore`` by failing the ``chmod`` that follows the write;
- a critical step that raised before writing, driven through a service manager
  whose ``install`` refuses, leaving its declared definition file absent.

The third case is the one no redirection protects against: a ``HOME`` its owner
cannot write to. Nothing is created, so nothing may be claimed, and the run has
to say which path refused it.

**And the fourth is what the first fix for those got wrong.** It answered "did
this run write the file?" with "is the file there now?", on the argument that a
step only reaches an apply while its probe says ``MISSING`` -- so anything
present afterwards must have been created by this run. ``MISSING`` means *not as
setup wants it*, which is not the same as absent, and four shipped steps reach
their apply with the declared path already sitting there: a 0755 ``~/.theurian``
being tightened, a *directory* at ``auth/mcp-token``, an env file whose contents
differ (#128), and ``~/.claude.json``, which exists whenever Claude Code is on
PATH and which ``claude mcp add`` leaves byte-identical when it refuses. Each was
published as a file this run wrote. The credential row is the one with teeth: the
plugin reads ``changedPaths`` and tells the operator to rotate what it names, and
there was no credential.

So the question is answered by *provenance* -- what the path looked like
immediately before this step's apply, against what it looks like now -- and the
tests below drive both answers plus the two edges the comparison introduces: a
write that is only a mode, and a path that stops being statable while the apply
is running. The last one is disclosed *because* the check could not tell, which
is the one arm where naming a path setup did not write is the correct answer.

Real files under a temporary root, fake collaborators. Installing a real
LaunchAgent would register it in the developer's own login session, which no
amount of ``HOME`` redirection prevents.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, override

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import Step
from theurian.domain.setup import SetupState, SetupStep, StepId, StepOutcome, StepStatus
from theurian.infrastructure.claude.mcp_config import ClaudeCodeMcpConfig, ConnectionSpec
from theurian.infrastructure.secrets.file_store import TOKEN_KEY, FileSecretStore
from theurian.infrastructure.services.runner import CommandResult
from theurian.security.paths import ensure_private_mode

pytestmark = pytest.mark.integration

PORT = 7419

#: POSIX permission bits are what the read-only ``HOME`` case turns on, and they
#: do not refuse root. Skipped rather than adapted: a run as root would create
#: the directory, converge, and pass every prohibition below while testing
#: nothing.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


def _context(tmp_path: Path, **overrides: Any) -> SetupContext:
    """A machine where nothing is set up yet, and no daemon is running.

    Deliberately local rather than shared with ``test_setup_service.py``: every
    test here needs one collaborator replaced by something that fails, and the
    executable lives outside ``home`` so that "nothing was created under HOME" is
    a statement about setup rather than about the fixture.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_dir = home / ".theurian"
    # Popped with a default rather than coalesced with ``or``: an explicit
    # ``service=None`` means "this platform has no service manager", and turning
    # that back into a ``FakeService`` would leave a test silently exercising the
    # arm it was written to avoid.
    service = overrides.pop("service", FakeService())
    # 0755 and a real script: `probe_core` requires an absolute path that
    # resolves *and* can be started, and a 0644 file makes `core-present`
    # conflict -- which aborts the run before any apply, so every test below
    # would be asserting about a report that never reached a step (#49).
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
        "health": lambda: (
            {"dataDir": str(data_dir)} if getattr(service, "started", False) else None
        ),
        "service": service,
        "executable": str(executable),
    }
    return SetupContext(**{**defaults, **overrides})


# -- A critical step that wrote its artefact and then raised ------------------


def _fail_the_chmod_that_follows_the_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``FileSecretStore.set`` fail *after* the secret reaches the disk.

    The seam is the one the real code has: ``set`` opens the file with
    ``O_CREAT``, writes the token, and then calls ``ensure_private_mode`` to
    tighten it. Refusing that last call reproduces the shape of a real partial
    apply -- a filesystem that will not take a ``chmod``, an interrupted
    process -- without inventing a failure mode the code does not have.

    Only the call on the *file* is refused. The same function is called on the
    ``auth/`` directory a few lines earlier, and failing that one would abort
    before anything was created, which is the other test's case.

    Patched where ``file_store`` bound the name, not where it is defined:
    ``from ... import ensure_private_mode`` copies the reference at import time,
    so replacing it in ``theurian.security.paths`` would leave the store calling
    the original. ``monkeypatch`` raises if that attribute ever stops existing,
    which is what keeps this seam from silently becoming a no-op.
    """

    def refuse_once_the_secret_is_written(path: Path, *, mode: int) -> bool:
        if path.is_file():
            raise OSError(errno.EPERM, "Operation not permitted", str(path))
        return ensure_private_mode(path, mode=mode)

    monkeypatch.setattr(
        "theurian.infrastructure.secrets.file_store.ensure_private_mode",
        refuse_once_the_secret_is_written,
    )


def test_a_step_that_wrote_its_artefact_before_failing_still_discloses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#47, SEC-6. The credential on disk is the whole reason ``changed_paths`` exists.

    An apply that raised may already have written its artefact, and for the token
    step that artefact is a live credential: ``FileSecretStore.set`` creates the
    file and writes the secret before the ``chmod`` that can fail. Accumulating
    only the steps that *finished* leaves that file on disk and absent from
    everything the operator reads afterwards, so they cannot rotate or remove
    what they were never told about -- the disclosure defect #47 set out to fix,
    in the arm the first fix did not cover.

    Asserted on the disk *and* the report, because either alone is weak: a report
    naming a path nothing wrote satisfies the second, and a credential on disk
    the report is silent about satisfies the first. The bytes are checked too, so
    an empty file created and abandoned cannot stand in for a written secret.
    """
    context = _context(tmp_path)
    _fail_the_chmod_that_follows_the_write(monkeypatch)

    report = SetupService(context).run(SetupRequest())

    token = context.auth_dir / TOKEN_KEY
    minted = report.step(StepId.TOKEN)
    assert report.state is SetupState.HALTED, "the token step is critical"
    assert minted is not None and minted.outcome is StepOutcome.FAILED, (
        "the fixture has to make the token step raise, not some later one"
    )
    assert token.read_text(encoding="utf-8").strip(), "the secret reached the disk before the raise"
    assert str(token) in report.changed_paths, "so the operator is told the credential is there"


# -- A critical step that raised before writing anything ---------------------


class _RefusesToInstall(FakeService):
    """A service manager that raises instead of writing its definition.

    ``apply_daemon_service`` has one call in it, so a manager that refuses is the
    whole of "this step raised before it wrote". Its declared path is put under
    the test's own temporary root rather than left at ``FakeService``'s
    ``/fake/service.plist``: the assertion below is that the file does not exist,
    and that must be a fact about this run rather than about the developer's
    filesystem.
    """

    def __init__(self, plist_path: Path) -> None:
        super().__init__()
        self.plist_path = plist_path

    @override
    async def install(self, *, port: int, data_directory: str) -> None:
        raise OSError(errno.EACCES, "Permission denied", str(self.plist_path))


def test_a_step_that_failed_before_writing_does_not_claim_the_file_it_never_made(
    tmp_path: Path,
) -> None:
    """#47. ``changed_paths`` names files, not intentions.

    A failed step still carries the ``paths`` its probe declared -- that is what
    the plan promised to write -- so publishing a failed step's declarations
    unconditionally is the easy way to make the test above pass, and it announces
    a service definition that was never created. The operator then goes looking
    for a plist to remove, and setup has told them something false about their own
    machine at the moment they are trying to repair it.

    This is the assertion the two possible implementations disagree on: moving the
    accumulation ahead of the apply is indistinguishable from the correct code on
    every other test in the suite, and it was measured as a surviving mutation.

    The last assertion is what stops the prohibition passing on an empty list: the
    steps that ran before this one did write their files, and those are named.
    """
    plist = tmp_path / "LaunchAgents" / "dev.theurian.daemon.plist"
    context = _context(tmp_path, service=_RefusesToInstall(plist))

    report = SetupService(context).run(SetupRequest())

    failed = report.step(StepId.DAEMON_SERVICE)
    assert report.state is SetupState.HALTED, "the daemon-service step is critical"
    assert failed is not None and failed.outcome is StepOutcome.FAILED
    assert failed.paths == (str(plist),), "the step did declare the file it would have written"
    assert not plist.exists(), "and it never got as far as writing it"
    assert str(plist) not in report.changed_paths, "so no run wrote it, and none may claim it"
    assert str(context.env_file) in report.changed_paths, (
        "while the files the earlier steps really did write are still named"
    )


# -- A HOME that refuses to be written to ------------------------------------


@pytest.fixture
def home_that_refuses_writes(tmp_path: Path) -> Iterator[SetupContext]:
    """A ``HOME`` its owner may read and traverse but not write to.

    0500, which is a real state: a mounted-read-only profile, a locked-down
    managed account, a directory somebody tightened by hand. The mode is restored
    on the way out whether or not the test passed, because pytest's own temporary
    directory cleanup cannot remove a tree it may not write to, and that failure
    would surface on some later run rather than on this one.
    """
    context = _context(tmp_path)
    context.home.chmod(0o500)
    try:
        yield context
    finally:
        context.home.chmod(0o700)


@pytest.mark.skipif(
    _CANNOT_BE_REFUSED_BY_A_MODE,
    reason="POSIX permission bits, and root is refused by none of them",
)
def test_a_home_it_cannot_write_to_halts_the_run_and_names_the_path_that_refused(
    home_that_refuses_writes: SetupContext,
) -> None:
    """The first step fails, so this is the halt with nothing behind it (§6.4).

    Every other halt in the suite happens after something was written, which
    leaves the empty-``changed_paths`` arm of the halted return untested: a run
    that could not create ``~/.theurian`` created nothing, journalled nothing --
    ``_journal`` needs that same directory and swallows its ``OSError`` -- and so
    must claim nothing. A report listing a path here would be inventing one.

    The warning is the other half. ``state`` says the run stopped and
    ``changed_paths`` is empty by construction, so the only thing that can tell
    the operator *what* to fix is the sentence naming the directory that refused
    -- and a halt whose warnings said nothing would satisfy every prohibition
    above.
    """
    context = home_that_refuses_writes

    report = SetupService(context).run(SetupRequest())

    assert report.state is SetupState.HALTED
    assert not context.data_dir.exists(), "the data directory could not be created"
    assert list(context.home.iterdir()) == [], "and nothing else was created under HOME either"
    assert report.changed_paths == (), "a run that wrote nothing may not name a file"
    assert any(str(context.data_dir) in warning for warning in report.warnings), (
        "the report has to name the path that refused; nothing else here can"
    )


# -- A declared path that was already there, and did not move ----------------

#: Captured at import, so the refusal below can still perform the real thing for
#: every path it is not refusing.
_real_chmod = Path.chmod


def _refuse_chmod_on(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Make ``Path.chmod`` raise for exactly one path.

    A filesystem that will not take a ``chmod`` -- a read-only mount, a share
    with no POSIX modes, an interrupted process. Only the one path is refused so
    that everything else in the run behaves normally and the failure under test
    is the failure being asserted about.

    ``Path.chmod`` rather than ``os.chmod``: ``apply_data_directory`` calls the
    method, and ``security.paths.ensure_private_mode`` calls the function, so
    patching the wrong one leaves the step under test succeeding.
    """

    def refuse(self: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        if self == target:
            raise OSError(errno.EPERM, "Operation not permitted", str(self))
        _real_chmod(self, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", refuse)


def test_a_directory_the_run_could_not_tighten_is_not_reported_as_one_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#47. The state the step reports ``MISSING`` for is a directory that exists.

    ``~/.theurian`` at 0755 *is* the finding -- "readable by other users, tighten
    it to 0700" -- so the data-directory step reaches its apply with the path
    already there, holding whatever the user put in it. A filesystem that refuses
    the ``chmod`` leaves it exactly as it was, and the existence check that
    shipped first published it among the files this run wrote. An operator
    reading that goes looking for a directory setup created, at the moment they
    are trying to repair a machine setup stopped halfway through.

    The inode and mode are compared across the run, so the prohibition is
    asserted against a directory that demonstrably did not move rather than
    against one this test merely believes did not.

    The last assertion is what stops the prohibition passing on an empty list:
    the runner's own journal *was* written, and a report that named nothing at
    all would satisfy every line above.
    """
    context = _context(tmp_path)
    context.data_dir.mkdir(parents=True)
    context.data_dir.chmod(0o755)
    before = context.data_dir.stat()
    _refuse_chmod_on(monkeypatch, context.data_dir)
    service = SetupService(context)

    report = service.run(SetupRequest())

    failed = report.step(StepId.DATA_DIRECTORY)
    after = context.data_dir.stat()
    assert report.state is SetupState.HALTED, "the data-directory step is critical"
    assert failed is not None and failed.outcome is StepOutcome.FAILED
    assert failed.paths == (str(context.data_dir),), "the step did declare the directory"
    assert (after.st_ino, after.st_mode) == (before.st_ino, before.st_mode), (
        "the refused chmod left the directory exactly as the user had it"
    )
    assert str(context.data_dir) not in report.changed_paths, (
        "so no run wrote it, and none may claim it"
    )
    assert str(service.journal_path) in report.changed_paths, (
        "while the one file this run really did write is still named"
    )


def test_a_credential_that_was_never_minted_is_not_offered_for_rotation(
    tmp_path: Path,
) -> None:
    """#47, SEC-6. The false positive with teeth: a path shaped like a credential.

    A *directory* at ``auth/mcp-token`` makes ``FileSecretStore.get`` raise before
    ``set`` writes anything, so the token step fails with its declared path
    present and no credential anywhere. Publishing it sends the operator -- or
    the plugin, which reads ``changedPaths`` and advises rotating what it names --
    after a secret that does not exist, and `theurian auth rotate` on a directory
    is not a recovery.

    Both halves are asserted: the path is there, so the existence check this
    replaced would have named it, and no file is there, so there is nothing to
    rotate. The data directory is asserted present in the same report, because
    this run genuinely did tighten it -- the prohibition is about provenance, not
    about a report that gave up and said nothing.
    """
    context = _context(tmp_path)
    context.data_dir.mkdir(parents=True)
    context.data_dir.chmod(0o755)
    context.auth_dir.mkdir(parents=True, mode=0o700)
    (context.auth_dir / TOKEN_KEY).mkdir(mode=0o700)

    report = SetupService(context).run(SetupRequest())

    token = context.auth_dir / TOKEN_KEY
    failed = report.step(StepId.TOKEN)
    assert report.state is SetupState.HALTED, "the token step is critical"
    assert failed is not None and failed.outcome is StepOutcome.FAILED
    assert "IsADirectoryError" in failed.detail, "the store's read is what raised, before any write"
    assert token.is_dir() and not token.is_file(), (
        "the path exists, so an existence check names it, and it is not a credential"
    )
    assert str(token) not in report.changed_paths, "nothing may be offered for rotation"
    assert str(context.data_dir) in report.changed_paths, (
        "while the directory this run really did tighten is still named"
    )


class _ClaudeThatRefuses:
    """``claude`` is on PATH; ``claude mcp add`` fails and writes nothing.

    The shape the real CLI has when it cannot reach its config store: a non-zero
    exit and an untouched ``~/.claude.json``. Nothing here writes the file,
    which is the point -- :class:`ClaudeCodeMcpConfig` never writes it either,
    and that is the claim the test around this is protecting.
    """

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = 20.0,  # noqa: ARG002 - part of the CommandRunner protocol
        env: Mapping[str, str] | None = None,  # noqa: ARG002 - same
    ) -> CommandResult:
        self.commands.append(list(args))
        return CommandResult(exit_code=1, stderr="Error: could not connect to config store")

    def which(self, executable: str) -> str | None:
        return "/usr/local/bin/claude" if executable == "claude" else None


def test_a_config_theurian_never_writes_is_not_claimed_when_claude_refuses(
    tmp_path: Path,
) -> None:
    """#47, ADR-0012. Theurian reads ``~/.claude.json`` and never writes it.

    :mod:`theurian.infrastructure.claude.mcp_config` opens by saying so: every
    write to that file is delegated to ``claude mcp add``, because it is Claude
    Code's live state and Claude Code may be running. So a report naming it among
    the files *this* run wrote contradicts the product's own account of itself --
    and it did, on every machine with Claude Code installed, because the file is
    always there and the existence check could not tell the difference.

    Driven through the real :class:`ClaudeCodeMcpConfig` with a fake ``claude``,
    rather than through a fake config: what is being asserted is that the adapter
    left the bytes alone, which a fake cannot demonstrate. The bytes are compared
    before and after for the same reason.

    ``mcp-connection`` is not critical, so this is also the non-halting arm of the
    same rule -- the provenance check runs in the failure branch whatever the
    step's criticality, and a run that ends DEGRADED publishes ``changed_paths``
    from `_verify` rather than from the halted return.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    runner = _ClaudeThatRefuses()
    config = ClaudeCodeMcpConfig(home=home, runner=runner)
    config.path.write_text(
        json.dumps({"mcpServers": {}, "hasCompletedOnboarding": True}) + "\n", encoding="utf-8"
    )
    before = config.path.read_bytes()
    context = _context(tmp_path, mcp_config=config)

    report = SetupService(context).run(SetupRequest())

    failed = report.step(StepId.MCP_CONNECTION)
    assert report.state is SetupState.DEGRADED, "a failing optional step degrades, it does not halt"
    assert failed is not None and failed.outcome is StepOutcome.FAILED
    assert failed.paths == (str(config.path),), "the step did declare the config"
    assert runner.commands[-1][1:3] == ["mcp", "add"], "`claude mcp add` was the call that failed"
    assert config.path.read_bytes() == before, "and it left the file byte-identical"
    assert str(config.path) not in report.changed_paths, (
        "so a file Theurian never writes may not be named among the files it wrote"
    )
    assert str(context.env_file) in report.changed_paths, (
        "while the files the steps before it really did write are still named"
    )


# -- The two edges the comparison introduces ---------------------------------


def _step_over(artefact: Path, apply: Any) -> Step:
    """A one-step plan whose declared path is ``artefact``.

    Written here rather than borrowed from ``setup_steps``, because both cases
    below need an apply that fails *after* doing something specific, and no
    shipped step can be driven into either: ``apply_data_directory``'s ``chmod``
    is its last statement, and no apply locks its own parent. The subject is the
    runner's comparison, not the step, so a synthetic step is the honest fixture
    -- and it is run through the real :class:`SetupService`, not around it.
    """

    def probe(_: SetupContext) -> SetupStep:
        return SetupStep(
            step_id=StepId.DATA_DIRECTORY,
            status=StepStatus.MISSING,
            summary=f"{artefact} is not as setup wants it.",
            action=f"Write {artefact}.",
            paths=(str(artefact),),
        )

    return Step(StepId.DATA_DIRECTORY, probe, apply)


def test_a_step_that_changed_only_a_mode_before_failing_still_discloses_its_artefact(
    tmp_path: Path,
) -> None:
    """#47. ``st_mode`` is in the signature because a whole shipped write *is* a mode.

    Tightening an existing 0755 ``~/.theurian`` to 0700 moves no bytes, no size
    and no mtime: a comparison blind to permissions cannot see that step happen
    at all, and would report the one arm of the data-directory step that changes
    a live directory as having written nothing. The same applies to
    ``FileSecretStore.set`` re-tightening a token file it did not have to
    rewrite.

    The assertion that makes this the ``st_mode`` pin rather than a general
    disclosure test is the one comparing everything else: inode, size and mtime
    are unchanged across the run, so the *only* field that can carry this
    disclosure is the mode.
    """
    context = _context(tmp_path)
    artefact = tmp_path / "artefact"
    artefact.write_text("bytes that do not move\n", encoding="utf-8")
    artefact.chmod(0o644)
    before = artefact.stat()

    def tighten_then_fail(_: SetupContext) -> None:
        artefact.chmod(0o600)
        raise OSError(errno.EPERM, "Operation not permitted", str(artefact))

    report = SetupService(context, steps=(_step_over(artefact, tighten_then_fail),)).run(
        SetupRequest()
    )

    after = artefact.stat()
    assert report.state is SetupState.HALTED, "the step is critical"
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ), "nothing but the mode moved, so nothing but st_mode can carry this"
    assert after.st_mode & 0o777 == 0o600, "and the mode did move"
    assert str(artefact) in report.changed_paths, (
        "a file this run tightened is a file this run wrote"
    )


@pytest.mark.skipif(
    _CANNOT_BE_REFUSED_BY_A_MODE,
    reason="POSIX permission bits, and root is refused by none of them",
)
def test_a_path_that_stops_being_statable_is_disclosed_rather_than_assumed_untouched(
    tmp_path: Path,
) -> None:
    """#47. "Nothing was written" and "I could not look" are different answers.

    A comparison has a third outcome the existence check did not: the check
    itself can fail. EACCES on a parent, ELOOP, ENAMETOOLONG -- and here, an
    apply whose own failure takes the directory above its artefact with it. The
    run cannot then say the path is untouched, so it names it, and the bias
    toward disclosure is deliberate: a spurious path costs the operator a look,
    and a missed one costs them a credential they never hear about.

    **This is also the arm that must not raise.** The comparison runs inside the
    ``except`` that assembles the halted report, and nothing between there and
    ``setup_command`` catches anything; ``Path.exists`` -- which stood here --
    re-raises EACCES. So the assertion that the state is HALTED is not
    scaffolding for the one below it. It is the difference between the report an
    operator repairs their machine from and a traceback.

    The file is asserted unchanged at the end: the run did *not* write it, and it
    is disclosed anyway, which is the whole content of the unknown arm.
    """
    context = _context(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    artefact = locked / "artefact"
    artefact.write_text("present when the step was planned\n", encoding="utf-8")

    def lock_the_parent_then_fail(_: SetupContext) -> None:
        locked.chmod(0o000)
        raise OSError(errno.EIO, "Input/output error", str(artefact))

    service = SetupService(context, steps=(_step_over(artefact, lock_the_parent_then_fail),))

    try:
        report = service.run(SetupRequest())
        with pytest.raises(PermissionError):
            artefact.stat()  # the apply really did make it unobservable
    finally:
        locked.chmod(0o700)

    assert report.state is SetupState.HALTED, "a report an operator can act on, not a traceback"
    assert str(artefact) in report.changed_paths, (
        "a path setup could not look at is named, because silence would be a claim"
    )
    assert artefact.read_text(encoding="utf-8") == "present when the step was planned\n", (
        "and it is named without having been written, which is what this arm is for"
    )


def test_a_write_through_a_symlinked_declaration_is_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#47. The observation follows links, because every apply here does.

    ``apply_env_reference`` opens with ``O_TRUNC`` and ``apply_data_directory``,
    ``FileSecretStore.set`` and ``claude mcp add`` all write *through* a symlink
    rather than replacing one. A home directory kept in a dotfiles repository,
    where these files are links into it, is an ordinary machine -- and watching
    the link instead of what it points at reports every such write as "nothing
    happened", which is the silence #47 exists to end arriving from the other
    side.

    Measured on the link, which is what the step declares: writing the target
    leaves the link's own inode, size and mtime untouched, so this passes under
    ``os.stat`` and fails under ``os.lstat``. The target's bytes are asserted
    rewritten first, so the disclosure is being compared against a write that
    really happened.
    """
    context = _context(tmp_path)
    target = tmp_path / "dotfiles" / "env"
    target.parent.mkdir(parents=True)
    target.write_text("export THEURIAN_MCP_TOKEN=an-older-spelling\n", encoding="utf-8")
    context.data_dir.mkdir(parents=True, mode=0o700)
    context.env_file.symlink_to(target)
    link = context.env_file.lstat()
    _refuse_chmod_on(monkeypatch, context.env_file)

    report = SetupService(context).run(SetupRequest())

    failed = report.step(StepId.ENV_REFERENCE)
    assert report.state is SetupState.HALTED, "the env-reference step is critical"
    assert failed is not None and failed.outcome is StepOutcome.FAILED
    assert "an-older-spelling" not in target.read_text(encoding="utf-8"), (
        "the apply wrote through the link before its chmod raised"
    )
    after = context.env_file.lstat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        link.st_ino,
        link.st_size,
        link.st_mtime_ns,
    ), "while the link itself did not move, which is what lstat would have seen"
    assert str(context.env_file) in report.changed_paths, (
        "so the declared path is disclosed on the strength of what it points at"
    )
