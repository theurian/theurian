"""Connection management and the write lock (ADR-0018, NFR-7).

Reads open independent WAL connections through :func:`open_read_connection`.
A write runs inside :func:`write_transaction`, which holds an OS advisory lock
on its ``lock_path`` for the duration of the transaction, so two processes that
both enter it serialise rather than corrupt.

Entering it is what carries the guarantee. ``CanonicalStore`` publishes its
write methods -- ``append_revision``, ``put_item``, ``add_relation`` and the
rest -- directly, so exclusivity is held by convention at each call site rather
than behind a single interface. ADR-0018 records this in its Milestone 5
amendment, which retracted the single-interface claim this docstring used to
repeat.

Milestone 3 adds a daemon-owned asyncio queue for in-daemon writes and keeps this
lock for CLI invocations running alongside it (ADR-0018 point 3). Both are
required between Milestone 3 and 1.0, because a CLI invocation is a separate
process that a queue inside the daemon cannot reach.
"""

from __future__ import annotations

import errno
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

if sys.platform == "win32":  # pragma: no cover - Windows is not a 1.0 target
    # Checked before the import, or `import fcntl` raises first with a message
    # that says nothing about why Theurian needs it.
    raise ImportError(
        "Theurian's write lock is POSIX-only (fcntl). Windows needs an msvcrt or "
        "LockFileEx implementation; see packaging/windows/README.md."
    )

import fcntl  # must follow the platform guard above

from theurian.domain.errors import TheurianError
from theurian.infrastructure.sqlite.schema import (
    CONNECTION_PRAGMAS,
    DDL,
    SCHEMA_VERSION,
    is_supported,
)

#: How long to wait for the write lock before giving up. Long enough for a
#: normal migration run to finish, short enough that a wedged process is
#: reported rather than waited on indefinitely.
WRITE_LOCK_TIMEOUT_SECONDS: Final = 30.0


class SchemaVersionMismatchError(TheurianError):
    """The database was written by a build with a different schema.

    Not an upgrade prompt: state databases are rebuilt, never migrated
    (ADR-0017). The remedy is always to replay the Git-tracked migrations.
    """

    def __init__(self, path: Path, found: int, expected: int) -> None:
        self.path = path
        self.found = found
        self.expected = expected
        super().__init__(
            f"{path.name} was written at schema version {found}, but this build uses "
            f"{expected}. State databases are derived; rebuild with "
            f"`theurian migrate apply` rather than migrating this file."
        )


class WriteLockTimeoutError(TheurianError):
    """Another process held the write lock for too long.

    **Sets its own remedy** (#404 R1-5). It never did, so it inherited
    ``TheurianError``'s empty default, and every caller that reads
    ``exc.remedy or <default>`` -- ``findings build`` among them -- fell to a
    generic cure ("Run `theurian doctor`") that does not clear a held lock. The
    remedy names the actual cure and is byte-identical to the text
    ``cli/commands.py::_state_remedy`` already returns for this type through its
    own ``isinstance`` branch, so ``migrate apply`` (which resolves the remedy
    *before* reading ``exc.remedy``) is unaffected while every other acquirer now
    inherits the right one.
    """

    def __init__(self, path: Path, seconds: float) -> None:
        self.remedy = "Wait for the other `theurian` process to finish, then retry."
        super().__init__(
            f"Could not acquire the write lock on {path.name} within {seconds:.0f}s. "
            f"Another Theurian process is writing. If none is running, remove {path}."
        )


