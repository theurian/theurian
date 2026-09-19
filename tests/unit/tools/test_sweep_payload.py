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

The rest is AC4: the issue has to say which modules, which run, what each
mutation did and what each verdict was, and it has to end with the ratchet stub
that PR-2's process rule anchors on. One run files one issue, so "which modules"
is a section per block member rather than a single target line, and a member
with nothing to mutate is reported rather than skipped over.
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

#: The run 2026-09-16 falls in, computed rather than written down: the marker,
#: the header line and the dedup key all carry it, and a fixture that invented a
#: number would pin the body against a run no date resolves to.
_RUN = sweep_census.run_index(_NIGHT)

_TARGET = "packages/theurian-core/src/theurian/x.py"

#: Every metacharacter that would matter if any of this reached a shell, plus a
#: code fence for the markdown channel. Paths like this do not exist in the
#: census today; the payload builder is not allowed to depend on that.
_HOSTILE_PATH = 'packages/theurian-core/src/theurian/$(id)/`whoami`/"it\'s".py'
_HOSTILE_OLD = "value < 3  # ``` still inside the fence"


def _candidate(label: str, path: str = _TARGET) -> sweep_mutations.Candidate:
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


def _attempt(
    path: str,
    *,
    candidate: sweep_mutations.Candidate | None = None,
    index: int = 0,
    lap: int = 4,
    dropped: int = 0,
) -> sweep_mutations.Attempt:
    """One block slot, in the shape :func:`sweep_mutations.for_block` hands over.

    ``candidate`` of ``None`` is a barren member -- a slot the run kept and
    bought no walk with -- which is a state the single-target record could not
    represent at all and which the body now has a section for.
    """
    return sweep_mutations.Attempt(
        slot=sweep_census.Slot(path=path, index=index, lap=lap),
        generated=sweep_mutations.Generated(
            path=path,
            candidates=() if candidate is None else (candidate,),
            skipped=tuple(
                sweep_mutations.Skip(line=line, swapped_from="<", reason="anchor-not-unique")
                for line in range(dropped)
            ),
        ),
        candidate=candidate,
    )


def _night(  # noqa: PLR0913 - a builder for a ten-field record; every field is varied by a test
    *,
    target: str = _TARGET,
    candidate: sweep_mutations.Candidate | None = None,
    attempts: tuple[sweep_mutations.Attempt, ...] = (),
    outcomes: tuple[sweep_verdict.Outcome, ...] = (),
    reading: sweep_verdict.Reading | None = None,
    mutate_exit: int = 1,
    commit: str | None = "9f2c1ab4d5e6f70819a2b3c4d5e6f7089a1b2c3d",
) -> sweep_filing.Night:
    block = attempts or (
        _attempt(
            target,
            candidate=candidate or _candidate("sweep-2026-09-16-a1e446-00-le-to-lt-l42", target),
            dropped=3,
        ),
    )
    picked = tuple(item.candidate for item in block if item.candidate is not None)
    reported = outcomes or (
        sweep_verdict.Outcome("__control__", "control-green", 903.2, "1 passed"),
        *(sweep_verdict.Outcome(item.label, "SURVIVED", 512.5, "5571 passed") for item in picked),
    )
    return sweep_filing.Night(
        on=_NIGHT,
        run=_RUN,
        attempts=block,
        harness=("uv", "run", "--frozen", "python", "tools/mutate.py"),
        command=("uv", "run", "--frozen", "python", "tools/sweep.py", "--date", "2026-09-16"),
        mutate_exit=mutate_exit,
        outcomes=reported,
        reading=reading
        or sweep_verdict.Reading(
            reason=sweep_verdict.SURVIVORS,
            detail=f"{len(picked)} of {len(picked)} mutation(s) the suite does not hold",
            unheld=tuple(item.label for item in picked),
        ),
        commit=commit,
    )


