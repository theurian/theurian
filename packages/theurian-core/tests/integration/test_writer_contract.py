"""The append-revision invariants, bound to every ``MigrationWriter`` at once.

Two adapters implement ``MigrationWriter`` (ADR-0003): the real
:class:`~theurian.infrastructure.sqlite.store.SqliteWriter` and the in-memory
:class:`~fakes.store.InMemoryWriter` the engine's unit tests run against. Two of
``append_revision``'s invariants -- a revision id belongs to one item (INV-2,
SEC-13) and a revision is immutable (INV-1) -- are enforced in *both*, by hand,
in mirrored code. Nothing bound the two to the same behaviour: ``test_ports``
checks the Protocol's shape, and every other test drives one adapter or the
other, never the pair against one requirement.

That gap is not hypothetical. A change to one adapter's refusal that forgets the
other would leave the fake accepting a state the real store rejects -- an item
pointing at another item's revision -- and every engine test written against the
fake would then describe a database that cannot exist, all while staying green.
The cross-item leak this file's siblings pin (a revisionId reused across a
``rejected`` item and an ``approved`` one) is exactly that state, so the fake
having a *matching* guard is part of the security property, not a convenience.

The mirroring runs both ways, so both invariants are covered here rather than
only the item-scope one the security fix added: the content-mismatch arm is the
same hand-copied shape and would drift the same way. Parametrised over both
adapters, each test states one behaviour, so a red run names which adapter and
which invariant moved.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from fakes import InMemoryWriter

from theurian.application.migration_engine import MigrationWriter
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.store import SqliteWriter

pytestmark = pytest.mark.integration

PROJECT = ProjectId("demo")
ITEM = ItemId("architecture.auth-policy")
#: A second item, so a revision can be offered to one it does not belong to.
OTHER_ITEM = ItemId("architecture.caching-policy")
REV_1 = RevisionId("01K1REV00101234567890ABCDE")
MIGRATION = MigrationId("01K1AAAAAA01234567890ABCDE")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _project() -> Project:
    return Project(
        project_id=PROJECT,
        root_path="/tmp/demo",  # noqa: S108 - a value, never opened
        repository_url="https://github.com/acme/demo",
        default_branch="main",
        knowledge_directory=PurePosixPath(".theurian"),
        registered_at=NOW,
    )


def _revision(body: str = "A body.") -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=REV_1,
        item_id=ITEM,
        project_id=PROJECT,
        migration_id=MIGRATION,
        title="Auth policy",
        body=body,
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=NOW),
        author="engineer@example.com",
        created_at=NOW,
        source_anchors=(SourceAnchor(provider="git", source_uri="git://demo/a.md"),),
    )


@pytest.fixture(params=["sqlite", "memory"])
def writer(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MigrationWriter]:
    """One live ``MigrationWriter`` per adapter, ready to take an append.

    The real adapter is handed inside an open write transaction with its project
    registered, so it behaves exactly as ``migrate apply`` drives it; the fake
    needs neither. Both then expose the same ``append_revision`` and
    ``list_revision_ids`` the tests below call, which is the whole point of
    parametrising over one variable rather than duplicating each assertion.
    """
    if request.param == "memory":
        yield InMemoryWriter()
        return

    database = tmp_path / "state" / "theurian-state-abc123.sqlite"
    create_database(database, state_hash="a" * 64, engine_version=1)
    lock = tmp_path / "runtime" / "write.lock"
    with write_transaction(database, lock) as connection:
        real = SqliteWriter(connection)
        real.register_project(_project())
        yield real


def test_a_revision_id_refuses_to_change_hands_on_every_writer(writer: MigrationWriter) -> None:
    """INV-2, SEC-13: no item may adopt another item's revision, on either adapter.

    A second item offering the first item's revision id -- with the *same*
    content, so only the owner differs -- must be refused, not answered as an
    FR-K8 no-op. If the fake ever stopped refusing this, the engine could build
    the exact cross-item pointer the real store rejects, and its tests would
    describe an impossible database. Verified by removing each adapter's item
    arm in turn: this test then goes RED for that adapter's parameter alone.
    """
    writer.append_revision(_revision())

    with pytest.raises(InvariantViolationError) as caught:
        writer.append_revision(replace(_revision(), item_id=OTHER_ITEM))

    message = str(caught.value)
    assert REV_1.value in message, "the refusal must name the id that was reused"
    assert "cannot claim it" in message, (
        "and say it belongs to another item -- the item-scope arm, not the content arm"
    )


def test_rewriting_a_revision_with_different_content_is_refused_on_every_writer(
    writer: MigrationWriter,
) -> None:
    """INV-1: a revision is immutable, on either adapter.

    The mirror of the test above, and here for the same reason: the immutability
    arm is hand-copied into both writers, so it can drift in one and not the
    other just as the item-scope arm can. A different body under an existing id
    must be refused. Verified by removing each adapter's content arm in turn:
    RED for that adapter's parameter alone.
    """
    writer.append_revision(_revision(body="original"))

    with pytest.raises(InvariantViolationError) as caught:
        writer.append_revision(_revision(body="rewritten"))

    message = str(caught.value)
    assert REV_1.value in message, "the refusal must name the id"
    assert "different content" in message, "the immutability arm, not the item-scope arm"
    assert "Revisions are immutable; write a new revision instead." in message, (
        "and name the remedy -- pinned on both adapters so the fake's message cannot "
        "drift from the real store's, which binds only substrings otherwise"
    )


def test_an_identical_re_append_stays_a_no_op_on_every_writer(writer: MigrationWriter) -> None:
    """FR-K8: re-applying a migration repeats its appends, and both must let it.

    The legitimate case the two refusals must not swallow -- and the guard that a
    writer which refused *everything* would fail rather than pass. The same
    revision appended twice is one stored revision on either adapter.
    """
    writer.append_revision(_revision())
    writer.append_revision(_revision())

    assert writer.list_revision_ids(PROJECT, ITEM) == (REV_1,), (
        "an identical re-append must leave exactly one revision, stored once"
    )


def test_put_item_refuses_a_pointer_to_another_items_revision_on_every_writer(
    writer: MigrationWriter,
) -> None:
    """M1, INV-2, SEC-13: an item may not point its revision at another item's, either adapter.

    The store half of INV-2. ``KnowledgeItem.with_revision`` already refuses a
    cross-item ``current_revision_id`` in memory, but the ``put_item`` upsert
    trusts the caller to have gone through it -- every call site does today, and
    none has to for the leak to reopen. So the pointer here is built *directly*,
    bypassing the domain guard, which is the whole point of M1: this exercises the
    store's own refusal, not the domain object's.

    That refusal is what stops an approved item coming to serve a withheld item's
    title, anchors and body -- a state no reader can catch, because a reader
    dereferences ``current_revision_id`` and is right to. Mirrored into the fake
    so the memory arm passes for the same reason the real store does; without the
    mirror the memory arm could not be pinned at all. Verified by making each
    adapter's guard a no-op in turn: RED for that adapter's parameter alone.
    """
    withheld_body = "WITHHELD sk-live-9f2a7c41 the body only this revision holds"
    writer.append_revision(_revision(body=withheld_body))

    # Built directly rather than via ``with_revision``, so the pointer is
    # cross-item: OTHER_ITEM names REV_1, which belongs to ITEM. The domain guard
    # would have refused this construction; the store guard is what must here.
    pointer = KnowledgeItem(
        item_id=OTHER_ITEM,
        project_id=PROJECT,
        namespace="backend",
        kind=KnowledgeKind.ARCHITECTURE,
        status=KnowledgeStatus.DRAFT,
        current_revision_id=REV_1,
        owner="platform-team",
        trust_level=TrustLevel.UNVERIFIED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=NOW),
    )

    with pytest.raises(InvariantViolationError) as caught:
        writer.put_item(pointer)

    message = str(caught.value)
    assert REV_1.value in message, "the refusal must name the revision id that was reused"
    assert "cannot point its current revision at it" in message, (
        "the put_item cross-item arm, so a red run says which guard moved"
    )
    assert withheld_body not in message, (
        "the withheld body must not ride out on the refusal that protects it"
    )
