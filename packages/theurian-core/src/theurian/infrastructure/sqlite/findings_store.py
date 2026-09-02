"""SQLite adapter for the canonical review-finding store (ADR-0029, ADR-0004).

Implements :class:`~theurian.domain.ports.review_finding_store.ReviewFindingStore`
over a ``theurian-findings-*.sqlite`` file. Modelled on
:class:`~theurian.infrastructure.sqlite.index_store.SqliteIndexStore`'s connection
handling: ``sqlite3.connect`` as a context manager commits but does not close, so
writes go through :func:`contextlib.closing` with an explicit commit, and reads
open a ``mode=ro`` connection that will not conjure a missing file.

**One write, and it is a wholesale projection of git history.** :meth:`replace_all`
is the only mutation. It rebuilds the file from empty every time -- assembling
under a ``.building`` sibling and publishing by ``os.replace``, so the live name
only ever holds a whole store (#404) -- so the schema is always current after a
rebuild (a stale
:data:`~theurian.infrastructure.sqlite.findings_schema.FINDINGS_SCHEMA_VERSION`
cannot survive one), the rows are exactly the load's, and two rebuilds over one
load leave a logically identical store (AC-2). Its sole *shipped* caller feeds it
a ``FindingLoad`` a git source resolved -- but that is a fact about who calls it,
not a structural guarantee this adapter enforces: :meth:`replace_all` accepts any
``FindingLoad``, including one built from a fabricated ``commit_sha``, because
neither this adapter nor its port verifies commit provenance (see
:class:`~theurian.domain.ports.review_finding_store.ReviewFindingStore`'s port
docstring for the measured detail).

**One serving read, and it is :meth:`SqliteReviewFindingStore.serve_findings`**
(ADR-0029 phase-2 slice-3). The other reads are two metadata lookups and one
whole-table verification dump. Every SQL statement in this module that names the
``rejected_trailers`` table is a write or a dump; the serving statement selects
from ``findings`` alone, so no rejected trailer's bytes are read on a path that
answers a caller -- not filtered out afterwards, never fetched.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.errors import TheurianError
from theurian.domain.ports.review_finding_store import (
    FindingQuery,
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

#: The eleven columns of one accepted finding, in :class:`StoredFinding` order.
#: One string, so the dump and the serving read cannot drift on what a row is.
_FINDING_COLUMNS: Final = (
    "commit_sha, position, reviewer, severity, finding_text, provider, source_uri, "
    "committed_at, pull_request, family, specialist"
)

#: The served order: most recently committed first, ties broken by the primary
#: key. ``committed_at`` is UTC-normalised and fixed-width (#405), so byte order
#: *is* instant order and a TEXT ``DESC`` really is newest-first;
#: ``(commit_sha, position)`` is unique, so the whole ordering is total and two
#: calls over one store return one sequence. Without the tiebreak, `LIMIT` would
#: truncate an order SQLite is free to vary between runs -- a response that
#: changes without the store changing.
_SERVE_ORDER: Final = "ORDER BY committed_at DESC, commit_sha ASC, position ASC"

#: The escape character for the ``finding_text`` substring match. A caller's text
#: is matched *literally*: ``%`` and ``_`` in it are its own characters, not
#: wildcards, so ``q`` cannot be turned into a pattern language.
_LIKE_ESCAPE: Final = "\\"


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


#: The working-name suffix a rebuild assembles under before publishing (#404).
#: Matches the one ``index build`` and the purge use, so a reader of either file's
#: directory sees one convention for "a writer has not finished with this".
_BUILDING_SUFFIX: Final = ".building"

#: SQLite's two companion files. A database is these three names, so anything that
#: removes one removes all three or leaves a write-ahead log paired with a database
#: that never wrote it (``cli/commands.py``, ``sqlite/index_purge.py``).
_SIDECAR_SUFFIXES: Final = ("-wal", "-shm")


def _unlink_sidecars(path: Path) -> None:
    """Remove ``path``'s ``-wal``/``-shm`` companions, leaving ``path`` itself."""
    for suffix in _SIDECAR_SUFFIXES:
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _unlink_with_sidecars(path: Path) -> None:
    """Remove a database and both its companions, so nothing of it survives."""
    path.unlink(missing_ok=True)
    _unlink_sidecars(path)


