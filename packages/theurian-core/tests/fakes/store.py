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
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, SpecId
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
            if existing.content_sha256 != revision.content_sha256:
                raise InvariantViolationError(
                    f"Revision {revision.revision_id} already exists with different content."
                )
            return
        self.revisions[revision.revision_id.value] = revision

    def put_item(self, item: KnowledgeItem) -> None:
        self.items[(item.project_id.value, item.item_id.value)] = item

    def get_item(self, project_id: ProjectId, item_id: ItemId) -> KnowledgeItem | None:
        return self.items.get((project_id.value, item_id.value))

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
