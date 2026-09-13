"""The MCP input-validation core: what it loads, what it refuses, and how loudly.

``theurian.mcp.validation`` is the plain-Python half of SEC-12 (ADR-0031). The
schemas it reads are build artifacts, and the arguments it checks arrive from
the wire, so the tests here split the same way: a *load* failure raises and
stops the daemon serving anything, while a *request* failure comes back as a
bounded :class:`~theurian.mcp.validation.InputRefusal`.

Every schema below is written into ``tmp_path``. The published per-tool schemas
are authored in the slice's second commit; these fixtures are built to
ADR-0031 decision 1's fixed composition -- ``allOf: [{"$ref": tool-context}]``
plus the tool's own ``properties``, closed with ``unevaluatedProperties:
false`` -- so the properties pinned here are the properties those files will
have to hold.
"""

from __future__ import annotations

import ast
import json
import pathlib
import urllib.request
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry

from theurian.mcp import validation
from theurian.mcp.validation import (
    MAX_ECHOED_FRAGMENT_CHARS,
    MAX_PARAMS_NESTING,
    MAX_PARAMS_NODES,
    MAX_PARAMS_RENDERED_CHARS,
    MAX_REFUSAL_CHARS,
    InputRefusal,
    InputSchemaError,
    InputSchemaSet,
    load_input_schemas,
)

CONTEXT_ID = "https://theurian.dev/schemas/mcp/tool-context.schema.json"
PROBE_TOOL = "probe.tool"

VALID_ARGUMENTS: dict[str, Any] = {"projectId": "demo", "query": "auth policy", "limit": 5}


def _context_schema() -> dict[str, Any]:
    """A local stand-in for the published tool context, carrying no closure.

    ADR-0031 decision 1 moves ``additionalProperties: false`` off the referent
    and onto each per-tool schema's ``unevaluatedProperties``, because the table
    measured there shows a *closed* referent rejects the valid document under
    every composition -- including the one this module is built around.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": CONTEXT_ID,
        "title": "Theurian MCP tool call context",
        "type": "object",
        "required": ["projectId"],
        "properties": {
            "projectId": {
                "type": "string",
                "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "maxLength": 200,
            },
            "snapshotId": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        },
    }


def _tool_schema(
    tool: str = PROBE_TOOL,
    *,
    stem: str = "probe",
    closure: str = "unevaluatedProperties",
    reference: str = CONTEXT_ID,
) -> dict[str, Any]:
    """One per-tool input schema in decision 1's composition.

    ``closure`` is a parameter so the composition the ADR measured as *broken*
    -- the same schema closed with ``additionalProperties`` -- can be built here
    and shown to reject the valid document.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://theurian.dev/schemas/mcp/{stem}-input.schema.json",
        "title": f"Theurian MCP {tool} input",
        "x-theurian-tool": tool,
        "type": "object",
        "allOf": [{"$ref": reference}],
        "properties": {
            "query": {"type": "string", "maxLength": 2_000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "nested": {"type": "array"},
        },
        closure: False,
    }


def _write(directory: pathlib.Path, name: str, document: object) -> pathlib.Path:
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def schemas_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(tmp_path, "probe-input.schema.json", _tool_schema())
    return tmp_path


@pytest.fixture
def loaded(schemas_dir: pathlib.Path) -> InputSchemaSet:
    return load_input_schemas(schemas_dir)


def _arguments_nesting(depth: int) -> dict[str, Any]:
    """Arguments whose deepest value sits at exactly ``depth``.

    The arguments object is level 1 and everything it holds is level 2, so a
    chain of ``depth - 2`` further lists reaches ``depth``. The arithmetic is
    stated once here and checked against the module's own walk by
    :func:`test_the_nesting_fixture_reaches_the_depth_it_claims`, because an
    off-by-one in this builder would weaken both bound tests silently.
    """
    value: list[Any] = []
    for _ in range(depth - 2):
        value = [value]
    return {"projectId": "demo", "nested": value}


def _deepest(arguments: dict[str, Any]) -> int:
    return max(depth for _, depth in validation._iter_nodes(arguments))


