"""The scheduled red-team sweep: a block of files, one mutation each, one issue.

CLAUDE.md's blast-radius table routes open-ended adversarial depth to a standing
async red-team sweep over ``main``, whose findings enter filing-time triage like
any other. This is the driver for that sweep's mutation leg (issue #378); a
separate workflow holds the cadence and schedules it.

Usage
-----
::

    uv run --frozen python tools/sweep.py \\
        --date 2026-09-20 --block-size 6 --json /tmp/sweep.json [--dry-run]

What one run does, in order: take the ``--block-size`` census slots the date
selects (:func:`sweep_census.block`), generate one anchorable mutation for each
by its visit number (:func:`sweep_mutations.for_block`), hand the whole block to
``tools/mutate.py`` as a single spec, read what came back
(:mod:`sweep_verdict`), and either say nothing or file one issue under the
``async-sweep`` label (:mod:`sweep_filing`).

Exit codes, which the workflow's alarm depends on
-------------------------------------------------
``0``
    The sweep completed, and anything it needed to file was filed. **A finding
    is a successful sweep.** A workflow that alarmed on findings would be muted
    within a month.
``1``
    The sweep could not run, or could not file what it found. This is the only
    case worth waking anyone for, and the case with no other witness: a night
    that misreports its own failure as success leaves nothing behind at all.

Budget
------
A verdict costs a full suite walk, and the harness spends **one** control walk
per spec however many mutations the spec carries. So a run costs

    1 + BLOCK_SIZE * MUTATIONS_PER_FILE  =  1 + 6 * 1  =  7 walks

across :data:`WORKERS` workers -- the same seven the single-file form spent on
six mutations of one module, against the same ceiling. What the block form buys
for that price is reach: six modules a run, and a census cover in
``ceil(len / BLOCK_SIZE)`` runs where six mutations of one module needed one run
per module -- 24 against 142 at the census of 2026-09-19.

Seven is the worst case, not the expectation: a barren slot yields no mutation
and therefore no walk, and its walk is *not* handed to another file (see
:func:`sweep_mutations.for_block`). Neither factor is a knob the workflow is
expected to tune, and a worker count raised to buy wall clock buys less than it
looks (see :data:`WORKERS`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Final

import sweep_filing
import sweep_mutations
import sweep_verdict
from sweep_census import BLOCK_SIZE, REPO_ROOT, SweepError, block, census, run_index
from sweep_mutations import MUTATIONS_PER_FILE


def walk_budget(block_size: int = BLOCK_SIZE) -> int:
    """The most suite walks one run can cost.

    The shared control plus one mutation per block member. Computed from the two
    constants rather than written down, so the module docstring's arithmetic, the
    workflow's ceiling and the code cannot drift apart -- the failure the
    red-team workflow's own schedule comment already paid for once.
    """
    return 1 + block_size * MUTATIONS_PER_FILE


#: Isolated full suites running at once on one four-core runner.
#:
#: Two rather than the harness's own default of four, for the two reasons the
#: harness itself records. A green walk grows *with* the worker count, because
#: concurrent suites contend for one machine: ``mutate._WALK_BASE_SECONDS`` fits
#: that growth at 240 s per added worker (measured on an Apple silicon laptop at
#: 5498 tests, recorded on #566), so four workers on four cores does not divide
#: the wall clock by four. And ``mutate.py``'s "the port hazard is the reason to
#: keep ``--workers`` modest" applies here in its sharpest form -- the e2e tests
#: take real ports by binding port 0 and closing the socket, which concurrent
#: suites can be handed twice.
#:
#: Not exposed as a flag. The per-run budget arithmetic in this module's
#: docstring depends on it, and the harness's hang timeout scales with it, so
#: raising it at 3 a.m. changes two things at once.
WORKERS: Final = 2

#: The real harness. Overridable with ``--mutate-cmd`` for rehearsals, which is
#: announced on stdout on every path -- a run whose verdicts came from something
#: else is not evidence about this repository's suite, and "0 findings" from such
#: a run reads exactly like the real thing.
#:
#: **``--with-git`` is not optional here, it is what makes a verdict possible.**
#: The harness copies the checkout without ``.git``, and since the compliance
#: census landed, ``tools/audit/claim_surfaces.repo_root`` raises
#: ``SystemExit("no .git found at or above ...")`` in such a copy rather than
#: skipping. Measured 2026-09-16 in a default prepared tree at ``f0e58408``:
#: ``tests/integration/audit/test_census_audits_run.py`` comes back **11
#: failed**, which is the same count issue #527 recorded and which turns the
#: harness's unmutated control RED. A run without this flag would therefore
#: exit 2 and file a ``run-untrusted`` issue every single time and never a real
#: verdict. #527 is closed as a recorded quirk, not as a fix -- neither of its
#: two options was taken, so the flag is the whole remedy.
#:
#: It also buys what it was built for: without it the four rules that read a
#: *blob* skip, so a mutation whose only killer is one of them comes back
#: SURVIVED from a run that never executed the test holding it. For an
#: unattended job that files what it finds, that is a fabricated finding.
#:
#: The flag needs a plain repository at the checkout's ``.git``. A CI checkout is
#: one; a linked worktree is not, so running this driver's default harness from a
#: worktree fails loudly with the harness's own message and files
#: ``run-untrusted``. That is the right failure, and it is why a rehearsal with
#: ``--mutate-cmd`` cannot exercise this path.
DEFAULT_MUTATE_COMMAND: Final = (
    "uv",
    "run",
    "--frozen",
    "python",
    str(REPO_ROOT / "tools/mutate.py"),
    "--with-git",
)

#: Runs the harness and returns its exit code. Injected by the tests, which
#: cannot afford a full suite walk per mutation.
MutateRunner = Callable[[Sequence[str]], int]


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tools/sweep.py",
        description="Run one block of the async red-team sweep and file what it finds.",
    )
    parser.add_argument("--date", required=True, help="the run, as YYYY-MM-DD; picks the block")
    parser.add_argument(
        "--block-size",
        type=int,
        default=BLOCK_SIZE,
        help=(
            "census slots this run consumes, one mutation each. The run costs one "
            "shared control walk plus one walk per non-barren slot"
        ),
    )
    parser.add_argument("--json", dest="results", required=True, help="where the harness writes")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the issue that would be filed instead of filing it; never calls gh",
    )
    parser.add_argument("--repo", default=None, help="owner/name; omitted lets gh infer it")
    parser.add_argument(
        "--commit",
        default=None,
        help=(
            "the sha this night ran against, 7-40 lowercase hex. The filed issue "
            "leads its reproduction with `git checkout <sha>`, because the date alone "
            "does not fix which file the sweep attacks"
        ),
    )
    parser.add_argument(
        "--mutate-cmd",
        default=None,
        help=(
            "run this instead of tools/mutate.py, split as a shell word list but never "
            "run through a shell -- for rehearsals. Announced in the output of every run "
            "that uses it, including the ones that file nothing"
        ),
    )
    return parser.parse_args(argv)


def _night_of(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise SweepError(f"--date {raw!r} is not a YYYY-MM-DD date: {error}") from error


#: What a commit may look like before it is written into a shell instruction.
#:
#: Seven is git's own abbreviation floor and forty is a full sha-1. The class
#: holds no metacharacter, no space and no upper case, which is the point: this
#: is the one repository-derived string the payload puts inside a block written
#: to be *pasted into a shell* rather than read as data, so it is refused rather
#: than escaped.
_COMMIT_SHAPE: Final = re.compile(r"[0-9a-f]{7,40}")


def _commit_of(raw: str | None) -> str | None:
    """A validated sha, or ``None`` when the caller did not have one.

    Refusing early matters for more than injection: a night that cannot write a
    true reproduction instruction should not spend two hours of CI earning the
    right to write a false one.
    """
    if raw is None:
        return None
    if _COMMIT_SHAPE.fullmatch(raw) is None:
        raise SweepError(
            f"--commit {raw!r} is not 7-40 lowercase hex. This value is pasted into a "
            "`git checkout` line in the filed issue, so it is refused rather than escaped"
        )
    return raw


def _source_of(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


#: Names the harness's children must not inherit.
#:
#: The harness runs this repository's whole dependency tree under mutation --
#: seven full suite walks, roughly two hours, unattended, on a schedule -- and
#: ``mutate_run._child_env`` builds each suite's environment from
#: ``dict(os.environ)``. Whatever this driver holds is therefore what every one
#: of those processes holds, and in CI that is a token with ``issues:write``.
#: The red-team workflow is the only one in this repository pairing suite
#: execution with a write token.
#:
#: A subtraction rather than an allow-list, deliberately: ``tools/mutate.py``
#: needs ``UV_CACHE_DIR`` to keep each isolated tree's virtualenv warm, ``HOME``
#: to find it when that is unset, and git's own configuration. A scrubbed
#: environment would make every tree build from scratch or fail outright, and
#: that half of the fix is the half no security assertion notices going wrong.
#:
#: The driver keeps the token for itself -- it files through ``gh`` afterwards,
#: in a process this does not touch.
_TOKENS_THE_HARNESS_MUST_NOT_SEE: Final = frozenset({"GH_TOKEN", "GITHUB_TOKEN"})


def _harness_env() -> dict[str, str]:
    """This process's environment, minus the tracker tokens."""
    return {
        name: value
        for name, value in os.environ.items()
        if name not in _TOKENS_THE_HARNESS_MUST_NOT_SEE
    }