def test_the_body_names_every_module_the_run_and_every_verdict() -> None:
    """AC4, item by item, because each one is what makes the issue actionable.

    Without the modules and the run, the finding cannot be reproduced -- the date
    and the tree are the whole input to the sweep. Without every verdict, a
    reader cannot tell a block where one of six mutations survived from one where
    five did, and the control's verdict is what says whether any of it counts.
    """
    payload = sweep_filing.build_payload(_night())

    assert sweep_filing.module_heading(_TARGET) in payload.body
    assert f"- **Run:** 2026-09-16 (run {_RUN})" in payload.body
    assert "SURVIVED" in payload.body
    assert "control-green" in payload.body
    assert "sweep-2026-09-16-a1e446-00-le-to-lt-l42" in payload.body


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
    week, a control that keeps timing out -- opened a *new* issue on every run,
    because every run draws a different block. The code review measured 28
    distinct targets over 30 consecutive draws: 28 issues for one cause, which
    also fills the hundred-issue dedup window in about as many runs and silently
    breaks the dedup the survivors filing shares with it.

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


def test_one_run_files_one_issue_that_names_every_module_it_found_a_gap_in() -> None:
    """The unit of a finding is the run, and the modules are inside it.

    Keyed per *target*, a block of six unheld modules would open six issues for
    one night's work, each one a sixth of the evidence and none of them carrying
    the control that says whether any of it counts. Keyed per run, they are one
    issue: one marker, one title saying how many modules, and a ``## Modules``
    section naming each. The title still names the module when there is exactly
    one, because that is the case a triager can act on from the title alone.

    What must not collapse is the *content*: a mutation the suite does not hold
    is a statement about that file's tests, so both paths are asserted present in
    the body even though neither is in the title.
    """
    one = "packages/theurian-core/src/theurian/one.py"
    two = "packages/theurian-core/src/theurian/two.py"
    left, right = _candidate("sweep-one", one), _candidate("sweep-two", two)

    payload = sweep_filing.build_payload(
        _night(attempts=(_attempt(one, candidate=left), _attempt(two, candidate=right, index=1)))
    )
    alone = sweep_filing.build_payload(_night(target=one, candidate=left))

    assert payload.marker == sweep_filing.run_marker(_RUN)
    assert payload.marker != sweep_filing.UNTRUSTED_MARKER
    assert "2 mutation(s) not held in 2 modules" in payload.title
    assert one in payload.body and two in payload.body
    assert one in alone.title


def test_the_standing_untrusted_thread_still_dates_and_names_each_run() -> None:
    """One thread must not mean one indistinguishable pile of comments.

    Whoever picks up the standing issue needs to know which runs are in it and
    what each of them was attacking -- otherwise the constant key trades a
    triage flood for an unreadable log.
    """
    payload = _untrusted("packages/theurian-core/src/theurian/one.py")

    assert f"- **Run:** 2026-09-16 (run {_RUN})" in payload.body
    assert sweep_filing.module_heading("packages/theurian-core/src/theurian/one.py") in payload.body


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
        _night(target=_HOSTILE_PATH, candidate=_candidate("sweep-x", path=_HOSTILE_PATH))
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

    payload = sweep_filing.build_payload(_night(candidate=mutated))

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
    # Counted in runs: 30 consecutive dates are 5 runs under the run index, so
    # the date-counted form of this figure claimed six times its own sample.
    assert "0 of 30 runs" in body


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


def test_the_dedup_marker_is_the_run_number_and_carries_no_repository_text() -> None:
    """The key the comment-instead-of-open decision turns on.

    The digest this replaced existed to keep a path out of an HTML comment: a
    path carrying ``-->`` closes it early, leaving a marker that matches nothing
    and a body whose first line is half a comment. Keyed on the run there is no
    path left to protect -- an integer cannot carry a delimiter -- and the check
    that matters becomes the one below: the marker is a function of the run and
    of nothing else, so a rerun inside the same week joins its own thread and the
    next week opens a new one.

    Pinned as the exact string rather than as "it contains the number". The
    reader that finds an open thread matches this prefix on the body's first
    line, and a marker that gained a space or lost its comment syntax would file
    a duplicate every run while every other assertion here stayed green.
    """
    marker = sweep_filing.run_marker(_RUN)

    assert marker == f"<!-- async-sweep-run: {_RUN} -->"
    assert marker == sweep_filing.run_marker(_RUN)
    assert marker != sweep_filing.run_marker(_RUN + 1)
    assert "-->" not in marker[: -len(" -->")]
    assert sweep_filing.build_payload(_night()).body.startswith(marker)


