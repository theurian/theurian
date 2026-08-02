"""What every setup step is allowed to touch (§6, ADR-0003).

One context object rather than a pile of parameters, so that a step cannot
quietly reach for something the plan did not account for: everything a step may
read or write is on this record, and everything on this record is either a path
or an injected adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from theurian.domain.ports.daemon_manager import DaemonManager
from theurian.domain.ports.mcp_client_config import McpClientConfig
from theurian.domain.ports.secret_store import SecretStore


@dataclass(frozen=True, slots=True)
class SetupContext:
    """Everything the steps operate on."""

    home: Path
    data_dir: Path
    port: int
    #: The repository setup was invoked in, or ``None`` when it was not invoked
    #: inside one. Project steps report themselves not-applicable rather than
    #: failing: installing the machine-wide parts outside a repository is a
    #: perfectly reasonable thing to do.
    project_root: Path | None
    #: The connection the MCP client should hold. Opaque here: its shape is the
    #: client adapter's business, and the steps only pass it through.
    connection: Any
    mcp_config: McpClientConfig
    secrets: SecretStore
    #: Asks whatever is on the port whether it is a healthy Theurian. Injected
    #: rather than imported so the application layer keeps depending on
    #: behaviour instead of on the daemon's HTTP client (ADR-0003).
    health: Callable[[], dict[str, object] | None]
    #: ``None`` when this platform has no user-scoped service manager. Supported
    #: rather than fatal — the daemon can be started by hand.
    service: DaemonManager | None
    #: Absolute path to the ``theurian`` executable, as the service unit will
    #: invoke it. A relative name would resolve against launchd's PATH, which is
    #: not the user's.
    executable: str

    @property
    def auth_dir(self) -> Path:
        return self.data_dir / "auth"

    @property
    def env_file(self) -> Path:
        return self.data_dir / "env"

    @property
    def theurian_dir(self) -> Path | None:
        return self.project_root / ".theurian" if self.project_root else None
