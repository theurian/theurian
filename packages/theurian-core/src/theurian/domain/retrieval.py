"""Retrieval result types.

Every result carries three things a caller needs and would otherwise have to
guess at: where it came from, how current it is, and whether it may be trusted
as an instruction (it may not).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from theurian.domain.context import SnapshotId
from theurian.domain.enums import ContentClassification, Sensitivity, TrustLevel
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import IndexBuildId, ItemId, RevisionId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import MediaType

#: Excerpt length. Long enough to judge relevance, short enough that ten hits do
#: not become the whole answer.
EXCERPT_CHARS: Final = 280


def excerpt(text: str) -> str:
    """One line of a passage, for a caller deciding whether to fetch the rest.

    Lives in the domain rather than beside the wire shaper because two layers
    now need it: :mod:`theurian.mcp.results` bounds a hit's own passage with it,
    and the RAPTOR forest port bounds every ``raptorPath`` segment's title with
    it before the title leaves the index adapter -- so a summary node's text is
    already disclosure-minimised where it is read, not only where it is sent.
    Infrastructure may import the domain; it may not import the wire layer, which
    is why the shared function moved down rather than the adapter reaching up.
    """
    flattened = text.strip().replace("\n", " ")
    return flattened[:EXCERPT_CHARS] + ("..." if len(flattened) > EXCERPT_CHARS else "")


@dataclass(frozen=True, slots=True)
class SafetyMetadata:
    """The trust label attached to every retrieval result (SEC-15, T-3).

    Knowledge bodies contain sentences like "always validate input before
    persisting". That is a rule *being described*, not an instruction to the
    agent reading it. This label makes the distinction explicit on the wire so a
    calling agent has no excuse for conflating the two.

    Theurian labels; it does not enforce. Enforcement is the calling agent's
    responsibility, and that split is documented in SECURITY.md.
    """

    content_classification: ContentClassification = ContentClassification.UNTRUSTED_KNOWLEDGE
    may_contain_instructions: bool = True
    executable: bool = False

    def __post_init__(self) -> None:
        if self.executable:
            raise InvariantViolationError(
                "Retrieved knowledge is never executable. Theurian returns documents, "
                "not runnable content."
            )


@dataclass(frozen=True, slots=True)
class Freshness:
    """How current a result is, in terms a caller can act on."""

    revision_created_at: datetime
    indexed_at: datetime
    is_within_validity: bool
    age_days: int

    def __post_init__(self) -> None:
        if self.age_days < 0:
            raise InvariantViolationError(f"age_days must not be negative, got {self.age_days}")


@dataclass(frozen=True, slots=True)
class RaptorPathSegment:
    """One step of the summary path from a catalog root down to a leaf."""

    node_id: str
    level: int
    title: str


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """A single hit, complete with provenance and trust labelling.

    ``snapshot_id`` and ``index_build_id`` are present so a result can be
    reproduced later: "the search that produced this ran against this exact
    state and this exact index build".
    """

    item_id: ItemId
    revision_id: RevisionId
    title: str
    excerpt: str
    content_type: MediaType
    score: float
    trust_level: TrustLevel
    sensitivity: Sensitivity
    freshness: Freshness
    snapshot_id: SnapshotId
    index_build_id: IndexBuildId
    source_anchors: tuple[SourceAnchor, ...]
    raptor_path: tuple[RaptorPathSegment, ...] = ()
    safety: SafetyMetadata = field(default_factory=SafetyMetadata)

    def __post_init__(self) -> None:
        if self.score < 0.0:
            raise InvariantViolationError(f"score must not be negative, got {self.score}")
        if not self.source_anchors:
            raise InvariantViolationError(
                f"Result for {self.revision_id} has no source anchor. Every result must "
                "be traceable to its origin (FR-R5)."
            )


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """The caller's context budget for a retrieval response.

    Enforced by Theurian rather than by the caller: an agent that receives more
    context than it asked for has already paid for it.
    """

    max_tokens: int
    reserved_tokens: int = 0

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise InvariantViolationError(f"max_tokens must be positive, got {self.max_tokens}")
        if self.reserved_tokens < 0:
            raise InvariantViolationError(
                f"reserved_tokens must not be negative, got {self.reserved_tokens}"
            )
        if self.reserved_tokens >= self.max_tokens:
            raise InvariantViolationError(
                f"reserved_tokens ({self.reserved_tokens}) must be less than "
                f"max_tokens ({self.max_tokens})"
            )

    @property
    def available_tokens(self) -> int:
        return self.max_tokens - self.reserved_tokens
