"""A sweep that did not really run must never read clean (#378).

This is the whole reason the nightly job is worth having. A red-team sweep whose
failure mode is "quietly reports nothing" is worse than no sweep: it converts an
absence of evidence into evidence of absence, nightly, in a place nobody looks.

So "clean" is defined positively and narrowly, and everything else files. A night
is clean only when the harness exited 0, its unmutated control came back GREEN,
and the results record carries a KILLED verdict for every mutation that was
submitted. Each of the other shapes below -- a missing control, an empty outcome
list, a results file that disagrees with the exit code -- reads exactly like a
clean night in the summary line, and each of them must file instead.

``tools/mutate.py``'s own exit codes are the input: 0 every mutation killed, 1 at
least one survived or hung, 2 the run cannot be trusted at all.
"""

from __future__ import annotations

import json

import mutate
import pytest
import sweep_census
import sweep_verdict

pytestmark = pytest.mark.unit


def _outcome(label: str, verdict: str, seconds: float = 12.5) -> dict[str, object]:
    return {"label": label, "verdict": verdict, "seconds": seconds, "summary": f"{verdict} line"}


def _document(*outcomes: dict[str, object]) -> str:
    return json.dumps({"options": {"workers": 2}, "outcomes": list(outcomes)})


_GREEN_CONTROL = _outcome("__control__", "control-green")


def test_the_control_label_is_the_one_the_harness_actually_writes() -> None:
    """A seam between two tools, pinned rather than assumed.

    The sweep tells the control apart from the mutations by label. If
    ``tools/mutate.py`` renamed its control, every outcome would read as a
    mutation, the "exactly one control-green" rule would never be satisfied, and
    every night would file a run-untrusted issue -- a failure that is at least
    loud. The reverse drift is the dangerous one: a sweep looking for a label
    nothing writes can never see a control-red.
    """
    assert sweep_verdict.CONTROL_LABEL == mutate._CONTROL_LABEL


def test_the_untrusted_exit_code_is_the_one_the_harness_actually_returns() -> None:
    """The other half of the seam, taken from the harness rather than from its docs.

    The sweep branches on exit 2 in two places -- whether a missing results file
    is expected, and whether the night files a run-untrusted issue. If the
    harness ever returned something else for an untrusted run, both branches
    would silently take the trusted path and a batch that proved nothing would be
    read as a batch that proved everything.

    Driven through ``mutate.main`` with no arguments at all, which is the
    cheapest untrusted run there is: it refuses before building a tree, running
    a suite or touching the checkout.
    """
    assert mutate.main([]) == sweep_verdict.UNTRUSTED_EXIT


def test_a_full_house_of_killed_mutations_over_a_green_control_is_clean() -> None:
    """AC3's precondition: the one shape that is allowed to file nothing.

    Every element is load-bearing and each is removed in a test below: the exit
    code, the control's presence, the control's verdict, the count, and every
    individual verdict.
    """
    outcomes = sweep_verdict.read_results(
        _document(_GREEN_CONTROL, _outcome("m0", "KILLED"), _outcome("m1", "KILLED"))
    )

    reading = sweep_verdict.classify(mutate_exit=0, outcomes=outcomes, submitted=2)

    assert reading.reason is None


@pytest.mark.parametrize(
    ("verdict", "exit_code"),
    [("SURVIVED", 1), ("HUNG", 1)],
)
def test_a_mutation_the_suite_does_not_hold_is_a_finding(verdict: str, exit_code: int) -> None:
    """SURVIVED and HUNG are one class here, as they are in the harness.

    A suite that never finishes under a mutation cannot go RED for it, so it
    holds exactly as little as a suite that finished green. Reading HUNG as
    "inconclusive, try again" is how a real gap survives a nightly job for
    months.
    """
    outcomes = sweep_verdict.read_results(
        _document(_GREEN_CONTROL, _outcome("m0", "KILLED"), _outcome("m1", verdict))
    )

    reading = sweep_verdict.classify(mutate_exit=exit_code, outcomes=outcomes, submitted=2)

    assert reading.reason == sweep_verdict.SURVIVORS
    assert reading.unheld == ("m1",)


