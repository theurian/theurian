"""Two published sentences that named a value nothing bounded (round two).

``test_review_ingest_refusals.py`` walks every sentence this package publishes
and requires each interpolated value to be routed or to be this package's own.
Round two found the walk's **key** too narrow rather than its table wrong: it saw
``ReviewIngestRefusedError`` summaries and nothing else, so two published values
sat outside a population whose entire purpose is to catch exactly them.

* ``ReviewEvidenceStore.write``'s collision refusal rendered
  ``{record.record_key!r}`` -- a provider node id, from a response -- and
  published 2,000,468 characters of it.
* ``ReviewRecordIdentity.describe`` rendered ``{self.pull_request_number}`` raw,
  and that line is published once per report entry: 4,300 digits (the
  interpreter's own limit, which is what ``json.loads`` allows through) times a
  500-slot window is 2.17 MB of document.

The walk is widened; these are the drivers. **Both are driven with a value that
expands under rendering**, because a bound taken before rendering is not a bound
on what the sentence carries -- ``repr`` turns one U+202E into the six
characters ``\\u202e``, which is the ordering defect ``bounded_quote`` exists to
stop and the reason a cut-then-quote fix would pass a length-only test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from theurian.application.review_landing_gate import ReviewRecordIdentity
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewThread,
)
from theurian.domain.review_ingest import MAX_SUMMARY_ECHO_CHARS
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceError,
    ReviewEvidenceStore,
)

pytestmark = pytest.mark.unit

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: U+202E, spelled by code point because a literal one is invisible in a diff and
#: reorders every line it sits on. **Six characters under ``repr``**, which is
#: what makes it the driver here: a value cut to
#: :data:`MAX_SUMMARY_ECHO_CHARS` and quoted afterwards comes back six times that
#: long, so a fix that bounds before it renders passes a length assertion and
#: still publishes a sentence past its own limit.
RIGHT_TO_LEFT_OVERRIDE: Final = chr(0x202E)

#: How much longer ``repr`` makes one of those. Measured rather than asserted, so
#: the "expander" claim above is a fact this file checks rather than one it makes.
_EXPANSION: Final = 6


def _participant() -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id="U_kwDO1", display_name="Reviewer One")


def _thread(external_id: str) -> ReviewThread:
    return ReviewThread(
        external_id=external_id,
        project_id=PROJECT,
        event_key=f"{PROVIDER}:{REPOSITORY}#42",
        file_path="src/order.py",
        comments=(
            ReviewComment(
                external_id="IC_kwDO42",
                author=_participant(),
                body="This retries forever.",
                created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
            ),
        ),
        state=ReviewThreadState.OPEN,
    )


def _record(external_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=SourceAnchor(
            provider=PROVIDER, source_uri=f"https://github.com/{REPOSITORY}/pull/42"
        ),
        payload=_thread(external_id),
    )


def _event(number: int) -> ReviewEvent:
    return ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title="t",
        body="b",
        author=_participant(),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="h",
        labels=(),
    )


def test_the_driver_really_expands_under_rendering() -> None:
    """The premise both rows rest on, measured rather than assumed.

    Without this the two assertions below would hold for a value ``repr`` leaves
    alone, and a cut-then-quote implementation would pass them: the whole
    difference between the two orderings is visible only for a value whose
    rendering is longer than itself.
    """
    assert len(repr(RIGHT_TO_LEFT_OVERRIDE)) - len("''") == _EXPANSION


def test_a_hostile_record_key_is_bounded_in_the_collision_refusal(tmp_path: Path) -> None:
    """RED means a provider node id is published at whatever length it arrived.

    The collision refusal is the one sentence that names a record key, and it
    named it with ``!r`` -- which bounds nothing at all. Measured before the fix:
    2,000,468 characters into an operator's terminal, over an identifier the
    provider chose.

    The assertion is on the **rendering's** length, because the rendering is what
    the sentence carries: a driver of 4,000 U+202E is 24,000 characters once
    quoted, so a cut applied before quoting would leave the sentence six times
    past its bound while passing a check written against the value.
    """
    root = tmp_path / "review"
    root.mkdir()
    hostile = f"PRRT_kwDO{RIGHT_TO_LEFT_OVERRIDE * 4_000}"
    record = _record(hostile)
    run = IngestionRun(run_id="run-1", observed_at=datetime(2026, 8, 1, tzinfo=UTC))

    with pytest.raises(ReviewEvidenceError) as raised:
        ReviewEvidenceStore(root).write([record, record], run=run)

    message = str(raised.value)
    assert RIGHT_TO_LEFT_OVERRIDE not in message, (
        "the refusal published the override raw, so it reorders every line printed "
        "around it in a terminal"
    )
    assert "cut from" in message, f"the record key was not cut at all: {message[:200]}"
    assert len(message) < MAX_SUMMARY_ECHO_CHARS * _EXPANSION, (
        f"the refusal is {len(message)} characters. A bound taken before `repr` is not a "
        f"bound on the sentence: quoting expands one U+202E into {_EXPANSION}."
    )


@pytest.mark.parametrize(
    ("label", "digits", "expected"),
    [
        ("at the interpreter's own digit limit", 4_299, "cut from"),
        ("past what `str` will render", 5_000, "cannot render"),
    ],
    ids=["4299-digits", "5000-digits"],
)
def test_a_pull_request_number_is_bounded_in_the_published_line(
    label: str, digits: int, expected: str
) -> None:
    """RED means one report entry can carry megabytes of digits.

    ``describe`` is published once per entry in three lists, so the cost is the
    line times the window: a 4,300-digit number -- exactly what ``json.loads``
    lets through, since it applies the interpreter's own limit while parsing --
    across a 500-slot window is 2.17 MB of document.

    The second row is the reason ``bounded_echo`` rather than a slice:
    ``str()`` of a large enough integer **raises**, and this line is published
    rather than attempted. A raw f-string would turn a report into a traceback.
    """
    line = ReviewRecordIdentity(REPOSITORY, 10**digits).describe()

    assert expected in line, f"{label}: the number was published unbounded -- {line[:120]}"
    assert len(line) < MAX_SUMMARY_ECHO_CHARS * 2, f"{label}: the line is {len(line)} characters"


def test_an_ordinary_line_still_reads_as_itself() -> None:
    """The other direction: routing did not make the common case unreadable.

    A bound that mangled every ordinary value would pass both rows above. The
    number a reviewer actually types has to come through as itself, because this
    line is what an operator reads to answer "which pull requests am I missing".
    """
    line = ReviewRecordIdentity(REPOSITORY, _event(42).number).describe()

    assert line == f"'{REPOSITORY}'#42"
