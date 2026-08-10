"""Building and querying the retrieval index (FR-R1, FR-R2).

Two retrievers over one file: FTS5 for terms and an exact vector scan for
meaning. Both return *ranked lists*, never scores to be compared across
retrievers — fusion happens in :mod:`theurian.domain.ranking`, which is where it
can be reasoned about without a database.

Filtering happens in SQL, before ranking (FR-R1). Filtering after ranking would
let a caller infer that a document they may not read exists, by noticing how many
results vanished.

What a caller's text has to survive before it reaches any of this lives in
:mod:`theurian.infrastructure.sqlite.index_query` — the operator characters, the
input bounds, the trigram floor, the `LIKE` escaping. Anyone auditing how an
untrusted query becomes SQL should read that file, not this one (SEC-8). The one
retriever that has to invent its own ranking, the scan below the trigram floor,
keeps its statement and its cost in
:mod:`theurian.infrastructure.sqlite.index_scan`.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.chunking import IndexableChunk
from theurian.domain.enums import KnowledgeStatus
from theurian.domain.errors import TheurianError
from theurian.domain.ranking import Ranked, RetrieverPage, estimate_tokens
from theurian.infrastructure.sqlite.index_purge import purge_into
from theurian.infrastructure.sqlite.index_query import (
    MAX_QUERY_CHARS,
    MAX_QUERY_TERMS,
    to_match_expression,
    to_scan_terms,
    to_trigram_expression,
)
from theurian.infrastructure.sqlite.index_scan import scan_statement
from theurian.infrastructure.sqlite.index_schema import (
    FTS5_PROBE,
    INDEX_DDL,
    INDEX_SCHEMA_VERSION,
)
from theurian.infrastructure.sqlite.schema import CONNECTION_PRAGMAS, read_only_uri

#: Little-endian float32. Fixed rather than native so an index built on one
#: machine reads correctly on another -- the file is derived, but it is also
#: copied between machines more often than anyone plans for.
_VECTOR_FORMAT: Final = "<%df"

#: The width of one component of the above. Named rather than spelled `4` in two
#: places, because the two places are a pack and an unpack and they have to agree.
_FLOAT32_BYTES: Final = 4

#: Cosine below which a dense hit is noise rather than a match.
#:
#: Hashed n-grams give almost any pair of strings a small nonzero similarity, so
#: without a floor every query returns the whole corpus ranked by accident — and
#: an agent asking about payroll receives an approved architecture decision. The
#: value is measured, not guessed: over 400 random strings against a real corpus
#: the 99th percentile was 0.187 and the maximum 0.238, while genuinely related
#: queries scored 0.296 and above. 0.25 sits in that gap.
#:
#: A real embedding model separates these distributions far better and would
#: want its own floor; it arrives with its own adapter.
DENSE_SIMILARITY_FLOOR: Final = 0.25


class IndexBuildError(TheurianError):
    """The index could not be built or queried."""


class Fts5UnavailableError(IndexBuildError):
    """This SQLite build has no FTS5.

    Detected up front rather than discovered halfway through a build, because
    the failure otherwise arrives as a bare syntax error on a `CREATE VIRTUAL
    TABLE` and points at nothing actionable.
    """

    def __init__(self) -> None:
        super().__init__(
            "This Python's SQLite was built without FTS5, so lexical search is "
            "unavailable. Install Python from python.org or your distribution's "
            "python3 package, both of which enable it."
        )


class IndexUnreadableError(IndexBuildError):
    """This index file cannot be searched by this build.

    Raised rather than swallowed, and that distinction is the whole point. An
    index written by an older schema is missing tables this code queries, and
    SQLite reports that as an ``OperationalError`` indistinguishable, to a bare
    ``except``, from "your query was malformed". Returning ``()`` for it made an
    upgrade silently switch Japanese search off for an entire knowledge base:
    `chunks_trigram` was gone, `unicode61` cannot segment CJK, every query
    answered "no results", and the response still said ``indexed: true``.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"This project's retrieval index cannot be read ({detail}). It was "
            f"built by an older version of Theurian, damaged, or replaced while "
            f"this search was reading it. Run `theurian index build` to rebuild "
            f"it; the index is derived, so nothing is lost."
        )


class _QueryExpressionError(IndexUnreadableError):
    """FTS5 refused the caller's *expression*, not the file.

    Private, and deliberately a subclass of the error it carves out from. The
    two retrievers that hand caller text to FTS5 catch this and answer ``()``;
    anywhere else it stays the loud, remedy-carrying refusal. A carve-out with
    its own unrelated base would escape an uninterested caller as a bare
    traceback at an agent — the failure :func:`_mapping_read_failures` exists to prevent,
    reintroduced by the mechanism meant to soften it.
    """


#: FTS5's own expression-parser complaints — the only thing a caller's text can
#: make SQLite say about a *read* of this file.
#:
#: An allow-list, but over the closed side, and that inversion is the whole
#: point. What this replaced enumerated the shapes a *broken file* takes, and
#: that set is open: three escaped it. `unable to open database file`, which is
#: the state a build reclaimed out from under an in-flight search leaves, was
#: never on the list; and both corruption fragments that *were* on it —
#: SQLITE_CORRUPT and SQLITE_NOTADB — arrive as `sqlite3.DatabaseError` rather
#: than `OperationalError`, so the handler consulting the list could not see
#: them however long the list grew. Whatever is not below is now the file's
#: fault, so the shape nobody anticipated fails towards a remedy rather than
#: towards a traceback at an agent.
#:
#: Measured on SQLite 3.51.2, not guessed: `NEAR(`, `a OR`, `^`, `(a` and
#: `a AND AND b` give the first; `"unbalanced` the second; `*` the third;
#: `NEAR(a b, x)` the fourth.
#:
#: Matched as *prefixes*, which is load-bearing twice. `no such module: fts5` —
#: how a SQLite built without FTS5 reports this index's virtual tables — contains
#: "fts5" and is emphatically the file's problem, and the substring test this
#: replaced would have handed it to the query side. A corrupt-file message that
#: happened to embed one of these phrases would go the same wrong way.
_QUERY_EXPRESSION_ERRORS: Final = (
    "fts5: syntax error",
    "unterminated string",
    "unknown special query",
    "expected integer, got",
)


