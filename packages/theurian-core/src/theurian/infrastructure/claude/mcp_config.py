"""Installing the MCP connection into Claude Code (ADR-0011, ADR-0012, SEC-5).

**Theurian reads ``~/.claude.json``. It never writes it.** Every write is
delegated to ``claude mcp add`` / ``claude mcp remove``, for three reasons that
were each confirmed against the real CLI rather than assumed:

1. That file is Claude Code's live state — model caches, project history,
   onboarding flags — not a configuration file Theurian has any business
   reformatting. A JSON round-trip would rewrite all of it to prove a
   twelve-line change.
2. Claude Code may be running while setup runs. Two writers on one file is a
   lost update, and the update that gets lost could be the user's.
3. ``claude mcp add`` stores ``${THEURIAN_MCP_TOKEN}`` verbatim rather than
   expanding it, which is precisely what SEC-5 requires — the literal token
   never enters a config file.

Reading it directly is safe and is what the probe does, because ``claude mcp
get`` cannot report *why* an entry differs and the user has to be shown that
before approving an overwrite.

One verified quirk drives the whole design: ``claude mcp add`` refuses to touch
an existing entry — it prints "already exists" and exits non-zero, leaving the
old entry in place. So a conflicting entry cannot be repaired by adding over it;
it has to be removed first. The write is confirmed by reading the file back
rather than by trusting an exit code, because "the command succeeded" and "the
entry is now what we wanted" are different claims.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, final

from theurian.infrastructure.services.runner import (
    CommandResult,
    CommandRunner,
    SubprocessRunner,
)
from theurian.security.tokens import TOKEN_ENV_VAR

#: The server name Theurian registers under. Also what `uninstall` removes.
SERVER_NAME: Final = "theurian"

#: Claude Code's user-scope configuration. Read here, written only by `claude`.
CONFIG_FILENAME: Final = ".claude.json"

#: The other MCP server Theurian expects to meet. Detected, never modified.
SERENA: Final = "serena"


@dataclass(frozen=True, slots=True)
class ConnectionSpec:
    """The connection Theurian wants Claude Code to have."""

    port: int = 7419
    host: str = "127.0.0.1"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def authorization(self) -> str:
        """A reference, never the secret (SEC-5, ADR-0011).

        Passed to ``claude`` as one argv element, so no shell ever sees it and
        the ``${...}`` reaches the config file intact.
        """
        return f"Bearer ${{{TOKEN_ENV_VAR}}}"

    def as_entry(self) -> dict[str, Any]:
        """The entry as Claude Code stores it — verified against the real CLI."""
        return {
            "type": "http",
            "url": self.url,
            "headers": {"Authorization": self.authorization},
        }


@final
class ClaudeCodeMcpConfig:
    """Probes and installs Theurian's entry in Claude Code's user config."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._home = home or Path.home()
        self._runner = runner or SubprocessRunner()

    @property
    def path(self) -> Path:
        return self._home / CONFIG_FILENAME

    def _claude(self, *args: str) -> CommandResult:
        """Run `claude`, pointed at *this* home.

        `--scope user` means "the home directory `claude` is running in". Letting
        it inherit the caller's HOME would make a configured home apply to reads
        and be ignored by writes -- so setup against a temporary profile, and
        every test, would silently edit the developer's real configuration.
        """
        return self._runner.run(["claude", *args], env={"HOME": str(self._home)})

    def is_available(self) -> bool:
        """Whether Claude Code is installed at all.

        Absent is not a failure. Theurian serves any MCP client, and a machine
        without Claude Code simply has no such entry to install.
        """
        return self._runner.which("claude") is not None

    # -- Probing ----------------------------------------------------------

    def _servers(self) -> dict[str, Any]:
        """Every configured MCP server, or empty if there is no config yet.

        A malformed config reads as empty rather than raising: the remedy for
        Claude Code's own broken state file is not a Theurian traceback, and the
        step that consumes this reports it as a conflict.
        """
        if not self.path.is_file():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return {}
        servers = loaded.get("mcpServers") if isinstance(loaded, dict) else None
        return servers if isinstance(servers, dict) else {}

    def installed_entry(self) -> dict[str, Any] | None:
        entry = self._servers().get(SERVER_NAME)
        return entry if isinstance(entry, dict) else None

    def serena_detected(self) -> bool:
        """Whether Serena is configured. Reported, never modified (§18)."""
        return SERENA in self._servers()

    def difference(self, spec: ConnectionSpec) -> str:
        """Empty when the installed entry already matches, else what differs.

        Compared field by field rather than by equality alone, because the user
        is being asked to approve replacing whatever is there and "it differs"
        is not enough to decide on.
        """
        installed = self.installed_entry()
        if installed is None:
            return ""

        wanted = spec.as_entry()
        if installed == wanted:
            return ""

        lines = [f"The `{SERVER_NAME}` entry in {self.path} differs:"]
        for key in sorted(set(installed) | set(wanted)):
            have, want = installed.get(key), wanted.get(key)
            if have != want:
                lines.append(f"  {key}:")
                lines.append(f"    installed: {_render(have)}")
                lines.append(f"    Theurian:  {_render(want)}")
        return "\n".join(lines)

    # -- Writing, by way of the tool that owns the file --------------------

    def install(self, spec: ConnectionSpec) -> str:
        """Ensure the entry exists and matches. Returns a failure, or empty.

        Idempotent: an entry that already matches is left alone, so a second
        setup run touches nothing (FR-L2).
        """
        installed = self.installed_entry()
        if installed == spec.as_entry():
            return ""

        if installed is not None:
            # `claude mcp add` will not overwrite -- it reports "already exists"
            # and leaves the old entry. Removing first is the only way to change
            # one, and the caller has already shown the difference and asked.
            removal = self._claude("mcp", "remove", SERVER_NAME, "--scope", "user")
            if not removal.ok:
                return f"Could not remove the existing entry: {removal.output}"

        result = self._claude(
            "mcp",
            "add",
            "--scope",
            "user",
            "--transport",
            "http",
            SERVER_NAME,
            spec.url,
            "--header",
            # One argv element. No shell is involved, so `${...}` is stored
            # verbatim rather than expanded (SEC-5).
            f"Authorization: {spec.authorization}",
        )

        # Confirmed by reading the file back, not by the exit code: "the command
        # succeeded" and "the entry is now what we wanted" are different claims,
        # and only the second one is what setup is about to report.
        if self.installed_entry() != spec.as_entry():
            if not result.ok:
                return f"`claude mcp add` failed: {result.output}"
            return (
                f"`claude mcp add` reported success but {self.path} does not "
                f"contain the expected entry. Check it for a conflicting `{SERVER_NAME}` server."
            )
        return ""

    def remove(self) -> str:
        """Remove Theurian's entry. Removing an absent entry is not an error.

        Independent of removing the plugin, the daemon, or the data — the
        granularity FR-L5 requires.
        """
        if self.installed_entry() is None:
            return ""
        result = self._claude("mcp", "remove", SERVER_NAME, "--scope", "user")
        return "" if result.ok else f"`claude mcp remove` failed: {result.output}"

    def back_up(self) -> Path | None:
        """Copy the config aside before a change. ``None`` if there is none yet.

        Taken even though `claude` owns the write: the removal step is real
        destruction of whatever the user had, and it should stay recoverable
        (SEC-18).
        """
        if not self.path.is_file():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = self.path.with_name(f"{CONFIG_FILENAME}.{stamp}.backup")
        backup.write_bytes(self.path.read_bytes())
        backup.chmod(0o600)
        return backup


def _render(value: object) -> str:
    """A value, compactly, for a diff a person reads."""
    return json.dumps(value, sort_keys=True) if value is not None else "(absent)"
