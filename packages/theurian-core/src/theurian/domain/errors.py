"""Domain errors.

Every error carries enough structured context for a caller to act on it. A
message that says only "conflict" forces the reader back into the code.
"""

from __future__ import annotations

import errno as _errno
from typing import TYPE_CHECKING, Literal, NamedTuple

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


class DuplicateContentFileError(MigrationError):
    """Two revisions in one migration set back onto the same body file (issue #210).

    The two revisions are judged the same body by **filesystem identity**
    (``st_dev``/``st_ino``), not by the path string: a case-insensitive
    filesystem reaches one physical file through many spellings, and a
    string-keyed check would let a second revision slip a case-variant spelling
    past this refusal (the disclosure the re-key closes). So the two ``contentFile``
    values this names may *differ* as text while pointing at one inode.

    A body file cannot be independently frozen or attributed to each of two
    revisions: it holds one version at a time and there is one set of bytes to
    hash. Where no ``contentSha256`` is declared the loader adopts the file's
    current hash, so the earlier revision silently records the *later* body under
    its own title and author -- measured, self-consistent afterwards, undetectable.
    But the refusal is **unconditional of pinning**, and its reason must be true
    for the pinned case too: even when *both* revisions pin the same digest, one
    file backing two revisions still cannot attribute distinct bytes to each, so
    the hazard is the sharing itself, not the absence of a pin (issue #210's
    pinned-pair face).

    Names both offending revisions -- their ids, their migrations, and their
    *authored* paths (resolved path supplementary) -- so the message is diagnostic
    whether the two ops live in two migrations or one. The earlier "neither
    migration is wrong on its own" framing was false in the single-migration case,
    where one migration carries both ops and is wrong on its own.

    Carries no remedy text of its own, for the reason
    :class:`UnenforceableScopeError` carries none: whether the honest fix is an
    edit or a rebuild depends on whether the offending migration was already
    applied, and only a caller holding a store can know that.
    """

    def __init__(  # noqa: PLR0913 -- two structured references, keyword-only so a call cannot mis-order them
        self,
        *,
        first_migration: MigrationId,
        first_revision: RevisionId,
        first_content_file: str,
        second_migration: MigrationId,
        second_revision: RevisionId,
        second_content_file: str,
        resolved_content_path: str | None = None,
    ) -> None:
        self.first_migration = first_migration
        self.first_revision = first_revision
        self.first_content_file = first_content_file
        self.second_migration = second_migration
        self.second_revision = second_revision
        self.second_content_file = second_content_file
        self.resolved_content_path = resolved_content_path
        resolved_note = (
            f" (resolved: {resolved_content_path!r})" if resolved_content_path is not None else ""
        )
        super().__init__(
            f"revision {second_revision} (migration {second_migration}, body "
            f"{second_content_file!r}) and revision {first_revision} (migration "
            f"{first_migration}, body {first_content_file!r}) resolve to the same body "
            f"file on disk{resolved_note}. One body file cannot back two revisions: it "
            f"holds one version at a time and cannot be independently frozen or attributed "
            f"to each, so whichever revision is written first records whatever the file "
            f"holds then, under its own title and author -- and even a shared contentSha256 "
            f"cannot separate them, because there is only one set of bytes to hash."
        )


