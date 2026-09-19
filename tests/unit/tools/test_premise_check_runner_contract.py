"""No premise-sweep library function defaults its runner, and none swaps a
falsy one for the real subprocess -- the #729 defect class, restated as AC5.

`runner=None` reading as "use the real subprocess" is exactly the shape #729
was filed over. `Runner`'s own comment in `tools/premise_check.py` states
both halves of the contract: it is "required everywhere it is used" and
"only `main` constructs the real one." This file pins the signature (no
default) and the behaviour (a runner that merely evaluates falsy is still
the one actually called, never silently replaced).
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence

import premise_check
import premise_verify
import pytest

pytestmark = pytest.mark.unit


def test_fetch_and_check_require_their_runner_with_no_default() -> None:
    fetch_runner = inspect.signature(premise_check.fetch).parameters["runner"]
    check_runner = inspect.signature(premise_check.check).parameters["runner"]

    assert fetch_runner.default is inspect.Parameter.empty
    assert check_runner.default is inspect.Parameter.empty


class _FalsyRunner:
    """Callable, but `bool(...)` is `False` -- exactly what `runner or run_command`
    would treat as "no runner given".
    """

    def __init__(self, script: dict[tuple[str, ...], premise_verify.CommandResult]) -> None:
        self._script = script
        self.calls: list[tuple[str, ...]] = []

    def __bool__(self) -> bool:
        return False

    def __call__(self, argv: Sequence[str]) -> premise_verify.CommandResult:
        key = tuple(argv)
        self.calls.append(key)
        return self._script[key]


def test_a_falsy_but_callable_runner_is_the_one_check_actually_uses() -> None:
    """Had `check` (or anything it calls) read a falsy runner as "none given"
    and substituted `run_command`, this checkout's real `git` would answer
    instead of the fixture below -- silently, since a real `git rev-parse
    HEAD` and a real `git ls-tree` both succeed in this checkout too. The
    only witness to that substitution is whether the fixture was ever
    called at all.
    """
    runner = _FalsyRunner(
        {
            ("git", "rev-parse", "HEAD"): premise_verify.CommandResult(0, "deadbeefcafe\n", ""),
            ("git", "ls-tree", "-r", "--name-only", "HEAD"): premise_verify.CommandResult(
                0, "", ""
            ),
        }
    )

    report = premise_check.check(premise_check.Snapshot(issues=()), runner, "snap.json")

    assert runner.calls == [
        ("git", "rev-parse", "HEAD"),
        ("git", "ls-tree", "-r", "--name-only", "HEAD"),
    ]
    assert report.head_commit == "deadbeefcafe"


def test_a_falsy_but_callable_runner_is_the_one_fetch_actually_uses() -> None:
    issue_argv = (
        "gh",
        "issue",
        "list",
        "--state",
        "open",
        "--limit",
        str(premise_check.FETCH_LIMIT),
        "--json",
        "number,title,createdAt,labels,body,comments",
    )
    pr_argv = (
        "gh",
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        str(premise_check.PR_FETCH_LIMIT),
        "--json",
        "number,state",
    )
    runner = _FalsyRunner(
        {
            issue_argv: premise_verify.CommandResult(0, "[]", ""),
            pr_argv: premise_verify.CommandResult(0, "[]", ""),
        }
    )

    snapshot = premise_check.fetch(runner, "gh")

    assert runner.calls == [issue_argv, pr_argv]
    assert snapshot.issues == ()
    assert snapshot.pr_states == ()
