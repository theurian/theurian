"""Migration engine guarantees (ADR-0005, ADR-0006).

The four properties that make the canonical store trustworthy: an applied
migration is frozen, conflicts are reported rather than merged, re-application
is a no-op, and ordering is deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes import FrozenClock, InMemoryWriter

from theurian.application.migration_alias_guards import refuse_alias_item_id_collision
from theurian.application.migration_body_guards import (
    duplicate_content_file_violations,
    refuse_duplicate_content_files,
)
from theurian.application.migration_engine import (
    ApplyReport,
    MigrationEngine,
    refuse_unenforceable_scope,
    revisions_to_purge,
    run_static_migration_guards,
    unenforceable_scope_violations,
    verify_no_applied_migration_changed,
    verify_no_applied_migration_removed,
)
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import (
    AliasItemCollisionError,
    DuplicateContentFileError,
    InvariantViolationError,
    MigrationChecksumMismatchError,
    MigrationCycleError,
    MigrationDependencyMissingError,
    MigrationError,
    MigrationHistoryMissingError,
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
    RestoreItem,
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


#: The disclosure flavor of a build made under the shipped default ceiling: every
#: level, so nothing is withheld on that axis and a case that is not about the
#: ceiling reduces exactly as it did before the axis existed (#119).
EVERY_SENSITIVITY = frozenset(Sensitivity)

#: What a build made under a declared ``internal`` ceiling holds -- the flavor a
#: reclassification to ``confidential`` or ``restricted`` falls outside of.
UP_TO_INTERNAL = frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL})

ITEM = ItemId("architecture.auth-policy")
REV_1 = RevisionId("01K1REV00101234567890ABCDE")
REV_2 = RevisionId("01K1REV00201234567890ABCDE")
MIG_1 = "01K1AAAAAA01234567890ABCDE"
MIG_2 = "01K1BBBBBB01234567890ABCDE"
MIG_3 = "01K1CCCCCC01234567890ABCDE"


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


def test_a_revision_id_given_to_a_second_item_is_refused() -> None:
    """The engine's half of INV-2: no item may adopt another item's revision.

    `KnowledgeItem.with_revision` already refuses to point an item at a revision
    of another -- but the revision it is handed here is honest, built from *this*
    operation's `itemId`, so that check passes and the disagreement exists only
    between the operation and the writer's stored row. The refusal therefore has
    to come from the writer, and the engine has to let it out rather than
    swallowing it as an append it had already made.

    Nothing rolls back here -- this writer has no transaction -- so the state
    afterwards is only asserted as far as that allows: the stored row still
    belongs to the item that created it. What the transaction guarantees is
    pinned against a real one in
    ``integration/test_revision_id_reuse.py``.
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    other = ItemId("architecture.caching-policy")

    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    with pytest.raises(InvariantViolationError) as caught:
        engine.apply(
            writer,
            PROJECT,
            MigrationSet.ordered(
                (
                    _create_and_upsert(MIG_1, REV_1, BODY_V1),
                    _migration(
                        MIG_2,
                        CreateItem(
                            item_id=other,
                            kind_=KnowledgeKind.ARCHITECTURE,
                            namespace="backend",
                            owner="platform-team",
                        ),
                        UpsertRevision(
                            item_id=other,
                            revision_id=REV_1,
                            content_file_path="../knowledge/a.md",
                            metadata=_metadata(),
                            content_sha256=ContentHash.of_text(BODY_V1),
                        ),
                    ),
                )
            ),
        )

    assert REV_1.value in str(caught.value)
    assert writer.revisions[REV_1.value].item_id == ITEM, "the stored row keeps its owner"


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


def test_deleting_an_applied_migration_is_detected() -> None:
    """Issue #116: the reverse direction the checksum trail cannot see.

    ``recorded`` holds a migration whose file is gone -- the case a deletion
    leaves in the previously active history. The forward check iterates the files
    that remain and never reaches it; this one iterates the recorded history and
    names it.
    """
    remaining = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    recorded = {
        MigrationId(MIG_1): remaining.migrations[0].checksum.value,
        MigrationId(MIG_2): ContentHash.of_text("gone").value,
    }

    # The forward check is blind to it: MIG_2 is not among the files it iterates.
    verify_no_applied_migration_changed(recorded, remaining)

    with pytest.raises(MigrationHistoryMissingError) as exc:
        verify_no_applied_migration_removed(recorded, remaining)
    assert exc.value.migration_id == MigrationId(MIG_2)
    assert "never be deleted" in str(exc.value)


def test_the_first_missing_migration_in_application_order_is_named() -> None:
    """Determinism: `recorded` arrives ORDER BY sequence, so the earliest gone.

    A dict preserves insertion order, and `applied_migrations` inserts in
    application order, so naming the first-iterated missing id names the
    earliest-applied one -- the migration a reader restores first.
    """
    remaining = MigrationSet.ordered(())
    recorded = {
        MigrationId(MIG_2): ContentHash.of_text("second-gone").value,
        MigrationId(MIG_3): ContentHash.of_text("third-gone").value,
    }

    with pytest.raises(MigrationHistoryMissingError) as exc:
        verify_no_applied_migration_removed(recorded, remaining)
    assert exc.value.migration_id == MigrationId(MIG_2)


def test_a_present_pending_migration_is_not_a_removal() -> None:
    """The honest opposite: a file present but not yet recorded must pass.

    A pending migration is present-in-set-but-absent-from-history -- the exact
    inverse of a deletion -- and confusing it for tampering would refuse every
    ordinary `migrate apply` that adds a migration.
    """
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    verify_no_applied_migration_removed({}, migrations)


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


