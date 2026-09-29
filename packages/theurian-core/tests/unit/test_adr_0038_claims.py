"""ADR-0038's repository facts, held against the tree they were measured on.

ADR-0038 decides that the ``Specification`` entity folds into a knowledge
``kind``; the branch where the entity stays separate is the rejected one. The
ADR ships no code, so every sentence it states about the codebase was measured
once, at ``855ebd87``, and the slices written against it -- #274's policy,
#275's edge representation, the retirement (#841) -- read those sentences rather
than the tree.

Each claim is held from both sides: a fact half recomputed from live source,
RED when the tree moves, and a prose half (:func:`test_the_adr_still_states`),
RED when the record drifts. A new reference to a specification reader -- the
shape a breach of decision 3 takes -- reddens
:func:`test_the_store_specification_methods_are_reached_only_by_the_engines_two_writes`.

**Reach.** The scans parse every ``.py`` file ``git ls-files`` lists and see
attribute references, bare names and exact-string spellings. Four keys reach
further. A ``structured`` read is an attribute load, ``getattr(x,
"structured")`` or the bare string ``"structured"`` (a row or payload key), and
beside it every whole-object read -- ``asdict``, ``astuple``, ``vars``,
``__dict__``, ``getattr`` with a computed name -- is held as an exact allow-set.
A compiled ``apiVersion`` check is any comparison or ``match`` that reads the
key or names the constant, a container holding it, or a name bound to either;
and every read of the key is held to the two functions that check it today.
SQL names the ``specifications`` table bare, quoted, schema-qualified or after a
comma join. A construction is ``Specification(...)`` called by bare name or as
an attribute. The scans do not see a class imported under another name, a copy
made by ``dataclasses.replace``, a keyword smuggled through ``**kwargs``, a
dictionary key computed at run time, SQL assembled from fragments, or a second
whole-object read inside a function already on the allow-set.

**What is not parsed, and what it costs.** This module spells every name it
searches for as data, so it is excluded (tracked, it would find itself).
``domain/enums.py``, read here only by importing its enums, ``mcp/results.py``
and ``tests/unit/test_gate_call_sites.py`` are fenced because PR #835
(https://github.com/theurian/theurian/pull/835) edits all three. The fence is
not free: ``mcp/results.py`` is a real ``KnowledgeRevision`` serialiser -- it
builds the search-result payload field by field -- so a specification-reader
call, a ``structured`` publication or a traceability-edge method placed there
is not seen by these scans while the fence stands. Deleting those three entries
from :data:`_UNREAD` once #835 has merged lifts it.

Every scan whose expected answer is *nothing* runs beside a positive control on
the same key, and every widened shape is driven through its classifier from an
in-memory snippet, because a broken walk and a clean tree look alike from
outside.

Pure: syntax trees, in-memory domain objects, JSON schemas, YAML, Markdown, and
read-only ``git ls-files`` and ``git grep``. No database, socket or temporary
directory.
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import functools
import inspect
import itertools
import json
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import typing
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
import yaml

from theurian.application.index_builder import IndexBuilder
from theurian.application.okf_export import OkfExporter, OkfExportSession
from theurian.application.proposal_service import V1_OPERATION_KINDS
from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    ACYCLIC_RELATIONS,
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    SpecificationStatus,
    TraceNodeType,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import (
    ItemId,
    MigrationId,
    ProjectId,
    RevisionId,
    SpecId,
    _DottedId,
)
from theurian.domain.knowledge import (
    AUTHORED_IN_THEURIAN,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
)
from theurian.domain.migration import (
    MIGRATION_API_VERSION,
    MIGRATION_ENGINE_VERSION,
    Operation,
    OperationKind,
)
from theurian.domain.ports.canonical_store import (
    CanonicalReadSession,
    CanonicalStore,
    IndexBuildSession,
)
from theurian.domain.ports.specification_provider import SpecificationProvider
from theurian.domain.specification import (
    Specification,
    TraceabilityEdge,
    TraceabilityPolicy,
    TraceabilityRule,
    TraceNode,
)
from theurian.domain.state import StateInputs, compute_state_hash
from theurian.domain.values import MediaType, ValidityPeriod
from theurian.infrastructure.sqlite import schema as sqlite_schema
from theurian.infrastructure.sqlite.store import (
    _ITEM_METADATA_SQL,
    _ITEM_WITH_CURRENT_CONTENT_SQL,
    _specification_from_row,
)
from theurian.mcp.tools import _relation_is_visible

pytestmark = pytest.mark.unit

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"
README: Final = REPO_ROOT / "README.md"
THREAT_MODEL: Final = REPO_ROOT / "docs" / "security" / "threat-model.md"
TRACEABILITY: Final = REPO_ROOT / "docs" / "architecture" / "traceability.md"
ADR_0005: Final = REPO_ROOT / "docs" / "adr" / "0005-yaml-knowledge-migrations.md"
MIGRATION_SCHEMA: Final = REPO_ROOT / "schemas" / "migrations" / "migration.schema.json"

SRC: Final = "packages/theurian-core/src/theurian/"
ALIAS_GUARDS: Final = SRC + "application/migration_alias_guards.py"
ENGINE: Final = SRC + "application/migration_engine.py"
INGESTION: Final = SRC + "application/ingestion_service.py"
PROPOSALS: Final = SRC + "application/proposal_service.py"
COMMANDS: Final = SRC + "cli/commands.py"
KNOWLEDGE: Final = SRC + "domain/knowledge.py"
MIGRATION: Final = SRC + "domain/migration.py"
PORT: Final = SRC + "domain/ports/canonical_store.py"
PROVIDER: Final = SRC + "domain/ports/specification_provider.py"
LOADER: Final = SRC + "infrastructure/filesystem/migration_loader.py"
PARSERS: Final = SRC + "infrastructure/filesystem/parsers/"
STORE: Final = SRC + "infrastructure/sqlite/store.py"
TOOLS: Final = SRC + "mcp/tools.py"
SNIPPET: Final = "<snippet>"
CORRUPTION_TEST: Final = (
    "packages/theurian-core/tests/integration/test_canonical_store_corruption.py"
)
SAMPLE_MIGRATION: Final = (
    "examples/sample-project/.theurian/migrations/"
    "01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml"
)

THIS_MODULE: Final = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()

_UNREAD: Final = frozenset(
    {
        SRC + "domain/enums.py",
        SRC + "mcp/results.py",
        "packages/theurian-core/tests/unit/test_gate_call_sites.py",
        THIS_MODULE,
    }
)

_SPECIFICATION_METHODS: Final = frozenset(
    {
        "get_specification",
        "list_specifications",
        "register_specification",
        "supersede_specification",
    }
)
_SPECIFICATION_READERS: Final = frozenset({"get_specification", "list_specifications"})
_SPECIFICATIONS_TABLE: Final = (
    r"""(?:(?:\w+|"\w+"|`\w+`|\[\w+\])\s*\.\s*)?"""
    r"""(?:specifications|"specifications"|`specifications`|\[specifications\])(?![\w"`\]])"""
)
_READS_SPECIFICATIONS: Final = re.compile(
    rf"""\b(?:FROM|JOIN)\s+(?:[\w."`\[\]]+(?:\s+(?:AS\s+)?\w+)?\s*,\s*)*{_SPECIFICATIONS_TABLE}""",
    re.IGNORECASE,
)
_WRITES_SPECIFICATIONS: Final = re.compile(
    rf"\b(?:(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+"
    rf"{_SPECIFICATIONS_TABLE}",
    re.IGNORECASE,
)
_NAMES_A_SPECIFICATION_OPERATION: Final = re.compile(
    r"registerSpecification|supersedeSpecification"
)
_WHOLE_OBJECT_READERS: Final = frozenset({"asdict", "astuple", "vars"})


# -- Reading the tree ---------------------------------------------------------


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not on PATH, so the committed tree cannot be listed")
    # A hook exports GIT_DIR / GIT_INDEX_FILE, which would answer for another tree.
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return subprocess.run(  # noqa: S603 - fixed argv, no caller input
        [git, "-c", f"safe.directory={REPO_ROOT}", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        env=environment,
        check=False,
        timeout=30,
    )


def _tracked(*pathspecs: str) -> tuple[str, ...]:
    listed = _git("ls-files", "-z", "--", *pathspecs)
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    return tuple(sorted(filter(None, listed.stdout.decode("utf-8", "surrogateescape").split("\0"))))


@functools.cache
def _parsed() -> Mapping[str, ast.Module]:
    return {
        path: ast.parse((REPO_ROOT / path).read_bytes(), filename=path)
        for path in _tracked("*.py")
        if path not in _UNREAD and (REPO_ROOT / path).is_file()
    }


def _trees(prefix: str = "") -> Mapping[str, ast.Module]:
    trees = {path: tree for path, tree in _parsed().items() if path.startswith(prefix)}
    assert {STORE, ENGINE} <= trees.keys(), (
        f"the scanned population under {prefix!r} no longer holds store.py and "
        f"migration_engine.py, so every scan over it would report nothing and read as a pass"
    )
    return trees


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


@functools.cache
def _index() -> tuple[Mapping[str, set[str]], Mapping[str, set[str]]]:
    """The files spelling each name, and the files defining a function by it: one walk."""
    spelled: collections.defaultdict[str, set[str]] = collections.defaultdict(set)
    defined: collections.defaultdict[str, set[str]] = collections.defaultdict(set)
    for path, tree in _parsed().items():
        for node in ast.walk(tree):
            match node:
                case ast.Attribute(attr=name) | ast.Name(id=name) | ast.Constant(value=str(name)):
                    spelled[name].add(path)
                case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                    defined[name].add(path)
    return spelled, defined


def _references(names: frozenset[str], prefix: str = "") -> set[tuple[str, str]]:
    """Every attribute, bare name or exact string spelling one of ``names``."""
    within = set(_trees(prefix))
    spelled, _ = _index()
    return {(path, name) for name in names for path in spelled.get(name, set()) & within}


def _definitions(name: str, prefix: str = "") -> set[str]:
    within = set(_trees(prefix))
    _, defined = _index()
    return defined.get(name, set()) & within


def _function(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    found = [
        node
        for node in ast.walk(_trees()[path])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"{path} defines {name} {len(found)} times; this pin reads exactly one"
    return found[0]


def _callees(node: ast.AST) -> set[str]:
    called: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            match call.func:
                case ast.Attribute(attr=name) | ast.Name(id=name):
                    called.add(name)
    return called


def _constructions(
    class_name: str, trees: Mapping[str, ast.Module] | None = None
) -> list[tuple[str, str, ast.Call]]:
    found: list[tuple[str, str, ast.Call]] = []
    for path, tree in (_trees(SRC) if trees is None else trees).items():
        for node, scope in _scoped(tree):
            match node:
                case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)) if (
                    name == class_name
                ):
                    found.append((path, scope, node))
    return found


def _sites(
    trees: Mapping[str, ast.Module], classify: Callable[[ast.AST], str | None]
) -> set[tuple[str, str, str]]:
    """``(path, function, what)`` for every node ``classify`` names."""
    return {
        (path, scope, found)
        for path, tree in trees.items()
        for node, scope in _scoped(tree)
        if (found := classify(node)) is not None
    }


def _structured_read(node: ast.AST) -> str | None:
    match node:
        case (
            ast.Attribute(attr="structured", ctx=ast.Load())
            | ast.Call(func=ast.Name(id="getattr"), args=[_, ast.Constant(value="structured"), *_])
            | ast.Constant(value="structured")
        ):
            return ast.unparse(node)
    return None


def _whole_object_read(node: ast.AST) -> str | None:
    """A read of every field of whatever object it is handed."""
    match node:
        case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)) if (
            name in _WHOLE_OBJECT_READERS
        ):
            return name
        case ast.Call(func=ast.Name(id="getattr"), args=[_, attribute, *_]) if not isinstance(
            attribute, ast.Constant
        ):
            return "getattr"
        case ast.Attribute(attr="__dict__"):
            return "__dict__"
    return None


