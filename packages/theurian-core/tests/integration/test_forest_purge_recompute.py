"""The withdrawal re-derivation over a fan-out and over several scopes at once.

Two properties of :func:`~theurian.application.withdrawal_purge._recompute_forest`
that ``test_forest_purge_equality.py`` does not reach, because they only appear at
a corpus shape the CLI-driven equality tests keep small:

1. **A withdrawal that re-batches a fanned-out Domain tier (HIGH).** Above
   :data:`~theurian.application.forest_builder.MAX_CHILDREN_PER_DOMAIN` a kind
   splits into deterministic batches ``kind#0 .. kind#(b-1)`` (``_domain_batches``).
   When a withdrawal drops the batch count ``b -> b-1``, the re-derivation over the
   survivors mints only ``kind#0`` -- so a *surviving* top batch ``kind#(b-1)``,
   none of whose members was withdrawn and which ``_delete`` therefore never
   dooms, keeps a ``tree_id`` that is **not** in the fresh set. Today
   ``_recompute_forest`` deletes only the fresh ``tree_ids``, misses that stale
   node, and the purge either publishes a doubled forest or -- as it does now --
   fails closed: the delete of the survivors' Document nodes cascades the stale
   node's edges away, leaving it unprovenanced, and ``_verify`` refuses the build
   (``IndexPurgeError``: "node(s) with no provenance"). Either way the purge does
   not re-derive the scope. The fix deletes the affected scope's **entire** current
   node set before inserting fresh, so the stale batch is reached.

   These tests are **RED ahead of that fix**: they assert the purge publishes, the
   purged forest equals a never-held build over the survivors, and the orphaned
   batch node is gone. They go green only once the delete covers the whole scope.

2. **A single withdrawal spanning two scopes (test-gap PIN).** ``_affected_scopes``
   must return *every* scope the withdrawal touched, or a second affected scope is
   delete-only -- a silently wrong forest. The equality file's withdrawal lands in
   one scope, so an ``_affected_scopes`` that returned only the first would survive
   it; this pins two scopes at once, and reddens under a ``LIMIT 1`` on that query.

**Driven at the store API, not the CLI, and the fan-out boundary is made cheap by
patching ``MAX_CHILDREN_PER_DOMAIN`` to 4 rather than building 500+ documents.**
The bug is that the re-derivation's ``tree_ids`` set misses a renumbered surviving
batch; that is a property of ``_domain_batches``' ``f"{kind}#{index}"`` keying and
the tail-merge (``tail < min_children -> pop``), both of which fire identically at
the small cap. The reviewers' ``e2_boundary_matrix.py`` ran the *real* cap at
n=503 and got the byte-identical failure this reproduces at n=7 -- "1 node(s) with
no provenance" for the victim-sorts-first case, a clean publish for
victim-sorts-last -- so the small cap exercises the same path at a hundredth of the
cost. Real SQLite files under ``tmp_path``; nothing reaches the developer's machine
and nothing starts a daemon.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest

from theurian.application import forest_builder
from theurian.application.forest_builder import ForestBuilder
from theurian.application.withdrawal_purge import make_forest_recompute
from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT: Final = "demo"
DOCUMENT_LEVEL: Final = 1
DOMAIN_LEVEL: Final = 2

#: The small fan-out cap the fan-out tests patch in. Chosen with the default
#: ``min_children_per_summary`` of 3 so that n=7 fans into two batches (4 + 3) and
#: withdrawing one member of the low batch collapses it to one (6 -> a single
#: ``kind#0`` of 502 documents at the real cap, of six here). Any value strictly
#: above ``min_children`` and small enough to build in milliseconds would do; 4 is
#: the smallest that still leaves a non-final batch above the floor.
SMALL_CAP: Final = 4

_SOURCE_BUILD: Final = "01K1" + "0" * 18 + "SRC1"
_PURGED_BUILD: Final = "01K1" + "0" * 18 + "PRG1"
_STATE_HASH: Final = "s" * 64


def _ulid(index: int) -> str:
    """A valid 26-character Crockford base32 ULID for document ``index``.

    Digits only after the ``01K1`` prefix, so no ``I``/``L``/``O``/``U`` can slip
    in -- asserted rather than assumed, because a runtime-assembled id bypasses the
    quoted-literal guard in ``tests/unit/test_test_fixtures.py``.
    """
    value = f"01K1{index:022d}"
    assert len(value) == 26, f"{value!r} is not a 26-character ULID"
    assert not set(value) & set("ILOU"), f"{value!r} is not Crockford base32"
    return value


@dataclass(frozen=True, slots=True)
class Doc:
    """One document, identified by ``index`` so its ids are stable across corpora.

    The re-derivation equality compares two builds row by row, and a survivor must
    carry the same content-addressed ids in the withheld build, the purged build
    and a never-held one. Keying every fact on ``index`` -- not on the document's
    position in a corpus -- is what makes a survivor the same node in all three.
    """

    index: int
    namespace: str = "backend"
    kind: str = "architecture"

    @property
    def revision_id(self) -> str:
        return _ulid(self.index)

    def chunks(self) -> list[IndexableChunk]:
        """Three chunks -- above ``min_children_per_summary`` -- unique to this doc.

        The per-document token keeps two documents from deriving identical chunk
        text, which would collapse two content-addressed node ids into one and make
        the equality hold for the wrong reason.
        """
        rev = self.revision_id
        return [
            IndexableChunk(
                chunk=Chunk(
                    chunk_id=f"{rev}#{ordinal}",
                    ordinal=ordinal,
                    text=(
                        f"Document {self.index} section {ordinal}. The gateway issues "
                        f"token m{self.index}s{ordinal} on restart. Rotation expires hourly."
                    ),
                    heading="Section",
                ),
                project_id=PROJECT,
                item_id=f"{self.kind}.doc-{self.index}",
                revision_id=rev,
                status="approved",
                sensitivity="internal",
                trust_level="reviewed",
                namespace=self.namespace,
                kind=self.kind,
            )
            for ordinal in range(3)
        ]


def _build(path: Path, docs: list[Doc]) -> None:
    """Write a --raptor build of ``docs`` to ``path``: chunks and the derived forest."""
    store = SqliteIndexStore(path)
    store.create(index_build_id=_SOURCE_BUILD, state_hash=_STATE_HASH)
    chunks = [chunk for doc in docs for chunk in doc.chunks()]
    store.add_chunks(chunks)
    nodes = ForestBuilder(summarizer=ExtractiveSummarizer()).derive(chunks)
    store.add_nodes(nodes, embedding_model="", embedding_model_revision="", embedding_dimension=0)


def _purge(source: Path, target: Path, *, revisions: list[str]) -> int:
    """Derive ``source`` minus ``revisions`` into ``target``, re-deriving the forest.

    The whole point under test: ``make_forest_recompute`` is the callback the CLI
    wires, so this exercises the real re-derivation, not a stand-in.
    """
    recompute = make_forest_recompute(
        store_factory=SqliteIndexStore,
        forest_builder=ForestBuilder(summarizer=ExtractiveSummarizer()),
        embedder=None,
    )
    return SqliteIndexStore(source).derive_purged(
        target,
        revision_ids=revisions,
        index_build_id=_PURGED_BUILD,
        state_hash=_STATE_HASH,
        recompute_forest=recompute,
    )


def _rows(path: Path, sql: str) -> list[dict[str, Any]]:
    """Every row of ``sql`` as plain dicts, on a handle ``closing`` disposes of.

    ``closing`` and not ``with sqlite3.connect(...)``: the latter commits without
    closing, and ``filterwarnings = error`` turns the leaked handle's
    ``ResourceWarning`` into a failure of whichever test is running.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


