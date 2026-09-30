"""
Notification channels and test delivery API endpoints.

Provides endpoints to create, list, update, delete, and test
notification channels configured with universal Apprise URLs.
"""

import asyncio
import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.models import (
    DailyDigestRunResponse,
    MessageResponse,
    NotificationChannelCreate,
    NotificationChannelResponse,
    NotificationChannelUpdate,
    NotificationTestRequest,
    NotificationTestResponse,
)
from app.services.notifier import (
    decrypt_channel_url,
    encrypt_channel_url,
    get_notifier,
    mask_notification_url,
    validate_notification_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/channels", response_model=list[NotificationChannelResponse])
async def list_notification_channels(user: dict = Depends(get_current_user)) -> list[NotificationChannelResponse]:
    """List all configured notification channels with masked URLs."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, url, is_enabled, created_at, updated_at "
            "FROM notification_channels ORDER BY id ASC"
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            channel_id = r["id"] if hasattr(r, "keys") else r[0]
            name = r["name"] if hasattr(r, "keys") else r[1]
            encrypted_url = r["url"] if hasattr(r, "keys") else r[2]
            is_enabled = bool(r["is_enabled"]) if hasattr(r, "keys") else bool(r[3])
            created_at = str(r["created_at"]) if hasattr(r, "keys") else str(r[4])
            updated_at = str(r["updated_at"]) if hasattr(r, "keys") else str(r[5])

            try:
                decrypted = decrypt_channel_url(encrypted_url)
                masked = mask_notification_url(decrypted)
            except Exception:
                masked = "********"

            results.append(
                NotificationChannelResponse(
                    id=channel_id,
                    name=name,
                    url=masked,
                    is_enabled=is_enabled,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        return results

    return await run_db_query(_query)


@router.post("/channels", response_model=NotificationChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_notification_channel(
    channel_in: NotificationChannelCreate,
    user: dict = Depends(get_current_user),
) -> NotificationChannelResponse:
    """Validate URL, encrypt it at rest, and register a new notification channel."""
    # Validate Apprise URL schema
    valid, err_msg = await asyncio.to_thread(validate_notification_url, channel_in.url)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg or "Invalid or unsupported notification URL.",
        )

    encrypted_url = encrypt_channel_url(channel_in.url)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _insert(conn):
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO notification_channels (name, url, is_enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                channel_in.name.strip(),
                encrypted_url,
                int(channel_in.is_enabled),
                now_iso,
                now_iso,
            ),
        )
        channel_id = cur.lastrowid
        conn.commit()
        return channel_id

    channel_id = await run_db_query(_insert)

    return NotificationChannelResponse(
        id=channel_id,
        name=channel_in.name.strip(),
        url=mask_notification_url(channel_in.url),
        is_enabled=channel_in.is_enabled,
        created_at=now_iso,
        updated_at=now_iso,
    )


@router.put("/channels/{channel_id}", response_model=NotificationChannelResponse)
async def update_notification_channel(
    channel_id: int,
    channel_in: NotificationChannelUpdate,
    user: dict = Depends(get_current_user),
) -> NotificationChannelResponse:
    """Update channel name, enabled status, or URL."""
    def _fetch(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, url, is_enabled, created_at, updated_at "
            "FROM notification_channels WHERE id = ?",
            (channel_id,),
        )
        return cur.fetchone()

    existing = await run_db_query(_fetch)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification channel {channel_id} not found.",
        )

    current_name = existing["name"] if hasattr(existing, "keys") else existing[1]
    current_enc_url = existing["url"] if hasattr(existing, "keys") else existing[2]
    current_enabled = bool(existing["is_enabled"]) if hasattr(existing, "keys") else bool(existing[3])
    created_at = str(existing["created_at"]) if hasattr(existing, "keys") else str(existing[4])

    new_name = channel_in.name.strip() if channel_in.name is not None else current_name
    new_enabled = channel_in.is_enabled if channel_in.is_enabled is not None else current_enabled
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Determine whether URL is being updated
    if channel_in.url is not None and channel_in.url.strip() and channel_in.url.strip() != "********":
        valid, err_msg = await asyncio.to_thread(validate_notification_url, channel_in.url)
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err_msg or "Invalid or unsupported notification URL.",
            )
        new_enc_url = encrypt_channel_url(channel_in.url)
        display_url = mask_notification_url(channel_in.url)
    else:
        new_enc_url = current_enc_url
        try:
            display_url = mask_notification_url(decrypt_channel_url(current_enc_url))
        except Exception:
            display_url = "********"

    def _update(conn):
        cur = conn.cursor()
        cur.execute(
            "UPDATE notification_channels SET name = ?, url = ?, is_enabled = ?, updated_at = ? WHERE id = ?",
            (new_name, new_enc_url, int(new_enabled), now_iso, channel_id),
        )
        conn.commit()

    await run_db_query(_update)

    return NotificationChannelResponse(
        id=channel_id,
        name=new_name,
        url=display_url,
        is_enabled=new_enabled,
        created_at=created_at,
        updated_at=now_iso,
    )


@router.delete("/channels/{channel_id}", response_model=MessageResponse)
async def delete_notification_channel(
    channel_id: int,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete a notification channel."""
    def _delete(conn):
        cur = conn.cursor()
        cur.execute("DELETE FROM notification_channels WHERE id = ?", (channel_id,))
        rows_affected = cur.rowcount
        conn.commit()
        return rows_affected

    deleted_count = await run_db_query(_delete)
    if deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification channel {channel_id} not found.",
        )

    return MessageResponse(
        status="ok",
        detail=f"Notification channel {channel_id} deleted successfully.",
    )


@router.post("/test", response_model=NotificationTestResponse)
async def test_notification(
    payload: NotificationTestRequest,
    user: dict = Depends(get_current_user),
) -> NotificationTestResponse:
    """
    Deliver an immediate test notification.
    Accepts either an existing channel_id or an unsaved raw candidate url.
    """
    notifier = get_notifier()

    if payload.channel_id is not None:
        def _get_url(conn):
            cur = conn.cursor()
            cur.execute("SELECT url FROM notification_channels WHERE id = ?", (payload.channel_id,))
            row = cur.fetchone()
            return row["url"] if (row and hasattr(row, "keys")) else (row[0] if row else None)

        encrypted_url = await run_db_query(_get_url)
        if not encrypted_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Notification channel {payload.channel_id} not found.",
            )
        try:
            target_url = decrypt_channel_url(encrypted_url)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to decrypt notification channel URL: {exc}",
            )
    elif payload.url is not None and payload.url.strip():
        target_url = payload.url.strip()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either channel_id or url must be provided for testing.",
        )

    success, message = await notifier.test_channel(target_url)
    return NotificationTestResponse(success=success, message=message)


@router.post("/digest/send", response_model=DailyDigestRunResponse)
async def send_daily_digest(
    user: dict = Depends(get_current_user),
) -> DailyDigestRunResponse:
    """
    Manually trigger generation and delivery of the 24-hour daily digest.
    """
    from app.services.daily_digest import run_daily_digest
    from app.core.config import get_db_path

    db_path = get_db_path()
    res = await run_daily_digest(db_path, force=True)
    return DailyDigestRunResponse(**res)