def _sql(pattern: re.Pattern[str]) -> Callable[[ast.AST], str | None]:
    def classify(node: ast.AST) -> str | None:
        match node:
            case ast.Constant(value=str(text)) if pattern.search(text):
                return "sql"
        return None

    return classify


def _revision_creates(trees: Mapping[str, ast.Module]) -> set[str]:
    return {
        path
        for path, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and ast.unparse(node) == "KnowledgeRevision.create"
    }


def _keywords(call: ast.Call) -> dict[str | None, str]:
    return {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}


def _arms(node: ast.AST) -> dict[str, ast.match_case]:
    """Each ``case`` under ``node``, keyed by the class or the string it matches."""
    arms: dict[str, ast.match_case] = {}
    for arm in ast.walk(node):
        if isinstance(arm, ast.match_case):
            match arm.pattern:
                case (
                    ast.MatchClass(cls=ast.Name(id=key))
                    | ast.MatchValue(value=ast.Constant(value=str(key)))
                ):
                    arms[key] = arm
    return arms


def _raises(node: ast.AST) -> bool:
    return any(isinstance(inner, ast.Raise) for inner in ast.walk(node))


def _executed_sql(function: ast.AST) -> list[str]:
    return [
        ast.literal_eval(call.args[0])
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "execute"
    ]


def _ddl_tables() -> dict[str, dict[str, str]]:
    """Column name to the rest of its definition line, per ``CREATE TABLE``."""
    constraints = {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"}
    tables: dict[str, dict[str, str]] = {}
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sqlite_schema.DDL, re.DOTALL):
        definitions = (line.split(maxsplit=1) for line in body.splitlines())
        tables[table] = {
            words[0]: words[-1]
            for words in definitions
            if words and not words[0].startswith("--") and words[0] not in constraints
        }
    return tables


def _migration_defs() -> dict[str, Any]:
    return dict(json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))["$defs"])


def _reads_the_version(node: ast.AST) -> bool:
    match node:
        case (
            ast.Subscript(slice=ast.Constant(value="apiVersion"))
            | ast.Call(func=ast.Attribute(attr="get"), args=[ast.Constant(value="apiVersion"), *_])
        ):
            return True
    return False


def _names_the_version(node: ast.AST | None, bound: frozenset[str]) -> bool:
    """The constant, a container holding it, or a name bound to either."""
    match node:
        case ast.Constant(value="theurian.dev/v1"):
            return True
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name in bound
        case ast.Set(elts=items) | ast.List(elts=items) | ast.Tuple(elts=items):
            return any(_names_the_version(item, bound) for item in items)
        case ast.Dict(keys=keys):
            return any(_names_the_version(key, bound) for key in keys)
        case ast.Call(args=[inner]):
            return _names_the_version(inner, bound)
    return False


def _api_version_checks(trees: Mapping[str, ast.Module]) -> set[tuple[str, str]]:
    """Every comparison or ``match`` over a document's ``apiVersion``, in any shape."""
    assignments = [
        (target.id, node.value)
        for tree in trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    ]
    bound = frozenset({"MIGRATION_API_VERSION"})
    while (
        grown := {name for name, value in assignments if _names_the_version(value, bound)} - bound
    ):
        bound |= grown

    def checks_the_version(node: ast.AST) -> bool:
        return _reads_the_version(node) or _names_the_version(node, bound)

    checks: set[tuple[str, str]] = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            match node:
                case ast.Compare(left=left, comparators=rest) if any(
                    map(checks_the_version, (left, *rest))
                ):
                    checks.add((path, ast.unparse(node)))
                case ast.Match(subject=subject, cases=cases) if _reads_the_version(subject) or any(
                    isinstance(pattern, ast.MatchValue) and checks_the_version(pattern.value)
                    for case in cases
                    for pattern in ast.walk(case.pattern)
                ):
                    checks.add((path, f"match {ast.unparse(subject)}"))
    return checks


# -- Reading the documents ----------------------------------------------------


def _collapsed(text: str) -> str:
    """Line wrapping, emphasis and code markers flattened away; they carry no claim."""
    return " ".join(text.replace("*", "").replace("`", "").split())


def _adr() -> Path:
    found = sorted((REPO_ROOT / "docs" / "adr").glob("0038-*.md"))
    assert len(found) == 1, f"expected one ADR-0038 file, found {found}"
    return found[0]


def _section(path: Path, heading: str) -> list[str]:
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
        stripped = line.strip()
        if stripped.startswith("|"):
            rows.append(tuple(cell.strip() for cell in stripped.strip("|").split("|")))
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
    return {number: _collapsed(" ".join(parts)) for number, parts in items.items()}


# -- In-memory domain objects -------------------------------------------------

PROJECT: Final = ProjectId("sample")
MOMENT: Final = datetime(2026, 9, 29, tzinfo=UTC)


def _item(item_id: str) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=ItemId(item_id),
        project_id=PROJECT,
        namespace="orders",
        kind=KnowledgeKind.DOMAIN,
        status=KnowledgeStatus.APPROVED,
        current_revision_id=None,
        owner="orders-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=MOMENT),
    )


