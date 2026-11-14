"""
Log retention pruning and database cleanup service for LogShed.
"""

import asyncio
import datetime
import logging
import os
import shutil
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Optional, Union

from app.services.operation_guard import OperationInProgressError, run_exclusive
from app.core.backup import backups_dir, prune_backups
from app.core.config import get_max_retention_days, is_max_retention_days_overridden
from app.core.migrations import get_connection
from app.core.pipeline import wait_for_ingest_write
from app.services import log_store
from app.services.storage_metrics import note_logs_deleted, record_metrics, prune_old_metrics

logger = logging.getLogger(__name__)

# Delay before the PruneWorker retries when a manual prune or vacuum holds the shared lock
_BUSY_RETRY_SECONDS: float = 60.0


# Prune schedule tracking (process-local, matches the single-process model).
# The PruneWorker runs at startup and then this long after each UTC midnight, so a day file is
# deleted on the day its whole day passes the cutoff (retention counts whole UTC days).
_PRUNE_AFTER_MIDNIGHT_SECONDS: float = 300.0
_schedule_lock = threading.Lock()
_last_prune_at: Optional[datetime.datetime] = None
_next_scheduled_prune_at: Optional[datetime.datetime] = None


def _record_prune_completed() -> None:
    global _last_prune_at
    with _schedule_lock:
        _last_prune_at = datetime.datetime.now(datetime.timezone.utc)


def _set_next_scheduled_prune(delay_seconds: Optional[float]) -> None:
    global _next_scheduled_prune_at
    with _schedule_lock:
        if delay_seconds is None:
            _next_scheduled_prune_at = None
        else:
            _next_scheduled_prune_at = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(seconds=delay_seconds)


def seconds_until_next_prune(now: Optional[datetime.datetime] = None) -> float:
    """Seconds from now to the next scheduled prune: _PRUNE_AFTER_MIDNIGHT_SECONDS after a UTC midnight."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    midnight = datetime.datetime.combine(now.date(), datetime.time(), tzinfo=datetime.timezone.utc)
    next_at = midnight + datetime.timedelta(seconds=_PRUNE_AFTER_MIDNIGHT_SECONDS)
    if next_at <= now:
        next_at += datetime.timedelta(days=1)
    return (next_at - now).total_seconds()


def get_prune_schedule() -> dict[str, Optional[str]]:
    """Return when the last prune finished and when the PruneWorker will next run (if known)."""
    with _schedule_lock:
        return {
            "last_cleanup_at": _last_prune_at.isoformat() if _last_prune_at else None,
            "next_cleanup_at": (
                _next_scheduled_prune_at.isoformat() if _next_scheduled_prune_at else None
            ),
        }


# History rows (alert_history) that a prune with a given cutoff deletes. Non-maintenance rows
# age out by triggered_at. Maintenance rows age out by the session's ended_at, so a window that
# is still running is never deleted. A maintenance row whose session row no longer exists falls
# back to triggered_at.
_HISTORY_DELETE_WHERE = """
    (
        ({a}.maintenance_session_id IS NULL AND {a}.triggered_at < :cutoff)
        OR (
            {a}.maintenance_session_id IS NOT NULL
            AND (
                EXISTS (
                    SELECT 1 FROM maintenance_sessions s
                    WHERE s.session_id = {a}.maintenance_session_id
                      AND s.is_active = 0 AND s.ended_at IS NOT NULL AND s.ended_at < :cutoff
                )
                OR (
                    NOT EXISTS (
                        SELECT 1 FROM maintenance_sessions s
                        WHERE s.session_id = {a}.maintenance_session_id
                    )
                    AND {a}.triggered_at < :cutoff
                )
            )
        )
    )
"""

# Mirrors the kind detection used by the incident history UI.
_HISTORY_KIND_CASE = """
    CASE
        WHEN h.maintenance_session_id IS NOT NULL THEN 'maintenance'
        WHEN h.rule_name = 'Daily Digest' THEN 'digest'
        WHEN h.rule_id IS NULL AND (
            h.rule_name = 'On-Demand Analysis'
            OR (h.ai_audit_id IS NOT NULL AND h.sample_log IS NULL)
        ) THEN 'on_demand'
        ELSE 'alert'
    END
"""

_HISTORY_DELETE_H = _HISTORY_DELETE_WHERE.format(a="h")
_HISTORY_DELETE_K = _HISTORY_DELETE_WHERE.format(a="k")

# AI audit records that go with a prune: those linked to a deleted history row that no kept
# history row still references, plus unreferenced records older than the cutoff.
_AI_AUDIT_LINKED_WHERE = f"""
    a.id IN (
        SELECT h.ai_audit_id FROM alert_history h
        WHERE h.ai_audit_id IS NOT NULL AND {_HISTORY_DELETE_H}
    )
    AND NOT EXISTS (
        SELECT 1 FROM alert_history k
        WHERE k.ai_audit_id = a.id AND NOT {_HISTORY_DELETE_K}
    )
