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
from collections.abc import Iterator
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
from theurian.infrastructure.filesystem.migration_loader import _validator, load_migrations
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


# -- MigrationError: a YAML syntax error used to propagate uncaught (issue #217) --


def test_load_migrations_raises_migration_error_for_a_malformed_migration_yaml(
    project: Path,
) -> None:
    """Before issue #217's fix, ``yaml.YAMLError`` was neither ``UnicodeDecodeError``
    nor ``ValueError`` -- the two types ``_load_one``'s ``except`` clause around
    ``load_yaml_mapping`` caught at the time (``migration_loader.py``; it now
    catches three, ``yaml.YAMLError`` added alongside them) -- so a migration
    file with a YAML syntax error propagated uncaught through
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
    scanner: a ``yaml.reader.ReaderError``, also a ``yaml.YAMLError`` and, before
    issue #217's fix, also uncaught by the then-two-type ``except`` clause. The
    byte decodes as valid UTF-8 on its own, so this never reaches
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


# -- MigrationError: a document nested past the parser's recursion limit -----


def test_load_migrations_raises_migration_error_for_a_migration_nested_past_the_recursion_limit(
    project: Path,
) -> None:
    """Adversarial HIGH (round two): ``"apiVersion: theurian.dev/v1\\nid: " +
    "["*495 + "]"*495 + "\\n"`` (1023 bytes) makes ``load_yaml_mapping`` raise
    ``RecursionError`` -- reached through none of ``UnicodeDecodeError``,
    ``ValueError``, or ``yaml.YAMLError``, the three types ``_load_one``'s
    ``except`` clauses around ``load_yaml_mapping`` catch
    (``migration_loader.py``) -- so it sailed past all of them and reached
    ``resolve_context`` as a raw traceback under ``--json``, exactly the
    escape #217 closed for a YAML syntax error one exception type over.
    Depth 1000 here, well past the measured 495-bracket-pair leak threshold,
    so this stays red even if PyYAML's own recursion cost per nesting level
    shifts.

    ``security/yaml_loading.py``'s own translation of ``RecursionError`` to
    ``ValueError`` -- pinned directly at
    ``tests/unit/test_yaml_loading.py::test_excessive_nesting_raises_value_error_not_recursion_error``
    -- is what lets this reach ``_load_one``'s existing ``ValueError`` clause
    without a fourth one; this test is the loader-level half of that same
    contract, not a duplicate of the unit-level one.
    """
    migrations_dir = project / ".theurian" / "migrations"
    nested = "apiVersion: theurian.dev/v1\nid: " + "[" * 1000 + "]" * 1000 + "\n"
    migration = migrations_dir / "01K1RRRRRR01234567890ABCDE-nested.yaml"
    migration.write_text(nested)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert migration.name in str(excinfo.value)


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
    never ran. Before the fix, the failure instead happened inside
    ``pathlib.Path.glob``'s own ``scandir``, which caught the
    ``PermissionError`` it raises internally and yielded nothing: that
    returned an *empty* ``LoadedMigrations`` rather than raising, so
    ``migrate validate --json`` reported ``valid: true`` with
    ``migrationCount: 0`` for a project whose migrations were never read, and
    ``migrate apply --json`` went on to create a state database for that
    empty set. Measured directly against ``load_migrations`` for both modes;
    0o111 failed silently the identical way.
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
    # Exact equality, not `.startswith`: a mutation collapsing
    # `exc.strerror or str(exc)` to a constant survived a looser check because
    # the fixed prefix alone was still present. Anchored to
    # `os.strerror(errno.EACCES)` rather than a hardcoded string for the same
    # portability reason `_assert_file_unreadable_payload`
    # (`tests/integration/test_cli_commands.py`) already gives.
    assert str(excinfo.value) == f"{relative!r} could not be listed: {os.strerror(errno.EACCES)}"
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
    ``Path.is_file()`` -- in the enumeration comprehension that today reads
    ``paths = sorted(p for p in migrations_dir.iterdir() if
    p.name.endswith(".yaml") and p.is_file())`` (``migration_loader.py``); the
    ``Path.glob("*.yaml")`` this replaced is the historical justification for
    driving this failure at all, not what the file contains now -- needs to
    stat each entry and raises ``EACCES``. Before the fix, pathlib's ``glob``
    selector did not catch that one the way it caught the silent ``scandir``
    failure above: it escaped as a raw ``PermissionError``, a raw Rich
    traceback under ``--json``, exit 1. Measured directly against
    ``load_migrations``: mode 444 (and 400) raise; mode 500 (execute
    restored) does not.
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
    # Exact equality: see the identical reasoning on the sibling 000/111 test
    # above.
    assert str(excinfo.value) == f"{relative!r} could not be listed: {os.strerror(errno.EACCES)}"
    assert excinfo.value.remedy == (
        f"Confirm this user has read and execute permission on {relative!r} "
        f"and its parent directories, then retry."
    )


# -- a fourth face of the same class: a symlink loop at the probe (round two) -


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_raises_migrations_directory_unreadable_error_for_a_symlink_loop(
    project: Path,
) -> None:
    """Adversarial HIGH (round two, orchestrator-measured): a symlink chain
    longer than ``SYMLOOP_MAX`` at ``migrations_dir`` makes ``Path.is_dir()``
    swallow ``ELOOP`` -- the same convenience it already extends to
    ``ENOENT``/``ENOTDIR`` (the comment on ``load_migrations``'s own
    ``is_dir()`` probe, ``migration_loader.py``) -- and return ``False``.
    Before this fix that made a ``migrations_dir`` that is actually a loop,
    not a directory that never existed, hit the same ``if not is_directory:
    return LoadedMigrations.empty()`` branch a genuinely absent directory
    does: ``migrate validate --json`` reported ``valid: true`` with
    ``migrationCount: 0``, and ``migrate apply --json`` went on to seed a
    state database for the empty set it wrongly believed was the whole story
    (the CLI-level face of this is pinned in
    ``tests/integration/test_cli_commands.py``). ``os.stat`` on the identical
    path raises ``ELOOP`` -- measured directly below, not inferred -- so the
    fix has to probe explicitly rather than trust ``is_dir()``'s own
    exception-swallowing.

    Forty links: measured directly against this machine's loop threshold (32
    on both Linux and macOS by default) to close reliably without depending
    on a platform constant Python does not expose.
    """
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.rmdir()
    theurian_dir = project / ".theurian"
    links = [theurian_dir / f"loop-{i}" for i in range(40)]
    migrations_dir.symlink_to(links[0])
    for index in range(len(links) - 1):
        links[index].symlink_to(links[index + 1])
    links[-1].symlink_to(links[0])

    with pytest.raises(OSError) as os_excinfo:
        migrations_dir.stat()
    assert os_excinfo.value.errno == errno.ELOOP, "fixture must actually reproduce ELOOP"

    with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(migrations_dir.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be listed: {os.strerror(errno.ELOOP)}"
    assert excinfo.value.remedy == (
        f"{relative!r} is a loop of symbolic links. Point it at a real directory, then retry."
    )


# -- errno policy for a failure during enumeration, not the is_dir() probe ---


@pytest.mark.parametrize("stale_errno", [errno.ENOENT, errno.ENOTDIR], ids=["ENOENT", "ENOTDIR"])
def test_load_migrations_treats_a_stale_enumeration_failure_as_an_empty_set(
    project: Path, monkeypatch: pytest.MonkeyPatch, stale_errno: int
) -> None:
    """Round-two errno policy: ``ENOENT``/``ENOTDIR`` raised while
    *enumerating* ``migrations_dir`` -- after the ``is_dir()`` probe already
    answered ``True``, one line above -- means the directory vanished or was
    replaced between the probe and the listing, the identical race
    ``is_dir()``'s own ``ENOENT``/``ENOTDIR`` swallow already treats as
    "nothing to load" (``if not is_directory: return
    LoadedMigrations.empty()``, ``migration_loader.py``). Before this fix,
    every enumeration-time ``OSError`` converted to
    ``MigrationsDirectoryUnreadableError`` regardless of errno, which would
    have reported a refusal for a directory that merely raced its own
    deletion instead of the same "nothing to load" answer a directory that
    was simply never created gets.

    Driven with an injected ``OSError`` on ``Path.iterdir`` rather than an
    actual race: a real race is not reliably reproducible on demand, and the
    contract under test is what ``load_migrations`` does with a given errno,
    not whether the race itself can be forced. The patch only intercepts
    calls against this test's own ``migrations_dir``, delegating every other
    ``Path.iterdir()`` call in the process -- including ``schema_root()``'s
    own filesystem probes -- to the real implementation.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)

    real_iterdir = Path.iterdir

    def _fake_iterdir(self: Path) -> Iterator[Path]:
        if self == migrations_dir:
            raise OSError(stale_errno, os.strerror(stale_errno))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _fake_iterdir)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    assert loaded == LoadedMigrations.empty()


