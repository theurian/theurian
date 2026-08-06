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
from theurian.application.setup_withholding import failure_detail
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
    #: Consent to *proceed past* steps that reported ``CONFLICTING``, which is
    #: not consent to resolve them: :meth:`SetupService._apply` applies a step
    #: only where the plan said ``MISSING``, so a conflicting step is left
    #: exactly as the user has it and reported as conflicting again by the
    #: verification pass. Withheld by default even so, because proceeding still
    #: installs everything else around a configuration the user did not put
    #: there, and silence is not agreement (SEC-18).
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
                detail=failure_detail(exc, for_publication=self._context.for_publication),
                critical=step.critical,
            )
        # The step definition owns criticality; a probe should not be able to
        # promote its own failure into one that rolls the whole run back. It owns
        # `paths` for the same reason: the field is read as "setup writes here",
        # and a step with no action writes nowhere in any of its arms. Enforced
        # here rather than trusted to each probe, because the arms are the
        # problem -- `probe_project_registered` left `paths` empty on the arm its
        # author was thinking about and set it on the one beside it, and §6.2's
        # unimplemented rows will start reporting MISSING one day with no reason
        # to remember any of this.
        return SetupStep(
            step_id=probed.step_id,
            status=probed.status,
            summary=probed.summary,
            action=probed.action,
            paths=probed.paths if step.apply is not None else (),
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
                    f"{s.step_id.value} conflicts with what setup would install, and setup "
                    f"never replaces it. Re-run with --approve-conflicts to leave it as it "
                    f"is and apply the remaining steps."
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

            action = definition.apply
            if action is None:
                # A step that only describes. §6.2 rows 11-13 report what
                # `theurian init` and `theurian project register` would do and
                # setup performs neither, so there is nothing here to record as
                # done. This branch used to be reachable only through the one
                # below, which tests ``would_change`` -- and ``would_change`` is
                # ``MISSING`` and nothing else, so a report-only step that found
                # something missing fell through to the apply, called a
                # do-nothing function, and was recorded ``CHANGED`` with its
                # paths added to `changed_paths` and an "applied" line in the
                # journal. Five paths, all five absent when the run ended, named
                # as modified by every run including the second (FR-L2). Every
                # one of them is written by *some* command -- `theurian init`
                # writes four and `theurian project register` the fifth -- and
                # none of them by this one, which is the whole confusion.
                #
                # What the user is told does not shrink: the step keeps its
                # ``MISSING`` status and its ``action``, and `_verify` re-probes
                # it, warns, and ends the run DEGRADED exactly as before.
                applied.append(planned.applied(StepOutcome.UNCHANGED))
                continue

            if not planned.would_change:
                # `would_change` is ``MISSING`` and nothing else, so a
                # ``CONFLICTING`` step is recorded ``UNCHANGED`` and never
                # applied -- including when ``approve_conflicts`` let the run
                # reach here. That is the design, not an omission: approval buys
                # progress on the rest of the list, never an overwrite of a file
                # the user owns (SEC-18, ADR-0012), and `_verify` reports the
                # step as still conflicting so the run ends DEGRADED rather than
                # claiming a convergence it did not reach.
                applied.append(planned.applied(StepOutcome.UNCHANGED))
                continue

            try:
                action(self._context)
            except Exception as exc:  # reported in the step, not propagated
                # The same rule as a failed *probe*, through the same function.
                # Not reachable from `doctor --report` today, because that is a
                # dry run and never reaches `_apply` -- but spelling it out here
                # is what stops the two drifting: a bare f-string beside a
                # withholding one is how the first of these was missed.
                reason = failure_detail(exc, for_publication=self._context.for_publication)
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
    """Conflicts that consent cannot let the run proceed past.

    Not "conflicts consent cannot resolve": consent resolves none of them, since
    a ``CONFLICTING`` step is left exactly as it is whatever the user answers. What
    separates these two is that approval buys progress on the rest of the list for
    every other conflict and buys nothing here -- an unsupported platform or an
    unlocatable executable leaves nothing worth installing around it, so setup
    stops before it creates anything it would then have to explain.
    """
    return tuple(
        step
        for step in plan.conflicting_steps
        if step.step_id in {StepId.PLATFORM, StepId.CORE_PRESENT}
    )
