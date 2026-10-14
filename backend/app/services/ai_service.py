"""
AI Service Module for LogShed.

Encapsulates database log retrieval, host note aggregation, settings decryption,
shared diagnosis context preparation, model cache management, audit log persistence,
and AI follow-up conversation threads.
"""

import datetime
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, status

from app.api.deps import run_db_query
from app.core.config import (
    DEFAULT_AI_MODEL,
    DEFAULT_AI_THINKING_BUDGET,
    DEFAULT_AI_TIMEOUT,
    get_all_system_settings,
    get_cached_setting,
)
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


def resolve_ai_provider_settings(
    settings: dict[str, str],
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
    fallback_models_override: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Resolve the provider, model, credentials, fallback chain and runtime limits for an AI request
    from the current system settings, applying optional per-request overrides.
    """
    active_db_provider = (settings.get("ai_provider") or "gemini").lower()
    provider = (provider_override or active_db_provider).lower()
    default_model = DEFAULT_AI_MODEL if provider == "gemini" else ("gpt-4o" if provider == "openai" else ("claude-sonnet-4-6" if provider == "anthropic" else "llama3.2"))
    model = model_override or settings.get(f"ai_model_{provider}") or (settings.get("ai_model") if provider == active_db_provider else None) or default_model
    api_key = (
        settings.get(f"ai_api_key_{provider}")
        or (settings.get("ai_api_key", "") if provider == active_db_provider else "")
    )
    base_url = settings.get(f"ai_base_url_{provider}") or (settings.get("ai_base_url") if provider == active_db_provider else None) or None

    fallback_models_str = settings.get(f"ai_fallback_models_{provider}") or (settings.get("ai_fallback_models", "") if provider == active_db_provider else "")
    configured_fallbacks = [m.strip() for m in fallback_models_str.split(",") if m.strip()]
    fallback_models = (
        fallback_models_override
        if fallback_models_override is not None
        else configured_fallbacks
    )

    stored_ai_enabled = settings.get("ai_enabled")
    if stored_ai_enabled is not None:
        ai_enabled = stored_ai_enabled.strip().lower() not in ("0", "false", "no", "off")
    else:
        ai_enabled = bool(api_key.strip())

    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "fallback_models": fallback_models,
        "ai_timeout": float(get_cached_setting("ai_timeout", DEFAULT_AI_TIMEOUT)),
        "ai_thinking_budget": int(get_cached_setting("ai_thinking_budget", DEFAULT_AI_THINKING_BUDGET)),
        "ai_enabled": ai_enabled,
        "has_ai_api_key": bool(api_key.strip()),
    }


async def build_diagnosis_context(
    log_ids: list[int],
    user_context: Optional[str] = None,
    prompt_override: Optional[str] = None,
    system_prompt_override: Optional[str] = None,
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
    fallback_models_override: Optional[list[str]] = None,
    client_timezone: Optional[str] = None,
    client_utc_offset_minutes: Optional[int] = None,
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

    now_local = datetime.datetime.now().astimezone()
    eff_tz_name = client_timezone or os.environ.get("TZ") or now_local.tzname() or "UTC"
    eff_tz_offset = client_utc_offset_minutes
    if eff_tz_offset is None and now_local.utcoffset():
        eff_tz_offset = int(now_local.utcoffset().total_seconds() // 60)

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
            tz_name=eff_tz_name,
            tz_offset_minutes=eff_tz_offset,
        )
        for r in rows
    ]
    redacted_lines = redact(raw_lines)
    redacted_logs_text = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)
    redacted_logs_text = truncate_logs_to_budget(redacted_logs_text)

    provider_settings = resolve_ai_provider_settings(
        settings,
        provider_override=provider_override,
        model_override=model_override,
        fallback_models_override=fallback_models_override,
    )

    system_prompt = (
        system_prompt_override.strip()
        if (system_prompt_override and system_prompt_override.strip())
        else (settings.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT)
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
            tz_name=eff_tz_name,
            tz_offset_minutes=eff_tz_offset,
        )
        redacted_prompt_override = None

    # Estimate token count (~3.5 characters per token including system prompt and framing overhead)
    estimated_tokens = max(1, int(len(full_prompt) // 3.5 + len(system_prompt) // 3.5 + 50))

    return {
        **provider_settings,
        "rows": rows,
        "source_alias": source_alias,
        "app_name": app_name,
        "redacted_logs": redacted_logs_text,
        "system_prompt": system_prompt,
        "redacted_user_context": redacted_user_context,
        "redacted_host_notes": redacted_host_notes,
        "redacted_prompt_override": redacted_prompt_override,
        "full_prompt": full_prompt,
        "estimated_tokens": estimated_tokens,
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





MAX_FOLLOW_UP_TURNS = 10


def get_conversation_messages(conn: sqlite3.Connection, audit_id: int) -> list[dict[str, Any]]:
    """Return the follow-up conversation messages for an audit entry in chronological order."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, role, content, tokens_used, model, created_at
        FROM ai_conversation_messages
        WHERE audit_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (audit_id,),
    )
    return [
        {
            "id": r[0],
            "role": r[1],
            "content": r[2],
            "tokens_used": r[3],
            "model": r[4],
            "created_at": str(r[5]),
        }
        for r in cursor.fetchall()
    ]


