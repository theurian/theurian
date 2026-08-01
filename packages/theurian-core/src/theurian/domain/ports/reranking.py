"""RerankingProvider port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from theurian.domain.identifiers import RevisionId


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """A candidate passage awaiting reranking."""

    chunk_id: str
    revision_id: RevisionId
    text: str
    fusion_score: float


@runtime_checkable
class RerankingProvider(Protocol):
    """Reorders fused candidates against the query.

    The default is the identity reranker: it preserves fusion order exactly. That
    keeps Theurian useful with no model configured, and it makes the effect of a
    real reranker measurable against a fixed baseline (ADR-0009).
    """

    @property
    def model_id(self) -> str: ...

    async def rerank(
        self, query: str, candidates: tuple[ScoredCandidate, ...], *, limit: int
    ) -> tuple[ScoredCandidate, ...]:
        """Return at most ``limit`` candidates, best first.

        Implementations must not invent candidates and must not alter
        ``chunk_id`` or ``revision_id``. Only order and score may change --
        otherwise provenance breaks between fusion and the returned result.
        """
        ...
