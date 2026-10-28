"""A real daemon on a free port and a plain Streamable HTTP MCP client, shared by
the e2e modules that drive the wire -- extracted here rather than copied per
module, following the precedent of ``packages/theurian-core/tests/mcp_wire_session.py``,
which was extracted for the same reason."""

from __future__ import annotations

import http.client
import json
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

STARTUP_TIMEOUT_SECONDS = 30.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def health(port: int) -> dict[str, Any] | None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status != 200:
            return None
        payload: dict[str, Any] = json.loads(response.read().decode())
    except OSError:
        return None
    finally:
        connection.close()
    return payload


@contextmanager
def serve_daemon(
    theurian: str, root: Path, env: dict[str, str], log: Path
) -> Iterator[tuple[int, str]]:
    """``theurian daemon start --foreground`` on a free port; yields ``(port, token)``.

    The token is read from ``env["THEURIAN_DATA_DIR"]``, the directory the daemon uses."""
    port = free_port()
    with log.open("wb") as sink:
        process = subprocess.Popen(  # noqa: S603
            [theurian, "daemon", "start", "--foreground", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if health(port) is not None:
                break
            if process.poll() is not None:
                pytest.fail(f"daemon exited: {log.read_text()}")
            time.sleep(0.2)
        else:
            pytest.fail(f"daemon did not become healthy in time: {log.read_text()}")

        yield port, (Path(env["THEURIAN_DATA_DIR"]) / "auth" / "mcp-token").read_text().strip()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=10)


class McpClient:
    """A minimal Streamable HTTP MCP client, owning its connection explicitly so
    no keep-alive socket is left to the garbage collector under ``filterwarnings =
    error``."""

    def __init__(self, port: int, token: str) -> None:
        self._connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self._token = token
        self._session: dict[str, str] = {}

    def __enter__(self) -> McpClient:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "session", "version": "1"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *_: object) -> None:
        self._connection.close()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._connection.request(
            "POST",
            "/mcp",
            body=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self._session,
            },
        )
        response = self._connection.getresponse()
        if session_id := response.getheader("mcp-session-id"):
            self._session["mcp-session-id"] = session_id
        raw = response.read().decode()
        if not raw.strip():
            return {}
        match = re.search(r"^data: (.*)$", raw, re.MULTILINE)
        parsed: dict[str, Any] = json.loads(match.group(1) if match else raw)
        return parsed

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Every registered tool's served ``inputSchema`` (the SDK's, built from the
        handler signature, not the ``schemas/mcp`` files), by name."""
        response = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        return {tool["name"]: tool.get("inputSchema", {}) for tool in response["result"]["tools"]}

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        result: dict[str, Any] = response["result"]
        return result
