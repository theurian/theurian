"""What the shipped commands publish when the state database will not open (#530, #526).

Three faults, all met before a byte of the database has been interpreted, and
each one used to be reported as damage:

* a state directory this process may not write, which `_prepare` answered with
  "it is damaged ... delete `.theurian/state/`" (#530);
* an artefact that is not a regular file at the database path, which the read
  opener did not answer at all -- it blocked inside `open()` (#526).

**The bound is a subprocess, not `SIGALRM`.** The suite's `hang_guard` turns a
blocked `open()` into a failing test by raising from a signal handler, and that
does not reach this one: SQLite's `robust_open` retries the interrupted call, so
measured on 2026-09-06 the timer fired at 3 s and the process was still inside
`__open` at 150 s. Every command that could meet an unbounded open therefore runs
in a child this module kills, and the kill is what makes a regression a red test
rather than a stalled suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.cli.commands import STATE_REBUILD_REMEDY
from theurian.cli.main import app
from theurian.infrastructure.sqlite.connection import StateDatabaseUnreadableError

pytestmark = pytest.mark.integration

#: The exact sentence the damaged-database refusal publishes, computed from the
#: class rather than quoted. A quoted fragment goes stale the day someone
#: rewords that message, and a stale key passes while the defect is back --
#: `"damaged"` in particular is a substring of the *corrected* wording, which
#: says nothing here is damaged.
#:
#: `OperationalError` is the type name `_prepare` was measured passing in for
#: this configuration on 2026-09-06, so the comparison is against the string a
#: regression would actually produce.
_THE_DAMAGE_SENTENCE: Final = str(StateDatabaseUnreadableError("OperationalError"))

runner = CliRunner()

EXIT_STATE_ERROR: Final = 4

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all, so a
#: mode-based plant would report a *successful* command as a passing refusal.
#: The suite's standing idiom; offline CI runs as root.
_CANNOT_BE_REFUSED_BY_A_MODE: Final = sys.platform == "win32" or os.geteuid() == 0

#: `os.mkfifo` is POSIX-only, and it is the only way to plant the artefact #526
#: is about without a device node this test may not create.
_CAN_MAKE_A_NAMED_PIPE: Final = hasattr(os, "mkfifo")

#: How long a child gets before it is killed and the test fails. Generous next to
#: a command that opens a small SQLite file and short next to a CI job.
_CHILD_TIMEOUT_SECONDS: Final = 60.0

MIGRATION_ID: Final = "01K1FFFFFF01234567890ABCDE"
REVISION_ID: Final = "01K1FFFREV01234567890ABCDE"
BODY: Final = "# State faults\n\nEvery opener answers for what the path holds.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-09-06T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.state-faults
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.state-faults
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/state-faults.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: State faults
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/state-faults.md
"""

#: The child program: run one CLI invocation and report what it published.
#:
#: A child rather than `runner.invoke`, so a command that fails to bound its own
#: open is killed by the parent instead of stalling the run. `CliRunner` rather
#: than the `theurian` console script, so the test does not depend on the package
#: being installed on `PATH` -- a dependency that turns a real failure into a
#: silent skip.
#:
#: ``SystemExit`` is not an escape: it is how Typer *reports* the exit code, so
#: every refusing command carries one and reading it as a crash would fail every
#: assertion below on a correct refusal. The same narrowing
#: ``test_migrate_apply_lock_confinement.py`` makes on the same question.
_CHILD: Final = (
    "import json, sys\n"
    "from typer.testing import CliRunner\n"
    "from theurian.cli.main import app\n"
    "result = CliRunner().invoke(app, sys.argv[1:])\n"
    "escaped = None if isinstance(result.exception, SystemExit) else result.exception\n"
    "print(json.dumps({\n"
    "    'code': result.exit_code,\n"
    "    'out': result.stdout,\n"
    "    'err': result.stderr,\n"
    "    'raised': type(escaped).__name__ if escaped is not None else None,\n"
    "}))\n"
)


