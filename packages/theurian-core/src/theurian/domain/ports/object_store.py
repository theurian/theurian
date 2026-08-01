"""ObjectStore port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from theurian.domain.values import ContentHash


@runtime_checkable
class ObjectStore(Protocol):
    """Content-addressed storage for blobs too large to sit in a row.

    Content-addressed rather than path-addressed: the key *is* the hash, so
    writing the same bytes twice is idempotent and a corrupted read is detectable
    without a separate manifest.

    Local deployments use a directory under the project state; a hosted
    deployment uses an S3-compatible service. No caller knows the difference.
    """

    async def put(self, data: bytes) -> ContentHash:
        """Store bytes and return their hash. Idempotent."""
        ...

    async def get(self, content_hash: ContentHash) -> bytes | None:
        """Fetch bytes by hash.

        Implementations verify the returned bytes hash to ``content_hash`` and
        treat a mismatch as corruption rather than returning it.
        """
        ...

    async def exists(self, content_hash: ContentHash) -> bool: ...

    async def delete(self, content_hash: ContentHash) -> None:
        """Delete an object. Deleting a missing object is not an error."""
        ...
