"""CanonicalStore port: persistence for the record of truth.

Deliberately exposes no method that updates a revision. Immutability (ADR-0006)
is expressed in the type signature, not only in prose -- an adapter cannot offer
an update path without violating the Protocol.

Two Protocols live here, not two ports. :class:`CanonicalReadSession` is a
narrowing of :class:`CanonicalStore`, so the port set ADR-0003 fixes is
unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId, SpecId
from theurian.domain.knowledge import (
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
)
from theurian.domain.project import Project
from theurian.domain.specification import Specification, TraceabilityEdge, TraceNode


@runtime_checkable
class CanonicalStore(Protocol):
    """Reads and appends canonical state.

    All reads are scoped by :class:`RequestContext`, so an adapter cannot
    accidentally offer a cross-project query (SEC-13). All writes are append-only.
    """

    # -- Projects ---------------------------------------------------------

    def register_project(self, project: Project) -> None:
        """Register or update a project. Idempotent by ``project_id``."""
        ...

    def unregister_project(self, project_id: ProjectId) -> None:
        """Remove a project registration. Never deletes Git-tracked content."""
        ...

    def get_project(self, project_id: ProjectId) -> Project | None: ...

    def list_projects(self) -> tuple[Project, ...]: ...

    # -- Knowledge (append-only) ------------------------------------------

    def append_revision(self, revision: KnowledgeRevision) -> None:
        """Append an immutable revision.

        A revision id belongs to exactly one item, so an adapter must decide
        idempotency on the whole revision and never on the id alone: returning
        as a no-op for an id another item holds lets the caller's item pointer
        name a revision of that other item, and every reader dereferences that
        pointer.

        Raises:
            InvariantViolationError: If ``revision.revision_id`` already exists
                under a different ``item_id``, or under the same one with
                different content. Revisions are never rewritten, and never
                change hands.
        """
        ...

    def put_item(self, item: KnowledgeItem) -> None:
        """Write the item pointer.

        The one mutable write in this port: an item's ``current_revision_id`` and
        derived metadata move forward as revisions are appended.
        """
        ...

    def get_item(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        """Fetch an item, resolving aliases."""
        ...

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None: ...

    def list_revisions(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRevision, ...]:
        """Full history for an item, oldest first."""
        ...

    def list_items(
        self,
        context: RequestContext,
        *,
        namespace: str | None = None,
    ) -> tuple[KnowledgeItem, ...]:
        """Every item in scope, unfiltered by validity window.

        Deliberately carries no ``current_at``. It did once, implemented as a
        SQL ``WHERE`` clause comparing a stored ``valid_from``/``valid_to``
        against a bound parameter as SQLite ``TEXT`` -- a lexicographic
        ordering of the ISO-8601 string, not of the absolute instant it
        names, so it silently disagreed with
        :meth:`~theurian.domain.values.ValidityPeriod.contains` whenever the
        two sides were authored in different UTC offsets (found in review
        round 1 of PR #112, #63 phase 2). Deleted rather than fixed in place:
        a caller that needs a validity-window filter constructs the moment
        once, as a timezone-aware ``datetime``, and applies
        ``ValidityPeriod.contains`` in Python -- the same method every other
        caller in this codebase already uses, so there is exactly one
        comparison to get right instead of two that have to be kept in
        agreement.
        """
        ...

    def list_items_by_status(
        self,
        context: RequestContext,
        *,
        statuses: frozenset[KnowledgeStatus],
        sensitivities: frozenset[Sensitivity],
    ) -> tuple[KnowledgeItem, ...]:
        """Every item in scope on both axes, filtered in SQL.

        A dumb two-axis filtered read. It holds no visibility semantics and never
        decides what a caller may see: the caller passes the sets it has already
        resolved. ``knowledge.search``'s substring fallback is the caller, and it
        builds ``statuses`` from :func:`~theurian.domain.enums.may_surface`, so
        both gates stay in the tool layer where they are enumerated (SEC-13,
        T-15, #119) and this port stays gate-agnostic -- an adapter must not
        consult a visibility rule.

        Neither set is defaulted. A default for either would mean "everything",
        which is the answer a forgotten argument must not silently produce on a
        read path.

        Its value over :meth:`list_items` is *where* the filtering happens.
        ``list_items`` reads every row and filters in Python, so a caller keeping
        only some rows still pays to materialise the rest -- and its response
        time then scales with the count of the rows it discards, recoverable by
        measuring it (T-17; the ``search._scan`` sibling of the channel #19 closed
        for ``knowledge.status``, #158).

        **The two axes are pushed down equally and are not equally flat, because
        only one of them is indexed.** ``statuses`` goes into the ``IN`` predicate
        ``idx_items_status(project_id, status)`` serves: the seek locates only the
        in-set rows and ``SELECT *`` then fetches each by rowid (``USING INDEX``,
        not ``USING COVERING INDEX``), so a row whose status is not in
        ``statuses`` is never fetched at all. ``sensitivities`` has no index
        column, so it is applied to the rows that seek returns -- an above-ceiling
        row is fetched from the table and dropped before it crosses into Python.

        What that buys and what it does not. Measured on SQLite 3.47.1 against
        this schema, over an ``approved`` project holding 50 rows an ``internal``
        ceiling admits and 0/50/300/1,000 above it, VM steps counted with a
        progress handler:

        ================== ============= ============= ==================
        Above-ceiling rows With the axis Without it    Rows into Python
        ================== ============= ============= ==================
        0                  2,110         1,955         50
        50                 2,410         3,655         50
        300                3,910         12,155        50
        1,000              8,110         35,955        50
        ================== ============= ============= ==================

        So the *Python* cost -- item construction, and in ``search._scan`` the
        per-item revision read and body scan that dominate it -- is flat, while
        the predicate itself carries exactly 6.0 VM steps per above-ceiling row
        against the 34 the same rows cost when they are returned instead.

        **The comparison against the residual the ranked path already accepts is
        in microseconds, not in VM steps**, because the two are not measured in
        one unit and an earlier revision of this paragraph compared them as
        though they were. Six VM steps is **about 0.20 us** per above-ceiling row
        on the machine the table above was taken on -- the figure and its method
        are recorded at
        :meth:`~theurian.infrastructure.sqlite.store.SqliteCanonicalStore.list_items_by_status`,
        which is where a re-measurement belongs. Against that, the canonical read
        the ranked path accepts and the threat model records is **14.7 us per
        withheld row** (T-17; the 15 us
        :meth:`~theurian.application.visibility.CanonicalVisibility.cleared`
        quotes is the same measurement rounded), so this term is roughly seventy
        times smaller *per row* -- and it is not zero, and it is bounded by the
        corpus rather than by the caller's ask, because the statement carries no
        ``LIMIT``.

        **The 14.7 us is the stale build's, and the comparison it anchors is
        against a window rather than against a standing cost.** It was taken on a
        published build that still held the withdrawn rows. Re-taken 2026-09-01
        against a real index and its purged twin (`ec0dbcd`;
        ``docs/work-logs/2026-09-01-472-purged-build-re-measurement.md``, F2/F1'),
        the stale build reproduces the shape at 24.3 us per withheld row -- a
        different machine 27 days later, so comparable in shape and not in
        magnitude -- while a purged build has no per-withheld-row term at all.
        **Why it has none is branch-dependent**, and stating it as one mechanism
        was this note's own error, caught in review: on the scan below the
        trigram floor, which carries no ``LIMIT``, the purged ``|ranking|`` is
        the visible count; on the branches that truncate it is ``depth`` whatever
        was withheld, before and after the purge alike. Pinned over withheld
        counts 0, 50 and 200 by
        ``test_a_purged_build_reads_canonical_once_per_visible_row_however_many_were_withheld``
        and over 49--52 by
        ``test_a_purged_build_stays_at_one_retriever_pass_across_the_first_pass_depth_edge``.
        The 0.20 us above is not like that: it is corpus-bounded and survives a
        purge, which is why the seventy-times-smaller comparison holds only
        inside the window between a withdrawal and the purge that follows it.

        Adding ``sensitivity`` as a third column of ``idx_items_status`` flattens
        it exactly -- 2,032 steps at 0 and at 1,000, measured the same way -- at
        the price of a ``SCHEMA_VERSION`` bump, which invalidates every existing
        state database and moves the ``schemaVersion`` ``knowledge.status``
        publishes. That was not a change #119 phase 2 could make, because its
        contract was that an allow-all deployment behaves exactly as it did; it
        is owned by https://github.com/theurian/theurian/issues/338.

        An empty ``statuses`` or an empty ``sensitivities`` returns ``()`` without
        a query: neither can match, so a query would only return zero rows. Both
        guards are defensive -- ``search._scan`` always resolves at least APPROVED
        into the first, and
        :class:`~theurian.application.authorization.AuthorizationGrant` refuses to
        exist with an empty second.
        """
        ...

    def count_surfaceable_by_status(
        self, context: RequestContext, *, sensitivities: frozenset[Sensitivity]
    ) -> dict[str, int]:
        """Count the items a caller may see, grouped by status, in SQL.

        Returns a ``status-value -> count`` mapping over
        :data:`~theurian.domain.enums.SURFACEABLE_STATUSES` **and** the levels in
        ``sensitivities``. Deprecated, superseded and rejected rows are never
        counted, nor is a row above the deployment's ceiling, so nothing here --
        not even a sum across it -- restores the withheld total.

        That is what separates it from :meth:`list_items`, which reads every row
        and leaves the filtering to the caller. ``knowledge.status`` did that
        filtering in Python, which made its *response time* proportional to the
        withheld rows rather than to what it publishes: subtracting the published
        count recovered the withheld one (T-17; #158 owns the ``search._scan``
        sibling). Counting in SQL keeps the retired rows out of the walk -- the
        seek on ``idx_items_status`` skips every row outside
        ``SURFACEABLE_STATUSES``, so the response time no longer scales with the
        withheld *status* count. It is not a *covering* scan, though: since #119
        phase 6 the grouping also reads ``sensitivity``, a column that index does
        not carry, so each in-status row is fetched and the ``GROUP BY`` needs a
        temp b-tree -- a bounded per-above-ceiling-row term measured at the
        adapter and recorded as T-22.

        **``sensitivities`` is required and has no default** (#119 phase 6), the
        convention :meth:`list_items_by_status` set: a default would mean
        "everything", which is the answer a forgotten argument must not silently
        produce on a read path. An empty set returns ``{}`` without a query.

        **The level is interpreted, not matched**, which is where this method
        parts company with :meth:`list_items_by_status`'s SQL predicate. An
        adapter is expected to aggregate in SQL and admit in the domain, so a
        stored level it cannot interpret refuses the read rather than dropping
        out of a match -- a corrupt cell would otherwise answer ``itemCount: 0``
        over a project holding items, silently, while ``knowledge.search`` and
        ``knowledge.get`` both refuse the same cell. The cost of that choice is
        measured at the adapter.

        **It narrows what is published and not what is checked.** This is the
        number ``knowledge.status`` publishes as ``itemsByStatus``, and its sum is
        ``itemCount``; both are statistics over rows the caller may see and
        therefore follow the grant (SEC-13, T-17). The #30 integrity comparison
        deliberately does **not** read this method -- it reads
        :meth:`count_surfaceable_items`, which takes no grant -- because that
        comparison checks a *ceiling-blind* record written by ``migrate apply``
        against the live population, and narrowing one half of it would make
        every restricted deployment report ``damageDetected`` on a healthy
        project.

        Lives on this port beside :meth:`applied_migrations`, the other thing
        ``knowledge.status`` reads, rather than on :class:`CanonicalReadSession`:
        the narrowed session is the index builder's read subset, and a status
        breakdown is not one of the questions it asks.
        """
        ...

    def count_surfaceable_items(self, context: RequestContext) -> int:
        """How many items in scope a caller may see, totalled in SQL.

        The same **status** population :meth:`count_surfaceable_by_status`
        groups, without the breakdown and **without that method's disclosure
        axis**. It exists because ``knowledge.search`` and ``knowledge.get`` need
        the total and publish no breakdown: it is the live half of the ``#30``
        integrity comparison, checked against :meth:`expected_surfaceable_count`
        on every request.

        **It takes no grant, and that is the decision rather than an omission**
        (#119 phase 6). Its number is never published; it is compared against a
        record ``migrate apply`` wrote ceiling-blind from the rows it had just
        written. Narrowing this side alone would make a deployment's own ceiling
        read as damage on a healthy project, and narrowing both sides would make
        the check compare a live population against a record written under
        whatever ceiling happened to be declared at write time. So the
        comparison stays on the ungated population at both ends, and the
        ceiling narrows :meth:`count_surfaceable_by_status`, which is the half a
        caller reads.

        Retired rows -- deprecated, superseded, rejected -- are not counted, so
        neither this number nor its comparison carries anything about content the
        caller may not read (SEC-13, T-17). Its cost is ``O(surfaceable)`` over
        the covering index, never ``O(total)``, so calling it per request reopens
        none of the timing channels #158 and #19 closed.
        """
        ...

    def expected_surfaceable_count(self, project_id: ProjectId) -> int | None:
        """What the writer recorded that count should be, or ``None`` if absent.

        Written once per ``theurian migrate apply`` that creates a database or
        applies a migration, inside that transaction, from the rows it just wrote
        (``#30`` PR2). Nothing on a query path computes it, which is what keeps
        the comparison a single indexed lookup and keeps the expectation from
        being recomputed by the very state it is meant to check.

        ``None`` is not "no opinion". A build that can read this database is a
        build whose schema version declares the table, so a project with rows and
        no record is a project whose record was lost -- damage, and reported as
        such. That inference is only available because
        :func:`~theurian.infrastructure.sqlite.schema.is_supported` refuses every
        older database outright rather than reinterpreting it.
        """
        ...

    # -- Relations, aliases, evidence --------------------------------------

    def add_relation(self, relation: KnowledgeRelation) -> None: ...

    def remove_relation(self, relation: KnowledgeRelation) -> None: ...

    def list_relations(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRelation, ...]:
        """Relations touching ``item_id`` in either direction, inverses included."""
        ...

    def add_alias(self, alias: KnowledgeAlias) -> None: ...

    def remove_alias(self, context: RequestContext, alias: ItemId) -> None: ...

    def add_evidence(self, evidence: KnowledgeEvidence) -> None: ...

    def remove_evidence(
        self, context: RequestContext, item_id: ItemId, source_uri: str
    ) -> None: ...

    # -- Specifications ----------------------------------------------------

    def register_specification(self, specification: Specification) -> None: ...

    def get_specification(
        self, context: RequestContext, spec_id: SpecId
    ) -> Specification | None: ...

    def list_specifications(self, context: RequestContext) -> tuple[Specification, ...]: ...

    # -- Traceability ------------------------------------------------------

    def add_traceability_edge(self, edge: TraceabilityEdge) -> None: ...

    def list_traceability_edges(
        self,
        context: RequestContext,
        *,
        source: TraceNode | None = None,
        target: TraceNode | None = None,
    ) -> tuple[TraceabilityEdge, ...]: ...

    # -- Migration history -------------------------------------------------

    def record_migration(
        self, project_id: ProjectId, migration_id: MigrationId, checksum: str, applied_at: datetime
    ) -> None: ...

    def applied_migrations(self, project_id: ProjectId) -> tuple[tuple[MigrationId, str], ...]:
        """Applied migrations as ``(id, checksum)`` pairs, in application order.

        The checksum is what makes tampering with an applied migration detectable
        (ADR-0005).
        """
        ...

    def count_migration_history(self, project_id: ProjectId) -> int:
        """How many migration-history rows this project holds, counted in SQL.

        The bounded integrity signal a tool emits on ``#30``: the active pointer
        that chose the state database records how many migrations it was built
        from (``ActiveState.migration_count``), and the state database is
        immutable once built, so in a healthy project this count equals that
        number. A difference is damage -- a corrupt ``project_id`` cell dropping
        the row out of the ``WHERE``, a lost row, or another project's rows
        bleeding in -- which a tool reads back and discloses rather than
        answering with silently less than the database holds.

        Deliberately a bare ``COUNT``, not :meth:`applied_migrations`, and
        deliberately gate-agnostic: it interprets no migration cell -- not the
        id, not the checksum -- so it cannot itself refuse or leak on a damaged
        one, and it is served by ``idx_migration_history_sequence(project_id,
        sequence)`` as a covering index scan over one project's rows. Its cost is
        therefore ``O(migrations)`` and independent of the corpus, so a tool that
        calls it on every request -- ``knowledge.search`` included -- reopens
        none of the ``O(withheld)`` timing channels #158 and #19 closed.
        """
        ...


@runtime_checkable
class CanonicalReadSession(Protocol):
    """One pass over canonical state, opened and closed by the caller.

    **Not a fifteenth port.** This is the read subset of :class:`CanonicalStore`
    that a derived-artifact builder needs, plus the one thing that port
    deliberately does not express: when the underlying handle is released. The
    port set ADR-0003 fixes is unchanged.

    The lifetime belongs in the contract because index building is the use case
    that has to get it right. It walks the whole store once and must then let
    the handle go -- ``sqlite3.connect`` used as a context manager commits but
    does not close, which leaked a handle per call in Milestone 1.

    It exists at all because the alternative in place was
    ``Callable[[Path], Any]``. That typed the index builder's only collaborator
    as nothing whatsoever: strict mypy could not tell whether the object it was
    handed could answer these questions, and nothing stopped an adapter's
    ``sqlite3.Row`` from reaching the application layer -- the same failure
    :mod:`theurian.domain.ports.index_store` was written to record.

    Deliberately no write method. A builder that could append to canonical state
    would make a derived artifact authoritative, which ADR-0004 rules out.
    """

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        """Every item in the request's project, scoped by the context (SEC-13)."""
        ...

    def get_item(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        """Fetch an item, resolving aliases.

        Narrowed in from :class:`CanonicalStore` because resolving a ranked
        chunk into a result needs the *item*'s status, not the revision's. The
        index stamps each chunk with the status that was in force when it was
        built; only the item says what is approved now, and answering a search
        from the build-time copy is how a retired document comes back wearing
        the label it had before it was retired (FR-R5, SEC-13).
        """
        ...

    def get_item_exact(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        """Fetch the row ``item_id`` *literally* names, resolving no alias (T-21).

        The authority counterpart of :meth:`get_item`. Reachability may follow an
        alias -- a rename has to let a fetch of the old id answer with the new
        item -- but a *visibility decision on a referenced id* must read the row
        that id names, never the row an alias redirects it to. An ``addAlias``
        key is a string an author chooses freely, so a key equal to a live but
        non-surfaceable item's id would otherwise let that item clear a gate as
        the approved item the alias points at:
        :func:`~theurian.mcp.tools._relation_is_visible` gates each relation
        endpoint through this exact read, so an endpoint that is also an alias
        key is judged by its own status and not the alias target's (SEC-13, T-21).
        """
        ...

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None: ...

    def __enter__(self) -> CanonicalReadSession:
        """Acquire the handle **here**, not at the first read.

        Part of the contract rather than an adapter's business, because the one
        caller that matters is a security gate. ``ResultGate`` opens a session,
        asks the retrievers for rows *through* it, and shows the caller none of
        what it withheld — so a session that acquires lazily charges its setup
        only to requests that found something, and "found something" is exactly
        the fact the response is refusing to state. The SQLite adapter leaked
        0.6 ms that way, enough to classify a single call 88.3% of the time.

        An adapter with nothing to acquire satisfies this trivially. An adapter
        that connects, authenticates or handshakes must do it here.
        """
        ...

    def __exit__(self, *details: object) -> None: ...
