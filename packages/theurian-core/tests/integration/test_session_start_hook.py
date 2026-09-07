"""The SessionStart hook, run as a real process (NFR-2, CP-2, §8).

``plugins/claude-code/scripts/session-start.sh`` opens with a promise: *"Exits 0
unconditionally -- a degraded Theurian must never block a session from
starting."* Claude Code treats a non-zero SessionStart hook as a reason to stop,
so the promise is the whole safety argument for running Theurian's health check
on every session.

Until this module existed, the only thing holding that promise was
``assert script.rstrip().endswith("exit 0")`` -- a check on the file's last
line. It cannot see that ``lib.sh`` re-enables ``errexit`` in the caller's shell
and that the unguarded assignment on the compat-check line therefore kills the
script long before the last line is reached. Both the shipped plugin (0.1.0) and
that assertion were green while an incompatible Core silently blocked sessions.

So these tests run the hook. Each degraded mode gets its own row, because the
mechanism is shared but the exit codes are not, and a fix that rescues only the
mode someone happened to think of is the defect again.

What is faked and why:

* ``theurian`` is a recording stub. ``compat check`` is forwarded to the **real**
  Core binary wherever the row allows it, so the exit codes under test are
  Core's own and not this file's guess about them. Only the "older Core, no
  ``compat`` subcommand" row has to be synthesised, because no build in this
  checkout can produce it. Its ``project status`` answer is parameterized by
  ``status_payload`` (see :func:`_stub_theurian`) rather than hard-wired, so a
  row can put any payload Core's ``_unresolved_status`` can produce in front of
  the hook -- until issue #380, this stub answered the same fixed healthy
  payload for every row, and every row that reached the daemon probe was
  refused there by ``curl`` first (below), so nothing here had ever driven the
  ``project status`` branch of the hook at all.
* ``curl`` reports a refused connection by default -- exit 7, "failed to
  connect to host", what a machine without a running daemon gives anyway --
  but a row that needs to reach the hook's ``project status`` branch installs
  a ``curl`` that succeeds instead (:func:`_make_sandbox`'s ``daemon_healthy``
  flag). Either way it never opens a socket: both stubs answer immediately
  without touching the network.
* ``HOME`` and ``THEURIAN_DATA_DIR`` point into ``tmp_path`` and the environment
  is built from scratch rather than inherited, so nothing here can reach the
  developer's own machine. The real-Core fidelity rows near the bottom of this
  file are the one exception that talks to a real ``theurian`` process instead
  of the stub, and they sandbox the same two variables the same way.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN = REPO_ROOT / "plugins" / "claude-code"

#: The hook resolves ``lib.sh`` next to itself and ``compatibility.yaml`` from
#: ``CLAUDE_PLUGIN_ROOT``, so a sandbox needs exactly these three files.
_HOOK_RELPATH = Path("scripts") / "session-start.sh"
_LIB_RELPATH = Path("scripts") / "lib.sh"
_DECLARATION_RELPATH = Path("compatibility.yaml")

#: Core's published exit code for a compatibility mismatch, mirrored from
#: ``lib.sh``'s ``THEURIAN_EXIT_INCOMPATIBLE``. Repeated rather than imported
#: because the plugin may not import Core (CP-2).
_EXIT_INCOMPATIBLE = 3

_BASH = shutil.which("bash")
_GIT = shutil.which("git")
_REAL_THEURIAN = Path(sys.executable).parent / "theurian"

#: ``curl`` exit 7 is "failed to connect to host", which is what the hook's
#: health probe sees on a machine with no daemon listening. Every row that does
#: not ask :func:`_make_sandbox` for ``daemon_healthy=True`` gets this one, and
#: the hook's own ``if ! theurian::daemon_healthy`` branch answers before
#: ``project`` is ever invoked -- which is why a row exercising the healthy
#: ``project status`` path needs the other stub below instead.
_CURL_STUB = "#!/usr/bin/env bash\nexit 7\n"

#: The health probe succeeding without a real daemon: also exit-code-only, so
#: this opens no socket either.
_CURL_HEALTHY_STUB = "#!/usr/bin/env bash\nexit 0\n"

#: ``project status --json`` for a healthy, registered, up-to-date project --
#: the shape ``_unresolved_status`` never produces, since it never resolved a
#: project at all. The default for every row that does not care about this
#: payload.
_HEALTHY_STATUS_PAYLOAD = '{"registered": true, "indexStale": false}'


@dataclass(frozen=True)
class Sandbox:
    """One prepared invocation of the hook."""

    plugin_root: Path
    env: dict[str, str]
    call_log: Path


def _bash_single_quote(value: str) -> str:
    """Quote *value* as one bash single-quoted word.

    Not ``repr()``: Python switches a repr to double quotes the moment the
    string holds an apostrophe and no double quote, which is not bash syntax
    and would hand a stub script's own ``printf`` a broken argument list. This
    instead closes the quote, emits a literal ``'`` escaped through a
    double-quoted segment, and reopens it -- the standard bash idiom, and the
    one thing that stays correct for arbitrary content, apostrophes included.
    """
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _stub_theurian(
    log: Path, compat_branch: str, status_payload: str = _HEALTHY_STATUS_PAYLOAD
) -> str:
    """A ``theurian`` that records every invocation before answering it.

    ``daemon status`` reports ``not-installed`` and ``project status`` answers
    with *status_payload* verbatim, so every row below takes the same path
    *after* the compat check and up to whichever of those two the hook's own
    branching reaches. That is what makes the recorded call sequence a usable
    statement about how far the hook got.

    ``status_payload`` is single-quoted for bash rather than interpolated
    through this function's own f-string braces (the way the two fixed
    payloads below still are): a row's payload carries attacker-shaped content
    on purpose -- a YAML parser's source snippet, a planted remedy -- and an
    f-string substitution would have to double every literal ``{`` or ``}``
    in it to survive, which a caller composing JSON should never have to
    remember.
    """
    quoted_status = _bash_single_quote(status_payload)
    return f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {str(log)!r}
if [ "$1" = "compat" ] && [ "$2" = "check" ]; then
{compat_branch}
fi
if [ "$1" = "daemon" ] && [ "$2" = "status" ]; then
  printf '{{"state": "not-installed"}}\\n'
  exit 0
fi
if [ "$1" = "project" ] && [ "$2" = "status" ]; then
  printf '%s\\n' {quoted_status}
  exit 0
fi
exit 0
"""


