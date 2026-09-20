"""A minimal in-process Streamable HTTP MCP client, for the eval harness.

Adapted from ``packages/theurian-core/tests/mcp_wire_session.py`` -- the same
SSE-or-JSON read, the DNS-rebinding-safe ``base_url``, and the ``TestClient``
lifespan handling -- copied rather than imported because ``tools/eval`` is a
development tool and does not import from ``packages/theurian-core/tests``.

``server.call_tool`` cannot reach the SEC-12 input-validation middleware,
which runs only in front of a real inbound transport message, so every search
this harness runs goes through here.
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
PROTOCOL_VERSION: Final = "2025-06-18"
#: Not the daemon's default 7419: ``TestClient`` never binds a real socket, so
#: the number is cosmetic today, but 7419 is the resident dogfood daemon's
#: port and a future real-transport variant of this client must not collide
#: with it. Fed to both ``BASE_URL`` and ``DaemonConfig`` below -- the two
#: must agree, since ``build_app``'s DNS-rebinding allow-list is
#: ``{config.host}:{config.port}`` and a mismatch is the 421 that protection
#: exists to raise.
PORT: Final = 7420
#: DNS-rebinding protection rejects ``TestClient``'s default ``testserver`` host.
BASE_URL: Final = f"http://127.0.0.1:{PORT}"
_HTTP_OK: Final = 200

_INITIALIZE: Final = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "theurian-eval-harness", "version": "1"},
    },
}


class ToolCall(Protocol):
    def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def _headers(session: str | None = None) -> dict[str, str]:
    built = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session is not None:
        built["mcp-session-id"] = session
    return built


def _payload(response: Any) -> dict[str, Any]:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                parsed: dict[str, Any] = json.loads(line.split(":", 1)[1])
                return parsed
        raise RuntimeError(f"no data frame in the event stream: {response.text!r}")
    loaded: dict[str, Any] = response.json()
    return loaded


@contextmanager
def mcp_session(server: MCPServer, data_dir: Path) -> Iterator[ToolCall]:
    """A handshaken client with one ``tools/call`` entry point.

    The context manager is not optional: mounting the MCP app disables the
    SDK's own lifespan, and without this one the session manager never starts.
    """
    config = DaemonConfig(
        token=TOKEN, data_dir=data_dir, port=PORT, started_at=datetime.now(UTC).isoformat()
    )
    with TestClient(build_app(config, server), base_url=BASE_URL) as client:
        opened = client.post("/mcp", json=_INITIALIZE, headers=_headers())
        if opened.status_code != _HTTP_OK:
            raise RuntimeError(f"MCP handshake failed: {opened.status_code} {opened.text}")
        session = opened.headers["mcp-session-id"]
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_headers(session),
        )

        def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                headers=_headers(session),
            )
            if response.status_code != _HTTP_OK:
                raise RuntimeError(f"tools/call failed: {response.status_code} {response.text}")
            result = _payload(response).get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"malformed tools/call response for {tool!r}: {result!r}")
            if result.get("isError"):
                raise RuntimeError(f"`{tool}` returned an error: {result}")
            structured = result.get("structuredContent")
            if not isinstance(structured, dict):
                raise RuntimeError(f"`{tool}` published no structuredContent: {result!r}")
            return structured

        yield call
