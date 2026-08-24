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
from typing import Final

import pytest

from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    SURFACEABLE_STATUSES,
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
#: A second item, so a revision can be offered to one it does not belong to.
OTHER_ITEM = ItemId("architecture.caching-policy")
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


def test_a_revision_id_cannot_change_hands(database: Path, lock: Path) -> None:
    """A revision id names one item, and matching content does not transfer it.

    Idempotency is resolved on the whole revision rather than on the id, because
    these two inputs are indistinguishable by id and content alone: a `migrate
    apply` repeating an append it has already made, and a second item laying
    claim to the first item's revision. Answering the second as a no-op leaves
    that item with no revision of its own, and the `put_item` which follows in
    the same transaction then points it at this row.
    """
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())

        with pytest.raises(InvariantViolationError) as caught:
            writer.append_revision(replace(_revision(), item_id=OTHER_ITEM))

    message = str(caught.value)
    assert ITEM.value in message, "the refusal must name the item that holds the id"
    assert REV_1.value in message, "and the id that was reused, or it names no line to edit"
    assert "revisionId" in message, "and the field an author has to change"


def test_a_damaged_stored_item_id_is_not_reported_as_a_reused_revision_id(
    database: Path, lock: Path
) -> None:
    """The second arm of the partition the stored hash already had.

    A cell that is not an item id at all differs from the appended revision's
    item exactly as a genuinely reused id does, and it is reached on the input
    that must *succeed* -- the unchanged re-append FR-K8 requires to be a no-op.
    An author who is told to "give this operation its own revisionId" by a
    damaged database appends a duplicate revision into it.
    """
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision())

    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute(
            "UPDATE knowledge_revisions SET item_id = ? WHERE revision_id = ?",
            (SENTINEL, REV_1.value),
        )

    with (
        write_transaction(database, lock) as connection,
        pytest.raises(StateDatabaseUnreadableError) as caught,
    ):
        SqliteWriter(connection).append_revision(_revision())

    assert "theurian migrate apply" in str(caught.value), "a refusal a caller cannot act on"
    assert "revisionId" not in str(caught.value), "the reuse remedy is the harmful one here"
    assert SENTINEL not in str(caught.value), "and the cell stays inside the guard"


def test_a_damaged_item_id_under_a_content_change_reports_damage_not_a_rewrite(
    database: Path, lock: Path
) -> None:
    """The item, before the content -- the ordering the whole partition rests on.

    `_refuse_unless_it_is_the_same_revision` checks the stored `item_id` before
    the stored `content_sha256`, and the order is load-bearing because the two
    arms answer to opposite remedies. When the stored `item_id` cell is not an
    item id at all *and* the incoming body differs, the content arm -- reached
    second -- would raise `InvariantViolationError` ("write a new revision
    instead"): the immutability cure, which tells an author to append a duplicate
    into a database that is already broken. The item arm, reached first, cannot
    read the cell as an `ItemId` and answers `StateDatabaseUnreadableError`: the
    rebuild cure, which is the correct one for a damaged file.

    No other test in this suite reaches this square. The one damaged-`item_id`
    case above keeps the body identical, so the content arm's condition is false
    and the ordering never shows -- which is exactly why swapping the two arms
    (content before item) passed the whole suite. Verified: with the arms
    swapped, this test goes RED with an `InvariantViolationError`.
    """
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.append_revision(_revision(body="original"))

    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute(
            "UPDATE knowledge_revisions SET item_id = ? WHERE revision_id = ?",
            (SENTINEL, REV_1.value),
        )

    with (
        write_transaction(database, lock) as connection,
        pytest.raises(StateDatabaseUnreadableError) as caught,
    ):
        SqliteWriter(connection).append_revision(_revision(body="a rewritten body"))

    message = str(caught.value)
    assert "theurian migrate apply" in message, "a damaged cell names the rebuild remedy"
    assert "immutable" not in message, "not the immutability cure the content arm would print"
    assert "revisionId" not in message, "and not the reuse cure the item arm would print"
    assert SENTINEL not in message, "the cell stays inside the guard"


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