class _Endpoints:
    """The one read ``_relation_is_visible`` makes, answered from a dict."""

    def __init__(self, *items: KnowledgeItem) -> None:
        self._items = {item.item_id: item for item in items}

    def get_item_exact_metadata(
        self, _context: RequestContext, item_id: ItemId
    ) -> KnowledgeItem | None:
        return self._items.get(item_id)


# -- The decision (AC-1) -------------------------------------------------------


def test_the_adr_records_the_fold_as_accepted_and_rejects_keeping_the_entity_separate() -> None:
    lines = _adr().read_text(encoding="utf-8").splitlines()
    alternatives = _table(_section(_adr(), "## Alternatives considered"))

    assert lines[0] == "# ADR-0038: The Specification entity folds into a knowledge `kind`"
    assert "- Status: accepted" in lines
    assert alternatives[0] == ("Alternative", "Why rejected")
    assert (
        "Keep Specification a separate entity and bridge it to knowledge by relations or "
        "traceability edges"
    ) in {_collapsed(row[0]) for row in alternatives[1:]}


def test_the_compliance_section_names_this_module() -> None:
    assert THIS_MODULE == "packages/theurian-core/tests/unit/test_adr_0038_claims.py"


# -- Context: the entity, its table and its readers ---------------------------


def test_the_specification_entity_has_its_own_identifier_status_vocabulary_and_table() -> None:
    hints = typing.get_type_hints(Specification)

    assert Specification.__module__ == "theurian.domain.specification"
    assert hints["spec_id"] is SpecId
    assert hints["status"] is SpecificationStatus
    assert {"specifications", "traceability_edges"} <= _ddl_tables().keys()


def test_the_entitys_revision_is_its_only_link_to_an_item() -> None:
    hints = typing.get_type_hints(Specification)

    assert typing.get_type_hints(KnowledgeRevision)["item_id"] is ItemId, "positive control"
    assert [name for name, hint in hints.items() if hint is ItemId or "item" in name] == []
    assert [name for name, hint in hints.items() if hint is RevisionId] == ["revision_id"]


def test_the_traceability_edge_methods_are_declared_on_the_port_alone_and_never_called() -> None:
    edge_methods = frozenset({"add_traceability_edge", "list_traceability_edges"})

    assert _references(frozenset({"register_specification"})), (
        "positive control: the reference scan no longer sees the engine's known write"
    )
    for name in edge_methods:
        assert _definitions(name) == {PORT}, f"{name} is now defined outside the port"
    assert _references(edge_methods) == set(), (
        "a traceability edge method is now referenced; ADR-0038's `No edge exists yet` "
        "and #277's `cheap moment` both move with it"
    )


def test_register_specification_pins_the_items_current_revision() -> None:
    function = _function(ENGINE, "_register_specification")
    constructions = _constructions("Specification")
    engine_call = next(call for path, _, call in constructions if path == ENGINE)
    keywords = _keywords(engine_call)
    attribute_form = ast.parse("entity = specification.Specification(spec_id=spec_id)")

    assert len(_constructions("Specification", {SNIPPET: attribute_form})) == 1, (
        "positive control: the attribute form"
    )
    assert "item = self._require_item(writer, project_id, operation.item_id, migration)" in {
        ast.unparse(statement) for statement in function.body
    }
    assert any(
        isinstance(statement, ast.If)
        and ast.unparse(statement.test) == "item.current_revision_id is None"
        and _raises(statement)
        for statement in function.body
    )
    assert sorted((path, scope) for path, scope, _ in constructions) == [
        (ENGINE, "_register_specification"),
        (STORE, "_specification_from_row"),
    ], "a new `Specification(...)` construction is new code writing the entity (decision 3)"
    assert keywords["revision_id"] == "item.current_revision_id"
    assert keywords["title"] == "operation.spec_id.value"
    assert not {"structured", "anchors", None} & keywords.keys()


def test_the_specifications_table_hangs_off_a_revision_without_anchors_or_sensitivity() -> None:
    columns = _ddl_tables()["specifications"]

    assert {"revision_id", "superseded_by"} <= columns.keys()
    assert re.search(
        r"REFERENCES\s+knowledge_revisions\s*\(\s*revision_id\s*\)", columns["revision_id"]
    )
    assert not {"anchors", "sensitivity"} & columns.keys()
    assert columns["structured"] == "TEXT NOT NULL DEFAULT '{}',"


def test_only_a_registration_writes_the_revision_pin_and_re_registering_re_pins_it() -> None:
    writes = _sites(_trees(SRC), _sql(_WRITES_SPECIFICATIONS))
    [upsert] = _executed_sql(_function(STORE, "register_specification"))
    [update] = _executed_sql(_function(STORE, "supersede_specification"))
    assigned = re.fullmatch(
        r"UPDATE specifications SET (.+) WHERE project_id = \? AND spec_id = \?", update
    )

    assert writes == {
        (STORE, "register_specification", "sql"),
        (STORE, "supersede_specification", "sql"),
    }
    assert "ON CONFLICT(project_id, spec_id) DO UPDATE SET" in upsert
    assert "revision_id = excluded.revision_id" in upsert
    assert assigned
    assert [column.split("=")[0].strip() for column in assigned.group(1).split(",")] == [
        "status",
        "superseded_by",
    ]


def test_the_table_keys_match_every_spelling_of_the_table_and_not_a_longer_name() -> None:
    reads = [
        'SELECT * FROM "specifications"',
        "SELECT * FROM `specifications`",
        "SELECT * FROM [specifications]",
        "SELECT * FROM main.specifications",
        'SELECT * FROM "main"."specifications"',
        "SELECT * FROM knowledge_items AS k, specifications s",
        "SELECT 1 FROM x JOIN Specifications ON 1",
    ]
    writes = [
        'UPDATE main."specifications" SET status = ?',
        "INSERT OR REPLACE INTO [specifications] (spec_id) VALUES (?)",
        "DELETE FROM `specifications`",
        "REPLACE INTO specifications VALUES (?)",
    ]

    assert [sql for sql in reads if not _READS_SPECIFICATIONS.search(sql)] == []
    assert [sql for sql in writes if not _WRITES_SPECIFICATIONS.search(sql)] == []
    assert not _READS_SPECIFICATIONS.search("SELECT * FROM specifications_archive")
    assert not _WRITES_SPECIFICATIONS.search("UPDATE main.specifications_archive SET x = 1")


def test_the_sample_registers_a_separate_tracked_yaml_document_against_a_markdown_item() -> None:
    migration = PurePosixPath(SAMPLE_MIGRATION)
    operations = yaml.safe_load((REPO_ROOT / migration).read_text(encoding="utf-8"))["operations"]
    [registration] = [op for op in operations if op["op"] == "registerSpecification"]
    [created] = [
        op
        for op in operations
        if op["op"] == "createItem" and op["itemId"] == registration["itemId"]
    ]
    [revision] = [
        op
        for op in operations
        if op["op"] == "upsertRevision" and op["itemId"] == registration["itemId"]
    ]
    # The loader stores `sourceUri` verbatim and nothing in src resolves it. It is
    # read here against the project root -- the directory holding `.theurian/` --
    # which is the resolution the ADR's own path spells; `contentFile` is relative
    # to the migration's directory, as the loader resolves it.
    document = (migration.parents[2] / registration["sourceUri"]).as_posix()
    body = posixpath.normpath(migration.parent / revision["contentFile"])

    assert (registration["specId"], registration["itemId"]) == (
        "spec.order-cancellation",
        "domain.order-cancellation",
    )
    assert created["kind"] == revision["metadata"]["kind"] == "domain"
    assert revision["metadata"]["contentType"] == "text/markdown"
    assert registration["format"] == "application/yaml"
    assert document == "examples/sample-project/.theurian/specifications/order-cancellation.yaml"
    assert body != document
    assert _tracked(document, body) == tuple(sorted((document, body)))


