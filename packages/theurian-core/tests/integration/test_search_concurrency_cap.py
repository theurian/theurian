"""A concurrency cap on the daemon retrieval path (issue #26, SEC-8, T-6).

Sync MCP tools run through ``anyio.to_thread.run_sync``, and cancelling the
*awaiting* task does not stop the worker thread it dispatched to -- so a
transport-level wall-clock timeout cannot bound how much CPU or GIL time a
flood of concurrent ``knowledge.search`` calls spends. T-6 answers this with
admission control instead: a bounded semaphore around the answer block
(``hybrid_answer`` through ``substring_answer``), refusing the caller that
arrives once the cap is already full rather than letting it queue behind
however much work is already running.

These tests are driving tests written before the implementation lands
(``MAX_CONCURRENT_SEARCHES`` and ``ADMISSION_WAIT_SECONDS`` do not exist yet in
``theurian.mcp.tools``). Every test in this file is expected to fail at
collection with an ``ImportError`` until they do; that failure -- not a test
assertion -- is this file's RED state for now.

Self-contained rather than importing fixtures from ``test_mcp_tools.py``:
``--import-mode=importlib`` does not add ``tests/integration/`` to
``sys.path`` (see that directory's own absence of a ``conftest.py``), so a
bare ``from test_mcp_tools import registry`` only works by accident of
collection order. AC-3 also needs two or three *independently rooted* stores
compared in one test, which pytest's fixture caching would defeat if it were
reusing one shared fixture instance for all of them.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Final, NoReturn

import httpx2 as httpx
import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.daemon.server import DaemonConfig, build_app
from theurian.mcp import tools
from theurian.mcp.tools import ADMISSION_WAIT_SECONDS, MAX_CONCURRENT_SEARCHES
from theurian.security.tokens import generate_token

pytestmark = pytest.mark.integration

_runner = CliRunner()

#: How long any single bounded wait in this file may run before it is treated
#: as a broken harness rather than a slow one. Every wait here is either a
#: ``threading.Event`` with a ``timeout=`` or an ``asyncio.wait_for`` -- never
#: an unbounded poll -- so a leaked blocked worker fails the test instead of
#: hanging the suite.
_WAIT_BOUND_SECONDS: Final = 5.0

# -- A minimal registered project (self-contained migration fixtures) ------

_AUTH_MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
_AUTH_REVISION_ID = "01K1AAAREV01234567890ABCDE"
_AUTH_BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"


def _auth_migration(body: str) -> str:
    return f"""apiVersion: theurian.dev/v1
id: {_AUTH_MIGRATION_ID}
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
    revisionId: {_AUTH_REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(body)}
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
          sourceUri: git://demo/auth-policy.md
"""


#: A second, independent migration adding three withheld rows on top of the
#: one approved item -- copied verbatim (ids, bodies, statuses) from
#: ``test_mcp_tools.py``'s own ``RETIRED_MIGRATION``, which is the proven
#: shape for "deprecated" (via ``deprecateItem``), "superseded" and "rejected"
#: (via revision metadata) all in one migration.
_RETIRED_MIGRATION_ID = "01K1DDDDDD01234567890ABCDE"
_RETIRED_BODY = "# Retired\n\nContent nobody outside the team may be told exists.\n"

_RETIRED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_RETIRED_MIGRATION_ID}
createdAt: 2026-08-02T16:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.retired-gateway
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.retired-gateway
    revisionId: 01K1DDDGWA01234567890ABCDE
    contentFile: ../knowledge/architecture/retired-gateway.md
    contentSha256: {body_pin(_RETIRED_BODY)}
    metadata:
      title: Retired gateway
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/retired-gateway.md
  - op: deprecateItem
    itemId: architecture.retired-gateway
    reason: replaced by the edge proxy
  - op: createItem
    itemId: architecture.superseded-sessions
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.superseded-sessions
    revisionId: 01K1DDDSSN01234567890ABCDE
    contentFile: ../knowledge/architecture/superseded-sessions.md
    contentSha256: {body_pin(_RETIRED_BODY)}
    metadata:
      title: Superseded sessions
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: superseded
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/superseded-sessions.md
  - op: createItem
    itemId: architecture.rejected-store
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.rejected-store
    revisionId: 01K1DDDRST01234567890ABCDE
    contentFile: ../knowledge/architecture/rejected-store.md
    contentSha256: {body_pin(_RETIRED_BODY)}
    metadata:
      title: Rejected store
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/rejected-store.md
"""