def _is_query_expression_error(exc: sqlite3.Error) -> bool:
    """Whether FTS5 rejected the *expression* — a case that can no longer happen.

    **The carve-out from a file-shaped default, which is the reverse of what the
    predicate beside it used to be.** :func:`_mapping_read_failures` treats every
    `sqlite3.Error` as the index's fault until this says otherwise, and asks this
    of nothing else: a decode, an unpack or a numeric conversion cannot be the
    caller's expression, so :data:`_UNREADABLE_VALUES` gets no carve-out at all.

    **Deliberately narrower than the true set of query-borne messages.** An FTS5
    column filter — `col : x`, `-a`, `{a b` — is rejected with `no such column`,
    byte-for-byte what a file missing that column says. The ambiguity resolves to
    the file, because "this index cannot be read" over a malformed query is a
    wrong *diagnosis* while "nothing matched" over a broken file is a wrong
    *answer*, and only the second is silent.

    **This predicate is unreachable today, and that is recorded rather than
    hidden.** Nothing `index_query` can now build is a malformed FTS5 expression:

    - `to_match_expression` deletes every ``"`` and strips the operator
      characters from each term's edges, then wraps each term in quotes — so what
      reaches FTS5 is a list of phrases whose only syntactically significant
      character cannot be present;
    - `_is_transportable` removes the two things that broke the string before
      FTS5 ever parsed it: a NUL and a lone surrogate.

    Measured, not assumed. Replacing both call sites with a raise leaves the
    whole suite green, and 5,580 adversarial queries — every one- and
    two-character string over ``"*():^-{}[]<>!@#$%&+=~`|\\/;,.?'`` plus NUL,
    a lone surrogate and whitespace, FTS5's own operator syntax, and 4,000
    random strings — reached it zero times across 11,160 calls.

    **Kept anyway, as the cheap half of a defence in depth.** The day someone
    relaxes the sanitising above, a syntax error stops being impossible and
    starts being an exception that escapes as a tool failure at an agent — the
    exact HIGH that was just closed. A branch that costs two string comparisons
    is a poor thing to trade for that.

    **What actually holds the invariant is the sanitising, not this branch.** It
    now lives one module away: anyone changing `to_match_expression`,
    `_query_terms`, or `_is_transportable` in
    :mod:`theurian.infrastructure.sqlite.index_query` is changing the thing under
    test, and the tests that fail are `test_punctuation_never_raises_at_the_user`,
    `test_a_nul_byte_in_a_query_returns_nothing_rather_than_raising`, and
    `test_a_lone_surrogate_in_a_query_returns_nothing_rather_than_raising` in
    ``tests/integration/test_index_store.py``, plus
    `test_a_query_containing_an_untransportable_character_does_not_raise` in
    ``tests/integration/test_mcp_tools.py``. None of them exercises this line,
    and none of them should have to.

    The distinction from the bm25 relevance floor this milestone removed is the
    whole point of writing it down. That floor *claimed* to exclude weak matches
    and excluded nothing — a guard that read as protection and was not, which is
    why it went. This one is known not to fire, says so, and is kept for a
    named future case. The code is nearly identical; only the comment separates
    an honest belt-and-braces from a lie.
    """
    return str(exc).lower().startswith(_QUERY_EXPRESSION_ERRORS)


def fts5_available() -> bool:
    """Whether this SQLite build supports FTS5."""
    with closing(sqlite3.connect(":memory:")) as connection:
        try:
            connection.execute(FTS5_PROBE)
        except sqlite3.OperationalError:
            return False
    return True


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """A configured *writable* connection that is always closed.

    ``sqlite3.connect`` as a context manager commits or rolls back but does not
    close, which leaks a handle per call -- caught in Milestone 1 and worth not
    repeating.

    Used by the build paths only. Reads go through :func:`_open_read`, which
    refuses to create the file it was asked to open.
    """
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        for pragma in CONNECTION_PRAGMAS:
            connection.execute(pragma)
        yield connection
    finally:
        connection.close()


