"""Knowledge migration model (ADR-0005).

A migration is a declarative, storage-independent statement about knowledge
state. The types here describe what a migration *is*; applying one is the
application layer's job, and loading one from disk is the infrastructure's.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, override

from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.errors import (
    MigrationCycleError,
    MigrationDependencyMissingError,
    MigrationError,
)
from theurian.domain.identifiers import ItemId, MigrationId, RevisionId, SpecId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import ContentHash, MediaType

#: The migration format this build understands.
MIGRATION_API_VERSION: Final = "theurian.dev/v1"

#: Bumped when the engine's *semantics* change in a way that would produce a
#: different canonical state from identical inputs. It is hashed into the state
#: hash so an engine change invalidates cached state instead of silently
#: reinterpreting it (ADR-0007).
MIGRATION_ENGINE_VERSION: Final = 1

#: The governed metadata a revision acquires when its migration omits it. These
#: are the single source of truth: the dataclass defaults below, the loader's
#: ``.get(...)`` fallbacks, and the ``propose`` note that warns an author which
#: default an omission will publish all read *these* constants, so the warning
#: cannot claim one value while the loader fills another (#249).
DEFAULT_TRUST_LEVEL: Final = TrustLevel.UNVERIFIED
DEFAULT_SENSITIVITY: Final = Sensitivity.INTERNAL


class OperationKind(StrEnum):
    """The closed operation set. Adding one bumps ``apiVersion`` (ADR-0005)."""

    CREATE_ITEM = "createItem"
    UPSERT_REVISION = "upsertRevision"
    DEPRECATE_ITEM = "deprecateItem"
    RESTORE_ITEM = "restoreItem"
    ADD_RELATION = "addRelation"
    REMOVE_RELATION = "removeRelation"
    ADD_ALIAS = "addAlias"
    REMOVE_ALIAS = "removeAlias"
    CHANGE_SENSITIVITY = "changeSensitivity"
    CHANGE_OWNER = "changeOwner"
    REGISTER_SPECIFICATION = "registerSpecification"
    SUPERSEDE_SPECIFICATION = "supersedeSpecification"
    ADD_EVIDENCE = "addEvidence"
    REMOVE_EVIDENCE = "removeEvidence"


@dataclass(frozen=True, slots=True)
class Operation:
    """Base for every migration operation."""

    @property
    def kind(self) -> OperationKind:
        raise NotImplementedError

    @property
    def content_file(self) -> str | None:
        """The body file this operation references, if any.

        Declared on the base so the loader can collect referenced files without
        knowing every operation type -- and so a new operation carrying content
        cannot be forgotten by the state-hash computation.
        """
        return None


@dataclass(frozen=True, slots=True)
class CreateItem(Operation):
    item_id: ItemId
    kind_: KnowledgeKind
    namespace: str
    owner: str
    sensitivity: Sensitivity = DEFAULT_SENSITIVITY
    trust_level: TrustLevel = DEFAULT_TRUST_LEVEL

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.CREATE_ITEM


@dataclass(frozen=True, slots=True)
class RevisionMetadataSpec:
    """The metadata block of an ``upsertRevision`` operation."""

    title: str
    content_type: MediaType
    kind: KnowledgeKind
    namespace: str
    status: KnowledgeStatus
    owner: str
    trust_level: TrustLevel = DEFAULT_TRUST_LEVEL
    sensitivity: Sensitivity = DEFAULT_SENSITIVITY
    tenant_id: str = "local"
    acl_group: str = "default"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    labels: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    source_anchors: tuple[SourceAnchor, ...] = ()


@dataclass(frozen=True, slots=True)
class UpsertRevision(Operation):
    item_id: ItemId
    revision_id: RevisionId
    content_file_path: str
    metadata: RevisionMetadataSpec
    expected_revision: RevisionId | None = None
    content_sha256: ContentHash | None = None
    #: ``content_file_path`` as the loader resolved it -- project-relative, with
    #: ``..`` collapsed and symlinks followed. Kept for display only: it names a
    #: body a reader can ``shasum`` and identifies the file in a refusal message.
    #: It is **not** the key two operations are compared on -- see
    #: ``content_identity`` for why the path string cannot be that key. ``None``
    #: for an operation built in memory, which has no tree to resolve against.
    resolved_content_path: str | None = None
    #: The resolved body's filesystem identity, ``(st_dev, st_ino)``, taken by the
    #: loader from the same ``stat`` that confirmed the file readable. This is the
    #: key on which two operations are judged to reference the *same* body (issue
    #: #210): a path *string* is not, because a case-insensitive filesystem (APFS,
    #: NTFS) reaches one physical file through many spellings -- ``note.md`` and
    #: ``NOTE.md``, an uppercase extension, a case-variant directory, an NFC/NFD
    #: pair -- each of which ``resolve()`` leaves distinct while ``stat`` returns
    #: one inode. Casefolding the string instead would be wrong the other way: it
    #: would false-refuse two genuinely different files on a case-sensitive Linux
    #: filesystem. ``None`` for an in-memory operation, which has no file on disk;
    #: such an operation cannot participate in the identity comparison, and the
    #: loader -- the only path a gate ever sees -- always sets it.
    content_identity: tuple[int, int] | None = None

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.UPSERT_REVISION

    @override
    @property
    def content_file(self) -> str | None:
        return self.content_file_path


@dataclass(frozen=True, slots=True)
class DeprecateItem(Operation):
    item_id: ItemId
    reason: str | None = None
    superseded_by: ItemId | None = None

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.DEPRECATE_ITEM


@dataclass(frozen=True, slots=True)
class RestoreItem(Operation):
    item_id: ItemId
    reason: str | None = None

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.RESTORE_ITEM


@dataclass(frozen=True, slots=True)
class AddRelation(Operation):
    source_item_id: ItemId
    relation_type: RelationType
    target_item_id: ItemId
    note: str | None = None

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.ADD_RELATION


@dataclass(frozen=True, slots=True)
class RemoveRelation(Operation):
    source_item_id: ItemId
    relation_type: RelationType
    target_item_id: ItemId

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.REMOVE_RELATION


@dataclass(frozen=True, slots=True)
class AddAlias(Operation):
    alias: ItemId
    item_id: ItemId

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.ADD_ALIAS


@dataclass(frozen=True, slots=True)
class RemoveAlias(Operation):
    alias: ItemId

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.REMOVE_ALIAS


@dataclass(frozen=True, slots=True)
class ChangeSensitivity(Operation):
    item_id: ItemId
    sensitivity: Sensitivity
    #: Required by the schema. Reclassification changes who may read the content,
    #: so the rationale is recorded. It does not force a rebuild: a result reads
    #: the item's current sensitivity, so the response reflects the new label at
    #: once, and the built index re-derives the label on the next ``index build``.
    reason: str

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.CHANGE_SENSITIVITY


@dataclass(frozen=True, slots=True)
class ChangeOwner(Operation):
    item_id: ItemId
    owner: str

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.CHANGE_OWNER


@dataclass(frozen=True, slots=True)
class RegisterSpecification(Operation):
    spec_id: SpecId
    item_id: ItemId
    source_uri: str
    content_format: MediaType
    status: SpecificationStatus = SpecificationStatus.ACTIVE

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.REGISTER_SPECIFICATION


@dataclass(frozen=True, slots=True)
class SupersedeSpecification(Operation):
    spec_id: SpecId
    superseded_by: SpecId

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.SUPERSEDE_SPECIFICATION


@dataclass(frozen=True, slots=True)
class AddEvidence(Operation):
    item_id: ItemId
    anchor: SourceAnchor
    description: str
    confidence: float = 1.0

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.ADD_EVIDENCE


@dataclass(frozen=True, slots=True)
class RemoveEvidence(Operation):
    item_id: ItemId
    source_uri: str

    @override
    @property
    def kind(self) -> OperationKind:
        return OperationKind.REMOVE_EVIDENCE


@dataclass(frozen=True, slots=True)
class Migration:
    """One migration file, parsed and validated.

    ``checksum`` is over the file's raw bytes. It is what makes editing an
    already-applied migration detectable, and it is an input to the state hash.
    """

    migration_id: MigrationId
    created_at: datetime
    author: str
    operations: tuple[Operation, ...]
    checksum: ContentHash
    depends_on: tuple[MigrationId, ...] = ()
    description: str | None = None
    #: Project-relative path, for error messages. Deliberately excluded from the
    #: checksum and the state hash: renaming a file must not change the state.
    source_path: str | None = None

    def __post_init__(self) -> None:
        if not self.operations:
            raise MigrationError(f"Migration {self.migration_id} has no operations")
        if self.created_at.tzinfo is None:
            raise MigrationError(
                f"Migration {self.migration_id} createdAt must carry an explicit offset"
            )
        if self.migration_id in self.depends_on:
            raise MigrationError(f"Migration {self.migration_id} depends on itself")
        duplicates = {d for d in self.depends_on if self.depends_on.count(d) > 1}
        if duplicates:
            listed = ", ".join(sorted(str(d) for d in duplicates))
            raise MigrationError(f"Migration {self.migration_id} lists {listed} twice in dependsOn")

    @property
    def content_files(self) -> tuple[str, ...]:
        """Body files referenced by this migration, in operation order."""
        return tuple(path for op in self.operations if (path := op.content_file) is not None)


@dataclass(frozen=True, slots=True)
class MigrationSet:
    """An ordered, validated collection of migrations.

    Construction performs the ordering and validation that must happen before
    any operation runs: a cycle or a missing dependency means no valid
    application order exists, and discovering that halfway through would leave
    the store in a state no migration describes.
    """

    migrations: tuple[Migration, ...]
    _by_id: dict[MigrationId, Migration] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_id: dict[MigrationId, Migration] = {}
        for migration in self.migrations:
            if migration.migration_id in by_id:
                raise MigrationError(
                    f"Duplicate migration id {migration.migration_id}: "
                    f"{by_id[migration.migration_id].source_path} and {migration.source_path}"
                )
            by_id[migration.migration_id] = migration
        object.__setattr__(self, "_by_id", by_id)

        for migration in self.migrations:
            for dependency in migration.depends_on:
                if dependency not in by_id:
                    raise MigrationDependencyMissingError(migration.migration_id, dependency)

    @classmethod
    def ordered(cls, migrations: tuple[Migration, ...]) -> MigrationSet:
        """Build a set sorted into a valid application order.

        Raises:
            MigrationCycleError: If ``dependsOn`` forms a cycle.
            MigrationDependencyMissingError: If a dependency is not present.
            MigrationError: On a duplicate id.
        """
        unordered = cls(migrations)
        return cls(unordered._topological_order())

    def __iter__(self) -> Iterator[Migration]:
        return iter(self.migrations)

    def __len__(self) -> int:
        return len(self.migrations)

    def __contains__(self, migration: object) -> bool:
        # Completes ``Collection`` alongside ``__iter__``/``__len__``: a consumer
        # typed on ``Collection[Migration]`` -- the pin guard's landed set -- can
        # then be handed this whole rather than a one-shot iterator it re-reads.
        return migration in self.migrations

    def get(self, migration_id: MigrationId) -> Migration | None:
        return self._by_id.get(migration_id)

    @property
    def ids(self) -> tuple[MigrationId, ...]:
        return tuple(m.migration_id for m in self.migrations)

    def _topological_order(self) -> tuple[Migration, ...]:
        """Kahn's algorithm, with ULID order as the tie-break.

        The tie-break is what makes ordering *deterministic* rather than merely
        valid. Two independent migrations must always apply in the same order,
        or the same inputs would produce different states on different machines
        and the state hash would stop identifying a state (ADR-0007).
        """
        remaining = {m.migration_id: set(m.depends_on) for m in self.migrations}
        ordered: list[Migration] = []

        while remaining:
            ready = sorted(
                (mid for mid, deps in remaining.items() if not deps),
                key=lambda mid: mid.value,
            )
            if not ready:
                raise MigrationCycleError(self._find_cycle(remaining))

            for migration_id in ready:
                ordered.append(self._by_id[migration_id])
                del remaining[migration_id]
            for deps in remaining.values():
                deps.difference_update(ready)

        return tuple(ordered)

    def _find_cycle(
        self, remaining: dict[MigrationId, set[MigrationId]]
    ) -> tuple[MigrationId, ...]:
        """Extract one concrete cycle, so the error names the actual problem.

        "A cycle exists" sends the reader hunting; "A -> B -> C -> A" does not.
        """
        start = min(remaining, key=lambda mid: mid.value)
        path: list[MigrationId] = []
        seen: set[MigrationId] = set()
        current = start

        while current not in seen:
            seen.add(current)
            path.append(current)
            candidates = sorted(
                (d for d in remaining[current] if d in remaining), key=lambda mid: mid.value
            )
            if not candidates:
                break
            current = candidates[0]

        if current in path:
            return (*path[path.index(current) :], current)
        return tuple(path)


def current_revision_in(migrations: Iterable[Migration], item_id: ItemId) -> RevisionId | None:
    """The revision an item currently has after applying ``migrations`` in order.

    The approved migration set *is* the canonical state -- applying it to an
    empty database reproduces the state exactly (FR-K4) -- so a reader that needs
    to know an item's current revision without opening the state database can
    derive it here. Only :class:`UpsertRevision` moves ``current_revision_id``;
    a deprecate or a status change does not, which is why nothing else is
    consulted.

    ``migrations`` must already be in application order (a :class:`MigrationSet`
    iterates in that order): the *last* upsert for the item is the current one.
    Returns ``None`` when no migration ever revised the item -- it does not yet
    exist, so a proposal that creates it is a first revision, not an update.
    """
    current: RevisionId | None = None
    for migration in migrations:
        for operation in migration.operations:
            if isinstance(operation, UpsertRevision) and operation.item_id == item_id:
                current = operation.revision_id
    return current


@dataclass(frozen=True, slots=True)
class LoadedMigrations:
    """A validated migration set plus the content it references.

    Produced by an infrastructure loader, consumed by the application layer. It
    lives in the domain because it is composed entirely of domain types -- and
    because putting it in infrastructure would force the application layer to
    import an adapter to name its own input (ADR-0003).

    Carrying the bodies here means the engine performs no file I/O inside a write
    transaction (NFR-8).
    """

    migration_set: MigrationSet
    content_checksums: tuple[ContentHash, ...]
    #: Body text keyed by content hash.
    content_by_hash: dict[str, str]

    @classmethod
    def empty(cls) -> LoadedMigrations:
        return cls(MigrationSet(()), (), {})