def _run(*args: str) -> None:
    result = _runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _build_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    with_retired_rows: bool = False,
) -> ProjectRegistry:
    """An independently rooted, registered project: one approved item, and
    optionally three withheld ones on top.

    Takes its own ``name`` for both the root directory and (via `theurian
    project register`'s directory-name default) the project id, so two calls
    in the same test with different names produce two genuinely separate
    stores under two separate data directories -- what AC-3 needs to compare
    a corpus with withheld rows against one with none.
    """
    root = tmp_path / name
    root.mkdir()
    for git_args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(git_args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / f"{name}-data"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(_AUTH_BODY)
    (root / f".theurian/migrations/{_AUTH_MIGRATION_ID}-auth.yaml").write_text(
        _auth_migration(_AUTH_BODY)
    )
    _run("project", "register")
    _run("migrate", "apply")

    if with_retired_rows:
        for slug in ("retired-gateway", "superseded-sessions", "rejected-store"):
            (knowledge / f"{slug}.md").write_text(_RETIRED_BODY)
        (root / f".theurian/migrations/{_RETIRED_MIGRATION_ID}-retire.yaml").write_text(
            _RETIRED_MIGRATION
        )
        _run("migrate", "apply")

    return ProjectRegistry.default(data_dir)


def _build_two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """Two projects, ``alpha`` and ``beta``, registered under one data dir.

    One daemon serving many projects is the design (ADR-0002), and AC-3(b)
    needs exactly that shape: one server, one shared semaphore, two different
    ``projectId``s refused by the same saturated gate.
    """
    data_dir = tmp_path / "two-projects-data"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))

    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir()
        for git_args in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(git_args, cwd=root, check=True, capture_output=True)  # noqa: S603

        monkeypatch.chdir(root)
        _run("init")
        (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(_AUTH_BODY)
        (root / f".theurian/migrations/{_AUTH_MIGRATION_ID}-auth.yaml").write_text(
            _auth_migration(_AUTH_BODY)
        )
        _run("project", "register")
        _run("migrate", "apply")

    return ProjectRegistry.default(data_dir)


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """This file's baseline corpus: one registered project, one approved item."""
    return _build_project(tmp_path, monkeypatch, name="demo")


# -- Calling a tool through the same entry point the transport uses --------


async def _call_on(server: MCPServer, tool: str, **arguments: Any) -> dict[str, Any]:
    """Invoke a tool and return its payload, mirroring ``test_mcp_tools._call_on``."""
    result = await server.call_tool(tool, arguments)
    content: Any = result.content  # type: ignore[union-attr]
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    import json

    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


async def _call_failing_on(server: MCPServer, tool: str, **arguments: Any) -> str:
    """The message a client would see when ``tool`` fails.

    ``call_tool`` re-raises a failing tool as the SDK's own ``ToolError``
    (``mcp.server.mcpserver.tools.base.Tool.run``, which wraps *any* exception
    the tool body raises as ``f"Error executing tool {name}: {e}"``). That
    wrapper is what AC-3 relies on: the tool name is a per-registration
    constant and the wrapped text is whatever ``theurian.mcp.tools.ToolError``
    was raised with, so a constant inner message stays constant all the way to
    this string.
    """
    with pytest.raises(SdkToolError) as raised:
        await server.call_tool(tool, arguments)
    return str(raised.value)


# -- Saturating the answer block deterministically --------------------------


class _SaturationGate:
    """Stands in for ``hybrid_answer``, holding worker threads open on cue.

    ``knowledge_search`` looks up ``hybrid_answer`` by name in
    ``theurian.mcp.tools``'s module globals at call time (a plain
    ``LOAD_GLOBAL``, resolved fresh on every call), so
    ``monkeypatch.setattr(tools, "hybrid_answer", gate.stub)`` redirects every
    future call regardless of when the server was built.

    Every wait here is bounded (``_WAIT_BOUND_SECONDS``) and ``release`` is
    always set from a ``try``/``finally`` in the tests that use this gate: a
    stub left blocked on ``self.release.wait()`` forever is a worker thread
    the whole suite cannot finish, and ``anyio.to_thread.run_sync`` gives it no
    way to be cancelled from the outside.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entered_count = 0
        self._expected = 0
        self.all_entered = threading.Event()
        self.release = threading.Event()

    def expect(self, count: int) -> None:
        """Reset the gate for a fresh saturation round admitting ``count`` callers."""
        with self._lock:
            self._expected = count
            self._entered_count = 0
        self.all_entered.clear()
        self.release.clear()

    @property
    def entered_count(self) -> int:
        """How many calls have entered :meth:`stub` so far, across every round."""
        with self._lock:
            return self._entered_count

    def stub(self, *_args: object, **kwargs: object) -> dict[str, Any]:
        """Replaces ``hybrid_answer``: signal entry, then block for ``release``.

        Runs on the worker thread ``anyio.to_thread.run_sync`` dispatched, not
        on the asyncio loop -- which is exactly the mechanism fact this cap
        exists to work around (a blocked worker thread here does not block
        anything else the event loop is serving, ``/health`` included).
        """
        with self._lock:
            self._entered_count += 1
            if self._entered_count >= self._expected:
                self.all_entered.set()
        released = self.release.wait(timeout=_WAIT_BOUND_SECONDS)
        assert released, (
            "the saturation gate's release was never set within "
            f"{_WAIT_BOUND_SECONDS}s -- a leaked blocked worker would hang the whole "
            "suite, so this fails loudly instead"
        )
        return {"query": kwargs.get("query", ""), "count": 0, "results": []}


def _raising_stub(*_args: object, **_kwargs: object) -> NoReturn:
    """Replaces ``hybrid_answer`` with an immediate, unrelated failure.

    Used by AC-4(b): the semaphore must release on *this* exit path too, not
    only on a clean return.
    """
    msg = (
        "synthetic failure inside the answer block (AC-4b) -- the semaphore "
        "must still release on this exit path"
    )
    raise RuntimeError(msg)


async def _saturate(
    server: MCPServer, gate: _SaturationGate, *, project_id: str, count: int
) -> list[asyncio.Task[dict[str, Any]]]:
    """Launch ``count`` concurrent ``knowledge.search`` calls and block until
    every one has *entered* the stub -- not merely been scheduled.

    The wait for ``all_entered`` runs on a different thread
    (``run_in_executor``) than the event loop dispatching the ``count`` tasks:
    a raw blocking call on the loop's own thread would prevent those tasks
    from ever reaching ``anyio.to_thread.run_sync`` in the first place, which
    is a deadlock, not a saturation.
    """
    gate.expect(count)
    tasks = [
        asyncio.create_task(
            _call_on(server, "knowledge.search", projectId=project_id, query=f"holder query {i}")
        )
        for i in range(count)
    ]
    entered = await asyncio.get_running_loop().run_in_executor(
        None, gate.all_entered.wait, _WAIT_BOUND_SECONDS
    )
    assert entered, (
        f"only {gate.entered_count}/{count} holder calls entered the stub within "
        f"{_WAIT_BOUND_SECONDS}s -- the harness failed to saturate, which would also "
        "be exactly what a leaked permit from an earlier round looks like"
    )
    return tasks


async def _drain(gate: _SaturationGate, tasks: list[asyncio.Task[dict[str, Any]]]) -> list[Any]:
    """Release the gate and collect every holder task's outcome.

    ``return_exceptions=True`` so a holder's failure is reported by the
    caller's own assertion rather than masking whatever assertion is already
    in flight when this runs from a ``finally``.
    """
    gate.release.set()
    return await asyncio.wait_for(
        asyncio.gather(*tasks, return_exceptions=True), timeout=_WAIT_BOUND_SECONDS
    )


def _assert_all_succeeded(drained: list[Any], expected_count: int) -> None:
    assert len(drained) == expected_count
    failures = [item for item in drained if isinstance(item, BaseException)]
    assert not failures, f"every holder call must succeed once released: {failures}"


async def _refused(server: MCPServer, *, project_id: str, query: str) -> str:
    """The message an excess caller sees once the admission wait elapses."""
    return await asyncio.wait_for(
        _call_failing_on(server, "knowledge.search", projectId=project_id, query=query),
        timeout=ADMISSION_WAIT_SECONDS + _WAIT_BOUND_SECONDS,
    )


# -- AC-1: the excess caller is refused, and does no retrieval work ---------


@pytest.mark.asyncio
async def test_the_cap_refuses_the_excess_caller(
    registry: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-1 / SEC-8, T-6.

    A flood of concurrent searches must not be allowed to spend unbounded
    daemon CPU: `anyio` cannot cancel a worker thread already dispatched, so
    the only place to stop the (N+1)th caller is before it starts any
    retrieval work at all. This is the test that would have caught a cap that
    let the excess caller queue and run anyway, which does not fail on
    result *correctness* but on cost -- the observable family this whole
    slice exists to close (see brief-26, "a resource the query consumes").
    """
    server = build_server(registry)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)

    tasks = await _saturate(server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES)
    try:
        message = await _refused(server, project_id="demo", query="the excess caller's query")
        assert message, "a refusal must carry a message, not an empty string"
        assert gate.entered_count == MAX_CONCURRENT_SEARCHES, (
            "the excess caller must not have entered the answer block at all -- "
            "the gate must refuse before any retrieval work runs, not after"
        )
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)


