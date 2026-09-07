"""One ingestion run driven end to end, minus the network (ADR-0030 decisions 3, 4).

The claims, each with cases of its own:

* **Scope-matched containment, across three seams.** A refusal *raised* by
  ``list_pull_requests`` halts the run before a single per-pull-request fetch
  happens; a pull request the listing *returned* as skipped, and a refusal raised
  by ``get_threads`` or ``get_reviews``, each withhold that one pull request's
  records whole while the run continues. All three are driven with the **same
  grade** (``LIMIT_EXCEEDED``), because the discrimination the service makes is
  by where the fault was and a set of cases using different grades could not tell
  that rule apart from a shortcut that read the grade.
* **Retryability without a marker.** Run one skips a record; run two over the
  same window lands it once the cause is gone. Driven rather than asserted: the
  second run is a real second run against the same directory, with the same
  arguments and nothing carried between them -- and driven from **both** skip
  seams, because a marker invented at either one would step over the record.
* **Only the verdict's payload is written.** A redacting run's landed file
  carries the placeholder, and the control beside it shows the records the
  provider answered with still carry the real name -- so a service that
  re-derived the payload from the candidate fails here rather than passing
  quietly.

The provider is :class:`~fakes.CannedReviewProvider`; nothing here spawns ``gh``
and nothing reaches the network. Marked ``unit`` and writes only under
``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

import pytest
from fakes import CannedReviewProvider, ReadKey

from theurian.application.review_ingest_service import (
    LandedRecord,
    ReviewIngestReport,
    ReviewIngestRequest,
    ReviewIngestService,
    _span,
)
from theurian.application.review_landing_gate import (
    REDACTED_DISPLAY_NAME,
    LandingCandidate,
    ReviewScanOutcome,
    screen_landing_candidates,
)
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.security.project_config import PROJECT_CONFIG_FILE

pytestmark = pytest.mark.unit

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: A synthetic credential: AWS's own published example key, which this
#: repository's detector reports as ``aws-access-key-id``. Written out rather than
#: derived from the detector, so the input is one a person can recognise instead
#: of an expectation computed from the thing under test.
SECRET: Final = "AKIAIOSFODNN7EXAMPLE"  # noqa: S105 - AWS's published example key, not a credential

RUN_ONE: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))
RUN_TWO: Final = IngestionRun("01K1BBBBBB01234567890ABCDE", datetime(2026, 9, 8, 9, 0, tzinfo=UTC))


# -- canned provider values ---------------------------------------------------


def _participant(name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id="U_kwDO1", display_name=name)


def _event(number: int = 42, **overrides: object) -> ReviewEvent:
    event = ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title="Bound the retry budget",
        body="The retry loop is now bounded.",
        author=_participant(),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="fix/retry-budget",
        labels=("security",),
        milestone="0.2.0",
    )
    return replace(event, **overrides)  # type: ignore[arg-type]


def _submission(event: ReviewEvent) -> ReviewSubmission:
    return ReviewSubmission(
        external_id=f"PRR_kwDO{event.number}",
        project_id=PROJECT,
        event_key=event.external_key,
        author=_participant("Reviewer Two"),
        body="Approving; the budget is bounded now.",
        state="APPROVED",
        submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
    )


def _thread(event: ReviewEvent, **overrides: object) -> ReviewThread:
    thread = ReviewThread(
        external_id=f"PRRT_kwDO{event.number}",
        project_id=PROJECT,
        event_key=event.external_key,
        file_path="src/order.py",
        comments=(
            ReviewComment(
                external_id=f"IC_kwDO{event.number}",
                author=_participant(),
                body="This retries forever.",
                created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
            ),
        ),
        state=ReviewThreadState.RESOLVED,
        resolution=ReviewResolution(
            state=ReviewThreadState.RESOLVED,
            resolved_by=_participant("Reviewer Two"),
            fix_commit="e" * 40,
        ),
        # `startLine: null` with `line` set is what GitHub answers for a
        # single-line comment, and `SourceAnchor` refuses an end without a start:
        # the anchoring case worth carrying in the default fixture rather than in
        # one test.
        line_start=None,
        line_end=12,
        commit_sha="f" * 40,
    )
    return replace(thread, **overrides)  # type: ignore[arg-type]


def _over_cap(number: int) -> ReviewIngestRefusedError:
    """The refusal an over-cap thread raises, in the adapter's own grade.

    ``LIMIT_EXCEEDED`` deliberately: the pull-request listing's page cap carries
    the same grade, so a service that discriminated on the grade rather than on
    where the fault was would treat this one as a reason to halt the whole run.
    """
    return ReviewIngestRefusedError(
        RefusalGrade.LIMIT_EXCEEDED,
        f"Review thread on {REPOSITORY}#{number} carries more than the recorded cap.",
    )


def _over_the_label_cap(number: int) -> ReviewIngestRefusedError:
    """The refusal the adapter meets while **building** one pull request's record.

    The third grade-sharing member, and the one this file could not express
    before the port carried a skip channel: a pull request's labels overflow the
    single page they are asked for, which is a fact about that pull request and
    not about the repository. Same grade as the two above, again on purpose.
    """
    return ReviewIngestRefusedError(
        RefusalGrade.LIMIT_EXCEEDED,
        f"Pull request {REPOSITORY}#{number} carries more than the recorded label cap.",
    )


def _provider(
    events: Sequence[ReviewEvent],
    *,
    refusals: dict[ReadKey, ReviewIngestRefusedError] | None = None,
    listing_refusal: ReviewIngestRefusedError | None = None,
    listing_faults: dict[int, ReviewIngestRefusedError] | None = None,
    threads: dict[int, tuple[ReviewThread, ...]] | None = None,
) -> CannedReviewProvider:
    """The fake, pre-loaded with one thread and one review per pull request."""
    given = threads or {}
    return CannedReviewProvider(
        events,
        threads={event.number: given.get(event.number, (_thread(event),)) for event in events},
        submissions={event.number: (_submission(event),) for event in events},
        refusals=refusals,
        listing_refusal=listing_refusal,
        listing_faults=listing_faults,
    )


# -- composition --------------------------------------------------------------


@final
class _Lander:
    """The store, bound the way a composition root binds it, plus a call log.

    A four-field copy from :class:`LandedRecord` onto the store's own record,
    which is the whole of the mapping ADR-0003 keeps in a composition root --
    ``cli/review_commands.py`` owns the shipped one and
    ``tests/integration/test_review_ingest_cli.py`` drives it. Recorded here so a
    test can assert *what* was handed over and not only what reached the disk.
    """

    def __init__(self, store: ReviewEvidenceStore, run: IngestionRun) -> None:
        self._store = store
        self._run = run
        self.handed: list[LandedRecord] = []
        self.calls = 0

    def __call__(self, records: Sequence[LandedRecord]) -> tuple[str, ...]:
        self.calls += 1
        self.handed.extend(records)
        return self._store.write(
            [
                EvidenceRecord(
                    provider=record.provider,
                    repository=record.repository,
                    anchor=record.anchor,
                    payload=record.payload,
                )
                for record in records
            ],
            run=self._run,
        )


def _project(tmp_path: Path, config: str | None = None) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True, exist_ok=True)
    path = root / ".theurian" / PROJECT_CONFIG_FILE
    if config is not None:
        path.write_text(config, encoding="utf-8")
    return root, path


def _settings(*, policy: str | None = None, redact: bool | None = None) -> str:
    lines = ["apiVersion: theurian.dev/v1"]
    if policy is not None:
        lines += ["security:", f'  secretScan: "{policy}"']
    if redact is not None:
        lines += ["providers:", "  review:", f"    redactParticipantNames: {str(redact).lower()}"]
    return "\n".join(lines) + "\n"


def _store(root: Path) -> ReviewEvidenceStore:
    return ReviewEvidenceStore(root / ".theurian" / "review")


def _service(
    root: Path,
    config_file: Path,
    provider: CannedReviewProvider,
    lander: _Lander,
    store: ReviewEvidenceStore,
) -> ReviewIngestService:
    """The service as a composition root builds it, with the canned provider.

    The reader filters by repository **case-folded**, which is the equality the
    adapter itself uses when it checks GitHub's answer against the allowlisted
    entry: a project may spell its allowlist entry in any case the provider
    accepts, and a byte comparison would report every record as vanished.
    """

    def read_landed() -> frozenset[str]:
        return frozenset(
            stored.relative_path
            for stored in store.read_all()
            if stored.record.repository.casefold() == REPOSITORY.casefold()
        )

    return ReviewIngestService(
        provider=provider,
        land=lander,
        read_landed=read_landed,
        root=root,
        config_file=config_file,
    )


def _request(since_number: int | None = None) -> ReviewIngestRequest:
    return ReviewIngestRequest(
        project_id=PROJECT, repository=REPOSITORY, limit=50, since_number=since_number
    )


async def _run(
    tmp_path: Path,
    provider: CannedReviewProvider,
    *,
    config: str | None = None,
    since_number: int | None = None,
) -> tuple[ReviewIngestReport, _Lander, Path]:
    root, config_file = _project(tmp_path, config)
    store = _store(root)
    lander = _Lander(store, RUN_ONE)
    report = await _service(root, config_file, provider, lander, store).run(_request(since_number))
    return report, lander, root


def _landed_files(root: Path) -> set[str]:
    review = root / ".theurian" / "review"
    if not review.exists():
        return set()
    return {str(path.relative_to(review)) for path in review.rglob("*.json")}


# -- a clean run --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_run_lands_every_record_and_reports_it_by_kind(tmp_path: Path) -> None:
    """One pull request lands three records: the event, its review, its thread."""
    report, _lander, root = await _run(tmp_path, _provider([_event()]))

    assert report.clean
    assert (report.pull_requests, report.review_submissions, report.review_threads) == (1, 1, 1)
    assert report.landed == 3
    assert (report.new, report.updated, report.kept) == (3, 0, 0)
    assert report.refused == ()
    assert report.findings == ()
    assert report.skipped == ()
    assert len(_landed_files(root)) == 3


@pytest.mark.asyncio
async def test_a_second_run_over_the_same_window_updates_rather_than_adds(tmp_path: Path) -> None:
    """Refetch is a refresh: the same records land again over their own files."""
    root, config_file = _project(tmp_path)
    store = _store(root)
    provider = _provider([_event()])

    first = await _service(root, config_file, provider, _Lander(store, RUN_ONE), store).run(
        _request()
    )
    second = await _service(root, config_file, provider, _Lander(store, RUN_TWO), store).run(
        _request()
    )

    assert (first.new, first.updated) == (3, 0)
    assert (second.new, second.updated, second.kept) == (0, 3, 0)
    assert len(_landed_files(root)) == 3


@pytest.mark.asyncio
async def test_a_record_outside_the_window_keeps_its_file_and_counts_as_kept(
    tmp_path: Path,
) -> None:
    """Nothing deletes: a record this run did not land keeps its file and its stamp.

    Driven through the **window** rather than through an upstream deletion,
    because the two are one fact to this service -- it landed the record last
    time and did not land it this time -- and the window is the cause a caller
    controls. ``kept`` is deliberately not an upstream-deletion count, which is
    exactly what this case shows.
    """
    root, config_file = _project(tmp_path)
    store = _store(root)
    both = _provider([_event(42), _event(41)])

    await _service(root, config_file, both, _Lander(store, RUN_ONE), store).run(_request())
    narrowed = await _service(root, config_file, both, _Lander(store, RUN_TWO), store).run(
        _request(since_number=41)
    )

    assert (narrowed.updated, narrowed.kept) == (3, 3)
    assert len(_landed_files(root)) == 6
    assert {stored.last_seen.run_id for stored in store.read_all()} == {
        RUN_ONE.run_id,
        RUN_TWO.run_id,
    }


# -- scope-matched containment ------------------------------------------------


@pytest.mark.asyncio
async def test_a_repository_scope_refusal_halts_before_any_per_pull_request_fetch(
    tmp_path: Path,
) -> None:
    """AC-2: the listing refused, so the set of records is not one to iterate.

    The assertion that carries the claim is the empty read log, not the raised
    exception: a service that caught the listing refusal and reported it would
    still have to be shown not to have fetched anything, and only the log shows
    that.
    """
    refusal = ReviewIngestRefusedError(
        RefusalGrade.REPOSITORY_IS_PRIVATE,
        f"Review ingestion refused {REPOSITORY!r}: it does not resolve as public.",
    )
    provider = _provider([_event()], listing_refusal=refusal)
    root, config_file = _project(tmp_path)
    store = _store(root)
    lander = _Lander(store, RUN_ONE)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await _service(root, config_file, provider, lander, store).run(_request())

    assert raised.value.grade is RefusalGrade.REPOSITORY_IS_PRIVATE
    assert raised.value.remedy
    assert provider.reads == []
    assert lander.calls == 0
    assert not (root / ".theurian" / "review").exists()


@pytest.mark.asyncio
async def test_an_over_cap_pull_request_is_skipped_while_its_neighbour_lands(
    tmp_path: Path,
) -> None:
    """AC-1: one known-bad member of a set that is still worth iterating."""
    provider = _provider([_event(42), _event(41)], refusals={("get_threads", 42): _over_cap(42)})

    report, _lander, root = await _run(tmp_path, provider)

    assert not report.clean
    assert report.landed == 3
    assert [skip.identity.pull_request_number for skip in report.skipped] == [42]
    (skip,) = report.skipped
    assert skip.grade is RefusalGrade.LIMIT_EXCEEDED
    assert "#42" in skip.identity.describe()
    assert "recorded cap" in skip.summary
    assert skip.remedy
    landed = _landed_files(root)
    assert len(landed) == 3
    assert not any("42" in name for name in landed), (
        f"the skipped pull request left a file behind: {sorted(landed)}"
    )
    assert provider.reads[-1] == ("get_reviews", 41), (
        "the run stopped at the refusing pull request instead of continuing"
    )


@pytest.mark.asyncio
async def test_a_pull_request_the_listing_could_not_build_is_skipped_not_a_halt(
    tmp_path: Path,
) -> None:
    """The newest pull request is unbuildable, and the rest of the repository lands.

    This is the seam the service could not see before the port carried it. A
    record-scope fault met **inside** the listing -- one pull request's labels
    past their cap -- used to leave the adapter as a raised refusal, and from the
    newest pull request that denied the whole repository at any ``--limit``: no
    ``--since`` steps forward over it.

    The read log is the assertion that carries the containment claim: the
    unbuildable pull request was never fetched, and its neighbour was.
    """
    provider = _provider([_event(42), _event(41)], listing_faults={42: _over_the_label_cap(42)})

    report, lander, root = await _run(tmp_path, provider)

    assert not report.clean
    assert report.landed == 3
    assert provider.reads == [("get_threads", 41), ("get_reviews", 41)]
    (skip,) = report.skipped
    assert skip.identity.pull_request_number == 42
    assert skip.grade is RefusalGrade.LIMIT_EXCEEDED
    assert "#42" in skip.identity.describe()
    assert "label cap" in skip.summary
    assert "`limit`" in skip.remedy
    assert [
        record.payload.number for record in lander.handed if isinstance(record.payload, ReviewEvent)
    ] == [41]
    assert len(_landed_files(root)) == 3


@pytest.mark.asyncio
async def test_a_pull_request_outside_the_window_is_not_reported_as_skipped(
    tmp_path: Path,
) -> None:
    """An excluded pull request is not a withheld one, however bad its data is.

    ``since_number`` says which pull requests this run is about, so a fault in
    one below the boundary is not this run's to report. Reporting it would put a
    record the caller deliberately excluded into the report and make every
    incremental re-run read as unclean for as long as the bad record exists.

    The fake applies the window before consulting its fault map, which is the
    adapter's own order -- see :class:`~fakes.CannedReviewProvider` on why that
    ordering is load-bearing rather than incidental.
    """
    provider = _provider(
        [_event(102), _event(101), _event(100)], listing_faults={100: _over_the_label_cap(100)}
    )

    report, _lander, _root = await _run(tmp_path, provider, since_number=100)

    assert report.clean
    assert report.skipped == ()
    assert report.pull_requests == 2
    assert provider.reads == [
        ("get_threads", 102),
        ("get_reviews", 102),
        ("get_threads", 101),
        ("get_reviews", 101),
    ]


@pytest.mark.asyncio
async def test_one_grade_halts_at_the_listing_and_skips_at_both_seams(tmp_path: Path) -> None:
    """The discrimination is by where the fault was: one grade, three outcomes.

    All three drive ``LIMIT_EXCEEDED`` -- the grade the pull-request listing's
    page cap, a pull request's own label cap and a thread's comment cap share --
    so a service reading ``exc.grade`` to decide would answer the same for every
    one of them. This is the case that tells the rule apart from that shortcut,
    and it is the reason a listing fault has to arrive as a **value**: an
    exception carries no scope a caller can read.
    """
    at_the_fetch, _lander, _root = await _run(
        tmp_path / "fetch", _provider([_event(42)], refusals={("get_threads", 42): _over_cap(42)})
    )
    assert [skip.grade for skip in at_the_fetch.skipped] == [RefusalGrade.LIMIT_EXCEEDED]

    in_the_listing, _lander, _root = await _run(
        tmp_path / "node", _provider([_event(42)], listing_faults={42: _over_the_label_cap(42)})
    )
    assert [skip.grade for skip in in_the_listing.skipped] == [RefusalGrade.LIMIT_EXCEEDED]

    halting = _provider([_event(42)], listing_refusal=_over_cap(42))
    with pytest.raises(ReviewIngestRefusedError):
        await _run(tmp_path / "listing", halting)
    assert halting.reads == []


@pytest.mark.asyncio
async def test_a_gate_answering_fewer_verdicts_than_candidates_lands_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short verdict list stops the run rather than shifting it (#605 item 2).

    ``_screen`` pairs candidates with verdicts **positionally**, so a gate that
    answered one verdict fewer gives every remaining candidate the verdict of the
    record after it -- a payload paired with an anchor composed from the event
    standing beside it in the candidate list, and with two pull requests in the
    window the shift crosses the boundary between them.
    ``zip(..., strict=True)`` is the whole of what refuses that, and relaxing it
    over this fixture was measured landing **five records where the run fetched
    six**: pull request 42's own event record disappears with nothing reporting a
    corpus smaller than the one screened.

    **What escapes is a bare ``ValueError`` from ``zip`` itself**, carrying no
    grade and no remedy, unlike every other refusal this service surfaces. That
    is asserted here as the contract that ships rather than endorsed as the right
    one: whether this belongs in the graded envelope is the open half of #605
    item 2, and answering it is a production change this test does not make.

    The stub cannot pass for the wrong reason. It calls the real gate and drops
    one verdict from the answer, so a ``monkeypatch`` that missed its target
    would let the run finish and this would fail at the ``raises`` instead.
    """
    import theurian.application.review_ingest_service as service

    def _one_verdict_short(
        candidates: Sequence[LandingCandidate], *, root: Path, config_file: Path
    ) -> ReviewScanOutcome:
        outcome = screen_landing_candidates(candidates, root=root, config_file=config_file)
        return replace(outcome, verdicts=outcome.verdicts[1:])

    monkeypatch.setattr(service, "screen_landing_candidates", _one_verdict_short)
    root, config_file = _project(tmp_path)
    store = _store(root)
    lander = _Lander(store, RUN_ONE)

    with pytest.raises(ValueError) as raised:
        await _service(root, config_file, _provider([_event(42), _event(41)]), lander, store).run(
            _request()
        )

    assert type(raised.value) is ValueError, "a graded refusal now reaches here; re-read #605"
    assert lander.handed == []
    assert _landed_files(root) == set()


