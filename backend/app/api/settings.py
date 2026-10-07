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
    resolve_all_system_settings,
    invalidate_settings_cache,
    get_cached_setting,
)
from app.core.security import decrypt_value, encrypt_value, mask_secret
from app.core.utils import parse_iso_to_utc_datetime

from app.models import MessageResponse, SettingsResponse, SettingsUpdateRequest, AiProviderConfig
from app.services.ai_engine import DEFAULT_SYSTEM_PROMPT

router = APIRouter(prefix="/settings", tags=["Settings"])

SENSITIVE_KEYS = {
    "ai_api_key",
    "ai_api_key_gemini",
    "ai_api_key_openai",
    "ai_api_key_anthropic",
    "ai_api_key_openai_compatible",
}

DEFAULT_AI_MODELS: dict[str, str] = {
    "gemini": DEFAULT_AI_MODEL,
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
    "openai_compatible": "llama3.2",
}


@router.get("", response_model=SettingsResponse)
async def get_settings(user: dict = Depends(get_current_user)) -> SettingsResponse:
    """
    Retrieve application configuration.
    Sensitive secrets (API keys) are masked with '********' and never returned decrypted.
    """
    stored = await run_db_query(get_all_system_settings)

    active_provider = (stored.get("ai_provider") or "gemini").lower()

    providers_config: dict[str, AiProviderConfig] = {}
    for p in ("gemini", "openai", "anthropic", "openai_compatible"):
        pkey = stored.get(f"ai_api_key_{p}") or (stored.get("ai_api_key", "") if p == active_provider else "")
        pmodel = stored.get(f"ai_model_{p}") or (stored.get("ai_model") if p == active_provider else "") or DEFAULT_AI_MODELS.get(p, DEFAULT_AI_MODEL)
        pfallbacks = stored.get(f"ai_fallback_models_{p}") or (stored.get("ai_fallback_models", "") if p == active_provider else "")
        pbase = stored.get(f"ai_base_url_{p}") or (stored.get("ai_base_url") if p == active_provider else None)
        has_pkey = bool(pkey.strip()) if p != "openai_compatible" else True
        providers_config[p] = AiProviderConfig(
            has_api_key=has_pkey,
            ai_api_key=mask_secret(pkey) if pkey else "",
            ai_model=pmodel,
            ai_fallback_models=pfallbacks,
            ai_base_url=pbase,
        )

    active_config = providers_config.get(active_provider) or providers_config["gemini"]
    has_active_key = active_config.has_api_key
    ai_api_key_val = stored.get(f"ai_api_key_{active_provider}") or (stored.get("ai_api_key", "") if active_provider == (stored.get("ai_provider") or "gemini").lower() else "")

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
        dt = parse_iso_to_utc_datetime(stored_until)
        if dt and datetime.datetime.now(datetime.timezone.utc) < dt:
            maintenance_until = stored_until.strip()

    resolved = resolve_all_system_settings(stored)

    stored_ai_enabled = stored.get("ai_enabled")
    if stored_ai_enabled is not None:
        ai_enabled = stored_ai_enabled.strip().lower() not in ("0", "false", "no", "off")
    else:
        ai_enabled = bool(ai_api_key_val) if active_provider != "openai_compatible" else True

    daily_digest_enabled_raw = stored.get("daily_digest_enabled")
    daily_digest_enabled = False
    if daily_digest_enabled_raw is not None:
        daily_digest_enabled = daily_digest_enabled_raw.strip().lower() in ("1", "true", "yes", "on")

    daily_digest_channel_id_raw = stored.get("daily_digest_channel_id")
    daily_digest_channel_id = None
    if daily_digest_channel_id_raw and daily_digest_channel_id_raw.strip():
        try:
            daily_digest_channel_id = int(daily_digest_channel_id_raw.strip())
        except ValueError:
            daily_digest_channel_id = None

    daily_digest_schedule_time = stored.get("daily_digest_schedule_time") or "09:00"
    daily_digest_last_run = stored.get("daily_digest_last_run") or None

    return SettingsResponse(
        ai_enabled=ai_enabled,
        ai_provider=active_provider,
        ai_model=active_config.ai_model or DEFAULT_AI_MODELS.get(active_provider, DEFAULT_AI_MODEL),
        ai_fallback_models=active_config.ai_fallback_models or "",
        ai_api_key=mask_secret(ai_api_key_val) if ai_api_key_val else "",
        ai_base_url=active_config.ai_base_url,
        ai_system_prompt=stored.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT,
        ai_providers_config=providers_config,
        retention_days=retention_days,
        max_retention_days=max_days,
        retention_overridden=retention_overridden,
        has_ai_api_key=has_active_key,
        internal_log_level=internal_log_level,
        check_for_updates=check_for_updates,
        maintenance_until=maintenance_until,
        ai_timeout=resolved["ai_timeout"],
        ai_thinking_budget=resolved["ai_thinking_budget"],
        app_url=resolved["app_url"],
        allow_private_notification_targets=resolved["allow_private_notification_targets"],
        enable_docker=resolved["enable_docker"],
        docker_exclude_containers=resolved["docker_exclude_containers"],
        docker_source_alias=resolved["docker_source_alias"],
        trusted_proxies=resolved["trusted_proxies"],
        trust_docker_proxies=resolved["trust_docker_proxies"],
        cookie_secure=resolved["cookie_secure"],
        syslog_max_tcp_connections=resolved["syslog_max_tcp_connections"],
        syslog_tcp_inactivity_timeout=resolved["syslog_tcp_inactivity_timeout"],
        daily_digest_enabled=daily_digest_enabled,
        daily_digest_channel_id=daily_digest_channel_id,
        daily_digest_schedule_time=daily_digest_schedule_time,
        daily_digest_last_run=daily_digest_last_run,
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

        cursor.execute("SELECT value FROM system_settings WHERE key = 'ai_provider'")
        current_db_provider_row = cursor.fetchone()
        current_db_provider = current_db_provider_row[0] if current_db_provider_row else "gemini"
        target_provider = (req.ai_provider or current_db_provider or "gemini").lower()

        if req.ai_enabled is not None:
            updates.append(("ai_enabled", "1" if req.ai_enabled else "0", 0))

        if req.ai_provider is not None:
            updates.append(("ai_provider", target_provider, 0))

        if req.ai_model is not None:
            updates.append(("ai_model", req.ai_model, 0))
            updates.append((f"ai_model_{target_provider}", req.ai_model, 0))

        if req.ai_fallback_models is not None:
            updates.append(("ai_fallback_models", req.ai_fallback_models, 0))
            updates.append((f"ai_fallback_models_{target_provider}", req.ai_fallback_models, 0))

        if req.ai_base_url is not None:
            updates.append(("ai_base_url", req.ai_base_url, 0))
            updates.append((f"ai_base_url_{target_provider}", req.ai_base_url, 0))

        if req.ai_system_prompt is not None:
            updates.append(("ai_system_prompt", req.ai_system_prompt, 0))

        if req.ai_providers_config:
            for p, p_cfg in req.ai_providers_config.items():
                p_clean = (p or "").lower().strip()
                if not p_clean:
                    continue
                if p_cfg.ai_model is not None:
                    updates.append((f"ai_model_{p_clean}", p_cfg.ai_model, 0))
                    if p_clean == target_provider and req.ai_model is None:
                        updates.append(("ai_model", p_cfg.ai_model, 0))
                if p_cfg.ai_fallback_models is not None:
                    updates.append((f"ai_fallback_models_{p_clean}", p_cfg.ai_fallback_models, 0))
                    if p_clean == target_provider and req.ai_fallback_models is None:
                        updates.append(("ai_fallback_models", p_cfg.ai_fallback_models, 0))
                if p_cfg.ai_base_url is not None:
                    updates.append((f"ai_base_url_{p_clean}", p_cfg.ai_base_url, 0))
                    if p_clean == target_provider and req.ai_base_url is None:
                        updates.append(("ai_base_url", p_cfg.ai_base_url, 0))
                if p_cfg.ai_api_key is not None:
                    if p_cfg.ai_api_key == "********":
                        pass
                    elif p_cfg.ai_api_key == "":
                        updates.append((f"ai_api_key_{p_clean}", "", 1))
                        if p_clean == target_provider and req.ai_api_key is None:
                            updates.append(("ai_api_key", "", 1))
                    else:
                        enc = encrypt_value(p_cfg.ai_api_key)
                        updates.append((f"ai_api_key_{p_clean}", enc, 1))
                        if p_clean == target_provider and req.ai_api_key is None:
                            updates.append(("ai_api_key", enc, 1))

        if req.retention_days is not None:
            updates.append(("retention_days", str(req.retention_days), 0))

        if req.internal_log_level is not None:
            updates.append(("internal_log_level", req.internal_log_level, 0))

        if req.check_for_updates is not None:
            updates.append(("check_for_updates", "1" if req.check_for_updates else "0", 0))

        # Advanced System Settings
        if req.ai_timeout is not None:
            updates.append(("ai_timeout", str(req.ai_timeout), 0))

        if req.ai_thinking_budget is not None:
            updates.append(("ai_thinking_budget", str(req.ai_thinking_budget), 0))

        if req.app_url is not None:
            updates.append(("app_url", req.app_url, 0))

        if req.allow_private_notification_targets is not None:
            updates.append(("allow_private_notification_targets", "1" if req.allow_private_notification_targets else "0", 0))

        if req.enable_docker is not None:
            updates.append(("enable_docker", "1" if req.enable_docker else "0", 0))

        if req.docker_exclude_containers is not None:
            updates.append(("docker_exclude_containers", req.docker_exclude_containers, 0))

        if req.docker_source_alias is not None:
            updates.append(("docker_source_alias", req.docker_source_alias, 0))

        if req.trusted_proxies is not None:
            updates.append(("trusted_proxies", req.trusted_proxies, 0))

        if req.trust_docker_proxies is not None:
            updates.append(("trust_docker_proxies", "1" if req.trust_docker_proxies else "0", 0))

        if req.cookie_secure is not None:
            updates.append(("cookie_secure", "1" if req.cookie_secure else "0", 0))

        if req.syslog_max_tcp_connections is not None:
            updates.append(("syslog_max_tcp_connections", str(req.syslog_max_tcp_connections), 0))

        if req.syslog_tcp_inactivity_timeout is not None:
            updates.append(("syslog_tcp_inactivity_timeout", str(req.syslog_tcp_inactivity_timeout), 0))

        if req.daily_digest_enabled is not None:
            updates.append(("daily_digest_enabled", "1" if req.daily_digest_enabled else "0", 0))

        if "daily_digest_channel_id" in req.model_fields_set:
            val = "" if req.daily_digest_channel_id is None else str(req.daily_digest_channel_id)
            updates.append(("daily_digest_channel_id", val, 0))

        if req.daily_digest_schedule_time is not None:
            updates.append(("daily_digest_schedule_time", req.daily_digest_schedule_time.strip(), 0))

        if "maintenance_until" in req.model_fields_set:
            if req.maintenance_until is None or req.maintenance_until.strip() == "":
                updates.append(("maintenance_until", "", 0))
            else:
                dt = parse_iso_to_utc_datetime(req.maintenance_until)
                if dt is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Invalid ISO 8601 datetime format for maintenance_until: {req.maintenance_until}",
                    )
                stored_iso = dt.isoformat()
                updates.append(("maintenance_until", stored_iso, 0))

        # Handle sensitive fields
        if req.ai_api_key is not None:
            if req.ai_api_key == "********":
                pass
            elif req.ai_api_key == "":
                updates.append(("ai_api_key", "", 1))
                updates.append((f"ai_api_key_{target_provider}", "", 1))
            else:
                encrypted = encrypt_value(req.ai_api_key)
                updates.append(("ai_api_key", encrypted, 1))
                updates.append((f"ai_api_key_{target_provider}", encrypted, 1))

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

    # Invalidate in-memory cached system settings
    invalidate_settings_cache()

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

    # Dynamic worker dispatch
    if req.syslog_max_tcp_connections is not None or req.syslog_tcp_inactivity_timeout is not None:
        try:
            from app.main import get_syslog_server
            syslog_server = get_syslog_server()
            if syslog_server is not None:
                max_tcp = int(get_cached_setting("syslog_max_tcp_connections", 250)) if req.syslog_max_tcp_connections is not None else None
                inact_to = float(get_cached_setting("syslog_tcp_inactivity_timeout", 0.0)) if req.syslog_tcp_inactivity_timeout is not None else None
                syslog_server.update_limits(max_connections=max_tcp, inactivity_timeout=inact_to)
        except Exception:
            pass

    if (
        req.enable_docker is not None
        or req.docker_exclude_containers is not None
        or req.docker_source_alias is not None
    ):
        try:
            from app.main import get_docker_tailer
            docker_tailer = get_docker_tailer()
            if docker_tailer is not None:
                en_docker = bool(get_cached_setting("enable_docker", True)) if req.enable_docker is not None else None
                excl = str(get_cached_setting("docker_exclude_containers", "")) if req.docker_exclude_containers is not None else None
                alias = str(get_cached_setting("docker_source_alias", "docker")) if req.docker_source_alias is not None else None
                await docker_tailer.update_settings(
                    enable_docker=en_docker,
                    exclude_containers=excl,
                    source_alias=alias,
                )
        except Exception:
            pass

    return MessageResponse(status="ok")