# -- ADR-0024 decision 5: what a withdrawal tells a still-published index ----


def _purged(
    report: ApplyReport,
    *,
    indexes_unapproved: bool = True,
    indexed_sensitivities: frozenset[Sensitivity] = EVERY_SENSITIVITY,
) -> list[str]:
    """The revisions the report's candidates reduce to at a given index flavor.

    The engine gathers candidates (final item states); the purge reduces them
    against the published index's flavor, which has two axes. These cases are
    flavor-independent on both -- deprecated/rejected are withheld at every
    ``indexesUnapproved``, a restored item at none, and none of them moves an
    item's disclosure class -- so both parameters default to the widest value that
    withholds nothing on its own. The status split is exercised by
    :func:`test_a_reject_in_place_to_draft_is_withdrawn_only_from_a_default_index`
    and the sensitivity split by
    :func:`test_a_reclassification_is_a_withdrawal_only_past_the_builds_own_ceiling`.
    """
    return revisions_to_purge(
        report.withdrawn_candidates,
        indexes_unapproved=indexes_unapproved,
        indexed_sensitivities=indexed_sensitivities,
    )


def test_a_deprecation_reports_the_items_whole_history_as_withdrawn() -> None:
    """Retiring an item withdraws every revision it ever had.

    A published index built from *any* of them must stop holding it, and the
    engine cannot know which build is live, so it names all of them. The purge
    ignores an id no chunk carries, which is what makes naming the whole history
    safe rather than wasteful (ADR-0024 decision 5).
    """
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

    retire = MigrationSet.ordered(
        (_migration(MIG_3, DeprecateItem(item_id=ITEM, reason="Retired")),)
    )
    report = engine.apply(writer, PROJECT, retire)

    assert sorted(_purged(report)) == sorted((REV_1.value, REV_2.value))


def test_a_supersede_reports_the_revision_it_replaced_as_withdrawn() -> None:
    """A redaction leaves the prior revision holding the pre-redaction text.

    The item stays surfaceable, so no gate withholds it; the only thing between
    the old text and a caller is a purge of the revision the supersede moved
    ``currentRevisionId`` off (ADR-0024 decision 5, DECISION 2).
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1, BODY_V2)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    redact = MigrationSet.ordered(
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
    )
    report = engine.apply(writer, PROJECT, redact)

    # The revision the supersede left behind, and not the one it created: the new
    # revision's chunks are the redacted text and must survive.
    assert _purged(report) == [REV_1.value]


def test_an_initial_revision_withdraws_nothing() -> None:
    """The first revision of an item supersedes nothing, so nothing is withdrawn."""
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    report = engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    assert _purged(report) == []


def test_a_reapplied_withdrawal_withdraws_nothing() -> None:
    """Idempotence reaches the withdrawal record too.

    A migration already applied is skipped, so re-running the set reports no
    withdrawn revisions -- a purge on every command that changes nothing is work
    with no cause (FR-K8).
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    setup = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1),
            _migration(MIG_2, DeprecateItem(item_id=ITEM, reason="Retired")),
        )
    )
    engine.apply(writer, PROJECT, setup)
    second = engine.apply(writer, PROJECT, setup)

    assert _purged(second) == []


def test_a_reject_in_place_withdraws_the_item_though_its_revision_id_never_moved() -> None:
    """The third withdrawal verb (ADR-0024 decision 5): reject in place.

    An ``upsertRevision`` that reuses the current revision id and changes only its
    status to ``rejected`` makes the item non-surfaceable -- ``with_revision``
    adopts the status, ``may_surface`` then refuses it -- while its revision id
    never moves. A withdrawn set keyed on "the current revision id changed" misses
    this entirely. Reading final canonical state does not: the item's status is
    non-surfaceable, so every revision it holds -- the one whose chunks are indexed
    included -- is named.
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    reject = MigrationSet.ordered(
        (
            _migration(
                MIG_2,
                UpsertRevision(
                    item_id=ITEM,
                    revision_id=REV_1,
                    content_file_path="../knowledge/a.md",
                    metadata=_metadata(status=KnowledgeStatus.REJECTED),
                    content_sha256=ContentHash.of_text(BODY_V1),
                ),
            ),
        )
    )
    report = engine.apply(writer, PROJECT, reject)

    item = writer.get_item(PROJECT, ITEM)
    assert item is not None and item.status is KnowledgeStatus.REJECTED
    assert _purged(report) == [REV_1.value]


@pytest.mark.parametrize("draft_status", [KnowledgeStatus.DRAFT, KnowledgeStatus.PROPOSED])
def test_a_reject_in_place_to_draft_is_withdrawn_only_from_a_default_index(
    draft_status: KnowledgeStatus,
) -> None:
    """The flavor split (ADR-0024 decision 5, r3), decided at the reduction.

    An ``upsertRevision`` reusing the current revision id and changing an approved
    item's status to ``draft`` (or ``proposed``) makes it non-surfaceable to a
    *default* reader while ``may_surface`` still passes it under
    ``include_unapproved``. So the same candidate must be withdrawn from a default
    index -- whose only chunks are approved, and now hold a chunk no default reader
    may see, moving visible-row rankings (T-17a) -- and **kept** in an
    ``--include-unapproved`` index, which was told to hold drafts. The engine
    gathers one candidate; the flavor decides.
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )

    to_draft = MigrationSet.ordered(
        (
            _migration(
                MIG_2,
                UpsertRevision(
                    item_id=ITEM,
                    revision_id=REV_1,
                    content_file_path="../knowledge/a.md",
                    metadata=_metadata(status=draft_status),
                    content_sha256=ContentHash.of_text(BODY_V1),
                ),
            ),
        )
    )
    report = engine.apply(writer, PROJECT, to_draft)

    assert _purged(report, indexes_unapproved=False) == [REV_1.value], (
        "a default index holds only approved chunks, so the now-unapproved current "
        "revision is withheld from it and must be purged"
    )
    assert _purged(report, indexes_unapproved=True) == [], (
        "an --include-unapproved index legitimately holds this draft/proposal, so it "
        "survives -- a uniform False would wrongly delete it"
    )