# -- The composition ADR-0031 decision 1 fixed -----------------------------


def test_a_valid_document_passes_the_fixed_composition(loaded: InputSchemaSet) -> None:
    """The regression an ``additionalProperties`` composition would cause.

    Decision 1's table measured every other arrangement rejecting the very
    document it was written to admit, so the acceptance is pinned before the
    refusals are: a control that only refuses passes for a build that refuses
    everything.
    """
    assert loaded.validate(PROBE_TOOL, VALID_ARGUMENTS) is None


def test_the_context_reference_resolves_from_the_local_tree(loaded: InputSchemaSet) -> None:
    """``projectId`` is typed by the referent, so its rule fires through the ``$ref``."""
    refusal = loaded.validate(PROBE_TOOL, {"projectId": "Not A Project Id"})
    assert refusal is not None
    assert "projectId" in refusal.message


def test_an_unknown_key_beside_valid_ones_is_refused_by_name(loaded: InputSchemaSet) -> None:
    refusal = loaded.validate(PROBE_TOOL, {**VALID_ARGUMENTS, "bogus": "x"})
    assert refusal is not None
    assert "bogus" in refusal.message


def test_closing_with_additional_properties_rejects_the_valid_document(
    tmp_path: pathlib.Path,
) -> None:
    """The broken half of decision 1's table, driven rather than cited.

    ``additionalProperties`` considers only the ``properties`` in its own schema
    object, so it does not see what the ``allOf`` branch evaluated and refuses
    the context fields the schema just referenced. This is why the keyword this
    module's fixtures and the published schemas use is
    ``unevaluatedProperties``.
    """
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(
        tmp_path,
        "broken-input.schema.json",
        _tool_schema("broken.tool", stem="broken", closure="additionalProperties"),
    )
    refusal = load_input_schemas(tmp_path).validate("broken.tool", VALID_ARGUMENTS)
    assert refusal is not None
    assert "projectId" in refusal.message


# -- Fail-closed dispatch (decision 5) -------------------------------------


def test_a_tool_with_no_schema_is_refused_rather_than_passed(loaded: InputSchemaSet) -> None:
    refusal = loaded.validate("knowledge.search", VALID_ARGUMENTS)
    assert refusal is not None
    assert "knowledge.search" in refusal.message


def test_the_set_names_only_the_tools_its_schemas_declare(loaded: InputSchemaSet) -> None:
    assert loaded.tool_names == (PROBE_TOOL,)


def test_tool_names_is_sorted_rather_than_in_load_order(tmp_path: pathlib.Path) -> None:
    """The filenames and the tool names are deliberately in opposite orders.

    Schemas are loaded in path order, so a set that simply reported its mapping
    would answer ``('zulu.tool', 'alpha.tool')`` here -- an order that reaches
    the sweep ADR-0031 owes and varies with what the files happen to be called.
    """
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(tmp_path, "alpha-input.schema.json", _tool_schema("zulu.tool", stem="alpha"))
    _write(tmp_path, "zulu-input.schema.json", _tool_schema("alpha.tool", stem="zulu"))
    assert load_input_schemas(tmp_path).tool_names == ("alpha.tool", "zulu.tool")


def test_a_response_schema_contributes_no_tool(tmp_path: pathlib.Path) -> None:
    """A file without the input suffix is a referent, not a tool contract.

    It is still read into the offline registry -- that is how a per-tool schema
    reaches its ``$ref`` -- but it names no tool, so a response schema that
    happens to sit in the same directory cannot be mistaken for a tool contract
    that forgot its ``x-theurian-tool``.
    """
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(tmp_path, "probe-input.schema.json", _tool_schema())
    _write(
        tmp_path,
        "probe-response.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://theurian.dev/schemas/mcp/probe-response.schema.json",
            "title": "Theurian MCP probe response",
            "type": "object",
            "additionalProperties": False,
            "properties": {"results": {"type": "array"}},
        },
    )
    assert load_input_schemas(tmp_path).tool_names == (PROBE_TOOL,)


