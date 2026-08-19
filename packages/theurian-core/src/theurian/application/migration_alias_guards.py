"""Alias/item-id collision guard over a migration set (SEC-13, T-21).

A static, whole-set check that no ``addAlias`` key equals the id of an item whose
final status is anything but ``deprecated``. Pure function of a ``MigrationSet``:
it touches no store, opens no transaction, and reads nothing from disk.
``MigrationEngine.apply`` calls :func:`refuse_alias_item_id_collision`; ``migrate
validate``/``apply`` call it through the CLI before any state is written, and
``migrate status`` reports it through :func:`alias_item_collision_violations`.

The collision is the T-21 disclosure's write-side cause.
``SqliteCanonicalStore`` resolves an alias before it looks up a status, so an
``addAlias`` key equal to a ``rejected`` item's id resolves to the approved item
the alias points at and clears a relation-visibility gate as that item --
publishing the rejected item's edge and its ``note`` (where the secret that
caused the rejection lives) on the approved item's response. The read side is
fixed independently (``_relation_is_visible`` reads the literally-named row via
``get_item_exact``); this refuses the collision from ever being authored.

Whole-set rather than pending-only, for the reason
:func:`~theurian.application.migration_engine.refuse_unenforceable_scope` is:
``migrate validate`` holds no store and so has no notion of pending, and the two
commands must decide a statically decidable rule on identical input or reopen
issue #36's class. It also covers the cross-set collision without a store read,
because ``migrate apply`` reloads every migration file -- an item created by an
earlier applied migration is in the set the guard sees.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from theurian.domain.enums import KnowledgeStatus
from theurian.domain.errors import AliasItemCollisionError
from theurian.domain.identifiers import ItemId, MigrationId
from theurian.domain.migration import (
    AddAlias,
    CreateItem,
    DeprecateItem,
    MigrationSet,
    RemoveAlias,
    RestoreItem,
    UpsertRevision,
)


def _final_item_statuses(migration_set: MigrationSet) -> dict[ItemId, KnowledgeStatus]:
    """Each item id's status after the whole set, computed in application order.

    "Final" is well defined only in application (topological) order, so this
    normalises to it with ``MigrationSet.ordered`` rather than trusting the
    caller's iteration order: a raw ``MigrationSet(tuple(...))`` whose
    ``deprecateItem`` merely lands last would otherwise read as ``deprecated`` and
    defeat the T-21 exemption. ``ordered`` is idempotent for an already-ordered
    set, so the ordered-loader caller pays only a re-sort, and re-validates a
    cycle/missing-dep an applyable set has already passed.

    Only the four operations that move an item's status are consulted, and the
    same way ``MigrationEngine`` moves it: ``createItem`` sets ``draft`` for a
    *new* id (a re-create of an existing one is a no-op, so ``setdefault``),
    ``upsertRevision`` sets the revision's declared status, ``deprecateItem`` sets
    ``deprecated``, ``restoreItem`` sets ``approved``. ``changeSensitivity`` and
    ``changeOwner`` preserve status (the engine's ``_replace_item`` does), so they
    are not consulted.
    """
    statuses: dict[ItemId, KnowledgeStatus] = {}
    for migration in MigrationSet.ordered(migration_set.migrations):
        for operation in migration.operations:
            match operation:
                case CreateItem():
                    statuses.setdefault(operation.item_id, KnowledgeStatus.DRAFT)
                case UpsertRevision():
                    statuses[operation.item_id] = operation.metadata.status
                case DeprecateItem():
                    statuses[operation.item_id] = KnowledgeStatus.DEPRECATED
                case RestoreItem():
                    statuses[operation.item_id] = KnowledgeStatus.APPROVED
    return statuses


@dataclass(frozen=True, slots=True)
class _AliasTarget:
    """Where a live alias key points, and the migration that set it."""

    item_id: ItemId
    migration_id: MigrationId


def _final_alias_targets(migration_set: MigrationSet) -> dict[ItemId, _AliasTarget]:
    """Each alias key still live after the whole set, and where it was set.

    Normalised to application order via ``MigrationSet.ordered`` for the same
    reason :func:`_final_item_statuses` is: whether a key added then removed on
    two migrations is still live, and which migration id a refusal names, are
    last-write-wins over application order, not over the caller's tuple order.

    ``addAlias`` records a key; ``removeAlias`` takes one back, so a key added and
    later removed is not a live collision and is dropped. The migration id kept is
    the ``addAlias``'s own, so a refusal names the operation an author edits.
    """
    targets: dict[ItemId, _AliasTarget] = {}
    for migration in MigrationSet.ordered(migration_set.migrations):
        for operation in migration.operations:
            match operation:
                case AddAlias():
                    targets[operation.alias] = _AliasTarget(
                        operation.item_id, migration.migration_id
                    )
                case RemoveAlias():
                    targets.pop(operation.alias, None)
    return targets


@dataclass(frozen=True, slots=True)
class _AliasCollision:
    """One alias key that also names a live, non-deprecated item."""

    alias: ItemId
    target: ItemId
    status: KnowledgeStatus
    migration_id: MigrationId


def _alias_item_collisions(migration_set: MigrationSet) -> Iterator[_AliasCollision]:
    """Every live alias key that also names an item whose final status is not deprecated.

    Both collision directions are one predicate: the shape is always "an alias key
    equals an item id", whether the ``addAlias`` was authored over an existing
    item or a ``createItem`` later took an id an alias already keyed. Yielded in
    sorted alias-key order, so the throwing guard refuses a deterministic first
    one when a set carries several.
    """
    statuses = _final_item_statuses(migration_set)
    targets = _final_alias_targets(migration_set)
    for alias in sorted(targets, key=lambda a: a.value):
        status = statuses.get(alias)
        if status is not None and status is not KnowledgeStatus.DEPRECATED:
            target = targets[alias]
            yield _AliasCollision(
                alias=alias,
                target=target.item_id,
                status=status,
                migration_id=target.migration_id,
            )


def refuse_alias_item_id_collision(migration_set: MigrationSet) -> None:
    """Refuse a set whose alias key collides with a live, non-deprecated item id (T-21).

    Raises:
        AliasItemCollisionError: On the first colliding alias key in sorted order.
    """
    for collision in _alias_item_collisions(migration_set):
        raise AliasItemCollisionError(
            alias=collision.alias,
            alias_target=collision.target,
            item_status=collision.status.value,
            migration_id=collision.migration_id,
        )


def alias_item_collision_violations(migration_set: MigrationSet) -> tuple[MigrationId, ...]:
    """Every migration :func:`refuse_alias_item_id_collision` would refuse, without raising.

    The non-throwing enumerator ``migrate status`` needs, the sibling of
    ``unenforceable_scope_violations`` and ``duplicate_content_file_violations``.
    ``status`` reports rather than gates (issue #63's MEDIUM-3), so this
    statically decidable rule must stay visible there under ``refusedIds`` --
    otherwise ``status`` would report ``refusedIds: []`` for a set
    ``validate``/``apply`` exit 4 on, the #210 gap. Reports each colliding
    ``addAlias``'s migration id, at most once, in sorted alias-key order.
    """
    refused: list[MigrationId] = []
    for collision in _alias_item_collisions(migration_set):
        if collision.migration_id not in refused:
            refused.append(collision.migration_id)
    return tuple(refused)


__all__ = [
    "alias_item_collision_violations",
    "refuse_alias_item_id_collision",
]
