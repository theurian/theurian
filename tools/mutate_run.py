"""Running one mutation's suite and turning the result into a verdict.

Split out of ``tools/mutate.py`` (see its module docstring for the harness
this supports) because the file grew past a comfortable size once a hung
suite needed its own verdict. Everything here is about a single tree: finding
``uv``, building its isolated environment, running the suite inside it with a
timeout, and reading what came back. ``mutate.py`` imports what it needs and
stays the orchestration layer -- building the trees themselves, batching
mutations across them, and reporting.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from mutate_edits import HarnessError, Mutation, _apply, _restore_all

_PYTEST_ARGS: Final = (
    # Deterministic collection order, so a fail-fast stop is comparable across
    # mutations, and no cache written into the throwaway tree.
    "-p",
    "no:randomly",
    "-p",
    "no:cacheprovider",
    # These come *after* the plugin flags on purpose, and the order is load
    # bearing. A run started here lives for minutes inside a checkout where
    # other work is also running `pytest -q`, and the usual way to stop that
    # work is `pkill -f "pytest -q"` -- which matches on the whole argv, so
    # leading with `-q` volunteers this harness for everyone else's cleanup.
    # Killed mid-run it reports `control-red` or a false SURVIVED, and neither
    # says "someone shot me". The prepared-tree runner has always ordered them
    # this way; the verdict path had not, which is why only the verdict path
    # kept dying.
    "-q",
    "--no-header",
    "--tb=no",
)


class SuiteHungError(HarnessError):
    """The suite did not finish. A *result*, not a broken run.

    Distinguished from every other `HarnessError` because the two need opposite
    readings. An anchor that did not match means the harness learned nothing; a
    suite that never terminated under a mutation means the suite cannot go RED
    for that mutation, which is the same class of finding as SURVIVED and is
    strictly worse in CI -- a hung job reports a timeout with no test name on it.

    Milestone 6 met this on the first mutation it tried against
    `_visible_ranking`'s progress guard: both the guard's removal and an
    off-by-one in it hang, and the batch reported "ERROR (the run proves
    nothing)" with the digests dropped, so nothing in the output said the
    mutation had even applied.

    Carries whatever partial output the suite had already produced before it
    was killed, because a hung job showing no test name is exactly the gap
    this verdict exists to close, and re-obtaining it costs the timeout again
    -- up to 1800s by default.
    """

    def __init__(self, seconds: int, tree: Path, output: str = "") -> None:
        super().__init__(f"the suite did not finish within {seconds}s in {tree}")
        self.seconds = seconds
        self.output = output


@dataclass(frozen=True)
class Outcome:
    label: str
    verdict: str
    suite_green: bool | None
    seconds: float
    summary: str
    failures: tuple[str, ...] = ()
    detail: str = ""
    digests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Options:
    workers: int
    fail_fast: bool
    control: bool
    timeout: int
    keep_trees: bool
    json_path: Path | None
    work_dir: Path | None


def _uv() -> str:
    found = shutil.which("uv")
    if found is None:
        raise HarnessError("uv is not on PATH; the harness runs the suite through `uv run`")
    return found


def _child_env(tree: Path, cache_dir: Path) -> dict[str, str]:
    """Environment for a suite run: isolated HOME, TMPDIR, no inherited virtualenv."""
    env = dict(os.environ)
    for leaked in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        env.pop(leaked, None)
    home = tree / ".mutate-home"
    data = tree / ".mutate-data"
    tmp = tree / ".mutate-tmp"
    for directory in (home, data, tmp):
        directory.mkdir(exist_ok=True)
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(home),
            "THEURIAN_DATA_DIR": str(data),
            # Resolved before HOME moved, so the child still hits the warm cache.
            "UV_CACHE_DIR": str(cache_dir),
            # A per-tree TMPDIR, so each worker's pytest gets its own
            # `pytest-of-<user>` basetemp under `tempfile.gettempdir()`. The trees
            # already isolate HOME and the data dir but shared the system TMPDIR,
            # so four concurrent suites built their tmp_path trees under one
            # basetemp -- and a pytest session removes all but the last few
            # numbered dirs at start-up, so one worker's start-up could delete a
            # tmp_path tree another worker was mid-test in. That surfaced as a
            # control-red on the tmp_path-heavy `registry` fixture under
            # `--workers 4` only; single-process never shared, so it was always
            # green. TMP/TEMP set alongside for a collaborator that reads them.
            "TMPDIR": str(tmp),
            "TMP": str(tmp),
            "TEMP": str(tmp),
        }
    )
    return env


def _run_suite(tree: Path, options: Options, cache_dir: Path) -> subprocess.CompletedProcess[str]:
    argv = [_uv(), "run", "--frozen", "--no-sync", "pytest", *_PYTEST_ARGS]
    if options.fail_fast:
        argv.append("-x")
    try:
        return subprocess.run(  # noqa: S603 - argv is harness-owned, never user input
            argv,
            cwd=tree,
            env=_child_env(tree, cache_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=options.timeout,
        )
    except subprocess.TimeoutExpired as expired:
        # `capture_output=True` still hands back partial output as bytes on a
        # timeout, `text=True` notwithstanding -- confirmed against 3.13.
        partial = expired.output
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        raise SuiteHungError(options.timeout, tree, output=partial or "") from expired


def _summarise(stdout: str) -> tuple[str, tuple[str, ...]]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    summary = lines[-1] if lines else "(no output)"
    failures = tuple(line for line in lines if line.startswith(("FAILED", "ERROR")))[:20]
    return summary, failures


# pytest's own final line always names what it counted. Anything else on that
# line -- a progress marker like `......[100%]`, a partial write -- means the
# run was cut off before it could report, and the exit code alone cannot be
# trusted to mean what it usually means.
_SUMMARY_MARKERS: Final = ("passed", "failed", "error")


def _recognised_summary(summary: str) -> bool:
    """Is this pytest's own summary line, or noise from a truncated run?

    Issue #50: mutation ``C7`` came back KILLED under ``--workers 4`` with a
    summary of ``......[100%]`` and no ``FAILED`` line anywhere in the output --
    a run truncated under parallelism, not a suite that went red. A rerun alone
    reported SURVIVED. Trusting the exit code without checking that the summary
    line is one pytest itself would print reports a caught mutation that
    nothing actually caught, which is the exact failure this harness exists to
    prevent. This is not a parser for every summary pytest can print; it only
    needs to reject the case that reached this project.
    """
    lowered = summary.lower()
    return any(marker in lowered for marker in _SUMMARY_MARKERS)


def _run_one(tree: Path, mutation: Mutation, options: Options, cache_dir: Path) -> Outcome:
    started = time.monotonic()
    if mutation.is_control:
        return _run_control(tree, mutation.label, options, cache_dir, started)
    return _run_mutation(tree, mutation, options, cache_dir, started)


def _hung_summary(hung: SuiteHungError) -> tuple[str, tuple[str, ...]]:
    """Best-available summary/failures from whatever the suite had printed.

    A hang costs the full timeout to reproduce -- 1800s by default -- so
    whatever partial output it left behind is the most expensive thing this
    harness can discard. Falls back to the timeout message itself only when
    the suite hung before printing anything at all.
    """
    if not hung.output:
        return str(hung), ()
    summary, failures = _summarise(hung.output)
    return summary or str(hung), failures


def _run_control(
    tree: Path, label: str, options: Options, cache_dir: Path, started: float
) -> Outcome:
    try:
        completed = _run_suite(tree, options, cache_dir)
    except SuiteHungError as hung:
        summary, failures = _hung_summary(hung)
        return Outcome(
            label=label,
            verdict="ERROR",
            suite_green=None,
            seconds=time.monotonic() - started,
            summary=summary,
            failures=failures,
            detail=hung.output[-4000:],
        )
    summary, failures = _summarise(completed.stdout)
    if not _recognised_summary(summary):
        return Outcome(
            label=label,
            verdict="ERROR",
            suite_green=None,
            seconds=time.monotonic() - started,
            summary=summary,
            failures=failures,
            detail=completed.stdout[-4000:],
        )
    green = completed.returncode == 0
    return Outcome(
        label=label,
        verdict="control-green" if green else "control-red",
        suite_green=green,
        seconds=time.monotonic() - started,
        summary=summary,
        failures=failures,
        detail="" if green else completed.stdout[-4000:],
    )


def _run_mutation(
    tree: Path, mutation: Mutation, options: Options, cache_dir: Path, started: float
) -> Outcome:
    landed = _apply(tree, mutation)
    completed: subprocess.CompletedProcess[str] | None = None
    hung: SuiteHungError | None = None
    try:
        completed = _run_suite(tree, options, cache_dir)
    except SuiteHungError as caught:
        hung = caught
    finally:
        # Restored exactly once here, whichever way the suite ended. The HUNG
        # branch below used to restore a second time on its own, which for a
        # composite mutation briefly wrote the intermediate mutated state
        # back to disk in between two calls that both landed on the same
        # correct final content -- unproven safe, not actually needed.
        digests = _restore_all(landed, tree)

    if hung is not None:
        summary, failures = _hung_summary(hung)
        return Outcome(
            label=mutation.label,
            verdict="HUNG",
            suite_green=None,
            seconds=time.monotonic() - started,
            summary=summary,
            failures=failures,
            detail=hung.output[-4000:],
            digests=digests,
        )
    if completed is None:
        # Unreachable: `completed` is only ever left `None` when `hung` is
        # set, and that case returns above. Guarded rather than asserted
        # (asserts are stripped under `-O`), so a future change that breaks
        # this invariant fails as a HarnessError, not a silent AttributeError.
        raise HarnessError(f"{mutation.label}: the suite produced no result and did not hang")

    summary, failures = _summarise(completed.stdout)
    if not _recognised_summary(summary):
        return Outcome(
            label=mutation.label,
            verdict="ERROR",
            suite_green=None,
            seconds=time.monotonic() - started,
            summary=summary,
            failures=failures,
            digests=digests,
        )
    green = completed.returncode == 0
    return Outcome(
        label=mutation.label,
        verdict="SURVIVED" if green else "KILLED",
        suite_green=green,
        seconds=time.monotonic() - started,
        summary=summary,
        failures=failures,
        digests=digests,
    )
