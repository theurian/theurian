"""ADR-0038's repository facts, held against the tree they were measured on.

ADR-0038 decides that the ``Specification`` entity folds into a knowledge
``kind``; the branch where the entity stays separate is the rejected one. The
ADR ships no code, so every sentence it states about the codebase was measured
once, at ``855ebd87``, and the slices written against it -- #274's policy,
#275's edge representation, the retirement -- read those sentences rather than
the tree.

Each claim is held from both sides: a fact half recomputed from live source,
RED when the tree moves, and a prose half (:func:`test_the_adr_still_states`),
RED when the record drifts. A new reference to a specification reader -- the
shape a breach of decision 3 takes -- reddens
:func:`test_the_store_specification_methods_are_reached_only_by_the_engines_two_writes`.

**Reach.** The scans parse every ``.py`` file ``git ls-files`` lists and see
attribute references, bare names and exact-string spellings (``getattr``).
They do not see a name held in a variable, a keyword smuggled through
``**kwargs``, or SQL assembled from fragments. Four files are not parsed: this
module, which spells every name it searches for as data (tracked, it would find
itself); ``domain/enums.py``, read only by importing its enums; and
``mcp/results.py`` and ``tests/unit/test_gate_call_sites.py``, fenced for a
concurrent lane when this pin landed -- lifting that is deleting their entries
from :data:`_UNREAD`.
Every scan whose expected answer is *nothing* runs beside a positive control on
the same key, because a broken walk and a clean tree look alike from outside.

Pure: syntax trees, JSON schemas, Markdown, and read-only ``git ls-files`` and
``git grep``. No database, socket or temporary directory.
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
import re
import shutil
import sqlite3
import subprocess
import typing
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from theurian.application.index_builder import IndexBuilder
from theurian.application.okf_export import OkfExporter, OkfExportSession
from theurian.application.proposal_service import V1_OPERATION_KINDS
from theurian.domain.enums import (
    ACYCLIC_RELATIONS,
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    SpecificationStatus,
    TraceNodeType,
)
from theurian.domain.identifiers import ItemId, ProjectId, RevisionId, SpecId, _DottedId
from theurian.domain.knowledge import KnowledgeItem, KnowledgeRevision
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
from theurian.domain.specification import Specification
from theurian.domain.state import StateInputs, compute_state_hash
from theurian.domain.values import MediaType, ValidityPeriod
from theurian.infrastructure.sqlite import schema as sqlite_schema
from theurian.infrastructure.sqlite.store import (
    _ITEM_METADATA_SQL,
    _ITEM_WITH_CURRENT_CONTENT_SQL,
    _specification_from_row,
)

pytestmark = pytest.mark.unit

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"
README: Final = REPO_ROOT / "README.md"
THREAT_MODEL: Final = REPO_ROOT / "docs" / "security" / "threat-model.md"
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
_READS_SPECIFICATIONS: Final = re.compile(r"\b(?:FROM|JOIN)\s+specifications\b", re.IGNORECASE)
_NAMES_A_SPECIFICATION_OPERATION: Final = re.compile(
    r"registerSpecification|supersedeSpecification"
)


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


def _constructions(class_name: str) -> list[tuple[str, str, ast.Call]]:
    return [
        (path, scope, node)
        for path, tree in _trees(SRC).items()
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == class_name
    ]


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


def _is_api_version(node: ast.expr) -> bool:
    match node:
        case (
            ast.Name(id="MIGRATION_API_VERSION")
            | ast.Attribute(attr="MIGRATION_API_VERSION")
            | ast.Constant(value="theurian.dev/v1")
        ):
            return True
    return False


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


def test_register_specification_builds_a_sidecar_of_the_items_current_revision() -> None:
    function = _function(ENGINE, "_register_specification")
    constructions = _constructions("Specification")
    engine_call = next(call for path, _, call in constructions if path == ENGINE)
    keywords = _keywords(engine_call)

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
    trees = _trees(SRC)
    reading_sql = {
        (path, scope)
        for path, tree in trees.items()
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _READS_SPECIFICATIONS.search(node.value)
    }

    for reader in _SPECIFICATION_READERS:
        assert _definitions(reader, SRC) == {PORT, STORE}
    assert reading_sql == {(STORE, "get_specification"), (STORE, "list_specifications")}


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

    assert parsed_forms, "the parsers no longer produce a parsed form"
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


def test_the_structured_readers_are_the_ingest_report_the_two_store_writes_and_knowledge_get() -> (
    None
):
    readers = {
        (path, scope, ast.unparse(node.value))
        for path, tree in _trees(SRC).items()
        for node, scope in _scoped(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "structured"
        and isinstance(node.ctx, ast.Load)
    }
    assignments = {
        ast.unparse(node)
        for node in ast.walk(_function(TOOLS, "knowledge_get"))
        if isinstance(node, ast.Assign)
    }

    assert "structured" in {field.name for field in dataclasses.fields(KnowledgeRevision)}
    assert "structured" in _ddl_tables()["knowledge_revisions"]
    assert "payload['structured'] = revision.structured" in assignments
    assert readers == {
        (INGESTION, "_to_document", "normalized"),
        (COMMANDS, "ingest_command", "d"),
        (STORE, "append_revision", "revision"),
        (STORE, "register_specification", "specification"),
        (TOOLS, "knowledge_get", "revision"),
    }, "a new reader of a `structured` field; ADR-0038's Context and #834's owed item move with it"


# -- Context: what the roadmap and the README say ------------------------------


def test_the_roadmap_recommends_the_fold_and_owes_candidates_three_and_six_before_phase_c() -> None:
    additions = _numbered(_section(ROADMAP, "### Four additions"))
    candidates = _numbered(_section(ROADMAP, "## 9. ADR candidates"))

    assert "ADR candidate #6" in additions[1]
    assert (
        "The recommendation is the unified form: spec-as-knowledge goes in kind, and the "
        "machine-readable payload goes in structured."
    ) in additions[1]
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


# -- Decision and the field table ---------------------------------------------


def test_knowledge_kind_has_no_specification_member_and_the_schema_publishes_the_same_set() -> None:
    published = set(_migration_defs()["kind"]["enum"])
    kinds = {member.value for member in KnowledgeKind}

    assert published
    assert kinds == published
    assert "specification" not in kinds
    assert "SPECIFICATION" not in KnowledgeKind.__members__


def test_trace_nodes_and_the_closed_operation_set_carry_the_specification_roles() -> None:
    assert {"SPECIFICATION", "KNOWLEDGE", "DECISION"} <= TraceNodeType.__members__.keys()
    assert {"registerSpecification", "supersedeSpecification"} <= {
        kind.value for kind in OperationKind
    }


def test_the_field_table_maps_every_specification_field_to_a_real_knowledge_side_target() -> None:
    rows = _table(_section(_adr(), "### Where each `Specification` field lands"))[1:]
    mapped = {re.findall(r"`(\w+)`", source)[0] for source, _ in rows}
    targets = {
        target
        for _, cell in rows
        for target in re.findall(r"\b(KnowledgeRevision|KnowledgeItem)\.(\w+)", cell)
    }
    fields = {
        "KnowledgeRevision": {field.name for field in dataclasses.fields(KnowledgeRevision)},
        "KnowledgeItem": {field.name for field in dataclasses.fields(KnowledgeItem)},
    }
    specification_fields = {field.name for field in dataclasses.fields(Specification)}

    assert mapped == specification_fields | {"superseded_by"}, (
        "the field table no longer maps exactly the entity's fields plus the table's "
        "`superseded_by` column, so ADR-0038's `Nothing is lost` has a field with no home"
    )
    assert "superseded_by" in _ddl_tables()["specifications"]
    assert "superseded_by" not in specification_fields
    assert targets
    assert {(owner, name) for owner, name in targets if name not in fields[owner]} == set()


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
    comparisons = {
        (path, ast.unparse(node))
        for path, tree in _trees(SRC).items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(map(_is_api_version, (node.left, *node.comparators)))
    }

    assert MIGRATION_API_VERSION == "theurian.dev/v1"
    assert schema["properties"]["apiVersion"]["const"] == MIGRATION_API_VERSION
    assert comparisons == {
        (LOADER, "document['apiVersion'] != MIGRATION_API_VERSION"),
        (PROPOSALS, "document.get('apiVersion') != MIGRATION_API_VERSION"),
    }, "a third compiled apiVersion check, or a relaxed one: ADR-0038's `no window` moves with it"


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
    "sidecar": """`application/migration_engine.py`'s `_register_specification` looks up the
        operation's `itemId`, refuses when that item has no current revision, and constructs a
        `Specification` whose `revision_id` is the item's `current_revision_id` and whose
        `title` is the spec id's own string; it passes no `structured` and no `anchors`.""",
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
    "trace-node": """A `TraceNode` whose `node_type` is `TraceNodeType.SPECIFICATION` carries a
        knowledge item id: the node type names the role, and the id space is the knowledge item
        id space that `KNOWLEDGE` and `DECISION` nodes share.""",
    "same-grammar": "since `SpecId` shares `ItemId`'s grammar",
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
