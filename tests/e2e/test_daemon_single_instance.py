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
    #: The project's working tree. Carried because a test that has to *build*
    #: something -- `theurian findings build` reads this repository's git history
    #: -- needs the directory the CLI runs in, and deriving it from `tmp_path`
    #: again would be a second spelling of the fixture's own choice.
    root: Path


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
        yield Daemon("demo", port, token, data_dir, log, root)
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
        "review.findings",
        "system.capabilities",
    ]
    for name in tools:
        assert not any(verb in name for verb in ("create", "update", "delete", "write", "apply"))


def test_findings_are_refused_rather_than_answered_empty_before_a_build(
    running_daemon: Daemon,
) -> None:
    """ADR-0029 AC-3, over the wire: "never built" must not read as "no findings".

    The fixture project has never run `theurian findings build`, which is the
    state every project starts in -- so this is the answer most callers meet
    first, and `count: 0` here would be a false absence they act on. Driven
    through the real transport because that is where the refusal has to arrive
    as `isError` content rather than as a result: in-process tests see the
    exception, and the transport is the layer that turns it into what a client
    reads.
    """
    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        result = client.call("review.findings", {"projectId": "demo"})

    assert "_error" in result, f"a project with no findings store answered: {result}"
    assert "theurian findings build" in result["_error"]
    assert "count" not in result


def _git(root: Path, *args: str, when: str | None = None) -> None:
    """One git command in ``root``, blind to the developer's own git configuration.

    ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` go to ``os.devnull`` for the
    reason ``tests/integration/test_findings_build_cli.py`` records: a real
    ``~/.gitconfig`` carrying ``commit.gpgsign = true`` would make this file's
    fixture commits prompt for a passphrase or a hardware token, in a test that
    invokes no signing.
    """
    identity = (
        {}
        if when is None
        else {
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
        }
    )
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git from PATH, arguments are this file's own
        cwd=root,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            **identity,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )


#: The trailer whose text every findings assertion below keys on. Written once so
#: the corpus and the expectation cannot drift apart.
NEWEST_FINDING = "the daemon served a finding over the wire"
OLDEST_FINDING = "a bearer token reached the log"

#: A keyed line the grammar refuses. It is here because a response asserted over a
#: corpus with nothing to withhold asserts nothing about withholding: this is the
#: row that must not appear on the transport, in any field.
REJECTED_TRAILER = "Review-Finding: nonsense CRITICAL — the private key is in fixtures/"

#: The published bound on a served `findingText` (docs/protocol/mcp-tools.md), and
#: the length a cut value comes back at: the bound plus the three-character
#: marker. Spelled rather than imported, like the `between 1 and 100` assertion
#: below: this file speaks to the daemon the way a client does, and a client has
#: the published numbers and not the package. That these numbers *are* the build's
#: is pinned in process by
#: `test_review_findings_tool.py::test_the_published_bounds_are_the_bounds_this_build_enforces`.
SERVED_TEXT_BOUND = 2_000
CUT_TEXT_LENGTH = SERVED_TEXT_BOUND + len("...")

#: A trailer three times the served bound, committed as one line -- what a
#: repository contributor can put in front of this tool (T-5's actor, not the
#: caller). Oldest of the three, so it does not disturb the page-boundary
#: assertions above it.
PLANTED_FINDING = "z" * (SERVED_TEXT_BOUND * 3)


def _build_findings(daemon: Daemon) -> None:
    """Land a real findings store for ``daemon``'s project, the way a user does.

    Two commits carrying ``Review-Finding:`` trailers plus one malformed keyed
    line, published to a bare origin so ``refs/remotes/origin/main`` -- the one
    ref the source reads (ADR-0029 D7) -- resolves, then the installed
    ``theurian findings build``. Nothing here writes the store directly: the
    provenance record that makes it servable is the build command's own
    (ADR-0004, SEC-7, T-19), so a store planted by a test would be refused.
    """
    assert THEURIAN is not None
    origin = daemon.root.parent / "origin.git"
    _git(daemon.root.parent, "init", "--bare", "-q", "-b", "main", str(origin))
    _git(daemon.root, "remote", "add", "origin", str(origin))
    _git(
        daemon.root,
        "commit",
        "--allow-empty",
        "-m",
        f"fix: an earlier change\n\nReview-Finding: security CRITICAL — {OLDEST_FINDING}",
        when="2026-08-25T09:00:00+00:00",
    )
    _git(
        daemon.root,
        "commit",
        "--allow-empty",
        "-m",
        f"fix: a later change\n\nReview-Finding: adversarial HIGH — {NEWEST_FINDING}\n"
        f"{REJECTED_TRAILER}",
        when="2026-08-26T09:00:00+00:00",
    )
    _git(
        daemon.root,
        "commit",
        "--allow-empty",
        "-m",
        f"chore: a planted change\n\nReview-Finding: code-review LOW — {PLANTED_FINDING}",
        when="2026-08-24T09:00:00+00:00",
    )
    _git(daemon.root, "push", "-q", "origin", "main")
    _git(daemon.root, "fetch", "-q", "origin")

    subprocess.run(  # noqa: S603
        [THEURIAN, "findings", "build", "--json"],
        cwd=daemon.root,
        env={**os.environ, "THEURIAN_DATA_DIR": str(daemon.data_dir)},
        check=True,
        capture_output=True,
        timeout=60,
    )


