"""AC-7 runtime companion: the *built* daemon serves no finding (ADR-0029).

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

Together with the unit prongs, the boundary is held from both ends: the serving
layer cannot *import* the store (prong a), no serving module names its *tables*
(prong b, grep), and the surface a caller actually speaks to registers exactly the
known read tools and reaches no store symbol (prong b, runtime -- here).

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
        "system.capabilities",
    }
)

#: Store symbols no serving tool may reach in its own bytecode. Names, because
#: ``_referenced_names`` walks a function's code object -- a tool that constructed
#: ``SqliteReviewFindingStore`` or called into ``FindingsBuilder`` would carry the
#: name whether or not it also imported it at module scope.
STORE_SYMBOLS = frozenset(
    {
        "SqliteReviewFindingStore",
        "ReviewFindingStore",
        "FindingsBuilder",
        "FindingsBuildRequest",
    }
)


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
        f"level. If it serves finding content it belongs to the deferred findings "
        f"lane and its disclosure round, not to this read-only surface."
    )


def test_no_registered_tool_serves_or_reaches_a_finding(empty_registry: ProjectRegistry) -> None:
    """No tool on the built server names a finding or reaches a store symbol (AC-7).

    Two checks over the *actual* registered tools, not the source. First, no tool
    name serves a finding. Second, and stronger for the direct case, no tool's
    bytecode references a store symbol -- a tool that constructed the store or
    called its builder would carry the name in its code object even if it imported
    it lazily inside the function body, where the module-level import scan of prong
    (a) does not look.
    """
    server = build_server(empty_registry)
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this test vacuously"

    naming_a_finding = sorted(tool.name for tool in tools if "finding" in tool.name.lower())
    assert not naming_a_finding, (
        f"the built server registers finding-serving tool(s): {naming_a_finding}. "
        f"A findings search is the deferred disclosure lane, not a read tool here."
    )

    reaching_the_store = {
        tool.name: sorted(hit)
        for tool in tools
        if (hit := _referenced_names(tool.fn) & STORE_SYMBOLS)
    }
    assert not reaching_the_store, (
        "a registered tool reaches the review-finding store in its bytecode:\n"
        + "\n".join(f"  {name} :: {hits}" for name, hits in sorted(reaching_the_store.items()))
        + "\n\nA read-only tool that constructs the store or its builder is a "
        "serving path to a finding -- and one added lazily inside the tool body "
        "hides from the module-level import scan. The findings serving lane lands "
        "with its own disclosure round (ADR-0029), not as a reach from a Milestone "
        "3 read tool."
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
