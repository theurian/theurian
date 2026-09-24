"""Committed integration pins for the Phase A harness (ADR-0036, Compliance).

Determinism (decisions 5 and 7), the disclosure-equality set comparison
(decision 6) with its reach and companion controls, and the one loader
refusal that needs a real build (``census-mismatch``) all drive the CLI,
SQLite and the MCP wire through ``tools/eval`` -- unit weight cannot reach
them. ``tools/eval`` is a flat script directory, not a package: this file
puts it on ``sys.path`` itself, the way
``tests/ci/test_red_team_sweep_prose.py`` puts ``tools/`` on its own.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.eval]

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import corpus as harness_corpus  # noqa: E402
import corpus_build as harness_build  # noqa: E402
import metrics as harness_metrics  # noqa: E402
import report as harness_report  # noqa: E402
import run as harness_run  # noqa: E402
from wire import ToolCall, mcp_session  # noqa: E402

from theurian.application.project_service import ProjectRegistry  # noqa: E402
from theurian.daemon.runner import build_server  # noqa: E402

SMOKE_CORPUS = REPO_ROOT / "tests" / "fixtures" / "eval-smoke"

#: The two `retrieval` fields, bare -- for indexing into `response["retrieval"]`.
BUILD_IDENTITY_FIELDS = ("indexBuildId", "snapshotId")

#: What ADR-0036 decision 6 permits an equality query's two responses to
#: differ on: which artifact answered, never what it answered. Dotted, to
#: match `differing_paths`' own path notation.
BUILD_IDENTITY = frozenset(f"retrieval.{field}" for field in BUILD_IDENTITY_FIELDS)

#: Matches only `knowledge/architecture/gateway-policy.md`, present identically
#: in both planes -- picked to share no vocabulary with the gate-tested
#: runbook, so this comparison is not accidentally reading the other channel.
EQUALITY_QUERY = "authenticates inbound requests"

#: The gate-tested (draft) withheld item's synthetic incident key (see
#: manifest.yaml's header comment for its derivation). Since 6ef2b606, both
#: builds index with `--include-unapproved`, so this row sits in `full`'s
#: index; a default-flags query must still not surface it (the query-time
#: gate T-17a is about), which the reach control below proves is a real gate
#: rather than the row's simple absence.
WITHHELD_QUERY = "0710645F7E85DCE09F4B"

#: `token-rotation-policy`'s own text: the draft runbook shares "rotation"/
#: "ledger"/"immediately" vocabulary with its own body, so it genuinely
#: competes for this query rather than merely sitting in the corpus.
#: `test_the_competing_vocabulary_querys_candidacy_is_an_enforced_premise`
#: below turns that candidacy into an assertion instead of a comment: a
#: corpus edit that drops the shared vocabulary, or approves the deprecated
#: item, reddens there instead of silently returning the set-equality
#: parametrization to the vacuous state (nothing left to displace).
COMPETING_VOCABULARY_QUERY = "What is the token rotation policy?"

#: The visible ids `COMPETING_VOCABULARY_QUERY` reaches at default flags,
#: identical between `full` and `clean` -- enforced, not merely measured.
COMPETING_VOCABULARY_VISIBLE_IDS = frozenset(
    {"security.token-rotation", "architecture.gateway-policy", "architecture.retry-policy"}
)


@pytest.fixture(scope="module")
def smoke_calls() -> Iterator[dict[str, Any]]:
    """One build of the smoke corpus's two planes, with an open wire session each."""
    loaded = harness_corpus.load_corpus(SMOKE_CORPUS)
    with tempfile.TemporaryDirectory(prefix="theurian-eval-pin-") as workspace_name:
        workspace = Path(workspace_name)
        built = harness_build.build_both(loaded, workspace)
        with ExitStack() as sessions:
            calls: dict[str, ToolCall] = {
                name: sessions.enter_context(
                    mcp_session(
                        build_server(ProjectRegistry.default(project.data_dir)), project.data_dir
                    )
                )
                for name, project in built.projects.items()
            }
            yield {"calls": calls, "projects": built.projects}


