"""Migration engine guarantees (ADR-0005, ADR-0006).

The four properties that make the canonical store trustworthy: an applied
migration is frozen, conflicts are reported rather than merged, re-application
is a no-op, and ordering is deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FrozenClock, InMemoryWriter

from theurian.application.migration_engine import (
    MigrationEngine,
    refuse_unenforceable_scope,
    verify_no_applied_migration_changed,
)
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import (
    MigrationChecksumMismatchError,
    MigrationCycleError,
    MigrationDependencyMissingError,
    MigrationError,
    RevisionConflictError,
    UnenforceableScopeError,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import (
    AddAlias,
    AddRelation,
    ChangeOwner,
    CreateItem,
    DeprecateItem,
    Migration,
    MigrationSet,
    RevisionMetadataSpec,
    UpsertRevision,
)
from theurian.domain.values import MARKDOWN, ContentHash

PROJECT = ProjectId("demo")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
ANCHOR = SourceAnchor(provider="git", source_uri="git://demo/a.md")

BODY_V1 = "The first body."
BODY_V2 = "The second body."


def _metadata(**overrides: object) -> RevisionMetadataSpec:
    base: dict[str, object] = {
        "title": "Auth policy",
        "content_type": MARKDOWN,
        "kind": KnowledgeKind.ARCHITECTURE,
        "namespace": "backend",
        "status": KnowledgeStatus.APPROVED,
        "owner": "platform-team",
        "trust_level": TrustLevel.REVIEWED,
        "source_anchors": (ANCHOR,),
    }
    base.update(overrides)
    return RevisionMetadataSpec(**base)  # type: ignore[arg-type]


def _migration(
    migration_id: str, *operations: object, depends_on: tuple[str, ...] = ()
) -> Migration:
    return Migration(
        migration_id=MigrationId(migration_id),
        created_at=NOW,
        author="engineer@example.com",
        operations=tuple(operations),  # type: ignore[arg-type]
        checksum=ContentHash.of_text(migration_id),
        depends_on=tuple(MigrationId(d) for d in depends_on),
        source_path=f"{migration_id}.yaml",
    )


def _engine(*bodies: str) -> MigrationEngine:
    content = {ContentHash.of_text(b).value: b for b in bodies}
    return MigrationEngine(FrozenClock(NOW), content)


ITEM = ItemId("architecture.auth-policy")
REV_1 = RevisionId("01K1REV00101234567890ABCDE")
REV_2 = RevisionId("01K1REV00201234567890ABCDE")
MIG_1 = "01K1AAAAAA01234567890ABCDE"
MIG_2 = "01K1BBBBBB01234567890ABCDE"


def _create_and_upsert(
    migration_id: str,
    revision_id: RevisionId,
    body: str,
    metadata: RevisionMetadataSpec | None = None,
    **kw: object,
) -> Migration:
    return _migration(
        migration_id,
        CreateItem(
            item_id=ITEM,
            kind_=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            owner="platform-team",
        ),
        UpsertRevision(
            item_id=ITEM,
            revision_id=revision_id,
            content_file_path="../knowledge/a.md",
            metadata=metadata or _metadata(),
            content_sha256=ContentHash.of_text(body),
            **kw,  # type: ignore[arg-type]
        ),
    )


# -- Application -----------------------------------------------------------


def test_applying_a_migration_creates_an_item_and_a_revision() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))

    report = engine.apply(writer, PROJECT, migrations)

    assert report.applied == [MigrationId(MIG_1)]
    assert report.operations_applied == 2
    assert report.changed

    item = writer.get_item(PROJECT, ITEM)
    assert item is not None
    assert item.current_revision_id == REV_1
    assert item.status is KnowledgeStatus.APPROVED
    assert writer.revisions[REV_1.value].body == BODY_V1


def test_reapplying_the_same_set_is_a_no_op() -> None:
    """FR-K8. Idempotence is the engine's property, not each author's."""
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))

    _engine(BODY_V1).apply(writer, PROJECT, migrations)
    second = _engine(BODY_V1).apply(writer, PROJECT, migrations)

    assert second.applied == []
    assert second.skipped == [MigrationId(MIG_1)]
    assert not second.changed
    assert len(writer.revisions) == 1


def test_a_second_revision_supersedes_the_first() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1, BODY_V2)

    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )
    engine.apply(
        writer,
        PROJECT,
        MigrationSet.ordered(
            (
                _migration(
                    MIG_2,
                    UpsertRevision(
                        item_id=ITEM,
                        revision_id=REV_2,
                        content_file_path="../knowledge/a.md",
                        metadata=_metadata(),
                        expected_revision=REV_1,
                        content_sha256=ContentHash.of_text(BODY_V2),
                    ),
                ),
            )
        ),
    )

    item = writer.get_item(PROJECT, ITEM)
    assert item is not None
    assert item.current_revision_id == REV_2
    # History is preserved: the earlier revision is still readable (INV-1).
    assert len(writer.revisions) == 2
    assert writer.revisions[REV_1.value].body == BODY_V1


# -- ADR-0006: optimistic concurrency --------------------------------------


def test_a_stale_expected_revision_is_a_conflict() -> None:
    """Reported, never merged: an automatic merge of a design decision
    produces text nobody approved."""
    writer = InMemoryWriter()
    engine = _engine(BODY_V1, BODY_V2)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    stale = RevisionId("01K1REV09901234567890ABCDE")
    conflicting = MigrationSet.ordered(
        (
            _migration(
                MIG_2,
                UpsertRevision(
                    item_id=ITEM,
                    revision_id=REV_2,
                    content_file_path="../knowledge/a.md",
                    metadata=_metadata(),
                    expected_revision=stale,
                    content_sha256=ContentHash.of_text(BODY_V2),
                ),
            ),
        )
    )

    with pytest.raises(RevisionConflictError) as exc:
        engine.apply(writer, PROJECT, conflicting)

    assert exc.value.expected == stale
    assert exc.value.actual == REV_1
    assert exc.value.item_id == ITEM


def test_conflict_reports_both_revisions_so_a_human_can_decide() -> None:
    error = RevisionConflictError(ITEM, REV_1, REV_2)
    message = str(error)
    assert str(REV_1) in message
    assert str(REV_2) in message


def test_omitting_expected_revision_over_an_existing_item_is_a_conflict() -> None:
    """An absent guard means "this is the first revision".

    Applying it over an existing one would silently discard whatever is there.
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1, BODY_V2)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    with pytest.raises(RevisionConflictError):
        engine.apply(
            writer,
            PROJECT,
            MigrationSet.ordered(
                (
                    _migration(
                        MIG_2,
                        UpsertRevision(
                            item_id=ITEM,
                            revision_id=REV_2,
                            content_file_path="../knowledge/a.md",
                            metadata=_metadata(),
                            content_sha256=ContentHash.of_text(BODY_V2),
                        ),
                    ),
                )
            ),
        )


