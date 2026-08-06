"""``theurian setup``, ``doctor``, and ``uninstall`` (FR-L1, FR-L5).

A composition root. This is where the abstract steps meet concrete adapters:
the file-backed secret store, the LaunchAgent or systemd manager, Claude Code's
configuration, and the daemon's health probe (ADR-0003).

``/theurian:setup`` is a presentation shell over ``theurian setup --json``.
There is one implementation of setup, because two would drift and the one that
drifted would be the one the user ran.
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from theurian import __version__
from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.daemon.instance import DEFAULT_PORT, probe_health
from theurian.domain.setup import SetupState
from theurian.infrastructure.claude.mcp_config import ClaudeCodeMcpConfig, ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore, default_data_dir
from theurian.infrastructure.services import detect_manager
from theurian.security.env_file import TOKEN_KEY

#: A setup that could not converge. Distinct from 1 so a caller can tell "you
#: need to decide something" from "it broke".
EXIT_NEEDS_CONSENT = 5

PortOption = Annotated[int, typer.Option("--port", help="Port the daemon binds on 127.0.0.1.")]
JsonFlag = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


def build_context(port: int = DEFAULT_PORT, data_dir: Path | None = None) -> SetupContext:
    """Assemble the context from this machine.

    Everything concrete is chosen here and nowhere else, which is what lets the
    state machine be tested against a temporary home directory.
    """
    resolved = data_dir or default_data_dir()
    home = Path.home()
    connection = ConnectionSpec(port=port)

    return SetupContext(
        home=home,
        data_dir=resolved,
        port=port,
        project_root=_repository_root(),
        connection=connection,
        mcp_config=ClaudeCodeMcpConfig(home=home),
        secrets=FileSecretStore(resolved),
        health=lambda: probe_health(port=port),
        service=detect_manager(executable=_executable(), home=home),
        executable=_executable(),
    )


def _executable() -> str:
    """The absolute path a service unit will invoke.

    ``sys.argv[0]`` is what the user typed, which may be a bare name resolved
    through their shell's PATH -- and a service manager's PATH is not theirs.
    """
    found = shutil.which("theurian")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else ""


def _repository_root() -> Path | None:
    """The Git repository the command was invoked in, if any.

    ``None`` is normal: installing the machine-wide parts outside a repository
    is a reasonable thing to do, and the project steps report themselves not
    applicable rather than failing.
    """
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def setup_command(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan and change nothing.")
    ] = False,
    approve_conflicts: Annotated[
        bool,
        typer.Option(
            "--approve-conflicts",
            help=(
                "Proceed past configuration that differs from what Theurian would "
                "install, applying the remaining steps. The differing configuration is "
                "reported, never replaced."
            ),
        ),
    ] = False,
    port: PortOption = DEFAULT_PORT,
    as_json: JsonFlag = False,
) -> None:
    """Install and configure Theurian for this machine and repository.

    The only command that installs software, registers an OS service, or writes
    configuration. Running it twice changes nothing (FR-L2).
    """
    service = SetupService(build_context(port=port))
    report = service.run(SetupRequest(dry_run=dry_run, approve_conflicts=approve_conflicts))

    _write(report.to_json(), as_json=as_json)

    if report.state is SetupState.AWAITING_CONSENT:
        raise typer.Exit(EXIT_NEEDS_CONSENT)
    if not report.succeeded and report.state is not SetupState.PLAN_BUILT:
        raise typer.Exit(1)


def doctor_command(
    port: PortOption = DEFAULT_PORT,
    report_mode: Annotated[
        bool,
        typer.Option(
            "--report",
            help="Produce a redacted diagnostic that is safe to paste into an issue.",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Report what is wrong, and change nothing.

    Deliberately read-only. A diagnostic that repairs things is a diagnostic
    whose output you cannot trust, because you can no longer tell what was
    broken from what it just fixed.
    """
    context = build_context(port=port)
    report = SetupService(context).run(SetupRequest(dry_run=True))
    payload = report.to_json()

    problems = [step for step in report.steps if step.would_change or step.needs_consent]
    payload["healthy"] = not problems
    payload["problemCount"] = len(problems)

    if report_mode:
        payload = _redacted(payload, context)

    _write(payload, as_json=as_json)
    if problems:
        raise typer.Exit(1)


