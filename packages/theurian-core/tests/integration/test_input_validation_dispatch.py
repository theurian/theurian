"""SEC-12's structural claims, driven at dispatch (ADR-0031 decisions 2, 4, 5).

``test_input_validation_wire.py`` holds what the middleware refuses about a
*request*. This file holds the two claims that are about the registered **set**
rather than about any one request, plus the bounds that must hold before
``jsonschema`` is handed anything:

* **Fail-closed by structure** (decision 5). A tool registered without a
  published input schema is *unreachable* -- refused at dispatch, its handler
  never entered. Asserted through a real ``tools/call`` against a server
  carrying exactly that tool, with a sentinel the handler would set if it ran.
* **The population is derived, not listed** (decision 5's owed sweep). Every
  tool the built server registers resolves to a loaded schema, and every loaded
  schema names a registered tool -- set equality in both directions, so a schema
  published for a tool that no longer exists fails here too. The precedent is
  ``test_network_call_sites.py``'s ``PROCESS_SPAWN_SITES``, asserted by equality
  so it fails on an addition *and* on a removal.
* **The untrusted-document bounds are applied at this boundary** (ADR-0031's
  *Still owed*, the ``#291``/``#245`` caps). ``test_input_validation.py`` drives
  each bound against a synthetic schema set; what is owed here is that a real
  inbound ``tools/call`` past each bound is refused over the wire, and that the
  refusal is bounded -- it does not echo the oversized payload back.

Everything here goes through the transport for the reason
``test_input_validation_wire.py`` records: ``server.call_tool`` is the SDK's
tool dispatcher and sits *below* the ``ServerMiddleware`` tier, so an
in-process call never reaches the seat under test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server import MCPServer
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.mcp.tools import _tool
from theurian.mcp.validation import (
    MAX_PARAMS_NESTING,
    MAX_PARAMS_NODES,
    MAX_PARAMS_RENDERED_CHARS,
)

from mcp_server_probe import loaded_input_schemas, registered_tool_names  # isort: skip
from mcp_wire_session import headers, mcp_session, open_client  # isort: skip

pytestmark = pytest.mark.integration

#: A tool name no published schema declares, and no ``@_tool`` in
#: ``mcp/tools.py`` registers. Checked against both, below, so this file cannot
#: quietly start testing a tool that grew a contract.
UNPUBLISHED_TOOL: Final = "test.without.a.schema"

#: A value no message in this codebase could produce. Searched for across the
#: whole response, so "the refusal does not echo what the caller sent" is
#: checkable without trusting that a test looked at the right field.
SENTINEL: Final = "sentinel-value-3b7e41af"


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry(path=tmp_path / "projects.json")


@contextmanager
def _server_with_an_unpublished_tool(
    registry: ProjectRegistry,
) -> Iterator[tuple[MCPServer, list[str]]]:
    """A real built server carrying one extra tool that has no published schema.

    Registered through ``_tool`` -- the one registration seam -- rather than
    through ``server.tool`` directly, so the extra tool is the same kind of
    object every real tool is and the difference under test is *only* the
    missing schema.

    **Neither seam pin is tripped by this, and that is a property of what they
    read rather than of this comment.** Both
    ``test_tool_error_type_contract.py::test_every_tool_is_registered_through_the_one_seam``
    (which parses ``register``'s AST) and
    ``test_mcp_tools.py::test_every_registered_tool_goes_through_the_forwarding_seam``
    (which calls ``build_server`` itself and asks *that* server) constrain the
    production registration set. This registration happens on a server object
    this test built and holds, after ``build_server`` returned, so neither sees
    it -- and going through ``_tool`` means it would satisfy both if they did.

    Yields the server and the list the handler appends to. The list stays empty
    unless the handler runs, which is the assertion: a guard no data reaches
    survives its own deletion, so non-execution is what is asserted rather than
    the presence of a refusal alone.
    """
    server = build_server(registry)
    entered: list[str] = []

    @_tool(
        server,
        name=UNPUBLISHED_TOOL,
        description="A tool with no published input schema. Registered by a test only.",
    )
    def without_a_schema(marker: str) -> dict[str, str]:
        entered.append(marker)
        return {"marker": marker}

    yield server, entered


def test_a_tool_with_no_published_schema_is_refused_at_dispatch(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """ADR-0031 decision 5: a tool with no published input schema is unreachable.

    This is the clause that makes SEC-12 survive the next contributor. A control
    that applies to the tools somebody remembered to enumerate is one a future
    tool leaves by omission, and the omission is silent in the direction that
    matters -- the new tool is served, unvalidated, and nothing says so.

    **The assertion is that the handler did not run**, not that a refusal came
    back. A refusal assertion alone would still pass against a build that
    refused *after* dispatching, which is the same disclosure with a worse
    error message.
    """
    with (
        _server_with_an_unpublished_tool(registry) as (server, entered),
        mcp_session(server, tmp_path / "data") as call,
    ):
        answer = call(UNPUBLISHED_TOOL, {"marker": SENTINEL})

    assert entered == [], (
        f"the handler for {UNPUBLISHED_TOOL!r} ran with {entered!r}, so a tool "
        f"registered without a published input schema is reachable and SEC-12 "
        f"applies only to the tools someone remembered to publish"
    )
    assert answer["result"]["isError"] is True, answer
    assert "publishes no input schema" in answer["result"]["content"][0]["text"], answer
    assert SENTINEL not in json.dumps(answer), answer


def test_an_ordinary_tool_is_still_served_on_that_same_server(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The positive control the refusal above is worthless without.

    A refusal test with no served counterpart passes for a server that refuses
    everything, which is the failure mode ADR-0031 names when it owes this pair.
    Same server, same session, same middleware -- only the tool differs.
    """
    with (
        _server_with_an_unpublished_tool(registry) as (server, _entered),
        mcp_session(server, tmp_path / "data") as call,
    ):
        refused = call(UNPUBLISHED_TOOL, {"marker": SENTINEL})
        served = call("project.list", {})

    assert refused["result"]["isError"] is True, refused
    assert served["result"]["isError"] is False, served
    assert served["result"]["structuredContent"]["projects"] == [], served


def test_the_unpublished_tool_name_is_one_no_build_artifact_claims(
    registry: ProjectRegistry,
) -> None:
    """The premise the two tests above rest on, checked rather than assumed.

    If ``UNPUBLISHED_TOOL`` ever became a real tool name -- or a schema were
    published under it -- the refusal test would be asserting that a *published*
    tool is refused, which is the opposite claim, and it would still be green.
    """
    server = build_server(registry)

    assert UNPUBLISHED_TOOL not in loaded_input_schemas(server).tool_names
    assert UNPUBLISHED_TOOL not in registered_tool_names(server)


def test_every_registered_tool_resolves_to_a_published_input_schema(
    registry: ProjectRegistry,
) -> None:
    """The derived population, in both directions (ADR-0031's owed sweep).

    Walked off the **built** server rather than off a list in this file, so a
    tool added later joins the sweep by existing -- the shape
    ``test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write``
    uses, and for the same reason: a list is a thing someone updates, a walk is
    a property.

    **Equality, not containment.** A schema published for a tool this build no
    longer registers is a finding too: it is a wire contract a client may read
    and code against, describing a call that answers "no such tool". The
    precedent is ``PROCESS_SPAWN_SITES`` in ``test_network_call_sites.py``,
    asserted by equality so it fails on an addition *and* on a removal.
    """
    server = build_server(registry)
    registered = registered_tool_names(server)
    published = set(loaded_input_schemas(server).tool_names)

    assert registered, "an empty tool list would pass this test vacuously"
    assert registered == published, (
        f"registered with no published input schema (refused at dispatch, so "
        f"unreachable): {sorted(registered - published)}; published for a tool "
        f"this build does not register (a contract describing a call that "
        f"answers 'no such tool'): {sorted(published - registered)}"
    )


def test_every_published_schema_names_the_file_it_came_from(registry: ProjectRegistry) -> None:
    """The sweep above compares names; this is what makes a failure locatable.

    Without it, ``registered == published`` failing names a tool and not the
    artifact to edit. ``origins`` is the loader's own record of which file
    claimed which tool, so it is asserted to cover the same population rather
    than left as an unchecked convenience.
    """
    schemas = loaded_input_schemas(build_server(registry))

    assert set(schemas.origins) == set(schemas.tool_names)
    for tool_name, origin in schemas.origins.items():
        assert origin.name.endswith("-input.schema.json"), (tool_name, origin)
        assert origin.is_file(), (tool_name, origin)


def _nested(depth: int) -> dict[str, Any]:
    """Arguments whose deepest value sits at ``depth``, counting the arguments
    object as level 1 -- the way :func:`~theurian.mcp.validation._unbounded`
    counts.

    ``{"a": 1}`` is depth 2: the mapping is 1 and the value inside it is 2. So
    ``depth`` levels need ``depth - 2`` intermediate mappings under the root.
    """
    innermost: Any = 1
    for _ in range(depth - 2):
        innermost = {"a": innermost}
    return {"a": innermost}


def test_arguments_past_the_nesting_bound_are_refused_over_the_wire(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The #291/#245 caps apply at the MCP boundary, not only in the loader.

    ``jsonschema`` renders a failing instance with ``{instance!r}`` while
    building its own message, and that render recurses -- so past CPython's C
    recursion budget the library cannot build even its refusal, and the
    ``RecursionError`` that escapes is indistinguishable from a broken schema.
    The bound is checked *before* the document is handed over, which is what
    this drives: one level past the cap, refused, over a real ``tools/call``.
    """
    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", _nested(MAX_PARAMS_NESTING + 1))

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, answer
    assert "levels of nesting" in text, text
    assert str(MAX_PARAMS_NESTING) in text, text


def test_arguments_at_the_nesting_bound_reach_the_schema(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The positive control for the bound above: at the cap, the schema decides.

    Without it, the refusal test passes against a boundary that refuses every
    nested document -- and the two answers are told apart by *which* refusal
    arrives, so the assertion is on the schema's wording rather than on
    ``isError``. A document at the cap is still invalid input (it carries an
    unknown key ``a``), and being refused *by the schema* is the proof it got
    that far.
    """
    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", _nested(MAX_PARAMS_NESTING))

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, answer
    assert "published input schema" in text, text
    assert "levels of nesting" not in text, text


def test_arguments_past_the_node_bound_are_refused_over_the_wire(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """A wide document costs per-node work before any keyword runs.

    Nesting is not the only unbounded axis: a flat list of a million elements
    never recurses and still walks a million values, in this module's own walk
    and again in ``jsonschema``'s. Sized one node past the cap rather than
    generously -- this is a correctness pin, not a load test.
    """
    # `discovered` starts at 1 for the arguments object itself, and the two keys
    # and the list below are 3 more, so a list of MAX_PARAMS_NODES elements
    # crosses the bound by exactly the elements' own count.
    arguments = {"projectId": "demo", "values": [0] * MAX_PARAMS_NODES}

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", arguments)

    text = answer["result"]["content"][0]["text"]
    assert answer["result"]["isError"] is True, answer
    assert "more values than this daemon will validate" in text, text
    assert str(MAX_PARAMS_NODES) in text, text


def test_the_widest_body_this_transport_admits_is_refused_without_echoing_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """One node, unbounded width: the axis a node count cannot see.

    A single string is one node and renders to as many characters as it holds,
    which is what ``MAX_PARAMS_NODES`` is blind to. Driven at the widest value
    that can actually arrive -- see the test below for why that is the
    transport's limit and not :data:`MAX_PARAMS_RENDERED_CHARS` -- and the
    assertion is decision 4's: the answer names a key path and a constraint, and
    is orders of magnitude smaller than the request. A refusal that quoted the
    value back would make this boundary a ~1x amplifier of the caller's own
    bytes (#17).

    **The value opens with the sentinel**, so "nothing of what the caller sent
    came back" is checked by searching the whole response rather than by reading
    the one field this test remembered to look at -- and a *bounded* echo, the
    120-character one ``_echo`` would produce, fails it exactly as an unbounded
    one does. A 4 MiB payload of one repeated character would survive that
    mutation: the response is still small, and a prefix assertion on ``"aaa..."``
    only catches an echo longer than whatever prefix was guessed.
    """
    # Room for the JSON-RPC envelope around it, so the body itself stays under
    # the cap the test below measures.
    oversized = SENTINEL + "a" * (DEFAULT_MAX_REQUEST_BODY_SIZE - 500 - len(SENTINEL))

    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call("knowledge.search", {"projectId": "demo", "query": oversized})

    result = answer["result"]
    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert "query" in text and "maxLength" in text, text
    assert SENTINEL not in json.dumps(answer), text
    assert len(json.dumps(answer)) < len(oversized) // 1000, len(json.dumps(answer))


def test_the_rendered_character_bound_sits_above_what_the_transport_will_carry(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """:data:`MAX_PARAMS_RENDERED_CHARS` cannot be reached over this transport,
    and this records it rather than endorsing it.

    ``build_app`` calls ``streamable_http_app`` without ``max_request_body_size``,
    so the SDK's own :data:`DEFAULT_MAX_REQUEST_BODY_SIZE` applies -- 4 MiB,
    against this module's 12 MiB. A body past it is answered ``413`` by
    ``RequestBodyLimitMiddleware`` before any MCP framing exists, so the bounded
    refusal ``validation.py`` builds for this axis is unreachable at this seam in
    the shipped default configuration.

    Both halves are measured rather than transcribed: the relationship from the
    two live constants, and the 413 from a real POST one byte past the cap. The
    pin has teeth in both directions -- raising the transport limit above
    :data:`MAX_PARAMS_RENDERED_CHARS` makes this daemon's own refusal reachable
    and owes it a wire test, and lowering
    :data:`MAX_PARAMS_RENDERED_CHARS` under the transport limit does the same.
    Either way somebody has to look, which is the whole point of writing the
    relationship down.
    """
    assert MAX_PARAMS_RENDERED_CHARS > DEFAULT_MAX_REQUEST_BODY_SIZE, (
        f"MAX_PARAMS_RENDERED_CHARS ({MAX_PARAMS_RENDERED_CHARS}) is no longer above "
        f"the transport's own body limit ({DEFAULT_MAX_REQUEST_BODY_SIZE}), so this "
        f"daemon's bounded refusal for that axis is now reachable over the wire and "
        f"owes a test that drives it"
    )

    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "knowledge.search",
                "arguments": {"projectId": "demo", "query": "a" * DEFAULT_MAX_REQUEST_BODY_SIZE},
            },
        }
        refused = client.post("/mcp", json=request, headers=headers(session))

    assert refused.status_code == 413, refused.status_code
    assert "aaaa" not in refused.text, refused.text[:200]
