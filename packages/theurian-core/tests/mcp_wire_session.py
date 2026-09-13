"""A handshaken MCP client over a built server, for tests that need the wire.

**In-process ``server.call_tool`` cannot reach the middleware tier.** That entry
point is the SDK's tool dispatcher; a ``ServerMiddleware`` runs above it, in
``ServerRunner._on_request``, and is reached only by a real inbound message. So
a test of anything seated there has to go through the transport: ``initialize``,
the session id it returns, the ``notifications/initialized`` that opens the
gate, then a real ``tools/call``.

Extracted here rather than copied per module: the plumbing below carries three
measured decisions -- the SSE-or-JSON read, the ``base_url`` that satisfies
DNS-rebinding protection, and the ``TestClient`` context manager the ASGI
lifespan depends on -- and a second copy is a second place for one of them to
be quietly dropped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

from mcp.server import MCPServer
from starlette.testclient import TestClient

from theurian.daemon.server import DaemonConfig, build_app
from theurian.security.tokens import generate_token

TOKEN: Final = generate_token()

#: The negotiated era these sessions speak. Named rather than left implicit
#: because the refusal envelope the input-validation middleware owns is
#: era-dependent: a modern connection requires ``resultType`` and this one does
#: not describe it, so an assertion about the answer's shape is an assertion
#: about this string too.
PROTOCOL_VERSION: Final = "2025-06-18"

INITIALIZE: Final = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}

#: The ``Host`` a ``TestClient`` must present. DNS-rebinding protection rejects
#: its default ``testserver``, so ``base_url`` is not cosmetic.
BASE_URL: Final = "http://127.0.0.1:7419"


class ToolCall(Protocol):
    """What :func:`mcp_session` yields: one ``tools/call`` on an open session."""

    def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def headers(session: str | None = None) -> dict[str, str]:
    built = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session is not None:
        built["mcp-session-id"] = session
    return built


def payload(response: Any) -> dict[str, Any]:
    """One JSON-RPC message out of a response that may be SSE or plain JSON.

    Streamable HTTP answers a POST with ``text/event-stream`` whenever the
    client accepts it, which this client does because a real one does. Reading
    only ``response.json()`` would pass on a transport that stopped streaming
    and fail on the one this daemon actually serves.
    """
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                parsed: dict[str, Any] = json.loads(line.split(":", 1)[1])
                return parsed
        raise AssertionError(f"no data frame in the event stream: {response.text!r}")
    loaded: dict[str, Any] = response.json()
    return loaded


@contextmanager
def open_client(server: MCPServer, data_dir: Path) -> Iterator[tuple[TestClient, str]]:
    """A handshaken client and its session id, for a test that needs the client
    itself -- ``tools/list``, a notification, a second connection.

    The context manager is not optional: mounting the MCP app disables the SDK's
    own lifespan, and without ours the session manager never starts, so every
    request fails with "Task group is not initialized".
    """
    config = DaemonConfig(token=TOKEN, data_dir=data_dir, started_at=datetime.now(UTC).isoformat())
    with TestClient(build_app(config, server), base_url=BASE_URL) as client:
        opened = client.post("/mcp", json=INITIALIZE, headers=headers())
        assert opened.status_code == 200, opened.text
        session = opened.headers["mcp-session-id"]
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers(session),
        )
        yield client, session


@contextmanager
def mcp_session(server: MCPServer, data_dir: Path) -> Iterator[ToolCall]:
    """A client that has completed the handshake, so ``tools/call`` is reachable."""
    with open_client(server, data_dir) as (client, session):

        def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                headers=headers(session),
            )
            assert response.status_code == 200, response.text
            return payload(response)

        yield call
