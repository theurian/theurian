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
    probe_returned_hit,
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

#: #787's flag-probe cause: an expectAbstention query's default-flags call
#: returned nothing, but the same query against the full build with
#: includeUnapproved=true returned something -- the row is indexed and only
#: the query-time gate withheld it, not absence. Mirrors
#: _FORBIDDEN_ZERO_BY_CONSTRUCTION's convention: annotated when the cause is
#: derived, never guessed; unannotated (absence-earned) when the flagged call
#: also returns nothing.
_ABSTENTION_GATE_WITHHELD: Final = (
    "gate-withheld: the row is in the index and the default-flag gate held it back"
)

#: What decision 6 permits an equality query's two responses to differ on --
#: which artifact answered, never what it answered. Matches `differing_paths`'
#: own dotted-path notation. Pinned equal to the integration pin's own
#: `BUILD_IDENTITY` (tests/integration/tools/test_harness_pins.py) by a test
#: in the tests pass that follows this one -- not merely asserted here.
_BUILD_IDENTITY_EXEMPT: Final = frozenset({"retrieval.indexBuildId", "retrieval.snapshotId"})

#: The two labels an equality query's per-limit entry carries. Shared by
#: `_equality_section` (which pairs each with its own `HarnessConstants`
#: limit) and `_channel_summary` (which counts by the same two labels), so
#: the population either function reads cannot silently drift from the
#: other's.
_EQUALITY_LABELS: Final = ("atLimit", "atEqualityLimit")

