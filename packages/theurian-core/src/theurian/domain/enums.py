"""Closed vocabularies, and the two rules that read one of them.

These are ``StrEnum`` so they serialise to their own names in JSON and YAML, which
keeps migration files and MCP payloads readable without a mapping table.

:func:`may_surface` and :func:`may_disclose` are here rather than beside a caller
because each has several, across the application and MCP layers -- see their
docstrings, and the sets spelled out and pinned in
``tests/unit/test_gate_call_sites.py``. They are the *two* axes a read path gates
on: what state the item is in, and what disclosure class this deployment serves.
"""

from __future__ import annotations

from enum import StrEnum


class KnowledgeStatus(StrEnum):
    """Lifecycle state of a knowledge item.

    Only :attr:`APPROVED` is returned by default retrieval. Anything else needs an
    explicit opt-in filter, so an unreviewed draft can never be mistaken for a
    team decision.
    """

    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class TrustLevel(StrEnum):
    """How much scrutiny the content has received.

    Distinguishing :attr:`INFERRED` from :attr:`REVIEWED` is what keeps
    machine-generated knowledge from silently acquiring the authority of a
    human decision.
    """

    UNVERIFIED = "unverified"
    INFERRED = "inferred"
    REVIEWED = "reviewed"
    AUTHORITATIVE = "authoritative"


class Sensitivity(StrEnum):
    """Disclosure class. Also a RAPTOR scope component (ADR-0008)."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class KnowledgeKind(StrEnum):
    """What sort of knowledge an item holds. Drives default namespacing and retrieval weighting."""

    ARCHITECTURE = "architecture"
    DECISION = "decision"
    DOMAIN = "domain"
    OPERATIONS = "operations"
    SECURITY = "security"
    TESTING = "testing"
    API = "api"
    INCIDENT = "incident"
    CONVENTION = "convention"
    REJECTED_APPROACH = "rejected-approach"
    KNOWN_EXCEPTION = "known-exception"


class RelationType(StrEnum):
    """Typed edges between knowledge, specifications, reviews, code, and tests."""

    IMPLEMENTS = "implements"
    IMPLEMENTED_BY = "implemented_by"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    DEPENDS_ON = "depends_on"
    CONSTRAINED_BY = "constrained_by"
    VERIFIED_BY = "verified_by"
    REVIEWED_BY = "reviewed_by"
    CONTRADICTS = "contradicts"
    RELATED_TO = "related_to"
    DERIVED_FROM = "derived_from"
    EVIDENCED_BY = "evidenced_by"
    REJECTS = "rejects"
    EXCEPTION_TO = "exception_to"


#: Relations whose meaning is direction-dependent, mapped to their inverse.
#: Stored one way and traversed both ways, so a caller never has to know which
#: direction was recorded.
INVERSE_RELATIONS: dict[RelationType, RelationType] = {
    RelationType.IMPLEMENTS: RelationType.IMPLEMENTED_BY,
    RelationType.IMPLEMENTED_BY: RelationType.IMPLEMENTS,
    RelationType.SUPERSEDES: RelationType.SUPERSEDED_BY,
    RelationType.SUPERSEDED_BY: RelationType.SUPERSEDES,
}

#: Relations that must never form a cycle (INV-6). A superseded chain that loops
#: has no current revision, and a dependency loop has no resolution order.
ACYCLIC_RELATIONS: frozenset[RelationType] = frozenset(
    {
        RelationType.SUPERSEDES,
        RelationType.SUPERSEDED_BY,
        RelationType.DEPENDS_ON,
    }
)


class SpecificationStatus(StrEnum):
    """Lifecycle state of a specification."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class ReviewCommentCategory(StrEnum):
    """Classification applied to a review comment before candidate generation (§21)."""

    SPECIFICATION_GAP = "specification-gap"
    ARCHITECTURE_RULE = "architecture-rule"
    SECURITY_RULE = "security-rule"
    PERFORMANCE_RULE = "performance-rule"
    RELIABILITY_RULE = "reliability-rule"
    CODING_CONVENTION = "coding-convention"
    TESTING_RULE = "testing-rule"
    DOMAIN_RULE = "domain-rule"
    REJECTED_APPROACH = "rejected-approach"
    KNOWN_EXCEPTION = "known-exception"
    INCIDENT_PREVENTION = "incident-prevention"


class ReviewThreadState(StrEnum):
    """Resolution state of a review thread, as reported by the provider."""

    OPEN = "open"
    RESOLVED = "resolved"
    OUTDATED = "outdated"
    DISMISSED = "dismissed"


class CandidateStatus(StrEnum):
    """Lifecycle of a knowledge candidate.

    There is no ``auto_approved``. Promotion to approved knowledge happens only
    through a human-authored migration (ADR-0013, INV-7).
    """

    GENERATED = "generated"
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class TraceNodeType(StrEnum):
    """The kinds of node a traceability edge can connect."""

    REQUIREMENT = "requirement"
    SPECIFICATION = "specification"
    DECISION = "decision"
    KNOWLEDGE = "knowledge"
    PLAN = "plan"
    TASK = "task"
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    REVIEW_THREAD = "review_thread"
    CODE = "code"
    TEST = "test"
    OPERATIONAL_EVIDENCE = "operational_evidence"


