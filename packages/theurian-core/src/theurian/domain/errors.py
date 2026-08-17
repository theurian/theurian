"""Domain errors.

Every error carries enough structured context for a caller to act on it. A
message that says only "conflict" forces the reader back into the code.
"""

from __future__ import annotations

import errno as _errno
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId


class TheurianError(Exception):
    """Base class for every error Theurian raises deliberately.

    ``remedy`` defaults to empty and is a class attribute, not a constructor
    parameter every subclass must thread through: a subclass that wants one
    sets ``self.remedy = ...`` in its own ``__init__`` (as :class:`ProjectError`
    already did before this attribute existed here), and one that does not is
    still safe to read -- ``exc.remedy`` is never an ``AttributeError``. CLI
    callers (``cli/commands.py::_context_remedy``, ``_require_project``) prefer
    a non-empty ``exc.remedy`` before falling back to a type-keyed default,
    which is what let two hand-enumerated ``isinstance``/``except`` lists over
    :class:`MigrationContentUnreadableError` and
    :class:`MigrationFileUnreadableError` collapse into that one check (issue
    #205).
    """

    remedy: str = ""


class DomainError(TheurianError):
    """A domain rule was violated."""


class InvalidIdentifierError(DomainError):
    """An identifier did not satisfy its format contract."""


class InvariantViolationError(DomainError):
    """A domain invariant would be broken by the attempted operation."""


class RevisionConflictError(DomainError):
    """An ``expectedRevision`` guard did not match the stored current revision.

    Reported rather than merged: automatically reconciling two competing versions
    of a design decision produces text nobody approved (ADR-0006).
    """

    def __init__(
        self,
        item_id: ItemId,
        expected: RevisionId | None,
        actual: RevisionId | None,
    ) -> None:
        self.item_id = item_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Revision conflict on {item_id}: "
            f"migration expected {expected or '<none>'}, store holds {actual or '<none>'}"
        )


class MigrationError(TheurianError):
    """A knowledge migration could not be validated or applied."""


class MigrationChecksumMismatchError(MigrationError):
    """An already-applied migration's file no longer matches its recorded checksum.

    Fatal and never auto-repaired: the recorded history and the file on disk make
    different claims about what was applied, and only a human can say which is
    right (ADR-0005).
    """

    def __init__(self, migration_id: MigrationId, recorded: str, observed: str) -> None:
        self.migration_id = migration_id
        self.recorded = recorded
        self.observed = observed
        super().__init__(
            f"Migration {migration_id} was applied with checksum {recorded} "
            f"but the file on disk hashes to {observed}. "
            f"An applied migration must never be edited."
        )


class MigrationCycleError(MigrationError):
    """``dependsOn`` declares a cycle, so no application order exists."""

    def __init__(self, cycle: tuple[MigrationId, ...]) -> None:
        self.cycle = cycle
        rendered = " -> ".join(str(m) for m in cycle)
        super().__init__(f"Migration dependency cycle: {rendered}")


class MigrationDependencyMissingError(MigrationError):
    """A migration depends on one that is not present in the reachable set."""

    def __init__(self, migration_id: MigrationId, missing: MigrationId) -> None:
        self.migration_id = migration_id
        self.missing = missing
        super().__init__(f"Migration {migration_id} depends on unknown migration {missing}")


def _read_failure_remedy(
    target: str, errno_value: int | None, *, missing_or_wrong_text: str
) -> str:
    """The cure selected by *why* a read failed, not one guess for every ``OSError``.

    Measured false before this existed: :class:`MigrationContentUnreadableError`
    carried one fixed remedy and a docstring claiming "the cure does not depend
    on which OS error caused the read to fail." For ``EACCES``/``EPERM`` the
    path was already right and the file already existed -- "fix the path, or
    restore the file" is not merely unhelpful there, it sends a reader to check
    two things that were never wrong. Selecting by ``errno`` is what makes the
    remedy match the failure instead of guessing at the single most common one
    (issue #205).

    ``errno_value`` is ``None`` for a failure that never reached the OS at all
    -- an embedded NUL raises ``ValueError`` from ``os.path.realpath`` before
    any syscall runs -- and for that case ``missing_or_wrong_text`` is exactly
    right: a NUL byte in a path is a malformed-path problem, the same family as
    "this path does not exist."
    """
    if errno_value in (_errno.EACCES, _errno.EPERM):
        return (
            f"Confirm this user has read permission on {target!r} and its "
            f"parent directory, then retry."
        )
    if errno_value == _errno.EISDIR:
        return f"{target!r} names a directory, not a file."
    return missing_or_wrong_text


