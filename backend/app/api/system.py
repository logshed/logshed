"""
System health, storage metrics, and retention maintenance API endpoints for LogShed.
"""

import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import get_current_user, get_optional_user, run_db_query
from app.core.config import get_db_path, get_max_retention_days, is_max_retention_days_overridden
from app.core.pipeline import get_dropped_count, get_ingest_rate, get_queue

from app.models import (
    HealthResponse,
    PruneResponse,
    StorageMetricItem,
    StorageOverviewResponse,
    VacuumResponse,
    VersionResponse,
)
from app.services.retention import (
    InsufficientDiskSpaceError,
    execute_prune_async,
    execute_vacuum,
    get_effective_retention_days,
)
from app.services.storage_metrics import sample_storage_metrics
from app.services.version_service import check_for_updates

logger = logging.getLogger(__name__)
_vacuum_lock = asyncio.Lock()

router = APIRouter(tags=["System & Maintenance"])


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health_check(request: Request, response: Response) -> HealthResponse:
    """
    Container healthcheck endpoint.
    Verifies SQLite connectivity, in-memory queue depth, dropped log counter, and ingest rate.
    Returns HTTP 503 when the database check fails so Docker container healthcheck detects unhealthy state.
    Unauthenticated callers receive minimal {"status": "ok"} or {"status": "degraded"}.
    Authenticated callers receive detailed queue and ingest metrics.
    """
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
    )


@router.post("/maintenance/prune", response_model=PruneResponse)
async def trigger_prune(user: dict = Depends(get_current_user)) -> PruneResponse:
    """
    Manually triggers log retention pruning, FTS5 index compaction,
    WAL truncation, and takes a fresh storage metrics snapshot.
    """
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
async def trigger_vacuum(user: dict = Depends(get_current_user)) -> VacuumResponse:
    """
    On-demand database compaction (VACUUM) to reclaim host disk space from freelist pages.
    Pauses SQLite writes while buffering incoming logs in memory, checkpoints WAL,
    and runs VACUUM.
    """
    if _vacuum_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Database compaction is already in progress.",
        )

    async with _vacuum_lock:
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


@router.get("/system/storage", response_model=StorageOverviewResponse)
async def get_storage_overview(user: dict = Depends(get_current_user)) -> StorageOverviewResponse:
    """
    Fetch current disk usage, DB footprint, log count, and up to 30 days of historical storage metrics.
    """
    db_path = get_db_path()
    current = await asyncio.to_thread(sample_storage_metrics, db_path)

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
        return [
            StorageMetricItem(
                recorded_at=str(r["recorded_at"]),
                db_size_bytes=r["db_size_bytes"],
                disk_free_bytes=r["disk_free_bytes"],
                disk_total_bytes=r["disk_total_bytes"],
                total_logs_count=r["total_logs_count"],
            )
            for r in rows
        ]

    history = await run_db_query(_get_history)

    return StorageOverviewResponse(
        db_size_bytes=current["db_size_bytes"],
        disk_free_bytes=current["disk_free_bytes"],
        disk_total_bytes=current["disk_total_bytes"],
        total_logs_count=current["total_logs_count"],
        history=history,
    )


@router.get("/system/version", response_model=VersionResponse)
async def get_version_info(
    refresh: bool = False,
    user: dict | None = Depends(get_optional_user),
) -> VersionResponse:
    """
    Returns current application version and checks GHCR for stable updates.
    """
    if refresh and not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    info = await check_for_updates(force_refresh=refresh)
    return VersionResponse(**info)

