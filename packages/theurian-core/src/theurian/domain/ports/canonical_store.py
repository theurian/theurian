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

        Raises:
            InvariantViolationError: If ``revision.revision_id`` already exists
                with different content. Revisions are never rewritten.
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