def test_a_stored_specification_reads_back_with_no_anchors_and_an_unset_payload_is_empty() -> None:
    row = {
        "spec_id": "spec.order-cancellation",
        "project_id": "sample",
        "revision_id": "01K1DEFABC01234567890ABCDE",
        "title": "spec.order-cancellation",
        "status": "active",
        "content_format": "application/yaml",
        "source_uri": "specs/order-cancellation.yaml",
        "structured": "{}",
        "valid_from": "2026-09-29T00:00:00+00:00",
        "valid_to": None,
        "superseded_by": None,
    }
    fresh = Specification(
        spec_id=SpecId("spec.order-cancellation"),
        project_id=ProjectId("sample"),
        revision_id=RevisionId("01K1DEFABC01234567890ABCDE"),
        title="spec.order-cancellation",
        status=SpecificationStatus.ACTIVE,
        content_format=MediaType("application/yaml"),
        source_uri="specs/order-cancellation.yaml",
        validity=ValidityPeriod(valid_from=datetime(2026, 9, 29, tzinfo=UTC)),
    )

    assert row.keys() == _ddl_tables()["specifications"].keys()
    assert _specification_from_row(typing.cast("sqlite3.Row", row)).anchors == ()
    assert fresh.structured == {}


def test_the_store_implements_the_two_readers_and_holds_the_only_sql_that_reads_the_table() -> None:
    reading_sql = _sites(_trees(SRC), _sql(_READS_SPECIFICATIONS))

    for reader in _SPECIFICATION_READERS:
        assert _definitions(reader, SRC) == {PORT, STORE}
    assert reading_sql == {
        (STORE, "get_specification", "sql"),
        (STORE, "list_specifications", "sql"),
    }


def test_the_store_specification_methods_are_reached_only_by_the_engines_two_writes() -> None:
    assert _references(_SPECIFICATION_METHODS) == {
        (ENGINE, "register_specification"),
        (ENGINE, "supersede_specification"),
    }, (
        "a specification store method is now referenced outside the engine's two writes. "
        "A reader reference falsifies ADR-0038's `readers are implemented and never called` "
        "and is the shape a breach of decision 3 takes."
    )


def test_no_walkers_session_type_declares_a_specification_reader() -> None:
    exporter = inspect.signature(OkfExporter.__init__).parameters["store_factory"].annotation
    builder = inspect.signature(IndexBuilder.__init__).parameters["store_factory"].annotation
    gate = _function(TOOLS, "_relation_is_visible").args.args[0].annotation

    assert set(dir(CanonicalStore)) >= _SPECIFICATION_READERS, "positive control"
    assert exporter == "Callable[[Path], OkfExportSession]"
    assert builder == "Callable[[Path], IndexBuildSession]"
    assert gate is not None and ast.unparse(gate) == "CanonicalReadSession"
    for session in (CanonicalReadSession, IndexBuildSession, OkfExportSession):
        assert not _SPECIFICATION_READERS & set(dir(session)), session.__name__


# -- Context: neither side persists a parsed payload --------------------------


def test_only_the_row_decoders_and_the_ingest_carry_pass_a_structured_payload() -> None:
    passing = {
        (path, scope, ast.unparse(node.func))
        for path, tree in _trees(SRC).items()
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Call) and any(k.arg == "structured" for k in node.keywords)
    }
    parsed_forms = {
        entry
        for entry in passing
        if entry[0].startswith(PARSERS) and entry[2] == "NormalizedDocument"
    }
    revisions = {(path, scope) for path, scope, _ in _constructions("KnowledgeRevision")}

    assert {PARSERS + "structured.py", PARSERS + "openapi.py"} <= {
        path for path, _, _ in parsed_forms
    }
    assert passing - parsed_forms == {
        (INGESTION, "_to_document", "IngestedDocument"),
        (KNOWLEDGE, "create", "cls"),
        (STORE, "_revision_from_row", "KnowledgeRevision"),
        (STORE, "_specification_from_row", "Specification"),
    }, "a new write of a `structured` payload; ADR-0038's `Neither side persists` moves with it"
    assert (ENGINE, "_upsert_revision") in revisions
    assert _revision_creates(_trees()), (
        "positive control: the tests call `KnowledgeRevision.create`"
    )
    assert _revision_creates(_trees(SRC)) == set()


def test_the_structured_readers_are_the_ingest_report_the_store_and_knowledge_get() -> None:
    readers = _sites(_trees(SRC), _structured_read)
    assignments = {
        ast.unparse(node)
        for node in ast.walk(_function(TOOLS, "knowledge_get"))
        if isinstance(node, ast.Assign)
    }
    leak = ast.parse("def leak(revision):\n    return getattr(revision, 'structured')")

    assert "structured" in {field.name for field in dataclasses.fields(KnowledgeRevision)}
    assert "structured" in _ddl_tables()["knowledge_revisions"]
    assert "payload['structured'] = revision.structured" in assignments
    assert (SNIPPET, "leak", "getattr(revision, 'structured')") in _sites(
        {SNIPPET: leak}, _structured_read
    ), "positive control: the getattr form"
    assert readers == {
        (INGESTION, "_to_document", "normalized.structured"),
        (COMMANDS, "ingest_command", "d.structured"),
        (COMMANDS, "ingest_command", "'structured'"),
        (STORE, "append_revision", "revision.structured"),
        (STORE, "register_specification", "specification.structured"),
        (STORE, "_revision_from_row", "'structured'"),
        (STORE, "_specification_from_row", "'structured'"),
        (TOOLS, "knowledge_get", "revision.structured"),
        (TOOLS, "knowledge_get", "'structured'"),
    }, "a new reader of a `structured` field; ADR-0038's Context and #834's owed item move with it"


def test_every_whole_object_read_in_src_is_one_already_judged_not_to_publish_a_revision() -> None:
    dump = ast.parse(
        "def dump(revision, name):\n"
        "    return (dataclasses.asdict(revision), astuple(revision), vars(revision),\n"
        "            revision.__dict__, getattr(revision, name))"
    )

    assert {what for _, _, what in _sites({SNIPPET: dump}, _whole_object_read)} == {
        "asdict",
        "astuple",
        "vars",
        "__dict__",
        "getattr",
    }, "positive control"
    assert _sites(_trees(SRC), _whole_object_read) == {
        (SRC + "application/okf_codec.py", "_anchor_mapping", "getattr"),
        (SRC + "application/setup_steps.py", "_service_path", "getattr"),
        (SRC + "cli/setup_commands.py", "_service_path", "getattr"),
        (SRC + "domain/raptor.py", "_differing_components", "getattr"),
        (SRC + "domain/review_finding.py", "_matching_surface", "vars"),
        (SRC + "domain/review_search.py", "texts_of", "getattr"),
        (SRC + "security/yaml_loading.py", "_drop_implicit_resolver", "__dict__"),
    }, (
        "a new whole-object read, which publishes `structured` if it is handed a revision; "
        "decide which, then add it here"
    )


# -- Context: what the roadmap and the README say ------------------------------


def test_the_roadmap_recommends_the_fold_and_owes_candidates_three_and_six_before_phase_c() -> None:
    additions = _numbered(_section(ROADMAP, "### Four additions"))
    candidates = _numbered(_section(ROADMAP, "## 9. ADR candidates"))

    assert "ADR candidate #6" in additions[1]
    assert (
        "The recommendation is the unified form: spec-as-knowledge goes in kind, and the "
        "machine-readable payload goes in structured."
    ) in additions[1]
    assert "and source_commit pinning when it was measured." in additions[2]
    assert candidates[6].startswith("Whether the Specification entity folds into a knowledge kind")
    assert candidates[3].endswith("(before Phase C).")
    assert candidates[6].endswith("(before Phase C).")
    assert candidates[4].endswith("(Phase C).")


def test_only_phase_d_is_adr_first_while_phase_c_security_opens_a_disclosure_family() -> None:
    principle = _collapsed(" ".join(_section(ROADMAP, "### 3. A disclosure change is ADR-first")))
    readme = README.read_text(encoding="utf-8").splitlines()
    phases = _table(readme[readme.index("| Phase | Scope |") :])[1:]
    phase_c = {
        row[0]: _collapsed(row[1])
        for row in _table(_section(ROADMAP, "### Phase C — Traceability foundation"))
    }

    assert "most directly Phase D's history access" in principle
    assert {phase for phase, scope in phases if "ADR-first" in scope} == {"D"}
    assert phase_c["**Security**"].startswith("A graph response is a new disclosure family.")
    assert (
        "a traversal hop must not resolve an alias when deciding authority."
        in phase_c["**Security**"]
    )


def test_the_traceability_design_draws_a_spec_trailer_and_a_coverage_query() -> None:
    text = TRACEABILITY.read_text(encoding="utf-8")

    assert 'B["Commit trailers: Refs: spec.order-cancellation"]' in text
    assert "| `spec.getCoverage` |" in text


# -- Decision and the field table ---------------------------------------------


