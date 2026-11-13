"""
The log store's work on day files (app/core/day_files.py): finding the files of a database,
opening them, giving out log ids, writing rows, full-text indexing, and the SQL that reads one
file. ``log_store`` runs these over every file a call needs and merges the results; no other
module uses this one.

Connections: each thread keeps its own connections to the day files it has used, as
``run_db_query`` does for ``logs.db``. A file is opened only if it exists (reads never create
one). ``forget_connections`` makes every thread reopen its files, for when a file is deleted or
replaced. A search's interrupt check (``run_db_query``'s ``should_interrupt``) is passed on to
the day-file connections the search uses, through ``use_interrupt``.
"""

import contextlib
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from app.core import day_files
from app.core.day_files import RAW_SQL, iso_sql, iso_to_us

# SQLite virtual machine instructions between two interrupt checks (as run_db_query)
_INTERRUPT_CHECK_INSTRUCTIONS = 10_000

# Log ids a process reserves from logs.db at a time
ID_BLOCK = 100_000

# Rows per multi-row INSERT (12 parameters each)
INSERT_CHUNK_ROWS = 500

_local = threading.local()
_generation = 0
_generation_lock = threading.Lock()


def forget_connections() -> None:
    """Make every thread close and reopen its day-file connections (a file was deleted or replaced)."""
    global _generation
    with _generation_lock:
        _generation += 1
    close_thread_connections()


def close_thread_connections() -> None:
    """Close the calling thread's day-file connections."""
    cache = getattr(_local, "connections", None)
    if cache:
        for conn in cache.values():
            with contextlib.suppress(Exception):
                conn.close()
        cache.clear()


@contextlib.contextmanager
def use_interrupt(check: Optional[Callable[[], bool]]) -> Iterator[None]:
    """Apply a search's interrupt check to the day-file connections this thread uses meanwhile."""
    previous = getattr(_local, "interrupt", None)
    _local.interrupt = check
    try:
        yield
    finally:
        _local.interrupt = previous


def _open(path: Path, create: bool) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'rwc' if create else 'rw'}"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        if create:
            day_files.create_layout(conn)
        day_files.check_layout(conn, path)
    except BaseException:
        conn.close()
        raise
    return conn


def connection(path: Path, create: bool = False) -> Optional[sqlite3.Connection]:
    """
    The calling thread's connection to a day file (rows as sqlite3.Row), or None when the file
    does not exist and create is False. With create, a missing file is made with the layout.
    """
    cache = getattr(_local, "connections", None)
    if cache is None or getattr(_local, "generation", None) != _generation:
        close_thread_connections()
        cache = _local.connections = {}
        _local.generation = _generation
    key = str(path)
    conn = cache.get(key)
    if conn is None:
        if not create and not path.exists():
            return None
        try:
            conn = _open(path, create)
        except sqlite3.OperationalError:
            if not create and not path.exists():
                return None
            raise
        cache[key] = conn
    check = getattr(_local, "interrupt", None)
    if check is not None:
        conn.set_progress_handler(check, _INTERRUPT_CHECK_INSTRUCTIONS)
    else:
        conn.set_progress_handler(None, 0)
    return conn