class MigrationContentUnreadableError(MigrationError):
    """An ``upsertRevision`` operation's ``contentFile`` could not be read (issue #205).

    Raised at the one place the read happens
    (``infrastructure/filesystem/migration_loader.py::_parse_upsert``), translating
    a bare ``OSError`` or ``ValueError`` -- ``read_source_file``'s own docstring
    names ``FileNotFoundError`` as one of the things it raises, and an embedded
    NUL byte in ``contentFile`` makes ``Path.resolve()`` raise ``ValueError``
    before ``read_source_file`` is even reached -- into a :class:`TheurianError`
    subtype. That is the whole fix: every command that reaches
    ``resolve_context`` already guards ``TheurianError`` or one of
    ``MigrationError``'s existing subtypes around it, never a bare ``OSError``
    or ``ValueError``, so converting here is what every one of those callers
    inherits without its own patch (CP-2). Before this class existed, the
    read's `FileNotFoundError` escaped through Typer as a Rich traceback: exit
    1, empty stdout, no ``{error, remedy}`` payload, even under ``--json`` --
    reproduced against both ``migrate validate`` and ``init``. A NUL byte in
    ``contentFile`` -- the JSON Schema's ``contentFile`` definition checks
    type, length and a path-escape prefix, none of which excludes it --
    reproduced the identical escape one call site earlier, as ``ValueError:
    lstat: embedded null character in path``.
    """

    def __init__(
        self, migration_path: str, content_file: str, reason: str, errno_value: int | None = None
    ) -> None:
        self.migration_path = migration_path
        self.content_file = content_file
        self.remedy = _read_failure_remedy(
            content_file,
            errno_value,
            missing_or_wrong_text=(
                f"{content_file!r} resolves relative to the migration file "
                f"({migration_path!r}), not to a proposal directory "
                f"(docs/protocol/migrations.md, 'Path safety'). Fix the path, or "
                f"restore the referenced file, then retry."
            ),
        )
        super().__init__(
            f"{migration_path!r}: contentFile {content_file!r} could not be read: {reason}"
        )


class MigrationFileUnreadableError(MigrationError):
    """A migration file itself could not be read from disk (issue #205).

    :class:`MigrationContentUnreadableError`'s sibling for the *other* raw read
    on the same load path -- the migration YAML itself, discovered by
    ``migrations_dir.glob("*.yaml")`` and then opened in
    ``infrastructure/filesystem/migration_loader.py::_load_one``. Deliberately
    not the same class: there is no "resolves relative to" rule to restate
    here, because a migration file's own path is never author-chosen the way
    ``contentFile`` is -- it is whatever `.theurian/migrations/` already holds.
    Reproduced against the real CLI: a schema-valid migration `chmod 000`'d
    crashed `migrate validate --json` with a raw `PermissionError` Rich
    traceback, exit 1, empty stdout -- the identical escape
    `MigrationContentUnreadableError` closed for `contentFile`, one call site
    over.
    """

    def __init__(self, migration_path: str, reason: str, errno_value: int | None = None) -> None:
        self.migration_path = migration_path
        self.remedy = _read_failure_remedy(
            migration_path,
            errno_value,
            missing_or_wrong_text=f"Confirm {migration_path!r} still exists, then retry.",
        )
        super().__init__(f"{migration_path!r} could not be read: {reason}")


class MigrationsDirectoryUnreadableError(MigrationError):
    """``.theurian/migrations/`` itself could not be probed or listed (issue #205).

    ``pathlib.Path.is_dir()`` in this interpreter swallows ``ENOENT``/
    ``ENOTDIR``/``ELOOP`` -- the well-formed "not a directory" case
    ``load_migrations`` already answers by returning an empty migration set --
    but re-raises ``EACCES``. That escaped every command that resolves a
    project (`init`, `project register`, `project status`, and every one of
    `_require_project`'s seven callers), `project status` included, even
    though that command's whole contract is to answer at exit 0 rather than
    crash. Measured against the real CLI: `chmod 000 .theurian` (also
    `chmod 400`, missing only the execute bit) crashed all of them with a raw
    `PermissionError` Rich traceback.
    """

    def __init__(self, migrations_path: str, reason: str) -> None:
        self.migrations_path = migrations_path
        self.remedy = (
            f"Confirm this user has read and execute permission on "
            f"{migrations_path!r} and its parent directories, then retry."
        )
        super().__init__(f"{migrations_path!r} could not be listed: {reason}")


