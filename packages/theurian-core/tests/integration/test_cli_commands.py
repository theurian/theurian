"""CLI commands, invoked in-process.

The e2e suite runs the installed binary and proves packaging works. These run
the same commands through Typer's runner: faster, measurable by coverage, and
able to assert on the exact JSON a caller receives.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectError, ProjectRegistry
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_STATE_ERROR = 4

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_auth_rotate.py` and `test_setup_journal.py` use before
#: a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
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
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
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
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command and parse its JSON.

    ``mix_stderr=False`` matters: the CLI keeps stdout a clean machine channel
    and puts errors on stderr, and a test that merged them could not tell.
    """
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _write_migration(root: Path, migration: str = MIGRATION, body: str = BODY) -> None:
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(migration)


# -- issue #205: a contentFile that does not resolve ------------------------

UNRESOLVABLE_MIGRATION_ID = "01K1EEEEEE01234567890ABCDE"
UNRESOLVABLE_REVISION_ID = "01K1EEEREV01234567890ABCDE"

#: The natural authoring mistake the issue names: a path that would have
#: resolved against a proposal directory, left uncorrected after the migration
#: moved into `.theurian/migrations/`, which is where `contentFile` actually
#: resolves from (docs/protocol/migrations.md, "Path safety").
UNRESOLVABLE_CONTENT_FILE = "content.md"

UNRESOLVABLE_CONTENT_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {UNRESOLVABLE_MIGRATION_ID}
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
    revisionId: {UNRESOLVABLE_REVISION_ID}
    contentFile: {UNRESOLVABLE_CONTENT_FILE}
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


def _write_unresolvable_content_migration(root: Path) -> None:
    """A migration referencing a ``contentFile`` that is never written to disk.

    Reproduces issue #205: `resolve_context` loads every migration under
    `.theurian/migrations/`, including this one, so any `--json` command that
    calls it inherits the crash unless the loader itself converts the read
    failure into a structured error.
    """
    (root / f".theurian/migrations/{UNRESOLVABLE_MIGRATION_ID}-repro.yaml").write_text(
        UNRESOLVABLE_CONTENT_MIGRATION
    )


def _content_unreadable_migration_path() -> str:
    return f".theurian/migrations/{UNRESOLVABLE_MIGRATION_ID}-repro.yaml"


def _assert_content_unreadable_payload(payload: dict[str, Any]) -> None:
    """Full equality, not a substring: a gutted ``error``/``remedy`` must fail this.

    Anchored to ``os.strerror(errno.ENOENT)`` rather than a hardcoded
    "No such file or directory", so the assertion is portable and still pins
    that ``exc.strerror`` reaches the message -- a mutation that dropped it
    survived a looser ``"content.md" in payload["error"]`` check, because the
    filename alone was still present.
    """
    migration_path = _content_unreadable_migration_path()
    reason = os.strerror(errno.ENOENT)
    assert payload["error"] == (
        f"{migration_path!r}: contentFile {UNRESOLVABLE_CONTENT_FILE!r} could not be read: {reason}"
    )
    assert payload["remedy"] == (
        f"{UNRESOLVABLE_CONTENT_FILE!r} resolves relative to the migration file "
        f"({migration_path!r}), not to a proposal directory "
        f"(docs/protocol/migrations.md, 'Path safety'). Fix the path, or "
        f"restore the referenced file, then retry."
    )


# -- issue #205: a migration file that is unreadable -------------------------
#
# The sibling face: the migration YAML itself, not a `contentFile` it names.
# The `chmod 000` measurement behind both fixes lives on
# `MigrationFileUnreadableError`'s own docstring (`domain/errors.py`), not
# repeated at each of these call sites.

UNREADABLE_MIGRATION_ID = "01K1JJJJJJ01234567890ABCDE"
UNREADABLE_MIGRATION_FILENAME = f"{UNREADABLE_MIGRATION_ID}-unreadable.yaml"

