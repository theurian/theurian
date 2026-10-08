"""Which write the permissive-move report records as a field's writer (GHSA-v2qg-23fc-7fqp).

The report's ``kind`` is judged on the predicate the accept floors use, so a write
replaces the recorded writer only when it changes that predicate: surfaceability for
status, the class for sensitivity. A status write between two retired values changes
the value and not the predicate; recording it replaced a real withdrawal with a
non-withdrawal, and the accepted update that readmitted the item was filed ``lowers``.

The recorded writer is read from ``LabelWriters._last``, which is private: the public
rows exist only for a writer followed by a loosening upsert, and a write landing on a
surfaceable status cannot be followed by one.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, datetime
from typing import Final

import pytest

from theurian.application.authorization import DISCLOSURE_ORDER
from theurian.application.permissive_moves import LabelWriters
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    Sensitivity,
    TrustLevel,
    may_surface,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId
from theurian.domain.knowledge import KnowledgeItem
from theurian.domain.migration import ChangeSensitivity, CreateItem
from theurian.domain.values import ValidityPeriod

pytestmark = pytest.mark.unit

_ITEM: Final = ItemId("architecture.auth-policy")
_SEEDING: Final = MigrationId("01K1BBBBBB01234567890ABCDE")
_WRITING: Final = MigrationId("01K1CCCCCC01234567890ABCDE")


def _item(status: KnowledgeStatus, sensitivity: Sensitivity) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=_ITEM,
        project_id=ProjectId("backend-service"),
        namespace="backend",
        kind=KnowledgeKind.ARCHITECTURE,
        status=status,
        current_revision_id=None,
        owner="platform-team",
        trust_level=TrustLevel.UNVERIFIED,
        sensitivity=sensitivity,
        validity=ValidityPeriod(valid_from=datetime(2026, 8, 1, tzinfo=UTC)),
    )


def _surfaceable(status: KnowledgeStatus) -> bool:
    return may_surface(status, include_unapproved=True)


def _differently_from(value: KnowledgeStatus | Sensitivity) -> KnowledgeStatus | Sensitivity:
    pool = KnowledgeStatus if isinstance(value, KnowledgeStatus) else Sensitivity
    return next(other for other in pool if other != value)


def _status_pairs() -> list[object]:
    cases: list[object] = []
    for prior, landed in itertools.product(KnowledgeStatus, repeat=2):
        cases.append(pytest.param(prior, landed, id=f"{prior}->{landed}"))
    return cases


@pytest.mark.parametrize(("prior", "landed"), _status_pairs())
def test_a_status_write_replaces_the_recorded_writer_only_by_changing_surfaceability(
    prior: KnowledgeStatus, landed: KnowledgeStatus
) -> None:
    """Value granularity let a retired -> retired write displace the withdrawal that retired it.

    The perturbation this must catch is `_record` comparing status VALUE where the
    floors compare the predicate; every value-only pair is RED under it.
    """
    writers = LabelWriters()
    sensitivity = Sensitivity.INTERNAL
    writers.upserted(
        _SEEDING,
        _item(_differently_from(prior), sensitivity),  # type: ignore[arg-type]
        _item(prior, sensitivity),
    )

    writers.upserted(_WRITING, _item(prior, sensitivity), _item(landed, sensitivity))

    recorded = writers._last[_ITEM, "status"]
    changes_predicate = _surfaceable(prior) != _surfaceable(landed)
    assert (recorded.migration_id == _WRITING) is changes_predicate
    if changes_predicate:
        assert recorded.withdrawal is (_surfaceable(prior) and not _surfaceable(landed))


@pytest.mark.parametrize(
    ("prior", "landed"),
    list(itertools.product(DISCLOSURE_ORDER, repeat=2)),
    ids=str,
)
def test_a_sensitivity_write_replaces_the_recorded_writer_only_by_changing_the_class(
    prior: Sensitivity, landed: Sensitivity
) -> None:
    """Sensitivity is totally ordered, so value granularity and predicate granularity coincide."""
    writers = LabelWriters()
    status = KnowledgeStatus.APPROVED
    writers.upserted(
        _SEEDING,
        _item(status, _differently_from(prior)),  # type: ignore[arg-type]
        _item(status, prior),
    )

    writers.upserted(_WRITING, _item(status, prior), _item(status, landed))

    recorded = writers._last[_ITEM, "sensitivity"]
    assert (recorded.migration_id == _WRITING) is (prior != landed)
    if prior != landed:
        raised = DISCLOSURE_ORDER.index(landed) > DISCLOSURE_ORDER.index(prior)
        assert recorded.withdrawal is raised


_X: Final = ItemId("architecture.x")
_Y: Final = ItemId("architecture.y")
_INTERNAL: Final = Sensitivity.INTERNAL
_CONFIDENTIAL: Final = Sensitivity.CONFIDENTIAL
_PUBLIC: Final = Sensitivity.PUBLIC
_CHANGE: Final = ChangeSensitivity.__new__(ChangeSensitivity)


def _migration(number: int) -> MigrationId:
    return MigrationId(f"01K1{number:02d}" + "0" * 20)


def _labelled(item_id: ItemId, sensitivity: Sensitivity) -> KnowledgeItem:
    return replace(_item(KnowledgeStatus.APPROVED, sensitivity), item_id=item_id)


def _order(writers: LabelWriters) -> list[tuple[str, ItemId, str | None, str | None]]:
    return [
        (m.migration_id.value[4:6], m.item_id, m.kind, m.undoes.value[4:6] if m.undoes else None)
        for m in writers.moves
    ]


def test_upsert_rows_follow_the_first_write_that_loosened_each_field() -> None:
    """Migration 10 replays after 30 and 20, restates X, lowers Y, then lowers X.

    0.5.1 placed an upsert's rows by the write that loosened the field, so X's row sorts
    after Y's; the order is read by the text report and by the accept refusal's first row.
    """
    writers = LabelWriters()
    create = CreateItem.__new__(CreateItem)
    writers.wrote(_migration(1), create, prior=None, landed=_labelled(_X, _INTERNAL))
    writers.wrote(_migration(1), create, prior=None, landed=_labelled(_Y, _INTERNAL))
    writers.wrote(
        _migration(30), _CHANGE, prior=_labelled(_X, _INTERNAL), landed=_labelled(_X, _CONFIDENTIAL)
    )
    writers.wrote(
        _migration(20), _CHANGE, prior=_labelled(_X, _CONFIDENTIAL), landed=_labelled(_X, _INTERNAL)
    )

    writers.upserted(_migration(10), _labelled(_X, _INTERNAL), _labelled(_X, _INTERNAL))
    writers.upserted(_migration(10), _labelled(_Y, _INTERNAL), _labelled(_Y, _PUBLIC))
    writers.upserted(_migration(10), _labelled(_X, _INTERNAL), _labelled(_X, _PUBLIC))

    assert _order(writers) == [
        ("20", _X, "reorders", "30"),
        ("10", _Y, "lowers", "01"),
        ("10", _X, "lowers", "20"),
    ]


def test_upsert_rows_keep_0_5_1s_order_where_no_larger_id_holds_the_field() -> None:
    """Migration 10 lowers and restores X by sanctioned writes, then upserts Y and X lower.

    Only a write inverted against a larger id may note a field; a plain sanctioned
    lowering noting X first would order X's row ahead of Y's, where 0.5.1 printed Y's.
    """
    writers = LabelWriters()
    create = CreateItem.__new__(CreateItem)
    writers.wrote(_migration(1), create, prior=None, landed=_labelled(_X, _INTERNAL))
    writers.wrote(_migration(1), create, prior=None, landed=_labelled(_Y, _INTERNAL))
    writers.wrote(
        _migration(10), _CHANGE, prior=_labelled(_X, _INTERNAL), landed=_labelled(_X, _PUBLIC)
    )
    writers.wrote(
        _migration(10), _CHANGE, prior=_labelled(_X, _PUBLIC), landed=_labelled(_X, _INTERNAL)
    )

    writers.upserted(_migration(10), _labelled(_Y, _INTERNAL), _labelled(_Y, _PUBLIC))
    writers.upserted(_migration(10), _labelled(_X, _INTERNAL), _labelled(_X, _PUBLIC))

    assert _order(writers) == [("10", _Y, "lowers", "01"), ("10", _X, "lowers", "01")]


def test_reorders_rows_follow_the_first_write_that_loosened_each_field() -> None:
    """Migration 10 replays after 30, restates X, lowers Y, then lowers X: Y's row comes first."""
    writers = LabelWriters()
    create = CreateItem.__new__(CreateItem)
    writers.wrote(_migration(1), create, prior=None, landed=_labelled(_X, _INTERNAL))
    writers.wrote(_migration(1), create, prior=None, landed=_labelled(_Y, _INTERNAL))
    writers.wrote(
        _migration(30), _CHANGE, prior=_labelled(_X, _INTERNAL), landed=_labelled(_X, _CONFIDENTIAL)
    )
    writers.wrote(
        _migration(30), _CHANGE, prior=_labelled(_Y, _INTERNAL), landed=_labelled(_Y, _CONFIDENTIAL)
    )

    writers.wrote(
        _migration(10),
        _CHANGE,
        prior=_labelled(_X, _CONFIDENTIAL),
        landed=_labelled(_X, _CONFIDENTIAL),
    )
    writers.wrote(
        _migration(10), _CHANGE, prior=_labelled(_Y, _CONFIDENTIAL), landed=_labelled(_Y, _INTERNAL)
    )
    writers.wrote(
        _migration(10), _CHANGE, prior=_labelled(_X, _CONFIDENTIAL), landed=_labelled(_X, _INTERNAL)
    )

    assert [m.item_id for m in writers.moves] == [_Y, _X]