def test_a_restore_cancels_a_deprecation_so_the_replay_withdraws_nothing() -> None:
    """The shape a replay takes, and the bug an operation log has in it.

    ``migrate apply`` re-applies the whole set when the state hash shifts
    (ADR-0016), so a project whose history is create -> deprecate -> restore
    replays all three on the next unrelated apply. An operation log re-adds the
    deprecation's revision on every replay and never cancels it, deleting the
    restored -- and now ``approved`` -- item from the index. Reading the final
    state instead: the item is surfaceable and its only revision is current, so
    nothing is withdrawn.
    """
    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    whole_history = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1),
            _migration(MIG_2, DeprecateItem(item_id=ITEM, reason="pulled")),
            _migration(MIG_3, RestoreItem(item_id=ITEM)),
        )
    )
    report = engine.apply(writer, PROJECT, whole_history)

    item = writer.get_item(PROJECT, ITEM)
    assert item is not None and item.status is KnowledgeStatus.APPROVED
    assert _purged(report) == [], "a restored item is visible, not withdrawn"


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


def _reclassified(level: Sensitivity) -> ApplyReport:
    """One item created, then moved to ``level`` by a second apply.

    Two applies rather than one, so the report under test carries *only* the
    reclassification -- the create's own candidate would otherwise be in it and
    every assertion below would be about two items.
    """
    from theurian.domain.migration import ChangeSensitivity

    writer = InMemoryWriter()
    engine = _engine(BODY_V1)
    engine.apply(
        writer, PROJECT, MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    )
    return engine.apply(
        writer,
        PROJECT,
        MigrationSet.ordered(
            (
                _migration(
                    MIG_2,
                    ChangeSensitivity(
                        item_id=ITEM, sensitivity=level, reason="Contains incident detail"
                    ),
                ),
            )
        ),
    )


def test_a_reclassification_is_a_withdrawal_only_past_the_builds_own_ceiling() -> None:
    """ADR-0025 part 2: a ``changeSensitivity`` reaches the purge, and the flavor decides.

    This reverses a recorded decision, and the reversal is the point. The engine
    used to leave ``changeSensitivity`` out of the affected set on the ground that
    a reclassification withholds nothing -- its status and current revision are
    unchanged -- and that the built index's stale ``sensitivity`` column "is read
    by no gate". #119 phases 3 and 4 made the second half false: a build now writes
    no row above the deployment's ceiling and every retriever filters on the
    column, so a *reclassified* row is the only above-ceiling row a served build
    can hold, and its text is still in four FTS5 tables pricing every visible row
    against it (T-17a on this axis).

    Both directions are asserted from one report, because the operation is the same
    one and only the *build's* recorded flavor differs. Against a build made under
    the shipped default every level is held, so a move to ``confidential`` removes
    nothing and no index is copied -- which is what lets the operation join the
    candidate set unconditionally instead of the engine having to know a ceiling it
    cannot see. Against a build made at ``internal`` the same move is outside what
    that build was allowed to write, and every revision of the item goes.

    Removing ``ChangeSensitivity`` from ``_withdrawal_affected_item`` turns the
    second assertion red; dropping the ``may_disclose`` term from
    ``revisions_to_purge`` turns the first one red.
    """
    report = _reclassified(Sensitivity.CONFIDENTIAL)

    assert report.applied == [MigrationId(MIG_2)], "the reclassification must have applied"
    assert [candidate.sensitivity for candidate in report.withdrawn_candidates] == [
        Sensitivity.CONFIDENTIAL
    ], "the candidate must carry the item's *final* class, or the purge judges the old one"
    assert _purged(report, indexed_sensitivities=EVERY_SENSITIVITY) == [], (
        "a build made under the shipped default holds every level, so a move to "
        "`confidential` is still a row it was allowed to write -- purging it would copy "
        "the whole index on every relabelling and delete a row nothing withholds"
    )
    assert _purged(report, indexed_sensitivities=UP_TO_INTERNAL) == [REV_1.value], (
        "an item reclassified above the ceiling its build ran under is a row that build "
        "would not write today, and nothing but the purge can take it out of the file"
    )


