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
from datetime import datetime
from pathlib import Path
from typing import Any, override

import pytest

from theurian.domain.setup import DifferingFields
from theurian.infrastructure.claude import mcp_config as mcp_config_module
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


def test_the_operators_diff_names_keys_theurian_never_writes(home: Path) -> None:
    """`difference` is the operator's own output and holds nothing back.

    Worth its own test because its publishable sibling deliberately does the
    opposite, and the two are one edit apart: `difference` once took its key
    list from `differing_keys`, which would have silently dropped `command` and
    `args` from the diff the user is asked to decide on.
    """
    _write_servers(home, {SERVER_NAME: {"command": "theurian", "args": ["mcp"]}})

    difference = _config(home).difference(ConnectionSpec())

    assert "command" in difference
    assert "args" in difference
    assert "url" in difference, "and the fields Theurian would install, missing here"


def test_differing_keys_answers_for_an_absent_entry(home: Path) -> None:
    """Reachable through the port, not only through `difference`.

    Nothing in setup calls this without a difference in hand, but it is a public
    method on `McpClientConfig`, and answering `AttributeError` to "what differs
    from an entry that is not there" would be a poor contract.
    """
    assert _config(home).differing_keys(ConnectionSpec()) == DifferingFields()


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
    assert list(home.glob(f"{CONFIG_FILENAME}.*.backup")) == [], (
        "a fresh install has nothing to destroy and must not back anything up"
    )


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
    assert list(home.glob(f"{CONFIG_FILENAME}.*.backup")) == [], (
        "the second, idempotent run has nothing to destroy either"
    )


def test_replacing_a_conflicting_entry_removes_it_first(home: Path) -> None:
    """`claude mcp add` refuses to overwrite -- it reports "already exists" and
    exits 0. Adding over a conflict would silently do nothing."""
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://127.0.0.1:9999/mcp"}})
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    assert config.install(ConnectionSpec()) == ""

    assert any("remove" in " ".join(c) for c in runner.commands)
    assert config.installed_entry() == ConnectionSpec().as_entry()


def test_a_failed_removal_is_reported_not_swallowed(home: Path) -> None:
    """SEC-18, issue #27. `install`'s removal branch checks ``removal.ok`` and
    returns a failure that names the removal -- but only if that check
    actually reaches the caller. A mutation that turned the returned message
    into an empty string survived the full suite, so this pins the outcome at
    this port's level: the conflicting entry stays exactly as it was, `claude
    mcp add` is never attempted on top of a failed removal, and the caller is
    told why -- rather than being handed a `success` result for a run that
    changed nothing it was trying to change.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    runner = FakeClaude(home / CONFIG_FILENAME, fail="mcp remove")
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    failure = config.install(ConnectionSpec())

    assert failure != ""
    assert "remov" in failure.lower()
    assert not any(command[1:3] == ["mcp", "add"] for command in runner.commands), (
        "add must never be attempted on top of a removal that failed"
    )
    assert config.installed_entry() == {"type": "http", "url": "http://old/mcp"}


def test_a_backup_exists_before_the_destructive_remove_runs(home: Path) -> None:
    """SEC-18, issue #27. `install` must back up the config before running the
    destructive `claude mcp remove` on a conflicting entry.

    An end-state check ("a backup exists after `install` returns") would stay
    green even if the backup were taken *after* the removal -- by which point
    the removal has already destroyed what it was meant to preserve. So this
    pins the ordering directly: the fake `claude` runner records, at the exact
    moment it is asked to run `mcp remove`, whether a `*.backup` sibling of the
    config already exists and what it contains. Only that moment tells us the
    backup came first.
    """

    class BackupOrderingClaude(FakeClaude):
        """Snapshots the config's `.backup` siblings when asked to `mcp remove`."""

        def __init__(self, config: Path) -> None:
            super().__init__(config)
            self.backups_seen_at_removal: list[Path] = []

        @override
        def run(
            self,
            args: Sequence[str],
            *,
            timeout: float = 20.0,
            env: Mapping[str, str] | None = None,
        ) -> CommandResult:
            if args[1:3] == ["mcp", "remove"]:
                self.backups_seen_at_removal = sorted(
                    self._config.parent.glob(f"{self._config.name}.*.backup")
                )
            return super().run(args, timeout=timeout, env=env)

    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    original_bytes = (home / CONFIG_FILENAME).read_bytes()
    runner = BackupOrderingClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    assert config.install(ConnectionSpec()) == ""

    assert runner.backups_seen_at_removal, "no backup existed at the moment `mcp remove` ran"
    assert runner.backups_seen_at_removal[0].read_bytes() == original_bytes
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