class AliasItemCollisionError(MigrationError):
    """An ``addAlias`` key collides with a live item id (SEC-13, T-21).

    An alias key is a string an author chooses, and the store resolves it *before*
    it looks up a status (``SqliteCanonicalStore._resolve_alias``). So a key equal
    to the id of an item whose final status is anything but ``deprecated`` -- a
    ``rejected`` item is the dangerous case -- lets a lookup for that retired id
    resolve to the approved item the alias points at, and a relation-visibility
    gate keyed on the resolved row then publishes the retired item's edge and its
    ``note`` (where the secret that caused the rejection lives) on the approved
    item's response. The one legitimate shape, ``deprecateItem(old)`` then
    ``addAlias(old -> new)``, leaves ``old`` ``deprecated`` and is exempt.

    Refused whole-set at write time so neither ``migrate validate`` nor ``migrate
    apply`` accepts it (issue #36 parity), in both directions: an ``addAlias``
    over an existing item, and a ``createItem`` at an id an existing alias keys.
    Names the alias, the item it points at, and the item's final status so the
    author can find the operation to remove; quotes no body and no note.

    Carries no remedy text of its own, for the reason
    :class:`UnenforceableScopeError` carries none: whether the honest fix is an
    edit or a rebuild depends on whether the offending migration was already
    applied, which only the caller holding a store can know
    (``cli/commands.py::ALIAS_ITEM_COLLISION_REMEDY`` decides).
    """

    def __init__(
        self,
        *,
        alias: ItemId,
        alias_target: ItemId,
        item_status: str,
        migration_id: MigrationId,
    ) -> None:
        self.alias = alias
        self.alias_target = alias_target
        self.item_status = item_status
        self.migration_id = migration_id
        super().__init__(
            f"{migration_id}: addAlias {alias} -> {alias_target} collides with knowledge item "
            f"{alias} (status {item_status}). An alias key and an item id must be distinct: a "
            f"lookup for {alias} resolves through the alias to {alias_target}, so the item "
            f"{alias} names could surface its content -- an edge, a note -- under {alias_target}."
        )


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

    ``ELOOP`` gets its own branch (round three), the file-level counterpart of
    :func:`_directory_unreadable_remedy`'s identical one: a `chmod` fixes a
    permission problem but does nothing about a symlink chain, so folding it
    into the ``EACCES``/``EPERM`` branch would send a reader to check a
    permission bit that was never wrong. A *dangling* symlink -- resolvable,
    but pointing at nothing -- has no branch of its own: it raises ``ENOENT``,
    which matches none of ``ELOOP``/``EACCES``/``EPERM``/``EISDIR`` above, so
    it falls through to the final ``return missing_or_wrong_text`` -- the same
    fallback every plain missing-file case already uses. What differs is only
    *which* text that parameter carries:
    :class:`MigrationFileUnreadableError`'s own ``missing_or_wrong_text``
    keyword (not a separate ``remedy`` keyword, which briefly existed on that
    class and was removed -- see its own docstring) lets its caller substitute
    "restore the target or remove the link" for the generic "confirm this
    still exists" text this function's default would otherwise return,
    without this function needing a branch of its own for it.
    """
    if errno_value == _errno.ELOOP:
        return f"{target!r} is a loop of symbolic links. Point it at a real file, then retry."
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
    ``migrations_dir.iterdir()`` and then opened in
    ``infrastructure/filesystem/migration_loader.py::_load_one``. Deliberately
    not the same class: there is no "resolves relative to" rule to restate
    here, because a migration file's own path is never author-chosen the way
    ``contentFile`` is -- it is whatever `.theurian/migrations/` already holds.
    Reproduced against the real CLI: a schema-valid migration `chmod 000`'d
    crashed `migrate validate --json` with a raw `PermissionError` Rich
    traceback, exit 1, empty stdout -- the identical escape
    `MigrationContentUnreadableError` closed for `contentFile`, one call site
    over.

    Round three widened this past the open-and-read failure above to the
    *enumeration* step that finds the path in the first place: a `*.yaml`
    entry that is a symlink loop, or a symlink whose target is missing, is
    also raised from here rather than silently dropped by
    ``Path.is_file()``'s own errno-swallowing (see
    ``infrastructure/filesystem/migration_loader.py``'s enumeration comment).
    The loop case reuses :func:`_read_failure_remedy`'s ``ELOOP`` branch; the
    dangling case passes ``missing_or_wrong_text`` explicitly, because
    "restore the target or remove the link" is specific to a symlink whose
    target is missing and does not fit the errno-keyed dispatch every other
    case here shares. This is the same ``missing_or_wrong_text`` fallback hook
    :func:`_read_failure_remedy` already exposes for
    :class:`MigrationContentUnreadableError`'s own missing-file text -- not a
    separate ``remedy`` keyword, which briefly existed here and silently
    ignored ``errno_value`` whenever both were passed (two sources of truth
    for the same string).
    """

    def __init__(
        self,
        migration_path: str,
        reason: str,
        errno_value: int | None = None,
        *,
        missing_or_wrong_text: str | None = None,
    ) -> None:
        self.migration_path = migration_path
        self.remedy = _read_failure_remedy(
            migration_path,
            errno_value,
            missing_or_wrong_text=(
                missing_or_wrong_text
                if missing_or_wrong_text is not None
                else f"Confirm {migration_path!r} still exists, then retry."
            ),
        )
        super().__init__(f"{migration_path!r} could not be read: {reason}")


