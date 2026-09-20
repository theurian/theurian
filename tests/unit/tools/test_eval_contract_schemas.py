"""Pins for the Phase A corpus interface contract (docs/roadmap.md, "Phase A").

``tools/eval/schemas/{manifest,queries,judgements}.schema.json`` are what the
frozen fixture corpus a later slice (S3) builds will be validated
against. The S2 loader that consumes them does not exist yet, so without this
module every ``allOf``/``if``/``then`` gate, closed ``additionalProperties``
and pattern bound in the three files is a guard no data reaches -- a schema
change here would pass every existing test in the repository. Each ``C``-group
test below asserts a validation *failure* on a probe instance, so deleting the
constraint it pins turns the assertion false.

The ``D``-group tests pin the three places these schemas transcribe a bound
from elsewhere rather than deriving it: a judged ``itemId``'s shape from
``retrieval-result.schema.json`` (what ``knowledge.search`` actually
publishes), a query's length bound from ``knowledge-search-input.schema.json``,
and ``kValues``' ceiling from ``theurian.mcp.tools.MAX_RESULTS``. If the
transcribed source moves and the copy here does not, these redden.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from theurian.mcp.tools import MAX_RESULTS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_SCHEMAS = REPO_ROOT / "tools" / "eval" / "schemas"
WIRE_SCHEMAS = REPO_ROOT / "schemas"

MANIFEST_SCHEMA = "manifest.schema.json"
QUERIES_SCHEMA = "queries.schema.json"
JUDGEMENTS_SCHEMA = "judgements.schema.json"


def _load_eval(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EVAL_SCHEMAS / name).read_text(encoding="utf-8"))
    return loaded


def _load_wire(relative: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((WIRE_SCHEMAS / relative).read_text(encoding="utf-8"))
    return loaded


def _is_valid(name: str, instance: dict[str, Any]) -> bool:
    return Draft202012Validator(_load_eval(name)).is_valid(instance)


def _valid_manifest() -> dict[str, Any]:
    return {
        "contractVersion": 1,
        "corpusId": "adr-corpus-v1",
        "kValues": [1, 5, 10],
        "migrations": [
            {"file": "01MB4V3XKQ7ZPYE8R2NGT5HW6A-adr-0001-example.yaml", "plane": "visible"},
        ],
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


def _valid_queries() -> dict[str, Any]:
    return {
        "queries": [
            {
                "id": "auth-policy-lookup",
                "class": "exact-decision",
                "query": "What is the auth policy?",
            },
        ]
    }


def _valid_judgements() -> dict[str, Any]:
    return {
        "judgements": [
            {
                "queryId": "auth-policy-lookup",
                "relevant": [{"itemId": "architecture.auth-policy"}],
            }
        ]
    }


# -- A: the schemas are themselves valid JSON Schema -------------------------


@pytest.mark.parametrize("name", [MANIFEST_SCHEMA, QUERIES_SCHEMA, JUDGEMENTS_SCHEMA])
def test_each_contract_schema_is_a_valid_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(_load_eval(name))


# -- B: a minimal instance validates ------------------------------------------


def test_a_minimal_manifest_instance_validates() -> None:
    assert _is_valid(MANIFEST_SCHEMA, _valid_manifest())


def test_a_minimal_queries_document_validates() -> None:
    assert _is_valid(QUERIES_SCHEMA, _valid_queries())


def test_a_minimal_judgements_document_validates() -> None:
    assert _is_valid(JUDGEMENTS_SCHEMA, _valid_judgements())


# -- C: manifest constraint machinery -----------------------------------------


def test_manifest_rejects_a_migration_plane_outside_the_declared_enum() -> None:
    manifest = _valid_manifest()
    manifest["migrations"][0]["plane"] = "hidden"

    assert not _is_valid(MANIFEST_SCHEMA, manifest)


def test_manifest_rejects_a_k_value_above_the_max_results_bound() -> None:
    manifest = _valid_manifest()
    manifest["kValues"] = [60]

    assert not _is_valid(MANIFEST_SCHEMA, manifest)


def test_manifest_rejects_an_undeclared_property_inside_census_full() -> None:
    manifest = _valid_manifest()
    manifest["census"]["full"]["unexpectedField"] = 1

    assert not _is_valid(MANIFEST_SCHEMA, manifest)


def test_manifest_rejects_a_migration_filename_without_a_ulid_prefix() -> None:
    manifest = _valid_manifest()
    manifest["migrations"][0]["file"] = "adr-0001-example.yaml"

    assert not _is_valid(MANIFEST_SCHEMA, manifest)


# -- C: queries constraint machinery ------------------------------------------


def test_queries_gates_a_deferred_class_on_enabled_false() -> None:
    """historical/spec-adr-impl/code-decision/impact are admitted ahead of the
    mechanism that answers them (docs/roadmap.md); this `allOf`/`if`/`then` is
    what stops one running before its phase ships.
    """
    enabled_true = _valid_queries()
    enabled_true["queries"][0]["class"] = "historical"
    enabled_true["queries"][0]["enabled"] = True

    enabled_absent = _valid_queries()
    enabled_absent["queries"][0]["class"] = "historical"

    enabled_false = _valid_queries()
    enabled_false["queries"][0]["class"] = "historical"
    enabled_false["queries"][0]["enabled"] = False

    assert not _is_valid(QUERIES_SCHEMA, enabled_true)
    assert not _is_valid(QUERIES_SCHEMA, enabled_absent)
    assert _is_valid(QUERIES_SCHEMA, enabled_false)


def test_queries_rejects_an_empty_corpora_list() -> None:
    queries = _valid_queries()
    queries["queries"][0]["corpora"] = []

    assert not _is_valid(QUERIES_SCHEMA, queries)


def test_queries_rejects_a_query_over_the_length_bound() -> None:
    queries = _valid_queries()
    queries["queries"][0]["query"] = "x" * 2001

    assert not _is_valid(QUERIES_SCHEMA, queries)


def test_queries_rejects_an_undeclared_property_on_a_query_entry() -> None:
    queries = _valid_queries()
    queries["queries"][0]["unexpectedField"] = "x"

    assert not _is_valid(QUERIES_SCHEMA, queries)


# -- C: judgements constraint machinery ---------------------------------------


def test_judgements_gates_abstention_on_an_empty_relevant_list() -> None:
    non_empty = _valid_judgements()
    non_empty["judgements"][0]["expectAbstention"] = True

    empty_relevant = _valid_judgements()
    empty_relevant["judgements"][0]["relevant"] = []
    empty_relevant["judgements"][0]["expectAbstention"] = True

    absent_relevant = {"judgements": [{"queryId": "auth-policy-lookup", "expectAbstention": True}]}

    assert not _is_valid(JUDGEMENTS_SCHEMA, non_empty)
    assert _is_valid(JUDGEMENTS_SCHEMA, empty_relevant)
    assert _is_valid(JUDGEMENTS_SCHEMA, absent_relevant)


def test_judgements_rejects_a_ulid_shaped_item_id() -> None:
    """Pins the S1 finding: a judgement `itemId` is the domain slug
    `knowledge.search` publishes (`architecture.auth-policy`), never a ULID --
    even a syntactically valid one.
    """
    judgements = _valid_judgements()
    judgements["judgements"][0]["relevant"][0]["itemId"] = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    assert not _is_valid(JUDGEMENTS_SCHEMA, judgements)


def test_judgements_rejects_duplicate_evidence_entries() -> None:
    judgements = _valid_judgements()
    judgements["judgements"][0]["evidence"] = [
        {"sourceUri": "https://example.com/doc"},
        {"sourceUri": "https://example.com/doc"},
    ]

    assert not _is_valid(JUDGEMENTS_SCHEMA, judgements)


def test_judgements_rejects_an_undeclared_property_inside_a_relevant_item() -> None:
    judgements = _valid_judgements()
    judgements["judgements"][0]["relevant"][0]["unexpectedField"] = "x"

    assert not _is_valid(JUDGEMENTS_SCHEMA, judgements)


# -- D: bounds transcribed from elsewhere, pinned against their source -------


def test_judged_item_id_pattern_and_max_length_match_the_retrieval_result_schema() -> None:
    """The judgements contract transcribes what `knowledge.search` actually
    publishes rather than approximating it -- if `retrieval-result.schema.json`
    moves its `itemId` bound and this copy does not, a judgement file could
    validate against a shape the wire no longer emits.
    """
    judged_item_id = _load_eval(JUDGEMENTS_SCHEMA)["$defs"]["judgedItem"]["properties"]["itemId"]
    wire_item_id = _load_wire("knowledge/retrieval-result.schema.json")["properties"]["itemId"]

    assert judged_item_id["pattern"] == wire_item_id["pattern"]
    assert judged_item_id["maxLength"] == wire_item_id["maxLength"]


def test_query_max_length_matches_the_knowledge_search_input_schema() -> None:
    query_entry = _load_eval(QUERIES_SCHEMA)["$defs"]["queryEntry"]
    search_input = _load_wire("mcp/knowledge-search-input.schema.json")

    assert (
        query_entry["properties"]["query"]["maxLength"]
        == search_input["properties"]["query"]["maxLength"]
    )


def test_k_values_maximum_matches_max_results() -> None:
    k_values_max = _load_eval(MANIFEST_SCHEMA)["properties"]["kValues"]["items"]["maximum"]

    assert k_values_max == MAX_RESULTS
