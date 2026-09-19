"""What a run that did not come back clean puts on the tracker.

Two rules shape everything here, and they are about the same thing from two
sides: **the strings in this payload are data, and the sweep read them off the
repository.** A file path comes from a directory walk and two slices of source
come from somebody's module. None of that is attacker-controlled today; all of
it is treated as though it were, because the job runs on a runner holding a
token with issue-write scope.

**The process channel.** ``gh`` is invoked with a list argv and never through a
shell, and the body travels on stdin (``--body-file -``) rather than as an
argument. Backticks and ``$( )`` are then ordinary bytes, the body stays out of
the runner's process list, and no argument-length limit applies.

**Which strings are repository text.** The list is longer than it looks, and
under-reading it was this module's own defect. The target path comes from a
directory walk; the mutation label and the two slices of source come from the
generator; and **the harness's per-outcome ``summary`` and ``verdict`` come from
``tools/mutate.py``'s JSON record** -- the summary being pytest's last printed
line (whatever a test printed, or the source a failure echoed) or a
``HarnessError`` string carrying up to eighty characters of the anchor verbatim
(``mutate_edits._apply_edit``). Every one of them is delimited. The summary and
the verdict were not, and a source line was enough to put a live image and a
collapsible block into an issue filed unattended by a token with issues:write.

**The markdown channel.** A source line may contain three backticks -- one
production module does today, ``infrastructure/filesystem/parsers/markdown.py``,
measured 2026-09-16 -- and inside a fixed three-backtick fence such a line
closes the block, spilling the rest of the issue (including the ratchet stub)
into prose. Every delimiter here is therefore sized against the text it holds,
inline code spans (:func:`_inline`) as well as blocks (:func:`_block`).

The issue ends with the "Proposed automation" stub: CLAUDE.md's ratchet says
every adversarial finding proposes its own automation before it closes, and this
is where a sweep finding acquires that obligation.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from sweep_census import SweepError
from sweep_mutations import Candidate
from sweep_verdict import UNTRUSTED, Outcome, Reading

#: The tracker label the sweep files under. It already exists; nothing here
#: creates labels, and ``gh issue create`` fails outright on an unknown one.
LABEL: Final = "async-sweep"

AUTOMATION_HEADING: Final = "## Proposed automation"

#: PR-2 writes the process rule this anchors. Kept as one sentence, last in the
#: body, because it is an instruction about what closes the issue.
AUTOMATION_INSTRUCTION: Final = (
    "This finding closes only when a test, lint rule, or CI gate covering it lands, "
    "or a decline is recorded here."
)

#: The verdicts ``tools/mutate.py`` actually writes, which may render as plain
#: emphasis. Anything else in that field -- a future harness, a corrupted record
#: -- is repository text like any other and goes through :func:`_inline`. An
#: allow-list rather than an escape because the ordinary line is read dozens of
#: times per issue and ``**SURVIVED**`` scans better than code; pinned against
#: ``sweep_verdict``'s own strings so the two modules cannot drift apart quietly.
KNOWN_VERDICTS: Final = frozenset(
    {"SURVIVED", "KILLED", "HUNG", "ERROR", "control-green", "control-red"}
)

_MARKER_PREFIX: Final = "<!-- async-sweep-target: "

#: The key and the title every untrusted night shares.
#:
#: A run the harness could not stand behind is a statement about the *harness*,
#: not about the file the rotation happened to draw -- and keying it per target
#: made one persistent cause open one issue a night. The code review measured 28
#: distinct targets over 30 nights: 28 issues for one broken test, which also
#: fills the hundred-issue dedup window in about three and a half months and then
#: silently breaks the per-target dedup that shares it.
#:
#: Constant, so the nights accumulate as comments on one standing thread -- the
#: shape an alarm already has. Which file each night drew, and when, stays in the
#: body, where it is a detail of the night rather than the identity of the
#: finding.
#: Why a date does not fix a batch, said once and used by both reproduce shapes.
#:
#: The first clause is the one that is easy to miss: the index is
#: ``(ordinal // 7) % len(census)``, so the census *size* re-points every date at
#: once rather than only the dates near a change. Re-measured under the run index
#: in PR #759's round -- still 0 of 30 dates, 140 modules to 139.
_DRIFT_MECHANISM: Final = (
    "The target is the census indexed by the run -- `(ordinal // 7) % census-size` -- so a "
    "module added or removed **anywhere** in the tree re-points every date at once, and "
    "a file's own edits change which of its candidates can still be anchored. Measured "
    "over one week of this repository's growth, 130 modules to 139: 0 of 30 dates "
    "resolved to the same file before and after."
)

UNTRUSTED_MARKER: Final = "<!-- async-sweep-untrusted -->"
UNTRUSTED_TITLE: Final = "async sweep: the harness could not produce a verdict"

#: How many open issues under the label to look through for an existing thread.
#: A sweep that files at most one issue per run cannot plausibly need more, and
#: an unbounded page walk would turn a rate-limited listing into a long silent
#: retry.
_LIST_LIMIT: Final = "100"


@dataclass(frozen=True)
class CommandResult:
    """What running one external command produced."""

    returncode: int
    stdout: str
    stderr: str


#: Runs one command and returns what it produced. Injected so no test has to
#: reach the real ``gh`` -- and so the real one can be a single shell-free call.
Runner = Callable[[Sequence[str], str], CommandResult]


@dataclass(frozen=True)
class Night:
    """Everything the filed issue has to be able to say about one sweep."""

    on: date
    target: str
    #: The harness argv, so a reader can tell a substituted one from the real one.
    harness: tuple[str, ...]
    #: The sweep invocation itself, so the night can be reproduced from the issue.
    command: tuple[str, ...]
    mutate_exit: int
    picked: tuple[Candidate, ...]
    outcomes: tuple[Outcome, ...]
    reading: Reading
    #: Candidates the generator dropped for want of a unique anchor.
    skipped: int
    #: The commit the sweep ran against, when the caller knew it.
    #:
    #: Without it the reproduction instruction is false. The target is
    #: ``(ordinal // 7) % len(census)``, so the census *size* re-points every date
    #: at once whenever a module is added or removed anywhere in the tree -- 0 of 30
    #: dates resolved to the same file across one week of growth, 130 modules to
    #: 139 -- and which of that file's candidates are anchorable depends on its
    #: own contents. `main` moves daily, so a command pasted a week later sweeps
    #: a different file, comes back clean, and closes a finding that is live.
    commit: str | None = None


@dataclass(frozen=True)
class Payload:
    """The issue as it will be filed, and the key that finds it again."""

    title: str
    body: str
    marker: str


def target_marker(path: str) -> str:
    """The hidden key that says which file an open issue is about.

    A digest rather than the path itself. The path is repository text, and a
    path containing ``-->`` would close the HTML comment early -- leaving a
    marker that matches nothing and a body whose first line is half a comment, so
    every run on that file would open a new issue instead of joining a thread.
    The human-readable path is in the body a few lines below, where it cannot
    break anything.
    """
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"{_MARKER_PREFIX}{digest} -->"


def _fence(text: str, minimum: int = 1) -> str:
    """A run of backticks at least one longer than the longest run inside the text."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(minimum, longest + 1)


