"""SEC-12 over the wire: what the middleware refuses, and what proves it is the
one refusing (ADR-0031 decisions 2 and 4).

**In-process ``server.call_tool`` cannot answer this question.** That entry point
is the SDK's tool dispatcher; the ``ServerMiddleware`` tier sits above it, in
``ServerRunner._on_request``, and is reached only by a real inbound message. So
every test here goes through the transport: ``initialize``, the session id it
returns, the ``notifications/initialized`` that opens the gate, then a real
``tools/call``.

**The control is the point of the file.** A refusal test on its own passes
against a build whose handler merely ignored the unknown key -- which is exactly
what this surface did before the middleware existed, because the SDK's argument
model sets no ``extra="forbid"`` and pydantic's default is ``ignore``. So each
refusal is paired with the same call against the same server with this one
middleware lifted off, and what that pairing shows is not "the key is refused"
but *who refuses it*.

**Five tests here are the complement: what this seat deliberately does not
refuse.** Seated above the SDK's params validation, the middleware is handed the
*raw* inbound params, so it meets envelopes a conforming
``CallToolRequestParams`` could never produce -- a non-string ``name``, a
non-object ``arguments``, a null ``params`` object altogether. Those are passed
on and fail closed one tier down as ``INVALID_PARAMS``, while an absent
``arguments`` key is coerced to ``{}``, because that one is the legal spelling of
a call with no arguments and the published schema has to see the empty object.
Each is driven over raw JSON-RPC rather than through ``mcp_session``, whose
``call`` can only assemble a well-formed ``{"name", "arguments"}`` envelope and
so cannot express any of them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server import MCPServer
from mcp.types import INVALID_PARAMS
from starlette.testclient import TestClient

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.mcp.middleware import InputValidationMiddleware

from mcp_wire_session import headers, mcp_session, open_client, payload  # isort: skip

pytestmark = pytest.mark.integration

#: A value no message in this codebase could produce, so "the refusal does not
#: echo what the caller sent" is checkable by searching the whole response rather
#: than by reading the one field a test remembered to look at.
SENTINEL: Final = "sentinel-value-9d41c0f2"


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry(path=tmp_path / "projects.json")


def _without_the_middleware(server: MCPServer) -> MCPServer:
    """The same built server with this one middleware lifted off.

    Rebuilding a bare ``MCPServer`` here instead would vary the registration as
    well as the seat, and then a served call would prove nothing about which of
    the two differences served it. ``MCPServer.middleware`` is the live chain, so
    what is left is the SDK's own two built-ins and every tool exactly as
    ``build_server`` registered it.
    """
    server.middleware[:] = [
        entry for entry in server.middleware if not isinstance(entry, InputValidationMiddleware)
    ]
    assert not any(isinstance(entry, InputValidationMiddleware) for entry in server.middleware)
    return server


def _raw_tool_call(client: TestClient, session: str, params: object) -> dict[str, Any]:
    """One ``tools/call`` whose ``params`` cross the wire exactly as given.

    ``mcp_session``'s ``call`` always assembles ``{"name": tool, "arguments":
    {...}}``, which is the one thing the tests below must not do: the middleware
    reads the raw inbound params, above the SDK's params validation, so the
    envelopes it has to have a decision about are precisely the ones a
    conforming client cannot send.
    """
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": params},
        headers=headers(session),
    )
    assert response.status_code == 200, response.text
    return payload(response)


def test_build_server_seats_the_middleware_inside_the_sdks_own(registry: ProjectRegistry) -> None:
    """The seat is occupied, and its position is the SDK's rather than a choice.

    ``Server.__init__`` seeds the chain with ``OpenTelemetryMiddleware``,
    ``MCPServer.__init__`` appends ``RequestStateBoundary``, and only then is the
    caller's list extended in -- so Theurian's runs inside both. Asserted as
    *membership and lastness*, not as outermost: being outermost is not something
    this control needs or can promise.
    """
    chain = [type(entry).__name__ for entry in build_server(registry).middleware]

    assert chain[-1] == "InputValidationMiddleware", chain
    assert chain[:-1] == ["OpenTelemetryMiddleware", "RequestStateBoundary"], chain


def test_an_unknown_key_is_refused_and_the_refusal_names_the_key_not_the_value(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """A misspelled parameter is a refusal, and the refusal is safe to read.

    ``includeUnaproved`` is the shape this control exists for: one letter from a
    real parameter, accepted by every layer below, and answered confidently
    without the flag the caller believed it had set.

    Two assertions, and the second is the one ADR-0031 decision 4 owes: the
    message names the *key path* and the constraint, and the value the caller
    sent appears nowhere in the response. A refusal that echoed it would be an
    amplifier of the caller's own bytes at a boundary that takes untrusted input.
    """
    with mcp_session(build_server(registry), tmp_path / "data") as call:
        answer = call(
            "knowledge.search",
            {"projectId": "backend-service", "query": "auth", "includeUnaproved": SENTINEL},
        )

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    assert "includeUnaproved" in text, text
    assert "published input schema" in text, text
    assert SENTINEL not in json.dumps(answer), answer


def test_the_same_call_is_served_when_the_middleware_is_lifted_off(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The control: without this seat the unknown key is silently dropped.

    Run, not reasoned from the ADR. The SDK builds each tool's argument model
    with ``ArgModelBase``, which sets no ``extra="forbid"``, so pydantic's
    default of ``ignore`` applies and the key is gone before any Theurian code
    sees the request -- the handler cannot refuse what it is never shown.

    Two halves, because "served" has to mean more than "did not refuse". First,
    a tool whose answer needs no corpus is answered **successfully** with the
    unknown key attached. Second, the same tool answers **byte-identically**
    whether the key is sent or not, which is what "dropped" means and what makes
    the refusal above attributable to the middleware rather than to anything
    downstream noticing.
    """
    bare = _without_the_middleware(build_server(registry))

    with mcp_session(bare, tmp_path / "data") as call:
        with_unknown = call("project.list", {"unknownParameter": SENTINEL})
        without = call("project.list", {})

    assert with_unknown["result"]["isError"] is False, with_unknown
    assert with_unknown["result"] == without["result"]