#: SQLite's two primary result codes for "someone else is holding it": a lock
#: this connection could not take (``SQLITE_BUSY``) and one it could not take
#: without deadlocking (``SQLITE_LOCKED``).
#:
#: **``SQLITE_BUSY`` is the only measured arrival at the two statements
#: :func:`_execute_own` guards. The second member and the mask below are defense
#: in depth, not something measurement reached** (pre-round-two sweep; measured
#: 2026-09-03 on SQLite 3.47.1):
#:
#: * ``BEGIN IMMEDIATE`` under a held write lock reports the **primary** code 5,
#:   and so does the only ``COMMIT`` contention reproducible at all -- see
#:   :func:`_open_transaction`, which records that the ``COMMIT`` arm needs a
#:   rollback journal to fire.
#: * ``SQLITE_LOCKED`` is table-level contention within one connection or a
#:   shared cache. It was not demonstrated reachable across the separate
#:   connections this module opens, and no test drives it.
#: * No extended spelling was demonstrated either. ``SQLITE_BUSY_SNAPSHOT``
#:   (517) needs a *deferred* transaction upgrading a read to a write, which
#:   cannot happen at either guarded statement: this function opens with ``BEGIN
#:   IMMEDIATE``, so the write lock is already held by the time any caller
#:   statement runs, and the upgrade path lives inside the ``yield`` that is
#:   deliberately left unconverted.
#:
#: So the mask (``sqlite_errorcode & 0xFF``) guards a case nothing has reached
#: rather than one measurement found. It is kept because it costs one bitwise
#: ``and`` and it over-covers in the safe direction: an extended spelling of a
#: condition this set *does* name would otherwise fall through to the
#: damaged-file class and be answered with a cure that deletes state.
_CONTENTION_RESULT_CODES: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})


