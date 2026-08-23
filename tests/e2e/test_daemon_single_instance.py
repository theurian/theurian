"""The single-daemon guarantee, against a real process (ADR-0002, NFR-1, T-13).

Acceptance criterion 4: ten or more MCP clients connect concurrently and exactly
one daemon process exists. No in-process test can establish that — the failure
being prevented is *two operating-system processes* writing one SQLite file.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from migration_fixtures import body_pin

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]

STARTUP_TIMEOUT_SECONDS = 30.0
CONCURRENT_CLIENTS = 12

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed JWT.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth.md
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass(frozen=True)
class Daemon:
    """A running daemon and everything a test needs to talk to it."""

    project_id: str
    port: int
    token: str
    data_dir: Path
    log: Path


@pytest.fixture
def running_daemon(tmp_path: Path) -> Iterator[Daemon]:
    """A real daemon serving a real project."""
    assert THEURIAN is not None
    root = tmp_path / "demo"
    root.mkdir()
    data_dir = tmp_path / "datadir"
    env = {**os.environ, "THEURIAN_DATA_DIR": str(data_dir)}

    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    def cli(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [THEURIAN, *args], cwd=root, env=env, check=True, capture_output=True, timeout=60
        )

    cli("init", "--json")
    (root / ".theurian/knowledge/architecture/auth.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    cli("project", "register", "--json")
    cli("migrate", "apply", "--json")

    port = _free_port()
    log = tmp_path / "daemon.log"
    # A file rather than a pipe. Nobody drains a pipe while the test body runs,
    # so a daemon that logged more than the 64 KiB buffer would block forever on
    # its own stderr -- and the log has to outlive the process for the
    # token-leak assertion below.
    with log.open("wb") as sink:
        process = subprocess.Popen(  # noqa: S603
            [THEURIAN, "daemon", "start", "--foreground", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )

    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _health(port) is not None:
                break
            if process.poll() is not None:
                pytest.fail(f"daemon exited: {log.read_text()}")
            time.sleep(0.2)
        else:
            pytest.fail(f"daemon did not become healthy in time: {log.read_text()}")

        token = (data_dir / "auth" / "mcp-token").read_text().strip()
        yield Daemon("demo", port, token, data_dir, log)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=10)


def _health(port: int) -> dict[str, Any] | None:
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


class _McpClient:
    """A minimal Streamable HTTP MCP client.

    Hand-rolled rather than borrowing the SDK's, so the test exercises the wire
    protocol a real client speaks rather than the SDK's own code paths.

    Owns its connection explicitly. ``urllib`` leaves a keep-alive socket to the
    garbage collector, which under ``filterwarnings = error`` fails the run from
    outside any test.
    """

    def __init__(self, port: int, token: str, name: str) -> None:
        self._connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self._token = token
        self._name = name
        self._session: dict[str, str] = {}

    def __enter__(self) -> _McpClient:
        return self.connect()

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
        # Drained in full: http.client cannot reuse the connection for the next
        # request until the current response is consumed.
        raw = response.read().decode()

        if not raw.strip():
            return {}
        match = re.search(r"^data: (.*)$", raw, re.MULTILINE)
        parsed: dict[str, Any] = json.loads(match.group(1) if match else raw)
        return parsed

    def connect(self) -> _McpClient:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": self._name, "version": "1"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        result = response.get("result", {})
        if result.get("isError"):
            return {"_error": result["content"][0]["text"]}
        structured = result.get("structuredContent")
        if structured is not None:
            parsed: dict[str, Any] = structured
            return parsed
        loaded: dict[str, Any] = json.loads(result["content"][0]["text"])
        return loaded

    def tools(self) -> list[str]:
        response = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        return sorted(t["name"] for t in response["result"]["tools"])


def _listening_pids(port: int) -> set[str]:
    result = subprocess.run(  # noqa: S603
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return {line.split()[1] for line in result.stdout.splitlines()[1:] if line.split()}


# -- Acceptance criterion 4 ------------------------------------------------


def test_many_concurrent_clients_share_one_daemon(running_daemon: Daemon) -> None:
    """The criterion the whole design exists to satisfy.

    A stdio MCP server would spawn one process per client. Twelve clients would
    mean twelve writers on one SQLite file, which is corruption rather than
    slowness (ADR-0002).
    """

    def query(index: int) -> int:
        with _McpClient(running_daemon.port, running_daemon.token, f"agent-{index}") as client:
            result = client.call(
                "knowledge.search", {"projectId": running_daemon.project_id, "query": "JWT"}
            )
        return int(result["count"])

    with ThreadPoolExecutor(max_workers=CONCURRENT_CLIENTS) as pool:
        counts = list(pool.map(query, range(CONCURRENT_CLIENTS)))

    assert len(counts) == CONCURRENT_CLIENTS
    assert set(counts) == {1}, "every client must see the same knowledge"
    assert len(_listening_pids(running_daemon.port)) == 1, "exactly one daemon process"


def test_concurrent_starts_produce_one_winner(running_daemon: Daemon) -> None:
    """Reuse is a success, not an error.

    A second starter that confirms the first is healthy has done its job. It
    never kills the winner and never repairs data.
    """
    assert THEURIAN is not None
    port = running_daemon.port
    env = {**os.environ, "THEURIAN_DATA_DIR": str(running_daemon.data_dir)}

    def start(_: int) -> tuple[int, str]:
        completed = subprocess.run(  # noqa: S603
            [THEURIAN, "daemon", "start", "--foreground", "--port", str(port), "--json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        decision = ""
        if completed.stdout.strip():
            decision = json.loads(completed.stdout).get("decision", "")
        return completed.returncode, decision

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(start, range(5)))

    assert all(code == 0 for code, _ in outcomes), "a loser must exit 0, not fail"
    assert {decision for _, decision in outcomes} == {"reuse"}
    assert len(_listening_pids(port)) == 1


# -- Security --------------------------------------------------------------


def test_health_is_reachable_without_a_credential(running_daemon: Daemon) -> None:
    health = _health(running_daemon.port)

    assert health is not None
    assert health["status"] == "ok"
    assert Path(health["dataDir"]).resolve() == running_daemon.data_dir.resolve()


def test_mcp_without_a_token_is_refused(running_daemon: Daemon) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", running_daemon.port, timeout=10)
    try:
        connection.request(
            "POST",
            "/mcp",
            body=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode()
    finally:
        connection.close()

    assert response.status == 401
    assert response.getheader("WWW-Authenticate") == "Bearer"
    assert running_daemon.token not in body


def test_the_token_file_is_private(running_daemon: Daemon) -> None:
    token_file = running_daemon.data_dir / "auth" / "mcp-token"

    assert token_file.stat().st_mode & 0o777 == 0o600
    assert (running_daemon.data_dir / "auth").stat().st_mode & 0o777 == 0o700


def test_the_token_never_reaches_the_log(running_daemon: Daemon) -> None:
    """SEC-6, T-9. The only end-to-end assertion over a real daemon's real log.

    It does **not** hold because `daemon/runner.py` passes `access_log=False`,
    which is what this docstring used to claim. Measured with both uvicorn
    arguments switched back on — `access_log=True`, `log_level="debug"` — the
    access lines carry no header at all: `uvicorn.logging.AccessFormatter`
    formats `client_addr`, `method`, `full_path`, `http_version` and
    `status_code`, and an `Authorization` header is not among them. Neither flip
    makes this test red.

    What keeps the credential out is that nothing in this stack logs request
    headers — wider than the recorded reason, and nobody's decision, which is
    exactly why it is worth asserting rather than reasoning about. This test
    fails when some component starts writing a header or a token into that file,
    which is the event no configuration flag announces.

    A weaker guard than it reads, and kept for what it does cover: the whole log
    of a real process across a real authenticated MCP call.
    """
    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        client.tools()

    assert running_daemon.token not in running_daemon.log.read_text(errors="replace")


def test_an_unregistered_project_is_refused(running_daemon: Daemon) -> None:
    """SEC-13. A client asking for another project gets an error naming what is
    registered, never another project's knowledge."""
    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        result = client.call("knowledge.search", {"projectId": "not-registered", "query": "x"})

    assert "_error" in result
    assert "not registered" in result["_error"]


