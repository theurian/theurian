"""AC-7 runtime companion: exactly one *built* tool serves a finding (ADR-0029).

The unit prongs in ``tests/unit/test_findings_store_is_unreachable.py`` read the
shipped source. This is the arm that reads the running registry: it constructs the
MCP server the daemon builds and asks it, rather than ``mcp/tools.py``, what tools
exist. Two things the source scan cannot see are visible here:

- a tool registered by some mechanism other than the registration decorators the
  AST arm keys on -- an ``add_tool`` call, a name assembled at runtime -- still
  appears in ``server._tool_manager.list_tools()``;
- a tool that reaches a store *symbol* in its own bytecode is caught by walking
  ``tool.fn`` and everything it wraps, which is stronger than a name check for
  the direct case. The ``__wrapped__`` hop is load-bearing rather than
  incidental: since #491 ``tool.fn`` is ``mcp/tools.py``'s ``_forwarding``
  wrapper, and a walk that stopped there saw three names belonging to the
  wrapper while a planted store symbol in the tool body passed unnoticed.

**Slice-3 inverts this rather than relaxing it, and the inversion makes the walk
self-checking.** ``review.findings`` serves findings, so the claim is no longer
"no tool reaches a store symbol" but "exactly ``review.findings`` does". That
turns the sanctioned tool into a **positive control**: the walk must *find* the
adapter in its bytecode, so a walk that reaches nothing now fails outright
instead of reporting a clean search. The store is constructed in the tool's own
body precisely so this arm has something to require -- a tool that delegated the
construction to a helper would leave the walk with only the helper's name, which
is the one-hop gap the unit file records.

Together with the unit prongs, the boundary is held from both ends: only the
sanctioned serving modules *import* the store (prong a), no serving module names
its *tables* (prong b, grep), and the surface a caller actually speaks to
registers exactly the known tools, of which exactly one names a finding and
exactly that one reaches a store symbol (prong b, runtime -- here).

Hermetic: the server is built against an empty registry under a redirected
``THEURIAN_DATA_DIR``, so the tool *set* -- which is registered independently of
any project's contents -- is enumerated without a real project on disk. No socket
is opened; the server is constructed, not served.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

#: The same set the unit AST arm pins, restated here so the runtime registry is
#: checked against the read-only surface directly. Duplicated on purpose: the two
#: arms verify the *same* claim through different mechanisms (source vs. built
#: server), and a single shared constant would let one mechanism's break hide the
#: other's.
KNOWN_TOOL_NAMES = frozenset(
    {
        "knowledge.search",
        "knowledge.get",
        "knowledge.status",
        "project.list",
        "review.findings",
        "system.capabilities",
    }
)

#: The one tool sanctioned to reach the store, restated here for the same reason
#: the set above is: the two arms check one claim through different mechanisms,
#: and a shared constant would let one arm's break hide the other's.
FINDINGS_TOOL_NAME = "review.findings"

#: Store symbols only the sanctioned tool may reach in its own bytecode. Names,
#: because ``_referenced_names`` walks a function's code object -- a tool that
#: constructed ``SqliteReviewFindingStore`` or called into ``FindingsBuilder``
#: would carry the name whether or not it also imported it at module scope.
STORE_SYMBOLS = frozenset(
    {
        "SqliteReviewFindingStore",
        "ReviewFindingStore",
        "FindingsBuilder",
        "FindingsBuildRequest",
    }
)

#: What the sanctioned tool is permitted to reach, which is the adapter and
#: nothing else. ``FindingsBuilder``/``FindingsBuildRequest`` are the rebuild
#: path: a read tool reaching one would be the serving surface acquiring a write
#: (ADR-0013's shape, at a different store), and it fails below even though the
#: tool is otherwise sanctioned.
SANCTIONED_TOOL_SYMBOLS = frozenset({"SqliteReviewFindingStore"})


@pytest.fixture
def empty_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registry over a redirected, empty data dir -- no real project touched.

    The registered tool set does not depend on any project's contents, so an empty
    registry is enough to enumerate it. ``THEURIAN_DATA_DIR`` is redirected so the
    registry never reads or writes the developer's real data directory.
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    yield ProjectRegistry.default(tmp_path / "datadir")


def _referenced_names(function: Any) -> set[str]:
    """Every attribute and global name reachable from a function's code.

    Follows nested code objects, so a store constructed inside a comprehension or
    an inner helper of the tool is still visible. The same walk
    ``test_mcp_tools.py`` uses to pin that no tool reaches a canonical write.

    **And follows ``__wrapped__``.** Since #491 every tool is registered through
    ``mcp/tools.py``'s ``_tool`` seam, so ``Tool.fn`` is ``_forwarding``'s
    wrapper and reaches the real body through a *free variable*, which is not in
    ``co_consts``. Walking ``Tool.fn`` alone saw the wrapper's own three names
    and a planted store symbol inside a tool body passed this guard. The whole
    chain is walked -- wrapper *and* wrapped, not ``inspect.unwrap``'s innermost
    function alone -- so a store reference introduced in an intermediate wrapper
    is still seen.
    """
    seen: set[str] = set()
    pending: list[Any] = []
    fn: Any = function
    while fn is not None and hasattr(fn, "__code__"):
        pending.append(fn.__code__)
        fn = getattr(fn, "__wrapped__", None)
    while pending:
        code = pending.pop()
        seen.update(code.co_names)
        pending.extend(const for const in code.co_consts if hasattr(const, "co_names"))
    return seen


def test_the_built_server_registers_exactly_the_known_read_tools(
    empty_registry: ProjectRegistry,
) -> None:
    """The running registry matches the pinned read-only set -- however tools land.

    The unit arm reads the ``_tool(server, name=...)`` registrations from source;
    this reads the tool manager of the constructed server. A tool added by a
    mechanism the source scan does not key on would show up here and nowhere
    else, so this whole-set equality
    is the drift guard for the runtime surface -- and, like its unit twin, forces a
    new tool through classification before it can be registered unremarked.
    """
    server = build_server(empty_registry)

    # The private manager, deliberately: `list_tools` returns wire schemas, this
    # needs the tool objects and their callables.
    registered = {tool.name for tool in server._tool_manager.list_tools()}

    assert registered == set(KNOWN_TOOL_NAMES), (
        f"the built server registers {sorted(registered)}, pinned set is "
        f"{sorted(KNOWN_TOOL_NAMES)}. A tool was added or removed at the registry "
        f"level. If it serves finding content it needs its own disclosure round -- "
        f"`{FINDINGS_TOOL_NAME}` had one (ADR-0029 phase-2 slice-3), and a second "
        f"surface does not inherit it."
    )


def test_exactly_one_registered_tool_serves_and_reaches_a_finding(
    empty_registry: ProjectRegistry,
) -> None:
    """One tool names a finding, one tool reaches the store, and they are the same one.

    Three checks over the *actual* registered tools, not the source, and each
    fails in both directions since slice-3:

    1. exactly one registered name says "finding". Two is a serving surface with
       no disclosure round; zero is the sanctioned tool renamed out from under
       this file;
    2. exactly one tool's bytecode reaches a store symbol, and it is that same
       tool. A tool that constructed the store or called its builder would carry
       the name in its code object even if it imported it lazily inside the
       function body, where the module-level import scan of prong (a) does not
       look;
    3. what the sanctioned tool reaches is the *adapter*, not the builder -- the
       read surface must not be holding the rebuild path.

    Check 2's positive half is what makes this arm self-checking: the walk has to
    *find* something, so a walk that reached nothing -- the #491 failure, where
    ``tool.fn`` became a wrapper and the walk collapsed to its three names --
    fails here rather than reporting every tool clean.
    """
    server = build_server(empty_registry)
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this test vacuously"

    naming_a_finding = sorted(tool.name for tool in tools if "finding" in tool.name.lower())
    assert naming_a_finding == [FINDINGS_TOOL_NAME], (
        f"the built server registers finding-serving tool(s) {naming_a_finding}, "
        f"and the sanctioned set is ['{FINDINGS_TOOL_NAME}']. A second findings "
        f"surface does not inherit the first one's disclosure round; an empty list "
        f"means the tool was renamed and this arm is checking nothing."
    )

    reaching_the_store = {
        tool.name: _referenced_names(tool.fn) & STORE_SYMBOLS
        for tool in tools
        if _referenced_names(tool.fn) & STORE_SYMBOLS
    }
    assert sorted(reaching_the_store) == [FINDINGS_TOOL_NAME], (
        "the set of registered tools reaching the review-finding store in their "
        f"bytecode is {sorted(reaching_the_store)}, and it must be exactly "
        f"['{FINDINGS_TOOL_NAME}']:\n"
        + "\n".join(
            f"  {name} :: {sorted(hits)}" for name, hits in sorted(reaching_the_store.items())
        )
        + "\n\nToo many: a read tool that constructs the store or its builder is a "
        "serving path to a finding, and one added lazily inside the tool body hides "
        "from the module-level import scan. Too few (an empty set): the walk is not "
        "reaching tool bodies -- the #491 shape, where `Tool.fn` became a wrapper "
        "and the walk collapsed onto its own three names while a planted store "
        "symbol passed unnoticed."
    )

    assert reaching_the_store[FINDINGS_TOOL_NAME] <= SANCTIONED_TOOL_SYMBOLS, (
        f"`{FINDINGS_TOOL_NAME}` reaches "
        f"{sorted(reaching_the_store[FINDINGS_TOOL_NAME])}, which is more of the "
        f"store than the read surface may hold: {sorted(SANCTIONED_TOOL_SYMBOLS)}. "
        f"The builder rebuilds the store from git -- a read tool reaching it is the "
        f"serving surface acquiring a write."
    )


def _names_without_following_wrapped(function: Any) -> set[str]:
    """``_referenced_names``' walk with the ``__wrapped__`` hop removed.

    The reblinded walk, kept beside the real one so the premise check below can
    say what the hop is worth. Identical in every respect but that one hop.
    """
    seen: set[str] = set()
    pending = [function.__code__]
    while pending:
        code = pending.pop()
        seen.update(code.co_names)
        pending.extend(const for const in code.co_consts if hasattr(const, "co_names"))
    return seen


def test_the_findings_walk_reaches_a_real_tool_body(empty_registry: ProjectRegistry) -> None:
    """The premise the disclosure guard above rests on, asserted not assumed.

    ``test_no_registered_tool_serves_or_reaches_a_finding`` answers "does any
    tool reach a store symbol?" and reports *no* as clean. A walk that reaches
    nothing therefore passes it while checking nothing -- exactly what the #491
    seam caused when ``Tool.fn`` became a wrapper whose body hangs off a free
    variable: the walk collapsed to the wrapper's own names, and a planted
    ``SqliteReviewFindingStore`` inside a tool body passed.

    ``test_mcp_tools.py`` grew this ratchet in round one; this file's walker did
    not, so reverting *its* ``__wrapped__`` hop alone -- no plant -- survived the
    full suite while the ADR-0029 disclosure guard passed vacuously (adversarial
    R2-B). The two walkers now carry the same premise check.

    Stated as a strict superset per tool rather than as a count: following
    ``__wrapped__`` must add at least one name for every registered tool, or the
    walk that backs the disclosure guard is inspecting the wrapper, not the body.
    """
    server = build_server(empty_registry)
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this test vacuously"

    thin: dict[str, int] = {}
    for tool in tools:
        full = _referenced_names(tool.fn)
        wrapper_only = _names_without_following_wrapped(tool.fn)
        if not wrapper_only < full:
            thin[tool.name] = len(full)

    assert not thin, (
        f"following `__wrapped__` added no name for these tools, so the disclosure "
        f"guard that walks them is inspecting the wrapper rather than the tool body "
        f"and would report a store reference in a tool as clean: {thin}"
    )
