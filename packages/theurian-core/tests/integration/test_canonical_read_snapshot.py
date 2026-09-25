"""``SqliteCanonicalStore.read_snapshot`` -- one snapshot, opt-in (ADR-0037 decision 3).

The export reads the whole canonical state and writes a bundle from it, so a
``migrate apply`` landing mid-walk would otherwise leave a bundle straddling two
states: a withdrawn row present as a concept, absent from the index file, and
pointed at by a relation gated under the newer state. Decision 3 answers that by
reading once, and this module is where the reading-once is measured rather than
asserted.

Every claim here needs a *real* file: WAL isolation is a property of the engine
and the pragmas the opener sets, and an in-memory fake would hold whichever
semantics its author believed. Each test therefore drives a database
``create_database`` made and ``write_transaction`` wrote, and the writes are
issued exactly as ``migrate apply`` issues them -- ``BEGIN IMMEDIATE``, the
writer's statements, ``COMMIT``.

**Both directions are measured, because the claim is opt-in.** A test that only
showed the snapshot holding would stay green if the context were entered
unconditionally for every read; the control in each pair shows what the default
path does with the same interleaving.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite.connection import (
    create_database,
    open_read_connection,
    write_transaction,
)
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("demo")
NOW: Final = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
CONTEXT: Final = RequestContext(project_id=PROJECT)
ANCHOR: Final = SourceAnchor(provider="git", source_uri="git://demo/a.md")

#: One revision id per item this module writes, so a test can name the second
#: write without computing one.
FIRST: Final = RevisionId("01K1REV00101234567890ABCDE")
SECOND: Final = RevisionId("01K1REV00201234567890ABCDE")


def _project() -> Project:
    return Project(
        project_id=PROJECT,
        root_path="/nonexistent/demo",  # a value, never opened
        repository_url="https://example.com/demo",
        default_branch="main",
        knowledge_directory=PurePosixPath(".theurian"),
        registered_at=NOW,
    )


def _revision(item_id: ItemId, revision_id: RevisionId) -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=revision_id,
        item_id=item_id,
        project_id=PROJECT,
        migration_id=MigrationId("01K1AAAAAA01234567890ABCDE"),
        title=f"Title of {item_id.value}",
        body="A body.",
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
        source_anchors=(ANCHOR,),
    )


def _item(item_id: ItemId) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=item_id,
        project_id=PROJECT,
        namespace="backend",
        kind=KnowledgeKind.ARCHITECTURE,
        status=KnowledgeStatus.DRAFT,
        current_revision_id=None,
        owner="platform-team",
        trust_level=TrustLevel.UNVERIFIED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=NOW),
    )


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "runtime" / "write.lock"


@pytest.fixture
def database(tmp_path: Path, lock: Path) -> Path:
    """A state database holding exactly one item, written the way `migrate apply` does."""
    path = tmp_path / "state" / "theurian-state-snapshot.sqlite"
    create_database(path, state_hash="a" * 64, engine_version=1)
    with write_transaction(path, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        revision = _revision(ItemId("a"), FIRST)
        writer.append_revision(revision)
        writer.put_item(_item(ItemId("a")).with_revision(revision))
    return path


def _land_a_second_item(database: Path, lock: Path) -> None:
    """One more approved item, committed through the real write path."""
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        revision = _revision(ItemId("b"), SECOND)
        writer.append_revision(revision)
        writer.put_item(_item(ItemId("b")).with_revision(revision))


# -- What the engine is actually running -----------------------------------


def test_a_read_connection_reports_wal(database: Path) -> None:
    """The journal mode the snapshot's isolation rests on, asked of the connection.

    ``CONNECTION_PRAGMAS`` sets ``journal_mode = WAL`` and the read opener runs
    that loop on a ``mode=ro`` connection, where the pragma cannot *change* a
    mode -- so this asks the file what it is rather than trusting the pragma
    list. Under a rollback journal the reader and the writer would contend
    instead of one being isolated from the other, and every property below would
    be describing a different engine.
    """
    with closing(open_read_connection(database)) as probe:
        assert probe.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# -- The snapshot, and the default path beside it ---------------------------


def test_a_commit_inside_a_read_snapshot_is_invisible_to_it(database: Path, lock: Path) -> None:
    """A writer landing mid-walk does not reach a walk already in progress.

    The property decision 3 asks for, driven at the shape an export walk has:
    a read, a ``migrate apply``-shaped commit, then a second read that must
    answer from the same state as the first.
    """
    with SqliteCanonicalStore(database) as store, store.read_snapshot():
        before = store.list_items(CONTEXT)
        _land_a_second_item(database, lock)
        after = store.list_items(CONTEXT)

    assert [item.item_id.value for item in before] == ["a"]
    assert [item.item_id.value for item in after] == ["a"], (
        "a row committed while the snapshot was open reached a read inside it, so a "
        "bundle could straddle two canonical states"
    )


def test_the_default_read_path_sees_a_commit_that_lands_between_two_reads(
    database: Path, lock: Path
) -> None:
    """The control for the test above: outside the context, reads interleave.

    This is what every shipped reader does today and goes on doing --
    :meth:`~theurian.infrastructure.sqlite.store.SqliteCanonicalStore.read_snapshot`
    is opt-in. It is also the vacuity guard: with the primitive entered for every
    read, the test above would pass while saying nothing.
    """
    with SqliteCanonicalStore(database) as store:
        before = store.list_items(CONTEXT)
        _land_a_second_item(database, lock)
        after = store.list_items(CONTEXT)

    assert [item.item_id.value for item in before] == ["a"]
    assert [item.item_id.value for item in after] == ["a", "b"]


def test_reads_see_the_newer_state_once_the_snapshot_ends(database: Path, lock: Path) -> None:
    """The ``ROLLBACK`` really ends the transaction, on the same connection.

    Without it the store would hold its first snapshot for the rest of its life,
    which is the opposite defect: a long-lived reader answering from a state the
    project has moved on from.
    """
    with SqliteCanonicalStore(database) as store:
        with store.read_snapshot():
            _land_a_second_item(database, lock)
            assert [item.item_id.value for item in store.list_items(CONTEXT)] == ["a"]
        assert [item.item_id.value for item in store.list_items(CONTEXT)] == ["a", "b"]


def test_a_writer_is_not_blocked_while_a_read_snapshot_is_open(database: Path, lock: Path) -> None:
    """WAL isolates the reader rather than delaying the writer.

    Measured because the alternative shape is a real hazard: a read transaction
    that held the write lock would make every export delay `migrate apply` for
    as long as the walk takes, and the writer would meet
    ``WriteTransactionBusyError`` rather than a snapshot. The writer here is the
    real path -- ``write_transaction`` issues ``BEGIN IMMEDIATE`` -- and it is
    entered *and committed* inside the reader's context.
    """
    with SqliteCanonicalStore(database) as store, store.read_snapshot():
        _land_a_second_item(database, lock)

    with closing(open_read_connection(database)) as probe:
        assert probe.execute("SELECT COUNT(*) AS n FROM knowledge_items").fetchone()["n"] == 2


def test_the_pin_is_what_takes_the_snapshot_and_not_the_begin(database: Path, lock: Path) -> None:
    """Why :data:`_PIN_THE_SNAPSHOT` exists, measured on raw connections.

    ``BEGIN`` is deferred, so it acquires nothing: the *first read* is what pins
    a WAL snapshot. Driven here rather than described, because the difference is
    the whole reason the primitive issues a second statement -- with ``BEGIN``
    alone the snapshot instant is wherever the caller's first read happens to
    fall, and a writer committing before it is inside the walk.

    Deliberately below the store: the point is a property of the engine, and
    asking it through the store would be asking whether the store still calls
    the pin.
    """
    with closing(open_read_connection(database)) as unpinned:
        unpinned.execute("BEGIN")
        _land_a_second_item(database, lock)
        seen = unpinned.execute("SELECT COUNT(*) AS n FROM knowledge_items").fetchone()["n"]
        unpinned.execute("ROLLBACK")

    assert seen == 2, "BEGIN alone pinned a snapshot, so the pin read would be dead weight"


def test_re_entering_a_read_snapshot_is_refused_by_name(database: Path) -> None:
    """Nested entry is a caller bug, and it must not read as a damaged database.

    SQLite answers a nested ``BEGIN`` with ``cannot start a transaction within a
    transaction``, and inside :func:`_reading` that would reach the caller as
    :class:`StateDatabaseUnreadableError` -- an operator told to delete derived
    state over a mistake in a call sequence.
    """
    with SqliteCanonicalStore(database) as store, store.read_snapshot():
        with pytest.raises(ValueError, match="already open"), store.read_snapshot():
            pass  # pragma: no cover - the refusal lands at the `with`, not here
        # The outer snapshot survives the refusal, so the walk it was opened for
        # can continue: a refusal that closed it would turn a caller bug into a
        # half-read state.
        assert [item.item_id.value for item in store.list_items(CONTEXT)] == ["a"]


def test_a_second_snapshot_may_be_opened_after_the_first_closes(database: Path, lock: Path) -> None:
    """The refusal above is re-entry, not one-per-store.

    The bookkeeping that refuses a nested entry has to be cleared when the
    context exits, or a second export through one store would be refused for a
    snapshot nobody holds.
    """
    with SqliteCanonicalStore(database) as store:
        with store.read_snapshot():
            assert [item.item_id.value for item in store.list_items(CONTEXT)] == ["a"]
        _land_a_second_item(database, lock)
        with store.read_snapshot():
            assert [item.item_id.value for item in store.list_items(CONTEXT)] == ["a", "b"]


def test_a_failing_body_still_ends_the_snapshot(database: Path, lock: Path) -> None:
    """The transaction is ended on the way out however the body ends.

    A leaked read transaction pins the connection to a stale snapshot and holds a
    WAL read mark, which stops checkpointing: the ``-wal`` file then grows for as
    long as the process lives.
    """
    with SqliteCanonicalStore(database) as store:
        with pytest.raises(RuntimeError, match="the walk failed"), store.read_snapshot():
            msg = "the walk failed"
            raise RuntimeError(msg)
        _land_a_second_item(database, lock)
        assert [item.item_id.value for item in store.list_items(CONTEXT)] == ["a", "b"]


def test_the_snapshot_holds_across_every_read_the_export_makes(database: Path, lock: Path) -> None:
    """One snapshot over the three reads a bundle is built from.

    ``list_items``, ``get_revision`` and ``list_relations`` are three separate
    statements on the same connection, and the export's own claim is about all of
    them rather than about the first. A second item landing mid-walk must be
    invisible to each: absent from the walk, and absent as a revision the walk
    could dereference.
    """
    with SqliteCanonicalStore(database) as store, store.read_snapshot():
        items = store.list_items(CONTEXT)
        _land_a_second_item(database, lock)
        revisions = [
            store.get_revision(CONTEXT, item.current_revision_id)
            for item in items
            if item.current_revision_id is not None
        ]
        edges = [store.list_relations(CONTEXT, item.item_id) for item in items]
        landed_later = store.get_revision(CONTEXT, SECOND)

    assert [revision.item_id.value for revision in revisions if revision is not None] == ["a"]
    assert edges == [()]
    assert landed_later is None, (
        "a revision committed inside the snapshot was readable through it, so a "
        "relation or a body could come from the newer state"
    )


def test_a_snapshot_over_a_missing_database_reports_the_missing_file(tmp_path: Path) -> None:
    """The opener's own refusal travels, rather than being converted here.

    ``read_snapshot`` acquires the connection, so it meets every fault
    ``open_read_connection`` answers for. ``FileNotFoundError`` is the member
    whose cure differs most -- build the state, delete nothing -- and it is in
    ``_ALREADY_ANSWERED`` precisely so a nested guard cannot restate it as a
    damaged file.
    """
    store = SqliteCanonicalStore(tmp_path / "state" / "absent.sqlite")
    with pytest.raises(FileNotFoundError), store.read_snapshot():
        pass  # pragma: no cover - the refusal lands at the `with`, not here
