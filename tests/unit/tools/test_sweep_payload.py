"""What the nightly sweep files, and why none of it can be a command (#378).

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


def _night(
    *,
    target: str = "packages/theurian-core/src/theurian/x.py",
    candidates: tuple[sweep_mutations.Candidate, ...] = (),
    outcomes: tuple[sweep_verdict.Outcome, ...] = (),
    reading: sweep_verdict.Reading | None = None,
    mutate_exit: int = 1,
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
    """
    payload = sweep_filing.build_payload(_night())

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
    """
    payload = sweep_filing.build_payload(
        _night(
            mutate_exit=2,
            reading=sweep_verdict.Reading(
                reason=sweep_verdict.UNTRUSTED, detail="the harness exited 2"
            ),
        )
    )

    assert sweep_verdict.UNTRUSTED in payload.title
    assert "exited 2" in payload.body


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

    Any Python file may contain three backticks -- this repository's own
    docstrings do. Rendered inside a three-backtick fence, such a line closes it,
    and everything after it (including the ratchet stub) renders as prose. The
    fence is therefore sized against the content rather than fixed.
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


def test_the_dedup_marker_is_a_digest_no_path_can_forge() -> None:
    """The key the comment-instead-of-open decision turns on.

    Embedding the raw path would put repository text inside an HTML comment, and
    a path carrying ``-->`` would close it early -- leaving a marker that matches
    nothing and a body whose first line is half a comment. Two nights on one file
    would then open two issues instead of one thread.
    """
    marker = sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    hostile = sweep_filing.target_marker("packages/-->/x.py")

    assert marker != hostile
    assert "-->" not in hostile[: -len(" -->")]
    assert marker == sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    assert sweep_filing.build_payload(_night()).body.startswith(marker)


def test_an_open_issue_for_the_same_target_is_found_by_its_marker() -> None:
    """Dedup, so a file that survives a mutation every night grows one thread.

    Matched on the marker rather than on the title: a title carries a count and a
    date, so the same target files a differently titled issue every night, and
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


def test_no_open_issue_for_this_target_means_a_new_one() -> None:
    """The other branch: a marker nothing carries opens a thread rather than joining one."""
    marker = sweep_filing.target_marker("packages/theurian-core/src/theurian/x.py")
    listing = json.dumps([{"number": 900, "body": "about another file entirely"}])

    assert sweep_filing.existing_issue(listing, marker) is None


def test_a_listing_that_cannot_be_read_stops_the_sweep_rather_than_opening_a_duplicate() -> None:
    """An unreadable listing is not an empty one.

    Reading it as empty would open a second issue for a target that already has
    an open thread, every night, for as long as ``gh`` kept returning something
    unexpected. Exit 1 is the honest answer.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_filing.existing_issue("<html>rate limited</html>", "<!-- marker -->")
