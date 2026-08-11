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
  that do not have an explicit INTEGER PRIMARY KEY". `chunks.chunk_id` and
  `nodes.node_id` are both TEXT primary keys, and `chunks_fts`,
  `chunks_trigram`, `nodes_fts` and `nodes_trigram` are all external-content
  tables keyed on the rowid of one of them, so a renumbering would silently
  repoint every posting in four indexes -- two at v3, four since v4 gave summary
  nodes their own storage. A design resting on observed-but-unpromised behaviour
  becomes a silent corruption at the next release.

:meth:`sqlite3.Connection.backup` is the remaining primitive: page-level, so
rowid stability is not a behaviour it could get wrong, and taken through a
connection, so it sees the WAL.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from theurian.domain.chunking import ChunkScope
from theurian.domain.errors import TheurianError
from theurian.domain.ports.index_store import ForestRecompute
from theurian.infrastructure.sqlite.schema import CONNECTION_PRAGMAS, read_only_uri

#: The chunks a purge removes: exactly the withdrawn revisions. A chunk is never
#: the *target* of a derivation edge -- only a node can be built from something
#: -- so this half needs no recursion.
#:
#: `%s` expands to one placeholder per withdrawn revision, or to `NULL` when
#: nothing was withdrawn: `IN ()` is a syntax error and `IN (NULL)` is never
#: true, which is the answer a purge with an empty withdrawal list needs.
_DOOMED_CHUNKS = """\
doomed_chunks(chunk_id) AS (
    SELECT chunk_id FROM chunks WHERE revision_id IN (%s)
)"""

#: Nodes whose provenance closes into a cycle, and so is grounded in nothing.
#:
#: `reaches` is the transitive closure of "is built from", so a node that appears
#: as its own descendant sits on a cycle. Detected explicitly rather than
#: inferred, because a cycle is the one ungrounded shape no forward walk from a
#: withdrawn chunk and no backward walk from a broken edge can reach: every
#: member has provenance, every edge resolves, and no member is ever grounded.
#: Measured before this existed, by purging a build of two chunks and three
#: summaries with *both* revisions withdrawn: a two-cycle of summaries of the
#: withdrawn incident survived, text intact, and `_verify` accepted the build
#: as publishable.
#:
#: `UNION` and not `UNION ALL`, which is what makes it terminate: the closure is
#: at most one row per ordered pair of nodes, and deduplication is what stops the
#: walk going round the cycle forever. It costs that bound too -- O(nodes x
#: edges) on a graph that is one long chain or one big cycle -- which is why the
#: shape ADR-0008 decision 2 actually builds matters: three levels, so the
#: closure is a few rows per node. Measured on a real build of 1,100 nodes and
#: 11,000 edges: 0.55 ms; on 5,500 and 55,000: 3.0 ms.
_CYCLIC_NODES = """\
reaches(start, cur) AS (
    SELECT node_id, source_node_id FROM node_derivation WHERE source_node_id IS NOT NULL
    UNION
    SELECT reaches.start, node_derivation.source_node_id
      FROM reaches
      JOIN node_derivation ON node_derivation.node_id = reaches.cur
     WHERE node_derivation.source_node_id IS NOT NULL
),
cyclic(node_id) AS (
    SELECT DISTINCT start FROM reaches WHERE start = cur
)"""

