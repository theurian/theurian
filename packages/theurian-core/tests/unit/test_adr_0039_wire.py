"""ADR-0039's wire facts: what the published schemas close, and what travels open.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import re
from datetime import UTC, datetime
from typing import Final

import pytest
from adr_0037_support import collapsed, revision
from adr_0039_support import (
    GOVERNED_NAMES,
    MIGRATION_SCHEMA,
    REPO_ROOT,
    SCHEMAS,
    _git,
    _nodes,
    _schema,
    _scoped,
    _spelled,
    _trees,
    occurrences,
)

from theurian.application.okf_import import ImportedConcept
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.review import KnowledgeCandidate
from theurian.mcp.results import result_payload

pytestmark = pytest.mark.unit


_RESULT: Final = "schemas/knowledge/retrieval-result.schema.json"
_CONFIG: Final = "schemas/config/project-config.schema.json"
_STATUS: Final = "schemas/mcp/knowledge-status-response.schema.json"


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


def test_the_wire_closes_status_trust_level_and_sensitivity_in_two_tools() -> None:
    """Retrieval results reach the wire only by ``$ref``; ``itemsByStatus`` is a second tool."""
    wire = _wire()
    closing = {
        (path, pointer)
        for path in wire
        for kind, pointer, _ in occurrences(_schema(REPO_ROOT / path))
        if kind in ("enumerated", "key-closure")
    }
    record_kind = "#/properties/records/items/properties/kind"
    review_search = _schema(SCHEMAS / "mcp" / "review-search-response.schema.json")

    assert wire[_RESULT] == "schemas/mcp/knowledge-search-response.schema.json"
    assert _RESULT not in _wire(follow_refs=False), "retrieval-result is on the wire only by $ref"
    assert closing == {
        (_RESULT, "#/properties/status"),
        (_RESULT, "#/properties/trustLevel"),
        (_RESULT, "#/properties/sensitivity"),
        (_STATUS, "#/properties/itemsByStatus"),
    }
    assert _CONFIG not in wire, "includeStatuses is a published configuration schema, not the wire"
    assert dict(_nodes(review_search))[record_kind]["enum"] == [
        "pull-request",
        "review-submission",
        "review-thread",
    ]
    assert [o for o in occurrences(review_search) if o[1] == record_kind] == []


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