# -- ADR-0005: an applied migration is frozen ------------------------------


def test_editing_an_applied_migration_is_fatal() -> None:
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    _engine(BODY_V1).apply(writer, PROJECT, migrations)

    edited = MigrationSet.ordered(
        (
            Migration(
                migration_id=MigrationId(MIG_1),
                created_at=NOW,
                author="engineer@example.com",
                operations=migrations.migrations[0].operations,
                checksum=ContentHash.of_text("EDITED"),
            ),
        )
    )

    with pytest.raises(MigrationChecksumMismatchError) as exc:
        _engine(BODY_V1).plan(writer, PROJECT, edited)
    assert exc.value.migration_id == MigrationId(MIG_1)
    assert "never be edited" in str(exc.value)


def test_verification_runs_against_any_recorded_history() -> None:
    """ADR-0016: the evidence of an edit lives in the *previously active* state.

    Editing a migration changes the state hash, which routes the next command to
    a fresh empty database where nothing looks wrong. Checking only the current
    database would make this guarantee silently unenforceable.
    """
    recorded = {MigrationId(MIG_1): ContentHash.of_text("original").value}
    edited = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))

    with pytest.raises(MigrationChecksumMismatchError):
        verify_no_applied_migration_changed(recorded, edited)


def test_verification_passes_when_checksums_match() -> None:
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    recorded = {MigrationId(MIG_1): migrations.migrations[0].checksum.value}
    verify_no_applied_migration_changed(recorded, migrations)