def main_path(conn: sqlite3.Connection) -> Optional[Path]:
    """The file of a connection's main database, or None for an in-memory one."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":
            return Path(row[2]) if row[2] else None
    return None


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


@dataclass
class Part:
    """One place logs are stored: the legacy table in logs.db, or one day file."""

    day: str
    conn: sqlite3.Connection
    legacy: bool
    min_id: Optional[int]
    max_id: Optional[int]
    # The logs' own times, UTC microseconds; None when unknown (the part is then searched)
    min_ts: Optional[int]
    max_ts: Optional[int]

    def overlaps(self, from_us: Optional[int], to_us: Optional[int]) -> bool:
        if from_us is not None and self.max_ts is not None and self.max_ts < from_us:
            return False
        if to_us is not None and self.min_ts is not None and self.min_ts > to_us:
            return False
        return True

    def holds_any(self, ids: tuple[int, ...]) -> bool:
        if self.min_id is None or self.max_id is None:
            return False
        return any(self.min_id <= i <= self.max_id for i in ids)

    @property
    def newest_ts(self) -> float:
        return float("inf") if self.max_ts is None else self.max_ts


def has_catalogue(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'log_files'"
    ).fetchone() is not None


def _legacy_table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'logs'"
    ).fetchone() is not None


def _time_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return iso_to_us(str(value))
    except (ValueError, OverflowError):
        return None


def _legacy_part(conn: sqlite3.Connection) -> Optional[Part]:
    row = conn.execute(
        "SELECT (SELECT MIN(id) FROM logs), (SELECT MAX(id) FROM logs), "
        "(SELECT MIN(timestamp) FROM logs), (SELECT MAX(timestamp) FROM logs)"
    ).fetchone()
    if row[0] is None:
        return None
    return Part(day_files.LEGACY_DAY, conn, True, row[0], row[1], _time_or_none(row[2]), _time_or_none(row[3]))


def _day_part(day: str, day_conn: sqlite3.Connection) -> Optional[Part]:
    row = day_conn.execute(
        "SELECT (SELECT MIN(id) FROM logs), (SELECT MAX(id) FROM logs), "
        "(SELECT MIN(ts) FROM logs), (SELECT MAX(ts) FROM logs)"
    ).fetchone()
    if row[0] is None:
        return None
    return Part(day, day_conn, False, row[0], row[1], row[2], row[3])


def catalogue_days(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(day, state) of every catalogue entry, legacy included, newest day first."""
    return [
        (r[0], r[1])
        for r in conn.execute("SELECT day, state FROM log_files ORDER BY day DESC").fetchall()
    ]


def parts(conn: sqlite3.Connection) -> list[Part]:
    """
    Every place that holds logs, newest first (by the newest log time each holds), with the
    id and time ranges of what each holds now. Without a catalogue (a database before schema
    4) the logs table is the only one.
    """
    if not has_catalogue(conn):
        legacy = _legacy_part(conn) if _legacy_table_exists(conn) else None
        return [legacy] if legacy else []
    db_path = main_path(conn)
    found: list[Part] = []
    for day, state in catalogue_days(conn):
        if day == day_files.LEGACY_DAY:
            if _legacy_table_exists(conn):
                part = _legacy_part(conn)
                if part:
                    found.append(part)
            continue
        if db_path is None or state == "deleting":
            continue
        day_conn = connection(day_files.day_path(db_path, day))
        if day_conn is None:
            continue
        part = _day_part(day, day_conn)
        if part:
            found.append(part)
    found.sort(key=lambda p: (p.newest_ts, p.max_id or 0), reverse=True)
    return found


# ---------------------------------------------------------------------------
# Log ids
# ---------------------------------------------------------------------------

_id_lock = threading.Lock()
# (database file, its inode) -> [next id, end of the reserved block]
_id_blocks: dict[tuple[str, int], list[int]] = {}


def _main_write_conn(db_path: Path) -> sqlite3.Connection:
    """This thread's own connection to logs.db for the store's small catalogue writes."""
    cache = getattr(_local, "main", None)
    if cache is None or getattr(_local, "main_generation", None) != _generation:
        if cache:
            for conn in cache.values():
                with contextlib.suppress(Exception):
                    conn.close()
        cache = _local.main = {}
        _local.main_generation = _generation
    key = str(db_path)
    conn = cache.get(key)
    if conn is None:
        conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        cache[key] = conn
    return conn


