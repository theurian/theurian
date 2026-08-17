"""Load migration files from disk into the domain model (ADR-0005).

Loading is where untrusted input enters the system. A migration file is written
by whoever can commit to the repository, and it names arbitrary paths. Every
check that keeps that safe lives here.
"""

from __future__ import annotations

import errno
import json
import stat
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.errors import (
    MigrationContentUnreadableError,
    MigrationError,
    MigrationFileUnreadableError,
    MigrationsDirectoryUnreadableError,
    PathEscapeError,
    SchemaUnreadableError,
)
from theurian.domain.identifiers import ItemId, MigrationId, RevisionId, SpecId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import (
    MIGRATION_API_VERSION,
    AddAlias,
    AddEvidence,
    AddRelation,
    ChangeOwner,
    ChangeSensitivity,
    CreateItem,
    DeprecateItem,
    LoadedMigrations,
    Migration,
    MigrationSet,
    Operation,
    RegisterSpecification,
    RemoveAlias,
    RemoveEvidence,
    RemoveRelation,
    RestoreItem,
    RevisionMetadataSpec,
    SupersedeSpecification,
    UpsertRevision,
)
from theurian.domain.values import ContentHash, MediaType
from theurian.security.paths import read_source_file, resolve_within_root
from theurian.security.yaml_loading import load_yaml_mapping

#: Ceiling on migration files *loaded* per project. Not a design limit -- it
#: bounds how many `Migration` objects a single load can produce, not the
#: directory walk that finds them: the check below runs *after* enumeration
#: -- the `iterdir()`/per-entry classification loop below, costlier than the
#: `glob("*.yaml")` it replaced (see that loop's own comment for why `glob`
#: is not used here) -- has already completed. Re-measured 2026-08-17 at
#: HEAD (round four), 5-run minimum, APFS/CPython 3.13.11: 10,000 `.yaml`
#: files, `glob` 13.8-14.5 ms vs classify 114.0-125.9 ms (7.9-9.1x); 1,000
#: `.yaml` mixed with 9,000 non-matching entries, 6.6-7.3 ms vs 35.6-37.2 ms
#: (4.9-5.6x). The earlier bcdec22 measurement this comment carried (1.27x,
#: 2.71x) understated both: that commit's loop classified with a bare
#: `is_file()`, before `_entry_is_migration_file`'s own unguarded
#: `is_symlink()` lstat -- one extra syscall per entry -- existed to widen
#: the gap. Ratios vary by machine and directory shape; the direction --
#: classification costlier, more so the larger the non-matching fraction --
#: is what this comment is pinning, not the exact multiples. A pathological
#: or generated directory still pays for the full walk before this refuses
#: to load what it found; it does not bound the walk's own cost.
MAX_MIGRATIONS: Final = 10_000

_SCHEMA_RELATIVE: Final = "migrations/migration.schema.json"


