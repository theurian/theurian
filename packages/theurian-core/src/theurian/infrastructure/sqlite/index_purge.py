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

**And a purge is not finished when the rows are deleted.** FTS5 answers a
`DELETE` by writing a tombstone rather than removing the row's postings, so the
copy the delete leaves still holds the posting list of everything withdrawn --
absent from every response and legible on the clock, which is how #499 read the
withdrawn count off query duration. :func:`_merge_full_text` is what ends that,
and it is why the ordering of the four steps in :func:`purge_into` is a
correctness property rather than a preference.
"""

from __future__ import annotations

import os
import re
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


#: Every table in the file that might be an FTS5 index, read out of the build
#: rather than written down here.
#:
#: **Discovered, because a written-down list is what the next table slips past.**
#: The schema carried two of these at v3 and carries four at v4, since summary
#: nodes got their own storage (`index_schema.py`); issue #499's own sketch of
#: this fix says "both FTS5 tables", which was the v3 count and is now half of
#: them. A merge over a constant tuple is correct the day it is written and
#: silently partial the day the next table lands -- and partial here is not a
#: smaller effect but an open channel, because the **tombstone residue** in a
#: table nothing merges compounds across purges, each one copying the last
#: (:func:`_merge_full_text`, which names the other two residues this module
#: talks about and keeps them apart from this one).
#:
#: `sql IS NOT NULL` is a **null guard, not a filter**: measured on a fresh index,
#: `type = 'table'` already selects 32 rows of which 32 have SQL, because the rows
#: SQLite writes with a null `sql` are the implicit indexes and they are
#: `type = 'index'`. It earns its place by keeping a null out of
#: :data:`_FTS5_DECLARATION`, which would otherwise be handed the string `"None"`.
#:
#: The shadow tables each FTS5 table owns -- `<name>_data`, `_idx`, `_docsize`,
#: `_config`, and `_content` too unless the table is external-content, so four or
#: five depending on that -- *do* carry `CREATE TABLE` text and survive to here.
#: :data:`_FTS5_DECLARATION` is what excludes them.
#:
#: `ORDER BY name` so a purge does the same work in the same order on every run.
#: Nothing downstream reads the order -- each table's merge is independent of the
#: others -- so this is determinism for its own sake and is argued, not pinned.
_FTS5_TABLE_CANDIDATES: Final = """
SELECT name, sql FROM sqlite_master
 WHERE type = 'table' AND sql IS NOT NULL
 ORDER BY name