def _domain_tree_ids(path: Path) -> set[str]:
    return {str(row["tree_id"]) for row in _rows(path, "SELECT tree_id FROM nodes WHERE level = 2")}


@dataclass(frozen=True, slots=True)
class Forest:
    """A build's node graph in a form two builds can be compared by value.

    ``index_build_id`` is dropped -- it names the build, and two builds are two
    builds -- and every other node column plus the derivation edges are kept, so
    the comparison is over the whole derived layer, ids included (ADR-0008
    decision 9).
    """

    nodes: dict[str, tuple[tuple[str, Any], ...]]
    edges: tuple[tuple[Any, ...], ...]


def _forest(path: Path) -> Forest:
    nodes = {
        str(row["node_id"]): tuple(
            sorted((key, value) for key, value in row.items() if key != "index_build_id")
        )
        for row in _rows(path, "SELECT * FROM nodes")
    }
    edges = tuple(
        sorted(
            (str(e["node_id"]), e["source_chunk_id"], e["source_node_id"])
            for e in _rows(path, "SELECT * FROM node_derivation")
        )
    )
    return Forest(nodes=nodes, edges=edges)


def _document_index_at_sort(path: Path, position: int) -> int:
    """The ``index`` of the Document node at ``position`` in ``ORDER BY node_id``.

    ``_domain_batches`` slices documents in exactly this content-addressed order,
    so the node at sort position 0 is in the low (non-final) batch and the node at
    -1 is in the final batch -- the two cases ``e2_boundary_matrix.py`` names.
    """
    rows = _rows(path, "SELECT source_revision_id FROM nodes WHERE level = 1 ORDER BY node_id")
    revision = str(rows[position]["source_revision_id"])
    return int(revision[4:])


