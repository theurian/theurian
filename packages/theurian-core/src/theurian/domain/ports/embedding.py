"""EmbeddingProvider port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into dense vectors.

    The default implementation is a deterministic hashing embedder that needs no
    network and no API key, so Theurian works offline out of the box (ADR-0009).
    Semantic quality is correspondingly limited; lexical FTS carries retrieval
    until a real provider is configured.
    """

    @property
    def model_id(self) -> str:
        """Stable identifier persisted with every embedding.

        Changing it invalidates derived vectors deterministically instead of
        producing an index that silently mixes two embedding spaces.
        """
        ...

    @property
    def model_revision(self) -> str:
        """Version of the model or of this implementation."""
        ...

    @property
    def dimension(self) -> int:
        """Vector dimension. Constant for a given ``model_id``/``model_revision``."""
        ...

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed a batch, returning one vector per input in the same order.

        Batched because a real provider charges per request. Async because a real
        provider is network-bound; the deterministic default simply returns
        immediately.

        Implementations must apply a timeout to any network call (SEC-19).
        """
        ...