def test_knowledge_kind_has_no_specification_member_and_the_schema_publishes_the_same_set() -> None:
    published = set(_migration_defs()["kind"]["enum"])
    kinds = {member.value for member in KnowledgeKind}

    assert published
    assert kinds == published
    assert "specification" not in kinds
    assert "SPECIFICATION" not in KnowledgeKind.__members__


def test_a_trace_node_is_a_free_string_with_no_project_and_the_trace_types_stay_put() -> None:
    hints = typing.get_type_hints(TraceNode)

    assert hints == {"node_type": TraceNodeType, "node_id": str}
    assert "SPECIFICATION" in TraceNodeType.__members__
    assert {
        kind.__module__
        for kind in (TraceNode, TraceabilityEdge, TraceabilityRule, TraceabilityPolicy)
    } == {"theurian.domain.specification"}


#: The field-table rows whose home is not spelled ``KnowledgeItem.x`` or
#: ``KnowledgeRevision.x``, each with the words that name it. Every other row
#: must spell a real field of the specification item.
_HOMES_NAMED_OTHERWISE: Final = {
    "spec_id": ("The specification item's id.",),
    "status": ("The specification item's KnowledgeStatus",),
    "source_uri": ("The specification item's revision body:",),
    "revision_id": (
        "The link is to become a typed relation from the governing item to the specification item",
        "The pin — which of the governing item's revisions was current at registration — "
        "will not be carried",
        "#275's to decide",
    ),
    "superseded_by": ("A supersedes relation between two specification items",),
}
_NOT_CARRIED: Final = re.compile(
    r"\b(?:not (?:be )?carried|drop(?:s|ped)?|none|no home|lost|unrepresented)\b", re.IGNORECASE
)


def test_every_field_table_row_names_a_home_on_the_specification_item() -> None:
    table = _table(_section(_adr(), "### Where each `Specification` field lands"))[1:]
    rows = {re.findall(r"`(\w+)`", source)[0]: _collapsed(cell) for source, cell in table}
    fields = {
        "KnowledgeRevision": {field.name for field in dataclasses.fields(KnowledgeRevision)},
        "KnowledgeItem": {field.name for field in dataclasses.fields(KnowledgeItem)},
    }
    owed = _numbered(_section(_adr(), "## Compliance"))[3]

    assert rows.keys() == {field.name for field in dataclasses.fields(Specification)} | {
        "superseded_by"
    }, "the field table no longer maps exactly the entity's fields plus `superseded_by`"
    assert "superseded_by" in _ddl_tables()["specifications"]
    for field, cell in rows.items():
        spelled = re.findall(r"\b(KnowledgeRevision|KnowledgeItem)\.(\w+)", cell)
        named = _HOMES_NAMED_OTHERWISE.get(field, ())

        assert [(owner, name) for owner, name in spelled if name not in fields[owner]] == []
        assert [words for words in named if words not in cell] == [], field
        assert named or (spelled and cell.startswith("The specification item's ")), (
            f"the `{field}` row names no home on the specification item"
        )
    assert {field for field, cell in rows.items() if _NOT_CARRIED.search(cell)} == {
        "revision_id"
    }, "only the revision pin is owed rather than carried"
    assert owed.startswith("[#275](")
    assert "whether an edge or relation carries the revision pin the fold will drop" in owed
    assert "item_id" in fields["KnowledgeItem"]
    assert "body" in fields["KnowledgeRevision"]
    assert typing.get_type_hints(KnowledgeItem)["status"] is KnowledgeStatus
    assert RelationType("supersedes") in ACYCLIC_RELATIONS


def test_spec_ids_and_item_ids_share_one_grammar_and_one_schema_type() -> None:
    defs = _migration_defs()
    schema_types = {
        defs["opRegisterSpecification"]["properties"]["specId"]["$ref"],
        defs["opSupersedeSpecification"]["properties"]["specId"]["$ref"],
        defs["opSupersedeSpecification"]["properties"]["supersededBy"]["$ref"],
    }

    assert SpecId.__bases__ == (_DottedId,) == ItemId.__bases__
    assert "_validate" in vars(_DottedId), "positive control"
    assert "_validate" not in vars(SpecId)
    assert "_validate" not in vars(ItemId)
    assert ItemId("spec.order-cancellation").value == SpecId("spec.order-cancellation").value
    assert schema_types == {"#/$defs/itemId"}


def test_an_item_takes_its_kind_from_each_revision_it_is_moved_to() -> None:
    item = _item("domain.order-cancellation")
    revision = KnowledgeRevision.create(
        revision_id=RevisionId("01K1DEFREV01234567890ABCDE"),
        item_id=item.item_id,
        project_id=item.project_id,
        migration_id=MigrationId("01K1DEFABC01234567890ABCDE"),
        title="Order cancellation",
        body="openapi: 3.1.0\n",
        content_type=MediaType("application/yaml"),
        metadata=RevisionMetadata(
            kind=KnowledgeKind.API,
            namespace=item.namespace,
            status=item.status,
            trust_level=item.trust_level,
            sensitivity=item.sensitivity,
            owner=item.owner,
            labels=(AUTHORED_IN_THEURIAN,),
        ),
        validity=item.validity,
        author="orders-team@example.com",
        created_at=MOMENT,
    )
    upsert = _arms(_function(ENGINE, "_apply_operation"))["UpsertRevision"]

    moved = item.with_revision(revision)

    assert item.kind is KnowledgeKind.DOMAIN
    assert (moved.item_id, moved.kind) == (item.item_id, KnowledgeKind.API)
    assert "_upsert_revision" in _callees(upsert)
    assert "with_revision" in _callees(_function(ENGINE, "_upsert_revision"))


def test_deprecate_item_writes_the_acyclic_supersedes_relation() -> None:
    arm = _arms(_function(ENGINE, "_apply_operation"))["DeprecateItem"]
    relations = [
        _keywords(node)
        for node in ast.walk(arm)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "KnowledgeRelation"
    ]

    assert len(relations) == 1
    assert relations[0]["source_item_id"] == "operation.superseded_by"
    assert relations[0]["relation_type"] == "RelationType.SUPERSEDES"
    assert relations[0]["target_item_id"] == "operation.item_id"
    assert {RelationType.SUPERSEDES, RelationType.SUPERSEDED_BY} <= ACYCLIC_RELATIONS


def test_a_relation_names_two_items_and_no_revision_and_refuses_a_self_relation() -> None:
    hints = typing.get_type_hints(KnowledgeRelation)
    governing = ItemId("domain.order-cancellation")

    def relation(target: ItemId) -> KnowledgeRelation:
        return KnowledgeRelation(
            project_id=PROJECT,
            source_item_id=governing,
            relation_type=RelationType.RELATED_TO,
            target_item_id=target,
            created_at=MOMENT,
        )

    assert [name for name, hint in hints.items() if hint is ItemId] == [
        "source_item_id",
        "target_item_id",
    ]
    assert RevisionId not in hints.values()
    assert relation(ItemId("spec.order-cancellation")).source_item_id == governing, (
        "positive control"
    )
    with pytest.raises(InvariantViolationError):
        relation(governing)


def test_must_be_acyclic_has_no_caller_in_src() -> None:
    names = frozenset({"must_be_acyclic"})

    assert _references(names), "positive control: the tests read `must_be_acyclic`"
    assert _references(names, SRC) == set()


# -- Consequences: the gate the id space lands in -----------------------------


def test_the_relation_gate_reads_both_endpoints_through_the_non_resolving_metadata_read() -> None:
    gate = _function(TOOLS, "_relation_is_visible")
    called = _callees(gate)
    item_reads = {name for name in dir(CanonicalReadSession) if name.startswith("get_item")}
    loops = {ast.unparse(node.iter) for node in ast.walk(gate) if isinstance(node, ast.For)}

    assert {"get_item", "get_item_exact", "get_item_exact_metadata"} <= item_reads
    assert "(relation.source_item_id, relation.target_item_id)" in loops
    assert called & item_reads == {"get_item_exact_metadata"}, (
        "the gate reads an endpoint through another item read; `get_item` or "
        "`get_item_metadata` resolves an alias (T-21), `get_item_exact` joins the body (T-26)"
    )
    assert {"may_surface", "may_disclose"} <= called
    assert "_relation_is_visible" in _callees(_function(TOOLS, "knowledge_get"))