def _open_read(path: Path) -> sqlite3.Connection:
    """A read-only connection that will not conjure the file it cannot find.

    **`mode=ro` is the whole point, and it closes a defect rather than tightening
    a permission** (ADR-0024 decision 7). ``sqlite3.connect`` on a path that does
    not exist *creates an empty database there*, so a pointer that outlived its
    file -- which `theurian index gc` makes an ordinary state -- turned the "no
    index, fall back to the substring scan" branch into a raw ``no such table:
    chunks_fts`` at the agent, and left a file behind that made every later
    attempt fail identically. Measured: the default connect recreates the path;
    `mode=ro` raises ``unable to open database file`` and creates nothing.

    All four ``CONNECTION_PRAGMAS`` are accepted on a read-only connection, and
    the read and write paths stay configured the same way -- **but that rests on
    a premise: every index file is created in WAL mode.** ``PRAGMA journal_mode =
    WAL`` on a read-only connection only *reports* the mode when the file is
    already WAL; on a DELETE-mode file it tries to *set* it and raises ``attempt
    to write a readonly database`` (measured on SQLite 3.47.1). Index files are
    always created WAL -- `create` runs `CONNECTION_PRAGMAS`, and
    ``test_a_built_index_is_always_in_wal_mode`` pins it -- so the premise holds
    for every file this opens. If it were ever violated the failure would be a
    mapped ``IndexUnreadableError`` naming a rebuild, not a corruption, which is
    the right shape for a malformed index; but the premise is what makes these
    pragmas harmless rather than the read-only mode alone.

    A write through this connection is refused by SQLite rather than by
    convention.
    """
    connection = sqlite3.connect(read_only_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    for pragma in CONNECTION_PRAGMAS:
        connection.execute(pragma)
    return connection


#: What *interpreting this file* can raise, other than through `sqlite3` itself.
#:
#: **The population is a boundary, not an exception hierarchy, and stating it as
#: a hierarchy is what reopened this twice.** Both previous statements looked
#: complete from inside:
#:
#: - the first enumerated *message fragments* of `sqlite3.OperationalError`.
#:   Three shapes escaped it, `unable to open database file` — the state a
#:   rebuild reclaiming this file out from under an in-flight search leaves —
#:   among them;
#: - the second enumerated `sqlite3.DatabaseError` and inverted the default, so
#:   that anything which was not FTS5 judging the caller's *expression* became
#:   the file's fault. Measured through the real ``knowledge.search`` against a
#:   real index with 1 to 40 random bytes corrupted past the first page, 400
#:   fixtures: 117 refusals naming `theurian index build`, and **25 escapes** —
#:   `ToolError: 'utf-8' codec can't decode byte 0xb9 in position 53` at the
#:   agent, no remedy named, every later query against that project failing
#:   identically.
#:
#: The key that closes it is not a base class but a question: **does this line
#: interpret bytes that came out of this file?** Those bytes are untrusted — the
#: index is derived, unsigned and git-ignored (SEC-7), and a bit flipped past the
#: header survives the schema gate, which still reports ``schema_version: 2`` and
#: ``is_searchable: True``. Interpretation fails in whichever type the
#: interpreter uses, and that is almost never `sqlite3`'s:
#:
#: - `UnicodeDecodeError` — SQLite's own *error text* for a corrupt schema is not
#:   always valid UTF-8, and `sqlite3` decodes it before it can raise. It fires
#:   inside :func:`_connect`'s PRAGMA loop, so it took out all nine reads at once
#:   and was the widest member of the class by a factor of seven;
#: - `struct.error` and `TypeError` — an `embeddings.vector` holding two bytes,
#:   nine bytes, TEXT or an INTEGER, none of which `_unpack` can refuse;
#: - `ValueError` and `TypeError` again — `float()` and `int()` over a cell whose
#:   storage class is not the one its column declares. A NULL `chunks.heading`
#:   makes the scan branch's whole ordering key NULL (`length(lower(NULL))` is
#:   NULL and the sum propagates it), and `float(None)` is not a database error;
#:   a TEXT `index_metadata.index_schema_version` gave `invalid literal for
#:   int() with base 10: 'two'` out of :meth:`SqliteIndexStore.schema_version`,
#:   which is the method that promises never to raise.
#:
#: **The cost of this width, stated rather than discovered later.** A genuine
#: programming error inside a :meth:`SqliteIndexStore._read` block is now reported as an
#: unreadable index. Two things bound it: nothing goes inside such a block except
#: executing a statement and converting its rows — which is a convention this
#: module holds and the next reader must keep — and ``raise ... from exc``, so
#: whoever has the traceback still has the real cause. It is the better of two
#: wrong answers. "Rebuild your index" costs seconds and loses nothing
#: (ADR-0004); a traceback at an agent names no remedy and repeats forever.
#:
#: **The key holds beyond this file. Where to ask it is a separate question, and
#: every statement of that has so far been too small.** Re-drawn in `ef325c9`
#: over two halves divided by who owns the error contract — the module containing
#: the read (the state pointer, the registry, the ingestion manifest), and a port
#: adapter under ADR-0003 (this store, and
#: :class:`~theurian.infrastructure.sqlite.store.SqliteCanonicalStore`) — that
#: statement said no member was open. Three were, and no reading of this comment
#: found any of them:
#:
#: - **the canonical half is not one module.** Opening a connection interprets
#:   the file, and `write_transaction` opens one without going through the store
#:   at all, so `int()` over `schema_metadata.schema_version` in
#:   :func:`~theurian.infrastructure.sqlite.connection._assert_schema_version`
#:   sat outside every guard on the write path and published that cell from
#:   `theurian migrate status` and `theurian migrate apply`. Closed in `3893ab2`
#:   by guarding :func:`~theurian.infrastructure.sqlite.connection._prepare`,
#:   which is the one function both openers call;
#: - **withholding a cell from a message is not withholding it.** The cause
#:   travels on ``__cause__`` by design and Typer renders the chain, so the same
#:   two commands published from six positions what the guard had just withheld.
#:   That is a *different* class with a different root cause; it is named in
#:   :class:`~theurian.infrastructure.sqlite.connection.StateDatabaseUnreadableError`
#:   and closed at the CLI's ``--json`` boundary, not here.
#: - **a sweep reaches only the branches its corpus takes.** `_refuse_if_empty`
#:   in `cli/index_commands.py` opens a second store session, outside the
#:   conversion `_run_build` puts one function above, and only a build that
#:   indexed zero chunks runs it. `index build` was already in the CLI sweep in
#:   `tests/integration/test_canonical_store_corruption.py`, over a corpus that
#:   indexes chunks, so the branch was never taken and the sweep reported green
#:   across it. Closed in `c7d59b4` — found by enumerating every CLI call site
#:   that can raise `TheurianError`, and fixtured so the branch now has a
#:   corpus. A closure argument whose evidence is a sweep claims no more than
#:   the sweep's corpus can reach.
#:
#: What is claimed here, and no more: **in this file**, all nine reads enter
#: through :meth:`SqliteIndexStore._read` and convert inside the block. Both
#: counts need a command anchored to code, because a grep whose subject is a grep
#: matches its own quotation. `_read` replaced the free `_reading` function when
#: reads moved to a session-scoped connection (ADR-0024 point 7), so the anchor
#: moved with it:
#:
#: - ``grep -cE '^[[:space:]]+with self\._read\(\)' index_store.py`` is 9;
#: - ``grep -cE '^[^#]*\.fetch(all|one)\(\)' index_store.py`` is 9, over the
#:   same nine method bodies.
#:
#: The mapping those nine blocks share is :func:`_mapping_read_failures`, which
#: `_read` enters whether it opens a fresh connection or reuses the session's --
#: so a held connection and a per-call one cannot answer differently for the same
#: broken file.
#:
#: Nothing enforces that a tenth read joins them; this is a count, not a
#: guarantee, and the same shape of sentence written one file over was the one
#: three reviewers falsified. The canonical store states its own population and
#: its own evidence in :func:`~theurian.infrastructure.sqlite.store._reading`.
#: Neither claims an inventory of Theurian's reads of untrusted bytes — the key
#: is a question to ask of a new read, and no comment stays true on its own.
_UNREADABLE_VALUES: Final = (UnicodeDecodeError, struct.error, TypeError, ValueError)


@contextmanager
def _mapping_read_failures() -> Iterator[None]:
    """Everything a read of this file can fail with, mapped to one type.

    Separated from the connection's lifetime so that a held connection gets the
    same treatment as a per-call one. Opening and mapping were one function until
    reads moved to a session-scoped connection (ADR-0024 point 7), which needs a
    mapping that wraps a read without opening it.
    """
    try:
        yield
    except sqlite3.Error as exc:
        if _is_query_expression_error(exc):
            raise _QueryExpressionError(str(exc)) from exc
        raise IndexUnreadableError(str(exc)) from exc
    except _UNREADABLE_VALUES as exc:
        # The exception's *type* and never its message. `float()` and `int()`
        # put the cell they could not convert into the `ValueError` they raise,
        # and under corruption that cell holds whatever was on the page --
        # knowledge text included. This detail is rendered to a user by the
        # index CLI, so building the message out of `str(exc)` would publish a
        # fragment of a document to whoever can make an index unreadable.
        raise IndexUnreadableError(f"an unreadable value, {type(exc).__name__}") from exc


@final
class SqliteIndexStore:
    """Writes and reads one index build."""

    def __init__(self, path: Path) -> None:
        # A path and nothing else. `_scan_cache` stood here until `IndexStore`
        # gained an explicit exhaustion signal (#16): a memoisation of
        # `_scan_below_the_trigram_floor` that its own docstring called "a
        # mitigation for one named defect, not an optimisation -- delete it when
        # the defect is fixed at its cause". The defect was `_visible_ranking`
        # inferring exhaustion from a row count, and the second call the memo
        # existed to answer no longer happens. `_scan_below_the_trigram_floor`
        # carries the account: what now holds the property the memo held, and
        # what became of the fresh-instance-per-search rule it imposed.
        #
        # A path, and a connection only while a `session()` is open. `_reader`
        # is `None` at rest, which is what keeps "one caller's query leaves
        # nothing behind for another's" checkable by reading this method: a cache
        # would leak a withheld-row count into the next caller's latency, and a
        # connection cannot, because it carries no result.
        self._path = path
        self._reader: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def session(self) -> Iterator[SqliteIndexStore]:
        """Hold one read connection for the duration of a request (ADR-0024 point 7).

        **Opt-in, and scoped to a request rather than to this object's
        lifetime.** Every read outside a session opens and closes its own
        connection, which is what a one-shot caller like ``theurian index
        status`` wants and what every construction site in the suite already
        does. Inside a session the reads share one connection, which is what a
        *search* needs -- and the difference is not an optimisation.

        `RetrievalService.search` reads this port several times: two retrievers
        through the depth loop, then `chunk_texts`. Each gap between those reads
        used to be a window in which `theurian index gc` could unlink the file,
        and `sqlite3.connect` on the deleted path then created an empty database
        there, so the fallback never ran and the agent got `no such table:
        chunks_fts`.

        Measured, one request of four index calls with the unlink landing after
        the first: **1 of 4 answered** with a connection per call, leaving a file
        recreated at the reaped path, against **4 of 4** inside a session,
        recreating nothing. On POSIX an open descriptor keeps the inode readable
        after the name is gone -- in all three WAL configurations, sidecars kept
        or unlinked -- so the request finishes against the build it started on.

        A session is not a transaction and holds no snapshot: reads inside one
        still see another connection's commits. What it holds is the *file*.
        """
        if self._reader is not None:  # pragma: no cover - nesting is not a use here
            yield self
            return
        with _mapping_read_failures():
            self._reader = _open_read(self._path)
        try:
            yield self
        finally:
            self._reader.close()
            self._reader = None

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """A read, through the session's connection when there is one.

        The error mapping is `_mapping_read_failures`, entered whether this opens
        a fresh connection or reuses the session's, so a held connection and a
        per-call one cannot answer differently for the same broken file. Opening
        happens inside it, because opening interprets the file exactly as a query
        does.
        """
        with _mapping_read_failures():
            if self._reader is not None:
                yield self._reader
                return
            connection = _open_read(self._path)
            try:
                yield connection
            finally:
                connection.close()

    # -- Building ---------------------------------------------------------

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        """Create an empty index database.

        Fails if the file already exists rather than adding to it: an index build
        is an all-or-nothing artifact, and appending to a half-built one produces
        a file that looks complete and silently is not.
        """
        if self._path.exists():
            msg = f"{self._path} already exists. An index build writes a new file."
            raise IndexBuildError(msg)
        if not fts5_available():
            raise Fts5UnavailableError

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._path) as connection:
            connection.executescript(INDEX_DDL)
            connection.execute(
                "INSERT INTO index_metadata (id, index_schema_version, index_build_id, "
                "state_hash, built_at) VALUES (1, ?, ?, ?, ?)",
                (INDEX_SCHEMA_VERSION, index_build_id, state_hash, datetime.now(UTC).isoformat()),
            )
            connection.commit()

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        """Insert chunks. Returns how many were written."""
        if not chunks:
            return 0

        with _connect(self._path) as connection:
            connection.executemany(
                "INSERT INTO chunks (chunk_id, project_id, item_id, revision_id, ordinal, "
                "heading, text, token_estimate, status, sensitivity, trust_level, namespace) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.chunk.chunk_id,
                        c.project_id,
                        c.item_id,
                        c.revision_id,
                        c.chunk.ordinal,
                        c.chunk.heading,
                        c.chunk.text,
                        estimate_tokens(c.chunk.text),
                        c.status,
                        c.sensitivity,
                        c.trust_level,
                        c.namespace,
                    )
                    for c in chunks
                ],
            )
            connection.commit()
        return len(chunks)

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
    ) -> int:
        """Write this build minus `revision_ids` to `target` (ADR-0024).

        The one write on this store that does not write to `self._path`, and the
        asymmetry is the decision: a published build is immutable, so the purge
        reads here and writes there. `index_purge` owns the SQL for the same
        reason `index_scan` owns the scan's — this file is already the largest in
        the package, and a purge is a distinct concern from a query.
        """
        return purge_into(
            self._path,
            target,
            revision_ids=revision_ids,
            index_build_id=index_build_id,
            state_hash=state_hash,
        )

    def holds_any_revision(self, revision_ids: Sequence[str]) -> bool:
        """Whether a purge of ``revision_ids`` would remove anything from this build.

        The cheap pre-check that keeps the withdrawal purge (ADR-0024 decision 5)
        from copying a whole index only to delete nothing: ``migrate apply``
        replays the whole set on any state-hash shift (ADR-0016), so a project
        with a past withdrawal asks this on every apply and almost always gets
        ``False``. Answered by the ``chunks_by_revision`` index, so the first
        clause is a lookup rather than a scan.

        **It tests both seeds of `index_purge._DOOMED`, not just the revision
        match**, so it stays equivalent to ``derive_purged`` returning a non-zero
        count. The second is a node with no provenance edge at all -- absent from
        ``node_derivation`` -- which the purge deletes even when nothing was
        withdrawn (a partial or migrated build leaves them). No node exists until
        RAPTOR writes one (ADR-0008), so the clause is dormant today; without it,
        once one does, a build with an unprovenanced node but no withdrawn-
        revision match would be skipped and its residue would survive.

        **v4, not v3.** The second clause moved from ``chunks``/``derived = 1``/
        ``chunk_derivation`` to ``nodes``/``node_derivation`` when ADR-0008
        decision 5's amendment gave RAPTOR summaries their own tables -- a
        predicate still naming ``chunk_derivation`` would raise ``no such table``
        against a v4 index rather than merely answering wrong, because this runs
        on the withdrawal path (``application/withdrawal_purge.py`` calls it as
        the pre-check on every ``migrate apply`` that withdraws anything), not
        only on the purge path.

        Two ``SELECT``s joined by ``UNION ALL`` rather than one ``OR``, because
        the two clauses now read different tables and ``OR`` cannot span a
        ``FROM``. Still one round trip: the second half only has to be evaluated
        when the first finds nothing, and ``LIMIT 1`` over the union stops there.
        """
        placeholders = ", ".join("?" for _ in revision_ids) or "NULL"
        unprovenanced = "node_id NOT IN (SELECT node_id FROM node_derivation)"
        with self._read() as connection:
            row = connection.execute(
                f"SELECT 1 FROM chunks WHERE revision_id IN ({placeholders}) "  # noqa: S608 - placeholders generated, values bound
                f"UNION ALL SELECT 1 FROM nodes WHERE {unprovenanced} LIMIT 1",
                tuple(revision_ids),
            ).fetchone()
            return row is not None

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        """Store one vector per chunk.

        Absent embeddings are a supported state, not a broken one: a machine with
        no embedding provider still gets lexical search, and the reported
        retrieval mode says so rather than pretending otherwise.
        """
        if not vectors:
            return 0

        with _connect(self._path) as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO embeddings (chunk_id, dimension, vector) VALUES (?, ?, ?)",
                [(chunk_id, len(vector), _pack(vector)) for chunk_id, vector in vectors],
            )
            connection.commit()
        return len(vectors)

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        """Record which model produced the vectors.

        A query embedded by a different model than the corpus produces confident
        nonsense -- the vectors are comparable arithmetically and meaningless
        semantically. Storing the model is what lets that be refused instead.
        """
        with _connect(self._path) as connection:
            connection.execute(
                "UPDATE index_metadata SET embedding_model = ?, embedding_dimension = ? "
                "WHERE id = 1",
                (model_id, dimension),
            )
            connection.commit()

    # -- Reading ----------------------------------------------------------

    def metadata(self) -> dict[str, object]:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM index_metadata WHERE id = 1").fetchone()
            # Converted inside the block, like every other read here. `dict()`
            # over a `sqlite3.Row` cannot fail today; the rule is uniform so that
            # the next conversion added to a read does not have to be noticed.
            return dict(row) if row else {}

    def schema_version(self) -> int:
        """The index schema this file was written with, or 0 if unknowable.

        0 covers an unreadable file, a missing metadata row, and a database that
        is not an index at all. Callers treat it exactly as they treat a
        mismatch, because operationally it means the same thing: this build
        cannot search this file, and the remedy is a rebuild.

        Never raises, and that had stopped being true. `int()` sat outside the
        block below, so a corrupt cell where the version belongs — TEXT, a blob,
        anything `int()` will not take — reached the caller as a `ValueError`
        while this line promised it could not. `theurian index status` repeats the
        promise (`_index_schema_version`, "never raises") and both surfaces broke
        together. The index is derived (ADR-0004), so a caller asking whether it
        is usable must get an answer rather than an exception.

        Reads through :meth:`_read` like every other read and then converts
        its refusal, rather than naming an exception type here. One place decides
        what a broken index looks like; this is the one caller that answers with
        a value instead of propagating it.
        """
        try:
            with self._read() as connection:
                row = connection.execute(
                    "SELECT index_schema_version FROM index_metadata WHERE id = 1"
                ).fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except IndexUnreadableError:
            return 0

    def is_searchable(self) -> bool:
        """Whether this file's schema is the one this build queries.

        Checked *before* searching, because after searching it is too late to
        tell the difference: a schema mismatch removes tables, and a query
        against a missing table fails in a way that looks like an empty result.

        The version was written into every index from the first build and read
        by nothing until now, which is how a bump could ship without anyone
        noticing that old files kept being searched.

        Not repaired, and not migrated. ADR-0004 makes the index a derived
        artifact: the honest response to a version it does not understand is to
        say so and name the rebuild, not to attempt an upgrade path that would
        have to be maintained forever for a file that costs seconds to recreate.
        """
        return self.schema_version() == INDEX_SCHEMA_VERSION

    def chunk_count(self) -> int:
        with self._read() as connection:
            return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def chunk_texts(self, chunk_ids: Sequence[str], *, project_id: str) -> dict[str, str]:
        """The matched passage per chunk.

        Returned so a hit can show *what* matched rather than the head of the
        document it came from. Chunking buys ranking precision; without this the
        caller never sees the paragraph it bought.

        Text rather than rows. Handing a ``sqlite3.Row`` to the application layer
        would couple the ranking pipeline to this adapter's cursor semantics and
        column names -- coupling no import check can catch.

        Project-scoped as defence in depth (SEC-13). Every id reaching here came
        from a search this class already scoped, so the filter should match
        everything -- and "should" is what a scoping bug sounds like in the
        moment before it becomes a cross-project disclosure of knowledge text.
        The cost is one indexed predicate on a lookup of at most `limit` rows.
        """
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        with self._read() as connection:
            rows = connection.execute(
                "SELECT chunk_id, text FROM chunks "  # noqa: S608 - placeholders only
                f"WHERE project_id = ? AND chunk_id IN ({placeholders})",
                (project_id, *chunk_ids),
            ).fetchall()
            # `str()` on the key as well as the value: a corrupt row can hold an
            # INTEGER where the schema declares TEXT, and a key of a type the
            # caller never looks up is a passage silently missing from a result.
            return {str(row["chunk_id"]): str(row["text"]) for row in rows}

    def texts(self, chunk_ids: Sequence[str], *, project_id: str) -> dict[str, sqlite3.Row]:
        """Fetch whole chunk rows by id. For adapters and tests, not for the
        application layer -- see :meth:`chunk_texts` for why rows stop here.

        Off the port deliberately: this is how a test reads a column no caller
        needs, without that column becoming a capability the port claims.

        Project-scoped like :meth:`chunk_texts`, and for a reason "adapters and
        tests only" does not cover. That phrase describes today's callers, not
        the method: this is a public name on a public class returning *every*
        column of any chunk id handed to it, and the next caller to want a
        column the port does not carry gets a cross-project read for free
        (SEC-13). Requiring the scope makes that caller state which project it
        is entitled to, at the cost of one indexed predicate.
        """
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM chunks "  # noqa: S608 - placeholders only
                f"WHERE project_id = ? AND chunk_id IN ({placeholders})",
                (project_id, *chunk_ids),
            ).fetchall()
            return {str(row["chunk_id"]): row for row in rows}

    def search_lexical(
        self,
        query: str,
        *,
        project_id: str,
        limit: int = 50,
        include_unapproved: bool = False,
    ) -> RetrieverPage:
        """Rank chunks by BM25 (FR-R2).

        Filters run in the same statement as the match, so an unapproved or
        out-of-project chunk is never ranked in the first place (FR-R1).

        **``LIMIT ? + 1``, and the extra row is never returned.** ``limit`` is a
        true ceiling on this port, so exactly ``limit`` rows is the one answer a
        row count cannot interpret: it is either the whole match set or a
        truncation. Fetching one row past the ceiling answers that question
        directly, and the answer is what the caller reads instead of guessing.
        A `LIMIT` on an FTS5 query bounds the rows returned and not the index
        walked, so the extra row costs a row and not a pass.
        """
        _require_a_positive_limit(limit)
        expression = to_match_expression(query)
        if not expression:
            return RetrieverPage(rows=(), exhausted=True)

        clauses, parameters = self._scope(project_id, include_unapproved)

        # The f-string interpolates only literals this module wrote (see
        # `_scope`); every user-supplied value is a bound parameter.
        sql = (
            "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, "  # noqa: S608 - clauses are module-owned literals; values are bound
            "  bm25(chunks_fts) AS rank_score "
            # CROSS JOIN, which SQLite honours as an ordering instruction. With a
            # plain JOIN the FR-R1 predicates on `chunks` make the planner drive
            # from `chunks` and re-run MATCH per candidate row: measured at 20,000
            # chunks, a 64-term query took 235 seconds. Forcing FTS5 to lead takes
            # the same query to 0.25 seconds, with identical results.
            "FROM chunks_fts CROSS JOIN chunks ON chunks.rowid = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND {' AND '.join(clauses)} "
            # Ties break on chunk id so two runs agree, matching the dense
            # side. BM25 ties are common among short chunks, and a tie
            # straddling the LIMIT boundary changes which rows survive.
            "ORDER BY rank_score, chunks.chunk_id LIMIT ?"
        )
        # No relevance floor on the score below, deliberately, and this is a known
        # gap. A review reported that BM25 returns "exactly 0.0000" for a hit
        # whose only matching terms appear in every row, and proposed excluding
        # those. Measured, SQLite returns -1.375e-06 for that case, not zero --
        # the 0.0000 was a printed rounding. A score threshold therefore excludes
        # nothing, and a floor that excludes nothing while claiming to be a floor
        # is worse than none. Separating "matched only common words" from
        # "matched weakly" needs a per-term IDF test, not a threshold on the
        # combined score. Recorded as outstanding rather than papered over, and
        # filed for Milestone 6 as
        # https://github.com/theurian/theurian/issues/21.
        try:
            with self._read() as connection:
                rows = connection.execute(sql, (expression, *parameters, limit + 1)).fetchall()
                return _page(_ranked(rows, _bm25), limit)
        except _QueryExpressionError:
            # Unreachable: sanitising cannot produce a malformed expression any
            # more. Kept as the guard that catches it again if sanitising is ever
            # relaxed -- `_is_query_expression_error` documents why, and names the
            # tests that hold the invariant in its place. Everything else `_read`
            # raises is the file's problem and must not be answered with an empty
            # page, which is indistinguishable from "nothing matched".
            return RetrieverPage(rows=(), exhausted=True)

    def search_substring(
        self,
        query: str,
        *,
        project_id: str,
        limit: int = 50,
        include_unapproved: bool = False,
    ) -> RetrieverPage:
        """Rank by trigram substring match.

        The retriever that makes Japanese searchable. `unicode61` turns a
        Japanese sentence into a single token, so `トークン` never matches
        `署名付きトークン` and the whole knowledge base is invisible to search.

        Kept beside the word index rather than replacing it: trigrams are worse
        at exact terms, which is what engineering queries are mostly made of.
        Both feed the fusion, and agreement between them is meaningful in the way
        agreement between two lexical strategies can be.

        ``limit`` binds the lookup below and not the scan the short-query branch
        falls through to, which is why the port documents it as a floor rather
        than a ceiling. There is no `LIMIT` to bind there:
        :func:`~theurian.infrastructure.sqlite.index_scan.scan_statement` has to
        score every matching row before it can name the best of them, so that
        branch hands back everything and reports itself exhausted on its first
        and only call. The lookup below is a ceiling like `search_lexical`'s and
        resolves its own exhaustion the same way, by asking for one row more than
        it will return.
        """
        _require_a_positive_limit(limit)
        expression = to_trigram_expression(query)
        if not expression:
            # Not "nothing matched". The trigram floor is a property of the
            # index, not of the query, so falling through it is the normal path
            # for a short query rather than a special case -- see
            # `_scan_below_the_trigram_floor`.
            return self._scan_below_the_trigram_floor(
                query,
                project_id=project_id,
                include_unapproved=include_unapproved,
            )

        clauses, parameters = self._scope(project_id, include_unapproved)
        sql = (
            "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, "  # noqa: S608 - clauses are module-owned literals; values are bound
            "  bm25(chunks_trigram) AS rank_score "
            "FROM chunks_trigram CROSS JOIN chunks ON chunks.rowid = chunks_trigram.rowid "
            f"WHERE chunks_trigram MATCH ? AND {' AND '.join(clauses)} "
            "ORDER BY rank_score, chunks.chunk_id LIMIT ?"
        )
        try:
            with self._read() as connection:
                rows = connection.execute(sql, (expression, *parameters, limit + 1)).fetchall()
                return _page(_ranked(rows, _bm25), limit)
        except _QueryExpressionError:
            # Unreachable, and kept for the same reason as in `search_lexical`.
            # What must *not* be caught here is anything else `_read` raises:
            # swallowing it is how a v1 index -- one with no `chunks_trigram` at
            # all -- reported zero hits for every Japanese query while the
            # response still claimed to be answering from an index. `unicode61`
            # cannot segment CJK, so this retriever is the *only* one that can
            # answer at all for such a corpus, and its silence was total.
            return RetrieverPage(rows=(), exhausted=True)

    def _scan_below_the_trigram_floor(
        self,
        query: str,
        *,
        project_id: str,
        include_unapproved: bool,
    ) -> RetrieverPage:
        """Answer a query too short to form a trigram, by scanning instead.

        **Why the floor exists.** A trigram index stores three-character grams,
        so a two-character term has no gram to look up, and including one makes
        FTS5 answer nothing for the *whole* expression — which is why
        :func:`~theurian.infrastructure.sqlite.index_query.to_trigram_expression`
        drops it rather than spending it.

        **Why falling through it is correct, and not a special case.** The floor
        describes this index's storage, not the caller's question. `unicode61`
        cannot segment CJK, so for a Japanese corpus this retriever is the only
        one that can answer at all — and 認証, 決済, 監査, 契約 are two characters
        each, the most common noun length in the language. Dropping the term did
        not degrade such an answer, it deleted it: `theurian index build`, the
        documented operation, made search strictly worse than having no index,
        and reported `count: 0, indexed: true` with no fallback reason. An agent
        reads that as "no such decision exists".

        The evidence was never missing — `chunks.text` holds it — only
        unreachable by this one path. So the path changes and the contract does
        not: the same rows, the same :meth:`_scope` filter (FR-R1, SEC-13), and
        an order that is total and therefore reproducible (FR-R7).

        **No `limit`, which is the one place this branch does differ from the
        lookup above it, and the reason is `search_dense`'s.** The ordering key
        has to be evaluated over every matching row before the best of them can
        be named, so a `LIMIT` here bounded the rows handed back and not the work
        done: measured on 6,000 chunks, `limit=100` cost 0.055s and
        `limit=12,800` cost 0.064s. A caller that believed the limit bounded the
        work would re-ask at greater depth to look past rows the canonical store
        has withdrawn, and pay for the whole corpus again — which is exactly what
        :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
        did, six times, for 3.06s against the 0.51s a single scan costs.

        Reached only when *every* term is short. A short term mixed with a longer
        one is still dropped and still unsearched, which is a smaller, deliberate
        residual — `index_query.to_trigram_expression` says why it is left open.

        **What this method keeps is the file, not the query.** The statement, the
        ordering that makes the caller's cut select on relevance rather than on
        age, how many of the query's terms it can afford to spend, and what that
        costs are all in
        :func:`~theurian.infrastructure.sqlite.index_scan.scan_statement` — the
        last of those bounds recall as well as cost, and
        :data:`~theurian.infrastructure.sqlite.index_scan.SCAN_TERMS` says by how
        much. The connection, the scope filter and the error mapping stay here
        because they are decisions about *this index*, and answering a
        file-shaped complaint with `()` is the mistake the two branches above
        exist to avoid.

        ADR-0023's cost objection to `LIKE` compared it only against the trigram
        lookup, not against `substring_answer`, the alternative that actually
        runs whenever no index can answer. That comparison has since been made,
        and it does not say what this docstring used to: `substring_answer` is
        *cheaper* — about half at equal row counts — and it is not the same
        match, since it tests the whole query as one literal substring where
        this branch runs an up-to-eight-term OR with a relevance order. It also
        costs two queries per document rather than one. What it does not do is
        release the GIL. See
        :func:`~theurian.infrastructure.sqlite.index_scan.scan_statement` for
        the measurements and T-6 in `docs/security/threat-model.md` for why the
        more expensive member is still the right one here.

        **Exhausted on its first and only call, and that is what deleted a
        cache.** Having read and scored every matching row, this method has by
        construction nothing further to give, whatever the corpus and whatever
        the canonical store has since withdrawn. It says so, and
        `_visible_ranking` stops.

        Until `IndexStore` carried that signal the loop inferred exhaustion from
        a row count, which is exactly wrong here: the whole ranking totalling
        precisely `FIRST_PASS_DEPTH` rows is indistinguishable from a truncation,
        so the loop asked again -- one call for every withheld count from 0 to
        50, two for every count from 51 to 99, a step function of the withheld
        count with a sharp edge. A `_scan_cache` field memoised the second call
        so that it cost no second pass over the corpus (29.17 ms for two
        independent scans against 14.04 ms for one, on a 6,000-row corpus shaped
        to sit on that edge), and its own docstring called it a mitigation for
        one named defect rather than an optimisation, to be deleted when the
        defect was fixed at its cause. It has been. There is no second call left
        to make cheap, so the property the cache was holding -- one search, one
        pass over the corpus, however many rows were withheld -- is now a
        consequence of this method's contract instead of a memo standing in front
        of a wrong inference.

        The cache also imposed a rule on callers: *construct a fresh
        `SqliteIndexStore` per search*, because a pooled one would have leaked
        one caller's withheld-row count into another caller's latency through the
        memo. That rule went with it. This store holds no cross-request state at
        all now -- which is not the same as saying it may be pooled, only that
        this is no longer the reason it may not be.
        """
        terms = to_scan_terms(query)
        if not terms:
            # Every term was too short to be worth a pass over the corpus --
            # see `index_query._is_worth_scanning`. Not "nothing matched": the
            # word index answers a single Latin letter as a word, which is the
            # only sense in which it is a query.
            return RetrieverPage(rows=(), exhausted=True)

        clauses, parameters = self._scope(project_id, include_unapproved)
        sql, arguments = scan_statement(terms, clauses=clauses, scope=parameters)
        # No query-shaped carve-out: this branch matches with `LIKE` over bound
        # parameters, so no caller text is ever parsed as an expression and every
        # complaint SQLite can make here is about the file -- `chunks` absent, a
        # truncated copy, something that is not an index at all.
        with self._read() as connection:
            rows = connection.execute(sql, arguments).fetchall()
            return RetrieverPage(rows=_ranked(rows, _scan_score), exhausted=True)

    def _scope(self, project_id: str, include_unapproved: bool) -> tuple[list[str], list[object]]:
        """Project and status, shared by every retriever so none can forget it.

        **Not "the FR-R1 filter", which is what this said until #63.** FR-R1 is
        *filter by Project, tenant, ACL, sensitivity, and validity window before
        ranking*; the two clauses below are Project, plus a status check FR-R1
        does not name. `chunks` carries `sensitivity`, `trust_level` and
        `namespace`, and no query reads any of them -- the schema says so beside
        the columns, and this docstring said the opposite 850 lines later, which
        is the version a reader inspecting the filter would have found.

        Every retriever means every retriever: lexical, substring, and dense all
        build their WHERE clause from here. That was not true when this docstring
        was first written -- only the substring retriever called it, while the
        other two assembled the same two predicates by hand -- and the gap was
        found by mutation: the cross-project isolation test only failed when all
        three copies were broken at once, so any single copy could have lost its
        `project_id` predicate with the suite still green.

        A comment claiming a single point of enforcement is worse than no comment
        when there are three, because it tells the next reader this is already
        handled. It is now, and this is the one place to change it (SEC-13) --
        which is the same reason the paragraph above names the axes this does
        *not* filter on.
        """
        clauses = ["chunks.project_id = ?"]
        parameters: list[object] = [project_id]
        if not include_unapproved:
            clauses.append("chunks.status = ?")
            parameters.append(KnowledgeStatus.APPROVED.value)
        return clauses, parameters

    def search_dense(
        self,
        query_vector: Sequence[float],
        *,
        project_id: str,
        include_unapproved: bool = False,
    ) -> RetrieverPage:
        """Rank chunks by cosine similarity, by exact scan.

        Exact rather than approximate: a local knowledge base is thousands of
        chunks, where a full scan is both fast enough and exactly reproducible.
        An ANN index would trade reproducibility -- which FR-R7 requires -- for a
        speed-up nobody here can measure.

        Returns the whole ranking. It carried a `limit` and the parameter was a
        fiction: the scan below reads and scores every embedding in the project
        before the slice, so the cost was identical at 50 and at 12,800 and only
        the number of rows handed back changed. Nothing is saved by cutting here,
        and the caller -- which has to look past rows the canonical store has
        withdrawn -- would otherwise re-run the whole scan to see one row more.
        The peak memory is unchanged either way: `fetchall` already holds every
        vector.

        Always exhausted, and truthfully so: the whole ranking is what comes
        back, so there is never anything further for the caller to ask for.
        """
        if not query_vector:
            return RetrieverPage(rows=(), exhausted=True)

        clauses, parameters = self._scope(project_id, include_unapproved)

        sql = (
            "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, embeddings.vector "  # noqa: S608 - clauses are module-owned literals; values are bound
            "FROM embeddings JOIN chunks ON chunks.chunk_id = embeddings.chunk_id "
            f"WHERE {' AND '.join(clauses)}"
        )
        # This retriever once had no guard at all, which made `hybrid_answer`'s
        # claim -- "never answer from a broken index", even where the version gate
        # cannot -- false for `useDense=true`. An index whose metadata row outlived
        # its `embeddings` table reached the agent as a bare `no such table:
        # embeddings`: the MCP fallback keys on `IndexBuildError`, and a raw
        # `OperationalError` is not one. Both halves of that are held, in
        # `tests/integration/test_index_fallback.py`:
        # `test_a_dense_search_over_a_lost_embeddings_table_raises_too` for the
        # raise and
        # `test_a_dense_query_over_a_broken_index_falls_back_rather_than_failing`
        # for what the agent then receives.
        #
        # No query-shaped carve-out here either. This retriever takes a vector, so
        # no caller text reaches SQL.
        #
        # Scored inside the block rather than after it. Unpacking a blob is an
        # interpretation of this file's bytes exactly as `execute` is, and it ran
        # outside the connection's lifetime while the guard that answers for such
        # bytes ended with the connection -- see `_UNREADABLE_VALUES`.
        with self._read() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
            return RetrieverPage(rows=_dense_ranking(rows, query_vector), exhausted=True)