# -- Fail-closed loading ---------------------------------------------------


def test_a_schema_without_a_tool_name_refuses_the_whole_load(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    document = _tool_schema()
    del document["x-theurian-tool"]
    _write(tmp_path, "probe-input.schema.json", document)
    with pytest.raises(InputSchemaError) as caught:
        load_input_schemas(tmp_path)
    assert "x-theurian-tool" in str(caught.value)
    assert caught.value.remedy


@pytest.mark.parametrize("declared", ["", "   "], ids=["empty", "blank"])
def test_an_unusable_tool_name_refuses_the_whole_load(
    tmp_path: pathlib.Path, declared: str
) -> None:
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(tmp_path, "probe-input.schema.json", {**_tool_schema(), "x-theurian-tool": declared})
    with pytest.raises(InputSchemaError):
        load_input_schemas(tmp_path)


def test_two_schemas_claiming_one_tool_name_refuse_the_whole_load(
    tmp_path: pathlib.Path,
) -> None:
    """Fatal to the set, not to the second file.

    Keeping the first and dropping the second would serve one of two
    contradictory contracts, chosen by directory order.
    """
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(tmp_path, "probe-input.schema.json", _tool_schema())
    _write(tmp_path, "twin-input.schema.json", _tool_schema(stem="twin"))
    with pytest.raises(InputSchemaError) as caught:
        load_input_schemas(tmp_path)
    message = str(caught.value)
    assert PROBE_TOOL in message
    assert "probe-input.schema.json" in message


def test_a_ref_to_an_absent_local_file_refuses_the_load(tmp_path: pathlib.Path) -> None:
    """``check_schema`` never follows a reference, so the load has to.

    Without this the daemon would come up holding a validator that looks
    healthy and fails on the first real request instead.
    """
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(
        tmp_path,
        "probe-input.schema.json",
        _tool_schema(reference="https://theurian.dev/schemas/mcp/absent.schema.json"),
    )
    with pytest.raises(InputSchemaError) as caught:
        load_input_schemas(tmp_path)
    assert "$ref" in str(caught.value)


def test_a_ref_to_a_network_uri_fails_closed_without_fetching(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry carries no ``retrieve``, so an external ``$ref`` is not fetched.

    The patched ``urlopen`` is proved reachable first: a zero-call assertion
    against a patch nothing could have called says nothing.
    """
    calls: list[str] = []

    def _forbidden(*args: object, **kwargs: object) -> object:
        calls.append("urlopen")
        raise AssertionError("the schema loader reached the network")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    with pytest.raises(AssertionError):
        urllib.request.urlopen("https://example.invalid/control.json")
    assert calls == ["urlopen"]
    calls.clear()

    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(
        tmp_path,
        "probe-input.schema.json",
        _tool_schema(reference="https://example.invalid/tool-context.schema.json"),
    )
    with pytest.raises(InputSchemaError):
        load_input_schemas(tmp_path)
    assert calls == []


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("probe-input.schema.json", "{not json"),
        ("probe-input.schema.json", "[]"),
        ("probe-input.schema.json", '{"$schema": "https://json-schema.org/draft/2020-12/schema"}'),
    ],
    ids=["unparseable", "not-an-object", "no-id"],
)
def test_a_malformed_schema_file_refuses_the_whole_load(
    tmp_path: pathlib.Path, name: str, content: str
) -> None:
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    (tmp_path / name).write_text(content, encoding="utf-8")
    with pytest.raises(InputSchemaError) as caught:
        load_input_schemas(tmp_path)
    assert caught.value.remedy


def test_a_schema_the_metaschema_rejects_refuses_the_whole_load(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    _write(tmp_path, "probe-input.schema.json", {**_tool_schema(), "required": "projectId"})
    with pytest.raises(InputSchemaError) as caught:
        load_input_schemas(tmp_path)
    assert "Draft 2020-12" in str(caught.value)


# -- Bounded refusals (decision 4) -----------------------------------------


def test_a_ten_thousand_character_unknown_key_produces_a_short_message(
    loaded: InputSchemaSet,
) -> None:
    key = "a" * 10_000
    refusal = loaded.validate(PROBE_TOOL, {**VALID_ARGUMENTS, key: "x"})
    assert refusal is not None
    assert len(refusal.message) <= MAX_REFUSAL_CHARS
    assert key not in refusal.message


def test_a_ten_thousand_character_tool_name_produces_a_short_message(
    loaded: InputSchemaSet,
) -> None:
    name = "b" * 10_000
    refusal = loaded.validate(name, VALID_ARGUMENTS)
    assert refusal is not None
    assert len(refusal.message) <= MAX_REFUSAL_CHARS
    assert name not in refusal.message


def test_a_refused_value_is_never_echoed_back(loaded: InputSchemaSet) -> None:
    """Decision 4: the refusal names the key path and the constraint, not the value."""
    refusal = loaded.validate(PROBE_TOOL, {"projectId": "NOT-A-PROJECT-ID"})
    assert refusal is not None
    assert "NOT-A-PROJECT-ID" not in refusal.message
    assert "pattern" in refusal.message


def test_a_control_character_in_a_key_reaches_no_message_raw(loaded: InputSchemaSet) -> None:
    """A newline a caller sent must not forge a line of this daemon's output."""
    refusal = loaded.validate(PROBE_TOOL, {**VALID_ARGUMENTS, "a\nb\x1bc": "x"})
    assert refusal is not None
    assert "\n" not in refusal.message
    assert "\x1b" not in refusal.message


def test_a_control_character_in_the_tool_name_reaches_no_message_raw(
    loaded: InputSchemaSet,
) -> None:
    """The tool name is caller-chosen too, and its refusal fires before any lookup."""
    refusal = loaded.validate("bad\ntool\x1b", VALID_ARGUMENTS)
    assert refusal is not None
    assert "\n" not in refusal.message
    assert "\x1b" not in refusal.message


def test_a_root_level_rejection_names_the_root(loaded: InputSchemaSet) -> None:
    """An empty path is a location a reader can read, not an empty gap in a sentence."""
    refusal = loaded.validate(PROBE_TOOL, {**VALID_ARGUMENTS, "bogus": 1})
    assert refusal is not None
    assert "At <root>" in refusal.message


def test_a_deep_path_of_caller_written_keys_stays_inside_the_ceiling(
    tmp_path: pathlib.Path,
) -> None:
    """The assembled location is bounded, not just the key inside it.

    A schema that descends into caller-written keys (here, ``additionalProperties``
    as a subschema, recursing) lets a caller choose both how long each key is and
    how many of them the failing path carries. Six two-hundred-character keys
    assemble a location several times the whole message ceiling, so without that
    bound this call has no bounded answer at all.

    The keys carry a newline and an ESC too, because the location is the one
    fragment this module escapes itself rather than quoting from ``jsonschema``,
    and a path that never reaches a caller key would leave that escaping
    undriven.
    """
    _write(tmp_path, "tool-context.schema.json", _context_schema())
    document = _tool_schema()
    document["properties"]["nested"] = {"$ref": "#/$defs/tree"}
    document["$defs"] = {
        "tree": {
            "type": "object",
            "properties": {"leaf": {"type": "integer"}},
            "additionalProperties": {"$ref": "#/$defs/tree"},
        }
    }
    _write(tmp_path, "probe-input.schema.json", document)

    branch: dict[str, Any] = {"leaf": "not an integer"}
    for index in range(6):
        branch = {f"{index}\n\x1b{'k' * 200}": branch}
    refusal = load_input_schemas(tmp_path).validate(
        PROBE_TOOL, {"projectId": "demo", "nested": branch}
    )
    assert refusal is not None
    assert len(refusal.message) <= MAX_REFUSAL_CHARS
    assert "\n" not in refusal.message
    assert "\x1b" not in refusal.message


def test_a_refusal_past_the_ceiling_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="MAX_REFUSAL_CHARS"):
        InputRefusal(tool="probe", message="x" * (MAX_REFUSAL_CHARS + 1))


def test_a_refusal_with_no_message_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="names no cure"):
        InputRefusal(tool="probe", message="")