def test_an_open_issue_for_the_same_run_is_found_by_its_marker() -> None:
    """Dedup, so a rerun inside one week comments rather than opening a second issue.

    Matched on the marker rather than on the title: a title carries a count and a
    date, so a rerun that drew one more survivor files a differently titled issue
    for work already recorded, and title matching would open a new issue each
    time.

    The listing is newest-first, which is the order ``gh issue list`` returns,
    and the expected answer is the *oldest* match. Taking whichever match came
    first would split one run's history across every issue ever opened for it,
    and a fixture in ascending order would not tell the two apart.
    """
    marker = sweep_filing.run_marker(_RUN)
    listing = json.dumps(
        [
            {"number": 900, "body": "an unrelated async-sweep issue"},
            {"number": 877, "body": f"{marker}\n\na later rerun of this run"},
            {"number": 812, "body": f"{marker}\n\nthe first filing for this run"},
        ]
    )

    assert sweep_filing.existing_issue(listing, marker) == 812


def test_a_marker_quoted_inside_another_issue_body_does_not_claim_the_thread() -> None:
    """Matching anywhere in a body lets one issue capture another run's findings.

    Reproduced by the security review against the per-target key: a trailing
    comment on an anchorable source line put one marker inside the diff that
    another issue quotes, the lookup matched anywhere, and ``min(matches)``
    preferred the older number -- so a run's findings were filed under a thread
    that was not its own, where nobody looking for them would read.

    The key is the run now, which narrows what a source line can forge but does
    not change the rule: **every** body quotes repository text inside its diffs,
    and a marker is only this thread's key at the position the builder writes it.
    The carrier body below is the shape the review produced, with the marker of a
    different run quoted in its diff.
    """
    victim = sweep_filing.run_marker(_RUN)
    carrier = sweep_filing.run_marker(_RUN - 1)
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
    marker = sweep_filing.run_marker(_RUN)
    mentioned = json.dumps([{"number": 800, "body": f"see also {marker} for context"}])
    written = json.dumps([{"number": 800, "body": f"\r\n  {marker}\n\nthe body"}])

    assert sweep_filing.existing_issue(mentioned, marker) is None
    assert sweep_filing.existing_issue(written, marker) == 800


def test_no_open_issue_for_this_run_means_a_new_one() -> None:
    """The other branch: a marker nothing carries opens a thread rather than joining one."""
    marker = sweep_filing.run_marker(_RUN)
    listing = json.dumps([{"number": 900, "body": "about another run entirely"}])

    assert sweep_filing.existing_issue(listing, marker) is None


