"""
System settings API endpoints for LogShed.
Provides secure storage with encryption at rest for API keys.
"""

import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.core.config import (
    DEFAULT_AI_MODEL,
    get_all_system_settings,
    get_max_retention_days,
    is_max_retention_days_overridden,
    get_internal_log_level,
    get_internal_log_level_name,
)
from app.core.security import decrypt_value, encrypt_value, mask_secret

from app.models import MessageResponse, SettingsResponse, SettingsUpdateRequest
from app.services.ai_engine import DEFAULT_SYSTEM_PROMPT

router = APIRouter(prefix="/settings", tags=["Settings"])

SENSITIVE_KEYS = {"ai_api_key"}


@router.get("", response_model=SettingsResponse)
async def get_settings(user: dict = Depends(get_current_user)) -> SettingsResponse:
    """
    Retrieve application configuration.
    Sensitive secrets (API keys) are masked with '********' and never returned decrypted.
    """
    stored = await run_db_query(get_all_system_settings)

    ai_api_key_val = stored.get("ai_api_key", "")

    retention_raw = stored.get("retention_days", "14")
    try:
        retention_days = int(retention_raw)
    except ValueError:
        retention_days = 14

    max_days = get_max_retention_days()
    retention_overridden = is_max_retention_days_overridden()
    if retention_overridden:
        retention_days = max_days
        if stored.get("retention_days") != str(max_days):
            def _persist_override(conn):
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO system_settings (key, value, is_encrypted, updated_at)
                    VALUES ('retention_days', ?, 0, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (str(max_days), now),
                )
                conn.commit()

            await run_db_query(_persist_override)
    else:
        clamped_days = max(1, min(retention_days, max_days))
        if retention_days > max_days:
            def _persist_clamped(conn):
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO system_settings (key, value, is_encrypted, updated_at)
                    VALUES ('retention_days', ?, 0, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (str(clamped_days), now),
                )
                conn.commit()

            await run_db_query(_persist_clamped)
        retention_days = clamped_days

    env_level = get_internal_log_level()
    env_level_name = get_internal_log_level_name(env_level)
    stored_level = stored.get("internal_log_level")
    if stored_level:
        from app.core.config import to_canonical_log_level_name
        internal_log_level = to_canonical_log_level_name(stored_level)
    else:
        internal_log_level = env_level_name

    check_for_updates_raw = stored.get("check_for_updates")
    check_for_updates = True
    if check_for_updates_raw is not None:
        check_for_updates = check_for_updates_raw.strip().lower() not in ("0", "false", "no", "off")

    stored_until = stored.get("maintenance_until")
    maintenance_until = None
    if stored_until and stored_until.strip():
        try:
            dt = datetime.datetime.fromisoformat(stored_until.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            if datetime.datetime.now(datetime.timezone.utc) < dt:
                maintenance_until = stored_until.strip()
        except Exception:
            maintenance_until = None

    return SettingsResponse(
        ai_provider=stored.get("ai_provider") or "gemini",
        ai_model=stored.get("ai_model") or DEFAULT_AI_MODEL,
        ai_fallback_models=stored.get("ai_fallback_models") or "",
        ai_api_key=mask_secret(ai_api_key_val),
        ai_base_url=stored.get("ai_base_url") or None,
        ai_system_prompt=stored.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT,
        retention_days=retention_days,
        max_retention_days=max_days,
        retention_overridden=retention_overridden,
        has_ai_api_key=bool(ai_api_key_val),
        internal_log_level=internal_log_level,
        check_for_updates=check_for_updates,
        maintenance_until=maintenance_until,
    )


@router.post("", response_model=MessageResponse)
async def update_settings(
    req: SettingsUpdateRequest,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """
    Update application configuration.
    Sensitive keys are encrypted at rest with Fernet.
    Masked strings ('********') are preserved without overwriting existing secrets.
    """
    if req.retention_days is not None:
        max_days = get_max_retention_days()
        if req.retention_days < 1 or req.retention_days > max_days:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Retention days must be between 1 and {max_days}",
            )

    def _save_settings(conn):

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor = conn.cursor()

        updates: list[tuple[str, str, int]] = []

        if req.ai_provider is not None:
            updates.append(("ai_provider", req.ai_provider, 0))

        if req.ai_model is not None:
            updates.append(("ai_model", req.ai_model, 0))

        if req.ai_fallback_models is not None:
            updates.append(("ai_fallback_models", req.ai_fallback_models, 0))

        if req.ai_base_url is not None:
            updates.append(("ai_base_url", req.ai_base_url, 0))

        if req.ai_system_prompt is not None:
            updates.append(("ai_system_prompt", req.ai_system_prompt, 0))

        if req.retention_days is not None:
            updates.append(("retention_days", str(req.retention_days), 0))

        if req.internal_log_level is not None:
            updates.append(("internal_log_level", req.internal_log_level, 0))

        if req.check_for_updates is not None:
            updates.append(("check_for_updates", "1" if req.check_for_updates else "0", 0))

        if "maintenance_until" in req.model_fields_set:
            if req.maintenance_until is None or req.maintenance_until.strip() == "":
                updates.append(("maintenance_until", "", 0))
            else:
                try:
                    dt = datetime.datetime.fromisoformat(req.maintenance_until.strip().replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    stored_iso = dt.astimezone(datetime.timezone.utc).isoformat()
                    updates.append(("maintenance_until", stored_iso, 0))
                except Exception as e:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Invalid ISO 8601 datetime format for maintenance_until: {e}",
                    )

        # Handle sensitive fields
        for sensitive_key in ("ai_api_key",):
            val = getattr(req, sensitive_key)
            if val is not None:
                # If value is masked placeholder ("********"), do not overwrite existing key
                if val == "********":
                    continue
                elif val == "":
                    # Empty string clears the secret
                    updates.append((sensitive_key, "", 1))
                else:
                    encrypted = encrypt_value(val)
                    updates.append((sensitive_key, encrypted, 1))

        for key, val, is_enc in updates:
            cursor.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at,
                    is_encrypted = excluded.is_encrypted
                """,
                (key, val, now, is_enc),
            )

        conn.commit()

    await run_db_query(_save_settings)

    if req.internal_log_level is not None:
        try:
            from app.main import configure_internal_log_handler
            configure_internal_log_handler(req.internal_log_level)
        except Exception:
            pass

    if req.check_for_updates is not None:
        try:
            from app.services.version_service import clear_version_cache
            clear_version_cache()
        except Exception:
            pass

    return MessageResponse(status="ok")