def test_load_migrations_raises_migrations_directory_unreadable_error_for_an_unrecognised_errno(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The residual case in the same errno policy: an enumeration-time
    ``OSError`` whose errno is neither the permission family
    (``EACCES``/``EPERM``, unchanged -- pinned by the 000/111/444 tests
    above) nor the stale-race family (``ENOENT``/``ENOTDIR``, pinned
    immediately above) still gets a refusal, but with a remedy that does not
    misdiagnose it as a permission problem: "resolves to a readable
    directory", not "read and execute permission". ``ENAMETOOLONG`` drives
    this because it is portable -- reachable on every POSIX platform without
    depending on a filesystem-specific limit -- and injected rather than
    constructed for real, since an actual over-length path is itself
    platform-dependent to build.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)

    real_iterdir = Path.iterdir

    def _fake_iterdir(self: Path) -> Iterator[Path]:
        if self == migrations_dir:
            raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _fake_iterdir)

    with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(migrations_dir.relative_to(project))
    assert str(excinfo.value) == (
        f"{relative!r} could not be listed: {os.strerror(errno.ENAMETOOLONG)}"
    )
    assert excinfo.value.remedy == (
        f"Confirm {relative!r} resolves to a readable directory, then retry."
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
    """Enumeration parity pin -- one of two green pins in this batch. The
    other is the sibling test immediately above,
    ``test_load_migrations_on_an_ordinarily_readable_empty_directory_returns_an_empty_set``:
    that one pins the legitimately-empty case, this one pins which
    *non-empty* set of files gets loaded.

    Measured directly against CPython 3.13's stdlib, not assumed:
    ``pathlib.Path.glob("*.yaml")`` -- unlike ``glob.glob()``, which hides a
    leading dot by default -- does **not** treat ``.hidden.yaml`` as hidden,
    so it was loaded exactly like any other ``*.yaml`` sibling
    (``sorted(Path(tmp).glob("*.yaml"))`` on CPython 3.13.11 returns both).
    That measurement is now the historical justification for the population
    this test pins, not a live equivalence: the #214 fix's enumeration no
    longer calls ``Path.glob`` at all (``glob`` swallows the very
    ``PermissionError`` that fix exists to surface -- see the tests above),
    it calls ``migrations_dir.iterdir()`` and filters with
    ``p.name.endswith(".yaml")`` in ``migration_loader.py``, and this test is
    what keeps that substitution matching ``glob``'s dotfile behaviour rather
    than silently narrowing which migrations a project has, in either
    direction.

    The bare ``.yaml`` file below guards a narrower mutation than the dotfile
    does: ``Path(".yaml").suffix`` is ``""`` (a name with nothing before its
    only dot has no suffix in ``pathlib``), so a regression from
    ``p.name.endswith(".yaml")`` to ``p.suffix == ".yaml"`` would silently
    drop it -- while ``.hidden.yaml`` has a real suffix and would still load
    under either form, so it alone would not catch that regression.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    dotfile_migration_id = "01K1DDDDDD01234567890ABCDE"
    dotfile_migration = _VALID_MIGRATION.replace(
        "01K1KKKKKK01234567890ABCDE", dotfile_migration_id
    ).replace("architecture.auth-policy", "architecture.dotfile-policy")
    (migrations_dir / ".hidden.yaml").write_text(dotfile_migration)
    bare_migration_id = "01K1BBBBBB01234567890ABCDE"
    bare_migration = _VALID_MIGRATION.replace(
        "01K1KKKKKK01234567890ABCDE", bare_migration_id
    ).replace("architecture.auth-policy", "architecture.bare-policy")
    (migrations_dir / ".yaml").write_text(bare_migration)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    ids = {str(m.migration_id) for m in loaded.migration_set}
    assert ids == {"01K1KKKKKK01234567890ABCDE", dotfile_migration_id, bare_migration_id}


# -- ordering determinism: which file a multi-failure error names -----------


def test_load_migrations_reports_the_lexicographically_first_invalid_file(
    project: Path,
) -> None:
    """Code-review MEDIUM (round two): a mutation collapsing the
    enumeration's own ``sorted(...)`` to ``list(...)`` (``migration_loader.py``)
    survived round one's suite -- nothing pinned *which* of several invalid
    files a multi-file failure names, only that some ``MigrationError`` was
    raised, so an unstable ``iterdir()`` order would have passed unnoticed.
    Two invalid files, named so filesystem creation order and lexicographic
    order disagree (``z-...`` written first, ``a-...`` second): the loop in
    ``load_migrations`` that calls ``_load_one`` once per sorted path raises
    on the first invalid file it reaches, so pinning *which* file the error
    names pins the sort itself, not merely that a sort happened.
    """
    migrations_dir = project / ".theurian" / "migrations"
    malformed = "id: [unclosed\n  bad: {{{\n"
    (migrations_dir / "z-01K1ZZZZZZ01234567890ABCDE-second.yaml").write_text(malformed)
    (migrations_dir / "a-01K1AAAAAA01234567890ABCDE-first.yaml").write_text(malformed)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert "a-01K1AAAAAA01234567890ABCDE-first.yaml" in str(excinfo.value)
    assert "z-01K1ZZZZZZ01234567890ABCDE-second.yaml" not in str(excinfo.value)


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


# -- SchemaUnreadableError: the read succeeds, the content is corrupt -------
#
# Adversarial MEDIUM (round two, measured directly against `_validator`
# below, not inferred): `_validator`'s own `try/except OSError`
# (`migration_loader.py`) only guards `schema_path.read_text(...)` failing to
# *run*. A read that runs and returns corrupt content is a different failure
# shape, and it escaped uncaught in four independently measured forms.
#
# `_validator` is `functools.lru_cache`d on `schema_root`; each test below
# clears it both before and after. Each also uses its own `tmp_path`-derived
# directory, so the cache key alone would already avoid a collision with any
# other test in this file -- the explicit clear is what makes the assertion
# "the guard now catches this", not "this test happened to get a fresh cache
# key".


def test_validator_raises_schema_unreadable_error_for_truncated_json(tmp_path: Path) -> None:
    """Measured: an unterminated JSON string raises `json.JSONDecodeError`,
    not the `OSError` `_validator`'s guard catches.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text('{"type": "obj')

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert excinfo.value.remedy


def test_validator_raises_schema_unreadable_error_for_an_empty_file(tmp_path: Path) -> None:
    """Measured: an empty file also raises `json.JSONDecodeError` --
    `Expecting value` rather than truncation's `Unterminated string`, a
    distinct message from the same exception type, pinned as its own case so
    a fix keyed on one message does not leave the other uncaught.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("")

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert excinfo.value.remedy


def test_validator_raises_schema_unreadable_error_for_non_utf8_bytes(tmp_path: Path) -> None:
    """Measured: bytes that are not valid UTF-8 raise `UnicodeDecodeError` at
    the same `read_text(encoding="utf-8")` call the `OSError` guard already
    wraps -- a failure *inside* the guarded call, not a different call the
    guard never reaches.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_bytes(b"\xff\xfe\x00\x01")

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert excinfo.value.remedy


def test_validator_raises_schema_unreadable_error_for_a_json_list_schema(tmp_path: Path) -> None:
    """Measured: a schema that parses but is a JSON *list* rather than an
    object raises `AttributeError`, not at the read but one line later, at
    `Draft202012Validator(schema)` construction -- `jsonschema` calls
    `schema.get(...)` internally, and a `list` has no `.get`. The read itself
    succeeded, so this is outside even the two `json.JSONDecodeError` shapes
    above.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("[1, 2, 3]")

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert excinfo.value.remedy
