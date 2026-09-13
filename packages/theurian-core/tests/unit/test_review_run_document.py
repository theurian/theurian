"""What ``theurian review ingest`` publishes about the pull requests it skipped (#656).

``cli/review_commands._payload`` is the one place a run becomes a document, and
until #656 its ``skipped`` entries were ``FetchRefusal.describe()`` strings --
grade and summary, no cure. ``FetchRefusal`` carries the envelope's remedy and
:data:`~theurian.domain.review_ingest.REMEDIES` records one per grade, so the
cure existed, was correct, and reached nobody: an operator whose pull request was
skipped ``limit-exceeded`` read what was refused and never what to do about it.
#597 had just rewritten that cure with a per-record arm addressed to exactly this
audience.

**Grade-keyed rather than per item**, and these tests are where that shape is
held. The remedy is grade-constant by the domain's own design -- looked up, never
passed in, and ``RefusalEnvelope`` refuses an empty one -- so a copy per skipped
pull request would publish one string many times: ``limit-exceeded``'s cure alone
is over a thousand characters. One entry per grade publishes it at its natural
cardinality, and the entries stay a mapping so a caller reads
``skippedRemedies[grade]`` beside the ``skipped`` line that names the grade.

Unit rather than integration: every claim here is about the function that builds
the document, driven with a real :class:`ReviewIngestReport` rather than a
stand-in, the way ``test_review_ingest_changelog_claims.py`` drives the same
function. ``tests/integration/test_review_ingest_cli.py`` is where the document is
read back off the shipped command, on both channels.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

import pytest

from theurian.application.review_ingest_service import FetchRefusal, ReviewIngestReport
from theurian.cli.review_commands import _payload
from theurian.domain.review_ingest import REMEDIES, RefusalGrade, ReviewIngestRefusedError

pytestmark = pytest.mark.unit

REPOSITORY: Final = "acme/order-service"

#: The published key this file is about. Spelled once, so a rename moves every
#: assertion below together rather than leaving some of them asserting about a key
#: the document no longer has.
_CURES: Final = "skippedRemedies"


def _skip(number: int, grade: RefusalGrade) -> FetchRefusal:
    """One skipped pull request, built the way the ingest run builds one.

    Through :class:`ReviewIngestRefusedError` rather than by constructing a
    :class:`~theurian.domain.review_ingest.RefusalEnvelope` here, because that is
    the only construction that *looks the remedy up* -- the domain's rule is that a
    remedy is never passed in, and a test that passed one could pin a cure the
    shipped table does not hold.
    """
    refused = ReviewIngestRefusedError(grade, f"Pull request {REPOSITORY}#{number} was refused.")
    return FetchRefusal.of(REPOSITORY, number, refused.envelope)


def _published_cures(document: Mapping[str, object]) -> dict[str, str]:
    """The ``skippedRemedies`` mapping, narrowed once for every assertion below.

    ``_payload`` is annotated ``dict[str, object]`` because the document is
    heterogeneous, so an assertion that indexed this field directly would be
    indexing an ``object``. The two checks here are part of the claim rather than a
    cast to get past the type checker: the published shape is a mapping of grade to
    cure, and a value of any other shape fails here naming what arrived instead.
    """
    published = document[_CURES]
    assert isinstance(published, dict), (
        f"`{_CURES}` is published as a {type(published).__name__}, not a mapping of "
        "grade to cure, so a caller cannot look a cure up by the grade beside it"
    )
    wrong = {key: value for key, value in published.items() if not isinstance(value, str)}
    assert not wrong, f"`{_CURES}` carries a non-string cure: {wrong}"
    return published


def _published_skips(document: Mapping[str, object]) -> list[str]:
    """The ``skipped`` lines, narrowed for the join assertion, the same way."""
    published = document["skipped"]
    assert isinstance(published, list), (
        f"`skipped` is published as a {type(published).__name__}, not a list of lines"
    )
    return published


def _report(*skipped: FetchRefusal) -> ReviewIngestReport:
    """A run that landed nothing and skipped exactly what it was given."""
    return ReviewIngestReport(
        repository=REPOSITORY,
        policy="block",
        redacted=False,
        secrets_warned=False,
        pull_requests=0,
        review_submissions=0,
        review_threads=0,
        new=0,
        updated=0,
        kept=0,
        refused=(),
        findings=(),
        skipped=skipped,
    )


def test_a_skipped_pull_request_publishes_the_cure_recorded_for_its_grade() -> None:
    """#656: the recorded cure reaches a published surface at last.

    The whole of the defect was that it did not. ``skipped`` named the pull
    request and its grade, ``FetchRefusal`` held the remedy, and no document, no
    exit code and no stderr line carried it -- the run-ending raise was the only
    path on which ``limit-exceeded``'s cure ever reached an operator, and a
    *skipped* pull request never takes that path.

    Asserted as equality against :data:`REMEDIES` rather than as a substring
    check, because the delivery being lossy is as much a defect as it being
    absent: a truncated or summarised cure reads as a cure and sends the reader
    off with part of the instructions.
    """
    document = _payload(_report(_skip(42, RefusalGrade.LIMIT_EXCEEDED)))

    assert _published_cures(document) == {
        RefusalGrade.LIMIT_EXCEEDED.value: REMEDIES[RefusalGrade.LIMIT_EXCEEDED]
    }


def test_a_run_that_skipped_nothing_publishes_an_empty_mapping_and_no_cure_text() -> None:
    """The key is part of the shape, not something that appears on trouble.

    Always present, so a caller scripting ``--json | jq '.skippedRemedies'`` reads
    a mapping on every run rather than ``null`` on the good ones -- the discipline
    ``secretFindings`` already holds one document over.

    The second half is the one a shape assertion misses: an empty mapping is worth
    nothing if the cure text arrived somewhere else in the document anyway. A run
    that skipped nothing has no audience for any of these cures, so none of them
    may be in it -- checked over the whole rendered document and over every grade
    in the table, rather than over the one grade the tests above happen to use.
    """
    document = _payload(_report())

    assert _published_cures(document) == {}

    rendered = json.dumps(document)
    leaked = sorted(grade.value for grade in RefusalGrade if REMEDIES[grade][:60] in rendered)
    assert not leaked, (
        f"a run that skipped nothing published the cure for {leaked} anyway, so the "
        "empty mapping above is not the whole claim: something else in this document "
        "carries cure text."
    )


def test_two_pull_requests_skipped_for_one_reason_publish_that_cure_once() -> None:
    """The reason the mapping is keyed on the grade rather than on the item.

    ``REMEDIES`` is a lookup by grade -- the domain's docstring says the remedy is
    "looked up, never passed in", and ``RefusalEnvelope``'s construction refuses an
    empty one -- so two pull requests refused for the same reason carry the *same*
    string. Publishing it per item would put a cure of over a thousand characters
    into the document once per skipped pull request, and a run may skip as many as
    its window holds.

    The count is asserted over the rendered document rather than over the mapping,
    because the mapping cannot repeat a key by construction: what this rules out is
    a *second* copy elsewhere, which is exactly what a per-item field would have
    been.
    """
    two = _report(
        _skip(42, RefusalGrade.LIMIT_EXCEEDED),
        _skip(41, RefusalGrade.LIMIT_EXCEEDED),
    )

    document = _payload(two)

    assert len(_published_skips(document)) == 2, "both pull requests must still be named"
    assert _published_cures(document) == {
        RefusalGrade.LIMIT_EXCEEDED.value: REMEDIES[RefusalGrade.LIMIT_EXCEEDED]
    }
    rendered = json.dumps(document)
    assert rendered.count(REMEDIES[RefusalGrade.LIMIT_EXCEEDED][:60]) == 1, (
        "the cure appears more than once in the document, which is the repetition the "
        "grade-keyed shape exists to avoid"
    )


def test_each_distinct_grade_in_a_run_publishes_its_own_cure_in_first_seen_order() -> None:
    """Two reasons, two cures, and the order is the report's rather than the table's.

    ``ReviewIngestReport.skipped`` is ordered -- the listing's refusals first, then
    the fetches' -- and dict construction keeps first-seen insertion order, so the
    mapping reads in the order the skips are published in. Pinned because the
    alternative a reader might assume, iteration over ``RefusalGrade`` or over
    ``REMEDIES``, would publish rows for grades this run never met.
    """
    document = _payload(
        _report(
            _skip(42, RefusalGrade.TOOL_FAILED),
            _skip(41, RefusalGrade.LIMIT_EXCEEDED),
            _skip(40, RefusalGrade.TOOL_FAILED),
        )
    )

    published = _published_cures(document)

    assert list(published) == [
        RefusalGrade.TOOL_FAILED.value,
        RefusalGrade.LIMIT_EXCEEDED.value,
    ]
    assert published[RefusalGrade.TOOL_FAILED.value] == REMEDIES[RefusalGrade.TOOL_FAILED]
    assert published[RefusalGrade.LIMIT_EXCEEDED.value] == REMEDIES[RefusalGrade.LIMIT_EXCEEDED]


def test_only_the_grades_this_run_met_are_published_never_the_whole_table() -> None:
    """Which rows reach the field, which is the half a value assertion cannot make.

    The document's audience is the operator of *this* run, and the table holds a
    cure for every grade the provider can refuse with -- most of them about
    repositories and tooling this run never touched. Publishing the table would
    hand a caller instructions for faults that did not happen, and would make the
    field say nothing about the run at all.

    Asserted as a strict subset plus an equality with the run's own grades, so it
    reddens both for a row that should not be there and for a run whose grade is
    missing.
    """
    skips = (_skip(42, RefusalGrade.LIMIT_EXCEEDED), _skip(41, RefusalGrade.TOOL_FAILED))

    published = _published_cures(_payload(_report(*skips)))

    assert set(published) == {skip.grade.value for skip in skips}
    assert set(published) < {grade.value for grade in RefusalGrade}, (
        "this run met two grades and the document published every grade in the table"
    )


def test_a_skipped_entry_and_its_cure_are_joined_by_the_grade_string() -> None:
    """The join a reader has to make, and the only thing that makes the mapping usable.

    ``skipped`` is a list of rendered lines and ``skippedRemedies`` is keyed on the
    grade; a caller pairs them by reading the grade out of the line. That works
    only while the line spells the grade exactly as the key does -- both come from
    ``RefusalGrade.value`` today, and a describe that started printing a prettier
    label would break the pairing while every other assertion in this file stayed
    green.
    """
    skips = (_skip(42, RefusalGrade.LIMIT_EXCEEDED), _skip(41, RefusalGrade.TOOL_FAILED))

    document = _payload(_report(*skips))

    published = _published_cures(document)

    for skip, line in zip(skips, _published_skips(document), strict=True):
        assert skip.grade.value in line, (
            f"the published line for {skip.identity.pull_request_number} does not spell "
            f"the grade that keys its cure, so a caller cannot pair the two: {line!r}"
        )
        assert published[skip.grade.value]
