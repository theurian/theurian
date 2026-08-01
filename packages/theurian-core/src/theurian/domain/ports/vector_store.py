"""VectorStore port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from theurian.domain.identifiers import IndexBuildId, RevisionId
from theurian.domain.values import Scope


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """One nearest-neighbour hit."""

    chunk_id: str
    revision_id: RevisionId
    score: float


@runtime_checkable
class VectorStore(Protocol):
    """Stores embeddings and answers nearest-neighbour queries.

    The default adapter is ``sqlite-vec``; a brute-force pure-SQLite adapter
    exists as the tested fallback, because ``sqlite-vec`` is pre-1.0 (ADR-0014).

    ``search`` takes a :class:`Scope` rather than a post-filter. Filtering after
    ranking silently shrinks the result set below ``limit`` and can return zero
    results for a query that had plenty of authorized matches (FR-R1).
    """

    async def upsert(
        self,
        index_build_id: IndexBuildId,
        entries: tuple[tuple[str, RevisionId, Scope, tuple[float, ...]], ...],
    ) -> None:
        """Insert or replace vectors for one index build.

        Writes target a build that is not yet published; the active index is
        untouched until the atomic swap (ADR-0007, NFR-4).
        """
        ...

    async def search(
        self,
        index_build_id: IndexBuildId,
        query_vector: tuple[float, ...],
        *,
        scopes: tuple[Scope, ...],
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        """Nearest neighbours restricted to ``scopes``, best first."""
        ...

    async def drop_build(self, index_build_id: IndexBuildId) -> None:
        """Delete every vector belonging to an abandoned or superseded build."""
        ...
