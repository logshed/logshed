"""
AI Service Module for LogShed.

Encapsulates database log retrieval, host note aggregation, settings decryption,
shared diagnosis context preparation, model cache management, and audit log persistence.
"""

import datetime
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, status

from app.api.deps import run_db_query
from app.core.config import DEFAULT_AI_MODEL, get_all_system_settings
from app.core.redactor import redact
from app.core.security import decrypt_value
from app.core.utils import parse_iso_to_utc_datetime
from app.services.ai_engine import (
    DEFAULT_SYSTEM_PROMPT,
    build_analysis_prompt,
    format_prompt_log_line,
    truncate_logs_to_budget,
)

logger = logging.getLogger(__name__)


def fetch_and_validate_logs(
    conn: sqlite3.Connection,
    log_ids: list[int],
) -> tuple[Optional[list[sqlite3.Row]], Optional[str]]:
    """
    Fetch logs by IDs from the database and sort chronologically.
    Returns (rows, error_message).
    """
    if len(log_ids) > 200:
        return None, "Maximum of 200 log IDs allowed per request."
    if not log_ids:
        return None, "No logs found for provided IDs."

    placeholders = ",".join("?" * len(log_ids))
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT id, timestamp, source_ip, source_alias, app_name, severity, message, raw
        FROM logs
        WHERE id IN ({placeholders})
        ORDER BY timestamp ASC, id ASC
        """,
        log_ids,
    )
    rows = cursor.fetchall()
    if not rows:
        return None, "No logs found for provided IDs."

    return rows, None


def get_aggregated_host_notes(conn: sqlite3.Connection, rows: list) -> Optional[str]:
    """
    Aggregate host notes from host_aliases matching any source_ip or source_alias in the batch.
    For a single host with notes, returns the direct notes string.
    For multiple hosts with notes, returns structured host-attributed notes.
    """
    unique_ips = list({r["source_ip"] for r in rows if r["source_ip"]})
    unique_aliases = list({r["source_alias"] for r in rows if r["source_alias"]})

    cursor = conn.cursor()
    cursor.execute("SELECT ip, alias, notes FROM host_aliases")
    all_aliases = cursor.fetchall()

    notes_by_host: dict[str, str] = {}
    for a in all_aliases:
        note = (a["notes"] or "").strip()
        if note and (a["ip"] in unique_ips or a["alias"] in unique_aliases):
            key = a["alias"] or a["ip"]
            notes_by_host[key] = note

    if not notes_by_host:
        return None

    # If all selected rows belong to a single host (alias or IP)
    unique_batch_hosts = {r["source_alias"] or r["source_ip"] for r in rows}
    if len(unique_batch_hosts) <= 1 and len(notes_by_host) == 1:
        return list(notes_by_host.values())[0]

    # Multi-host batch: attribute each note to its host
    return "\n".join(f"- [{h}]: {n}" for h, n in sorted(notes_by_host.items()))


def read_ai_settings(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """
    Read all system_settings, automatically decrypting encrypted values.
    Returns (settings_dict, updated_map).
    """
    settings_dict = get_all_system_settings(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT key, updated_at FROM system_settings")
    rows = cursor.fetchall()
    updated_dict = {
        r["key"] if isinstance(r, sqlite3.Row) else r[0]: r["updated_at"] if isinstance(r, sqlite3.Row) else r[1]
        for r in rows
    }
    return settings_dict, updated_dict


def is_cache_fresh(updated_at_str: Optional[str], max_age_seconds: int = 86400) -> bool:
    """Check if cached models or settings are within the specified TTL (default 24h)."""
    dt = parse_iso_to_utc_datetime(updated_at_str)
    if dt is None:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - dt).total_seconds() < max_age_seconds


def save_models_cache(conn: sqlite3.Connection, cache_key: str, models_data: list[dict[str, Any]]) -> str:
    """Persist discovered models cache in SQLite system_settings table."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO system_settings (key, value, updated_at, is_encrypted)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (cache_key, json.dumps(models_data), now_iso),
    )
    conn.commit()
    return now_iso