def test_a_reclassification_within_the_ceiling_purges_nothing() -> None:
    """The downward and sideways case, pinned apart from the upward one.

    ``internal -> public`` under a build made at ``internal``: the item was
    holdable before and is holdable after, so the reduction is empty and
    ``publish_purge_for_withdrawal`` returns its ``no-withdrawal`` state without
    reading a single index page. Asserted on its own because the upward test's
    allow-all half varies the *build* while this varies the *item*, and a
    reduction that keyed on "any reclassification at all" would pass that one.

    The **downward** residual this cannot fix is recorded in ADR-0025 and pinned
    end to end in ``test_sensitivity_purge.py``: an item moving *below* the
    ceiling is a row the build never wrote, and a purge copies a build and deletes
    from the copy -- there is nothing to add. It stays absent until the next
    ``index build``, failing toward fewer results.
    """
    report = _reclassified(Sensitivity.PUBLIC)

    assert _purged(report, indexed_sensitivities=UP_TO_INTERNAL) == [], (
        "a reclassification that stays within the build's ceiling withheld a revision "
        "the build is still allowed to hold"
    )


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


# -- Issue #63: scope fields nothing routes on are refused ----------------
#
# `RevisionMetadataSpec.tenant_id` and `.acl_group` are kept by the schema
# because they describe the hosted deployment's shape (ADR-0003), and nothing
# routes on either. The `AuthorizationProvider` implementation #119 shipped
# answers a deployment serving profile -- one tenant, one ACL group, a
# sensitivity ceiling -- so a revision naming a non-default value would read as
# a security boundary while nothing checks it, and it is refused at write time
# instead. That refusal is one of the three things carrying #119's degenerate
# discharge of these two axes (ADR-0025); the others are `_resolve`'s
# tenant-boundary refusal and, in `test_authorization_provider.py`,
# `test_tenant_and_acl_group_are_the_values_write_time_already_refuses`, which
# binds the provider's grant to the very constants these tests depart from.


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
    """A smoke check that both call sites reach the same function, not a
    substitute for pinning the CLI wiring.

    This cannot fail on its own: `MigrationEngine.apply`'s first statement is
    the very call to `refuse_unenforceable_scope` this test also makes
    directly, on the same `MigrationSet`, and the function is deterministic.
    Deleting `migrate validate`'s call to it in `cli/commands.py`, or its
    dedicated `except` clause in `migrate apply`, leaves this test green,
    because neither line of `commands.py` ever runs here (issue #63's
    MEDIUM-2). The test that actually exercises that wiring, and goes RED
    under both deletions, is
    `test_validate_and_apply_refuse_an_unenforceable_tenant_identically` in
    `tests/integration/test_cli_commands.py`.
    """
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="acme-corp")),)
    )

    with pytest.raises(UnenforceableScopeError) as validate_exc:
        refuse_unenforceable_scope(migrations)

    with pytest.raises(UnenforceableScopeError) as apply_exc:
        _engine(BODY_V1).apply(InMemoryWriter(), PROJECT, migrations)

    assert str(validate_exc.value) == str(apply_exc.value)


def test_a_violation_in_the_second_migration_of_a_set_is_still_caught() -> None:
    """The loop must cover every migration, not stop after the first."""
    writer = InMemoryWriter()
    engine = _engine(BODY_V1, BODY_V2)
    clean = _create_and_upsert(MIG_1, REV_1, BODY_V1)
    violating = _migration(
        MIG_2,
        UpsertRevision(
            item_id=ITEM,
            revision_id=REV_2,
            content_file_path="../knowledge/a.md",
            metadata=_metadata(tenant_id="acme-corp"),
            expected_revision=REV_1,
            content_sha256=ContentHash.of_text(BODY_V2),
        ),
    )
    migrations = MigrationSet.ordered((clean, violating))
    assert migrations.ids == (MigrationId(MIG_1), MigrationId(MIG_2)), "fixture must be ordered"

    with pytest.raises(UnenforceableScopeError):
        engine.apply(writer, PROJECT, migrations)


def test_an_already_applied_revision_with_a_foreign_tenant_is_still_refused() -> None:
    """Whole-set checking is a recorded decision, not merely "checks pending":
    a migration already applied under an earlier build, before this refusal
    existed, must still be caught on the next run -- against a `writer` that
    already recorded it, with nothing left pending -- or the false boundary
    persists silently forever in exactly the stores issue #63 was filed
    against (this is HIGH-1's upgrade scenario, reproduced at the engine
    level: `record_migration` stands in for an apply an older, unrefusing
    build already performed).
    """
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="acme-corp")),)
    )
    writer.record_migration(
        PROJECT, MigrationId(MIG_1), migrations.migrations[0].checksum.value, NOW
    )

    plan = _engine(BODY_V1).plan(writer, PROJECT, migrations)
    assert plan.is_empty, "fixture must have nothing pending -- the violation is in applied only"

    with pytest.raises(UnenforceableScopeError):
        _engine(BODY_V1).apply(writer, PROJECT, migrations)


def test_a_tampered_checksum_against_the_current_database_outranks_scope() -> None:
    """Checksum tamper-evidence (FR-K5) takes priority over the scope refusal
    at every layer this engine can see, matching `_verify_history`'s
    precedence at the CLI layer against the *previously* active database
    (issue #63's HIGH-3). A reader who sees `MigrationChecksumMismatchError`
    should never have to wonder whether a hidden scope problem is the real
    reason their edit went unreported."""
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="acme-corp")),)
    )
    # A checksum recorded for this migration id that does not match the file
    # loaded now -- both conditions hold at once: a tampered checksum *and* a
    # foreign tenant.
    writer.record_migration(PROJECT, MigrationId(MIG_1), "0" * 64, NOW)

    with pytest.raises(MigrationChecksumMismatchError):
        _engine(BODY_V1).apply(writer, PROJECT, migrations)


