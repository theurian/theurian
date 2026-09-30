"""ADR-0039's repository facts, held from both sides.

ADR-0039 decides how the migration format's closed sets change and ships no
code, so every sentence it states about the codebase was measured once, at
``f0e4d754``. Each claim is held twice: a fact half recomputed from live source,
schema or behaviour, RED when the tree moves; and a prose half -- a table or list
parsed out of the ADR, or a fragment in :data:`ADR_STATES` -- RED when the record
drifts.

**Reach.** Each scan's key, beside the claim it holds:

- *The closed sets* are every ``enum`` and ``const`` node of
  ``schemas/migrations/migration.schema.json``, keyed by JSON pointer.
- *A governed position* is a ``$defs`` property whose schema is a ``$ref`` to one
  of the six enum ``$defs``, an inline ``enum``, the ``op`` discriminator, or the
  root ``apiVersion``.
- *An entry point* is, over every ``.py`` under ``src/theurian``: a call of
  ``KnowledgeKind``/``RelationType``/``OperationKind`` by bare name or as an
  attribute; a ``_closed_value`` call whose first argument names one of them; or
  a ``cli/`` parameter whose annotation names one. An aliased import, a class
  held in a variable, or ``getattr`` is outside it.
- *Enumerates* is the ADR's key: an ``enum`` all of whose non-null members are
  members of one governed set, over every schema under ``schemas/`` except the
  migration schema. A superset enum is not counted by it, which is why the
  *overlap* population is pinned exactly: any growth there goes RED for a human
  to classify.
- *The wire* is ``schemas/mcp/*.json`` plus every file a ``$ref`` reaches from
  them, an absolute ``$ref`` resolved by the ``$id`` it names.
- *Published to a client* is a dict-literal key ``relationType`` or
  ``operations`` under ``mcp/``. A key built at run time, or a payload assembled
  by ``dict(...)`` or subscript assignment, is outside it.
- *Loads migrations* is a module spelling ``load_migrations``. A call reaching
  the loader through another module's function is outside it.
- *A gate's reads* are the governed class names in a module-level function of
  ``domain/enums.py``, annotations included, followed one module-level constant
  deep.
- *Decoded inside the guard* is every reference to a ``store.py`` ``*_from_row``
  function, outside such a function's own body, lying inside a
  ``with _reading()`` block or an argument of ``_read_one``/``_read_all``.

**Not pinned.** The ``file:line`` citations are the ADR's dated measurement at
``f0e4d754``; what each line holds is pinned by symbol, never by number.

The Markdown readers are this module's own rather than ``adr_0038_support``'s:
that module's ``_parsed`` refuses to run while any module outside
``test_adr_0038_*.py`` imports it at column 0.
"""

from __future__ import annotations

import ast
import collections
import copy
import dataclasses
import functools
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
import typer.main
import yaml
from adr_0037_support import collapsed, revision
from jsonschema import ValidationError

from theurian import __protocol_version__
from theurian.application.migration_engine import (
    verify_no_applied_migration_changed,
    verify_no_applied_migration_removed,
)
from theurian.application.okf_import import ImportedConcept, ImportRefusal, _map_concept
from theurian.application.project_service import resolve_state_hash
from theurian.cli.main import app, compat_check
from theurian.cli.propose_commands import propose_app
from theurian.domain.compatibility import (
    CURRENT_PROTOCOL_VERSION,
    CompatibilityDeclaration,
    CompatibilityOutcome,
    resolve_compatibility,
)
from theurian.domain.enums import (
    SURFACEABLE_STATUSES,
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TrustLevel,
)
from theurian.domain.errors import (
    MigrationChecksumMismatchError,
    MigrationError,
    MigrationHistoryMissingError,
)
from theurian.domain.migration import MIGRATION_ENGINE_VERSION, LoadedMigrations, OperationKind
from theurian.domain.review import KnowledgeCandidate
from theurian.domain.state import StateInputs, compute_state_hash
from theurian.infrastructure.filesystem.migration_loader import (
    MAX_ECHOED_VALUE,
    load_migrations,
    validate_migration_document,
)
from theurian.infrastructure.sqlite import schema as sqlite_schema
from theurian.infrastructure.sqlite.connection import StateDatabaseUnreadableError
from theurian.infrastructure.sqlite.store import (
    _item_from_row,
    _reading,
    _relation_from_row,
    _revision_from_row,
)
from theurian.mcp.results import result_payload
from theurian.mcp.tools import ToolError, _closed_value
from theurian.security import load_yaml_mapping

