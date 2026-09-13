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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from mcp.server import MCPServer

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
        "project.list",
        "review.findings",
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
