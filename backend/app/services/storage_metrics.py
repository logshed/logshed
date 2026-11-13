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
from typing import Optional

from app.core.migrations import get_connection
from app.services import log_store

logger = logging.getLogger(__name__)


# The log count kept between snapshots, so a snapshot counts only the rows added since the last
# one (a primary key range) instead of every row (about 1 s at 50 million logs). Every path that
# deletes logs calls note_logs_deleted() before and after, which makes the next snapshot count
# every row again; a count taken while the generation changed is not kept.
_count_lock = threading.Lock()
_count_generation = 0
_count_state: dict[str, tuple[int, int, int]] = {}  # db path -> (generation, count, max id)
# The legacy table's count: it takes no new rows after the upgrade to day files, so it changes
# only when logs are deleted
_legacy_state: dict[str, tuple[int, Optional[int]]] = {}  # db path -> (generation, count)


def note_logs_deleted() -> None:
    """Forget the kept log count: rows were or are being deleted."""
    global _count_generation
    with _count_lock:
        _count_generation += 1


def count_logs(conn: sqlite3.Connection, db_path: str | Path) -> int:
    """Exact number of rows in logs, adding the rows since the last count to the kept count."""
    key = str(db_path)
    with _count_lock:
        generation = _count_generation
        state = _count_state.get(key)
    # One read transaction, so the maximum id and the count see the same rows
    conn.execute("BEGIN")
    try:
        max_id = log_store.max_id(conn)
        if state is not None and state[0] == generation and max_id >= state[2]:
            total = state[1] + log_store.count_rows(conn, after_id=state[2])
        else:
            total = log_store.count_rows(conn)
    finally:
        conn.rollback()
    with _count_lock:
        if generation == _count_generation:
            _count_state[key] = (generation, total, max_id)
    return total


def _legacy_count(conn: sqlite3.Connection, db_path: str | Path) -> Optional[int]:
    """The legacy table's row count, kept until logs are deleted (None when the table is gone)."""
    key = str(db_path)
    with _count_lock:
        generation = _count_generation
        state = _legacy_state.get(key)
    if state is not None and state[0] == generation:
        return state[1]
    total = log_store.legacy_count(conn)
    with _count_lock:
        if generation == _count_generation:
            _legacy_state[key] = (generation, total)
    return total


def _file_items(conn: sqlite3.Connection, db_path: str | Path) -> list[dict]:
    """The Storage page's list of day files (and the legacy table), as dicts."""
    items = []
    for f in log_store.files(conn):
        rows = _legacy_count(conn, db_path) if f.state == "legacy" else f.row_count
        items.append({
            "day": f.day, "file_name": f.file_name, "state": f.state,
            "logs": rows or 0, "size_bytes": f.size_bytes, "newest_log_at": f.max_ts,
        })
    return items


def sample_storage_metrics(db_path: str | Path) -> dict:
    """
    Collects a single storage metrics snapshot.
    """
    db_path_obj = Path(db_path)
    
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

    # Query log count, and the day files (their sizes count towards the database's)
    total_logs = 0
    log_files: list[dict] = []
    conn = None
    try:
        conn = get_connection(db_path)
        log_files = _file_items(conn, db_path)
        total_logs = count_logs(conn, db_path)
    except sqlite3.Error as e:
        logger.error(f"Failed to query log count: {e}")
    finally:
        if conn:
            conn.close()
    day_files_size = sum(f["size_bytes"] or 0 for f in log_files)

    # Database footprint, measured after the catalogue refresh above wrote to logs.db
    db_size = 0
    for suffix in ["", "-wal", "-shm"]:
        file_path = db_path_obj.with_name(f"{db_path_obj.name}{suffix}")
        try:
            db_size += os.path.getsize(file_path)
        except FileNotFoundError:
            pass

    return {
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "db_size_bytes": db_size + day_files_size,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "total_logs_count": total_logs,
        "main_db_size_bytes": db_size,
        "day_files_size_bytes": day_files_size,
        "log_files": log_files,
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
    # Seconds between samples, and the wait after an error (doubling up to the maximum)
    SAMPLE_INTERVAL = 3600.0
    ERROR_BACKOFF = 5.0
    ERROR_BACKOFF_MAX = 300.0

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
        backoff = self.ERROR_BACKOFF
        loop = asyncio.get_running_loop()
        
        while self._running:
            try:
                # Record metrics
                await asyncio.to_thread(record_metrics, self._db_path)
                self._next_sample_time = loop.time() + self.SAMPLE_INTERVAL
                backoff = self.ERROR_BACKOFF
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in StorageMetricsWorker loop: {e}. Retrying in {backoff:.1f}s...")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, self.ERROR_BACKOFF_MAX)
                    continue
            
            # Wait until next scheduled sample time or stop signal
            while self._running:
                delay = self._next_sample_time - loop.time()
                if delay <= 0:
                    break
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=min(delay, self.SAMPLE_INTERVAL))
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
            self._next_sample_time = loop.time() + self.SAMPLE_INTERVAL
        except RuntimeError:
            pass
        return metrics


# Short-lived snapshot cache for read-only endpoints, so every metrics or storage poll does
# not repeat the file size, disk usage and log count work.
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
    """Drop cached snapshots and kept log counts (used by tests)."""
    with _snapshot_lock:
        _snapshot_cache.clear()
    with _count_lock:
        _count_state.clear()
        _legacy_state.clear()
