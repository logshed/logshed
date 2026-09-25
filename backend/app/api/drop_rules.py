"""
Drop rules management API endpoints.

Allows configuring, updating, testing, and toggling in-memory log drop rules
to discard repetitive syslog or container noise before persistence.
"""

import asyncio
import datetime
import json
import re
from typing import Any
from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from app.api.deps import get_current_user, run_db_query
from app.models import (
    DropPresetResponse,
    DropRuleCreate,
    DropRuleExportBundle,
    DropRuleExportItem,
    DropRuleExportSingle,
    DropRuleImportResponse,
    DropRuleResponse,
    DropRuleTestRequest,
    DropRuleTestResponse,
    DropRuleUpdate,
    MessageResponse,
)
from app.services.drop_filter import CompiledDropRule, get_drop_filter
from app.services.drop_presets import get_drop_presets, get_drop_preset_by_id
from app.core.regex_validator import check_regex_safety, validate_regex_pattern

router = APIRouter(prefix="/drop-rules", tags=["Drop Rules"])


@router.get("", response_model=list[DropRuleResponse])
async def list_drop_rules(user: dict = Depends(get_current_user)) -> list[DropRuleResponse]:
    """List all configured drop rules with accumulated dropped counts."""
    drop_filter = get_drop_filter()
    counts = drop_filter.get_all_pending_counts()

    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at "
            "FROM drop_rules ORDER BY id ASC"
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            rule_id = r["id"]
            live_count = r["dropped_count"] + counts.get(rule_id, 0)
            results.append(
                DropRuleResponse(
                    id=rule_id,
                    source_pattern=r["source_pattern"],
                    app_pattern=r["app_pattern"],
                    message_pattern=r["message_pattern"],
                    is_regex=bool(r["is_regex"]),
                    is_enabled=bool(r["is_enabled"]),
                    severity_threshold=r["severity_threshold"],
                    dropped_count=live_count,
                    created_at=str(r["created_at"]),
                )
            )
        return results

    return await run_db_query(_query)


