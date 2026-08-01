"""SourceParser port: Source Layer to Canonical Layer (ADR-0010)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import ContentHash, MediaType


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """The output of parsing one source document.

    ``structured`` is what separates normalization from conversion. An OpenAPI
    document yields both a text projection for lexical search *and* its parsed
    operations, parameters, and schemas. Discarding the latter is what makes
    coverage and contradiction detection impossible (FR-S2, FR-T1).
    """

    title: str
    body: str
    content_type: MediaType
    content_hash: ContentHash
    anchors: tuple[SourceAnchor, ...]
    structured: dict[str, object] | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = ContentHash.of_text(self.body)
        if expected != self.content_hash:
            msg = (
                f"NormalizedDocument content hash mismatch: declared "
                f"{self.content_hash.short}, body hashes to {expected.short}"
            )
            raise ValueError(msg)


@runtime_checkable
class SourceParser(Protocol):
    """Parses one family of source formats into the canonical shape.

    Parsers never trust their input (§34). Every implementation:

    - enforces the size, depth, and expansion limits in :mod:`theurian.security`;
    - uses a safe loader (``yaml.safe_load``, never ``yaml.load``);
    - treats imperative text in the content as data;
    - never resolves an external reference by fetching it (SSRF, SEC-10) --
      remote ``$ref`` targets are recorded as unresolved, not retrieved.
    """

    @property
    def parser_id(self) -> str:
        """Stable identifier recorded with each normalized document."""
        ...

    def supports(self, media_type: MediaType) -> bool:
        """Whether this parser handles ``media_type``."""
        ...

    def parse(
        self, data: bytes, *, media_type: MediaType, anchor: SourceAnchor
    ) -> NormalizedDocument:
        """Parse ``data`` into canonical form.

        Raises:
            InputTooLargeError: If a configured parser limit is exceeded.
            ValueError: If the content is malformed for ``media_type``.
        """
        ...