def _directory_unreadable_remedy(
    directory: str, errno_value: int | None, *, missing_or_wrong_text: str | None = None
) -> str:
    """The cure selected by *why* ``.theurian/migrations/`` could not be
    probed or listed, the same errno-keyed shape :func:`_read_failure_remedy`
    already gives the per-file siblings above.

    ``ELOOP`` gets its own remedy (round two): a `chmod` fixes a permission
    problem but does nothing about a symlink chain, so folding it into the
    ``EACCES``/``EPERM`` branch would send a reader to check a permission bit
    that was never wrong. ``missing_or_wrong_text`` (round four) is the
    directory-level counterpart of :class:`MigrationFileUnreadableError`'s
    identically-named hook: a dangling ``migrations_dir`` symlink needs
    "restore the target or remove the link", not the generic residual text,
    and that text does not fit this function's single ``directory``-shaped
    contract any more than it fit :func:`_read_failure_remedy`'s. The residual
    branch -- neither a known permission errno, a loop, nor a caller-supplied
    override -- covers whatever else the platform can raise at this probe
    (round two's adversarial test injects ``ENAMETOOLONG``); it names no
    specific cause, because there is no single one to name, only "this is not
    a readable directory."
    """
    if errno_value == _errno.ELOOP:
        return (
            f"{directory!r} is a loop of symbolic links. Point it at a real directory, then retry."
        )
    if errno_value in (_errno.EACCES, _errno.EPERM):
        return (
            f"Confirm this user has read and execute permission on "
            f"{directory!r} and its parent directories, then retry."
        )
    if missing_or_wrong_text is not None:
        return missing_or_wrong_text
    return f"Confirm {directory!r} resolves to a readable directory, then retry."


