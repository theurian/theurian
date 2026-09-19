"""The sweep driver end to end, with the suite and the tracker stood in for (#378).

Everything between the two boundaries is real here: the census walk over the
production tree, the block the date selects, the generator, the spec file that is
written to disk, the argv handed to the harness, the results document that is
read back, and the payload that is printed or filed. Only the two expensive edges
are injected -- ``tools/mutate.py`` (a full suite walk per mutation, tens of
minutes) and ``gh`` (the network, and a token with issue-write scope).

The contract this pins is the one the workflow consumes:

    tools/sweep.py --date YYYY-MM-DD --block-size N --json PATH [--dry-run]
                   [--repo owner/repo]

with **exit 0 when the sweep completed and any filing succeeded** -- findings are
a successful sweep -- and **exit 1 only when the sweep itself could not run or
could not file**. The workflow alarms on 1, so a night that misreports its own
failure as success is the failure mode with no witness.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import sweep
import sweep_census
import sweep_filing

pytestmark = pytest.mark.integration

_NIGHT = "2026-09-16"


class _FakeMutate:
    """Stands in for ``tools/mutate.py``: records its argv, writes a results file."""

    def __init__(self, *, exit_code: int = 0, verdicts: str = "KILLED", write: bool = True) -> None:
        self.argv: tuple[str, ...] = ()
        self.spec: list[dict[str, str]] = []
        self.exit_code = exit_code
        self.verdicts = verdicts
        self.write = write

    def __call__(self, argv: Sequence[str]) -> int:
        self.argv = tuple(argv)
        spec_path = Path(self.argv[self.argv.index("--spec") + 1])
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if self.write:
            outcomes = [
                {
                    "label": "__control__",
                    "verdict": "control-green",
                    "seconds": 900.0,
                    "summary": "5571 passed",
                }
            ]
            outcomes += [
                {
                    "label": entry["label"],
                    "verdict": self.verdicts,
                    "seconds": 500.0,
                    "summary": "5571 passed" if self.verdicts == "SURVIVED" else "1 failed",
                }
                for entry in self.spec
            ]
            results = Path(self.argv[self.argv.index("--json") + 1])
            results.write_text(
                json.dumps({"options": {"workers": 2}, "outcomes": outcomes}), encoding="utf-8"
            )
        return self.exit_code


class _FakeGh:
    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self.returncode = returncode

    def __call__(self, argv: Sequence[str], stdin: str) -> sweep_filing.CommandResult:
        self.calls.append((tuple(argv), stdin))
        listing = "[]" if tuple(argv)[2] == "list" else ""
        return sweep_filing.CommandResult(self.returncode, listing, "gh said no")


def _argv(results: Path, *extra: str, block_size: int = 2) -> list[str]:
    return [
        "--date",
        _NIGHT,
        "--block-size",
        str(block_size),
        "--json",
        str(results),
        *extra,
    ]


def test_a_night_where_every_mutation_died_over_a_green_control_files_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC3. The one outcome that is allowed to be quiet, and it has to be quiet.

    A sweep that filed on a clean night would put an issue on the tracker on
    every single run, and the label would be worthless inside a handful of them.
    ``gh`` is asserted never to have been called at all, rather than merely that
    nothing was created: a listing call on a clean night is a token spent for
    nothing.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    code = sweep.main(_argv(tmp_path / "r.json"), mutate_runner=mutate, gh_runner=gh)

    assert code == 0
    assert gh.calls == []
    assert "no finding" in capsys.readouterr().out


def test_a_surviving_mutation_prints_the_whole_payload_under_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC4 through the CLI: the title and the body, on stdout, with nothing filed.

    ``--dry-run`` is what makes this driver runnable by a human on a laptop, so
    it must reach the same payload the real path would file -- and must not touch
    ``gh`` even to list issues, because a dry run that needed credentials would
    not be usable where it is needed.
    """
    mutate, gh = _FakeMutate(exit_code=1, verdicts="SURVIVED"), _FakeGh()

    code = sweep.main(_argv(tmp_path / "r.json", "--dry-run"), mutate_runner=mutate, gh_runner=gh)

    printed = capsys.readouterr().out
    assert code == 0
    assert gh.calls == []
    assert "async sweep: 2 mutation(s) not held in" in printed
    assert sweep_filing.AUTOMATION_INSTRUCTION in printed
    assert "```diff" in printed
    # The body prints the harness argv it was actually given, so the `--with-git`
    # the default carries has to survive into the issue: a reader who reproduces
    # the batch without it gets a control-red and no verdict (#527).
    assert "- **Harness:** `uv run --frozen python" in printed
    assert "tools/mutate.py --with-git`" in printed


