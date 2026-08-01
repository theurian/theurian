"""Specifications and traceability edges.

A specification keeps its native structure (YAML, JSON, OpenAPI). Flattening it
to prose would remove exactly the fields coverage and drift detection need
(ADR-0010, FR-T1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import override

from theurian.domain.enums import (
    ChangeType,
    PolicyRequirement,
    SpecificationStatus,
    TraceNodeType,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import EdgeId, ProjectId, RevisionId, SpecId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import MediaType, ValidityPeriod


@dataclass(frozen=True, slots=True)
class Specification:
    """A registered specification, anchored to an immutable revision.

    ``structured`` holds the parsed native form. It is what makes
    ``spec.getCoverage`` and contradiction detection possible; the text
    projection is only for lexical retrieval.
    """

    spec_id: SpecId
    project_id: ProjectId
    revision_id: RevisionId
    title: str
    status: SpecificationStatus
    content_format: MediaType
    source_uri: str
    validity: ValidityPeriod
    structured: dict[str, object] = field(default_factory=dict)
    anchors: tuple[SourceAnchor, ...] = ()

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvariantViolationError(f"Specification {self.spec_id} must have a title")
        if not self.source_uri:
            raise InvariantViolationError(f"Specification {self.spec_id} must have a source_uri")

    def is_active_at(self, moment: datetime) -> bool:
        return self.status is SpecificationStatus.ACTIVE and self.validity.contains(moment)


@dataclass(frozen=True, slots=True)
class TraceNode:
    """One endpoint of a traceability edge.

    Deliberately not a foreign key: a traceability graph must be able to point at
    a test file or a pull request that Theurian does not itself own.
    """

    node_type: TraceNodeType
    node_id: str

    def __post_init__(self) -> None:
        if not self.node_id:
            raise InvariantViolationError("TraceNode.node_id must not be empty")

    @override
    def __str__(self) -> str:
        return f"{self.node_type.value}:{self.node_id}"


@dataclass(frozen=True, slots=True)
class TraceabilityEdge:
    """A typed, evidenced link along the requirement-to-operation chain.

    ``confidence`` matters because some edges are asserted by a human and others
    are inferred by a heuristic. Drift reporting must be able to distinguish
    "we know" from "we guessed".
    """

    edge_id: EdgeId
    project_id: ProjectId
    source: TraceNode
    relation_type: str
    target: TraceNode
    evidence: tuple[SourceAnchor, ...]
    confidence: float
    created_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise InvariantViolationError(
                f"confidence must be within [0.0, 1.0], got {self.confidence}"
            )
        if self.source == self.target:
            raise InvariantViolationError(f"Self-referential traceability edge on {self.source}")
        if not self.relation_type:
            raise InvariantViolationError("TraceabilityEdge.relation_type must not be empty")

    @property
    def is_inferred(self) -> bool:
        """Whether this edge was derived heuristically rather than asserted."""
        return self.confidence < 1.0


@dataclass(frozen=True, slots=True)
class TraceabilityRule:
    """What a given change type must be able to prove (§22 of the brief)."""

    change_type: ChangeType
    specification: PolicyRequirement = PolicyRequirement.OPTIONAL
    test: PolicyRequirement = PolicyRequirement.OPTIONAL
    review: PolicyRequirement = PolicyRequirement.OPTIONAL
    adr: PolicyRequirement = PolicyRequirement.NONE
    issue: PolicyRequirement = PolicyRequirement.NONE
    regression_test: PolicyRequirement = PolicyRequirement.NONE
    behavior_preservation_evidence: PolicyRequirement = PolicyRequirement.NONE

    def required_artifacts(self) -> tuple[str, ...]:
        """Names of the artifacts this rule requires, in declaration order."""
        return tuple(
            name
            for name, requirement in (
                ("specification", self.specification),
                ("test", self.test),
                ("review", self.review),
                ("adr", self.adr),
                ("issue", self.issue),
                ("regressionTest", self.regression_test),
                ("behaviorPreservationEvidence", self.behavior_preservation_evidence),
            )
            if requirement is PolicyRequirement.REQUIRED
        )


@dataclass(frozen=True, slots=True)
class TraceabilityPolicy:
    """The per-project mapping from change type to required traceability."""

    rules: tuple[TraceabilityRule, ...]

    def __post_init__(self) -> None:
        seen = [rule.change_type for rule in self.rules]
        duplicates = {c for c in seen if seen.count(c) > 1}
        if duplicates:
            names = ", ".join(sorted(c.value for c in duplicates))
            raise InvariantViolationError(f"Duplicate traceability rules for: {names}")

    def rule_for(self, change_type: ChangeType) -> TraceabilityRule:
        """The rule for ``change_type``, or a permissive default.

        Defaulting to permissive is deliberate: an unconfigured change type must
        not block work. Teams tighten policy as they adopt it.
        """
        for rule in self.rules:
            if rule.change_type is change_type:
                return rule
        return TraceabilityRule(change_type=change_type)
