"""CLI commands, invoked in-process.

The e2e suite runs the installed binary and proves packaging works. These run
the same commands through Typer's runner: faster, measurable by coverage, and
able to assert on the exact JSON a caller receives.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from hang_guard import CAN_INTERRUPT_A_HANG, fails_rather_than_hanging
from migration_fixtures import UNREACHED_BODY_PIN, body_pin
from registry_deletion_cure_claims import (
    THE_PINNED_CURE_LEADS,
    THE_SHARED_CURE_TAIL,
    assert_a_deletion_cure_does_not_claim_it_is_costless,
    assert_registry_deletion_cure_shape,
    the_pinned_cure,
    the_pinned_lead,
)
from typer.testing import CliRunner

from theurian.application.project_service import (
    ACTIVE_POINTER_REMEDY,
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    ProjectError,
    ProjectPaths,
    ProjectRegistry,
    derived_escape_remedy,
    read_active_state,
)

# Private, and imported rather than transcribed for the reason every other
# read-it-from-production pin in this file is: a literal here would go on
# agreeing with itself after the shipped default moved, which is the drift the
# assertion exists to catch. Importing a private whose value *is* the subject is
# the established shape in this package -- `_PROVENANCE_REMEDY` in
# `test_findings_build_cli.py`, `_ACCEPT_STEPS`/`_DRAFT_STEPS` in
# `test_propose_cli.py`. Re-locate rather than trusting those names:
#
#   git grep -nE '^from theurian\.[a-z_.]+ import _' -- packages/theurian-core/tests
from theurian.cli.commands import _UNRESOLVED_STATUS_REMEDY
from theurian.cli.context import resolve_context, schema_root
from theurian.cli.index_status_report import index_staleness
from theurian.cli.main import app
from theurian.domain.errors import MigrationError
from theurian.domain.extras import DAEMON_REINSTALL, DAEMON_REINSTALL_COMMANDS

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_STATE_ERROR = 4

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_auth_rotate.py` and `test_setup_journal.py` use before
#: a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: Issue #215's reproduction needs a FIFO to build one and a timer to keep a
#: regression from stalling the suite instead of failing it (``hang_guard``).
_CAN_MAKE_A_BLOCKING_FILE = hasattr(os, "mkfifo") and CAN_INTERRUPT_A_HANG

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
    contentSha256: {body_pin(BODY)}
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

#: A second, independent migration over its own item, so #116's delete-detection
#: test can remove *one* applied migration and leave a non-empty set behind --
#: the realistic tampering, not the degenerate "delete the only migration" case.
#: Named apart from :data:`SECOND_MIGRATION` below, which revises the *same* item
#: and would collide as a module constant.
RATE_LIMIT_MIGRATION_ID = "01K1CCCCCC01234567890ABCDE"
RATE_LIMIT_REVISION_ID = "01K1CCCREV01234567890ABCDE"
RATE_LIMIT_BODY = "# Rate limiting\n\nEvery endpoint carries a request budget.\n"

RATE_LIMIT_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {RATE_LIMIT_MIGRATION_ID}
createdAt: 2026-08-02T10:05:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.rate-limit
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.rate-limit
    revisionId: {RATE_LIMIT_REVISION_ID}
    contentFile: ../knowledge/architecture/rate-limit.md
    contentSha256: {body_pin(RATE_LIMIT_BODY)}
    metadata:
      title: Rate limiting
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/rate-limit.md
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


def _invoke_argv(words: list[str]) -> tuple[int, dict[str, Any]]:
    """Run exactly these words and parse the JSON they produce.

    The two streams staying apart matters: the CLI keeps stdout a clean machine
    channel and puts errors on stderr, and a test that merged them could not
    tell. ``CliRunner`` separates them, so the fallback below reads
    ``result.stderr`` first and only then ``result.stdout``.

    Split out of :func:`_invoke` so a caller that has to place ``--json``
    somewhere other than last can still use one parse
    (:func:`_following_the_unregister_cure`). Nothing here reaches a shell: the
    words are handed to Typer's runner as argv.
    """
    result = runner.invoke(app, words, catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command with ``--json`` appended, and parse its JSON."""
    return _invoke_argv([*args, "--json"])


def _write_migration(root: Path, migration: str = MIGRATION, body: str = BODY) -> None:
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(migration)


#: The two migrations ``_write_cyclic_migrations`` makes depend on each other, so
#: ``MigrationSet.ordered`` can find no application order. Crockford base32, like
#: every ULID in this file: no ``I``, ``L``, ``O`` or ``U``.
CYCLE_FIRST_ID = "01K1EAAAAA01234567890ABCDE"
CYCLE_SECOND_ID = "01K1EBBBBB01234567890ABCDE"

#: The path #116's test deletes -- a module constant so the write and the unlink
#: cannot drift on the filename spelling.
RATE_LIMIT_MIGRATION_PATH = f".theurian/migrations/{RATE_LIMIT_MIGRATION_ID}-add-rate-limit.yaml"


def _write_rate_limit_migration(root: Path) -> None:
    (root / ".theurian/knowledge/architecture/rate-limit.md").write_text(RATE_LIMIT_BODY)
    (root / RATE_LIMIT_MIGRATION_PATH).write_text(RATE_LIMIT_MIGRATION)


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
    contentSha256: {UNREACHED_BODY_PIN}
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


# -- issue #215: a contentFile that is a FIFO ------------------------------

#: The body path `MIGRATION` already names, made a FIFO instead of a file. Reusing
#: that migration verbatim is the point: nothing about the *document* is unusual,
#: so the only thing a reviewer could catch is the shape of a file on disk.
#:
#: Not reachable through a plain `git clone`, though, and an earlier version of
#: this comment said it was: Git versions no FIFO -- its tree modes are 100644,
#: 100755, 120000, 160000 and 040000 -- so placing one takes local write access
#: to the working tree, which is T-1's actor and what #215's own issue body
#: records. What the guard buys against that actor is the refusal below in place
#: of a hang, and a hang cannot even be graded.
_FIFO_CONTENT_FILE = ".theurian/knowledge/architecture/auth-policy.md"

#: What the refusal is allowed to name: the migration file, which `iterdir()`
#: produced. Never `MIGRATION`'s own `contentFile` value, which the author wrote.
_FIFO_MIGRATION_FILE = f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"


def _write_fifo_content_migration(root: Path) -> None:
    body = root / _FIFO_CONTENT_FILE
    body.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(body)
    (root / _FIFO_MIGRATION_FILE).write_text(MIGRATION)


