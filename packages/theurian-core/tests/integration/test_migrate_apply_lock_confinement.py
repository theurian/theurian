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

import contextlib
import errno
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

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

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_cli_commands.py` and `test_auth_rotate.py` use before a
#: permission-refusal test. Offline CI runs as root, where a mode denies nothing.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

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


# -- The remedy is executed, not read -----------------------------------------
#
# `.theurian/runtime` replaced by a symbolic link out of the tree is refused by
# containment, and the refusal publishes an instruction to remove that path. The
# instruction is a shell command, so the only test that can say whether it is
# safe is one that *runs* it: round two's finding was a remedy that read
# perfectly and destroyed the link's target when followed, because `rm -rf` on a
# trailing-slash symlink follows the link. Measured here on `/bin/rm` before this
# test was written -- `rm -rf link/` left the link in place and took the target
# directory and its files with it, while `rm -rf link` and `rm link` both removed
# the link and left the target byte-identical.


#: A backquoted `rm` invocation with a real path argument.
#:
#: The final argument may not begin with `-`, which is the whole reason this is
#: not the obvious pattern: the remedy also contains the bare words ``rm -rf``
#: inside the sentence warning about the trailing slash, and a pattern that
#: allowed a flag as the last token matched that too (measured while writing
#: this) -- handing the runner a command with no operand.
_AN_RM_COMMAND: Final = re.compile(r"`(rm(?:\s+-[A-Za-z]+)*\s+[^\s`-][^\s`]*)`")

#: Anything backquoted that ends in a slash -- the destructive rendering.
_A_TRAILING_SLASH_PATH: Final = re.compile(r"`[^`]*/`")


def _plant_an_escaping_runtime_link(runtime: Path, outside: Path) -> None:
    """Replace ``runtime`` with a symbolic link to ``outside``, from any prior state.

    Both branches are load-bearing: the first plant replaces the real directory
    ``theurian init`` created, and a re-plant may find a link the command under
    test failed to remove -- and ``shutil.rmtree`` does not remove a symbolic
    link, it declines (silently, under ``ignore_errors``), so a single-branch
    helper leaves the old link and the next ``symlink_to`` raises ``FileExists``.
    """
    if runtime.is_symlink():
        runtime.unlink()
    else:
        shutil.rmtree(runtime, ignore_errors=True)
    runtime.symlink_to(outside)


def _rm_commands(remedy: str) -> list[list[str]]:
    """Every `rm` the remedy offers, split into argv exactly as written.

    ``shlex.split`` and no shell: the point is to run what the reader is told to
    run, and a shell would add its own word-splitting to a string this test is
    supposed to be reproducing faithfully.
    """
    return [shlex.split(found) for found in _AN_RM_COMMAND.findall(remedy)]


def test_the_published_remedy_for_an_escaping_runtime_link_is_safe_when_executed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#483 round two. The remedy is run, and the link's target has to survive it.

    RED before the fix, GREEN after. The remedy read ``Delete
    `.theurian/runtime/` `` -- with the trailing slash -- and BSD ``rm -rf``
    follows a link spelled that way. Measured end to end: the whole directory the
    link pointed at, outside the working tree, was destroyed, **and the link was
    left in place**, so the retry met the identical refusal and the reader had
    lost data and gained nothing.

    **This executes the instruction rather than matching its text**, which is the
    only way the property can be checked at all: no string assertion distinguishes
    a command that removes a link from one that removes what the link points at.
    Each offered command is run against its own fresh plant, because a reader
    picks *one* of the two by looking at the path -- running them in sequence
    would test only the first against the link and leave the second measured
    against an already-empty path.

    **The target is a directory holding files, which is where the severity is.**
    A link to a single file loses one file; the measured case was a whole tree.
    The bytes are compared rather than the existence of the directory, so a
    command that emptied it without removing it would still fail here.

    **The structural pin rides beside this, and does not replace it.** The
    destructive asymmetry is BSD's: GNU ``rm`` refuses a trailing-slash symlink
    outright, so on a GNU box the execution below passes even for the old
    wording. :func:`test_the_remedy_never_renders_a_path_with_a_trailing_slash`
    is what catches a re-introduction there.

    ``names_a_remedy`` is deliberately not applied. The ``runtime`` remedy names
    no Theurian command on purpose -- nothing under that directory needs
    rebuilding -- so the actionable instruction *is* the ``rm``, and requiring a
    command would push a false rebuild into it.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_migration(project)

    outside = tmp_path / "outside-the-working-tree"
    outside.mkdir()
    (outside / "notes.md").write_text("a file the reader has not backed up\n")
    (outside / "nested").mkdir()
    (outside / "nested" / "deeper.md").write_text("and one further down\n")
    before = {
        path.relative_to(outside).as_posix(): path.read_bytes()
        for path in sorted(outside.rglob("*"))
        if path.is_file()
    }
    assert before, "the target must hold files, or 'the files survived' asserts nothing"

    runtime = _lock_path(project).parent
    _plant_an_escaping_runtime_link(runtime, outside)

    refused = runner.invoke(app, ["migrate", "apply", "--json"])

    assert refused.exit_code == EXIT_STATE_ERROR, refused.stdout + (refused.stderr or "")
    remedy = str(json.loads(refused.stderr)["remedy"])
    commands = _rm_commands(remedy)
    assert commands, (
        f"the remedy offers no runnable `rm`, so there is nothing for a reader to "
        f"execute and nothing for this test to check: {remedy!r}"
    )

    for command in commands:
        target = command[-1]
        assert not Path(target).is_absolute() and ".." not in Path(target).parts, (
            f"the remedy instructs removing {target!r}, which is not a path inside "
            f"the project; this test will not run it"
        )
        _plant_an_escaping_runtime_link(runtime, outside)

        completed = subprocess.run(  # noqa: S603 - argv from the remedy, checked above
            command, cwd=project, capture_output=True, text=True, check=False
        )

        assert completed.returncode == 0, (
            f"a command the remedy tells the reader to run failed: "
            f"{command} -> {completed.stderr.strip()!r}"
        )
        assert not runtime.is_symlink() and not runtime.exists(), (
            f"{command} left the escaping link in place, so the retry meets the "
            f"identical refusal -- which is exactly what the trailing-slash form did"
        )
        assert outside.is_dir(), f"{command} removed the directory the link pointed at"
        after = {
            path.relative_to(outside).as_posix(): path.read_bytes()
            for path in sorted(outside.rglob("*"))
            if path.is_file()
        }
        assert after == before, (
            f"{command} changed what the link pointed at, outside the working tree. "
            f"Before: {sorted(before)}; after: {sorted(after)}"
        )

    retried = runner.invoke(app, ["migrate", "apply", "--json"])

    assert retried.exit_code == 0, (
        f"following the remedy did not clear the refusal: {retried.stderr}"
    )
    assert runtime.is_dir() and not runtime.is_symlink(), (
        "the retry did not recreate `runtime` as a real directory, so the remedy's "
        "claim that the next command recreates it is false"
    )


def test_the_remedy_never_renders_a_path_with_a_trailing_slash(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cheap structural guard against re-introducing the destructive form.

    The execution test above is the real property, and it cannot see this one
    everywhere: BSD ``rm`` destroys through a trailing-slash symlink and GNU
    ``rm`` refuses outright, so on a GNU runner the old wording would execute
    harmlessly and pass. What is platform-independent is that the remedy must
    never *render* the path with a trailing slash -- neither as the standalone
    `` `.theurian/runtime/` `` the first cut published nor inside an ``rm``
    invocation -- because a reader on macOS following it loses the target.

    Applied to every backquoted span rather than to the known bad string, so a
    reworded remedy that reintroduces the shape somewhere else fails here too.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_migration(project)
    outside = tmp_path / "outside-the-working-tree"
    outside.mkdir()
    _plant_an_escaping_runtime_link(_lock_path(project).parent, outside)

    refused = runner.invoke(app, ["migrate", "apply", "--json"])

    assert refused.exit_code == EXIT_STATE_ERROR, refused.stdout + (refused.stderr or "")
    remedy = str(json.loads(refused.stderr)["remedy"])
    rendered_with_a_slash = _A_TRAILING_SLASH_PATH.findall(remedy)
    assert rendered_with_a_slash == [], (
        f"the remedy renders a path with a trailing slash: {rendered_with_a_slash}. "
        f"`rm -rf` follows a symbolic link spelled that way and deletes what it "
        f"points at while leaving the link in place (measured on /bin/rm)"
    )


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


# -- A lock the open cannot take is a refusal, not a traceback (#520) ---------
#
# Both calls the write lock's own `held` makes before it has a descriptor now
# translate their own `OSError` into `WriteLockUnusableError`: the open, and the
# `mkdir` on the lock's parent beside it. Until this section landed the open translated exactly
# one shape -- the `O_NOFOLLOW` `ELOOP` a symbolic link at the final component
# produces -- and the `mkdir` translated none, while every handler that could
# catch one narrows to `TheurianError`: `migrate apply`'s `except TheurianError`
# around the whole critical section, `findings build`'s, and
# `write_transaction`'s. So an ordinary filesystem condition at the lock path
# left `--json` with a Rich traceback and an empty machine channel.
#
# Four artefacts a clone or a bad umask really delivers, each measured producing
# exit 1, **zero bytes of stdout and zero bytes of stderr**, and an uncaught
# exception -- the first three on `491bded6`, the fourth on the commit that added
# it here:
#
#   - a directory at `.theurian/runtime/write.lock` -> `IsADirectoryError`;
#   - a lock file at mode 0000 -> `PermissionError`;
#   - `.theurian/runtime` at mode 0500 with no lock file yet -> `PermissionError`
#     from the `O_CREAT`;
#   - `.theurian/runtime` absent under a `.theurian` at mode 0500 ->
#     `PermissionError` from the `mkdir`, one call earlier than the other three
#     and found by a different key: reading `held`, not sweeping the lock path.
#
# **A FIFO is deliberately not among them.** `O_WRONLY` on a FIFO with no reader
# blocks, so that artefact hangs inside the open rather than raising, and closing
# it needs a different change (#526, the lock face). Nothing here plants one, and
# every arm below passes without that fix.


@dataclass(frozen=True, slots=True)
class UnusableLock:
    """One artefact at the lock path that the lock open cannot accept.

    ``plant`` is handed the lock path and leaves the artefact there; ``restore``
    puts the tree back far enough for the fixture teardown to remove it, which a
    directory at mode 0500 otherwise refuses.
    """

    label: str
    plant: Callable[[Path], None]
    restore: Callable[[Path], None]
    needs_a_mode_that_denies: bool


def _plant_a_directory(lock: Path) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.unlink(missing_ok=True)
    lock.mkdir()


def _plant_an_unreadable_lock_file(lock: Path) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.unlink(missing_ok=True)
    lock.touch()
    lock.chmod(0o000)


def _plant_a_runtime_directory_that_denies_creation(lock: Path) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.unlink(missing_ok=True)
    lock.parent.chmod(0o500)


def _plant_a_knowledge_directory_that_denies_the_runtime_create(lock: Path) -> None:
    """Take ``.theurian/runtime`` away and make ``.theurian`` refuse to recreate it.

    The one artefact that stops the acquisition *before* the open: ``held`` runs
    ``mkdir(parents=True, exist_ok=True)`` on the lock's parent first, and with
    the parent absent that is a real create rather than the ``EEXIST`` the other
    three plants produce. Mode ``0500`` on ``.theurian`` leaves every read the
    command makes on the way here working -- the migration set and the state
    directory are both still traversable -- so the first thing that fails is the
    ``mkdir``.
    """
    shutil.rmtree(lock.parent, ignore_errors=True)
    lock.parent.parent.chmod(0o500)


UNUSABLE_LOCKS: Final = (
    UnusableLock(
        label="a directory where the lock file belongs",
        plant=_plant_a_directory,
        restore=lambda lock: shutil.rmtree(lock, ignore_errors=True),
        needs_a_mode_that_denies=False,
    ),
    UnusableLock(
        label="a lock file this process may not open",
        plant=_plant_an_unreadable_lock_file,
        restore=lambda lock: lock.chmod(0o600),
        needs_a_mode_that_denies=True,
    ),
    UnusableLock(
        label="a runtime directory that refuses the create",
        plant=_plant_a_runtime_directory_that_denies_creation,
        restore=lambda lock: lock.parent.chmod(0o700),
        needs_a_mode_that_denies=True,
    ),
    UnusableLock(
        label="a knowledge directory that refuses to recreate runtime",
        plant=_plant_a_knowledge_directory_that_denies_the_runtime_create,
        restore=lambda lock: lock.parent.parent.chmod(0o700),
        needs_a_mode_that_denies=True,
    ),
)


def _the_acquisition_really_refuses(lock: Path) -> bool:
    """Whether taking the lock actually fails over the planted artefact.

    The positive control on the plant. A mode denies nothing to root and nothing
    on a filesystem that ignores permission bits, and a plant that quietly
    permitted the acquisition would leave the assertions below describing a
    *successful* apply -- green, and about nothing.

    Both calls ``held`` makes before it has a descriptor are issued here, in that
    order and with the same arguments: the ``mkdir`` on the lock's parent, then
    the ``open`` with the flags the write lock's own ``_open`` uses. Probing only
    the open would answer ``True`` for the ``mkdir`` plant by accident -- the lock
    file is missing, so the open fails with ``ENOENT`` whether or not the
    ``mkdir`` was refused -- and a plant whose refusal the probe cannot attribute
    is a plant that can go quietly wrong.

    ``ELOOP`` is excluded because ``O_NOFOLLOW`` returns it for a symbolic link,
    which is #481's artefact and has its own test above.
    """
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return True
    try:
        handle = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        return exc.errno != errno.ELOOP
    os.close(handle)
    return False


@pytest.mark.parametrize("artefact", UNUSABLE_LOCKS, ids=lambda case: case.label)
def test_a_lock_the_open_cannot_take_is_refused_as_a_document(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artefact: UnusableLock
) -> None:
    """Issue #520 (CP-2). RED at ``491bded6``, GREEN since ``_open`` converts.

    A ``--json`` caller receives one ``{error, remedy}`` document on stderr and
    an empty stdout, whatever is at the lock path. Before the conversion widened
    it received *nothing* on either channel for all three artefacts: the
    ``OSError`` walked past ``migrate apply``'s ``except TheurianError`` and Typer
    rendered it as a boxed traceback with absolute source paths -- a caller
    parsing stdout could not tell a refusal from a crash, and the operator was
    handed the product's internals instead of a cure.

    **The assertions are observables, never the mechanism.** Which exception the
    conversion raises, and whether ``_open`` widens its ``except`` or checks the
    path first, is the implementation's business. What is pinned is what reaches
    the caller: the exit code every other state-integrity refusal uses, a clean
    machine channel, and a remedy naming the file to act on.

    **The refusal must not describe the artefact as a symbolic link.** None of
    these three is one, and ``WriteLockUnusableError``'s message said "is a
    symbolic link, not a lock file" unconditionally -- correct for the #481 case
    it was written for and false for every artefact here. A published sentence
    that is false about the thing it describes sends the reader looking for a
    link that is not there; a widened translation that reused the old wording was
    the likely shape of the fix, and this is what refuses it. The shipped
    conversion keys the sentence on the errno instead, so ``ELOOP`` still
    publishes it and nothing else does.
    """
    if artefact.needs_a_mode_that_denies and _CANNOT_BE_REFUSED_BY_A_MODE:
        pytest.skip("POSIX permission bits, and not as root")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_migration(project)
    lock = _lock_path(project)
    artefact.plant(lock)
    try:
        if not _the_acquisition_really_refuses(lock):
            pytest.skip(f"this filesystem accepts the lock over {artefact.label}")

        result = runner.invoke(app, ["migrate", "apply", "--json"])

        escaped = None if isinstance(result.exception, SystemExit) else result.exception
        assert escaped is None, (
            f"the refusal over {artefact.label} escaped `--json` as a traceback "
            f"rather than an envelope: {escaped!r}"
        )
        assert result.exit_code == EXIT_STATE_ERROR, (
            "a lock this command cannot take is a knowledge-state problem the "
            "user must repair, graded like every other one"
        )
        assert result.stdout == ""
        payload = json.loads(result.stderr)
        assert payload.get("error"), payload
        remedy = str(payload.get("remedy", ""))
        names_the_lock_file = lock.relative_to(project).as_posix()
        assert names_the_lock_file in remedy, (
            f"the remedy does not name {names_the_lock_file}, the file the operator "
            f"has to deal with, so it is a sentence rather than a cure: {remedy!r}"
        )
        if not (lock.exists() or lock.is_symlink()):
            # Two of the four artefacts leave *nothing* at the lock path -- the
            # `0500` runtime directory refusing the `O_CREAT`, and the
            # `.theurian` that will not recreate `runtime/` -- and a cure that
            # opens "remove whatever is at <lock path>" sends the reader to
            # delete a file that is not there, past the permission that is the
            # actual fix. Same defect as the symbolic-link sentence below, in the
            # remedy rather than the message.
            assert "Remove whatever is at" not in remedy, (
                f"nothing is at {lock}, and the cure opens by telling the reader to "
                f"remove it: {remedy!r}"
            )
        published = f"{payload.get('error', '')}\n{remedy}"
        assert "symbolic link" not in published, (
            f"the refusal calls {artefact.label} a symbolic link, which it is not; "
            f"the reader is sent to look for a link that does not exist: {published!r}"
        )
    finally:
        artefact.restore(lock)


# -- A fault inside the transaction must not delete the state (#521) ----------


def _state_database(project: Path) -> Path:
    (database,) = (project / ".theurian/state").glob("theurian-state-*.sqlite")
    return database


def _apply_once(project: Path) -> Path:
    """Build the corpus's canonical state, and return the provenanced database.

    Provenance is asserted rather than assumed: the ``created is False`` arm of
    ``migrate apply`` -- the one this section is about -- is reached only for a
    database this installation built and recorded. Without the record, the apply
    takes the *discard-untrusted-state* branch instead, deletes the file on
    purpose, and a test written against that path would be pinning the opposite
    behaviour.
    """
    _write_migration(project)
    applied = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)
    assert applied.exit_code == 0, applied.stderr
    state_hash = str(json.loads(applied.stdout)["stateHash"])
    assert BuildProvenance.default().has_state(project, state_hash), (
        "the corpus is not provenanced, so a re-apply would take the discard "
        "branch and this pin would be about a different code path"
    )
    return _state_database(project)


def _assert_the_state_survived(project: Path, database: Path, before: bytes) -> None:
    """The whole property: a failed command is not allowed to become data loss."""
    assert database.exists(), (
        "the failed apply deleted the canonical state database it could not write "
        "to -- every revision the project ever recorded, removed because an "
        "unrelated fault interrupted one transaction"
    )
    assert database.read_bytes() == before, (
        "the failed apply changed the canonical state database's bytes; a "
        "transaction that could not proceed must leave the file as it found it"
    )
    after = runner.invoke(app, ["migrate", "status", "--json"], catch_exceptions=False)
    assert after.exit_code == 0, after.stderr
    assert json.loads(after.stdout)["stateBuilt"] is True, (
        "the project no longer reports built state after a failed apply"
    )


def test_a_fault_inside_the_transaction_leaves_the_provenanced_state_untouched(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #521: pin the no-cleanup decision the transaction backstop records.

    ``migrate apply``'s ``(OSError, sqlite3.Error)`` handler around
    ``apply_migration_set`` deliberately does **not** unlink, and section A's
    handler a few lines above deliberately does -- the asymmetry is the whole
    point, and its reasoning is written into the code: section A may have left a
    half-written file it just created, while here the database is very often one
    this installation built and provenanced, so "deleting a live state because an
    unrelated fault interrupted a write would turn a failed command into data
    loss".

    That reasoning was measured during #518 and pinned by nothing. Adding
    ``_unlink_database_and_sidecars(database)`` to this handler passed the whole
    suite: no test forced a fault at this point against a database worth keeping.
    GREEN before and after, and RED under exactly that mutation.

    The fault is injected at ``apply_migration_set`` rather than produced on
    disk, so the arm runs everywhere -- including as root, where the read-only
    companion below cannot. ``database_created`` is read off the call to prove the
    injection landed in the ``created is False`` arm; a fixture that had not
    applied first would inject into section A's territory and pin nothing.
    """
    database = _apply_once(project)
    before = database.read_bytes()
    created_flags: list[Any] = []

    def raise_inside_the_transaction(**kwargs: Any) -> None:
        created_flags.append(kwargs.get("database_created"))
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr("theurian.cli.commands.apply_migration_set", raise_inside_the_transaction)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

    assert created_flags == [False], (
        f"the fault did not land in the pre-existing-database arm: {created_flags}"
    )
    assert result.exit_code == EXIT_STATE_ERROR
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload.get("error")
    assert payload.get("remedy")
    _assert_the_state_survived(project, database, before)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_real_write_fault_inside_the_transaction_leaves_the_state_untouched(
    project: Path,
) -> None:
    """The same property with the fault produced on disk rather than injected.

    A read-only canonical database: ``sqlite3.connect`` succeeds, ``_prepare``
    runs, and ``BEGIN IMMEDIATE`` fails with SQLite's own "attempt to write a
    readonly database" -- a ``sqlite3.Error`` reaching the same backstop by the
    route a real filesystem produces, with no production symbol replaced. The
    companion above is what covers the platforms where a mode denies nothing;
    this is what proves the injected arm is not merely testing a mock.

    Re-applying the *same* migration set on purpose: adding a migration would
    move the state hash, and the apply would build a new database rather than
    meeting the fault at the provenanced one this pin is about.
    """
    database = _apply_once(project)
    for sidecar in ("-wal", "-shm"):
        database.with_name(f"{database.name}{sidecar}").unlink(missing_ok=True)
    before = database.read_bytes()
    database.chmod(0o444)
    try:
        result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)

        assert result.exit_code == EXIT_STATE_ERROR, result.stdout + (result.stderr or "")
        assert result.stdout == ""
        payload = json.loads(result.stderr)
        assert payload.get("error")
        assert payload.get("remedy")
        assert database.exists(), (
            "the failed apply deleted a canonical state database it merely could not write to"
        )
        assert database.read_bytes() == before
    finally:
        # The file may be gone, which is the failure this test exists to catch --
        # so a teardown that raised over its absence would replace that message
        # with a `FileNotFoundError` from `chmod` and hide what went wrong.
        with contextlib.suppress(OSError):
            database.chmod(0o644)
