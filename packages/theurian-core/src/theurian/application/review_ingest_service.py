"""One review-ingestion run: list, fetch, screen, land, report (ADR-0030 decisions 3, 4).

The service that turns a repository name into evidence files. It lists a
repository's pull requests, fetches each one's threads and top-level reviews,
hands every record to :func:`~theurian.application.review_landing_gate.screen_landing_candidates`,
and writes what the gate cleared -- through injected callables, because this
layer names no adapter (ADR-0003).

**Scope-matched containment: which failures halt the run, and which skip one
record.** The question the split answers is *can I still trust the set of
records I am iterating?* A failure at the repository scope poisons the set; a
failure at one pull request is one known-bad member of a set that is still
trustworthy.

* **Repository scope halts.** Everything :meth:`ReviewProvider.list_pull_requests`
  *raises* -- the allowlist, a repository that resolves private, a rename
  redirect, a transport override, a ``gh`` that is missing, too old or
  unauthenticated, an answer whose envelope cannot be read, and the pull-request
  listing's own page cap -- propagates as the graded envelope it was raised as.
  Nothing is fetched afterwards and nothing is written, because the set of pull
  requests this run would iterate is the thing that could not be established.
* **Record scope skips, at both seams.** Two things reach this run about one
  pull request rather than about the repository, and they are folded into one
  report channel:

  1. a :class:`~theurian.domain.ports.review_provider.SkippedPullRequest` in the
     listing the provider *returned* -- one pull request whose own data the
     provider could not build a record from;
  2. a refusal *raised* by :meth:`ReviewProvider.get_threads` or
     :meth:`ReviewProvider.get_reviews` for **one** pull request, caught at that
     call site.

  Either way that pull request's records -- the event, its reviews and its
  threads -- are withheld **whole**, the refusal is reported by identity with
  its grade and summary in :attr:`ReviewIngestReport.skipped`, and the run
  continues to the next pull request.

**The discrimination is by where the fault was, never by grade.** Both scopes
carry ``LIMIT_EXCEEDED``: the pull-request listing's page cap is a
repository-scope stop while one pull request's label cap and a thread's comment
cap are record-scope ones, and all three share a grade because an operator does
the same thing about any of them. A run that told them apart by reading
``exc.grade`` would halt on an over-long thread and skip a repository it may not
contact. The key is
``test_one_grade_halts_at_the_listing_and_skips_at_both_seams`` in
``tests/unit/test_review_ingest_service.py``: it drives that one grade through
all three and asserts three different outcomes.

One consequence is worth naming rather than discovering. ``get_threads`` and
``get_reviews`` re-check the allowlist and the transport override themselves, so
a repository-scope condition that first becomes true *during* a run -- the
operator edits ``.theurian/config.yaml``, or a ``gh`` setting, mid-run --
arrives at those call sites and degrades **every remaining pull request** to a
skip. That is loud rather than silent: each skip is reported by identity with
its own envelope and the run does not read as clean.

**What one run costs is bounded by the port's implementation, and the numbers
live with it.** This service makes one
:meth:`~theurian.domain.ports.review_provider.ReviewProvider.list_pull_requests`
call and then two per pull request the listing built -- ``get_threads`` and
``get_reviews`` -- which is the whole of its own contribution to the cost. A
pull request the listing skipped is never fetched, so the per-record term counts
built records rather than window slots.
``test_a_run_makes_one_listing_call_and_two_per_pull_request`` is that shape,
driven against the real service. What one of those calls may then spend --
children spawned, wall clock, bytes read -- is the adapter's, and the shipped
one records both grains as derived constants in
``infrastructure/github/limits.py``: ``MAX_PORT_CALLS_PER_RUN``,
``MAX_SPAWNS_PER_RUN``, ``MAX_SECONDS_PER_RUN`` and ``MAX_READ_BYTES_PER_RUN``.
Named rather than imported, because this layer depends on the port and not on
any adapter (ADR-0003); a second provider brings its own file of them.

**This slice ships no advance marker, and that is a decision.** There is no
"last ingested" file, no watermark, nothing that records a pull request as seen.
The window is the caller's: an explicit ``since_number``, and otherwise the
newest ``limit`` pull requests. A skipped record is therefore retried by the
next run whose window covers its number -- from either seam above, with no state
to reconcile -- which is the property :meth:`run` exists to keep. A marker
invented now would have to
decide whether a *skipped* record counts as seen, and a marker that answered yes
would step over the record permanently: the skip would become a silent data
loss, which is the one failure this arm cannot have. When slice 3 builds the
derived store, the marker is derivable from the evidence files themselves --
they carry the run that last saw each record -- so nothing is being deferred
that will have to be invented.

**FR-V5 holds structurally here, not by a fallback.** This module imports no
model, no embedder, no summarizer and no reranker, and neither does anything it
constructs; raw ingestion cannot be broken by candidate generation failing
because candidate generation is not in this path at all.
``tests/integration/test_review_ingest_is_model_free.py`` walks the built
pipeline's object graph and says so.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import final

from theurian.application.review_landing_gate import (
    LandingCandidate,
    ReviewRecordIdentity,
    ReviewRecordPayload,
    ReviewScanOutcome,
    ReviewSecretFinding,
    screen_landing_candidates,
)
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.ports.review_provider import ReviewProvider
from theurian.domain.review import ReviewEvent, ReviewSubmission, ReviewThread
from theurian.domain.review_ingest import (
    RefusalEnvelope,
    RefusalGrade,
    ReviewIngestRefusedError,
)


@dataclass(frozen=True, slots=True)
class LandedRecord:
    """One screened record, as this layer hands it to whatever makes it durable.

    **Deliberately not the evidence store's own record type, and the reason is
    ADR-0003 rather than taste.** That type's identity includes the filesystem
    layout it lands under -- it derives its own path -- so an application service
    importing it would depend on a store's on-disk shape to describe a record it
    screened. Mapping one onto the other is what a composition root is for, and
    the CLI does exactly that in four field copies.

    ``payload`` is what the gate cleared, never what arrived: see
    :attr:`~theurian.application.review_landing_gate.RecordVerdict.landing`.
    """

    #: The provider that answered, taken from the port rather than spelled here.
    provider: str
    #: The repository as the provider resolved it. Carried per record rather than
    #: per run because the adapter records the **allowlisted** spelling, which the
    #: caller's argument need only match case-folded.
    repository: str
    #: FR-S3's pointer back to the upstream object, written by Theurian. Scanned
    #: by nothing -- it is the third of ADR-0030 decision 3's *Controlled by*
    #: values, and the only one of the three this service produces.
    anchor: SourceAnchor
    payload: ReviewRecordPayload


#: How a run makes its screened records durable.
#:
#: Answers one **opaque key per record**, in the order the records were given.
#: Opaque is the contract: this layer compares keys for equality and never reads
#: one, so what the adapter chooses -- a relative path today -- stays the
#: adapter's. The equality is what :attr:`ReviewIngestReport.new`,
#: :attr:`~ReviewIngestReport.updated` and :attr:`~ReviewIngestReport.kept` are
#: computed from.
LandRecords = Callable[[Sequence[LandedRecord]], tuple[str, ...]]

#: What this repository already had on disk before the run, in the same opaque
#: keys :data:`LandRecords` answers with. Called **before** anything is fetched,
#: so a record that vanished upstream is still visible as one this run did not
#: land.
ReadLandedKeys = Callable[[], frozenset[str]]


@dataclass(frozen=True, slots=True)
class ReviewIngestRequest:
    """One run's window, in the terms the provider port takes them.

    ``since_number`` and ``limit`` are the whole of the window, and nothing here
    remembers either: see this module's docstring on why no marker ships.
    """

    project_id: ProjectId
    repository: str
    limit: int
    since_number: int | None = None


@dataclass(frozen=True, slots=True)
class FetchRefusal:
    """One pull request whose records this run withheld whole.

    Carries the envelope's own three fields rather than the exception: a report is
    a published document, and the summary is already bounded by
    :class:`~theurian.domain.review_ingest.RefusalEnvelope`'s construction.

    **One shape for both record-scope seams**, the listing's returned skip and a
    per-pull-request fetch's raised refusal. Two shapes would be two vocabularies
    for one fact -- *this pull request's records are not here, and here is why* --
    and a composition root would have to publish both.
    """

    identity: ReviewRecordIdentity
    grade: RefusalGrade
    summary: str
    remedy: str

    @classmethod
    def of(cls, repository: str, number: int, envelope: RefusalEnvelope) -> FetchRefusal:
        """The report's shape for one refused pull request, from its envelope."""
        return cls(
            identity=ReviewRecordIdentity(repository, number),
            grade=envelope.grade,
            summary=envelope.summary,
            remedy=envelope.remedy,
        )

    def describe(self) -> str:
        """One line naming the pull request, the grade and what was refused."""
        return f"{self.identity.describe()}: {self.grade.value} -- {self.summary}"


