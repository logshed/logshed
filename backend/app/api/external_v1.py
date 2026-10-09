"""
External REST API v1 endpoints for LogShed.
Supports Bearer token authentication, scoped permissions, multi-session maintenance tracking,
system telemetry and metrics, and log and incident queries.
"""

import asyncio
import datetime
import json
import logging
import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import require_api_token, require_auth_or_token, run_db_query
from app.core.config import get_db_path
from app.core.pipeline import (
    get_dropped_by_filter_count,
    get_dropped_count,
    get_ingest_rate,
    get_queue,
)
from app.core.utils import (
    expand_source_aliases,
    format_fts_query,
    parse_iso_to_utc_datetime,
    parse_multi_values,
)
from app.models import (
    ApiTokenVerifyResponse,
    ExternalMaintenanceDisableRequest,
    ExternalMaintenanceDisableResponse,
    ExternalMaintenanceEnableRequest,
    ExternalMaintenanceEnableResponse,
    ExternalMaintenanceStatusResponse,
    LogContextResponse,
    LogEntry,
    LogFacetsResponse,
    LogListResponse,
    PruneResponse,
    StorageMetricItem,
    SystemAlertState,
    SystemMaintenanceMetrics,
    SystemMetricsResponse,
    TopErrorBreakdown,
    TopErrorEntity,
    VacuumResponse,
)
from app.services.maintenance_service import (
    create_maintenance_session,
    disable_maintenance_sessions,
    get_maintenance_status,
)
from app.services.retention import (
    InsufficientDiskSpaceError,
    execute_prune_async,
    execute_vacuum,
    get_effective_retention_days,
)
from app.services.storage_metrics import sample_storage_metrics
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["External API v1"])

# Cooldown tracking for expensive maintenance actions (5 minutes)
_last_prune_time: float = 0.0
_last_vacuum_time: float = 0.0
_cooldown_lock = threading.Lock()
_OPERATION_COOLDOWN_SECONDS: float = 300.0  # 5 minutes

# Telemetry error stats in-memory cache (12-second TTL)
_cached_error_stats: Optional[dict[str, Any]] = None
_cached_error_stats_time: float = 0.0
_error_cache_lock = threading.Lock()
_METRICS_CACHE_TTL: float = 12.0


# ---------------------------------------------------------------------------
# Auth Verification
# ---------------------------------------------------------------------------

@router.get("/auth/verify", response_model=ApiTokenVerifyResponse)
async def verify_token(
    token: dict[str, Any] = Depends(require_api_token([])),
) -> ApiTokenVerifyResponse:
    """Verify API token validity and retrieve its assigned scopes and metadata."""
    return ApiTokenVerifyResponse(
        valid=True,
        name=token["name"],
        scopes=token["scopes"],
        expires_at=token.get("expires_at"),
    )


# ---------------------------------------------------------------------------
# Multi-Session Maintenance Control
# ---------------------------------------------------------------------------

@router.post("/maintenance/enable", response_model=ExternalMaintenanceEnableResponse)
async def enable_maintenance_window(
    payload: ExternalMaintenanceEnableRequest,
    auth: dict[str, Any] = Depends(require_auth_or_token(["maintenance:write"])),
) -> ExternalMaintenanceEnableResponse:
    """
    Start an on-demand maintenance session.
    Silences alert rules and optionally filters incoming errors for the specified duration.
    """
    initiated_by = (
        f"api_token: {auth.get('name', 'token')}"
        if auth.get("auth_type") == "api_token"
        else f"admin: {auth.get('username') or 'session'}"
    )
    token_id = auth.get("id") if auth.get("auth_type") == "api_token" else None

    def _create(conn):
        return create_maintenance_session(
            conn,
            duration_minutes=payload.duration_minutes,
            reason=payload.reason,
            log_handling=payload.log_handling,
            target_app=payload.target_app,
            target_host=payload.target_host,
            initiated_by=initiated_by,
            token_id=token_id,
        )

    session_data = await run_db_query(_create)
    return ExternalMaintenanceEnableResponse(**session_data)


