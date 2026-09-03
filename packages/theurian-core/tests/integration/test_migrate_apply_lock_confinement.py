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

from theurian.application.project_service import BuildProvenance, ProjectPaths
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


def test_provenance_is_recorded_before_the_pointer_publishes(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#478 round two (adversarial MEDIUM-1): the confinement test above pins
    that `record_state` and `write_active_state` each run *under* the lock,
    but not their *order* relative to each other -- reversing them (both
    still inside the lock) survives the whole suite, e2e included, because
    nothing checks which one ran first. The order matters on its own: a
    single-process crash between the two writes, in the swapped order, would
    leave `active.json` naming a state hash whose provenance is absent -- the
    serve-side provenance gate then refuses it (fail-closed, not a
    disclosure), but it is a real regression from what moving `record_state`
    ahead of the publish is supposed to buy (this command's own docstring).

    Probes at `write_active_state`'s own call site: by the instant it runs,
    `provenance.has_state` for the exact state hash about to be published
    must already be True. RED under a mutation that swaps the two calls.
    """
    _write_migration(project)
    observed: dict[str, bool] = {}

    def probed_write_active_state(
        paths_arg: ProjectPaths, state_hash: StateHash, migration_count: int, clock: Clock
    ) -> ActiveState:
        provenance = BuildProvenance.default()
        observed["has_state_before_publish"] = provenance.has_state(paths_arg.root, str(state_hash))
        return real_write_active_state(paths_arg, state_hash, migration_count, clock)

    monkeypatch.setattr("theurian.cli.commands.write_active_state", probed_write_active_state)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == 0, result.stderr
    assert observed.get("has_state_before_publish") is True


def test_a_failed_create_database_leaves_no_partial_file_behind(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#478 round two (adversarial MEDIUM-2): the directory-at-the-database-path
    backstop test below never exercises real cleanup -- `unlink` refuses a
    directory too, so `contextlib.suppress(OSError)` swallowing *that*
    failure reads identically to a no-op cleanup that swallows everything.
    This forces a real failure inside `create_database`'s own DDL execution
    -- `sqlite3.connect` has already created a real, non-directory file on
    disk by the time `executescript` fails on invalid SQL -- so the file
    `_unlink_database_and_sidecars` has to remove is one it actually *can*
    remove, proving the cleanup call does something rather than merely that
    it does not itself crash.
    """
    _write_migration(project)
    paths = ProjectPaths.of(project)

    validated = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)
    assert validated.exit_code == 0, validated.stderr
    state_hash = str(json.loads(validated.stdout)["stateHash"])
    database_path = paths.state / f"theurian-state-{state_hash[:12]}.sqlite"

    monkeypatch.setattr(
        "theurian.infrastructure.sqlite.connection.DDL", "THIS IS NOT VALID SQL AT ALL;"
    )

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == ""
    error_payload = json.loads(result.stderr)
    assert "error" in error_payload
    assert "remedy" in error_payload
    assert not database_path.exists(), "the real partial file must be gone after cleanup"
    assert not database_path.with_name(f"{database_path.name}-wal").exists()
    assert not database_path.with_name(f"{database_path.name}-shm").exists()
    assert not paths.active_pointer.exists()


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


#: What the victim file holds before the lock is taken, and after.
#:
#: Content rather than an empty file, because the damage is a truncation: a
#: guard that refused the link *and* an implementation that never opened
#: anything are indistinguishable over a file that was empty to begin with.
LOCK_LINK_VICTIM_BODY = "# Runbook\n\nRotate the signing key every 90 days.\n"


def _lock_path(root: Path) -> Path:
    """Where the advisory lock file lives, from the project root (ADR-0002)."""
    return root / ".theurian/runtime/write.lock"


def test_a_lock_file_symlinked_onto_a_file_in_the_tree_never_truncates_that_file(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #481. Taking the lock must not become a destructive write somewhere else.

    RED before the fix, GREEN after. ADR-0002 chooses a lock *file* over a PID
    file because the kernel releases an advisory lock however the holder exits.
    The file is a synchronisation artefact and nothing else: its bytes are never
    read, and no caller asks for it to be emptied. Acquiring it nevertheless
    opened it in a mode that follows a symbolic link and truncates its target --
    so a link at the lock path turned every writer's first act into an `O_TRUNC`
    on whatever the link named, and the victim file here went to zero bytes at
    exit 0. All three production sites reach that one open (`migrate apply`'s
    critical section, `findings build`, and `write_transaction`'s own
    acquisition), so the command chosen here is a consumer, not the surface.

    **The link is in-tree, and that is the whole finding.** `ProjectPaths.
    write_lock` routes through `_contain`, which resolves the path and refuses it
    when it lands outside the project root -- measured: a lock symlinked to a
    file outside the tree already exited 4 with a clean envelope and left its
    target intact, before any of this. A link whose target is *inside* the root
    resolves inside it and passed containment untouched.

    **How it gets there** is the ADR-0004 delivery `BuildProvenance`'s docstring
    records for the state database: `.theurian/runtime/` is derived and
    git-ignored, and a repository contributor can force-add past the ignore, so a
    clone carries the link and the victim's first write truncates a file in their
    own working tree. Nothing warned them -- the command exited 0 and reported
    success.

    **The assertions are observables, not the mechanism.** Which exception
    subtype the refusal raises, and whether it decides by `O_NOFOLLOW` or by a
    pre-check, is the implementation's business; what is pinned is that the
    target keeps its bytes, that the command refuses rather than reporting
    success, and that the refusal reaches a `--json` caller as one parseable
    envelope on stderr rather than as a traceback.

    **The remedy has to name the file to remove, and that is not decoration.**
    Round one's adversarial mutations replaced `WriteLockUnusableError.remedy`
    with "Something went wrong.", and separately dropped `_state_remedy`'s
    `exc.remedy or ...` so a self-describing subtype fell through to the generic
    migration-set advice -- both survived the whole suite, because every check on
    the field asked whether it was non-empty. The generic fallback is worse than
    empty here: it *does* name a runnable command (`theurian migrate validate`),
    so even the file's own `names_a_remedy` standard passes it while sending the
    operator to look at their migration set over a symlink in
    `.theurian/runtime/`. What kills both is asking that the remedy name the
    thing to act on.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_migration(project)
    victim = project / "runbook.md"
    victim.write_text(LOCK_LINK_VICTIM_BODY)
    before = victim.read_bytes()

    lock = _lock_path(project)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.unlink(missing_ok=True)
    lock.symlink_to(Path("../../runbook.md"))

    result = runner.invoke(app, ["migrate", "apply", "--json"])

    assert victim.read_bytes() == before, (
        f"taking the write lock truncated {victim.name}, a file in the working "
        f"tree that has nothing to do with locking: {before!r} -> "
        f"{victim.read_bytes()!r}"
    )
    escaped = None if isinstance(result.exception, SystemExit) else result.exception
    assert escaped is None, (
        f"the refusal escaped `--json` as a traceback rather than an envelope: {escaped!r}"
    )
    assert result.exit_code != 0, (
        "the command reported success over a lock file it could not honestly "
        "take, so nothing tells the operator their tree was written through"
    )
    assert result.stdout == ""
    error_payload = json.loads(result.stderr)
    assert error_payload.get("error"), error_payload
    remedy = str(error_payload.get("remedy", ""))
    # The project-relative spelling, which the absolute path the remedy prints
    # ends with -- so this admits either spelling while still requiring the
    # remedy to name the file rather than merely to be a non-empty sentence.
    names_the_lock_file = lock.relative_to(project).as_posix()
    assert names_the_lock_file in remedy, (
        f"the remedy does not name {names_the_lock_file}, the file the operator "
        f"has to remove, so it is a sentence rather than a cure -- and the "
        f"generic fallback that lands here when a self-describing subtype's own "
        f"remedy is dropped sends them to their migration set instead: {remedy!r}"
    )


def test_an_ordinary_lock_file_is_still_taken_and_the_apply_succeeds(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the case above. GREEN before the fix and after.

    A refusal that fired on every lock file would satisfy the property above
    perfectly and break every write in the product. Both shapes the real world
    delivers are run here -- the lock file absent, which is the first apply in a
    fresh project, and a regular file already at the path, which is every apply
    after it -- so a fix that refuses either one fails here rather than in the
    field.

    Also the one place the created file's **mode** is observable. The open that
    stopped following links stopped taking its mode from the umask at the same
    time, and a lock file nothing reads has no reason to be world-readable
    (round one, code review M-4).
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_migration(project)
    lock = _lock_path(project)
    assert not lock.exists(), "the absent-lock arm needs the lock not to exist yet"

    first = runner.invoke(app, ["migrate", "apply", "--json"])

    assert first.exit_code == 0, first.stderr
    assert lock.is_file() and not lock.is_symlink()
    # Binds the file this run *created*, and only that. `os.open`'s mode
    # argument applies at creation, so a lock file that already existed keeps
    # whatever mode it had -- a project that ran an earlier build still carries
    # the umask-derived 0o644 it was created with, and nothing here or in the
    # product changes it. That gap is recorded rather than asserted: closing it
    # means chmod-ing a file this code did not create.
    assert lock.stat().st_mode & 0o777 == 0o600, (
        f"a newly created lock file is readable beyond its owner "
        f"({lock.stat().st_mode & 0o777:o}); nothing reads this file's bytes, "
        f"including its own holder"
    )

    second = runner.invoke(app, ["migrate", "apply", "--json"])

    assert second.exit_code == 0, second.stderr


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