@dataclass(frozen=True, slots=True)
class ReviewIngestReport:
    """What one run did, in counts and identities and no evidence content.

    **The clean rule, stated once here because a composition root turns it into
    an exit code.** A run is clean when it refused no record and skipped no pull
    request. ``warn`` findings alone leave it clean: under ``warn`` the project
    has recorded that a finding is reported and the record lands anyway, so a
    finding is the policy working rather than the run failing. Under ``block`` a
    finding withholds the record, which puts it in :attr:`refused` and is what
    makes the run non-clean -- the finding is not what is counted, the withheld
    record is.

    This is **not** :attr:`~theurian.application.review_landing_gate.ReviewScanOutcome.clean`,
    which answers "found nothing and withheld nothing" and therefore reads
    ``False`` on a warned run that landed everything it fetched. Two questions,
    two answers, and the one an exit code wants is this one.

    **The warned run needs a third answer, and :attr:`secrets_warned` is it.**
    Under ``warn`` a run that found a secret is clean, refuses nothing and exits
    zero -- correct, and silent about the file it just wrote. Neither of the two
    booleans above can carry that, so it is published rather than left to a
    caller who would have to join ``policy`` against a finding count to see it.
    """

    #: The repository this run was asked for, as the caller spelled it.
    repository: str
    #: The ``security.secretScan`` value in force, as the schema publishes it.
    #: A string and not the enum: importing the policy type would enrol this
    #: module in the SEC-11 importer census, which exists to answer *who reached
    #: for the control*, and a report that re-publishes an answer reached for
    #: nothing.
    policy: str
    #: Whether participant display names were replaced before anything was
    #: written (R-12).
    redacted: bool
    #: Whether a secret-shaped string was found **and its record landed anyway**
    #: -- true exactly when the policy is ``warn`` and this run found something.
    #:
    #: The **third** signal, beside :attr:`clean` and :attr:`refused`, and it is
    #: here because those two are both honest and both silent about this state: a
    #: warned run is clean, refuses nothing, exits zero, and has just written a
    #: file carrying a credential into a directory ``theurian init`` does not
    #: git-ignore. Nothing about that is a change of behaviour -- ``warn`` is the
    #: project recording that choice -- but a caller had to know the joining rule
    #: to see it, and a rule each reader re-derives is one some reader will not.
    secrets_warned: bool
    pull_requests: int
    review_submissions: int
    review_threads: int
    #: Records landed under a key this repository did not have before.
    new: int
    #: Records landed over a key it already had -- decision 3's best-effort
    #: refresh.
    updated: int
    #: Keys this repository had and this run did not land. **Kept, never
    #: deleted** (decision 3), and deliberately not called an upstream-deletion
    #: count: a record falls in here when upstream no longer returns it, when it
    #: sits outside this run's window, and when the gate refused it. A narrow
    #: ``--limit`` therefore makes this number large, and that is the honest
    #: reading of it.
    kept: int
    #: Every record the gate withheld, by identity. Empty unless the policy is
    #: ``block``.
    refused: tuple[ReviewRecordIdentity, ...]
    #: Every finding, under either policy, described without the matched bytes.
    findings: tuple[ReviewSecretFinding, ...]
    #: Every pull request this run withheld whole, by identity and envelope --
    #: the ones the listing could not build and the ones whose fetch refused, in
    #: that order. One channel for both, because an operator reads it to answer
    #: "which pull requests am I missing", and which seam lost a record is a
    #: property of the refusal rather than a second question.
    skipped: tuple[FetchRefusal, ...]

    @property
    def clean(self) -> bool:
        """Whether this run refused no record and skipped no pull request."""
        return not self.refused and not self.skipped

    @property
    def landed(self) -> int:
        """How many records became files this run."""
        return self.pull_requests + self.review_submissions + self.review_threads