def _dense_ranking(
    rows: Sequence[sqlite3.Row], query_vector: Sequence[float]
) -> tuple[Ranked, ...]:
    """Cosine-rank fetched rows against a query vector.

    Split out of :meth:`SqliteIndexStore.search_dense` so the whole of it fits
    inside one ``with self._read()`` block without nesting the scan loop under a
    connection by hand.
    """
    query_norm = math.sqrt(sum(value * value for value in query_vector))
    if query_norm == 0.0:
        return ()

    scored: list[Ranked] = []
    for row in rows:
        vector = _stored_vector(row["vector"], len(query_vector))
        if vector is None:
            continue
        similarity = _cosine(query_vector, vector, query_norm)
        if similarity < DENSE_SIMILARITY_FLOOR:
            continue
        scored.append(
            Ranked(
                chunk_id=str(row["chunk_id"]),
                item_id=str(row["item_id"]),
                revision_id=str(row["revision_id"]),
                score=similarity,
            )
        )

    # Ties break on chunk id so two runs agree (FR-R7).
    scored.sort(key=lambda ranked: (-ranked.score, ranked.chunk_id))
    return tuple(scored)


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(_VECTOR_FORMAT % len(vector), *vector)


def _stored_vector(blob: object, width: int) -> tuple[float, ...] | None:
    """One stored embedding, or ``None`` if these bytes are not one.

    **The width check moved in front of the unpack, and that is the whole of it.**
    `search_dense` already skipped a vector whose length disagreed with the
    query's -- a corpus embedded by a different model, where the arithmetic would
    succeed and the meaning would not -- but it measured that length *after*
    `_unpack` had run. Measured against a real index with `embeddings.vector`
    overwritten: two bytes gives `unpack requires a buffer of 0 bytes`, nine bytes
    gives `8 bytes`, TEXT gives `a bytes-like object is required, not 'str'`, and
    an INTEGER gives `object of type 'int' has no len()`. None of those is a
    `sqlite3` exception and the `dimension` column that would have caught them is
    recorded by `add_embeddings` and read by nothing.

    So the same rows are skipped the same way, and one more class joins them: a
    blob that cannot be a float32 vector of this width at all. `_read` still
    covers what is left, because a guard that depends on this function staying
    exhaustive is the enumeration that failed twice already.
    """
    if not isinstance(blob, bytes):
        return None
    if len(blob) % _FLOAT32_BYTES != 0 or len(blob) // _FLOAT32_BYTES != width:
        return None
    return _unpack(blob)


