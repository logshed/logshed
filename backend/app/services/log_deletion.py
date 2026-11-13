"""
Log deletion service for LogShed.

Provides targeted deletion of single log lines, multiple log lines,
application logs, host logs, and logs bounded by time ranges and criteria.
Coordinates with FTS5 index synchronization, iterative batch execution,
and storage metrics updates.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Union

from app.core.migrations import get_connection
from app.core.pipeline import wait_for_ingest_write
from app.models import LogDeleteRequest
from app.services import log_store
from app.services.log_query import LogFilter
from app.services.storage_metrics import note_logs_deleted

logger = logging.getLogger(__name__)


def deletion_log_filter(criteria: LogDeleteRequest) -> LogFilter:
    """The LogFilter of a delete request. Raises InvalidTimeBound for an unreadable from or to."""
    return LogFilter.parse(
        query=criteria.query,
        sources=criteria.sources,
        apps=criteria.apps,
        severity_max=criteria.severity_max,
        from_=criteria.from_,
        to=criteria.to,
        log_ids=criteria.log_ids,
    )


def checked_deletion_filter(criteria: LogDeleteRequest) -> LogFilter:
    """
    The LogFilter of a delete request, guarding against an unconstrained full-table wipe unless
    delete_all is explicitly True.

    Raises:
        InvalidTimeBound: an unreadable from or to (a ValueError).
        ValueError: if no criteria are provided and delete_all is False.
    """
    log_filter = deletion_log_filter(criteria)
    if log_filter.is_empty and not criteria.delete_all:
        raise ValueError("Deletion request must specify at least one filter criterion or set delete_all=True.")
    return log_filter


def count_matching_logs(db_path: Union[str, Path], criteria: LogDeleteRequest) -> int:
    """
    Count the number of logs matching the deletion criteria without modifying the database.
    Used for UI preview counters and safety confirmation dialogs.
    """
    log_filter = checked_deletion_filter(criteria)
    conn = get_connection(db_path)
    try:
        return log_store.count_matching(conn, log_filter)
    finally:
        conn.close()


async def count_matching_logs_async(db_path: Union[str, Path], criteria: LogDeleteRequest) -> int:
    """Async wrapper executing count_matching_logs on a worker thread."""
    return await asyncio.to_thread(count_matching_logs, db_path, criteria)


def _deletes_everything(criteria: LogDeleteRequest) -> bool:
    """True when the request deletes all logs (delete_all with no filter). Reads no database."""
    if not criteria.delete_all:
        return False
    try:
        return deletion_log_filter(criteria).is_empty
    except ValueError:
        return False


def execute_delete_logs(
    db_path: Union[str, Path],
    criteria: LogDeleteRequest,
    batch_size: int = 5000,
    index_pending: bool = True,
) -> dict[str, Any]:
    """
    Execute deletion of matching logs in iterative chunks to avoid write-lock starvation.
    A filtered delete first drains pending FTS indexing so the logs_ad trigger consistently
    purges all deleted tokens from logs_fts; deleting everything empties logs_fts instead.
    """
    db_path_obj = Path(db_path)

    total_deleted = 0
    log_filter = checked_deletion_filter(criteria)
    conn = get_connection(db_path_obj)
    try:
        cursor = conn.cursor()

        # Pre-drain unindexed logs so logs_ad trigger cleans all target rows from logs_fts
        if index_pending and not log_filter.is_empty:
            from app.services.fts_indexer import index_pending_logs
            try:
                index_pending_logs(db_path_obj)
            except Exception as e:
                logger.warning(f"Failed to index pending logs prior to deletion: {e}")

        note_logs_deleted()
        try:
            if log_filter.is_empty:
                total_deleted = log_store.delete_everything(conn, batch_size, wait_for_ingest_write)
            else:
                total_deleted = log_store.delete(conn, log_filter, batch_size, wait_for_ingest_write)
        finally:
            note_logs_deleted()

        # Compact FTS5 index if a significant volume of rows was deleted, in short merge steps
        if total_deleted >= 5000:
            from app.services.fts_indexer import merge_fts_index
            try:
                merge_fts_index(conn)
            except sqlite3.Error as e:
                logger.warning(f"FTS index compaction warning after deletion: {e}")

        # Passive WAL checkpoint to commit changes
        try:
            cursor.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error as e:
            logger.debug(f"WAL passive checkpoint notice after deletion: {e}")
    finally:
        conn.close()

    # Refresh storage metrics
    try:
        from app.services.storage_metrics import record_metrics
        record_metrics(db_path_obj)
    except Exception as e:
        logger.warning(f"Failed to record storage metrics after log deletion: {e}")

    logger.info(f"Log deletion completed: deleted {total_deleted} logs.")
    return {
        "status": "ok",
        "deleted_count": total_deleted,
        "message": f"Successfully deleted {total_deleted} log record{'s' if total_deleted != 1 else ''}.",
    }


async def execute_delete_logs_async(
    db_path: Union[str, Path],
    criteria: LogDeleteRequest,
    batch_size: int = 5000,
    index_pending: bool = True,
) -> dict[str, Any]:
    """
    Async wrapper executing execute_delete_logs on a worker thread. Deleting everything pauses
    the FTS indexer meanwhile, which would otherwise index the old rows about to be deleted.
    """
    from app.services.fts_indexer import get_fts_worker

    fts_worker = get_fts_worker() if _deletes_everything(criteria) else None
    if fts_worker is not None:
        await fts_worker.pause_indexing()
    try:
        return await asyncio.to_thread(
            execute_delete_logs,
            db_path,
            criteria,
            batch_size=batch_size,
            index_pending=index_pending,
        )
    finally:
        if fts_worker is not None:
            fts_worker.resume_indexing()