def test_a_short_backup_write_aborts_the_removal_and_leaves_no_partial_backup(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. `back_up` calls `os.write(descriptor, payload)` and
    discards the return value -- but POSIX `write()` is permitted to write
    fewer bytes than requested and still return normally; nothing about that
    is an error condition the OS reports on its own. Reproduced: a wrapper
    around `os.write` that truncates the write to the backup descriptor lets
    `back_up` return a path with no exception, so `install`'s removal branch
    treats a truncated file under a `.backup` name as a successful backup and
    proceeds to run the destructive `claude mcp remove` on top of it.

    Only the write to the backup file's own descriptor is truncated -- tracked
    by wrapping `os.open` to note which descriptor a `.backup` path was opened
    on -- so this does not disturb any other write the test process makes.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    original_bytes = (home / CONFIG_FILENAME).read_bytes()
    cap = 32
    assert len(original_bytes) > cap, "the payload must exceed the cap for a short write to occur"

    real_open = os.open
    real_write = os.write
    backup_descriptors: set[int] = set()

    def tracking_open(path: Any, flags: int, mode: int = 0o777, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if str(path).endswith(".backup"):
            backup_descriptors.add(descriptor)
        return descriptor

    def short_write(fd: int, data: bytes) -> int:
        if fd in backup_descriptors:
            return real_write(fd, data[:cap])
        return real_write(fd, data)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "write", short_write)
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    failure = config.install(ConnectionSpec())

    assert failure != "", "a short backup write must not be treated as a successful backup"
    assert runner.commands == [], (
        "claude must never be invoked once the backup itself is incomplete"
    )
    assert config.installed_entry() == {"type": "http", "url": "http://old/mcp"}
    assert list(home.glob(f"{CONFIG_FILENAME}.*.backup")) == [], (
        "a truncated file under a `.backup` name is not a backup"
    )


def test_a_backup_is_born_0600_never_briefly_wider(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. `back_up` used to `write_bytes` and only then
    `chmod(0o600)`, leaving a window between creation and the mode change
    during which the backup -- a copy of the user's live Claude Code state,
    a bearer token included -- is as readable as the process umask allows.
    Measured: 0644 under the common umask 022.

    Neutralising `Path.chmod` alone is not enough: a write-then-`chmod`
    implementation may just as well call the module-level `os.chmod` on the
    path, which is a different callable and survives a `Path.chmod` no-op
    untouched -- a mutation doing exactly that (`write_bytes` + `os.chmod`,
    keeping the name-uniqueness loop) passed this test before both were
    neutralised. `os.fchmod` is left real: the fix legitimately calls it on
    the still-restrictive descriptor `os.open` returned, and stubbing it out
    would make the assertion pass for the wrong reason.

    With both `chmod`s doing nothing, the only way the file can end up 0600
    is if the mode came from the call that created it, so it was never
    briefly wider.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    config = _config(home)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)  # noqa: ARG005
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)  # noqa: ARG005
    previous_umask = os.umask(0o000)

    try:
        backup = config.back_up()
    finally:
        os.umask(previous_umask)

    assert backup is not None
    assert backup.stat().st_mode & 0o777 == 0o600, "the backup was wider than 0600 at some point"


