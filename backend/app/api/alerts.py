"""
Alert rules, security presets, and incident firing history API endpoints.

Provides full CRUD operations for alert rules, 1-click security canary presets,
dry-run pattern testing, and historical alert log auditing.
"""

import asyncio
import datetime
import json
import logging
import re
import sqlite3
from typing import Any, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app.api.deps import get_current_user, run_db_query
from app.models import (
    AlertHistoryItem,
    AlertHistoryListResponse,
    AlertPresetInstallRequest,
    AlertPresetResponse,
    AlertRuleCreate,
    AlertRuleExportBundle,
    AlertRuleExportItem,
    AlertRuleExportSingle,
    AlertRuleImportResponse,
    AlertRuleResponse,
    AlertRuleUpdate,
    AlertTestRequest,
    AlertTestResponse,
    MaintenanceSchedule,
    MaintenanceSchedulesUpdateRequest,
    MaintenanceWindowRequest,
    MaintenanceWindowResponse,
    MessageResponse,
)
from app.services.alert_evaluator import CompiledAlertRule, get_alert_evaluator
from app.services.alert_presets import extract_ip_from_message, get_alert_presets, get_alert_preset_by_id
from app.core.regex_validator import check_regex_safety, validate_regex_pattern
from app.core.utils import parse_iso_to_epoch, parse_iso_to_utc_datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def _row_to_alert_rule_response(r: Any) -> AlertRuleResponse:
    """Map a raw database row tuple from alert_rules into an AlertRuleResponse."""
    return AlertRuleResponse(
        id=r[0],
        name=r[1],
        rule_type=r[2],
        channel_id=r[3],
        filter_app=r[4],
        filter_severity=r[5],
        match_pattern=r[6],
        threshold_count=r[7] or 1,
        window_seconds=r[8] or 60,
        cooldown_seconds=r[9] or 300,
        ai_enrichment=bool(r[10]),
        is_enabled=bool(r[11]),
        trigger_count=r[12] or 0,
        last_triggered_at=str(r[13]) if r[13] else None,
        suppress_until=str(r[14]) if r[14] else None,
        created_at=str(r[15]),
    )


# ---------------------------------------------------------------------------
# Global Maintenance Window Endpoints
# ---------------------------------------------------------------------------

@router.get("/maintenance", response_model=MaintenanceWindowResponse)
async def get_maintenance_window(
    user: dict = Depends(get_current_user),
) -> MaintenanceWindowResponse:
    """Retrieve current maintenance window status including on-demand and recurring schedules."""
    from app.services.maintenance_service import get_maintenance_status
    return await run_db_query(get_maintenance_status)


@router.post("/maintenance", response_model=MaintenanceWindowResponse)
async def set_maintenance_window(
    payload: MaintenanceWindowRequest,
    user: dict = Depends(get_current_user),
) -> MaintenanceWindowResponse:
    """
    Set or clear the on-demand alert maintenance window.
    Pass { "until": "<ISO 8601 datetime>" } to activate or { "until": null } to clear.
    """
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_dt.isoformat()

    if payload.until is None or not payload.until.strip():
        def _clear(conn):
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES ('maintenance_until', '', ?, 0)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (now_iso,),
            )
            conn.commit()

        await run_db_query(_clear)
    else:
        dt = parse_iso_to_utc_datetime(payload.until)
        if dt is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid ISO 8601 datetime format for maintenance_until: {payload.until}",
            )

        stored_iso = dt.isoformat()

        def _save(conn):
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES ('maintenance_until', ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (stored_iso, now_iso),
            )
            conn.commit()

        await run_db_query(_save)

    from app.services.maintenance_service import get_maintenance_status
    return await run_db_query(get_maintenance_status)


@router.post("/maintenance/schedules", response_model=MaintenanceWindowResponse)
@router.put("/maintenance/schedules", response_model=MaintenanceWindowResponse, include_in_schema=False)
async def update_maintenance_schedules(
    payload: MaintenanceSchedulesUpdateRequest,
    user: dict = Depends(get_current_user),
) -> MaintenanceWindowResponse:
    """Save recurring maintenance schedules (daily, weekly, monthly)."""
    from app.services.maintenance_service import get_maintenance_status, save_schedules

    def _save_all(conn):
        save_schedules(conn, payload.schedules)

    await run_db_query(_save_all)
    return await run_db_query(get_maintenance_status)


