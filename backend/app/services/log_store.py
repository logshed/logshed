"""
The log store: every read and write of stored logs goes through these functions, so the rest of
the app never runs SQL against the ``logs`` table or its full-text index.

Each function takes a connection to the main database (``logs.db``) and works on the calling
thread; callers run it through ``run_db_query``, ``run_search_query`` or ``asyncio.to_thread``
as before. Functions that return rows need a connection with ``sqlite3.Row`` rows (as
``run_db_query`` gives). Rows come back as mappings with the stored column names (``id``, ``timestamp``,
``received_at``, ``source_ip``, ``source_alias``, ``app_name``, ``facility``, ``severity``,
``message`` and, when asked for, ``raw``), with times as UTC ISO strings.

Helpers the store builds on: ``log_query`` turns a ``LogFilter`` into SQL and runs full-text
searches newest first, and ``fts_indexer`` keeps the full-text index up to date.
"""

import contextlib
import datetime
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.core.day_files import LEGACY_DAY, RAW_SQL, day_start_us, iso_sql, iso_to_us
from app.core.utils import DOCKER_ALIASES_SQL, format_severity_label
from app.services import day_store
from app.services.log_query import (
    LogFilter,
    build_day_where,
    build_where,
    context_source_clause,
    docker_aliases,
    execute_with_fts_fallback,
    expand_sources,
    log_columns,
    notify_fts_worker,
    run_newest_first,
)

# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_INSERT_COLUMNS = "timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw"
_ROW_PLACEHOLDER = "(?, ?, ?, ?, ?, ?, ?, ?, ?)"
# Rows per multi-row INSERT: 9 parameters each stays far below SQLite's parameter limit
INSERT_CHUNK_ROWS = 500


def row_values(entry: dict) -> list:
    """Values for one stored row, in column order, with the defaults a missing field gets."""
    return [
        entry.get("timestamp"),
        entry.get("received_at"),
        entry.get("source_ip", ""),
        entry.get("source_alias", ""),
        entry.get("app_name", ""),
        entry.get("facility", 1),
        entry.get("severity", 6),
        entry.get("message", ""),
        entry.get("raw", ""),
    ]


def _writes_day_files(conn: sqlite3.Connection) -> bool:
    """True from schema 4 on: new logs go to day files. Older schemas (in tests) write the logs table."""
    return day_store.has_catalogue(conn)


