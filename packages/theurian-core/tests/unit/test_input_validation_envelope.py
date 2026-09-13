"""The refusal envelope's per-era shape equals the SDK's own sieve (ADR-0031).

``InputValidationMiddleware`` answers a refused ``tools/call`` without calling
``call_next``, which skips ``ServerRunner._serialize`` -- the per-version sieve
that drops result fields the negotiated protocol era does not define. So
``middleware._envelope`` reapplies that one rule itself, and its docstring claims
the result equals ``mcp_types.methods.serialize_server_result``'s own output for
every protocol era the SDK supports.

That is an equivalence, not a single measurement, and the wire tests exercise it
over only the one era ``mcp_wire_session`` negotiates (``2025-06-18``). Held here
instead: ``_envelope`` is recomputed against the SDK's serializer for **every**
era in ``SUPPORTED_PROTOCOL_VERSIONS``, and the era list is read off the SDK
rather than transcribed, so a future era joins this check by existing rather than
by someone remembering to add it.

Both arms of the era gate are driven -- the modern era keeps ``resultType`` and
every earlier era drops it. ``test_the_checked_era_set_exercises_both_sides_of_the_gate``
refuses to let the suite pass unless the checked set holds at least one of each:
without a modern era, mutating the gate to drop ``resultType`` unconditionally
would survive; without a legacy era, gating a different field (or never dropping)
would.

The one field ``_serialize`` also stamps on a modern result and ``_envelope``
deliberately does not -- ``_meta.serverInfo``, built from server state no
middleware is handed -- is not a divergence from ``serialize_server_result``,
which is the pure per-era shaper and adds no such stamp either. The equivalence
this file pins is with that function, exactly as the middleware docstring words
it; the ``_meta`` residual is a property of the fuller ``_serialize`` and is not
in scope here.
"""

from __future__ import annotations

import pytest
from mcp.types import CallToolResult, TextContent
from mcp.types.version import MODERN_PROTOCOL_VERSIONS, SUPPORTED_PROTOCOL_VERSIONS
from mcp_types.methods import serialize_server_result

from theurian.mcp.middleware import CALL_TOOL_METHOD, _envelope
from theurian.mcp.validation import InputRefusal

pytestmark = pytest.mark.unit

#: One refusal, built once. Its exact text does not matter to the equivalence --
#: what matters is that the same refusal is envelope-shaped two ways and the two
#: agree -- so a plausible message is used rather than a sentinel.
_REFUSAL = InputRefusal(
    tool="knowledge.search",
    message="knowledge.search: the request does not match this tool's published input schema.",
)


def _server_model_dump() -> dict[str, object]:
    """The SDK server model's own dump of :data:`_REFUSAL`.

    This is the shape ``ServerRunner._serialize`` is handed *inside*
    ``call_next`` -- the full model with ``resultType`` present, before any era
    sieve -- and it is what both ``_envelope`` and the SDK serializer start from.
    Built from ``mcp.types.CallToolResult`` so nothing here transcribes the
    modern-only field's name or its ``"complete"`` value; the SDK supplies both.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=_REFUSAL.message)], is_error=True
    ).model_dump(by_alias=True, mode="json", exclude_none=True)


def test_the_checked_era_set_exercises_both_sides_of_the_gate() -> None:
    """The premise the equivalence rests on: the SDK lists both kinds of era.

    The parametrized check below only exercises the era gate's ``keep`` arm if
    ``SUPPORTED_PROTOCOL_VERSIONS`` names a modern era, and its ``drop`` arm only
    if it names an earlier one. If a future SDK narrowed the set to one side, the
    equivalence check would still be green while covering half the gate; this
    fails first and says which side went missing.
    """
    modern = [era for era in SUPPORTED_PROTOCOL_VERSIONS if era in MODERN_PROTOCOL_VERSIONS]
    legacy = [era for era in SUPPORTED_PROTOCOL_VERSIONS if era not in MODERN_PROTOCOL_VERSIONS]

    assert modern, ("no modern era in", SUPPORTED_PROTOCOL_VERSIONS)
    assert legacy, ("no legacy era in", SUPPORTED_PROTOCOL_VERSIONS)


@pytest.mark.parametrize("era", SUPPORTED_PROTOCOL_VERSIONS)
def test_the_refusal_envelope_equals_the_sdk_serializer_for_every_era(era: str) -> None:
    """RED if ``_envelope`` and the SDK's per-era sieve disagree for any era.

    ``_envelope`` shapes the refusal for ``era`` by reapplying, by hand, the one
    rule the skipped ``_serialize`` would have; ``serialize_server_result`` is
    the SDK's own shaper for the same ``(method, era)``. They must produce the
    same dict from the same server-model dump -- for the modern era that keeps
    ``resultType`` and for every earlier era that drops it. Mutating the gate to
    drop ``resultType`` unconditionally makes the modern case diverge; gating a
    different field makes a legacy case diverge; either goes RED here.
    """
    assert _envelope(_REFUSAL, era) == serialize_server_result(
        CALL_TOOL_METHOD, era, _server_model_dump()
    )