#: A node that no chain of derivations anchors in a surviving chunk, judged on
#: its own edges. Five arms, and each is a way of *never* reaching one:
#:
#: 1. its own `source_revision_id` names a withdrawn revision -- the node's text
#:    was built against state the caller may no longer read, whatever its edges
#:    still point at;
#: 2. it has no `node_derivation` row at all, so it cannot say what it holds;
#: 3. an edge names a chunk that is withdrawn, or one that is not in the file;
#: 4. an edge names a node that is not in the file;
#: 5. it sits on a provenance cycle (:data:`_CYCLIC_NODES`).
#:
#: **The rule is universal, not existential: *every* declared source has to
#: terminate at a surviving chunk, not merely one of them.** A summary cannot be
#: partially grounded any more than it can be partially withdrawn, so a node with
#: one good parent and one that leads nowhere goes.
#:
#: Measured by a differential over 400 random graphs against a well-founded
#: reference, run three times and reported as three numbers because the
#: population changed underneath it. The seeded traversal this replaces diverged
#: on **91** of the 400 -- its smallest counterexample a node naming itself as
#: its own source -- and every divergence was cycle-reachable. Once the schema's
#: self-edge `CHECK` removes that shape from the population (142 of the
#: generated edges), the same seeded traversal diverges on **11**, which is the
#: part of the gap the `CHECK` alone does not close. The reading below diverges
#: on **none**.
#:
#: `EXISTS`/`NOT EXISTS` rather than `IN`/`NOT IN` throughout, kept even now that
#: both id columns are `NOT NULL`: `x NOT IN (SELECT ...)` answers NULL -- falsy,
#: for every row -- as soon as one NULL is in the set, so the failure mode is a
#: check that silently stops checking rather than one that reports.
#:
#: The outer `WHERE EXISTS` keeps the result to ids that name a real row: an
#: edge whose *owner* node is gone (which `PRAGMA foreign_keys = OFF` can leave
#: behind) would otherwise be counted as a doomed node and inflate the removed
#: count by a row no `DELETE` can find.
#:
#: `UNION ALL` between the arms, so one node named by two of them appears twice.
#: Harmless to both readers and load-bearing for one: `_DOOMED`'s recursive
#: `UNION` collapses the duplicates, and the existence check needs to be able to
#: stop at the first row. `UNION` here sorts every arm into a temp B-tree before
#: yielding anything: measured 14.8 ms per pre-check against 3.8 ms, on a build
#: of 60,000 unprovenanced nodes where the second arm already had the answer.
_UNANCHORED_NODES = """\
    SELECT unanchored_node.node_id FROM (
        SELECT node_id FROM nodes WHERE source_revision_id IN (%s)
        UNION ALL
        SELECT nodes.node_id FROM nodes
         WHERE NOT EXISTS (SELECT 1 FROM node_derivation e WHERE e.node_id = nodes.node_id)
        UNION ALL
        SELECT e.node_id FROM node_derivation e
         WHERE e.source_chunk_id IS NOT NULL
           AND (EXISTS (SELECT 1 FROM doomed_chunks d WHERE d.chunk_id = e.source_chunk_id)
                OR NOT EXISTS (SELECT 1 FROM chunks c WHERE c.chunk_id = e.source_chunk_id))
        UNION ALL
        SELECT e.node_id FROM node_derivation e
         WHERE e.source_node_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.node_id = e.source_node_id)
        UNION ALL
        SELECT node_id FROM cyclic
    ) AS unanchored_node
     WHERE EXISTS (SELECT 1 FROM nodes n WHERE n.node_id = unanchored_node.node_id)"""

#: Everything a purge of the given revisions removes: the withdrawn chunks, and
#: every node not well-founded in what survives them.
#:
#: **v4, not v3.** RAPTOR summaries now live in `nodes`, provenanced by
#: `node_derivation`, rather than as `chunks` rows with `derived = 1` (ADR-0008
#: decision 5's amendment, ADR-0024 decision 8's amendment).
#:
#: The node half is the complement of *grounded*: a node survives only if every
#: derivation path below it terminates at a surviving chunk in finitely many
#: steps. Written as its complement because that is the direction a recursive CTE
#: can compute -- grounding is a least fixed point under a universal quantifier,
#: which SQLite's row-at-a-time recursion cannot express, while "unanchored, and
#: everything built on top of it" is ordinary forward chaining.
#:
#: The closure arm is what makes the reading transitive: a node built from a
#: doomed node is doomed, to the fixed point. It joins `nodes` because an edge
#: can outlive the row that owns it.
#:
#: A recursive query rather than the foreign keys' `ON DELETE CASCADE`, because
#: the cascade removes the *edge* and leaves the node. A summary built from a
#: retired incident note still contains the note; deleting the note and keeping
#: the summary withdraws nothing (ADR-0024 decision 8).
#:
#: `kind` discriminates the two id spaces in one result set, so `_delete` can
#: route each row to the table it actually lives in without a second query.
_DOOMED = f"""
WITH RECURSIVE
{_DOOMED_CHUNKS},
{_CYCLIC_NODES},
unanchored(node_id) AS (
{_UNANCHORED_NODES}
),
doomed_nodes(node_id) AS (
    SELECT node_id FROM unanchored
    UNION
    SELECT e.node_id
      FROM node_derivation e
      JOIN doomed_nodes d ON e.source_node_id = d.node_id
      JOIN nodes n ON n.node_id = e.node_id
)
SELECT 'chunk' AS kind, chunk_id AS id FROM doomed_chunks
UNION ALL
SELECT 'node' AS kind, node_id AS id FROM doomed_nodes
"""  # noqa: S608 - composed from module-owned literals; every value is bound

