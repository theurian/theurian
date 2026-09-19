"""The Markdown triage report :mod:`premise_check`'s ``check`` leg can write.

Split out of ``premise_check.py`` to keep that module under this repository's
line cap, and because rendering has a property verification does not: it is
an **injection surface**. Every string here that came off the tracker --
title, and anything a citation's ``token``, ``command`` or ``output`` carries
-- was typed by whoever opened or commented on the issue, and this report is
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
    return "\n".join(lines)


def _citation_row(citation: CitationResult) -> str:
    return (
        f"| {citation.kind} | {_inline(citation.token)} | {citation.status} | "
        f"{_inline(citation.command)} |"
    )


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


def render(report: Report) -> str:
    """The full Markdown triage report for one ``check`` run."""
    lines = [
        "# Issue premise sweep",
        "",
        f"- Head commit: {_inline(report.head_commit)}",
        f"- Snapshot: {_inline(report.snapshot_path)}",
        f"- Issues checked: {len(report.issues)}",
        "",
        _summary_table(report.issues),
        "",
    ]
    for issue in report.issues:
        lines.append(_issue_block(issue))
        lines.append("")
        lines.append("| Kind | Token | Status | Command |")
        lines.append("| --- | --- | --- | --- |")
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
    return "\n".join(lines)
