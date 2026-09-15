"""Applying an admitted operation leaves every item's read controls alone, with
one named exception (ADR-0032 decision 3, and round 3's closure argument pinned).

``generateMigrationDraft``'s v1 set was chosen on the axis *what a wrong proposal
moves*. The two operations that move an enforced read control -- ``changeSensitivity``
(``may_disclose``) and ``restoreItem`` (``may_surface``, widening) -- are pulled.
``deprecateItem`` stays: it is the one admitted kind that moves a read control,
and only in the *withdrawing* direction (``may_surface``, taking an item out of
the surfaceable set). Every other admitted kind touches a table beside the item
row -- relations, aliases, specifications, evidence, ownership -- and moves no
read control at all.

That was round 3's closure argument, and until now it was held by a keyword at a
call site and nothing else: ``migration_engine.py`` spells
``_replace_item(item, owner=operation.owner)`` while ``_replace_item`` accepts
``{"sensitivity", "owner", "trust_level", "status"}``, so an admitted operation
that grew a second keyword would move a read control with no test going RED. This
turns the argument into a pinned property: apply each admitted kind against a
corpus and compare every item's ``(status, sensitivity)`` pair before and after.
Only ``deprecateItem`` may move it, only the status half, and only its own item.

The corpus is built once with the preconditions every admitted kind needs -- a
second item to relate to, an alias and a relation to remove, a specification to
supersede, an evidence anchor to remove -- so each operation applies for real
rather than no-opping over an empty precondition (which would move nothing
whatever the code did).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FrozenClock, InMemoryWriter

from theurian.application.migration_engine import MigrationEngine
from theurian.application.proposal_service import V1_OPERATION_KINDS
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId, SpecId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import (
    AddAlias,
    AddEvidence,
    AddRelation,
    ChangeOwner,
    CreateItem,
    DeprecateItem,
    Migration,
    MigrationSet,
    Operation,
    OperationKind,
    RegisterSpecification,
    RemoveAlias,
    RemoveEvidence,
    RemoveRelation,
    RevisionMetadataSpec,
    SupersedeSpecification,
    UpsertRevision,
)
from theurian.domain.values import MARKDOWN, ContentHash

PROJECT = ProjectId("demo")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

ITEM = ItemId("architecture.auth-policy")
TARGET = ItemId("architecture.session-store")
SPEC = SpecId("architecture.auth-spec")
SPEC_SUCCESSOR = SpecId("architecture.auth-spec-v2")
EXISTING_ALIAS = ItemId("architecture.legacy-auth")
NEW_ALIAS = ItemId("architecture.new-auth")

REV_ITEM = RevisionId("01K1REV00101234567890ABCDE")
REV_TARGET = RevisionId("01K1REV00201234567890ABCDE")
BASE_MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
OP_MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"

BODY_ITEM = "The item body."
BODY_TARGET = "The target body."

#: The evidence anchor the base attaches to ITEM and ``removeEvidence`` removes.
EVIDENCE_ANCHOR = SourceAnchor(provider="git", source_uri="git://demo/evidence.md")


def _metadata(item_id: ItemId, sensitivity: Sensitivity) -> RevisionMetadataSpec:
    return RevisionMetadataSpec(
        title=item_id.value,
        content_type=MARKDOWN,
        kind=KnowledgeKind.ARCHITECTURE,
        namespace="backend",
        status=KnowledgeStatus.APPROVED,
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=sensitivity,
        source_anchors=(SourceAnchor(provider="git", source_uri=f"git://demo/{item_id.value}.md"),),
    )


def _create_and_upsert(
    item_id: ItemId, revision_id: RevisionId, body: str
) -> tuple[Operation, ...]:
    return (
        CreateItem(
            item_id=item_id,
            kind_=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            owner="platform-team",
        ),
        UpsertRevision(
            item_id=item_id,
            revision_id=revision_id,
            content_file_path=f"../knowledge/{item_id.value}.md",
            metadata=_metadata(item_id, Sensitivity.INTERNAL),
            content_sha256=ContentHash.of_text(body),
        ),
    )


#: The base corpus, carrying every precondition an admitted operation needs so it
#: applies for real: two items (``ITEM`` internal, ``TARGET`` internal), an alias
#: and a relation to remove, a specification to supersede, and an evidence anchor
#: to remove.
_BASE_OPERATIONS: tuple[Operation, ...] = (
    *_create_and_upsert(ITEM, REV_ITEM, BODY_ITEM),
    *_create_and_upsert(TARGET, REV_TARGET, BODY_TARGET),
    AddAlias(alias=EXISTING_ALIAS, item_id=ITEM),
    AddRelation(source_item_id=ITEM, relation_type=RelationType.RELATED_TO, target_item_id=TARGET),
    RegisterSpecification(
        spec_id=SPEC,
        item_id=ITEM,
        source_uri="git://demo/spec.md",
        content_format=MARKDOWN,
        status=SpecificationStatus.ACTIVE,
    ),
    AddEvidence(item_id=ITEM, anchor=EVIDENCE_ANCHOR, description="Base evidence for removal"),
)

#: One operation per admitted ``OperationKind``, placed against the base so each
#: really applies. Keyed by kind so the mapping's coverage is asserted against
#: :data:`V1_OPERATION_KINDS` -- a new admitted kind lands here by a deliberate
#: edit, never by omission.
_ADMITTED_OPERATION: dict[OperationKind, Operation] = {
    OperationKind.DEPRECATE_ITEM: DeprecateItem(item_id=ITEM, reason="Replaced"),
    OperationKind.ADD_RELATION: AddRelation(
        source_item_id=ITEM, relation_type=RelationType.DEPENDS_ON, target_item_id=TARGET
    ),
    OperationKind.REMOVE_RELATION: RemoveRelation(
        source_item_id=ITEM, relation_type=RelationType.RELATED_TO, target_item_id=TARGET
    ),
    OperationKind.ADD_ALIAS: AddAlias(alias=NEW_ALIAS, item_id=ITEM),
    OperationKind.REMOVE_ALIAS: RemoveAlias(alias=EXISTING_ALIAS),
    OperationKind.CHANGE_OWNER: ChangeOwner(item_id=ITEM, owner="security-team"),
    OperationKind.REGISTER_SPECIFICATION: RegisterSpecification(
        spec_id=SPEC_SUCCESSOR,
        item_id=ITEM,
        source_uri="git://demo/spec-v2.md",
        content_format=MARKDOWN,
        status=SpecificationStatus.DRAFT,
    ),
    OperationKind.SUPERSEDE_SPECIFICATION: SupersedeSpecification(
        spec_id=SPEC, superseded_by=SPEC_SUCCESSOR
    ),
    OperationKind.ADD_EVIDENCE: AddEvidence(
        item_id=ITEM,
        anchor=SourceAnchor(provider="git", source_uri="git://demo/more-evidence.md"),
        description="Another anchor",
    ),
    OperationKind.REMOVE_EVIDENCE: RemoveEvidence(
        item_id=ITEM, source_uri=EVIDENCE_ANCHOR.source_uri
    ),
}


def _engine() -> MigrationEngine:
    content = {ContentHash.of_text(body).value: body for body in (BODY_ITEM, BODY_TARGET)}
    return MigrationEngine(FrozenClock(NOW), content)


def _migration(migration_id: str, operations: tuple[Operation, ...]) -> Migration:
    return Migration(
        migration_id=MigrationId(migration_id),
        created_at=NOW,
        author="engineer@example.com",
        operations=operations,
        checksum=ContentHash.of_text(migration_id),
        depends_on=(),
        source_path=f"{migration_id}.yaml",
    )


def _read_controls(writer: InMemoryWriter) -> dict[str, tuple[KnowledgeStatus, Sensitivity]]:
    return {item.item_id.value: (item.status, item.sensitivity) for item in writer.items.values()}


def _state_after(*extra: Operation) -> dict[str, tuple[KnowledgeStatus, Sensitivity]]:
    """Every item's ``(status, sensitivity)`` after the base, then ``extra``.

    ``extra`` is applied as a second migration so the base stays frozen and the
    only change between two calls is the operation under test.
    """
    writer = InMemoryWriter()
    engine = _engine()
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_migration(BASE_MIGRATION_ID, _BASE_OPERATIONS),))
    )
    if extra:
        engine.apply(writer, PROJECT, MigrationSet.ordered((_migration(OP_MIGRATION_ID, extra),)))
    return _read_controls(writer)


def test_the_admitted_operation_map_covers_the_v1_set() -> None:
    """The battery below applies exactly the ten admitted kinds -- no more, no less.

    Asserted against :data:`V1_OPERATION_KINDS` rather than a hand-count, so a
    fifteenth ``OperationKind`` admitted into v1 later must be given a real
    operation here (with whatever precondition it needs) rather than silently
    escaping the read-control invariant (ADR-0032 decision 3).
    """
    assert set(_ADMITTED_OPERATION) == V1_OPERATION_KINDS


def test_the_base_corpus_holds_the_read_controls_the_invariant_measures() -> None:
    """Guards the invariant: both items start ``approved``/``internal``.

    Without this, a base that failed to build (or built one item at a different
    class) would make every comparison below trivially equal for a reason that has
    nothing to do with the operation under test.
    """
    base = _state_after()

    assert base == {
        ITEM.value: (KnowledgeStatus.APPROVED, Sensitivity.INTERNAL),
        TARGET.value: (KnowledgeStatus.APPROVED, Sensitivity.INTERNAL),
    }


_SORTED_ADMITTED: list[OperationKind] = sorted(V1_OPERATION_KINDS, key=lambda kind: kind.value)


@pytest.mark.parametrize("kind", _SORTED_ADMITTED, ids=[kind.value for kind in _SORTED_ADMITTED])
def test_an_admitted_operation_preserves_status_and_sensitivity_except_deprecate(
    kind: OperationKind,
) -> None:
    """Applying an admitted kind moves no item's ``(status, sensitivity)`` pair,
    with ``deprecateItem`` the one named exception (ADR-0032 decision 3).

    The control that gives this teeth is the comparison itself: if an admitted
    operation grew a ``sensitivity=`` or ``status=`` keyword at its
    ``_replace_item`` call site -- the way a wrong widening would arrive -- the
    pair would move and this would go RED. ``deprecateItem`` is allowed to move
    the *status* half of its own item, and only in the withdrawing direction
    (to ``DEPRECATED``); it must leave that item's sensitivity and every other
    item untouched.
    """
    before = _state_after()
    after = _state_after(_ADMITTED_OPERATION[kind])

    if kind is OperationKind.DEPRECATE_ITEM:
        assert after[ITEM.value] == (KnowledgeStatus.DEPRECATED, before[ITEM.value][1]), (
            "deprecateItem must move only the status of its own item, to DEPRECATED, "
            "and must not touch its sensitivity"
        )
        assert after[TARGET.value] == before[TARGET.value], (
            "deprecateItem moved a read control on an item it does not name"
        )
    else:
        assert after == before, (
            f"{kind.value} moved an item's (status, sensitivity) pair -- an admitted "
            f"operation must move no enforced read control (ADR-0032 decision 3):\n"
            f"  before: {before}\n  after:  {after}"
        )
