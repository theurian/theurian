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

import errno
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

if sys.platform == "win32":  # pragma: no cover - Windows is not a 1.0 target
    raise ImportError(
        "Theurian's instance lock is POSIX-only (fcntl). See packaging/windows/README.md."
    )

#: How long to wait for a health probe. Long enough for a daemon that is busy,
#: short enough that a startup check never feels hung.
PROBE_TIMEOUT_SECONDS: Final = 2.0

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 7419


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
    """The instance lock could not be acquired or was found in a bad state."""


class InstanceLock:
    """An advisory file lock held for the daemon's lifetime.

    A lock file rather than a PID file: PIDs are recycled, so a stale PID file
    can name a live unrelated process, and a "single instance" guarantee built on
    one is a guarantee that silently lapses. An advisory lock is released by the
    kernel when the holder exits, however it exits.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> bool:
        """Try to take the lock without blocking.

        Returns:
            ``True`` if this process now holds it.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise InstanceLockError(f"Cannot lock {self._path}: {exc}") from exc

        # Written for humans reading the file, never used to decide anything.
        # The lock itself is the mechanism; this is only a breadcrumb.
        handle.write(json.dumps({"pid": os.getpid()}) + "\n")
        handle.flush()
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
