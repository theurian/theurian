"""The individual setup steps of §6.2, and the one step §6.2 predates.

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

from theurian.application.authorization import (
    DISCLOSURE_ORDER,
    ServingProfileError,
    load_serving_profile,
    serving_profile_path,
)
from theurian.application.project_service import (
    ProjectError,
    ProjectPaths,
    ProjectRegistry,
    locate_gitignore_block,
    read_active_state,
    render_gitignore_block,
)
from theurian.application.setup_context import SetupContext
from theurian.application.setup_withholding import (
    ANOTHER_DATA_DIRECTORY,
    failure_detail,
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
from theurian.domain.project import GITIGNORE_ENTRIES
from theurian.domain.setup import SetupStep, StepId, StepStatus
from theurian.security.env_file import (
    ENV_BLOCK_START,
    TOKEN_KEY,
    MalformedEnvBlockError,
    contains_current_block,
    contains_shadowing_assignment,
    merge_env_file,
)
from theurian.security.paths import is_world_accessible
from theurian.security.tokens import MIN_TOKEN_LENGTH, TOKEN_ENV_VAR, generate_token

# The marker pair that used to be declared here as `PROFILE_BEGIN`/`PROFILE_END`
# now lives with the text it delimits, as
# :data:`~theurian.security.env_file.ENV_BLOCK_START`. Nothing ever read the
# constants here -- no step writes a shell profile -- while the claim attached to
# them, that only the block between them is rewritten, was false of the one file
# setup does write until #128.

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
    #: A failure here halts the run rather than degrading it. Inert on a
    #: step whose ``apply`` is ``None``: nothing there can fail, and
    #: `_blocking_conflicts` consults only PLATFORM and CORE_PRESENT. Set
    #: anyway, because it records what §6.2 says the step *is* rather than what
    #: today's runner happens to read -- a step that later gains an action must
    #: not acquire halt authority silently along with it.
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
    """Whether ``candidate`` is an absolute path to a regular file marked executable.

    Exactly those three things, and **not** "a service manager could exec this".
    That stronger claim was written here and is false: five shapes satisfy all
    three predicates and still fail to exec, measured on macOS --

    ===================================================  =======================
    shape                                                ``execve`` answers
    ===================================================  =======================
    0755 script whose shebang names a removed binary     ENOENT
    0755 console script whose shebang is a dangling link ENOENT
    0755 text with no shebang                            ENOEXEC
    0755 zero-byte file                                  ENOEXEC
    0755 Linux ELF on macOS                              ENOEXEC
    ===================================================  =======================

    All five are file *format* and shebang, which no ``stat`` can see. Nothing
    short of running the thing separates them, and running it would still be a
    check-to-use race, so the predicate is stated at the width it actually has.

    Deliberately not ``exists()``: that is true of a directory, and it resolves
    a relative name against whatever directory setup happened to run in, while
    the string is written verbatim into a launchd plist or a systemd unit.
    ``is_file`` also rejects FIFOs, dangling symlinks, symlink loops and
    directories, which is what it earns its place for.
    """
    return candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK)


def probe_core(context: SetupContext) -> SetupStep:
    """Whether Core is installed *and* can do what the rest of the plan needs.

    Two things, because the step's name is read as one. The executable has to be
    nameable by an absolute path -- the service unit invokes it that way, since
    launchd and systemd start with a PATH that is not the user's, so an
    installation reachable only through a shell alias or a virtualenv on
    ``PATH`` produces a service that cannot start.

    **Three shapes reported ``satisfied`` that no service manager could start**,
    all measured: a bare name, which ``exists()`` resolves against the current
    working directory; a *directory*, which ``exists()`` is also true of; and a
    regular file without the executable bit. The last two survived the first fix
    for #49, which is why the check names three predicates rather than the one
    the summary happened to mention.

    What that check establishes is narrower than "Core will start", and
    :func:`_is_runnable_absolute_path` records the five shapes that pass it and
    still fail to exec. This step does not close that gap and does not claim to;
    a Core in any of those states could not have run the ``setup`` command that
    is asking.

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


# -- 6a. The deployment serving profile (#119, ADR-0025) ---------------------


