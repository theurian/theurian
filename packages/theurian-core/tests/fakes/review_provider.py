"""A canned :class:`ReviewProvider` (ADR-0003, OSS-15, ADR-0030).

Answers from preset domain values so an ingestion run can be driven offline: no
``gh`` is spawned, no process exists, and nothing reaches the network. The real
adapter's own behaviour -- the allowlist, the version floor, the caps, the
response shape checks -- is tested against a stand-in child in
``tests/integration/test_gh_review_provider.py``; this fake exists for the
consumers that *inject* the port.

**Three refusal channels, because the port has three outcomes and a consumer
must be drivable against each.** ``listing_refusal`` is raised from
``list_pull_requests`` and is a repository-scope stop; ``listing_faults`` is
answered *inside* the listing and is a record-scope skip; ``refusals`` is raised
from ``get_threads``/``get_reviews`` and is a record-scope skip at the other
seam. All three can carry the same grade, which is what lets a caller drive the
distinction the landing service makes without letting it read ``exc.grade``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import final

from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.review_provider import PullRequestListing, SkippedPullRequest
from theurian.domain.review import ReviewEvent, ReviewSubmission, ReviewThread
from theurian.domain.review_ingest import ReviewIngestRefusedError

#: Which of the two per-pull-request reads a canned refusal belongs to, paired
#: with the pull request's number. Spelled as the port's own method names so a
#: reader of a test does not have to translate.
ReadKey = tuple[str, int]


@final
class CannedReviewProvider:
    """Replays preset pull requests, threads and reviews, and records every read.

    Args:
        events: What ``list_pull_requests`` answers with, newest first, before
            the caller's window is applied.
        threads: Per pull-request number. A number with no entry answers with no
            threads, which is an ordinary answer rather than an error.
        submissions: Per pull-request number, the same way.
        refusals: Keyed by ``("get_threads" | "get_reviews", number)``. Raised
            from that read, for that pull request only.
        listing_refusal: Raised from ``list_pull_requests`` instead of answering.
        listing_faults: Per pull-request number, the refusal the real adapter
            would meet while **building** that pull request's record. Answered as
            a skip rather than raised.
    """

    def __init__(  # noqa: PLR0913 -- one keyword per canned answer or refusal channel
        self,
        events: Sequence[ReviewEvent] = (),
        *,
        threads: Mapping[int, tuple[ReviewThread, ...]] | None = None,
        submissions: Mapping[int, tuple[ReviewSubmission, ...]] | None = None,
        refusals: Mapping[ReadKey, ReviewIngestRefusedError] | None = None,
        listing_refusal: ReviewIngestRefusedError | None = None,
        listing_faults: Mapping[int, ReviewIngestRefusedError] | None = None,
    ) -> None:
        self._events = tuple(events)
        self._threads = dict(threads or {})
        self._submissions = dict(submissions or {})
        self._refusals = dict(refusals or {})
        self._listing_refusal = listing_refusal
        self._listing_faults = dict(listing_faults or {})
        #: Every per-pull-request read this fake was asked for, in order. What a
        #: caller asserts on when the claim is about a call that was *not* made.
        self.reads: list[ReadKey] = []
        #: How many times the listing was asked for. Counted separately because
        #: it is not a per-pull-request read: a caller pricing a run needs the
        #: two terms of ``1 + 2N`` apart, and a listing called once per page or
        #: once per record would be invisible in ``reads``.
        self.listings = 0

    @property
    def provider_id(self) -> str:
        return "github"

    async def list_pull_requests(
        self,
        project_id: ProjectId,
        repository: str,
        *,
        since_number: int | None = None,
        limit: int = 100,
    ) -> PullRequestListing:
        """The preset events, narrowed by the caller's window.

        ``since_number`` stops at the first pull request at or below it, which is
        the adapter's own boundary: numbers are assigned in creation order and
        the query is ordered by creation.

        **The order of the three steps below mirrors the adapter's, and it is
        load-bearing rather than cosmetic.** The window is applied to a number
        first, the record's fault is consulted second, and the limit counts
        skipped pull requests as well as built ones. A fake that filtered
        ``since_number`` over records it had already "built" cannot express a
        poison record *outside* the window -- which is exactly the case the
        adapter got wrong, and exactly why no service-level test could see it:
        every service test drove a fake whose ordering made the bug
        unrepresentable.
        """
        # Counted before the refusal, for the reason `_refuse` logs before it
        # raises: "asked and refused" and "never asked" are different facts.
        self.listings += 1
        if self._listing_refusal is not None:
            raise self._listing_refusal
        chosen: list[ReviewEvent] = []
        skipped: list[SkippedPullRequest] = []
        for event in self._events:
            if since_number is not None and event.number <= since_number:
                break
            fault = self._listing_faults.get(event.number)
            if fault is None:
                chosen.append(event)
            else:
                skipped.append(SkippedPullRequest(event.repository, event.number, fault.envelope))
            if len(chosen) + len(skipped) >= limit:
                break
        return PullRequestListing(tuple(chosen), tuple(skipped))

    async def get_threads(
        self, project_id: ProjectId, event: ReviewEvent
    ) -> tuple[ReviewThread, ...]:
        self.reads.append(("get_threads", event.number))
        self._refuse("get_threads", event.number)
        return self._threads.get(event.number, ())

    async def get_reviews(
        self, project_id: ProjectId, event: ReviewEvent
    ) -> tuple[ReviewSubmission, ...]:
        self.reads.append(("get_reviews", event.number))
        self._refuse("get_reviews", event.number)
        return self._submissions.get(event.number, ())

    def _refuse(self, read: str, number: int) -> None:
        """Raise this read's canned refusal, if it has one.

        Raised **after** the read is logged, so a caller can tell "asked and
        refused" from "never asked" -- which is the distinction a scope-matched
        containment test rests on.
        """
        refusal = self._refusals.get((read, number))
        if refusal is not None:
            raise refusal
