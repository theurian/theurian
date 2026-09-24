"""Committed pins for the Phase A retrieval-evaluation harness (ADR-0036, "Still owed").

Pins the structural claim decision 2 makes -- "no module under
``packages/theurian-core/src/`` reads, imports, or names the corpus, the
queries or the judgements" -- and the S2 loader's fourteen named refusals
(the eleventh, ``withheld-item-disclosable``, landed in 07e7e099; the
twelfth and thirteenth, ``relevant-item-unretrievable`` and
``migration-order-not-topological``, landed in 636c15ca, which also
schema-validates every migration document -- ``_base_migrations()`` below
matches a real fixture's shape for exactly that reason; the fourteenth,
``relevant-item-unknown``, landed in 6aeab078, splitting rule 12's
existence clause off its enabled-scoped retrievability clauses). Each
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

import subprocess
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
import metrics as harness_metrics  # noqa: E402
import report as harness_report  # noqa: E402

CORE_SRC = REPO_ROOT / "packages" / "theurian-core" / "src"

BASE_MIGRATION_ONE = "1M23BW8DJNKT2GJB31BMEYQP08-first.yaml"
BASE_MIGRATION_TWO = "2R8B5ZVNYVVS83VJW4JTPDGDA2-second.yaml"
WITHHELD_MIGRATION = "3Y8WCK8TKQY13VGPN206KD1Q6G-third.yaml"
EXTRA_VISIBLE_MIGRATION = "47KBH2J7YP7AAWBQX9F8NKJQTE-fourth.yaml"

#: A `contentSha256` no test here ever builds against -- `load_corpus` never
#: reads `contentFile` off disk, only a real `corpus_build` run would, and
#: none of these tests run one. Named after
#: `packages/theurian-core/tests/migration_fixtures.UNREACHED_BODY_PIN`,
#: which records the same reasoning for the product's own fixtures.
UNREACHED_BODY_PIN = "deadbeef" * 8


# -- A: the structural pin for decision 2 -------------------------------------


def _references(src_root: Path, needles: list[str]) -> list[tuple[Path, int, str]]:
    """Every line under ``src_root`` containing one of ``needles`` as a substring."""
    hits: list[tuple[Path, int, str]] = []
    for path in sorted(src_root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(needle in line for needle in needles):
                hits.append((path, lineno, line))
    return hits


def _needle_forms(root: Path) -> set[str]:
    """The repo-relative path, its last two segments, and the dotted spelling.

    A reference need not be spelled as the full repo-relative path to name
    the same directory: ``# from fixtures/eval-smoke import x`` and
    ``# from fixtures.eval-smoke import x`` both name
    ``tests/fixtures/eval-smoke`` as surely as the full path does. For
    ``tools/eval``, which is already two segments, the "last two" form equals
    the full path, so the set below has two distinct members rather than
    three in that case -- a set, not a fixed-length tuple, on purpose.
    """
    relative = str(root.relative_to(REPO_ROOT))
    short = "/".join(root.parts[-2:])
    dotted = short.replace("/", ".")
    return {relative, short, dotted}


def _fixture_and_harness_needles() -> list[str]:
    """Population derived from where a corpus manifest actually sits, not hardcoded.

    ADR-0036's "Still owed" item 1: hardcoding the two path prefixes the
    decision-2 key spells (``tools/eval``, ``fixtures/eval``) would leave this
    pin green and blind if the corpus or the harness relocated. Both are
    derived instead: the fixture roots from a *recursive* glob over every
    committed corpus manifest -- so a corpus nested under a subdirectory of
    ``tests/fixtures/`` cannot sit outside the scan -- the harness root from
    the already-imported loader module's own file. Each root expands to
    :func:`_needle_forms`'s three spellings, not the one full path alone.
    """
    fixture_roots = [
        manifest.parent
        for manifest in sorted((REPO_ROOT / "tests" / "fixtures").rglob("manifest.yaml"))
    ]
    harness_root = Path(harness_corpus.__file__).resolve().parent
    needles: set[str] = set()
    for root in (*fixture_roots, harness_root):
        needles |= _needle_forms(root)
    return sorted(needles)


def test_no_module_under_core_src_references_the_harness_or_its_fixtures() -> None:
    """ADR-0036 decision 2, "Still owed" item 1.

    The retrieval path never reads the judgements: this is the enforced form
    of the decision-2 key (``git grep -n -E "tools/eval|fixtures/eval" --
    packages/theurian-core/src/``), recomputed against the tree rather than
    read once and trusted. Wider than that key's own reach: every needle is
    checked in all three of :func:`_needle_forms`' spellings (full
    repo-relative path, last-two-segments short form, dotted form), not the
    full path alone -- a reference spelled ``fixtures/eval-smoke`` or
    ``fixtures.eval-smoke`` names the fixture corpus exactly as surely as
    ``tests/fixtures/eval-smoke`` does, and the original grep key would not
    have caught either.
    """
    needles = _fixture_and_harness_needles()
    assert needles, "the population must be non-empty, or this pin checks nothing"

    hits = _references(CORE_SRC, needles)

    assert hits == []


def test_the_reference_scan_reports_a_planted_full_path_reference(tmp_path: Path) -> None:
    """Guards the guard: an empty answer from a scan that finds nothing states nothing."""
    needles = _fixture_and_harness_needles()
    full_path_needle = next(n for n in needles if n.count("/") >= 2)
    planted = tmp_path / "planted_full.py"
    planted.write_text(f"# a stray import of {full_path_needle}\n", encoding="utf-8")

    hits = _references(tmp_path, needles)

    assert len(hits) == 1
    assert hits[0][0] == planted


def test_the_reference_scan_also_reports_a_planted_short_form_reference(tmp_path: Path) -> None:
    """The needle set catches the short spelling too, not only the full
    repo-relative path -- the face the original decision-2 grep key missed.
    """
    needles = _fixture_and_harness_needles()
    short_form_needle = next(n for n in needles if n.startswith("fixtures/"))
    planted = tmp_path / "planted_short.py"
    planted.write_text(f"# from {short_form_needle} import x\n", encoding="utf-8")

    hits = _references(tmp_path, needles)

    assert len(hits) == 1
    assert hits[0][0] == planted


# -- B: the loader's fourteen named refusals ------------------------------------


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


def _minimal_migration(migration_id: str, *operations: dict[str, Any]) -> dict[str, Any]:
    """A schema-valid migration document, matching a real fixture's shape.

    ``636c15ca`` schema-validates every migration document at load
    (``schemas/migrations/migration.schema.json``), so a stub carrying only
    ``{"id": ...}`` now fails before any loader-rule pin's own check runs.
    """
    return {
        "apiVersion": "theurian.dev/v1",
        "id": migration_id,
        "createdAt": "2026-09-20T09:00:00+09:00",
        "author": "eval-harness@theurian.dev",
        "operations": list(operations),
    }


def _create_and_approve(item_id: str, revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """``createItem`` + ``upsertRevision`` making ``item_id`` approved, internal --
    retrievable at the harness's own default flags (rule 12).
    """
    return (
        {
            "op": "createItem",
            "itemId": item_id,
            "kind": "domain",
            "namespace": "pin-corpus",
            "owner": "eval-harness",
        },
        {
            "op": "upsertRevision",
            "itemId": item_id,
            "revisionId": revision_id,
            "contentFile": f"../knowledge/{item_id}.md",
            "contentSha256": UNREACHED_BODY_PIN,
            "metadata": {
                "title": item_id,
                "contentType": "text/markdown",
                "kind": "domain",
                "namespace": "pin-corpus",
                "status": "approved",
                "owner": "eval-harness",
            },
        },
    )


def _base_migrations() -> dict[str, dict[str, Any]]:
    """``domain.item-one`` is approved so ``_valid_judgements()``'s own relevant
    item is genuinely retrievable (rule 12) -- otherwise every test whose
    corpus reaches the full pipeline without violating an earlier rule would
    trip over this one instead. ``domain.item-two`` is a bare ``createItem``:
    no judgement ever names it.
    """
    return {
        BASE_MIGRATION_ONE: _minimal_migration(
            "1M23BW8DJNKT2GJB31BMEYQP08",
            *_create_and_approve("domain.item-one", "17GA408085G2HAWYGGT9D39TH0"),
        ),
        BASE_MIGRATION_TWO: _minimal_migration(
            "2R8B5ZVNYVVS83VJW4JTPDGDA2",
            {
                "op": "createItem",
                "itemId": "domain.item-two",
                "kind": "domain",
                "namespace": "pin-corpus",
                "owner": "eval-harness",
            },
        ),
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
    migrations[WITHHELD_MIGRATION] = _minimal_migration(
        "3Y8WCK8TKQY13VGPN206KD1Q6G",
        {
            "op": "createItem",
            "itemId": "domain.withheld-item",
            "kind": "domain",
            "namespace": "pin-corpus",
            "owner": "eval-harness",
        },
    )
    migrations[BASE_MIGRATION_TWO]["dependsOn"] = ["3Y8WCK8TKQY13VGPN206KD1Q6G"]
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
    migrations[WITHHELD_MIGRATION] = _minimal_migration(
        "3Y8WCK8TKQY13VGPN206KD1Q6G",
        {
            "op": "createItem",
            "itemId": "domain.withheld-item",
            "kind": "domain",
            "namespace": "pin-corpus",
            "owner": "eval-harness",
        },
        {
            "op": "upsertRevision",
            "itemId": "domain.withheld-item",
            "revisionId": "31XVNYGT5PCMXQM9VB8B748Y15",
            "contentFile": "../knowledge/domain.withheld-item.md",
            "contentSha256": UNREACHED_BODY_PIN,
            "metadata": {
                "title": "Withheld Item",
                "contentType": "text/markdown",
                "kind": "domain",
                "namespace": "pin-corpus",
                "status": status,
                "owner": "eval-harness",
                "sensitivity": sensitivity,
            },
        },
    )
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


def test_relevant_item_unretrievable_rule_refuses_a_withheld_plane_relevant_item(
    tmp_path: Path,
) -> None:
    """ADR-0036's gate-vs-census derivation rule, the twelfth named refusal (636c15ca).

    A relevant item withheld by plane can never come back from a
    default-flags query -- the harness never queries with
    ``includeUnapproved=true`` -- which would pin recall and MRR to zero for
    a reason that has nothing to do with ranking quality (the class that
    fixed ``withheld-incident-key``'s own recall/MRR at 0 in the smoke corpus
    before this round).
    """
    manifest, migrations = _manifest_and_migrations_with_withheld_item("draft", "internal")
    judgements = {
        "judgements": [
            {"queryId": "sample-query", "relevant": [{"itemId": "domain.withheld-item"}]}
        ]
    }
    _write_corpus(tmp_path, manifest, _valid_queries(), judgements, migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "relevant-item-unretrievable"


def _manifest_and_migrations_with_extra_visible_item(
    status: str, sensitivity: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _valid_manifest()
    manifest["migrations"].append({"file": EXTRA_VISIBLE_MIGRATION, "plane": "visible"})
    migrations = _base_migrations()
    migrations[EXTRA_VISIBLE_MIGRATION] = _minimal_migration(
        "47KBH2J7YP7AAWBQX9F8NKJQTE",
        {
            "op": "createItem",
            "itemId": "domain.extra-item",
            "kind": "domain",
            "namespace": "pin-corpus",
            "owner": "eval-harness",
        },
        {
            "op": "upsertRevision",
            "itemId": "domain.extra-item",
            "revisionId": "4MV43PZ2DFM6QQB07MT0E7WJ21",
            "contentFile": "../knowledge/domain.extra-item.md",
            "contentSha256": UNREACHED_BODY_PIN,
            "metadata": {
                "title": "Extra Item",
                "contentType": "text/markdown",
                "kind": "domain",
                "namespace": "pin-corpus",
                "status": status,
                "owner": "eval-harness",
                "sensitivity": sensitivity,
            },
        },
    )
    return manifest, migrations


@pytest.mark.parametrize(
    ("status", "sensitivity"),
    [("draft", "internal"), ("approved", "confidential")],
    ids=["visible-but-not-approved", "visible-but-above-ceiling"],
)
def test_relevant_item_unretrievable_rule_refuses_a_visible_item_that_cannot_surface(
    tmp_path: Path, status: str, sensitivity: str
) -> None:
    """The other two ways a relevant item can be unretrievable: not withheld by
    plane at all, but still never surfaceable at default flags -- a draft
    status, or a sensitivity above the build ceiling.
    """
    manifest, migrations = _manifest_and_migrations_with_extra_visible_item(status, sensitivity)
    judgements = {
        "judgements": [{"queryId": "sample-query", "relevant": [{"itemId": "domain.extra-item"}]}]
    }
    _write_corpus(tmp_path, manifest, _valid_queries(), judgements, migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "relevant-item-unretrievable"


_HISTORICAL_QUERY_ID = "historical-ttl-evolution"


def _corpus_with_a_query_judging_a_superseded_item(
    *, query_class: str, enabled: bool
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """A visible, superseded-status item judged relevant by one added query.

    Shared by the rule-12 pins below: the exemption and the enabled control
    vary only ``query_class``/``enabled``; the existence-clause pin further
    below reuses this and then overwrites the judged itemId.
    """
    manifest, migrations = _manifest_and_migrations_with_extra_visible_item(
        "superseded", "internal"
    )
    queries = _valid_queries()
    queries["queries"].append(
        {
            "id": _HISTORICAL_QUERY_ID,
            "class": query_class,
            "query": "How did the session-token TTL evolve?",
            "enabled": enabled,
        }
    )
    judgements = _valid_judgements()
    judgements["judgements"].append(
        {"queryId": _HISTORICAL_QUERY_ID, "relevant": [{"itemId": "domain.extra-item"}]}
    )
    return manifest, queries, judgements, migrations


def test_relevant_item_unretrievable_rule_exempts_a_disabled_querys_judgement(
    tmp_path: Path,
) -> None:
    """ADR-0036, "What the rule asks of a corpus editor" (PR #793); the joint-build
    refusal this closes is narrated in full at
    ``_check_relevant_items_retrievable``'s own docstring.
    """
    manifest, queries, judgements, migrations = _corpus_with_a_query_judging_a_superseded_item(
        query_class="historical", enabled=False
    )
    _write_corpus(tmp_path, manifest, queries, judgements, migrations)

    loaded = harness_corpus.load_corpus(tmp_path)

    judgement = loaded.judgement_for(_HISTORICAL_QUERY_ID)
    assert judgement is not None
    assert judgement.relevant == (harness_corpus.JudgedItem(item_id="domain.extra-item"),)


def test_relevant_item_unretrievable_rule_still_fires_once_the_same_query_is_enabled(
    tmp_path: Path,
) -> None:
    """The exemption above is scoped to ``enabled: false``, not a blanket skip.

    The same shape that loads while the query is disabled must still refuse
    once the query is enabled -- otherwise ADR-0036's "What the rule asks of a
    corpus editor" (PR #793) exemption in ``_check_relevant_items_retrievable``
    would have silently widened into no check at all rather than a scoped one.
    ``class: historical`` forces ``enabled: false`` in ``queries.schema.json``,
    so the enabled control changes the class to ``unknown``, which carries no
    such constraint; the judged item and its superseded status are otherwise
    identical.
    """
    manifest, queries, judgements, migrations = _corpus_with_a_query_judging_a_superseded_item(
        query_class="unknown", enabled=True
    )
    _write_corpus(tmp_path, manifest, queries, judgements, migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "relevant-item-unretrievable"
    assert "superseded" in str(excinfo.value)


def test_relevant_item_unknown_rule_refuses_a_disabled_querys_judgement_naming_no_item(
    tmp_path: Path,
) -> None:
    """The existence clause 6aeab078 split off rule 12, narrated in full at
    ``_check_relevant_items_retrievable``'s own docstring: it runs over every
    judgement regardless of ``query.enabled``, so a typo'd itemId in a
    disabled query's judgement is refused now rather than loading silently
    and reading as forced zero recall once the query's phase enables it.
    """
    manifest, queries, judgements, migrations = _corpus_with_a_query_judging_a_superseded_item(
        query_class="historical", enabled=False
    )
    judgements["judgements"][-1]["relevant"][0]["itemId"] = "domain.no-such-item"
    _write_corpus(tmp_path, manifest, queries, judgements, migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "relevant-item-unknown"


def test_migration_order_not_topological_rule_refuses_a_forward_referencing_dependson(
    tmp_path: Path,
) -> None:
    """ADR-0036's gate-vs-census derivation rule, the thirteenth named refusal (636c15ca).

    The replay machinery (``_final_status_and_sensitivity``) iterates
    migrations in manifest order, not a dependency-aware topological order --
    a ``dependsOn`` pointing at a migration that appears *later* in that
    order would make the replay compute a status the real ``migrate apply``
    never produces.
    """
    manifest = _valid_manifest()
    migrations = _base_migrations()
    migrations[BASE_MIGRATION_ONE]["dependsOn"] = [migrations[BASE_MIGRATION_TWO]["id"]]
    _write_corpus(tmp_path, manifest, _valid_queries(), _valid_judgements(), migrations)

    with pytest.raises(harness_corpus.CorpusError) as excinfo:
        harness_corpus.load_corpus(tmp_path)

    assert excinfo.value.rule == "migration-order-not-topological"


def test_the_committed_s3_corpus_loads_through_the_loader() -> None:
    """Converts the joint build's hand verification into CI: a loader rule that
    refuses the committed S3 corpus reddens HERE, never again first at the
    joint build.
    """
    loaded = harness_corpus.load_corpus(REPO_ROOT / "tests" / "fixtures" / "eval")

    assert len(loaded.queries) == 27
    assert len(loaded.judgements) == 27
    assert {query.id for query in loaded.queries if not query.enabled} == {"q-hist-ttl-evolution"}


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
    ceiling (``internal``, since ``corpus_build.py`` writes no serving
    profile). A retired status or an above-ceiling sensitivity alone is
    enough to make an item census-tested -- excluded from every index
    regardless of the flag.
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


# -- D: metrics.py pure-function pins -----------------------------------------
#
# Every function in tools/eval/metrics.py had zero direct tests before this
# round -- only exercised indirectly through a real build's report.json.
# Each pin below drives exactly one branch.


def _judgement(
    relevant: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
    evidence: tuple[harness_corpus.EvidenceRef, ...] = (),
    expect_abstention: bool = False,
) -> harness_corpus.JudgementEntry:
    return harness_corpus.JudgementEntry(
        query_id="q",
        relevant=tuple(harness_corpus.JudgedItem(item_id=i) for i in relevant),
        evidence=evidence,
        forbidden=tuple(harness_corpus.JudgedItem(item_id=i) for i in forbidden),
        expect_abstention=expect_abstention,
    )


def test_differing_paths_reports_a_missing_key_at_the_shallowest_path() -> None:
    left = {"a": {"b": 1, "c": 2}}
    right = {"a": {"b": 1}}

    assert harness_metrics.differing_paths(left, right) == frozenset({"a.c"})


def test_differing_paths_reports_a_list_length_mismatch_at_the_list_itself() -> None:
    """A length mismatch is reported at the list's own path, not recursed into --
    ``zip(..., strict=True)`` would raise on mismatched lengths, so the length
    check has to fire first.
    """
    left = {"items": [1, 2, 3]}
    right = {"items": [1, 2]}

    assert harness_metrics.differing_paths(left, right) == frozenset({"items"})


def test_differing_paths_recurses_into_a_nested_differing_leaf() -> None:
    left = {"a": {"b": {"c": 1}}}
    right = {"a": {"b": {"c": 2}}}

    assert harness_metrics.differing_paths(left, right) == frozenset({"a.b.c"})


def test_differing_paths_returns_the_empty_set_for_equal_structures() -> None:
    left = {"a": [1, {"b": 2}], "c": "x"}
    right = {"a": [1, {"b": 2}], "c": "x"}

    assert harness_metrics.differing_paths(left, right) == frozenset()


def test_recall_at_k_deduplicates_repeated_item_ids_in_the_denominator() -> None:
    """Two relevant entries agreeing on ``itemId`` share one denominator slot,
    not two -- ``uniqueItems`` on ``judgements.yaml``'s own schema would refuse
    two byte-identical entries, but nothing below that schema layer stops two
    :class:`~corpus.JudgedItem` objects agreeing only on ``item_id``, and
    ``recall_at_k``'s set comprehension is what actually holds the count to one.
    """
    judgement = harness_corpus.JudgementEntry(
        query_id="q",
        relevant=(
            harness_corpus.JudgedItem(item_id="a"),
            harness_corpus.JudgedItem(item_id="a"),
        ),
        evidence=(),
        forbidden=(),
        expect_abstention=False,
    )
    response = {"results": [{"itemId": "a"}]}

    assert harness_metrics.recall_at_k(response, judgement, k=10) == 1.0


def test_recall_at_k_is_none_on_an_empty_relevant_denominator() -> None:
    judgement = _judgement()
    response: dict[str, Any] = {"results": []}

    assert harness_metrics.recall_at_k(response, judgement, k=10) is None


def test_mrr_is_the_reciprocal_rank_of_the_first_relevant_hit() -> None:
    judgement = _judgement(relevant=("b",))
    response = {"results": [{"itemId": "a"}, {"itemId": "b"}, {"itemId": "c"}]}

    assert harness_metrics.mrr(response, judgement) == 0.5


def test_mrr_is_zero_when_no_relevant_item_is_present() -> None:
    judgement = _judgement(relevant=("z",))
    response = {"results": [{"itemId": "a"}]}

    assert harness_metrics.mrr(response, judgement) == 0.0


def test_mrr_is_none_on_an_empty_relevant_denominator() -> None:
    judgement = _judgement()
    response: dict[str, Any] = {"results": []}

    assert harness_metrics.mrr(response, judgement) is None


def test_evidence_precision_scores_the_share_of_anchors_that_match() -> None:
    judgement = _judgement(evidence=(harness_corpus.EvidenceRef(source_uri="u1", file_path=None),))
    response = {"results": [{"sourceAnchors": [{"sourceUri": "u1"}, {"sourceUri": "u2"}]}]}

    assert harness_metrics.evidence_precision(response, judgement) == 0.5


def test_evidence_precision_is_none_when_the_judgement_carries_no_evidence() -> None:
    judgement = _judgement()
    response = {"results": [{"sourceAnchors": [{"sourceUri": "u1"}]}]}

    assert harness_metrics.evidence_precision(response, judgement) is None


def test_evidence_precision_is_none_not_zero_on_an_empty_anchor_denominator() -> None:
    """636c15ca: undefined over an empty denominator, matching ``recall_at_k``'s
    own convention -- a response with zero anchors says nothing about
    precision, and ``0.0`` would read as "every anchor is wrong" when there
    were none to be wrong at all.
    """
    judgement = _judgement(evidence=(harness_corpus.EvidenceRef(source_uri="u1", file_path=None),))
    response: dict[str, Any] = {"results": [{"sourceAnchors": []}]}

    assert harness_metrics.evidence_precision(response, judgement) is None


def test_abstention_correct_true_when_the_response_is_genuinely_empty() -> None:
    judgement = _judgement(expect_abstention=True)
    response = {"count": 0, "results": []}

    assert harness_metrics.abstention_correct(response, judgement) is True


def test_abstention_correct_false_when_the_response_is_not_empty() -> None:
    judgement = _judgement(expect_abstention=True)
    response = {"count": 1, "results": [{"itemId": "a"}]}

    assert harness_metrics.abstention_correct(response, judgement) is False


def test_abstention_correct_is_none_when_the_judgement_does_not_expect_abstention() -> None:
    judgement = _judgement(expect_abstention=False)
    response = {"count": 0, "results": []}

    assert harness_metrics.abstention_correct(response, judgement) is None


def test_forbidden_present_true_when_a_forbidden_item_appears() -> None:
    judgement = _judgement(forbidden=("x",))
    response = {"results": [{"itemId": "x"}]}

    assert harness_metrics.forbidden_present(response, judgement) is True


def test_forbidden_present_false_when_no_forbidden_item_appears() -> None:
    judgement = _judgement(forbidden=("x",))
    response = {"results": [{"itemId": "y"}]}

    assert harness_metrics.forbidden_present(response, judgement) is False


def test_forbidden_present_is_none_when_the_judgement_forbids_nothing() -> None:
    judgement = _judgement(forbidden=())
    response: dict[str, Any] = {"results": []}

    assert harness_metrics.forbidden_present(response, judgement) is None


# -- E: report.py pure-function and error-handling pins -----------------------


def test_report_round_rounds_floats_to_six_decimals_and_recurses_through_dicts_and_lists() -> None:
    value = {"a": 1.0 / 3, "b": [2.0 / 3, {"c": 1.0}], "d": "text", "e": True, "f": 5}

    rounded = harness_report._round(value)

    assert rounded == {
        "a": round(1.0 / 3, 6),
        "b": [round(2.0 / 3, 6), {"c": round(1.0, 6)}],
        "d": "text",
        "e": True,
        "f": 5,
    }


def _empty_loaded_corpus() -> harness_corpus.Corpus:
    manifest = harness_corpus.Manifest(
        contract_version=1,
        corpus_id="timings-pin",
        k_values=(1,),
        migrations=(),
        census={},
        description=None,
    )
    return harness_corpus.Corpus(
        root=Path(), manifest=manifest, queries=(), judgements=(), withheld_coverage=()
    )


def test_build_timings_degrades_commit_sha_to_unknown_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``report._commit_sha`` (636c15ca): a copied tree with no ``.git``, or a
    mutation sweep's throwaway checkout, is a real environment this harness
    runs in, not a defect to crash on.
    """

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    timings = harness_report.build_timings(
        loaded=_empty_loaded_corpus(), runs=(), build_costs={}, repo_root=Path("/nonexistent")
    )

    assert timings["commitSha"] == "unknown"


# -- E2: report._abstention_cause / _channel_summary, pure (#787) -------------


def test_abstention_cause_is_gate_withheld_when_the_flagged_probe_reaches_a_hit() -> None:
    assert (
        harness_report._abstention_cause(True, {"count": 1, "results": [{"itemId": "x"}]})
        == harness_report._ABSTENTION_GATE_WITHHELD
    )


def test_abstention_cause_is_none_when_the_flagged_probe_also_returns_nothing() -> None:
    """The absence-earned half of #787's cause: silence under either flag, never
    a guess. The smoke corpus's own two abstention samples cannot drive this
    branch -- one is gate-earned (the probe finds the row) and the other
    fails its own abstention judgement before the probe is even read (see
    ``test_harness_pins.py``'s integration file) -- so this is pinned
    directly on the pure function instead.
    """
    assert harness_report._abstention_cause(True, {"count": 0, "results": []}) is None


def test_abstention_cause_is_none_when_the_outcome_was_not_itself_correct() -> None:
    """The guard reads ``correct`` before it ever reads ``probe`` -- a wrong
    abstention outcome must not read as gate-earned merely because the
    flagged call happens to return something.
    """
    assert (
        harness_report._abstention_cause(False, {"count": 5, "results": [{"itemId": "x"}]}) is None
    )


def test_abstention_cause_is_none_when_no_probe_was_run() -> None:
    assert harness_report._abstention_cause(True, None) is None


#: A literal copy of ``report._CHANNEL_REASON``'s value, not a reference to
#: the constant itself -- comparing the module's output to the same constant
#: that produced it is structurally unfailable (adversarial finding: the
#: single-user mutation below survived 78/78 against the constant-referencing
#: form). This literal is the pin's own authority: drifting ``_CHANNEL_REASON``
#: now diverges from the copy below and reds. See
#: ``test_the_equality_channel_summary_carries_the_787_reason_verbatim_and_the_measured_counts``
#: (tests/integration/tools/test_harness_pins.py) for why the exact wording
#: matters, not merely "some string is present".
_CHANNEL_REASON_LITERAL = (
    "recorded channel, T-17a family; not a disclosure finding because "
    "includeUnapproved is a request parameter (not a grant) and the Core is "
    "one-principal (#119); reachable only under the operator's "
    "--include-unapproved build, absent from the shipped default."
)


def test_channel_summary_counts_only_queries_differing_beyond_build_identity() -> None:
    """``_channel_summary``'s own contract (#787): a query whose only difference
    is which artifact answered (the two ``_BUILD_IDENTITY_EXEMPT`` fields)
    does not count; anything beyond it does. Exact dict equality pins the
    shape, the counts and the reason together.
    """
    section = {
        "identity-only": {
            "atLimit": {"limit": 10, "differingFields": ["retrieval.indexBuildId"]},
            "atEqualityLimit": {"limit": 50, "differingFields": []},
        },
        "beyond-identity": {
            "atLimit": {
                "limit": 10,
                "differingFields": ["retrieval.snapshotId", "results[0].itemId"],
            },
            "atEqualityLimit": {"limit": 50, "differingFields": ["results[3].itemId"]},
        },
    }

    assert harness_report._channel_summary(section) == {
        "reason": _CHANNEL_REASON_LITERAL,
        "atLimit": {"queriesDiffering": 1, "of": 2},
        "atEqualityLimit": {"queriesDiffering": 1, "of": 2},
    }


# -- F: the withheld incident key is genuinely derived, not merely documented -


def test_the_withheld_incident_key_is_genuinely_derived_as_documented() -> None:
    """manifest.yaml's header comment states the derivation recipe as prose;
    this recomputes it independently and pins the literal it must equal, so
    the recipe cannot drift from the committed bytes without reddening here.
    """
    import hashlib

    derived = (
        hashlib.sha256(b"theurian eval-smoke withheld incident key v1").hexdigest()[:20].upper()
    )

    smoke_corpus = REPO_ROOT / "tests" / "fixtures" / "eval-smoke"
    body = (smoke_corpus / "knowledge" / "security" / "incident-runbook.md").read_text(
        encoding="utf-8"
    )
    queries_text = (smoke_corpus / "queries.yaml").read_text(encoding="utf-8")

    assert derived in body
    assert derived in queries_text
