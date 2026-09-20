"""Committed pins for the Phase A retrieval-evaluation harness (ADR-0036, "Still owed").

Pins the structural claim decision 2 makes -- "no module under
``packages/theurian-core/src/`` reads, imports, or names the corpus, the
queries or the judgements" -- and the S2 loader's eleven named refusals
(the eleventh, ``withheld-item-disclosable``, landed in 07e7e099). Each
loader-rule pin builds a minimal corpus that violates exactly one rule and
asserts :class:`CorpusError.rule` names it, so a rule silently dropped from
``load_corpus`` reddens one specific test rather than a vague "something
changed" failure.

``tools/eval`` is a flat script directory, not a package: this file puts it on
``sys.path`` itself, the way ``tests/ci/test_red_team_sweep_prose.py`` puts
``tools/`` on its own path, since a fixture-corpus module set that collided by
name with something under ``tools/audit`` would only surface when both
directories' conftests happened to run in the same session.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = REPO_ROOT / "tools" / "eval"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import corpus as harness_corpus  # noqa: E402

CORE_SRC = REPO_ROOT / "packages" / "theurian-core" / "src"

BASE_MIGRATION_ONE = "1M23BW8DJNKT2GJB31BMEYQP08-first.yaml"
BASE_MIGRATION_TWO = "2R8B5ZVNYVVS83VJW4JTPDGDA2-second.yaml"
WITHHELD_MIGRATION = "3Y8WCK8TKQY13VGPN206KD1Q6G-third.yaml"


# -- A: the structural pin for decision 2 -------------------------------------


def _references(src_root: Path, needles: list[str]) -> list[tuple[Path, int, str]]:
    """Every line under ``src_root`` containing one of ``needles`` as a substring."""
    hits: list[tuple[Path, int, str]] = []
    for path in sorted(src_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(needle in line for needle in needles):
                hits.append((path, lineno, line))
    return hits


def _fixture_and_harness_needles() -> list[str]:
    """Population derived from where a corpus manifest actually sits, not hardcoded.

    ADR-0036's "Still owed" item 1: hardcoding the two path prefixes the
    decision-2 key spells (``tools/eval``, ``fixtures/eval``) would leave this
    pin green and blind if the corpus or the harness relocated. Both are
    derived instead: the fixture roots from a glob over every committed
    corpus manifest, the harness root from the already-imported loader
    module's own file.
    """
    fixture_roots = sorted(REPO_ROOT.glob("tests/fixtures/*/manifest.yaml"))
    needles = [str(manifest.parent.relative_to(REPO_ROOT)) for manifest in fixture_roots]
    harness_root = Path(harness_corpus.__file__).resolve().parent
    needles.append(str(harness_root.relative_to(REPO_ROOT)))
    return needles


def test_no_module_under_core_src_references_the_harness_or_its_fixtures() -> None:
    """ADR-0036 decision 2, "Still owed" item 1.

    The retrieval path never reads the judgements: this is the enforced form
    of the decision-2 key (``git grep -n -E "tools/eval|fixtures/eval" --
    packages/theurian-core/src/``), recomputed against the tree rather than
    read once and trusted.
    """
    needles = _fixture_and_harness_needles()
    assert needles, "the population must be non-empty, or this pin checks nothing"

    hits = _references(CORE_SRC, needles)

    assert hits == []


def test_the_reference_scan_reports_a_planted_reference(tmp_path: Path) -> None:
    """Guards the guard: an empty answer from a scan that finds nothing states nothing."""
    needles = _fixture_and_harness_needles()
    planted = tmp_path / "planted.py"
    planted.write_text(f"# a stray import of {needles[0]}\n", encoding="utf-8")

    hits = _references(tmp_path, needles)

    assert len(hits) == 1
    assert hits[0][0] == planted


# -- B: the loader's eleven named refusals -------------------------------------


def _valid_manifest() -> dict[str, Any]:
    return {
        "contractVersion": 1,
        "corpusId": "pin-corpus-v1",
        "kValues": [1, 5],
        "migrations": [
            {"file": BASE_MIGRATION_ONE, "plane": "visible"},
            {"file": BASE_MIGRATION_TWO, "plane": "visible"},
        ],
        "census": {
            "full": {
                "items": 2,
                "byStatus": {"approved": 2},
                "bySensitivity": {"public": 2},
                "chunks": 2,
            },
            "clean": {
                "items": 2,
                "byStatus": {"approved": 2},
                "bySensitivity": {"public": 2},
                "chunks": 2,
            },
        },
    }


def _valid_queries() -> dict[str, Any]:
    return {
        "queries": [
            {"id": "sample-query", "class": "exact-decision", "query": "What is the sample policy?"}
        ]
    }


def _valid_judgements() -> dict[str, Any]:
    return {
        "judgements": [{"queryId": "sample-query", "relevant": [{"itemId": "domain.item-one"}]}]
    }


def _base_migrations() -> dict[str, dict[str, Any]]:
    return {
        BASE_MIGRATION_ONE: {"id": "1M23BW8DJNKT2GJB31BMEYQP08"},
        BASE_MIGRATION_TWO: {"id": "2R8B5ZVNYVVS83VJW4JTPDGDA2"},
    }


def _write_corpus(
    root: Path,
    manifest: dict[str, Any],
    queries: dict[str, Any],
    judgements: dict[str, Any],
    migrations: dict[str, dict[str, Any]],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (root / "queries.yaml").write_text(yaml.safe_dump(queries, sort_keys=False), encoding="utf-8")
    (root / "judgements.yaml").write_text(
        yaml.safe_dump(judgements, sort_keys=False), encoding="utf-8"
    )
    migrations_dir = root / "migrations"
    migrations_dir.mkdir(exist_ok=True)
    for filename, content in migrations.items():
        (migrations_dir / filename).write_text(
            yaml.safe_dump(content, sort_keys=False), encoding="utf-8"
        )


def test_migration_order_rule_refuses_migrations_out_of_filename_order(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    manifest["migrations"] = list(reversed(manifest["migrations"]))
    _write_corpus(tmp_path, manifest, _valid_queries(), _valid_judgements(), _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "migration-order"


def test_visible_depends_on_withheld_rule_refuses_a_visible_migration_depending_on_a_withheld_one(
    tmp_path: Path,
) -> None:
    manifest = _valid_manifest()
    manifest["migrations"].append({"file": WITHHELD_MIGRATION, "plane": "withheld"})
    manifest["migrations"].sort(key=lambda entry: entry["file"])
    migrations = _base_migrations()
    migrations[WITHHELD_MIGRATION] = {"id": "3Y8WCK8TKQY13VGPN206KD1Q6G"}
    migrations[BASE_MIGRATION_TWO] = {
        "id": "2R8B5ZVNYVVS83VJW4JTPDGDA2",
        "dependsOn": ["3Y8WCK8TKQY13VGPN206KD1Q6G"],
    }
    _write_corpus(tmp_path, manifest, _valid_queries(), _valid_judgements(), migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "visible-depends-on-withheld"


def test_duplicate_query_id_rule_refuses_two_queries_sharing_an_id(tmp_path: Path) -> None:
    queries = _valid_queries()
    queries["queries"].append(
        {"id": "sample-query", "class": "unknown", "query": "A second, duplicate-id query."}
    )
    _write_corpus(tmp_path, _valid_manifest(), queries, _valid_judgements(), _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "duplicate-query-id"


def test_duplicate_judgement_query_id_rule_refuses_two_judgements_sharing_a_query_id(
    tmp_path: Path,
) -> None:
    judgements = _valid_judgements()
    judgements["judgements"].append(
        {"queryId": "sample-query", "forbidden": [{"itemId": "domain.other-item"}]}
    )
    _write_corpus(tmp_path, _valid_manifest(), _valid_queries(), judgements, _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "duplicate-judgement-query-id"


def test_judgement_unknown_query_rule_refuses_a_judgement_naming_an_undeclared_query(
    tmp_path: Path,
) -> None:
    judgements = {
        "judgements": [{"queryId": "no-such-query", "relevant": [{"itemId": "domain.item-one"}]}]
    }
    _write_corpus(tmp_path, _valid_manifest(), _valid_queries(), judgements, _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "judgement-unknown-query"


def test_query_missing_judgement_rule_refuses_an_enabled_query_with_no_judgement(
    tmp_path: Path,
) -> None:
    queries = _valid_queries()
    queries["queries"].append(
        {"id": "unjudged-query", "class": "unknown", "query": "A query nobody judged."}
    )
    _write_corpus(tmp_path, _valid_manifest(), queries, _valid_judgements(), _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "query-missing-judgement"


def test_relevant_forbidden_overlap_rule_refuses_the_same_item_id_in_both_lists(
    tmp_path: Path,
) -> None:
    judgements = {
        "judgements": [
            {
                "queryId": "sample-query",
                "relevant": [{"itemId": "domain.item-one"}],
                "forbidden": [{"itemId": "domain.item-one"}],
            }
        ]
    }
    _write_corpus(tmp_path, _valid_manifest(), _valid_queries(), judgements, _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "relevant-forbidden-overlap"


def test_empty_judgement_rule_refuses_a_judgement_that_judges_nothing(tmp_path: Path) -> None:
    judgements = {"judgements": [{"queryId": "sample-query"}]}
    _write_corpus(tmp_path, _valid_manifest(), _valid_queries(), judgements, _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "empty-judgement"


def test_evidence_subsumption_rule_refuses_a_plain_and_narrowed_entry_for_one_source(
    tmp_path: Path,
) -> None:
    judgements = {
        "judgements": [
            {
                "queryId": "sample-query",
                "evidence": [
                    {"sourceUri": "https://example.com/doc"},
                    {"sourceUri": "https://example.com/doc", "filePath": "README.md"},
                ],
            }
        ]
    }
    _write_corpus(tmp_path, _valid_manifest(), _valid_queries(), judgements, _base_migrations())

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "evidence-subsumption"


def _manifest_and_migrations_with_withheld_item(
    status: str, sensitivity: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _valid_manifest()
    manifest["migrations"].append({"file": WITHHELD_MIGRATION, "plane": "withheld"})
    migrations = _base_migrations()
    migrations[WITHHELD_MIGRATION] = {
        "id": "3Y8WCK8TKQY13VGPN206KD1Q6G",
        "operations": [
            {"op": "createItem", "itemId": "domain.withheld-item"},
            {
                "op": "upsertRevision",
                "itemId": "domain.withheld-item",
                "metadata": {"status": status, "sensitivity": sensitivity},
            },
        ],
    }
    return manifest, migrations


def test_withheld_item_disclosable_rule_refuses_an_approved_within_ceiling_withheld_item(
    tmp_path: Path,
) -> None:
    """ADR-0036's gate-vs-census derivation rule, the eleventh named refusal (07e7e099).

    An approved, within-ceiling withheld-plane item is excluded by no
    mechanism -- indexed and surfaced at default flags exactly like any other
    approved item -- so neither coverage label would be honest for it, per
    the tests lane's teeth run (a1f54e82) that prompted this refusal.
    """
    manifest, migrations = _manifest_and_migrations_with_withheld_item("approved", "internal")
    _write_corpus(tmp_path, manifest, _valid_queries(), _valid_judgements(), migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "withheld-item-disclosable"


@pytest.mark.parametrize(
    ("status", "sensitivity", "expected_gate_tested"),
    [("draft", "internal", True), ("approved", "confidential", False)],
    ids=["accepted-gate-tested-neighbour", "accepted-census-tested-neighbour"],
)
def test_withheld_item_disclosable_rules_two_accepted_neighbours_load(
    tmp_path: Path, status: str, sensitivity: str, expected_gate_tested: bool
) -> None:
    """The refusal above partitions cleanly: move either clause and the corpus loads.

    Draft-and-within-ceiling is gate-tested; approved-and-above-ceiling is
    census-tested by the sensitivity clause alone. Only approved-and-within-
    ceiling -- disclosable by no mechanism -- is refused.
    """
    manifest, migrations = _manifest_and_migrations_with_withheld_item(status, sensitivity)
    _write_corpus(tmp_path, manifest, _valid_queries(), _valid_judgements(), migrations)

    loaded = harness_corpus.load_corpus(tmp_path)

    (coverage,) = loaded.withheld_coverage
    assert coverage.is_gate_tested is expected_gate_tested


# -- C: the gate-vs-census coverage derivation (6ef2b606) ---------------------


def test_gate_tested_statuses_equals_draft_and_proposed_today() -> None:
    """ADR-0036's gate-vs-census derivation rule: the derived value, pinned (f5f677a5).

    GATE_TESTED_STATUSES is DERIVED from theurian.domain.enums.may_surface
    folded over every KnowledgeStatus member, not a hand-copied literal -- so
    this pin cannot drift out of sync with the product by construction: it
    would still read whatever may_surface says. It exists so that a future
    move in the product's surfaceability semantics reddens here, forcing a
    recorded eval re-baseline decision instead of letting the eval silently
    carry a semantics change into its published numbers. Not a tautology: it
    pins the derived VALUE against today's recorded expectation, not the
    derivation mechanism against itself.
    """
    assert {"draft", "proposed"} == harness_corpus.GATE_TESTED_STATUSES


def _coverage_manifest(*files: str) -> harness_corpus.Manifest:
    return harness_corpus.Manifest(
        contract_version=1,
        corpus_id="coverage-pin",
        k_values=(1,),
        migrations=tuple(harness_corpus.MigrationEntry(file=f, plane="withheld") for f in files),
        census={},
        description=None,
    )


def _created_and_revised(item_id: str, status: str, sensitivity: str) -> dict[str, Any]:
    return {
        "operations": [
            {"op": "createItem", "itemId": item_id},
            {
                "op": "upsertRevision",
                "itemId": item_id,
                "metadata": {"status": status, "sensitivity": sensitivity},
            },
        ]
    }


@pytest.mark.parametrize(
    ("status", "sensitivity", "expected_gate_tested"),
    [
        ("draft", "internal", True),
        ("rejected", "internal", False),
        ("draft", "confidential", False),
    ],
    ids=["gate-tested", "census-tested-retired-status", "census-tested-above-ceiling"],
)
def test_withheld_item_coverage_classifies_by_final_status_and_sensitivity(
    status: str, sensitivity: str, expected_gate_tested: bool
) -> None:
    """ADR-0036, the gate-vs-census derivation rule (6ef2b606).

    Gate-tested needs both axes: a status ``--include-unapproved`` admits to
    the index (``draft``/``proposed``) AND a sensitivity within the build
    ceiling (``internal``, since ``build.py`` writes no serving profile). A
    retired status or an above-ceiling sensitivity alone is enough to make an
    item census-tested -- excluded from every index regardless of the flag.
    """
    manifest = _coverage_manifest("one.yaml")
    documents = {"one.yaml": _created_and_revised("security.item", status, sensitivity)}

    (coverage,) = harness_corpus._withheld_item_coverage(manifest, documents)

    assert (coverage.final_status, coverage.final_sensitivity) == (status, sensitivity)
    assert coverage.is_gate_tested is expected_gate_tested


def test_withheld_item_coverage_uses_the_final_status_not_the_first() -> None:
    """A status set by an early migration and overridden by a later one classifies
    on the override -- the item's coverage is what it ends up as, not what it
    started as.
    """
    manifest = _coverage_manifest("one.yaml", "two.yaml")
    documents = {
        "one.yaml": _created_and_revised("security.item", "draft", "internal"),
        "two.yaml": {"operations": [{"op": "deprecateItem", "itemId": "security.item"}]},
    }

    (coverage,) = harness_corpus._withheld_item_coverage(manifest, documents)

    assert coverage.final_status == "deprecated"
    assert coverage.is_gate_tested is False