@dataclass(frozen=True, slots=True)
class _Fetched:
    """One pull request whose two per-record reads both answered."""

    event: ReviewEvent
    submissions: tuple[ReviewSubmission, ...]
    threads: tuple[ReviewThread, ...]

    def payloads(self) -> tuple[ReviewRecordPayload, ...]:
        """The event and everything hanging off it, in landing order."""
        return (self.event, *self.submissions, *self.threads)


@final
class ReviewIngestService:
    """Runs one ingestion, from a repository name to a report.

    Args:
        provider: The review provider port. The allowlist is consulted **inside**
            it, before any process exists (SEC-10, ADR-0030 decision 1), so
            nothing here re-checks it: a second copy of a control is a second
            place for it to disagree with the first.
        land: How screened records become durable. See :data:`LandRecords`.
        read_landed: What this repository already has. See :data:`ReadLandedKeys`.
        root: The project root, the containment boundary the gate reads its
            configuration through.
        config_file: The project's ``config.yaml``, composed by the caller from
            ``ProjectPaths``.
    """

    def __init__(
        self,
        *,
        provider: ReviewProvider,
        land: LandRecords,
        read_landed: ReadLandedKeys,
        root: Path,
        config_file: Path,
    ) -> None:
        self._provider = provider
        self._land = land
        self._read_landed = read_landed
        self._root = root
        self._config_file = config_file

    async def run(self, request: ReviewIngestRequest) -> ReviewIngestReport:
        """Fetch, screen and land one window of one repository.

        Raises:
            ReviewIngestRefusedError: For every repository-scope refusal -- see
                this module's docstring. Propagated rather than folded into the
                report: the run established no set of pull requests, so a report
                of what it landed would be a report about nothing.
            ReviewEvidenceError: If what is already on disk cannot be read, or a
                record cannot be written. Raised by the injected callables and
                left to the composition root, which knows what artefact they
                name.
        """
        already = self._read_landed()
        listing = await self._provider.list_pull_requests(
            request.project_id,
            request.repository,
            since_number=request.since_number,
            limit=request.limit,
        )

        fetched: list[_Fetched] = []
        # Listing skips first, and the order is the run's own: the provider
        # answered them before a single fetch happened, so a report that
        # interleaved them would order two seams by nothing.
        skipped: list[FetchRefusal] = [
            FetchRefusal.of(item.repository, item.number, item.envelope) for item in listing.skipped
        ]
        for event in listing.events:
            outcome = await self._fetch(request.project_id, event)
            if isinstance(outcome, FetchRefusal):
                skipped.append(outcome)
                continue
            fetched.append(outcome)

        records, scan = self._screen(fetched)
        landed = frozenset(self._land(records))
        return ReviewIngestReport(
            repository=request.repository,
            policy=scan.policy.value,
            redacted=scan.redacted,
            secrets_warned=scan.warned,
            pull_requests=_count(records, ReviewEvent),
            review_submissions=_count(records, ReviewSubmission),
            review_threads=_count(records, ReviewThread),
            new=len(landed - already),
            updated=len(landed & already),
            kept=len(already - landed),
            refused=tuple(verdict.identity for verdict in scan.refusals),
            findings=scan.findings,
            skipped=tuple(skipped),
        )

    async def _fetch(self, project_id: ProjectId, event: ReviewEvent) -> _Fetched | FetchRefusal:
        """One pull request's two per-record reads, or the refusal that stopped them.

        **This ``try`` wraps one pull request's reads and nothing wider**, which
        is what makes the scope discrimination a property of where the fault was
        rather than of the grade: a refusal from anywhere else in :meth:`run`
        propagates. Both reads sit inside the one ``try`` on purpose -- a pull
        request whose threads arrived and whose reviews refused is a pull request
        this run cannot record honestly, so it is withheld whole rather than
        landed missing its verdicts.

        The listing's own record-scope faults never reach here: the provider
        already answered them as values, and :meth:`run` folds them into the same
        report channel this returns into.
        """
        try:
            threads = await self._provider.get_threads(project_id, event)
            submissions = await self._provider.get_reviews(project_id, event)
        except ReviewIngestRefusedError as exc:
            return FetchRefusal.of(event.repository, event.number, exc.envelope)
        return _Fetched(event=event, submissions=submissions, threads=threads)

    def _screen(
        self, fetched: Sequence[_Fetched]
    ) -> tuple[tuple[LandedRecord, ...], ReviewScanOutcome]:
        """Every fetched record through the gate, and what may be written.

        **Only :attr:`~theurian.application.review_landing_gate.RecordVerdict.landing`
        becomes a record.** Re-deriving the payload from the candidate would
        write the pre-redaction value and silently undo R-12, which is the one
        mistake that shape exists to make impossible; the verdict is the only
        thing read here.

        ``strict=True`` on the zip is the assertion that the gate answered one
        verdict per candidate, in candidate order. Without it, a gate that
        dropped a verdict would pair each remaining one with the wrong pull
        request and land records under another event's anchor.
        """
        pairs = [(item.event, payload) for item in fetched for payload in item.payloads()]
        candidates = [
            LandingCandidate(
                repository=event.repository, pull_request_number=event.number, payload=payload
            )
            for event, payload in pairs
        ]
        scan = screen_landing_candidates(candidates, root=self._root, config_file=self._config_file)
        records = tuple(
            LandedRecord(
                provider=self._provider.provider_id,
                repository=event.repository,
                anchor=_anchor(self._provider.provider_id, event, verdict.landing),
                payload=verdict.landing,
            )
            for (event, _payload), verdict in zip(pairs, scan.verdicts, strict=True)
            if verdict.landing is not None
        )
        return records, scan