def test_get_item_exact_keeps_the_non_resolving_read_and_drops_the_body_free_one() -> None:
    readers = ("get_item_metadata", "get_item_exact", "get_item_exact_metadata")
    resolving = {name for name in readers if "_resolve_alias" in _callees(_function(STORE, name))}
    statements = {
        name: {node.id for node in ast.walk(_function(STORE, name)) if isinstance(node, ast.Name)}
        & {"_ITEM_METADATA_SQL", "_ITEM_WITH_CURRENT_CONTENT_SQL"}
        for name in readers[1:]
    }
    t26 = next(
        line
        for line in THREAT_MODEL.read_text(encoding="utf-8").splitlines()
        if line.startswith("#### T-26 ")
    )

    assert resolving == {"get_item_metadata"}
    assert statements == {
        "get_item_exact": {"_ITEM_WITH_CURRENT_CONTENT_SQL"},
        "get_item_exact_metadata": {"_ITEM_METADATA_SQL"},
    }
    assert re.search(r"\bbody\b", _ITEM_WITH_CURRENT_CONTENT_SQL, re.IGNORECASE)
    assert re.search(r"\bJOIN\b", _ITEM_WITH_CURRENT_CONTENT_SQL, re.IGNORECASE)
    assert not re.search(r"\bbody\b|\bJOIN\b", _ITEM_METADATA_SQL, re.IGNORECASE)
    assert "closed in 0.2.3" in t26


def test_the_relation_gate_withholds_a_missing_endpoint_and_reads_within_the_project() -> None:
    governing, document = _item("domain.order-cancellation"), _item("spec.order-cancellation")
    relation = KnowledgeRelation(
        project_id=PROJECT,
        source_item_id=governing.item_id,
        relation_type=RelationType.RELATED_TO,
        target_item_id=document.item_id,
        created_at=MOMENT,
    )
    read = _function(STORE, "get_item_exact_metadata")

    def visible(store: _Endpoints) -> bool:
        return _relation_is_visible(
            typing.cast("CanonicalReadSession", store),
            RequestContext(project_id=PROJECT),
            relation,
            include_unapproved=False,
            visible_sensitivities=frozenset(Sensitivity),
        )

    assert visible(_Endpoints(governing, document)), "positive control"
    assert not visible(_Endpoints(governing))
    assert not visible(_Endpoints(document))
    assert _ITEM_METADATA_SQL.endswith("FROM knowledge_items WHERE project_id = ? AND item_id = ?")
    assert "(context.project_id.value, item_id.value)" in {
        ast.unparse(node) for node in ast.walk(read) if isinstance(node, ast.Tuple)
    }


def test_no_traceability_tool_is_registered_and_capabilities_publish_it_false() -> None:
    registered = {
        ast.literal_eval(keyword.value)
        for node in ast.walk(_trees()[TOOLS])
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "_tool"
        for keyword in node.keywords
        if keyword.arg == "name"
    }
    published = [
        ast.literal_eval(value)
        for node in ast.walk(_function(TOOLS, "system_capabilities"))
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "traceability"
    ]

    assert {"knowledge.get", "system.capabilities"} <= registered, "positive control"
    assert [name for name in registered if "trace" in name.lower()] == []
    assert published == [False]


# -- Consequences: the closed sets the fold moves -----------------------------


@pytest.mark.parametrize(
    "schema", ["knowledge-propose-change-input", "review-generate-knowledge-candidate-input"]
)
def test_the_mcp_input_schema_types_kind_as_a_string_the_handler_closes(schema: str) -> None:
    path = REPO_ROOT / "schemas" / "mcp" / f"{schema}.schema.json"
    kind = json.loads(path.read_text(encoding="utf-8"))["properties"]["kind"]

    assert kind["type"] == "string"
    assert "enum" not in kind
    assert (
        "The closed set is `KnowledgeKind`; an unknown value is refused by the handler"
        in kind["description"]
    )


def test_adr_0005_closes_the_set_over_both_operations_and_governs_only_adding_one() -> None:
    text = ADR_0005.read_text(encoding="utf-8")
    closed = re.search(r"The operation set is closed: (.*?)\. Adding", " ".join(text.split()))
    residue = re.sub(r"\b(?:removeRelation|removeAlias|removeEvidence)\b", "", text)

    assert closed
    assert {"registerSpecification", "supersedeSpecification"} <= set(
        re.findall(r"`(\w+)`", closed.group(1))
    )
    assert (
        "Adding an operation is a protocol change and requires a version bump of apiVersion."
        in _collapsed(text)
    )
    assert re.search(r"(?i)remov", text), "positive control: the removal key reads this file"
    assert not re.search(r"(?i)remov|retir|delet|drop|withdr", residue)


def test_both_operations_are_published_admitted_to_drafts_and_parsed_into_their_own_classes() -> (
    None
):
    defs = _migration_defs()
    union = {branch["$ref"] for branch in defs["operation"]["oneOf"]}
    arms = _arms(_function(LOADER, "_parse_operation"))

    for operation, parsed_as in (
        ("registerSpecification", "RegisterSpecification"),
        ("supersedeSpecification", "SupersedeSpecification"),
    ):
        definition = f"op{operation[0].upper()}{operation[1:]}"
        returned = [
            ast.unparse(node.value.func)
            for node in ast.walk(arms[operation])
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
        ]

        assert defs[definition]["properties"]["op"]["const"] == operation
        assert f"#/$defs/{definition}" in union
        assert OperationKind(operation) in V1_OPERATION_KINDS
        assert returned == [parsed_as]


def test_two_exact_comparisons_are_the_only_compiled_api_version_checks() -> None:
    schema = json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))
    reads = {
        (path, scope)
        for path, tree in _trees(SRC).items()
        for node, scope in _scoped(tree)
        if _reads_the_version(node)
    }

    assert MIGRATION_API_VERSION == "theurian.dev/v1"
    assert schema["properties"]["apiVersion"]["const"] == MIGRATION_API_VERSION
    assert _api_version_checks(_trees(SRC)) == {
        (LOADER, "document['apiVersion'] != MIGRATION_API_VERSION"),
        (PROPOSALS, "document.get('apiVersion') != MIGRATION_API_VERSION"),
    }, "a third compiled apiVersion check, or a relaxed one: ADR-0038's `no window` moves with it"
    assert reads == {(LOADER, "_load_one"), (PROPOSALS, "_refuse_a_document_the_schema_rejects")}


@pytest.mark.parametrize(
    "source",
    [
        'ok = version not in {"theurian.dev/v1"}',
        "ACCEPTED = frozenset({MIGRATION_API_VERSION})\nok = version not in ACCEPTED",
        "V1 = migration.MIGRATION_API_VERSION\nok = version == V1",
        'ok = document.get("apiVersion") in accepted()',
        'match document["apiVersion"]:\n    case _:\n        pass',
        "match version:\n    case migration.MIGRATION_API_VERSION:\n        pass",
    ],
    ids=[
        "in-a-literal-set",
        "in-a-bound-name",
        "an-alias",
        "the-key-read",
        "match-key",
        "match-case",
    ],
)
def test_the_api_version_key_finds_each_shape_of_check(source: str) -> None:
    assert len(_api_version_checks({SNIPPET: ast.parse(source)})) == 1


def test_every_committed_dogfood_migration_is_v1_and_names_no_specification_operation() -> None:
    migrations = [path for path in _tracked(".theurian/migrations") if path.endswith(".yaml")]
    sample = (REPO_ROOT / SAMPLE_MIGRATION).read_text(encoding="utf-8")
    texts = {path: (REPO_ROOT / path).read_text(encoding="utf-8") for path in migrations}

    assert migrations, (
        "no committed dogfood migration was listed; the checks below would hold vacuously"
    )
    assert _NAMES_A_SPECIFICATION_OPERATION.search(sample), "positive control"
    assert [
        path
        for path, text in texts.items()
        if yaml.safe_load(text)["apiVersion"] != MIGRATION_API_VERSION
    ] == []
    assert [
        path for path, text in texts.items() if _NAMES_A_SPECIFICATION_OPERATION.search(text)
    ] == []


def test_only_the_sample_migration_names_a_specification_operation_in_committed_yaml() -> None:
    grep = _git(
        "grep", "-l", "-z", "-E", _NAMES_A_SPECIFICATION_OPERATION.pattern, "--", "*.yaml", "*.yml"
    )

    assert grep.returncode in {0, 1}, grep.stderr.decode("utf-8", "replace")
    assert set(filter(None, grep.stdout.decode("utf-8", "surrogateescape").split("\0"))) == {
        SAMPLE_MIGRATION
    }, "the retirement slice's owed list of documents to move is read off this set"


