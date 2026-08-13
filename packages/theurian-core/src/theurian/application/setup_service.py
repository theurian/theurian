"""The setup state machine (FR-L1, FR-L2, §6.1).

One implementation, shared by ``theurian setup`` and ``/theurian:setup``. Two
would drift, and the one that drifted would be the one the user ran.

The run is: probe everything → report the plan → apply only what the plan said →
probe everything again. That last pass is what makes the report trustworthy: it
states what *is*, not what the apply functions believe they did.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
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

#: The journal is created at -- and on every append repaired to -- the same mode
#: as the token beside it, for a weaker but real reason: see
#: :meth:`SetupService._journal`.
_JOURNAL_MODE: Final = 0o600


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
        # promote its own failure into one that halts the whole run. It owns
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
        journalled = False

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

            # Taken immediately before the apply, so the window it is compared
            # against excludes setup's own earlier writes -- not the journal
            # below, which is appended after the comparison, and not an earlier
            # step's artefacts. It cannot exclude another process touching the
            # same path while the apply runs; no clock window can, and the bias
            # in `_moved` is toward naming the path when it cannot tell.
            before = _snapshot(planned.paths)
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
                # An apply that raised may still have written its artefact:
                # `FileSecretStore.set` creates the token file with `O_CREAT`
                # before the `os.write` or the `chmod` that can fail, and
                # `apply_env_reference` opens with `O_TRUNC`. Listing only the
                # steps that *finished* leaves that file on disk and absent from
                # what the operator reads afterwards -- the disclosure defect #47
                # set out to fix, in the arm it did not cover. Which of them this
                # run actually wrote is `_changed_since`'s question, and it is
                # answered by comparison rather than by existence: a `MISSING`
                # step's declared path can be sitting there untouched, on every
                # arm that docstring enumerates.
                changed.extend(_changed_since(before))
                journalled = self._journal(definition.step_id, "failed", reason) or journalled
                failed_critically = definition.critical
                continue

            applied.append(planned.applied(StepOutcome.CHANGED))
            changed.extend(planned.paths)
            journalled = self._journal(definition.step_id, "applied", planned.action) or journalled

        if journalled:
            # The journal is a file this run wrote, and `changed_paths` is the
            # list of those. It belongs to no step -- the runner appends it, not
            # an apply -- so accumulating step paths alone left
            # `~/.theurian/setup-journal.jsonl` out of every applying run's
            # report while `--help` was claiming the steps are every write setup
            # performs. Added only when an append actually reached the disk:
            # `_journal` swallows its ``OSError``, and naming a file that was
            # never created is the same defect pointing the other way.
            changed.append(str(self.journal_path))

        if failed_critically:
            # Nothing is undone. Every apply here creates, tightens or rewrites a
            # file Theurian owns -- the env file only between its markers, since
            # #128 -- and the journal records what was done with no inverse to
            # replay. So the honest report is HALTED, "this is where it stopped",
            # not a rollback that deletes a token another session may already be
            # using (§6.4).
            return SetupReport(
                state=SetupState.HALTED,
                steps=tuple(applied),
                changed_paths=_unique(changed),
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
            changed_paths=_unique(changed),
            warnings=tuple(warnings),
            serena_detected=self._context.mcp_config.serena_detected(),
        )

    # -- Journal ----------------------------------------------------------

    def _journal(self, step_id: StepId, event: str, detail: str) -> bool:
        """Append one line, reporting whether the append completed.

        Not "whether the file grew", which is what this used to say: the open
        can succeed and the write or the close still raise. The caller turns
        this answer into a claim that the journal is a file this run wrote, so
        it has to be about the whole operation and not about the first part of
        it that could be observed.

        **A buffered writer and not a bare ``os.write``**, which is what makes
        that true. ``write(2)`` is permitted to write fewer bytes than it was
        handed and return that count without raising, so under a file-size
        limit -- or a full disk, the same path -- an ``os.write`` whose return
        was discarded left a truncated record and reported success: measured at
        three half-lines run together into a single entry no reader can parse,
        announced in ``changed_paths`` as a file this run wrote.
        :class:`io.BufferedWriter` loops until the buffer is empty and raises
        whatever the flush or the close hit, which lands in the ``except
        OSError`` below. The bytes that did reach the disk are left there: the
        file is opened ``O_APPEND``, so truncating back to a remembered length
        would discard a concurrent writer's record rather than this one's. What
        was false was the answer, not the byte.

        **Created 0600, rather than at whatever the process umask allows.** The
        lines hold local absolute paths and the verbatim text of the exception
        that stopped a step; ``changed_paths`` now points every reader of a
        halted report straight at the file; and the arm that fails to tighten
        the data directory is precisely the arm that leaves this file's parent
        0755, because a refused ``chmod`` is what put the run here. A 0644
        default would publish the location of a record of the operator's
        filesystem that every local account can read. The mode is applied by
        the ``open`` that creates the file, so there is no window at the wider
        one.

        **And re-asserted on every append**, for two reasons the creation mode
        cannot reach on its own:

        * a journal an earlier version created keeps its own mode otherwise,
          and 0.1.0.dev0 and dev1 both created it through ``Path.open("a")`` --
          0644 under the usual umask. An installation that has run either of
          them would carry that file for its whole life;
        * the mode argument to ``os.open`` is ANDed with the umask, which can
          only take bits away. A 0277 umask creates the journal 0400, and then
          every later run's ``O_WRONLY`` open fails EACCES and the journal is
          silently never written again -- the failure mode this whole method
          exists to avoid, arriving from the direction nobody guards.

        ``os.fchmod`` on the descriptor rather than ``chmod`` on the path, so
        what changes mode is the file that was just opened and not whatever the
        name resolves to a moment later -- the same reason
        :meth:`~theurian.infrastructure.claude.mcp_config.ClaudeCodeMcpConfig.back_up`
        does it that way. Before the write, so nothing is appended to a file
        this run could not make private; a refused ``fchmod`` -- a journal owned
        by another account -- therefore skips the append and answers ``False``,
        which is the trade the 0600 creation already makes.

        Journalling must never break a working setup, so an ``OSError`` is
        swallowed -- and that is exactly why the answer is returned rather than
        assumed by the caller: ``changed_paths`` names the journal only when a
        write reached the disk, never because one was attempted. Reached by the
        read-only ``HOME`` case, where the directory this file needs is the one
        setup could not create.
        """
        entry = {"step": step_id.value, "event": event, "detail": detail}
        line = json.dumps(entry, sort_keys=True) + "\n"
        try:
            # Never the creator on a path setup takes itself, and kept for the
            # day that stops being true. `_journal` runs only from `_apply`, and
            # DATA_DIRECTORY is the first step there with an apply: by the time
            # anything is journalled the directory exists, or the condition that
            # stopped its apply -- a read-only `HOME` -- stops this `mkdir` too.
            # So the 0700 is what it *would* create with; an existing 0755 data
            # directory keeps 0755, which is why the file's own mode is
            # load-bearing above.
            self.journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # The builtin and not `Path.open`, which the linter would otherwise
            # want here: it takes no `opener`, and the opener is what carries
            # the creation mode.
            with open(
                self.journal_path,
                "a",
                encoding="utf-8",
                opener=lambda path, flags: os.open(path, flags, _JOURNAL_MODE),
            ) as handle:
                os.fchmod(handle.fileno(), _JOURNAL_MODE)
                handle.write(line)
        except OSError:
            return False
        return True


@dataclass(frozen=True, slots=True)
class _Observation:
    """One declared path as ``os.stat`` saw it, at a single instant.

    ``signature`` is ``None`` for a path that was not there, which is an answer
    and compares like one. :attr:`known` is what separates that from "setup
    could not look", because the two lead to opposite disclosures.
    """

    #: ``(st_ino, st_mode, st_size, st_mtime_ns)`` -- identity, permissions,
    #: length, and when the contents last moved. The mode is in there because
    #: the data-directory step's entire write *is* a mode: tightening an
    #: existing 0755 directory to 0700 moves nothing else, so a signature blind
    #: to permissions could not see that step happen at all.
    #:
    #: The last field is only as fine as the volume stores it. APFS and ext4
    #: keep nanoseconds; HFS+ and the FAT family keep whole seconds or coarser,
    #: and ``THEURIAN_DATA_DIR`` can point at one of those. There, a rewrite
    #: that changes neither length nor mode inside the same second is invisible
    #: here and its path goes unnamed -- the one direction in which this
    #: comparison is not biased toward disclosure.
    signature: tuple[int, int, int, int] | None
    #: ``False`` when the check itself failed for any reason other than absence.
    known: bool = True


#: Neither present nor absent: what a refused or impossible check reports.
_UNOBSERVABLE: Final = _Observation(signature=None, known=False)


def _observe(path: str) -> _Observation:
    """Reduce one path to the fields a write moves. Never raises.

    ``os.stat`` and not ``os.lstat``: every apply here writes *through* a
    symlink rather than replacing one -- ``os.open``, ``Path.mkdir``,
    ``Path.chmod`` and ``claude mcp add`` all follow links -- and a home
    directory kept in a dotfiles repository, where ``~/.claude.json`` is a link
    into it, is an ordinary machine rather than an exotic one. Watching the link
    instead of what it points at would report every such write as "nothing
    happened", which is the silence #47 exists to end, arriving from the other
    side.

    ``os.stat`` and not ``Path.stat`` either, which is what the linter wants
    here: ``Path("")`` is ``Path(".")``, so a step declaring the empty string --
    which ``setup_steps._service_path`` returns when no adapter attribute names
    the definition file -- would be measured against the current working
    directory. ``os.stat("")`` answers ENOENT, which is the truth about it.
    """
    try:
        status = os.stat(path)  # noqa: PTH116 -- see above: Path("") is not this path
    except (FileNotFoundError, NotADirectoryError):
        return _Observation(signature=None)
    except (OSError, ValueError):
        # EACCES on a parent, ELOOP, ENAMETOOLONG -- and ``ValueError`` for a
        # NUL byte in the string, which is not an ``OSError`` at all. The
        # distinction between "absent" and "unobservable" is the whole point of
        # the arm above; everything else says nothing about the file.
        return _UNOBSERVABLE
    return _Observation(
        signature=(status.st_ino, status.st_mode, status.st_size, status.st_mtime_ns)
    )


def _snapshot(paths: Iterable[str]) -> dict[str, _Observation]:
    """Observe one step's declared paths, keeping the order they were declared in.

    Called for each step just before its apply, so this is two ``stat`` calls at
    the very most -- no step declares more than one path today -- and never a
    walk of anything.
    """
    return {path: _observe(path) for path in paths}


def _changed_since(before: Mapping[str, _Observation]) -> tuple[str, ...]:
    """Which of those paths the step that just failed may have written.

    **Provenance, not existence.** Existence was the test here until it was
    measured, and it published paths a failed run had never touched. The claim
    it rested on -- that a step reaches the failure arm only with ``MISSING``,
    so a declared path that exists now was created by this run -- is false:
    ``MISSING`` means "not as setup wants it", which is not the same as absent.

    ==================  =====================================================
    step                how its declared path is already there when it fails
    ==================  =====================================================
    data-directory      the tighten arm, where a 0755 ``~/.theurian`` is the
                        very thing being reported; and a *group- or
                        other-accessible* regular file sitting where the
                        directory goes, which refuses the ``mkdir``. A 0600
                        file there is reported SATISFIED by the probe and
                        never applied at all, so it cannot reach this arm
    token,              a *directory* at ``auth/mcp-token``, which makes the
    token-storage       store's read raise before it writes anything
    env-reference       "present but differing" (#128)
    mcp-connection      ``~/.claude.json`` exists whenever Claude Code is on
                        PATH, and ``claude mcp add`` leaves it byte-identical
                        when it fails
    ==================  =====================================================

    The last two rows are why this is a correctness question and not a tidiness
    one. :class:`~theurian.infrastructure.claude.mcp_config.ClaudeCodeMcpConfig`
    opens by stating that Theurian never writes that file, so a report naming it
    among the files this run wrote contradicted the product's own account of
    itself. The token row is worse: the plugin reads ``changedPaths`` and tells
    the operator to rotate the credential it names, and there was no credential.

    So a path is named only where this run's own window shows it moved -- absent
    before and present now, or a different signature. The bias is still toward
    disclosure, and it has to be: a check that could not tell, on either side,
    names the path anyway, because "nothing was written" and "I could not look"
    are different answers and only one of them is safe to give in silence.

    **Never raises**, which is load-bearing rather than defensive. This runs
    inside the ``except`` arm that assembles the halted report; ``Path.exists``,
    which used to stand here, re-raises EACCES and ENAMETOOLONG (measured on
    3.13); and nothing between here and ``setup_command`` catches anything. A
    raise on this line would replace the report an operator repairs their
    machine from with a traceback.
    """
    return tuple(path for path, was in before.items() if _moved(was, _observe(path)))


def _moved(before: _Observation, after: _Observation) -> bool:
    """Whether the window between two observations may have written the path."""
    if not before.known or not after.known:
        return True
    return before.signature != after.signature


def _unique(paths: Iterable[str]) -> tuple[str, ...]:
    """Order-preserving de-duplication for a report's ``changed_paths``.

    The TOKEN and TOKEN_STORAGE steps both name ``auth/mcp-token``, so accumulating
    each applied step's ``paths`` lists it twice in what an operator reads after a
    run. A ``dict`` collapses repeats while keeping first-seen order -- the same
    mechanism :attr:`SetupPlan.paths` uses for the analogous aggregate.
    """
    return tuple(dict.fromkeys(paths))


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