def test_an_untrusted_harness_run_files_rather_than_reading_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC5. Exit 2 from the harness is a finding about the sweep, and it must surface.

    Every KILLED verdict above an exit 2 is meaningless, so the night has nothing
    to report and must say so loudly. The sweep's own exit code stays 0: it did
    its job, which was to notice.
    """
    mutate, gh = _FakeMutate(exit_code=2), _FakeGh()

    code = sweep.main(_argv(tmp_path / "r.json", "--dry-run"), mutate_runner=mutate, gh_runner=gh)

    printed = capsys.readouterr().out
    assert code == 0
    assert sweep_filing.UNTRUSTED_TITLE in printed
    assert sweep_filing.UNTRUSTED_MARKER in printed


def test_an_untrusted_run_that_wrote_no_results_file_still_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The harness can exit 2 before a single verdict exists.

    A refused anchor or a tree that would not build raises before any suite runs,
    so there is no document to read. Requiring one here would turn the loudest
    failure the harness has into a crash in the driver -- and the crash would
    exit 1, which reads as "the sweep failed" rather than "the sweep found
    something".
    """
    mutate, gh = _FakeMutate(exit_code=2, write=False), _FakeGh()

    code = sweep.main(_argv(tmp_path / "r.json", "--dry-run"), mutate_runner=mutate, gh_runner=gh)

    assert code == 0
    assert sweep_filing.UNTRUSTED_TITLE in capsys.readouterr().out


def test_a_harness_that_claims_success_but_wrote_nothing_fails_the_sweep(
    tmp_path: Path,
) -> None:
    """Exit 0 with no record is not a clean night; it is a night with no evidence.

    This is the silent stop at the driver's own boundary. Reading the absent file
    as "no outcomes" would route into the untrusted path and file an issue, which
    is wrong in the other direction: nothing was swept, so there is nothing to
    triage. Exit 1 is what the workflow alarms on.
    """
    mutate, gh = _FakeMutate(exit_code=0, write=False), _FakeGh()

    code = sweep.main(_argv(tmp_path / "r.json"), mutate_runner=mutate, gh_runner=gh)

    assert code == 1
    assert gh.calls == []


def test_a_finding_that_reaches_the_tracker_is_a_successful_sweep(tmp_path: Path) -> None:
    """Exit 0 on a filed finding, because finding something is the job.

    A workflow that alarmed on findings would be muted within a month. It alarms
    on the sweep failing, which is why this distinction is in the exit code
    rather than in the log.
    """
    mutate, gh = _FakeMutate(exit_code=1, verdicts="SURVIVED"), _FakeGh()

    code = sweep.main(
        _argv(tmp_path / "r.json", "--repo", "theurian/theurian"),
        mutate_runner=mutate,
        gh_runner=gh,
    )

    assert code == 0
    assert [call[0][2] for call in gh.calls] == ["list", "create"]
    assert sweep_filing.AUTOMATION_INSTRUCTION in gh.calls[-1][1]