@pytest.mark.asyncio
async def test_a_pull_request_whose_reviews_refuse_lands_none_of_its_records(
    tmp_path: Path,
) -> None:
    """Withheld whole: the threads arrived, and the event still does not land.

    A pull request recorded without its verdicts looks whole and is not, which is
    the reason the adapter refuses a half-read thread rather than truncating it.
    """
    provider = _provider([_event(42)], refusals={("get_reviews", 42): _over_cap(42)})

    report, lander, root = await _run(tmp_path, provider)

    assert provider.reads == [("get_threads", 42), ("get_reviews", 42)]
    assert report.landed == 0
    assert lander.handed == []
    assert _landed_files(root) == set()


# -- retryability, with no marker anywhere ------------------------------------


@pytest.mark.asyncio
async def test_a_skipped_pull_request_lands_on_the_next_run_over_the_same_window(
    tmp_path: Path,
) -> None:
    """AC-3, cap cause: run one skips it, run two lands it, nothing recorded it as seen.

    The second run asks for the **same window** with the same arguments. Nothing
    is reset between them and nothing is passed forward: were a marker to exist
    and count a skipped record as seen, the second run would land nothing and
    this would go RED.
    """
    root, config_file = _project(tmp_path)
    store = _store(root)

    refusing = _provider([_event(42)], refusals={("get_threads", 42): _over_cap(42)})
    first = await _service(root, config_file, refusing, _Lander(store, RUN_ONE), store).run(
        _request()
    )
    assert first.landed == 0
    assert [skip.identity.pull_request_number for skip in first.skipped] == [42]
    assert _landed_files(root) == set()

    healthy = _provider([_event(42)])
    second = await _service(root, config_file, healthy, _Lander(store, RUN_TWO), store).run(
        _request()
    )

    assert second.clean
    assert second.landed == 3
    assert (second.new, second.updated, second.kept) == (3, 0, 0)


