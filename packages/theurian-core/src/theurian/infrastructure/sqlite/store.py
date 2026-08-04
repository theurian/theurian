"""SQLite implementation of the CanonicalStore port.

Writes happen only inside :func:`write_transaction` (ADR-0018). Reads open their
own WAL connection, so a search never blocks on a running rebuild (NFR-4, NFR-7).

Every line here that turns a stored cell into a value goes through
:func:`_reading`, which answers for the whole class of ways this file can fail to
be one -- see that function and :data:`_ALREADY_ANSWERED` for the key and why it
closes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, final

from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId, SpecId
from theurian.domain.knowledge import (
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.specification import Specification
from theurian.domain.values import (
    AclGroup,
    ContentHash,
    MediaType,
    TenantId,
    ValidityPeriod,
)
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
    open_read_connection,
)

#: What a read can raise that is *not* this file's bytes failing to be a value.
#:
#: The key :func:`_reading` applies is one question -- **does this line interpret
#: bytes that came out of this file?** -- and these three are the answers of "no"
#: that are still errors:
#:
#: - `FileNotFoundError`: there was nothing to interpret. Its message names the
#:   path the *caller* asked for, not a cell, and the remedy differs -- a state
#:   database that was never built is built by `theurian migrate apply` with
#:   nothing to delete first. Pinned by
#:   `test_a_read_session_reports_a_missing_database_when_it_is_opened`.
#: - `SchemaVersionMismatchError`: the header was interpreted *successfully* and
#:   said a number this build does not support. It already names the same remedy
#:   family and carries no cell.
#: - `StateDatabaseUnreadableError`: a nested read has already answered. Reads
#:   nest -- `get_revision`'s mapper calls `_anchors_for` -- and re-wrapping would
#:   replace a type name with `StateDatabaseUnreadableError`, which says nothing.
#:
#: Everything else is the file's fault, and that default is inverted deliberately
#: rather than enumerated. Both statements of this class that had to be redrawn
#: in the index store were enumerations of the *closed* side: a list of message
#: fragments, then a list of exception base classes. Neither could grow fast
#: enough, because the population is a boundary and not a hierarchy. Here the
#: three converter families do not even share a base -- `int` and
#: `datetime.fromisoformat` raise `ValueError`, the enums raise `ValueError`, and
#: every domain value object raises `DomainError`, which descends from
#: `TheurianError` and would have escaped a guard written over `ValueError` alone.
#: That is precisely how the `content_type` face of the measurement above got
#: out.
_ALREADY_ANSWERED: Final = (
    FileNotFoundError,
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
)


@contextmanager
def _reading() -> Iterator[None]:
    """One statement and the values it produces, with every failure mapped.

    **The block is the unit, not the converter.** An exception raised inside a
    ``with _reading()`` body is thrown into this generator at the ``yield``, so
    acquiring the connection, executing the statement and mapping the rows are
    covered by one guard. That matters because the interpretation is spread
    across all three: `open_read_connection` runs the PRAGMA loop, where
    `sqlite3` decodes SQLite's own error text and a corrupt schema makes that
    text invalid UTF-8, and then `int()`s the stored schema version.

    **Nothing goes inside a block but those three, and here that is structural
    rather than a convention.** :meth:`SqliteCanonicalStore._read_one` and
    :meth:`~SqliteCanonicalStore._read_all` take a statement and a mapper and are
    the only way this class reads, so a read added later cannot escape the guard
    by forgetting it -- the equivalent guard in the index store depends on the
    next reader keeping a rule, and that is the difference between the two.

    **The cost, stated rather than discovered later.** A genuine programming
    error inside such a block -- a mistyped column name, an argument count -- is
    now reported as an unreadable state database. It is the better of two wrong
    answers: this one names a remedy that costs seconds and loses nothing, and
    `raise ... from exc` keeps the real cause for whoever has the traceback,
    while a `ValueError` reaching an agent names no remedy and repeats forever.

    **Reads only, in both classes.** :class:`SqliteWriter` guards the three
    places it reads a stored value and nothing else: an `INSERT` interprets the
    caller's domain objects, not this file, and reporting a constraint violation
    as an unreadable database would name the wrong cause. The index store draws
    the same line between reads and writes for a reason that does *not* transfer
    -- it withholds the guard from writes because "rebuild your index" is the
    wrong remedy mid-build -- so the line is drawn here by the key instead, and
    it happens to fall in the same place.
    """
    try:
        yield
    except _ALREADY_ANSWERED:
        raise
    except Exception as exc:
        raise StateDatabaseUnreadableError(type(exc).__name__) from exc


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _opt_dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


@final
class SqliteCanonicalStore:
    """Reads canonical state from one state database.

    Read-only by construction: every write goes through
    :class:`SqliteWriter`, which requires an open write transaction. Splitting
    them means a caller cannot write by accident, and the single-writer rule is
    visible in the type rather than in a comment.
    """

    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._connection: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = open_read_connection(self._path)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> SqliteCanonicalStore:
        # Opened here rather than at the first read, and that is a security
        # decision rather than symmetry with `__exit__`.
        #
        # `CanonicalVisibility.cleared` is a comprehension over the retriever's
        # rows, so a query that matched nothing never calls `get_item`, never
        # calls `_conn`, and never opens this connection. The ~0.4 ms of
        # `sqlite3.connect` plus the pragmas plus the schema-version check was
        # therefore charged to exactly those requests that *found* something —
        # and when the response says `count: 0`, that bit says "everything it
        # found is something you may not read".
        #
        # Measured on a 61-document Japanese corpus, 600 interleaved calls: one
        # `knowledge.search` against a probe query classified correctly 88.3% of
        # the time versus a control one character away, +0.60 ms at the median.
        # Six characters of a credential no response contains came back in 836
        # ordinary calls with the response body never read. Opening here takes
        # the same measurement to 57.8%, which is chance.
        #
        # Guarded, because opening is already an interpretation of this file:
        # `open_read_connection` runs the PRAGMA loop -- where `sqlite3` decodes
        # SQLite's own error text, which a corrupt schema makes invalid UTF-8 --
        # and then `int()`s the stored schema version. Both fail here rather
        # than at a read, so a guard confined to the reads would miss them.
        with _reading():
            self._conn()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- Reading ----------------------------------------------------------

    def _read_one[T](
        self, sql: str, parameters: Sequence[str], mapper: Callable[[sqlite3.Row], T]
    ) -> T | None:
        """One row, mapped inside the guard that answers for this file.

        ``parameters`` is ``Sequence[str]`` rather than the ``list[Any]`` that
        used to sit in `list_items`: every value this class binds is already a
        string, and saying so removes an `Any` from the module.
        """
        with _reading():
            row = self._conn().execute(sql, parameters).fetchone()
            return None if row is None else mapper(row)

    def _read_all[T](
        self, sql: str, parameters: Sequence[str], mapper: Callable[[sqlite3.Row], T]
    ) -> tuple[T, ...]:
        """Every row, mapped inside the guard.

        ``tuple(...)`` rather than a generator returned to the caller: it forces
        the mapping while the guard is still on the stack. A generator would be
        consumed after the ``with`` had exited, which is exactly how the index
        store's first attempt at this left every conversion uncovered.
        """
        with _reading():
            rows = self._conn().execute(sql, parameters).fetchall()
            return tuple(mapper(row) for row in rows)

    # -- Projects ---------------------------------------------------------

    def get_project(self, project_id: ProjectId) -> Project | None:
        return self._read_one(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id.value,),
            _project_from_row,
        )

    def list_projects(self) -> tuple[Project, ...]:
        return self._read_all("SELECT * FROM projects ORDER BY project_id", (), _project_from_row)

    # -- Knowledge --------------------------------------------------------

    def get_item(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        resolved = self._resolve_alias(context.project_id, item_id)
        return self._read_one(
            "SELECT * FROM knowledge_items WHERE project_id = ? AND item_id = ?",
            (context.project_id.value, resolved.value),
            _item_from_row,
        )

    def _resolve_alias(self, project_id: ProjectId, item_id: ItemId) -> ItemId:
        alias = self._read_one(
            "SELECT item_id FROM knowledge_aliases WHERE project_id = ? AND alias = ?",
            (project_id.value, item_id.value),
            lambda row: ItemId(row["item_id"]),
        )
        return item_id if alias is None else alias

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None:
        return self._read_one(
            "SELECT * FROM knowledge_revisions WHERE project_id = ? AND revision_id = ?",
            (context.project_id.value, revision_id.value),
            lambda row: _revision_from_row(row, self._anchors_for(revision_id)),
        )

    def _anchors_for(self, revision_id: RevisionId) -> tuple[SourceAnchor, ...]:
        return self._read_all(
            "SELECT * FROM source_anchors WHERE revision_id = ? ORDER BY anchor_id",
            (revision_id.value,),
            _anchor_from_row,
        )

    def list_revisions(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRevision, ...]:
        resolved = self._resolve_alias(context.project_id, item_id)
        return self._read_all(
            "SELECT * FROM knowledge_revisions WHERE project_id = ? AND item_id = ? "
            "ORDER BY revision_id",
            (context.project_id.value, resolved.value),
            lambda row: _revision_from_row(row, self._anchors_for(RevisionId(row["revision_id"]))),
        )

    def list_items(
        self,
        context: RequestContext,
        *,
        namespace: str | None = None,
        current_at: datetime | None = None,
    ) -> tuple[KnowledgeItem, ...]:
        sql = "SELECT * FROM knowledge_items WHERE project_id = ?"
        params: list[str] = [context.project_id.value]
        if namespace is not None:
            sql += " AND namespace = ?"
            params.append(namespace)
        if current_at is not None:
            # Half-open window, matching ValidityPeriod.contains.
            sql += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([current_at.isoformat(), current_at.isoformat()])
        sql += " ORDER BY item_id"
        return self._read_all(sql, params, _item_from_row)

    def list_relations(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRelation, ...]:
        """Relations touching ``item_id`` in either direction.

        Only one direction is stored; the inverse is synthesised so a caller
        never has to know which way an author happened to write it.
        """
        resolved = self._resolve_alias(context.project_id, item_id)
        stored = self._read_all(
            "SELECT * FROM knowledge_relations WHERE project_id = ? "
            "AND (source_item_id = ? OR target_item_id = ?) "
            "ORDER BY source_item_id, relation_type, target_item_id",
            (context.project_id.value, resolved.value, resolved.value),
            _relation_from_row,
        )

        # Outside the guard deliberately: this loop reads `INVERSE_RELATIONS`
        # and already-constructed domain objects, never a cell, so a failure
        # here would be a domain bug and must not be reported as a damaged file.
        relations: list[KnowledgeRelation] = []
        for relation in stored:
            if relation.source_item_id == resolved:
                relations.append(relation)
            elif (inverse := relation.inverse) is not None:
                relations.append(inverse)
            else:
                relations.append(relation)
        return tuple(relations)

    def list_aliases(self, context: RequestContext) -> tuple[KnowledgeAlias, ...]:
        return self._read_all(
            "SELECT * FROM knowledge_aliases WHERE project_id = ? ORDER BY alias",
            (context.project_id.value,),
            _alias_from_row,
        )

    def list_evidence(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeEvidence, ...]:
        return self._read_all(
            "SELECT * FROM knowledge_evidence WHERE project_id = ? AND item_id = ? "
            "ORDER BY evidence_id",
            (context.project_id.value, item_id.value),
            _evidence_from_row,
        )

    # -- Specifications ---------------------------------------------------

    def get_specification(self, context: RequestContext, spec_id: SpecId) -> Specification | None:
        return self._read_one(
            "SELECT * FROM specifications WHERE project_id = ? AND spec_id = ?",
            (context.project_id.value, spec_id.value),
            _specification_from_row,
        )

    def list_specifications(self, context: RequestContext) -> tuple[Specification, ...]:
        return self._read_all(
            "SELECT * FROM specifications WHERE project_id = ? ORDER BY spec_id",
            (context.project_id.value,),
            _specification_from_row,
        )

    # -- Migration history ------------------------------------------------

    def applied_migrations(self, project_id: ProjectId) -> tuple[tuple[MigrationId, str], ...]:
        return self._read_all(
            "SELECT migration_id, checksum FROM migration_history WHERE project_id = ? "
            "ORDER BY sequence",
            (project_id.value,),
            _applied_migration_from_row,
        )


@final
class SqliteWriter:
    """Append-only writes, valid only inside an open write transaction.

    Constructed from a connection that the caller obtained via
    ``write_transaction``. There is no way to build one otherwise, so the
    single-writer guarantee cannot be sidestepped by reaching for this class.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # -- Projects ---------------------------------------------------------

    def register_project(self, project: Project) -> None:
        self._conn.execute(
            "INSERT INTO projects (project_id, root_path, repository_url, default_branch, "
            "knowledge_directory, tenant_id, registered_at, last_seen_commit) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "  root_path = excluded.root_path, "
            "  repository_url = excluded.repository_url, "
            "  default_branch = excluded.default_branch, "
            "  knowledge_directory = excluded.knowledge_directory, "
            "  last_seen_commit = excluded.last_seen_commit",
            (
                project.project_id.value,
                project.root_path,
                project.repository_url,
                project.default_branch,
                str(project.knowledge_directory),
                project.tenant_id.value,
                project.registered_at.isoformat(),
                project.last_seen_commit,
            ),
        )

    def unregister_project(self, project_id: ProjectId) -> None:
        """Remove a registration. Cascades to derived rows only.

        Git-tracked content under ``.theurian/`` is untouched -- this store holds
        a projection of it, never the original (ADR-0004).
        """
        self._conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id.value,))

    # -- Knowledge --------------------------------------------------------

    def append_revision(self, revision: KnowledgeRevision) -> None:
        """Append an immutable revision (INV-1).

        Raises:
            InvariantViolationError: If the id exists with different content.
                Re-appending the *identical* revision is allowed, because
                re-applying a migration must be a no-op (FR-K8).
            StateDatabaseUnreadableError: If the stored hash differs because it
                is not a hash. Distinguished from the above because the two
                remedies disagree -- see the comment on the comparison.
        """
        existing = self._conn.execute(
            "SELECT content_sha256 FROM knowledge_revisions WHERE revision_id = ?",
            (revision.revision_id.value,),
        ).fetchone()
        if existing is not None:
            stored: str = existing["content_sha256"]
            if stored != revision.content_sha256.value:
                # Two states produce this mismatch and their cures are opposite:
                # an author rewriting a revision, and a damaged cell. Only the
                # first is INV-1, and telling the second to "write a new revision
                # instead" appends a duplicate into a database that is already
                # broken -- reached by re-applying an *unchanged* migration,
                # which FR-K8 requires to be a no-op.
                #
                # The comparison itself stays a comparison of opaque strings, so
                # the guard's key -- does this line interpret bytes that came out
                # of this file? -- is answered "no" everywhere but here. Only the
                # question of *why* the comparison failed is an interpretation,
                # and it is asked only once the answer changes what is raised.
                with _reading():
                    ContentHash(stored)
                raise InvariantViolationError(
                    f"Revision {revision.revision_id} already exists with different content. "
                    f"Revisions are immutable; write a new revision instead."
                )
            return

        metadata = revision.metadata
        self._conn.execute(
            "INSERT INTO knowledge_revisions (revision_id, item_id, project_id, migration_id, "
            "title, body, content_type, content_sha256, kind, namespace, status, trust_level, "
            "sensitivity, owner, tenant_id, acl_group, labels, scope_paths, structured, "
            "valid_from, valid_to, author, created_at, source_commit) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision.revision_id.value,
                revision.item_id.value,
                revision.project_id.value,
                revision.migration_id.value,
                revision.title,
                revision.body,
                str(revision.content_type),
                revision.content_sha256.value,
                metadata.kind.value,
                metadata.namespace,
                metadata.status.value,
                metadata.trust_level.value,
                metadata.sensitivity.value,
                metadata.owner,
                metadata.tenant_id.value,
                metadata.acl_group.value,
                json.dumps(list(metadata.labels)),
                json.dumps(list(metadata.scope_paths)),
                None if revision.structured is None else json.dumps(revision.structured),
                revision.validity.valid_from.isoformat(),
                None
                if revision.validity.valid_to is None
                else revision.validity.valid_to.isoformat(),
                revision.author,
                revision.created_at.isoformat(),
                revision.source_commit,
            ),
        )

        for anchor in revision.source_anchors:
            self._insert_anchor(revision.project_id, revision.revision_id, anchor)

    def _insert_anchor(
        self, project_id: ProjectId, revision_id: RevisionId, anchor: SourceAnchor
    ) -> None:
        self._conn.execute(
            "INSERT INTO source_anchors (revision_id, project_id, provider, source_uri, "
            "repository, commit_sha, blob_sha, file_path, line_start, line_end, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revision_id.value,
                project_id.value,
                anchor.provider,
                anchor.source_uri,
                anchor.repository,
                anchor.commit_sha,
                anchor.blob_sha,
                anchor.file_path,
                anchor.line_start,
                anchor.line_end,
                anchor.external_id,
            ),
        )

    def put_item(self, item: KnowledgeItem) -> None:
        self._conn.execute(
            "INSERT INTO knowledge_items (item_id, project_id, namespace, kind, status, "
            "current_revision_id, owner, trust_level, sensitivity, tenant_id, acl_group, "
            "valid_from, valid_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, item_id) DO UPDATE SET "
            "  namespace = excluded.namespace, kind = excluded.kind, status = excluded.status, "
            "  current_revision_id = excluded.current_revision_id, owner = excluded.owner, "
            "  trust_level = excluded.trust_level, sensitivity = excluded.sensitivity, "
            "  tenant_id = excluded.tenant_id, acl_group = excluded.acl_group, "
            "  valid_from = excluded.valid_from, valid_to = excluded.valid_to",
            (
                item.item_id.value,
                item.project_id.value,
                item.namespace,
                item.kind.value,
                item.status.value,
                None if item.current_revision_id is None else item.current_revision_id.value,
                item.owner,
                item.trust_level.value,
                item.sensitivity.value,
                item.tenant_id.value,
                item.acl_group.value,
                item.validity.valid_from.isoformat(),
                None if item.validity.valid_to is None else item.validity.valid_to.isoformat(),
            ),
        )

    def add_relation(self, relation: KnowledgeRelation) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO knowledge_relations (project_id, source_item_id, "
            "relation_type, target_item_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                relation.project_id.value,
                relation.source_item_id.value,
                relation.relation_type.value,
                relation.target_item_id.value,
                relation.note,
                relation.created_at.isoformat(),
            ),
        )

    def remove_relation(self, relation: KnowledgeRelation) -> None:
        self._conn.execute(
            "DELETE FROM knowledge_relations WHERE project_id = ? AND source_item_id = ? "
            "AND relation_type = ? AND target_item_id = ?",
            (
                relation.project_id.value,
                relation.source_item_id.value,
                relation.relation_type.value,
                relation.target_item_id.value,
            ),
        )

    def add_alias(self, alias: KnowledgeAlias) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO knowledge_aliases (alias, item_id, project_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                alias.alias.value,
                alias.item_id.value,
                alias.project_id.value,
                alias.created_at.isoformat(),
            ),
        )

    def remove_alias(self, project_id: ProjectId, alias: ItemId) -> None:
        self._conn.execute(
            "DELETE FROM knowledge_aliases WHERE project_id = ? AND alias = ?",
            (project_id.value, alias.value),
        )

    def add_evidence(self, evidence: KnowledgeEvidence) -> None:
        anchor = evidence.anchor
        self._conn.execute(
            "INSERT OR REPLACE INTO knowledge_evidence (project_id, item_id, provider, "
            "source_uri, repository, commit_sha, file_path, line_start, line_end, external_id, "
            "description, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                evidence.project_id.value,
                evidence.item_id.value,
                anchor.provider,
                anchor.source_uri,
                anchor.repository,
                anchor.commit_sha,
                anchor.file_path,
                anchor.line_start,
                anchor.line_end,
                anchor.external_id,
                evidence.description,
                evidence.confidence,
                evidence.created_at.isoformat(),
            ),
        )

    def remove_evidence(self, project_id: ProjectId, item_id: ItemId, source_uri: str) -> None:
        self._conn.execute(
            "DELETE FROM knowledge_evidence WHERE project_id = ? AND item_id = ? "
            "AND source_uri = ?",
            (project_id.value, item_id.value, source_uri),
        )

    # -- Specifications ---------------------------------------------------

    def register_specification(self, specification: Specification) -> None:
        self._conn.execute(
            "INSERT INTO specifications (spec_id, project_id, revision_id, title, status, "
            "content_format, source_uri, structured, valid_from, valid_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, spec_id) DO UPDATE SET "
            "  revision_id = excluded.revision_id, title = excluded.title, "
            "  status = excluded.status, content_format = excluded.content_format, "
            "  source_uri = excluded.source_uri, structured = excluded.structured, "
            "  valid_from = excluded.valid_from, valid_to = excluded.valid_to",
            (
                specification.spec_id.value,
                specification.project_id.value,
                specification.revision_id.value,
                specification.title,
                specification.status.value,
                str(specification.content_format),
                specification.source_uri,
                json.dumps(specification.structured),
                specification.validity.valid_from.isoformat(),
                None
                if specification.validity.valid_to is None
                else specification.validity.valid_to.isoformat(),
            ),
        )

    def supersede_specification(
        self, project_id: ProjectId, spec_id: SpecId, superseded_by: SpecId
    ) -> None:
        self._conn.execute(
            "UPDATE specifications SET status = ?, superseded_by = ? "
            "WHERE project_id = ? AND spec_id = ?",
            (
                SpecificationStatus.SUPERSEDED.value,
                superseded_by.value,
                project_id.value,
                spec_id.value,
            ),
        )

    # -- Migration history ------------------------------------------------

    def record_migration(
        self,
        project_id: ProjectId,
        migration_id: MigrationId,
        checksum: str,
        applied_at: datetime,
    ) -> None:
        # `int()` over a stored aggregate is an interpretation of this file, so
        # it is guarded like any other -- a TEXT `sequence` cell puts itself into
        # the `ValueError` it raises. The INSERT below is *not* guarded: it
        # interprets the caller's arguments, and a constraint violation reported
        # as a damaged database would name the wrong cause.
        with _reading():
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS s "
                "FROM migration_history WHERE project_id = ?",
                (project_id.value,),
            ).fetchone()
            sequence = int(row["s"]) + 1

        self._conn.execute(
            "INSERT OR REPLACE INTO migration_history "
            "(migration_id, project_id, checksum, applied_at, sequence) VALUES (?, ?, ?, ?, ?)",
            (
                migration_id.value,
                project_id.value,
                checksum,
                applied_at.isoformat(),
                sequence,
            ),
        )

    def applied_migrations(self, project_id: ProjectId) -> tuple[tuple[MigrationId, str], ...]:
        with _reading():
            rows = self._conn.execute(
                "SELECT migration_id, checksum FROM migration_history WHERE project_id = ? "
                "ORDER BY sequence",
                (project_id.value,),
            ).fetchall()
            return tuple(_applied_migration_from_row(row) for row in rows)

    def get_item(self, project_id: ProjectId, item_id: ItemId) -> KnowledgeItem | None:
        """Read an item inside the write transaction.

        Needed for ``expectedRevision`` checks, which must observe the state as
        it is *within* this transaction rather than as a reader outside it sees
        it (ADR-0006).

        Guarded despite living on the writer: this is a read, and the key asks
        what a line interprets rather than which transaction it sits in.
        """
        with _reading():
            row = self._conn.execute(
                "SELECT * FROM knowledge_items WHERE project_id = ? AND item_id = ?",
                (project_id.value, item_id.value),
            ).fetchone()
            return None if row is None else _item_from_row(row)


