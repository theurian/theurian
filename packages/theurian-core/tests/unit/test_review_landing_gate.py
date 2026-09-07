"""The ingestion secret gate and R-12's redaction, driven (ADR-0030 decisions 3, 4).

Five claims, each with its own cases:

* **The scan reads decision 3's untrusted rows, and every participant id
  beside them.** A synthetic credential is planted in every one of them **in
  turn** -- sixteen positions, one case each -- because a guard no planted input
  reaches survives its own deletion. The negative cases plant the same string in
  a structural field and assert the run is clean, which is where the boundary is.
  A participant's ``external_id`` moved onto the planted side in PR #596 round 1:
  the GitHub adapter falls back to the author's own login when the response
  carries no node id, so it is not the provider's value it was recorded as.
* **``block`` withholds the record whole, and the file is never created.** The
  assertion is on the filesystem: the gate and
  :class:`~theurian.infrastructure.review_evidence.ReviewEvidenceStore` are
  composed the way an ingestion run composes them, and the flagged record's path
  does not exist afterwards.
* **A flagged record in one pull request does not stop another landing.**
* **``off`` scans nothing**, observed with a counter around the detector rather
  than by an absence of output -- a gate that scanned and discarded the findings
  would produce the same empty report.
* **Redaction replaces every display name, keeps every provider node id, and
  pseudonymises a login-fallback id**, and the scan reads the *post*-redaction
  candidate, so a credential that only ever sat in a name this run is about to
  drop does not refuse the record -- while the login-fallback id, which the run
  *would* have written, is a pseudonym by the time anything reads it.

Marked ``unit`` and writes only under ``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.application.review_landing_gate import (
    REDACTED_DISPLAY_NAME,
    REDACTED_ID_PREFIX,
    LandingCandidate,
    ReviewRecordPayload,
    ReviewScanOutcome,
    screen_landing_candidates,
)
from theurian.domain.enums import ReviewThreadState
from theurian.domain.errors import ProjectConfigError
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
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.security.content_secrets import MAX_FINDINGS, scan_text
from theurian.security.project_config import PROJECT_CONFIG_FILE, SecretScanPolicy

pytestmark = pytest.mark.unit

PROJECT = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"
NUMBER: Final = 42

#: A synthetic credential, not a real one: AWS's own documentation example key,
#: which this repository's detector reports as ``aws-access-key-id``. Written out
#: here rather than derived from the detector's patterns, so the test drives an
#: input a person can recognise instead of an expectation computed from the thing
#: under test.
SECRET: Final = "AKIAIOSFODNN7EXAMPLE"  # noqa: S105 - AWS's published example key, not a credential

#: U+202E, spelled by code point because a literal one is invisible in a diff and
#: reorders every line it sits on. Six characters under ``repr``, which is what
#: makes it cross the bound-then-quote trip threshold.
_RIGHT_TO_LEFT_OVERRIDE: Final = "\u202e"

RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))


def _project(tmp_path: Path, config: str | None = None) -> tuple[Path, Path]:
    """A project root and its ``config.yaml``, written only when ``config`` is given."""
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    path = root / ".theurian" / PROJECT_CONFIG_FILE
    if config is not None:
        path.write_text(config, encoding="utf-8")
    return root, path


def _settings(*, policy: str | None = None, redact: bool | None = None) -> str:
    """A configuration file stating one or both of the keys this gate reads."""
    lines = ["apiVersion: theurian.dev/v1"]
    if policy is not None:
        lines += ["security:", f'  secretScan: "{policy}"']
    if redact is not None:
        lines += ["providers:", "  review:", f"    redactParticipantNames: {str(redact).lower()}"]
    return "\n".join(lines) + "\n"


def _participant(name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id="U_kwDO1", display_name=name)


def _identified(external_id: str) -> ReviewParticipant:
    """A participant whose *id* is the planted value and whose name is ordinary.

    One field at a time, so a case that refuses says which of the two positions
    the scan read.
    """
    return ReviewParticipant(
        provider=PROVIDER, external_id=external_id, display_name="Reviewer One"
    )


def _login_only(login: str) -> ReviewParticipant:
    """The shape the GitHub adapter builds when a response carries no node id.

    ``response.optional_participant`` reads ``external_id`` as *node id or login*
    and ``display_name`` as *login or external id*, so both fields hold the same
    author-chosen string. Written out here rather than imported, because what this
    file drives is the record the gate meets, not the mapper that made it -- the
    adapter's own side is
    ``tests/integration/test_gh_review_provider.py::test_an_author_the_response_gave_no_node_id_is_recorded_under_its_login``.
    """
    return ReviewParticipant(provider=PROVIDER, external_id=login, display_name=login)


def _event(**overrides: object) -> ReviewEvent:
    event = ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=NUMBER,
        title="Bound the retry budget",
        body="The retry loop is now bounded.",
        author=_participant(),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{NUMBER}",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="fix/retry-budget",
        labels=("security",),
        linked_issue_ids=("I_kwDO1",),
        milestone="0.2.0",
    )
    return replace(event, **overrides)  # type: ignore[arg-type]


def _submission(**overrides: object) -> ReviewSubmission:
    submission = ReviewSubmission(
        external_id="PRR_kwDO1",
        project_id=PROJECT,
        event_key=f"{PROVIDER}:{REPOSITORY}#{NUMBER}",
        author=_participant("Reviewer Two"),
        body="Approving; the budget is bounded now.",
        state="APPROVED",
        submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
    )
    return replace(submission, **overrides)  # type: ignore[arg-type]


def _comment(**overrides: object) -> ReviewComment:
    comment = ReviewComment(
        external_id="IC_kwDO1",
        author=_participant(),
        body="This retries forever.",
        created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
    )
    return replace(comment, **overrides)  # type: ignore[arg-type]


def _thread(**overrides: object) -> ReviewThread:
    thread = ReviewThread(
        external_id="PRRT_kwDO1",
        project_id=PROJECT,
        event_key=f"{PROVIDER}:{REPOSITORY}#{NUMBER}",
        file_path="src/order.py",
        comments=(_comment(),),
        state=ReviewThreadState.RESOLVED,
        resolution=ReviewResolution(
            state=ReviewThreadState.RESOLVED,
            resolved_by=_participant("Reviewer Two"),
            fix_commit="e" * 40,
        ),
        commit_sha="f" * 40,
    )
    return replace(thread, **overrides)  # type: ignore[arg-type]


def _candidate(payload: ReviewRecordPayload, number: int = NUMBER) -> LandingCandidate:
    return LandingCandidate(repository=REPOSITORY, pull_request_number=number, payload=payload)


def _screen(
    tmp_path: Path, payload: ReviewRecordPayload, *, config: str | None = None
) -> ReviewScanOutcome:
    root, path = _project(tmp_path, config)
    return screen_landing_candidates([_candidate(payload)], root=root, config_file=path)


# -- the scanned population ---------------------------------------------------

#: Every author-controlled field of decision 3's table plus every participant id,
#: as a builder that puts ``SECRET`` in exactly that field and the literal the
#: report should name.
#:
#: **Sixteen positions, one case each**, because a field the guard forgot passes
#: every aggregate assertion: the three that look structural -- a label, the head
#: branch name, the milestone -- are the row decision 3 says is most easily got
#: wrong, and each is planted on its own here. The four ``externalId`` rows are
#: the row the table itself had wrong (PR #596 round 1): they sit here rather than
#: in :data:`_STRUCTURAL` because the adapter writes a login into that field
#: whenever GitHub's answer carried no node id.
_PLANTED: Final[tuple[tuple[str, Callable[[], ReviewRecordPayload], str, str | None], ...]] = (
    ("event title", lambda: _event(title=SECRET), "title", None),
    ("event body", lambda: _event(body=f"see {SECRET}"), "body", None),
    (
        "event author display name",
        lambda: _event(author=_participant(SECRET)),
        "author.displayName",
        None,
    ),
    (
        "event author external id",
        lambda: _event(author=_identified(SECRET)),
        "author.externalId",
        None,
    ),
    ("head branch name", lambda: _event(head_ref_name=f"fix/{SECRET}"), "headRefName", None),
    ("a label", lambda: _event(labels=("security", SECRET)), "labels[1]", None),
    ("the milestone name", lambda: _event(milestone=SECRET), "milestone", None),
    ("review body", lambda: _submission(body=f"see {SECRET}"), "body", None),
    (
        "review author display name",
        lambda: _submission(author=_participant(SECRET)),
        "author.displayName",
        None,
    ),
    (
        "review author external id",
        lambda: _submission(author=_identified(SECRET)),
        "author.externalId",
        None,
    ),
    ("thread file path", lambda: _thread(file_path=f"src/{SECRET}.py"), "filePath", None),
    (
        "comment body",
        lambda: _thread(comments=(_comment(body=f"try {SECRET}"),)),
        "comments[0].body",
        "IC_kwDO1",
    ),
    (
        "comment author display name",
        lambda: _thread(comments=(_comment(author=_participant(SECRET)),)),
        "comments[0].author.displayName",
        "IC_kwDO1",
    ),
    (
        "comment author external id",
        lambda: _thread(comments=(_comment(author=_identified(SECRET)),)),
        "comments[0].author.externalId",
        "IC_kwDO1",
    ),
    (
        "the participant who resolved the thread",
        lambda: _thread(
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                resolved_by=_participant(SECRET),
                fix_commit="e" * 40,
            )
        ),
        "resolution.resolvedBy.displayName",
        None,
    ),
    (
        "the external id of the participant who resolved the thread",
        lambda: _thread(
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                resolved_by=_identified(SECRET),
                fix_commit="e" * 40,
            )
        ),
        "resolution.resolvedBy.externalId",
        None,
    ),
)


@pytest.mark.parametrize(
    ("build", "field", "comment_id"),
    [(row[1], row[2], row[3]) for row in _PLANTED],
    ids=[row[0] for row in _PLANTED],
)
def test_a_secret_planted_in_any_author_controlled_field_refuses_the_record(
    tmp_path: Path, build: Callable[[], ReviewRecordPayload], field: str, comment_id: str | None
) -> None:
    """AC-3: decision 3's untrusted rows, each driven with an input that reaches it.

    ``block`` is the shipped default, so no configuration file is written: the
    absent-key path is the one an ordinary project takes, and testing the
    configured path instead would leave the default untested.
    """
    outcome = _screen(tmp_path, build())

    assert outcome.policy is SecretScanPolicy.BLOCK
    (verdict,) = outcome.verdicts
    assert verdict.refused, f"a secret in `{field}` did not refuse the record"
    assert verdict.landing is None
    assert [finding.field for finding in verdict.findings] == [field]
    assert verdict.findings[0].comment_id == comment_id
    assert verdict.findings[0].finding.family == "aws-access-key-id"


#: The same string in a field the table calls structural, with the reason it is
#: not scanned. A provider chooses these; nobody writes prose into them.
#:
#: A participant's ``external_id`` sat here until PR #596 round 1 and is in
#: :data:`_PLANTED` now: "a provider chooses these" was false of it, because the
#: adapter writes the author's login into that field whenever GitHub's answer
#: carried no node id. Nothing else moved -- these eight are record ids, shas, a
#: URL, a provider's own state vocabulary and a key this product composes.
_STRUCTURAL: Final[tuple[tuple[str, Callable[[], ReviewRecordPayload]], ...]] = (
    ("the head commit sha", lambda: _event(head_commit=SECRET)),
    ("a linked issue id", lambda: _event(linked_issue_ids=(SECRET,))),
    ("the pull request url", lambda: _event(url=f"https://example.invalid/{SECRET}")),
    ("a review state", lambda: _submission(state=SECRET)),
    ("a thread's own id", lambda: _thread(external_id=SECRET)),
    ("a thread's commit sha", lambda: _thread(commit_sha=SECRET)),
    ("a comment's own id", lambda: _thread(comments=(_comment(external_id=SECRET),))),
    ("the event key", lambda: _thread(event_key=SECRET)),
)


@pytest.mark.parametrize(
    "build", [row[1] for row in _STRUCTURAL], ids=[row[0] for row in _STRUCTURAL]
)
def test_a_secret_shaped_value_in_a_structural_field_does_not_refuse(
    tmp_path: Path, build: Callable[[], ReviewRecordPayload]
) -> None:
    """AC-3's boundary: the scan reads the untrusted rows and stops there.

    Without these cases "the scan reads exactly the author-controlled fields"
    would be held only from one side -- a gate that scanned every string on the
    object would pass every case above. Structural values are the provider's, and
    a scan that refused a record over its own commit sha would withhold evidence
    on a shape nobody authored.
    """
    outcome = _screen(tmp_path, build())

    (verdict,) = outcome.verdicts
    assert not verdict.refused
    assert verdict.findings == ()


def test_the_url_is_structural_even_though_a_person_can_choose_a_branch_in_it(
    tmp_path: Path,
) -> None:
    """The one structural row worth stating: a URL is the provider's rendering.

    Decision 3's table puts the pull request's ``url`` on the provider's side, and
    the head branch name -- which a person does choose -- on the author's. This
    module scans the branch name and not the URL, which is why the branch case
    above refuses and this one does not.
    """
    outcome = _screen(tmp_path, _event(url=f"https://example.invalid/{SECRET}"))

    assert outcome.clean


#: More distinct credentials than one record's whole budget, in a single value.
#:
#: AWS's ``AKIA`` shape with a distinct sixteen-character suffix each, so the
#: detector reports them as separate findings rather than as repetitions of one
#: value, and the margin above :data:`MAX_FINDINGS` is what makes the **cap**
#: rather than the supply the thing that ends the scan.
_MORE_SECRETS_THAN_THE_BUDGET: Final = " ".join(
    f"AKIAEXAMPLEKEY{index:06d}" for index in range(MAX_FINDINGS + 5)
)


def test_the_finding_budget_is_spent_once_across_a_records_fields(tmp_path: Path) -> None:
    """A field that fills the budget does not buy the next field a fresh one.

    ``screen_landing_candidates`` records the budget as *per record*, and the
    number it caps is one that leaves the process: every finding becomes an entry
    in a refusal message and in the report the CLI publishes. A gate that handed
    each scanned value its own
    :data:`~theurian.security.content_secrets.MAX_FINDINGS` would publish as many
    times that number as the record has fields -- which for a thread is two per
    comment, so the ceiling would be set by the provider's answer rather than by
    this constant.

    The title alone carries more than the budget, so the count is settled there
    and the body's own credential is never reached. Both halves are asserted,
    because either alone is weak: the exact count, and that every finding names
    the field the budget was spent in. ``MAX_FINDINGS`` is imported rather than
    restated, so a change to the constant moves this expectation with it.
    """
    outcome = _screen(
        tmp_path, _event(title=_MORE_SECRETS_THAN_THE_BUDGET, body=f"and also {SECRET}")
    )

    (verdict,) = outcome.verdicts
    assert len(verdict.findings) == MAX_FINDINGS
    assert {finding.field for finding in verdict.findings} == {"title"}


# -- the three policies -------------------------------------------------------


def test_under_block_the_flagged_record_never_becomes_a_file(tmp_path: Path) -> None:
    """AC-1, asserted on the filesystem rather than on a return value.

    The gate and the evidence store are composed the way an ingestion run
    composes them: screen, then write what survived. A gate that reported a
    refusal and still handed the payload on would pass an assertion about
    ``verdict.refused`` and fail here.
    """
    root, config = _project(tmp_path)
    store = ReviewEvidenceStore(ProjectPaths.of(root).review)
    flagged = _thread(comments=(_comment(body=f"token {SECRET}"),))

    outcome = screen_landing_candidates([_candidate(flagged)], root=root, config_file=config)
    store.write(_records(outcome), run=RUN)

    assert outcome.refusals
    assert not outcome.clean
    assert list(ProjectPaths.of(root).review.rglob("*.json")) == []


def test_under_block_a_flagged_record_does_not_stop_another_pull_request_landing(
    tmp_path: Path,
) -> None:
    """Refusal is per record: PR 43's clean thread lands while PR 42's is withheld."""
    root, config = _project(tmp_path)
    store = ReviewEvidenceStore(ProjectPaths.of(root).review)
    flagged = _candidate(_thread(comments=(_comment(body=f"token {SECRET}"),)))
    clean = LandingCandidate(
        repository=REPOSITORY,
        pull_request_number=43,
        payload=_thread(external_id="PRRT_kwDO2"),
    )

    outcome = screen_landing_candidates([flagged, clean], root=root, config_file=config)
    store.write(_records(outcome), run=RUN)

    assert [verdict.refused for verdict in outcome.verdicts] == [True, False]
    landed = [path.name for path in ProjectPaths.of(root).review.rglob("*.json")]
    assert landed == ["PRRT_kwDO2.json"]


def test_under_warn_the_record_lands_and_every_finding_is_reported(tmp_path: Path) -> None:
    """AC-2: ``warn`` is the operator's recorded choice to keep the evidence."""
    root, config = _project(tmp_path, _settings(policy="warn"))
    store = ReviewEvidenceStore(ProjectPaths.of(root).review)
    flagged = _thread(comments=(_comment(body=f"token {SECRET}"), _comment(external_id="IC_2")))

    outcome = screen_landing_candidates([_candidate(flagged)], root=root, config_file=config)
    store.write(_records(outcome), run=RUN)

    assert outcome.policy is SecretScanPolicy.WARN
    assert outcome.refusals == ()
    assert [path.name for path in ProjectPaths.of(root).review.rglob("*.json")] == [
        "PRRT_kwDO1.json"
    ]
    (finding,) = outcome.findings
    assert finding.identity.repository == REPOSITORY
    assert finding.identity.pull_request_number == NUMBER
    assert finding.identity.record_id == "PRRT_kwDO1"
    assert finding.comment_id == "IC_kwDO1"


