"""ADR-0039's populations: the entry points, the derived store and `compat check`.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import dataclasses
import inspect
import re
import textwrap
from collections.abc import Callable, Mapping
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
from mcp.server import MCPServer
from mcp.types import CallToolResult

from theurian import __protocol_version__
from theurian.application.project_service import resolve_state_hash
from theurian.cli.main import app, compat_check
from theurian.domain.compatibility import (
    CURRENT_PROTOCOL_VERSION,
    CompatibilityDeclaration,
    CompatibilityOutcome,
    resolve_compatibility,
)
from theurian.domain.enums import KnowledgeKind, RelationType
from theurian.domain.migration import OperationKind, RegisterSpecification
from theurian.domain.state import StateInputs

pytestmark = pytest.mark.unit

ADDITIVE_CLASSES: Final = (KnowledgeKind, RelationType, OperationKind)


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


#: Each additive class's member names: an attribute naming one is a static member access.
_MEMBERS_OF: Final = {cls.__name__: frozenset(cls.__members__) for cls in ADDITIVE_CLASSES}


def _annotations(tree: ast.AST) -> tuple[set[int], set[int]]:
    """Ids of every node inside an annotation, and of those inside a parameter's."""
    everywhere: set[int] = set()
    parameters: set[int] = set()
    for node in ast.walk(tree):
        roots: list[ast.AST] = []
        if isinstance(node, ast.arg) and node.annotation is not None:
            parameters |= {id(inner) for inner in ast.walk(node.annotation)}
            roots.append(node.annotation)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            roots.append(node.returns)
        if isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        everywhere |= {id(inner) for root in roots for inner in ast.walk(root)}
    return everywhere, parameters


def _form(node: ast.AST, parent: ast.AST, name: str) -> str:
    """What a value-position reference does with the class, by the node that holds it."""
    match parent:
        case ast.Call(func=func) if func is node:
            return "construction"
        case ast.Call(func=func, args=[first, *_]) if first is node and _named(func) == (
            "_closed_value"
        ):
            return "_closed_value"
        case ast.Call(func=ast.Name(id="isinstance"), args=[_, second]) if second is node:
            return "isinstance"
        case ast.Attribute(value=value, attr=attr) if value is node and attr in _MEMBERS_OF[name]:
            return "member"
    return "unclassified"


#: ``(path, function, class, form, node)``.
Reference = tuple[str, str, str, str, ast.AST]


