"""Load migration files from disk into the domain model (ADR-0005).

Loading is where untrusted input enters the system. A migration file is written
by whoever can commit to the repository, and it names arbitrary paths. Every
check that keeps that safe lives here.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

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

#: Ceiling on migration files per project. Not a design limit -- it is a guard
#: against a pathological or generated directory turning a status check into a
#: multi-minute filesystem walk.
MAX_MIGRATIONS: Final = 10_000

_SCHEMA_RELATIVE: Final = "migrations/migration.schema.json"


@lru_cache(maxsize=1)
def _validator(schema_root: Path) -> Draft202012Validator:
    """Build the migration-schema validator, translating a corrupted install.

    Reads the *installed package's* schema, never a path under a user's
    project -- `schema_root()` (`cli/context.py`) already raises
    `ProjectError` when neither candidate location exists at all, checked with
    `.exists()` before either is returned here. What that leaves is "a
    location was found, but reading it failed": a permission problem on
    site-packages, or a symlink loop. Originally left as a bare `OSError` on
    the reasoning that install-integrity is not user-project state -- true,
    but beside the CP-2 point: an unguarded read still crashes every `--json`
    command that reaches `resolve_context` (issue #205's Class 1), so it is
    translated here too, to `SchemaUnreadableError` rather than a
    `MigrationError`, keeping that distinction in the *type* instead of in
    whether the failure is caught at all.
    """
    schema_path = schema_root / _SCHEMA_RELATIVE
    try:
        text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaUnreadableError(str(schema_path), exc.strerror or str(exc)) from exc
    schema = json.loads(text)
    return Draft202012Validator(schema)


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
        PathEscapeError: If a ``contentFile`` points outside ``project_root``.
        InputTooLargeError: If a file exceeds its size limit.
    """
    try:
        # CPython's `Path.is_dir()` swallows `ENOENT`/`ENOTDIR`/`ELOOP` -- the
        # well-formed "not a directory" case the `if not is_directory` below
        # already answers by returning an empty migration set -- but
        # re-raises `EACCES` when a *parent* of `migrations_dir` denies
        # traversal. The measurement behind converting that is on
        # `MigrationsDirectoryUnreadableError`'s own docstring, not repeated
        # here.
        is_directory = migrations_dir.is_dir()
    except OSError as exc:
        raise MigrationsDirectoryUnreadableError(
            str(migrations_dir.relative_to(project_root)), exc.strerror or str(exc)
        ) from exc
    if not is_directory:
        return LoadedMigrations.empty()

    # `glob` is a *different* raw-IO call than `is_dir` above, and does not
    # belong to the class this file sweeps: `chmod 000` on `migrations_dir`
    # *itself* (rather than its parent, above) leaves `is_dir()` on it
    # succeeding -- stat needs no permission on the target, only on its
    # ancestors -- while `pathlib.Path.glob` catches the `PermissionError` its
    # own `scandir` hits internally and yields nothing, so this returns an
    # *empty* migration set rather than crashing. `migrate validate --json`
    # then reports `valid: true` with `migrationCount: 0` for a project whose
    # migrations were never read. That is a different, quieter defect than the
    # Rich-traceback crash this file's `MigrationFileUnreadableError`/
    # `MigrationContentUnreadableError`/`MigrationsDirectoryUnreadableError`
    # sites close, and sweeping it into a bare-`OSError` conversion here would
    # do nothing, because there is no `OSError` to catch -- filed separately.
    #
    # Sorted so a failure reports the first file in a stable order rather than
    # whichever the filesystem happened to yield first.
    paths = sorted(p for p in migrations_dir.glob("*.yaml") if p.is_file())
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
