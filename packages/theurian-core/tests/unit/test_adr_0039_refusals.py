"""ADR-0039's measured refusals: what an unknown value meets today, and the freeze.

``test_adr_0039_claims.py`` names the rest of the pin and states its reach.
"""

from __future__ import annotations

import ast
import copy
import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
import typer.main
import yaml
from adr_0037_support import collapsed, revision
from adr_0039_support import (
    MIGRATION_SCHEMA,
    SAMPLE,
    SCHEMAS,
    SECOND,
    _adr,
    _function,
    _named,
    _schema,
    _spelled,
    _trees,
)
from jsonschema import ValidationError

from theurian.application.migration_engine import (
    verify_no_applied_migration_changed,
    verify_no_applied_migration_removed,
)
from theurian.application.okf_import import ImportedConcept, ImportRefusal, _map_concept
from theurian.cli.propose_commands import propose_app
from theurian.domain.enums import (
    KnowledgeKind,
    RelationType,
)
from theurian.domain.errors import (
    MigrationChecksumMismatchError,
    MigrationError,
    MigrationHistoryMissingError,
)
from theurian.domain.migration import LoadedMigrations
from theurian.infrastructure.filesystem.migration_loader import (
    MAX_ECHOED_VALUE,
    load_migrations,
    validate_migration_document,
)
from theurian.infrastructure.sqlite.connection import StateDatabaseUnreadableError
from theurian.infrastructure.sqlite.store import (
    _item_from_row,
    _reading,
    _relation_from_row,
    _revision_from_row,
)
from theurian.mcp.tools import ToolError, _closed_value
from theurian.security import load_yaml_mapping

pytestmark = pytest.mark.unit


def _governed_positions() -> set[tuple[str, str]]:
    """``(definition, property)`` for every place the schema admits a governed value."""
    defs = _schema(MIGRATION_SCHEMA)["$defs"]
    enum_defs = {name for name, body in defs.items() if "enum" in body}
    positions = {("<root>", "apiVersion")}
    for definition, body in defs.items():
        for prop, value in body.get("properties", {}).items():
            if value.get("$ref", "").removeprefix("#/$defs/") in enum_defs or "enum" in value:
                positions.add((definition, prop))
            elif prop == "op" and "const" in value:
                positions.add(("operation", "op"))
    return positions


#: The sample project lacks these two operations, so every refusal case appends them
#: with legal values: the control loads with them, and a planted value is the only
#: difference between the control and the refused copy.
_APPENDED: Final = (
    {
        "op": "removeRelation",
        "sourceItemId": "domain.order-cancellation",
        "relationType": "constrained_by",
        "targetItemId": "architecture.auth-policy",
    },
    {
        "op": "changeSensitivity",
        "itemId": "domain.order-cancellation",
        "sensitivity": "confidential",
        "reason": "Reclassified.",
    },
)


#: One case per governed position (the input grammar), plus the ADR's own
#: `registerSpecification renamed away`: (position, operation, path, planted value).
_CASES: Final = (
    (("opCreateItem", "kind"), "createItem", ("kind",), "requirement"),
    (("revisionMetadata", "kind"), "upsertRevision", ("metadata", "kind"), "requirement"),
    (("opAddRelation", "relationType"), "addRelation", ("relationType",), "traces_to"),
    (("opRemoveRelation", "relationType"), "removeRelation", ("relationType",), "traces_to"),
    (("revisionMetadata", "status"), "upsertRevision", ("metadata", "status"), "archived"),
    (("opCreateItem", "sensitivity"), "createItem", ("sensitivity",), "secret"),
    (("revisionMetadata", "sensitivity"), "upsertRevision", ("metadata", "sensitivity"), "secret"),
    (("opChangeSensitivity", "sensitivity"), "changeSensitivity", ("sensitivity",), "secret"),
    (("opCreateItem", "trustLevel"), "createItem", ("trustLevel",), "verified"),
    (("revisionMetadata", "trustLevel"), "upsertRevision", ("metadata", "trustLevel"), "verified"),
    (("opRegisterSpecification", "status"), "registerSpecification", ("status",), "archived"),
    (("operation", "op"), "addEvidence", ("op",), "addTrace"),
    (("operation", "op"), "registerSpecification", ("op",), "registerSpec"),
)


