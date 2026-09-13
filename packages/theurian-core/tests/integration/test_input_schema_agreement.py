"""The published input schema and the SDK-derived one must agree (ADR-0031 decision 6).

Two descriptions of every tool's input exist: the file under ``schemas/mcp/``
that clients read, and the schema ``mcp==2.1.1`` computes from the handler's
annotations. Two descriptions of one thing drift, and the ADR's answer is a test
that **recomputes the agreement from both live sides** rather than a reviewer
comparing them by eye.

The relation is stated here, in full, because a relation nobody wrote down is
one a later contributor loosens to make a failure go away.

## The relation

For every tool the built server registers, with ``P`` the *effective* published
schema (the file, with its one ``allOf`` ``$ref`` to ``tool-context.schema.json``
resolved) and ``D`` the SDK-derived ``Tool.parameters``:

1. **Key sets agree**, modulo the exclusion below:
   ``keys(P) - EXCLUDED == keys(D)``. Left-to-right catches *the published
   schema permitting what the handler refuses* -- a key the contract admits, the
   handler has no parameter for, and the SDK therefore drops: a published lie
   that costs a caller a call it was told would work. Right-to-left catches a
   handler parameter no file publishes, which each schema's
   ``unevaluatedProperties: false`` makes **unreachable** -- decision 6's second
   forbidden thing, a capability retired by editing a file.
2. **Requiredness agrees**: ``required(P) == required(D)``, with ``required(P)``
   the union of the file's own ``required`` and the referent's. Left-to-right
   would let a caller omit an argument the handler demands; right-to-left tells
   a caller a field is mandatory when the handler defaults it.
3. **Declared types are compatible**: ``types(P) ⊆ types(D)``. Subset, not
   equality, because narrowing is the published schema's job and widening is the
   defect. ``anyOf: [{"type": "string"}, {"type": "null"}]`` (how pydantic
   spells an optional) and ``"type": ["string", "null"]`` (how the files spell
   it) are the same set, which is what :func:`_type_names` is for.

## What the relation deliberately EXCLUDES

**The closure axis.** The derived schema never sets ``additionalProperties``
-- ADR-0031 measured 7 of 7 absent, and
:func:`test_no_derived_schema_carries_a_closure_keyword` recomputes it here
rather than citing it. A relation that compared closure would be RED on every
tool for ever: the published schema closes with ``unevaluatedProperties: false``
and the derived one closes with nothing. Closure is the **middleware's**
(decision 2), not this comparison's, and
``test_input_validation_wire.py::test_an_unknown_key_is_refused_and_the_refusal_names_the_key_not_the_value``
is where it is held.

**Value-domain tightening.** ``pattern``, ``enum``, ``maxLength``, ``minLength``
and ``format`` present in the file and absent from the derived schema are the
published schema's *purpose*, never a failure -- that is the whole argument
ADR-0031's alternatives table makes against generating the file from the handler
signature. Those bounds are pinned against their live constants in
``tests/unit/test_input_schema_bounds.py``; they are not compared here, because
there is nothing on the derived side to compare them to.

**``snapshotId``, ``agentId`` and ``taskId``.** The tool-context ``$ref``
publishes all three on every project-scoped tool, and **no handler in this build
reads any of them**, so each is a published key with no derived counterpart --
exactly the shape rule 1 exists to catch. It is excluded as a *recorded
deferral*, not because it is harmless: a caller that pins ``snapshotId`` is
answered as though it had not, which is the silence SEC-12 exists to end.
[#665](https://github.com/theurian/theurian/issues/665) is where that is
decided, and :func:`test_the_excluded_context_keys_are_still_the_unread_three`
is the pin that makes this exclusion shrink or disappear when it is: the
exclusion is asserted to be *exactly* the set that is published-and-underived,
so a fourth such key does not join it silently and a key that starts being read
takes this file RED.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server import MCPServer

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

from mcp_server_probe import loaded_input_schemas, registered_tool_names  # isort: skip

pytestmark = pytest.mark.integration

#: Published by the tool-context ``$ref`` and read by no handler in this build.
#: A recorded deferral, tracked at #665 -- see the module docstring, and
#: :func:`test_the_excluded_context_keys_are_still_the_unread_three`, which holds
#: this set equal to the population it claims to describe.
UNREAD_CONTEXT_KEYS: Final = frozenset({"snapshotId", "agentId", "taskId"})


@pytest.fixture(scope="module")
def server() -> MCPServer:
    """One built server for the whole module: every test here is a pure read.

    Module-scoped because ``build_server`` parses fifteen schema files and the
    tests below only look at what came back. The registry path is a name that is
    never created -- ``ProjectRegistry.load`` treats an absent file as an empty
    registry -- so this touches no temporary directory of its own.
    """
    return build_server(ProjectRegistry(path=Path("/nonexistent/theurian-agreement/projects.json")))


def _published(server: MCPServer) -> dict[str, dict[str, Any]]:
    """Each tool's published input schema, read from the file the server loaded.

    ``origins`` is the loader's own record of which file claimed which tool, so
    this cannot drift onto a file the running server does not use.
    """
    schemas = loaded_input_schemas(server)
    return {
        tool: json.loads(path.read_text(encoding="utf-8")) for tool, path in schemas.origins.items()
    }


def _by_id(server: MCPServer) -> dict[str, dict[str, Any]]:
    """Every schema beside the published input files, addressable by ``$id``.

    The referent resolution below is one level deep and is asserted to be
    complete by :func:`test_every_published_schema_composes_exactly_one_level`,
    so a second level added later fails rather than being silently skipped.
    """
    origins = loaded_input_schemas(server).origins
    directory = next(iter(origins.values())).parent
    documents = (json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json"))
    return {document["$id"]: document for document in documents if "$id" in document}


def _referents(document: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [by_id[branch["$ref"]] for branch in document.get("allOf", []) if "$ref" in branch]


def _effective_properties(
    document: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in [*_referents(document, by_id), document]:
        merged.update(source.get("properties", {}))
    return merged


def _effective_required(document: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> set[str]:
    required: set[str] = set()
    for source in [*_referents(document, by_id), document]:
        required.update(source.get("required", []))
    return required


def _type_names(subschema: dict[str, Any], where: str) -> frozenset[str]:
    """The JSON type names ``subschema`` admits, from either spelling.

    The files write an optional as ``"type": ["string", "null"]``; pydantic
    derives it as ``anyOf: [{"type": "string"}, {"type": "null"}]``. Both are one
    set of type names, and comparing the *spellings* would report a difference
    that is not one.

    **A shape neither arm recognises raises rather than returning nothing.** An
    empty set is a subset of every set, so a silent fall-through would turn rule
    3 into a test that passes on exactly the subschemas it cannot read.
    """
    declared = subschema.get("type")
    if isinstance(declared, str):
        return frozenset({declared})
    if isinstance(declared, list):
        return frozenset(declared)
    branches = subschema.get("anyOf")
    if isinstance(branches, list) and branches:
        return frozenset().union(*(_type_names(branch, where) for branch in branches))
    raise AssertionError(
        f"{where}: {subschema!r} declares neither `type` nor `anyOf`, so this "
        f"comparison cannot read what it admits. Extend `_type_names` rather than "
        f"letting an unreadable subschema pass by returning an empty set."
    )


def _admits(derived: frozenset[str]) -> frozenset[str]:
    """``derived`` with the one JSON Schema subtype relation applied.

    ``integer`` is a subset of ``number``, so a handler annotated ``float`` is
    correctly narrowed by a published ``"type": "integer"``. Nothing in this
    build exercises it today; it is here so the first one is not read as
    widening.
    """
    return derived | {"integer"} if "number" in derived else derived


def _tools(server: MCPServer) -> list[str]:
    return sorted(registered_tool_names(server))


def _derived(server: MCPServer, tool_name: str) -> dict[str, Any]:
    found = [tool for tool in server._tool_manager.list_tools() if tool.name == tool_name]
    assert len(found) == 1, tool_name
    parameters: dict[str, Any] = found[0].parameters
    return parameters


# Parametrized off a *fresh* build rather than off the module fixture: pytest
# collects parameters before any fixture runs, so the population cannot come
# from `server`. Built once here, at import, and discarded.
_TOOL_NAMES: Final = _tools(
    build_server(ProjectRegistry(path=Path("/nonexistent/theurian-agreement/collect.json")))
)


def test_the_parametrization_covers_the_tools_the_fixture_serves(server: MCPServer) -> None:
    """The collection-time population and the fixture's must be the same set.

    They are built from two separate ``build_server`` calls, so a difference
    between them would silently shrink every parametrized test below to the tools
    both happened to have -- and a sweep that misses a tool reports it as clean.
    """
    assert _TOOL_NAMES, "an empty tool list would pass every parametrized test vacuously"
    assert set(_TOOL_NAMES) == registered_tool_names(server)


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_the_published_schema_names_no_key_the_handler_has_no_parameter_for(
    server: MCPServer, tool_name: str
) -> None:
    """Rule 1, the direction ADR-0031 decision 6 calls a published lie.

    A key the contract admits and the handler has no parameter for is dropped by
    the SDK's argument model and answered as though it had never been sent. The
    caller reads a successful response to a question it did not ask -- which is
    the same silence SEC-12 exists to end, arriving through the published
    contract instead of through a typo.
    """
    published = set(_effective_properties(_published(server)[tool_name], _by_id(server)))
    derived = set(_derived(server, tool_name).get("properties", {}))

    assert published - UNREAD_CONTEXT_KEYS - derived == set(), (
        f"{tool_name}: {sorted(published - UNREAD_CONTEXT_KEYS - derived)} is published "
        f"as input and the handler has no parameter for it, so a caller that sends it "
        f"is answered as though it had not"
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_no_handler_parameter_is_left_unpublished_and_therefore_unreachable(
    server: MCPServer, tool_name: str
) -> None:
    """Rule 1, the other direction: decision 6's *retired capability*.

    Each published schema closes with ``unevaluatedProperties: false``, so a
    parameter the handler accepts and no file publishes is not merely
    undocumented -- it is **refused at dispatch**. The capability is gone, with
    no changelog entry and no deprecation, which is exactly what decision 6's
    second forbidden thing describes.
    """
    published = set(_effective_properties(_published(server)[tool_name], _by_id(server)))
    derived = set(_derived(server, tool_name).get("properties", {}))

    assert derived - published == set(), (
        f"{tool_name}: the handler takes {sorted(derived - published)} and no published "
        f"schema names it, so `unevaluatedProperties: false` refuses every call that "
        f"sends it -- the parameter exists and is reachable by nothing"
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_the_published_and_derived_schemas_agree_about_what_is_required(
    server: MCPServer, tool_name: str
) -> None:
    """Rule 2. A published schema that lets a caller omit an argument the handler
    demands is a published lie; one that demands an argument the handler defaults
    retires the call that omits it.

    ``required`` is read from the file *and* its referent, because ``projectId``
    is required by ``tool-context.schema.json`` and deliberately not repeated in
    any per-tool file.
    """
    published = _effective_required(_published(server)[tool_name], _by_id(server))
    derived = set(_derived(server, tool_name).get("required", []))

    assert published == derived, (
        f"{tool_name}: the published schema requires {sorted(published)} and the "
        f"handler requires {sorted(derived)}. Published-only "
        f"{sorted(published - derived)} refuses a call this build would serve; "
        f"handler-only {sorted(derived - published)} admits a call this build refuses"
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_no_published_type_admits_what_the_handler_refuses(
    server: MCPServer, tool_name: str
) -> None:
    """Rule 3, asymmetric: the published schema may narrow and may not widen.

    A published ``"type": ["string", "null"]`` over a handler annotated ``str``
    admits a ``null`` the handler cannot take -- the argument model refuses it
    below the surface, so the contract promised a call that fails. The reverse,
    a published ``"string"`` over a derived ``string | null``, is ordinary
    tightening and passes.
    """
    by_id = _by_id(server)
    published = _effective_properties(_published(server)[tool_name], by_id)
    derived = _derived(server, tool_name).get("properties", {})

    widened = {}
    for key, subschema in sorted(published.items()):
        if key in UNREAD_CONTEXT_KEYS or key not in derived:
            continue
        published_types = _type_names(subschema, f"{tool_name}.{key} (published)")
        derived_types = _admits(_type_names(derived[key], f"{tool_name}.{key} (derived)"))
        if not published_types <= derived_types:
            widened[key] = (sorted(published_types - derived_types), sorted(derived_types))

    assert not widened, (
        f"{tool_name}: these published types admit a value the handler's own argument "
        f"model refuses, so the contract promises a call that fails below the surface "
        f"-- {{key: (admitted-but-refused, what the handler takes)}} {widened}"
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_no_derived_schema_carries_a_closure_keyword(server: MCPServer, tool_name: str) -> None:
    """The closure exclusion, recomputed rather than cited.

    ADR-0031 measured ``additionalProperties`` absent on 7 of 7 derived schemas
    and excluded the closure axis from this relation on that basis. A measurement
    quoted from a document is a claim about a past tree; this is the same
    measurement over the tree under test. If the SDK ever started closing its
    derived schemas, the exclusion would be describing something that is no
    longer true and the relation should gain an axis rather than keep skipping
    one.
    """
    derived = _derived(server, tool_name)

    assert "additionalProperties" not in derived, derived.get("additionalProperties")
    assert "unevaluatedProperties" not in derived, derived.get("unevaluatedProperties")


def test_the_excluded_context_keys_are_still_the_unread_three(server: MCPServer) -> None:
    """The exclusion is held equal to the population it claims to describe (#665).

    Stated as equality rather than as containment, for the
    ``PROCESS_SPAWN_SITES`` reason: a *fourth* published-but-underived key would
    otherwise join the exclusion by being excluded, and the exclusion is the one
    place in this file where a real disagreement is deliberately not reported.

    So this goes RED in both directions #665 could move it: a handler that starts
    reading one of the three shrinks the population, and a new unread context key
    grows it. Either way the docstring above stops being true and has to be
    rewritten with it.
    """
    by_id = _by_id(server)
    published_documents = _published(server)

    unread: set[str] = set()
    for tool_name in _TOOL_NAMES:
        published = set(_effective_properties(published_documents[tool_name], by_id))
        derived = set(_derived(server, tool_name).get("properties", {}))
        unread |= published - derived

    assert unread == UNREAD_CONTEXT_KEYS, (
        f"the published-but-unread key set is {sorted(unread)} and this module excludes "
        f"{sorted(UNREAD_CONTEXT_KEYS)} from the agreement. Decide the difference at "
        f"https://github.com/theurian/theurian/issues/665 and move the exclusion and "
        f"the module docstring together"
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_every_published_schema_composes_exactly_one_level(
    server: MCPServer, tool_name: str
) -> None:
    """The premise the resolution above rests on, checked rather than assumed.

    ``_effective_properties`` merges the file with the referents of its own
    ``allOf`` branches and goes no deeper. A referent that itself composed -- a
    second ``allOf``, a ``$ref`` inside its ``properties`` -- would contribute
    keys this comparison never sees, and every test above would then be agreeing
    about a subset of the contract while reporting it as the whole.
    """
    document = _published(server)[tool_name]
    by_id = _by_id(server)

    for branch in document.get("allOf", []):
        assert set(branch) == {"$ref"}, (tool_name, branch)
        assert branch["$ref"] in by_id, (tool_name, branch["$ref"])
    for referent in _referents(document, by_id):
        where = (tool_name, referent["$id"])
        assert "allOf" not in referent, where
        assert "$ref" not in json.dumps(referent.get("properties", {})), where