def test_a_listing_that_cannot_be_read_stops_the_sweep_rather_than_opening_a_duplicate() -> None:
    """An unreadable listing is not an empty one.

    Reading it as empty would open a second issue for a target that already has
    an open thread, on every run, for as long as ``gh`` kept returning something
    unexpected. Exit 1 is the honest answer.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_filing.existing_issue("<html>rate limited</html>", "<!-- marker -->")


def test_a_barren_block_member_is_reported_rather_than_left_out_of_the_body() -> None:
    """A slot with nothing to mutate is a fact about the run's reach.

    The single-target form could only express a barren draw by advancing past it,
    so the issue never mentioned it and the census index the date actually named
    went unswept and unrecorded. Leaving it out of the body now would read as a
    five-module block rather than a six-module block with one member that offered
    nothing -- and the difference is exactly the module a reader might otherwise
    believe was tested.
    """
    barren = "packages/theurian-core/src/theurian/constants.py"
    payload = sweep_filing.build_payload(
        _night(
            attempts=(
                _attempt(_TARGET, candidate=_candidate("sweep-one")),
                _attempt(barren, index=1),
            )
        )
    )

    section = payload.body.split(sweep_filing.module_heading(barren), 1)[1]

    assert "- **Block:** 2 module(s), 1 mutated, 1 barren" in payload.body
    assert section.startswith("\n\nCensus index 1, visit 4, 0 candidate(s),")
    assert "bought no walk" in section


def test_a_verdict_nobody_can_attribute_is_reported_outside_every_module_section() -> None:
    """An outcome whose label matches no mutation is a statement about the harness.

    It is the shape of a harness that ran something else -- a stale spec, a
    substituted command -- and dropping it would hide the one signal that says
    so. Crediting it to a module would be worse: ``tools/audit/sweep_kill_rates``
    reads a verdict as belonging to the heading above it, so an orphan left
    inside the last module's section becomes that module's kill or miss.

    Asserted by position, because "the body contains it" is true of both the
    correct rendering and the defect.
    """
    payload = sweep_filing.build_payload(
        _night(
            outcomes=(
                sweep_verdict.Outcome("__control__", "control-green", 903.2, ""),
                sweep_verdict.Outcome("sweep-2026-09-16-a1e446-00-le-to-lt-l42", "KILLED", 1.0, ""),
                sweep_verdict.Outcome("sweep-from-another-spec", "SURVIVED", 2.0, ""),
            )
        )
    )

    body = payload.body

    assert sweep_filing.UNATTRIBUTED_HEADING in body
    assert body.index("sweep-from-another-spec") < body.index(sweep_filing.MODULES_HEADING)
    assert body.index(sweep_filing.UNATTRIBUTED_HEADING) < body.index(sweep_filing.MODULES_HEADING)


def test_the_control_walk_is_the_runs_own_and_not_any_modules() -> None:
    """The control says whether the run counts; it is not evidence about a file.

    Rendered under ``## Control`` and above ``## Modules`` on purpose: the reader
    that folds a per-module kill rate stops at any other ``##`` heading, so a
    control left inside a module's section would credit whichever module sorted
    first with a kill it did not earn.
    """
    body = sweep_filing.build_payload(_night()).body

    assert body.index("## Control") < body.index(sweep_filing.MODULES_HEADING)
    assert body.index("control-green") < body.index(sweep_filing.MODULES_HEADING)


def test_a_module_whose_verdict_never_came_back_says_so_in_its_own_section() -> None:
    """Silence per module, rather than one summary line for the whole run.

    A block where five of six verdicts came back is not a five-mutation run: the
    sixth was asked and went unanswered, which is what the untrusted reading
    exists to surface. The sentence lives in the module's own section because
    that is where a triager looking at that module will be.
    """
    payload = sweep_filing.build_payload(
        _night(
            outcomes=(sweep_verdict.Outcome("__control__", "control-green", 903.2, ""),),
            reading=sweep_verdict.Reading(
                reason=sweep_verdict.UNTRUSTED, detail="1 of 1 mutation(s) reported no verdict"
            ),
        )
    )

    section = payload.body.split(sweep_filing.module_heading(_TARGET), 1)[1]

    assert "No verdict came back for" in section
    assert "cannot be read as a result" in section


def test_the_header_totals_the_candidates_dropped_across_the_whole_block() -> None:
    """A drop is invisible in the body otherwise, and it bounds what the run asked.

    The generator drops a candidate whose anchor is not unique -- 506 of them
    across the census, measured 2026-09-19 at ``92581f77`` -- and a reader who is
    not told how many cannot tell a module the sweep asked one question of from
    one it could only ask one question of. The number is a sum over the block
    now, not a field the driver passes: a per-attempt total that silently
    reported only the first member's drops would read as a cleaner census than
    this one is.
    """
    payload = sweep_filing.build_payload(
        _night(
            attempts=(
                _attempt(_TARGET, candidate=_candidate("sweep-one"), dropped=3),
                _attempt("packages/theurian-core/src/theurian/two.py", index=1, dropped=4),
            )
        )
    )

    assert "- **Candidates dropped for a non-unique anchor:** 7" in payload.body