@pytest.mark.asyncio
async def test_a_pull_request_skipped_in_the_listing_lands_on_the_next_run(
    tmp_path: Path,
) -> None:
    """AC-3, listing cause: the third skip seam is retryable like the other two.

    Retryability is a property of the *absence of a marker*, and a marker would
    have to decide what a skip means at every seam that produces one. This drives
    the seam the port newly carries: run one cannot build the record, run two
    over the same window with the same arguments lands it. Were a marker to exist
    and count the skipped pull request as seen, the second run would land nothing
    and this would go RED.
    """
    root, config_file = _project(tmp_path)
    store = _store(root)

    unbuildable = _provider([_event(42)], listing_faults={42: _over_the_label_cap(42)})
    first = await _service(root, config_file, unbuildable, _Lander(store, RUN_ONE), store).run(
        _request()
    )
    assert first.landed == 0
    assert [skip.identity.pull_request_number for skip in first.skipped] == [42]
    assert unbuildable.reads == [], "the unbuildable pull request was fetched anyway"
    assert _landed_files(root) == set()

    healthy = _provider([_event(42)])
    second = await _service(root, config_file, healthy, _Lander(store, RUN_TWO), store).run(
        _request()
    )

    assert second.clean
    assert second.landed == 3
    assert (second.new, second.updated, second.kept) == (3, 0, 0)


