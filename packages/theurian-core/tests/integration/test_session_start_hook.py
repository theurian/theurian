"""What the SessionStart hook does when Theurian is degraded (FR-L4, NFR-2, §8).

``session-start.sh`` opens with a promise: *"Exits 0 unconditionally -- a
degraded Theurian must never block a session from starting."* The exit code is
not cosmetic. Claude Code runs this hook on every session, and a non-zero exit
is how a hook reports that the session must not proceed -- so a user whose Core
is a version too old, or whose ``compatibility.yaml`` failed to ship, loses the
editor rather than the feature.

**The promise was false when this file was written, and the test that claimed to
hold it asserted that the script's last line reads ``exit 0``.** It does, and
the script still exited 3: ``lib.sh`` set ``-e`` into the caller's shell, so the
bare assignment ``verdict="$(theurian::compat_check ...)"`` killed the script
before the exit-code capture on the next line could run. Four of the seven rows
below exited non-zero, and the one branch written to explain the problem to the
user printed nothing at all.

So these tests run the real script, in a real subprocess, and read its real exit
code. Everything the hook shells out to is a recording stub, for two reasons:
the daemon probe would otherwise reach ``127.0.0.1:7419`` on the developer's own
machine, and the stub log is what proves the hook stopped where it says it
stops. ``compat check`` alone is forwarded to the installed binary -- the
verdict is Core's to compute (§34), and a hand-written fake verdict would test
this file's idea of Core rather than Core.
"""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Iterator

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO_ROOT / "plugins" / "claude-code"
HOOK = PLUGIN / "scripts" / "session-start.sh"
LIB = PLUGIN / "scripts" / "lib.sh"


def _installed_cli() -> pathlib.Path | None:
    """The real ``theurian`` executable, or ``None`` if Core is not installed.

    Prefers the interpreter's own ``bin/`` over ``PATH`` so that ``uv run
    pytest`` finds the workspace build rather than whatever an earlier
    ``uv tool install`` left on the developer's ``PATH``. Testing one Core while
    reporting on another is the failure this ordering avoids.
    """
    sibling = pathlib.Path(sys.executable).parent / "theurian"
    if sibling.is_file():
        return sibling
    found = shutil.which("theurian")
    return pathlib.Path(found) if found else None


THEURIAN = _installed_cli()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed"),
]

#: Directories the hook's own tooling lives in. ``PATH`` is rebuilt from these
#: plus the stub directory, so nothing the developer happens to have installed
#: can answer for ``theurian`` or ``curl``.
_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

#: A declaration naming a Core range that the Core in this workspace satisfies.
#: The lower bound is pre-release-tolerant for the reason the shipped
#: ``compatibility.yaml`` gives: Core reports PEP 440 versions such as
#: ``0.1.0.dev0``, which sort *before* ``0.1.0``.
_COMPATIBLE_DECLARATION = """pluginVersion: 0.1.0
coreCompatibility:
  minimum: 0.0.1-dev.0
  maximumExclusive: 999.0.0
protocolVersion: theurian/v1
"""

#: A well-formed declaration that no Core in this century satisfies. Produces
#: exit 3 from ``compat check`` -- the code the hook is written to handle.
_INCOMPATIBLE_DECLARATION = """pluginVersion: 0.1.0
coreCompatibility:
  minimum: 999.0.0
  maximumExclusive: 1000.0.0
protocolVersion: theurian/v1
"""

#: Versions ``Version.parse`` refuses, so ``compat check`` exits 2 before it has
#: a verdict to report. Reachable in the field from a hand-edited plugin or a
#: half-applied update.
_UNPARSEABLE_DECLARATION = """pluginVersion: not-a-version
coreCompatibility:
  minimum: not-a-version
  maximumExclusive: not-a-version
protocolVersion: theurian/v1
"""


