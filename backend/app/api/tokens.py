"""
Internal API token management endpoints for the LogShed Web UI.
Enables listing, creating, editing (name and scopes), and revoking API tokens under administrator authentication.
"""

import datetime
import json
import logging
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.api.ingest import forget_ingest_token, refresh_ingest_token
from app.api.mcp import on_token_revoked as close_mcp_sessions_for_token
from app.api.mcp import on_token_updated as refresh_mcp_sessions_for_token
from app.core.api_tokens import (
    api_token_rate_limiter,
    forget_last_used,
    format_masked_identifier,
    generate_raw_token,
    hash_token,
)
from app.models import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenListItem,
    ApiTokenUpdateRequest,
    MessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tokens", tags=["API Tokens"])

_TOKEN_COLUMNS = "id, name, token_prefix, scopes, created_at, expires_at, last_used_at, created_by"


def _row_to_item(row: sqlite3.Row) -> ApiTokenListItem:
    scopes_val = row["scopes"]
    try:
        scopes_list = json.loads(scopes_val) if isinstance(scopes_val, str) else scopes_val
    except Exception:
        scopes_list = []
    return ApiTokenListItem(
        id=row["id"],
        name=row["name"],
        token_prefix=row["token_prefix"],
        scopes=scopes_list,
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        last_used_at=row["last_used_at"],
        created_by=row["created_by"] or "admin",
    )


@router.get("", response_model=list[ApiTokenListItem])
async def list_tokens(user: dict[str, Any] = Depends(get_current_user)) -> list[ApiTokenListItem]:
    """Retrieve all API tokens for management in the administration interface."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(f"SELECT {_TOKEN_COLUMNS} FROM api_tokens ORDER BY created_at DESC, id DESC")
        return [_row_to_item(r) for r in cur.fetchall()]

    return await run_db_query(_query)


@router.post("", response_model=ApiTokenCreateResponse)
async def create_token(
    payload: ApiTokenCreateRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiTokenCreateResponse:
    """Generate a new API token with the specified scopes and optional expiration."""
    raw_token = generate_raw_token()
    token_hash_val = hash_token(raw_token)
    token_prefix_val = format_masked_identifier(raw_token)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_utc.isoformat()
    expires_iso = None
    if payload.expires_days:
        expires_iso = (now_utc + datetime.timedelta(days=payload.expires_days)).isoformat()

    scopes_json = json.dumps(payload.scopes)
    created_by_val = user.get("username") or user.get("sub") or "admin"

    def _insert(conn):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO api_tokens (name, token_hash, token_prefix, scopes, created_at, expires_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                token_hash_val,
                token_prefix_val,
                scopes_json,
                now_iso,
                expires_iso,
                created_by_val,
            ),
        )
        conn.commit()
        return cur.lastrowid

    token_id = await run_db_query(_insert)

    token_item = ApiTokenListItem(
        id=token_id,
        name=payload.name,
        token_prefix=token_prefix_val,
        scopes=payload.scopes,
        created_at=now_iso,
        expires_at=expires_iso,
        last_used_at=None,
        created_by=created_by_val,
    )
    return ApiTokenCreateResponse(token=token_item, raw_token=raw_token)


@router.patch("/{token_id}", response_model=ApiTokenListItem)
async def update_token(
    token_id: int,
    payload: ApiTokenUpdateRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> ApiTokenListItem:
    """
    Rename an API token, change its scopes, or both, keeping its secret, expiry and usage history.
    The change applies to the token's next request; nothing is written when no field differs.
    """
    def _update(conn) -> Optional[tuple[ApiTokenListItem, ApiTokenListItem]]:
        # The write lock is taken before reading, so the comparison and the UPDATE see the same row.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT {_TOKEN_COLUMNS} FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
        if row is None:
            return None
        before = _row_to_item(row)
        after = before.model_copy(
            update={
                "name": payload.name if payload.name is not None else before.name,
                "scopes": payload.scopes if payload.scopes is not None else before.scopes,
            }
        )
        if after.name != before.name or after.scopes != before.scopes:
            conn.execute(
                "UPDATE api_tokens SET name = ?, scopes = ? WHERE id = ?",
                (after.name, json.dumps(after.scopes), token_id),
            )
            conn.commit()
        return before, after

    result = await run_db_query(_update)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API token {token_id} not found.",
        )
    before, after = result
    renamed = after.name != before.name
    rescoped = after.scopes != before.scopes
    if not (renamed or rescoped):
        return after

    if renamed:
        logger.info("API token %d renamed from %r to %r", token_id, before.name, after.name)
    if rescoped:
        logger.info(
            "API token %d (%r) scopes changed from %s to %s", token_id, after.name, before.scopes, after.scopes
        )

    # Cached copies of the token record pick up the change on the next request. The rate limit
    # windows and last-used throttle are left alone: this is the same token, not a new one.
    refresh_ingest_token(token_id)
    refresh_mcp_sessions_for_token(token_id, after.scopes if rescoped else None)
    return after


@router.delete("/{token_id}", response_model=MessageResponse)
async def revoke_token(
    token_id: int,
    user: dict[str, Any] = Depends(get_current_user),
) -> MessageResponse:
    """Revoke and permanently delete an API token."""
    def _delete(conn):
        cur = conn.cursor()
        cur.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
        conn.commit()
        return cur.rowcount > 0

    deleted = await run_db_query(_delete)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API token {token_id} not found.",
        )

    # Purge in-memory per-token state so a revoked token leaves nothing behind (and cannot be served from cache).
    api_token_rate_limiter.forget(f"token_{token_id}")
    forget_last_used(token_id)
    forget_ingest_token(token_id)
    # Legacy MCP SSE sessions opened with this token are closed instead of living on by session id.
    close_mcp_sessions_for_token(token_id)
    return MessageResponse(status="ok")
