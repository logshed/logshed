"""
Log retention pruning and database cleanup service for LogShed.
"""

import asyncio
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from app.core.config import get_max_retention_days, is_max_retention_days_overridden
from app.core.migrations import get_connection
from app.services.storage_metrics import record_metrics, prune_old_metrics

logger = logging.getLogger(__name__)

def execute_prune(
    db_path: str | Path, retention_days: int = 14, index_pending: bool = True
) -> dict:
    """
    Executes iterative batch pruning, FTS5 index compaction, WAL checkpointing,
    and updates storage metrics.
    
    Returns a dict with:
        deleted_logs: Total number of log rows deleted
        deleted_metrics: Total number of old metric rows deleted
        metrics: Newly recorded storage metrics snapshot
    """
    retention_days = max(1, retention_days)
    total_deleted = 0
    db_path_obj = Path(db_path)

    if index_pending:
        from app.services.fts_indexer import index_pending_logs
        try:
            index_pending_logs(db_path_obj)
        except Exception as e:
            logger.warning(f"Failed to index pending logs prior to prune: {e}")

    conn = get_connection(db_path_obj)
    try:
        cursor = conn.cursor()
        
        # 1. Iterative batch deletion of logs older than retention_days.
        # Defense-in-depth: only delete rows that have already been indexed by FTSIndexWorker
        # to prevent orphaned FTS records or deleting unindexed rows.
        delete_query = """
            DELETE FROM logs WHERE id IN (
                SELECT id FROM logs 
                WHERE timestamp < datetime('now', '-' || ? || ' days') 
                  AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1)
                LIMIT 5000
            )
        """
        while True:
            cursor.execute(delete_query, (retention_days,))
            count = cursor.rowcount
            conn.commit()
            total_deleted += count
            if count == 0:
                break

        # 2. Compact FTS5 virtual table segments
        try:
            cursor.execute("INSERT INTO logs_fts(logs_fts) VALUES('optimize');")
            conn.commit()
        except sqlite3.Error as e:
            logger.warning(f"FTS5 compaction warning: {e}")

        # 3. Checkpoint and truncate WAL
        try:
            cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            row = cursor.fetchone()
            if row:
                busy, log_pages, checkpointed_pages = row[0], row[1], row[2]
                if busy == 1:
                    logger.warning(
                        f"WAL checkpoint(TRUNCATE) blocked by active readers (busy=1, log_pages={log_pages}, checkpointed_pages={checkpointed_pages}). Falling back to PASSIVE checkpoint."
                    )
                    cursor.execute("PRAGMA wal_checkpoint(PASSIVE);")
                    passive_row = cursor.fetchone()
                    if passive_row and passive_row[0] == 1:
                        logger.warning(
                            f"WAL checkpoint(PASSIVE) also blocked (busy=1, log_pages={passive_row[1]}, checkpointed_pages={passive_row[2]})."
                        )
                else:
                    logger.debug(
                        f"WAL checkpoint(TRUNCATE) succeeded (log_pages={log_pages}, checkpointed_pages={checkpointed_pages})."
                    )
        except sqlite3.Error as e:
            logger.warning(f"WAL checkpoint warning: {e}")
        finally:
            cursor.close()
    finally:
        conn.close()

    # 4. Prune storage metrics older than 30 days
    deleted_metrics = prune_old_metrics(db_path_obj)

    # 5. Take fresh storage metrics snapshot
    metrics = record_metrics(db_path_obj)

    logger.info(
        f"Prune completed: deleted {total_deleted} logs, "
        f"{deleted_metrics} old metrics, retention_days={retention_days}"
    )

    return {
        "status": "ok",
        "deleted_logs": total_deleted,
        "deleted_metrics": deleted_metrics,
        "metrics": metrics,
    }


async def execute_prune_async(
    db_path: str | Path, retention_days: int = 14, index_pending: bool = True
) -> dict:
    """Async wrapper executing prune on a thread."""
    return await asyncio.to_thread(execute_prune, db_path, retention_days, index_pending)



def get_effective_retention_days(conn: sqlite3.Connection) -> int:
    """
    Query configured retention_days from system_settings, respecting
    MAX_RETENTION_DAYS override or clamp.
    """
    if is_max_retention_days_overridden():
        return get_max_retention_days()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = 'retention_days'")
        row = cursor.fetchone()
        if row:
            raw_val = row["value"] if isinstance(row, sqlite3.Row) else row[0]
            if raw_val:
                try:
                    val = int(raw_val)
                    max_days = get_max_retention_days()
                    return max(1, min(val, max_days))
                except ValueError:
                    pass
    except Exception as e:
        logger.warning(f"Failed to read retention_days from database, defaulting to 14: {e}")
    return min(14, get_max_retention_days())


