"""ADR-0039's schema scan: every governed spelling under ``schemas/``, held exact.

Round two found a keyword key narrower than the claim it held three times, so
detection enumerates no construct: ``adr_0039_support.occurrences`` reads every
key and every string value by the ADR's *Detection* rule, and the keyword key only
classifies what it finds. ``test_adr_0039_claims.py`` names the rest of the pin and
states its reach.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Any, Final

import pytest
from adr_0039_support import (
    MEMBERS,
    MIGRATION_SCHEMA,
    REPO_ROOT,
    SCHEMAS,
    SETS,
    Occurrence,
    _function,
    _schema,
    _section,
    _spelled,
    _table,
    closed,
    keywords,
    occurrences,
)
from jsonschema import Draft202012Validator

from theurian.domain.enums import SURFACEABLE_STATUSES

pytestmark = pytest.mark.unit

_CONFIG: Final = "schemas/config/project-config.schema.json"
_RESULT: Final = "schemas/knowledge/retrieval-result.schema.json"
_STATUS: Final = "schemas/mcp/knowledge-status-response.schema.json"
_FINDINGS: Final = "schemas/mcp/review-findings-response.schema.json"
_CANDIDATE: Final = "schemas/mcp/review-generate-knowledge-candidate-input.schema.json"


def _scan() -> dict[str, list[Occurrence]]:
    """Each schema's occurrences, keyed by its repository-relative path."""
    return {
        path.relative_to(REPO_ROOT).as_posix(): occurrences(_schema(path))
        for path in sorted(SCHEMAS.rglob("*.json"))
    }


def _published() -> list[tuple[str, str, str, str]]:
    """``(class, file, pointer, member)`` for every occurrence outside the migration schema."""
    migration = MIGRATION_SCHEMA.relative_to(REPO_ROOT).as_posix()
    return [
        (kind, path, pointer, member)
        for path, found in _scan().items()
        if path != migration
        for kind, pointer, member in found
    ]


