"""The seat SEC-12's check runs from: an SDK ``ServerMiddleware`` (ADR-0031).

:mod:`theurian.mcp.validation` answers *may this tool call reach a handler?*
without importing the SDK. This module is the other half -- where that question
is asked -- and it is a middleware rather than a check inside a tool body for
one measured reason: **by the time a tool body runs, the evidence is gone.**

``mcp==2.1.1`` builds a per-tool pydantic model from the handler's signature,
and ``ArgModelBase`` sets no ``extra="forbid"`` (``ConfigDict(
arbitrary_types_allowed=True)``), so pydantic's default of ``ignore`` applies: a
key the model does not name is dropped before any Theurian code sees the
request. No check a handler could carry can notice it was ever there. The
``ServerMiddleware`` tier is the one seat above that coercion -- its own contract
says it "runs at the top of ``ServerRunner._on_request`` ... **before any
validation**, lookup, or handshake", with "the method and the raw inbound params
... ``ctx.method`` and ``ctx.params``".

**This middleware's position among the SDK's own two is the SDK's, and nothing
here depends on being outermost.** ``Server.__init__`` seeds the chain with
``OpenTelemetryMiddleware``, ``MCPServer.__init__`` appends
``RequestStateBoundary``, and only then is the caller's list extended in. Both
built-ins run before params validation, which is all this control needs.

**No message is built here.** Every string a caller reads comes from
:mod:`~theurian.mcp.validation`, whose bounded-refusal discipline *is* the
control (ADR-0031 decision 4): each caller-written fragment escaped through
``repr`` and cut, the assembled message held under a ceiling at construction.
A message composed at this seam would be outside all of it. For the same reason
this module invents no refusal *distinctions*: which inputs refuse and how they
are told apart is ``validation.py``'s, so an error cannot become a channel that
fires for one input and not another (SEC-13).

**A refusal crosses as the shape this surface already uses**: the SDK's tool
dispatcher converts a ``ToolError`` into ``CallToolResult(content=[TextContent(
...)], is_error=True)``, and a refusal from here is that same ``content`` plus
``isError``. A caller should not be able to tell which tier refused it from the
*shape* of the answer -- only from what it says.

**Which costs one step the SDK would otherwise do, and that is measured rather
than assumed.** ``ServerRunner._serialize`` -- the per-version sieve that drops
result fields the negotiated protocol era does not define -- runs *inside*
``_inner``, so a middleware that answers without calling ``call_next`` skips it.
The SDK says so in its own comment: such a middleware "is trusted to return its
own well-formed result - including its response envelope. The pipeline never
patches it up after the fact." Returning the model straight from here therefore
published ``resultType: "complete"`` on a connection that had negotiated
``2025-06-18`` -- driven over a real ``tools/call``, where the same refusal from
the handler tier carried ``content`` and ``isError`` alone.

So the envelope is built from the SDK's model and then given the one era rule
the sieve would have applied: ``resultType`` is a modern-era field and is
dropped for every other era. The keys are still the model's -- ``isError`` comes
from its alias rather than from this module spelling a wire name -- and the
result is equal to ``mcp_types.methods.serialize_server_result``'s own output
for ``2025-03-26``, ``2025-06-18`` and ``2026-07-28`` (driven against
``mcp==2.1.1``). That function is not called here because importing
``mcp_types`` directly would name a distribution the ``daemon`` extra supplies
only transitively -- ``MODERN_PROTOCOL_VERSIONS`` comes from
``mcp.types.version``, the mirror the SDK ships for exactly that reason ("code
that depends on ``mcp`` ... without importing the ``mcp_types`` distribution
directly"), rather than being respelled here as a date.

**One residual, recorded rather than discovered later.** ``_serialize`` also
stamps ``_meta.serverInfo`` on a modern-era result, and that stamp is built from
server state no middleware is handed. A refusal from this tier reaches a
``2026-07-28`` client without it. Nothing about the refusal changes; the client
learns the server's identity from the handshake instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent
from mcp.types.version import MODERN_PROTOCOL_VERSIONS

from theurian.mcp.validation import InputRefusal, InputSchemaSet

#: The one inbound method whose params carry a tool call. Every other method --
#: ``initialize``, ``tools/list``, ``ping``, a notification -- passes through
#: untouched: this control is over tool *input*, and a middleware that answered
#: for the handshake would be a different control with a different failure mode.
CALL_TOOL_METHOD: Final = "tools/call"

#: The result field the modern protocol era requires and every earlier era does
#: not describe. Named here because this module has to apply the sieve's rule to
#: its own envelope; see the module docstring for why it owns one at all.
_MODERN_ONLY_RESULT_FIELD: Final = "resultType"


def _envelope(refusal: InputRefusal, protocol_version: str) -> dict[str, Any]:
    """``refusal`` as the wire result a sieved ``tools/call`` failure would be.

    Built from the SDK's own model so every key is its, then stripped of the one
    field the negotiated era does not describe -- the single rule
    ``ServerRunner._serialize`` would have applied had this answer come from
    inside ``call_next``. The module docstring carries the measurement and the
    residual.
    """
    dumped = CallToolResult(
        content=[TextContent(type="text", text=refusal.message)], is_error=True
    ).model_dump(by_alias=True, mode="json", exclude_none=True)
    if protocol_version not in MODERN_PROTOCOL_VERSIONS:
        dumped.pop(_MODERN_ONLY_RESULT_FIELD, None)
    return dumped


class InputValidationMiddleware:
    """Refuse a ``tools/call`` whose arguments its published schema rejects.

    Holds the loaded set rather than a path: the schemas are read once, when the
    server is built, so a request pays no filesystem cost and a damaged install
    fails at startup instead of once per call (ADR-0031 decision 5, and
    :func:`~theurian.mcp.validation.load_input_schemas`, which is where the
    fail-closed conditions live).

    A tool this set does not hold is **refused**, not passed through, which is
    that decision's whole point: a control that applies to the tools somebody
    remembered to enumerate is one a future tool leaves by omission, silently and
    in the direction that matters.

    **The checked object and the dispatched object are the same object**, not two
    parses of one payload: ``_inner`` reads ``method, params = ctx.method,
    ctx.params`` and validates *that* mapping into the handler's model. So there
    is no parser differential to exploit -- no duplicate key resolved one way
    here and another below, no second decoding of the same bytes -- which is the
    classic way a validating proxy and the thing it protects come to disagree.
    """

    __slots__ = ("_schemas",)

    def __init__(self, schemas: InputSchemaSet) -> None:
        self._schemas = schemas

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        refusal = self._refusal(ctx.method, ctx.params)
        if refusal is None:
            return await call_next(ctx)
        return _envelope(refusal, ctx.protocol_version)

    def _refusal(self, method: str, params: Mapping[str, Any] | None) -> InputRefusal | None:
        """The refusal this call earns, or ``None`` to let the chain continue.

        **Two malformed envelopes pass through here and are refused below**, and
        that is measured rather than assumed: ``CallToolRequestParams`` types
        ``name`` as ``str`` and ``arguments`` as an object-or-null, so a
        non-string ``name``, a non-object ``arguments`` and an absent ``name``
        each raise ``ValidationError`` inside ``call_next`` -- ``INVALID_PARAMS``,
        before lookup and before any handler. Nothing reaches a tool body either
        way, so passing them on is fail-closed; refusing them *here* would mean
        wording a third refusal shape this module is not allowed to invent.

        An absent ``arguments`` is a different case and is checked here: it is
        the legal spelling of a call with no arguments, and the schema for a tool
        that requires one must see the empty object rather than nothing.
        """
        if method != CALL_TOOL_METHOD or params is None:
            return None
        name = params.get("name")
        if not isinstance(name, str):
            return None
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return None
        return self._schemas.validate(name, arguments)
