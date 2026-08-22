"""What the run tells a maintainer, and what it must never tell them.

Three claims live here, and each has a way of quietly becoming false.

**A missing source is drift, not an absence.** ``Report.drifted`` covers both
shapes -- changed bytes and a document that is gone -- because a corpus holding
a snapshot of a file this repository no longer publishes is the same finding as
one holding a stale copy. Partitioning ``SOURCE_MISSING`` anywhere else makes
deleting an ADR the way to make its drift warning disappear.

**An uncheckable anchor is reported but is not evidence.** ``Report.compared``
is the count that decides whether the run asserted anything at all, and it is
what stands between "26 anchors, all fine" and "26 anchors, none of them
actually compared".

**The remedy has to be the right remedy.** The committed body is pinned by
``contentSha256`` *and* by a byte-identity rule against its own anchor commit
(``test_dogfood_corpus_governance``), so a maintainer who reads this output and
edits the body to match takes that governance test RED and destroys the thing
that made it a snapshot. The text says re-seed through `theurian propose`, and
this file holds it to that -- including holding it to *not* appearing under a
clean report, where it would be advice about nothing.
"""

from __future__ import annotations

import pytest
from corpus_drift import (
    REMEDY,
    Comparison,
    Report,
    Status,
    Verdict,
    render_github,
    render_summary,
    render_text,
)

pytestmark = pytest.mark.unit

_MIGRATION = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6A-adr-0001-example.yaml"
_DOCUMENT = "docs/adr/0001-example.md"


def _comparison(
    verdict: Verdict,
    *,
    detail: str = "a stated reason",
    expected: str = "",
    actual: str = "",
) -> Comparison:
    return Comparison(
        migration=_MIGRATION,
        item_id="architecture.example",
        revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
        file_path=_DOCUMENT,
        verdict=verdict,
        expected=expected,
        actual=actual,
        detail=detail,
    )


_MATCHED = _comparison(Verdict.MATCHED, expected="a" * 64, actual="a" * 64)
_DRIFTED = _comparison(
    Verdict.DRIFTED,
    expected="a" * 64,
    actual="b" * 64,
    detail=f"{_DOCUMENT} now hashes to bbbbbbbbbbbb, and the corpus pins aaaaaaaaaaaa",
)
_MISSING = _comparison(Verdict.SOURCE_MISSING, expected="a" * 64, detail=f"{_DOCUMENT} is gone")
_SKIPPED = _comparison(Verdict.UNCHECKABLE, detail="carries a line range")

_CLEAN = Report((_MATCHED,), Status.CLEAN, "no drift -- compared 1 anchor(s).")
_DRIFT = Report((_DRIFTED,), Status.DRIFTED, "1 drifted -- compared 1 anchor(s).")
_EMPTY = Report((_SKIPPED,), Status.NOTHING_COMPARED, "all 1 anchor(s) were uncheckable.")


# -- what the run counted ----------------------------------------------------


def test_a_source_document_that_is_gone_is_counted_as_drift() -> None:
    """Deleting the ADR must not be a way to silence its drift warning.

    The corpus still publishes a snapshot of it, still labelled `approved`, and
    Theurian's own agents still retrieve it.
    """
    report = Report((_MISSING,), Status.DRIFTED, "1 drifted.")

    assert report.drifted == (_MISSING,)


def test_an_uncheckable_anchor_is_reported_but_is_not_counted_as_drift() -> None:
    """A skip is not a finding about the document; it is a finding about the anchor."""
    report = Report((_MATCHED, _SKIPPED), Status.CLEAN, "no drift.")

    assert report.drifted == ()
    assert report.uncheckable == (_SKIPPED,)


def test_an_uncheckable_anchor_is_not_evidence_that_anything_was_compared() -> None:
    """`compared` is what stands between "all fine" and "nothing was checked".

    Counting a skip here is how a corpus whose every anchor became uncomparable
    would report a clean run for the rest of the project's life.
    """
    report = Report((_MATCHED, _SKIPPED, _MISSING), Status.DRIFTED, "1 drifted.")

    assert report.compared == (_MATCHED, _MISSING)


# -- the remedy --------------------------------------------------------------


def test_the_remedy_sends_the_maintainer_to_propose_and_not_to_the_committed_body() -> None:
    """An in-place edit takes the governance test RED; the re-seed is the only fix.

    Both halves are asserted: that it names the workflow, and that it forbids
    the edit. A remedy that named `theurian propose` while staying silent about
    the body would still leave "just fix the file" as the obvious reading.
    """
    assert "theurian propose --item-id" in REMEDY
    assert "theurian propose accept" in REMEDY
    assert "do not edit the\ncommitted body, which is pinned verbatim" in REMEDY


def test_a_text_report_of_drift_carries_the_remedy() -> None:
    """The finding and the fix arrive together, in the job log and on a local run."""
    assert REMEDY in render_text(_DRIFT)


def test_a_clean_text_report_does_not_offer_a_remedy_for_nothing() -> None:
    """Advice printed on every green run is advice nobody reads on the red one."""
    assert REMEDY not in render_text(_CLEAN)


def test_the_summary_carries_the_remedy_and_names_the_drifted_document() -> None:
    """`$GITHUB_STEP_SUMMARY` is where a maintainer looks before the log.

    The table is the delivery mechanism for an advisory job: the exit status is
    0, so this is the only place the finding is visible without opening the run.
    """
    summary = render_summary(_DRIFT)

    assert f"| `architecture.example` | `{_DOCUMENT}` | drifted |" in summary
    assert REMEDY in summary


# -- the annotations ---------------------------------------------------------


def test_drift_is_annotated_as_a_warning_on_the_document_that_changed() -> None:
    """The annotation lands on the changed `docs/` file in the pull request's Files view.

    `file=` is the source document rather than the migration on purpose: the
    person who needs to see it is the one who just edited the ADR.
    """
    commands = render_github(_DRIFT)

    assert commands[0].startswith(f"::warning file={_DOCUMENT},title=Corpus drift::")
    assert "architecture.example" in commands[0]


def test_an_uncheckable_anchor_is_annotated_rather_than_silently_dropped() -> None:
    """A skipped anchor is named, with its reason, keyed to the migration that holds it.

    A `::notice` rather than a `::warning`: it is not a finding about `docs/`,
    but a run that stopped comparing something must say so out loud.
    """
    commands = render_github(Report((_MATCHED, _SKIPPED), Status.CLEAN, "no drift."))

    assert commands == (
        f"::notice file={_MIGRATION},title=Corpus anchor not compared::carries a line range",
    )


def test_a_run_that_compared_nothing_is_annotated_as_an_error() -> None:
    """The one outcome that must never read as green, in the one place CI shows it.

    Drift is deliberately only a `::warning` because the job is advisory. That
    choice is exactly why the empty run needs its own `::error`: without it, a
    corpus that vanished produces a quieter log than an edited ADR.
    """
    commands = render_github(_EMPTY)

    assert commands[-1].startswith("::error title=Corpus drift check ran empty::")


def test_a_clean_run_emits_no_annotations_at_all() -> None:
    """Nothing to say. Without this, "annotate everything" passes both tests above."""
    assert render_github(_CLEAN) == ()
