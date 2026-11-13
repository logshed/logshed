"""
System health, storage metrics, and retention maintenance API endpoints for LogShed.
"""

import asyncio
import logging
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.deps import get_current_user, get_optional_user, run_db_query
from app.collectors.syslog import get_udp_receive_errors
from app.core import backup as db_backup
from app.core import lifecycle
from app.core.config import get_db_path, get_max_retention_days
from app.core.loop_lag import get_loop_lag
from app.core.pipeline import get_dropped_count, get_ingest_rate, get_queue

from app.models import (
    HealthResponse,
    UpgradeStatus,
    PrunePreviewResponse,
    PruneResponse,
    StorageMetricItem,
    StorageOverviewResponse,
    VacuumResponse,
    VersionResponse,
)
from app.services.operation_guard import (
    OperationInProgressError,
    cooldown_remaining_seconds,
    mark_operation_success,
    run_exclusive,
)
from app.services.retention import (
    InsufficientDiskSpaceError,
    compute_prune_preview,
    execute_prune_async,
    execute_vacuum,
    get_effective_retention_days,
)
from app.services.storage_metrics import get_cached_storage_metrics
from app.services.version_service import check_for_updates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["System & Maintenance"])


# exclude_unset: anonymous callers get only status, while authenticated callers get every field,
# including udp_receive_errors as null when it is unknown.
@router.get("/health", response_model=HealthResponse, response_model_exclude_unset=True)
async def health_check(request: Request, response: Response) -> HealthResponse:
    """
    Container healthcheck endpoint.
    Verifies SQLite connectivity, in-memory queue depth, dropped log counter, and ingest rate.
    Returns HTTP 503 when the database check fails so Docker container healthcheck detects unhealthy state.
    Unauthenticated callers receive minimal {"status": "ok"} or {"status": "degraded"}.
    Authenticated callers receive detailed queue and ingest metrics.
    While the database is upgraded or after a failed upgrade, everyone gets HTTP 200 with status
    "upgrading" or "migration_failed" and the stage, so a health check does not restart the
    container part way through a backup.
    """
    if not lifecycle.is_ready():
        snap = lifecycle.snapshot()
        upgrade = UpgradeStatus(
            stage=snap.get("stage") or "",
            done=snap.get("done"),
            total=snap.get("total"),
            from_schema=snap.get("from_schema") or 0,
            target_schema=snap.get("target_schema") or 0,
            failed_at=snap.get("failed_at"),
        )
        state = "upgrading" if snap["status"] == lifecycle.UPGRADING else "migration_failed"
        return HealthResponse(status=state, upgrade=upgrade)

    def _ping_db(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        return cursor.fetchone()[0] == 1

    try:
        db_ok = await run_db_query(_ping_db)
        db_status = "ok" if db_ok else "error"
    except Exception:
        db_status = "error"

    overall_status = "ok" if db_status == "ok" else "degraded"
    if db_status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    user = await get_optional_user(request)
    if not user:
        return HealthResponse(status=overall_status)

    queue = get_queue()
    queue_depth = queue.qsize()
    dropped_count = get_dropped_count()
    ingest_rate = get_ingest_rate()

    return HealthResponse(
        status=overall_status,
        db=db_status,
        queue_depth=queue_depth,
        dropped_logs=dropped_count,
        ingest_rate=ingest_rate,
        udp_receive_errors=get_udp_receive_errors(),
        **get_loop_lag(),
    )


def operation_conflict(active: Optional[str] = None) -> HTTPException:
    detail = (
        "A database backup is in progress; try again when it has finished."
        if active == "backup"
        else "A prune or compaction operation is already in progress."
    )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _enforce_cooldown(name: str, label: str) -> None:
    remaining = cooldown_remaining_seconds(name)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{label} cooldown active. Please wait {remaining} seconds before repeating.",
            headers={"Retry-After": str(remaining)},
        )


def metrics_to_item(metrics: Mapping[str, Any]) -> StorageMetricItem:
    """A storage metrics snapshot (a dict or a storage_metrics row) as the API item."""
    return StorageMetricItem(
        recorded_at=str(metrics["recorded_at"]),
        db_size_bytes=metrics["db_size_bytes"],
        disk_free_bytes=metrics["disk_free_bytes"],
        disk_total_bytes=metrics["disk_total_bytes"],
        total_logs_count=metrics["total_logs_count"],
    )


async def run_prune_operation(*, cooldown: bool = False) -> PruneResponse:
    """
    Prune by the saved retention, compact the FTS5 index, truncate the WAL and take a fresh
    storage metrics snapshot. 409 while another prune or compaction runs. With cooldown
    (the external API), 429 within 5 minutes of the last successful prune.
    """
    async def _work() -> dict:
        if cooldown:
            _enforce_cooldown("prune", "Prune operation")
        retention_days = await run_db_query(get_effective_retention_days)
        db_path = get_db_path()
        try:
            result = await execute_prune_async(db_path, retention_days=retention_days)
        except Exception:
            logger.exception("Retention prune failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Prune failed. See server logs for details.",
            )
        if cooldown:
            mark_operation_success("prune")
        return result

    try:
        result = await run_exclusive("prune", _work)
    except OperationInProgressError as e:
        raise operation_conflict(e.active)

    return PruneResponse(
        status="ok",
        deleted_logs=result["deleted_logs"],
        deleted_metrics=result["deleted_metrics"],
        deleted_history=result["deleted_history"],
        deleted_ai_audit=result["deleted_ai_audit"],
        metrics=metrics_to_item(result["metrics"]),
    )