"""
_AI_AUDIT_ORPHAN_WHERE = """
    a.timestamp < :cutoff
    AND NOT EXISTS (SELECT 1 FROM alert_history k WHERE k.ai_audit_id = a.id)
"""



def _prune_history(conn: sqlite3.Connection, cutoff_iso: str) -> tuple[int, int]:
    """
    Delete expired alert history rows, their linked AI audit records, orphaned AI audit
    records older than the cutoff, and ended maintenance sessions older than the cutoff.
    Returns (deleted_history_rows, deleted_ai_audit_rows).
    """
    cursor = conn.cursor()
    params = {"cutoff": cutoff_iso}

    # Collect the AI audit ids that go with the history rows being deleted, before the delete.
    cursor.execute(
        f"""
        SELECT a.id FROM ai_audit_log a
        WHERE {_AI_AUDIT_LINKED_WHERE}
        """,
        params,
    )
    linked_audit_ids = [row[0] for row in cursor.fetchall()]

    cursor.execute(
        f"DELETE FROM alert_history WHERE id IN (SELECT h.id FROM alert_history h WHERE {_HISTORY_DELETE_H})",
        params,
    )
    deleted_history = cursor.rowcount

    deleted_ai_audit = 0
    for i in range(0, len(linked_audit_ids), 500):
        chunk = linked_audit_ids[i : i + 500]
        placeholders = ",".join("?" for _ in chunk)
        cursor.execute(f"DELETE FROM ai_audit_log WHERE id IN ({placeholders})", chunk)
        deleted_ai_audit += cursor.rowcount

    cursor.execute(
        f"DELETE FROM ai_audit_log WHERE id IN (SELECT a.id FROM ai_audit_log a WHERE {_AI_AUDIT_ORPHAN_WHERE})",
        params,
    )
    deleted_ai_audit += cursor.rowcount

    cursor.execute(
        "DELETE FROM maintenance_sessions WHERE is_active = 0 AND ended_at IS NOT NULL AND ended_at < ?",
        (cutoff_iso,),
    )
    conn.commit()
    return deleted_history, deleted_ai_audit


def compute_prune_preview(conn: sqlite3.Connection, retention_days: int) -> dict[str, Any]:
    """
    Read-only counts of what a prune with the given retention_days would delete right now.
    Uses the same predicates as execute_prune, including the FTS-indexed guard for logs.
    """
    retention_days = max(1, retention_days)
    cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)
    cutoff_iso = cutoff_dt.isoformat()
    params = {"cutoff": cutoff_iso}
    cursor = conn.cursor()

    logs = log_store.count_prunable(conn, cutoff_iso)

    cursor.execute("SELECT COUNT(*) FROM storage_metrics WHERE recorded_at < datetime('now', '-30 days')")
    metrics = cursor.fetchone()[0]

    kinds = {"alert": 0, "on_demand": 0, "digest": 0, "maintenance": 0}
    cursor.execute(
        f"""
        SELECT {_HISTORY_KIND_CASE} AS kind, COUNT(*) FROM alert_history h
        WHERE {_HISTORY_DELETE_H}
        GROUP BY kind
        """,
        params,
    )
    for kind, count in cursor.fetchall():
        kinds[kind] = count

    cursor.execute(
        f"""
        SELECT COUNT(*) FROM ai_audit_log a
        WHERE ({_AI_AUDIT_LINKED_WHERE}) OR ({_AI_AUDIT_ORPHAN_WHERE})
        """,
        params,
    )
    ai_audit = cursor.fetchone()[0]

    history_total = sum(kinds.values())
    schedule = get_prune_schedule()
    return {
        "retention_days": retention_days,
        "cutoff": cutoff_iso,
        "logs": logs,
        "history_alerts": kinds["alert"],
        "history_on_demand": kinds["on_demand"],
        "history_digests": kinds["digest"],
        "history_maintenance": kinds["maintenance"],
        "history_total": history_total,
        "metrics": metrics,
        "ai_audit": ai_audit,
        "total": logs + history_total + metrics + ai_audit,
        "last_cleanup_at": schedule["last_cleanup_at"],
        "next_cleanup_at": schedule["next_cleanup_at"],
    }


def execute_prune(
    db_path: str | Path, retention_days: int = 14, index_pending: bool = True
) -> dict:
    """
    Executes iterative batch pruning, FTS5 index compaction, WAL checkpointing,
    expiry of database backups older than 30 days, and updates storage metrics.

    Returns a dict with:
        deleted_logs: Total number of log rows deleted (logs only)
        deleted_metrics: Total number of old metric rows deleted
        deleted_history: Alert history rows deleted (alerts, digests, analyses, maintenance windows)
        deleted_ai_audit: AI audit records deleted (linked to deleted history, plus orphans)
        metrics: Newly recorded storage metrics snapshot
    """
    retention_days = max(1, retention_days)
    total_deleted = 0
    legacy_dropped = False
    deleted_history = 0
    deleted_ai_audit = 0
    db_path_obj = Path(db_path)
    cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)
    cutoff_iso = cutoff_dt.isoformat()

    if index_pending:
        from app.services.fts_indexer import index_pending_logs
        try:
            index_pending_logs(db_path_obj)
        except Exception as e:
            logger.warning(f"Failed to index pending logs prior to prune: {e}")

    conn = get_connection(db_path_obj)
    try:
        cursor = conn.cursor()
        
        # 1. Batch deletion of logs older than retention_days. Only rows the FTS indexer has
        # reached are deleted, so no deleted row leaves its tokens in logs_fts.
        note_logs_deleted()
        try:
            total_deleted = log_store.prune(conn, cutoff_iso, wait_for_ingest_write)
        finally:
            note_logs_deleted()

        # 1a. Once the logs from before the upgrade to day files are all gone, their table goes too
        try:
            legacy_dropped = log_store.drop_empty_legacy(conn)
        except sqlite3.Error as e:
            legacy_dropped = False
            logger.warning(f"Could not drop the emptied table of logs from before 1.4.0: {e}")
        if legacy_dropped:
            logger.info("The table of logs stored before the upgrade to day files is empty, so it is dropped.")

        # 1b. Prune alert history (alerts, digests, on-demand analyses, maintenance windows)
        # older than the cutoff, plus the AI audit records they link to.
        deleted_history, deleted_ai_audit = _prune_history(conn, cutoff_iso)

        # 2. Compact FTS5 index segments in short, separately committed merge steps
        from app.services.fts_indexer import merge_fts_index
        try:
            merge_fts_index(conn)
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

    # 4b. Delete database backups older than 30 days and stale temporary backup files
    try:
        prune_backups(backups_dir(db_path_obj))
    except OSError as e:
        logger.warning(f"Could not expire old database backups: {e}")

    # 5. Take fresh storage metrics snapshot
    metrics = record_metrics(db_path_obj)

    logger.info(
        f"Prune completed: deleted {total_deleted} logs, "
        f"{deleted_history} history rows, {deleted_ai_audit} AI audit rows, "
        f"{deleted_metrics} old metrics, retention_days={retention_days}"
    )

    _record_prune_completed()

    return {
        "status": "ok",
        "deleted_logs": total_deleted,
        "deleted_metrics": deleted_metrics,
        "deleted_history": deleted_history,
        "deleted_ai_audit": deleted_ai_audit,
        "metrics": metrics,
        "legacy_dropped": legacy_dropped,
    }


async def execute_prune_async(
    db_path: str | Path, retention_days: int = 14, index_pending: bool = True
) -> dict:
    """
    Async wrapper executing prune on a thread. When the prune dropped the emptied legacy logs
    table, logs.db is compacted once (it holds only settings and history by then, so this takes
    seconds) if the disk has room; otherwise the space comes back with Compact Database.
    """
    result = await asyncio.to_thread(execute_prune, db_path, retention_days, index_pending)
    if result.get("legacy_dropped"):
        try:
            vacuumed = await execute_vacuum(db_path)
            result["metrics"] = vacuumed["metrics"]
            logger.info(f"Compacted logs.db after dropping the old logs table: {vacuumed['reclaimed_bytes']} bytes reclaimed.")
        except InsufficientDiskSpaceError as e:
            logger.warning(f"logs.db was not compacted after dropping the old logs table, so the space comes back with Compact Database: {e}")
        except Exception as e:
            logger.warning(f"Could not compact logs.db after dropping the old logs table: {e}")
    return result



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
    Background worker that runs automated retention pruning with the configured retention_days:
    once at start-up, then a few minutes after each UTC midnight (seconds_until_next_prune).
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
                try:
                    # run_exclusive keeps the shared lock until the prune thread really finishes,
                    # even when this task is cancelled (shutdown) while the prune is still running
                    await run_exclusive(
                        "prune",
                        lambda: execute_prune_async(self._db_path, retention_days=retention_days),
                    )
                except OperationInProgressError:
                    logger.info(
                        f"PruneWorker: another prune, compaction or backup is running, retrying in {_BUSY_RETRY_SECONDS:.0f}s."
                    )
                    try:
                        _set_next_scheduled_prune(_BUSY_RETRY_SECONDS)
                        await asyncio.wait_for(self._stop_event.wait(), timeout=_BUSY_RETRY_SECONDS)
                        break
                    except asyncio.TimeoutError:
                        continue
                backoff = 5.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"PruneWorker error, backing off for {backoff}s: {e}")
                try:
                    _set_next_scheduled_prune(backoff)
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 3600.0)
                    continue

            # Wait until just after the next UTC midnight, or until stop signal
            try:
                delay = seconds_until_next_prune()
                _set_next_scheduled_prune(delay)
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
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
        _set_next_scheduled_prune(None)
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


def get_db_live_bytes(db_path: Union[str, Path]) -> int:
    """
    Bytes of the database's pages in use (its free pages left out): what VACUUM copies and then
    writes again. After the old logs table is dropped the file keeps its size, but this falls to
    what settings and history hold. The file's footprint when the database cannot be read.
    """
    try:
        with closing(sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=5.0)) as conn:
            pages, free, size = (conn.execute(f"PRAGMA {name}").fetchone()[0] for name in ("page_count", "freelist_count", "page_size"))
        return (pages - free) * size
    except sqlite3.Error:
        return get_db_footprint_bytes(db_path)


def sqlite_temp_dir() -> Path:
    """
    The folder SQLite's unix VFS writes temporary files to: the first of SQLITE_TMPDIR, TMPDIR,
    /var/tmp, /usr/tmp and /tmp that is a folder this process can write to. The Docker image sets
    SQLITE_TMPDIR to a folder on the data volume (entrypoint.sh).
    """
    for candidate in (os.environ.get("SQLITE_TMPDIR"), os.environ.get("TMPDIR"), "/var/tmp", "/usr/tmp", "/tmp"):
        if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK | os.X_OK):
            return Path(candidate)
    return Path(".")


def _filesystem_id(path: Path) -> int:
    return os.stat(path).st_dev


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except FileNotFoundError:
        return 0


def check_vacuum_headroom(
    db_path: Union[str, Path],
    safe_margin_bytes: int = 100 * 1024 * 1024,
) -> tuple[int, int, int]:
    """
    Verify there is room for compaction. VACUUM builds a copy of the database's pages in use in
    SQLite's temporary folder, then writes each of them again into the WAL beside the database, so
    each needs about the size of the live data (get_db_live_bytes); on one disk, twice that.
    Returns (db_footprint, disk_free, required_headroom) for the database's disk.
    Raises InsufficientDiskSpaceError if a disk has less free space than it needs.
    """
    db_path_obj = Path(db_path)
    db_size = get_db_live_bytes(db_path_obj)
    db_dir = db_path_obj.parent
    temp_dir = sqlite_temp_dir()

    try:
        same_disk = _filesystem_id(db_dir) == _filesystem_id(temp_dir)
    except FileNotFoundError:
        same_disk = True

    if same_disk:
        checks = [(db_dir, 2 * db_size + safe_margin_bytes, "the temporary copy and the write-ahead log")]
    else:
        checks = [
            (db_dir, db_size + safe_margin_bytes, "the write-ahead log"),
            (temp_dir, db_size + safe_margin_bytes, "the temporary copy"),
        ]

    for folder, needed, purpose in checks:
        free = _free_bytes(folder)
        if free < needed:
            raise InsufficientDiskSpaceError(
                f"Insufficient temporary disk headroom available for database compaction. "
                f"Compaction needs {needed} bytes free in {folder} for {purpose} "
                f"({'twice ' if same_disk else ''}the database's live data of {db_size} bytes, "
                f"plus a safe margin of {safe_margin_bytes} bytes), but only {free} bytes are available."
            )
    return db_size, _free_bytes(db_dir), checks[0][1]


async def execute_vacuum(
    db_path: Union[str, Path],
    queue_consumer: Optional[Any] = None,
    fts_worker: Optional[Any] = None,
    safe_margin_bytes: int = 100 * 1024 * 1024,
) -> dict:
    """
    Coordinates on-demand database compaction (VACUUM):
    1. Checks free disk space for the temporary copy and the WAL (about twice the database, see check_vacuum_headroom).
    2. Pauses QueueConsumer writes while incoming logs continue buffering in _log_queue.
    3. Waits for in-flight writes and FTS indexing to finish, then resets open connections.
    4. Executes PRAGMA wal_checkpoint(TRUNCATE), VACUUM, then another checkpoint so logs.db shrinks at once.
    5. Resumes QueueConsumer to drain all buffered logs to the compacted database.
    6. Records a fresh storage metrics snapshot and returns size and space reclaimed.
    """
    db_path_obj = Path(db_path)

    # 1. Free Headroom Guard
    check_vacuum_headroom(db_path_obj, safe_margin_bytes=safe_margin_bytes)
    previous_size = get_db_footprint_bytes(db_path_obj)

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
                # In WAL mode VACUUM writes the repacked pages to the WAL, and logs.db shrinks
                # only when they are checkpointed. Closing does that only for the last open
                # connection, and a small result never triggers the automatic checkpoint.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
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
