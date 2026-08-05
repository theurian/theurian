"""Installing the MCP connection into Claude Code (ADR-0012, SEC-5).

Two layers. Most tests drive a recorded ``claude`` so the decisions are checked
without needing the binary. A final group runs the **real** CLI against a
sandboxed ``HOME``, because every claim this module is built on -- where the
entry lands, that ``${VAR}`` is stored rather than expanded, that ``add``
refuses to overwrite -- is a fact about someone else's tool, and a fact about
someone else's tool is exactly the kind that changes without telling you.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, override

import pytest

from theurian.infrastructure.claude.mcp_config import (
    CONFIG_FILENAME,
    SERVER_NAME,
    ClaudeCodeMcpConfig,
    ConnectionSpec,
)
from theurian.infrastructure.services.runner import CommandResult

pytestmark = pytest.mark.integration

CLAUDE = shutil.which("claude")


class FakeClaude:
    """A ``claude`` that edits the sandboxed config the way the real one does.

    Including the part that matters: ``mcp add`` refuses to overwrite an
    existing entry, leaving the old one in place.
    """

    def __init__(self, config: Path, *, available: bool = True, fail: str = "") -> None:
        self._config = config
        self._available = available
        self._fail = fail
        self.commands: list[list[str]] = []
        self.environments: list[dict[str, str]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = 20.0,  # noqa: ARG002 - part of the CommandRunner protocol
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(list(args))
        self.environments.append(dict(env or {}))
        if self._fail and self._fail in " ".join(args):
            return CommandResult(exit_code=1, stderr="deliberate failure")

        document = self._read()
        servers = document.setdefault("mcpServers", {})

        if args[1:3] == ["mcp", "remove"]:
            servers.pop(args[3], None)
            self._write(document)
            return CommandResult(exit_code=0, stdout="Removed")

        if args[1:3] == ["mcp", "add"]:
            name = args[args.index("http") + 1] if "http" in args else SERVER_NAME
            if name in servers:
                # The verified quirk: the old entry stays, and it says so.
                return CommandResult(exit_code=1, stdout="already exists")
            header = args[args.index("--header") + 1]
            key, _, value = header.partition(": ")
            servers[name] = {
                "type": "http",
                "url": args[args.index("--header") - 1],
                "headers": {key: value},
            }
            self._write(document)
            return CommandResult(exit_code=0, stdout="Added")

        return CommandResult(exit_code=0)

    def which(self, executable: str) -> str | None:
        if executable == "claude" and not self._available:
            return None
        return f"/usr/bin/{executable}"

    def _read(self) -> dict[str, Any]:
        if not self._config.is_file():
            return {}
        loaded: dict[str, Any] = json.loads(self._config.read_text())
        return loaded

    def _write(self, document: dict[str, Any]) -> None:
        self._config.write_text(json.dumps(document, indent=2))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


def _config(home: Path, **kwargs: Any) -> ClaudeCodeMcpConfig:
    runner = kwargs.pop("runner", None) or FakeClaude(home / CONFIG_FILENAME)
    return ClaudeCodeMcpConfig(home=home, runner=runner)


def _write_servers(home: Path, servers: dict[str, Any], **extra: Any) -> None:
    (home / CONFIG_FILENAME).write_text(json.dumps({"mcpServers": servers, **extra}, indent=2))


# -- The entry Theurian wants ----------------------------------------------


def test_the_entry_carries_a_variable_reference_never_a_token() -> None:
    """SEC-5. Config files get copied into gists and pasted into issues."""
    entry = ConnectionSpec().as_entry()

    assert entry["headers"]["Authorization"] == "Bearer ${THEURIAN_MCP_TOKEN}"
    assert entry["type"] == "http", "stdio is forbidden for Theurian (ADR-0002)"
    assert entry["url"] == "http://127.0.0.1:7419/mcp"


def test_the_entry_declares_no_command() -> None:
    """ADR-0002. A `command` key would make this a stdio server -- one process
    per client, N writers on one SQLite file."""
    assert "command" not in ConnectionSpec().as_entry()


# -- Probing ---------------------------------------------------------------


def test_no_config_means_the_entry_is_missing(home: Path) -> None:
    assert _config(home).installed_entry() is None


def test_a_matching_entry_reports_no_difference(home: Path) -> None:
    _write_servers(home, {SERVER_NAME: ConnectionSpec().as_entry()})

    assert _config(home).difference(ConnectionSpec()) == ""


def test_a_differing_entry_shows_both_sides(home: Path) -> None:
    """The user decides whether the run proceeds around this entry, which setup
    leaves in place either way, so "it differs" is not enough to decide on."""
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://127.0.0.1:9999/mcp"}})

    difference = _config(home).difference(ConnectionSpec())

    assert "9999" in difference
    assert "7419" in difference
    assert "installed" in difference


def test_a_stdio_entry_someone_hand_wrote_is_a_difference(home: Path) -> None:
    """The exact case ADR-0002 forbids. It must be caught and shown, not
    silently left in place."""
    _write_servers(home, {SERVER_NAME: {"command": "theurian", "args": ["mcp"]}})

    difference = _config(home).difference(ConnectionSpec())

    assert "command" in difference


def test_a_malformed_config_reads_as_empty_rather_than_raising(home: Path) -> None:
    """The remedy for Claude Code's broken state file is not a Theurian
    traceback."""
    (home / CONFIG_FILENAME).write_text("{not json at all")

    assert _config(home).installed_entry() is None


def test_a_config_without_mcp_servers_is_handled(home: Path) -> None:
    (home / CONFIG_FILENAME).write_text(json.dumps({"someOtherKey": 1}))

    assert _config(home).installed_entry() is None


def test_serena_is_detected_but_not_touched(home: Path) -> None:
    """§18. Theurian and Serena answer different questions and coexist."""
    _write_servers(home, {"serena": {"type": "stdio", "command": "serena"}})
    config = _config(home)

    assert config.serena_detected()
    assert config.installed_entry() is None


# -- Installing ------------------------------------------------------------


def test_installing_adds_the_entry(home: Path) -> None:
    config = _config(home)

    assert config.install(ConnectionSpec()) == ""
    assert config.installed_entry() == ConnectionSpec().as_entry()


def test_installing_preserves_every_other_server(home: Path) -> None:
    """ADR-0012. Serena's entry, and anything else the user configured, must
    survive untouched."""
    _write_servers(home, {"serena": {"type": "stdio", "command": "serena"}})

    _config(home).install(ConnectionSpec())

    servers = json.loads((home / CONFIG_FILENAME).read_text())["mcpServers"]
    assert servers["serena"] == {"type": "stdio", "command": "serena"}
    assert SERVER_NAME in servers


def test_installing_preserves_unrelated_top_level_state(home: Path) -> None:
    """~/.claude.json holds Claude Code's own state, not just configuration.
    Losing it would be a far worse bug than a missing MCP entry."""
    _write_servers(home, {}, projects={"/some/path": {"history": ["a"]}}, onboarded=True)

    _config(home).install(ConnectionSpec())

    document = json.loads((home / CONFIG_FILENAME).read_text())
    assert document["projects"] == {"/some/path": {"history": ["a"]}}
    assert document["onboarded"] is True


def test_installing_twice_changes_nothing(home: Path) -> None:
    """FR-L2."""
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)
    config.install(ConnectionSpec())
    first = (home / CONFIG_FILENAME).read_text()
    runner.commands.clear()

    assert config.install(ConnectionSpec()) == ""
    assert (home / CONFIG_FILENAME).read_text() == first
    assert runner.commands == [], "a converged entry must not invoke claude at all"


def test_replacing_a_conflicting_entry_removes_it_first(home: Path) -> None:
    """`claude mcp add` refuses to overwrite -- it reports "already exists" and
    exits 0. Adding over a conflict would silently do nothing."""
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://127.0.0.1:9999/mcp"}})
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    assert config.install(ConnectionSpec()) == ""

    assert any("remove" in " ".join(c) for c in runner.commands)
    assert config.installed_entry() == ConnectionSpec().as_entry()


def test_a_failed_add_is_reported_rather_than_assumed(home: Path) -> None:
    runner = FakeClaude(home / CONFIG_FILENAME, fail="mcp add")
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    failure = config.install(ConnectionSpec())

    assert "failed" in failure


def test_a_success_that_wrote_nothing_is_caught_by_reading_the_file_back(home: Path) -> None:
    """An exit code says the command ran, not that the entry is now correct.

    Reading the file back is the only claim setup can actually make, and this
    is the case -- reported success, nothing written -- that an exit-code check
    alone would wave through.
    """

    class SilentlyDoesNothing(FakeClaude):
        @override
        def run(
            self,
            args: Sequence[str],
            *,
            timeout: float = 20.0,
            env: Mapping[str, str] | None = None,
        ) -> CommandResult:
            self.commands.append(list(args))
            return CommandResult(exit_code=0, stdout="Added")

    config = ClaudeCodeMcpConfig(home=home, runner=SilentlyDoesNothing(home / CONFIG_FILENAME))

    failure = config.install(ConnectionSpec())

    assert "reported success" in failure


def test_a_backup_is_taken_before_a_destructive_change(home: Path) -> None:
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    config = _config(home)

    backup = config.back_up()

    assert backup is not None
    assert "http://old/mcp" in backup.read_text()
    assert backup.stat().st_mode & 0o777 == 0o600, "it is a copy of a file holding user state"


def test_backing_up_a_missing_config_yields_nothing(home: Path) -> None:
    assert _config(home).back_up() is None


# -- Removing --------------------------------------------------------------


def test_removing_takes_out_only_theurian(home: Path) -> None:
    """FR-L5. Removing Theurian's entry is independent of everything else."""
    _write_servers(home, {SERVER_NAME: ConnectionSpec().as_entry(), "serena": {"type": "stdio"}})
    config = _config(home)

    assert config.remove() == ""

    servers = json.loads((home / CONFIG_FILENAME).read_text())["mcpServers"]
    assert SERVER_NAME not in servers
    assert "serena" in servers


