"""`migrate apply`'s single critical section, pinned deterministically (#478).

CI's required checks run `pytest -m "not e2e"` (`core.yml`), so
`tests/e2e/test_migrate_apply_concurrency.py` -- which drives two real OS
processes racing a 50ms poll window -- gates nothing on a pull request
(#485). This file is the compensating control: every test here is
`integration`-marked, runs in-process through `typer.testing.CliRunner`
(the same pattern `test_cli_commands.py` uses), and is deterministic --
no timing window, no race to win. It is what CI actually runs to hold
#468's fix in place; the e2e test is the readable end-to-end proof that a
real raced pair, over real OS processes, produces the same outcome.

**How a test here proves a write only happens under the lock: probe at the
write's own call site.** A non-blocking `flock` attempt from a fresh file
descriptor, at the instant `create_database` or `write_active_state` is
called, tells you whether *anything* currently holds the production lock --
independent of whether the command as a whole appears to block. That
independence is what makes the probe correct where "hold the lock
externally before calling the command, then assert nothing happened" is
not: #468's single critical section means the *first* write's own,
still-correct lock already blocks entry before a mutation that unlocked
only the *second* write would ever become observable that way -- either
mutation can hide behind the other write's still-correct acquisition. This
module was written test-first with exactly that external-hold shape, and
it was replaced by the probe once mutation testing confirmed the blind
spot (see the module's own git history for the discarded version, and
`test_connection_claims.py::test_the_only_test_that_constructs_the_write_lock_runs_in_one_process`
for the second, independent reason: a file that also needs `subprocess.run`
for git fixture setup -- unrelated to the lock, but present in its text --
does not belong in that module's narrowly-scoped lock-class population,
which is deliberately stricter than the wider one and refuses even a
provably-sequential `subprocess.run`).
"""

from __future__ import annotations

import fcntl
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths
from theurian.application.project_service import write_active_state as real_write_active_state
from theurian.cli.main import app
from theurian.domain.ports import Clock
from theurian.domain.state import ActiveState, StateHash
from theurian.infrastructure.sqlite.connection import create_database as real_create_database

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_STATE_ERROR = 4

MIGRATION_ID = "01K1EEEEEE01234567890ABCDE"
REVISION_ID = "01K1EEEREV01234567890ABCDE"
BODY = "# Lock confinement\n\nEvery write this command makes sits under one hold.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-09-01T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.lock-confinement
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.lock-confinement
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/lock-confinement.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Lock confinement
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/lock-confinement.md
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
    result = runner.invoke(app, ["init", "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stderr
    yield root


def _write_migration(root: Path) -> None:
    (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
    (root / ".theurian/knowledge/architecture/lock-confinement.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-lock-confinement.yaml").write_text(MIGRATION)


def _lock_is_held(lock_path: Path) -> bool:
    """Whether *something else* currently holds ``lock_path``.

    A non-blocking `flock` attempt from a fresh file descriptor: it raises
    `OSError` (`EWOULDBLOCK`) exactly when another open file description
    already holds an exclusive lock on the same file -- the same primitive
    the production lock's own acquire-retry loop uses to check without
    blocking.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def test_create_database_and_write_active_state_each_run_only_while_the_lock_is_held(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#468: each write is checked at its own call site, not inferred from blocking.

    Wraps `create_database` and `write_active_state` -- the two names
    `commands.py` imports and calls -- with a probe that checks
    :func:`_lock_is_held` at the instant each is invoked, then lets the real
    function run. Kills a mutation that unlocks *either* write independently
    (m1: `create_database`; m2: `write_active_state`), because each
    assertion below depends only on what is true at its own moment, not on
    how far the command got: under #468's single critical section, if only
    one write's own lock acquisition were removed, the *other* write's
    still-correct acquisition would make the command block or proceed the
    same way regardless, which is exactly the blind spot a test that only
    observes the command from outside cannot see past.
    """
    _write_migration(project)
    paths = ProjectPaths.of(project)
    held_at: dict[str, bool] = {}

    def probed_create_database(database_path: Path, state_hash: str, engine_version: int) -> None:
        held_at["create_database"] = _lock_is_held(paths.write_lock)
        real_create_database(database_path, state_hash, engine_version)

    def probed_write_active_state(
        paths_arg: ProjectPaths, state_hash: StateHash, migration_count: int, clock: Clock
    ) -> ActiveState:
        held_at["write_active_state"] = _lock_is_held(paths.write_lock)
        return real_write_active_state(paths_arg, state_hash, migration_count, clock)

    monkeypatch.setattr("theurian.cli.commands.create_database", probed_create_database)
    monkeypatch.setattr("theurian.cli.commands.write_active_state", probed_write_active_state)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == 0, result.stderr
    assert held_at.get("create_database") is True
    assert held_at.get("write_active_state") is True


def test_a_directory_at_the_database_path_fails_cleanly_and_cleans_up(project: Path) -> None:
    """#468 section A backstop: `(OSError, sqlite3.Error)` around the discard/create decision.

    A directory sitting at the exact path a fresh apply would use for its
    database makes `database.exists()` true, so the code takes the
    discard-untrusted-state branch first -- `Path.unlink()` on a directory
    raises `IsADirectoryError` (an `OSError`), not `create_database`'s own
    `sqlite3.connect` (which is never reached). Both are inside the same
    `(OSError, sqlite3.Error)` catch, so the observable contract is
    identical either way: exit 4, empty stdout, a clean `{error, remedy}`
    envelope on stderr, never a raw traceback. The best-effort cleanup
    cannot remove a directory either (the same `unlink` refusal), which is
    exactly why that cleanup is wrapped in its own `contextlib.suppress` --
    a cleanup failure must not replace the original, more informative
    error.
    """
    _write_migration(project)
    paths = ProjectPaths.of(project)

    validated = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)
    assert validated.exit_code == 0, validated.stderr
    state_hash = str(json.loads(validated.stdout)["stateHash"])
    database_path = paths.state / f"theurian-state-{state_hash[:12]}.sqlite"
    database_path.mkdir(parents=True)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == ""
    error_payload = json.loads(result.stderr)
    assert "error" in error_payload
    assert "remedy" in error_payload
    assert database_path.is_dir()  # unremovable directory: cleanup could not touch it
    assert not paths.active_pointer.exists()


def test_a_directory_at_the_active_pointer_temp_path_fails_cleanly(project: Path) -> None:
    """#468 section B backstop: `OSError` around `record_state`/`write_active_state`.

    `write_active_state` writes to `active.json.tmp` before `os.replace`-ing
    it onto `active.json`; a directory already sitting at that temp path
    makes `Path.write_text` raise `IsADirectoryError`, after
    `create_database` and the migration transaction have already succeeded
    -- the shape section B's own comment names as the reason `sqlite3.Error`
    is not in its catch (neither `record_state` nor `write_active_state`
    opens a database connection).
    """
    _write_migration(project)
    paths = ProjectPaths.of(project)
    temporary = paths.active_pointer.with_suffix(".json.tmp")
    temporary.mkdir(parents=True)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == ""
    error_payload = json.loads(result.stderr)
    assert "error" in error_payload
    assert "remedy" in error_payload
    assert not paths.active_pointer.exists()
