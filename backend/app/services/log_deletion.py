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
from typing import Any, Optional, Union

from app.core.migrations import get_connection
from app.core.pipeline import wait_for_ingest_write
from app.models import LogDeleteRequest
from app.services.log_query import LogFilter, build_where, execute_with_fts_fallback
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


def build_deletion_filter(
    criteria: LogDeleteRequest,
    conn: Optional[sqlite3.Connection] = None,
) -> tuple[str, dict[str, Any]]:
    """
    Build the parameterized WHERE clause and parameters for a delete request (build_where).
    Guards against an unconstrained full-table wipe unless delete_all is explicitly True.

    Raises:
        InvalidTimeBound: an unreadable from or to (a ValueError).
        ValueError: if no criteria are provided and delete_all is False.
    """
    log_filter = deletion_log_filter(criteria)
    if log_filter.is_empty and not criteria.delete_all:
        raise ValueError("Deletion request must specify at least one filter criterion or set delete_all=True.")
    return build_where(conn, log_filter)


def count_matching_logs(db_path: Union[str, Path], criteria: LogDeleteRequest) -> int:
    """
    Count the number of logs matching the deletion criteria without modifying the database.
    Used for UI preview counters and safety confirmation dialogs.
    """
    conn = get_connection(db_path)
    try:
        where_sql, params = build_deletion_filter(criteria, conn=conn)
        cursor = conn.cursor()
        count_sql = f"SELECT COUNT(*) FROM logs {where_sql}"

        def _count(run_params: dict[str, Any]) -> int:
            cursor.execute(count_sql, run_params)
            row = cursor.fetchone()
            return row[0] if row else 0

        return execute_with_fts_fallback(_count, params, criteria.query, empty=0)
    finally:
        conn.close()


async def count_matching_logs_async(db_path: Union[str, Path], criteria: LogDeleteRequest) -> int:
    """Async wrapper executing count_matching_logs on a worker thread."""
    return await asyncio.to_thread(count_matching_logs, db_path, criteria)


def _delete_matching(
    conn: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    criteria: LogDeleteRequest,
    where_sql: str,
    params: dict[str, Any],
    batch_size: int,
) -> int:
    """
    Delete the logs the criteria select, committing each chunk and letting a waiting ingest
    write go between chunks; return how many went.
    """
    total_deleted = 0
    # If direct log IDs are given, chunk them into groups of 500
    if criteria.log_ids:
        chunk_size = 500
        for i in range(0, len(criteria.log_ids), chunk_size):
            chunk = criteria.log_ids[i:i + chunk_size]
            placeholders = ", ".join(f":id_{j}" for j in range(len(chunk)))
            chunk_params = {f"id_{j}": log_id for j, log_id in enumerate(chunk)}
            cursor.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", chunk_params)
            conn.commit()
            total_deleted += cursor.rowcount
            wait_for_ingest_write()
    else:
        # Iterative batch deletion by criteria
        delete_sql = f"""
            DELETE FROM logs WHERE id IN (
                SELECT id FROM logs
                {where_sql}
                LIMIT :batch_limit
            )
        """

        def _delete_batches(run_params: dict[str, Any]) -> int:
            deleted = 0
            while True:
                cursor.execute(delete_sql, {**run_params, "batch_limit": batch_size})
                count = cursor.rowcount
                conn.commit()
                deleted += count
                if count == 0:
                    return deleted
                wait_for_ingest_write()

        total_deleted += execute_with_fts_fallback(_delete_batches, params, criteria.query, empty=0)
    return total_deleted


def _delete_everything(conn: sqlite3.Connection, batch_size: int) -> int:
    """
    Delete every log stored when the delete starts; return how many went.

    logs_fts is emptied in one step ('delete-all') instead of row by row through the logs_ad
    trigger, which is most of the work of a large delete and leaves delete markers that the
    merge afterwards has to rewrite. last_indexed_id goes back to 0 in the same transaction,
    so the trigger does not fire for the rows still to be deleted. Logs that arrive meanwhile
    have higher ids: they are kept, and the indexer, starting again from 0, finds only them
    once the old rows are gone. If the delete stops halfway, the indexer indexes the rows left.
    """
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        conn.execute("INSERT INTO logs_fts(logs_fts) VALUES('delete-all')")
        conn.execute(
            "UPDATE fts_index_state SET last_indexed_id = 0, updated_at = datetime('now') WHERE id = 1"
        )

    deleted = 0
    while True:
        cursor = conn.execute(
            "DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE id <= ? ORDER BY id LIMIT ?)",
            (last_id, batch_size),
        )
        count = cursor.rowcount
        conn.commit()
        deleted += count
        if count == 0:
            return deleted
        wait_for_ingest_write()


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
    conn = get_connection(db_path_obj)
    try:
        where_sql, params = build_deletion_filter(criteria, conn=conn)
        cursor = conn.cursor()

        # Pre-drain unindexed logs so logs_ad trigger cleans all target rows from logs_fts
        if index_pending and where_sql:
            from app.services.fts_indexer import index_pending_logs
            try:
                index_pending_logs(db_path_obj)
            except Exception as e:
                logger.warning(f"Failed to index pending logs prior to deletion: {e}")

        note_logs_deleted()
        try:
            if where_sql:
                total_deleted = _delete_matching(conn, cursor, criteria, where_sql, params, batch_size)
            else:
                total_deleted = _delete_everything(conn, batch_size)
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