@router.post("/maintenance/disable", response_model=ExternalMaintenanceDisableResponse)
async def disable_maintenance_window(
    payload: Optional[ExternalMaintenanceDisableRequest] = None,
    auth: dict[str, Any] = Depends(require_auth_or_token(["maintenance:write"])),
) -> ExternalMaintenanceDisableResponse:
    """
    Stop an on-demand maintenance window.
    Terminates the specified session, or clears all active sessions if session_id is omitted.
    """
    session_id = payload.session_id if payload else None

    def _disable(conn):
        return disable_maintenance_sessions(conn, session_id=session_id)

    terminated_count, remaining_active = await run_db_query(_disable)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return ExternalMaintenanceDisableResponse(
        active=remaining_active > 0,
        terminated_sessions=terminated_count,
        remaining_active_sessions=remaining_active,
        server_time=now_iso,
    )


@router.get("/maintenance/status", response_model=ExternalMaintenanceStatusResponse)
async def get_maintenance_status_v1(
    auth: dict[str, Any] = Depends(require_auth_or_token(["maintenance:read"])),
) -> ExternalMaintenanceStatusResponse:
    """Query current maintenance window status including all active concurrent sessions."""
    status_obj = await run_db_query(get_maintenance_status)
    return ExternalMaintenanceStatusResponse(
        active=status_obj.active,
        until=status_obj.until,
        remaining_seconds=status_obj.remaining_seconds,
        log_handling=status_obj.log_handling,
        active_sessions_count=status_obj.active_sessions_count,
        sessions=status_obj.sessions,
    )


# ---------------------------------------------------------------------------
# System Telemetry & Metrics
# ---------------------------------------------------------------------------

