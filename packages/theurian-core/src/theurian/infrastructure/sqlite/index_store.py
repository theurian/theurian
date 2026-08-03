"""Building and querying the retrieval index (FR-R1, FR-R2).

Two retrievers over one file: FTS5 for terms and an exact vector scan for
meaning. Both return *ranked lists*, never scores to be compared across
retrievers — fusion happens in :mod:`theurian.domain.ranking`, which is where it
can be reasoned about without a database.

Filtering happens in SQL, before ranking (FR-R1). Filtering after ranking would
let a caller infer that a document they may not read exists, by noticing how many
results vanished.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.chunking import IndexableChunk
from theurian.domain.enums import KnowledgeStatus
from theurian.domain.errors import TheurianError
from theurian.domain.ranking import Ranked, estimate_tokens
from theurian.infrastructure.sqlite.index_schema import (
    FTS5_PROBE,
    INDEX_DDL,
    INDEX_SCHEMA_VERSION,
)
from theurian.infrastructure.sqlite.schema import CONNECTION_PRAGMAS

#: Little-endian float32. Fixed rather than native so an index built on one
#: machine reads correctly on another -- the file is derived, but it is also
#: copied between machines more often than anyone plans for.
_VECTOR_FORMAT: Final = "<%df"

#: Characters that mean something to FTS5's query syntax. A user searching for
#: `auth OR "token"` means those as words, not as operators, and a query that
#: raised a syntax error at them would be a search box that punishes punctuation.
_FTS_SPECIAL: Final = '"*():^-'

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

#: Bounds on what one query may cost. FTS5's cost is roughly quadratic in term
#: count and linear in corpus size: measured against 2,000 chunks, a 500-term
#: query took 8.7 seconds and a 2,000-term query did not finish inside a minute.
#:
#: That is not merely slow. The MCP SDK runs synchronous tools on a 40-thread
#: pool, and `sqlite3` releases the GIL, so a handful of such queries saturate
#: the CPU and every tool call for *every project this daemon serves* waits
#: behind them. A query is attacker-influenceable — an agent composes it after
#: reading content — so it gets the same input bounds as any other parser
#: input (SEC-8).
MAX_QUERY_CHARS: Final = 2_000
MAX_QUERY_TERMS: Final = 64


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
            f"most likely built by an older version of Theurian. Run `theurian "
            f"index build` to rebuild it; the index is derived, so nothing is lost."
        )


#: Fragments of a SQLite message that mean *the file* is wrong, not the query.
#: A missing table is the shape an older index schema takes; the rest are the
#: shapes a truncated or corrupted copy takes.
_UNREADABLE_INDEX_ERRORS: Final = (
    "no such table",
    "no such column",
    "no such module",
    "malformed",
    "file is not a database",
)


def _index_is_unreadable(exc: sqlite3.Error) -> bool:
    """Whether SQLite is complaining about the index rather than the query.

    The two must not share a branch. A query-shaped complaint returns nothing,
    because a search box that raises at an unbalanced quote is a broken search
    box. A file-shaped complaint must never return nothing, because "no results"
    is exactly what a caller cannot distinguish from a correct empty answer.
    """
    message = str(exc).lower()
    return any(fragment in message for fragment in _UNREADABLE_INDEX_ERRORS)


def _is_query_syntax_error(exc: sqlite3.Error) -> bool:
    """Whether FTS5 rejected the *expression* — a case that can no longer happen.

    **This predicate is unreachable today, and that is recorded rather than
    hidden.** Nothing this module can now build is a malformed FTS5 expression:

    - :func:`_to_match_expression` deletes every ``"`` and strips the operator
      characters from each term's edges, then wraps each term in quotes — so what
      reaches FTS5 is a list of phrases whose only syntactically significant
      character cannot be present;
    - :func:`_is_transportable` removes the two things that broke the string
      before FTS5 ever parsed it: a NUL and a lone surrogate.

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

    **What actually holds the invariant is the sanitising, not this branch.**
    Anyone changing `_to_match_expression`, `_query_terms`, or
    `_is_transportable` is changing the thing under test, and the tests that
    fail are `test_punctuation_never_raises_at_the_user`,
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
    message = str(exc).lower()
    return "fts5" in message or "syntax" in message


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
    """A configured connection that is always closed.

    ``sqlite3.connect`` as a context manager commits or rolls back but does not
    close, which leaks a handle per call -- caught in Milestone 1 and worth not
    repeating.
    """
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        for pragma in CONNECTION_PRAGMAS:
            connection.execute(pragma)
        yield connection
    finally:
        connection.close()