def next_ids(conn: sqlite3.Connection, count: int) -> int:
    """
    Reserve count consecutive log ids and return the first. Ids come from a block of ID_BLOCK
    (or more) reserved in logs.db's log_id_state in one small transaction; the rest of a block
    is lost when the process stops, which leaves a harmless gap.
    """
    db_path = main_path(conn)
    if db_path is None:
        raise RuntimeError("Day files need a database file")
    key = (str(db_path), os.stat(db_path).st_ino)
    with _id_lock:
        block = _id_blocks.get(key)
        if block is None or block[1] - block[0] < count:
            size = max(ID_BLOCK, count)
            write = _main_write_conn(db_path)
            with write:
                first = write.execute(
                    "UPDATE log_id_state SET next_id = next_id + ? WHERE id = 1 RETURNING next_id - ?",
                    (size, size),
                ).fetchone()[0]
            block = _id_blocks[key] = [first, first + size]
        first = block[0]
        block[0] += count
        return first


def forget_id_blocks() -> None:
    """Drop the reserved id blocks (the next write reserves a new one); for restores and tests."""
    with _id_lock:
        _id_blocks.clear()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_INSERT_COLUMNS = (
    "id, ts, received_us, source_ip, host_id, app_id, source_kind, facility, severity, message, raw, raw_kind"
)
_ROW_PLACEHOLDER = "(" + ", ".join("?" * 12) + ")"


def _name_ids(day_conn: sqlite3.Connection, table: str, names: set[str]) -> dict[str, int]:
    """Ids of names in a lookup table (hosts or apps), adding the names it lacks."""
    if not names:
        return {}
    wanted = sorted(names)
    found: dict[str, int] = {}
    for i in range(0, len(wanted), 500):
        chunk = wanted[i:i + 500]
        placeholders = ",".join("?" * len(chunk))
        found.update(
            (r[1], r[0]) for r in day_conn.execute(f"SELECT id, name FROM {table} WHERE name IN ({placeholders})", chunk)
        )
    for name in wanted:
        if name not in found:
            found[name] = day_conn.execute(f"INSERT INTO {table} (name) VALUES (?) RETURNING id", (name,)).fetchone()[0]
    return found


def _open_day(conn: sqlite3.Connection, db_path: Path, day: str) -> sqlite3.Connection:
    """The day's file, with its catalogue entry; both are made on the day's first write."""
    write = _main_write_conn(db_path)
    with write:
        write.execute(
            "INSERT OR IGNORE INTO log_files (day, file_name, layout, state, row_count) VALUES (?, ?, ?, 'open', 0)",
            (day, day_files.file_name(day), day_files.LAYOUT_VERSION),
        )
    day_conn = connection(day_files.day_path(db_path, day), create=True)
    assert day_conn is not None
    return day_conn


def stored_values(entry: dict) -> tuple:
    """(ts, received_us, day) of an entry, read from its UTC ISO timestamp and received_at."""
    received = entry.get("received_at") or entry.get("timestamp")
    received_us = iso_to_us(str(received))
    stamp = entry.get("timestamp")
    ts = iso_to_us(str(stamp)) if stamp else received_us
    return ts, received_us, day_files.day_of_us(received_us)


