"""The daemon: auth boundary, health endpoint, and single-instance guard.

Uses a real ASGI transport rather than mocks. The auth middleware, the route
ordering, and the mount are exactly the things a mock would paper over.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Final, override

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from theurian.application.authorization import (
    AuthorizationGrant,
    serving_profile_path,
)
from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.instance import (
    InstanceLock,
    InstanceLockError,
    StartDecision,
    check_can_start,
    port_is_free,
    probe_health,
)
from theurian.daemon.runner import build_server, ensure_token, prepare, serve
from theurian.daemon.server import DaemonConfig, build_app
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.secrets.file_store import (
    TOKEN_KEY,
    FileSecretStore,
    InsecureSecretPermissionsError,
)
from theurian.infrastructure.sqlite.connection import LOCK_OPEN_FLAGS
from theurian.infrastructure.sqlite.schema import irregular_shape, irregular_shape_at
from theurian.security.env_file import env_file_contents
from theurian.security.no_follow import irregular_artefact_remedy, symbolic_link_remedy
from theurian.security.tokens import generate_token

pytestmark = pytest.mark.integration

TOKEN = generate_token()

#: Where a POSIX mode decides nothing, so a test that turns one on must not
#: run. The suite's standing idiom: Windows has no mode bits, and root is
#: exempt from the ones it has -- the offline CI image runs as root.
_CANNOT_BE_REFUSED_BY_A_MODE: Final = sys.platform == "win32" or os.geteuid() == 0


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


# -- The instance lock opens the way the write lock does (round one, H-3) -----
#
# `InstanceLock.acquire` opened with `Path.open("w")` until this section landed,
# while `connection.py` said the two lock sites were "the same call, same flags,
# same kind of file". Measured 2026-09-06 against that method, both faces reached
# by the documented `theurian daemon start`:
#
#   - a symbolic link at `daemon.lock` pointing into the user's tree was
#     followed and truncated -- down to this method's own breadcrumb, which is a
#     JSON object holding a pid and so has no fixed length -- and `acquire`
#     returned `True`, so the daemon started and reported success. The victim's
#     size before is `len(_LOCK_VICTIM_BODY)`, read off the constant below rather
#     than written out here: the number differs from the write lock's 49-byte
#     victim in `test_migrate_apply_lock_confinement.py`, and two files quoting
#     one number is how they came to be confused;
#   - a named pipe at the same path blocked inside `open()` past a 15-second
#     kill, so nothing was published at all.
#
# Both are #481's and #526's shapes on a second lock path, and the fix is to
# share `LOCK_OPEN_FLAGS` rather than to re-spell it.

_LOCK_VICTIM_BODY: Final = "# Notes\n\nSomething the operator wrote themselves.\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symbolic_link_at_the_instance_lock_is_refused_rather_than_written_through(
    tmp_path: Path,
) -> None:
    """RED before the flags were shared: the victim's bytes are the assertion.

    ``Path.open("w")`` follows a link and truncates what it names, so taking the
    instance lock destroyed a file the operator wrote -- and returned ``True``,
    which is worse than failing: the daemon came up and said so. The whole body
    is compared rather than its length, so the assertion says "unchanged" instead
    of "still some bytes".

    **The refusal must call it a symbolic link, not a named pipe** (round two).
    ``irregular_shape_at`` follows the link, so a link *to* a FIFO was described
    by its target's shape -- true of what the link names and false of the artefact
    at the path, which is the mis-description #520 removed from the write lock's
    own sentence. The errno arm answers first, and the cure is the shared
    ``symbolic_link_remedy`` the other five sites publish.
    """
    victim = tmp_path / "notes.md"
    victim.write_text(_LOCK_VICTIM_BODY)
    link = tmp_path / "daemon.lock"
    link.symlink_to(victim)

    with pytest.raises(InstanceLockError) as caught:
        InstanceLock(link).acquire()

    assert victim.read_text() == _LOCK_VICTIM_BODY, (
        "taking the instance lock wrote through a symbolic link at its path and "
        "truncated the file the link named"
    )
    assert "is a symbolic link" in str(caught.value), (
        f"the refusal does not call the artefact a symbolic link: {caught.value}"
    )
    assert caught.value.remedy == symbolic_link_remedy(link), (
        f"the cure is not the one every other symbolic-link refusal publishes, so an "
        f"operator meeting this one reads a different sentence for the same artefact: "
        f"{caught.value.remedy!r}"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
def test_a_link_to_a_named_pipe_is_named_by_the_artefact_not_by_its_target(
    tmp_path: Path,
) -> None:
    """Round two: the shape probe follows the link and answered for the target.

    The combination is what separates the two arms -- a link whose target is a
    named pipe. Keyed on shape, the refusal said "is a named pipe (FIFO)" about a
    path holding a symlink, and told the operator to remove something that is not
    what is there. Keyed on the errno, ``O_NOFOLLOW``'s ``ELOOP`` names the link.

    In-process: ``O_NOFOLLOW`` refuses at the link, so the open never reaches the
    pipe and cannot block.
    """
    target = tmp_path / "pipe"
    os.mkfifo(target)
    link = tmp_path / "daemon.lock"
    link.symlink_to(target)

    with pytest.raises(InstanceLockError) as caught:
        InstanceLock(link).acquire()

    published = f"{caught.value}\n{caught.value.remedy}"
    assert "symbolic link" in published, (
        f"the refusal does not name the artefact at the path: {published!r}"
    )
    assert "named pipe" not in published, (
        f"the refusal describes the link's *target* instead of the link, so the cure "
        f"names an artefact the operator will not find at that path: {published!r}"
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_at_the_instance_lock_is_refused_rather_than_waited_on(
    tmp_path: Path,
) -> None:
    """RED before ``O_NONBLOCK``: the acquisition never returned.

    The bound is the child, not a signal. ``os.open`` on a named pipe with no
    reader blocks in the kernel, and a test that called ``acquire`` in-process
    without the flag would stall the run rather than fail it -- so the call is
    made in a subprocess this test kills, exactly as the state-database faults
    are driven.

    The refusal must also *name* the artefact: ``ENXIO``'s ``strerror`` is
    "Device not configured" on macOS, which sends the reader looking for
    hardware that is not involved.
    """
    path = tmp_path / "daemon.lock"
    os.mkfifo(path)
    script = (
        "import sys\n"
        "from theurian.daemon.instance import InstanceLock, InstanceLockError\n"
        "try:\n"
        "    InstanceLock(__import__('pathlib').Path(sys.argv[1])).acquire()\n"
        "except InstanceLockError as exc:\n"
        "    print(f'REFUSED {exc} || {exc.remedy}')\n"
        "else:\n"
        "    print('ACQUIRED')\n"
    )
    try:
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script, str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "taking the instance lock over a named pipe did not return within 30s and was "
            "killed: `theurian daemon start` blocks in the open with nothing to grade"
        )

    assert done.stdout.startswith("REFUSED"), done
    assert "a named pipe (FIFO)" in done.stdout, (
        f"the refusal does not say what is at the lock path, so the operator is left "
        f"with the errno: {done.stdout!r}"
    )
    assert irregular_artefact_remedy(path, "a named pipe (FIFO)") in done.stdout, (
        f"the refusal carries no cure, so a caller reading `exc.remedy or <default>` "
        f"publishes the generic doctor fallback -- which neither reports the artefact "
        f"nor removes it: {done.stdout!r}"
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_with_a_reader_at_the_instance_lock_is_refused_too(
    tmp_path: Path,
) -> None:
    """The face ``O_NONBLOCK`` alone does not close, on this lock path too (H-1).

    With a reader attached the open *succeeds*, so a guard that only bounds the
    open hands ``flock`` a descriptor that is not a file. ``acquire`` asks the
    descriptor what it got, which is why this is refused rather than reported as
    a filesystem that cannot lock.

    In-process, deliberately: the open cannot block here -- that is the whole
    difference from the case above -- so a child would buy nothing and hide the
    exception this asserts on.
    """
    path = tmp_path / "daemon.lock"
    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with pytest.raises(InstanceLockError) as caught:
            InstanceLock(path).acquire()
    finally:
        os.close(reader)

    assert "a named pipe (FIFO)" in str(caught.value), (
        f"the refusal does not name the artefact the open accepted: {caught.value}"
    )
    assert caught.value.remedy == irregular_artefact_remedy(path, "a named pipe (FIFO)"), (
        f"the refusal carries no cure of its own: {caught.value.remedy!r}"
    )
    # The message/remedy split `WriteLockUnusableError` records: `error` is the
    # field quoted into a bug report, `remedy` the one pasted into a shell, so the
    # operator's absolute path belongs in exactly one of them.
    assert str(tmp_path) not in str(caught.value), (
        f"the published message carries the absolute path, which belongs in the remedy "
        f"and nowhere else: {caught.value}"
    )
    assert str(path) in caught.value.remedy, (
        f"the remedy does not name the path to act on: {caught.value.remedy!r}"
    )


def _open_descriptors() -> int:
    """How many file descriptors this process holds.

    ``/dev/fd`` on both platforms this project builds on. Counted rather than
    listed: the identity of the descriptors is not the subject, only whether a
    refused acquisition gave its own back.
    """
    return sum(1 for _ in Path("/dev/fd").iterdir())


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
@pytest.mark.skipif(not Path("/dev/fd").is_dir(), reason="needs /dev/fd to count descriptors")
def test_a_refused_acquisition_gives_its_descriptor_back(tmp_path: Path) -> None:
    """The ``os.close`` on the refusal path, which nothing observed (round two, M-9).

    The descriptor arm refuses *after* the open has succeeded, so it is holding
    one. Deleting its ``os.close`` left every test green -- a leak is invisible in
    one call's result, and visible only in how many the process is holding, which
    is what this counts. It matters more here than in an ordinary leak: the
    descriptor is on a lock file, so a leaked one is a lock this process still
    holds with nothing left to release it.

    Repeated, because a single leak is inside the noise of an interpreter that
    opens and closes files for its own reasons; fifty is not.
    """
    path = tmp_path / "daemon.lock"
    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        before = _open_descriptors()
        for _ in range(50):
            with pytest.raises(InstanceLockError):
                InstanceLock(path).acquire()
        after = _open_descriptors()
    finally:
        os.close(reader)

    assert after <= before + 2, (
        f"fifty refused acquisitions left {after - before} descriptors behind; each one "
        f"is an open file description on a lock file that nothing will release"
    )


def test_the_breadcrumb_leaves_no_tail_from_a_longer_earlier_one(tmp_path: Path) -> None:
    """``acquire``'s ``truncate`` is behaviour, so it is driven rather than described.

    The open dropped ``O_TRUNC`` deliberately -- that flag is what let a symbolic
    link at this path destroy the file it named -- so the breadcrumb is truncated
    *after* the write instead, once ``O_NOFOLLOW`` has resolved the name to a
    regular file this process holds a lock on. Deleting the ``truncate`` left the
    whole suite green, because every other test reads the lock's *lock* and
    nothing reads its bytes.

    A longer breadcrumb is what makes the tail visible: a stale line from an
    earlier run leaves trailing bytes after the new one, so the file parses as
    JSON up to the newline and then carries something a reader would have to know
    to ignore.
    """
    path = tmp_path / "daemon.lock"
    path.write_text('{"pid": 999999999999999999999999}\nstale tail nobody meant to leave\n')

    lock = InstanceLock(path)
    assert lock.acquire()
    try:
        written = path.read_text()
    finally:
        lock.release()

    assert written.endswith("\n"), written
    assert json.loads(written) == {"pid": os.getpid()}, (
        f"the lock file carries more than this run's breadcrumb -- a tail from the "
        f"longer line that was there before: {written!r}"
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
def test_the_descriptor_and_the_path_disagree_after_the_open(tmp_path: Path) -> None:
    """The *reason* both locks ask ``fstat`` rather than the path (round two, M-10).

    Both the write lock's ``_open`` and :meth:`InstanceLock.acquire` say the
    question goes to the descriptor because a path can be re-pointed between the answer
    and the open, and swapping the production ``fstat`` back to a path probe
    survived the whole suite: every artefact those tests plant sits still, so the
    two probes agree on all of them and the reason was reasoning, not a measured
    difference.

    This is the input where they disagree. The pipe is opened, the *name* is then
    re-pointed at a regular file, and the two questions are put side by side:
    ``os.fstat`` still answers "a named pipe (FIFO)" about the object the open
    actually got, while ``irregular_shape_at`` -- looking the name up again --
    answers ``None`` and would wave the descriptor through to ``flock``.

    The swap is done deliberately rather than raced, because the property under
    test is *which object each question is about*, not how often a racer wins.
    """
    lock = tmp_path / "daemon.lock"
    os.mkfifo(lock)
    reader = os.open(lock, os.O_RDONLY | os.O_NONBLOCK)
    fileno = os.open(lock, LOCK_OPEN_FLAGS, 0o600)
    try:
        lock.unlink()
        lock.write_text("")

        from_the_descriptor = irregular_shape(os.fstat(fileno).st_mode)
        from_the_path = irregular_shape_at(lock)
    finally:
        os.close(fileno)
        os.close(reader)

    assert from_the_descriptor == "a named pipe (FIFO)", (
        f"`fstat` no longer reports what the open actually got: {from_the_descriptor!r}"
    )
    assert from_the_path is None, (
        f"the path probe was expected to answer about the *new* file at the name; it "
        f"answered {from_the_path!r}, so this input no longer separates the two questions"
    )
    assert from_the_descriptor != from_the_path, (
        "the descriptor and the path agree here, so nothing in this test distinguishes "
        "asking the kernel about the open object from looking the name up again"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_directory_at_the_instance_lock_is_named_by_the_operating_system(
    tmp_path: Path,
) -> None:
    """The OS account is the whole diagnostic where there is no shape (round two, M-5).

    ``irregular_shape`` excludes directories on purpose -- ``O_WRONLY`` answers
    ``EISDIR`` before a byte moves, and that errno names the fault exactly -- so
    for this artefact the ``strerror`` clause is the *only* thing in the message
    that distinguishes it from any other refusal. Deleting that clause left every
    test green, which is why it is asserted here rather than assumed.
    """
    path = tmp_path / "daemon.lock"
    path.mkdir()

    with pytest.raises(InstanceLockError) as caught:
        InstanceLock(path).acquire()

    assert "Is a directory" in str(caught.value), (
        f"the refusal drops the operating system's own account, which for a directory "
        f"is the only thing that says what went wrong: {caught.value}"
    )
    assert caught.value.remedy, (
        f"a refusal with no cure falls back to the generic one: {caught.value}"
    )


# -- GATE-1: the lock directory's own refusal reaches `--json` ----------------
#
# `acquire`'s `mkdir` raised a bare `OSError` past every handler in the daemon's
# start path -- which is the `ExecStart` of the shipped launchd and systemd
# units. Measured 2026-09-06 against the real CLI in a sandboxed HOME and
# THEURIAN_DATA_DIR, `daemon start --foreground --json`:
#
#   - THEURIAN_DATA_DIR replaced by a regular file -> FileExistsError traceback,
#     exit 1, **zero bytes on stdout and zero on stderr**;
#   - its parent at mode 0500 -> PermissionError, the same.
#
# A supervised daemon crash-loops on that with nothing structured to read. The
# rule it now follows is the write lock's `_prepare_the_directory`, one file over: an
# acquisition has no step left that raises a bare `OSError`.

_DAEMON_START_CHILD: Final = (
    "import json, sys\n"
    "from typer.testing import CliRunner\n"
    "from theurian.cli.main import app\n"
    "r = CliRunner().invoke(app, sys.argv[1:])\n"
    "escaped = None if isinstance(r.exception, SystemExit) else r.exception\n"
    "print(json.dumps({'code': r.exit_code, 'out': r.stdout, 'err': r.stderr,\n"
    "                  'raised': type(escaped).__name__ if escaped is not None else None}))\n"
)


def _daemon_start_over(data_dir: Path, home: Path, cwd: Path) -> dict[str, object]:
    """``daemon start --foreground --json`` in a child, with everything redirected.

    A child rather than ``runner.invoke``: this command *serves* when the lock is
    takeable, and the whole point of these fixtures is that it must not get that
    far. The timeout is the backstop -- if a future change lets the acquisition
    through, the child is killed and the test fails instead of the run hanging on
    a daemon nobody asked for.

    ``--port 7420`` for the reason ``docs/contributing/development.md`` records:
    a dev-time daemon never touches 7419, where a resident one may be listening.
    """
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "THEURIAN_DATA_DIR": str(data_dir),
            "UV_TOOL_DIR": str(home / "uv-tool"),
            "UV_CACHE_DIR": str(home / "uv-cache"),
        }
    )
    try:
        done = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                _DAEMON_START_CHILD,
                "daemon",
                "start",
                "--foreground",
                "--json",
                "--port",
                "7420",
            ],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "`theurian daemon start --foreground` did not return within 60s over an "
            "unusable data directory: the acquisition got past the refusal and may be "
            "serving"
        )
    lines = done.stdout.strip().splitlines()
    assert lines, f"the child printed no report; rc={done.returncode} stderr={done.stderr!r}"
    report: dict[str, object] = json.loads(lines[-1])
    return report


@pytest.mark.parametrize("fault", ["a regular file", "an unwritable parent"])
def test_a_data_directory_that_cannot_hold_the_lock_is_refused_as_a_document(
    tmp_path: Path, fault: str
) -> None:
    """GATE-1. RED before the ``mkdir`` was wrapped: nothing reached either channel.

    The assertion is the reporting contract, not the wording: a ``--json`` caller
    receives one ``{error, remedy}`` document on stderr and an empty stdout,
    whatever is wrong with the directory. Before the wrap it received *nothing* on
    either channel and an uncaught ``OSError`` -- the CP-2 shape, on the command a
    service manager runs.

    **This is the one instance-lock test that reads the ``--json`` shape.** The
    three above stop at the library boundary, which is where round one left them:
    they prove ``acquire`` refuses, and none of them proves the refusal survives
    the trip to a caller.

    The cure must name the directory to act on. The generic fallback a caller
    reading ``exc.remedy or <default>`` would publish sends the operator to
    ``theurian doctor``, which reports nothing about a data directory it cannot
    create.
    """
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    if fault == "a regular file":
        data_dir.write_text("not a directory\n")
    else:
        if _CANNOT_BE_REFUSED_BY_A_MODE:
            pytest.skip("POSIX permission bits, and not as root")
        tmp_path.chmod(0o500)

    try:
        report = _daemon_start_over(data_dir, home, project)
    finally:
        tmp_path.chmod(0o700)

    assert report["raised"] is None, (
        f"the refusal escaped `--json` as a {report['raised']} rather than a document, "
        f"so a service manager restarting this daemon has nothing to read"
    )
    assert report["out"] == "", f"stdout stays a clean machine channel: {report['out']!r}"
    payload = json.loads(str(report["err"]))
    assert payload.get("error"), payload
    remedy = str(payload.get("remedy", ""))
    assert str(data_dir) in remedy, (
        f"the cure does not name the directory that could not be prepared: {remedy!r}"
    )
    assert "THEURIAN_DATA_DIR" in remedy, (
        f"the cure names no way to point Theurian somewhere else, which is the other "
        f"half of the fix for a data directory the operator got wrong: {remedy!r}"
    )


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


def test_serve_composes_the_grant_from_the_declared_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve` reads the operator's serving profile and threads its grant into the server.

    The composition is one line -- `StaticAuthorizationProvider(load_serving_profile(
    resolved)).deployment_grant()` -- and `daemon start` grabs a real port, so no
    integration test reaches it. Dropping the `load_serving_profile` read there, so
    the daemon serves the built-in `DEFAULT_CEILING` whatever the operator declared,
    left the whole suite green. This exercises the seam in-process, the way the MCP
    tests build a server without starting a daemon: `uvicorn.run`, `build_app` and
    `build_server` are stubbed so nothing binds or serves, and the grant `serve`
    hands to `build_server` is captured and checked against the profile on disk.

    Paired with the build side. `build_server` is held to *using* the grant it is
    given by the #119 tests in `test_mcp_tools.py`; this holds `serve` to *reading*
    the right one, so between them both ends of the seam read the same source. A
    `restricted` profile expands to every level, which the built-in default
    (`internal`) does not -- so a mutation that ignored the file would capture a
    strictly narrower set and redden here.
    """
    # Declared the way an operator does: 0600 file in a 0700 `auth/` directory,
    # the two modes `load_serving_profile` requires before it will honour the file.
    profile = serving_profile_path(tmp_path)
    profile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile.write_text("restricted\n")
    profile.chmod(0o600)

    captured: list[AuthorizationGrant] = []

    def _capture_grant(registry: ProjectRegistry, grant: AuthorizationGrant) -> object:
        captured.append(grant)
        return object()

    monkeypatch.setattr("theurian.daemon.runner.build_server", _capture_grant)
    monkeypatch.setattr("theurian.daemon.runner.build_app", lambda _config, _server: object())
    monkeypatch.setattr("theurian.daemon.runner.uvicorn.run", lambda *_args, **_kwargs: None)

    serve(tmp_path, port=_free_port())

    assert len(captured) == 1, "serve must compose one grant and hand it to build_server"
    assert captured[0].sensitivities == frozenset(Sensitivity), (
        "serve served the built-in default instead of the declared `restricted` ceiling: "
        f"{sorted(level.value for level in captured[0].sensitivities)}"
    )


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


