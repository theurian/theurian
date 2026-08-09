"""Producing a purged index build from a published one (ADR-0024).

**A purge is a build.** ADR-0022 asked whether removing withdrawn rows should
produce a new build and swap the pointer, or mutate the published file in place,
and assumed the first was expensive: "at the cost of rewriting the whole file to
remove a few rows". Measured, that phrase conflated *re-deriving* a build — read
the canonical store, chunk, embed, write — with *copying* one and deleting rows
from the copy, which re-derives nothing. On a 12.3 MB index the first costs
2,614 ms and the second 51 ms; on 150.3 MB, 37,684 ms against 579 ms. ADR-0024
carries the table and the decision.

So this module writes a new file and never touches the published one. What it
must not do, and why each is a separate hazard:

- **not `shutil.copyfile`.** The `-wal` sidecar is a separate file, so a copy
  taken while a writer holds committed-but-uncheckpointed content silently drops
  it. Measured: a copy holding 1,055 rows where the writer that had committed saw
  955, and — when the uncheckpointed pages carry the *schema* — a database with
  no table at all.
- **not `VACUUM INTO`.** Correct on SQLite 3.47.1 and resting on something SQLite
  declines to promise: VACUUM "may change the ROWIDs of entries in any tables
  that do not have an explicit INTEGER PRIMARY KEY". `chunks.chunk_id` is a TEXT
  primary key, and `chunks_fts` and `chunks_trigram` are
  `content='chunks', content_rowid='rowid'`, so a renumbering would silently
  repoint every posting in both indexes. A design resting on
  observed-but-unpromised behaviour becomes a silent corruption at the next
  release.

:meth:`sqlite3.Connection.backup` is the remaining primitive: page-level, so
rowid stability is not a behaviour it could get wrong, and taken through a
connection, so it sees the WAL.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from theurian.infrastructure.sqlite.schema import CONNECTION_PRAGMAS

#: Rows reachable from a withdrawn chunk through :data:`chunk_derivation`,
#: transitively, plus the withdrawn chunks themselves.
#:
#: A recursive query rather than the foreign key's `ON DELETE CASCADE`, because
#: the cascade removes the *edge* and leaves the node. A summary built from a
#: retired incident note still contains the note; deleting the note and keeping
#: the summary withdraws nothing (ADR-0024 decision 8).
_DOOMED = """
WITH RECURSIVE doomed(chunk_id) AS (
    SELECT chunk_id FROM chunks WHERE revision_id IN (%s)
    UNION
    SELECT chunk_derivation.node_chunk_id
      FROM chunk_derivation
      JOIN doomed ON chunk_derivation.source_chunk_id = doomed.chunk_id
)
SELECT chunk_id FROM doomed
"""

#: A derived row whose provenance cannot be resolved. Not reachable from any
#: withdrawn chunk -- it has no edges at all -- so the traversal above cannot see
#: it, and it is deleted rather than kept: a node that cannot say what it was
#: built from cannot be shown to hold nothing withdrawn (ADR-0024 decision 8).
_UNPROVENANCED = """
SELECT chunk_id FROM chunks
 WHERE derived = 1
   AND chunk_id NOT IN (SELECT node_chunk_id FROM chunk_derivation)
"""


class IndexPurgeError(Exception):
    """A purge could not produce a build fit to publish."""


@contextmanager
def _writing(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection configured the way every index connection is.

    ``CONNECTION_PRAGMAS`` rather than a bare ``connect``, and the reason is one
    line of it: ``PRAGMA foreign_keys = ON``. SQLite enforces foreign keys **per
    connection**, and the pragma defaults to *off*, so a purge that opened its
    own connection would delete a chunk and leave its embedding behind. That
    failure is silent and one-directional — the dense retriever joins
    ``embeddings`` to ``chunks``, so an orphaned vector returns nothing rather
    than returning a withdrawn row — which is exactly why it needs a test and not
    a review.
    """
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        for pragma in CONNECTION_PRAGMAS:
            connection.execute(pragma)
        yield connection
    finally:
        connection.close()


