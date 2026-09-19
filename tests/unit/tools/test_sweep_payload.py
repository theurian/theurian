"""What the sweep files, and why none of it can be a command (#378).

The payload is built from strings the sweep read off the repository: a file
path, a mutation label, and two slices of somebody's source. None of them is
attacker-controlled today and all of them are treated as if they were, because
the cost of being wrong is a shell running repository content on a runner that
holds a token with issue-write scope.

Two separate channels have to hold, and they fail differently:

- **The process channel.** ``gh`` is invoked with a list argv and never through
  a shell, and the body travels on stdin rather than as an argument. Backticks
  and ``$( )`` are then ordinary bytes.
- **The markdown channel.** A source line containing a code fence must not close
  the block the sweep put it in, or the rest of the issue renders as prose and
  the reader loses the diff.

The rest is AC4: the issue has to say which file, which night, what each
mutation did and what each verdict was, and it has to end with the ratchet stub
that PR-2's process rule anchors on.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
import sweep_census
import sweep_filing
import sweep_mutations
import sweep_verdict

pytestmark = pytest.mark.unit

_NIGHT = date(2026, 9, 16)

#: Every metacharacter that would matter if any of this reached a shell, plus a
#: code fence for the markdown channel. Paths like this do not exist in the
#: census today; the payload builder is not allowed to depend on that.
_HOSTILE_PATH = 'packages/theurian-core/src/theurian/$(id)/`whoami`/"it\'s".py'
_HOSTILE_OLD = "value < 3  # ``` still inside the fence"


def _candidate(
    label: str, path: str = "packages/theurian-core/src/theurian/x.py"
) -> sweep_mutations.Candidate:
    return sweep_mutations.Candidate(
        label=label,
        path=path,
        old="        if used <= budget:",
        new="        if used < budget:",
        line=42,
        swapped_from="<=",
        swapped_to="<",
        anchor="line",
    )


def _night(  # noqa: PLR0913 - a builder for a ten-field record; every field is varied by a test
    *,
    target: str = "packages/theurian-core/src/theurian/x.py",
    candidates: tuple[sweep_mutations.Candidate, ...] = (),
    outcomes: tuple[sweep_verdict.Outcome, ...] = (),
    reading: sweep_verdict.Reading | None = None,
    mutate_exit: int = 1,
    commit: str | None = "9f2c1ab4d5e6f70819a2b3c4d5e6f7089a1b2c3d",
) -> sweep_filing.Night:
    picked = candidates or (_candidate("sweep-2026-09-16-00-le-to-lt-l42"),)
    reported = outcomes or (
        sweep_verdict.Outcome("__control__", "control-green", 903.2, "1 passed"),
        sweep_verdict.Outcome(picked[0].label, "SURVIVED", 512.5, "5571 passed"),
    )
    return sweep_filing.Night(
        on=_NIGHT,
        target=target,
        harness=("uv", "run", "--frozen", "python", "tools/mutate.py"),
        command=("uv", "run", "--frozen", "python", "tools/sweep.py", "--date", "2026-09-16"),
        mutate_exit=mutate_exit,
        picked=picked,
        outcomes=reported,
        reading=reading
        or sweep_verdict.Reading(
            reason=sweep_verdict.SURVIVORS,
            detail="1 of 1 mutation(s) the suite does not hold",
            unheld=(picked[0].label,),
        ),
        skipped=3,
        commit=commit,
    )


def test_the_body_names_the_target_the_night_and_every_verdict() -> None:
    """AC4, item by item, because each one is what makes the issue actionable.

    Without the target and the night, the finding cannot be reproduced -- those
    two are the whole input to the sweep. Without every verdict, a reader cannot
    tell a batch where one of six mutations survived from a batch where five
    did, and the control's verdict is what says whether any of it counts.
    """
    payload = sweep_filing.build_payload(_night())

    assert "packages/theurian-core/src/theurian/x.py" in payload.body
    assert "2026-09-16" in payload.body
    assert "SURVIVED" in payload.body
    assert "control-green" in payload.body
    assert "sweep-2026-09-16-00-le-to-lt-l42" in payload.body


def test_the_body_carries_the_diff_of_every_mutation_that_was_run() -> None:
    """A verdict without its diff sends the reader back to regenerate the batch.

    Both sides are rendered with no space after the marker, so the anchor's own
    indentation survives into the issue: an anchor is an exact string, and a diff
    that adds a space to it is a diff nobody can paste back.

    The fence is three backticks when nothing in the content demands more. Sizing
    it purely against the content would give a *one*-backtick opener for the
    ordinary case, which is not a fenced block at all -- the whole diff would
    render as a paragraph with its leading minus signs eaten as list markers.
    """
    payload = sweep_filing.build_payload(_night())

    assert "```diff\n" in payload.body
    assert "-        if used <= budget:" in payload.body
    assert "+        if used < budget:" in payload.body


def test_the_body_ends_with_the_ratchet_stub() -> None:
    """The anchor PR-2's process rule attaches to, and the last thing a reader sees.

    "Ends with" is the assertion rather than "contains": the stub is an
    instruction about what closes the issue, and an instruction buried above a
    diff dump is one nobody reaches.
    """
    payload = sweep_filing.build_payload(_night())

    assert sweep_filing.AUTOMATION_HEADING in payload.body
    assert payload.body.rstrip().endswith(sweep_filing.AUTOMATION_INSTRUCTION)


def test_an_untrusted_run_says_so_in_its_title() -> None:
    """AC5. The two filings mean different things and must not read alike.

    "One mutation survived" is a gap in the suite. "The run cannot be trusted"
    is a gap in the *sweep*, and triaging it as the former sends somebody
    looking for a missing test that nothing measured.

    The untrusted title is the standing one and carries no file and no date --
    every untrusted run comments on one thread -- so what distinguishes the two
    is asserted as the title itself, and as the absence of the survivors phrasing
    that would send the reader looking for a test.
    """
    payload = sweep_filing.build_payload(
        _night(
            mutate_exit=2,
            reading=sweep_verdict.Reading(
                reason=sweep_verdict.UNTRUSTED, detail="the harness exited 2"
            ),
        )
    )

    assert payload.title == sweep_filing.UNTRUSTED_TITLE
    assert "not held in" not in payload.title
    assert "exited 2" in payload.body


def _untrusted(target: str) -> sweep_filing.Payload:
    return sweep_filing.build_payload(
        _night(
            target=target,
            mutate_exit=2,
            reading=sweep_verdict.Reading(
                reason=sweep_verdict.UNTRUSTED, detail="the harness exited 2"
            ),
        )
    )


def test_every_untrusted_run_joins_one_standing_thread() -> None:
    """A broken harness is a statement about the harness, not about a file.

    Keyed per target, one persistent failure -- a test broken on `main` for a
    week, a control that keeps timing out -- opens a *new* issue on every run,
    because the rotation names a different file each run. The code review
    measured 28 distinct targets over 30 consecutive draws: 28 issues for one
    cause, which also fills the hundred-issue dedup window in about as many runs
    and silently breaks the survivors dedup that shares it.

    So the untrusted reading gets a constant key and a constant title, and the
    runs accumulate as comments on one thread. Which file each run happened to
    draw is still in the body, where it belongs: it is a detail of the run, not
    the identity of the finding.
    """
    first = _untrusted("packages/theurian-core/src/theurian/one.py")
    second = _untrusted("packages/theurian-core/src/theurian/two.py")

    assert first.marker == second.marker == sweep_filing.UNTRUSTED_MARKER
    assert first.title == second.title == sweep_filing.UNTRUSTED_TITLE
    assert first.body.startswith(sweep_filing.UNTRUSTED_MARKER)
    assert "one.py" in first.body
    assert "two.py" in second.body


def test_a_surviving_mutation_is_still_a_finding_about_its_own_file() -> None:
    """The other half: survivors must not collapse onto one thread.

    A mutation the suite does not hold is a statement about *that file's* tests,
    and two files' gaps are two findings. Collapsing them would bury the second
    one in the first one's comments -- which is the mirror of the defect above,
    and a fix aimed only at the untrusted branch is what would cause it.
    """
    first = sweep_filing.build_payload(_night(target="packages/theurian-core/src/theurian/one.py"))
    second = sweep_filing.build_payload(_night(target="packages/theurian-core/src/theurian/two.py"))

    assert first.marker != second.marker
    assert first.marker != sweep_filing.UNTRUSTED_MARKER
    assert first.title != second.title
    assert "one.py" in first.title


def test_the_standing_untrusted_thread_still_dates_and_names_each_run() -> None:
    """One thread must not mean one indistinguishable pile of comments.

    Whoever picks up the standing issue needs to know which runs are in it and
    what each of them was attacking -- otherwise the constant key trades a
    triage flood for an unreadable log.
    """
    payload = _untrusted("packages/theurian-core/src/theurian/one.py")

    assert "- **Night:** 2026-09-16" in payload.body
    assert "- **Target:** `packages/theurian-core/src/theurian/one.py`" in payload.body


def test_two_runs_of_one_night_produce_the_same_body() -> None:
    """The harness reports outcomes as they land, which is not an order.

    ``mutate.py`` appends each result as its future completes, so the same batch
    writes its outcomes in whatever order the workers happened to finish. A body
    built in that order would differ between two runs of one night, and the
    dedup comment stream would read as a change when nothing had changed.
    """
    outcomes = (
        sweep_verdict.Outcome("sweep-b", "KILLED", 30.0, ""),
        sweep_verdict.Outcome("__control__", "control-green", 903.2, ""),
        sweep_verdict.Outcome("sweep-a", "SURVIVED", 512.5, ""),
    )

    forward = sweep_filing.build_payload(_night(outcomes=outcomes))
    shuffled = sweep_filing.build_payload(_night(outcomes=tuple(reversed(outcomes))))

    assert forward.body == shuffled.body
    assert forward.body.index("__control__") < forward.body.index("sweep-a")


def test_shell_metacharacters_in_a_path_survive_into_the_payload_as_text() -> None:
    """The injection family, checked on the rendered text rather than on a helper.

    A table of "which strings get escaped" agrees with the escaping it describes.
    What matters is that the bytes the sweep read are the bytes a reader sees --
    if they came back escaped, the issue would name a file that does not exist.
    """
    payload = sweep_filing.build_payload(
        _night(target=_HOSTILE_PATH, candidates=(_candidate("sweep-x", path=_HOSTILE_PATH),))
    )

    assert _HOSTILE_PATH in payload.title
    assert _HOSTILE_PATH in payload.body


def test_a_code_fence_inside_a_mutated_line_cannot_close_the_block_around_it() -> None:
    """The markdown channel: source text is data inside the fence, not syntax.

    Any Python file may contain three backticks, and one production module does
    today -- ``infrastructure/filesystem/parsers/markdown.py``, measured
    2026-09-16, which is one rotation away from being a target. Rendered inside a
    three-backtick fence, such a line closes it, and everything after it
    (including the ratchet stub) renders as prose. The fence is therefore sized
    against the content rather than fixed.
    """
    fenced = _candidate("sweep-y")
    mutated = sweep_mutations.Candidate(
        label=fenced.label,
        path=fenced.path,
        old=_HOSTILE_OLD,
        new=_HOSTILE_OLD.replace("<", "<="),
        line=7,
        swapped_from="<",
        swapped_to="<=",
        anchor="line",
    )

    payload = sweep_filing.build_payload(_night(candidates=(mutated,)))

    assert f"-{_HOSTILE_OLD}" in payload.body
    assert "````diff" in payload.body
    assert payload.body.rstrip().endswith(sweep_filing.AUTOMATION_INSTRUCTION)


def test_a_backtick_in_a_path_cannot_close_the_code_span_it_sits_in() -> None:
    """The same markdown rule as the fence above, one line up: inline code spans.

    The header renders the target inside a code span, and a single backtick in
    the value closes it -- so the rest of that line, and every delimiter after
    it, shifts by one. Sizing the block fences and leaving the spans fixed would
    have left the injection family half-closed on the only channel a reader
    actually looks at.

    CommonMark's two rules, both asserted: a delimiter longer than any run inside
    the value, and -- when the value begins or ends with a backtick -- one space
    of padding at *each* end, since the renderer only strips a leading space if
    there is a trailing one to match it. Padding one side alone would leave the
    space visible in the rendered path.
    """
    tricky = "packages/theurian-core/src/theurian/``odd``.py"
    trailing = "packages/theurian-core/src/theurian/odd`"

    assert sweep_filing._inline(tricky) == f"```{tricky}```"
    assert sweep_filing._inline(trailing) == f"`` {trailing} ``"
    assert tricky in sweep_filing.build_payload(_night(target=tricky)).body


def test_the_reproduce_block_checks_out_the_commit_the_night_ran_against() -> None:
    """A reproduction that lands on a different file closes a live finding.

    The target is not a function of the date alone. It is a function of the date,
    the census *contents* and the file contents: the census is every production
    module, so adding or removing one anywhere in the tree re-resolves which file
    a given date names, and editing a file changes which of its candidates are
    anchorable. `main` moves daily. A triager who pastes the command a week later
    therefore sweeps a different file, sees it come back clean, and closes an
    issue whose finding is still live -- which is worse than no reproduction
    instruction, because it carries the authority of one.

    The commit is what makes the instruction true, so it leads the block.
    """
    body = sweep_filing.build_payload(_night(commit="abc1234")).body

    reproduce = body.split("## Reproduce", 1)[1]
    assert "git checkout abc1234" in reproduce
    assert reproduce.index("git checkout abc1234") < reproduce.index("tools/sweep.py")
    assert "- **Commit:** `abc1234`" in body


def test_a_body_with_no_commit_states_the_precondition_it_cannot_satisfy() -> None:
    """When nothing pinned the tree, the issue has to say so rather than imply it.

    A sweep run by hand has no commit to quote. Printing the bare command there
    would make exactly the claim this pair of tests exists to remove -- that the
    date is enough -- so the intro states the precondition in words instead.
    """
    body = sweep_filing.build_payload(_night(commit=None)).body

    reproduce = body.split("## Reproduce", 1)[1]
    assert "git checkout" not in reproduce
    assert "same commit" in reproduce
    assert "- **Commit:**" not in body


@pytest.mark.parametrize("commit", ["abc1234", None])
def test_no_payload_claims_the_date_alone_fixes_which_mutations_run(commit: str | None) -> None:
    """The false sentence, asserted absent on both shapes rather than replaced once.

    It read "The mutations are a function of the date, so this regenerates
    exactly the ones above". Measured false: the census contents and the file
    contents are inputs too. Checking both the commit and the no-commit body is
    what stops the claim surviving in whichever branch the fix did not touch.
    """
    body = sweep_filing.build_payload(_night(commit=commit)).body

    assert "a function of the date, so this regenerates" not in body
    assert "regenerates exactly the ones above" not in body
    # Naming the mechanism, not just withdrawing the claim: the census *size* is
    # what re-points every date at once, and a reader who is not told that reads
    # "the tree moved" as "someone edited my file".
    assert "census-size" in body
    assert "0 of 30 dates" in body


#: Three markdown constructs GitHub's renderer honours in an issue body, and one
#: backtick run to close a naive code span. Rendered live by the security review
#: through GFM: a working link, a loaded image, a collapsible block.
_HOSTILE_SUMMARY = "1 failed ](evil) <img src=x onerror=1> <details>hidden</details> `` tick"


def test_a_harness_summary_carrying_markdown_lands_as_inert_text() -> None:
    """The verdict line's summary is repository text, and it was undelimited.

    Its two sources are pytest's last printed line -- which quotes whatever a
    test printed or a source line a failure echoed -- and a ``HarnessError``
    string, which carries up to eighty characters of the anchor verbatim
    (``mutate_edits._apply_edit``). So a source line is enough to put live HTML
    in an issue on a tracker, filed unattended by a token with issues:write.

    Asserted on the rendered body rather than on ``_inline``: a check that the
    helper escapes correctly says nothing about whether this caller reached it,
    and not reaching it was the defect.
    """
    outcomes = (
        sweep_verdict.Outcome("__control__", "control-green", 903.2, ""),
        sweep_verdict.Outcome("sweep-a", "SURVIVED", 512.5, _HOSTILE_SUMMARY),
    )

    body = sweep_filing.build_payload(_night(outcomes=outcomes)).body

    line = next(item for item in body.splitlines() if "SURVIVED" in item and "sweep-a" in item)
    assert _HOSTILE_SUMMARY in line
    assert line.count("```") >= 2
    assert line.endswith("```")


def test_an_unknown_verdict_is_delimited_like_every_other_repo_derived_string() -> None:
    """The verdict is read out of the harness's JSON, so it is not a closed set here.

    A record written by a future harness -- or a corrupted one -- can carry any
    string in that field, and it was rendered straight into ``**bold**``. The
    allow-list keeps the ordinary line clean; anything off it is data.
    """
    outcomes = (
        sweep_verdict.Outcome("__control__", "control-green", 903.2, ""),
        sweep_verdict.Outcome("sweep-a", "<img src=x> [click](evil)", 512.5, ""),
    )

    body = sweep_filing.build_payload(_night(outcomes=outcomes)).body

    line = next(item for item in body.splitlines() if "sweep-a" in item)
    assert "`<img src=x> [click](evil)`" in line
    assert "**<img" not in line


@pytest.mark.parametrize("verdict", sorted(sweep_filing.KNOWN_VERDICTS))
def test_a_verdict_the_harness_really_writes_still_reads_as_plain_emphasis(verdict: str) -> None:
    """The allow-list has to admit what the harness actually produces.

    An allow-list that admitted nothing would satisfy the rule above by
    rendering every line as code -- safe, and unreadable. These six are the
    strings ``mutate_run`` writes into the ``verdict`` field.
    """
    outcomes = (sweep_verdict.Outcome("sweep-a", verdict, 1.0, ""),)

    body = sweep_filing.build_payload(_night(outcomes=outcomes)).body

    assert f"- **{verdict}**" in body


def test_the_allow_list_covers_every_verdict_the_sweep_itself_branches_on() -> None:
    """Two modules naming the same strings, pinned against each other.

    ``sweep_verdict`` decides clean-or-file by comparing against ``KILLED``,
    ``control-green`` and the unheld set. If this list drifted from those, a
    legitimate verdict would start rendering as code -- cosmetic, but the first
    sign of the two modules disagreeing about what the harness emits.
    """
    assert sweep_verdict.UNHELD_VERDICTS <= sweep_filing.KNOWN_VERDICTS
    assert {"KILLED", "control-green", "control-red"} <= sweep_filing.KNOWN_VERDICTS


def test_the_dedup_marker_is_a_digest_no_path_can_forge() -> None:
    """The key the comment-instead-of-open decision turns on.

    Embedding the raw path would put repository text inside an HTML comment, and
    a path carrying ``-->`` would close it early -- leaving a marker that matches
    nothing and a body whose first line is half a comment. Two runs on one file
    would then open two issues instead of one thread.
    """
    marker = sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    hostile = sweep_filing.target_marker("packages/-->/x.py")

    assert marker != hostile
    assert "-->" not in hostile[: -len(" -->")]
    assert marker == sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    assert sweep_filing.build_payload(_night()).body.startswith(marker)


def test_an_open_issue_for_the_same_target_is_found_by_its_marker() -> None:
    """Dedup, so a file that survives a mutation on every run grows one thread.

    Matched on the marker rather than on the title: a title carries a count and a
    date, so the same target files a differently titled issue on every run, and
    title matching would open a new issue each time.

    The listing is newest-first, which is the order ``gh issue list`` returns,
    and the expected answer is the *oldest* match. Taking whichever match came
    first would split one file's history across every issue ever opened for it,
    and a fixture in ascending order would not tell the two apart.
    """
    marker = sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    listing = json.dumps(
        [
            {"number": 900, "body": "an unrelated async-sweep issue"},
            {"number": 877, "body": f"{marker}\n\nlast night, on this file"},
            {"number": 812, "body": f"{marker}\n\nthe first night on this file"},
        ]
    )

    assert sweep_filing.existing_issue(listing, marker) == 812


def test_a_marker_quoted_inside_another_issue_body_does_not_claim_the_thread() -> None:
    """Matching anywhere in a body lets one file's issue capture another's findings.

    Reproduced by the security review: a trailing comment on an anchorable source
    line puts the *victim* file's marker inside the diff that the *carrier*
    file's issue quotes. Because the lookup then matched anywhere and
    ``min(matches)`` prefers the oldest number, every later run on the victim
    file would comment on the carrier's thread -- its findings filed under
    another file's title, where nobody triaging that file would look.

    The builder writes the marker as the body's first line, so that is where the
    lookup reads it. The carrier body below is the shape the review produced: a
    legitimate marker of its own, and the victim's marker quoted in a diff.
    """
    victim = sweep_filing.target_marker("packages/theurian-core/src/theurian/victim.py")
    carrier = sweep_filing.target_marker("packages/theurian-core/src/theurian/carrier.py")
    listing = json.dumps(
        [
            {
                "number": 700,
                "body": (
                    f"{carrier}\n\n## Mutations\n\n```diff\n-value = 1  # {victim}\n+value = 2\n```"
                ),
            },
            {"number": 940, "body": f"{victim}\n\nthe victim file's own thread"},
        ]
    )

    assert sweep_filing.existing_issue(listing, victim) == 940
    assert sweep_filing.existing_issue(listing, carrier) == 700


def test_a_marker_below_the_first_line_is_not_the_thread_it_names() -> None:
    """The narrow form of the same rule, with no diff and no second issue.

    A body that merely mentions a marker -- a triager quoting one in prose, a
    cross-reference -- is not that target's thread. Only the position the builder
    writes it to counts, and leading whitespace is tolerated because a body
    round-tripped through the API can acquire it.
    """
    marker = sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    mentioned = json.dumps([{"number": 800, "body": f"see also {marker} for context"}])
    written = json.dumps([{"number": 800, "body": f"\r\n  {marker}\n\nthe body"}])

    assert sweep_filing.existing_issue(mentioned, marker) is None
    assert sweep_filing.existing_issue(written, marker) == 800


def test_no_open_issue_for_this_target_means_a_new_one() -> None:
    """The other branch: a marker nothing carries opens a thread rather than joining one."""
    marker = sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    listing = json.dumps([{"number": 900, "body": "about another file entirely"}])

    assert sweep_filing.existing_issue(listing, marker) is None


def test_a_listing_that_cannot_be_read_stops_the_sweep_rather_than_opening_a_duplicate() -> None:
    """An unreadable listing is not an empty one.

    Reading it as empty would open a second issue for a target that already has
    an open thread, on every run, for as long as ``gh`` kept returning something
    unexpected. Exit 1 is the honest answer.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_filing.existing_issue("<html>rate limited</html>", "<!-- marker -->")
