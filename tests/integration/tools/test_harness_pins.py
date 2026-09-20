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

import build as harness_build  # noqa: E402
import corpus as harness_corpus  # noqa: E402
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
#: in both planes -- picked to share no vocabulary with the withheld runbook,
#: so the comparison below is not accidentally reading the other channel.
EQUALITY_QUERY = "authenticates inbound requests"

#: The withheld item's synthetic incident key (see manifest.yaml's header
#: comment for its derivation) -- present in `full` only.
WITHHELD_QUERY = "0710645F7E85DCE09F4B"


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


def _search(smoke_calls: dict[str, Any], corpus_name: str, query: str) -> dict[str, Any]:
    project = smoke_calls["projects"][corpus_name]
    call: ToolCall = smoke_calls["calls"][corpus_name]
    return call(
        "knowledge.search",
        {"projectId": project.project_id, "query": query, "limit": 10, "maxTokens": 32_000},
    )


# -- A: the disclosure-equality set comparison, and its two controls ---------


def test_the_equality_query_differs_from_its_clean_counterpart_only_in_build_identity(
    smoke_calls: dict[str, Any],
) -> None:
    """ADR-0036 decision 6, "Still owed" item 2. Set equality, not a subset.

    A subset check would also pass a harness that had stopped publishing
    `retrieval.indexBuildId`, or whose `retrieval.snapshotId` had gone
    insensitive to canonical state -- both are contract changes that should be
    decided, not silently absorbed by a comparison that quietly widens.
    """
    full = _search(smoke_calls, "full", EQUALITY_QUERY)
    clean = _search(smoke_calls, "clean", EQUALITY_QUERY)

    assert full["count"] > 0, "a comparison of two empty answers proves nothing"
    moved = harness_metrics.differing_paths(full, clean)

    assert moved == BUILD_IDENTITY


def test_the_fixture_can_exhibit_a_wider_difference_when_content_genuinely_differs(
    smoke_calls: dict[str, Any],
) -> None:
    """Guards the guard: the reach control ADR-0036 "Still owed" item 2 asks for.

    Without this, the equality above could hold because `full` and `clean`
    happen to be identical, not because nothing withheld leaks through.
    Mirrors `test_the_depth_probe_reaches_the_withheld_document_inside_the_
    candidate_depth`'s discipline in `test_mcp_tools.py`: the corpus must be
    able to show a difference, or the equality above states nothing.
    """
    full = _search(smoke_calls, "full", WITHHELD_QUERY)
    clean = _search(smoke_calls, "clean", WITHHELD_QUERY)

    assert full["count"] > 0, "the withheld item must actually be a match in the full build"
    assert clean["count"] == 0, "and genuinely absent from the clean build, or nothing is withheld"
    assert any(hit["itemId"] == "security.incident-runbook" for hit in full["results"])

    moved = harness_metrics.differing_paths(full, clean)
    assert moved > BUILD_IDENTITY, "a real content difference must move more than the exempt pair"


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


# -- B: determinism (decisions 5 and 7) ---------------------------------------


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


# -- C: the one loader refusal that needs a real build ------------------------

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
