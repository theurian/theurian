"""Embedding adapters.

The default is deterministic, local, and honest about being a character n-gram
vectoriser rather than a semantic model (ADR-0009).
"""

from theurian.infrastructure.embedding.hashing import HashingEmbedding

__all__ = ["HashingEmbedding"]
