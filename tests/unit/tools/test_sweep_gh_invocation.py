"""How the sweep talks to ``gh``: argv only, body on stdin, never a shell (#378).

The scheduled job runs on a runner holding a token with issue-write scope, and
everything it puts in an issue it read out of the repository -- a path from a
directory walk, two slices of somebody's source. If any of that were assembled
into a shell string, a file named ``$(...)`` would be a command.

So this file asserts the shape of the call rather than the content of the issue
(``test_sweep_payload.py`` covers that): each string is its own argv element,
the body never appears in argv at all, and no call site in ``tools/sweep*.py``
passes ``shell=True``. ``tests/integration/tools/test_sweep_no_shell.py`` then
proves the same thing by running a real subprocess.

No test here reaches the network or the real ``gh``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
import sweep_census
import sweep_filing
import sweep_verdict

pytestmark = pytest.mark.unit

_HOSTILE_PATH = 'packages/theurian-core/src/theurian/$(id)/`whoami`/"it\'s".py'


class _RecordingGh:
    """Stands in for ``gh``, remembering exactly how it was called."""

    def __init__(self, listing: str = "[]", fails: str | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self.listing = listing
        #: Which subcommand comes back non-zero -- one at a time, because the
        #: list call and the write call are separate branches of the filing and
        #: a stand-in that failed both would only ever exercise the first.
        self.fails = fails

    def __call__(self, argv: Sequence[str], stdin: str) -> sweep_filing.CommandResult:
        self.calls.append((tuple(argv), stdin))
        subcommand = tuple(argv)[2]
        listing = self.listing if subcommand == "list" else ""
        failed = 1 if subcommand == self.fails else 0
        return sweep_filing.CommandResult(returncode=failed, stdout=listing, stderr="gh said no")


def _payload(target: str = "packages/theurian-core/src/theurian/x.py") -> sweep_filing.Payload:
    return sweep_filing.Payload(
        title=f"async sweep: 1 mutation not held in {target} (2026-09-16)",
        body=f"{sweep_filing.target_marker(target)}\n\nbody text\n",
        marker=sweep_filing.target_marker(target),
    )


def test_a_hostile_path_reaches_gh_as_one_argument_and_not_as_a_command() -> None:
    """AC6's process half: the title is data, whatever it spells.

    Asserted as *identity with one element* rather than as "appears somewhere":
    a string split across two argv elements has been through word splitting, and
    a string that appears in a single element which also holds other flags has
    been concatenated into one. Either would mean a shell had a say.
    """
    gh = _RecordingGh()
    payload = _payload(_HOSTILE_PATH)

    sweep_filing.file_finding(payload, repo="theurian/theurian", runner=gh, gh="/usr/bin/gh")

    argv, _ = gh.calls[-1]
    assert payload.title in argv
    assert argv.count(payload.title) == 1
    assert not [item for item in argv if item != payload.title and _HOSTILE_PATH in item]


def test_the_issue_body_travels_on_stdin_and_never_through_argv() -> None:
    """A body is thousands of bytes of somebody's source code.

    On argv it would be visible in the runner's process list, would meet the
    platform's argument-length limit, and would be one more string an operator
    might be tempted to quote by hand. ``--body-file -`` moves all three
    problems off the command line.
    """
    gh = _RecordingGh()
    payload = _payload()

    sweep_filing.file_finding(payload, repo="theurian/theurian", runner=gh, gh="/usr/bin/gh")

    argv, stdin = gh.calls[-1]
    assert "--body-file" in argv
    assert argv[argv.index("--body-file") + 1] == "-"
    assert stdin == payload.body
    assert payload.body not in argv


def test_an_existing_thread_is_commented_on_rather_than_duplicated() -> None:
    """The dedup branch, checked through the call and not only through the lookup.

    A target that survives a mutation on every run would otherwise open an issue
    on every run, and the third one would be triaged as a new finding.
    """
    target = "packages/theurian-core/src/theurian/x.py"
    listing = json.dumps([{"number": 812, "body": sweep_filing.target_marker(target)}])
    gh = _RecordingGh(listing=listing)

    sweep_filing.file_finding(_payload(), repo="theurian/theurian", runner=gh, gh="/usr/bin/gh")

    argv, _ = gh.calls[-1]
    assert argv[1:4] == ("issue", "comment", "812")
    assert "create" not in argv


def test_the_label_is_the_one_the_tracker_already_has() -> None:
    """``async-sweep`` exists; the sweep does not create labels.

    ``gh issue create --label`` fails outright on an unknown label, so a typo
    here is a scheduled job that never files anything -- the silent-stop shape,
    arriving through a string constant.
    """
    gh = _RecordingGh()

    sweep_filing.file_finding(_payload(), repo="theurian/theurian", runner=gh, gh="/usr/bin/gh")

    listed, created = gh.calls[0][0], gh.calls[-1][0]
    assert sweep_filing.LABEL == "async-sweep"
    assert listed[listed.index("--label") + 1] == "async-sweep"
    assert created[created.index("--label") + 1] == "async-sweep"


def test_a_repository_is_passed_when_given_and_omitted_when_not() -> None:
    """``--repo`` is how the workflow aims the filing; locally, gh infers it.

    Passing an empty ``--repo`` would be worse than omitting it: gh reads it as a
    malformed repository and fails, so a night without the flag would file
    nothing and say so only in a log.
    """
    with_repo = _RecordingGh()
    without_repo = _RecordingGh()

    sweep_filing.file_finding(_payload(), repo="theurian/theurian", runner=with_repo, gh="gh")
    sweep_filing.file_finding(_payload(), repo=None, runner=without_repo, gh="gh")

    assert "--repo" in with_repo.calls[-1][0]
    assert with_repo.calls[-1][0][with_repo.calls[-1][0].index("--repo") + 1] == "theurian/theurian"
    assert "--repo" not in without_repo.calls[-1][0]
    assert "" not in without_repo.calls[-1][0]


@pytest.mark.parametrize("failing", ["list", "create", "comment"])
def test_a_failing_gh_call_is_a_failed_sweep_and_not_a_filed_finding(failing: str) -> None:
    """AC's exit-code half: "filed" has to mean the tracker actually took it.

    A night that found a surviving mutation and could not file it has produced
    nothing a human will ever see. Returning success there would make the
    workflow's alarm -- which fires on exit 1 -- silent in exactly the case it
    exists for.

    Each of the three calls is failed on its own. A stand-in that failed every
    call would stop at the listing every time and leave the two write branches
    unchecked, which is the same fixture-steering defect the sweep exists to
    find in other people's tests.
    """
    target = "packages/theurian-core/src/theurian/x.py"
    thread = json.dumps([{"number": 812, "body": sweep_filing.target_marker(target)}])
    gh = _RecordingGh(listing=thread if failing == "comment" else "[]", fails=failing)

    with pytest.raises(sweep_census.SweepError):
        sweep_filing.file_finding(_payload(), repo=None, runner=gh, gh="gh")

    assert gh.calls[-1][0][2] == failing


def test_no_module_in_the_sweep_ever_asks_for_a_shell() -> None:
    """The one check that covers call sites nobody thought to test.

    ``shell=True`` anywhere in these modules would undo every assertion above,
    and it is one keyword away at every ``subprocess`` call. Read off the source
    because that is the property -- there is no runtime observation that can
    say "and nowhere else".
    """
    modules = sorted(Path(sweep_census.REPO_ROOT / "tools").glob("sweep*.py"))

    assert len(modules) >= 4
    for module in modules:
        assert "shell=True" not in module.read_text(encoding="utf-8"), module.name


def test_the_night_a_body_describes_is_the_night_that_was_run() -> None:
    """A guard against the payload being built from a different night's record.

    Cheap, and it is the shape a refactor produces: the driver holds the date in
    two places (the CLI argument and the generated labels), and a builder handed
    the wrong one files an issue nobody can reproduce.

    Asserted on the body alone. This night is untrusted, and every untrusted run
    shares one standing title with no date in it -- so the body is the only place
    the date can be, and the only place it needs to be: the thread collects runs,
    and each comment has to say which one it is.
    """
    payload = sweep_filing.build_payload(
        sweep_filing.Night(
            on=date(2026, 9, 16),
            target="packages/theurian-core/src/theurian/x.py",
            harness=("uv", "run", "python", "tools/mutate.py"),
            command=("uv", "run", "python", "tools/sweep.py"),
            mutate_exit=2,
            picked=(),
            outcomes=(),
            reading=sweep_verdict.Reading(
                reason="run-untrusted", detail="the harness exited 2 before any suite ran"
            ),
            skipped=0,
        )
    )

    assert payload.title == sweep_filing.UNTRUSTED_TITLE
    assert "2026-09-16" in payload.body
