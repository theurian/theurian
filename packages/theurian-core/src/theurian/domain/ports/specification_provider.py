"""SpecificationProvider port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from theurian.domain.context import RequestContext
from theurian.domain.specification import Specification


@runtime_checkable
class SpecificationProvider(Protocol):
    """Discovers specifications and parses them into their structured form.

    Separate from :class:`~theurian.domain.ports.source_parser.SourceParser`
    because discovery is a distinct concern: specifications may live in the
    repository, in an OpenAPI bundle produced by a build, or in an external
    registry. Parsing is delegated to a ``SourceParser``.
    """

    @property
    def provider_id(self) -> str: ...

    async def discover(self, context: RequestContext) -> tuple[Specification, ...]:
        """Specifications currently visible for this project and snapshot.

        Every returned specification carries at least one anchor, so a reader can
        always reach the file or endpoint it was read from (FR-S3).
        """
        ...