def _redaction_anchors(context: SetupContext) -> tuple[tuple[str, str], ...]:
    """Every path worth hiding, longest first.

    Longest first is the entire correctness argument. These are plain substring
    replacements, so an anchor that is a prefix of another must go second or it
    eats the prefix and leaves the rest: a checkout inside the home directory --
    which is where most checkouts are -- had ``/home/u/work/api`` turned into
    ``~/work/api`` by the home anchor before ``<repository>`` was ever tried, so
    that substitution was a no-op on every ordinary machine and published the
    repository's path relative to home.

    Resolved *and* unresolved, because the two arrive here from different
    places: ``context.home`` is whatever ``$HOME`` says while ``project_root``
    is ``Path.cwd().resolve()``. On an account whose home is a symlink -- macOS
    ``/var``, a Linux ``/home`` that points elsewhere -- the unresolved anchor
    matched *inside* the resolved path and produced ``/private~/work/api/…``,
    which discloses the tail it failed to replace.

    Both faults were live in a shipped command and invisible to a test whose
    fixture put the repository beside the home directory rather than inside it.
    """
    candidates: list[tuple[Path, str]] = [(context.auth_dir / TOKEN_KEY, "<token file>")]
    if context.project_root is not None:
        candidates.append((context.project_root, "<repository>"))
    # A data directory inside HOME needs no anchor of its own -- `~/.theurian`
    # discloses nothing and reads better than a placeholder. One outside HOME is
    # a `THEURIAN_DATA_DIR` pointing at a mount or a shared path, and nothing
    # else here covers it.
    if not context.data_dir.is_relative_to(context.home):
        candidates.append((context.data_dir, "<data directory>"))
    candidates.append((context.home, "~"))

    anchors: dict[str, str] = {}
    for path, replacement in candidates:
        for variant in (path, path.resolve()):
            anchors.setdefault(str(variant), replacement)
    # The path itself breaks length ties, so the order is total and the output
    # does not depend on dict iteration.
    return tuple(sorted(anchors.items(), key=lambda anchor: (-len(anchor[0]), anchor[0])))


def _redacted(payload: dict[str, Any], context: SetupContext) -> dict[str, Any]:
    """Strip anything personal from a diagnostic meant to be shared (O-3).

    ``doctor --report`` output is what people paste into public issues, so it is
    redacted by default rather than on request. Absolute paths name the user's
    account and their repositories; both are someone's private information even
    though neither is a credential.
    """
    anchors = _redaction_anchors(context)

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            for needle, replacement in anchors:
                value = value.replace(needle, replacement)
            return value
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    scrubbed: dict[str, Any] = scrub(payload)
    scrubbed["redacted"] = True
    scrubbed["platform"] = (
        f"{platform.system()} {platform.machine()} python{platform.python_version()}"
    )
    scrubbed["version"] = __version__
    return scrubbed


def uninstall_command(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List what would be removed, and remove nothing.")
    ] = False,
    service_only: Annotated[
        bool,
        typer.Option(
            "--service-only",
            help="Remove the OS service and the MCP entry, keeping the data directory.",
        ),
    ] = True,
    port: PortOption = DEFAULT_PORT,
    as_json: JsonFlag = False,
) -> None:
    """Remove the OS service and the MCP connection.

    Never removes approved knowledge. That lives in Git inside your repository,
    it is not Theurian's to delete, and no flag here reaches it (FR-L5). The
    data directory holds only derived state and the local token; removing it is
    a separate, explicit choice.
    """
    context = build_context(port=port)
    removed: list[str] = []
    warnings: list[str] = []

    service_path = _service_path(context)
    if context.service is not None and service_path:
        removed.append(service_path)
    if context.mcp_config.installed_entry() is not None:
        removed.append(f"{context.mcp_config.path} (the `theurian` MCP entry only)")

    if not dry_run:
        if context.service is not None:
            import asyncio  # noqa: PLC0415 - only this branch needs it

            asyncio.run(context.service.uninstall())
        failure = context.mcp_config.remove()
        if failure:
            warnings.append(failure)

    _write(
        {
            "dryRun": dry_run,
            "removed": removed,
            "warnings": warnings,
            "kept": [
                str(context.data_dir),
                "Approved knowledge in your repository, which Theurian never deletes.",
            ],
            "serviceOnly": service_only,
        },
        as_json=as_json,
    )


def _service_path(context: SetupContext) -> str:
    for attribute in ("plist_path", "unit_path"):
        path = getattr(context.service, attribute, None)
        if path is not None:
            return str(path)
    return ""


def _write(payload: dict[str, Any], *, as_json: bool) -> None:
    from theurian.cli.commands import _emit  # noqa: PLC0415 - avoids a circular import

    _emit(payload, as_json=as_json)
