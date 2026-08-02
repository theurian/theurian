"""The daemon: auth boundary, health endpoint, and single-instance guard.

Uses a real ASGI transport rather than mocks. The auth middleware, the route
ordering, and the mount are exactly the things a mock would paper over.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import override

import pytest
from starlette.testclient import TestClient

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.instance import (
    InstanceLock,
    StartDecision,
    check_can_start,
    port_is_free,
    probe_health,
)
from theurian.daemon.runner import build_server, ensure_token, prepare
from theurian.daemon.server import DaemonConfig, build_app
from theurian.infrastructure.secrets.file_store import (
    TOKEN_KEY,
    FileSecretStore,
    InsecureSecretPermissionsError,
    env_file_contents,
)
from theurian.security.tokens import generate_token

pytestmark = pytest.mark.integration

TOKEN = generate_token()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A client with the app's lifespan actually running.

    Entering the context manager matters: mounting the MCP app disables the
    SDK's own lifespan, so ours must start the session manager. Without it every
    MCP request fails with "Task group is not initialized", and a test that
    skipped the lifespan would never notice.
    """
    config = DaemonConfig(
        token=TOKEN,
        data_dir=tmp_path / "data",
        started_at=datetime.now(UTC).isoformat(),
    )
    registry = ProjectRegistry(path=tmp_path / "projects.json")
    # base_url sets the Host header. DNS-rebinding protection rejects
    # TestClient's default `testserver`, which is the control working -- see
    # test_a_foreign_host_header_is_rejected below.
    with TestClient(
        build_app(config, build_server(registry)), base_url="http://127.0.0.1:7419"
    ) as client:
        yield client


def _auth() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


# -- /health ---------------------------------------------------------------