#: Whether :data:`_DOOMED` would return anything at all, without building the
#: set. Read by :meth:`SqliteIndexStore.holds_any_revision`, the pre-check that
#: decides whether a withdrawal is worth copying a whole index for.
#:
#: **It is exactly `_DOOMED` non-empty, and shares the SQL that makes it so.**
#: `doomed_chunks` and `_UNANCHORED_NODES` are the same literals `_DOOMED` is
#: built from, and `_DOOMED`'s only other content is the upward closure -- which
#: adds nothing to an empty seed. So `holds_any_revision` and a non-zero
#: `derive_purged` agree by construction rather than by two predicates being kept
#: in step by hand, which is what the v3 pair required and did not get: a build
#: whose only damage was a dangling edge answered "nothing to purge" from this
#: side and "refuse to publish" from the other.
#:
#: `UNION ALL` and `LIMIT 1`, so the withdrawn-chunk lookup answers on its own
#: index and the node arms are never evaluated when it hits: 0.55 ms on a
#: 10,000-chunk build against 7.7 ms for the same build's miss, and 0.56 ms
#: against 41 ms at 50,000 chunks. The miss is the common answer, and it is still
#: the cheaper half of the trade it exists for: the copy it avoids measured
#: 51 ms on a 12.3 MB index and 579 ms on 150.3 MB, before the delete and the six
#: post-conditions.
ANY_DOOMED_ROW = f"""
WITH RECURSIVE
{_DOOMED_CHUNKS},
{_CYCLIC_NODES}
SELECT 1 FROM doomed_chunks
UNION ALL
SELECT 1 FROM (
{_UNANCHORED_NODES}
)
LIMIT 1
"""  # noqa: S608 - composed from module-owned literals; every value is bound


#: Rows of the withdrawn revisions still in the build -- chunks by `revision_id`,
#: and nodes by the `source_revision_id` stamp that says which revision their
#: text was written against (ADR-0008 decision 5). Both, because a purge that
#: removed the chunk and kept a summary built from that revision withdrew
#: nothing. Answered from `chunks_by_revision`; `temp.withdrawn` is materialised
#: by :func:`_verify` so that every post-condition below is a bare statement with
#: nothing to bind.
_WITHDRAWN_ROWS = """
SELECT (SELECT count(*) FROM chunks
         WHERE revision_id IN (SELECT revision_id FROM temp.withdrawn))
     + (SELECT count(*) FROM nodes
         WHERE source_revision_id IN (SELECT revision_id FROM temp.withdrawn))
"""

#: A vector whose chunk is gone. `ON DELETE CASCADE` removes it with the chunk,
#: and :func:`_writing` turns `PRAGMA foreign_keys` on for every delete this
#: module makes, so reaching this count means the build being verified arrived
#: already holding it.
_ORPHANED_EMBEDDINGS = """
SELECT count(*) FROM embeddings
 WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.chunk_id = embeddings.chunk_id)
"""