@pytest.mark.asyncio
async def test_a_blocked_record_lands_on_the_next_run_once_the_secret_is_gone(
    tmp_path: Path,
) -> None:
    """AC-3, flag cause: the same sequence with the gate as the cause of the skip."""
    root, config_file = _project(tmp_path)
    store = _store(root)

    flagged = _provider(
        [_event(42)], threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}
    )
    first = await _service(root, config_file, flagged, _Lander(store, RUN_ONE), store).run(
        _request()
    )
    assert first.landed == 2
    assert [identity.record_id for identity in first.refused] == ["PRRT_kwDO42"]
    assert not first.clean

    cleaned = _provider([_event(42)])
    second = await _service(root, config_file, cleaned, _Lander(store, RUN_TWO), store).run(
        _request()
    )

    assert second.clean
    assert second.landed == 3
    assert second.new == 1, "the previously refused thread is the one new record"
    assert second.updated == 2


# -- the gate's three policies, through the service ---------------------------


@pytest.mark.asyncio
async def test_block_withholds_the_flagged_record_and_lands_its_siblings(tmp_path: Path) -> None:
    """AC-4, ``block``: the flagged unit never becomes a file and the run is not clean."""
    provider = _provider(
        [_event(42)], threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}
    )

    report, lander, root = await _run(tmp_path, provider, config=_settings(policy="block"))

    assert not report.clean
    assert report.review_threads == 0
    assert report.pull_requests == 1
    assert [identity.record_id for identity in report.refused] == ["PRRT_kwDO42"]
    assert [finding.field for finding in report.findings] == ["filePath"]
    assert SECRET not in " ".join(finding.describe() for finding in report.findings)
    assert not any("PRRT" in name for name in _landed_files(root))
    assert [type(record.payload).__name__ for record in lander.handed] == [
        "ReviewEvent",
        "ReviewSubmission",
    ]


