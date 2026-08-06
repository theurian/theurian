"""LaunchAgent and systemd user adapters (§24.2, FR-L3, FR-L5).

Real files, fake commands. The plist and the unit are written to a temporary
home and read back, because their *contents* are the contract with launchd and
systemd. ``launchctl`` and ``systemctl`` are recorded rather than executed: a
test that ran them would register a service in the developer's own account, and
CI has no session bus to register it into.
"""

from __future__ import annotations

import plistlib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from theurian.domain.ports.daemon_manager import DaemonManager, ServiceState
from theurian.domain.setup import DifferingFields, SetupError
from theurian.infrastructure.services import detect_manager
from theurian.infrastructure.services.launchagent import LABEL, LaunchAgentManager
from theurian.infrastructure.services.runner import CommandResult, SubprocessRunner
from theurian.infrastructure.services.systemd_user import (
    _RENDERED_DIRECTIVES,
    UNIT_NAME,
    SystemdUserManager,
    _directives,
)

pytestmark = pytest.mark.integration


class RecordingRunner:
    """Records every command and replies from a table."""

    def __init__(
        self, replies: dict[str, CommandResult] | None = None, missing: Sequence[str] = ()
    ) -> None:
        self.commands: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self._replies = replies or {}
        self._missing = set(missing)

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = 20.0,  # noqa: ARG002 - part of the CommandRunner protocol
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(list(args))
        self.environments.append(dict(env or {}))
        for key, reply in self._replies.items():
            if key in " ".join(args):
                return reply
        return CommandResult(exit_code=0)

    def which(self, executable: str) -> str | None:
        return None if executable in self._missing else f"/usr/bin/{executable}"

    def ran(self, fragment: str) -> bool:
        return any(fragment in " ".join(c) for c in self.commands)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


# -- The adapters satisfy the port -----------------------------------------


def test_both_adapters_satisfy_the_daemon_manager_port(home: Path) -> None:
    """ADR-0003. The composition root types against the port, so an adapter
    that drifted from it would only fail where it is wired up."""
    launch = LaunchAgentManager(executable="/usr/local/bin/theurian", home=home)
    systemd = SystemdUserManager(executable="/usr/local/bin/theurian", home=home)

    assert isinstance(launch, DaemonManager)
    assert isinstance(systemd, DaemonManager)


# -- LaunchAgent: the definition -------------------------------------------


def test_the_plist_is_a_user_agent_not_a_root_daemon(home: Path) -> None:
    """The security posture of the whole file. A LaunchDaemon would need
    administrator rights and run as root to read one user's home directory."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)

    assert _AGENTS in str(manager.plist_path)
    assert "LaunchDaemons" not in str(manager.plist_path)
    assert str(manager.plist_path).startswith(str(home))


def test_the_plist_runs_the_daemon_in_the_foreground(home: Path) -> None:
    """launchd supervises the process. A program that forks and exits is
    reported as a crash and restarted forever."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)

    plist = plistlib.loads(manager.render(port=7419, data_directory="/data"))

    assert plist["ProgramArguments"] == [
        "/opt/theurian",
        "daemon",
        "start",
        "--foreground",
        "--port",
        "7419",
    ]
    assert plist["Label"] == LABEL