def test_a_state_database_written_at_version_one_is_refused(database: Path) -> None:
    """Version 1 specifically -- the version every pre-fix state database carries.

    Not ``assert SCHEMA_VERSION == 2`` and not ``SCHEMA_VERSION - 1``: this pins
    the literal 1 because that is the version a build *before* the revision-reuse
    fix stamped into its state files, and the residual-disclosure closure rests on
    exactly this refusal. A state database an affected build derived may still hold
    the withheld body; the closure holds only because such a file is refused and
    rebuilt from the Git-tracked migrations rather than read in place, and the
    ``SCHEMA_VERSION`` bump to 2 is the mechanism that forces the rebuild. So
    reverting ``SCHEMA_VERSION`` to 1 -- which a version-agnostic
    ``== SCHEMA_VERSION`` check cannot see -- would make ``open_read_connection``
    accept the poisoned file, and this test is what goes RED when it does.
    """
    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute("UPDATE schema_metadata SET schema_version = 1")

    with pytest.raises(SchemaVersionMismatchError) as exc:
        open_read_connection(database)

    assert exc.value.found == 1
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


OTHER_PROJECT: Final = ProjectId("other-service")


def test_a_revision_cannot_be_moved_out_from_under_the_item_pointing_at_it(
    database: Path, lock: Path
) -> None:
    """#24. The item -> revision pointer is scoped the way every read of it is.

    `knowledge_items.current_revision_id` used to reference `knowledge_revisions
    (revision_id)` alone, while `get_revision` and `list_revisions` both filter on
    `project_id` as well. The two never met: a revision whose `project_id` moved
    -- what a project id changing over an unchanged root does -- left the item
    pointing at a row its own project-scoped read could no longer see, and
    `PRAGMA foreign_key_check` reported the database as satisfied. Schema version
    3 makes the key composite, so SQLite refuses the move instead.

    **Both arms, because "refused" alone is satisfied by a key that refuses
    everything.** The stranding UPDATE must fail and the harmless one -- the same
    statement over a revision no item points at -- must still succeed. Revert the
    foreign key to `REFERENCES knowledge_revisions(revision_id)` and the first
    arm goes RED; widen it to something that refuses any move and the second does.

    **The destination project is registered first, and that is load-bearing
    rather than tidy.** `knowledge_revisions.project_id` also references
    `projects`, so an UPDATE to an unregistered id is refused by *that* key on
    every build this repository has ever had -- the arm would pass with the
    composite key reverted, testing nothing. Registering `other-service` leaves
    the pointer's own key as the only thing that can refuse.

    Run on a real writer connection through `write_transaction`, which is where
    the enforcement has to hold: `foreign_keys` is a per-connection pragma in
    SQLite, so a key the writer's own connection does not enforce is not enforced
    at all.
    """
    stranded = _revision(REV_2, "nothing points at this one")
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.register_project(replace(_project(), project_id=OTHER_PROJECT))
        pointed_at = _revision()
        writer.append_revision(pointed_at)
        writer.append_revision(stranded)
        writer.put_item(_item().with_revision(pointed_at))
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1, (
            "the writer's connection does not enforce foreign keys, so neither arm below "
            "measures the schema"
        )

    with (
        pytest.raises(sqlite3.IntegrityError),
        write_transaction(database, lock) as connection,
    ):
        connection.execute(
            "UPDATE knowledge_revisions SET project_id = ? WHERE revision_id = ?",
            (OTHER_PROJECT.value, REV_1.value),
        )

    with write_transaction(database, lock) as connection:
        moved = connection.execute(
            "UPDATE knowledge_revisions SET project_id = ? WHERE revision_id = ?",
            (OTHER_PROJECT.value, REV_2.value),
        ).rowcount
    assert moved == 1, (
        "a revision no item points at must still be movable, or the arm above is satisfied by a "
        "key that refuses every write to this column"
    )

    with SqliteCanonicalStore(database) as store:
        item = store.get_item(RequestContext(project_id=PROJECT), ITEM)
        assert item is not None and item.current_revision_id == REV_1
        assert store.get_revision(RequestContext(project_id=PROJECT), REV_1) is not None, (
            "the pointer must still resolve inside its own project -- the stranding this key "
            "prevents is exactly a pointer whose project-scoped read comes back empty"
        )


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