def _search(
    smoke_calls: dict[str, Any],
    corpus_name: str,
    query: str,
    *,
    include_unapproved: bool = False,
) -> dict[str, Any]:
    project = smoke_calls["projects"][corpus_name]
    call: ToolCall = smoke_calls["calls"][corpus_name]
    return call(
        "knowledge.search",
        {
            "projectId": project.project_id,
            "query": query,
            "limit": 10,
            "maxTokens": 32_000,
            "includeUnapproved": include_unapproved,
        },
    )


# -- A: the disclosure-equality set comparison, and its two controls ---------


@pytest.mark.parametrize(
    "query",
    [WITHHELD_QUERY, EQUALITY_QUERY, COMPETING_VOCABULARY_QUERY],
    ids=["gate-tested-vocabulary", "shared-vocabulary", "competing-vocabulary"],
)
def test_the_equality_query_differs_from_its_clean_counterpart_only_in_build_identity(
    smoke_calls: dict[str, Any], query: str
) -> None:
    """ADR-0036 decision 6 (Compliance), held under 6ef2b606's fix.

    Both builds index with ``--include-unapproved``, so the gate-tested draft
    row genuinely sits in ``full``'s index (the reach control below proves
    it) rather than being absent from both by construction; every query here
    still runs at default flags, so what this measures is the query-time
    gate over that row, never a build-time exclusion. A subset check would
    also pass a harness that had stopped publishing ``retrieval.indexBuildId``,
    or whose ``retrieval.snapshotId`` had gone insensitive to canonical
    state -- both are contract changes that should be decided, not silently
    absorbed by a comparison that quietly widens.

    The third parameter is the adversarial case: unlike the other two, the
    draft row is a genuine ranking *candidate* for this query (see
    ``COMPETING_VOCABULARY_QUERY``'s own measurement) and >=2 visible items
    are in play, so this is where a leak through candidate displacement or
    BM25 collection statistics would actually have somewhere to show up.
    """
    full = _search(smoke_calls, "full", query)
    clean = _search(smoke_calls, "clean", query)

    moved = harness_metrics.differing_paths(full, clean)

    assert moved == BUILD_IDENTITY


def test_the_default_flag_gate_hides_the_draft_row_the_include_unapproved_flag_reveals(
    smoke_calls: dict[str, Any],
) -> None:
    """The reach control the set-equality pin above needs to mean anything.

    Without this, the equality above could hold because the draft row never
    reached ``full``'s index at all -- the T-17a vacuity 6ef2b606 closed --
    rather than because a default-flags query correctly withholds a row that
    genuinely is there. Mirrors ``test_the_depth_probe_reaches_the_withheld_
    document_inside_the_candidate_depth``'s discipline in ``test_mcp_tools.
    py``: the same wire path, with one flag flipped, must surface the row in
    ``full`` and never in ``clean``, which held no such row under either flag
    (zero-only-counts: a green equality battery over an unreachable row
    proves nothing).
    """
    full_default = _search(smoke_calls, "full", WITHHELD_QUERY)
    full_unapproved = _search(smoke_calls, "full", WITHHELD_QUERY, include_unapproved=True)
    clean_unapproved = _search(smoke_calls, "clean", WITHHELD_QUERY, include_unapproved=True)

    assert full_default["count"] == 0, "a default-flags query must not surface the draft row"
    assert full_unapproved["count"] > 0, "the same row, reached the same way, gate opened"
    assert any(hit["itemId"] == "security.incident-runbook" for hit in full_unapproved["results"])
    assert clean_unapproved["count"] == 0, (
        "clean never held the row at all, flag or no flag -- opening the gate finds nothing"
    )