def _references(trees: Mapping[str, ast.Module] | None = None) -> list[Reference]:
    """Every reference to an additive class, under the key in the claims module's docstring."""
    found: list[Reference] = []
    for path, tree in (_trees() if trees is None else trees).items():
        parents = {
            id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        annotation, parameter = _annotations(tree)
        for node, scope in _scoped(tree):
            name = _named(node) if isinstance(node, ast.Name | ast.Attribute) else None
            if name not in ADDITIVE:
                continue
            if id(node) in parameter and path.startswith("cli/"):
                form = "option"
            elif id(node) in annotation:
                continue
            else:
                form = _form(node, parents[id(node)], name)
            found.append((path, scope, name, form, node))
    return found


def _class_statements() -> list[tuple[str, ast.ClassDef]]:
    return [
        (path, node)
        for path, tree in _trees().items()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name in ADDITIVE
    ]


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
    references = _references()
    live = [
        collections.Counter(
            [
                *(
                    (path, _line(path, node))
                    for path, _, _, form, node in references
                    if form == "construction"
                ),
                *((path, _line(path, node)) for path, node in _class_statements()),
            ]
        ),
        *(
            collections.Counter(
                (path, _line(path, node)) for path, _, _, f, node in references if f == form
            )
            for form in ("_closed_value", "option")
        ),
    ]

    assert list(_pasted().values()) == live


def _held(references: list[Reference]) -> collections.Counter[tuple[str, str, str]]:
    """The entry-point tripwire: every reference by file, function and form, bar members.

    A member access is classified and not held, since holding it would turn this pin
    RED on ordinary code in any lane. A lookup built from member accesses turns a
    string into a member all the same, so the key is incomplete by construction:
    ``test_the_forms_outside_the_key_turn_a_string_into_a_member_unheld``.
    """
    return collections.Counter(
        (path, scope, form) for path, scope, _, form, _ in references if form != "member"
    )


#: Every reference to an additive class under ``src/theurian`` but a member access, by
#: form: held exact, so a new one of any form -- iteration, a comprehension or
#: ``next(...)`` over the class, ``.parse`` -- goes RED for a person to classify.
REFERENCES: Final = {
    ("application/okf_import.py", "_resolve_kind", "construction"): 1,
    ("application/proposal_service.py", "_refuse_operations_outside_the_v1_set", "construction"): 1,
    ("cli/propose_commands.py", "propose_draft", "option"): 1,
    ("infrastructure/filesystem/migration_loader.py", "_parse_operation", "construction"): 3,
    ("infrastructure/filesystem/migration_loader.py", "_parse_upsert", "construction"): 1,
    ("infrastructure/sqlite/store.py", "_item_from_row", "construction"): 1,
    ("infrastructure/sqlite/store.py", "_relation_from_row", "construction"): 1,
    ("infrastructure/sqlite/store.py", "_revision_from_row", "construction"): 1,
    ("mcp/tools.py", "knowledge_propose_change", "_closed_value"): 1,
    ("mcp/tools.py", "review_generate_knowledge_candidate", "_closed_value"): 1,
}


def test_every_reference_to_an_additive_class_is_classified_and_held() -> None:
    references = _references()

    assert [
        (path, scope, ast.unparse(node))
        for path, scope, _, form, node in references
        if form == "unclassified"
    ] == []
    assert _held(references) == REFERENCES


def test_each_form_of_reference_is_classified_and_annotations_are_not_references() -> None:
    """Driven from snippets: the live tree holds no ``isinstance`` and no unclassified form."""
    snippet = ast.parse(
        "from theurian.domain.enums import KnowledgeKind\n"
        "def forms(raw, enums, kind: KnowledgeKind) -> KnowledgeKind:\n"
        "    held: KnowledgeKind = KnowledgeKind(raw)\n"
        "    enums.RelationType(raw)\n"
        "    _closed_value(KnowledgeKind, raw, 'kind')\n"
        "    isinstance(raw, KnowledgeKind)\n"
        "    KnowledgeKind.DOMAIN\n"
        "    KnowledgeKind[raw]\n"
        "    RelationType._value2member_map_.get(raw)\n"
        "    raw in OperationKind\n"
        "    KnowledgeKind.__call__(raw)\n"
        "    {member.value: member for member in KnowledgeKind}\n"
        "    next(member for member in KnowledgeKind if member == raw)\n"
        "    KnowledgeKind.parse(raw)\n"
        "    getattr(KnowledgeKind, raw)\n"
    )
    option = ast.parse("def command(kind: KnowledgeKind | None = None) -> KnowledgeKind: ...\n")

    found = _references({"snippet.py": snippet, "cli/snippet.py": option})

    assert sorted(
        (getattr(node, "lineno", 0), form)
        for path, _, _, form, node in found
        if path == "snippet.py"
    ) == [
        (3, "construction"),
        (4, "construction"),
        (5, "_closed_value"),
        (6, "isinstance"),
        (7, "member"),
        *((line, "unclassified") for line in range(8, 16)),
    ]
    assert [(path, form) for path, _, _, form, _ in found if path == "cli/snippet.py"] == [
        ("cli/snippet.py", "option")
    ]
    assert _held(found) == {
        ("snippet.py", "forms", "construction"): 2,
        ("snippet.py", "forms", "_closed_value"): 1,
        ("snippet.py", "forms", "isinstance"): 1,
        ("snippet.py", "forms", "unclassified"): 8,
        ("cli/snippet.py", "command", "option"): 1,
    }, "a member access is classified and not held; an iteration is held"


def _by_dict(raw: str) -> object:
    return {"domain": KnowledgeKind.DOMAIN, "decision": KnowledgeKind.DECISION}[raw]


def _by_match(raw: str) -> object:
    match raw:
        case RelationType.IMPLEMENTS:
            return RelationType.IMPLEMENTS
    return None


def _by_next(raw: str) -> object:
    return next(m for m in (OperationKind.ADD_RELATION, OperationKind.ADD_ALIAS) if m == raw)


def _by_value_map(raw: str) -> object:
    return RelationType.IMPLEMENTS._value2member_map_[raw]  # type: ignore[attr-defined]


def _by_class(raw: str) -> object:
    return KnowledgeKind.DOMAIN.__class__(raw)


def _by_type(raw: str) -> object:
    return type(OperationKind.ADD_ALIAS)(raw)


def _by_annotation(kind: KnowledgeKind) -> str:
    return repr(kind)


#: Each lookup the claims module's Reach names outside the key, against (raw, member).
#: The first four are the ones PR #852's round 3 reproduced.
_OUTSIDE: Final = {
    _by_dict: ("domain", KnowledgeKind.DOMAIN),
    _by_match: ("implements", RelationType.IMPLEMENTS),
    _by_next: ("addAlias", OperationKind.ADD_ALIAS),
    _by_value_map: ("implements", RelationType.IMPLEMENTS),
    _by_class: ("domain", KnowledgeKind.DOMAIN),
    _by_type: ("addRelation", OperationKind.ADD_RELATION),
}


def _parsed(function: Callable[..., object]) -> ast.Module:
    return ast.parse(textwrap.dedent(inspect.getsource(function)))


def test_the_forms_outside_the_key_turn_a_string_into_a_member_unheld() -> None:
    """Each turns a string into a member while the scan holds none of them.

    RED when the key is widened to hold one; the claims module's Reach and ADR-0039's
    *Context* then move with it.
    """
    lookups = _references({f"mcp/{f.__name__}.py": _parsed(f) for f in _OUTSIDE})
    server = MCPServer("outside-the-key")
    server.tool()(_by_annotation)

    converted = asyncio.run(server.call_tool("_by_annotation", {"kind": "domain"}))

    assert [f(raw) is member for f, (raw, member) in _OUTSIDE.items()] == [True] * len(_OUTSIDE)
    assert {path for path, *_ in lookups} == {f"mcp/{f.__name__}.py" for f in _OUTSIDE}
    assert _held(lookups) == {}, "a member-built lookup is held: move the Reach with the key"
    assert isinstance(converted, CallToolResult)
    assert converted.structured_content == {"result": repr(KnowledgeKind.DOMAIN)}
    assert _references({"mcp/handler.py": _parsed(_by_annotation)}) == []
    assert [form for *_, form, _ in _references({"cli/handler.py": _parsed(_by_annotation)})] == [
        "option"
    ], "positive control: the same annotation under cli/ is held"


def test_the_additive_classes_define_their_members_and_nothing_else() -> None:
    """A method on one -- a ``parse`` classmethod, say -- is an entry point by another name."""
    statements = _class_statements()

    assert sorted(node.name for _, node in statements) == sorted(ADDITIVE)
    for path, node in statements:
        body = node.body[1:] if isinstance(node.body[0], ast.Expr) else node.body

        assert (node.decorator_list, [ast.unparse(base) for base in node.bases]) == (
            [],
            ["StrEnum"],
        ), path
        assert [
            ast.unparse(statement)
            for statement in body
            if not (
                isinstance(statement, ast.Assign)
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Constant)
            )
        ] == [], f"{node.name} defines more than its members"


