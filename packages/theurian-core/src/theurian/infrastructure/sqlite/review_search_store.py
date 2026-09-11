"""SQLite adapter for the review search store (ADR-0030 decisions 3 and 6, ADR-0004).

Writes and reads one ``theurian-review-*.sqlite`` file: the **derived** projection
of the evidence files under ``.theurian/review/``. Modelled on
:class:`~theurian.infrastructure.sqlite.findings_store.SqliteReviewFindingStore`'s
connection handling, for the reason that module records: ``sqlite3.connect`` as a
context manager commits but does not close, so writes go through
:func:`contextlib.closing` with an explicit commit and reads open a ``mode=ro``
connection that will not conjure a missing file.

**Every value a caller supplies is a bound parameter.** No repository, no author,
no file path and no search text reaches a *statement*: the only text concatenated
into one is text :mod:`theurian.infrastructure.sqlite.review_search_sql` wrote,
which is where the statements live and where that claim is checkable in a single
read. This module issues them and binds the values -- the discipline
``index_store``'s retriever states for its own ``f``-strings.

**Non-ranked, and structurally so.** There is no FTS5 table behind this store, no
tokenizer and no score: :meth:`SqliteReviewSearchStore.search` is a filtered,
ordered ``SELECT`` whose text predicate is a ``LIKE`` over a literally-escaped
pattern. ``*``, ``OR``, ``NEAR`` and ``"`` are therefore ordinary characters with
no operator meaning anywhere on this path, and ``%`` and ``_`` are escaped before
the pattern is bound. What that buys beyond correctness is the T-17a constraint
ADR-0030 decision 6 inherits: a ranked surface would price its results over
collection statistics computed at build time, which a filter does not clean and a
tombstone does not move, and no such statistic exists here.

**A withheld record has no row to reach.** Withholding happens one layer up, in
:class:`~theurian.application.review_search_builder.ReviewSearchBuilder`, by not
handing the record to :meth:`SqliteReviewSearchStore.replace_all` at all -- so
there is nothing here that could distinguish "withheld" from "never existed", in
any table, through any read. This module has no column, no predicate and no
refusal that knows the difference, which is what makes the indistinguishability a
property of the schema rather than of a filter someone has to keep correct.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.errors import DomainError, TheurianError
from theurian.domain.review_search import (
    ReviewSearchHit,
    ReviewSearchLoad,
    ReviewSearchQuery,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
)
from theurian.infrastructure.review_evidence.layout import EVIDENCE_FORMAT_VERSION
from theurian.infrastructure.sqlite.review_search_schema import (
    REVIEW_SEARCH_DDL,
    REVIEW_SEARCH_SCHEMA_VERSION,
)
from theurian.infrastructure.sqlite.review_search_sql import (
    DUMP_COLUMNS,
    INSERT_METADATA,
    INSERT_PARTICIPANT,
    INSERT_RECORD,
    INSERT_TEXT,
    SEARCH_ORDER,
    SELECT_STAMP,
    excerpt_columns,
    fragment_pattern,
    participant_rows,
    record_rows,
    text_rows,
    where_clause,
)
from theurian.infrastructure.sqlite.schema import (
    CONNECTION_PRAGMAS,
    irregular_shape_at,
    read_only_uri,
)

#: The read-path remedy. The store is derived from the evidence files (ADR-0030
#: decision 3), which are still on disk, so the cure for a damaged, stale or
#: missing store is to rebuild it -- never to repair it in place, and never to go
#: looking for lost evidence.
_REBUILD_REMEDY: Final = (
    "Run `theurian review build` to rebuild the search store from the evidence "
    "files under .theurian/review/."
)

#: The write-path remedy. It names the actual precondition -- an unwritable
#: ``.theurian/state`` directory or a full disk -- first, and only then, as its
#: second clause, tells the caller to retry. Leading with the retry alone would be
#: circular: a write failure means the rebuild itself could not finish, so "just
#: re-run it" is no cure by itself. The same split ``FindingsStoreError`` makes.
_WRITE_REMEDY: Final = (
    "Check that .theurian/state is writable and there is free disk space, then "
    "retry `theurian review build`."
)


class ReviewSearchStoreError(TheurianError):
    """The review search store could not be written or read.

    The remedy differs by which side failed. A **write**-path failure (the store
    could not be created or replaced) carries :data:`_WRITE_REMEDY`, which names
    the precondition to fix before the retry. A **read**-path failure -- the file
    is missing, damaged, stale, or otherwise unreadable -- carries the default
    :data:`_REBUILD_REMEDY`: the cure for a damaged projection is to reconstruct
    it from source, and the source is still there.
    """

    def __init__(self, detail: str, *, remedy: str = _REBUILD_REMEDY) -> None:
        self.remedy = remedy
        super().__init__(f"The review search store could not be used ({detail}).")


#: The working name a rebuild assembles under before publishing (#404). The same
#: suffix ``index build``, the purge and ``findings build`` use, so a reader of
#: ``.theurian/state/`` sees one convention for "a writer has not finished here".
_BUILDING_SUFFIX: Final = ".building"

#: SQLite's two companion files. A database is these three names, so anything that
#: removes one removes all three or leaves a write-ahead log paired with a
#: database that never wrote it.
_SIDECAR_SUFFIXES: Final = ("-wal", "-shm")


def _unlink_sidecars(path: Path) -> None:
    """Remove ``path``'s ``-wal``/``-shm`` companions, leaving ``path`` itself."""
    for suffix in _SIDECAR_SUFFIXES:
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _unlink_with_sidecars(path: Path) -> None:
    """Remove a database and both its companions, so nothing of it survives."""
    path.unlink(missing_ok=True)
    _unlink_sidecars(path)