def test_the_competing_vocabulary_querys_candidacy_is_an_enforced_premise(
    smoke_calls: dict[str, Any],
) -> None:
    """The other reach control the set-equality pin above needs: without it,
    the ``competing-vocabulary`` parametrization could pass vacuously (no
    shared visible hits left to displace) with nothing here to notice.

    (a) At default flags both planes reach the same >=2 visible ids for
    ``COMPETING_VOCABULARY_QUERY`` -- the vocabulary the draft row shares
    with them, not asserted. (b) With ``includeUnapproved=true`` against
    ``full``, the same query surfaces the draft row as a genuine ranking
    candidate, and never in ``clean``, which holds no such row under either
    flag.
    """
    full_default = _search(smoke_calls, "full", COMPETING_VOCABULARY_QUERY)
    clean_default = _search(smoke_calls, "clean", COMPETING_VOCABULARY_QUERY)
    full_unapproved = _search(
        smoke_calls, "full", COMPETING_VOCABULARY_QUERY, include_unapproved=True
    )
    clean_unapproved = _search(
        smoke_calls, "clean", COMPETING_VOCABULARY_QUERY, include_unapproved=True
    )

    full_default_ids = [hit["itemId"] for hit in full_default["results"]]
    clean_default_ids = [hit["itemId"] for hit in clean_default["results"]]
    full_unapproved_ids = [hit["itemId"] for hit in full_unapproved["results"]]
    clean_unapproved_ids = [hit["itemId"] for hit in clean_unapproved["results"]]

    assert len(set(full_default_ids)) >= 2, (
        "the set-equality parametrization above needs >=2 real visible hits to displace"
    )
    assert full_default_ids == clean_default_ids, "both planes must reach the same visible ids"
    assert set(full_default_ids) == COMPETING_VOCABULARY_VISIBLE_IDS

    assert "security.incident-runbook" in full_unapproved_ids
    assert "security.incident-runbook" not in clean_unapproved_ids


def test_both_build_identity_fields_are_constant_and_nonempty_within_one_build(
    smoke_calls: dict[str, Any],
) -> None:
    """The set-equality pin's companion (ADR-0036 decision 6, Compliance).

    A field left out of a comparison is a field nothing checks, so the two
    fields the comparison above excludes are checked here instead: they must
    be the same for every query against one project, and non-empty. Mirrors
    `test_the_build_identity_a_search_reports_does_not_vary_with_the_query`.
    """
    responses = [
        _search(smoke_calls, "full", EQUALITY_QUERY),
        _search(smoke_calls, "full", WITHHELD_QUERY),
        _search(smoke_calls, "full", "token rotation"),
    ]

    identities = [
        {field: response["retrieval"][field] for field in BUILD_IDENTITY_FIELDS}
        for response in responses
    ]

    assert all(identity == identities[0] for identity in identities)
    assert all(identities[0][field] for field in BUILD_IDENTITY_FIELDS), (
        "and neither field is empty"
    )


# -- B: forbiddenPresentCause reads the coverage classification --------------


def test_forbidden_present_cause_is_set_only_for_a_census_tested_trap() -> None:
    """ADR-0036, the gate-vs-census derivation rule (report.py's ``_forbidden_zero_cause``).

    A zero ``forbiddenPresent`` for a census-tested trap (``rejected``, never
    indexed under either flavor) is guaranteed by construction and annotated
    as such. For a gate-tested trap (``draft``, indexed in ``full`` since
    6ef2b606) the same zero is earned by the query-time gate, not guaranteed,
    so no cause is stated -- an unannotated zero here would misreport a real
    result as a foregone one.
    """
    with tempfile.TemporaryDirectory(prefix="theurian-eval-annotation-") as out_name:
        code = harness_run.main(["--corpus", str(SMOKE_CORPUS), "--out", out_name])
        assert code == 0
        report = json.loads((Path(out_name) / "report.json").read_text())

    census_tested = report["queries"]["rejected-note-forbidden-trap"]["corpora"]["full"]
    gate_tested = report["queries"]["gateway-policy-cross-build"]["corpora"]["full"]

    assert "forbiddenPresentCause" in census_tested
    assert "forbiddenPresentCause" not in gate_tested


# -- C: no artifact string value leaks corpus content -------------------------

#: Below this, near-everything is a substring of near-everything -- short
#: status/sensitivity/class labels (`"rejected"`, `"internal"`) coincide with
#: ordinary English words the corpus's own prose uses honestly (the rejected
#: note's body literally says "was rejected"). A real leak -- an excerpt, an
#: itemId, the withheld secret key -- is always well above this.
_ARTIFACT_LEAK_MIN_LENGTH = 16


def _every_string_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _every_string_value(item)]
    if isinstance(value, list):
        return [s for item in value for s in _every_string_value(item)]
    return []