@dataclasses.dataclass(frozen=True)
class _Scenario:
    """One state Theurian can be in when a session starts.

    ``declaration`` of ``None`` means no ``compatibility.yaml`` exists at all;
    ``cli`` of ``"absent"`` means no ``theurian`` on ``PATH``.
    """

    declaration: str | None
    cli: str  # "core" | "core-without-compat" | "absent"
    daemon_reachable: bool


#: Every row exits 0. That is the whole promise, and it is why they are one
#: table rather than seven tests: a future branch added to ``main`` has to
#: appear here, not merely avoid breaking whichever branch a reader thought of.
_SCENARIOS: dict[str, _Scenario] = {
    # The ordinary case, run to the end of the script.
    "everything-healthy": _Scenario(_COMPATIBLE_DECLARATION, "core", True),
    # Healthy versions, no daemon listening.
    "daemon-unreachable": _Scenario(_COMPATIBLE_DECLARATION, "core", False),
    # `compat check` exits 3. The branch the hook was written for.
    "core-incompatible": _Scenario(_INCOMPATIBLE_DECLARATION, "core", True),
    # `compat check` exits 2 on input it cannot parse.
    "declaration-unparseable": _Scenario(_UNPARSEABLE_DECLARATION, "core", True),
    # A Core old enough to predate `theurian compat`: exits 2, usage on stderr.
    "core-without-compat-command": _Scenario(
        _COMPATIBLE_DECLARATION, "core-without-compat", True
    ),
    # `compat check` is handed four empty strings, and exits 2.
    "declaration-missing": _Scenario(None, "core", True),
    # Core is not installed. The one degraded state the hook already survived.
    "core-not-installed": _Scenario(_COMPATIBLE_DECLARATION, "absent", True),
}


@dataclasses.dataclass(frozen=True)
class _HookRun:
    """What one execution of the hook did."""

    exit_code: int
    stdout: str
    stderr: str
    #: Every command the hook shelled out to, in call order, narrowed to the
    #: tool and its subcommand. See :func:`_invocations`.
    invocations: tuple[str, ...]
    #: The ``HOME`` the hook ran with, so a test can assert it stayed empty.
    home: pathlib.Path


def _write_executable(path: pathlib.Path, script: str) -> None:
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _theurian_stub(cli: str, real: pathlib.Path) -> str:
    """A recording ``theurian`` that forwards only ``compat check``.

    ``core-without-compat`` forwards to the real binary under a command name it
    does not have. That is deliberate rather than a hand-written error: what an
    older Core does with ``compat check`` is whatever Typer does with an unknown
    command, and asserting on a fake of it would pin this file's guess at Typer's
    exit code instead of Typer's.
    """
    forward = {
        "core": f'exec "{real}" "$@"',
        "core-without-compat": f'shift; exec "{real}" no-such-command "$@"',
    }[cli]
    return f"""#!/usr/bin/env bash
printf 'theurian %s\\n' "$*" >>"$THEURIAN_STUB_LOG"
if [ "${{1:-}}" = "compat" ] && [ "${{2:-}}" = "check" ]; then
  {forward}
fi
case "${{1:-}} ${{2:-}}" in
  "daemon status")  printf '{{"state": "not-installed"}}\\n'; exit 0 ;;
  "project status") printf '{{"registered": true, "indexStale": false}}\\n'; exit 0 ;;
esac
printf 'stub: unexpected command\\n' >&2
exit 127
"""


def _curl_stub(*, reachable: bool) -> str:
    """A recording ``curl`` that never opens a socket.

    The hook probes ``http://127.0.0.1:7419/health``, which on a developer's
    machine is a real Theurian. Stubbing the transport is what keeps these rows
    deterministic and keeps the suite off the developer's own daemon.
    """
    return f"""#!/usr/bin/env bash
printf 'curl %s\\n' "$*" >>"$THEURIAN_STUB_LOG"
exit {0 if reachable else 7}
"""