pytestmark = pytest.mark.unit

#: ``parents[4]``: ``unit`` -> ``tests`` -> ``theurian-core`` -> ``packages`` -> root.
REPO_ROOT: Final = Path(__file__).resolve().parents[4]
SRC: Final = REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian"
SCHEMAS: Final = REPO_ROOT / "schemas"
MIGRATION_SCHEMA: Final = SCHEMAS / "migrations" / "migration.schema.json"
ADR_DIR: Final = REPO_ROOT / "docs" / "adr"
ADR: Final = ADR_DIR / "0039-closed-set-extension-compatibility.md"
ADR_0005: Final = ADR_DIR / "0005-yaml-knowledge-migrations.md"
ADR_0038: Final = ADR_DIR / "0038-specification-folds-into-a-knowledge-kind.md"
ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"
SAMPLE: Final = REPO_ROOT / "examples" / "sample-project"
SECOND: Final = "01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml"
THIS_MODULE: Final = "packages/theurian-core/tests/unit/test_adr_0039_claims.py"
ADR_0039_LINK: Final = "[ADR-0039](0039-closed-set-extension-compatibility.md)"

GOVERNED: Final = (
    KnowledgeKind,
    RelationType,
    OperationKind,
    KnowledgeStatus,
    Sensitivity,
    TrustLevel,
    SpecificationStatus,
)
GOVERNED_NAMES: Final = frozenset(cls.__name__ for cls in GOVERNED)
ADDITIVE: Final = frozenset({"KnowledgeKind", "RelationType", "OperationKind"})


# -- Readers ------------------------------------------------------------------


def _adr() -> str:
    return collapsed(ADR.read_text(encoding="utf-8"))


def _section(heading: str, path: Path = ADR) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert heading in lines, f"{path.name} has no heading {heading!r}; this read anchors on it"
    start = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    ends = (at for at in range(start + 1, len(lines)) if re.match(rf"#{{1,{level}}} ", lines[at]))
    return lines[start + 1 : next(ends, len(lines))]


def _table(lines: Sequence[str]) -> list[tuple[str, ...]]:
    """Header and data rows of the first Markdown table in ``lines``."""
    rows: list[tuple[str, ...]] = []
    for line in lines:
        if line.strip().startswith("|"):
            rows.append(tuple(cell.strip() for cell in line.strip().strip("|").split("|")))
        elif rows:
            break
    return [row for row in rows if not set("".join(row)) <= set(":- ")]


def _numbered(lines: Sequence[str]) -> dict[int, str]:
    """The first numbered list in ``lines``, each item's wrapped lines rejoined."""
    items: dict[int, list[str]] = {}
    current: int | None = None
    for line in lines:
        start = re.match(r"(\d+)\. (.*)", line)
        if start:
            current = int(start.group(1))
            items[current] = [start.group(2)]
        elif current is not None and line.strip():
            items[current].append(line)
        elif current is not None:
            break
    return {number: collapsed(" ".join(parts)) for number, parts in items.items()}


def _blockquotes(path: Path) -> list[str]:
    """Each run of ``>`` lines, the markers stripped and the run collapsed."""
    runs: list[list[str]] = [[]]
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            runs[-1].append(line.removeprefix(">"))
        elif runs[-1]:
            runs.append([])
    return [collapsed(" ".join(run)) for run in runs if run]


def _git(*arguments: str) -> str:
    git = shutil.which("git")
    assert git is not None, "git is not on PATH, so the committed tree cannot be read"
    # A hook exports GIT_DIR / GIT_INDEX_FILE, which would answer for another tree.
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    completed = subprocess.run(  # noqa: S603 - fixed argv, no caller input
        [git, "-c", f"safe.directory={REPO_ROOT}", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        env=environment,
        check=False,
        timeout=30,
    )
    assert completed.returncode in {0, 1}, completed.stderr.decode("utf-8", "replace")
    return completed.stdout.decode("utf-8")


@functools.cache
def _trees() -> dict[str, ast.Module]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_bytes(), filename=str(path))
        for path in sorted(SRC.rglob("*.py"))
    }