def test_the_plist_states_the_data_directory_rather_than_inheriting_it(home: Path) -> None:
    """launchd starts agents with almost no environment. Inheriting THEURIAN_DATA_DIR
    from a shell that was never involved is how the service ends up serving the
    wrong knowledge base."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)

    plist = plistlib.loads(manager.render(port=7419, data_directory="/data/dir"))

    assert plist["EnvironmentVariables"]["THEURIAN_DATA_DIR"] == "/data/dir"


def test_a_data_directory_with_xml_characters_survives(home: Path) -> None:
    """Built with plistlib rather than string concatenation. A path containing
    `&` would produce a malformed plist, and launchd's diagnostic for that is
    unhelpful enough to cost an afternoon."""
    hostile = "/Users/a&b/<dir>/'quoted'"
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)

    plist = plistlib.loads(manager.render(port=7419, data_directory=hostile))

    assert plist["EnvironmentVariables"]["THEURIAN_DATA_DIR"] == hostile


def test_the_plist_is_byte_identical_across_renders(home: Path) -> None:
    """Idempotence depends on it: install compares bytes to decide whether
    anything needs to change, so an unstable render means every setup run
    rewrites the plist and restarts the daemon."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)

    first = manager.render(port=7419, data_directory="/data")
    second = manager.render(port=7419, data_directory="/data")

    assert first == second


# -- LaunchAgent: install ---------------------------------------------------


@pytest.mark.asyncio
async def test_installing_writes_the_plist_and_bootstraps_it(home: Path) -> None:
    runner = RecordingRunner()
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=runner, uid=501)

    await manager.install(port=7419, data_directory="/data")

    assert manager.plist_path.is_file()
    assert runner.ran(f"launchctl bootstrap gui/501 {manager.plist_path}")
    assert runner.ran("launchctl kickstart gui/501/dev.theurian.daemon")


@pytest.mark.asyncio
async def test_installing_twice_does_not_rewrite_an_identical_plist(home: Path) -> None:
    """FR-L2. Rewriting would bump the mtime and bootout/bootstrap the service,
    restarting a working daemon for no reason."""
    runner = RecordingRunner()
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=runner, uid=501)
    await manager.install(port=7419, data_directory="/data")
    first_mtime = manager.plist_path.stat().st_mtime_ns
    runner.commands.clear()

    await manager.install(port=7419, data_directory="/data")

    assert manager.plist_path.stat().st_mtime_ns == first_mtime
    assert not runner.ran("bootout"), "a converged service must not be torn down"


@pytest.mark.asyncio
async def test_a_different_existing_plist_is_backed_up_not_overwritten(home: Path) -> None:
    """SEC-18. Whatever is there may be something the user wrote."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=RecordingRunner())
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": ["/old"]}))

    await manager.install(port=7419, data_directory="/data")

    backups = list(manager.plist_path.parent.glob("*.backup"))
    assert len(backups) == 1
    assert plistlib.loads(backups[0].read_bytes())["ProgramArguments"] == ["/old"]
    assert plistlib.loads(manager.plist_path.read_bytes())["ProgramArguments"][0] == "/opt/theurian"


def test_an_identical_plist_reports_no_difference(home: Path) -> None:
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(manager.render(port=7419, data_directory="/data"))

    assert manager.differs_from_installed(port=7419, data_directory="/data") == ""


def test_a_reformatted_plist_is_not_reported_as_a_conflict(home: Path) -> None:
    """Compared by parsed content, not bytes. launchctl rewrites plists in
    binary form; reporting that as a conflict would halt the run for consent
    over a difference that means nothing -- and setup never rewrites a
    conflicting step, so it could never converge afterwards."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    parsed = plistlib.loads(manager.render(port=7419, data_directory="/data"))
    manager.plist_path.write_bytes(plistlib.dumps(parsed, fmt=plistlib.FMT_BINARY))

    assert manager.differs_from_installed(port=7419, data_directory="/data") == ""


def test_a_changed_port_is_reported_as_a_difference(home: Path) -> None:
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(manager.render(port=9999, data_directory="/data"))

    difference = manager.differs_from_installed(port=7419, data_directory="/data")

    assert "ProgramArguments" in difference


def test_an_unreadable_plist_is_reported_rather_than_parsed(home: Path) -> None:
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_text("this is not a plist")

    assert "not a readable plist" in manager.differs_from_installed(port=7419, data_directory="/d")