UNREADABLE_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {UNREADABLE_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""


def _write_unreadable_migration(root: Path) -> Path:
    """A schema-valid migration file, `chmod 000`'d after being written.

    Returns the path so the caller can restore its mode in a ``finally`` --
    pytest's own ``tmp_path`` cleanup walks the tree afterward, and a mode-000
    file left behind fails *that* instead of the calling test, which reads as
    an unrelated, confusing failure.
    """
    migration = root / f".theurian/migrations/{UNREADABLE_MIGRATION_FILENAME}"
    migration.write_text(UNREADABLE_MIGRATION)
    migration.chmod(0o000)
    return migration


def _assert_file_unreadable_payload(payload: dict[str, Any]) -> None:
    """Full equality, the same anti-mutation shape as :func:`_assert_content_unreadable_payload`.

    A remedy gutted to the 10-char token ``"permission"`` survived a looser
    ``"permission" in payload["remedy"]`` check; only exact equality catches
    that.
    """
    migration_path = f".theurian/migrations/{UNREADABLE_MIGRATION_FILENAME}"
    reason = os.strerror(errno.EACCES)
    assert payload["error"] == f"{migration_path!r} could not be read: {reason}"
    assert payload["remedy"] == (
        f"Confirm this user has read permission on {migration_path!r} and its "
        f"parent directory, then retry."
    )


# -- issue #217: a YAML syntax error used to propagate uncaught -------------

MALFORMED_YAML_MIGRATION_FILENAME = "01K1GGGGGG01234567890ABCDE-malformed.yaml"
_MALFORMED_YAML = "id: [unclosed\n  bad: {{{\n"


def _write_malformed_yaml_migration(root: Path) -> Path:
    migration = root / f".theurian/migrations/{MALFORMED_YAML_MIGRATION_FILENAME}"
    migration.write_text(_MALFORMED_YAML)
    return migration


# -- round two: a document nested past the parser's recursion limit ---------

DEEPLY_NESTED_MIGRATION_FILENAME = "01K1RRRRRR01234567890ABCDE-nested.yaml"
#: 1023 bytes -- the orchestrator's own reproduction, well past the measured
#: 495-bracket-pair leak threshold, so this stays red even if PyYAML's own
#: recursion cost per nesting level shifts.
_DEEPLY_NESTED_YAML = "apiVersion: theurian.dev/v1\nid: " + "[" * 1000 + "]" * 1000 + "\n"


def _write_deeply_nested_migration(root: Path) -> Path:
    migration = root / f".theurian/migrations/{DEEPLY_NESTED_MIGRATION_FILENAME}"
    migration.write_text(_DEEPLY_NESTED_YAML)
    return migration


# -- issue #214: an unreadable migrations directory used to look silently empty --


def _assert_directory_unreadable_payload(payload: dict[str, Any], migrations_path: str) -> None:
    """Every call site in this file drives this through `chmod 0o000` on
    `migrations_dir` itself -- an `EACCES` raised while *enumerating* the
    directory, not while probing it -- so `os.strerror(errno.EACCES)` is safe
    to pin in full here, the same reasoning `_assert_file_unreadable_payload`
    above already gives for the sibling `MigrationFileUnreadableError` case.
    Exact equality, not `.startswith`: a mutation collapsing
    `exc.strerror or str(exc)` to a constant survived a looser check because
    the fixed prefix alone was still present.
    """
    reason = os.strerror(errno.EACCES)
    assert payload["error"] == f"{migrations_path!r} could not be listed: {reason}"
    assert payload["remedy"] == (
        f"Confirm this user has read and execute permission on {migrations_path!r} "
        f"and its parent directories, then retry."
    )


# -- init ------------------------------------------------------------------


def test_init_creates_the_layout(project: Path) -> None:
    code, payload = _invoke("init")
    assert code == 0
    assert payload["changed"]
    assert (project / ".theurian/migrations").is_dir()
    assert (project / ".theurian/knowledge/architecture").is_dir()


def test_init_is_idempotent(project: Path) -> None:
    _invoke("init")
    code, payload = _invoke("init")
    assert code == 0
    assert not payload["changed"]
    assert payload["createdPaths"] == []


def test_init_appends_the_gitignore_block_once(project: Path) -> None:
    """SEC-18: re-running rewrites only Theurian's own marked block."""
    (project / ".gitignore").write_text("# a rule the user wrote\n*.log\n")

    _invoke("init")
    _invoke("init")

    content = (project / ".gitignore").read_text()
    assert content.count("# >>> theurian >>>") == 1
    assert "*.log" in content, "the user's own rules must survive"


def test_init_outside_a_git_repository_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, payload = _invoke("init")
    assert code == 1
    assert "not inside a Git repository" in payload["error"]


def test_init_reports_an_unresolvable_content_file_instead_of_crashing(project: Path) -> None:
    """Issue #205: every ``--json`` command reaching ``resolve_context`` is a
    member of this class, not only ``migrate validate``. ``init`` re-run against
    a project whose migrations already hold one is the second measured member.
    """
    _invoke("init")
    _write_unresolvable_content_migration(project)

    # `catch_exceptions=False` re-raises anything Click's own `SystemExit`
    # handling does not swallow, so a bare `FileNotFoundError` propagates out
    # of `invoke` itself and fails this test at the call above -- which is
    # exactly what it did before the fix. Reaching the assertions below is
    # itself the "no traceback reached the caller" proof.
    result = runner.invoke(app, ["init", "--json"], catch_exceptions=False)

    assert result.exit_code != 0
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_content_unreadable_payload(json.loads(result.stderr))


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_init_reports_an_unreadable_migration_file_instead_of_crashing(project: Path) -> None:
    """``init``'s counterpart to the ``migrate validate`` unreadable-file test below.

    Round-one review measured the `MigrationFileUnreadableError` branch in
    ``_context_remedy`` as never exercised by any test: every existing test
    reached it only through `_require_project`'s own except clause (`migrate
    validate`), never through a command that calls `resolve_context` directly.
    `init` re-run against a project whose migrations already hold an
    unreadable file is that missing combination.
    """
    _invoke("init")
    migration = _write_unreadable_migration(project)
    try:
        result = runner.invoke(app, ["init", "--json"], catch_exceptions=False)
    finally:
        migration.chmod(0o644)

    assert result.exit_code != 0
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_file_unreadable_payload(json.loads(result.stderr))


# -- project ---------------------------------------------------------------


def test_register_then_list(project: Path) -> None:
    _invoke("init")
    code, registered = _invoke("project", "register")
    assert code == 0
    assert registered["projectId"] == "demo"

    _, listed = _invoke("project", "list")
    assert listed["count"] == 1


def test_register_is_idempotent(project: Path) -> None:
    """FR-L2. The first registration time is preserved, not refreshed."""
    _invoke("init")
    _invoke("project", "register")
    _, again = _invoke("project", "register")
    assert not again["changed"]


def test_unregister_reports_that_knowledge_survives(project: Path) -> None:
    _invoke("init")
    _invoke("project", "register")

    _, removed = _invoke("project", "unregister", "demo")
    assert removed["removed"]
    assert removed["knowledgePreserved"]

    _, again = _invoke("project", "unregister", "demo")
    assert not again["removed"], "removing a missing project is not an error"


def test_status_reports_an_unbuilt_state(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, status = _invoke("project", "status")
    assert status["migrationCount"] == 1
    assert not status["stateBuilt"]
    assert status["indexStale"]


def test_status_outside_a_repository_reports_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, status = _invoke("project", "status")
    assert code == 0, "status must report, not fail, outside a project"
    assert not status["registered"]


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_status_surfaces_a_remedy_for_an_unresolved_project_not_only_a_reason(
    project: Path,
) -> None:
    """``_unresolved_status``'s own docstring promise, held to the unified attribute.

    Round-two's refactor moved `_context_remedy` and `_require_project` to a
    single ``if exc.remedy:`` check, replacing a type-keyed
    ``isinstance``/``except`` enumeration -- but `_unresolved_status` (this
    command's own exit-0 handler) was a *third*, separate caller that still
    read ``isinstance(exc, ProjectError) and exc.remedy``, so
    `MigrationsDirectoryUnreadableError` and its siblings reached this payload
    with `reason` but no `remedy`, silently narrower than every other command
    reporting the same exception. `chmod 000 .theurian` -- `is_dir()` must
    traverse it to stat `migrations_dir` -- drives exactly that exception
    through `resolve_context` into this handler.
    """
    _invoke("init")
    theurian_dir = project / ".theurian"
    theurian_dir.chmod(0o000)
    try:
        code, status = _invoke("project", "status")
    finally:
        theurian_dir.chmod(0o700)

    assert code == 0, "status must report, not fail, on an unresolved project"
    assert status["registered"] is False
    assert "could not be listed" in status["reason"]
    assert "remedy" in status, (
        "MigrationsDirectoryUnreadableError sets .remedy; _unresolved_status must surface it"
    )
    assert status["remedy"] == (
        f"Confirm this user has read and execute permission on {'.theurian/migrations'!r} "
        f"and its parent directories, then retry."
    )


# -- project list, when the registry file is not what it should be ----------
#
# `project list` is the command every other surface names when it wants a user
# to go and look -- `project register`'s and `ids_for_root`'s remedies both send
# them here -- so it is the one command that must survive a registry it cannot
# fully read. A skipped entry it did not report was a project that vanished in
# silence, and a file it could not parse at all reached the user as a Rich
# traceback from the very command the remedy told them to run.


@pytest.fixture
def registry_path(project: Path) -> Path:
    """The per-user registry file this test's ``THEURIAN_DATA_DIR`` points at.

    Written to directly, because no supported command can produce a malformed
    entry -- the file lives in the user's home directory and a hand edit is the
    only way in, which is exactly why these branches exist.
    """
    path = project.parent / "datadir" / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_list_reports_an_empty_unreadable_set_rather_than_omitting_the_field(
    project: Path,
) -> None:
    """CP-2. The field is part of the shape, not a flag that appears on trouble.

    A consumer that has to branch on whether a key is present will eventually
    forget to, and the day it forgets is the day the key is there -- when
    something is broken. Asserted as ``== []``, not as ``in listed``: a value of
    ``None``, or a string, would satisfy "the key exists" while breaking every
    caller that iterates it.
    """
    _invoke("init")
    _invoke("project", "register")

    _, listed = _invoke("project", "list")

    assert listed["unreadable"] == []
    assert "remedy" not in listed, "a healthy registry must not print a cure for nothing"


def test_list_names_the_unreadable_id_so_the_remedy_can_be_typed(
    project: Path, registry_path: Path
) -> None:
    """The id `project unregister` needs is the id only this command can show.

    Every other surface -- `project register`, `resolve_context`, `index status`
    -- reports an unreadable entry by telling the user to run
    ``theurian project unregister <id>`` and to find ``<id>`` here. Counting the
    readable entries and silently dropping the rest, which is what `load` alone
    does, made that remedy untypable and the project itself invisible.

    ``count`` deliberately still reports only what is readable: the entry is
    named under ``unreadable``, not padded into ``projects`` as a registration
    the command cannot actually describe.
    """
    registry_path.write_text(
        json.dumps(
            {
                "demo": {"rootPath": str(project), "defaultBranch": "main"},
                "hand-edited": {"defaultBranch": "main"},
            }
        )
    )

    code, listed = _invoke("project", "list")

    assert code == 0
    assert listed["unreadable"] == ["hand-edited"]
    assert listed["count"] == 1, "the unreadable entry is named, not counted as a project"
    assert [p["projectId"] for p in listed["projects"]] == ["demo"]
    assert "theurian project unregister" in listed["remedy"]


@pytest.mark.parametrize(
    ("corruption", "content", "expected"),
    [
        ("truncated JSON", b'{"demo": {"rootPath"', "cannot be read as JSON"),
        ("a JSON array", b"[]", "must hold a JSON object"),
        ("arbitrary bytes", b"\xff\xfe\x00\x01theurian", "cannot be read as JSON"),
    ],
    ids=["truncated-json", "json-array", "arbitrary-bytes"],
)
def test_list_reports_a_registry_it_cannot_parse_instead_of_raising(
    registry_path: Path, corruption: str, content: bytes, expected: str
) -> None:
    """CP-2, and the loop that had no exit.

    None of these can be recovered from entry by entry -- without a dict of ids
    there is nothing to partition -- so the whole file is refused. It still has
    to arrive as the ``{error, remedy}`` contract at exit 1: this command is
    where every other remedy sends the user, so a traceback here left them with
    a broken registry and no working way to inspect it.

    ``arbitrary bytes`` is the case that hid behind the other two. A registry of
    binary -- a partial overwrite, a restored file -- raises
    ``UnicodeDecodeError`` at ``read_text``, which is a ``ValueError`` and *not*
    a ``JSONDecodeError``, so it sailed past a handler that caught only the
    latter. ``catch_exceptions=False`` is what makes that a failure here rather
    than a silently swallowed exit code.
    """
    registry_path.write_bytes(content)

    code, payload = _invoke("project", "list")

    assert code == 1, f"{corruption} must be reported, not raised"
    assert expected in payload["error"]
    assert str(registry_path) in payload["error"], "the file to fix is named"
    assert "theurian project register" in payload["remedy"], (
        "the remedy is delete-and-re-register; a message with no way out is why this branch exists"
    )


# -- the same unreadable file, reached through the other two commands -------
#
# The fix above taught `project list` to report a registry it cannot parse.
# It never reached the two commands beside it, and both are on the remedy chain
# every other surface prints: `project status` is what a confused user runs
# first, and `project unregister <id>` is where the chain ends. A traceback at
# the first and a wrong cure at the last leave that chain broken at both ends.
#
# The corruption shapes are the three above, restated rather than shared,
# because "these three shapes reach this command" is the claim: a shared
# parameter list that lost one would quietly narrow every test using it.

REGISTRY_CORRUPTIONS = [
    ("truncated JSON", b'{"demo": {"rootPath"', "cannot be read as JSON"),
    ("a JSON array", b"[]", "must hold a JSON object"),
    ("arbitrary bytes", b"\xff\xfe\x00\x01theurian", "cannot be read as JSON"),
]


@pytest.mark.parametrize(
    ("corruption", "content", "expected"),
    REGISTRY_CORRUPTIONS,
    ids=["truncated-json", "json-array", "arbitrary-bytes"],
)
def test_status_reports_a_registry_it_cannot_parse_instead_of_raising(
    registry_path: Path, corruption: str, content: bytes, expected: str
) -> None:
    """The ``--json`` contract has to hold on the command people run when lost.

    ``status``'s handler for a failed ``resolve_context`` asks the registry for
    its unreadable ids -- and on a file that is not JSON at all, that read raises
    the very exception the handler is inside. Measured before the fix: exit 1,
    stdout zero bytes, a Rich traceback, on all three shapes. A caller parsing
    ``--json`` gets nothing to parse, from the command every remedy sends them
    to first.

    Exit 0 is deliberate and matches the rest of this command: ``status``
    answers for directories that are not projects at all, so "cannot tell" is a
    status rather than a command failure -- and ``registered: null`` is already
    its value for exactly that.

    ``unreadable`` is ``[]`` here and that is not a claim that nothing is
    broken: without a JSON object there is no set of ids to partition, so the
    list is empty because it could not be computed. ``reason`` and ``remedy``
    carry the whole-file failure, and the field stays present because a
    consumer that has to branch on key presence eventually forgets to.
    """
    registry_path.write_bytes(content)

    code, payload = _invoke("project", "status")

    assert code == 0, f"{corruption} must be reported as a status, not raised"
    assert payload["registered"] is None, "the registry cannot say, and False would be a guess"
    assert expected in payload["reason"]
    assert "re-register each project with `theurian project register`" in payload["remedy"], (
        "the whole-file failure has one reliable cure, and this is the command that must print it"
    )
    assert payload["unreadable"] == []


def test_unregister_names_the_unreadable_file_rather_than_blaming_the_id(
    registry_path: Path,
) -> None:
    """The last link of the remedy chain, and the one that pointed nowhere.

    ``project list``, ``project status``, ``probe_project_registered``,
    ``project register`` and every MCP tool name
    ``theurian project unregister <id>`` as the cure for a broken registry. When
    the file cannot be read at all, this command answered "Check the project id
    with `theurian project list`" -- and the id is not the problem, ``list``
    fails on the same file, and the user is returned to where they started.

    One corruption shape rather than three: what varies between them is the
    message, which the ``project list`` tests above already pin. What is
    asserted here is the remedy, which does not vary.
    """
    registry_path.write_bytes(b'{"demo": {"rootPath"')

    code, payload = _invoke("project", "unregister", "demo")

    assert code == 1
    assert "re-register each project with `theurian project register`" in payload["remedy"]
    assert "Check the project id" not in payload["remedy"], (
        "the id is not what is wrong, and `project list` cannot read this file either"
    )


def test_status_outside_a_repository_keeps_a_certain_answer_on_a_wholly_corrupt_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated ambiguity must not weaken an answer that is not ambiguous.

    A directory outside a Git working tree is not a project whatever the
    registry says: there is no root for any registration to name, so no failure
    to read that file could possibly be about *this* directory. ``registered``
    stays ``False`` -- the honest answer -- rather than being dragged to ``None``
    by a file the question does not depend on.

    Pinned because the tempting simplification is "the registry is broken, so
    nothing can be known", and it is wrong in exactly this one case. The
    in-repository counterpart, where ``None`` *is* correct, is
    ``test_status_reports_a_registry_it_cannot_parse_instead_of_raising``
    above; without this pair, either behaviour alone looks like the rule.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    path = tmp_path / "datadir" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"demo": {"rootPath"')

    code, payload = _invoke("project", "status")

    assert code == 0
    assert "not inside a Git repository" in payload["reason"], "the fixture must be outside one"
    assert payload["registered"] is False
    assert payload["unreadable"] == []


def test_status_says_it_cannot_know_when_the_registry_breaks_between_its_two_reads(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolved path's own version of "cannot know", which nothing reached.

    ``resolve_context`` asks the registry which project this root is, and the
    payload asks it again for the ``registered`` flag and the unreadable set.
    "A moment ago" is not "now": the file lives in the user's home directory,
    another ``theurian`` process shares it, and the product's own remedies tell
    people to edit it. A raise on the second read cost this command its entire
    ``--json`` payload for a file it consults for one field.

    Forced with a monkeypatch rather than a real race, because a race is not a
    fixture. What is being tested is the *handling*, and a test that has to win
    a timing lottery to reach it is a test that mostly does not.

    ``registered`` becomes ``None`` and nothing else is lost: the state hash,
    the migration count and the freshness all come from the project's own
    ``.theurian/``, which the registry has nothing to do with. A handler that
    turned the whole payload into an error would throw away every field that was
    still perfectly knowable.
    """
    _invoke("init")
    _write_migration(project)
    _invoke("project", "register")

    def _explode(self: object) -> dict[str, dict[str, str]]:
        raise ProjectError("hand-edited between reads", remedy="Delete it and re-register.")

    monkeypatch.setattr(ProjectRegistry, "load", _explode)

    code, payload = _invoke("project", "status")

    assert code == 0
    assert payload["registered"] is None, (
        "the file cannot be searched, and False would claim it was"
    )
    assert "hand-edited between reads" in payload["reason"]
    assert payload["remedy"] == "Delete it and re-register."
    assert payload["migrationCount"] == 1, "a field the registry has nothing to do with survives"
    assert payload["stateHash"], "and so does the one every other command compares against"


def test_unregister_does_not_refuse_an_id_for_its_shape(project: Path) -> None:
    """The escape command has to be able to name what broke the registry.

    This used to assert the opposite, and the opposite was the defect. Parsing
    the argument as a ``ProjectId`` first made this command refuse exactly the
    entries it exists to remove: a registry key is whatever a hand edit left
    behind, and ``theurian project unregister 'Team One/API'`` answered "Check
    the project id with `theurian project list`" -- the listing that had just
    printed it. Removing a key needs no id semantics; only writing one does.

    An id that is not a slug is now looked up like any other key. This one is
    absent from the file, so nothing is removed, at the exit code every other
    absent id already gets.

    The remedy branch this replaced is still pinned, from the side that can
    actually reach it:
    ``test_unregister_names_the_unreadable_file_rather_than_blaming_the_id``
    asserts the registry's own cure wins over ``_context_remedy``'s default.
    """
    _invoke("init")
    _invoke("project", "register")

    code, payload = _invoke("project", "unregister", "Not A Slug")

    assert code == 0, "a key absent from the file is not an error, whatever it looks like"
    assert payload["removed"] is False


# -- migrate ---------------------------------------------------------------


def test_validate_reports_the_application_order(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, validated = _invoke("migrate", "validate")
    assert validated["valid"]
    assert validated["applicationOrder"] == [MIGRATION_ID]


def test_apply_then_status(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    code, applied = _invoke("migrate", "apply")
    assert code == 0
    assert applied["applied"] == [MIGRATION_ID]
    assert applied["operationsApplied"] == 2

    _, status = _invoke("migrate", "status")
    assert status["applied"] == 1
    assert status["pending"] == 0


def test_apply_is_idempotent(project: Path) -> None:
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    _, second = _invoke("migrate", "apply")
    assert second["applied"] == []
    assert not second["changed"]


def test_status_on_an_unbuilt_state_lists_everything_as_pending(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, status = _invoke("migrate", "status")
    assert not status["stateBuilt"]
    assert status["pending"] == 1
    assert status["pendingIds"] == [MIGRATION_ID]


def test_editing_an_applied_migration_is_fatal(project: Path) -> None:
    """ADR-0016: checked against the previously active state."""
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    path = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    path.write_text(path.read_text() + "  # edited after apply\n")

    code, error = _invoke("migrate", "status")
    assert code == EXIT_STATE_ERROR
    assert "never be edited" in error["error"]


def test_a_revision_conflict_is_reported_not_merged(project: Path) -> None:
    """ADR-0006. The remedy must say a human decides, not the tool.

    The conflicting migration carries a body file of its own. Pointing it at the
    first migration's body -- which it did, incidentally, before the issue #210
    refusal existed -- now reports that ambiguity instead, and this test would
    then assert ADR-0006's message against a set that never reaches the store.
    """
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    # No I, L, O, or U: those are excluded from Crockford base32.
    stale = "01K1STAAAA01234567890ABCDE"
    second = "01K1BBBBBB01234567890ABCDE"
    (project / ".theurian/knowledge/architecture/auth-policy.revised.md").write_text(
        "# Authentication policy\n\nEvery call carries a signed token, checked twice.\n"
    )
    (project / f".theurian/migrations/{second}-conflict.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {second}
createdAt: 2026-08-02T11:00:00+09:00
author: other@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1BBBREV01234567890ABCDE
    expectedRevision: {stale}
    contentFile: ../knowledge/architecture/auth-policy.revised.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""
    )

    code, error = _invoke("migrate", "apply")
    assert code == EXIT_STATE_ERROR
    assert "Revision conflict" in error["error"]
    assert "does not merge knowledge automatically" in error["remedy"]


def test_validate_reports_an_unresolvable_content_file_instead_of_crashing(project: Path) -> None:
    """Issue #205: the CLI's own reproduction.

    Before the fix, ``read_source_file``'s documented ``FileNotFoundError``
    escaped `resolve_context` -- called from `_require_project` -- as a bare
    `OSError` that none of `_require_project`'s `except` clauses caught,
    reaching Typer as a Rich traceback: exit 1, empty stdout, no `{error,
    remedy}` payload even under `--json` (CP-2).
    """
    _invoke("init")
    _write_unresolvable_content_migration(project)

    # `catch_exceptions=False` re-raises anything Click's own `SystemExit`
    # handling does not swallow, so a bare `FileNotFoundError` propagates out
    # of `invoke` itself and fails this test at the call above -- which is
    # exactly what it did before the fix. Reaching the assertions below is
    # itself the "no traceback reached the caller" proof.
    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_content_unreadable_payload(json.loads(result.stderr))


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_validate_reports_an_unreadable_migration_file_instead_of_crashing(project: Path) -> None:
    """Issue #205's second face: the migration *file* itself, not a `contentFile`
    it names.

    `read_source_file`'s raw `PermissionError` used to escape from
    `_load_one`'s own read (`migration_loader.py`), the sibling of the
    `contentFile` escape closed above -- same seam, same root cause, one call
    site over.
    """
    _invoke("init")
    migration = _write_unreadable_migration(project)
    try:
        result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)
    finally:
        migration.chmod(0o644)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_file_unreadable_payload(json.loads(result.stderr))


def test_validate_reports_a_malformed_migration_yaml_instead_of_crashing(project: Path) -> None:
    """Before issue #217's fix, `yaml.YAMLError` was neither
    `UnicodeDecodeError` nor `ValueError` -- the two types `_load_one`'s
    `except` clause around `load_yaml_mapping` caught at the time
    (`migration_loader.py`; it now catches three, `yaml.YAMLError` added
    alongside them) -- so it propagated uncaught through `resolve_context`.
    Measured against the real CLI with `id: [unclosed\\n  bad: {{{`: exit 1,
    empty stdout, a raw Rich traceback on stderr instead of the CP-2 `{error,
    remedy}` payload every sibling malformed-migration case in this file
    already got (see `test_a_malformed_migration_names_the_offending_field`
    and its neighbours below, all of which already report a structured
    refusal for a document that parses but is invalid).
    """
    _invoke("init")
    migration = _write_malformed_yaml_migration(project)

    # `catch_exceptions=False`: see the identical comment on
    # `test_validate_reports_an_unresolvable_content_file_instead_of_crashing`
    # above. A `yaml.YAMLError` escaping `runner.invoke` itself is exactly
    # what happened before the #217 fix.
    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    assert migration.name in payload["error"]
    # Exact pin, not merely truthy: a bare `MigrationError` (no `.remedy` of
    # its own) falls back to `_require_project`'s own `except MigrationError`
    # clause default (`cli/commands.py`), and a gutted remedy string still
    # reads as truthy.
    assert payload["remedy"] == "Fix the migration file, then retry."


def test_validate_reports_a_deeply_nested_migration_instead_of_a_raw_traceback(
    project: Path,
) -> None:
    """Adversarial HIGH (round two, orchestrator-reproduced): a migration
    nested past PyYAML's own recursion limit makes `load_yaml_mapping` raise
    `RecursionError`.

    Corrected (round three): `RecursionError` is a `RuntimeError` subclass, in
    turn an `Exception` subclass -- not, as an earlier revision of this
    docstring claimed, a `BaseException` subclass in the sense of sitting
    outside `Exception`'s hierarchy; a bare `except Exception` would have
    caught it. What actually let it through is that none of `_load_one`'s
    three `except` clauses (`migration_loader.py`) name `RuntimeError` or
    `RecursionError` -- they name `UnicodeDecodeError`, `ValueError`, and
    `yaml.YAMLError`, and `RecursionError` is none of those -- so it sailed
    past every one of them and reached `resolve_context` as a raw traceback
    under `--json`, exactly the escape #217 closed for a YAML syntax error
    one exception type over. Reproduced directly against this CLI:
    `runner.invoke(..., catch_exceptions=False)` itself raised
    `RecursionError` before this fix, the identical failure mode
    `test_validate_reports_a_malformed_migration_yaml_instead_of_crashing`
    above documents for `yaml.YAMLError`.
    """
    _invoke("init")
    migration = _write_deeply_nested_migration(project)

    # `catch_exceptions=False`: see the identical comment on
    # `test_validate_reports_an_unresolvable_content_file_instead_of_crashing`
    # above. A `RecursionError` escaping `runner.invoke` itself is exactly
    # what happens before this fix.
    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    assert migration.name in payload["error"]
    assert payload["remedy"]


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_validate_refuses_an_unreadable_migrations_directory_instead_of_reporting_it_empty(
    project: Path,
) -> None:
    """Before issue #214's fix: `pathlib.Path.glob` swallowed the
    `PermissionError` a `chmod 000`'d `migrations_dir` raised internally in
    its own `scandir` and yielded nothing, so `load_migrations` returned an
    *empty* set. Measured against the real CLI at the time: `migrate validate
    --json` reported `valid: true` with `migrationCount: 0` for a project
    whose migrations were never read at all -- a silent false positive, and
    the more dangerous of this class's two faces, because nothing about the
    exit code or the payload shape said anything went wrong.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.chmod(0o000)
    try:
        result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)
    finally:
        migrations_dir.chmod(0o700)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_directory_unreadable_payload(
        json.loads(result.stderr), str(migrations_dir.relative_to(project))
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_apply_refuses_an_unreadable_migrations_directory_without_seeding_a_state_database(
    project: Path,
) -> None:
    """Issue #214's worse face: before the fix, `migrate apply --json` did not
    merely misreport an unreadable migrations directory as empty, it *acted*
    on that misreading. Measured against the real CLI at the time: it created
    a state database for the empty set it wrongly believed was the whole
    story (`databaseCreated: true`, `changed: false`), instead of refusing
    before any state was ever touched -- the same "a refusal costs nothing"
    contract `test_a_refused_apply_leaves_no_database_file` below already
    pins for issue #63's own refusal, reused here for this one.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.chmod(0o000)
    try:
        result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)
    finally:
        migrations_dir.chmod(0o700)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_directory_unreadable_payload(
        json.loads(result.stderr), str(migrations_dir.relative_to(project))
    )
    # Not only "no `*.sqlite`": measured pre-fix, both `active.json`
    # (`.theurian/state/active.json`, `application/project_service.py`) and
    # the sqlite database were created, so a check scoped to the database
    # alone would have missed half of what a refused apply must not seed.
    state_dir = project / ".theurian" / "state"
    assert not state_dir.exists() or not any(state_dir.iterdir())


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_refuses_a_symlink_loop_migrations_directory_without_seeding_a_state_database(
    project: Path,
) -> None:
    """Adversarial HIGH (round two, orchestrator-measured): the CLI-level
    face of the symlink-loop pin at
    `tests/unit/test_migration_loader_errors.py::test_load_migrations_raises_migrations_directory_unreadable_error_for_a_symlink_loop`.
    A symlink chain longer than `SYMLOOP_MAX` at `.theurian/migrations` makes
    `Path.is_dir()` swallow `ELOOP` and return `False` -- the same
    convenience it already extends to `ENOENT`/`ENOTDIR` -- so a directory
    that is actually a loop, not one that never existed, used to hit the same
    `LoadedMigrations.empty()` branch a genuinely absent directory does.
    Before this fix, measured against the real CLI: `migrate apply --json`
    seeded a state database for the empty set it wrongly believed was the
    whole story, the identical worse face
    `test_apply_refuses_an_unreadable_migrations_directory_without_seeding_a_state_database`
    above pins for a permission refusal.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    theurian_dir = project / ".theurian"
    shutil.rmtree(migrations_dir)
    links = [theurian_dir / f"loop-{i}" for i in range(40)]
    migrations_dir.symlink_to(links[0])
    for index in range(len(links) - 1):
        links[index].symlink_to(links[index + 1])
    links[-1].symlink_to(links[0])

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    relative = str(migrations_dir.relative_to(project))
    assert payload["error"] == f"{relative!r} could not be listed: {os.strerror(errno.ELOOP)}"
    assert payload["remedy"] == (
        f"{relative!r} is a loop of symbolic links. Point it at a real directory, then retry."
    )
    state_dir = project / ".theurian" / "state"
    assert not state_dir.exists() or not any(state_dir.iterdir())


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_refuses_a_dangling_migrations_directory_symlink_without_seeding_a_state_database(
    project: Path,
) -> None:
    """RED (round four): the CLI-level face of
    `tests/unit/test_migration_loader_errors.py::test_load_migrations_refuses_a_dangling_migrations_directory_symlink`
    -- `.theurian/migrations` symlinked at nothing, rather than the
    symlink-*loop* the sibling test above drives. Measured directly before
    this fix: `load_migrations`'s top-of-function probe follows the dangling
    link, gets `ENOENT`, and answers `LoadedMigrations.empty()` -- the
    identical branch a directory that never existed hits -- so `migrate apply
    --json` reports `databaseCreated: true, changed: false` at exit 0 and
    creates both `.theurian/state/active.json` and a `.sqlite` database for
    the empty set it wrongly believed was the whole story, the same worse
    face `test_apply_refuses_an_unreadable_migrations_directory_without_
    seeding_a_state_database` above pins for a permission refusal and the
    symlink-loop test immediately above pins for a loop.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    shutil.rmtree(migrations_dir)
    migrations_dir.symlink_to(project / ".theurian" / "does-not-exist")

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    relative = str(migrations_dir.relative_to(project))
    assert payload["error"] == f"{relative!r} could not be listed: symbolic link target is missing"
    assert payload["remedy"] == (
        f"{relative!r} is a symbolic link whose target is missing. Restore the target or "
        f"remove the link, then retry."
    )
    state_dir = project / ".theurian" / "state"
    assert not state_dir.exists() or not any(state_dir.iterdir())


# -- round three: entry-level enumeration policy, the CLI face --------------
#
# The symlink-loop tests above drive `.theurian/migrations` *itself* being a
# loop. These two drive a loop on one *entry* inside an otherwise-healthy
# migrations directory -- see
# `tests/unit/test_migration_loader_errors.py`'s own "round three:
# entry-level enumeration policy" section for the measured mechanism
# (`Path.is_file()` silently swallows `ELOOP` per entry) and the new
# per-entry contract these RED tests specify.


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_reports_a_symlink_loop_migration_entry_instead_of_silently_dropping_it(
    project: Path,
) -> None:
    """RED (round three, orchestrator-measured): with one real migration and
    one 40-link symlink-loop *entry* on disk, `migrate validate --json`
    reports `migrationCount: 1, valid: true`, exit 0 today -- the loop entry
    is silently dropped from the count, not refused, because
    `Path.is_file()` swallows the `ELOOP` its own `stat()` raises. Stays red
    until the entry-level classification the unit-level pin at
    `tests/unit/test_migration_loader_errors.py::test_load_migrations_raises_migration_file_unreadable_error_for_a_symlink_loop_entry`
    specifies lands.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    theurian_dir = project / ".theurian"
    links = [theurian_dir / f"entry-loop-{i}" for i in range(40)]
    loop_entry = migrations_dir / "01K1LLLLLL01234567890ABCDE-loop.yaml"
    loop_entry.symlink_to(links[0])
    for index in range(len(links) - 1):
        links[index].symlink_to(links[index + 1])
    links[-1].symlink_to(links[0])

    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    relative = str(loop_entry.relative_to(project))
    assert payload["error"] == f"{relative!r} could not be read: {os.strerror(errno.ELOOP)}"
    assert payload["remedy"] == (
        f"{relative!r} is a loop of symbolic links. Point it at a real file, then retry."
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_refuses_a_symlink_loop_migration_entry_without_seeding_a_state_database(
    project: Path,
) -> None:
    """RED (round three, orchestrator-measured): the `apply`-side worse face
    of the gap the `validate` test above pins -- mirroring
    `test_apply_refuses_a_symlink_loop_migrations_directory_without_seeding_a_state_database`
    at entry granularity instead of directory granularity. Measured directly:
    `migrate apply --json` seeds a state database for the one real migration
    it did see today, silently ignoring the loop entry instead of refusing
    before any state is touched.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    theurian_dir = project / ".theurian"
    links = [theurian_dir / f"entry-loop-{i}" for i in range(40)]
    loop_entry = migrations_dir / "01K1LLLLLL01234567890ABCDE-loop.yaml"
    loop_entry.symlink_to(links[0])
    for index in range(len(links) - 1):
        links[index].symlink_to(links[index + 1])
    links[-1].symlink_to(links[0])

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    relative = str(loop_entry.relative_to(project))
    assert payload["error"] == f"{relative!r} could not be read: {os.strerror(errno.ELOOP)}"
    assert payload["remedy"] == (
        f"{relative!r} is a loop of symbolic links. Point it at a real file, then retry."
    )
    state_dir = project / ".theurian" / "state"
    assert not state_dir.exists() or not any(state_dir.iterdir())


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_status_refuses_an_unreadable_migrations_directory_instead_of_reporting_it_empty(
    project: Path,
) -> None:
    """One representative for `migrate status --json`: all three `migrate`
    subcommands share the identical `_require_project` call site
    (`cli/commands.py`), so this is the third and last member of that shared
    class, not a fourth independent bug. Before issue #214's fix, measured
    against the real CLI: `total: 0`, `stateBuilt: false`, exit 0 -- the same
    silent misreading `migrate validate` and `migrate apply` both used to give
    above.
    """
    _invoke("init")
    _write_migration(project)
    migrations_dir = project / ".theurian" / "migrations"
    migrations_dir.chmod(0o000)
    try:
        result = runner.invoke(app, ["migrate", "status", "--json"], catch_exceptions=False)
    finally:
        migrations_dir.chmod(0o700)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_directory_unreadable_payload(
        json.loads(result.stderr), str(migrations_dir.relative_to(project))
    )


def test_a_malformed_migration_names_the_offending_field(project: Path) -> None:
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-bad.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "is invalid at" in error["error"]


def test_a_naive_timestamp_is_rejected(project: Path) -> None:
    """A naive timestamp compares wrong across a DST boundary, and validity
    windows depend on those comparisons."""
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-naive.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: "2026-08-02T10:00:00"
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "offset" in error["error"] or "invalid at" in error["error"]


def test_an_unknown_api_version_is_rejected(project: Path) -> None:
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-future.yaml").write_text(
        f"""apiVersion: theurian.dev/v2
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "invalid at" in error["error"] or "apiVersion" in error["error"]


def test_an_empty_project_validates(project: Path) -> None:
    """A project with no migrations is valid, not broken."""
    _invoke("init")
    code, validated = _invoke("migrate", "validate")
    assert code == 0
    assert validated["migrationCount"] == 0


# -- issue #63: tenantId/aclGroup nothing can yet enforce -------------------


def _migration_with_scope(tenant_id: str | None = None, acl_group: str | None = None) -> str:
    lines = [f"      tenantId: {tenant_id}"] if tenant_id is not None else []
    if acl_group is not None:
        lines.append(f"      aclGroup: {acl_group}")
    insertion = "\n".join((*lines, "      sourceAnchors:"))
    return MIGRATION.replace("      sourceAnchors:", insertion)


def test_default_tenant_and_acl_group_apply_cleanly_end_to_end(project: Path) -> None:
    """Negative control: default scope is not refused, at either command."""
    _invoke("init")
    _write_migration(
        project, migration=_migration_with_scope(tenant_id="local", acl_group="default")
    )

    validate_code, _ = _invoke("migrate", "validate")
    apply_code, applied = _invoke("migrate", "apply")

    assert validate_code == 0
    assert apply_code == 0
    assert applied["applied"] == [MIGRATION_ID]


def test_validate_and_apply_refuse_an_unenforceable_tenant_identically(project: Path) -> None:
    """Issue #63's MEDIUM-1. Nothing below the CLI pinned that `migrate
    validate`'s call to `refuse_unenforceable_scope`, or `migrate apply`'s
    dedicated `except UnenforceableScopeError` clause, actually runs:
    deleting either line from `cli/commands.py` left the full test suite
    green (mutation-verified). Both assertions below must go RED if either
    is removed -- exit code and remedy text are pinned against known values,
    not only against each other, since two commands that agree by both
    falling back to the *same wrong* text would still pass an equality-only
    check.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["error"] == apply_error["error"]
    assert validate_error["remedy"] == apply_error["remedy"]
    assert "tenantId" in validate_error["remedy"]
    assert "'local'" in validate_error["remedy"]
    assert "#63" in validate_error["remedy"]
    assert validate_error["remedy"] != "Fix the migration set, then retry."


def test_a_refused_apply_leaves_no_database_file(project: Path) -> None:
    """Issue #63 LOW: the refusal is checked before `create_database`, so a
    refused `apply` costs the same as a refused `validate` -- nothing."""
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    code, _ = _invoke("migrate", "apply")

    assert code == EXIT_STATE_ERROR
    assert list((project / ".theurian/state").glob("*.sqlite")) == []


def test_status_reports_refused_ids_without_gating(project: Path) -> None:
    """Issue #63's MEDIUM-3: `migrate status` is observation, not a gate, so
    it keeps exit 0 -- but the same statically decidable property must be
    visible here too, via `refusedIds`."""
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    code, status = _invoke("migrate", "status")

    assert code == 0
    assert status["refusedIds"] == [MIGRATION_ID]
    assert status["pendingIds"] == [MIGRATION_ID]


def test_status_reports_no_refused_ids_for_a_clean_set(project: Path) -> None:
    _invoke("init")
    _write_migration(project)
    code, status = _invoke("migrate", "status")
    assert code == 0
    assert status["refusedIds"] == []


def test_an_already_applied_foreign_tenant_gets_a_remedy_that_actually_works(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #63's HIGH-1. A revision applied by an earlier build that did not
    refuse it (only possible on `0.1.0.dev0`/`0.1.0.dev1`) must not get the
    "just edit the field" remedy: editing an already-applied migration's file
    changes its checksum and trips FR-K5's tamper-evidence check instead,
    whose own remedy says to restore the file -- looping the reader between
    two contradictory errors with no documented way out.

    Simulated by disabling the refusal for one seeding `apply`, standing in
    for the earlier, unrefusing build that produced this exact state.

    The seeding patch uses `monkeypatch.context()`, a *separate* scoped
    `MonkeyPatch`, rather than calling `.undo()` on the fixture-provided
    `monkeypatch` directly: the `project` fixture above also patches through
    that same instance (`chdir` into the temp project, `setenv` for
    `THEURIAN_DATA_DIR`), and `.undo()` reverts every patch it has recorded,
    not only this test's two -- it was caught here reverting the working
    directory back to wherever pytest was invoked from mid-test, which is
    exactly the real checkout the isolation rules in this repository exist to
    keep the CLI away from. `migrate validate` never writes, so nothing was
    written by the mistake, but the harness itself must not depend on that.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    with monkeypatch.context() as seed:
        seed.setattr(
            "theurian.application.migration_engine.refuse_unenforceable_scope", lambda _ms: None
        )
        seed.setattr("theurian.cli.commands.refuse_unenforceable_scope", lambda _ms: None)
        seed_code, seeded = _invoke("migrate", "apply")
    assert seed_code == 0, "fixture setup failed: the seeding apply itself was refused"
    assert seeded["applied"] == [MIGRATION_ID]

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["remedy"] == apply_error["remedy"]
    assert ".theurian/state" in validate_error["remedy"]
    assert "FR-K4" in validate_error["remedy"]
    # Must NOT be the unapplied-case remedy: that exact text is what HIGH-1
    # found looping the reader between two contradictory errors, since
    # editing an applied migration's file trips the checksum guard instead.
    assert "then retry" not in validate_error["remedy"]

    # And the working procedure the remedy describes must actually work:
    # edit every offending field to the default, delete state, reapply.
    (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(
        _migration_with_scope(tenant_id="local")
    )
    shutil.rmtree(project / ".theurian/state")
    recovered_code, recovered = _invoke("migrate", "apply")
    assert recovered_code == 0
    assert recovered["applied"] == [MIGRATION_ID]


def test_a_never_applied_tenant_gets_the_unapplied_remedy(project: Path) -> None:
    """The other branch of `_unenforceable_scope_remedy`, pinned on its own.

    Both remedy texts mention `tenantId`, `'local'`, and issue #63 -- a check
    that only looks for those substrings cannot tell them apart, and cannot
    catch a mutant that always returns the applied-case remedy. `"then
    retry"` appears only in the unapplied text; `.theurian/state` and
    `FR-K4` appear only in the applied one.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    code, error = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    assert "then retry" in error["remedy"]
    assert ".theurian/state" not in error["remedy"]
    assert "FR-K4" not in error["remedy"]


_SECOND_MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"
_SECOND_REVISION_ID = "01K1BBBREV01234567890ABCDE"
_SECOND_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_SECOND_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.second-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.second-policy
    revisionId: {_SECOND_REVISION_ID}
    contentFile: ../knowledge/architecture/second-policy.md
    metadata:
      title: Second policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/second-policy.md
"""


def test_an_already_applied_foreign_tenant_survives_a_state_hash_shift(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #63's HIGH-1, recurred.

    `_applied_migration_ids` used to check only `database_for(current_hash)`
    -- correct only while the applied migration is the *entire* set. Adding
    one clean, pending migration afterward (issue #63's actual upgrade path,
    not an edge case) shifts the state hash (ADR-0016): the database that
    recorded the foreign tenant sits at the *old* hash's filename, which a
    current-hash-only lookup never finds. The migration then read as
    unapplied, and the routine "edit the field, then retry" remedy was
    printed -- following it edits an *applied* migration, which trips FR-K5's
    checksum guard instead, whose own remedy says to restore the file. Back
    to the scope refusal: the exact loop HIGH-1 was supposed to close.

    `test_an_already_applied_foreign_tenant_gets_a_remedy_that_actually_works`
    above did not catch this because its fixture has zero pending migrations,
    so the current hash always equals the apply-time hash and
    `database_for()` always finds the seeded database by luck.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    with monkeypatch.context() as seed:
        seed.setattr(
            "theurian.application.migration_engine.refuse_unenforceable_scope", lambda _ms: None
        )
        seed.setattr("theurian.cli.commands.refuse_unenforceable_scope", lambda _ms: None)
        seed_code, seeded = _invoke("migrate", "apply")
    assert seed_code == 0, "fixture setup failed: the seeding apply itself was refused"
    assert seeded["applied"] == [MIGRATION_ID]

    # A clean, pending migration -- this shifts the state hash (ADR-0016), so
    # `database_for(context.state_hash)` no longer names the database the
    # seeding apply above just built.
    (project / ".theurian/knowledge/architecture/second-policy.md").write_text("# Second\n")
    (project / f".theurian/migrations/{_SECOND_MIGRATION_ID}-second.yaml").write_text(
        _SECOND_MIGRATION
    )

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["remedy"] == apply_error["remedy"]
    # Must be the applied-case remedy (state-rebuild), not the unapplied one
    # -- this exact selection is what regressed.
    assert ".theurian/state" in validate_error["remedy"]
    assert "FR-K4" in validate_error["remedy"]
    assert "then retry" not in validate_error["remedy"]

    # And the procedure it describes must still work end to end, with the
    # second, clean migration also applying.
    (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(
        _migration_with_scope(tenant_id="local")
    )
    shutil.rmtree(project / ".theurian/state")
    recovered_code, recovered = _invoke("migrate", "apply")
    assert recovered_code == 0
    assert sorted(recovered["applied"]) == sorted([MIGRATION_ID, _SECOND_MIGRATION_ID])


def _contains_migration(database: Path, migration_id: str) -> bool:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT 1 FROM migration_history WHERE migration_id = ?", (migration_id,)
        ).fetchall()
    finally:
        connection.close()
    return bool(rows)


_THIRD_MIGRATION_ID = "01K1CCCCCC01234567890ABCDE"
_THIRD_REVISION_ID = "01K1CCCREV01234567890ABCDE"
_THIRD_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_THIRD_MIGRATION_ID}
createdAt: 2026-08-02T12:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.third-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.third-policy
    revisionId: {_THIRD_REVISION_ID}
    contentFile: ../knowledge/architecture/third-policy.md
    metadata:
      title: Third policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/third-policy.md
"""


def test_a_foreign_tenant_recorded_in_a_non_first_sorted_database_is_still_found(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #63's HIGH-1, round 3, MEDIUM.

    `_applied_migration_ids` must read every `theurian-state-*.sqlite` under
    `paths.state`, not only the one a glob happens to sort first -- the
    filename is a content hash, unrelated to apply order or to which
    database recorded the foreign tenant. Every other test in this file only
    ever produces *one* state database (a single seed apply; adding a
    pending migration does not create one), so `sorted(...)[0]` and
    glob-all are indistinguishable to them -- a mutant that reads only the
    first-sorted database survives the whole suite without this test.

    Two applies (a clean one alone, then the same clean one plus a foreign
    tenant together) leave two databases on disk. Which one sorts first is a
    property of the content hash, not of apply order, so the ordering this
    test needs is forced deterministically by renaming the databases after
    seeding, rather than by searching for an unlucky hash by chance.
    """
    _invoke("init")

    # Database #1: a clean migration, applied alone.
    (project / ".theurian/knowledge/architecture/second-policy.md").write_text("# Second\n")
    (project / f".theurian/migrations/{_SECOND_MIGRATION_ID}-second.yaml").write_text(
        _SECOND_MIGRATION
    )
    clean_code, clean_applied = _invoke("migrate", "apply")
    assert clean_code == 0
    assert clean_applied["applied"] == [_SECOND_MIGRATION_ID]

    # Database #2: the same clean migration plus a foreign-tenant one,
    # applied together -- a different state hash, so a second, independent
    # database that records *both* ids (nothing carries over from #1).
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))
    with monkeypatch.context() as seed:
        seed.setattr(
            "theurian.application.migration_engine.refuse_unenforceable_scope", lambda _ms: None
        )
        seed.setattr("theurian.cli.commands.refuse_unenforceable_scope", lambda _ms: None)
        seed_code, seeded = _invoke("migrate", "apply")
    assert seed_code == 0, "fixture setup failed: the seeding apply itself was refused"
    assert sorted(seeded["applied"]) == sorted([MIGRATION_ID, _SECOND_MIGRATION_ID])

    databases = sorted((project / ".theurian/state").glob("theurian-state-*.sqlite"))
    assert len(databases) == 2, "fixture must produce exactly two state databases"
    foreign_db = next(db for db in databases if _contains_migration(db, MIGRATION_ID))
    clean_db = next(db for db in databases if db != foreign_db)

    # Force the unlucky ordering: the database *without* the foreign tenant
    # sorts first, the one *with* it sorts last. A first-sorted-only glob
    # would then check only the clean one and miss the foreign migration.
    clean_db.rename(clean_db.with_name("theurian-state-000000000000.sqlite"))
    foreign_db.rename(foreign_db.with_name("theurian-state-zzzzzzzzzzzz.sqlite"))
    renamed = sorted((project / ".theurian/state").glob("theurian-state-*.sqlite"))
    assert renamed[0].name == "theurian-state-000000000000.sqlite"
    assert not _contains_migration(renamed[0], MIGRATION_ID)
    assert _contains_migration(renamed[1], MIGRATION_ID)

    # A further clean, pending migration -- shifts the state hash again, so
    # neither renamed database matches `context.state_hash` either.
    (project / ".theurian/knowledge/architecture/third-policy.md").write_text("# Third\n")
    (project / f".theurian/migrations/{_THIRD_MIGRATION_ID}-third.yaml").write_text(
        _THIRD_MIGRATION
    )

    code, error = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    # The applied-case remedy: correct only if the first-sorted-only bug is
    # NOT present. A first-sorted-only reader would check
    # theurian-state-000000000000.sqlite, find nothing, and print the
    # unapplied-case remedy instead.
    assert ".theurian/state" in error["remedy"]
    assert "FR-K4" in error["remedy"]
    assert "then retry" not in error["remedy"]


# -- issue #210: two migrations referencing one body file ------------------

_SECOND_MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"
_SECOND_REVISION_ID = "01K1BBBREV01234567890ABCDE"

#: The path the first migration already names. Written as its own constant so
#: the spelling test below can vary it without varying anything else.
_SHARED_CONTENT_FILE = "../knowledge/architecture/auth-policy.md"


def _second_migration(content_file: str = _SHARED_CONTENT_FILE) -> str:
    """A well-formed update to the item the first migration created.

    Everything about it is correct except that its ``contentFile`` is the body
    the first migration already references: the ``expectedRevision`` chain is
    right, the ids are unique, and no ``contentSha256`` is declared -- the
    unpinned, hand-authored shape issue #210 measured applying cleanly and
    recording the *second* body under the *first* revision's title and author.
    """
    return f"""apiVersion: theurian.dev/v1
id: {_SECOND_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {_SECOND_REVISION_ID}
    expectedRevision: {REVISION_ID}
    contentFile: {content_file}
    metadata:
      title: Authentication policy, revised
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


def _write_second_migration(root: Path, content_file: str = _SHARED_CONTENT_FILE) -> None:
    (root / f".theurian/migrations/{_SECOND_MIGRATION_ID}-revise.yaml").write_text(
        _second_migration(content_file)
    )


def test_validate_and_apply_refuse_two_migrations_sharing_one_body_file(project: Path) -> None:
    """Issue #210, face 1. Both commands exited 0 and the store recorded the
    second body under the first revision's title and author, self-consistently:
    the loader adopts the file's current hash where no ``contentSha256`` is
    declared, so nothing afterwards can tell the substitution happened.

    Pinned at both commands, not one: the refusal has two call sites (issue
    #36's class -- a property visible to one command and not the other), and a
    test that asked only `validate` would stay green with `apply`'s call
    deleted.
    """
    _invoke("init")
    _write_migration(project)
    _write_second_migration(project)

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["error"] == apply_error["error"]
    assert MIGRATION_ID in validate_error["error"], "the migration that names the body first"
    assert _SECOND_MIGRATION_ID in validate_error["error"], "and the one that names it again"
    assert "auth-policy.md" in validate_error["error"], "and the path they share"
    assert validate_error["remedy"] != "Fix the migration set, then retry."
    assert list((project / ".theurian/state").glob("*.sqlite")) == [], (
        "a refused apply must cost no state file, as issue #63's refusal already does"
    )


def test_two_spellings_of_one_body_file_are_still_refused(project: Path) -> None:
    """The comparison is over the resolved path, not the authored string.

    ``../knowledge/architecture/./auth-policy.md`` is the same file as
    ``../knowledge/architecture/auth-policy.md``; a string-keyed guard reports
    two distinct references and lets the set through. The loader already
    resolves this path -- against the migrations directory, symlinks followed
    -- to read the body at all, so the resolved form is what the refusal
    compares.
    """
    _invoke("init")
    _write_migration(project)
    _write_second_migration(project, content_file="../knowledge/architecture/./auth-policy.md")

    code, error = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    assert _SECOND_MIGRATION_ID in error["error"]


def test_two_migrations_naming_two_body_files_are_not_refused(project: Path) -> None:
    """The negative control. Refusing every second migration would pass the
    two tests above while breaking the ordinary case they exist to protect."""
    _invoke("init")
    _write_migration(project)
    (project / ".theurian/knowledge/architecture/auth-policy.revised.md").write_text(
        "# Authentication policy, revised\n\nEvery call carries a signed token.\n"
    )
    _write_second_migration(
        project, content_file="../knowledge/architecture/auth-policy.revised.md"
    )

    validate_code, validated = _invoke("migrate", "validate")
    apply_code, applied = _invoke("migrate", "apply")

    assert validate_code == 0
    assert validated["valid"] is True
    assert apply_code == 0
    assert applied["applied"] == [MIGRATION_ID, _SECOND_MIGRATION_ID]


def test_status_reports_a_body_sharing_migration_without_gating(project: Path) -> None:
    """Issue #210 on `migrate status`: observation, not a gate.

    The body-sharing migration is named under `refusedIds` -- exactly as the
    tenant/ACL rule already is -- while the command keeps exit 0. Before this,
    `status` reported `refusedIds: []` for a set `validate`/`apply` exit 4 on,
    so the property the gating commands refuse went invisible on the one command
    whose contract is to keep reporting.
    """
    _invoke("init")
    _write_migration(project)
    _write_second_migration(project)

    code, status = _invoke("migrate", "status")

    assert code == 0, "status observes, it does not gate"
    assert _SECOND_MIGRATION_ID in status["refusedIds"], "the later migration validate/apply refuse"
    assert _SECOND_MIGRATION_ID in status["pendingIds"], "and it is still reported as pending"


#: An in-place status change (ADR-0024 decision 5): the item's *current* revision
#: id, re-declared against its own body, changing only ``status``. The revision
#: id does not move and the body is the same file, so the two operations share an
#: identity but name one revision -- a legitimate no-op re-declaration (FR-K8),
#: not one file backing two revisions. No ``expectedRevision``: it does not
#: advance the item's revision, it restates it.
_INPLACE_STATUS_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_SECOND_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: {_SHARED_CONTENT_FILE}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""


def test_an_in_place_status_change_is_not_reported_as_body_sharing(project: Path) -> None:
    """The shape the body-sharing refusal must let through, end to end (#210).

    The second migration re-declares the first revision's own body to change its
    status: same body file, same revision id. The identity collides but the
    revision id does not, so this is a re-declaration, not a collision. `status`
    must keep exit 0 and *not* name it under `refusedIds` -- the enumerator's
    revision-id guard, untested until now -- and `validate` must not refuse it,
    the throwing form's guard at the CLI edge.
    """
    _invoke("init")
    _write_migration(project)
    (project / f".theurian/migrations/{_SECOND_MIGRATION_ID}-reject.yaml").write_text(
        _INPLACE_STATUS_MIGRATION
    )

    status_code, status = _invoke("migrate", "status")
    validate_code, validated = _invoke("migrate", "validate")

    assert status_code == 0, "status observes, it does not gate"
    assert _SECOND_MIGRATION_ID not in status["refusedIds"], (
        "re-declaring a revision's own body is a no-op, not one file backing two revisions"
    )
    assert validate_code == 0, "and the gating command must not refuse the re-declaration either"
    assert validated["valid"] is True


# -- issue #210: the re-key -- identity, not the path string ----------------
#
# The refusal above keys on filesystem identity (`st_dev`/`st_ino`), not on the
# resolved path string. A case-insensitive filesystem (APFS, NTFS) reaches one
# physical file through many spellings, so a string key let a second revision
# slip a case-variant spelling past the refusal and cross-record a withheld body
# through an approved item's index. These prove the identity key against the real
# loader; the pure-comparison faces are in `test_migration_engine.py`.

_KNOWLEDGE_DIR = ".theurian/knowledge/architecture"


def _temp_dir_is_case_insensitive(root: Path) -> bool:
    """Whether *this* checkout's filesystem collapses case, decided at runtime.

    APFS and NTFS do; a case-sensitive Linux volume does not, and there the
    case-variant collapse cannot occur, so the tests that depend on it skip
    rather than false-fail.
    """
    probe = root / _KNOWLEDGE_DIR / "_case_probe.md"
    probe.write_text("x")
    try:
        return (probe.parent / "_CASE_PROBE.MD").exists()
    finally:
        probe.unlink()


def _temp_dir_collapses_nfc_nfd(root: Path) -> bool:
    """Whether an NFC name and its NFD spelling reach one inode here (APFS does)."""
    name = "café_probe.md"
    nfc = root / _KNOWLEDGE_DIR / unicodedata.normalize("NFC", name)
    nfc.write_text("x")
    try:
        nfd = root / _KNOWLEDGE_DIR / unicodedata.normalize("NFD", name)
        return nfd.exists() and nfc.stat().st_ino == nfd.stat().st_ino
    finally:
        nfc.unlink()


def test_a_hardlinked_second_name_for_one_body_is_refused(project: Path) -> None:
    """Issue #210's re-key, the cross-platform proof: two distinct paths, one
    inode. A path-string key sees two references and lets the set cross; the
    identity key sees one file and refuses. A hardlink needs no case-insensitive
    filesystem, so this is the non-negotiable driving test -- it runs everywhere.
    """
    _invoke("init")
    _write_migration(project)
    original = project / _KNOWLEDGE_DIR / "auth-policy.md"
    os.link(original, project / _KNOWLEDGE_DIR / "auth-policy-hardlink.md")
    _write_second_migration(
        project, content_file="../knowledge/architecture/auth-policy-hardlink.md"
    )

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, _ = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert _SECOND_MIGRATION_ID in validate_error["error"], "the migration that shares the inode"
    assert list((project / ".theurian/state").glob("*.sqlite")) == [], (
        "a refused apply must cost no state file"
    )


@pytest.mark.parametrize(
    "spelling",
    [
        "../knowledge/architecture/auth-policy.MD",
        "../knowledge/Architecture/auth-policy.md",
        "../KNOWLEDGE/ARCHITECTURE/AUTH-POLICY.MD",
    ],
)
def test_a_case_variant_spelling_of_one_body_cannot_bypass_the_refusal(
    project: Path, spelling: str
) -> None:
    """The bypass the re-key closes. Each spelling reaches the very file the
    first migration already names -- an uppercase extension, a case-variant
    directory, all-uppercase -- and each `resolve()`s to a *distinct* string.
    A string-keyed guard crossed all three (measured); the identity key refuses
    all three, because one inode backs both revisions."""
    _invoke("init")
    _write_migration(project)
    if not _temp_dir_is_case_insensitive(project):
        pytest.skip("this filesystem is case-sensitive; the case-variant collapse cannot occur")
    _write_second_migration(project, content_file=spelling)

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, _ = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR, f"{spelling} crossed validate"
    assert apply_code == EXIT_STATE_ERROR, f"{spelling} crossed apply"
    assert _SECOND_MIGRATION_ID in validate_error["error"]
    assert list((project / ".theurian/state").glob("*.sqlite")) == []


def test_an_nfc_nfd_spelling_of_one_body_cannot_bypass_the_refusal(project: Path) -> None:
    """The Unicode face of the same collapse. The body is named once; the two
    migrations reach it through an NFC spelling and its NFD equivalent, which
    APFS resolves to one inode and Python leaves as distinct strings."""
    _invoke("init")
    if not _temp_dir_collapses_nfc_nfd(project):
        pytest.skip("this filesystem keeps NFC and NFD distinct")

    name = "café-policy.md"
    body_path = project / _KNOWLEDGE_DIR / unicodedata.normalize("NFC", name)
    body_path.write_text(BODY)
    first = MIGRATION.replace(
        "contentFile: ../knowledge/architecture/auth-policy.md",
        f"contentFile: ../knowledge/architecture/{unicodedata.normalize('NFC', name)}",
    )
    (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(first)
    _write_second_migration(
        project, content_file=f"../knowledge/architecture/{unicodedata.normalize('NFD', name)}"
    )

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, _ = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert _SECOND_MIGRATION_ID in validate_error["error"]


# The disclosure the re-key closes: a status-gate-boundary crossing. An approved,
# public item and a rejected, restricted item share one body file through a
# case-variant spelling. The status gate withholds the rejected item directly,
# but the shared body would be served through the *approved* item's published
# index -- unless the set is refused before it is ever applied.
_DISCLOSURE_APPROVED_MIGRATION_ID = "01K1DAAAAA01234567890ABCDE"
_DISCLOSURE_APPROVED_REVISION_ID = "01K1DAAREV01234567890ABCDE"
_DISCLOSURE_REJECTED_MIGRATION_ID = "01K1DBBBBB01234567890ABCDE"
_DISCLOSURE_REJECTED_REVISION_ID = "01K1DBBREV01234567890ABCDE"

_DISCLOSURE_APPROVED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_DISCLOSURE_APPROVED_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.public-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.public-policy
    revisionId: {_DISCLOSURE_APPROVED_REVISION_ID}
    contentFile: ../knowledge/architecture/shared.md
    metadata:
      title: Public policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sensitivity: public
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/shared.md
"""

_DISCLOSURE_REJECTED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_DISCLOSURE_REJECTED_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.secret-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.secret-policy
    revisionId: {_DISCLOSURE_REJECTED_REVISION_ID}
    contentFile: ../knowledge/architecture/SHARED.MD
    metadata:
      title: Secret policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sensitivity: restricted
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/shared.md
"""


def test_a_rejected_restricted_body_cannot_reach_an_approved_index_via_a_case_variant(
    project: Path,
) -> None:
    """The security proof for issue #210's re-key (disclosure regression).

    An `approved`/`public` item and a `rejected`/`restricted` item name one body
    file through two case-variant spellings. On the pre-fix, path-string-keyed
    guard the set applies at exit 0, and the rejected body -- withheld from
    direct retrieval by the status gate -- is served through the approved item's
    published index. The re-key refuses the set at both `validate` and `apply`,
    before any index can be built, closing the crossing at its source.
    """
    _invoke("init")
    if not _temp_dir_is_case_insensitive(project):
        pytest.skip("this filesystem is case-sensitive; the case-variant collapse cannot occur")

    (project / _KNOWLEDGE_DIR / "shared.md").write_text(BODY)
    (project / f".theurian/migrations/{_DISCLOSURE_APPROVED_MIGRATION_ID}-public.yaml").write_text(
        _DISCLOSURE_APPROVED_MIGRATION
    )
    (project / f".theurian/migrations/{_DISCLOSURE_REJECTED_MIGRATION_ID}-secret.yaml").write_text(
        _DISCLOSURE_REJECTED_MIGRATION
    )

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR, "the rejected body must not reach an approved index"
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["error"] == apply_error["error"]
    assert _DISCLOSURE_REJECTED_REVISION_ID in validate_error["error"]
    assert _DISCLOSURE_APPROVED_REVISION_ID in validate_error["error"]
    assert list((project / ".theurian/state").glob("*.sqlite")) == [], (
        "a refused apply must cost no state file -- the crossing is stopped before any build"
    )


# -- issue #210: an upsertRevision carrying no contentSha256 ---------------


def _pinned_migration() -> str:
    """The same migration, with the pin the schema calls optional."""
    digest = hashlib.sha256(BODY.encode("utf-8")).hexdigest()
    return MIGRATION.replace(
        f"    contentFile: {_SHARED_CONTENT_FILE}\n",
        f"    contentFile: {_SHARED_CONTENT_FILE}\n    contentSha256: {digest}\n",
    )


def test_validate_warns_about_a_revision_that_pins_no_body_digest(project: Path) -> None:
    """``contentSha256`` is optional and nothing recommended it (issue #210).

    A warning rather than a refusal: both shipped example migrations are
    unpinned and 21 of the 22 test files naming ``upsertRevision`` never mention
    the field, so requiring it is a schema decision with a measured cost, taken
    in Milestone 7. What `validate` can do now is stop being silent -- an
    unpinned body is the one whose out-of-band edit nothing detects.

    Exit 0 is asserted first: a warning that refuses is a refusal.
    """
    _invoke("init")
    _write_migration(project)

    code, validated = _invoke("migrate", "validate")

    assert code == 0
    assert validated["valid"] is True
    warned = validated["unpinnedRevisions"]
    assert len(warned) == 1
    assert REVISION_ID in warned[0], "the revision whose body nothing pins"
    assert MIGRATION_ID in warned[0], "the file the author has to edit"
    assert "auth-policy.md" in warned[0], "and the body whose digest to take"


def test_validate_says_nothing_about_a_revision_that_pins_its_body(project: Path) -> None:
    """The negative control: a field that is always non-empty is not a warning."""
    _invoke("init")
    _write_migration(project, migration=_pinned_migration())

    code, validated = _invoke("migrate", "validate")

    assert code == 0
    assert validated["unpinnedRevisions"] == []


def test_the_unpinned_warning_reaches_the_human_output_too(project: Path) -> None:
    """`--json` is the plugin's channel; a person reading the default output
    must see the same warning, or the two disagree about the same project."""
    _invoke("init")
    _write_migration(project)

    result = runner.invoke(app, ["migrate", "validate"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "unpinnedRevisions" in result.stdout
    assert REVISION_ID in result.stdout


def test_the_unpinned_warning_names_a_shasummable_path_and_a_non_fatal_remedy(
    project: Path,
) -> None:
    """Issue #210's remedy loop. The warning fires on already-applied migrations
    too, and the naive cure -- add `contentSha256` to the migration -- is fatal
    there: editing an applied migration trips FR-K5's checksum guard, whose own
    remedy says to restore the file, looping the reader (issue #63's HIGH-1
    shape). And the body path must be one the reader can actually `shasum` from
    the repository root -- the authored `../knowledge/...` is relative to the
    migration file and shasums to nothing there.
    """
    _invoke("init")
    _write_migration(project)
    apply_code, _ = _invoke("migrate", "apply")
    assert apply_code == 0, "the migration is applied, so its warning must give the applied remedy"

    code, validated = _invoke("migrate", "validate")

    assert code == 0, "an unpinned body is a warning, not a refusal, even once applied"
    warning = validated["unpinnedRevisions"][0]
    # The body path the reader shasums, resolved from the repository root -- not
    # the authored path, which is relative to the migration file.
    assert ".theurian/knowledge/architecture/auth-policy.md" in warning
    assert "../knowledge/architecture/auth-policy.md" not in warning, (
        "authored path is un-shasummable"
    )
    # A non-empty remedy that does not stop at the fatal "edit the applied migration".
    assert "shasum" in warning, "the digest command the reader runs"
    assert ".theurian/state/" in warning, "the applied-case escape, not just 'add the pin'"


# ==========================================================================
# ingest
# ==========================================================================


MARKDOWN_DOC = """---
status: approved
reviewers: [alice]
---

# Authentication policy

Every call carries a signed token.
"""

OPENAPI_DOC = """openapi: 3.1.0
info:
  title: Orders API
  version: "1.0"
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        "200": {description: OK}
"""


def _write_sources(root: Path) -> None:
    (root / ".theurian/knowledge/architecture/auth.md").write_text(MARKDOWN_DOC)
    (root / ".theurian/specifications/api.yaml").write_text(OPENAPI_DOC)


def test_ingest_normalizes_every_format(project: Path) -> None:
    _invoke("init")
    _write_sources(project)

    code, report = _invoke("ingest")

    assert code == 0
    assert report["ingested"] == 2
    assert report["succeeded"]
    assert {d["parser"] for d in report["documents"]} == {"markdown", "openapi"}


def test_ingest_is_incremental(project: Path) -> None:
    """Touching a file without changing it costs one hash, not a reparse."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    code, second = _invoke("ingest")

    assert code == 0
    assert second["ingested"] == 0
    assert second["unchanged"] == 2


def test_ingest_reports_a_governed_front_matter_key(project: Path) -> None:
    """ADR-0019: a silently ignored `status: approved` is the case where an
    author believes something is approved and it is not."""
    _invoke("init")
    _write_sources(project)

    _, report = _invoke("ingest")

    codes = {w["code"] for w in report["warnings"]}
    assert codes == {"front-matter-governed-field"}


def test_a_parse_failure_is_reported_without_losing_the_rest(project: Path) -> None:
    _invoke("init")
    _write_sources(project)
    (project / ".theurian/specifications/broken.yaml").write_text("key: [unclosed\n")

    code, report = _invoke("ingest")

    assert code == EXIT_STATE_ERROR, "a partial run is not a clean run"
    assert report["ingested"] == 2, "the good documents still got in"
    assert report["failed"] == 1


def test_a_corrupt_manifest_costs_a_reparse_not_a_failure(project: Path) -> None:
    """The manifest is a derived cache. Refusing to run would let a disposable
    file block the command."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    (project / ".theurian/cache/ingestion.json").write_text("{ not json")

    code, report = _invoke("ingest")

    assert code == 0
    assert report["ingested"] == 2


def test_ingest_writes_a_manifest_under_the_derived_cache(project: Path) -> None:
    """ADR-0004: the manifest is derived, so it belongs somewhere git-ignored."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    manifest = project / ".theurian/cache/ingestion.json"
    assert manifest.is_file()
    assert ".theurian/cache/" in (project / ".gitignore").read_text()
