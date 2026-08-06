"""Setup as data (FR-L1, FR-L2, §6).

These are the rules the state machine cannot break, tested without a machine to
set up.
"""

from __future__ import annotations

import pytest

from theurian.domain.setup import (
    DifferingFields,
    SetupError,
    SetupPlan,
    SetupReport,
    SetupState,
    SetupStep,
    StepId,
    StepOutcome,
    StepStatus,
)


def _satisfied(step_id: StepId = StepId.TOKEN) -> SetupStep:
    return SetupStep(step_id=step_id, status=StepStatus.SATISFIED, summary="already there")


def _missing(step_id: StepId = StepId.TOKEN, paths: tuple[str, ...] = ()) -> SetupStep:
    return SetupStep(
        step_id=step_id,
        status=StepStatus.MISSING,
        summary="not present",
        action="create it",
        paths=paths,
    )


def _conflicting(step_id: StepId = StepId.MCP_CONNECTION) -> SetupStep:
    return SetupStep(
        step_id=step_id,
        status=StepStatus.CONFLICTING,
        summary="a different entry exists",
        detail="- url: http://old\n+ url: http://new",
    )


# -- A step must be able to explain itself ---------------------------------


def test_a_missing_step_must_say_what_it_would_do() -> None:
    """A plan is shown to a person before they approve it. "token: missing"
    with no action tells them nothing about what they are approving."""
    with pytest.raises(SetupError, match="what it would do"):
        SetupStep(step_id=StepId.TOKEN, status=StepStatus.MISSING, summary="no token")


def test_a_conflicting_step_must_carry_the_difference() -> None:
    """The user is asked whether the run may proceed past the difference, which
    setup leaves in place either way. Asking without showing it is asking them to
    guess (SEC-18)."""
    with pytest.raises(SetupError, match="difference"):
        SetupStep(
            step_id=StepId.MCP_CONNECTION,
            status=StepStatus.CONFLICTING,
            summary="something is different",
        )


def test_a_satisfied_step_needs_no_action() -> None:
    assert _satisfied().action == ""


# -- Convergence -----------------------------------------------------------


def test_a_plan_of_satisfied_steps_is_empty() -> None:
    """§6.3. The second run must reach this, which is what makes setup safe to
    put in front of every user on every machine."""
    plan = SetupPlan(steps=tuple(_satisfied(s) for s in list(StepId)[:5]))

    assert plan.is_empty
    assert not plan.requires_consent
    assert plan.paths == ()


def test_a_plan_with_one_missing_step_is_not_empty() -> None:
    plan = SetupPlan(steps=(_satisfied(StepId.DATA_DIRECTORY), _missing(StepId.TOKEN)))

    assert not plan.is_empty
    assert plan.requires_consent


def test_a_plan_that_only_conflicts_still_requires_consent() -> None:
    """A conflicting step is never applied, so it changes nothing by itself and
    consent does not change that. What consent releases is the rest of the run,
    which builds around a configuration setup did not install -- and treating
    "nothing to create" as "nothing to ask" would do that silently (SEC-18)."""
    plan = SetupPlan(steps=(_satisfied(), _conflicting()))

    assert not plan.is_empty
    assert plan.requires_consent
    assert plan.conflicting_steps[0].step_id is StepId.MCP_CONNECTION


def test_a_not_applicable_step_does_not_make_a_plan_non_empty() -> None:
    """Serena being absent is not work to do."""
    absent = SetupStep(
        step_id=StepId.SERENA_DETECTION,
        status=StepStatus.NOT_APPLICABLE,
        summary="Serena is not configured",
    )
    plan = SetupPlan(steps=(_satisfied(), absent))

    assert plan.is_empty


# -- Enumerating what setup touches ----------------------------------------


def test_the_plan_enumerates_every_path_it_would_touch() -> None:
    """`uninstall --dry-run` must be able to list everything setup created, so
    a plan that under-reports its paths leaves orphans behind (§20)."""
    plan = SetupPlan(
        steps=(
            _missing(StepId.DATA_DIRECTORY, ("/home/u/.theurian",)),
            _missing(StepId.TOKEN, ("/home/u/.theurian/auth/mcp-token",)),
            _satisfied(StepId.GITIGNORE),
        )
    )

    assert plan.paths == ("/home/u/.theurian", "/home/u/.theurian/auth/mcp-token")


def test_paths_are_deduplicated_but_keep_plan_order() -> None:
    """Two steps may touch one file — the token and its storage mode do. It
    should appear once, where it is first created."""
    plan = SetupPlan(
        steps=(
            _missing(StepId.TOKEN, ("/a/token",)),
            _missing(StepId.TOKEN_STORAGE, ("/a/token",)),
            _missing(StepId.ENV_REFERENCE, ("/a/env",)),
        )
    )

    assert plan.paths == ("/a/token", "/a/env")