# -- Row mapping ----------------------------------------------------------
#
# Every function below runs inside a `_reading()` block and nowhere else. They
# are named and module-level rather than inlined as lambdas so that the guard's
# coverage is a property of *where they are called*, which is one place each.


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        project_id=ProjectId(row["project_id"]),
        root_path=row["root_path"],
        repository_url=row["repository_url"],
        default_branch=row["default_branch"],
        knowledge_directory=PurePosixPath(row["knowledge_directory"]),
        registered_at=_dt(row["registered_at"]),
        last_seen_commit=row["last_seen_commit"],
        tenant_id=TenantId(row["tenant_id"]),
    )


def _item_from_row(row: sqlite3.Row) -> KnowledgeItem:
    current = row["current_revision_id"]
    return KnowledgeItem(
        item_id=ItemId(row["item_id"]),
        project_id=ProjectId(row["project_id"]),
        namespace=row["namespace"],
        kind=KnowledgeKind(row["kind"]),
        status=KnowledgeStatus(row["status"]),
        current_revision_id=None if current is None else RevisionId(current),
        owner=row["owner"],
        trust_level=TrustLevel(row["trust_level"]),
        sensitivity=Sensitivity(row["sensitivity"]),
        validity=ValidityPeriod(_dt(row["valid_from"]), _opt_dt(row["valid_to"])),
        tenant_id=TenantId(row["tenant_id"]),
        acl_group=AclGroup(row["acl_group"]),
    )