@pytest.mark.asyncio
async def test_warn_lands_the_flagged_record_and_still_reports_the_finding(
    tmp_path: Path,
) -> None:
    """AC-4, ``warn``: reported, landed, and the run reads clean.

    ``clean`` is the exit-code question and it answers ``True`` here on purpose:
    the project has recorded that a finding is reported and the record lands
    anyway. The gate's own ``ReviewScanOutcome.clean`` answers ``False`` for this
    same run, which is why the two are different properties under different
    names.
    """
    provider = _provider(
        [_event(42)], threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}
    )

    report, _lander, root = await _run(tmp_path, provider, config=_settings(policy="warn"))

    assert report.clean
    assert report.policy == "warn"
    assert report.review_threads == 1
    assert report.refused == ()
    assert [finding.field for finding in report.findings] == ["filePath"]
    assert len(_landed_files(root)) == 3


@pytest.mark.asyncio
async def test_off_scans_nothing_and_lands_everything(tmp_path: Path) -> None:
    """AC-4, ``off``: no findings, no refusals, and the same secret lands."""
    provider = _provider(
        [_event(42)], threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}
    )

    report, _lander, root = await _run(tmp_path, provider, config=_settings(policy="off"))

    assert report.clean
    assert report.policy == "off"
    assert report.findings == ()
    assert report.refused == ()
    assert len(_landed_files(root)) == 3