@pytest.mark.parametrize(
    ("statuses", "sensitivities", "axis"),
    [
        (frozenset[KnowledgeStatus](), frozenset(Sensitivity), "statuses"),
        (SURFACEABLE_STATUSES, frozenset[Sensitivity](), "sensitivities"),
        (frozenset[KnowledgeStatus](), frozenset[Sensitivity](), "both"),
    ],
    ids=["empty-statuses", "empty-sensitivities", "both-empty"],
)
def test_list_items_by_status_short_circuits_an_empty_set_without_a_query(
    database: Path,
    lock: Path,
    statuses: frozenset[KnowledgeStatus],
    sensitivities: frozenset[Sensitivity],
    axis: str,
) -> None:
    """SEC-13, T-17, #158, #119. An empty set on either axis: no items, via no query.

    ``list_items_by_status`` builds ``status IN (?, ?, ...)`` and ``sensitivity IN
    (?, ?, ...)`` with one placeholder per member, so an empty set on either axis
    would build ``IN ()``. The ``if not statuses or not sensitivities: return ()``
    guard short-circuits before that statement is ever assembled. Neither arm is
    reachable from ``_scan`` today -- the resolved surfaceable set always contains
    ``approved``, and ``AuthorizationGrant`` refuses at construction to hold an
    empty sensitivity set -- so nothing else exercises them: the adversarial
    reviewer mutated the original guard to ``if statuses is None:`` and the whole
    suite stayed green, because an empty ``frozenset`` is not ``None`` and falls
    straight through to build ``IN ()``.

    Both arms are parametrised, because a guard written as ``if not statuses:``
    alone passes the first case and lets the second build ``sensitivity IN ()`` --
    the exact half-fix a single-axis test cannot see.

    Why this asserts *no statement ran* and not merely *the result is empty*: on
    this SQLite build ``IN ()`` does not raise -- it evaluates to false and
    returns zero rows -- so ``result == ()`` holds under the mutation too and would
    not catch it. Verified: with the guard mutated to ``if statuses is None:`` this
    test goes RED only on the ``statements == []`` assertion, having recorded the
    ``... status IN () ...`` statement the mutant let through. The empty-result
    assertion is kept for its own sake (the contract is "no items"), but the
    statement-capture assertion is the one that is load-bearing, so it must not be
    relaxed to a bare ``result == ()``.
    """
    approved = replace(
        _item(), item_id=ItemId("architecture.approved"), status=KnowledgeStatus.APPROVED
    )
    draft = replace(_item(), item_id=ItemId("architecture.draft"), status=KnowledgeStatus.DRAFT)
    rejected = replace(
        _item(), item_id=ItemId("architecture.rejected"), status=KnowledgeStatus.REJECTED
    )
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        writer.put_item(approved)
        writer.put_item(draft)
        writer.put_item(rejected)

    # Watch the exact statements the store hands to its reader. The store is
    # entered first so its connection opens before the spy is installed -- that
    # open does not go through `_read_all`, so an empty capture means the method
    # itself issued no read.
    statements: list[str] = []
    real_read_all = SqliteCanonicalStore._read_all

    def spy(
        store: SqliteCanonicalStore,
        sql: str,
        parameters: tuple[str, ...],
        mapper: object,
    ) -> tuple[object, ...]:
        statements.append(sql)
        return real_read_all(store, sql, parameters, mapper)  # type: ignore[arg-type]

    with SqliteCanonicalStore(database) as store, pytest.MonkeyPatch.context() as patch:
        patch.setattr(SqliteCanonicalStore, "_read_all", spy)
        result = store.list_items_by_status(
            RequestContext(project_id=PROJECT), statuses=statuses, sensitivities=sensitivities
        )

    assert result == (), (
        f"an empty {axis} set must resolve to no items; the store returned {result!r} instead"
    )
    assert statements == [], (
        f"the empty {axis} set reached the store's reader, so it built `IN ()` on that "
        f"axis -- a predicate that silently matches nothing rather than raising. The "
        f"`if not statuses or not sensitivities` short-circuit is gone or covers only "
        f"one axis. Statements run: {statements!r}"
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
