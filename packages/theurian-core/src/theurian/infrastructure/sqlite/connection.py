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


def _configure(connection: sqlite3.Connection) -> None:
    for pragma in CONNECTION_PRAGMAS:
        connection.execute(pragma)
    connection.row_factory = sqlite3.Row


def open_read_connection(database_path: Path) -> sqlite3.Connection:
    """Open a read connection, verifying the schema version first.

    Raises:
        SchemaVersionMismatchError: If the database was written by another build.
        FileNotFoundError: If the database does not exist.
    """
    if not database_path.exists():
        raise FileNotFoundError(f"No state database at {database_path}")

    # `mode=ro` so a read path cannot create or modify a database by accident --
    # a misconfigured caller then fails loudly instead of silently writing.
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, isolation_level=None)
    try:
        _configure(connection)
        _assert_schema_version(connection, database_path)
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
    """
    lock = WriteLock(lock_path)
    with lock.held():
        connection = sqlite3.connect(database_path, isolation_level=None)
        try:
            _configure(connection)
            _assert_schema_version(connection, database_path)
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