@pytest.mark.parametrize(
    "tenant_id", ["Local", "LOCAL", "loc", "locally", "local ", " local", "acme-corp"]
)
def test_only_the_exact_default_tenant_is_accepted(tenant_id: str) -> None:
    """Pins exact string comparison: no case-folding, no prefix or substring
    match, no trimming. Every one of these is refused exactly as
    `'acme-corp'` is."""
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id=tenant_id)),)
    )
    with pytest.raises(UnenforceableScopeError):
        _engine(BODY_V1).apply(writer, PROJECT, migrations)


@pytest.mark.parametrize(
    "acl_group", ["Default", "DEFAULT", "def", "defaults", "default ", " default", "engineering"]
)
def test_only_the_exact_default_acl_group_is_accepted(acl_group: str) -> None:
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(acl_group=acl_group)),)
    )
    with pytest.raises(UnenforceableScopeError):
        _engine(BODY_V1).apply(writer, PROJECT, migrations)


def test_the_error_names_the_field_and_the_offending_value() -> None:
    """A message that only says "scope refused" sends the reader back into
    the migration file to find out which field. Both must be named."""
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (_create_and_upsert(MIG_1, REV_1, BODY_V1, metadata=_metadata(tenant_id="acme-corp")),)
    )

    with pytest.raises(UnenforceableScopeError) as exc:
        _engine(BODY_V1).apply(writer, PROJECT, migrations)

    assert "tenantId" in str(exc.value)
    assert "acme-corp" in str(exc.value)


def test_both_violating_fields_are_named_in_one_error() -> None:
    """A revision naming both a foreign tenant and a foreign ACL group is one
    problem statement, not two errors discovered one `migrate validate` at a
    time -- the first `apply` should already say both."""
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(
                MIG_1,
                REV_1,
                BODY_V1,
                metadata=_metadata(tenant_id="acme-corp", acl_group="engineering"),
            ),
        )
    )

    with pytest.raises(UnenforceableScopeError) as exc:
        _engine(BODY_V1).apply(writer, PROJECT, migrations)

    message = str(exc.value)
    assert "tenantId" in message
    assert "acme-corp" in message
    assert "aclGroup" in message
    assert "engineering" in message
    assert len(exc.value.violations) == 2


def test_unenforceable_scope_error_is_a_migration_error() -> None:
    """CLI error handling and any future caller that catches the broader
    `MigrationError` family must still see this one -- it is not a new,
    disconnected error type."""
    assert issubclass(UnenforceableScopeError, MigrationError)


# -- Issue #63's third consumer: `migrate status` never raises -------------


def test_status_reports_every_refused_migration_without_raising() -> None:
    clean = _create_and_upsert(MIG_1, REV_1, BODY_V1)
    violating = _migration(
        MIG_2,
        UpsertRevision(
            item_id=ITEM,
            revision_id=REV_2,
            content_file_path="../knowledge/a.md",
            metadata=_metadata(tenant_id="acme-corp"),
            expected_revision=REV_1,
            content_sha256=ContentHash.of_text(BODY_V2),
        ),
    )
    migrations = MigrationSet.ordered((clean, violating))

    refused = unenforceable_scope_violations(migrations)

    assert refused == (MigrationId(MIG_2),)


def test_status_reports_no_refused_ids_when_the_set_is_clean() -> None:
    migrations = MigrationSet.ordered((_create_and_upsert(MIG_1, REV_1, BODY_V1),))
    assert unenforceable_scope_violations(migrations) == ()


# -- Issue #210: one body file backs one revision --------------------------

#: Two synthetic filesystem identities, ``(st_dev, st_ino)``. The loader takes
#: these from a real ``stat``; here they stand in for "same file" and "a
#: different file" so the comparison can be exercised without touching disk.
#: Real-filesystem faces -- hardlinks, case-variant spellings, NFC/NFD -- are
#: proved against the loader in ``test_cli_commands.py``.
IDENTITY_A = (1, 1001)
IDENTITY_B = (1, 1002)


def _upsert(  # noqa: PLR0913 -- a test builder; every field models one axis of the identity check
    revision_id: RevisionId,
    body: str,
    content_file_path: str,
    *,
    identity: tuple[int, int] | None = None,
    resolved: str | None = None,
    expected_revision: RevisionId | None = None,
) -> UpsertRevision:
    return UpsertRevision(
        item_id=ITEM,
        revision_id=revision_id,
        content_file_path=content_file_path,
        resolved_content_path=resolved,
        content_identity=identity,
        metadata=_metadata(),
        expected_revision=expected_revision,
        content_sha256=ContentHash.of_text(body),
    )


def test_a_body_file_backing_two_revisions_is_refused() -> None:
    """Issue #210, face 1, at the layer that decides it.

    Nothing about either migration is wrong on its own: the ids are unique and
    the ``expectedRevision`` chain is correct. What is wrong is that one file
    has to be two versions at once, and the file cannot be -- so the earlier
    revision records whatever the later author wrote there.
    """
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/a.md",
                    identity=IDENTITY_A,
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    with pytest.raises(DuplicateContentFileError) as exc:
        refuse_duplicate_content_files(migrations)

    message = str(exc.value)
    assert MIG_1 in message, "the migration that referenced the body first"
    assert MIG_2 in message, "and the one that referenced it again"
    assert REV_1.value in message, "the first revision, so the reader knows which two"
    assert REV_2.value in message, "and the second"
    assert "../knowledge/a.md" in message, "and the path, or the reader has to go looking"


