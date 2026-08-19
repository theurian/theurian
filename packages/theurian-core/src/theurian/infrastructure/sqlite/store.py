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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, final

from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    SURFACEABLE_STATUSES,
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
    r"""One statement and the values it produces, with every failure mapped.

    **The block is the unit, not the converter.** An exception raised inside a
    ``with _reading()`` body is thrown into this generator at the ``yield``, so
    acquiring the connection, executing the statement and mapping the rows are
    covered by one guard. That matters because the interpretation is spread
    across all three: `open_read_connection` runs the PRAGMA loop, where
    `sqlite3` decodes SQLite's own error text and a corrupt schema makes that
    text invalid UTF-8, and then `int()`s the stored schema version.

    **Nothing goes inside a block but those three, and at `c7d59b4` every read
    on this class goes through one -- which nothing enforces.**
    :meth:`SqliteCanonicalStore._read_one` and
    :meth:`~SqliteCanonicalStore._read_all` take a statement and a mapper, and
    every read is written in terms of them today, so a read added later has a
    helper to reach for rather than a rule to recall. That is the whole of the
    difference from the index store's guard: :meth:`~SqliteCanonicalStore._conn`
    is still a method on the class, and a new one that calls it directly
    type-checks and lints. It has happened -- ``git grep -c 'self\._conn()'
    67a792c -- '*sqlite/store.py'`` is 15, in a revision of this file with no
    `_reading` in it at all.

    An earlier version of this paragraph said "structural rather than a
    convention", and three reviewers falsified it. Making it structural means an
    AST test over the call sites, in the shape of
    `tests/unit/test_gate_call_sites.py`. Until one exists, the sentence above is
    a count at a commit and must be read as one.

    **The cost, stated rather than discovered later.** A genuine programming
    error inside such a block -- a mistyped column name, an argument count -- is
    now reported as an unreadable state database. It is the better of two wrong
    answers: this one names a remedy that costs seconds and loses nothing, and
    `raise ... from exc` keeps the real cause for whoever has the traceback,
    while a `ValueError` reaching an agent names no remedy and repeats forever.

    **Reads only, in both classes -- and on the writer, interpretations rather
    than reads.** :class:`SqliteWriter` reads a stored value in four places --
    `record_migration`, `applied_migrations`, `get_item` and `append_revision`
    -- and guards three of them; an `INSERT` interprets the caller's domain
    objects, not this file, and reporting a constraint violation as an
    unreadable database would name the wrong cause. The fourth read is
    `append_revision`'s ``SELECT item_id, content_sha256``, left outside a block
    deliberately: past ``BEGIN IMMEDIATE`` a failure is the caller's write
    against the caller's data, and "delete `.theurian/state/`" is
    a destructive remedy for a write that simply did not apply. Only the
    question of *why* one of those two comparisons failed is an interpretation,
    and each of those lines is guarded. Both arms are held by
    `test_a_writers_read_of_a_damaged_cell_answers_without_quoting_it` and
    `test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state`.

    The index store draws the same line between reads and writes for a reason
    that does *not* transfer -- it withholds the guard from writes because
    "rebuild your index" is the wrong remedy mid-build -- so the line is drawn
    here by the key instead, and it happens to fall in nearly the same place.
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
        self, sql: str, parameters: tuple[str, ...], mapper: Callable[[sqlite3.Row], T]
    ) -> T | None:
        """One row, mapped inside the guard that answers for this file.

        ``parameters`` is a tuple rather than the ``list[Any]`` that used to sit
        in `list_items`: every value this class binds is already a string, and
        saying so removes an `Any` from the module.

        **Not ``Sequence[str]``, which was the first attempt at that and did not
        exclude the mistake it was introduced to exclude.** `str` is itself a
        `Sequence[str]`, so a forgotten comma type-checked under `mypy --strict`
        and bound one parameter per *character*. Measured on SQLite 3.51.2: a
        four-character value against one placeholder raises `ProgrammingError:
        Incorrect number of bindings supplied. The current statement uses 1, and
        there are 4 supplied` -- inside :func:`_reading`, so a caller was told
        their state database was damaged -- and a one-character value against one
        placeholder raises nothing at all and answers with the wrong rows.
        """
        with _reading():
            row = self._conn().execute(sql, parameters).fetchone()
            return None if row is None else mapper(row)

    def _read_all[T](
        self, sql: str, parameters: tuple[str, ...], mapper: Callable[[sqlite3.Row], T]
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

    def get_item_exact(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        """The row ``item_id`` literally names, with no `_resolve_alias` (T-21).

        `get_item` above resolves an alias before this same read, which is right
        for *reaching* a renamed item but wrong for deciding whether a
        *referenced* id may surface. An `addAlias` key is an author-chosen string:
        a key equal to a `rejected` item's id resolves through `get_item` to the
        approved item it points at, so a gate keyed on the resolved status clears
        as that approved item and publishes the rejected item's content.
        `_relation_is_visible` reads each endpoint through this instead -- the
        row the id names, judged by its own status.
        """
        return self._read_one(
            "SELECT * FROM knowledge_items WHERE project_id = ? AND item_id = ?",
            (context.project_id.value, item_id.value),
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

    def current_revision(
        self, context: RequestContext, item: KnowledgeItem
    ) -> KnowledgeRevision | None:
        """Dereference ``item``'s pointer, refusing a revision that is not its own.

        **The read-side half of the pointer invariant, and the only half that
        answers for a database nothing in this process wrote.**
        :meth:`SqliteWriter._refuse_pointer_to_another_items_revision` stops this
        store *recording* a ``current_revision_id`` that names a sibling item's
        revision; it cannot stop a value already on the page from being one.
        Every writer path in this build goes through that guard, and a state
        database is a derived, unsigned, git-ignored file (ADR-0004, SEC-7) that
        any local process can edit, so "no writer of ours produced this" is not
        the same statement as "this cannot be read back".

        A foreign pointer is the corruption with no structural evidence at all: a
        sibling's revision id is type-valid, satisfies the composite foreign key
        `(project_id, current_revision_id)`, leaves ``PRAGMA foreign_key_check``
        empty, and moves neither ``#30`` integrity count -- the row keeps its
        `project_id` and its `status`, so it stays inside both counted scopes.
        Measured before this guard existed, one ``UPDATE`` of that one cell made
        ``knowledge.get`` publish a `rejected` revision's title and full body
        under ``status: 'approved'`` with no ``integrity`` key.

        Reported as a damaged state database rather than as a missing item.
        ``None`` here would hand a caller "not present" for a row that is present
        and unreadable, which is the gap
        ``tests/integration/test_canonical_store_corruption.py`` records for the
        `item_id` cell; there the row genuinely cannot be located, while here it
        can, and its content is exactly what must not be served.
        :class:`~theurian.infrastructure.sqlite.connection.StateDatabaseUnreadableError`
        is that answer, and it is the same one this cell already produces when it
        holds something that is not an id at all -- so the two damage shapes are
        indistinguishable to a caller, which is what stops the refusal itself
        becoming a bit about which one happened.

        One additional indexed lookup per dereference at worst, and usually none:
        the revision row is the one this method was going to fetch anyway.
        """
        if item.current_revision_id is None:
            return None
        revision = self.get_revision(context, item.current_revision_id)
        # Inside the guard because deciding this *is* an interpretation of bytes
        # that came out of this file -- the same key :func:`_reading` applies
        # everywhere else in this class -- and because the conversion is what
        # keeps the ids out of the message a caller sees. The
        # `InvariantViolationError` below names no cell and no id even so: it
        # travels on ``__cause__``, and Typer renders the whole chain to whoever
        # runs the CLI.
        with _reading():
            if revision is not None and not item.owns(revision):
                msg = (
                    "A knowledge_items.current_revision_id names a revision that belongs to "
                    "another item, so the item and the revision disagree about which item "
                    "this is."
                )
                raise InvariantViolationError(msg)
        return revision

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
    ) -> tuple[KnowledgeItem, ...]:
        # No `current_at` here -- see the port's docstring
        # (`domain/ports/canonical_store.py`) for why a SQL-side comparison of
        # `valid_from`/`valid_to` against a bound moment was removed rather
        # than fixed: it compared them as SQLite TEXT, which is a
        # lexicographic ordering of the ISO-8601 string rather than of the
        # absolute instant it names, and it silently disagreed with
        # `ValidityPeriod.contains` whenever the two sides were authored in
        # different UTC offsets.
        sql = "SELECT * FROM knowledge_items WHERE project_id = ?"
        params: tuple[str, ...] = (context.project_id.value,)
        if namespace is not None:
            sql += " AND namespace = ?"
            params += (namespace,)
        sql += " ORDER BY item_id"
        return self._read_all(sql, params, _item_from_row)

    def list_items_by_status(
        self, context: RequestContext, *, statuses: frozenset[KnowledgeStatus]
    ) -> tuple[KnowledgeItem, ...]:
        # A status-filtered read with no visibility semantics. The caller passes the
        # status set it has already resolved, so the `may_surface` gate stays in the
        # tool layer (`search._scan`) where the security enumeration expects it and
        # this adapter never consults it. `knowledge.search`'s substring fallback is
        # the caller; filtering in SQL is what keeps its response time proportional
        # to the rows it may return rather than to the retired rows it withholds --
        # the `search._scan` sibling of the channel #19 closed for `knowledge.status`
        # (T-17, SEC-13, #158).
        #
        # An empty set short-circuits to `()`: no status can match, so a query would
        # only return zero rows. The guard is defensive -- `search._scan` always
        # resolves at least APPROVED into the set
        # (`may_surface(APPROVED, include_unapproved=False)` is always true), so it
        # never passes an empty one. `sorted()` only fixes the bind order: the
        # statement text is `?, ?, ?` regardless of the values.
        if not statuses:
            return ()
        values = tuple(sorted(s.value for s in statuses))
        placeholders = ", ".join("?" for _ in values)
        # `INDEXED BY idx_items_status`, not left to the planner, and that hint is
        # the whole of what makes this timing-independent. `idx_items_status` is
        # `(project_id, status)`; the primary key gives a second index
        # `(project_id, item_id)`. `ORDER BY item_id` -- the order the substring
        # scan's `limit` cutoff depends on, so the result set stays identical to the
        # `list_items` path #158 replaced -- makes the planner *prefer* the primary
        # key, because it satisfies the sort with no temp b-tree. Preferring it
        # means seeking on `project_id` alone and reading every row of the project,
        # the discarded ones included, to apply `status` as a post-filter: measured
        # VM steps then grew 75 -> 325 -> 1575 across 0/50/300 filtered-out rows,
        # which is the O(withheld) channel this read exists to close (T-17, SEC-13,
        # #158). Forcing `idx_items_status` seeks straight to the in-set rows and
        # sorts only those in a bounded temp b-tree: 118 -> 119 -> 119, flat. The
        # force is also structural, not advisory -- drop or rename the index and the
        # query fails loudly rather than silently falling back to the scan that
        # reopens the channel.
        sql = (
            "SELECT * FROM knowledge_items INDEXED BY idx_items_status "  # noqa: S608 - placeholders only
            f"WHERE project_id = ? AND status IN ({placeholders}) "
            "ORDER BY item_id"
        )
        params = (context.project_id.value, *values)
        return self._read_all(sql, params, _item_from_row)

    def count_surfaceable_by_status(self, context: RequestContext) -> dict[str, int]:
        # Count in SQL so `knowledge.status` spends work proportional to what it
        # publishes, not to the retired rows it withholds. Filtering `list_items`
        # in Python read every row, which made the tool's response time scale
        # with the withheld count -- subtracting the published `itemCount`
        # recovered it (T-17; #158 owns the `search._scan` sibling). The `IN`
        # list is `SURFACEABLE_STATUSES` itself, so a status added to the domain
        # set reaches the query with no second edit here. `sorted()` only fixes
        # the bind order and is defensive: the statement text is `?, ?, ?`
        # regardless of the values, and the result order comes from the SQL
        # `ORDER BY status`. `GROUP BY` returns no row for a status with no
        # items, which keeps the mapping identical to the old first-appearance
        # loop. The
        # covering index `idx_items_status(project_id, status)` answers it
        # without reading a withheld row.
        statuses = tuple(sorted(s.value for s in SURFACEABLE_STATUSES))
        placeholders = ", ".join("?" for _ in statuses)
        sql = (
            "SELECT status, COUNT(*) AS n FROM knowledge_items "  # noqa: S608 - placeholders only
            f"WHERE project_id = ? AND status IN ({placeholders}) "
            "GROUP BY status ORDER BY status"
        )
        params = (context.project_id.value, *statuses)
        pairs = self._read_all(sql, params, lambda row: (str(row["status"]), int(row["n"])))
        return dict(pairs)

    def count_surfaceable_items(self, context: RequestContext) -> int:
        # The same population `count_surfaceable_by_status` groups, totalled in
        # SQL for the tools that need only the total: `knowledge.search` and
        # `knowledge.get` compare it against `project_integrity`'s recorded count
        # on every request (#30 PR2) and publish no breakdown. `knowledge.status`
        # calls neither for this -- it sums the breakdown it already read, which
        # is the same predicate over the same rows and one query fewer.
        #
        # `INDEXED BY idx_items_status`, measured as `SEARCH knowledge_items
        # USING COVERING INDEX idx_items_status (project_id=? AND status=?)` --
        # the seek form, so the withheld rows are never read and the cost is
        # O(surfaceable): 129 -> 130 -> 130 VM steps across 0, 50 and 300
        # non-surfaceable rows, flat where the channel T-17 describes would grow.
        #
        # **The planner picks the same index unaided here, and the force is still
        # not decoration.** Measured without it: the same plan, 133 -> 134 -> 134.
        # `list_items_by_status` is where leaving the choice open was measured to
        # go wrong -- its `ORDER BY item_id` made the primary key look cheaper, so
        # the planner seeked on `project_id` alone and post-filtered `status`,
        # reading every withheld row (75 -> 325 -> 1575). This query has no such
        # pull today. The force is what keeps that a property of the statement
        # rather than of a cost estimate: drop or rename the index and this fails
        # loudly instead of quietly reopening the channel (SEC-13, #158).
        statuses = tuple(sorted(s.value for s in SURFACEABLE_STATUSES))
        placeholders = ", ".join("?" for _ in statuses)
        count = self._read_one(
            "SELECT COUNT(*) AS n FROM knowledge_items "  # noqa: S608 - placeholders only
            f"INDEXED BY idx_items_status WHERE project_id = ? AND status IN ({placeholders})",
            (context.project_id.value, *statuses),
            lambda row: int(row["n"]),
        )
        # `COUNT(*)` always returns a row, so this never falls through; the guard
        # is defensive, as in `count_migration_history`.
        return 0 if count is None else count

    def expected_surfaceable_count(self, project_id: ProjectId) -> int | None:
        # The writer's own record of what a reader should be able to see, written
        # inside the transaction that produced the rows (#30 PR2). `None` means
        # the row is absent, which the detector reads as damage rather than as
        # "not recorded": `migrate apply` writes it whenever it creates a
        # database or applies a migration, and `is_supported` refuses every
        # database written before this table existed.
        #
        # `int()` over the cell is an interpretation of this file like any other,
        # so a cell that is not a number refuses through `_reading` with a remedy
        # rather than being silently read as 0 -- which would fabricate a damage
        # report, or hide one, depending on what the live count happens to be.
        return self._read_one(
            "SELECT expected_surfaceable_count AS n FROM project_integrity WHERE project_id = ?",
            (project_id.value,),
            lambda row: int(row["n"]),
        )

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

    def count_migration_history(self, project_id: ProjectId) -> int:
        # A bare COUNT over one project's migration-history rows, for the #30
        # integrity signal. It interprets no migration cell -- neither the id nor
        # the checksum, unlike `applied_migrations` above -- so a damaged one
        # cannot make this refuse or leak; the tool compares the number against
        # the active pointer's `migration_count` and discloses a mismatch.
        #
        # `INDEXED BY` forces `idx_migration_history_sequence(project_id,
        # sequence)`, which serves this as `USING COVERING INDEX
        # (project_id=?)` -- a scan of one project's index entries, O(migrations)
        # and never the table, so calling it on every `knowledge.search` reopens
        # none of the O(withheld) timing channels #158 and #19 closed. The force
        # is structural like `list_items_by_status`'s: drop or rename the index
        # and the query fails loudly rather than silently falling back to a scan.
        # `COUNT(*)` returns exactly one row, so `_read_one` never yields None
        # here; the guard is defensive.
        count = self._read_one(
            "SELECT COUNT(*) AS n FROM migration_history "
            "INDEXED BY idx_migration_history_sequence WHERE project_id = ?",
            (project_id.value,),
            lambda row: int(row["n"]),
        )
        return 0 if count is None else count


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
            InvariantViolationError: If the id is already held -- by a different
                item, or by this one with different content. Re-appending the
                *identical* revision is allowed, because re-applying a migration
                must be a no-op (FR-K8).
            StateDatabaseUnreadableError: If a cell the refusal compares is not
                the kind of value it claims to be. Distinguished from the above
                because the two remedies disagree -- see
                :meth:`_refuse_unless_it_is_the_same_revision`.
        """
        existing = self._conn.execute(
            "SELECT item_id, content_sha256 FROM knowledge_revisions "
            "WHERE revision_id = ? AND project_id = ?",
            (revision.revision_id.value, revision.project_id.value),
        ).fetchone()
        if existing is not None:
            self._refuse_unless_it_is_the_same_revision(existing, revision)
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

    @staticmethod
    def _refuse_unless_it_is_the_same_revision(
        existing: sqlite3.Row, revision: KnowledgeRevision
    ) -> None:
        """Return only when the stored row *is* the revision being appended.

        Reached when the id is already present, which FR-K8 requires to be the
        ordinary case: ``theurian migrate apply`` repeats every append of every
        migration it has already applied. Sameness therefore has to be decided
        here, and by more than the id.

        Both arms compare opaque strings, so the guard's key -- does this line
        interpret bytes that came out of this file? -- is answered "no" for the
        comparisons themselves. Only the question of *why* a comparison failed is
        an interpretation, and each of those two lines is guarded on its own.
        """
        # **The item, before the content.** A revision id names one item for the
        # life of the project, and resolving idempotency by the id alone left
        # that unenforced: a second item declaring an id the first already held
        # took the no-op path above, and the caller's own `put_item` -- whose
        # in-memory revision is honest, so INV-2 in `KnowledgeItem.with_revision`
        # passes -- then pointed the second item at the first item's row.
        #
        # No reader can catch that state, and none should have to: they
        # dereference `current_revision_id` and are right to. Measured on the
        # shipped CLI, one migration reusing a `revisionId` across a `rejected`
        # item and an `approved` one applied with exit 0, after which
        # `knowledge.get` for the *approved* id answered with the rejected
        # item's id, title, source anchors and full body, and `knowledge.search`
        # excerpted the same body -- past a gate that had correctly refused the
        # rejected item asked for by name. The refusal belongs here because this
        # is the only place the stored row's owner is visible.
        stored_item: str = existing["item_id"]
        if stored_item != revision.item_id.value:
            # Same two opposite cures as the content arm below: a reused id, and
            # a damaged cell. "Give this operation its own revisionId" is the
            # wrong answer to the second, and an author who follows it appends a
            # duplicate into a database that is already broken.
            with _reading():
                ItemId(stored_item)
            raise InvariantViolationError(
                f"Revision {revision.revision_id} already belongs to item {stored_item}, "
                f"so {revision.item_id} cannot claim it as well. A revision id names one "
                f"item for the life of the project; give this operation its own revisionId."
            )

        stored: str = existing["content_sha256"]
        if stored != revision.content_sha256.value:
            # Two states produce this mismatch and their cures are opposite: an
            # author rewriting a revision, and a damaged cell. Only the first is
            # INV-1, and telling the second to "write a new revision instead"
            # appends a duplicate into a database that is already broken --
            # reached by re-applying an *unchanged* migration, which FR-K8
            # requires to be a no-op.
            with _reading():
                ContentHash(stored)
            raise InvariantViolationError(
                f"Revision {revision.revision_id} already exists with different content. "
                f"Revisions are immutable; write a new revision instead."
            )

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
        self._refuse_pointer_to_another_items_revision(item)
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

    def _refuse_pointer_to_another_items_revision(self, item: KnowledgeItem) -> None:
        """Refuse a ``current_revision_id`` that names another item's revision.

        The store half of INV-2, symmetric to
        :meth:`_refuse_unless_it_is_the_same_revision` on the revision write.
        :meth:`KnowledgeItem.with_revision` already refuses a cross-item pointer,
        but that is an in-memory guarantee the ``ON CONFLICT`` upsert above
        trusts the caller to have used: every call site does today, and none has
        to for the leak to reopen. The append guard closes the row a bad pointer
        would dereference; this closes the pointer itself, so neither half stands
        on the other being reached first -- a pointer that adopts another item's
        revision is the path by which an approved item comes to serve a withheld
        item's title, anchors and body.

        Absence is not ours to answer: the ``current_revision_id`` foreign key
        already refuses a pointer to a revision that does not exist, so this
        guard decides only the cross-item case.
        """
        if item.current_revision_id is None:
            return
        # Project-scoped like `get_revision`, not because a global PK needs it
        # today but because an unscoped lookup would read another project's
        # `item_id` the moment a config puts two projects in one database.
        existing = self._conn.execute(
            "SELECT item_id FROM knowledge_revisions WHERE revision_id = ? AND project_id = ?",
            (item.current_revision_id.value, item.project_id.value),
        ).fetchone()
        if existing is None:
            return
        stored_item: str = existing["item_id"]
        if stored_item != item.item_id.value:
            # The same two opposite cures as the append guard: a pointer at
            # another item's revision, and a damaged cell whose garbage only
            # looks like a different id. "Point at a revision of this item" is
            # the wrong answer to the second, so validate the cell before naming
            # that remedy -- a bad cell is an unreadable database, not an author
            # error.
            with _reading():
                ItemId(stored_item)
            raise InvariantViolationError(
                f"Revision {item.current_revision_id} belongs to item {stored_item}, so "
                f"item {item.item_id} cannot point its current revision at it. A revision "
                f"id names one item for the life of the project; point at a revision of "
                f"this item."
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

    # -- Integrity ---------------------------------------------------------

    def record_expected_surfaceable_count(self, project_id: ProjectId) -> None:
        """Record how many items a reader should be able to see (#30 PR2).

        Called inside ``migrate apply``'s write transaction, after the apply, so
        the number is counted over the rows this transaction itself wrote and
        no reader outside it can observe an intermediate value. It is the
        expectation three MCP tools compare their own live count against; a
        difference is damage they disclose rather than answer around.

        **One statement, and nothing is read back.** The count is computed by the
        ``INSERT ... SELECT`` rather than fetched and re-bound, so this method
        interprets no stored cell -- there is no converter here that a damaged
        page could reach, and therefore no ``_reading`` guard to place. The
        ``COUNT`` runs over ``idx_items_status`` for the same reason the reader's
        does, and the ``ON CONFLICT`` makes a re-apply overwrite rather than
        raise on the primary key.
        """
        statuses = tuple(sorted(s.value for s in SURFACEABLE_STATUSES))
        placeholders = ", ".join("?" for _ in statuses)
        self._conn.execute(
            "INSERT INTO project_integrity (project_id, expected_surfaceable_count) "  # noqa: S608 - placeholders only
            "SELECT ?, COUNT(*) FROM knowledge_items INDEXED BY idx_items_status "
            f"WHERE project_id = ? AND status IN ({placeholders}) "
            "ON CONFLICT(project_id) DO UPDATE SET "
            "expected_surfaceable_count = excluded.expected_surfaceable_count",
            (project_id.value, project_id.value, *statuses),
        )

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

    def list_revision_ids(self, project_id: ProjectId, item_id: ItemId) -> tuple[RevisionId, ...]:
        """Every revision id an item has, read inside the write transaction.

        The engine reads this to say which revisions a withdrawal takes out of a
        still-published index (ADR-0024 decision 5). Ids only, not whole
        revisions: the purge deletes by ``revision_id``, and reconstructing a
        `KnowledgeRevision` here would read the body and its anchors for nothing.

        Guarded like `get_item`: it is a read, and it must see the item as this
        transaction has left it -- a revision this apply just appended is one the
        withdrawal in the same apply must be able to name.
        """
        with _reading():
            rows = self._conn.execute(
                "SELECT revision_id FROM knowledge_revisions "
                "WHERE project_id = ? AND item_id = ? ORDER BY revision_id",
                (project_id.value, item_id.value),
            ).fetchall()
            return tuple(RevisionId(row["revision_id"]) for row in rows)


# -- Row mapping ----------------------------------------------------------
#
# Every function below runs inside a `_reading()` block at `c7d59b4`, and
# nothing enforces that the next call site will. They are named and
# module-level rather than inlined as lambdas so that the guard's coverage is a
# property of *where they are called* -- a set small enough to read through,
# but not the "one place each" this comment used to claim:
# `grep -E '_from_row' store.py | grep -v '^#' | grep -vc '^def '` is 16 over
# nine mappers, and six of the nine are reached from more than one place
# (`grep -v '^#' store.py | grep -oE '_[a-z_]+_from_row' | sort | uniq -c`,
# where a mapper reached once shows 2 -- its own `def` line and that one
# reference). `_item_from_row` is reached from `get_item`, `list_items` and
# `SqliteWriter.get_item`; `_anchor_from_row` from `_anchors_for` and from
# inside `_evidence_from_row`, a mapper calling a mapper.


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
