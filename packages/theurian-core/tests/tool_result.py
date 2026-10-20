"""One place that reads a ``CallToolResult`` the way a client sees it.

A response is published twice, as a structured copy and as a JSON text block, and
a client may read either. A helper that reads only one leaves the other
unchecked, so this returns the structured copy and asserts the text block says
the same thing (#635). Aliases such as ``structuredContent`` are not attributes
on mcp 2.3.0; the fields are ``structured_content`` and ``is_error``.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent


def structured_payload(result: object) -> dict[str, Any]:
    assert isinstance(result, CallToolResult), f"got {type(result).__name__}, not a tool result"
    assert result.is_error is False, "the tool answered with the error flag set"

    structured = result.structured_content

    assert structured is not None, "the tool published no structured copy"
    assert len(result.content) == 1, "the tool published other than one content block"

    block = result.content[0]

    assert isinstance(block, TextContent), f"the content block is {type(block).__name__}, not text"
    assert json.dumps(json.loads(block.text), sort_keys=True) == json.dumps(
        structured, sort_keys=True
    ), "the text block and the structured copy of one response disagree"

    payload: dict[str, Any] = structured
    return payload