def _block(text: str, language: str = "") -> str:
    """A fenced block whose fence the text cannot close. Three backticks minimum."""
    fence = _fence(text, minimum=3)
    return f"{fence}{language}\n{text}\n{fence}"


def _inline(text: str) -> str:
    """One code span holding arbitrary text, by CommonMark's own two rules.

    The delimiter is longer than any run inside, and a value that begins or ends
    with a backtick is padded with one space at each end -- which CommonMark
    strips again when it renders, so the reader sees the value and not the
    padding. Without both, a path or a label carrying a backtick closes its own
    span and the rest of the line renders as prose.

    Not for multi-line text: a code span cannot contain a blank line, and the
    values passed here (a path, a label, an operator, an argv) never do.
    """
    fence = _fence(text)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _diff(candidate: Candidate) -> str:
    """One mutation, as a diff whose lines can be pasted back as anchors.

    No space after the marker: an anchor is an exact string and its leading
    indentation is part of it, so a diff that inserted a space would be one
    nobody could apply.
    """
    removed = "\n".join(f"-{line}" for line in candidate.old.splitlines())
    added = "\n".join(f"+{line}" for line in candidate.new.splitlines())
    return _block(f"{removed}\n{added}", "diff")


def _mutation_section(night: Night) -> list[str]:
    lines = ["## Mutations"]
    if not night.picked:
        lines.append("None: the harness was handed no mutation at all.")
        return lines
    for candidate in night.picked:
        lines.append("")
        lines.append(
            f"### {_inline(candidate.label)}\n\n"
            f"Line {candidate.line}, {_inline(candidate.swapped_from)} to "
            f"{_inline(candidate.swapped_to)}, anchored on the {candidate.anchor}."
        )
        lines.append("")
        lines.append(_diff(candidate))
    return lines