def test_health_needs_no_credential(client: TestClient) -> None:
    """This is what SessionStart and the instance probe call. Requiring a
    credential would push credential handling into a hook that runs on every
    session (ADR-0011)."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reveals_nothing_about_knowledge(client: TestClient) -> None:
    """Deliberately uninformative: enough to decide whether to start a daemon
    and whether it is *this* one, nothing more."""
    body = client.get("/health").json()

    assert set(body) == {"status", "version", "protocolVersion", "dataDir", "startedAt"}


def test_health_does_not_leak_the_token(client: TestClient) -> None:
    assert TOKEN not in client.get("/health").text


# -- Authentication --------------------------------------------------------


def test_mcp_without_a_token_is_refused(client: TestClient) -> None:
    response = client.post("/mcp", json=INITIALIZE)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_mcp_with_a_wrong_token_is_refused(client: TestClient) -> None:
    headers = {**_auth(), "Authorization": f"Bearer {generate_token()}"}
    assert client.post("/mcp", json=INITIALIZE, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "header", ["", "Basic abc", "Bearer", "bearer lowercase-scheme-token-aaaaaaaaaaaaaaaa"]
)
def test_malformed_authorization_headers_are_refused(client: TestClient, header: str) -> None:
    headers = {**_auth(), "Authorization": header}
    assert client.post("/mcp", json=INITIALIZE, headers=headers).status_code == 401


def test_the_401_names_the_fix_without_revealing_the_token(client: TestClient) -> None:
    """A bare 401 on a tool you just installed is a mystery. The variable name
    is public; the token is not."""
    response = client.post("/mcp", json=INITIALIZE)
    detail = response.json()["detail"]

    assert "THEURIAN_MCP_TOKEN" in detail
    assert "doctor" in detail
    assert TOKEN not in response.text


def test_a_valid_token_reaches_the_mcp_server(client: TestClient) -> None:
    response = client.post("/mcp", json=INITIALIZE, headers=_auth())

    assert response.status_code == 200
    assert "mcp-session-id" in response.headers


def test_mcp_is_served_without_a_redirect(client: TestClient) -> None:
    """A 307 on POST loses the body in some clients, so the documented endpoint
    would work for some callers and silently fail for others."""
    response = client.post("/mcp", json=INITIALIZE, headers=_auth(), follow_redirects=False)

    assert response.status_code == 200, "must answer /mcp directly, not redirect to /mcp/"


def test_a_foreign_host_header_is_rejected(client: TestClient) -> None:
    """T-2, SEC-2. A page the user visits can resolve a hostname to 127.0.0.1 so
    the browser treats the request as same-origin. Validating Host is what stops
    that reaching the MCP endpoint.
    """
    headers = {**_auth(), "Host": "evil.test"}
    response = client.post("/mcp", json=INITIALIZE, headers=headers)

    assert response.status_code != 200


def test_a_cross_origin_request_is_rejected(client: TestClient) -> None:
    headers = {**_auth(), "Origin": "https://evil.test"}
    response = client.post("/mcp", json=INITIALIZE, headers=headers)

    assert response.status_code != 200


# -- Binding ---------------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])  # noqa: S104 - the point
def test_binding_a_non_loopback_address_is_refused(host: str, tmp_path: Path) -> None:
    """SEC-1. A networked deployment needs TLS, OAuth 2.1, audience validation,
    and tenant isolation; shipping half of them would be worse than none."""
    with pytest.raises(ValueError, match="loopback-only"):
        DaemonConfig(token=TOKEN, data_dir=tmp_path, host=host)


def test_loopback_addresses_are_accepted(tmp_path: Path) -> None:
    for host in ("127.0.0.1", "localhost", "::1"):
        DaemonConfig(token=TOKEN, data_dir=tmp_path, host=host)


# -- Secret storage --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_token_round_trips_with_restrictive_permissions(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path)
    token = generate_token()

    await store.set(TOKEN_KEY, token)

    assert await store.get(TOKEN_KEY) == token
    path = tmp_path / "auth" / TOKEN_KEY
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "auth").stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
async def test_a_world_readable_token_is_refused(tmp_path: Path) -> None:
    """Refused rather than repaired-and-used: a token other accounts could
    already read is not a credential any more."""
    store = FileSecretStore(tmp_path)
    await store.set(TOKEN_KEY, generate_token())
    os.chmod(tmp_path / "auth" / TOKEN_KEY, 0o644)

    with pytest.raises(InsecureSecretPermissionsError, match="rotate"):
        await store.get(TOKEN_KEY)


@pytest.mark.asyncio
async def test_a_missing_secret_is_none_not_an_error(tmp_path: Path) -> None:
    assert await FileSecretStore(tmp_path).get(TOKEN_KEY) is None


@pytest.mark.asyncio
async def test_replacing_a_secret_keeps_it_private(tmp_path: Path) -> None:
    """An existing file keeps its old mode through O_CREAT."""
    store = FileSecretStore(tmp_path)
    await store.set(TOKEN_KEY, "first-token-value-aaaaaaaaaaaaaaaaaaaaaaa")
    os.chmod(tmp_path / "auth" / TOKEN_KEY, 0o644)

    await store.set(TOKEN_KEY, "second-token-value-bbbbbbbbbbbbbbbbbbbbbb")

    assert (tmp_path / "auth" / TOKEN_KEY).stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_ensure_token_never_regenerates(tmp_path: Path) -> None:
    """Silently replacing a token breaks every configured client at once, with
    no explanation. Rotation is explicit (ADR-0011)."""
    first = await ensure_token(tmp_path)
    second = await ensure_token(tmp_path)

    assert first == second


@pytest.mark.asyncio
async def test_a_key_cannot_escape_the_auth_directory(tmp_path: Path) -> None:
    from theurian.domain.errors import SecurityError

    store = FileSecretStore(tmp_path)
    for key in ("../escape", "sub/dir", ".hidden"):
        with pytest.raises(SecurityError):
            await store.get(key)


def test_the_env_file_references_the_token_rather_than_embedding_it(tmp_path: Path) -> None:
    """SEC-5. The secret lives in exactly one place; everything else points."""
    contents = env_file_contents(tmp_path)

    assert "THEURIAN_MCP_TOKEN" in contents
    assert str(tmp_path / "auth" / TOKEN_KEY) in contents
    assert "export" in contents


# -- Single instance -------------------------------------------------------


def test_the_lock_is_exclusive(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "daemon.lock")
    second = InstanceLock(tmp_path / "daemon.lock")

    assert first.acquire()
    try:
        assert not second.acquire(), "two processes must not both hold the lock"
    finally:
        first.release()

    assert second.acquire(), "the lock is available once released"
    second.release()


def test_the_lock_is_released_by_its_context_manager(tmp_path: Path) -> None:
    path = tmp_path / "daemon.lock"
    with InstanceLock(path):
        assert not InstanceLock(path).acquire()

    other = InstanceLock(path)
    assert other.acquire()
    other.release()


def test_a_free_port_and_a_free_lock_means_start(tmp_path: Path) -> None:
    lock = InstanceLock(tmp_path / "daemon.lock")
    check = check_can_start(lock, tmp_path, port=_free_port())

    assert check.decision is StartDecision.START
    assert check.may_start
    lock.release()


def test_an_occupied_port_with_no_health_is_a_conflict(tmp_path: Path) -> None:
    """Something that is not a Theurian daemon holds the port. Not ours to
    displace."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]

        lock = InstanceLock(tmp_path / "daemon.lock")
        check = check_can_start(lock, tmp_path, port=port)

    assert check.decision is StartDecision.CONFLICT
    assert not check.may_start
    assert "not a Theurian daemon" in check.detail