@router.get("/export", response_model=DropRuleExportBundle)
async def export_drop_rules(user: dict = Depends(get_current_user)) -> Response:
    """Export all configured drop rules as a JSON bundle."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold "
            "FROM drop_rules ORDER BY id ASC"
        )
        rows = cur.fetchall()
        return [
            DropRuleExportItem(
                source_pattern=r["source_pattern"],
                app_pattern=r["app_pattern"],
                message_pattern=r["message_pattern"],
                is_regex=bool(r["is_regex"]),
                is_enabled=bool(r["is_enabled"]),
                severity_threshold=r["severity_threshold"],
            )
            for r in rows
        ]

    exported_rules = await run_db_query(_query)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    bundle = DropRuleExportBundle(
        version="1",
        exported_at=now_iso,
        drop_rules=exported_rules,
    )
    return Response(
        content=json.dumps(bundle.model_dump(), indent=2),
        media_type="application/json",
    )


@router.get("/{rule_id}/export", response_model=DropRuleExportSingle)
async def export_single_drop_rule(
    rule_id: int,
    user: dict = Depends(get_current_user),
) -> Response:
    """Export a single drop rule as a JSON object."""
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold "
            "FROM drop_rules WHERE id = ?",
            (rule_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return DropRuleExportItem(
            source_pattern=row["source_pattern"],
            app_pattern=row["app_pattern"],
            message_pattern=row["message_pattern"],
            is_regex=bool(row["is_regex"]),
            is_enabled=bool(row["is_enabled"]),
            severity_threshold=row["severity_threshold"],
        )

    item = await run_db_query(_query)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Drop rule {rule_id} not found.",
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    single = DropRuleExportSingle(
        version="1",
        exported_at=now_iso,
        drop_rule=item,
    )
    return Response(
        content=json.dumps(single.model_dump(), indent=2),
        media_type="application/json",
    )


@router.post("/import", response_model=DropRuleImportResponse)
async def import_drop_rules(
    payload: Any = Body(...),
    user: dict = Depends(get_current_user),
) -> DropRuleImportResponse:
    """
    Import drop rules from a bundle, single rule object, or raw dictionary.
    Deduplicates rules matching (source_pattern, app_pattern, message_pattern) case-insensitively.
    """
    if isinstance(payload, dict):
        if "drop_rules" in payload and isinstance(payload["drop_rules"], list):
            items = payload["drop_rules"]
        elif "drop_rule" in payload and isinstance(payload["drop_rule"], dict):
            items = [payload["drop_rule"]]
        else:
            items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        return DropRuleImportResponse(
            imported=0,
            skipped=0,
            errors=["Invalid payload format: expected JSON object or array."],
        )

    def _get_existing(conn):
        cur = conn.cursor()
        cur.execute("SELECT source_pattern, app_pattern, message_pattern FROM drop_rules")
        rows = cur.fetchall()
        return {
            (
                (r["source_pattern"] or "").lower(),
                (r["app_pattern"] or "").lower(),
                (r["message_pattern"] or "").lower(),
            )
            for r in rows
        }

    existing_keys = await run_db_query(_get_existing)

    skipped = 0
    errors: list[str] = []
    to_insert: list[dict[str, Any]] = []

    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            errors.append(f"Item #{idx + 1} is not a valid JSON object.")
            continue

        src = raw_item.get("source_pattern")
        src = str(src).strip() if src is not None else None
        if src == "":
            src = None

        app = raw_item.get("app_pattern")
        app = str(app).strip() if app is not None else None
        if app == "":
            app = None

        msg = raw_item.get("message_pattern")
        msg = str(msg).strip() if msg is not None else "*"
        if not msg:
            msg = "*"

        is_regex = bool(raw_item.get("is_regex", False))
        is_enabled = bool(raw_item.get("is_enabled", True))

        sev = raw_item.get("severity_threshold")
        if sev is not None:
            try:
                sev = int(sev)
                if not (0 <= sev <= 7):
                    errors.append(f"Item #{idx + 1}: severity_threshold must be between 0 and 7.")
                    continue
            except (ValueError, TypeError):
                errors.append(f"Item #{idx + 1}: invalid severity_threshold value.")
                continue

        if not src and not app and (not msg or msg == "*") and sev is None:
            errors.append(f"Item #{idx + 1}: at least one filter criterion must be specified.")
            continue

        if is_regex and msg != "*":
            is_safe, err_str = check_regex_safety(msg)
            if not is_safe:
                errors.append(f"Item #{idx + 1}: regex safety check failed: {err_str}")
                continue
            try:
                re.compile(msg)
            except re.error as e:
                errors.append(f"Item #{idx + 1}: invalid regex pattern: {e}")
                continue

        dedup_key = (
            (src or "").lower(),
            (app or "").lower(),
            (msg or "").lower(),
        )

        if dedup_key in existing_keys:
            skipped += 1
            continue

        existing_keys.add(dedup_key)
        to_insert.append({
            "source_pattern": src,
            "app_pattern": app,
            "message_pattern": msg,
            "is_regex": is_regex,
            "is_enabled": is_enabled,
            "severity_threshold": sev,
        })

    if to_insert:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        def _insert_all(conn):
            cur = conn.cursor()
            for rule in to_insert:
                cur.execute(
                    """
                    INSERT INTO drop_rules (
                        source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        rule["source_pattern"],
                        rule["app_pattern"],
                        rule["message_pattern"],
                        1 if rule["is_regex"] else 0,
                        1 if rule["is_enabled"] else 0,
                        rule["severity_threshold"],
                        now_iso,
                    ),
                )
            conn.commit()

        await run_db_query(_insert_all)
        await asyncio.to_thread(get_drop_filter().reload_rules)

    return DropRuleImportResponse(
        imported=len(to_insert),
        skipped=skipped,
        errors=errors,
    )


@router.post("", response_model=DropRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_drop_rule(
    payload: DropRuleCreate,
    user: dict = Depends(get_current_user),
) -> DropRuleResponse:
    """Create a new drop rule and refresh the in-memory drop filter cache."""
    source = payload.source_pattern.strip() if payload.source_pattern else None
    app = payload.app_pattern.strip() if payload.app_pattern else None
    msg = payload.message_pattern.strip() if payload.message_pattern else "*"

    if not source and not app and (not msg or msg == "*") and payload.severity_threshold is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one filter criterion (Host/IP, App/Container, Message Pattern, or Severity Threshold) must be specified.",
        )

    # Validate regex syntax and ReDoS safety if enabled
    if payload.is_regex and msg != "*":
        validate_regex_pattern(msg)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _insert(conn):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO drop_rules (
                source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                source,
                app,
                msg,
                1 if payload.is_regex else 0,
                1 if payload.is_enabled else 0,
                payload.severity_threshold,
                now_iso,
            ),
        )
        conn.commit()
        rule_id = cur.lastrowid
        return DropRuleResponse(
            id=rule_id,
            source_pattern=source,
            app_pattern=app,
            message_pattern=msg,
            is_regex=payload.is_regex,
            is_enabled=payload.is_enabled,
            severity_threshold=payload.severity_threshold,
            dropped_count=0,
            created_at=now_iso,
        )

    result = await run_db_query(_insert)
    await asyncio.to_thread(get_drop_filter().reload_rules)
    return result


