"""
The system metrics of REST v1 (``GET /api/v1/system/metrics``, read by the Home Assistant integration)
and MCP (``get_system_metrics``): log rates, error counts and top offenders, alert and maintenance
state, storage figures, when each configured source last sent a log, and the newest backup.
"""

import asyncio
import datetime
import logging
import threading
import time
from typing import Any, Optional

from app.api.deps import run_db_query
from app.collectors.docker_collector import effective_source_ip
from app.core.backup import backups_dir, list_backups
from app.core.config import env_docker_disabled, get_db_path
from app.core.day_files import KIND_DOCKER
from app.core.pipeline import (
    get_dropped_by_filter_count,
    get_dropped_count,
    get_ingest_rate,
    get_queue,
)
from app.models import (
    LastBackup,
    SeverityCounts24h,
    SourceLastSeen,
    SystemAlertState,
    SystemMaintenanceMetrics,
    SystemMetricsResponse,
    TopErrorBreakdown,
    TopErrorEntity,
)
from app.services import log_store
from app.services.docker_endpoints import load_docker_endpoints
from app.services.source_activity import get_source_activity, us_to_iso
from app.services.maintenance_service import get_maintenance_status
from app.services.storage_metrics import get_cached_storage_metrics
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

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
    figures = log_store.error_figures(conn, since_1h, since_24h)
    counts = figures.severity_counts
    top_apps = [TopErrorEntity(name=r["app_name"], count=r["cnt"]) for r in figures.top_apps]
    top_hosts = [TopErrorEntity(name=r["host_val"] or "unknown", count=r["cnt"]) for r in figures.top_hosts]

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

        # Configured sources ("docker" is the alias of the first Docker host's containers, not an address)
        aliases = [
            (r["ip"], r["alias"])
            for r in cur.execute("SELECT ip, alias FROM host_aliases WHERE ip != 'docker' ORDER BY ip").fetchall()
        ]
        docker_hosts = [
            (effective_source_ip(r["url"], r["source_alias"], r["id"]), r["name"])
            for r in load_docker_endpoints(conn, enabled_only=True)
            if not (r["id"] == 1 and env_docker_disabled())
        ]

        return (
            instance_id, server_name, recent_active, recent_count, last_trig, last_rule, maint_status,
            aliases, docker_hosts,
        )

    (
        instance_id,
        server_name,
        alert_active,
        alert_count,
        alert_last_trig,
        alert_last_rule,
        maint_status,
        aliases,
        docker_hosts,
    ) = await run_db_query(_fetch_system_info)
    sources = _sources_last_seen(aliases, docker_hosts)
    last_backup = await asyncio.to_thread(_newest_backup, db_path)

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
        sources=sources,
        last_backup=last_backup,
    )


def _sources_last_seen(
    aliases: list[tuple[str, str]], docker_hosts: list[tuple[str, str]]
) -> list[SourceLastSeen]:
    """
    Each host alias and enabled Docker host with when it last sent a log, as Settings -> Sources
    shows them: an alias by its address's newest log of any kind but Docker (container logs from
    that address belong to the Docker host), a Docker host by its containers' newest log.
    """
    activity = get_source_activity()
    seen = activity.by_address(exclude_kinds={KIND_DOCKER})
    sources = [
        SourceLastSeen(name=alias, kind="alias", address=ip, last_seen=us_to_iso(seen.get(ip)))
        for ip, alias in aliases
    ]
    sources += [
        SourceLastSeen(
            name=name, kind="docker", address=address,
            last_seen=us_to_iso(activity.last_seen(address, KIND_DOCKER)),
        )
        for address, name in docker_hosts
    ]
    return sources


def _newest_backup(db_path) -> Optional[LastBackup]:
    """When the newest backup archive was made and its size, or None when there is none or the folder cannot be read."""
    try:
        backups = list_backups(backups_dir(db_path))
    except OSError as exc:
        logger.warning(f"Could not list the backups folder for the metrics: {exc}")
        return None
    if not backups:
        return None
    newest = backups[0]
    return LastBackup(created_at=newest.created_at.isoformat(), size_bytes=newest.size_bytes)
