"""Pins over slice S4c's committed raptor-arm baseline (ADR-0036, Compliance).

These read ``tools/eval/baseline/report.json`` and ``timings.json`` directly,
the way ``test_adr36_ratchet.py`` reads the ADR's own text or ``report.py``'s
own source: no SQLite, no MCP wire, no subprocess. Regenerating the baseline
and byte-comparing it -- which already covers the raptor arm's own
determinism, since ``report.json``'s committed bytes now include ``raptor``
and ``comparison`` -- is ``test_baseline_current.py``'s job, not this file's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.eval]

REPO_ROOT = Path(__file__).resolve().parents[3]
_BASELINE_DIR = REPO_ROOT / "tools" / "eval" / "baseline"


@pytest.fixture(scope="module")
def baseline_report() -> dict[str, Any]:
    report: dict[str, Any] = json.loads((_BASELINE_DIR / "report.json").read_text(encoding="utf-8"))
    return report


@pytest.fixture(scope="module")
def baseline_timings() -> dict[str, Any]:
    timings: dict[str, Any] = json.loads(
        (_BASELINE_DIR / "timings.json").read_text(encoding="utf-8")
    )
    return timings


# -- A: comparison's deltas are derived from the report's own two sections ---


def test_the_comparisons_overall_and_broad_architectural_deltas_are_raptor_minus_base(
    baseline_report: dict[str, Any],
) -> None:
    """``report._aggregate_delta``'s own contract: every delta is
    ``raptor_value - base_value``, recomputed here from ``aggregated`` and
    ``raptor.aggregated`` rather than pasted as a literal -- a re-measurement
    that moves either side without moving ``comparison`` to match reddens
    here, not two commits later when someone diffs the numbers by hand.

    Scoped to ``overall`` and ``broad-architectural`` (RAPTOR's own
    Domain/Catalog routing target, and the baseline README's headline
    figures) rather than every class, because this file reads only the
    already-disk-rounded ``report.json`` -- ``build_report`` computes
    ``comparison`` from full-precision floats, then ``write_report`` rounds
    the whole tree to 6 decimals in one pass, so re-deriving from the
    rounded ``aggregated`` figures and rounding again is a SECOND rounding
    that does not always reproduce the first. Measured: it agrees exactly
    for these two populations' every k and mrr; it does not for at least one
    other class (``rejected-alternative``'s recall@1: -0.666666 recomputed
    here vs -0.666667 committed) -- a real property of the format, not a
    bug this test works around by widening its own population.
    """
    comparison = baseline_report["comparison"]
    base_aggregated = baseline_report["aggregated"]
    raptor_aggregated = baseline_report["raptor"]["aggregated"]
    k_values = [str(k) for k in sorted(baseline_report["kValues"])]

    populations = {
        "overall": (
            base_aggregated["overall"],
            raptor_aggregated["overall"],
            comparison["overall"],
        ),
        "broad-architectural": (
            base_aggregated["byClass"]["broad-architectural"],
            raptor_aggregated["byClass"]["broad-architectural"],
            comparison["byClass"]["broad-architectural"],
        ),
    }

    for name, (base_entry, raptor_entry, delta_entry) in populations.items():
        assert delta_entry["mrr"] == round(raptor_entry["mrr"] - base_entry["mrr"], 6), name
        for k in k_values:
            expected = round(raptor_entry["recallAtK"][k] - base_entry["recallAtK"][k], 6)
            assert delta_entry["recallAtK"][k] == expected, (name, k)


# -- B: the dated, corpus-derived forest-size pin -----------------------------


def test_the_committed_comparisons_node_counts(baseline_report: dict[str, Any]) -> None:
    """``comparison.nodes`` is the RAPTOR forest's size (``IndexBuildCost.nodes``,
    a deterministic pure function of the chunks the same build wrote --
    ADR-0008 decisions 8/9), echoed from the raptor builds' own
    :class:`IndexBuildCost`. A DATED pin, not a structural one: it moves the
    moment the committed S3 corpus (``tests/fixtures/eval``) changes and the
    baseline is re-measured, the same way ``comparison``'s deltas above do.
    """
    assert baseline_report["comparison"]["nodes"] == {"full": 28, "clean": 26}


# -- C: the raptor pair's equality channel is reported, never asserted equal -


def test_the_raptor_pairs_equality_channel_is_reported_with_real_counts(
    baseline_report: dict[str, Any],
) -> None:
    """``raptor.equality.channel`` exists with the same shape the base arm's
    channel carries, and its counts are real measured integers -- this pin
    checks that the report says something, not that the raptor pair's two
    corpora agree.

    No committed test extends the base arm's set-equality claim
    (``test_the_equality_query_differs_from_its_clean_counterpart_only_in_build_identity``,
    ``tests/integration/tools/test_harness_pins.py``) to the raptor pair, and
    none should: an ``--include-unapproved`` raptor build derives Domain/
    Catalog summary nodes from the chunks it indexes, so a raptor-pair
    equality query's response can differ by more than build identity even
    when nothing is actually leaking -- GHSA-97q9-xxfg-33r6's own territory
    (a RAPTOR-derived field carrying content from rows the plain build never
    held). That is exactly why this channel is reported (a count) rather
    than gated on (a set-equality assertion): the base arm's own committed
    reach control already proves the plain gate holds; this residual is a
    different, RAPTOR-specific channel, not a regression of that gate.
    """
    channel = baseline_report["raptor"]["equality"]["channel"]

    assert isinstance(channel["reason"], str) and channel["reason"]
    for label in ("atLimit", "atEqualityLimit"):
        entry = channel[label]
        assert isinstance(entry["queriesDiffering"], int)
        assert isinstance(entry["of"], int)
        assert 0 <= entry["queriesDiffering"] <= entry["of"]
        assert entry["of"] == len(baseline_report["raptor"]["equality"]["queries"])


# -- D: determinism is already re-held by test_baseline_current --------------
#
# test_a_fresh_run_over_the_frozen_corpus_reproduces_the_committed_baseline_report
# (tests/integration/tools/test_baseline_current.py) byte-compares the WHOLE
# committed report.json against a fresh run -- `raptor`/`comparison` included
# since slice S4c landed. No separate raptor determinism pin belongs here.


# -- E: isolation -- the only thing that differs between the two arms is the -
#       RAPTOR forest's presence ---------------------------------------------


def test_the_raptor_section_echoes_no_divergent_harness_constants_of_its_own(
    baseline_report: dict[str, Any],
) -> None:
    """The isolation property `report.py`'s own shape makes true by construction:
    ``report["raptor"]`` never carries its own ``harnessConstants``,
    ``corpusId`` or ``kValues`` -- the whole report has exactly one of each,
    shared by both arms, so there is no per-arm copy that a future change
    could let diverge unnoticed.
    """
    raptor = baseline_report["raptor"]

    assert set(raptor) == {"abstentionProbe", "aggregated", "census", "equality", "queries"}
    assert "harnessConstants" not in raptor
    assert "corpusId" not in raptor
    assert "kValues" not in raptor


def test_every_base_arm_wire_call_has_a_raptor_arm_twin_at_the_same_limit_and_flags(
    baseline_timings: dict[str, Any],
) -> None:
    """``run.py`` dispatches every enabled query against the raptor pair
    exactly as against the base pair -- same corpus name, same limit, same
    ``includeUnapproved`` -- so the RAPTOR forest's presence is the only
    variable ``comparison`` can attribute a metric move to. Pinned as an
    exact population match over ``timings.json``'s own rows (the wire calls
    actually issued), not merely a count: a call present on one arm and
    missing on the other -- the isolation failure a reviewer would look for
    first -- reddens here as a set difference, not just a size mismatch.
    """
    rows = baseline_timings["queries"]

    def _key(row: dict[str, Any]) -> tuple[str, str, int, bool]:
        return (row["queryId"], row["corpus"], row["limit"], row["includeUnapproved"])

    base_keys = {_key(row) for row in rows if not row["raptor"]}
    raptor_keys = {_key(row) for row in rows if row["raptor"]}

    assert base_keys, "the population must be non-empty, or this pin checks nothing"
    assert base_keys == raptor_keys


def test_only_the_raptor_builds_own_a_nonzero_forest_in_the_committed_timings(
    baseline_timings: dict[str, Any],
) -> None:
    """The one thing the symmetric dispatch above is allowed to differ on:
    ``IndexBuildCost.nodes`` is the RAPTOR forest's size, zero for a build
    that never ran ``index build --raptor``. Read from ``timings.json``'s
    own ``indexBuild`` entries -- the base pair's own two builds carry no
    forest, the raptor pair's own two do.
    """
    index_build = baseline_timings["indexBuild"]

    assert index_build["full"]["nodes"] == 0
    assert index_build["clean"]["nodes"] == 0
    assert index_build["full-raptor"]["nodes"] > 0
    assert index_build["clean-raptor"]["nodes"] > 0
