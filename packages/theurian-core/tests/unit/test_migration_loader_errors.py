"""``load_migrations``'s own raw-filesystem error translations (issues #205,
#214, #217).

Behavioural, not structural: every test here actually calls
``infrastructure/filesystem/migration_loader.py::load_migrations`` and
asserts the specific ``TheurianError`` subclass it raises for a specific raw
``OSError``/``ValueError``/``yaml.YAMLError``.

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
import yaml

from theurian.cli.context import schema_root as real_schema_root
from theurian.domain.errors import (
    MigrationContentUnreadableError,
    MigrationError,
    MigrationFileUnreadableError,
    MigrationsDirectoryUnreadableError,
    SchemaUnreadableError,
)
from theurian.domain.migration import LoadedMigrations
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.security.yaml_loading import load_yaml_mapping

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


def test_load_migrations_names_the_directory_remedy_for_a_content_file_that_is_a_directory(
    project: Path,
) -> None:
    """The last unproven arm of ``_read_failure_remedy`` (``domain/errors.py``).

    Round-two review measured the ``EISDIR`` arm surviving reversion to the
    generic ``missing_or_wrong_text``: nothing drove a ``contentFile`` that
    resolves to an existing *directory* rather than a missing or
    permission-denied file, so a regression collapsing that branch back to
    "resolves relative to the migration file, not a proposal directory" --
    the very "make the reader check two irrelevant things" defect that
    docstring says selecting by ``errno`` fixes -- would have passed silently.
    ``ENOENT`` (missing file, above) and ``EACCES``
    (`test_load_migrations_raises_migration_file_unreadable_error_for_an_
    unreadable_migration`, the sibling error's own arm) were already proven;
    this is the third and last.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "adirectory").mkdir()
    (migrations_dir / "01K1NNNNNN01234567890ABCDE-eisdir.yaml").write_text(
        _UPSERT_MIGRATION.format(content_file="adirectory")
    )

    with pytest.raises(MigrationContentUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert excinfo.value.remedy == "'adirectory' names a directory, not a file."


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


# -- MigrationError: a YAML syntax error propagating uncaught (issue #217) ----


def test_load_migrations_raises_migration_error_for_a_malformed_migration_yaml(
    project: Path,
) -> None:
    """``yaml.YAMLError`` is neither ``UnicodeDecodeError`` nor ``ValueError``
    -- the two types ``_load_one``'s ``except`` clause around
    ``load_yaml_mapping`` catches (``migration_loader.py``) -- so a migration
    file with a YAML syntax error propagates uncaught through
    ``resolve_context``: a raw Rich traceback under ``--json``, escaping
    ``migrate validate``, ``migrate status``, and ``migrate apply`` alike.
    Reproduced against the real CLI with ``id: [unclosed\\n  bad: {{{``.

    The expected wrapped detail is derived from calling ``load_yaml_mapping``
    on the identical text, rather than hardcoded, so this does not pin
    PyYAML's own wording -- only that the fix's translation keeps naming
    *which file* and *what went wrong parsing it*, the same contract
    ``_load_one``'s adjacent ``ValueError`` branch (``f"{path.name}: {exc}"``)
    already keeps for a document that parses but is not a mapping.
    """
    migrations_dir = project / ".theurian" / "migrations"
    malformed = "id: [unclosed\n  bad: {{{\n"
    migration = migrations_dir / "01K1GGGGGG01234567890ABCDE-malformed.yaml"
    migration.write_text(malformed)

    with pytest.raises(yaml.YAMLError) as yaml_excinfo:
        load_yaml_mapping(malformed)
    expected_detail = str(yaml_excinfo.value)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert migration.name in str(excinfo.value)
    assert expected_detail in str(excinfo.value)


def test_load_migrations_raises_migration_error_for_a_nul_byte_in_the_yaml_source(
    project: Path,
) -> None:
    """The sibling crash: a raw NUL byte written directly into the YAML
    *source* -- not the escaped ``\\0`` inside a ``contentFile`` string that
    :class:`MigrationContentUnreadableError`'s own NUL-byte test above covers,
    an unrelated call site one step later -- trips PyYAML's reader, not its
    scanner: a ``yaml.reader.ReaderError``, also a ``yaml.YAMLError`` and also
    uncaught by the same two-type ``except`` clause (issue #217). The byte
    decodes as valid UTF-8 on its own, so this never reaches
    ``UnicodeDecodeError`` in ``_load_one`` -- reaching ``load_yaml_mapping``
    at all in the derivation below is itself the proof, since a
    ``UnicodeDecodeError`` would raise one line earlier, at ``.decode("utf-8")``.
    """
    migrations_dir = project / ".theurian" / "migrations"
    raw = b"apiVersion: theurian.dev/v1\nid: \x00broken\n"
    migration = migrations_dir / "01K1HHHHHH01234567890ABCDE-nul.yaml"
    migration.write_bytes(raw)

    text = raw.decode("utf-8")
    with pytest.raises(yaml.YAMLError) as yaml_excinfo:
        load_yaml_mapping(text)
    expected_detail = str(yaml_excinfo.value)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert migration.name in str(excinfo.value)
    assert expected_detail in str(excinfo.value)


# -- MigrationsDirectoryUnreadableError ---------------------------------------


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_load_migrations_raises_migrations_directory_unreadable_error_for_an_unreadable_parent(
    project: Path,
) -> None:
    """``chmod 000`` on ``.theurian`` (``migrations_dir``'s parent), not on
    ``migrations_dir`` itself -- ``is_dir()`` needs only traversal permission
    on the parent to raise ``EACCES``; a directory `chmod 000`'d itself
    leaves `is_dir()` succeeding, which is `glob`'s own, different,
    previously-unfixed silent-empty-result case, closed by the tests below
    (issue #214).
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


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
@pytest.mark.parametrize("mode", [0o000, 0o111], ids=["000", "111"])
def test_load_migrations_raises_migrations_directory_unreadable_error_for_the_directory_itself(
    project: Path, mode: int
) -> None:
    """Issue #214: ``chmod``'d on ``migrations_dir`` *itself*, not its parent
    (the sibling case above). ``is_dir()`` still succeeds under either mode --
    stat needs only traversal permission on ancestors, not on the target
    itself -- so the existing ``OSError`` conversion around ``is_dir()``
    never runs. The failure instead happens inside ``pathlib.Path.glob``'s
    own ``scandir``, which catches the ``PermissionError`` it raises
    internally and yields nothing: today this returns an *empty*
    ``LoadedMigrations`` rather than raising, so ``migrate validate --json``
    reports ``valid: true`` with ``migrationCount: 0`` for a project whose
    migrations were never read, and ``migrate apply --json`` goes on to
    create a state database for that empty set. Measured directly against
    ``load_migrations`` for both modes; 0o111 fails silently the identical way.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    migrations_dir.chmod(mode)
    try:
        with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
            load_migrations(project, migrations_dir, real_schema_root())
    finally:
        migrations_dir.chmod(0o700)

    relative = str(migrations_dir.relative_to(project))
    assert str(excinfo.value).startswith(f"{relative!r} could not be listed:")
    assert excinfo.value.remedy == (
        f"Confirm this user has read and execute permission on {relative!r} "
        f"and its parent directories, then retry."
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_load_migrations_raises_migrations_directory_unreadable_error_when_entries_are_unstattable(
    project: Path,
) -> None:
    """A third member of the same class, found while measuring #214 -- named
    by neither issue. ``chmod 444`` leaves the directory *readable* (so
    ``scandir`` can list its entries) but not *traversable*, so
    ``Path.is_file()`` in the same comprehension that filters ``glob``'s
    results (``paths = sorted(p for p in migrations_dir.glob("*.yaml") if
    p.is_file())``, ``migration_loader.py``) needs to stat each entry and
    raises ``EACCES`` -- and unlike the silent ``scandir`` failure above,
    pathlib's glob selector does not catch that one: it escapes as a raw
    ``PermissionError``, a raw Rich traceback under ``--json``, exit 1.
    Measured directly against ``load_migrations``: mode 444 (and 400) raise;
    mode 500 (execute restored) does not.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    migrations_dir.chmod(0o444)
    try:
        with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
            load_migrations(project, migrations_dir, real_schema_root())
    finally:
        migrations_dir.chmod(0o700)

    relative = str(migrations_dir.relative_to(project))
    assert str(excinfo.value).startswith(f"{relative!r} could not be listed:")
    assert excinfo.value.remedy == (
        f"Confirm this user has read and execute permission on {relative!r} "
        f"and its parent directories, then retry."
    )


# -- issue #214 must not overreach: the legitimate "zero migrations" cases ----


def test_load_migrations_on_an_ordinarily_readable_empty_directory_returns_an_empty_set(
    project: Path,
) -> None:
    """Pins the member of the "zero migrations" family the #214 fix must
    leave alone: an existing, ordinarily-readable, genuinely empty
    ``migrations_dir`` is not a refusal, it is ``LoadedMigrations.empty()`` --
    the same value ``load_migrations`` already returns when ``migrations_dir``
    does not exist at all (``if not is_directory: return
    LoadedMigrations.empty()``, ``migration_loader.py``). A fix that makes
    *any* zero-migration read raise would pass every test above it in this
    file and still be wrong.
    """
    migrations_dir = project / ".theurian" / "migrations"

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    assert loaded == LoadedMigrations.empty()


def test_load_migrations_matches_pathlib_globs_own_dotfile_behaviour_today(
    project: Path,
) -> None:
    """Enumeration parity pin -- deliberately the one green test in this batch.

    Measured directly against CPython 3.13's stdlib, not assumed:
    ``pathlib.Path.glob("*.yaml")`` -- unlike ``glob.glob()``, which hides a
    leading dot by default -- does **not** treat ``.hidden.yaml`` as hidden,
    so it is loaded today exactly like any other ``*.yaml`` sibling
    (``sorted(Path(tmp).glob("*.yaml"))`` on CPython 3.13.11 returns both).
    Whatever enumeration call the #214 fix substitutes for ``Path.glob``
    (needed because ``glob`` itself swallows the very ``PermissionError``
    that fix exists to surface -- see the tests above) must keep matching
    this file too, or the fix silently narrows which migrations a project
    has, in either direction, with nothing here to catch it.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    dotfile_migration_id = "01K1DDDDDD01234567890ABCDE"
    dotfile_migration = _VALID_MIGRATION.replace(
        "01K1KKKKKK01234567890ABCDE", dotfile_migration_id
    ).replace("architecture.auth-policy", "architecture.dotfile-policy")
    (migrations_dir / ".hidden.yaml").write_text(dotfile_migration)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    ids = {str(m.migration_id) for m in loaded.migration_set}
    assert ids == {"01K1KKKKKK01234567890ABCDE", dotfile_migration_id}


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
