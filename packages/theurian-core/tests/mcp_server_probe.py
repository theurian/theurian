"""Read what a *built* MCP server actually holds, for tests that assert over it.

Both SEC-12 sweeps -- the registered-tool/published-schema population and the
published-versus-derived agreement -- are claims about one server object, and
both would be weakened by asking the schemas tree the same question instead: a
second ``load_input_schemas`` call agrees with the tree while the server runs on
something else, and a sweep built that way reports the disagreement as clean.
"""

from __future__ import annotations

from mcp.server import MCPServer

from theurian.mcp.middleware import InputValidationMiddleware
from theurian.mcp.validation import InputSchemaSet


def loaded_input_schemas(server: MCPServer) -> InputSchemaSet:
    """The published input schemas ``server`` validates against, off its live chain."""
    seated = [entry for entry in server.middleware if isinstance(entry, InputValidationMiddleware)]
    assert len(seated) == 1, [type(entry).__name__ for entry in server.middleware]
    # The private attribute, deliberately: the middleware publishes no accessor,
    # and the alternative -- re-reading the tree -- answers a different question.
    return seated[0]._schemas


def registered_tool_names(server: MCPServer) -> frozenset[str]:
    """Every tool name ``server`` dispatches, off the private manager.

    The private manager rather than the public ``list_tools``: the public one is
    an async coroutine returning wire schemas, and every caller here wants the
    registration the SDK holds.
    """
    return frozenset(tool.name for tool in server._tool_manager.list_tools())