def test_the_differing_plist_keys_carry_no_values(home: Path) -> None:
    """What `doctor --report` publishes about a plist Theurian did not write.
    `EnvironmentVariables` is a dictionary a person may put a token in."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    installed = plistlib.loads(manager.render(port=7419, data_directory="/data"))
    installed["EnvironmentVariables"] = {"THEURIAN_MCP_TOKEN": "SentinelPlistTokenFFFF"}
    manager.plist_path.write_bytes(plistlib.dumps(installed))

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert fields == DifferingFields(named=("EnvironmentVariables",))
    # Stated as a property of the whole result rather than of `named`: an exact
    # equality above already pins `named`, so a second assertion over it cannot
    # fail on its own. What the CRITICAL broke was a *name* carrying a secret,
    # which is a claim about every string this returns.
    assert "SentinelPlistTokenFFFF" not in repr(fields)


def test_a_plist_path_that_cannot_be_read_is_reported_not_raised(home: Path) -> None:
    """`exists()` is a race, not a guarantee, and the failure is not the parser's.

    A directory where the plist should be passes `exists()` and raises
    `IsADirectoryError` from `read_bytes` -- an `OSError`, which the parser's own
    `except` clause does not name. It escaped as an unhandled exception and the
    state machine degraded it into "could not check daemon-service".
    """
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.mkdir(parents=True)

    assert manager.differing_keys(port=7419, data_directory="/d") == DifferingFields(
        unreadable="is not a readable plist"
    )
    assert "not a readable plist" in manager.differs_from_installed(port=7419, data_directory="/d")


def test_a_plist_whose_root_is_not_a_dictionary_is_reported_not_raised(home: Path) -> None:
    """Valid XML, valid plist, wrong shape. `.get` on a list raised
    `AttributeError` past the parser's own `except`, and the state machine
    degraded it into "could not check daemon-service" -- true, and two steps away
    from "your plist has an array at the top"."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(plistlib.dumps(["not", "a", "dictionary"]))

    assert manager.differing_keys(port=7419, data_directory="/d") == DifferingFields(
        unreadable="is not a readable plist"
    )
    assert "not a readable plist" in manager.differs_from_installed(port=7419, data_directory="/d")


def test_a_plist_key_theurian_never_writes_is_counted_and_not_named(home: Path) -> None:
    """A key in somebody else's file is data, not schema. `render` is the whole
    vocabulary this adapter may publish, so anything outside it is a number."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    installed = plistlib.loads(manager.render(port=7419, data_directory="/data"))
    installed["LimitLoadToSessionType-northwind-acquisition"] = "Aqua"
    manager.plist_path.write_bytes(plistlib.dumps(installed))

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert fields == DifferingFields(named=(), unnamed=1)
    assert "northwind" not in repr(fields)


def test_a_plist_too_damaged_to_parse_says_so_rather_than_naming_nothing(home: Path) -> None:
    """ "No key differs" and "the file does not parse" read as opposite things to
    whoever gets the report, and only the second is an answer. Reported as a
    reason rather than as an empty result, in this adapter's own words."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_text("this is not a plist")

    fields = manager.differing_keys(port=7419, data_directory="/d")

    assert fields == DifferingFields(unreadable="is not a readable plist")
    assert not fields.named


# -- LaunchAgent: status ----------------------------------------------------


@pytest.mark.asyncio
async def test_status_without_a_plist_is_not_installed(home: Path) -> None:
    """The distinction the SessionStart hook branches on: nothing to start."""
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=RecordingRunner())

    assert (await manager.status()).state is ServiceState.NOT_INSTALLED


@pytest.mark.asyncio
async def test_a_plist_that_launchd_does_not_know_is_installed_but_stopped(home: Path) -> None:
    runner = RecordingRunner({"launchctl print": CommandResult(exit_code=113, stderr="Could not")})
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=runner)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(manager.render(port=7419, data_directory="/d"))

    assert (await manager.status()).state is ServiceState.INSTALLED_STOPPED