def _run_mutate(argv: Sequence[str]) -> int:
    """Run the harness, letting its output through to the log as it goes.

    Not captured: a batch runs for tens of minutes and its per-mutation lines are
    the only sign of progress a workflow log has. ``cwd`` is the checkout because
    the harness resolves repository-relative anchors against its own root, and
    ``env`` is this process's own minus the tracker tokens (see
    :data:`_TOKENS_THE_HARNESS_MUST_NOT_SEE`).
    """
    print(f"harness   {' '.join(argv)}", flush=True)
    try:
        completed = subprocess.run(  # noqa: S603
            list(argv), cwd=REPO_ROOT, env=_harness_env(), check=False
        )
    except OSError as error:
        raise SweepError(f"could not run the mutation harness: {error}") from error
    return completed.returncode


def _outcomes_of(results: Path, mutate_exit: int) -> tuple[sweep_verdict.Outcome, ...]:
    """The harness's record, or an empty one when exit 2 says there may not be a record.

    The asymmetry is the point. An exit 2 can happen before a single suite runs
    -- a refused anchor, a tree that would not build -- so a missing file there
    is expected and the night still files. A *missing* file under any other exit
    code is a harness that claims a result it did not write, which is not a
    finding about the product and must not be filed as one.
    """
    if not results.is_file():
        if mutate_exit == sweep_verdict.UNTRUSTED_EXIT:
            return ()
        raise SweepError(
            f"the harness exited {mutate_exit} but wrote no results to {results}; "
            "nothing was measured, so this night has no evidence either way"
        )
    return sweep_verdict.read_results(results.read_text(encoding="utf-8"))