@router.get("/system/metrics", response_model=SystemMetricsResponse)
async def get_system_metrics(
    token: dict[str, Any] = Depends(require_api_token(["system:read"])),
) -> SystemMetricsResponse:
    """
    Retrieve comprehensive system metrics, throughput, error breakdown, and alert state.
    Serves rolling error stats from an in-memory cache to maintain high polling performance.
    """
    db_path = get_db_path()
    now_mono = time.monotonic()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    since_24h = (now_utc - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    since_1h = (now_utc - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    since_15m = (now_utc - datetime.timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S")

    # In-memory cached error breakdown
    global _cached_error_stats, _cached_error_stats_time
    with _error_cache_lock:
        need_refresh = (
            _cached_error_stats is None
            or (now_mono - _cached_error_stats_time) > _METRICS_CACHE_TTL
        )

    if need_refresh:
        def _fetch_error_aggregates(conn):
            cur = conn.cursor()
            # 24-hour error count
            cur.execute(
                "SELECT COUNT(*) FROM logs WHERE severity <= 3 AND timestamp >= ?",
                (since_24h,),
            )
            count_24h = cur.fetchone()[0]

            # 1-hour error count
            cur.execute(
                "SELECT COUNT(*) FROM logs WHERE severity <= 3 AND timestamp >= ?",
                (since_1h,),
            )
            count_1h = cur.fetchone()[0]

            # Top error apps (24h)
            cur.execute(
                """
                SELECT app_name, COUNT(*) as cnt
                FROM logs
                WHERE severity <= 3 AND timestamp >= ? AND app_name IS NOT NULL AND app_name != ''
                GROUP BY app_name
                ORDER BY cnt DESC
                LIMIT 5
                """,
                (since_24h,),
            )
            top_apps = [TopErrorEntity(name=r["app_name"], count=r["cnt"]) for r in cur.fetchall()]

            # Top error hosts (24h)
            cur.execute(
                """
                SELECT COALESCE(NULLIF(source_alias, ''), source_ip, 'unknown') as host_val, COUNT(*) as cnt
                FROM logs
                WHERE severity <= 3 AND timestamp >= ?
                GROUP BY host_val
                ORDER BY cnt DESC
                LIMIT 5
                """,
                (since_24h,),
            )
            top_hosts = [TopErrorEntity(name=r["host_val"] or "unknown", count=r["cnt"]) for r in cur.fetchall()]

            return {
                "count_24h": count_24h,
                "count_1h": count_1h,
                "top_apps": top_apps,
                "top_hosts": top_hosts,
            }

        fetched_stats = await run_db_query(_fetch_error_aggregates)
        with _error_cache_lock:
            _cached_error_stats = fetched_stats
            _cached_error_stats_time = now_mono

    error_stats = _cached_error_stats or {
        "count_24h": 0,
        "count_1h": 0,
        "top_apps": [],
        "top_hosts": [],
    }

    # Query system settings, alert state, and maintenance status
    def _fetch_system_info(conn):
        cur = conn.cursor()

        # Instance identity
        cur.execute("SELECT key, value FROM system_settings WHERE key IN ('instance_id', 'server_name')")
        settings_map = {r["key"]: r["value"] for r in cur.fetchall()}
        instance_id = settings_map.get("instance_id") or "00000000-0000-0000-0000-000000000000"
        server_name = settings_map.get("server_name") or "LogShed"

        # Alert state sliding 15-minute window
        cur.execute(
            "SELECT COUNT(*) FROM alert_history WHERE triggered_at >= ?",
            (since_15m,),
        )
        recent_count = cur.fetchone()[0]
        recent_active = recent_count > 0

        cur.execute(
            "SELECT triggered_at, rule_name FROM alert_history ORDER BY triggered_at DESC, id DESC LIMIT 1"
        )
        last_row = cur.fetchone()
        last_trig = last_row["triggered_at"] if last_row else None
        last_rule = last_row["rule_name"] if last_row else None

        # Maintenance status
        maint_status = get_maintenance_status(conn)

        return instance_id, server_name, recent_active, recent_count, last_trig, last_rule, maint_status

    (
        instance_id,
        server_name,
        alert_active,
        alert_count,
        alert_last_trig,
        alert_last_rule,
        maint_status,
    ) = await run_db_query(_fetch_system_info)

    # Storage metrics
    storage_info = await asyncio.to_thread(sample_storage_metrics, db_path)
    db_size_bytes = storage_info["db_size_bytes"]
    db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    total_logs = storage_info["total_logs_count"]
    disk_free = storage_info["disk_free_bytes"]
    disk_total = storage_info["disk_total_bytes"]
    disk_used_percent = (
        round(((disk_total - disk_free) / disk_total) * 100, 1) if disk_total > 0 else 0.0
    )

    return SystemMetricsResponse(
        status="ok",
        version=APP_VERSION,
        instance_id=instance_id,
        server_name=server_name,
        ingest_rate=get_ingest_rate(),
        error_count_24h=error_stats["count_24h"],
        error_count_1h=error_stats["count_1h"],
        top_error_breakdown=TopErrorBreakdown(
            top_apps=error_stats["top_apps"],
            top_hosts=error_stats["top_hosts"],
        ),
        alert_state=SystemAlertState(
            active=alert_active,
            recent_firing_count=alert_count,
            last_triggered_at=alert_last_trig,
            last_rule_name=alert_last_rule,
        ),
        queue_depth=get_queue().qsize(),
        dropped_logs=get_dropped_count(),
        dropped_by_filter=get_dropped_by_filter_count(),
        db_size_bytes=db_size_bytes,
        db_size_mb=db_size_mb,
        total_logs_count=total_logs,
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        disk_used_percent=disk_used_percent,
        maintenance=SystemMaintenanceMetrics(
            active=maint_status.active,
            until=maint_status.until,
            remaining_seconds=maint_status.remaining_seconds,
            active_sessions_count=maint_status.active_sessions_count,
            log_handling=maint_status.log_handling,
        ),
    )


# ---------------------------------------------------------------------------
# External Maintenance Operations (Compaction & Pruning)
# ---------------------------------------------------------------------------

@router.post("/system/prune", response_model=PruneResponse)
async def trigger_prune_v1(
    token: dict[str, Any] = Depends(require_api_token(["system:write"])),
) -> PruneResponse:
    """
    Trigger retention pruning and database compaction.
    Enforces a 5-minute cooldown between executions to prevent database lock disruption.
    """
    global _last_prune_time
    now = time.time()
    with _cooldown_lock:
        if (now - _last_prune_time) < _OPERATION_COOLDOWN_SECONDS:
            remaining = int(_OPERATION_COOLDOWN_SECONDS - (now - _last_prune_time))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Prune operation cooldown active. Please wait {remaining} seconds before repeating.",
            )
        _last_prune_time = now

    retention_days = await run_db_query(get_effective_retention_days)
    db_path = get_db_path()

    result = await execute_prune_async(db_path, retention_days=retention_days)
    metrics_raw = result["metrics"]
    metric_item = StorageMetricItem(
        recorded_at=str(metrics_raw["recorded_at"]),
        db_size_bytes=metrics_raw["db_size_bytes"],
        disk_free_bytes=metrics_raw["disk_free_bytes"],
        disk_total_bytes=metrics_raw["disk_total_bytes"],
        total_logs_count=metrics_raw["total_logs_count"],
    )

    return PruneResponse(
        status="ok",
        deleted_logs=result["deleted_logs"],
        deleted_metrics=result["deleted_metrics"],
        metrics=metric_item,
    )


@router.post("/system/vacuum", response_model=VacuumResponse)
async def trigger_vacuum_v1(
    token: dict[str, Any] = Depends(require_api_token(["system:write"])),
) -> VacuumResponse:
    """
    Execute database VACUUM to reclaim host disk space from freelist pages.
    Enforces a 5-minute cooldown between executions.
    """
    global _last_vacuum_time
    now = time.time()
    with _cooldown_lock:
        if (now - _last_vacuum_time) < _OPERATION_COOLDOWN_SECONDS:
            remaining = int(_OPERATION_COOLDOWN_SECONDS - (now - _last_vacuum_time))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Database compaction cooldown active. Please wait {remaining} seconds before repeating.",
            )
        _last_vacuum_time = now

    db_path = get_db_path()
    try:
        result = await execute_vacuum(db_path)
    except InsufficientDiskSpaceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Database compaction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database compaction failed: {e}",
        )

    metrics_raw = result["metrics"]
    metric_item = StorageMetricItem(
        recorded_at=str(metrics_raw["recorded_at"]),
        db_size_bytes=metrics_raw["db_size_bytes"],
        disk_free_bytes=metrics_raw["disk_free_bytes"],
        disk_total_bytes=metrics_raw["disk_total_bytes"],
        total_logs_count=metrics_raw["total_logs_count"],
    )

    return VacuumResponse(
        status="ok",
        previous_size_bytes=result["previous_size_bytes"],
        new_size_bytes=result["new_size_bytes"],
        reclaimed_bytes=result["reclaimed_bytes"],
        metrics=metric_item,
    )