def _document() -> dict[str, Any]:
    return dict(load_yaml_mapping((SAMPLE / ".theurian" / "migrations" / SECOND).read_text()))


def _plant(document: dict[str, Any], op: str, path: Sequence[str], value: str) -> int:
    """Replace one value in place; the operation's index."""
    index = [operation["op"] for operation in document["operations"]].index(op)
    target = document["operations"][index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return index


def _project(tmp_path: Path, document: Mapping[str, Any]) -> Path:
    root = tmp_path / "project"
    shutil.copytree(SAMPLE, root)
    (root / ".theurian" / "migrations" / SECOND).write_text(
        yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8"
    )
    return root


def _load(root: Path) -> LoadedMigrations:
    return load_migrations(root, root / ".theurian" / "migrations", SCHEMAS)


def _refusal(root: Path) -> MigrationError:
    with pytest.raises(MigrationError) as refused:
        _load(root)
    return refused.value


def test_the_refusal_cases_cover_every_position_the_schema_admits_a_governed_value_in() -> None:
    held_apart = ("<root>", "apiVersion")  # its own test, below: its refusal is exact

    assert {position for position, *_ in _CASES} | {held_apart} == _governed_positions()


@pytest.mark.parametrize(
    ("position", "op", "path", "value"),
    _CASES,
    ids=[f"{position[0]}.{position[1]}-{op}" for position, op, _, _ in _CASES],
)
def test_the_loader_refuses_an_unknown_value_as_a_whole_operation_failing_one_of(
    tmp_path: Path, position: tuple[str, str], op: str, path: tuple[str, ...], value: str
) -> None:
    """Today's undiagnosable refusal (ADR-0039 *Context*, *Negative*), held on purpose.

    When #849 lands a refusal naming the field path and the value, this goes RED
    and ADR-0039's loader row and first *Negative* item are amended with it.
    """
    control = _document()
    control["operations"].extend(copy.deepcopy(_APPENDED))
    planted = copy.deepcopy(control)
    index = _plant(planted, op, path, value)

    _load(_project(tmp_path / "control", control))
    refused = _refusal(_project(tmp_path / "planted", planted))

    message = str(refused)
    prefix = f"{SECOND} is invalid at operations/{index}: does not satisfy 'oneOf' (expected ["
    assert type(refused) is MigrationError
    assert isinstance(refused.__cause__, ValidationError), "refused at an enum constructor"
    assert message.startswith(prefix)
    assert repr(value) in message.split("; the value there is {", 1)[1]


def test_an_unknown_api_version_is_refused_at_the_schema_const_before_an_unknown_member(
    tmp_path: Path,
) -> None:
    document = _document()
    document["apiVersion"] = "theurian.dev/v2"
    _plant(document, "createItem", ("kind",), "requirement")

    refused = _refusal(_project(tmp_path, document))

    assert isinstance(refused.__cause__, ValidationError), "reached the compiled comparison"
    assert str(refused).startswith(f"{SECOND} ")
    assert collapsed(str(refused).removeprefix(f"{SECOND} ")) in _adr()


def test_a_legal_note_truncates_the_unknown_relation_type_out_of_the_echo(tmp_path: Path) -> None:
    note = "x" * 990
    control = _document()
    _plant(control, "addRelation", ("note",), note)
    planted = copy.deepcopy(control)
    _plant(planted, "addRelation", ("relationType",), "traces_to")
    limit = _schema(MIGRATION_SCHEMA)["$defs"]["opAddRelation"]["properties"]["note"]["maxLength"]

    _load(_project(tmp_path / "control", control))
    message = str(_refusal(_project(tmp_path / "planted", planted)))

    assert len(note) < limit == MAX_ECHOED_VALUE == 1_000
    assert message.split("; the value there is ", 1)[1].startswith("{'note': "), "note sorts first"
    assert "traces_to" not in message
    assert len(message) == 1_315


def test_the_proposal_services_validator_refuses_at_the_loaders_seam() -> None:
    validate_migration_document(_document(), SCHEMAS)

    for op, path, value in (
        ("addRelation", ("relationType",), "traces_to"),
        ("addEvidence", ("op",), "addTrace"),
    ):
        document = _document()
        index = _plant(document, op, path, value)
        with pytest.raises(MigrationError) as refused:
            validate_migration_document(document, SCHEMAS)

        assert str(refused.value).startswith(
            f"invalid migration at operations/{index}: does not satisfy 'oneOf'"
        )


def test_the_v1_gate_skips_an_unknown_op_for_the_validator_to_refuse() -> None:
    """Read from source, as the ADR's row is: the skip is the ``continue``."""
    gate = _function("application/proposal_service.py", "_refuse_operations_outside_the_v1_set")
    skips = [
        handler.body
        for node in ast.walk(gate)
        if isinstance(node, ast.Try) and "OperationKind" in _spelled(ast.Module(node.body, []))
        for handler in node.handlers
        if handler.type is not None and _named(handler.type) == "ValueError"
    ]
    wrap = _function("application/proposal_service.py", "_refuse_a_document_the_schema_rejects")
    raised = {
        _named(node.exc.func)
        for node in ast.walk(wrap)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
    }

    assert [[type(statement) for statement in body] for body in skips] == [[ast.Continue]]
    assert raised == {"ProposalError"}


def test_closed_value_names_the_field_and_the_valid_set_but_not_the_value() -> None:
    with pytest.raises(ToolError) as refused:
        _closed_value(KnowledgeKind, "requirement", "kind")

    assert "requirement" not in str(refused.value)
    assert collapsed(str(refused.value)) in _adr()
    assert _closed_value(KnowledgeKind, "domain", "kind") is KnowledgeKind.DOMAIN


def test_propose_kind_is_refused_at_option_parsing_naming_the_value_and_the_valid_set() -> None:
    [option] = [p for p in typer.main.get_command(propose_app).params if p.name == "kind"]
    valid = ", ".join(repr(member.value) for member in KnowledgeKind)

    with pytest.raises(typer.BadParameter) as refused:
        option.type.convert("requirement", option, None)

    assert option.opts == ["--kind"]
    assert str(refused.value) == f"'requirement' is not one of {valid}."


def test_okf_import_refuses_a_concept_of_an_unknown_type_on_its_own(tmp_path: Path) -> None:
    (tmp_path / "decisions").mkdir()
    for name, kind in (("unknown", "specification"), ("known", "domain")):
        (tmp_path / "decisions" / f"{name}.md").write_text(
            f"---\ntype: {kind}\ntitle: A concept\nstatus: stable\n---\n\nBody.\n",
            encoding="utf-8",
        )

    refused = _map_concept(tmp_path, PurePosixPath("decisions/unknown.md"))
    mapped = _map_concept(tmp_path, PurePosixPath("decisions/known.md"))

    assert refused == ImportRefusal(
        kind="concept", key="decisions/unknown.md", literal="unrecognized type: 'specification'"
    )
    assert isinstance(mapped, ImportedConcept)
    assert mapped.kind is KnowledgeKind.DOMAIN


def test_okf_import_drafts_every_relation_verbatim_into_one_document_refused_whole() -> None:
    """Read from source, as the ADR's row is."""
    operation = _function("application/okf_import.py", "_relation_operation")
    draft = _function("application/okf_import.py", "_draft_relations")
    carried = {
        key.value: ast.unparse(value)
        for node in ast.walk(operation)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
    }
    [handler] = [
        handler
        for node in ast.walk(draft)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    ]
    [refusal] = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and _named(node.func) == "ImportRefusal"
    ]
    drafts = [
        node
        for node in ast.walk(draft)
        if isinstance(node, ast.Call) and _named(node.func) == "draft_from_document"
    ]

    assert carried["relationType"] == "entry.type"
    assert {k.arg: ast.unparse(k.value) for k in refusal.keywords}.items() >= {
        ("kind", "KIND_RELATIONS"),
        ("key", "'addRelation'"),
    }
    assert (
        isinstance(handler.body[-1], ast.Return) and ast.unparse(handler.body[-1]) == "return None"
    )
    assert len(drafts) == 1