def purge_into(
    source: Path,
    target: Path,
    *,
    revision_ids: Sequence[str],
    index_build_id: str,
    state_hash: str,
) -> int:
    """Write `source` minus `revision_ids` to `target`. Returns rows removed.

    All-or-nothing, like a build: anything that raises unlinks `target`, so a
    half-purged file — one that looks complete and still holds withdrawn content
    — never exists to be published.

    Refuses an existing `target` for the reason :meth:`IndexStore.create` does:
    an index build is a whole artifact, and writing into someone else's is how
    the published index gets destroyed by a build that was refused permission to
    touch it.
    """
    if target.exists():
        msg = f"{target} already exists. A purge writes a new build, never into an existing one."
        raise IndexPurgeError(msg)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Written under a name `theurian index gc` does not reap, then renamed.
    #
    # **This is what makes a file under the published name complete by
    # construction**, rather than complete by an argument about id ordering.
    # `gc` reaps builds the pointer does not name, and a purge's output is not
    # yet pointed at, so the two race. The previous answer was ULID ordering --
    # an unpublished build's id sorts above the published one, so `gc` skips it
    # -- which holds within a process, where `SeededIdGenerator` serialises on a
    # lock, and degrades to millisecond resolution across processes. `os.replace`
    # is atomic on POSIX, so this needs no such argument: the completed name
    # appears only once the bytes behind it are final.
    building = Path(f"{target}.building")
    building.unlink(missing_ok=True)
    try:
        _copy(source, building)
        removed = _delete(building, revision_ids)
        _restamp(building, index_build_id=index_build_id, state_hash=state_hash)
        _verify(building, revision_ids)
        os.replace(building, target)  # noqa: PTH105 - os.replace is the atomic primitive
    except BaseException:
        for path in (building, target):
            path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        raise
    return removed


def _copy(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as reader, closing(sqlite3.connect(target)) as writer:
        reader.backup(writer)


def _delete(target: Path, revision_ids: Sequence[str]) -> int:
    """Remove the withdrawn revisions and everything derived from them."""
    with _writing(target) as connection, connection:
        doomed: set[str] = set()
        if revision_ids:
            placeholders = ", ".join("?" for _ in revision_ids)
            rows = connection.execute(_DOOMED % placeholders, tuple(revision_ids)).fetchall()
            doomed.update(str(row["chunk_id"]) for row in rows)
        doomed.update(str(row["chunk_id"]) for row in connection.execute(_UNPROVENANCED))

        if not doomed:
            return 0
        # Deleted by chunk id rather than by the predicates above, so that one
        # statement removes ordinary and derived rows alike and the FTS5 delete
        # triggers fire once per row whatever put it in the set.
        connection.executemany(
            "DELETE FROM chunks WHERE chunk_id = ?", [(chunk_id,) for chunk_id in sorted(doomed)]
        )
        return len(doomed)


def _restamp(target: Path, *, index_build_id: str, state_hash: str) -> None:
    """Make the new build's `index_metadata` describe the new build.

    `Connection.backup` copies pages, so without this the purged file still
    carries the id and timestamp of the build it was copied from — a file whose
    own record of itself disagrees with the pointer that names it. Nothing in
    `src/` reads `index_metadata.index_build_id` back today, which is what makes
    this cheap to get wrong and expensive to find later (ADR-0024 decision 2).
    """
    with _writing(target) as connection, connection:
        connection.execute(
            "UPDATE index_metadata SET index_build_id = ?, state_hash = ?, built_at = ? "
            "WHERE id = 1",
            (index_build_id, state_hash, datetime.now(UTC).isoformat()),
        )


def _verify(target: Path, revision_ids: Sequence[str]) -> None:
    """Refuse to hand back a build that still holds what it was asked to remove.

    The post-condition rather than the operation: `_delete` could be correct and
    this would still be worth running, because what publishes a build is a
    pointer swap and there is no later stage that looks. Cheap — one indexed
    count against `chunks_by_revision`.
    """
    with _writing(target) as connection:
        remaining = 0
        if revision_ids:
            placeholders = ", ".join("?" for _ in revision_ids)
            row = connection.execute(
                f"SELECT count(*) AS remaining FROM chunks WHERE revision_id IN ({placeholders})",  # noqa: S608 - placeholders are generated, values are bound
                tuple(revision_ids),
            ).fetchone()
            remaining = int(row["remaining"])
        # Checked whatever `revision_ids` held, and that is not symmetry. A purge
        # with nothing withdrawn still deletes unprovenanced derived rows, so it
        # can still orphan an embedding -- and an early return for the empty case
        # skipped exactly that. Found by a test that passed `[]`.
        orphans = connection.execute(
            "SELECT count(*) AS orphans FROM embeddings "
            "WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"
        ).fetchone()

    if remaining:
        msg = (
            f"The purged build still holds {remaining} chunk(s) of the revisions it was "
            f"asked to remove. Nothing was published; the partial build has been deleted."
        )
        raise IndexPurgeError(msg)
    if int(orphans["orphans"]):
        msg = (
            f"The purged build holds {orphans['orphans']} embedding(s) whose chunk is gone. "
            f"`PRAGMA foreign_keys` was off for the delete, so `ON DELETE CASCADE` did not run. "
            f"Nothing was published; the partial build has been deleted."
        )
        raise IndexPurgeError(msg)


__all__ = ["IndexPurgeError", "purge_into"]