def test_every_entry_point_the_schema_does_not_guard_has_its_table_row() -> None:
    references = _references()
    loader = [
        scope for path, scope, _, form, _ in references if path == _LOADER and form != "member"
    ]
    reachable = {
        (path, scope)
        for path, scope, _, form, _ in references
        if path != _LOADER and form in ("construction", "_closed_value", "option")
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


_STORE: Final = "infrastructure/sqlite/store.py"


def _decoded_by(method: str) -> set[str]:
    """The ``*_from_row`` decoders a ``SqliteCanonicalStore`` method reaches.

    Key: the decoders its body names, following ``self.<method>(...)`` into the
    class's own methods; a decoder reached through another object is outside it.
    """
    [store] = [
        node
        for node in _trees()[_STORE].body
        if isinstance(node, ast.ClassDef) and node.name == "SqliteCanonicalStore"
    ]
    methods = {node.name: node for node in store.body if isinstance(node, ast.FunctionDef)}
    seen: set[str] = set()
    pending = [method]
    while pending:
        seen.add(current := pending.pop())
        for node in ast.walk(methods[current]):
            match node:
                case ast.Call(func=ast.Attribute(value=ast.Name(id="self"), attr=called)) if (
                    called in methods and called not in seen
                ):
                    pending.append(called)
    return {
        name
        for current in seen
        for name in _spelled(methods[current])
        if name.endswith("_from_row")
    }


def test_knowledge_get_decodes_its_item_and_relations_before_the_gate_the_revision_after() -> None:
    """#853's two faces: a row the Core cannot decode refuses before the gate runs."""
    handler = _function("mcp/tools.py", "knowledge_get")
    calls = sorted(
        (node for node in ast.walk(handler) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    order = [_named(node.func) for node in calls]
    first = {name: order.index(name) for name in set(order) - {None}}
    reads = [
        _named(node.func)
        for node in calls
        if isinstance(node.func, ast.Attribute) and _named(node.func.value) == "store"
    ]

    assert reads[0] == "get_item_metadata"
    assert "_item_from_row" in _decoded_by("get_item_metadata")
    assert first["get_item_metadata"] < first["may_surface"] < first["current_revision"]
    assert "_revision_from_row" in _decoded_by("current_revision")
    assert first["list_relations"] < first["_relation_is_visible"]
    assert "_relation_from_row" in _decoded_by("list_relations")


def _first_call(function: ast.AST, name: str) -> int:
    """The source position of a function's first call of ``name``, as an ordinal."""
    calls = sorted(
        (node for node in ast.walk(function) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return [_named(node.func) for node in calls].index(name)


def test_three_more_registered_gate_sites_decode_a_row_before_they_judge_it() -> None:
    """#853's faces beyond ``knowledge.get``, keyed on the gate register decision 6 uses.

    Key: the site is in ``STATUS_GATE_CALL_SITES``, and its first call of a store
    read comes before its first ``may_surface`` call, by source position. A gate
    site not named here is outside the pin, not cleared by it.
    """
    register = REPO_ROOT / "packages/theurian-core/tests/unit/test_gate_call_sites.py"
    [status_sites] = [
        ast.literal_eval(node.value)
        for node in ast.parse(register.read_bytes()).body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "STATUS_GATE_CALL_SITES"
    ]
    neighbour = _function("mcp/tools.py", "_relation_is_visible")
    write_tools = _function("mcp/tools.py", "current_revision")
    [visibility] = [
        node
        for node in _trees()["application/visibility.py"].body
        if isinstance(node, ast.ClassDef) and node.name == "CanonicalVisibility"
    ]
    methods = {node.name: node for node in visibility.body if isinstance(node, ast.FunctionDef)}

    assert {
        ("mcp/tools.py", "_relation_is_visible"),
        ("mcp/tools.py", "register._draft_only_proposals.current_revision"),
        ("application/visibility.py", "CanonicalVisibility._may_surface"),
    } <= status_sites
    assert _first_call(neighbour, "get_item_exact_metadata") < _first_call(neighbour, "may_surface")
    assert "_item_from_row" in _decoded_by("get_item_exact_metadata")
    assert _first_call(write_tools, "get_item_metadata") < _first_call(write_tools, "may_surface")
    assert _first_call(methods["_may_surface"], "item") < _first_call(
        methods["_may_surface"], "may_surface"
    )
    assert "_lookup" in {
        _named(n.func) for n in ast.walk(methods["item"]) if isinstance(n, ast.Call)
    }
    assert "get_item_metadata" in {
        _named(n.func) for n in ast.walk(methods["_lookup"]) if isinstance(n, ast.Call)
    }


def test_the_engine_version_is_written_at_create_and_never_selected() -> None:
    """ADR-0007's invalidation holds on the build path only: nothing at open reads the engine.

    Key: a string literal holding an upper-case ``SELECT ... FROM`` or ``INSERT INTO``;
    SQL assembled from fragments at run time is outside it.
    """
    statements = [
        (path, node.value)
        for path, tree in _trees().items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\bSELECT\b.*\bFROM\b|\bINSERT INTO\b", node.value, re.DOTALL)
    ]

    assert [text for _, text in statements if "schema_metadata" in text and "SELECT" in text] == [
        "SELECT schema_version FROM schema_metadata WHERE id = 1"
    ], "positive control: the one read at open"
    assert [path for path, text in statements if "INSERT INTO schema_metadata" in text] == [
        "infrastructure/sqlite/connection.py"
    ]
    assert [text for _, text in statements if "INSERT INTO schema_metadata" in text] == [
        "INSERT INTO schema_metadata (id, schema_version, engine_version, state_hash, created_at) "
        "VALUES (1, ?, ?, ?, ?)"
    ]
    assert [
        path for path, text in statements if "SELECT" in text and "engine_version" in text
    ] == []


def test_the_readers_of_the_canonical_state_are_the_two_gate_registers() -> None:
    """Decision 6 names these registers as who must observe no difference."""
    register = REPO_ROOT / "packages/theurian-core/tests/unit/test_gate_call_sites.py"
    sites = {
        target.id: ast.literal_eval(node.value)
        for node in ast.parse(register.read_bytes()).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"STATUS_GATE_CALL_SITES", "DISCLOSURE_GATE_CALL_SITES"}
    }

    assert sites.keys() == {"STATUS_GATE_CALL_SITES", "DISCLOSURE_GATE_CALL_SITES"}
    assert {
        ("application/index_builder.py", "IndexBuilder._build"),
        ("application/migration_engine.py", "revisions_to_purge"),
        ("application/okf_export.py", "OkfExporter._walk"),
    } <= sites["STATUS_GATE_CALL_SITES"] & sites["DISCLOSURE_GATE_CALL_SITES"]


def test_the_loader_keeps_reading_a_register_specification_spec_id_and_status() -> None:
    """Decision 4 keeps the retiring set's read-side parse; #841 may re-type it."""
    [arm] = [
        case
        for case in ast.walk(_function(_LOADER, "_parse_operation"))
        if isinstance(case, ast.match_case)
        and isinstance(case.pattern, ast.MatchValue)
        and ast.literal_eval(case.pattern.value) == "registerSpecification"
    ]
    built = {
        _named(call.func): ast.unparse(call.args[0])
        for call in ast.walk(arm)
        if isinstance(call, ast.Call) and call.args
    }
    fields = {field.name: field.type for field in dataclasses.fields(RegisterSpecification)}

    assert built["SpecId"] == "payload['specId']"
    assert built["SpecificationStatus"] == "payload.get('status', 'active')"
    assert (fields["spec_id"], fields["status"]) == ("SpecId", "SpecificationStatus")


def test_resolve_compatibility_takes_a_declaration_and_cores_own_versions() -> None:
    """The ``compat-signature`` fragment: no parameter names a project, path or migration."""
    parameters = [
        *inspect.signature(resolve_compatibility).parameters,
        *inspect.signature(compat_check).parameters,
    ]

    assert list(inspect.signature(resolve_compatibility).parameters) == [
        "declaration",
        "core_version",
        "core_protocol_version",
    ]
    assert [p for p in parameters if re.search(r"project|path|root|migration|dir", p)] == []


def test_compat_checks_options_are_the_declaration_and_json() -> None:
    """The ``compat-options`` fragment."""
    group = typer.main.get_command(app).commands["compat"]  # type: ignore[attr-defined]
    options = {param.name: param.opts for param in group.commands["check"].params}

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


def test_compat_check_compares_cores_own_protocol_and_reads_no_project_migration_or_enum() -> None:
    """The ``compat-protocol`` fragment."""
    [call] = [
        node
        for node in ast.walk(_function("cli/main.py", "compat_check"))
        if isinstance(node, ast.Call) and _named(node.func) == "resolve_compatibility"
    ]
    module = _trees()["domain/compatibility.py"]
    imported = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    read = _spelled(_function("cli/main.py", "compat_check")) | _spelled(module) | imported

    assert [ast.unparse(arg) for arg in call.args[1:]] == [
        "Version.parse_python(__version__)",
        "__protocol_version__",
    ]
    assert __protocol_version__ == CURRENT_PROTOCOL_VERSION == "theurian/v1"
    assert "CompatibilityOutcome" in read, "positive control: the name key reads the module"
    assert read & (GOVERNED_NAMES | {"load_migrations", "resolve_context", "ProjectPaths"}) == set()


def test_compat_check_decides_one_of_five_outcomes() -> None:
    """The ``compat-outcomes`` fragment."""
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