# -- only the verdict's payload is written ------------------------------------


@pytest.mark.asyncio
async def test_only_the_verdicts_redacted_payload_reaches_the_lander(tmp_path: Path) -> None:
    """AC-5: a service that re-derived the payload from the candidate fails here.

    Two halves, and the second is what gives the first teeth. The landed bytes
    must carry the placeholder **and not** the display name -- and the display
    name has to be one the provider really answered with, or the assertion would
    hold for a service that wrote nothing at all. The control is the canned
    provider's own records, which still carry both names.
    """
    provider = _provider([_event(42)])

    report, lander, root = await _run(tmp_path, provider, config=_settings(redact=True))

    assert report.redacted
    answered = await provider.get_reviews(PROJECT, _event(42))
    assert answered[0].author.display_name == "Reviewer Two"

    named = {
        record.payload.author.display_name
        for record in lander.handed
        if isinstance(record.payload, ReviewEvent | ReviewSubmission)
    }
    assert named == {REDACTED_DISPLAY_NAME}

    review = root / ".theurian" / "review"
    landed_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(review.rglob("*.json"))
    )
    assert REDACTED_DISPLAY_NAME in landed_text
    assert "Reviewer One" not in landed_text
    assert "Reviewer Two" not in landed_text
    assert "U_kwDO1" in landed_text, "redaction must keep the provider id the graph is built on"


