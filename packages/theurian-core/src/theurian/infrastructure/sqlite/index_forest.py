"""Retrieval *through* the RAPTOR forest, and a surfaced leaf's ancestry.

Split from :mod:`theurian.infrastructure.sqlite.index_store` for the reason
:mod:`theurian.infrastructure.sqlite.index_scan` and
:mod:`theurian.infrastructure.sqlite.index_query` were: that file is already the
largest in the package, and forest retrieval is a distinct concern from the leaf
retrievers. The connection, the error mapping and the scope predicates stay with
the store; what lives here is the SQL that routes a query through summary nodes
to the leaves beneath them, and the upward walk that reconstructs a surfaced
leaf's path back to a catalog root.

**The disclosure closure this file is half of (SEC-13, T-15, ADR-0008 dec. 8).**
A summary node's text is a summariser's output over its children, so it repeats
their content -- which is exactly why routing through it, and publishing its
title, must never widen what a caller may read:

- :func:`summary_statement` filters the *node* match on the same scope the leaf
  retrievers filter on (Project, the deployment's disclosure grant, and status
  unless the caller asked for drafts), so a draft-scope or above-ceiling summary
  is not even traversed on a default query, and it filters the *descended leaves*
  again -- the double gate. The caller then
  re-clears every descended leaf through
  :class:`~theurian.application.visibility.CanonicalVisibility` in
  :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`,
  exactly as it does every other retriever's rows. Routing decides which leaves
  are *candidates*; it never decides whether a gated row may surface.
- :func:`walk_raptor_path` is only ever called for a leaf that cleared that gate
  (:meth:`~theurian.application.retrieval_service.ResultGate._surfaced`), and a
  node's children share its six-component scope by construction (ADR-0008
  decision 1, :class:`~theurian.domain.raptor.SummaryNode`), so every ancestor of
  a cleared leaf is in that leaf's own scope. That invariant is what the domain
  layer enforces at construction, not a promise this file could rely on alone
  against a hand-edited or corrupted file, so the walk's own ``nodes`` lookup
  filters on the leaf's project and status too -- a second, independent gate a
  scope-disagreeing ancestor cannot clear even if the invariant above were ever
  violated. An ancestor title therefore carries no content from a scope the leaf
  is not in, and a *withheld* leaf contributes no result and so no path -- its
  ancestors' titles never reach the wire. The title's build-time staleness is the
  recorded T-17a/#130 residual, the same one every excerpt carries, not a new
  channel.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Final

from theurian.domain.raptor import MAX_LEVEL
from theurian.domain.retrieval import RaptorPathSegment, excerpt

#: Route a query to leaves through the forest, in one statement.
#:
#: Three stages, all inside SQLite so no un-gated row ever crosses into Python:
#:
#: 1. ``matched`` -- summary nodes whose text matches the query, scoped by
#:    ``node_clauses`` (the *first* gate). Both FTS tables are consulted, unioned,
#:    so a Latin word matches through ``nodes_fts`` and a CJK substring through
#:    ``nodes_trigram`` -- the same pair the leaf retrievers split across
#:    ``search_lexical`` and ``search_substring``. The seed carries each node's
#:    ``bm25`` so a leaf can inherit the score of the summary that reached it;
#:    the two tables' scores are not strictly comparable, but only their order
#:    within this one ranking is used, and RRF fuses on rank, not score.
#: 2. ``descendants`` -- the downward closure of the matched nodes over
#:    ``source_node_id``. ``UNION`` (not ``UNION ALL``) dedups ``(node, score)``
#:    pairs, so a diamond in the forest cannot loop the recursion.
#: 3. the leaf select -- the chunks grounded in any descendant node, scoped again
#:    by ``leaf_clauses`` (the *second* gate) and ranked by the best (``min``)
#:    summary score that reached each, ``chunk_id`` breaking ties so the order is
#:    total and reproducible (FR-R7).
#:
#: ``LIMIT`` is appended by the caller as ``? `` and bound to ``limit + 1``, so
#: this is a true ceiling that answers its own exhaustion exactly as
#: ``search_lexical`` does -- the extra row says whether more remains and is then
#: dropped.
_MATCHED_SEED: Final = (
    "SELECT nodes.node_id, {score} "
    "FROM {index_table} CROSS JOIN nodes ON nodes.rowid = {index_table}.rowid "
    "WHERE {index_table} MATCH ? AND {node_where}"
)


def summary_statement(  # noqa: PLR0913 - the node gate and the leaf gate each take a clause list and its bound values
    *,
    fts_expression: str,
    trigram_expression: str,
    node_clauses: Sequence[str],
    node_scope: Sequence[object],
    leaf_clauses: Sequence[str],
    leaf_scope: Sequence[object],
) -> tuple[str, tuple[object, ...]]:
    """The forest-routing SQL and its bound arguments (see :data:`_MATCHED_SEED`).

    The caller guarantees at least one expression is non-empty -- a query that
    forms neither an FTS term nor a trigram cannot route through the forest, and
    the store returns an empty exhausted page rather than build a ``matched`` CTE
    with no arms. Every user value below is a bound ``?``; the interpolated text
    is this module's own literals and the store's scope clauses.
    """
    node_where = " AND ".join(node_clauses)
    seeds: list[str] = []
    args: list[object] = []
    for expression, index_table, score in (
        (fts_expression, "nodes_fts", "bm25(nodes_fts)"),
        (trigram_expression, "nodes_trigram", "bm25(nodes_trigram)"),
    ):
        if not expression:
            continue
        seeds.append(
            _MATCHED_SEED.format(score=score, index_table=index_table, node_where=node_where)
        )
        args.append(expression)
        args.extend(node_scope)
    matched = " UNION ALL ".join(seeds)
    leaf_where = " AND ".join(leaf_clauses)
    # Every interpolation below is module-owned text: `matched` is `_MATCHED_SEED`
    # filled with this file's literals and the store's scope clauses, and
    # `leaf_where` is those clauses joined. Every user value is a bound `?`.
    sql = (
        f"WITH RECURSIVE matched(node_id, score) AS ({matched}), "  # noqa: S608 - clauses are module-owned literals; values are bound
        "descendants(node_id, score) AS ("
        "SELECT node_id, score FROM matched "
        "UNION "
        "SELECT e.source_node_id, d.score FROM node_derivation e "
        "JOIN descendants d ON e.node_id = d.node_id "
        "WHERE e.source_node_id IS NOT NULL"
        ") "
        "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, min(d.score) AS rank_score "
        "FROM descendants d "
        "JOIN node_derivation e ON e.node_id = d.node_id AND e.source_chunk_id IS NOT NULL "
        "JOIN chunks ON chunks.chunk_id = e.source_chunk_id "
        f"WHERE {leaf_where} "
        "GROUP BY chunks.chunk_id, chunks.item_id, chunks.revision_id "
        "ORDER BY rank_score, chunks.chunk_id LIMIT ?"
    )
    args.extend(leaf_scope)
    return sql, tuple(args)


def walk_raptor_path(
    connection: sqlite3.Connection, revision_id: str, project_id: str
) -> tuple[RaptorPathSegment, ...]:
    """One surfaced leaf's forest ancestry, catalog root to leaf.

    Runs inside the store's ``_read`` block, so the ``int``/``str`` conversions
    below are covered by its corruption mapping -- a level or text cell holding
    the wrong storage class becomes an unreadable-index refusal, not a bare
    traceback at an agent.

    A revision's chunks all belong to one Document node in a well-formed forest;
    the walk anchors there and climbs ``source_node_id`` until a node has no
    parent, capped at :data:`~theurian.domain.raptor.MAX_LEVEL` steps -- the
    deepest a well-formed forest goes, so a cycle in a tampered file cannot spin
    this loop forever; it just stops climbing, which is the same "fails towards a
    shorter path" the membership guard below already promises for a different
    kind of inconsistency. A revision no forest was derived from -- a chunk-only
    build, or a leaf whose scope never cleared a tier's ``min_children`` floor --
    has no Document node, so this returns ``()`` and the caller emits no
    ``raptorPath``.

    The final ``nodes`` lookup is scoped to the leaf's own ``project_id`` and
    ``status`` -- read off the same chunk row the walk anchors from, never a
    hardcoded ``approved``, so an ``include_unapproved`` caller's draft leaf keeps
    its (also draft) ancestors. A node id read from ``node_derivation``
    references ``nodes`` under a foreign key, so *finding* the row is never in
    doubt; the membership guard drops one whose scope disagrees with the leaf's,
    the defense in depth the module docstring describes, or whose file is simply
    inconsistent -- both fail towards a shorter path rather than a crash or a
    leaked title.
    """
    leaf_rows = list(
        connection.execute(
            "SELECT chunk_id, status FROM chunks WHERE project_id = ? AND revision_id = ?",
            (project_id, revision_id),
        )
    )
    if not leaf_rows:
        return ()
    # Every chunk of one revision is indexed from the same `IndexableChunk` call
    # and so carries the same `status` (`SqliteIndexStore.add_chunks`) -- reading
    # it off the first row is not an assumption this query makes, only one it
    # does not need to re-derive.
    leaf_status = str(leaf_rows[0]["status"])
    chunk_ids = [str(row["chunk_id"]) for row in leaf_rows]
    placeholders = ",".join("?" * len(chunk_ids))
    document = connection.execute(
        f"SELECT DISTINCT node_id FROM node_derivation "  # noqa: S608 - placeholders only
        f"WHERE source_chunk_id IN ({placeholders}) ORDER BY node_id",
        chunk_ids,
    ).fetchone()
    if document is None:
        return ()

    leaf_to_root: list[str] = [str(document["node_id"])]
    for _ in range(MAX_LEVEL - 1):
        parent = connection.execute(
            "SELECT node_id FROM node_derivation WHERE source_node_id = ? ORDER BY node_id",
            (leaf_to_root[-1],),
        ).fetchone()
        if parent is None:
            break
        leaf_to_root.append(str(parent["node_id"]))

    node_placeholders = ",".join("?" * len(leaf_to_root))
    rows = {
        str(row["node_id"]): row
        for row in connection.execute(
            f"SELECT node_id, level, text FROM nodes "  # noqa: S608 - placeholders only
            f"WHERE node_id IN ({node_placeholders}) AND project_id = ? AND status = ?",
            [*leaf_to_root, project_id, leaf_status],
        )
    }
    return tuple(
        RaptorPathSegment(
            node_id=node_id,
            level=int(rows[node_id]["level"]),
            title=excerpt(str(rows[node_id]["text"])),
        )
        for node_id in reversed(leaf_to_root)
        if node_id in rows
    )
