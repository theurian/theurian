"""SQLite canonical store, against a real database (ADR-0004, ADR-0017, ADR-0018).

These use real files and real transactions. The in-memory fake proves the engine
is correct; only this proves the adapter is.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
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
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
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
    """INV-3 is checked on read, so a tampered stored hash is caught rather than
    trusted."""
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())

    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute(
            "UPDATE knowledge_revisions SET body = ? WHERE revision_id = ?",
            ("tampered", REV_1.value),
        )

    with SqliteCanonicalStore(database) as store, pytest.raises(InvariantViolationError):
        store.get_revision(RequestContext(project_id=PROJECT), REV_1)


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

    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.record_migration(PROJECT, MIGRATION, "checksum-one", NOW)
        writer.record_migration(PROJECT, second, "checksum-two", NOW)

    with SqliteCanonicalStore(database) as store:
        history = store.applied_migrations(PROJECT)

    assert history == ((MIGRATION, "checksum-one"), (second, "checksum-two"))