def _unpack(blob: bytes) -> tuple[float, ...]:
    count = len(blob) // _FLOAT32_BYTES
    return struct.unpack(_VECTOR_FORMAT % count, blob)


def _ranked(
    rows: Sequence[sqlite3.Row], score: Callable[[sqlite3.Row], float]
) -> tuple[Ranked, ...]:
    """Retriever rows as :class:`Ranked`, converted where the guard still reaches.

    Called from inside a ``with self._read()`` block by every retriever, which is
    what it exists for: this is the conversion that used to happen after the
    connection had closed, so a NULL where a score belongs reached an agent as
    ``float() argument must be a string or a real number, not 'NoneType'``.

    Ids are coerced with `str()` rather than passed through, which closes a
    hazard rather than a measured escape and is recorded as the smaller claim it
    is. `Ranked` declares them `str` and validates nothing, so an INTEGER in a
    corrupt `chunk_id` cell leaves this module as an `int` and is next compared
    with a neighbouring `str` in a tie-break -- `search_dense`'s `scored.sort`,
    and fusion's. Driving that from a real index (`UPDATE chunks SET chunk_id =
    42`) did not reach it, because a tie-break only runs on an exact score tie;
    the value simply travelled. A coerced id that names nothing fails to match
    in :class:`~theurian.application.visibility.CanonicalVisibility`, which drops
    the row -- the direction a value read out of a derived file should fail in.
    """
    return tuple(
        Ranked(
            chunk_id=str(row["chunk_id"]),
            item_id=str(row["item_id"]),
            revision_id=str(row["revision_id"]),
            score=score(row),
        )
        for row in rows
    )


