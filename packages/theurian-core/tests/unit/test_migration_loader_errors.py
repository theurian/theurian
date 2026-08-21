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
import json
import os
import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, override

import pytest
import yaml
from jsonschema.exceptions import ValidationError

from theurian.cli.context import schema_root as real_schema_root
from theurian.domain.errors import (
    EscapeSite,
    MigrationContentUnreadableError,
    MigrationError,
    MigrationFileUnreadableError,
    MigrationsDirectoryUnreadableError,
    PathDepthExceededError,
    PathEscapeError,
    SchemaUnreadableError,
)
from theurian.domain.migration import LoadedMigrations
from theurian.infrastructure.filesystem import migration_loader
from theurian.infrastructure.filesystem.migration_loader import (
    MAX_DOCUMENT_NESTING,
    MAX_DOCUMENT_NODES,
    _escape_role_of,
    _refuse_a_document_that_nests_too_deep,
    _schema_rejection,
    _validator,
    load_migrations,
    validate_migration_document,
)
from theurian.security.yaml_loading import load_yaml_mapping

pytestmark = pytest.mark.unit

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_cli_commands.py` uses before a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: The checklist every escape remedy ends in. It names the population the
#: culprit must be in and leaves the identification to the reader, because three
#: attempts to identify it here were each refuted (`EscapeSite`, `domain/errors.py`).
_CHECK_THE_CHAIN = (
    "Check it, each directory above it, and each link it resolves through, for the "
    "link that leaves the project. Repoint that link so it resolves inside the "
    "project, or remove that link, then retry."
)
#: `PathEscapeError`'s `"symlink"`-role remedy. `{name}` takes an already-`repr`'d
#: string. The opening sentence is the whole of what `lstat` proved.
_SYMLINK_REMEDY = "{name} is itself a symbolic link. " + _CHECK_THE_CHAIN


def _assert_names_no_file_to_delete(remedy: str) -> None:
    """The invariant that survives every construction, and the one a reviewer
    should attack next.

    Deletion-as-cure requires knowing the culprit, and the culprit can sit
    anywhere on the entry's ancestor chain or its resolution chain -- so no
    remedy may propose removing a *named* file. Every "remove" it contains must
    be "remove that link", the one the reader locates by walking the checklist.
    """
    assert "remove it" not in remedy, "that names the entry as the thing to delete"
    assert "delete" not in remedy, "no remedy proposes a deletion of its own"
    assert remedy.count("remove") == remedy.count("remove that link"), (
        "every 'remove' must refer to the link the reader locates, never to a filename"
    )


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


def test_load_migrations_gives_the_permission_remedy_for_an_injected_eperm_on_the_file_read(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-four mutation-adversarial (mutation x4 SURVIVED): the file-read
    sibling of
    ``test_load_migrations_gives_the_permission_remedy_for_an_injected_eperm``
    below, which drives ``EPERM`` only on the *directory* enumeration's own
    ``iterdir()`` call. ``_read_failure_remedy``'s ``EACCES``/``EPERM`` branch
    (``domain/errors.py``) is shared by both call sites --
    :class:`MigrationsDirectoryUnreadableError` and
    :class:`MigrationFileUnreadableError` alike -- but every existing
    ``MigrationFileUnreadableError`` permission test in this file (the
    ``chmod 0o000`` test immediately above) raises ``EACCES``, never
    ``EPERM``, so a mutation dropping ``_errno.EPERM`` from that tuple
    entirely would still pass every one of them.

    Driven with a call-counted ``Path.stat`` patch rather than a real
    ``chmod``, because ``EPERM`` is not reliably producible with a real mode
    change on every platform this suite runs on (the identical reasoning the
    directory-side sibling test gives). The patch lets the *first*
    ``follow_symlinks=True`` call against this file -- enumeration's own
    per-entry classification in ``_entry_is_migration_file``, which must
    succeed for this file to be included in the enumerated set at all --
    through unchanged, and fails only the *second* -- the read this file's
    own ``_load_one`` performs via ``read_source_file``'s ``resolved.stat()``
    (``security/paths.py``). Measured directly: without the counter, patching
    every call fails classification itself and reports
    ``MigrationsDirectoryUnreadableError`` instead, exercising the wrong
    class entirely.
    """
    migrations_dir = project / ".theurian" / "migrations"
    target = migrations_dir / "01K1PPPPPP01234567890ABCDE-eperm.yaml"
    target.write_text(_VALID_MIGRATION)

    real_stat = Path.stat
    calls = {"n": 0}

    def _fake_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if follow_symlinks and self.name == target.name:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError(errno.EPERM, os.strerror(errno.EPERM))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    with pytest.raises(MigrationFileUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert calls["n"] >= 2, "fixture must actually let classification pass before failing the read"
    relative = str(target.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be read: {os.strerror(errno.EPERM)}"
    assert excinfo.value.remedy == (
        f"Confirm this user has read permission on {relative!r} and its "
        f"parent directory, then retry."
    )


def test_load_migrations_names_the_still_exists_remedy_when_a_file_vanishes_before_the_read(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-four mutation-adversarial (mutation b5 SURVIVED):
    :func:`_read_failure_remedy`'s ``missing_or_wrong_text`` parameter
    (``domain/errors.py``) is what :class:`MigrationFileUnreadableError` falls
    back to when the errno is neither ``ELOOP``, ``EACCES``/``EPERM``, nor
    ``EISDIR`` -- a plain ``ENOENT`` from a migration file that simply is not
    there any more when ``_load_one`` tries to read it, distinct from the
    entry-level *symlink*-dangling case
    (``test_load_migrations_raises_migration_file_unreadable_error_for_a_dangling_symlink_entry``
    below), which passes its own explicit ``remedy=`` and never reaches this
    fallback at all. Nothing in this file previously asserted this fallback's
    exact text for the plain-file case -- every other
    :class:`MigrationFileUnreadableError` remedy test pins ``ELOOP`` or
    ``EACCES`` -- so a mutation collapsing ``missing_or_wrong_text`` here to
    an empty string survived every test in this file.

    Driven the same way as the ``x4`` test above: a regular (non-symlink)
    file present for enumeration's own classification, removed only for the
    second, read-time ``stat()`` call, reproducing a plain file vanishing
    between ``iterdir()`` finding it and ``_load_one`` reading it -- a race,
    not a symlink fault, so the generic ``f"Confirm {migration_path!r} still
    exists, then retry."`` is the one this pins, not the dangling-symlink
    text.
    """
    migrations_dir = project / ".theurian" / "migrations"
    target = migrations_dir / "01K1QQQQQQ01234567890ABCDE-race.yaml"
    target.write_text(_VALID_MIGRATION)

    real_stat = Path.stat
    calls = {"n": 0}

    def _fake_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if follow_symlinks and self.name == target.name:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    with pytest.raises(MigrationFileUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert calls["n"] >= 2, "fixture must actually let classification pass before failing the read"
    relative = str(target.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be read: {os.strerror(errno.ENOENT)}"
    assert excinfo.value.remedy == f"Confirm {relative!r} still exists, then retry."


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
    ``migrations_dir`` itself -- the probe needs only traversal permission on
    the parent to raise ``EACCES``, whether that probe is today's explicit
    ``migrations_dir.stat()`` (``migration_loader.py``) or the ``is_dir()``
    call that preceded it before round two's fix. A directory `chmod 000`'d
    itself instead of its parent leaves the probe succeeding either way,
    which was `glob`'s own, different, previously-unfixed silent-empty-result
    case, closed by the tests below (issue #214).
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
    (the sibling case above). At the time of issue #214, the top-level probe
    was still ``is_dir()`` -- round two later replaced it with an explicit
    ``os.stat`` call, for the unrelated ``ELOOP``-swallowing reason pinned
    above -- and it succeeded under either mode, since stat needs only
    traversal permission on ancestors, not on the target itself, so the
    ``OSError`` conversion issue #205 had already wrapped around that probe
    never ran. Before issue #214's own fix, the failure instead happened inside
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
    ``ENOENT``/``ENOTDIR`` (the comment on ``load_migrations``'s own explicit
    ``os.stat`` probe, ``migration_loader.py``, which replaced ``is_dir()``
    for exactly this reason) -- and return ``False``.
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


# -- round four: migrations_dir itself as a symlink, beyond the loop case ----
#
# The test above drives `migrations_dir` being a *loop*. `migrations_dir`'s
# top-of-function probe (`os.stat`, following symlinks) already answers that
# case, and a dangling target the identical way any missing directory is
# answered -- `ENOENT` is folded into `LoadedMigrations.empty()`
# (`load_migrations`'s own `except OSError`, `migration_loader.py`) alongside
# a genuinely absent directory, and an outside-pointing symlink is not
# checked directly at all -- it is `_load_one`'s call to `read_source_file`,
# reached only once a `*.yaml` entry exists to read, that incidentally raises
# `PathEscapeError` one call site later (the load_migrations docstring's own
# `PathEscapeError` note names this). Both are wrong in the same direction as
# the loop case: a directory that is not safely usable reports "nothing to
# load" (or, for `apply`, seeds state for that nothing) instead of refusing.
# Orchestrator-measured today, before any of this section's fixes exist:
#
#   * A dangling `migrations_dir` symlink: `load_migrations` returns
#     `LoadedMigrations.empty()`; `migrate apply --json` reports
#     `databaseCreated: true` and creates `.theurian/state/active.json` and a
#     `.sqlite` database for that empty set.
#   * A `migrations_dir` symlink resolving OUTSIDE `project_root` to a
#     directory holding no `*.yaml` files: `load_migrations` returns
#     `LoadedMigrations.empty()` (no entry to reach `_load_one`, so the
#     escape check `_load_one` would otherwise trigger never runs); `migrate
#     validate --json` reports `valid: true, migrationCount: 0`, exit 0.


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_refuses_a_dangling_migrations_directory_symlink(
    project: Path,
) -> None:
    """RED (round four): a `.theurian/migrations` that is itself a dangling
    symlink -- not merely one loop *entry* inside it -- must refuse, mirroring
    the entry-level dangling remedy
    (`test_load_migrations_raises_migration_file_unreadable_error_for_a_dangling_symlink_entry`
    above) at directory granularity: "symbolic link target is missing" /
    "restore the target or remove the link", not the generic directory
    remedy `_directory_unreadable_remedy`'s residual branch gives.

    Today `migrations_dir.stat()` (`load_migrations`'s top-of-function probe)
    follows the dangling link and raises `ENOENT`, which the probe's own
    `except OSError` clause already answers with `LoadedMigrations.empty()`
    -- the identical answer a genuinely absent directory gets -- so this
    `pytest.raises` block does not raise at all until `migrations_dir`'s own
    `is_symlink()` (an `lstat`, checked *before* the follow-symlinks probe) is
    consulted to tell the two cases apart.
    """
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.rmdir()
    migrations_dir.symlink_to(project / ".theurian" / "does-not-exist")

    with pytest.raises(OSError) as os_excinfo:
        migrations_dir.stat()
    assert os_excinfo.value.errno == errno.ENOENT, "fixture must actually reproduce ENOENT"
    assert migrations_dir.is_symlink(), (
        "fixture must actually be a symlink, not a missing directory"
    )

    with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(migrations_dir.relative_to(project))
    assert (
        str(excinfo.value) == f"{relative!r} could not be listed: symbolic link target is missing"
    )
    assert excinfo.value.remedy == (
        f"{relative!r} is a symbolic link whose target is missing. Restore the target or "
        f"remove the link, then retry."
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_refuses_a_migrations_directory_symlink_to_an_empty_outside_directory(
    project: Path, tmp_path: Path
) -> None:
    """RED (round four, orchestrator-measured -- see this section's banner
    comment for the exact CLI payload measured before this fix): a
    `.theurian/migrations` symlinked to a directory OUTSIDE `project_root`
    must refuse with `PathEscapeError`, the same type `_load_one`'s own
    `read_source_file` call already raises for an outside-pointing *entry*
    (`load_migrations`'s own `PathEscapeError` docstring note). The outside
    directory here is deliberately EMPTY -- holding no `*.yaml` files at all
    -- which is exactly the case that incidental check never reaches: with
    nothing to enumerate, `_load_one` is never called, so nothing ever probes
    where the directory resolves. A non-empty outside directory would
    (today) already raise `PathEscapeError` through that incidental path,
    which is why this section's fixture picks the empty case specifically:
    it is the one gap that check does not already close.

    The message, the remedy and ``.entry`` are pinned too (issue #233; the
    reasoning is on `PathEscapeError`'s own docstring, `domain/errors.py`).
    Round four's version pinned only the type and called the remedy out of
    scope; that remedy turned out to be the *false* one.
    """
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.rmdir()
    outside_empty = tmp_path / "outside-empty"
    outside_empty.mkdir()
    migrations_dir.symlink_to(outside_empty)

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(migrations_dir.relative_to(project))
    assert excinfo.value.entry == EscapeSite(relative, "symlink"), (
        "the `is_symlink()` lstat ran on this exact path, which is what earns `symlink`"
    )
    assert str(excinfo.value) == f"{relative!r} escapes the permitted root"
    assert excinfo.value.remedy == _SYMLINK_REMEDY.format(name=repr(relative))
    assert str(outside_empty) not in str(excinfo.value), "the resolved target is not echoed"
    assert str(outside_empty) not in excinfo.value.remedy, "nor into the remedy"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_names_the_entry_when_one_migration_file_escapes_the_project(
    project: Path, tmp_path: Path
) -> None:
    """Issue #233's second shape, at the other call site: one `*.yaml` *entry*
    inside a healthy `migrations_dir` symlinked outside `project_root`.

    The refusal comes from `_load_one`'s call to `read_source_file`, not from
    the directory-level check above, and `read_source_file` cannot name the
    entry itself (`tests/unit/test_path_security.py::
    test_no_refusal_branch_echoes_the_attacker_supplied_path` pins why).
    `_load_one` is the call site holding a `.theurian/migrations/` name
    `iterdir()` returned, so it is the one that attaches `entry` -- and here
    the entry genuinely *is* the link, so it earns the strong role.
    """
    outside_body = tmp_path / "id_ed25519"
    outside_body.write_text("PRIVATE KEY\n")
    migrations_dir = project / ".theurian" / "migrations"
    entry = migrations_dir / "01K1EVAAAA01234567890ABCDE-escape.yaml"
    entry.symlink_to(outside_body)

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(entry.relative_to(project))
    assert excinfo.value.entry == EscapeSite(relative, "symlink")
    assert str(excinfo.value) == f"{relative!r} escapes the permitted root"
    assert excinfo.value.remedy == _SYMLINK_REMEDY.format(name=repr(relative))
    assert str(outside_body) not in str(excinfo.value), "the resolved target is not echoed"
    assert str(outside_body) not in excinfo.value.remedy, "nor into the remedy"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_does_not_call_a_plain_entry_a_symlink_when_an_ancestor_escapes(
    project: Path, tmp_path: Path
) -> None:
    """The ancestor/plain-file face: `.theurian` itself is the outside-pointing symlink
    and the migration entries under it are ORDINARY FILES.

    Reachable from a plain `git clone` (issue #237), and an earlier commit on
    this branch asserted "this entry is a symbolic link ... or remove it" for every
    name it was given. Measured against the real CLI before this fix:
    following that remedy deleted the user's own authored migration, and once
    `migrations/` was empty `migrate validate` exited 0 -- with `.theurian`
    still resolving outside the project. The remedy walked the reader into
    losing work *and* into a silent pass.

    Fixing the acceptance itself is issue #237's box and is deliberately not
    attempted here; what this pins is that the refusal no longer *instructs*
    anyone into it. The three assertions are the whole contract: do not call
    this file a link, do not tell anyone to remove it, and do send them up the
    directory chain where the link actually is.
    """
    outside_theurian = tmp_path / "outside-theurian"
    shutil.move(str(project / ".theurian"), str(outside_theurian))
    (project / ".theurian").symlink_to(outside_theurian)
    migrations_dir = project / ".theurian" / "migrations"
    entry = migrations_dir / "01K1PLAIN101234567890ABCDE-plain.yaml"
    entry.write_text(_VALID_MIGRATION)
    assert not entry.is_symlink(), "fixture must drive a PLAIN file, not a link"

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(entry.relative_to(project))
    assert excinfo.value.entry == EscapeSite(relative, "resolved")
    assert "is a symbolic link" not in excinfo.value.remedy, (
        "the lstat says this entry is not one; the remedy must not claim otherwise"
    )
    assert "remove it" not in excinfo.value.remedy, (
        "following that instruction deletes the user's authored migration"
    )
    assert "each directory above it" in excinfo.value.remedy, (
        "the escaping component is an ancestor; that is where the reader must be sent"
    )
    assert excinfo.value.remedy == f"{relative!r} resolves outside the project. " + (
        _CHECK_THE_CHAIN
    )
    _assert_names_no_file_to_delete(excinfo.value.remedy)


def test_a_content_file_nested_too_deep_is_not_reported_as_an_escape(project: Path) -> None:
    """`_parse_upsert`'s depth face, and the guard that keeps it distinct.

    A `contentFile` nesting past the limit reaches `resolve_within_root` -- the
    *second* of the three sibling branches that can refuse a `contentFile` on
    containment grounds -- and raises `PathDepthExceededError`. That type is a
    `PathEscapeError` subclass, so the referrer wrapper immediately below it in
    the source would otherwise re-label it "the path this names resolves
    outside the project", which is false: this path never left the root.
    """
    migration = project / ".theurian" / "migrations" / "01K1MMMMMM01234567890ABCDE-deep.yaml"
    migration.write_text(
        _UPSERT_MIGRATION.format(content_file="/".join(["deep"] * 40) + "/body.md")
    )

    with pytest.raises(PathDepthExceededError) as excinfo:
        load_migrations(project, project / ".theurian" / "migrations", real_schema_root())

    relative = str(migration.relative_to(project))
    assert "escapes" not in str(excinfo.value), "this path never left the root"
    assert excinfo.value.entry == EscapeSite(relative, "referrer"), (
        "named like its escaping siblings: the migration file is where to look"
    )
    assert str(excinfo.value) == (
        f"{relative!r} names a path that exceeds the permitted depth limit of 32 segments"
    )
    _assert_names_no_file_to_delete(excinfo.value.remedy)


def test_the_sibling_content_file_branches_name_the_migration_file_too(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other two of `_parse_upsert`'s three containment refusals.

    Only the `relative_to` branch is reachable through the filesystem: by the
    time `resolve_within_root` and `read_source_file` run, the path has already
    been resolved and proved inside the root, so their escape branches are
    reachable only if the tree changes underneath the call. That race is why
    they exist, and it is also why this test drives them by injection rather
    than with a fixture -- but which of three adjacent branches happens to fire
    must not decide whether the user is told where to look, and before this
    they raised anonymously while their neighbour four lines up did not.
    """
    migration = project / ".theurian" / "migrations" / "01K1MMMMMM01234567890ABCDE-race.yaml"
    migration.write_text(_UPSERT_MIGRATION.format(content_file="body.md"))
    (project / ".theurian" / "migrations" / "body.md").write_text("# Body\n")

    def _escape(*_args: object, **_kwargs: object) -> Path:
        raise PathEscapeError("../../../etc/id_ed25519", str(project))

    monkeypatch.setattr(migration_loader, "resolve_within_root", _escape)

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, project / ".theurian" / "migrations", real_schema_root())

    relative = str(migration.relative_to(project))
    assert excinfo.value.entry == EscapeSite(relative, "referrer")
    assert "id_ed25519" not in str(excinfo.value), "the author-written value stays unechoed"
    assert "id_ed25519" not in excinfo.value.remedy


# -- the invariant every construction shares: no remedy names a file to delete -
#
# `is_symlink()` proves "this name is a link", never "this link is the escape",
# and the constructions below are the ones that earn the lstat while the escape
# sits elsewhere. Earlier rounds tried to keep the destructive "remove it" and
# withhold it from exactly these cases; the sibling-chain construction
# (`test_a_sibling_link_chain...` below) refuted the last such rule. So the
# remedy no longer names anything to delete, and that -- not a role assignment --
# is what these tests now pin: whatever role the entry gets, following the
# remedy destroys nothing. The role only decides whether the refusal opens by
# saying the entry is itself a link.


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_an_entry_linking_to_a_sibling_under_an_escaped_ancestor_names_no_file_to_delete(
    project: Path, tmp_path: Path
) -> None:
    """The entry IS a symlink pointing at a sibling inside its own directory,
    and `.theurian` is what escapes.

    `lstat` says link, so the entry gets role "symlink" and the refusal opens
    by saying so -- which is safe now only because the remedy names nothing to
    delete. An earlier rule kept "remove it" for entries like this, and
    following it deleted the user's committed migration while `.theurian` stayed
    outside; the invariant asserted here is that no such instruction survives.
    """
    shared = project / ".theurian" / "shared"
    shared.mkdir()
    (shared / "real.yaml").write_text(_VALID_MIGRATION)
    entry = project / ".theurian" / "migrations" / "01K1SIBLNG01234567890ABCDE-x.yaml"
    entry.symlink_to(Path("..") / "shared" / "real.yaml")
    outside_theurian = tmp_path / "outside-theurian"
    shutil.move(str(project / ".theurian"), str(outside_theurian))
    (project / ".theurian").symlink_to(outside_theurian)

    migrations_dir = project / ".theurian" / "migrations"
    assert entry.is_symlink(), (
        "the entry is a link, so it gets role 'symlink' -- which must stay safe"
    )

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    _assert_names_no_file_to_delete(excinfo.value.remedy)
    assert "each directory above it" in excinfo.value.remedy, (
        "the escaping component is an ancestor; the checklist must reach it"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_migrations_directory_link_under_an_escaped_ancestor_names_no_file_to_delete(
    project: Path, tmp_path: Path
) -> None:
    """`.theurian/migrations` is itself a symlink pointing at a sibling that is
    lexically inside the project, while `.theurian` escapes.

    The directory call site runs its own `is_symlink()`, so this entry also
    gets role "symlink" -- and that is deliberate and safe, because the remedy
    it produces names no file to delete. An earlier version paired the strong
    role with "repoint it inside the project, or remove it", both useless here:
    the link's target is already inside, and removing the link destroys the
    layout while `.theurian` still resolves outside. The invariant asserted here
    is that neither instruction survives.
    """
    real = project / ".theurian" / "real-migrations"
    shutil.move(str(project / ".theurian" / "migrations"), str(real))
    (project / ".theurian" / "migrations").symlink_to(Path("real-migrations"))
    outside_theurian = tmp_path / "outside-theurian"
    shutil.move(str(project / ".theurian"), str(outside_theurian))
    (project / ".theurian").symlink_to(outside_theurian)

    migrations_dir = project / ".theurian" / "migrations"
    assert migrations_dir.is_symlink(), "fixture must reach the directory-level symlink check"

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert excinfo.value.entry is not None
    assert excinfo.value.entry.name == ".theurian/migrations"
    _assert_names_no_file_to_delete(excinfo.value.remedy)
    assert "each directory above it" in excinfo.value.remedy


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_an_entry_that_is_itself_a_symlink_opens_with_the_sentence_lstat_proves(
    project: Path, tmp_path: Path
) -> None:
    """The entry is a symlink whose own link text is lexically in-project
    (`../../lib/real.yaml`), and `lib` is the escape.

    `lstat` says this entry is a link, so the refusal may say so -- that is the
    whole of the strong role's claim now, and the checklist that follows is the
    same one every escape remedy carries. Without a case that keeps the
    sentence, the rule could be satisfied by never saying anything, which would
    be safe and useless.
    """
    outside_lib = tmp_path / "outside-lib"
    outside_lib.mkdir()
    (outside_lib / "real.yaml").write_text(_VALID_MIGRATION)
    (project / "lib").symlink_to(outside_lib)
    migrations_dir = project / ".theurian" / "migrations"
    entry = migrations_dir / "01K1LEXICAL1234567890ABCD-x.yaml"
    entry.symlink_to(Path("..") / ".." / "lib" / "real.yaml")

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(entry.relative_to(project))
    assert excinfo.value.entry == EscapeSite(relative, "symlink")
    assert excinfo.value.remedy == _SYMLINK_REMEDY.format(name=repr(relative))
    _assert_names_no_file_to_delete(excinfo.value.remedy)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_sibling_link_chain_is_cured_by_the_checklist_without_deleting_anything(
    project: Path, tmp_path: Path
) -> None:
    """The construction that refuted the previous closure argument: `x.yaml` is
    a symlink to its sibling `y.yaml`, and `y.yaml` is the one pointing outside.

    Every conjunct of the old three-probe rule held for `x` -- its own `lstat`
    said link, its parent chain resolved inside, and it resolved outside -- so
    `x` was named as the culprit and the reader told to remove it. Measured
    against that build: removing `x` produced the same refusal for `y`,
    removing `y` too ended at `valid: true`, and two Git-tracked files were
    gone. The premise was false because resolution continues *through* the
    final component's target chain, so "the parent is inside and the result is
    outside" never left a single candidate.

    The minimal cure is repointing `y` alone -- `x` was never independently
    broken -- and this test performs exactly that: no deletion, one edit, the
    refusal gone. That is what the checklist form buys, and it is the property
    to attack next.

    The measured CLI construction used two `*.yaml` siblings. The intermediate
    link here is deliberately *not* named `*.yaml`, and that is the only
    difference: with both enumerated, `x` and `y` resolve to one file and the
    cured state trips the duplicate-migration-id rule instead, which would mask
    the thing under test. What matters -- the culprit sitting on the entry's
    resolution chain rather than being the entry -- is identical.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "real.yaml").write_text(_VALID_MIGRATION)
    migrations_dir = project / ".theurian" / "migrations"
    intermediate = project / ".theurian" / "link-y"
    intermediate.symlink_to(outside / "real.yaml")
    entry = migrations_dir / "01K1XXXXXX01234567890ABCDE-x.yaml"
    entry.symlink_to(Path("..") / intermediate.name)

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    # The entry is named -- lstat proved it is a link -- and it is not the
    # culprit. Nothing in the remedy tells anyone to delete it.
    assert excinfo.value.entry == EscapeSite(str(entry.relative_to(project)), "symlink")
    _assert_names_no_file_to_delete(excinfo.value.remedy)
    assert "each link it resolves through" in excinfo.value.remedy, (
        "the culprit is on the resolution chain; the checklist must send the reader there"
    )

    # Follow the checklist: walk the links the entry resolves through, find the
    # intermediate, repoint that one. One edit, no deletion, refusal gone.
    inside_body = project / ".theurian" / "real.yaml"
    inside_body.write_text(_VALID_MIGRATION)
    intermediate.unlink()
    intermediate.symlink_to(Path(inside_body.name))

    loaded = load_migrations(project, migrations_dir, real_schema_root())
    assert entry.is_symlink(), "the cure deleted nothing the refusal had named"
    assert len(loaded.migration_set) == 1


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_role_probe_that_cannot_run_degrades_rather_than_guessing(
    project: Path, tmp_path: Path
) -> None:
    """`_escape_role_of`'s own `except OSError` branch, driven rather than
    asserted: a mutation replacing its `return "resolved"` with `"symlink"`
    survived the suite, which means nothing was checking that a probe it cannot
    run stays on the safe answer.

    A directory `chmod 0o000` makes the `is_symlink()` lstat raise `EACCES`.
    The answer must be the role that proposes no deletion -- guessing the
    strong one from a failed probe would be the original defect with less
    evidence behind it.
    """
    denied = tmp_path / "denied"
    denied.mkdir()
    victim = denied / "entry.yaml"
    victim.write_text("x")
    denied.chmod(0o000)
    try:
        with pytest.raises(OSError) as os_excinfo:
            victim.is_symlink()
        assert os_excinfo.value.errno == errno.EACCES, "fixture must actually deny the lstat"

        assert _escape_role_of(victim) == "resolved"
    finally:
        denied.chmod(0o700)

    assert _escape_role_of(tmp_path / "nope" / "entry.yaml") == "resolved", (
        "a path that does not exist is not evidence of a symbolic link either"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_names_the_migration_file_when_its_content_file_escapes(
    project: Path,
) -> None:
    """A `contentFile` escaping names the migration file
    that carries it as the place to look.

    An earlier commit on this branch left this case unnamed, recording that "the only name it could
    give is the author-written value". That reasoning was false:
    `MigrationContentUnreadableError`, four lines up the same function, already
    prints this migration file's own project-relative name for a `contentFile`
    that merely fails to read. The value itself stays unechoed (SEC-7) -- it is
    the author-written string -- so the role is `referrer`: where to open, not
    what to delete.
    """
    migration = project / ".theurian" / "migrations" / "01K1MMMMMM01234567890ABCDE-ref.yaml"
    migration.write_text(_UPSERT_MIGRATION.format(content_file="../../../../../../etc/id_ed25519"))

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, project / ".theurian" / "migrations", real_schema_root())

    relative = str(migration.relative_to(project))
    assert excinfo.value.entry == EscapeSite(relative, "referrer")
    assert str(excinfo.value) == f"{relative!r} names a path that escapes the permitted root"
    assert "id_ed25519" not in str(excinfo.value), "the author-written value stays unechoed"
    assert "id_ed25519" not in excinfo.value.remedy, "nor in the remedy"
    _assert_names_no_file_to_delete(excinfo.value.remedy)
    assert excinfo.value.remedy == (
        f"Either the path that {relative!r} names is written wrong -- correct it so it "
        f"stays inside the project (the examples in docs/protocol/migrations.md show the "
        f"normal `../knowledge/...` form) -- or something it traverses is a symbolic link "
        f"that leaves the project; find and fix that link."
    ), (
        "both candidates, or the commonest case has no cure: a plain over-traversal typo "
        "(`../../../../../../etc/x`) involves no symlink at all, and an earlier version "
        "told the author that correcting the path was not the fix"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_an_escaping_entrys_control_characters_do_not_reach_output_raw(
    project: Path, tmp_path: Path
) -> None:
    """The `!r` quoting is load-bearing, and a mutation
    replacing ``{entry.name!r}`` with ``'{entry.name}'`` survived the suite.

    A filename may hold anything but ``/`` and NUL, so an ANSI escape and a
    newline both fit -- and this string is printed to a terminal by the CLI's
    human renderer and embedded in a JSON payload an agent reads. The raw ESC
    would repaint the terminal; the raw newline would let a crafted filename
    forge what looks like a second line of Theurian's own output.

    The expectation is built from literal escaped bytes rather than from
    ``!r``, so a test computing its expectation the same way the code computes
    its output cannot agree with a broken implementation.
    """
    outside_body = tmp_path / "target"
    outside_body.write_text("x\n")
    hostile = "01K1HOSTILE01234567890ABC-\x1b[31mRED\x1b[0m\nfake.yaml"
    entry = project / ".theurian" / "migrations" / hostile
    entry.symlink_to(outside_body)

    with pytest.raises(PathEscapeError) as excinfo:
        load_migrations(project, project / ".theurian" / "migrations", real_schema_root())

    for rendered in (str(excinfo.value), excinfo.value.remedy):
        assert "\x1b" not in rendered, "a raw ESC byte reaches the terminal and repaints it"
        assert "\n" not in rendered, "a raw newline lets a filename forge a line of output"
        assert "\\x1b[31mRED\\x1b[0m" in rendered, "the escape survives as inert text"
        assert "\\nfake.yaml" in rendered, "so does the newline"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_follows_a_migrations_directory_symlink_to_a_valid_in_project_directory(
    project: Path,
) -> None:
    """GREEN pin (round four): the directory-level counterpart of
    `test_load_migrations_follows_a_symlink_entry_to_a_valid_in_project_file`
    above -- the new dangling/outside-pointing refusals this section adds
    must not turn *every* `migrations_dir` symlink into a refusal, only the
    two broken shapes. A `migrations_dir` that is a symlink to a real,
    in-project directory holding real migrations must still load normally.
    Verified to pass today, before either RED fix in this section exists:
    this guards a future directory-level symlink check from overreaching
    into refusing this legitimate shape too.
    """
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.rmdir()
    real_target = project / ".theurian" / "real-migrations"
    real_target.mkdir()
    (real_target / "01K1KKKKKK01234567890ABCDE-real.yaml").write_text(_VALID_MIGRATION)
    migrations_dir.symlink_to(real_target)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    ids = {str(m.migration_id) for m in loaded.migration_set}
    assert ids == {"01K1KKKKKK01234567890ABCDE"}


# -- round three: entry-level enumeration policy -----------------------------
#
# The symlink-loop test above drives `migrations_dir` *itself* being a loop.
# This section drives a loop, or a dangling target, on one *entry* inside an
# otherwise-healthy `migrations_dir` -- a different fault, because entry-level
# enumeration goes through `p.is_file()` in the comprehension, not the
# top-of-function `os.stat` probe.
#
# Orchestrator-measured today, before any of the fixes this section's RED
# tests specify exist: with two `*.yaml` entries on disk -- one real, one a
# 40-link symlink loop -- `load_migrations` reports a `migrationCount` of 1,
# silently dropping the loop entry, because `Path.is_file()` swallows `ELOOP`
# (and `ENOENT`) internally (CPython's `pathlib._abc._ignore_error`,
# `_IGNORED_ERRNOS = (ENOENT, ENOTDIR, EBADF, ELOOP)`) and simply reports
# `False`, filtering the entry out of the enumerated set with no error at
# all. A dangling symlink entry (a valid target name, but nothing at the far
# end) vanishes the identical way, via `ENOENT` instead of `ELOOP`.
#
# WARNING carried from the adversarial review: a naive fix that swaps
# `p.is_file()` for a bare `p.stat()` inside this same comprehension, with no
# per-entry `try`, would make a *dangling* entry's `ENOENT` propagate out of
# the comprehension and into the directory-level `except OSError` around it
# (`load_migrations`, `migration_loader.py`), which already answers `ENOENT`
# with `LoadedMigrations.empty()` for the *whole* directory -- turning "one
# entry is broken" into "nothing loaded at all", a worse regression than
# today's silent per-entry drop. The green pins below (skip only the raced
# entry; still load a symlink to a valid target) exist to catch exactly that
# trap once a per-entry classification lands.


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_raises_migration_file_unreadable_error_for_a_symlink_loop_entry(
    project: Path,
) -> None:
    """RED (round three): entry-level counterpart of
    ``test_load_migrations_raises_migrations_directory_unreadable_error_for_a_symlink_loop``
    above -- here the loop is one *entry* inside `migrations_dir`, not the
    directory itself. Today this is silently dropped (see this section's own
    banner comment, measured directly); the specified fix is to raise
    :class:`MigrationFileUnreadableError` naming the entry, not the directory,
    with a remedy that says "loop", not "confirm it still exists" -- the
    generic ``_read_failure_remedy`` fallback every other
    :class:`MigrationFileUnreadableError` case in this file gets today has no
    branch for ``ELOOP`` at all, so this stays red until one is added.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-real.yaml").write_text(_VALID_MIGRATION)
    theurian_dir = project / ".theurian"
    links = [theurian_dir / f"entry-loop-{i}" for i in range(40)]
    loop_entry = migrations_dir / "01K1LLLLLL01234567890ABCDE-loop.yaml"
    loop_entry.symlink_to(links[0])
    for index in range(len(links) - 1):
        links[index].symlink_to(links[index + 1])
    links[-1].symlink_to(links[0])

    with pytest.raises(OSError) as os_excinfo:
        loop_entry.stat()
    assert os_excinfo.value.errno == errno.ELOOP, "fixture must actually reproduce ELOOP"

    with pytest.raises(MigrationFileUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(loop_entry.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be read: {os.strerror(errno.ELOOP)}"
    assert excinfo.value.remedy == (
        f"{relative!r} is a loop of symbolic links. Point it at a real file, then retry."
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_raises_migration_file_unreadable_error_for_a_dangling_symlink_entry(
    project: Path,
) -> None:
    """RED (round three): the entry-level sibling of the loop-entry test
    above -- a symlink whose target simply does not exist, rather than one
    that loops. Today this also vanishes silently (``Path.is_file()``
    swallows ``ENOENT`` the identical way it swallows ``ELOOP``); the
    specified fix distinguishes it from an *ordinary* missing file (a
    genuinely absent entry is not an entry at all -- there is nothing to
    enumerate) by checking symlink-ness first: this entry *is* a symlink
    (``lstat`` succeeds), and only its target is missing, so the reason and
    remedy name "symbolic link" rather than the generic "confirm this exists"
    every other missing-file case gets. This changes today's silent skip, so
    it stays red until that per-entry classification exists.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-real.yaml").write_text(_VALID_MIGRATION)
    dangling = migrations_dir / "01K1DDDDDD01234567890ABCDE-dangling.yaml"
    dangling.symlink_to(migrations_dir / "01K1DDDDDD01234567890ABCDE-does-not-exist.yaml")

    with pytest.raises(OSError) as os_excinfo:
        dangling.stat()
    assert os_excinfo.value.errno == errno.ENOENT, "fixture must actually reproduce ENOENT"
    assert dangling.is_symlink(), "fixture must actually be a symlink, not a missing plain file"

    with pytest.raises(MigrationFileUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(dangling.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be read: symbolic link target is missing"
    assert excinfo.value.remedy == (
        f"{relative!r} is a symbolic link whose target is missing. Restore the target or "
        f"remove the link, then retry."
    )


@pytest.mark.parametrize("vanish_errno", [errno.ENOENT, errno.ENOTDIR], ids=["ENOENT", "ENOTDIR"])
def test_load_migrations_skips_only_a_non_symlink_entry_that_vanishes_mid_enumeration(
    project: Path, monkeypatch: pytest.MonkeyPatch, vanish_errno: int
) -> None:
    """GREEN pin (round three; parametrized round four -- mutation b3
    SURVIVED): the regression this section's banner comment warns a naive
    per-entry fix could introduce -- one entry's ``Path.stat()`` raising
    ``ENOENT``/``ENOTDIR`` (a plain file removed, or replaced by something
    whose parent segment is no longer a directory, between ``iterdir()``
    listing it and the per-entry check -- not a symlink) must skip only that
    entry, never the whole directory. Driven by patching ``Path.stat`` itself,
    one layer below ``is_file()``: today's ``is_file()`` already swallows this
    internally and this test proves that fact stays true, so a future
    per-entry ``try`` around an explicit ``stat()`` call has this pin to
    fail against if it forgets to catch ``ENOENT``/``ENOTDIR`` per entry
    rather than letting it propagate to the directory-level ``except``
    around the whole enumeration (`load_migrations`, `migration_loader.py`),
    which would turn this one raced-away file into ``LoadedMigrations.empty()``
    for everything.

    Only ``ENOENT`` was driven before round four: the classification branch
    this pins reads ``if exc.errno in (errno.ENOENT, errno.ENOTDIR): return
    False`` (``migration_loader.py``), and a mutation dropping ``errno.ENOTDIR``
    from that tuple entirely still passed every test in this file, because
    none of them injected it -- the identical shape of survivor the sibling
    ``ENAMETOOLONG``/``EPERM`` residual-errno tests below already close for
    the *directory*-level enumeration ``except``.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-real.yaml").write_text(_VALID_MIGRATION)
    vanished_id = "01K1VVVVVV01234567890ABCDE"
    vanished = migrations_dir / f"{vanished_id}-vanished.yaml"
    vanished.write_text(
        _VALID_MIGRATION.replace("01K1KKKKKK01234567890ABCDE", vanished_id).replace(
            "architecture.auth-policy", "architecture.vanished-policy"
        )
    )

    real_stat = Path.stat

    def _fake_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self == vanished:
            raise OSError(vanish_errno, os.strerror(vanish_errno))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    ids = {str(m.migration_id) for m in loaded.migration_set}
    assert ids == {"01K1KKKKKK01234567890ABCDE"}


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo is POSIX-only")
def test_load_migrations_skips_a_fifo_and_a_directory_both_named_dot_yaml(
    project: Path,
) -> None:
    """GREEN pin (round three), item 3 of the entry-level policy: an entry
    that stats without error but is not a regular file -- a FIFO, or a
    subdirectory that happens to be named ``*.yaml`` -- is skipped exactly as
    it was before this round, not classified as any kind of failure. Neither
    is a symlink and neither has a broken stat, so this is the "ordinary,
    working as designed" branch the new symlink-loop/dangling handling above
    must not touch.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-real.yaml").write_text(_VALID_MIGRATION)
    os.mkfifo(migrations_dir / "a-fifo.yaml")
    (migrations_dir / "a-directory.yaml").mkdir()

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    ids = {str(m.migration_id) for m in loaded.migration_set}
    assert ids == {"01K1KKKKKK01234567890ABCDE"}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_follows_a_symlink_entry_to_a_valid_in_project_file(
    project: Path,
) -> None:
    """GREEN pin (round three): a symlink entry whose target exists, is a
    regular file, and stays inside ``project_root`` must still load like any
    other migration -- the new loop/dangling handling above must not turn
    *every* symlink entry into a refusal, only the two broken shapes. The
    target lives outside `migrations_dir` (directly under `.theurian/`) so
    this is not merely "a symlink next to its own target", proving the read
    genuinely follows the link rather than coincidentally finding the same
    bytes.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-real.yaml").write_text(_VALID_MIGRATION)

    linked_id = "01K1SSSSSS01234567890ABCDE"
    target = project / ".theurian" / f"{linked_id}-external-source.yaml"
    target.write_text(
        _VALID_MIGRATION.replace("01K1KKKKKK01234567890ABCDE", linked_id).replace(
            "architecture.auth-policy", "architecture.linked-policy"
        )
    )
    linked_entry = migrations_dir / f"{linked_id}-linked.yaml"
    linked_entry.symlink_to(target)

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    ids = {str(m.migration_id) for m in loaded.migration_set}
    assert ids == {"01K1KKKKKK01234567890ABCDE", linked_id}


# -- errno policy for a failure during enumeration, not the is_dir() probe ---


@pytest.mark.parametrize("stale_errno", [errno.ENOENT, errno.ENOTDIR], ids=["ENOENT", "ENOTDIR"])
def test_load_migrations_treats_a_stale_enumeration_failure_as_an_empty_set(
    project: Path, monkeypatch: pytest.MonkeyPatch, stale_errno: int
) -> None:
    """Round-two errno policy: ``ENOENT``/``ENOTDIR`` raised while
    *enumerating* ``migrations_dir`` -- after the directory probe above
    (``migrations_dir.stat()``, ``migration_loader.py``) already found it and
    answered "yes, this is a directory" -- means the directory vanished or
    was replaced between the probe and the listing, the identical race that
    probe's own ``except OSError`` clause already treats as "nothing to
    load" for ``ENOENT``/``ENOTDIR`` (``if exc.errno in (errno.ENOENT,
    errno.ENOTDIR): return LoadedMigrations.empty()``, the same file). Before
    this fix, every enumeration-time ``OSError`` converted to
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


def test_load_migrations_gives_the_permission_remedy_for_an_injected_eperm(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-three mutation-adversarial (mutation x4 SURVIVED): the sibling
    of the test above, but for the other half of
    ``_directory_unreadable_remedy``'s own permission tuple
    (``domain/errors.py``): ``if errno_value in (_errno.EACCES,
    _errno.EPERM):``. Every existing permission-refusal test in this file
    drives its failure with a real ``chmod``, which raises ``EACCES`` on this
    platform, never ``EPERM`` -- so a mutation dropping ``_errno.EPERM`` from
    that tuple entirely survived every test here despite the tuple's own
    reference to it. ``EPERM`` is injected the same way
    ``ENAMETOOLONG`` is above, since it is not reliably producible with a
    real ``chmod`` on every platform this suite runs on.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)

    real_iterdir = Path.iterdir

    def _fake_iterdir(self: Path) -> Iterator[Path]:
        if self == migrations_dir:
            raise OSError(errno.EPERM, os.strerror(errno.EPERM))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _fake_iterdir)

    with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(migrations_dir.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be listed: {os.strerror(errno.EPERM)}"
    assert excinfo.value.remedy == (
        f"Confirm this user has read and execute permission on {relative!r} "
        f"and its parent directories, then retry."
    )


def test_load_migrations_refuses_a_non_symlink_entry_racing_its_own_follow_stat(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-four mutation-adversarial (mutation n06 SURVIVED):
    ``_entry_is_migration_file``'s (``migration_loader.py``) bare ``raise`` --
    reached only when ``is_symlink`` is ``False`` and the follow-stat's errno
    is neither ``ENOENT`` nor ``ENOTDIR`` -- has no test that reaches it
    specifically. The existing ``chmod 0o444`` directory-level test
    (``test_load_migrations_raises_migrations_directory_unreadable_error_when_
    entries_are_unstattable`` above) does **not** drive this line: measured
    directly, a ``migrations_dir`` denying traversal makes ``entry.is_symlink()``
    itself -- an ``lstat``, called *unguarded* one line before this function's
    own ``try`` block -- raise ``EACCES`` first, so the bare ``raise`` this
    test targets is never reached on that path at all. The only way to reach
    it is the race the function's own docstring names: a *non*-symlink
    entry whose ``lstat`` (``is_symlink()``) succeeds, but whose separate
    follow-stat then fails with something other than ``ENOENT``/``ENOTDIR`` --
    a permission bit changing, or the entry being replaced by something
    unstattable, between the two calls.

    Driven with a ``Path.stat`` patch that answers ``follow_symlinks=False``
    (``is_symlink()``'s own call) with the real result, and only
    ``follow_symlinks=True`` (the explicit ``entry.stat()`` a few lines later
    in the same function) with an injected ``EACCES`` -- reproducing exactly
    that race without needing two real, differently-permissioned syscalls
    against the same path. Verified directly against this round's own
    mutation: replacing the bare ``raise`` with ``return False`` makes this
    entry silently skipped (``load_migrations`` returns an empty set, no
    exception) instead of refusing -- confirmed by reverting the fix in a
    scratch run and observing this assertion fail, then restoring it.
    """
    migrations_dir = project / ".theurian" / "migrations"
    target = migrations_dir / "01K1RRRRRR01234567890ABCDE-racy.yaml"
    target.write_text(_VALID_MIGRATION)

    real_stat = Path.stat

    def _fake_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self == target and follow_symlinks:
            raise OSError(errno.EACCES, os.strerror(errno.EACCES))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    with pytest.raises(MigrationsDirectoryUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    relative = str(migrations_dir.relative_to(project))
    assert str(excinfo.value) == f"{relative!r} could not be listed: {os.strerror(errno.EACCES)}"
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
    does not exist at all (``if exc.errno in (errno.ENOENT, errno.ENOTDIR):
    return LoadedMigrations.empty()`` inside the directory probe's own
    ``except OSError``, ``migration_loader.py``). A fix that makes *any*
    zero-migration read raise would pass every test above it in this file and
    still be wrong.
    """
    migrations_dir = project / ".theurian" / "migrations"

    loaded = load_migrations(project, migrations_dir, real_schema_root())

    assert loaded == LoadedMigrations.empty()


def test_load_migrations_treats_a_migrations_path_that_is_a_regular_file_as_an_empty_set(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-three pin for the ``stat.S_ISDIR``-false branch
    (``migration_loader.py``): nothing in this file previously drove
    ``migrations_dir`` existing as something other than a directory *and*
    other than genuinely absent -- the probe's own ``except OSError`` above
    only covers ``ENOENT``/``ENOTDIR`` (nothing there at all, or a parent
    segment that is not a directory). A path that *does* exist, *is*
    readable, and simply is not a directory -- a plain file sitting where
    ``.theurian/migrations`` should be -- reaches ``if not
    stat.S_ISDIR(probe.st_mode): return LoadedMigrations.empty()`` instead.

    Self-verified (round three): inverting that condition (``if
    stat.S_ISDIR(...)``) is *not* caught by a result-only assertion, and was
    measured to survive one -- a regular file also makes the enumeration
    step's own ``migrations_dir.iterdir()`` raise ``NotADirectoryError``
    (``ENOTDIR``), which the very next ``except OSError`` clause answers with
    the identical ``LoadedMigrations.empty()``. Two different branches
    agreeing on the same output is exactly the "equivalent only by luck of
    the enumeration fallback" trap round two's adversarial finding named, so
    a same-answer assertion cannot tell them apart. This instead proves the
    ``S_ISDIR`` branch itself short-circuits: it fails outright if
    ``Path.iterdir`` is ever called on ``migrations_dir``, which the
    enumeration fallback would necessarily do and the direct branch never
    does.
    """
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.rmdir()
    migrations_dir.write_text("not a directory\n")

    real_iterdir = Path.iterdir

    def _iterdir_must_not_run_for_a_non_directory(self: Path) -> Iterator[Path]:
        if self == migrations_dir:
            pytest.fail(
                "enumeration must not run at all for a migrations_dir that is "
                "not a directory -- the S_ISDIR check must short-circuit first"
            )
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _iterdir_must_not_run_for_a_non_directory)

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
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code-review MEDIUM (round two): a mutation collapsing the
    enumeration's own ``sorted(...)`` to ``list(...)`` (``migration_loader.py``)
    survived round one's suite -- nothing pinned *which* of several invalid
    files a multi-file failure names, only that some ``MigrationError`` was
    raised, so an unstable ``iterdir()`` order would have passed unnoticed.

    Reworked (round three, code-review MEDIUM): the previous version of this
    test relied on filesystem creation order disagreeing with lexicographic
    order (``z-...`` written first, ``a-...`` second) to prove ``sorted(...)``
    actually runs -- true on APFS, which walks ``iterdir()`` in creation
    order, but not a guarantee this suite can rely on: ext4 returns hash
    order, under which a mutation dropping ``sorted`` could coincidentally
    still report the files in the "right" order and pass. This instead
    injects ``Path.iterdir``'s raw yield order directly, forced to
    reverse-lexicographic regardless of what the real filesystem does (the
    same ``_fake_iterdir`` pattern the errno-policy tests above use), so the
    pin holds on every filesystem, not only this developer's own.

    The loop in ``load_migrations`` that calls ``_load_one`` once per
    *sorted* path raises on the first invalid file it reaches, so pinning
    *which* file the error names pins the sort itself, not merely that a
    sort happened.
    """
    migrations_dir = project / ".theurian" / "migrations"
    malformed = "id: [unclosed\n  bad: {{{\n"
    first = migrations_dir / "a-01K1AAAAAA01234567890ABCDE-first.yaml"
    second = migrations_dir / "z-01K1ZZZZZZ01234567890ABCDE-second.yaml"
    first.write_text(malformed)
    second.write_text(malformed)

    real_iterdir = Path.iterdir

    def _reverse_lexicographic_iterdir(self: Path) -> Iterator[Path]:
        if self == migrations_dir:
            return iter(sorted(real_iterdir(self), reverse=True))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _reverse_lexicographic_iterdir)

    observed_names = [p.name for p in migrations_dir.iterdir()]
    assert observed_names == [second.name, first.name], (
        "fixture must actually yield reverse-lexicographic order"
    )

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert "a-01K1AAAAAA01234567890ABCDE-first.yaml" in str(excinfo.value)
    assert "z-01K1ZZZZZZ01234567890ABCDE-second.yaml" not in str(excinfo.value)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_load_migrations_names_the_lexicographically_first_entry_when_classification_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED (round four, orchestrator-measured): the sibling gap the test
    above does not close. That test proves ``sorted(...)`` runs at all; this
    one proves *when* it runs relative to per-entry classification.

    Today's enumeration is ``sorted(p for p in migrations_dir.iterdir() if
    p.name.endswith(".yaml") and _entry_is_migration_file(p, project_root))``
    (``migration_loader.py``) -- a generator expression, consumed by
    ``sorted()`` lazily in whatever order ``iterdir()`` yields, with
    ``_entry_is_migration_file`` (round three's per-entry classification,
    which can itself raise :class:`MigrationFileUnreadableError` for a
    dangling or looping entry) called *inline*, before the collected names
    are ever sorted. A multi-failure raise therefore names whichever failing
    entry ``iterdir()`` happens to yield first on this filesystem -- APFS and
    ext4 disagree (APFS is measured here to walk in creation order; ext4's
    documented ``dir_index`` hashing walks in hash order, not measured on
    this non-Linux machine) -- not the lexicographically-first one. The new
    contract: names are sorted *before* classification runs, so the refusal is the same
    regardless of physical enumeration order.

    Measured directly with two failing entries, injected in REVERSED order
    (the loop entry, lexicographically last, yielded first) so today's
    generator-order bug is driven regardless of what this developer's own
    filesystem happens to do: today this reports the ``z...`` loop entry, not
    the ``a...`` dangling one.
    """
    migrations_dir = project / ".theurian" / "migrations"
    theurian_dir = project / ".theurian"

    dangling = migrations_dir / "a-01K1DDDDDD01234567890ABCDE-dangling.yaml"
    dangling.symlink_to(migrations_dir / "does-not-exist.yaml")

    links = [theurian_dir / f"ordering-loop-{i}" for i in range(40)]
    loop_entry = migrations_dir / "z-01K1LLLLLL01234567890ABCDE-loop.yaml"
    loop_entry.symlink_to(links[0])
    for index in range(len(links) - 1):
        links[index].symlink_to(links[index + 1])
    links[-1].symlink_to(links[0])

    with pytest.raises(OSError) as loop_excinfo:
        loop_entry.stat()
    assert loop_excinfo.value.errno == errno.ELOOP, "fixture must actually reproduce ELOOP"
    with pytest.raises(OSError) as dangling_excinfo:
        dangling.stat()
    assert dangling_excinfo.value.errno == errno.ENOENT, "fixture must actually reproduce ENOENT"

    real_iterdir = Path.iterdir

    def _reversed_iterdir(self: Path) -> Iterator[Path]:
        if self == migrations_dir:
            return iter([loop_entry, dangling])
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _reversed_iterdir)

    with pytest.raises(MigrationFileUnreadableError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert dangling.name in str(excinfo.value), (
        "the lexicographically-first failing entry must be named, "
        "not whichever iterdir() happened to yield first"
    )
    assert loop_entry.name not in str(excinfo.value)


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

    Strengthened (round three, mutation x2 SURVIVED): the three
    `json.JSONDecodeError`/`UnicodeDecodeError`/`AttributeError` `except`
    clauses in `_validator` (`migration_loader.py`) each build `reason` from
    `str(exc)` and pass it straight through -- a mutation collapsing every
    one of those `str(exc)` calls to the same hardcoded constant survived
    every schema test in this file, because none of them checked *which*
    message came back, only that `.remedy` was truthy. A shape-specific
    fragment of the underlying exception's own wording -- not the exact
    string, which is CPython's to change -- is what a wholesale-replacement
    mutation cannot satisfy for more than one of these four cases at once.

    This case also pins `SchemaUnreadableError.remedy` exactly (mutation x1,
    SURVIVED: replacing the whole `f"Reinstall theurian; ..."` template with
    a constant also passed every `assert excinfo.value.remedy` truthy check
    here). The expected reason is derived from calling `json.loads` on the
    identical text, not hardcoded, so this does not pin `json`'s own wording
    across Python versions -- only that `_validator` forwards it unchanged.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text('{"type": "obj')

    try:
        json.loads('{"type": "obj')
        pytest.fail("fixture must actually reproduce json.JSONDecodeError")
    except json.JSONDecodeError as json_exc:
        expected_reason = str(json_exc)
    assert "Unterminated string" in expected_reason, "fixture must exercise this specific shape"

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert "Unterminated string" in str(excinfo.value)
    assert excinfo.value.remedy == (
        f"Reinstall theurian; {str(schema_file)!r} could not be read ({expected_reason})."
    )


def test_validator_raises_schema_unreadable_error_for_an_empty_file(tmp_path: Path) -> None:
    """Measured: an empty file also raises `json.JSONDecodeError` --
    `Expecting value` rather than truncation's `Unterminated string`, a
    distinct message from the same exception type, pinned as its own case so
    a fix keyed on one message does not leave the other uncaught.

    Strengthened (round three): see the identical `str(exc)`-collapsing
    mutation this guards against on the truncated-JSON test above.
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

    assert "Expecting value" in str(excinfo.value)


def test_validator_raises_schema_unreadable_error_for_non_utf8_bytes(tmp_path: Path) -> None:
    """Measured: bytes that are not valid UTF-8 raise `UnicodeDecodeError` at
    the same `read_text(encoding="utf-8")` call the `OSError` guard already
    wraps -- a failure *inside* the guarded call, not a different call the
    guard never reaches.

    Strengthened (round three): see the identical `str(exc)`-collapsing
    mutation this guards against on the truncated-JSON test above.
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

    assert "can't decode" in str(excinfo.value)


def test_validator_raises_schema_unreadable_error_for_a_json_list_schema(tmp_path: Path) -> None:
    """RED (round three): a schema that parses but is a JSON *list* rather
    than an object is refused with a reason naming *what* is wrong -- "not an
    object" -- under the new semantic-hardening contract (round three), the
    same reason a bare JSON boolean gets
    (:func:`test_validator_raises_schema_unreadable_error_for_a_non_object_schema`
    below), because both are "the parsed value is not a JSON object", one
    check, not two.

    Before this round: a JSON list raised `AttributeError` one line later, at
    `Draft202012Validator(schema)` construction -- `jsonschema` calls
    `schema.get(...)` internally, and a `list` has no `.get` -- and
    `_validator`'s `except AttributeError` clause forwarded that raw message
    (`"'list' object has no attribute 'get'"`) as the reason. This is a
    behaviour change, not a strengthening of an existing pin: today's message
    does not contain "not an object", so this stays red until `_validator`
    checks `isinstance(schema, dict)` before constructing the validator at
    all.
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

    assert "not an object" in str(excinfo.value)


# -- SchemaUnreadableError: round three's semantic hardening -----------------
#
# The four tests above all cover a read that *fails to parse*. These two
# cover a read that parses cleanly to JSON but is not usable as a JSON
# Schema at all -- a case `_validator` does not guard today, so both are RED
# until it does. `{}` is deliberately not tested as refused anywhere in this
# file: it is a valid, if vacuous, schema (it matches every instance), and a
# fix that refused it would be wrong, not merely early.


def test_validator_raises_schema_unreadable_error_for_a_non_object_schema(
    tmp_path: Path,
) -> None:
    """RED (round three): JSON Schema's draft 2020-12 permits a bare boolean
    as a top-level schema (`true` matches everything, `false` matches
    nothing) -- so `json.loads("true")` succeeds, and
    `Draft202012Validator(True)` constructs without error today. That is
    worse than a crash: an installed schema corrupted to `true` would make
    every migration in every project validate, silently, because the
    resulting validator accepts anything. `_validator` needs an explicit
    `isinstance(schema, dict)` check before it ever reaches
    `Draft202012Validator` for this to be refused as install corruption
    rather than accepted as "no rules". Measured directly: today this
    `pytest.raises` block does not raise at all.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("true")

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert "not an object" in str(excinfo.value)


def test_validator_raises_schema_unreadable_error_for_structurally_invalid_schema_keywords(
    tmp_path: Path,
) -> None:
    """RED (round three): a schema whose own keywords are malformed --
    `required` must be an array of strings per the JSON Schema metaschema,
    and this one is a bare string -- constructs without error today
    (`Draft202012Validator.__init__` does not call `check_schema` on its own
    input), so the failure does not surface until a *migration* is validated
    against it. Measured directly: `Draft202012Validator({"type": "object",
    "required": "not-a-list"}).validate({"apiVersion": "x"})` raises
    `jsonschema.exceptions.ValidationError: 'n' is a required property` --
    `required`'s own keyword implementation iterates a string character by
    character, and "n" is `"not-a-list"[0]` -- which `_load_one`'s existing
    `except ValidationError` clause (`migration_loader.py`) happily converts
    to `MigrationError(f"{path.name} is invalid at <root>: 'n' is a required
    property")`: a perfectly schema-valid migration blamed for a fault in
    the *schema*. `_validator` needs to call `Draft202012Validator.
    check_schema(schema)` itself and translate `jsonschema.exceptions.
    SchemaError` to `SchemaUnreadableError`, so this is caught at load time,
    attributed to the installation, before any migration is ever checked
    against it.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text(json.dumps({"type": "object", "required": "not-a-list"}))

    try:
        with pytest.raises(SchemaUnreadableError):
            _validator(schema_dir)
    finally:
        _validator.cache_clear()


def test_validator_raises_schema_unreadable_error_for_a_schema_nested_past_the_recursion_limit(
    tmp_path: Path,
) -> None:
    """RED (round four, adversarial HIGH -- a regression this round's own
    `check_schema` call introduced): `Draft202012Validator.check_schema`, the
    call the structurally-invalid-keywords test immediately above added to
    `_validator` (`migration_loader.py`) this same round, recurses into a
    schema's own nested keywords -- and a schema deep enough blows Python's
    recursion limit the identical way an attacker-controlled *migration*
    document already does (`test_load_migrations_raises_migration_error_for_a_
    migration_nested_past_the_recursion_limit` above,
    `security/yaml_loading.py`'s `RecursionError` -> `ValueError`
    translation). `_validator`'s own three `except` clauses around the read
    (`OSError`, `UnicodeDecodeError`, `json.JSONDecodeError`) do not name
    `RecursionError`, and neither does the new `except SchemaError` this
    round wraps around `check_schema` -- `RecursionError` is a
    `RuntimeError` subclass, not a `jsonschema.exceptions.SchemaError` --
    so it escapes `_validator` raw. Measured directly: 400 levels of
    `{"not": {"not": ... {"type": "string"}}}}` reproduces a bare
    `RecursionError: maximum recursion depth exceeded` from
    `Draft202012Validator.check_schema` today, not `SchemaUnreadableError`.

    The installed schema is fixed content shipped with the package, not
    attacker-supplied migration content -- but `_validator`'s own docstring
    already treats "the read succeeds, the content is corrupt" as one class
    regardless of *how* it is corrupt, and a `RecursionError` escaping here
    crashes every `--json` command that resolves a project (`resolve_context`
    reaches `_validator` through `load_migrations`) with a raw traceback,
    the identical CP-2 escape every other member of this class was closed
    for.

    The reason is now pinned exactly, not merely the exception type: `_validator`
    has two separate `except RecursionError` clauses -- this one around
    `check_schema`, and a second around the earlier `json.loads` call
    (:func:`test_validator_raises_schema_unreadable_error_for_json_nested_past_the_recursion_limit`
    below) -- each with its own reason text, and a bare
    `pytest.raises(SchemaUnreadableError)` cannot tell them apart: deleting
    either clause on its own leaves *this* test green as long as the other
    still raises the same type, so the exact string is what actually pins
    which branch fired. 400 levels is confirmed below to still let
    `json.loads` itself succeed, so this genuinely drives `check_schema`'s own
    recursion and not the earlier parse.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)

    depth = 400
    nested: dict[str, Any] = {"type": "string"}
    for _ in range(depth):
        nested = {"not": nested}
    text = json.dumps(nested)
    schema_file.write_text(text)

    try:
        json.loads(text)
    except RecursionError:
        pytest.fail("fixture must let json.loads succeed, so only check_schema recurses")

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert str(excinfo.value) == (
        f"{str(schema_file)!r} could not be read: "
        f"the schema nests past check_schema's safe recursion depth"
    )


def test_validator_raises_schema_unreadable_error_for_json_nested_past_the_recursion_limit(
    tmp_path: Path,
) -> None:
    """Mutation survivor: `_validator`'s *other* `RecursionError` clause --
    the one wrapping `json.loads(text)` itself, item 4's `json.loads` leg in
    `_validator`'s own docstring -- had no test reaching it specifically.
    Every existing recursion test in this file drives `check_schema`'s
    recursion (the sibling test immediately above, confirmed there to leave
    `json.loads` succeeding at that depth) or a *migration* document's own
    recursion one call site over
    (`test_load_migrations_raises_migration_error_for_a_migration_nested_past_the_recursion_limit`),
    never the schema file's own JSON parse. Deleting the `except
    RecursionError` clause around `json.loads` entirely left the whole suite
    green, because nothing here ever fed `_validator` a document whose
    *parse* -- not its `check_schema` walk -- exhausts the recursion limit.

    20,000 levels of bare `[`/`]` mirrors the depth
    `test_parsers.py::test_json_parser_names_the_source_uri_for_a_document_nested_past_the_recursion_limit`
    already uses to drive the identical `json.loads` `RecursionError` one
    layer down, well past the 400 levels that trip `check_schema` on this
    interpreter. A JSON array, not an object: `_validator`'s
    `isinstance(schema, dict)` check runs only after `json.loads` returns, so
    a document that never finishes parsing never reaches it -- this exercises
    the parse itself, not the object-type refusal item 3 covers.

    The reason is pinned exactly to this branch's own text ("the JSON
    document nests past the parser's safe recursion depth"), distinct from
    `check_schema`'s branch ("the schema nests past check_schema's safe
    recursion depth") the sibling test above now pins with the identical
    exactness -- the two `except RecursionError` clauses are otherwise
    indistinguishable by exception type alone, which is exactly how a
    mutation deleting either one survived.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)

    deep = "[" * 20_000 + "]" * 20_000
    schema_file.write_text(deep)

    try:
        json.loads(deep)
        pytest.fail("fixture must actually reproduce json.loads RecursionError")
    except RecursionError:
        pass

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            _validator(schema_dir)
    finally:
        _validator.cache_clear()

    assert str(excinfo.value) == (
        f"{str(schema_file)!r} could not be read: "
        f"the JSON document nests past the parser's safe recursion depth"
    )


def test_validator_accepts_the_vacuous_empty_object_schema(tmp_path: Path) -> None:
    """Guards the non-goal the RED tests above name explicitly: `{}` is
    metaschema-valid -- `Draft202012Validator.check_schema({})` raises
    nothing -- and matches every instance. It is not this project's "no
    schema available" fallback: no such fallback exists anywhere in this
    tree (`cli/context.py::schema_root` raises `ProjectError` when neither
    the packaged nor the source-checkout schema directory exists, never
    substitutes `{}`). This is a deliberately permissive schema in its own
    right, and the semantic hardening those RED tests specify must not turn
    it into a refusal -- a residual this project accepts and records in
    `CHANGELOG.md`.
    """
    _validator.cache_clear()
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("{}")

    try:
        validator = _validator(schema_dir)
        validator.validate({"anything": "goes"})
    finally:
        _validator.cache_clear()


# -- SchemaUnreadableError: an installed-schema $ref resolves offline or not at all (#235) --
#
# `jsonschema`'s default validator fetches an external `$ref` over the network
# (`urllib.request.urlopen`) at validate time, and reads a `file://` ref off
# disk the same way -- an SSRF-shaped read gated only on this installed schema
# being corrupted or replaced. `_validator` now builds every validator with
# `referencing`'s `EMPTY_REGISTRY` (no `retrieve`), so those refs fail closed,
# and `_validate_document` translates the resulting resolution failures --
# which are `Unresolvable`/`RecursionError`, not `ValidationError`, and used to
# escape as a raw traceback under `--json` -- to `SchemaUnreadableError`.
#
# Each test replaces the whole schema file, so it drives `_validator`'s
# `lru_cache`; the clear before and after is the same hygiene the corruption
# tests above use.


def _recording_handler(hits: list[str]) -> type[BaseHTTPRequestHandler]:
    """A handler that records every path it is asked for, so a test can assert
    it is asked for *nothing* -- the network-fetch this fix closes."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # BaseHTTPRequestHandler dispatches by this exact method name
            hits.append(self.path)
            body = b'{"type": "string"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Silence the default stderr access log for the test run."""

    return Handler


def _write_schema(tmp_path: Path, schema: object) -> Path:
    schema_dir = tmp_path / "schema"
    schema_file = schema_dir / "migrations" / "migration.schema.json"
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text(json.dumps(schema))
    return schema_dir


def test_validate_migration_document_never_fetches_an_external_http_ref(tmp_path: Path) -> None:
    """The reproduction from issue #235: a schema whose top-level `$ref` points
    at an HTTP server must not cause that server to be contacted.

    A real recording server stands in for the SSRF target. Before the fix the
    default registry fetched ``/evil.json`` (one recorded hit), retrieved
    ``{"type": "string"}``, and validated the document against it -- raising a
    ``MigrationError`` and, more to the point, having made the request. After
    the fix the registry has no ``retrieve``, so the lookup fails closed as a
    translated ``SchemaUnreadableError`` and the server is never touched. The
    ``hits == []`` assertion is the crisp closed-SSRF proof; ``pytest.raises``
    alone would pass on a fix that fetched and then merely reclassified.
    """
    hits: list[str] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _recording_handler(hits))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _validator.cache_clear()
        schema_dir = _write_schema(tmp_path, {"$ref": f"http://127.0.0.1:{port}/evil.json"})
        try:
            with pytest.raises(SchemaUnreadableError) as excinfo:
                validate_migration_document({"apiVersion": "x"}, schema_dir)
        finally:
            _validator.cache_clear()
        assert "could not be resolved offline" in str(excinfo.value)
        assert hits == [], "the installed schema's external $ref must never be fetched"
    finally:
        server.shutdown()
        thread.join()
        # `shutdown` stops `serve_forever`; the listening socket stays open
        # until `server_close`, and a leaked socket surfaces as a
        # `PytestUnraisableExceptionWarning` during a later test's GC.
        server.server_close()


def test_validate_migration_document_never_reads_a_file_ref(tmp_path: Path) -> None:
    """The ``file://`` face of the same seam: ``urlopen`` reads a local file for
    a ``file://`` ref just as it fetches an ``http://`` one.

    An ``open`` audit hook records any open of the ``file://`` target, and the
    test asserts the list stays empty -- the no-read proof is that assertion,
    not the error message. Before the fix the default registry opened and read
    the target to resolve the ref; after it the registry has no ``retrieve``,
    so the ref fails closed as ``SchemaUnreadableError`` and the file is never
    touched. The ``pytest.raises`` + offline-reason assertions are kept as the
    fail-closed teeth alongside it.
    """
    _validator.cache_clear()
    target = tmp_path / "secret-subschema.json"
    target.write_text(json.dumps({"type": "string"}))
    schema_dir = _write_schema(tmp_path, {"$ref": target.as_uri()})

    opens: list[str] = []

    def _record_open(event: str, args: tuple[object, ...]) -> None:
        # `open` fires for every file open in the process, and an audit hook
        # cannot be removed once added -- so match only this test's unique
        # target name and keep the body a single cheap comparison.
        if event == "open" and args and isinstance(args[0], str) and args[0].endswith(target.name):
            opens.append(args[0])

    sys.addaudithook(_record_open)

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            validate_migration_document({"apiVersion": "x"}, schema_dir)
    finally:
        _validator.cache_clear()

    assert "could not be resolved offline" in str(excinfo.value)
    assert opens == [], f"the file:// target must never be opened, but was: {opens}"


def test_validate_migration_document_translates_a_dangling_ref(tmp_path: Path) -> None:
    """A ``$ref`` to a fragment that does not exist raises
    ``referencing.exceptions.Unresolvable`` (wrapped as jsonschema's
    ``_WrappedReferencingError``) at validate time -- not a ``ValidationError``,
    so before the fix it slipped past the ``except ValidationError`` seam and
    reached ``resolve_context`` as a raw traceback under ``--json``. It is now
    a ``SchemaUnreadableError`` naming the installed schema.

    The reason text (``"could not be resolved offline"``) is distinct from the
    recursive-``$ref`` branch below (``"resolves recursively without
    terminating"``), so a mutation collapsing one branch into the other, or
    dropping ``except Unresolvable`` entirely, is caught here rather than left
    green by a bare ``pytest.raises(SchemaUnreadableError)``.
    """
    _validator.cache_clear()
    schema_dir = _write_schema(tmp_path, {"$ref": "#/$defs/does-not-exist"})

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            validate_migration_document({"apiVersion": "x"}, schema_dir)
    finally:
        _validator.cache_clear()

    assert "could not be resolved offline" in str(excinfo.value)


def test_validate_migration_document_translates_a_dangling_dynamic_ref(tmp_path: Path) -> None:
    """The ``$dynamicRef`` sibling of the dangling-``$ref`` case: a dynamic
    reference to an anchor nothing defines also raises ``Unresolvable`` at
    validate time, and must reach the same translated ``SchemaUnreadableError``
    rather than escape raw. Pinned separately because ``$dynamicRef`` takes a
    different resolution path inside ``jsonschema`` than a plain ``$ref``.
    """
    _validator.cache_clear()
    schema_dir = _write_schema(tmp_path, {"$dynamicRef": "#no-such-anchor"})

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            validate_migration_document({"apiVersion": "x"}, schema_dir)
    finally:
        _validator.cache_clear()

    assert "could not be resolved offline" in str(excinfo.value)


def test_validate_migration_document_translates_a_recursive_ref(tmp_path: Path) -> None:
    """A self-recursive ``$ref`` (``"#"`` -- the schema referring to itself with
    no other keyword to terminate on) raises ``RecursionError`` at validate
    time, not ``Unresolvable`` (``RecursionError`` is not an ``Unresolvable``
    subclass, confirmed against jsonschema 4.26.0), so it needs its own
    ``except`` clause. Before the fix it escaped every ``except ValidationError``
    seam as a raw traceback under ``--json``; it is now a
    ``SchemaUnreadableError`` whose reason names the recursion.
    """
    _validator.cache_clear()
    schema_dir = _write_schema(tmp_path, {"$ref": "#"})

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            validate_migration_document({"apiVersion": "x"}, schema_dir)
    finally:
        _validator.cache_clear()

    assert "resolves recursively without terminating" in str(excinfo.value)


def test_load_migrations_translates_a_dangling_ref_in_the_installed_schema(project: Path) -> None:
    """The ``load_migrations`` -> ``_load_one`` seam is the same class as
    ``validate_migration_document`` above: both validate against the installed
    schema, and both used to let a schema ``$ref`` failure escape their own
    ``except ValidationError`` clause raw. This drives the load path with a real
    ``*.yaml`` migration on disk so the corrupt schema is actually validated
    against a document, proving ``_load_one`` routes through ``_validate_document``
    too and does not reintroduce the raw-traceback escape one call site over.
    """
    _validator.cache_clear()
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / "01K1KKKKKK01234567890ABCDE-ok.yaml").write_text(_VALID_MIGRATION)
    schema_dir = _write_schema(project, {"$ref": "#/$defs/does-not-exist"})

    try:
        with pytest.raises(SchemaUnreadableError) as excinfo:
            load_migrations(project, migrations_dir, schema_dir)
    finally:
        _validator.cache_clear()

    assert "could not be resolved offline" in str(excinfo.value)


def test_validate_migration_document_still_resolves_internal_defs_in_the_real_schema() -> None:
    """The registry change must not break the bundled schema's own internal
    ``#/$defs/...`` refs (regression guard). A document that only validates if
    ``operations`` -> ``#/$defs/operation`` -> ``#/$defs/opCreateItem`` ->
    ``#/$defs/itemId`` all resolve passes cleanly, and a document whose
    ``itemId`` violates the ``#/$defs/itemId`` pattern raises ``MigrationError``
    -- which can only happen if that nested ``$ref`` chain was followed and
    enforced, offline, from the schema's own root resource.
    """
    valid = {
        "apiVersion": "theurian.dev/v1",
        "id": "01K1KKKKKK01234567890ABCDE",
        "createdAt": "2026-08-02T10:00:00+09:00",
        "author": "engineer@example.com",
        "operations": [
            {
                "op": "createItem",
                "itemId": "architecture.auth-policy",
                "kind": "architecture",
                "namespace": "backend",
                "owner": "platform-team",
            }
        ],
    }
    validate_migration_document(valid, real_schema_root())

    bad_item_id = json.loads(json.dumps(valid))
    bad_item_id["operations"][0]["itemId"] = "Not A Valid Item Id"
    with pytest.raises(MigrationError):
        validate_migration_document(bad_item_id, real_schema_root())


# -- issue #289: what a schema rejection is allowed to echo ------------------
#
# `jsonschema` builds `ValidationError.message` by interpolating the failing
# instance with `{instance!r}` -- measured against the installed 4.26.0, all
# twenty keyword interpolations use `!r`, and `_utils.extras_msg` reprs each
# extra property name. Both seams in `migration_loader.py` --
# `validate_migration_document`'s `except ValidationError` and `_load_one`'s --
# forward that `exc.message` verbatim into a `MigrationError`.
#
# Two properties of the resulting message belong to Theurian and are pinned
# here rather than left to a third party:
#
# 1. **Boundedness.** The echo is unbounded today: an author-controlled 100 KB
#    string value is rendered whole into the refusal a reader receives.
#    Measured on this branch at 100,198 characters for the document the first
#    test below builds.
# 2. **Control-character escaping.** `!r` already escapes an ESC byte and a
#    newline, so no raw-ANSI injection is demonstrable today -- which is
#    exactly why it is pinned: the guarantee currently lives in `jsonschema`
#    internals that no test of ours holds, and a fix that reconstructs the
#    message from `exc.absolute_path`/`exc.validator`/a truncated instance
#    must not lose it on the way.
#
# The "still names the failing location" tests are the counterweight: a bound
# and an escape must not be bought by deleting the diagnosis.

#: Ceiling on a schema-rejection message, in characters. Deliberately generous
#: -- today's real messages for these shapes run a little over 200 characters,
#: and what is being pinned is that the echo is bounded by something Theurian
#: chooses, not that it is short. A refusal lands in a terminal, so an
#: unbounded echo is a resource the document's author spends on its reader.
_MESSAGE_CHARACTER_CAP = 2000

#: An author-controlled value large enough that echoing it whole is the defect
#: rather than a matter of taste. `owner` carries it because the bundled
#: schema caps that field's length, so a single oversized value is the whole
#: reason the document is refused.
_OVERSIZED_VALUE = "A" * 100_000

#: A value carrying a raw ESC byte and a raw newline, long enough to break the
#: root `author` field's 320-character ceiling.
#:
#: `author` carries it rather than a field inside the operation, and that
#: choice is load-bearing: an operation is refused by `#/$defs/operation`'s
#: `oneOf`, whose instance is the whole operation *mapping*, and a mapping's
#: `str()` is its `repr()` -- so a mutation replacing `{instance!r}` with
#: `{instance}` there escapes the value anyway and survives. `author` fails
#: `maxLength` at the leaf, where the instance is the hostile string itself and
#: the two renderings genuinely differ. Measured: the mapping-shaped version of
#: this test passed with the mutation applied.
_CONTROL_CHARACTER_AUTHOR = "\x1b[31mFORGED\x1b[0m\nSecond line" + "x" * 400


def _document_with(
    *, author: str = "engineer@example.com", **operation_overrides: object
) -> dict[str, Any]:
    """A schema-valid migration document with one field overridden.

    Built valid first so each test below breaks exactly one field and knows
    which rejection it is driving -- a second, incidental violation would let a
    message assertion pass for the wrong reason.
    """
    operation: dict[str, Any] = {
        "op": "createItem",
        "itemId": "architecture.auth-policy",
        "kind": "architecture",
        "namespace": "backend",
        "owner": "platform-team",
    }
    operation.update(operation_overrides)
    return {
        "apiVersion": "theurian.dev/v1",
        "id": "01K1KKKKKK01234567890ABCDE",
        "createdAt": "2026-08-02T10:00:00+09:00",
        "author": author,
        "operations": [operation],
    }


def test_a_schema_rejection_does_not_echo_an_oversized_value_whole() -> None:
    """Issue #289: the failing instance reaches the reader unbounded.

    `validate_migration_document` forwards `jsonschema`'s `exc.message`, which
    has already interpolated the whole failing operation -- including a 100 KB
    author-written `owner` -- with `{instance!r}`. Measured on this branch:
    `len(str(exc))` is 100,198.

    The document is otherwise valid, so the oversized value is both the reason
    for the refusal and the entire excess in it.
    """
    document = _document_with(owner=_OVERSIZED_VALUE)

    with pytest.raises(MigrationError) as excinfo:
        validate_migration_document(document, real_schema_root())

    message = str(excinfo.value)
    assert _OVERSIZED_VALUE not in message, "the author-controlled value is echoed whole"
    assert len(message) <= _MESSAGE_CHARACTER_CAP, f"message is {len(message)} characters"


def test_a_schema_rejection_never_puts_a_raw_control_character_in_the_message() -> None:
    """Issue #289, the half that is GREEN today and pinned for that reason.

    `jsonschema` escapes an ESC byte and a newline only because every keyword
    interpolates with `{instance!r}` -- a property of a third-party
    implementation that this seam currently inherits by accident and no test of
    ours holds. Issue #289's own stated mechanism (raw ANSI reaching stderr)
    was measured false on this branch for exactly that reason.

    The fix moves the message construction to our side of the seam, and this
    test is what stops the escaping being lost in the move. A refusal from this
    seam is one line, so a raw newline from the instance would let an
    author-written value forge a second line of Theurian's own output.

    The third assertion is what keeps the first two from being satisfiable by
    echoing nothing: the offending value must still be *reported*, inert. Same
    shape as
    `test_an_escaping_entrys_control_characters_do_not_reach_output_raw`
    above, which exists because a mutation dropping one `!r` survived the suite.

    See `_CONTROL_CHARACTER_AUTHOR` for why the hostile value sits on `author`
    and not inside the operation -- a mapping-shaped instance escapes its
    members whichever rendering is used, and that version of this test survived
    the mutation it exists to catch.
    """
    document = _document_with(author=_CONTROL_CHARACTER_AUTHOR)

    with pytest.raises(MigrationError) as excinfo:
        validate_migration_document(document, real_schema_root())

    message = str(excinfo.value)
    assert "\x1b" not in message, "a raw ESC byte in a terminal is an injected escape sequence"
    assert "\n" not in message, "an echoed value must not add a line to Theurian's own output"
    assert "FORGED" in message, "the offending value is still reported, as inert text"


def test_a_schema_rejection_still_names_where_in_the_document_it_failed() -> None:
    """The counterweight to the bound and the escaping above.

    Both are trivially satisfiable by echoing nothing at all, and a refusal
    that says only "invalid" sends the reader back into the schema. The
    location path is what makes the refusal actionable, so it is asserted
    separately -- GREEN today, and the test that goes RED if the #289 fix pays
    for its bound with the diagnosis.
    """
    document = _document_with(owner=_OVERSIZED_VALUE)

    with pytest.raises(MigrationError) as excinfo:
        validate_migration_document(document, real_schema_root())

    assert "operations/0" in str(excinfo.value), "the reader needs the failing location"


#: The migration file the two loader-seam tests below drive. Named once so the
#: "still names the file" assertion and the fixture cannot drift apart.
_OVERSIZED_MIGRATION_FILENAME = "01K1KKKKKK01234567890ABCDE-oversized-owner.yaml"


def _write_oversized_owner_migration(project: Path) -> Path:
    """A hand-authored migration whose only fault is a 100 KB `owner`.

    100 KB is comfortably inside `load_yaml`'s own 4 MiB ceiling, so the
    document really does reach the schema rather than being refused for size
    one layer earlier.
    """
    migrations_dir = project / ".theurian" / "migrations"
    (migrations_dir / _OVERSIZED_MIGRATION_FILENAME).write_text(
        _VALID_MIGRATION.replace("owner: platform-team", f"owner: {_OVERSIZED_VALUE}")
    )
    return migrations_dir


def test_a_migration_file_rejected_by_the_schema_does_not_echo_an_oversized_value_whole(
    project: Path,
) -> None:
    """`_load_one`'s wrap is the second seam with the same defect (issue #289).

    It words the refusal differently -- with the file name -- but forwards the
    same unbounded `exc.message`, so a fix applied only to
    `validate_migration_document` would move the defect one call site over
    rather than close it. This is the seam a repository author actually
    reaches: the file is checked out and loaded, not passed in by a generator.
    """
    migrations_dir = _write_oversized_owner_migration(project)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    message = str(excinfo.value)
    assert _OVERSIZED_VALUE not in message, "the author-controlled value is echoed whole"
    assert len(message) <= _MESSAGE_CHARACTER_CAP, f"message is {len(message)} characters"


def test_a_migration_file_rejected_by_the_schema_still_names_the_file(project: Path) -> None:
    """The loader seam's own counterweight: with many files on disk, the name
    is what tells the reader which one to open, and bounding the message must
    not truncate it away. GREEN today; RED if a bound is applied from the
    front of the message rather than to the echoed value.
    """
    migrations_dir = _write_oversized_owner_migration(project)

    with pytest.raises(MigrationError) as excinfo:
        load_migrations(project, migrations_dir, real_schema_root())

    assert _OVERSIZED_MIGRATION_FILENAME in str(excinfo.value), "which file to open"


# -- issue #291: a deep document is a document fault, not a broken install ---
#
# `_validate_document`'s `except RecursionError` attributes any recursion at
# `validator.validate(document)` to a self-recursive schema `$ref` and raises
# `SchemaUnreadableError`, whose remedy is "reinstall theurian". A deeply
# nested *document* raises the identical `RecursionError` at that call, and the
# two are indistinguishable once `validate` is running -- so a user-input fault
# is diagnosed as a corrupt installation. `_validate_document`'s docstring
# already records this honestly and cites #291; the behaviour is the gap.
#
# Measured on this branch, 2026-08-21, CPython 3.13.3 (arm64 macOS),
# jsonschema 4.26.0. The recursion is raised by `_keywords.oneOf`'s own
# `f"{instance!r} is not valid under any of the given schemas"` -- so #289's
# unbounded echo is what recurses, and the traceback says so verbatim:
# "maximum recursion depth exceeded while getting the repr of an object".
#
# That budget is CPython's C recursion limit, not `sys.setrecursionlimit`:
# `repr` of a nested dict is unaffected by the Python limit (checked directly
# at limits 100 through 100,000), so the depth at which this fires is set by
# `Py_C_RECURSION_LIMIT` minus whatever the ambient call stack has already
# consumed. Hence two tests at two ambient depths.

#: A nesting depth past what this interpreter can render at *any* plausible
#: ambient stack depth. The premise test below is what keeps this honest: it
#: fails loudly if an interpreter ever renders it, which would mean these two
#: tests had quietly stopped exercising #291 at all.
_DEEPER_THAN_THE_INTERPRETER_CAN_RENDER = 20_000

#: Levels of the interpreter's C recursion budget the ambient-depth test burns
#: before it calls. The shape issue #291's own harness used, and the dimension
#: that made the same document produce different diagnoses there.
_AMBIENT_FRAMES = 1200


def _nested(depth: int) -> Any:
    """A ``{"a": {"a": ...}}`` chain ``depth`` levels deep, built iteratively so
    the *fixture* never depends on the recursion budget the test is about."""
    value: Any = "leaf"
    for _ in range(depth):
        value = {"a": value}
    return value


def _deeply_nested_document(depth: int) -> dict[str, Any]:
    """A migration document carrying the deep value where validation reaches it.

    It sits inside the single operation rather than at the document root: the
    root's `additionalProperties: false` refuses an unknown key by name alone,
    and `Draft202012Validator.validate` raises the *first* error `iter_errors`
    yields, so a root-level placement is answered before anything reprs the
    deep value at all. Inside the operation, the refusal is `#/$defs/operation`'s
    `oneOf`, whose message reprs the whole operation.
    """
    return _document_with(extra=_nested(depth))


def _burn_c_recursion_budget(frames: int, call: Callable[[], None]) -> None:
    """Consume ``frames`` levels of the C recursion budget, then ``call``.

    Each level re-enters the evaluation loop through C -- `map` calls its
    callback from C -- and that is what spends the budget `repr` is limited by.
    Measured three ways at 1,200 levels against a depth-8,000 value that
    renders fine from a shallow stack: `map` leaves it unrenderable; a list
    comprehension and a plain recursive call both leave it renderable, because
    on CPython 3.13 neither adds a C frame. `C417`'s suggested rewrite to a
    comprehension would leave this helper burning nothing and both callers
    green for no reason, which is why it is suppressed rather than applied.
    """
    if frames <= 0:
        call()
        return
    list(map(lambda _: _burn_c_recursion_budget(frames - 1, call), [0]))  # noqa: C417 - see above


def test_the_nesting_depth_the_recursion_tests_use_really_does_defeat_repr() -> None:
    """A premise of the two tests below, not a claim about Theurian.

    Both assert that a document too deep to render is diagnosed as a document
    fault. If an interpreter ever renders `_DEEPER_THAN_THE_INTERPRETER_CAN_RENDER`
    levels, both would pass without the recursion ever happening -- green, and
    testing nothing. This is the assertion that fails first and says so.
    """
    with pytest.raises(RecursionError):
        repr(_nested(_DEEPER_THAN_THE_INTERPRETER_CAN_RENDER))


def test_a_deeply_nested_document_is_a_document_fault_not_a_broken_installation() -> None:
    """Issue #291, at ambient depth zero -- a direct call.

    Measured on this branch: `SchemaUnreadableError`, reason "a schema $ref
    resolves recursively without terminating", whose remedy tells the user to
    reinstall Theurian. Nothing is wrong with the installation: the bundled
    schema resolved every `$ref` it has, and the document is what nests. The
    correct answer is `MigrationError` -- fix your document -- which is also
    what every other document fault at this seam already raises.

    `SchemaUnreadableError` is not a `MigrationError` subclass, so this
    `pytest.raises` genuinely excludes today's answer rather than accepting it.
    """
    document = _deeply_nested_document(_DEEPER_THAN_THE_INTERPRETER_CAN_RENDER)

    with pytest.raises(MigrationError):
        validate_migration_document(document, real_schema_root())


def test_a_deeply_nested_document_is_a_document_fault_from_an_already_consumed_stack() -> None:
    """Issue #291's second ambient depth: called with 1,200 C-recursion levels
    already spent, the harness shape the issue used.

    The issue recorded this ambient depth producing a *third* outcome -- the
    `RecursionError` escaping the handler uncaught, a raw traceback and CP-2
    violation. That face did not reproduce here: on CPython 3.13.3 the budget
    is restored as the `repr` unwinds, so the handler has room to build its
    error and this call answers `SchemaUnreadableError`, the same
    mistranslation as the direct call above. Recorded rather than claimed --
    the wrong answer is the same at both depths on this interpreter, and both
    are wrong for the same reason.

    Kept as its own test because ambient depth is the dimension that selects
    the outcome for a *fixed* document: at 1,200 burned levels a depth-8,000
    document is answered `SchemaUnreadableError` while the identical document
    called directly is answered `MigrationError` (measured, same session). A
    fix that bounds document depth, or that attributes the recursion by
    measuring the document, holds at every ambient depth; one that tunes a
    threshold does not.
    """
    document = _deeply_nested_document(_DEEPER_THAN_THE_INTERPRETER_CAN_RENDER)
    previous_limit = sys.getrecursionlimit()
    # The burn spends Python frames as well as C budget; raising the Python
    # limit keeps this test measuring the C budget and not the harness.
    sys.setrecursionlimit(previous_limit + 10 * _AMBIENT_FRAMES)

    try:
        with pytest.raises(MigrationError):
            _burn_c_recursion_budget(
                _AMBIENT_FRAMES,
                lambda: validate_migration_document(document, real_schema_root()),
            )
    finally:
        sys.setrecursionlimit(previous_limit)


def test_a_list_valued_keyword_other_than_required_is_not_read_as_missing_properties(
    tmp_path: Path,
) -> None:
    """The guard inside the #289 fix's `required` wording, driven.

    `_schema_rejection` words a `required` rejection from the schema's own list
    of names -- the one rejection whose cause is nowhere in the instance. That
    branch is selected by the *keyword*, and this test is why the keyword check
    is there rather than being inferred from the shapes: `type` and `enum` also
    carry a list of strings, and against a mapping instance every name they
    list would otherwise be reported as a missing property. Deleting the
    `exc.validator != "required"` line turns the message below into "missing
    the required properties 'string', 'null'", which is not what `type` means.

    The bundled schema cannot drive it -- its one array-valued `type`
    (`validTo`) sits inside `#/$defs/operation`'s `oneOf`, which answers first
    with a list of *subschemas* -- so the schema is written here instead.
    """
    _validator.cache_clear()
    schema_dir = _write_schema(tmp_path, {"type": ["string", "null"]})

    try:
        with pytest.raises(MigrationError) as excinfo:
            validate_migration_document({"apiVersion": "theurian.dev/v1"}, schema_dir)
    finally:
        _validator.cache_clear()

    message = str(excinfo.value)
    assert "missing the required" not in message, "a type keyword lists types, not properties"
    assert "'type'" in message, "the reader is still told which keyword refused"


# -- issue #291's guard was itself an unbounded-work DoS over YAML aliases -----
#
# `_refuse_a_document_that_nests_too_deep` walks the parsed document, and a
# PyYAML anchor referenced from two aliases -- each referenced from two more --
# is a shared-reference DAG that expands to 2**N nodes from an N-line file. The
# shipped guard walked it as a tree, so a ~500-byte file cost 2**24 = 100 M node
# visits (orchestrator-measured 42.5 s), and even had it finished, `jsonschema`'s
# own `validate` interpolates the failing instance with `{instance!r}` -- which
# re-expands the identical DAG, building a 46 MB message at alias level 22
# (measured 2026-08-21, jsonschema 4.26.0) *before* `_schema_rejection` is
# reached. Memoising the walk on object identity would make the guard fast but
# leave that second cost untouched: a memoised walk counts the file as its ~24
# distinct nodes and waves it through to `validate`. So the guard stays
# un-collapsed and refuses at `MAX_DOCUMENT_NODES` -- ahead of `validate`, which
# is the only place the dependency's own repr cost can be stopped.


def _alias_bomb(levels: int) -> str:
    """The exponential shared-reference DAG, as a YAML migration document.

    ``l0`` anchors a small mapping; each ``l{k}`` references ``l{k-1}`` twice, so
    the expanded node count doubles per level while the source grows by one line.
    Placed at ``author`` as well, where the schema's ``type`` keyword would repr
    the whole DAG if it ever reached `validate`.
    """
    lines = ["l0: &l0 {v: 0}"]
    for level in range(1, levels):
        lines.append(f"l{level}: &l{level} {{a: *l{level - 1}, b: *l{level - 1}}}")
    return (
        "\n".join(lines)
        + "\napiVersion: theurian.dev/v1\nid: 01K1KKKKKK01234567890ABCDE\n"
        + "createdAt: 2026-08-02T10:00:00+09:00\n"
        + f"author: *l{levels - 1}\n"
        + "operations:\n  - op: createItem\n    itemId: a.b\n    kind: architecture\n"
        + "    namespace: n\n    owner: o\n"
    )


def test_an_alias_bomb_is_refused_before_jsonschema_can_expand_it() -> None:
    """RED before the fix: the guard walks the DAG as a tree and never returns
    for level 20 (2**20 visits), and if it did, `validate` would build a
    multi-megabyte message from the same DAG.

    Level 20 expands to ~1 M nodes, well past `MAX_DOCUMENT_NODES`, so the guard
    must refuse it -- and the refusal that reaches the caller must be the node
    ceiling's, proving the document was stopped *before* `validate` rather than
    after `jsonschema` had already paid the expansion. The shipped guard raises
    nothing for this document (it nests only 20 levels, under
    `MAX_DOCUMENT_NESTING`, and had no node ceiling at all), so
    ``pytest.raises`` fails on it outright.
    """
    document = load_yaml_mapping(_alias_bomb(20))

    with pytest.raises(MigrationError):
        _refuse_a_document_that_nests_too_deep(document, None)

    with pytest.raises(MigrationError) as excinfo:
        validate_migration_document(document, real_schema_root())

    message = str(excinfo.value)
    assert str(MAX_DOCUMENT_NODES) in message and "values" in message, (
        "the refusal must be the node ceiling's, i.e. raised before `validate`"
    )
    assert "does not satisfy" not in message, "a schema rejection means it reached `validate`"
    assert len(message) < 2000, "the refusal itself must be bounded"


def test_the_document_nesting_ceiling_holds_at_its_boundary() -> None:
    """`MAX_DOCUMENT_NESTING` and the depth at which the guard first refuses.

    The literal assertion kills a mutant that changes the constant's value (a
    survivor the shipped tests never pinned); the boundary -- driven with literal
    depths so it stays sensitive when the constant is mutated -- kills an
    off-by-one in where the frontier starts and a dropped depth check. A document
    is a chain of mappings, so a leaf at depth ``d`` needs ``d - 1`` wrappers.
    """
    assert MAX_DOCUMENT_NESTING == 64, "load-bearing; changing it is a security decision"

    _refuse_a_document_that_nests_too_deep(_nested(63), None)  # leaf at depth 64: allowed

    with pytest.raises(MigrationError) as excinfo:
        _refuse_a_document_that_nests_too_deep(_nested(64), None)  # leaf at depth 65: refused
    message = str(excinfo.value)
    assert "64" in message and "7 levels" in message, (
        "the message names the ceiling and the real max"
    )


def test_the_document_node_ceiling_holds_at_its_boundary() -> None:
    """`MAX_DOCUMENT_NODES` and the refusal it drives.

    The literal assertion kills a value-changing mutant; the behavioural half
    kills a dropped node check. A flat sequence one longer than the ceiling is
    refused as it is read (the frontier never holds all of it); a small one
    passes.
    """
    assert MAX_DOCUMENT_NODES == 100_000, "load-bearing; changing it is a security decision"

    _refuse_a_document_that_nests_too_deep({"operations": [0] * 10}, None)  # tiny: allowed

    with pytest.raises(MigrationError) as excinfo:
        _refuse_a_document_that_nests_too_deep({"operations": [0] * MAX_DOCUMENT_NODES}, None)
    assert str(MAX_DOCUMENT_NODES) in str(excinfo.value), "the message names the ceiling"


def test_the_guard_walks_a_mapping_key_not_only_its_value() -> None:
    """MEDIUM: the shipped guard read ``value.values()`` and never the keys, so a
    container sitting in a *key* slipped past the depth bound and reached
    `validate`. The parsed and propose seams never build a non-string key, so
    this is defense for an in-memory caller of `validate_migration_document`.

    A tuple nested past `MAX_DOCUMENT_NESTING`, used as a key, is refused by the
    depth bound now that keys are walked. Before the fix the key was ignored and
    the (shallow) values let the document through to `validate`, where it was
    reported as a schema rejection rather than a nesting fault -- so the message,
    not merely the exception type, is what this pins.
    """
    deep_key: object = "leaf"
    for _ in range(MAX_DOCUMENT_NESTING + 6):
        deep_key = (deep_key,)
    document = {deep_key: 1, "apiVersion": "theurian.dev/v1"}

    with pytest.raises(MigrationError) as excinfo:
        _refuse_a_document_that_nests_too_deep(document, None)  # type: ignore[arg-type]
    assert "nests more than" in str(excinfo.value), "a container key must trip the depth bound"


def test_the_guard_walks_a_set_not_only_lists_and_tuples() -> None:
    """MEDIUM: the shipped container check was ``list | tuple``, so a ``set`` or
    ``frozenset`` was treated as a leaf and its contents never counted. An
    in-memory caller could hide an oversized structure inside one and have it
    reach `validate` unbounded.

    A ``frozenset`` holding more than `MAX_DOCUMENT_NODES` elements is refused by
    the node bound now that sets are walked; before the fix it was a single leaf,
    counted as one node, and waved through.
    """
    document = {"operations": frozenset(range(MAX_DOCUMENT_NODES + 10))}

    with pytest.raises(MigrationError) as excinfo:
        _refuse_a_document_that_nests_too_deep(document, None)
    assert str(MAX_DOCUMENT_NODES) in str(excinfo.value), "a set's contents must count as nodes"


def test_a_schema_rejection_renders_a_deep_instance_without_recursing() -> None:
    """Issue #289 face two: `_schema_rejection` echoed the instance with
    ``_bounded(repr(...))``, which builds the *whole* repr before truncating.

    On a value too deep for `repr` to render, that ``repr`` raises
    `RecursionError` -- so the refusal builder itself crashes rather than
    bounding the echo. `reprlib` stops at its ``maxlevel`` and never recurses
    that far, so the message is produced whatever the instance's depth. The
    guard refuses such a document before it reaches `validate`, so this drives
    `_schema_rejection` directly: it is the seam's own robustness that is pinned,
    not a reachable path.
    """
    deep: object = "leaf"
    for _ in range(12_000):  # past the ~9,997 a shallow stack can repr
        deep = [deep]
    exc = ValidationError(
        "unused", validator="type", validator_value="string", instance=deep, path=["author"]
    )

    message = _schema_rejection(exc)  # must not raise RecursionError

    assert "author" in message, "the location is still named"
    assert len(message) <= 2000, "the echo is bounded"
