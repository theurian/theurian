"""ADR-0038's populations: who references, reads, writes and defines what.

Each scan's answer is an exact set, read off the tree through
``adr_0038_support``. ``test_adr_0038_claims.py`` names the rest of the pin.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
import typing
from typing import Final

import pytest
from adr_0038_support import (
    _READS_SPECIFICATIONS,
    _WRITES_SPECIFICATIONS,
    ALIAS_GUARDS,
    COMMANDS,
    ENGINE,
    INGESTION,
    KNOWLEDGE,
    LOADER,
    MIGRATION_SCHEMA,
    PARSERS,
    PORT,
    PROPOSALS,
    PROVIDER,
    SNIPPET,
    SRC,
    STORE,
    TOOLS,
    _api_version_checks,
    _arms,
    _callees,
    _constructions,
    _ddl_tables,
    _definitions,
    _executed_sql,
    _function,
    _raises,
    _reads_the_version,
    _references,
    _revision_creates,
    _scoped,
    _sites,
    _sql,
    _structured_read,
    _trees,
    _whole_object_read,
)

from theurian.application.index_builder import IndexBuilder
from theurian.application.okf_export import OkfExporter, OkfExportSession
from theurian.domain.knowledge import KnowledgeRevision
from theurian.domain.migration import (
    MIGRATION_API_VERSION,
    Operation,
)
from theurian.domain.ports.canonical_store import (
    CanonicalReadSession,
    CanonicalStore,
    IndexBuildSession,
)
from theurian.domain.ports.specification_provider import SpecificationProvider
from theurian.domain.specification import Specification

pytestmark = pytest.mark.unit


_SPECIFICATION_METHODS: Final = frozenset(
    {
        "get_specification",
        "list_specifications",
        "register_specification",
        "supersede_specification",
    }
)
_SPECIFICATION_READERS: Final = frozenset({"get_specification", "list_specifications"})


# -- Context: the entity's store methods and the table's SQL ------------------


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


# -- Consequences: the compiled apiVersion checks -----------------------------


_API_VERSION_REFUSALS: Final = {
    (LOADER, "_load_one"): "document['apiVersion'] != MIGRATION_API_VERSION",
    (PROPOSALS, "_refuse_a_document_the_schema_rejects"): (
        "document.get('apiVersion') != MIGRATION_API_VERSION"
    ),
}


def test_two_exact_comparisons_are_the_only_compiled_api_version_checks() -> None:
    schema = json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))
    reads = {
        (path, scope)
        for path, tree in _trees(SRC).items()
        for node, scope in _scoped(tree)
        if _reads_the_version(node)
    }
    # Key: each site's top-level `if` whose test reads the key -- its whole test and its body's
    # statement types. An earlier `return` in the same function is not held.
    refusals = {
        site: [
            (ast.unparse(statement.test), [type(inner) for inner in statement.body])
            for statement in _function(*site).body
            if isinstance(statement, ast.If)
            and any(map(_reads_the_version, ast.walk(statement.test)))
        ]
        for site in _API_VERSION_REFUSALS
    }

    assert MIGRATION_API_VERSION == "theurian.dev/v1"
    assert schema["properties"]["apiVersion"]["const"] == MIGRATION_API_VERSION
    assert _api_version_checks(_trees(SRC)) == {
        (path, comparison) for (path, _), comparison in _API_VERSION_REFUSALS.items()
    }, "a third compiled apiVersion check: ADR-0038's `no window` moves with it"
    assert refusals == {
        site: [(comparison, [ast.Raise])] for site, comparison in _API_VERSION_REFUSALS.items()
    }, (
        "a check is no longer a top-level `if` of exactly its comparison whose body is one "
        "`raise` -- an added conjunct, an enclosing guard or a body that may not raise opens "
        "the window ADR-0038 says v1 lacks"
    )
    assert reads == set(_API_VERSION_REFUSALS)


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


# -- Decision and Neutral: code with no caller or no specification reader -----


def test_must_be_acyclic_has_no_caller_in_src() -> None:
    names = frozenset({"must_be_acyclic"})

    assert _references(names), "positive control: the tests read `must_be_acyclic`"
    assert _references(names, SRC) == set()


def test_specification_provider_has_no_implementation_and_returns_the_entity() -> None:
    assert _definitions("discover", "packages/") == {PROVIDER}
    assert (
        typing.get_type_hints(SpecificationProvider.discover)["return"] == tuple[Specification, ...]
    )


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


def test_the_alias_guard_takes_its_keys_from_final_alias_targets_alone() -> None:
    collisions = _function(ALIAS_GUARDS, "_alias_item_collisions")

    assert _callees(_function(ALIAS_GUARDS, "refuse_alias_item_id_collision")) == {
        "_alias_item_collisions",
        "AliasItemCollisionError",
    }
    assert _callees(collisions) == {
        "_final_alias_targets",
        "_final_item_statuses",
        "_AliasCollision",
        "get",
        "sorted",
    }, (
        "the collision walk calls something new, which may be a second alias producer; "
        "ADR-0038's Still owed item 2 says only `_final_alias_targets` feeds the guard"
    )
    assert "targets = _final_alias_targets(migration_set)" in {
        ast.unparse(statement) for statement in collisions.body
    }
