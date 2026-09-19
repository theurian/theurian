"""The verdict grader: PREMISE-HOLDS is the narrow case, everything else is
NEEDS-AGENT, and the module never emits a third value (AC3).

`_reasons` decides which of the five `needs_agent_reasons` fires, and
`_check_issue`'s ternary is the only place a citation's grading becomes the
verdict a triager reads. The module's own docstring is explicit that it never
emits `PREMISE-MOOT` or `PREMISE-CHANGED` -- naming the commit that mooted an
issue is judgement, and this instrument only verifies -- so that boundary is
pinned here as a property of the code, not as an absent substring (the module
docstring itself names both strings, as the calls it does not make).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import premise_check
import premise_verify
import pytest

pytestmark = pytest.mark.unit


def _citation(
    status: str, kind: str = "path", value: str = "tools/x.py"
) -> premise_check.CitationResult:
    return premise_check.CitationResult(kind, value, "git ...", "", "", status)


def _commit(sha: str = "abc1234") -> premise_check.TouchingCommit:
    return premise_check.TouchingCommit(sha, "a later commit")


def test_no_citations_at_all_reads_no_citations() -> None:
    reasons = premise_check._reasons((), (), log_failed=False)

    assert reasons == (premise_check.NO_CITATIONS,)


def test_a_single_intact_citation_and_no_touching_commit_has_no_reason_to_flag() -> None:
    reasons = premise_check._reasons((_citation(premise_verify.INTACT),), (), log_failed=False)

    assert reasons == ()


def test_a_dangling_citation_among_others_reads_dangling_citation() -> None:
    citations = (
        _citation(premise_verify.INTACT, value="a.py"),
        _citation(premise_verify.DANGLING, value="b.py"),
    )

    reasons = premise_check._reasons(citations, (), log_failed=False)

    assert reasons == (premise_check.DANGLING_CITATION,)


def test_an_unknown_citation_reads_unknown_citation() -> None:
    reasons = premise_check._reasons((_citation(premise_verify.UNKNOWN),), (), log_failed=False)

    assert reasons == (premise_check.UNKNOWN_CITATION,)


def test_a_touched_path_reads_surface_touched_even_with_every_citation_intact() -> None:
    reasons = premise_check._reasons(
        (_citation(premise_verify.INTACT),), (_commit(),), log_failed=False
    )

    assert reasons == (premise_check.SURFACE_TOUCHED,)


def test_a_citation_level_error_reads_check_error() -> None:
    reasons = premise_check._reasons((_citation(premise_verify.ERROR),), (), log_failed=False)

    assert reasons == (premise_check.CHECK_ERROR,)


def test_a_failed_touching_commit_log_reads_check_error_even_with_no_citations_at_all() -> None:
    """`log_failed` is carried separately from an empty touching-commit tuple
    (module docstring): a plumbing failure must not read the same as "nothing
    touched this file".
    """
    reasons = premise_check._reasons((), (), log_failed=True)

    assert premise_check.CHECK_ERROR in reasons


def test_an_unknown_issue_ref_citation_reads_reference_not_open() -> None:
    """A not-open cross-referenced issue is the most direct PREMISE-MOOT
    signal available offline (round 1, 16538d17); before that fix it graded
    only `unknown-citation` and sank into the same no-agent-spend tail as
    every other unresolved guess.
    """
    citations = (_citation(premise_verify.UNKNOWN, kind="issue_ref", value="#42"),)

    reasons = premise_check._reasons(citations, (), log_failed=False)

    assert premise_check.REFERENCE_NOT_OPEN in reasons


def test_an_unknown_non_issue_ref_citation_never_reads_reference_not_open() -> None:
    """The reason is specific to `issue_ref`'s own UNKNOWN status; a `constant`
    or `symbol` miss is `unknown-citation` only, and must not be mistaken for
    a not-open cross-reference it never was.
    """
    citations = (_citation(premise_verify.UNKNOWN, kind="constant", value="X"),)

    reasons = premise_check._reasons(citations, (), log_failed=False)

    assert premise_check.REFERENCE_NOT_OPEN not in reasons


def test_multiple_reasons_can_fire_at_once_and_each_appears_once() -> None:
    citations = (
        _citation(premise_verify.DANGLING),
        _citation(premise_verify.UNKNOWN, value="y.py"),
    )

    reasons = premise_check._reasons(citations, (_commit(),), log_failed=False)

    assert set(reasons) == {
        premise_check.DANGLING_CITATION,
        premise_check.UNKNOWN_CITATION,
        premise_check.SURFACE_TOUCHED,
    }


def test_premise_holds_only_when_every_condition_is_met_at_once() -> None:
    """The four AND'd conditions PREMISE-HOLDS requires (module docstring),
    exercised through the real ternary in `_check_issue` rather than a second
    implementation of the rule: one citation, INTACT, no touching commit, no
    UNKNOWN/ERROR.
    """
    issue = premise_check.IssueRecord(
        number=1,
        title="t",
        created_at="2026-01-01T00:00:00Z",
        labels=(),
        body="See tools/premise_check.py.",
        comments=(),
    )
    script: dict[tuple[str, ...], premise_verify.CommandResult] = {
        ("git", "cat-file", "-e", "HEAD:tools/premise_check.py"): premise_verify.CommandResult(
            0, "", ""
        ),
        (
            "git",
            "log",
            "--format=%H%x09%s",
            f"--since-as-filter={issue.created_at}",
            "HEAD",
            "--",
            "tools/premise_check.py",
        ): premise_verify.CommandResult(0, "", ""),
    }

    def runner(argv: Sequence[str]) -> premise_verify.CommandResult:
        return script[tuple(argv)]

    report_issue = premise_check._check_issue(issue, runner, frozenset(), {}, {})

    assert report_issue.machine_verdict == premise_check.HOLDS
    assert report_issue.needs_agent_reasons == ()


def test_check_issue_reports_needs_agent_through_the_real_ternary_not_a_reimplementation() -> None:
    issue = premise_check.IssueRecord(
        number=1, title="t", created_at="2026-01-01T00:00:00Z", labels=(), body="", comments=()
    )

    def runner(argv: Sequence[str]) -> premise_verify.CommandResult:
        raise AssertionError(f"no citation should reach the runner: {argv!r}")

    report_issue = premise_check._check_issue(issue, runner, frozenset(), {}, {})

    assert report_issue.machine_verdict == premise_check.NEEDS_AGENT
    assert report_issue.needs_agent_reasons == (premise_check.NO_CITATIONS,)


def test_holds_and_needs_agent_are_exactly_the_two_documented_verdict_strings() -> None:
    assert premise_check.HOLDS == "PREMISE-HOLDS"
    assert premise_check.NEEDS_AGENT == "NEEDS-AGENT"


def test_machine_verdict_is_assigned_at_exactly_one_call_site_as_the_two_way_ternary() -> None:
    """A structural pin rather than a substring search: the module's own
    docstring names `PREMISE-MOOT` and `PREMISE-CHANGED` explicitly, as the
    verdicts it does not emit, so `"PREMISE-MOOT" not in source` would trip
    over the module's own prose. What actually decides `machine_verdict` is
    this one line; a future branch adding a third verdict has to touch it.
    """
    source = Path(premise_check.__file__).read_text(encoding="utf-8")

    assignments = [
        line.strip().rstrip(",") for line in re.findall(r"machine_verdict=([^\n]+)", source)
    ]

    assert assignments == ["NEEDS_AGENT if reasons else HOLDS"]
