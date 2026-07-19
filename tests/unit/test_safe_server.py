"""Tests for the intentionally restricted planner MCP surface."""

from typing import Literal

import pytest

from texas_grocery_mcp.safe_server import build_mcp
from texas_grocery_mcp.utils.config import Settings


async def _tool_names(write_scope: Literal["read-only", "shopping-list"]) -> set[str]:
    settings = Settings(heb_write_scope=write_scope)
    server = build_mcp(settings)
    return {tool.name for tool in await server.list_tools()}


@pytest.mark.asyncio
async def test_read_only_server_exposes_no_account_mutations():
    names = await _tool_names("read-only")

    assert "product_search" in names
    assert "planner_queue_get" in names
    assert "shopping_list_get" in names
    assert "cart_get" in names
    assert "shopping_list_add" not in names
    assert (
        not {
            "cart_add",
            "cart_add_many",
            "cart_remove",
            "coupon_clip",
            "shopping_list_remove",
            "store_change",
            "session_save_credentials",
            "session_refresh",
            "session_save_instructions",
            "session_clear",
        }
        & names
    )


@pytest.mark.asyncio
async def test_shopping_list_scope_is_add_only():
    names = await _tool_names("shopping-list")

    assert {"shopping_list_add", "shopping_list_add_many"} <= names
    assert "shopping_list_add_with_retry" not in names
    assert (
        not {
            "shopping_list_remove",
            "cart_add",
            "cart_remove",
            "coupon_clip",
            "store_change",
        }
        & names
    )
