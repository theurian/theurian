"""Review knowledge.

Review history is *evidence*: what was said, by whom, on which line, and whether
it was acted on. Approved knowledge is a *generalisation* of that evidence into a
reusable rule. The two are never the same object, and the step between them is a
human decision (ADR-0013, FR-V4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from theurian.domain.enums import (
    CandidateStatus,
    KnowledgeKind,
    ReviewCommentCategory,
    ReviewThreadState,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.knowledge import SourceAnchor


@dataclass(frozen=True, slots=True)
class ReviewParticipant:
    """A review author.

    Stored as the provider's stable id plus a display name, so redaction can
    replace the display name without breaking the identity graph (R-12).
    """

    provider: str
    external_id: str
    display_name: str

    def __post_init__(self) -> None:
        if not self.external_id:
            raise InvariantViolationError("ReviewParticipant.external_id must not be empty")


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    """A pull request together with its outcome."""

    project_id: ProjectId
    provider: str
    repository: str
    number: int
    title: str
    author: ReviewParticipant
    created_at: datetime
    url: str
    head_commit: str
    base_commit: str
    merged: bool = False
    merge_commit: str | None = None
    merged_at: datetime | None = None
    ci_successful: bool | None = None
    linked_issue_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.number < 1:
            raise InvariantViolationError(
                f"Pull request number must be positive, got {self.number}"
            )
        if self.merged and self.merge_commit is None:
            raise InvariantViolationError(
                f"Merged pull request {self.repository}#{self.number} must record a merge commit"
            )

    @property
    def external_key(self) -> str:
        """Stable identity across re-ingestion: provider, repository, number."""
        return f"{self.provider}:{self.repository}#{self.number}"


@dataclass(frozen=True, slots=True)
class ReviewComment:
    """A single comment within a thread."""

    external_id: str
    author: ReviewParticipant
    body: str
    created_at: datetime
    category: ReviewCommentCategory | None = None
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        if not self.external_id:
            raise InvariantViolationError("ReviewComment.external_id must not be empty")


@dataclass(frozen=True, slots=True)
class ReviewResolution:
    """How, and if the provider records it, when and by whom a thread was closed.

    **Two fields are optional because GitHub cannot fill them** (ADR-0030
    decision 5, measured by schema introspection on 2026-09-05):
    ``PullRequestReviewThread`` carries **no resolution timestamp at all**, and
    its ``resolvedBy`` is nullable -- a thread can be resolved with no
    participant recorded.

    A required field the provider cannot fill leaves an adapter only bad
    options. Fabricate a value -- the ingestion time, or the last comment's --
    and every consumer downstream reads as a measurement something nobody
    measured; drop the whole resolution record instead, and the resolution
    *state* the model exists to carry is lost with it. ``None`` is the honest
    value for a quantity the provider does not record, and it is the one thing
    neither bad option can express.

    ``state`` leads the field order because it is the only one the provider
    always answers, and because the two optional fields must follow a field with
    no default. That reordering is the breaking half of this change: a positional
    construction moves.
    """

    state: ReviewThreadState
    resolved_by: ReviewParticipant | None = None
    resolved_at: datetime | None = None
    fix_commit: str | None = None

    def __post_init__(self) -> None:
        if self.state is ReviewThreadState.OPEN:
            raise InvariantViolationError("A resolution cannot record state 'open'")


@dataclass(frozen=True, slots=True)
class ReviewThread:
    """A conversation anchored to a file and line range."""

    external_id: str
    project_id: ProjectId
    event_key: str
    file_path: str | None
    comments: tuple[ReviewComment, ...]
    state: ReviewThreadState
    resolution: ReviewResolution | None = None
    line_start: int | None = None
    line_end: int | None = None
    commit_sha: str | None = None

    def __post_init__(self) -> None:
        if not self.comments:
            raise InvariantViolationError(f"Review thread {self.external_id} has no comments")
        if self.state is ReviewThreadState.RESOLVED and self.resolution is None:
            raise InvariantViolationError(
                f"Review thread {self.external_id} is resolved but records no resolution"
            )

    @property
    def categories(self) -> frozenset[ReviewCommentCategory]:
        return frozenset(c.category for c in self.comments if c.category is not None)


@dataclass(frozen=True, slots=True)
class PromotionGate:
    """The signals that decide whether a thread is worth a human's attention.

    This gate answers "should someone look at this?", never "is this true?".
    Every field is an observed fact, so the decision is auditable rather than a
    model's opinion (FR-V4).
    """

    pull_request_merged: bool
    thread_resolved: bool
    fix_commit_present: bool
    not_dismissed_or_outdated: bool
    ci_successful: bool
    generalizable: bool
    has_evidence: bool

    @property
    def is_satisfied(self) -> bool:
        return all(
            (
                self.pull_request_merged,
                self.thread_resolved,
                self.fix_commit_present,
                self.not_dismissed_or_outdated,
                self.ci_successful,
                self.generalizable,
                self.has_evidence,
            )
        )

    def unmet(self) -> tuple[str, ...]:
        """Names of the unsatisfied signals, for an actionable explanation."""
        return tuple(
            name
            for name, satisfied in (
                ("pull_request_merged", self.pull_request_merged),
                ("thread_resolved", self.thread_resolved),
                ("fix_commit_present", self.fix_commit_present),
                ("not_dismissed_or_outdated", self.not_dismissed_or_outdated),
                ("ci_successful", self.ci_successful),
                ("generalizable", self.generalizable),
                ("has_evidence", self.has_evidence),
            )
            if not satisfied
        )


@dataclass(frozen=True, slots=True)
class KnowledgeCandidate:
    """A proposed generalisation of review evidence into a reusable rule.

    A candidate is never approved knowledge. It has no ``approve()`` method by
    design: promotion happens only through a human-authored migration (INV-7).
    """

    candidate_id: str
    project_id: ProjectId
    source_thread_id: str
    proposed_item_id: ItemId
    title: str
    body: str
    kind: KnowledgeKind
    category: ReviewCommentCategory
    gate: PromotionGate
    evidence: tuple[SourceAnchor, ...]
    generated_at: datetime
    status: CandidateStatus = CandidateStatus.GENERATED
    generator_model: str | None = None
    #: Always ``INFERRED``. A candidate cannot claim review-level trust; that is
    #: precisely what a human reviewer would be granting it.
    trust_level: TrustLevel = field(default=TrustLevel.INFERRED, init=False)
    #: Inherited from the review's project default; never widened at generation.
    sensitivity: Sensitivity = Sensitivity.INTERNAL

    def __post_init__(self) -> None:
        if not self.evidence:
            raise InvariantViolationError(
                f"Knowledge candidate {self.candidate_id} has no evidence. "
                "Candidates without evidence are not generated (ADR-0013)."
            )
        if not self.body.strip():
            raise InvariantViolationError(
                f"Knowledge candidate {self.candidate_id} has an empty body"
            )
        if not self.gate.is_satisfied:
            unmet = ", ".join(self.gate.unmet())
            raise InvariantViolationError(
                f"Knowledge candidate {self.candidate_id} was generated with an unmet "
                f"promotion gate: {unmet}"
            )