class ChangeType(StrEnum):
    """Change categories a traceability policy can be configured against (§22)."""

    DOMAIN_BEHAVIOR = "domain-behavior"
    ARCHITECTURE = "architecture"
    BUG_FIX = "bug-fix"
    REFACTORING = "refactoring"
    FORMATTING = "formatting"


class PolicyRequirement(StrEnum):
    """How strongly a traceability policy demands an artifact."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    NONE = "none"


class ContentClassification(StrEnum):
    """Trust label attached to every retrieval result (SEC-15).

    Knowledge bodies are authored by people and ingested from external systems.
    Neither is a trusted instruction source, so every result says so explicitly.
    """

    UNTRUSTED_KNOWLEDGE = "untrusted-knowledge"
    SYSTEM_METADATA = "system-metadata"


#: Statuses `includeUnapproved` may surface (SEC-13, T-15).
#:
#: `REJECTED` is deliberately absent and there is no flag that adds it. A
#: rejected revision is one the team decided must *not* be followed, and it is
#: also where a secret that caused the rejection still lives. Everything else
#: unapproved is work in progress, which an author asking for it has a reason to
#: see.
SURFACEABLE_STATUSES: frozenset[KnowledgeStatus] = frozenset(
    {
        KnowledgeStatus.APPROVED,
        KnowledgeStatus.DRAFT,
        KnowledgeStatus.PROPOSED,
    }
)


def may_surface(status: KnowledgeStatus, *, include_unapproved: bool) -> bool:
    """Whether a caller may see an item in this state (SEC-13, T-15).

    ``include_unapproved`` widens which statuses are allowed. It never disables
    the check: retired knowledge -- deprecated, superseded, rejected -- is
    reachable through no flag, because a rejected revision is where the secret
    that caused the rejection still lives.

    Beside the set it reads, and in the domain, because it is consulted from
    six call sites: the index builder decides what to write; ``knowledge.search``
    decides what to return on each of its two answer paths; ``knowledge.get``
    decides both what to hand over by id and, per edge, whether a related item is
    surfaceable before it publishes the relation; and the withdrawal purge decides
    which revisions a still-published index must stop holding (ADR-0024 decision
    5), the one *inverse* use -- it names what is non-surfaceable so the purge and
    the surfacing gate cannot disagree about what is withheld. The builder used to
    inline the two comparisons instead of calling this, which is one copy of a
    security rule too many -- ``knowledge.get`` having *no* copy is how a caller
    who could not search for a withheld item could still fetch it. The sites are
    spelled out and pinned in ``tests/unit/test_gate_call_sites.py`` so a seventh
    cannot land unnoticed.
    """
    if status not in SURFACEABLE_STATUSES:
        return False
    return include_unapproved or status is KnowledgeStatus.APPROVED


def may_disclose(sensitivity: Sensitivity, *, visible: frozenset[Sensitivity]) -> bool:
    """Whether this deployment serves an item of this disclosure class (#119, SEC-13).

    ``visible`` is the expanded set of levels the deployment's authorization grant
    permits -- never a ceiling, and this signature is what forecloses the mistake.
    :data:`~theurian.application.authorization.DISCLOSURE_ORDER` expands the
    operator's declared ceiling into the set exactly once, at startup, because
    :class:`Sensitivity` is a ``StrEnum`` whose members compare *as strings*:
    ``Sensitivity.CONFIDENTIAL < Sensitivity.INTERNAL`` is ``True``, so an
    implementation reaching for ``<`` would not raise -- it would serve
    confidential content under an ``internal`` ceiling. A membership test cannot
    express that error.

    A second axis rather than a widening of :func:`may_surface`, because the two
    answer different questions and move independently: ``may_surface`` asks what
    the *item's lifecycle* permits and is refined by the caller's
    ``include_unapproved``; this asks what the *deployment* serves and no request
    parameter reaches it. An item must clear both.

    There is deliberately no default for ``visible``. "Everything is visible" is
    the bug this whole gate exists to close, and a default parameter is how it
    would come back -- the reason
    :class:`~theurian.application.visibility.Visibility` refuses one too.

    Consulted from three call sites, each spelled out and pinned in
    ``tests/unit/test_gate_call_sites.py``: the ranked path's canonical re-check
    (``CanonicalVisibility._may_surface``), ``knowledge.get``'s gate on the item it
    hands over by id, and the per-edge gate on each endpoint of a relation before
    it is published. ``knowledge.search``'s unranked fallback does *not* appear
    there and is not a fourth: it hands ``visible`` to the canonical store as a SQL
    predicate, so no above-ceiling row is materialised for a Python check to run on
    (``mcp.search._scan``, and the cost note on
    :meth:`~theurian.domain.ports.canonical_store.CanonicalStore.list_items_by_status`).
    """
    return sensitivity in visible