@pytest.mark.asyncio
async def test_every_landed_record_carries_an_anchor_back_to_its_pull_request(
    tmp_path: Path,
) -> None:
    """FR-S3: three records, three anchors, one pull-request URL.

    The thread's line span is the case worth driving: GitHub answers a
    single-line comment with ``startLine: null`` and ``line`` set, which
    :class:`~theurian.domain.knowledge.SourceAnchor` refuses as an end with no
    start.
    """
    _report, lander, _root = await _run(tmp_path, _provider([_event(42)]))

    assert {record.anchor.source_uri for record in lander.handed} == {
        f"https://github.com/{REPOSITORY}/pull/42"
    }
    assert {record.anchor.provider for record in lander.handed} == {PROVIDER}
    (thread,) = [record for record in lander.handed if isinstance(record.payload, ReviewThread)]
    assert (thread.anchor.line_start, thread.anchor.line_end) == (12, None)
    assert thread.anchor.file_path == "src/order.py"
    assert thread.anchor.external_id == "PRRT_kwDO42"


@pytest.mark.parametrize("end", (None, -1, 0, 1, 5))
@pytest.mark.parametrize("start", (None, -1, 0, 1, 5))
def test_every_line_pair_a_provider_can_answer_becomes_an_anchor(
    start: int | None, end: int | None
) -> None:
    """``_span`` is total against all three of ``SourceAnchor``'s line guards.

    The domain refuses a number below one, an end without a start, and an end
    that precedes its start. ``_span`` was written against the last two only, so
    a ``startLine: 0`` -- a number a JSON response carries and nothing upstream
    of here rejects -- reached the constructor as an argument it refuses and
    aborted the run *after* the fetch had been paid for.

    The grid rather than the one case: a guard missed once is missed the same way
    twice, and the pairs are what the function's contract ranges over. The
    construction is the assertion -- it raises when the pair is one the domain
    will not hold -- so this cannot pass by agreeing with ``_span`` about what a
    good pair is.
    """
    first, last = _span(start, end)

    anchor = SourceAnchor(
        provider=PROVIDER,
        source_uri=f"https://github.com/{REPOSITORY}/pull/42",
        line_start=first,
        line_end=last,
    )

    assert (anchor.line_start, anchor.line_end) == (first, last)