def _invocations(log: pathlib.Path) -> tuple[str, ...]:
    """Each shelled-out command as ``tool subcommand``, in call order.

    Narrowed on purpose: the full argv carries a temporary directory, and a test
    that pins it would fail for a reason that is not the behaviour. What matters
    is *which* commands ran and *in what order* -- which is how a row proves the
    hook stood down rather than carrying on.
    """
    if not log.exists():
        return ()
    invocations: list[str] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        tokens = line.split()
        invocations.append(" ".join(tokens[:3]) if tokens[0] == "theurian" else tokens[0])
    return tuple(invocations)


def _run_hook(scenario: _Scenario, workspace: pathlib.Path) -> _HookRun:
    """Execute the real hook against ``scenario``, touching nothing outside ``workspace``."""
    assert THEURIAN is not None  # narrowed by the module-level skipif

    plugin_root = workspace / "plugin-root"
    stubs = workspace / "stubs"
    home = workspace / "home"
    for directory in (plugin_root, stubs, home):
        directory.mkdir()

    if scenario.declaration is not None:
        (plugin_root / "compatibility.yaml").write_text(scenario.declaration, encoding="utf-8")
    if scenario.cli != "absent":
        _write_executable(stubs / "theurian", _theurian_stub(scenario.cli, THEURIAN))
    _write_executable(stubs / "curl", _curl_stub(reachable=scenario.daemon_reachable))

    log = workspace / "invocations.log"
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(HOOK)],
        env={
            "PATH": f"{stubs}:{_SYSTEM_PATH}",
            "HOME": str(home),
            "THEURIAN_DATA_DIR": str(workspace / "data"),
            "CLAUDE_PLUGIN_ROOT": str(plugin_root),
            "THEURIAN_STUB_LOG": str(log),
            "LC_ALL": "C",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    return _HookRun(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        invocations=_invocations(log),
        home=home,
    )


@pytest.fixture
def workspace(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """A directory the hook may do anything it likes inside, and nowhere else."""
    root = tmp_path / "session"
    root.mkdir()
    yield root


# -- The promise -----------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_a_degraded_theurian_never_blocks_a_session(name: str, workspace: pathlib.Path) -> None:
    """Every state Theurian can be in leaves the hook exiting 0 (FR-L4, §8).

    A SessionStart hook's exit code is the session's veto. Four of these rows
    exercised it: an incompatible Core, an unparseable declaration, a Core too
    old to have ``compat``, and a missing ``compatibility.yaml`` each ended the
    hook at ``verdict="$(theurian::compat_check ...)"`` with the CLI's own exit
    code, because ``lib.sh`` had switched ``errexit`` on in a script that
    deliberately did not.

    This assertion may not be relaxed for any row. "Degraded Theurian" is
    exactly the population the promise is about -- a healthy one blocking
    nothing was never in doubt.
    """
    scenario = _SCENARIOS[name]

    run = _run_hook(scenario, workspace)

    assert run.exit_code == 0, (
        f"the SessionStart hook exited {run.exit_code} with Theurian in the "
        f"{name!r} state, which vetoes the session. stderr: {run.stderr!r}"
    )


def test_an_incompatible_core_is_named_to_the_user(workspace: pathlib.Path) -> None:
    """Standing down silently is indistinguishable from Theurian being broken.

    §30 forbids the hook from upgrading anything on its own, which makes the
    message the entire remedy: the user is the one who has to act, and cannot
    unless told what happened. Both halves are asserted because they failed
    together -- the branch was unreachable, so neither the warning nor the
    verdict it forwards had ever run.
    """
    run = _run_hook(_SCENARIOS["core-incompatible"], workspace)

    assert "Theurian: plugin and Core versions are incompatible." in run.stderr
    assert '"compatible": false' in run.stderr, (
        f"the hook captured Core's verdict but did not forward it: {run.stderr!r}"
    )


def test_an_incompatible_core_stops_the_hook_before_it_touches_the_daemon(
    workspace: pathlib.Path,
) -> None:
    """§30: stop using Theurian for this session, rather than press on.

    The exit-code assertion above cannot see the difference between a hook that
    stood down and one that ran to the end and happened to exit 0. The call log
    can: an incompatible verdict must be the last thing that happens.
    """
    run = _run_hook(_SCENARIOS["core-incompatible"], workspace)

    assert run.invocations == ("theurian compat check",)


def test_a_healthy_theurian_says_nothing_at_all(workspace: pathlib.Path) -> None:
    """"A silent hook is a good hook" -- the script's own words, now pinned.

    This runs on every session. A hook that prints something reassuring on each
    one trains the user to ignore the line that eventually matters, and it is
    the only line the incompatible-Core branch has.
    """
    run = _run_hook(_SCENARIOS["everything-healthy"], workspace)

    assert run.stderr == ""
    assert run.stdout == ""


def test_a_healthy_session_start_probes_and_stops(workspace: pathlib.Path) -> None:
    """The hook's whole cost, enumerated (NFR-2: p95 <= 300 ms, hard cap 2 s).

    Three calls, in this order. Pinning the sequence rather than a duration is
    what makes the budget checkable on a loaded CI runner: a fourth probe is
    visible here, and a wall-clock assertion would be a flake.

    ``curl`` being in the list is load-bearing beyond cost. It proves the health
    probe went through the stub rather than opening a socket to whatever is on
    ``127.0.0.1:7419`` -- if the probe is ever reimplemented with another tool,
    this row fails instead of quietly reaching the developer's own daemon.
    """
    run = _run_hook(_SCENARIOS["everything-healthy"], workspace)

    assert run.invocations == ("theurian compat check", "curl", "theurian project status")


def test_a_stopped_daemon_is_not_started_unless_a_service_is_registered(
    workspace: pathlib.Path,
) -> None:
    """§8: the hook may resume a user-approved service, never install one.

    ``daemon status`` reports ``not-installed`` here, so ``daemon start`` is an
    install by another name. The list is asserted whole rather than checked for
    the absence of one string, because the next mutating command someone reaches
    for will not be the one a denylist happened to name.
    """
    run = _run_hook(_SCENARIOS["daemon-unreachable"], workspace)

    assert run.invocations == ("theurian compat check", "curl", "theurian daemon status")
    assert "Theurian: daemon is not running and no service is registered." in run.stderr


def test_the_hook_writes_nothing_into_the_users_home(workspace: pathlib.Path) -> None:
    """FR-L3 and §8: a session-start probe that leaves a file behind is a surprise.

    ``~/.claude.json`` and ``~/Library/LaunchAgents`` are what this is really
    about, and neither can be asserted on directly without reading the
    developer's own machine. An empty ``HOME`` after the run is the property
    that covers both, and it covers the file nobody has thought of yet.
    """
    run = _run_hook(_SCENARIOS["daemon-unreachable"], workspace)

    assert list(run.home.iterdir()) == []


# -- The mechanism ---------------------------------------------------------


def test_sourcing_the_shared_library_leaves_the_callers_shell_options_alone(
    workspace: pathlib.Path,
) -> None:
    """``lib.sh`` is sourced, so every option it sets belongs to somebody else.

    This is the defect above stated as a contract rather than as seven
    behaviours. ``session-start.sh`` chose ``set -uo pipefail`` and deliberately
    omitted ``-e``; ``lib.sh`` then set ``-euo pipefail`` into that same shell
    and overrode the choice from underneath it -- which is invisible at the call
    site, because the line that breaks is three functions and twenty lines away.

    Asserted over all of ``set -o`` rather than over ``errexit`` alone: the next
    library to reach for ``set`` will not necessarily reach for the same flag,
    and a caller that has to read its dependency to know its own shell options
    has no contract at all.
    """
    probe = workspace / "probe.sh"
    _write_executable(
        probe,
        f"""#!/usr/bin/env bash
before="$(set -o)"
. "{LIB}"
after="$(set -o)"
if [ "$before" = "$after" ]; then printf 'unchanged\\n'; else diff <(printf '%s' "$before") <(printf '%s' "$after"); fi
""",
    )

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.stdout == "unchanged\n", (
        f"sourcing lib.sh changed the caller's shell options:\n{completed.stdout}"
    )
