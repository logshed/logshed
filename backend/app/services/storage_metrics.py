"""
Storage metrics collection service for LogShed.
"""
import asyncio
import datetime
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from app.core.migrations import get_connection

logger = logging.getLogger(__name__)


def sample_storage_metrics(db_path: str | Path) -> dict:
    """
    Collects a single storage metrics snapshot.
    """
    db_path_obj = Path(db_path)
    
    # Calculate database footprint
    db_size = 0
    for suffix in ["", "-wal", "-shm"]:
        file_path = db_path_obj.with_name(f"{db_path_obj.name}{suffix}")
        try:
            db_size += os.path.getsize(file_path)
        except FileNotFoundError:
            pass

    # Disk usage
    parent_dir = db_path_obj.parent
    try:
        usage = shutil.disk_usage(parent_dir)
        disk_free = usage.free
        disk_total = usage.total
    except FileNotFoundError:
        # Fallback if parent dir somehow doesn't exist
        disk_free = 0
        disk_total = 0

    # Query log count
    total_logs = 0
    conn = None
    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM logs")
        row = cursor.fetchone()
        if row:
            total_logs = row[0]
    except sqlite3.Error as e:
        logger.error(f"Failed to query log count: {e}")
    finally:
        if conn:
            conn.close()

    return {
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "db_size_bytes": db_size,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "total_logs_count": total_logs
    }


def record_metrics(db_path: str | Path) -> dict:
    """
    Samples and writes storage metrics to the database.
    """
    metrics = sample_storage_metrics(db_path)
    with _snapshot_lock:
        _snapshot_cache[str(db_path)] = (time.monotonic(), dict(metrics))
    
    conn = None
    try:
        conn = get_connection(db_path)
        with conn:
            conn.execute(
                """
                INSERT INTO storage_metrics 
                (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    metrics["recorded_at"],
                    metrics["db_size_bytes"],
                    metrics["disk_free_bytes"],
                    metrics["disk_total_bytes"],
                    metrics["total_logs_count"]
                )
            )
    except sqlite3.Error as e:
        logger.error(f"Failed to record storage metrics: {e}")
    finally:
        if conn:
            conn.close()
        
    return metrics


def prune_old_metrics(db_path: str | Path) -> int:
    """
    Deletes storage metrics older than 30 days.
    """
    conn = None
    try:
        conn = get_connection(db_path)
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM storage_metrics WHERE recorded_at < datetime('now', '-30 days')")
            deleted = cursor.rowcount
            return deleted
    except sqlite3.Error as e:
        logger.error(f"Failed to prune old metrics: {e}")
        return 0
    finally:
        if conn:
            conn.close()


class StorageMetricsWorker:
    """
    Async background worker that samples storage metrics hourly and on manual trigger.
    """
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._running = False
        self._stop_event = asyncio.Event()
        self._manual_trigger = asyncio.Event()
        self._next_sample_time: float = 0.0

    async def run(self) -> None:
        """Main loop: sample hourly, with exponential backoff on errors."""
        self._running = True
        logger.info("StorageMetricsWorker started.")
        backoff = 5.0
        loop = asyncio.get_running_loop()
        
        while self._running:
            try:
                # Record metrics
                await asyncio.to_thread(record_metrics, self._db_path)
                self._next_sample_time = loop.time() + 3600.0
                backoff = 5.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in StorageMetricsWorker loop: {e}. Retrying in {backoff:.1f}s...")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 300.0)
                    continue
            
            # Wait until next scheduled sample time or stop signal
            while self._running:
                delay = self._next_sample_time - loop.time()
                if delay <= 0:
                    break
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=min(delay, 3600.0))
                    # Stop event was signaled
                    break
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._stop_event.set()
        logger.info("StorageMetricsWorker stopping.")

    async def trigger_sample(self) -> dict:
        """Trigger an immediate sample (e.g., after prune). Returns the metrics dict."""
        metrics = await asyncio.to_thread(record_metrics, self._db_path)
        try:
            loop = asyncio.get_running_loop()
            self._next_sample_time = loop.time() + 3600.0
        except RuntimeError:
            pass
        return metrics


# Short-lived snapshot cache for read-only endpoints. sample_storage_metrics runs a full
# SELECT COUNT(*) FROM logs, which is too costly to repeat on every metrics or storage poll.
STORAGE_SNAPSHOT_TTL_SECONDS: float = 15.0
_snapshot_lock = threading.Lock()
_snapshot_cache: dict[str, tuple[float, dict]] = {}


def get_cached_storage_metrics(db_path: str | Path) -> dict:
    """
    Return a storage snapshot no older than STORAGE_SNAPSHOT_TTL_SECONDS.

    The lock is held while sampling, so concurrent callers share one refresh. Snapshots written
    by record_metrics (hourly worker, prune) also refresh the cache.
    """
    key = str(db_path)
    with _snapshot_lock:
        entry = _snapshot_cache.get(key)
        if entry and (time.monotonic() - entry[0]) <= STORAGE_SNAPSHOT_TTL_SECONDS:
            return dict(entry[1])
        snapshot = sample_storage_metrics(db_path)
        _snapshot_cache[key] = (time.monotonic(), snapshot)
        return dict(snapshot)


def clear_storage_snapshot_cache() -> None:
    """Drop cached snapshots (used by tests)."""
    with _snapshot_lock:
        _snapshot_cache.clear()