def _require_a_positive_limit(limit: int) -> None:
    """Refuse a `limit` below 1, at the entry rather than in the slice.

    `_page` cuts with `ranked[:limit]`, and a negative `limit` makes that a
    from-the-end slice: measured on an 8-row match set before this guard existed,
    `limit=-2` returned **6 rows** with `exhausted=False` -- more rows than
    `limit`, which the port calls a true ceiling, and a claim that more was
    coming. `limit=0` and `limit=-1` instead reached `RetrieverPage`'s invariant
    and raised `RankingError`, whose message tells the reader to fix the adapter
    when the adapter is right and the argument is wrong.

    Unreachable through the documented API -- `_visible_ranking` starts at
    `FIRST_PASS_DEPTH` and only doubles -- so this refuses a caller bug rather
    than a user's input, and `ValueError` is the shape for that here
    (`index_scan.scan_statement`, `ports.source_parser`). It is deliberately not
    a `TheurianError`: those carry a remedy a person can run, and no `theurian`
    command produces this.
    """
    if limit < 1:
        msg = (
            f"limit must be at least 1, got {limit}. IndexStore treats it as a row "
            f"count from the top of a best-first ranking, so there is no reading of "
            f"zero or fewer."
        )
        raise ValueError(msg)


def _page(ranked: tuple[Ranked, ...], limit: int) -> RetrieverPage:
    """A page from a ``LIMIT ? + 1`` fetch: the extra row answers, it never ships.

    Both `LIMIT`-bearing retrievers ask for one row past their ceiling. Its
    presence is the whole of what "is there anything further" means for them, and
    dropping it again is what keeps ``limit`` a true ceiling on the port. Written
    once rather than at each call site, because the two halves -- probe by one,
    cut by one -- have to move together or the ceiling leaks a row.

    Sound only for ``limit >= 1``, which `_require_a_positive_limit` establishes
    at each entry. Given that, `ranked[:limit]` is a genuine prefix and an empty
    page is always exhausted, so this function cannot construct the state
    `RetrieverPage` refuses -- and that refusal is left for the defective
    *adapter* its message actually describes.
    """
    return RetrieverPage(rows=ranked[:limit], exhausted=len(ranked) <= limit)