# ---------------------------------------------------------------------------
# Alert Rules CRUD
# ---------------------------------------------------------------------------

@router.get("/rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(user: dict = Depends(get_current_user)) -> list[AlertRuleResponse]:
    """List all configured alert rules."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, rule_type, channel_id, filter_app, filter_severity,
                   match_pattern, threshold_count, window_seconds, cooldown_seconds,
                   ai_enrichment, is_enabled, trigger_count, last_triggered_at,
                   suppress_until, created_at
            FROM alert_rules
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
        return [_row_to_alert_rule_response(r) for r in rows]

    return await run_db_query(_query)


@router.get("/export", response_model=AlertRuleExportBundle)
@router.get("/rules/export", response_model=AlertRuleExportBundle, include_in_schema=False)
async def export_alert_rules(user: dict = Depends(get_current_user)) -> Response:
    """Export all configured alert rules as a JSON bundle."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name, rule_type, filter_app, filter_severity, match_pattern,
                   threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled
            FROM alert_rules
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
        return [
            AlertRuleExportItem(
                name=r["name"],
                rule_type=r["rule_type"] or "threshold",
                filter_app=r["filter_app"],
                filter_severity=r["filter_severity"],
                match_pattern=r["match_pattern"],
                threshold_count=r["threshold_count"] or 1,
                window_seconds=r["window_seconds"] or 60,
                cooldown_seconds=r["cooldown_seconds"] or 300,
                ai_enrichment=bool(r["ai_enrichment"]),
                is_enabled=bool(r["is_enabled"]),
            )
            for r in rows
        ]

    exported_rules = await run_db_query(_query)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    bundle = AlertRuleExportBundle(
        version="1",
        exported_at=now_iso,
        alert_rules=exported_rules,
    )
    return Response(
        content=json.dumps(bundle.model_dump(), indent=2),
        media_type="application/json",
    )


@router.get("/{rule_id}/export", response_model=AlertRuleExportSingle)
@router.get("/rules/{rule_id}/export", response_model=AlertRuleExportSingle, include_in_schema=False)
async def export_single_alert_rule(
    rule_id: int,
    user: dict = Depends(get_current_user),
) -> Response:
    """Export a single alert rule as a JSON object."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name, rule_type, filter_app, filter_severity, match_pattern,
                   threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled
            FROM alert_rules
            WHERE id = ?
            """,
            (rule_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return AlertRuleExportItem(
            name=row["name"],
            rule_type=row["rule_type"] or "threshold",
            filter_app=row["filter_app"],
            filter_severity=row["filter_severity"],
            match_pattern=row["match_pattern"],
            threshold_count=row["threshold_count"] or 1,
            window_seconds=row["window_seconds"] or 60,
            cooldown_seconds=row["cooldown_seconds"] or 300,
            ai_enrichment=bool(row["ai_enrichment"]),
            is_enabled=bool(row["is_enabled"]),
        )

    item = await run_db_query(_query)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found.",
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    single = AlertRuleExportSingle(
        version="1",
        exported_at=now_iso,
        alert_rule=item,
    )
    return Response(
        content=json.dumps(single.model_dump(), indent=2),
        media_type="application/json",
    )


@router.post("/import", response_model=AlertRuleImportResponse)
@router.post("/rules/import", response_model=AlertRuleImportResponse, include_in_schema=False)
async def import_alert_rules(
    payload: Any = Body(...),
    user: dict = Depends(get_current_user),
) -> AlertRuleImportResponse:
    """
    Import alert rules from a bundle, single rule object, or raw dictionary.
    Deduplicates rules with matching names case-insensitively.
    """
    if isinstance(payload, dict):
        if "alert_rules" in payload and isinstance(payload["alert_rules"], list):
            items = payload["alert_rules"]
        elif "alert_rule" in payload and isinstance(payload["alert_rule"], dict):
            items = [payload["alert_rule"]]
        else:
            items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        return AlertRuleImportResponse(
            imported=0,
            skipped=0,
            errors=["Invalid payload format: expected JSON object or array."],
        )

    def _get_existing(conn):
        cur = conn.cursor()
        cur.execute("SELECT name FROM alert_rules")
        rows = cur.fetchall()
        return {r["name"].strip().lower() for r in rows if r["name"]}

    existing_names = await run_db_query(_get_existing)

    skipped = 0
    errors: list[str] = []
    to_insert: list[dict[str, Any]] = []

    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            errors.append(f"Item #{idx + 1} is not a valid JSON object.")
            continue

        raw_name = raw_item.get("name")
        if not raw_name or not str(raw_name).strip():
            errors.append(f"Item #{idx + 1}: rule name is required.")
            continue

        name = str(raw_name).strip()
        rule_type = str(raw_item.get("rule_type", "threshold")).strip().lower()
        if rule_type not in ("threshold", "pattern", "rate"):
            rule_type = "threshold"

        filter_app = raw_item.get("filter_app")
        filter_app = str(filter_app).strip() if filter_app is not None else None
        if filter_app == "":
            filter_app = None

        filter_severity = raw_item.get("filter_severity")
        if filter_severity is not None:
            try:
                filter_severity = int(filter_severity)
                if not (0 <= filter_severity <= 7):
                    errors.append(f"Rule '{name}': filter_severity must be between 0 and 7.")
                    continue
            except (ValueError, TypeError):
                errors.append(f"Rule '{name}': invalid filter_severity value.")
                continue

        match_pattern = raw_item.get("match_pattern")
        match_pattern = str(match_pattern).strip() if match_pattern is not None else None
        if match_pattern == "":
            match_pattern = None

        if match_pattern:
            is_safe, err_str = check_regex_safety(match_pattern)
            if not is_safe:
                errors.append(f"Rule '{name}': regex safety check failed: {err_str}")
                continue
            try:
                re.compile(match_pattern)
            except re.error as e:
                errors.append(f"Rule '{name}': invalid regex pattern: {e}")
                continue

        threshold_count = raw_item.get("threshold_count", 1)
        try:
            threshold_count = max(1, min(100000, int(threshold_count)))
        except (ValueError, TypeError):
            threshold_count = 1

        window_seconds = raw_item.get("window_seconds", 60)
        try:
            window_seconds = max(1, min(86400, int(window_seconds)))
        except (ValueError, TypeError):
            window_seconds = 60

        cooldown_seconds = raw_item.get("cooldown_seconds", 300)
        try:
            cooldown_seconds = max(5, min(86400, int(cooldown_seconds)))
        except (ValueError, TypeError):
            cooldown_seconds = 300

        ai_enrichment = bool(raw_item.get("ai_enrichment", False))
        is_enabled = bool(raw_item.get("is_enabled", True))

        if name.lower() in existing_names:
            skipped += 1
            continue

        existing_names.add(name.lower())
        to_insert.append({
            "name": name,
            "rule_type": rule_type,
            "filter_app": filter_app,
            "filter_severity": filter_severity,
            "match_pattern": match_pattern,
            "threshold_count": threshold_count,
            "window_seconds": window_seconds,
            "cooldown_seconds": cooldown_seconds,
            "ai_enrichment": ai_enrichment,
            "is_enabled": is_enabled,
        })

    if to_insert:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        def _insert_all(conn):
            cur = conn.cursor()
            for rule in to_insert:
                cur.execute(
                    """
                    INSERT INTO alert_rules
                    (name, rule_type, channel_id, filter_app, filter_severity, match_pattern,
                     threshold_count, window_seconds, cooldown_seconds, ai_enrichment,
                     is_enabled, trigger_count, created_at)
                    VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        rule["name"],
                        rule["rule_type"],
                        rule["filter_app"],
                        rule["filter_severity"],
                        rule["match_pattern"],
                        rule["threshold_count"],
                        rule["window_seconds"],
                        rule["cooldown_seconds"],
                        int(rule["ai_enrichment"]),
                        int(rule["is_enabled"]),
                        now_iso,
                    ),
                )
            conn.commit()

        await run_db_query(_insert_all)
        await asyncio.to_thread(get_alert_evaluator().reload_rules)

    return AlertRuleImportResponse(
        imported=len(to_insert),
        skipped=skipped,
        errors=errors,
    )


@router.post("/rules", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    rule_in: AlertRuleCreate,
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    """Create a new alert rule and update the evaluation engine."""
    if rule_in.match_pattern:
        validate_regex_pattern(rule_in.match_pattern)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _insert(conn):
        cur = conn.cursor()

        # Validate channel_id if specified
        if rule_in.channel_id is not None:
            cur.execute("SELECT id FROM notification_channels WHERE id = ?", (rule_in.channel_id,))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Notification channel {rule_in.channel_id} does not exist.",
                )

        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, channel_id, filter_app, filter_severity, match_pattern,
             threshold_count, window_seconds, cooldown_seconds, ai_enrichment,
             is_enabled, trigger_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                rule_in.name.strip(),
                rule_in.rule_type.strip(),
                rule_in.channel_id,
                rule_in.filter_app.strip() if rule_in.filter_app else None,
                rule_in.filter_severity,
                rule_in.match_pattern.strip() if rule_in.match_pattern else None,
                rule_in.threshold_count,
                rule_in.window_seconds,
                rule_in.cooldown_seconds,
                int(rule_in.ai_enrichment),
                int(rule_in.is_enabled),
                now_iso,
            ),
        )
        rule_id = cur.lastrowid
        conn.commit()
        return rule_id

    rule_id = await run_db_query(_insert)
    await asyncio.to_thread(get_alert_evaluator().reload_rules)

    return AlertRuleResponse(
        id=rule_id,
        name=rule_in.name.strip(),
        rule_type=rule_in.rule_type.strip(),
        channel_id=rule_in.channel_id,
        filter_app=rule_in.filter_app.strip() if rule_in.filter_app else None,
        filter_severity=rule_in.filter_severity,
        match_pattern=rule_in.match_pattern.strip() if rule_in.match_pattern else None,
        threshold_count=rule_in.threshold_count,
        window_seconds=rule_in.window_seconds,
        cooldown_seconds=rule_in.cooldown_seconds,
        ai_enrichment=rule_in.ai_enrichment,
        is_enabled=rule_in.is_enabled,
        trigger_count=0,
        last_triggered_at=None,
        suppress_until=None,
        created_at=now_iso,
    )


@router.get("/rules/{rule_id}", response_model=AlertRuleResponse)
async def get_alert_rule(
    rule_id: int,
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    """Retrieve details for a single alert rule."""
    def _fetch(conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, rule_type, channel_id, filter_app, filter_severity,
                   match_pattern, threshold_count, window_seconds, cooldown_seconds,
                   ai_enrichment, is_enabled, trigger_count, last_triggered_at,
                   suppress_until, created_at
            FROM alert_rules
            WHERE id = ?
            """,
            (rule_id,),
        )
        return cur.fetchone()

    row = await run_db_query(_fetch)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found.",
        )

    return _row_to_alert_rule_response(row)


@router.put("/rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: int,
    rule_in: AlertRuleUpdate,
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    """Update fields on an existing alert rule."""
    def _fetch(conn):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, rule_type, channel_id, filter_app, filter_severity,
                   match_pattern, threshold_count, window_seconds, cooldown_seconds,
                   ai_enrichment, is_enabled, trigger_count, last_triggered_at,
                   suppress_until, created_at
            FROM alert_rules
            WHERE id = ?
            """,
            (rule_id,),
        )
        return cur.fetchone()

    row = await run_db_query(_fetch)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found.",
        )

    fields = rule_in.model_fields_set
    new_name = rule_in.name.strip() if "name" in fields and rule_in.name is not None else row[1]
    new_type = rule_in.rule_type.strip() if "rule_type" in fields and rule_in.rule_type is not None else row[2]
    new_channel = rule_in.channel_id if "channel_id" in fields else row[3]
    new_app = (rule_in.filter_app.strip() if rule_in.filter_app else None) if "filter_app" in fields else row[4]
    new_sev = rule_in.filter_severity if "filter_severity" in fields else row[5]
    new_pat = (rule_in.match_pattern.strip() if rule_in.match_pattern else None) if "match_pattern" in fields else row[6]
    if "match_pattern" in fields and new_pat:
        validate_regex_pattern(new_pat)
    new_thresh = rule_in.threshold_count if "threshold_count" in fields and rule_in.threshold_count is not None else row[7]
    new_win = rule_in.window_seconds if "window_seconds" in fields and rule_in.window_seconds is not None else row[8]
    new_cool = rule_in.cooldown_seconds if "cooldown_seconds" in fields and rule_in.cooldown_seconds is not None else row[9]
    new_ai = rule_in.ai_enrichment if "ai_enrichment" in fields and rule_in.ai_enrichment is not None else bool(row[10])
    new_enabled = rule_in.is_enabled if "is_enabled" in fields and rule_in.is_enabled is not None else bool(row[11])

    # Recompute or expire suppress_until when cooldown_seconds is modified or reset requested
    if rule_in.reset_cooldown:
        suppress_until = None
    elif "cooldown_seconds" in fields and new_cool != row[9]:
        if row[13]:
            last_epoch = parse_iso_to_epoch(row[13], fallback=0.0)
            if last_epoch > 0.0:
                new_suppress_epoch = last_epoch + new_cool
                now_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
                suppress_until = (
                    None
                    if new_suppress_epoch <= now_epoch
                    else datetime.datetime.fromtimestamp(new_suppress_epoch, tz=datetime.timezone.utc).isoformat()
                )
            else:
                suppress_until = None
        else:
            suppress_until = None
    elif row[14]:
        old_suppress_epoch = parse_iso_to_epoch(row[14], fallback=0.0)
        now_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
        suppress_until = None if (old_suppress_epoch <= now_epoch or old_suppress_epoch <= 0.0) else row[14]
    else:
        suppress_until = None

    def _update(conn):
        cur = conn.cursor()
        if new_channel is not None and new_channel != row[3]:
            cur.execute("SELECT id FROM notification_channels WHERE id = ?", (new_channel,))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Notification channel {new_channel} does not exist.",
                )

        cur.execute(
            """
            UPDATE alert_rules
            SET name = ?, rule_type = ?, channel_id = ?, filter_app = ?,
                filter_severity = ?, match_pattern = ?, threshold_count = ?,
                window_seconds = ?, cooldown_seconds = ?, ai_enrichment = ?,
                is_enabled = ?, suppress_until = ?
            WHERE id = ?
            """,
            (
                new_name,
                new_type,
                new_channel,
                new_app,
                new_sev,
                new_pat,
                new_thresh,
                new_win,
                new_cool,
                int(new_ai),
                int(new_enabled),
                suppress_until,
                rule_id,
            ),
        )
        cur.execute(
            """
            SELECT id, name, rule_type, channel_id, filter_app, filter_severity,
                   match_pattern, threshold_count, window_seconds, cooldown_seconds,
                   ai_enrichment, is_enabled, trigger_count, last_triggered_at,
                   suppress_until, created_at
            FROM alert_rules
            WHERE id = ?
            """,
            (rule_id,),
        )
        updated_row = cur.fetchone()
        conn.commit()
        return updated_row

    updated_row = await run_db_query(_update)
    await asyncio.to_thread(get_alert_evaluator().reload_rules)

    return _row_to_alert_rule_response(updated_row)


@router.delete("/rules/{rule_id}", response_model=MessageResponse)
async def delete_alert_rule(
    rule_id: int,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete an alert rule and remove it from active evaluation."""
    def _delete(conn):
        cur = conn.cursor()
        cur.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        count = cur.rowcount
        conn.commit()
        return count

    deleted = await run_db_query(_delete)
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert rule {rule_id} not found.",
        )

    await asyncio.to_thread(get_alert_evaluator().reload_rules)

    return MessageResponse(
        status="ok",
        detail=f"Alert rule {rule_id} deleted successfully.",
    )


# ---------------------------------------------------------------------------
# Test Dry-Run Pattern Endpoint
# ---------------------------------------------------------------------------

@router.post("/test", response_model=AlertTestResponse)
async def test_alert_pattern(
    payload: AlertTestRequest,
    user: dict = Depends(get_current_user),
) -> AlertTestResponse:
    """
    Test pattern matching and IP extraction against a sample log payload.
    """
    try:
        temp_rule = CompiledAlertRule(
            id=0,
            name="test",
            rule_type=payload.rule_type,
            channel_id=None,
            filter_app=payload.filter_app,
            filter_severity=payload.filter_severity,
            match_pattern=payload.match_pattern,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=0,
            ai_enrichment=False,
            is_enabled=True,
        )

        sample_entry = {
            "app_name": payload.sample_app or "",
            "severity": payload.sample_severity if payload.sample_severity is not None else 6,
            "message": payload.sample_message,
            "raw": payload.sample_message,
        }

        regex_error = None
        if payload.match_pattern and payload.match_pattern.strip() and payload.match_pattern.strip() != "*":
            is_safe, err = check_regex_safety(payload.match_pattern.strip())
            if not is_safe:
                regex_error = err

        matched = False if regex_error else temp_rule.matches(sample_entry)
        extracted_ip = extract_ip_from_message(payload.sample_message)

        return AlertTestResponse(
            matched=matched,
            extracted_ip=extracted_ip,
            error=regex_error,
        )
    except Exception as exc:
        return AlertTestResponse(
            matched=False,
            extracted_ip=None,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Alert Presets
# ---------------------------------------------------------------------------

@router.get("/presets", response_model=list[AlertPresetResponse])
async def list_alert_presets(user: dict = Depends(get_current_user)) -> list[AlertPresetResponse]:
    """Retrieve all available 1-click alert presets."""
    presets = get_alert_presets()
    return [AlertPresetResponse(**p) for p in presets]


@router.post("/presets/{preset_id}/install", response_model=AlertRuleResponse, status_code=status.HTTP_201_CREATED)
async def install_preset(
    preset_id: str,
    payload: Optional[AlertPresetInstallRequest] = None,
    user: dict = Depends(get_current_user),
) -> AlertRuleResponse:
    """
    1-click install of an alert preset into active alert rules.
    """
    preset = get_alert_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert preset '{preset_id}' not found.",
        )

    channel_id = payload.channel_id if payload else None
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _insert(conn):
        cur = conn.cursor()
        if channel_id is not None:
            cur.execute("SELECT id FROM notification_channels WHERE id = ?", (channel_id,))
            if not cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Notification channel {channel_id} does not exist.",
                )

        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, channel_id, filter_app, filter_severity, match_pattern,
             threshold_count, window_seconds, cooldown_seconds, ai_enrichment,
             is_enabled, trigger_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
            """,
            (
                preset["name"],
                preset["rule_type"],
                channel_id,
                preset.get("filter_app"),
                preset.get("filter_severity"),
                preset.get("match_pattern"),
                preset["threshold_count"],
                preset["window_seconds"],
                preset["cooldown_seconds"],
                int(preset["ai_enrichment"]),
                now_iso,
            ),
        )
        rule_id = cur.lastrowid
        conn.commit()
        return rule_id

    rule_id = await run_db_query(_insert)
    await asyncio.to_thread(get_alert_evaluator().reload_rules)

    return AlertRuleResponse(
        id=rule_id,
        name=preset["name"],
        rule_type=preset["rule_type"],
        channel_id=channel_id,
        filter_app=preset.get("filter_app"),
        filter_severity=preset.get("filter_severity"),
        match_pattern=preset.get("match_pattern"),
        threshold_count=preset["threshold_count"],
        window_seconds=preset["window_seconds"],
        cooldown_seconds=preset["cooldown_seconds"],
        ai_enrichment=preset["ai_enrichment"],
        is_enabled=True,
        trigger_count=0,
        last_triggered_at=None,
        suppress_until=None,
        created_at=now_iso,
    )


# ---------------------------------------------------------------------------
# Alert History Endpoints
# ---------------------------------------------------------------------------

@router.get("/history", response_model=AlertHistoryListResponse)
async def list_alert_history(
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset start"),
    rule_id: Optional[int] = Query(None, description="Optional rule ID filter"),
    user: dict = Depends(get_current_user),
) -> AlertHistoryListResponse:
    """Retrieve historical alert and AI analysis firing events with pagination."""
    def _query(conn):
        cur = conn.cursor()
        where_sql = "WHERE h.rule_id = ?" if rule_id is not None else ""
        count_where = "WHERE rule_id = ?" if rule_id is not None else ""
        params = [rule_id] if rule_id is not None else []

        cur.execute(f"SELECT COUNT(*) FROM alert_history {count_where}", params)
        total = cur.fetchone()[0]

        query_sql = f"""
            SELECT h.id, h.rule_id, h.rule_name, h.channel_id, h.trigger_count,
                   h.sample_log, h.incident_summary, h.ai_enrichment, h.ai_model,
                   h.ai_audit_id, h.triggered_at,
                   a.tokens_in, a.tokens_out, a.tokens_thoughts, a.tokens_used,
                   a.prompt_sent, a.system_prompt, a.response_text,
                   a.source_alias, a.app_name, a.user_context
            FROM alert_history h
            LEFT JOIN ai_audit_log a ON h.ai_audit_id = a.id
            {where_sql}
            ORDER BY h.triggered_at DESC, h.id DESC
            LIMIT ? OFFSET ?
        """
        cur.execute(query_sql, params + [limit, offset])
        rows = cur.fetchall()

        items = []
        for r in rows:
            p_sent = r["prompt_sent"]
            if p_sent and not p_sent.startswith("### System Metadata") and not p_sent.startswith("### Redacted Log Stream") and not p_sent.startswith("### Security"):
                from app.services.ai_engine import build_analysis_prompt
                p_sent = build_analysis_prompt(
                    source_alias=r["source_alias"] or "unknown",
                    app_name=r["app_name"] or "unknown",
                    redacted_logs=p_sent,
                    log_count=r["trigger_count"] or 1,
                    user_context=r["user_context"],
                )

            items.append(
                AlertHistoryItem(
                    id=r["id"],
                    rule_id=r["rule_id"],
                    rule_name=r["rule_name"],
                    channel_id=r["channel_id"],
                    trigger_count=r["trigger_count"] or 1,
                    sample_log=r["sample_log"],
                    incident_summary=r["incident_summary"],
                    ai_enrichment=bool(r["ai_enrichment"]),
                    ai_model=r["ai_model"],
                    ai_audit_id=r["ai_audit_id"],
                    triggered_at=str(r["triggered_at"]),
                    tokens_in=r["tokens_in"],
                    tokens_out=r["tokens_out"],
                    tokens_thoughts=r["tokens_thoughts"],
                    tokens_used=r["tokens_used"],
                    prompt_sent=p_sent,
                    system_prompt=r["system_prompt"],
                    response_text=r["response_text"],
                    source_alias=r["source_alias"],
                    app_name=r["app_name"],
                    user_context=r["user_context"],
                )
            )
        return items, total

    items, total = await run_db_query(_query)
    return AlertHistoryListResponse(items=items, total=total, limit=limit, offset=offset)


@router.delete("/history/{history_id}", response_model=MessageResponse)
async def delete_alert_history_item(
    history_id: int,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete a single alert firing history record and associated AI audit log if present."""
    def _delete(conn):
        cur = conn.cursor()
        cur.execute("SELECT ai_audit_id FROM alert_history WHERE id = ?", (history_id,))
        row = cur.fetchone()
        if not row:
            return 0
        ai_audit_id = row["ai_audit_id"] if isinstance(row, sqlite3.Row) else row[0]
        cur.execute("DELETE FROM alert_history WHERE id = ?", (history_id,))
        count = cur.rowcount
        if ai_audit_id:
            cur.execute("DELETE FROM ai_audit_log WHERE id = ?", (ai_audit_id,))
        conn.commit()
        return count

    deleted = await run_db_query(_delete)
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert history record {history_id} not found.",
        )

    return MessageResponse(
        status="ok",
        detail=f"Alert history record {history_id} deleted successfully.",
    )


@router.delete("/history", response_model=MessageResponse)
async def clear_alert_history(user: dict = Depends(get_current_user)) -> MessageResponse:
    """Clear all historical alert firing records and AI audit logs."""
    def _clear(conn):
        cur = conn.cursor()
        cur.execute("DELETE FROM alert_history")
        count = cur.rowcount
        cur.execute("DELETE FROM ai_audit_log")
        conn.commit()
        return count

    deleted = await run_db_query(_clear)
    return MessageResponse(
        status="ok",
        detail=f"Cleared {deleted} alert history record(s).",
    )