def test_two_spellings_of_one_file_are_one_identity() -> None:
    """The key is the file's identity, not the path string it was reached by.

    A case-insensitive filesystem reaches one physical file through many
    spellings; ``resolve()`` leaves them distinct strings while ``stat`` returns
    one inode. Two *different* resolved paths that share an identity are one
    file, and a guard keyed on the string reports two references and lets the
    set cross -- the disclosure this re-key closes.
    """
    migrations = MigrationSet.ordered(
        (
            _migration(
                MIG_1,
                _upsert(
                    REV_1, BODY_V1, "../knowledge/note.md", identity=IDENTITY_A, resolved="note.md"
                ),
            ),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/NOTE.MD",
                    identity=IDENTITY_A,
                    resolved="NOTE.MD",
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    with pytest.raises(DuplicateContentFileError):
        refuse_duplicate_content_files(migrations)


def test_two_files_whose_paths_differ_only_by_case_are_not_refused() -> None:
    """The Linux-safety control: the key is identity, never a casefolded path.

    Two *genuinely different* files (distinct inodes) whose paths differ only by
    case coexist on a case-sensitive filesystem. Casefolding the path to close
    the case-variant bypass would false-refuse this legitimate pair; keying on
    ``(st_dev, st_ino)`` does not, because different files have different inodes
    regardless of how their names compare.
    """
    migrations = MigrationSet.ordered(
        (
            _migration(
                MIG_1,
                _upsert(
                    REV_1, BODY_V1, "../knowledge/note.md", identity=IDENTITY_A, resolved="note.md"
                ),
            ),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/NOTE.md",
                    identity=IDENTITY_B,
                    resolved="NOTE.md",
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    refuse_duplicate_content_files(migrations)


def test_a_pair_pinning_one_digest_and_sharing_one_file_is_still_refused() -> None:
    """Agreeing on the digest does not make one file two revisions (issue #210).

    A pin freezes a body against out-of-band edits; it does not make one file
    able to be two revisions. Two revisions recording the *same* digest and
    sharing one file still cannot each be independently attributed -- the hazard
    is the sharing -- so the set is refused, and the reason names why it holds
    even here. Since ADR-0027 every revision pins, so this is the only shape the
    refusal ever meets; it stays a separate case from the differing-digest pair
    above because an identity-keyed guard that compared digests would pass it.
    """
    migrations = MigrationSet.ordered(
        (
            _migration(
                MIG_1,
                _upsert(REV_1, BODY_V1, "../knowledge/a.md", identity=IDENTITY_A),
            ),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V1,
                    "../knowledge/a.md",
                    identity=IDENTITY_A,
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    with pytest.raises(DuplicateContentFileError) as exc:
        refuse_duplicate_content_files(migrations)

    assert "contentSha256" in str(exc.value), "the reason must be true for a pinned pair too"


def test_two_upserts_in_one_migration_sharing_a_file_name_both_revisions() -> None:
    """The single-migration degeneration the old message got wrong (issue #210).

    Two ``upsertRevision`` operations in *one* migration can share a file. The
    old error printed that migration's id twice and named no revision, and said
    "neither migration is wrong on its own" -- false, since one migration carries
    both and is wrong alone. The message must name both revisions so the reader
    can tell the two operations apart within the one file to edit.
    """
    migration = _migration(
        MIG_1,
        _upsert(REV_1, BODY_V1, "../knowledge/a.md", identity=IDENTITY_A),
        _upsert(REV_2, BODY_V2, "../knowledge/a.md", identity=IDENTITY_A),
    )

    with pytest.raises(DuplicateContentFileError) as exc:
        refuse_duplicate_content_files(MigrationSet.ordered((migration,)))

    message = str(exc.value)
    assert REV_1.value in message and REV_2.value in message, "both revisions, not one id twice"
    assert "neither migration is wrong" not in message, "false when it is one migration"


def test_the_refusal_names_both_authored_paths() -> None:
    """Issue #210's message quality: the reader edits the *authored* path.

    When the two spellings differ -- a case variant, a hardlinked second name --
    naming only one leaves the reader guessing which contentFile to change. Both
    authored paths appear; the resolved path is supplementary.
    """
    migrations = MigrationSet.ordered(
        (
            _migration(
                MIG_1,
                _upsert(
                    REV_1,
                    BODY_V1,
                    "../knowledge/note.md",
                    identity=IDENTITY_A,
                    resolved="knowledge/note.md",
                ),
            ),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/NOTE.MD",
                    identity=IDENTITY_A,
                    resolved="knowledge/note.md",
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    with pytest.raises(DuplicateContentFileError) as exc:
        refuse_duplicate_content_files(migrations)

    message = str(exc.value)
    assert "../knowledge/note.md" in message, "the first revision's authored path"
    assert "../knowledge/NOTE.MD" in message, "and the second's, which differs"


def test_an_in_place_status_change_may_re_declare_its_own_body() -> None:
    """The shape the refusal must not break (ADR-0024 decision 5).

    A reject or draft in place re-declares the item's *current* revision,
    changing only ``status``: the revision id does not move, and naming the same
    body file is what keeps ``append_revision`` the no-op FR-K8 requires. Keying
    the refusal on identity alone -- with the revision id ignored -- would refuse
    this, taking the withdrawal purge's ``reject`` and ``inplace-draft`` faces
    with it.
    """
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                UpsertRevision(
                    item_id=ITEM,
                    revision_id=REV_1,
                    content_file_path="../knowledge/a.md",
                    content_identity=IDENTITY_A,
                    metadata=_metadata(status=KnowledgeStatus.REJECTED),
                    content_sha256=ContentHash.of_text(BODY_V1),
                ),
            ),
        )
    )

    refuse_duplicate_content_files(migrations)

    report = _engine(BODY_V1).apply(InMemoryWriter(), PROJECT, migrations)
    assert report.applied == [MigrationId(MIG_1), MigrationId(MIG_2)]


def test_two_revisions_with_their_own_body_files_are_not_refused() -> None:
    """The negative control. Refusing every second `upsertRevision` would pass
    every assertion above while forbidding the ordinary way to revise an item."""
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/b.md",
                    identity=IDENTITY_B,
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    refuse_duplicate_content_files(migrations)

    report = _engine(BODY_V1, BODY_V2).apply(InMemoryWriter(), PROJECT, migrations)
    assert report.applied == [MigrationId(MIG_1), MigrationId(MIG_2)]


def test_apply_refuses_a_shared_body_file_before_writing_anything() -> None:
    """`apply` refuses on its own, not merely because a caller checked first.

    Asserted on the store as well as on the exception: a refusal raised after
    the first migration had been written would leave a half-applied set behind
    on any writer without a transaction to roll back.
    """
    writer = InMemoryWriter()
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/a.md",
                    identity=IDENTITY_A,
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    with pytest.raises(DuplicateContentFileError):
        _engine(BODY_V1, BODY_V2).apply(writer, PROJECT, migrations)

    assert writer.revisions == {}
    assert writer.history == []


def test_an_in_memory_operation_without_an_identity_is_not_compared() -> None:
    """An operation with no ``content_identity`` cannot participate (issue #210).

    The loader always sets the identity from the ``stat`` that read the body, so
    no gate ever sees ``None``; an operation built in memory has no file, and is
    skipped rather than folded back onto the path string it happens to carry.
    Two such operations naming one path do not collide -- otherwise the skip
    would have quietly re-introduced the string key it replaced.
    """
    migrations = MigrationSet.ordered(
        (
            _migration(MIG_1, _upsert(REV_1, BODY_V1, "../knowledge/a.md")),
            _migration(
                MIG_2, _upsert(REV_2, BODY_V2, "../knowledge/a.md", expected_revision=REV_1)
            ),
        )
    )

    refuse_duplicate_content_files(migrations)


def test_status_reports_the_migration_that_shares_a_body_without_raising() -> None:
    """`migrate status` observes, never gates (issue #63's MEDIUM-3), so the
    body-sharing property has to reach it without a raise -- the second, later
    migration, matching the throwing form's culprit and the remedy."""
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/a.md",
                    identity=IDENTITY_A,
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    assert duplicate_content_file_violations(migrations) == (MigrationId(MIG_2),)


def test_status_reports_no_body_sharing_for_a_set_of_distinct_files() -> None:
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/b.md",
                    identity=IDENTITY_B,
                    expected_revision=REV_1,
                ),
            ),
        )
    )

    assert duplicate_content_file_violations(migrations) == ()


def test_duplicate_content_file_error_is_a_migration_error() -> None:
    """CLI error handling catches the broader family, so this must join it."""
    assert issubclass(DuplicateContentFileError, MigrationError)


# -- Issue #210 hardening: the guards' two easily-mutated edges -------------


def test_status_does_not_report_an_in_place_re_declaration_as_body_sharing() -> None:
    """The status enumerator's revision-id guard, driven for the first time.

    An in-place status change re-declares the item's *current* revision id
    against its own body, changing only ``status`` (ADR-0024 decision 5, FR-K8's
    no-op ``append_revision``): the identity matches but the revision id does
    not move, so it is a legitimate re-declaration, not one file backing two
    revisions. The throwing form's exclusion of this shape was pinned
    (``test_an_in_place_status_change_may_re_declare_its_own_body``); the
    non-throwing enumerator ``migrate status`` calls was not. A mutation
    dropping the ``!= operation.revision_id`` guard in
    ``duplicate_content_file_violations`` survived the whole suite, so ``status``
    could begin reporting a re-declaration under ``refusedIds`` -- gating a
    legitimate no-op in the one command whose contract is to observe, not gate --
    with nothing red. This makes that mutation fail.
    """
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=IDENTITY_A),
            _migration(
                MIG_2,
                UpsertRevision(
                    item_id=ITEM,
                    revision_id=REV_1,
                    content_file_path="../knowledge/a.md",
                    content_identity=IDENTITY_A,
                    metadata=_metadata(status=KnowledgeStatus.REJECTED),
                    content_sha256=ContentHash.of_text(BODY_V1),
                ),
            ),
        )
    )

    assert duplicate_content_file_violations(migrations) == ()