@pytest.mark.asyncio
async def test_a_loaded_agent_with_a_pid_is_running(home: Path) -> None:
    printed = "\n".join(["dev.theurian.daemon = {", "\tactive count = 1", "\tpid = 4242", "}"])
    runner = RecordingRunner({"launchctl print": CommandResult(exit_code=0, stdout=printed)})
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=runner)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(manager.render(port=7419, data_directory="/d"))

    status = await manager.status()

    assert status.state is ServiceState.RUNNING
    assert status.pid == 4242
    assert status.is_healthy


@pytest.mark.asyncio
async def test_a_loaded_agent_without_a_pid_is_stopped(home: Path) -> None:
    """launchd knows the label but nothing is running. Reporting that as
    RUNNING would make SessionStart skip a start it needed to do."""
    printed = "dev.theurian.daemon = {\n\tactive count = 0\n}"
    runner = RecordingRunner({"launchctl print": CommandResult(exit_code=0, stdout=printed)})
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=runner)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(manager.render(port=7419, data_directory="/d"))

    assert (await manager.status()).state is ServiceState.INSTALLED_STOPPED


@pytest.mark.asyncio
async def test_uninstalling_removes_the_service_but_says_nothing_about_data(home: Path) -> None:
    """FR-L5. Deleting knowledge is a separate, separately confirmed choice."""
    runner = RecordingRunner()
    manager = LaunchAgentManager(executable="/opt/theurian", home=home, runner=runner, uid=501)
    await manager.install(port=7419, data_directory="/data")

    await manager.uninstall()

    assert not manager.plist_path.exists()
    assert runner.ran("launchctl bootout gui/501/dev.theurian.daemon")


# -- systemd -----------------------------------------------------------------


def test_the_unit_is_a_user_unit_not_a_system_unit(home: Path) -> None:
    manager = SystemdUserManager(executable="/opt/theurian", home=home)

    assert ".config/systemd/user" in str(manager.unit_path)
    assert not str(manager.unit_path).startswith("/etc")


def test_the_unit_runs_in_the_foreground_under_systemd(home: Path) -> None:
    manager = SystemdUserManager(executable="/opt/theurian", home=home)

    unit = manager.render(port=7419, data_directory="/data")

    assert "Type=simple" in unit
    assert "ExecStart=/opt/theurian daemon start --foreground --port 7419" in unit
    assert "Environment=THEURIAN_DATA_DIR=/data" in unit


def test_the_unit_confines_the_daemon_to_what_it_needs(home: Path) -> None:
    """SEC-9. Cheap directives that bound what a compromised parser reaches."""
    unit = SystemdUserManager(executable="/opt/theurian", home=home).render(
        port=7419, data_directory="/data"
    )

    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/data" in unit


@pytest.mark.asyncio
async def test_installing_enables_and_starts_the_unit(home: Path) -> None:
    runner = RecordingRunner()
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=runner)

    await manager.install(port=7419, data_directory="/data")

    assert manager.unit_path.is_file()
    assert runner.ran("systemctl --user daemon-reload")
    assert runner.ran(f"systemctl --user enable {UNIT_NAME}")
    assert runner.ran(f"systemctl --user start {UNIT_NAME}")


@pytest.mark.asyncio
async def test_reinstalling_an_identical_unit_does_not_reload(home: Path) -> None:
    runner = RecordingRunner()
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=runner)
    await manager.install(port=7419, data_directory="/data")
    runner.commands.clear()

    await manager.install(port=7419, data_directory="/data")

    assert not runner.ran("daemon-reload")


@pytest.mark.asyncio
async def test_a_different_existing_unit_is_backed_up(home: Path) -> None:
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=RecordingRunner())
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text("[Service]\nExecStart=/something/the/user/wrote\n")

    await manager.install(port=7419, data_directory="/data")

    backups = list(manager.unit_path.parent.glob("*.backup"))
    assert len(backups) == 1
    assert "the/user/wrote" in backups[0].read_text()