def test_findings_are_served_over_the_transport_once_a_build_has_run(
    running_daemon: Daemon,
) -> None:
    """ADR-0029 phase-2 slice-3, over the wire a client actually speaks.

    Every other test of this tool calls ``server.call_tool`` in process. That
    reaches the tool body, and it does not reach the transport: no *successful*
    ``review.findings`` response had ever been observed over HTTP, so the parts
    that only the transport decides -- that a ``dict`` with a bool member and
    non-ASCII finding text survives serialization, and that a refusal arrives as
    ``isError`` content rather than as a result -- were held by nothing (PR #504
    round 1, fix stage). The companion in this file drives the *refused* side
    before a build; this drives the served side after one.

    Three properties, in one daemon's lifetime because each needs the same real
    store: the page boundary (``truncated``), the SEC-15 triple on a row a client
    renders, and a bound that refuses over the wire.
    """
    _build_findings(running_daemon)

    with _McpClient(running_daemon.port, running_daemon.token, "probe") as client:
        page = client.call("review.findings", {"projectId": "demo", "limit": 1})
        whole = client.call("review.findings", {"projectId": "demo"})
        refused = client.call("review.findings", {"projectId": "demo", "limit": 101})

    assert page["count"] == 1, f"the transport did not serve one finding: {page}"
    assert page["truncated"] is True, (
        f"a page of 1 over a two-finding corpus did not say it was truncated, so a "
        f"client reads it as the whole answer: {page}"
    )
    row = page["findings"][0]
    assert row["findingText"] == NEWEST_FINDING, "newest first, over the wire"
    assert row["reviewer"] == "adversarial"
    assert row["severity"] == "HIGH"
    assert row["contentClassification"] == "untrusted-knowledge"
    assert row["mayContainInstructions"] is True
    assert row["executable"] is False

    assert whole["count"] == 3, f"the build landed a corpus this test cannot reason about: {whole}"
    assert whole["truncated"] is False, "the whole answer must not claim more exists"
    assert [f["findingText"] for f in whole["findings"][:2]] == [NEWEST_FINDING, OLDEST_FINDING]

    # The byte bound, over the transport. The daemon fetches `bound + 1`
    # characters from the store and publishes `bound` plus the marker, so a
    # planted line three times the bound arrives at a fixed size whatever it held
    # -- and it arrives *marked*, so a client cannot read the cut as the whole
    # line. Asserted here because only the transport shows what a client receives:
    # the in-process tests see the tool's return value, not the serialized frame.
    planted = whole["findings"][2]
    assert len(planted["findingText"]) == CUT_TEXT_LENGTH, (
        f"a planted trailer of {len(PLANTED_FINDING)} characters arrived at "
        f"{len(planted['findingText'])} over the wire, not the published "
        f"{CUT_TEXT_LENGTH}: the bound a client is promised is not the one it gets"
    )
    assert planted["findingText"] == "z" * SERVED_TEXT_BOUND + "...", (
        "the cut value is not the bound's own prefix followed by the marker"
    )
    assert len(json.dumps(whole, ensure_ascii=False)) < 10_000, (
        "one planted trailer still sizes the whole response, so the bound is not "
        "reaching the bytes that cross the wire"
    )
    assert REJECTED_TRAILER not in json.dumps(whole, ensure_ascii=False), (
        "the malformed keyed line reached a client through the transport"
    )

    assert "_error" in refused, f"a limit past the published cap was answered: {refused}"
    assert "between 1 and 100" in refused["_error"], (
        "the published cap is `at most 100` (docs/protocol/mcp-tools.md); a caller "
        "over it must be refused naming that bound rather than silently clamped"
    )


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