def test_under_off_the_detector_is_never_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-2: observed with a counter, because an empty report is not evidence.

    A gate that scanned and threw the findings away would publish exactly the
    report ``off`` publishes. The counter is what tells the two apart, and it is
    the assertion the ADR's owed test asks for.
    """
    import theurian.application.review_landing_gate as gate

    calls = 0

    def _counted(text: str, *, max_findings: int = 20) -> tuple[()]:
        nonlocal calls
        calls += 1
        return ()

    monkeypatch.setattr(gate, "scan_text", _counted)
    root, config = _project(tmp_path, _settings(policy="off"))

    outcome = screen_landing_candidates(
        [_candidate(_thread(comments=(_comment(body=f"token {SECRET}"),)))],
        root=root,
        config_file=config,
    )

    assert calls == 0
    assert outcome.policy is SecretScanPolicy.OFF
    assert outcome.refusals == ()
    assert outcome.landing


def test_the_counter_would_have_seen_a_scan_under_the_default_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control on the counter above.

    Without it a monkeypatch that missed its target -- a rebound name, a moved
    import -- would leave the ``off`` assertion passing for the wrong reason.
    """
    import theurian.application.review_landing_gate as gate

    calls = 0

    def _counted(text: str, *, max_findings: int = 20) -> tuple[()]:
        nonlocal calls
        calls += 1
        return ()

    monkeypatch.setattr(gate, "scan_text", _counted)
    root, config = _project(tmp_path)

    screen_landing_candidates([_candidate(_event())], root=root, config_file=config)

    assert calls > 0


