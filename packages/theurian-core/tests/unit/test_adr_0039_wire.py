"""ADR-0039's wire facts: what the published schemas close, and what travels open.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import importlib
import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from adr_0037_support import collapsed, revision
from adr_0039_support import (
    GOVERNED,
    GOVERNED_NAMES,
    MIGRATION_SCHEMA,
    REPO_ROOT,
    SCHEMAS,
    _git,
    _nodes,
    _schema,
    _scoped,
    _section,
    _spelled,
    _trees,
)

from theurian.application.okf_import import ImportedConcept
from theurian.domain.enums import (
    SURFACEABLE_STATUSES,
    KnowledgeKind,
    KnowledgeStatus,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.review import KnowledgeCandidate
from theurian.mcp.results import result_payload

pytestmark = pytest.mark.unit


_SETS: Final = {cls.__name__: frozenset(member.value for member in cls) for cls in GOVERNED}


def _closed(node: dict[str, Any]) -> set[str] | None:
    """The values a closing construct admits, as text; ``None`` if it closes nothing."""
    if isinstance(node.get("enum"), list):
        raw = node["enum"]
    elif "const" in node:
        raw = [node["const"]]
    else:
        branches = node.get("oneOf") or node.get("anyOf")
        if not isinstance(branches, list) or not branches:
            return None
        parts = [_closed(branch) if isinstance(branch, dict) else None for branch in branches]
        return None if None in parts else set().union(*(part for part in parts if part))
    return {
        value if isinstance(value, str) else json.dumps(value) for value in raw if value is not None
    }


def _constructs(node: object, pointer: str = "#") -> Iterator[tuple[str, str, set[str]]]:
    """``(pointer, construct, values)``; a closing ``oneOf``/``anyOf`` is not descended into."""
    if isinstance(node, dict):
        values = _closed(node)
        if values is not None:
            construct = "enum" if "enum" in node else "const" if "const" in node else "oneOf/anyOf"
            yield pointer, construct, values
            if construct == "oneOf/anyOf":
                return
        for key, value in node.items():
            yield from _constructs(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _constructs(value, f"{pointer}/{index}")


def _classified(
    documents: Mapping[str, object] | None = None,
) -> tuple[
    dict[tuple[str, str], list[str]],
    dict[tuple[str, str], dict[str, list[str]]],
    collections.Counter[str],
]:
    """The ADR's program: what enumerates a governed set, what only overlaps one, what was read."""
    if documents is None:
        documents = {
            path.relative_to(REPO_ROOT).as_posix(): _schema(path)
            for path in sorted(SCHEMAS.rglob("*.json"))
            if path != MIGRATION_SCHEMA
        }
    enumerates: dict[tuple[str, str], list[str]] = {}
    overlaps: dict[tuple[str, str], dict[str, list[str]]] = {}
    read: collections.Counter[str] = collections.Counter()
    for path, document in documents.items():
        for pointer, construct, values in _constructs(document):
            read[construct] += 1
            inside = [name for name, members in _SETS.items() if values and values <= members]
            shared = {
                name: sorted(values & members)
                for name, members in _SETS.items()
                if values & members
            }
            if inside:
                enumerates[(path, pointer)] = inside
            elif shared:
                overlaps[(path, pointer)] = shared
    return enumerates, overlaps, read


_RESULT: Final = "schemas/knowledge/retrieval-result.schema.json"


_CONFIG: Final = "schemas/config/project-config.schema.json"


def test_the_adrs_pasted_output_is_the_live_classification() -> None:
    """The ADR's pasted run, re-run: its ``read`` line is the positive control per construct."""
    enumerates, overlaps, read = _classified()
    lines = _section("### What the wire carries")
    pasted = lines[lines.index("```text") + 1 :]
    live = [
        *(f"enumerates {names} {path} {pointer}" for (path, pointer), names in enumerates.items()),
        *(f"overlaps {shared} {path} {pointer}" for (path, pointer), shared in overlaps.items()),
        f"read {dict(sorted(read.items()))}",
    ]

    assert pasted[: pasted.index("```")] == live


