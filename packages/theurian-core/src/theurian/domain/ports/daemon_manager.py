"""DaemonManager port: user-scoped OS service lifecycle (§24.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from theurian.domain.setup import DifferingFields


class ServiceState(StrEnum):
    """Observed state of the daemon service."""

    NOT_INSTALLED = "not-installed"
    INSTALLED_STOPPED = "installed-stopped"
    RUNNING = "running"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """A snapshot of the service, as observed rather than as remembered.

    Deliberately never derived from a PID file alone: a stale PID file naming a
    recycled PID is exactly how a "single instance" guarantee gets broken
    (ADR-0002, §24.1).
    """

    state: ServiceState
    pid: int | None = None
    version: str | None = None
    listening: bool = False
    service_identifier: str | None = None
    detail: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.state is ServiceState.RUNNING and self.listening


@runtime_checkable
class DaemonManager(Protocol):
    """Installs, starts, stops, and removes the user-scoped daemon service.

    Implementations: macOS LaunchAgent, Linux systemd user unit, and later
    Windows and Docker Compose.

    Two hard rules, both from §7 and §24.2 of the brief:

    - Services are installed **per user**. Nothing here may require
      administrator or root privileges.
    - ``install`` is only ever reached from an explicit user action
      (``theurian setup`` or ``/theurian:setup``). It is never called from a
      ``SessionStart`` hook or from any implicit code path (FR-L3).
    """

    @property
    def platform_id(self) -> str:
        """Which manager this is, e.g. ``launchagent`` or ``systemd-user``."""
        ...

    def is_supported(self) -> bool:
        """Whether this manager can operate on the current OS and architecture."""
        ...

    async def status(self) -> ServiceStatus:
        """Observe the current service state.

        Cheap and side-effect-free: this is what ``SessionStart`` calls, and it
        must stay within the latency budget in NFR-2.
        """
        ...

    def differs_from_installed(self, *, port: int, data_directory: str) -> str:
        """Empty when the installed definition matches, else what differs.

        Renders the installed values, so this is the *operator's* output and
        never a shared report's: an installed definition is a file somebody else
        may have edited, and what they put in it is not Theurian's to publish.
        """
        ...

    def differing_keys(self, *, port: int, data_directory: str) -> DifferingFields:
        """The same difference, in the form ``doctor --report`` may publish.

        On the port rather than left to each adapter, and reached by a direct
        call rather than ``getattr``, because the failure mode is silent: a
        manager implementing only :meth:`differs_from_installed` would publish
        "the installed values are withheld" with no field names at all --
        string-identical to a definition too damaged to parse -- and both the
        type checker and the tests would pass (SEC-6, O-3).
        """
        ...

    async def install(self, *, port: int, data_directory: str) -> None:
        """Register the user-scoped service. Idempotent.

        An existing unit or plist with different contents is backed up and the
        difference reported, never silently overwritten (SEC-18).
        """
        ...

    async def start(self) -> None:
        """Start the service.

        Starting an already-running service is a no-op, not an error, and never
        results in a second process (§24.1).
        """
        ...

    async def stop(self) -> None:
        """Stop the service. Stopping a stopped service is a no-op."""
        ...

    async def restart(self) -> None: ...

    async def uninstall(self) -> None:
        """Deregister the service.

        Removes only the service definition. Data, configuration, and Git-tracked
        knowledge are out of scope here -- their deletion is a separate, explicitly
        confirmed choice (FR-L5).
        """
        ...
