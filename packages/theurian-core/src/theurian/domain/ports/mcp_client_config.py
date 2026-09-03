"""McpClientConfig: an MCP client's server list (ADR-0012, §24.2).

Claude Code is the first implementation, not the only possible one. Theurian
serves any MCP client, and the setup step that installs a connection should not
have to know which one it is talking to — so the step depends on this, and the
composition root names the adapter.

**Injected like a port, but not a member of the port register.** This docstring
used to open "McpClientConfig port", and that overstated its standing. ADR-0003
point 5's Milestone 7 amendment settles the register as
``theurian.domain.ports.ALL_PORTS``; this Protocol is not in it, and
``ports/__init__.py`` does not import it, so ``test_port_set_is_closed`` and
the nine per-port checks beside it have never run against it -- not failing
against it, *unreached* by it.

It is wired like a port all the same: ``SetupContext.mcp_config`` is
constructor-injected and ``cli/setup_commands.py``, a composition root, names
``ClaudeCodeMcpConfig`` as the adapter. Whether it should therefore *join* the
register is a decision ADR-0003 says requires an ADR. That decision is open and
is deliberately not taken here; the trail is
https://github.com/theurian/theurian/issues/140.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from theurian.domain.setup import DifferingFields


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

    def differing_keys(self, spec: Any) -> DifferingFields:
        """Which fields differ from ``spec``, without their values.

        On the port rather than left to each adapter because the caller that
        needs it is the one building ``doctor --report``, whose output is meant
        to be published: an installed entry's values were written by someone
        other than Theurian and may be a literal credential, so no adapter is
        free to answer this by rendering them (SEC-6, O-3).

        The return type carries the second half of that rule. A field *name*
        taken from the installed entry is data too -- it is a hand-editable
        object in somebody else's state file -- so an implementation names only
        what ``spec`` itself produces and counts the rest.
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