# -- The fan-out re-batch fix (RED ahead of the fix) --------------------------


def test_a_re_batching_withdrawal_re_derives_the_scope_and_drops_the_orphaned_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A withdrawal that collapses a fan-out b->b-1 must re-derive, not fail closed.

    The HIGH all three reviewers found. n=7 at the cap of 4 fans into ``kind#0``
    (four documents) and ``kind#1`` (three). Withdrawing one member of ``kind#0``
    -- the low, non-final batch -- leaves six survivors, which re-derive to a single
    ``kind#0``. ``kind#1``'s three members all survive, so ``_delete`` never dooms
    it; its ``tree_id`` is not in the fresh set, so a re-derivation that deletes only
    the fresh trees leaves it standing. The purge must instead publish a forest
    identical to a never-held build over the six survivors, with ``kind#1`` gone.

    RED today: the delete of the survivors' Document nodes cascades ``kind#1``'s
    edges away, ``_verify`` sees an unprovenanced node, and ``derive_purged`` raises
    ``IndexPurgeError`` -- reproduced at the real cap (n=503) in
    ``e2_boundary_matrix.py`` as the identical "node(s) with no provenance".
    """
    monkeypatch.setattr(forest_builder, "MAX_CHILDREN_PER_DOMAIN", SMALL_CAP)
    withheld = tmp_path / "withheld.db"
    _build(withheld, [Doc(i) for i in range(7)])
    assert len(_domain_tree_ids(withheld)) == 2, "the fixture did not fan the Domain tier out"
    victim_index = _document_index_at_sort(withheld, 0)
    survivors = [Doc(i) for i in range(7) if i != victim_index]

    never_held = tmp_path / "never.db"
    _build(never_held, survivors)
    stale_trees = _domain_tree_ids(withheld) - _domain_tree_ids(never_held)
    purged = tmp_path / "purged.db"
    removed = _purge(withheld, purged, revisions=[_ulid(victim_index)])

    assert removed > 0, "the purge removed nothing, so it did not run over the withdrawal"
    assert stale_trees, "the fixture built no orphanable batch, so this proves nothing"
    assert stale_trees.isdisjoint(_domain_tree_ids(purged)), (
        "the collapsed fan-out left a stale Domain batch node the re-derivation never deleted"
    )
    assert _forest(purged) == _forest(never_held), (
        "a re-batched purge must equal a build that never held the withdrawn rows"
    )


def test_a_withdrawal_from_the_final_batch_still_re_derives_the_collapsed_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The victim-sorts-last case: green today, and must stay green under the fix.

    Withdrawing a member of the *final* batch dooms ``kind#1`` and re-derives the
    six survivors to a single ``kind#0`` -- whose ``tree_id`` equals the old
    ``kind#0``'s, so the delete-only re-derivation happens to reach it and the purge
    already publishes (``e2_boundary_matrix.py`` at n=503 shows the same asymmetry).
    Pinned so the whole-scope delete the fan-out fix introduces does not regress the
    case that worked, and to hold that this collapse, too, equals a never-held build.
    """
    monkeypatch.setattr(forest_builder, "MAX_CHILDREN_PER_DOMAIN", SMALL_CAP)
    withheld = tmp_path / "withheld.db"
    _build(withheld, [Doc(i) for i in range(7)])
    assert len(_domain_tree_ids(withheld)) == 2, "the fixture did not fan the Domain tier out"
    victim_index = _document_index_at_sort(withheld, -1)
    survivors = [Doc(i) for i in range(7) if i != victim_index]

    never_held = tmp_path / "never.db"
    _build(never_held, survivors)
    purged = tmp_path / "purged.db"
    removed = _purge(withheld, purged, revisions=[_ulid(victim_index)])

    assert removed > 0, "the purge removed nothing, so it did not run over the withdrawal"
    assert _domain_tree_ids(purged) == _domain_tree_ids(never_held), (
        "the collapsed fan-out did not re-derive to the never-held build's single batch"
    )
    assert _forest(purged) == _forest(never_held), (
        "a final-batch withdrawal must equal a build that never held the withdrawn rows"
    )