def test_a_valid_call_is_served_unchanged_through_the_middleware(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The positive control: valid input reaches the handler, answer untouched.

    A refusal test with no served counterpart passes for a server that refuses
    everything. Compared against the same call on a server with the seat lifted
    off, so "unchanged" is measured against this build rather than against a
    remembered payload.
    """
    guarded = build_server(registry)
    bare = _without_the_middleware(build_server(registry))

    with mcp_session(guarded, tmp_path / "guarded") as call:
        through = call("project.list", {})
    with mcp_session(bare, tmp_path / "bare") as call:
        direct = call("project.list", {})

    assert through["result"]["isError"] is False, through
    assert through["result"]["structuredContent"] == {
        "count": 0,
        "projects": [],
        "unreadable": [],
        "remedy": None,
    }
    assert through["result"] == direct["result"]


def test_a_method_that_is_not_a_tool_call_passes_through_untouched(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """``tools/list`` is how a caller learns what to send, so refusing it would
    take away the remedy every refusal here names.

    The handshake is covered by every test in this file: ``mcp_session`` fails
    outright if ``initialize`` does not answer through the same chain.
    """
    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        listed = payload(
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                headers=headers(session),
            )
        )

    assert "error" not in listed, listed
    assert {tool["name"] for tool in listed["result"]["tools"]} == {
        "knowledge.search",
        "knowledge.get",
        "knowledge.status",
        "knowledge.proposeChange",
        "knowledge.generateMigrationDraft",
        "project.list",
        "review.findings",
        "review.generateKnowledgeCandidate",
        "review.search",
        "system.capabilities",
    }


def test_a_refusal_carries_the_shape_a_handler_refusal_carries(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """A caller must not be able to tell which tier refused it from the shape.

    This middleware answers without calling ``call_next``, which skips
    ``ServerRunner._serialize`` -- the per-version sieve that drops result fields
    the negotiated era does not describe. Returning the SDK's model straight out
    published ``resultType`` on this ``2025-06-18`` connection, where the same
    refusal raised inside a tool body carries ``content`` and ``isError`` alone.

    So the two are compared here rather than asserted apart: an unregistered
    project is a refusal the *handler* raises, and its key set is the one a
    schema refusal must have.
    """
    with mcp_session(build_server(registry), tmp_path / "data") as call:
        from_schema = call("knowledge.search", {"projectId": "backend-service", "unknown": 1})
        from_handler = call("knowledge.search", {"projectId": "backend-service", "query": "auth"})

    assert "published input schema" in from_schema["result"]["content"][0]["text"], from_schema
    assert from_schema["result"]["isError"] is True, from_schema
    assert from_handler["result"]["isError"] is True, from_handler
    assert set(from_schema["result"]) == set(from_handler["result"])
    assert "resultType" not in from_schema["result"], from_schema


def test_a_call_with_no_arguments_key_is_checked_as_the_empty_object(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """Omitting ``arguments`` is the legal spelling of a call with no arguments,
    so a tool that requires one is refused *by its published schema* rather than
    passed on unchecked.

    ``CallToolRequestParams`` types ``arguments`` as object-or-null, so this
    envelope is well-formed and the SDK dispatches it. Coercing absent to ``{}``
    is what makes the published contract answer -- ``tool-context.schema.json``'s
    ``"required": ["projectId"]``, reached by ``$ref`` from
    ``knowledge-search-input.schema.json`` -- and the assertion is on *which*
    tier answered.

    So it is on the refusal's text, not on ``isError``, which is true either way:
    with the coercion dropped the SDK's per-tool argument model answers instead,
    in pydantic's wording and naming the fields it found missing (measured
    against this build). Both are errors; only one is this control's.
    """
    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = _raw_tool_call(client, session, {"name": "knowledge.search"})

    assert "error" not in answer, answer
    assert answer["result"]["isError"] is True, answer
    text = answer["result"]["content"][0]["text"]
    assert "published input schema" in text, text
    assert "does not satisfy 'required'" in text, text
    assert "projectId" in text, text


def test_a_tool_that_needs_no_arguments_is_served_when_the_key_is_absent(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The coercion above must not cost a caller a call it is entitled to make.

    Asserted as equality against the same call sent with an explicit ``{}``,
    because "served" has to mean more than ``isError`` being false: the two
    legal spellings of a no-argument call are one call.

    This half carries no teeth of its own and is not meant to --
    ``test_a_call_with_no_arguments_key_is_checked_as_the_empty_object`` is the
    one that goes RED when the coercion is dropped, since ``project.list`` is
    served either way (measured). What this pins is the direction a future
    tightening of that guard must not break.
    """
    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        absent = _raw_tool_call(client, session, {"name": "project.list"})
        explicit = _raw_tool_call(client, session, {"name": "project.list", "arguments": {}})

    assert absent["result"]["isError"] is False, absent
    assert absent["result"]["structuredContent"] == {
        "count": 0,
        "projects": [],
        "unreadable": [],
        "remedy": None,
    }
    assert absent["result"] == explicit["result"]


