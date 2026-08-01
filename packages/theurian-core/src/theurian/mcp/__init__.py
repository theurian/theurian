"""The MCP server: tool definitions and request handling.

A composition root. Streamable HTTP at ``/mcp``, with ``Origin`` and ``Host``
validation and a bearer token on every request (ADR-0011).

Every project-scoped tool requires an explicit ``projectId``. There is no
process-global and no connection-scoped current project: with many agents
sharing one daemon, an implicit default resolves one agent's query against
another agent's project (ADR-0002, SEC-13).

No tool reaches a write path for approved knowledge (ADR-0013).
"""