def probe_serving_profile(context: SetupContext) -> SetupStep:
    """Which sensitivity ceiling this deployment serves, and whether it is honoured.

    The only security setting `doctor` did not look at, and the one that decides
    what every ``knowledge.search`` may return. A profile the loader refuses
    stops the daemon from starting and refuses every ``theurian index build``,
    and until this step existed the health check had nothing to say about either.

    **Three statuses, and none of them is ``MISSING``.** ``MISSING`` is
    ``would_change``, so `doctor` counts it a problem and exits 1 -- and an
    undeclared ceiling is the ordinary state of every deployment, cleared by no
    command Theurian ships. A permanent non-zero exit that nothing can clear is
    how a health check stops being read (§6.2), and
    ``test_doctor_names_a_remedy_for_every_problem`` would demand an ``action``
    naming a command that does not exist. So:

    - **not-applicable** -- no profile declared. Reported rather than omitted,
      with the level in force named, because an operator who has never opened
      that file has no other way to learn what their deployment withholds. This
      is ``probe_serena``'s shape: probed, found not to apply, and said so.
    - **satisfied** -- a profile is declared and honoured, and the summary names
      the ceiling. That is the split ``mcp/search.py``'s ``_PROFILE_MISMATCH``
      note defers here: a degraded search never names a level to an agent, and
      this is the operator at their own terminal.
    - **conflicting** -- the file is present and cannot be honoured. Never
      ``MISSING``: setup overwrites nothing it did not install, and this file is
      the operator's own (SEC-18), so what a conflict buys is consent to proceed
      past it. The detail carries the refusal's own remedy, which is the only
      text that says what would have worked.

    ``paths`` is empty on every arm, because no arm writes: setup neither creates
    a profile nor repairs one, and a path listed there is read as "setup would
    touch this" by the plan and by ``changedPaths``.

    The word from the file reaches ``detail`` on the conflicting arm only, and
    only when the report is not for publication. ``UnknownSensitivityCeilingError``
    echoes what it read -- deliberately, since an operator cannot fix a typo they
    cannot see -- and that is a value Theurian did not author, so
    ``doctor --report`` gets the type name instead (:func:`failure_detail`).
    """
    path = serving_profile_path(context.data_dir)
    try:
        profile = load_serving_profile(context.data_dir)
    except ServingProfileError as exc:
        return SetupStep(
            step_id=StepId.SERVING_PROFILE,
            status=StepStatus.CONFLICTING,
            summary=f"{path} does not declare a ceiling Theurian can honour.",
            detail=(
                failure_detail(exc, for_publication=True)
                if context.for_publication
                else f"{exc} {exc.remedy}".strip()
            ),
        )

    # `is_file` and never `exists`, which follows a symlink and answers False for
    # a dangling one -- the widening `load_serving_profile` was corrected for.
    # Reached only after a successful load, which leaves exactly two shapes: no
    # entry at all, or a regular file the loader read. Every other shape raised.
    if not path.is_file():
        return SetupStep(
            step_id=StepId.SERVING_PROFILE,
            status=StepStatus.NOT_APPLICABLE,
            summary=(
                f"No deployment serving profile is declared, so this deployment serves "
                f"{profile.ceiling.value} and below."
            ),
            detail=(
                f"Write one of {', '.join(level.value for level in DISCLOSURE_ORDER)} into "
                f"{path} at mode 0600 to declare a different ceiling, then rebuild each "
                f"project's index with `theurian index build`."
            ),
        )

    return SetupStep(
        step_id=StepId.SERVING_PROFILE,
        status=StepStatus.SATISFIED,
        summary=f"This deployment serves {profile.ceiling.value} and below.",
    )


# -- 7. Env reference -------------------------------------------------------