@final
class SqliteIndexStore:
    """Writes and reads one index build."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

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
        with _connect(self._path) as connection:
            row = connection.execute("SELECT * FROM index_metadata WHERE id = 1").fetchone()
        return dict(row) if row else {}

    def schema_version(self) -> int:
        """The index schema this file was written with, or 0 if unknowable.

        0 covers an unreadable file, a missing metadata row, and a database that
        is not an index at all. Callers treat it exactly as they treat a
        mismatch, because operationally it means the same thing: this build
        cannot search this file, and the remedy is a rebuild.

        Never raises. The index is derived (ADR-0004), so a caller asking whether
        it is usable must get an answer rather than an exception.
        """
        try:
            with _connect(self._path) as connection:
                row = connection.execute(
                    "SELECT index_schema_version FROM index_metadata WHERE id = 1"
                ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row[0]) if row and row[0] is not None else 0

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
        with _connect(self._path) as connection:
            return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def token_sizes(self, chunk_ids: Sequence[str], *, project_id: str) -> dict[str, int]:
        """Token estimate per chunk, for packing to a budget (FR-R4).

        Sizes rather than rows. Handing a ``sqlite3.Row`` to the application
        layer would couple the ranking pipeline to this adapter's cursor
        semantics and column names -- coupling no import check can catch.

        Project-scoped as defence in depth (SEC-13). Every id reaching here came
        from a search this class already scoped, so the filter should match
        everything -- and "should" is what a scoping bug sounds like in the
        moment before it becomes a cross-project disclosure. The cost is one
        indexed predicate on a lookup of at most `limit` rows.
        """
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        with _connect(self._path) as connection:
            rows = connection.execute(
                "SELECT chunk_id, token_estimate FROM chunks "  # noqa: S608 - placeholders only
                f"WHERE project_id = ? AND chunk_id IN ({placeholders})",
                (project_id, *chunk_ids),
            ).fetchall()
        return {row["chunk_id"]: int(row["token_estimate"]) for row in rows}

    def chunk_texts(self, chunk_ids: Sequence[str], *, project_id: str) -> dict[str, str]:
        """The matched passage per chunk.

        Returned so a hit can show *what* matched rather than the head of the
        document it came from. Chunking buys ranking precision; without this the
        caller never sees the paragraph it bought.

        Project-scoped for the reason :meth:`token_sizes` gives, and with more at
        stake: this one returns knowledge text rather than a number.
        """
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        with _connect(self._path) as connection:
            rows = connection.execute(
                "SELECT chunk_id, text FROM chunks "  # noqa: S608 - placeholders only
                f"WHERE project_id = ? AND chunk_id IN ({placeholders})",
                (project_id, *chunk_ids),
            ).fetchall()
        return {row["chunk_id"]: str(row["text"]) for row in rows}

    def texts(self, chunk_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        """Fetch whole chunk rows by id. For adapters and tests, not for the
        application layer -- see :meth:`token_sizes`."""
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        with _connect(self._path) as connection:
            rows = connection.execute(
                f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",  # noqa: S608 - placeholders only
                tuple(chunk_ids),
            ).fetchall()
        return {row["chunk_id"]: row for row in rows}

    def search_lexical(
        self,
        query: str,
        *,
        project_id: str,
        limit: int = 50,
        include_unapproved: bool = False,
    ) -> tuple[Ranked, ...]:
        """Rank chunks by BM25 (FR-R2).

        Filters run in the same statement as the match, so an unapproved or
        out-of-project chunk is never ranked in the first place (FR-R1).
        """
        expression = _to_match_expression(query)
        if not expression:
            return ()

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
        with _connect(self._path) as connection:
            try:
                rows = connection.execute(sql, (expression, *parameters, limit)).fetchall()
            except sqlite3.OperationalError as exc:
                # An index this build cannot read is not a query problem, and
                # answering it with `()` would be indistinguishable from "nothing
                # matched". Checked first, so no query-shaped guard below can
                # accidentally absorb it.
                if _index_is_unreadable(exc):
                    raise IndexUnreadableError(str(exc)) from exc
                # Unreachable: sanitising cannot produce a malformed expression
                # any more. Kept as the guard that catches it again if sanitising
                # is ever relaxed -- `_is_query_syntax_error` documents why, and
                # names the tests that hold the invariant in its place.
                if _is_query_syntax_error(exc):
                    return ()
                raise

        # bm25() returns a *negative* score where more negative is better, so it
        # is negated here. Only the resulting order is used downstream; RRF never
        # compares this number with a cosine similarity.
        # No relevance floor here, deliberately, and this is a known gap.
        #
        # A review reported that BM25 returns "exactly 0.0000" for a hit whose
        # only matching terms appear in every row, and proposed excluding those.
        # Measured, SQLite returns -1.375e-06 for that case, not zero -- the
        # 0.0000 was a printed rounding. A score threshold therefore excludes
        # nothing, and a floor that excludes nothing while claiming to be a floor
        # is worse than none.
        #
        # Separating "matched only common words" from "matched weakly" needs a
        # per-term IDF test, not a threshold on the combined score. Recorded as
        # outstanding rather than papered over.
        return tuple(
            Ranked(
                chunk_id=row["chunk_id"],
                item_id=row["item_id"],
                revision_id=row["revision_id"],
                score=-float(row["rank_score"]),
            )
            for row in rows
        )

    def search_substring(
        self,
        query: str,
        *,
        project_id: str,
        limit: int = 50,
        include_unapproved: bool = False,
    ) -> tuple[Ranked, ...]:
        """Rank by trigram substring match.

        The retriever that makes Japanese searchable. `unicode61` turns a
        Japanese sentence into a single token, so `トークン` never matches
        `署名付きトークン` and the whole knowledge base is invisible to search.

        Kept beside the word index rather than replacing it: trigrams are worse
        at exact terms, which is what engineering queries are mostly made of.
        Both feed the fusion, and agreement between them is meaningful in the way
        agreement between two lexical strategies can be.
        """
        expression = _to_trigram_expression(query)
        if not expression:
            return ()

        clauses, parameters = self._scope(project_id, include_unapproved)
        sql = (
            "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, "  # noqa: S608 - clauses are module-owned literals; values are bound
            "  bm25(chunks_trigram) AS rank_score "
            "FROM chunks_trigram CROSS JOIN chunks ON chunks.rowid = chunks_trigram.rowid "
            f"WHERE chunks_trigram MATCH ? AND {' AND '.join(clauses)} "
            "ORDER BY rank_score, chunks.chunk_id LIMIT ?"
        )
        with _connect(self._path) as connection:
            try:
                rows = connection.execute(sql, (expression, *parameters, limit)).fetchall()
            except sqlite3.OperationalError as exc:
                # This handler used to swallow everything, which is how a v1
                # index -- one with no `chunks_trigram` at all -- reported zero
                # hits for every Japanese query while the response still claimed
                # to be answering from an index. `unicode61` cannot segment CJK,
                # so this retriever is the *only* one that can answer at all for
                # such a corpus, and its silence was total.
                if _index_is_unreadable(exc):
                    raise IndexUnreadableError(str(exc)) from exc
                # Unreachable, and kept for the same reason as in
                # `search_lexical`. See `_is_query_syntax_error`.
                if _is_query_syntax_error(exc):
                    return ()
                raise

        return tuple(
            Ranked(
                chunk_id=row["chunk_id"],
                item_id=row["item_id"],
                revision_id=row["revision_id"],
                score=-float(row["rank_score"]),
            )
            for row in rows
        )

    def _scope(self, project_id: str, include_unapproved: bool) -> tuple[list[str], list[object]]:
        """The FR-R1 filter, shared by every retriever so none can forget it.

        Every retriever means every retriever: lexical, substring, and dense all
        build their WHERE clause from here. That was not true when this docstring
        was first written -- only the substring retriever called it, while the
        other two assembled the same two predicates by hand -- and the gap was
        found by mutation: the cross-project isolation test only failed when all
        three copies were broken at once, so any single copy could have lost its
        `project_id` predicate with the suite still green.

        A comment claiming a single point of enforcement is worse than no comment
        when there are three, because it tells the next reader this is already
        handled. It is now, and this is the one place to change it (SEC-13).
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
        limit: int = 50,
        include_unapproved: bool = False,
    ) -> tuple[Ranked, ...]:
        """Rank chunks by cosine similarity, by exact scan.

        Exact rather than approximate: a local knowledge base is thousands of
        chunks, where a full scan is both fast enough and exactly reproducible.
        An ANN index would trade reproducibility -- which FR-R7 requires -- for a
        speed-up nobody here can measure.
        """
        if not query_vector:
            return ()

        clauses, parameters = self._scope(project_id, include_unapproved)

        sql = (
            "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, embeddings.vector "  # noqa: S608 - clauses are module-owned literals; values are bound
            "FROM embeddings JOIN chunks ON chunks.chunk_id = embeddings.chunk_id "
            f"WHERE {' AND '.join(clauses)}"
        )
        with _connect(self._path) as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()

        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_norm == 0.0:
            return ()

        scored: list[Ranked] = []
        for row in rows:
            vector = _unpack(row["vector"])
            if len(vector) != len(query_vector):
                # A corpus embedded by a different model. Skipped rather than
                # scored: the arithmetic would succeed and the meaning would not.
                continue
            similarity = _cosine(query_vector, vector, query_norm)
            if similarity < DENSE_SIMILARITY_FLOOR:
                continue
            scored.append(
                Ranked(
                    chunk_id=row["chunk_id"],
                    item_id=row["item_id"],
                    revision_id=row["revision_id"],
                    score=similarity,
                )
            )

        # Ties break on chunk id so two runs agree (FR-R7).
        scored.sort(key=lambda ranked: (-ranked.score, ranked.chunk_id))
        return tuple(scored[:limit])


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(_VECTOR_FORMAT % len(vector), *vector)


