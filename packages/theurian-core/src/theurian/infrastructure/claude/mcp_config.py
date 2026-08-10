"""Installing the MCP connection into Claude Code (ADR-0011, ADR-0012, SEC-5).

**Theurian reads ``~/.claude.json``. It never writes it.** Every write to the
config *itself* is delegated to ``claude mcp add`` / ``claude mcp remove``, for
three reasons that were each confirmed against the real CLI rather than
assumed. The one exception is the timestamped ``.backup`` sibling
:meth:`ClaudeCodeMcpConfig.back_up` writes beside it before a destructive
change (SEC-18) -- a copy sitting next to the config, never the config:

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
get`` cannot report *why* an entry differs, and a differing entry is reported as
a conflict the user has to see: setup leaves it in place and asks only whether
the rest of the run may proceed around it.

One verified quirk shapes the write path: ``claude mcp add`` refuses to touch an
existing entry — it prints "already exists" and exits non-zero, leaving the old
entry in place. So an existing entry cannot be changed by adding over it; it
would have to be removed first. Setup never reaches that: a differing entry
probes as ``CONFLICTING`` and is left exactly as it is, so ``install`` is called
only where there was no entry at all. The remove-first branch it still carries is
reachable only by a race, and says so. The write is confirmed by reading the file
back rather than by trusting an exit code, because "the command succeeded" and
"the entry is now what we wanted" are different claims.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, final

from theurian.domain.setup import DifferingFields
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

#: A bound on `back_up`'s same-second disambiguation loop -- generous enough
#: that no real run of `install` hits it, small enough that a bug forcing every
#: attempt to collide fails fast instead of spinning.
_MAX_BACKUP_ATTEMPTS: Final = 1000


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
        is being asked whether the run may proceed around whatever is there --
        setup does not replace it -- and "it differs" is not enough to decide
        that on.
        """
        installed = self.installed_entry()
        if installed is None:
            return ""

        wanted = spec.as_entry()
        if installed == wanted:
            return ""

        lines = [f"The `{SERVER_NAME}` entry in {self.path} differs:"]
        # Every differing key, including ones Theurian never writes. This output
        # is the operator's own; only `differing_keys` is publishable.
        for key in _differing_names(installed, wanted):
            have, want = installed.get(key), wanted.get(key)
            lines.append(f"  {key}:")
            lines.append(f"    installed: {_render(have)}")
            lines.append(f"    Theurian:  {_render(want)}")
        return "\n".join(lines)

    def differing_keys(self, spec: ConnectionSpec) -> DifferingFields:
        """Which fields differ, without a word about their values.

        What :meth:`difference` renders is the *installed* entry, and an
        installed entry's values are not Theurian's to hand on. The state that
        makes this step conflict at all is a ``theurian`` entry someone else
        wrote, and the most likely reason they wrote it is that they pasted the
        token in literally instead of by reference -- so the payload people
        publish to ask why setup is stuck carried a live bearer token, and the
        redaction that runs afterwards substitutes paths and had no anchor for
        it.

        Only the fields :meth:`ConnectionSpec.as_entry` produces are named. The
        rest are counted: this entry is a hand-editable object in somebody
        else's state file, so a top-level key in it is as much data as a value
        (see :class:`DifferingFields`).

        A name still tells the reader which field to go and look at on their own
        terminal, where :meth:`difference` prints it in full.
        """
        installed = self.installed_entry()
        if installed is None:
            return DifferingFields()
        wanted = spec.as_entry()
        return DifferingFields.over(_differing_names(installed, wanted), authored=wanted)

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
            # and leaves the old entry -- so removing first is the only way to
            # change one.
            #
            # This branch is not reached through `SetupService`. Measured against
            # `probe_mcp_connection`: no entry -> MISSING, a differing entry ->
            # CONFLICTING, an identical entry -> SATISFIED; and `SetupService.
            # _apply` runs a step's action only where the plan said MISSING. So
            # setup calls `install` only when there is nothing installed, and the
            # sole route here is a race -- an entry appearing between the probe
            # and this call, in which nobody has been shown a difference or asked
            # anything.
            #
            # Backed up first (SEC-18), matching `LaunchAgentManager.install`: the
            # entry `mcp remove` is about to destroy is real user state, and a
            # failed backup aborts the removal rather than risking it. The bytes
            # surviving on disk next to the config is what "recoverable" means
            # here -- no report yet names the backup's path to the caller (#126).
            try:
                self.back_up()
            except OSError as exc:
                return (
                    f"Could not back up {self.path} before replacing the entry: "
                    f"{exc}. Free space or fix permissions in your home "
                    f"directory, then run setup again."
                )
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

        Created via ``O_CREAT | O_EXCL`` at mode 0600 in the same call that
        creates the file, not by writing and then `chmod`-ing: the config being
        copied is Claude Code's live state, a bearer token included, and a
        write-then-chmod leaves a window in which the copy is as readable as the
        process umask allows.

        The name carries a second-precision timestamp, so two calls landing in
        the same UTC second collide on it; `O_EXCL` turns that collision into
        `FileExistsError` instead of silently overwriting the first backup, and
        a numeric suffix (``-1``, ``-2``, ...) is appended before ``.backup``
        until a free name is found, so both copies survive.
        """
        if not self.path.is_file():
            return None
        payload = self.path.read_bytes()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        for attempt in range(_MAX_BACKUP_ATTEMPTS):
            suffix = f"-{attempt}" if attempt else ""
            candidate = self.path.with_name(f"{CONFIG_FILENAME}.{stamp}{suffix}.backup")
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            return candidate
        msg = (
            f"Could not find a free backup name for {self.path} at {stamp} "
            f"after {_MAX_BACKUP_ATTEMPTS} attempts."
        )
        raise OSError(msg)


def _differing_names(installed: Mapping[str, Any], wanted: Mapping[str, Any]) -> tuple[str, ...]:
    """Every key whose value differs, sorted. Names only; nothing is rendered."""
    return tuple(
        sorted(key for key in set(installed) | set(wanted) if installed.get(key) != wanted.get(key))
    )


def _render(value: object) -> str:
    """A value, compactly, for a diff a person reads."""
    return json.dumps(value, sort_keys=True) if value is not None else "(absent)"