def _revision_from_row(row: sqlite3.Row, anchors: tuple[SourceAnchor, ...]) -> KnowledgeRevision:
    structured = row["structured"]
    return KnowledgeRevision(
        revision_id=RevisionId(row["revision_id"]),
        item_id=ItemId(row["item_id"]),
        project_id=ProjectId(row["project_id"]),
        migration_id=MigrationId(row["migration_id"]),
        title=row["title"],
        body=row["body"],
        content_type=MediaType(row["content_type"]),
        # Verified against the body by KnowledgeRevision.__post_init__ (INV-3),
        # so a tampered stored hash is caught on read, not trusted.
        content_sha256=ContentHash(row["content_sha256"]),
        metadata=RevisionMetadata(
            kind=KnowledgeKind(row["kind"]),
            namespace=row["namespace"],
            status=KnowledgeStatus(row["status"]),
            trust_level=TrustLevel(row["trust_level"]),
            sensitivity=Sensitivity(row["sensitivity"]),
            owner=row["owner"],
            tenant_id=TenantId(row["tenant_id"]),
            acl_group=AclGroup(row["acl_group"]),
            scope_paths=tuple(json.loads(row["scope_paths"])),
            labels=tuple(json.loads(row["labels"])),
        ),
        validity=ValidityPeriod(_dt(row["valid_from"]), _opt_dt(row["valid_to"])),
        author=row["author"],
        created_at=_dt(row["created_at"]),
        source_commit=row["source_commit"],
        source_anchors=anchors,
        structured=None if structured is None else json.loads(structured),
    )