def test_the_two_status_vocabularies_are_not_a_rename_of_each_other() -> None:
    assert {status.value for status in SpecificationStatus} == {
        "draft",
        "active",
        "superseded",
        "retired",
    }
    assert {status.value for status in KnowledgeStatus} == {
        "draft",
        "proposed",
        "approved",
        "deprecated",
        "superseded",
        "rejected",
    }


# -- Neutral and the alternatives' reasons ------------------------------------


def test_the_schema_module_docstring_says_the_store_is_replayable_from_git() -> None:
    assert (
        "Every byte in it is reconstructible by replaying Git-tracked YAML migrations into an "
        "empty file"
    ) in _collapsed(sqlite_schema.__doc__ or "")


def test_specification_provider_has_no_implementation_and_returns_the_entity() -> None:
    assert _definitions("discover", "packages/") == {PROVIDER}
    assert (
        typing.get_type_hints(SpecificationProvider.discover)["return"] == tuple[Specification, ...]
    )


def test_unpopulated_tables_excludes_traceability_edges_alone() -> None:
    values = [
        node.value
        for node in ast.walk(_trees()[CORRUPTION_TEST])
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and "UNPOPULATED_TABLES"
        in {
            ast.unparse(target)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        }
    ]

    assert len(values) == 1
    assert isinstance(values[0], ast.Call)
    assert ast.unparse(values[0].func) == "frozenset"
    assert ast.literal_eval(values[0].args[0]) == {"traceability_edges"}


def test_the_engine_version_is_hashed_into_the_state_and_its_comment_says_why() -> None:
    lines = (REPO_ROOT / MIGRATION).read_text(encoding="utf-8").splitlines()
    declared = next(
        at for at, line in enumerate(lines) if line.startswith("MIGRATION_ENGINE_VERSION")
    )
    comment = reversed(
        list(itertools.takewhile(lambda line: line.startswith("#:"), reversed(lines[:declared])))
    )
    inputs = StateInputs(
        migrations=(), content_checksums=(), schema_version=sqlite_schema.SCHEMA_VERSION
    )
    bumped = dataclasses.replace(inputs, engine_version=MIGRATION_ENGINE_VERSION + 1)

    assert inputs.engine_version == MIGRATION_ENGINE_VERSION
    assert compute_state_hash(inputs) != compute_state_hash(bumped)
    assert (
        "so an engine change invalidates cached state instead of silently reinterpreting it "
        "(ADR-0007)"
    ) in _collapsed(" ".join(line.removeprefix("#:") for line in comment))