# -- The daemon CLI --------------------------------------------------------
#
# The SessionStart hook branches on this output. It is a contract with a shell
# script that greps for specific keys, so the shape is not an implementation
# detail.


def test_status_distinguishes_a_missing_service_from_a_stopped_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SessionStart hook branches on exactly this.

    `installed-stopped` means a user-approved service may be resumed;
    `not-installed` means send the user to `/theurian:setup` and install nothing
    (FR-L3). Conflating them makes the hook either install without consent or
    refuse to start something the user already approved.
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))  # no service is registered here
    result = CliRunner().invoke(app, ["daemon", "status", "--port", str(_free_port()), "--json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["state"] in {"not-installed", "unknown"}
    assert payload["listening"] is False


def test_status_reports_a_running_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path))

    with _fake_daemon(str(tmp_path)) as port:
        result = CliRunner().invoke(app, ["daemon", "status", "--port", str(port), "--json"])

    payload = json.loads(result.stdout)
    assert payload["state"] == "running"
    assert payload["listening"] is True
    assert payload["version"] == "9.9.9"
    assert payload["endpoint"] == f"http://127.0.0.1:{port}/mcp"


def test_status_states_come_from_the_service_state_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook and the DaemonManager must agree on the words."""
    from theurian.domain.ports.daemon_manager import ServiceState

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["daemon", "status", "--port", str(_free_port()), "--json"])

    assert json.loads(result.stdout)["state"] in {s.value for s in ServiceState}


def test_starting_an_unregistered_service_is_refused_not_improvised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-L3. The SessionStart hook calls this to resume a service the user
    already approved. It must never become the thing that installs one, so
    "nothing is registered" is a refusal that names setup rather than an
    invitation to guess.
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["daemon", "start", "--port", str(_free_port()), "--json"])
    message = (result.stderr or result.stdout).lower()

    assert result.exit_code != 0
    assert "nothing to start" in message or "no user-scoped service manager" in message
    assert "setup" in message or "foreground" in message


def test_a_detached_start_never_daemonises_theurian_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """launchd and systemd already do supervision, restart-on-failure, and log
    redirection. A hand-rolled double-fork would be a second, worse
    implementation of all three -- so no daemon is left behind here.
    """
    port = _free_port()
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    CliRunner().invoke(app, ["daemon", "start", "--port", str(port), "--json"])

    assert port_is_free(port=port), "a refused start must leave nothing listening"


def test_stopping_without_a_registered_service_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately not a PID-based kill. This design uses an advisory lock
    rather than a PID file because PIDs are recycled, so a stale one can name a
    live unrelated process -- and signalling that is exactly the damage a
    convenience command must not be able to do (ADR-0002).
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    result = CliRunner().invoke(app, ["daemon", "stop", "--json"])
    message = (result.stderr or result.stdout).lower()

    assert result.exit_code != 0
    assert "nothing to stop" in message or "no user-scoped service manager" in message
    assert "ctrl-c" in message
