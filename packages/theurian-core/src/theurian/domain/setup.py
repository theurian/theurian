"""Setup as data: a plan, a report, and a journal (FR-L1, FR-L2, §6).

Setup is modelled as a *plan* that is built by probing, shown, and only then
applied — not as a script that does things. The difference matters three times:

- ``--dry-run`` is the same code path with the apply step skipped, so what the
  user is shown cannot drift from what runs.
- Idempotence becomes checkable rather than hoped for: a second run must produce
  a plan whose every step is :attr:`StepStatus.SATISFIED`.
- A step that finds something unexpected can report :attr:`StepStatus.CONFLICTING`
  and stop, instead of overwriting and discovering the mistake later.

Nothing here performs I/O. The probes and actions live in the application layer,
which is what lets the whole state machine be tested without a machine to set up.
"""

from __future__ import annotations

from collections.abc import Container, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Self

from theurian.domain.errors import TheurianError


class StepId(StrEnum):
    """The steps of §6.2, in application order, and one §6.2 predates.

    An enum rather than free strings: the plugin's presentation groups steps by
    identity, and the journal records what was applied under these names -- a
    readable record for whoever repairs a half-finished run, not something
    production code reads back, since there is no inverse action to replay and
    nothing opens the file outside the tests. NFR-12's ``uninstall --dry-run``
    enumeration is the third reader and is **not wired**, as
    :attr:`SetupStep.paths` records. A typo in any of those should not typecheck.
    """

    PLATFORM = "platform"
    CORE_PRESENT = "core-present"
    ARTIFACT_INTEGRITY = "artifact-integrity"
    DATA_DIRECTORY = "data-directory"
    TOKEN = "token"  # noqa: S105 - a step name, not a secret
    TOKEN_STORAGE = "token-storage"  # noqa: S105 - a step name, not a secret
    #: The one member §6.2's numbered table does not number, added by #119 with
    #: the deployment serving profile (ADR-0025). It sits beside the token
    #: because it is the other operator-owned file in ``auth/``, and it is
    #: unnumbered because inserting a row here would move every row below it --
    #: and "§6.2 row N" is cited across the tree, the threat model included.
    #: §6.2 lists it as row 6a for that reason.
    SERVING_PROFILE = "serving-profile"
    ENV_REFERENCE = "env-reference"
    DAEMON_SERVICE = "daemon-service"
    DAEMON_RUNNING = "daemon-running"
    SINGLE_INSTANCE = "single-instance"
    PROJECT_REGISTERED = "project-registered"
    PROJECT_LAYOUT = "project-layout"
    GITIGNORE = "gitignore"
    MCP_CONNECTION = "mcp-connection"
    MCP_HEALTH = "mcp-health"
    MIGRATIONS_VALID = "migrations-valid"
    INITIAL_INDEX = "initial-index"
    SERENA_DETECTION = "serena-detection"


class StepStatus(StrEnum):
    """The tri-state probe result every step reports.

    Three states rather than a boolean because "already correct" and "present
    but different" demand opposite responses: one is skipped silently, the other
    stops the run to be shown to the user and is then left exactly as it was --
    setup overwrites nothing it did not install, with or without consent (SEC-18).
    """

    SATISFIED = "satisfied"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    #: Probed and found not to apply here — an optional integration that is
    #: absent, or a step this platform does not have. Distinct from SATISFIED so
    #: a report never claims to have checked something it skipped.
    NOT_APPLICABLE = "not-applicable"


class StepOutcome(StrEnum):
    """What actually happened to a step once the plan was applied."""

    UNCHANGED = "unchanged"
    CHANGED = "changed"
    FAILED = "failed"
    #: The plan was never applied (``--dry-run``), or an earlier critical
    #: failure stopped the run before reaching this step.
    NOT_ATTEMPTED = "not-attempted"


class SetupState(StrEnum):
    """Terminal and intermediate states of §6.1."""

    PREFLIGHT = "preflight"
    PLAN_BUILT = "plan-built"
    AWAITING_CONSENT = "awaiting-consent"
    APPLYING = "applying"
    VERIFYING = "verifying"
    CONVERGED = "converged"
    #: Success with warnings, not a failure. A missing optional integration must
    #: not stop local knowledge from working.
    DEGRADED = "degraded"
    #: A critical step failed and the run stopped where it was. Nothing is
    #: undone: every apply creates, tightens or rewrites a file Theurian owns,
    #: and the journal is append-only with no inverse action to replay (§6.4) --
    #: #128 records that the env rewrite does not preserve a hand-edited file.
    #: ``changed_paths`` lists the files this run wrote -- each applied step's
    #: declared artefacts, plus the failed step's declared paths this run moved
    #: or could not observe, plus the setup journal -- **including any
    #: credential minted before the failure**, so the operator can act on it.
    #: Terminal, and not a success.
    HALTED = "halted"
    ABORTED = "aborted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            SetupState.CONVERGED,
            SetupState.DEGRADED,
            SetupState.HALTED,
            SetupState.ABORTED,
        }

    @property
    def is_success(self) -> bool:
        """``DEGRADED`` counts. It is success with warnings (§6.1)."""
        return self in {SetupState.CONVERGED, SetupState.DEGRADED}


