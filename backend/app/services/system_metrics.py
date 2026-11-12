"""
The system metrics of REST v1 (``GET /api/v1/system/metrics``, read by the Home Assistant integration)
and MCP (``get_system_metrics``): log rates, error counts and top offenders, alert and maintenance
state, and storage figures.
"""

import asyncio
import datetime
import threading
import time
from typing import Any, Optional

from app.api.deps import run_db_query
from app.core.config import get_db_path
from app.core.pipeline import (
    get_dropped_by_filter_count,
    get_dropped_count,
    get_ingest_rate,
    get_queue,
)
from app.models import (
    SeverityCounts24h,
    SystemAlertState,
    SystemMaintenanceMetrics,
    SystemMetricsResponse,
    TopErrorBreakdown,
    TopErrorEntity,
)
from app.services.maintenance_service import get_maintenance_status
from app.services.storage_metrics import get_cached_storage_metrics
from app.version import APP_VERSION

# Telemetry error stats in-memory cache (12-second TTL)
_cached_error_stats: Optional[dict[str, Any]] = None
_cached_error_stats_time: float = 0.0
_error_cache_lock = threading.Lock()
_METRICS_CACHE_TTL: float = 12.0

_error_refresh_task: Optional["asyncio.Future[dict[str, Any]]"] = None

# Alert state counts only alert rule firings: maintenance windows, daily digests and on-demand
# analyses also live in alert_history but are not alerts (digest and on-demand rows have no rule_id)
_ALERT_FIRING_FILTER = (
    "maintenance_session_id IS NULL "
    "AND NOT (rule_id IS NULL AND rule_name IN ('Daily Digest', 'On-Demand Analysis'))"
)


def _fetch_error_aggregates(conn) -> dict[str, Any]:
    """Compute the rolling error counts and top offenders in as few passes as possible."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    since_24h = (now_utc - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    since_1h = (now_utc - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.cursor()

    # The error totals and per-severity counts (warning included) share one pass over the
    # 24-hour range of idx_logs_time_sev, so use conditional aggregation
    cur.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN severity <= 3 THEN 1 ELSE 0 END), 0) AS count_24h,
               COALESCE(SUM(CASE WHEN severity <= 3 AND timestamp >= ? THEN 1 ELSE 0 END), 0) AS count_1h,
               COALESCE(SUM(CASE WHEN severity = 0 THEN 1 ELSE 0 END), 0) AS count_emergency,
               COALESCE(SUM(CASE WHEN severity = 1 THEN 1 ELSE 0 END), 0) AS count_alert,
               COALESCE(SUM(CASE WHEN severity = 2 THEN 1 ELSE 0 END), 0) AS count_critical,
               COALESCE(SUM(CASE WHEN severity = 3 THEN 1 ELSE 0 END), 0) AS count_error,
               COALESCE(SUM(CASE WHEN severity = 4 THEN 1 ELSE 0 END), 0) AS count_warning
        FROM logs
        WHERE severity <= 4 AND timestamp >= ?
        """,
        (since_1h, since_24h),
    )
    counts = cur.fetchone()

    # Top error apps (24h). The unary + keeps SQLite on the 24-hour range of idx_logs_time_sev:
    # left to itself it walks every app's range of idx_logs_app_time (1.3 s instead of 0.13 s at
    # 50 million logs).
    cur.execute(
        """
        SELECT app_name, COUNT(*) as cnt
        FROM logs
        WHERE severity <= 3 AND timestamp >= ? AND +app_name IS NOT NULL AND +app_name != ''
        GROUP BY +app_name
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
        "count_24h": counts["count_24h"],
        "count_1h": counts["count_1h"],
        "severity_counts_24h": SeverityCounts24h(
            emergency=counts["count_emergency"],
            alert=counts["count_alert"],
            critical=counts["count_critical"],
            error=counts["count_error"],
            warning=counts["count_warning"],
        ),
        "top_apps": top_apps,
        "top_hosts": top_hosts,
    }


async def _refresh_error_stats() -> dict[str, Any]:
    global _cached_error_stats, _cached_error_stats_time
    fetched = await run_db_query(_fetch_error_aggregates)
    with _error_cache_lock:
        _cached_error_stats = fetched
        _cached_error_stats_time = time.monotonic()
    return fetched


async def _get_error_stats() -> dict[str, Any]:
    """
    Return cached error aggregates, refreshing when stale. Concurrent callers that find the
    cache stale share a single in-flight refresh instead of each running the aggregate queries.
    """
    global _error_refresh_task
    with _error_cache_lock:
        cached = _cached_error_stats
        fresh = cached is not None and (time.monotonic() - _cached_error_stats_time) <= _METRICS_CACHE_TTL
    if fresh:
        return cached  # type: ignore[return-value]

    # Event-loop code runs atomically between awaits, so the in-flight handle needs no extra lock
    task = _error_refresh_task
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.ensure_future(_refresh_error_stats())
        _error_refresh_task = task
    return await asyncio.shield(task)


async def get_system_metrics() -> SystemMetricsResponse:
    """
    Retrieve comprehensive system metrics, throughput, error breakdown, and alert state.
    Serves rolling error stats from an in-memory cache to maintain high polling performance.
    """
    from app.collectors.syslog import get_udp_receive_errors
    from app.core.loop_lag import get_loop_lag

    db_path = get_db_path()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    since_15m = (now_utc - datetime.timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S")

    error_stats = await _get_error_stats()

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
            f"SELECT COUNT(*) FROM alert_history WHERE triggered_at >= ? AND {_ALERT_FIRING_FILTER}",
            (since_15m,),
        )
        recent_count = cur.fetchone()[0]
        recent_active = recent_count > 0

        cur.execute(
            f"SELECT triggered_at, rule_name FROM alert_history WHERE {_ALERT_FIRING_FILTER} "
            "ORDER BY triggered_at DESC, id DESC LIMIT 1"
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
    storage_info = await asyncio.to_thread(get_cached_storage_metrics, db_path)
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
        severity_counts_24h=error_stats["severity_counts_24h"],
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
        udp_receive_errors=get_udp_receive_errors(),
        **get_loop_lag(),
    )