def count_conversation_turns(messages: list[dict[str, Any]]) -> int:
    """Number of operator questions in a conversation thread."""
    return sum(1 for m in messages if m["role"] == "user")


def save_conversation_message(
    conn: sqlite3.Connection,
    audit_id: int,
    role: str,
    content: str,
    model: str,
    created_at: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_thoughts: int = 0,
    tokens_used: int = 0,
) -> int:
    """Insert one conversation message without committing. Returns the new message id."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO ai_conversation_messages
        (audit_id, role, content, tokens_in, tokens_out, tokens_thoughts, tokens_used, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (audit_id, role, content, tokens_in, tokens_out, tokens_thoughts, tokens_used, model, created_at),
    )
    return cursor.lastrowid


def delete_conversation_thread(conn: sqlite3.Connection, audit_id: int) -> int:
    """Delete every follow-up message for an audit entry without committing. Returns the number deleted."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ai_conversation_messages WHERE audit_id = ?", (audit_id,))
    return cursor.rowcount


def audit_entry_exists(conn: sqlite3.Connection, audit_id: int) -> bool:
    """True when an ai_audit_log entry with this id exists."""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM ai_audit_log WHERE id = ?", (audit_id,))
    return cursor.fetchone() is not None


def audit_not_found(audit_id: int) -> HTTPException:
    """404 error for an unknown AI analysis."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"AI analysis {audit_id} not found.",
    )


def turn_limit_reached() -> HTTPException:
    """400 error for a conversation that has used all of its follow-up questions."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Conversation turn limit of {MAX_FOLLOW_UP_TURNS} reached.",
    )


async def build_follow_up_context(
    audit_id: int,
    question: str,
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
) -> dict[str, Any]:
    """
    Reconstruct the conversation for a follow-up question: the original analysis prompt and
    diagnosis, previous follow-up turns, then the redacted new question. Provider and model are
    resolved from the current settings (with optional overrides), never from the audit entry, so
    follow-ups keep working after the original model is retired or reconfigured.
    Raises 404 for an unknown audit entry and 400 when the turn limit is reached.
    """
    def _fetch(conn: sqlite3.Connection):
        cursor = conn.cursor()
        cursor.execute("SELECT prompt_sent, response_text FROM ai_audit_log WHERE id = ?", (audit_id,))
        audit = cursor.fetchone()
        if audit is None:
            return None
        settings_dict, _ = read_ai_settings(conn)
        return audit[0], audit[1], get_conversation_messages(conn, audit_id), settings_dict

    result = await run_db_query(_fetch)
    if result is None:
        raise audit_not_found(audit_id)
    prompt_sent, response_text, history, settings = result

    if not (prompt_sent or "").strip() or not (response_text or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This analysis has no stored prompt and diagnosis to follow up on.",
        )

    turn_count = count_conversation_turns(history)
    if turn_count >= MAX_FOLLOW_UP_TURNS:
        raise turn_limit_reached()

    redacted_question = str(redact(question.strip()))
    messages = [
        {"role": "user", "content": prompt_sent},
        {"role": "assistant", "content": response_text},
        *({"role": m["role"], "content": m["content"]} for m in history),
        {"role": "user", "content": redacted_question},
    ]

    return {
        **resolve_ai_provider_settings(settings, provider_override=provider_override, model_override=model_override),
        "messages": messages,
        "question": redacted_question,
    }


async def save_follow_up_exchange(
    audit_id: int,
    question: str,
    reply: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    tokens_thoughts: int,
    tokens_used: int,
) -> tuple[dict[str, Any], int]:
    """
    Persist a follow-up question and the model reply in one transaction, re-checking the turn
    limit under the write lock so concurrent requests cannot exceed it.
    Returns (assistant_message, turn_count). Raises 404 if the audit entry was deleted meanwhile.
    """
    def _save(conn: sqlite3.Connection):
        conn.execute("BEGIN IMMEDIATE")
        if not audit_entry_exists(conn, audit_id):
            raise audit_not_found(audit_id)
        turn_count = count_conversation_turns(get_conversation_messages(conn, audit_id))
        if turn_count >= MAX_FOLLOW_UP_TURNS:
            raise turn_limit_reached()

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_conversation_message(conn, audit_id, "user", question, model, now)
        message_id = save_conversation_message(
            conn,
            audit_id,
            "assistant",
            reply,
            model,
            now,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thoughts=tokens_thoughts,
            tokens_used=tokens_used,
        )
        conn.commit()
        message = {
            "id": message_id,
            "role": "assistant",
            "content": reply,
            "tokens_used": tokens_used,
            "model": model,
            "created_at": now,
        }
        return message, turn_count + 1

    return await run_db_query(_save)