def _corpus_texts() -> list[str]:
    """Every knowledge body, migration ``metadata.title``, and corpus identifier
    (``itemId``, ``revisionId``, ``sourceUri``) in the smoke corpus.

    The identifiers matter as much as the prose: a published field naming
    *which row* reached it -- an itemId, a revision, a source anchor -- is
    the "which rows reached a field" disclosure axis, not merely an excerpt
    of body text. Containment of a corpus identifier is checked the same way
    as containment of a body excerpt: the artifact value must not appear
    inside anything in this population.
    """
    texts = [
        path.read_text(encoding="utf-8")
        for path in sorted((SMOKE_CORPUS / "knowledge").rglob("*.md"))
    ]
    for migration_path in sorted((SMOKE_CORPUS / "migrations").glob("*.yaml")):
        document = yaml.safe_load(migration_path.read_text(encoding="utf-8"))
        for op in document.get("operations", []):
            item_id = op.get("itemId")
            if item_id:
                texts.append(item_id)
            revision_id = op.get("revisionId")
            if revision_id:
                texts.append(revision_id)
            metadata = op.get("metadata")
            if not metadata:
                continue
            if "title" in metadata:
                texts.append(metadata["title"])
            for anchor in metadata.get("sourceAnchors", []):
                if "sourceUri" in anchor:
                    texts.append(anchor["sourceUri"])
    return texts


def test_no_artifact_string_value_is_contained_in_any_corpus_body_or_migration_title() -> None:
    """ADR-0036 decision 6: the harness produces metrics, never content.

    ``report.json`` and ``timings.json`` publish counts, classes and cause
    strings -- never a knowledge body's prose, a migration's title, or a
    corpus identifier (itemId, revisionId, sourceUri). Positive probe: the
    withheld runbook's own synthetic secret key is genuinely present in the
    corpus (proving the scan can find a real match) and must be absent from
    both artifacts. This reddens the moment an excerpt- or itemId-bearing
    field is added to ``report._query_metrics``.
    """
    texts = _corpus_texts()
    assert any(WITHHELD_QUERY in text for text in texts), (
        "the positive probe needs its target genuinely present in the corpus"
    )

    with tempfile.TemporaryDirectory(prefix="theurian-eval-artifact-") as out_name:
        code = harness_run.main(["--corpus", str(SMOKE_CORPUS), "--out", out_name])
        assert code == 0
        report = json.loads((Path(out_name) / "report.json").read_text())
        timings = json.loads((Path(out_name) / "timings.json").read_text())

    values = _every_string_value(report) + _every_string_value(timings)
    leaked = [
        value
        for value in values
        if len(value) >= _ARTIFACT_LEAK_MIN_LENGTH and any(value in text for text in texts)
    ]
    assert leaked == []


# -- D: determinism (decisions 5 and 7) ---------------------------------------


def test_two_consecutive_harness_runs_over_the_smoke_corpus_produce_a_byte_identical_report() -> (
    None
):
    """ADR-0036 decisions 5 and 7 (Compliance).

    Measured at the scope decision 5 states, and at no wider one: one machine,
    one interpreter, one SQLite build, consecutive runs. Cross-install
    identity of BM25-derived orderings is unmeasured and not claimed here.

    The sibling assertion is decision 7's split held as a property rather than
    a filing convention: `timings.json` carries the wall clock and the commit
    sha, so it differs between the two runs while `report.json` -- sorted
    keys, fixed rounding, no commit sha embedded -- does not.
    """
    with (
        tempfile.TemporaryDirectory(prefix="theurian-eval-det-a-") as out_a,
        tempfile.TemporaryDirectory(prefix="theurian-eval-det-b-") as out_b,
    ):
        code_a = harness_run.main(["--corpus", str(SMOKE_CORPUS), "--out", out_a])
        code_b = harness_run.main(["--corpus", str(SMOKE_CORPUS), "--out", out_b])

        assert (code_a, code_b) == (0, 0)
        report_a = (Path(out_a) / "report.json").read_bytes()
        report_b = (Path(out_b) / "report.json").read_bytes()
        timings_a = (Path(out_a) / "timings.json").read_bytes()
        timings_b = (Path(out_b) / "timings.json").read_bytes()

        assert report_a == report_b
        assert timings_a != timings_b, "decision 7's split is a property, not a filing convention"


# -- E: the one loader refusal that needs a real build ------------------------

_CENSUS_MIGRATION = "1N311FSDACRHQJQV010HQ93Y3Q-sample.yaml"


