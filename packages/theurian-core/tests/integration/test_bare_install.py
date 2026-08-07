"""What the CLI does on an install without the ``daemon`` extra (#78, ADR-0014).

`uv tool install theurian` is the command every install surface names, and it
installs a Theurian whose ``daemon`` extra is absent. Measured against the real
PyPI artifact ``0.1.0.dev0``: both ``theurian daemon start`` and
``theurian daemon status`` ended in a Rich traceback and
``ModuleNotFoundError: No module named 'uvicorn'``.

The two are not the same defect and do not get the same fix:

- ``daemon start`` genuinely needs the extra. It cannot do its job, so it says
  so, names the extra and gives the command -- the codebase's rule that a raised
  message names the command that fixes it.
- ``daemon status`` never needed the extra. It reached into
  ``daemon/runner.py`` for a filename constant and paid ``import uvicorn`` for
  it. **This is the one the SessionStart hook runs on every session**, so a bare
  install did not merely fail to start a daemon -- it printed a traceback into
  every Claude Code session on the machine. Answering it with a nicer error
  would have been the wrong fix; it has a correct answer to give.

The development environment always has the extra, so absence is simulated by a
meta-path finder rather than by uninstalling anything. That makes these tests a
statement about the *import graph*, which is what actually broke: the
constant's module, not the constant.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Final, override

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from typer.testing import CliRunner

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import probe_core
from theurian.cli.main import app
from theurian.domain.extras import DAEMON_EXTRA, DAEMON_MODULES
from theurian.domain.setup import SetupState, SetupStep, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

runner: Final = CliRunner()

#: Theurian modules that reach the extra, and so must be re-imported under the
#: block rather than served from ``sys.modules`` by an earlier test.
_TAINTED_PREFIXES: Final = ("theurian.daemon.runner", "theurian.daemon.server", "theurian.mcp")


def _covered_by(name: str, blocked: frozenset[str]) -> str | None:
    """The blocked entry ``name`` falls under, matching whole dotted segments.

    ``startswith`` on the bare string would make blocking ``mcp`` also block
    ``mcpx``, so the test would pass for a reason it does not state. One
    predicate serves both the finder and the ``sys.modules`` purge on purpose:
    when they disagree, the purge leaves the module cached, the finder is never
    consulted, and the block silently does nothing -- which is how two of these
    tests first went red against unmodified code.
    """
    return next((entry for entry in blocked if name == entry or name.startswith(f"{entry}.")), None)


class _Blocker(importlib.abc.MetaPathFinder):
    """Refuses a fixed set of modules the way a bare install does.

    Raises out of ``find_spec`` rather than returning ``None``: returning
    ``None`` means "not mine, ask the next finder", which is how the real
    installed package would then be found and the block would do nothing.
    """

    def __init__(self, blocked: frozenset[str]) -> None:
        self._blocked = blocked

    @override
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        entry = _covered_by(fullname, self._blocked)
        if entry is not None:
            raise ModuleNotFoundError(f"No module named {entry!r}", name=fullname)
        return None


@contextmanager
def _without(*modules: str) -> Iterator[None]:
    """Make ``modules`` unimportable, and anything already importing them stale.

    ``sys.modules`` is restored key by key rather than by ``clear()`` and
    ``update()``: a live reference to the dict is held by the import system
    itself, and emptying it even briefly is how a suite that passes alone starts
    failing in a full run.
    """
    blocked = frozenset(modules)
    saved = dict(sys.modules)
    for name in list(sys.modules):
        if _covered_by(name, blocked) is not None or name.startswith(_TAINTED_PREFIXES):
            del sys.modules[name]

    finder = _Blocker(blocked)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name in list(sys.modules):
            if name not in saved:
                del sys.modules[name]
        sys.modules.update(saved)


def _output(result: object) -> str:
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", None) or ""
    return stdout + stderr


def test_daemon_start_names_the_extra_and_the_command_that_installs_it() -> None:
    """The reported defect, at the surface that reports it.

    Three things have to be true at once, and the shipped behaviour had none of
    them: a non-zero exit, prose that names ``daemon`` rather than ``uvicorn``,
    and a command the reader can run.
    """
    with _without(*DAEMON_MODULES):
        result = runner.invoke(app, ["daemon", "start"])

    text = _output(result)
    assert result.exit_code == 1, text
    assert "daemon" in text
    assert "uv tool install 'theurian[daemon]'" in text
    assert "pipx install --force 'theurian[daemon]'" in text


def test_daemon_start_does_not_hand_the_user_a_traceback() -> None:
    """`ModuleNotFoundError` reaching a user is the defect, not its symptom.

    Pinned separately from the message above because the two fail apart: a
    remedy printed *before* an unhandled exception still satisfies every
    substring check, and that is precisely what a guard placed one line too late
    produces.
    """
    with _without(*DAEMON_MODULES):
        result = runner.invoke(app, ["daemon", "start"])

    text = _output(result)
    assert "ModuleNotFoundError" not in text, text
    assert "Traceback" not in text, text
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception


def test_daemon_start_says_it_on_the_json_channel_too() -> None:
    """`--json` is a published contract (CP-2); a traceback is not JSON.

    The plugin reads this. A failure that arrives as Rich-rendered text is
    unparseable at exactly the moment the caller most needs to know why.
    """
    with _without(*DAEMON_MODULES):
        result = runner.invoke(app, ["daemon", "start", "--json"])

    payload = json.loads(_output(result))
    assert payload["error"]
    assert "uv tool install 'theurian[daemon]'" in payload["remedy"]


def test_daemon_status_answers_normally_without_the_extra() -> None:
    """The command the SessionStart hook runs on every session.

    ``daemon status`` wanted one string -- the lock file's name -- and imported
    it from the module that starts a web server. Nothing about reporting
    "no daemon is running" requires ``uvicorn``, so the fix is that the constant
    moved to ``daemon/instance.py`` beside the lock it names, not that the
    failure got a better message.
    """
    with _without(*DAEMON_MODULES):
        result = runner.invoke(app, ["daemon", "status", "--json"])
        # Inside the block: importing this is itself the claim being made, since
        # `daemon status` reaches it on a bare install and must find it there.
        from theurian.daemon.instance import LOCK_FILENAME

    text = _output(result)
    assert result.exit_code == 0, text
    payload = json.loads(text)
    assert payload["listening"] is False
    assert Path(payload["lockFile"]).name == LOCK_FILENAME


def test_a_theurian_module_that_fails_to_import_is_not_blamed_on_the_extra() -> None:
    """A broken Theurian must not send the user to reinstall a working package.

    The guard reads ``ModuleNotFoundError.name``. Without that check it would
    catch every import failure inside ``daemon/runner.py`` -- including one
    caused by a file *inside the wheel the remedy tells them to install again* --
    and turn a Theurian bug into a user's afternoon.
    """
    with _without("theurian.security"), pytest.raises(ModuleNotFoundError) as caught:
        runner.invoke(app, ["daemon", "start"], catch_exceptions=False)

    assert caught.value.name is not None
    assert caught.value.name.startswith("theurian.")


def test_the_lock_file_name_is_written_once_in_the_source() -> None:
    """Moving the constant must not fork it.

    ``daemon status`` publishes this path as ``lockFile`` and ``runner.serve``
    creates the file; two spellings make the reported path a lie on the one
    machine where it matters. Counted by searching the tree rather than by
    reading the two modules, because the way this forks is a third caller
    writing the literal inline -- which no assertion about these two would see.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "theurian"
    occurrences = [
        str(path.relative_to(src))
        for path in sorted(src.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if '"daemon.lock"' in line
    ]

    assert occurrences == ["daemon/instance.py"], occurrences


def _existing_executable(tmp_path: Path) -> str:
    """Runnable, not merely present.

    `probe_core` requires a path a service manager could exec. `touch()` leaves
    0644, which reaches the step's *path* arm -- so these tests would report the
    conflict they are looking for without ever consulting the extra (#49).
    """
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    return str(executable)


def _setup_context(tmp_path: Path, *, executable: str) -> SetupContext:
    """A machine with nothing set up, outside a repository, no daemon running."""
    data_dir = tmp_path / "home" / ".theurian"
    return SetupContext(
        home=tmp_path / "home",
        data_dir=data_dir,
        port=7419,
        project_root=None,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable=executable,
    )


def _core_present(tmp_path: Path) -> SetupStep:
    """The ``core-present`` step against an executable that really exists.

    A path that does not resolve reaches the step's *other* conflicting arm, so a
    fixture that skipped this would report the abort it was looking for and
    prove nothing about the extra.
    """
    return probe_core(_setup_context(tmp_path, executable=_existing_executable(tmp_path)))


def test_core_present_is_not_satisfied_by_a_core_whose_daemon_cannot_start(
    tmp_path: Path,
) -> None:
    """The step reported `satisfied` for the install this whole file is about.

    It checked that a file exists at a path. Everything after it in the plan --
    the service unit, the daemon, the MCP connection, its health check -- is
    about a daemon that this Core cannot run, so `satisfied` was not a small
    overstatement: it was the last chance to say so before setup registered a
    launchd job that fails on every start.
    """
    with _without(*DAEMON_MODULES):
        step = _core_present(tmp_path)

    assert step.status is StepStatus.CONFLICTING
    assert DAEMON_EXTRA in step.summary
    assert "uv tool install 'theurian[daemon]'" in step.detail


def test_core_present_is_satisfied_when_the_extra_is_there(tmp_path: Path) -> None:
    """The other half, or the step above would pass by always conflicting."""
    step = _core_present(tmp_path)

    assert step.status is StepStatus.SATISFIED


def test_a_missing_extra_looks_the_same_when_find_spec_answers_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real bare install and this file's blocker fail differently.

    ``importlib.util.find_spec`` *returns* ``None`` for a module that was never
    installed, and *raises* for one this file's meta-path finder refuses. Only
    the first is what a user has, so the probe is checked against it directly
    rather than trusted to the simulation everywhere else here.
    """
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, package=None: None)  # noqa: ARG005

    step = _core_present(tmp_path)

    assert step.status is StepStatus.CONFLICTING
    assert DAEMON_EXTRA in step.summary


def test_setup_stops_before_registering_a_service_that_cannot_start(tmp_path: Path) -> None:
    """`core-present` is one of the two conflicts consent cannot buy past.

    That set exists for steps that "leave nothing worth installing around
    them", and a Core with no daemon is exactly that: every remaining step
    either serves the daemon or connects a client to it. Reporting the run
    ABORTED and writing nothing is better than a DEGRADED run that leaves a
    registered launchd job crash-looping and a user to work out why.
    """
    context = _setup_context(tmp_path, executable=_existing_executable(tmp_path))

    with _without(*DAEMON_MODULES):
        report = SetupService(context).run(SetupRequest())

    assert report.state is SetupState.ABORTED
    assert report.changed_paths == ()
    assert not context.data_dir.exists()
    assert any(warning.startswith("core-present:") for warning in report.warnings)


def test_the_lock_filename_module_is_free_of_the_extra() -> None:
    """The property the status fix rests on, stated as a test.

    ``instance.py`` may not grow an import of the extra, or ``daemon status``
    silently returns to crashing on a bare install and only a real PyPI install
    would show it.
    """
    source = (
        Path(__file__).resolve().parents[2] / "src" / "theurian" / "daemon" / "instance.py"
    ).read_text(encoding="utf-8")

    for module in DAEMON_MODULES:
        assert f"import {module}" not in source, f"daemon/instance.py imports {module}"
