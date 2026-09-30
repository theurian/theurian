"""ADR-0039's populations: the entry points, the derived store and `compat check`.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import inspect
import re
from collections.abc import Mapping
from typing import Final

import pytest
import typer.main
from adr_0037_support import collapsed
from adr_0039_support import (
    ADDITIVE,
    GOVERNED_NAMES,
    REPO_ROOT,
    _function,
    _line,
    _named,
    _scoped,
    _section,
    _spelled,
    _table,
    _trees,
)

from theurian import __protocol_version__
from theurian.application.project_service import resolve_state_hash
from theurian.cli.main import app, compat_check
from theurian.domain.compatibility import (
    CURRENT_PROTOCOL_VERSION,
    CompatibilityDeclaration,
    CompatibilityOutcome,
    resolve_compatibility,
)
from theurian.domain.state import StateInputs

pytestmark = pytest.mark.unit


def _pasted() -> dict[str, collections.Counter[tuple[str, str]]]:
    """The console block's output, per command: ``(src-relative path, line text)``."""
    lines = _section("### What an unknown value meets today")
    block = lines[lines.index("```console") + 1 : lines.index("```")]
    outputs: dict[str, collections.Counter[tuple[str, str]]] = {}
    command = ""
    for line in block:
        if line.startswith("$ "):
            command = line
            outputs[command] = collections.Counter()
        else:
            path, _, text = line.removeprefix("packages/theurian-core/src/theurian/").split(":", 2)
            outputs[command][(path, text.strip())] += 1
    return outputs


#: Attributes that turn a value into a member, or answer whether one would.
_LOOKUP_ATTRIBUTES: Final = frozenset(
    {"_value2member_map_", "_member_map_", "__members__", "__call__", "__new__", "_missing_"}
)


def _lookup(node: ast.AST) -> bool:
    """A value reaching one of the classes other than by a plain call of it."""
    match node:
        case ast.Subscript(value=cls) if _named(cls) in ADDITIVE:
            return True
        case ast.Attribute(value=cls, attr=attr) if _named(cls) in ADDITIVE:
            return attr in _LOOKUP_ATTRIBUTES
        case ast.Compare(ops=ops, comparators=comparators):
            return any(
                isinstance(op, ast.In | ast.NotIn) and _named(right) in ADDITIVE
                for op, right in zip(ops, comparators, strict=True)
            )
        case ast.Call(func=ast.Name(id="getattr" | "map"), args=[cls, *_]):
            return _named(cls) in ADDITIVE
    return False


def _entry_points(
    trees: Mapping[str, ast.Module] | None = None,
) -> dict[str, list[tuple[str, str, ast.AST]]]:
    """``(path, function, node)`` per mechanism, under the key in the module docstring."""
    found: dict[str, list[tuple[str, str, ast.AST]]] = collections.defaultdict(list)
    for path, tree in (_trees() if trees is None else trees).items():
        for node, scope in _scoped(tree):
            if _lookup(node):
                found["lookup"].append((path, scope, node))
            elif isinstance(node, ast.Call) and _named(node.func) in ADDITIVE:
                found["construction"].append((path, scope, node))
            elif (
                isinstance(node, ast.Call)
                and _named(node.func) == "_closed_value"
                and _named(node.args[0]) in ADDITIVE
            ):
                found["_closed_value"].append((path, scope, node))
            elif isinstance(node, ast.arg) and path.startswith("cli/") and node.annotation:
                found["option"] += [
                    (path, scope, name)
                    for name in ast.walk(node.annotation)
                    if isinstance(name, ast.Name) and name.id in ADDITIVE
                ]
            elif isinstance(node, ast.ClassDef) and node.name in ADDITIVE:
                found["class"].append((path, scope, node))
    return found


#: Each construction site the loader's schema check does not stand in front of,
#: against the table row that accounts for it: (row's first cell, a token in it).
_TABLED: Final = {
    ("application/okf_import.py", "_resolve_kind"): (
        "OKF import, a concept's type",
        "application/okf_import.py",
    ),
    ("application/proposal_service.py", "_refuse_operations_outside_the_v1_set"): (
        "Proposal service draft_from_document",
        "OperationKind(raw)",
    ),
    ("infrastructure/sqlite/store.py", "_item_from_row"): (
        "Derived store row decoders",
        "_item_from_row",
    ),
    ("infrastructure/sqlite/store.py", "_revision_from_row"): (
        "Derived store row decoders",
        "_revision_from_row",
    ),
    ("infrastructure/sqlite/store.py", "_relation_from_row"): (
        "Derived store row decoders",
        "_relation_from_row",
    ),
    ("mcp/tools.py", "knowledge_propose_change"): (
        "MCP knowledge.proposeChange and review.generateKnowledgeCandidate",
        "_closed_value",
    ),
    ("mcp/tools.py", "review_generate_knowledge_candidate"): (
        "MCP knowledge.proposeChange and review.generateKnowledgeCandidate",
        "_closed_value",
    ),
    ("cli/propose_commands.py", "propose_draft"): ("theurian propose --kind", "--kind"),
}


