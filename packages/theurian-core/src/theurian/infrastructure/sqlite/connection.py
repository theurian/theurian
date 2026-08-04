"""Connection management and the single-writer guarantee (ADR-0018, NFR-7).

Reads use independent WAL connections. Writes go through one interface holding
an OS advisory lock, so two concurrent processes serialise rather than corrupt.
Milestone 3 replaces the lock with a daemon-owned queue without changing the
interface.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Final

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
    """Another process held the write lock for too long."""

    def __init__(self, path: Path, seconds: float) -> None:
        super().__init__(
            f"Could not acquire the write lock on {path.name} within {seconds:.0f}s. "
            f"Another Theurian process is writing. If none is running, remove {path}."
        )


class StateDatabaseUnreadableError(TheurianError):
    """A stored value in this state database is not the value it claims to be.

    **Carries the failing exception's type and never its message, which is the
    whole of this class.** Every converter the store reaches for puts the value
    it would not accept into the error it raises: `datetime.fromisoformat` quotes
    the string, each of the six enums quotes the member it could not find, and
    every domain value object -- `MediaType`, `ContentHash`, `ItemId` -- renders
    its argument with ``!r``. Under corruption that value is whatever bytes were
    on the page, and the canonical store holds *every* revision, `draft` and
    `rejected` included (ADR-0006). So the message a caller receives had to stop
    being a function of the cell.

    Measured through ``build_server(registry).call_tool`` against a database
    built by the real CLI: overwriting any of `created_at`, `valid_from`,
    `content_type` or `status` published that cell verbatim to an MCP client,
    through both ``knowledge.get`` and ``knowledge.search`` -- eight of eight,
    with the driver's own text arriving as ``ToolError: Error executing tool
    knowledge.get: Invalid isoformat string: '<the cell>'``.

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
    has the real exception with its real message.

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
    """

    def __init__(self, lock_path: Path, timeout: float = WRITE_LOCK_TIMEOUT_SECONDS) -> None:
        self._path = lock_path
        self._timeout = timeout

    @contextmanager
    def held(self) -> Iterator[None]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("w")
        try:
            self._acquire(handle)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _acquire(self, handle: IO[str]) -> None:
        deadline = time.monotonic() + self._timeout
        fileno = handle.fileno()
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


@contextmanager
def write_transaction(database_path: Path, lock_path: Path) -> Iterator[sqlite3.Connection]:
    """Open an exclusive write transaction.

    The only way to write. ``CanonicalStore`` exposes no connection, so the
    single-writer guarantee lives in one place and can change mechanism in
    Milestone 3 without touching application code (ADR-0018).

    NFR-8: no external I/O inside. Read and hash content files *before* entering.

    Raises:
        StateDatabaseUnreadableError: If the file cannot be interpreted far
            enough to start a transaction. Raised before ``BEGIN IMMEDIATE`` and
            never after it -- see :func:`_prepare`.
    """
    lock = WriteLock(lock_path)
    with lock.held():
        connection = sqlite3.connect(database_path, isolation_level=None)
        try:
            _prepare(connection, database_path)
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()
