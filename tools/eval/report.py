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

#: The keys `_raptor_section` drops from a full `build_report` call over the
#: raptor arm's own runs -- shared verbatim by both arms (one corpus, one set
#: of constants), so repeating them under `report["raptor"]` would be noise,
#: not a second measurement. Everything else `build_report` ever publishes --
#: `abstentionProbe` today, and a key neither arm's author has written yet --
#: flows to both arms by default, which an allowlist of the current keys
#: could not guarantee for the ones still to come.
_RAPTOR_SECTION_DROPPED_KEYS: Final = frozenset({"corpusId", "kValues", "harnessConstants"})

#: The raptor arm's own equality scope (Phase A slice S4c): everything
#: `EQUALITY_SCOPE` names, plus the RAPTOR node-traversal gate a raptor build
#: additionally exercises -- a matched summary node still has to clear
#: `_may_surface` at every descended leaf before that leaf may surface
#: (ADR-0008 decision 8's routing-only invariant), so this arm's equality
#: queries exercise a second gate the base arm's own scope does not name.
RAPTOR_EQUALITY_SCOPE: Final = (
    "Query-time gate over draft/proposed rows admitted to the index by "
    "--include-unapproved on both builds, plus the RAPTOR node-traversal "
    "gate (ADR-0008 decision 8) between a matched summary node and the "
    "leaves it may route to; every query in this section runs at default "
    "flags (includeUnapproved=false)."
)

#: The raptor arm's own channel reason (Phase A slice S4c). `_CHANNEL_REASON`
#: names `--include-unapproved` as the reachable condition, which is true of
#: the base arm's own T-17a residual but under-describes this one: reaching
#: it also needs `--raptor` (ADR-0008 decision 8, GHSA-97q9's `raptorPath`
#: territory), so quoting the base string here would omit half of what makes
#: this channel unreachable from the shipped default.
_RAPTOR_CHANNEL_REASON: Final = (
    "recorded channel, T-17a family and RAPTOR summary routing (ADR-0008 "
    "decision 8, GHSA-97q9's raptorPath territory); not a disclosure finding "
    "because includeUnapproved is a request parameter (not a grant) and the "
    "Core is one-principal (#119); reachable only under the operator's "
    "--include-unapproved AND --raptor build, absent from the shipped default."
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


@dataclass(frozen=True, slots=True)
class RaptorArm:
    """The raptor-on arm's own measurements (Phase A slice S4c), bundled so
    ``build_report``/``build_timings`` each take one extra parameter rather
    than three -- ``runs``, ``census`` and ``build_costs`` always travel
    together, one call to ``corpus_build.build_both(..., raptor=True)`` and
    one query loop over it (``run.py``).
    """

    runs: Sequence[QueryRun]
    census: Mapping[str, CorpusCensus]
    build_costs: Mapping[str, IndexBuildCost]


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
    *,
    raptor: RaptorArm | None = None,
) -> dict[str, Any]:
    """Assemble the base arm's report, and -- when ``raptor`` is given -- the
    raptor arm's own ``raptor`` section and the ``comparison`` block between
    the two (Phase A slice S4c).

    ``raptor.runs`` symmetric to ``runs``: the same enabled queries, against
    the same corpora, at the same limits, with the same #787 abstention
    probes -- the only variable the comparison isolates is the RAPTOR
    forest's presence (``run.py`` is what keeps the two calls symmetric).
    """
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
    if raptor is not None:
        report["raptor"] = _raptor_section(loaded, constants, raptor.runs, raptor.census)
        report["comparison"] = _comparison(
            report["aggregated"],
            report["raptor"]["aggregated"],
            loaded.manifest.k_values,
            # `-raptor`-suffixed, matching `build_timings`'s own `indexBuild`
            # keys for these same builds -- a bare `full`/`clean` here would
            # answer two different questions under one key (`indexBuild.full
            # .nodes` is the BASE build's forest, always 0; this is the
            # RAPTOR build's).
            {f"{name}-raptor": cost.nodes for name, cost in raptor.build_costs.items()},
        )
    return report


def _raptor_section(
    loaded: Corpus,
    constants: HarnessConstants,
    runs: Sequence[QueryRun],
    census: Mapping[str, CorpusCensus],
) -> dict[str, Any]:
    """The raptor arm's per-query, equality and aggregate metrics (Phase A slice S4c).

    Built by recursing into :func:`build_report` over the raptor arm's own
    ``runs``/``census`` with no ``raptor`` of its own -- the same per-query,
    equality and channel machinery the base arm uses, so the two can never
    drift in shape, and a future ``build_report`` key reaches both arms
    without this function naming it (:data:`_RAPTOR_SECTION_DROPPED_KEYS`).

    Its ``equality.scope`` and ``equality.channel.reason`` are relabelled
    after the recursive call returns, not threaded through
    :func:`build_report` as parameters: the two strings are pure description,
    read by nothing the counts depend on, and adding them as
    :func:`build_report` arguments would grow that signature by two for a
    value only this one caller ever varies. Its ``differingFields`` sets are
    reported, not asserted, for a different reason than the base arm's own
    ``EQUALITY_SCOPE`` gives: the two builds derive their forests over
    different chunk populations, so node routing (ADR-0008 decision 8)
    surfaces a different selection and ordering of APPROVED leaves on each
    side -- a wider set here is expected rather than a regression. It is not
    unapproved text reaching a default-flag response, and that is verified
    rather than assumed: ``IndexStore._node_scope`` applies the same status
    and sensitivity predicates to a summary node's own scope that a leaf
    match clears, and
    ``test_no_raptor_path_title_in_the_full_arms_default_response_leaks_an_unapproved_body``
    (``tests/integration/tools/test_raptor_baseline.py``) drives a real
    ``--raptor`` build and checks every ``raptorPath[].title`` a default-flag
    response actually carries against the unapproved fixture bodies. Exactly
    the channel ``_channel_summary`` already reports rather than gates on,
    reused verbatim (only its ``reason`` changes) rather than widening the
    base arm's own set-equality claim to cover it.
    """
    full = build_report(loaded, constants, runs, census)
    section = {key: value for key, value in full.items() if key not in _RAPTOR_SECTION_DROPPED_KEYS}
    section["equality"] = {**section["equality"], "scope": RAPTOR_EQUALITY_SCOPE}
    section["equality"]["channel"] = {
        **section["equality"]["channel"],
        "reason": _RAPTOR_CHANNEL_REASON,
    }
    return section