def probe_env_reference(context: SetupContext) -> SetupStep:
    """The file that exports the token by *reference* (SEC-5).

    The question is whether *the Theurian block* is current, not whether the
    file matches something. Comparing the whole file answered ``Missing`` for
    every user who had appended a line to it -- and the apply then rewrote the
    file from scratch, which is how a line somebody added disappeared with no
    diff and no backup (#128). §6.2 row 7 says "the Theurian-owned block only",
    and this is that sentence in both arms: what is compared, and what is
    written.

    Blind to the lines around the block, but not to what they appear to *do*: a
    line below it assigning the same variable is what the shell keeps, so the
    arm that finds one reports the block current and says so rather than
    claiming the file exports the token by reference.

    That arm rests on a heuristic.
    :func:`~theurian.security.env_file.contains_shadowing_assignment` recognises
    the direct assignment forms and no others, and its docstring tabulates the
    shapes that evade it and the one that trips it. Both sentences this step
    publishes therefore say the line *appears* to assign: a shape the heuristic
    misses leaves the final arm's "exports it by reference" standing, which is
    the failure this step inherits and does not close.
    """
    path = context.env_file
    if not path.is_file():
        return SetupStep(
            step_id=StepId.ENV_REFERENCE,
            status=StepStatus.MISSING,
            summary=f"{path} does not exist.",
            action=f"Write {path}, exporting {TOKEN_ENV_VAR} from the token file.",
            paths=(str(path),),
        )

    try:
        content = _read_env_file(path)
        current = contains_current_block(content, context.data_dir)
    except MalformedEnvBlockError as exc:
        # Not a difference setup can resolve: the markers are what tells it
        # which lines are its own. Reported as a conflict, which is never
        # applied -- so the file is left exactly as its owner left it, and the
        # detail says what to do about it (SEC-18).
        return SetupStep(
            step_id=StepId.ENV_REFERENCE,
            status=StepStatus.CONFLICTING,
            summary=f"The Theurian block in {path} is not delimited.",
            # Marker text and the path only. Every other byte of that file was
            # written by somebody else, and `doctor --report` publishes this.
            detail=f"{path}: {exc}",
        )

    if not current:
        return SetupStep(
            step_id=StepId.ENV_REFERENCE,
            status=StepStatus.MISSING,
            summary=f"The Theurian block in {path} is missing or out of date.",
            action=f"Rewrite the Theurian-owned block in {path}. Other lines are left as they are.",
            paths=(str(path),),
        )

    if contains_shadowing_assignment(content):
        # Satisfied, because the block *is* current and applying this step would
        # write the same bytes -- and reported, because the machine may well not
        # export what the block says. Not a conflict: a conflict asks the user
        # for consent to proceed past it, and there is nothing here setup wants
        # to do. `SetupService` turns this detail into a warning, which is what
        # ends a real run DEGRADED instead of CONVERGED and what puts it in
        # `doctor`'s payload.
        return SetupStep(
            step_id=StepId.ENV_REFERENCE,
            status=StepStatus.SATISFIED,
            summary=(
                f"The Theurian block in {path} is current, but a later line appears to override it."
            ),
            # The path, the variable and the marker -- all Theurian's own text.
            # Not the offending line: `doctor --report` publishes this, and
            # somebody wrote that line for their own reasons. Which also means
            # the reader cannot see what was matched, so the sentence has to
            # carry its own uncertainty rather than lean on them checking.
            detail=(
                f"A line below the block in {path} appears to assign {TOKEN_ENV_VAR} again, "
                f"and a shell keeps the last assignment it reads. Theurian does not edit "
                f"lines outside its markers: remove that line, or move it above "
                f"{ENV_BLOCK_START!r}, for the block's value to be the one your shell exports."
            ),
        )

    return SetupStep(
        step_id=StepId.ENV_REFERENCE,
        status=StepStatus.SATISFIED,
        summary=f"{path} exports {TOKEN_ENV_VAR} by reference.",
    )


def _read_env_file(path: Path) -> str:
    """The env file exactly as it is on disk, ``\\r`` bytes and all.

    ``newline=""`` on the *read*, which is half of a byte-for-byte promise that
    the write cannot keep on its own: universal newline translation turns every
    ``\\r\\n`` into ``\\n`` before the merge ever sees the file, so a rewrite of
    the block silently rewrote the line endings of lines it does not own -- and
    a ``\\r`` inside a quoted value, ``export GREETING="hello\\rworld"``, came
    back as a newline that splits the assignment in two. Measured on a CRLF file
    through the real ``SetupService``: two ``\\r`` bytes in, zero out.
    """
    return path.read_text(encoding="utf-8", newline="")


