"""Tests for the read-only planner import-pack tool."""

import json
from unittest.mock import patch

import pytest

from texas_grocery_mcp.utils.config import Settings


@pytest.mark.asyncio
async def test_planner_queue_get_filters_ready_rows(tmp_path):
    from texas_grocery_mcp.tools.planner import planner_queue_get

    pack = tmp_path / "pack.json"
    pack.write_text(
        json.dumps(
            [
                {
                    "queue_id": "Q001",
                    "item": "eggs",
                    "quantity": "1",
                    "unit": "dozen",
                    "import_group": "ready",
                    "search_query": "eggs",
                    "estimated_cost": "3.50",
                },
                {
                    "queue_id": "Q002",
                    "item": "cheese",
                    "quantity": "1",
                    "unit": "package",
                    "import_group": "review_first",
                    "search_query": "cheese",
                    "estimated_cost": "",
                },
            ]
        ),
        encoding="utf-8",
    )

    with patch(
        "texas_grocery_mcp.tools.planner.get_settings",
        return_value=Settings(planner_import_pack_path=pack),
    ):
        result = await planner_queue_get(group="ready")

    assert result["configured"] is True
    assert result["summary"] == {
        "total_rows": 2,
        "ready": 1,
        "review_first": 1,
        "returned": 1,
        "estimated_total": 3.5,
    }
    assert result["items"][0]["queue_id"] == "Q001"
    assert result["items"][0]["item"] == "eggs"


@pytest.mark.asyncio
async def test_planner_queue_get_does_not_accept_caller_path():
    from texas_grocery_mcp.tools.planner import planner_queue_get

    with patch(
        "texas_grocery_mcp.tools.planner.get_settings",
        return_value=Settings(planner_import_pack_path=None),
    ):
        result = await planner_queue_get()

    assert result["configured"] is False
    assert result["items"] == []
