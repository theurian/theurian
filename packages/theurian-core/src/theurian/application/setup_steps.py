"""The individual setup steps of §6.2.

Each step is a **probe** that reports what it found without changing anything,
and — where setup acts on the answer — an **apply** that makes that answer true.
Keeping them apart is what makes ``--dry-run`` the same code path as a real run,
and what lets the whole state machine be tested against a temporary home
directory.

A probe never writes. An apply is only ever reached for a step whose probe said
:attr:`StepStatus.MISSING` -- a conflicting step is never applied, whatever the
user approved, because approval here is consent to *proceed past* a conflict and
not consent to overwrite it (:class:`SetupRequest`, SEC-18).

**Several steps have no apply at all** and are declared without one. They exist
to say what is undone and name the command that does it: §6.2 rows 11-13 are
`theurian init` and `theurian project register`, and setup performs neither.
Such a step reports and names no paths, because every reader of ``paths`` -- the
plan shown before consent, the changed-files list, ``uninstall --dry-run`` --
reads it as a file setup would create or modify.
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from theurian.application.project_service import (
    ProjectError,
    ProjectPaths,
    ProjectRegistry,
    read_active_state,
)
from theurian.application.setup_context import SetupContext
from theurian.domain.ports.daemon_manager import ServiceState
from theurian.domain.setup import SetupStep, StepId, StepStatus
from theurian.security.env_file import TOKEN_KEY, env_file_contents
from theurian.security.paths import is_world_accessible
from theurian.security.tokens import MIN_TOKEN_LENGTH, TOKEN_ENV_VAR, generate_token

#: Marker delimiting the block setup owns in a shell profile. Only ever this
#: block is rewritten; the rest of the file is never touched (SEC-18).
PROFILE_BEGIN: Final = "# >>> theurian >>>"
PROFILE_END: Final = "# <<< theurian <<<"

_DATA_DIR_MODE: Final = 0o700

#: How long to wait for a freshly started daemon to answer. Generous enough for
#: a cold start on a loaded machine, short enough that a daemon which will never
#: start does not hold up a person waiting on setup.
DAEMON_START_TIMEOUT_SECONDS: Final = 15.0
_POLL_SECONDS: Final = 0.2

#: Paths under `.theurian/` that are derived and must never be committed
#: (ADR-0004). `theurian init` writes these; setup only verifies them.
_REQUIRED_PROJECT_DIRS: Final = ("migrations", "knowledge", "state")


@dataclass(frozen=True, slots=True)
class Step:
    """One step, as a probe and an action."""

    step_id: StepId
    probe: Callable[[SetupContext], SetupStep]
    #: ``None`` for a step that only ever describes what it found. Absence
    #: rather than a do-nothing function, because the do-nothing function was
    #: indistinguishable from a real one at the call site: ``_apply`` called it,
    #: found no exception, and recorded CHANGED -- so three steps that write
    #: nothing reported five files as modified and journalled them as applied.
    #: A step with no action is now something the runner can see.
    apply: Callable[[SetupContext], None] | None = None
    #: A failure here rolls the run back rather than degrading it.
    critical: bool = True


# -- 1. Platform ------------------------------------------------------------


def probe_platform(_: SetupContext) -> SetupStep:
    """macOS and Linux are the 1.0 targets.

    Windows is refused up front rather than half-installed: the instance lock is
    POSIX ``fcntl``, and a setup that "succeeded" into a daemon that cannot
    enforce single-instance would be worse than a clear refusal.
    """
    supported = sys.platform == "darwin" or sys.platform.startswith("linux")
    if supported:
        return SetupStep(
            step_id=StepId.PLATFORM,
            status=StepStatus.SATISFIED,
            summary=f"{platform.system()} {platform.machine()} is supported.",
        )
    return SetupStep(
        step_id=StepId.PLATFORM,
        status=StepStatus.CONFLICTING,
        summary=f"{platform.system()} is not supported yet.",
        detail=(
            f"Theurian 1.0 targets macOS and Linux. The single-instance lock uses "
            f"POSIX fcntl, which {platform.system()} does not provide. See "
            f"packaging/windows/README.md."
        ),
    )


# -- 2. Core present --------------------------------------------------------


def probe_core(context: SetupContext) -> SetupStep:
    """Whether the running ``theurian`` can be named by an absolute path.

    The service unit invokes it by absolute path, because launchd and systemd
    start with a PATH that is not the user's -- so an installation reachable only
    through a shell alias or a virtualenv on ``PATH`` produces a service that
    cannot start.
    """
    if context.executable and Path(context.executable).exists():
        return SetupStep(
            step_id=StepId.CORE_PRESENT,
            status=StepStatus.SATISFIED,
            summary=f"Core is installed at {context.executable}.",
        )
    return SetupStep(
        step_id=StepId.CORE_PRESENT,
        status=StepStatus.CONFLICTING,
        summary="Could not determine an absolute path to the theurian executable.",
        detail=(
            "The daemon service must invoke Theurian by absolute path, because a "
            "service manager starts with a PATH that is not your shell's. Install "
            "Theurian with `uv tool install theurian` or `pipx install theurian`."
        ),
    )


# -- 3. Artifact integrity --------------------------------------------------


def probe_artifact_integrity(_: SetupContext) -> SetupStep:
    """Verify a downloaded artifact against the release manifest.

    Not applicable while Theurian is pre-release: there is no published release
    manifest to check against, and a step that reported ``satisfied`` without
    checking anything would be a false assurance about supply chain integrity
    (T-16). Reported as skipped so the gap is visible.
    """
    return SetupStep(
        step_id=StepId.ARTIFACT_INTEGRITY,
        status=StepStatus.NOT_APPLICABLE,
        summary="No signed release manifest exists yet; nothing to verify against.",
        detail="Artifact verification arrives with the first tagged release (OSS-7, T-16).",
    )


# -- 4. Data directory ------------------------------------------------------


def probe_data_directory(context: SetupContext) -> SetupStep:
    directory = context.data_dir
    if not directory.exists():
        return SetupStep(
            step_id=StepId.DATA_DIRECTORY,
            status=StepStatus.MISSING,
            summary=f"{directory} does not exist.",
            action=f"Create {directory} with mode 0700.",
            paths=(str(directory),),
        )
    if is_world_accessible(directory):
        mode = directory.stat().st_mode & 0o777
        return SetupStep(
            step_id=StepId.DATA_DIRECTORY,
            status=StepStatus.MISSING,
            summary=f"{directory} is mode {mode:04o}, readable by other users.",
            action=f"Tighten {directory} to 0700.",
            paths=(str(directory),),
        )
    return SetupStep(
        step_id=StepId.DATA_DIRECTORY,
        status=StepStatus.SATISFIED,
        summary=f"{directory} exists with private permissions.",
    )


def apply_data_directory(context: SetupContext) -> None:
    context.data_dir.mkdir(parents=True, exist_ok=True, mode=_DATA_DIR_MODE)
    context.data_dir.chmod(_DATA_DIR_MODE)


# -- 5 & 6. Token and its storage -------------------------------------------


def probe_token(context: SetupContext) -> SetupStep:
    path = context.auth_dir / TOKEN_KEY
    if not path.is_file():
        return SetupStep(
            step_id=StepId.TOKEN,
            status=StepStatus.MISSING,
            summary="No local access token yet.",
            action="Generate a 256-bit token with the system CSPRNG.",
            paths=(str(path),),
        )
    if len(path.read_text(encoding="utf-8").strip()) < MIN_TOKEN_LENGTH:
        return SetupStep(
            step_id=StepId.TOKEN,
            status=StepStatus.CONFLICTING,
            summary="The stored token is too short to be the one Theurian generates.",
            detail=(
                f"{path} holds fewer than {MIN_TOKEN_LENGTH} characters. Rotate it "
                f"with `theurian auth rotate` rather than having setup replace it, "
                f"so every configured client is updated deliberately."
            ),
        )
    return SetupStep(
        step_id=StepId.TOKEN,
        status=StepStatus.SATISFIED,
        summary="A local access token is present. Never regenerated by setup (ADR-0011).",
    )


def apply_token(context: SetupContext) -> None:
    """Mint a token only if there is none.

    Never regenerates: silently replacing a token breaks every configured client
    at once, with no explanation. Rotation is explicit (ADR-0011).
    """
    if asyncio.run(context.secrets.get(TOKEN_KEY)) is None:
        asyncio.run(context.secrets.set(TOKEN_KEY, generate_token()))


def probe_token_storage(context: SetupContext) -> SetupStep:
    path = context.auth_dir / TOKEN_KEY
    if not path.is_file():
        return SetupStep(
            step_id=StepId.TOKEN_STORAGE,
            status=StepStatus.MISSING,
            summary="The token file does not exist yet.",
            action="Store the token as a 0600 file inside a 0700 directory.",
            paths=(str(path),),
        )
    if is_world_accessible(path) or is_world_accessible(context.auth_dir):
        return SetupStep(
            step_id=StepId.TOKEN_STORAGE,
            status=StepStatus.CONFLICTING,
            summary="The token is readable by other local users.",
            detail=(
                f"{path} is mode {path.stat().st_mode & 0o777:04o}. Tightening the "
                f"mode is not enough once a credential has been exposed -- rotate it "
                f"with `theurian auth rotate`."
            ),
        )
    return SetupStep(
        step_id=StepId.TOKEN_STORAGE,
        status=StepStatus.SATISFIED,
        summary="The token is stored 0600 inside a 0700 directory.",
    )


def apply_token_storage(context: SetupContext) -> None:
    apply_token(context)


# -- 7. Env reference -------------------------------------------------------


def probe_env_reference(context: SetupContext) -> SetupStep:
    """The file that exports the token by *reference* (SEC-5)."""
    wanted = env_file_contents(context.data_dir)
    path = context.env_file
    if not path.is_file():
        return SetupStep(
            step_id=StepId.ENV_REFERENCE,
            status=StepStatus.MISSING,
            summary=f"{path} does not exist.",
            action=f"Write {path}, exporting {TOKEN_ENV_VAR} from the token file.",
            paths=(str(path),),
        )
    if path.read_text(encoding="utf-8") != wanted:
        return SetupStep(
            step_id=StepId.ENV_REFERENCE,
            status=StepStatus.MISSING,
            summary=f"{path} does not match the current token location.",
            action=f"Rewrite {path}.",
            paths=(str(path),),
        )
    return SetupStep(
        step_id=StepId.ENV_REFERENCE,
        status=StepStatus.SATISFIED,
        summary=f"{path} exports {TOKEN_ENV_VAR} by reference.",
    )


def apply_env_reference(context: SetupContext) -> None:
    path = context.env_file
    path.parent.mkdir(parents=True, exist_ok=True, mode=_DATA_DIR_MODE)
    # 0600 before anything is written: the file names the token's location, and
    # its whole purpose is to be sourced by the owner alone.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, env_file_contents(context.data_dir).encode("utf-8"))
    finally:
        os.close(descriptor)
    path.chmod(0o600)


# -- 8. Daemon service ------------------------------------------------------


def probe_daemon_service(context: SetupContext) -> SetupStep:
    service = context.service
    if service is None:
        return SetupStep(
            step_id=StepId.DAEMON_SERVICE,
            status=StepStatus.NOT_APPLICABLE,
            summary="This platform has no user-scoped service manager.",
            detail=(
                "Theurian works without one: start the daemon with "
                "`theurian daemon start --foreground`. Nothing was installed."
            ),
        )

    status = asyncio.run(service.status())
    if status.state is ServiceState.NOT_INSTALLED:
        return SetupStep(
            step_id=StepId.DAEMON_SERVICE,
            status=StepStatus.MISSING,
            summary=f"No {service.platform_id} service is registered.",
            action=f"Install a user-scoped {service.platform_id} service. No root is required.",
            paths=(_service_path(context),),
        )

    difference = _service_difference(context)
    if difference:
        return SetupStep(
            step_id=StepId.DAEMON_SERVICE,
            status=StepStatus.CONFLICTING,
            summary="A service is registered with a different definition.",
            detail=difference,
        )
    return SetupStep(
        step_id=StepId.DAEMON_SERVICE,
        status=StepStatus.SATISFIED,
        summary=f"A user-scoped {service.platform_id} service is registered.",
    )


def apply_daemon_service(context: SetupContext) -> None:
    if context.service is None:  # pragma: no cover - probe reports NOT_APPLICABLE
        return
    asyncio.run(context.service.install(port=context.port, data_directory=str(context.data_dir)))


def _service_path(context: SetupContext) -> str:
    service = context.service
    for attribute in ("plist_path", "unit_path"):
        path = getattr(service, attribute, None)
        if path is not None:
            return str(path)
    return ""  # pragma: no cover - both adapters expose one


def _service_difference(context: SetupContext) -> str:
    differ = getattr(context.service, "differs_from_installed", None)
    if differ is None:  # pragma: no cover - both adapters expose it
        return ""
    result: str = differ(port=context.port, data_directory=str(context.data_dir))
    return result


# -- 9 & 10. Daemon running, single instance --------------------------------


def probe_daemon_running(context: SetupContext) -> SetupStep:
    if context.health() is not None:
        return SetupStep(
            step_id=StepId.DAEMON_RUNNING,
            status=StepStatus.SATISFIED,
            summary=f"A daemon is answering on 127.0.0.1:{context.port}.",
        )
    if context.service is None:
        return SetupStep(
            step_id=StepId.DAEMON_RUNNING,
            status=StepStatus.NOT_APPLICABLE,
            summary="No daemon is running, and this platform has no service manager.",
            detail="Start it with `theurian daemon start --foreground`.",
        )
    return SetupStep(
        step_id=StepId.DAEMON_RUNNING,
        status=StepStatus.MISSING,
        summary=f"Nothing is answering on 127.0.0.1:{context.port}.",
        action="Start the service that was just registered.",
    )


def apply_daemon_running(context: SetupContext) -> None:
    """Start the service, then wait for it to actually answer.

    Without the wait, the verification pass re-probes health microseconds after
    the start command returns -- long before the daemon has bound its port -- so
    a perfectly healthy install would report ``degraded`` almost every time.
    Asking the service manager to start something and having something answer
    are separate events, and setup reports the second.
    """
    if context.service is None:  # pragma: no cover - probe reports NOT_APPLICABLE
        return
    asyncio.run(context.service.start())

    deadline = time.monotonic() + DAEMON_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if context.health() is not None:
            return
        time.sleep(_POLL_SECONDS)


def probe_single_instance(context: SetupContext) -> SetupStep:
    """Confirms the reuse guarantee holds after everything above ran.

    Never repairs. Two daemons on one data directory is a state to report, not
    one to resolve by killing something that may belong to another session
    (ADR-0002).
    """
    health = context.health()
    if health is None:
        return SetupStep(
            step_id=StepId.SINGLE_INSTANCE,
            status=StepStatus.NOT_APPLICABLE,
            summary="No daemon is running, so there is nothing to be duplicated.",
        )

    running_dir = str(health.get("dataDir", ""))
    if running_dir and Path(running_dir).resolve() != context.data_dir.resolve():
        return SetupStep(
            step_id=StepId.SINGLE_INSTANCE,
            status=StepStatus.CONFLICTING,
            summary="The daemon on this port serves a different data directory.",
            detail=(
                f"Port {context.port} is held by a Theurian serving {running_dir}, not "
                f"{context.data_dir}. Nothing was changed: stop it deliberately, or "
                f"run this daemon on another port."
            ),
        )
    return SetupStep(
        step_id=StepId.SINGLE_INSTANCE,
        status=StepStatus.SATISFIED,
        summary="Exactly one daemon serves this data directory.",
    )


# -- 11 & 12 & 13. The repository -------------------------------------------


def _unreadable_registry_summary(registry: ProjectRegistry, root: Path) -> str:
    """Why the registry could not say, in terms that shape of failure allows.

    Two different refusals reach :meth:`ProjectRegistry.ids_for_root`'s caller and
    only one of them is about an *entry*. A file whose top level does not parse
    -- not JSON, a JSON array, arbitrary bytes -- has no entries to speak of, so
    "holds an entry that cannot be read" is a claim nothing supports: it invites
    the reader to go and find the offending line in a file that has none, and it
    disagreed in kind with the ``detail`` beside it, which already carried the
    file-level cure.

    Told apart by asking for the ids: an unreadable *entry* leaves the set
    computable and non-empty, an unreadable *file* leaves it uncomputable. A
    second read of a small file is the honest price -- the alternative is
    inferring the shape from the exception's message text.
    """
    try:
        registry.unreadable_ids()
    except ProjectError:
        return (
            f"Cannot tell whether {root.name} is registered: {registry.path} cannot be "
            f"read at all, so nothing in it can be checked."
        )
    return (
        f"Cannot tell whether {root.name} is registered: {registry.path} holds "
        f"an entry that cannot be read, and it might be this repository's own."
    )


def probe_project_registered(context: SetupContext) -> SetupStep:
    """Whether this repository is registered -- or, honestly, that it cannot be told.

    Uses :meth:`ProjectRegistry.ids_for_root`, not a hand-rolled scan of
    ``load()``: a malformed entry names no root path, so there is no way to tell
    whether it is *this* directory's own registration -- the same impossibility
    ``ids_for_root`` itself refuses on rather than guesses. A scan of ``load()``
    would have silently skipped that entry and reported ``MISSING`` beside a
    remedy that cannot work: registering while an unreadable entry might hold
    this very root's id is refused by :meth:`ProjectRegistry.register`, and the
    step this report is shown on is the first screen a person reads when
    something is broken.
    """
    root = context.project_root
    if root is None:
        return SetupStep(
            step_id=StepId.PROJECT_REGISTERED,
            status=StepStatus.NOT_APPLICABLE,
            summary="Not inside a Git repository; nothing to register.",
        )

    registry = ProjectRegistry.default(context.data_dir)
    try:
        found = registry.ids_for_root(root)
    except ProjectError as exc:
        # `paths` is left empty, unlike a MISSING step's: this step never writes
        # to `registry.path`, whatever the user decides, so listing it there
        # would claim setup "would touch" a file it only ever reads.
        return SetupStep(
            step_id=StepId.PROJECT_REGISTERED,
            status=StepStatus.CONFLICTING,
            summary=_unreadable_registry_summary(registry, root),
            detail=f"{exc} {exc.remedy}".strip(),
        )
    if found:
        return SetupStep(
            step_id=StepId.PROJECT_REGISTERED,
            status=StepStatus.SATISFIED,
            summary=f"{root.name} is registered.",
        )
    # `paths` is empty for the same reason it is empty above: this step has no
    # apply, so setup never writes `registry.path` whatever the user decides.
    # Naming it here put the file in the plan's "would be created or modified"
    # list and then in `changed_paths`, for a run that only ever read it.
    return SetupStep(
        step_id=StepId.PROJECT_REGISTERED,
        status=StepStatus.MISSING,
        summary=f"{root.name} is not registered with this daemon.",
        action="Register this repository. Run `theurian project register`.",
        critical=False,
    )


def probe_project_layout(context: SetupContext) -> SetupStep:
    directory = context.theurian_dir
    if directory is None:
        return SetupStep(
            step_id=StepId.PROJECT_LAYOUT,
            status=StepStatus.NOT_APPLICABLE,
            summary="Not inside a Git repository.",
        )
    missing = [name for name in _REQUIRED_PROJECT_DIRS if not (directory / name).is_dir()]
    if missing:
        # No `paths`: `init` creates these, not setup. The summary already names
        # every directory that is absent, so nothing a reader needs is lost by
        # keeping them out of a list that means "setup would write this".
        return SetupStep(
            step_id=StepId.PROJECT_LAYOUT,
            status=StepStatus.MISSING,
            summary=f"{directory} is missing {', '.join(missing)}.",
            action="Create the missing directories. Run `theurian init`.",
            critical=False,
        )
    return SetupStep(
        step_id=StepId.PROJECT_LAYOUT,
        status=StepStatus.SATISFIED,
        summary=f"{directory} has the expected layout.",
    )


def probe_gitignore(context: SetupContext) -> SetupStep:
    """Derived artifacts must not be committable (ADR-0004, O-2)."""
    root = context.project_root
    if root is None:
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.NOT_APPLICABLE,
            summary="Not inside a Git repository.",
        )
    gitignore = root / ".gitignore"
    contents = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if ".theurian/state" in contents:
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.SATISFIED,
            summary="Derived Theurian artifacts are ignored.",
        )
    # No `paths`: `init` appends the block, setup only reads the file -- and it
    # may not exist at all, which is how a `.gitignore` that was never created
    # came to be reported as one setup had modified.
    return SetupStep(
        step_id=StepId.GITIGNORE,
        status=StepStatus.MISSING,
        summary="Derived Theurian artifacts are not ignored by Git.",
        action="Add the Theurian block to .gitignore. Run `theurian init`.",
        critical=False,
    )


# -- 14 & 15. The MCP connection --------------------------------------------


def probe_mcp_connection(context: SetupContext) -> SetupStep:
    config = context.mcp_config
    if not config.is_available():
        return SetupStep(
            step_id=StepId.MCP_CONNECTION,
            status=StepStatus.NOT_APPLICABLE,
            summary="Claude Code is not installed; there is no MCP entry to add.",
            detail=(
                "Theurian serves any MCP client. Point yours at "
                f"{context.connection.url} with an Authorization header."
            ),
        )

    difference = config.difference(context.connection)
    if difference:
        return SetupStep(
            step_id=StepId.MCP_CONNECTION,
            status=StepStatus.CONFLICTING,
            summary="Claude Code already has a different `theurian` MCP entry.",
            detail=difference,
        )
    if config.installed_entry() is None:
        return SetupStep(
            step_id=StepId.MCP_CONNECTION,
            status=StepStatus.MISSING,
            summary="Claude Code has no `theurian` MCP entry.",
            action=(
                f"Add an HTTP MCP server at {context.connection.url}, with the token "
                f"passed as ${{{TOKEN_ENV_VAR}}} rather than a literal value."
            ),
            paths=(str(config.path),),
        )
    return SetupStep(
        step_id=StepId.MCP_CONNECTION,
        status=StepStatus.SATISFIED,
        summary="Claude Code points at this daemon.",
    )


def apply_mcp_connection(context: SetupContext) -> None:
    from theurian.domain.setup import SetupError  # noqa: PLC0415 - avoids a cycle

    failure = context.mcp_config.install(context.connection)
    if failure:
        raise SetupError(failure)


def probe_mcp_health(context: SetupContext) -> SetupStep:
    """Whether the daemon answers MCP at all.

    Never critical: a Theurian whose knowledge is built and whose daemon runs is
    useful even if this machine's Claude Code cannot reach it yet, and reporting
    that in ``degraded`` beats rolling back everything that did work (§6.1).
    """
    if context.health() is None:
        return SetupStep(
            step_id=StepId.MCP_HEALTH,
            status=StepStatus.NOT_APPLICABLE,
            summary="No daemon is running, so the MCP endpoint cannot be checked.",
        )
    return SetupStep(
        step_id=StepId.MCP_HEALTH,
        status=StepStatus.SATISFIED,
        summary=f"The daemon answers on {context.connection.url}.",
    )


# -- 16 & 17. Knowledge state ------------------------------------------------


def probe_migrations(context: SetupContext) -> SetupStep:
    """Reports; never repairs. Migrations are Git-tracked authored content, and
    setup has no business editing them (§6.2 row 16)."""
    root = context.project_root
    if root is None:
        return SetupStep(
            step_id=StepId.MIGRATIONS_VALID,
            status=StepStatus.NOT_APPLICABLE,
            summary="Not inside a Git repository.",
        )
    paths = ProjectPaths.of(root)
    if not paths.migrations.is_dir():
        return SetupStep(
            step_id=StepId.MIGRATIONS_VALID,
            status=StepStatus.NOT_APPLICABLE,
            summary="No migrations directory yet.",
        )
    count = len(list(paths.migrations.glob("*.yaml")))
    return SetupStep(
        step_id=StepId.MIGRATIONS_VALID,
        status=StepStatus.SATISFIED,
        summary=f"{count} migration(s) found. Run `theurian migrate validate` to check them.",
    )


def probe_initial_index(context: SetupContext) -> SetupStep:
    """Building a retrieval index. Not applicable until Milestone 5.

    Reported rather than omitted, so that a report never silently lacks a step
    the specification lists.
    """
    root = context.project_root
    if root is None:
        return SetupStep(
            step_id=StepId.INITIAL_INDEX,
            status=StepStatus.NOT_APPLICABLE,
            summary="Not inside a Git repository.",
        )
    built = read_active_state(ProjectPaths.of(root)) is not None
    return SetupStep(
        step_id=StepId.INITIAL_INDEX,
        status=StepStatus.NOT_APPLICABLE,
        summary=(
            "Knowledge state is built."
            if built
            else "No knowledge state built yet. Run `theurian migrate apply`."
        ),
        detail="Retrieval indexes arrive in Milestone 5; there is nothing to build yet.",
    )


# -- 18. Serena --------------------------------------------------------------


def probe_serena(context: SetupContext) -> SetupStep:
    """Detected and reported. Never modified (§18).

    They answer different questions: Theurian answers "what did we decide and
    why", Serena answers "where is this symbol defined".
    """
    if context.mcp_config.serena_detected():
        return SetupStep(
            step_id=StepId.SERENA_DETECTION,
            status=StepStatus.SATISFIED,
            summary="Serena is configured. Theurian coexists with it; nothing was changed.",
        )
    return SetupStep(
        step_id=StepId.SERENA_DETECTION,
        status=StepStatus.NOT_APPLICABLE,
        summary="Serena is not configured.",
    )


#: Every step, in the order §6.2 lists them. The order is the contract: the
#: token must exist before the env file references it, and the service must be
#: registered before anything tries to start it.
STEPS: Final[tuple[Step, ...]] = (
    Step(StepId.PLATFORM, probe_platform),
    Step(StepId.CORE_PRESENT, probe_core),
    Step(StepId.ARTIFACT_INTEGRITY, probe_artifact_integrity),
    Step(StepId.DATA_DIRECTORY, probe_data_directory, apply_data_directory),
    Step(StepId.TOKEN, probe_token, apply_token),
    Step(StepId.TOKEN_STORAGE, probe_token_storage, apply_token_storage),
    Step(StepId.ENV_REFERENCE, probe_env_reference, apply_env_reference),
    Step(StepId.DAEMON_SERVICE, probe_daemon_service, apply_daemon_service),
    Step(StepId.DAEMON_RUNNING, probe_daemon_running, apply_daemon_running, critical=False),
    Step(StepId.SINGLE_INSTANCE, probe_single_instance),
    Step(StepId.PROJECT_REGISTERED, probe_project_registered, critical=False),
    Step(StepId.PROJECT_LAYOUT, probe_project_layout, critical=False),
    Step(StepId.GITIGNORE, probe_gitignore, critical=False),
    Step(StepId.MCP_CONNECTION, probe_mcp_connection, apply_mcp_connection, critical=False),
    Step(StepId.MCP_HEALTH, probe_mcp_health, critical=False),
    Step(StepId.MIGRATIONS_VALID, probe_migrations, critical=False),
    Step(StepId.INITIAL_INDEX, probe_initial_index, critical=False),
    Step(StepId.SERENA_DETECTION, probe_serena, critical=False),
)
