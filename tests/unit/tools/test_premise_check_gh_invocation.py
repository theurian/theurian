"""How the issue-premise sweep talks to `gh`: read-only, and only during `fetch` (AC4).

`tools/premise_check.py`'s own module docstring states the fence as a claim
about code, not only as prose: "there is no code path here that constructs
`gh issue close`, `gh issue edit`, `gh issue comment`, or any other write."
This file checks it two ways -- the one call `fetch` actually makes, over an
injected runner, and a structural sweep over every `premise_*.py` module's
own literal argv tuples. The structural check walks the AST rather than
searching the text, because the docstring quoted above names the very verbs a
plain substring search would mistake for evidence of a real write call.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from pathlib import Path

import premise_check
import premise_citations
import premise_report
import premise_verify
import pytest

pytestmark = pytest.mark.unit

_WRITE_VERBS = frozenset(
    {"close", "reopen", "edit", "comment", "delete", "transfer", "lock", "unlock", "pin", "unpin"}
)


class _RecordingRunner:
    def __init__(self, result: premise_verify.CommandResult) -> None:
        self._result = result
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str]) -> premise_verify.CommandResult:
        self.calls.append(tuple(argv))
        return self._result


def test_fetch_issues_exactly_two_read_only_gh_calls_and_nothing_else() -> None:
    """Round 2 HIGH-3 added the PR-state map: `fetch` now makes exactly two
    `gh` calls, one `issue list` and one `pr list`, both read-only.
    """
    runner = _RecordingRunner(premise_verify.CommandResult(0, "[]", ""))

    premise_check.fetch(runner, "gh", repo="theurian/theurian")

    assert len(runner.calls) == 2
    for argv in runner.calls:
        assert argv[0] == "gh"
        assert not any(verb in argv for verb in _WRITE_VERBS)
    assert runner.calls[0][1:3] == ("issue", "list")
    assert runner.calls[1][1:3] == ("pr", "list")


def test_fetch_cli_summary_reports_both_issue_and_pr_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 3 LOW: the summary line used to name only the issue count,
    hiding that the second, PR-state read (round 2 HIGH-3) had happened at
    all.
    """

    def fake_run_command(argv: Sequence[str]) -> premise_verify.CommandResult:
        if tuple(argv)[1:3] == ("issue", "list"):
            return premise_verify.CommandResult(0, "[]", "")
        return premise_verify.CommandResult(0, json.dumps([{"number": 1, "state": "MERGED"}]), "")

    monkeypatch.setattr(premise_check, "run_command", fake_run_command)
    snapshot_path = tmp_path / "snap.json"

    exit_code = premise_check.main(["fetch", "--json", str(snapshot_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "0 open issue(s)" in out
    assert "1 pr state(s)" in out


def test_check_makes_no_gh_call_at_all() -> None:
    """The offline leg: `check` reads only `git` (module docstring, "no network
    call of any kind"). A `gh` call reaching the runner here would mean the
    grading leg quietly started talking to the network.
    """
    runner = _RecordingRunner(premise_verify.CommandResult(0, "", ""))
    snapshot = premise_check.Snapshot(issues=())

    premise_check.check(snapshot, runner, "snap.json")

    assert runner.calls  # the head-commit and tracked-paths reads still happen
    assert all(argv and argv[0] == "git" for argv in runner.calls)


def _string_constants(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.Tuple, ast.List)):
        return []
    return [
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def test_no_premise_module_source_ever_builds_an_issue_argv_carrying_a_write_verb() -> None:
    """A structural sweep, not a substring search.

    The module docstring itself quotes `gh issue close`, `gh issue edit` and
    `gh issue comment` by name, as the calls that must not exist -- so a
    plain `"close" not in source` check would trip over its own
    documentation. Walking the AST for a literal tuple or list that carries
    both `"issue"` and a write verb as direct elements sidesteps prose
    entirely: only a real argv-shaped literal can make this fail, regardless
    of whatever non-literal pieces (a variable, a `str(...)` call) sit
    alongside them in the same collection.
    """
    modules = (
        premise_check.__file__,
        premise_citations.__file__,
        premise_report.__file__,
        premise_verify.__file__,
    )

    for path in modules:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
        for node in ast.walk(tree):
            elements = _string_constants(node)
            if "issue" not in elements:
                continue
            offending = _WRITE_VERBS.intersection(elements)
            assert not offending, f"{path}: {elements} carries {offending}"