def _write_census_mismatch_corpus(root: Path) -> None:
    """A one-item corpus whose declared census cannot match a real build."""
    manifest = {
        "contractVersion": 1,
        "corpusId": "census-mismatch-pin-v1",
        "kValues": [1, 5],
        "migrations": [{"file": _CENSUS_MIGRATION, "plane": "visible"}],
        "census": {
            "full": {
                "items": 99,
                "byStatus": {"approved": 99},
                "bySensitivity": {"public": 99},
                "chunks": 99,
            },
            "clean": {
                "items": 99,
                "byStatus": {"approved": 99},
                "bySensitivity": {"public": 99},
                "chunks": 99,
            },
        },
    }
    queries = {
        "queries": [
            {"id": "sample-query", "class": "exact-decision", "query": "What is the sample item?"}
        ]
    }
    judgements = {
        "judgements": [{"queryId": "sample-query", "relevant": [{"itemId": "domain.sample-item"}]}]
    }
    migration = {
        "apiVersion": "theurian.dev/v1",
        "id": "1N311FSDACRHQJQV010HQ93Y3Q",
        "createdAt": "2026-09-20T09:00:00+09:00",
        "author": "eval-harness@theurian.dev",
        "operations": [
            {
                "op": "createItem",
                "itemId": "domain.sample-item",
                "kind": "domain",
                "namespace": "pin-corpus",
                "owner": "eval-harness",
                "sensitivity": "public",
            },
            {
                "op": "upsertRevision",
                "itemId": "domain.sample-item",
                "revisionId": "1C2T1H7RR1GG39BJXXW5WV5YFD",
                "contentFile": "../knowledge/sample-item.md",
                "contentSha256": "5d96312bf935ef89154d85c5da2d45ff3897717abf47e595ad4dd4414bf2faa7",
                "metadata": {
                    "title": "Sample Item",
                    "contentType": "text/markdown",
                    "kind": "domain",
                    "namespace": "pin-corpus",
                    "status": "approved",
                    "owner": "eval-harness",
                    "trustLevel": "reviewed",
                    "sensitivity": "public",
                    "sourceAnchors": [
                        {"provider": "git", "sourceUri": "git://census-mismatch-pin/sample-item.md"}
                    ],
                },
            },
        ],
    }

    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (root / "queries.yaml").write_text(yaml.safe_dump(queries, sort_keys=False), encoding="utf-8")
    (root / "judgements.yaml").write_text(
        yaml.safe_dump(judgements, sort_keys=False), encoding="utf-8"
    )
    migrations_dir = root / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / _CENSUS_MIGRATION).write_text(
        yaml.safe_dump(migration, sort_keys=False), encoding="utf-8"
    )
    knowledge_dir = root / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "sample-item.md").write_text(
        "# Sample Item\n\nA single sample item for the census-mismatch pin.\n", encoding="utf-8"
    )