def _item_row() -> dict[str, object]:
    return {
        "item_id": "domain.example",
        "project_id": "demo",
        "namespace": "domain",
        "kind": "domain",
        "status": "approved",
        "current_revision_id": None,
        "owner": "team",
        "trust_level": "reviewed",
        "sensitivity": "internal",
        "valid_from": "2026-09-30T00:00:00+00:00",
        "valid_to": None,
        "tenant_id": "local",
        "acl_group": "default",
    }


def _relation_row() -> dict[str, object]:
    return {
        "project_id": "demo",
        "source_item_id": "domain.example",
        "relation_type": "constrained_by",
        "target_item_id": "domain.other",
        "created_at": "2026-09-30T00:00:00+00:00",
        "note": None,
    }


def _revision_row() -> dict[str, object]:
    fixture = revision()
    return {
        **_item_row(),
        "revision_id": fixture.revision_id.value,
        "item_id": fixture.item_id.value,
        "migration_id": fixture.migration_id.value,
        "title": fixture.title,
        "body": fixture.body,
        "content_type": str(fixture.content_type),
        "content_sha256": fixture.content_sha256.value,
        "scope_paths": "[]",
        "labels": '["authored-in-theurian"]',
        "author": fixture.author,
        "created_at": "2026-09-30T00:00:00+00:00",
        "source_commit": None,
        "structured": None,
    }