async def build_diagnosis_context(
    log_ids: list[int],
    user_context: Optional[str] = None,
    prompt_override: Optional[str] = None,
    system_prompt_override: Optional[str] = None,
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
    fallback_models_override: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Shared context preparation routine reused by preview_ai_prompt, diagnose_logs,
    and diagnose_logs_stream.
    Retrieves logs, decrypts configuration, aggregates host notes, redacts sensitive values,
    truncates to budget, and constructs the final prompt payload and token estimation.
    """
    if len(log_ids) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot analyze more than 200 logs at once.",
        )

    def _fetch_all(conn: sqlite3.Connection):
        rows, err = fetch_and_validate_logs(conn, log_ids)
        if err:
            return None, err

        settings_dict, _ = read_ai_settings(conn)
        host_notes = get_aggregated_host_notes(conn, rows)
        return (rows, settings_dict, host_notes), None

    result, err = await run_db_query(_fetch_all)
    if err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err,
        )

    rows, settings, host_notes = result

    unique_aliases = sorted(list({r["source_alias"] for r in rows if r["source_alias"]}))
    source_alias = ", ".join(unique_aliases) if unique_aliases else (rows[0]["source_ip"] or "unknown")

    unique_apps = sorted(list({r["app_name"] for r in rows if r["app_name"]}))
    app_name = ", ".join(unique_apps) if unique_apps else "unknown"

    raw_lines = [
        format_prompt_log_line(
            timestamp=r["timestamp"],
            source=r["source_alias"] or r["source_ip"],
            app_name=r["app_name"],
            message=r["message"],
            severity=r["severity"],
            raw=r["raw"] if "raw" in r.keys() else None,
        )
        for r in rows
    ]
    redacted_lines = redact(raw_lines)
    redacted_logs_text = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)
    redacted_logs_text = truncate_logs_to_budget(redacted_logs_text)

    active_db_provider = (settings.get("ai_provider") or "gemini").lower()
    provider = (provider_override or active_db_provider).lower()
    default_model = DEFAULT_AI_MODEL if provider == "gemini" else ("gpt-4o" if provider == "openai" else ("claude-sonnet-4-6" if provider == "anthropic" else "llama3.2"))
    model = model_override or settings.get(f"ai_model_{provider}") or (settings.get("ai_model") if provider == active_db_provider else None) or default_model
    api_key = (
        settings.get(f"ai_api_key_{provider}")
        or (settings.get("ai_api_key", "") if provider == active_db_provider else "")
    )
    base_url = settings.get(f"ai_base_url_{provider}") or (settings.get("ai_base_url") if provider == active_db_provider else None) or None

    system_prompt = (
        system_prompt_override.strip()
        if (system_prompt_override and system_prompt_override.strip())
        else (settings.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT)
    )

    fallback_models_str = settings.get(f"ai_fallback_models_{provider}") or (settings.get("ai_fallback_models", "") if provider == active_db_provider else "")
    configured_fallbacks = [m.strip() for m in fallback_models_str.split(",") if m.strip()]
    fallback_models = (
        fallback_models_override
        if fallback_models_override is not None
        else configured_fallbacks
    )

    redacted_host_notes = str(redact(host_notes)) if host_notes else None
    redacted_user_context = str(redact(user_context.strip())) if (user_context and user_context.strip()) else None

    if prompt_override and prompt_override.strip():
        full_prompt = str(redact(prompt_override.strip()))
        redacted_prompt_override = full_prompt
    else:
        full_prompt = build_analysis_prompt(
            source_alias=source_alias,
            app_name=app_name,
            redacted_logs=redacted_logs_text,
            log_count=len(rows),
            user_context=redacted_user_context,
            host_notes=redacted_host_notes,
        )
        redacted_prompt_override = None

    # Estimate token count (~3.5 characters per token including system prompt and framing overhead)
    estimated_tokens = max(1, int(len(full_prompt) // 3.5 + len(system_prompt) // 3.5 + 50))

    from app.core.config import get_cached_setting, DEFAULT_AI_TIMEOUT, DEFAULT_AI_THINKING_BUDGET
    ai_timeout = float(get_cached_setting("ai_timeout", DEFAULT_AI_TIMEOUT))
    ai_thinking_budget = int(get_cached_setting("ai_thinking_budget", DEFAULT_AI_THINKING_BUDGET))

    stored_ai_enabled = settings.get("ai_enabled")
    if stored_ai_enabled is not None:
        ai_enabled = stored_ai_enabled.strip().lower() not in ("0", "false", "no", "off")
    else:
        ai_enabled = bool(api_key.strip())
    has_ai_api_key = bool(api_key.strip())

    return {
        "rows": rows,
        "source_alias": source_alias,
        "app_name": app_name,
        "redacted_logs": redacted_logs_text,
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "fallback_models": fallback_models,
        "redacted_user_context": redacted_user_context,
        "redacted_host_notes": redacted_host_notes,
        "redacted_prompt_override": redacted_prompt_override,
        "full_prompt": full_prompt,
        "estimated_tokens": estimated_tokens,
        "ai_timeout": ai_timeout,
        "ai_thinking_budget": ai_thinking_budget,
        "ai_enabled": ai_enabled,
        "has_ai_api_key": has_ai_api_key,
    }


async def save_diagnosis_audit(
    source_alias: str,
    app_name: str,
    log_count: int,
    user_context: Optional[str],
    actual_model: str,
    prompt_sent: str,
    raw_response: str,
    tokens_in: int,
    tokens_out: int,
    tokens_thoughts: int,
    tokens_used: int,
    system_prompt: Optional[str],
    trigger_source: str = "on-demand",
    custom_db_path: Optional[Path] = None,
) -> int:
    """Persist an AI diagnosis result to the ai_audit_log table."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _save(conn: sqlite3.Connection) -> int:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_audit_log
            (timestamp, source_alias, app_name, log_count, user_context, model, prompt_sent, response_text, tokens_in, tokens_out, tokens_thoughts, tokens_used, system_prompt, trigger_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                source_alias,
                app_name,
                log_count,
                user_context or "",
                actual_model,
                prompt_sent,
                raw_response,
                tokens_in,
                tokens_out,
                tokens_thoughts,
                tokens_used,
                system_prompt,
                trigger_source,
            ),
        )
        audit_id = cursor.lastrowid
        if trigger_source == "on-demand":
            cursor.execute(
                """
                INSERT INTO alert_history
                (rule_id, rule_name, channel_id, trigger_count, sample_log, incident_summary, ai_enrichment, ai_model, ai_audit_id, triggered_at)
                VALUES (NULL, 'On-Demand Analysis', NULL, ?, NULL, ?, 1, ?, ?, ?)
                """,
                (log_count, raw_response, actual_model, audit_id, now),
            )
        conn.commit()
        return audit_id

    return await run_db_query(_save, custom_db_path=custom_db_path)



