"""HIGH-2 fix 4: an exception the harness did not anticipate must exit 2.

Before this fix, ``main`` only caught ``HarnessError``. Anything else --
reachable at the time from a non-dict ``edits`` element or a non-UTF-8
second-edit file, both now converted to ``HarnessError`` upstream, but in
principle any exception type this harness has not anticipated yet -- fell
through to Python's default handling: a traceback on stderr and exit code 1,
indistinguishable from the documented "at least one survived".
"""

from __future__ import annotations

import mutate
import pytest
from mutate_edits import HarnessError

pytestmark = pytest.mark.integration


def test_an_unanticipated_exception_from_verdict_mode_exits_two_not_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-``HarnessError`` reaching ``main`` must still exit 2.

    Replaces ``_verdict_mode`` with something that raises a plain
    ``TypeError`` -- unrelated to any of the exception types this PR already
    converts to ``HarnessError`` upstream -- to prove the *last resort* net
    catches it too, not just the two known causes.

    Deliberately not ``ValueError``: narrowing ``except Exception`` to
    ``except ValueError`` would still catch a ``ValueError`` here and this
    test would stay green, silently reopening HIGH-2 for every other
    exception type. ``TypeError`` (or a bare ``Exception``) is what actually
    exercises the *unnarrowed* net.
    """

    def _boom(args: object, options: object) -> int:
        raise TypeError("an exception the harness did not anticipate")

    monkeypatch.setattr(mutate, "_verdict_mode", _boom)

    exit_code = mutate.main(["--file", "tools/mutate.py", "--old", "x", "--new", "y"])

    assert exit_code == 2


def test_a_harnesserror_from_verdict_mode_still_exits_two_with_its_own_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard: the last-resort net must not swallow the specific path.

    ``HarnessError`` keeps its own short ``error: {message}`` line rather
    than falling into the generic "did not anticipate" branch and its
    traceback.
    """

    def _boom(args: object, options: object) -> int:
        raise HarnessError("a specific, expected failure")

    monkeypatch.setattr(mutate, "_verdict_mode", _boom)

    exit_code = mutate.main(["--file", "tools/mutate.py", "--old", "x", "--new", "y"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "error: a specific, expected failure" in captured.err
    assert "did not anticipate" not in captured.err