def _is_contention(exc: sqlite3.Error) -> bool:
    """Whether SQLite is reporting another writer rather than an unusable file.

    ``sqlite_errorcode`` exists only on errors SQLite itself raised; a
    module-raised ``sqlite3.Error`` -- a `ProgrammingError` for the wrong number
    of bind parameters, say -- carries no such attribute at all (measured on
    CPython 3.13). So its absence is read as "not contention" rather than
    assumed away, and the ``isinstance`` narrowing is what keeps that decision
    from resting on a value whose type nothing checked.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and code & 0xFF in _CONTENTION_RESULT_CODES


#: How :func:`_prepare` names what it was doing, for the one message both the
#: read and the write opener can publish. A phrase rather than a SQL statement
#: because ``_prepare`` catches around four pragmas *and* a ``SELECT`` and cannot
#: say which of them met the holder -- and because "preparing a connection" is
#: true of ``open_read_connection`` as well, which is the caller the write-shaped
#: wording used to misdescribe.
_PREPARING_A_CONNECTION: Final = "preparing a connection to it"


class WriteTransactionBusyError(TheurianError):
    """Another process holds the database, so this command could not use it.

    **The state database is intact. That is the whole point of this type.**
    Before it existed, the two ``(OSError, sqlite3.Error)`` backstops #484 added
    to ``migrate status`` and ``migrate apply`` caught a write conflict in the
    same net as a directory sitting at the database path, and answered both with
    the cure for the second: delete ``.theurian/state/`` and rebuild. Measured
    with a second connection holding ``BEGIN IMMEDIATE`` -- an operator's
    ``sqlite3`` shell, another tool, a process that exited with a transaction
    open -- both commands published ``database is locked`` beside an instruction
    to delete a state database that was in perfect condition, and both exited 0
    on the retry once the holder let go. An operator who follows that remedy
    destroys derived state for nothing; a scripted agent does it without reading.

    **The sibling of :class:`WriteLockTimeoutError`, one layer down.** That type
    reports contention for Theurian's *own* advisory lock and says to wait; this
    one reports contention for the database itself, which the advisory lock
    cannot mediate because a writer outside Theurian never takes it. Same family,
    same shape of cure, and neither one damages anything.

    Raised for the statements :func:`_open_transaction` issues on its own behalf
    and, since #484 round two, for contention met while :func:`_prepare` makes a
    connection usable. Each boundary is recorded where it is drawn.

    **The wording has to hold in a read context as well as a write one**, because
    :func:`_prepare` serves :func:`open_read_connection` too. Two earlier
    sentences did not. "Another *writer* holds it, so it is locked for writing"
    describes what a reader met only by accident, and "a writer outside Theurian
    never takes Theurian's lock" is plainly false on the read path: nothing there
    takes the advisory lock, so the holder blocking a reader can perfectly well be
    another Theurian command. What is true on both paths, and is what the message
    says, is that the holder is one *this command's* advisory write lock does not
    mediate -- which is the whole reason the conflict is visible at all.
    """

    def __init__(self, path: Path, during: str) -> None:
        self.remedy = (
            "Wait for the other writer to finish, then run the command again. "
            "`theurian migrate status` reports what is still pending once it has. "
            "Nothing is damaged and nothing needs rebuilding: a write conflict leaves "
            "the database exactly as it found it."
        )
        super().__init__(
            f"{path.name} is locked by another process, so {during} could not proceed. "
            f"The holder is one this command's advisory write lock does not mediate -- "
            f"another tool, an open `sqlite3` shell, a Theurian command reading the same "
            f"file, or a process that exited with a transaction open."
        )


class WriteLockUnusableError(TheurianError):
    """The write-lock path is a symbolic link, so taking the lock would write elsewhere.

    A lock file is a synchronisation artefact and nothing else: its bytes are
    never read, and no caller asks for it to be emptied. Acquiring the lock
    nevertheless opened it with ``"w"``, and that mode follows a symbolic link and
    ``O_TRUNC``s whatever the link names -- so a link at the lock path turned
    every writer's first act into a destructive write somewhere else, at exit 0
    with a success report (#481). Measured with a real CLI run: the tracked file
    ``test_migrate_apply_lock_confinement.py`` plants at the link's target went
    from its 49 bytes to 0. The number is that file's ``LOCK_LINK_VICTIM_BODY``
    and moves with it; what does not move is that the count was nonzero before
    and zero after.

    ``.theurian/runtime/`` is derived and git-ignored (ADR-0004), which is the
    same delivery :class:`~theurian.application.project_service.BuildProvenance`
    records for a doctored state database: a repository contributor can force-add
    past the ignore, so a clone carries the link and the first write through it
    truncates a file in the user's own tree.

    **Containment does not cover this and could not.**
    ``ProjectPaths.write_lock`` routes through ``_contain``, which refuses a path
    resolving *outside* the project root -- a link pointing at a file *inside* the
    root resolves inside it and passes untouched, which is exactly the shape that
    does the damage to a working tree.

    **Sets its own remedy** (the #205 rule): the cure is to remove a file, and no
    caller can infer that from the exception's type. It is a ``TheurianError``
    rather than the raw ``OSError`` the refusal arrives as, because
    ``migrate apply`` wraps its whole critical section in an ``except
    TheurianError`` -- a bare ``OSError`` there escapes ``--json`` as a Rich
    traceback with an empty machine channel, which is the reporting failure #483
    and #484 close on the state-database path.
    """

    def __init__(self, path: Path) -> None:
        self.remedy = (
            f"Remove the symbolic link at {path} and retry. It is derived state "
            f"(ADR-0004) that Theurian recreates, so nothing authored is lost -- and "
            f"a repository that carries one has committed it past that ignore."
        )
        super().__init__(
            f"The write lock at {path.name} is a symbolic link, not a lock file. Opening "
            f"it would write through the link to whatever it names, so Theurian refuses "
            f"to take the lock rather than touching that file."
        )


class StateDatabaseUnreadableError(TheurianError):
    """A stored value in this state database is not the value it claims to be.

    **Carries the failing exception's type and never its message.** Every
    converter the store reaches for puts the value it would not accept into the
    error it raises: `datetime.fromisoformat` quotes the string, each of the six
    enums quotes the member it could not find, and every domain value object --
    `MediaType`, `ContentHash`, `ItemId` -- renders its argument with ``!r``.
    Under corruption that value is whatever bytes were on the page, and the
    canonical store holds *every* revision, `draft` and `rejected` included
    (ADR-0006). So the message a caller receives had to stop being a function of
    the cell.

    Measured through ``build_server(registry).call_tool`` against a database
    built by the real CLI: overwriting any of `created_at`, `valid_from`,
    `content_type` or `status` published that cell verbatim to an MCP client,
    through both ``knowledge.get`` and ``knowledge.search`` -- eight of eight,
    with the driver's own text arriving as ``ToolError: Error executing tool
    knowledge.get: Invalid isoformat string: '<the cell>'``.

    **All of that is true of this exception and false of the class it belongs
    to.** Keeping the cell out of one message is not withholding it. The cause
    travels on ``__cause__`` by design, and Typer renders the whole chain, so
    until `f8d6e5d` the CLI printed one line below this message exactly what the
    constructor had just withheld -- six (command, column) positions: ``migrate
    status`` and ``migrate apply`` over `migration_history.migration_id`,
    `migration_history.checksum` and `schema_metadata.schema_version`. The
    boundary is the CLI's ``--json`` surface, where the message is the only thing
    rendered, and not this constructor. `c7d59b4` closed the same shape one site
    further out, at a second store session opened outside ``_run_build``'s
    conversion and reachable only by a build that indexed zero chunks.

    **The closure condition, stated so it can be checked: no ``TheurianError``
    escapes a ``--json`` command.** A property over all of them, not over the two
    that leaked -- enumerating the leaks is what left the second site standing
    behind the first. Checked over damaged-cell inputs in
    ``tests/integration/test_canonical_store_corruption.py``:
    `test_every_shipped_command_is_swept_or_excluded_with_a_reason` keeps the
    swept population a partition of the shipped app rather than a list someone
    maintains, `test_every_cli_failure_over_a_damaged_database_carries_a_remedy`
    goes RED on the missing `remedy` field an escape leaves behind -- a Rich
    traceback and no JSON document at all -- and
    `test_exactly_these_commands_notice_a_single_damaged_cell` stops both from
    passing vacuously.

    **The type name is the whole detail, including for `sqlite3`'s own errors.**
    :class:`~theurian.infrastructure.sqlite.index_store.IndexUnreadableError`
    passes `str(exc)` through for those on the grounds that a driver complaint is
    structural. It nearly always is. Measured on SQLite 3.51.2, damaging one
    `sqlite_master.sql` cell gives ``DatabaseError: malformed database schema
    (payroll_secret_band_l7) - incomplete input`` -- a name read straight out of
    the file -- so "nearly" is a case analysis over SQLite's error catalogue that
    the next release can invalidate. Enumerating what a broken file can say is
    the exact method that reopened this class twice; one rule that needs no
    enumeration replaces it.

    The cause travels by ``raise ... from``, so whoever holds the traceback still
    has the real exception with its real message. That is what pays for how wide
    :func:`_prepare` catches: a genuine programming error inside the guarded block
    now reports as a damaged database, and ``__cause__`` is the only thing left
    that names it correctly.

    Lives here rather than beside the store that raises it most, because opening
    a connection interprets this file too and :func:`write_transaction` opens one
    without going through the store at all -- see :func:`_prepare`.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"This project's state database cannot be read ({detail}): it is damaged, or "
            f"holds a value this build cannot interpret. A state database is derived and "
            f"git-ignored (ADR-0004), so delete `.theurian/state/` and run "
            f"`theurian migrate apply` to rebuild it from the Git-tracked migrations. "
            f"Nothing authored is lost."
        )


def _configure(connection: sqlite3.Connection) -> None:
    for pragma in CONNECTION_PRAGMAS:
        connection.execute(pragma)
    connection.row_factory = sqlite3.Row


def _prepare(connection: sqlite3.Connection, database_path: Path) -> None:
    """Make a fresh connection usable, answering for what the file may hold.

    Both of these lines interpret bytes that came out of the database.
    ``_configure`` runs the PRAGMA loop, where `sqlite3` decodes SQLite's own
    error text and a corrupt schema makes that text invalid UTF-8;
    ``_assert_schema_version`` runs a `SELECT` against a table that may be
    missing and then `int()`s the cell it finds.

    Called from both openers because the failure is a property of the file, not
    of the direction of travel. Measured with `schema_metadata.schema_version`
    overwritten by a sentinel: ``theurian index build`` -- which reads through
    the store, whose own guard covered it -- named a remedy, while ``migrate
    status`` and ``migrate apply`` raised ``ValueError: invalid literal for
    int() with base 10: '<the cell>'`` from here, uncaught, with the cell in the
    text and an empty JSON stdout.

    The scope stops short of ``BEGIN IMMEDIATE`` on purpose. Past that point a
    failure is the caller's statement against the caller's data -- a constraint
    violation, a conflicting write -- and reporting one of those as a damaged
    database would name the wrong cause and hand out a remedy that deletes the
    state.

    **The boundary this function draws is interpretation versus contention, not
    before-versus-after ``BEGIN IMMEDIATE``** (#484 round two). Reading it as the
    latter is what left contention converted here into a damaged-database
    verdict: both statements below run on the connection *before* the transaction
    opens, and both can meet another process holding the file. Two members were
    reproduced against the real CLI, each publishing the delete-your-state cure
    for a database in perfect condition:

    * a holder in ``PRAGMA locking_mode = EXCLUSIVE``, which the ``_configure``
      loop meets as ``database is locked``;
    * a database left in a rollback journal, where ``PRAGMA journal_mode = WAL``
      has to *change* the mode and takes the conflict itself.

    The predicate was already right -- :func:`_is_contention` returns ``True``
    for both -- and only its placement was wrong, so the classification moved
    ahead of the broad catch rather than being widened. What still reaches
    :class:`StateDatabaseUnreadableError` is what this function is actually for:
    bytes that cannot be interpreted, plus the permissions face (an unreadable
    file or an unwritable state directory), which is a different class and is
    filed as #530 rather than folded in here.
    """
    try:
        _configure(connection)
        _assert_schema_version(connection, database_path)
    except SchemaVersionMismatchError:
        # The header was interpreted *successfully* and said a number this build
        # does not support. It already names the same remedy family and carries
        # no cell, so re-wrapping it would replace a precise answer with a vague
        # one.
        raise
    except Exception as exc:
        # Ahead of the conversion, never inside it: a file another process is
        # holding is not a file this build cannot interpret, and the two have
        # opposite cures. Kept as one arm rather than a separate `except
        # sqlite3.Error` so there is still exactly one place that decides a
        # database is unreadable.
        if isinstance(exc, sqlite3.Error) and _is_contention(exc):
            raise WriteTransactionBusyError(database_path, _PREPARING_A_CONNECTION) from exc
        raise StateDatabaseUnreadableError(type(exc).__name__) from exc


def open_read_connection(database_path: Path) -> sqlite3.Connection:
    """Open a read connection, verifying the schema version first.

    Raises:
        SchemaVersionMismatchError: If the database was written by another build.
        StateDatabaseUnreadableError: If the file cannot be interpreted at all.
        FileNotFoundError: If the database does not exist.
    """
    if not database_path.exists():
        raise FileNotFoundError(f"No state database at {database_path}")

    # `mode=ro` so a read path cannot create or modify a database by accident --
    # a misconfigured caller then fails loudly instead of silently writing.
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, isolation_level=None)
    try:
        _prepare(connection, database_path)
    except Exception:
        connection.close()
        raise
    return connection