class MigrationsDirectoryUnreadableError(MigrationError):
    """``.theurian/migrations/`` itself could not be probed or listed (issues
    #205, #214, round two's symlink-loop and residual-errno faces, and round
    four's dangling-symlink face).

    Six raw-IO shapes converge on this one class:

    1. **A parent denies traversal (#205).** ``pathlib.Path.is_dir()`` in this
       interpreter swallows ``ENOENT``/``ENOTDIR``/``ELOOP`` -- the well-formed
       "not a directory" case ``load_migrations`` already answers by returning
       an empty migration set -- but re-raises ``EACCES``. That escaped every
       command that resolves a project (`init`, `project register`, `project
       status`, and every one of `_require_project`'s callers -- nine as of
       2026-08-20; re-count with ``grep -rn '_require_project(as_json)'
       packages/theurian-core/src/theurian/cli/`` rather than trusting this
       number), `project status` included, even though its whole contract is to
       answer at exit 0 rather than crash. Measured against the real CLI:
       `chmod 000 .theurian` (also `chmod 400`, missing only the execute bit)
       crashed all of them with a raw `PermissionError` Rich traceback.
    2. **The directory itself denies listing (#214).** `chmod 000`/`0o111` on
       `migrations_dir` itself -- not its parent -- leaves the directory probe
       succeeding, since stat needs no permission on the target, only its
       ancestors. `pathlib.Path.glob("*.yaml")`'s own `scandir` used to catch
       the resulting `PermissionError` internally and yield nothing, so
       `migrate validate --json` reported `valid: true` with
       `migrationCount: 0` for a project whose migrations were never read, and
       `migrate apply --json` went on to create a state database for that
       empty set.
    3. **The directory lists but denies stat (#214).** `chmod 0o444` leaves
       `migrations_dir` readable -- `scandir` can list its entries -- but not
       traversable, so stat-ing each entry to filter to regular files raises
       `PermissionError` too, and unlike case 2 that one was never caught: it
       escaped as a raw Rich traceback.
    4. **The directory itself is a symlink loop (round two).** A chain longer
       than the platform's loop limit at `migrations_dir` made the previous
       `is_dir()`-based probe swallow `ELOOP` the same way it already swallows
       `ENOENT`/`ENOTDIR`, misreporting a loop as "does not exist" -- an empty
       migration set, and `migrate apply --json` seeding a state database for
       it, rather than the refusal every other member of this class gets.
    5. **A residual errno neither of the above names (round two).** Whatever
       else the platform can raise at the probe or the listing --
       `ENAMETOOLONG` is what round two's adversarial test injects, since it is
       portable and does not depend on constructing a real over-length path --
       still refuses, but with a remedy that does not misdiagnose it as a
       permission problem: "confirm this resolves to a readable directory,"
       not "check read and execute permission."
    6. **`migrations_dir` itself is a dangling symlink (round four).** Distinct
       from case 4's loop: a chain that terminates at nothing, rather than one
       that never terminates, made the previous target-following probe raise
       `ENOENT` -- the identical errno a directory that never existed at all
       raises -- so a dangling `migrations_dir` used to fold into
       `LoadedMigrations.empty()` right alongside it. `load_migrations` now
       checks `migrations_dir.is_symlink()` (an `lstat`, checked before the
       following probe) first, so a dangling link is told apart from a
       genuinely absent directory before that probe ever runs, and refuses
       with `missing_or_wrong_text` naming the link rather than the generic
       residual text.

    All six are now raised from ``load_migrations``'s ``is_symlink()`` check,
    its explicit ``os.stat`` probe, and its enumeration `try`, all keying the
    remedy on ``errno_value`` (or an explicit ``missing_or_wrong_text``) via
    :func:`_directory_unreadable_remedy`. A `migrations_dir` symlink that
    resolves *outside* `project_root` is not a member of this class -- see
    :class:`PathEscapeError`, raised directly by the same `is_symlink()` check
    rather than folded in here, since "escapes the root" is a different fault
    from "cannot be read at all."

    The ``is_symlink()`` check covers the *final* path component only. A
    symlinked *ancestor* of `migrations_dir` -- `.theurian` itself being a
    symlink -- is a different class again, keyed on the writer/context stack
    trusting a resolved `.theurian` rather than on this read path, and is
    tracked at issue #237, not here.
    """

    def __init__(
        self,
        migrations_path: str,
        reason: str,
        errno_value: int | None = None,
        *,
        missing_or_wrong_text: str | None = None,
    ) -> None:
        self.migrations_path = migrations_path
        self.remedy = _directory_unreadable_remedy(
            migrations_path, errno_value, missing_or_wrong_text=missing_or_wrong_text
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
    any user's project. Round two widened "touching it failed" past the read
    itself: a read that *succeeds* can still hand back a schema this build
    cannot use -- truncated or empty JSON, or non-UTF-8 bytes. Round three
    widened it again to a read that parses cleanly but is not usable as a
    schema: not a JSON object at all (a list, or a bare boolean -- both
    otherwise-valid JSON, and the latter otherwise a valid top-level JSON
    Schema too, refused anyway because it would build a validator that
    accepts everything), or an object whose own keywords are structurally
    malformed against the JSON Schema metaschema. All of these are translated
    here rather than left to reach a migration author as a misattributed
    :class:`MigrationError` (see ``_validator``'s own docstring,
    ``infrastructure/filesystem/migration_loader.py``, for the full list).
    Not a :class:`MigrationError`: nothing about migration *content* failed,
    the installation this build ships with did.
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


#: What a caller has actually *proved* about the entry it is naming in a
#: :class:`PathEscapeError` (issue #233). The role picks the remedy, and each
#: one is exactly as strong as the check behind it:
#:
#: * ``"symlink"`` -- the caller proved this entry is *the component that
#:   escaped*: its own ``lstat`` says symbolic link, its parent chain resolves
#:   inside the root, and it still resolves outside. Only this role may tell
#:   anyone to repoint or remove it, and only because all three hold together.
#: * ``"resolved"`` -- this entry is the path that resolved outside the root,
#:   but *which component* carried it there is unknown: an ancestor directory
#:   may be the link while the entry is an ordinary file, or a link whose own
#:   target is perfectly ordinary. The remedy sends the reader up the chain and
#:   never proposes deleting anything.
#: * ``"referrer"`` -- this entry is the file that *names* the offending path.
#:   It is where to look, not what is wrong, and it is not itself outside.
EscapeRole = Literal["symlink", "resolved", "referrer"]


class EscapeSite(NamedTuple):
    """A name a refusal may print, paired with what the caller proved about it.

    One value rather than two parameters, because the pairing is the invariant.
    An earlier commit on this branch carried a bare ``entry: str`` whose remedy
    asserted "is a symbolic link ... or remove it" for every name it was given,
    and ``_load_one`` passed one for a *plain file* whose escaping component was
    an ancestor directory. Following that instruction deleted the user's own
    authored migration, and ``migrate validate`` then exited 0 with
    ``.theurian`` still pointing outside the project: the remedy walked the
    reader into losing work *and* into a silent pass.

    **The acceptance every remedy here must satisfy: in every construction where
    the remedy proposes removing or repointing X, doing so must actually cure
    the escape.** Keying the strong role on ``is_symlink()`` alone does not meet
    it -- that proves "this name is a link", never "this link is the escape".
    Three constructions earn the ``lstat`` and break the promise anyway: an
    entry linking to a *sibling* under an escaped ancestor, a
    ``migrations``-directory link that is lexically in-project under an escaped
    ancestor, and an entry whose own link text is in-project (``../../lib/x``)
    while ``lib`` is the escape.

    What does meet it is the conjunction :data:`EscapeRole` describes: the
    parent chain resolves inside the root, and the full path resolves outside.
    Resolution walks components left to right, so a base *inside* the root and a
    result *outside* it leave exactly one candidate -- the final component --
    and ``lstat`` confirms that component is a link. Remove it and the escape is
    gone; repoint it so it resolves inside and the escape is gone. Where any
    conjunct fails, the role degrades to ``"resolved"``, whose remedy proposes
    nothing destructive and is complete for every construction that can reach
    it: "check each directory above it" covers an escaping ancestor, and covers
    a link whose target chain escapes further up.

    The conjuncts are established by adjacent probes, not atomically -- a
    concurrent replacement between them can leave a printed role describing a
    path that has since changed. That residual is bounded by what the role
    *does*: it selects advice for a human who will then go and look, never an
    action Theurian takes.
    """

    name: str
    role: EscapeRole


class PathEscapeError(SecurityError):
    """A path resolved outside its permitted root.

    Raised for ``..`` traversal, absolute paths, and symlinks that leave the
    root. A path merely nested too deep is refused by
    :class:`PathDepthExceededError`, a subclass, because it need not have left
    the root at all.

    **What is never rendered.** The ``requested`` *field* is never read for
    output -- at most of this class's call sites it holds attacker-controlled
    text, a ``contentFile`` written by whoever authored the migration (SEC-7).
    (Where an ``entry`` is given, its name can coincide with ``requested`` by
    construction, and *that* string is rendered: what the rule forbids is
    reaching for the field, not the accident of two variables holding equal
    values.) The ``root`` field is likewise never read for output, because it
    is absolute and no sibling refusal on this load path prints an absolute
    path.

    **What is rendered.** ``entry`` is how a refusal still says *where* the
    problem is (issue #233), and it is supplied by the caller rather than
    derived here, because only the caller knows whether it holds a name that is
    safe to print: a ``project_root``-relative path Theurian itself produced by
    listing its own directory, not one an author chose. It is rendered with the
    same ``!r`` quoting :class:`MigrationFileUnreadableError` and
    :class:`MigrationsDirectoryUnreadableError` apply to theirs, which is what
    keeps a control character in a filename from reaching a terminal raw.

    **Why the role exists.** An earlier commit on this branch justified an
    unconditional "this is a symbolic link" remedy by arguing that a name
    ``iterdir()`` returned from inside the project has no ``..`` to climb with,
    so a symlink is the only way it can escape. That argument was about the
    *final component* and never covered the ancestors: ``.theurian`` itself
    being an outside-pointing symlink -- reachable from a plain ``git clone``,
    issue #237 -- makes every plain file under it resolve outside while none of
    them is a link. Narrowing the claim to "``lstat`` says link" was still not
    enough; :class:`EscapeSite` carries the acceptance that finally is.

    Where no ``entry`` is given at all, the remedy names every mechanism and
    stays root-agnostic: three raise sites in
    ``application/proposal_service.py`` protect ``.theurian/knowledge`` rather
    than the project root, so "keep it inside the project" would be advice a
    caller has already followed.

    Exported by name rather than through an ``__all__``: this module declares
    none, and every consumer imports the symbol it needs directly.

    Before this class carried a remedy, ``cli/commands.py::_context_remedy``
    fell through to its generic default and told a user whose
    ``.theurian/migrations`` was an outside-pointing symlink to "run this
    inside an initialised Theurian project" -- which is where they already
    were. That is the "exception that does not describe itself" shape issue
    #205's :attr:`TheurianError.remedy` exists to end.
    """

    def __init__(self, requested: str, root: str, *, entry: EscapeSite | None = None) -> None:
        self.requested = requested
        self.root = root
        self.entry = entry
        if entry is None:
            self.remedy = (
                "Keep the referenced path inside the permitted root and within its depth "
                "limit: remove any `..` that climbs above the root, any absolute prefix, "
                "and any symbolic link that leaves it, then retry."
            )
            super().__init__("Path escapes the permitted root")
            return
        self.remedy = _path_escape_remedy(entry)
        if entry.role == "referrer":
            super().__init__(f"{entry.name!r} names a path that escapes the permitted root")
            return
        super().__init__(f"{entry.name!r} escapes the permitted root")


def _path_escape_remedy(entry: EscapeSite) -> str:
    """The cure selected by what the caller proved, the same role-keyed shape
    :func:`_read_failure_remedy` and :func:`_directory_unreadable_remedy` give
    their own siblings.

    Only the ``"symlink"`` branch proposes removing the named thing, and it is
    reached only where :class:`EscapeSite`'s acceptance holds -- so removal
    genuinely cures the escape there. The other two send the reader looking
    without telling them to delete anything, because at those call sites the
    named entry may be a file the user wrote and the fault may be an ancestor
    directory or a line inside the file.

    "Repoint it **so it resolves** inside the project", not "repoint it inside
    the project": link text and resolved location are different things, and a
    link reading ``../../lib/real.yaml`` is already lexically inside a project
    it escapes through ``lib``.
    """
    if entry.role == "symlink":
        return (
            f"{entry.name!r} is a symbolic link resolving outside the project. "
            f"Repoint it so it resolves inside the project, or remove it, then retry."
        )
    if entry.role == "referrer":
        return (
            f"The path {entry.name!r} names resolves outside the project. Check each "
            f"directory it traverses for a symbolic link that leaves the project, then "
            f"retry. A leading `..` is the canonical form here "
            f"(docs/protocol/migrations.md, 'Path safety'), so removing it is not the cure."
        )
    return (
        f"{entry.name!r} resolves outside the project. Check it and each directory above "
        f"it for a symbolic link that leaves the project, then repoint or remove that "
        f"link and retry. Do not delete {entry.name!r} itself: an ancestor directory can "
        f"be the link while this entry is an ordinary file."
    )


class PathDepthExceededError(PathEscapeError):
    """A path nested deeper below the root than the containment check will walk.

    A subclass rather than a plain :class:`PathEscapeError`, because the two say
    different things: this path may sit entirely *inside* the root and still be
    refused, so "Path escapes the permitted root" was simply false for it. It
    stays under :class:`PathEscapeError` so that every existing ``except`` and
    every exit-code route keeps catching it unchanged -- what changes is only
    what the caller is told.

    ``limit`` is passed in rather than imported: the constant lives in
    ``security/paths.py``, which imports *this* module, and reaching back for it
    would close the cycle.
    """

    def __init__(self, requested: str, root: str, *, limit: int) -> None:
        self.requested = requested
        self.root = root
        self.entry = None
        self.limit = limit
        self.remedy = (
            f"Keep the referenced path within {limit} path segments below the permitted "
            f"root -- flatten the directories it nests through -- then retry."
        )
        # `SecurityError.__init__`, deliberately skipping `PathEscapeError`'s:
        # that one composes the message from `entry`, and this refusal's message
        # is not about an escape at all.
        SecurityError.__init__(self, f"Path exceeds the permitted depth limit of {limit} segments")


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
