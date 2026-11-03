"""
Export, import and reorder steps shared by the alert rule and drop rule routers.

Each router keeps its own routes, response models and per-field import checks; these helpers hold
the parts that only differ by table, bundle key and the engine to reload.
"""

import asyncio
import json
import sqlite3
from typing import Any, Callable, Optional

from fastapi import HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import run_db_query


def export_response(export: BaseModel) -> Response:
    """An export bundle or single-rule export as an indented JSON download."""
    return Response(
        content=json.dumps(export.model_dump(), indent=2),
        media_type="application/json",
    )


def import_items(payload: Any, bundle_key: str, single_key: str) -> Optional[list[Any]]:
    """
    The rule objects in an import payload: an export bundle ({bundle_key: [...]}), a single-rule export
    ({single_key: {...}}), one bare rule object or a list of them. None when the payload is none of these.
    """
    if isinstance(payload, dict):
        if bundle_key in payload and isinstance(payload[bundle_key], list):
            return payload[bundle_key]
        if single_key in payload and isinstance(payload[single_key], dict):
            return [payload[single_key]]
        return [payload]
    if isinstance(payload, list):
        return payload
    return None


INVALID_IMPORT_PAYLOAD = "Invalid payload format: expected JSON object or array."


def next_display_order(conn: sqlite3.Connection, table: str) -> int:
    """The display order that puts a new rule after every existing rule in table."""
    return conn.execute(f"SELECT COALESCE(MAX(display_order), 0) + 1 FROM {table}").fetchone()[0]


async def reorder_rules(table: str, label: str, rule_ids: list[int], reload: Callable[[], Any]) -> None:
    """
    Store rule_ids as the new order (1, 2, 3, ...) of every rule in table, then reload the engine off the
    event loop. The list must name each rule once: duplicates or a missing or unknown id give 400.
    """
    if len(rule_ids) != len(set(rule_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate rule IDs in reorder request",
        )

    def _reorder(conn: sqlite3.Connection) -> None:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table}")
        existing_ids = {r[0] for r in cur.fetchall()}
        if set(rule_ids) != existing_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Reorder payload must match all configured {label} IDs",
            )

        for order_idx, r_id in enumerate(rule_ids, start=1):
            cur.execute(f"UPDATE {table} SET display_order = ? WHERE id = ?", (order_idx, r_id))
        conn.commit()

    await run_db_query(_reorder)
    await asyncio.to_thread(reload)
