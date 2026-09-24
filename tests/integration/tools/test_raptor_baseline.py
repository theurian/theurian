"""Pins over slice S4c's committed raptor-arm baseline (ADR-0036, Compliance).

Most of these read ``tools/eval/baseline/report.json`` and ``timings.json``
directly, the way ``test_adr36_ratchet.py`` reads the ADR's own text or
``report.py``'s own source: no SQLite, no MCP wire, no subprocess.
Regenerating the baseline and byte-comparing it -- which already covers the
raptor arm's own determinism, since ``report.json``'s committed bytes now
include ``raptor`` and ``comparison`` -- is ``test_baseline_current.py``'s
job, not this file's. Two pins are the exception (a live ``build_report``
call, and a real raptor-enabled build over the S3 corpus): each says why in
its own docstring.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Final

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.eval]

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import corpus as harness_corpus  # noqa: E402
import corpus_build as harness_build  # noqa: E402
import report as harness_report  # noqa: E402
from wire import mcp_session  # noqa: E402

from theurian.application.project_service import ProjectRegistry  # noqa: E402
from theurian.daemon.runner import build_server  # noqa: E402

_BASELINE_DIR = REPO_ROOT / "tools" / "eval" / "baseline"
CORPUS = REPO_ROOT / "tests" / "fixtures" / "eval"


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


# -- A: every comparison delta, every population, within a measured tolerance

#: The double-rounding residual `comparison`'s own derivation can carry:
#: `write_report` rounds the whole tree to 6 decimals in ONE pass, after
#: `comparison` is already computed from full-precision `aggregated` floats,
#: so re-deriving from the already-rounded figures here is a SECOND rounding
#: that does not always reproduce the first. Plus headroom for float-
#: representation noise in the comparison itself: Python's own
#: ``0.018519 - 0.018518`` is ``1.000000000001e-06``, not exactly the
#: mathematical ``1e-06`` a naive ``<= 1e-6`` literal would require. Measured
#: worst case over the whole committed baseline (every class, every k, every
#: family): ``1.0000000000287557e-06``, at
#: ``rejected-alternative``'s recall@1 -- comfortably inside this tolerance,
#: never enough to hide a genuine second mismatch (a real corruption is
#: orders of magnitude larger).
_DELTA_TOLERANCE: Final = 1e-6 + 1e-9

#: The `_aggregate_entries` families `_aggregate_delta` subtracts (mirrors
#: `report._DELTA_FAMILIES`, read as a literal here for the same reason the
#: channel-reason pins below compare against literals, not the constant).
_DELTA_FAMILIES: Final = (
    "mrr",
    "evidencePrecision",
    "abstentionAccuracy",
    "supersededKnowledgeErrorRate",
)


def test_every_comparison_delta_in_every_population_is_raptor_minus_base_within_tolerance(
    baseline_report: dict[str, Any],
) -> None:
    """``report._aggregate_delta``'s own contract, over every population
    ``comparison`` publishes -- ``overall`` and every ``byClass`` member, not
    only the two the narrower predecessor of this pin covered. Every delta is
    ``raptor_value - base_value``, within ``_DELTA_TOLERANCE`` above, not
    pasted as a literal. ``cross-adr`` -- the concentrated-cost class the
    baseline's own README singles out -- is in this population now, along
    with `conflicting`, `exact-decision`, `superseded` and `unknown`.

    The re-measurement scenario the narrower predecessor of this pin named
    ("a re-measurement that moves either side without moving ``comparison``
    to match") cannot actually happen: ``comparison`` is computed from
    ``aggregated``/``raptor.aggregated`` inside the same ``build_report``
    call that produces both, so the two can never legitimately disagree in a
    committed baseline. What this pin's population actually reaches is a
    HAND-EDITED ``report.json`` -- one number changed in any population
    without recomputing its dependents -- which is exactly the failure a
    derivation pin exists to catch.
    """
    comparison = baseline_report["comparison"]
    base_aggregated = baseline_report["aggregated"]
    raptor_aggregated = baseline_report["raptor"]["aggregated"]
    k_values = [str(k) for k in sorted(baseline_report["kValues"])]

    populations = {
        "overall": (base_aggregated["overall"], raptor_aggregated["overall"], comparison["overall"])
    }
    for cls in base_aggregated["byClass"]:
        populations[cls] = (
            base_aggregated["byClass"][cls],
            raptor_aggregated["byClass"][cls],
            comparison["byClass"][cls],
        )
    assert set(populations) == set(comparison["byClass"]) | {"overall"}

    for name, (base_entry, raptor_entry, delta_entry) in populations.items():
        assert (
            raptor_entry["sampleCount"] == base_entry["sampleCount"] == delta_entry["sampleCount"]
        ), name

        for family in _DELTA_FAMILIES:
            base_value = base_entry[family]
            raptor_value = raptor_entry[family]
            stored = delta_entry[family]
            if base_value is None or raptor_value is None:
                assert stored is None, (name, family)
                continue
            assert abs(stored - round(raptor_value - base_value, 6)) <= _DELTA_TOLERANCE, (
                name,
                family,
            )

        for k in k_values:
            base_value = base_entry["recallAtK"].get(k)
            raptor_value = raptor_entry["recallAtK"].get(k)
            stored = delta_entry["recallAtK"].get(k)
            if base_value is None or raptor_value is None:
                assert stored is None, (name, "recallAtK", k)
                continue
            expected = round(raptor_value - base_value, 6)
            assert abs(stored - expected) <= _DELTA_TOLERANCE, (name, "recallAtK", k)


# -- B: the dated, corpus-derived forest-size pin -----------------------------


def test_the_committed_comparisons_node_counts(baseline_report: dict[str, Any]) -> None:
    """``comparison.nodes`` is the RAPTOR forest's size (``IndexBuildCost.nodes``),
    echoed from the raptor builds' own :class:`IndexBuildCost`, keyed
    ``full-raptor``/``clean-raptor`` to match ``timings.json``'s own
    ``indexBuild`` suffix. Deterministic because the default
    ``SummarizationProvider`` is extractive (ADR-0008 decision 7): tree
    derivation is a pure function of the surviving rows for a provider with
    that property, which is the property decision 7 chooses the extractive
    default for. A DATED pin, not a structural one: it moves the moment the
    committed S3 corpus (``tests/fixtures/eval``) changes and the baseline is
    re-measured, the same way ``comparison``'s deltas above do.
    """
    assert baseline_report["comparison"]["nodes"] == {"full-raptor": 28, "clean-raptor": 26}


# -- C: the raptor channel's counts are derived, and its own strings pinned --


def test_the_raptor_channels_counts_are_derived_from_its_own_per_query_differing_fields(
    baseline_report: dict[str, Any],
) -> None:
    """adversarial MEDIUM M1: the shape-only predecessor of this pin
    (``isinstance``/bounds checks alone) could not fail -- swapping in the
    BASE arm's own channel, or an all-zero stub with a matching ``of``, both
    satisfied every assertion it made, because nothing read the raptor arm's
    own per-query data. This recomputes each count directly from
    ``raptor.equality.queries``' own ``differingFields`` entries -- the same
    population ``report._channel_summary`` reads, counting a query as
    differing when its set exceeds ``report._BUILD_IDENTITY_EXEMPT`` -- and
    asserts equality with the published counts: an independent derivation,
    not a shape check.

    **Why this channel is reported, never asserted equal, and why it is
    measured wider today.** The threat model's own T-17a row
    (``docs/security/threat-model.md``) is the accurate citation, not an
    invented one: GHSA-97q9-xxfg-33r6 closes the *purge-failed*-build cell --
    a build that still holds withdrawn rows can leak one through a
    ``--raptor`` build's ``raptorPath[].title``, and the advisory's fix is to
    refuse serving such a build at all. That is a different failure mode
    than "the raptor pair's two corpora disagree by more than build
    identity", which is what this channel counts; the base arm's own
    ``EQUALITY_SCOPE`` claim (query-time gate over draft/proposed rows) does
    not, on its own, explain today's widening either. The real control is
    the approved-content population itself: the committed census shows
    ``full`` and ``clean`` legitimately disagree on *approved* content alone
    (26 vs 24 approved items, ``report.json``'s own ``census``), before
    RAPTOR enters at all, and RAPTOR's node-routing layer gives that
    pre-existing, legitimate difference more surface to show up on --
    clustering, node counts and excerpts all move with which approved
    documents exist, not with which unapproved ones leaked through. What
    rules OUT the unapproved-leak explanation is
    ``test_no_raptor_path_title_in_the_full_arms_default_response_leaks_an_unapproved_body``
    below: it verifies, over a real build, that no title in the full-raptor
    arm's default-flags responses contains any unapproved fixture body's
    text -- this pin's teeth for that claim, not an assumption backing it.

    A dated expectation, not a structural one: today the raptor channel
    differs from the base arm's at ``atLimit`` (20 vs 18) and agrees with it
    at ``atEqualityLimit`` (21 == 21) -- both read here from the committed
    baseline, and both move the moment the corpus or the harness does.
    """
    exempt = harness_report._BUILD_IDENTITY_EXEMPT
    raptor_queries = baseline_report["raptor"]["equality"]["queries"]
    assert raptor_queries, "the population must be non-empty, or this pin checks nothing"
    base_channel = baseline_report["equality"]["channel"]
    raptor_channel = baseline_report["raptor"]["equality"]["channel"]

    for label in ("atLimit", "atEqualityLimit"):
        derived = sum(
            1 for entry in raptor_queries.values() if set(entry[label]["differingFields"]) - exempt
        )
        assert raptor_channel[label]["queriesDiffering"] == derived, label
        assert raptor_channel[label]["of"] == len(raptor_queries), label

    assert raptor_channel["atLimit"]["queriesDiffering"] == 20
    assert base_channel["atLimit"]["queriesDiffering"] == 18
    assert (
        raptor_channel["atLimit"]["queriesDiffering"] != base_channel["atLimit"]["queriesDiffering"]
    )
    assert (
        raptor_channel["atEqualityLimit"]["queriesDiffering"]
        == base_channel["atEqualityLimit"]["queriesDiffering"]
        == 21
    )


#: Literal copies of ``report.RAPTOR_EQUALITY_SCOPE``/``report._RAPTOR_CHANNEL_REASON``,
#: not references to the constants themselves -- the same discipline the base
#: arm's ``_CHANNEL_REASON_LITERAL`` pin uses
#: (``tests/integration/tools/test_harness_pins.py``): comparing the module's
#: published string to the same constant that produced it is structurally
#: unfailable, whatever the constant is later edited to say.
_RAPTOR_EQUALITY_SCOPE_LITERAL = (
    "Query-time gate over draft/proposed rows admitted to the index by "
    "--include-unapproved on both builds, plus the RAPTOR node-traversal "
    "gate (ADR-0008 decision 8) between a matched summary node and the "
    "leaves it may route to; every query in this section runs at default "
    "flags (includeUnapproved=false)."
)
_RAPTOR_CHANNEL_REASON_LITERAL = (
    "recorded channel, T-17a family and RAPTOR summary routing (ADR-0008 "
    "decision 8, GHSA-97q9's raptorPath territory); not a disclosure finding "
    "because includeUnapproved is a request parameter (not a grant) and the "
    "Core is one-principal (#119); reachable only under the operator's "
    "--include-unapproved AND --raptor build, absent from the shipped default."
)


def test_the_raptor_arms_own_equality_scope_and_channel_reason_are_pinned_verbatim(
    baseline_report: dict[str, Any],
) -> None:
    """security M1's pin half: the raptor arm's ``equality.scope`` and
    ``equality.channel.reason`` are its OWN strings (a code MEDIUM finding
    this round fixed -- they used to be the base arm's, reused verbatim and
    under-describing the ``--raptor`` condition), pinned by exact equality
    against literal copies above, never against ``report.py``'s own
    constants -- the same self-reference trap the derivation pin above
    replaced a shape-only check to avoid.
    """
    assert baseline_report["raptor"]["equality"]["scope"] == _RAPTOR_EQUALITY_SCOPE_LITERAL
    assert (
        baseline_report["raptor"]["equality"]["channel"]["reason"] == _RAPTOR_CHANNEL_REASON_LITERAL
    )


# -- D: no raptorPath title leaks an unapproved fixture body -----------------


def _fixture_bodies_by_approval(loaded: harness_corpus.Corpus) -> tuple[list[str], list[str]]:
    """Every revision body, split into ``(unapproved, approved)`` by this
    build's own default-flags surfaceability -- unapproved means draft,
    proposed, rejected, superseded or deprecated status, or above-ceiling
    (confidential/restricted) sensitivity; everything else is approved. Read
    straight from the fixture files ``tests/fixtures/eval/migrations``
    reference, via the same replay ``corpus.py``'s own loader uses
    (``_final_status_and_sensitivity``) rather than a second, hand-rolled
    classification.
    """
    documents = {
        entry.file: harness_corpus._load_migration_document(CORPUS / "migrations" / entry.file)
        for entry in loaded.manifest.migrations
    }
    all_ids = harness_corpus._all_item_ids(loaded.manifest, documents)
    status_by_item, sensitivity_by_item = harness_corpus._final_status_and_sensitivity(
        loaded.manifest, documents, all_ids
    )
    unapproved_ids = {
        item_id
        for item_id in all_ids
        if status_by_item.get(item_id) not in harness_corpus.DEFAULT_SURFACEABLE_STATUSES
        or sensitivity_by_item.get(item_id) not in harness_corpus.BUILD_CEILING_SENSITIVITIES
    }
    unapproved: list[str] = []
    approved: list[str] = []
    for entry in loaded.manifest.migrations:
        for op in documents[entry.file].get("operations", []):
            if op.get("op") != "upsertRevision":
                continue
            text = (CORPUS / "migrations" / op["contentFile"]).read_text(encoding="utf-8")
            (unapproved if op.get("itemId") in unapproved_ids else approved).append(text)
    return unapproved, approved


#: Below this, a window is common enough English to false-positive (matches
#: the ``_ARTIFACT_LEAK_MIN_LENGTH`` convention in this directory's
#: ``test_harness_pins.py``).
_RAPTOR_TITLE_LEAK_WINDOW: Final = 16

_WHITESPACE_RUN: Final = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace -- including a markdown blank line --
    to one space, and strip the ends.

    Load-bearing, not cosmetic: a RAPTOR title never carries a body's own
    newlines verbatim (measured -- a real title reads as flattened,
    single-spaced prose), so comparing RAW body text against a title
    manufactures a "distinctive" window out of pure formatting. Measured
    case: ``security.draft-scan-hardening`` (unapproved) references "the
    approved secret scanning policy in one place", while
    ``security.secret-scan-policy`` (approved) heads itself "# Secret
    scanning policy\\n\\nTwo secret scans ..." -- the two texts share
    "scanning policy" verbatim and differ only in what follows it, a space
    against a markdown blank line, which without normalization is enough to
    make ``"scanning policy "`` read as absent from the approved corpus.
    """
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _leaked_windows(
    titles: list[str], unapproved_bodies: list[str], approved_bodies: list[str]
) -> list[str]:
    """Every ``>=16``-char verbatim window of ``titles`` that is DISTINCTIVE
    to the unapproved population -- present in some (whitespace-normalized)
    string in ``unapproved_bodies`` and absent from every (whitespace-
    normalized) string in ``approved_bodies`` -- the substring-leak detector
    both the real pin below and its teeth
    (``test_leaked_windows_catches_a_planted_span_and_excludes_an_approved_shared_one``,
    a hand-built ``titles`` list) run against.

    **Walks the TITLES, at step 1 -- not the bodies, as an earlier version
    did (security HIGH).** That version slid a 16-char window across each
    BODY at step 8, which guarantees full coverage only for a leaked span
    ``>= 23`` chars (``16 + 8 - 1``): a planted 16-char distinctive span
    starting at an offset not aligned to 8 (a reviewer's reproduction used
    offset 11) sits inside no step-8 window and went undetected, while 16 is
    this very function's own distinctiveness floor. Titles are the
    responses' own text -- a few hundred characters each -- so walking them
    at step 1 instead gives EXACT coverage of every ``>=16``-char span at
    any offset, and is cheaper besides: a handful of short haystacks
    substring-searched into the (larger, but few) body strings, rather than
    thousands of body-windows searched into the (larger, many) title
    strings.

    The distinctiveness check is load-bearing, not decorative -- two false
    positives found while writing this, both benign topical overlap between
    an unapproved item and an approved one on the same subject, neither an
    actual leak: ``domain.session-token-ttl-v1``/``-v2`` (superseded) share
    their opening heading, ``# Session token TTL policy``, with
    ``domain.session-token-ttl`` (approved, the same topic evolved forward);
    ``security.draft-scan-hardening`` (draft) names ``security.secret-scan-
    policy`` (approved) by its own heading text, "scanning policy". Without
    excluding windows the approved corpus already carries (whitespace-
    normalized, see :func:`_normalize_whitespace`), either would
    false-positive here, and a real knowledge base has more of both --
    supersession and cross-referencing -- than this one fixture does.
    """
    joined_unapproved = "\x00".join(_normalize_whitespace(body) for body in unapproved_bodies)
    joined_approved = "\x00".join(_normalize_whitespace(body) for body in approved_bodies)
    leaked: list[str] = []
    for title in titles:
        normalized_title = _normalize_whitespace(title)
        last_start = max(len(normalized_title) - _RAPTOR_TITLE_LEAK_WINDOW, 0)
        for start in range(0, last_start + 1):
            window = normalized_title[start : start + _RAPTOR_TITLE_LEAK_WINDOW]
            if len(window) != _RAPTOR_TITLE_LEAK_WINDOW:
                continue
            if window in joined_unapproved and window not in joined_approved:
                leaked.append(window)
    return leaked


def _distinctive_window_population(unapproved_bodies: list[str], approved_bodies: list[str]) -> int:
    """How many ``>=16``-char windows of ``unapproved_bodies`` (whitespace-
    normalized, stepped by 8 across each body) are absent from every
    (whitespace-normalized) ``approved_bodies`` string -- the candidate
    population :func:`_leaked_windows` would have something to find IF a
    leak existed. A population of zero would make a green
    ``_leaked_windows(...) == []`` meaningless: nothing distinctive to find,
    not nothing found. Step 8 here (not the exact, step-1 coverage the
    detector above needs) is fine for a population-floor sanity check --
    undercounting only makes the floor stricter, never wrongly satisfied.
    """
    joined_approved = "\x00".join(_normalize_whitespace(body) for body in approved_bodies)
    count = 0
    for body in unapproved_bodies:
        normalized_body = _normalize_whitespace(body)
        last_start = max(len(normalized_body) - _RAPTOR_TITLE_LEAK_WINDOW, 0)
        for start in range(0, last_start + 1, 8):
            window = normalized_body[start : start + _RAPTOR_TITLE_LEAK_WINDOW]
            if len(window) == _RAPTOR_TITLE_LEAK_WINDOW and window not in joined_approved:
                count += 1
    return count


def test_leaked_windows_catches_a_planted_span_and_excludes_an_approved_shared_one() -> None:
    """``_leaked_windows``'s own docstring names this teeth -- a hand-built
    ``titles`` list, not a real build -- and it did not exist: security
    MEDIUM, ``git grep -n _leaked_windows`` returned only the function's own
    definition and the one call inside the real pin below.

    Also drives the security HIGH's own reproduction: the planted span sits
    at offset 11 in ``unapproved_body``, the exact offset a step-8 body-walk
    could not reach (``16 + 8 - 1 = 23`` was the shortest span that walk
    guaranteed), and the shared window proves the approved-body exclusion
    still holds under the new, inverted (title-walked) detector.
    """
    shared_prefix = "Shared opening clause about the policy. "
    unapproved_body = "xxxxxxxxxxx" + "UNIQUE-WITHDRAWN" + "yyyyyyyyyyyyyyyy" + shared_prefix
    approved_body = "Ordinary approved prose, nothing distinctive. " + shared_prefix

    distinctive_window = "UNIQUE-WITHDRAWN"
    shared_window = shared_prefix[:_RAPTOR_TITLE_LEAK_WINDOW]
    assert len(distinctive_window) == _RAPTOR_TITLE_LEAK_WINDOW
    assert unapproved_body[11:27] == distinctive_window, (
        "must sit at offset 11, the HIGH's own reproduction"
    )
    assert distinctive_window not in approved_body
    assert shared_window in unapproved_body
    assert shared_window in approved_body

    planted_title = f"Domain summary node: {distinctive_window} routes here"
    shared_title = f"Domain summary node: {shared_window} routes here"

    assert _leaked_windows([planted_title], [unapproved_body], [approved_body]) == [
        distinctive_window
    ]
    assert _leaked_windows([shared_title], [unapproved_body], [approved_body]) == []


def test_no_raptor_path_title_in_the_full_arms_default_response_leaks_an_unapproved_body() -> None:
    """security MEDIUM M2's teeth: ``IndexStore._node_scope``
    (``packages/theurian-core/src/theurian/infrastructure/sqlite/index_store.py``,
    ~1517) applies the same status/sensitivity predicates to a summary
    node's own scope that a leaf match clears -- "a draft-scope or
    above-ceiling summary node is ... not even traversed on a default
    query". This VERIFIES that claim over the real S3 corpus rather than
    assuming it.

    ``report.json`` stores only ``differingFields`` paths, never response
    bodies, so this is one of the two pins in this file that drives a real
    build: a raptor-enabled build (``index build --raptor``) over the
    committed corpus, then every enabled query at DEFAULT flags against the
    ``full`` plane, walking every ``results[*].raptorPath[*].title`` the
    responses actually carry and checking each against the UNAPPROVED
    fixture bodies (draft, proposed, rejected, superseded, deprecated status,
    or confidential/restricted sensitivity) rather than the approved ones.

    A green ``== []`` on ``_leaked_windows`` is meaningless without a
    non-trivial candidate population behind it -- security MEDIUM: the
    distinctive-window filter could in principle exclude everything, making
    "nothing found" read as "nothing distinctive existed to find" rather
    than "checked and clean". Measured (whitespace-normalized, this file's
    own S3 corpus): 1,434 windows survive the approved-body filter; a dated
    floor well below that (1,000) is asserted so this pin still means
    something as the fixture corpus grows or shrinks slightly, without being
    pinned to an exact count a routine content edit would move.
    """
    loaded = harness_corpus.load_corpus(CORPUS)
    unapproved_bodies, approved_bodies = _fixture_bodies_by_approval(loaded)
    assert unapproved_bodies, "the population must be non-empty, or this pin checks nothing"
    assert approved_bodies, "the population must be non-empty, or this pin checks nothing"
    survivors = _distinctive_window_population(unapproved_bodies, approved_bodies)
    assert survivors >= 1000, (
        f"only {survivors} distinctive windows survived the approved-body filter "
        f"(measured 1434) -- too few for a green _leaked_windows(...) == [] "
        f"result to mean anything"
    )

    titles: list[str] = []
    with tempfile.TemporaryDirectory(prefix="theurian-eval-raptor-title-leak-") as workspace_name:
        built = harness_build.build_both(loaded, Path(workspace_name), raptor=True)
        with ExitStack() as sessions:
            project = built.projects["full"]
            call = sessions.enter_context(
                mcp_session(
                    build_server(ProjectRegistry.default(project.data_dir)), project.data_dir
                )
            )
            for query in loaded.queries:
                if not query.enabled:
                    continue
                response = call(
                    "knowledge.search",
                    {
                        "projectId": project.project_id,
                        "query": query.query,
                        "limit": 10,
                        "maxTokens": 32_000,
                        "includeUnapproved": False,
                    },
                )
                for hit in response["results"]:
                    for segment in hit.get("raptorPath") or []:
                        title = segment.get("title")
                        if title:
                            titles.append(title)

    assert titles, "the population must be non-empty, or this pin checks nothing"
    assert _leaked_windows(titles, unapproved_bodies, approved_bodies) == []


# -- E: determinism is already re-held by test_baseline_current --------------
#
# test_a_fresh_run_over_the_frozen_corpus_reproduces_the_committed_baseline_report
# (tests/integration/tools/test_baseline_current.py) byte-compares the WHOLE
# committed report.json against a fresh run -- `raptor`/`comparison` included
# since slice S4c landed. No separate raptor determinism pin belongs here.


# -- F: isolation -- the only thing that differs between the two arms is the -
#       RAPTOR forest's presence ---------------------------------------------


def test_the_raptor_sections_key_set_drops_exactly_corpus_id_k_values_and_harness_constants() -> (
    None
):
    """code MEDIUM M3's pin half: ``report._RAPTOR_SECTION_DROPPED_KEYS`` is a
    DROP-list (this round's fix) -- everything ``build_report`` publishes
    flows to both arms by default, not only the keys an allowlist happened
    to name -- verified against a REAL, live ``build_report``/
    ``_raptor_section`` call, not the committed ``report.json``, which could
    silently drift from what ``report.py`` now produces until someone
    re-runs the harness and re-commits it. The cheapest honest instrument: a
    one-query synthetic corpus, no SQLite, no subprocess -- the same
    ``build_report`` call ``test_adr36_ratchet.py``'s own
    ``_synthetic_report`` fixture drives (``tests/unit/tools/``), not
    imported cross-file since the two files are independently owned.
    """
    manifest = harness_corpus.Manifest(
        contract_version=1,
        corpus_id="raptor-shape-pin",
        k_values=(1,),
        migrations=(),
        census={},
        description=None,
    )
    query = harness_corpus.QueryEntry(
        id="q", query_class="exact-decision", query="text", enabled=True, corpora=("full",)
    )
    judgement = harness_corpus.JudgementEntry(
        query_id="q",
        relevant=(harness_corpus.JudgedItem(item_id="a"),),
        evidence=(),
        forbidden=(),
        expect_abstention=False,
    )
    loaded = harness_corpus.Corpus(
        root=Path(),
        manifest=manifest,
        queries=(query,),
        judgements=(judgement,),
        withheld_coverage=(),
    )
    constants = harness_report.HarnessConstants(
        limit=10,
        max_tokens=1000,
        include_unapproved=False,
        use_dense=False,
        equality_limit=50,
        build_ceiling="internal",
    )
    response: dict[str, Any] = {"count": 1, "results": [{"itemId": "a", "sourceAnchors": []}]}
    runs = [
        harness_report.QueryRun(
            query_id="q", corpus="full", limit=10, response=response, latency_ms=1.0
        )
    ]
    census = {
        "full": harness_corpus.CorpusCensus(
            items=1, by_status={"approved": 1}, by_sensitivity={"public": 1}, chunks=1
        )
    }

    full = harness_report.build_report(loaded, constants, runs, census)
    section = harness_report._raptor_section(loaded, constants, runs, census)

    assert set(full) - set(section) == {"corpusId", "kValues", "harnessConstants"}


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
