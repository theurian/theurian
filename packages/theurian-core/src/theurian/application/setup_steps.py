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

**Several steps have no apply at all**, and are declared with an explicit
``None``. They exist to say what is undone and name the command that does it:
§6.2 rows 11-13 are `theurian init` and `theurian project register`, and setup
performs neither. Such a step names no paths in any arm, because ``paths`` is
read as "setup writes here" by the plan shown before consent and by the
changed-files list. It does not have to remember: :meth:`SetupService._probe`
drops them, the same way it takes criticality from the definition rather than
from the probe.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
from theurian.application.setup_withholding import (
    ANOTHER_DATA_DIRECTORY,
    unreadable_registry_summary,
    withheld_difference,
    withheld_registry_detail,
)
from theurian.domain.extras import (
    DAEMON_EXTRA,
    DAEMON_EXTRA_REMEDY,
    DAEMON_INSTALLERS,
    DAEMON_MODULES,
)
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
    #:
    #: Deliberately **not** defaulted. A default makes ``Step(id, probe)``
    #: type-check, so an edit that drops a real action produces a plausible
    #: degraded run instead of the ``Missing positional argument "apply"`` that
    #: catches it before anything is committed. Every report-only step spells
    #: the ``None`` out.
    apply: Callable[[SetupContext], None] | None
    #: A failure here rolls the run back rather than degrading it. Inert on a
    #: step whose ``apply`` is ``None``: nothing there can fail, and
    #: `_blocking_conflicts` consults only PLATFORM and CORE_PRESENT. Set
    #: anyway, because it records what §6.2 says the step *is* rather than what
    #: today's runner happens to read -- a step that later gains an action must
    #: not acquire rollback authority silently along with it.
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


def _missing_daemon_modules() -> tuple[str, ...]:
    """Which of the ``daemon`` extra's modules this interpreter cannot import.

    ``find_spec`` rather than ``import``, because importing ``uvicorn`` and the
    MCP SDK costs around 430 ms and this runs inside ``setup`` and ``doctor``.

    A raise counts as missing alongside the documented ``None``. ``find_spec``
    returns ``None`` for a module that was never installed -- the case a bare
    install produces -- and raises for a package whose parent is absent or whose
    ``sys.modules`` entry has no ``__spec__``. Both mean the same thing to the
    caller, and letting the second escape would abort setup with an import
    traceback in place of the sentence written for it.
    """
    missing: list[str] = []
    for name in DAEMON_MODULES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return tuple(missing)


def _is_runnable_absolute_path(candidate: Path) -> bool:
    """Whether a service manager could exec ``candidate`` as it is written.

    Deliberately not ``exists()``: that is true of a directory, and it resolves
    a relative name against whatever directory setup happened to run in, while
    the string is written verbatim into a launchd plist or a systemd unit.
    """
    return candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK)


def probe_core(context: SetupContext) -> SetupStep:
    """Whether Core is installed *and* can do what the rest of the plan needs.

    Two things, because the step's name is read as one. The executable has to be
    nameable by an absolute path -- the service unit invokes it that way, since
    launchd and systemd start with a PATH that is not the user's, so an
    installation reachable only through a shell alias or a virtualenv on
    ``PATH`` produces a service that cannot start.

    **The predicate is "a service manager could exec this", and it is checked
    here rather than assumed of the caller.** Three shapes reported ``satisfied``
    while failing it, all measured: a bare name, which ``exists()`` resolves
    against the current working directory; a *directory*, which ``exists()`` is
    also true of; and a regular file without the executable bit. The last two
    survived the first fix for #49, which is why the check now names all three
    predicates instead of the one the summary happened to mention.

    ``is_file`` **and** ``os.access(X_OK)``, because neither alone is enough and
    each admits precisely what the other rejects: ``X_OK`` is true of a
    directory, where the bit means "searchable", and ``is_file`` is true of a
    0644 file. Both follow symlinks on purpose -- ``uv tool install`` and
    ``pipx`` put a symlink on ``PATH`` pointing into the tool's virtualenv, and
    that is what ``shutil.which`` hands back, so ``lstat`` semantics here would
    reject every real installation.

    ``_executable()`` resolves before it returns and ``shutil.which`` answers
    ``None`` for all three shapes, so no shipped caller reaches them today. The
    requirement is still the check's to state rather than its current caller's
    -- the same argument that closed the bare-name case, applied to the rest of
    it (#49).

    And the ``daemon`` extra has to be present. That arm exists because the path
    check alone reported ``satisfied`` for ``uv tool install theurian`` -- the
    command every install surface names -- whose very next ``theurian daemon
    start`` failed with ``ModuleNotFoundError: No module named 'uvicorn'``
    (#78). A step that asserts presence by finding a file was making a claim
    about the flow that follows it, and every remaining step in that flow either
    serves the daemon or connects a client to it.

    ``CONFLICTING`` rather than ``MISSING``, which makes this one of the two
    conflicts consent cannot buy past: setup stops and writes nothing. Measured
    on the other branch -- a bare install ran through to ``DEGRADED`` and left an
    env file, a service unit and an MCP entry behind, so the machine ended with a
    registered service that fails on every start. "Leaves nothing worth
    installing around it" is what that set is for.

    **The extra is checked in this interpreter; the path is checked on disk.**
    They are the same installation whenever ``_executable()`` resolved
    ``theurian`` to the process running setup, which is every ordinary
    invocation, and not when a *different* ``theurian`` comes first on ``PATH``.
    Recorded rather than closed: nothing here can import from another
    installation's site-packages, and the alternative -- shelling out to the
    named executable on every ``doctor`` run -- buys a subprocess to cover the
    case of a user who already has two Cores.
    """
    executable = Path(context.executable) if context.executable else None
    if executable is None or not _is_runnable_absolute_path(executable):
        return SetupStep(
            step_id=StepId.CORE_PRESENT,
            status=StepStatus.CONFLICTING,
            summary="Could not determine an absolute path to the theurian executable.",
            detail=(
                f"The daemon service must invoke Theurian by absolute path, because a "
                f"service manager starts with a PATH that is not your shell's. Install "
                f"Theurian with `{DAEMON_INSTALLERS[0]}` or `{DAEMON_INSTALLERS[1]}`."
            ),
        )

    missing = _missing_daemon_modules()
    if missing:
        return SetupStep(
            step_id=StepId.CORE_PRESENT,
            status=StepStatus.CONFLICTING,
            summary=f"Core is installed, but without its `{DAEMON_EXTRA}` extra.",
            detail=(
                f"Everything setup does from here serves the daemon or connects a client "
                f"to it, and {', '.join(missing)} cannot be imported. "
                f"{DAEMON_EXTRA_REMEDY} Then run `theurian setup` again."
            ),
        )

    return SetupStep(
        step_id=StepId.CORE_PRESENT,
        status=StepStatus.SATISFIED,
        summary=f"Core is installed at {context.executable}.",
    )


