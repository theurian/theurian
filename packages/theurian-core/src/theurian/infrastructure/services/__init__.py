"""Service adapters: user-scoped daemon lifecycle (§24.2).

Selection is by *capability*, not by ``sys.platform`` alone. A Linux container
without a session bus has ``systemctl`` and no user manager behind it, and
installing a unit there produces a service that can never start — so each
adapter is asked whether it can actually operate here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from theurian.domain.ports.daemon_manager import DaemonManager
from theurian.infrastructure.services.launchagent import LaunchAgentManager
from theurian.infrastructure.services.runner import (
    CommandResult,
    CommandRunner,
    SubprocessRunner,
)
from theurian.infrastructure.services.systemd_user import SystemdUserManager

__all__ = [
    "CommandResult",
    "CommandRunner",
    "LaunchAgentManager",
    "SubprocessRunner",
    "SystemdUserManager",
    "detect_manager",
]


def detect_manager(
    *,
    executable: str,
    home: Path | None = None,
    runner: CommandRunner | None = None,
    platform: str | None = None,
) -> DaemonManager | None:
    """The service manager for this machine, or ``None`` if there is none.

    ``None`` is a supported outcome, not an error. Theurian runs perfectly well
    with the daemon started by hand; what it must never do is claim to have
    installed a service on a platform that cannot hold one.
    """
    resolved = platform or sys.platform
    shared = {"executable": executable, "home": home, "runner": runner}

    candidate: DaemonManager
    if resolved == "darwin":
        candidate = LaunchAgentManager(**shared)  # type: ignore[arg-type]
    elif resolved.startswith("linux"):
        candidate = SystemdUserManager(**shared)  # type: ignore[arg-type]
    else:
        return None

    return candidate if candidate.is_supported() else None