def test_a_changed_unit_is_reported_as_a_diff(home: Path) -> None:
    """The user is asked whether the run may proceed around this unit, which
    setup leaves as it is, so they have to see what differs."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(manager.render(port=9999, data_directory="/data"))

    difference = manager.differs_from_installed(port=7419, data_directory="/data")

    assert "--port 9999" in difference
    assert "--port 7419" in difference


def test_the_differing_unit_directives_carry_no_values(home: Path) -> None:
    """What `doctor --report` publishes about a unit Theurian did not write.
    `Environment=` is where a hand-edited unit keeps a literal token."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data").replace(
            "Environment=THEURIAN_DATA_DIR=/data",
            "Environment=THEURIAN_MCP_TOKEN=SentinelUnitTokenGGGG",
        )
    )

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert fields == DifferingFields(named=("Environment",))
    assert "SentinelUnitTokenGGGG" not in repr(fields)


def test_a_repeated_directive_is_not_collapsed(home: Path) -> None:
    """systemd lets `Environment=` repeat. Collapsing repeats would report two
    units as equal when one of them sets a variable the other does not.

    The extra line goes *before* Theurian's, which is what makes the property
    observable. Placed after, a last-wins collapse still leaves the surviving
    value equal to Theurian's, so both implementations answer
    `named=('Environment',)` and the fixture pins nothing -- the same trap as the
    comment-ordering test one section down, which this originally fell into.
    Placed first, a collapse makes the two units compare *equal* and the answer
    becomes `DifferingFields()`.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data").replace(
            "Environment=THEURIAN_DATA_DIR=/data",
            "Environment=THEURIAN_MCP_TOKEN=literal\nEnvironment=THEURIAN_DATA_DIR=/data",
        )
    )

    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields(
        named=("Environment",)
    )


def test_the_named_fields_are_sorted(home: Path) -> None:
    """Two machines holding the same unit must produce the same sentence.

    Load-bearing only for this adapter: the plist and MCP callers hand
    `DifferingFields.over` an already-sorted tuple, while this one hands it a
    set, whose iteration order moves with `PYTHONHASHSEED`. Every other
    assertion in this file names one field, so the sort has never been observed
    -- removing it changed nothing and this is the test that notices.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data")
        .replace("Restart=on-failure", "Restart=always")
        .replace("Type=simple", "Type=exec")
        .replace("WantedBy=default.target", "WantedBy=multi-user.target")
        .replace("Environment=THEURIAN_DATA_DIR=/data", "Environment=THEURIAN_DATA_DIR=/elsewhere")
    )

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert fields.named == ("Environment", "Restart", "Type", "WantedBy")


# -- systemd: the vocabulary a report may publish ----------------------------