#: A node whose provenance cannot be resolved at all: no `node_derivation` row
#: names it. Deleted rather than kept, because a node that cannot say where it
#: came from cannot be shown to hold nothing withdrawn (ADR-0024 decision 8).
#:
#: One of the five arms of :data:`_UNANCHORED_NODES`, checked again here as a
#: post-condition: after the purge this count must be zero, which is what fails
#: if that arm is ever lost.
_UNPROVENANCED_NODES = """
SELECT count(*) FROM nodes
 WHERE NOT EXISTS (SELECT 1 FROM node_derivation e WHERE e.node_id = nodes.node_id)
"""

#: A `node_derivation` edge naming a source that is no longer there: a
#: `source_chunk_id` absent from `chunks`, or a `source_node_id` absent from
#: `nodes`. A node that carries one is unanchored and the purge deletes it, so
#: what survives to be counted here is an edge whose own node is already gone --
#: which `ON DELETE CASCADE` removes, and which therefore says the same thing the
#: orphaned-embedding count says: the build arrived damaged.
_DANGLING_NODE_DERIVATION = """
SELECT count(*) FROM node_derivation e
 WHERE (e.source_chunk_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.chunk_id = e.source_chunk_id))
    OR (e.source_node_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.node_id = e.source_node_id))
"""

#: Nodes left standing on a provenance cycle. The one ungrounded shape the four
#: counts above cannot see: every member has an edge, every edge resolves, and no
#: member is grounded.
_CYCLIC_NODE_COUNT = f"""
WITH RECURSIVE
{_CYCLIC_NODES}
SELECT count(*) FROM cyclic
"""  # noqa: S608 - composed from module-owned literals; every value is bound

#: A summary's vector outliving the summary -- `_ORPHANED_EMBEDDINGS` over the
#: node tables. Its own count rather than a second `OR` on that one, because the
#: two name different remedies to whoever reads the message.
_ORPHANED_NODE_EMBEDDINGS = """
SELECT count(*) FROM node_embeddings
 WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.node_id = node_embeddings.node_id)
"""

_UNPUBLISHED: Final = (
    "Nothing was published, so retrieval still uses the current index and the partial build "
    "has been deleted."
)
_REBUILD: Final = "Run `theurian index build` to produce a build from canonical state instead."
_REPORT: Final = (
    "Run `theurian index build`; this is a defect in Theurian rather than in your project, "
    "so please report it."
)
_CASCADE_RAN: Final = (
    "The purge deletes with `PRAGMA foreign_keys` on, so `ON DELETE CASCADE` did run and the "
    "build this one was copied from already held them."
)

#: Every way the delete can be incomplete, in the order they are checked, each
#: with the message its count raises. A sequence rather than a run of `if`s so
#: that adding a condition is adding a row, and so that all six raise from one
#: place -- the shape a fifth and sixth condition made worth having.
#:
#: **The six together are complete: a build that passes them holds nothing
#: `_DOOMED` would remove.** Take one that passes. `_WITHDRAWN_ROWS` is zero, so
#: no chunk of a withdrawn revision remains and no node carries a withdrawn
#: stamp. `_UNPROVENANCED_NODES` is zero, so every node has at least one edge.
#: `_DANGLING_NODE_DERIVATION` is zero, so every edge names a row that is there --
#: and with the withdrawn chunks already gone, every chunk edge therefore names a
#: surviving one. `_CYCLIC_NODE_COUNT` is zero, so the node-to-node graph is
#: acyclic, hence finite and well ordered. Induct up that order: a node with only
#: chunk edges is grounded in surviving chunks, and a node whose node edges all
#: point lower is grounded by the ones below it. That is why the cycle count is
#: here and not merely `_DOOMED` asked a second time -- a post-condition computed
#: by the function being checked cannot catch that function being wrong, which is
#: the whole reason `_verify` exists. ADR-0024's first decision is what leaves it
#: no second chance: from the moment `active-index.json` names a build, that file
#: is read-only for the rest of its life, so publishing is a pointer swap and
#: nothing downstream ever looks inside.
_POST_CONDITIONS: Final[tuple[tuple[str, str], ...]] = (
    (
        _WITHDRAWN_ROWS,
        "The purged build still holds {count} row(s) of the revisions it was asked to remove. "
        f"{_UNPUBLISHED} {_REBUILD}",
    ),
    (
        _ORPHANED_EMBEDDINGS,
        "The purged build holds {count} embedding(s) whose chunk is gone. "
        f"{_CASCADE_RAN} {_UNPUBLISHED} {_REBUILD}",
    ),
    (
        _UNPROVENANCED_NODES,
        "The purged build holds {count} node(s) with no provenance, which cannot be shown to "
        f"hold nothing withdrawn. {_UNPUBLISHED} {_REPORT}",
    ),
    (
        _DANGLING_NODE_DERIVATION,
        "The purged build holds {count} node derivation edge(s) whose source is gone. "
        f"{_CASCADE_RAN} {_UNPUBLISHED} {_REBUILD}",
    ),
    (
        _CYCLIC_NODE_COUNT,
        "The purged build holds {count} node(s) whose provenance closes into a cycle, so no "
        "chain of derivations shows them free of withdrawn content. "
        f"{_UNPUBLISHED} {_REPORT}",
    ),
    (
        _ORPHANED_NODE_EMBEDDINGS,
        "The purged build holds {count} node embedding(s) whose node is gone. "
        f"{_CASCADE_RAN} {_UNPUBLISHED} {_REBUILD}",
    ),
)


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