@final
class SqliteReviewSearchStore:
    """Writes and reads one wholesale-rebuilt review search store."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    @property
    def building_path(self) -> Path:
        """Where :meth:`replace_all` assembles the next store before publishing it.

        A sibling of the published path, named by suffix, so it is contained by
        whatever contained that path: ``ProjectPaths.review_search_for`` proves
        the publish name sits inside ``.theurian/state/``, and a name derived from
        it by appending cannot leave that directory the way a separately-composed
        working path could (#237, SEC-7). Exposed so a test can assert what a
        failed build left behind, not because any caller needs to name it.
        """
        return self._path.with_name(self._path.name + _BUILDING_SUFFIX)

    def replace_all(self, load: ReviewSearchLoad) -> None:
        """Rebuild the store to hold exactly ``load``, wholesale and idempotently.

        The one write. The file is built from empty each call and published by
        rename, so the schema is always current afterwards, the row set is exactly
        the load's, and two rebuilds over one load leave a logically identical
        store. A record the load does not carry has no row in any table --
        which is what makes the builder's withholding physical rather than a
        filter this store would have to remember.

        **Published by rename, never written under the live name** (#404). The
        build assembles at :attr:`building_path` and ``os.replace`` moves it onto
        the publish name once it is whole, so a concurrent reader observes the
        previous store or the new one and never a half-written file. The old
        database's ``-wal``/``-shm`` are reaped *before* the rename, not after:
        after it, the publish name would briefly hold the new main file beside the
        previous one's log -- a database plus a foreign journal, which a reader
        opening then reads as neither store. The reasoning in full, and the
        measurement behind it, is on
        :meth:`~theurian.infrastructure.sqlite.findings_store.SqliteReviewFindingStore.replace_all`.

        **``os.replace`` is atomic only within a filesystem**, which is why the
        working name is a sibling rather than a path under a temporary directory:
        both sit in ``.theurian/state/`` by construction, so the rename cannot
        degrade into a cross-device copy.

        The three content tables are written in one transaction with the stamp, so
        a crash before it commits leaves no stamp row rather than publishing a
        half-populated store as valid. Records go in before participants and
        texts, which the foreign keys require and ``PRAGMA foreign_keys = ON``
        enforces.

        **Serialisation is the caller's, and it is needed.** Two processes
        assembling at the same working name would corrupt each other, so
        ``review build`` holds ``ProjectPaths.write_lock`` across this whole call.
        This adapter does not take the lock itself: it is handed a path, not a
        project, and a lock acquired here could not extend to the caller's own
        critical section -- the mistake #468 records.

        Raises:
            ReviewSearchStoreError: For every failure of this write, whatever its
                Python type. The arm is deliberately the **complement** of what
                the CLI already grades rather than a list of families: the
                observable is that ``theurian review build`` publishes a graded
                refusal and never a traceback, the CLI's catch is ``except
                TheurianError``, so the population that breaks it is everything
                outside that class. It is not hypothetical here -- a hand-edited
                evidence file carrying a JSON ``\\ud800`` escape reaches the
                driver as a lone surrogate and raises ``UnicodeEncodeError``,
                which is neither ``sqlite3.Error`` nor ``OSError``. The builder
                refuses that case upstream with a message naming the file; this is
                the backstop for a caller that builds a load by hand. The trade,
                recorded rather than traded away: this width also reports a
                *product* defect raised in the block as "the store could not be
                written", a remedy that will not help -- accepted because the
                alternative is a traceback, and the cause survives in the chain
                (``from exc``).
        """
        records = record_rows(load)
        participants = participant_rows(load)
        texts = text_rows(load)
        # Fixed width (`timespec="microseconds"`), for the reason `findings_store`
        # records: a bare `isoformat()` drops the fractional part when it is zero,
        # so `built_at` would be 26 or 32 characters depending on the wall clock.
        stamped_at = datetime.now(UTC).isoformat(timespec="microseconds")
        building = self.building_path

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # TWO controls, not one. Wholesale: a previous run that died mid-build
            # leaves a `.building` file that sqlite would happily open and extend,
            # landing its rows in the new store. CONTAINMENT (#404 R1-8):
            # `building` is derived lexically, so a symlink planted there pointing
            # outside the project would be written *through* by the connect;
            # unlinking the name first makes the connection create a fresh regular
            # file in the tree instead.
            _unlink_with_sidecars(building)
            with closing(sqlite3.connect(building)) as connection:
                for pragma in CONNECTION_PRAGMAS:
                    connection.execute(pragma)
                # `executescript` commits any pending transaction, so the DDL lands
                # before the data transaction the inserts and the stamp share.
                connection.executescript(REVIEW_SEARCH_DDL)
                connection.executemany(INSERT_RECORD, records)
                connection.executemany(INSERT_PARTICIPANT, participants)
                connection.executemany(INSERT_TEXT, texts)
                connection.execute(
                    INSERT_METADATA,
                    (REVIEW_SEARCH_SCHEMA_VERSION, EVIDENCE_FORMAT_VERSION, stamped_at),
                )
                connection.commit()
            _unlink_sidecars(self._path)
            os.replace(building, self._path)  # noqa: PTH105 - the atomic primitive
        except Exception as exc:
            # Best-effort and suppressed on purpose: a cleanup failure must not
            # replace the real error with a less informative one.
            with suppress(OSError):
                _unlink_with_sidecars(building)
            raise ReviewSearchStoreError(
                f"writing {self._path.name}: {exc}", remedy=_WRITE_REMEDY
            ) from exc

    def dump(self) -> tuple[ReviewSearchRecord, ...]:
        """Every stored record in ``relative_path`` order, for verification.

        Not a serving read: it takes no predicate, applies no bound and returns
        whole fragments, so a test can assert that the projection equals the load
        that produced it -- which is what makes "delete the store, rebuild from the
        same evidence, and it reproduces" a comparison over values rather than
        over the SQLite file's bytes, which legitimately drift under identical
        logical content.

        A missing store dumps empty. A damaged or otherwise unreadable one --
        including a file whose schema transaction committed and whose data
        transaction did not -- raises rather than returning a partial dump that
        would read as a smaller-but-valid corpus, which is why the metadata row's
        presence is checked before any content row is read.
        """
        try:
            exists = self._path.exists()
        except OSError as exc:
            raise ReviewSearchStoreError(f"reading {self._path.name}: {exc}") from exc
        if not exists:
            return ()
        try:
            with self._read() as connection:
                if connection.execute("SELECT 1 FROM review_search_metadata").fetchone() is None:
                    raise ReviewSearchStoreError(
                        f"{self._path.name} has no metadata row -- the file was left "
                        "half-built by a rebuild that crashed after its schema "
                        "committed but before its data transaction did"
                    )
                dumped = tuple(self._dumped_records(connection))
        except ReviewSearchStoreError:
            raise
        except Exception as exc:
            raise ReviewSearchStoreError(f"reading {self._path.name}: {exc}") from exc
        return dumped

    def _dumped_records(self, connection: sqlite3.Connection) -> Iterator[ReviewSearchRecord]:
        """Rebuild every stored record from the three content tables.

        Three whole-table reads rather than a query per record: a verification
        dump is over a store a test built, and one pass per table keeps the read
        linear in the corpus instead of quadratic in it.
        """
        participants: dict[str, list[str]] = {}
        for row in connection.execute(
            "SELECT relative_path, external_id FROM review_participants "
            "ORDER BY relative_path, position"
        ):
            participants.setdefault(str(row["relative_path"]), []).append(str(row["external_id"]))
        texts: dict[str, list[ReviewTextFragment]] = {}
        for row in connection.execute(
            "SELECT relative_path, channel, content FROM review_texts "
            "ORDER BY relative_path, position"
        ):
            texts.setdefault(str(row["relative_path"]), []).append(
                ReviewTextFragment(
                    channel=ReviewTextChannel(str(row["channel"])), content=str(row["content"])
                )
            )
        records = connection.execute(
            # The only interpolation is `review_search_sql`'s column-list
            # constant; nothing here comes from a caller (S608).
            f"SELECT {DUMP_COLUMNS} FROM review_records r ORDER BY r.relative_path"  # noqa: S608
        ).fetchall()
        for row in records:
            relative_path = str(row["relative_path"])
            yield _record_from(
                row,
                participants=tuple(participants.get(relative_path, ())),
                texts=tuple(texts.get(relative_path, ())),
            )

    def search(self, query: ReviewSearchQuery, *, text_chars: int) -> tuple[ReviewSearchHit, ...]:
        """The records ``query`` selects, in a total order, at most ``limit``.

        The one serving read. What it promises, and how each promise is kept:

        **Bounded in rows, and in excerpt text.** ``LIMIT`` is bound from
        :class:`ReviewSearchQuery`'s already-positive value, and ``text_chars`` --
        required, with no default -- cuts the excerpt inside the ``SELECT`` (see
        :func:`excerpt_columns`), so the **excerpt** this method materialises is
        bounded by ``limit * text_chars`` whatever the corpus holds.

        **The other columns come back whole, and nothing here bounds them.** That
        sentence used to read as though ``limit * text_chars`` bounded the whole
        read; it bounds one term of it. ``file_path`` and ``author_display_name``
        are author-controlled (ADR-0030 decision 3) and arrive at whatever length
        they were stored at, as do the structural strings. What bounds them is
        elsewhere and is recorded where it is: the evidence writer refuses a
        record above ``MAX_SOURCE_FILE_BYTES`` at landing, and the serving surface
        stops adding records past
        ``mcp/review_search.MAX_REVIEW_SEARCH_RESPONSE_CHARS`` when it shapes the
        response.

        **The cut bounds the projection, never the match.** The ``EXISTS``
        predicate names the whole ``content`` column; only the excerpt sub-selects
        cut it. So a substring living past ``text_chars`` still selects its record
        rather than reading as absent -- deliberately, because matching the cut
        text would manufacture false absences.

        **Substring matching folds ASCII case and nothing else.** ``LIKE`` is
        SQLite's, which case-folds the 26 ASCII letters and leaves every other
        codepoint exact: ``retry`` finds ``Retry``, ``É`` does not find ``é``, and
        text in a script with no case is matched exactly. Recorded rather than
        fixed here: ``lower()`` has the identical ASCII-only bound without an ICU
        build, so a "fully case-insensitive" claim is not one this store can make.

        **Current, or nothing -- checked through the connection that reads the
        rows.** Two things carry that, and neither of them is ``mode=ro``. The
        first is that **the stamp and the rows are read on one connection**: a
        second open between them would resolve the name again and could land on a
        different file, which is the split this method must not have. The second
        is that :meth:`replace_all` **publishes by ``os.replace``**, which swaps
        the directory entry and leaves an already-open connection reading the
        inode it holds -- a property of the open descriptor, not of the mode it
        was opened in. So a rebuild landing mid-call cannot split this method
        across two stores: the stamp and the rows come from one file, and the
        worst a concurrent rebuild does is make this call answer from the
        immediately-previous store -- whole, consistent, and one publish behind.

        Attributing it to ``mode=ro`` was wrong, and measured to be: a plain
        read-write connect holds the same property (PR #630 round 1, 2026-09-10),
        so the old sentence named a flag that could be dropped without the
        behaviour moving. Both real halves are driven --
        ``test_review_search_rebuild.py::``
        ``test_a_rebuild_that_lands_mid_call_answers_from_the_store_the_call_opened``
        lands a publish immediately after the stamp read and requires the whole
        answer to come from the store this call opened, and
        ``::test_publishing_a_rebuild_swaps_a_new_inode_onto_the_live_name`` is
        the rename's own fingerprint.

        **What ``mode=ro`` does do is a different guarantee**, and it is stated
        where it is applied: it stops a read conjuring an empty database at a path
        whose file is gone (:meth:`_read`, driven by
        ``test_review_search_store_guards.py::``
        ``test_a_serving_read_of_a_missing_store_conjures_no_database``). That
        matters to the refusal this method raises for a missing store; it has
        nothing to do with the race above.

        **A total, deterministic order** (:data:`SEARCH_ORDER`), and nothing in it
        is a rank: no key is computed from the query, so the sequence is a
        property of the corpus alone and two calls over one store agree.

        Raises:
            DomainError: If ``text_chars`` is not positive. Refused rather than
                passed to ``substr``, which answers a non-positive length with the
                empty string -- so a caller's arithmetic slip would silently serve
                every excerpt as ``""`` while the surface above, seeing a value
                inside its own bound, published it unmarked. That is the one way
                this parameter produces a wrong answer instead of a bounded one.
            ReviewSearchStoreError: If the store is missing, its metadata row is
                absent, its stamp is stale, it cannot be read, or a row it returned
                cannot be converted. **Every** failure raised inside this method's
                own read arrives as this class, whatever its Python type: the
                surface above turns this class into one constant refusal, so an
                escape of any other type is a second refusal shape for a second
                kind of damage (SEC-13).
        """
        if text_chars < 1:
            raise DomainError(
                f"review search text_chars must be at least 1, got {text_chars}. A "
                "non-positive cut is not a smaller answer, it is a wrong one: SQLite "
                "would serve every excerpt as empty and the surface above would "
                "publish it as the whole fragment."
            )
        pattern = fragment_pattern(query)
        where, parameters = where_clause(query, pattern)
        # Interpolated: four constants and two functions, all of them
        # `review_search_sql`'s -- the column list, the two excerpt sub-selects,
        # `where_clause`'s fixed column names and operators, and the fixed order
        # clause. Every *value* -- the cut, the filters, the patterns and the limit
        # -- is a bound parameter, so no caller text reaches the statement (S608).
        #
        # The three leading parameters bind in SELECT-list order, which precedes
        # the WHERE clause's and the LIMIT's: sqlite3 fills positional parameters
        # in statement order.
        statement = (
            f"SELECT {DUMP_COLUMNS}, {excerpt_columns()} "  # noqa: S608
            f"FROM review_records r {where} {SEARCH_ORDER} LIMIT ?"
        )
        try:
            with self._read() as connection:
                # **The staleness check lives here, inside the read that serves
                # the rows, and there is no separate probe.** A store whose stamp
                # is not the current (schema version, evidence format version)
                # pair is stale: either this file's DDL has moved or the evidence
                # documents it was projected from are written to a different
                # shape, and in both cases its rows would be read differently
                # now. Two opens would let a rebuild land between the check and
                # the rows; one connection cannot be split that way.
                #
                # `_read` is also where the *shape* of the path is refused, and
                # that refusal is a `ReviewSearchStoreError` rather than an
                # `OSError` -- so a socket, a named pipe or a directory at the
                # store path arrives as this method's own class, passes through
                # the `except ReviewSearchStoreError` arm below still worded for
                # its own cause, and is never relabelled "reading <file>". The
                # reach regression `findings_store` records having made once is
                # what that costs when the class is wrong.
                stamp_row = connection.execute(SELECT_STAMP).fetchone()
                if stamp_row is None:
                    raise ReviewSearchStoreError(
                        f"{self._path.name} carries no stamp, so nothing can say which "
                        "build produced its rows"
                    )
                if (
                    int(stamp_row["review_search_schema_version"]) != REVIEW_SEARCH_SCHEMA_VERSION
                    or int(stamp_row["evidence_format_version"]) != EVIDENCE_FORMAT_VERSION
                ):
                    raise ReviewSearchStoreError(
                        f"{self._path.name} was built by a superseded schema or from a "
                        "superseded evidence format, so its rows would be read "
                        "differently now"
                    )
                rows = connection.execute(
                    statement,
                    (text_chars, pattern, pattern, *parameters, query.limit),
                ).fetchall()
                # Converted INSIDE the boundary, not after it. A store whose
                # `pull_request` column holds text rather than a number -- SQLite's
                # columns are typed by affinity, so nothing stops one -- raises
                # `ValueError` in `int()` here, and outside the `try` that escapes
                # as a crash while this method's contract says a damaged store
                # raises `ReviewSearchStoreError`.
                served = tuple(_hit_from(row) for row in rows)
        except ReviewSearchStoreError:
            # The stamp refusals above are already this class and already worded
            # for their cause; re-wrapping would bury them under "reading <file>".
            raise
        except Exception as exc:
            # Deliberately every exception, for the reason `replace_all`'s arm
            # gives and one more: what reaches a caller from a damaged or hostile
            # store must be one class whatever the damage was, or the refusal shape
            # itself becomes a channel (SEC-13).
            raise ReviewSearchStoreError(f"reading {self._path.name}: {exc}") from exc
        return served

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """A read-only connection that will not create the file it cannot find.

        ``mode=ro`` so a query never conjures an empty database at a path whose
        file is gone -- the defect ``index_store._open_read`` records. Without it
        the serving read would create an empty database at the path and then
        report a store with no stamp.

        **``mode=ro`` says nothing about what is at the path, and the shape check
        below is what does.** A named pipe at a store path makes ``connect`` block
        inside ``open()`` -- measured on the findings store, still blocked when a
        watchdog fired six seconds later -- and a blocked call inside the daemon's
        admission gate holds a permit that removing the artefact does not release.
        ``stat`` answers from the directory entry and never opens anything, so it
        is the one check that cannot itself be what blocks. A **directory** is a
        member here: ``connect`` answers "disk I/O error" for one, which names
        nothing an operator can act on.

        **What this does not close is the window behind it.** It is a check on a
        name, and the ``connect`` that follows resolves that name again, so a
        co-resident process swapping a database and a named pipe at this path can
        still be answered "regular file" and then hand the open a pipe. The window
        cannot be closed here -- ``sqlite3.connect`` takes a path and no descriptor
        -- and it is recorded as a residual with its precondition, a racing writer
        co-resident with the reader, rather than claimed closed. The identical
        residual, with its measurement, is on ``findings_store._read``.
        """
        shape = irregular_shape_at(self._path) or ("a directory" if self._path.is_dir() else None)
        if shape is not None:
            raise ReviewSearchStoreError(
                f"the review search store path holds {shape}, not a file Theurian wrote",
                remedy=(
                    f"Remove {self._path} and run `theurian review build` to rebuild the "
                    f"store; `ls -l {self._path}` shows what is at the path now. It is "
                    f"derived state (ADR-0004), rebuilt from the evidence files under "
                    f".theurian/review/, so nothing authored is lost."
                ),
            )
        connection = sqlite3.connect(read_only_uri(self._path), uri=True)
        try:
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _record_from(
    row: sqlite3.Row, *, participants: tuple[str, ...], texts: tuple[ReviewTextFragment, ...]
) -> ReviewSearchRecord:
    pull_request = row["pull_request"]
    return ReviewSearchRecord(
        relative_path=str(row["relative_path"]),
        record_key=str(row["record_key"]),
        kind=str(row["kind"]),
        provider=str(row["provider"]),
        repository=str(row["repository"]),
        pull_request=None if pull_request is None else int(pull_request),
        thread_state=_optional_text(row["thread_state"]),
        file_path=_optional_text(row["file_path"]),
        source_uri=str(row["source_uri"]),
        author_external_id=str(row["author_external_id"]),
        author_display_name=str(row["author_display_name"]),
        participant_ids=participants,
        texts=texts,
        last_seen_run_id=str(row["last_seen_run_id"]),
        last_seen_at=str(row["last_seen_at"]),
    )


def _hit_from(row: sqlite3.Row) -> ReviewSearchHit:
    pull_request = row["pull_request"]
    return ReviewSearchHit(
        relative_path=str(row["relative_path"]),
        record_key=str(row["record_key"]),
        kind=str(row["kind"]),
        provider=str(row["provider"]),
        repository=str(row["repository"]),
        pull_request=None if pull_request is None else int(pull_request),
        thread_state=_optional_text(row["thread_state"]),
        file_path=_optional_text(row["file_path"]),
        source_uri=str(row["source_uri"]),
        author_external_id=str(row["author_external_id"]),
        author_display_name=str(row["author_display_name"]),
        last_seen_run_id=str(row["last_seen_run_id"]),
        last_seen_at=str(row["last_seen_at"]),
        excerpt=_optional_text(row["excerpt"]),
        excerpt_channel=_optional_text(row["excerpt_channel"]),
    )
