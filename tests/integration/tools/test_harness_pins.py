"""Committed integration pins for the Phase A harness (ADR-0036, "Still owed").

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

#: `token-rotation-policy`'s own text. Measured (this session, against the
#: committed smoke corpus): at default flags this returns three VISIBLE
#: results in `full` -- security.token-rotation, architecture.gateway-policy,
#: architecture.retry-policy, in that order, identical in `clean` -- and with
#: `includeUnapproved=true` against `full` the draft runbook joins as a
#: fourth, ranked candidate (security.token-rotation, architecture.gateway-
#: policy, security.incident-runbook, architecture.retry-policy). So this
#: query is adversarial-measured candidacy, not asserted: the runbook
#: genuinely competes for it (shares "rotation"/"ledger"/"immediately"
#: vocabulary with its own body), and the set-equality property is measured
#: over >=2 real visible hits, not one.
COMPETING_VOCABULARY_QUERY = "What is the token rotation policy?"


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
    """ADR-0036 decision 6, "Still owed" item 2, held under 6ef2b606's fix.

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


def test_both_build_identity_fields_are_constant_and_nonempty_within_one_build(
    smoke_calls: dict[str, Any],
) -> None:
    """The set-equality pin's companion (ADR-0036, "Still owed" item 2).

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
    """Every knowledge body and migration ``metadata.title`` in the smoke corpus."""
    texts = [
        path.read_text(encoding="utf-8")
        for path in sorted((SMOKE_CORPUS / "knowledge").rglob("*.md"))
    ]
    for migration_path in sorted((SMOKE_CORPUS / "migrations").glob("*.yaml")):
        document = yaml.safe_load(migration_path.read_text(encoding="utf-8"))
        for op in document.get("operations", []):
            metadata = op.get("metadata")
            if metadata and "title" in metadata:
                texts.append(metadata["title"])
    return texts


def test_no_artifact_string_value_is_contained_in_any_corpus_body_or_migration_title() -> None:
    """ADR-0036 decision 6: the harness produces metrics, never content.

    ``report.json`` and ``timings.json`` publish counts, classes, ids and
    cause strings -- nothing that should ever contain a knowledge body's
    prose or a migration's title. Positive probe: the withheld runbook's own
    synthetic secret key is genuinely present in the corpus (proving the
    scan can find a real match) and must be absent from both artifacts. This
    reddens the moment an excerpt- or itemId-bearing field is added to
    ``report._query_metrics`` and that field's value happens to quote corpus
    text back.
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
    """ADR-0036 decisions 5 and 7, "Still owed" item 3.

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