def _bm25(row: sqlite3.Row) -> float:
    """FTS5's score, negated.

    bm25() returns a *negative* score where more negative is better. Only the
    resulting order is used downstream; RRF never compares this number with a
    cosine similarity, which
    `tests/unit/test_ranking.py::test_fusion_uses_rank_not_score` holds.
    """
    return -float(row["rank_score"])


def _scan_score(row: sqlite3.Row) -> float:
    """How much of the query this row accounts for -- see
    :func:`~theurian.infrastructure.sqlite.index_scan.scan_statement`.

    NULL here is reachable and is why this conversion had to move inside the
    guard: the key is a sum over `length(lower(chunks.text))` and
    `length(lower(chunks.heading))`, and a NULL in either column makes the whole
    sum NULL rather than zero.
    """
    return float(row["matched_characters"])


def _cosine(left: Sequence[float], right: Sequence[float], left_norm: float) -> float:
    right_norm = math.sqrt(sum(value * value for value in right))
    if right_norm == 0.0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


#: `MAX_QUERY_CHARS` and `MAX_QUERY_TERMS` are re-exported rather than merely
#: imported. They moved to `index_query` with the code that enforces them, but
#: they are read from here by callers who think of them as properties of the
#: store's query surface, and breaking those imports would be churn with no
#: reader-visible benefit.
__all__ = [
    "MAX_QUERY_CHARS",
    "MAX_QUERY_TERMS",
    "Fts5UnavailableError",
    "IndexBuildError",
    "IndexUnreadableError",
    "IndexableChunk",
    "SqliteIndexStore",
    "fts5_available",
]
