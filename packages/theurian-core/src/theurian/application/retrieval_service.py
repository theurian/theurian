"""Building the index, and answering queries against it (FR-R1..R7).

Two use cases over one index file:

- :meth:`IndexBuilder.build` reads the canonical store, splits each approved
  revision into chunks, and writes a new index file;
- :meth:`RetrievalService.search` runs both retrievers, fuses them, diversifies,
  and packs to the caller's budget.

Both take their collaborators by injection. The ranking they depend on lives in
:mod:`theurian.domain.ranking` and never touches a database, so the interesting
behaviour is testable without one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, final

from theurian.domain.chunking import IndexableChunk, chunk_document
from theurian.domain.context import RequestContext
from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ports.index_store import IndexStore
from theurian.domain.ranking import (
    DENSE,
    LEXICAL,
    Fused,
    Ranked,
    RetrievalMode,
    diversify,
    mode_of,
    pack,
    reciprocal_rank_fusion,
)

#: How many candidates each retriever contributes before fusion. Generous: RRF
#: rewards a document both retrievers found, and a document the dense retriever
#: ranked 30th cannot demonstrate agreement if only 10 were asked for.
CANDIDATE_DEPTH: Final = 50

#: Default context allowance when a caller states none. Roughly a page of prose —
#: enough to answer, small enough that a caller who forgot the parameter is not
#: handed their whole window back.
DEFAULT_BUDGET_TOKENS: Final = 2000

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


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """One query, and the shape of the answer the caller can afford."""

    query: str
    project_id: str
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    limit: int = 10
    include_unapproved: bool = False
    #: Chunks any one item may contribute. Two lets a long document make its
    #: case twice without crowding out every other opinion.
    per_item: int = 2


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """A fused, diversified, packed result set, and how it was produced."""

    candidates: tuple[Fused, ...]
    mode: RetrievalMode
    used_tokens: int
    dropped_for_budget: int
    #: Named so a caller can tell an n-gram-backed hybrid search from one backed
    #: by a real semantic model. Empty when no dense retriever ran.
    embedding_model: str = ""


@final
class IndexBuilder:
    """Turns a canonical state into an index build."""

    def __init__(
        self,
        *,
        store_factory: Callable[[Path], Any],
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
        """
        index = self._index_factory(request.index_path)
        index.create(index_build_id=request.index_build_id, state_hash=request.state_hash)

        context = RequestContext(project_id=ProjectId(request.project_id))
        indexable: list[IndexableChunk] = []

        with self._store_factory(request.database) as store:
            for item in store.list_items(context):
                if item.status not in SURFACEABLE_STATUSES:
                    continue
                if not request.include_unapproved and item.status is not KnowledgeStatus.APPROVED:
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
        a relevance problem rather than a build problem. The build discards the
        whole index file if any batch fails, so a partial one never publishes.
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


@final
class RetrievalService:
    """Answers a query against one index build."""

    def __init__(self, index: IndexStore, embedder: EmbeddingProvider | None = None) -> None:
        self._index = index
        self._embedder = embedder

    def search(self, request: SearchRequest) -> SearchOutcome:
        """Run both retrievers, fuse, diversify, and pack (FR-R2, FR-R4)."""
        lexical = self._index.search_lexical(
            request.query,
            project_id=request.project_id,
            limit=CANDIDATE_DEPTH,
            include_unapproved=request.include_unapproved,
        )
        dense = self._dense(request)

        rankings: dict[str, Sequence[Ranked]] = {LEXICAL: lexical, DENSE: dense}
        fused = reciprocal_rank_fusion(rankings)
        diversified = diversify(fused, per_item=request.per_item)[: request.limit]

        sizes = self._index.token_sizes([candidate.chunk_id for candidate in diversified])
        packed = pack(diversified, sizes, budget_tokens=request.budget_tokens)

        return SearchOutcome(
            candidates=packed.candidates,
            mode=mode_of(rankings),
            used_tokens=packed.used_tokens,
            dropped_for_budget=packed.dropped,
            embedding_model=self._embedding_model_if_used(dense),
        )

    def _dense(self, request: SearchRequest) -> tuple[Ranked, ...]:
        """Rank by vector similarity, or return nothing.

        Nothing is a supported answer. A missing embedder, an index built before
        embeddings existed, or a corpus embedded by a different model all reduce
        the search to lexical — and `mode_of` says so, rather than letting a
        degraded search look like a healthy one.
        """
        if self._embedder is None:
            return ()

        stored = str(self._index.metadata().get("embedding_model", ""))
        if stored and stored != self._embedder.model_id:
            # Comparable arithmetically, meaningless semantically. Refused rather
            # than scored, because the output would be confident and wrong.
            return ()

        vector = asyncio.run(self._embedder.embed((request.query,)))[0]
        result: tuple[Ranked, ...] = self._index.search_dense(
            vector,
            project_id=request.project_id,
            limit=CANDIDATE_DEPTH,
            include_unapproved=request.include_unapproved,
        )
        return result

    def _embedding_model_if_used(self, dense: Sequence[Ranked]) -> str:
        return self._embedder.model_id if (dense and self._embedder) else ""