def test_a_finding_that_could_not_be_filed_fails_the_sweep(tmp_path: Path) -> None:
    """The case the alarm exists for: a real finding that no human will ever see."""
    mutate, gh = _FakeMutate(exit_code=1, verdicts="SURVIVED"), _FakeGh(returncode=1)

    code = sweep.main(_argv(tmp_path / "r.json"), mutate_runner=mutate, gh_runner=gh)

    assert code == 1


def test_the_spec_handed_to_the_harness_anchors_in_the_real_target_file(
    tmp_path: Path,
) -> None:
    """The join between the generator and the harness, checked on the real file.

    ``tools/mutate.py`` refuses an anchor that does not match exactly once, and
    it refuses it *after* building an isolated tree and syncing a virtualenv. The
    uniqueness rule is unit-tested against the generator's output; this is the
    only check that the string which actually reaches the spec file still
    satisfies it against the file on disk.

    The count is bounded rather than pinned: which two modules a block of two
    draws is a function of the census, and a module added anywhere re-points it.
    What is invariant is the budget -- at most one mutation per block member, so
    never more entries than slots, one file each -- and that the spec is not
    empty, which is what keeps the loop below from passing over nothing.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    sweep.main(_argv(tmp_path / "r.json"), mutate_runner=mutate, gh_runner=gh)

    assert 1 <= len(mutate.spec) <= 2
    assert len({entry["file"] for entry in mutate.spec}) == len(mutate.spec)
    for entry in mutate.spec:
        source = (sweep_census.REPO_ROOT / entry["file"]).read_text(encoding="utf-8")
        assert source.count(entry["old"]) == 1, entry["label"]
        assert entry["old"] != entry["new"]


def test_the_harness_is_told_where_to_write_and_how_many_workers_to_use(
    tmp_path: Path,
) -> None:
    """The three arguments the batch's cost and readability depend on.

    An absolute results path, because the harness runs with its own working
    directory and a relative one would land somewhere nobody looks. A worker
    count, because the harness's own default is four and this job runs on four
    cores -- and because the harness scales its hang timeout with whatever it is
    given, so an unstated worker count is also an unstated timeout.
    """
    mutate, gh = _FakeMutate(), _FakeGh()
    results = tmp_path / "r.json"

    sweep.main(_argv(results), mutate_runner=mutate, gh_runner=gh)

    assert mutate.argv[mutate.argv.index("--json") + 1] == str(results.resolve())
    assert Path(mutate.argv[mutate.argv.index("--spec") + 1]).is_absolute()
    # The literal, not `str(sweep.WORKERS)`: an assertion built from the constant
    # it checks agrees with that constant whatever it says, which is the shape
    # that let `--with-git` go missing once already.
    assert mutate.argv[mutate.argv.index("--workers") + 1] == "2"
    assert sweep.WORKERS == 2


def test_a_relative_results_path_is_resolved_before_the_harness_sees_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harness runs in the checkout, so a relative path means two directories.

    ``tools/mutate.py`` writes ``--json`` relative to its own working directory,
    which is the checkout, while the sweep read it relative to the caller's. A
    workflow step invoked with ``--json sweep.json`` would therefore write the
    record into the repository and look for it where it was launched -- finding
    nothing, reading that as "the harness wrote no results", and failing a night
    that had in fact run seven full suites.

    ``tmp_path`` is already absolute, so only an explicitly relative argument
    reaches this. The working directory is moved into ``tmp_path`` for the same
    reason the CLI fences exist: a relative path resolved against this checkout
    would write into it.
    """
    monkeypatch.chdir(tmp_path)
    mutate, gh = _FakeMutate(), _FakeGh()

    sweep.main(
        ["--date", _NIGHT, "--block-size", "2", "--json", "run.json"],
        mutate_runner=mutate,
        gh_runner=gh,
    )

    handed = Path(mutate.argv[mutate.argv.index("--json") + 1])
    assert handed.is_absolute()
    assert handed == tmp_path / "run.json"


