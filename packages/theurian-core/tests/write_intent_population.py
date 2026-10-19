"""The key for "a registered write-intent tool", shared by every test that needs the set.

A tool is write-intent when its body closes over ``_draft_only_proposals``, the
per-call factory that hands it a ``DraftOnlyProposals`` facade (ADR-0032 decision
8). Measured at 6c3c2827: 3 tools, the 3 call sites of
``_draft_only_proposals(projectId)`` in ``mcp/tools.py`` (key:
``git grep -n -F '_draft_only_proposals(projectId)' 6c3c2827 --
packages/theurian-core/src/theurian/mcp/tools.py``).

Cannot see a tool that drafts without the facade.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

FACADE_FREEVAR = "_draft_only_proposals"


def closes_over_the_facade(fn: Callable[..., Any]) -> bool:
    body = getattr(fn, "__wrapped__", fn)
    return FACADE_FREEVAR in body.__code__.co_freevars


def write_intent_names(tools: Iterable[Any]) -> frozenset[str]:
    """Names of the tools in *tools* (FastMCP ``Tool`` objects) keyed as write-intent."""
    return frozenset(tool.name for tool in tools if closes_over_the_facade(tool.fn))


def registered_write_intent_names(server: Any) -> frozenset[str]:
    return write_intent_names(server._tool_manager.list_tools())
