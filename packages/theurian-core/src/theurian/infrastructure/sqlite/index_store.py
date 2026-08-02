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
from theurian.domain.errors import TheurianError
from theurian.domain.ranking import Ranked
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

        from theurian.domain.ranking import estimate_tokens  # noqa: PLC0415 - avoids a cycle

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

    def chunk_count(self) -> int:
        with _connect(self._path) as connection:
            return int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def texts(self, chunk_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        """Fetch chunk rows by id, for building results after ranking."""
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

        clauses = ["chunks.project_id = ?"]
        parameters: list[object] = [project_id]
        if not include_unapproved:
            clauses.append("chunks.status = 'approved'")

        # The f-string interpolates only literals this module wrote (see
        # `clauses` above); every user-supplied value is a bound parameter.
        sql = (
            "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, "  # noqa: S608 - clauses are module-owned literals; values are bound
            "  bm25(chunks_fts) AS rank_score "
            "FROM chunks_fts JOIN chunks ON chunks.rowid = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND {' AND '.join(clauses)} "
            "ORDER BY rank_score LIMIT ?"
        )
        with _connect(self._path) as connection:
            try:
                rows = connection.execute(sql, (expression, *parameters, limit)).fetchall()
            except sqlite3.OperationalError as exc:
                # A query that survived sanitising can still be rejected -- an
                # unbalanced quote, say. A search box must not raise at the user.
                if "fts5" in str(exc).lower() or "syntax" in str(exc).lower():
                    return ()
                raise

        # bm25() returns a *negative* score where more negative is better, so it
        # is negated here. Only the resulting order is used downstream; RRF never
        # compares this number with a cosine similarity.
        return tuple(
            Ranked(
                chunk_id=row["chunk_id"],
                item_id=row["item_id"],
                revision_id=row["revision_id"],
                score=-float(row["rank_score"]),
            )
            for row in rows
        )

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

        clauses = ["chunks.project_id = ?"]
        parameters: list[object] = [project_id]
        if not include_unapproved:
            clauses.append("chunks.status = 'approved'")

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


def _to_match_expression(query: str) -> str:
    """Turn user text into an FTS5 MATCH expression.

    Every term is quoted, so FTS5's operators cannot be reached from user input.
    That is a correctness measure as much as a security one: someone searching
    for `auth OR token` means three words, and a bare `-` or unbalanced `"` would
    otherwise raise a syntax error at a person who typed a perfectly ordinary
    sentence.

    Terms are ANDed. A query whose words all appear is a better default than one
    where any word does, which on a knowledge base returns everything.
    """
    terms = [term.strip(_FTS_SPECIAL) for term in query.replace('"', " ").split()]
    kept = [f'"{term}"' for term in terms if term]
    return " AND ".join(kept)


__all__ = [
    "Fts5UnavailableError",
    "IndexBuildError",
    "IndexableChunk",
    "SqliteIndexStore",
    "fts5_available",
]