def test_removing_an_absent_entry_is_not_an_error(home: Path) -> None:
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    assert config.remove() == ""
    assert runner.commands == [], "nothing to do means nothing to run"


def test_claude_being_absent_is_reported_not_assumed(home: Path) -> None:
    """Theurian serves any MCP client. A machine without Claude Code has no
    such entry to install, which is a skipped step and not a failure."""
    runner = FakeClaude(home / CONFIG_FILENAME, available=False)

    assert not ClaudeCodeMcpConfig(home=home, runner=runner).is_available()


# -- Against the real CLI --------------------------------------------------
#
# Everything above encodes what `claude` was observed to do. These run it, so
# the day that changes, this fails here rather than in a user's setup.


@pytest.mark.skipif(CLAUDE is None, reason="the claude CLI is not installed")
def test_the_real_cli_stores_the_variable_reference_verbatim(tmp_path: Path) -> None:
    """SEC-5 depends on this exactly: if `claude` ever expanded `${VAR}` at add
    time, setup would write the literal token into a config file."""
    sandbox = tmp_path / "sandboxed-home"
    sandbox.mkdir()
    config = ClaudeCodeMcpConfig(home=sandbox)

    assert config.install(ConnectionSpec(port=7419)) == ""

    entry = config.installed_entry()
    assert entry is not None
    assert entry["headers"]["Authorization"] == "Bearer ${THEURIAN_MCP_TOKEN}"
    assert "${THEURIAN_MCP_TOKEN}" in (sandbox / CONFIG_FILENAME).read_text()