def _count(records: Sequence[LandedRecord], kind: type[ReviewRecordPayload]) -> int:
    """How many landed records carry a payload of ``kind``.

    Keyed on the payload's own type rather than on a kind string, so the three
    counts cannot drift from the three shapes the domain has: a fourth payload
    type reddens ``mypy`` at :func:`_anchor`'s exhaustive ``match`` before it can
    reach a count nobody added.
    """
    return sum(1 for record in records if isinstance(record.payload, kind))


def _anchor(provider: str, event: ReviewEvent, payload: ReviewRecordPayload) -> SourceAnchor:
    """FR-S3's pointer back to the upstream object this record came from.

    The pull request's ``url`` is the source URI for all three kinds, because it
    is the only address the provider gives that a person can open: a review
    submission and a thread are addressed by node id, and composing a fragment
    URL out of one would be a link this code invented rather than one the
    provider answered with.

    Built from the **landing** payload wherever the value lives on it. What comes
    from ``event`` instead is the URL and the repository, neither of which
    redaction touches -- so nothing here can reintroduce a display name the run
    decided to drop.
    """
    match payload:
        case ReviewEvent():
            return SourceAnchor(
                provider=provider,
                source_uri=payload.url,
                repository=payload.repository,
                commit_sha=payload.head_commit,
                external_id=payload.external_key,
            )
        case ReviewSubmission():
            return SourceAnchor(
                provider=provider,
                source_uri=event.url,
                repository=event.repository,
                external_id=payload.external_id,
            )
        case ReviewThread():
            start, end = _span(payload.line_start, payload.line_end)
            return SourceAnchor(
                provider=provider,
                source_uri=event.url,
                repository=event.repository,
                commit_sha=payload.commit_sha,
                file_path=payload.file_path,
                line_start=start,
                line_end=end,
                external_id=payload.external_id,
            )