@dataclass(frozen=True, slots=True)
class _PurgeDelta:
    """What :func:`_delete` did, and what the re-derivation needs to know next.

    ``removed`` is the purge's returned count. ``affected_scopes`` are the scopes
    whose leaf chunks the delete removed -- the ones whose trees a re-derivation
    must rebuild, read before the delete so the rows that name them still exist.
    ``has_surviving_nodes`` is what keeps a chunk-only build (no forest) from
    taking the re-derivation path and a fully-withdrawn build from being
    re-derived out of nothing: both leave zero nodes, and re-deriving either would
    either invent a forest that was never asked for or add one identical to the
    empty forest a never-held corpus produces.
    """

    removed: int
    affected_scopes: frozenset[ChunkScope]
    has_surviving_nodes: bool


def purge_into(  # noqa: PLR0913 - a build's identity (id, state hash), its input, and its outputs
    source: Path,
    target: Path,
    *,
    revision_ids: Sequence[str],
    index_build_id: str,
    state_hash: str,
    recompute_forest: ForestRecompute | None = None,
) -> int:
    """Write `source` minus `revision_ids` to `target`. Returns rows removed.

    All-or-nothing, like a build: anything that raises unlinks `target`, so a
    half-purged file — one that looks complete and still holds withdrawn content
    — never exists to be published.

    Refuses an existing `target` for the reason :meth:`IndexStore.create` does:
    an index build is a whole artifact, and writing into someone else's is how
    the published index gets destroyed by a build that was refused permission to
    touch it.

    `recompute_forest` re-derives each affected scope's summary trees over the
    surviving rows (ADR-0008 decision 9), so a purged forest equals one built from
    a corpus that never held the withdrawn rows. It runs **after** the delete --
    on the file the delete left, whose surviving chunks it reads back -- and
    **before** `_restamp` and `_verify`, so a re-derivation that produces an
    ungrounded node is caught by the same post-conditions a bad delete is, and the
    fresh nodes are stamped with this build's id along with the survivors. It is
    injected, not imported: `index_purge` may not name the application-layer
    forest builder the callback closes over (ADR-0003). It is skipped over a build
    with no surviving forest, so today's delete-only chunk purge is untouched.
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
        delta = _delete(building, revision_ids)
        # Only when a withdrawal touched a scope that still has a forest to
        # rebuild: a chunk-only build leaves `has_surviving_nodes` false and takes
        # the delete-only path it always has, and a purge with no withdrawal
        # (residue cleanup) has no affected scope to re-derive.
        if recompute_forest is not None and delta.affected_scopes and delta.has_surviving_nodes:
            recompute_forest(building, tuple(sorted(delta.affected_scopes)))
        _restamp(building, index_build_id=index_build_id, state_hash=state_hash)
        _verify(building, revision_ids)
        os.replace(building, target)  # noqa: PTH105 - os.replace is the atomic primitive
    except BaseException:
        for path in (building, target):
            path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        raise
    return delta.removed


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
            closing(sqlite3.connect(read_only_uri(source), uri=True)) as reader,
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


def _delete(target: Path, revision_ids: Sequence[str]) -> _PurgeDelta:
    """Remove the withdrawn revisions and everything they no longer ground.

    Returns a :class:`_PurgeDelta`: the count, the scopes the withdrawal reached,
    and whether any node survives. The scopes are read before the delete removes
    the chunks that name them, and the surviving-node flag after it.
    """
    with _writing(target) as connection, connection:
        # `NULL` when nothing was withdrawn: `IN ()` is a syntax error, and
        # `IN (NULL)` is never true, so the query still runs and still reaches its
        # other arms. A purge with an empty withdrawal list is not a no-op -- it
        # still removes every node the surviving corpus cannot ground.
        #
        # Twice, because `_DOOMED` names the withdrawn set in two places: the
        # chunks it removes, and the `source_revision_id` stamp that dooms a node
        # whose edges all still resolve.
        placeholders = ", ".join("?" for _ in revision_ids) or "NULL"
        # Before the delete, while the withdrawn chunks are still there to be read:
        # a scope is affected -- its trees need re-deriving -- exactly when it
        # loses a leaf chunk, and the delete below is about to remove the rows
        # that say which scope that was.
        affected = _affected_scopes(connection, revision_ids)
        rows = connection.execute(
            _DOOMED % (placeholders, placeholders), tuple(revision_ids) * 2
        ).fetchall()
        doomed_chunks: set[str] = set()
        doomed_nodes: set[str] = set()
        for row in rows:
            identifier = row["id"]
            if identifier is None:
                # Unreachable while both primary keys are `NOT NULL`. Skipped
                # rather than coerced, because `str(None)` deletes nothing under
                # the id "None" while still counting a row as removed -- a purge
                # reporting more progress than it made. Anything this leaves
                # behind, `_verify` refuses the build over.
                continue
            bucket = doomed_chunks if row["kind"] == "chunk" else doomed_nodes
            bucket.add(str(identifier))

        if not doomed_chunks and not doomed_nodes:
            return _PurgeDelta(
                removed=0,
                affected_scopes=affected,
                has_surviving_nodes=_has_surviving_nodes(connection),
            )
        # Two statements now, not one: chunks and nodes are separate tables at
        # v4, each with its own FTS5 delete triggers (`chunks_fts`/
        # `chunks_trigram` for the first, `nodes_fts` for the second) that fire
        # once per row whatever seed put it in its set.
        if doomed_chunks:
            connection.executemany(
                "DELETE FROM chunks WHERE chunk_id = ?",
                [(chunk_id,) for chunk_id in sorted(doomed_chunks)],
            )
        if doomed_nodes:
            connection.executemany(
                "DELETE FROM nodes WHERE node_id = ?",
                [(node_id,) for node_id in sorted(doomed_nodes)],
            )
        return _PurgeDelta(
            removed=len(doomed_chunks) + len(doomed_nodes),
            affected_scopes=affected,
            # After the delete: a re-derivation runs only where a forest survives.
            # A --raptor build with survivors keeps at least one node here (a
            # surviving item's Document node grounds only on its own chunks and so
            # is never doomed); a chunk-only build has none, and neither does one
            # whose every item was withdrawn -- both of which re-derive to nothing.
            has_surviving_nodes=_has_surviving_nodes(connection),
        )


def _affected_scopes(
    connection: sqlite3.Connection, revision_ids: Sequence[str]
) -> frozenset[ChunkScope]:
    """The distinct scopes of the chunks the withdrawn revisions own.

    Read off the four denormalised columns a chunk carries -- the ones a forest
    partitions on (:class:`~theurian.domain.chunking.ChunkScope`) -- so a
    re-derivation can rebuild exactly the scopes that lost a row and leave every
    other scope's copied nodes byte-identical. Must be called before the delete,
    while the withdrawn chunks are still present.
    """
    placeholders = ", ".join("?" for _ in revision_ids) or "NULL"
    rows = connection.execute(
        "SELECT DISTINCT project_id, namespace, sensitivity, status "  # noqa: S608 - placeholders only
        f"FROM chunks WHERE revision_id IN ({placeholders})",
        tuple(revision_ids),
    ).fetchall()
    return frozenset(
        ChunkScope(
            project_id=str(row["project_id"]),
            namespace=str(row["namespace"]),
            sensitivity=str(row["sensitivity"]),
            status=str(row["status"]),
        )
        for row in rows
    )


def _has_surviving_nodes(connection: sqlite3.Connection) -> bool:
    """Whether any summary node remains -- the signal a forest is there to rebuild."""
    return connection.execute("SELECT 1 FROM nodes LIMIT 1").fetchone() is not None


def _restamp(target: Path, *, index_build_id: str, state_hash: str) -> None:
    """Make the new build's `index_metadata` describe the new build.

    `Connection.backup` copies pages, so without this the purged file still
    carries the id and timestamp of the build it was copied from — a file whose
    own record of itself disagrees with the pointer that names it. Nothing in
    `src/` reads `index_metadata.index_build_id` back today, which is what makes
    this cheap to get wrong and expensive to find later (ADR-0024 decision 2).

    **`nodes` carries a second copy of that identity and needs the same
    treatment.** `index_build_id` is one of ADR-0008 decision 5's fourteen
    provenance columns, recording which build a summary belongs to. Measured by
    purging a build with one node anchored in a surviving chunk and reading both
    columns back: the surviving node named the build it was copied from while
    `index_metadata` named the new one — the same disagreement one level down,
    which is what `test_restamp_updates_survivors_index_build_id_too` now pins.
    """
    with _writing(target) as connection, connection:
        connection.execute(
            "UPDATE index_metadata SET index_build_id = ?, state_hash = ?, built_at = ? "
            "WHERE id = 1",
            (index_build_id, state_hash, datetime.now(UTC).isoformat()),
        )
        # Fires `nodes_fts_update` and `nodes_trigram_update` once per surviving
        # node, which rewrites each one's postings with identical text. That cost
        # is the price of the row's own record of itself being true; the
        # alternative is a provenance column that lies about which build it is in.
        connection.execute("UPDATE nodes SET index_build_id = ?", (index_build_id,))


def _verify(target: Path, revision_ids: Sequence[str]) -> None:
    """Refuse to hand back a build that still holds what it was asked to remove.

    The post-condition rather than the operation: `_delete` could be correct and
    this would still be worth running, because what publishes a build is a
    pointer swap and there is no later stage that looks.

    :data:`_POST_CONDITIONS` holds the six counts and the message each one
    raises, together with the argument that the six are jointly complete. Every
    one of them is checked whatever `revision_ids` held, and that is not
    symmetry: a purge with nothing withdrawn still removes every node the corpus
    cannot ground, so it can still orphan an embedding — and an early return for
    the empty case skipped exactly that. Found by a test that passed `[]`.
    """
    with _writing(target) as connection:
        # A table rather than bound parameters, so that every condition is a
        # statement with nothing to bind and the sequence can stay a plain pair
        # of strings. It lives on this connection only, and `_writing` closes it.
        with connection:
            connection.execute("CREATE TEMP TABLE withdrawn (revision_id TEXT PRIMARY KEY)")
            connection.executemany(
                "INSERT OR IGNORE INTO temp.withdrawn (revision_id) VALUES (?)",
                [(revision_id,) for revision_id in revision_ids],
            )
        for condition, message in _POST_CONDITIONS:
            count = int(connection.execute(condition).fetchone()[0])
            if count:
                raise IndexPurgeError(message.format(count=count))


__all__ = ["ANY_DOOMED_ROW", "IndexPurgeError", "purge_into"]
