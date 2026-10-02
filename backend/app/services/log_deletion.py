"""
Log deletion service for LogShed.

Provides targeted deletion of single log lines, multiple log lines,
application logs, host logs, and logs bounded by time ranges and criteria.
Coordinates with FTS5 index synchronization, iterative batch execution,
and storage metrics updates.
"""

import asyncio
import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from app.core.migrations import get_connection
from app.core.utils import (
    escape_fts_tokens,
    expand_source_aliases,
    format_fts_query,
    parse_multi_values,
)
from app.models import LogDeleteRequest

logger = logging.getLogger(__name__)


def build_deletion_filter(
    criteria: LogDeleteRequest,
    conn: Optional[sqlite3.Connection] = None,
) -> tuple[str, dict[str, Any]]:
    """
    Build parameterized WHERE clause and parameter dictionary for log deletion.
    Guards against unconstrained full-table wipe unless delete_all is explicitly True.

    Returns:
        tuple of (where_sql_clause, params_dict)
    Raises:
        ValueError: If no criteria are provided and delete_all is False.
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

    has_filter = False

    # 1. Direct log IDs filter
    if criteria.log_ids:
        has_filter = True
        id_placeholders = ", ".join(f":id_{i}" for i in range(len(criteria.log_ids)))
        where_clauses.append(f"logs.id IN ({id_placeholders})")
        for i, log_id in enumerate(criteria.log_ids):
            params[f"id_{i}"] = log_id

    # 2. Source / Host filter (source_alias or source_ip)
    parsed_sources = expand_source_aliases(criteria.sources, conn=conn)
    if parsed_sources:
        has_filter = True
        src_placeholders = ", ".join(f":src_{i}" for i in range(len(parsed_sources)))
        where_clauses.append(f"(logs.source_alias IN ({src_placeholders}) OR logs.source_ip IN ({src_placeholders}))")
        for i, s in enumerate(parsed_sources):
            params[f"src_{i}"] = s

    # 3. Application / Container filter
    parsed_apps = parse_multi_values(criteria.apps)
    if parsed_apps:
        has_filter = True
        app_placeholders = ", ".join(f":app_{i}" for i in range(len(parsed_apps)))
        where_clauses.append(f"logs.app_name IN ({app_placeholders})")
        for i, a in enumerate(parsed_apps):
            params[f"app_{i}"] = a

    # 4. Severity threshold
    if criteria.severity_max is not None:
        has_filter = True
        where_clauses.append("logs.severity <= :severity_max")
        params["severity_max"] = criteria.severity_max

    # 5. Datetime bounds
    if criteria.from_:
        has_filter = True
        try:
            datetime.datetime.fromisoformat(criteria.from_)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid 'from' datetime format: {criteria.from_}")
        where_clauses.append("logs.timestamp >= :from_time")
        params["from_time"] = criteria.from_

    if criteria.to:
        has_filter = True
        try:
            datetime.datetime.fromisoformat(criteria.to)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid 'to' datetime format: {criteria.to}")
        where_clauses.append("logs.timestamp <= :to_time")
        params["to_time"] = criteria.to

    # 6. FTS search query
    if criteria.query and criteria.query.strip():
        has_filter = True
        fts_term = format_fts_query(criteria.query)
        if not fts_term.strip():
            where_clauses.append("0")
        else:
            where_clauses.append("logs.id IN (SELECT rowid FROM logs_fts WHERE logs_fts MATCH :fts_term)")
            params["fts_term"] = fts_term

    if not has_filter and not criteria.delete_all:
        raise ValueError("Deletion request must specify at least one filter criterion or set delete_all=True.")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, params


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
        try:
            cursor.execute(count_sql, params)
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            if "fts_term" in params and criteria.query:
                fallback_term = escape_fts_tokens(criteria.query)
                params["fts_term"] = fallback_term
                cursor.execute(count_sql, params)
                row = cursor.fetchone()
                return row[0] if row else 0
            raise
    finally:
        conn.close()


async def count_matching_logs_async(db_path: Union[str, Path], criteria: LogDeleteRequest) -> int:
    """Async wrapper executing count_matching_logs on a worker thread."""
    return await asyncio.to_thread(count_matching_logs, db_path, criteria)


def execute_delete_logs(
    db_path: Union[str, Path],
    criteria: LogDeleteRequest,
    batch_size: int = 5000,
    index_pending: bool = True,
) -> dict[str, Any]:
    """
    Execute deletion of matching logs in iterative chunks to avoid write-lock starvation.
    Ensures pending FTS indexes are drained before deletion so the logs_ad trigger
    consistently purges all deleted tokens from logs_fts.
    """
    db_path_obj = Path(db_path)

    # Pre-drain unindexed logs so logs_ad trigger cleans all target rows from logs_fts
    if index_pending:
        from app.services.fts_indexer import index_pending_logs
        try:
            index_pending_logs(db_path_obj)
        except Exception as e:
            logger.warning(f"Failed to index pending logs prior to deletion: {e}")

    total_deleted = 0
    conn = get_connection(db_path_obj)
    try:
        where_sql, params = build_deletion_filter(criteria, conn=conn)
        cursor = conn.cursor()

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
        else:
            # Iterative batch deletion by criteria
            while True:
                delete_sql = f"""
                    DELETE FROM logs WHERE id IN (
                        SELECT id FROM logs
                        {where_sql}
                        LIMIT :batch_limit
                    )
                """
                cursor.execute(delete_sql, {**params, "batch_limit": batch_size})
                count = cursor.rowcount
                conn.commit()
                total_deleted += count
                if count == 0:
                    break

        # Compact FTS5 index if a significant volume of rows was deleted
        if total_deleted >= 5000:
            try:
                cursor.execute("INSERT INTO logs_fts(logs_fts) VALUES('optimize');")
                conn.commit()
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
    """Async wrapper executing execute_delete_logs on a worker thread."""
    return await asyncio.to_thread(
        execute_delete_logs,
        db_path,
        criteria,
        batch_size=batch_size,
        index_pending=index_pending,
    )
