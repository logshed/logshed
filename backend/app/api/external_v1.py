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
from app.core.redactor import redact
from app.core.pipeline import (
    get_dropped_by_filter_count,
    get_dropped_count,
    get_ingest_rate,
    get_queue,
)
from app.core.utils import (
    expand_source_aliases,
    format_fts_query,
    format_severity_label,
    parse_iso_to_utc_datetime,
    parse_multi_values,
    parse_relative_or_iso_time,
    resolve_severity,
)
from app.models import (
    AlertHistoryItem,
    ApiTokenVerifyResponse,
    ExternalMaintenanceDisableRequest,
    ExternalMaintenanceDisableResponse,
    ExternalMaintenanceEnableRequest,
    ExternalMaintenanceEnableResponse,
    ExternalMaintenanceStatusResponse,
    ExternalLogContextResponse,
    LogCountResponse,
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

from app.api.ingest import router as ingest_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["External API v1"])
router.include_router(ingest_router)

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

def build_log_where_clause(
    conn: Any,
    query: Optional[str] = None,
    source: Optional[list[str]] = None,
    app_name: Optional[list[str]] = None,
    severity_max: Optional[int] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> tuple[str, dict[str, Any]]:
    """Build shared WHERE clause and parameter map for log search and count queries."""
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

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
            f"(logs.source_alias IN ({src_placeholders}) OR (logs.source_ip IN ({src_placeholders}) AND (logs.source_alias IS NULL OR logs.source_alias NOT IN (SELECT source_alias FROM docker_endpoints WHERE source_alias IS NOT NULL)) AND logs.source_ip != 'docker'))"
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

    if from_ts:
        where_clauses.append("logs.timestamp >= :from_ts")
        params["from_ts"] = from_ts

    if to_ts:
        where_clauses.append("logs.timestamp <= :to_ts")
        params["to_ts"] = to_ts

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, params


_INVALID_DATETIME_DETAIL = (
    "Invalid datetime format. Expected ISO-8601 string or relative duration (e.g. '1h', '24h')."
)

_LOG_BASE_COLUMNS = (
    "logs.id, logs.timestamp, logs.received_at, logs.source_ip, "
    "logs.source_alias, logs.app_name, logs.facility, logs.severity, logs.message"
)


def _log_columns(include_raw: bool) -> str:
    """Return the SELECT column list for log rows, selecting the raw packet only when needed."""
    return f"{_LOG_BASE_COLUMNS}, logs.raw" if include_raw else _LOG_BASE_COLUMNS


def _parse_log_filters(
    severity_max: Optional[int],
    severity: Optional[str],
    from_: Optional[str],
    to: Optional[str],
) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Resolve named severity and relative/ISO time bounds, raising HTTP 422 on invalid input."""
    effective_severity_max = severity_max
    if severity is not None:
        try:
            effective_severity_max = resolve_severity(severity)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    if from_:
        try:
            from_ = parse_relative_or_iso_time(from_)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=_INVALID_DATETIME_DETAIL)

    if to:
        try:
            to = parse_relative_or_iso_time(to)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=_INVALID_DATETIME_DETAIL)

    return effective_severity_max, from_, to


def _row_to_log_entry(r: Any, include_raw: bool = True) -> LogEntry:
    """Map a logs table row to a LogEntry (single mapping point for external log endpoints).

    The raw packet is populated only when include_raw is true; otherwise it is None
    and omitted from serialized responses. Message and raw are always passed through
    the redactor because these endpoints serve external clients and AI assistants.
    """
    return LogEntry(
        id=r["id"],
        timestamp=str(r["timestamp"]),
        received_at=str(r["received_at"]),
        source_ip=r["source_ip"],
        source_alias=r["source_alias"] if r["source_alias"] is not None else r["source_ip"],
        app_name=r["app_name"] if r["app_name"] is not None else "unknown",
        facility=r["facility"],
        severity=r["severity"],
        message=redact(r["message"]),
        raw=redact(r["raw"]) if include_raw and r["raw"] is not None else None,
    )


@router.get("/logs", response_model=LogListResponse, response_model_exclude_none=True)
async def search_logs_v1(
    query: Optional[str] = Query(None, description="Full-text search query (SQLite FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0 - 7)"),
    severity: Optional[str] = Query(None, description="Named severity (e.g. 'error', 'warning', 'info') or numeric code"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter or relative duration (e.g. '1h', '24h')"),
    to: Optional[str] = Query(None, description="ISO datetime end filter or relative duration"),
    limit: int = Query(50, ge=1, le=1000, description="Max logs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    include_raw: bool = Query(False, description="Include the raw syslog packet in each entry"),
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogListResponse:
    """Search and filter logs with FTS5 expressions, source and app filters, and time ranges."""
    effective_severity_max, from_, to = _parse_log_filters(severity_max, severity, from_, to)

    def _query_db(conn):
        where_sql, params = build_log_where_clause(
            conn=conn,
            query=query,
            source=source,
            app_name=app_name,
            severity_max=effective_severity_max,
            from_ts=from_,
            to_ts=to,
        )

        # Total count query: same cap as the internal /api/logs endpoint, which avoids
        # full table scans on broad queries.
        count_limit = max(1001, offset + limit + 1)
        count_sql = f"SELECT COUNT(*) FROM (SELECT 1 FROM logs {where_sql} LIMIT :count_limit)"
        cursor = conn.cursor()
        cursor.execute(count_sql, {**params, "count_limit": count_limit})
        total = cursor.fetchone()[0]

        select_sql = f"""
            SELECT {_log_columns(include_raw)}
            FROM logs
            {where_sql}
            ORDER BY logs.timestamp DESC, logs.id DESC
            LIMIT :limit OFFSET :offset
        """
        cursor.execute(select_sql, {**params, "limit": limit, "offset": offset})
        rows = cursor.fetchall()
        logs_list = [_row_to_log_entry(r, include_raw) for r in rows]
        return logs_list, total, bool(total >= count_limit)

    logs_out, total_count, total_capped = await run_db_query(_query_db)
    return LogListResponse(
        logs=logs_out,
        total=total_count,
        limit=limit,
        offset=offset,
        total_capped=total_capped,
    )


@router.get("/logs/count", response_model=LogCountResponse)
async def count_logs_v1(
    query: Optional[str] = Query(None, description="Full-text search query (SQLite FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0 - 7)"),
    severity: Optional[str] = Query(None, description="Named severity (e.g. 'error', 'warning', 'info') or numeric code"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter or relative duration (e.g. '1h', '24h')"),
    to: Optional[str] = Query(None, description="ISO datetime end filter or relative duration"),
    group_by: Optional[str] = Query(None, description="Group breakdown by 'severity', 'app', or 'source'"),
    group_limit: int = Query(25, ge=1, le=200, description="Max groups returned for 'app' and 'source' grouping"),
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogCountResponse:
    """Count logs matching filters with optional grouping by severity, app, or source."""
    effective_severity_max, from_, to = _parse_log_filters(severity_max, severity, from_, to)

    def _count_db(conn):
        where_sql, params = build_log_where_clause(
            conn=conn,
            query=query,
            source=source,
            app_name=app_name,
            severity_max=effective_severity_max,
            from_ts=from_,
            to_ts=to,
        )
        cursor = conn.cursor()
        valid_group = (group_by or "").lower().strip()

        def _total_count() -> int:
            cursor.execute(f"SELECT COUNT(*) FROM logs {where_sql}", params)
            return cursor.fetchone()[0]

        def _limited_group(expr: str, label: str):
            """Run a capped GROUP BY, fetching one extra row to detect truncation."""
            group_sql = f"""
                SELECT {expr} as grp, COUNT(*) as cnt
                FROM logs
                {where_sql}
                GROUP BY grp
                ORDER BY cnt DESC, grp ASC
                LIMIT :group_fetch_limit
            """
            cursor.execute(group_sql, {**params, "group_fetch_limit": group_limit + 1})
            rows = cursor.fetchall()
            truncated = len(rows) > group_limit
            rows = rows[:group_limit]
            breakdown = {r["grp"]: r["cnt"] for r in rows}
            # When every group was returned, the group counts sum to the exact total,
            # so the extra COUNT query is only needed when the breakdown is truncated.
            total = _total_count() if truncated else sum(breakdown.values())
            return total, label, breakdown, truncated

        if valid_group in ("severity", "level"):
            group_sql = f"""
                SELECT logs.severity, COUNT(*) as cnt
                FROM logs
                {where_sql}
                GROUP BY logs.severity
                ORDER BY logs.severity ASC
            """
            cursor.execute(group_sql, params)
            rows = cursor.fetchall()
            breakdown: dict[str, int] = {}
            total = 0
            for r in rows:
                sev_int = r["severity"]
                cnt = r["cnt"]
                total += cnt
                label = format_severity_label(sev_int)
                breakdown[label] = cnt
            return total, "severity", breakdown, False

        elif valid_group in ("app", "app_name"):
            return _limited_group("COALESCE(logs.app_name, 'unknown')", "app")

        elif valid_group in ("source", "source_alias", "host"):
            return _limited_group("COALESCE(logs.source_alias, logs.source_ip, 'unknown')", "source")

        else:
            return _total_count(), None, {}, False

    total, grouped_by_out, breakdown_out, truncated_out = await run_db_query(_count_db)
    return LogCountResponse(
        total=total,
        grouped_by=grouped_by_out,
        breakdown=breakdown_out,
        truncated=truncated_out,
        time_range={"from": from_, "to": to},
    )



@router.get("/logs/{log_id}/context", response_model=ExternalLogContextResponse, response_model_exclude_none=True)
async def get_log_context_v1(
    log_id: int,
    lines: int = Query(10, ge=1, le=50, description="Context lines before and after target log"),
    same_app: bool = Query(False, description="Restrict context to logs sharing the target's app_name"),
    include_raw: bool = Query(False, description="Include the raw syslog packet in each entry"),
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> ExternalLogContextResponse:
    """Fetch symmetrical surrounding context logs before and after a specific log entry."""
    def _fetch(conn):
        cursor = conn.cursor()
        columns = _log_columns(include_raw)
        cursor.execute(f"SELECT {columns} FROM logs WHERE id = ?", (log_id,))
        target_row = cursor.fetchone()
        if not target_row:
            return None, [], []

        source_alias = target_row["source_alias"]
        app_name = target_row["app_name"]
        t_time = target_row["timestamp"]
        t_id = target_row["id"]

        # NULL-safe source matching: "col = NULL" never matches, so NULL aliases
        # are matched via IS NULL and constrained to the same source_ip.
        if source_alias is None:
            source_clause = "source_alias IS NULL AND source_ip = ? "
            source_params: tuple = (target_row["source_ip"],)
        else:
            source_clause = "source_alias = ? "
            source_params = (source_alias,)

        if not same_app:
            app_clause = ""
            app_params: tuple = ()
        elif app_name is None:
            app_clause = "AND app_name IS NULL "
            app_params = ()
        else:
            app_clause = "AND app_name = ? "
            app_params = (app_name,)

        # Fetch lines before (chronologically earlier)
        before_sql = f"""
            SELECT {columns} FROM logs
            WHERE {source_clause}{app_clause}AND (timestamp < ? OR (timestamp = ? AND id < ?))
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """
        cursor.execute(before_sql, (*source_params, *app_params, t_time, t_time, t_id, lines))
        before_rows = cursor.fetchall()
        before_rows.reverse()

        # Fetch lines after (chronologically later)
        after_sql = f"""
            SELECT {columns} FROM logs
            WHERE {source_clause}{app_clause}AND (timestamp > ? OR (timestamp = ? AND id > ?))
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
        """
        cursor.execute(after_sql, (*source_params, *app_params, t_time, t_time, t_id, lines))
        after_rows = cursor.fetchall()

        target_entry = _row_to_log_entry(target_row, include_raw)
        before_entries = [_row_to_log_entry(r, include_raw) for r in before_rows]
        after_entries = [_row_to_log_entry(r, include_raw) for r in after_rows]

        return target_entry, before_entries, after_entries

    target_entry, before_entries, after_entries = await run_db_query(_fetch)
    if not target_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log entry {log_id} not found.",
        )

    return ExternalLogContextResponse(
        target_id=log_id,
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


_ALERT_TEXT_FIELDS = (
    "sample_log",
    "incident_summary",
    "prompt_sent",
    "system_prompt",
    "response_text",
    "user_context",
)


def _redact_alert_item(item: AlertHistoryItem) -> AlertHistoryItem:
    """Return a copy of an alert history item with free-text fields passed through the redactor."""
    updates = {
        name: redact(getattr(item, name))
        for name in _ALERT_TEXT_FIELDS
        if getattr(item, name)
    }
    return item.model_copy(update=updates)


@router.get("/alerts/history", response_model=list[AlertHistoryItem])
async def list_alert_history_v1(
    rule_id: Optional[int] = Query(None, description="Optional alert rule ID filter"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    token: dict[str, Any] = Depends(require_api_token(["alerts:read"])),
) -> list[AlertHistoryItem]:
    """Retrieve historical alert firing incidents and AI diagnosis summaries."""
    from app.api.alerts import list_alert_history
    history_resp = await list_alert_history(
        limit=limit,
        offset=offset,
        rule_id=rule_id,
        user={"auth_type": "api_token"},
    )
    return [_redact_alert_item(item) for item in history_resp.items]
