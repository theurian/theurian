"""Single-instance enforcement (ADR-0002, NFR-1, T-13).

One daemon per user per machine. Theurian owns a SQLite canonical store, an
index publisher, and RAPTOR build jobs, all of which require a single writer. Two
daemons on one data directory is not slowness — it is corruption.

Three independent mechanisms, because **each one alone has a known failure
mode**:

============================  =========================================
Mechanism                     Fails when
============================  =========================================
OS advisory file lock         the lock file is deleted; some network filesystems
Port health probe             something else holds the port
Startup handshake             (covers the other two: identifies *which* daemon)
============================  =========================================

Together they cover each other. A losing starter exits 0 after confirming the
winner is healthy; it never kills the winner and never repairs data.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Final

from theurian.domain.errors import TheurianError
from theurian.infrastructure.sqlite.connection import CONTENTION_ERRNOS as _CONTENTION_ERRNOS
from theurian.infrastructure.sqlite.connection import LOCK_OPEN_FLAGS
from theurian.infrastructure.sqlite.schema import irregular_shape, irregular_shape_at
from theurian.security.no_follow import (
    irregular_artefact_remedy,
    is_a_symbolic_link_refusal,
    symbolic_link_remedy,
)

if sys.platform == "win32":  # pragma: no cover - Windows is not a 1.0 target
    raise ImportError(
        "Theurian's instance lock is POSIX-only (fcntl). See packaging/windows/README.md."
    )

#: How long to wait for a health probe. Long enough for a daemon that is busy,
#: short enough that a startup check never feels hung.
PROBE_TIMEOUT_SECONDS: Final = 2.0

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 7419

#: Name of the lock file inside the data directory.
#:
#: Here rather than in ``runner.py``, beside the :class:`InstanceLock` that
#: creates it. The move is not tidiness: ``daemon status`` publishes this path as
#: ``lockFile`` and imported the constant from the runner, so reporting "no
#: daemon is running" pulled in ``uvicorn`` and ``mcp`` and died with
#: ``ModuleNotFoundError`` on any install without the ``daemon`` extra -- on the
#: one command the SessionStart hook runs on every session (#78). This module has
#: no third-party import at all, which is the property that keeps that fixed, and
#: ``tests/integration/test_bare_install.py`` holds it.
LOCK_FILENAME: Final = "daemon.lock"


class StartDecision(StrEnum):
    """What a would-be starter should do."""

    START = "start"
    #: A healthy daemon with our data directory already serves. Exit 0.
    REUSE = "reuse"
    #: Something else holds the port, or a different Theurian owns it.
    CONFLICT = "conflict"
    #: A lock exists but nothing answers. Reported, never auto-repaired.
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class InstanceCheck:
    """The result of asking whether this process may start."""

    decision: StartDecision
    detail: str
    existing_version: str | None = None
    existing_data_dir: str | None = None

    @property
    def may_start(self) -> bool:
        return self.decision is StartDecision.START


class InstanceLockError(TheurianError):
    """The instance lock could not be acquired or was found in a bad state.

    **Carries its own remedy** (round two, and the #404 R1-5 shape
    ``WriteLockTimeoutError`` already took). It set none, so it inherited
    ``TheurianError``'s empty default and every caller reading
    ``exc.remedy or <default>`` published "Run `theurian doctor`" -- which
    neither reports an artefact at the lock path nor clears one. The cure has to
    come from the raiser, because only it knows whether the fault is a link, a
    named pipe, a directory that could not be made, or a lock another daemon
    holds.
    """

    def __init__(self, detail: str, *, remedy: str = "") -> None:
        self.remedy = remedy
        super().__init__(detail)


class InstanceLock:
    """An advisory file lock held for the daemon's lifetime.

    A lock file rather than a PID file: PIDs are recycled, so a stale PID file
    can name a live unrelated process, and a "single instance" guarantee built on
    one is a guarantee that silently lapses. An advisory lock is released by the
    kernel when the holder exits, however it exits.

    **The open is the write lock's, imported rather than re-spelled.**
    :data:`~theurian.infrastructure.sqlite.connection.LOCK_OPEN_FLAGS` records
    what each flag is for and the measurement behind it; :meth:`acquire` records
    what this path cost while it had its own spelling. Sharing the constant is
    what makes ``connection.py``'s "same call, same flags, same kind of file"
    true -- it was written while it was not.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _unusable(self, shape: str | None, *, cause: OSError | None) -> InstanceLockError:
        """One refusal for "the instance lock path does not hold a lock file".

        Three call sites reach it and they differ in what they know: the ``open``
        that failed has an errno and no descriptor, the ``fstat`` that followed a
        successful open has a descriptor and no errno, and the symbolic-link arm
        has an errno it must not describe by the *target's* shape. So the shape
        leads wherever it is known and the operating system's own account trails
        when there is one.

        **A symbolic link is answered first, by errno, and not by shape** (round
        two). ``irregular_shape_at`` follows the link, so a link pointing at a
        named pipe was published as "is a named pipe (FIFO)" about a path holding
        a symlink -- true of the target and false of the artefact the operator has
        to remove, which is the same mis-description #520 removed from the write
        lock's own sentence. ``O_NOFOLLOW`` gives ``ELOOP`` for exactly this, and
        :func:`~theurian.security.no_follow.symbolic_link_remedy` is the cure the
        other five sites already publish.

        **The message names the basename and the remedy the absolute path** --
        the split ``WriteLockUnusableError`` records: ``error`` is the field
        quoted into a bug report and ``remedy`` the one pasted into a shell, so
        the operator's absolute path belongs in one of them and not in both.

        **The operating system's own account is load-bearing, not decoration.**
        For a directory or a link there is no shape to publish, so ``strerror``
        is the only thing in the message that distinguishes one refusal from the
        next -- which is why the driving tests assert it rather than only the
        noun phrase.
        """
        if cause is not None and is_a_symbolic_link_refusal(cause):
            return InstanceLockError(
                f"The instance lock at {self._path.name} is a symbolic link, not a lock "
                f"file. Opening it would write through the link to whatever it names, so "
                f"Theurian refuses to take the lock rather than touching that file.",
                remedy=symbolic_link_remedy(self._path),
            )
        artefact = shape or "something Theurian did not write"
        account = f": {cause.strerror or cause}" if cause is not None else ""
        return InstanceLockError(
            f"The instance lock at {self._path.name} is {artefact}, not a lock file"
            f"{account}. Theurian takes that lock before it serves, so it refuses to "
            f"start rather than proceed without it.",
            remedy=irregular_artefact_remedy(self._path, artefact),
        )

    def _directory_unusable(self, cause: OSError) -> InstanceLockError:
        """The refusal for a lock directory that could not be prepared (GATE-1).

        ``acquire``'s ``mkdir`` raised a bare ``OSError`` past every handler in
        the daemon's start path. Measured 2026-09-06 against the real CLI with a
        sandboxed ``HOME`` and ``THEURIAN_DATA_DIR``: ``theurian daemon start
        --foreground --json`` with the data directory replaced by a regular file
        exited 1 with a ``FileExistsError`` traceback, **zero bytes on stdout and
        zero on stderr**, and the same with its parent at mode ``0500``
        (``PermissionError``). That path is the ``ExecStart`` of the shipped
        launchd and systemd units, so a data directory the operator got wrong
        crash-loops a supervised daemon with nothing structured to read.

        The rule it now follows is
        ``connection.WriteLock._prepare_the_directory``'s, stated one file over:
        an acquisition has no step left that raises a bare ``OSError``. Named for
        the *call* rather than for the errno, for that method's reason --
        ``EACCES`` arrives from the ``mkdir`` and from the open's ``O_CREAT``
        alike, and only the call site knows which ran.
        """
        return InstanceLockError(
            f"The directory holding the instance lock could not be prepared: "
            f"{cause.strerror or cause}. Theurian takes the lock at {self._path.name} "
            f"before it serves, so it refuses to start rather than proceed without it.",
            remedy=(
                f"Make {self._path.parent.parent} writable, and make sure nothing but a "
                f"directory sits at {self._path.parent}, then retry. That directory is "
                f"Theurian's data directory -- `theurian doctor` reports where it is, and "
                f"`THEURIAN_DATA_DIR` overrides it."
            ),
        )

    def acquire(self) -> bool:
        """Try to take the lock without blocking.

        **Opened with the write lock's own flags** (:data:`LOCK_OPEN_FLAGS`),
        which is what round one found this method was not doing while
        ``connection.py`` said it was. ``Path.open("w")`` gets three things
        wrong at once, and each was measured on 2026-09-06 against this method:

        * **``O_TRUNC``.** A symbolic link at ``daemon.lock`` pointing at a file
          in the user's own tree was followed and truncated -- a 49-byte victim
          became 15 bytes (this method's own breadcrumb) -- and ``acquire``
          returned ``True``, so the daemon started and reported success. Same
          shape as #481 at the write lock, on a path reached by the documented
          ``theurian daemon start``.
        * **No ``O_NOFOLLOW``.** Which is what let the link be followed at all.
        * **No ``O_NONBLOCK``.** A named pipe at the path blocked inside
          ``open()`` -- still blocked when a 15-second kill fired -- so a
          `daemon start` never returned and published nothing to grade.

        The breadcrumb still goes in, through the descriptor this method already
        holds. It is truncated *after* the write rather than by the open: at that
        point ``O_NOFOLLOW`` has already resolved the name to a regular file this
        process holds a lock on, so the truncation cannot reach a link's target.
        Without it, a longer breadcrumb from an earlier run would leave a tail of
        stale bytes -- cosmetic, since nothing reads them, and still wrong to
        leave.

        The descriptor is asked what it is, for the reason
        ``connection.WriteLock._open`` records: a named pipe with a reader
        attached takes these flags without complaint, and ``flock`` then reports
        ``ENOTSUP`` -- a fault about the descriptor, arriving where it can only
        be described as one about the file. That the descriptor and the path can
        disagree is measured (``test_the_descriptor_and_the_path_disagree_after_
        the_open``); that this line must ask the descriptor is argued from that
        measurement and held by a structural pin, because no input the suite
        plants separates them through *this* method.

        Returns:
            ``True`` if this process now holds it.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._directory_unusable(exc) from exc
        try:
            fileno = os.open(self._path, LOCK_OPEN_FLAGS, 0o600)
        except OSError as exc:
            # The errno alone describes nothing an operator can act on: a named
            # pipe with no reader answers `ENXIO`, whose `strerror` is "Device
            # not configured" on macOS. So the path is asked what is there, and
            # the OS account travels as the second clause when it is.
            raise self._unusable(irregular_shape_at(self._path), cause=exc) from exc
        # Everything between the open and the `fdopen` that takes ownership of
        # the descriptor closes it on the way out. `BaseException`, not
        # `Exception`: a `KeyboardInterrupt` between these two lines leaks the
        # descriptor exactly as an error would, and a leaked descriptor on a lock
        # file is a lock this process still holds with nothing left to release
        # it (round two, descriptor hygiene).
        try:
            shape = irregular_shape(os.fstat(fileno).st_mode)
            if shape is not None:
                # The open *succeeded* on something that is not a file -- a named
                # pipe with a reader attached takes these flags without complaint
                # -- so there is no refusal to quote, only the descriptor's own
                # answer.
                raise self._unusable(shape, cause=None)
            handle = os.fdopen(fileno, "w")
        except BaseException:
            os.close(fileno)
            raise
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in _CONTENTION_ERRNOS:
                return False
            raise InstanceLockError(
                f"Cannot lock {self._path.name}: {exc.strerror or exc}.",
                remedy=(
                    f"Check which filesystem holds {self._path}: `df -h "
                    f"{self._path.parent}` names it. Theurian's locks need `flock`, which "
                    f"network filesystems such as NFS do not provide (ADR-0018)."
                ),
            ) from exc

        # Written for humans reading the file, never used to decide anything.
        # The lock itself is the mechanism; this is only a breadcrumb.
        handle.write(json.dumps({"pid": os.getpid()}) + "\n")
        handle.flush()
        handle.truncate()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise InstanceLockError(f"Another process holds {self._path}")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def probe_health(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = PROBE_TIMEOUT_SECONDS
) -> dict[str, object] | None:
    """Ask whatever is on the port whether it is a healthy Theurian.

    ``/health`` is unauthenticated by design (ADR-0011), which is what lets this
    run before any credential is available.

    Returns:
        The health payload, or ``None`` if nothing usable answered.
    """
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:  # noqa: PLR2004 - HTTP OK
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None

    return payload if isinstance(payload, dict) else None


def port_is_free(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """Whether the port can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def check_can_start(
    lock: InstanceLock,
    data_dir: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> InstanceCheck:
    """Decide whether this process should start a daemon.

    The handshake is what makes the answer *specific*. Knowing the port is
    occupied is not enough: reusing a daemon that serves a different data
    directory would silently answer queries from the wrong knowledge base, and
    killing it would break whoever it belongs to.
    """
    if lock.acquire():
        if port_is_free(host, port):
            return InstanceCheck(StartDecision.START, "Lock acquired and port free.")

        # Lock free but port taken: either a foreign process, or a daemon whose
        # lock file was removed. Either way, not ours to displace.
        lock.release()
        health = probe_health(host, port, PROBE_TIMEOUT_SECONDS)
        if health is None:
            return InstanceCheck(
                StartDecision.CONFLICT,
                f"Port {port} is in use by a process that is not a Theurian daemon.",
            )
        return _reuse_or_conflict(health, data_dir, port)

    # Someone holds the lock. Ask who.
    health = probe_health(host, port, PROBE_TIMEOUT_SECONDS)
    if health is None:
        return InstanceCheck(
            StartDecision.STALE,
            f"{lock.path} is held but nothing answers on {host}:{port}. "
            f"A daemon may be starting, or a process is wedged. "
            f"Run `theurian doctor`; no data is removed automatically.",
        )

    return _reuse_or_conflict(health, data_dir, port)


def _reuse_or_conflict(health: dict[str, object], data_dir: Path, port: int) -> InstanceCheck:
    """Decide based on what the running daemon says it is."""
    running_dir = str(health.get("dataDir", ""))
    version = str(health.get("version", "unknown"))

    if running_dir and Path(running_dir).resolve() != data_dir.resolve():
        return InstanceCheck(
            StartDecision.CONFLICT,
            f"A Theurian daemon on port {port} serves a different data directory "
            f"({running_dir}). Reusing it would answer queries from the wrong "
            f"knowledge base. Stop it, or start this one on another port.",
            existing_version=version,
            existing_data_dir=running_dir,
        )

    return InstanceCheck(
        StartDecision.REUSE,
        f"A healthy Theurian daemon (version {version}) already serves this data "
        f"directory on port {port}.",
        existing_version=version,
        existing_data_dir=running_dir or str(data_dir),
    )