def _unpack(blob: bytes) -> tuple[float, ...]:
    count = len(blob) // 4
    return struct.unpack(_VECTOR_FORMAT % count, blob)


def _cosine(left: Sequence[float], right: Sequence[float], left_norm: float) -> float:
    right_norm = math.sqrt(sum(value * value for value in right))
    if right_norm == 0.0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


#: Trigram matching needs at least three characters to form one gram.
_MIN_TRIGRAM_CHARS: Final = 3


def _is_transportable(term: str) -> bool:
    """Whether this term can be handed to SQLite as text at all (SEC-8).

    The contract with SQLite is not "a Python string"; it is *a NUL-terminated
    UTF-8 byte string*. Two kinds of `str` cannot become one, and both are
    reachable from a JSON-RPC caller because JSON can carry ``\\u0000`` and an
    unpaired ``\\ud800``:

    - a NUL ends the C string early, so FTS5 stops reading the MATCH expression
      mid-token and reports ``unterminated string``;
    - a lone surrogate cannot be encoded as UTF-8 at all, so the failure is a
      ``UnicodeEncodeError`` raised by the driver before SQLite is even called —
      which no ``except sqlite3.OperationalError`` could ever have caught.

    Both used to escape as a tool failure at the agent. Both are now a term this
    matcher declines to spend, which is the same answer punctuation already got.

    Stated as *this* property rather than as a list of bad characters, on
    purpose. A query is an arbitrary string chosen by something that has just
    read untrusted content, so the safe formulation is "what can cross this
    boundary", not "which characters have been observed to break it". Measured
    against the alternative: every other C0 and C1 control, ZWSP, the BOM, the
    non-characters U+FFFE/U+FFFF, and U+10FFFF all cross it intact and match
    nothing, so banning controls wholesale would have been a rule with no defect
    behind it.
    """
    if "\x00" in term:
        return False
    try:
        term.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _query_terms(query: str, *, min_length: int) -> list[str]:
    """The distinct terms a query is allowed to spend, longest first.

    De-duplicated, because a repeated term adds cost to the expression and
    changes nothing about the BM25 order: ``"token " * 2000`` collapses to one
    term rather than to a minute of CPU.

    **Longest first is a selection rule, not a display order.** When a query
    brings more distinct terms than :data:`MAX_QUERY_TERMS`, something has to be
    dropped, and taking the first N in the order they were typed is the worst
    available choice — an English question front-loads its least selective words
    ("how do we handle the ...") so the truncated query keeps `how`, `do`, `we`
    and discards the noun it was about. The caller believes they searched for
    that noun. Length is a cheap, tokenizer-free proxy for selectivity, and under
    an OR match a low-IDF term barely moves the BM25 order anyway.

    The alternative was to keep the typed order and report the truncation. That
    was rejected here rather than dismissed: the count would have to travel back
    through :class:`~theurian.domain.ports.index_store.IndexStore` to reach a
    client, widening the port for a condition a query must exceed 64 distinct
    terms to reach — while still answering the question worse than this does.

    Ties keep the order the user typed, because ``sorted`` is stable, so one
    query always produces one expression (FR-R7).

    This is the only place a caller-supplied string becomes SQL text — the dense
    retriever takes a vector, and the by-id reads take chunk ids the index
    itself produced — so it is also the only place that has to hold
    :func:`_is_transportable`. A term that cannot cross into SQLite is dropped
    rather than the whole query, so `auth token\\x00` still searches for `auth`.
    """
    unique: dict[str, None] = {}
    for word in query[:MAX_QUERY_CHARS].replace('"', " ").split():
        term = word.strip(_FTS_SPECIAL)
        if len(term) >= min_length and _is_transportable(term):
            unique.setdefault(term, None)
    return sorted(unique, key=lambda term: -len(term))[:MAX_QUERY_TERMS]


