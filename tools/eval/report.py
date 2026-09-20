"""Assemble the deterministic ``report.json`` and dated ``timings.json`` (ADR-0036 decision 7).

The split exists so a re-run whose metrics did not move produces an empty
diff on ``report.json``: no commit sha, no timestamp, no wall-clock figure
lives in it. Everything dated -- the commit, the environment, per-query
latency, index-build cost -- goes in ``timings.json`` instead.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from corpus import Corpus, CorpusCensus, JudgementEntry, QueryEntry
from corpus_build import IndexBuildCost
from metrics import (
    abstention_correct,
    differing_paths,
    evidence_precision,
    forbidden_present,
    mrr,
    recall_at_k,
)

#: An equality query names exactly two corpora ("full" and "clean") -- the
#: whole set the manifest schema's `corpora` enum admits.
_EQUALITY_CORPORA_COUNT: Final = 2

#: What the equality battery actually measures (ADR-0036, the gate-vs-census
#: derivation rule): with plain builds a status-withheld row never enters the
#: index, making the comparison vacuous for the mechanism it exists to catch.
#: Both builds index with `--include-unapproved`; every query here still runs
#: at default flags, so what is compared is the query-time gate over the
#: draft/proposed rows the index now holds, never the build-time exclusion.
EQUALITY_SCOPE: Final = (
    "Query-time gate over draft/proposed rows admitted to the index by "
    "--include-unapproved on both builds; every query in this section runs "
    "at default flags (includeUnapproved=false)."
)

#: A judgement's forbidden-trap contribution reads zero whenever every
#: forbidden item is census-tested, i.e. excluded from the index under either
#: build flavor -- a build-time property this harness's corpora already
#: guarantee, not a ranking signal. Annotated rather than silently trusted, so
#: a zero this cause does not honestly explain is left unannotated instead.
_FORBIDDEN_ZERO_BY_CONSTRUCTION: Final = (
    "zero-by-construction: every forbidden item is census-tested -- excluded "
    "from the index under either build flavor, so its absence is a "
    "build-time property, not evidence about ranking"
)

#: What `aggregated`'s mean is computed over, stated so the report says it
#: rather than leaving a reader to assume every corpus a query ran against
#: contributed a sample.
_AGGREGATION_POPULATION: Final = (
    "full-corpus runs only; a query's clean run, where one exists, is not "
    "counted here -- it exists for the equality section's cross-corpus "
    "comparison, not as a second sample of the same judgement"
)


@dataclass(frozen=True, slots=True)
class HarnessConstants:
    """Fixed parameters every query is run under, echoed into ``report.json``.

    ``limit`` is derived from the loaded corpus (``max(kValues)``) rather than
    hardcoded, since a k beyond it could never be reached by a real response.
    No threshold or target value lives here (ADR-0036 decision 4) -- this
    records what was asked, never what would count as good.
    """

    limit: int
    max_tokens: int
    include_unapproved: bool
    use_dense: bool
    equality_limit: int
    build_ceiling: str


@dataclass(frozen=True, slots=True)
class QueryRun:
    query_id: str
    corpus: str
    limit: int
    response: dict[str, Any]
    latency_ms: float


def build_report(
    loaded: Corpus,
    constants: HarnessConstants,
    runs: Sequence[QueryRun],
    census: Mapping[str, CorpusCensus],
) -> dict[str, Any]:
    queries_section: dict[str, Any] = {}
    for query in loaded.queries:
        if not query.enabled:
            continue
        judgement = loaded.judgement_for(query.id)
        if judgement is None:
            msg = f"loader invariant violated: enabled query {query.id!r} has no judgement"
            raise RuntimeError(msg)
        queries_section[query.id] = _query_entry(query, judgement, constants, runs, loaded)

    return {
        "corpusId": loaded.manifest.corpus_id,
        "kValues": list(loaded.manifest.k_values),
        "harnessConstants": {
            "limit": constants.limit,
            "maxTokens": constants.max_tokens,
            "includeUnapproved": constants.include_unapproved,
            "useDense": constants.use_dense,
            "equalityLimit": constants.equality_limit,
            "buildCeiling": constants.build_ceiling,
        },
        "census": {name: _census_dict(value) for name, value in census.items()},
        "queries": queries_section,
        "equality": {
            "scope": EQUALITY_SCOPE,
            "queries": _equality_section(loaded, constants, runs),
        },
        "aggregated": _aggregate(queries_section, loaded.manifest.k_values),
    }


def _query_entry(
    query: QueryEntry,
    judgement: JudgementEntry,
    constants: HarnessConstants,
    runs: Sequence[QueryRun],
    loaded: Corpus,
) -> dict[str, Any]:
    k_values = loaded.manifest.k_values
    corpora_section: dict[str, Any] = {}
    is_equality = len(set(query.corpora)) > 1
    for corpus_name in sorted(set(query.corpora)):
        base = _find_run(runs, query.id, corpus_name, constants.limit)
        entry = _query_metrics(base.response, judgement, k_values, loaded)
        if is_equality:
            widened = _find_run(runs, query.id, corpus_name, constants.equality_limit)
            entry["atEqualityLimit"] = _query_metrics(widened.response, judgement, k_values, loaded)
        corpora_section[corpus_name] = entry
    return {"class": query.query_class, "corpora": corpora_section}


def _query_metrics(
    response: dict[str, Any], judgement: JudgementEntry, k_values: Sequence[int], loaded: Corpus
) -> dict[str, Any]:
    recall = {
        str(k): value
        for k in sorted(k_values)
        if (value := recall_at_k(response, judgement, k)) is not None
    }
    metrics: dict[str, Any] = {
        "recallAtK": recall,
        "mrr": mrr(response, judgement),
        "evidencePrecision": evidence_precision(response, judgement),
        "forbiddenPresent": forbidden_present(response, judgement),
        "abstentionCorrect": abstention_correct(response, judgement),
    }
    cause = _forbidden_zero_cause(judgement, loaded)
    if cause is not None:
        metrics["forbiddenPresentCause"] = cause
    return metrics


def _forbidden_zero_cause(judgement: JudgementEntry, loaded: Corpus) -> str | None:
    """Why a ``forbiddenPresent: false`` result is guaranteed rather than earned.

    Only stated when every forbidden item is a withheld-plane item classified
    census-tested: excluded from the index under both build flavors, so its
    absence proves nothing about ranking. A forbidden item this harness cannot
    classify (a visible-plane item, or a gate-tested one that could genuinely
    leak) leaves the cause undecided, and this returns ``None`` rather than
    guessing.
    """
    if not judgement.forbidden:
        return None
    classifications = [loaded.coverage_for(item.item_id) for item in judgement.forbidden]
    if all(c is not None and not c.is_gate_tested for c in classifications):
        return _FORBIDDEN_ZERO_BY_CONSTRUCTION
    return None


def _equality_section(
    loaded: Corpus, constants: HarnessConstants, runs: Sequence[QueryRun]
) -> dict[str, Any]:
    """Per equality query, the fields differing between its two corpora.

    ADR-0036 decision 6: the only fields permitted to differ are exactly
    ``{retrieval.indexBuildId, retrieval.snapshotId}``. Run at both the
    harness's own ``limit`` and at ``equalityLimit`` -- a difference can be
    absorbed inside a smaller result window and only surface at the wider one.
    """
    section: dict[str, Any] = {}
    for query in loaded.queries:
        corpora = sorted(set(query.corpora))
        if not query.enabled or len(corpora) != _EQUALITY_CORPORA_COUNT:
            continue
        left_name, right_name = corpora
        entry: dict[str, Any] = {}
        for label, limit in (
            ("atLimit", constants.limit),
            ("atEqualityLimit", constants.equality_limit),
        ):
            left = _find_run(runs, query.id, left_name, limit)
            right = _find_run(runs, query.id, right_name, limit)
            entry[label] = {
                "limit": limit,
                "differingFields": sorted(differing_paths(left.response, right.response)),
            }
        section[query.id] = entry
    return section


def _find_run(runs: Sequence[QueryRun], query_id: str, corpus_name: str, limit: int) -> QueryRun:
    for run in runs:
        if run.query_id == query_id and run.corpus == corpus_name and run.limit == limit:
            return run
    raise KeyError(
        f"no wire call recorded for query={query_id!r} corpus={corpus_name!r} limit={limit}"
    )


def _aggregate(queries_section: Mapping[str, Any], k_values: Sequence[int]) -> dict[str, Any]:
    """Aggregate recall/MRR/evidence precision over ``full``-corpus runs only.

    A query's ``clean`` run exists for the equality section's cross-corpus
    comparison, not as a second sample of the same relevant/forbidden/
    evidence judgement -- folding it in here would double-weight every
    equality query's contribution to the mean. A query that never ran against
    ``full`` (an unusual ``corpora`` choice) contributes no sample.

    Per-class means nest under ``byClass`` rather than sitting flat beside
    ``overall`` and ``population``: a query class named either word would
    otherwise collide with them. The queries schema's closed ``class`` enum
    makes that unreachable today, but a schema edit adding such a class would
    make the collision silent rather than a visible key clash.
    """
    samples: list[tuple[str, dict[str, Any]]] = []
    for entry in queries_section.values():
        full_metrics = entry["corpora"].get("full")
        if full_metrics is not None:
            samples.append((entry["class"], full_metrics))

    by_class: dict[str, list[dict[str, Any]]] = {}
    for class_name, metrics in samples:
        by_class.setdefault(class_name, []).append(metrics)

    return {
        "byClass": {name: _aggregate_entries(items, k_values) for name, items in by_class.items()},
        "overall": _aggregate_entries([metrics for _, metrics in samples], k_values),
        "population": _AGGREGATION_POPULATION,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _share(values: list[bool]) -> float | None:
    return sum(1 for value in values if value) / len(values) if values else None


def _aggregate_entries(
    entries: Sequence[dict[str, Any]], k_values: Sequence[int]
) -> dict[str, Any]:
    recall = {
        str(k): mean
        for k in sorted(k_values)
        if (mean := _mean([e["recallAtK"][str(k)] for e in entries if str(k) in e["recallAtK"]]))
        is not None
    }
    return {
        "recallAtK": recall,
        "mrr": _mean([e["mrr"] for e in entries if e["mrr"] is not None]),
        "evidencePrecision": _mean(
            [e["evidencePrecision"] for e in entries if e["evidencePrecision"] is not None]
        ),
        "supersededKnowledgeErrorRate": _share(
            [e["forbiddenPresent"] for e in entries if e["forbiddenPresent"] is not None]
        ),
        "abstentionAccuracy": _share(
            [e["abstentionCorrect"] for e in entries if e["abstentionCorrect"] is not None]
        ),
        "sampleCount": len(entries),
    }


def _census_dict(census: CorpusCensus) -> dict[str, Any]:
    return {
        "items": census.items,
        "byStatus": dict(census.by_status),
        "bySensitivity": dict(census.by_sensitivity),
        "chunks": census.chunks,
    }


def _round(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round(item) for item in value]
    return value


def write_report(report: dict[str, Any], path: Path) -> None:
    text = json.dumps(_round(report), sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _commit_sha(repo_root: Path) -> str:
    """This repository's ``HEAD``, or ``"unknown"`` if it cannot be read.

    ``timings.json`` is the dated annex, environment-dependent by design
    (ADR-0036 decision 7) -- a copied tree with no ``.git``, or a mutation
    sweep's throwaway checkout, is a real environment this runs in, not a
    defect to crash on.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def build_timings(
    loaded: Corpus,
    runs: Sequence[QueryRun],
    build_costs: Mapping[str, IndexBuildCost],
    repo_root: Path,
) -> dict[str, Any]:
    return {
        "date": datetime.now(UTC).isoformat(),
        "commitSha": _commit_sha(repo_root),
        "corpusId": loaded.manifest.corpus_id,
        "platform": platform.platform(),
        "pythonVersion": sys.version,
        "sqliteVersion": sqlite3.sqlite_version,
        "indexBuild": {name: _cost_dict(cost) for name, cost in build_costs.items()},
        "queries": [
            {
                "queryId": run.query_id,
                "corpus": run.corpus,
                "limit": run.limit,
                "latencyMs": round(run.latency_ms, 3),
            }
            for run in sorted(runs, key=lambda r: (r.query_id, r.corpus, r.limit))
        ],
    }


def _cost_dict(cost: IndexBuildCost) -> dict[str, Any]:
    return {
        "wallClockMs": round(cost.wall_clock_ms, 3),
        "chunks": cost.chunks,
        "embeddings": cost.embeddings,
        "nodes": cost.nodes,
        "indexBytes": cost.index_bytes,
    }


def write_timings(timings: dict[str, Any], path: Path) -> None:
    text = json.dumps(timings, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
