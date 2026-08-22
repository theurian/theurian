#!/usr/bin/env python3
"""Query the sample project through Theurian's shipped MCP surface."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

DEFAULT_QUERY = "order cancellation deadline before mutation"
EXPECTED_TOP_HIT = "domain.order-cancellation"


def _default_data_dir() -> Path:
    return Path(os.environ.get("THEURIAN_DATA_DIR", Path.home() / ".theurian")).expanduser()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the sample project's first useful Theurian query over MCP."
    )
    parser.add_argument("--project-id", default="sample-project")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument(
        "--url",
        default=f"http://127.0.0.1:{os.environ.get('THEURIAN_PORT', '7419')}/mcp",
        help="Theurian MCP endpoint.",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=_default_data_dir() / "auth" / "mcp-token",
        help="Bearer-token file written by `theurian daemon start`.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full structured MCP response instead of the short demo summary.",
    )
    parser.add_argument(
        "--no-assert",
        action="store_true",
        help=f"Do not fail if the top hit is not {EXPECTED_TOP_HIT}.",
    )
    return parser.parse_args()


async def _search(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token_file.expanduser().read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {token}"}

    # One `async with` rather than three nested ones: each context manager may
    # use the name bound by the one before it, so the transport, the streams,
    # and the session still open in order and close in reverse.
    async with (
        create_mcp_http_client(headers=headers) as http_client,
        streamable_http_client(args.url, http_client=http_client) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "knowledge.search",
            {
                "projectId": args.project_id,
                "query": args.query,
                "limit": args.limit,
            },
        )
        if result.is_error:
            message = result.content[0].text if result.content else "tool call failed"
            raise RuntimeError(message)
        structured = result.structured_content
        if structured is None:
            raise RuntimeError("knowledge.search returned no structured content")
        return dict(structured)


def _print_summary(payload: dict[str, Any]) -> None:
    results = payload.get("results", [])
    if not results:
        print("No results.")
        return

    hit = results[0]
    freshness = hit.get("freshness", {})
    anchors = hit.get("sourceAnchors", [])
    anchor = anchors[0] if anchors else {}

    print(f"Found: {hit.get('itemId')}")
    print(f"Title: {hit.get('title')}")
    print(
        "Status: "
        f"{hit.get('status')} / trust={hit.get('trustLevel')} / "
        f"valid={freshness.get('isWithinValidity')}"
    )
    print(f"Evidence: {anchor.get('sourceUri', 'none')}")
    print(f"Excerpt: {hit.get('excerpt')}")


def main() -> int:
    args = _parse_args()
    payload = asyncio.run(_search(args))

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)

    results = payload.get("results", [])
    if not args.no_assert and (not results or results[0].get("itemId") != EXPECTED_TOP_HIT):
        print(f"Expected top hit {EXPECTED_TOP_HIT}, but got a different result.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