@functools.cache
def _source_lines(relative: str) -> tuple[str, ...]:
    return tuple((SRC / relative).read_text(encoding="utf-8").splitlines())


def _line(relative: str, node: ast.AST) -> str:
    return _source_lines(relative)[getattr(node, "lineno", 0) - 1].strip()


def _scoped(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """Every node with the name of the innermost function holding it."""

    def walk(node: ast.AST, scope: str) -> Iterator[tuple[ast.AST, str]]:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else scope
            )
            yield child, inner
            yield from walk(child, inner)

    return walk(tree, "<module>")


def _named(node: ast.AST) -> str | None:
    match node:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return None


def _function(relative: str, name: str) -> ast.FunctionDef:
    found = [
        node
        for node in ast.walk(_trees()[relative])
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"{relative} defines {name} {len(found)} times; this pin reads one"
    return found[0]


def _spelled(node: ast.AST) -> set[str]:
    return {name for inner in ast.walk(node) if (name := _named(inner)) is not None}


def _schema(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _nodes(node: object, pointer: str = "#") -> Iterator[tuple[str, dict[str, Any]]]:
    """Every JSON object in a schema, with its JSON pointer."""
    if isinstance(node, dict):
        yield pointer, node
        for key, value in node.items():
            yield from _nodes(value, f"{pointer}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _nodes(value, f"{pointer}/{index}")


# -- D1: the closed sets and their classes ------------------------------------


#: The table's `Set` column against its `Class` column: decision 1's assignment.
CLASSES: Final = {
    "`kind`": "vocabulary",
    "`relationType`": "vocabulary",
    "the operation set": "grammar",
    "`status`": "wire-enumerated",
    "`sensitivity`": "wire-enumerated",
    "`trustLevel`": "wire-enumerated",
    "a specification's status": "retiring",
    "the format version": "the version constant",
}


def _closed_set_rows() -> list[tuple[str, ...]]:
    rows = _table(_section("### The closed sets the v1 format publishes"))
    assert rows[0] == ("Set", "Published as", "Python mirror", "Members", "Class (decision 1)")
    return rows[1:]


def _pointers(published_as: str) -> set[str]:
    """The schema pointers a `Published as` cell names."""
    token = re.findall(r"`([^`]+)`", published_as)[0]
    if token.endswith(".oneOf"):
        branches = _schema(MIGRATION_SCHEMA)["$defs"]["operation"]["oneOf"]
        return {f"#/$defs/{branch['$ref'].rsplit('/', 1)[1]}/properties/op" for branch in branches}
    return {f"#/{token}"}


def test_the_table_names_every_closed_set_the_migration_schema_publishes_in_one_class() -> None:
    text = MIGRATION_SCHEMA.read_text(encoding="utf-8")
    walked = {
        pointer: keyword
        for pointer, node in _nodes(_schema(MIGRATION_SCHEMA))
        for keyword in ("enum", "const")
        if keyword in node
    }
    rows = _closed_set_rows()

    assert sorted((row[0], row[4]) for row in rows) == sorted(CLASSES.items())
    assert set().union(*(_pointers(row[1]) for row in rows)) == set(walked)
    assert (
        len(re.findall(r'"(?:enum|const)":', text)),
        list(walked.values()).count("enum"),
        list(walked.values()).count("const"),
    ) == (21, 6, 15), "the ADR's `prints 21 lines: the apiVersion const, six enums, ...`"


@pytest.mark.parametrize("name", CLASSES, ids=[collapsed(name) for name in CLASSES])
def test_each_python_mirror_equals_its_published_set_member_for_member(name: str) -> None:
    """Adding a member to the schema or to the enum alone goes RED here."""
    [row] = [row for row in _closed_set_rows() if row[0] == name]
    module, attribute = re.findall(r"`([^`]+)`", row[2])
    mirror = getattr(
        importlib.import_module(f"theurian.{module[:-3].replace('/', '.')}"), attribute
    )
    schema = _schema(MIGRATION_SCHEMA)
    published = [
        value
        for pointer in _pointers(row[1])
        for node in [dict(_nodes(schema))[pointer]]
        for value in node.get("enum", [node.get("const")])
    ]
    enumerated = isinstance(mirror, type) and issubclass(mirror, Enum)
    mirrored = [member.value for member in mirror] if enumerated else [mirror]

    assert sorted(published) == sorted(mirrored)
    assert len(published) == int(row[3])


def test_the_engine_version_is_one_hashed_into_the_state_and_published_by_project_status() -> None:
    inputs = StateInputs(
        migrations=(), content_checksums=(), schema_version=sqlite_schema.SCHEMA_VERSION
    )
    bumped = dataclasses.replace(inputs, engine_version=MIGRATION_ENGINE_VERSION + 1)
    published = {
        ast.unparse(value)
        for node in ast.walk(_trees()["cli/commands.py"])
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "engineVersion"
    }

    assert MIGRATION_ENGINE_VERSION == 1
    assert compute_state_hash(inputs) != compute_state_hash(bumped)
    assert published == {"MIGRATION_ENGINE_VERSION"}


# -- Context: what an unknown value meets today (the #849 defect) -------------


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


# -- Context: the entry-point population --------------------------------------


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


def _entry_points() -> dict[str, list[tuple[str, str, ast.AST]]]:
    """``(path, function, node)`` per mechanism, under the key in the module docstring."""
    found: dict[str, list[tuple[str, str, ast.AST]]] = collections.defaultdict(list)
    for path, tree in _trees().items():
        for node, scope in _scoped(tree):
            if isinstance(node, ast.Call) and _named(node.func) in ADDITIVE:
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


def test_every_entry_point_the_schema_does_not_guard_has_its_table_row() -> None:
    found = _entry_points()
    loader = [scope for path, scope, _ in found["construction"] if path == _LOADER]
    reachable = {
        (path, scope)
        for mechanism in ("construction", "_closed_value", "option")
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


# -- Context: each entry point's refusal, measured ----------------------------


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


# -- Context: the derived store ------------------------------------------------


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


# -- Context: what `compat check` compares -------------------------------------


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
    read = _spelled(_function("cli/main.py", "compat_check")) | _spelled(
        _trees()["domain/compatibility.py"]
    )

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


# -- Context: what the wire carries (D8, D9) ------------------------------------


_SETS: Final = {cls.__name__: frozenset(member.value for member in cls) for cls in GOVERNED}


def _classified() -> tuple[
    dict[tuple[str, str], list[str]], dict[tuple[str, str], dict[str, list[str]]]
]:
    """The ADR's program: which enums enumerate a governed set, and which only overlap one."""
    enumerates: dict[tuple[str, str], list[str]] = {}
    overlaps: dict[tuple[str, str], dict[str, list[str]]] = {}
    for path in sorted(SCHEMAS.rglob("*.json")):
        if path == MIGRATION_SCHEMA:
            continue
        for pointer, node in _nodes(_schema(path)):
            if not isinstance(node.get("enum"), list):
                continue
            where = (path.relative_to(REPO_ROOT).as_posix(), pointer)
            values = {value for value in node["enum"] if value is not None}
            inside = [name for name, members in _SETS.items() if values and values <= members]
            shared = {
                name: sorted(values & members)
                for name, members in _SETS.items()
                if values & members
            }
            if inside:
                enumerates[where] = inside
            elif shared:
                overlaps[where] = shared
    return enumerates, overlaps


_RESULT: Final = "schemas/knowledge/retrieval-result.schema.json"
_CONFIG: Final = "schemas/config/project-config.schema.json"


def test_the_enumerates_key_finds_status_trust_level_and_sensitivity_and_nothing_additive() -> None:
    enumerates, _ = _classified()
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
    """The key's hole as a tripwire: a new overlap is for a person to classify.

    A superset enum -- a governed set plus other members -- is not *contained* in
    the set, so the enumerates key does not count it; it lands here instead.
    """
    _, overlaps = _classified()

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
    enumerates, _ = _classified()
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


# -- Context: applied migrations are frozen (the premise of D4) ----------------


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


# -- Decisions D3, D4, D7 and the tests the ADR rests on ------------------------


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


# -- The ADR's own structure: the matrix, the decisions, compliance -------------


def test_the_matrix_holds_rows_c1_to_c8_each_filling_every_column() -> None:
    header, *rows = _table(_section("### The matrix"))

    assert [label for row in rows for label in re.findall(r"^\*\*(C\d+)\*\* ", row[0])] == [
        f"C{number}" for number in range(1, 9)
    ]
    assert [(len(row), all(row)) for row in rows] == [(len(header), True)] * len(rows)


#: Each decision's anchor phrase: a rewording that keeps the decision stays GREEN.
DECISIONS: Final = {
    1: "The policy governs the eight entries of the table above, each in exactly one class",
    2: "Adding a vocabulary member is additive.",
    3: "Adding an operation keeps ADR-0005's rule: it bumps apiVersion.",
    4: "Removing or renaming a member or an operation takes it out of Core's own writers only.",
    5: "Read support is permanent.",
    6: "Changing what an existing member or operation means, under its existing spelling, bumps",
    7: "Reordering members is a reviewed diff, not a version event.",
    8: "Wire-enumerated sets are outside the additive class.",
    9: "No change to kind, relationType or the operation set bumps protocolVersion",
    10: "theurian compat check does not surface an enum mismatch",
}


def test_the_decision_section_holds_exactly_decisions_one_to_ten() -> None:
    decisions = _numbered(_section("## Decision"))

    assert sorted(decisions) == sorted(DECISIONS)
    assert [n for n, anchor in DECISIONS.items() if not decisions[n].startswith(anchor)] == []


def test_the_compliance_section_names_this_module_whose_docstring_states_its_reach() -> None:
    assert Path(__file__).resolve().relative_to(REPO_ROOT).as_posix() == THIS_MODULE
    assert collapsed(f"`{THIS_MODULE}`, the claims pin.") in _adr()
    assert "**Reach.**" in (__doc__ or "")


# -- Amendments and the roadmap -----------------------------------------------


@pytest.mark.parametrize("path", [ADR_0005, ADR_0038], ids=["ADR-0005", "ADR-0038"])
def test_the_amended_record_carries_a_block_naming_adr_0039_and_lost_no_line(path: Path) -> None:
    relative = path.relative_to(REPO_ROOT).as_posix()
    base = _git("show", f"origin/main:{relative}").splitlines()
    working = iter(path.read_text(encoding="utf-8").splitlines())

    assert base, f"origin/main has no {relative}; `git fetch origin main` first"
    assert [
        block
        for block in _blockquotes(path)
        if block.startswith("Amended") and collapsed(ADR_0039_LINK) in block
    ]
    assert all(line in working for line in base), "a line of origin/main's record is gone"


def test_adr_0005s_amendment_rests_on_the_rules_it_cites() -> None:
    lines = ADR_0005.read_text(encoding="utf-8").splitlines()
    rules = _numbered(lines[lines.index("Rules the engine enforces:") + 2 :])
    [block] = _blockquotes(ADR_0005)

    assert rules[2].startswith("An applied migration is frozen.")
    assert (
        rules[8] == "Applying all migrations to an empty store reproduces the full canonical state."
    )
    assert "an applied migration is frozen (rule 2)" in block
    assert "an empty store replays every committed document (rule 8)" in block


def test_the_roadmap_records_adr_0039_against_candidate_three_and_section_four() -> None:
    marker = "Recorded: [ADR-0039](adr/0039-closed-set-extension-compatibility.md)"
    candidates = _numbered(_section("## 9. ADR candidates", ROADMAP))
    compatibility = collapsed(" ".join(_section("### Migration compatibility", ROADMAP)))

    assert candidates[3].startswith("The compatibility policy for extending a closed enum")
    assert marker in candidates[3]
    assert f"{marker}, which takes the first two clauses and declines the third" in compatibility
    assert (ROADMAP.parent / "adr" / ADR.name).is_file()


# -- The prose half -----------------------------------------------------------


#: What ADR-0039 still has to say, in its own markup and wrapped at will: the
#: comparison collapses whitespace, emphasis and code markers on both sides.
#: Each fragment's fact half is the test above that carries the same claim.
ADR_STATES: Final[dict[str, str]] = {
    "measured-at": """**Every repository fact below was measured on 2026-09-30 against
        `origin/main` at `f0e4d754`.**""",
    "schema-lines": """prints 21 lines: the `apiVersion` const, six enums, and the fourteen
        `op` consts of `$defs/operation.oneOf`'s fourteen branches.""",
    "engine-version": """`domain/migration.py` also holds `MIGRATION_ENGINE_VERSION = 1`, which
        is hashed into the state hash (`domain/state.py:132`) and reported as `engineVersion`
        by `theurian project status`""",
    "adr-0005-silent": """It said nothing of adding a `kind` or `relationType` member, of
        removing, renaming or reordering anything, or of changing what a member means.""",
    "entry-key": """The population is keyed on the three classes the additive policy governs —
        `KnowledgeKind`, `RelationType` and `OperationKind` — at every place a value becomes
        one of them: a construction from a value, a call of the MCP layer's closed-set parser
        with one of them, and a CLI option typed by one of them.""",
    "entry-accounting": """Of the first command's twelve lines, three are class statements and
        four are the loader's constructions, which run after schema validation, so no unknown
        value reaches them.""",
    "outside": """`SqliteCanonicalStore.count_surfaceable_by_status` constructs `Sensitivity`
        from a stored row (`infrastructure/sqlite/store.py:845`), and `knowledge.status` reaches
        it""",
    "loader-row": """`<file> is invalid at operations/<N>: does not satisfy 'oneOf' (expected
        [...]); the value there is {...}`. The location is the operation index and the keyword
        is the discriminated `oneOf`, which fails whole, so neither the field nor the enum that
        failed is named""",
    "loader-cases": """An unknown `kind` in `createItem` or in `upsertRevision.metadata`,
        `relationType`, `status`, `sensitivity`, `trustLevel` or `op`, and a
        `registerSpecification` renamed away, were each refused so, with the value inside the
        echo; the unmodified copy loads.""",
    "truncation": """An `addRelation` carrying a legal 990-character `note` (its `maxLength` is
        1,000, and `note` sorts before `relationType` in the echo) and `relationType: traces_to`
        was refused with `traces_to` nowhere in the 1,315-character message; the same operation
        with `constrained_by` loads""",
    "api-version": """before the compiled comparison at `:1613` is reached. With
        `theurian.dev/v2` and an unknown `kind` in one document, this is the refusal reported""",
    "validator": """`MigrationError`: `invalid migration at operations/<N>: does not satisfy
        'oneOf' ...`""",
    "v1-gate": """The v1 gate skips an unknown `op` (`OperationKind(raw)` at `:3767-3770`
        `continue`s) and leaves it to the injected validator""",
    "wrap": """`_refuse_a_document_the_schema_rejects` wraps the validator's refusal as
        `ProposalError`""",
    "closed-value": "No: it names the field and the valid set",
    "typer": """Option parsing, Typer `BadParameter`: `'requirement' is not one of
        'architecture', ..., 'known-exception'.`""",
    "okf-concept": """`ImportRefusal(kind="concept", key=<file>,
        literal="unrecognized type: 'specification'")`; other concepts still import""",
    "okf-relations": """Carried verbatim into an `addRelation` in the one relations draft. An
        unknown type fails that draft's validation, and the whole draft is refused as one
        `ImportRefusal(kind="relations", key="addRelation")`""",
    "store-guard": """every store read runs its decoder inside `_reading()` (`_read_one`,
        `:418-420`), which re-raises it as `StateDatabaseUnreadableError`""",
    "store-cause": "No: the message names only the exception type; the value is on `__cause__`",
    "state-inputs": """The state hash's inputs are the migration ids and checksums, the body
        checksums, `SCHEMA_VERSION` and `MIGRATION_ENGINE_VERSION` (`domain/state.py:47-54`); no
        Core version and no enum membership.""",
    "cli-loads": "`resolve_state_hash(loaded: LoadedMigrations, ...)`",
    "mcp-no-load": """The MCP tools do not load migrations: they open the database the
        project's active-state pointer names (`mcp/tools.py:1769`, `read_active_state`; `:1817`,
        `state_database_named`).""",
    "compat-signature": """`resolve_compatibility(declaration, core_version,
        core_protocol_version)`""",
    "compat-outcomes": """decides one of `compatible`, `core-missing`, `core-too-old`,
        `core-too-new` and `protocol-mismatch`""",
    "compat-options": """The command's options are `--plugin-version`, `--core-minimum`,
        `--core-maximum-exclusive`, `--protocol-version` and `--json`""",
    "compat-protocol": """`CURRENT_PROTOCOL_VERSION = "theurian/v1"`
        (`domain/compatibility.py:125`). It reads no project, no migration and no enum.""",
    "enumerates-key": """An `enum` enumerates a governed set when all of its non-null members
        are members of that set.""",
    "enumerates-found": """the program below finds four such enums and none of `kind`,
        `relationType` or the operation set""",
    "overlaps": """`review-generate-knowledge-candidate-input`'s `category` shares
        `rejected-approach` and `known-exception` with `KnowledgeKind`""",
    "overlaps-reviewer": "`review-findings-response`'s `reviewer` shares `security`",
    "hole": """That is the key's hole: a future enum carrying a governed set plus other members
        is not contained in the set, so the key does not count it, and it is to be classified
        by a person when it appears rather than passed silently.""",
    "inputs": """type `kind` as a string (and the first types `trustLevel` and `sensitivity` as
        string or null), and `knowledge-generate-migration-draft-input` constrains no `op`.""",
    "record-kind": """`review-search-response`'s `records.items.kind`, `pull-request`,
        `review-submission` and `review-thread`""",
    "two-fields": """Values still travel, un-enumerated, in two response fields, neither under
        a response schema: `knowledge.get` publishes each visible relation's `relationType`""",
    "operations-field": """`knowledge.generateMigrationDraft` publishes the drafted document's
        operation names as `operations` (`_drafted_migration_payload`""",
    "no-kind": """No MCP response publishes a `KnowledgeKind`: under `mcp/`, it appears only as
        the two `_closed_value` inputs above, and the one `.kind` published is the review
        record's""",
    "search-ref": """`knowledge-search-response` types each result by `$ref` to
        `schemas/knowledge/retrieval-result.schema.json`, which enumerates `status` as the three
        surfaceable members (`approved`, `draft`, `proposed`), `trustLevel` (4) and
        `sensitivity` (4); `mcp/results.py:95-97` publishes all three on every result.""",
    "plugins": """`plugins/` uses a member in two places and parses none""",
    "plugins-count": "The key is all 39 `kind`, `relationType` and `op` values, as whole words",
    "plugins-lines": "It prints 37 lines, all in Markdown.",
    "freeze": """`verify_no_applied_migration_changed` (`application/migration_engine.py:168`)
        refuses an applied migration whose checksum moved""",
    "gates": "`status` feeds `may_surface` and `sensitivity` feeds `may_disclose`",
    "trust-neither": "`trustLevel` feeds neither gate",
    "trust-inferred": "which import and candidate generation fix at `inferred`",
    "stamps": """the two lines under `packages/theurian-core/src` that stamp `apiVersion` are
        both in `application/proposal_service.py`""",
    "v1-enumerated": """`V1_OPERATION_KINDS` is already that pattern for the operation set —
        enumerated rather than derived as `frozenset(OperationKind) - refused`""",
    "reorder-test": """holds `$defs/relationType` equal to `RelationType` as an ordered list
        because "a reorder is a diff its reviewer should see\"""",
    "count-test": "asserts `len(RelationType) == 14`",
    "partition-test": """`test_the_v1_operation_set_partitions_operation_kind` fails when an
        `OperationKind` is routed nowhere.""",
    "config-status": """A change to `status` also moves `project-config.schema.json`'s
        `retrieval.includeStatuses`, which lists all six members; that is a published
        configuration schema, not the wire""",
    "config-api-version": """`schemas/config/project-config.schema.json` carries its own
        `apiVersion` const with the same spelling, `theurian.dev/v1`.""",
    "reach": "Its reach is stated in its module docstring.",
}


@pytest.mark.parametrize(("name", "fragment"), ADR_STATES.items(), ids=list(ADR_STATES))
def test_the_adr_still_states(name: str, fragment: str) -> None:
    assert collapsed(fragment) in _adr(), (
        f"ADR-0039 no longer states ({name}):\n\n  {collapsed(fragment)}\n\nIf the fact half is "
        f"GREEN, the tree did not move and the record is what gets restored; if it is RED, the "
        f"sentence moves with the tree."
    )