def test_an_unrecorded_migration_is_not_a_mismatch() -> None:
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    verify_no_applied_migration_changed({}, migrations)


# -- Ordering --------------------------------------------------------------


def test_dependencies_determine_application_order() -> None:
    later = _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="b"), depends_on=(MIG_2,))
    earlier = _migration(MIG_2, ChangeOwner(item_id=ITEM, owner="a"))

    ordered = MigrationSet.ordered((later, earlier))
    assert ordered.ids == (MigrationId(MIG_2), MigrationId(MIG_1))


def test_independent_migrations_order_by_ulid_deterministically() -> None:
    """Determinism, not merely validity.

    Two independent migrations must always apply in the same order, or the same
    inputs would produce different states on different machines.
    """
    a = _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="a"))
    b = _migration(MIG_2, ChangeOwner(item_id=ITEM, owner="b"))

    assert MigrationSet.ordered((a, b)).ids == MigrationSet.ordered((b, a)).ids
    assert MigrationSet.ordered((b, a)).ids == (MigrationId(MIG_1), MigrationId(MIG_2))


def test_a_cycle_names_the_actual_cycle() -> None:
    """'A cycle exists' sends the reader hunting; the path does not."""
    a = _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="a"), depends_on=(MIG_2,))
    b = _migration(MIG_2, ChangeOwner(item_id=ITEM, owner="b"), depends_on=(MIG_1,))

    with pytest.raises(MigrationCycleError) as exc:
        MigrationSet.ordered((a, b))

    rendered = str(exc.value)
    assert MIG_1 in rendered
    assert MIG_2 in rendered
    assert "->" in rendered


def test_a_missing_dependency_is_rejected() -> None:
    orphan = _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="a"), depends_on=(MIG_2,))
    with pytest.raises(MigrationDependencyMissingError):
        MigrationSet.ordered((orphan,))


def test_a_duplicate_migration_id_is_rejected() -> None:
    a = _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="a"))
    b = _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="b"))
    with pytest.raises(MigrationError, match="Duplicate migration id"):
        MigrationSet.ordered((a, b))


def test_a_migration_cannot_depend_on_itself() -> None:
    with pytest.raises(MigrationError, match="depends on itself"):
        _migration(MIG_1, ChangeOwner(item_id=ITEM, owner="a"), depends_on=(MIG_1,))


def test_a_migration_must_have_operations() -> None:
    with pytest.raises(MigrationError, match="no operations"):
        _migration(MIG_1)


# -- Other operations ------------------------------------------------------


def test_deprecating_records_the_successor_relation() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    successor = ItemId("architecture.auth-policy-v2")
    engine.apply(
        writer,
        PROJECT,
        MigrationSet.ordered(
            (
                _migration(
                    MIG_2,
                    DeprecateItem(item_id=ITEM, reason="Replaced", superseded_by=successor),
                ),
            )
        ),
    )

    item = writer.get_item(PROJECT, ITEM)
    assert item is not None
    assert item.status is KnowledgeStatus.DEPRECATED
    assert any(
        r.source_item_id == successor
        and r.relation_type is RelationType.SUPERSEDES
        and r.target_item_id == ITEM
        for r in writer.relations
    )


def test_changing_owner_and_sensitivity_updates_the_item() -> None:
    from theurian.domain.migration import ChangeSensitivity

    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    engine.apply(
        writer,
        PROJECT,
        MigrationSet.ordered(
            (
                _migration(
                    MIG_2,
                    ChangeOwner(item_id=ITEM, owner="security-team"),
                    ChangeSensitivity(
                        item_id=ITEM,
                        sensitivity=Sensitivity.CONFIDENTIAL,
                        reason="Contains incident detail",
                    ),
                ),
            )
        ),
    )

    item = writer.get_item(PROJECT, ITEM)
    assert item is not None
    assert item.owner == "security-team"
    assert item.sensitivity is Sensitivity.CONFIDENTIAL


