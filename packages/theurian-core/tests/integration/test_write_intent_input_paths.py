"""No write-intent input field is a path the daemon reads (ADR-0032 decision 2).

The ADR's "holds as shipped" sentence is false by name: the schemas declare
``sourceAnchors[].filePath``/``sourceUri``, ``scopePaths``, ``repository`` and
``fixCommit``. They are recorded, never dereferenced as paths, or (``fixCommit``)
dereferenced as a git object id confined by its hex ``pattern``. So the pin is on
the closed set of declared members, each path-shaped one with its reason, and on
dereference driven with a canary file, not on a name pattern.

Reach: the schemas are ``schemas/mcp`` input schemas whose ``x-theurian-tool`` is a
registered write-intent tool (:mod:`write_intent_population`). The closed set stops
at ``knowledge.generateMigrationDraft``'s ``document``, an opaque object at that
schema; its interior is covered separately, over ``migration.schema.json``'s
operation definitions for the kinds in ``V1_OPERATION_KINDS``. Canary drives cover
``knowledge.proposeChange`` and ``knowledge.generateMigrationDraft`` landing and
``review.generateKnowledgeCandidate``'s refused lookups; that tool's ``scopePaths``
and ``sourceAnchors`` are not driven. The scan sees a canary's bytes only in a file
under the test's ``tmp_path``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Final

import pytest
from write_intent_population import demo_project, registered_write_intent_names  # noqa: F401

from theurian.application.project_service import ProjectRegistry
from theurian.application.proposal_service import V1_OPERATION_KINDS
from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration

_SCHEMAS: Final = pathlib.Path(__file__).resolve().parents[4] / "schemas"
_BY_ID: Final = {
    schema["$id"]: schema
    for schema in (json.loads(p.read_text(encoding="utf-8")) for p in _SCHEMAS.rglob("*.json"))
    if "$id" in schema
}
_MIGRATION_ID: Final = "https://theurian.dev/schemas/migrations/migration.schema.json"
_CANARY_BYTES: Final = b"CANARY-9f3b2c71-never-read"

_CONTEXT: Final = "projectId snapshotId agentId taskId"
_EVIDENCE: Final = "evidence evidence.agentId evidence.taskId evidence.model evidence.reasoning"
_ANCHOR: Final = (
    "sourceAnchors sourceAnchors[].provider sourceAnchors[].sourceUri "
    "sourceAnchors[].repository sourceAnchors[].commitSha sourceAnchors[].filePath"
)
#: Every property path reachable from each schema, ``$ref`` followed (measured at
#: 6c3c2827 by walking ``schemas/mcp``). A path added anywhere reddens the pin.
_RECORDED: Final = {
    "knowledge.proposeChange": frozenset(
        f"{_CONTEXT} {_EVIDENCE} {_ANCHOR} itemId title kind owner author description body "
        "contentType labels scopePaths namespace trustLevel sensitivity expectedRevision "
        "local".split()
    ),
    "review.generateKnowledgeCandidate": frozenset(
        f"{_CONTEXT} {_EVIDENCE} {_ANCHOR} repository recordKey fixCommit itemId title body "
        "kind category owner author description labels scopePaths namespace "
        "expectedRevision".split()
    ),
    "knowledge.generateMigrationDraft": frozenset(f"{_CONTEXT} {_EVIDENCE} document local".split()),
}
_NO_DEREF: Final = "recorded, never dereferenced"
_PATH_SHAPED: Final = {
    **{
        (tool, path): _NO_DEREF
        for tool in ("knowledge.proposeChange", "review.generateKnowledgeCandidate")
        for path in (
            "sourceAnchors[].filePath",
            "sourceAnchors[].sourceUri",
            "sourceAnchors[].repository",
            "scopePaths",
        )
    },
    ("review.generateKnowledgeCandidate", "repository"): "store lookup key, refused if unknown",
    ("review.generateKnowledgeCandidate", "fixCommit"): "git object id, pattern-confined",
}
_PATH_NAMES: Final = frozenset(
    ["filePath", "sourceUri", "scopePaths", "repository", "fixCommit", "contentFile"]
)


def property_paths(
    node: Any, doc: dict[str, Any], prefix: str = "", stack: tuple[str, ...] = ()
) -> set[str]:
    """Dotted property paths under *node*, through ``$ref``, ``allOf/anyOf/oneOf`` and items."""
    found: set[str] = set()
    if not isinstance(node, dict):
        return found
    ref = node.get("$ref")
    if ref and ref not in stack:
        base, _, pointer = ref.partition("#")
        target_doc = _BY_ID[base] if base else doc
        target: Any = target_doc
        for part in filter(None, pointer.split("/")):
            target = target[part]
        found |= property_paths(target, target_doc, prefix, (*stack, ref))
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(key, ()):
            found |= property_paths(sub, doc, prefix, stack)
    for name, sub in node.get("properties", {}).items():
        path = f"{prefix}.{name}" if prefix else name
        found |= {path} | property_paths(sub, doc, path, stack)
    if "items" in node:
        found |= property_paths(node["items"], doc, f"{prefix}[]", stack)
    return found


def _input_schemas() -> dict[str, dict[str, Any]]:
    return {s["x-theurian-tool"]: s for s in _BY_ID.values() if "x-theurian-tool" in s}


def _tail(path: str) -> str:
    return path.rsplit(".", 1)[-1].removesuffix("[]")


@pytest.fixture
def server(tmp_path: pathlib.Path) -> Any:
    return build_server(ProjectRegistry.default(tmp_path / "datadir"))


def test_the_schemas_checked_are_the_registered_write_intent_tools(server: Any) -> None:
    registered = registered_write_intent_names(server)

    assert registered
    assert set(_RECORDED) == registered
    assert registered <= set(_input_schemas())


@pytest.mark.parametrize("tool", sorted(_RECORDED))
def test_a_write_intent_schema_declares_only_the_recorded_properties(tool: str) -> None:
    schema = _input_schemas()[tool]

    assert property_paths(schema, schema) == _RECORDED[tool]


def test_the_walk_flags_a_widened_schema_and_follows_a_ref() -> None:
    schema = {"allOf": [{"$ref": "#/$defs/a"}], "$defs": {"a": {"properties": {"x": {}}}}}
    widened = {**schema, "properties": {"contentFile": {"type": "string"}}}

    assert property_paths(schema, schema) == {"x"}
    assert property_paths(widened, widened) == {"x", "contentFile"}


def test_each_path_shaped_member_carries_a_reason() -> None:
    shaped = {
        (tool, path)
        for tool, paths in _RECORDED.items()
        for path in paths
        if _tail(path) in _PATH_NAMES
    }

    assert shaped == set(_PATH_SHAPED)


def _v1_operation_paths() -> dict[str, set[str]]:
    migration = _BY_ID[_MIGRATION_ID]
    return {
        kind.value: property_paths(
            migration["$defs"]["op" + kind.value[0].upper() + kind.value[1:]], migration
        )
        for kind in V1_OPERATION_KINDS
    }


def test_no_v1_admitted_operation_declares_the_dereferenced_body_file() -> None:
    """``contentFile`` is dereferenced by ``accept``; at 6c3c2827 only ``opUpsertRevision``
    declares it, and v1 does not admit that kind. RED driver, run at 6c3c2827:
    ``upsertRevision`` added to ``V1_OPERATION_KINDS`` in a private copy.
    """
    paths = _v1_operation_paths()

    assert len(paths) == len(V1_OPERATION_KINDS)
    assert {kind for kind, found in paths.items() if "contentFile" in found} == set()
    assert {
        (kind, p) for kind, found in paths.items() for p in found if _tail(p) in _PATH_NAMES
    } == {
        ("registerSpecification", "sourceUri"),
        ("removeEvidence", "sourceUri"),
        ("addEvidence", "anchor.sourceUri"),
        ("addEvidence", "anchor.filePath"),
        ("addEvidence", "anchor.repository"),
    }


# -- dereference, driven with a canary ---------------------------------------


_EVIDENCE_ARG: Final = {"agentId": "a", "taskId": "t", "model": "m", "reasoning": "r"}


def _canary(tmp_path: pathlib.Path) -> str:
    canary = tmp_path / "canary.txt"
    canary.write_bytes(_CANARY_BYTES)
    return str(canary)


def _written(tmp_path: pathlib.Path) -> bytes:
    return b"".join(
        p.read_bytes()
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file() and p.name != "canary.txt" and ".git" not in p.parts
    )


def _call(demo: ProjectRegistry, tmp_path: pathlib.Path, tool: str, args: dict[str, Any]) -> Any:
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        return call(tool, {"projectId": "demo", **args})["result"]


def test_a_path_in_a_recorded_field_is_stored_as_text_and_never_read(
    demo: ProjectRegistry, tmp_path: pathlib.Path
) -> None:
    """Drives the scan's own positive control: the path *text* lands, its bytes do not.
    RED driver: the handler appending ``Path(scopePaths[0]).read_bytes()`` to the body,
    planted in a private copy (``body + Path(scopePaths[0]).read_text()`` after the
    facade is built in ``proposeChange``) and run at 6c3c2827 (RED); the refused candidate
    lookups below see a read only if its bytes reach a written file.
    """
    canary = _canary(tmp_path)

    result = _call(
        demo,
        tmp_path,
        "knowledge.proposeChange",
        {
            "itemId": "architecture.canary",
            "title": "T",
            "kind": "architecture",
            "owner": "o",
            "author": "a@example.com",
            "description": "d",
            "body": "# T\n",
            "contentType": "text/markdown",
            "evidence": _EVIDENCE_ARG,
            "scopePaths": [canary],
            "sourceAnchors": [{"provider": "git", "sourceUri": canary, "filePath": canary}],
        },
    )

    assert result["isError"] is False, result
    written = _written(tmp_path)
    assert canary.encode() in written
    assert _CANARY_BYTES not in written


def test_a_v1_operation_path_is_stored_as_text_and_a_content_file_is_refused_unread(
    demo: ProjectRegistry, tmp_path: pathlib.Path
) -> None:
    """A v1 op's ``sourceUri`` lands as text; an ``upsertRevision`` ``contentFile`` is refused.
    RED driver, run at 6c3c2827: the handler appending ``Path(<first op's sourceUri>)
    .read_text()`` to the document's ``author`` before drafting, planted in a private copy.
    """
    canary = _canary(tmp_path)
    evidence = {"op": "removeEvidence", "itemId": "architecture.x", "sourceUri": canary}
    upsert = {"op": "upsertRevision", "itemId": "architecture.x", "contentFile": canary}

    landed = _call(
        demo,
        tmp_path,
        "knowledge.generateMigrationDraft",
        {
            "document": {"author": "a@example.com", "operations": [evidence]},
            "evidence": _EVIDENCE_ARG,
        },
    )
    after_landing = _written(tmp_path)
    refused = _call(
        demo,
        tmp_path,
        "knowledge.generateMigrationDraft",
        {
            "document": {"author": "a@example.com", "operations": [upsert]},
            "evidence": _EVIDENCE_ARG,
        },
    )

    assert landed["isError"] is False, landed
    assert canary.encode() in after_landing
    assert refused["isError"] is True, refused
    assert _written(tmp_path) == after_landing
    assert _CANARY_BYTES not in _written(tmp_path)


@pytest.mark.parametrize("field", ["repository", "recordKey", "fixCommit"])
def test_a_canary_path_in_a_candidate_lookup_field_is_refused_with_nothing_written(
    demo: ProjectRegistry, tmp_path: pathlib.Path, field: str
) -> None:
    canary = _canary(tmp_path)
    args: dict[str, Any] = {
        "repository": "acme/x",
        "recordKey": "k",
        "fixCommit": "a" * 40,
        "itemId": "architecture.c",
        "title": "T",
        "body": "# T\n",
        "kind": "architecture",
        "category": "c",
        "owner": "o",
        "author": "a@example.com",
        "description": "d",
        "evidence": _EVIDENCE_ARG,
        "sourceAnchors": [{"provider": "git", "sourceUri": "git://x"}],
        field: canary,
    }
    before = _written(tmp_path)

    result = _call(demo, tmp_path, "review.generateKnowledgeCandidate", args)

    assert result["isError"] is True, result
    assert _written(tmp_path) == before
