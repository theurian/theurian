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

Real files under a temporary root, fake collaborators. Installing a real
LaunchAgent would register it in the developer's own login session, which no
amount of ``HOME`` redirection prevents.
"""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any, override

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.domain.setup import SetupState, StepId, StepOutcome
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import TOKEN_KEY, FileSecretStore
from theurian.security.paths import ensure_private_mode

pytestmark = pytest.mark.integration

PORT = 7419


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
    service = overrides.pop("service", None) or FakeService()
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
        "health": lambda: {"dataDir": str(data_dir)} if service.started else None,
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