# -- AC-2: nothing changes for a caller who never meets the cap -------------


@pytest.mark.asyncio
async def test_a_normal_search_is_unaffected_by_the_cap(registry: ProjectRegistry) -> None:
    """AC-2. With nothing else in flight, the admission gate must be invisible.

    Reuses the exact assertion ``test_mcp_tools.test_search_finds_an_approved_item``
    makes: a capped call with only one caller in flight must answer exactly as
    an uncapped one always has, or this slice changed ordinary behaviour and
    not only behaviour under load. Deliberately does not monkeypatch
    ``hybrid_answer`` -- the real answer path has to run for this to mean
    anything.
    """
    result = await _call_on(
        build_server(registry), "knowledge.search", projectId="demo", query="signed token"
    )

    assert result["count"] == 1
    assert result["results"][0]["itemId"] == "architecture.auth-policy"


# -- AC-3: the refusal never varies with query, projectId, or store content -


@pytest.mark.asyncio
async def test_the_refusal_is_byte_identical_whatever_the_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3 / SEC-13. The whole reason a load-triggered cap was chosen over a
    cost-triggered timeout: a refusal that varied with the query, the
    project, or whether the store holds withheld rows would itself be a
    disclosure channel (the observable family "an error that fires for one
    input and not another"). This test is the disclosure closure for that
    channel -- it must fail if the refusal message is built from anything
    but a fixed string.

    Three independent saturation rounds, each on its own server (and so its
    own semaphore), covering:
      (a) two different queries against the same store,
      (b) two different projectIds against one server serving both, and
      (c) a corpus with three withheld rows against one with none.
    All five captured refusal strings must be the same string.
    """
    plain = _build_project(tmp_path, monkeypatch, name="plain")
    two_projects = _build_two_projects(tmp_path, monkeypatch)
    retired = _build_project(tmp_path, monkeypatch, name="retired", with_retired_rows=True)

    messages: list[str] = []

    # (a) two different queries, one store, one saturation round.
    plain_server = build_server(plain)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(plain_server, gate, project_id="plain", count=MAX_CONCURRENT_SEARCHES)
    try:
        messages.append(await _refused(plain_server, project_id="plain", query="a short query"))
        messages.append(
            await _refused(
                plain_server,
                project_id="plain",
                query="an entirely different, much longer query about something else",
            )
        )
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    # (b) two different projectIds, one server, one saturation round.
    two_server = build_server(two_projects)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(two_server, gate, project_id="alpha", count=MAX_CONCURRENT_SEARCHES)
    try:
        messages.append(await _refused(two_server, project_id="alpha", query="shared query"))
        messages.append(await _refused(two_server, project_id="beta", query="shared query"))
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    # (c) a corpus with withheld rows against one with none.
    retired_server = build_server(retired)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(
        retired_server, gate, project_id="retired", count=MAX_CONCURRENT_SEARCHES
    )
    try:
        messages.append(await _refused(retired_server, project_id="retired", query="a short query"))
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    assert len(messages) == 5
    assert len({*messages}) == 1, (
        "the refusal must be byte-identical whatever the input; observed distinct "
        f"strings: {sorted(set(messages))}"
    )


# -- AC-4: capacity is restored on every exit path ---------------------------


@pytest.mark.asyncio
async def test_capacity_is_restored_on_every_exit_path(
    registry: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-4. If a call exits the gated block by any path -- success, the
    admission refusal itself, or an unrelated exception -- the system shall
    NOT leak a permit.

    This is written so that deleting the ``finally: sem.release()`` in the
    implementation goes RED: with the release removed, the second half of
    this test (N exception-exits followed by an attempt to saturate N
    concurrent callers) can no longer admit all N, and ``_saturate``'s bounded
    wait for every caller to enter times out.
    """
    server = build_server(registry)

    # (a) success + refusal, then release: a fresh call afterward must not
    # be refused, proving neither the successful holders nor the refusal
    # itself left the semaphore short a permit.
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES)
    try:
        await _refused(server, project_id="demo", query="the excess caller's query")
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    fresh = await _call_on(server, "knowledge.search", projectId="demo", query="post-release check")
    assert fresh["count"] == 0, "a call right after release must succeed, not be refused"

    # (b) N exception-exits, then N concurrent callers must all be admitted.
    monkeypatch.setattr(tools, "hybrid_answer", _raising_stub)
    for _ in range(MAX_CONCURRENT_SEARCHES):
        message = await asyncio.wait_for(
            _call_failing_on(server, "knowledge.search", projectId="demo", query="boom"),
            timeout=_WAIT_BOUND_SECONDS,
        )
        assert "Error executing tool knowledge.search" in message

    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES)
    drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)


