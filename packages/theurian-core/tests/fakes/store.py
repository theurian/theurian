"""An in-memory MigrationWriter (ADR-0003, OSS-15).

Lets the migration engine be tested without SQLite, a filesystem, or a
transaction. It implements the same ``MigrationWriter`` protocol as the real
adapter, and a conformance test asserts both satisfy it -- a fake that drifts
from its port is worse than no fake.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import final

from theurian.domain.enums import SpecificationStatus
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId, SpecId
from theurian.domain.knowledge import (
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
)
from theurian.domain.specification import Specification


@final
class InMemoryWriter:
    """Records what the engine did, with the same invariants as the real store."""

    def __init__(self) -> None:
        self.revisions: dict[str, KnowledgeRevision] = {}
        self.items: dict[tuple[str, str], KnowledgeItem] = {}
        self.relations: list[KnowledgeRelation] = []
        self.aliases: dict[tuple[str, str], KnowledgeAlias] = {}
        self.evidence: list[KnowledgeEvidence] = []
        self.specifications: dict[tuple[str, str], Specification] = {}
        self.history: list[tuple[MigrationId, str]] = []

    def append_revision(self, revision: KnowledgeRevision) -> None:
        existing = self.revisions.get(revision.revision_id.value)
        if existing is not None:
            # Both arms, in the real store's order. A fake that resolved
            # idempotency by the id alone would let the engine build a state the
            # adapter refuses -- an item pointing at another item's revision --
            # and every engine test written against it would be describing a
            # database that cannot exist.
            if existing.item_id != revision.item_id:
                raise InvariantViolationError(
                    f"Revision {revision.revision_id} already belongs to item "
                    f"{existing.item_id}, so {revision.item_id} cannot claim it as well. "
                    f"A revision id names one item for the life of the project; give this "
                    f"operation its own revisionId."
                )
            if existing.content_sha256 != revision.content_sha256:
                # The remedy is part of the mirror: the real store names it and a
                # contract test pins it on both adapters, so a fake that only said
                # "different content" would let the wording drift undetected.
                raise InvariantViolationError(
                    f"Revision {revision.revision_id} already exists with different content. "
                    f"Revisions are immutable; write a new revision instead."
                )
            return
        self.revisions[revision.revision_id.value] = revision

    def put_item(self, item: KnowledgeItem) -> None:
        self._refuse_pointer_to_another_items_revision(item)
        self.items[(item.project_id.value, item.item_id.value)] = item

    def _refuse_pointer_to_another_items_revision(self, item: KnowledgeItem) -> None:
        # Mirrors SqliteWriter._refuse_pointer_to_another_items_revision -- the
        # store half of INV-2. `put_item` upserts an item's `current_revision_id`,
        # and the real adapter refuses one that names a revision belonging to a
        # different item. A fake that stored it anyway would let the engine build
        # the exact cross-item pointer -- an approved item serving a withheld
        # item's body -- that the real store rejects, and every engine test
        # written against the fake would describe a database that cannot exist.
        if item.current_revision_id is None:
            return
        existing = self.revisions.get(item.current_revision_id.value)
        # Project-scoped like the real store's `... WHERE project_id = ?`: a
        # revision in another project is not found here, so it is not a cross-item
        # pointer this guard should refuse.
        if existing is None or existing.project_id.value != item.project_id.value:
            return
        if existing.item_id.value != item.item_id.value:
            raise InvariantViolationError(
                f"Revision {item.current_revision_id} belongs to item {existing.item_id}, so "
                f"item {item.item_id} cannot point its current revision at it. A revision "
                f"id names one item for the life of the project; point at a revision of "
                f"this item."
            )

    def get_item(self, project_id: ProjectId, item_id: ItemId) -> KnowledgeItem | None:
        return self.items.get((project_id.value, item_id.value))

    def list_revision_ids(self, project_id: ProjectId, item_id: ItemId) -> tuple[RevisionId, ...]:
        return tuple(
            sorted(
                (
                    revision.revision_id
                    for revision in self.revisions.values()
                    if revision.project_id.value == project_id.value
                    and revision.item_id.value == item_id.value
                ),
                key=lambda revision_id: revision_id.value,
            )
        )

    def add_relation(self, relation: KnowledgeRelation) -> None:
        key = (
            relation.project_id.value,
            relation.source_item_id.value,
            relation.relation_type.value,
            relation.target_item_id.value,
        )
        if key not in {
            (
                r.project_id.value,
                r.source_item_id.value,
                r.relation_type.value,
                r.target_item_id.value,
            )
            for r in self.relations
        }:
            self.relations.append(relation)

    def remove_relation(self, relation: KnowledgeRelation) -> None:
        self.relations = [
            r
            for r in self.relations
            if not (
                r.project_id == relation.project_id
                and r.source_item_id == relation.source_item_id
                and r.relation_type == relation.relation_type
                and r.target_item_id == relation.target_item_id
            )
        ]

    def add_alias(self, alias: KnowledgeAlias) -> None:
        self.aliases[(alias.project_id.value, alias.alias.value)] = alias

    def remove_alias(self, project_id: ProjectId, alias: ItemId) -> None:
        self.aliases.pop((project_id.value, alias.value), None)

    def add_evidence(self, evidence: KnowledgeEvidence) -> None:
        self.evidence = [
            e
            for e in self.evidence
            if not (
                e.project_id == evidence.project_id
                and e.item_id == evidence.item_id
                and e.anchor.source_uri == evidence.anchor.source_uri
            )
        ]
        self.evidence.append(evidence)

    def remove_evidence(self, project_id: ProjectId, item_id: ItemId, source_uri: str) -> None:
        self.evidence = [
            e
            for e in self.evidence
            if not (
                e.project_id == project_id
                and e.item_id == item_id
                and e.anchor.source_uri == source_uri
            )
        ]

    def register_specification(self, specification: Specification) -> None:
        self.specifications[(specification.project_id.value, specification.spec_id.value)] = (
            specification
        )

    def supersede_specification(
        self, project_id: ProjectId, spec_id: SpecId, superseded_by: SpecId
    ) -> None:
        key = (project_id.value, spec_id.value)
        if key in self.specifications:
            self.specifications[key] = dataclasses.replace(
                self.specifications[key], status=SpecificationStatus.SUPERSEDED
            )
        self.superseded_by = superseded_by

    def record_migration(
        self,
        project_id: ProjectId,
        migration_id: MigrationId,
        checksum: str,
        applied_at: datetime,
    ) -> None:
        self.history = [(m, c) for m, c in self.history if m != migration_id]
        self.history.append((migration_id, checksum))

    def applied_migrations(self, project_id: ProjectId) -> tuple[tuple[MigrationId, str], ...]:
        return tuple(self.history)