def insert_rows(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """
    Store entries (dicts with the row fields) and set each entry's "id". From schema 4 the rows
    go to the day file of their received_at day (UTC), each file's rows committed together;
    entries that already have an id (stored by an earlier attempt) are skipped, so a batch can
    be retried after a failure part way. On an older schema they go to the logs table in
    multi-row INSERTs, and the caller commits or rolls back the whole batch.
    """
    if _writes_day_files(conn):
        day_store.write_rows(conn, entries)
        return
    cursor = conn.cursor()
    for i in range(0, len(entries), INSERT_CHUNK_ROWS):
        chunk = entries[i:i + INSERT_CHUNK_ROWS]
        placeholders = ", ".join([_ROW_PLACEHOLDER] * len(chunk))
        params: list = []
        for entry in chunk:
            params.extend(row_values(entry))
        cursor.execute(f"INSERT INTO logs ({_INSERT_COLUMNS}) VALUES {placeholders} RETURNING id", params)
        returned_rows = cursor.fetchall()
        if len(returned_rows) != len(chunk):
            raise RuntimeError(
                f"Returned ID count ({len(returned_rows)}) does not match chunk size ({len(chunk)})"
            )
        for entry, (row_id,) in zip(chunk, returned_rows):
            entry["id"] = row_id


def insert_rows_one_by_one(
    conn: sqlite3.Connection, entries: list[dict], repair: Callable[[dict], None]
) -> tuple[list[dict], list[tuple[dict, Exception]]]:
    """
    Store entries one at a time, each in its own savepoint, so one bad row does not stop the
    rest: a row that fails with UnicodeEncodeError is repaired (repair changes the entry in
    place) and tried once more, and a row that still fails is skipped. A busy or locked database
    (OperationalError) rolls back and is raised; with day files, the files committed before it
    keep their rows and ids, so a retry with insert_rows stores each row once. Returns the
    stored entries and the skipped entries with their errors.
    """
    if _writes_day_files(conn):
        return day_store.write_rows_one_by_one(conn, entries, repair)
    stored: list[dict] = []
    skipped: list[tuple[dict, Exception]] = []
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        for entry in entries:
            conn.execute("SAVEPOINT log_row")
            try:
                try:
                    entry["id"] = _insert_one(conn, entry)
                except UnicodeEncodeError:
                    repair(entry)
                    entry["id"] = _insert_one(conn, entry)
                stored.append(entry)
            except sqlite3.OperationalError:
                raise
            except Exception as row_error:
                conn.execute("ROLLBACK TO log_row")
                skipped.append((entry, row_error))
            conn.execute("RELEASE log_row")
        conn.commit()
    except BaseException:
        with contextlib.suppress(Exception):
            conn.rollback()
        for entry in stored:
            entry.pop("id", None)
        raise
    return stored, skipped


def _insert_one(conn: sqlite3.Connection, entry: dict) -> int:
    rows = conn.execute(
        f"INSERT INTO logs ({_INSERT_COLUMNS}) VALUES {_ROW_PLACEHOLDER} RETURNING id", row_values(entry)
    ).fetchall()
    return rows[0][0]


def relabel_host(
    conn: sqlite3.Connection,
    source_ip: str,
    target_alias: str,
    *,
    only_alias: Optional[str] = None,
    keep_aliases: tuple[str, ...] = (),
    batch_size: int = 1000,
) -> int:
    """
    Set the host name of stored logs from source_ip to target_alias, newest first (day files from
    the newest, then the legacy table), in batches of batch_size committed separately. With
    only_alias, only rows named only_alias change; otherwise every row not already named
    target_alias, except rows named one of keep_aliases. Returns the number of rows changed.
    """
    total = 0
    if only_alias is not None:
        day_sql, day_params = "source_ip = ? AND host_id = (SELECT id FROM hosts WHERE name = ?)", (source_ip, only_alias)
    else:
        kept = (target_alias, *keep_aliases)
        day_sql = f"source_ip = ? AND host_id NOT IN (SELECT id FROM hosts WHERE name IN ({','.join('?' * len(kept))}))"
        day_params = (source_ip, *kept)
    for day, day_conn in day_store.open_day_connections(conn):
        # The figures are rewritten even when a batch fails part way (refresh_figures skips a file not sealed)
        try:
            total += day_store.set_names(day_conn, "host_id", day_sql, day_params, target_alias, batch_size, _pause)
        finally:
            day_store.refresh_figures(conn, day)
    if not _has_legacy_table(conn):
        return total
    if only_alias is not None:
        alias_sql, alias_params = "+source_alias = ?", (only_alias,)
    elif keep_aliases:
        placeholders = ",".join("?" for _ in keep_aliases)
        alias_sql = f"+source_alias IS NULL OR (+source_alias != ? AND +source_alias NOT IN ({placeholders}))"
        alias_params = (target_alias, *keep_aliases)
    else:
        alias_sql, alias_params = "+source_alias != ? OR +source_alias IS NULL", (target_alias,)
    return total + _keyset_alias_backfill(conn, source_ip, alias_sql, alias_params, target_alias, batch_size)


def _pause() -> None:
    """A moment between relabel batches, so ingest and readers get the files in between."""
    time.sleep(0.005)


def _has_legacy_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'logs'").fetchone() is not None


def _keyset_alias_backfill(
    conn: sqlite3.Connection,
    source_ip: str,
    alias_filter_sql: str,
    alias_filter_params: tuple,
    target_alias: str,
    batch_size: int,
) -> int:
    """
    Set source_alias = target_alias on rows with this source_ip that match alias_filter_sql,
    newest first, in batches of batch_size committed separately.

    Each batch starts below the lowest id of the previous one (keyset batching), so rows
    already updated are never walked again. alias_filter_sql writes the column as
    `+source_alias`: the unary plus keeps SQLite on the (source_ip, rowid) index, so each
    batch is a range seek instead of a scan of every remaining row for that alias.
    """
    cursor = conn.cursor()
    where_sql = f"source_ip = ? AND ({alias_filter_sql})"
    where_params = (source_ip, *alias_filter_params)
    upper_id: Optional[int] = None
    total = 0
    while True:
        bound_sql = "" if upper_id is None else " AND id < ?"
        bound_params: tuple = () if upper_id is None else (upper_id,)
        cursor.execute(
            f"""
            SELECT MIN(id), COUNT(*) FROM (
                SELECT id FROM logs WHERE {where_sql}{bound_sql} ORDER BY id DESC LIMIT ?
            )
            """,
            (*where_params, *bound_params, batch_size),
        )
        low_id, count = cursor.fetchone()
        if not count:
            break
        cursor.execute(
            f"UPDATE logs SET source_alias = ? WHERE {where_sql} AND id >= ?{bound_sql}",
            (target_alias, *where_params, low_id, *bound_params),
        )
        total += max(cursor.rowcount, 0)
        conn.commit()
        if count < batch_size:
            break
        upper_id = low_id
        time.sleep(0.005)
    return total


def relabel_docker_app(
    conn: sqlite3.Connection,
    raw_name: str,
    target_name: str,
    source_ip: str = "docker",
    source_alias: Optional[str] = None,
    batch_size: int = 1000,
) -> None:
    """
    Rename a Docker app (container) from raw_name to target_name on stored logs from source_ip,
    in batches committed separately. With source_alias, only that Docker host's rows change;
    otherwise rows under any Docker host name, so syslog rows from a shared address keep theirs.
    """
    if source_alias:
        hosts_sql, host_params = "host_id = (SELECT id FROM hosts WHERE name = ?)", (source_alias,)
    else:
        names = docker_aliases(conn)
        hosts_sql = f"host_id IN (SELECT id FROM hosts WHERE name IN ({','.join('?' * len(names))}))"
        host_params = tuple(names)
    day_sql = f"source_ip = ? AND app_id = (SELECT id FROM apps WHERE name = ?) AND {hosts_sql}"
    for day, day_conn in day_store.open_day_connections(conn):
        try:
            day_store.set_names(day_conn, "app_id", day_sql, (source_ip, raw_name, *host_params), target_name, batch_size, _pause)
        finally:
            day_store.refresh_figures(conn, day)
    if not _has_legacy_table(conn):
        return
    cursor = conn.cursor()
    if source_alias:
        update_query = """
            UPDATE logs SET app_name = ?
            WHERE source_ip = ? AND app_name = ? AND source_alias = ? AND id IN (
                SELECT id FROM logs
                WHERE source_ip = ? AND app_name = ? AND source_alias = ?
                ORDER BY id DESC
                LIMIT ?
            )
        """
        params: tuple = (target_name, source_ip, raw_name, source_alias, source_ip, raw_name, source_alias, batch_size)
    else:
        update_query = f"""
            UPDATE logs SET app_name = ?
            WHERE source_ip = ? AND app_name = ?
              AND source_alias IN ({DOCKER_ALIASES_SQL})
              AND id IN (
                SELECT id FROM logs
                WHERE source_ip = ? AND app_name = ?
                  AND source_alias IN ({DOCKER_ALIASES_SQL})
                ORDER BY id DESC
                LIMIT ?
            )
        """
        params = (target_name, source_ip, raw_name, source_ip, raw_name, batch_size)

    while True:
        cursor.execute(update_query, params)
        count = cursor.rowcount
        conn.commit()
        if count < batch_size:
            break
        time.sleep(0.005)


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------

# Log ids per DELETE when a delete names its logs by id alone
_DELETE_ID_CHUNK = 500


def delete(
    conn: sqlite3.Connection,
    log_filter: LogFilter,
    batch_size: int,
    between_batches: Callable[[], None],
) -> int:
    """
    Delete the logs log_filter selects (it must not be empty), committing each batch and
    calling between_batches after each one, so a waiting ingest write can go first. Logs
    named by id are deleted only when they also match the filter's other criteria: the same
    logs count_matching counts. Returns how many went.
    """
    found = _parts_for(conn, log_filter)
    if not _only_legacy(found):
        return _delete_in_parts(conn, found, log_filter, batch_size, between_batches)
    if log_filter.log_ids and log_filter == LogFilter(log_ids=log_filter.log_ids):
        return _delete_ids(conn, log_filter.log_ids, between_batches)

    where_sql, params = build_where(conn, log_filter)
    return _delete_legacy_where(conn, where_sql, params, log_filter.query, batch_size, between_batches)


def _delete_in_parts(
    conn: sqlite3.Connection,
    found: list[day_store.Part],
    log_filter: LogFilter,
    batch_size: int,
    between_batches: Callable[[], None],
) -> int:
    """delete() over several parts (or a day file), each part in its own batches."""
    by_ids = bool(log_filter.log_ids) and log_filter == LogFilter(log_ids=log_filter.log_ids)
    query = _PartQuery.prepare(conn, log_filter)
    deleted = 0
    for part in found:
        if part.legacy:
            if by_ids:
                ids = [i for i in log_filter.log_ids if part.min_id <= i <= part.max_id]  # type: ignore[operator]
                deleted += _delete_ids(conn, tuple(ids), between_batches)
            else:
                where_sql, params = query.where(part)
                deleted += _delete_legacy_where(conn, where_sql, params, log_filter.query, batch_size, between_batches)
            continue
        # A sealed file's figures are rewritten even when the delete stops part way, and also when
        # the file was sealed after this delete began (refresh_figures checks its state itself)
        try:
            if by_ids:
                ids = [i for i in log_filter.log_ids if part.min_id <= i <= part.max_id]  # type: ignore[operator]
                deleted += day_store.delete_ids(part.conn, ids, between_batches)
            else:
                where_sql, params = query.where(part)
                deleted += execute_with_fts_fallback(
                    lambda p, c=part.conn, w=where_sql: day_store.delete_rows(c, w, p, batch_size, between_batches),
                    params,
                    log_filter.query,
                    empty=0,
                )
        finally:
            day_store.refresh_figures(conn, part.day)
    return deleted


def _delete_legacy_where(
    conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    query: Optional[str],
    batch_size: int,
    between_batches: Callable[[], None],
) -> int:
    cursor = conn.cursor()
    delete_sql = f"DELETE FROM logs WHERE id IN (SELECT id FROM logs {where_sql} LIMIT :batch_limit)"

    def _delete_batches(run_params: dict[str, Any]) -> int:
        deleted = 0
        while True:
            cursor.execute(delete_sql, {**run_params, "batch_limit": batch_size})
            count = cursor.rowcount
            conn.commit()
            deleted += count
            if count == 0:
                return deleted
            between_batches()

    return execute_with_fts_fallback(_delete_batches, params, query, empty=0)


def _delete_ids(conn: sqlite3.Connection, log_ids: tuple[int, ...], between_batches: Callable[[], None]) -> int:
    """Delete the logs with these ids, a chunk per commit; return how many went."""
    cursor = conn.cursor()
    deleted = 0
    for i in range(0, len(log_ids), _DELETE_ID_CHUNK):
        chunk = log_ids[i:i + _DELETE_ID_CHUNK]
        placeholders = ", ".join(f":id_{j}" for j in range(len(chunk)))
        chunk_params = {f"id_{j}": log_id for j, log_id in enumerate(chunk)}
        cursor.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", chunk_params)
        conn.commit()
        deleted += cursor.rowcount
        between_batches()
    return deleted


def delete_everything(conn: sqlite3.Connection, batch_size: int, between_batches: Callable[[], None]) -> int:
    """
    Delete every log stored when the delete starts; return how many went.

    logs_fts is emptied in one step ('delete-all') instead of row by row through the logs_ad
    trigger, which is most of the work of a large delete and leaves delete markers that the
    merge afterwards has to rewrite. last_indexed_id goes back to 0 in the same transaction,
    so the trigger does not fire for the rows still to be deleted. Logs that arrive meanwhile
    have higher ids: they are kept, and the indexer, starting again from 0, finds only them
    once the old rows are gone. If the delete stops halfway, the indexer indexes the rows left.

    Day files of days before yesterday (UTC) are deleted whole. Today's and yesterday's may still
    receive a batch that was waiting in the ingest queue, so their rows stored when the delete
    starts are deleted row by row.
    """
    deleted = 0
    if _writes_day_files(conn):
        yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat()
        for part in day_store.parts(conn):
            if part.legacy:
                continue
            if part.day < yesterday:
                deleted += part.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
                day_store.delete_file(conn, part.day)
            else:
                try:
                    deleted += day_store.delete_rows(
                        part.conn, "WHERE logs.id <= :last", {"last": part.max_id}, batch_size, between_batches
                    )
                finally:
                    day_store.refresh_figures(conn, part.day)
    if not _has_legacy_table(conn):
        return deleted
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM logs").fetchone()[0]
        conn.execute("INSERT INTO logs_fts(logs_fts) VALUES('delete-all')")
        conn.execute(
            "UPDATE fts_index_state SET last_indexed_id = 0, updated_at = datetime('now') WHERE id = 1"
        )

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
        between_batches()


# Only rows the full-text indexer has reached are pruned, so the logs_ad trigger removes every
# pruned row's tokens from logs_fts (an unindexed row has none to remove)
_PRUNABLE_WHERE = """
    timestamp < :cutoff
      AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1)
"""


def _expired_days(conn: sqlite3.Connection, cutoff_iso: str) -> list[tuple[str, int]]:
    """(day, rows) of the day files whose whole UTC day is older than the cutoff, oldest first."""
    cutoff_us = iso_to_us(cutoff_iso)
    expired = []
    # From the catalogue, not parts(): a file with no rows left expires too
    for day, state in day_store.catalogue_days(conn):
        if day == LEGACY_DAY or day_start_us(day) + _DAY_US > cutoff_us:
            continue
        expired.append((day, day_store.stored_count(conn, day, state)))
    return sorted(expired)


_DAY_US = 86_400_000_000


def count_prunable(conn: sqlite3.Connection, cutoff_iso: str) -> int:
    """
    How many logs prune(cutoff_iso) would delete now: those of the day files whose whole day is
    older than the cutoff, and the legacy rows older than it.
    """
    total = sum(rows for _day, rows in _expired_days(conn, cutoff_iso)) if _writes_day_files(conn) else 0
    if _has_legacy_table(conn):
        total += conn.execute(f"SELECT COUNT(*) FROM logs WHERE {_PRUNABLE_WHERE}", {"cutoff": cutoff_iso}).fetchone()[0]
    return total


def prune(
    conn: sqlite3.Connection,
    cutoff_iso: str,
    between_batches: Callable[[], None],
    batch_size: int = 5000,
) -> int:
    """
    Delete the logs older than cutoff_iso (UTC ISO): every day file whose whole UTC day is older
    than the cutoff, at once, so a log is kept up to a day longer than the cutoff asks and its
    space comes back at once; then the legacy rows older than the cutoff, a batch per commit,
    calling between_batches after each one. Returns how many went.
    """
    total = 0
    if _writes_day_files(conn):
        for day, rows in _expired_days(conn, cutoff_iso):
            day_store.delete_file(conn, day)
            total += rows
            between_batches()
    if not _has_legacy_table(conn):
        return total
    cursor = conn.cursor()
    delete_query = f"DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE {_PRUNABLE_WHERE} LIMIT :batch)"
    while True:
        cursor.execute(delete_query, {"cutoff": cutoff_iso, "batch": batch_size})
        count = cursor.rowcount
        conn.commit()
        total += count
        if count == 0:
            return total
        between_batches()


def drop_empty_legacy(conn: sqlite3.Connection) -> bool:
    """
    Once retention (or a delete) has emptied the legacy logs table, drop it with its full-text
    index, triggers and indexer state, and remove its catalogue entry. Returns True when it
    dropped them. Only from schema 4, where new logs go to day files; the space comes back with
    a VACUUM of logs.db, which the caller runs.
    """
    if not _writes_day_files(conn) or not _has_legacy_table(conn):
        return False
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT EXISTS(SELECT 1 FROM logs)").fetchone()[0]:
            conn.execute("ROLLBACK")
            return False
        # Dropping the table drops its triggers; logs_fts drops its own shadow tables
        conn.execute("DROP TABLE IF EXISTS logs_fts")
        conn.execute("DROP TABLE logs")
        conn.execute("DROP TABLE IF EXISTS fts_index_state")
        conn.execute("DELETE FROM log_files WHERE day = ?", (LEGACY_DAY,))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return True


# ---------------------------------------------------------------------------
# Where logs are
# ---------------------------------------------------------------------------
#
# Logs live in parts (day_store.Part): the legacy table in logs.db (until retention empties it)
# and one file per UTC day. A read that needs one part runs there as it always has; one that
# needs several runs in each, newest first, and merges the results. A database whose only part
# is the legacy table (every database before its first day file) runs the legacy SQL unchanged.


def _bound_us(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return iso_to_us(value)
    except (ValueError, OverflowError):
        return None


def _parts_for(conn: sqlite3.Connection, log_filter: LogFilter) -> list[day_store.Part]:
    """The parts that can hold logs log_filter selects (by time range and ids), newest first."""
    from_us, to_us = _bound_us(log_filter.from_ts), _bound_us(log_filter.to_ts)
    found = [p for p in day_store.parts(conn) if p.overlaps(from_us, to_us)]
    if log_filter.log_ids:
        found = [p for p in found if p.holds_any(log_filter.log_ids)]
    return found


def _only_legacy(found: list[day_store.Part]) -> bool:
    return len(found) == 1 and found[0].legacy


def _row_us(row: Any) -> float:
    """A row's own time in microseconds, for merging parts (an unreadable time sorts oldest)."""
    try:
        return iso_to_us(str(row["timestamp"]))
    except (ValueError, OverflowError):
        return float("-inf")


def _newest_first(rows: list) -> list:
    return sorted(rows, key=lambda r: (_row_us(r), r["id"]), reverse=True)


def _oldest_first(rows: list) -> list:
    return sorted(rows, key=lambda r: (_row_us(r), r["id"]))


@dataclass(frozen=True)
class _PartQuery:
    """A filter ready to run in any part: its sources expanded once, and the Docker host names."""

    log_filter: LogFilter
    docker_names: list[str]

    @classmethod
    def prepare(cls, conn: sqlite3.Connection, log_filter: LogFilter) -> "_PartQuery":
        return cls(expand_sources(conn, log_filter), docker_aliases(conn) if log_filter.sources else [])

    def where(self, part: day_store.Part) -> tuple[str, dict[str, Any]]:
        if part.legacy:
            return build_where(None, self.log_filter)
        return build_day_where(self.log_filter, self.docker_names)


# ---------------------------------------------------------------------------
# Searching and reading
# ---------------------------------------------------------------------------


def search(
    conn: sqlite3.Connection,
    log_filter: LogFilter,
    *,
    fetch: int,
    count_limit: int,
    offset: int = 0,
    before: Optional[tuple[str, int]] = None,
    include_raw: bool = True,
) -> tuple[int, list]:
    """
    One page of the logs log_filter selects, newest first (timestamp, then id), and their count
    capped at count_limit: (count, rows). fetch is how many rows to return at most, from offset;
    before, a (timestamp, id) position, keeps only rows older than it (keyset paging). The count
    ignores offset and before. Full-text terms are matched in the newest rows first
    (run_newest_first); the result is the same as a search over every row.

    Across parts, each part's newest offset + fetch rows are merged, newest part first; a part
    whose newest log is older than the last row needed is only counted, and once the count is
    reached too, the parts after it are not opened.
    """
    found = _parts_for(conn, log_filter)
    if not found:
        if log_filter.query:
            notify_fts_worker()
        return 0, []
    if _only_legacy(found):
        where_sql, params = build_where(conn, log_filter)
        return _search_part(conn, False, where_sql, params, log_filter, fetch, count_limit, offset, before, include_raw)
    query = _PartQuery.prepare(conn, log_filter)
    if len(found) == 1:
        part = found[0]
        return _search_part(part.conn, not part.legacy, *query.where(part), log_filter, fetch, count_limit, offset, before, include_raw)

    needed = offset + fetch
    total = 0
    rows: list = []
    for part in found:
        cap = max(count_limit - total, 0)
        rows_complete = len(rows) >= needed and _row_us(rows[needed - 1]) > part.newest_ts
        if rows_complete and cap == 0:
            break
        part_total, part_rows = _search_part(
            part.conn, not part.legacy, *query.where(part), log_filter,
            0 if rows_complete else needed, cap, 0, before, include_raw,
        )
        total += part_total
        rows = _newest_first(rows + part_rows)[:needed]
    return min(total, count_limit), rows[offset:]


def _search_part(
    table_conn: sqlite3.Connection,
    day_file: bool,
    where_sql: str,
    params: dict[str, Any],
    log_filter: LogFilter,
    fetch: int,
    count_limit: int,
    offset: int,
    before: Optional[tuple[str, int]],
    include_raw: bool,
) -> tuple[int, list]:
    """search() in one part. With fetch 0, only the count."""
    count_sql = f"SELECT COUNT(*) FROM (SELECT 1 FROM logs {where_sql} LIMIT :count_limit)"
    cursor = table_conn.cursor()
    if fetch == 0:
        def _count_only(run_params: dict[str, Any]) -> tuple[int, list]:
            cursor.execute(count_sql, {**run_params, "count_limit": count_limit})
            return cursor.fetchone()[0], []

        return execute_with_fts_fallback(_count_only, params, log_filter.query, empty=(0, []))

    time_column = "ts" if day_file else "timestamp"
    page_where = where_sql
    page_params: dict[str, Any] = {"count_limit": count_limit, "fetch_limit": fetch, "offset": offset}
    if before is not None:
        keyset_sql = (
            f"(logs.{time_column} < :before_ts OR (logs.{time_column} = :before_ts AND logs.id < :before_id))"
        )
        page_where = f"{where_sql} AND {keyset_sql}" if where_sql else f"WHERE {keyset_sql}"
        page_params["before_ts"] = iso_to_us(before[0]) if day_file else before[0]
        page_params["before_id"] = before[1]

    if day_file:
        # The page's ids first, then its columns: the names and ISO times are built for the page
        # only, not for every row the sort reads
        select_sql = f"""
            SELECT {day_store.columns(include_raw)}
            FROM (
                SELECT logs.id, logs.ts FROM logs
                {page_where}
                ORDER BY logs.ts DESC, logs.id DESC
                LIMIT :fetch_limit OFFSET :offset
            ) AS page
            JOIN logs ON logs.id = page.id
            ORDER BY page.ts DESC, page.id DESC
        """
    else:
        select_sql = f"""
            SELECT {log_columns(include_raw)}
            FROM logs
            {page_where}
            ORDER BY logs.timestamp DESC, logs.id DESC
            LIMIT :fetch_limit OFFSET :offset
        """

    def _run(run_params: dict[str, Any]) -> tuple[int, list]:
        cursor.execute(count_sql, {**run_params, **page_params})
        run_total = cursor.fetchone()[0]
        cursor.execute(select_sql, {**run_params, **page_params})
        return run_total, cursor.fetchall()

    def _oldest_needed(result: tuple[int, list]) -> Optional[str]:
        run_total, run_rows = result
        if run_total >= count_limit and len(run_rows) >= fetch:
            return run_rows[-1]["timestamp"]
        # Too few matches: still complete when the window covers the whole time range
        return log_filter.from_ts

    # A part searched only for rows (its count cap already reached elsewhere) sizes its windows by rows
    if count_limit:
        matches, needed = (lambda r: r[0]), count_limit
    else:
        matches, needed = (lambda r: len(r[1])), fetch
    return execute_with_fts_fallback(
        lambda p: run_newest_first(table_conn, p, _run, _oldest_needed, matches, needed, day_file=day_file),
        params,
        log_filter.query,
        empty=(0, []),
    )


def count(
    conn: sqlite3.Connection,
    log_filter: LogFilter,
    group_by: Optional[str] = None,
    group_limit: int = 25,
) -> tuple[int, Optional[str], dict[str, int], bool]:
    """
    How many logs log_filter selects: (total, grouped_by, breakdown, truncated). group_by is
    "severity" (or "level"), "app" (or "app_name"), "source" (or "source_alias", "host") or None.
    Severity groups are labelled by name, in severity order; app and source groups are the
    group_limit largest, and truncated says whether more exist.
    """
    valid_group = (group_by or "").lower().strip()
    if valid_group in ("severity", "level"):
        label: Optional[str] = "severity"
    elif valid_group in ("app", "app_name"):
        label = "app"
    elif valid_group in ("source", "source_alias", "host"):
        label = "source"
    else:
        label = None

    found = _parts_for(conn, log_filter)
    if not found:
        return 0, label, {}, False
    if _only_legacy(found):
        return _legacy_count(conn, log_filter, label, group_limit)

    query = _PartQuery.prepare(conn, log_filter)
    if label is None:
        total = 0
        for part in found:
            groups = _catalogue_groups(conn, part, log_filter, None)
            total += (groups if groups is not None else _part_groups(part, query, None)).get(None, 0)
        return total, None, {}, False

    merged: dict[Any, int] = {}
    for part in found:
        groups = _catalogue_groups(conn, part, log_filter, label)
        for key, cnt in (groups if groups is not None else _part_groups(part, query, label)).items():
            merged[key] = merged.get(key, 0) + cnt
    total = sum(merged.values())
    if label == "severity":
        return total, label, {format_severity_label(sev): merged[sev] for sev in sorted(merged)}, False
    ordered = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    return total, label, dict(ordered[:group_limit]), len(ordered) > group_limit


def _catalogue_groups(
    conn: sqlite3.Connection, part: day_store.Part, log_filter: LogFilter, label: Optional[str]
) -> Optional[dict[Any, int]]:
    """
    _part_groups() of a sealed day file from the catalogue's counts, without opening the file,
    when the filter takes the whole file (no criterion but a time range that covers it). None
    when the file must be read: another criterion, a range that cuts it, or hosts by address
    (a row without a host name is grouped by its address, which the counts do not keep).
    """
    if not part.sealed or part.row_count is None:
        return None
    if log_filter.query or log_filter.sources or log_filter.apps or log_filter.log_ids or log_filter.severity_max is not None:
        return None
    from_us, to_us = _bound_us(log_filter.from_ts), _bound_us(log_filter.to_ts)
    if (log_filter.from_ts and (from_us is None or part.min_ts is None or from_us > part.min_ts)) or (
        log_filter.to_ts and (to_us is None or part.max_ts is None or to_us < part.max_ts)
    ):
        return None
    if label is None:
        return {None: part.row_count}
    column = {"severity": "severity", "app": "app", "source": "host"}[label]
    rows = conn.execute(
        f"SELECT {column}, SUM(count) FROM log_file_counts WHERE day = ? GROUP BY {column}", (part.day,)
    ).fetchall()
    if label == "source" and any(r[0] == "" for r in rows):
        return None
    return {r[0]: r[1] for r in rows}


def _part_groups(part: day_store.Part, query: _PartQuery, label: Optional[str]) -> dict[Any, int]:
    """Every group's count in one part: by severity number, app name or host (None: the total, under None)."""
    where_sql, params = query.where(part)
    cursor = part.conn.cursor()
    if label is None:
        sql = f"SELECT NULL, COUNT(*) FROM logs {where_sql}"
    elif label == "severity":
        sql = f"SELECT logs.severity, COUNT(*) FROM logs {where_sql} GROUP BY logs.severity"
    elif part.legacy and label == "app":
        sql = f"SELECT COALESCE(logs.app_name, 'unknown') AS grp, COUNT(*) FROM logs {where_sql} GROUP BY grp"
    elif part.legacy:
        sql = (
            "SELECT COALESCE(NULLIF(logs.source_alias, ''), logs.source_ip, 'unknown') AS grp, COUNT(*) "
            f"FROM logs {where_sql} GROUP BY grp"
        )
    elif label == "app":
        sql = (
            "SELECT (SELECT name FROM apps WHERE apps.id = g.app_id), g.cnt FROM "
            f"(SELECT logs.app_id AS app_id, COUNT(*) AS cnt FROM logs {where_sql} GROUP BY logs.app_id) AS g"
        )
    else:
        sql = (
            "SELECT COALESCE(NULLIF((SELECT name FROM hosts WHERE hosts.id = g.host_id), ''), NULLIF(g.source_ip, ''), 'unknown'), g.cnt FROM "
            f"(SELECT logs.host_id AS host_id, logs.source_ip AS source_ip, COUNT(*) AS cnt FROM logs {where_sql} "
            "GROUP BY logs.host_id, logs.source_ip) AS g"
        )

    def _run(run_params: dict[str, Any]) -> dict[Any, int]:
        groups: dict[Any, int] = {}
        for key, cnt in cursor.execute(sql, run_params).fetchall():
            groups[key] = groups.get(key, 0) + cnt
        return groups

    return execute_with_fts_fallback(_run, params, query.log_filter.query, empty={})


def _legacy_count(
    conn: sqlite3.Connection,
    log_filter: LogFilter,
    label: Optional[str],
    group_limit: int,
) -> tuple[int, Optional[str], dict[str, int], bool]:
    """count() on the legacy table alone."""
    where_sql, params = build_where(conn, log_filter)
    cursor = conn.cursor()

    def _total_count(run_params: dict[str, Any]) -> int:
        cursor.execute(f"SELECT COUNT(*) FROM logs {where_sql}", run_params)
        return cursor.fetchone()[0]

    def _limited_group(run_params: dict[str, Any], expr: str, label: str):
        """Run a capped GROUP BY, fetching one extra row to detect truncation."""
        group_sql = f"""
            SELECT {expr} as grp, COUNT(*) as cnt
            FROM logs
            {where_sql}
            GROUP BY grp
            ORDER BY cnt DESC, grp ASC
            LIMIT :group_fetch_limit
        """
        cursor.execute(group_sql, {**run_params, "group_fetch_limit": group_limit + 1})
        rows = cursor.fetchall()
        truncated = len(rows) > group_limit
        rows = rows[:group_limit]
        breakdown = {r[0]: r[1] for r in rows}
        # When every group was returned, the group counts sum to the exact total,
        # so the extra COUNT query is only needed when the breakdown is truncated.
        total = _total_count(run_params) if truncated else sum(breakdown.values())
        return total, label, breakdown, truncated

    def _severity_groups(run_params: dict[str, Any]):
        group_sql = f"""
            SELECT logs.severity, COUNT(*) as cnt
            FROM logs
            {where_sql}
            GROUP BY logs.severity
            ORDER BY logs.severity ASC
        """
        cursor.execute(group_sql, run_params)
        breakdown: dict[str, int] = {}
        total = 0
        for r in cursor.fetchall():
            total += r[1]
            breakdown[format_severity_label(r[0])] = r[1]
        return total, "severity", breakdown, False

    if label == "severity":
        run = _severity_groups
    elif label == "app":
        run = lambda p: _limited_group(p, "COALESCE(logs.app_name, 'unknown')", "app")  # noqa: E731
    elif label == "source":
        run = lambda p: _limited_group(  # noqa: E731
            p, "COALESCE(NULLIF(logs.source_alias, ''), logs.source_ip, 'unknown')", "source"
        )
    else:
        run = lambda p: (_total_count(p), None, {}, False)  # noqa: E731

    return execute_with_fts_fallback(run, params, log_filter.query, empty=(0, label, {}, False))


def count_matching(conn: sqlite3.Connection, log_filter: LogFilter) -> int:
    """How many logs log_filter selects."""
    return count(conn, log_filter)[0]


_GET_MANY_LEGACY = "id, timestamp, source_ip, source_alias, app_name, severity, message, raw"


def get_many(conn: sqlite3.Connection, log_ids: list[int]) -> list:
    """The logs with these ids that exist, oldest first, without received_at and facility."""
    if not log_ids:
        return []
    ids = tuple(log_ids)
    found = [p for p in day_store.parts(conn) if p.holds_any(ids)]
    rows: list = []
    for part in found:
        part_ids = [i for i in ids if part.min_id <= i <= part.max_id]  # type: ignore[operator]
        placeholders = ",".join("?" * len(part_ids))
        if part.legacy:
            sql = f"SELECT {_GET_MANY_LEGACY} FROM logs WHERE id IN ({placeholders}) ORDER BY timestamp ASC, id ASC"
        else:
            sql = (
                f"SELECT logs.id, {iso_sql('logs.ts')} AS timestamp, logs.source_ip, "
                "(SELECT name FROM hosts WHERE hosts.id = logs.host_id) AS source_alias, "
                "(SELECT name FROM apps WHERE apps.id = logs.app_id) AS app_name, "
                f"logs.severity, logs.message, {RAW_SQL} AS raw "
                f"FROM logs WHERE id IN ({placeholders}) ORDER BY ts ASC, id ASC"
            )
        rows.extend(part.conn.execute(sql, part_ids).fetchall())
    return rows if len(found) <= 1 else _oldest_first(rows)


def context(
    conn: sqlite3.Connection,
    log_id: int,
    before: int,
    after: int,
    same_app: bool = False,
    include_raw: bool = True,
) -> Optional[tuple[Any, list[Any], list[Any]]]:
    """
    The rows around one log, from the same host (context_source_clause) and, with same_app, the
    same app: (target, up to before earlier rows, up to after later rows), each list in time
    order. None when the log does not exist. The rows may come from other parts than the
    target's (the day before or after midnight UTC).
    """
    found = day_store.parts(conn)
    target = None
    for part in found:
        if part.holds_any((log_id,)):
            target = _context_target(part, log_id, include_raw)
            if target is not None:
                break
    if target is None:
        return None
    if _only_legacy(found):
        return _legacy_context(conn, target, before, after, same_app, include_raw)

    t_us = _row_us(target)
    before_parts = [p for p in found if p.min_ts is None or p.min_ts <= t_us]
    before_rows: list = []
    for part in before_parts:
        if len(before_rows) >= before and _row_us(before_rows[before - 1]) > part.newest_ts:
            break
        before_rows = _newest_first(before_rows + _context_rows(part, target, before, same_app, include_raw, older=True))[:before]
    after_parts = sorted(
        (p for p in found if p.max_ts is None or p.max_ts >= t_us),
        key=lambda p: (float("-inf") if p.min_ts is None else p.min_ts, p.min_id or 0),
    )
    after_rows: list = []
    for part in after_parts:
        if len(after_rows) >= after and part.min_ts is not None and _row_us(after_rows[after - 1]) < part.min_ts:
            break
        after_rows = _oldest_first(after_rows + _context_rows(part, target, after, same_app, include_raw, older=False))[:after]
    return target, _oldest_first(before_rows), after_rows


def _context_target(part: day_store.Part, log_id: int, include_raw: bool) -> Any:
    if part.legacy:
        return part.conn.execute(f"SELECT {log_columns(include_raw)} FROM logs WHERE id = ?", (log_id,)).fetchone()
    return part.conn.execute(f"SELECT {day_store.columns(include_raw)} FROM logs WHERE id = ?", (log_id,)).fetchone()


def _context_rows(part: day_store.Part, target: Any, limit: int, same_app: bool, include_raw: bool, older: bool) -> list:
    """Up to limit rows of target's host (and app) in one part, just older or just newer than target."""
    if limit <= 0:
        return []
    t_time, t_id = target["timestamp"], target["id"]
    direction, order = ("<", "DESC") if older else (">", "ASC")
    if part.legacy:
        source_clause, source_params = context_source_clause(target["source_alias"], target["source_ip"])
        if not same_app:
            app_clause, app_params = "", ()
        elif target["app_name"] is None:
            app_clause, app_params = "AND app_name IS NULL ", ()
        else:
            app_clause, app_params = "AND app_name = ? ", (target["app_name"],)
        return part.conn.execute(
            f"""
            SELECT {log_columns(include_raw)} FROM logs
            WHERE {source_clause}{app_clause}AND (timestamp {direction} ? OR (timestamp = ? AND id {direction} ?))
            ORDER BY timestamp {order}, id {order}
            LIMIT ?
            """,
            (*source_params, *app_params, t_time, t_time, t_id, limit),
        ).fetchall()

    alias, source_ip = target["source_alias"], target["source_ip"]
    if alias is None:
        return []
    if alias == "":
        source_clause, source_params = "logs.host_id = (SELECT id FROM hosts WHERE name = '') AND logs.source_ip = ? ", (source_ip,)
    else:
        source_clause, source_params = "logs.host_id = (SELECT id FROM hosts WHERE name = ?) ", (alias,)
    if not same_app:
        app_clause, app_params = "", ()
    elif target["app_name"] is None:
        return []
    else:
        app_clause, app_params = "AND logs.app_id = (SELECT id FROM apps WHERE name = ?) ", (target["app_name"],)
    t_us = iso_to_us(t_time)
    return part.conn.execute(
        f"""
        SELECT {day_store.columns(include_raw)} FROM logs
        WHERE {source_clause}{app_clause}AND (logs.ts {direction} ? OR (logs.ts = ? AND logs.id {direction} ?))
        ORDER BY logs.ts {order}, logs.id {order}
        LIMIT ?
        """,
        (*source_params, *app_params, t_us, t_us, t_id, limit),
    ).fetchall()


def _legacy_context(
    conn: sqlite3.Connection, target: Any, before: int, after: int, same_app: bool, include_raw: bool
) -> tuple[Any, list[Any], list[Any]]:
    """context() on the legacy table alone."""
    columns = log_columns(include_raw)
    source_clause, source_params = context_source_clause(target["source_alias"], target["source_ip"])
    if not same_app:
        app_clause, app_params = "", ()
    elif target["app_name"] is None:
        app_clause, app_params = "AND app_name IS NULL ", ()
    else:
        app_clause, app_params = "AND app_name = ? ", (target["app_name"],)
    t_time, t_id = target["timestamp"], target["id"]

    before_rows = conn.execute(
        f"""
        SELECT {columns} FROM logs
        WHERE {source_clause}{app_clause}AND (timestamp < ? OR (timestamp = ? AND id < ?))
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
        """,
        (*source_params, *app_params, t_time, t_time, t_id, before),
    ).fetchall()
    before_rows.reverse()
    after_rows = conn.execute(
        f"""
        SELECT {columns} FROM logs
        WHERE {source_clause}{app_clause}AND (timestamp > ? OR (timestamp = ? AND id > ?))
        ORDER BY timestamp ASC, id ASC
        LIMIT ?
        """,
        (*source_params, *app_params, t_time, t_time, t_id, after),
    ).fetchall()
    return target, before_rows, after_rows


# ---------------------------------------------------------------------------
# Hosts and apps seen (the facets)
# ---------------------------------------------------------------------------


@dataclass
class SeenNames:
    """
    The host and app names stored logs carry, each list at most scan_limit long (a longer list
    means more exist) and each pair list at most pair_limit long.
    """

    # (source_alias, a source_ip of that alias) for every non-empty alias
    aliases: list[tuple[str, str]]
    # source_ip of logs with no alias
    bare_ips: list[str]
    apps: list[str]
    # (source_alias, app_name, source_ip) for every non-empty alias and app pair
    alias_apps: list[tuple[str, str, str]]
    # (app_name, source_ip) of logs with no alias; empty when bare_ips is
    bare_ip_apps: list[tuple[str, str]]


def seen_names(conn: sqlite3.Connection, scan_limit: int, pair_limit: int) -> SeenNames:
    """
    The distinct host names, addresses and apps of every stored log, read with loose index
    skip-scans (one B-tree seek per distinct value), so a name with a single log is listed and
    the cost does not grow with the number of logs. Across parts, the lists are merged and
    capped again.
    """
    found = day_store.parts(conn)
    if not found:
        return SeenNames([], [], [], [], [])
    results: list[SeenNames] = []
    # Per day file: each host's first (app, address) pair, when its index walk saw every triple
    walked: list[Optional[dict[str, tuple[str, str]]]] = []
    for part in found:
        if part.legacy:
            results.append(_legacy_seen_names(part.conn, scan_limit, pair_limit))
            walked.append(None)
        else:
            seen, first_pair = _day_seen(part.conn, scan_limit, pair_limit)
            results.append(seen)
            walked.append(first_pair)
    if len(results) == 1:
        return results[0]
    # Each host name is listed with the address of its first (app, address) pair, as one table gives
    first: dict[str, tuple[str, str]] = {}
    for part, seen, first_pair in zip(found, results, walked):
        names = [a for a, _ip in seen.aliases]
        pairs = {a: first_pair[a] for a in names if a in first_pair} if first_pair is not None else _alias_first_addresses(part, names)
        for alias, pair in pairs.items():
            if alias not in first or pair < first[alias]:
                first[alias] = pair
    aliases = {alias: ip for alias, (_app, ip) in first.items()}
    alias_apps: dict[tuple[str, str], str] = {}
    for seen in results:
        for alias, app, ip in seen.alias_apps:
            alias_apps[(alias, app)] = min(alias_apps.get((alias, app), ip), ip)
    bare_ips = sorted({ip for seen in results for ip in seen.bare_ips})[:scan_limit]
    return SeenNames(
        aliases=sorted(aliases.items())[:scan_limit],
        bare_ips=bare_ips,
        apps=sorted({app for seen in results for app in seen.apps})[:scan_limit],
        alias_apps=[(a, app, ip) for (a, app), ip in sorted(alias_apps.items())][:pair_limit],
        bare_ip_apps=sorted({pair for seen in results for pair in seen.bare_ip_apps})[:pair_limit] if bare_ips else [],
    )


def _alias_first_addresses(part: day_store.Part, aliases: list[str]) -> dict[str, tuple[str, str]]:
    """For each host name in one part, the (app, address) pair that sorts first: its address is the one listed."""
    found: dict[str, tuple[str, str]] = {}
    for alias in aliases:
        if part.legacy:
            row = part.conn.execute(
                "SELECT app_name, source_ip FROM logs WHERE source_alias = ? ORDER BY source_alias, app_name, source_ip LIMIT 1",
                (alias,),
            ).fetchone()
            if row:
                found[alias] = (row[0] or "", row[1])
        else:
            for app, ip in part.conn.execute(
                "SELECT (SELECT name FROM apps WHERE apps.id = logs.app_id), source_ip FROM logs "
                "WHERE host_id = (SELECT id FROM hosts WHERE name = ?) GROUP BY app_id, source_ip",
                (alias,),
            ).fetchall():
                if alias not in found or (app, ip) < found[alias]:
                    found[alias] = (app, ip)
    return found


def _day_seen_names(day_conn: sqlite3.Connection, scan_limit: int, pair_limit: int) -> SeenNames:
    """seen_names() of one day file: one seek per distinct (address, app, host) in idx_logs_ip_app_host."""
    return _day_seen(day_conn, scan_limit, pair_limit)[0]


def _day_seen(
    day_conn: sqlite3.Connection, scan_limit: int, pair_limit: int
) -> tuple[SeenNames, Optional[dict[str, tuple[str, str]]]]:
    """_day_seen_names(), with each host's first (app, address) pair when the walk saw every triple (else None)."""
    walk_limit = 2 * pair_limit + 2 * scan_limit
    triples = day_conn.execute(
        """
        WITH RECURSIVE t(ip, app, host) AS (
            SELECT * FROM (
                SELECT source_ip, app_id, host_id FROM logs ORDER BY source_ip, app_id, host_id LIMIT 1
            )
            UNION ALL
            SELECT nxt.source_ip, nxt.app_id, nxt.host_id
            FROM t
            -- The next triple by equality seeks (next host, else next app, else next address),
            -- one B-tree seek each: a row-value range uses only the index's first column, so it
            -- read every row of the current address up to the position (1.2 s on a 536k-row file
            -- with 2 addresses)
            JOIN logs nxt ON nxt.id = COALESCE(
                (SELECT id FROM logs WHERE source_ip = t.ip AND app_id = t.app AND host_id > t.host
                    ORDER BY source_ip, app_id, host_id LIMIT 1),
                (SELECT id FROM logs WHERE source_ip = t.ip AND app_id > t.app
                    ORDER BY source_ip, app_id, host_id LIMIT 1),
                (SELECT id FROM logs WHERE source_ip > t.ip
                    ORDER BY source_ip, app_id, host_id LIMIT 1)
            )
            LIMIT ?
        )
        SELECT t.ip, (SELECT name FROM apps WHERE apps.id = t.app), (SELECT name FROM hosts WHERE hosts.id = t.host)
        FROM t
        """,
        (walk_limit,),
    ).fetchall()
    bare_ips: set[str] = set()
    apps: set[str] = set()
    alias_apps: dict[tuple[str, str], str] = {}
    bare_ip_apps: set[tuple[str, str]] = set()
    first_pair: dict[str, tuple[str, str]] = {}
    for ip, app, host in triples:
        if app:
            apps.add(app)
        if host:
            if host not in first_pair or (app, ip) < first_pair[host]:
                first_pair[host] = (app, ip)
            if app:
                alias_apps[(host, app)] = min(alias_apps.get((host, app), ip), ip)
        elif ip:
            bare_ips.add(ip)
            if app:
                bare_ip_apps.add((app, ip))
    seen = SeenNames(
        # Each host name with the address of its first (app, address) pair, as the legacy index gives
        aliases=sorted((host, pair[1]) for host, pair in first_pair.items())[:scan_limit],
        bare_ips=sorted(bare_ips)[:scan_limit],
        apps=sorted(apps)[:scan_limit],
        alias_apps=[(a, app, ip) for (a, app), ip in sorted(alias_apps.items())][:pair_limit],
        bare_ip_apps=sorted(bare_ip_apps)[:pair_limit] if bare_ips else [],
    )
    return seen, (first_pair if len(triples) < walk_limit else None)


def _legacy_seen_names(conn: sqlite3.Connection, scan_limit: int, pair_limit: int) -> SeenNames:
    """seen_names() of the legacy table."""
    cursor = conn.cursor()

    cursor.execute(
        f"""
        WITH RECURSIVE distinct_sources AS (
            SELECT MIN(source_alias) AS val FROM logs WHERE source_alias != ''
            UNION ALL
            SELECT (SELECT MIN(source_alias) FROM logs WHERE source_alias > s.val AND source_alias != '')
            FROM distinct_sources s
            WHERE s.val IS NOT NULL
            LIMIT {scan_limit}
        )
        SELECT s.val, (SELECT source_ip FROM logs WHERE source_alias = s.val LIMIT 1)
        FROM distinct_sources s
        WHERE s.val IS NOT NULL;
        """
    )
    aliases = [(r[0], r[1]) for r in cursor.fetchall()]

    cursor.execute(
        f"""
        WITH RECURSIVE distinct_ips AS (
            SELECT MIN(source_ip) AS val FROM logs WHERE source_alias = '' AND source_ip != ''
            UNION ALL
            SELECT (SELECT MIN(source_ip) FROM logs WHERE source_alias = '' AND source_ip > s.val AND source_ip != '')
            FROM distinct_ips s
            WHERE s.val IS NOT NULL
            LIMIT {scan_limit}
        )
        SELECT val FROM distinct_ips WHERE val IS NOT NULL;
        """
    )
    bare_ips = [r[0] for r in cursor.fetchall()]

    cursor.execute(
        f"""
        WITH RECURSIVE distinct_apps AS (
            SELECT MIN(app_name) AS val FROM logs WHERE app_name != ''
            UNION ALL
            SELECT (SELECT MIN(app_name) FROM logs WHERE app_name > a.val AND app_name != '')
            FROM distinct_apps a
            WHERE a.val IS NOT NULL
            LIMIT {scan_limit}
        )
        SELECT val FROM distinct_apps WHERE val IS NOT NULL;
        """
    )
    apps = [r[0] for r in cursor.fetchall()]

    # Distinct (source_alias, app_name, source_ip) pairs via covering index skip-scan
    cursor.execute(
        f"""
        WITH RECURSIVE cte(s, a, ip) AS (
            SELECT s.source_alias, s.app_name, s.source_ip
            FROM (
                SELECT source_alias, app_name, source_ip
                FROM logs
                WHERE source_alias != '' AND app_name != ''
                ORDER BY source_alias ASC, app_name ASC
                LIMIT 1
            ) s
            UNION ALL
            SELECT nxt.source_alias, nxt.app_name, nxt.source_ip
            FROM cte
            JOIN logs nxt ON nxt.id = COALESCE(
                (SELECT id FROM logs WHERE source_alias = cte.s AND app_name > cte.a AND app_name != '' ORDER BY app_name ASC LIMIT 1),
                (SELECT id FROM logs WHERE source_alias > cte.s AND source_alias != '' AND app_name != '' ORDER BY source_alias ASC, app_name ASC LIMIT 1)
            )
            LIMIT {pair_limit}
        )
        SELECT s AS source_alias, a AS app_name, ip AS source_ip FROM cte;
        """
    )
    alias_apps = [(r[0], r[1], r[2]) for r in cursor.fetchall()]

    # Every (app, IP) pair of alias-less logs in one skip-scan over idx_logs_source_app_ip, with
    # one index seek per pair, instead of one query per IP that read all of that IP's logs
    bare_ip_apps: list[tuple[str, str]] = []
    if bare_ips:
        cursor.execute(
            f"""
            WITH RECURSIVE ip_apps(a, ip) AS (
                SELECT * FROM (
                    SELECT app_name, source_ip FROM logs
                    WHERE source_alias = '' AND app_name != '' AND source_ip != ''
                    ORDER BY source_alias, app_name, source_ip
                    LIMIT 1
                )
                UNION ALL
                SELECT nxt.app_name, nxt.source_ip
                FROM ip_apps
                JOIN logs nxt ON nxt.id = COALESCE(
                    (SELECT id FROM logs WHERE source_alias = '' AND app_name = ip_apps.a AND source_ip > ip_apps.ip
                     ORDER BY source_alias, app_name, source_ip LIMIT 1),
                    (SELECT id FROM logs WHERE source_alias = '' AND app_name > ip_apps.a AND source_ip != ''
                     ORDER BY source_alias, app_name, source_ip LIMIT 1)
                )
                LIMIT {pair_limit}
            )
            SELECT a, ip FROM ip_apps;
            """
        )
        bare_ip_apps = [(r[0], r[1]) for r in cursor.fetchall()]

    return SeenNames(aliases, bare_ips, apps, alias_apps, bare_ip_apps)


# ---------------------------------------------------------------------------
# Figures for the dashboard, the digest and the Storage page
# ---------------------------------------------------------------------------


@dataclass
class StoredFile:
    """One place logs are stored, for the Storage page: a day file, or the legacy table in logs.db."""

    day: str  # 'YYYY-MM-DD' (UTC), or 'legacy'
    file_name: str
    state: str  # 'open', 'sealed' or 'legacy'
    row_count: Optional[int]  # None for the legacy table (the caller counts it)
    size_bytes: Optional[int]  # None for the legacy table, which shares logs.db
    max_ts: Optional[str]  # the newest log's own time (ISO, UTC)


def files(conn: sqlite3.Connection) -> list[StoredFile]:
    """
    Every day file in the catalogue (newest day first) with its logs and size, then the legacy
    table while it exists. The open files' figures are brought up to date first when logs.db can
    be written (not during a Compact, which holds it: the figures of the last refresh are used).
    """
    if not _writes_day_files(conn):
        return []
    try:
        day_store.refresh_open_figures(conn)
    except sqlite3.OperationalError:
        pass
    found = []
    db_path = day_store.main_path(conn)
    for day, file_name, state, row_count in conn.execute(
        "SELECT day, file_name, state, row_count FROM log_files "
        "WHERE day != ? AND state IN ('open', 'sealed') ORDER BY day DESC",
        (LEGACY_DAY,),
    ).fetchall():
        # Sizes from the disk, so they are right even when the refresh above could not run
        size = day_store.file_bytes(db_path.parent / file_name) if db_path is not None else 0
        found.append(StoredFile(day, file_name, state, row_count, size, None))
    if _has_legacy_table(conn):
        newest = conn.execute("SELECT MAX(timestamp) FROM logs").fetchone()[0]
        found.append(StoredFile(LEGACY_DAY, "logs.db", "legacy", None, None, newest))
    return found


def legacy_count(conn: sqlite3.Connection) -> Optional[int]:
    """How many logs the legacy table holds, or None when it is gone."""
    if not _has_legacy_table(conn):
        return None
    return conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]


def max_id(conn: sqlite3.Connection) -> int:
    """The highest log id stored (0 when there are none)."""
    return max((p.max_id or 0 for p in day_store.parts(conn)), default=0)


def count_rows(conn: sqlite3.Connection, after_id: Optional[int] = None) -> int:
    """How many logs are stored, or only those with an id above after_id."""
    total = 0
    for part in day_store.parts(conn):
        if after_id is None and part.row_count is not None:
            total += part.row_count
        elif after_id is None:
            total += part.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        elif part.max_id is not None and part.max_id > after_id:
            total += part.conn.execute("SELECT COUNT(*) FROM logs WHERE id > ?", (after_id,)).fetchone()[0]
    return total


@dataclass
class ErrorFigures:
    """Error counts of the last 24 hours (severity_counts: the row of per-severity counts) and the top offenders."""

    severity_counts: Any
    top_apps: list
    top_hosts: list


_ERROR_COUNT_COLUMNS = (
    "count_24h", "count_1h", "count_emergency", "count_alert", "count_critical", "count_error", "count_warning",
)


def error_figures(conn: sqlite3.Connection, since_1h: str, since_24h: str) -> ErrorFigures:
    """
    Error counts since since_24h and since_1h (UTC, stored layout) and the five apps and hosts
    with most errors (severity 3 or lower) since since_24h.
    """
    since_24h_us = _bound_us(since_24h)
    found = [p for p in day_store.parts(conn) if p.overlaps(since_24h_us, None)]
    if not found or _only_legacy(found):
        return _legacy_error_figures(conn, since_1h, since_24h)

    counts = dict.fromkeys(_ERROR_COUNT_COLUMNS, 0)
    apps: dict[str, int] = {}
    hosts: dict[str, int] = {}
    for part in found:
        if part.legacy:
            row = _legacy_error_counts(part.conn, since_1h, since_24h)
            app_rows = part.conn.execute(
                "SELECT app_name, COUNT(*) FROM logs WHERE severity <= 3 AND timestamp >= ? "
                "AND +app_name IS NOT NULL AND +app_name != '' GROUP BY +app_name",
                (since_24h,),
            ).fetchall()
            host_rows = part.conn.execute(
                "SELECT COALESCE(NULLIF(source_alias, ''), source_ip, 'unknown'), COUNT(*) FROM logs "
                "WHERE severity <= 3 AND timestamp >= ? GROUP BY 1",
                (since_24h,),
            ).fetchall()
        else:
            row = part.conn.execute(
                _ERROR_COUNTS_SQL.replace("timestamp", "ts"), (iso_to_us(since_1h), since_24h_us)
            ).fetchone()
            app_rows = part.conn.execute(
                "SELECT (SELECT name FROM apps WHERE apps.id = g.app_id), g.cnt FROM "
                "(SELECT app_id, COUNT(*) AS cnt FROM logs WHERE severity <= 3 AND ts >= ? GROUP BY +app_id) AS g",
                (since_24h_us,),
            ).fetchall()
            host_rows = part.conn.execute(_DAY_ERROR_HOSTS_SQL, (since_24h_us,)).fetchall()
        for name, value in zip(_ERROR_COUNT_COLUMNS, row):
            counts[name] += value or 0
        for name, cnt in app_rows:
            if name:
                apps[name] = apps.get(name, 0) + cnt
        for name, cnt in host_rows:
            hosts[name] = hosts.get(name, 0) + cnt
    top_apps = [{"app_name": n, "cnt": c} for n, c in sorted(apps.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    top_hosts = [{"host_val": n, "cnt": c} for n, c in sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    return ErrorFigures(counts, top_apps, top_hosts)


# A day file's errors per host since a time. Grouped by +host_id (as the apps by +app_id): grouped
# by the bare column, the planner skip-scanned the host index and read every row of the range
# for its severity (0.7 s on a 1.7M-row day), where the time index holds the severity
_DAY_ERROR_HOSTS_SQL = (
    "SELECT COALESCE(NULLIF((SELECT name FROM hosts WHERE hosts.id = g.host_id), ''), g.source_ip), g.cnt FROM "
    "(SELECT host_id, source_ip, COUNT(*) AS cnt FROM logs WHERE severity <= 3 AND ts >= ? "
    "GROUP BY +host_id, source_ip) AS g"
)


# A day file's errors per host, address and app since a time, for the digest; +host_id for the
# same reason (a whole-index scan, 19 times slower on a 300k-row day)
_DAY_DIGEST_ERRORS_SQL = """
    SELECT (SELECT name FROM hosts WHERE hosts.id = g.host_id), g.source_ip,
           (SELECT name FROM apps WHERE apps.id = g.app_id), g.cnt, g.e0, g.e1, g.e2, g.e3, g.low
    FROM (
        SELECT host_id, source_ip, app_id, COUNT(*) AS cnt, SUM(severity = 0) AS e0,
               SUM(severity = 1) AS e1, SUM(severity = 2) AS e2, SUM(severity = 3) AS e3,
               MIN(severity) AS low
        FROM logs WHERE severity <= 3 AND ts >= ? GROUP BY +host_id, source_ip, app_id
    ) AS g
"""


# The error totals and per-severity counts (warning included) share one pass over the
# 24-hour range of idx_logs_time_sev, so use conditional aggregation
_ERROR_COUNTS_SQL = """
    SELECT COALESCE(SUM(CASE WHEN severity <= 3 THEN 1 ELSE 0 END), 0) AS count_24h,
           COALESCE(SUM(CASE WHEN severity <= 3 AND timestamp >= ? THEN 1 ELSE 0 END), 0) AS count_1h,
           COALESCE(SUM(CASE WHEN severity = 0 THEN 1 ELSE 0 END), 0) AS count_emergency,
           COALESCE(SUM(CASE WHEN severity = 1 THEN 1 ELSE 0 END), 0) AS count_alert,
           COALESCE(SUM(CASE WHEN severity = 2 THEN 1 ELSE 0 END), 0) AS count_critical,
           COALESCE(SUM(CASE WHEN severity = 3 THEN 1 ELSE 0 END), 0) AS count_error,
           COALESCE(SUM(CASE WHEN severity = 4 THEN 1 ELSE 0 END), 0) AS count_warning
    FROM logs
    WHERE severity <= 4 AND timestamp >= ?
"""


def _legacy_error_counts(conn: sqlite3.Connection, since_1h: str, since_24h: str) -> Any:
    return conn.execute(_ERROR_COUNTS_SQL, (since_1h, since_24h)).fetchone()


def _legacy_error_figures(conn: sqlite3.Connection, since_1h: str, since_24h: str) -> ErrorFigures:
    """error_figures() on the legacy table alone."""
    cur = conn.cursor()
    counts = _legacy_error_counts(conn, since_1h, since_24h)

    # Top error apps (24h). The unary + keeps SQLite on the 24-hour range of idx_logs_time_sev:
    # left to itself it walks every app's range of idx_logs_app_time (1.3 s instead of 0.13 s at
    # 50 million logs).
    cur.execute(
        """
        SELECT app_name, COUNT(*) as cnt
        FROM logs
        WHERE severity <= 3 AND timestamp >= ? AND +app_name IS NOT NULL AND +app_name != ''
        GROUP BY +app_name
        ORDER BY cnt DESC, app_name ASC
        LIMIT 5
        """,
        (since_24h,),
    )
    top_apps = cur.fetchall()

    cur.execute(
        """
        SELECT COALESCE(NULLIF(source_alias, ''), source_ip, 'unknown') as host_val, COUNT(*) as cnt
        FROM logs
        WHERE severity <= 3 AND timestamp >= ?
        GROUP BY host_val
        ORDER BY cnt DESC, host_val ASC
        LIMIT 5
        """,
        (since_24h,),
    )
    return ErrorFigures(counts, top_apps, cur.fetchall())


@dataclass
class DigestFigures:
    """The daily digest's log figures for its window."""

    total_logs: int
    total_errors: int
    top_errors: list[dict]
    top_services: list[dict]


def digest_figures(conn: sqlite3.Connection, since_iso: str) -> DigestFigures:
    """
    Logs and errors (severity 3 or lower) since since_iso, the three hosts (or Docker apps) with
    the most severe and most errors, and the three apps with most logs.
    """
    since_us = _bound_us(since_iso)
    found = [p for p in day_store.parts(conn) if p.overlaps(since_us, None)]
    if not found or _only_legacy(found):
        return _legacy_digest_figures(conn, since_iso)

    total_logs = total_errors = 0
    # entity -> [errors, emergency, alert, critical, error, lowest severity]
    entities: dict[Any, list[int]] = {}
    services: dict[str, int] = {}
    for part in found:
        if part.legacy:
            since: Any = since_iso
            time_column = "timestamp"
            error_rows = part.conn.execute(
                f"""
                SELECT {_DIGEST_ENTITY_SQL} AS entity, COUNT(*),
                       SUM(severity = 0), SUM(severity = 1), SUM(severity = 2), SUM(severity = 3), MIN(severity)
                FROM logs WHERE severity <= 3 AND timestamp >= ? GROUP BY entity
                """,
                (since,),
            ).fetchall()
            service_rows = part.conn.execute(
                "SELECT COALESCE(NULLIF(app_name, ''), 'unknown') AS service, COUNT(*) FROM logs "
                "WHERE timestamp >= ? GROUP BY service",
                (since,),
            ).fetchall()
        else:
            since = since_us
            time_column = "ts"
            error_rows = []
            for alias, ip, app, *figures in part.conn.execute(
                _DAY_DIGEST_ERRORS_SQL,
                (since,),
            ).fetchall():
                docker = (ip or "").lower() == "docker" or (alias or "").lower() == "docker"
                error_rows.append((app if docker else (alias or ip), *figures))
            service_rows = part.conn.execute(
                "SELECT COALESCE(NULLIF((SELECT name FROM apps WHERE apps.id = g.app_id), ''), 'unknown'), g.cnt FROM "
                "(SELECT app_id, COUNT(*) AS cnt FROM logs WHERE ts >= ? GROUP BY app_id) AS g",
                (since,),
            ).fetchall()
        total_logs += part.conn.execute(f"SELECT COUNT(*) FROM logs WHERE {time_column} >= ?", (since,)).fetchone()[0]
        total_errors += part.conn.execute(
            f"SELECT COUNT(*) FROM logs WHERE severity <= 3 AND {time_column} >= ?", (since,)
        ).fetchone()[0]
        for entity, cnt, e0, e1, e2, e3, low in error_rows:
            figures = entities.setdefault(entity, [0, 0, 0, 0, 0, low])
            for i, value in enumerate((cnt, e0, e1, e2, e3)):
                figures[i] += value or 0
            figures[5] = min(figures[5], low)
        for service, cnt in service_rows:
            services[service] = services.get(service, 0) + cnt

    ranked = sorted(entities.items(), key=lambda kv: (kv[1][5], -kv[1][0], "" if kv[0] is None else kv[0]))[:3]
    top_errors = [
        {"entity": entity, "error_count": f[0], "emerg_count": f[1], "alert_count": f[2], "crit_count": f[3], "err_count": f[4]}
        for entity, f in ranked
    ]
    top_services = [
        {"service": s, "log_count": c} for s, c in sorted(services.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    ]
    return DigestFigures(total_logs, total_errors, top_errors, top_services)


_DIGEST_ENTITY_SQL = """
    CASE
        WHEN lower(source_ip) = 'docker' OR lower(source_alias) = 'docker' THEN app_name
        ELSE COALESCE(NULLIF(source_alias, ''), source_ip, 'unknown')
    END
"""


def _legacy_digest_figures(conn: sqlite3.Connection, since_iso: str) -> DigestFigures:
    """digest_figures() on the legacy table alone."""
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM logs WHERE timestamp >= ?", (since_iso,))
    total_logs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM logs WHERE severity <= 3 AND timestamp >= ?", (since_iso,))
    total_errors = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT
            CASE
                WHEN lower(source_ip) = 'docker' OR lower(source_alias) = 'docker' THEN app_name
                ELSE COALESCE(NULLIF(source_alias, ''), source_ip, 'unknown')
            END AS entity,
            COUNT(*) as error_count,
            SUM(CASE WHEN severity = 0 THEN 1 ELSE 0 END) as emerg_count,
            SUM(CASE WHEN severity = 1 THEN 1 ELSE 0 END) as alert_count,
            SUM(CASE WHEN severity = 2 THEN 1 ELSE 0 END) as crit_count,
            SUM(CASE WHEN severity = 3 THEN 1 ELSE 0 END) as err_count,
            MIN(severity) as min_severity
        FROM logs
        WHERE severity <= 3 AND timestamp >= ?
        GROUP BY entity
        ORDER BY min_severity ASC, error_count DESC, entity ASC
        LIMIT 3
        """,
        (since_iso,),
    )
    top_errors = [
        {
            "entity": row[0],
            "error_count": row[1],
            "emerg_count": row[2] or 0,
            "alert_count": row[3] or 0,
            "crit_count": row[4] or 0,
            "err_count": row[5] or 0,
        }
        for row in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT
            COALESCE(NULLIF(app_name, ''), 'unknown') AS service,
            COUNT(*) as log_count
        FROM logs
        WHERE timestamp >= ?
        GROUP BY service
        ORDER BY log_count DESC, service ASC
        LIMIT 3
        """,
        (since_iso,),
    )
    top_services = [{"service": row[0], "log_count": row[1]} for row in cursor.fetchall()]

    return DigestFigures(total_logs, total_errors, top_errors, top_services)
