"""CanonicalStore port: persistence for the record of truth.

Deliberately exposes no method that updates a revision. Immutability (ADR-0006)
is expressed in the type signature, not only in prose -- an adapter cannot offer
an update path without violating the Protocol.
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
        current_at: datetime | None = None,
    ) -> tuple[KnowledgeItem, ...]: ...

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
