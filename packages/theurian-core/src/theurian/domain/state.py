"""State hash computation (ADR-0007, ADR-0016, ADR-0017).

The state hash content-addresses an entire canonical state, so that a database
file's name describes its contents. Every guarantee that rests on it -- O(1)
branch switching, reproducible pinned snapshots, cache correctness -- depends on
one property: **identical inputs must produce an identical hash on any machine,
in any process, in any order of discovery.**

Everything in this module exists to protect that property. The determinism rules
below have each been a real bug in comparable systems.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final, override

from theurian.domain.errors import DomainError
from theurian.domain.identifiers import MigrationId
from theurian.domain.migration import MIGRATION_ENGINE_VERSION, MigrationSet
from theurian.domain.values import ContentHash

#: Separator between hashed fields. The unit separator cannot occur in a ULID,
#: a hex digest, or a decimal version, so no two distinct input sequences can
#: serialise to the same byte string.
_SEP: Final = b"\x1f"

#: Separator between records.
_RECORD_SEP: Final = b"\x1e"

#: Domain separation. Prevents a state hash from ever colliding with a content
#: hash computed over the same bytes by some other part of the system.
_PREFIX: Final = b"theurian-state-v1"


@dataclass(frozen=True, slots=True)
class StateInputs:
    """Everything that determines a canonical state.

    Note what is *absent*: no absolute path, no mtime, no inode, no hostname, no
    environment, no file ordering from the filesystem. Any of those would make
    the hash machine-specific, which would silently disable every cache and make
    a pinned snapshot unreproducible on another checkout.
    """

    #: ``(migration_id, migration_file_checksum)``, in any order. Sorted here.
    migrations: tuple[tuple[MigrationId, ContentHash], ...]
    #: Checksums of the body files migrations reference, in any order. Sorted
    #: here. Identified by content alone: a body file's *path* is not hashed, so
    #: renaming a file without changing it does not change the state.
    content_checksums: tuple[ContentHash, ...]
    schema_version: int
    engine_version: int = MIGRATION_ENGINE_VERSION

    def __post_init__(self) -> None:
        if self.schema_version < 1:
            raise DomainError(f"schema_version must be positive, got {self.schema_version}")
        if self.engine_version < 1:
            raise DomainError(f"engine_version must be positive, got {self.engine_version}")

        seen = [mid for mid, _ in self.migrations]
        duplicates = {m for m in seen if seen.count(m) > 1}
        if duplicates:
            listed = ", ".join(sorted(str(d) for d in duplicates))
            raise DomainError(f"Duplicate migration ids in state inputs: {listed}")


@dataclass(frozen=True, slots=True)
class StateHash:
    """A content address for one canonical state."""

    value: ContentHash

    @property
    def short(self) -> str:
        """The form used in state database filenames."""
        return self.value.short

    @property
    def database_filename(self) -> str:
        return f"theurian-state-{self.short}.sqlite"

    @override
    def __str__(self) -> str:
        return self.value.value


def compute_state_hash(inputs: StateInputs) -> StateHash:
    """Hash a canonical state's inputs.

    Determinism rules, each protecting against a specific failure:

    1. **Byte-wise sort, never locale collation.** ``str.sort`` in Python is
       already byte-wise for ASCII, but sorting is done on the encoded bytes to
       remove any doubt. A locale-sensitive sort would produce different orders
       under different ``LC_COLLATE`` values.
    2. **Explicit separators.** Without them, ``("ab", "c")`` and ``("a", "bc")``
       hash identically.
    3. **Length-prefix-free but separator-delimited**, using bytes that cannot
       occur in any field, so the encoding is unambiguous.
    4. **No iteration over unsorted structures.** Every sequence is sorted before
       it is fed in, so filesystem enumeration order cannot leak in.
    5. **Versions participate**, so a schema or engine change invalidates cached
       state rather than silently reinterpreting it (ADR-0017).
    """
    digest = hashlib.sha256()
    digest.update(_PREFIX)
    digest.update(_RECORD_SEP)

    for migration_id, checksum in sorted(
        inputs.migrations, key=lambda pair: pair[0].value.encode("ascii")
    ):
        digest.update(migration_id.value.encode("ascii"))
        digest.update(_SEP)
        digest.update(checksum.value.encode("ascii"))
        digest.update(_RECORD_SEP)

    digest.update(b"content")
    digest.update(_RECORD_SEP)
    for checksum in sorted(inputs.content_checksums, key=lambda c: c.value.encode("ascii")):
        digest.update(checksum.value.encode("ascii"))
        digest.update(_RECORD_SEP)

    digest.update(b"schema")
    digest.update(_SEP)
    digest.update(str(inputs.schema_version).encode("ascii"))
    digest.update(_RECORD_SEP)

    digest.update(b"engine")
    digest.update(_SEP)
    digest.update(str(inputs.engine_version).encode("ascii"))
    digest.update(_RECORD_SEP)

    return StateHash(ContentHash(digest.hexdigest()))


def state_inputs_from(
    migration_set: MigrationSet,
    content_checksums: tuple[ContentHash, ...],
    schema_version: int,
) -> StateInputs:
    """Build :class:`StateInputs` from a loaded migration set."""
    return StateInputs(
        migrations=tuple((m.migration_id, m.checksum) for m in migration_set),
        content_checksums=content_checksums,
        schema_version=schema_version,
    )


@dataclass(frozen=True, slots=True)
class ActiveState:
    """The state a project is currently serving.

    Persisted as ``.theurian/state/active.json`` and replaced by an atomic
    ``os.replace``, so a reader never observes a partially written pointer.
    """

    state_hash: StateHash
    database_filename: str
    migration_count: int
    updated_at: str

    def to_json(self) -> dict[str, object]:
        return {
            "stateHash": str(self.state_hash),
            "databaseFilename": self.database_filename,
            "migrationCount": self.migration_count,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> ActiveState:
        """Parse a pointer file's contents, refusing one that cannot be true.

        ``migration_count`` is range-checked here because it is *published*: the
        MCP `knowledge.status` tool reports it as ``appliedMigrations``, whose
        schema declares ``minimum: 0``. Parsing it as any integer meant a
        hand-edited ``migrationCount: -5`` reached the wire verbatim, and a
        strict client rejects that whole response -- including the ``integrity``
        signal it carries, which is the one field saying the state is damaged.
        Refusing here turns a false answer into an actionable one.

        Raises:
            DomainError: On a missing, unparseable or negative field. Every
                caller reaches this through
                :func:`~theurian.application.project_service.read_active_state`,
                which converts it to a ``ProjectError`` carrying
                ``ACTIVE_POINTER_REMEDY`` (delete the derived pointer and
                re-apply); the MCP tools' ``_resolve`` then re-raises that as a
                ``ToolError`` whose message names the remedy.
        """
        try:
            state_hash = StateHash(ContentHash(str(payload["stateHash"])))
            migration_count = int(str(payload["migrationCount"]))
            if migration_count < 0:
                msg = f"migrationCount is negative ({migration_count})"
                raise ValueError(msg)
            return cls(
                state_hash=state_hash,
                database_filename=str(payload["databaseFilename"]),
                migration_count=migration_count,
                updated_at=str(payload["updatedAt"]),
            )
        except (KeyError, ValueError) as exc:
            raise DomainError(f"Malformed active state pointer: {exc}") from exc