def test_a_backup_is_exactly_0600_regardless_of_umask(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. `back_up` passes mode 0o600 to `os.open`, but that
    mode is ANDed with the process umask the same as any `creat()` call --
    `os.open` never widens what was asked for, but an unusual umask can still
    strip bits from it. Under umask 0o400 (which clears the owner-read bit),
    the file this creates is 0200: the owner cannot read their own backup, and
    the CHANGELOG's unconditional "created 0600 from birth" is false for this
    umask.

    Two properties, not one: the mode can never be *wider* than 0600 -- umask
    only removes bits, and `os.open`'s own mode argument is the sole point of
    creation, so there is no window in which more bits are set than that --
    but "never wider" is not "exactly 0600", and only the second is what the
    CHANGELOG asserts unconditionally. The fix restores it with
    `os.fchmod(descriptor, 0o600)` on the still-open, still-private descriptor
    after `os.open` returns, which is safe for the same reason: the descriptor
    was never reachable by another process at a wider mode in between.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    config = _config(home)
    previous_umask = os.umask(0o400)

    try:
        backup = config.back_up()
    finally:
        os.umask(previous_umask)

    assert backup is not None
    mode = backup.stat().st_mode & 0o777
    assert mode & ~0o600 == 0, "the backup must never be wider than 0600, regardless of umask"
    assert mode == 0o600, "the backup must be exactly 0600, not merely a subset of it"


def test_two_backups_in_the_same_second_do_not_collide(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. The stamp `back_up` names its file with is
    second-precision; two calls landing in the same UTC second must not let
    the second overwrite the first, which would destroy the very artefact the
    backup exists to preserve. Measured: a full-suite mutation that hard-coded
    a fixed stamp survived all 2037 tests, so this pins the *property* -- two
    distinct files, the first one's content intact -- rather than the stamp's
    own format, which a fix is free to change.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://first/mcp"}})
    config = _config(home)

    class _FrozenDatetime(datetime):
        @classmethod
        @override
        def now(cls, tz: Any = None) -> _FrozenDatetime:
            return cls(2024, 1, 1, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(mcp_config_module, "datetime", _FrozenDatetime)

    first_backup = config.back_up()
    assert first_backup is not None
    first_bytes = first_backup.read_bytes()

    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://second/mcp"}})
    second_backup = config.back_up()

    assert second_backup is not None
    assert first_backup != second_backup, (
        "the same UTC second must not make the second call reuse the first's name"
    )
    backups = sorted(home.glob(f"{CONFIG_FILENAME}.*.backup"))
    assert len(backups) == 2
    assert first_backup.read_bytes() == first_bytes, (
        "the first backup's content must survive the second call"
    )


def test_a_failing_backup_is_installs_own_failure_not_an_exception(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. `install`'s port contract (`McpClientConfig`) is
    "returns a failure message, or empty" -- it never raises. Reproduced
    directly: a `PermissionError` out of `back_up` (an unwritable home, a full
    disk, a symlink race) used to propagate straight out of `install`, which
    the removal branch is not allowed to do -- origin/main's `install` was
    total.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    def _raise(self: ClaudeCodeMcpConfig) -> Path | None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(ClaudeCodeMcpConfig, "back_up", _raise)

    failure = config.install(ConnectionSpec())

    assert failure != ""
    assert "back" in failure.lower(), "the failure must name the backup, not just fail generically"
    assert runner.commands == [], "claude must never be invoked once the backup itself failed"
    assert config.installed_entry() == {"type": "http", "url": "http://old/mcp"}


def test_a_backup_name_collision_is_retried_but_no_other_open_failure_is(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. `back_up`'s naming loop catches `FileExistsError`
    specifically, not `OSError` broadly -- a name collision (two backups in the
    same UTC second) is the only condition worth retrying; anything else
    `os.open` can raise (an unwritable home, a full disk, a permission
    problem) is a real failure and must propagate on the first attempt rather
    than spin through all `_MAX_BACKUP_ATTEMPTS` names before finally giving
    up with the wrong diagnosis ("could not find a free name" instead of
    "permission denied").
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)
    attempts: list[int] = []

    def always_permission_denied(
        path: Any,  # part of the os.open signature being replaced
        flags: int,
        mode: int = 0o777,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        attempts.append(1)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", always_permission_denied)

    failure = config.install(ConnectionSpec())

    assert len(attempts) == 1, (
        "only FileExistsError should be retried; any other OSError must propagate immediately"
    )
    assert failure != ""
    assert "back" in failure.lower()
    assert runner.commands == [], "claude must never be invoked once the backup itself failed"
    assert config.installed_entry() == {"type": "http", "url": "http://old/mcp"}


def test_exhausting_every_backup_name_is_installs_own_failure_too(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-18, issue #27. When every candidate name in the same UTC second
    collides -- `_MAX_BACKUP_ATTEMPTS` of them -- `back_up` gives up and
    raises its own `OSError` naming the reason, rather than looping forever.
    `install` must fold that into its own failure string, exactly like any
    other backup failure, and must not have run `claude` on top of it.
    """
    _write_servers(home, {SERVER_NAME: {"type": "http", "url": "http://old/mcp"}})
    runner = FakeClaude(home / CONFIG_FILENAME)
    config = ClaudeCodeMcpConfig(home=home, runner=runner)

    def always_exists(
        path: Any,  # part of the os.open signature being replaced
        flags: int,
        mode: int = 0o777,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        raise FileExistsError(17, "File exists")

    monkeypatch.setattr(os, "open", always_exists)

    failure = config.install(ConnectionSpec())

    assert failure != ""
    assert "free backup name" in failure, "the exhaustion diagnosis must reach the caller"
    assert runner.commands == [], "claude must never be invoked once the backup itself failed"
    assert config.installed_entry() == {"type": "http", "url": "http://old/mcp"}
    assert list(home.glob(f"{CONFIG_FILENAME}.*.backup")) == []


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
