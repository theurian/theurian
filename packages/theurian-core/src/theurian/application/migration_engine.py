"""The knowledge migration engine (ADR-0005, ADR-0006, ADR-0018).

Applies an ordered migration set to a canonical store, enforcing the guarantees
that make the store trustworthy:

- an applied migration is frozen (checksum mismatch is fatal, never repaired);
- ``expectedRevision`` conflicts are reported, never merged;
- re-application is a no-op, so the engine is safe to run repeatedly;
- all operations in one migration share one transaction.

This module depends on the domain and on a writer protocol. It never imports an
adapter, so the same engine runs against SQLite today and PostgreSQL later.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol

from theurian.domain.enums import KnowledgeStatus, RelationType
from theurian.domain.errors import (
    MigrationChecksumMismatchError,
    MigrationError,
    RevisionConflictError,
    ScopeViolation,
    UnenforceableScopeError,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, SpecId
from theurian.domain.knowledge import (
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
)
from theurian.domain.migration import (
    AddAlias,
    AddEvidence,
    AddRelation,
    ChangeOwner,
    ChangeSensitivity,
    CreateItem,
    DeprecateItem,
    Migration,
    MigrationSet,
    Operation,
    RegisterSpecification,
    RemoveAlias,
    RemoveEvidence,
    RemoveRelation,
    RestoreItem,
    SupersedeSpecification,
    UpsertRevision,
)
from theurian.domain.ports import Clock
from theurian.domain.specification import Specification
from theurian.domain.values import AclGroup, ContentHash, TenantId, ValidityPeriod


class MigrationWriter(Protocol):
    """The write surface the engine needs.

    Narrower than the full store: the engine appends and reads back within one
    transaction, and nothing more. A narrow protocol is also what makes an
    in-memory fake practical to write.
    """

    def append_revision(self, revision: KnowledgeRevision) -> None: ...
    def put_item(self, item: KnowledgeItem) -> None: ...
    def get_item(self, project_id: ProjectId, item_id: ItemId) -> KnowledgeItem | None: ...
    def add_relation(self, relation: KnowledgeRelation) -> None: ...
    def remove_relation(self, relation: KnowledgeRelation) -> None: ...
    def add_alias(self, alias: KnowledgeAlias) -> None: ...
    def remove_alias(self, project_id: ProjectId, alias: ItemId) -> None: ...
    def add_evidence(self, evidence: KnowledgeEvidence) -> None: ...
    def remove_evidence(self, project_id: ProjectId, item_id: ItemId, source_uri: str) -> None: ...
    def register_specification(self, specification: Specification) -> None: ...
    def supersede_specification(
        self, project_id: ProjectId, spec_id: SpecId, superseded_by: SpecId
    ) -> None: ...
    def record_migration(
        self,
        project_id: ProjectId,
        migration_id: MigrationId,
        checksum: str,
        applied_at: datetime,
    ) -> None: ...
    def applied_migrations(self, project_id: ProjectId) -> tuple[tuple[MigrationId, str], ...]: ...


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """What applying a set would do, without doing it."""

    pending: tuple[Migration, ...]
    already_applied: tuple[MigrationId, ...]
    total: int

    @property
    def is_empty(self) -> bool:
        return not self.pending


@dataclass(slots=True)
class ApplyReport:
    """The outcome of an apply run."""

    applied: list[MigrationId] = field(default_factory=list)
    skipped: list[MigrationId] = field(default_factory=list)
    operations_applied: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def verify_no_applied_migration_changed(
    recorded: Mapping[MigrationId, str], migration_set: MigrationSet
) -> None:
    """Assert every already-applied migration still hashes to what was recorded.

    Raises:
        MigrationChecksumMismatchError: On the first mismatch.

    This must be checked against **any** history that recorded a migration, not
    only the history of the state currently being built.

    The reason is an interaction between two decisions. Editing a migration
    changes the state hash (ADR-0016), which routes the next command to a fresh,
    empty database -- where nothing has been applied and nothing looks wrong. The
    evidence that an applied migration was edited lives only in the *previously
    active* database. Checking just the current one would make the guarantee in
    ADR-0005 silently unenforceable exactly when it matters.
    """
    for migration in migration_set:
        recorded_checksum = recorded.get(migration.migration_id)
        if recorded_checksum is not None and recorded_checksum != migration.checksum.value:
            raise MigrationChecksumMismatchError(
                migration.migration_id, recorded_checksum, migration.checksum.value
            )


#: The only tenant and ACL group a document may name until a real
#: `AuthorizationProvider` exists (issue #63). Read off `TenantId`/`AclGroup`'s
#: own defaults rather than duplicated as literals, so the enforced value and
#: the domain type's documented default cannot drift apart.
_ENFORCED_TENANT_ID: Final = TenantId().value
_ENFORCED_ACL_GROUP: Final = AclGroup().value


def refuse_unenforceable_scope(migration_set: MigrationSet) -> None:
    """Refuse a revision naming a tenant or ACL group nothing can enforce yet.

    No `AuthorizationProvider` (`domain/ports/authorization.py`) is implemented
    anywhere in this tree, so a revision naming a tenant other than `local` or
    an ACL group other than `default` would read as a security boundary while
    nothing checks it. Refused at write time (issue #63) rather than accepted
    silently.

    Called on the *whole* `migration_set`, not merely what is still pending --
    :meth:`MigrationEngine.apply` calls this after planning (a checksum tamper
    against the current database is reported first; see `apply`'s own
    docstring), and `migrate validate` calls it directly on the same
    `LoadedMigrations.migration_set` with no store and no engine involved.
    Checking the same input the same way is what keeps `migrate validate` and
    `migrate apply` in agreement for this one statically decidable rule (issue
    #36's class: a property visible to one and not the other).

    `migrate status`, the third and non-gating consumer of a migration set,
    does not call this -- see `unenforceable_scope_violations`, which reports
    every violation instead of stopping at the first, and never raises.

    Raises:
        UnenforceableScopeError: On the first revision naming an unenforced
            tenant or ACL group, in migration and operation order.
    """
    for migration in migration_set:
        for operation in migration.operations:
            if isinstance(operation, UpsertRevision):
                _refuse_operation_scope(migration.migration_id, operation)


def unenforceable_scope_violations(migration_set: MigrationSet) -> tuple[MigrationId, ...]:
    """Every migration id `refuse_unenforceable_scope` would refuse, without raising.

    `migrate status` reports observation, not a gate (issue #63's MEDIUM-3): a
    migration set containing an unenforceable tenant or ACL group is still a
    reachable set worth reporting on (`stateBuilt`, `pending`, `applied` all
    keep their meaning), so `status` must not raise the way `validate`/`apply`
    do. It still needs to say *which* migrations carry the problem -- the
    property this module exists to surface must not become invisible on the
    one consumer that keeps going.

    Returns ids in migration order, once each, even when a single migration
    carries more than one violating revision.
    """
    refused: list[MigrationId] = []
    for migration in migration_set:
        if any(
            _scope_violations(operation)
            for operation in migration.operations
            if isinstance(operation, UpsertRevision)
        ):
            refused.append(migration.migration_id)
    return tuple(refused)


def _refuse_operation_scope(migration_id: MigrationId, operation: UpsertRevision) -> None:
    violations = _scope_violations(operation)
    if violations:
        raise UnenforceableScopeError(migration_id, operation.revision_id, violations)


def _scope_violations(operation: UpsertRevision) -> tuple[ScopeViolation, ...]:
    """Every unenforced scope field on one ``upsertRevision``, without raising.

    Both fields are collected rather than stopping at the first: a revision
    naming a foreign tenant *and* a foreign ACL group is one problem a reader
    fixes once, not two errors discovered one `migrate validate` at a time.
    """
    metadata = operation.metadata
    violations: list[ScopeViolation] = []
    if metadata.tenant_id != _ENFORCED_TENANT_ID:
        violations.append(ScopeViolation("tenantId", metadata.tenant_id, _ENFORCED_TENANT_ID))
    if metadata.acl_group != _ENFORCED_ACL_GROUP:
        violations.append(ScopeViolation("aclGroup", metadata.acl_group, _ENFORCED_ACL_GROUP))
    return tuple(violations)


class MigrationEngine:
    """Applies migrations to a canonical store.

    ``content_by_hash`` is supplied by the loader, which has already read and
    hashed every body file. The engine therefore performs no file I/O inside a
    transaction (NFR-8): holding a write transaction across disk reads blocks
    every other writer for the duration.
    """

    def __init__(self, clock: Clock, content_by_hash: dict[str, str]) -> None:
        self._clock = clock
        self._content = content_by_hash

    # -- Planning ---------------------------------------------------------

    def plan(
        self, writer: MigrationWriter, project_id: ProjectId, migration_set: MigrationSet
    ) -> MigrationPlan:
        """Decide what to apply, and verify nothing already applied has changed.

        Raises:
            MigrationChecksumMismatchError: If an applied migration's file no
                longer matches its recorded checksum. Fatal: the recorded
                history and the file on disk make different claims about what
                was applied, and only a human can say which is right.
        """
        recorded = dict(writer.applied_migrations(project_id))
        verify_no_applied_migration_changed(recorded, migration_set)

        pending: list[Migration] = []
        already: list[MigrationId] = []

        for migration in migration_set:
            if migration.migration_id in recorded:
                already.append(migration.migration_id)
            else:
                pending.append(migration)

        return MigrationPlan(
            pending=tuple(pending),
            already_applied=tuple(already),
            total=len(migration_set),
        )

    # -- Application ------------------------------------------------------

    def apply(
        self, writer: MigrationWriter, project_id: ProjectId, migration_set: MigrationSet
    ) -> ApplyReport:
        """Apply every pending migration in order.

        The caller supplies ``writer`` from an open write transaction. Applying
        the same set twice produces an empty report rather than an error
        (FR-K8): idempotence is a property of the engine, not something each
        migration author has to implement.

        Raises:
            MigrationChecksumMismatchError: Checked first, via `plan`. A
                tampered checksum against the current database takes priority
                over the scope refusal below, matching `_verify_history`'s
                precedence at the CLI layer against the *previously* active
                database (issue #63's HIGH-3) -- a reader who sees this error
                should never have to wonder whether a hidden scope problem is
                the real reason an edit went unreported.
            UnenforceableScopeError: If any revision -- already applied or
                still pending -- names a tenant or ACL group nothing can yet
                enforce (issue #63). Checked over the whole set, after
                planning but before any write, so it never depends on what
                has already been written.
        """
        plan = self.plan(writer, project_id, migration_set)
        refuse_unenforceable_scope(migration_set)
        report = ApplyReport(skipped=list(plan.already_applied))

        for migration in plan.pending:
            for operation in migration.operations:
                self._apply_operation(writer, project_id, migration, operation)
                report.operations_applied += 1
            writer.record_migration(
                project_id,
                migration.migration_id,
                migration.checksum.value,
                self._clock.now(),
            )
            report.applied.append(migration.migration_id)

        return report

    def _apply_operation(  # noqa: PLR0912 -- a flat dispatch over 14 closed operations
        self,
        writer: MigrationWriter,
        project_id: ProjectId,
        migration: Migration,
        operation: Operation,
    ) -> None:
        match operation:
            case CreateItem():
                self._create_item(writer, project_id, operation)
            case UpsertRevision():
                self._upsert_revision(writer, project_id, migration, operation)
            case DeprecateItem():
                self._set_status(writer, project_id, operation.item_id, KnowledgeStatus.DEPRECATED)
                if operation.superseded_by is not None:
                    writer.add_relation(
                        KnowledgeRelation(
                            project_id=project_id,
                            source_item_id=operation.superseded_by,
                            relation_type=RelationType.SUPERSEDES,
                            target_item_id=operation.item_id,
                            created_at=self._clock.now(),
                            note=operation.reason,
                        )
                    )
            case RestoreItem():
                self._set_status(writer, project_id, operation.item_id, KnowledgeStatus.APPROVED)
            case AddRelation():
                writer.add_relation(
                    KnowledgeRelation(
                        project_id=project_id,
                        source_item_id=operation.source_item_id,
                        relation_type=operation.relation_type,
                        target_item_id=operation.target_item_id,
                        created_at=self._clock.now(),
                        note=operation.note,
                    )
                )
            case RemoveRelation():
                writer.remove_relation(
                    KnowledgeRelation(
                        project_id=project_id,
                        source_item_id=operation.source_item_id,
                        relation_type=operation.relation_type,
                        target_item_id=operation.target_item_id,
                        created_at=self._clock.now(),
                    )
                )
            case AddAlias():
                writer.add_alias(
                    KnowledgeAlias(
                        alias=operation.alias,
                        item_id=operation.item_id,
                        project_id=project_id,
                        created_at=self._clock.now(),
                    )
                )
            case RemoveAlias():
                writer.remove_alias(project_id, operation.alias)
            case ChangeSensitivity():
                item = self._require_item(writer, project_id, operation.item_id, migration)
                writer.put_item(_replace_item(item, sensitivity=operation.sensitivity))
            case ChangeOwner():
                item = self._require_item(writer, project_id, operation.item_id, migration)
                writer.put_item(_replace_item(item, owner=operation.owner))
            case RegisterSpecification():
                self._register_specification(writer, project_id, operation, migration)
            case SupersedeSpecification():
                writer.supersede_specification(
                    project_id, operation.spec_id, operation.superseded_by
                )
            case AddEvidence():
                writer.add_evidence(
                    KnowledgeEvidence(
                        item_id=operation.item_id,
                        project_id=project_id,
                        anchor=operation.anchor,
                        description=operation.description,
                        confidence=operation.confidence,
                        created_at=self._clock.now(),
                    )
                )
            case RemoveEvidence():
                writer.remove_evidence(project_id, operation.item_id, operation.source_uri)
            case _:  # pragma: no cover - the loader rejects unknown operations
                raise MigrationError(
                    f"{migration.migration_id}: unsupported operation {operation.kind}"
                )

    # -- Operation implementations ----------------------------------------

    def _create_item(
        self, writer: MigrationWriter, project_id: ProjectId, operation: CreateItem
    ) -> None:
        existing = writer.get_item(project_id, operation.item_id)
        if existing is not None:
            # Not an error: re-applying a migration must be a no-op (FR-K8), and
            # the create is the first thing a re-run would repeat.
            return

        now = self._clock.now()
        writer.put_item(
            KnowledgeItem(
                item_id=operation.item_id,
                project_id=project_id,
                namespace=operation.namespace,
                kind=operation.kind_,
                status=KnowledgeStatus.DRAFT,
                current_revision_id=None,
                owner=operation.owner,
                trust_level=operation.trust_level,
                sensitivity=operation.sensitivity,
                validity=ValidityPeriod(valid_from=now),
            )
        )

    def _upsert_revision(
        self,
        writer: MigrationWriter,
        project_id: ProjectId,
        migration: Migration,
        operation: UpsertRevision,
    ) -> None:
        item = writer.get_item(project_id, operation.item_id)
        self._check_expected_revision(item, operation)

        if operation.content_sha256 is None:  # pragma: no cover - loader always sets it
            raise MigrationError(
                f"{migration.migration_id}: {operation.revision_id} has no content hash"
            )
        body = self._content.get(operation.content_sha256.value)
        if body is None:  # pragma: no cover - loader populates every referenced body
            raise MigrationError(
                f"{migration.migration_id}: body for {operation.revision_id} was not loaded"
            )

        metadata = operation.metadata
        now = self._clock.now()
        valid_from = metadata.valid_from or now

        revision = KnowledgeRevision(
            revision_id=operation.revision_id,
            item_id=operation.item_id,
            project_id=project_id,
            migration_id=migration.migration_id,
            title=metadata.title,
            body=body,
            content_type=metadata.content_type,
            content_sha256=ContentHash.of_text(body),
            metadata=RevisionMetadata(
                kind=metadata.kind,
                namespace=metadata.namespace,
                status=metadata.status,
                trust_level=metadata.trust_level,
                sensitivity=metadata.sensitivity,
                owner=metadata.owner,
                tenant_id=TenantId(metadata.tenant_id),
                acl_group=AclGroup(metadata.acl_group),
                scope_paths=metadata.scope_paths,
                labels=metadata.labels,
            ),
            validity=ValidityPeriod(valid_from=valid_from, valid_to=metadata.valid_to),
            author=migration.author,
            created_at=migration.created_at,
            source_anchors=metadata.source_anchors,
        )
        writer.append_revision(revision)

        if item is None:
            item = KnowledgeItem(
                item_id=operation.item_id,
                project_id=project_id,
                namespace=metadata.namespace,
                kind=metadata.kind,
                status=metadata.status,
                current_revision_id=None,
                owner=metadata.owner,
                trust_level=metadata.trust_level,
                sensitivity=metadata.sensitivity,
                validity=ValidityPeriod(valid_from=valid_from),
            )
        writer.put_item(item.with_revision(revision))

    @staticmethod
    def _check_expected_revision(item: KnowledgeItem | None, operation: UpsertRevision) -> None:
        """Optimistic concurrency (ADR-0006).

        A mismatch is reported with both revisions, never merged: automatically
        reconciling two versions of a design decision produces text nobody
        approved.
        """
        actual = None if item is None else item.current_revision_id

        if operation.expected_revision is None:
            # Absent means "this creates the first revision". Applying it over an
            # existing revision would silently discard whatever is there --
            # unless it is a re-run of the very same revision, which is a no-op.
            if actual is not None and actual != operation.revision_id:
                raise RevisionConflictError(operation.item_id, None, actual)
            return

        if actual != operation.expected_revision:
            raise RevisionConflictError(operation.item_id, operation.expected_revision, actual)

    def _set_status(
        self,
        writer: MigrationWriter,
        project_id: ProjectId,
        item_id: ItemId,
        status: KnowledgeStatus,
    ) -> None:
        item = writer.get_item(project_id, item_id)
        if item is None:
            raise MigrationError(f"Cannot change the status of unknown item {item_id}")
        writer.put_item(item.with_status(status))

    @staticmethod
    def _require_item(
        writer: MigrationWriter,
        project_id: ProjectId,
        item_id: ItemId,
        migration: Migration,
    ) -> KnowledgeItem:
        item = writer.get_item(project_id, item_id)
        if item is None:
            raise MigrationError(f"{migration.migration_id} references unknown item {item_id}")
        return item

    def _register_specification(
        self,
        writer: MigrationWriter,
        project_id: ProjectId,
        operation: RegisterSpecification,
        migration: Migration,
    ) -> None:
        item = self._require_item(writer, project_id, operation.item_id, migration)
        if item.current_revision_id is None:
            raise MigrationError(
                f"{migration.migration_id}: cannot register {operation.spec_id} against "
                f"{operation.item_id}, which has no revision yet"
            )
        writer.register_specification(
            Specification(
                spec_id=operation.spec_id,
                project_id=project_id,
                revision_id=item.current_revision_id,
                title=operation.spec_id.value,
                status=operation.status,
                content_format=operation.content_format,
                source_uri=operation.source_uri,
                validity=ValidityPeriod(valid_from=self._clock.now()),
            )
        )


def _replace_item(item: KnowledgeItem, **changes: object) -> KnowledgeItem:
    """Return a copy of ``item`` with governance fields changed.

    Deliberately not a general-purpose setter: only fields a migration operation
    is allowed to move are reachable here.
    """
    allowed = {"sensitivity", "owner", "trust_level", "status"}
    unexpected = set(changes) - allowed
    if unexpected:  # pragma: no cover - guards a future caller, not current ones
        raise MigrationError(f"Cannot change {sorted(unexpected)} on an item")
    return dataclasses.replace(item, **changes)  # type: ignore[arg-type]


__all__ = [
    "ApplyReport",
    "MigrationEngine",
    "MigrationPlan",
    "MigrationWriter",
    "refuse_unenforceable_scope",
    "unenforceable_scope_violations",
    "verify_no_applied_migration_changed",
]
