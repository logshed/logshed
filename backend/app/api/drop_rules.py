"""
Drop rules management API endpoints.

Allows configuring, updating, testing, and toggling in-memory log drop rules
to discard repetitive syslog or container noise before persistence.
"""

import asyncio
import datetime
import re
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.models import (
    DropRuleCreate,
    DropRuleResponse,
    DropRuleTestRequest,
    DropRuleTestResponse,
    DropRuleUpdate,
    MessageResponse,
)
from app.services.drop_filter import CompiledDropRule, get_drop_filter
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
