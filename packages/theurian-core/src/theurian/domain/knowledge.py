"""Knowledge entities: items, revisions, relations, anchors, aliases, evidence.

A :class:`KnowledgeRevision` is immutable. A :class:`KnowledgeItem` is a mutable
pointer to the revision that is current now. See ADR-0006.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Self

from theurian.domain.enums import (
    ACYCLIC_RELATIONS,
    INVERSE_RELATIONS,
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.values import AclGroup, ContentHash, MediaType, Scope, TenantId, ValidityPeriod


@dataclass(frozen=True, slots=True)
class SourceAnchor:
    """A pointer from canonical content back to where it came from.

    Every retrieval result must reach an original commit, file, and line, or an
    external source URI (FR-R5). Without an anchor, knowledge is an assertion
    with no way to check it.
    """

    provider: str
    source_uri: str
    repository: str | None = None
    commit_sha: str | None = None
    blob_sha: str | None = None
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    external_id: str | None = None

    def __post_init__(self) -> None:
        if not self.provider:
            raise InvariantViolationError("SourceAnchor.provider must not be empty")
        if not self.source_uri:
            raise InvariantViolationError("SourceAnchor.source_uri must not be empty")
        if self.line_start is not None and self.line_start < 1:
            raise InvariantViolationError(f"line_start is 1-based, got {self.line_start}")
        if self.line_end is not None:
            if self.line_start is None:
                raise InvariantViolationError("line_end requires line_start")
            if self.line_end < self.line_start:
                raise InvariantViolationError(
                    f"line_end ({self.line_end}) must not precede line_start ({self.line_start})"
                )

    @property
    def is_git_anchored(self) -> bool:
        """Whether this anchor pins an exact immutable Git object."""
        return self.commit_sha is not None and self.file_path is not None


#: Marker used when knowledge originates in Theurian itself rather than an
#: external source, so INV-8 stays checkable without inventing a fake anchor.
AUTHORED_IN_THEURIAN = "authored-in-theurian"


@dataclass(frozen=True, slots=True)
class RevisionMetadata:
    """Governance metadata attached to a revision.

    Separate from the body so that a status change is a distinct, auditable
    operation rather than an edit to prose (ADR-0010).
    """

    kind: KnowledgeKind
    namespace: str
    status: KnowledgeStatus
    trust_level: TrustLevel
    sensitivity: Sensitivity
    owner: str
    tenant_id: TenantId = field(default_factory=TenantId)
    acl_group: AclGroup = field(default_factory=AclGroup)
    scope_paths: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.owner:
            raise InvariantViolationError("RevisionMetadata.owner must not be empty")

    def scope_for(self, project_id: ProjectId) -> Scope:
        """The RAPTOR isolation scope implied by this metadata (ADR-0008)."""
        return Scope(
            project_id=project_id,
            tenant_id=self.tenant_id,
            sensitivity=self.sensitivity,
            acl_group=self.acl_group,
            namespace=self.namespace,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRevision:
    """An immutable snapshot of a knowledge item's content and metadata.

    Never updated in place. Correcting a revision means writing a new one, which
    is what keeps a citation to ``revision_id`` meaningful forever (INV-1).
    """

    revision_id: RevisionId
    item_id: ItemId
    project_id: ProjectId
    migration_id: MigrationId
    title: str
    body: str
    content_type: MediaType
    content_sha256: ContentHash
    metadata: RevisionMetadata
    validity: ValidityPeriod
    author: str
    created_at: datetime
    source_commit: str | None = None
    source_anchors: tuple[SourceAnchor, ...] = ()
    structured: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvariantViolationError(
                f"Revision {self.revision_id} must have a non-empty title"
            )
        if self.created_at.tzinfo is None:
            raise InvariantViolationError("created_at must be timezone-aware")

        expected = ContentHash.of_text(self.body)
        if expected != self.content_sha256:
            raise InvariantViolationError(
                f"Revision {self.revision_id} content hash mismatch: "
                f"declared {self.content_sha256.short}, body hashes to {expected.short}"
            )

        # INV-8: knowledge must be attributable to a source, or explicitly marked
        # as originating here. Silent unattributed knowledge is what this whole
        # system exists to prevent.
        if not self.source_anchors and AUTHORED_IN_THEURIAN not in self.metadata.labels:
            raise InvariantViolationError(
                f"Revision {self.revision_id} has no source anchor. Add one, or label "
                f"the revision {AUTHORED_IN_THEURIAN!r} to declare it originates in Theurian."
            )

    @classmethod
    def create(  # noqa: PLR0913 -- a revision has this many fields; all are keyword-only
        cls,
        *,
        revision_id: RevisionId,
        item_id: ItemId,
        project_id: ProjectId,
        migration_id: MigrationId,
        title: str,
        body: str,
        content_type: MediaType,
        metadata: RevisionMetadata,
        validity: ValidityPeriod,
        author: str,
        created_at: datetime,
        source_commit: str | None = None,
        source_anchors: tuple[SourceAnchor, ...] = (),
        structured: dict[str, object] | None = None,
    ) -> Self:
        """Build a revision, computing the content hash from the body.

        Preferred over the constructor everywhere except deserialisation, where
        the stored hash must be checked rather than recomputed.
        """
        return cls(
            revision_id=revision_id,
            item_id=item_id,
            project_id=project_id,
            migration_id=migration_id,
            title=title,
            body=body,
            content_type=content_type,
            content_sha256=ContentHash.of_text(body),
            metadata=metadata,
            validity=validity,
            author=author,
            created_at=created_at,
            source_commit=source_commit,
            source_anchors=source_anchors,
            structured=structured,
        )

    @property
    def scope(self) -> Scope:
        return self.metadata.scope_for(self.project_id)

    def is_current_at(self, moment: datetime) -> bool:
        """Whether this revision is both approved and within its validity window."""
        return self.metadata.status is KnowledgeStatus.APPROVED and self.validity.contains(moment)


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    """The stable identity behind a series of revisions.

    Frozen because every state change goes through a migration operation, and
    those operations return a new item rather than mutating one. The store
    persists the result; nothing edits an item in memory.
    """

    item_id: ItemId
    project_id: ProjectId
    namespace: str
    kind: KnowledgeKind
    status: KnowledgeStatus
    current_revision_id: RevisionId | None
    owner: str
    trust_level: TrustLevel
    sensitivity: Sensitivity
    validity: ValidityPeriod
    tenant_id: TenantId = field(default_factory=TenantId)
    acl_group: AclGroup = field(default_factory=AclGroup)

    def with_revision(self, revision: KnowledgeRevision) -> Self:
        """Return a copy pointing at ``revision`` and adopting its metadata.

        INV-2: the pointer must reference a revision of this same item.
        """
        if revision.item_id != self.item_id:
            raise InvariantViolationError(
                f"Revision {revision.revision_id} belongs to {revision.item_id}, "
                f"not to {self.item_id}"
            )
        if revision.project_id != self.project_id:
            raise InvariantViolationError(
                f"Revision {revision.revision_id} belongs to project {revision.project_id}, "
                f"not to {self.project_id}"
            )
        return replace(
            self,
            current_revision_id=revision.revision_id,
            status=revision.metadata.status,
            owner=revision.metadata.owner,
            trust_level=revision.metadata.trust_level,
            sensitivity=revision.metadata.sensitivity,
            namespace=revision.metadata.namespace,
            kind=revision.metadata.kind,
            tenant_id=revision.metadata.tenant_id,
            acl_group=revision.metadata.acl_group,
            validity=revision.validity,
        )

    def with_status(self, status: KnowledgeStatus) -> Self:
        return replace(self, status=status)

    @property
    def scope(self) -> Scope:
        return Scope(
            project_id=self.project_id,
            tenant_id=self.tenant_id,
            sensitivity=self.sensitivity,
            acl_group=self.acl_group,
            namespace=self.namespace,
            status=self.status,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeAlias:
    """A retired identifier that still resolves to an item.

    Renaming without aliases silently breaks every citation, in every past pull
    request, with no error anywhere.
    """

    alias: ItemId
    item_id: ItemId
    project_id: ProjectId
    created_at: datetime

    def __post_init__(self) -> None:
        if self.alias == self.item_id:
            raise InvariantViolationError(f"Alias {self.alias} cannot point at itself")


@dataclass(frozen=True, slots=True)
class KnowledgeRelation:
    """A typed, directed edge between two knowledge items."""

    project_id: ProjectId
    source_item_id: ItemId
    relation_type: RelationType
    target_item_id: ItemId
    created_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        if self.source_item_id == self.target_item_id:
            raise InvariantViolationError(
                f"Self-relation on {self.source_item_id} via {self.relation_type}"
            )

    @property
    def inverse(self) -> KnowledgeRelation | None:
        """The mirrored edge, for relation types that have one.

        Only one direction is stored; traversal uses this so callers never need
        to know which direction an author happened to write.
        """
        inverse_type = INVERSE_RELATIONS.get(self.relation_type)
        if inverse_type is None:
            return None
        return KnowledgeRelation(
            project_id=self.project_id,
            source_item_id=self.target_item_id,
            relation_type=inverse_type,
            target_item_id=self.source_item_id,
            created_at=self.created_at,
            note=self.note,
        )

    @property
    def must_be_acyclic(self) -> bool:
        """Whether a cycle through this relation type is a violation (INV-6)."""
        return self.relation_type in ACYCLIC_RELATIONS


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    """A concrete artifact supporting a knowledge claim.

    Distinct from :class:`SourceAnchor`: an anchor says where the text came from,
    evidence says why the claim is believed. A rule generalised from a review
    thread is anchored to the approved Markdown and evidenced by the thread.
    """

    item_id: ItemId
    project_id: ProjectId
    anchor: SourceAnchor
    description: str
    confidence: float
    created_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise InvariantViolationError(
                f"confidence must be within [0.0, 1.0], got {self.confidence}"
            )
        if not self.description.strip():
            raise InvariantViolationError("KnowledgeEvidence.description must not be empty")