def test_operations_on_an_unknown_item_are_rejected() -> None:
    writer = InMemoryWriter()
    with pytest.raises(MigrationError, match="unknown item"):
        _engine().apply(
            writer,
            PROJECT,
            MigrationSet.ordered((_migration(MIG_1, ChangeOwner(item_id=ITEM, owner="x")),)),
        )


def test_aliases_are_recorded() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    old = ItemId("architecture.old-auth")
    engine.apply(
        writer,
        PROJECT,
        MigrationSet.ordered((_migration(MIG_2, AddAlias(alias=old, item_id=ITEM)),)),
    )
    assert writer.aliases[(PROJECT.value, old.value)].item_id == ITEM


def test_relations_are_deduplicated() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    target = ItemId("domain.pricing")
    relation = AddRelation(
        source_item_id=ITEM, relation_type=RelationType.RELATED_TO, target_item_id=target
    )
    engine.apply(writer, PROJECT, MigrationSet.ordered((_migration(MIG_2, relation, relation),)))

    matching = [r for r in writer.relations if r.target_item_id == target]
    assert len(matching) == 1


# -- Planning --------------------------------------------------------------


def test_plan_reports_pending_without_applying() -> None:
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))

    plan = _engine(BODY_V1).plan(writer, PROJECT, migrations)

    assert len(plan.pending) == 1
    assert plan.already_applied == ()
    assert plan.total == 1
    assert not plan.is_empty
    assert writer.revisions == {}, "plan() must not write"


def test_plan_is_empty_once_everything_is_applied() -> None:
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    _engine(BODY_V1).apply(writer, PROJECT, migrations)

    plan = _engine(BODY_V1).plan(writer, PROJECT, migrations)
    assert plan.is_empty
    assert len(plan.already_applied) == 1


# -- Issue #63: scope fields nothing can yet enforce are refused -----------
#
# `RevisionMetadataSpec.tenant_id` and `.acl_group` are kept by the schema
# because they describe the hosted deployment's shape (ADR-0003), but no
# `AuthorizationProvider` is implemented anywhere in this tree. A revision
# naming a non-default value would read as a security boundary while nothing
# checks it, so it is refused at write time instead.


def test_a_revision_naming_a_tenant_other_than_local_is_refused() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="acme-corp")),)
    )

    with pytest.raises(UnenforceableScopeError, match="issue #63"):
        engine.apply(writer, PROJECT, migrations)

    assert writer.revisions == {}, "a refused migration must write nothing"


def test_a_revision_naming_an_acl_group_other_than_default_is_refused() -> None:
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(acl_group="engineering")),)
    )

    with pytest.raises(UnenforceableScopeError, match="issue #63"):
        engine.apply(writer, PROJECT, migrations)

    assert writer.revisions == {}, "a refused migration must write nothing"


def test_the_default_tenant_and_acl_group_still_apply_cleanly() -> None:
    """Negative control: the refusal targets non-default values, not scope
    as a whole. Without this, the two tests above could pass because
    everything got refused."""
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(
                MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="local", acl_group="default")
            ),
        )
    )

    report = engine.apply(writer, PROJECT, migrations)

    assert report.changed
    item = writer.get_item(PROJECT, ITEM)
    assert item is not None
    assert item.current_revision_id == REV_1


def test_validate_and_apply_agree_on_an_unenforceable_tenant() -> None:
    """Issue #36's class: a statically decidable property must not be checked
    only at apply time. `refuse_unenforceable_scope` is what `migrate
    validate` calls directly, on the loaded `MigrationSet`, with no store and
    no engine. `MigrationEngine.apply` calls the very same function
    internally. A document must be refused by both, with the same message, or
    neither."""
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="acme-corp")),)
    )

    with pytest.raises(UnenforceableScopeError) as validate_exc:
        refuse_unenforceable_scope(migrations)

    with pytest.raises(UnenforceableScopeError) as apply_exc:
        _engine(BODY_V1).apply(InMemoryWriter(), PROJECT, migrations)

    assert str(validate_exc.value) == str(apply_exc.value)