# -- AC-5: /health stays live while the cap is saturated ---------------------


@pytest.mark.asyncio
async def test_health_answers_promptly_while_the_cap_is_saturated(
    registry: ProjectRegistry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-5. ``/health`` is what the SessionStart hook and the single-instance
    probe call, and it must answer even while every search worker thread is
    blocked: it is served directly as an ``async def`` route on the asyncio
    event loop, never through ``anyio.to_thread.run_sync``, so it shares
    nothing with the pool the cap is bounding (the mechanism fact brief-26
    records as the whole reason a cap was chosen over a wall-clock timeout).

    Skips the ``with TestClient(app) as client:`` lifespan dance
    ``test_daemon.py`` uses for the ``/mcp`` mount: ``/health`` reads only the
    ``DaemonConfig`` closure, never ``mcp.session_manager``, so the one thing
    the lifespan starts plays no part in the route this test drives -- and a
    plain ``httpx2.ASGITransport`` request confirmed that against the
    *current*, un-gated tool during this test's own construction.
    """
    server = build_server(registry)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    # /health is unauthenticated (see build_app), so this token is never checked --
    # generated rather than a literal string so nothing here reads as a secret.
    config = DaemonConfig(token=generate_token(), data_dir=tmp_path)
    starlette_app = build_app(config, server)

    tasks = await _saturate(server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES)
    try:
        transport = httpx.ASGITransport(app=starlette_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:7419"
        ) as client:
            started = time.monotonic()
            response = await asyncio.wait_for(client.get("/health"), timeout=_WAIT_BOUND_SECONDS)
            elapsed = time.monotonic() - started
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert elapsed < 0.5, (
        f"/health took {elapsed:.3f}s while every search worker thread was blocked -- "
        "it must be served off the same asyncio loop those blocked threads never touch, "
        "not queued behind them"
    )
