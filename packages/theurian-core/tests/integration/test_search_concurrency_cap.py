"""A concurrency cap on the daemon retrieval path (issue #26, SEC-8, T-6).

Sync MCP tools run through ``anyio.to_thread.run_sync``, and cancelling the
*awaiting* task does not stop the worker thread it dispatched to -- so a
transport-level wall-clock timeout cannot bound how much CPU or GIL time a
flood of concurrent ``knowledge.search`` calls spends. T-6 answers this with
admission control instead: a bounded semaphore around the answer block
(``hybrid_answer`` through ``substring_answer``), refusing the caller that
arrives once the cap is already full rather than letting it queue behind
however much work is already running.

These were written as driving tests against a scratch implementation, before
``MAX_CONCURRENT_SEARCHES`` and ``ADMISSION_WAIT_SECONDS`` existed in
``theurian.mcp.tools`` -- every test in this file failed at collection with an
``ImportError`` until they did. Both constants and this file landed together
with the cap itself in ``a8c1ce3``.

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

import anyio.to_thread
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
from theurian.mcp.tools import (
    ADMISSION_WAIT_SECONDS,
    MAX_CONCURRENT_SEARCHES,
    SEARCH_CAPACITY_REFUSAL,
)
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
        """How many calls have entered :meth:`stub` so far in the current round.

        Per-round, not cumulative: :meth:`expect` resets this to 0 every time it
        is called.
        """
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


async def _refused(server: MCPServer, *, project_id: str, query: str, **kwargs: Any) -> str:
    """The message an excess caller sees once the admission wait elapses.

    ``**kwargs`` forwards any other `knowledge.search` parameter (`limit`,
    `maxTokens`, `useDense`, `includeUnapproved`, ...) so AC-3 can capture a
    refusal at a non-default value on each of those axes too.
    """
    return await asyncio.wait_for(
        _call_failing_on(server, "knowledge.search", projectId=project_id, query=query, **kwargs),
        timeout=ADMISSION_WAIT_SECONDS + _WAIT_BOUND_SECONDS,
    )


# -- Literal pins: the cap's recorded constants (adversarial M-1) -----------


def test_the_cap_pins_its_recorded_constants(registry: ProjectRegistry) -> None:
    """MEDIUM (adversarial M-1).

    T-6 records ``MAX_CONCURRENT_SEARCHES=4``, ``ADMISSION_WAIT_SECONDS=1.0``
    and a *bounded* semaphore as recorded defaults, not tunings -- see those
    constants' own docstrings in ``tools.py``. Every other test in this file
    is parameterised by these symbols, which pins their *use* but not their
    *value*: round 1 of the adversarial review changed the cap to 5 or 20,
    swapped the bounded semaphore for a plain one, and emptied the refusal
    string, and the whole suite stayed green under each (``cap-five``,
    ``cap-twenty``, ``plain-semaphore``, ``refusal-empty``). This test pins
    the literal recorded values directly instead of only through their use.
    """
    assert MAX_CONCURRENT_SEARCHES == 4
    assert ADMISSION_WAIT_SECONDS == 1.0

    # The semaphore itself is a local inside `register`'s closure, not a
    # module attribute -- reached here through the registered tool function's
    # own closure cell. `Tool.fn` (`mcp.server.mcpserver.tools.base.Tool.
    # from_function`) stores the exact function object `register` built, not
    # a wrapper, so `knowledge_search`'s free variable `search_admission`
    # still names the same semaphore `register` constructed it around.
    server = build_server(registry)
    tool = server._tool_manager.get_tool("knowledge.search")
    assert tool is not None, "knowledge.search must be registered"
    fn = tool.fn
    index = fn.__code__.co_freevars.index("search_admission")
    semaphore = fn.__closure__[index].cell_contents  # type: ignore[index]
    assert isinstance(semaphore, threading.BoundedSemaphore), (
        "an unbounded threading.Semaphore here would let a bug that "
        "over-releases inflate the cap silently instead of raising -- see "
        "the `finally: search_admission.release()` in knowledge_search, the "
        "one call site that could ever over-release it (AC-4)"
    )

    # The refusal content itself, read against hardcoded literal text rather
    # than derived from `SEARCH_CAPACITY_REFUSAL` -- comparing the constant
    # against itself would hold even if the constant were mutated to
    # something else entirely (`refusal-empty`).
    assert "maximum number of concurrent searches (4)" in SEARCH_CAPACITY_REFUSAL
    assert (
        "This refusal message is a constant: it carries nothing from your "
        "request or from any project's contents."
    ) in SEARCH_CAPACITY_REFUSAL


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
    result *correctness* but on cost -- "a resource the query consumes", the
    observable family threat-model T-6 enumerates and this slice closes.
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


# -- The admission wait actually admits, given a freed permit ---------------


@pytest.mark.asyncio
async def test_the_admission_wait_admits_a_caller_who_arrives_just_before_a_slot_frees(
    registry: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM (code M-2 / adversarial ``wait-zero``).

    ``ADMISSION_WAIT_SECONDS`` exists precisely so a caller who merely
    overlaps a slow search is admitted once a permit frees, rather than
    refused outright -- see that constant's own docstring. Nothing in this
    file held that claim: setting it to ``0.0`` left the whole suite green
    (round 1, mutation ``wait-zero``), because every other test here only
    ever exercises the *refused* branch.

    Saturates the cap, then frees the gate -- every currently blocked holder
    returns at once -- after a delay comfortably shorter than the wait, and
    asserts the excess caller, parked in the admission wait the whole time,
    is admitted rather than refused. With ``ADMISSION_WAIT_SECONDS`` at 0.0,
    ``search_admission.acquire(timeout=0.0)`` never blocks at all, so the
    excess caller is refused before the 0.2s release ever happens -- which is
    exactly the shape of the mutation this test exists to catch.
    """
    server = build_server(registry)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)

    tasks = await _saturate(server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES)
    try:
        excess = asyncio.create_task(
            _call_on(
                server, "knowledge.search", projectId="demo", query="the excess caller's query"
            )
        )
        # Comfortably shorter than ADMISSION_WAIT_SECONDS (1.0s): long enough
        # that the excess caller has certainly reached the semaphore wait
        # (dispatch to a worker thread is far faster than this), short enough
        # that the admission wait has certainly not yet elapsed.
        await asyncio.sleep(0.2)
        gate.release.set()
        result = await asyncio.wait_for(
            excess, timeout=ADMISSION_WAIT_SECONDS + _WAIT_BOUND_SECONDS
        )
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    assert result["count"] == 0, "the excess caller must be admitted once a permit frees"


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


