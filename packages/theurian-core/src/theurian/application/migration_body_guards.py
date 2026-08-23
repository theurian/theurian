"""Body-file guards over a migration set (issue #210).

The static, whole-set checks about how ``upsertRevision`` operations reference
their body files -- extracted from ``migration_engine`` to keep that module under
its size ceiling. Each is a pure function of a ``MigrationSet``: they touch no
store, open no transaction, and read nothing from disk (the loader has already
resolved every path and taken every file's identity). ``MigrationEngine.apply``
and ``migrate validate``/``apply`` call :func:`refuse_duplicate_content_files`;
``migrate status`` reports rather than gates, so it calls the non-throwing
:func:`duplicate_content_file_violations` instead.

A third guard used to live here -- ``unpinned_revisions``, which warned about a
revision declaring no ``contentSha256``. ADR-0027 made the pin schema-required,
so no schema-valid document can reach that state and the warning went with the
``unpinnedRevisions`` field it fed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from theurian.domain.errors import DuplicateContentFileError
from theurian.domain.identifiers import MigrationId, RevisionId
from theurian.domain.migration import Migration, MigrationSet, UpsertRevision


@dataclass(frozen=True, slots=True)
class _ContentClaim:
    """The first revision to reference a given body file, for the refusal message."""

    revision_id: RevisionId
    migration_id: MigrationId
    content_file: str
    resolved_content_path: str | None


def _identified_upserts(
    migration_set: MigrationSet,
) -> Iterator[tuple[Migration, UpsertRevision, tuple[int, int]]]:
    """Every ``upsertRevision`` carrying a filesystem identity, in application order.

    The single place the None-identity skip lives, so the throwing and the
    non-throwing body-sharing guards cannot drift on *which* operations they
    compare. Yields ``(migration, operation, identity)`` for each
    ``upsertRevision`` whose ``content_identity`` is set, in the application order
    a ``MigrationSet`` iterates -- deterministic, so both guards report the same
    collision first.

    An operation whose ``content_identity`` is ``None`` is skipped, not compared:
    it has no file on disk and so cannot participate in an identity comparison,
    and folding it back onto the path string it happens to carry is exactly the
    weaker key issue #210 replaced -- two spellings of one file compare unequal
    as strings. The loader -- the sole *production* constructor of
    ``UpsertRevision`` -- sets the identity from the ``stat`` that read the body,
    so no gate reached from ``migrate validate``/``apply`` ever sees ``None``
    (pinned by ``test_migration_loader_identity.py``). A future in-memory
    production constructor that does not set the identity would be silently
    skipped here; it must set the identity, or that skip becomes a live hole and
    the change gets its own review.
    """
    for migration in migration_set:
        for operation in migration.operations:
            if not isinstance(operation, UpsertRevision):
                continue
            identity = operation.content_identity
            if identity is None:
                continue
            yield migration, operation, identity


def refuse_duplicate_content_files(migration_set: MigrationSet) -> None:
    """Refuse a body file backing two different revisions (issue #210).

    A body file holds one version at a time and carries no history, so a set in
    which two *distinct* revisions read one file does not describe a state -- it
    describes whatever that file was last written with. Measured against the
    unpinned form: both migrations applied, exit 0, and the earlier revision
    recorded the later body under its own title and author. Nothing detects it
    afterwards, because the loader adopts the file's current hash where no
    ``contentSha256`` is declared, so the wrong record is internally consistent.
    The refusal is **unconditional of pinning**: even a pair that both pin the
    same digest is refused, because one file cannot attribute distinct bytes to
    two revisions -- the hazard is the sharing, not the missing pin.

    **Keyed by filesystem identity (``st_dev``/``st_ino``), not the path string.**
    A case-insensitive filesystem (APFS, NTFS) reaches one physical file through
    many spellings -- ``note.md`` and ``NOTE.md``, an uppercase extension, a
    case-variant directory, an NFC/NFD pair -- each of which ``resolve()`` leaves
    distinct while ``stat`` returns one inode. Keying on the resolved path string
    let a second revision slip such a spelling past this refusal and cross-record
    a withheld body through an approved item's index (the disclosure this re-key
    closes). Casefolding the string instead would be wrong the other way, false-
    refusing two genuinely different files on a case-sensitive Linux filesystem;
    identity is correct on every platform. The loader sets ``content_identity``
    from the same ``stat`` that read the body, so a gate always sees it; an
    in-memory operation carries ``None`` and cannot participate, which is a skip,
    not the old silent fall-back to a weaker path-string key.

    **Distinct revision ids are what keep two legitimate shapes working.**
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
    :func:`~theurian.application.migration_engine.refuse_unenforceable_scope` is:
    `migrate validate` holds no store and so has no notion of pending, and the two
    commands must decide a statically decidable rule on identical input or reopen
    issue #36's class. An already-applied duplicate is refused too -- reachable
    only from a build older than this guard, and no less ambiguous for having
    landed.

    `migrate status` does not call this throwing form; it reports the same
    migrations without raising -- see :func:`duplicate_content_file_violations`,
    which surfaces them under ``refusedIds`` exactly as it does the scope rule.

    Raises:
        DuplicateContentFileError: On the first body file claimed by a second
            revision, in migration and operation order -- deterministic, since a
            `MigrationSet` iterates in the application order it settled at
            construction.
    """
    claimed_by: dict[tuple[int, int], _ContentClaim] = {}
    for migration, operation, identity in _identified_upserts(migration_set):
        claim = claimed_by.get(identity)
        if claim is None:
            claimed_by[identity] = _ContentClaim(
                revision_id=operation.revision_id,
                migration_id=migration.migration_id,
                content_file=operation.content_file_path,
                resolved_content_path=operation.resolved_content_path,
            )
        elif claim.revision_id != operation.revision_id:
            raise DuplicateContentFileError(
                first_migration=claim.migration_id,
                first_revision=claim.revision_id,
                first_content_file=claim.content_file,
                second_migration=migration.migration_id,
                second_revision=operation.revision_id,
                second_content_file=operation.content_file_path,
                resolved_content_path=claim.resolved_content_path,
            )


def duplicate_content_file_violations(migration_set: MigrationSet) -> tuple[MigrationId, ...]:
    """Every migration :func:`refuse_duplicate_content_files` would refuse, without raising.

    The non-throwing enumerator `migrate status` needs, the sibling of
    :func:`~theurian.application.migration_engine.unenforceable_scope_violations`.
    `status` reports rather than gates (issue #63's MEDIUM-3), so the statically
    decidable body-sharing property must be visible there too, under
    ``refusedIds`` -- it was not, and `status` reported ``refusedIds: []`` for a
    set `validate`/`apply` exit 4 on.

    Reports the *second* migration of each colliding pair -- the later one whose
    body a reader gives its own file, matching both the throwing form's culprit
    and the remedy. Every collision is reported, not only the first, and each
    migration id at most once, in migration and operation order.
    """
    claimed_by: dict[tuple[int, int], RevisionId] = {}
    refused: list[MigrationId] = []
    for migration, operation, identity in _identified_upserts(migration_set):
        claimant = claimed_by.get(identity)
        if claimant is None:
            claimed_by[identity] = operation.revision_id
        elif claimant != operation.revision_id and migration.migration_id not in refused:
            refused.append(migration.migration_id)
    return tuple(refused)


__all__ = [
    "duplicate_content_file_violations",
    "refuse_duplicate_content_files",
]
