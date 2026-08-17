"""``load_migrations``'s own raw-filesystem error translations (issue #205).

Behavioural, not structural: every test here actually calls
``infrastructure/filesystem/migration_loader.py::load_migrations`` and
asserts the specific ``TheurianError`` subclass it raises for a specific raw
``OSError``/``ValueError``.

This replaces a prior test,
``test_the_loaders_read_errors_are_theurian_errors``
(``tests/unit/test_resolve_context_call_sites.py``), that asserted
``issubclass(ErrorType, TheurianError)`` without ever importing or calling
the loader. That was true, and it was not a proof that the loader raises
that type for the failure it claims to guard: deleting the `_parse_upsert`
translation this file's tests exercise left every one of that prior test's
eight parametrizations green, because none of them ran the code path they
were supposedly pinning. Confirmed the other way here too -- each `with
pytest.raises(...)` in this file was checked to go red when its matching
translation is deleted, not merely written to look like it would.
"""

from __future__ import annotations

import errno
import os
import shutil
import sys
from pathlib import Path

import pytest

from theurian.cli.context import schema_root as real_schema_root
from theurian.domain.errors import (
    MigrationContentUnreadableError,
    MigrationFileUnreadableError,
    MigrationsDirectoryUnreadableError,
    SchemaUnreadableError,
)
from theurian.infrastructure.filesystem.migration_loader import load_migrations

pytestmark = pytest.mark.unit

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_cli_commands.py` uses before a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

_VALID_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1KKKKKK01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""

#: ``{content_file}`` is substituted per test, so both an ENOENT (a plain
#: missing filename) and a NUL byte (a YAML double-quoted scalar's ``\0``
#: escape -- see the NUL test below) can drive the same template.
_UPSERT_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1MMMMMM01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1MMMREV01234567890ABCDE
    contentFile: {content_file}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".theurian" / "migrations").mkdir(parents=True)
    return root


def _copy_real_schema(tmp_path: Path) -> Path:
    """A private copy of the real schema tree, so a test can ``chmod`` it safely.

    Copying rather than pointing at the installed package's own schema
    directory: that directory is shared by every test in the run, and
    `chmod`-ing a file inside it races every other test using
    `schema_root()` concurrently.
    """
    schema_dir = tmp_path / "schemas"
    (schema_dir / "migrations").mkdir(parents=True)
    real = real_schema_root() / "migrations" / "migration.schema.json"
    shutil.copy(real, schema_dir / "migrations" / "migration.schema.json")
    return schema_dir


# -- MigrationContentUnreadableError -----------------------------------------


def test_load_migrations_raises_migration_content_unreadable_error_for_a_missing_content_file(
    project: Path,
) -> None:
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1MMMMMM01234567890ABCDE-repro.yaml").write_text(
        _UPSERT_MIGRATION.format(content_file="content.md")
    )

    with pytest.raises(MigrationContentUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert "content.md" in str(excinfo.value)
    assert excinfo.value.remedy


def test_load_migrations_raises_migration_content_unreadable_error_for_a_nul_byte_in_content_file(
    project: Path,
) -> None:
    """Issue #205's Class 1a: a NUL byte reaches ``Path.resolve()`` unfiltered.

    The JSON Schema's ``contentFile`` definition checks ``type``, length, and
    a ``..``/absolute-path prefix -- none of which excludes an embedded NUL,
    so a schema-valid document reaches the resolution call this test drives.
    Written as a YAML double-quoted scalar's ``\\0`` escape, not a literal
    byte in the file: a literal NUL in the YAML *source* is refused by the
    YAML reader itself, a different and unrelated failure this test is not
    about.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1MMMMMM01234567890ABCDE-nul.yaml").write_text(
        _UPSERT_MIGRATION.format(content_file='"content.md\\0evil"')
    )

    with pytest.raises(MigrationContentUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert "embedded null character" in str(excinfo.value)


# -- MigrationFileUnreadableError ---------------------------------------------


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_load_migrations_raises_migration_file_unreadable_error_for_an_unreadable_migration(
    project: Path,
) -> None:
    migrations_dir = project / ".theurian" / "migrations"
    migration = migrations_dir / "01K1KKKKKK01234567890ABCDE-unreadable.yaml"
    migration.write_text(_VALID_MIGRATION)
    migration.chmod(0o000)
    try:
        with pytest.raises(MigrationFileUnreadableError) as excinfo:
            load_migrations(project, migrations_dir, real_schema_root())
    finally:
        migration.chmod(0o644)

    assert "unreadable.yaml" in str(excinfo.value)
    assert excinfo.value.remedy


# -- MigrationsDirectoryUnreadableError ---------------------------------------


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_load_migrations_raises_migrations_directory_unreadable_error_for_an_unreadable_parent(
    project: Path,
) -> None:
    """``chmod 000`` on ``.theurian`` (``migrations_dir``'s parent), not on
    ``migrations_dir`` itself -- ``is_dir()`` needs only traversal permission
    on the parent to raise ``EACCES``; a directory `chmod 000`'d itself
    leaves `is_dir()` succeeding, which is `glob`'s own, different, and
    deliberately unfixed silent-empty-result case (documented at
    `load_migrations`'s own call site, not this one).
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    theurian_dir = project / ".theurian"
    theurian_dir.chmod(0o000)
    try:
        with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
            load_migrations(project, migrations_dir, real_schema_root())
    finally:
        theurian_dir.chmod(0o700)

    assert "migrations" in str(excinfo.value)
    assert excinfo.value.remedy


# -- SchemaUnreadableError -----------------------------------------------------


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_load_migrations_raises_schema_unreadable_error_for_an_unreadable_schema_file(
    project: Path, tmp_path: Path
) -> None:
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    schema_dir = _copy_real_schema(tmp_path)
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.chmod(0o000)
    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            load_migrations(project, migrations_dir, schema_dir)
    finally:
        schema_file.chmod(0o644)

    assert excinfo.value.remedy
    assert os.strerror(errno.EACCES) in str(excinfo.value)