#: Every refusal template this module publishes, with the fields a *caller* can
#: influence named separately from the ones only this module fills in. The
#: partition is stated here because it is a reading of each template, not
#: something reflection can recover; the test below holds that the *population*
#: of templates is complete, so a new one cannot be added without joining it.
_TEMPLATES: tuple[tuple[str, str, tuple[str, ...], dict[str, object]], ...] = (
    ("MISMATCH_REFUSAL", validation.MISMATCH_REFUSAL, ("tool", "location", "detail"), {}),
    ("NO_SCHEMA_REFUSAL", validation.NO_SCHEMA_REFUSAL, ("tool",), {}),
    (
        "OVERSIZED_REFUSAL",
        validation.OVERSIZED_REFUSAL,
        ("tool",),
        {"unit": "characters of content", "limit": MAX_PARAMS_RENDERED_CHARS},
    ),
    ("UNUSABLE_SCHEMA_REFUSAL", validation.UNUSABLE_SCHEMA_REFUSAL, ("tool",), {}),
)


def test_the_template_table_covers_every_published_refusal() -> None:
    """The table above is the whole population, not the part someone remembered."""
    published = {name for name in vars(validation) if name.endswith("_REFUSAL")}
    assert published == {name for name, _, _, _ in _TEMPLATES}


