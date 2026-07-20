#!/usr/bin/env python3
"""Run a low-volume, read-only H-E-B capability check."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from texas_grocery_mcp.tools.product import product_search
from texas_grocery_mcp.tools.session import session_status
from texas_grocery_mcp.tools.shopping_list import shopping_list_get
from texas_grocery_mcp.tools.store import store_search
from texas_grocery_mcp.utils.config import get_settings


async def run_smoke(
    *,
    address: str,
    store_id: str,
    query: str,
    include_list: bool,
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "safety": {
            "read_only": True,
            "mutations_attempted": 0,
        }
    }

    results["store_search"] = await store_search(address=address, radius_miles=5)
    auth = await session_status()
    results["session_status"] = auth

    if not auth.get("authenticated"):
        results["product_search"] = {
            "skipped": True,
            "reason": "Capture a valid manual browser session first.",
        }
        if include_list:
            results["shopping_list_get"] = {
                "skipped": True,
                "reason": "Capture a valid manual browser session first.",
            }
        return results

    if store_id:
        results["product_search"] = await product_search(
            query=query,
            store_id=store_id,
            limit=5,
        )
    else:
        results["product_search"] = {
            "skipped": True,
            "reason": "Set HEB_DEFAULT_STORE or pass --store-id.",
        }
    if include_list:
        results["shopping_list_get"] = await shopping_list_get()
    return results


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        default="San Antonio, TX",
    )
    parser.add_argument("--store-id", default=settings.heb_default_store or "")
    parser.add_argument("--query", default="bananas")
    parser.add_argument("--include-list", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(
        run_smoke(
            address=args.address,
            store_id=args.store_id,
            query=args.query,
            include_list=args.include_list,
        )
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
