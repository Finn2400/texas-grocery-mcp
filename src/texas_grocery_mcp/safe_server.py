"""Planner-safe MCP entry point with an intentionally narrow tool surface."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastmcp import FastMCP

from texas_grocery_mcp import __version__
from texas_grocery_mcp.observability.health import health_live, health_ready
from texas_grocery_mcp.observability.logging import configure_logging
from texas_grocery_mcp.state import StateManager
from texas_grocery_mcp.tools.cart import cart_check_auth, cart_get
from texas_grocery_mcp.tools.coupon import (
    coupon_categories,
    coupon_clipped,
    coupon_list,
    coupon_search,
)
from texas_grocery_mcp.tools.planner import planner_queue_get
from texas_grocery_mcp.tools.product import product_get, product_search, product_search_batch
from texas_grocery_mcp.tools.session import session_status
from texas_grocery_mcp.tools.shopping_list import (
    shopping_list_add,
    shopping_list_add_many,
    shopping_list_check_auth,
    shopping_list_get,
)
from texas_grocery_mcp.tools.store import store_get_default, store_search
from texas_grocery_mcp.utils.config import Settings, get_settings

configure_logging()
logger = structlog.get_logger()


def build_mcp(settings: Settings | None = None) -> FastMCP:
    """Build the restricted MCP server for grocery planning and list staging."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncIterator[None]:
        if resolved.heb_default_store:
            StateManager.set_default_store_id_sync(resolved.heb_default_store)
        if resolved.heb_default_shopping_list:
            StateManager.set_default_shopping_list_name_sync(resolved.heb_default_shopping_list)
        logger.info(
            "Planner-safe HEB MCP started",
            store_id=resolved.heb_default_store,
            write_scope=resolved.heb_write_scope,
        )
        yield
        logger.info("Planner-safe HEB MCP stopped")

    instructions = f"""
## H-E-B Planner MCP

This server is intentionally limited to planning and reviewed shopping-list staging.

- Write scope: `{resolved.heb_write_scope}`.
- Login credentials are never requested or stored by this server. Capture a manual login
  from the dedicated real-Chrome profile with `scripts/capture_session.py`.
- Shopping-list additions require `confirm=true` and are only exposed when
  `HEB_WRITE_SCOPE=shopping-list`.
- Cart additions, item removal, coupon clipping, store mutation, checkout, cancellation,
  payment, and account-management tools are not exposed.
- Use `session_status` first. A stale or expired session requires another explicit
  capture from the dedicated real browser; this server never launches login automation.
"""

    server = FastMCP(
        name="heb-planner-mcp",
        version=__version__,
        instructions=instructions,
        lifespan=lifespan,
    )

    # Public/read-only discovery.
    server.tool(annotations={"readOnlyHint": True})(store_search)
    server.tool(annotations={"readOnlyHint": True})(store_get_default)
    server.tool(annotations={"readOnlyHint": True})(product_search)
    server.tool(annotations={"readOnlyHint": True})(product_search_batch)
    server.tool(annotations={"readOnlyHint": True})(product_get)
    server.tool(annotations={"readOnlyHint": True})(planner_queue_get)

    # Authenticated reads.
    server.tool(annotations={"readOnlyHint": True})(shopping_list_check_auth)
    server.tool(annotations={"readOnlyHint": True})(shopping_list_get)
    server.tool(annotations={"readOnlyHint": True})(cart_check_auth)
    server.tool(annotations={"readOnlyHint": True})(cart_get)
    server.tool(annotations={"readOnlyHint": True})(coupon_list)
    server.tool(annotations={"readOnlyHint": True})(coupon_search)
    server.tool(annotations={"readOnlyHint": True})(coupon_categories)
    server.tool(annotations={"readOnlyHint": True})(coupon_clipped)

    # Add-only list staging is opt-in. No remove/delete tool is ever registered here.
    if resolved.heb_write_scope == "shopping-list":
        server.tool(annotations={"destructiveHint": True})(shopping_list_add)
        server.tool(annotations={"destructiveHint": True})(shopping_list_add_many)

    # Local session state and diagnostics. Browser-launch, credential, and delete tools are omitted.
    server.tool(annotations={"readOnlyHint": True})(session_status)
    server.tool(annotations={"readOnlyHint": True})(health_live)
    server.tool(annotations={"readOnlyHint": True})(health_ready)

    return server


mcp = build_mcp()


def main() -> None:
    """Run the planner-safe MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