def test_each_closing_construct_is_classified_by_containment() -> None:
    """Driven from a snippet: no live schema closes a value to a governed set by ``oneOf``."""
    snippet = {
        "snippet": {
            "kinds": {"oneOf": [{"const": "domain"}, {"const": "api"}]},
            "open": {"anyOf": [{"const": "elsewhere"}, {"type": "string"}]},
            "one": {"const": "supersedes"},
            "superset": {"enum": ["domain", "elsewhere", None]},
        }
    }

    enumerates, overlaps, read = _classified(snippet)

    assert enumerates == {
        ("snippet", "#/kinds"): ["KnowledgeKind"],
        ("snippet", "#/one"): ["RelationType"],
    }
    assert overlaps == {("snippet", "#/superset"): {"KnowledgeKind": ["domain"]}}
    assert read == {"oneOf/anyOf": 1, "const": 2, "enum": 1}


def test_the_enumerates_key_finds_status_trust_level_and_sensitivity_and_nothing_additive() -> None:
    enumerates, _, _ = _classified()
    result = _schema(REPO_ROOT / _RESULT)["properties"]

    assert enumerates == {
        (_CONFIG, "#/properties/retrieval/properties/includeStatuses/items"): ["KnowledgeStatus"],
        (_RESULT, "#/properties/status"): ["KnowledgeStatus"],
        (_RESULT, "#/properties/trustLevel"): ["TrustLevel"],
        (_RESULT, "#/properties/sensitivity"): ["Sensitivity"],
    }
    assert sorted(result["status"]["enum"]) == sorted(
        status.value for status in SURFACEABLE_STATUSES
    )
    assert (
        set(
            _schema(REPO_ROOT / _CONFIG)["properties"]["retrieval"]["properties"][
                "includeStatuses"
            ]["items"]["enum"]
        )
        == _SETS["KnowledgeStatus"]
    )
    assert (len(result["trustLevel"]["enum"]), len(result["sensitivity"]["enum"])) == (4, 4)


def test_the_overlap_population_is_exactly_the_two_enums_the_adr_classified() -> None:
    """The key's superset hole as a tripwire: a new overlap is for a person to classify.

    A construct carrying a governed set plus other members is not *contained* in
    the set, so the enumerates key does not count it; it lands here instead.
    """
    _, overlaps, _ = _classified()

    assert overlaps == {
        (
            "schemas/mcp/review-findings-response.schema.json",
            "#/properties/findings/items/properties/reviewer",
        ): {"KnowledgeKind": ["security"]},
        (
            "schemas/mcp/review-generate-knowledge-candidate-input.schema.json",
            "#/properties/category",
        ): {"KnowledgeKind": ["known-exception", "rejected-approach"]},
    }


def _spells(member: str, pattern: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(member)}(?![\w-])", pattern) is not None


def test_no_pattern_default_or_example_closes_a_value_to_a_governed_member() -> None:
    """The key's ``pattern`` hole, measured here because the key reads no pattern."""
    members = set().union(*_SETS.values())
    accepting: set[tuple[str, str]] = set()
    spelling: list[tuple[str, str]] = []
    governed: list[tuple[str, str, object]] = []
    for path in sorted(SCHEMAS.rglob("*.json")):
        if path == MIGRATION_SCHEMA:
            continue
        name = path.relative_to(REPO_ROOT).as_posix()
        for pointer, node in _nodes(_schema(path)):
            pattern = node.get("pattern")
            if isinstance(pattern, str):
                if any(re.search(pattern, member) for member in members):
                    accepting.add((name, pointer.rsplit("/", 1)[1]))
                spelling += [(name, member) for member in members if _spells(member, pattern)]
            for keyword in ("const", "default", "examples"):
                value = node.get(keyword)
                values = value if isinstance(value, list) else [value]
                if {v for v in values if isinstance(v, str)} & members:
                    governed.append((name, f"{pointer}/{keyword}", value))

    assert _spells("domain", "^(domain|api)$"), "positive control: the spelling key"
    assert {prop for _, prop in accepting} == {"projectId", "itemId", "platform"}
    assert {name for name, prop in accepting if prop == "platform"} == {
        "schemas/cli/version.schema.json"
    }
    assert spelling == []
    assert governed == [
        (_CONFIG, "#/properties/retrieval/properties/includeStatuses/default", ["approved"])
    ]


