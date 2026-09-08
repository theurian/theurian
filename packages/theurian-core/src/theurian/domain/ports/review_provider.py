"""ReviewProvider port: the GitHub adapter first, GitLab later (FR-V1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from theurian.domain.identifiers import ProjectId
from theurian.domain.review import ReviewEvent, ReviewSubmission, ReviewThread
from theurian.domain.review_ingest import RefusalEnvelope


@dataclass(frozen=True, slots=True)
class SkippedPullRequest:
    """One pull request a listing could not build a record from, and why.

    Carries the envelope rather than an exception, because this is a value on a
    *return* path: an implementation that raised here would take the rest of the
    listing with it, which is the whole reason this channel exists.

    ``repository`` is the implementation's own resolved spelling -- the same one
    a :class:`~theurian.domain.review.ReviewEvent` would have carried -- so a
    caller names the skipped record exactly as it names a landed one.
    """

    repository: str
    number: int
    envelope: RefusalEnvelope


@dataclass(frozen=True, slots=True)
class PullRequestListing:
    """What one window of a repository's pull requests answered with.

    Two channels, and the split is the port's own contract rather than an
    implementation's convenience: :attr:`events` is what could be built and
    :attr:`skipped` is what could not. A caller is expected to report the second
    -- a listing that answered fewer events than the repository holds, with
    nothing saying which are missing, is a partial read mistaken for a whole one.

    Returning them together rather than adding a second method is deliberate: a
    sibling method is one a caller can forget to call, while a caller of this one
    has the skipped set in hand whether or not it reads it.
    """

    events: tuple[ReviewEvent, ...] = ()
    skipped: tuple[SkippedPullRequest, ...] = ()


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
    ) -> PullRequestListing:
        """Pull requests, newest first, and the ones this window could not build.

        ``since_number`` supports incremental ingestion so a re-run does not
        refetch the entire history. **The window is applied to a pull request's
        number before its record is built**, so a pull request the caller
        excluded cannot fail a read the caller never asked for.

        **Which failures raise and which land in** :attr:`PullRequestListing.skipped`
        **is decided by whose data was faulty, never by the grade.** A fault in
        **one node's** data -- an unreadable field of that pull request, a
        connection of its own past a cap -- is that pull request's, and it is
        reported as a :class:`SkippedPullRequest` while the rest of the window is
        answered. A fault in the **listing's own machinery** -- the allowlist,
        the repository's visibility or resolved name, the transport, the tool,
        this listing's own pagination -- is the repository's, and it is raised:
        the set of pull requests being iterated is the thing that could not be
        established, so there is no window left to answer. The two can carry the
        same grade, which is why a reader of the grade cannot tell them apart.

        **Both channels are :class:`~theurian.domain.review_ingest.RefusalEnvelope`-shaped,
        and that is an obligation on the implementation rather than a
        description of one** (round two, R2-A). A caller catches the graded
        family and reads the skipped channel; anything else an implementation
        lets out reaches that caller as neither -- past the skip channel, past
        the ``except``, and out of whatever composition root is above it as a
        traceback. So a value a *record build* reads and a domain type then
        refuses must be refused **here**, by this method, with a grade: the
        shipped adapter reads every such field through a helper that answers with
        a value or a graded refusal, and
        ``GitHubReviewProvider._event`` records the key that decides which fields
        those are. An implementation that let a constructor's own
        ``InvariantViolationError`` out instead would satisfy the type signature
        and break this contract, which is exactly what the GitHub adapter did
        with a nulled ``url`` until round two measured it.

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

    async def get_reviews(
        self, project_id: ProjectId, event: ReviewEvent
    ) -> tuple[ReviewSubmission, ...]:
        """Top-level reviews for one pull request: the verdicts, not the line comments.

        Its own method rather than a second return value from :meth:`get_threads`,
        because FR-V1 names reviews and threads separately and providers carry
        them as separate connections that paginate independently. A caller that
        wants only threads then pays for only threads.

        Implementations fetch these in a **per-pull-request** request. A
        connection nested inside the pull-request listing is asked for once per
        pull request in the page and carries no cursor an implementation could
        follow, so what overflows there is lost with nothing to report it -- and
        the same allowlist and timeout obligations :meth:`list_pull_requests`
        records apply here unchanged.
        """
        ...
