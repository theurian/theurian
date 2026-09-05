"""Project registration, retrieval result shapes, review promotion, traceability."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

import pytest

from theurian.domain.context import RequestContext, SnapshotId
from theurian.domain.enums import (
    CandidateStatus,
    ChangeType,
    ContentClassification,
    KnowledgeKind,
    PolicyRequirement,
    ReviewCommentCategory,
    ReviewThreadState,
    Sensitivity,
    SpecificationStatus,
    TraceNodeType,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import (
    AgentId,
    EdgeId,
    IndexBuildId,
    ItemId,
    ProjectId,
    RevisionId,
    SpecId,
    TaskId,
)
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.project import (
    DEFAULT_KNOWLEDGE_DIRECTORY,
    GITIGNORE_ENTRIES,
    GITIGNORE_SECTIONS,
    GitignoreSection,
    Project,
)
from theurian.domain.retrieval import (
    Freshness,
    RetrievalResult,
    SafetyMetadata,
    TokenBudget,
)
from theurian.domain.review import (
    KnowledgeCandidate,
    PromotionGate,
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.domain.specification import (
    Specification,
    TraceabilityEdge,
    TraceabilityPolicy,
    TraceabilityRule,
    TraceNode,
)
from theurian.domain.values import MARKDOWN, ContentHash, ValidityPeriod

NOW = datetime(2026, 8, 1, tzinfo=UTC)
PROJECT = ProjectId("backend-service")
ANCHOR = SourceAnchor(provider="git", source_uri="git://x/a.md", file_path="a.md")


# ==========================================================================
# Project
# ==========================================================================


def _project(**overrides: object) -> Project:
    base: dict[str, object] = {
        "project_id": PROJECT,
        "root_path": "/Users/dev/repos/backend-service",
        "repository_url": "https://github.com/acme/backend-service",
        "default_branch": "main",
        "knowledge_directory": DEFAULT_KNOWLEDGE_DIRECTORY,
        "registered_at": NOW,
    }
    base.update(overrides)
    return Project(**base)  # type: ignore[arg-type]


def test_project_root_must_be_absolute() -> None:
    """A relative root makes every containment check depend on the cwd."""
    with pytest.raises(InvariantViolationError, match="absolute"):
        _project(root_path="repos/backend-service")


def test_knowledge_directory_must_be_relative() -> None:
    with pytest.raises(InvariantViolationError, match="relative"):
        _project(knowledge_directory=PurePosixPath("/etc/theurian"))


def test_project_exposes_its_standard_subdirectories() -> None:
    project = _project()
    assert project.migrations_directory == PurePosixPath(".theurian/migrations")
    assert project.knowledge_content_directory == PurePosixPath(".theurian/knowledge")
    assert project.specifications_directory == PurePosixPath(".theurian/specifications")
    assert project.proposals_directory == PurePosixPath(".theurian/proposals")
    assert project.proposals_local_directory == PurePosixPath(".theurian/proposals-local")
    assert project.state_directory == PurePosixPath(".theurian/state")


@pytest.mark.parametrize(
    "path",
    [
        ".theurian/state/theurian-state-a1b2c3.sqlite",
        ".theurian/cache/embeddings/a1b2c3.bin",
        ".theurian/runtime/daemon.pid",
        ".theurian/generated/reviews/pr-431.md",
        "somewhere/else/index.sqlite",
        "a/b/db.sqlite-wal",
    ],
)
def test_derived_paths_are_recognised(path: str) -> None:
    """`doctor` warns when Git is tracking something rebuildable (ADR-0004)."""
    assert _project().is_derived(PurePosixPath(path))


@pytest.mark.parametrize(
    "path",
    [
        ".theurian/migrations/01K1.yaml",
        ".theurian/knowledge/architecture/auth-policy.md",
        ".theurian/specifications/order-cancellation.yaml",
        ".theurian/config.yaml",
        ".theurian",
        "src/main.py",
    ],
)
def test_git_tracked_paths_are_not_derived(path: str) -> None:
    assert not _project().is_derived(PurePosixPath(path))


def test_gitignore_block_covers_every_derived_location() -> None:
    """The entries written by `theurian init` must match what is actually derived."""
    assert ".theurian/state/" in GITIGNORE_ENTRIES
    assert ".theurian/cache/" in GITIGNORE_ENTRIES
    assert ".theurian/runtime/" in GITIGNORE_ENTRIES
    assert ".theurian/generated/" in GITIGNORE_ENTRIES
    assert "*.sqlite" in GITIGNORE_ENTRIES
    assert "*.sqlite-wal" in GITIGNORE_ENTRIES
    assert "*.sqlite-shm" in GITIGNORE_ENTRIES


def test_the_local_proposal_directory_is_ignored_without_being_derived() -> None:
    """The two properties are separate, and only one of them applies (ADR-0028).

    `theurian init` must git-ignore `.theurian/proposals-local/`, because a
    committed ignore rule is the one thing `.git/info/exclude` could not give a
    clone. It must *not* be derived: `doctor` reads `is_derived` to tell an
    operator that a tracked path is a rebuildable artifact, and nothing rebuilds
    an authored local proposal -- so the sentence would be false in the
    direction that loses work.
    """
    assert ".theurian/proposals-local/" in GITIGNORE_ENTRIES
    assert not _project().is_derived(PurePosixPath(".theurian/proposals-local/01K/a.yaml"))


def test_each_managed_ignore_section_labels_by_the_derived_test_it_claims() -> None:
    """A section's label is a claim ``Project.is_derived`` must agree with (M-1).

    The block carries two labels since ADR-0028: "Derived artifacts. Rebuilt from
    Git-tracked migrations" and "Authored ... Nothing rebuilds it". ``doctor``
    reads ``is_derived`` to decide whether a tracked path is a rebuildable
    artifact it may tell an operator to delete, so an entry filed under the
    derived label that ``is_derived`` calls ``False`` -- or the reverse -- is a
    label that lies about the entry beneath it. Moving
    ``.theurian/proposals-local/`` under the derived header is exactly that, and a
    mutation doing so survives every other rule here; this is where it dies.

    The label's derived-claim is read from its own ADR-0004 wording ("Rebuilt"),
    so a reword that changes what a label claims is meant to land on this test.
    """
    project = _project()

    for section in GITIGNORE_SECTIONS:
        claims_derived = "Rebuilt" in section.comment
        for entry in section.entries:
            assert project.is_derived(PurePosixPath(entry)) == claims_derived, (
                f"{entry!r} is filed under {section.comment!r} (derived={claims_derived}), but "
                f"Project.is_derived reports {project.is_derived(PurePosixPath(entry))}"
            )


def test_every_managed_ignore_entry_is_declared_under_exactly_one_label() -> None:
    """`GITIGNORE_ENTRIES` is the sections' concatenation, not a second list.

    The block stopped being homogeneous in ADR-0028, so the label moved onto the
    run of entries it covers. That is worth nothing if the flat tuple is
    restated beside the sections: the two would agree on the day they were
    written and drift on the day an entry is added to one of them.
    """
    from_sections = [entry for section in GITIGNORE_SECTIONS for entry in section.entries]

    assert list(GITIGNORE_ENTRIES) == from_sections
    assert len(set(from_sections)) == len(from_sections), from_sections
    assert all(section.comment.startswith("#") for section in GITIGNORE_SECTIONS)


@pytest.mark.parametrize(
    ("comment", "entries", "expected"),
    [
        # A label written without its `#` is not a label: `ensure_gitignore`
        # writes it verbatim, so Git would read it as a rule and ignore a path
        # nobody chose.
        ("Derived artifacts.", (".theurian/state/",), "'#'"),
        ("# Derived artifacts.", (), "no entries"),
    ],
)
def test_a_gitignore_section_that_could_not_label_anything_is_refused(
    comment: str, entries: tuple[str, ...], expected: str
) -> None:
    with pytest.raises(InvariantViolationError, match=expected):
        GitignoreSection(comment=comment, entries=entries)


# ==========================================================================
# Request context
# ==========================================================================


def test_context_carries_explicit_project() -> None:
    context = RequestContext(project_id=PROJECT)
    assert context.project_id == PROJECT
    assert not context.is_pinned


def test_pinned_context_reports_its_snapshot() -> None:
    snapshot = SnapshotId(ContentHash.of_text("state"))
    context = RequestContext(project_id=PROJECT, snapshot_id=snapshot)
    assert context.is_pinned
    assert str(context.snapshot_id) == snapshot.state_hash.value


def test_context_redaction_is_logging_safe() -> None:
    context = RequestContext(
        project_id=PROJECT,
        agent_id=AgentId("agent-7"),
        task_id=TaskId("task-3"),
    )
    assert context.redacted() == {
        "projectId": "backend-service",
        "snapshotId": None,
        "agentId": "agent-7",
        "taskId": "task-3",
    }


def test_snapshot_id_parses_from_a_state_hash() -> None:
    digest = ContentHash.of_text("state").value
    assert SnapshotId.parse(digest).state_hash.value == digest


# ==========================================================================
# Retrieval results
# ==========================================================================


def _result(**overrides: object) -> RetrievalResult:
    base: dict[str, object] = {
        "item_id": ItemId("architecture.auth-policy"),
        "revision_id": RevisionId("01K1DEFREV1234567890ABCDEF"),
        "title": "Auth policy",
        "excerpt": "All service-to-service calls carry a signed token.",
        "content_type": MARKDOWN,
        "score": 0.87,
        "trust_level": TrustLevel.REVIEWED,
        "sensitivity": Sensitivity.INTERNAL,
        "freshness": Freshness(
            revision_created_at=NOW,
            indexed_at=NOW,
            is_within_validity=True,
            age_days=0,
        ),
        "snapshot_id": SnapshotId(ContentHash.of_text("state")),
        "index_build_id": IndexBuildId("01K1DEFDX01234567890ABCDEF"),
        "source_anchors": (ANCHOR,),
    }
    base.update(overrides)
    return RetrievalResult(**base)  # type: ignore[arg-type]


def test_results_are_labelled_untrusted_by_default() -> None:
    """SEC-15. A result that does not say so would be read as an instruction."""
    result = _result()
    assert result.safety.content_classification is ContentClassification.UNTRUSTED_KNOWLEDGE
    assert result.safety.may_contain_instructions
    assert not result.safety.executable


def test_a_result_can_never_be_marked_executable() -> None:
    with pytest.raises(InvariantViolationError, match="never executable"):
        SafetyMetadata(executable=True)


def test_result_without_provenance_is_rejected() -> None:
    """FR-R5: an unverifiable claim is worse than no answer."""
    with pytest.raises(InvariantViolationError, match="no source anchor"):
        _result(source_anchors=())


def test_negative_scores_are_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="score"):
        _result(score=-0.1)


def test_negative_age_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="age_days"):
        Freshness(revision_created_at=NOW, indexed_at=NOW, is_within_validity=True, age_days=-1)


def test_token_budget_reserves_headroom() -> None:
    assert TokenBudget(max_tokens=8000, reserved_tokens=1000).available_tokens == 7000


@pytest.mark.parametrize(
    ("max_tokens", "reserved"),
    [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 200)],
)
def test_invalid_token_budgets_are_rejected(max_tokens: int, reserved: int) -> None:
    with pytest.raises(InvariantViolationError):
        TokenBudget(max_tokens=max_tokens, reserved_tokens=reserved)


# ==========================================================================
# Review knowledge
# ==========================================================================

REVIEWER = ReviewParticipant(provider="github", external_id="U123", display_name="reviewer")


def test_merged_pull_request_must_record_a_merge_commit() -> None:
    """Without it, "was this actually shipped?" is unanswerable."""
    with pytest.raises(InvariantViolationError, match="merge commit"):
        ReviewEvent(
            project_id=PROJECT,
            provider="github",
            repository="acme/backend-service",
            number=431,
            title="Add cancellation guard",
            author=REVIEWER,
            created_at=NOW,
            url="https://github.com/acme/backend-service/pull/431",
            head_commit="a" * 40,
            base_commit="b" * 40,
            merged=True,
        )


def test_review_event_key_is_stable_across_reingestion() -> None:
    event = ReviewEvent(
        project_id=PROJECT,
        provider="github",
        repository="acme/backend-service",
        number=431,
        title="Add cancellation guard",
        author=REVIEWER,
        created_at=NOW,
        url="https://github.com/acme/backend-service/pull/431",
        head_commit="a" * 40,
        base_commit="b" * 40,
    )
    assert event.external_key == "github:acme/backend-service#431"


def test_thread_without_comments_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="no comments"):
        ReviewThread(
            external_id="PRRT-1",
            project_id=PROJECT,
            event_key="github:acme/backend-service#431",
            file_path="src/order.py",
            comments=(),
            state=ReviewThreadState.OPEN,
        )


def test_resolved_thread_must_record_its_resolution() -> None:
    with pytest.raises(InvariantViolationError, match="records no resolution"):
        ReviewThread(
            external_id="PRRT-1",
            project_id=PROJECT,
            event_key="github:acme/backend-service#431",
            file_path="src/order.py",
            comments=(_comment(),),
            state=ReviewThreadState.RESOLVED,
        )


def _comment(**overrides: object) -> ReviewComment:
    base: dict[str, object] = {
        "external_id": "C1",
        "author": REVIEWER,
        "body": "Cancellation must check the deadline before mutating state.",
        "created_at": NOW,
        "category": ReviewCommentCategory.DOMAIN_RULE,
    }
    base.update(overrides)
    return ReviewComment(**base)  # type: ignore[arg-type]


def test_thread_reports_its_comment_categories() -> None:
    thread = ReviewThread(
        external_id="PRRT-1",
        project_id=PROJECT,
        event_key="github:acme/backend-service#431",
        file_path="src/order.py",
        comments=(_comment(), _comment(external_id="C2", category=None)),
        state=ReviewThreadState.RESOLVED,
        resolution=ReviewResolution(
            resolved_by=REVIEWER,
            resolved_at=NOW,
            state=ReviewThreadState.RESOLVED,
            fix_commit="c" * 40,
        ),
    )
    assert thread.categories == frozenset({ReviewCommentCategory.DOMAIN_RULE})


def test_resolution_cannot_claim_the_thread_is_open() -> None:
    with pytest.raises(InvariantViolationError, match="cannot record state 'open'"):
        ReviewResolution(resolved_by=REVIEWER, resolved_at=NOW, state=ReviewThreadState.OPEN)


def _gate(**overrides: object) -> PromotionGate:
    base: dict[str, object] = dict.fromkeys(
        (
            "pull_request_merged",
            "thread_resolved",
            "fix_commit_present",
            "not_dismissed_or_outdated",
            "ci_successful",
            "generalizable",
            "has_evidence",
        ),
        True,
    )
    base.update(overrides)
    return PromotionGate(**base)  # type: ignore[arg-type]


def test_satisfied_gate_reports_no_unmet_signals() -> None:
    gate = _gate()
    assert gate.is_satisfied
    assert gate.unmet() == ()


def test_unmet_signals_are_named_so_the_reason_is_actionable() -> None:
    gate = _gate(ci_successful=False, generalizable=False)
    assert not gate.is_satisfied
    assert gate.unmet() == ("ci_successful", "generalizable")


def _candidate(**overrides: object) -> KnowledgeCandidate:
    base: dict[str, object] = {
        "candidate_id": "cand-1",
        "project_id": PROJECT,
        "source_thread_id": "PRRT-1",
        "proposed_item_id": ItemId("domain.order-cancellation-deadline"),
        "title": "Check the cancellation deadline before mutating state",
        "body": "Cancellation must verify the deadline before any state change.",
        "kind": KnowledgeKind.DOMAIN,
        "category": ReviewCommentCategory.DOMAIN_RULE,
        "gate": _gate(),
        "evidence": (ANCHOR,),
        "generated_at": NOW,
    }
    base.update(overrides)
    return KnowledgeCandidate(**base)  # type: ignore[arg-type]


def test_candidate_is_always_inferred_never_reviewed() -> None:
    """A candidate cannot grant itself the trust a human reviewer would give."""
    assert _candidate().trust_level is TrustLevel.INFERRED


def test_a_candidate_cannot_be_constructed_with_a_trust_level() -> None:
    """The whole invariant above is one keyword, and nothing else defends it.

    ``trust_level`` is ``field(init=False)`` and ``__post_init__`` never looks at
    it, so a candidate claiming review-level trust is refused by the *signature*
    rather than by a check. That makes the keyword load-bearing in a way a reader
    of ``__post_init__`` would not guess, and it is exactly the sort of thing a
    later edit relaxes to "make the constructor uniform": dropping ``init=False``
    keeps the default and keeps the test above green, while
    ``KnowledgeCandidate(trust_level=TrustLevel.REVIEWED)`` starts working. That
    mutation survived the whole suite (#129).

    ADR-0013 and INV-7: a candidate is evidence, and promotion to reviewed
    knowledge is a human decision recorded in a migration. A generator that could
    name its own trust level would make that decision for the human.
    """
    with pytest.raises(TypeError, match="trust_level"):
        _candidate(trust_level=TrustLevel.REVIEWED)


def test_candidate_starts_unapproved() -> None:
    assert _candidate().status is CandidateStatus.GENERATED


def test_candidate_has_no_self_approval_method() -> None:
    """ADR-0013: promotion happens through a human-authored migration only."""
    assert not {"approve", "promote", "publish"} & set(dir(KnowledgeCandidate))
    assert "AUTO_APPROVED" not in {member.name for member in CandidateStatus}


def test_candidate_without_evidence_is_rejected_at_generation() -> None:
    with pytest.raises(InvariantViolationError, match="no evidence"):
        _candidate(evidence=())


def test_candidate_with_an_unmet_gate_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="unmet promotion gate"):
        _candidate(gate=_gate(ci_successful=False))


def test_candidate_with_an_empty_body_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="empty body"):
        _candidate(body="   ")


# ==========================================================================
# Specifications and traceability
# ==========================================================================


def _specification(**overrides: object) -> Specification:
    base: dict[str, object] = {
        "spec_id": SpecId("spec.order-cancellation"),
        "project_id": PROJECT,
        "revision_id": RevisionId("01K1DEFREV1234567890ABCDEF"),
        "title": "Order cancellation",
        "status": SpecificationStatus.ACTIVE,
        "content_format": MARKDOWN,
        "source_uri": "git://x/.theurian/specifications/order-cancellation.yaml",
        "validity": ValidityPeriod(valid_from=NOW),
        "structured": {"preconditions": ["order.status == pending"]},
    }
    base.update(overrides)
    return Specification(**base)  # type: ignore[arg-type]


def test_specification_keeps_its_structured_form() -> None:
    """ADR-0010: flattening to prose would remove what coverage checks read."""
    assert _specification().structured == {"preconditions": ["order.status == pending"]}


def test_only_active_in_window_specifications_are_current() -> None:
    assert _specification().is_active_at(NOW)
    assert not _specification(status=SpecificationStatus.SUPERSEDED).is_active_at(NOW)
    assert not _specification(
        validity=ValidityPeriod(valid_from=NOW, valid_to=NOW + timedelta(days=1))
    ).is_active_at(NOW + timedelta(days=2))


def test_specification_requires_a_source_uri() -> None:
    with pytest.raises(InvariantViolationError, match="source_uri"):
        _specification(source_uri="")


def test_trace_node_renders_as_type_colon_id() -> None:
    node = TraceNode(node_type=TraceNodeType.SPECIFICATION, node_id="spec.order-cancellation")
    assert str(node) == "specification:spec.order-cancellation"


def _edge(**overrides: object) -> TraceabilityEdge:
    base: dict[str, object] = {
        "edge_id": EdgeId("01K1DEFEDG01234567890ABCDE"),
        "project_id": PROJECT,
        "source": TraceNode(TraceNodeType.SPECIFICATION, "spec.order-cancellation"),
        "relation_type": "implemented_by",
        "target": TraceNode(TraceNodeType.PULL_REQUEST, "431"),
        "evidence": (ANCHOR,),
        "confidence": 1.0,
        "created_at": NOW,
    }
    base.update(overrides)
    return TraceabilityEdge(**base)  # type: ignore[arg-type]


def test_asserted_and_inferred_edges_are_distinguishable() -> None:
    """Drift reporting must separate "we know" from "we guessed"."""
    assert not _edge().is_inferred
    assert _edge(confidence=0.6).is_inferred


def test_self_referential_edge_is_rejected() -> None:
    node = TraceNode(TraceNodeType.SPECIFICATION, "spec.x")
    with pytest.raises(InvariantViolationError, match="Self-referential"):
        _edge(source=node, target=node)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_edge_confidence_is_bounded(confidence: float) -> None:
    with pytest.raises(InvariantViolationError, match=r"\[0.0, 1.0\]"):
        _edge(confidence=confidence)


def test_policy_lists_only_required_artifacts() -> None:
    rule = TraceabilityRule(
        change_type=ChangeType.DOMAIN_BEHAVIOR,
        specification=PolicyRequirement.REQUIRED,
        test=PolicyRequirement.REQUIRED,
        review=PolicyRequirement.REQUIRED,
        adr=PolicyRequirement.OPTIONAL,
    )
    assert rule.required_artifacts() == ("specification", "test", "review")


def test_unconfigured_change_types_default_to_permissive() -> None:
    """Adopting a policy must never block work it was not written for."""
    policy = TraceabilityPolicy(
        rules=(
            TraceabilityRule(
                change_type=ChangeType.DOMAIN_BEHAVIOR,
                specification=PolicyRequirement.REQUIRED,
            ),
        )
    )
    assert policy.rule_for(ChangeType.DOMAIN_BEHAVIOR).required_artifacts() == ("specification",)
    assert policy.rule_for(ChangeType.FORMATTING).required_artifacts() == ()


def test_duplicate_policy_rules_are_rejected() -> None:
    """Two rules for one change type means the effective policy is arbitrary."""
    with pytest.raises(InvariantViolationError, match="Duplicate"):
        TraceabilityPolicy(
            rules=(
                TraceabilityRule(change_type=ChangeType.BUG_FIX),
                TraceabilityRule(change_type=ChangeType.BUG_FIX),
            )
        )
