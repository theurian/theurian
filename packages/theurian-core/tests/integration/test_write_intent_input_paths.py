"""No write-intent input field is a path the daemon reads (ADR-0032 decision 2).

The ADR's "holds as shipped" sentence is false by name: the schemas declare
``sourceAnchors[].filePath``/``sourceUri``, ``scopePaths``, ``repository`` and
``fixCommit``. They are recorded, never dereferenced as paths, or (``fixCommit``)
dereferenced as a git object id confined by its hex ``pattern``. So the pin is on
the closed set of declared members, each path-shaped one with its reason, and on
dereference driven with a canary file. The reason requirement is keyed on
``_PATH_NAMES``, a name list; the closed set is not.

Reach: the schemas are ``schemas/mcp`` input schemas whose ``x-theurian-tool`` is a
registered write-intent tool (:mod:`write_intent_population`). The closed set stops
at ``knowledge.generateMigrationDraft``'s ``document``, an opaque object at that
schema; its interior is outside this file. Canary drives cover
``knowledge.proposeChange`` landing, and
``review.generateKnowledgeCandidate``'s refused ``repository`` and ``recordKey``
(canary path) and its schema-refused path-shaped ``fixCommit`` (no canary file);
that tool's ``scopePaths`` and ``sourceAnchors`` are not driven. The scan sees a
canary's bytes only in a file under the test's ``tmp_path`` or in the tool's
response. The schema walk raises on any keyword outside ``_WALKED`` and ``_INERT``,
so a path added under one is refused rather than missed.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any, Final

import pytest
import review_candidate_fixtures as corpus
from review_candidate_project import ServedProject, served_project
from write_intent_population import demo_project, registered_write_intent_names  # noqa: F401

from theurian.application.candidate_generation import UNRESOLVED_RECORD_REFUSAL
from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration

_SCHEMAS: Final = pathlib.Path(__file__).resolve().parents[4] / "schemas"
_BY_ID: Final = {
    schema["$id"]: schema
    for schema in (json.loads(p.read_text(encoding="utf-8")) for p in _SCHEMAS.rglob("*.json"))
    if "$id" in schema
}
_CANARY_BYTES: Final = b"CANARY-9f3b2c71-never-read"

_CONTEXT: Final = "projectId snapshotId agentId taskId"
_EVIDENCE: Final = "evidence evidence.agentId evidence.taskId evidence.model evidence.reasoning"
_ANCHOR: Final = (
    "sourceAnchors sourceAnchors[].provider sourceAnchors[].sourceUri "
    "sourceAnchors[].repository sourceAnchors[].commitSha sourceAnchors[].filePath"
)
#: Every property path reachable from each schema, ``$ref`` followed (measured at
#: 6c3c2827 by walking ``schemas/mcp``). A path added under a keyword the walk
#: follows reddens the pin; one added under any other keyword raises.
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


#: Keywords the walk descends, and annotations or constraints that cannot add a
#: member. A node carrying any other key raises, so an applicator the walk does not
#: implement (``patternProperties``, ``prefixItems``, ``if``, ...) is refused, not missed.
_WALKED: Final = frozenset(["$ref", "allOf", "anyOf", "oneOf", "properties", "items", "$defs"])
_INERT: Final = frozenset(
    (  # noqa: SIM905
        "type description title enum const pattern maxLength minLength minimum maximum "
        "minItems maxItems uniqueItems required format default examples $id $schema "
        "x-theurian-tool"
    ).split()
)
_CLOSED_FLAGS: Final = ("additionalProperties", "unevaluatedProperties")


def property_paths(
    node: Any, doc: dict[str, Any], prefix: str = "", stack: tuple[str, ...] = ()
) -> set[str]:
    """Dotted property paths under *node*, through ``$ref``, ``allOf/anyOf/oneOf`` and items."""
    found: set[str] = set()
    if not isinstance(node, dict):
        return found
    for key, value in node.items():
        closed_flag = key in _CLOSED_FLAGS and value is False
        if key not in _WALKED | _INERT and not closed_flag:
            raise ValueError(f"{key} at {prefix or '<root>'} is not a keyword the walk follows")
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


_APPLICATORS: Final = (  # noqa: SIM905
    "patternProperties prefixItems dependentSchemas if then else contains propertyNames "
    "not additionalProperties unevaluatedProperties"
).split()


@pytest.mark.parametrize("keyword", _APPLICATORS)
def test_the_walk_refuses_an_applicator_it_does_not_follow(keyword: str) -> None:
    node: dict[str, Any] = {keyword: {"properties": {"contentFile": {}}}}

    with pytest.raises(ValueError, match="not a keyword the walk follows"):
        property_paths(node, node)


def test_the_walk_accepts_a_closed_additional_properties_flag() -> None:
    node = {"additionalProperties": False, "unevaluatedProperties": False, "properties": {"a": {}}}

    assert property_paths(node, node) == {"a"}


def test_each_path_shaped_member_carries_a_reason() -> None:
    shaped = {
        (tool, path)
        for tool, paths in _RECORDED.items()
        for path in paths
        if _tail(path) in _PATH_NAMES
    }

    assert shaped == set(_PATH_SHAPED)


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
        if p.is_file() and p != tmp_path / "canary.txt" and ".git" not in p.parts
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
    facade is built in ``proposeChange``) and run at 6c3c2827 (RED).
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
            "sourceAnchors": [
                {"provider": "git", "sourceUri": canary, "filePath": canary, "repository": canary}
            ],
        },
    )

    assert result["isError"] is False, result
    written = _written(tmp_path)
    assert canary.encode() in written
    assert _CANARY_BYTES not in written
    assert _CANARY_BYTES not in json.dumps(result).encode()


@pytest.fixture
def served(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServedProject]:
    yield from served_project(
        tmp_path, monkeypatch, records=corpus.evidence_records(), withheld=frozenset()
    )


def _candidate_args(project: ServedProject, **overrides: Any) -> dict[str, Any]:
    return {
        "repository": corpus.REPOSITORY,
        "recordKey": corpus.THREAD_SATISFYING,
        "fixCommit": project.verifying,
        "itemId": "reliability.retry-lock-order",
        "title": "Acquire locks after reads",
        "body": "Acquire locks after reads.\n",
        "kind": "convention",
        "category": "reliability-rule",
        "owner": "platform-team",
        "author": "dana@example.com",
        "description": "Generalise the thread",
        "evidence": {"agentId": "a", "taskId": "t", "model": "m", "reasoning": "r"},
        "sourceAnchors": [{"provider": "github", "sourceUri": "https://example.com/x"}],
        **overrides,
    }


def _candidate_call(project: ServedProject, tmp_path: pathlib.Path, **overrides: Any) -> Any:
    with mcp_session(build_server(project.registry), tmp_path / "data") as call:
        answer = call(
            "review.generateKnowledgeCandidate",
            {"projectId": "demo", **_candidate_args(project, **overrides)},
        )
    return answer["result"]


def test_the_candidate_call_the_canary_drives_vary_is_a_control_that_lands(
    served: ServedProject, tmp_path: pathlib.Path
) -> None:
    """Without it, the refusals below would hold for a call that refuses everything."""
    result = _candidate_call(served, tmp_path)

    assert result["isError"] is False, result


@pytest.mark.parametrize("field", ["repository", "recordKey"])
def test_a_canary_path_in_a_candidate_lookup_key_is_refused_by_the_resolve(
    served: ServedProject, tmp_path: pathlib.Path, field: str
) -> None:
    """The refusal is the resolve's own constant, not the schema's or another gate's.
    Sees a read only if its bytes reach a written file or the response. RED driver, run
    at 6c3c2827: a handler that reads the ``repository``/``recordKey`` file and then
    raises ``REVIEW_SEARCH_UNAVAILABLE_REFUSAL``, planted in a private copy.
    """
    canary = _canary(tmp_path)

    result = _candidate_call(served, tmp_path, **{field: canary})

    assert result["isError"] is True, result
    assert UNRESOLVED_RECORD_REFUSAL in result["content"][0]["text"], result
    assert _CANARY_BYTES not in _written(tmp_path)
    assert _CANARY_BYTES not in json.dumps(result).encode()
    assert served.proposals() == set()


def test_a_path_shaped_fix_commit_is_refused_at_the_schema_naming_the_field(
    served: ServedProject, tmp_path: pathlib.Path
) -> None:
    """A 40-character non-hex value passes ``maxLength`` and reaches the ``pattern``;
    the SEC-12 refusal is asserted, not the promotion gate's own. RED driver, run at
    6c3c2827: ``pattern`` and ``maxLength`` both removed from the candidate schema's
    ``fixCommit`` in a private copy.
    """
    result = _candidate_call(served, tmp_path, fixCommit="/" + "g" * 39)

    text = result["content"][0]["text"]
    assert result["isError"] is True, result
    assert "does not match this tool's published input schema" in text, text
    assert "At fixCommit" in text, text
    assert _CANARY_BYTES not in json.dumps(result).encode()
    assert served.proposals() == set()