def test_a_survivor_in_an_untrusted_batch_is_not_a_finding_about_the_suite() -> None:
    """Exit 2 voids a SURVIVED exactly as it voids a KILLED.

    The survivors branch guards on the exit code, and nothing reached that guard:
    the code review perturbed the whole condition to a bare ``if unheld:`` and
    every test stayed green, because no case here paired an unheld verdict with
    an untrusted run. A SURVIVED under exit 2 came from a batch whose own harness
    says it proves nothing -- a restore that did not restore, a mutation that
    reached the real checkout -- and filing it as a gap in the suite sends
    somebody to write a test for a mutation that may never have been applied.
    """
    outcomes = sweep_verdict.read_results(
        _document(_GREEN_CONTROL, _outcome("m0", "SURVIVED"), _outcome("m1", "KILLED"))
    )

    reading = sweep_verdict.classify(mutate_exit=2, outcomes=outcomes, submitted=2)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_a_survivor_over_a_red_control_is_not_a_finding_about_the_suite() -> None:
    """A tree that was already RED cannot say which mutations it holds.

    With no baseline, a mutation's SURVIVED means only that the suite failed the
    same way with the mutation as without it -- which is not evidence that
    nothing holds the property. This is the guard the review found unpinned by
    dropping ``green_control`` from the condition.
    """
    outcomes = sweep_verdict.read_results(
        _document(_outcome("__control__", "control-red"), _outcome("m0", "SURVIVED"))
    )

    reading = sweep_verdict.classify(mutate_exit=1, outcomes=outcomes, submitted=1)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_a_survivor_in_an_incomplete_batch_is_not_a_finding_about_the_suite() -> None:
    """One verdict for six mutations: five questions went unasked.

    The one that did come back is not thereby trustworthy -- a batch that lost
    five jobs lost them for a reason, and the surviving record is a fragment of a
    run nobody can account for. Filing the fragment as a finding also hides the
    loss, because the issue reads like an ordinary night.
    """
    outcomes = sweep_verdict.read_results(_document(_GREEN_CONTROL, _outcome("m0", "SURVIVED")))

    reading = sweep_verdict.classify(mutate_exit=1, outcomes=outcomes, submitted=6)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_an_untrusted_harness_run_files_even_though_no_mutation_survived() -> None:
    """AC5. Exit 2 means every verdict in the batch is worthless, including KILLED.

    ``tools/mutate.py`` exits 2 for an anchor that did not match, a restore that
    did not restore, a control that was not green, or a mutation that reached the
    real checkout. In every one of those the KILLED verdicts above it prove
    nothing -- and a night that swallowed them would report the strongest
    possible result from the weakest possible run.
    """
    outcomes = sweep_verdict.read_results(_document(_GREEN_CONTROL, _outcome("m0", "KILLED")))

    reading = sweep_verdict.classify(mutate_exit=2, outcomes=outcomes, submitted=1)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_a_run_that_produced_no_mutation_verdict_at_all_is_not_a_clean_night() -> None:
    """The silent stop: a harness that exits 0 having run nothing.

    An empty outcome list satisfies "no mutation survived" vacuously, and that is
    precisely the reading this job must never produce. The submitted count is
    what makes the claim positive: six mutations were handed over, so six
    verdicts have to come back.
    """
    outcomes = sweep_verdict.read_results(_document())

    reading = sweep_verdict.classify(mutate_exit=0, outcomes=outcomes, submitted=6)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_a_night_that_submitted_nothing_is_not_a_clean_night() -> None:
    """A green control and no mutations at all satisfies every other rule vacuously.

    This is the strongest form of the silent stop, and the only shape that
    reaches the ``submitted > 0`` guard: the control ran and passed, ``0 == 0``
    mutations came back, all zero of them were killed, and the exit code agrees.
    An empty outcome list does *not* reach it -- the missing control catches that
    one first -- so a check written against the empty list would leave this
    exact reading green.
    """
    outcomes = sweep_verdict.read_results(_document(_GREEN_CONTROL))

    reading = sweep_verdict.classify(mutate_exit=0, outcomes=outcomes, submitted=0)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_a_batch_missing_its_control_is_not_a_clean_night() -> None:
    """Without the control, every KILLED below it is meaningless -- mutate.py's words.

    A tree that was already RED kills every mutation for free. The harness says
    so in its own summary; the sweep has to refuse to read a batch that never
    established a baseline, because "all KILLED" is exactly what a broken tree
    produces.
    """
    outcomes = sweep_verdict.read_results(_document(_outcome("m0", "KILLED")))

    reading = sweep_verdict.classify(mutate_exit=0, outcomes=outcomes, submitted=1)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_a_red_control_is_not_a_clean_night() -> None:
    """The baseline failed, so the batch measured the tree and not the mutations."""
    outcomes = sweep_verdict.read_results(
        _document(_outcome("__control__", "control-red"), _outcome("m0", "KILLED"))
    )

    reading = sweep_verdict.classify(mutate_exit=0, outcomes=outcomes, submitted=1)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_fewer_verdicts_than_mutations_submitted_is_not_a_clean_night() -> None:
    """Five verdicts for six mutations means one question went unasked.

    Nothing in the exit code says so: a batch that lost a job still exits 0 if
    everything it did report was killed. The count is the only witness.
    """
    outcomes = sweep_verdict.read_results(
        _document(_GREEN_CONTROL, *(_outcome(f"m{index}", "KILLED") for index in range(5)))
    )

    reading = sweep_verdict.classify(mutate_exit=0, outcomes=outcomes, submitted=6)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_an_exit_code_that_disagrees_with_the_record_is_not_a_clean_night() -> None:
    """Exit 1 over an all-KILLED record: one of the two is wrong, so neither is trusted.

    This is the shape a partially written results file takes -- the harness
    persists after every result, so a batch killed mid-run leaves a document that
    is internally consistent and incomplete. Trusting the record over the exit
    code would turn a killed job into a clean night.
    """
    outcomes = sweep_verdict.read_results(_document(_GREEN_CONTROL, _outcome("m0", "KILLED")))

    reading = sweep_verdict.classify(mutate_exit=1, outcomes=outcomes, submitted=1)

    assert reading.reason == sweep_verdict.UNTRUSTED