def _anchor_from_row(row: sqlite3.Row) -> SourceAnchor:
    keys = row.keys()
    return SourceAnchor(
        provider=row["provider"],
        source_uri=row["source_uri"],
        repository=row["repository"] if "repository" in keys else None,
        commit_sha=row["commit_sha"] if "commit_sha" in keys else None,
        blob_sha=row["blob_sha"] if "blob_sha" in keys else None,
        file_path=row["file_path"] if "file_path" in keys else None,
        line_start=row["line_start"] if "line_start" in keys else None,
        line_end=row["line_end"] if "line_end" in keys else None,
        external_id=row["external_id"] if "external_id" in keys else None,
    )


def _alias_from_row(row: sqlite3.Row) -> KnowledgeAlias:
    return KnowledgeAlias(
        alias=ItemId(row["alias"]),
        item_id=ItemId(row["item_id"]),
        project_id=ProjectId(row["project_id"]),
        created_at=_dt(row["created_at"]),
    )


def _evidence_from_row(row: sqlite3.Row) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        item_id=ItemId(row["item_id"]),
        project_id=ProjectId(row["project_id"]),
        anchor=_anchor_from_row(row),
        description=row["description"],
        confidence=float(row["confidence"]),
        created_at=_dt(row["created_at"]),
    )