def _verdict_section(night: Night) -> list[str]:
    lines = ["## Verdicts"]
    if not night.outcomes:
        lines.append("")
        lines.append("None: the harness recorded no outcome before it stopped.")
        return lines
    lines.append("")
    # Sorted, control first. The harness appends each outcome as its future
    # completes, so its own order is whatever the workers happened to do; a body
    # built in that order would differ between two runs of one night.
    for outcome in sorted(night.outcomes, key=lambda item: (not item.is_control, item.label)):
        summary = f" -- {_inline(outcome.summary)}" if outcome.summary else ""
        verdict = outcome.verdict if outcome.verdict in KNOWN_VERDICTS else _inline(outcome.verdict)
        lines.append(f"- **{verdict}** {_inline(outcome.label)} ({outcome.seconds:.1f}s){summary}")
    return lines


def _is_untrusted(night: Night) -> bool:
    return night.reading.reason == UNTRUSTED


def _headline(night: Night) -> str:
    """The issue title, which is also half of what a reader dedups by eye.

    A survivors night names its file, because a mutation the suite does not hold
    is a statement about that file's tests and two files' gaps are two findings.
    An untrusted night does not, because it is the same finding every time.
    """
    if _is_untrusted(night):
        return UNTRUSTED_TITLE
    count = len(night.reading.unheld)
    return f"async sweep: {count} mutation(s) not held in {night.target} ({night.on})"


def _reproduce_section(night: Night) -> list[str]:
    """How to get this exact batch back, and what that depends on.

    The date does **not** fix the batch on its own, and saying so was this
    section's defect. Two mechanisms move it, and the first is the one that is
    easy to miss: the target is ``(ordinal // 7) % len(census)``, so the census
    *size* is an input -- a module added or removed anywhere re-points every
    date at once, not just the dates near it. Measured over one week of this
    repository's growth, 130 modules to 139, 0 of 30 dates resolved to the same
    file. The second is the file's own contents, which decide how many of its
    candidates can be anchored. A triager reproducing against a later ``main``
    therefore sweeps a different file, gets a clean run, and closes a live
    finding on the strength of it.

    So a known commit leads the block as a `git checkout`, and an unknown one is
    said out loud rather than papered over -- an instruction that cannot be made
    true has to carry its own precondition.
    """
    lines = ["## Reproduce", ""]
    if night.commit is not None:
        lines.append(
            "The batch is a function of the date **and of the tree it ran against**. "
            f"{_DRIFT_MECHANISM} Check that tree out first."
        )
        script = f"git checkout {night.commit}\n{' '.join(night.command)}"
    else:
        lines.append(
            "This run recorded no commit, and the date alone does not fix the batch. "
            f"{_DRIFT_MECHANISM} Run this against the **same commit** the night ran on, "
            "or it will sweep a different file and come back clean."
        )
        script = " ".join(night.command)
    lines.append(
        "It also re-runs the batch: `--dry-run` suppresses the filing, not the harness, "
        "so expect one full suite walk per mutation plus one for the control."
    )
    lines.extend(("", _block(script, "sh"), ""))
    return lines


def build_payload(night: Night) -> Payload:
    """The issue this night files.

    Everything the reader needs to act without re-running anything: which file,
    which night, what each mutation changed, what each verdict was, and the exact
    command that reproduces the batch.
    """
    marker = UNTRUSTED_MARKER if _is_untrusted(night) else target_marker(night.target)
    header = [
        marker,
        "",
        "The scheduled red-team sweep over `main` (#378) did not come back clean.",
        "",
        f"- **Night:** {night.on}",
        f"- **Target:** {_inline(night.target)}",
        f"- **Reading:** {night.reading.detail}",
        f"- **Harness exit:** {night.mutate_exit}",
        f"- **Harness:** {_inline(' '.join(night.harness))}",
        f"- **Candidates dropped for a non-unique anchor:** {night.skipped}",
    ]
    if night.commit is not None:
        header.append(f"- **Commit:** {_inline(night.commit)}")
    header.append("")
    reproduce = _reproduce_section(night)
    automation = [AUTOMATION_HEADING, "", AUTOMATION_INSTRUCTION]
    body = "\n".join(
        [
            *header,
            *_verdict_section(night),
            "",
            *_mutation_section(night),
            "",
            *reproduce,
            *automation,
        ]
    )
    return Payload(title=_headline(night), body=body, marker=marker)