#: The channel report's reason, verbatim (#787, ADR-0036 Amendment 1 rider 1):
#: why the T-17a residual the channel summary counts stays inside the
#: disclosure boundary rather than reading as a finding. Quoted, never
#: paraphrased -- "single-user" is specifically wrong here, since the daemon
#: serves many agents.
_CHANNEL_REASON: Final = (
    "recorded channel, T-17a family; not a disclosure finding because "
    "includeUnapproved is a request parameter (not a grant) and the Core is "
    "one-principal (#119); reachable only under the operator's "
    "--include-unapproved build, absent from the shipped default."
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
    """Fixed parameters every query's DEFAULT-FLAGS call runs under, echoed
    into ``report.json``'s ``harnessConstants``.

    ``limit`` is derived from the loaded corpus (``max(kValues)``) rather than
    hardcoded, since a k beyond it could never be reached by a real response.
    No threshold or target value lives here (ADR-0036 decision 4) -- this
    records what was asked, never what would count as good.

    Not every wire call: #787's abstention flag-probe runs under a
    ``dataclasses.replace(constants, include_unapproved=True)`` copy that
    never reaches ``harnessConstants`` -- that a probe ran, and at which
    limits, is what ``report.json``'s sibling ``abstentionProbe`` member
    (built by :func:`_abstention_probe_summary`) records instead.
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
    #: True only for #787's abstention flag-probe call -- every ordinary run
    #: is a default-flags call. Needed because the probe shares (query_id,
    #: corpus, limit) with the ordinary "full" run it exists to compare
    #: against, and `_find_run` must not confuse the two.
    include_unapproved: bool = False


def probe_limits_for(
    query: QueryEntry, judgement: JudgementEntry, constants: HarnessConstants
) -> frozenset[int]:
    """Which limits #787's flag-probe runs at for ``query``, or empty if it doesn't.

    The one place that decision is made: `run.py` calls this to know which
    probe wire calls to issue, and :func:`_abstention_probe_summary` reads
    the probes ``run.py`` actually issued rather than re-deriving this a
    second time, so the two can never drift apart.

    Empty when the judgement does not expect abstention, or when ``query``
    never runs against ``full`` at all -- ``clean`` never held a withheld row
    under either flag (ADR-0036, the gate-vs-census derivation rule), so a
    probe against it would test nothing. Otherwise mirrors the limits
    ``query`` itself runs at: the harness's own ``limit``, plus the equality
    limit when ``query`` runs against both corpora -- one probe per plane, so
    each plane's cause derives from its own-limit call rather than a
    different plane's.
    """
    if not judgement.expect_abstention or "full" not in set(query.corpora):
        return frozenset()
    limits = {constants.limit}
    if len(set(query.corpora)) > 1:
        limits.add(constants.equality_limit)
    return frozenset(limits)


def _abstention_probe_summary(runs: Sequence[QueryRun]) -> dict[str, Any] | None:
    """The #787 flag-probe's own record, beside ``harnessConstants``, or ``None``.

    Read off which :class:`QueryRun`\\ s were actually issued with
    ``includeUnapproved=true`` -- never re-derived from the corpus a second
    time, which would be a second copy of :func:`probe_limits_for`'s decision,
    free to drift from the first. Still deterministic: which calls exist and
    at which limits is fixed by the corpus and the harness constants, never
    by wall-clock order -- only ``latency_ms`` and the wire response bytes
    carry timing or environment, and neither is read here. ``None`` when the
    corpus's judgements never expect abstention against ``full``: no probe
    ran, so nothing to record.
    """
    limits = sorted({run.limit for run in runs if run.include_unapproved})
    if not limits:
        return None
    return {"includeUnapproved": True, "limits": limits}


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

    equality_queries = _equality_section(loaded, constants, runs)
    report: dict[str, Any] = {
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
            "queries": equality_queries,
            "channel": _channel_summary(equality_queries),
        },
        "aggregated": _aggregate(queries_section, loaded.manifest.k_values),
    }
    probe_summary = _abstention_probe_summary(runs)
    if probe_summary is not None:
        report["abstentionProbe"] = probe_summary
    return report


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
        probe = _abstention_probe_response(runs, query, judgement, corpus_name, constants.limit)
        entry = _query_metrics(base.response, judgement, k_values, loaded, probe)
        if is_equality:
            widened = _find_run(runs, query.id, corpus_name, constants.equality_limit)
            widened_probe = _abstention_probe_response(
                runs, query, judgement, corpus_name, constants.equality_limit
            )
            entry["atEqualityLimit"] = _query_metrics(
                widened.response, judgement, k_values, loaded, widened_probe
            )
        corpora_section[corpus_name] = entry
    return {"class": query.query_class, "corpora": corpora_section}


def _abstention_probe_response(
    runs: Sequence[QueryRun],
    query: QueryEntry,
    judgement: JudgementEntry,
    corpus_name: str,
    limit: int,
) -> dict[str, Any] | None:
    """The #787 flag-probe response feeding `corpus_name`'s metrics at ``limit``, or ``None``.

    Scoped to ``full``: ``clean`` never held a withheld row under either flag
    (ADR-0036, the gate-vs-census derivation rule) -- a probe read against it
    would test nothing. Keyed on ``limit`` because a probe runs at each limit
    ``query`` itself runs at (`run.py`'s use of :func:`probe_limits_for`): the
    ``atEqualityLimit`` plane must read its own-limit probe, never the base
    plane's -- a single limit-10 probe standing in for both planes would be
    sound only via an unstated count-monotonicity between the two.
    """
    if not judgement.expect_abstention or corpus_name != "full":
        return None
    return _find_run(runs, query.id, "full", limit, include_unapproved=True).response


def _query_metrics(
    response: dict[str, Any],
    judgement: JudgementEntry,
    k_values: Sequence[int],
    loaded: Corpus,
    abstention_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recall = {
        str(k): value
        for k in sorted(k_values)
        if (value := recall_at_k(response, judgement, k)) is not None
    }
    correct = abstention_correct(response, judgement)
    metrics: dict[str, Any] = {
        "recallAtK": recall,
        "mrr": mrr(response, judgement),
        "evidencePrecision": evidence_precision(response, judgement),
        "forbiddenPresent": forbidden_present(response, judgement),
        "abstentionCorrect": correct,
    }
    cause = _forbidden_zero_cause(judgement, loaded)
    if cause is not None:
        metrics["forbiddenPresentCause"] = cause
    abstention_cause = _abstention_cause(correct, abstention_probe)
    if abstention_cause is not None:
        metrics["abstentionCause"] = abstention_cause
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


def _abstention_cause(correct: bool | None, probe: dict[str, Any] | None) -> str | None:
    """Why an earned ``abstentionCorrect: true`` is gate-earned rather than absence-earned.

    ``probe`` is #787's flag-probe response (the same query against the full
    build with ``includeUnapproved=true``). A non-earned or unclassifiable
    outcome (``correct`` is not ``True``, or no probe was run) leaves the
    cause undecided -- ``None`` rather than a guess, the same convention
    :func:`_forbidden_zero_cause` uses. Where the probe still returns nothing,
    the zero is absence-earned, the default reading, and stays unannotated.

    Aggregation note, matching the ``forbiddenPresentCause`` convention this
    mirrors: this cause annotates a sample, it does not split or exclude it --
    ``_aggregate_entries``'s blended ``abstentionAccuracy`` mean still counts
    every non-``None`` ``abstentionCorrect``, gate-earned or absence-earned
    alike, the same way an annotated ``forbiddenPresent`` zero still counts
    toward ``supersededKnowledgeErrorRate``.
    """
    if not correct or probe is None:
        return None
    return _ABSTENTION_GATE_WITHHELD if probe_returned_hit(probe) else None


def _equality_section(
    loaded: Corpus, constants: HarnessConstants, runs: Sequence[QueryRun]
) -> dict[str, Any]:
    """Per equality query, the fields differing between its two corpora.

    ADR-0036 decision 6: the only fields permitted to differ are exactly
    ``{retrieval.indexBuildId, retrieval.snapshotId}``. Run at both the
    harness's own ``limit`` and at ``equalityLimit`` -- a difference can be
    absorbed inside a smaller result window and only surface at the wider one.
    """
    limit_by_label = {"atLimit": constants.limit, "atEqualityLimit": constants.equality_limit}
    section: dict[str, Any] = {}
    for query in loaded.queries:
        corpora = sorted(set(query.corpora))
        if not query.enabled or len(corpora) != _EQUALITY_CORPORA_COUNT:
            continue
        left_name, right_name = corpora
        entry: dict[str, Any] = {}
        for label in _EQUALITY_LABELS:
            limit = limit_by_label[label]
            left = _find_run(runs, query.id, left_name, limit)
            right = _find_run(runs, query.id, right_name, limit)
            entry[label] = {
                "limit": limit,
                "differingFields": sorted(differing_paths(left.response, right.response)),
            }
        section[query.id] = entry
    return section


def _channel_summary(section: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """The T-17a residual counted, not asserted (ADR-0036 Amendment 1 rider 1; #787).

    ``of`` is `section`'s own population -- the enabled queries that actually
    ran on both corpora -- never the harness's whole query count. A query
    counts as differing at a given limit when its ``differingFields`` there
    holds something beyond :data:`_BUILD_IDENTITY_EXEMPT`, the two fields
    naming which artifact answered rather than what it answered. Pure over
    `section`'s already-computed responses: no timing, no sha.
    """
    channel: dict[str, Any] = {"reason": _CHANNEL_REASON}
    for label in _EQUALITY_LABELS:
        differing = sum(
            1
            for entry in section.values()
            if set(entry[label]["differingFields"]) - _BUILD_IDENTITY_EXEMPT
        )
        channel[label] = {"queriesDiffering": differing, "of": len(section)}
    return channel


def _find_run(
    runs: Sequence[QueryRun],
    query_id: str,
    corpus_name: str,
    limit: int,
    *,
    include_unapproved: bool = False,
) -> QueryRun:
    for run in runs:
        if (
            run.query_id == query_id
            and run.corpus == corpus_name
            and run.limit == limit
            and run.include_unapproved == include_unapproved
        ):
            return run
    raise KeyError(
        f"no wire call recorded for query={query_id!r} corpus={corpus_name!r} "
        f"limit={limit} includeUnapproved={include_unapproved}"
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
                "includeUnapproved": run.include_unapproved,
                "latencyMs": round(run.latency_ms, 3),
            }
            for run in sorted(
                runs, key=lambda r: (r.query_id, r.corpus, r.limit, r.include_unapproved)
            )
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