def _wire(*, follow_refs: bool = True) -> dict[str, str | None]:
    """Each wire schema, against the schema whose ``$ref`` first reached it."""
    by_id = {_schema(path)["$id"]: path for path in SCHEMAS.rglob("*.json")}
    reached: dict[str, str | None] = {
        path.relative_to(REPO_ROOT).as_posix(): None
        for path in sorted((SCHEMAS / "mcp").glob("*.json"))
    }
    queue = list(reached)
    while queue and follow_refs:
        current = queue.pop()
        for _, node in _nodes(_schema(REPO_ROOT / current)):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                target = by_id[ref.split("#", 1)[0]].relative_to(REPO_ROOT).as_posix()
                if target not in reached:
                    reached[target] = current
                    queue.append(target)
    return reached


def test_the_wire_enumerates_status_trust_level_and_sensitivity_only_through_the_search_ref() -> (
    None
):
    wire = _wire()
    enumerates, _, _ = _classified()
    record_kind = dict(_nodes(_schema(SCHEMAS / "mcp" / "review-search-response.schema.json")))[
        "#/properties/records/items/properties/kind"
    ]

    assert wire[_RESULT] == "schemas/mcp/knowledge-search-response.schema.json"
    assert _RESULT not in _wire(follow_refs=False), "retrieval-result is on the wire only by $ref"
    assert {where for where in enumerates if where[0] in wire} == {
        (_RESULT, "#/properties/status"),
        (_RESULT, "#/properties/trustLevel"),
        (_RESULT, "#/properties/sensitivity"),
    }
    assert _CONFIG not in wire, "includeStatuses is a published configuration schema, not the wire"
    assert record_kind["enum"] == ["pull-request", "review-submission", "review-thread"], (
        "positive control: the walk reaches an enum on a property named `kind`"
    )


def test_the_mcp_inputs_type_governed_fields_as_strings_and_constrain_no_op() -> None:
    propose = _schema(SCHEMAS / "mcp" / "knowledge-propose-change-input.schema.json")["properties"]
    candidate = _schema(SCHEMAS / "mcp" / "review-generate-knowledge-candidate-input.schema.json")
    draft = _schema(SCHEMAS / "mcp" / "knowledge-generate-migration-draft-input.schema.json")

    assert [propose[field]["type"] for field in ("kind", "trustLevel", "sensitivity")] == [
        "string",
        ["string", "null"],
        ["string", "null"],
    ]
    assert candidate["properties"]["kind"]["type"] == "string"
    assert [
        pointer
        for pointer, node in _nodes(draft)
        if "op" in node.get("properties", {}) or "enum" in node or "const" in node
    ] == []


def test_a_governed_value_reaches_a_client_only_in_the_two_unenumerated_response_fields() -> None:
    published = {
        (path, scope, key.value)
        for path, tree in _trees().items()
        if path.startswith("mcp/")
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and key.value in {"relationType", "operations"}
    }

    assert published == {
        ("mcp/tools.py", "knowledge_get", "relationType"),
        ("mcp/tools.py", "_drafted_migration_payload", "operations"),
    }
    for tool in ("knowledge-get", "knowledge-generate-migration-draft"):
        assert not (SCHEMAS / "mcp" / f"{tool}-response.schema.json").exists()


def test_no_mcp_response_publishes_a_knowledge_kind() -> None:
    kinds = {
        (path, scope)
        for path, tree in _trees().items()
        if path.startswith("mcp/")
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Name) and node.id == "KnowledgeKind"
    }
    attributes = {
        (path, ast.unparse(node))
        for path, tree in _trees().items()
        if path.startswith("mcp/")
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"kind", "kind_"}
    }

    assert kinds == {
        ("mcp/tools.py", "knowledge_propose_change"),
        ("mcp/tools.py", "review_generate_knowledge_candidate"),
    }
    assert attributes == {("mcp/review_search.py", "hit.kind")}


