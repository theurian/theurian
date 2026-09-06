"""A run killed by the clock must say so, not show its last progress line (#566).

``_summarise`` takes pytest's final line, and a suite killed mid-walk has not
printed one -- what it leaves behind is a progress marker. Reported under a
verdict with no other explanation, a control that merely ran out of time read at
a glance exactly like a baseline that had gone RED, and the two have opposite
remedies: a red tree needs fixing, a timed-out one needs more seconds or fewer
workers. PR #581's adversarial round spent roughly 90 minutes on that confusion.

An empty ``failures`` is the discriminator. A run that really did go red before
the clock ran out has a ``FAILED``/``ERROR`` line, and that line is the thing
worth reading, so it must not be displaced by the timeout note.
"""

from __future__ import annotations

import pathlib

import pytest
from mutate_run import SuiteHungError, _hung_summary

pytestmark = pytest.mark.unit

#: Never opened -- ``_hung_summary`` reads the exception, not the filesystem.
_TREE = pathlib.Path("theurian-mutate-tree")


def test_a_timeout_with_nothing_failing_is_reported_as_a_timeout() -> None:
    """The exact shape that read as a red baseline: a progress marker, no failure.

    ``......[ 43%]`` is what a green walk that ran out of clock leaves behind.
    Printed alone it is indistinguishable from a truncated red run, which is
    what made a whole batch of HUNG verdicts look like findings.
    """
    hung = SuiteHungError(1800, _TREE, output="collected 5498 items\n......[ 43%]\n")

    summary, failures = _hung_summary(hung)

    assert "timed out after 1800s" in summary
    assert "no failing test" in summary
    assert failures == ()


def test_the_timeout_line_still_carries_what_the_suite_had_printed() -> None:
    """Re-obtaining a hang costs the whole timeout, so the partial output stays.

    Dropping it to make room for the timeout note would trade one expensive
    loss for another: the last line is the only evidence of how far the walk
    got before it was killed.
    """
    hung = SuiteHungError(2820, _TREE, output="collected 5498 items\n......[ 43%]\n")

    summary, _ = _hung_summary(hung)

    assert "......[ 43%]" in summary


def test_a_run_that_failed_before_the_clock_ran_out_reports_the_failure() -> None:
    """A real failure must not be relabelled as a timeout.

    The note exists to explain an *absent* failure. A suite that went red and
    then hung has a name worth reading, and burying it under "no failing test"
    would be a false statement about the run rather than a clarifying one.
    """
    output = "FAILED tests/e2e/test_bare_install.py::test_it - OSError: port in use\n....\n"
    hung = SuiteHungError(1800, _TREE, output=output)

    summary, failures = _hung_summary(hung)

    assert "no failing test" not in summary
    assert failures == ("FAILED tests/e2e/test_bare_install.py::test_it - OSError: port in use",)


def test_a_suite_that_printed_nothing_at_all_still_names_the_timeout() -> None:
    """The pre-existing fallback: no output means the exception's own message.

    Kept as a separate case because it reaches a different branch, and that
    branch already said "did not finish within 1800s" before this change --
    so nothing here should have moved.
    """
    hung = SuiteHungError(1800, _TREE, output="")

    summary, failures = _hung_summary(hung)

    assert "did not finish within 1800s" in summary
    assert failures == ()