def _assert_fifo_refusal_payload(payload: dict[str, Any]) -> None:
    """Full equality, the same anti-mutation shape as its siblings above.

    The name in both halves is the *migration file*, attached by
    `_parse_upsert`. `read_source_file` publishes no path at all: its argument is
    the author's own string, and echoing it is what
    `tests/unit/test_path_security.py::
    test_no_reachable_refusal_branch_echoes_the_attacker_supplied_path` forbids.
    """
    assert payload["error"] == (
        f"{_FIFO_MIGRATION_FILE!r} names a file that is a named pipe (FIFO), not a regular file"
    )
    assert payload["remedy"] == (
        f"Replace the file {_FIFO_MIGRATION_FILE!r} names with a regular file, then retry. "
        f"The size Theurian checks before it opens a file bounds nothing about what a read "
        f"of a named pipe (FIFO) returns, so it is refused unread."
    )
    assert _FIFO_CONTENT_FILE not in payload["error"], (
        "the path handed to read_source_file stays unechoed"
    )
    assert _FIFO_CONTENT_FILE not in payload["remedy"], (
        "the path handed to read_source_file stays unechoed"
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


def test_init_creates_the_local_proposal_directory_without_marking_it(project: Path) -> None:
    """ADR-0028's last owed item: the command must not commit what it just hid.

    Git does not carry an empty directory, which is why three of this layout's
    directories get a ``.gitkeep``. ``.theurian/proposals-local/`` must not be a
    fourth: the placeholder would be a tracked path inside the one directory a
    clone is supposed to arrive without, put there by the command that created
    it. The reason is already written beside the loop for the derived
    directories, and it lands the same way for an ignored directory that is not
    derived.

    The tracked proposal directory's own ``.gitkeep`` is asserted beside it, so
    this cannot pass because the marking loop stopped running altogether -- and
    ``createdPaths`` is checked as well as the disk, because that list is what
    ``setup`` shows the operator as the record of what it wrote.
    """
    code, payload = _invoke("init")

    assert code == 0, payload
    assert (project / ".theurian/proposals-local").is_dir(), "init creates the directory"
    assert not (project / ".theurian/proposals-local/.gitkeep").exists()
    assert (project / ".theurian/proposals/.gitkeep").is_file(), "the marking loop still runs"
    assert ".theurian/proposals-local" in payload["createdPaths"]
    assert not [
        path for path in payload["createdPaths"] if path.endswith("proposals-local/.gitkeep")
    ]


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


# -- issue #287: InputTooLargeError never set its own remedy ----------------


def test_an_oversized_content_file_is_diagnosed_by_its_own_remedy(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #287: ``InputTooLargeError`` never set ``.remedy`` (``domain/errors.py``),
    so ``_context_remedy`` (``cli/commands.py``) found nothing to prefer and fell
    through to ``_require_project``'s generic default -- "Run this inside an
    initialised Theurian project." -- printed to a user who was already inside
    one, whose actual problem was a ``contentFile`` too large to read.

    ``InputTooLargeError`` is a ``SecurityError``, not a ``MigrationError`` or a
    ``PathEscapeError``, so it skips both of ``_require_project``'s type-keyed
    branches -- named by symbol rather than by line, which the next edit rots --
    and lands in the generic ``except TheurianError`` clause instead. This
    exercises
    that through the real CLI on the real load path every ``_require_project``
    caller shares -- reading a migration's ``contentFile`` via
    ``read_source_file`` -- rather than calling ``_context_remedy`` directly:
    what matters is the diagnosis the command actually prints, and a synthetic
    call could pass while the real wiring between the loader and the CLI stayed
    broken.
    """
    _invoke("init")
    monkeypatch.setattr("theurian.security.paths.MAX_SOURCE_FILE_BYTES", 16)
    _write_migration(project, body="x" * 64)

    code, payload = _invoke("migrate", "validate")

    assert code == 1, (
        "a SEC-8 input cap exits 1, not EXIT_STATE_ERROR, on the same load path that "
        "grades a containment refusal 4 -- the split EXIT_STATE_ERROR's own note records"
    )
    assert payload["remedy"] != "Run this inside an initialised Theurian project.", (
        "the diagnosis must name the actual problem -- an oversized input -- "
        "not the generic project-resolution fallback"
    )
    assert "too large" in payload["remedy"]
    assert "shrink" in payload["remedy"] or "split" in payload["remedy"], (
        "the remedy must tell the user how to fix it, not only that it failed"
    )


def test_a_build_that_cannot_find_its_schemas_is_diagnosed_by_its_own_remedy(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #529, and the same shape as #287 above: no ``.remedy`` on the raise.

    ``schema_root`` (``cli/context.py``) refuses when neither candidate location
    exists -- "This build is incomplete; reinstall theurian" -- and set no
    ``remedy``, so ``_context_remedy`` found nothing to prefer and fell through
    to ``_require_project``'s generic default. Every one of that function's
    callers therefore answered a broken *installation* with "Run this inside an
    initialised Theurian project.", printed to somebody standing in one.
    Measured through the real CLI with both candidate directories moved aside:
    ``migrate validate --json`` and ``index status --json`` published it.

    ``migrate validate`` is the representative command on purpose rather than
    for coverage: `doctor`'s ``initial-index`` step names it as the thing to run
    when a migration set could not be read, so this is where a truthful step
    routed the operator into the one sentence that misdirects them.

    Provoked at the production predicate ``schema_root`` consults, so the real
    raise runs -- a monkeypatched ``schema_root`` would prove only that the CLI
    prints whatever it is handed. The refusal is asserted first, so a patch that
    failed to provoke it fails here instead of passing for the wrong reason.
    """
    _invoke("init")
    monkeypatch.setattr("theurian.cli.context._schema_candidate_exists", lambda _c: False)
    with pytest.raises(ProjectError):
        schema_root()

    code, payload = _invoke("migrate", "validate")

    assert code == 1
    assert payload["remedy"] != "Run this inside an initialised Theurian project.", (
        "the diagnosis must name the broken installation, not send someone who is "
        "already inside a project to go and initialise one"
    )
    assert payload["remedy"] == DAEMON_REINSTALL, (
        "and it is the shared reinstall remedy, so a raised `requires-python` floor "
        "reaches this sentence through `test_daemon_extra.py`'s sweep"
    )
    for installer in DAEMON_REINSTALL_COMMANDS:
        assert installer in payload["remedy"], "uv and pipx, since both are offered elsewhere"


def test_status_outside_a_repository_reports_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, status = _invoke("project", "status")
    assert code == 0, "status must report, not fail, outside a project"
    assert status["registered"] is False, (
        "`not` cannot tell `False` from `None`, and this field publishes all three"
    )


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


# -- issue #381: the unresolved branch has to name the registry failure too --
#
# `_unresolved_status` publishes one `reason`, and it is the *resolution*
# failure's. `resolve_context` loads and validates the migrations before it asks
# the registry which project this root is, so a broken migration raises first --
# and when the registry was *also* unreadable, the payload up to `2d3c23bb`
# paired a `registered: null` with migration prose and said nothing at all about
# the file that produced the null. The reader was told a project's registration
# could not be established and handed a cure for a YAML file.
#
# The rule these four tests hold is two additive keys, `registryReason` and
# `registryRemedy`, published whenever this branch's own `_read_registry()` comes
# back with a failure *and* the directory is inside a Git working tree. Outside
# one, `registered` is a literal `False` that no registry could contradict, and
# `project list` owns reporting the file.
#
# One test per arm: the compound payload, where nothing named the registry at
# all; the registry-only payload, where the right prose was already published
# under a name that does not say which of two files it is about; and the two
# fences -- every state in which the read *succeeded*, and every directory
# outside a working tree -- where the keys must not appear.
#
# `remedy` is deliberately unasserted in the compound test. Its absence there is
# issue #384's face -- `_unresolved_status` copies `exc.remedy`, and a malformed
# YAML migration raises without one -- and pinning the whole payload here would
# make this test fail when #384 lands beside it.

#: The whole-file corruption used by the tests below. One shape rather than
#: `REGISTRY_CORRUPTIONS`' three: what varies between those three is the
#: message, which the `project list` and `project status` tests above already
#: pin. What varies here is which *key* carries it.
_UNPARSEABLE_REGISTRY = b'{"demo": {"rootPath"'

#: The invocation every arm of the registry cure must keep. Owned as a property
#: by `registry_deletion_cure_claims`, whose
#: `assert_a_deletion_cure_names_the_way_back` holds it over the cure itself, and
#: restated here rather than imported, because "these payloads carry this exact
#: string" is a claim about the CLI surface and this file is where that surface
#: is read: an edit to the shared constant alone must not be able to relax an
#: assertion about what a caller receives.
#:
#: That restatement is why the enumerating search has to take **both**
#: spellings -- this file's literals and the reads of a constant named
#: `RE_REGISTER_INVOCATION`, defined here and in `registry_deletion_cure_claims`
#: and imported from the latter by `test_project_registry_errors`::
#:
#:     git grep -n -e "re-register each project" -e RE_REGISTER_INVOCATION \
#:         packages/theurian-core/tests
#:
#: One form, spelled the same way here, in `registry_deletion_cure_claims` and in
#: `_HOW_TO_RECOVER_FROM_THE_DELETION`'s own note in
#: `application/project_service.py`. The three used to disagree -- two of them
#: matched `re-register each project with`, which misses the wrapped literals
#: whose `with` is on the next source line, and returned a different line total
#: for the same population.
#:
#: **Scope: `packages/theurian-core/tests`.** A third spelling of the invocation
#: lives in `src` -- `_THE_RE_REGISTRATION_INVOCATION` below in this same file's
#: subject, `cli/commands.py` -- and is deliberately outside this key. It is not
#: another pin of the cure: it is the antecedent check for the sentence
#: `_registry_cure_in_repair_order` appends, and it raises at runtime when the
#: cure stops carrying an invocation, so it defends itself.
#:
#: The population is the *assertions* in that output, by either spelling: 12 of
#: the 40 lines it returns at the commit this note lands in, eight of them in this
#: file. The literal alone is not the key -- it finds none of the constant reads,
#: which is six of those twelve. Re-run it **after `git add`** rather than trusting
#: either number: `git grep` reads tracked files, so a count taken beside a
#: still-untracked test file is a count of the tree the commit is not landing,
#: and this denominator was one short for exactly that reason. Both move with
#: every test added, including the one added beside the note.
RE_REGISTER_INVOCATION = "re-register each project with `theurian project register`"

#: The sentence `_registry_cure_in_repair_order` appends to the registry cure on
#: the unresolved branch, byte for byte.
#:
#: Two cures in one payload need an order, and this one's is not the order a
#: reader takes them in: the registry cure ends by telling them to run `theurian
#: project register`, and on the compound arm that command refuses on the *other*
#: failure named in the same payload. So the destructive half would be followed
#: and the recovery half would not run. Appended at the emit site rather than
#: inside the shared cure, because `project list` renders that cure with no
#: `reason` key beside it for the sentence to point at.
#:
#: **Every claim it makes sits inside its own conditional**, which is what
#: `30e460b9`'s wording did not. That sentence said `register` refuses "while
#: this repository's project cannot be resolved" outside the `if`, and in the
#: registry-only arm -- where the registry failure *is* the resolution failure --
#: that reads as a warning against the cure's own last step. Measured against the
#: real CLI on an unparsable registry with healthy migrations, following the cure
#: (`rm projects.json`, then `theurian project register`) exits 0;
#: `test_register_refuses_on_the_other_failure_the_repair_order_sentence_names`
#: below is the compound arm, where it exits 1.
THE_REPAIR_ORDER_SENTENCE = (
    "If `reason` names a different failure, fix that one first -- while it stands, `theurian "
    "project register` fails on it too, so the re-registration above cannot run."
)


def test_status_names_the_registry_failure_a_broken_migration_would_otherwise_hide(
    project: Path, registry_path: Path
) -> None:
    """Issue #381. Two faults, one `reason`, and the wrong one won.

    Measured at ``2d3c23bb``, this exact state published ``{"reason": <the
    migration parse error>, "registered": null, "unreadable": []}`` at exit 0 --
    three keys, none of which mentions the registry. A caller saw membership
    withheld and every published word explaining a migration file, so the one
    action that would restore the answer was named nowhere.

    ``registryReason`` and ``registryRemedy`` are additive: the migration
    ``reason`` stays exactly what it was, because it is still true and is still
    what ``migrate validate`` will report. The defect was never that ``reason``
    was wrong; it was that it was the *only* thing said.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_malformed_yaml_migration(project)
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    code, payload = _invoke("project", "status")

    assert code == 0, "a compound failure is still a status, not a crash"
    assert payload["registered"] is None, "the registry cannot say, and False would be a guess"
    assert MALFORMED_YAML_MIGRATION_FILENAME in payload["reason"], (
        "the resolution failure keeps the top-level reason; the new keys are additive"
    )
    assert str(registry_path) not in payload["reason"], (
        "and that is the defect: the top-level reason is the migration's and says nothing "
        "about the file the null actually came from"
    )
    assert "cannot be read as JSON" in payload["registryReason"], (
        "the null's own cause is published under its own name"
    )
    assert str(registry_path) in payload["registryReason"], "and it names the file to go to"
    assert RE_REGISTER_INVOCATION in payload["registryRemedy"], (
        "a `cannot know` with no cure beside it is unactionable -- the whole point of #381"
    )
    assert_a_deletion_cure_does_not_claim_it_is_costless(
        payload["registryRemedy"], where="project status --json registryRemedy"
    )


def test_the_compound_payload_says_which_of_its_two_failures_to_fix_first(
    project: Path, registry_path: Path
) -> None:
    """A destructive instruction whose recovery the same payload has already blocked.

    This is the state the keys above created and nothing yet ordered. ``reason``
    is the migration's, ``registryRemedy`` is the registry's cure, and where that
    migration raises without a ``remedy`` of its own (issue #384) the destructive
    cure is the *only* instruction the payload carries. Followed verbatim it
    unregisters every project on the machine, and then the ``theurian project
    register`` it ends with refuses at exit 1 on the migration named in the same
    payload -- ``_resolve_or_refuse`` calls the ``resolve_context`` that already
    failed. The reader takes the loss and does not get the recovery.

    Pinned as the cure *plus* the ordering sentence, byte for byte, rather than
    as a substring: what has to hold is that the shared cure arrived unaltered
    and the ordering was appended to it. A cure quietly rewritten at this emit
    site would satisfy "the sentence is in there" and put a second spelling of a
    destructive offer one layer up, which is the drift PR #596 watched reach four
    faces.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_malformed_yaml_migration(project)
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    code, payload = _invoke("project", "status")

    assert code == 0
    assert MALFORMED_YAML_MIGRATION_FILENAME in payload["reason"], (
        "the fixture must reach the compound arm, where `reason` is not the registry's"
    )
    assert payload["registryRemedy"] == (
        f"{the_pinned_cure('unparsable', registry_path)} {THE_REPAIR_ORDER_SENTENCE}"
    ), (
        "the compound payload must publish the shared cure unaltered and name the order the "
        "two failures have to be repaired in"
    )


def test_status_names_the_registry_failure_beside_the_reason_that_already_carried_it(
    project: Path, registry_path: Path
) -> None:
    """The registry-only arm: today's keys unchanged, the new ones beside them.

    With the migrations healthy, ``resolve_context`` gets as far as the registry
    and the whole-file refusal *is* the resolution failure, so ``reason`` and
    ``remedy`` already carry the registry's own prose --
    ``test_status_reports_a_registry_it_cannot_parse_instead_of_raising`` above
    is the pin on that and is left exactly as it was.

    ``registryReason``/``registryRemedy`` appear here too, duplicating them. That
    is decided, not accidental: a consumer must be able to read the registry's
    verdict from one pair of keys without first working out *why* resolution
    failed, and a pair that is present only sometimes is a pair every caller
    eventually stops checking. The equality assertion is what pins the
    duplication as duplication -- one sentence about one file under two names --
    rather than leaving room for two subtly different sentences about it.

    Two *reads* produce those values, not one, and they are allowed to disagree:
    ``test_status_says_it_cannot_know_when_the_registry_breaks_between_its_two_reads``
    below is the case where the file changes in between and each key stays honest
    about its own read. What is pinned here is that in a state where nothing
    changes between them, neither key gains a sentence the other lacks -- and
    since ``30e460b9`` that includes the repair-order sentence, which
    ``_unresolved_status`` appends to ``remedy`` too wherever ``remedy`` is this
    same cure.
    """
    _invoke("init")
    _invoke("project", "register")
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    code, payload = _invoke("project", "status")

    assert code == 0
    assert payload["registered"] is None
    assert payload["unreadable"] == []
    assert "cannot be read as JSON" in payload["reason"], "today's reason is unchanged"
    assert RE_REGISTER_INVOCATION in payload["remedy"], "and so is today's remedy"

    assert payload["registryReason"] == payload["reason"], (
        "one file, read twice with nothing changing in between, published under the name "
        "that says which file it is about as well as under the one that does not"
    )
    assert payload["registryRemedy"] == payload["remedy"], (
        "the duplication is deliberate: the registry keys must be readable without first "
        "deciding whether the resolution failure happened to be the registry's"
    )
    assert payload["remedy"].endswith(THE_REPAIR_ORDER_SENTENCE), (
        "the ordering sentence is appended to whichever keys carry this cure, so the two "
        "keys cannot end up one sentence apart"
    )
    assert_a_deletion_cure_does_not_claim_it_is_costless(
        payload["registryRemedy"], where="project status --json registryRemedy"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_resolution_failure_with_a_cure_of_its_own_keeps_it_when_the_registry_also_fails(
    project: Path, registry_path: Path
) -> None:
    """The other direction of "the keys move together or not at all", and it was unpinned.

    ``_unresolved_status`` appends the ordering sentence to ``remedy`` only where
    ``remedy`` is already the same registry cure ``registryRemedy`` carries, and
    the test above pins the arm where it *does*. This is the arm where it must
    not: an unreadable ``.theurian/migrations`` is a resolution failure that
    raises with a cure of its own, about a different artefact entirely. Replacing
    that cure with the registry's would hand a reader an offer to delete every
    project's registration in answer to a directory at mode ``000``, and would
    lose the one instruction that actually fixes what they have.

    Unpinned until now, and measurably so: replacing the equality gate with
    ``True`` survived round two's whole-suite mutation run, and with this test
    deselected it still leaves the seven-file scope of this branch green. Both
    keys are asserted,
    because the defect has two halves -- ``remedy`` keeping its own cure, and
    ``registryRemedy`` still carrying the registry's *with* the ordering
    sentence, which is what makes the payload's two cures distinguishable rather
    than merely different.
    """
    _invoke("init")
    _invoke("project", "register")
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    migrations = project / ".theurian/migrations"
    migrations.chmod(0o000)
    try:
        code, payload = _invoke("project", "status")
    finally:
        migrations.chmod(0o700)

    assert code == 0
    assert "could not be listed" in payload["reason"], (
        "the fixture must reach the compound arm through the migrations directory, not the registry"
    )
    assert payload["remedy"] == (
        f"Confirm this user has read and execute permission on {'.theurian/migrations'!r} "
        f"and its parent directories, then retry."
    ), "the resolution failure's own cure is what `remedy` answers for, and it is untouched"
    assert THE_REPAIR_ORDER_SENTENCE not in payload["remedy"], (
        "the ordering sentence points at `the re-registration above`, and this key offers no "
        "re-registration to point at"
    )
    assert payload["registryRemedy"] == (
        f"{the_pinned_cure('unparsable', registry_path)} {THE_REPAIR_ORDER_SENTENCE}"
    ), "while the registry's own key carries the registry's cure, ordering sentence and all"


def test_register_refuses_on_the_other_failure_the_repair_order_sentence_names(
    project: Path, registry_path: Path
) -> None:
    """The mechanism the ordering sentence asserts, run rather than described.

    ``THE_REPAIR_ORDER_SENTENCE`` tells a reader that while ``reason`` names a
    different failure, ``theurian project register`` fails on it too -- so the
    re-registration the registry cure ends with cannot run. That is a claim about
    a second command's exit code, and nothing in this suite executed it: the
    sentence was pinned byte for byte at three surfaces and its truth at none.

    Driven as the reader would arrive at it. The registry is unparsable *and*
    a migration is malformed, which is the compound arm the sentence is written
    for, and ``project register`` is invoked in the same state the payload
    describes. Exit 1 naming the migration is what makes the warning true; exit 0
    would make it a false instruction to fix something first.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_malformed_yaml_migration(project)
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    _, status = _invoke("project", "status")
    code, payload = _invoke("project", "register")

    assert THE_REPAIR_ORDER_SENTENCE in status["registryRemedy"], (
        "the fixture must be the state the sentence is published in"
    )
    assert code == 1, (
        "the sentence warns that this command fails while the other failure stands; exit 0 "
        "would make it a warning against nothing"
    )
    assert MALFORMED_YAML_MIGRATION_FILENAME in payload["error"], (
        "and it fails on the failure `reason` named, which is what makes `fix that one first` "
        "the right order rather than an arbitrary one"
    )


def test_the_repair_order_sentence_refuses_a_cure_with_no_re_registration_to_point_at() -> None:
    """The sentence's antecedent, guarded at the appender rather than assumed.

    "The re-registration above" is a pointer, and `_registry_cure_in_repair_order`
    is handed whatever cure the failing error carries -- `_context_remedy` picks
    that, not this function. A registry error added later with a cure of its own
    would get the sentence appended to a text that never offered a
    re-registration, and the reader would be told to wait for something the
    payload never told them to run.

    Driven directly rather than through a command, because reaching it through
    one would need a registry error this codebase does not raise; the point is
    that the guard fires for the error somebody adds next. The happy direction is
    asserted first, so a guard that refused everything would fail here too.
    """
    from theurian.cli.commands import _registry_cure_in_repair_order

    cure = the_pinned_cure("unparsable", Path("/data/projects.json"))

    assert _registry_cure_in_repair_order(cure) == f"{cure} {THE_REPAIR_ORDER_SENTENCE}", (
        "a cure that does offer the re-registration must come back with the sentence appended "
        "and nothing else changed"
    )

    with pytest.raises(ValueError, match="no longer carries") as excinfo:
        _registry_cure_in_repair_order("Fix the migration file, then retry.")

    assert "re-register each project with" in str(excinfo.value), (
        "the refusal has to name the invocation it went looking for, or the next author cannot "
        "tell which half of the pointer broke"
    )


def _a_healthy_registry(project: Path, registry_path: Path) -> None:
    """Registered, readable, and holding this root: the resolved payload.

    Takes both paths and uses neither, so the three setups below share one
    signature and the parametrize stays a table of states rather than of shapes.
    """
    _invoke("project", "register")


def _an_unreadable_entry_for_another_root(project: Path, registry_path: Path) -> None:
    """An id no consumer accepts, over an absolute root that is somewhere else.

    `ids_for_root` does not refuse this -- the entry names a root and it is not
    this one -- so resolution *succeeds* and the payload is the resolved shape,
    with `registered: null` from `holds_root`'s broader refusal. The file parses,
    so `_read_registry().failure` is `None`: the arm the new keys must not reach.
    """
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(
        json.dumps(
            {
                "demo": entry,
                "Team One/API": {
                    "rootPath": str(project.parent / "elsewhere"),
                    "defaultBranch": "main",
                },
            }
        )
    )


def _an_entry_that_names_no_root(project: Path, registry_path: Path) -> None:
    """A rootless entry, which `ids_for_root` *does* refuse.

    So this one lands on the **unresolved** branch -- the branch issue #381
    changes -- with the registry perfectly parseable. It is the fence that
    matters most: a fix keyed on `unreadable` or on `registered is None` rather
    than on `failure is not None` would publish a registry cure here, for a file
    whose only problem is one entry that `project list` already names.
    """
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(json.dumps({"demo": entry, "hand-edited": {"defaultBranch": "main"}}))


@dataclass(frozen=True)
class _RegistryReadSucceeds:
    """One state in which ``_read_registry()`` comes back with no failure.

    The three expectations travel with the setup rather than as three more
    parameters, so the case declares the payload it means to produce and the test
    can refuse a fixture that stopped producing it.
    """

    set_up: Callable[[Path, Path], None]
    branch: str
    registered: bool | None
    unreadable: list[str]


@pytest.mark.parametrize(
    "case",
    [
        _RegistryReadSucceeds(_a_healthy_registry, "resolved", True, []),
        _RegistryReadSucceeds(
            _an_unreadable_entry_for_another_root, "resolved", None, ["Team One/API"]
        ),
        _RegistryReadSucceeds(_an_entry_that_names_no_root, "unresolved", None, ["hand-edited"]),
    ],
    ids=["healthy", "unreadable-entry-resolved", "unreadable-entry-unresolved"],
)
def test_status_publishes_no_registry_failure_keys_when_the_registry_could_be_read(
    project: Path, registry_path: Path, case: _RegistryReadSucceeds
) -> None:
    """The keys answer for a failure, not for a null -- and one case is what says so.

    ``registered: null`` has two causes and only one of them is a registry that
    could not be read. The other is a file that parsed perfectly and holds an
    entry this reader cannot attribute -- ``holds_root``'s deliberately broader
    refusal, whose cure is ``theurian project unregister <id>`` and whose subject
    is named in ``unreadable``, not a whole-file failure. A fix keyed on the null
    instead of on ``_read_registry().failure`` would print "delete the registry"
    over one hand-edited line.

    **Only ``unreadable-entry-unresolved`` can catch that.** The keys are written
    by ``_unresolved_status``, which the other two cases never enter: they resolve,
    so no gate keyed any way at all could publish a registry key there. Calling
    all three a fence would overstate two of them. What they are is controls --
    the resolved payload has never carried these keys and this says so if it ever
    starts -- and the discriminating case is the third, which reaches the
    unresolved branch with ``registered: null``, an ``unreadable`` entry, and a
    registry that read perfectly.

    ``not in`` rather than ``is None``: absence is this payload's "never asked",
    and ``null`` is "asked, and unknowable" -- the distinction
    ``_unresolved_status``' own docstring draws for ``indexStale``. A key present
    and empty would satisfy a truthiness check while telling every consumer that
    the registry had something to report.

    All three cases were GREEN at ``2d3c23bb``, where the keys did not exist, and
    have been GREEN since ``043f0c5e``, where they arrived; the point of the pin
    is that they stayed green *through* the change rather than that they ever
    went red. Each asserts the state it set up first, so a fixture that stopped
    reaching its branch fails here rather than passing the key check vacuously.
    """
    _invoke("init")
    case.set_up(project, registry_path)

    code, payload = _invoke("project", "status")

    assert code == 0
    assert ("projectId" in payload) == (case.branch == "resolved"), (
        f"the fixture must reach the {case.branch} branch for this case to mean anything"
    )
    assert payload["registered"] is case.registered
    assert payload["unreadable"] == case.unreadable

    assert "registryReason" not in payload, (
        "the registry read succeeded; a key naming its failure would describe one that "
        "did not happen"
    )
    assert "registryRemedy" not in payload, (
        "and a cure for it would send the reader to delete the file over a single entry "
        "`theurian project unregister` removes"
    )


def test_status_outside_a_repository_publishes_no_registry_failure_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other fence: outside a working tree the registry is not the subject.

    ``_unresolved_status`` short-circuits on ``find_git_root`` and leaves
    ``registered`` the literal ``False`` it initialised the payload with, because
    no entry could be about a directory in no working tree -- the rule
    ``test_status_outside_a_repository_keeps_a_certain_answer_on_a_wholly_corrupt_registry``
    above pins for ``registered``, extended here to issue #381's keys. Naming the
    registry failure here would attach a cure to an answer that does not depend
    on it, and would tell a user standing in ``/tmp`` to delete every project's
    registration. ``theurian project list`` is the surface that reports the file.

    GREEN at ``2d3c23bb`` and at ``043f0c5e`` alike: the pin is that the keys are
    gated on the same ``find_git_root`` the ``registered`` answer is, and not on
    the registry read alone.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    path = tmp_path / "datadir" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(_UNPARSEABLE_REGISTRY)

    code, payload = _invoke("project", "status")

    assert code == 0
    assert "not inside a Git repository" in payload["reason"], "the fixture must be outside one"
    assert payload["registered"] is False, (
        "no entry could be about a directory in no working tree, and that answer stays certain"
    )
    assert "registryReason" not in payload, (
        "the registry's condition cannot change an answer that does not depend on it"
    )
    assert "registryRemedy" not in payload, (
        "and a delete-the-registry cure printed to somebody in /tmp is unattached to any "
        "question they asked"
    )


# -- issue #384: the two states `project status` diagnosed without curing -----
#
# #381 taught the unresolved branch to name the registry failure beside its
# `registered: null`. Two states were left where this command published a
# diagnosis and no way out of it, and they sit on different branches. Both are
# measured at `28f5f115`, the commit these tests were written against.
#
# **Face A, on the unresolved branch.** `_unresolved_status` copied `exc.remedy`
# under a bare `if`, so a resolution failure raising *without* one -- a malformed
# migration, which is the ordinary case -- reached the payload as a `reason` with
# nothing beside it. `migrate status` grades that same exception through
# `_require_project`, which spells `exc.remedy or "Fix the migration file, then
# retry."`, so two commands answered one repository state with two different
# amounts of help: `project status --json` published `{reason, registered,
# unreadable}` at exit 0 while `migrate status --json` exited 4 carrying that cure.
#
# **Face B, on the resolved branch.** An entry keyed by an id no consumer accepts,
# over a `rootPath` naming some other directory, is not something `ids_for_root`
# refuses -- so resolution succeeds, `holds_root` withholds membership anyway, and
# the payload is the full resolved shape with `registered: null` and the offending
# id under `unreadable`. At `28f5f115` that payload carried no `reason` and no
# `remedy` anywhere in it, while `theurian project list` over the same file printed
# "Remove them with `theurian project unregister <id>`. Until then, commands that
# resolve a project from the current directory refuse rather than guess."
#
# **What the two faces do not share is the cure.** Face B's is per entry: the file
# parsed, and one line in it is bad. The whole-file deletion cure must not reach
# it -- that offer unregisters every project on the machine over one hand-edited
# line, which is why `failure_fields` is keyed on the registry read's own failure
# and not on the null. The fence on that gate is the `unreadable-entry-unresolved`
# case of
# `test_status_publishes_no_registry_failure_keys_when_the_registry_could_be_read`
# above.
#
# **The rootless kind of unreadable entry is out of scope here, and what is held
# about it is exactly what one test asserts.** It reaches the *unresolved* branch,
# because `ids_for_root` refuses every root while one entry names none, so no
# rootless entry produces the state face B is about.
# `test_a_rootless_entry_keeps_the_refusals_own_per_entry_cure` below is the
# measurement, and the claim is its three assertions and no wider: the payload is
# the unresolved shape, `remedy` *is* `resolve_context`'s own exception text, and
# that text backticks exactly one well-formed `unregister` naming the rootless id.
# A pair rendered from the resolved branch would displace the middle one, which is
# why this face leaves that payload as it stands.
#
# It is **not** held that the unresolved branch is missing nothing. The
# unusable-key kind reaches it too -- beside an unrelated resolution failure, such
# as a malformed migration -- and there the `unreadable` id is reported with no
# sentence explaining it and no per-entry cure. That gap is pre-existing, was
# reproduced in PR #626's round one, and is issue #628's; the tests here do not
# cover it and must not be read as saying it cannot happen.


#: The span of the whole-file cure that must never appear in a per-entry one: it
#: is the cost of deleting `projects.json`, and a payload naming one bad entry has
#: no business quoting it. Checked against :data:`THE_SHARED_CURE_TAIL` before it
#: is looked for, so a re-pin of that tail cannot leave the absence check reading
#: for a sentence nothing ships.
_THE_WHOLE_FILE_DELETION_COST = "deleting it unregisters all of them"


def _assert_this_is_not_the_whole_file_deletion_cure(
    remedy: str, *, registry_path: Path, where: str
) -> None:
    """The per-entry cure is not the registry-deletion cure wearing a new key.

    Walked over the arms of :data:`THE_PINNED_CURE_LEADS` rather than against a
    list written here, so a fifth arm is covered the day it is added -- PR #596's
    rule for this family, applied in the negative direction: there the render must
    reach the shared cure, here it must not.

    Both halves are looked for, because either alone leaks the offer. The lead is
    what names the deletion; the tail is what names its cost and its recovery, and
    :data:`RE_REGISTER_INVOCATION` is the one span of that tail the assertions in
    this file already read by name.
    """
    assert _THE_WHOLE_FILE_DELETION_COST in THE_SHARED_CURE_TAIL, (
        f"{_THE_WHOLE_FILE_DELETION_COST!r} is no longer a span of the shipped cure, so its "
        f"absence from a remedy proves nothing. Re-read the tail and re-pick the span."
    )
    for arm in THE_PINNED_CURE_LEADS:
        assert the_pinned_lead(arm, registry_path) not in remedy, (
            f"{where} carries the {arm!r} arm of the whole-file deletion cure. The file parsed: "
            f"one entry in it is unreadable, and the cure for that is `theurian project "
            f"unregister <id>`, not an offer to unregister every project on the machine."
        )
    assert THE_SHARED_CURE_TAIL not in remedy, (
        f"{where} carries the whole-file cure's shared tail -- the cost of deleting "
        f"`projects.json` and the re-registration that follows it -- for a file that can be "
        f"read: {remedy!r}"
    )
    assert _THE_WHOLE_FILE_DELETION_COST not in remedy, (
        f"{where} tells the reader that what it offers unregisters every project, which is "
        f"true of deleting the file and false of removing one entry: {remedy!r}"
    )
    assert RE_REGISTER_INVOCATION not in remedy, (
        f"{where} offers the whole-file cure's recovery, so something above it has already "
        f"offered the deletion that recovery undoes: {remedy!r}"
    )


def _argv_of(span: str) -> list[str]:
    """A backticked span as the words a reader would type, or nothing.

    ``shlex`` rather than ``str.split``, because that is the difference between a
    typeable invocation and a broken one: a hand-edited key like ``Team One/API``
    reaches ``theurian project unregister`` as *one word* only when the cure
    quotes it, which is what ``unregister_commands`` uses ``shlex.quote`` for.
    One word is not yet one *argument* -- :data:`_END_OF_OPTIONS` is what buys
    that, and the caller below is where it is asserted. A span that is not a
    well-formed command line yields no argv rather than raising, so one such span
    cannot hide every other span from the search.
    """
    try:
        return shlex.split(span)
    except ValueError:
        return []


#: The end-of-options marker ``unregister_commands`` renders between the command
#: and the id it names, and the reason the extraction below reads position 3.
#:
#: ``shlex.quote`` buys one shell *word*; it does not buy one *positional
#: argument*. A registry key of ``--help`` is already a safe word, so it quotes to
#: itself, and the cure rendered ``theurian project unregister --help`` -- which
#: prints usage at exit 0 and leaves the entry exactly where it was (PR #626 round
#: one, adversarial HIGH-1). ``--json`` and ``-x`` exited 2 instead. The marker is
#: what makes the quoted word an argument, and
#: ``test_the_cure_for_an_option_shaped_id_removes_the_entry_rather_than_printing_usage``
#: is the row that keeps it there.
_END_OF_OPTIONS = "--"


def _the_unregister_invocation_the_remedy_backticks(
    remedy: str, *, for_id: str, where: str
) -> list[str]:
    """The ``theurian project unregister -- <id>`` argv this remedy names for ``for_id``.

    The #381 execution predicate's rule, applied to a cure this file both reads
    and runs: the words a test types come out of the quote rather than from beside
    it (``test_registry_cure_execution.py``, "And the commands come out of the
    quote, not from beside it"). A literal typed here would go on working after
    the remedy stopped naming the id, which is the mutation that module measured
    surviving the suite.

    Exactly one invocation for the id, not merely one somewhere in the text: a
    remedy naming a *different* entry's id would satisfy "an unregister command is
    in here" while sending the reader to remove somebody else's registration.

    **The shape includes the marker, and that is the whole of the round-one
    containment.** ``argv[3]`` is asserted to be :data:`_END_OF_OPTIONS` rather
    than being skipped over, so a render that goes back to quoting alone fails
    here and not only in the one fixture whose id is option-shaped. Read back off
    what ``unregister_commands`` renders at this commit::

        `theurian project unregister -- 'Team One/API'`
        `theurian project unregister -- --help`
    """
    spans = re.findall(r"`([^`]+)`", remedy)
    matching = [
        argv
        for argv in (_argv_of(span) for span in spans)
        if argv[:3] == ["theurian", "project", "unregister"]
        and argv[3:4] == [_END_OF_OPTIONS]
        and argv[4:] == [for_id]
    ]
    assert len(matching) == 1, (
        f"{where} must name the entry to remove as a command the reader can type -- exactly "
        f"one backticked `theurian project unregister -- {shlex.quote(for_id)}`. An id a hand "
        f"edit left behind is one argument only when the cure quotes it, and it is a "
        f"*positional* argument only when the cure ends option parsing before it. The remedy "
        f"backticks {spans!r}"
    )
    return matching[0]


def _following_the_unregister_cure(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """Run the argv a remedy named, with ``--json`` in the one place it still works.

    :func:`_invoke` appends ``--json`` last, and last is now *past* the
    end-of-options marker, where it is a second positional argument rather than a
    flag. Measured against the real command over a planted registry:
    ``["project", "unregister", "--", "Team One/API", "--json"]`` exits 2 with
    ``Error: Got unexpected extra argument(s) (--json)`` and leaves the entry in
    the file, while ``["project", "unregister", "--json", "--", "Team One/API"]``
    exits 0 and the entry is gone.

    So the *flag* moves and the extracted words do not: everything the cure
    backticked still runs, in the order it backticked it, which is what keeps this
    an execution of the published text rather than of a convenient paraphrase.
    A reader wanting the machine channel puts the flag ahead of the marker the
    same way -- ``unregister_commands``' own docstring records that measurement.

    Nothing reaches a shell here; the words go to Typer's runner as argv.
    """
    assert argv[0] == "theurian", f"the cure must name the binary, not {argv[0]!r}"
    marker = argv.index(_END_OF_OPTIONS)
    return _invoke_argv([*argv[1:marker], "--json", *argv[marker:]])


def test_status_and_migrate_status_publish_one_cure_for_one_broken_migration(
    project: Path,
) -> None:
    """Issue #384, face A: one exception, two commands, and only one of them helped.

    At ``28f5f115`` ``_unresolved_status`` published ``exc.remedy`` under a bare
    ``if``, and a schema-invalid migration raises a plain ``MigrationError`` with
    no ``remedy`` of its own -- so the command a confused user runs *first* named
    the broken file and said nothing about what to do with it, while ``migrate
    status``, over the identical repository, published ``_require_project``'s "Fix
    the migration file, then retry." The parity is the claim, and it is the one
    #205 already settled everywhere else: a self-describing error carries its own
    cure, and an error that describes nothing still gets the branch's default
    rather than silence.

    **Asserted by invoking both commands against one repository, never against a
    transcription.** A sentence pinned here would go on agreeing with itself after
    the shared text moved, which is exactly what a parity test must not do.
    ``reason``/``error`` are compared for the same reason: both are ``str(exc)``,
    so their equality is what says the two payloads describe one exception rather
    than two coincidentally similar failures.

    The exit codes are deliberately unequal and are asserted as such. ``project
    status`` answers 0 for a directory that is not a project at all; ``migrate
    status`` grades a migration set it cannot load at ``EXIT_STATE_ERROR``. The
    defect was never the code -- it was the missing sentence.

    RED at ``28f5f115`` on the ``"remedy" in status`` line, where the payload was
    ``{reason, registered, unreadable}`` and nothing else; GREEN once the
    unresolved branch answers that key through ``_context_remedy`` like every other
    surface reporting the same exception.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_malformed_yaml_migration(project)

    status_code, status = _invoke("project", "status")
    migrate_code, migrated = _invoke("migrate", "status")

    assert (status_code, migrate_code) == (0, EXIT_STATE_ERROR), (
        "the two contracts differ by design; what must not differ is the help they give"
    )
    assert status["reason"] == migrated["error"], (
        "the fixture must drive one exception into both commands, or the parity below is "
        "comparing two unrelated failures"
    )
    assert migrated["remedy"], (
        "and the surface being matched must publish a cure at all, or the equality holds "
        "on two empty strings"
    )
    assert "remedy" in status, (
        "a status that names a broken migration and offers no way to fix it is the whole of "
        "issue #384's face A"
    )
    assert status["remedy"] == migrated["remedy"], (
        "one exception, one cure: `migrate status` publishes it through `_require_project`, "
        "and the exit-0 branch beside it must not be narrower than the command it sends "
        "people to"
    )


def test_the_compound_payload_keeps_the_migrations_cure_apart_from_the_registrys(
    project: Path, registry_path: Path
) -> None:
    """The identity gate's not-clobber direction, driven by the state face A creates.

    ``_unresolved_status`` appends the repair-order sentence to ``remedy`` only
    where ``remedy`` is already the same registry cure ``registryRemedy`` carries.
    ``test_a_resolution_failure_with_a_cure_of_its_own_keeps_it_when_the_registry_also_fails``
    drives the arm where it must not, through an exception that carries its own
    ``.remedy``. Face A creates a *second* such arm and it is the one the compound
    payload was written around: here ``remedy`` must be ``_context_remedy``'s answer
    for a migration that describes nothing, so the gate meets a cure that no error
    supplied. Two ways to get this wrong, and both are pinned below -- overwriting
    the migration's cure with the registry's, and appending the ordering sentence
    to a key that offers no re-registration for "the re-registration above" to
    point at.

    ``migrate status`` supplies the expected cure rather than a literal, for
    ``test_status_and_migrate_status_publish_one_cure_for_one_broken_migration``'s
    reason -- and here the equality carries a second claim: that the cure survives
    the registry keys arriving beside it *unchanged*, which is what makes the two
    instructions in this payload distinguishable rather than merely different.

    RED at ``28f5f115`` on the ``"remedy" in payload`` line: the compound payload
    published ``reason``, ``registryReason`` and ``registryRemedy``, so its only
    actionable instruction was the destructive one. GREEN once face A's fix gives
    the migration failure a cure of its own on this branch.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_malformed_yaml_migration(project)
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    code, payload = _invoke("project", "status")
    _, migrated = _invoke("migrate", "status")

    assert code == 0
    assert MALFORMED_YAML_MIGRATION_FILENAME in payload["reason"], (
        "the fixture must reach the compound arm, where `reason` is not the registry's"
    )
    assert "remedy" in payload, (
        "the payload's only instruction was the one that deletes every registration on the "
        "machine; the failure `reason` names has a cure of its own and this is where it goes"
    )
    assert payload["remedy"] == migrated["remedy"], (
        "and it is the same cure the command beside it publishes for this same migration, "
        "unaltered by the registry keys arriving in the same payload"
    )
    assert payload["remedy"] != payload["registryRemedy"], (
        "two failures, two cures: a payload whose keys carry one sentence twice has lost one "
        "of the two things it had to say"
    )
    assert THE_REPAIR_ORDER_SENTENCE not in payload["remedy"], (
        "the ordering sentence points at `the re-registration above`, and the migration's "
        "cure offers no re-registration to point at"
    )
    assert RE_REGISTER_INVOCATION not in payload["remedy"], (
        "and it must not have become a second spelling of the destructive offer one key over"
    )
    assert payload["registryRemedy"] == (
        f"{the_pinned_cure('unparsable', registry_path)} {THE_REPAIR_ORDER_SENTENCE}"
    ), "while the registry's own key still carries the shared cure, ordering sentence and all"


def test_status_outside_a_repository_publishes_the_refusals_own_cure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fence face A's fix must not move: a self-describing refusal keeps its cure.

    ``resolve_context`` raises "not inside a Git repository" *with* a ``remedy``,
    and this payload publishes it. Routing the key through ``_context_remedy``
    changes nothing here, by that function's own first rule -- "A non-empty
    ``exc.remedy`` wins over everything below it" (issue #205) -- and this is where
    that is measured rather than assumed.

    The expected sentence comes from the exception itself, driven through the
    production ``resolve_context`` in the same directory, rather than transcribed:
    a literal here would pass a payload that had quietly stopped publishing the
    refusal's own words and started publishing a copy of them.

    GREEN at ``28f5f115``, and unpinned there. The sentence appears twice in the
    tree at the commit this test lands in, and neither occurrence is an assertion::

        git grep -n "Run this inside a Git repository" -- packages/theurian-core/src

    -- ``cli/context.py``'s raise and ``cli/commands.py``'s ``_context_remedy``
    default. Run over ``packages/theurian-core/tests`` instead, the same search
    returns exactly one line and it is the one printed above, in this docstring:
    no assertion anywhere in the suite spells the sentence, which is why a default
    that swallowed this cure was invisible to it. Both counts are ``git grep``, so
    re-run them **after ``git add``** -- a count taken beside an untracked file is
    a count of a tree the commit does not have.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    with pytest.raises(ProjectError) as excinfo:
        resolve_context()
    assert excinfo.value.remedy, "the fixture must produce a refusal that describes itself"

    code, payload = _invoke("project", "status")

    assert code == 0
    assert "not inside a Git repository" in payload["reason"], "the fixture must be outside one"
    assert payload["remedy"] == excinfo.value.remedy, (
        "a refusal that carries its own cure keeps it: a status-branch default may only "
        "answer for an exception that supplies nothing"
    )


def test_a_rootless_entry_keeps_the_refusals_own_per_entry_cure(
    project: Path, registry_path: Path
) -> None:
    """The other fence, and the state #384 deliberately leaves alone.

    An entry naming no root makes ``ids_for_root`` refuse every root, so this is
    the *unresolved* branch -- and the exception it raises already carries both the
    reason and the per-entry cure, naming the id to remove and the invocation that
    removes it. Nothing is missing **in this state**, which is why #384's face B
    answers for the resolved branch only and leaves this payload as it stands. The
    claim is this state and not the branch: an unusable-key entry beside an
    unrelated resolution failure lands here explained by nothing, and that gap is
    issue #628's (PR #626 round one).

    Pinned as equality against the exception's own ``remedy``, driven through the
    production ``resolve_context``: this state's cure is the one text a fix keyed
    on ``registered is None`` would replace with an offer to delete the whole file,
    and until now the suite asserted only that the ``registryReason``/
    ``registryRemedy`` keys stay away from it
    (``test_status_publishes_no_registry_failure_keys_when_the_registry_could_be_read``'s
    ``unreadable-entry-unresolved`` case), never what ``remedy`` itself says.

    GREEN at ``28f5f115``.
    """
    _invoke("init")
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(json.dumps({"demo": entry, "hand-edited": {"defaultBranch": "main"}}))
    with pytest.raises(ProjectError) as excinfo:
        resolve_context()

    code, payload = _invoke("project", "status")

    assert code == 0
    assert "projectId" not in payload, "the fixture must reach the unresolved branch"
    assert payload["registered"] is None
    assert payload["unreadable"] == ["hand-edited"]
    assert payload["remedy"] == excinfo.value.remedy, (
        "the refusal's own per-entry cure is what this branch publishes, and it is the text "
        "a whole-file deletion offer would displace"
    )
    _the_unregister_invocation_the_remedy_backticks(
        payload["remedy"], for_id="hand-edited", where="project status --json remedy"
    )
    _assert_this_is_not_the_whole_file_deletion_cure(
        payload["remedy"],
        registry_path=registry_path,
        where="project status --json remedy, unresolved branch",
    )


def test_status_names_the_unreadable_entry_that_withholds_membership(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """Issue #384, face B: a resolved payload that withholds an answer and explains nothing.

    An entry keyed by an id no consumer accepts, over an absolute ``rootPath``
    naming somewhere else, is not one ``ids_for_root`` refuses -- it names a root,
    and not this one -- so resolution succeeds, while ``holds_root`` withholds
    membership anyway, because ``load`` keeps no key a consumer would reject and
    ``unreadable_ids`` reports it
    (``test_the_resolved_branch_reaches_the_same_null_with_nothing_racing_it``
    pins that this state is deterministic, not a race). Measured at ``28f5f115``,
    the payload was the full resolved shape -- ``projectId``, ``root``,
    ``stateHash`` -- with ``registered: null``, the offending id under
    ``unreadable``, and no ``reason`` or ``remedy`` anywhere in it. The same file,
    read by ``theurian project list``, printed "Remove them with `theurian project
    unregister <id>`."

    So this payload published a withheld answer and left the reader to work out
    both why it was withheld and what to do about it -- the defect #381 fixed one
    branch over, arriving here through the other cause of the same null.

    **The cure is per entry and must stay per entry.** The file parsed; one line
    in it is bad. ``_assert_this_is_not_the_whole_file_deletion_cure`` is what
    refuses the tempting reuse of the registry-deletion cure, which unregisters
    every project on the machine and re-derives every id from a directory name.

    RED at ``28f5f115`` on the ``"reason" in payload`` line; GREEN once the resolved
    branch publishes a producer for the unreadable entries it already reports.
    """
    _invoke("init")
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(
        json.dumps(
            {
                "demo": entry,
                "Team One/API": {"rootPath": str(tmp_path / "elsewhere"), "defaultBranch": "main"},
            }
        )
    )

    code, payload = _invoke("project", "status")

    assert code == 0
    assert payload["projectId"] == "demo", "the fixture must reach the resolved branch"
    assert payload["registered"] is None, "and it must be the state that withholds membership"
    assert payload["unreadable"] == ["Team One/API"]
    assert "reason" in payload, (
        "a `registered: null` with nothing beside it is a status a reader cannot act on -- "
        "the rule `failure_fields` already holds for the other cause of this null"
    )
    assert all(unreadable_id in payload["reason"] for unreadable_id in payload["unreadable"]), (
        "and what it names is the entries the null came from: an id absent from the prose is "
        "an id the reader cannot look up in the file"
    )
    assert "remedy" in payload, "a `cannot know` with no cure beside it is unactionable"
    _the_unregister_invocation_the_remedy_backticks(
        payload["remedy"], for_id="Team One/API", where="project status --json remedy"
    )
    _assert_this_is_not_the_whole_file_deletion_cure(
        payload["remedy"],
        registry_path=registry_path,
        where="project status --json remedy, resolved branch",
    )


def test_a_healthy_resolved_status_explains_nothing_because_nothing_is_wrong(
    project: Path,
) -> None:
    """The fence face B's producer must not cross, and the hook reads it as one.

    ``reason`` is this command's word for "part of this answer is degraded", and
    ``plugins/claude-code/scripts/session-start.sh`` greps for exactly that key to
    decide whether to warn a user at the start of every session. A resolved,
    registered, readable project that published one would make that hook cry
    degradation over a healthy repository, on every session, forever.

    Asserted as key absence rather than as a falsy value, which is this payload's
    own distinction: ``null`` is "asked, and the answer is unknowable"
    (``registered`` over a registry nobody can read) and absence is "never asked"
    (``_unresolved_status``' docstring, for ``indexStale``). The distinction has
    teeth here: a ``reason`` of ``""`` passes a truthiness check and still matches
    the hook's ``"reason": *"`` grep, so an assertion on the value rather than on
    the key would not see the warning this test exists to prevent.

    The state it set up is asserted first, so a fixture that stopped being healthy
    fails here rather than passing the absence check vacuously.

    GREEN at ``28f5f115``.
    """
    _invoke("init")
    _invoke("project", "register")

    code, payload = _invoke("project", "status")

    assert code == 0
    assert payload["registered"] is True, "the fixture must be a healthy registered project"
    assert payload["unreadable"] == [], "with nothing in the registry left to explain"
    assert "reason" not in payload, (
        "nothing about this status is degraded, and `reason` is what a session-start hook "
        "greps for before it warns"
    )
    assert "remedy" not in payload, "a healthy project must not be handed a cure for nothing"


def test_a_reader_of_the_unreadable_entry_cure_recovers_by_following_it(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """Face B's cure, executed in the condition it is written for.

    The test above pins what the payload *says*. This plants the condition, reads
    the invocation back out of the remedy's own backticks, runs it, and measures
    what the reader is left holding -- the executed-instruction predicate
    ``test_registry_cure_execution.py`` holds every arm of the registry cure to,
    applied to the one cure this payload publishes. A cure naming an id the file
    does not hold, or an id the command refuses, fails mechanically here; a goal
    statement ("the reader gets out") passes only because recovery is measured.

    **The command comes out of the quote.** ``_the_unregister_invocation_the_remedy_backticks``
    returns the argv, and that argv is what runs -- so a remedy rewritten to name a
    different id runs that different id and fails at the recovery, rather than
    passing on a literal typed beside it.
    :func:`_following_the_unregister_cure` is what runs it, because ``--json``
    appended last would land past the end-of-options marker and exit 2; the flag
    is the only word that moves.

    **Nothing here reaches a shell.** The invocation is handed to the in-process
    CLI runner as argv, never interpolated into a command line: a cure is text from
    a file a hand edit wrote, and the id in this fixture is deliberately not a slug.

    Recovery is all three halves: the entry is gone, ``unreadable`` is empty, and
    ``registered`` answers ``True`` again -- the honest answer, since ``demo``'s own
    registration was never what was broken. A cure that removed the wrong entry
    would leave ``unreadable`` populated; one that removed both would leave
    ``registered`` ``False``.

    RED at ``28f5f115``, where the payload carried no ``remedy`` to follow; GREEN
    once the test above is, since this one follows the text that one reads.
    """
    _invoke("init")
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(
        json.dumps(
            {
                "demo": entry,
                "Team One/API": {"rootPath": str(tmp_path / "elsewhere"), "defaultBranch": "main"},
            }
        )
    )
    _, broken = _invoke("project", "status")
    assert broken["unreadable"] == ["Team One/API"], "the fixture must plant the condition"
    assert "remedy" in broken, "and the payload must offer the cure this test then follows"

    argv = _the_unregister_invocation_the_remedy_backticks(
        broken["remedy"], for_id="Team One/API", where="project status --json remedy"
    )
    removed_code, removed = _following_the_unregister_cure(argv)

    assert removed_code == 0, f"the invocation the cure names must run: {removed!r}"
    assert removed["removed"] is True, "and it must remove the entry it names"
    code, recovered = _invoke("project", "status")
    assert code == 0
    assert recovered["unreadable"] == [], "the entry the cure named is gone"
    assert recovered["registered"] is True, (
        "and membership is answerable again -- honestly, since this root's own registration "
        "was never the thing that was broken"
    )
    assert "reason" not in recovered, "nothing is left to explain"
    assert "remedy" not in recovered, "and nothing is left to cure"


def _plant_unusable_keys_beside_demo(
    registry_path: Path, *, elsewhere: Path, keys: tuple[str, ...]
) -> None:
    """Hand-edit the registry so ``demo`` stays healthy and ``keys`` are unreadable.

    Each planted entry carries a ``rootPath`` field that is absolute and names
    somewhere *else*, which is what puts the payload on the resolved branch:
    ``ids_for_root`` refuses only a rootless entry, and an unusable key over
    another directory is one it lets through, so ``holds_root`` withholds
    membership and face B's producer renders. A key with no ``rootPath`` would
    land on the unresolved branch instead and none of these tests would be about
    what they say they are -- which is why every caller asserts ``projectId`` is
    in the payload before it asserts anything else.

    The roots are distinct per key so no two planted entries claim one directory.
    """
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(
        json.dumps(
            {
                "demo": entry,
                **{
                    key: {
                        "rootPath": str(elsewhere / f"elsewhere-{index}"),
                        "defaultBranch": "main",
                    }
                    for index, key in enumerate(keys)
                },
            }
        )
    )


def test_the_cure_for_an_option_shaped_id_removes_the_entry_rather_than_printing_usage(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """PR #626 round one, adversarial HIGH-1: the row that keeps ``--`` in the render.

    ``shlex.quote`` guarantees one shell *word*. It does not guarantee that the
    receiving command reads that word as a positional argument, and a registry key
    of ``--help`` is the case where the two come apart: it is already a safe word,
    so it quotes to itself, and the cure said ``theurian project unregister
    --help``. Followed verbatim that printed usage, exited **0**, and left the
    entry exactly where it was -- a published removal instruction that removes
    nothing while reporting success. ``--json`` and ``-x`` as keys exited 2
    instead, which is wrong more loudly rather than less.

    So the argv is asserted as a whole rather than by substring. The marker is a
    word in a specific position, and equality is what says *which* position; a
    containment check would pass on a render that put it anywhere.

    Then the invocation runs and recovery is measured on all three halves -- the
    entry gone, ``unreadable`` empty, ``registered`` back to ``True`` -- because
    "the cure is well-formed" is a claim about text and "the reader gets out" is a
    claim about the file, and this defect satisfied the first while failing the
    second.

    RED at ``83004c61``, where ``unregister_commands`` rendered the quote alone:
    the argv extraction finds no invocation for ``--help`` at all. GREEN with the
    marker rendered.
    """
    _invoke("init")
    _invoke("project", "register")
    _plant_unusable_keys_beside_demo(registry_path, elsewhere=tmp_path, keys=("--help",))
    _, broken = _invoke("project", "status")
    assert broken["projectId"] == "demo", "the fixture must reach the resolved branch"
    assert broken["unreadable"] == ["--help"], "and the planted key must be the unreadable one"

    argv = _the_unregister_invocation_the_remedy_backticks(
        broken["remedy"], for_id="--help", where="project status --json remedy"
    )
    removed_code, removed = _following_the_unregister_cure(argv)

    assert argv == ["theurian", "project", "unregister", "--", "--help"], (
        "an option-shaped key is one argument only behind the end-of-options marker; without "
        "it this same text is `theurian project unregister --help`, which prints usage"
    )
    assert removed_code == 0, f"the invocation the cure names must run: {removed!r}"
    assert removed["removed"] is True, (
        "and it must remove the entry -- exit 0 was what the defect already had, so the "
        "removal is the half that says the cure did something"
    )
    _, recovered = _invoke("project", "status")
    assert recovered["unreadable"] == [], "the entry the cure named is gone"
    assert recovered["registered"] is True, "and membership is answerable again"


def test_two_unreadable_entries_are_both_named_and_both_curable(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """PR #626 round one, adversarial MEDIUM: the arity every other fixture leaves free.

    Face B's ``reason`` says "entries" and its ``remedy`` says "Each removes only
    the entry it names", and until this test every fixture that reached the
    producer planted exactly one entry -- so both claims held vacuously. That
    round measured the cost: truncating either field to its first id was among
    its four full-suite survivors. Two entries is the smallest fixture that can
    tell the difference, and the assertions are written over
    ``payload["unreadable"]`` rather than over a list typed here, so they scale
    with whatever the fixture plants.

    Both ids are sought in ``reason`` and one invocation is sought *per* id in
    ``remedy``: ``_the_unregister_invocation_the_remedy_backticks`` insists on
    exactly one match for each, so a remedy naming one entry twice fails as
    surely as one naming it once.

    Then **both** are followed, in the order the payload named them, and recovery
    is the same three halves. Removing one and stopping would leave ``unreadable``
    populated, which is what makes the second removal a measurement rather than a
    repetition.

    RED at ``83004c61``, on the argv extraction: the cure rendered no
    end-of-options marker there. Its ``reason`` half would have held at that
    commit -- the text was already a join over the whole tuple -- so what the
    arity pin buys is that a truncation of either field now has somewhere to fail,
    not that either field was wrong.
    """
    _invoke("init")
    _invoke("project", "register")
    _plant_unusable_keys_beside_demo(
        registry_path, elsewhere=tmp_path, keys=("Team One/API", "Team Two/API")
    )

    _, broken = _invoke("project", "status")

    assert broken["projectId"] == "demo", "the fixture must reach the resolved branch"
    assert broken["unreadable"] == ["Team One/API", "Team Two/API"], (
        "and it must plant two entries, or the arity this test exists for is untested"
    )
    assert all(unreadable_id in broken["reason"] for unreadable_id in broken["unreadable"]), (
        f"an id absent from the prose is an id the reader cannot look up in the file, and the "
        f"sentence says `entries`: {broken['reason']!r}"
    )
    # And each in its *quoted* form: a raw id is a substring of its own repr, so
    # the line above passes with `repr` dropped from the join (PR #626 round two,
    # adversarial MEDIUM). Derived per id rather than typed, so the check grows
    # with whatever the fixture plants.
    assert all(repr(unreadable_id) in broken["reason"] for unreadable_id in broken["unreadable"]), (
        f"the ids are `repr`-quoted, which is what keeps an empty-string key legible as `''` "
        f"rather than closing the list to `()`: {broken['reason']!r}"
    )
    for unreadable_id in broken["unreadable"]:
        argv = _the_unregister_invocation_the_remedy_backticks(
            broken["remedy"], for_id=unreadable_id, where="project status --json remedy"
        )
        removed_code, removed = _following_the_unregister_cure(argv)
        assert (removed_code, removed["removed"]) == (0, True), (
            f"every id the payload reports has to be removable by the invocation the same "
            f"payload names for it, and {unreadable_id!r} was not: {removed!r}"
        )

    _, recovered = _invoke("project", "status")
    assert recovered["unreadable"] == [], "following the cure for each entry clears all of them"
    assert recovered["registered"] is True, (
        "and membership is answerable again -- `demo`'s own registration was never broken, so "
        "a cure that removed one entry too many would answer `False` here"
    )
    assert "reason" not in recovered, "nothing is left to explain"
    assert "remedy" not in recovered, "and nothing is left to cure"


def test_the_unreadable_entry_reason_names_the_file_and_a_cause_the_file_does_not_refute(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """PR #626 round one, code-review M-3 and adversarial HIGH-2, over one field.

    **The shape half (M-3).** Gutted to ``", ".join(self.unreadable)`` this
    producer still emits a ``reason`` and still names every id. Measured with that
    gut applied, over the nine files this change touches: every test that existed
    before this one still passed. What the gut stops doing is naming the file the
    ids are keys of, and saying that membership is *unanswerable* rather than
    merely unknown. Those two are the load-bearing claims, so those two are
    asserted, as spans rather than as a byte pin: the sentence around them is
    prose and may be rewritten, while a ``reason`` that names neither the artefact
    nor the epistemic status is not this text at all.

    **The causation half (HIGH-2).** The text at ``83004c61`` offered two causes
    and the file refutes the second. For the unusable-key kind that actually
    reaches this branch, ``ids_for_root`` reads the same entry's own ``rootPath``,
    sees a different directory, and ends its refusal "Every other project on this
    machine is unaffected" -- so "cannot be ruled out as this directory's own
    registration" made the product hold two disagreeing sentences about one
    registry at one instant. The absence assert below is what keeps the refuted
    disjunct out; the honest cause is ``holds_root``'s own, and it is the span
    asserted positively beside it.

    **The quoting half (round two).** Every assertion above and elsewhere reads
    the ids raw, and a raw id is a substring of its own ``repr`` -- so dropping
    ``repr`` from the join changed the shipped text and 204 tests went on passing.
    The quoted form is asserted for that reason alone: it is the only spelling
    that can tell the two renders apart, and it is what keeps an empty-string key
    legible as ``''`` rather than closing the list to ``()``.

    https://github.com/theurian/theurian/pull/626#issuecomment-5605076253

    RED at ``83004c61`` three times over -- that text carried neither
    "unanswerable" nor the honest cause, and did carry the refuted disjunct. RED
    under the M-3 gut on the path and the two spans, and RED on the quoted form
    alone when only ``repr`` is dropped.
    """
    _invoke("init")
    _invoke("project", "register")
    _plant_unusable_keys_beside_demo(registry_path, elsewhere=tmp_path, keys=("Team One/API",))

    _, payload = _invoke("project", "status")

    assert payload["projectId"] == "demo", "the fixture must reach the resolved branch"
    assert payload["unreadable"] == ["Team One/API"]
    assert str(registry_path) in payload["reason"], (
        f"the ids are keys of a file, and a reader who is not told which file cannot open it: "
        f"{payload['reason']!r}"
    )
    assert re.search(r"\bunanswerable\b", payload["reason"]), (
        f"`registered: null` is 'asked, and the answer is unknowable', and the reason is where "
        f"that status is said in words rather than in a JSON literal: {payload['reason']!r}"
    )
    assert re.search(r"\bcomputed from the entries it could load\b", payload["reason"]), (
        f"and the cause has to be the one this reader can defend -- the membership answer comes "
        f"from what `load` returned, and a dropped entry is not in it: {payload['reason']!r}"
    )
    # The *quoted* form, because a raw id is a substring of its own repr and the
    # all-ids assertions elsewhere therefore pass either way -- dropping `repr`
    # from the join survived 204 tests (PR #626 round two, adversarial MEDIUM).
    # The quoting is what keeps an empty-string key legible as `''` instead of
    # closing the list to `()`, and this line is what keeps the quoting.
    assert "'Team One/API'" in payload["reason"], (
        f"the ids are `repr`-quoted, as `ids_for_root`'s unusable-key refusal already spells "
        f"them over the same file: {payload['reason']!r}"
    )
    # The refuted disjunct, kept out by name. `ids_for_root` over this same file
    # ends its own refusal for this same entry with "Every other project on this
    # machine is unaffected", so publishing the opposite here is the product
    # contradicting itself over one registry at one instant.
    # https://github.com/theurian/theurian/pull/626#issuecomment-5605076253
    assert "cannot be ruled out as this directory's own registration" not in payload["reason"]


def test_the_unreadable_entry_remedy_says_what_each_removal_costs(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """PR #626 round one, code-review M-3, over the other field.

    Gutted to ``unregister_commands(self.unreadable)`` alone, the remedy still
    carries a well-formed invocation per id and still passes the argv extraction
    and the whole-file-cure absence walk. Measured with that gut applied, over the
    nine files this change touches: this test was the only failure. What the gut
    loses is the one claim this text makes about a command it does not run --
    that removing the entry a cure names costs the reader nothing else. That claim
    is why the payload can offer the removal at all beside a *healthy*
    registration, so it is pinned byte-exact: it is one sentence, and rewording it
    is a decision about what the product promises rather than prose maintenance.

    ``theurian project list`` is asserted beside it because the sentence pair is
    what makes the cure runnable when the reader does not trust the id: one names
    the cost, the other names where to read the ids back.

    ``test_a_reader_of_the_unreadable_entry_cure_recovers_by_following_it`` and
    ``test_two_unreadable_entries_are_both_named_and_both_curable`` are what make
    the claim true rather than merely present; this is what makes it present.

    GREEN at ``83004c61``: the marker is the only thing this remedy gained, and
    this test reads no invocation. It is the byte pin arriving, not a defect
    closing.
    """
    _invoke("init")
    _invoke("project", "register")
    _plant_unusable_keys_beside_demo(registry_path, elsewhere=tmp_path, keys=("Team One/API",))

    _, payload = _invoke("project", "status")

    assert payload["projectId"] == "demo", "the fixture must reach the resolved branch"
    assert payload["unreadable"] == ["Team One/API"]
    assert "Each removes only the entry it names." in payload["remedy"], (
        f"the scoping claim is what lets this cure be offered beside a healthy registration, "
        f"and a remedy that is only a list of commands has withdrawn it: {payload['remedy']!r}"
    )
    assert "`theurian project list` shows them under `unreadable`." in payload["remedy"], (
        f"and the reader who does not trust an id retyped from prose needs the surface that "
        f"prints it: {payload['remedy']!r}"
    )


def test_a_corrupt_state_pointer_beside_an_unreadable_entry_publishes_the_entrys_pair(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """PR #626 round one, code-review M-1: the recorded which-wins, pinned as recorded.

    ``reason``/``remedy`` is one pair spoken by several producers, and a corrupt
    ``active.json`` and a hand-edited registry entry are independent states of two
    different files -- so both fire at once and the last merge wins. Today that is
    the entry's, and the pointer's own text is published nowhere: only
    ``statePointerCorrupt: true`` survives of it.

    That is the #622 collision gaining a member rather than a new defect. Before
    this producer existed the same two failures left the *entry* unexplained, the
    mirror of what is lost now, so the change swapped which text is lost rather
    than losing one that used to be published. What it owes is that the state
    stops being unpinned: the choice is recorded here, and a change of mind has to
    edit this test rather than land unremarked.

    https://github.com/theurian/theurian/issues/622

    The pointer's own prose is driven out of ``read_active_state`` rather than
    transcribed, so this cannot pass by looking for a sentence the product stopped
    producing. Both halves are asserted -- that the entry's text is what the pair
    carries, and that the pointer's is absent from the payload *anywhere*, not
    merely from those two keys -- because "the entry wins" and "the pointer is
    published under some other name" are different states and only the second
    would make the loss survivable.

    RED at ``83004c61`` on the argv extraction alone: the ordering this pins is
    what that commit already did, and what was missing was any test that said so.
    """
    _invoke("init")
    _invoke("project", "register")
    _plant_unusable_keys_beside_demo(registry_path, elsewhere=tmp_path, keys=("Team One/API",))
    pointer = project / ".theurian/state/active.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("not json at all")
    with pytest.raises(ProjectError) as pointer_failure:
        read_active_state(ProjectPaths.of(project))

    _, payload = _invoke("project", "status")

    assert payload["projectId"] == "demo", "the fixture must reach the resolved branch"
    assert payload["unreadable"] == ["Team One/API"], "with the registry entry planted"
    assert payload["statePointerCorrupt"] is True, "and the pointer failing at the same time"
    assert str(registry_path) in payload["reason"], (
        "the entry's producer merges last, so the shared pair is the registry's -- this is the "
        "recorded choice, and a swap of it belongs in this assertion rather than in a diff"
    )
    _the_unregister_invocation_the_remedy_backticks(
        payload["remedy"], for_id="Team One/API", where="project status --json remedy"
    )
    published = json.dumps(payload)
    assert str(pointer_failure.value) not in published, (
        "the pointer's own reason is published nowhere in this payload, under no key -- "
        "`statePointerCorrupt: true` is the whole of what survives of it (#622)"
    )
    assert ACTIVE_POINTER_REMEDY not in published, "and its cure goes with it"


def _write_cyclic_migrations(root: Path) -> None:
    """Two migrations that depend on each other, so no application order exists.

    ``createItem`` only: the cycle is rejected by ``MigrationSet.ordered`` inside
    ``load_migrations``, before any content file is read, so bodies would be two
    more things to keep in sync for no coverage.
    """
    for migration_id, dependency, item in (
        (CYCLE_FIRST_ID, CYCLE_SECOND_ID, "architecture.cycle-one"),
        (CYCLE_SECOND_ID, CYCLE_FIRST_ID, "architecture.cycle-two"),
    ):
        (root / f".theurian/migrations/{migration_id}-cycle.yaml").write_text(
            f"apiVersion: theurian.dev/v1\n"
            f"id: {migration_id}\n"
            f"createdAt: 2026-08-02T10:00:00+09:00\n"
            f"author: engineer@example.com\n"
            f"dependsOn:\n"
            f"  - {dependency}\n"
            f"operations:\n"
            f"  - op: createItem\n"
            f"    itemId: {item}\n"
            f"    kind: architecture\n"
            f"    namespace: backend\n"
            f"    owner: platform-team\n"
        )


def test_a_dependency_cycle_gets_the_cycles_cure_not_the_generic_migration_one(
    project: Path,
) -> None:
    """PR #626 round one, code-review M-2 / adversarial MEDIUM-2: the discrimination.

    ``_context_remedy`` answers three ways below a self-describing error --
    ``MigrationCycleError`` first, then ``MigrationError``, then the caller's
    ``default`` -- and face A's fix routed this branch through it. Only the middle
    arm was driven. That round measured it: rewriting the call site to the
    hardcoded ``exc.remedy or "Fix the migration file, then retry."`` that
    ``migrate status`` spells was one of its four full-suite survivors. Under that
    rewrite a cycle -- which is reachable from ``project status``, because
    ``load_migrations`` orders the set inside ``resolve_context`` -- would tell the
    reader to fix a file, when what is wrong is a relationship between two of them
    and neither is malformed.

    So the cure is asserted **and** the one it must not be, because the two are
    both plausible sentences about a broken migration set and only one of them
    points at the edit that fixes this state.

    GREEN at ``83004c61``, which is the point: the discrimination was already
    correct and nothing drove it, so a regression to the hardcoded rule was a
    silent one.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_cyclic_migrations(project)

    code, payload = _invoke("project", "status")

    assert code == 0, "this branch answers at exit 0 for a repository it cannot resolve"
    assert "Migration dependency cycle" in payload["reason"], (
        f"the fixture must drive a cycle rather than some other load failure: {payload['reason']!r}"
    )
    assert payload["remedy"] == "Break the dependency cycle shown above, then retry.", (
        f"`reason` names the cycle, so the cure is the edit that breaks it: {payload['remedy']!r}"
    )
    assert payload["remedy"] != "Fix the migration file, then retry.", (
        "the generic migration cure points at a file, and no file here is malformed -- what is "
        "wrong is the edge between two well-formed ones"
    )


def test_a_repository_whose_name_derives_no_id_gets_the_branchs_artefact_free_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #626 round one, code-review M-2: the ``default`` limb, driven at last.

    :data:`_UNRESOLVED_STATUS_REMEDY` is what ``project status`` publishes for a
    resolution failure that describes neither itself nor its type, and that round
    measured no test driving it. One state does: ``derive_project_id`` slugs the
    directory name through ``[^a-z0-9]+`` and strips the hyphens, so a repository
    named ``---`` leaves an empty slug and raises a bare ``ProjectError`` --
    ``remedy`` the empty string, and not a ``MigrationError``, so both of
    ``_context_remedy``'s named arms decline and the ``default`` renders.

    Asserted as equality against the imported constant, so a re-pin of the
    sentence moves both together, and then asserted to name **no artefact**: that
    is the decision recorded beside the constant, and it is the whole reason this
    branch's default differs from every other ``default`` in the module. One call
    to ``resolve_context`` reaches the working tree, the registry, every migration
    and the installed schemas, so a remedy naming one of them would name a
    non-cause for every arrival but one -- the defect #481, #520 and #525 each
    shipped.

    The directory is created here rather than through the ``project`` fixture,
    which names its repository ``demo``.

    GREEN at ``83004c61``, like the cycle test above and for the same reason: the
    limb was correct and undriven. Rewriting the constant to name an artefact
    keeps the equality above green -- it is imported, so both sides move -- and
    goes RED on the artefact check, which is why there are two assertions and not
    one.
    """
    root = tmp_path / "---"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)

    code, payload = _invoke("project", "status")

    assert code == 0, "this branch answers at exit 0 for a repository it cannot resolve"
    assert "projectId" not in payload, "the fixture must reach the unresolved branch"
    assert "Cannot derive a project id" in payload["reason"], (
        f"and it must fail on the id derivation rather than on a migration or the registry, "
        f"or the arm under test is not the one that rendered: {payload['reason']!r}"
    )
    assert payload["remedy"] == _UNRESOLVED_STATUS_REMEDY, (
        f"an exception that describes nothing still gets this branch's own cure, and it is the "
        f"one shipped beside the constant rather than a copy: {payload['remedy']!r}"
    )
    assert not [
        artefact
        for artefact in (str(root), "projects.json", "active.json", ".theurian", ".gitignore")
        if artefact in payload["remedy"]
    ], (
        f"and it names no artefact: this branch cannot know which of the several files "
        f"`resolve_context` touches was the one that failed, and `reason` is the field that "
        f"does say: {payload['remedy']!r}"
    )


# -- the destructive half of #381: what a delete-the-registry cure may claim --
#
# `projects.json` is the enumeration of every project's registration, and an
# entry's `registeredAt` is recorded in no project's own `.theurian/`. What a
# cure offering to delete it may claim is `registry_deletion_cure_claims` --
# shared with the unit pins rather than restated here, because this family
# (PR #596) recurs at exactly the seam between two files that each keep their own
# copy of the rule. The CLI kept such a copy until `30e460b9`: `_context_remedy`'s
# registry `default` was a second spelling of the same destructive offer one
# layer up. There is one cure now, with one lead per `RegistryFailureArm`, and
# both of those call sites pass the `UNKNOWN` arm of it.
#
# Recount the render sites rather than trusting a number written here:
#
#   git grep -n "registry_deletion_remedy(" packages/theurian-core/src/theurian
#
# At `30e460b9`, and unchanged in number by the errno split since, that is the
# definition, four raises inside `ProjectRegistry` -- which reach a caller as
# `exc.remedy`, and which `tests/unit/test_project_registry_errors.py` holds arm
# by arm for the conditions a mode produces, and
# `tests/integration/test_registry_cure_execution.py` for the ones it does
# not -- and two `_context_remedy` defaults in `cli/commands.py`, at the two
# surfaces that read the whole registry: `project list` and
# `_RegistryRead.failure_fields`. Those two are what the cases below render.
#
# A `default` renders only when the raising error carries no remedy of its own,
# and every refusal `ProjectRegistry` raises today carries one -- hence the
# monkeypatched raise below. That is a claim about the `default`, not about the
# `UNKNOWN` arm it passes: since `_arm_for_a_refused_registry`, every
# non-`EACCES` refusal reaches that arm through a real raise carrying a real
# remedy, and those are driven in the execution module named above.
#
# One rendering is deliberately not driven here: `failure_fields` also publishes
# it as `registryRemedy` on `project status`' unresolved branch. That is the same
# string from the same call site as the resolved case below, so what it would add
# is coverage of the key name -- which the #381 tests above already hold.


def _raise_without_a_remedy(self: object) -> dict[str, dict[str, str]]:
    """A registry failure that carries no cure of its own, so the default renders.

    Every refusal `ProjectRegistry` actually raises today sets a remedy, which is
    why *this* rendering of the `UNKNOWN` arm -- the `default` these two surfaces
    pass -- is otherwise unreachable, and was unrendered by any test until this
    one. It is still shipped text: a fifth self-describing error added to those
    readers without a remedy would publish it, and the arm's own note in
    `RegistryFailureArm` is written for exactly that reader.

    The arm itself is no longer only a default. `_arm_for_a_refused_registry`
    routes every non-`EACCES` refusal to it, so a directory sitting at the
    registry path reaches a caller on this arm through a raise that does carry a
    remedy; `tests/integration/test_registry_cure_execution.py` plants that one.
    What this patch isolates is the seam, not the arm.
    """
    raise ProjectError("the registry could not be read", remedy="")


def _published_remedy(payload: dict[str, Any]) -> str:
    """The payload's ``remedy``, refused unless it is a string.

    `_invoke` parses into `dict[str, Any]`, so a helper returning `payload[...]`
    hands the shape checks an `Any` and every one of them passes vacuously on a
    `None` or a list. This is where that stops.
    """
    remedy = payload["remedy"]
    assert isinstance(remedy, str), f"remedy must be a string, got {remedy!r}"
    return remedy


def _list_over_a_registry_error_with_no_remedy(monkeypatch: pytest.MonkeyPatch) -> str:
    """The cure's `UNKNOWN` arm, rendered through `project list`'s refusal."""
    monkeypatch.setattr(ProjectRegistry, "load", _raise_without_a_remedy)
    code, payload = _invoke("project", "list")

    assert code == 1, "the fixture must reach the refusal branch"
    return _published_remedy(payload)


def _status_over_a_registry_error_with_no_remedy(monkeypatch: pytest.MonkeyPatch) -> str:
    """The same `UNKNOWN` arm, rendered through `failure_fields`.

    The resolved branch: `resolve_context` reads the registry through
    `ids_for_root`, which this patch does not touch, so the project resolves and
    the *second* read is the one that fails. `failure_fields` therefore spells
    the pair `reason`/`remedy` here, not `registryReason`/`registryRemedy`.
    """
    _invoke("project", "register")
    monkeypatch.setattr(ProjectRegistry, "load", _raise_without_a_remedy)
    code, payload = _invoke("project", "status")

    assert code == 0, "the fixture must reach the degraded status, not a refusal"
    assert payload["registered"] is None, "and the failure must be the one it degraded on"
    return _published_remedy(payload)


@pytest.mark.parametrize(
    "render",
    [_list_over_a_registry_error_with_no_remedy, _status_over_a_registry_error_with_no_remedy],
    ids=["project-list", "project-status"],
)
def test_every_registry_cure_that_offers_deletion_names_what_the_deletion_costs(
    project: Path,
    registry_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render: Callable[[pytest.MonkeyPatch], str],
) -> None:
    """The same lens as the unit pins, over the cure a CLI caller is handed.

    Both cases render the one `UNKNOWN` arm, so the parametrize is over the
    *routes* rather than over two texts: a unified cure is only unified while
    every route still arrives at it. The whole shape is asserted rather than the
    cost alone; the cost is simply the property that was RED.

    **Which arm arrives is asserted too, byte for byte.** The shape holds for all
    four arms -- `test_each_registry_failure_arm_renders_the_cure_it_is_pinned_to`
    asserts exactly that -- so it cannot tell them apart. Measured with the two
    assertions below removed: swapping both `default`s to the `FILE_UNREADABLE`
    arm leaves the seven-file scope of this branch green, while it tells a reader
    whose registry this surface has never even opened to `chmod u+r` it. The
    equality is what refuses that, and the `UNKNOWN` arm is the only one either
    site may pass -- a `default` renders for an error carrying no `remedy`, so
    neither site knows what opened and what did not.

    The text this replaced was "Inspect {path}, or delete it and re-register each
    project.", written out separately at each of those two call sites. It led
    with inspection and promised nothing, so it cleared two of today's four
    properties; what it never said was what the deletion removes, and "each
    project" reads as "the ones you care about" rather than "every registration
    on this machine". RED on the cost property at `2d3c23bb`, GREEN at
    `043f0c5e`.

    Walked over the *rendered* text rather than asserted against a table of call
    sites: a table agrees with the routing it checks, while a render can only
    agree with what a caller is actually handed. That is PR #596's rule for this
    family.

    **Its reach is these two routes and no further.** The parametrize is a
    literal pair, so a third route added to `cli/commands.py` is checked by
    nothing here -- it is forgotten by exactly the sort of list this file argues
    against, and claiming otherwise would be worse than the gap. What does cover
    a new *arm* is `tests/unit/test_registry_deletion_cure_claims.py`, which
    walks `RegistryFailureArm` itself and pins every member's bytes. The
    reflective walk that would do the same for render routes is recorded on issue
    #620, as population for that issue's structural contract test.
    """
    _invoke("init")

    remedy = render(monkeypatch)

    assert_registry_deletion_cure_shape(remedy, where=f"{render.__name__}'s rendered remedy")
    assert "named in the message beside this remedy" in remedy, (
        "this arm prescribes nothing and sends the reader to the message it travels with, "
        "which is the half a `chmod`-shaped arm arriving here would replace"
    )
    assert remedy == the_pinned_cure("unknown", registry_path), (
        f"{render.__name__} must publish the UNKNOWN arm of the shared cure, unaltered in "
        f"either direction"
    )


def test_project_list_publishes_the_registry_cure_with_no_sentence_about_a_reason_key(
    project: Path, registry_path: Path
) -> None:
    """The fence on where the repair-order sentence is appended.

    ``_registry_cure_in_repair_order`` tells the reader that if ``reason`` names a
    different failure they must fix that one first. It is appended at
    ``_unresolved_status``' emit site and not inside the shared cure, and this is
    the reason why: ``project list`` publishes ``error`` and ``remedy``, has no
    ``reason`` key at all, and reports exactly one failure -- the registry's. A
    sentence sending this reader to compare against ``reason`` would point at a
    key that is not in the payload and at a second failure that does not exist.

    Pinned as byte equality against the ``unparsable`` arm rather than as an
    absence, so the assertion also refuses the opposite drift: the cure arriving
    here with anything else added or removed.
    """
    _invoke("init")
    registry_path.write_bytes(_UNPARSEABLE_REGISTRY)

    code, payload = _invoke("project", "list")

    assert code == 1, "the fixture must reach the whole-file refusal"
    assert "reason" not in payload, (
        "there is no `reason` key on this payload for a repair-order sentence to point at"
    )
    assert THE_REPAIR_ORDER_SENTENCE not in payload["remedy"], (
        "the ordering sentence belongs to the payload that publishes two failures, not to "
        "the cure every surface shares"
    )
    assert payload["remedy"] == the_pinned_cure("unparsable", registry_path), (
        "and what this surface publishes is the shared cure, unaltered in either direction"
    )


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


# -- issue #226: `registered` answers about the registry, not about resolution --
#
# `project status` reaches `_unresolved_status` whenever `resolve_context`
# fails, and *why* it failed is mostly nothing to do with registration: an
# unreadable migrations directory, a malformed migration, a state schema that
# will not parse. Publishing `registered: false` for all of them made the one
# command a confused user runs first contradict `project list` in the same
# breath, and told them to run `project register` for a repository that was
# already registered.


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_status_keeps_a_registered_project_registered_when_the_context_will_not_resolve(
    project: Path,
) -> None:
    """Issue #226, and the two surfaces that have to answer one fact the same way.

    ``chmod 000 .theurian/migrations`` is a failure of the *project*, not of the
    registry: the registry is readable, parses, and holds this root. Measured
    before the fix, ``project status`` published ``registered: false`` while
    ``project list`` -- run against the same file, in the same test -- listed
    this very ``rootPath`` at ``count: 1``.

    ``registered`` is asserted ``is True`` rather than truthily, because the
    whole defect is a field answering a question it never asked: ``None`` would
    be a different wrong answer here, and a bare ``assert status["registered"]``
    cannot tell the two apart.
    """
    _invoke("init")
    _invoke("project", "register")

    migrations = project / ".theurian/migrations"
    migrations.chmod(0o000)
    try:
        code, status = _invoke("project", "status")
        _, listed = _invoke("project", "list")
    finally:
        migrations.chmod(0o700)

    assert code == 0, "status must report, not fail, on an unresolved project"
    assert status["registered"] is True, (
        "the registry holds this root; a resolution failure elsewhere is not a deregistration"
    )
    assert status["reason"], "the resolution failure is still reported, it is just not the answer"
    assert "indexStale" not in status, (
        "nothing here read the state pointer, so freshness is unasked rather than false"
    )

    assert listed["count"] == 1, "the fixture must be registered for this test to mean anything"
    assert {Path(row["rootPath"]).resolve() for row in listed["projects"]} == {project.resolve()}, (
        "`project list` and `project status` read the same file and must not disagree about it"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_status_says_it_cannot_know_when_the_registry_itself_cannot_be_opened(
    project: Path, registry_path: Path
) -> None:
    """The fence around the fix above: membership is unknown, not denied.

    The tempting shape for "does the registry hold this root" is a scan of the
    entries that loaded, and that scan answers ``False`` for an empty result --
    including the empty result a file nobody can open produces. ``registered:
    false`` there is the same guess this command already refuses to make for a
    file it cannot parse; this pins the third way a registry goes unreadable,
    ``EACCES`` at ``open`` rather than a body that is not JSON, which no CLI
    test reached.
    """
    _invoke("init")
    _invoke("project", "register")

    registry_path.chmod(0o000)
    try:
        code, payload = _invoke("project", "status")
    finally:
        registry_path.chmod(0o600)

    assert code == 0, "an unreadable registry is a status, not a crash"
    assert payload["registered"] is None, (
        "the file cannot be searched, and False would claim it was"
    )
    assert "cannot be opened" in payload["reason"]
    assert "re-register each project with `theurian project register`" in payload["remedy"], (
        "a `cannot know` with no cure beside it is unactionable"
    )
    assert payload["unreadable"] == [], "no ids could be partitioned, and the field stays present"


def test_status_does_not_guess_index_freshness_for_a_project_it_never_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``indexStale: false`` was an answer to a question this branch never asks.

    Nothing on the unresolved path reads the active state pointer or computes a
    state hash -- there is no project to compute one for -- so the hardcoded
    ``false`` claimed a freshly built index for a directory Theurian had not
    looked at. Absent rather than ``null``, which is the rule
    ``statePointerCorrupt`` already follows in this same payload: ``null`` is
    "asked, and the answer is unknowable" (``registered`` on a broken registry),
    absence is "never asked".

    Its ``registered`` assertion pins the short-circuit, not the membership
    rule: outside a Git working tree ``find_git_root`` is ``None`` and
    ``_unresolved_status`` never calls ``holds_root`` at all. The three tests
    below are what hold that rule.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, status = _invoke("project", "status")

    assert code == 0
    assert "indexStale" not in status, "a field nothing computed must not be published as false"
    assert status["registered"] is False, (
        "a directory outside a Git working tree is genuinely unregistered, and that answer stays"
    )
    assert "not inside a Git repository" in status["reason"], (
        "and the two unresolved shapes stay distinguishable: this one is not a permission failure"
    )


# -- what `holds_root` actually keys on ------------------------------------
#
# The tests above all run in a repository that is either registered or in no
# registry at all, so a rule reading "does the registry hold *anything*" passes
# every one of them. Measured: replacing the membership scan with
# `bool(self.entries)` survived this whole file, and so did keying it on
# `Path.cwd()` instead of the Git root. Each test below is aimed at one of
# those, and a broken migration is what fails resolution -- not a `chmod`,
# which the CI job running as root cannot be refused by.


def test_status_does_not_borrow_a_neighbours_registration_for_an_unregistered_root(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """Membership is about *this* root, and a populated registry is not a match.

    The neighbour is readable and well-formed -- nothing here is ambiguous --
    so `unreadable` is empty and the only thing standing between this
    repository and a `true` is the root comparison itself.
    """
    _invoke("init")
    registry_path.write_text(
        json.dumps(
            {"neighbour": {"rootPath": str(tmp_path / "elsewhere"), "defaultBranch": "main"}}
        )
    )
    _write_malformed_yaml_migration(project)

    code, status = _invoke("project", "status")

    assert code == 0
    assert status["registered"] is False, (
        "another root's entry is not this root's registration, however readable it is"
    )
    assert status["unreadable"] == [], "the neighbour is well-formed; nothing here is ambiguous"


def test_status_answers_for_the_repository_not_the_directory_it_was_run_from(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registration names the working-tree root, and `status` runs anywhere in it.

    `_unresolved_status` asks `find_git_root` and must pass *that* to the
    membership check. Keying it on `Path.cwd()` instead is invisible from the
    repository root, where the two are the same path, and reports a registered
    project as unregistered from any subdirectory -- which is where a developer
    actually stands.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_malformed_yaml_migration(project)
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    code, status = _invoke("project", "status")

    assert code == 0
    assert status["registered"] is True, (
        "the registry names the working-tree root, and this is inside it"
    )


def test_a_vendored_checkout_is_not_registered_by_the_repository_around_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test above says "inside the tree counts". This says where that stops.

    ``find_git_root`` answers with the *innermost* working tree, so a vendored
    or submodule checkout at ``<registered>/vendor/inner`` is its own project and
    nothing registers it. A membership test that accepted an ancestor -- reading
    "is this root under a registered one" instead of "is this root registered" --
    would satisfy the subdirectory test above just as well, and it survived the
    whole suite until this existed.

    It is the same defect issue #226 is about, reached from the other side: a
    directory answering as a project it is not, this time the enclosing
    repository rather than a name-colliding neighbour. Vendoring a dependency is
    an ordinary thing to do, so this is not an exotic input.
    """
    _invoke("init")
    _invoke("project", "register")
    inner = project / "vendor" / "inner"
    inner.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=inner, check=True, capture_output=True)  # noqa: S603
    _write_malformed_yaml_migration(project)
    monkeypatch.chdir(inner)

    code, status = _invoke("project", "status")

    assert code == 0
    assert status["registered"] is False, (
        "the entry names the outer root; being underneath it is not being it"
    )


def test_status_matches_a_registered_root_written_in_a_non_normal_form(
    project: Path, registry_path: Path
) -> None:
    """The comparison normalises both sides, and a hand edit is why it has to.

    The registry lives in the user's home directory and the product's own
    remedies tell people to edit it, so `rootPath` arrives in whatever absolute
    spelling they typed -- here with a `/..` and a trailing `/.` that name
    exactly this root. `entry_root` resolves it, the same way
    `ProjectRegistry.load` and `ids_for_root` do; a raw string comparison
    against `str(root)` would call this repository unregistered while
    `project list` went on listing it.
    """
    _invoke("init")
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    non_normal = f"{project.parent}/./{project.name}/../{project.name}/."
    assert non_normal != str(project.resolve()), "the fixture must not be normal already"
    registry_path.write_text(json.dumps({"demo": {**entry, "rootPath": non_normal}}))
    _write_malformed_yaml_migration(project)

    code, status = _invoke("project", "status")

    assert code == 0
    assert status["unreadable"] == [], "an absolute path that resolves is a readable entry"
    assert status["registered"] is True, "the entry names this root, spelled the long way round"


def test_status_cannot_say_while_an_unusable_key_holds_an_entry_for_another_root(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """The deliberately broader `null`, kept -- and now pinned rather than assumed.

    `ProjectRegistry.ids_for_root` could answer here: the offending entry names
    a root, and it is not this one, so the id-shape defect is somebody else's
    problem. `_RegistryRead` cannot see that -- `entries` holds what `load`
    kept, and `load` keeps neither a rootless entry nor one under a key no
    consumer accepts -- so it refuses for the whole file rather than reasoning
    about an entry it does not have.

    That is a choice, not an oversight (`holds_root`'s docstring records it),
    and the conservative direction: "cannot say" about a registry that is partly
    illegible, with `unreadable` naming the entry to remove. Pinned so that
    narrowing it later is a decision somebody takes rather than a diff nobody
    notices.
    """
    _invoke("init")
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(
        json.dumps(
            {
                "demo": entry,
                "Team One/API": {"rootPath": str(tmp_path / "elsewhere"), "defaultBranch": "main"},
            }
        )
    )
    _write_malformed_yaml_migration(project)

    code, status = _invoke("project", "status")

    assert code == 0
    assert status["registered"] is None, (
        "a readable entry names this root, and the answer is still withheld while the file "
        "holds an entry this reader cannot attribute"
    )
    assert status["unreadable"] == ["Team One/API"], "and the entry to remove is named"


def test_the_resolved_branch_reaches_the_same_null_with_nothing_racing_it(
    project: Path, registry_path: Path, tmp_path: Path
) -> None:
    """The same registry, one line lighter, and it is the *resolved* branch.

    The claim this pins was made in a commit body and was wrong: that a resolved
    payload can only meet an unreadable entry through the race between
    ``resolve_context``'s registry read and this command's own, because
    ``ids_for_root`` refuses on any unreadable entry. It does not refuse on this
    one. It raises for an entry naming *no* root, and for an unusable id among
    the entries naming *this* root -- and an unusable key over an absolute
    ``rootPath`` pointing somewhere else is neither. ``unreadable_ids`` still
    reports it, because ``load`` cannot hand out a key no consumer accepts.

    So the resolved branch reaches ``registered: null`` deterministically, with
    nothing racing anything, and this state is what proves it: the payload is
    unmistakably the resolved shape -- ``projectId`` and ``root`` present and
    correct -- while membership is withheld. Dropping the broader refusal would
    make this ``true``, which is why it is asserted on this branch and not only
    on the unresolved one above.
    """
    _invoke("init")
    _invoke("project", "register")
    entry = json.loads(registry_path.read_text())["demo"]
    registry_path.write_text(
        json.dumps(
            {
                "demo": entry,
                "Team One/API": {"rootPath": str(tmp_path / "elsewhere"), "defaultBranch": "main"},
            }
        )
    )

    code, status = _invoke("project", "status")

    assert code == 0
    assert status["projectId"] == "demo", "this is the resolved payload, not the unresolved one"
    assert "root" in status, "and it carries the resolved-only keys that say so"
    assert status["registered"] is None, (
        "the resolved branch withholds membership for the reason the unresolved one does, "
        "and reaches it without a race"
    )
    assert status["unreadable"] == ["Team One/API"]


# -- issue #100: `indexStale` answers about the index -----------------------
#
# `project status` computed its own verdict from the *canonical* state pointer
# -- `active is None or active.state_hash != context.state_hash` -- which is a
# statement about whether `migrate apply` is up to date, published under a name
# about the index. It never read `.theurian/state/active-index.json` at all, so
# every axis `theurian index status` recognises that lives in that file was
# invisible here: a project that had never built an index reported
# `indexStale: false` in the same second `index status` reported
# `built: false, stale: true`.
#
# The tests below drive one axis each through the real CLI and assert both
# halves: the verdict itself, and that the two commands agree on it.
#
# Which half is load-bearing differs by test, and the difference is worth
# recording. Measured against the old expression, the parameterized cases fail
# on the `indexStale is True` line -- the agreement assertion beside it would
# hold for any fork of the computation that happened to agree. It is
# `test_status_agrees_with_index_status_where_the_verdict_moved_the_other_way`
# that the agreement carries, because there the two verdicts *differ* under the
# old computation and only the comparison can see it.

#: A second migration over the item :data:`MIGRATION` creates, so applying it
#: moves the state hash without touching anything the index pointer records.
#: That is the state-hash axis in its pure form: after `migrate apply` the
#: canonical pointer is current again, so the *old* computation reported a fresh
#: index while the published build was one migration behind.
SECOND_MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"
SECOND_REVISION_ID = "01K1BBBREV01234567890ABCDE"
REVISED_BODY = BODY + "\nTokens expire after 15 minutes.\n"

SECOND_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {SECOND_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
description: Give tokens an expiry.
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {SECOND_REVISION_ID}
    expectedRevision: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy-v2.md
    contentSha256: {body_pin(REVISED_BODY)}
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


def _edit_index_pointer(root: Path, **fields: Any) -> None:
    """Overwrite keys in the published index pointer, in place.

    The pointer is derived, git-ignored and unsigned (SEC-7), so a hand edit is
    an input the product already has to answer for -- and it is the only way to
    reach the ``purgeFailed`` and ``orphaned`` axes without staging a failing
    purge or a project rename around them, neither of which is what these tests
    are about.
    """
    pointer = root / ".theurian/state/active-index.json"
    pointer.write_text(json.dumps({**json.loads(pointer.read_text()), **fields}, indent=2))


def _never_built(_root: Path) -> None:
    """No pointer at all: `migrate apply` ran, `index build` did not.

    This is the state issue #100 reports, and it is the state the *whole verdict*
    has to answer -- but it does not exercise the ``published is None`` term on
    its own. Measured: dropping that term from the disjunction survives this file
    and ``test_index_fallback`` entirely, because with no pointer ``indexed`` is
    ``None`` and the schema version is ``None``, so two later terms each carry
    the state alone. The term stays for readability -- it names the axis in the
    verdict rather than leaving it inferred from two ``None`` comparisons -- and
    this note is here so nobody reads the case as pinning it.
    """


def _state_hash_behind(root: Path) -> None:
    """A published build, then one more migration applied on top of it."""
    assert _invoke("index", "build")[0] == 0
    (root / ".theurian/knowledge/architecture/auth-policy-v2.md").write_text(REVISED_BODY)
    (root / f".theurian/migrations/{SECOND_MIGRATION_ID}-revise.yaml").write_text(SECOND_MIGRATION)
    assert _invoke("migrate", "apply")[0] == 0


def _orphaned(root: Path) -> None:
    """A published build stamped with another project's id."""
    assert _invoke("index", "build")[0] == 0
    _edit_index_pointer(root, projectId="somebody-else")


def _purge_failed(root: Path) -> None:
    """A published build whose withdrawal purge did not complete (GHSA-97q9-xxfg-33r6).

    The state hash is left matching deliberately: the taint has to make the
    build stale on its own axis, which is the whole point of the field. Every
    other axis here is clean, so a verdict that misses this one reports a fresh
    index for a build that still holds withdrawn rows.
    """
    assert _invoke("index", "build")[0] == 0
    _edit_index_pointer(root, purgeFailed=True)


def _purge_failed_as_a_truthy_string(root: Path) -> None:
    """The same taint written as ``"false"``, which is a *truthy* string.

    The pointer is derived and unsigned (SEC-7), so any value a hand edit or a
    half-written generator leaves under the key is what arrives -- and the
    verdict reads it the way the serve path's own ``if published.get(
    "purgeFailed")`` reads it, by truthiness rather than ``is True``. Narrowing
    it to ``is True`` survives every other test in this file: this is the input
    that tells the two readings apart, and it is the direction that matters,
    because the safe answer for an unparseable taint is "stale".
    """
    assert _invoke("index", "build")[0] == 0
    _edit_index_pointer(root, purgeFailed="false")


#: The remedy `theurian index build` prints for a build that is simply behind.
#: The period is load-bearing: the orphaned arm says "Run `theurian index
#: build`;" and the purge-failed arm "Run `theurian index build` to produce a
#: clean build", so this string is a substring of neither.
_PLAIN_REBUILD = "Run `theurian index build`."

#: A phrase from the orphaned arm and from no other.
_ORPHANED_PHRASE = "different project id"

#: A phrase from the purge-failed arm and from no other -- the same one
#: ``tests/unit/test_index_status_remedy.py`` holds that arm to.
_PURGE_FAILED_PHRASE = "the purge that follows a withdrawal did not complete"


@pytest.mark.parametrize(
    ("prepare", "axis", "remedy_phrase"),
    [
        pytest.param(_never_built, "no published build", _PLAIN_REBUILD, id="never-built"),
        pytest.param(
            _state_hash_behind, "the build's state hash", _PLAIN_REBUILD, id="state-hash-behind"
        ),
        pytest.param(_orphaned, "another project's id", _ORPHANED_PHRASE, id="orphaned"),
        pytest.param(
            _purge_failed, "a failed withdrawal purge", _PURGE_FAILED_PHRASE, id="purge-failed"
        ),
        pytest.param(
            _purge_failed_as_a_truthy_string,
            "a taint written as a truthy string",
            _PURGE_FAILED_PHRASE,
            id="purge-failed-truthy-string",
        ),
    ],
)
def test_status_reports_the_index_stale_on_every_axis_index_status_recognises(
    project: Path, prepare: Any, axis: str, remedy_phrase: str
) -> None:
    """Issue #100, AC-5 and AC-6: one staleness verdict, two surfaces.

    ``never-built`` is the reproduction the issue names, and the rest are the
    axes that live in the index pointer -- a file the old computation never
    opened. ``state-hash-behind`` is the one that shows *why* reading the
    canonical pointer instead is not merely narrow but inverted: applying the
    second migration makes ``active.state_hash == context.state_hash`` again, so
    the old expression answered ``false`` at the exact moment the published build
    fell a migration behind.

    Each case asserts the *distinctive* remedy as well as the verdict, and that
    is not decoration. ``IndexStaleness`` now carries ``orphaned`` and
    ``purge_failed`` to ``remedy_for`` as fields, and mutants forcing either to
    ``False`` -- at the dataclass or at the call site -- left the shipped
    remedies degraded to the plain rebuild while every assertion in this file
    passed. The phrases above are substrings of one arm each, so a degraded
    remedy is a failure and not a silence.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0
    prepare(project)

    code, status = _invoke("project", "status")
    index_code, index_status = _invoke("index", "status")

    assert code == 0
    assert index_code == 0
    assert index_status["stale"] is True, (
        f"the fixture must make the index stale on {axis} for this test to mean anything"
    )
    assert status["indexStale"] is True, (
        f"`project status` reported a fresh index while `index status` called it stale on {axis}"
    )
    assert status["indexStale"] == index_status["stale"], (
        "both commands answer one fact and must not compute it twice"
    )
    assert remedy_phrase in index_status["remedy"], (
        f"the remedy for {axis} degraded to a message that does not name it: "
        f"{index_status['remedy']!r}"
    )


def test_status_calls_a_freshly_built_index_fresh(project: Path) -> None:
    """The fence around the four above: the verdict is not stuck on ``true``.

    Every test in this section drives a stale axis, so a verdict hardcoded to
    ``true`` -- or one that simply forgot to read the pointer in the other
    direction -- passes all of them. This is the state where nothing is stale,
    and both surfaces have to say so.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0
    assert _invoke("index", "build")[0] == 0

    _, status = _invoke("project", "status")
    _, index_status = _invoke("index", "status")

    assert index_status["stale"] is False, "the fixture just built the index from current state"
    assert status["indexStale"] is False, (
        "a build published from the current state hash is not stale on any axis"
    )


#: An ``indexBuildId`` whose filename no ordinary filesystem will accept.
#:
#: ``index_for`` builds ``theurian-index-<id>.sqlite``, so ``NAME_MAX`` (255 on
#: macOS and on the Linux filesystems CI runs on) is exceeded at 234 characters:
#: 15 + 234 + 7 = 256. Measured through the real CLI on macOS before the fix --
#: 200 and 233 answered, 234, 240, 256 and 300 each ended both status commands
#: in an uncaught ``OSError`` at exit 1 with empty stdout. 300 is used rather
#: than the boundary so the input stays refused on a filesystem with a slightly
#: larger limit; the test below skips instead of passing vacuously if some
#: filesystem accepts it anyway.
_OVERLONG_BUILD_ID = "A" * 300


def test_status_answers_for_a_pointer_naming_a_filename_the_platform_refuses(
    project: Path, tmp_path: Path
) -> None:
    """A pointer that names an unusable filename is a status, not a traceback.

    ``index_schema_version`` (``cli/index_status_report.py``) probed the build
    with ``path.is_file()`` *outside* its ``try``. ``Path.is_file()`` swallows
    only the errnos ``pathlib`` lists as "this is not a file", and
    ``ENAMETOOLONG`` is not among them, so an over-long ``indexBuildId`` in the
    unsigned, git-ignored pointer (SEC-7) escaped as a bare ``OSError``: exit 1,
    empty stdout, none of the ``{error, remedy}`` shape CP-2 promises -- from
    ``theurian project status``, which had answered this at exit 0 before this
    branch made it read the file at all, *and* from ``theurian index status``,
    which had crashed this way since the probe was written.

    ``index_for``'s own conversion cannot catch it: ``Path.resolve()`` in
    non-strict mode never stats, so the name it returns is one the OS has not
    yet been asked about. Both surfaces now answer, with schema ``0`` -- this
    function's documented "unknowable" -- which makes the build stale.

    The other two ``index_for`` callers (``index gc``, ``mcp/search``) reach the
    same probe by their own routes and are issue #388's; nothing here touches
    them, which is also why this axis is pinned here rather than added to
    ``test_index_fallback``'s pointer enumeration, where every recipe is driven
    through the search path as well.
    """
    probe = tmp_path / f"theurian-index-{_OVERLONG_BUILD_ID}.sqlite"
    try:
        probe.is_file()
    except OSError:
        pass
    else:
        pytest.skip("this filesystem accepts the name, so there is no refusal to answer for")

    _invoke("init")
    _invoke("project", "register")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0
    assert _invoke("index", "build")[0] == 0
    _edit_index_pointer(project, indexBuildId=_OVERLONG_BUILD_ID)

    code, status = _invoke("project", "status")
    index_code, index_status = _invoke("index", "status")

    assert code == 0, "a pointer naming an impossible filename is a status, not a crash"
    assert index_code == 0, "and the same is true of the surface that has always read it"
    assert status["indexStale"] is True, (
        "a build whose schema version cannot be established is not one to serve from"
    )
    assert index_status["indexSchemaVersion"] == 0, (
        "0 is this function's `unknowable`, and it must reach the payload rather than an errno"
    )
    assert status["indexStale"] == index_status["stale"]


def test_status_agrees_with_index_status_where_the_verdict_moved_the_other_way(
    project: Path,
) -> None:
    """One member of the class where the verdict moved the *other* way.

    The class, not a count: the canonical state pointer no longer participates
    in the verdict, so ``indexStale`` reads ``false`` wherever
    ``.theurian/state/active.json`` disagrees with the migrations while a
    published build still matches them. Measured, three members -- the pointer is
    missing (this test), the pointer is unreadable (the test below, which is the
    member with no agreement to assert), or the pointer parses and names a
    different state hash.

    Deleting the pointer leaves the index genuinely current -- it was built from
    the state hash the migrations still derive -- while canonical state has no
    pointer at all. The old computation's ``active is None`` made that
    ``indexStale: true``; the index's own verdict is ``false``, which is what
    ``theurian index status`` has always answered here (``stale: false`` beside
    ``knowledgeNotApplied: true``).

    Both are asserted, so this reads as the two surfaces agreeing rather than as
    a claim that ``false`` is the interesting answer: what the payload says about
    the *state* is ``activeStateHash`` and ``stateBuilt``, and those still report
    it.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0
    assert _invoke("index", "build")[0] == 0
    (project / ".theurian/state/active.json").unlink()

    _, status = _invoke("project", "status")
    _, index_status = _invoke("index", "status")

    assert index_status["knowledgeNotApplied"] is True, "the state pointer is gone"
    assert index_status["stale"] is False, "and the published build is still current"
    assert status["indexStale"] == index_status["stale"], (
        "the two surfaces answer one fact, including where the answer moved"
    )
    assert status["activeStateHash"] is None, (
        "the missing state is reported by the fields that are about the state"
    )


def test_status_is_the_only_surface_answering_over_an_unreadable_state_pointer(
    project: Path,
) -> None:
    """The member of that class where there is no agreement to assert.

    ``theurian index status`` reads the canonical pointer through ``_read_active``,
    which converts an unreadable one into ``{error, remedy}`` and exits 1 -- so
    it publishes no ``stale`` at all here, and the two surfaces cannot be
    compared. ``project status`` reads the same file through its own guarded
    read and keeps its exit-0 contract, which is why it is the only surface
    answering, and why the CHANGELOG says so rather than claiming agreement it
    cannot have.

    What it answers is about the *index*, which is current: the state pointer's
    condition is carried by ``statePointerCorrupt`` and ``reason`` beside it, and
    both are asserted so that a ``false`` here can never be read as silence about
    the file.

    Raw text is the spelling used because all four measure the same on this
    surface except one: a pointer holding a bare JSON array (``[]``) escapes
    ``read_active_state``'s conversion as a ``TypeError`` from
    ``ActiveState.from_json``, crashing *both* commands. That is a pre-existing
    defect of the canonical pointer's reader -- unchanged on this branch, and the
    twin of the ``active-index.json`` refusal fixed above -- not something this
    test may quietly depend on.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0
    assert _invoke("index", "build")[0] == 0
    (project / ".theurian/state/active.json").write_text("not json at all")

    code, status = _invoke("project", "status")
    index_code, _ = _invoke("index", "status")

    assert index_code == 1, "the surface that refuses this state must go on refusing it"
    assert code == 0, "and the surface that answers it must go on answering"
    assert status["indexStale"] is False, "the published build still matches the migrations"
    assert status["statePointerCorrupt"] is True, (
        "the state pointer's condition is not silence -- it is the field named for it"
    )
    assert status["reason"], "and the reason travels with it"


#: Every key ``theurian index status`` publishes, and the whole of it.
#:
#: Frozen for the reason :data:`_PUBLISHED_VALIDATE_KEYS` below is: this payload
#: is a published contract, and a key appearing or disappearing is a decision
#: somebody takes here rather than a diff somebody misses. Recorded now because
#: nothing held it before -- the command's payload was rebuilt out of a shared
#: function (issue #100), and a refactor that silently dropped a key would have
#: had nothing to fail against.
_PUBLISHED_INDEX_STATUS_KEYS = frozenset(
    {
        "built",
        "indexPointerCorrupt",
        "indexBuildId",
        "indexStateHash",
        "indexProjectId",
        "indexSchemaVersion",
        "expectedIndexSchemaVersion",
        "orphaned",
        "purgeFailed",
        "servedSensitivities",
        "indexedSensitivities",
        "profileMismatch",
        "profileUnrecorded",
        "profileUnreadable",
        "builtStateHash",
        "currentStateHash",
        "projectId",
        "stale",
        "knowledgeNotApplied",
        "remedy",
    }
)

#: The six ``index_status`` writes itself, beside ``**index.payload``. Everything
#: else in the set above comes from the shared function.
_INDEX_STATUS_OWN_KEYS = frozenset(
    {"builtStateHash", "currentStateHash", "projectId", "stale", "knowledgeNotApplied", "remedy"}
)


def test_index_status_publishes_exactly_the_recorded_key_set(project: Path) -> None:
    """The contract pin, and the disjointness the merge depends on.

    Two assertions, because the equality alone cannot see the failure that
    matters. ``index_status`` emits ``{**index.payload, <six literals>}``, and a
    literal silently wins a collision: if the shared payload ever gained a key
    named like one of the six, the literal would overwrite it, the count would
    stay 20, and the key set would still match. The second assertion is what
    sees it -- 20 keys out means the two sides contributed disjoint sets.

    That is the recorded decision on the merge: it stays a plain expansion in
    production, and disjointness is held here rather than by defensive code in a
    status command that must never raise. The shared payload's keys are read
    from the live function rather than listed again, so a key added there is
    accounted for automatically and only a *collision* fails.
    """
    _invoke("init")
    _invoke("project", "register")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0
    assert _invoke("index", "build")[0] == 0

    code, payload = _invoke("index", "status")
    shared = index_staleness(
        ProjectPaths.of(project), project_id="demo", current_state_hash="unused-for-key-shape"
    ).payload

    assert code == 0, f"the fixture must build for this to be about keys: {payload}"
    assert set(payload) == _PUBLISHED_INDEX_STATUS_KEYS, (
        f"`theurian index status --json` publishes {sorted(payload)}, and this file records "
        f"{sorted(_PUBLISHED_INDEX_STATUS_KEYS)}. Adding or removing a key is a contract "
        f"change; make it here, in the same change that updates the CHANGELOG."
    )
    assert len(payload) == len(shared) + len(_INDEX_STATUS_OWN_KEYS), (
        f"`index_status` merges {len(shared)} shared keys with {len(_INDEX_STATUS_OWN_KEYS)} of "
        f"its own and published {len(payload)}, so the two sides collide and the literal won "
        f"silently: {sorted(set(shared) & _INDEX_STATUS_OWN_KEYS)}"
    )


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


#: Every key ``migrate validate`` publishes on the success path, and the whole
#: of it -- read out of ``migrate_validate``'s own ``_emit`` call
#: (``cli/commands.py``) and measured against a real invocation below.
#:
#: Frozen deliberately, the way ``test_schemas.py``'s
#: :data:`PUBLISHED_RETRIEVAL_KEYS` is. This payload is a published contract a
#: plugin parses, so a key appearing or disappearing is a decision somebody
#: takes here rather than a diff somebody misses.
#:
#: ``unpinnedRevisions`` is the key that left (ADR-0027). It warned about a
#: revision declaring no ``contentSha256``; the pin is schema-required now, so
#: for every input that reaches this payload the list was empty -- and a
#: permanently empty published field is a claim that its condition is still
#: reachable. Four tests read that field and were deleted with it, which is what
#: leaves this set as the thing holding the removal.
_PUBLISHED_VALIDATE_KEYS = frozenset(
    {
        "applicationOrder",
        "contentFileCount",
        "migrationCount",
        "stateHash",
        "valid",
    }
)


def test_validate_publishes_exactly_the_recorded_key_set(project: Path) -> None:
    """ADR-0027 decision 1's second break: ``unpinnedRevisions`` is gone.

    Asserted as an equality over the whole payload rather than as the one
    absence, because both directions are the same defect. A re-added
    ``unpinnedRevisions`` republishes a warning that can no longer fire; a *new*
    key arriving unannounced is the shape that reached the wire before -- and a
    key silently *lost* is a caller's ``KeyError`` in the field.
    """
    _invoke("init")
    _write_migration(project)

    code, validated = _invoke("migrate", "validate")

    assert code == 0, f"the fixture project must validate for this to be about keys: {validated}"
    assert "unpinnedRevisions" not in validated, (
        "`migrate validate --json` publishes `unpinnedRevisions` again. The pin is "
        "required by the schema (ADR-0027 decision 1), so an unpinned revision is "
        "refused at load and never reaches this payload: the list is empty for "
        "every input that gets here, and publishing it claims otherwise."
    )
    assert set(validated) == _PUBLISHED_VALIDATE_KEYS, (
        f"`migrate validate --json` publishes {sorted(validated)}, and this file "
        f"records {sorted(_PUBLISHED_VALIDATE_KEYS)}. Adding or removing a key is a "
        f"break in a contract the Claude Code plugin parses; make it here, in the "
        f"same change that updates the CHANGELOG."
    )


def test_the_human_output_carries_no_pin_warning_either(project: Path) -> None:
    """The other channel the deleted ``unpinnedRevisions`` tests covered.

    ``_emit`` renders one payload two ways, so a warning present in ``--json``
    and absent from the default output -- or the reverse -- would be the two
    channels disagreeing about the same project. That is why the removal is
    checked here as well as in the JSON key set, rather than argued from the
    shared payload.

    The positive assertion is not decoration: without it this passes against a
    command that printed nothing at all, which is the failure mode an
    absence-only test cannot see.
    """
    _invoke("init")
    _write_migration(project)

    result = runner.invoke(app, ["migrate", "validate"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "applicationOrder" in result.stdout, "the payload really was rendered here"
    assert "unpinnedRevisions" not in result.stdout, (
        "the default output warns about a revision that pins no body, which the "
        "schema no longer permits to exist (ADR-0027 decision 1)"
    )


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


def test_deleting_an_applied_migration_is_fatal(project: Path) -> None:
    """FR-K5 (#116): removal is the one tampering the checksum trail cannot see.

    The forward checksum check binds only files that still exist, so deleting an
    applied migration -- the strongest tampering -- passed silently while its
    canonical rows stayed in the store (issue #116). The reverse check refuses
    it, symmetric with `test_editing_an_applied_migration_is_fatal` above, and
    names the deleted migration so the operator can restore it (AC-2).

    Checked at ``validate``, ``apply`` *and* ``status``, the three commands that
    resolve a project through ``_verify_history``: an evidence guard that fired
    at one and not the others would let a caller route around it by reaching for
    a different command, the same divergence issue #63 recorded for the scope
    guard.
    """
    _invoke("init")
    _write_migration(project)
    _write_rate_limit_migration(project)
    assert _invoke("migrate", "apply")[0] == 0, "both migrations must apply cleanly first"

    (project / RATE_LIMIT_MIGRATION_PATH).unlink()

    for command in ("validate", "apply", "status"):
        code, error = _invoke("migrate", command)
        assert code == EXIT_STATE_ERROR, (
            f"`migrate {command}` did not refuse a deleted applied migration (#116)"
        )
        assert RATE_LIMIT_MIGRATION_ID in error["error"], (
            f"`migrate {command}` refused without naming the deleted migration (AC-2)"
        )
        assert "never be deleted" in error["error"]
        assert error["remedy"], f"`migrate {command}` named no remedy to restore it"


def test_apply_is_unaffected_when_no_applied_migration_was_deleted(project: Path) -> None:
    """AC-3: the honest path -- adding a new migration on top -- still applies.

    Guards the reverse check against over-refusal: a *pending* migration is
    recorded nowhere yet, so it is present-in-set-but-absent-from-history, the
    opposite direction the delete check must not confuse for tampering. Adding
    one shifts the state hash (ADR-0016) and routes to a fresh database, so both
    migrations replay there -- exit 0, and the new migration among the applied.
    """
    _invoke("init")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0

    _write_rate_limit_migration(project)
    code, applied = _invoke("migrate", "apply")
    assert code == 0, "applying a newly added migration is honest, not tampering"
    assert applied["applied"] == [MIGRATION_ID, RATE_LIMIT_MIGRATION_ID]


# -- issue #130: a body edited after the migration that pinned it was applied --

#: Issue #130's reproduction, and deliberately a *removal*: the body loses the
#: one sentence that made it findable. Removal is the face that matters, because
#: a stale index goes on answering with a line the working tree no longer
#: contains -- disclosure of withdrawn content, not merely a wrong answer.
#: `_write_migration` pins `BODY`, so writing this afterwards is what puts the
#: declared digest and the bytes on disk out of step.
_BODY_WITH_ITS_ONE_CLAIM_REMOVED = "# Authentication policy\n\n"


def test_a_body_edited_after_apply_is_refused_when_apply_is_re_run(project: Path) -> None:
    """Issue #130's reproduction, driven at the layer that refuses it today.

    The 2026-08-10 report: edit a revision's body file in place, re-run plain
    ``migrate apply``, get exit 0 -- and the canonical store, plus every index
    built from it, keeps serving the removed line. What killed that
    reproduction is ``contentSha256`` becoming schema-required on every
    ``upsertRevision`` (ADR-0027 decision 1) and re-verified against the bytes
    on disk each time the loader re-reads a body (``_parse_upsert``): the
    re-apply now refuses instead of silently confirming a state nothing on disk
    supports.

    **No test drove that refusal.** Measured 2026-08-26, the sentence asserted
    below occurred exactly once in the repository -- in the loader that raises
    it -- and nowhere in ``tests/``. The adjacent pin tests all enter through
    ``propose accept`` or ``migrate validate``; none re-applies. A guard that no
    test reaches survives its own deletion, and this one guards the difference
    between "fewer results" and "withdrawn content still served".
    """
    _invoke("init")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0, "the drift must start from a cleanly applied state"

    body = project / ".theurian/knowledge/architecture/auth-policy.md"
    body.write_text(_BODY_WITH_ITS_ONE_CLAIM_REMOVED)

    code, error = _invoke("migrate", "apply")

    assert code == EXIT_STATE_ERROR, "a re-apply over a drifted body must not report success"
    # Both digests by value, not merely "a mismatch was reported": a refusal
    # naming neither side tells an author nothing about which half to correct,
    # and an equality-only check would pass on two identically wrong strings.
    assert body_pin(_BODY_WITH_ITS_ONE_CLAIM_REMOVED)[:12] in error["error"], (
        "the refusal must name the digest of the bytes actually on disk"
    )
    assert body_pin(BODY)[:12] in error["error"], (
        "and the digest the applied migration pinned, which is what it no longer matches"
    )
    assert "The body file changed after the migration was written." in error["error"], (
        "the diagnosis is the operator-facing half: which of the two files moved"
    )
    # The referrer, so the remedy points at a file the author can open. Both
    # halves: the migration that carries the stale pin, and the body it names.
    assert f"{MIGRATION_ID}-add-auth-policy.yaml" in error["error"]
    assert "../knowledge/architecture/auth-policy.md" in error["error"]


def test_a_body_edited_after_apply_is_refused_by_validate_as_well(project: Path) -> None:
    """The same drift, through the command an operator reaches for first.

    ``migrate validate`` is documented as reporting what can be checked without
    touching state, so an author who has been told a re-apply refuses will run
    it to find out why. If it passed while ``apply`` refused, the two commands
    would disagree about whether the project is well-formed -- the divergence
    issue #63 recorded for the scope guard, here on the body pin.

    Pinned against known values rather than against ``apply``'s output: two
    commands agreeing by both falling back to the same wrong text would satisfy
    an equality-only check.
    """
    _invoke("init")
    _write_migration(project)
    assert _invoke("migrate", "apply")[0] == 0, "the drift must start from a cleanly applied state"

    body = project / ".theurian/knowledge/architecture/auth-policy.md"
    body.write_text(_BODY_WITH_ITS_ONE_CLAIM_REMOVED)

    code, error = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR, "validate must not call a drifted project well-formed"
    assert body_pin(_BODY_WITH_ITS_ONE_CLAIM_REMOVED)[:12] in error["error"]
    assert body_pin(BODY)[:12] in error["error"]
    assert "The body file changed after the migration was written." in error["error"]


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
    revised = "# Authentication policy\n\nEvery call carries a signed token, checked twice.\n"
    (project / ".theurian/knowledge/architecture/auth-policy.revised.md").write_text(revised)
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
    contentSha256: {body_pin(revised)}
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


@pytest.mark.skipif(
    not _CAN_MAKE_A_BLOCKING_FILE, reason="needs os.mkfifo and an interruptible timer"
)
def test_validate_refuses_a_fifo_content_file_instead_of_hanging(project: Path) -> None:
    """Issue #215's own reproduction, at the CLI.

    ``read_source_file`` enforced SEC-8 from ``st_size``, and a FIFO reports 0 --
    so the cap passed and ``open()`` blocked for a writer that never came.
    Measured against the real CLI before the fix, in a sandboxed HOME and
    THEURIAN_DATA_DIR: ``migrate validate --json`` produced no output and no exit
    within 15 seconds. That is worse than the Rich traceback issue #205 closed on
    this same seam -- a hang cannot even be graded, because nothing arrives.

    The timer is what turns a regression back into a failing test rather than a
    stalled suite (``hang_guard``); the assertion is that it never fires.

    Exit 1 rather than ``EXIT_STATE_ERROR``, and this seam is *not* uniformly
    graded: no branch of ``_require_project`` names this type or
    ``InputTooLargeError``, so both take its generic ``except TheurianError``
    branch at 1, while a ``PathEscapeError`` or a
    ``MigrationContentUnreadableError`` from the same ``read_source_file`` call
    is named there and exits 4. Pinned rather than argued, because it is a
    published contract: ``EXIT_STATE_ERROR``'s own note records why it is left
    where 0.1.0.dev9 shipped it. What the fix changes is not the grading but the
    payload -- CP-2's ``{error, remedy}`` on stderr, with a clean stdout.
    """
    _invoke("init")
    _write_fifo_content_migration(project)

    with fails_rather_than_hanging(15, waiting_for="migrate validate over a FIFO contentFile"):
        result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == 1
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    _assert_fifo_refusal_payload(json.loads(result.stderr))


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


def _escape_the_theurian_directory(project: Path) -> Path:
    """Deliver ``.theurian`` as a symbolic link that leaves the working tree.

    Models what a clone hands the victim in #237: the layout lives beside the
    tree at ``../shared`` and ``.theurian`` is a committed relative symlink into
    it. The link is relative because a clone carries a relative one; the target
    is genuinely outside the clone's real tree, which is the property the
    assertions turn on.

    Returns the out-of-tree ``shared`` directory the writes would land in.
    """
    shared = project.parent / "shared"
    shutil.move(str(project / ".theurian"), str(shared))
    (project / ".theurian").symlink_to(Path("..") / "shared")
    assert not shared.resolve().is_relative_to(project.resolve()), (
        "the fixture must place the layout genuinely outside the clone's real tree"
    )
    return shared


def _escape_the_state_directory(project: Path) -> Path:
    """Deliver ``.theurian/state`` as a symbolic link that leaves the tree.

    The descendant face of #237: ``.theurian`` stays an honest directory (so the
    root-join check waves it through), and a clone force-adds ``state`` as a
    symlink past the ADR-0004 ignore. The state database, active pointer and any
    read of them would follow it outside the tree. ``state`` is derived and empty
    after ``init``, so replacing it models the committed link without losing
    content.

    Returns the out-of-tree directory the writes would land in.
    """
    shared_state = project.parent / "shared_state"
    shared_state.mkdir(exist_ok=True)
    shutil.rmtree(project / ".theurian" / "state")
    # Relative to the link's own directory, `.theurian/`: two `..` reach the
    # tree's parent, where the out-of-tree target sits.
    (project / ".theurian" / "state").symlink_to(Path("..") / ".." / "shared_state")
    assert not shared_state.resolve().is_relative_to(project.resolve()), (
        "the fixture must place the state directory genuinely outside the clone's real tree"
    )
    return shared_state


def _escaped_state_artefacts(shared: Path) -> list[Path]:
    """Every state or lifecycle artefact that landed outside the tree.

    The three faces #237 writes: the state database (``*.sqlite``), the active
    pointer (``active.json``) and the write lock (``write.lock``). ``init``
    leaves the directories that hold them empty, so any *file* here is a write
    that escaped.
    """
    return sorted(
        path
        for path in shared.rglob("*")
        if path.is_file()
        and (path.suffix == ".sqlite" or path.name in {"active.json", "write.lock"})
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_refuses_an_escaping_theurian_symlink_and_writes_nothing_outside_the_tree(
    project: Path,
) -> None:
    """#237 (HIGH): a committed ``.theurian -> ../shared`` symlink made
    ``migrate apply`` write the state database, active pointer and write lock
    outside the cloned working tree and return 0.

    Reproduced against the real CLI: with the layout relocated to ``../shared``
    and ``.theurian`` a symlink into it, an empty ``migrate apply`` seeded
    ``shared/state/theurian-state-*.sqlite``, ``shared/state/active.json`` and
    ``shared/runtime/write.lock`` -- all outside the clone. The root-join
    containment in ``ProjectPaths.of`` refuses before any helper derives a path,
    so nothing is written and the caller is told how to repair the link.

    The assertion compares against the clone's *real* tree: the writes escape
    through a symlink, so a check that stayed lexical would miss them.
    """
    _invoke("init")
    shared = _escape_the_theurian_directory(project)

    code, payload = _invoke("migrate", "apply")

    # `EXIT_STATE_ERROR`, since #550 made `ProjectPaths.of` raise the same
    # `ProjectPathEscapeError` the containment chokepoint raises. Being upstream
    # of the migration load is why the *refusal* happens here and not there; it
    # was never a reason for a different exit code, and while it was treated as
    # one this command answered 1 for a link at `.theurian` and 4 for the same
    # link one level deeper at `.theurian/state`.
    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == KNOWLEDGE_DIR_ESCAPE_REMEDY
    assert _escaped_state_artefacts(shared) == [], "migrate apply wrote state outside the tree"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_status_over_an_escaping_theurian_symlink_reads_nothing_from_outside_the_tree(
    project: Path,
) -> None:
    """AC-2 (read face): the same root-join fix closes reads, not only writes.

    A real state is built first, then the whole ``.theurian`` -- state included
    -- is relocated outside the tree and replaced by the escaping symlink, the
    shape a clone could carry with a genuine build sitting at the link's target.
    Before the fix, ``migrate status`` followed the link and reported
    ``stateBuilt: true`` read back from outside the clone; every read derives
    from the same ``knowledge_dir`` the write path does, so containing the join
    refuses the read at the same point.

    Deliberately no migration is written: an empty apply still seeds the state,
    and a ``contentFile`` would make the loader refuse the migration read first
    (exit 4), masking the pointer read this pins. With no migration, ``migrate
    status`` reaches ``read_active_state`` and, before the fix, reads the active
    pointer straight back from outside the tree.
    """
    _invoke("init")
    assert _invoke("migrate", "apply")[0] == 0
    _escape_the_theurian_directory(project)

    code, payload = _invoke("migrate", "status")

    # `EXIT_STATE_ERROR` for the same reason the write face above reports it: one
    # root cause, one grade, whichever guard notices it (#550). The escape is
    # still caught resolving the context, before any pointer under the tree is
    # read -- what changed is that being caught early no longer means being
    # graded differently.
    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == KNOWLEDGE_DIR_ESCAPE_REMEDY


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_refuses_an_escaping_state_symlink_and_writes_nothing_outside_the_tree(
    project: Path,
) -> None:
    """#237 descendant face: the root-join check alone did not close this.

    ``.theurian`` is an honest directory, so ``ProjectPaths.of``'s join check
    passes it -- but a clone force-added ``.theurian/state`` as a symlink to
    ``../../shared_state`` past the ADR-0004 ignore. Reproduced against the real
    CLI before the per-target containment: an empty ``migrate apply`` seeded the
    state database and active pointer in ``shared_state``, outside the clone.
    ``ProjectPaths._contained`` refuses the moment ``paths.state`` is derived, so
    nothing escapes.

    The refused path is a leaf *under* ``state``, so the remedy is the
    derived-artifact one rather than ``KNOWLEDGE_DIR_ESCAPE_REMEDY`` (#483 round
    one, H-1): the older text named the operator's knowledge directory for a
    refusal about ``.theurian/state/`` and sent them to ``theurian init``, which
    meets the identical refusal.

    ``EXIT_STATE_ERROR``, and this assertion **changed** with #525: the same
    refusal reported 1 here and 4 through ``database_for`` until the refusals
    ``test_contained_path_envelope.py`` sweeps were graded once. That sweep is
    nine commands; four more were measured by hand and moved with them
    (``init``, ``findings build``, ``propose``, ``propose accept``). No claim is
    made here about a route neither covers. A working tree
    carrying a symbolic link
    force-added past ADR-0004's ignore is a knowledge-state problem the user must
    repair, which is what 4 means; 1 is this CLI's "the command could not run
    here". The move is a breaking change to a published exit code, named as one
    in the changelog, and the whole class it applies to is swept by
    ``test_contained_path_envelope.py``.
    """
    _invoke("init")
    shared_state = _escape_the_state_directory(project)

    code, payload = _invoke("migrate", "apply")

    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == derived_escape_remedy(".theurian", "state")
    assert _escaped_state_artefacts(shared_state) == [], (
        "migrate apply wrote state outside the tree"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_status_over_an_escaping_state_symlink_reads_nothing_from_outside_the_tree(
    project: Path,
) -> None:
    """The read face of the descendant escape.

    A real state is built, then ``.theurian/state`` is replaced by a symlink to a
    directory outside the tree holding that state. Before the per-target
    containment, ``migrate status`` followed the link and read ``stateBuilt:
    true`` back from outside the clone; ``_contained`` refuses when ``paths.state``
    is derived, so the read never leaves the tree.

    Graded ``EXIT_STATE_ERROR`` since #525, for the reason its write-face sibling
    above records: one root cause and one exit code across the swept nine and the
    four measured beside them, whichever helper noticed it.
    """
    _invoke("init")
    assert _invoke("migrate", "apply")[0] == 0
    # Move the built state out of the tree, then point the symlink at it -- the
    # shape a clone could carry with a genuine build sitting at the link's target.
    shared_state = project.parent / "shared_state"
    shutil.move(str(project / ".theurian" / "state"), str(shared_state))
    (project / ".theurian" / "state").symlink_to(Path("..") / ".." / "shared_state")

    code, payload = _invoke("migrate", "status")

    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == derived_escape_remedy(".theurian", "state")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_init_refuses_an_escaping_knowledge_symlink_and_creates_nothing_outside(
    project: Path,
) -> None:
    """H-1 (code-review + security HIGH): `init` bypassed the containment chokepoint.

    `initialize_project` builds `paths.knowledge_dir / relative` directly and
    mkdir's it, so a clone that tracks `.theurian/knowledge` as a symlink to
    outside the tree -- the same authored-symlink class as `.theurian` itself
    (#237, T-5) -- made `init` create the knowledge subtree at the link's target,
    exit 0, `createdPaths` reporting them as if in-tree. Reproduced against the
    real CLI: `outside/{architecture,domain,operations,security,testing}`.

    The gap the existing init tests missed: they all init an honest tree *then*
    introduce the symlink. This one has the escaping symlink present AT init time,
    which is what a clone delivers. `init` must refuse before the first mkdir and
    create nothing outside; every write target now routes through `_contain`.

    The code **changed** with #525's second round: this reported 1 while the
    identical condition under a swept command reported 4, and `init` is outside
    `CLI_SWEEP` (it writes `.theurian/` and appends to `.gitignore` in the working
    directory), so the sweep could not see the disagreement -- a reviewer did.
    `initialize_project` reaches the containment chokepoint directly, once per
    directory it creates, which is the population the sweep's key was widened to
    include.
    """
    (project / ".theurian").mkdir()
    outside = project.parent / "outside-knowledge"
    outside.mkdir()
    (project / ".theurian" / "knowledge").symlink_to(outside)

    code, payload = _invoke("init")

    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == KNOWLEDGE_DIR_ESCAPE_REMEDY
    assert list(outside.iterdir()) == [], "init created directories outside the tree"


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


# -- issue #233: the CLI face of a refusal that described itself wrongly ------
#
# What the refusal used to say, why each part of it was wrong, and what the
# `EscapeRole` split now guarantees are all recorded once, on
# `PathEscapeError`'s own docstring (`domain/errors.py`). These tests pin the
# exit code and the payload a caller actually receives; they do not restate it.
#
# The refusal itself is unchanged -- T-5 containment held before and after.

#: The whole of what `lstat` proves, followed by the checklist every escape
#: remedy converges on. No file is named for deletion: "that link" is whichever
#: one the reader locates by walking the chain (`EscapeSite`, `domain/errors.py`).
_SYMLINK_REMEDY_TAIL = (
    "is itself a symbolic link. Check it, each directory above it, and each link it "
    "resolves through, for the link that leaves the project. Repoint that link so it "
    "resolves inside the project, or remove that link, then retry."
)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_names_the_symlink_when_the_migrations_directory_escapes_the_project(
    project: Path,
) -> None:
    """Shape one of issue #233: `.theurian/migrations` is a symlink to a
    directory outside the project root.

    The outside directory is deliberately empty, matching the unit-level
    fixture at
    `tests/unit/test_migration_loader_errors.py::test_load_migrations_refuses_a_migrations_directory_symlink_to_an_empty_outside_directory`:
    with no `*.yaml` entry to enumerate, `_load_one`'s incidental
    `read_source_file` escape check never runs, so the refusal comes from
    `_refuse_unusable_migrations_directory_symlink`'s own check and nothing
    else.
    """
    _invoke("init")
    migrations_dir = project / ".theurian" / "migrations"
    outside = project.parent / "outside-migrations"
    outside.mkdir()
    shutil.rmtree(migrations_dir)
    migrations_dir.symlink_to(outside)

    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR, (
        "a containment refusal is knowledge state; the load path's SEC-8 input caps are "
        "not, and exit 1 (see EXIT_STATE_ERROR's own note)"
    )
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    relative = str(migrations_dir.relative_to(project))
    assert payload["error"] == f"{relative!r} escapes the permitted root"
    assert payload["remedy"] == f"{relative!r} {_SYMLINK_REMEDY_TAIL}"
    assert str(project) not in payload["error"], "the absolute root is not the user's business"
    assert "valid" not in payload, "containment still refuses; this is not a passing validate"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_names_the_symlink_when_one_migration_file_escapes_the_project(
    project: Path,
) -> None:
    """Shape two of issue #233: one `*.yaml` entry inside a healthy
    `.theurian/migrations` is a symlink to a file outside the project root.

    A different call site from the shape above -- `_load_one`'s
    `read_source_file`, one level down, reached only once an entry exists to
    read -- and the same three defects, so the fix has to cover both or it has
    only moved the false remedy one call site over. The other migration on
    disk is real and valid: the refusal must be about the escaping entry, not
    about an empty set.
    """
    _invoke("init")
    _write_migration(project)
    outside_body = project.parent / "id_ed25519"
    outside_body.write_text("PRIVATE KEY\n")
    escape_entry = project / ".theurian/migrations/01K1EVAAAA01234567890ABCDE-escape.yaml"
    escape_entry.symlink_to(outside_body)

    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR, (
        "a containment refusal is knowledge state; the load path's SEC-8 input caps are "
        "not, and exit 1 (see EXIT_STATE_ERROR's own note)"
    )
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    payload = json.loads(result.stderr)
    relative = str(escape_entry.relative_to(project))
    assert payload["error"] == f"{relative!r} escapes the permitted root"
    assert payload["remedy"] == f"{relative!r} {_SYMLINK_REMEDY_TAIL}"
    assert str(project) not in payload["error"], "the absolute root is not the user's business"
    assert "PRIVATE KEY" not in result.stderr, "the refusal must not hand over what it refused"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_apply_refuses_an_escaping_migration_file_without_seeding_a_state_database(
    project: Path,
) -> None:
    """The worse face of the shape above: `migrate apply` must refuse before
    any state exists, the same pin every other member of this load path's
    refusal family already carries. Issue #233 changes only what the refusal
    *says*, so this is the test that goes red if fixing the wording were to
    move the refusal itself.
    """
    _invoke("init")
    _write_migration(project)
    outside_body = project.parent / "id_ed25519"
    outside_body.write_text("PRIVATE KEY\n")
    escape_entry = project / ".theurian/migrations/01K1EVAAAA01234567890ABCDE-escape.yaml"
    escape_entry.symlink_to(outside_body)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    state_dir = project / ".theurian" / "state"
    assert not state_dir.exists() or not any(state_dir.iterdir())


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_does_not_tell_a_user_to_delete_a_migration_an_ancestor_symlink_broke(
    project: Path,
) -> None:
    """The ancestor/plain-file face, through the CLI a user actually reads.

    `.theurian` itself is the outside-pointing symlink -- reachable from a
    plain `git clone`, issue #237 -- and the migration inside it is an
    ordinary file. Measured against this branch before the `EscapeRole` split:
    the payload told the user that their own authored migration "is a symbolic
    link ... or remove it", and following that instruction emptied
    `migrations/`, after which `migrate validate` exited 0 with `.theurian`
    still outside the project.

    Since #237's root-join containment, the refusal arrives *earlier* than the
    migration loader: `ProjectPaths.of` proves `.theurian` resolves inside the
    tree while resolving the command context, so `migrate validate` never
    reaches the loader's `EscapeRole` remedy for this shape. That earlier
    refusal names the `.theurian` link rather than any migration, so the harm
    this test guards against -- routing a user into deleting their own work --
    is closed at the root instead of avoided in the wording. The loader's
    `EscapeRole` remedy is still pinned where it is still reached: at the unit
    level in `tests/unit/test_migration_loader_errors.py`, and for a *descendant*
    symlink the root join does not resolve.

    What stays pinned here is that the earlier refusal is non-destructive even
    with a real migration sitting behind the link: the entry is untouched. Its
    grade is ``EXIT_STATE_ERROR`` since #550, which is also what the loader's own
    ``PathEscapeError`` earns -- so a caller reading the code cannot tell the two
    guards apart, and does not have to.
    """
    _invoke("init")
    _write_migration(project)
    outside_theurian = project.parent / "outside-theurian"
    shutil.move(str(project / ".theurian"), str(outside_theurian))
    (project / ".theurian").symlink_to(outside_theurian)
    entry = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    assert not entry.is_symlink(), "fixture must drive a PLAIN file, not a link"

    code, payload = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == KNOWLEDGE_DIR_ESCAPE_REMEDY
    assert "is a symbolic link" not in payload["remedy"]
    assert "remove it" not in payload["remedy"], "that instruction destroys the user's work"
    assert entry.exists(), "the refusal must not have removed anything itself"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_does_not_tell_a_user_to_delete_a_symlink_that_is_not_the_escape(
    project: Path,
) -> None:
    """The same harm as the test above, reached through a symlink rather than a
    plain file -- the construction that survived keying the strong role on
    `is_symlink()` alone.

    The entry is a symbolic link, so the `lstat` the old loader remedy trusted is
    earned; it points at a sibling *inside its own directory*, and `.theurian` is
    what escapes. Since #237's root-join containment, `migrate validate` refuses
    while resolving the command context -- before the loader inspects the entry
    at all -- so the entry's link-ness never selects a remedy. What this pins is
    the outcome that made the old wording a defect rather than a nit: the refusal
    removes nothing, so the migration is still there to be repaired once the
    `.theurian` link is fixed. It reports ``EXIT_STATE_ERROR`` since #550, the
    grade every other face of a doctored clone already carried.
    """
    _invoke("init")
    _write_migration(project)
    shared = project / ".theurian" / "shared"
    shared.mkdir()
    entry = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    shutil.move(str(entry), str(shared / "real.yaml"))
    entry.symlink_to(Path("..") / "shared" / "real.yaml")
    outside_theurian = project.parent / "outside-theurian"
    shutil.move(str(project / ".theurian"), str(outside_theurian))
    (project / ".theurian").symlink_to(outside_theurian)
    assert entry.is_symlink(), "fixture must earn the lstat the old rule trusted"

    code, payload = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    assert payload["remedy"] == KNOWLEDGE_DIR_ESCAPE_REMEDY
    assert "remove it" not in payload["remedy"], "that instruction destroys the user's work"
    assert entry.is_symlink(), "the entry is still there to be repaired"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_names_the_migration_file_when_its_content_file_escapes(project: Path) -> None:
    """Through the CLI: a `contentFile` escaping names
    the migration file carrying it, and still never the author-written value.

    `MigrationContentUnreadableError` already prints this same project-relative
    filename for a `contentFile` that merely fails to read, so naming it here
    discloses nothing new -- which is what made the earlier "the only name it
    could give is the author-written value" false.
    """
    _invoke("init")
    escaping = f".theurian/migrations/{MIGRATION_ID}-escape.yaml"
    (project / escaping).write_text(
        MIGRATION.replace(
            "contentFile: ../knowledge/architecture/auth-policy.md",
            "contentFile: ../../../../../../etc/id_ed25519",
        )
    )

    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    payload = json.loads(result.stderr)
    assert payload["error"] == f"{escaping!r} names a path that escapes the permitted root"
    assert escaping in payload["remedy"], "the migration file is the place to open"
    assert "id_ed25519" not in result.stderr, "the author-written value stays unechoed"
    assert "remove it" not in payload["remedy"], "the migration file is not the culprit"


#: Six spellings of ONE traversal: every entry leaves the project through a
#: symlink and returns to the same body, and every one is `contentFile`-legal
#: under the published schema. They exist as a table because a single spelling
#: is what the first fix was written against, and round 1 measured five of the
#: six still reaching `migrate validate` exit 0 against it -- the guard was
#: keyed on where a resolution landed rather than on the route it took. `..`-out
#: needs no link outside the project at all, and `folded` hides the whole
#: traversal inside one component's link chain, so neither is reachable by
#: inspecting intermediate components.
_IN_AND_OUT_SPELLINGS = [
    pytest.param("../knowledge/hop/back/auth-policy.md", id="two-components"),
    pytest.param("../knowledge/hop/./back/auth-policy.md", id="a-dot-between-them"),
    pytest.param("../knowledge/folded/auth-policy.md", id="folded-into-one-component"),
    pytest.param("../knowledge/deep/auth-policy.md", id="out-through-a-nested-directory"),
    pytest.param("shortcut/auth-policy.md", id="a-link-inside-migrations"),
    pytest.param("../../../outside-knowledge/back/auth-policy.md", id="dotdot-out-one-link-back"),
]


def _plant_the_in_and_out_links(project: Path) -> Path:
    """Every link the spellings above travel through, and the body they reach."""
    knowledge = project / ".theurian" / "knowledge"
    architecture = knowledge / "architecture"
    architecture.mkdir(parents=True, exist_ok=True)
    (architecture / "auth-policy.md").write_text(BODY)

    outside = project.parent / "outside-knowledge"
    (outside / "nested").mkdir(parents=True)
    (outside / "back").symlink_to(architecture, target_is_directory=True)
    (outside / "nested" / "home").symlink_to(architecture, target_is_directory=True)

    (knowledge / "hop").symlink_to(outside, target_is_directory=True)
    (knowledge / "folded").symlink_to(outside / "back", target_is_directory=True)
    (knowledge / "deep").symlink_to(outside / "nested" / "home", target_is_directory=True)
    (project / ".theurian" / "migrations" / "shortcut").symlink_to(
        outside / "back", target_is_directory=True
    )
    return architecture / "auth-policy.md"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("content_file", _IN_AND_OUT_SPELLINGS)
def test_validate_refuses_a_content_file_that_leaves_the_project_and_comes_back(
    project: Path, content_file: str
) -> None:
    """Issue #288 and round 1's R1-A, through the CLI: the route, not the endpoint.

    Each spelling *resolves* to the very body the sibling test above writes --
    the assertion below says so -- so the pin matches, containment on the
    resolved path holds, and every check keyed on the destination is satisfied.
    Measured at ``0a52479``, at ``33ee7ae1`` and again at ``dcd11dcd``: ``exit
    0``, ``migrationCount: 1``, ``valid: true`` for all but the first two.

    The refusal names the migration file and never the author's ``contentFile``,
    which is the same division of labour the sibling test pins; the remedy is
    the referrer form, so it offers the traversed-link cause rather than
    asserting the path text is wrong.
    """
    _invoke("init")
    body = _plant_the_in_and_out_links(project)

    escaping = f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    (project / escaping).write_text(
        MIGRATION.replace(
            "contentFile: ../knowledge/architecture/auth-policy.md",
            f"contentFile: {content_file}",
        )
    )
    assert (project / ".theurian" / "migrations" / content_file).resolve() == body.resolve(), (
        "the fixture must resolve back inside, or it tests the plain escape"
    )

    result = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == "", "stdout stays a clean machine channel on failure"
    assert "Traceback" not in result.stderr, "a refusal is graded, never a raw escape"
    payload = json.loads(result.stderr)
    # Every spelling here reaches the body through a link whose absolute target
    # names a location this project does not answer to, so the refusal says that
    # rather than "escapes the permitted root" -- which would be false for the
    # five of six whose destination is squarely inside the project (#233's
    # family, round 2). The `..`-out spelling is the one genuine escape, and it
    # keeps the escape wording.
    expected = (
        f"{escaping!r} names a path reached through a link pointing outside the project"
        if content_file != "../../../outside-knowledge/back/auth-policy.md"
        else f"{escaping!r} names a path that escapes the permitted root"
    )
    assert payload["error"] == expected
    assert escaping in payload["remedy"], "the migration file is the place to open"
    assert content_file not in result.stderr, "the author-written value stays unechoed"
    assert str(project) not in result.stderr, "the absolute root is not the user's business"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_validate_still_accepts_a_content_file_reached_through_an_in_project_link(
    project: Path,
) -> None:
    """The narrowness control for the table above, at the same call site.

    Without it, every spelling above is equally satisfied by refusing each
    ``contentFile`` that touches a symlink at all -- which would be a ban on
    links rather than the containment rule the guard documents, and would break
    a repository that organises ``knowledge/`` with in-project links.
    """
    _invoke("init")
    knowledge = project / ".theurian" / "knowledge"
    (knowledge / "architecture").mkdir(parents=True, exist_ok=True)
    (knowledge / "architecture" / "auth-policy.md").write_text(BODY)
    (knowledge / "by-topic").symlink_to(knowledge / "architecture", target_is_directory=True)

    (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(
        MIGRATION.replace(
            "contentFile: ../knowledge/architecture/auth-policy.md",
            "contentFile: ../knowledge/by-topic/auth-policy.md",
        )
    )

    code, payload = _invoke("migrate", "validate")

    assert code == 0, payload
    assert payload["valid"] is True
    assert payload["migrationCount"] == 1


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


def _without_the_scope_guard(seed: pytest.MonkeyPatch) -> None:
    """Let a foreign tenant into the state database, to seed an *applied* one.

    Three tests below need a migration the scope guard refuses to already be
    recorded as applied -- a state no shipped command will produce -- so the
    guard is neutralised for the seeding `migrate apply` alone, inside a
    ``monkeypatch.context()`` that puts it back.

    **One patch, where this needed two.** ``cli/commands.py`` used to hold its
    own binding of ``refuse_unenforceable_scope`` and call it directly, so the
    engine's copy and the CLI's copy both had to be replaced or the seeding apply
    was refused. Both commands now reach the guard through
    ``run_static_migration_guards``, which resolves it in ``migration_engine``'s
    own globals: one binding remains, and the second ``setattr`` becoming
    unnecessary is precisely the divergence the shared guard set removes.
    """
    seed.setattr(
        "theurian.application.migration_engine.refuse_unenforceable_scope", lambda _ms: None
    )


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


def test_a_fourth_guard_error_reaches_the_json_contract_not_a_traceback(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guard error the translator has no branch for is still a document, not a crash.

    ``_refuse_a_set_a_static_guard_rejects`` names the three guard errors that
    exist. A fourth whole-set guard added to ``run_static_migration_guards`` would
    raise its own ``MigrationError`` subclass, and without a terminal branch that
    escapes Typer as a Rich traceback -- exit 1, empty stdout, no ``{error,
    remedy}`` even under ``--json`` (the CP-2 shape every named branch avoids).
    Simulated by making the shared guard set raise a synthetic subclass; the
    terminal branch turns it into the same document every named refusal produces,
    with the generic remedy since the synthetic error carries none of its own.
    """
    _invoke("init")
    _write_migration(project)

    class _FourthGuardError(MigrationError):
        """A guard error type the translator has never heard of."""

    def _raise(_migration_set: object) -> None:
        raise _FourthGuardError("a whole-set guard the translator does not name refused")

    monkeypatch.setattr("theurian.cli.commands.run_static_migration_guards", _raise)

    code, payload = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    assert "a whole-set guard the translator does not name refused" in payload["error"]
    assert payload["remedy"] == (
        "Fix the migration set the guard refused, then retry. `theurian migrate validate` "
        "reports what can be checked without touching state."
    )


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
    not only this test's seeding one -- it was caught here reverting the working
    directory back to wherever pytest was invoked from mid-test, which is
    exactly the real checkout the isolation rules in this repository exist to
    keep the CLI away from. `migrate validate` never writes, so nothing was
    written by the mistake, but the harness itself must not depend on that.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    with monkeypatch.context() as seed:
        _without_the_scope_guard(seed)
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
_SECOND_POLICY_BODY = "# Second\n"
_THIRD_POLICY_BODY = "# Third\n"
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
    contentSha256: {body_pin(_SECOND_POLICY_BODY)}
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
        _without_the_scope_guard(seed)
        seed_code, seeded = _invoke("migrate", "apply")
    assert seed_code == 0, "fixture setup failed: the seeding apply itself was refused"
    assert seeded["applied"] == [MIGRATION_ID]

    # A clean, pending migration -- this shifts the state hash (ADR-0016), so
    # `database_for(context.state_hash)` no longer names the database the
    # seeding apply above just built.
    (project / ".theurian/knowledge/architecture/second-policy.md").write_text(_SECOND_POLICY_BODY)
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
    contentSha256: {body_pin(_THIRD_POLICY_BODY)}
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
    (project / ".theurian/knowledge/architecture/second-policy.md").write_text(_SECOND_POLICY_BODY)
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
        _without_the_scope_guard(seed)
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
    (project / ".theurian/knowledge/architecture/third-policy.md").write_text(_THIRD_POLICY_BODY)
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


def _second_migration(content_file: str = _SHARED_CONTENT_FILE, body: str = BODY) -> str:
    """A well-formed update to the item the first migration created.

    Everything about it is correct except that its ``contentFile`` is the body
    the first migration already references: the ``expectedRevision`` chain is
    right, the ids are unique, and the pin agrees with the bytes on disk. Issue
    #210 measured this shape applying cleanly and recording the *second* body
    under the *first* revision's title and author, which is what the
    body-sharing refusal below stops -- and it stops it whether or not the two
    operations pin, because the hazard is one file standing for two revisions.
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
    contentSha256: {body_pin(body)}
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


def _write_second_migration(
    root: Path, content_file: str = _SHARED_CONTENT_FILE, body: str = BODY
) -> None:
    (root / f".theurian/migrations/{_SECOND_MIGRATION_ID}-revise.yaml").write_text(
        _second_migration(content_file, body)
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
    revised = "# Authentication policy, revised\n\nEvery call carries a signed token.\n"
    (project / ".theurian/knowledge/architecture/auth-policy.revised.md").write_text(revised)
    _write_second_migration(
        project, content_file="../knowledge/architecture/auth-policy.revised.md", body=revised
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
    contentSha256: {body_pin(BODY)}
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
    contentSha256: {body_pin(BODY)}
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
    contentSha256: {body_pin(BODY)}
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