@router.put("/{rule_id}", response_model=DropRuleResponse)
async def update_drop_rule(
    rule_id: int,
    payload: DropRuleUpdate,
    user: dict = Depends(get_current_user),
) -> DropRuleResponse:
    """Update criteria or status for an existing drop rule."""
    def _update(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at "
            "FROM drop_rules WHERE id = ?",
            (rule_id,),
        )
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drop rule not found.")

        new_source = (
            payload.source_pattern.strip()
            if payload.source_pattern is not None
            else existing["source_pattern"]
        )
        new_app = (
            payload.app_pattern.strip()
            if payload.app_pattern is not None
            else existing["app_pattern"]
        )
        new_message = (
            payload.message_pattern
            if payload.message_pattern is not None
            else existing["message_pattern"]
        )
        new_is_regex = (
            payload.is_regex
            if payload.is_regex is not None
            else bool(existing["is_regex"])
        )
        new_is_enabled = (
            payload.is_enabled
            if payload.is_enabled is not None
            else bool(existing["is_enabled"])
        )
        new_severity_threshold = (
            payload.severity_threshold
            if "severity_threshold" in payload.model_fields_set
            else existing["severity_threshold"]
        )

        if new_is_regex and new_message != "*":
            validate_regex_pattern(new_message)

        source_normalized = new_source if new_source else None
        app_normalized = new_app if new_app else None
        existing_source = existing["source_pattern"] if existing["source_pattern"] else None
        existing_app = existing["app_pattern"] if existing["app_pattern"] else None

        criteria_changed = (
            (payload.source_pattern is not None and source_normalized != existing_source)
            or (payload.app_pattern is not None and app_normalized != existing_app)
            or (payload.message_pattern is not None and new_message != existing["message_pattern"])
            or (payload.is_regex is not None and new_is_regex != bool(existing["is_regex"]))
            or ("severity_threshold" in payload.model_fields_set and new_severity_threshold != existing["severity_threshold"])
        )
        should_reset = (payload.reset_counter is True) or criteria_changed

        cur.execute(
            """
            UPDATE drop_rules SET
                source_pattern = ?,
                app_pattern = ?,
                message_pattern = ?,
                is_regex = ?,
                is_enabled = ?,
                severity_threshold = ?
            WHERE id = ?
            """,
            (
                source_normalized,
                app_normalized,
                new_message,
                1 if new_is_regex else 0,
                1 if new_is_enabled else 0,
                new_severity_threshold,
                rule_id,
            ),
        )
        if should_reset:
            cur.execute("UPDATE drop_rules SET dropped_count = 0 WHERE id = ?", (rule_id,))
            get_drop_filter().reset_rule_count(rule_id)
            live_count = 0
        else:
            live_count = existing["dropped_count"] + get_drop_filter().get_pending_count(rule_id)
        conn.commit()

        return DropRuleResponse(
            id=rule_id,
            source_pattern=source_normalized,
            app_pattern=app_normalized,
            message_pattern=new_message,
            is_regex=new_is_regex,
            is_enabled=new_is_enabled,
            severity_threshold=new_severity_threshold,
            dropped_count=live_count,
            created_at=str(existing["created_at"]),
        )

    result = await run_db_query(_update)
    await asyncio.to_thread(get_drop_filter().reload_rules)
    return result