# ---------------------------------------------------------------------------
# Log & Incident Query Endpoints
# ---------------------------------------------------------------------------

@router.get("/logs", response_model=LogListResponse)
async def search_logs_v1(
    query: Optional[str] = Query(None, description="Full-text search query (SQLite FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0 - 7)"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter"),
    to: Optional[str] = Query(None, description="ISO datetime end filter"),
    limit: int = Query(50, ge=1, le=1000, description="Max logs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogListResponse:
    """Search and filter logs with FTS5 expressions, source and app filters, and time ranges."""
    if from_:
        try:
            datetime.datetime.fromisoformat(from_)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="Invalid datetime format. Expected ISO-8601 string.",
            )

    if to:
        try:
            datetime.datetime.fromisoformat(to)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="Invalid datetime format. Expected ISO-8601 string.",
            )

    def _query_db(conn):
        where_clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        is_fts = bool(query and query.strip())
        if is_fts:
            fts_term = format_fts_query(query)
            if not fts_term.strip():
                where_clauses.append("0")
            else:
                where_clauses.append("logs.id IN (SELECT rowid FROM logs_fts WHERE logs_fts MATCH :fts_term)")
                params["fts_term"] = fts_term

        parsed_sources = expand_source_aliases(source, conn=conn)
        if parsed_sources:
            src_placeholders = ", ".join(f":src_{i}" for i in range(len(parsed_sources)))
            where_clauses.append(
                f"(logs.source_alias IN ({src_placeholders}) OR logs.source_ip IN ({src_placeholders}))"
            )
            for i, s in enumerate(parsed_sources):
                params[f"src_{i}"] = s

        parsed_apps = parse_multi_values(app_name)
        if parsed_apps:
            app_placeholders = ", ".join(f":app_{i}" for i in range(len(parsed_apps)))
            where_clauses.append(f"logs.app_name IN ({app_placeholders})")
            for i, a in enumerate(parsed_apps):
                params[f"app_{i}"] = a

        if severity_max is not None:
            where_clauses.append("logs.severity <= :severity_max")
            params["severity_max"] = severity_max

        if from_:
            where_clauses.append("logs.timestamp >= :from_ts")
            params["from_ts"] = from_

        if to:
            where_clauses.append("logs.timestamp <= :to_ts")
            params["to_ts"] = to

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total count query
        count_sql = f"SELECT COUNT(*) FROM logs {where_sql}"
        cursor = conn.cursor()
        cursor.execute(count_sql, {k: v for k, v in params.items() if k not in ("limit", "offset")})
        total = cursor.fetchone()[0]

        select_sql = f"""
            SELECT logs.id, logs.timestamp, logs.received_at, logs.source_ip,
                   logs.source_alias, logs.app_name, logs.facility, logs.severity,
                   logs.message, logs.raw
            FROM logs
            {where_sql}
            ORDER BY logs.timestamp DESC, logs.id DESC
            LIMIT :limit OFFSET :offset
        """
        cursor.execute(select_sql, params)
        rows = cursor.fetchall()
        logs_list = [
            LogEntry(
                id=r["id"],
                timestamp=str(r["timestamp"]),
                received_at=str(r["received_at"]),
                source_ip=r["source_ip"],
                source_alias=r["source_alias"],
                app_name=r["app_name"],
                facility=r["facility"],
                severity=r["severity"],
                message=r["message"],
                raw=r["raw"],
            )
            for r in rows
        ]
        return logs_list, total

    logs_out, total_count = await run_db_query(_query_db)
    return LogListResponse(
        logs=logs_out,
        total=total_count,
        limit=limit,
        offset=offset,
        total_capped=False,
    )


@router.get("/logs/{log_id}/context", response_model=LogContextResponse)
async def get_log_context_v1(
    log_id: int,
    lines: int = Query(10, ge=1, le=50, description="Context lines before and after target log"),
    same_app: bool = Query(False, description="Restrict context to logs sharing the target's app_name"),
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogContextResponse:
    """Fetch symmetrical surrounding context logs before and after a specific log entry."""
    def _fetch(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs WHERE id = ?", (log_id,))
        target_row = cursor.fetchone()
        if not target_row:
            return None, None, [], []

        source_alias = target_row["source_alias"]
        app_name = target_row["app_name"]
        t_time = target_row["timestamp"]
        t_id = target_row["id"]

        app_clause = "AND app_name = ? " if same_app else ""
        app_params = (app_name,) if same_app else ()

        # Fetch lines before (chronologically earlier)
        before_sql = f"""
            SELECT * FROM logs
            WHERE source_alias = ? {app_clause}AND (timestamp < ? OR (timestamp = ? AND id < ?))
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """
        cursor.execute(before_sql, (source_alias, *app_params, t_time, t_time, t_id, lines))
        before_rows = cursor.fetchall()
        before_rows.reverse()

        # Fetch lines after (chronologically later)
        after_sql = f"""
            SELECT * FROM logs
            WHERE source_alias = ? {app_clause}AND (timestamp > ? OR (timestamp = ? AND id > ?))
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
        """
        cursor.execute(after_sql, (source_alias, *app_params, t_time, t_time, t_id, lines))
        after_rows = cursor.fetchall()

        def _to_entry(r):
            return LogEntry(
                id=r["id"],
                timestamp=str(r["timestamp"]),
                received_at=str(r["received_at"]),
                source_ip=r["source_ip"],
                source_alias=r["source_alias"],
                app_name=r["app_name"],
                facility=r["facility"],
                severity=r["severity"],
                message=r["message"],
                raw=r["raw"],
            )

        target_entry = _to_entry(target_row)
        before_entries = [_to_entry(r) for r in before_rows]
        after_entries = [_to_entry(r) for r in after_rows]
        all_entries = before_entries + [target_entry] + after_entries

        return target_entry, before_entries, after_entries, all_entries

    target_entry, before_entries, after_entries, all_entries = await run_db_query(_fetch)
    if not target_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log entry {log_id} not found.",
        )

    return LogContextResponse(
        target_id=log_id,
        logs=all_entries,
        target_log=target_entry,
        before_logs=before_entries,
        after_logs=after_entries,
    )


@router.get("/logs/facets", response_model=LogFacetsResponse)
async def get_log_facets_v1(
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogFacetsResponse:
    """Retrieve distinct sources, applications, and source-to-application relationships."""
    from app.api.logs import get_log_facets
    # Reuse core facets calculation logic
    return await get_log_facets(user={"auth_type": "api_token"})


@router.get("/alerts/history")
async def list_alert_history_v1(
    rule_id: Optional[int] = Query(None, description="Optional alert rule ID filter"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    token: dict[str, Any] = Depends(require_api_token(["alerts:read"])),
) -> list[dict[str, Any]]:
    """Retrieve historical alert firing incidents and AI diagnosis summaries."""
    from app.api.alerts import list_alert_history
    history_resp = await list_alert_history(
        limit=limit,
        offset=offset,
        rule_id=rule_id,
        user={"auth_type": "api_token"},
    )
    return [item.model_dump() for item in history_resp.items]