def test_an_untrusted_run_that_wrote_no_results_at_all_still_files() -> None:
    """Exit 2 can happen before a single verdict is written.

    A refused anchor or a failed tree build raises before any suite runs, so
    there is no document to read. The night still has to say something, and
    "nothing to report" is the one thing it must not say.
    """
    reading = sweep_verdict.classify(mutate_exit=2, outcomes=(), submitted=6)

    assert reading.reason == sweep_verdict.UNTRUSTED


@pytest.mark.parametrize(
    "document",
    [
        "not json at all",
        '{"outcomes": "not a list"}',
        '["the old bare list format"]',
        '{"outcomes": [{"label": "m0"}]}',
        '{"outcomes": [{"verdict": "KILLED"}]}',
    ],
)
def test_a_results_document_that_cannot_be_read_stops_the_sweep(document: str) -> None:
    """An unreadable record is not an empty one.

    Returning "no outcomes" here would route straight into the untrusted path,
    which files an issue -- and filing a red-team finding because a JSON file was
    truncated wastes a triage slot on a broken job. The driver exits 1 instead,
    which is the signal the workflow alarms on.

    The bare-list case is the shape ``tools/mutate.py`` wrote before #566 moved
    the verdicts under an ``outcomes`` key; a reader that silently accepted it
    would be reading a document whose options block -- including ``deselect`` --
    it cannot see. The last two cases drop one required field each: a defaulted
    label makes an outcome indistinguishable from a mutation the sweep never
    submitted, and a defaulted verdict reads as "not KILLED" whatever it was.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_verdict.read_results(document)
