"""``theurian setup``, ``doctor``, and ``uninstall`` (FR-L1, FR-L5).

A composition root. This is where the abstract steps meet concrete adapters:
the file-backed secret store, the LaunchAgent or systemd manager, Claude Code's
configuration, and the daemon's health probe (ADR-0003).

``/theurian:setup`` is a presentation shell over ``theurian setup --json``.
There is one implementation of setup, because two would drift and the one that
drifted would be the one the user ran.

Typer prints a command's docstring and every ``typer.Option(help=...)`` string
in this module verbatim, so both stay plain prose with single backticks. A
``:func:`` role or a ``literal`` reaches the user as its own markup -- which is
how the reST first drafted here was caught.

**Verbatim is a property this module does not own.** It holds because
``cli/main.py`` builds the app with ``rich_markup_mode=None``; with Typer's
default, ``'theurian[daemon]'`` in the sentence below printed as
``'theurian'``, one line above the sentence explaining that the extra is what
makes ``theurian daemon start`` work. Escaping it was tried and reverted --
``TYPER_USE_RICH=0`` formats through Click, where the escape reaches the user
as a backslash and the command it prints is not an installable requirement.
``tests/unit/test_cli_help_rendering.py`` renders every ``--help`` in the tree
under both settings and fails on any string that does not arrive intact.
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
from theurian.domain.setup import SetupError, SetupState
from theurian.infrastructure.claude.mcp_config import ClaudeCodeMcpConfig, ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore, default_data_dir
from theurian.infrastructure.services import detect_manager
from theurian.security.env_file import TOKEN_KEY

#: A setup that could not converge. Distinct from 1 so a caller can tell "you
#: need to decide something" from "it broke".
EXIT_NEEDS_CONSENT = 5

#: The one data directory `doctor --report` leaves legible, as `~/.theurian`.
#: Kept in step with :func:`default_data_dir`, which builds the same name.
_DEFAULT_DATA_DIRNAME = ".theurian"

PortOption = Annotated[int, typer.Option("--port", help="Port the daemon binds on 127.0.0.1.")]
JsonFlag = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


def build_context(
    port: int = DEFAULT_PORT,
    data_dir: Path | None = None,
    *,
    for_publication: bool = False,
) -> SetupContext:
    """Assemble the context from this machine.

    Everything concrete is chosen here and nowhere else, which is what lets the
    state machine be tested against a temporary home directory.

    ``for_publication`` is decided here for the same reason: this is the layer
    that knows where the output is going, and a probe cannot find that out for
    itself.

    **It defaults to ``False``, which is fail-open, and that is deliberate.** The
    default is what ``theurian setup`` and plain ``theurian doctor`` want, and
    both are read on the terminal of the person who ran them. What makes the
    default safe is that nothing publishes a payload without going through
    :func:`_redacted`, which refuses a context that did not ask for publication
    -- so the failure mode is a raised error, not a quiet disclosure. Any new
    caller that shares output has to set this *and* redact; neither alone works.
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
        for_publication=for_publication,
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
    """Configure this machine to run Theurian, and connect Claude Code to it.

    Creates the data directory or tightens an existing one to 0700, mints and
    stores the local access token, writes the block that references it in the
    env file, registers and starts the user-scoped OS service, and writes the
    MCP connection entry. Those 7 steps are every write setup performs, apart
    from the setup journal it appends under the data directory -- a write that
    belongs to no step, and one `changedPaths` names whenever that append
    reached the disk. Nothing else registers the service or writes that entry,
    and running it twice changes nothing (FR-L2).

    In the env file it rewrites only the lines between its own `# >>> theurian
    >>>` markers. Anything you added around them stays where you put it.

    Setup cannot tell you Core is missing, because setup is Core: it runs from
    the installation it would have to create, and a shell with no `theurian` on
    its PATH never reaches this text at all. Core arrives through
    `uv tool install --python 3.13 'theurian[daemon]'` or
    `pipx install --python 3.13 'theurian[daemon]'`, and no step here installs
    anything. The extra is not decoration: without it `theurian daemon start`
    has no server to run, and `core-present` refuses.

    The other 11 steps only report what they found: platform, core-present,
    artifact-integrity, single-instance, project-registered, project-layout,
    gitignore, mcp-health, migrations-valid, initial-index, serena-detection.
    Several of them name the command that does the work instead --
    `theurian init`, `theurian project register` -- and setup runs none of them.
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

    A problem is something setup would change, or something it would ask you to
    approve. A step can also be satisfied and still carry a reservation -- a
    line below Theurian's block in your env file that appears to assign the same
    variable, say, which setup will not touch because it is yours. Those are
    listed under warnings and are not counted as problems, so a machine can be
    healthy and still have something worth reading.
    """
    context = build_context(port=port, for_publication=report_mode)
    report = SetupService(context).run(SetupRequest(dry_run=True))
    payload = report.to_json()

    # Deliberately not widened to take in the report's warnings. A reservation
    # is a finding with no work attached -- `SetupService._reservations` states
    # the split -- and counting one as a problem would exit 1 on a machine where
    # there is nothing for `theurian setup` to do about it. It reaches the reader
    # through `warnings`, which this payload carries verbatim.
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

    **This list is enumerated in prose in three places** -- ``SECURITY.md``,
    ``CONTRIBUTING.md`` and ``docs/security/local-mcp.md`` -- because a reader
    deciding whether to paste a report needs to know what was substituted. None
    of them can be reached from here by a search for a symbol, so they are named
    here: changing an anchor means changing four things.

    ``project_root`` has only one spelling and needs only one: it arrives from
    ``_repository_root``, and ``os.getcwd()`` is fully resolved on POSIX, so
    there is no second form of it for ``.resolve()`` to have discarded. The
    operator's own spelling of the repository still reaches the payload -- a
    shell keeps it in ``$PWD``, and it arrives here inside whatever they typed
    into ``THEURIAN_DATA_DIR``. That string is a data directory, and it is
    anchored as one, which is where ``~/work/api/.theurian-data`` was coming
    from.
    """
    candidates: list[tuple[Path, str]] = [(context.auth_dir / TOKEN_KEY, "<token file>")]
    if context.project_root is not None:
        candidates.append((context.project_root, "<repository>"))
    # Only the *default* data directory is left to the `~` substitution. `~` is
    # anonymous and `~/.theurian` reads better than a placeholder, but that
    # argument is about one path and the guard used to be about every path under
    # HOME -- so `THEURIAN_DATA_DIR=$HOME/clients/northwind-acquisition/theurian`
    # was published in full, on the strength of a comment reasoning from the
    # default. Anything the operator chose is redacted, wherever it points.
    if context.data_dir != context.home / _DEFAULT_DATA_DIRNAME:
        candidates.append((context.data_dir, "<data directory>"))
    # The install location is genuinely useful for diagnosis and is given up
    # deliberately: it is routinely a virtualenv under a project directory, so
    # it names a repository as surely as the repository does. `platform` and
    # `version` are still published, and the install method can be asked for.
    if context.executable:
        candidates.append((Path(context.executable), "<executable>"))
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

    **This is half of the control, and it is the half that only reaches values
    Theurian itself put in the payload.** Substitution is an allowlist of the
    paths this context holds, so a string that came from another file, another
    process, or an exception has no anchor here and goes out verbatim -- which is
    how a literal ``Authorization: Bearer <token>``, read out of someone's
    ``~/.claude.json``, once left in a report that said ``redacted: true``. The
    other half is :attr:`SetupContext.for_publication`, which is set on the
    context this payload was produced from and makes each probe withhold what it
    did not author. Adding a value from outside to a step is not made safe by
    anything in this function.

    Which is why the two halves are welded together here. Calling this with a
    context that was not built for publication produces a payload stamped
    ``"redacted": true`` that still carries whatever the steps read -- the exact
    defect this pair of mechanisms exists to close, reachable by using one of
    them. It cannot happen today, because ``doctor_command`` is the only caller;
    the guard is for the caller that does not exist yet.

    Raises:
        SetupError: If *context* was not built with ``for_publication``.
    """
    if not context.for_publication:
        msg = (
            "A report can only be redacted from a run that withheld as it went: "
            "build the context with `for_publication=True`. Stamping this payload "
            "`redacted` would claim a review of values no anchor here can reach."
        )
        raise SetupError(msg)

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
