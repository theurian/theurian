"""macOS LaunchAgent adapter for the daemon service (§24.2, FR-L3).

A **LaunchAgent**, never a LaunchDaemon. The distinction is the whole security
posture of this file: a LaunchAgent lives in the user's own
``~/Library/LaunchAgents`` and runs as that user, while a LaunchDaemon lives in
``/Library/LaunchDaemons``, needs administrator rights to install, and runs as
root. Theurian serves one user's knowledge from one user's home directory; asking
for root to do that would be indefensible.
"""

from __future__ import annotations

import os
import plistlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.ports.daemon_manager import ServiceState, ServiceStatus
from theurian.infrastructure.services.runner import (
    CommandRunner,
    SubprocessRunner,
)

#: Reverse-DNS, as launchd expects. Also the plist's basename.
LABEL: Final = "dev.theurian.daemon"

_AGENTS_DIRECTORY: Final = "Library/LaunchAgents"


@final
class LaunchAgentManager:
    """Installs and controls the daemon as a user-scoped LaunchAgent."""

    platform_id = "launchagent"

    def __init__(
        self,
        *,
        executable: str,
        home: Path | None = None,
        runner: CommandRunner | None = None,
        uid: int | None = None,
    ) -> None:
        self._executable = executable
        self._home = home or Path.home()
        self._runner = runner or SubprocessRunner()
        self._uid = uid if uid is not None else os.getuid()

    # -- Identity ---------------------------------------------------------

    @property
    def plist_path(self) -> Path:
        return self._home / _AGENTS_DIRECTORY / f"{LABEL}.plist"

    @property
    def _domain(self) -> str:
        """launchd's per-user domain.

        ``gui/<uid>`` rather than ``user/<uid>``: the GUI domain is the one a
        desktop session belongs to, and an agent bootstrapped into ``user/`` is
        not reachable from a terminal the user opened in Aqua.
        """
        return f"gui/{self._uid}"

    @property
    def _target(self) -> str:
        return f"{self._domain}/{LABEL}"

    def is_supported(self) -> bool:
        return self._runner.which("launchctl") is not None

    # -- Definition -------------------------------------------------------

    def render(self, *, port: int, data_directory: str) -> bytes:
        """The plist, as launchd will read it.

        Built with :mod:`plistlib` rather than a string template. A data
        directory containing an ``&`` or a ``<`` would produce a malformed plist
        by concatenation, and launchd's diagnostic for that is famously unhelpful.
        """
        definition: dict[str, object] = {
            "Label": LABEL,
            # --foreground is required: launchd supervises the process itself,
            # and a program that forks and exits is reported as a crash and
            # restarted forever.
            "ProgramArguments": [
                self._executable,
                "daemon",
                "start",
                "--foreground",
                "--port",
                str(port),
            ],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            # launchd starts agents with almost no environment, so the data
            # directory has to be stated rather than inherited from a shell that
            # was never involved.
            "EnvironmentVariables": {"THEURIAN_DATA_DIR": data_directory},
            "WorkingDirectory": data_directory,
            "StandardOutPath": str(Path(data_directory) / "daemon.log"),
            "StandardErrorPath": str(Path(data_directory) / "daemon.log"),
            "ProcessType": "Background",
        }
        return plistlib.dumps(definition, sort_keys=True)

    def differs_from_installed(self, *, port: int, data_directory: str) -> str:
        """Empty when the installed plist already matches, else what differs.

        Compared by parsed content, not bytes: a plist rewritten by
        ``launchctl`` or edited by hand keeps its meaning while changing its
        formatting, and reporting that as a conflict would halt the run for
        consent over a difference that means nothing. Worse than noise: setup
        never rewrites a conflicting step, so the formatting would keep being
        reported and every later run would end DEGRADED with no way to converge.
        """
        if not self.plist_path.exists():
            return ""

        wanted = plistlib.loads(self.render(port=port, data_directory=data_directory))
        try:
            installed = plistlib.loads(self.plist_path.read_bytes())
        except (plistlib.InvalidFileException, ValueError):
            return f"{self.plist_path} is not a readable plist."

        if installed == wanted:
            return ""

        differing = sorted(
            key for key in set(installed) | set(wanted) if installed.get(key) != wanted.get(key)
        )
        lines = [f"{self.plist_path} differs from the definition Theurian would install:"]
        lines.extend(
            f"  {key}: installed={installed.get(key)!r} wanted={wanted.get(key)!r}"
            for key in differing
        )
        return "\n".join(lines)

    # -- Lifecycle --------------------------------------------------------

    async def status(self) -> ServiceStatus:
        """Observe the service. Cheap enough for ``SessionStart`` (NFR-2)."""
        if not self.plist_path.exists():
            return ServiceStatus(
                state=ServiceState.NOT_INSTALLED,
                service_identifier=LABEL,
                detail=f"No LaunchAgent at {self.plist_path}.",
            )

        printed = self._runner.run(["launchctl", "print", self._target])
        if not printed.ok:
            return ServiceStatus(
                state=ServiceState.INSTALLED_STOPPED,
                service_identifier=LABEL,
                detail="The plist is installed but not loaded into launchd.",
            )

        pid = _parse_pid(printed.stdout)
        if pid is None:
            return ServiceStatus(
                state=ServiceState.INSTALLED_STOPPED,
                service_identifier=LABEL,
                detail="Loaded into launchd but not currently running.",
            )
        return ServiceStatus(
            state=ServiceState.RUNNING, pid=pid, listening=True, service_identifier=LABEL
        )

    async def install(self, *, port: int, data_directory: str) -> None:
        """Write the plist and bootstrap it. Idempotent.

        Only ever reached from an explicit user action — ``theurian setup`` or
        ``/theurian:setup``. Never from ``SessionStart`` (FR-L3).
        """
        wanted = self.render(port=port, data_directory=data_directory)
        if self.plist_path.exists() and self.plist_path.read_bytes() == wanted:
            await self.start()
            return

        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        if self.plist_path.exists():
            self.back_up()

        # Written to a temporary file and renamed: launchd may read the path at
        # any moment, and a half-written plist is a load failure whose message
        # points at the file rather than at the write.
        temporary = self.plist_path.with_suffix(".plist.tmp")
        temporary.write_bytes(wanted)
        temporary.replace(self.plist_path)

        # Bootout first so a redefinition takes effect. A missing service makes
        # this fail, which is expected and ignored.
        self._runner.run(["launchctl", "bootout", self._target])
        self._runner.run(["launchctl", "bootstrap", self._domain, str(self.plist_path)])
        await self.start()

    def back_up(self) -> Path:
        """Copy the installed plist aside, returning the backup's path.

        Never overwritten in place (SEC-18): whatever is there may be something
        the user wrote, and it has to remain recoverable.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = self.plist_path.with_name(f"{self.plist_path.name}.{stamp}.backup")
        backup.write_bytes(self.plist_path.read_bytes())
        return backup

    async def start(self) -> None:
        """Start the service. Starting a running service is a no-op (§24.1)."""
        self._runner.run(["launchctl", "kickstart", self._target])

    async def stop(self) -> None:
        self._runner.run(["launchctl", "kill", "SIGTERM", self._target])

    async def restart(self) -> None:
        self._runner.run(["launchctl", "kickstart", "-k", self._target])

    async def uninstall(self) -> None:
        """Deregister the service and remove its definition.

        Removes the service only. Data, configuration, and Git-tracked knowledge
        are a separate and separately confirmed choice (FR-L5).
        """
        self._runner.run(["launchctl", "bootout", self._target])
        self.plist_path.unlink(missing_ok=True)


def _parse_pid(printed: str) -> int | None:
    """Pull the pid out of ``launchctl print`` output.

    A loaded-but-stopped agent prints no ``pid =`` line at all, which is how the
    two states are told apart.
    """
    for line in printed.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid ="):
            _, _, value = stripped.partition("=")
            try:
                return int(value.strip())
            except ValueError:  # pragma: no cover - launchctl prints an integer
                return None
    return None
