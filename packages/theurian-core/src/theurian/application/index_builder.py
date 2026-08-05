"""Turning a canonical state into an index build (FR-R2, FR-R3, ADR-0022).

Split out of :mod:`theurian.application.retrieval_service` for the reason
:mod:`theurian.application.visibility` was split out of it before: that file had
grown past the size at which it can be read in one sitting, and this is a seam
rather than a cut. It described itself as "three use cases over one index file",
and this is the one that *writes*. Everything left there reads.

The seam is real rather than arithmetic. Nothing here consults a
:class:`~theurian.application.visibility.Visibility`, because at build time there
is no caller to be visible *to*: the filter that applies is
:func:`~theurian.domain.enums.may_surface` against the operator's
``include_unapproved``, and it decides what is written rather than what is shown.
The equality property the query side exists to hold has no counterpart here.

Takes its collaborators by injection, so a build is testable without a database
and without an embedding provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from theurian.domain.chunking import IndexableChunk, chunk_document
from theurian.domain.context import RequestContext
from theurian.domain.enums import may_surface
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.canonical_store import CanonicalReadSession
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ports.index_store import IndexStore

#: Chunks per embedding request. An API-backed provider caps request size, and a
#: local one gains nothing from an unbounded batch -- while an unbounded batch
#: holds the whole corpus and all its vectors in memory at once.
EMBED_BATCH: Final = 128


@dataclass(frozen=True, slots=True)
class IndexRequest:
    """What to index, and where to put it."""

    database: Path
    index_path: Path
    project_id: str
    state_hash: str
    index_build_id: str
    #: Whether unapproved revisions are written at all. Off by default, so an
    #: operator who never opts in has a hard guarantee that no draft is in the
    #: file — not merely that a query filter is expected to hold.
    include_unapproved: bool = False


@final
class IndexBuilder:
    """Turns a canonical state into an index build."""

    def __init__(
        self,
        *,
        store_factory: Callable[[Path], CanonicalReadSession],
        index_factory: Callable[[Path], IndexStore],
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._store_factory = store_factory
        self._index_factory = index_factory
        self._embedder = embedder

    def build(self, request: IndexRequest) -> dict[str, object]:
        """Write a new index file from a canonical state.

        Unapproved revisions are written only when asked for, and `rejected`
        never is.

        The obvious simplification — index everything, filter at query time —
        was tried and reverted. It makes `includeUnapproved=True` a single
        boolean that reaches content the team decided must not be followed, and
        it removes the operator's ability to guarantee that a draft is not in
        the file at all. The cost is that `includeUnapproved=True` cannot return
        rows that were never written, which is reported rather than hidden:
        `indexesUnapproved` says whether this build can answer such a query.

        Either a whole index file or none: a build that fails part-way deletes
        what it wrote. That guarantee used to live only in the CLI while
        :meth:`_embed`'s docstring asserted it here, so any other caller of
        `build` — a daemon, a test, a future scheduled rebuild — got a
        half-written file and the promise that it could not happen.

        **What it wrote, and nothing else.** `IndexStore.create` refuses to
        overwrite an existing file and raises; the cleanup then unlinked the very
        file it had just been refused permission to touch — which is the file
        `active-index.json` names, so a build against an already-taken path
        deleted the published index and left the pointer aimed at nothing. Not
        reachable from `theurian index build`, which mints a fresh ULID per
        build, but `build` is a public application-layer API and `create`'s
        contract says an existing file is left alone.
        """
        # Sampled before `create` rather than inferred from the exception: only a
        # path this call brought into existence may be removed by it.
        preexisting = request.index_path.exists()
        try:
            return self._build(request)
        except Exception:
            # A partial index is worse than none. It looks complete, ranks the
            # fraction it holds, and never surfaces the rest -- which reads as a
            # relevance problem rather than a build failure, and so does not get
            # investigated. Nothing this build wrote is published until it
            # returns, so deleting here loses no index a search could have used.
            if not preexisting:
                request.index_path.unlink(missing_ok=True)
            raise

    def _build(self, request: IndexRequest) -> dict[str, object]:
        index = self._index_factory(request.index_path)
        index.create(index_build_id=request.index_build_id, state_hash=request.state_hash)

        context = RequestContext(project_id=ProjectId(request.project_id))
        indexable: list[IndexableChunk] = []

        with self._store_factory(request.database) as store:
            for item in store.list_items(context):
                # The same authority the search paths consult. Inlined here as
                # two comparisons until `may_surface` moved to the domain, which
                # is one copy of a security rule too many.
                if not may_surface(item.status, include_unapproved=request.include_unapproved):
                    continue
                if item.current_revision_id is None:
                    continue
                revision = store.get_revision(context, item.current_revision_id)
                if revision is None:  # pragma: no cover - the pointer is a foreign key
                    continue

                # The title is prepended to the body before splitting so that a
                # query matching only the title still finds the document. A
                # separately indexed title field would need its own retriever and
                # its own fusion weight for the same effect.
                body = f"{revision.title}\n\n{revision.body}"
                for chunk in chunk_document(revision.revision_id.value, body):
                    indexable.append(
                        IndexableChunk(
                            chunk=chunk,
                            project_id=request.project_id,
                            item_id=item.item_id.value,
                            revision_id=revision.revision_id.value,
                            status=item.status.value,
                            sensitivity=revision.metadata.sensitivity.value,
                            trust_level=revision.metadata.trust_level.value,
                        )
                    )

        index.add_chunks(indexable)
        embedded = self._embed(index, indexable)

        return {
            "indexBuildId": request.index_build_id,
            "stateHash": request.state_hash,
            "indexPath": str(request.index_path),
            "chunks": len(indexable),
            "embeddings": embedded,
            "embeddingModel": self._embedder.model_id if self._embedder else "",
            "indexesUnapproved": request.include_unapproved,
        }

    def _embed(self, index: IndexStore, indexable: Sequence[IndexableChunk]) -> int:
        """Embed every chunk, or none.

        Batched, because a real provider caps request size and a local one gains
        nothing from an unbounded batch.

        A partial embedding is worse than none: the dense retriever would rank
        the embedded half and silently never surface the rest, which looks like
        a relevance problem rather than a build problem. :meth:`build` discards
        the whole index file if any batch raises, so a partial one never exists
        to be published.
        """
        if self._embedder is None or not indexable:
            return 0

        embedded = 0
        for start in range(0, len(indexable), EMBED_BATCH):
            batch = indexable[start : start + EMBED_BATCH]
            vectors = asyncio.run(self._embedder.embed(tuple(c.chunk.text for c in batch)))
            index.add_embeddings(
                [(c.chunk.chunk_id, v) for c, v in zip(batch, vectors, strict=True)]
            )
            embedded += len(vectors)

        index.record_embedding_model(
            model_id=self._embedder.model_id, dimension=self._embedder.dimension
        )
        return embedded


__all__ = ["EMBED_BATCH", "IndexBuilder", "IndexRequest"]
