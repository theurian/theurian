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

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from sweep_census import SweepError
from sweep_mutations import Attempt, Candidate
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

_MARKER_PREFIX: Final = "<!-- async-sweep-run: "

#: The two lines the filed body and its reader both depend on.
#:
#: ``tools/audit/sweep_kill_rates.py`` derives a per-module kill rate by reading
#: issues this module wrote, which makes the body's shape a contract with two
#: ends rather than a rendering detail. Both ends read these, and the writers
#: below (:func:`module_heading`, :func:`verdict_line`) emit them, so a shape
#: change is one edit here and the round-trip pin in the suite catches a reader
#: that stopped parsing what the filer files.
#:
#: Tolerant of :func:`_inline`'s two devices, because that is what writes the
#: values: a delimiter longer than any backtick run inside, and a space of
#: padding when the value itself starts or ends with a backtick.
MODULE_HEADING_PATTERN: Final = re.compile(r"^### (?P<fence>`+) ?(?P<path>.+?) ?(?P=fence)$")

_VERDICT_LINE_PATTERN: Final = re.compile(
    r"^- \*\*(?P<verdict>.+?)\*\* (?P<fence>`+) ?(?P<label>.+?) ?(?P=fence)(?: \(|$)"
)

_INLINED_VALUE_PATTERN: Final = re.compile(r"(?P<fence>`+) ?(?P<value>.+?) ?(?P=fence)")


def parse_verdict_line(line: str) -> tuple[str, str] | None:
    """``(verdict, label)`` from a line :func:`verdict_line` wrote, or ``None``.

    The reading half of the contract, kept beside the writing half so the
    round-trip is one file's business: ``parse_verdict_line(verdict_line(v, l,
    s)) == (v, l)`` is what the suite pins.

    **The emphasis shape alone does not identify a verdict**, and assuming it did
    is the defect this function exists to have fixed. The body's own header
    carries ``- **Target:** `path` ``, ``- **Harness:** `argv` `` and
    ``- **Commit:** `sha` ``, which are that shape exactly: read loosely, three
    header lines counted as three mutations, and a kill rate computed over them
    is wrong in the direction that looks like evidence.

    So the verdict field is admitted on the writer's own rule and nothing
    weaker -- plain emphasis only for a verdict in :data:`KNOWN_VERDICTS`, and
    otherwise a code span, because that is exactly what :func:`verdict_line`
    emits for a value it does not recognise. A header label is plain and not
    known, so it is refused; a future harness's unknown verdict is inlined, so
    it still reads.
    """
    found = _VERDICT_LINE_PATTERN.match(line)
    if found is None:
        return None
    field = found.group("verdict")
    if field in KNOWN_VERDICTS:
        return field, found.group("label")
    inlined = _INLINED_VALUE_PATTERN.fullmatch(field)
    if inlined is None:
        return None
    return inlined.group("value"), found.group("label")


#: The heading the per-module sections sit under, and the one the reader finds
#: them by. A run's control walk is *not* under it: the control is a statement
#: about the run, and counting it as a module's verdict would credit whichever
#: module happened to sort first with a kill it did not earn.
MODULES_HEADING: Final = "## Modules"

#: Verdicts whose label matched no mutation this run handed over. Never dropped:
#: an outcome nobody can attribute is the shape of a harness that ran something
#: else, which is exactly what the untrusted reading exists to surface.
UNATTRIBUTED_HEADING: Final = "## Unattributed verdicts"

