"""CLI entry point for the Phase A retrieval-evaluation harness (ADR-0036).

Builds two projects from one fixture corpus -- ``full`` (every migration) and
``clean`` (visible-plane only) -- runs every enabled query against the
corpora it names over the real MCP wire, and writes ``report.json`` (the
deterministic metrics pin) and ``timings.json`` (the dated annex), per
decision 7's split. The harness produces measurements; it asserts nothing
(decision 4) -- there is no pass/fail threshold anywhere in this module.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Final

from corpus import BUILD_CEILING, CorpusError, QueryEntry, load_corpus
from corpus_build import BuiltProject, build_both
from report import (
    HarnessConstants,
    QueryRun,
    build_report,
    build_timings,
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
        except CorpusError as exc:
            print(f"build refused: {exc}", file=sys.stderr)
            return 1

        runs: list[QueryRun] = []
        with ExitStack() as sessions:
            calls = {
                name: sessions.enter_context(
                    mcp_session(
                        build_server(ProjectRegistry.default(project.data_dir)), project.data_dir
                    )
                )
                for name, project in built.projects.items()
            }
            for query in loaded.queries:
                if not query.enabled:
                    continue
                runs.extend(_run_query(calls, built.projects, query, constants))

        census = {name: project.census for name, project in built.projects.items()}
        build_costs = {name: project.build_cost for name, project in built.projects.items()}
        report = build_report(loaded, constants, runs, census)
        timings = build_timings(loaded, runs, build_costs, REPO_ROOT)

    write_report(report, args.out / "report.json")
    write_timings(timings, args.out / "timings.json")
    return 0


def _run_query(
    calls: dict[str, ToolCall],
    projects: dict[str, BuiltProject],
    query: QueryEntry,
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