class SetupError(TheurianError):
    """Setup could not proceed. Carries a remedy, never a stack trace."""


@dataclass(frozen=True, slots=True)
class DifferingFields:
    """Which fields of an installed configuration differ, in a form safe to publish.

    A conflicting setup step is asked what differs by a report that gets pasted
    into public issues, and the installed configuration is a file somebody else
    wrote. Values were the obvious hazard and are never carried here. **Names are
    the second hazard**, and the reason this type exists rather than a plain
    tuple of strings.

    A field name is only schema if Theurian is the one who defined it. Take the
    names from the installed file and a name is data: ``~/.claude.json``'s
    ``theurian`` entry may hold any top-level key a person put there, and a
    systemd unit's directives are read out of a format Theurian does not own --
    a continuation line, which is the *value* of the directive above it, parses
    alone as a name, so

        ExecStart=/usr/bin/theurian daemon start \\
            --header "Authorization: Bearer <token>"

    yielded that header as a differing field name, inside the sentence promising
    the values were withheld.

    So :meth:`over` intersects with the names Theurian's own renderer produces
    and counts the rest. **A name Theurian writes cannot be a value it read**,
    which closes that without resting on a parser being right about somebody
    else's file format -- and a count still tells the reader there is more to
    look at on their own terminal.
    """

    #: Differing field names Theurian itself authors. Sorted, so two machines
    #: holding the same configuration produce the same sentence.
    named: tuple[str, ...] = ()
    #: How many further fields differ under names Theurian does not write. Their
    #: names are withheld with their values.
    unnamed: int = 0
    #: Why there is nothing to name, when *unreadable* is the answer rather than
    #: "nothing differs". A Theurian-authored fragment such as ``could not be
    #: parsed`` -- never text taken from the file it describes, which is the
    #: whole reason this type exists. Empty means the comparison succeeded.
    #:
    #: Told apart from an empty result because the two mean opposite things to a
    #: reader: "no field differs" invites them to look elsewhere, and "the file
    #: does not parse" is the answer.
    unreadable: str = ""

    def __post_init__(self) -> None:
        if self.unnamed < 0:
            msg = f"unnamed cannot be negative, got {self.unnamed}"
            raise SetupError(msg)
        if self.unreadable and (self.named or self.unnamed):
            msg = (
                f"a configuration that {self.unreadable} yields no field names; "
                f"got named={self.named!r} unnamed={self.unnamed}"
            )
            raise SetupError(msg)

    @classmethod
    def over(cls, differing: Iterable[str], *, authored: Container[str]) -> Self:
        """Split differing names by whether Theurian's renderer produces them."""
        found = tuple(differing)
        return cls(
            named=tuple(sorted(name for name in found if name in authored)),
            unnamed=sum(1 for name in found if name not in authored),
        )


@dataclass(frozen=True, slots=True)
class SetupStep:
    """One step: what it is, what was found, and what would be done about it."""

    step_id: StepId
    status: StepStatus
    #: One line, addressed to a person reading a plan.
    summary: str
    #: What applying this step would do. Empty when nothing would.
    action: str = ""
    #: Absolute paths this step would create or modify. Drives the changed-files
    #: list, and is published as ``steps[].paths``. A step that only reports
    #: names none, whatever it found -- every reader takes this field as a
    #: promise that setup writes there. The runner enforces that rather than
    #: trusting each probe's every arm to remember it.
    #:
    #: NFR-12 -- every file Theurian creates is enumerable by
    #: `uninstall --dry-run` before deletion -- is the requirement this field
    #: exists to satisfy, and it is **not wired yet**: `uninstall_command` builds
    #: its list from the service path and the MCP config alone and reads this
    #: field nowhere. Recorded as a requirement rather than a description,
    #: because it read as one for long enough to be repeated in three other
    #: comments.
    paths: tuple[str, ...] = ()
    #: A step whose failure halts the run rather than degrading it.
    critical: bool = True
    outcome: StepOutcome = StepOutcome.NOT_ATTEMPTED
    #: What was found, beyond the one line of :attr:`summary`: the difference on
    #: a CONFLICTING step, the reason on a FAILED one, why a NOT_APPLICABLE step
    #: does not apply here -- and on a SATISFIED step, a caveat that survives it
    #: being satisfied. :meth:`SetupService._verify` turns that last one into a
    #: report warning, which is how a run whose every step is satisfied can
    #: still end DEGRADED instead of claiming a state the machine is not in.
    #:
    #: Shown to the user, who decides whether the run proceeds around it; what
    #: was found is left in place either way. Published verbatim by `doctor
    #: --report`, so it carries Theurian's own text and never a line read out of
    #: a file somebody else wrote.
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status is StepStatus.MISSING and not self.action:
            msg = f"{self.step_id}: a MISSING step must say what it would do"
            raise SetupError(msg)
        if self.status is StepStatus.CONFLICTING and not self.detail:
            msg = (
                f"{self.step_id}: a CONFLICTING step must carry the difference it "
                f"found; the user is asked to approve it"
            )
            raise SetupError(msg)

    @property
    def would_change(self) -> bool:
        return self.status is StepStatus.MISSING

    @property
    def needs_consent(self) -> bool:
        """No conflict is passed over in silence, whatever the step (SEC-18).

        Consent to *proceed past* it, never to resolve it: the step is left
        exactly as it was found either way.
        """
        return self.status is StepStatus.CONFLICTING

    def applied(self, outcome: StepOutcome, detail: str = "") -> Self:
        return replace(self, outcome=outcome, detail=detail or self.detail)


