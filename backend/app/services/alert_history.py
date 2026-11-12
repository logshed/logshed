"""
Alert history records (alert firings, maintenance windows, daily digests and on-demand analyses)
for the web UI, REST v1 and MCP. REST and MCP pass each item through redact_alert_item.
"""

import sqlite3
from typing import Optional

from app.api.deps import run_db_query
from app.core.redactor import redact
from app.models import AlertHistoryItem, AlertHistoryListResponse

HISTORY_SELECT = """
    SELECT h.id, h.rule_id, h.rule_name, h.channel_id, h.trigger_count,
           h.sample_log, h.sample_log_id, h.incident_summary, h.ai_enrichment, h.ai_model,
           h.ai_audit_id, h.triggered_at,
           a.tokens_in, a.tokens_out, a.tokens_thoughts, a.tokens_used,
           a.prompt_sent, a.system_prompt, a.response_text,
           a.source_alias, a.app_name, a.user_context,
           h.maintenance_session_id,
           m.log_handling AS m_log_handling, m.created_at AS m_created_at,
           m.ended_at AS m_ended_at, m.end_reason AS m_end_reason,
           m.trigger_label AS m_trigger_label, m.target_app AS m_target_app,
           m.target_host AS m_target_host, m.dropped_count AS m_dropped_count
    FROM alert_history h
    LEFT JOIN ai_audit_log a ON h.ai_audit_id = a.id
    LEFT JOIN maintenance_sessions m ON h.maintenance_session_id = m.session_id
"""


def history_item_from_row(r: sqlite3.Row, pending_drops: dict) -> AlertHistoryItem:
    """Build an API history item from a row of `HISTORY_SELECT`."""
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

    return AlertHistoryItem(
        id=r["id"],
        rule_id=r["rule_id"],
        rule_name=r["rule_name"],
        channel_id=r["channel_id"],
        trigger_count=r["trigger_count"] or 1,
        sample_log=r["sample_log"],
        sample_log_id=r["sample_log_id"],
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
        maintenance_session_id=r["maintenance_session_id"],
        maintenance_type=r["m_log_handling"],
        maintenance_started_at=r["m_created_at"],
        maintenance_ended_at=r["m_ended_at"],
        maintenance_end_reason=r["m_end_reason"],
        maintenance_trigger=r["m_trigger_label"],
        maintenance_target_app=r["m_target_app"],
        maintenance_target_host=r["m_target_host"],
        maintenance_dropped_count=(
            (r["m_dropped_count"] or 0) + pending_drops.get(r["maintenance_session_id"], 0)
            if r["maintenance_session_id"] else None
        ),
    )


async def list_alert_history(limit: int, offset: int = 0, rule_id: Optional[int] = None) -> AlertHistoryListResponse:
    """A page of history records, newest first, optionally of one alert rule."""
    def _query(conn):
        from app.services.maintenance_service import get_pending_dropped_counts, sweep_maintenance_sessions_if_needed
        # Write only when there are pending drop counts or overdue sessions, so history reads stay read-only
        sweep_maintenance_sessions_if_needed(conn)
        pending_drops = get_pending_dropped_counts()
        cur = conn.cursor()
        where_sql = "WHERE h.rule_id = ?" if rule_id is not None else ""
        count_where = "WHERE rule_id = ?" if rule_id is not None else ""
        params = [rule_id] if rule_id is not None else []

        cur.execute(f"SELECT COUNT(*) FROM alert_history {count_where}", params)
        total = cur.fetchone()[0]

        cur.execute(
            f"{HISTORY_SELECT} {where_sql} ORDER BY h.triggered_at DESC, h.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        items = [history_item_from_row(r, pending_drops) for r in cur.fetchall()]
        return items, total

    items, total = await run_db_query(_query)
    return AlertHistoryListResponse(items=items, total=total, limit=limit, offset=offset)


async def get_alert_history_item(history_id: int) -> Optional[AlertHistoryItem]:
    """One history record, or None when it does not exist."""
    def _query(conn):
        from app.services.maintenance_service import get_pending_dropped_counts, sweep_maintenance_sessions_if_needed
        sweep_maintenance_sessions_if_needed(conn)
        pending_drops = get_pending_dropped_counts()
        row = conn.execute(f"{HISTORY_SELECT} WHERE h.id = ?", (history_id,)).fetchone()
        return history_item_from_row(row, pending_drops) if row else None

    return await run_db_query(_query)


_ALERT_TEXT_FIELDS = (
    "sample_log",
    "incident_summary",
    "prompt_sent",
    "system_prompt",
    "response_text",
    "user_context",
)


def redact_alert_item(item: AlertHistoryItem) -> AlertHistoryItem:
    """Return a copy of an alert history item with free-text fields passed through the redactor."""
    updates = {
        name: redact(getattr(item, name))
        for name in _ALERT_TEXT_FIELDS
        if getattr(item, name)
    }
    return item.model_copy(update=updates)