def test_every_refusal_template_fits_the_ceiling() -> None:
    """The ceiling is a derivation over the live prose, recomputed here.

    Each caller-influenced field is at most ``MAX_ECHOED_FRAGMENT_CHARS`` plus
    the cut marker, so the longest message a template can assemble is its own
    prose plus that width once per such field. If a template's prose grows past
    what the ceiling admits, this goes red before a request does.
    """
    widest = MAX_ECHOED_FRAGMENT_CHARS + len(validation._CUT_MARKER)
    for name, template, caller_fields, fixed in _TEMPLATES:
        prose = template.format(**dict.fromkeys(caller_fields, ""), **fixed)
        assert len(prose) + widest * len(caller_fields) <= MAX_REFUSAL_CHARS, name


def test_every_refusal_names_a_runnable_remedy(loaded: InputSchemaSet) -> None:
    """A remedy names the thing to act on *and* something the reader can run.

    ``tools/list`` is the MCP method that publishes each tool's input schema, so
    it is both; the operator-side refusals name the install instead, because a
    caller cannot repair this build's schemas.
    """
    refusals = [
        loaded.validate(PROBE_TOOL, {**VALID_ARGUMENTS, "bogus": 1}),
        loaded.validate("absent.tool", VALID_ARGUMENTS),
        loaded.validate(PROBE_TOOL, _arguments_nesting(MAX_PARAMS_NESTING + 1)),
    ]
    for refusal in refusals:
        assert refusal is not None
        assert "tools/list" in refusal.message
        assert "inputSchema" in refusal.message or "input schema" in refusal.message


# -- Bounds on an untrusted request ----------------------------------------


@pytest.mark.parametrize("depth", [2, 3, MAX_PARAMS_NESTING, MAX_PARAMS_NESTING + 1])
def test_the_nesting_fixture_reaches_the_depth_it_claims(depth: int) -> None:
    assert _deepest(_arguments_nesting(depth)) == depth


def test_arguments_at_the_nesting_bound_are_validated(loaded: InputSchemaSet) -> None:
    """The positive control for the bound below: at the limit, the schema decides."""
    assert loaded.validate(PROBE_TOOL, _arguments_nesting(MAX_PARAMS_NESTING)) is None


def test_arguments_past_the_nesting_bound_are_refused(loaded: InputSchemaSet) -> None:
    """Past CPython's C recursion budget ``jsonschema`` cannot build its own message.

    The refusal has to be made before ``iter_errors`` is handed the arguments,
    which is why the bound is a walk of this module's own rather than an
    ``except RecursionError``.
    """
    refusal = loaded.validate(PROBE_TOOL, _arguments_nesting(MAX_PARAMS_NESTING + 1))
    assert refusal is not None
    assert "levels of nesting" in refusal.message


