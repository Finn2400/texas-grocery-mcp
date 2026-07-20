"""Read the reviewed queue produced by the companion grocery planner."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from texas_grocery_mcp.utils.config import get_settings

QueueGroup = Literal["ready", "review_first", "all"]

EXPOSED_FIELDS = (
    "queue_id",
    "item",
    "quantity",
    "unit",
    "category",
    "import_group",
    "list_text",
    "search_query",
    "confidence",
    "estimated_cost",
    "warnings",
    "source",
)


def _read_pack(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("Import pack must be a JSON array of objects.")
    return payload


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in EXPOSED_FIELDS}


def _estimated_total(rows: list[dict[str, Any]]) -> float:
    total = 0.0
    for row in rows:
        raw = row.get("estimated_cost")
        if raw in (None, ""):
            continue
        try:
            total += float(raw)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


async def planner_queue_get(
    group: Annotated[
        QueueGroup,
        Field(description="Return ready items, review-first items, or the full queue."),
    ] = "ready",
    limit: Annotated[
        int,
        Field(description="Maximum queue rows to return.", ge=1, le=100),
    ] = 100,
) -> dict[str, Any]:
    """Read reviewed grocery rows from the configured local planner import pack.

    This tool is read-only. The path is set by `PLANNER_IMPORT_PACK_PATH`; callers
    cannot provide arbitrary filesystem paths.
    """
    configured_path = get_settings().planner_import_pack_path
    if configured_path is None:
        return {
            "configured": False,
            "items": [],
            "message": "Set PLANNER_IMPORT_PACK_PATH to a reviewed planner JSON pack.",
        }

    path = configured_path.expanduser()
    if not path.is_file():
        return {
            "configured": True,
            "error": True,
            "code": "PLANNER_PACK_NOT_FOUND",
            "items": [],
            "message": "The configured planner import pack does not exist.",
        }

    try:
        rows = _read_pack(path)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return {
            "configured": True,
            "error": True,
            "code": "PLANNER_PACK_INVALID",
            "items": [],
            "message": f"Could not read the planner import pack: {error}",
        }

    counts = Counter(str(row.get("import_group", "unknown")) for row in rows)
    selected = rows if group == "all" else [row for row in rows if row.get("import_group") == group]
    selected = selected[:limit]
    public_rows = [_public_row(row) for row in selected]

    return {
        "configured": True,
        "group": group,
        "items": public_rows,
        "summary": {
            "total_rows": len(rows),
            "ready": counts.get("ready", 0),
            "review_first": counts.get("review_first", 0),
            "returned": len(public_rows),
            "estimated_total": _estimated_total(public_rows),
        },
        "message": f"Loaded {len(public_rows)} '{group}' planner queue item(s).",
    }