class SchemaUnreadableError(TheurianError):
    """The installed package's JSON Schema could not be probed or read (issue #205).

    Distinct from ``cli/context.py::schema_root``'s "neither candidate
    location exists" :class:`~theurian.application.project_service.ProjectError`
    -- itself defined in ``application/``, which no module under
    ``infrastructure/`` imports anywhere in this tree, and this class lives
    where this failure is raised, in ``infrastructure/filesystem/
    migration_loader.py``. ADR-0003 does not name this specific edge, only
    that ``application/`` "depends on `domain/` only"; a domain-level type is
    what stays importable from every layer regardless. This is "a candidate
    was found, but touching it failed" -- an ``.exists()`` probe or a
    ``read_text()`` that hit a permission problem on the installation, not on
    any user's project. Not a :class:`MigrationError`: nothing about
    migration *content* failed, the installation this build ships with did.
    """

    def __init__(self, schema_path: str, reason: str) -> None:
        self.schema_path = schema_path
        self.remedy = f"Reinstall theurian; {schema_path!r} could not be read ({reason})."
        super().__init__(f"{schema_path!r} could not be read: {reason}")


class ScopeViolation(NamedTuple):
    """One field on a revision naming a value nothing can yet enforce (issue #63).

    ``default`` is carried alongside ``field_name``/``value`` rather than left
    for the reader to look up, because the two fields this applies to
    (``tenantId``, ``aclGroup``) do not share one default.
    """

    field_name: str
    value: str
    default: str


class UnenforceableScopeError(MigrationError):
    """A revision names a tenant or ACL group nothing yet enforces (issue #63).

    ``tenantId`` and ``aclGroup`` are kept by the migration schema because they
    describe the hosted deployment's shape (ADR-0003), but no
    ``AuthorizationProvider`` (``domain/ports/authorization.py``) is implemented
    anywhere in this tree. Accepting a value other than the enforced default
    would let the field read as a security boundary while nothing checks it --
    so it is refused at write time instead of silently accepted.

    ``violations`` holds every offending field on the one revision, not only
    the first: a revision naming both a foreign tenant and a foreign ACL group
    is one problem statement, not two separate errors a reader fixes one at a
    time by re-running the command twice.

    Deliberately carries no "how to fix this" text -- whether the honest fix
    is "edit the field" or "rebuild state" depends on whether this revision
    was already applied, which only the caller holding a store can know
    (``cli/commands.py`` decides; see ``UNENFORCEABLE_SCOPE_REMEDY_APPLIED``
    and its unapplied counterpart, issue #63's HIGH-1).
    """

    def __init__(
        self,
        migration_id: MigrationId,
        revision_id: RevisionId,
        violations: tuple[ScopeViolation, ...],
    ) -> None:
        self.migration_id = migration_id
        self.revision_id = revision_id
        self.violations = violations
        named = "; ".join(f"{v.field_name} {v.value!r}" for v in violations)
        super().__init__(
            f"{migration_id}: revision {revision_id} names {named}, but Theurian has no "
            f"AuthorizationProvider implemented to enforce it (issue #63)."
        )


class SecurityError(TheurianError):
    """An operation was refused for a security reason."""


class PathEscapeError(SecurityError):
    """A path resolved outside its permitted root.

    Raised for ``..`` traversal, absolute paths, and symlinks that leave the root.
    The offending path is not echoed verbatim into user-facing output to avoid
    reflecting attacker-controlled text (SEC-7).
    """

    def __init__(self, requested: str, root: str) -> None:
        self.requested = requested
        self.root = root
        super().__init__(f"Path escapes the permitted root {root}")


class InputTooLargeError(SecurityError):
    """Input exceeded a configured parser limit (SEC-8)."""

    def __init__(self, limit_name: str, limit: int, observed: int) -> None:
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(f"{limit_name} exceeded: limit {limit}, observed {observed}")


class AuthorizationError(SecurityError):
    """The principal is not authorized for the requested project or action."""

    def __init__(self, project_id: ProjectId, action: str) -> None:
        self.project_id = project_id
        self.action = action
        super().__init__(f"Not authorized to {action} project {project_id}")


class CompatibilityError(TheurianError):
    """The plugin and Core versions, or their protocol versions, are incompatible.

    Always terminal and never self-healing: Theurian does not upgrade or downgrade
    anything on its own (§30 of the brief).
    """

    def __init__(self, message: str, *, remedy: str) -> None:
        self.remedy = remedy
        super().__init__(f"{message}\n{remedy}")