@pytest.fixture
def applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A real project with its canonical state built, ready to be damaged."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.chdir(root)

    started = runner.invoke(app, ["init", "--json"], catch_exceptions=False)
    assert started.exit_code == 0, started.stderr

    (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
    (root / ".theurian/knowledge/architecture/state-faults.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-state-faults.yaml").write_text(MIGRATION)

    result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stderr
    yield root


def _state_database(project: Path) -> Path:
    (database,) = (project / ".theurian/state").glob("theurian-state-*.sqlite")
    return database


def _run_in_a_child(project: Path, argv: list[str]) -> dict[str, object]:
    """One CLI invocation in a child, killed if it does not return.

    Returns the child's report. Fails the test naming the command if the child
    had to be killed, which is the only shape a lost bound can take here: a
    command that never returns publishes nothing to assert against.
    """
    environment = dict(os.environ)
    try:
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _CHILD, *argv],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_SECONDS,
            # A refusing command exits non-zero on purpose; that is the subject
            # here, not a failure of the harness.
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`theurian {' '.join(argv)}` did not return within {_CHILD_TIMEOUT_SECONDS}s "
            f"and was killed: the open is unbounded again, and nothing reaches the caller "
            f"to be graded"
        )
    lines = done.stdout.strip().splitlines()
    assert lines, (
        f"the child running `theurian {' '.join(argv)}` printed no report; it exited "
        f"{done.returncode} with stderr {done.stderr!r}"
    )
    report: dict[str, object] = json.loads(lines[-1])
    return report


def _envelope(report: dict[str, object], argv: list[str]) -> dict[str, str]:
    """The `{error, remedy}` document a refusing `--json` command owes its caller."""
    assert report["raised"] is None, (
        f"`theurian {' '.join(argv)}` escaped `--json` as a {report['raised']} rather than "
        f"a document, so a caller parsing stdout cannot tell a refusal from a crash"
    )
    assert report["out"] == "", (
        f"`theurian {' '.join(argv)}` wrote to stdout while failing; the machine channel "
        f"stays clean: {report['out']!r}"
    )
    payload: dict[str, str] = json.loads(str(report["err"]))
    assert payload.get("error"), payload
    assert payload.get("remedy"), payload
    return payload


# -- #530: an unwritable state directory is not a damaged database ------------
#
# `sqlite3.connect` succeeds against a database in a directory this process may
# not write; `PRAGMA journal_mode = WAL` then fails creating the `-wal` and
# `-shm` files beside it, with `SQLITE_READONLY_DIRECTORY`. `_prepare`'s broad
# catch called that damage, and the published cure opened "delete
# `.theurian/state/`" -- which destroys derived state over a permission bit and
# then fails identically on the rebuild, into the same directory.

#: The commands measured reaching `_prepare` in this configuration on 2026-09-06:
#: `migrate status` through its read-modify path and `migrate apply` through the
#: transaction. Both published the delete-state cure before #530.
_COMMANDS_THAT_PREPARE_A_CONNECTION: Final = (
    ["migrate", "status", "--json"],
    ["migrate", "apply", "--json"],
)


def _the_directory_really_denies_the_write(directory: Path) -> bool:
    """Whether the mode planted below actually refuses this process.

    The positive control on the plant, and not the same question as the skip
    above: a filesystem that ignores permission bits denies nothing to anyone,
    and a plant that quietly permitted the write would leave the assertions
    describing a *successful* command -- green, and about nothing.
    """
    probe = directory / ".theurian-write-probe"
    try:
        probe.touch()
    except OSError:
        return True
    probe.unlink()
    return False


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
@pytest.mark.parametrize(
    "argv", _COMMANDS_THAT_PREPARE_A_CONNECTION, ids=lambda argv: " ".join(argv[:2])
)
def test_an_unwritable_state_directory_is_not_answered_by_deleting_the_state(
    applied: Path, argv: list[str]
) -> None:
    """Issue #530. RED before `_prepare` classified the fault ahead of its broad catch.

    Measured against the pre-fix source with `.theurian/state/` at `0555` and the
    database present: both commands exited 4 publishing "This project's state
    database cannot be read (OperationalError): it is damaged, or holds a value
    this build cannot interpret ... delete `.theurian/state/`" -- over a file
    nothing had read a byte of.

    **The assertion is on the cure, not on the exception type.** What the
    operator does next is the whole defect: one instruction destroys derived
    state and rebuilds it into the same unwritable directory, the other is a
    `chmod`. So the remedy must name the directory and a command that changes
    its mode, and must not open by telling anyone to delete anything.

    The sidecars are removed first because a clean close removes them anyway --
    measured, `theurian-state-*.sqlite-wal` and `-shm` are gone once the applying
    connection closes -- and their presence is exactly what makes this
    configuration *work*: with a live `-shm` already there, both openers succeed
    at `0555`. Deleting them is not a contrivance, it is the state a real project
    is in between commands.
    """
    database = _state_database(applied)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    directory = database.parent
    directory.chmod(0o555)
    try:
        if not _the_directory_really_denies_the_write(directory):
            pytest.skip("this filesystem does not refuse a write to a 0555 directory")

        report = _run_in_a_child(applied, argv)
        payload = _envelope(report, argv)
    finally:
        directory.chmod(0o755)

    assert report["code"] == EXIT_STATE_ERROR, report
    remedy = payload["remedy"]
    assert str(directory) in remedy, (
        f"the remedy does not name the directory whose mode is the fault, so the reader "
        f"has to guess which path to act on: {remedy!r}"
    )
    assert "chmod" in remedy, (
        f"the remedy names no command that changes a mode, so it is a diagnosis rather "
        f"than a cure: {remedy!r}"
    )
    assert STATE_REBUILD_REMEDY not in remedy, (
        f"the cure still carries the delete-your-state instruction, for a database "
        f"nothing has read a byte of: {remedy!r}"
    )
    assert payload["error"] != _THE_DAMAGE_SENTENCE, (
        f"the published message is still the damaged-database one, word for word, about "
        f"an intact file: {payload['error']!r}"
    )