def test_the_published_vocabulary_is_exactly_what_render_writes(home: Path) -> None:
    """The constant and the renderer must not drift.

    Stated as a constant rather than derived from `render`, because deriving it
    was a disclosure: `render` interpolates the data directory into text that is
    re-parsed as structure. This is the check that keeps the constant honest
    when a directive is added, and it uses a benign input on purpose -- deriving
    the expectation from a hostile one is the defect.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    written = {name for _, name in _directives(manager.render(port=7419, data_directory="/data"))}

    assert written == set(_RENDERED_DIRECTIVES)


@pytest.mark.parametrize(
    ("executable", "data_directory"),
    [
        ("/opt/theurian", "/data\nX-Injected=value"),
        ("/opt/theurian\nX-Injected=value", "/data"),
        ("/opt/theurian", "/data\rX-Injected=value"),
    ],
)
def test_a_line_break_in_an_interpolated_value_is_refused(
    home: Path, executable: str, data_directory: str
) -> None:
    """A newline does not land in the field it was meant for; it starts a
    directive. `install` writes the data directory at three interpolation
    points, so an injected directive went into the user's unit file three times.
    """
    manager = SystemdUserManager(executable=executable, home=home)

    with pytest.raises(SetupError, match="line break"):
        manager.render(port=7419, data_directory=data_directory)


def test_a_name_only_in_the_installed_unit_cannot_enter_the_vocabulary(home: Path) -> None:
    """The second defence, asserted without the first.

    Rejecting line breaks closes the route that was measured. This asserts the
    property that makes the rejection a defence in depth rather than the only
    one: `over` is handed the constant, so a name that is not in it is counted
    whatever the input did.
    """
    fields = DifferingFields.over(
        {"ExecStart", "X-Northwind-Deal-Code"}, authored=_RENDERED_DIRECTIVES
    )

    assert fields == DifferingFields(named=("ExecStart",), unnamed=1)


def test_a_unit_that_is_not_text_says_so_rather_than_raising(home: Path) -> None:
    """The plist adapter has had this since a directory where the plist should be
    escaped its parser as an `OSError`; the unit adapter had no equivalent, so
    invalid UTF-8 raised out of both comparison methods and the report degraded
    to "could not check daemon-service"."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_bytes(b"[Service]\nExecStart=/opt/\xff\xfe not utf-8\n")

    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields(
        unreadable="is not a readable unit file"
    )
    assert "not a readable unit file" in manager.differs_from_installed(
        port=7419, data_directory="/data"
    )


def test_a_unit_path_that_cannot_be_read_says_so_rather_than_raising(home: Path) -> None:
    """`exists()` is a race, not a guarantee."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.mkdir(parents=True)

    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields(
        unreadable="is not a readable unit file"
    )


# -- systemd: the line parser ------------------------------------------------


def test_a_continuation_joins_with_a_space_and_without_the_backslash(home: Path) -> None:
    """The joined value is compared, so how it joins decides equality.

    Joined without a separator, `ExecStart=a \\` + `b` becomes `ab`; joined
    without stripping the backslash it keeps a stray `\\`. Either makes a unit
    that means what Theurian wrote compare as differing, which is a conflict the
    operator can never resolve -- setup does not rewrite a conflicting step.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data").replace(
            "ExecStart=/opt/theurian daemon start --foreground --port 7419",
            "ExecStart=/opt/theurian daemon start \\\n    --foreground --port 7419",
        )
    )

    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields()


def test_a_dangling_continuation_on_the_last_line_is_still_read(home: Path) -> None:
    """A file whose final line ends in a backslash has no line after it to join.
    Dropped, `ExecStart` disappears from the installed side and is reported as
    differing when it does not differ."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text("[Service]\nExecStart=/opt/theurian daemon start \\")

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert "ExecStart" in fields.named
    assert _directives("[Service]\nExecStart=/opt/x \\") == {
        ("[Service]", "ExecStart"): ("/opt/x",)
    }


def test_whitespace_around_a_directive_is_not_part_of_it(home: Path) -> None:
    """`Restart = on-failure` is the same directive as `Restart=on-failure`.
    Unstripped, the name carries a trailing space and the value a leading one,
    so a unit that agrees with Theurian's reports every one of those lines as
    differing -- and an unstripped *name* is a name outside the vocabulary,
    which turns a real difference into an unnamed count."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data")
        .replace("Restart=on-failure", "Restart = on-failure")
        .replace("Type=simple", "Type\t=\tsimple")
    )

    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields()


