"""SQLite adapter for the canonical review-finding store (ADR-0029, ADR-0004).

Implements :class:`~theurian.domain.ports.review_finding_store.ReviewFindingStore`
over a ``theurian-findings-*.sqlite`` file. Modelled on
:class:`~theurian.infrastructure.sqlite.index_store.SqliteIndexStore`'s connection
handling: ``sqlite3.connect`` as a context manager commits but does not close, so
writes go through :func:`contextlib.closing` with an explicit commit, and reads
open a ``mode=ro`` connection that will not conjure a missing file.

**One write, and it is a wholesale projection of git history.** :meth:`replace_all`
is the only mutation. It rebuilds the file from empty every time -- deleting any
prior file first -- so the schema is always current after a rebuild (a stale
:data:`~theurian.infrastructure.sqlite.findings_schema.FINDINGS_SCHEMA_VERSION`
cannot survive one), the rows are exactly the load's, and two rebuilds over one
load leave a logically identical store (AC-2). Its sole *shipped* caller feeds it
a ``FindingLoad`` a git source resolved -- but that is a fact about who calls it,
not a structural guarantee this adapter enforces: :meth:`replace_all` accepts any
``FindingLoad``, including one built from a fabricated ``commit_sha``, because
neither this adapter nor its port verifies commit provenance (see
:class:`~theurian.domain.ports.review_finding_store.ReviewFindingStore`'s port
docstring for the measured detail).

**No serving read.** The reads are two metadata lookups and one whole-table
verification dump in a fixed order -- never a query-by-content. A findings search
is a later slice with its own disclosure round.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.errors import TheurianError
from theurian.domain.ports.review_finding_store import (
    FindingsDump,
    FindingsStamp,
    StoredFinding,
    StoredRejection,
)
from theurian.domain.review_finding import PARSER_STAMP, FindingLoad, RejectedTrailer, ReviewFinding
from theurian.infrastructure.sqlite.findings_schema import FINDINGS_DDL, FINDINGS_SCHEMA_VERSION
from theurian.infrastructure.sqlite.schema import CONNECTION_PRAGMAS, read_only_uri

#: One inserted findings row: the eleven columns of the ``findings`` table, in
#: order. Named so the insert statement and the row builder cannot drift on arity.
_FindingRow = tuple[str, int, str, str, str, str, str, str, int | None, str | None, str | None]

#: One inserted rejected row: ``(commit_sha, position, raw_line, reason)``.
_RejectedRow = tuple[str, int, str, str]

_INSERT_FINDING: Final = (
    "INSERT INTO findings (commit_sha, position, reviewer, severity, finding_text, "
    "provider, source_uri, committed_at, pull_request, family, specialist) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_REJECTED: Final = (
    "INSERT INTO rejected_trailers (commit_sha, position, raw_line, reason) VALUES (?, ?, ?, ?)"
)
_INSERT_METADATA: Final = (
    "INSERT INTO findings_metadata (id, findings_schema_version, parser_stamp, built_at) "
    "VALUES (1, ?, ?, ?)"
)


#: The read-path remedy: the store is a projection of git history (ADR-0004), so
#: the cure for a damaged or stale *file* is to rebuild it, never to repair it in
#: place.
_REBUILD_REMEDY: Final = "Run `theurian findings build` to rebuild the store from git history."

#: The write-path remedy. It names the actual cause -- an unwritable
#: ``.theurian/state`` directory or a full disk -- first, and only then, as its
#: second clause, tells the caller to retry `theurian findings build`. Leading
#: with the retry alone would be circular: a write failure means the rebuild
#: command itself could not finish, so "just re-run it" is no cure by itself;
#: naming the precondition to fix first is what makes the retry meaningful.
_WRITE_REMEDY: Final = (
    "Check that .theurian/state is writable and there is free disk space, then "
    "retry `theurian findings build`."
)


class FindingsStoreError(TheurianError):
    """The review-finding store could not be written or read.

    The remedy differs by which side failed. A **write**-path failure (the store
    could not be created or replaced) carries :data:`_WRITE_REMEDY`. A **read**-path
    failure (the file exists but its content is damaged, stale, or otherwise
    unreadable) carries the default :data:`_REBUILD_REMEDY`: the cure for a damaged
    projection is to reconstruct it from source, never to repair it in place.
    """

    def __init__(self, detail: str, *, remedy: str = _REBUILD_REMEDY) -> None:
        self.remedy = remedy
        super().__init__(f"The review-finding store could not be used ({detail}).")


def _finding_rows(accepted: tuple[ReviewFinding, ...]) -> list[_FindingRow]:
    """Project accepted findings to insertable rows, assigning the position key.

    ``position`` is the finding's ordinal *within its commit*, in the source's
    total order -- so several findings on one commit stay distinct and stably
    ordered, and a rebuild over unchanged history assigns the same positions (AC-2).
    """
    counters: dict[str, int] = {}
    rows: list[_FindingRow] = []
    for finding in accepted:
        position = counters.get(finding.commit_sha, 0)
        counters[finding.commit_sha] = position + 1
        rows.append(
            (
                finding.commit_sha,
                position,
                finding.reviewer.value,
                finding.severity.value,
                finding.finding_text,
                finding.provider,
                finding.anchor.source_uri,
                finding.date.isoformat(),
                finding.pull_request,
                finding.family,
                finding.specialist,
            )
        )
    return rows


def _rejected_rows(rejected: tuple[RejectedTrailer, ...]) -> list[_RejectedRow]:
    """Project rejected trailers to insertable rows, assigning the position key.

    ``raw_line`` is copied through verbatim and never inspected: it is inert,
    author-controlled, untrusted commit text (ADR-0029 D3, refinement B).
    """
    counters: dict[str, int] = {}
    rows: list[_RejectedRow] = []
    for entry in rejected:
        position = counters.get(entry.commit_sha, 0)
        counters[entry.commit_sha] = position + 1
        rows.append((entry.commit_sha, position, entry.raw_line, entry.reason))
    return rows


@final
class SqliteReviewFindingStore:
    """Writes and reads one wholesale-rebuilt review-finding store."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def replace_all(self, load: FindingLoad) -> None:
        """Rebuild the store to hold exactly ``load``, wholesale and idempotently.

        The file is recreated from empty each call -- any prior file is unlinked
        first -- so the schema is always current afterwards and the row set is
        exactly the load's. The findings, the rejected trailers and the stamp are
        written in one transaction: a crash before it commits leaves no stamp row
        rather than publishing a half-written store as valid. That absent row would
        read as "not current" through :meth:`is_current` -- though no shipped path
        checks it today (see that method's own docstring) -- and in any case the
        next call to this method overwrites the file wholesale regardless of what
        it finds, unconditional rebuild being how this slice's one writer already
        behaves.

        The whole operation, including directory creation and the unlink, runs
        inside one ``try``: an earlier cut left ``mkdir``/``unlink`` outside it,
        catching only ``sqlite3.Error``, so a ``PermissionError`` on either escaped
        as a raw traceback past every ``TheurianError`` handler above this adapter
        (the same shape ``project_service.index_for`` converts ``(ValueError,
        OSError)`` for). Both ``OSError`` and ``sqlite3.Error`` convert here.

        **Not yet atomic across a reader.** ``index build`` writes under a
        ``.building`` working name and calls ``os.replace`` into the completed
        name only once the build is whole (``cli/index_commands.py``), so a
        concurrent reader never observes a partially written index file. This
        method instead
        unlinks the live path and writes the replacement in place, so a reader
        that opens the file mid-``replace_all`` can observe a missing file or a
        file with the new schema but not yet all its rows. Deliberately not fixed
        here -- this slice ships no reader that races a build (AC-7: nothing
        serves from this store yet) -- and adopting the same working-name
        discipline is tracked as its own follow-up issue rather than folded in.
        """
        finding_rows = _finding_rows(load.accepted)
        rejected_rows = _rejected_rows(load.rejected)
        stamped_at = datetime.now(UTC).isoformat()

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Wholesale: unlink the main file first, so no earlier schema or row in
            # it can survive. This does not remove a `-wal`/`-shm` sibling left by a
            # killed prior connection -- `sqlite3.connect` below recreates and
            # reconciles those itself, so no row from them resurfaces (measured);
            # the invariant is "no earlier schema or row reachable from this
            # connection", not "no earlier bytes anywhere on disk".
            self._path.unlink(missing_ok=True)
            with closing(sqlite3.connect(self._path)) as connection:
                for pragma in CONNECTION_PRAGMAS:
                    connection.execute(pragma)
                # `executescript` commits any pending transaction, so the DDL lands
                # before the data transaction the inserts and stamp share below.
                connection.executescript(FINDINGS_DDL)
                connection.executemany(_INSERT_FINDING, finding_rows)
                connection.executemany(_INSERT_REJECTED, rejected_rows)
                connection.execute(
                    _INSERT_METADATA, (FINDINGS_SCHEMA_VERSION, PARSER_STAMP, stamped_at)
                )
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            raise FindingsStoreError(
                f"writing {self._path.name}: {exc}", remedy=_WRITE_REMEDY
            ) from exc

    def stamp(self) -> FindingsStamp | None:
        """The recorded (schema version, parser stamp), or ``None`` if unreadable.

        A missing file, a missing metadata row, an unreadable one, or an OS-level
        failure merely checking whether the file exists (an untraversable parent
        directory raises ``PermissionError`` from :meth:`Path.exists`, which does
        not treat every ``OSError`` as "missing") all answer ``None`` -- each means
        the same thing to a staleness check: there is no trustworthy stamp, so a
        rebuild is owed. A corrupt file is *not* raised here for that reason;
        :meth:`dump`, which promises real content, is where a damaged store becomes
        loud.
        """
        try:
            exists = self._path.exists()
        except OSError:
            return None
        if not exists:
            return None
        try:
            with self._read() as connection:
                row = connection.execute(
                    "SELECT findings_schema_version, parser_stamp FROM findings_metadata "
                    "WHERE id = 1"
                ).fetchone()
        except (sqlite3.Error, OSError):
            return None
        if row is None:
            return None
        return FindingsStamp(
            findings_schema_version=int(row["findings_schema_version"]),
            parser_stamp=str(row["parser_stamp"]),
        )

    def is_current(self) -> bool:
        """Whether the recorded stamp matches the build that would rebuild it now.

        ``False`` for a missing, stale-schema, or stale-parser store. The parser
        stamp and the schema version are independent forcing functions; either
        mismatch is staleness. The signal exists for the consumer that arrives with
        the serving slice: **no shipped caller reads it today** (verified over the
        shipped package, 2026-08-28 -- the only production call to
        :meth:`is_current`, :meth:`stamp`, or :meth:`dump` is this method's own use
        of :meth:`stamp` below). The one shipped writer, ``findings build``,
        rebuilds wholesale on every run regardless of staleness -- strictly
        stronger than staleness-checking, not weaker -- so nothing in this slice
        needs this signal to decide whether to rebuild.
        """
        recorded = self.stamp()
        return (
            recorded is not None
            and recorded.findings_schema_version == FINDINGS_SCHEMA_VERSION
            and recorded.parser_stamp == PARSER_STAMP
        )

    def dump(self) -> FindingsDump:
        """Every stored row in ``(commit_sha, position)`` order, for verification.

        Not a serving read: it takes no content predicate and returns the whole
        store, so a test can assert the projection equals its git source. A missing
        store dumps empty; a damaged or otherwise unreadable one -- including an
        OS-level failure merely checking whether the file exists, since
        :meth:`Path.exists` does not treat every ``OSError`` as "missing", and a
        file whose schema committed but whose data transaction never did -- raises
        rather than returning a partial dump that would read as a smaller-but-valid
        corpus.

        ``replace_all``'s ``executescript`` commits the DDL before the data
        transaction that lands the rows and the metadata row share (see its
        docstring): a crash in that window leaves empty, well-formed tables and no
        metadata row. Without a guard, that file dumps as ``FindingsDump((), ())`` --
        indistinguishable from a genuinely empty store -- so the metadata row's
        presence is checked first, and its absence raises rather than answering
        empty.
        """
        try:
            exists = self._path.exists()
        except OSError as exc:
            raise FindingsStoreError(f"reading {self._path.name}: {exc}") from exc
        if not exists:
            return FindingsDump(findings=(), rejected=())
        try:
            with self._read() as connection:
                metadata_row = connection.execute(
                    "SELECT 1 FROM findings_metadata WHERE id = 1"
                ).fetchone()
                if metadata_row is None:
                    raise FindingsStoreError(
                        f"{self._path.name} has no metadata row -- the file was left "
                        "half-built by a rebuild that crashed after its schema "
                        "committed but before its data transaction did"
                    )
                finding_rows = connection.execute(
                    "SELECT commit_sha, position, reviewer, severity, finding_text, provider, "
                    "source_uri, committed_at, pull_request, family, specialist FROM findings "
                    "ORDER BY commit_sha, position"
                ).fetchall()
                rejected_rows = connection.execute(
                    "SELECT commit_sha, position, raw_line, reason FROM rejected_trailers "
                    "ORDER BY commit_sha, position"
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            raise FindingsStoreError(f"reading {self._path.name}: {exc}") from exc
        return FindingsDump(
            findings=tuple(_stored_finding(row) for row in finding_rows),
            rejected=tuple(_stored_rejection(row) for row in rejected_rows),
        )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """A read-only connection that will not create the file it cannot find.

        ``mode=ro`` so a query never conjures an empty database at a path whose file
        is gone -- the defect `index_store._open_read` records. The caller has
        already checked the file exists; this refuses to write through it anyway.
        """
        connection = sqlite3.connect(read_only_uri(self._path), uri=True)
        try:
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()


def _stored_finding(row: sqlite3.Row) -> StoredFinding:
    pull_request = row["pull_request"]
    family = row["family"]
    specialist = row["specialist"]
    return StoredFinding(
        commit_sha=str(row["commit_sha"]),
        position=int(row["position"]),
        reviewer=str(row["reviewer"]),
        severity=str(row["severity"]),
        finding_text=str(row["finding_text"]),
        provider=str(row["provider"]),
        source_uri=str(row["source_uri"]),
        committed_at=str(row["committed_at"]),
        pull_request=None if pull_request is None else int(pull_request),
        family=None if family is None else str(family),
        specialist=None if specialist is None else str(specialist),
    )


def _stored_rejection(row: sqlite3.Row) -> StoredRejection:
    return StoredRejection(
        commit_sha=str(row["commit_sha"]),
        position=int(row["position"]),
        raw_line=str(row["raw_line"]),
        reason=str(row["reason"]),
    )