def create_database(database_path: Path, state_hash: str, engine_version: int) -> None:
    """Create and initialise a new state database.

    Raises:
        FileExistsError: If one already exists. Overwriting would silently
            discard a state another process may be reading.
    """
    if database_path.exists():
        raise FileExistsError(f"State database already exists: {database_path}")

    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        _configure(connection)
        connection.executescript(DDL)
        connection.execute(
            "INSERT INTO schema_metadata "
            "(id, schema_version, engine_version, state_hash, created_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (SCHEMA_VERSION, engine_version, state_hash, datetime.now(UTC).isoformat()),
        )
    finally:
        connection.close()


def _assert_schema_version(connection: sqlite3.Connection, path: Path) -> None:
    row = connection.execute("SELECT schema_version FROM schema_metadata WHERE id = 1").fetchone()
    if row is None:
        raise SchemaVersionMismatchError(path, found=0, expected=SCHEMA_VERSION)
    found = int(row["schema_version"])
    if not is_supported(found):
        raise SchemaVersionMismatchError(path, found=found, expected=SCHEMA_VERSION)


class WriteLock:
    """An OS advisory lock serialising writers across processes.

    A lock file rather than a PID file, for the reason in ADR-0002: PIDs are
    recycled, so a stale PID file can name a live unrelated process. An advisory
    lock is released by the kernel when the holder exits, however it exits.

    The file carries no content in either direction -- nothing writes bytes into
    it and nothing reads them out -- which is why :meth:`_open` neither truncates
    it nor follows a link at its path (#481).
    """

    def __init__(self, lock_path: Path, timeout: float = WRITE_LOCK_TIMEOUT_SECONDS) -> None:
        self._path = lock_path
        self._timeout = timeout

    @contextmanager
    def held(self) -> Iterator[None]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fileno = self._open()
        try:
            self._acquire(fileno)
            try:
                yield
            finally:
                fcntl.flock(fileno, fcntl.LOCK_UN)
        finally:
            os.close(fileno)

    def _open(self) -> int:
        """Open the lock file without following a link and without emptying it.

        ``os.open`` rather than ``Path.open("w")``, for two independent reasons
        that the mode string got wrong at once (#481):

        * **No ``O_TRUNC``.** The lock file's bytes carry no meaning to anything
          here, so clearing them was never a step this class needed -- and
          truncation is what turns a mis-aimed open into data loss rather than
          into a harmless one.
        * **``O_NOFOLLOW``, and the refusal is the ``open`` itself.** A
          ``Path.is_symlink()`` check ahead of the open would be a decision about
          a path taken before the open acts on it, and the window between the two
          is a window an attacker with write access to ``.theurian/runtime/``
          picks. ``O_NOFOLLOW`` moves the decision into the kernel's own
          resolution of this call, where there is no window to pick.

        POSIX mandates ``ELOOP`` when ``O_NOFOLLOW`` is set and the final
        component is a symbolic link, and that is what this platform returns
        (measured on macOS 26.6: errno 62).

        **Why an ``ELOOP`` reaching the translation below is the final component,
        stated the way the measurements actually came out.** An earlier version
        of this paragraph said the ``mkdir`` in :meth:`held` "walks that prefix
        first and fails there", which is false as an unqualified claim
        (round one, three independent measurements):

        * An **ordinary prefix link** is not refused by either call.
          ``Path.mkdir(parents=True, exist_ok=True)`` succeeds through a
          directory symlink -- ``exist_ok`` consults ``is_dir()``, which follows
          it -- and ``O_NOFOLLOW`` constrains the *final component only*, so the
          open follows the prefix too and no ``ELOOP`` arises at all. Measured
          both ways.
        * A **chain long enough to exhaust ``SYMLOOP_MAX``** does produce
          ``ELOOP`` at this open, from the prefix -- measured, so the guard is
          not what the old sentence claimed. It never gets here: ``mkdir`` runs
          first over that same prefix and fails, with ``EEXIST`` when the lock's
          own parent is the chain head (measured on a 64-link chain and on a
          two-link cycle, both ``EEXIST``, not ``ELOOP``) and with ``ELOOP``
          deeper in.
        * So the inference holds by *ordering* rather than by errno: ``mkdir``
          resolves exactly this open's prefix and returns first, leaving only the
          final component able to raise ``ELOOP`` here -- and ``O_NOFOLLOW``
          makes that one refuse without following.

        **The bound on that argument, recorded rather than closed.**
        ``O_NOFOLLOW``'s atomicity covers the final component, not the prefix,
        and ``mkdir`` and ``open`` are two calls: an attacker who rewrites a
        prefix component between them defeats the ordering above, and two writers
        racing that rewrite take locks on two different files rather than
        contending for one. The refusal also misdescribes itself in that race: an
        ``ELOOP`` produced by the rewritten *prefix* is translated below as a
        symbolic link at the final component, so the error names the lock file
        while the culprit is a directory above it. Closing both needs ``openat``
        against a directory descriptor opened with ``O_NOFOLLOW`` at every level,
        which this class does not do.

        **A contained prefix link relocates the lock, and that is allowed.** With
        ``.theurian/runtime`` a symlink to another directory *inside* the tree,
        containment passes -- it is not an escape -- and the lock file is created
        at the link's target: measured, ``migrate apply`` exits 0 with a
        zero-byte ``write.lock`` there. Nothing is destroyed, and mutual
        exclusion survives because every process follows the same link to the
        same file. It is a relocation, not the truncation #481 is about.

        Every other ``OSError`` is left exactly as it was -- an unwritable
        ``.theurian/`` and a directory at the lock path both still raise the same
        errno to the same handlers, since neither is this class and neither has
        this class's cure.
        """
        try:
            # `O_WRONLY`, not `O_RDWR`: `flock` locks the open file description
            # whatever its access mode, and nothing reads these bytes -- while
            # `O_RDWR` would refuse a lock file left at mode 0200, which
            # `Path.open("w")` opened without complaint (measured: EACCES against
            # a success). Keeping the request no wider than the old one is what
            # makes the sentence above about every other `OSError` true.
            #
            # 0o600 applies **only when this call creates the file**; a lock file
            # that already exists keeps the mode it was created with, including
            # the umask-derived 0o644 an earlier build left behind. Nothing here
            # chmods a file it did not create.
            return os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            raise WriteLockUnusableError(self._path) from exc

    def _acquire(self, fileno: int) -> None:
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise WriteLockTimeoutError(self._path, self._timeout) from None
                # Poll rather than block, so the timeout is honoured. 50 ms is
                # imperceptible next to a migration run and costs nothing.
                time.sleep(0.05)