#: Why a date does not fix a batch, said once and used by both reproduce shapes.
#:
#: The first clause is the one that is easy to miss: the block opens at
#: ``(run · block-size) % len(census)``, so the census *size* re-points every date
#: at once rather than only the dates near a change.
#:
#: **Counted in runs, and that is the correction rather than a detail.** This
#: measurement used to be stated over 30 consecutive *dates*, which under the run
#: index is 5 buckets and therefore 5 independent draws -- the sentence claimed
#: six times the evidence it had, in a paragraph whose whole subject is what a
#: date does and does not fix. Re-measured 2026-09-19 at 142 modules over 30
#: consecutive runs: 0 of 30.
_DRIFT_MECHANISM: Final = (
    "The block is the census indexed by the run -- it opens at "
    "`(run * block-size) % census-size` and takes the next `block-size` entries, "
    "wrapping -- so a module added or removed **anywhere** in the tree re-points every "
    "date at once, and a file's own edits change which of its candidates can still be "
    "anchored. Which candidate a file offers is indexed by its visit number, so a "
    "file offering more than one answers a different question on a later lap. "
    "Measured 2026-09-19 over 30 consecutive runs, one module removed from a "
    "142-module census: 0 of 30 runs drew the same block, and none so much as "
    "opened on the same file."
)

#: The key and the title every untrusted run shares.
#:
#: A run the harness could not stand behind is a statement about the *harness*,
#: not about the block the rotation happened to draw, so this key is **constant**
#: where :func:`run_marker` is per-run: the runs accumulate as comments on one
#: standing thread, which is the shape an alarm already has. Which block each run
#: drew, and when, stays in the body, where it is a detail of the run rather than
#: the identity of the finding.
#:
#: A key that varied would open one issue per affected run for as long as one
#: cause persisted, and :data:`_LIST_LIMIT` of them fill the dedup window every
#: thread under this label shares. Counted in runs deliberately: this paragraph
#: used to give the same bound as "about three and a half months", which was
#: seven times wrong the day the cadence went weekly, while the arithmetic it
#: came from had not moved at all.
#:
#: The measurement behind the decision predates both the block form and the
#: weekly cadence: the code review counted 28 distinct targets over 30
#: consecutive daily runs of the single-file rotation, so one broken test would
#: have opened 28 issues under the per-target key this replaced.
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
    """Everything the filed issue has to be able to say about one sweep run."""

    on: date
    #: ``sweep_census.run_index(on)``. The dedup key, and the one number that
    #: distinguishes two runs a reader might otherwise read as one.
    run: int
    #: Every slot of the block, barren ones included -- a block member with
    #: nothing to mutate is a fact about the run's reach, and the old single-file
    #: form could only express it by advancing past it.
    attempts: tuple[Attempt, ...]
    #: The harness argv, so a reader can tell a substituted one from the real one.
    harness: tuple[str, ...]
    #: The sweep invocation itself, so the run can be reproduced from the issue.
    command: tuple[str, ...]
    mutate_exit: int
    outcomes: tuple[Outcome, ...]
    reading: Reading
    #: The commit the sweep ran against, when the caller knew it.
    #:
    #: Without it the reproduction instruction is false. The block opens at
    #: ``(run · block-size) % len(census)``, so the census *size* re-points every
    #: date at once whenever a module is added or removed anywhere in the tree --
    #: 0 of 30 consecutive runs drew the same block when one module was removed
    #: from a 142-module census, measured 2026-09-19 -- and which of a file's
    #: candidates are anchorable depends on its own contents. `main` moves daily,
    #: so a command pasted a week later sweeps a different block, comes back
    #: clean, and closes a finding that is live.
    commit: str | None = None

    @property
    def picked(self) -> tuple[Candidate, ...]:
        """The mutations this run actually handed the harness, in block order."""
        return tuple(
            attempt.candidate for attempt in self.attempts if attempt.candidate is not None
        )

    @property
    def skipped(self) -> int:
        """Candidates the generator dropped across the whole block."""
        return sum(len(attempt.generated.skipped) for attempt in self.attempts)


@dataclass(frozen=True)
class Payload:
    """The issue as it will be filed, and the key that finds it again."""

    title: str
    body: str
    marker: str