"""

#: A `sqlite_master.sql` text that *declares* an FTS5 table, as against one that
#: merely mentions one.
#:
#: Both halves of this are load-bearing, and each was measured wrong before it was
#: measured right (2026-09-03, SQLite 3.47.1, fifteen declarations in
#: `test_purge_full_text_discovery.py`'s own vocabulary):
#:
#: **The module name may be quoted.** `USING "fts5"`, `USING [fts5]`,
#: `` USING `fts5` `` and `USING 'fts5'` are all accepted by SQLite and all name
#: the same module. A reading that insisted on the bare token skipped every one of
#: them, which is a table left holding its tombstones with nothing to say so.
#:
#: **The name portion may not cross into the argument list.** `[^(]+?` rather than
#: `.+?`, because a module's arguments and a quoted table name are both places an
#: operator's own text lands: an `fts5vocab` view *named* `x USING fts5` is legal,
#: and under `.+?` its own name satisfied this pattern -- so the merge issued
#: `optimize` against a read-only vocab table, which raises, which unlinks the
#: build. A purge destroying its own output over a legal table is the failure this
#: half exists to stop, and `test_purge_full_text_discovery.py` plants that table.
#:
#: Trailing `\s*\(` rather than `\b`, and it is what separates `fts5` from
#: `fts5vocab` now that the leading quote is optional: every FTS5 table declares at
#: least one column, so the module name is always followed by an argument list,
#: while `fts5vocab(` puts `v` where this wants a quote or a parenthesis.
#:
#: No `DOTALL`: nothing here is `.` any more. `[^(]` and `\s` both match a newline
#: on their own, so a DDL rewrapped across lines -- between the table name and
#: `USING`, or anywhere else before the arguments -- still matches.
_FTS5_DECLARATION: Final = re.compile(
    r"^\s*CREATE\s+VIRTUAL\s+TABLE\s+[^(]+?\s+USING\s+[\"'`\[]?fts5[\"'`\]]?\s*\(",
    re.IGNORECASE,
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
        # Last of the three writers, because a merge only holds until the next
        # write to a full-text index and both of the others are one: the delete
        # above, and `_restamp`'s per-node `UPDATE`.
        _merge_full_text(building)
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
    own record of itself disagrees with the pointer that names it. Nothing
    *serves* that column: `mcp/search.py` publishes `indexBuildId` from the
    pointer, so the disagreement reaches no caller. It is not unread, though —
    `SqliteIndexStore.add_nodes` reads it back out of `index_metadata` to stamp
    each summary node with the build it belongs to (`index_store.py`, which says
    so in its own docstring), which is why this is cheap to get wrong and
    expensive to find later (ADR-0024 decision 2).

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


def _merge_full_text(target: Path) -> None:
    """Merge each FTS5 index down to one segment, so the purge leaves no tombstones.

    **A `DELETE` does not remove a row's postings from an FTS5 index.** The
    external-content triggers issue `INSERT INTO <t>(<t>, rowid, ...) VALUES
    ('delete', ...)`, which appends a delete *marker*: the withdrawn row's
    posting list stays in the segment structure until a merge rewrites the
    segment without it. So until this ran, the build a purge published still held
    the postings of everything it had been asked to remove, and every query
    scanned past them.

    None of that reached a response, which is why the whole suite stayed green
    with it missing: a tombstoned row is excluded from results *and* from the
    collection statistics `bm25` reads, so a purged build already answered
    identically to one that never held the rows (ADR-0024, T-17). What it reached
    was the clock. Issue #499 calibrated query duration against how many rows had
    been withdrawn and recovered the withdrawn count at three of five points
    exactly, rising to 5.67x the never-held duration at 5,950 withdrawn -- a count
    SEC-13 arranges the response not to state. That is T-17a's root cause, *the
    index still holds the withdrawn rows*, surviving the purge that exists to end
    it.

    **After `_restamp`, and not inside `_delete` where #499 sketched it.**
    `_restamp`'s `UPDATE nodes SET index_build_id` fires `nodes_fts_update` and
    `nodes_trigram_update` once per surviving node, so it writes a tombstone per
    node *after* the delete -- on every purge, including one that withdraws
    nothing at all. Merging before it publishes a build still carrying that
    restamp's residue: measured on a 200-chunk, 60-node corpus, `nodes_fts` at
    1.50x and `nodes_trigram` at 1.77x a build that never held the withdrawn
    rows, against 0.75x and 0.89x merging afterwards.

    Call that the **restamp residue**. Three different quantities in this module
    answer to the word, and only the last survives the shipped ordering: the
    *tombstone residue* a merge exists to remove, which compounds across purges
    when nothing merges it (:data:`_FTS5_TABLE_CANDIDATES`); this restamp residue;
    and the *varint residue* at the end of this docstring. The restamp residue
    does *not* compound -- the next purge's copy carries it in and that purge's
    merge clears it -- so
    what the earlier placement leaves is bounded by the surviving node count
    rather than by the withdrawn count, and the count channel #499 is about closes
    under either placement. The later one is taken because it needs no such
    argument: **nothing writes to a full-text index after this call**, so a
    published build is merged outright and `test_purge_full_text_discovery.py` can
    pin that by re-running the merge and finding nothing to do. `recompute_forest`
    is upstream of `_restamp` and so is covered by the same ordering; `_verify`
    only counts and `os.replace` only renames.

    **`optimize`, not the bounded `merge` #499 offered as its alternative.** A
    `merge` does a page-budgeted amount of work and stops, so whatever it does not
    reach stays -- the channel attenuated rather than closed. Measured on a
    2,000-row table with half its rows deleted (2026-09-03, SQLite 3.47.1), a
    `merge` at rank 4 moved the posting bytes not at all, called once or twenty
    times over, where `optimize` took them 119,186 -> 53,154. `optimize` runs
    until the index is a single segment, which is the parity
    `test_purged_build_structure.py` asserts against a build that never held the
    rows.

    Priced before it shipped, because a merge is O(index) where the delete it
    follows is not (`time.perf_counter` around `derive_purged`, arms alternating,
    10-core arm64). **The ratio has two axes and they do not read alike**, so
    quoting one number for it would misdescribe whichever case the reader had:

    *Source size*, withdrawn held at a tenth of visible, medians of nine at load
    ~3 -- 4.3 MB 62 -> 100 ms; 17.6 MB 307 -> 524 ms; 35.3 MB 571 -> 965 ms;
    88.3 MB 1,568 -> 2,606 ms. **1.60x to 1.70x across a twentyfold span**, and
    the flatness is the reading that decides the trade: the merge is the same
    order as the page copy a purge already pays, so along this axis it is a
    constant factor rather than a term that overtakes the rest at scale.

    *Withdrawal count*, visible held at 10,000, medians of nine at load 8-14 (so
    the milliseconds are inflated and only the shape is claimed; two runs at
    different loads agree on it) -- 0 withdrawn 73 -> 472 ms; 500 436 -> 940 ms;
    2,000 657 -> 902 ms; 5,000 1,798 -> 2,065 ms. **6.4x falling to 1.15x**,
    because the merge's own cost tracks the index while the purge's tracks what it
    deletes: the ratio is worst exactly where the purge is cheapest, a
    residue-cleanup purge with nothing withdrawn, and there it is 472 ms on an
    11.6 MB index. The absolute cost, which is the one an operator waits on, stays
    bounded by index size on both axes.

    Cheap against the alternative either way -- ADR-0024's table above prices
    *re-deriving* a build at 2,614 ms for 12.3 MB and 37,684 ms for 150.3 MB, so a
    purge that merges still comes in about an order of magnitude under the rebuild
    it exists to avoid. And it is paid once per withdrawal, against a per-query
    cost it removes: on a 5,000-chunk corpus with 5,000 rows withdrawn, a
    substring query took 32.5 ms on a purged build before this and 16.0 ms after,
    against 16.6 ms on a build that never held the rows.

    **Scope: this ends the query-reachable face of T-17a and not the file face.**
    What a query walks is the segment structure, and after this that structure is
    a never-held build's. The *file* is not compacted -- the merged-away pages
    become free-list rather than returning to the filesystem, measured at 95.9%
    free-list on a purged build -- so anyone reading the raw file still sees where
    the withdrawn rows were. That face is #344's, which also records that this
    merge *increases* the free-list share it measures, and neither `secure_delete`
    nor `VACUUM` is taken here (the second would renumber the rowids four
    external-content indexes key on, which is the module docstring's second
    hazard).

    One residue survives on the query-reachable side and is named rather than
    claimed away: the **varint residue**, the rowid deltas a posting list encodes.
    Withdrawing rows widens the gaps between the surviving rowids, so a purged
    build's posting bytes still move with the withdrawn count -- measured 0.28% on
    `chunks_trigram` and 0.72% on `chunks_fts` across 0 to 400 withdrawn, monotone
    non-decreasing and saturating, 69x to 181x inside the band
    `test_purged_build_structure.py` asserts. It is the honest slack in that
    file's key, and it is a different quantity from the restamp residue above.

    A `sqlite3.Error` here is left to propagate rather than dressed as an
    `IndexPurgeError`, which is the opposite of what `_copy` does above.
    `publish_purge_for_withdrawal` catches it, reports `type(exc).__name__`, and
    attaches `PURGE_FAILED_REMEDY`, which already names the rebuild -- and it
    deliberately never surfaces an `IndexPurgeError.msg`, so wrapping here would
    cost the operator the type without adding a remedy.
    """
    with _writing(target) as connection:
        candidates = connection.execute(_FTS5_TABLE_CANDIDATES).fetchall()
        for row in candidates:
            if not _FTS5_DECLARATION.match(str(row["sql"])):
                continue
            # Quoted, and any embedded quote doubled: the identifier arrives from
            # `sqlite_master` rather than from this module, so it is data at this
            # point even though the schema that wrote it is ours. Pinned by the
            # `odd"name_fts` table `test_purge_full_text_discovery.py` plants --
            # without the doubling that name is a syntax error, not a silent
            # mis-target.
            quoted = '"{}"'.format(str(row["name"]).replace('"', '""'))
            # One transaction per table rather than one around all of them. An
            # `optimize` rewrites a whole index into a single segment, and holding
            # four of those open together is four indexes' worth of pages in the
            # WAL before any checkpoint can run.
            with connection:
                connection.execute(
                    f"INSERT INTO {quoted}({quoted}) VALUES ('optimize')"  # noqa: S608 - identifier is data here, quoted and doubled above
                )


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
