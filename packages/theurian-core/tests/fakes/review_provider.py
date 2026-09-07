"""A canned :class:`ReviewProvider` (ADR-0003, OSS-15, ADR-0030).

Answers from preset domain values so an ingestion run can be driven offline: no
``gh`` is spawned, no process exists, and nothing reaches the network. The real
adapter's own behaviour -- the allowlist, the version floor, the caps, the
response shape checks -- is tested against a stand-in child in
``tests/integration/test_gh_review_provider.py``; this fake exists for the
consumers that *inject* the port.

**Refusals are keyed by ``(read, pull request number)``**, so one map expresses
both per-record reads. That is what lets a caller drive the distinction the
landing service makes: a refusal from ``list_pull_requests`` is a repository-scope
failure and one from ``get_threads``/``get_reviews`` is a record-scope failure,
and they can carry the same grade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import final

from theurian.domain.identifiers import ProjectId
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
    """

    def __init__(
        self,
        events: Sequence[ReviewEvent] = (),
        threads: Mapping[int, tuple[ReviewThread, ...]] | None = None,
        submissions: Mapping[int, tuple[ReviewSubmission, ...]] | None = None,
        refusals: Mapping[ReadKey, ReviewIngestRefusedError] | None = None,
        listing_refusal: ReviewIngestRefusedError | None = None,
    ) -> None:
        self._events = tuple(events)
        self._threads = dict(threads or {})
        self._submissions = dict(submissions or {})
        self._refusals = dict(refusals or {})
        self._listing_refusal = listing_refusal
        #: Every per-pull-request read this fake was asked for, in order. What a
        #: caller asserts on when the claim is about a call that was *not* made.
        self.reads: list[ReadKey] = []

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
    ) -> tuple[ReviewEvent, ...]:
        """The preset events, narrowed by the caller's window.

        ``since_number`` stops at the first pull request at or below it, which is
        the adapter's own boundary: numbers are assigned in creation order and
        the query is ordered by creation.
        """
        if self._listing_refusal is not None:
            raise self._listing_refusal
        chosen: list[ReviewEvent] = []
        for event in self._events:
            if since_number is not None and event.number <= since_number:
                break
            chosen.append(event)
            if len(chosen) >= limit:
                break
        return tuple(chosen)

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