def run_marker(run: int) -> str:
    """The hidden key that says which run an open issue is about.

    Keyed on the run and no longer on the target, because the unit changed: one
    run now attacks a whole block and files one issue, so there is no single
    target to key on. The run number is the honest key, and it makes the one case
    that *should* join a thread join it -- a rerun inside the same Sunday-to-
    Saturday bucket has the same run index, draws the same block, and comments
    rather than opening a second issue for work already recorded.

    Two runs are two findings and open two issues. That is the intended reading:
    each run is its own batch of questions, and a week's answers do not amend the
    previous week's.

    The value is the run number rather than a digest of it because an integer
    cannot contain ``-->`` and close the comment early. The path digest this
    replaced existed for that reason and no longer has a path to protect.
    """
    return f"{_MARKER_PREFIX}{run} -->"


def module_heading(path: str) -> str:
    """One module's section heading. Parsed back by :data:`MODULE_HEADING_PATTERN`."""
    return f"### {_inline(path)}"


def verdict_line(verdict: str, label: str, seconds: float, summary: str = "") -> str:
    """One verdict, as the body renders it. Parsed back by :func:`parse_verdict_line`.

    A known verdict renders as plain emphasis because the ordinary line is read
    dozens of times per issue and ``**SURVIVED**`` scans better than code;
    anything else is repository text and goes through :func:`_inline`.
    """
    rendered = verdict if verdict in KNOWN_VERDICTS else _inline(verdict)
    tail = f" -- {_inline(summary)}" if summary else ""
    return f"- **{rendered}** {_inline(label)} ({seconds:.1f}s){tail}"


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


def _module_section(night: Night) -> list[str]:
    """One section per block member: what it was asked, and what came back.

    Per module rather than one flat verdict list, because the run's unit changed
    and a triager acts per module: the section is what lets one file's finding be
    reproduced, and what ``tools/audit/sweep_kill_rates.py`` counts a kill rate
    from. Barren members keep their section -- "this module offered nothing to
    mutate" is a fact about the sweep's reach that a reader cannot otherwise
    recover from the issue.
    """
    by_label = {outcome.label: outcome for outcome in night.outcomes if not outcome.is_control}
    lines = [MODULES_HEADING]
    for attempt in night.attempts:
        lines.extend(("", module_heading(attempt.path), ""))
        lines.append(
            f"Census index {attempt.slot.index}, visit {attempt.slot.lap}, "
            f"{len(attempt.generated.candidates)} candidate(s), "
            f"{len(attempt.generated.skipped)} dropped for a non-unique anchor."
        )
        candidate = attempt.candidate
        if candidate is None:
            lines.extend(("", "Barren: nothing here to mutate, so this slot bought no walk."))
            continue
        outcome = by_label.get(candidate.label)
        lines.append("")
        if outcome is None:
            lines.append(
                f"No verdict came back for {_inline(candidate.label)}, which is why this "
                "run cannot be read as a result."
            )
        else:
            lines.append(
                verdict_line(outcome.verdict, outcome.label, outcome.seconds, outcome.summary)
            )
        lines.extend(
            (
                "",
                f"Line {candidate.line}, {_inline(candidate.swapped_from)} to "
                f"{_inline(candidate.swapped_to)}, anchored on the {candidate.anchor}.",
                "",
                _diff(candidate),
            )
        )
    return lines


def _control_section(night: Night) -> list[str]:
    """The run's own control walk, and anything nobody could attribute.

    Sorted, because the harness appends each outcome as its future completes: its
    own order is whatever the workers happened to do, and a body built in that
    order would differ between two runs of one date.
    """
    handed = {candidate.label for candidate in night.picked}
    controls = [item for item in night.outcomes if item.is_control]
    orphans = [item for item in night.outcomes if not item.is_control and item.label not in handed]
    lines = ["## Control", ""]
    if not controls:
        lines.append("None: the harness recorded no control run, so nothing here is a result.")
    for outcome in sorted(controls, key=lambda item: item.label):
        lines.append(verdict_line(outcome.verdict, outcome.label, outcome.seconds, outcome.summary))
    if orphans:
        lines.extend(("", UNATTRIBUTED_HEADING, ""))
        for outcome in sorted(orphans, key=lambda item: item.label):
            lines.append(
                verdict_line(outcome.verdict, outcome.label, outcome.seconds, outcome.summary)
            )
    return lines