def apply_env_reference(context: SetupContext) -> None:
    """Rewrite the Theurian-owned block, leaving every other line alone (#128).

    Read, merge, then write -- in that order, and the order is the point. The
    merge is computed before the file is opened, so a failure to read it, or a
    file whose markers cannot be delimited, leaves the original untouched
    rather than truncated. What cannot be deferred is the truncation the write
    itself performs, so the merged content exists in full before the ``open``
    that shortens the file to nothing.

    **In place, rather than written beside and renamed over.** Not because a
    rename cannot be made atomic here -- ``os.replace`` onto ``path.resolve()``
    keeps a symlink into a dotfiles repository pointing where it pointed, which
    is what the note here used to deny. The reasons are the ones a rename brings
    with it: the temporary file needs write permission in the *target's*
    directory, which a read-only dotfiles checkout does not give; a failure
    between the write and the rename leaves that temporary file behind in a
    directory whose whole contents a person curates; and a rename replaces the
    inode, so any hard link to this file stops following it. An in-place write
    trades atomicity for those three, and the trade is recorded rather than
    inherited.

    **A buffered writer and not a bare ``os.write``**, for the reason
    :meth:`~theurian.application.setup_service.SetupService._journal` adopted
    one: ``write(2)`` may write fewer bytes than it was handed and return that
    count without raising, and a short write here now destroys lines Theurian
    did not author. :class:`io.BufferedWriter` loops until the buffer is empty
    and raises what the flush or the close hit, which reaches the runner as a
    failed step instead of a silently truncated file.
    """
    path = context.env_file
    path.parent.mkdir(parents=True, exist_ok=True, mode=_DATA_DIR_MODE)
    existing = _read_env_file(path) if path.is_file() else None
    merged = merge_env_file(existing, context.data_dir)
    # 0600 from the moment it is created: the file names the token's location,
    # and its whole purpose is to be sourced by its owner alone. The builtin
    # and not `Path.open`, which takes no `opener` -- and the opener is what
    # carries the creation mode. `newline=""` for the reason `_read_env_file`
    # passes it: what this writes is what the merge produced, byte for byte.
    with open(
        path,
        "w",
        encoding="utf-8",
        newline="",
        opener=lambda file, flags: os.open(file, flags, 0o600),
    ) as handle:
        handle.write(merged)
    # Re-asserted, because the mode above is ANDed with the umask and because a
    # file an earlier version created keeps whatever mode it was given.
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
    """Whether the managed block is there and current (ADR-0004, O-2).

    The predicate is **block identity**, not entry presence: exactly one
    well-formed marker pair, and the span between the markers byte-for-byte the
    block `theurian init` writes. That is deliberately the same question
    :func:`ensure_gitignore` answers when it decides whether to rewrite, and it
    is asked through the same two functions so the two cannot drift.

    Entry presence was the old check, over the whole file, and a substring is not
    a rule (#87). Two files satisfied it while Git ignored nothing: every entry
    prefixed with ``!``, which is the syntax for *un*-ignoring, and every entry
    prefixed with ``# ``, which is the syntax for not writing a rule. Measured
    against the first, ``git check-ignore .theurian/state/index.db`` exits 1
    while the step reported ``satisfied`` -- so ADR-0004's derived artifacts and
    ADR-0028's deliberately machine-local `.theurian/proposals-local/` were
    committable on a machine `doctor` called converged.

    A third file satisfied it for a reason that is not a disclosure: the managed
    entries written by hand, with no markers. Those rules do ignore what they
    name. What is wrong there is the *next* `theurian init`, which finds no block
    to rewrite, appends its own, and leaves the file carrying two lists that
    drift apart with nothing to say so -- which is why the step is stricter than
    "is it ignored" and says why in its own sentence.

    HIGH-2 (#49) is the same class one step earlier and stays closed by
    construction: a stale block -- every project initialised before ADR-0028
    added `.theurian/proposals-local/` -- is not the rendered block, and the
    summary names the entry a re-run brings in.

    ``newline=""`` on the read is load-bearing. `ensure_gitignore` reads and
    writes with it so a CRLF file is not silently rewritten end to end, and it
    *does* rewrite a CRLF block (measured, ``changed=True``); a universal-newline
    read here would find the rendered block in what it read and report satisfied
    for a file every `theurian init` changes.
    """
    root = context.project_root
    if root is None:
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.NOT_APPLICABLE,
            summary="Not inside a Git repository.",
        )
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return _gitignore_missing(
            f"{gitignore} does not exist, so nothing ignores the derived artifacts."
        )

    content = gitignore.read_text(encoding="utf-8", newline="")
    try:
        span = locate_gitignore_block(content, gitignore)
    except ProjectError:
        # The refusal's own text is not published: it names the marker line and
        # the path, both Theurian's, but the remedy it carries tells the reader
        # to re-run `theurian init` -- and that command refuses a file in this
        # state. The action here says what to repair instead.
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.MISSING,
            summary=(
                f"{gitignore}'s Theurian block markers are malformed; "
                f"`theurian init` refuses the file in this state."
            ),
            action="Repair the Theurian block markers by hand, then run `theurian init`.",
            critical=False,
        )

    if span is None:
        return _gitignore_missing(
            f"{gitignore} has no Theurian block. Rules written by hand are not evaluated here."
        )
    return _managed_block_verdict(gitignore, content[span[0] : span[1]])


