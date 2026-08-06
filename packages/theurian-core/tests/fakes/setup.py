"""Fakes for the setup collaborators (ADR-0003).

A real LaunchAgent would register itself in the developer's own login session,
which no amount of ``HOME`` redirection prevents, and Claude Code's config is
someone else's state file. Both are ports, so both get a fake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from theurian.domain.ports.daemon_manager import ServiceState, ServiceStatus


class FakeService:
    """A DaemonManager that records instead of touching launchd."""

    platform_id = "fake"

    def __init__(
        self,
        *,
        installed: bool = False,
        difference: str = "",
        differing: tuple[str, ...] = (),
    ) -> None:
        self.installed = installed
        self.started = False
        self.uninstalled = False
        self._difference = difference
        self._differing = differing
        self.plist_path = Path("/fake/service.plist")

    def is_supported(self) -> bool:
        return True

    def differs_from_installed(self, *, port: int, data_directory: str) -> str:
        return self._difference

    def differing_keys(self, *, port: int, data_directory: str) -> tuple[str, ...]:
        return self._differing

    async def status(self) -> ServiceStatus:
        state = ServiceState.INSTALLED_STOPPED if self.installed else ServiceState.NOT_INSTALLED
        return ServiceStatus(state=state, service_identifier="fake")

    async def install(self, *, port: int, data_directory: str) -> None:
        self.installed = True

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None: ...

    async def restart(self) -> None: ...

    async def uninstall(self) -> None:
        self.installed = False
        self.uninstalled = True


class FakeMcpConfig:
    """A Claude Code config that lives in a dict."""

    def __init__(self, *, available: bool = True, entry: dict[str, Any] | None = None) -> None:
        self._available = available
        self._entry = entry
        self.serena = False
        self.path = Path("/fake/.claude.json")
        self.installs = 0

    def is_available(self) -> bool:
        return self._available

    def installed_entry(self) -> dict[str, Any] | None:
        return self._entry

    def serena_detected(self) -> bool:
        return self.serena

    def difference(self, spec: Any) -> str:
        if self._entry is None or self._entry == spec.as_entry():
            return ""
        return f"installed={self._entry} wanted={spec.as_entry()}"

    def differing_keys(self, spec: Any) -> tuple[str, ...]:
        if self._entry is None:
            return ()
        wanted: dict[str, Any] = spec.as_entry()
        entry = self._entry
        return tuple(
            key for key in sorted(set(entry) | set(wanted)) if entry.get(key) != wanted.get(key)
        )

    def install(self, spec: Any) -> str:
        self.installs += 1
        self._entry = spec.as_entry()
        return ""

    def remove(self) -> str:
        self._entry = None
        return ""

    def back_up(self) -> Path | None:
        return None