async def run_vacuum_operation(*, cooldown: bool = False) -> VacuumResponse:
    """
    VACUUM the database (writes pause while incoming logs wait in memory). 400 when the disk
    is too full, 409 while another prune or compaction runs. With cooldown (the external API),
    429 within 5 minutes of the last successful compaction.
    """
    db_path = get_db_path()

    async def _work() -> dict:
        if cooldown:
            _enforce_cooldown("vacuum", "Database compaction")
        try:
            result = await execute_vacuum(db_path)
        except InsufficientDiskSpaceError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        except Exception:
            logger.exception("Database compaction failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database compaction failed. See server logs for details.",
            )
        if cooldown:
            mark_operation_success("vacuum")
        return result

    try:
        result = await run_exclusive("vacuum", _work)
    except OperationInProgressError as e:
        raise operation_conflict(e.active)

    return VacuumResponse(
        status="ok",
        previous_size_bytes=result["previous_size_bytes"],
        new_size_bytes=result["new_size_bytes"],
        reclaimed_bytes=result["reclaimed_bytes"],
        metrics=metrics_to_item(result["metrics"]),
    )


@router.get("/maintenance/prune/preview", response_model=PrunePreviewResponse)
async def preview_prune(
    retention_days: Optional[int] = Query(None, description="Retention in days to preview (default: saved value)"),
    user: dict = Depends(get_current_user),
) -> PrunePreviewResponse:
    """
    Read-only preview of what a prune would delete right now for the given retention:
    logs, alert history by kind, AI audit records, and old storage metrics.
    """
    max_days = get_max_retention_days()
    if retention_days is not None and not (1 <= retention_days <= max_days):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"retention_days must be between 1 and {max_days}.",
        )

    def _preview(conn) -> dict:
        days = retention_days if retention_days is not None else get_effective_retention_days(conn)
        return compute_prune_preview(conn, days)

    return PrunePreviewResponse(**await run_db_query(_preview))


@router.post("/maintenance/prune", response_model=PruneResponse)
async def trigger_prune(user: dict = Depends(get_current_user)) -> PruneResponse:
    """
    Manually triggers log and alert history retention pruning, FTS5 index compaction,
    WAL truncation, and takes a fresh storage metrics snapshot.
    Returns 409 while another prune or compaction is running.
    """
    return await run_prune_operation()


@router.post("/system/vacuum", response_model=VacuumResponse)
async def trigger_vacuum(user: dict = Depends(get_current_user)) -> VacuumResponse:
    """
    On-demand database compaction (VACUUM) to reclaim host disk space from freelist pages.
    Pauses SQLite writes while buffering incoming logs in memory, checkpoints WAL,
    and runs VACUUM. Returns 409 while another prune or compaction is running.
    """
    return await run_vacuum_operation()


@router.get("/system/storage", response_model=StorageOverviewResponse)
async def get_storage_overview(user: dict = Depends(get_current_user)) -> StorageOverviewResponse:
    """
    Fetch current disk usage, DB footprint, log count, and up to 30 days of historical storage metrics.
    """
    db_path = get_db_path()
    current = await asyncio.to_thread(get_cached_storage_metrics, db_path)

    def _get_history(conn):
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count
            FROM storage_metrics
            WHERE recorded_at >= datetime('now', '-30 days')
            ORDER BY recorded_at ASC
            """
        )
        rows = cursor.fetchall()
        return [metrics_to_item(r) for r in rows]

    history = await run_db_query(_get_history)
    backups = await asyncio.to_thread(db_backup.list_backups, db_backup.backups_dir(db_path))

    return StorageOverviewResponse(
        db_size_bytes=current["db_size_bytes"],
        disk_free_bytes=current["disk_free_bytes"],
        disk_total_bytes=current["disk_total_bytes"],
        total_logs_count=current["total_logs_count"],
        backups_size_bytes=sum(b.size_bytes for b in backups),
        backups_count=len(backups),
        history=history,
    )


@router.get("/system/version", response_model=VersionResponse)
async def get_version_info(
    refresh: bool = False,
    user: dict | None = Depends(get_optional_user),
) -> VersionResponse:
    """
    Returns current application version and checks GHCR for stable updates.
    Anonymous callers get an empty version and no update check, so the endpoint neither
    reveals the running version nor lets anyone trigger outbound registry requests.
    """
    if refresh and not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    if not user:
        return VersionResponse(current_version="", checked_at=None)
    info = await check_for_updates(force_refresh=refresh)
    return VersionResponse(**info)

