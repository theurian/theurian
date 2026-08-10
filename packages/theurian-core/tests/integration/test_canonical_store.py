"""SQLite canonical store, against a real database (ADR-0004, ADR-0017, ADR-0018).

These use real files and real transactions. The in-memory fake proves the engine
is correct; only this proves the adapter is.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import pytest

from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeAlias,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.values import MARKDOWN, ContentHash, ValidityPeriod
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
    create_database,
    open_read_connection,
    write_transaction,
)
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

PROJECT = ProjectId("demo")
ITEM = ItemId("architecture.auth-policy")
REV_1 = RevisionId("01K1REV00101234567890ABCDE")
REV_2 = RevisionId("01K1REV00201234567890ABCDE")
MIGRATION = MigrationId("01K1AAAAAA01234567890ABCDE")
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

#: A cell that is a digest to no reader, holding nothing this codebase says
#: elsewhere, so a fragment of it in a message came out of the database file.
SENTINEL = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"
ANCHOR = SourceAnchor(
    provider="git",
    source_uri="git://demo/.theurian/knowledge/a.md",
    commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    file_path=".theurian/knowledge/a.md",
    line_start=1,
    line_end=10,
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "state" / "theurian-state-abc123.sqlite"
    create_database(path, state_hash="a" * 64, engine_version=1)
    return path


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "runtime" / "write.lock"


def _project() -> Project:
    return Project(
        project_id=PROJECT,
        root_path="/tmp/demo",  # noqa: S108 - a value, never opened
        repository_url="https://github.com/acme/demo",
        default_branch="main",
        knowledge_directory=PurePosixPath(".theurian"),
        registered_at=NOW,
    )


def _revision(revision_id: RevisionId = REV_1, body: str = "A body.") -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=revision_id,
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
            scope_paths=("services/auth/**",),
        ),
        validity=ValidityPeriod(valid_from=NOW),
        author="engineer@example.com",
        created_at=NOW,
        source_anchors=(ANCHOR,),
    )


def _item() -> KnowledgeItem:
    return KnowledgeItem(
        item_id=ITEM,
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


# -- Round trip ------------------------------------------------------------


def test_a_revision_survives_a_round_trip(database: Path, lock: Path) -> None:
    revision = _revision()
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(revision)
        writer.put_item(_item().with_revision(revision))

    with SqliteCanonicalStore(database) as store:
        context = RequestContext(project_id=PROJECT)
        loaded = store.get_revision(context, REV_1)

    assert loaded is not None
    assert loaded.body == revision.body
    assert loaded.content_sha256 == revision.content_sha256
    assert loaded.metadata.scope_paths == ("services/auth/**",)
    assert loaded.source_anchors[0].commit_sha == ANCHOR.commit_sha
    assert loaded.source_anchors[0].line_end == 10


def test_reading_a_revision_verifies_its_content_hash(database: Path, lock: Path) -> None:
    """INV-3 is checked on read, so a tampered stored hash is caught rather than trusted.

    The refusal now arrives as `StateDatabaseUnreadableError` rather than as the
    bare `InvariantViolationError`, and the change is deliberate: INV-3's message
    names ``content_sha256.short`` and the hash of the *stored body*, which is a
    12-character confirmation oracle over a revision the caller may not be
    entitled to read. The violation still travels as ``__cause__``, which is what
    this asserts -- the check is unchanged, only who is told what.
    """
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())

    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute(
            "UPDATE knowledge_revisions SET body = ? WHERE revision_id = ?",
            ("tampered", REV_1.value),
        )

    with (
        SqliteCanonicalStore(database) as store,
        pytest.raises(StateDatabaseUnreadableError) as caught,
    ):
        store.get_revision(RequestContext(project_id=PROJECT), REV_1)

    assert isinstance(caught.value.__cause__, InvariantViolationError), (
        "INV-3 must still be what refused it"
    )
    assert "content hash mismatch" in str(caught.value.__cause__)
    assert "theurian migrate apply" in str(caught.value), "and the refusal must name a remedy"


# -- ADR-0006: immutability ------------------------------------------------


def test_appending_the_identical_revision_twice_is_a_no_op(database: Path, lock: Path) -> None:
    """Required for FR-K8: re-applying a migration repeats its appends."""
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())
        writer.append_revision(_revision())

    with SqliteCanonicalStore(database) as store:
        assert len(store.list_revisions(RequestContext(project_id=PROJECT), ITEM)) == 1


def test_rewriting_a_revision_with_different_content_is_refused(database: Path, lock: Path) -> None:
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision(body="original"))

        with pytest.raises(InvariantViolationError, match="immutable"):
            writer.append_revision(_revision(body="rewritten"))


def test_a_damaged_stored_hash_is_not_reported_as_an_immutability_violation(
    database: Path, lock: Path
) -> None:
    """The remedy INV-1 prints is harmful when the mismatch is a damaged cell.

    The comparison above has two causes and only one message. A stored
    ``content_sha256`` that is not a digest at all differs from the caller's hash
    exactly as a rewritten body does, so re-applying an unchanged migration --
    the FR-K8 path, which repeats every append -- reported ``Revisions are
    immutable; write a new revision instead``. An author who follows that appends
    a duplicate into a database that is already damaged.

    Asserted through the *unchanged* revision on purpose: this is the input that
    must succeed, and it is the one the misdiagnosis fired on.
    """
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())

    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute(
            "UPDATE knowledge_revisions SET content_sha256 = ? WHERE revision_id = ?",
            (SENTINEL, REV_1.value),
        )

    with (
        write_transaction(database, lock) as connection,
        pytest.raises(StateDatabaseUnreadableError) as caught,
    ):
        SqliteWriter(connection).append_revision(_revision())

    assert "theurian migrate apply" in str(caught.value), "a refusal a caller cannot act on"
    assert "immutable" not in str(caught.value), "the immutability remedy is the harmful one here"
    assert SENTINEL not in str(caught.value), "and the cell stays inside the guard"


def test_history_is_preserved_across_revisions(database: Path, lock: Path) -> None:
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        first = _revision(REV_1, "first")
        second = _revision(REV_2, "second")
        writer.append_revision(first)
        writer.put_item(_item().with_revision(first))
        writer.append_revision(second)
        writer.put_item(_item().with_revision(second))

    with SqliteCanonicalStore(database) as store:
        context = RequestContext(project_id=PROJECT)
        revisions = store.list_revisions(context, ITEM)
        item = store.get_item(context, ITEM)

    assert [r.revision_id for r in revisions] == [REV_1, REV_2]
    assert item is not None
    assert item.current_revision_id == REV_2


# -- Transactions ----------------------------------------------------------


def test_a_failed_transaction_rolls_everything_back(database: Path, lock: Path) -> None:
    """All operations in one migration share one transaction (ADR-0005)."""
    with (
        pytest.raises(RuntimeError, match="deliberate"),
        write_transaction(database, lock) as connection,
    ):
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())
        raise RuntimeError("deliberate failure mid-migration")

    with closing(sqlite3.connect(database)) as raw, raw:
        count = raw.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0]
    assert count == 0


def test_a_second_writer_waits_rather_than_corrupting(lock: Path) -> None:
    """ADR-0018: the write lock serialises writers.

    Uses a non-blocking probe from a second process-level lock handle, which is
    what the timeout path is built on.
    """
    from theurian.infrastructure.sqlite.connection import WriteLock

    outer = WriteLock(lock, timeout=0.2)
    inner = WriteLock(lock, timeout=0.2)

    from theurian.infrastructure.sqlite.connection import WriteLockTimeoutError

    # A different WriteLock object, so this exercises the real flock path rather
    # than reentrancy within one handle.
    with outer.held(), pytest.raises(WriteLockTimeoutError), inner.held():
        pass  # pragma: no cover - the acquisition above must fail


# -- ADR-0017: schema version ----------------------------------------------


def test_schema_version_is_recorded_inside_the_database(database: Path) -> None:
    with closing(sqlite3.connect(database)) as raw, raw:
        row = raw.execute("SELECT schema_version FROM schema_metadata").fetchone()
    assert row[0] == SCHEMA_VERSION


def test_a_foreign_schema_version_is_refused(database: Path) -> None:
    """Rebuilt, never migrated. A state database is derived (ADR-0017)."""
    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute("UPDATE schema_metadata SET schema_version = 999")

    with pytest.raises(SchemaVersionMismatchError) as exc:
        open_read_connection(database)

    assert exc.value.found == 999
    assert "rebuild" in str(exc.value).lower()


def test_creating_over_an_existing_database_is_refused(database: Path) -> None:
    """Overwriting would discard a state another process may be reading."""
    with pytest.raises(FileExistsError):
        create_database(database, state_hash="b" * 64, engine_version=1)


def test_a_read_connection_cannot_write(database: Path) -> None:
    connection = open_read_connection(database)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM projects")
    finally:
        connection.close()


# -- Relations and aliases -------------------------------------------------


def test_relations_are_traversable_in_both_directions(database: Path, lock: Path) -> None:
    successor = ItemId("architecture.auth-policy-v2")

    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.add_relation(
            KnowledgeRelation(
                project_id=PROJECT,
                source_item_id=successor,
                relation_type=RelationType.SUPERSEDES,
                target_item_id=ITEM,
                created_at=NOW,
            )
        )

    with SqliteCanonicalStore(database) as store:
        context = RequestContext(project_id=PROJECT)
        from_target = store.list_relations(context, ITEM)
        from_source = store.list_relations(context, successor)

    # Stored once, traversable both ways: a caller never has to know which
    # direction the author happened to write.
    assert from_source[0].relation_type is RelationType.SUPERSEDES
    assert from_target[0].relation_type is RelationType.SUPERSEDED_BY
    assert from_target[0].source_item_id == ITEM


def test_an_alias_resolves_to_its_item(database: Path, lock: Path) -> None:
    """Renaming without aliases silently breaks every past citation."""
    old = ItemId("architecture.old-auth")

    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        revision = _revision()
        writer.append_revision(revision)
        writer.put_item(_item().with_revision(revision))
        writer.add_alias(
            KnowledgeAlias(alias=old, item_id=ITEM, project_id=PROJECT, created_at=NOW)
        )

    with SqliteCanonicalStore(database) as store:
        resolved = store.get_item(RequestContext(project_id=PROJECT), old)

    assert resolved is not None
    assert resolved.item_id == ITEM


# -- Project isolation -----------------------------------------------------


def test_a_query_never_crosses_a_project_boundary(database: Path, lock: Path) -> None:
    """SEC-13, at the storage layer."""
    other = ProjectId("other-service")

    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        revision = _revision()
        writer.append_revision(revision)
        writer.put_item(_item().with_revision(revision))

    with SqliteCanonicalStore(database) as store:
        assert store.get_item(RequestContext(project_id=other), ITEM) is None
        assert store.get_revision(RequestContext(project_id=other), REV_1) is None
        assert store.list_items(RequestContext(project_id=other)) == ()


# -- Migration history -----------------------------------------------------


def test_migration_history_records_order_and_checksums(database: Path, lock: Path) -> None:
    second = MigrationId("01K1BBBBBB01234567890ABCDE")
    # Real digests, because the read side now refuses anything that is not one:
    # the only production writer is `MigrationEngine.apply` passing
    # `migration.checksum.value`, and a placeholder here was testing a round
    # trip that cannot happen.
    first_checksum = ContentHash.of_text("one").value
    second_checksum = ContentHash.of_text("two").value

    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.record_migration(PROJECT, MIGRATION, first_checksum, NOW)
        writer.record_migration(PROJECT, second, second_checksum, NOW)

    with SqliteCanonicalStore(database) as store:
        history = store.applied_migrations(PROJECT)

    assert history == ((MIGRATION, first_checksum), (second, second_checksum))


def test_list_items_never_filters_by_validity(database: Path, lock: Path) -> None:
    """FR-R1's validity axis is not this store's job (#63 phase 2, CRITICAL
    finding in review round 1 of PR #112).

    An earlier version gave ``list_items`` a ``current_at`` parameter that
    filtered in SQL, comparing a stored ``valid_from``/``valid_to`` against a
    bound moment as SQLite TEXT -- silently wrong whenever the two were
    authored in different UTC offsets, since that comparison is lexicographic
    over the ISO-8601 string rather than over the absolute instant it names.
    Deleted rather than fixed in place: both `knowledge.search` answer paths
    now apply ``ValidityPeriod.contains`` in Python instead --
    ``CanonicalVisibility.at_moment`` on the ranked path (deliberately *after*
    the depth-doubling loop that ``cleared`` drives, so a caller-chosen moment
    cannot bias that loop's own retriever-call count; see that method's
    docstring for the CRITICAL finding this closes) and a plain
    ``item.validity.contains`` check inside ``mcp.search._scan`` on the
    unranked fallback. Neither reaches this store, which is what this test
    pins: every item is returned, whatever its validity window, always.
    """
    always = replace(
        _item(), item_id=ItemId("architecture.always"), validity=ValidityPeriod(valid_from=NOW)
    )
    later = replace(
        _item(),
        item_id=ItemId("architecture.later"),
        validity=ValidityPeriod(valid_from=NOW + timedelta(days=365)),
    )
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.put_item(always)
        writer.put_item(later)

    with SqliteCanonicalStore(database) as store:
        items = store.list_items(RequestContext(project_id=PROJECT))

    assert {i.item_id.value for i in items} == {"architecture.always", "architecture.later"}, (
        "an item not yet valid is still listed -- this store has no concept of "
        "`current_at` to exclude it with"
    )


# -- Session lifetime ------------------------------------------------------


def test_entering_a_read_session_opens_before_the_first_read(database: Path) -> None:
    """`CanonicalReadSession.__enter__` acquires the handle, and it is timed.

    Not tidiness about `__exit__` having something to close. `ResultGate` opens
    one of these, ranks through it, and shows the caller none of what the
    canonical store withheld -- so a session that connected at its *first read*
    charged `sqlite3.connect`, the pragmas and the schema-version check only to
    requests that found something, and "found something" is exactly the fact a
    `count: 0` response is refusing to state.

    `CanonicalVisibility.cleared` is a comprehension, so zero rows means zero
    `get_item` calls and, before this, zero connections. Measured on a
    61-document Japanese corpus: one ordinary `knowledge.search` classified a
    probe against a one-character-different control 88.3% of the time, +0.60 ms
    at the median, and six characters of a credential no response contains came
    back in 836 calls with the body never read. Opening in `__enter__` takes the
    same measurement to 57.8%, which is chance.

    Asserted on the private attribute deliberately: the observable this closes
    *is* whether a connection exists, so a public proxy for it would be a proxy
    for the wrong thing. `return self` restores the leak and fails here.
    """
    with SqliteCanonicalStore(database) as store:
        assert store._connection is not None, "the handle is acquired on entry, not on first read"
        opened = store._connection

        # A whole session that reads nothing -- the shape a query matching no
        # indexed chunk takes -- must still have paid for the connection.
        assert store._connection is opened

    assert store._connection is None, "and released on exit"


def test_a_read_session_reports_a_missing_database_when_it_is_opened(tmp_path: Path) -> None:
    """Eager opening moves this error to the `with`, so the remedy is named there.

    A consequence of the change above rather than a separate decision, recorded
    because it changes *where* a caller sees the failure: it used to surface at
    whichever read happened to run first, which on a search that matched nothing
    was never.
    """
    with (
        pytest.raises(FileNotFoundError, match="No state database at"),
        SqliteCanonicalStore(tmp_path / "absent.sqlite"),
    ):
        pass  # pragma: no cover - the context manager raises on entry
