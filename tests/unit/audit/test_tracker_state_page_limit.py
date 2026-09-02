"""A full page of tracker state is indistinguishable from a truncated one (sec-L2).

``tracker_state._live`` asks ``gh`` for every issue and every pull request at
``--limit 2000``. ``gh`` returns the newest ``--limit`` entries and reports no
truncation, so once the repository outgrows that number the table silently loses
its *oldest* numbers -- and ``is_open`` treats a number the table does not carry
as **not open**. Two of the five audits in ``tools/audit/`` decide a verdict from
exactly that answer: ``controls_discharge``'s dead-owner rule and
``owner_position_cites``'s ``open owner``. Both would stop reporting the
residuals they exist to find, and both would exit 0 while doing it.

That is the false-green direction, which is the one this repository's own
``tracker_state`` docstring says decides the design. A full page therefore
raises rather than returning a table nobody can trust.

``gh`` is faked here rather than called: this is a ``unit`` test, and reaching
the network would make the assertion depend on how many issues the project
happens to have today.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
import tracker_state

pytestmark = pytest.mark.unit


def _fake_gh(issue_count: int, pull_count: int) -> Callable[..., str]:
    """A ``_gh`` stand-in returning the requested number of entries per list call."""

    def gh(*arguments: str) -> str:
        count = issue_count if arguments[0] == "issue" else pull_count
        return json.dumps([{"number": number, "state": "CLOSED"} for number in range(1, count + 1)])

    return gh


def test_a_page_short_of_the_limit_is_read_as_the_whole_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED means the guard fires on a tracker that fits, and every audit stops running.

    The bound is the interesting half of the pair: a repository whose issue count
    is one below the page size is complete, and refusing it would take all five
    audits offline for a state that is fine.
    """
    monkeypatch.setattr(tracker_state, "_gh", _fake_gh(tracker_state._PAGE - 1, 3))

    table = tracker_state._live()

    assert table is not None
    assert len(table) == tracker_state._PAGE - 1


def test_a_full_page_of_issues_refuses_rather_than_returning_a_truncated_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED means a truncated tracker table reaches the audits and clears dead owners.

    A number missing from the table is ``not open``, so a cite of an old closed
    issue in owner position would read as ``unmarked`` and a control owed to an
    old number would read as discharged -- silently, on an audit that exits 0.
    """
    monkeypatch.setattr(tracker_state, "_gh", _fake_gh(tracker_state._PAGE, 3))

    with pytest.raises(RuntimeError, match="the page is full"):
        tracker_state._live()


def test_a_full_page_of_pull_requests_refuses_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """RED means the guard covers one of the two list calls, which is the shape a
    reader assumes it does not have.

    Pull requests outnumber issues on this repository, so the pull-request call is
    the one that reaches the page first. A guard written only for the issue call
    would look present and cover the wrong list.
    """
    monkeypatch.setattr(tracker_state, "_gh", _fake_gh(3, tracker_state._PAGE))

    with pytest.raises(RuntimeError, match="the page is full"):
        tracker_state._live()