def test_the_refusal_compares_the_full_identity_tuple_not_the_inode_alone() -> None:
    """The key is ``(st_dev, st_ino)``, not ``st_ino`` on its own (issue #210).

    An inode number is unique only *within* a filesystem; the same number names
    two different files across two mounts. Keying the refusal on the inode alone
    would false-refuse two genuinely distinct files that happen to share one --
    the mirror of the case-variant false-refuse casefolding a path would cause --
    so the comparison must be over the whole tuple. Two operations with the same
    inode on *different* devices are distinct files and pass; the same device and
    inode are one file and are refused.

    This pins the *guard's* use of the full tuple, at both call sites. The
    *loader's* capture of a real ``st_dev`` -- rather than a constant -- is
    pinned separately by ``test_migration_loader_identity.py``'s equality
    against the body's real ``stat``; what neither exercises is two genuinely
    different real devices sharing an inode number, which needs a multi-device
    mount, and that residual is an accepted LOW.
    """
    same_inode_two_devices = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=(1, 5)),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/b.md",
                    identity=(2, 5),
                    expected_revision=REV_1,
                ),
            ),
        )
    )
    refuse_duplicate_content_files(same_inode_two_devices)
    assert duplicate_content_file_violations(same_inode_two_devices) == ()

    same_device_and_inode = MigrationSet.ordered(
        (
            _create_and_upsert(MIG_1, REV_1, BODY_V1, content_identity=(1, 5)),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/a.md",
                    identity=(1, 5),
                    expected_revision=REV_1,
                ),
            ),
        )
    )
    with pytest.raises(DuplicateContentFileError):
        refuse_duplicate_content_files(same_device_and_inode)
    assert duplicate_content_file_violations(same_device_and_inode) == (MigrationId(MIG_2),)