#: Each decoder the ADR's store row names: (decode, a valid row, column, planted, enum).
_DECODERS: Final[
    dict[str, tuple[Callable[[Any], object], Callable[[], dict[str, object]], str, str, type]]
] = {
    "item": (_item_from_row, _item_row, "kind", "requirement", KnowledgeKind),
    "revision": (
        lambda row: _revision_from_row(row, ()),
        _revision_row,
        "kind",
        "requirement",
        KnowledgeKind,
    ),
    "relation": (_relation_from_row, _relation_row, "relation_type", "traces_to", RelationType),
}


@pytest.mark.parametrize("decoder", _DECODERS)
def test_a_store_row_naming_an_unknown_member_reads_as_an_unreadable_database(
    decoder: str,
) -> None:
    decode, valid, column, value, cls = _DECODERS[decoder]

    with _reading():
        decode(valid())
    with pytest.raises(StateDatabaseUnreadableError) as refused, _reading():
        decode({**valid(), column: value})

    assert value not in str(refused.value)
    assert collapsed(str(refused.value).split(" A state database", 1)[0]) in _adr()
    assert "delete `.theurian/state/` and run `theurian migrate apply`" in str(refused.value)
    assert isinstance(refused.value.__cause__, ValueError)
    assert str(refused.value.__cause__) == f"{value!r} is not a valid {cls.__name__}"


def test_every_store_read_decodes_its_row_inside_the_reading_guard() -> None:
    tree = _trees()["infrastructure/sqlite/store.py"]
    decoders = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.endswith("_from_row")
    }
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With) and "_reading" in _spelled(node.items[0].context_expr):
            guarded |= {id(inner) for inner in ast.walk(node)}
        elif isinstance(node, ast.Call) and _named(node.func) in {"_read_one", "_read_all"}:
            guarded |= {id(inner) for arg in node.args for inner in ast.walk(arg)}
        elif isinstance(node, ast.FunctionDef) and node.name in decoders:
            guarded |= {id(inner) for inner in ast.walk(node)}
    references = [
        node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in decoders
    ]
    readers = {
        name: _function("infrastructure/sqlite/store.py", name)
        for name in ("_read_one", "_read_all")
    }

    assert {"_item_from_row", "_revision_from_row", "_relation_from_row"} <= {
        node.id for node in references
    }, "positive control: the key no longer sees the three decoders the ADR names"
    assert [node.lineno for node in references if id(node) not in guarded] == []
    for name, reader in readers.items():
        [block] = [node for node in ast.walk(reader) if isinstance(node, ast.With)]
        assert "_reading" in _spelled(block.items[0].context_expr), name
        assert "mapper" in _spelled(ast.Module(block.body, [])), name


def _history(root: Path) -> dict[Any, str]:
    return {m.migration_id: m.checksum.value for m in _load(root).migration_set}


def test_an_applied_migration_edited_in_place_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path, _document())
    recorded = _history(root)
    verify_no_applied_migration_changed(recorded, _load(root).migration_set)
    migration = root / ".theurian" / "migrations" / SECOND

    migration.write_text(migration.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")

    with pytest.raises(MigrationChecksumMismatchError):
        verify_no_applied_migration_changed(recorded, _load(root).migration_set)


def test_an_applied_migration_deleted_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path, _document())
    applied = tuple(_history(root).items())
    verify_no_applied_migration_removed(applied, _load(root).migration_set)

    (root / ".theurian" / "migrations" / SECOND).unlink()

    with pytest.raises(MigrationHistoryMissingError):
        verify_no_applied_migration_removed(applied, _load(root).migration_set)