@pytest.mark.skipif(CLAUDE is None, reason="the claude CLI is not installed")
def test_the_real_cli_writes_to_the_user_scope_file_this_module_reads(tmp_path: Path) -> None:
    """ADR-0012 point 3 names ~/.claude.json. If Claude Code moved user scope
    elsewhere, the probe would read a file the writer no longer writes -- and
    setup would loop, reinstalling an entry it could never see."""
    sandbox = tmp_path / "sandboxed-home"
    sandbox.mkdir()

    ClaudeCodeMcpConfig(home=sandbox).install(ConnectionSpec())

    document = json.loads((sandbox / CONFIG_FILENAME).read_text())
    assert SERVER_NAME in document["mcpServers"]


@pytest.mark.skipif(CLAUDE is None, reason="the claude CLI is not installed")
def test_the_real_cli_refuses_to_overwrite_an_existing_entry(tmp_path: Path) -> None:
    """The quirk the conflict path exists for. If `claude` ever starts
    overwriting, the remove-first step becomes unnecessary -- and this test
    tells us, instead of us never noticing."""
    sandbox = tmp_path / "sandboxed-home"
    sandbox.mkdir()
    assert CLAUDE is not None
    environment = {**os.environ, "HOME": str(sandbox)}

    results = [
        subprocess.run(  # noqa: S603
            [
                CLAUDE,
                "mcp",
                "add",
                "--scope",
                "user",
                "--transport",
                "http",
                SERVER_NAME,
                f"http://127.0.0.1:{port}/mcp",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        for port in (7419, 9999)
    ]

    assert results[0].returncode == 0
    assert results[1].returncode != 0, "refusing to overwrite is what remove-first exists for"
    entry = json.loads((sandbox / CONFIG_FILENAME).read_text())["mcpServers"][SERVER_NAME]
    assert "7419" in entry["url"], "the second add must not have replaced the first"


@pytest.mark.skipif(CLAUDE is None, reason="the claude CLI is not installed")
def test_the_real_cli_leaves_other_servers_alone(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandboxed-home"
    sandbox.mkdir()
    config = ClaudeCodeMcpConfig(home=sandbox)
    config.install(ConnectionSpec())

    assert CLAUDE is not None
    subprocess.run(  # noqa: S603
        [CLAUDE, "mcp", "add", "--scope", "user", "other-server", "echo"],
        env={**os.environ, "HOME": str(sandbox)},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )

    assert config.remove() == ""
    servers = json.loads((sandbox / CONFIG_FILENAME).read_text())["mcpServers"]
    assert "other-server" in servers, "removing Theurian must not disturb anything else"
    assert SERVER_NAME not in servers
