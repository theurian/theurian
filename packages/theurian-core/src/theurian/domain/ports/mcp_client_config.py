"""McpClientConfig port: an MCP client's server list (ADR-0012, §24.2).

Claude Code is the first implementation, not the only possible one. Theurian
serves any MCP client, and the setup step that installs a connection should not
have to know which one it is talking to — so the step depends on this, and the
composition root names the adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class McpClientConfig(Protocol):
    """Reads, and asks the owning tool to write, a client's MCP server list."""

    @property
    def path(self) -> Path:
        """Where the configuration lives. Shown to the user in a plan."""
        ...

    def is_available(self) -> bool:
        """Whether this client is installed at all.

        Absent is not a failure: a machine without this client simply has no
        entry to install, and setup reports the step as not applicable.
        """
        ...

    def installed_entry(self) -> dict[str, Any] | None:
        """Theurian's current entry, or ``None``."""
        ...

    def serena_detected(self) -> bool:
        """Whether Serena is configured. Reported, never modified (§18)."""
        ...

    def difference(self, spec: Any) -> str:
        """Empty when the installed entry matches ``spec``, else what differs.

        A string rather than a bool because the difference is put to the user,
        who decides whether the run proceeds *around* whatever is there -- setup
        leaves a conflicting entry exactly as it found it, with or without
        consent (SEC-18). "It differs" is not enough to decide that on.
        """
        ...

    def install(self, spec: Any) -> str:
        """Make the entry match ``spec``. Returns a failure message, or empty.

        Idempotent: an entry that already matches must be left alone, so a
        second setup run touches nothing (FR-L2).
        """
        ...

    def remove(self) -> str:
        """Remove Theurian's entry. Removing an absent entry is not an error."""
        ...

    def back_up(self) -> Path | None:
        """Copy the configuration aside. ``None`` when there is none yet."""
        ...