def run_command(argv: Sequence[str], stdin: str = "") -> CommandResult:
    """Run one command with a list argv. There is no shell anywhere in this call.

    ``shell`` is left at its default rather than passed explicitly, so that the
    source check in ``tests/unit/tools/test_sweep_gh_invocation.py`` -- which
    greps these modules for a request for one -- cannot be confused by a literal
    that says the opposite of what it means. That check fires on this docstring
    too if the phrase is spelled out here, which is the check working.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argv is a list built in this module
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise SweepError(f"could not run {argv[0]!r}: {error}") from error
    return CommandResult(
        returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )


def existing_issue(listing: str, marker: str) -> int | None:
    """The open issue already tracking this target, if there is one.

    Matched at the position the builder writes it -- the body's first line -- and
    not anywhere in the body. Anywhere was exploitable: the security review put a
    *victim* file's marker into a trailing comment on an anchorable source line,
    so the *carrier* file's issue quoted it inside a diff, and every later night
    on the victim file then commented on the carrier's thread. Its findings would
    be filed under another file's title, where nobody triaging that file looks.
    Leading whitespace is tolerated because a body round-tripped through the API
    can acquire it; nothing else is.

    The lowest matching number rather than the first, because ``gh`` returns
    newest first and "the thread for this file" is the oldest one: a night that
    commented on whichever issue happened to sort first would split one file's
    history across every issue ever opened for it.

    An unreadable listing raises rather than reading as empty. Empty means "open
    a new issue", so a rate-limited or malformed response would open a duplicate
    per run for as long as it lasted.
    """
    try:
        loaded = json.loads(listing)
    except json.JSONDecodeError as error:
        raise SweepError(f"`gh issue list` did not return JSON: {error}") from error
    if not isinstance(loaded, list):
        raise SweepError("`gh issue list` returned something that is not a list of issues")
    matches: list[int] = []
    for entry in loaded:
        if not isinstance(entry, dict):
            raise SweepError("`gh issue list` returned an entry that is not an issue object")
        if str(entry.get("body", "")).lstrip().startswith(marker):
            matches.append(int(entry["number"]))
    return min(matches) if matches else None


def _repo_flag(repo: str | None) -> tuple[str, ...]:
    """``--repo owner/name``, or nothing at all.

    Nothing, rather than an empty value: ``gh`` reads ``--repo ''`` as a
    malformed repository and fails, so a night without the flag would file
    nothing.
    """
    return ("--repo", repo) if repo else ()


def list_argv(gh: str, repo: str | None) -> tuple[str, ...]:
    """The open issues under the sweep's label, with the bodies the marker hides in."""
    return (
        gh,
        "issue",
        "list",
        *_repo_flag(repo),
        "--label",
        LABEL,
        "--state",
        "open",
        "--limit",
        _LIST_LIMIT,
        "--json",
        "number,body",
    )


def create_argv(gh: str, repo: str | None, title: str) -> tuple[str, ...]:
    """A new thread. The body is not here; it goes in on stdin."""
    return (
        gh,
        "issue",
        "create",
        *_repo_flag(repo),
        "--title",
        title,
        "--label",
        LABEL,
        "--body-file",
        "-",
    )


def comment_argv(gh: str, repo: str | None, number: int) -> tuple[str, ...]:
    """Another night on a file that already has an open thread."""
    return (gh, "issue", "comment", str(number), *_repo_flag(repo), "--body-file", "-")


def file_finding(payload: Payload, *, repo: str | None, runner: Runner, gh: str) -> str:
    """Put this payload on the tracker, and say what happened to it.

    Every failure raises. "Filed" has to mean the tracker took it: a night that
    found a surviving mutation and could not file it has produced nothing a human
    will ever see, and reporting success there would silence the workflow's alarm
    in exactly the case it exists for.
    """
    listed = runner(list_argv(gh, repo), "")
    if listed.returncode != 0:
        raise SweepError(f"`gh issue list` failed ({listed.returncode}): {listed.stderr.strip()}")

    number = existing_issue(listed.stdout, payload.marker)
    if number is None:
        result = runner(create_argv(gh, repo, payload.title), payload.body)
        action = "opened a new issue"
    else:
        result = runner(comment_argv(gh, repo, number), payload.body)
        action = f"commented on #{number}"
    if result.returncode != 0:
        raise SweepError(f"`gh issue` failed ({result.returncode}): {result.stderr.strip()}")
    return f"{action}: {result.stdout.strip() or payload.title}"