def test_a_satisfied_step_contributes_no_paths() -> None:
    """Otherwise the changed-files list of a converged run is not empty, and
    the idempotence contract cannot be asserted."""
    plan = SetupPlan(
        steps=(SetupStep(StepId.TOKEN, StepStatus.SATISFIED, "there", paths=("/a/token",)),)
    )

    assert plan.paths == ()


# -- States ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "success"),
    [
        (SetupState.CONVERGED, True),
        (SetupState.DEGRADED, True),
        (SetupState.ROLLED_BACK, False),
        (SetupState.ABORTED, False),
    ],
)
def test_degraded_is_a_success(state: SetupState, success: bool) -> None:
    """§6.1. A missing `gh` token must not prevent local knowledge from
    working, so `degraded` cannot be reported as failure."""
    assert state.is_success is success
    assert state.is_terminal


@pytest.mark.parametrize(
    "state",
    [SetupState.PREFLIGHT, SetupState.PLAN_BUILT, SetupState.APPLYING, SetupState.VERIFYING],
)
def test_intermediate_states_are_not_terminal(state: SetupState) -> None:
    assert not state.is_terminal


# -- The published report shape --------------------------------------------


def test_a_dry_run_report_changes_nothing() -> None:
    report = SetupReport(
        state=SetupState.PLAN_BUILT, steps=(_missing(),), dry_run=True, changed_paths=()
    )

    assert report.to_json()["dryRun"] is True
    assert report.to_json()["changedPaths"] == []


def test_the_report_json_uses_the_published_key_names() -> None:
    """The plugin command reads these exact keys; renaming one breaks
    `/theurian:setup` without breaking a single Python test."""
    report = SetupReport(state=SetupState.CONVERGED, steps=(_satisfied(),), serena_detected=True)
    payload = report.to_json()

    assert set(payload) == {
        "state",
        "dryRun",
        "succeeded",
        "serenaDetected",
        "changedPaths",
        "backups",
        "warnings",
        "steps",
    }
    assert set(payload["steps"][0]) == {  # type: ignore[index]
        "id",
        "status",
        "outcome",
        "summary",
        "action",
        "paths",
        "detail",
    }
    assert payload["serenaDetected"] is True


def test_the_report_is_json_serialisable() -> None:
    """`--json` output goes through `json.dumps`; a StrEnum that leaked through
    as an enum would fail there rather than here."""
    import json

    report = SetupReport(
        state=SetupState.DEGRADED,
        steps=(_satisfied(), _missing(paths=("/a",)), _conflicting()),
        warnings=("gh is not authenticated",),
    )

    assert json.loads(json.dumps(report.to_json()))["state"] == "degraded"


def test_applying_a_step_records_its_outcome_without_mutation() -> None:
    """Steps are immutable; applying returns a new one. A plan that mutated in
    place could not be shown before and after."""
    before = _missing()
    after = before.applied(StepOutcome.CHANGED)

    assert before.outcome is StepOutcome.NOT_ATTEMPTED
    assert after.outcome is StepOutcome.CHANGED
    assert after.step_id == before.step_id


def test_a_step_can_be_found_by_id() -> None:
    report = SetupReport(
        state=SetupState.CONVERGED, steps=(_satisfied(StepId.TOKEN), _satisfied(StepId.GITIGNORE))
    )

    assert report.step(StepId.GITIGNORE) is not None
    assert report.step(StepId.MCP_HEALTH) is None


def test_every_step_of_the_specification_has_an_identifier() -> None:
    """§6.2 lists 19 rows; row 19 is the report itself rather than a step.

    A step that exists in the specification but not here would silently never
    run, and the report would look complete.
    """
    assert len(StepId) == 18
    assert StepId.PLATFORM is next(iter(StepId)), "platform check runs first"


# -- DifferingFields --------------------------------------------------------


def test_a_field_theurian_authors_is_named_and_the_rest_are_counted() -> None:
    """The whole rule the type exists for. A name read out of somebody else's
    file is whatever string sat in key position, so only the authored vocabulary
    may be published -- and the result is sorted, because it reaches a sentence
    two machines holding the same configuration must produce identically."""
    fields = DifferingFields.over(
        ["ExecStart", "X-Injected", "Environment"], authored={"ExecStart", "Environment"}
    )

    assert fields == DifferingFields(named=("Environment", "ExecStart"), unnamed=1)


def test_an_unreadable_configuration_cannot_also_name_fields() -> None:
    """`unreadable` means the comparison never happened. Fields beside it would
    be a sentence claiming both that nothing could be read and that these
    particular things differ."""
    with pytest.raises(SetupError, match="yields no field names"):
        DifferingFields(named=("ExecStart",), unreadable="is not a readable plist")

    with pytest.raises(SetupError, match="yields no field names"):
        DifferingFields(unnamed=1, unreadable="is not a readable plist")


def test_a_negative_count_of_withheld_fields_is_refused() -> None:
    """It reaches a sentence a person reads. "-1 further fields differ" is not a
    diagnostic, and the arithmetic that produced it is the thing to fix."""
    with pytest.raises(SetupError, match="cannot be negative"):
        DifferingFields(unnamed=-1)
