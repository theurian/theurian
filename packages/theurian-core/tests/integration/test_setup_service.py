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
from typing import Any, NamedTuple, override

import pytest
from fakes.clock import FrozenClock
from fakes.setup import FakeMcpConfig, FakeService
from migration_fixtures import body_pin
from setup_migrations import state_hash_from_the_loader, unchecked_migrations

from theurian.application.project_service import (
    ProjectPaths,
    ProjectRegistry,
    ensure_gitignore,
    read_active_index_pointer,
    resolve_state_hash,
    write_active_state,
)
from theurian.application.setup_context import MigrationsCheck, SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import (
    STEPS,
    Step,
    probe_initial_index,
    probe_project_registered,
)
from theurian.cli.commands import _emit
from theurian.cli.context import schema_root
from theurian.domain.errors import MigrationError
from theurian.domain.setup import (
    SetupReport,
    SetupState,
    SetupStep,
    StepId,
    StepOutcome,
    StepStatus,
)
from theurian.domain.state import StateHash
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION
from theurian.security.env_file import TOKEN_KEY
from theurian.security.tokens import MIN_TOKEN_LENGTH

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


def test_a_healthy_machine_ends_the_run_converged(context: SetupContext) -> None:
    """§6.1. CONVERGED is the state every other state in this module departs from.

    Nothing asserted it. `SetupReport.succeeded` is true of DEGRADED as well --
    it is success with warnings -- so every `assert report.succeeded` above and
    below stays green on a run that ended DEGRADED with a page of warnings, and
    so does every assertion about the files on disk. Measured: replacing
    `SetupService._verify`'s ``SetupState.CONVERGED if not warnings else
    DEGRADED`` with an unconditional ``DEGRADED`` passed all 2426 tests. The one
    state a person is trying to reach was the one state nothing pinned.

    Asserted by *value* rather than as "not DEGRADED": the five states are what
    the plugin branches on, and a run that reached PLAN_BUILT without applying
    anything is also not DEGRADED.
    """
    report = _service(context).run()

    assert report.state is SetupState.CONVERGED, report.warnings


def test_a_step_that_is_not_applicable_and_says_why_is_not_a_warning(
    context: SetupContext,
) -> None:
    """The other half of `SetupService._reservations`, which no test held.

    A reservation is a step that is SATISFIED *and* carries a ``detail`` -- the
    env file's shadowed-block arm, pinned in `test_setup_env_file.py`. The status
    half of that condition is what this holds: every machine, converged or not,
    carries NOT_APPLICABLE steps with a detail explaining the gap, and
    ``artifact-integrity`` carries one on every platform in every state (T-16).
    Dropping the status test -- ``if step.detail`` alone -- therefore turns every
    healthy machine DEGRADED, with the supply-chain note published as though it
    were something wrong with this install. Measured: that mutation passed all
    2426 tests, because nothing asserted the warnings of a converged run.

    The fixture guard is the first two assertions rather than a comment: with no
    NOT_APPLICABLE-with-detail step in the report, ``warnings == ()`` says
    nothing about the branch.
    """
    report = _service(context).run()

    explained = [s for s in report.steps if s.status is StepStatus.NOT_APPLICABLE and s.detail]
    assert explained, "the fixture has to reach the branch"
    assert StepId.ARTIFACT_INTEGRITY in {s.step_id for s in explained}
    assert report.warnings == (), "a gap that is explained is not a finding about this machine"


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

#: Every ``(step, status)`` pair the twelve actionless steps reach across
#: `_states`, counted off their branches: platform 2, core-present 2 (three
#: returns, two of them CONFLICTING), artifact-integrity 1, serving-profile 1
#: (three returns, one reachable here), single-instance 3, project-registered 4,
#: project-layout 3, gitignore 3, mcp-health 2, migrations-valid 3 (four arms,
#: two of them NOT_APPLICABLE), initial-index 1 (three of its four summaries
#: reached here, all one status), serena-detection 2. Those twelve add to 27.
#:
#: This comment used to carry a second, independent count -- "31 ``return
#: SetupStep(...)`` statements collapsing onto 26 pairs". It is gone rather than
#: updated: #87 and #91 rewrote ``probe_gitignore`` and ``probe_migrations`` into
#: a different number of arms, and a hand-counted source total that the next
#: rewrite will falsify again is prose asserting a measurement nobody re-takes.
#: The per-step tally above is the derivation, and the assertion below prints
#: the observed set when it disagrees.
#:
#: Three steps have returns no state here can walk, and all three are walked
#: elsewhere. `core-present`'s third -- Core installed without its ``daemon``
#: extra (#78) -- is unreachable because the test process is a development
#: environment that always has the extra;
#: ``tests/integration/test_bare_install.py`` blocks the modules instead of
#: varying the context. `serving-profile`'s SATISFIED and CONFLICTING arms need
#: a profile file in the data directory, which no context here writes;
#: ``tests/integration/test_setup_cli.py`` declares one and asserts all three
#: statuses through `doctor`. `initial-index`'s built arm needs a state database
#: at the hash the migrations on disk resolve to, which means a migration set
#: the real loader accepts; this module's #451 section builds one and asserts
#: both that arm and the one beside it. The count above is what those arms cost
#: this number: nothing for `core-present`, whose other CONFLICTING arm already
#: supplies the pair, nothing for `initial-index`, whose other arms supply its
#: only status, and two pairs for `serving-profile` that simply never appear
#: here.
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
ACTIONLESS_STEP_STATUS_PAIRS = 27