def _open_transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
    """The transaction body itself, with no opinion about the lock.

    Split out of :func:`write_transaction` (#468) so a caller that already
    holds ``lock_path`` -- ``migrate apply``'s single critical section, which
    now spans creation, this transaction, the provenance record and the
    pointer publish as one hold -- can run the identical connect/BEGIN
    IMMEDIATE/commit-or-rollback sequence without a second, nested
    acquisition of the same lock file. A process re-``open()``-ing and
    ``flock``-ing a lock file it is already holding self-blocks for the full
    timeout: ``flock`` locks an *open file description*, not a process or a
    path, so two separate ``os.open()`` calls from the same process are two
    separate descriptions that contend with each other exactly as two
    processes would.

    **Contention on the two statements this function issues itself is
    converted; the caller's own statements are not.** (Contention met *earlier*,
    while :func:`_prepare` makes the connection usable, is converted there --
    #484 round two; this paragraph is about the statements below it.) ``BEGIN IMMEDIATE`` and
    ``COMMIT`` are this function's, made on its own behalf, so a ``SQLITE_BUSY``
    arriving from either can say one thing only: another writer holds the
    database. That is a transient condition over an undamaged file, and left as
    a raw ``sqlite3.OperationalError`` it reached the CLI's ``(OSError,
    sqlite3.Error)`` backstops in the same net as a directory at the database
    path -- and was answered with the same delete-your-state cure (#484 round
    two).

    **Only one of the two arms is reached by anything that has been measured,
    and the other is kept deliberately** (pre-round-two sweep; measured
    2026-09-03 on SQLite 3.47.1). ``BEGIN IMMEDIATE`` is the driven case: primary
    ``SQLITE_BUSY``, reproduced across two OS processes and covered by
    ``test_a_transient_write_conflict_is_never_answered_by_deleting_the_state``.
    The ``COMMIT`` arm is believed **unreachable under this product's own
    ``journal_mode = WAL``** -- an attempt to force COMMIT-time contention with a
    live reader committed silently under WAL and reported ``SQLITE_BUSY`` only
    under ``journal_mode = delete``. It is kept for that rollback-journal edge
    and for robustness if the pragma ever changes, and it is recorded here as an
    arm no test drives rather than presented as a second measured path.

    The boundary is exactly the one :func:`_prepare` records for itself and for
    the same reason, read from the other side: past ``BEGIN IMMEDIATE`` a
    failure is "the caller's statement against the caller's data", which this
    function must not reinterpret. So the conversion wraps the two statements
    and not the ``yield`` between them -- a busy arriving from the caller's own
    write keeps travelling as the driver raised it.

    **That residue is believed unreachable under WAL, and the claim is scoped the
    same way the ``COMMIT`` arm's is.** Under ``journal_mode = WAL`` a connection
    that has completed ``BEGIN IMMEDIATE`` owns the database's write lock, so no
    later statement of the caller's can be refused for contention; and
    ``SQLITE_BUSY_SNAPSHOT``, the one contention code that arises past that point
    in general, needs a *deferred* read upgrading to a write, which this function
    never opens (:data:`_CONTENTION_RESULT_CODES` records that measurement).
    Under a rollback journal the argument is weaker and is **not** claimed
    closed: ``BEGIN IMMEDIATE`` takes only ``RESERVED`` there, and a
    mid-transaction cache spill has to upgrade to ``EXCLUSIVE``, which a live
    reader can refuse. That neighbour is unproven in both directions -- nothing
    here has produced it and nothing here rules it out -- and it is stated rather
    than folded into the WAL claim.
    """
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        _prepare(connection, database_path)
        _execute_own(connection, "BEGIN IMMEDIATE", database_path)
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            _execute_own(connection, "COMMIT", database_path)
    finally:
        connection.close()