def test_census_mismatch_rule_refuses_a_manifest_whose_census_disagrees_with_a_real_build(
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    _write_census_mismatch_corpus(corpus_root)
    loaded = harness_corpus.load_corpus(corpus_root)

    with (
        tempfile.TemporaryDirectory(prefix="theurian-eval-census-") as workspace_name,
        pytest.raises(harness_corpus.CorpusError) as excinfo,
    ):
        harness_build.build_both(loaded, Path(workspace_name))

    assert excinfo.value.rule == "census-mismatch"


# -- F: #787's abstention-cause flag-probe and the equality channel summary --


@pytest.fixture(scope="module")
def smoke_run() -> tuple[dict[str, Any], dict[str, Any]]:
    """One ``harness_run.main`` build over the smoke corpus, both artifacts --
    shared by every read-only assertion in this section that needs
    ``report.json``, ``timings.json``, or both. These check what one run
    says, not whether two runs agree (the byte-identity pin above is that
    check).
    """
    with tempfile.TemporaryDirectory(prefix="theurian-eval-smoke-report-") as out_name:
        code = harness_run.main(["--corpus", str(SMOKE_CORPUS), "--out", out_name])
        assert code == 0
        report: dict[str, Any] = json.loads((Path(out_name) / "report.json").read_text())
        timings: dict[str, Any] = json.loads((Path(out_name) / "timings.json").read_text())
        return report, timings


@pytest.fixture(scope="module")
def smoke_report(smoke_run: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    return smoke_run[0]


def test_abstention_cause_marks_the_gate_earned_sample_and_leaves_its_clean_counterpart_bare(
    smoke_report: dict[str, Any],
) -> None:
    """#787's flag-probe, read over ``withheld-incident-key``'s own two planes.

    ``full``'s default-flags call returns nothing while the flagged probe
    reaches the draft row (the reach control
    ``test_the_default_flag_gate_hides_the_draft_row_the_include_unapproved_flag_reveals``
    proves that same reach) -- gate-earned, so ``abstentionCause`` states it,
    pinned by exact equality against the module's own constant. ``clean``
    abstains for the unrelated reason that it never held the row at all
    under either flag, but the probe is scoped to ``full`` alone
    (``_abstention_probe_response``), so ``clean``'s entry -- also correctly
    abstaining -- must carry no cause.
    """
    corpora = smoke_report["queries"]["withheld-incident-key"]["corpora"]

    assert corpora["full"]["abstentionCorrect"] is True
    assert corpora["full"]["abstentionCause"] == harness_report._ABSTENTION_GATE_WITHHELD
    assert corpora["clean"]["abstentionCorrect"] is True
    assert "abstentionCause" not in corpora["clean"]


def test_abstention_cause_never_appears_on_a_query_that_does_not_expect_abstention(
    smoke_report: dict[str, Any],
) -> None:
    """A judgement that never expects abstention leaves ``abstentionCorrect``
    at ``None``, and ``_abstention_cause``'s guard reads ``None`` as much
    "not True" as ``False`` -- checked over two different query classes
    (exact-decision, superseded), not just one.
    """
    for query_id in ("token-rotation-policy", "cache-invalidation-current"):
        entry = smoke_report["queries"][query_id]["corpora"]["full"]
        assert entry["abstentionCorrect"] is None
        assert "abstentionCause" not in entry


def test_an_abstention_query_that_returns_a_hit_of_its_own_is_not_mislabeled_gate_earned(
    smoke_report: dict[str, Any],
) -> None:
    """``mainframe-disaster-recovery`` shares no vocabulary with anything in the
    smoke corpus (measured: none of "mainframe", "disaster" or "recovery"
    appears anywhere under ``knowledge/``), yet the smoke corpus's default
    plane holds only three approved items and ranking still returns all
    three at default flags -- ``abstentionCorrect`` is measured ``False``
    here, a genuinely wrong abstention rather than an absence-earned or
    gate-earned one. ``_abstention_cause`` reads ``correct`` before it ever
    reads the probe, so this wrong outcome must not read as gate-earned even
    though the flagged probe (run for every ``expectAbstention`` judgement)
    does add the withheld runbook to this same query's hit set.
    """
    entry = smoke_report["queries"]["mainframe-disaster-recovery"]["corpora"]["full"]

    assert entry["abstentionCorrect"] is False
    assert "abstentionCause" not in entry


#: A literal copy of ``report._CHANNEL_REASON``'s value, not a reference to
#: the constant itself. Comparing the module's published ``reason`` against
#: the SAME constant that produced it is structurally unfailable: an
#: adversarial mutation replacing ``_CHANNEL_REASON``'s wording with a
#: "single-user" paraphrase (specifically wrong -- the daemon serves many
#: agents, #119) survived 78/78 against the constant-referencing form, since
#: both sides of the comparison read the mutated value. This literal is the
#: canonical test-side site for that rationale: the literal is the pin's own
#: authority, and drifting ``_CHANNEL_REASON`` now diverges from the copy
#: below and reds, whatever the constant says.
_CHANNEL_REASON_LITERAL = (
    "recorded channel, T-17a family; not a disclosure finding because "
    "includeUnapproved is a request parameter (not a grant) and the Core is "
    "one-principal (#119); reachable only under the operator's "
    "--include-unapproved build, absent from the shipped default."
)


def test_the_equality_channel_summary_carries_the_787_reason_verbatim_and_the_measured_counts(
    smoke_report: dict[str, Any],
) -> None:
    """ADR-0036 Amendment 1 rider 1, #787's channel summary.

    ``reason`` is pinned against ``_CHANNEL_REASON_LITERAL`` above, not
    merely "some string is present". ``queriesDiffering``/``of`` are the
    smoke corpus's own measured counts (3 equality queries -- excluding
    non-equality ``mainframe-disaster-recovery`` and the two single-corpus
    superseded/forbidden-trap queries -- none differing beyond build
    identity at either limit), not assumed from the frozen S3 corpus's
    unrelated 18/26 and 21/26.
    """
    assert smoke_report["equality"]["channel"] == {
        "reason": _CHANNEL_REASON_LITERAL,
        "atLimit": {"queriesDiffering": 0, "of": 3},
        "atEqualityLimit": {"queriesDiffering": 0, "of": 3},
    }


def test_the_channel_exempt_field_set_matches_this_files_own_build_identity_constant() -> None:
    """report.py's own comment on ``_BUILD_IDENTITY_EXEMPT`` promises this test
    (adversarial finding M1, constant half).

    Two independent authorities name the same two fields: ``BUILD_IDENTITY``
    above (what the equality set-comparison
    ``test_the_equality_query_differs_from_its_clean_counterpart_only_in_build_identity``
    enforces a response may differ on) and ``_BUILD_IDENTITY_EXEMPT`` (what
    ``_channel_summary`` excludes when counting a query as differing). Widening
    either -- adding, say, ``count``, ``results`` or ``usedTokens`` to the
    exempt set -- would silently stop counting a real content difference as a
    channel occurrence while this file's own equality pin kept enforcing the
    narrower set, with nothing to notice the two had drifted apart.
    """
    assert harness_report._BUILD_IDENTITY_EXEMPT == BUILD_IDENTITY


def test_the_abstention_flag_probe_adds_no_extra_query_entry_to_the_report(
    smoke_report: dict[str, Any],
) -> None:
    """The probe (#787) is a second wire call feeding an existing entry's
    ``abstentionCause``, never a query of its own: it must not inflate
    ``queries`` or the ``equality`` section's population.
    """
    loaded = harness_corpus.load_corpus(SMOKE_CORPUS)
    enabled_ids = {query.id for query in loaded.queries if query.enabled}
    equality_ids = {
        query.id for query in loaded.queries if query.enabled and len(set(query.corpora)) == 2
    }

    assert set(smoke_report["queries"]) == enabled_ids
    assert set(smoke_report["equality"]["queries"]) == equality_ids


def test_the_equality_querys_two_planes_each_carry_their_own_probes_cause_and_timings_row(
    smoke_run: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """The per-limit fix (e49c6520): the ``atLimit`` and ``atEqualityLimit``
    planes of an equality abstention query each derive ``abstentionCause``
    from their OWN-limit probe, never one plane's probe standing in for the
    other via an unstated count-monotonicity between limit 10 and limit 50
    (code/security/adversarial MEDIUM, "single-limit probe stands in for the
    atEqualityLimit plane"). ``abstentionProbe`` records both flag-on limits
    the corpus actually issued a probe at, and ``timings.json`` carries a row
    for each -- ``{10, true}`` feeding the base plane, ``{50, true}`` feeding
    ``atEqualityLimit``.
    """
    report, timings = smoke_run

    full = report["queries"]["withheld-incident-key"]["corpora"]["full"]
    assert full["abstentionCause"] == harness_report._ABSTENTION_GATE_WITHHELD
    assert full["atEqualityLimit"]["abstentionCause"] == harness_report._ABSTENTION_GATE_WITHHELD

    assert report["abstentionProbe"] == {"includeUnapproved": True, "limits": [10, 50]}

    probe_rows = {
        (row["limit"], row["includeUnapproved"])
        for row in timings["queries"]
        if row["queryId"] == "withheld-incident-key" and row["includeUnapproved"]
    }
    assert probe_rows == {(10, True), (50, True)}


# -- G: the probe never escapes a clean-only query's declared corpora --------

_CLEAN_ONLY_ABSTENTION_MIGRATION = "1N311FSDACRHQJQV010HQ93Y3Q-clean-only.yaml"


def _write_clean_only_abstention_corpus(root: Path) -> None:
    """A one-item corpus whose sole query declares ``corpora: [clean]`` alone.

    Census measured against a real ``corpus_build.build_both`` run of this
    exact content (one visible, approved, public item): both planes hold
    ``items=1, chunks=1``.
    """
    manifest = {
        "contractVersion": 1,
        "corpusId": "clean-only-abstention-pin-v1",
        "kValues": [1, 5],
        "migrations": [{"file": _CLEAN_ONLY_ABSTENTION_MIGRATION, "plane": "visible"}],
        "census": {
            "full": {
                "items": 1,
                "byStatus": {"approved": 1},
                "bySensitivity": {"public": 1},
                "chunks": 1,
            },
            "clean": {
                "items": 1,
                "byStatus": {"approved": 1},
                "bySensitivity": {"public": 1},
                "chunks": 1,
            },
        },
    }
    queries = {
        "queries": [
            {
                "id": "clean-only-abstention",
                "class": "unknown",
                "query": "a phrase sharing no vocabulary with the corpus at all",
                "corpora": ["clean"],
            }
        ]
    }
    judgements = {"judgements": [{"queryId": "clean-only-abstention", "expectAbstention": True}]}
    migration = {
        "apiVersion": "theurian.dev/v1",
        "id": "1N311FSDACRHQJQV010HQ93Y3Q",
        "createdAt": "2026-09-20T09:00:00+09:00",
        "author": "eval-harness@theurian.dev",
        "operations": [
            {
                "op": "createItem",
                "itemId": "domain.sample-item",
                "kind": "domain",
                "namespace": "pin-corpus",
                "owner": "eval-harness",
                "sensitivity": "public",
            },
            {
                "op": "upsertRevision",
                "itemId": "domain.sample-item",
                "revisionId": "1C2T1H7RR1GG39BJXXW5WV5YFD",
                "contentFile": "../knowledge/sample-item.md",
                "contentSha256": "517dc231187d99708415ca6b43b2d097fb996e20f9d0a77113cedb856275aa4c",
                "metadata": {
                    "title": "Sample Item",
                    "contentType": "text/markdown",
                    "kind": "domain",
                    "namespace": "pin-corpus",
                    "status": "approved",
                    "owner": "eval-harness",
                    "trustLevel": "reviewed",
                    "sensitivity": "public",
                    "sourceAnchors": [
                        {"provider": "git", "sourceUri": "git://clean-only-pin/sample-item.md"}
                    ],
                },
            },
        ],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (root / "queries.yaml").write_text(yaml.safe_dump(queries, sort_keys=False), encoding="utf-8")
    (root / "judgements.yaml").write_text(
        yaml.safe_dump(judgements, sort_keys=False), encoding="utf-8"
    )
    migrations_dir = root / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / _CLEAN_ONLY_ABSTENTION_MIGRATION).write_text(
        yaml.safe_dump(migration, sort_keys=False), encoding="utf-8"
    )
    knowledge_dir = root / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "sample-item.md").write_text(
        "# Sample Item\n\nA single sample item for the clean-only abstention pin.\n",
        encoding="utf-8",
    )


def test_a_clean_only_abstention_querys_probe_never_escapes_into_a_full_or_flagged_timings_row(
    tmp_path: Path,
) -> None:
    """The adversarial's phantom-row reproduction (code/security/adversarial
    MEDIUM), committed: ``probe_limits_for`` gates on ``"full"`` being in
    ``query.corpora`` -- a clean-only abstention query must get no probe wire
    call and no ``full`` entry at all, never a phantom ``full``/flag-on row
    the query itself never declared.

    The corpus's own single item happens to rank for this query regardless
    of shared vocabulary (measured; the same one-item-corpus effect
    ``test_an_abstention_query_that_returns_a_hit_of_its_own_is_not_mislabeled_gate_earned``
    records for ``mainframe-disaster-recovery``) -- irrelevant to what this
    pin checks, which is containment, not whether the query's own abstention
    judgement holds.
    """
    corpus_root = tmp_path / "corpus"
    _write_clean_only_abstention_corpus(corpus_root)

    with tempfile.TemporaryDirectory(prefix="theurian-eval-clean-only-") as out_name:
        code = harness_run.main(["--corpus", str(corpus_root), "--out", out_name])
        assert code == 0
        report: dict[str, Any] = json.loads((Path(out_name) / "report.json").read_text())
        timings: dict[str, Any] = json.loads((Path(out_name) / "timings.json").read_text())

    assert set(report["queries"]["clean-only-abstention"]["corpora"]) == {"clean"}
    assert "abstentionProbe" not in report

    assert len(timings["queries"]) == 1
    row = timings["queries"][0]
    assert (row["queryId"], row["corpus"], row["limit"], row["includeUnapproved"]) == (
        "clean-only-abstention",
        "clean",
        5,
        False,
    )