@router.delete("/{rule_id}", response_model=MessageResponse)
async def delete_drop_rule(
    rule_id: int,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Delete a drop rule and reload active rules cache."""
    def _delete(conn):
        cur = conn.cursor()
        cur.execute("SELECT id FROM drop_rules WHERE id = ?", (rule_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drop rule not found.")
        cur.execute("DELETE FROM drop_rules WHERE id = ?", (rule_id,))
        conn.commit()

    await run_db_query(_delete)
    get_drop_filter().reset_rule_count(rule_id)
    await asyncio.to_thread(get_drop_filter().reload_rules)
    return MessageResponse(status="ok", detail=f"Rule {rule_id} deleted successfully.")


@router.post("/{rule_id}/reset", response_model=DropRuleResponse)
async def reset_drop_rule_counter(
    rule_id: int,
    user: dict = Depends(get_current_user),
) -> DropRuleResponse:
    """Reset the dropped log counter for a specific drop rule."""
    def _reset(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT id, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, created_at "
            "FROM drop_rules WHERE id = ?",
            (rule_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drop rule not found.")
        cur.execute("UPDATE drop_rules SET dropped_count = 0 WHERE id = ?", (rule_id,))
        conn.commit()
        return DropRuleResponse(
            id=row["id"],
            source_pattern=row["source_pattern"],
            app_pattern=row["app_pattern"],
            message_pattern=row["message_pattern"],
            is_regex=bool(row["is_regex"]),
            is_enabled=bool(row["is_enabled"]),
            severity_threshold=row["severity_threshold"],
            dropped_count=0,
            created_at=str(row["created_at"]),
        )

    result = await run_db_query(_reset)
    get_drop_filter().reset_rule_count(rule_id)
    return result


@router.post("/test", response_model=DropRuleTestResponse)
async def test_drop_rule(
    payload: DropRuleTestRequest,
    user: dict = Depends(get_current_user),
) -> DropRuleTestResponse:
    """
    Dry-run test of candidate drop rule criteria against sample log inputs.
    Does not modify database records or increment counters.
    """
    msg_pat = payload.message_pattern.strip() if payload.message_pattern else "*"
    compiled_re = None
    if payload.is_regex and msg_pat != "*":
        is_safe, err = check_regex_safety(msg_pat)
        if not is_safe:
            return DropRuleTestResponse(matched=False, error=err)
        compiled_re = re.compile(msg_pat, re.IGNORECASE)

    rule = CompiledDropRule(
        id=0,
        source_pattern=payload.source_pattern.strip() if payload.source_pattern else None,
        app_pattern=payload.app_pattern.strip() if payload.app_pattern else None,
        message_pattern=msg_pat,
        is_regex=payload.is_regex,
        is_enabled=True,
        severity_threshold=payload.severity_threshold,
        compiled_regex=compiled_re,
    )

    matched = rule.matches(
        source_alias=payload.sample_source,
        source_ip=payload.sample_source,
        app_name=payload.sample_app,
        message=payload.sample_message,
        severity=payload.sample_severity,
    )
    return DropRuleTestResponse(matched=matched, error=None)


# ---------------------------------------------------------------------------
# Drop Rule Presets
# ---------------------------------------------------------------------------

@router.get("/presets", response_model=list[DropPresetResponse])
async def list_drop_presets(user: dict = Depends(get_current_user)) -> list[DropPresetResponse]:
    """Retrieve all available 1-click drop rule presets."""
    presets = get_drop_presets()
    return [DropPresetResponse(**p) for p in presets]


@router.post("/presets/{preset_id}/install", response_model=DropRuleResponse, status_code=status.HTTP_201_CREATED)
async def install_drop_preset(
    preset_id: str,
    user: dict = Depends(get_current_user),
) -> DropRuleResponse:
    """1-click install of a predefined drop rule preset into active drop rules."""
    preset = get_drop_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Drop preset '{preset_id}' not found.",
        )

    msg = preset.get("message_pattern") or "*"
    if preset.get("is_regex") and msg != "*":
        validate_regex_pattern(msg)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _insert(conn):
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO drop_rules (
                source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at
            ) VALUES (?, ?, ?, ?, 1, ?, 0, ?)
            """,
            (
                preset.get("source_pattern"),
                preset.get("app_pattern"),
                msg,
                1 if preset.get("is_regex") else 0,
                preset.get("severity_threshold"),
                now_iso,
            ),
        )
        rule_id = cur.lastrowid
        conn.commit()
        return rule_id

    rule_id = await run_db_query(_insert)
    await asyncio.to_thread(get_drop_filter().reload_rules)

    return DropRuleResponse(
        id=rule_id,
        source_pattern=preset.get("source_pattern"),
        app_pattern=preset.get("app_pattern"),
        message_pattern=msg,
        is_regex=bool(preset.get("is_regex")),
        is_enabled=True,
        severity_threshold=preset.get("severity_threshold"),
        dropped_count=0,
        created_at=now_iso,
    )

