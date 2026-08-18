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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol

from theurian.domain.enums import KnowledgeStatus, RelationType, may_surface
from theurian.domain.errors import (
    DuplicateContentFileError,
    MigrationChecksumMismatchError,
    MigrationError,
    RevisionConflictError,
    ScopeViolation,
    UnenforceableScopeError,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId, SpecId
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
    def list_revision_ids(
        self, project_id: ProjectId, item_id: ItemId
    ) -> tuple[RevisionId, ...]: ...
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


@dataclass(frozen=True, slots=True)
class WithdrawalCandidate:
    """An item a withdrawal touched, and its final canonical state.

    Enough for the purge to decide -- at the *published index's own build flavor*,
    which only the index pointer records -- which of the item's revisions that
    index must stop holding (ADR-0024 decision 5). The engine cannot make that
    decision on its own: whether a ``draft`` is withheld depends on whether the
    index was built with ``--include-unapproved``, and the engine never reads the
    index. So it gathers the final state here and `revisions_to_purge` reduces it
    against the flavor the pointer carries.
    """

    status: KnowledgeStatus
    current_revision_id: str | None
    revision_ids: tuple[str, ...]


@dataclass(slots=True)
class ApplyReport:
    """The outcome of an apply run."""

    applied: list[MigrationId] = field(default_factory=list)
    skipped: list[MigrationId] = field(default_factory=list)
    operations_applied: int = 0
    #: The items this apply moved into or out of a withdrawn state, each with its
    #: **final** canonical state (status, current and all revisions) -- gathered
    #: after the apply commits, never accumulated per operation. The purge reduces
    #: these to a purge set against the published index's own flavor
    #: (`revisions_to_purge`), because whether a ``draft`` is withheld is a
    #: property of the *index*, not the store. Reading final state is what makes a
    #: restore cancel a deprecation, a reject-in-place withdraw an item whose
    #: revision id never changed, and a full replay idempotent.
    withdrawn_candidates: list[WithdrawalCandidate] = field(default_factory=list)

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


@dataclass(frozen=True, slots=True)
class UnpinnedRevision:
    """One ``upsertRevision`` whose body no ``contentSha256`` freezes."""

    migration_id: MigrationId
    revision_id: RevisionId
    #: The path as authored, not as resolved: this is what a reader edits.
    content_file: str


def unpinned_revisions(migration_set: MigrationSet) -> tuple[UnpinnedRevision, ...]:
    """Every ``upsertRevision`` that declares no ``contentSha256`` (issue #210).

    A warning's worth of information, not a refusal's: `migrate validate`
    reports these and keeps exit 0. The field is optional in the schema, both
    shipped example migrations omit it, and requiring it is a Milestone 7
    decision with a measured cost -- what can be said now is that an unpinned
    body is the one whose out-of-band edit nothing detects, and saying nothing
    at all was the state issue #210 was filed against.

    Reported per operation rather than per migration, unlike
    :func:`unenforceable_scope_violations`: the fix is a digest computed from
    one named body file, so collapsing two revisions of one migration into one
    id would drop the half of the answer that says which file.

    Returns them in migration and operation order -- deterministic, since a
    `MigrationSet` iterates in the application order it settled at
    construction.
    """
    return tuple(
        UnpinnedRevision(
            migration_id=migration.migration_id,
            revision_id=operation.revision_id,
            content_file=operation.content_file_path,
        )
        for migration in migration_set
        for operation in migration.operations
        if isinstance(operation, UpsertRevision) and not operation.content_pinned
    )


def refuse_duplicate_content_files(migration_set: MigrationSet) -> None:
    """Refuse a body file backing two different revisions (issue #210).

    A body file holds one version at a time and carries no history, so a set in
    which two *distinct* revisions read one path does not describe a state -- it
    describes whatever that file was last written with. Measured against the
    unpinned form: both migrations applied, exit 0, and the earlier revision
    recorded the later body under its own title and author. Nothing detects it
    afterwards, because the loader adopts the file's current hash where no
    ``contentSha256`` is declared, so the wrong record is internally consistent.

    **Keyed by revision id, which is what keeps two legitimate shapes working.**
    Re-declaring one revision against its own body is how an in-place status
    change is written -- the revision id does not move, ``append_revision`` is
    the no-op FR-K8 requires, and only ``status`` differs (ADR-0024 decision 5,
    the ``reject``/``inplace-draft`` faces in ``test_absence_proof.py``). And a
    *reused* revision id across two items, sharing a body, stays this function's
    business to let through: it is refused at write time by the guard that
    exists for it, whose error names the two items -- refusing it here first
    would replace that diagnosis with a less specific one for the more serious
    fault.

    Whole-set rather than pending-only, for the reason
    :func:`refuse_unenforceable_scope` is: `migrate validate` holds no store and
    so has no notion of pending, and the two commands must decide a statically
    decidable rule on identical input or reopen issue #36's class. An
    already-applied duplicate is refused too -- reachable only from a build
    older than this guard, and no less ambiguous for having landed.

    Compares :attr:`UpsertRevision.content_reference`, the loader's own resolved
    path, so ``a/./x.md`` and ``a/x.md`` collide.

    `migrate status` does not call this, matching how it treats the scope rule:
    its contract is observation, so it keeps reporting on a set the gating
    commands refuse.

    Raises:
        DuplicateContentFileError: On the first body file claimed by a second
            revision, in migration and operation order -- deterministic, since a
            `MigrationSet` iterates in the application order it settled at
            construction.
    """
    claimed_by: dict[str, tuple[RevisionId, MigrationId]] = {}
    for migration in migration_set:
        for operation in migration.operations:
            if not isinstance(operation, UpsertRevision):
                continue
            reference = operation.content_reference
            claim = claimed_by.get(reference)
            if claim is None:
                claimed_by[reference] = (operation.revision_id, migration.migration_id)
            elif claim[0] != operation.revision_id:
                raise DuplicateContentFileError(reference, claim[1], migration.migration_id)


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
            DuplicateContentFileError: If two operations reference one body
                file (issue #210). Checked last of the three and over the same
                whole set: the two above name a specific migration as wrong,
                while this one is a statement about the set, and reporting the
                narrower fault first is what keeps a reader from being sent to
                a second migration that is not the one to edit.
        """
        plan = self.plan(writer, project_id, migration_set)
        refuse_unenforceable_scope(migration_set)
        refuse_duplicate_content_files(migration_set)
        report = ApplyReport(skipped=list(plan.already_applied))

        # The items whose surfaceability or current revision an operation could
        # have moved. Their *final* state is gathered below and reduced to a purge
        # set by the flavor-aware `revisions_to_purge`, never accumulated per
        # operation.
        affected: set[ItemId] = set()
        for migration in plan.pending:
            for operation in migration.operations:
                self._apply_operation(writer, project_id, migration, operation)
                report.operations_applied += 1
                item_id = _withdrawal_affected_item(operation)
                if item_id is not None:
                    affected.add(item_id)
            writer.record_migration(
                project_id,
                migration.migration_id,
                migration.checksum.value,
                self._clock.now(),
            )
            report.applied.append(migration.migration_id)

        report.withdrawn_candidates = _gather_withdrawal_candidates(writer, project_id, affected)
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


def _withdrawal_affected_item(operation: Operation) -> ItemId | None:
    """The item an operation could move into (or out of) a withdrawn state.

    Only the three operations that touch an item's status or its current revision:
    a ``deprecateItem``, a ``restoreItem`` (which can *undo* a withdrawal), and an
    ``upsertRevision`` (which can supersede an old revision, or -- reusing a
    revision id and only changing status -- reject an item in place). The
    withdrawn set is then read off the final state of exactly these items, so an
    item some *other* apply withdrew and this one does not touch is left to the
    apply that did, and a replay that re-touches all of them recomputes the same
    answer.

    A ``changeSensitivity`` is deliberately *not* here, even though it moves a
    scope component (SEC-14, ADR-0008 decision 1). This set feeds only the
    withdrawal purge, which copies the published build and deletes withheld rows
    (`index_purge`) -- and a reclassification withholds none (its status and
    current revision are unchanged), so the purge would gather the item and then
    discard it. Nothing auto-rebuilds the index for it, and nothing needs to: the
    live response is item-authoritative (`result_payload` reads the item's current
    sensitivity, so a search reports the new label the instant the migration
    commits, before any rebuild), and the built index's stale ``sensitivity``
    column is read by no gate before #119 -- an unsigned local index row nothing
    reads is not a disclosure (SEC-7). That column matches canonical again after
    the next ``index build``, which re-derives at the item's current label.
    """
    match operation:
        case DeprecateItem() | RestoreItem() | UpsertRevision():
            return operation.item_id
        case _:
            return None


def _gather_withdrawal_candidates(
    writer: MigrationWriter, project_id: ProjectId, affected: set[ItemId]
) -> list[WithdrawalCandidate]:
    """The final canonical state of each item a withdrawal touched.

    Read once, inside the transaction, from the store rather than accumulated per
    operation -- which is what makes a restore cancel a deprecation, a reject in
    place register though its revision id never moved, and a replay idempotent.
    The purge, not this, decides which revisions to remove, because that depends
    on the *index's* flavor (`revisions_to_purge`).
    """
    candidates: list[WithdrawalCandidate] = []
    for item_id in affected:
        item = writer.get_item(project_id, item_id)
        if item is None:  # pragma: no cover - the affecting op created or requires it
            continue
        current = item.current_revision_id
        candidates.append(
            WithdrawalCandidate(
                status=item.status,
                current_revision_id=None if current is None else current.value,
                revision_ids=tuple(
                    revision_id.value
                    for revision_id in writer.list_revision_ids(project_id, item_id)
                ),
            )
        )
    return candidates


def revisions_to_purge(
    candidates: Sequence[WithdrawalCandidate], *, indexes_unapproved: bool
) -> list[str]:
    """Revision ids a published index must not hold, **at its own build flavor**.

    The class the withdrawal purge converges on (ADR-0024 decision 5): a published
    index holds no revision that is non-surfaceable *at that index's own build
    flavor*, nor non-current. ``indexes_unapproved`` is that flavor, read off the
    index pointer -- and it is load-bearing, because a uniform flavor is wrong in
    one direction each:

    - a **default** index (``indexes_unapproved=False``) holds only approved
      chunks, so a revision now ``draft`` or ``proposed`` -- an in-place status
      change reaching it from ``approved`` -- is non-surfaceable *there* and must
      go, along with ``deprecated``/``rejected``. Testing with the flag *True*
      leaves that now-draft chunk in the file, moving visible-row BM25 rankings
      (T-17a) in the shipped default;
    - an ``--include-unapproved`` index (``True``) was told to hold drafts and
      proposals, so those are surfaceable *there* and survive; only what is
      withheld under every flag -- deprecated, rejected -- and non-current
      revisions go. Testing with the flag *False* would delete a draft that build
      legitimately holds.

    So the same ``may_surface`` rule the surfacing gate uses is applied here with
    the index's own flag, which is what keeps the purge and the gate from
    disagreeing about what "withheld" means for a given build.

    Sorted only for a deterministic result across a replay. No runtime consumer
    observes the order -- the purge deletes by set membership -- so nothing pins
    the sortedness and nothing depends on it.
    """
    purge: set[str] = set()
    for candidate in candidates:
        surfaceable = may_surface(candidate.status, include_unapproved=indexes_unapproved)
        for revision_id in candidate.revision_ids:
            if not surfaceable or revision_id != candidate.current_revision_id:
                purge.add(revision_id)
    return sorted(purge)


__all__ = [
    "ApplyReport",
    "MigrationEngine",
    "MigrationPlan",
    "MigrationWriter",
    "UnpinnedRevision",
    "WithdrawalCandidate",
    "refuse_duplicate_content_files",
    "refuse_unenforceable_scope",
    "revisions_to_purge",
    "unenforceable_scope_violations",
    "unpinned_revisions",
    "verify_no_applied_migration_changed",
]