def test_the_alias_collision_guard_reads_alias_operations_and_never_a_specification_one() -> None:
    tree = _trees()[ALIAS_GUARDS]
    spellings = (
        {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        | {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
    )
    operation_classes = {operation.__name__ for operation in Operation.__subclasses__()}

    assert _definitions("refuse_alias_item_id_collision", SRC) == {ALIAS_GUARDS}
    assert "refuse_alias_item_id_collision" in _callees(
        _function(ENGINE, "run_static_migration_guards")
    )
    assert _arms(_function(ALIAS_GUARDS, "_final_alias_targets")).keys() == {
        "AddAlias",
        "RemoveAlias",
    }
    assert {"AddAlias", "RemoveAlias"} <= spellings & operation_classes
    assert not spellings & {
        "RegisterSpecification",
        "SupersedeSpecification",
        "REGISTER_SPECIFICATION",
        "SUPERSEDE_SPECIFICATION",
        "registerSpecification",
        "supersedeSpecification",
    }


def test_supersede_specification_is_a_bare_update_and_add_relation_an_insert_or_ignore() -> None:
    supersede = _function(STORE, "supersede_specification")
    attributes = {node.attr for node in ast.walk(supersede) if isinstance(node, ast.Attribute)}
    arm = _arms(_function(ENGINE, "_apply_operation"))["SupersedeSpecification"]
    updates = _executed_sql(supersede)
    inserts = _executed_sql(_function(STORE, "add_relation"))

    assert len(updates) == 1
    assert re.fullmatch(
        r"UPDATE specifications SET .+ WHERE project_id = \? AND spec_id = \?", updates[0]
    )
    assert _raises(_function(ENGINE, "_register_specification")), "positive control"
    assert not _raises(supersede)
    assert "execute" in attributes, "positive control"
    assert "rowcount" not in attributes
    assert [ast.unparse(statement) for statement in arm.body] == [
        "writer.supersede_specification(project_id, operation.spec_id, operation.superseded_by)"
    ]
    assert len(inserts) == 1
    assert inserts[0].startswith("INSERT OR IGNORE INTO knowledge_relations ")


# -- The prose half -----------------------------------------------------------

#: What ADR-0038 still has to say, in its own markup and wrapped at will: the
#: comparison collapses whitespace on both sides. Each entry's fact half is the
#: test above that carries the same claim.
ADR_STATES: Final[dict[str, str]] = {
    "measured-at": """**Every repository fact below was measured on 2026-09-29 against
        `origin/main` at `855ebd87`.**""",
    "decision-1": "1. **A specification is a knowledge item of `kind: specification`.**",
    "decision-3": "3. **Nothing new is built on the entity.**",
    "entity": """`domain/specification.py` declares a `Specification` entity with its own
        table (`CREATE TABLE specifications` in `infrastructure/sqlite/schema.py`), its own
        identifier (`SpecId`), its own status vocabulary (`SpecificationStatus`) and two
        migration operations, `registerSpecification` and `supersedeSpecification`.""",
    "edges-unbuilt": """`traceability_edges` is declared in `schema.py`, and
        `CanonicalStore.add_traceability_edge` and `list_traceability_edges` are declared on
        `domain/ports/canonical_store.py` with no implementation and no caller.""",
    "reference": """**The entity is a reference from a knowledge item to a separate document,
        pinned at one revision of that item.**""",
    "registration": """`application/migration_engine.py`'s `_register_specification` looks up
        the operation's `itemId`, refuses when that item has no current revision, and constructs
        a `Specification` whose `revision_id` is the item's `current_revision_id` and whose
        `title` is the spec id's own string; it passes no `structured` and no `anchors`.""",
    "only-link": "The entity has no item field, so that revision is its only link to the item.",
    "sample-document": """the one committed `registerSpecification`, the sample project's
        (*Negative*), registers `spec.order-cancellation` against `domain.order-cancellation`, a
        `kind: domain` item with a `text/markdown` revision, while its `sourceUri` and
        `format: application/yaml` name a different tracked file,
        `examples/sample-project/.theurian/specifications/order-cancellation.yaml`.""",
    "re-pin": """Only a registration writes `revision_id` (an upsert, so re-registering the spec
        id re-pins it); a later revision of the item leaves it,""",
    "supersede-sets": "`supersede_specification`, sets `status` and `superseded_by`.",
    "table-shape": """The table's `revision_id` is a foreign key to
        `knowledge_revisions(revision_id)`; the table has no `anchors` column and no
        `sensitivity` column.""",
    "decoder": """`_specification_from_row` builds a `Specification` without `anchors`, so a
        stored specification always reads back with `anchors=()`.""",
    "readers": """`store.py` implements two readers, `get_specification` and
        `list_specifications`, and they hold the only `FROM specifications` SQL in
        `packages/theurian-core/src`.""",
    "readers-uncalled": """No call of `.get_specification(` or `.list_specifications(` exists
        anywhere in the repository.""",
    "writes-only": """The only calls into the store's specification methods are the two writes
        in `migration_engine.py`, `register_specification` and `supersede_specification`.""",
    "okf-export": """The OKF export (`application/okf_export.py`) walks knowledge items,
        revisions and relations and never the `specifications` table""",
    "structured-spec": """`Specification.structured` defaults to an empty dict, and
        `_register_specification` never sets it.""",
    "structured-revision": """`KnowledgeRevision.structured` is declared, stored
        (`knowledge_revisions.structured`) and published by `knowledge.get` (`mcp/tools.py`),
        but no construction of a `KnowledgeRevision` in `packages/theurian-core/src` other than
        the store's row decoder passes `structured`: the migration engine's does not, and
        `KnowledgeRevision.create` has no caller there.""",
    "ingest-reader": """`application/ingestion_service.py` carries it into
        `IngestedDocument.structured`, whose only reader is the `theurian ingest` report.""",
    "fr-t1": "neither `structured` field contributes to it.",
    "roadmap-recommends": """the unified form: spec-as-knowledge in `kind`, the machine-readable
        payload in `structured`.""",
    "candidates-owed": """The roadmap lists candidates 3 and 6 as owed "before Phase C" and
        candidate 4, #275, as Phase C's own.""",
    "principle-3": """§6 principle 3 ("A disclosure change is ADR-first") names Phase D's
        history access as its most direct case, and the README marks only Phase D ADR-first.""",
    "security-row": """Phase C's own Security row nevertheless opens "A graph response is a new
        disclosure family\"""",
    "kind-absent": """`KnowledgeKind` has no `specification` member today, and this ADR does not
        add one""",
    "parsers-produce": """which the structured and OpenAPI parsers under
        `infrastructure/filesystem/parsers/` already produce at ingestion""",
    "trace-node": """A `TraceNode` whose `node_type` is `TraceNodeType.SPECIFICATION` is to carry
        a knowledge item id: the node type names the role, and the id space is the knowledge
        item id space.""",
    "refs-trailer": """[`traceability.md`](../architecture/traceability.md) draws a commit
        trailer, `Refs: spec.order-cancellation`""",
    "kind-rides": """`kind` rides on the revision: `KnowledgeItem.with_revision` adopts
        `kind=revision.metadata.kind` (`domain/knowledge.py`), so an item entered before
        `kind: specification` exists can be retyped by a later revision without changing the id
        its edges address.""",
    "same-grammar": "since `SpecId` shares `ItemId`'s grammar",
    "relation-no-revision": "a `KnowledgeRelation` names two items and no revision.",
    "pin-unread": "Nothing reads the pin today (*Context*).",
    "one-field-lacking": """It keeps a table whose one field the knowledge side lacks is the
        revision pin (the field table)""",
    "source-commit": "(roadmap §4 item 2's `source_commit` pinning)",
    "structured-column": """The payload's home moves from a column that exists,
        `specifications.structured TEXT NOT NULL DEFAULT '{}'`, to a field whose writer is
        owed""",
    "self-relation": """two specifications registered against one item would be a
        self-relation, which `KnowledgeRelation` refuses""",
    "supersedes-row": """which `deprecateItem`'s `supersededBy` already writes and which
        `ACYCLIC_RELATIONS` declares acyclic (INV-6). `KnowledgeRelation.must_be_acyclic` has
        no caller in `packages/theurian-core/src`""",
    "gate": """`mcp/tools.py`'s `_relation_is_visible` checks both endpoints of a relation, each
        read by the id it literally names through
        `CanonicalReadSession.get_item_exact_metadata`, which does not resolve an alias, against
        `may_surface` and `may_disclose`.""",
    "t21-corrected-form": '("a traversal hop must not resolve an alias when deciding authority")',
    "two-properties": """it does not resolve an alias (T-21), and it reads the endpoint's
        metadata, never its body""",
    "t26": "[T-26](../security/threat-model.md), closed in 0.2.3 by the metadata form.",
    "get-item-exact": """A hop read through `get_item_exact` keeps the first property and drops
        the second.""",
    "gate-others": """both endpoints judged, with no direction inference; a missing endpoint
        withheld; and the read scoped to the project, as `_ITEM_METADATA_SQL`'s `project_id`
        scopes it and a `TraceNode`, which carries no project, cannot.""",
    "no-trace-path": """No traceability read path exists for it to gate — `knowledge.trace` is
        not registered, `system.capabilities` publishes `traceability: false`, and
        `TraceNode.node_id` is a free string""",
    # The refusal itself is held by tests/integration/test_alias_item_id_collision.py.
    "alias-guard-keys": """`refuse_alias_item_id_collision` refuses an `addAlias` key equal to
        the id of a live, non-deprecated item. It takes alias keys from `addAlias` and
        `removeAlias` alone, so a spec id reaches it only as an `addAlias`""",
    "trace-types-stay": """but not `TraceNode`, `TraceabilityEdge`, `TraceabilityRule` or
        `TraceabilityPolicy`, which stay in `domain/specification.py`""",
    "get-coverage": "[`traceability.md`](../architecture/traceability.md)'s `spec.getCoverage`",
    "kind-published": "`schemas/migrations/migration.schema.json` publishes it as `$defs/kind`.",
    "mcp-kind": """type `kind` as a string whose description names `KnowledgeKind` as the closed
        set their handlers refuse against.""",
    "closed-set": """`registerSpecification` and `supersedeSpecification` are members of
        ADR-0005's closed operation set.""",
    "adr-0005": """ADR-0005 makes *adding* an operation "a protocol change" requiring an
        `apiVersion` bump and says nothing of removing one""",
    "schema-and-v1": """Both are in `schemas/migrations/migration.schema.json`
        (`$defs/opRegisterSpecification`, `$defs/opSupersedeSpecification`) and in
        `application/proposal_service.py`'s `V1_OPERATION_KINDS`""",
    "api-version": """The published schema pins `apiVersion` with `"const": "theurian.dev/v1"`,
        and two compiled checks refuse on exact equality against `MIGRATION_API_VERSION`
        (`"theurian.dev/v1"`, `domain/migration.py`):
        `infrastructure/filesystem/migration_loader.py`
        (`if document["apiVersion"] != MIGRATION_API_VERSION:`) and
        `application/proposal_service.py`
        (`if document.get("apiVersion") != MIGRATION_API_VERSION:`).""",
    "measured-corpus": "*What is committed, measured 2026-09-29 at `855ebd87`.*",
    "corpus-command": r"""(`git ls-tree -r --name-only HEAD .theurian/migrations/ |
        grep -c '\.yaml$'`)""",
    "version-command": """(`git grep -h '^apiVersion' -- .theurian/migrations/ | sort |
        uniq -c`""",
    "none-names-command": """none names either operation
        (`git grep -c -E 'registerSpecification|supersedeSpecification' --
        .theurian/migrations/` prints nothing).""",
    "yaml-population": """Across every committed `.yaml` and `.yml` file, the one naming either
        operation is the sample project's
        `examples/sample-project/.theurian/migrations/01K1DEFABC01234567890ABCDE-add-order-cancellation.yaml`
        (`git grep -l -E 'registerSpecification|supersedeSpecification' -- '*.yaml'
        '*.yml'`).""",
    "status-vocabularies": """It holds `draft`, `active`, `superseded` and `retired`;
        `KnowledgeStatus` holds `draft`, `proposed`, `approved`, `deprecated`, `superseded` and
        `rejected`.""",
    "no-counterpart": "`proposed` and `rejected` have no specification counterpart",
    "old-path": """A v1 document may still carry `registerSpecification`, including one drafted
        through `knowledge.generateMigrationDraft`, and it writes a row that no caller reads.""",
    "schema-docstring": """`infrastructure/sqlite/schema.py`'s module docstring: every byte is
        reconstructible by replaying the Git-tracked migrations into an empty file""",
    "dotted-id": """Both subclass `_DottedId` (`domain/identifiers.py`), and the migration schema
        types `specId` and `supersededBy` as `$defs/itemId`.""",
    "provider": """The port (`domain/ports/specification_provider.py`) has no implementation —
        no `def discover` exists under `packages/` outside it — and its `discover` returns
        `tuple[Specification, ...]`.""",
    "unpopulated": """excludes `traceability_edges`, and only that table, from its
        every-table-holds-a-row check.""",
    "walkers": """the index build, `knowledge.get`'s relations, the OKF export — none of which
        reads the `specifications` table today.""",
    "loader-classes": """the loader parses them into their own operation classes
        (`RegisterSpecification`, `SupersedeSpecification`)""",
    "engine-version": """`MIGRATION_ENGINE_VERSION` (`domain/migration.py`) is hashed into the
        state hash "so an engine change invalidates cached state instead of silently
        reinterpreting it (ADR-0007)\"""",
    "alias-guard": """The alias collision guard in `application/migration_alias_guards.py` reads
        `addAlias` and `removeAlias` and never `registerSpecification`""",
    "sql-semantics": """`supersedeSpecification` is an `UPDATE` of one `specifications` row —
        nothing when no row matches — where `addRelation` is an `INSERT OR IGNORE` into
        `knowledge_relations`""",
    "compliance": "`packages/theurian-core/tests/unit/test_adr_0038_claims.py`, the claims pin.",
}


@pytest.mark.parametrize("fragment", ADR_STATES.values(), ids=list(ADR_STATES))
def test_the_adr_still_states(fragment: str) -> None:
    assert _collapsed(fragment) in _collapsed(_adr().read_text(encoding="utf-8")), (
        f"ADR-0038 no longer states:\n\n  {_collapsed(fragment)}\n\nIf the fact half is GREEN, "
        f"the tree did not move and the record is what gets restored; if it is RED, the "
        f"sentence moves with the tree."
    )