#: Hand the arguments straight to the Core in this checkout.
_FORWARD_TO_REAL_CORE = f'  exec {str(_REAL_THEURIAN)!r} "$@"'

#: A Core released before ``compat check`` existed. Typer writes its refusal to
#: stderr and exits 2, the same code Core uses for a malformed declaration --
#: which is precisely why the hook must not treat "not 3" as "fine, carry on
#: silently dying".
_NO_COMPAT_SUBCOMMAND = """  printf "No such command 'compat'.\\n" >&2
  exit 2"""


def _make_sandbox(
    tmp_path: Path,
    *,
    compat_branch: str = _FORWARD_TO_REAL_CORE,
    declaration: str | None = None,
    status_payload: str = _HEALTHY_STATUS_PAYLOAD,
    daemon_healthy: bool = False,
) -> Sandbox:
    """Assemble a plugin root, a stub PATH, and an environment that reaches nothing real.

    ``declaration=None`` installs the plugin's own shipped ``compatibility.yaml``,
    so a row that says nothing about compatibility gets the healthy case. A row
    that wants no declaration at all deletes the file afterwards, which is what
    the user did.

    ``daemon_healthy`` picks which ``curl`` stub answers the hook's health
    probe. It defaults to ``False`` -- every row before issue #380 depended on
    that, since the "daemon not running" branch it produces is what every
    earlier row's expectations were written against -- and only a row that
    means to exercise ``project status`` (``status_payload`` is otherwise
    unreachable: the hook never calls ``theurian project status`` past a
    failed health probe) sets it ``True``.
    """
    plugin_root = tmp_path / "plugin"
    (plugin_root / "scripts").mkdir(parents=True)
    shutil.copy2(PLUGIN / _HOOK_RELPATH, plugin_root / _HOOK_RELPATH)
    shutil.copy2(PLUGIN / _LIB_RELPATH, plugin_root / _LIB_RELPATH)
    if declaration is None:
        shutil.copy2(PLUGIN / _DECLARATION_RELPATH, plugin_root / _DECLARATION_RELPATH)
    else:
        (plugin_root / _DECLARATION_RELPATH).write_text(declaration, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "theurian-calls.log"
    stub = bin_dir / "theurian"
    stub.write_text(_stub_theurian(call_log, compat_branch, status_payload), encoding="utf-8")
    stub.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(_CURL_HEALTHY_STUB if daemon_healthy else _CURL_STUB, encoding="utf-8")
    curl.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    return Sandbox(
        plugin_root=plugin_root,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "THEURIAN_DATA_DIR": str(data),
            "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        },
        call_log=call_log,
    )


def _run_hook(sandbox: Sandbox) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None, "bash is required to run the plugin's hooks"
    return subprocess.run(  # noqa: S603 - fixed argv, sandboxed environment
        [_BASH, str(sandbox.plugin_root / _HOOK_RELPATH)],
        env=sandbox.env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _subcommands(sandbox: Sandbox) -> list[str]:
    """The ``theurian`` subcommands the hook reached, in order.

    Two words is enough to name each one and short enough that a new flag does
    not rewrite the expectation.
    """
    if not sandbox.call_log.exists():
        return []
    lines = sandbox.call_log.read_text(encoding="utf-8").splitlines()
    return [" ".join(line.split()[:2]) for line in lines if line.strip()]


def _declaration(*, minimum: str, maximum_exclusive: str, plugin_version: str) -> str:
    """The shipped declaration with three fields rewritten.

    Derived from the real ``compatibility.yaml`` rather than written out here,
    so a row cannot quietly degrade into a copy of the healthy one if that file
    changes shape. Each substitution is required to land.
    """
    text = (PLUGIN / _DECLARATION_RELPATH).read_text(encoding="utf-8")
    for key, value in (
        ("pluginVersion", plugin_version),
        ("minimum", minimum),
        ("maximumExclusive", maximum_exclusive),
    ):
        text, replaced = re.subn(
            rf"^(\s*){key}:[ \t]*\S+", rf"\g<1>{key}: {value}", text, count=1, flags=re.MULTILINE
        )
        assert replaced == 1, f"compatibility.yaml no longer declares {key!r}; fixture is blind"
    return text


# -- The fixture itself has to be able to reach the branch under test ---------


def test_the_shipped_declaration_is_the_one_the_fixture_rewrites() -> None:
    """A rewrite that silently matched nothing would make every row below the healthy row.

    :func:`_declaration` asserts its own substitution count, so this only has to
    prove that the three keys are reachable at all -- if the plugin moves them
    into a nested document or renames them, that assertion is the thing that
    should fail, and it should fail here rather than inside a row where the
    message would read as a hook defect.
    """
    rewritten = _declaration(minimum="99.0.0", maximum_exclusive="100.0.0", plugin_version="0.1.0")

    assert "minimum: 99.0.0" in rewritten
    assert "maximumExclusive: 100.0.0" in rewritten


def test_the_real_core_binary_is_the_one_under_test() -> None:
    """Every row that forwards to Core is worthless if the forward target is missing.

    The stub would ``exec`` a non-existent path, bash would report 127, and the
    rows would then be measuring the harness rather than the hook.
    """
    assert _REAL_THEURIAN.exists(), (
        f"expected the Core console script beside this interpreter at {_REAL_THEURIAN}"
    )


# -- Every degraded mode must let the session start ---------------------------


def test_a_compatible_core_lets_the_hook_run_to_the_daemon_probe(tmp_path: Path) -> None:
    """The control row: this is what the whole table is compared against.

    If this one ever stops reaching ``daemon status``, the rows below prove
    nothing about degraded modes -- they would simply be measuring a harness
    that cannot get past the compat check under any conditions.
    """
    sandbox = _make_sandbox(tmp_path)

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert _subcommands(sandbox) == ["compat check", "daemon status"]


def test_an_incompatible_core_warns_and_still_lets_the_session_start(tmp_path: Path) -> None:
    """The shipped defect: Core exits 3 and the hook died there without a word.

    Two claims, and the second is the one a bare exit-code check would miss. The
    session must start, *and* the user must be told why Theurian is standing
    down -- a hook that exits 0 by never running its warning branch is the same
    silence with a better exit code. §30: report the mismatch, upgrade nothing.
    """
    sandbox = _make_sandbox(
        tmp_path,
        declaration=_declaration(
            minimum="99.0.0", maximum_exclusive="100.0.0", plugin_version="0.1.0"
        ),
    )

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert "plugin and Core versions are incompatible." in result.stderr
    assert "core-too-old" in result.stderr, "the verdict Core produced must reach the user"
    assert _subcommands(sandbox) == ["compat check"]


def test_an_incompatible_core_is_the_exit_code_the_hook_claims_to_handle(tmp_path: Path) -> None:
    """Pins the premise the row above depends on: Core really does exit 3 here.

    ``lib.sh`` hard-codes ``THEURIAN_EXIT_INCOMPATIBLE=3`` and the hook compares
    against it. If Core ever renumbered that, the row above would still see exit
    0 -- from the fall-through path, with no warning -- and its stderr assertion
    would be the only thing complaining, pointing at the wrong file.
    """
    declaration = _declaration(
        minimum="99.0.0", maximum_exclusive="100.0.0", plugin_version="0.1.0"
    )
    sandbox = _make_sandbox(tmp_path, declaration=declaration)
    root = sandbox.plugin_root

    verdict = subprocess.run(  # noqa: S603 - fixed argv, sandboxed environment
        [
            str(_REAL_THEURIAN),
            "compat",
            "check",
            "--plugin-version",
            "0.1.0",
            "--core-minimum",
            "99.0.0",
            "--core-maximum-exclusive",
            "100.0.0",
            "--protocol-version",
            "theurian/v1",
            "--json",
        ],
        env=sandbox.env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert verdict.returncode == _EXIT_INCOMPATIBLE


def test_a_malformed_declaration_does_not_block_the_session(tmp_path: Path) -> None:
    """Core exits 2 for an unparseable declaration, and 2 is not 3.

    The hook has no branch for this, which is the correct design -- a plugin
    that cannot state its own version is a packaging bug, not a reason to refuse
    the user a session. It must fall through to the rest of the health check.
    """
    sandbox = _make_sandbox(
        tmp_path,
        declaration=_declaration(
            minimum="0.1.0-dev.0", maximum_exclusive="0.2.0", plugin_version="not-a-version"
        ),
    )

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert _subcommands(sandbox) == ["compat check", "daemon status"]


def test_a_core_too_old_to_know_compat_check_does_not_block_the_session(tmp_path: Path) -> None:
    """The upgrade path nobody can test with this checkout's Core, and the likeliest one.

    ``compat check`` shipped with Core 0.1.0. Anyone whose Core predates it gets
    a CLI parse failure, not a verdict -- and that is exactly the population the
    compatibility check exists to serve. A hook that dies here refuses sessions
    to the users it was written for.
    """
    sandbox = _make_sandbox(tmp_path, compat_branch=_NO_COMPAT_SUBCOMMAND)

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert _subcommands(sandbox) == ["compat check", "daemon status"]


def test_a_missing_declaration_does_not_block_the_session(tmp_path: Path) -> None:
    """No ``compatibility.yaml`` means Core is asked about empty versions and refuses.

    ``theurian::compat_value`` returns 1, but it is expanded in argument
    position where its status is discarded, so Core is invoked with four empty
    strings and answers ``invalid-declaration``. A half-installed plugin must
    still let the session start.
    """
    sandbox = _make_sandbox(tmp_path)
    (sandbox.plugin_root / _DECLARATION_RELPATH).unlink()

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert _subcommands(sandbox) == ["compat check", "daemon status"]


def test_an_absent_core_does_not_block_the_session(tmp_path: Path) -> None:
    """The one degraded mode that already worked, kept so a fix cannot trade it away.

    ``theurian::cli_present`` guards this branch with ``if !``, which exempts it
    from errexit -- that is why it survived. It is also the branch a user hits
    before installing anything (FR-L3), so it earns a row of its own.
    """
    sandbox = _make_sandbox(tmp_path)
    (sandbox.plugin_root.parent / "bin" / "theurian").unlink()

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert "Core is not installed" in result.stderr
    assert _subcommands(sandbox) == []


# -- The `project status` branch, and a broken context inside it (issue #380) -


def test_a_healthy_registered_project_stays_silent(tmp_path: Path) -> None:
    """The control every row below is compared against.

    A silent hook is the correct answer for a project that is registered, has
    no ``reason`` to report, and whose index is fresh -- and it is the one
    outcome ``daemon_healthy=True`` was previously never tested for at all
    (see the module docstring): every existing row above stops at the daemon
    probe, because ``curl`` always refused. This is the first row to let the
    probe succeed and reach ``project status``.
    """
    sandbox = _make_sandbox(tmp_path, daemon_healthy=True)

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert result.stderr == ""
    assert _subcommands(sandbox) == ["compat check", "project status"]


def test_a_broken_migration_warns_and_points_at_doctor(tmp_path: Path) -> None:
    """A1: registered, but ``resolve_context`` could not load the migrations.

    Measured against the real Core binary (see the fidelity row below): a
    malformed ``.theurian/migrations/*.yaml`` makes ``project status --json``
    answer ``registered: true`` -- the registry still holds this root -- with
    a ``reason`` carrying the YAML parser's own source snippet, and no
    ``indexStale`` at all, because ``_unresolved_status`` never asks that
    question. Before this test, the shipped hook's two greps matched neither
    ``"registered": *false`` nor ``"indexStale": *true`` and fell all the way
    through in silence -- the defect issue #380 reports.
    """
    status_payload = json.dumps(
        {
            "registered": True,
            "reason": (
                "broken.yaml: mapping values are not allowed here\n"
                "  in RAW_MIGRATION_BYTES, line 1, column 10"
            ),
            "unreadable": [],
        }
    )
    sandbox = _make_sandbox(tmp_path, daemon_healthy=True, status_payload=status_payload)

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert result.stderr.strip() != ""
    assert "degraded" in result.stderr or "broken" in result.stderr
    assert "doctor" in result.stderr


def test_an_unreadable_registry_warns_and_surfaces_the_remedy(tmp_path: Path) -> None:
    """B1: ``registered: null`` -- the registry itself could not be read.

    Measured against the real Core binary: a ``projects.json`` that is not
    valid JSON makes ``resolve_context`` fail with a ``ProjectError`` carrying
    its own ``remedy``, and the same corruption makes
    ``_RegistryRead.holds_root`` answer ``None`` rather than ``False`` -- so
    ``registered`` is neither ``true`` nor ``false``, and the shipped hook's
    ``"registered": *false`` grep does not match it either. The advisory in
    ``remedy`` is Core's own sentence, not project content, and is safe to
    print in full.
    """
    status_payload = json.dumps(
        {
            "registered": None,
            "reason": "/data/projects.json cannot be read as JSON: PLANTED_PARSE_DETAIL",
            "remedy": "PLANTED_REMEDY_TEXT: delete it and re-register each project.",
            "unreadable": [],
        }
    )
    sandbox = _make_sandbox(tmp_path, daemon_healthy=True, status_payload=status_payload)

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert "PLANTED_REMEDY_TEXT" in result.stderr
    assert "doctor" in result.stderr


def test_the_broken_context_warning_never_echoes_the_raw_reason(tmp_path: Path) -> None:
    """The DON'T (AC-7): ``reason`` can carry project file bytes.

    ``_unresolved_status``'s own docstring records that a broken migration's
    ``reason`` is built from a YAML parser's message, which quotes the
    offending source line back verbatim. A session-start hook prints an error
    *class* -- "the context is degraded, run doctor" -- never that line, so a
    marker planted inside ``reason`` here must never reach stderr.
    """
    marker = "RAW_MIGRATION_BYTES_MUST_NOT_APPEAR_IN_STDERR"
    status_payload = json.dumps(
        {"registered": True, "reason": f"broken.yaml: {marker}", "unreadable": []}
    )
    sandbox = _make_sandbox(tmp_path, daemon_healthy=True, status_payload=status_payload)

    result = _run_hook(sandbox)

    assert marker not in result.stderr


def test_an_unregistered_repository_with_a_reason_keeps_the_original_advice(
    tmp_path: Path,
) -> None:
    """AC-5, the scope fence: ``registered: false`` still wins its own case.

    A not-in-git repository is ``registered: false`` *and* carries a
    ``reason`` (issue #381's own face, out of this change's scope) -- and the
    hook's first check must still catch it before the broken-context branch
    ever runs, so the "not registered" advice is unchanged.
    """
    status_payload = json.dumps(
        {"registered": False, "reason": "/repo is not inside a Git repository."}
    )
    sandbox = _make_sandbox(tmp_path, daemon_healthy=True, status_payload=status_payload)

    result = _run_hook(sandbox)

    assert result.returncode == 0
    assert "this repository is not registered. Run /theurian:register-project." in result.stderr
    assert "doctor" not in result.stderr
