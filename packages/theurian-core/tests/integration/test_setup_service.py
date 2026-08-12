"""The setup state machine, end to end against a temporary home (§6, FR-L1/L2).

Real files, fake collaborators. The data directory, the token, and the env file
are genuinely created and read back, because their modes are the security
property. The service manager and Claude Code are fakes: installing a real
LaunchAgent would register it in the developer's own login session, which no
amount of ``HOME`` redirection prevents.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.project_service import ProjectRegistry
from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import STEPS, Step, probe_project_registered
from theurian.domain.setup import (
    SetupReport,
    SetupState,
    SetupStep,
    StepId,
    StepOutcome,
    StepStatus,
)
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.security.env_file import TOKEN_KEY

pytestmark = pytest.mark.integration


@pytest.fixture
def context(tmp_path: Path) -> SetupContext:
    """A machine where nothing is set up yet, and no daemon is running."""
    return _with(tmp_path)


def _installed_executable(tmp_path: Path) -> str:
    """A file a service manager could really exec.

    The `core-present` probe requires an absolute path that resolves *and can be
    started*, because a service unit invokes Theurian by absolute path -- so a
    fixture pointing at a path that does not exist, or at a 0644 file, would be
    testing the abort case by accident.

    The mode is load-bearing rather than decorative: this fixture used
    `touch()`, and every setup test in this file asserted `satisfied` against a
    Core that could not be executed (#49).
    """
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
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
    """`uninstall --dry-run` has to be able to enumerate what setup created.

    NFR-12, and a requirement rather than a description of today: `uninstall`
    reads neither this aggregate nor the steps' own paths. See `SetupStep.paths`.
    """
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
    """A conflict means setup found something it did not install. It stops to say
    so and creates nothing while the answer is pending -- and replaces nothing
    after one either. Silence is not agreement."""
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


def test_approving_a_conflict_lets_the_run_proceed_and_leaves_the_conflict_unapplied(
    tmp_path: Path,
) -> None:
    """What consent actually buys, which is not what this test used to be called.

    ``approve_conflicts`` is a gate on the *run*, not an authorisation to
    replace: ``_apply`` only ever calls a step's action when the plan said
    ``would_change``, and a CONFLICTING step is recorded ``UNCHANGED`` and
    skipped. So the difference the user acknowledged is still there when the run
    ends, and the verification pass says so rather than reporting success.

    Named ``..._is_resolved`` before, asserting only that the state was one of
    two values and that the data directory existed -- both true of a run that
    replaced the user's service definition without asking, and both true of this
    one, which replaces nothing. The outcome is what tells them apart.
    """
    context = _with(tmp_path, service=FakeService(installed=True, difference="ExecStart differs"))

    report = _service(context).run(SetupRequest(approve_conflicts=True))

    step = report.step(StepId.DAEMON_SERVICE)
    assert step is not None
    assert step.outcome is StepOutcome.UNCHANGED, "consent is not authority to overwrite"
    assert step.status is StepStatus.CONFLICTING, "the difference survives the run"
    assert (context.auth_dir / TOKEN_KEY).is_file(), "the steps that were not in conflict did run"
    assert report.state is SetupState.DEGRADED, "an unresolved conflict is a warning, not success"
    assert any("daemon-service is still conflicting" in w for w in report.warnings)


def test_the_consent_warning_does_not_promise_a_replacement_it_will_not_make(
    tmp_path: Path,
) -> None:
    """The regression a text has no other way of being caught by.

    "needs your approval before anything is replaced" was the wording here, and
    it described a behaviour that does not exist: nothing is replaced with
    approval either. The same sentence had propagated to the ``--approve-conflicts``
    help, to ``SetupRequest``'s comment and to two module docstrings before
    anyone measured it -- prose spreads by being copied, and no test in this
    suite reads prose.

    Asserted as a prohibition plus a positive, because either alone is weak. The
    prohibition allows any correct wording rather than pinning one phrasing a
    rewrite would have to update; the positive stops it being satisfied by an
    empty or uninformative warning, and names the flag the user has to type,
    which is the one part of this string that is not editorial. No assertion
    pins a *particular* correct sentence: that would make an editorial
    improvement fail a test, which is how a test gets relaxed rather than
    obeyed.
    """
    context = _with(tmp_path, service=FakeService(installed=True, difference="ExecStart differs"))

    report = _service(context).run()

    assert report.state is SetupState.AWAITING_CONSENT
    warning = next(w for w in report.warnings if "daemon-service" in w)
    assert "--approve-conflicts" in warning, "the warning has to name the way forward"
    assert "before anything is replaced" not in warning, (
        "consent releases the run, not the step; nothing is replaced with approval either"
    )


def test_a_dry_run_still_reports_a_conflict_without_asking(tmp_path: Path) -> None:
    """`--dry-run` is how the plugin shows the plan, so it has to surface the
    conflict the user is about to be asked about."""
    context = _with(tmp_path, service=FakeService(installed=True, difference="differs"))

    report = _service(context).run(SetupRequest(dry_run=True))

    assert report.state is SetupState.PLAN_BUILT
    step = report.step(StepId.DAEMON_SERVICE)
    assert step is not None and step.needs_consent


# -- Inside a repository (§6.2 row 11) ---------------------------------------
#
# Every other test in this module leaves `project_root` at `None`, which is the
# state the CLI produces outside a Git working tree -- so `probe_project_
# registered` reported NOT_APPLICABLE for all of them and the entire in-a-
# repository branch had never executed. Not the CONFLICTING arm that an
# unreadable registry entry reaches, and not SATISFIED or MISSING either.
#
# A plain directory rather than a real Git repository: `project_root` is
# whatever `find_git_root` handed the context, and this probe re-checks nothing
# about it. Initialising a repository here would test `git init`.


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "api"
    root.mkdir(exist_ok=True)
    return root


def _registry_holding(context: SetupContext, entries: dict[str, Any]) -> Path:
    """Put ``entries`` in the registry file this context's probes will read.

    Addressed through `ProjectRegistry.default` rather than by filename, so the
    test cannot come to disagree with the probe about where the registry lives.
    """
    return _registry_bytes(context, json.dumps(entries).encode("utf-8"))


def _registry_bytes(context: SetupContext, raw: bytes) -> Path:
    """The same file, written as bytes.

    Separate from :func:`_registry_holding` because that one goes through
    ``json.dumps`` and therefore *cannot* produce the shape the whole-file
    branch needs: a file that does not parse at all. Every existing registry
    fixture in this module has that limitation, which is why the branch had no
    test.
    """
    path = ProjectRegistry.default(context.data_dir).path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def test_a_repository_that_is_registered_is_reported_satisfied(tmp_path: Path) -> None:
    """The convergent case, and the one that makes the others mean something.

    A probe that reported MISSING unconditionally would satisfy the test below
    and every idempotence assertion in this module, because nothing else in it
    is ever inside a repository.
    """
    root = _repository(tmp_path)
    context = _with(tmp_path, project_root=root)
    _registry_holding(context, {"api": {"rootPath": str(root.resolve())}})

    step = probe_project_registered(context)

    assert step.status is StepStatus.SATISFIED
    assert not step.would_change, "a registered repository must not be re-registered"


def test_an_unregistered_repository_is_reported_missing_with_the_command_to_fix_it(
    tmp_path: Path,
) -> None:
    """A MISSING step must say what it would do; the plugin renders `action`.

    ``paths`` stays empty here for the same reason the CONFLICTING arm below
    leaves it empty, which is the reason this arm used to disagree with: this
    step never writes to the registry, whatever the user decides. `register` is
    what writes it, and the action says so.
    """
    root = _repository(tmp_path)
    context = _with(tmp_path, project_root=root)
    _registry_holding(context, {"other": {"rootPath": str(tmp_path / "elsewhere")}})

    step = probe_project_registered(context)

    assert step.status is StepStatus.MISSING
    assert "theurian project register" in step.action
    assert step.paths == (), "a step that only reads must not appear in the changed-files list"


def test_an_unreadable_entry_makes_registration_undecidable_rather_than_missing(
    tmp_path: Path,
) -> None:
    """SEC-13. The arm this milestone added, and the one a `load()` scan gets wrong.

    An unreadable entry is exactly one that names no root path, so nothing in
    the file says it is not *this* repository's own registration. Reporting
    MISSING would pair that guess with a remedy that cannot work: `register`
    refuses while the broken entry holds the id, and this step is the first
    screen a person reads when something is wrong.

    ``paths`` stays empty, unlike a MISSING step's. This step never writes to
    the registry whatever the user decides, and listing the file would claim
    setup "would touch" something it only ever reads.
    """
    root = _repository(tmp_path)
    context = _with(tmp_path, project_root=root)
    _registry_holding(context, {"payments": {"defaultBranch": "main"}})

    step = probe_project_registered(context)

    assert step.status is StepStatus.CONFLICTING
    assert "theurian project unregister payments" in step.detail
    assert step.paths == (), "a step that only reads must not appear in the changed-files list"


@pytest.mark.parametrize(
    ("corruption", "raw"),
    [
        ("truncated JSON", b'{"api": {"rootPath"'),
        ("a JSON array", b"[]"),
        ("arbitrary bytes", b"\xff\xfe\x00\x01theurian"),
    ],
    ids=["truncated-json", "json-array", "arbitrary-bytes"],
)
def test_a_registry_that_does_not_parse_is_not_reported_as_a_bad_entry(
    tmp_path: Path, corruption: str, raw: bytes
) -> None:
    """The other refusal that reaches this probe, and the one that had no test.

    ``ids_for_root`` raises for two different reasons and only one of them is
    about an entry. A file whose top level does not parse has no entries to
    speak of, so a summary saying it "holds an entry that cannot be read" sends
    the reader looking for an offending line in a file that has none -- and
    disagrees in kind with the ``detail`` beside it, which carries the
    delete-and-re-register cure for the whole file.

    All three shapes, because they arrive by different routes:
    ``JSONDecodeError`` for truncated text, a type check for a JSON array, and
    ``UnicodeDecodeError`` -- a ``ValueError`` that is not a ``JSONDecodeError``
    -- for arbitrary bytes. A handler catching only the first two leaves the
    third escaping as a traceback.
    """
    root = _repository(tmp_path)
    context = _with(tmp_path, project_root=root)
    _registry_bytes(context, raw)

    step = probe_project_registered(context)

    assert step.status is StepStatus.CONFLICTING, f"{corruption} must be reported, not raised"
    assert "entry" not in step.summary, (
        "nothing entry-level is knowable when the file itself will not parse"
    )
    assert "re-register each project with `theurian project register`" in step.detail, (
        "the file-level failure has one reliable cure and the detail is where it travels"
    )
    assert step.paths == (), "a step that only reads must not appear in the changed-files list"


def test_an_undecidable_registration_halts_the_run_before_anything_is_created(
    tmp_path: Path,
) -> None:
    """One corrupted registry entry stops a machine-wide install, on purpose (SEC-18).

    A conflict is a step Theurian will not change without being told to
    proceed -- and will still not change afterwards: ``_apply`` only ever runs a
    step whose plan said ``would_change``, so a CONFLICTING one is recorded
    ``UNCHANGED`` whatever the user answers. What consent releases is the *run*,
    not the step. Until it is given the run does not start, so a broken entry
    belonging to some *other* project blocks the token, the service registration
    and the MCP entry as well. That is a wide consequence for a narrow cause, and
    it is a decision rather than an accident: the alternative is a setup that
    runs to completion while the repository it was run in may or may not be
    registered, reported as though it were.

    Asserted on the artifacts rather than on the state alone: a run that reached
    AWAITING_CONSENT after generating a token would satisfy the state and still
    have written a secret nobody approved.
    """
    root = _repository(tmp_path)
    context = _with(tmp_path, project_root=root)
    _registry_holding(context, {"payments": {"defaultBranch": "main"}})

    report = _service(context).run(SetupRequest(approve_conflicts=False))

    conflicted = report.step(StepId.PROJECT_REGISTERED)
    assert conflicted is not None and conflicted.status is StepStatus.CONFLICTING
    assert report.state is SetupState.AWAITING_CONSENT
    assert not (context.auth_dir / TOKEN_KEY).exists(), "no token may be generated"
    assert not context.env_file.exists()
    assert isinstance(context.service, FakeService) and not context.service.installed
    assert context.mcp_config.installed_entry() is None


# -- Steps setup reports but does not perform (§6.2 rows 11-13) --------------
#
# Three steps probe something setup never acts on: whether the repository is
# registered, whether `.theurian/` has its directories, and whether `.gitignore`
# covers the derived artifacts. `theurian project register` does the first and
# `theurian init` the other two; setup only says they are undone, and each one's
# `action` names the command.
#
# Reaching any of that needs `project_root` set, which the module's `context`
# fixture leaves at None -- so every test above them ran with all three reporting
# NOT_APPLICABLE, and the idempotence assertion at the top of this file was green
# against a machine where the branch had never executed. The blind fixture stays:
# outside a Git working tree is a real way to run setup, and the machine-wide
# steps behave the same either way.


@pytest.fixture
def in_a_repository(tmp_path: Path) -> SetupContext:
    """Nothing set up yet, and setup invoked from inside a repository."""
    return _with(tmp_path, project_root=_repository(tmp_path))


#: §6.2 rows 11-13. Written out rather than derived from ``STEPS``, so that the
#: tests below cannot quietly come to assert nothing; the population itself is
#: checked against ``STEPS`` by the first test.
REPORT_ONLY = (StepId.PROJECT_REGISTERED, StepId.PROJECT_LAYOUT, StepId.GITIGNORE)


def _paths_setup_never_writes(context: SetupContext) -> set[str]:
    """The five files the three report-only steps used to claim.

    Spelled out rather than read off the plan. Reading the plan would make every
    assertion below vacuous the moment the plan stopped naming them, which is
    precisely the change these tests exist to hold in place.
    """
    root = context.project_root
    assert root is not None
    return {
        str(ProjectRegistry.default(context.data_dir).path),
        str(root / ".gitignore"),
        *(str(root / ".theurian" / name) for name in ("migrations", "knowledge", "state")),
    }


def test_the_steps_that_report_without_acting_are_the_three_expected_ones(
    in_a_repository: SetupContext,
) -> None:
    """A fourth one would be tested by nothing below until it is added here.

    Measured on a cold machine rather than asserted statically: several steps
    carry no action -- platform, single-instance, migrations-valid -- and what
    distinguishes these three is that they are also the ones that report MISSING,
    which is the only status that used to reach the apply branch.
    """
    plan = _service(in_a_repository).plan()
    actionless = {step.step_id for step in STEPS if step.apply is None}

    reported = {s.step_id for s in plan.steps if s.would_change and s.step_id in actionless}

    assert reported == set(REPORT_ONLY)


def test_a_step_setup_does_not_perform_is_never_reported_as_changed(
    in_a_repository: SetupContext,
) -> None:
    """The defect this section was written for.

    `would_change` is MISSING and nothing else, so a step with no action reached
    the apply branch, called nothing, and was recorded CHANGED unconditionally.

    The outcome is asserted by *value*, not as "not CHANGED". `outcome` is a
    published field and the plugin's `setup.md` renders it, so which of the four
    it is matters: NOT_ATTEMPTED means the run stopped before reaching the step,
    which a completed run must never say about a step it probed twice. A
    prohibition alone let that substitution through.
    """
    report = _service(in_a_repository).run()

    for step_id in REPORT_ONLY:
        step = report.step(step_id)
        assert step is not None
        assert step.status is StepStatus.MISSING, "the fixture has to reach the branch"
        assert step.outcome is StepOutcome.UNCHANGED, (
            f"{step_id.value} has no action; the run reached it and it did not change"
        )


def test_a_file_setup_never_writes_is_not_listed_among_the_files_it_changed(
    in_a_repository: SetupContext,
) -> None:
    """All five were absent from the disk when the run ended.

    Asserted on absence from the list *and* on absence from the disk, because
    either alone is weak: a run that silently dropped a genuinely written path
    would satisfy the first, and a run that stopped reporting nothing at all
    while some probe quietly created the files would satisfy the second.
    """
    report = _service(in_a_repository).run()

    phantom = _paths_setup_never_writes(in_a_repository)
    assert not phantom & set(report.changed_paths), "setup did not write these"
    assert not any(Path(p).exists() for p in phantom), "and nothing else wrote them either"


def test_the_plan_offers_none_of_the_five_paths_rows_11_to_13_used_to_name(
    in_a_repository: SetupContext,
) -> None:
    """The plan is what `--dry-run` shows before consent is asked for.

    Same claim as the changed-files list, one stage earlier: a path offered here
    is one the user is told setup would create or modify.

    Named for the five, because five is what it checks. It is the concrete
    regression and nothing wider -- the property that *no* actionless step names
    a path in *any* state is two tests below, and this one went on holding a
    universal in its title while intersecting against a hardcoded list.
    """
    plan = _service(in_a_repository).plan()

    published = {path for step in plan.steps for path in step.paths}
    assert not _paths_setup_never_writes(in_a_repository) & published
    assert not _paths_setup_never_writes(in_a_repository) & set(plan.paths)


def test_the_summaries_carry_the_locations_that_removing_paths_took_out(
    in_a_repository: SetupContext,
) -> None:
    """The compensation, which was the untested half of removing `paths`.

    Dropping `paths` from these three is only defensible because nothing a
    reader needs goes with it, and for two of them that was made true by moving
    the location into the summary rather than by its already being there. Both
    the CHANGELOG and the probes' own comments assert it. Until this test,
    shortening any of the three summaries to a sentence naming no location
    passed the whole suite -- measured, as three surviving mutations.

    `project-layout` needs no help and gets asserted anyway, because "its
    summary already names them" is the reason it was left alone.
    """
    root = in_a_repository.project_root
    assert root is not None
    expected = {
        StepId.PROJECT_REGISTERED: str(ProjectRegistry.default(in_a_repository.data_dir).path),
        StepId.PROJECT_LAYOUT: str(root / ".theurian"),
        StepId.GITIGNORE: str(root / ".gitignore"),
    }

    report = _service(in_a_repository).run()

    for step_id, location in expected.items():
        step = report.step(step_id)
        assert step is not None
        assert step.status is StepStatus.MISSING, "the fixture has to reach the branch"
        assert location in step.summary, (
            f"{step_id.value} no longer says where; `paths` was the only other place it lived"
        )
    registered = report.step(StepId.PROJECT_REGISTERED)
    assert registered is not None
    assert str(root) in registered.summary, "and which repository has no entry"


# -- The property those five paths are one instance of -----------------------
#
# `SetupStep.paths` claims that a step with no action names none *whatever it
# found*, and that claim is about arms. Arms are where it broke:
# `probe_project_registered` left `paths` empty on its CONFLICTING arm, with a
# comment saying exactly why, and set it on the MISSING arm three lines below.
# A test that intersects one state against five hardcoded paths cannot see that,
# and two mutations proved it -- `probe_gitignore` naming a file again, and
# `probe_migrations` naming the directory it only reads -- both surviving the
# whole suite while restoring the published defect.

#: Every ``(step, status)`` pair the eleven actionless steps reach across
#: `_states`, counted off their branches: platform 2, core-present 2 (three
#: returns, two of them CONFLICTING), artifact-integrity 1, single-instance 3,
#: project-registered 4, project-layout 3, gitignore 3, mcp-health 2,
#: migrations-valid 2 (three returns, two of them NOT_APPLICABLE), initial-index
#: 1 (two summaries, one status), serena-detection 2. Independently: 28
#: ``return SetupStep(...)`` statements collapsing onto 25 pairs.
#:
#: `core-present`'s third return -- Core installed without its ``daemon`` extra
#: (#78) -- is not reachable from any state here, because the test process is a
#: development environment that always has the extra. It is walked by
#: ``tests/integration/test_bare_install.py``, which blocks the modules instead
#: of varying the context. The count above is what the arm costs this number:
#: nothing, since the other CONFLICTING arm already supplies the pair.
#:
#: **What the assertion holds, exactly.** A fall means a state stopped reaching
#: a status it used to. A rise means a step began emitting a status it did not
#: emit before. It does *not* enumerate arms and cannot: an arm no state walks
#: produces no observation, so deleting one is invisible here whenever another
#: arm of the same step already emits that status. Measured -- deleting
#: `probe_migrations`'s "No migrations directory yet." NOT_APPLICABLE arm
#: survives this, because the `root is None` arm keeps emitting
#: NOT_APPLICABLE. What catches a probe naming a path is the `paths` assertion
#: in the loop, not this number.
ACTIONLESS_STEP_STATUS_PAIRS = 25

#: Any absolute path. What matters is only that a probe named one.
_A_NAMED_PATH = "/tmp/a-file-this-step-only-reads"  # noqa: S108 - never opened


def _under(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _converged_repository(base: Path) -> SetupContext:
    """Rows 11-13 satisfied, Serena present, a migration and a built state on disk.

    The built state is the point of the ``active.json``: `probe_initial_index`
    branches on `read_active_state`, and with no state ever built no context
    reached the built side of it. A mutation naming a path only there survived
    the whole suite.
    """
    root = _repository(base)
    for name in ("migrations", "knowledge", "state"):
        (root / ".theurian" / name).mkdir(parents=True, exist_ok=True)
    (root / ".theurian" / "migrations" / "0001-initial.yaml").touch()
    (root / ".theurian" / "state" / "active.json").write_text(
        json.dumps(
            {
                "stateHash": "b" * 64,
                "databaseFilename": "knowledge-bbbb.sqlite",
                "migrationCount": 1,
                "updatedAt": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".theurian/state/\n", encoding="utf-8")
    mcp_config = FakeMcpConfig()
    mcp_config.serena = True
    context = _with(base, project_root=root, mcp_config=mcp_config)
    _registry_holding(context, {"api": {"rootPath": str(root.resolve())}})
    return context


def _conflicted_repository(base: Path) -> SetupContext:
    """Three conflicts at once: no executable, a foreign daemon, a broken entry.

    Its `.theurian/` is *partly* built -- `migrations` alone -- because
    `probe_project_layout` computes which directories are absent and no other
    state leaves that list a proper subset. A mutation naming only the missing
    ones survived while every state had either all three or none.
    """
    root = _repository(base)
    (root / ".theurian" / "migrations").mkdir(parents=True, exist_ok=True)
    context = _with(
        base,
        project_root=root,
        executable="",
        health=lambda: {"dataDir": "/somewhere/that/is/not/this/one"},
    )
    _registry_holding(context, {"payments": {"defaultBranch": "main"}})
    return context


def _initialised_but_empty_repository(base: Path) -> SetupContext:
    """`theurian init` has run and nothing has been written yet.

    The state right after `init`, and the one `_converged_repository` cannot
    stand in for: seeding a migration there means `probe_migrations` never
    reaches its own directory-exists-but-is-empty case. A mutation that made
    that case report MISSING with the directory in `paths` survived the whole
    suite, because no state walked it.

    Its `.gitignore` exists and says nothing about Theurian, which is the other
    half of `probe_gitignore`'s MISSING status: everywhere else the file is
    simply absent, so a mutation naming it only when it exists survived too.
    """
    root = _repository(base)
    for name in ("migrations", "knowledge", "state"):
        (root / ".theurian" / name).mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("*.log\nnode_modules/\n", encoding="utf-8")
    return _with(base, project_root=root)


def _states(tmp_path: Path) -> dict[str, SetupContext]:
    """One context per shape a report-only step can be probed in."""
    served = _under(tmp_path, "served")
    served_data_dir = served / "home" / ".theurian"
    return {
        "outside a repository": _with(_under(tmp_path, "outside")),
        "cold inside a repository": _with(
            _under(tmp_path, "cold"), project_root=_repository(_under(tmp_path, "cold"))
        ),
        "initialised but empty": _initialised_but_empty_repository(_under(tmp_path, "initialised")),
        "converged inside a repository": _converged_repository(_under(tmp_path, "converged")),
        "conflicted": _conflicted_repository(_under(tmp_path, "conflicted")),
        "a daemon already serving this data directory": _with(
            served,
            project_root=_repository(served),
            health=lambda: {"dataDir": str(served_data_dir)},
        ),
    }


def test_no_step_without_an_action_names_a_path_in_any_state_it_can_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source half of the guarantee, across every state the probes branch on.

    Stripping in the runner (below) makes a probe that names a path harmless, not
    correct: the next reader of `probe_gitignore` would find `paths=(...)` in the
    source and `"paths": []` in the JSON and have to work out which one lied.
    This keeps the two agreeing.

    The `paths` assertion is what does the work. `ACTIONLESS_STEP_STATUS_PAIRS`
    is a coverage floor and nothing more -- see its comment for what it can and
    cannot notice. What decides whether this test finds anything is the *states*:
    three mutations survived an earlier version of it because no context here
    had a `.gitignore` that existed without the marker, a built knowledge state,
    or a partly-populated `.theurian/`.

    Both platform arms are walked by patching rather than by whichever host is
    running, so Linux CI and a macOS laptop cover the same pairs.
    """
    states = _states(tmp_path)
    observed: set[tuple[StepId, StepStatus]] = set()
    offenders: list[str] = []

    for platform_name in ("darwin", "win32"):
        monkeypatch.setattr(sys, "platform", platform_name)
        for label, context in states.items():
            for step in STEPS:
                if step.apply is not None:
                    continue
                probed = step.probe(context)
                observed.add((probed.step_id, probed.status))
                if probed.paths:
                    offenders.append(f"{probed.step_id.value} names {probed.paths} when {label}")

    assert offenders == []
    assert {status for _, status in observed} == set(StepStatus), "every status has to be walked"
    assert len(observed) == ACTIONLESS_STEP_STATUS_PAIRS


def _probe_naming_a_path(
    step_id: StepId, status: StepStatus
) -> Callable[[SetupContext], SetupStep]:
    """A probe that names a path in whichever status it is asked for."""

    def probe(_: SetupContext) -> SetupStep:
        return SetupStep(
            step_id=step_id,
            status=status,
            summary="found something worth mentioning",
            action="do the thing" if status is StepStatus.MISSING else "",
            detail="differs from what setup installs" if status is StepStatus.CONFLICTING else "",
            paths=(_A_NAMED_PATH,),
        )

    return probe


def _an_action(_: SetupContext) -> None:
    """A stand-in. Never called: the tests below only plan."""


@pytest.mark.parametrize("status", list(StepStatus), ids=[s.value for s in StepStatus])
def test_the_runner_drops_the_paths_a_step_without_an_action_names(
    context: SetupContext, status: StepStatus
) -> None:
    """The guarantee belongs to the runner, not to each probe's every arm.

    A probe is edited one arm at a time and there are 25 of them; there is one
    funnel. §6.2's unimplemented rows will start reporting MISSING one day, with
    no reason to have read any of this -- and `_probe` already takes `critical`
    from the definition rather than the probe for exactly the same reason.
    """
    steps = (
        Step(StepId.MIGRATIONS_VALID, _probe_naming_a_path(StepId.MIGRATIONS_VALID, status), None),
    )

    plan = SetupService(context, steps=steps).plan()

    assert plan.steps[0].paths == ()
    assert plan.paths == ()


@pytest.mark.parametrize("status", list(StepStatus), ids=[s.value for s in StepStatus])
def test_the_runner_keeps_the_paths_a_step_with_an_action_names(
    context: SetupContext, status: StepStatus
) -> None:
    """The other half, without which `paths=()` for everyone would pass.

    `data-directory`, `token` and `env-reference` are where the changed-files
    list gets its contents, and emptying it would satisfy every prohibition in
    this section.
    """
    steps = (
        Step(
            StepId.DATA_DIRECTORY, _probe_naming_a_path(StepId.DATA_DIRECTORY, status), _an_action
        ),
    )

    plan = SetupService(context, steps=steps).plan()

    assert plan.steps[0].paths == (_A_NAMED_PATH,)


def test_a_step_setup_does_not_perform_is_not_journalled_as_applied(
    in_a_repository: SetupContext,
) -> None:
    """§6.4. The journal exists so a crash mid-run can be repaired afterwards.

    An `applied` line for a step that ran nothing sends whoever reads it looking
    for an inverse of an action that was never taken.
    """
    service = _service(in_a_repository)
    service.run()

    journalled = {
        json.loads(line)["step"]
        for line in service.journal_path.read_text().splitlines()
        if line.strip()
    }

    assert not journalled & {step_id.value for step_id in REPORT_ONLY}


def test_a_second_run_inside_a_repository_changes_nothing(
    in_a_repository: SetupContext,
) -> None:
    """FR-L2 again, on the fixture that reaches rows 11-13.

    The assertion at the top of this file is the same one, and it was green
    because all three steps reported NOT_APPLICABLE there. A second run named the
    same five files as the first.
    """
    first = _service(in_a_repository).run()
    assert first.succeeded, first.warnings

    second = _service(in_a_repository).run()

    assert second.succeeded, second.warnings
    assert second.changed_paths == (), "a converged machine must report no changes"
    assert all(step.outcome is not StepOutcome.CHANGED for step in second.steps), (
        "no step may change anything on a second run"
    )


def test_a_dry_run_inside_a_repository_still_creates_nothing(
    in_a_repository: SetupContext,
) -> None:
    """The seeing half of `test_a_dry_run_changes_nothing_at_all`.

    That one cannot see a probe in rows 11-13 that writes, because with
    `project_root` at None none of them gets past its first branch. This one
    checks the data directory *and* the repository, since the registry lives
    under the first and the layout under the second.
    """
    report = _service(in_a_repository).run(SetupRequest(dry_run=True))

    root = in_a_repository.project_root
    assert report.state is SetupState.PLAN_BUILT
    assert report.changed_paths == ()
    assert not in_a_repository.data_dir.exists(), "a probe may not write while planning"
    assert root is not None and not (root / ".theurian").exists()


def test_setup_still_reports_what_it_declined_to_do(in_a_repository: SetupContext) -> None:
    """Not doing the step is the design; not saying so would be a worse defect.

    The point of probing rows 11-13 at all is the sentence a person reads, so the
    fix has to leave the status, the remedy and the warning intact -- a run that
    silently recorded UNCHANGED and said nothing would pass every assertion above
    and help nobody.
    """
    report = _service(in_a_repository).run()

    assert report.state is SetupState.DEGRADED, "an unperformed step is a warning, not silence"
    for step_id in REPORT_ONLY:
        step = report.step(step_id)
        assert step is not None
        assert step.action, "a MISSING step has to say what would fix it"
        assert any(step_id.value in warning for warning in report.warnings)
    registered = report.step(StepId.PROJECT_REGISTERED)
    assert registered is not None and "theurian project register" in registered.action


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


# -- Halting on a critical apply failure (§6.4, #47) -------------------------
#
# ABORTED above stops *before* applying anything. HALTED is the other terminal
# failure: a critical step fails partway through the apply, after earlier steps
# have already written to disk. Nothing is rolled back -- the journal is
# append-only with no inverse action -- so the report has to be honest that the
# credential minted before the failure is still there, rather than naming a
# state that implies it was cleaned up.


def _halt_on_env_reference(tmp_path: Path) -> tuple[SetupContext, SetupReport]:
    """Run setup so a critical apply fails *after* a credential is minted.

    ``DATA_DIRECTORY`` is pre-converged at 0700, so `token` and `token-storage`
    both apply and mint ``auth/mcp-token`` before the run reaches step 7. The env
    file is created as a *directory*, so ``apply_env_reference``'s ``os.open``
    raises ``IsADirectoryError`` -- a real critical failure from a shipped step,
    not an injected fake one. ``env-reference`` is step 7, ahead of
    ``daemon-service`` step 8, so the run halts before any service registration:
    the fixture's fake service is never installed and nothing touches a real
    service manager.
    """
    context = _with(tmp_path)
    context.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    context.data_dir.chmod(0o700)
    context.env_file.mkdir()

    report = _service(context).run(SetupRequest())
    return context, report


def test_a_critical_apply_failure_halts_and_discloses_the_leftover_credential(
    tmp_path: Path,
) -> None:
    """#47. A halted run must not name a rollback the code cannot perform (§6.4).

    When a critical step fails mid-apply, nothing is undone: every apply is a
    create-or-tighten and the journal has no inverse to replay, so the token
    minted by the earlier steps is still on disk. Deleting a token another
    session may already be using would itself be a defect, so the honest report
    is HALTED and the leftover credential is surfaced through ``changed_paths``,
    never hidden behind a state that reads as "cleaned up".

    Asserted on the token file *and* the report, because either alone is weak: a
    report naming a path nothing wrote would satisfy the second, and a token on
    disk the report stays silent about would satisfy the first. The security
    half is the disclosure -- an operator who is not told the credential is there
    cannot rotate or remove it.
    """
    context, report = _halt_on_env_reference(tmp_path)

    token = context.auth_dir / TOKEN_KEY
    assert report.state is SetupState.HALTED
    assert report.succeeded is False, "a halted run is a failure, not success with warnings"
    assert token.is_file(), "the credential minted before the failure is still on disk"
    assert str(token) in report.changed_paths, "and the operator is told it is there"


def test_a_halted_run_lists_the_leftover_credential_exactly_once(
    tmp_path: Path,
) -> None:
    """#47. `token` and `token-storage` both name ``auth/mcp-token``.

    Accumulating each applied step's ``paths`` listed the credential twice in the
    report an operator reads after a failure -- and twice reads like two separate
    leftovers to chase, not one. ``changed_paths`` is de-duplicated
    order-preservingly, so the token path appears exactly once and the list holds
    no duplicate at all.

    The second assertion is the general form of the first: pinning only the token
    path's count would stay green if some other path began doubling, which is the
    regression the ``_unique`` funnel exists to stop for every path, not just this
    one.
    """
    context, report = _halt_on_env_reference(tmp_path)

    token = str(context.auth_dir / TOKEN_KEY)
    assert report.changed_paths.count(token) == 1, "the credential is listed once, not twice"
    assert len(report.changed_paths) == len(set(report.changed_paths)), (
        "no path may appear twice in what an operator reads after a failure"
    )


def test_a_converged_run_lists_each_changed_path_once(context: SetupContext) -> None:
    """#47. The dedup is applied at *both* return points, not just the halt.

    ``_unique`` guards the CONVERGED/DEGRADED report as well as the HALTED one,
    and a cold run walks that success path. `token` and `token-storage` both name
    ``auth/mcp-token``, so without the funnel a fully converged run lists the
    credential twice in ``changed_paths`` -- the same double-listing #47 fixes,
    on the run an operator sees most often. The halted-path test above cannot see
    this: a critical failure never reaches ``_verify``, so its ``_unique`` could
    be removed and stay green there. Measured -- reverting the success-path dedup
    survived the whole setup suite until this test.
    """
    report = _service(context).run()

    assert report.succeeded, report.warnings
    token = str(context.auth_dir / TOKEN_KEY)
    assert report.changed_paths.count(token) == 1, "the credential is listed once, not twice"
    assert len(report.changed_paths) == len(set(report.changed_paths)), (
        "a converged report may not list any path twice"
    )


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
