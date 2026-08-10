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

from theurian.domain.errors import TheurianError
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
    SELECT chunk_id FROM chunks
     WHERE derived = 1
       AND chunk_id NOT IN (SELECT node_chunk_id FROM chunk_derivation)
    UNION
    SELECT chunk_derivation.node_chunk_id
      FROM chunk_derivation
      JOIN doomed ON chunk_derivation.source_chunk_id = doomed.chunk_id
)
SELECT chunk_id FROM doomed
"""

#: A derived row whose provenance cannot be resolved: `derived = 1` with no edge
#: naming what it was built from. Deleted rather than kept, because a node that
#: cannot say where it came from cannot be shown to hold nothing withdrawn
#: (ADR-0024 decision 8).
#:
#: **It is a *seed* of the traversal above, not a separate sweep, and that
#: distinction was a defect.** Deleting the two sets independently leaves the
#: children: a node whose only source is an unprovenanced node is reachable from
#: nothing withdrawn, so the recursive walk never saw it, and the second sweep
#: only removed its parent. Measured -- a summary of an unprovenanced summary
#: survived a purge with its text intact and retrievable. Seeding both into one
#: recursive query takes the traversal to its fixed point instead.
#:
#: Kept as a constant because `_verify` reads it as a post-condition: after the
#: purge this count must be zero, which is the check that fails if the seed above
#: is ever removed from `_DOOMED`.
_UNPROVENANCED = """
SELECT chunk_id FROM chunks
 WHERE derived = 1
   AND chunk_id NOT IN (SELECT node_chunk_id FROM chunk_derivation)
"""


class IndexPurgeError(TheurianError):
    """A purge could not produce a build fit to publish. Carries a remedy.

    A `TheurianError` and not a bare `Exception`, because that is the type every
    CLI handler catches -- `(TheurianError, sqlite3.Error, OSError)` -- and the
    difference between the two is a rendered remedy against a Rich traceback with
    the operator's absolute paths in it.
    """


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
        # The name only. This message reaches a user through the index CLI, and
        # the absolute path names the operator's home directory and project
        # layout for a condition that is about the build id, not the location.
        msg = (
            f"{target.name} already exists. A purge writes a new build, never into an "
            f"existing one. Run `theurian index build` to produce a fresh build, or "
            f"`theurian index gc` if it is a superseded build that was never reclaimed."
        )
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
    if building.exists():
        # Not ours to delete: a `.building` file is either another writer's work
        # in progress or the leftovers of one that crashed, and this function
        # cannot tell them apart. Removing it would be the same class of mistake
        # as writing into an existing build.
        msg = (
            f"{building.name} already exists, so another build or purge may be writing it. "
            f"Retry in a moment; if nothing else is running, `theurian index gc` reports "
            f"what is stranded."
        )
        raise IndexPurgeError(msg)
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
    """Page-copy the source build into a fresh file.

    **The source is opened `mode=ro`, and that is not tidiness.** A bare
    `sqlite3.connect` on a path that does not exist *creates an empty database
    there* -- which for the source means conjuring a 0-byte file at the published
    index path, and then failing with a raw `no such table: chunks`. Measured
    before this guard: the exception escaped as `OperationalError`, a 0-byte file
    was left at the published path, and the next search misdiagnosed it as a
    schema mismatch rather than a missing file, because a file that exists cannot
    take the missing-file branch. That is the same defect ADR-0024 decision 7
    closed on the read path, arriving on the write path.
    """
    try:
        with (
            closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as reader,
            closing(sqlite3.connect(target)) as writer,
        ):
            reader.backup(writer)
    except sqlite3.Error as exc:
        msg = (
            f"The index build being purged could not be read ({type(exc).__name__}). Nothing "
            f"was published, so retrieval still uses the current index. Run `theurian index "
            f"build` to rebuild it; the index is derived, so nothing authored is lost."
        )
        raise IndexPurgeError(msg) from exc


def _delete(target: Path, revision_ids: Sequence[str]) -> int:
    """Remove the withdrawn revisions and everything derived from them."""
    with _writing(target) as connection, connection:
        # `NULL` when nothing was withdrawn: `IN ()` is a syntax error, and
        # `revision_id IN (NULL)` is never true, so the query still runs and still
        # reaches its other seed. A purge with an empty withdrawal list is not a
        # no-op -- it still removes derived rows whose provenance is unresolvable.
        placeholders = ", ".join("?" for _ in revision_ids) or "NULL"
        rows = connection.execute(_DOOMED % placeholders, tuple(revision_ids)).fetchall()
        doomed = {str(row["chunk_id"]) for row in rows}

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
    pointer swap and there is no later stage that looks.

    **Three counts, each for a different way the delete can be incomplete**, and
    the third is the one a review found missing: rows of the withdrawn revisions,
    embeddings whose chunk is gone, and derived rows with no provenance. All
    three must be zero. The first is an indexed count against
    `chunks_by_revision`; the other two are small scans over tables the purge has
    just shrunk.
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
        # The third check, and the one that fails if `_UNPROVENANCED` is ever
        # removed as a seed of `_DOOMED`. Deleting the two sets independently
        # left the *children* of an unprovenanced node behind, text intact and
        # retrievable, so this counts what should now be a fixed point.
        unprovenanced = connection.execute(
            f"SELECT count(*) AS unprovenanced FROM ({_UNPROVENANCED})"  # noqa: S608 - module-owned literal
        ).fetchone()

    if remaining:
        msg = (
            f"The purged build still holds {remaining} chunk(s) of the revisions it was "
            f"asked to remove. Nothing was published, so retrieval still uses the current "
            f"index and the partial build has been deleted. Run `theurian index build` to "
            f"produce a build from canonical state instead."
        )
        raise IndexPurgeError(msg)
    if int(orphans["orphans"]):
        msg = (
            f"The purged build holds {orphans['orphans']} embedding(s) whose chunk is gone. "
            f"`PRAGMA foreign_keys` was off for the delete, so `ON DELETE CASCADE` did not run. "
            f"Nothing was published, so retrieval still uses the current index and the partial "
            f"build has been deleted. Run `theurian index build`; this is a defect in Theurian "
            f"rather than in your project, so please report it."
        )
        raise IndexPurgeError(msg)
    if int(unprovenanced["unprovenanced"]):
        msg = (
            f"The purged build holds {unprovenanced['unprovenanced']} derived row(s) with no "
            f"provenance, which cannot be shown to hold nothing withdrawn. Nothing was "
            f"published, so retrieval still uses the current index and the partial build has "
            f"been deleted. Run `theurian index build`; this is a defect in Theurian rather "
            f"than in your project, so please report it."
        )
        raise IndexPurgeError(msg)


__all__ = ["IndexPurgeError", "purge_into"]