def test_a_single_line_comment_still_anchors_at_the_line_it_names() -> None:
    """The positive control on the grid: dropping everything would pass it.

    ``_span`` may answer ``(None, None)`` for every input and satisfy every
    construction above. What it must not lose is the shape GitHub actually sends
    -- ``startLine: null`` with ``line`` set -- which is an anchor at one line
    rather than a missing locator.
    """
    assert _span(None, 12) == (12, None)
    assert _span(10, 12) == (10, 12)
    assert _span(10, None) == (10, None)


@pytest.mark.asyncio
async def test_a_thread_whose_provider_numbered_it_from_zero_still_lands(
    tmp_path: Path,
) -> None:
    """The service-level half: a bad line number costs the locator, not the run.

    Driven through the real service rather than through ``_span`` alone, because
    what the finding cost was the *run*: the construction raised inside
    ``_anchor``, after every fetch had happened, so one provider answer withheld
    the whole repository's evidence -- including the two records that had nothing
    to do with lines.
    """
    event = _event(42)
    provider = _provider([event], threads={42: (_thread(event, line_start=0, line_end=0),)})

    report, lander, root = await _run(tmp_path, provider)

    assert report.clean
    assert len(_landed_files(root)) == 3
    (thread,) = [record for record in lander.handed if isinstance(record.payload, ReviewThread)]
    assert (thread.anchor.line_start, thread.anchor.line_end) == (None, None)
