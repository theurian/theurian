"""The alias/item-id collision guard classifies by application order (SEC-13, T-21).

:func:`~theurian.application.migration_alias_guards.refuse_alias_item_id_collision`
computes each item's *final* status with last-write-wins over the migration set
and exempts a collision only when that final status is ``deprecated`` -- the one
legitimate ``deprecateItem(old)`` then ``addAlias(old -> new)`` shape. "Final" is
meaningful only in *application* order, and the guard normalises to it with
``MigrationSet.ordered`` rather than trusting the order the caller's
``MigrationSet`` iterates, so its classification does not depend on how the
caller built the set.

A guard that trusted the caller's iteration order would misclassify the
exemption: handed a raw ``MigrationSet(tuple(...))`` in a non-application order,
an item that ends ``rejected`` in application order but whose ``deprecateItem``
merely lands last in the raw tuple would read as ``deprecated`` and be let
through, re-opening the T-21 disclosure the guard exists to close. This test
pins that it does not -- it hands the guard exactly that mis-ordered tuple and
asserts the collision is still refused as ``rejected``.

Pure: no store, no transaction, no disk. The domain objects are built in memory
so the guard's ordering can be attacked directly, which the CLI-level tests in
``tests/integration/test_alias_item_id_collision.py`` cannot do -- they only ever
reach the guard through the ordered loader.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from theurian.application.migration_alias_guards import refuse_alias_item_id_collision
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.errors import AliasItemCollisionError
from theurian.domain.identifiers import ItemId, MigrationId, RevisionId
from theurian.domain.migration import (
    AddAlias,
    CreateItem,
    DeprecateItem,
    Migration,
    MigrationSet,
    Operation,
    RevisionMetadataSpec,
    UpsertRevision,
)
from theurian.domain.values import MARKDOWN, ContentHash

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

#: The id used as *both* a live, non-surfaceable item and an ``addAlias`` key --
#: the T-21 collision. Ends ``rejected`` once the set is in application order.
WITHHELD = ItemId("architecture.withheld")
#: The approved item the alias points at; a lookup for ``WITHHELD`` resolves here.
PUBLISHED = ItemId("architecture.public-note")

# `M_rej` depends on `M_dep`, so application order is fixed by the dependency and
# does not rest on the ULID tie-break. Valid 26-char Crockford base32 (no I/L/O/U).
MIG_DEP = "01K1AAAAAA01234567890ABCDE"
MIG_REJ = "01K1BBBBBB01234567890ABCDE"
REV_WITHHELD = "01K2AAAAAA01234567890ABCDE"
REV_PUBLISHED = "01K2BBBBBB01234567890ABCDE"


def _upsert(item_id: ItemId, revision_id: str, status: KnowledgeStatus) -> UpsertRevision:
    return UpsertRevision(
        item_id=item_id,
        revision_id=RevisionId(revision_id),
        content_file_path="../knowledge/a.md",
        metadata=RevisionMetadataSpec(
            title="Doc",
            content_type=MARKDOWN,
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=status,
            owner="platform-team",
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
        ),
    )


def _migration(
    migration_id: str, *operations: Operation, depends_on: tuple[str, ...] = ()
) -> Migration:
    return Migration(
        migration_id=MigrationId(migration_id),
        created_at=NOW,
        author="engineer@example.com",
        operations=tuple(operations),
        checksum=ContentHash.of_text(migration_id),
        depends_on=tuple(MigrationId(d) for d in depends_on),
        source_path=f"{migration_id}.yaml",
    )


def _deprecate_then_reject() -> tuple[Migration, Migration]:
    """Two migrations whose *application* order makes ``WITHHELD`` end ``rejected``.

    ``M_dep`` deprecates ``WITHHELD`` and creates the approved ``PUBLISHED`` the
    alias points at. ``M_rej`` (which ``dependsOn`` ``M_dep``) re-upserts
    ``WITHHELD`` as ``rejected`` and aliases ``WITHHELD -> PUBLISHED``. Applied in
    order ``M_dep -> M_rej``, ``WITHHELD``'s final status is ``rejected`` -- a live,
    non-deprecated item that an alias key also names, which is the collision. Seen
    in the raw tuple order ``(M_rej, M_dep)`` the ``deprecateItem`` lands last and
    the exemption misfires: that is the ordering fault this drives out.
    """
    dep = _migration(
        MIG_DEP,
        CreateItem(
            item_id=PUBLISHED,
            kind_=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            owner="platform-team",
        ),
        _upsert(PUBLISHED, REV_PUBLISHED, KnowledgeStatus.APPROVED),
        DeprecateItem(item_id=WITHHELD),
    )
    rej = _migration(
        MIG_REJ,
        _upsert(WITHHELD, REV_WITHHELD, KnowledgeStatus.REJECTED),
        AddAlias(alias=WITHHELD, item_id=PUBLISHED),
        depends_on=(MIG_DEP,),
    )
    return dep, rej


def test_the_collision_in_application_order_is_refused() -> None:
    """The collision is genuine: presented in application order, the guard refuses it.

    Companion to the order-independence test below, and what gives it meaning --
    the raw-order case must reach *this* refusal, not a spurious one.
    ``MigrationSet.ordered`` sorts ``(rej, dep)`` into application order
    ``(dep, rej)``, so ``WITHHELD`` ends ``rejected`` and the guard raises naming
    that status and the migration carrying the ``addAlias``. GREEN on HEAD.
    """
    dep, rej = _deprecate_then_reject()
    ordered = MigrationSet.ordered((rej, dep))

    with pytest.raises(AliasItemCollisionError) as caught:
        refuse_alias_item_id_collision(ordered)

    assert caught.value.item_status == "rejected", (
        "the status application order produces for WITHHELD"
    )
    assert caught.value.migration_id == MigrationId(MIG_REJ), "the migration carrying the addAlias"


def test_a_collision_is_refused_regardless_of_raw_migration_set_iteration_order() -> None:
    """The guard must classify by application order, not by tuple iteration order (T-21).

    The exact same two migrations as the test above, handed to a *raw*
    ``MigrationSet((rej, dep))`` whose iteration order is not application order.
    Application order makes ``WITHHELD`` end ``rejected`` -- a live, non-deprecated
    item an alias key also names -- so the guard MUST refuse; the raw order puts
    ``deprecateItem`` last, and a guard that trusts iteration order sees
    ``deprecated`` and wrongly exempts the collision, letting the rejected item's
    note surface under the approved id the alias points at.

    ``item_status == "rejected"`` is the load-bearing assertion: a refusal alone
    could be produced by a guard that raised for the wrong reason, but only a
    guard that resolved the final status in application order can report
    ``rejected`` here rather than ``deprecated``.

    RED on HEAD: the guard iterates the raw order, sees ``deprecated`` last, and
    does not raise. GREEN once the guard normalises to application order.
    """
    dep, rej = _deprecate_then_reject()
    misordered = MigrationSet((rej, dep))

    with pytest.raises(AliasItemCollisionError) as caught:
        refuse_alias_item_id_collision(misordered)

    assert caught.value.alias == WITHHELD, "the id used as both a live item and an alias key"
    assert caught.value.alias_target == PUBLISHED, "and the approved item the alias points at"
    assert caught.value.item_status == "rejected", (
        "the status the application order produces -- not `deprecated`, which is what the raw "
        "iteration order sees when `deprecateItem` lands last, and is the fault this drives out"
    )
    assert caught.value.migration_id == MigrationId(MIG_REJ), "the migration carrying the addAlias"
