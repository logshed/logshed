"""
Internal API token management endpoints for the LogShed Web UI.
Enables listing, creating, and revoking API tokens under administrator authentication.
"""

import datetime
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.core.api_tokens import (
    format_masked_identifier,
    generate_raw_token,
    hash_token,
)
from app.models import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenListItem,
    MessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tokens", tags=["API Tokens"])


@router.get("", response_model=list[ApiTokenListItem])
async def list_tokens(user: dict[str, Any] = Depends(get_current_user)) -> list[ApiTokenListItem]:
    """Retrieve all API tokens for management in the administration interface."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, token_prefix, scopes, created_at, expires_at, last_used_at, created_by
            FROM api_tokens
            ORDER BY created_at DESC, id DESC
            """
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            scopes_val = r["scopes"]
            try:
                scopes_list = json.loads(scopes_val) if isinstance(scopes_val, str) else scopes_val
            except Exception:
                scopes_list = []
            result.append(
                ApiTokenListItem(
                    id=r["id"],
                    name=r["name"],
                    token_prefix=r["token_prefix"],
                    scopes=scopes_list,
                    created_at=r["created_at"],
                    expires_at=r["expires_at"],
                    last_used_at=r["last_used_at"],
                    created_by=r["created_by"] or "admin",
                )
            )
        return result

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
                payload.name.strip(),
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
        name=payload.name.strip(),
        token_prefix=token_prefix_val,
        scopes=payload.scopes,
        created_at=now_iso,
        expires_at=expires_iso,
        last_used_at=None,
        created_by=created_by_val,
    )
    return ApiTokenCreateResponse(token=token_item, raw_token=raw_token)


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
    return MessageResponse(status="ok")