#: Any absolute path. What matters is only that a probe named one.
_A_NAMED_PATH = "/tmp/a-file-this-step-only-reads"  # noqa: S108 - never opened


def _under(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _converged_repository(base: Path) -> SetupContext:
    """Rows 11-13 satisfied, Serena present, and a migration file on disk.

    Its ``0001-initial.yaml`` is empty, which the real loader refuses -- an empty
    document is not a mapping at the root -- so this is the state that walks
    `probe_initial_index`'s cannot-tell arm. That arm is the one with a raise
    behind it: `_with` hands every context here the real state-hash resolver, and
    the loop below calls each probe *directly*, with no `SetupService._probe` net
    under it, so a resolver or a probe that let the refusal escape ends this test
    in an error rather than a comparison.

    It used to carry a hand-written ``active.json`` instead, for a question
    `probe_initial_index` no longer asks: whether a pointer exists at all was the
    #451 defect, and nothing in ``STEPS`` reads that file now. The built and
    not-built arms are held where they can be stated against a real state hash,
    in this module's #451 section.
    """
    root = _repository(base)
    for name in ("migrations", "knowledge", "state"):
        (root / ".theurian" / name).mkdir(parents=True, exist_ok=True)
    (root / ".theurian" / "migrations" / "0001-initial.yaml").touch()
    # Written by the function `theurian init` calls, rather than by joining the
    # entries: since #87 `probe_gitignore` reports `satisfied` only for the
    # managed block itself, and the bare entries with no markers around them are
    # a hand-written list it deliberately does not credit. Composing them here
    # would leave this state reporting MISSING and quietly drop
    # `gitignore: satisfied` out of the pair count below.
    ensure_gitignore(root)
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


def _migrations_that_do_not_validate(base: Path) -> SetupContext:
    """A repository whose migration set the loader refuses (#91).

    The only state here that walks `probe_migrations`' MISSING arm. Every other
    one either has no migrations directory or a checker reporting nothing wrong,
    and an arm no state walks is invisible to the count below -- which is how a
    mutation naming ``paths=(str(paths.migrations),)`` on it would survive, the
    same shape as the `probe_migrations` mutation this section's comment already
    records.

    The refusal is injected rather than produced by writing a broken YAML file,
    because what this state needs is the *arm*, and the loader is wired in
    ``tests/integration/test_probe_migrations_validate.py`` where the wiring
    itself is the subject.
    """
    root = _repository(base)
    (root / ".theurian" / "migrations").mkdir(parents=True, exist_ok=True)
    refusal = MigrationError("0001-broken.yaml: mapping values are not allowed here")
    return _with(
        base,
        project_root=root,
        check_migrations=lambda _: MigrationsCheck(count=0, failure=refusal),
    )


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
        "migrations that do not validate": _migrations_that_do_not_validate(
            _under(tmp_path, "unvalidated")
        ),
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


# -- initial-index answers about the *current* state hash (#451) -------------
#
# `probe_initial_index` asked whether an active-state pointer exists at all,
# while `theurian project status` publishes ``"stateBuilt": database.exists()``
# for the ``database_for(context.state_hash)`` it resolved a few lines earlier
# -- whether the database for the migration set *on disk right now* is there
# (``project_status`` in ``cli/commands.py``). Those two agreed until a
# migration landed, and then they parted for good: the pointer a first `migrate
# apply` writes is never removed, so from that moment `doctor` answered
# "Knowledge state is built." for every later state, and the arm naming the
# remedy was unreachable.
#
# That is the state a pull puts a deployment into -- migrations fetched,
# `migrate apply` not yet run -- so the step published its false claim exactly
# when a person was asking `doctor` what to do. #451 records the pair, measured
# on f2279e1: one migration added to an applied project, `project status --json`
# reporting ``stateBuilt: false`` and ``migrationCount: 1`` on the same tree
# whose `doctor --json` reported initial-index "Knowledge state is built.".
#
# Observable family: **a published field**. A `doctor` summary is a claim an
# operator acts on, and the sentence *is* the whole output of a step that has no
# action -- there is nothing else in it that could carry the answer.

#: The claims `probe_initial_index` chooses between. Quoted from
#: ``setup_steps.py`` rather than paraphrased, because #451 is entirely about
#: *which* of them a given project is handed: an assertion naming only one of
#: them would be satisfied by a summary that says two, or none.
_STATE_IS_BUILT = "Knowledge state is built."
_APPLY_REMEDY = "Run `theurian migrate apply`."

#: The third one, pinned **verbatim** where the two above are not. It is the
#: answer for a set the resolver could not read, and it is the arm with the
#: least holding it down: a state whose migrations stopped loading still has a
#: database and a pointer on disk, so rewriting this sentence back to "Knowledge
#: state is built." is #451's own lie in the one place the fixtures cannot
#: contradict by arithmetic. Wording that must be typed out is wording a rewrite
#: has to come here to change.
_CANNOT_TELL = (
    "Cannot tell what state this project is at: its migration set could not be "
    "read. Run `theurian migrate validate`, which prints why."
)

#: The clauses of the published ``detail`` that #451's second half is about.
#: `theurian index build` shipped in Milestone 5, so a detail promising
#: retrieval indexes as future work and telling the reader there is nothing to
#: build is false twice over on every project that has one to build.
#:
#: The token "Milestone 5" is deliberately *not* forbidden here, and neither is
#: any replacement wording pinned: a detail saying the indexes *shipped* in
#: Milestone 5 would be true, and a test that refused it would be holding the
#: wording rather than the claim -- which is how a fossil survives its rewrite.
_CLAIMS_INDEXES_ARE_FUTURE_WORK = ("Retrieval indexes arrive in", "there is nothing to build yet")

#: What the ``detail`` has to *say*, as opposed to what it must not. Forbidding
#: phrases alone is satisfied by a field carrying nothing at all: ``detail=""``
#: was caught by nothing in this file (measured) and by nothing in the suite
#: when round one measured it, and an empty field is how the reader stops being
#: told that the canonical state this step reports and the retrieval index it is
#: named for are two artefacts. Both commands are registered in
#: ``cli/index_commands.py`` -- ``build``, ``gc``, ``status``, measured off
#: ``index_app`` -- so the field sends its reader somewhere that exists.
_DETAIL_NAMES_THE_INDEX_COMMANDS = ("`theurian index build`", "`theurian index status`")

#: Crockford base32, as every ULID in this repository is: no ``I``, ``L``, ``O``
#: or ``U``, and a first character in ``0-7``. The migration schema's own pattern
#: is ``^[0-7][0-9A-HJKMNP-TV-Z]{25}$``, and a fixture that fails it is refused
#: by the loader rather than by the behaviour under test.
_FIRST_MIGRATION = "01K451AAAA01234567890ABCDE"
_FIRST_REVISION = "01K451AAAREV01234567890ABC"
_SECOND_MIGRATION = "01K451BBBB01234567890ABCDE"
_SECOND_REVISION = "01K451BBBREV01234567890ABC"


def _loadable_migration(migration_id: str, revision_id: str, slug: str, body: str) -> str:
    """A migration the real loader accepts, over its own item.

    Real rather than a stub file, because the state hash these fixtures pivot on
    is computed from the *loaded* set: `_converged_repository`'s empty
    ``0001-initial.yaml`` is refused by the loader, so it can stand in for a
    migration only where nothing asks what the set hashes to.
    """
    return f"""apiVersion: theurian.dev/v1
id: {migration_id}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: {revision_id}
    contentFile: ../knowledge/architecture/{slug}.md
    contentSha256: {body_pin(body)}
    metadata:
      title: {slug}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""


def _write_a_migration(root: Path, migration_id: str, revision_id: str, slug: str) -> None:
    """Author one migration and the body it pins, the way a person would."""
    body = f"# {slug}\n\nEvery call carries a signed token.\n"
    paths = ProjectPaths.of(root)
    (paths.knowledge / "architecture").mkdir(parents=True, exist_ok=True)
    (paths.knowledge / "architecture" / f"{slug}.md").write_text(body, encoding="utf-8")
    paths.migrations.mkdir(parents=True, exist_ok=True)
    (paths.migrations / f"{migration_id}-{slug}.yaml").write_text(
        _loadable_migration(migration_id, revision_id, slug, body), encoding="utf-8"
    )


def _current_state(root: Path) -> tuple[StateHash, int]:
    """What ``theurian project status`` would publish as ``stateHash`` here.

    The same two calls `cli/context.resolve_context` makes -- `load_migrations`
    then `resolve_state_hash(loaded, SCHEMA_VERSION)` -- so these fixtures
    cannot come to disagree with the product about which database is the current
    one. Deriving the hash any other way would let the test and `project status`
    address different files while both looked right.
    """
    paths = ProjectPaths.of(root)
    loaded = load_migrations(paths.root, paths.migrations, schema_root())
    return resolve_state_hash(loaded, SCHEMA_VERSION), len(loaded.migration_set)


def _apply_the_current_set(root: Path) -> StateHash:
    """Leave behind what a successful `theurian migrate apply` leaves behind.

    The database is created empty on purpose: "built" is `database.exists()` --
    that is the predicate `project status` publishes as ``stateBuilt`` -- so a
    file at the right path is the whole of the state being fixtured. The pointer
    goes through the product's own `write_active_state`, so a fixture cannot
    quietly write a shape the reader would refuse.
    """
    paths = ProjectPaths.of(root)
    state_hash, count = _current_state(root)
    database = paths.database_for(state_hash)
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()
    write_active_state(paths, state_hash, count, FrozenClock())
    return state_hash


def _never_applied(base: Path) -> SetupContext:
    """Migrations authored, `migrate apply` never run. No pointer, no database."""
    root = _repository(base)
    _write_a_migration(root, _FIRST_MIGRATION, _FIRST_REVISION, "auth-policy")
    return _with(base, project_root=root)


def _applied_and_current(base: Path) -> SetupContext:
    """Applied, and nothing has changed since. The one state that *is* built."""
    root = _repository(base)
    _write_a_migration(root, _FIRST_MIGRATION, _FIRST_REVISION, "auth-policy")
    _apply_the_current_set(root)
    return _with(base, project_root=root)


def _migrations_pulled_but_not_applied(base: Path) -> SetupContext:
    """Applied once, then a migration landed: #451's state, and the common one.

    Every corpus change puts a deployment here between `git pull` and `theurian
    migrate apply`. The pointer and the earlier database both survive on disk --
    they are what made the pointer-existence question answer "built" -- while the
    migration set has moved and no database exists for what it now hashes to.
    """
    root = _repository(base)
    _write_a_migration(root, _FIRST_MIGRATION, _FIRST_REVISION, "auth-policy")
    _apply_the_current_set(root)
    _write_a_migration(root, _SECOND_MIGRATION, _SECOND_REVISION, "rate-limit")
    return _with(base, project_root=root)


def _the_pointer_deleted_but_the_database_kept(base: Path) -> SetupContext:
    """Applied, then the derived pointer went missing. The old predicate's own face.

    ``.theurian/state/`` is git-ignored, derived and routinely wiped; the
    database for the current hash surviving without its pointer is what a
    half-cleaned state directory looks like. The old question -- does a pointer
    exist -- answered "not built" here while `project status` answered
    ``stateBuilt: true``, so this is #451 pointing the other way, and it is the
    shape that proves the new predicate reads the *database* rather than having
    swapped one proxy for another.
    """
    root = _repository(base)
    _write_a_migration(root, _FIRST_MIGRATION, _FIRST_REVISION, "auth-policy")
    _apply_the_current_set(root)
    ProjectPaths.of(root).active_pointer.unlink()
    return _with(base, project_root=root)


def _an_applied_project_whose_migrations_stopped_loading(base: Path) -> SetupContext:
    """Applied, then the set stopped loading. There is no current state to name.

    Applied *first*, deliberately: this leaves a database and an active pointer
    on disk, so every proxy for built-ness that #451 was about is present and
    saying yes while the one question that matters -- which state is this
    project at -- has no answer at all. A step that reached for either would say
    "Knowledge state is built." here, about a project whose migrations nothing
    can read.

    The break is a second, empty ``*.yaml``: an empty document is not a mapping,
    and the loader refuses the whole set on it, which is the same shape
    `_converged_repository` reaches by accident.
    """
    root = _repository(base)
    _write_a_migration(root, _FIRST_MIGRATION, _FIRST_REVISION, "auth-policy")
    _apply_the_current_set(root)
    (ProjectPaths.of(root).migrations / "0002-broken.yaml").touch()
    return _with(base, project_root=root)


class _Shape(NamedTuple):
    """One state a project can be in when `initial-index` probes it.

    ``is_built`` is what `theurian project status` publishes as ``stateBuilt``
    for this shape, and ``None`` means the question has no answer because the
    migration set does not load. It stays a plain bool everywhere else on
    purpose: whether a pointer exists is not part of the predicate any more, so
    a shape that varies the pointer varies nothing this field records.
    """

    name: str
    build: Callable[[Path], SetupContext]
    is_built: bool | None


#: Every shape, and the population the tests below select from. Three of the
#: five are *not* the plain built case, and they differ in exactly the things
#: #451 confused with built-ness: no pointer and no database, a pointer with the
#: database behind it, a database with no pointer at all. The last has no
#: ``stateBuilt`` to publish, which is why it is answered separately rather than
#: folded in as a third truth value.
_SHAPES: tuple[_Shape, ...] = (
    _Shape("never-applied", _never_applied, False),
    _Shape("applied-and-current", _applied_and_current, True),
    _Shape("migrations-pulled-not-applied", _migrations_pulled_but_not_applied, False),
    _Shape("pointer-deleted-database-kept", _the_pointer_deleted_but_the_database_kept, True),
    _Shape("set-stopped-loading", _an_applied_project_whose_migrations_stopped_loading, None),
)

#: The shapes whose state hash resolves, so `project status` has a ``stateBuilt``
#: to publish and the step reaches the built / not-built return. Selected rather
#: than hardcoded, so a shape added above joins these tests by being added once.
_DECIDABLE = [
    pytest.param(s.build, s.is_built, id=s.name) for s in _SHAPES if s.is_built is not None
]

#: Its complement: the shapes with no state to name at all.
_UNDECIDABLE = [pytest.param(s.build, id=s.name) for s in _SHAPES if s.is_built is None]


@pytest.mark.parametrize(("state", "is_built"), _DECIDABLE)
def test_the_initial_index_step_answers_whether_the_current_state_is_built(
    tmp_path: Path, state: Callable[[Path], SetupContext], is_built: bool
) -> None:
    """`doctor` must not tell a deployment its knowledge state is built when the
    database for the migration set on disk does not exist (#451).

    The claim is about *which* state, not about whether any state was ever
    built: a project applied last week and pulled this morning has a pointer, a
    database, and no database for what its migrations now hash to. `project
    status` says ``stateBuilt: false`` there and `doctor` said "Knowledge state
    is built.", so the two commands disagreed about one project on the same
    machine, and the one a person runs when something looks wrong was the one
    that was wrong.

    Both directions are asserted per shape -- the claim that must appear and the
    claim that must not -- because a summary carrying both would satisfy either
    half alone. The four decidable shapes are the class: pointer and database
    both absent, both present and current, pointer present with the database
    behind it, and database present with no pointer at all. The last pair are
    #451's two faces, and the second keeps the others from being satisfiable by
    a step that simply never says anything is built.

    Deliberately silent about `status`: nothing here fixes NOT_APPLICABLE in
    place, so a later change may make an unbuilt state MISSING with an action.
    The sentence an operator reads is the requirement.
    """
    context = state(tmp_path)
    root = context.project_root
    assert root is not None
    state_hash, _ = _current_state(root)
    assert ProjectPaths.of(root).database_for(state_hash).exists() is is_built, (
        "the fixture has to put on disk the state it claims to be fixturing"
    )
    expected, refused = (
        (_STATE_IS_BUILT, _APPLY_REMEDY) if is_built else (_APPLY_REMEDY, _STATE_IS_BUILT)
    )

    step = probe_initial_index(context)

    assert expected in step.summary, f"the summary has to say {expected!r}: {step.summary!r}"
    assert refused not in step.summary, f"the summary must not say {refused!r}: {step.summary!r}"


@pytest.mark.parametrize("state", _UNDECIDABLE)
def test_the_step_says_it_cannot_tell_when_the_migration_set_will_not_load(
    tmp_path: Path, state: Callable[[Path], SetupContext]
) -> None:
    """A project whose migrations stopped loading is not a project that is built.

    The sharpest place #451 can come back. A database and an active pointer are
    both on disk here, so every proxy the old predicate could reach for says
    yes, while the question the step publishes an answer to -- which state is
    this project at -- has no answer: the set the hash is computed from does not
    load. Answering "Knowledge state is built." would be the original defect,
    and answering "no knowledge state built yet" would be it pointing the other
    way, because a set nobody can read is not a set anyone can say that about.

    Pinned verbatim, unlike the two decidable arms. This wording is what a
    reader is sent to `theurian migrate validate` by, and it is the arm with no
    arithmetic behind it: the fixtures cannot contradict a rewrite here the way
    a moved state hash contradicts one there. Both other claims are pinned
    absent in the same breath -- the assertion that would have failed on `main`.
    """
    context = state(tmp_path)
    root = context.project_root
    assert root is not None
    paths = ProjectPaths.of(root)
    assert paths.active_pointer.exists(), "the pointer is present, and must not decide the answer"
    assert list(paths.state.glob("*.sqlite")), "so is a database; also not the question"

    step = probe_initial_index(context)

    assert step.summary == _CANNOT_TELL
    assert _STATE_IS_BUILT not in step.summary
    assert _APPLY_REMEDY not in step.summary


@pytest.mark.parametrize(("state", "is_built"), _DECIDABLE)
def test_the_initial_index_detail_names_the_commands_that_build_and_report_the_index(
    tmp_path: Path, state: Callable[[Path], SetupContext], is_built: bool
) -> None:
    """The step is named for the retrieval index and reports the canonical state,
    and the ``detail`` is the only place that difference is said (#451).

    The affirmative half, and the reason the negative one below can mean
    anything: with only forbidden phrases pinned, emptying the field satisfied
    every one of them trivially, and nothing else in this file looked at it
    (measured; round one measured the same across the suite). A reader whose
    state is built and whose index is not would be left with no sentence at all.

    ``is_built`` is taken and unused on purpose: it is what selects this
    parametrization to the shapes that reach the return carrying a ``detail``.
    The cannot-tell arm publishes none, which the test above is about.
    """
    context = state(tmp_path)

    step = probe_initial_index(context)

    missing = [c for c in _DETAIL_NAMES_THE_INDEX_COMMANDS if c not in step.detail]
    assert not missing, f"the detail no longer names {missing}: {step.detail!r}"


@pytest.mark.parametrize(("state", "is_built"), _DECIDABLE)
def test_the_initial_index_step_does_not_publish_indexes_as_future_work(
    tmp_path: Path, state: Callable[[Path], SetupContext], is_built: bool
) -> None:
    """The step's ``detail`` is a published claim, and it named a shipped feature
    as work that had not started (#451).

    "Retrieval indexes arrive in Milestone 5; there is nothing to build yet" is
    read by someone deciding what to do next, and `theurian index build` shipped
    in Milestone 5 -- so the reader was told to skip the command the same report
    exists to send them to. Prose in a report is not decoration: this file
    already pins three other summaries to the locations they would otherwise have
    lost, for the same reason.

    **What the sweep across shapes actually buys, stated honestly.** All four
    decidable shapes land on *one* ``return``, and two of them produce a
    byte-identical ``detail``, so this is one string checked against several
    inputs -- not several arms watched at once. It would not catch a rewrite
    confined to an arm no shape here reaches. What holds the content is the
    affirmative test above; this one holds that a *particular* false claim, the
    one #451 was filed over, does not come back in any of them.

    What is pinned is the absence of the false clauses -- see
    `_CLAIMS_INDEXES_ARE_FUTURE_WORK` for why the milestone number itself is
    fair game, and why no replacement wording is required here.
    """
    context = state(tmp_path)

    step = probe_initial_index(context)

    published = " ".join((step.summary, step.action, step.detail))
    assert not [clause for clause in _CLAIMS_INDEXES_ARE_FUTURE_WORK if clause in published], (
        f"a shipped feature is still published as future work: {published!r}"
    )


#: A build id for the index the rule below publishes. Crockford base32 like
#: every other identifier in this file.
_AN_INDEX_BUILD = "01K451CCCC01234567890ABCDE"


def _publish_a_retrieval_index(root: Path) -> None:
    """Put a retrieval index on disk: a pointer naming a build, and that build.

    What the product's own reader requires and no more. ``read_active_index_
    pointer`` returns a payload for a pointer carrying a non-empty
    ``indexBuildId`` and ``ActiveIndexPointer()`` for a tree with none, so these
    two trees are distinguishable by the code that answers the index half --
    which is the whole property the rule below needs, and it is asserted rather
    than assumed.

    Freshness is deliberately not fixtured. ``index_staleness`` weighs a state
    hash, a project id, a schema version and a serving profile, and nothing here
    claims anything about *which* verdict an index-side answer would reach --
    only that `initial-index` reaches none.
    """
    paths = ProjectPaths.of(root)
    index = paths.index_for(_AN_INDEX_BUILD)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.touch()
    paths.active_index_pointer.write_text(
        json.dumps({"indexBuildId": _AN_INDEX_BUILD}), encoding="utf-8"
    )


@pytest.mark.parametrize("state", [pytest.param(s.build, id=s.name) for s in _SHAPES])
def test_no_shape_changes_its_answer_when_a_retrieval_index_appears(
    tmp_path: Path, state: Callable[[Path], SetupContext]
) -> None:
    """§6.2 row 17 names two artefacts and `initial-index` answers the first
    (#451). The second one it must not answer at all, and this is what says so.

    Row 17's predicate -- "an ``active_index`` exists for the current
    ``state_hash``" -- is two questions. `theurian index status` owns the index
    half, and `requirements-analysis.md` records `doctor`'s silence about it as
    a requirement. A record like that is worth what holds it: one query against
    two corpora, the shape this project's closure arguments settle on. A tree
    with a published index and the same tree without one must produce the *same*
    step, field for field, on every shape a project can be in.

    Stronger than reading the source for an index symbol, and deliberately kept
    beside that rule rather than instead of it: this one does not care how an
    index answer would have been reached -- a helper, a ``getattr``, or a new
    injected ``SetupContext`` port, which is the shape #451's own fix took and
    the shape a name scan cannot see. It only cares that the published answer
    did not move.

    **It goes RED when #528 lands as an extension of this step**, which is its
    purpose: `test_setup_domain.py`'s ``len(StepId) == 19`` moves when #528 adds
    a *step* and does not move when #528 extends this one. When this fails,
    §6.2's paragraph is the thing to change first.
    """
    context = state(tmp_path)
    root = context.project_root
    assert root is not None
    paths = ProjectPaths.of(root)
    assert read_active_index_pointer(paths).payload is None, "no index has been built here yet"
    without_an_index = probe_initial_index(context)

    _publish_a_retrieval_index(root)

    assert read_active_index_pointer(paths).payload is not None, (
        "the fixture has to leave an index the product's own reader can see"
    )
    assert probe_initial_index(context) == without_an_index, (
        "the step's answer moved when a retrieval index appeared, so `doctor` now "
        "reports on row 17's second artefact; §6.2's record that no step does is "
        "no longer true"
    )


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
    file is created as a *directory*: a directory is not ``is_file()``, so the
    merge #128 added reads nothing and produces a fresh block, and the
    ``open(path, "w")`` that writes it raises ``IsADirectoryError`` -- a real
    critical failure from a shipped step, not an injected fake one. Verified
    against the merged code, because a fixture that stopped raising would leave
    every halt assertion below running on a converged run. ``env-reference`` is
    step 7, ahead of
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


def test_a_halted_report_says_how_far_the_run_got(tmp_path: Path) -> None:
    """#47. The steps are the only record of where a halted run stopped.

    Nothing is undone, so "which steps ran" is what an operator needs before they
    can repair the machine by hand: the ones that finished, the one that failed,
    and the ones never attempted are three different situations and the report
    has to tell them apart. The halt has its own ``return`` in
    `SetupService._apply`, so the steps it publishes are assembled by that arm
    alone -- measured, as a mutation: replacing that return's ``steps`` with an
    empty tuple passed the whole suite, publishing a failure that named no step
    at all while `changedPaths` still listed the files it wrote.

    All three outcomes are asserted by *value*, and the population is asserted to
    be every step in the specification. A halted report that dropped the steps it
    never reached would satisfy a check on the failed one alone, and tell the
    operator nothing about what setup still has left to do.
    """
    _, report = _halt_on_env_reference(tmp_path)

    assert report.state is SetupState.HALTED
    assert {step.step_id for step in report.steps} == set(StepId), (
        "a halted report still accounts for every step, attempted or not"
    )
    minted = report.step(StepId.TOKEN)
    failed = report.step(StepId.ENV_REFERENCE)
    never_reached = report.step(StepId.DAEMON_SERVICE)
    assert minted is not None and minted.outcome is StepOutcome.CHANGED, "this one was done"
    assert failed is not None and failed.outcome is StepOutcome.FAILED, "this one is why it stopped"
    assert never_reached is not None and never_reached.outcome is StepOutcome.NOT_ATTEMPTED, (
        "and this one was never tried, which is not the same as having failed"
    )


def test_a_halted_report_carries_the_reason_the_run_stopped(tmp_path: Path) -> None:
    """#47. ``warnings`` is the only field that says *why* a halt happened.

    ``state`` says the run stopped and ``changed_paths`` says what it had written
    by then; neither names the failure. The halted return builds its own
    ``warnings`` tuple, and emptying it passed the whole suite -- leaving a
    report that an operator can only act on by guessing which step broke.

    Asserted as the exact line the runner composes, so a warning that named the
    step without its reason, or the reason without its step, fails. The reason is
    checked for content first: an empty ``detail`` would make the membership
    assertion pass on a bare ``"env-reference: "``, which tells nobody anything.
    """
    _, report = _halt_on_env_reference(tmp_path)

    failed = report.step(StepId.ENV_REFERENCE)
    assert failed is not None
    assert "IsADirectoryError" in failed.detail, "the failed step has to carry the reason"

    assert f"{StepId.ENV_REFERENCE.value}: {failed.detail}" in report.warnings, (
        "the warning names the step that stopped the run and why it stopped"
    )


def test_a_halted_run_leaves_the_journal_it_wrote_on_disk(tmp_path: Path) -> None:
    """#47, §6.4. "Nothing is undone" covers the journal too.

    The journal is the record a person repairs a half-finished machine from, so
    a halt that tidied it away would delete the one artefact that says what had
    already been done -- and would do it precisely when it is needed. Nothing in
    `SetupService` deletes anything, and this pins that: inserting
    ``self.journal_path.unlink(missing_ok=True)`` before the halted return passed
    the whole suite, because every journal assertion in this module runs on a
    converged run.

    The contents are asserted as well as the file, so a halt that truncated or
    recreated it empty fails here too.
    """
    context, report = _halt_on_env_reference(tmp_path)

    journal = _service(context).journal_path
    assert report.state is SetupState.HALTED, "the fixture has to reach the halt path"
    assert journal.is_file(), "a halt undoes nothing, and the journal is a file this run wrote"
    assert journal.read_text(encoding="utf-8").splitlines(), (
        "and it still holds the record the run appended before it stopped"
    )


def test_a_halted_run_names_the_journal_among_the_files_it_wrote(tmp_path: Path) -> None:
    """#47. The journal belongs to no step, so only the runner can disclose it.

    ``changed_paths`` is read as the list of files this run wrote, and the
    journal is written by the runner rather than by any step's apply -- so
    accumulating step paths alone left ``~/.theurian/setup-journal.jsonl`` out of
    the report of every run that created it, while `--help` was claiming the
    seven steps are every write setup performs. Both halves have since moved: the
    runner appends the journal to ``changed_paths``, and `--help` names it as the
    one write outside those steps.

    The file is checked on disk first. Naming a path nothing wrote is the same
    defect pointing the other way -- `_journal` swallows its ``OSError``, so an
    append that never reached the disk must not be announced.

    **``count(...) == 1`` is guaranteed by the funnel, not by this run.** Every
    path leaves `SetupService._apply` through `_unique`, which returns
    ``dict.fromkeys(paths)`` -- so no path can appear twice in ``changed_paths``
    whatever the runner accumulated, and this count cannot reach two. Unlike the
    credential beside it, which ``token`` and ``token-storage`` genuinely both
    declare and which the funnel really is what collapses. What is pinned here is
    therefore presence; the count is the cheaper spelling of it, and it would
    only begin doing work of its own if the journal were ever appended per step
    ahead of the funnel.
    """
    context, report = _halt_on_env_reference(tmp_path)

    journal = _service(context).journal_path
    assert journal.is_file(), "the run has to have appended to the journal"
    assert report.changed_paths.count(str(journal)) == 1, (
        "the file the runner itself wrote is disclosed, and disclosed once"
    )


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


def test_a_halted_report_never_carries_the_token_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#47, SEC-6. A halted report discloses the credential's *path*, never its value.

    The halt path lists the leftover token in ``changed_paths`` so an operator can
    rotate it, but the token's own bytes must appear nowhere in the report -- not
    in ``changed_paths``, not in a ``warnings`` line, not in any step's
    ``summary``, ``action`` or ``detail``.

    **What the rest of the suite does and does not already hold.**
    `test_setup_report_withholding.py` sweeps sentinels seeded into files
    Theurian reads, but through `doctor --report`, which is a dry run -- so those
    four token assertions are on the **plan-built** path and never reach an
    apply. Its one real-apply case, `test_an_exception_from_an_apply_is_withheld
    _like_one_from_a_probe`, drives a **non-critical** step, ends DEGRADED, and
    asserts an exception *message* rather than a credential. So no test asserted
    anything about the value of the token setup itself mints against a report of
    any kind, and the HALTED terminal state has its own ``return`` in
    `SetupService._apply` that neither path reaches. This locks the property
    there.

    Asserted against **both** renderings the CLI ships, because they are separate
    code paths over the same payload: `theurian setup --json` goes through
    ``json.dumps`` while the default goes through
    `theurian.cli.commands._render`, which formats each ``steps`` entry with an
    f-string over the whole dictionary. A value withheld from one is not thereby
    withheld from the other.

    The text half carries two positive assertions before its prohibition, and
    they are what stop it decaying into a check that nothing was printed at all.
    `_render` reaches a step's ``detail`` only through its list branch, and a
    rendering that stopped printing lists would satisfy ``not in`` while covering
    nothing -- so the changed path and the failed step's reason are asserted
    *present* first.

    The guard on the value's length is what stops the absence assertions passing
    vacuously: a real ``token_urlsafe(32)`` credential is 43 characters of CSPRNG
    output and cannot coincidentally be absent, so ``not in`` is a measurement
    rather than an artefact of a short or empty string -- an empty value would
    make ``"" not in ...`` false and fail the test rather than pass it, which is
    exactly why the minimum length is asserted first.
    """
    context, report = _halt_on_env_reference(tmp_path)

    token_value = (context.auth_dir / TOKEN_KEY).read_text(encoding="utf-8").strip()
    assert report.state is SetupState.HALTED, "the fixture has to reach the halt path"
    assert token_value and len(token_value) >= MIN_TOKEN_LENGTH, (
        "a real credential must have been minted before the failure, or the "
        "absence assertions below prove nothing"
    )

    payload = report.to_json()
    assert token_value not in json.dumps(payload), (
        "a halted report may name the leftover credential's path, never its value"
    )
    _emit(payload, as_json=False)
    rendered = capsys.readouterr().out
    assert str(context.auth_dir / TOKEN_KEY) in rendered, "changedPaths has to be rendered"
    assert "IsADirectoryError" in rendered, "and the steps, which is where a leak would land"
    assert token_value not in rendered, (
        "and the same holds of the text rendering, which is what `theurian setup` "
        "prints when --json is not passed"
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
        # A checker that reads nothing, because no test in this module is about
        # migration validity and `_converged_repository`'s `0001-initial.yaml`
        # is an empty file the real loader refuses -- which would make that
        # state not converged for a reason that has nothing to do with what it
        # is fixturing. `_migrations_that_do_not_validate` injects a refusal
        # where the arm is wanted; the loader itself is wired in
        # `tests/integration/test_probe_migrations_validate.py`.
        "check_migrations": unchecked_migrations,
        "current_state_hash": state_hash_from_the_loader,
    }
    return SetupContext(**{**defaults, **overrides})