class PruneWorker:
    """
    Background worker that runs daily automated retention pruning.
    Executes once every 24 hours using the configured retention_days from system_settings.
    """
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._running = False
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Main loop: run daily retention pruning with exponential backoff on errors."""
        self._running = True
        logger.info("PruneWorker started.")
        backoff = 5.0

        while self._running:
            try:
                retention_days = await asyncio.to_thread(self._get_retention_days)
                await execute_prune_async(self._db_path, retention_days=retention_days)
                backoff = 5.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PruneWorker error, backing off for {backoff}s: {e}")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 3600.0)
                    continue

            # Wait 24 hours (86400 seconds) or until stop signal
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=86400.0)
                break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _get_retention_days(self) -> int:
        """Synchronous query for configured retention_days."""
        conn = None
        try:
            conn = get_connection(self._db_path)
            return get_effective_retention_days(conn)
        except Exception as e:
            logger.warning(f"Failed to connect to database for retention_days: {e}")
            return min(14, get_max_retention_days())
        finally:
            if conn:
                conn.close()

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._stop_event.set()
        logger.info("PruneWorker stopping.")


class InsufficientDiskSpaceError(Exception):
    """Raised when available host disk space is insufficient for database compaction."""
    pass


def get_db_footprint_bytes(db_path: Union[str, Path]) -> int:
    """Calculate the total size of SQLite database, WAL, and SHM files in bytes."""
    db_path_obj = Path(db_path)
    total_size = 0
    for suffix in ["", "-wal", "-shm"]:
        file_path = db_path_obj.with_name(f"{db_path_obj.name}{suffix}")
        try:
            total_size += os.path.getsize(file_path)
        except FileNotFoundError:
            pass
    return total_size


def check_vacuum_headroom(
    db_path: Union[str, Path],
    safe_margin_bytes: int = 100 * 1024 * 1024,
) -> tuple[int, int, int]:
    """
    Verify that host disk has sufficient temporary space for compaction.
    Returns (db_footprint, disk_free, required_headroom).
    Raises InsufficientDiskSpaceError if disk_free < required_headroom.
    """
    db_path_obj = Path(db_path)
    db_size = get_db_footprint_bytes(db_path_obj)

    parent_dir = db_path_obj.parent
    try:
        usage = shutil.disk_usage(parent_dir)
        disk_free = usage.free
    except FileNotFoundError:
        disk_free = 0

    required_headroom = db_size + safe_margin_bytes
    if disk_free < required_headroom:
        raise InsufficientDiskSpaceError(
            f"Insufficient temporary disk headroom available for database compaction. "
            f"Compaction requires at least {required_headroom} bytes free ({db_size} bytes database size + "
            f"{safe_margin_bytes} bytes safe margin), but only {disk_free} bytes are available."
        )
    return db_size, disk_free, required_headroom


async def execute_vacuum(
    db_path: Union[str, Path],
    queue_consumer: Optional[Any] = None,
    fts_worker: Optional[Any] = None,
    safe_margin_bytes: int = 100 * 1024 * 1024,
) -> dict:
    """
    Coordinates on-demand database compaction (VACUUM):
    1. Checks host disk space against database footprint plus safe margin (100 MB).
    2. Pauses QueueConsumer writes while incoming logs continue buffering in _log_queue.
    3. Waits for in-flight writes and FTS indexing to finish, then resets open connections.
    4. Executes PRAGMA wal_checkpoint(TRUNCATE) followed by VACUUM on a worker thread.
    5. Resumes QueueConsumer to drain all buffered logs to the compacted database.
    6. Records a fresh storage metrics snapshot and returns size and space reclaimed.
    """
    db_path_obj = Path(db_path)

    # 1. Free Headroom Guard
    previous_size, _, _ = check_vacuum_headroom(db_path_obj, safe_margin_bytes=safe_margin_bytes)

    # Resolve workers if not passed
    if queue_consumer is None:
        from app.core.pipeline import get_queue_consumer
        queue_consumer = get_queue_consumer()

    if fts_worker is None:
        from app.services.fts_indexer import get_fts_worker
        fts_worker = get_fts_worker()

    # 2. Pause Ingestion Writes & FTS Indexing
    if queue_consumer is not None:
        await queue_consumer.pause_writes()
    if fts_worker is not None:
        await fts_worker.pause_indexing()

    try:
        # 3. Drain & Flush connections
        from app.api.deps import reset_all_db_connections_async
        await reset_all_db_connections_async()

        # 4. Execute Vacuum
        def _vacuum_worker(path: Path) -> None:
            conn = sqlite3.connect(str(path), timeout=60.0)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.execute("VACUUM;")
            finally:
                conn.close()

        await asyncio.to_thread(_vacuum_worker, db_path_obj)
    finally:
        # 5. Resume Ingestion
        if fts_worker is not None:
            fts_worker.resume_indexing()
        if queue_consumer is not None:
            queue_consumer.resume_writes()

    # 6. Record Metrics & calculate reclaimed space
    new_size = get_db_footprint_bytes(db_path_obj)
    reclaimed_bytes = max(0, previous_size - new_size)
    metrics = await asyncio.to_thread(record_metrics, db_path_obj)

    logger.info(
        f"Database compaction complete: previous={previous_size} bytes, "
        f"new={new_size} bytes, reclaimed={reclaimed_bytes} bytes."
    )

    return {
        "status": "ok",
        "previous_size_bytes": previous_size,
        "new_size_bytes": new_size,
        "reclaimed_bytes": reclaimed_bytes,
        "metrics": metrics,
    }


coordinate_vacuum = execute_vacuum