def test_a_continuation_line_is_the_value_above_it_and_never_a_name(home: Path) -> None:
    """systemd continues a line ending in a backslash, so the next line is part
    of the *value*. Split on its own `=`, its left-hand side became a directive
    name -- and a directive name is what this adapter publishes:

        ExecStart=… daemon start \\
            --header "Authorization: Bearer <token>"

    put the header into `Fields that differ`, inside the sentence that promises
    the values are withheld. Two defences, both asserted here: the parser joins
    the line, and the name would not have been published anyway because `render`
    does not produce it.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data").replace(
            "ExecStart=/opt/theurian daemon start --foreground --port 7419",
            "ExecStart=/opt/theurian daemon start --foreground \\\n"
            '    --header "Authorization: Bearer SentinelContinuationHHHH" --port=7419',
        )
    )

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert fields == DifferingFields(named=("ExecStart",))
    assert "SentinelContinuationHHHH" not in repr(fields)


def test_a_directive_theurian_never_writes_is_counted_and_not_named(home: Path) -> None:
    """A unit file is somebody else's text in a format Theurian does not own, so
    a name read out of it is data. Only `render`'s own vocabulary is published."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data").replace(
            "Restart=on-failure",
            "Restart=on-failure\nExecStartPre=/opt/northwind-acquisition/preflight.sh",
        )
    )

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert fields == DifferingFields(named=(), unnamed=1)
    assert "northwind" not in repr(fields)


