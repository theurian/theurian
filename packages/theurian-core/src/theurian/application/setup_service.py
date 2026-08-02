"""The setup state machine (FR-L1, FR-L2, §6.1).

One implementation, shared by ``theurian setup`` and ``/theurian:setup``. Two
would drift, and the one that drifted would be the one the user ran.

The run is: probe everything → report the plan → apply only what the plan said →
probe everything again. That last pass is what makes the report trustworthy: it
states what *is*, not what the apply functions believe they did.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import STEPS, Step
from theurian.domain.setup import (
    SetupPlan,
    SetupReport,
    SetupState,
    SetupStep,
    StepId,
    StepOutcome,
    StepStatus,
)

#: Appended to, never rewritten. A crash mid-run leaves a readable record of
#: what had already been done -- which is the only thing that makes an
#: after-the-fact repair possible (§6.4).
JOURNAL_FILENAME: Final = "setup-journal.jsonl"


@dataclass(frozen=True, slots=True)
class SetupRequest:
    """What the caller is asking for."""

    dry_run: bool = False
    #: Consent to resolve steps that reported ``CONFLICTING``. Withheld by
    #: default: a conflict means something the user did not put there is about to
    #: be replaced, and silence is not agreement (SEC-18).
    approve_conflicts: bool = False


@final
class SetupService:
    """Builds a plan, applies it, and reports what is actually true."""

    def __init__(self, context: SetupContext, steps: Iterable[Step] = STEPS) -> None:
        self._context = context
        self._steps = tuple(steps)

    @property
    def journal_path(self) -> Path:
        return self._context.data_dir / JOURNAL_FILENAME

    # -- Planning ---------------------------------------------------------

    def plan(self) -> SetupPlan:
        """Probe everything, changing nothing.

        Every step is probed even after one reports a conflict, so the user sees
        the whole picture in one pass rather than fixing problems one run at a
        time.
        """
        return SetupPlan(steps=tuple(self._probe(step) for step in self._steps))

    def _probe(self, step: Step) -> SetupStep:
        """Probe one step, turning a broken probe into a reportable conflict.

        A probe that raises would otherwise abort the whole run, and the step
        that raised is usually the least important one on the list.
        """
        try:
            probed = step.probe(self._context)
        except Exception as exc:  # a probe must not abort the whole run
            return SetupStep(
                step_id=step.step_id,
                status=StepStatus.CONFLICTING,
                summary=f"Could not check {step.step_id.value}.",
                detail=f"{type(exc).__name__}: {exc}",
                critical=step.critical,
            )
        # The step definition owns criticality; a probe should not be able to
        # promote its own failure into one that rolls the whole run back.
        return SetupStep(
            step_id=probed.step_id,
            status=probed.status,
            summary=probed.summary,
            action=probed.action,
            paths=probed.paths,
            critical=step.critical,
            outcome=probed.outcome,
            detail=probed.detail,
        )

    # -- Running ----------------------------------------------------------

    def run(self, request: SetupRequest | None = None) -> SetupReport:
        """Build a plan and, unless this is a dry run, apply it."""
        ask = request or SetupRequest()
        plan = self.plan()

        blocking = _blocking_conflicts(plan)
        if blocking:
            return SetupReport(
                state=SetupState.ABORTED,
                steps=plan.steps,
                dry_run=ask.dry_run,
                serena_detected=self._context.mcp_config.serena_detected(),
                warnings=tuple(f"{s.step_id.value}: {s.detail}" for s in blocking),
            )

        if ask.dry_run:
            return SetupReport(
                state=SetupState.PLAN_BUILT,
                steps=plan.steps,
                dry_run=True,
                serena_detected=self._context.mcp_config.serena_detected(),
            )

        if plan.conflicting_steps and not ask.approve_conflicts:
            return SetupReport(
                state=SetupState.AWAITING_CONSENT,
                steps=plan.steps,
                serena_detected=self._context.mcp_config.serena_detected(),
                warnings=tuple(
                    f"{s.step_id.value} needs your approval before anything is replaced."
                    for s in plan.conflicting_steps
                ),
            )

        return self._apply(plan)

    def _apply(self, plan: SetupPlan) -> SetupReport:
        applied: list[SetupStep] = []
        changed: list[str] = []
        warnings: list[str] = []
        failed_critically = False

        for definition in self._steps:
            planned = plan.step(definition.step_id)
            if planned is None:  # pragma: no cover - plan covers every step
                continue

            if failed_critically:
                applied.append(planned.applied(StepOutcome.NOT_ATTEMPTED))
                continue

            if not planned.would_change:
                applied.append(planned.applied(StepOutcome.UNCHANGED))
                continue

            try:
                definition.apply(self._context)
            except Exception as exc:  # reported in the step, not propagated
                reason = f"{type(exc).__name__}: {exc}"
                applied.append(planned.applied(StepOutcome.FAILED, reason))
                warnings.append(f"{definition.step_id.value}: {reason}")
                self._journal(definition.step_id, "failed", reason)
                failed_critically = definition.critical
                continue

            applied.append(planned.applied(StepOutcome.CHANGED))
            changed.extend(planned.paths)
            self._journal(definition.step_id, "applied", planned.action)

        if failed_critically:
            # Nothing is undone. Every apply here is a create-or-tighten, and the
            # journal records what was done -- so the honest report is "this is
            # where it stopped", not a rollback that deletes a token another
            # session may already be using (§6.4).
            return SetupReport(
                state=SetupState.ROLLED_BACK,
                steps=tuple(applied),
                changed_paths=tuple(changed),
                warnings=tuple(warnings),
                serena_detected=self._context.mcp_config.serena_detected(),
            )

        return self._verify(applied, changed, warnings)

    def _verify(
        self, applied: list[SetupStep], changed: list[str], warnings: list[str]
    ) -> SetupReport:
        """Probe again and report what is true now.

        Without this the report says what the apply functions *tried* to do. The
        difference matters: writing a launchd plist and having launchd accept it
        are separate events, and only the second one is worth reporting.
        """
        verified = self.plan()
        outcomes = {step.step_id: step.outcome for step in applied}

        final_steps = tuple(
            step.applied(outcomes.get(step.step_id, StepOutcome.UNCHANGED))
            for step in verified.steps
        )
        unresolved = [step for step in final_steps if step.would_change or step.needs_consent]
        warnings.extend(
            f"{step.step_id.value} is still {step.status.value} after setup ran: {step.summary}"
            for step in unresolved
        )

        state = SetupState.CONVERGED if not warnings else SetupState.DEGRADED
        return SetupReport(
            state=state,
            steps=final_steps,
            changed_paths=tuple(changed),
            warnings=tuple(warnings),
            serena_detected=self._context.mcp_config.serena_detected(),
        )

    # -- Journal ----------------------------------------------------------

    def _journal(self, step_id: StepId, event: str, detail: str) -> None:
        """Append one line. Journalling must never break a working setup."""
        entry = {"step": step_id.value, "event": event, "detail": detail}
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:  # pragma: no cover - defensive
            return


def _blocking_conflicts(plan: SetupPlan) -> tuple[SetupStep, ...]:
    """Conflicts that no amount of consent can resolve.

    An unsupported platform or an unlocatable executable is not a decision the
    user can approve their way past, so setup stops before it creates anything
    it would then have to explain.
    """
    return tuple(
        step
        for step in plan.conflicting_steps
        if step.step_id in {StepId.PLATFORM, StepId.CORE_PRESENT}
    )
