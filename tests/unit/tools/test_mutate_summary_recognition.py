"""``_recognised_summary`` is the guard that closes issue #50.

Issue #50: mutation ``C7`` came back KILLED under ``--workers 4`` with a
summary of ``......[100%]`` and no ``FAILED`` line anywhere -- a run truncated
under parallelism, not a suite that went red. A rerun alone reported SURVIVED.
``_run_one`` now checks this before trusting the exit code, so a truncated
run is reported ``ERROR`` instead of silently becoming a false KILLED (or, in
principle, a false SURVIVED). These tests pin the boundary the check draws.
"""

from __future__ import annotations

import pytest
from mutate_run import _recognised_summary

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "summary",
    [
        "1493 passed, 1 xfailed in 130.45s",
        "3 failed, 42 passed in 5.67s",
        "3 errors in 0.12s",
        "2 failed in 0.03s",
        "1 passed in 0.01s",
    ],
)
def test_a_genuine_pytest_summary_line_is_recognised(summary: str) -> None:
    """pytest's own final line always names what it counted."""
    assert _recognised_summary(summary) is True


def test_the_exact_truncated_line_from_issue_50_is_not_recognised() -> None:
    """The literal reproduction: a progress marker, not pytest's own summary."""
    assert _recognised_summary("......[100%]") is False


def test_no_output_at_all_is_not_recognised() -> None:
    """``_summarise`` reports this literal string when stdout is empty."""
    assert _recognised_summary("(no output)") is False