def test_a_configuration_fault_is_raised_rather_than_reported_as_a_verdict(
    tmp_path: Path,
) -> None:
    """The operator's own file is wrong, and no review is at fault.

    Labelling it as a scan result would send them to look at a pull request.
    """
    root, config = _project(tmp_path, _settings(policy="warm"))

    with pytest.raises(ProjectConfigError):
        screen_landing_candidates([_candidate(_event())], root=root, config_file=config)


# -- the report ---------------------------------------------------------------


def test_no_report_line_carries_the_matched_bytes(tmp_path: Path) -> None:
    """Decision 4: a report that quotes the secret is a second copy of it.

    Driven with the credential in the **file path**, which is the position that
    caught ``proposal_service`` out: a location derived from the scanned text
    republishes the match whenever the match is the location.
    """
    outcome = _screen(tmp_path, _thread(file_path=f"src/{SECRET}.py"))

    (finding,) = outcome.findings
    published = f"{finding.describe()} {finding.field} {finding.identity.describe()}"
    assert SECRET not in published
    assert "AKIA..." in published, "the finding no longer says which shape matched"
    assert "filePath" in published


def test_a_report_line_survives_a_repository_name_that_quotes_long(tmp_path: Path) -> None:
    """Outside values are rendered and then cut, in that order.

    A repository name arrives from a response and can be a megabyte of characters
    that each expand under quoting. Bounding the raw value and quoting afterwards
    is the ordering ``bounded_quote`` exists to stop, and a report line is a
    sentence somebody reads.
    """
    root, config = _project(tmp_path)
    hostile = LandingCandidate(
        repository=_RIGHT_TO_LEFT_OVERRIDE * 100_000,
        pull_request_number=NUMBER,
        payload=_event(title=SECRET),
    )

    outcome = screen_landing_candidates([hostile], root=root, config_file=config)

    (finding,) = outcome.findings
    line = finding.describe()
    assert _RIGHT_TO_LEFT_OVERRIDE not in line, "a raw right-to-left override reached the report"
    assert "\\u202e" in line
    assert len(line) < 1_000