def test_a_bulk_low_withdrawal_that_collapses_a_fan_out_still_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broader trigger: withdrawing a whole batch's worth of low documents.

    Not only the razor's-edge single withdrawal collapses a fan-out. n=8 at the cap
    of 4 fans into ``kind#0`` and ``kind#1`` (four each); withdrawing the three
    lowest-sorted documents -- all in ``kind#0`` -- leaves five survivors that
    re-derive to one ``kind#0``, orphaning the fully-surviving ``kind#1`` exactly as
    the single case does. ``e6_broader.py`` is this at n=520 with 18 withdrawals.

    RED today for the same reason as the exact-boundary case: the orphaned ``kind#1``
    fails ``_verify`` closed.
    """
    monkeypatch.setattr(forest_builder, "MAX_CHILDREN_PER_DOMAIN", SMALL_CAP)
    withheld = tmp_path / "withheld.db"
    _build(withheld, [Doc(i) for i in range(8)])
    assert len(_domain_tree_ids(withheld)) == 2, "the fixture did not fan the Domain tier out"
    low_indices = [_document_index_at_sort(withheld, position) for position in range(3)]
    survivors = [Doc(i) for i in range(8) if i not in low_indices]

    never_held = tmp_path / "never.db"
    _build(never_held, survivors)
    stale_trees = _domain_tree_ids(withheld) - _domain_tree_ids(never_held)
    purged = tmp_path / "purged.db"
    removed = _purge(withheld, purged, revisions=[_ulid(i) for i in low_indices])

    assert removed > 0, "the purge removed nothing, so it did not run over the withdrawal"
    assert stale_trees, "the fixture built no orphanable batch, so this proves nothing"
    assert stale_trees.isdisjoint(_domain_tree_ids(purged)), (
        "a bulk low withdrawal left a stale Domain batch node the re-derivation never deleted"
    )
    assert _forest(purged) == _forest(never_held), (
        "a bulk-collapsed purge must equal a build that never held the withdrawn rows"
    )


# -- Multi-scope affected set (PIN against an _affected_scopes LIMIT 1) --------


def test_a_single_withdrawal_spanning_two_scopes_re_derives_both(tmp_path: Path) -> None:
    """Every scope the withdrawal touched must re-derive, not only the first.

    ``_affected_scopes`` returns the distinct scopes of the withdrawn chunks; if it
    returned only one, the other affected scope would be delete-only -- its Domain
    node deleted and never rebuilt -- a silently wrong forest. Two scopes here,
    ``backend`` and ``frontend`` (differing in the namespace component of the scope
    tuple), each a kind of four documents; one purge withdraws a document from
    **each**. Both scopes must re-derive a Domain node over their three survivors,
    equal to a never-held build of each scope's survivors.

    The equality file's withdrawal lands in a single scope, so an
    ``_affected_scopes`` truncated to ``LIMIT 1`` would pass it. This reddens under
    that mutation because whichever scope is dropped is left with no Domain node.
    """
    backend = [Doc(i, namespace="backend") for i in range(4)]
    frontend = [Doc(10 + i, namespace="frontend") for i in range(4)]

    withheld = tmp_path / "withheld.db"
    _build(withheld, [*backend, *frontend])
    withdrawn = [backend[0].revision_id, frontend[0].revision_id]
    survivors = [*backend[1:], *frontend[1:]]

    never_held = tmp_path / "never.db"
    _build(never_held, survivors)
    purged = tmp_path / "purged.db"
    removed = _purge(withheld, purged, revisions=withdrawn)

    assert removed > 0, "the purge removed nothing, so it did not run over the withdrawal"
    purged_forest = _forest(purged)
    never_forest = _forest(never_held)
    domain_nodes = _rows(purged, "SELECT node_id FROM nodes WHERE level = 2")
    assert len(domain_nodes) == 2, (
        "both affected scopes must re-derive a Domain node; only one did, so a scope was "
        "dropped from the affected set"
    )
    assert purged_forest == never_forest, (
        "a two-scope withdrawal must re-derive both scopes to the never-held build"
    )