def _describe(attempts: Sequence[sweep_mutations.Attempt], block_size: int) -> None:
    for attempt in attempts:
        offered = len(attempt.generated.candidates)
        dropped = len(attempt.generated.skipped)
        if attempt.candidate is None:
            print(f"slot {attempt.slot.index:>4}  barren    {attempt.path} ({dropped} dropped)")
            continue
        print(
            f"slot {attempt.slot.index:>4}  lap {attempt.slot.lap:<6} {attempt.path} "
            f"-> candidate {1 + attempt.slot.lap % offered} of {offered} ({dropped} dropped)"
        )
    walks = 1 + sum(1 for attempt in attempts if attempt.candidate is not None)
    # The ceiling comes from the *configured* block size, never from the block
    # that was realised: `walk_budget(len(attempts))` re-derives the bound from
    # the same number it is supposed to bound, so a run that built a short block
    # would print its own shortfall as the budget and read as compliant.
    print(f"budget    {walks} walk(s) of at most {walk_budget(block_size)}")


def _print_payload(payload: sweep_filing.Payload) -> None:
    print(f"\n--- would file under label {sweep_filing.LABEL} ---")
    print(f"title: {payload.title}\n")
    print(payload.body)
    print("--- end of payload ---")


def _sweep(
    args: argparse.Namespace, mutate_runner: MutateRunner, gh_runner: sweep_filing.Runner
) -> int:
    night = _night_of(args.date)
    commit = _commit_of(args.commit)
    harness = tuple(shlex.split(args.mutate_cmd)) if args.mutate_cmd else DEFAULT_MUTATE_COMMAND
    if harness != DEFAULT_MUTATE_COMMAND:
        print(f"WARNING   --mutate-cmd substituted the mutation harness with {' '.join(harness)};")
        print("          these verdicts are not this repository's own suite")

    slots = block(census(), night, size=args.block_size)
    attempts = sweep_mutations.for_block(slots, _source_of, on=night)
    _describe(attempts, args.block_size)
    picked = tuple(attempt.candidate for attempt in attempts if attempt.candidate is not None)

    results = Path(args.results).resolve()
    # Removed before the harness starts, never after. The workflow writes the
    # record to a fixed name, so a rerun on a cached workspace -- or any local
    # rerun -- finds the previous night's file already there, and a harness that
    # exits before writing leaves the driver reading a complete, well-formed
    # document belonging to another night. The issue would then report those
    # verdicts as tonight's: fabricated evidence, in the one artifact the job
    # exists to produce.
    results.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="theurian-sweep-") as scratch:
        spec = Path(scratch) / "mutations.json"
        spec.write_text(
            json.dumps(sweep_mutations.spec_entries(picked), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        argv = (*harness, "--spec", str(spec), "--json", str(results), "--workers", str(WORKERS))
        mutate_exit = mutate_runner(argv)

    outcomes = _outcomes_of(results, mutate_exit)
    reading = sweep_verdict.classify(
        mutate_exit=mutate_exit, outcomes=outcomes, submitted=len(picked)
    )
    if reading.reason is None:
        print(f"clean     {reading.detail}; no finding to file")
        return 0

    payload = sweep_filing.build_payload(
        sweep_filing.Night(
            on=night,
            run=run_index(night),
            attempts=attempts,
            harness=harness,
            command=_reproduction(args, night),
            mutate_exit=mutate_exit,
            outcomes=outcomes,
            reading=reading,
            commit=commit,
        )
    )
    if args.dry_run:
        _print_payload(payload)
        return 0
    filed = sweep_filing.file_finding(
        payload,
        repo=args.repo,
        runner=gh_runner,
        gh=shutil.which("gh") or "gh",
    )
    print(f"filed     {filed}")
    return 0


def _reproduction(args: argparse.Namespace, night: date) -> tuple[str, ...]:
    """The command that regenerates this night, for the issue to carry.

    Not ``sys.argv``: that names a results path on a runner that will not exist
    tomorrow, and may carry a ``--mutate-cmd`` whose rehearsal harness is not
    what a reader should re-run.
    """
    return (
        "uv",
        "run",
        "--frozen",
        "python",
        "tools/sweep.py",
        "--date",
        str(night),
        "--block-size",
        str(args.block_size),
        "--json",
        f"sweep-{night}.json",
        "--dry-run",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    mutate_runner: MutateRunner | None = None,
    gh_runner: sweep_filing.Runner | None = None,
) -> int:
    """One night. See the module docstring for what the two exit codes mean."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return _sweep(
            args,
            mutate_runner or _run_mutate,
            gh_runner or sweep_filing.run_command,
        )
    except SweepError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