def test_a_held_lock_with_no_listener_is_stale_not_repaired(tmp_path: Path) -> None:
    """Reported, never auto-repaired: deleting state a wedged process may still
    be writing is worse than stopping and saying so."""
    holder = InstanceLock(tmp_path / "daemon.lock")
    holder.acquire()
    try:
        check = check_can_start(InstanceLock(tmp_path / "daemon.lock"), tmp_path, port=_free_port())
    finally:
        holder.release()

    assert check.decision is StartDecision.STALE
    assert "doctor" in check.detail
    assert "no data is removed" in check.detail


# -- The startup handshake -------------------------------------------------
#
# The lock and the port probe answer "is something there?". Only the handshake
# answers "is it *ours*?", and that is the question that matters: reusing a
# daemon that serves a different data directory would answer every query from
# the wrong knowledge base, silently and forever.


class _LocalHTTPServer(HTTPServer):
    """``HTTPServer`` without the reverse DNS lookup.

    ``HTTPServer.server_bind`` calls :func:`socket.getfqdn`, which blocks for
    about thirty seconds on a machine whose hostname does not resolve -- and the
    answer is cached, so exactly one test in the file pays for it and the cause
    looks like whichever test happened to run first.
    """

    @override
    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


@contextmanager
def _fake_daemon(data_dir: str, version: str = "9.9.9") -> Iterator[int]:
    """A server that answers /health the way a Theurian daemon does."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"status": "ok", "version": version, "dataDir": data_dir}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, *_: object) -> None:
            """Silent: the default handler writes to stderr on every probe."""

    server = _LocalHTTPServer(("127.0.0.1", 0), Handler)
    # poll_interval bounds how long shutdown() blocks; the 0.5s default is the
    # dominant cost of every test that uses this fixture.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_a_daemon_on_our_data_directory_is_reused(tmp_path: Path) -> None:
    """Reuse is the whole point of one daemon per user (ADR-0002)."""
    lock = InstanceLock(tmp_path / "daemon.lock")
    with _fake_daemon(str(tmp_path)) as port:
        check = check_can_start(lock, tmp_path, port=port)

    assert check.decision is StartDecision.REUSE
    assert not check.may_start
    assert check.existing_version == "9.9.9"


def test_a_daemon_on_a_different_data_directory_is_a_conflict(tmp_path: Path) -> None:
    """Not reused and not killed. It belongs to someone else, and its knowledge
    is not this project's."""
    other = tmp_path / "somebody-elses-profile"
    lock = InstanceLock(tmp_path / "daemon.lock")

    with _fake_daemon(str(other)) as port:
        check = check_can_start(lock, tmp_path, port=port)

    assert check.decision is StartDecision.CONFLICT
    assert not check.may_start
    assert "different data directory" in check.detail
    assert str(other) in check.detail, "the operator has to be able to find it"
    assert check.existing_data_dir == str(other)


def test_a_symlinked_data_directory_is_still_recognised_as_ours(tmp_path: Path) -> None:
    """`/tmp` is a symlink to `/private/tmp` on macOS, so a daemon started
    through one path and probed through the other would look foreign and turn
    every start into a conflict."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    lock = InstanceLock(tmp_path / "daemon.lock")
    with _fake_daemon(str(real)) as port:
        check = check_can_start(lock, link, port=port)

    assert check.decision is StartDecision.REUSE


def test_a_held_lock_plus_a_healthy_daemon_is_reuse_not_stale(tmp_path: Path) -> None:
    """The normal race: two starters, one already serving. The loser must not
    read a held lock as a wedged process."""
    holder = InstanceLock(tmp_path / "daemon.lock")
    holder.acquire()
    try:
        with _fake_daemon(str(tmp_path)) as port:
            check = check_can_start(InstanceLock(tmp_path / "daemon.lock"), tmp_path, port=port)
    finally:
        holder.release()

    assert check.decision is StartDecision.REUSE


def test_probing_a_server_that_is_not_theurian_yields_nothing(tmp_path: Path) -> None:
    """SEC-2 in the other direction: whatever is on 7419 may be hostile. A
    non-JSON body must not become a health verdict."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]

        assert probe_health(port=port, timeout=0.5) is None


def test_prepare_reports_without_starting_anything(tmp_path: Path) -> None:
    """`daemon status` must answer the same question the starter asks, without
    binding a port or writing state."""
    port = _free_port()
    check, lock, resolved = prepare(tmp_path, port=port)
    lock.release()

    assert check.decision is StartDecision.START
    assert resolved == tmp_path
    assert port_is_free(port=port), "asking must not leave a listener behind"


def test_port_is_free_detects_an_occupied_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        assert not port_is_free(port=squatter.getsockname()[1])


def test_probing_nothing_returns_none() -> None:
    assert probe_health(port=_free_port(), timeout=0.5) is None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
