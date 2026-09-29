"""ADR-0038's model: the entity, the knowledge side it folds into, and the gate.

The entity's fields, table and registration; where each field lands on the
specification item; and the relation gate the id space lands in.
``test_adr_0038_claims.py`` names the rest of the pin.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import sqlite3
import typing
from datetime import UTC, datetime
from typing import Final

import pytest
from adr_0038_support import (
    ENGINE,
    SNIPPET,
    STORE,
    THREAT_MODEL,
    TOOLS,
    _adr,
    _arms,
    _callees,
    _collapsed,
    _constructions,
    _ddl_tables,
    _function,
    _keywords,
    _migration_defs,
    _numbered,
    _raises,
    _section,
    _table,
    _trees,
)

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
from theurian.domain.ports.canonical_store import CanonicalReadSession
from theurian.domain.specification import (
    Specification,
    TraceabilityEdge,
    TraceabilityPolicy,
    TraceabilityRule,
    TraceNode,
)
from theurian.domain.values import MediaType, ValidityPeriod
from theurian.infrastructure.sqlite.store import (
    _ITEM_METADATA_SQL,
    _ITEM_WITH_CURRENT_CONTENT_SQL,
    _specification_from_row,
)
from theurian.mcp.tools import _relation_is_visible

pytestmark = pytest.mark.unit


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


# -- Context: the entity, its table and its registration ----------------------


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


# -- Consequences: the two status vocabularies --------------------------------


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