# -- The static-guard order run_static_migration_guards promises ------------
#
# `run_static_migration_guards` calls its three guards in a fixed order, and its
# docstring calls that order load-bearing: the scope refusal names one migration
# as wrong, while the body-sharing and alias-collision refusals are statements
# about the whole set, so reporting the narrower fault first keeps a reader from
# being sent to a second migration that is not the one to edit. A set that trips
# two guards at once is what pins the order -- reversing the calls changes which
# error surfaces, and nothing else does, so the order-reversed mutation survived
# every test until these.

ITEM_B = ItemId("architecture.other-note")
REV_B = RevisionId("01K1REVBBB01234567890ABCDE")


def _upsert_of(
    item_id: ItemId, revision_id: RevisionId, body: str, identity: tuple[int, int]
) -> UpsertRevision:
    """One ``upsertRevision`` for an arbitrary item, sharing a body identity.

    ``_upsert`` above fixes ``item_id`` to :data:`ITEM`; the alias-collision case
    below needs a *second* live item, so this takes the id explicitly.
    ``_metadata()`` defaults keep tenant and ACL at the enforced values, so the
    only guards a set built from these trips are the ones it is constructed to.
    """
    return UpsertRevision(
        item_id=item_id,
        revision_id=revision_id,
        content_file_path="../knowledge/a.md",
        content_identity=identity,
        metadata=_metadata(),
        content_sha256=ContentHash.of_text(body),
    )


def test_the_scope_refusal_is_reported_before_a_duplicate_body() -> None:
    """Scope precedes duplicate: the narrower fault, named first (the docstring's claim).

    MIG_1 names a foreign tenant *and* shares MIG_2's body file, so the set trips
    the scope guard and the body-sharing guard at once -- both asserted present
    below, so the order is the only thing left deciding which surfaces. The
    docstring promises the scope refusal, which names one migration, ahead of the
    body-sharing one, which is a statement about the set. Reversing those two calls
    surfaces ``DuplicateContentFileError`` here instead, so this pins the first
    edge of the order.
    """
    migrations = MigrationSet.ordered(
        (
            _create_and_upsert(
                MIG_1,
                REV_1,
                BODY_V1,
                metadata=_metadata(tenant_id="acme-corp"),
                content_identity=IDENTITY_A,
            ),
            _migration(
                MIG_2,
                _upsert(
                    REV_2,
                    BODY_V2,
                    "../knowledge/a.md",
                    identity=IDENTITY_A,
                    expected_revision=REV_1,
                ),
            ),
        )
    )
    with pytest.raises(UnenforceableScopeError):
        refuse_unenforceable_scope(migrations)
    with pytest.raises(DuplicateContentFileError):
        refuse_duplicate_content_files(migrations)

    with pytest.raises(UnenforceableScopeError):
        run_static_migration_guards(migrations)


def test_a_duplicate_body_is_reported_before_an_alias_collision() -> None:
    """Duplicate precedes alias: the second edge, pinning the whole order.

    One migration shares a body file across two live items *and* aliases a live
    item id, so it trips the body-sharing guard and the alias-collision guard at
    once -- both asserted present below. ``run_static_migration_guards`` runs the
    body-sharing guard second and the alias-collision guard third, so it raises
    ``DuplicateContentFileError``; reversing those two calls would surface
    ``AliasItemCollisionError`` instead. Together with the test above this pins
    scope < duplicate < alias, the whole order the docstring calls load-bearing.
    """
    migrations = MigrationSet.ordered(
        (
            _migration(
                MIG_1,
                CreateItem(
                    item_id=ITEM,
                    kind_=KnowledgeKind.ARCHITECTURE,
                    namespace="backend",
                    owner="platform-team",
                ),
                _upsert_of(ITEM, REV_1, BODY_V1, IDENTITY_A),
                CreateItem(
                    item_id=ITEM_B,
                    kind_=KnowledgeKind.ARCHITECTURE,
                    namespace="backend",
                    owner="platform-team",
                ),
                _upsert_of(ITEM_B, REV_B, BODY_V2, IDENTITY_A),
                AddAlias(alias=ITEM, item_id=ITEM_B),
            ),
        )
    )
    with pytest.raises(DuplicateContentFileError):
        refuse_duplicate_content_files(migrations)
    with pytest.raises(AliasItemCollisionError):
        refuse_alias_item_id_collision(migrations)

    with pytest.raises(DuplicateContentFileError):
        run_static_migration_guards(migrations)