# -- redaction (R-12) ---------------------------------------------------------


def _participants(value: object) -> Iterator[ReviewParticipant]:
    """Every participant reachable from ``value``, however deeply nested.

    Walked rather than enumerated. The redaction lists four positions it replaces;
    this finds them by type, so a participant added anywhere in
    ``domain/review.py`` -- a second author, a dismisser, a requested reviewer --
    is covered by this test the day it exists rather than the day somebody
    remembers to extend a list.
    """
    if isinstance(value, ReviewParticipant):
        yield value
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _participants(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            yield from _participants(getattr(value, field.name))


def test_the_walk_finds_a_participant_in_every_position_a_record_has() -> None:
    """The positive control on the walk the redaction test below rests on.

    A walk that found nothing would make that test pass against a redaction that
    does nothing at all. Four positions, named here so the count is a measurement
    of the shapes this file builds rather than a claim about the domain.
    """
    assert len(list(_participants(_event()))) == 1
    assert len(list(_participants(_submission()))) == 1
    assert len(list(_participants(_thread()))) == 2


@pytest.mark.parametrize(
    "build", (_event, _submission, _thread), ids=("event", "submission", "thread")
)
def test_no_participant_reachable_from_a_landed_record_keeps_its_name(
    tmp_path: Path, build: Callable[[], ReviewRecordPayload]
) -> None:
    """AC-7: the display name goes and the provider id stays (R-12).

    Keeping ``external_id`` is what makes this redaction rather than deletion: the
    identity graph is built on it, so dropping it would disconnect the record
    instead of anonymising it.
    """
    original = build()
    outcome = _screen(tmp_path, original, config=_settings(redact=True))

    (landed,) = outcome.landing
    assert outcome.redacted
    assert [person.display_name for person in _participants(landed)] == [
        REDACTED_DISPLAY_NAME for _ in _participants(original)
    ]
    assert [person.external_id for person in _participants(landed)] == [
        person.external_id for person in _participants(original)
    ]


def test_redaction_is_off_unless_the_project_asks_for_it(tmp_path: Path) -> None:
    """The default direction: a record names the people who wrote the review."""
    outcome = _screen(tmp_path, _thread())

    (landed,) = outcome.landing
    assert not outcome.redacted
    assert REDACTED_DISPLAY_NAME not in [person.display_name for person in _participants(landed)]


def test_a_secret_only_in_a_display_name_redaction_removes_does_not_refuse(
    tmp_path: Path,
) -> None:
    """AC-7's load-bearing half: the scan reads the landing candidate.

    The gate's premise is *before a record becomes a file*, so what it reads has
    to be what would be written. With redaction on, the credential in this display
    name is not in the landing candidate at all -- refusing over it would withhold
    a record because of a string this run had already decided to drop. Reverse the
    order and this test goes RED, which is what makes the module docstring's
    claim about the order a measured one.
    """
    outcome = _screen(tmp_path, _thread(comments=(_comment(author=_participant(SECRET)),)))
    redacting = _screen(
        tmp_path / "second",
        _thread(comments=(_comment(author=_participant(SECRET)),)),
        config=_settings(redact=True),
    )

    assert outcome.refusals, "the control case: without redaction the name refuses the record"
    assert redacting.refusals == ()
    assert redacting.clean


def test_redaction_does_not_hide_a_secret_that_is_anywhere_else(tmp_path: Path) -> None:
    """The other direction: redaction removes a name, never a body.

    Without this the test above would be satisfied by a redaction that emptied the
    whole record, which would pass every "no refusal" assertion and land nothing
    worth keeping.
    """
    outcome = _screen(
        tmp_path,
        _thread(comments=(_comment(author=_participant(SECRET), body=f"token {SECRET}"),)),
        config=_settings(redact=True),
    )

    (verdict,) = outcome.verdicts
    assert verdict.refused
    assert [finding.field for finding in verdict.findings] == ["comments[0].body"]


def test_a_redacted_record_reads_back_from_disk_with_the_placeholder(tmp_path: Path) -> None:
    """What lands is what the gate returned, checked through the store's own reader.

    A landing service that re-derived the payload from the candidate rather than
    taking ``verdict.landing`` would write the original name, and every assertion
    above would still pass.
    """
    root, config = _project(tmp_path, _settings(redact=True))
    store = ReviewEvidenceStore(ProjectPaths.of(root).review)

    outcome = screen_landing_candidates([_candidate(_thread())], root=root, config_file=config)
    store.write(_records(outcome), run=RUN)

    (stored,) = store.read_all()
    assert [person.display_name for person in _participants(stored.record.payload)] == [
        REDACTED_DISPLAY_NAME,
        REDACTED_DISPLAY_NAME,
    ]


# -- a login-fallback participant id (PR #596 round 1, adversarial H-D) --------


def test_a_secret_in_a_login_fallback_participant_id_refuses_the_record(tmp_path: Path) -> None:
    """Redaction off: the id the run would write is the id the scan reads.

    A GitHub login is up to 39 characters of ``[A-Za-z0-9-]``, so
    :data:`SECRET` -- twenty alphanumerics -- is a login somebody can register.
    With no node id in the answer, that string is what the adapter puts in
    ``external_id`` as well as in ``display_name``, and both positions report.
    """
    outcome = _screen(tmp_path, _submission(author=_login_only(SECRET)))

    (verdict,) = outcome.verdicts
    assert verdict.refused
    assert [finding.field for finding in verdict.findings] == [
        "author.displayName",
        "author.externalId",
    ]


def test_a_login_fallback_id_is_pseudonymised_before_it_can_land(tmp_path: Path) -> None:
    """AC-7, the half that made turning redaction *on* the more dangerous setting.

    Before this fix the same record refused under the default and **landed** under
    ``redactParticipantNames: true``, carrying the login in ``externalId`` while
    the ``displayName`` R-12 promised to remove was replaced. The assertion is on
    the bytes of every file the store wrote, not on the returned object: what R-12
    promises is about a file.
    """
    root, config = _project(tmp_path, _settings(redact=True))
    store = ReviewEvidenceStore(ProjectPaths.of(root).review)

    outcome = screen_landing_candidates(
        [_candidate(_submission(author=_login_only(SECRET)))], root=root, config_file=config
    )
    store.write(_records(outcome), run=RUN)

    (landed,) = outcome.landing
    assert not outcome.refusals
    (person,) = _participants(landed)
    assert person.display_name == REDACTED_DISPLAY_NAME
    assert person.external_id.startswith(REDACTED_ID_PREFIX)
    written = list(ProjectPaths.of(root).review.rglob("*.json"))
    assert written, "the record was supposed to land, so there is something to sweep"
    for path in written:
        assert SECRET.encode("utf-8") not in path.read_bytes(), f"{path.name} carries the login"
    assert person.external_id in "\n".join(path.read_text(encoding="utf-8") for path in written), (
        "the pseudonym is what the record is identified by now"
    )


def test_a_node_id_participant_keeps_its_external_id_under_redaction(tmp_path: Path) -> None:
    """The other direction, in one record: a real identity graph survives R-12.

    Both participants of this thread are redacted in the same pass -- the comment's
    author arrives with a node id and keeps it verbatim, the resolver arrives as a
    login fallback and is pseudonymised. A redaction that replaced every id would
    pass the test above and disconnect every record ever ingested.
    """
    thread = _thread(
        comments=(_comment(author=_participant()),),
        resolution=ReviewResolution(
            state=ReviewThreadState.RESOLVED,
            resolved_by=_login_only("octocat"),
            fix_commit="e" * 40,
        ),
    )

    outcome = _screen(tmp_path, thread, config=_settings(redact=True))

    (landed,) = outcome.landing
    author, resolver = _participants(landed)
    assert author.external_id == "U_kwDO1"
    assert resolver.external_id.startswith(REDACTED_ID_PREFIX)
    assert resolver.external_id != "octocat"


def test_the_same_login_pseudonymises_the_same_way_in_two_separate_runs(tmp_path: Path) -> None:
    """AC-7: a pseudonym is a record identity, so it may not move between runs.

    Two screenings under two project roots, each landing its own file, and the
    ``externalId`` on disk compared byte for byte. A pseudonym seeded with
    anything a run chooses -- a token, the clock, ``hash()`` -- passes every
    single-run assertion above and makes the same person a new participant on
    every ingest.
    """
    stored: list[str] = []
    for name in ("first", "second"):
        root, config = _project(tmp_path / name, _settings(redact=True))
        store = ReviewEvidenceStore(ProjectPaths.of(root).review)
        outcome = screen_landing_candidates(
            [_candidate(_submission(author=_login_only("octocat")))],
            root=root,
            config_file=config,
        )
        store.write(_records(outcome), run=RUN)
        (record,) = store.read_all()
        (person,) = _participants(record.record.payload)
        stored.append(person.external_id)

    assert stored[0] == stored[1]
    assert stored[0].startswith(REDACTED_ID_PREFIX)


def test_two_different_logins_do_not_pseudonymise_onto_one_participant(tmp_path: Path) -> None:
    """The positive control on determinism: a constant is stable too.

    Without this, a pseudonym that ignored its input entirely would satisfy the
    test above and collapse every author in the project onto one identity.
    """
    first = _screen(
        tmp_path / "a", _submission(author=_login_only("octocat")), config=_settings(redact=True)
    )
    second = _screen(
        tmp_path / "b", _submission(author=_login_only("hubot")), config=_settings(redact=True)
    )

    (one,) = _participants(first.landing[0])
    (other,) = _participants(second.landing[0])
    assert one.external_id != other.external_id


def test_a_pseudonymous_id_is_not_itself_reported_as_a_secret() -> None:
    """The pseudonym is scanned like every other field, so it must not read as one.

    A substitution the detector flags would make ``block`` refuse every record it
    redacted -- turning R-12 on would stop ingestion altogether. Two independent
    properties keep it out: the value is 25 characters, under the detector's
    32-character candidate floor, and lowercase hex carries no upper-case
    character for its class gate. Both are asserted, because either alone would
    let a change to :data:`_PSEUDONYM_DIGITS` pass unnoticed.
    """
    import theurian.application.review_landing_gate as gate

    logins = [f"user-{index}" for index in range(200)] + [SECRET, "ghost", "octocat"]
    for login in logins:
        pseudonym = gate._pseudonym(login)
        assert len(pseudonym) < 32
        assert pseudonym == pseudonym.lower()
        assert scan_text(pseudonym) == ()


def test_a_login_carrying_a_lone_surrogate_is_pseudonymised_rather_than_raising() -> None:
    """``json.loads`` decodes ``\\ud800`` into a string plain UTF-8 cannot encode.

    That value reaches ``ReviewParticipant`` through the adapter's ``text``
    reader, so a hash that encoded strictly would raise a ``UnicodeEncodeError``
    out of the gate -- a traceback at an operator, over a document GitHub sent.
    """
    import theurian.application.review_landing_gate as gate

    hostile = "oct\ud800cat"

    pseudonym = gate._pseudonym(hostile)

    assert pseudonym.startswith(REDACTED_ID_PREFIX)
    assert pseudonym == gate._pseudonym(hostile)


def _records(outcome: ReviewScanOutcome) -> list[EvidenceRecord]:
    """What a landing service would write, given a gate's verdicts.

    Stands in for the service this slice does not build. It reads
    ``verdict.landing`` and nothing else, which is the whole of the contract
    between the two halves.
    """
    return [
        EvidenceRecord(
            provider=PROVIDER,
            repository=REPOSITORY,
            anchor=SourceAnchor(
                provider=PROVIDER, source_uri=f"https://github.com/{REPOSITORY}/pull/{NUMBER}"
            ),
            payload=payload,
        )
        for payload in outcome.landing
    ]