def committed_at_text(moment: datetime) -> str:
    """One instant as the ``committed_at`` TEXT the store sorts on (#405).

    **Byte order is instant order, for every value this returns.** ``committed_at``
    is TEXT, and SQLite compares TEXT byte-wise, so an offset-preserving ISO-8601
    string is *not* a chronological key: a ``+14:00`` commit that is earlier in real
    time sorts after a ``-11:00`` commit that is later (the inversion #405
    measured), and the same instant written through two offsets is two unequal
    strings that unrelated rows can fall between. This is the bug class PR #112
    already recorded for the canonical store (``sqlite/schema.py``); the findings
    store repeats it because it stores what the committer's own timezone said.

    Two properties together make the relation exact, and neither is sufficient
    alone:

    - **normalised** -- ``astimezone(UTC)`` collapses every spelling of one instant
      to one string, so equal instants compare equal;
    - **fixed width** -- ``timespec="microseconds"`` pads the fractional part, so a
      sub-second value cannot sort against a whole-second one on the ``.``/``+``
      byte at offset 19. git's ``%cI`` is second-resolution and never triggers that,
      but the derived writers ADR-0029 owes are not bound by ``%cI``, and a key that
      is total only for one producer's precision is a trap rather than a key.

    The year is always four digits (``datetime`` spans 1..9999), so the whole string
    is exactly 32 characters and no prefix comparison can be truncated short.

    A naive ``datetime`` cannot reach here: ``ReviewFinding.__post_init__`` refuses
    one, and this store writes no other date. That refusal is what keeps
    ``astimezone`` from silently reading the *machine's* local offset into a stored
    value, which would make the store a function of where it was built.

    **An out-of-range UTC shift is a graded refusal, not a bare crash** (#405 R1-1).
    ``__post_init__`` admits *any* aware datetime, so a directly-constructed
    ``FindingLoad`` can carry a max-year negative-offset (or min-year positive-offset)
    date whose ``astimezone(UTC)`` raises ``OverflowError`` -- an ``ArithmeticError``,
    not a ``ValueError``. The shipped git path never reaches this: its
    ``_parse_committer_date`` rejects such a date upstream. This is the mirror guard
    for the port's documented direct-construction path, so the overflow surfaces as a
    :class:`FindingsStoreError` a ``TheurianError`` handler catches rather than a
    traceback -- and its remedy is the default rebuild-from-git, because git history
    is the one source that cannot produce this value.
    """
    try:
        return moment.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as exc:
        raise FindingsStoreError(
            f"committer date {moment.isoformat()!r} is out of range once converted to UTC, "
            "so it cannot be stored"
        ) from exc


def _contains_pattern(needle: str) -> str:
    """``needle`` as a LIKE pattern that matches it literally, anywhere in a value.

    The three characters LIKE gives meaning to -- the escape itself, ``%`` and
    ``_`` -- are escaped, so a caller's ``%`` matches a percent sign rather than
    every row. The escape character is doubled *first*: doing it after would also
    escape the backslashes this function itself just introduced.
    """
    escaped = (
        needle.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


def _where(query: FindingQuery) -> tuple[str, list[str | int]]:
    """``query``'s filters as a SQL clause and its bound parameters.

    Every value is a **bound parameter**; only the fixed column names and
    operators below are ever concatenated into SQL, so no caller-supplied text
    reaches the statement text. An absent filter contributes no clause at all
    rather than a tautology, so an unfiltered query is a plain ordered read.
    """
    clauses: list[str] = []
    parameters: list[str | int] = []
    equalities: tuple[tuple[str, str | int | None], ...] = (
        ("reviewer", None if query.reviewer is None else query.reviewer.value),
        ("severity", None if query.severity is None else query.severity.value),
        ("family", query.family),
        ("specialist", query.specialist),
        ("commit_sha", query.commit_sha),
        ("pull_request", query.pull_request),
    )
    for column, value in equalities:
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if query.text_contains is not None:
        clauses.append(f"finding_text LIKE ? ESCAPE '{_LIKE_ESCAPE}'")
        parameters.append(_contains_pattern(query.text_contains))
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), parameters


