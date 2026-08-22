"""A run that compared too little must not read as a run that found nothing wrong.

``Status.NOTHING_COMPARED`` fires only when the compared count reaches *zero*,
and zero is not how a corpus stops being checked. It stops one anchor at a time:
:func:`corpus_drift.anchor_refusal` matches ``sourceUri`` exactly, so a single
re-seed under ``git@github.com:theurian/theurian.git`` -- or under the same URL
without the ``.git`` suffix -- retires that item for good, reported as one
``::notice`` on a job that is advisory by design. Reproduced before the fix at 1
compared and 25 uncheckable: status ``CLEAN``, exit 0.

Two functions close that, and they are split because they answer different
questions.

:func:`corpus_drift.minimum_compared_for` decides **which floor a tree is held
to**. ``MINIMUM_COMPARED`` is a measurement of *this* repository's corpus, so it
binds on this tree and on no other: asserting 26 against a fixture would be
inventing a number, and it would take every small-corpus caller of
:func:`corpus_drift.main` -- the one- and two-migration corpora
``tests/integration/tools/test_corpus_drift_cli.py`` drives the CLI with -- to
exit 2. The suite's ``scan`` callers are *not* among them, and naming them here
would misdescribe where the floor binds: it is applied in ``main`` and never
inside :func:`corpus_drift.scan`, so a corpus of one compared anchor stays an
ordinary input to ``scan`` whatever floor this tree is held to. Another tree
states its own floor with ``--minimum-compared``.

:func:`corpus_drift.held_to_floor` decides **what a breach does to the report**,
and it outranks drift. ``--advisory`` turns drift into exit 0; exit 2 is the code
it does not touch. Without that ordering, a run that found one drifted anchor and
lost the other twenty-five to uncheckability comes back as a finding *and* a
green tick, which is a worse outcome than either alone.

The comparisons survive the hold, and that is the half a "return an empty
report" implementation would quietly lose: the ``::notice`` naming each anchor
that stopped being comparable is the only thing that tells a maintainer which
ones to restore.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from corpus_drift import (
    REMEDY,
    REPO_ROOT,
    Comparison,
    Report,
    Status,
    Verdict,
    held_to_floor,
    minimum_compared_for,
    render_github,
    render_text,
)

pytestmark = pytest.mark.unit

_MIGRATION = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6A-adr-0001-example.yaml"
_DOCUMENT = "docs/adr/0001-example.md"

#: Any tree that is not this checkout: a clone, a fixture, a container mount.
#: Never touched -- ``minimum_compared_for`` is path arithmetic and reads nothing.
_ANOTHER_TREE = Path("/elsewhere/a-clone-of-this-repository")


def _matched(index: int = 0) -> Comparison:
    return Comparison(
        migration=f".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW{index:02d}-adr.yaml",
        item_id=f"architecture.example-{index}",
        revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
        file_path=f"docs/adr/{index:04d}-example.md",
        verdict=Verdict.MATCHED,
        expected="a" * 64,
        actual="a" * 64,
        detail=f"docs/adr/{index:04d}-example.md still hashes to aaaaaaaaaaaa",
    )


def _uncheckable(index: int) -> Comparison:
    return Comparison(
        migration=f".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW{index:02d}-adr.yaml",
        item_id=f"architecture.example-{index}",
        revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
        file_path=f"docs/adr/{index:04d}-example.md",
        verdict=Verdict.UNCHECKABLE,
        detail=(
            "names sourceUri 'git@github.com:theurian/theurian.git', not "
            "'https://github.com/theurian/theurian.git', so the file it means is not in "
            "this checkout"
        ),
    )


_DRIFTED = Comparison(
    migration=_MIGRATION,
    item_id="architecture.example",
    revision_id="01MB4V3XKQ7ZPYE8R2NGT5HW6B",
    file_path=_DOCUMENT,
    verdict=Verdict.DRIFTED,
    expected="a" * 64,
    actual="b" * 64,
    detail=f"{_DOCUMENT} now hashes to bbbbbbbbbbbb, and the corpus pins aaaaaaaaaaaa",
)


#: One sentence per route :func:`corpus_drift.scan` reaches
#: ``Status.NOTHING_COMPARED`` by. Paraphrased rather than imported on purpose:
#: what the floor must not do is overwrite *whichever* sentence arrived with the
#: status, so a test that only held ``scan``'s current wording would go quiet the
#: day that wording is reworded. A fragment of each sentence is asserted where
#: it is produced, in ``tests/integration/tools/test_corpus_drift_scan.py``;
#: no test holds any of the three verbatim.
_GIT_WOULD_NOT_ANSWER = (
    "`git ls-files --cached` did not answer in /elsewhere/not-a-working-copy, and there is no "
    "filesystem fallback on purpose."
)
_CORPUS_IS_GONE = (
    "nothing tracked under .theurian/migrations/. The committed corpus is gone -- which is a "
    "finding, not a reason for this check to report drift-free."
)
_EVERY_ANCHOR_UNCHECKABLE = (
    "all 25 anchor(s) across 25 committed migration(s) were uncheckable, so this run compared "
    "nothing and proves nothing."
)


# -- which floor a tree is held to -------------------------------------------


def test_this_repositorys_own_tree_is_held_to_the_floor_its_corpus_was_measured_at() -> None:
    """26 anchors ship here, so a run that compares fewer has stopped covering the corpus.

    The number is pinned as a literal rather than read back from
    ``MINIMUM_COMPARED``, because it is a *measurement* (2026-08-22 at 64e33da:
    26 revisions, 26 anchors, 26 compared, 0 uncheckable) and not an arbitrary
    constant. Raising it is a decision that has to be taken against a re-measured
    corpus -- it is a lower bound, so growing the corpus does not require it --
    and lowering it is how a maintainer would silence this check without saying
    so.
    """
    assert minimum_compared_for(REPO_ROOT, None) == 26


def test_a_tree_that_is_not_this_repository_is_held_to_no_floor_it_never_measured() -> None:
    """26 was measured here and nowhere else; asserting it elsewhere invents a number.

    This is what keeps ``--repo-root`` usable at all. Every synthetic corpus the
    CLI tests in ``tests/integration/tools/test_corpus_drift_cli.py`` build holds
    one or two migrations, and a floor of 26 applied to whatever tree it is
    handed would take all of them -- and any downstream project vendoring this
    script -- straight to exit 2.
    """
    assert minimum_compared_for(_ANOTHER_TREE, None) == 0


def test_an_explicit_floor_replaces_the_one_this_repository_would_inherit() -> None:
    """`--minimum-compared` is how any tree states the floor it can actually meet.

    Asserted against ``REPO_ROOT`` on purpose: the flag has to beat the default
    where the default is non-zero, which is the only place the two can disagree.
    """
    assert minimum_compared_for(REPO_ROOT, 2) == 2


def test_a_requested_floor_of_zero_turns_the_floor_off_rather_than_reading_as_absent() -> None:
    """`--minimum-compared 0` is a stated decision, and `0` is falsy.

    The driving case for testing ``requested is not None`` rather than
    ``requested``: under a truthiness check this returns 26, so an operator who
    disabled the floor on this tree -- mid-re-seed, say -- gets exit 2 and no
    indication that the flag they passed was ignored.
    """
    assert minimum_compared_for(REPO_ROOT, 0) == 0


# -- a run that already said why it compared nothing --------------------------


@pytest.mark.parametrize(
    ("comparisons", "detail"),
    [
        pytest.param((), _GIT_WOULD_NOT_ANSWER, id="git-would-not-answer"),
        pytest.param((), _CORPUS_IS_GONE, id="corpus-is-gone"),
        pytest.param(
            tuple(_uncheckable(index) for index in range(1, 26)),
            _EVERY_ANCHOR_UNCHECKABLE,
            id="every-anchor-uncheckable",
        ),
    ],
)
def test_a_report_that_already_said_why_it_compared_nothing_keeps_that_sentence(
    comparisons: tuple[Comparison, ...], detail: str
) -> None:
    """The floor may only overturn a verdict, never talk over a stated diagnosis.

    Zero compared anchors is *below* any floor above zero, so the arithmetic
    alone fires on all three of these -- and replaces the one sentence that says
    which failure a maintainer is looking at with text written for a different
    one. That text offers "restore them, or lower the floor" as the remedy for a
    git that would not answer, and promises "every anchor that stopped being
    comparable is named in this report" over the two routes that name none
    because there are none. The exit status is 2 either way (see
    ``tests/unit/tools/test_corpus_drift_exit_codes.py``), so the diagnosis is
    the entire difference between the three.

    The input class the rest of this module never reaches: every other report
    built here arrives ``CLEAN`` or ``DRIFTED``, which is exactly the class the
    floor is *supposed* to overturn.
    """
    report = Report(comparisons, Status.NOTHING_COMPARED, detail)

    held = held_to_floor(report, 26)

    assert held.detail == detail
    assert held.status is Status.NOTHING_COMPARED
    assert held == report


# -- what a breach does to the report ----------------------------------------


def test_a_run_that_compared_fewer_anchors_than_its_floor_is_not_a_verdict() -> None:
    """One healthy anchor out of a corpus of twenty-six is not a clean corpus.

    The status is what ``exit_code`` reads, and ``NOTHING_COMPARED`` is the one
    outcome ``--advisory`` cannot downgrade. Both numbers are in the detail
    because a maintainer reading the job log needs to know how far the count has
    fallen, not just that it did.
    """
    report = Report((_matched(),), Status.CLEAN, "no drift -- compared 1 anchor(s).")

    held = held_to_floor(report, 26)

    assert held.status is Status.NOTHING_COMPARED
    assert "compared 1 anchor(s), fewer than the 26" in held.detail


def test_an_uncheckable_anchor_does_not_count_toward_the_floor() -> None:
    """The exact shape reproduced before the fix: 1 compared, 25 uncheckable, exit 0.

    Counting the anchors the run *reached* rather than the ones it *compared*
    puts the floor back where it started, because an anchor going uncheckable
    does not remove it from the corpus -- it removes it from the evidence. The
    26 comparisons here clear a naive `len(report.comparisons) >= 26`.
    """
    report = Report(
        (_matched(), *(_uncheckable(index) for index in range(1, 26))),
        Status.CLEAN,
        "no drift -- compared 1 anchor(s) across 26 committed migration(s); 25 uncheckable.",
    )

    held = held_to_floor(report, 26)

    assert len(report.comparisons) == 26
    assert held.status is Status.NOTHING_COMPARED


def test_a_run_that_compared_exactly_its_floor_is_left_alone() -> None:
    """The boundary is `>=`, not `>`: 26 of 26 is the corpus in full health.

    Off by one here turns every green CI run into exit 2, which is the failure
    mode a floor has to be trusted not to have before anyone will leave it
    enabled.
    """
    report = Report(
        tuple(_matched(index) for index in range(26)),
        Status.CLEAN,
        "no drift -- compared 26 anchor(s).",
    )

    assert held_to_floor(report, 26) == report


def test_a_run_that_cleared_its_floor_keeps_the_drift_verdict_it_earned() -> None:
    """The floor must not overwrite an ordinary finding, only an untrustworthy run.

    Without this, `always NOTHING_COMPARED` satisfies the tests above and turns
    the advisory job into a build failure on every edited ADR -- the precise
    outcome `--advisory` exists to prevent.
    """
    report = Report((_DRIFTED,), Status.DRIFTED, "1 drifted -- compared 1 anchor(s).")

    assert held_to_floor(report, 1) == report


def test_a_run_held_to_the_floor_still_reports_every_comparison_it_made() -> None:
    """A breach changes the verdict, never the evidence.

    The comparisons are what the annotations, the summary table and the remedy
    are rendered from, so an implementation that returned an empty report would
    tell a maintainer that the check has gone quiet while withholding both the
    drift it found and the names of the anchors that stopped being comparable --
    which is the list they have to work from to restore it.
    """
    report = Report((_DRIFTED, _uncheckable(2)), Status.DRIFTED, "1 drifted -- compared 1.")

    held = held_to_floor(report, 26)

    assert held.comparisons == report.comparisons
    printed = render_text(held)
    assert f"DRIFT  architecture.example: {_DOCUMENT} now hashes to" in printed
    assert REMEDY in printed


def test_a_breach_is_annotated_as_an_error_beside_the_drift_it_found() -> None:
    """CI reads annotations, and the floor's own reason has to be one of them.

    The drift stays a `::warning` -- the job is advisory about drift -- and the
    breach adds the `::error` that no flag downgrades. Emitting only one of the
    two loses either the finding or the reason the run cannot be trusted.
    """
    held = held_to_floor(
        Report((_DRIFTED, _uncheckable(2)), Status.DRIFTED, "1 drifted -- compared 1."),
        26,
    )

    commands = render_github(held)

    assert commands[0].startswith(f"::warning file={_DOCUMENT},title=Corpus drift::")
    assert commands[-1].startswith("::error ")
    assert "fewer than the 26 this corpus is held to" in commands[-1]
