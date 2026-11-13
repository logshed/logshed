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
import datetime
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

_DAY_US = 86_400_000_000

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


def _build(path: Path) -> None:
    """
    Make a day file with its layout under a temporary name, then rename it into place, so a
    reader never opens a file without its tables. WAL mode is stored in the file, so every
    later connection gets it without asking (asking needs a lock a reader should not wait for).
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    conn = sqlite3.connect(str(tmp))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        day_files.create_layout(conn)
    finally:
        conn.close()
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        for name in (tmp, Path(f"{tmp}-wal"), Path(f"{tmp}-shm")):
            with contextlib.suppress(FileNotFoundError):
                name.unlink()


def _open(path: Path, create: bool) -> sqlite3.Connection:
    if create and not path.exists():
        _build(path)
    conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, check_same_thread=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
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
    """
    One place logs are stored: the legacy table in logs.db, or one day file. A sealed day
    file's ranges come from the catalogue, and the file is opened only when a query uses it.
    """

    day: str
    _conn: Optional[sqlite3.Connection]
    legacy: bool
    min_id: Optional[int]
    max_id: Optional[int]
    # The logs' own times, UTC microseconds; None when unknown (the part is then searched)
    min_ts: Optional[int]
    max_ts: Optional[int]
    # A sealed day file: its path, and its row count from the catalogue
    path: Optional[Path] = None
    sealed: bool = False
    row_count: Optional[int] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            assert self.path is not None
            opened = connection(self.path)
            if opened is None:
                raise sqlite3.OperationalError(f"The log file {self.path} is missing")
            self._conn = opened
        return self._conn

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
    rows = conn.execute(
        "SELECT day, state, row_count, min_id, max_id, min_ts, max_ts FROM log_files ORDER BY day DESC"
    ).fetchall()
    for day, state, row_count, min_id, max_id, min_ts, max_ts in rows:
        if day == day_files.LEGACY_DAY:
            if _legacy_table_exists(conn):
                part = _legacy_part(conn)
                if part:
                    found.append(part)
            continue
        if db_path is None or state == "deleting":
            continue
        path = day_files.day_path(db_path, day)
        if state == "sealed":
            # The seal wrote the file's figures, and a write after it opens the file again
            if row_count and path.exists():
                found.append(Part(day, None, False, min_id, max_id, min_ts, max_ts, path, True, row_count))
            continue
        day_conn = connection(path)
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
    # By inode too: a logs.db replaced at the same path (a restore) gets a new connection
    key = (str(db_path), os.stat(db_path).st_ino)
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
    """Drop the reserved id blocks and the days known to be listed (the next write checks again); for restores and tests."""
    with _id_lock:
        _id_blocks.clear()
    with _listed_lock:
        _listed_days.clear()


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


# Days this process knows are in the catalogue, so ingest writes logs.db only on a day's first write
_listed_days: set[tuple[str, int, str]] = set()
_listed_lock = threading.Lock()


def _open_day(conn: sqlite3.Connection, db_path: Path, day: str) -> sqlite3.Connection:
    """The day's file, with its catalogue entry; both are made on the day's first write."""
    key = (str(db_path), os.stat(db_path).st_ino, day)
    if key not in _listed_days:
        write = _main_write_conn(db_path)
        with write:
            write.execute(
                "INSERT OR IGNORE INTO log_files (day, file_name, layout, state, row_count) VALUES (?, ?, ?, 'open', 0)",
                (day, day_files.file_name(day), day_files.LAYOUT_VERSION),
            )
        with _listed_lock:
            _listed_days.add(key)
    path = day_files.day_path(db_path, day)
    is_new = not path.exists()
    day_conn = connection(path, create=True)
    assert day_conn is not None
    if is_new:
        _give_to_database_owner(db_path, path)
    return day_conn


def _give_to_database_owner(db_path: Path, path: Path) -> None:
    """
    A day file made by a command run as root (docker exec) goes to logs.db's owner, as backups
    do, so the web process (which runs as PUID) can write it.
    """
    from app.core.backup import _give_to, _owner_to_apply

    owner = _owner_to_apply(db_path)
    if owner is None:
        return
    for name in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if name.exists():
            _give_to(name, owner)


def _read_us(value: Any) -> Optional[int]:
    if not value:
        return None
    try:
        return iso_to_us(str(value))
    except (ValueError, OverflowError):
        return None


def stored_values(entry: dict) -> tuple:
    """
    (ts, received_us, day) of an entry, read from its UTC ISO timestamp and received_at. A time
    that cannot be read never stops a log being stored: a missing or unreadable received_at is
    now, and a missing or unreadable timestamp is the received time.
    """
    received = entry.get("received_at")
    stamp = entry.get("timestamp")
    received_us = _read_us(received)
    if received_us is None:
        received, received_us = stamp, _read_us(stamp)
    if received_us is None:
        received = None
        received_us = iso_to_us(datetime.datetime.now(datetime.timezone.utc).isoformat())
    ts = received_us if stamp == received else _read_us(stamp)
    if ts is None:
        ts = received_us
    # The day of a UTC ISO time is its first ten characters (what ingest gives); others are worked out
    if isinstance(received, str) and received.endswith("+00:00") and received[10:11] == "T":
        day = received[:10]
    else:
        day = day_files.day_of_us(received_us)
    return ts, received_us, day


def write_rows(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """
    Store entries (dicts with the row fields) in the day files of their received_at day, give
    each its id ("id"), and commit each file's rows in one transaction. A batch that spans
    midnight writes two files. If a file's write fails, the entries of files already written
    keep their ids, the failed ones lose theirs, and the error is raised. Entries that already
    have an id were stored by an earlier attempt and are skipped, so a retry stores each once.
    """
    entries = [e for e in entries if e.get("id") is None]
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
        if day < _today():
            reopen_if_sealed(db_path, day)


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _insert(day_conn: sqlite3.Connection, rows: list[tuple[dict, int, int]]) -> None:
    hosts = _name_ids(day_conn, "hosts", {e.get("source_alias") or "" for e, _t, _r in rows})
    apps = _name_ids(day_conn, "apps", {e.get("app_name") or "" for e, _t, _r in rows})
    for i in range(0, len(rows), INSERT_CHUNK_ROWS):
        chunk = rows[i:i + INSERT_CHUNK_ROWS]
        params: list = []
        for entry, ts, received_us in chunk:
            # As in the legacy table: a field left out takes its default, an explicit None fails NOT NULL
            message = entry.get("message", "")
            raw, raw_kind = day_files.split_raw(message or "", entry.get("raw", ""))
            params.extend((
                entry["id"],
                ts,
                received_us,
                entry.get("source_ip", ""),
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
# Start-up
# ---------------------------------------------------------------------------


@dataclass
class StartUpCheck:
    finished_deletes: list[str]
    unlisted: list[str]


def check_files(conn: sqlite3.Connection) -> StartUpCheck:
    """
    At start-up, before anything writes: finish deleting the day files a crash left 'deleting',
    remove day files left half built under a temporary name, and list the day files beside the
    database that the catalogue does not list. Those are never used: after a restore, or a run
    of 1.3.x, the same log ids may have been handed out again. The caller warns about them.
    """
    db_path = main_path(conn)
    if db_path is None or not has_catalogue(conn):
        return StartUpCheck([], [])
    listed = {}
    for day, state in catalogue_days(conn):
        listed[day] = state
    finished = [day for day, state in listed.items() if state == "deleting"]
    for day in finished:
        delete_file(conn, day)
    unlisted = []
    for entry in sorted(db_path.parent.iterdir()):
        name = entry.name
        if name.startswith(".logs-") and ".tmp" in name and entry.is_file():
            with contextlib.suppress(OSError):
                entry.unlink()
            continue
        day = day_files.day_from_file_name(name)
        if day is not None and day not in listed:
            unlisted.append(name)
    return StartUpCheck(finished, unlisted)


# ---------------------------------------------------------------------------
# Sealing and the catalogue's figures
# ---------------------------------------------------------------------------

# A day is sealed this long after it ends (UTC), so the tail of a queue that was still writing
# the day's last logs at midnight is usually in before the seal
SEAL_AFTER_US = 2 * 60 * 1_000_000

_HOUR_US = 3_600_000_000


def file_bytes(path: Path) -> int:
    """The size of a database file with its -wal and -shm."""
    total = 0
    for name in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        with contextlib.suppress(FileNotFoundError):
            total += name.stat().st_size
    return total


@dataclass
class Figures:
    """What the catalogue holds about one day file's rows."""

    row_count: int
    min_id: Optional[int]
    max_id: Optional[int]
    min_ts: Optional[int]
    max_ts: Optional[int]
    # (hour start in epoch seconds, host, app, severity) -> count
    counts: dict[tuple[int, str, str, int], int]