def _span(start: int | None, end: int | None) -> tuple[int | None, int | None]:
    """A thread's two line numbers as an anchor is allowed to hold them.

    :class:`~theurian.domain.knowledge.SourceAnchor` puts **three** guards on
    this pair, and this function answers a pair that satisfies all three: a line
    number below one is refused because the numbering is 1-based, an end without
    a start is refused, and an end that precedes its start is refused. A provider
    can answer with any of those shapes. GitHub sends ``startLine: null`` with
    ``line`` set for a single-line comment, so an end alone is an anchor at that
    one line rather than a missing value -- which is why it moves into ``start``
    instead of being dropped.

    **The 1-based guard is the one this function was written without**, and it
    was the same fault as the ordering guard rather than a different kind: a
    ``startLine: 0`` in a GraphQL answer reached ``SourceAnchor`` as an argument
    the domain refuses, aborting a whole run *after* the fetch it had already
    paid for. A non-positive number is now dropped exactly the way a non-ordered
    end already was.

    Dropping loses a locator and no evidence: the comment body is what the record
    carries, and it lands whole either way. Refusing the record instead would let
    a provider's bad line number withhold a review conversation.
    """
    first = start if start is not None and start >= 1 else None
    last = end if end is not None and end >= 1 else None
    if first is None:
        return last, None
    if last is None or last < first:
        return first, None
    return first, last
