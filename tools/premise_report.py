"""The Markdown triage report :mod:`premise_check`'s ``check`` leg can write.

Split out of ``premise_check.py`` to keep that module under this repository's
line cap, and because rendering has a property verification does not: it is
an **injection surface**. Every string here that came off the tracker --
title, and anything a citation's ``token`` or ``command`` carries -- was
typed by whoever opened or commented on the issue, and this report is
read by a later agent pass. A title that closes a code fence early, or that
looks like a heading, must not be able to redirect what that pass reads as
the report's own structure rather than as quoted issue text.

So every issue-derived string renders inside a fenced block whose fence is
sized against the text it holds (:func:`_fence`, the same device
``tools/sweep_filing.py`` uses for the same reason -- a source line there can
carry three backticks and close a naive fence early) and under a label that
says, in the rendered output itself, that what follows is untrusted tracker
text and not part of this report's own prose.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from premise_check import CitationResult, IssueReport, Report

#: Mirrors ``premise_check.HOLDS`` as a literal rather than an import: that
#: module imports this one to call :func:`render`, and importing it back
#: would make the two modules mutually dependent at load time.
_HOLDS: Final = "PREMISE-HOLDS"

#: Mirrors ``premise_check.DANGLING_CITATION``, ``SURFACE_TOUCHED``,
#: ``CHECK_ERROR`` and ``REFERENCE_NOT_OPEN`` -- the reasons worth an
#: agent's attention. An issue whose only reasons are ``unknown-citation``
#: or ``no-citations`` goes in the unknown-only tail instead: see
#: :func:`_is_high_signal`.
_HIGH_SIGNAL_REASONS: Final = frozenset(
    {"dangling-citation", "surface-touched", "check-error", "reference-not-open"}
)

#: How much of an issue title to show. Long enough for a triager to recognise
#: the issue, short enough that one absurdly long title does not dominate.
_EXCERPT_CHARS: Final = 400


def _fence(text: str, minimum: int = 3) -> str:
    """A run of backticks at least one longer than the longest run inside ``text``."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(minimum, longest + 1)


def _labelled_block(label: str, text: str) -> str:
    """``text`` inside a self-sizing fence, marked as untrusted tracker text."""
    fence = _fence(text)
    return f"Untrusted tracker text -- {label}:\n{fence}\n{text}\n{fence}"


def _inline(text: str) -> str:
    """One code span holding arbitrary text, by CommonMark's own two rules.

    The delimiter is longer than any backtick run inside, and a value that
    begins or ends with a backtick is padded with one space at each end,
    which CommonMark strips again on render. Without both, a token or
    command containing a backtick closes its own span and spills the rest
    of a table row into prose.
    """
    fence = _fence(text, minimum=1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _inline_multiline(text: str) -> str:
    """``_inline``, one code span per line joined by ``<br>``: a literal
    newline inside a table cell breaks GFM's one-row-per-line table syntax,
    and both ``command`` and ``captured_output`` can now carry more than one
    line (round 1 HIGH-2's multi-step evidence).
    """
    return "<br>".join(_inline(line) for line in text.splitlines() or [text])


def _excerpt(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= _EXCERPT_CHARS:
        return stripped
    return f"{stripped[:_EXCERPT_CHARS]} …[truncated]"


def _summary_table(issues: Sequence[IssueReport]) -> str:
    holds = sum(1 for issue in issues if issue.machine_verdict == _HOLDS)
    reason_counts: dict[str, int] = {}
    for issue in issues:
        for reason in issue.needs_agent_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    lines = [
        "| Verdict | Count |",
        "| --- | --- |",
        f"| PREMISE-HOLDS | {holds} |",
        f"| NEEDS-AGENT | {len(issues) - holds} |",
    ]
    for reason in sorted(reason_counts):
        lines.append(f"| &nbsp;&nbsp;reason: {reason} | {reason_counts[reason]} |")
    lines.append("")
    lines.append(
        "_An issue can carry more than one reason; the reason rows do not sum to NEEDS-AGENT._"
    )
    return "\n".join(lines)


def _citation_row(citation: CitationResult) -> str:
    return (
        f"| {citation.kind} | {_inline(citation.token)} | {citation.status} | "
        f"{_inline_multiline(citation.command)} | {_inline_multiline(citation.captured_output)} |"
    )


def _is_high_signal(issue: IssueReport) -> bool:
    return any(reason in _HIGH_SIGNAL_REASONS for reason in issue.needs_agent_reasons)


def _sections(
    issues: Sequence[IssueReport],
) -> tuple[list[IssueReport], list[IssueReport], list[IssueReport]]:
    """Partition, preserving issue-number ordering: high-signal, unknown-only, holds."""
    high_signal: list[IssueReport] = []
    unknown_only: list[IssueReport] = []
    holds: list[IssueReport] = []
    for issue in issues:
        if issue.machine_verdict == _HOLDS:
            holds.append(issue)
        elif _is_high_signal(issue):
            high_signal.append(issue)
        else:
            unknown_only.append(issue)
    return high_signal, unknown_only, holds


def _issue_block(issue: IssueReport) -> str:
    return "\n".join(
        [
            "<details>",
            f"<summary>#{issue.number} -- {issue.machine_verdict}</summary>",
            "",
            _labelled_block("title", _excerpt(issue.title)),
            "",
            f"- Labels: {', '.join(issue.labels) or '(none)'}",
            f"- Created: {issue.created_at}",
            f"- Reasons: {', '.join(issue.needs_agent_reasons) or '(none)'}",
        ]
    )


def _issue_lines(issue: IssueReport) -> list[str]:
    lines = [
        _issue_block(issue),
        "",
        "| Kind | Token | Status | Command | Captured output |",
        "| --- | --- | --- | --- | --- |",
    ]
    for citation in issue.citations:
        lines.append(_citation_row(citation))
    if issue.touching_commits:
        lines.append("")
        lines.append("Touching commits since creation:")
        for commit in issue.touching_commits:
            lines.append(f"- {_inline(commit.sha)} {_inline(commit.subject)}")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines


def _section(heading: str, issues: Sequence[IssueReport], *prose: str) -> list[str]:
    lines = [f"## {heading} ({len(issues)})", ""]
    lines.extend(prose)
    for issue in issues:
        lines.extend(_issue_lines(issue))
    return lines


def render(report: Report) -> str:
    """The full Markdown triage report for one ``check`` run, sectioned by triage weight.

    High-signal first (worth an agent's time), then the unknown-only tail
    (no agent spend by decision), then the holds -- so a human triager reads
    the high-signal subset first and can stop there.
    """
    high_signal, unknown_only, holds = _sections(report.issues)
    lines = [
        "# Issue premise sweep",
        "",
        f"- Head commit: {_inline(report.head_commit)}",
        f"- Snapshot: {_inline(report.snapshot_path)}",
        f"- Issues checked: {len(report.issues)}",
        f"- High-signal: {len(high_signal)}",
        "",
        _summary_table(report.issues),
        "",
    ]
    lines.extend(_section("High-signal", high_signal))
    lines.extend(
        _section(
            "Unknown-only tail",
            unknown_only,
            "Only unknown-citation and no-citations land here (a not-open issue "
            "reference is high-signal instead) -- this set gets no agent spend by "
            "decision, and its size is a grammar-recall measure, not a defect count.",
            "",
        )
    )
    lines.extend(_section("PREMISE-HOLDS", holds))
    return "\n".join(lines)
