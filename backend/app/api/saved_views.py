"""
Saved views management API endpoints.

Allows creating, listing, updating, pinning, and deleting custom search/filter views.
"""

import datetime
import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.models import (
    MessageResponse,
    SavedViewCreate,
    SavedViewResponse,
    SavedViewUpdate,
)

router = APIRouter(prefix="/saved-views", tags=["Saved Views"])


def _parse_query_params(raw: str) -> dict[str, Any]:
    """Safely deserialize query_params JSON string into a dictionary."""
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@router.get("", response_model=list[SavedViewResponse])
async def list_saved_views(user: dict = Depends(get_current_user)) -> list[SavedViewResponse]:
    """List all saved views ordered with pinned views first, then alphabetically by name."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, query_params, is_pinned, created_at FROM saved_views "
            "ORDER BY is_pinned DESC, name ASC"
        )
        rows = cur.fetchall()
        return [
            SavedViewResponse(
                id=r["id"],
                name=r["name"],
                query_params=_parse_query_params(r["query_params"]),
                is_pinned=bool(r["is_pinned"]),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    return await run_db_query(_query)


@router.post("", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
async def create_saved_view(
    payload: SavedViewCreate,
    user: dict = Depends(get_current_user),
) -> SavedViewResponse:
    """Create a new saved filter view."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    serialized_params = json.dumps(payload.query_params)

    def _insert(conn):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO saved_views (name, query_params, is_pinned, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (payload.name.strip(), serialized_params, 1 if payload.is_pinned else 0, now_iso),
        )
        conn.commit()
        view_id = cur.lastrowid
        return SavedViewResponse(
            id=view_id,
            name=payload.name.strip(),
            query_params=payload.query_params,
            is_pinned=payload.is_pinned,
            created_at=now_iso,
        )

    return await run_db_query(_insert)


@router.put("/{view_id}", response_model=SavedViewResponse)
async def update_saved_view(
    view_id: int,
    payload: SavedViewUpdate,
    user: dict = Depends(get_current_user),
) -> SavedViewResponse:
    """Update name, filter parameters, or pinned status of a saved view."""
    def _update(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, query_params, is_pinned, created_at FROM saved_views WHERE id = ?",
            (view_id,),
        )
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved view not found.")

        new_name = payload.name.strip() if payload.name is not None else existing["name"]
        new_params = (
            json.dumps(payload.query_params)
            if payload.query_params is not None
            else existing["query_params"]
        )
        new_is_pinned = (
            payload.is_pinned
            if payload.is_pinned is not None
            else bool(existing["is_pinned"])
        )

        cur.execute(
            """
            UPDATE saved_views SET
                name = ?,
                query_params = ?,
                is_pinned = ?
            WHERE id = ?
            """,
            (new_name, new_params, 1 if new_is_pinned else 0, view_id),
        )
        conn.commit()

        return SavedViewResponse(
            id=view_id,
            name=new_name,
            query_params=_parse_query_params(new_params),
            is_pinned=new_is_pinned,
            created_at=str(existing["created_at"]),
        )

    return await run_db_query(_update)


@router.delete("/{view_id}", response_model=MessageResponse)
async def delete_saved_view(
    view_id: int,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete a saved view."""
    def _delete(conn):
        cur = conn.cursor()
        cur.execute("SELECT id FROM saved_views WHERE id = ?", (view_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved view not found.")
        cur.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
        conn.commit()

    await run_db_query(_delete)
    return MessageResponse(status="ok", detail=f"Saved view {view_id} deleted successfully.")
