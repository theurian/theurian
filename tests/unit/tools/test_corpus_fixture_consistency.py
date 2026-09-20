"""The frozen fixture corpus under ``tests/fixtures/eval/`` holds its own rules.

Nothing else checks it. The three contract schemas state the shape of one file
each and cannot reach across two (ADR-0036 decision 6), the S2 loader that would
is not written, and ``schemas/migrations/migration.schema.json`` is applied by
``theurian migrate`` against a project's own migrations, never against a fixture
directory. So until S2 lands, an edited body whose ``contentSha256`` was not
re-pinned, a judgement naming an item no migration creates, or a withheld item
promoted into a ``relevant`` list would each be committed green.

Every rule below is a function returning the violations it found, and every one
is asserted twice: once over the corpus as committed, and once over a deep copy
carrying exactly the defect the rule exists to catch. A rule asserted only over
a corpus that satisfies it is a rule that would also pass if it checked nothing.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_ROOT = REPO_ROOT / "tests" / "fixtures" / "eval"
MIGRATIONS_DIR = CORPUS_ROOT / "migrations"
EVAL_SCHEMAS = REPO_ROOT / "tools" / "eval" / "schemas"
MIGRATION_SCHEMA = REPO_ROOT / "schemas" / "migrations" / "migration.schema.json"

#: Item counts, statuses and sensitivities follow from the migration operations;
#: ``chunks`` does not, and is measured by a real build instead.
DERIVABLE_CENSUS_KEYS = ("items", "byStatus", "bySensitivity")

#: Every key through which an operation names a knowledge item.
ITEM_REFERENCES = ("itemId", "sourceItemId", "targetItemId", "supersededBy", "alias", "specId")


@dataclass(frozen=True)
class Corpus:
    manifest: dict[str, Any]
    queries: dict[str, Any]
    judgements: dict[str, Any]
    #: ``(filename, document)`` in filename order.
    migrations: tuple[tuple[str, dict[str, Any]], ...]
    #: Body bytes keyed by the ``contentFile`` path a migration names.
    bodies: dict[str, bytes]


def _yaml(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def _schema(path: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _load() -> Corpus:
    migrations = tuple((path.name, _yaml(path)) for path in sorted(MIGRATIONS_DIR.glob("*.yaml")))
    bodies = {
        operation["contentFile"]: (MIGRATIONS_DIR / operation["contentFile"]).read_bytes()
        for _name, document in migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
    }
    return Corpus(
        manifest=_yaml(CORPUS_ROOT / "manifest.yaml"),
        queries=_yaml(CORPUS_ROOT / "queries.yaml"),
        judgements=_yaml(CORPUS_ROOT / "judgements.yaml"),
        migrations=migrations,
        bodies=bodies,
    )


CORPUS = _load()

MANIFEST_VALIDATOR = _schema(EVAL_SCHEMAS / "manifest.schema.json")
QUERIES_VALIDATOR = _schema(EVAL_SCHEMAS / "queries.schema.json")
JUDGEMENTS_VALIDATOR = _schema(EVAL_SCHEMAS / "judgements.schema.json")
MIGRATION_VALIDATOR = _schema(MIGRATION_SCHEMA)


# -- derivation helpers -------------------------------------------------------


def _ids(entry: dict[str, Any], field: str) -> set[str]:
    return {item["itemId"] for item in entry.get(field, [])}


def _planes(corpus: Corpus) -> dict[str, str]:
    return {entry["file"]: entry["plane"] for entry in corpus.manifest["migrations"]}


def _creating_plane(corpus: Corpus) -> dict[str, str]:
    """Each created itemId, mapped to the plane of the migration that created it."""
    planes = _planes(corpus)
    return {
        operation["itemId"]: planes.get(name, "withheld")
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "createItem"
    }


def _tally(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _derive_census(corpus: Corpus, wanted: set[str]) -> dict[str, Any]:
    """Replay the wanted planes' migrations and count the items they leave behind.

    Status and sensitivity are read the way the engine reads them: a revision's
    metadata adopts both onto the item (``KnowledgeItem.with_revision``), while
    ``deprecateItem`` and ``changeSensitivity`` move one of them afterwards.
    """
    planes = _planes(corpus)
    state: dict[str, dict[str, str]] = {}
    for name, document in corpus.migrations:
        if planes.get(name) not in wanted:
            continue
        for operation in document["operations"]:
            item = operation.get("itemId")
            match operation["op"]:
                case "createItem":
                    state[item] = {"status": "", "sensitivity": operation["sensitivity"]}
                case "upsertRevision":
                    state[item] = {
                        "status": operation["metadata"]["status"],
                        "sensitivity": operation["metadata"]["sensitivity"],
                    }
                case "deprecateItem" if item in state:
                    state[item]["status"] = "deprecated"
                case "changeSensitivity" if item in state:
                    state[item]["sensitivity"] = operation["sensitivity"]
                case _:
                    pass
    return {
        "items": len(state),
        "byStatus": _tally([item["status"] for item in state.values()]),
        "bySensitivity": _tally([item["sensitivity"] for item in state.values()]),
    }


# -- the rules ----------------------------------------------------------------


def _schema_violations(corpus: Corpus) -> list[str]:
    found = [
        f"{name}: {error.json_path}"
        for name, document, validator in (
            ("manifest.yaml", corpus.manifest, MANIFEST_VALIDATOR),
            ("queries.yaml", corpus.queries, QUERIES_VALIDATOR),
            ("judgements.yaml", corpus.judgements, JUDGEMENTS_VALIDATOR),
        )
        for error in validator.iter_errors(document)
    ]
    found += [
        f"{name}: {error.json_path}"
        for name, document in corpus.migrations
        for error in MIGRATION_VALIDATOR.iter_errors(document)
    ]
    return found


def _content_hash_violations(corpus: Corpus) -> list[str]:
    return [
        f"{name}: {operation['contentFile']}"
        for name, document in corpus.migrations
        for operation in document["operations"]
        if operation["op"] == "upsertRevision"
        and hashlib.sha256(corpus.bodies[operation["contentFile"]]).hexdigest()
        != operation["contentSha256"]
    ]


def _plane_key_violations(corpus: Corpus) -> list[str]:
    return [name for name, document in corpus.migrations if "plane" in document]


def _manifest_order_violations(corpus: Corpus) -> list[str]:
    declared = [entry["file"] for entry in corpus.manifest["migrations"]]
    present = [name for name, _document in corpus.migrations]
    found = []
    if declared != sorted(declared):
        found.append("manifest order disagrees with ULID order")
    if declared != present:
        found.append("manifest names a different set of files than the directory holds")
    found += [
        f"{name}: filename prefix is not the migration id"
        for name, document in corpus.migrations
        if not name.startswith(f"{document['id']}-")
    ]
    return found


def _plane_dependency_violations(corpus: Corpus) -> list[str]:
    """A ``visible`` migration must not name an item only a ``withheld`` one creates.

    The reverse is allowed and used once: the withheld draft carries a
    ``related_to`` edge onto a visible ADR. That direction is the affordance the
    corpus exists to exercise; this one would make the ``clean`` build fail.
    """
    planes = _planes(corpus)
    creators = _creating_plane(corpus)
    return [
        f"{name}: references withheld item {value}"
        for name, document in corpus.migrations
        if planes.get(name) == "visible"
        for operation in document["operations"]
        for key in ITEM_REFERENCES
        if (value := operation.get(key)) is not None and creators.get(value) == "withheld"
    ]


def _query_id_violations(corpus: Corpus) -> list[str]:
    ids = [query["id"] for query in corpus.queries["queries"]]
    return sorted({name for name in ids if ids.count(name) > 1})


def _corpora_invariant_violations(corpus: Corpus) -> list[str]:
    return [
        query["id"]
        for query in corpus.queries["queries"]
        if query.get("enabled", True) and query.get("corpora") != ["full", "clean"]
    ]


def _judgement_coverage_violations(corpus: Corpus) -> list[str]:
    judged = [entry["queryId"] for entry in corpus.judgements["judgements"]]
    declared = {query["id"] for query in corpus.queries["queries"]}
    enabled = {query["id"] for query in corpus.queries["queries"] if query.get("enabled", True)}
    duplicated = {name for name in judged if judged.count(name) > 1}
    return (
        [f"unjudged enabled query: {name}" for name in sorted(enabled - set(judged))]
        + [f"judgement names no query: {name}" for name in sorted(set(judged) - declared)]
        + [f"query judged twice: {name}" for name in sorted(duplicated)]
    )


def _disjointness_violations(corpus: Corpus) -> list[str]:
    return [
        f"{entry['queryId']}: {sorted(overlap)}"
        for entry in corpus.judgements["judgements"]
        if (overlap := _ids(entry, "relevant") & _ids(entry, "forbidden"))
    ]


def _empty_judgement_violations(corpus: Corpus) -> list[str]:
    return [
        entry["queryId"]
        for entry in corpus.judgements["judgements"]
        if not (
            entry.get("relevant")
            or entry.get("forbidden")
            or entry.get("evidence")
            or entry.get("expectAbstention")
        )
    ]


def _evidence_subsumption_violations(corpus: Corpus) -> list[str]:
    found = []
    for entry in corpus.judgements["judgements"]:
        pairs = [(ref["sourceUri"], ref.get("filePath")) for ref in entry.get("evidence", [])]
        found += [
            f"{entry['queryId']}: duplicate {pair}"
            for pair in sorted({p for p in pairs if pairs.count(p) > 1})
        ]
        bare = {uri for uri, path in pairs if path is None}
        narrowed = {uri for uri, path in pairs if path is not None}
        found += [f"{entry['queryId']}: subsumed {uri}" for uri in sorted(bare & narrowed)]
    return found


def _judged_item_violations(corpus: Corpus) -> list[str]:
    creators = _creating_plane(corpus)
    found = []
    for entry in corpus.judgements["judgements"]:
        judged = _ids(entry, "relevant") | _ids(entry, "forbidden")
        found += [
            f"{entry['queryId']}: no migration creates {name}"
            for name in sorted(judged - set(creators))
        ]
        found += [
            f"{entry['queryId']}: relevant item {name} is not visible-plane"
            for name in sorted(_ids(entry, "relevant"))
            if creators.get(name, "withheld") != "visible"
        ]
    return found


def _census_violations(corpus: Corpus) -> list[str]:
    found = []
    for plane, wanted in (("full", {"visible", "withheld"}), ("clean", {"visible"})):
        derived = _derive_census(corpus, wanted)
        declared = corpus.manifest["census"][plane]
        found += [
            f"{plane}.{key}: declared {declared[key]!r}, derived {derived[key]!r}"
            for key in DERIVABLE_CENSUS_KEYS
            if declared[key] != derived[key]
        ]
    return found


# -- the corpus as committed satisfies every rule -----------------------------


def test_every_contract_document_and_migration_validates_against_its_schema() -> None:
    assert _schema_violations(CORPUS) == []


def test_every_content_sha256_matches_the_body_bytes_committed_beside_it() -> None:
    assert _content_hash_violations(CORPUS) == []


def test_no_migration_document_declares_a_plane() -> None:
    """ADR-0036 decision 6: the plane lives in the manifest and nowhere else.

    The migration schema is closed at its root, so a ``plane`` written into one
    is already refused -- but only where that schema runs, which is ``theurian
    migrate`` over a project and not this directory.
    """
    assert _plane_key_violations(CORPUS) == []


def test_the_manifest_lists_every_migration_once_in_ulid_order() -> None:
    assert _manifest_order_violations(CORPUS) == []


def test_no_visible_migration_depends_on_a_withheld_one() -> None:
    """The ``clean`` build applies the visible plane alone; a visible migration
    naming a withheld item would make that build fail outright, which is a
    corpus defect and not a retrieval measurement.
    """
    assert _plane_dependency_violations(CORPUS) == []


def test_no_two_queries_share_an_id() -> None:
    assert _query_id_violations(CORPUS) == []


def test_every_enabled_query_runs_against_both_corpora() -> None:
    """A query that ran against ``full`` alone could not show that the withheld
    plane moved nothing, so the disclosure-equality form ADR-0036 decision 6
    fixes has no exceptions to enumerate.
    """
    assert _corpora_invariant_violations(CORPUS) == []


def test_every_enabled_query_has_exactly_one_judgement() -> None:
    assert _judgement_coverage_violations(CORPUS) == []


def test_no_judgement_both_requires_and_forbids_an_item() -> None:
    assert _disjointness_violations(CORPUS) == []


def test_no_judgement_entry_judges_nothing() -> None:
    """``judgementEntry`` requires ``queryId`` alone, so an entry carrying none
    of the four judging fields validates while asserting nothing (ADR-0036,
    *Still owed*).
    """
    assert _empty_judgement_violations(CORPUS) == []


def test_no_evidence_entry_subsumes_another() -> None:
    """``filePath`` narrows a ``sourceUri``, so ``(u, absent)`` standing beside
    ``(u, f)`` lets one cited anchor satisfy both entries and score twice,
    inflating evidence precision (ADR-0036, *Still owed*).
    """
    assert _evidence_subsumption_violations(CORPUS) == []


def test_every_judged_item_exists_and_every_relevant_one_is_visible_plane() -> None:
    """A withheld item may be named in ``forbidden`` and never in ``relevant``.

    A ``relevant`` withheld item would ask the ``clean`` corpus to return a
    document it never held, so every equality query would score a miss that is
    not a retrieval defect.
    """
    assert _judged_item_violations(CORPUS) == []


def test_the_manifest_census_agrees_with_the_migrations_it_declares() -> None:
    assert _census_violations(CORPUS) == []


def test_the_census_omits_every_zero_count_label() -> None:
    """The manifest schema's own words: "a status with zero expected items is
    simply absent rather than listed as `0`".
    """
    counts = [
        count
        for plane in ("full", "clean")
        for key in ("byStatus", "bySensitivity")
        for count in CORPUS.manifest["census"][plane][key].values()
    ]

    assert 0 not in counts


# -- each rule, over a copy carrying the defect it exists to catch ------------


def test_the_schema_rule_catches_a_query_id_the_pattern_refuses() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"][0]["id"] = "Q-Uppercase"

    assert _schema_violations(replace(CORPUS, queries=queries)) != []


def test_the_schema_rule_catches_a_migration_missing_its_api_version() -> None:
    name, document = CORPUS.migrations[0]
    broken = copy.deepcopy(document)
    del broken["apiVersion"]

    assert _schema_violations(replace(CORPUS, migrations=((name, broken),))) != []


def test_the_content_hash_rule_catches_a_body_edited_after_it_was_pinned() -> None:
    bodies = dict(CORPUS.bodies)
    edited = next(iter(bodies))
    bodies[edited] = bodies[edited] + b"one appended byte\n"

    assert _content_hash_violations(replace(CORPUS, bodies=bodies)) != []


def test_the_plane_rule_catches_a_plane_written_into_a_migration() -> None:
    name, document = CORPUS.migrations[0]
    perturbed = copy.deepcopy(document)
    perturbed["plane"] = "withheld"

    assert _plane_key_violations(replace(CORPUS, migrations=((name, perturbed),))) != []


def test_the_order_rule_catches_a_manifest_listing_two_migrations_out_of_order() -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    manifest["migrations"][0], manifest["migrations"][1] = (
        manifest["migrations"][1],
        manifest["migrations"][0],
    )

    assert _manifest_order_violations(replace(CORPUS, manifest=manifest)) != []


def test_the_order_rule_catches_a_migration_the_manifest_never_declares() -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    del manifest["migrations"][-1]

    assert _manifest_order_violations(replace(CORPUS, manifest=manifest)) != []


def test_the_plane_dependency_rule_catches_a_visible_edge_onto_a_withheld_item() -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    migrations = copy.deepcopy(list(CORPUS.migrations))
    withheld = next(
        entry["file"] for entry in manifest["migrations"] if entry["plane"] == "withheld"
    )
    visible = next(index for index, (name, _doc) in enumerate(migrations) if name != withheld)
    migrations[visible][1]["operations"].append(
        {
            "op": "addRelation",
            "sourceItemId": "architecture.a-purge-is-a-build",
            "relationType": "related_to",
            "targetItemId": "domain.rejected-credential-cache",
        }
    )

    perturbed = replace(CORPUS, manifest=manifest, migrations=tuple(migrations))

    assert _plane_dependency_violations(perturbed) != []


def test_the_query_id_rule_catches_a_duplicated_id() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"].append(copy.deepcopy(queries["queries"][0]))

    assert _query_id_violations(replace(CORPUS, queries=queries)) != []


def test_the_corpora_rule_catches_an_enabled_query_running_against_one_index() -> None:
    queries = copy.deepcopy(CORPUS.queries)
    queries["queries"][0]["corpora"] = ["full"]

    assert _corpora_invariant_violations(replace(CORPUS, queries=queries)) != []


def test_the_coverage_rule_catches_an_enabled_query_nobody_judged() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    del judgements["judgements"][0]

    assert _judgement_coverage_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_coverage_rule_catches_a_judgement_naming_no_declared_query() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["queryId"] = "q-never-declared"

    assert _judgement_coverage_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_coverage_rule_catches_one_query_judged_twice() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"].append(copy.deepcopy(judgements["judgements"][0]))

    assert _judgement_coverage_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_disjointness_rule_catches_an_item_both_required_and_forbidden() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = judgements["judgements"][0]
    entry["forbidden"] = [copy.deepcopy(entry["relevant"][0])]

    assert _disjointness_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_empty_judgement_rule_catches_an_entry_carrying_only_a_query_id() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0] = {"queryId": judgements["judgements"][0]["queryId"]}

    assert _empty_judgement_violations(replace(CORPUS, judgements=judgements)) != []


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"sourceUri": "https://github.com/theurian/theurian.git"}, id="subsumes"),
        pytest.param(
            {
                "sourceUri": "https://github.com/theurian/theurian.git",
                "filePath": "docs/adr/0006-immutable-revisions-and-optimistic-concurrency.md",
            },
            id="duplicate-pair",
        ),
    ],
)
def test_the_evidence_rule_catches_an_entry_that_scores_one_anchor_twice(
    extra: dict[str, str],
) -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    entry = next(e for e in judgements["judgements"] if e["queryId"] == "q-optimistic-concurrency")
    entry["evidence"].append(extra)

    assert _evidence_subsumption_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_judged_item_rule_catches_a_judgement_naming_an_item_no_migration_creates() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "architecture.never-created"

    assert _judged_item_violations(replace(CORPUS, judgements=judgements)) != []


def test_the_judged_item_rule_catches_a_withheld_item_promoted_into_relevant() -> None:
    judgements = copy.deepcopy(CORPUS.judgements)
    judgements["judgements"][0]["relevant"][0]["itemId"] = "domain.rejected-credential-cache"

    assert _judged_item_violations(replace(CORPUS, judgements=judgements)) != []


@pytest.mark.parametrize("plane", ["full", "clean"])
def test_the_census_rule_catches_a_declared_item_count_the_migrations_do_not_support(
    plane: str,
) -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    manifest["census"][plane]["items"] += 1

    assert _census_violations(replace(CORPUS, manifest=manifest)) != []


@pytest.mark.parametrize("key", ["byStatus", "bySensitivity"])
def test_the_census_rule_catches_a_label_moved_between_two_buckets(key: str) -> None:
    manifest = copy.deepcopy(CORPUS.manifest)
    labels = sorted(manifest["census"]["full"][key])
    manifest["census"]["full"][key][labels[0]] += 1
    manifest["census"]["full"][key][labels[-1]] -= 1

    assert _census_violations(replace(CORPUS, manifest=manifest)) != []