def test_every_search_result_publishes_status_trust_level_and_sensitivity() -> None:
    payload = result_payload(
        revision(),
        KnowledgeStatus.APPROVED,
        Sensitivity.INTERNAL,
        datetime(2026, 9, 30, tzinfo=UTC),
    )

    assert (payload["status"], payload["trustLevel"], payload["sensitivity"]) == (
        "approved",
        revision().metadata.trust_level.value,
        "internal",
    )


def test_status_feeds_may_surface_sensitivity_feeds_may_disclose_and_trust_level_neither() -> None:
    tree = _trees()["domain/enums.py"]
    constants = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign | ast.Assign) and node.value is not None
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    gates = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    def reads(gate: ast.FunctionDef) -> set[str]:
        names = _spelled(gate)
        return (
            names | set().union(*(_spelled(constants[n]) for n in names & constants.keys()))
        ) & GOVERNED_NAMES

    assert {name: reads(gate) for name, gate in gates.items()} == {
        "may_surface": {"KnowledgeStatus"},
        "may_disclose": {"Sensitivity"},
    }


def test_import_and_candidate_generation_fix_trust_level_at_inferred_and_never_copy_it() -> None:
    for cls in (ImportedConcept, KnowledgeCandidate):
        field = {f.name: f for f in dataclasses.fields(cls)}["trust_level"]

        assert (field.default, field.init) == (TrustLevel.INFERRED, False), cls.__name__
    assert (
        "theurian_trust_level and theurian_sensitivity are never copied onto a drafted proposal"
        in collapsed(importlib.import_module("theurian.application.okf_import").__doc__ or "")
    )


def test_the_project_config_carries_its_own_api_version_for_another_format() -> None:
    config = _schema(REPO_ROOT / _CONFIG)

    assert config["properties"]["apiVersion"]["const"] == "theurian.dev/v1"
    assert config["$id"] != _schema(MIGRATION_SCHEMA)["$id"]


def test_the_kind_descriptions_advertising_members_are_the_two_c3_names() -> None:
    """C3's list of what a removal updates, beside ``--kind architecture`` pinned below.

    Key: an ``e.g.`` list naming a ``KnowledgeKind`` member in the description of a
    property named ``kind``; an example in any other prose is outside it.
    """
    members = {member.value for member in KnowledgeKind}
    examples = {
        path.relative_to(REPO_ROOT).as_posix(): example
        for path in sorted(SCHEMAS.rglob("*.json"))
        for pointer, node in _nodes(_schema(path))
        if pointer.endswith("/properties/kind")
        for example in re.findall(r"e\.g\. ([^.;]*)", node.get("description", ""))
        if set(example.split(", ")) & members
    }

    assert examples == {
        "schemas/mcp/knowledge-propose-change-input.schema.json": (
            "architecture, decision, security"
        ),
        "schemas/mcp/review-generate-knowledge-candidate-input.schema.json": (
            "convention, architecture, security"
        ),
    }
    assert all(set(example.split(", ")) <= members for example in examples.values())


def test_plugins_use_a_member_in_the_four_places_the_adr_names_and_parse_none() -> None:
    defs = _schema(MIGRATION_SCHEMA)["$defs"]
    key = [
        *defs["kind"]["enum"],
        *defs["relationType"]["enum"],
        *(
            defs[b["$ref"].rsplit("/", 1)[1]]["properties"]["op"]["const"]
            for b in defs["operation"]["oneOf"]
        ),
    ]
    hits = _git(
        "grep", "-n", "-w", "-F", *(f"-e{value}" for value in key), "--", "plugins"
    ).splitlines()
    uses = {
        (hit.split(":", 1)[0], value)
        for hit in hits
        for value in key
        if f"`{value}`" in hit or f"--kind {value}" in hit
    }

    assert len(key) == 39
    assert len(hits) == 37
    assert {hit.split(":", 1)[0].rsplit(".", 1)[1] for hit in hits} == {"md"}
    assert uses == {
        ("plugins/claude-code/commands/index.md", "upsertRevision"),
        ("plugins/claude-code/commands/index.md", "removeRelation"),
        ("plugins/claude-code/commands/index.md", "deprecateItem"),
        ("plugins/claude-code/commands/propose.md", "architecture"),
    }