def _to_match_expression(query: str) -> str:
    """Turn user text into an FTS5 MATCH expression.

    Every term is quoted, so FTS5's operators cannot be reached from user input.
    That is a correctness measure as much as a security one: someone searching
    for `auth OR token` means three words, and a bare `-` or unbalanced `"` would
    otherwise raise a syntax error at a person who typed a perfectly ordinary
    sentence.

    Bounded in length, in distinct terms, and de-duplicated — see
    :data:`MAX_QUERY_CHARS` and :func:`_query_terms`.

    Terms are ORed and left to BM25 to rank. ANDing them requires every token to
    appear in one chunk -- including `how`, `do`, `for`, which the `unicode61`
    tokenizer does not treat as stop words -- so a natural-language question, the
    main thing an agent actually sends, matches nothing at all. Recall is BM25's
    problem to rank, not the matcher's problem to refuse.
    """
    return " OR ".join(f'"{term}"' for term in _query_terms(query, min_length=1))


def _to_trigram_expression(query: str) -> str:
    """Turn user text into a trigram MATCH expression.

    Terms shorter than a trigram are dropped rather than sent: FTS5 cannot match
    them against a trigram index, and including one makes the whole expression
    return nothing.
    """
    return " OR ".join(f'"{term}"' for term in _query_terms(query, min_length=_MIN_TRIGRAM_CHARS))


__all__ = [
    "Fts5UnavailableError",
    "IndexBuildError",
    "IndexUnreadableError",
    "IndexableChunk",
    "SqliteIndexStore",
    "fts5_available",
]