def test_arguments_past_the_node_bound_are_refused(loaded: InputSchemaSet) -> None:
    wide = {f"k{index}": index for index in range(MAX_PARAMS_NODES)}
    refusal = loaded.validate(PROBE_TOOL, {"projectId": "demo", **wide})
    assert refusal is not None
    assert "values" in refusal.message


def test_arguments_past_the_rendered_character_bound_are_refused(
    loaded: InputSchemaSet,
) -> None:
    arguments = {"projectId": "demo", "query": "a" * (MAX_PARAMS_RENDERED_CHARS + 1)}
    refusal = loaded.validate(PROBE_TOOL, arguments)
    assert refusal is not None
    assert "characters of content" in refusal.message


def test_a_giant_integer_is_charged_against_the_rendered_bound(
    loaded: InputSchemaSet,
) -> None:
    """One node, unbounded render: the face a node count cannot see.

    JSON admits an integer literal of any length, and ``jsonschema`` re-emits
    the failing value into its own message. A budget that charged only strings
    would wave this through as a single small node, so the width is estimated
    from ``bit_length`` and charged like any other leaf.
    """
    bits = (MAX_PARAMS_RENDERED_CHARS + 10) * 100_000 // 30_103 + 1
    refusal = loaded.validate(PROBE_TOOL, {"projectId": "demo", "limit": 1 << bits})
    assert refusal is not None
    assert "characters of content" in refusal.message


# -- Determinism and ordering ----------------------------------------------


def test_a_failing_referenced_branch_is_reported_at_its_own_path(
    loaded: InputSchemaSet,
) -> None:
    """The reason the ordering is deepest-first, driven.

    ``unevaluatedProperties`` credits only the keys an ``allOf`` branch
    evaluated *successfully*, so a bad ``projectId`` produces both a ``pattern``
    failure at ``projectId`` and a root-level *"'projectId' was unexpected"*.
    Reporting the shallower one would tell a caller who sent the right key with
    the wrong value that the key is unknown.
    """
    refusal = loaded.validate(PROBE_TOOL, {"projectId": "Bad Id", "query": "x"})
    assert refusal is not None
    assert "At projectId" in refusal.message
    assert "unexpected" not in refusal.message


def test_the_same_arguments_refuse_identically_every_time(loaded: InputSchemaSet) -> None:
    """No ordering here may be decided by dict iteration order."""
    arguments = {**VALID_ARGUMENTS, "zebra": 1, "alpha": 2, "middle": 3}
    messages = {loaded.validate(PROBE_TOOL, dict(arguments)) for _ in range(5)}
    assert len(messages) == 1


# -- An installation that cannot answer ------------------------------------


def test_a_schema_that_stops_resolving_answers_the_operator_remedy() -> None:
    """A validator whose ``$ref`` no longer resolves is this build's fault, not the
    caller's, and ``Unresolvable`` is not a ``ValidationError`` -- so without the
    catch it would leave this seam as a raw traceback.
    """
    orphan = Draft202012Validator(_tool_schema(), registry=Registry())
    schemas = InputSchemaSet(
        validators={PROBE_TOOL: orphan}, origins={PROBE_TOOL: pathlib.Path("probe")}
    )
    refusal = schemas.validate(PROBE_TOOL, VALID_ARGUMENTS)
    assert refusal is not None
    assert "Reinstall theurian" in refusal.message
    assert "nothing was changed" in refusal.message


# -- The claim the module docstring makes about itself ----------------------


def test_the_validation_core_imports_no_mcp_sdk() -> None:
    """The module docstring says nothing here imports the MCP SDK, so it is checked.

    That claim is what lets this control be driven without the ``daemon`` extra
    installed, and an import added later would delete it silently.
    """
    source = pathlib.Path(validation.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    sdk = {name for name in imported if name == "mcp" or name.startswith("mcp.")}
    assert sdk == set()