# -- 3. Artifact integrity --------------------------------------------------


def probe_artifact_integrity(_: SetupContext) -> SetupStep:
    """§6.2 row 3: verify the installed artifact against the release manifest.

    Theurian does not do it, and this step's business is to say so. Setup never
    downloads or installs Core -- it runs from the very artifact it would have to
    check -- and no code in the package hashes one and compares it to a published
    checksum. A step that reported ``satisfied`` without checking anything would
    be a false assurance about supply chain integrity (T-16), so the gap is
    published as ``NOT_APPLICABLE`` rather than hidden.

    **The premise is a property of Theurian, deliberately never one of the
    world.** These strings previously read "No signed release manifest exists
    yet; nothing to verify against" and "Artifact verification arrives with the
    first tagged release". Both were true when they were written and both turn
    false at the first ``core-v*`` tag, which publishes ``SHA256SUMS`` and a
    CycloneDX SBOM on the GitHub release: the summary would tell every user not
    to bother checking a record that is sitting in front of them -- cancelling
    the only mitigation they have -- and the detail would become an overdue
    promise. One function ships on both sides of that boundary, so it asserts
    nothing the boundary moves. The schedule belongs to an issue, which has an
    owner; a string does not.
    """
    return SetupStep(
        step_id=StepId.ARTIFACT_INTEGRITY,
        status=StepStatus.NOT_APPLICABLE,
        summary="Theurian does not verify the artifact it is running from.",
        detail=(
            "Setup never downloads or installs Core, and no code in Theurian compares an "
            "installed artifact against a checksum. Checking a download against the "
            "checksums published with it is a manual step, tracked at "
            "https://github.com/theurian/theurian/issues/39 (T-16)."
        ),
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

    directory = str(context.data_dir)
    # Both called through the port rather than reached with `getattr`. A manager
    # missing `differing_keys` used to answer `None` silently, which published a
    # withholding sentence naming no field at all.
    difference = service.differs_from_installed(port=context.port, data_directory=directory)
    if difference:
        return SetupStep(
            step_id=StepId.DAEMON_SERVICE,
            status=StepStatus.CONFLICTING,
            summary="A service is registered with a different definition.",
            detail=(
                withheld_difference(
                    _service_path(context),
                    service.differing_keys(port=context.port, data_directory=directory),
                )
                if context.for_publication
                else difference
            ),
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
    """Where this platform's service definition lives.

    Still ``getattr``, unlike the two comparison calls beside it, because the
    attribute genuinely differs by platform -- ``plist_path`` or ``unit_path`` --
    and neither is on the port. It fails to ``""``, which reaches a report as a
    sentence missing its subject rather than as a disclosure, so it is left for
    the milestone that gives :class:`DaemonManager` a definition path.
    """
    service = context.service
    for attribute in ("plist_path", "unit_path"):
        path = getattr(service, attribute, None)
        if path is not None:
            return str(path)
    return ""  # pragma: no cover - both adapters expose one


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
        # The other daemon's directory came off the wire from a process this one
        # does not own, so it is a path the local context never held and no
        # anchor in `cli/setup_commands._redacted` can reach. Which directory it
        # is only matters on the terminal of the person who has to go and stop
        # it; a reader of a public issue needs to know that it is not this one.
        served = ANOTHER_DATA_DIRECTORY if context.for_publication else running_dir
        return SetupStep(
            step_id=StepId.SINGLE_INSTANCE,
            status=StepStatus.CONFLICTING,
            summary="The daemon on this port serves a different data directory.",
            detail=(
                f"Port {context.port} is held by a Theurian serving {served}, not "
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

    Every summary here names the repository by its **whole path**, never by
    ``root.name``. `doctor --report` redacts by substituting known paths, and a
    bare directory name is not a path: it survived every anchor and published
    the repository's name into output meant for a public issue (O-3). The full
    path redacts to ``<repository>`` and, unredacted, says which checkout --
    which is the more useful answer to "not registered where?" anyway.
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
            summary=unreadable_registry_summary(registry, root),
            detail=(
                withheld_registry_detail(registry)
                if context.for_publication
                else f"{exc} {exc.remedy}".strip()
            ),
        )
    if found:
        return SetupStep(
            step_id=StepId.PROJECT_REGISTERED,
            status=StepStatus.SATISFIED,
            summary=f"{root} is registered.",
        )
    # `paths` is empty for the same reason it is empty above: this step has no
    # apply, so setup never writes `registry.path` whatever the user decides.
    # Naming it there put the file in the plan's "would be created or modified"
    # list and then in `changed_paths`, for a run that only ever read it.
    #
    # The location moves into the summary rather than being dropped. It is the
    # one fact here that is not recoverable from the rest of the step, and
    # `paths` was carrying it by accident -- somebody asking "not registered
    # *where*?" was the only reader that field served honestly.
    return SetupStep(
        step_id=StepId.PROJECT_REGISTERED,
        status=StepStatus.MISSING,
        summary=f"{root} has no entry in {registry.path}.",
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
    exists = gitignore.is_file()
    contents = gitignore.read_text(encoding="utf-8") if exists else ""
    if ".theurian/state" in contents:
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.SATISFIED,
            summary="Derived Theurian artifacts are ignored.",
        )
    # No `paths`: `init` appends the block, setup only reads the file, and the
    # file may not be there at all -- which is how a `.gitignore` that was never
    # created came to be reported as one setup had modified.
    #
    # Two summaries for one status, because the file's absence and its silence
    # are different things to be told and the reader acts on them differently.
    # Naming the path is what removing `paths` has to compensate for; saying "it
    # does not ignore" of a file that is not there was how the first attempt at
    # that got it wrong.
    return SetupStep(
        step_id=StepId.GITIGNORE,
        status=StepStatus.MISSING,
        summary=(
            f"{gitignore} has no Theurian block."
            if exists
            else f"{gitignore} does not exist, so nothing ignores the derived artifacts."
        ),
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
            detail=(
                withheld_difference(
                    f"The Theurian MCP entry in {config.path}",
                    config.differing_keys(context.connection),
                )
                if context.for_publication
                else difference
            ),
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
    Step(StepId.PLATFORM, probe_platform, None),
    Step(StepId.CORE_PRESENT, probe_core, None),
    Step(StepId.ARTIFACT_INTEGRITY, probe_artifact_integrity, None),
    Step(StepId.DATA_DIRECTORY, probe_data_directory, apply_data_directory),
    Step(StepId.TOKEN, probe_token, apply_token),
    Step(StepId.TOKEN_STORAGE, probe_token_storage, apply_token_storage),
    Step(StepId.ENV_REFERENCE, probe_env_reference, apply_env_reference),
    Step(StepId.DAEMON_SERVICE, probe_daemon_service, apply_daemon_service),
    Step(StepId.DAEMON_RUNNING, probe_daemon_running, apply_daemon_running, critical=False),
    Step(StepId.SINGLE_INSTANCE, probe_single_instance, None),
    Step(StepId.PROJECT_REGISTERED, probe_project_registered, None, critical=False),
    Step(StepId.PROJECT_LAYOUT, probe_project_layout, None, critical=False),
    Step(StepId.GITIGNORE, probe_gitignore, None, critical=False),
    Step(StepId.MCP_CONNECTION, probe_mcp_connection, apply_mcp_connection, critical=False),
    Step(StepId.MCP_HEALTH, probe_mcp_health, None, critical=False),
    Step(StepId.MIGRATIONS_VALID, probe_migrations, None, critical=False),
    Step(StepId.INITIAL_INDEX, probe_initial_index, None, critical=False),
    Step(StepId.SERENA_DETECTION, probe_serena, None, critical=False),
)