def _applied_migration_from_row(row: sqlite3.Row) -> tuple[MigrationId, str]:
    # Constructed rather than returned as the `str` it used to be, and the
    # construction is the whole of it: the only line in this file that read a
    # cell without interpreting it was the only line whose cell escaped.
    # `migration_history.checksum` is compared against a file's hash and then
    # rendered into `MigrationChecksumMismatchError`'s message, so a damaged one
    # travelled to the operator as data -- `theurian migrate status --json`
    # answered `{"error": "Migration 01K1... was applied with checksum ROTATE-ME
    # sk-live-... but the file on disk hashes to 744a5080..."}`.
    #
    # `ContentHash` is what the value already is on the way in: the only writer
    # is `MigrationEngine.apply`, passing `migration.checksum.value`. Refusing
    # anything else on the way out costs a regex over 64 characters and puts
    # this cell inside `_reading` with the rest.
    checksum = ContentHash(row["checksum"])
    return MigrationId(row["migration_id"]), checksum.value


def _relation_from_row(row: sqlite3.Row) -> KnowledgeRelation:
    return KnowledgeRelation(
        project_id=ProjectId(row["project_id"]),
        source_item_id=ItemId(row["source_item_id"]),
        relation_type=RelationType(row["relation_type"]),
        target_item_id=ItemId(row["target_item_id"]),
        created_at=_dt(row["created_at"]),
        note=row["note"],
    )


def _specification_from_row(row: sqlite3.Row) -> Specification:
    return Specification(
        spec_id=SpecId(row["spec_id"]),
        project_id=ProjectId(row["project_id"]),
        revision_id=RevisionId(row["revision_id"]),
        title=row["title"],
        status=SpecificationStatus(row["status"]),
        content_format=MediaType(row["content_format"]),
        source_uri=row["source_uri"],
        validity=ValidityPeriod(_dt(row["valid_from"]), _opt_dt(row["valid_to"])),
        structured=json.loads(row["structured"]),
    )
