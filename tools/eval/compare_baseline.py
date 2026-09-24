"""Advisory comparison of a freshly regenerated ``report.json`` against the committed baseline.

Phase A slice S4b (ADR-0036 decision 4,
``docs/adr/0036-golden-judgements-are-committed-regression-fixtures.md``; roadmap Phase A
Exit-criteria row: "A baseline report is committed and CI reports regressions against it").

Same comparison target as
``tests/integration/tools/test_baseline_current.py`` -- this checkout's regeneration against this
checkout's own committed ``tools/eval/baseline/report.json`` -- rendered for a reviewer instead of
asserted with a bare ``AssertionError``: a per-field listing and a step summary, never a red
build. This is the LOCAL comparison, not the CROSS-COMMIT one that test file's own docstring names
as "a later assignment" (a pull request's regeneration against the baseline committed on
``main``); that remains unbuilt. What this catches without it: a reviewer reading *which* fields
moved when the local pytest pin already failed hard, and genuine cross-machine drift -- ADR-0036
decision 5 measures byte-identity only "at one machine, one interpreter, one SQLite build", so a
runner with a different SQLite build can legitimately reproduce a different ``report.json`` with
no code change at all, and that must not turn a job red.

Whether this comparison ever becomes a blocking gate is a separate decision (ADR-0036 decision 4)
this tool does not take. This module's docstring is the one place that decision, and this tool's
own ``--advisory`` scope, is stated -- a caller citing it (``.github/workflows/core.yml``, an ADR)
should point here rather than restate it, so the claim has one authority instead of drifting
copies. ``--advisory`` is **total** here, unlike ``tools/corpus_drift.py``'s own partial one: that
tool keeps an unsuppressible "the checker stopped checking" exit because its subject is a governed
corpus this repository holds to a floor. This comparison has no floor and no target value anywhere
(decision 4), so nothing it can find is worth failing a build over yet -- not even a harness that
could not run at all: a real harness defect already fails the separate, non-advisory ``test`` job
that this one does not gate. Concretely, every one of the following converges to :attr:`Status.
ERRORED` and still exits 0 under ``--advisory``: the committed baseline is unreadable or is not
valid JSON, ``tools/eval/run.py`` exits nonzero, it raises instead of returning, it returns 0
without writing a ``report.json``, or ``$GITHUB_STEP_SUMMARY`` cannot be written to. :func:`_run`
is where all of those are caught in one place, deliberately, rather than one exception type at a
time -- see its own docstring.

Usage
-----
    uv run python tools/eval/compare_baseline.py             # exit 1 on a difference
    uv run python tools/eval/compare_baseline.py --advisory  # exit 0 whatever this run finds
    uv run python tools/eval/compare_baseline.py --advisory --format github --summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

import run as harness_run
from metrics import differing_paths

#: tools/eval/compare_baseline.py -> baseline/report.json, beside this file.
BASELINE_REPORT: Final = Path(__file__).resolve().parent / "baseline" / "report.json"

#: What a maintainer does about a difference, printed once beside every DIFFERS finding. Phrased
#: as *regenerate*, never *edit report.json by hand*: hand-editing would desync it from what the
#: harness actually produces, which is exactly what `test_baseline_current.py` exists to catch.
REMEDY: Final = (
    "A deliberate retrieval change updates the baseline in the same PR: regenerate "
    "tools/eval/baseline/report.json (see tools/eval/baseline/README.md's Re-check commands) "
    "and commit it -- test_baseline_current.py then pins the new figures to this tree."
)


class HarnessError(RuntimeError):
    """``tools/eval/run.py`` exited nonzero instead of producing a ``report.json`` to compare."""


class Status(Enum):
    """What this run found."""

    HOLDS = "holds"
    DIFFERS = "differs"
    ERRORED = "errored"


@dataclass(frozen=True, slots=True)
class Comparison:
    """One run's outcome. ``differing`` is populated only for :attr:`Status.DIFFERS`; ``detail``
    is the one sentence every render function prints.

    Named ``differing``, not ``differing_paths``: this module also imports
    :func:`metrics.differing_paths`, and a field of that name would read as if it were that
    function shadowed rather than a value computed by calling it.
    """

    status: Status
    differing: frozenset[str] = field(default_factory=frozenset)
    detail: str = ""


def regenerate_report_bytes(corpus: Path = harness_run.DEFAULT_CORPUS) -> bytes:
    """``report.json``'s bytes from a fresh in-process run over ``corpus``.

    The same call path ``tests/integration/tools/test_baseline_current.py`` drives -- no
    subprocess, so this reads the harness already on ``sys.path``, not a second interpreter's
    copy of it. Raises :class:`HarnessError` when the harness exits nonzero; a bare
    ``FileNotFoundError`` when it exits 0 but writes nothing; and whatever the harness itself
    raises, uncaught, when it raises instead of returning. :func:`_run` is where all three (and
    every other failure mode) converge to one outcome -- nothing here is a place to catch them.
    """
    with tempfile.TemporaryDirectory(prefix="theurian-eval-compare-") as out_name:
        code = harness_run.main(["--corpus", str(corpus), "--out", out_name])
        if code != 0:
            raise HarnessError(f"tools/eval/run.py exited {code} over {corpus}")
        return (Path(out_name) / "report.json").read_bytes()


def compare(baseline_bytes: bytes, current_bytes: bytes) -> Comparison:
    """Whether ``current_bytes`` reproduces ``baseline_bytes`` byte-for-byte.

    Byte equality is ``write_report``'s own canonical form (sorted keys, rounded floats) and is
    the same property ``test_baseline_current.py`` pins, so it answers "holds" with no JSON parse
    at all. Only when it fails is either side parsed, purely to name *which* field moved --
    :func:`metrics.differing_paths`, the function ADR-0036 decision 6's own equality section
    already uses for exactly this shape. ``json.loads`` raises uncaught on an unparseable
    baseline or an unparseable regeneration; see :func:`_run`.
    """
    if baseline_bytes == current_bytes:
        return Comparison(
            Status.HOLDS, detail="report.json reproduces the committed baseline byte-for-byte."
        )
    diff = differing_paths(json.loads(baseline_bytes), json.loads(current_bytes))
    if not diff:
        return Comparison(
            Status.DIFFERS,
            detail=(
                "report.json's bytes differ from the committed baseline, but no field-level "
                "difference was found -- a formatting artifact rather than a metric move."
            ),
        )
    return Comparison(
        Status.DIFFERS, frozenset(diff), f"{len(diff)} field(s) differ from the committed baseline."
    )


def exit_code(comparison: Comparison, *, advisory: bool) -> int:
    """0 always under ``--advisory``; otherwise 0 when the baseline holds, 1 otherwise.

    Total, unlike ``tools/corpus_drift.py``'s own ``--advisory`` -- see the module docstring for
    why this comparison has no unsuppressible failure mode to preserve.
    """
    if comparison.status is Status.HOLDS or advisory:
        return 0
    return 1


def render_text(comparison: Comparison) -> str:
    """The human report, for a local run and for the CI job log."""
    lines = [f"Baseline comparison: {comparison.status.value} -- {comparison.detail}"]
    lines.extend(f"  DIFFERS  {path}" for path in sorted(comparison.differing))
    if comparison.status is Status.DIFFERS:
        lines.extend(("", REMEDY))
    return "\n".join(lines)


def render_github(comparison: Comparison) -> tuple[str, ...]:
    """Workflow commands. ``::warning``, never ``::error``: nothing this tool finds is severe
    enough to mark a step red (see the module docstring).
    """
    if comparison.status is Status.HOLDS:
        return ()
    if comparison.status is Status.ERRORED:
        return (f"::warning title=Retrieval baseline comparison did not run::{comparison.detail}",)
    if not comparison.differing:
        return (f"::warning title=Retrieval baseline differs::{comparison.detail} {REMEDY}",)
    return tuple(
        f"::warning file=tools/eval/baseline/report.json,title=Retrieval baseline differs::"
        f"`{path}` moved from the committed baseline. {REMEDY}"
        for path in sorted(comparison.differing)
    )


def render_summary(comparison: Comparison) -> str:
    """Markdown for ``$GITHUB_STEP_SUMMARY``."""
    lines = [
        "## Retrieval baseline comparison",
        "",
        f"**{comparison.status.value}** -- {comparison.detail}",
        "",
    ]
    if comparison.differing:
        lines.extend(
            (
                "| Field |",
                "| :-- |",
                *(f"| `{path}` |" for path in sorted(comparison.differing)),
                "",
                REMEDY,
                "",
            )
        )
    return "\n".join(lines)


def _run() -> Comparison:
    """The whole comparison, one exception clause wide.

    Reproduced escaping this tool before this clause existed: the committed baseline unreadable
    or not valid JSON, ``tools/eval/run.py`` exiting nonzero (:class:`HarnessError`, named so its
    own message is specific), exiting 0 without writing a ``report.json`` (a bare
    ``FileNotFoundError`` from :func:`regenerate_report_bytes`), and the harness *raising* instead
    of returning (``run.py``'s ``RuntimeError`` on a build or corpus refusal is a live example --
    nothing inside ``run.main`` guards ``build_server``/``mcp_session`` against raising). A
    catch-all rather than one clause per case: the list above is what was found, not a claim that
    it is complete, and ``--advisory`` is total (see the module docstring) precisely because a
    fifth uncaught exception must not be a fifth way to fail this job.
    """
    try:
        baseline_bytes = BASELINE_REPORT.read_bytes()
        current_bytes = regenerate_report_bytes()
        return compare(baseline_bytes, current_bytes)
    except Exception as error:  # deliberate and total; see the docstring above
        return Comparison(Status.ERRORED, detail=f"{type(error).__name__}: {error}")


def _write_summary(comparison: Comparison) -> None:
    """Best-effort append to ``$GITHUB_STEP_SUMMARY``. A step summary GitHub's runner cannot
    accept is not this tool's failure to report -- it is caught here rather than in :func:`_run`
    because it happens after the comparison already has an outcome, and must not overwrite one.
    """
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    try:
        with Path(summary_file).open("a", encoding="utf-8") as handle:
            handle.write(render_summary(comparison) + "\n")
    except OSError as error:
        print(f"::warning title=Retrieval baseline summary not written::{error}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Exit 0 whatever this run finds -- differs, or could not run at all.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="`github` adds ::warning workflow commands.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Append a Markdown summary to $GITHUB_STEP_SUMMARY, when it is set.",
    )
    arguments = parser.parse_args(argv)

    comparison = _run()
    print(render_text(comparison))
    if arguments.format == "github":
        for command in render_github(comparison):
            print(command)
    if arguments.summary:
        _write_summary(comparison)
    return exit_code(comparison, advisory=arguments.advisory)


if __name__ == "__main__":
    sys.exit(main())