SECOND_MIGRATION_ID: Final = "01K1GGGGGG01234567890ABCDE"
SECOND_REVISION_ID: Final = "01K1GGGREV01234567890ABCDE"
SECOND_BODY: Final = "# A second decision\n\nUnapplied, so the state hash moves.\n"

SECOND_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {SECOND_MIGRATION_ID}
createdAt: 2026-09-06T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.second-decision
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.second-decision
    revisionId: {SECOND_REVISION_ID}
    contentFile: ../knowledge/architecture/second-decision.md
    contentSha256: {body_pin(SECOND_BODY)}
    metadata:
      title: A second decision
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/second-decision.md
"""


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_fr_k5_check_over_an_unwritable_state_directory_publishes_a_document(
    applied: Path,
) -> None:
    """The same fault one call earlier, where an unhandled type escapes entirely.

    ``_verify_history`` runs inside ``_require_project`` and opens the
    *previously active* state database to check FR-K5. It sits **outside**
    ``_require_project``'s own ``try``, so a ``TheurianError`` it does not name
    reaches a ``--json`` caller as a Rich traceback with an empty machine
    channel -- the CP-2 shape #483 closed and the one a new exception type
    reopens by existing.

    Reaching it needs the previously active state to differ from the one being
    built, which an *unapplied* second migration produces: the state hash is a
    function of the migration set (ADR-0016), so adding a file moves it without
    touching the database on disk. The early return at
    ``active.state_hash == context.state_hash`` is why the tests above do not
    reach this path, and why this one has to arrange it deliberately.

    ``migrate validate`` is the command driven here rather than ``migrate
    status``: it routes through ``_require_project`` like every other one, and
    it has no state-database work of its own afterwards, so what it publishes
    can only have come from the FR-K5 check.
    """
    (applied / ".theurian/knowledge/architecture/second-decision.md").write_text(SECOND_BODY)
    (applied / f".theurian/migrations/{SECOND_MIGRATION_ID}-second-decision.yaml").write_text(
        SECOND_MIGRATION
    )
    database = _state_database(applied)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    directory = database.parent
    directory.chmod(0o555)
    argv = ["migrate", "validate", "--json"]
    try:
        if not _the_directory_really_denies_the_write(directory):
            pytest.skip("this filesystem does not refuse a write to a 0555 directory")

        report = _run_in_a_child(applied, argv)
        payload = _envelope(report, argv)
    finally:
        directory.chmod(0o755)

    assert report["code"] == EXIT_STATE_ERROR, report
    assert "FR-K5" in payload["error"], (
        f"the refusal does not say which guarantee could not be confirmed, so a reader "
        f"cannot tell it from an unrelated state fault: {payload['error']!r}"
    )
    assert str(directory) in payload["remedy"] and "chmod" in payload["remedy"], (
        f"the FR-K5 refusal names no directory to act on and no command that acts on it: "
        f"{payload['remedy']!r}"
    )
    assert STATE_REBUILD_REMEDY not in payload["remedy"], (
        f"the FR-K5 refusal still offers to delete the state -- which here destroys the "
        f"tamper evidence the check exists to hold, over a permission bit: "
        f"{payload['remedy']!r}"
    )


# -- #526: an artefact at the database path is refused, never waited on --------
#
# `sqlite3.connect(f"file:{path}?mode=ro")` issues `os.open(path, O_RDONLY)`,
# which blocks on a named pipe until a writer appears. Nothing downstream bounds
# it: the driver takes a timeout for locks, not for the open, and SQLite's
# `robust_open` retries an `open` interrupted by a signal, so the suite's
# `SIGALRM` guard does not reach it either. `_connect` therefore refuses on the
# shape, before the open, which is the only position where the check cannot be
# the thing that hangs.

#: The commands measured meeting the state database with a FIFO planted at its
#: path, 2026-09-06 against the real CLI. `index build` is the one that hung --
#: it reads through `SqliteCanonicalStore` -- while `migrate status` and `migrate
#: apply` reached the write opener and published a driver complaint ("disk I/O
#: error") under a cure about NFS and deleting state. `index status` is here as
#: the negative: it exited 0, because it never opens the canonical store, and a
#: sweep that quietly lost a command would show up as this one changing.
_COMMANDS_OVER_THE_DATABASE_PATH: Final = (
    ["index", "build", "--json"],
    ["migrate", "status", "--json"],
    ["migrate", "apply", "--json"],
)


def _replace_the_database_with(project: Path, plant: str) -> Path:
    """Put ``plant`` where the state database is, and return the path."""
    database = _state_database(project)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    database.unlink()
    if plant == "fifo":
        os.mkfifo(database)
    else:  # pragma: no cover - one plant today; the branch keeps the name honest
        raise AssertionError(f"unknown plant {plant!r}")
    return database


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
@pytest.mark.parametrize(
    "argv", _COMMANDS_OVER_THE_DATABASE_PATH, ids=lambda argv: " ".join(argv[:2])
)
def test_a_named_pipe_at_the_database_path_is_refused_rather_than_waited_on(
    applied: Path, argv: list[str]
) -> None:
    """Issue #526's read face, and the write opener beside it.

    RED before `_connect` checked the shape, in two different ways depending on
    the opener -- which is why one test covers both rather than two tests
    covering one each:

    * `index build` reads through `SqliteCanonicalStore`, whose open is
      `mode=ro`. Measured against the pre-fix source: still inside `__open` when
      a 12-second kill fired, with an empty `--json` stdout and nothing on
      stderr. That is worse than a traceback -- nothing arrives to grade.
    * `migrate status` and `migrate apply` reach the write opener, where `O_RDWR`
      on a named pipe returns at once. They published `disk I/O error` under a
      cure about NFS and deleting state: bounded, and about nothing the operator
      can act on.

    The kill in :func:`_run_in_a_child` is the bound, and it has to be: the
    suite's `SIGALRM` guard does not escape this open (SQLite retries the
    interrupted call), so a regression under an in-process runner would stall the
    whole run instead of failing this test.

    The remedy is asserted to name the file and a command to run, not to be a
    particular sentence: `index build` publishes its own cure for every state
    fault it meets rather than the exception's, so requiring one wording here
    would be asserting that command's contract instead of this refusal's. What
    all three owe a reader is the same two things -- which file, and what to
    type.
    """
    database = _replace_the_database_with(applied, "fifo")
    report = _run_in_a_child(applied, argv)
    payload = _envelope(report, argv)

    assert report["code"] != 0, (
        f"`theurian {' '.join(argv)}` reported success over a named pipe where its state "
        f"database belongs: {report}"
    )
    remedy = payload["remedy"]
    assert database.name in remedy, (
        f"the remedy does not name the file holding the named pipe, so the reader cannot "
        f"tell which artefact to act on: {remedy!r}"
    )
    assert "theurian migrate apply" in remedy, (
        f"the remedy names nothing the reader can run to get back to a working state: {remedy!r}"
    )
    assert payload["error"] != _THE_DAMAGE_SENTENCE, (
        f"the refusal is still the damaged-database sentence, about a file nothing "
        f"opened: {payload['error']!r}"
    )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_the_refusal_names_the_artefact_and_not_the_driver(applied: Path) -> None:
    """What the refusal *says*, pinned once rather than at every swept command.

    The sweep above asserts the shape of what arrives; this asserts its content,
    and the two are separated because a message assertion repeated per command
    turns one wording decision into three failing tests.

    "a named pipe (FIFO)" is the vocabulary `security/paths.py` already publishes
    for the same artefact behind a `contentFile`, and
    `tests/unit/test_connection_faults.py::
    test_both_shape_namers_answer_alike_for_every_file_type` is what keeps the
    two spellings equal. Asserted here because that unit test compares the two
    namers to each other and would stay green if both drifted together, while
    this one is about what an operator actually reads.
    """
    _replace_the_database_with(applied, "fifo")
    argv = ["index", "build", "--json"]
    payload = _envelope(_run_in_a_child(applied, argv), argv)

    assert "a named pipe (FIFO)" in payload["error"], (
        f"the refusal does not say what is at the path, so the reader is left with a "
        f"driver complaint to interpret: {payload['error']!r}"
    )
