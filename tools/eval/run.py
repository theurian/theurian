"""CLI entry point for the Phase A retrieval-evaluation harness (ADR-0036).

Builds two projects from one fixture corpus -- ``full`` (every migration) and
``clean`` (visible-plane only) -- runs every enabled query against the
corpora it names over the real MCP wire, and writes ``report.json`` (the
deterministic metrics pin) and ``timings.json`` (the dated annex), per
decision 7's split. The harness produces measurements; it asserts nothing
(decision 4) -- there is no pass/fail threshold anywhere in this module.

**Slice S4c adds a second, raptor-ON pair, run through the identical query
loop.** Every enabled query is dispatched against the raptor pair exactly as
against the base pair -- same default flags, same limits, same #787
abstention-probe machinery -- and the symmetry is deliberate: it is what lets
``report.py``'s ``comparison`` block attribute a metric move to the RAPTOR
forest's presence alone, rather than to some other difference between the
two runs.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Final

from corpus import BUILD_CEILING, CorpusError, JudgementEntry, QueryEntry, load_corpus
from corpus_build import BuildResult, BuiltProject, build_both
from report import (
    HarnessConstants,
    QueryRun,
    RaptorArm,
    build_report,
    build_timings,
    probe_limits_for,
    write_report,
    write_timings,
)
from wire import ToolCall, mcp_session

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

#: tools/eval/run.py -> eval -> tools -> repo root.
REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS: Final = REPO_ROOT / "tests" / "fixtures" / "eval"

#: Harness constants (ADR-0036 decision 4: no threshold lives beside these).
MAX_TOKENS: Final = 32_000
INCLUDE_UNAPPROVED: Final = False
USE_DENSE: Final = False
EQUALITY_LIMIT: Final = 50


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.corpus == DEFAULT_CORPUS and not args.corpus.exists():
        print(
            f"corpus refused: {args.corpus} does not exist. The frozen "
            f"measurement corpus is a later Phase A slice; pass --corpus to "
            f"point at a fixture corpus that exists.",
            file=sys.stderr,
        )
        return 1
    try:
        loaded = load_corpus(args.corpus)
    except CorpusError as exc:
        print(f"corpus refused: {exc}", file=sys.stderr)
        return 1

    constants = HarnessConstants(
        limit=max(loaded.manifest.k_values),
        max_tokens=MAX_TOKENS,
        include_unapproved=INCLUDE_UNAPPROVED,
        use_dense=USE_DENSE,
        equality_limit=EQUALITY_LIMIT,
        build_ceiling=BUILD_CEILING.value,
    )

    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="theurian-eval-") as workspace_name:
        workspace = Path(workspace_name)
        try:
            built = build_both(loaded, workspace)
            raptor_built = build_both(loaded, workspace, raptor=True)
        except CorpusError as exc:
            print(f"build refused: {exc}", file=sys.stderr)
            return 1

        runs: list[QueryRun] = []
        raptor_runs: list[QueryRun] = []
        with ExitStack() as sessions:
            calls = _open_sessions(sessions, built)
            raptor_calls = _open_sessions(sessions, raptor_built)
            for query in loaded.queries:
                if not query.enabled:
                    continue
                judgement = loaded.judgement_for(query.id)
                if judgement is None:
                    msg = f"loader invariant violated: enabled query {query.id!r} has no judgement"
                    raise RuntimeError(msg)
                runs.extend(_run_query(calls, built.projects, query, judgement, constants))
                raptor_runs.extend(
                    _run_query(raptor_calls, raptor_built.projects, query, judgement, constants)
                )

        census = {name: project.census for name, project in built.projects.items()}
        build_costs = {name: project.build_cost for name, project in built.projects.items()}
        raptor_arm = RaptorArm(
            runs=raptor_runs,
            census={name: project.census for name, project in raptor_built.projects.items()},
            build_costs={
                name: project.build_cost for name, project in raptor_built.projects.items()
            },
        )
        report = build_report(loaded, constants, runs, census, raptor=raptor_arm)
        timings = build_timings(loaded, runs, build_costs, REPO_ROOT, raptor=raptor_arm)

    write_report(report, args.out / "report.json")
    write_timings(timings, args.out / "timings.json")
    return 0


def _open_sessions(sessions: ExitStack, built: BuildResult) -> dict[str, ToolCall]:
    return {
        name: sessions.enter_context(
            mcp_session(build_server(ProjectRegistry.default(project.data_dir)), project.data_dir)
        )
        for name, project in built.projects.items()
    }


def _run_query(
    calls: dict[str, ToolCall],
    projects: dict[str, BuiltProject],
    query: QueryEntry,
    judgement: JudgementEntry,
    constants: HarnessConstants,
) -> list[QueryRun]:
    limits = {constants.limit}
    if len(set(query.corpora)) > 1:
        limits.add(constants.equality_limit)
    runs = []
    for corpus_name in sorted(set(query.corpora)):
        project = projects[corpus_name]
        for limit in sorted(limits):
            runs.append(_one_call(calls[corpus_name], project, query, limit, constants))
    probe_limits = probe_limits_for(query, judgement, constants)
    if probe_limits:
        # #787's flag-probe: the same wire path, once more per limit against
        # the full build with includeUnapproved=true (probe_limits_for is
        # also the gate on "full" in query.corpora -- a clean-only abstention
        # query gets no probe at all). report.py reads each probe beside its
        # own-limit default-flags "full" call to tell a gate-earned
        # abstention (the row is indexed and the gate held it back) from an
        # absence-earned one (nothing there under either flag), one limit at
        # a time -- a single limit's probe standing in for every plane would
        # assume an unstated count-monotonicity between limits. A
        # probe-flavored HarnessConstants, not a new _one_call parameter: the
        # flag `_one_call` reads is already `constants.include_unapproved`.
        probe_constants = dataclasses.replace(constants, include_unapproved=True)
        for limit in sorted(probe_limits):
            runs.append(_one_call(calls["full"], projects["full"], query, limit, probe_constants))
    return runs


def _one_call(
    call: ToolCall,
    project: BuiltProject,
    query: QueryEntry,
    limit: int,
    constants: HarnessConstants,
) -> QueryRun:
    started = time.monotonic()
    response = call(
        "knowledge.search",
        {
            "projectId": project.project_id,
            "query": query.query,
            "limit": limit,
            "includeUnapproved": constants.include_unapproved,
            "maxTokens": constants.max_tokens,
            "useDense": constants.use_dense,
        },
    )
    latency_ms = (time.monotonic() - started) * 1000
    return QueryRun(
        query_id=query.id,
        corpus=project.name,
        limit=limit,
        response=response,
        latency_ms=latency_ms,
        include_unapproved=constants.include_unapproved,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS, help="Fixture corpus root.")
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory to write report.json/timings.json into."
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