_LOADER: Final = "infrastructure/filesystem/migration_loader.py"


def test_the_pasted_commands_are_the_live_entry_point_population() -> None:
    """Keyed on text, not on line numbers: the pasted lines are dated to ``f0e4d754``."""
    found = _entry_points()
    live = [
        collections.Counter(
            (path, _line(path, node))
            for mechanism in mechanisms
            for path, _, node in found[mechanism]
        )
        for mechanisms in (("construction", "class"), ("_closed_value",), ("option",))
    ]

    assert list(_pasted().values()) == live


def test_every_way_of_reaching_a_class_from_a_value_is_recognised() -> None:
    """Driven from a snippet: no live site uses any of these forms today."""
    snippet = ast.parse(
        "def forms(raw, enums):\n"
        "    KnowledgeKind[raw]\n"
        "    enums.RelationType[raw]\n"
        "    RelationType._value2member_map_.get(raw)\n"
        "    OperationKind._member_map_\n"
        "    KnowledgeKind.__members__\n"
        "    KnowledgeKind.__call__(raw)\n"
        "    raw in OperationKind\n"
        "    raw not in KnowledgeKind\n"
        "    getattr(KnowledgeKind, raw)\n"
        "    map(RelationType, raw)\n"
        "    KnowledgeKind.DOMAIN\n"
        "    isinstance(raw, KnowledgeKind)\n"
        "    frozenset(OperationKind)\n"
    )

    found = _entry_points({"snippet.py": snippet})

    assert [ast.unparse(node) for _, _, node in found["lookup"]] == [
        "KnowledgeKind[raw]",
        "enums.RelationType[raw]",
        "RelationType._value2member_map_",
        "OperationKind._member_map_",
        "KnowledgeKind.__members__",
        "KnowledgeKind.__call__",
        "raw in OperationKind",
        "raw not in KnowledgeKind",
        "getattr(KnowledgeKind, raw)",
        "map(RelationType, raw)",
    ]
    assert found["construction"] == []


def test_every_entry_point_the_schema_does_not_guard_has_its_table_row() -> None:
    found = _entry_points()
    loader = [
        scope
        for mechanism in ("construction", "lookup")
        for path, scope, _ in found[mechanism]
        if path == _LOADER
    ]
    reachable = {
        (path, scope)
        for mechanism in ("construction", "lookup", "_closed_value", "option")
        for path, scope, _ in found[mechanism]
        if path != _LOADER
    }
    lines = _section("### What an unknown value meets today")
    table = _table(
        lines[lines.index("The entry points an unknown value can reach refuse it as follows:") :]
    )
    rows = {collapsed(row[0]): collapsed(" ".join(row[:2])) for row in table[1:]}

    assert sorted(loader) == [
        "_parse_operation",
        "_parse_operation",
        "_parse_operation",
        "_parse_upsert",
    ]
    assert reachable == set(_TABLED)
    for label, token in _TABLED.values():
        [cell] = [text for first, text in rows.items() if first.startswith(label)]
        assert token in cell, f"the {label!r} row no longer names {token!r}"


def test_the_wire_enumerated_sets_reach_an_mcp_decoder_outside_the_population() -> None:
    counter = _function("infrastructure/sqlite/store.py", "count_surfaceable_by_status")
    status = _function("mcp/tools.py", "knowledge_status")

    assert "Sensitivity" in {
        _named(node.func) for node in ast.walk(counter) if isinstance(node, ast.Call)
    }
    assert "count_surfaceable_by_status" in {
        _named(node.func) for node in ast.walk(status) if isinstance(node, ast.Call)
    }


def test_the_state_hash_reads_no_core_version_and_no_enum_membership() -> None:
    assert [field.name for field in dataclasses.fields(StateInputs)] == [
        "migrations",
        "content_checksums",
        "schema_version",
        "engine_version",
    ]
    assert inspect.signature(resolve_state_hash).parameters["loaded"].annotation == (
        "LoadedMigrations"
    )