def test_a_non_object_arguments_value_is_left_to_the_sdks_params_validation(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """A shape no conforming client can send is passed on, not answered here.

    Fail-closed either way: ``arguments`` is typed object-or-null, so the params
    model rejects this envelope inside ``call_next`` and the caller is answered
    ``INVALID_PARAMS`` where a served call would have carried a result. What the
    assertion pins is that it fails closed *there*: answering it at this tier
    would mean wording a refusal shape ``mcp/middleware.py`` deliberately does
    not own, since every string a caller reads comes from ``mcp/validation.py``.

    So ``result`` must be absent, not merely an error result. A guard that
    coerced this to ``{}`` would answer a schema refusal, which is ``isError``
    true and a different tier entirely.
    """
    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = _raw_tool_call(
            client, session, {"name": "knowledge.search", "arguments": "not-an-object"}
        )

    assert "result" not in answer, answer
    assert answer["error"]["code"] == INVALID_PARAMS, answer


def test_a_non_string_tool_name_is_left_to_the_sdks_params_validation(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """The same for a ``name`` that is not a string, and here the alternative is
    worse than refusing: it is inventing a name.

    A guard that substituted ``""`` would look that up, find no published schema
    and answer a *tool result* refusing a tool the caller never named -- a
    refusal distinction this tier is not allowed to create, since which inputs
    refuse and how they are told apart belongs to ``mcp/validation.py``.
    ``name`` is typed ``str`` with no default, so the SDK refuses the envelope
    itself and the caller is told the params were invalid, which is what they
    were.
    """
    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = _raw_tool_call(client, session, {"name": 123, "arguments": {}})

    assert "result" not in answer, answer
    assert answer["error"]["code"] == INVALID_PARAMS, answer


def test_a_tools_call_with_null_params_is_left_to_the_sdks_params_validation(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """A ``tools/call`` whose ``params`` is null is passed on, not crashed on.

    The middleware reads the raw inbound params above the SDK's validation, so it
    is handed ``params`` exactly as the wire sent it -- and ``"params": null`` is
    a shape a conforming client does not send but the transport carries. The
    ``params is None`` clause of ``_refusal``'s guard is what makes that a
    pass-through: it returns ``None``, the chain continues, and the SDK answers
    ``INVALID_PARAMS`` where a served call would have carried a result.

    Without that clause the next line is ``params.get("name")`` on ``None`` -- an
    ``AttributeError`` raised inside the middleware, before any handler, turning a
    fail-closed ``-32602`` into a crash. So the assertion is the ``-32602``
    disposition itself: ``result`` absent, ``error.code`` the SDK's, and the
    request handled at all rather than blown up at this seat. Driven over raw
    JSON-RPC because ``mcp_session``'s ``call`` always builds a ``params`` object
    and so cannot express a null one.
    """
    with open_client(build_server(registry), tmp_path / "data") as (client, session):
        answer = _raw_tool_call(client, session, None)

    assert "result" not in answer, answer
    assert answer["error"]["code"] == INVALID_PARAMS, answer
