#!/usr/bin/env python3
"""Verify the planner-safe MCP handshake and local planner queue."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import Client

from texas_grocery_mcp.safe_server import mcp

FORBIDDEN_TOOLS = {
    "cart_add",
    "cart_add_many",
    "cart_remove",
    "checkout",
    "coupon_clip",
    "session_refresh",
    "session_save_credentials",
    "shopping_list_remove",
    "store_change",
}


def _result_data(result: Any) -> dict[str, Any]:
    for attribute in ("data", "structured_content"):
        value = getattr(result, attribute, None)
        if isinstance(value, dict):
            return value
    return {}


async def run_smoke() -> dict[str, Any]:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = sorted(tool.name for tool in tools)
        forbidden = sorted(FORBIDDEN_TOOLS.intersection(names))
        if forbidden:
            raise RuntimeError(f"Forbidden tools exposed: {forbidden}")

        queue_result = await client.call_tool("planner_queue_get", {"group": "ready"})
        queue_data = _result_data(queue_result)
        summary = queue_data.get("summary", {})

        return {
            "connected": True,
            "tool_count": len(names),
            "list_write_tools": [name for name in names if name.startswith("shopping_list_add")],
            "cart_write_tools": [name for name in names if name.startswith("cart_add")],
            "checkout_tools": [name for name in names if "checkout" in name],
            "planner_queue": {
                "configured": queue_data.get("configured"),
                "ready": summary.get("ready"),
                "review_first": summary.get("review_first"),
                "returned": summary.get("returned"),
            },
        }


def main() -> None:
    print(json.dumps(asyncio.run(run_smoke()), indent=2))


if __name__ == "__main__":
    main()