def _comparison(
    base_aggregated: Mapping[str, Any],
    raptor_aggregated: Mapping[str, Any],
    k_values: Sequence[int],
    raptor_build_nodes: Mapping[str, int],
) -> dict[str, Any]:
    """Raptor-on minus raptor-off, over the ``full``-corpus default-flag runs (decision 4: deltas
    only, no judgment about whether a move is good or bad).

    Every :func:`_aggregate_entries` family gets a delta -- ``recallAtK``,
    ``mrr``, ``evidencePrecision``, ``abstentionAccuracy`` and
    ``supersededKnowledgeErrorRate`` -- plus ``sampleCount`` as context (not a
    delta: both arms measure the same population, so this is the shared
    denominator a reader needs to weigh the others by, not a second number to
    subtract). A block naming only two of five families would let a mover in
    one of the other three go unpublished.

    Both sides' ``byClass`` share one key set: the classes come from the same
    loaded queries against the same judged corpus, RAPTOR only ever moving
    which rows rank where. Every family follows :func:`_aggregate_entries`'s
    own ``None``-for-empty-denominator convention -- a class or k either side
    has no sample for stays out of the delta rather than reading as a false
    zero.
    """
    return {
        "byClass": {
            name: _aggregate_delta(raptor_aggregated["byClass"][name], entry, k_values)
            for name, entry in base_aggregated["byClass"].items()
        },
        "overall": _aggregate_delta(
            raptor_aggregated["overall"], base_aggregated["overall"], k_values
        ),
        "nodes": dict(raptor_build_nodes),
    }


#: The `_aggregate_entries` families `_aggregate_delta` subtracts straight
#: (every one but `recallAtK`, which is per-k and handled separately, and
#: `sampleCount`, which is context rather than a delta).
_DELTA_FAMILIES: Final = (
    "mrr",
    "evidencePrecision",
    "abstentionAccuracy",
    "supersededKnowledgeErrorRate",
)


def _aggregate_delta(
    raptor_entry: Mapping[str, Any], base_entry: Mapping[str, Any], k_values: Sequence[int]
) -> dict[str, Any]:
    recall = {
        str(k): delta
        for k in sorted(k_values)
        if (
            delta := _delta(
                raptor_entry["recallAtK"].get(str(k)), base_entry["recallAtK"].get(str(k))
            )
        )
        is not None
    }
    deltas: dict[str, Any] = {
        family: _delta(raptor_entry[family], base_entry[family]) for family in _DELTA_FAMILIES
    }
    return {"recallAtK": recall, **deltas, "sampleCount": base_entry["sampleCount"]}


def _delta(raptor_value: float | None, base_value: float | None) -> float | None:
    return None if raptor_value is None or base_value is None else raptor_value - base_value


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
    *,
    raptor: RaptorArm | None = None,
) -> dict[str, Any]:
    """The dated annex, over both arms when ``raptor`` is given.

    A raptor query row shares ``(queryId, corpus, limit, includeUnapproved)`` with its base-arm
    counterpart -- both arms' :class:`BuiltProject` are named ``"full"``/``"clean"`` (see
    ``corpus_build.build_both``) -- so ``raptor`` is a fifth field on every row rather than a
    fifth key in the tuple a caller might already be matching on.
    """
    index_build = {name: _cost_dict(cost) for name, cost in build_costs.items()}
    if raptor is not None:
        index_build.update(
            {f"{name}-raptor": _cost_dict(cost) for name, cost in raptor.build_costs.items()}
        )
    tagged_runs = [(run, False) for run in runs] + [
        (run, True) for run in (raptor.runs if raptor is not None else ())
    ]
    return {
        "date": datetime.now(UTC).isoformat(),
        "commitSha": _commit_sha(repo_root),
        "corpusId": loaded.manifest.corpus_id,
        "platform": platform.platform(),
        "pythonVersion": sys.version,
        "sqliteVersion": sqlite3.sqlite_version,
        "indexBuild": index_build,
        "queries": [
            {
                "queryId": run.query_id,
                "corpus": run.corpus,
                "raptor": is_raptor,
                "limit": run.limit,
                "includeUnapproved": run.include_unapproved,
                "latencyMs": round(run.latency_ms, 3),
            }
            for run, is_raptor in sorted(
                tagged_runs,
                key=lambda pair: (
                    pair[0].query_id,
                    pair[0].corpus,
                    pair[1],
                    pair[0].limit,
                    pair[0].include_unapproved,
                ),
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