def test_the_reproduction_command_cannot_file_on_its_reader_s_behalf(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue prints a command written to be pasted, so it must be safe to paste.

    Whoever reproduces a finding runs that line with their own credentials.
    Without `--dry-run` the driver would file at the end of it -- a second issue
    on the same thread, authored by the triager, every time anybody checks.
    Deleting the flag from `_reproduction` currently changes nothing that any
    test reads, which is why the pin is on the rendered block and not on the
    function.
    """
    mutate, gh = _FakeMutate(exit_code=1, verdicts="SURVIVED"), _FakeGh()

    sweep.main(_argv(tmp_path / "r.json", "--dry-run"), mutate_runner=mutate, gh_runner=gh)

    printed = capsys.readouterr().out
    block = printed.split("## Reproduce", 1)[1].split("```sh", 1)[1].split("```", 1)[0]
    assert block.strip().endswith("--dry-run")
    assert "tools/sweep.py" in block


def test_a_previous_run_s_results_are_not_read_as_this_run_s(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A harness that exits without writing must not inherit the previous record.

    The workflow writes the record to a fixed name, so a rerun on a runner with
    a cached workspace -- or any local rerun -- finds the previous run's file
    already there. A harness that then exits 2 before writing anything leaves the
    driver reading a complete, well-formed document belonging to another run, and
    the filed issue reports its verdicts as this run's. Fabricated evidence, in
    the one artifact the whole job exists to produce.

    Asserted on the label, because that is the part a reader would act on.
    """
    results = tmp_path / "r.json"
    results.write_text(
        json.dumps(
            {
                "options": {"workers": 2},
                "outcomes": [
                    {
                        "label": "__control__",
                        "verdict": "control-green",
                        "seconds": 1.0,
                        "summary": "",
                    },
                    {
                        "label": "sweep-from-a-night-that-is-over",
                        "verdict": "SURVIVED",
                        "seconds": 1.0,
                        "summary": "",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    mutate, gh = _FakeMutate(exit_code=2, write=False), _FakeGh()

    code = sweep.main(_argv(results, "--dry-run"), mutate_runner=mutate, gh_runner=gh)

    printed = capsys.readouterr().out
    assert code == 0
    assert "sweep-from-a-night-that-is-over" not in printed
    assert sweep_filing.UNTRUSTED_TITLE in printed


def test_two_dry_runs_of_one_night_print_the_same_thing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC1 at the level a human actually observes it.

    The unit tests pin the census, the rotation and the generator separately.
    Only running the whole driver twice shows whether anything between them --
    a temporary filename, a dict iteration, the order the harness reported --
    reached the output.
    """
    first_mutate, second_mutate = (
        _FakeMutate(exit_code=1, verdicts="SURVIVED"),
        _FakeMutate(exit_code=1, verdicts="SURVIVED"),
    )

    sweep.main(_argv(tmp_path / "a.json", "--dry-run"), mutate_runner=first_mutate, gh_runner=None)
    first = capsys.readouterr().out
    sweep.main(_argv(tmp_path / "b.json", "--dry-run"), mutate_runner=second_mutate, gh_runner=None)
    second = capsys.readouterr().out

    assert first.replace("a.json", "r.json") == second.replace("b.json", "r.json")
    assert first_mutate.spec == second_mutate.spec


def test_a_substituted_harness_is_named_even_on_a_night_that_files_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--mutate-cmd`` exists for tests and rehearsals, and it can lie by omission.

    A run whose verdicts came from something other than this repository's suite
    is not evidence about this repository's suite, and "0 findings" from such a
    run reads identically to the real thing. The substitution is therefore
    announced on stdout on every path, including the quiet one.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    code = sweep.main(
        _argv(tmp_path / "r.json", "--mutate-cmd", "python /tmp/not-the-harness.py"),
        mutate_runner=mutate,
        gh_runner=gh,
    )

    printed = capsys.readouterr().out
    assert code == 0
    assert "not-the-harness.py" in printed
    assert "substituted" in printed


def test_the_default_harness_is_the_real_one_and_lends_it_a_git(tmp_path: Path) -> None:
    """The unattended night runs ``tools/mutate.py``, and runs it so it can answer.

    A default pointing anywhere else would make every unattended run a
    rehearsal, and nothing in the output of a rehearsal says which repository's
    suite it measured.

    ``--with-git`` is the half that is easy to drop and impossible to notice
    without running the real harness. The copy the harness builds has no ``.git``,
    and since the compliance census landed ``claim_surfaces.repo_root`` raises
    there instead of skipping: measured 2026-09-16 in a default prepared tree,
    ``tests/integration/audit/test_census_audits_run.py`` comes back 11 failed --
    the count issue #527 recorded -- which turns the control RED and makes the
    harness exit 2. Every run would then file ``run-untrusted`` and none would
    carry a verdict.

    The flag is asserted as a literal rather than through
    ``DEFAULT_MUTATE_COMMAND``. Comparing the argv against the constant it was
    built from agrees with that constant whatever it says, so it is the line
    above that would stay green if the flag were dropped again.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    sweep.main(_argv(tmp_path / "r.json"), mutate_runner=mutate, gh_runner=gh)

    assert mutate.argv[: len(sweep.DEFAULT_MUTATE_COMMAND)] == sweep.DEFAULT_MUTATE_COMMAND
    assert [item for item in mutate.argv if item.endswith("tools/mutate.py")]
    assert "--with-git" in mutate.argv


def test_a_run_asked_for_an_empty_block_fails_rather_than_reporting_a_clean_run(
    tmp_path: Path,
) -> None:
    """``--block-size 0`` consumes no census slot, so it cannot answer anything.

    The harness is never started. Exiting 0 here would be the purest silent stop
    available: a workflow with a typo in one numeric argument would report a
    clean sweep on every run for as long as nobody read the log -- and an empty
    spec comes back from the real harness with a green control and no mutation,
    which is what that clean line would be built from.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    code = sweep.main(_argv(tmp_path / "r.json", block_size=0), mutate_runner=mutate, gh_runner=gh)

    assert code == 1
    assert mutate.argv == ()


def test_the_commit_the_workflow_passes_reaches_the_filed_issue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow knows the sha; the issue is the only place it can survive to.

    `--commit` exists because the reproduction instruction is false without it:
    the target is resolved against the census *contents*, so a triager working a
    week later sweeps a different file unless the issue says which tree to stand
    on. A flag the driver accepted and dropped on the floor would leave the
    instruction exactly as wrong as before, which is why this asserts on the
    rendered body and not on the parsed arguments.
    """
    mutate, gh = _FakeMutate(exit_code=1, verdicts="SURVIVED"), _FakeGh()
    sha = "0123456789abcdef0123456789abcdef01234567"

    code = sweep.main(
        _argv(tmp_path / "r.json", "--dry-run", "--commit", sha),
        mutate_runner=mutate,
        gh_runner=gh,
    )

    printed = capsys.readouterr().out
    assert code == 0
    assert f"- **Commit:** `{sha}`" in printed
    assert f"git checkout {sha}" in printed


@pytest.mark.parametrize(
    "commit",
    [
        "0123456",
        "0123456789abcdef0123456789abcdef01234567",
        "abcdef7",
    ],
)
def test_a_well_shaped_commit_is_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], commit: str
) -> None:
    """Seven hex digits is git's own abbreviation floor; forty is a full sha-1.

    Both ends are accepted because both are what a caller legitimately has: a
    workflow passes `${{ github.sha }}` in full, a human pastes what `git log
    --oneline` printed.
    """
    mutate, gh = _FakeMutate(exit_code=1, verdicts="SURVIVED"), _FakeGh()

    code = sweep.main(
        _argv(tmp_path / "r.json", "--dry-run", "--commit", commit),
        mutate_runner=mutate,
        gh_runner=gh,
    )

    assert code == 0
    assert f"git checkout {commit}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "commit",
    [
        "012345",
        "0123456789abcdef0123456789abcdef012345678",
        "0123456ABCDEF",
        "0123456;rm -rf ~",
        "0123456 && echo pwned",
        "$(id)abc",
        "main",
        "",
    ],
)
def test_a_commit_that_is_not_a_commit_fails_the_sweep(tmp_path: Path, commit: str) -> None:
    """The one repo-derived string that reaches the body *inside a shell block*.

    Everything else the payload carries is delimited as data and read by a human;
    `git checkout <sha>` is an instruction written to be pasted into a shell. So
    this value is not escaped, it is *refused* unless it is 7-40 lowercase hex --
    a shape with no metacharacter in it at all. Too short is refused as well,
    because six hex digits is below git's abbreviation floor and an ambiguous
    prefix makes the instruction fail in a way that reads like the finding.

    The harness is never started: a night that cannot write a true reproduction
    instruction should not spend two hours earning the right to write a false
    one.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    code = sweep.main(
        _argv(tmp_path / "r.json", "--dry-run", "--commit", commit),
        mutate_runner=mutate,
        gh_runner=gh,
    )

    assert code == 1
    assert mutate.argv == ()


def test_a_date_that_is_not_a_date_fails_the_sweep(tmp_path: Path) -> None:
    """The target is a function of the date, so an unparseable date selects nothing.

    Handled as a sweep failure rather than as a usage error, because the caller
    is a workflow: exit 1 is the code its alarm already watches.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    code = sweep.main(
        ["--date", "last-tuesday", "--block-size", "2", "--json", str(tmp_path / "r.json")],
        mutate_runner=mutate,
        gh_runner=gh,
    )

    assert code == 1
    assert mutate.argv == ()


def test_the_run_reports_the_walks_it_will_spend_against_the_budget_it_has(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The cost model is the reason for the block size, and it is printed once.

    A run buys seven full suite walks: one shared control plus one per mutation.
    That arithmetic is what sets the workflow's ``timeout-minutes``, and it is
    computed from the two constants rather than written down -- so a block of two
    must report three, not the seven the shipped default would spend. A line that
    printed the ceiling regardless of what the run actually asked would make an
    over-budget batch look in-budget in the only place anyone reads it.
    """
    mutate, gh = _FakeMutate(), _FakeGh()

    sweep.main(_argv(tmp_path / "r.json"), mutate_runner=mutate, gh_runner=gh)

    printed = capsys.readouterr().out
    assert f"budget    {1 + len(mutate.spec)} walk(s) of at most 3" in printed
    assert sweep.walk_budget(2) == 3


def test_a_barren_block_member_is_announced_and_costs_the_run_no_walk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The slot the single-target form could only express by advancing past it.

    Driven with the whole census as one block, because that is the only way to
    reach a barren member without pinning which modules a small block happens to
    draw -- the census is recomputed every run, so any smaller fixture is one
    added module away from drawing six productive files. 24 of the 142 modules
    are barren, measured 2026-09-19 at ``92581f77``; a census with none at all
    would fail this rather than silently passing over the branch, which is the
    honest failure.

    The two assertions are the two halves: the run *says* the slot was barren,
    and the slot buys no walk -- the spec is one entry per productive member and
    the freed walk is not handed to another file's second question.
    """
    census = sweep_census.census()
    mutate, gh = _FakeMutate(), _FakeGh()

    sweep.main(
        _argv(tmp_path / "r.json", block_size=len(census)), mutate_runner=mutate, gh_runner=gh
    )

    printed = capsys.readouterr().out
    assert "barren" in printed
    assert len(mutate.spec) < len(census)
    assert f"budget    {1 + len(mutate.spec)} walk(s) of at most {1 + len(census)}" in printed