def test_the_adrs_pasted_scan_output_is_the_live_scan() -> None:
    """Both populations, per class and per file: the tripwire the ADR holds exact."""
    migration = MIGRATION_SCHEMA.relative_to(REPO_ROOT).as_posix()
    where: dict[tuple[str, str], collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    totals: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for path, found in _scan().items():
        population = "migration" if path == migration else "published"
        for kind, _, member in found:
            totals[population][kind] += 1
            if population == "published":
                where[(kind, Path(path).name)][member] += 1
    live = [
        *(
            f"{kind} {name} {sum(members.values())} {dict(sorted(members.items()))}"
            for (kind, name), members in sorted(where.items())
        ),
        *(
            f"{population} {sum(counts.values())} {dict(sorted(counts.items()))}"
            for population, counts in sorted(totals.items())
        ),
    ]
    lines = _section("### What the wire carries")
    pasted = lines[lines.index("```text") + 1 :]

    assert pasted[: pasted.index("```")] == live


#: The class table's *Where* column, by pointer: ``(class, file, pointer) -> members``.
WHERE: Final = {
    ("enumerated", _RESULT, "#/properties/status"): ["approved", "draft", "proposed"],
    ("enumerated", _RESULT, "#/properties/trustLevel"): [
        "authoritative",
        "inferred",
        "reviewed",
        "unverified",
    ],
    ("enumerated", _RESULT, "#/properties/sensitivity"): [
        "confidential",
        "internal",
        "public",
        "restricted",
    ],
    ("enumerated", _CONFIG, "#/properties/retrieval/properties/includeStatuses/items"): sorted(
        SETS["KnowledgeStatus"]
    ),
    ("key-closure", _STATUS, "#/properties/itemsByStatus"): ["approved", "draft", "proposed"],
    ("overlap", _CANDIDATE, "#/properties/category"): ["known-exception", "rejected-approach"],
    ("overlap", _FINDINGS, "#/properties/findings/items/properties/reviewer"): ["security"],
    ("pattern", _CONFIG, "#/properties/traceabilityPolicy"): ["architecture"],
    ("property-name", _CONFIG, "#"): ["security"],
    ("non-closing", _CONFIG, "#/properties/retrieval/properties/includeStatuses"): ["approved"],
}


def test_each_published_occurrence_sits_where_the_class_table_says() -> None:
    grouped: dict[tuple[str, str, str], list[str]] = collections.defaultdict(list)
    for kind, path, pointer, member in _published():
        grouped[(kind, path, pointer)].append(member)

    assert {site: sorted(members) for site, members in grouped.items()} == WHERE


def test_the_class_table_counts_each_class_the_published_scan_finds() -> None:
    header, *rows = _table(_section("### What the wire carries"))
    counted = collections.Counter(kind for kind, *_ in _published())

    assert header == ("Class", "The occurrence is", "Outside the migration schema", "Where")
    assert {row[0].strip("`"): int(row[2]) for row in rows} == {
        kind: counted[kind]
        for kind in (
            "enumerated",
            "key-closure",
            "overlap",
            "pattern",
            "property-name",
            "non-closing",
            "unclassified",
        )
    }
    assert set(counted) <= {row[0].strip("`") for row in rows}, "a class the table lacks"


def test_the_migration_schemas_occurrences_are_enumerations_two_defaults_and_operations() -> None:
    found = occurrences(_schema(MIGRATION_SCHEMA))

    assert collections.Counter(kind for kind, _, _ in found) == {
        "enumerated": 57,
        "non-closing": 2,
        "property-name": 2,
    }
    assert sorted(m for kind, _, m in found if kind == "non-closing") == ["internal", "unverified"]
    assert [m for kind, _, m in found if kind == "property-name"] == ["operations", "operations"]


def test_no_published_occurrence_closes_a_value_to_kind_relation_type_or_the_operation_set() -> (
    None
):
    """Decisions 8 and 9 rest on this: only status, trust level and sensitivity close."""
    additive = SETS["KnowledgeKind"] | SETS["RelationType"] | SETS["OperationKind"]
    wire_enumerated = SETS["KnowledgeStatus"] | SETS["TrustLevel"] | SETS["Sensitivity"]
    published = _published()
    overlaps = {(path, pointer) for kind, path, pointer, _ in published if kind == "overlap"}

    assert {m for kind, *_, m in published if kind in ("enumerated", "key-closure")} <= (
        wire_enumerated
    )
    assert [
        (path, pointer)
        for path, pointer in overlaps
        if any(members <= _closed_at(path, pointer) for members in SETS.values())
    ] == []
    assert sorted((kind, pointer, m) for kind, _, pointer, m in published if m in additive) == [
        ("overlap", "#/properties/category", "known-exception"),
        ("overlap", "#/properties/category", "rejected-approach"),
        ("overlap", "#/properties/findings/items/properties/reviewer", "security"),
        ("pattern", "#/properties/traceabilityPolicy", "architecture"),
        ("property-name", "#", "security"),
    ]
    assert {m for *_, m in published} & (SETS["RelationType"] | SETS["OperationKind"]) == set()


def _closed_at(path: str, pointer: str) -> set[str]:
    node: Any = _schema(REPO_ROOT / path)
    for step in pointer.split("/")[1:]:
        node = node[int(step)] if isinstance(node, list) else node[step]
    return closed(node) or set()


def test_the_deprecated_annotation_is_the_live_keyword_the_scan_skips() -> None:
    config = _schema(REPO_ROOT / _CONFIG)
    annotated = [key for key, value in config["properties"].items() if value.get("deprecated")]

    assert "deprecated" in keywords() and "deprecated" in MEMBERS
    assert annotated, "positive control: a property carries the `deprecated` annotation"
    assert not [o for o in occurrences(config) if o[1] == f"#/properties/{annotated[0]}"]


#: The scan's positive controls and its stated holes, each driven from a document.
SNIPPETS: Final = {
    "a property named after a member": (
        {"type": "object", "properties": {"draft": {"type": "string"}}},
        [("property-name", "#", "draft")],
    ),
    "a governed enum beneath a closing oneOf": (
        {
            "oneOf": [{"const": "x-a"}, {"const": "x-b"}],
            "properties": {"kind": {"enum": ["architecture", "decision"]}},
        },
        [
            ("enumerated", "#/properties/kind", "architecture"),
            ("enumerated", "#/properties/kind", "decision"),
        ],
    ),
    "a closing oneOf of governed consts": (
        {"oneOf": [{"const": "supersedes"}, {"const": "depends_on"}]},
        [("enumerated", "#", "supersedes"), ("enumerated", "#", "depends_on")],
    ),
    "a key set closed by propertyNames": (
        {"type": "object", "propertyNames": {"enum": ["public", "internal"]}},
        [
            ("key-closure", "#/propertyNames", "public"),
            ("key-closure", "#/propertyNames", "internal"),
        ],
    ),
    "a superset construct is an overlap": (
        {"enum": ["domain", "elsewhere", None]},
        [("overlap", "#", "domain")],
    ),
    "a pattern spells whole words only": (
        {"pattern": "^(domain-behavior|depends_on)$"},
        [("pattern", "#", "depends_on")],
    ),
    "noise: a new file's $schema, a $id naming draft, a property limit": (
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://theurian.dev/schemas/draft/limit.schema.json",
            "description": "A draft of the limit.",
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
        [],
    ),
    "the stated hole: a member embedded in a longer example": (
        {"type": "string", "examples": ["status:draft"]},
        [],
    ),
    "a keyword key directly under properties is a name": (
        {"properties": {"deprecated": {}}},
        [("property-name", "#", "deprecated")],
    ),
    "a keyword key one level below properties is a keyword": (
        {"properties": {"a": {"deprecated": True}}},
        [],
    ),
}


@pytest.mark.parametrize(("document", "expected"), SNIPPETS.values(), ids=list(SNIPPETS))
def test_the_scan_finds_each_spelling_the_detection_rule_names_and_nothing_else(
    document: dict[str, Any], expected: list[Occurrence]
) -> None:
    assert occurrences(document) == expected


#: ADR-0039's residual bullet 2 (#858), each form beside its control: the same instance
#: literal with the excluding key made plain, which the scan reports.
INSTANCE_LITERAL_HOLE: Final = {
    "a keyword key of a const instance": (
        {"const": {"deprecated": True}},
        {"const": {"draft": True}},
        [("custom-key", "#/const", "draft")],
    ),
    "a keyword key of a default instance": (
        {"default": {"deprecated": True}},
        {"default": {"draft": True}},
        [("custom-key", "#/default", "draft")],
    ),
    "a prose key of a const instance": (
        {"const": {"title": "domain"}},
        {"const": {"name": "domain"}},
        [("unclassified", "#/const", "domain")],
    ),
    "an identifier key of an enum instance": (
        {"enum": [{"$id": "domain"}]},
        {"enum": [{"id": "domain"}]},
        [("unclassified", "#/enum/0", "domain")],
    ),
    "an x- key of each enum instance": (
        {"enum": [{"x-kind": "domain"}, {"x-kind": "api"}]},
        {"enum": [{"kind": "domain"}, {"kind": "api"}]},
        [("unclassified", "#/enum/0", "domain"), ("unclassified", "#/enum/1", "api")],
    ),
}


@pytest.mark.parametrize(
    ("hole", "control", "reported"), INSTANCE_LITERAL_HOLE.values(), ids=list(INSTANCE_LITERAL_HOLE)
)
def test_a_member_in_an_instance_literal_under_an_excluded_key_is_not_reported(
    hole: dict[str, Any], control: dict[str, Any], reported: list[Occurrence]
) -> None:
    """RED when #858 lands, and ADR-0039's residual bullet 2 must then move."""
    assert occurrences(control) == reported
    assert occurrences(hole) == []


def test_the_cross_file_ref_population_is_the_ten_the_adr_counts() -> None:
    """The ``$ref`` hole, held beside the scan: a ``$ref`` spells a pointer, not a member.

    Keyed on the canonical ``"$ref": "`` spelling only; ``$dynamicRef`` and any other
    spelling are outside it (#858).
    """
    by_id = {_schema(path)["$id"]: path.name for path in SCHEMAS.rglob("*.json")}
    refs = collections.Counter(
        (path.name, by_id[ref.split("#", 1)[0]])
        for path in sorted(SCHEMAS.rglob("*.json"))
        for ref in re.findall(r'"\$ref": *"([^#"][^"]*)"', path.read_text(encoding="utf-8"))
    )

    assert refs == {
        ("knowledge-generate-migration-draft-input.schema.json", "tool-context.schema.json"): 1,
        ("knowledge-get-input.schema.json", "tool-context.schema.json"): 1,
        ("knowledge-propose-change-input.schema.json", "tool-context.schema.json"): 1,
        ("knowledge-search-input.schema.json", "tool-context.schema.json"): 1,
        ("knowledge-search-response.schema.json", "retrieval-metadata.schema.json"): 1,
        ("knowledge-search-response.schema.json", "retrieval-result.schema.json"): 1,
        ("knowledge-status-input.schema.json", "tool-context.schema.json"): 1,
        ("review-findings-input.schema.json", "tool-context.schema.json"): 1,
        ("review-generate-knowledge-candidate-input.schema.json", "tool-context.schema.json"): 1,
        ("review-search-input.schema.json", "tool-context.schema.json"): 1,
    }


def test_items_by_status_closes_its_keys_to_the_surfaceable_statuses() -> None:
    items_by_status = _schema(REPO_ROOT / _STATUS)["properties"]["itemsByStatus"]
    validator = Draft202012Validator(items_by_status)
    counter = _function("infrastructure/sqlite/store.py", "count_surfaceable_by_status")

    assert set(items_by_status["properties"]) == {status.value for status in SURFACEABLE_STATUSES}
    assert items_by_status["additionalProperties"] is False
    assert not validator.is_valid({"superseded": 1})
    assert validator.is_valid({"approved": 1})
    assert "SURFACEABLE_STATUSES" in _spelled(counter)


def test_the_patterns_that_accept_a_governed_member_are_open_identifier_grammars() -> None:
    """The unspelled-pattern hole's tripwire, keyed by file and pointer.

    A pattern that closes a value without spelling a member is not an occurrence,
    so every pattern that *accepts* a governed member is held here instead: a new
    one goes RED for a person. Each is also held *open*, accepting an identifier no
    governed set holds, so one closed to members goes RED while one re-anchored or
    bounded but still open stays GREEN.
    """
    accepting = {
        (path.relative_to(REPO_ROOT).as_posix(), pointer): node["pattern"]
        for path in sorted(SCHEMAS.rglob("*.json"))
        if path != MIGRATION_SCHEMA
        for pointer, node in _pattern_nodes(_schema(path))
        if any(re.search(node["pattern"], member) for member in MEMBERS)
    }

    assert set(_OPEN_PROBES).isdisjoint(MEMBERS)
    assert set(accepting) == {
        ("schemas/cli/version.schema.json", "#/properties/platform"),
        (_CONFIG, "#/properties/projectId"),
        (_RESULT, "#/properties/itemId"),
        ("schemas/mcp/knowledge-search-response.schema.json", "#/properties/projectId"),
        (_STATUS, "#/properties/projectId"),
        (
            "schemas/mcp/project-list-response.schema.json",
            "#/properties/projects/items/properties/projectId",
        ),
        ("schemas/mcp/tool-context.schema.json", "#/properties/projectId"),
    }
    assert [
        site
        for site, pattern in accepting.items()
        if not any(re.search(pattern, probe) for probe in _OPEN_PROBES)
    ] == [], "a pattern that accepts a governed member accepts no other identifier"


#: Identifiers no governed set holds; an open identifier grammar accepts one of them.
_OPEN_PROBES: Final = ("zz-not-a-member", "zz-notamember", "zz")


def _pattern_nodes(node: object, pointer: str = "#") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if isinstance(node.get("pattern"), str):
            found.append((pointer, node))
        for key, value in node.items():
            found += _pattern_nodes(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _pattern_nodes(value, f"{pointer}/{index}")
    return found