def _execute_own(connection: sqlite3.Connection, statement: str, database_path: Path) -> None:
    """Run one of :func:`_open_transaction`'s own statements, naming contention.

    ``ROLLBACK`` deliberately does not come through here. It runs only while an
    exception is already travelling, and replacing that exception with a report
    about the rollback would hide the failure the caller actually needs.

    **That exclusion is a recorded decision, not a pinned one.** Routing
    ``ROLLBACK`` through here as well would be indistinguishable from this shape
    under everything the suite and the pre-round-two sweep ran: no input was
    found that makes ``ROLLBACK`` fail while a caller's exception is in flight,
    so nothing goes RED on the difference. Stated so the next reader knows the
    reasoning is argued rather than measured.
    """
    try:
        connection.execute(statement)
    except sqlite3.Error as exc:
        if not _is_contention(exc):
            raise
        raise WriteTransactionBusyError(database_path, f"this transaction's `{statement}`") from exc


@contextmanager
def write_transaction(
    database_path: Path, lock_path: Path, *, already_locked: bool = False
) -> Iterator[sqlite3.Connection]:
    """Open an exclusive write transaction.

    The lock-holding write path: an OS advisory lock is taken on ``lock_path``
    and held for the duration of the transaction, so two processes that both
    enter here serialise. A caller that writes without entering is outside that
    guarantee -- ``CanonicalStore`` publishes its write methods directly, so
    exclusivity is held by convention at each call site (ADR-0018, amended in
    Milestone 5).

    ``already_locked`` is ``False`` for every caller except ``migrate apply``'s
    own composition-root code (#468), which acquires ``lock_path`` itself
    *before* calling in here, to hold it across writes this function does not
    otherwise see (database creation, the provenance record, the pointer
    publish). Passing ``True`` there skips this function's own acquisition --
    the caller already holds it -- and is a caller's assertion, not something
    this function verifies: passing ``True`` without actually holding the lock
    is a caller bug, the same class as writing through ``CanonicalStore``
    outside ``write_transaction`` at all. Every other caller -- the
    standalone ``migrate status`` read-modify path, ``propose accept``'s
    rehearsal against a throwaway database and lock file, any future direct
    caller -- takes the default and gets the same self-contained acquisition
    this function has always done.

    NFR-8: no external I/O inside. Read and hash content files *before* entering.

    Raises:
        StateDatabaseUnreadableError: If the file cannot be interpreted far
            enough to start a transaction. Raised before ``BEGIN IMMEDIATE`` and
            never after it -- see :func:`_prepare`.
        WriteLockTimeoutError: If another holder keeps the advisory lock on
            ``lock_path`` past ``WRITE_LOCK_TIMEOUT_SECONDS``, the default this
            path takes. Raised before the database is opened, so no transaction
            has begun -- see :meth:`WriteLock._acquire`. Never raised when
            ``already_locked`` is ``True``, since no acquisition happens here.
        WriteLockUnusableError: If ``lock_path`` is a symbolic link, which taking
            the lock would otherwise write through (#481). Raised in the same
            place and never when ``already_locked`` is ``True``, for the same
            reason -- see :meth:`WriteLock._open`.
        WriteTransactionBusyError: If another writer holds the database itself,
            which the advisory lock cannot mediate. Raised whatever
            ``already_locked`` says, since it comes from the transaction and not
            from the lock -- see :func:`_open_transaction`.
    """
    if already_locked:
        yield from _open_transaction(database_path)
        return
    with WriteLock(lock_path).held():
        yield from _open_transaction(database_path)
