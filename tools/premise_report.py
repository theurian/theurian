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

#: Mirrors ``premise_check.DANGLING_CITATION``: the strongest evidence a
#: citation's premise moved -- the thing it names is gone at HEAD.
_DANGLING_CITATION: Final = "dangling-citation"

#: Mirrors ``premise_check.SURFACE_TOUCHED`` and ``premise_check.CHECK_ERROR``:
#: a commit landed on the cited surface since the issue opened, or the check
#: itself could not run. Both read as "look here", so one bucket holds both.
_SURFACE_REASONS: Final = frozenset({"surface-touched", "check-error"})

#: Mirrors ``premise_check.REFERENCE_NOT_OPEN``. The check can only tell a
#: closed reference from an open one -- not a mooted blocker from a
#: reference kept for provenance -- so a closed reference gets its own
#: section instead of flooding the read-first sections above it or being
#: buried in the no-spend tail below (measured at 88b346c3 against the
#: 2026-09-20 snapshot: 58 of 129 issues carry a not-open reference).
_REFERENCE_NOT_OPEN: Final = "reference-not-open"

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
    """One code span holding arbitrary text.

    Two rules are CommonMark's own: the delimiter is longer than any
    backtick run inside, and a value that begins or ends with a backtick is
    padded with one space at each end, which CommonMark strips again on
    render. Without both, a token or command containing a backtick closes
    its own span and spills the rest of a table row into prose. A third
    rule is GFM's, not CommonMark's: a table row splits on any unescaped
    ``|``, even inside a code span, so a literal pipe is backslash-escaped
    before the fence goes on (round 2 HIGH-2: 21 pipes in one captured
    `git grep` census turned a 5-column row into 26 cells).

    Empty text gets a single space rather than an empty pair of backticks
    (round 2 LOW-1): two bare backticks with nothing between them are one
    ambiguous 2-backtick run, not a closed empty span.
    """
    if not text:
        return "` `"
    escaped = text.replace("|", "\\|")
    fence = _fence(escaped, minimum=1)
    padding = " " if escaped.startswith("`") or escaped.endswith("`") else ""
    return f"{fence}{padding}{escaped}{padding}{fence}"


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


def _sections(
    issues: Sequence[IssueReport],
) -> tuple[
    list[IssueReport], list[IssueReport], list[IssueReport], list[IssueReport], list[IssueReport]
]:
    """Partition into five priority buckets, preserving issue-number ordering:
    each issue lands in the first match, checked holds, dangling,
    surface-touched, reference-not-open, unknown-only.
    """
    dangling: list[IssueReport] = []
    surface_touched: list[IssueReport] = []
    reference_not_open: list[IssueReport] = []
    unknown_only: list[IssueReport] = []
    holds: list[IssueReport] = []
    for issue in issues:
        reasons = issue.needs_agent_reasons
        if issue.machine_verdict == _HOLDS:
            holds.append(issue)
        elif _DANGLING_CITATION in reasons:
            dangling.append(issue)
        elif any(reason in _SURFACE_REASONS for reason in reasons):
            surface_touched.append(issue)
        elif _REFERENCE_NOT_OPEN in reasons:
            reference_not_open.append(issue)
        else:
            unknown_only.append(issue)
    return dangling, surface_touched, reference_not_open, unknown_only, holds


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

    Dangling first (strongest evidence a premise moved), then
    surface-touched/check-error, then a not-open issue reference, then the
    unknown-only tail (no agent spend by decision), then the holds -- so a
    human triager reads the strongest sections first and can stop early.
    """
    dangling, surface_touched, reference_not_open, unknown_only, holds = _sections(report.issues)
    lines = [
        "# Issue premise sweep",
        "",
        f"- Head commit: {_inline(report.head_commit)}",
        f"- Snapshot: {_inline(report.snapshot_path)}",
        f"- Issues checked: {len(report.issues)}",
        f"- Sections: Dangling {len(dangling)}, Surface-touched {len(surface_touched)}, "
        f"Reference-not-open {len(reference_not_open)}, "
        f"Unknown-only tail {len(unknown_only)}, PREMISE-HOLDS {len(holds)}",
        "",
        _summary_table(report.issues),
        "",
    ]
    lines.extend(_section("Dangling", dangling))
    lines.extend(_section("Surface-touched", surface_touched))
    lines.extend(
        _section(
            "Reference-not-open",
            reference_not_open,
            "A closed or merged reference may be a mooted blocker or mere "
            "provenance for the issue that cites it -- telling those apart is "
            "a reading judgment for the agent pass, not something this check "
            "can resolve.",
            "",
        )
    )
    lines.extend(
        _section(
            "Unknown-only tail",
            unknown_only,
            "Only unknown-citation and no-citations land here (dangling, "
            "surface-touched/check-error and reference-not-open citations are "
            "triaged in the sections above instead) -- this set gets no agent "
            "spend by decision, and its size is a grammar-recall measure, not "
            "a defect count.",
            "",
        )
    )
    lines.extend(_section("PREMISE-HOLDS", holds))
    return "\n".join(lines)