def _is_untrusted(night: Night) -> bool:
    return night.reading.reason == UNTRUSTED


def unheld_modules(night: Night) -> tuple[str, ...]:
    """The block members whose mutation the suite did not hold, in block order.

    De-duplicated by path though it cannot repeat today: one mutation per file
    per run means one label per module, and the ``dict.fromkeys`` is what keeps
    that an implementation detail rather than an assumption a later budget change
    silently breaks.
    """
    unheld = set(night.reading.unheld)
    return tuple(
        dict.fromkeys(
            attempt.path
            for attempt in night.attempts
            if attempt.candidate is not None and attempt.candidate.label in unheld
        )
    )


def _headline(night: Night) -> str:
    """The issue title, which is also half of what a reader dedups by eye.

    A survivors run still names its module when there is exactly one, because a
    mutation the suite does not hold is a statement about that module's tests and
    the five issues filed before the block form all read that way. A run that
    found gaps in several says how many instead: six paths do not fit a title,
    and the ``## Modules`` sections below name them all.

    An untrusted run names nothing, because it is the same finding every time.
    """
    if _is_untrusted(night):
        return UNTRUSTED_TITLE
    count = len(night.reading.unheld)
    modules = unheld_modules(night)
    where = modules[0] if len(modules) == 1 else f"{len(modules)} modules"
    return f"async sweep: {count} mutation(s) not held in {where} ({night.on})"


def _reproduce_section(night: Night) -> list[str]:
    """How to get this exact batch back, and what that depends on.

    The date does **not** fix the batch on its own, and saying so was this
    section's defect. Two mechanisms move it, and the first is the one that is
    easy to miss: the block opens at ``(run * block-size) % len(census)``, so the
    census *size* is an input -- a module added or removed anywhere re-points
    every date at once, not just the dates near it. Measured 2026-09-19 over 30
    consecutive runs, one module removed from a 142-module census: 0 of 30 drew
    the same block. Counted in runs because under the run index 30 consecutive
    dates are only 5 of them, which is what the date-counted form of this figure
    got wrong. The second is the file's own contents, which decide how many of its
    candidates can be anchored. A triager reproducing against a later ``main``
    therefore sweeps a different block, gets a clean run, and closes a live
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
            "or it will sweep a different block and come back clean."
        )
        script = " ".join(night.command)
    lines.append(
        "It also re-runs the batch: `--dry-run` suppresses the filing, not the harness, "
        "so expect one full suite walk per mutated module plus one shared control."
    )
    lines.extend(("", _block(script, "sh"), ""))
    return lines


def build_payload(night: Night) -> Payload:
    """The issue this night files.

    Everything the reader needs to act without re-running anything: which file,
    which night, what each mutation changed, what each verdict was, and the exact
    command that reproduces the batch.
    """
    marker = UNTRUSTED_MARKER if _is_untrusted(night) else run_marker(night.run)
    barren = sum(1 for attempt in night.attempts if attempt.candidate is None)
    header = [
        marker,
        "",
        "The scheduled red-team sweep over `main` (#378) did not come back clean.",
        "",
        f"- **Run:** {night.on} (run {night.run})",
        f"- **Block:** {len(night.attempts)} module(s), {len(night.picked)} mutated, "
        f"{barren} barren",
        f"- **Reading:** {night.reading.detail}",
        f"- **Harness exit:** {night.mutate_exit}",
        f"- **Harness:** {_inline(' '.join(night.harness))}",
        f"- **Candidates dropped for a non-unique anchor:** {night.skipped}",
    ]
    if night.commit is not None:
        header.append(f"- **Commit:** {_inline(night.commit)}")
    header.append("")
    automation = [AUTOMATION_HEADING, "", AUTOMATION_INSTRUCTION]
    body = "\n".join(
        [
            *header,
            *_control_section(night),
            "",
            *_module_section(night),
            "",
            *_reproduce_section(night),
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