@lru_cache(maxsize=1)
def _validator(schema_root: Path) -> Draft202012Validator:
    """Build the migration-schema validator, translating a corrupted install.

    Reads the *installed package's* schema, never a path under a user's
    project -- `schema_root()` (`cli/context.py`) already raises
    `ProjectError` when neither candidate location exists at all, checked with
    `.exists()` before either is returned here. What that leaves is "a
    location was found, but touching it failed", and that covers four
    different kinds of failure, all translated here to `SchemaUnreadableError`
    rather than a `MigrationError` -- keeping install-integrity failures in
    the *type* instead of in whether the failure is caught at all:

    1. **The read itself fails.** A permission problem on site-packages or a
       symlink loop raises `OSError`; non-UTF-8 bytes raise `UnicodeDecodeError`
       at the same `read_text(encoding="utf-8")` call. Originally only the
       first was guarded, on the reasoning that install-integrity is not
       user-project state -- true, but beside the CP-2 point: an unguarded
       read still crashes every `--json` command that reaches
       `resolve_context` (issue #205's Class 1).
    2. **The read succeeds, the content is corrupt (round two).** Truncated or
       empty JSON raises `json.JSONDecodeError` -- itself a `ValueError`
       subclass, measured with both an unterminated string and a zero-byte
       file, two distinct messages from the same type.
    3. **The read succeeds, the JSON is well-formed but unusable as a schema
       (round three).** A document that is not a JSON *object* -- a list, or a
       bare `true`/`false`, both permitted by JSON Schema itself as a
       top-level schema -- is refused explicitly with `isinstance(schema,
       dict)` before `Draft202012Validator` ever sees it. This is not because
       either would let every migration validate: `{}` is an equally
       accept-everything schema and this build keeps it (see the residual
       paragraph below). It is because this build treats only a JSON *object*
       as usable schema material at all -- a list or a bare boolean is a
       different, install-shaped kind of corruption than a permissive but
       well-typed schema, and the two are refused for that reason, not for
       being permissive. A document that *is* an object but whose own
       keywords are structurally malformed -- `required` must be an array of
       strings, and a bare string passed `check_schema`'s check silently
       until this round -- is caught by an explicit
       `Draft202012Validator.check_schema(schema)` call, translating
       `jsonschema.exceptions.SchemaError` before any migration is ever
       checked against it; before this, that failure surfaced only when a
       schema-valid *migration* tripped over the schema's own defect, blaming
       the wrong document entirely (`Draft202012Validator({"required":
       "not-a-list"}).validate(...)` raises `'n' is a required property`,
       misattributed to whichever migration validated first).
    4. **The schema, or the JSON encoding it, nests past Python's recursion
       limit (round four).** `json.loads` and `Draft202012Validator.
       check_schema` both recurse into nested structure -- a document, or a
       schema's own nested keywords, deep enough exhausts the interpreter
       stack the identical way an attacker-controlled *migration* document
       already does (`security/yaml_loading.py`'s `RecursionError` ->
       `ValueError` translation, and `parsers/structured.py`'s `JsonParser`).
       `check_schema`'s own recursion is a regression this round's own call
       introduced: it did not exist before item 3 above added that call, and
       is measured directly at 400 levels of nested `not` keywords. Neither
       call's `RecursionError` is caught by any `except` clause above it, so
       it used to escape `_validator` raw -- crashing every `--json` command
       that resolves a project, the identical CP-2 escape every other member
       of this class was closed for.

    `{}` remains accepted -- a valid, if vacuous, JSON Schema that matches
    every instance, and deliberately not a third refusal alongside item 3's
    two (`test_validator_accepts_the_vacuous_empty_object_schema`,
    `CHANGELOG.md`'s round-three entry). It is the residual this build lives
    with rather than the reason `true`/`false` are refused: the type check
    above is about what shape of document this build treats as a schema, and
    `{}` already satisfies that shape.

    **Not translated here.** Item 4's `RecursionError` guard covers the schema
    document's own JSON and keyword structure, not what a `$ref` inside it
    resolves to: a validate-time `$ref` resolution failure -- including
    whatever network fetch `jsonschema`'s own reference resolution performs
    for a remote `$ref` -- is out of scope for this function and is not
    translated to `SchemaUnreadableError` (issue #235).

    A JSON list used to reach `Draft202012Validator` construction and raise
    `AttributeError` there instead -- `jsonschema` calls `schema.get(...)`
    internally, and a `list` has no `.get` -- which this file's own `except
    AttributeError` used to translate. The `isinstance` check above now
    refuses every non-dict schema before that call runs at all, and every
    dict that reaches construction has already passed `check_schema`, so no
    path here can raise `AttributeError` any more: removed rather than kept
    as a defensive clause nothing can drive (measured against `jsonschema`
    4.26.0's own `Draft202012Validator.__init__`, which does not itself call
    anything that could raise it for a schema-conformant dict).
    """
    schema_path = schema_root / _SCHEMA_RELATIVE
    try:
        text = schema_path.read_text(encoding="utf-8")
        schema = json.loads(text)
    except OSError as exc:
        raise SchemaUnreadableError(str(schema_path), exc.strerror or str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise SchemaUnreadableError(str(schema_path), str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise SchemaUnreadableError(str(schema_path), str(exc)) from exc
    except RecursionError as exc:
        reason = "the JSON document nests past the parser's safe recursion depth"
        raise SchemaUnreadableError(str(schema_path), reason) from exc

    if not isinstance(schema, dict):
        reason = f"parsed to a {type(schema).__name__}, not an object"
        raise SchemaUnreadableError(str(schema_path), reason)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaUnreadableError(str(schema_path), str(exc)) from exc
    except RecursionError as exc:
        reason = "the schema nests past check_schema's safe recursion depth"
        raise SchemaUnreadableError(str(schema_path), reason) from exc
    return Draft202012Validator(schema)


def validate_migration_document(document: Mapping[str, object], schema_root: Path) -> None:
    """Check a migration *document* against the published schema, without a file.

    The loader's own check reads a path; this one takes the parsed mapping, so
    a generator can refuse to write a migration it has just built wrong rather
    than leaving one on disk for a reviewer to discover. ADR-0013 point 3 is the
    reason it belongs at generation: the gap between a proposal and approved
    knowledge is human review, not format conversion.

    Raises:
        MigrationError: If the document does not satisfy the schema.
        SchemaUnreadableError: If the installed schema cannot be read, or
            parses to something this build cannot use as a schema -- raised
            from :func:`_validator`, which this calls before validating.
    """
    try:
        _validator(schema_root).validate(document)
    except ValidationError as exc:
        location = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise MigrationError(f"invalid migration at {location}: {exc.message}") from exc


def load_migrations(
    project_root: Path, migrations_dir: Path, schema_root: Path
) -> LoadedMigrations:
    """Load, validate, and order every migration under ``migrations_dir``.

    Args:
        project_root: The containment boundary. No file outside it is read.
        migrations_dir: Directory holding ``*.yaml`` migration files.
        schema_root: The repository's ``schemas/`` directory.

    Raises:
        MigrationError: On a malformed, duplicate, cyclic, or unresolvable file.
        PathEscapeError: If a ``contentFile`` points outside ``project_root``;
            if ``migrations_dir`` itself is a symlink that resolves outside
            ``project_root`` (round four; checked directly, at the probe --
            see :func:`_refuse_unusable_migrations_directory_symlink`); or if
            a migration file inside it is such a symlink, which still
            surfaces through ``_load_one``'s call to ``read_source_file``
            (``security/paths.py``): a migration *entry*'s path starts with
            `project_root` as a string regardless of where it resolves, so
            `read_source_file`'s own resolve-and-compare is what actually
            catches that one, one call site later -- the same mechanism the
            directory-level check no longer has to rely on. This type's own
            remedy is generic rather than naming which of these raised it
            (issue #233; out of scope here).
        InputTooLargeError: If a file exceeds its size limit.
        MigrationsDirectoryUnreadableError: If ``migrations_dir`` cannot be
            probed or listed for a reason other than genuinely not existing --
            a parent that denies traversal, the directory itself denying
            listing or per-entry stat, a symlink loop or dangling symlink at
            ``migrations_dir`` itself (round four), or any other raw
            ``OSError``.
        MigrationFileUnreadableError: If a migration file cannot be read once
            found -- or, for a ``*.yaml`` entry found during enumeration, if
            it is a symlink loop or resolves to nothing (round three; see
            :func:`_entry_is_migration_file`).
        MigrationContentUnreadableError: If an ``upsertRevision`` operation's
            ``contentFile`` cannot be resolved or read.
        SchemaUnreadableError: If the installed schema cannot be read, or
            parses to something this build cannot use as a schema.
    """
    _refuse_unusable_migrations_directory_symlink(migrations_dir, project_root)

    try:
        # `os.stat` (`Path.stat()`, following symlinks like `os.stat` does) is
        # probed explicitly rather than `Path.is_dir()`, which internally
        # ignores only `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP` and reports `False`
        # for those four alike -- it re-raises everything else, `EACCES`
        # included (that re-raise is exactly why a *parent* of
        # `migrations_dir` denying traversal already escaped as a raw
        # `PermissionError` before issue #205's fix; see
        # `MigrationsDirectoryUnreadableError`'s docstring, `domain/errors.py`,
        # for that history). What `is_dir()`'s swallowing *did* conflate
        # (round two) is a directory that never existed with one hidden
        # behind a symlink chain longer than the platform's loop limit: both
        # used to answer "nothing to load" here, and only the first one
        # should. `ENOENT`/`ENOTDIR` are the well-formed "not a directory"
        # case the `if not stat.S_ISDIR(...)` below already answers by
        # returning an empty migration set; every other errno -- `EACCES`
        # when a *parent* of `migrations_dir` denies traversal, `ELOOP` for
        # the loop, and the residual case round two's adversarial test drives
        # with `ENAMETOOLONG` -- is a refusal, keyed by
        # `_directory_unreadable_remedy` (`domain/errors.py`) so the remedy
        # matches the failure instead of guessing at the single most common
        # one. A dangling or outside-pointing symlink at `migrations_dir`
        # never reaches this probe at all -- `_refuse_unusable_migrations_
        # directory_symlink` above already refused it -- so this probe's own
        # `ENOENT` branch stays keyed to the genuinely-absent case only.
        probe = migrations_dir.stat()
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return LoadedMigrations.empty()
        raise MigrationsDirectoryUnreadableError(
            str(migrations_dir.relative_to(project_root)), exc.strerror or str(exc), exc.errno
        ) from exc
    if not stat.S_ISDIR(probe.st_mode):
        return LoadedMigrations.empty()

    # Enumeration is `iterdir()`-based rather than `glob("*.yaml")`, and both
    # the directory listing and the per-entry classification (`_entry_is_
    # migration_file` below) happen inside this one `try`, so that every
    # raw-IO failure the enumeration can hit surfaces as
    # `MigrationsDirectoryUnreadableError` rather than one of two divergent
    # failure modes (issue #214): `chmod 000`/`0o111` on `migrations_dir`
    # *itself* (rather than its parent, above -- the probe needs no
    # permission on the target, only on its ancestors) makes `os.scandir`
    # raise `PermissionError` when the listing starts, and `chmod 0o444`
    # leaves the directory *listable* but not *traversable*, so stat-ing each
    # entry raises `PermissionError` instead. `pathlib.Path.glob` caught the
    # first of those internally and yielded nothing -- a silent
    # `migrationCount: 0` false positive -- while the second escaped as a raw
    # traceback; both are one class now. An `OSError` raised for a *non*-
    # symlink entry here goes through the identical `ENOENT`/`ENOTDIR`-is-a-
    # race vs. everything-else-is-a-refusal split the probe above uses: the
    # directory can vanish or be replaced between the probe and this listing,
    # and that race gets the same "nothing to load" answer a directory that
    # was simply never created gets. A *symlink* entry's own resolution
    # failure is different in kind -- a real fault on a real entry, not a
    # race against the directory -- and is refused by
    # `_entry_is_migration_file` itself, as `MigrationFileUnreadableError`,
    # before the exception ever reaches this `except`.
    #
    # Sorted so a failure reports the first file in a stable order rather than
    # whichever the filesystem happened to yield first -- and sorted *before*
    # `_entry_is_migration_file` runs (round four), not after: the names are
    # collected and sorted in one pass, then classification runs over that
    # already-sorted list, so a classification failure (a dangling or looping
    # entry) also reports the lexicographically-first offender rather than
    # whichever entry `iterdir()` happened to yield first -- APFS and ext4
    # disagree on that order, and the two candidly used to disagree on which
    # of two simultaneous failures got named. `iterdir()` does not filter
    # dotfiles (unlike `glob.glob()`), matching `Path.glob("*.yaml")`'s own
    # measured behaviour that this enumeration replaces.
    try:
        candidates = sorted(p for p in migrations_dir.iterdir() if p.name.endswith(".yaml"))
        paths = [p for p in candidates if _entry_is_migration_file(p, project_root)]
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return LoadedMigrations.empty()
        raise MigrationsDirectoryUnreadableError(
            str(migrations_dir.relative_to(project_root)), exc.strerror or str(exc), exc.errno
        ) from exc
    if len(paths) > MAX_MIGRATIONS:
        raise MigrationError(f"{len(paths)} migration files exceeds the limit of {MAX_MIGRATIONS}")

    validator = _validator(schema_root)
    migrations: list[Migration] = []
    content_by_hash: dict[str, str] = {}

    for path in paths:
        migration = _load_one(path, project_root, migrations_dir, validator, content_by_hash)
        migrations.append(migration)

    return LoadedMigrations(
        migration_set=MigrationSet.ordered(tuple(migrations)),
        content_checksums=tuple(ContentHash(h) for h in sorted(content_by_hash)),
        content_by_hash=content_by_hash,
    )


def _refuse_unusable_migrations_directory_symlink(migrations_dir: Path, project_root: Path) -> None:
    """Refuse ``migrations_dir`` itself being a dangling, looping, or
    outside-project symlink (round four), before ``load_migrations``'s own
    target-following probe runs.

    That probe (``migrations_dir.stat()``) cannot tell a dangling symlink
    apart from a directory that never existed: both raise the identical
    ``ENOENT``, and both used to fold into ``LoadedMigrations.empty()``. An
    outside-pointing target is not something that probe checks at all -- it
    only asks whether the resolved path is a readable directory, never where
    it resolves to -- and an outside directory holding no ``*.yaml`` files
    never reached `_load_one`'s own containment check either, since an empty
    directory gives enumeration nothing to call it on. Both are wrong in the
    same direction as the already-fixed loop case (round two): a directory
    that is not safely usable reports "nothing to load" instead of refusing.

    ``migrations_dir.is_symlink()`` (an ``lstat``, which never follows the
    final component) is checked first, the identical shape
    :func:`_entry_is_migration_file` already uses for the per-entry case: a
    non-symlink ``migrations_dir`` returns immediately, leaving the probe's
    existing, unwidened policy as the only check that runs for it. Called
    unguarded, like that function's own ``is_symlink()`` call: any ``OSError``
    it raises (``EACCES`` from a parent that denies traversal) is translated
    the same way a resolution failure below is, since there is nothing more
    specific to say about it.

    Raises:
        MigrationsDirectoryUnreadableError: If the symlink is dangling
            (``ENOENT``) or loops (``ELOOP``, reusing the probe's existing
            remedy), or for any other resolution failure -- including one at
            ``is_symlink()``'s own ``lstat``.
        PathEscapeError: If the symlink resolves to a location outside
            ``project_root``.
    """
    relative = str(migrations_dir.relative_to(project_root))
    try:
        is_dir_symlink = migrations_dir.is_symlink()
    except OSError as exc:
        raise MigrationsDirectoryUnreadableError(
            relative, exc.strerror or str(exc), exc.errno
        ) from exc
    if not is_dir_symlink:
        return

    try:
        resolved = migrations_dir.resolve(strict=True)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise MigrationsDirectoryUnreadableError(
                relative,
                "symbolic link target is missing",
                missing_or_wrong_text=(
                    f"{relative!r} is a symbolic link whose target is missing. "
                    f"Restore the target or remove the link, then retry."
                ),
            ) from exc
        raise MigrationsDirectoryUnreadableError(
            relative, exc.strerror or str(exc), exc.errno
        ) from exc

    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as exc:
        raise PathEscapeError(relative, str(project_root)) from exc


def _entry_is_migration_file(entry: Path, project_root: Path) -> bool:
    """Classify one ``*.yaml`` entry found during enumeration (round three).

    Replaces a bare ``entry.is_file()`` in the enumeration comprehension.
    ``is_file()`` performs the identical following ``stat()`` internally, but
    it swallows every errno in CPython's own ``_IGNORED_ERRNOS`` -- including
    ``ELOOP`` and ``ENOENT`` -- and reports ``False`` for all of them alike,
    silently dropping a symlink loop or a dangling symlink from the
    enumerated set with no error at all: measured directly, two ``*.yaml``
    entries on disk, one real and one a symlink loop, reported
    ``migrationCount: 1``.

    ``entry.is_symlink()`` is checked first (an ``lstat``, which never
    follows the final component and so never itself raises for a loop or a
    missing target) to tell those two faults apart from an ordinary
    enumeration race:

    * A symlink whose resolution fails is a real fault on a real entry --
      refused by name as :class:`MigrationFileUnreadableError`, whatever the
      errno. ``ENOENT`` means the target is missing (a dangling link, given
      an explicit ``missing_or_wrong_text`` naming that); every other errno
      (``ELOOP`` for a loop chief among them) goes through
      :func:`_read_failure_remedy`.
    * A non-symlink entry that raises ``ENOENT``/``ENOTDIR`` was there when
      ``iterdir()`` listed it and is simply gone now -- a plain file removed
      mid-enumeration, not a fault on this entry. Skipped, not refused,
      matching how ``migrations_dir``'s own ``ENOENT``/``ENOTDIR`` race is
      treated one level up.
    * Any other non-symlink errno is re-raised bare, to reach the
      enumeration's own ``except OSError`` in :func:`load_migrations` and
      surface as ``MigrationsDirectoryUnreadableError`` -- a naive per-entry
      `try` that answered *this* case with "skip the entry" too would turn a
      directory-wide permission refusal into a silently shrunken migration
      set, the identical worse-regression trap the dangling/loop fix itself
      exists to avoid one shape over. Reaching this branch needs a non-symlink
      entry whose ``is_symlink()`` lstat *succeeds* but whose separate
      follow-``stat()`` then fails with something other than
      ``ENOENT``/``ENOTDIR``: a permission bit changing, or the entry being
      replaced by something unstattable, in the gap between the two calls
      (measured with ``sys.settrace``, and driven directly by
      ``test_load_migrations_refuses_a_non_symlink_entry_racing_its_own_follow_stat``,
      ``tests/unit/test_migration_loader_errors.py``). A `chmod 0o444`
      `migrations_dir` does *not* reach this branch: its own ``is_symlink()``
      lstat fails first, at the unguarded call below.

    ``entry.is_symlink()`` is called unguarded: any ``OSError`` it can raise
    (``EACCES`` from the same denies-traversal ``migrations_dir``, the
    ``chmod 0o444`` case) propagates unchanged to that same enumeration
    ``except``, since there is nothing more specific to say about it than the
    bare stat failure already says.
    """
    is_symlink = entry.is_symlink()
    try:
        entry_stat = entry.stat()
    except OSError as exc:
        if is_symlink:
            relative = str(entry.relative_to(project_root))
            if exc.errno == errno.ENOENT:
                raise MigrationFileUnreadableError(
                    relative,
                    "symbolic link target is missing",
                    missing_or_wrong_text=(
                        f"{relative!r} is a symbolic link whose target is missing. "
                        f"Restore the target or remove the link, then retry."
                    ),
                ) from exc
            raise MigrationFileUnreadableError(
                relative, exc.strerror or str(exc), exc.errno
            ) from exc
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise
    return stat.S_ISREG(entry_stat.st_mode)


def _load_one(
    path: Path,
    project_root: Path,
    migrations_dir: Path,
    validator: Draft202012Validator,
    content_by_hash: dict[str, str],
) -> Migration:
    try:
        raw = read_source_file(project_root, PurePosixPath(path.relative_to(project_root)))
    except OSError as exc:
        # The sibling of `_parse_upsert`'s conversion below, for the *other*
        # raw read on this load path: the migration file itself, not a
        # `contentFile` it names. The measurement behind this conversion is on
        # `MigrationFileUnreadableError`'s own docstring, not repeated here.
        raise MigrationFileUnreadableError(
            str(path.relative_to(project_root)), exc.strerror or str(exc), exc.errno
        ) from exc
    checksum = ContentHash.of_bytes(raw)

    try:
        document = load_yaml_mapping(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise MigrationError(f"{path.name} is not valid UTF-8") from exc
    except ValueError as exc:
        raise MigrationError(f"{path.name}: {exc}") from exc
    except yaml.YAMLError as exc:
        # `load_yaml_mapping`'s own docstring names this as the type a parse
        # failure raises -- a syntax error via the scanner, or an embedded NUL
        # byte via the reader (`yaml.reader.ReaderError`, also a `YAMLError`
        # subclass). Neither is a `UnicodeDecodeError` nor a `ValueError`, so
        # both escaped the two clauses above uncaught until now, propagating
        # as a raw Rich traceback through `resolve_context` (issue #217).
        raise MigrationError(f"{path.name}: {exc}") from exc

    try:
        validator.validate(document)
    except ValidationError as exc:
        location = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise MigrationError(f"{path.name} is invalid at {location}: {exc.message}") from exc

    if document["apiVersion"] != MIGRATION_API_VERSION:
        raise MigrationError(
            f"{path.name} declares apiVersion {document['apiVersion']!r}; "
            f"this build understands {MIGRATION_API_VERSION!r}"
        )

    operations = tuple(
        _parse_operation(op, path, project_root, migrations_dir, content_by_hash)
        for op in document["operations"]
    )

    return Migration(
        migration_id=MigrationId(document["id"]),
        created_at=_parse_datetime(document["createdAt"], path),
        author=document["author"],
        operations=operations,
        checksum=checksum,
        depends_on=tuple(MigrationId(d) for d in document.get("dependsOn", [])),
        description=document.get("description"),
        source_path=str(path.relative_to(project_root)),
    )


def _parse_datetime(value: str, path: Path) -> datetime:
    """Parse an RFC 3339 timestamp, requiring an explicit offset.

    A naive timestamp compares wrong across a DST boundary, and knowledge
    validity windows depend on those comparisons.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MigrationError(f"{path.name}: {value!r} is not an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise MigrationError(
            f"{path.name}: {value!r} has no UTC offset. Timestamps must be unambiguous."
        )
    return parsed


def _parse_operation(  # noqa: PLR0911, PLR0912 -- a flat dispatch over 14 operations
    payload: dict[str, Any],
    path: Path,
    project_root: Path,
    migrations_dir: Path,
    content_by_hash: dict[str, str],
) -> Operation:
    op = payload["op"]

    match op:
        case "createItem":
            return CreateItem(
                item_id=ItemId(payload["itemId"]),
                kind_=KnowledgeKind(payload["kind"]),
                namespace=payload["namespace"],
                owner=payload["owner"],
                sensitivity=Sensitivity(payload.get("sensitivity", "internal")),
                trust_level=TrustLevel(payload.get("trustLevel", "unverified")),
            )
        case "upsertRevision":
            return _parse_upsert(payload, path, project_root, migrations_dir, content_by_hash)
        case "deprecateItem":
            superseded = payload.get("supersededBy")
            return DeprecateItem(
                item_id=ItemId(payload["itemId"]),
                reason=payload.get("reason"),
                superseded_by=None if superseded is None else ItemId(superseded),
            )
        case "restoreItem":
            return RestoreItem(item_id=ItemId(payload["itemId"]), reason=payload.get("reason"))
        case "addRelation":
            return AddRelation(
                source_item_id=ItemId(payload["sourceItemId"]),
                relation_type=RelationType(payload["relationType"]),
                target_item_id=ItemId(payload["targetItemId"]),
                note=payload.get("note"),
            )
        case "removeRelation":
            return RemoveRelation(
                source_item_id=ItemId(payload["sourceItemId"]),
                relation_type=RelationType(payload["relationType"]),
                target_item_id=ItemId(payload["targetItemId"]),
            )
        case "addAlias":
            return AddAlias(alias=ItemId(payload["alias"]), item_id=ItemId(payload["itemId"]))
        case "removeAlias":
            return RemoveAlias(alias=ItemId(payload["alias"]))
        case "changeSensitivity":
            return ChangeSensitivity(
                item_id=ItemId(payload["itemId"]),
                sensitivity=Sensitivity(payload["sensitivity"]),
                reason=payload["reason"],
            )
        case "changeOwner":
            return ChangeOwner(item_id=ItemId(payload["itemId"]), owner=payload["owner"])
        case "registerSpecification":
            return RegisterSpecification(
                spec_id=SpecId(payload["specId"]),
                item_id=ItemId(payload["itemId"]),
                source_uri=payload["sourceUri"],
                content_format=MediaType(payload["format"]),
                status=SpecificationStatus(payload.get("status", "active")),
            )
        case "supersedeSpecification":
            return SupersedeSpecification(
                spec_id=SpecId(payload["specId"]),
                superseded_by=SpecId(payload["supersededBy"]),
            )
        case "addEvidence":
            return AddEvidence(
                item_id=ItemId(payload["itemId"]),
                anchor=_parse_anchor(payload["anchor"]),
                description=payload["description"],
                confidence=float(payload.get("confidence", 1.0)),
            )
        case "removeEvidence":
            return RemoveEvidence(
                item_id=ItemId(payload["itemId"]), source_uri=payload["sourceUri"]
            )
        case _:  # pragma: no cover - the schema rejects unknown ops first
            raise MigrationError(f"{path.name}: unknown operation {op!r}")


def _parse_upsert(
    payload: dict[str, Any],
    path: Path,
    project_root: Path,
    migrations_dir: Path,
    content_by_hash: dict[str, str],
) -> UpsertRevision:
    content_file = payload["contentFile"]

    # `contentFile` is relative to the migration file, and it is attacker-
    # influenceable. Resolution happens against the project root with symlinks
    # followed first, so `../../../.ssh/id_ed25519` and a symlink that leaves
    # the tree are both refused (SEC-7, T-4, T-5).
    try:
        relative_to_root = (migrations_dir / content_file).resolve()
    except (ValueError, OSError) as exc:
        # An embedded NUL byte makes `Path.resolve()` -- `os.path.realpath`,
        # then an `lstat` the OS refuses to even attempt -- raise `ValueError`,
        # not `OSError`, before any of the checks below run. The JSON Schema's
        # `contentFile` definition checks type, length and a `..`/absolute-path
        # prefix; none of those exclude a NUL byte, so this reached the
        # resolve call unfiltered (issue #205's Class 1a, reproduced against
        # the real CLI as `ValueError: lstat: embedded null character in
        # path`). `OSError` is caught too on the same reasoning as the read
        # below: neither is a `TheurianError`, and this call sits ahead of
        # every guard in this file.
        raise MigrationContentUnreadableError(
            str(path.relative_to(project_root)),
            content_file,
            str(exc),
            getattr(exc, "errno", None),
        ) from exc
    try:
        relative = relative_to_root.relative_to(project_root.resolve())
    except ValueError as exc:
        raise PathEscapeError(content_file, str(project_root)) from exc

    relative_posix = PurePosixPath(relative)
    resolve_within_root(project_root, relative_posix)
    try:
        body_bytes = read_source_file(project_root, relative_posix)
    except OSError as exc:
        # `read_source_file`'s own docstring names `FileNotFoundError` as one of
        # the things it raises, and a bare `OSError` is none of the types every
        # `resolve_context` caller already guards (issue #205). Converting here,
        # at the one call site the read happens, is what makes every one of
        # those callers -- `migrate validate`, `init`, and the rest of
        # `_require_project`'s seven call sites -- report the CP-2 `{error,
        # remedy}` shape instead of a Rich traceback with an empty stdout.
        raise MigrationContentUnreadableError(
            str(path.relative_to(project_root)), content_file, exc.strerror or str(exc), exc.errno
        ) from exc

    try:
        body = body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationError(f"{path.name}: {content_file} is not valid UTF-8") from exc

    actual = ContentHash.of_bytes(body_bytes)
    declared = payload.get("contentSha256")
    if declared is not None and declared != actual.value:
        raise MigrationError(
            f"{path.name}: {content_file} hashes to {actual.short} but the migration "
            f"pins {declared[:12]}. The body file changed after the migration was written."
        )
    content_by_hash[actual.value] = body

    metadata = payload["metadata"]
    expected = payload.get("expectedRevision")

    return UpsertRevision(
        item_id=ItemId(payload["itemId"]),
        revision_id=RevisionId(payload["revisionId"]),
        content_file_path=content_file,
        expected_revision=None if expected is None else RevisionId(expected),
        content_sha256=actual,
        metadata=RevisionMetadataSpec(
            title=metadata["title"],
            content_type=MediaType(metadata["contentType"]),
            kind=KnowledgeKind(metadata["kind"]),
            namespace=metadata["namespace"],
            status=KnowledgeStatus(metadata["status"]),
            owner=metadata["owner"],
            trust_level=TrustLevel(metadata.get("trustLevel", "unverified")),
            sensitivity=Sensitivity(metadata.get("sensitivity", "internal")),
            tenant_id=metadata.get("tenantId", "local"),
            acl_group=metadata.get("aclGroup", "default"),
            valid_from=_optional_datetime(metadata.get("validFrom"), path),
            valid_to=_optional_datetime(metadata.get("validTo"), path),
            labels=tuple(metadata.get("labels", [])),
            scope_paths=tuple(metadata.get("scope", {}).get("paths", [])),
            source_anchors=tuple(_parse_anchor(a) for a in metadata.get("sourceAnchors", [])),
        ),
    )


def _optional_datetime(value: str | None, path: Path) -> datetime | None:
    return None if value is None else _parse_datetime(value, path)


def _parse_anchor(payload: dict[str, Any]) -> SourceAnchor:
    return SourceAnchor(
        provider=payload["provider"],
        source_uri=payload["sourceUri"],
        repository=payload.get("repository"),
        commit_sha=payload.get("commitSha"),
        blob_sha=payload.get("blobSha"),
        file_path=payload.get("filePath"),
        line_start=payload.get("lineStart"),
        line_end=payload.get("lineEnd"),
        external_id=payload.get("externalId"),
    )