def test_only_cli_modules_load_migrations_while_mcp_opens_the_pointers_database() -> None:
    loaders = {
        path
        for path, tree in _trees().items()
        if path != _LOADER
        and "load_migrations"
        in _spelled(tree)
        | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
    }
    resolve = _function("mcp/tools.py", "_resolve")
    called = {_named(node.func) for node in ast.walk(resolve) if isinstance(node, ast.Call)}

    assert loaders == {"cli/context.py", "cli/migration_pipeline.py", "cli/setup_commands.py"}
    assert {"read_active_state", "state_database_named"} <= called


def test_compat_check_takes_a_declaration_and_cores_own_versions_and_no_project() -> None:
    group = typer.main.get_command(app).commands["compat"]  # type: ignore[attr-defined]
    options = {param.name: param.opts for param in group.commands["check"].params}
    [call] = [
        node
        for node in ast.walk(_function("cli/main.py", "compat_check"))
        if isinstance(node, ast.Call) and _named(node.func) == "resolve_compatibility"
    ]
    parameters = [
        *inspect.signature(resolve_compatibility).parameters,
        *inspect.signature(compat_check).parameters,
    ]
    module = _trees()["domain/compatibility.py"]
    imported = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    read = _spelled(_function("cli/main.py", "compat_check")) | _spelled(module) | imported

    assert list(inspect.signature(resolve_compatibility).parameters) == [
        "declaration",
        "core_version",
        "core_protocol_version",
    ]
    assert set(options) - {"as_json"} == {
        f.name for f in dataclasses.fields(CompatibilityDeclaration)
    }
    assert sorted(opt for opts in options.values() for opt in opts) == sorted(
        [
            "--plugin-version",
            "--core-minimum",
            "--core-maximum-exclusive",
            "--protocol-version",
            "--json",
        ]
    )
    assert [ast.unparse(arg) for arg in call.args[1:]] == [
        "Version.parse_python(__version__)",
        "__protocol_version__",
    ]
    assert __protocol_version__ == CURRENT_PROTOCOL_VERSION == "theurian/v1"
    assert [p for p in parameters if re.search(r"project|path|root|migration|dir", p)] == []
    assert "CompatibilityOutcome" in read, "positive control: the name key reads the module"
    assert read & (GOVERNED_NAMES | {"load_migrations", "resolve_context", "ProjectPaths"}) == set()
    assert [outcome.value for outcome in CompatibilityOutcome] == [
        "compatible",
        "core-missing",
        "core-too-old",
        "core-too-new",
        "protocol-mismatch",
    ]


def test_core_stamps_api_version_in_two_places_both_in_the_proposal_service() -> None:
    stamps = {
        (path, scope, ast.unparse(value))
        for path, tree in _trees().items()
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "apiVersion"
    }

    assert stamps == {
        (
            "application/proposal_service.py",
            "_document_with_minted_identity",
            "MIGRATION_API_VERSION",
        ),
        ("application/proposal_service.py", "_migration_document", "MIGRATION_API_VERSION"),
    }


def test_the_v1_operation_set_is_enumerated_rather_than_derived_from_the_enum() -> None:
    [value] = [
        node.value
        for node in _trees()["application/proposal_service.py"].body
        if isinstance(node, ast.AnnAssign) and _named(node.target) == "V1_OPERATION_KINDS"
    ]

    assert isinstance(value, ast.Call) and _named(value.func) == "frozenset"
    assert isinstance(value.args[0], ast.Set)
    assert {ast.unparse(member).split(".")[0] for member in value.args[0].elts} == {"OperationKind"}


def test_the_tests_the_adr_cites_still_hold_what_it_quotes() -> None:
    adr_0037 = collapsed(
        (REPO_ROOT / "packages/theurian-core/tests/unit/test_adr_0037_claims.py").read_text()
    )
    partition = ast.parse(
        (REPO_ROOT / "packages/theurian-core/tests/unit/test_draft_only_proposals.py").read_bytes()
    )

    assert "a reorder is a diff its reviewer should see" in adr_0037
    assert "assert len(RelationType) == 14" in adr_0037
    assert "assert schema_enum == [member.value for member in RelationType]" in adr_0037
    assert "test_the_v1_operation_set_partitions_operation_kind" in {
        node.name for node in partition.body if isinstance(node, ast.FunctionDef)
    }