@dataclass(frozen=True, slots=True)
class SetupPlan:
    """Everything setup would do, before it does any of it."""

    steps: tuple[SetupStep, ...]

    @property
    def is_empty(self) -> bool:
        """True when the environment has already converged.

        The plugin stops here and reports "already configured" rather than
        asking for consent to do nothing.
        """
        return not any(s.would_change or s.needs_consent for s in self.steps)

    @property
    def mutating_steps(self) -> tuple[SetupStep, ...]:
        return tuple(s for s in self.steps if s.would_change)

    @property
    def conflicting_steps(self) -> tuple[SetupStep, ...]:
        return tuple(s for s in self.steps if s.needs_consent)

    @property
    def requires_consent(self) -> bool:
        return bool(self.mutating_steps or self.conflicting_steps)

    @property
    def paths(self) -> tuple[str, ...]:
        """Every path any step would touch, de-duplicated, in plan order.

        What ``setup --json`` publishes is each step's own ``paths`` through
        :meth:`SetupReport.to_json`; this aggregate has no caller outside the
        domain yet. A test that covers only this one covers nothing a user sees.
        """
        seen: dict[str, None] = {}
        for step in self.steps:
            if step.would_change or step.needs_consent:
                seen.update(dict.fromkeys(step.paths))
        return tuple(seen)

    def step(self, step_id: StepId) -> SetupStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)


@dataclass(frozen=True, slots=True)
class SetupReport:
    """The result of a run — or of a ``--dry-run``, which reports the plan.

    Serialised to JSON for `theurian setup --json`, which the plugin command
    renders. The field names are the published contract.
    """

    state: SetupState
    steps: tuple[SetupStep, ...]
    dry_run: bool = False
    serena_detected: bool = False
    warnings: tuple[str, ...] = ()
    #: The files this run wrote: each applied step's declared artefacts, plus --
    #: for a step that failed partway -- those of its declared paths this run
    #: moved or could not observe, plus the setup journal when this run appended
    #: to it. Directories created implicitly are not listed: ``auth/`` under the
    #: data directory, and ``~/Library/LaunchAgents`` or
    #: ``~/.config/systemd/user``, which the service adapters create on the way
    #: to the definition file they do declare. Files a registered service writes
    #: afterwards -- ``daemon.log``, written by launchd and not by setup -- are
    #: not listed either. Empty on a dry run, on a run that aborted before
    #: applying, and on a second real run -- which is the idempotence contract of
    #: §6.3, and holds through the journal too: a run that applies nothing
    #: journals nothing.
    changed_paths: tuple[str, ...] = ()
    backups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return self.state.is_success

    def step(self, step_id: StepId) -> SetupStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def to_json(self) -> dict[str, object]:
        """The published shape. Keys are camelCase, matching every other
        ``--json`` command."""
        return {
            "state": self.state.value,
            "dryRun": self.dry_run,
            "succeeded": self.succeeded,
            "serenaDetected": self.serena_detected,
            "changedPaths": list(self.changed_paths),
            "backups": list(self.backups),
            "warnings": list(self.warnings),
            "steps": [
                {
                    "id": s.step_id.value,
                    "status": s.status.value,
                    "outcome": s.outcome.value,
                    "summary": s.summary,
                    "action": s.action,
                    "paths": list(s.paths),
                    "detail": s.detail,
                }
                for s in self.steps
            ],
        }
