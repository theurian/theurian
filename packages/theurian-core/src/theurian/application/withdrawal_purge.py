"""Purging a still-published index the moment a withdrawal lands (ADR-0024 decision 5).

The gate takes a withdrawn row out of a *result*; it cannot take it out of the
BM25 collection statistics the surviving rows are scored against, so while a
published build still holds the row the visible ranking moves with content no
caller may read (threat model T-17a). Closing that window is a *removal from the
published build*, and the only quantity under this module's control is how long
the stale build stays published: the swap protects the next request, never one
already served (ADR-0024 decision 5).

**A purge is a build, and it goes through the same pointer swap `index build`
does** -- a new file, then an atomic rename of ``active-index.json``. Nothing
writes to the file the pointer already names (ADR-0024 point 1), so a search
in flight keeps reading the build it started on.

**Run after the write transaction commits, never inside it.** `purge_into` is a
whole-file backup, delete and verify; holding that across a write transaction
would block every other writer for its duration, which NFR-8 and ADR-0018 point 5
forbid. The purge reads the *published index*, not canonical state, so it needs
only the withdrawal committed -- not the lock that committed it.

**Everything the published build already declared is preserved, nothing is
overwritten.** The state hash, the ``projectId``, and the draft coverage all come
off the pointer being purged, not off the caller. The state hash because a purge
removes rows and does not add the content a later migration in the same apply may
have introduced, so stamping the new canonical state would report a build still
missing that content as fresh -- the silent-staleness failure this codebase
refuses. The ``projectId`` because a build's chunks are stamped with the id that
wrote them; a purge that adopted the caller's id would flip the pointer's after a
rename and make ``knowledge.search`` answer ``count: 0, indexed: true`` for
content that is really there. The T-17a property is unaffected -- it is that the
*ranking* of the visible rows no longer counts the withdrawn ones, which the FTS5
delete establishes regardless of the metadata (ADR-0024's measurement compares
chunk ids and scores).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from theurian.application.authorization import decode_sensitivities
from theurian.application.forest_builder import ForestBuilder
from theurian.application.index_builder import EMBED_BATCH
from theurian.application.migration_engine import WithdrawalCandidate, revisions_to_purge
from theurian.application.project_service import (
    ProjectPaths,
    mark_active_index_purge_failed,
    read_active_index_pointer,
    write_active_index_pointer,
)
from theurian.domain.chunking import ChunkScope, IndexableChunk
from theurian.domain.ports.determinism import IdGenerator
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ports.index_store import ForestRecompute
from theurian.domain.raptor import IndexableNode


class PurgeableIndex(Protocol):
    """What this use case asks of a published build.

    Narrower than :class:`~theurian.domain.ports.index_store.IndexStore` on
    purpose: the use case decides whether the build can be read at all
    (``is_searchable``), whether it holds anything worth purging
    (``holds_any_revision``), and, if so, derives a purged copy
    (``derive_purged``). A narrow protocol is what keeps the concrete SQLite
    adapter named only at the composition root (ADR-0003), never here.
    """

    def is_searchable(self) -> bool: ...

    def holds_any_revision(self, revision_ids: Sequence[str]) -> bool: ...

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
        recompute_forest: ForestRecompute | None = None,
    ) -> int: ...


class ForestRecomputeStore(Protocol):
    """What the re-derivation asks of a store bound to the *building* file.

    Narrower than :class:`~theurian.domain.ports.index_store.IndexStore`, and a
    different object than :class:`PurgeableIndex`: that one reads the published
    build the purge copies *from*, this one reads and writes the copy the purge is
    assembling. The concrete adapter is the same class, bound to a different path,
    and named only at the composition root (ADR-0003) -- so the callback that uses
    this holds it as a protocol and the CLI passes ``SqliteIndexStore``.

    ``surviving_chunks`` reads the copy's rows back as the builder's own input,
    ``delete_nodes_grounded_in_chunks`` clears an affected scope's *entire* current
    node set, and ``add_nodes``/``add_node_embeddings`` write the re-derived forest
    -- the same two writes a fresh build makes, so a re-derived scope and a
    never-built one take identical paths. ``metadata`` is read to tell a build that
    carried chunk embeddings from one built ``--no-embeddings``, so the re-derived
    nodes are vectorised exactly when a never-held build's would be.
    """

    def metadata(self) -> Mapping[str, object]: ...

    def surviving_chunks(self, *, project_id: str) -> Sequence[IndexableChunk]: ...

    def delete_nodes_grounded_in_chunks(self, chunk_ids: Sequence[str]) -> None: ...

    def add_nodes(
        self,
        nodes: Sequence[IndexableNode],
        *,
        embedding_model: str,
        embedding_model_revision: str,
        embedding_dimension: int,
    ) -> int: ...

    def add_node_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int: ...


#: A purge did not run, and the reason, in the vocabulary a command reports.
#:
#: These are states, not failures: a project with no index, or a withdrawal whose
#: revisions no published build holds, has nothing to purge and is correct.
NO_WITHDRAWAL: str = "no-withdrawal"
NO_PUBLISHED_INDEX: str = "no-published-index"
INDEX_UNUSABLE: str = "index-unusable"
NOTHING_TO_PURGE: str = "nothing-to-purge"

#: The published build a purge would copy forward was not produced by this
#: installation, so the composition root declines to purge it: copying an
#: unprovenanced build's surviving rows into a fresh build and recording that
#: build would launder a committed, doctored index into a provenanced one the
#: serve path trusts. Reported (not raised) so the skip is visible, and named
#: here because it is part of the ``reason`` vocabulary a command reports even
#: though the gate that produces it lives at the composition root (ADR-0004,
#: SEC-7).
UNTRUSTED_SOURCE_INDEX: str = "untrusted-source-index"

#: What an operator does when the purge itself failed. The index is derived
#: (ADR-0004), so the cure is always a rebuild -- and it is the load-bearing half
#: of the failure report, because until it runs the current build still holds the
#: withdrawn rows and is therefore stood aside from the serve path
#: (GHSA-97q9-xxfg-33r6), leaving retrieval on the unranked canonical scan.
#:
#: Note the two sibling messages in `infrastructure/sqlite/index_purge.py` that
#: still read "retrieval still uses the current index" are left as-is on purpose:
#: they are `IndexPurgeError.msg` fragments, and the except block above surfaces
#: only `type(exc).__name__`, never `.msg`, so their text never reaches an
#: operator -- and `index_purge` is infrastructure that must not name the
#: application-layer taint (ADR-0003). Do not "fix" them into a layering violation.
PURGE_FAILED_REMEDY: str = (
    "Nothing was published. The current index still holds the withdrawn rows, so "
    "it is no longer served: retrieval falls back to an unranked scan of canonical "
    "state until a rebuild. Run `theurian index build` to produce a clean build, "
    "which clears the taint; the index is derived, so nothing authored is lost."
)


@dataclass(frozen=True, slots=True)
class WithdrawalPurge:
    """What the withdrawal-triggered purge did, for the command to report.

    ``published`` is the security-relevant bit: a purge that was *needed* -- a
    withdrawal against a readable published build that holds the rows -- and did
    not publish means the stale build is still serving the withdrawn rows'
    statistics, and the operator has to know to rebuild. ``failed`` distinguishes
    "there was nothing to do" from "there was, and it did not complete", and
    carries ``remedy`` because only the second needs one.
    """

    published: bool
    index_build_id: str | None = None
    removed: int = 0
    #: Empty when a purge published; otherwise why it did not, which is either a
    #: benign state above or ``failed`` with a purge that raised.
    reason: str = ""
    failed: bool = False
    #: The command to run when ``failed``; empty otherwise.
    remedy: str = ""

    def __post_init__(self) -> None:
        if self.published and (self.reason or self.failed):
            msg = "a published purge carries no reason and did not fail"
            raise ValueError(msg)
        if self.published and self.index_build_id is None:
            msg = "a published purge names the build it published"
            raise ValueError(msg)
        if self.remedy and not self.failed:
            msg = "only a failed purge carries a remedy"
            raise ValueError(msg)


def publish_purge_for_withdrawal(  # noqa: PLR0911 - one early return per benign no-op state
    paths: ProjectPaths,
    *,
    withdrawal_candidates: Sequence[WithdrawalCandidate],
    ids: IdGenerator,
    index_factory: Callable[[Path], PurgeableIndex],
    recompute: ForestRecompute | None = None,
) -> WithdrawalPurge:
    """Publish a copy of the current index with the withdrawn revisions removed.

    Which revisions those are depends on the published index's **own build
    flavor** -- both axes of it, ``indexesUnapproved`` and
    ``indexedSensitivities`` -- which only the pointer records, so it is resolved
    here rather than in the engine (`revisions_to_purge`): a doc made ``draft`` in
    place is withheld from a default index but legitimately held by an
    ``--include-unapproved`` one, and an item reclassified to ``confidential`` is
    withheld from a build made at an ``internal`` ceiling but legitimately held by
    one made under the shipped default (#119, ADR-0025 part 2).

    Does nothing, cheaply, when there is nothing to do: no withdrawal touched an
    item, nothing that survives the flavor reduction, no published build, or a
    published build that holds none of what would be purged. That last case is the
    common one -- ``migrate apply`` replays the whole set whenever the state hash
    shifts (ADR-0016), so a project with any past withdrawal would otherwise copy
    its whole index on every apply -- and it is caught by ``holds_any_revision``
    before any file is copied, so a no-op apply pays a bounded read rather than a
    whole-file copy.

    All-or-nothing. `purge_into` unlinks its partial output on any failure, this
    function publishes the pointer only after ``derive_purged`` returns a non-zero
    count, and it discards the completed copy if the pointer write itself fails --
    so a purge that raises, or that would republish an identical build, leaves the
    previously published build serving and no orphan behind. A failure is reported
    through ``failed`` (with a remedy) so the operator can rebuild rather than
    discovering the still-withheld rows in a leak.
    """
    if not withdrawal_candidates:
        return WithdrawalPurge(published=False, reason=NO_WITHDRAWAL)

    published = read_active_index_pointer(paths).payload
    if published is None:
        # No build, or a pointer that names none: nothing holds the withdrawn
        # rows. A corrupt pointer takes this branch too -- its remedy is a
        # rebuild, which produces a clean build without the withdrawn rows anyway.
        return WithdrawalPurge(published=False, reason=NO_PUBLISHED_INDEX)

    build_id = str(published.get("indexBuildId", ""))
    # Everything below is read off the pointer being purged, never the caller --
    # see the module docstring on why the state hash and the project id are
    # preserved rather than restamped. `indexesUnapproved` is the flavor the
    # withdrawn set is computed against, not just metadata to carry forward.
    source_state_hash = str(published.get("stateHash", ""))
    source_project_id = str(published.get("projectId", ""))
    indexes_unapproved = bool(published.get("indexesUnapproved", False))
    indexed_sensitivities = decode_sensitivities(published.get("indexedSensitivities"))
    if indexed_sensitivities is None:
        # The second flavor, and the one this cannot invent (#119, ADR-0025). It
        # is read twice below -- carried forward onto the new pointer, and handed
        # to `revisions_to_purge` as the ceiling a reclassification is judged
        # against -- and a guess would be wrong in both. A purge copies a build and
        # deletes rows from the copy, so the copy holds exactly what the original
        # was allowed to hold, and the pointer is the only record of what that was.
        # A build whose flavor the pointer does not state is already one retrieval
        # stands aside from (`mcp.search._published_index`), so it is not serving
        # the withdrawn rows' statistics to anybody, and republishing it under a
        # *guessed* flavor is how a guess becomes the record. Reported as unusable,
        # whose standing remedy is the rebuild that fixes both.
        return WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)

    deduped = tuple(
        revisions_to_purge(
            withdrawal_candidates,
            indexes_unapproved=indexes_unapproved,
            indexed_sensitivities=indexed_sensitivities,
        )
    )
    if not deduped:
        # The apply touched items, but none of their revisions is withheld from
        # *this* build's flavor -- e.g. a draft that an --include-unapproved index
        # legitimately holds, or an item reclassified within the ceiling this build
        # ran under (#119). Nothing to purge.
        return WithdrawalPurge(published=False, reason=NO_WITHDRAWAL)

    orphan: Path | None = None
    try:
        source = paths.index_for(build_id)
        if not source.is_file():
            # The pointer outlived its file. Nothing published holds the rows, so
            # this is a benign state, not a failure to purge.
            return WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)
        current = index_factory(source)
        if not current.is_searchable():
            # An unreadable file or a schema this build does not understand is one
            # retrieval already falls back past (mcp.search), so it never scores
            # the withdrawn rows and carries no T-17a channel. Purging it would
            # only fail on the missing tables; a rebuild is the standing remedy.
            return WithdrawalPurge(published=False, reason=INDEX_UNUSABLE)
        if not current.holds_any_revision(deduped):
            # The published build holds nothing `derive_purged` would remove:
            # neither a chunk of the withdrawn revisions nor a node the purge
            # would remove -- `holds_any_revision` runs the same SQL literals the
            # purge's own predicate is built from, so the two cannot disagree.
            # Copying it to delete nothing and republishing an identical build is
            # pure churn -- the common replay case for a project with any past
            # withdrawal -- so skip before any file is copied.
            return WithdrawalPurge(published=False, reason=NOTHING_TO_PURGE)

        # No new index-write lock is taken. Safety against a concurrent producer
        # rests on the same two mechanisms `index build` uses (ADR-0022, #113): a
        # fresh ULID sorts above the published one so `theurian index gc` never
        # reaps this build before it publishes, and `purge_into` writes under a
        # `.building` name and `os.replace`s into position, so a file under the
        # final name is complete by construction. The single index-writer
        # interface ADR-0018 point 1 still owes the index is entangled with this
        # purge -- both are "productions of a new build" -- and is tracked in
        # issue #15's follow-through rather than opened here.
        new_id = ids.new_ulid().value
        target = paths.index_for(new_id)
        removed = current.derive_purged(
            target=target,
            revision_ids=deduped,
            index_build_id=new_id,
            state_hash=source_state_hash,
            recompute_forest=recompute,
        )
        if removed == 0:
            # A race the widened pre-check could not see (the rows left between the
            # check and the delete): nothing left the copy, so publishing it is
            # churn. Drop the orphan rather than swap the pointer to a build
            # identical to the one it names.
            _discard(target)
            return WithdrawalPurge(published=False, reason=NOTHING_TO_PURGE)

        # The copy is complete and would be orphaned if the swap now fails, so
        # track it until the pointer names it.
        orphan = target
        write_active_index_pointer(
            paths,
            index_build_id=new_id,
            state_hash=source_state_hash,
            project_id=source_project_id,
            indexes_unapproved=indexes_unapproved,
            indexed_sensitivities=indexed_sensitivities,
        )
        orphan = None
    except Exception as exc:  # fail closed: any adapter's failure leaves the old build serving
        # The withdrawal is already committed to canonical state; only the index
        # follow-up failed. Report it rather than raising, so the command that
        # applied the migration does not report the apply itself as failed -- and
        # so the still-published stale build is named as a thing to rebuild, not
        # left silent (ADR-0024 decision 5). The type name, not the message: an
        # `IndexPurgeError` and a `sqlite3.OperationalError` name different
        # repairs, but the message carries the operator's absolute paths, which
        # `index_purge` is careful to keep out of a reply and this must not put
        # back (the remedy is the actionable half).
        if orphan is not None:
            # `derive_purged` cleans its own partial output; this is the *complete*
            # copy left when the pointer write failed after it. Symmetric with the
            # `removed == 0` path, so a failed swap never strands a build `gc` will
            # not reap (its id sorts above the published one).
            _discard(orphan)
        # The stale build is still published and still holds the withdrawn rows,
        # so taint its pointer: the serve path stands a tainted build aside whole
        # rather than ranking visible rows against text no caller may read, and a
        # `--raptor` build's summary nodes carry that text verbatim into a visible
        # sibling's `raptorPath` (GHSA-97q9-xxfg-33r6, T-17a). `build_id` is the
        # id the *old* pointer names, and it still names it here -- both the
        # `derive_purged`-raised path (nothing was published) and the pointer-
        # write-failed path (the swap did not land, `os.replace` being atomic).
        # A concurrent `index build` may have published a clean build in the
        # meantime, which is why the taint is conditional on the pointer still
        # naming this build and never raises (`mark_active_index_purge_failed`):
        # its return is not consulted, because the purge already failed and this
        # report stands whether the taint applied or degraded to it.
        mark_active_index_purge_failed(paths, expected_build_id=build_id)
        return WithdrawalPurge(
            published=False,
            reason=f"purge-failed: {type(exc).__name__}",
            failed=True,
            remedy=PURGE_FAILED_REMEDY,
        )

    return WithdrawalPurge(published=True, index_build_id=new_id, removed=removed)


def _discard(build: Path) -> None:
    """Unlink a build file that will not be published, and its WAL sidecars.

    A purge that removed nothing wrote a complete copy under the final name (via
    `os.replace`); leaving it would strand a file `theurian index gc` does not
    reap, because its id sorts above the published one.
    """
    build.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(build) + suffix).unlink(missing_ok=True)


def make_forest_recompute(
    *,
    store_factory: Callable[[Path], ForestRecomputeStore],
    forest_builder: ForestBuilder,
    embedder: EmbeddingProvider | None,
) -> ForestRecompute:
    """The re-derivation callback the purge runs, closing over its collaborators.

    Built at the composition root so the application layer names no adapter: the
    CLI constructs the ``ForestBuilder`` (over the extractive default) and the
    embedder and hands them here, and the returned callable is passed to
    ``publish_purge_for_withdrawal`` and rides through to
    :func:`~theurian.infrastructure.sqlite.index_purge.purge_into`, which calls it
    on the building file (ADR-0003).

    **The default extractive summariser is deterministic, which is what makes the
    re-derivation reproduce a never-held build rather than merely resemble it**
    (ADR-0008 decision 9). A non-deterministic provider -- none exists today --
    could not, and its correct behaviour is a *different* path: delete the
    affected trees' nodes and record the forest stale, forfeiting the equality
    rather than faking it (ADR-0008 decision 9's final paragraphs). This callback
    assumes determinism and does not branch on it; the day such a provider is
    configured, that is the CL that adds the delete-and-mark-stale branch, not a
    dead one carried here in the meantime.
    """

    def recompute(building: Path, affected_scopes: Sequence[ChunkScope]) -> None:
        _recompute_forest(
            building,
            affected_scopes,
            store_factory=store_factory,
            forest_builder=forest_builder,
            embedder=embedder,
        )

    return recompute


def _recompute_forest(
    building: Path,
    affected_scopes: Sequence[ChunkScope],
    *,
    store_factory: Callable[[Path], ForestRecomputeStore],
    forest_builder: ForestBuilder,
    embedder: EmbeddingProvider | None,
) -> None:
    """Rebuild each affected scope's trees over the building file's surviving rows.

    The whole scope re-derives, not just the doomed nodes: a withdrawal changes a
    Domain node's *member set*, and content-addressing then moves the survivor's
    id and text with it (ADR-0008 decision 9), so a survivor is a different node
    rather than the old one minus a child -- and the Catalog above it is rebuilt
    over whichever Domain nodes remain. Unaffected scopes are never read, so their
    copied nodes stay byte-identical.

    Deletion removes the affected scope's **entire** current node set -- every node
    upward-reachable from its surviving chunks, not just the ones the fresh trees
    name (``delete_nodes_grounded_in_chunks``). A withdrawal that collapses a Domain
    fan-out ``b -> b-1`` leaves a surviving top batch ``kind#(b-1)`` whose members
    were all kept and which the fresh derivation, minting only ``kind#0``, does not
    reproduce: a delete by fresh tree ids would miss it, the cascade would strip its
    edges when the survivors' Document nodes were re-derived, and ``_verify`` would
    refuse the purge over the unprovenanced remnant. Clearing the whole scope
    reaches that stale batch, because it still grounds on the scope's surviving
    chunks. The earlier reliance on a primary-key collision to fail closed was an
    accidental net over an incomplete delete, not the mechanism, and is no longer
    relied on: the delete is now exact over the scope by construction.
    """
    affected = frozenset(affected_scopes)
    if not affected:
        return
    store = store_factory(building)
    project_id = next(iter(affected)).project_id
    scoped = [
        chunk
        for chunk in store.surviving_chunks(project_id=project_id)
        if chunk.scope_key in affected
    ]
    if not scoped:
        # Every affected scope lost all of its chunks. The nodes that stood on
        # them were doomed and already deleted, so there is nothing left to
        # rebuild -- and a never-held corpus of the (empty) survivors has no forest
        # either.
        return

    nodes = forest_builder.derive(scoped)
    if not nodes:
        # The survivors fall below every tier's threshold, so no fresh node is
        # produced. A withdrawal never changes an item's chunk count -- a revision
        # is withheld whole or not at all -- so every surviving Document node's
        # chunks are all present and this derivation would have reproduced it. Its
        # absence therefore means no old node lingers in the scope to delete either.
        return

    active = _embedder_for(store, embedder)
    store.delete_nodes_grounded_in_chunks([chunk.chunk.chunk_id for chunk in scoped])
    store.add_nodes(
        nodes,
        embedding_model=active.model_id if active is not None else "",
        embedding_model_revision=active.model_revision if active is not None else "",
        embedding_dimension=active.dimension if active is not None else 0,
    )
    if active is not None:
        _embed_nodes(store, nodes, active)


def _embedder_for(
    store: ForestRecomputeStore, embedder: EmbeddingProvider | None
) -> EmbeddingProvider | None:
    """The embedder to re-vectorise nodes with, or ``None`` for an unembedded build.

    A purge must reproduce the build it copies, embeddings included: a build made
    ``--no-embeddings`` carries no vector and no embedding identity on its nodes,
    and a re-derivation that added them would make the purged forest differ from a
    never-held one for a reason the withdrawal did not cause. The building file's
    ``index_metadata`` names a model exactly when chunk embeddings were written
    (``record_embedding_model``), so it is the signal -- and the injected embedder
    is the one that wrote them, since a single embedder is configured per run.
    """
    if embedder is None:
        return None
    return embedder if store.metadata().get("embedding_model") else None


def _embed_nodes(
    store: ForestRecomputeStore, nodes: Sequence[IndexableNode], embedder: EmbeddingProvider
) -> None:
    """Vectorise the re-derived nodes, batched as :meth:`IndexBuilder._embed_nodes` is.

    Deterministic per node text, so the vectors equal a never-held build's; the
    batch bound is memory, not correctness (``EMBED_BATCH``).
    """
    for start in range(0, len(nodes), EMBED_BATCH):
        batch = nodes[start : start + EMBED_BATCH]
        vectors = asyncio.run(embedder.embed(tuple(node.text for node in batch)))
        store.add_node_embeddings(
            [(node.node_id, vector) for node, vector in zip(batch, vectors, strict=True)]
        )


__all__ = [
    "INDEX_UNUSABLE",
    "NOTHING_TO_PURGE",
    "NO_PUBLISHED_INDEX",
    "NO_WITHDRAWAL",
    "PURGE_FAILED_REMEDY",
    "UNTRUSTED_SOURCE_INDEX",
    "ForestRecomputeStore",
    "PurgeableIndex",
    "WithdrawalPurge",
    "make_forest_recompute",
    "publish_purge_for_withdrawal",
]