def write_rows(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """
    Store entries (dicts with the row fields) in the day files of their received_at day, give
    each its id ("id"), and commit each file's rows in one transaction. A batch that spans
    midnight writes two files. If a file's write fails, the entries of files already written
    keep their ids, the failed ones lose theirs, and the error is raised.
    """
    if not entries:
        return
    db_path = main_path(conn)
    if db_path is None:
        raise RuntimeError("Day files need a database file")
    by_day: dict[str, list[tuple[dict, int, int]]] = {}
    for entry in entries:
        ts, received_us, day = stored_values(entry)
        by_day.setdefault(day, []).append((entry, ts, received_us))
    first = next_ids(conn, len(entries))
    next_id = first
    for day in sorted(by_day):
        rows = by_day[day]
        for entry, _ts, _received in rows:
            entry["id"] = next_id
            next_id += 1
        day_conn = _open_day(conn, db_path, day)
        try:
            _insert(day_conn, rows)
            day_conn.commit()
        except BaseException:
            with contextlib.suppress(Exception):
                day_conn.rollback()
            for entry, _ts, _received in rows:
                entry.pop("id", None)
            raise


def _insert(day_conn: sqlite3.Connection, rows: list[tuple[dict, int, int]]) -> None:
    hosts = _name_ids(day_conn, "hosts", {e.get("source_alias") or "" for e, _t, _r in rows})
    apps = _name_ids(day_conn, "apps", {e.get("app_name") or "" for e, _t, _r in rows})
    for i in range(0, len(rows), INSERT_CHUNK_ROWS):
        chunk = rows[i:i + INSERT_CHUNK_ROWS]
        params: list = []
        for entry, ts, received_us in chunk:
            message = entry.get("message") or ""
            raw, raw_kind = day_files.split_raw(message, entry.get("raw", ""))
            params.extend((
                entry["id"],
                ts,
                received_us,
                entry.get("source_ip") or "",
                hosts[entry.get("source_alias") or ""],
                apps[entry.get("app_name") or ""],
                entry.get("source_kind", 0),
                entry.get("facility", 1),
                entry.get("severity", 6),
                message,
                raw,
                raw_kind,
            ))
        placeholders = ", ".join([_ROW_PLACEHOLDER] * len(chunk))
        day_conn.execute(f"INSERT INTO logs ({_INSERT_COLUMNS}) VALUES {placeholders}", params)


# ---------------------------------------------------------------------------
# Full-text indexing
# ---------------------------------------------------------------------------


def index_pending(day_conn: sqlite3.Connection, batch_size: int) -> int:
    """
    Index one batch of a day file's rows not yet in its logs_fts, in one transaction that
    starts with BEGIN IMMEDIATE (as the legacy indexer does). Returns how many it indexed.
    """
    if day_conn.in_transaction:
        day_conn.commit()
    day_conn.execute("BEGIN IMMEDIATE")
    try:
        last_id = day_conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        returned = day_conn.execute(
            """
            INSERT INTO logs_fts(rowid, app_name, source_alias, message)
            SELECT logs.id, apps.name, hosts.name, logs.message
            FROM logs
            JOIN apps ON apps.id = logs.app_id
            JOIN hosts ON hosts.id = logs.host_id
            WHERE logs.id > ?
            ORDER BY logs.id ASC
            LIMIT ?
            RETURNING rowid
            """,
            (last_id, batch_size),
        ).fetchall()
        if returned:
            day_conn.execute(
                "UPDATE fts_index_state SET last_indexed_id = ?, updated_at = CAST(strftime('%s', 'now') AS INTEGER) WHERE id = 1",
                (max(r[0] for r in returned),),
            )
        day_conn.execute("COMMIT")
        return len(returned)
    except BaseException:
        with contextlib.suppress(Exception):
            day_conn.execute("ROLLBACK")
        raise


def has_unindexed(day_conn: sqlite3.Connection) -> bool:
    return bool(
        day_conn.execute(
            "SELECT EXISTS(SELECT 1 FROM logs WHERE id > (SELECT last_indexed_id FROM fts_index_state WHERE id = 1))"
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# Reading one day file
# ---------------------------------------------------------------------------


def columns(include_raw: bool = True, alias: str = "logs") -> str:
    """The SELECT list giving a day file's rows in the store's row form (legacy column names, ISO times)."""
    cols = (
        f"{alias}.id, {iso_sql(alias + '.ts')} AS timestamp, {iso_sql(alias + '.received_us')} AS received_at, "
        f"{alias}.source_ip, (SELECT name FROM hosts WHERE hosts.id = {alias}.host_id) AS source_alias, "
        f"(SELECT name FROM apps WHERE apps.id = {alias}.app_id) AS app_name, "
        f"{alias}.facility, {alias}.severity, {alias}.message"
    )
    if include_raw:
        cols += ", " + RAW_SQL.replace("logs.", f"{alias}.") + " AS raw"
    return cols


def names_in(table: str, placeholders: str) -> str:
    """SQL selecting the ids of names in a lookup table (hosts or apps)."""
    return f"(SELECT id FROM {table} WHERE name IN ({placeholders}))"