def _finding_rows(accepted: tuple[ReviewFinding, ...]) -> list[_FindingRow]:
    """Project accepted findings to insertable rows, assigning the position key.

    ``position`` is the finding's ordinal *within its commit*, in the source's
    total order -- so several findings on one commit stay distinct and stably
    ordered, and a rebuild over unchanged history assigns the same positions (AC-2).

    ``committed_at`` goes through :func:`committed_at_text` rather than
    ``date.isoformat()``: the stored column is a chronological sort key, and the
    committer's own UTC offset is not one (#405). Applied here, at the one place
    every accepted row is built, so the property holds for any ``FindingLoad`` the
    store admits -- including one a caller constructed without a git source, which
    the port explicitly says is possible.
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
                committed_at_text(finding.date),
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

    @property
    def building_path(self) -> Path:
        """Where :meth:`replace_all` assembles the next store before publishing it.

        A sibling of the published path, named by suffix, so it is contained by
        whatever contained that path: ``ProjectPaths.findings_for`` proves the
        publish name sits inside the project, and a name derived from it by
        appending cannot leave the directory the way a separately-composed working
        path could (#237, SEC-7). Exposed so a test can assert what a failed build
        left behind, not because any caller needs to name it.
        """
        return self._path.with_name(self._path.name + _BUILDING_SUFFIX)

    def replace_all(self, load: FindingLoad) -> None:
        """Rebuild the store to hold exactly ``load``, wholesale and idempotently.

        The file is built from empty each call and published by rename, so the
        schema is always current afterwards and the row set is exactly the load's.
        The findings, the rejected trailers and the stamp are written in one
        transaction: a crash before it commits leaves no stamp row rather than
        publishing a half-written store as valid.

        **Published by rename, never written under the live name** (#404). The
        build assembles at :attr:`building_path` and ``os.replace`` moves it onto
        the publish name once it is whole -- the discipline ``index build`` and the
        purge already record with their reasoning ("a file under the completed name
        is complete by construction", ``cli/index_commands.py``). The shape this
        replaced unlinked the live path first and wrote the replacement in place,
        so a concurrent reader could observe a missing file, or a file carrying the
        new schema and not yet its rows, and an interrupted rebuild destroyed the
        previously good store outright (measured on PR #396: 4 processes x 25
        rounds left one file with no tables at all under the publish name). Now the
        publish name only ever holds a whole store: the previous one until the
        rename, the new one after it.

        **The rename makes an old ``-wal``/``-shm`` beside the publish name
        dangerous, so they are removed just *before* it** (#404 R1-3). The in-place
        shape could argue that ``sqlite3.connect`` reconciles whatever a killed
        prior connection left; a rename cannot, because the file arriving under the
        name is a *different database* and a stale write-ahead log beside it belongs
        to the one just displaced -- a reader opening the new main beside the old
        log reads a mixture as neither store. Reaping *after* the rename left
        exactly that window; reaping before it makes the only intermediate state
        old-main without its sidecar (a valid earlier checkpoint of one database),
        and the rename then swaps in the whole new db atomically. Our own writes
        never leave a sidecar -- the last connection closing checkpoints and unlinks
        both (measured) -- so this only reaps a killed process's or a reader's
        residue.

        **``os.replace`` is atomic only within a filesystem**, which is why the
        working name is a sibling rather than a temporary directory: both paths sit
        in ``.theurian/state/`` by construction, so the rename cannot degrade into
        a copy across a device boundary.

        The whole operation, including directory creation and the rename, runs
        inside one ``try``: an earlier cut left ``mkdir``/``unlink`` outside it,
        catching only ``sqlite3.Error``, so a ``PermissionError`` on either escaped
        as a raw traceback past every ``TheurianError`` handler above this adapter
        (the same shape ``project_service.index_for`` converts ``(ValueError,
        OSError)`` for). Both ``OSError`` and ``sqlite3.Error`` convert here, and a
        failure takes the half-built sibling with it, so a failed rebuild leaves
        neither a partial file at the publish name nor a stale one beside it.

        **Serialisation is the caller's, and it is needed.** Two processes
        assembling at the same working name would corrupt each other, so
        ``findings build`` holds ``ProjectPaths.write_lock`` across this whole call
        (``application/findings_builder.py``). This adapter does not take the lock
        itself: it is handed a path, not a project, and a lock acquired here could
        not extend to the caller's own critical section -- the mistake #468 records,
        where two separate holds left a window worse than the race they closed.
        """
        finding_rows = _finding_rows(load.accepted)
        rejected_rows = _rejected_rows(load.rejected)
        # Fixed width (`timespec="microseconds"`), like `committed_at_text` (#404
        # R1-9): bare `isoformat()` drops the fractional part when it is zero, so
        # `built_at` would be 26 or 32 characters depending on the wall clock -- a
        # needless width wobble in a stored TEXT column, even though `built_at` is
        # metadata a reader never sorts on.
        stamped_at = datetime.now(UTC).isoformat(timespec="microseconds")
        building = self.building_path

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # This unlink is TWO controls, not one. Its wholesale role: a previous
            # run that died mid-build leaves a `.building` file behind, and sqlite
            # would happily open and extend it, landing its rows in the new store --
            # so it goes first. Its CONTAINMENT role (#404 R1-8): `building` is
            # derived lexically, so if a hostile tree plants a *symlink* there
            # pointing outside the project, unlinking it before `sqlite3.connect`
            # opens the name is what makes the connection create a fresh regular
            # file in the tree rather than write through the link to the target
            # (measured: dropping this unlink writes 24 KB to the outside target).
            # `building_path`'s own containment is only lexical; this is the control.
            _unlink_with_sidecars(building)
            with closing(sqlite3.connect(building)) as connection:
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
            # Reap the OLD db's sidecars *before* the rename, not after (#404 R1-3).
            # After it, the publish name would briefly hold the new main file beside
            # the old `-wal`/`-shm` -- a db plus a foreign log, which a reader opening
            # then reads as neither store. Doing it first makes the only intermediate
            # state old-main WITHOUT its sidecar (a valid earlier checkpoint of one
            # db), and the rename below then swaps in the whole new db atomically. It
            # also makes an unlink failure honest: it happens before anything is
            # published, so reporting a failed build is now correct rather than a
            # false failure over a store `os.replace` had already committed.
            _unlink_sidecars(self._path)
            # The atomic primitive. Everything above wrote to a name nothing reads.
            os.replace(building, self._path)  # noqa: PTH105 - the atomic primitive
        except (sqlite3.Error, OSError) as exc:
            # Best-effort, and suppressed on purpose: a cleanup failure must not
            # replace the real error with a less informative one (the shape
            # `migrate apply`'s create-database backstop uses).
            with suppress(OSError):
                _unlink_with_sidecars(building)
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
        mismatch is staleness.

        **The staleness *reaction* ships, and it is not here.**
        :meth:`serve_findings` refuses a stale store, but it makes the comparison
        itself, inside the connection it reads rows through, rather than calling
        this method first -- two opens would let a rebuild land between the check
        and the read, leaving the check answering for a file the rows did not come
        from. So this method still has no shipped caller: it is the standalone
        question, for a surface asking *about* a store rather than serving from
        it. The one shipped writer, ``findings build``, rebuilds wholesale on
        every run regardless of staleness -- strictly stronger than
        staleness-checking, not weaker -- so nothing needs this signal to decide
        whether to rebuild either.
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
                    # The only interpolation is this module's own column-list
                    # constant; nothing here comes from a caller (S608).
                    f"SELECT {_FINDING_COLUMNS} FROM findings ORDER BY commit_sha, position"  # noqa: S608
                ).fetchall()
                rejected_rows = connection.execute(
                    "SELECT commit_sha, position, raw_line, reason FROM rejected_trailers "
                    "ORDER BY commit_sha, position"
                ).fetchall()
                # Converted INSIDE the boundary, for the reason `serve_findings`
                # records: a value-damaged column raises where the row is built, not
                # where it is fetched, so a conversion left outside this `try` is a
                # crash escaping a method that promises to raise this class.
                dumped = FindingsDump(
                    findings=tuple(_stored_finding(row) for row in finding_rows),
                    rejected=tuple(_stored_rejection(row) for row in rejected_rows),
                )
        except FindingsStoreError:
            raise
        except Exception as exc:
            raise FindingsStoreError(f"reading {self._path.name}: {exc}") from exc
        return dumped

    def serve_findings(self, query: FindingQuery) -> tuple[StoredFinding, ...]:
        """The accepted findings ``query`` selects, newest first, at most ``limit``.

        The one sanctioned serving read (see the port). What this implementation
        adds to the port's four promises is *how* each is kept:

        **Accepted rows only, by the statement.** The ``SELECT`` names
        ``findings``; ``rejected_trailers`` appears nowhere on this path, so a
        rejected trailer's author-controlled ``raw_line`` and ``reason`` are never
        even read into this process on a call that answers a caller. That is
        stronger than fetching both and filtering: there is no filter to get
        wrong.

        **Current, or nothing -- checked through the connection that reads the
        rows.** ``mode=ro`` binds this connection to the file that existed when it
        opened, and ``replace_all`` publishes by ``os.replace`` onto that name
        (see its docstring), which swaps the *directory entry* and leaves an open
        connection reading the inode it already holds. So a rebuild landing
        mid-call cannot split this method across two stores: the stamp and the
        rows come from one file, and the worst a concurrent rebuild does is make
        this call answer from the immediately-previous store -- whole, consistent,
        and one publish behind. A reader that asked :meth:`is_current` first and
        then opened a second connection would have exactly the split this avoids.

        **Bounded.** ``LIMIT`` is bound as a parameter from
        :class:`FindingQuery`'s already-positive value. The scan behind it is a
        full pass over ``findings`` (the table's only index is its primary key,
        and the sort is on ``committed_at``), so the work is
        **corpus-bounded, not caller-bounded**: no filter a caller sends makes it
        larger, and the corpus is this repository's own history -- 502 accepted
        findings measured on ``origin/main`` @ ``141cf6f``, 2026-09-02 (T-6).

        **Substring matching folds ASCII case and nothing else.** ``LIKE`` is
        SQLite's, which case-folds the 26 ASCII letters and leaves every other
        codepoint exact: ``critical`` finds ``CRITICAL``, ``É`` does not find
        ``é``, and text in a script with no case is matched exactly. Recorded
        rather than fixed here: ``lower()`` has the identical ASCII-only bound
        without an ICU build, so a "fully case-insensitive" claim is not one this
        store can make, and stating the bound is what keeps a caller from relying
        on the claim it cannot keep.

        Raises:
            FindingsStoreError: If the store is missing, its metadata row is
                absent, its stamp is stale, it cannot be read, or a row it
                returned cannot be converted -- a column holding a value its type
                does not admit. **Every** failure raised inside this method's own
                read arrives as this class, whatever its Python type: the caller
                above turns this class into one constant refusal, so an escape of
                any other type is a second refusal shape for a second kind of
                damage (SEC-13). All of them carry the rebuild remedy, because the
                store is a projection of git history (ADR-0004) and rebuilding is
                the cure for each.
        """
        where, parameters = _where(query)
        # Interpolated: this module's column list, `_where`'s fixed column names
        # and operators, and the fixed order clause. Every *value* -- including
        # the limit -- is a bound parameter, so no caller text reaches the
        # statement text (S608).
        statement = f"SELECT {_FINDING_COLUMNS} FROM findings {where} {_SERVE_ORDER} LIMIT ?"  # noqa: S608
        try:
            with self._read() as connection:
                stamp_row = connection.execute(
                    "SELECT findings_schema_version, parser_stamp FROM findings_metadata "
                    "WHERE id = 1"
                ).fetchone()
                if stamp_row is None:
                    raise FindingsStoreError(
                        f"{self._path.name} carries no stamp, so nothing can say which "
                        "grammar produced its rows"
                    )
                if (
                    int(stamp_row["findings_schema_version"]) != FINDINGS_SCHEMA_VERSION
                    or str(stamp_row["parser_stamp"]) != PARSER_STAMP
                ):
                    raise FindingsStoreError(
                        f"{self._path.name} was built by a superseded schema or trailer "
                        "grammar, so its rows would be read differently now"
                    )
                rows = connection.execute(statement, (*parameters, query.limit)).fetchall()
                # Converted inside the boundary, not after it (R1-2 face iv). A
                # store whose `position` or `pull_request` holds text rather than a
                # number -- SQLite's columns are typed by affinity, so nothing stops
                # one -- raises `ValueError` in `int()` here, and outside the `try`
                # that escaped as a crash while this method's contract says a damaged
                # store raises `FindingsStoreError`. The tool above turns that class
                # into one constant refusal; a `ValueError` instead reached the caller
                # as a different error shape for a different kind of damage, which is
                # the refusal-distinguishability family (SEC-13) arriving by accident.
                served = tuple(_stored_finding(row) for row in rows)
        except FindingsStoreError:
            # The stamp refusals above are already this class, already worded for
            # their cause; re-wrapping would bury them under "reading <file>".
            raise
        except Exception as exc:
            # Deliberately every exception, not `(sqlite3.Error, OSError)`. What
            # reaches a caller from a damaged or hostile store must be this class
            # whatever the damage was: an `OverflowError` binding a parameter, a
            # `ValueError` converting a column, a `UnicodeDecodeError` on a text
            # column holding invalid bytes. The narrow tuple was a list of the
            # damage shapes somebody had thought of, and round 1 found the one
            # nobody had. Input-side mistakes are refused at the surface
            # (`mcp/findings.py`) rather than arriving here, so this arm's rebuild
            # remedy stays the right cure for everything it does catch.
            raise FindingsStoreError(f"reading {self._path.name}: {exc}") from exc
        return served

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """A read-only connection that will not create the file it cannot find.

        ``mode=ro`` so a query never conjures an empty database at a path whose file
        is gone -- the defect `index_store._open_read` records.

        **Not every caller has checked that the file exists**, and this is what
        makes that safe. :meth:`stamp` and :meth:`dump` probe first;
        :meth:`serve_findings` deliberately does not, because a probe followed by
        an open is two looks at a name a rebuild can move between them -- so the
        missing-store case arrives here as ``sqlite3.OperationalError`` from the
        open, converted by that method's own boundary. ``mode=ro`` is what keeps
        that honest: without it, the serving read would *create* an empty database
        at the path and then report a store with no stamp.
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