# -- AC-3: the refusal *message* never varies with query, projectId, or store content -


@pytest.mark.asyncio
async def test_the_refusal_is_byte_identical_whatever_the_input(  # noqa: PLR0915 -- 4 rounds, 13
    # captures compared in one assertion; splitting would defeat that comparison (see docstring)
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3 / SEC-13. The whole reason a load-triggered cap was chosen over a
    cost-triggered timeout: a refusal that varied with the query, the
    project, whether the store holds withheld rows, or any of the tool's
    other parameters would itself be a disclosure channel (the observable
    family "an error that fires for one input and not another"). This test is
    the disclosure closure for that channel -- it must fail if the refusal
    message is built from anything but a fixed string.

    Four independent saturation rounds, each on its own server (and so its
    own semaphore), covering:
      (a) two different queries against the same store,
      (b) two different projectIds against one server serving both,
      (c) a corpus with three withheld rows against one with none, AND that
          same withheld-row corpus crossed with each of the four
          non-default-valued parameters (d) exercises: `includeUnapproved=
          True`, `limit=50`, `maxTokens=500`, and `useDense=True`, and
      (d) those same four parameters -- a non-default `limit`, a non-default
          `maxTokens`, `useDense=True`, and `includeUnapproved=True` -- on
          the store with no withheld rows, the same one (a) uses.
    Every parameter axis (d) exercises against the store with nothing to
    withhold is therefore also exercised against the store that has
    something to withhold: the population this test closes is every
    non-default parameter value crossed with every store, not one parameter
    crossed with one store.
    All thirteen captured refusal strings must be the same string, and that
    string must be exactly `SEARCH_CAPACITY_REFUSAL` with nothing appended --
    a mutation that appended caller- or grant-derived text uniformly (round
    1's `refusal-carries-limit`, `refusal-carries-grant`) survived the
    original five-capture, self-consistency-only version of this test,
    because every one of those captures used the same default `limit` and
    the same deployment grant, so the appended text was identical across all
    of them.

    (c)'s captures after the first cross the withheld-corpus axis with a
    parameter axis, and round 2 added only one of them: the
    `includeUnapproved=True` capture. That closed one face of the class, not
    the class -- `limit`, `maxTokens` and `useDense` were still captured only
    against `plain`, whose one applied migration leaves
    `active.migration_count - 1` at `0`, so a mutation keyed on any of *those*
    parameters instead of `includeUnapproved` would still have survived.
    `refusal-cross-axis-leak` (adversarial round 2, M-1) appends
    `f" [{active.migration_count - 1}]"` to the refusal precisely when
    `includeUnapproved` is truthy AND that difference is truthy -- true only
    for `retired` (two applied migrations) with `includeUnapproved=True`, so
    it survived every capture the nine-capture version of this test took and
    is what (c)'s `includeUnapproved=True` capture exists to kill.
    `refusal-cross-axis-usedense` (adversarial round 3, M-1) is the identical
    shape keyed on `useDense` instead of `includeUnapproved` -- it survived
    every capture the ten-capture version of this test took, because that
    version's only withheld-row-store capture besides the default one used
    `includeUnapproved`, never `useDense`, against `retired`. (c)'s
    `useDense=True` capture is what kills it. (c)'s `limit=50` and
    `maxTokens=500` captures close the same population for those two
    parameters by the same construction; no mutation keyed on either is
    named here, because both are non-default numeric values that are truthy
    for essentially every value the tool accepts, so a mutation gated on
    "is truthy" would fire on every capture in this test rather than
    singling one out -- but the axis is still exercised, so a future
    mutation keyed on either has a capture to kill it.

    The `limit=50` capture in (d) -- not the untimed one in (c) -- is also
    timed: `MAX_RESULTS` clamps `capped_limit` to exactly 50, so a mutation
    that scaled the admission wait by `capped_limit` (round 1's
    `wait-scales-with-limit`) would make that one capture arrive at ~5s
    rather than within `ADMISSION_WAIT_SECONDS + 1.0`s.
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

    # (c) a corpus with withheld rows against one with none -- and, in the
    # same round, that withheld-row corpus crossed with each of the four
    # non-default parameters (d) exercises against the store with nothing to
    # withhold: the captures that cross the withheld-corpus axis with a
    # parameter axis (see the docstring's `refusal-cross-axis-leak` /
    # `refusal-cross-axis-usedense` paragraph). `limit` and `maxTokens` are
    # crossed here too so the population is every non-default parameter on
    # both stores, not only the two boolean flags.
    retired_server = build_server(retired)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(
        retired_server, gate, project_id="retired", count=MAX_CONCURRENT_SEARCHES
    )
    try:
        # Gathered concurrently, not five sequential `await`s: each excess
        # caller's own admission wait is `ADMISSION_WAIT_SECONDS` regardless
        # of how many others are also waiting, but the holders above are only
        # guaranteed to stay blocked for `_WAIT_BOUND_SECONDS` -- five
        # sequential ~`ADMISSION_WAIT_SECONDS`-long refusals came within
        # noise of that bound and flaked, where five concurrent ones cost
        # about one wait, not five.
        messages.extend(
            await asyncio.gather(
                _refused(retired_server, project_id="retired", query="a short query"),
                _refused(
                    retired_server,
                    project_id="retired",
                    query="a short query",
                    includeUnapproved=True,
                ),
                _refused(retired_server, project_id="retired", query="a short query", limit=50),
                _refused(
                    retired_server, project_id="retired", query="a short query", maxTokens=500
                ),
                _refused(
                    retired_server, project_id="retired", query="a short query", useDense=True
                ),
            )
        )
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    # (d) the same four non-default parameters as (c) above, saturated again
    # against the store with nothing to withhold (the same store as (a)) --
    # `limit`, `maxTokens`, `useDense` and `includeUnapproved` are surfaces
    # the refusal must not carry either, on either store.
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    tasks = await _saturate(plain_server, gate, project_id="plain", count=MAX_CONCURRENT_SEARCHES)
    try:
        started = time.monotonic()
        messages.append(
            await _refused(plain_server, project_id="plain", query="a short query", limit=50)
        )
        elapsed = time.monotonic() - started
        assert elapsed < ADMISSION_WAIT_SECONDS + 1.0, (
            f"the limit=50 refusal took {elapsed:.2f}s, over the "
            f"{ADMISSION_WAIT_SECONDS + 1.0:.2f}s bound -- the admission wait must not "
            "scale with `limit`"
        )
        messages.append(
            await _refused(plain_server, project_id="plain", query="a short query", maxTokens=500)
        )
        messages.append(
            await _refused(plain_server, project_id="plain", query="a short query", useDense=True)
        )
        messages.append(
            await _refused(
                plain_server, project_id="plain", query="a short query", includeUnapproved=True
            )
        )
    finally:
        drained = await _drain(gate, tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    assert len(messages) == 13
    assert len({*messages}) == 1, (
        "the refusal must be byte-identical whatever the input; observed distinct "
        f"strings: {sorted(set(messages))}"
    )
    # `_call_failing_on` returns the SDK's own wrapper (see its docstring):
    # `f"Error executing tool {name}: {e}"`. Compared against that wrapper
    # applied to the exact constant, not the bare constant, so this still
    # fails if anything is appended to what the tool itself raises.
    expected = f"Error executing tool knowledge.search: {SEARCH_CAPACITY_REFUSAL}"
    assert messages[0] == expected, (
        "the refusal must be exactly the recorded constant, with nothing else appended -- "
        f"got {messages[0]!r}"
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
    # try/finally from the assignment itself, not only around what follows it:
    # `_saturate` schedules its `count` tasks before it can fail its own
    # `entered` check, and a failure there must still release the gate so
    # those already-dispatched, already-blocked holders fail fast instead of
    # each idling out its own `_WAIT_BOUND_SECONDS` independently. `tasks`
    # starts `[]` so the `finally` has something to drain even when
    # `_saturate` never returns one.
    fresh_tasks: list[asyncio.Task[dict[str, Any]]] = []
    try:
        fresh_tasks = await _saturate(
            server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES
        )
    finally:
        drained = await _drain(gate, fresh_tasks)
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)


# -- AC-5: /health stays live while the cap is saturated ---------------------


@pytest.mark.asyncio
async def test_health_answers_promptly_while_the_cap_is_saturated(
    registry: ProjectRegistry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-5 (strengthened: code M-3 / sec M-2 / adversarial M-2). ``/health``
    is what the SessionStart hook and the single-instance probe call, and it
    must answer even while every search worker thread is blocked: it is
    served directly as an ``async def`` route on the asyncio event loop,
    never through ``anyio.to_thread.run_sync``, so it shares nothing with the
    pool the cap is bounding (the mechanism fact threat-model T-6 records as
    the reason a cap was chosen over a wall-clock timeout).

    Launching exactly ``MAX_CONCURRENT_SEARCHES`` holders and nothing else --
    this test's own earlier form -- never actually exercised that claim: no
    thread was ever parked in ``search_admission.acquire()``, and the
    40-token ``anyio`` pool sat at 4/40 borrowed while /health was probed.
    This version saturates the cap AND fills the *whole* pool --
    ``anyio.to_thread.current_default_thread_limiter().total_tokens`` total
    in-flight sync calls, the holders plus enough additional
    ``knowledge.search`` callers genuinely parked in the admission wait to
    exhaust every remaining token -- so the property pinned here is the one
    AC-5 actually claims: ``/health`` never takes a pool token, whatever else
    every one of them is doing.

    Skips the ``with TestClient(app) as client:`` lifespan dance
    ``test_daemon.py`` uses for the ``/mcp`` mount: ``/health`` reads only the
    ``DaemonConfig`` closure, never ``mcp.session_manager``, so the one thing
    the lifespan starts plays no part in the route this test drives -- and a
    plain ``httpx2.ASGITransport`` request confirmed that against the
    *current*, un-gated tool during this test's own construction.

    Scope: the bound pinned below (``elapsed < 0.5``) is for GIL-*releasing*
    holders. ``BoundedSemaphore.acquire`` and the stub's ``threading.Event.wait``
    both release the GIL while blocked, which is what keeps that bound tight.
    A holder that instead holds the GIL continuously -- the real substring
    scan exercised by ``test_the_cap_gates_the_real_substring_scan_fallback``
    below is CPU-bound and does not release it -- is a different residual,
    recorded in threat-model T-6 rather than tested here: with
    ``MAX_CONCURRENT_SEARCHES`` GIL-holding holders admitted, a 12-probe
    series there measured an asyncio tick delayed up to 844ms, 1.7x this
    test's own threshold, so a timing assertion pinned to that recorded worst
    would be inherently flaky rather than a stronger test.
    """
    server = build_server(registry)
    gate = _SaturationGate()
    monkeypatch.setattr(tools, "hybrid_answer", gate.stub)
    # /health is unauthenticated (see build_app), so this token is never checked --
    # generated rather than a literal string so nothing here reads as a secret.
    config = DaemonConfig(token=generate_token(), data_dir=tmp_path)
    starlette_app = build_app(config, server)

    limiter = anyio.to_thread.current_default_thread_limiter()
    total_pool_tokens = int(limiter.total_tokens)
    excess_count = total_pool_tokens - MAX_CONCURRENT_SEARCHES

    tasks = await _saturate(server, gate, project_id="demo", count=MAX_CONCURRENT_SEARCHES)
    # Pre-initialised (mirrors the `fresh_tasks` fix in AC-4's test above): if
    # the list comprehension below raised partway through, `waiters` would
    # otherwise be unbound in `finally`, masking the real failure behind a
    # `NameError` and leaking whatever tasks it had already created.
    waiters: list[asyncio.Task[dict[str, Any]]] = []
    try:
        # Each of these also occupies one `anyio` pool thread for as long as it
        # is parked in `search_admission.acquire(timeout=...)` -- the cap
        # never frees during this test, so together with the holders above
        # they hold every one of the pool's `total_pool_tokens` threads at
        # once, none of them ever reaching `hybrid_answer`/`gate.stub`.
        waiters = [
            asyncio.create_task(
                _call_on(server, "knowledge.search", projectId="demo", query=f"parked waiter {i}")
            )
            for i in range(excess_count)
        ]
        # A fixed delay, comfortably shorter than ADMISSION_WAIT_SECONDS
        # (1.0s), for every waiter to have certainly reached
        # `search_admission.acquire()` -- dispatch to a worker thread and the
        # pre-gate resolve work are both far faster than this.
        await asyncio.sleep(0.3)

        # `borrowed_tokens` is `anyio`'s own live occupancy count -- the
        # signal a prior round of this test claimed did not exist. Asserted
        # immediately before the probe: the fixed delay above is what gets
        # every waiter into `acquire()` in practice, but this is what confirms
        # it actually happened rather than merely assuming it did.
        assert int(limiter.borrowed_tokens) == total_pool_tokens, (
            f"only {int(limiter.borrowed_tokens)}/{total_pool_tokens} anyio pool tokens were "
            "borrowed right before the probe -- the harness failed to saturate the whole "
            "pool, so a fast /health response here would not have proven anything"
        )

        transport = httpx.ASGITransport(app=starlette_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:7419"
        ) as client:
            started = time.monotonic()
            response = await asyncio.wait_for(client.get("/health"), timeout=_WAIT_BOUND_SECONDS)
            elapsed = time.monotonic() - started

        # And again immediately after: a waiter that returned early during the
        # probe -- admitted once a permit freed, or refused once its own
        # admission wait elapsed -- would give up a pool token mid-probe, and
        # the precondition above must hold for the probe's whole duration, not
        # only at its start.
        assert int(limiter.borrowed_tokens) == total_pool_tokens, (
            f"only {int(limiter.borrowed_tokens)}/{total_pool_tokens} anyio pool tokens were "
            "still borrowed once the probe returned -- a waiter returned early during the "
            "probe, so it was not actually saturated for the whole response"
        )
    finally:
        drained = await _drain(gate, tasks)
        # Whether each waiter is admitted (a holder's permit frees once
        # `_drain` releases the gate, above) or refused (its own admission
        # wait elapsed first) is not this test's concern -- AC-1 and the
        # admission-wait test already pin that. What matters here is that
        # none of them leaks a pool thread past this point.
        await asyncio.wait_for(
            asyncio.gather(*waiters, return_exceptions=True), timeout=_WAIT_BOUND_SECONDS
        )
    _assert_all_succeeded(drained, MAX_CONCURRENT_SEARCHES)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert elapsed < 0.5, (
        f"/health took {elapsed:.3f}s while the whole {total_pool_tokens}-token pool was "
        "borrowed -- it must be served off the same asyncio loop those blocked threads "
        "never touch, not queued behind them"
    )


# -- The gate also holds the real substring-scan fallback (adversarial M-1(a),
#    the important one) -------------------------------------------------------

#: A registered-but-unbuilt-index project big enough that `_scan`'s real
#: substring walk holds a permit for a measurable amount of wall-clock time.
#: 900 approved items of roughly 1,000 characters each measured a 0.059s solo
#: no-match scan and, at `_SCAN_CORPUS_CONCURRENT_CALLERS` simultaneous
#: no-match searches against `ADMISSION_WAIT_SECONDS` monkeypatched to 0.3s
#: (this test only -- see its body), 11-13 of 24 refusals across 6 replays
#: under machine saturation on this project's own hardware, never zero --
#: the residual risk this margin does not close is faster hardware still,
#: which admits every caller regardless of the admission wait.
_SCAN_CORPUS_APPROVED_ITEMS: Final = 900
_SCAN_CORPUS_BODY_CHARS: Final = 1_000
_SCAN_CORPUS_CONCURRENT_CALLERS: Final = 24


def _scan_corpus_ulid(prefix: str, n: int) -> str:
    """A 26-char Crockford-base32 id, unique per ``(prefix, n)``.

    Crockford base32 excludes I/L/O/U (see this project's ULID fixture
    guard) -- the same 32-symbol alphabet every other id in this file uses,
    just enumerated by an integer counter instead of written out by hand,
    because `_SCAN_CORPUS_APPROVED_ITEMS` items need that many distinct
    revision ids.
    """
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    tail = ""
    value = n
    for _ in range(26 - len(prefix)):
        tail = alphabet[value % 32] + tail
        value //= 32
    return prefix + tail


def _build_scan_only_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """A registered project with `_SCAN_CORPUS_APPROVED_ITEMS` approved items
    and NO index built -- so `hybrid_answer`'s `_published_index` always
    returns a `Fallback` and every `knowledge.search` call against it takes
    the real `_scan` substring path, not a monkeypatched stand-in.

    `theurian index build` is deliberately never run here: that is the state
    of every project between `migrate apply` and the first `index build` (or
    permanently, if the operator never runs one), and it is the state in
    which `scan-outside-gate` -- releasing the semaphore right after
    `hybrid_answer` returns a `Fallback`, before the fallback scan itself
    runs -- would leave the expensive GIL-holding scan completely unguarded.
    """
    root = tmp_path / "scan-corpus"
    root.mkdir()
    for git_args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(git_args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "scan-corpus-data"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"

    filler = "no match here " * (_SCAN_CORPUS_BODY_CHARS // 15 + 1)
    ops: list[str] = []
    for i in range(_SCAN_CORPUS_APPROVED_ITEMS):
        slug = f"scan-item-{i:05d}"
        body = f"# {slug}\n\n{filler}\n"
        (knowledge / f"{slug}.md").write_text(body)
        item = f"architecture.{slug}"
        ops.append(
            f"  - op: createItem\n"
            f"    itemId: {item}\n"
            f"    kind: architecture\n"
            f"    namespace: backend\n"
            f"    owner: platform-team\n"
            f"  - op: upsertRevision\n"
            f"    itemId: {item}\n"
            f"    revisionId: {_scan_corpus_ulid('01K1S', i)}\n"
            f"    contentFile: ../knowledge/architecture/{slug}.md\n"
            f"    contentSha256: {body_pin(body)}\n"
            f"    metadata:\n"
            f"      title: {slug}\n"
            f"      contentType: text/markdown\n"
            f"      kind: architecture\n"
            f"      namespace: backend\n"
            f"      status: approved\n"
            f"      owner: platform-team\n"
            f"      trustLevel: reviewed\n"
            f"      sourceAnchors:\n"
            f"        - provider: git\n"
            f"          sourceUri: git://demo/{slug}.md\n"
        )

    # One migration file per 400 items (800 op entries): a single migration
    # file is refused past 1,000,000 characters of string content.
    chunk = 400
    for c, start in enumerate(range(0, len(ops), chunk * 2)):
        migration_id = _scan_corpus_ulid("01K1SM", c + 1)
        migration = (
            "apiVersion: theurian.dev/v1\n"
            f"id: {migration_id}\n"
            f"createdAt: 2026-08-0{(c % 8) + 1}T10:00:00+09:00\n"
            "author: engineer@example.com\n"
            "operations:\n" + "".join(ops[start : start + chunk * 2])
        )
        (root / f".theurian/migrations/{migration_id}-scan-bulk.yaml").write_text(migration)

    _run("project", "register")
    _run("migrate", "apply")

    return ProjectRegistry.default(data_dir)


@pytest.mark.asyncio
async def test_the_cap_gates_the_real_substring_scan_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM (adversarial M-1(a) -- the important one).

    Every other saturation test in this file monkeypatches `hybrid_answer`
    with an instrumented stub, so none of them exercises what the gate is
    actually protecting on the *fallback* path: `_scan`'s real substring walk
    over the canonical store, which holds the GIL for the whole duration it
    runs. `scan-outside-gate` (release the permit right after
    `hybrid_answer` returns, before the fallback scan) survived the whole
    suite in round 1: with no index built -- the state of every project
    until `theurian index build` runs -- `hybrid_answer` returns a
    `Fallback` almost immediately, so a mutant that releases there frees the
    semaphore before the expensive scan starts, and every concurrent caller
    runs unrefused.

    `hybrid_answer` itself is not monkeypatched here: `_build_scan_only_project`
    builds a real, unindexed project of `_SCAN_CORPUS_APPROVED_ITEMS` approved
    items, so every one of `_SCAN_CORPUS_CONCURRENT_CALLERS` concurrent
    no-match searches takes the genuine `_scan` path and genuinely holds a
    permit for the scan's own duration. If the gate does not hold across that
    path, none of them is ever refused; this asserts at least one is.

    `ADMISSION_WAIT_SECONDS` *is* monkeypatched, to 0.3s, purely to make that
    assertion robust to hardware speed rather than to exercise anything about
    the constant itself -- see the comment at its call site and
    `_SCAN_CORPUS_APPROVED_ITEMS`'s docstring for the measured margin.
    """
    registry = _build_scan_only_project(tmp_path, monkeypatch)
    server = build_server(registry)
    # Shrunk from the shipped 1.0s so an excess caller's admission wait
    # elapses well inside a single wave of the real scan (measured ~0.059s
    # solo below) rather than being outrun by it on fast hardware -- the
    # shipped value is a call-time module lookup (`tools.ADMISSION_WAIT_
    # SECONDS`, not a name captured at import time), verified live by two
    # round-2 mutation kills, and stays pinned elsewhere by
    # `test_the_cap_pins_its_recorded_constants`.
    monkeypatch.setattr(tools, "ADMISSION_WAIT_SECONDS", 0.3)

    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *[
                _call_on(
                    server, "knowledge.search", projectId="scan-corpus", query=f"zzznomatch-{i}"
                )
                for i in range(_SCAN_CORPUS_CONCURRENT_CALLERS)
            ],
            return_exceptions=True,
        ),
        # `asyncio.wait_for` already fails this test with its own
        # `TimeoutError` if the batch does not finish inside this bound --
        # an `assert elapsed < N` after a successful `wait_for` can only ever
        # be true (a prior round's `assert elapsed < 20.0`, more than double
        # this bound, could never fire), so no such assertion is added here.
        timeout=_WAIT_BOUND_SECONDS * 2,
    )

    refused = [o for o in outcomes if isinstance(o, BaseException) and "maximum number" in str(o)]
    other_errors = [
        o for o in outcomes if isinstance(o, BaseException) and "maximum number" not in str(o)
    ]
    succeeded = [o for o in outcomes if not isinstance(o, BaseException)]
    assert not other_errors, f"every failure here must be the cap's own refusal: {other_errors}"
    assert succeeded, "at least one caller should also have been admitted and answered"
    assert len(refused) >= 1, (
        f"expected the cap to refuse at least one of {_SCAN_CORPUS_CONCURRENT_CALLERS} "
        "concurrent real substring scans; none was refused -- the gate is not holding the "
        "fallback path"
    )