# -- Tool surface ----------------------------------------------------------


def test_the_tool_set_is_read_only(running_daemon: Daemon) -> None:
    """ADR-0013. Milestone 3 ships no write-intent tool at all, so there is no
    path from MCP to approved state -- not behind a flag, not behind a
    permission.
    """
    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        tools = client.tools()

    assert tools == [
        "knowledge.get",
        "knowledge.search",
        "knowledge.status",
        "project.list",
        "system.capabilities",
    ]
    for name in tools:
        assert not any(verb in name for verb in ("create", "update", "delete", "write", "apply"))


def test_results_carry_provenance_and_trust_labels(running_daemon: Daemon) -> None:
    """SEC-15, FR-R5. A result with no anchor is an unverifiable assertion, and
    one without the labels invites an agent to read a document as an
    instruction.
    """
    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        results = client.call(
            "knowledge.search", {"projectId": running_daemon.project_id, "query": "JWT"}
        )
    hit = results["results"][0]

    assert hit["contentClassification"] == "untrusted-knowledge"
    assert hit["mayContainInstructions"] is True
    assert hit["executable"] is False
    assert hit["sourceAnchors"], "every result must reach its origin"
    assert hit["revisionId"]
    assert hit["trustLevel"] == "reviewed"


def test_capabilities_report_no_write_tools(running_daemon: Daemon) -> None:
    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        capabilities = client.call("system.capabilities", {})

    assert capabilities["capabilities"]["writeTools"] is False