def figures(day_conn: sqlite3.Connection) -> Figures:
    """A day file's figures: its id and time ranges, and its counts by hour x host x app x severity."""
    bounds = day_conn.execute(
        "SELECT COUNT(*), MIN(id), MAX(id), MIN(ts), MAX(ts) FROM logs"
    ).fetchone()
    # The hour of a log's own time; // rounds down, so times before 1970 land in their hour too
    rows = day_conn.execute(
        f"""
        SELECT g.hour, hosts.name, apps.name, g.severity, g.cnt FROM (
            SELECT (ts - (((ts % {_HOUR_US}) + {_HOUR_US}) % {_HOUR_US})) / 1000000 AS hour,
                   host_id, app_id, severity, COUNT(*) AS cnt
            FROM logs GROUP BY hour, host_id, app_id, severity
        ) AS g
        JOIN hosts ON hosts.id = g.host_id
        JOIN apps ON apps.id = g.app_id
        """
    ).fetchall()
    counts: dict[tuple[int, str, str, int], int] = {}
    for hour, host, app, severity, cnt in rows:
        key = (hour, host, app, severity)
        counts[key] = counts.get(key, 0) + cnt
    return Figures(bounds[0], bounds[1], bounds[2], bounds[3], bounds[4], counts)


def _write_figures(write: sqlite3.Connection, day: str, found: Figures, size: int, seal: bool) -> None:
    """Store a day file's figures and counts in the catalogue (in the caller's transaction)."""
    sealed = ", state = 'sealed', sealed_at = :now" if seal else ""
    write.execute(
        f"""
        UPDATE log_files SET row_count = :rows, min_id = :min_id, max_id = :max_id,
            min_ts = :min_ts, max_ts = :max_ts, size_bytes = :size{sealed}
        WHERE day = :day
        """,
        {
            "rows": found.row_count, "min_id": found.min_id, "max_id": found.max_id,
            "min_ts": found.min_ts, "max_ts": found.max_ts, "size": size, "day": day,
            "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    )
    write.execute("DELETE FROM log_file_counts WHERE day = ?", (day,))
    write.executemany(
        "INSERT INTO log_file_counts (day, hour, host, app, severity, count) VALUES (?, ?, ?, ?, ?, ?)",
        [(day, hour, host, app, sev, cnt) for (hour, host, app, sev), cnt in found.counts.items()],
    )


def seal(conn: sqlite3.Connection, day: str) -> bool:
    """
    Seal a finished day's file: compact its full-text index (FTS5 'optimize'), store its figures
    and counts in the catalogue, and mark it 'sealed'. Returns False, changing nothing, when the
    file is missing, not 'open', or has rows not yet in its full-text index.

    The file's write lock is held from the check to the catalogue write, so no row can arrive
    between them; a batch that arrives after the seal opens the day again (reopen_if_sealed).
    """
    db_path = main_path(conn)
    if db_path is None:
        return False
    path = day_files.day_path(db_path, day)
    day_conn = connection(path)
    if day_conn is None:
        return False
    if day_conn.in_transaction:
        day_conn.commit()
    day_conn.execute("BEGIN IMMEDIATE")
    try:
        state = conn.execute("SELECT state FROM log_files WHERE day = ?", (day,)).fetchone()
        if state is None or state[0] != "open" or has_unindexed(day_conn):
            day_conn.execute("ROLLBACK")
            return False
        day_conn.execute("INSERT INTO logs_fts(logs_fts) VALUES('optimize')")
        found = figures(day_conn)
        write = _main_write_conn(db_path)
        with write:
            # file_bytes before the checkpoint below: the size settles once the WAL is folded in,
            # and the next storage snapshot writes the final figure
            _write_figures(write, day, found, file_bytes(path), seal=True)
        day_conn.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(Exception):
            day_conn.execute("ROLLBACK")
        raise
    with contextlib.suppress(sqlite3.Error):
        day_conn.execute("PRAGMA optimize")
        day_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return True


def days_to_seal(conn: sqlite3.Connection, now_us: int) -> list[str]:
    """The 'open' days that ended at least SEAL_AFTER_US before now_us, oldest first."""
    if not has_catalogue(conn):
        return []
    return [
        r[0]
        for r in conn.execute("SELECT day FROM log_files WHERE state = 'open' ORDER BY day ASC").fetchall()
        if r[0] != day_files.LEGACY_DAY and day_files.day_start_us(r[0]) + _DAY_US + SEAL_AFTER_US <= now_us
    ]


def seal_finished_days(conn: sqlite3.Connection, now_us: Optional[int] = None) -> list[str]:
    """Seal every finished day whose file is fully indexed; returns the days sealed."""
    if now_us is None:
        now_us = iso_to_us(datetime.datetime.now(datetime.timezone.utc).isoformat())
    return [day for day in days_to_seal(conn, now_us) if seal(conn, day)]


def reopen_if_sealed(db_path: Path, day: str) -> None:
    """A batch was written to a sealed day (a queue still draining after midnight): open it again to index and seal later."""
    write = _main_write_conn(db_path)
    with write:
        write.execute("UPDATE log_files SET state = 'open', sealed_at = NULL WHERE day = ? AND state = 'sealed'", (day,))


def stored_count(conn: sqlite3.Connection, day: str, state: str) -> int:
    """How many logs a day file holds: the catalogue's figure once sealed, otherwise counted (0 when missing)."""
    if state == "sealed":
        row = conn.execute("SELECT row_count FROM log_files WHERE day = ?", (day,)).fetchone()
        return row[0] if row else 0
    db_path = main_path(conn)
    day_conn = connection(day_files.day_path(db_path, day)) if db_path is not None else None
    return day_conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] if day_conn is not None else 0


def refresh_figures(conn: sqlite3.Connection, day: str) -> None:
    """Rewrite a sealed day's figures and counts after a targeted delete or relabel changed its rows."""
    db_path = main_path(conn)
    if db_path is None:
        return
    state = conn.execute("SELECT state FROM log_files WHERE day = ?", (day,)).fetchone()
    if state is None or state[0] != "sealed":
        return
    path = day_files.day_path(db_path, day)
    day_conn = connection(path)
    if day_conn is None:
        return
    found = figures(day_conn)
    write = _main_write_conn(db_path)
    with write:
        state = write.execute("SELECT state FROM log_files WHERE day = ?", (day,)).fetchone()
        if state is not None and state[0] == "sealed":
            _write_figures(write, day, found, file_bytes(path), seal=False)


def refresh_open_figures(conn: sqlite3.Connection) -> None:
    """
    Bring the catalogue's row counts, ranges and sizes up to date: the ranges of the open files
    (which the writer does not record as it goes), and every file's size on disk.
    """
    db_path = main_path(conn)
    if db_path is None or not has_catalogue(conn):
        return
    updates = []
    for day, state in catalogue_days(conn):
        if day == day_files.LEGACY_DAY or state == "deleting":
            continue
        path = day_files.day_path(db_path, day)
        size = file_bytes(path)
        if state == "open":
            day_conn = connection(path)
            if day_conn is None:
                continue
            bounds = day_conn.execute("SELECT COUNT(*), MIN(id), MAX(id), MIN(ts), MAX(ts) FROM logs").fetchone()
            updates.append(("UPDATE log_files SET row_count = ?, min_id = ?, max_id = ?, min_ts = ?, max_ts = ?, size_bytes = ? "
                            "WHERE day = ? AND state = 'open'", (*bounds, size, day)))
        else:
            updates.append(("UPDATE log_files SET size_bytes = ? WHERE day = ?", (size, day)))
    if not updates:
        return
    write = _main_write_conn(db_path)
    with write:
        for sql, params in updates:
            write.execute(sql, params)


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


def write_rows_one_by_one(
    conn: sqlite3.Connection, entries: list[dict], repair: Callable[[dict], None]
) -> tuple[list[dict], list[tuple[dict, Exception]]]:
    """
    Store entries one at a time, each in a savepoint, in one transaction per day file: a row
    that fails with UnicodeEncodeError is repaired (repair changes the entry in place) and tried
    once more, and a row that still fails is skipped. A busy or locked file (OperationalError)
    rolls that file back and is raised; files committed before it keep their rows and ids, so
    a retry (write_rows) stores only the rest. Returns the stored entries and the skipped ones
    with their errors.
    """
    pending = [e for e in entries if e.get("id") is None]
    stored: list[dict] = [e for e in entries if e.get("id") is not None]
    skipped: list[tuple[dict, Exception]] = []
    if not pending:
        return stored, skipped
    db_path = main_path(conn)
    if db_path is None:
        raise RuntimeError("Day files need a database file")
    by_day: dict[str, list[tuple[dict, int, int]]] = {}
    for entry in pending:
        try:
            ts, received_us, day = stored_values(entry)
        except (ValueError, OverflowError, TypeError) as error:
            skipped.append((entry, error))
            continue
        by_day.setdefault(day, []).append((entry, ts, received_us))
    first = next_ids(conn, sum(len(rows) for rows in by_day.values()))
    next_id = first
    for day in sorted(by_day):
        day_conn = _open_day(conn, db_path, day)
        if day_conn.in_transaction:
            day_conn.rollback()
        day_conn.execute("BEGIN IMMEDIATE")
        file_stored: list[dict] = []
        try:
            for row in by_day[day]:
                entry = row[0]
                entry["id"] = next_id
                next_id += 1
                day_conn.execute("SAVEPOINT log_row")
                try:
                    try:
                        _insert(day_conn, [row])
                    except UnicodeEncodeError:
                        repair(entry)
                        _insert(day_conn, [row])
                    file_stored.append(entry)
                except sqlite3.OperationalError:
                    raise
                except Exception as row_error:
                    day_conn.execute("ROLLBACK TO log_row")
                    entry.pop("id", None)
                    skipped.append((entry, row_error))
                day_conn.execute("RELEASE log_row")
            day_conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(Exception):
                day_conn.execute("ROLLBACK")
            for entry, _ts, _received in by_day[day]:
                entry.pop("id", None)
            raise
        stored.extend(file_stored)
        if file_stored and day < _today():
            reopen_if_sealed(db_path, day)
    return stored, skipped


def open_day_connections(conn: sqlite3.Connection, states: tuple[str, ...] = ("open", "sealed")) -> list[tuple[str, sqlite3.Connection]]:
    """(day, connection) of every day file in the catalogue in these states that exists, newest first."""
    db_path = main_path(conn)
    if db_path is None or not has_catalogue(conn):
        return []
    found = []
    for day, state in catalogue_days(conn):
        if day == day_files.LEGACY_DAY or state not in states:
            continue
        day_conn = connection(day_files.day_path(db_path, day))
        if day_conn is not None:
            found.append((day, day_conn))
    return found


def index_pending_all(conn: sqlite3.Connection, batch_size: int) -> int:
    """One indexing batch in each day file that has rows not yet in its full-text index."""
    indexed = 0
    for _day, day_conn in open_day_connections(conn, ("open",)):
        if has_unindexed(day_conn):
            indexed += index_pending(day_conn, batch_size)
    return indexed


def delete_file(conn: sqlite3.Connection, day: str) -> None:
    """
    Delete a day file and its catalogue entry: the entry is marked 'deleting', every thread's
    connections are dropped, the file, its -wal and -shm are deleted, then the entry. A crash in
    between leaves the entry 'deleting', and the next call (or start-up) finishes it.
    """
    db_path = main_path(conn)
    if db_path is None:
        return
    write = _main_write_conn(db_path)
    with write:
        write.execute("UPDATE log_files SET state = 'deleting' WHERE day = ?", (day,))
    with _listed_lock:
        _listed_days.difference_update({k for k in _listed_days if k[0] == str(db_path) and k[2] == day})
    forget_connections()
    path = day_files.day_path(db_path, day)
    for name in (Path(f"{path}-wal"), Path(f"{path}-shm"), path):
        with contextlib.suppress(FileNotFoundError):
            name.unlink()
    write = _main_write_conn(db_path)
    with write:
        write.execute("DELETE FROM log_files WHERE day = ?", (day,))


def _reindex(day_conn: sqlite3.Connection, ids: list[int]) -> None:
    """Replace the full-text rows of these ids (those already indexed) with their current text."""
    if not ids:
        return
    last = day_conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
    indexed = [i for i in ids if i <= last]
    if not indexed:
        return
    placeholders = ",".join("?" * len(indexed))
    day_conn.execute(f"DELETE FROM logs_fts WHERE rowid IN ({placeholders})", indexed)
    day_conn.execute(
        f"""
        INSERT INTO logs_fts(rowid, app_name, source_alias, message)
        SELECT logs.id, apps.name, hosts.name, logs.message
        FROM logs JOIN apps ON apps.id = logs.app_id JOIN hosts ON hosts.id = logs.host_id
        WHERE logs.id IN ({placeholders})
        """,
        indexed,
    )


def _unindex(day_conn: sqlite3.Connection, ids: list[int]) -> None:
    """Remove the full-text rows of these ids (those already indexed)."""
    last = day_conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
    indexed = [i for i in ids if i <= last]
    if indexed:
        placeholders = ",".join("?" * len(indexed))
        day_conn.execute(f"DELETE FROM logs_fts WHERE rowid IN ({placeholders})", indexed)


def _name_id(day_conn: sqlite3.Connection, table: str, name: str) -> Optional[int]:
    row = day_conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def set_names(
    day_conn: sqlite3.Connection,
    column: str,
    where_sql: str,
    params: tuple,
    target: str,
    batch_size: int,
    pause: Callable[[], None],
) -> int:
    """
    Set host_id or app_id (column) to target's id on the rows where_sql selects, newest id first,
    batch_size rows per commit, re-indexing their full-text rows. Returns how many changed.
    """
    table = "hosts" if column == "host_id" else "apps"
    ids = sorted((r[0] for r in day_conn.execute(f"SELECT id FROM logs WHERE {where_sql}", params)), reverse=True)
    if not ids:
        return 0
    changed = 0
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        placeholders = ",".join("?" * len(chunk))
        with day_conn:
            target_id = _name_ids(day_conn, table, {target})[target]
            cursor = day_conn.execute(f"UPDATE logs SET {column} = ? WHERE id IN ({placeholders})", (target_id, *chunk))
            changed += max(cursor.rowcount, 0)
            _reindex(day_conn, chunk)
        pause()
    return changed


def delete_rows(
    day_conn: sqlite3.Connection,
    where_sql: str,
    params: dict[str, Any],
    batch_size: int,
    between_batches: Callable[[], None],
) -> int:
    """Delete the rows where_sql selects (and their full-text rows), batch_size per commit; returns how many."""
    deleted = 0
    while True:
        with day_conn:
            ids = [r[0] for r in day_conn.execute(f"SELECT logs.id FROM logs {where_sql} LIMIT {int(batch_size)}", params)]
            if not ids:
                return deleted
            _unindex(day_conn, ids)
            placeholders = ",".join("?" * len(ids))
            deleted += day_conn.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", ids).rowcount
        between_batches()


def delete_ids(day_conn: sqlite3.Connection, log_ids: list[int], between_batches: Callable[[], None], chunk_size: int = 500) -> int:
    """Delete the rows with these ids (and their full-text rows), a chunk per commit; returns how many."""
    deleted = 0
    for i in range(0, len(log_ids), chunk_size):
        chunk = log_ids[i:i + chunk_size]
        with day_conn:
            _unindex(day_conn, chunk)
            placeholders = ",".join("?" * len(chunk))
            deleted += day_conn.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", chunk).rowcount
        between_batches()
    return deleted
