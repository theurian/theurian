"""The setup state machine, end to end against a temporary home (§6, FR-L1/L2).

Real files, fake collaborators. The data directory, the token, and the env file
are genuinely created and read back, because their modes are the security
property. The service manager and Claude Code are fakes: installing a real
LaunchAgent would register it in the developer's own login session, which no
amount of ``HOME`` redirection prevents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, override

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.domain.setup import SetupState, StepId, StepOutcome, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.security.env_file import TOKEN_KEY

pytestmark = pytest.mark.integration


@pytest.fixture
def context(tmp_path: Path) -> SetupContext:
    """A machine where nothing is set up yet, and no daemon is running."""
    return _with(tmp_path)


def _installed_executable(tmp_path: Path) -> str:
    """A file that really exists.

    The `core-present` probe requires an absolute path that resolves, because a
    service unit invokes Theurian by absolute path -- so a fixture pointing at a
    path that does not exist would be testing the abort case by accident.
    """
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    return str(executable)


def _service(context: SetupContext) -> SetupService:
    return SetupService(context)


# -- Dry run ---------------------------------------------------------------


def test_a_dry_run_changes_nothing_at_all(context: SetupContext) -> None:
    """The whole point of showing a plan first. If `--dry-run` created even the
    data directory, the plan would not be a plan."""
    report = _service(context).run(SetupRequest(dry_run=True))

    assert report.state is SetupState.PLAN_BUILT
    assert not context.data_dir.exists()
    assert report.changed_paths == ()


def test_the_plan_names_every_file_it_would_create(context: SetupContext) -> None:
    """`uninstall --dry-run` has to be able to enumerate what setup created."""
    plan = _service(context).plan()

    assert str(context.data_dir) in plan.paths
    assert str(context.auth_dir / TOKEN_KEY) in plan.paths
    assert str(context.env_file) in plan.paths


def test_every_specified_step_is_reported(context: SetupContext) -> None:
    """A step that silently never ran would leave the report looking complete."""
    report = _service(context).run(SetupRequest(dry_run=True))

    assert {step.step_id for step in report.steps} == set(StepId)


# -- Applying ---------------------------------------------------------------


def test_a_cold_setup_creates_everything_with_correct_modes(context: SetupContext) -> None:
    """§20's cold-setup case. The modes are the security property, so they are
    asserted rather than assumed."""
    report = _service(context).run()

    assert report.succeeded, report.warnings
    assert context.data_dir.stat().st_mode & 0o777 == 0o700
    assert (context.auth_dir / TOKEN_KEY).stat().st_mode & 0o777 == 0o600
    assert context.env_file.stat().st_mode & 0o777 == 0o600


def test_the_env_file_references_the_token_rather_than_embedding_it(
    context: SetupContext,
) -> None:
    """SEC-5. The secret lives in one place; everything else points at it."""
    _service(context).run()

    contents = context.env_file.read_text()
    token = (context.auth_dir / TOKEN_KEY).read_text().strip()

    assert "THEURIAN_MCP_TOKEN" in contents
    assert token not in contents, "the literal token must never be written into a config file"


def test_the_mcp_entry_is_installed_without_the_literal_token(context: SetupContext) -> None:
    _service(context).run()

    entry = context.mcp_config.installed_entry()
    token = (context.auth_dir / TOKEN_KEY).read_text().strip()

    assert entry is not None
    assert entry["headers"]["Authorization"] == "Bearer ${THEURIAN_MCP_TOKEN}"
    assert token not in json.dumps(entry)


def test_the_service_is_registered_and_started(context: SetupContext) -> None:
    _service(context).run()

    service = context.service
    assert isinstance(service, FakeService)
    assert service.installed


def test_the_report_lists_what_changed(context: SetupContext) -> None:
    report = _service(context).run()

    assert str(context.data_dir) in report.changed_paths
    assert str(context.env_file) in report.changed_paths


# -- Idempotence (§6.3) ------------------------------------------------------


def test_a_second_run_changes_nothing(context: SetupContext) -> None:
    """The contract that makes setup safe to put in front of every user on
    every machine: `setup(setup(E)) == setup(E)`."""
    first = _service(context).run()
    assert first.succeeded, first.warnings

    second = _service(context).run()

    assert second.succeeded, second.warnings
    assert second.changed_paths == (), "a converged machine must report no changes"
    assert all(step.outcome is not StepOutcome.CHANGED for step in second.steps), (
        "no step may change anything on a second run"
    )


def test_a_second_plan_is_empty(context: SetupContext) -> None:
    _service(context).run()

    assert _service(context).plan().is_empty


def test_a_second_run_never_regenerates_the_token(context: SetupContext) -> None:
    """ADR-0011. Silently replacing a token breaks every configured client at
    once, with no explanation."""
    _service(context).run()
    first = (context.auth_dir / TOKEN_KEY).read_text()

    _service(context).run()

    assert (context.auth_dir / TOKEN_KEY).read_text() == first


def test_a_second_run_does_not_touch_the_mcp_entry(context: SetupContext) -> None:
    _service(context).run()
    config = context.mcp_config
    assert isinstance(config, FakeMcpConfig)
    installs = config.installs

    _service(context).run()

    assert config.installs == installs


# -- Consent (SEC-18) --------------------------------------------------------


def test_a_conflict_stops_before_replacing_anything(tmp_path: Path) -> None:
    """A conflict means something the user did not put there is about to be
    replaced. Silence is not agreement."""
    context = _with(tmp_path, service=FakeService(installed=True, difference="ExecStart differs"))

    report = _service(context).run()

    assert report.state is SetupState.AWAITING_CONSENT
    assert not report.succeeded
    assert not context.data_dir.exists(), "nothing may be created while consent is pending"


def test_the_conflict_is_reported_with_its_difference(tmp_path: Path) -> None:
    context = _with(tmp_path, service=FakeService(installed=True, difference="ExecStart differs"))

    report = _service(context).run()
    step = report.step(StepId.DAEMON_SERVICE)

    assert step is not None
    assert step.status is StepStatus.CONFLICTING
    assert "ExecStart differs" in step.detail


def test_an_approved_conflict_is_resolved(tmp_path: Path) -> None:
    context = _with(tmp_path, service=FakeService(installed=True, difference="ExecStart differs"))

    report = _service(context).run(SetupRequest(approve_conflicts=True))

    assert report.state in {SetupState.CONVERGED, SetupState.DEGRADED}
    assert context.data_dir.exists()


def test_a_dry_run_still_reports_a_conflict_without_asking(tmp_path: Path) -> None:
    """`--dry-run` is how the plugin shows the plan, so it has to surface the
    conflict the user is about to be asked about."""
    context = _with(tmp_path, service=FakeService(installed=True, difference="differs"))

    report = _service(context).run(SetupRequest(dry_run=True))

    assert report.state is SetupState.PLAN_BUILT
    step = report.step(StepId.DAEMON_SERVICE)
    assert step is not None and step.needs_consent


# -- Degrading rather than failing (§6.1) ------------------------------------


def test_a_machine_without_claude_code_still_converges(tmp_path: Path) -> None:
    """Theurian serves any MCP client. A missing one is a skipped step."""
    context = _with(tmp_path, mcp_config=FakeMcpConfig(available=False))

    report = _service(context).run()

    assert report.succeeded
    step = report.step(StepId.MCP_CONNECTION)
    assert step is not None and step.status is StepStatus.NOT_APPLICABLE


def test_a_platform_without_a_service_manager_still_converges(tmp_path: Path) -> None:
    """The daemon can be started by hand. Refusing to set up the rest would
    help nobody."""
    context = _with(tmp_path, service=None)

    report = _service(context).run()

    assert report.succeeded
    assert (context.auth_dir / TOKEN_KEY).is_file()
    step = report.step(StepId.DAEMON_SERVICE)
    assert step is not None and step.status is StepStatus.NOT_APPLICABLE
    assert "daemon start --foreground" in step.detail


def test_a_failing_optional_step_degrades_rather_than_aborting(tmp_path: Path) -> None:
    """A missing MCP connection must not undo a working local knowledge base."""

    class RefusesToInstall(FakeMcpConfig):
        @override
        def install(self, spec: Any) -> str:
            return "claude is broken"

    context = _with(tmp_path, mcp_config=RefusesToInstall())

    report = _service(context).run()

    assert report.state is SetupState.DEGRADED
    assert report.succeeded, "degraded is success with warnings (§6.1)"
    assert (context.auth_dir / TOKEN_KEY).is_file(), "the parts that worked must stand"


def test_a_probe_that_raises_becomes_a_reported_conflict(tmp_path: Path) -> None:
    """One broken probe must not take the run down with it."""

    class Explodes(FakeMcpConfig):
        @override
        def serena_detected(self) -> bool:
            return False

        @override
        def difference(self, spec: Any) -> str:
            raise RuntimeError("the config is on fire")

    context = _with(tmp_path, mcp_config=Explodes(entry={"type": "http"}))

    report = _service(context).run(SetupRequest(dry_run=True))
    step = report.step(StepId.MCP_CONNECTION)

    assert step is not None
    assert step.status is StepStatus.CONFLICTING
    assert "on fire" in step.detail
    assert report.step(StepId.TOKEN) is not None, "the other steps must still be probed"


# -- Aborting ---------------------------------------------------------------


def test_an_unlocatable_executable_aborts_before_creating_anything(tmp_path: Path) -> None:
    """A service unit that cannot name Theurian is a service that can never
    start, so setup stops rather than installing one."""
    context = _with(tmp_path, executable="")

    report = _service(context).run()

    assert report.state is SetupState.ABORTED
    assert not context.data_dir.exists()


# -- Verification ------------------------------------------------------------


def test_the_report_states_what_is_true_not_what_was_attempted(tmp_path: Path) -> None:
    """A step whose apply silently did nothing must not be reported as done.

    Writing a plist and having launchd accept it are separate events, and the
    report is only worth trusting if it reflects the second one.
    """

    class PretendsToInstall(FakeService):
        @override
        async def install(self, *, port: int, data_directory: str) -> None:
            """Reports success, registers nothing."""

    context = _with(tmp_path, service=PretendsToInstall())

    report = _service(context).run()

    assert report.state is SetupState.DEGRADED
    assert any("daemon-service" in warning for warning in report.warnings)


# -- The journal (§6.4) ------------------------------------------------------


def test_every_applied_step_is_journalled(context: SetupContext) -> None:
    """A crash mid-run must leave a readable record of what had been done."""
    service = _service(context)
    service.run()

    entries = [
        json.loads(line) for line in service.journal_path.read_text().splitlines() if line.strip()
    ]

    assert entries
    assert {e["step"] for e in entries} >= {"data-directory", "token", "env-reference"}
    assert all(e["event"] == "applied" for e in entries)


def test_the_journal_is_appended_to_not_rewritten(context: SetupContext) -> None:
    """The record of the first run has to survive the second."""
    service = _service(context)
    service.run()
    first = len(service.journal_path.read_text().splitlines())

    service.run()

    assert len(service.journal_path.read_text().splitlines()) >= first


def _with(tmp_path: Path, **overrides: Any) -> SetupContext:
    """A context with one thing changed."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_dir = home / ".theurian"
    service = overrides.pop("service", FakeService()) if "service" in overrides else FakeService()
    defaults: dict[str, Any] = {
        "home": home,
        "data_dir": data_dir,
        "port": 7419,
        "project_root": None,
        "connection": ConnectionSpec(port=7419),
        "mcp_config": FakeMcpConfig(),
        "secrets": FileSecretStore(data_dir),
        # Starting the service is what makes it answer -- the same causality the
        # real probe observes. A health function that always returned None would
        # make the daemon-running step permanently unconvergeable, and every
        # idempotence assertion below would be testing that instead.
        "health": lambda: (
            {"dataDir": str(data_dir)} if getattr(service, "started", False) else None
        ),
        "service": service,
        "executable": _installed_executable(tmp_path),
    }
    return SetupContext(**{**defaults, **overrides})