def _managed_block_verdict(gitignore: Path, managed: str) -> SetupStep:
    """What to say about a well-formed block that is or is not the current one.

    *managed* is the span between the markers, and every question below is asked
    of it alone -- never of the whole file. An entry below the end marker ignores
    what it names today and is not in what the next `theurian init` rewrites, so
    crediting it would report a block as complete that comes back incomplete,
    leaving the user's own line as the only thing ignoring an ADR-0028 directory
    in a file they may reasonably tidy.
    """
    if managed == render_gitignore_block():
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.SATISFIED,
            summary=f"{gitignore}'s Theurian block is present and current.",
        )
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in managed]
    if missing:
        return _gitignore_missing(
            f"{gitignore}'s Theurian block is out of date: it does not ignore {', '.join(missing)}."
        )
    return _gitignore_missing(
        f"{gitignore}'s Theurian block differs from the one `theurian init` writes."
    )


def _gitignore_missing(summary: str) -> SetupStep:
    """One MISSING step, whichever way the block is not the current one.

    Five summaries for one status, because a file that is absent, one that is
    silent, one whose block is stale, one whose block was edited, and one whose
    markers cannot be acted on are different things to be told and the reader
    acts on them differently. The path is named in every one: `init` appends the
    block and setup only ever reads the file, so this step names no ``paths`` --
    which is how a `.gitignore` that was never created came to be reported as
    one setup had modified.
    """
    return SetupStep(
        step_id=StepId.GITIGNORE,
        status=StepStatus.MISSING,
        summary=summary,
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
    that in ``degraded`` beats halting everything that did work (§6.1).
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
    """Runs the static validation `theurian migrate validate` runs; never repairs.

    Migrations are Git-tracked authored content, and setup has no business
    editing them (§6.2 row 16) -- so no arm here names a path and no arm writes.
    What changed with #91 is that the step now *opens* them: it counted
    ``migrations/*.yaml`` and reported ``satisfied`` for any directory at all, so
    one file of nonsense read as converged while every ``theurian migrate``
    against that project refused.

    The count is the checker's and never a second enumeration taken beside it.
    ``glob("*.yaml")`` is not the loader's answer -- a symlinked or unreadable
    entry is a refusal there rather than a file -- and two commands reporting
    different numbers for the same directory is the same defect in a quieter
    form.

    The checker is injected (:attr:`SetupContext.check_migrations`) rather than
    imported, because loading a migration set means reading YAML off disk against
    the published JSON Schemas and the application layer does not reach for the
    infrastructure loader (ADR-0003).

    **Neither not-applicable arm calls it.** A load is the most expensive thing
    on this step's path and `doctor` runs it on every invocation, so a repository
    with no migrations directory says so without going to the disk.

    A refusal quotes the author's own file, and that is not Theurian's to publish
    (O-3, SEC-6): :func:`failure_detail` puts the message on the operator's
    terminal and the type name in a shared report.
    """
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

    check = context.check_migrations(root)
    if check.failure is None:
        return SetupStep(
            step_id=StepId.MIGRATIONS_VALID,
            status=StepStatus.SATISFIED,
            summary=f"{check.count} migration(s) parse and validate.",
        )
    # MISSING rather than CONFLICTING: there is nothing of the operator's here to
    # consent past -- setup neither edits migrations nor would if it were allowed
    # to -- only a file whose author has to fix it.
    return SetupStep(
        step_id=StepId.MIGRATIONS_VALID,
        status=StepStatus.MISSING,
        summary=f"The migrations in {paths.migrations} do not validate.",
        action="Fix the file it names; `theurian migrate validate` prints the full refusal.",
        detail=failure_detail(check.failure, for_publication=context.for_publication),
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


#: Every step, in the order §6.2 lists them, plus the one step §6.2 predates.
#: The order is the contract: the token must exist before the env file
#: references it, and the service must be registered before anything tries to
#: start it. ``serving-profile`` sits after ``token-storage`` because it is the
#: other operator-owned file in ``auth/``; §6.2 calls it row 6a rather than
#: renumbering the rows it is cited by.
STEPS: Final[tuple[Step, ...]] = (
    Step(StepId.PLATFORM, probe_platform, None),
    Step(StepId.CORE_PRESENT, probe_core, None),
    Step(StepId.ARTIFACT_INTEGRITY, probe_artifact_integrity, None),
    Step(StepId.DATA_DIRECTORY, probe_data_directory, apply_data_directory),
    Step(StepId.TOKEN, probe_token, apply_token),
    Step(StepId.TOKEN_STORAGE, probe_token_storage, apply_token_storage),
    Step(StepId.SERVING_PROFILE, probe_serving_profile, None, critical=False),
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