def test_a_unit_differing_only_in_its_comments_names_no_directive(home: Path) -> None:
    """A real difference the caller must still report, with no field to name.
    The published sentence withholds it whole rather than claiming none exists.

    The comment carries an `=` deliberately. Without one it is dropped for want
    of a separator rather than for being a comment, so deleting the comment
    filter entirely left this test green -- and `# ops note: legacy_key=abc`
    would have gone out as a published field name.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data")
        + "# ops note: legacy_key=northwind-acquisition\n"
    )

    fields = manager.differing_keys(port=7419, data_directory="/data")

    assert manager.differs_from_installed(port=7419, data_directory="/data") != ""
    assert fields == DifferingFields()
    assert "northwind" not in repr(fields)


def test_a_comment_ending_in_a_backslash_does_not_swallow_the_next_directive(
    home: Path,
) -> None:
    """Comments are dropped before continuations are joined, not after.

    Joined first, a comment ending in a backslash absorbs the line below it and
    the absorbed directive disappears from the comparison. Whether systemd itself
    continues a comment has varied by release, and this is not a format Theurian
    owns, so the choice that cannot hide a difference is the one taken.

    The directive below the comment is left *identical* to what Theurian would
    install, which is what makes the two orderings distinguishable: swallowed,
    `Restart` goes missing from the installed side and is named as differing
    when it does not differ. A test that changed the directive as well would
    report it as differing under either ordering and pin nothing -- which is what
    the first version of this test did.
    """
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data").replace(
            "Restart=on-failure",
            "# a note the operator left, ending in a backslash \\\nRestart=on-failure",
        )
    )

    assert manager.differs_from_installed(port=7419, data_directory="/data") != ""
    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields()


def test_a_directive_in_the_wrong_section_is_reported_as_differing(home: Path) -> None:
    """systemd's sections are not decoration: `Environment=` under `[Unit]` does
    nothing. Compared on the bare name, a unit with it misplaced there read as
    identical to a correct one, and the operator was told no directive differs."""
    manager = SystemdUserManager(executable="/opt/theurian", home=home)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(
        manager.render(port=7419, data_directory="/data")
        .replace("Environment=THEURIAN_DATA_DIR=/data\n", "")
        .replace(
            "Description=Theurian knowledge daemon",
            "Description=Theurian knowledge daemon\nEnvironment=THEURIAN_DATA_DIR=/data",
        )
    )

    assert manager.differing_keys(port=7419, data_directory="/data") == DifferingFields(
        named=("Environment",)
    )


@pytest.mark.asyncio
async def test_an_inactive_unit_is_installed_but_stopped(home: Path) -> None:
    runner = RecordingRunner({"is-active": CommandResult(exit_code=3, stdout="inactive")})
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=runner)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(manager.render(port=7419, data_directory="/d"))

    assert (await manager.status()).state is ServiceState.INSTALLED_STOPPED


@pytest.mark.asyncio
async def test_an_active_unit_reports_its_pid(home: Path) -> None:
    runner = RecordingRunner(
        {
            "is-active": CommandResult(exit_code=0, stdout="active"),
            "MainPID": CommandResult(exit_code=0, stdout="909\n"),
        }
    )
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=runner)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text(manager.render(port=7419, data_directory="/d"))

    status = await manager.status()

    assert status.state is ServiceState.RUNNING
    assert status.pid == 909


def test_systemctl_without_a_user_manager_is_unsupported(home: Path) -> None:
    """A container or WSL install often ships systemctl with no user manager.
    Installing a unit there produces a service that can never start."""
    runner = RecordingRunner(
        {"is-system-running": CommandResult(exit_code=127, stderr="not found")}
    )
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=runner)

    assert not manager.is_supported()


def test_a_missing_systemctl_is_unsupported(home: Path) -> None:
    manager = SystemdUserManager(
        executable="/opt/theurian", home=home, runner=RecordingRunner(missing=["systemctl"])
    )

    assert not manager.is_supported()


def test_lingering_failure_is_a_warning_not_an_error(home: Path) -> None:
    """§6.1. loginctl is often refused by policy, and a daemon that runs while
    the user is logged in is still a working Theurian."""
    runner = RecordingRunner({"enable-linger": CommandResult(exit_code=1, stderr="denied")})
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=runner)

    warning = manager.enable_lingering()

    assert "log out" in warning
    assert "enable-linger" in warning


def test_lingering_success_produces_no_warning(home: Path) -> None:
    manager = SystemdUserManager(executable="/opt/theurian", home=home, runner=RecordingRunner())

    assert manager.enable_lingering() == ""


# -- Detection ---------------------------------------------------------------


def test_macos_gets_the_launch_agent(home: Path) -> None:
    manager = detect_manager(
        executable="/opt/theurian", home=home, runner=RecordingRunner(), platform="darwin"
    )

    assert manager is not None
    assert manager.platform_id == "launchagent"


def test_linux_gets_the_systemd_user_unit(home: Path) -> None:
    manager = detect_manager(
        executable="/opt/theurian", home=home, runner=RecordingRunner(), platform="linux"
    )

    assert manager is not None
    assert manager.platform_id == "systemd-user"


def test_an_unsupported_platform_yields_no_manager(home: Path) -> None:
    """None is a supported outcome. Theurian works with the daemon started by
    hand; what it must never do is claim to have installed a service."""
    manager = detect_manager(
        executable="/opt/theurian", home=home, runner=RecordingRunner(), platform="win32"
    )

    assert manager is None


def test_a_platform_whose_tools_are_absent_yields_no_manager(home: Path) -> None:
    manager = detect_manager(
        executable="/opt/theurian",
        home=home,
        runner=RecordingRunner(missing=["launchctl"]),
        platform="darwin",
    )

    assert manager is None


# -- The real runner ---------------------------------------------------------


def test_a_missing_executable_is_a_result_not_an_exception() -> None:
    """This runs inside SessionStart. A traceback in front of someone who just
    opened a terminal is not an acceptable way to report a missing tool."""
    result = SubprocessRunner().run(["theurian-does-not-exist-anywhere"])

    assert not result.ok
    assert "not found" in result.output


def test_the_real_runner_reports_a_non_zero_exit_as_data() -> None:
    result = SubprocessRunner().run(["sh", "-c", "echo out; echo err >&2; exit 7"])

    assert result.exit_code == 7
    assert "out" in result.stdout
    assert "err" in result.stderr
    assert "out" in result.output and "err" in result.output


def test_a_hanging_command_is_cut_off() -> None:
    """A hung launchctl must not hang a setup run a person is waiting on."""
    result = SubprocessRunner().run(["sleep", "30"], timeout=0.3)

    assert not result.ok
    assert "timed out" in result.output


_AGENTS = "Library/LaunchAgents"
