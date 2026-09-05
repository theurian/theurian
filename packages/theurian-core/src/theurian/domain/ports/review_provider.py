"""ReviewProvider port: the GitHub adapter first, GitLab later (FR-V1)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from theurian.domain.identifiers import ProjectId
from theurian.domain.review import ReviewEvent, ReviewThread


@runtime_checkable
class ReviewProvider(Protocol):
    """Fetches pull requests and their review conversations.

    Ingestion of raw reviews must succeed even when downstream candidate
    generation fails (FR-V5). This port therefore returns evidence only; it never
    classifies, generalises, or calls a model.
    """

    @property
    def provider_id(self) -> str:
        """Provider name recorded on every anchor, e.g. ``github``."""
        ...

    async def list_pull_requests(
        self,
        project_id: ProjectId,
        repository: str,
        *,
        since_number: int | None = None,
        limit: int = 100,
    ) -> tuple[ReviewEvent, ...]:
        """Pull requests, newest first.

        ``since_number`` supports incremental ingestion so a re-run does not
        refetch the entire history.

        Implementations must apply a request timeout (SEC-19), respect rate
        limits, and validate ``repository`` against an allowlist **before any
        request** (SEC-10) -- which is not the same as "before building a URL",
        the wording this said until ADR-0030: the shipped adapter builds no URL
        at all, and an allowlist consulted after the request has already been
        made is not a control.
        """
        ...

    async def get_threads(
        self, project_id: ProjectId, event: ReviewEvent
    ) -> tuple[ReviewThread, ...]:
        """Review threads for one pull request, with comments and resolution state."""
        ...
