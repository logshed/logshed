"""
Day files: logs received on one UTC day are stored in their own SQLite file beside the main
database (``logs-2026-11-12-UTC.db`` beside ``logs.db``). The main database keeps everything
else, the catalogue of day files (``log_files``) and, after an upgrade from 1.3.x, the legacy
``logs`` table until retention empties it.

This module knows the file names, the layout of a day file and the stored forms of its values.
The log store (``app/services/log_store.py``) is the only reader and writer of the files.

Stored forms (layout 1):
- times are integer UTC microseconds since 1970 (``ts`` for the log's own time,
  ``received_us`` for when LogShed received it), given back as ``datetime.isoformat()`` in UTC;
- host and app names are ids into the file's own ``hosts`` and ``apps`` tables;
- ``raw`` is stored by ``raw_kind``: 0 when it equals ``message`` (``raw`` NULL), 1 when it is
  ``raw || message`` (only the prefix stored), 2 when it is stored whole.
"""

import datetime
import re
import sqlite3
from pathlib import Path
from typing import Optional, Union

# The layout version of the day files this LogShed writes (PRAGMA user_version of each file).
LAYOUT_VERSION = 1

# The catalogue's day value for the legacy logs table in the main database
LEGACY_DAY = "legacy"

_UTC = datetime.timezone.utc
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=_UTC)
_ONE_US = datetime.timedelta(microseconds=1)
_DAY_NAME = re.compile(r"^logs-(\d{4}-\d{2}-\d{2})-UTC\.db$")

# Source kinds (source_kind column)
KIND_SYSLOG = 1
KIND_DOCKER = 2
KIND_INGEST = 3
KIND_INTERNAL = 4

RAW_IS_MESSAGE = 0
RAW_IS_PREFIX = 1
RAW_IS_WHOLE = 2

LAYOUT_1_SCHEMA = """
CREATE TABLE hosts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE apps (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE logs (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    received_us INTEGER NOT NULL,
    source_ip TEXT NOT NULL,
    host_id INTEGER NOT NULL,
    app_id INTEGER NOT NULL,
    source_kind INTEGER NOT NULL,
    reported_host_id INTEGER,
    facility INTEGER NOT NULL DEFAULT 1,
    severity INTEGER NOT NULL DEFAULT 6,
    message TEXT NOT NULL,
    raw TEXT,
    raw_kind INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_logs_time_sev ON logs(ts DESC, severity);
CREATE INDEX idx_logs_app_time ON logs(app_id, ts DESC);
CREATE INDEX idx_logs_host_time ON logs(host_id, ts DESC);
CREATE INDEX idx_logs_ip_app_host ON logs(source_ip, app_id, host_id);
CREATE VIRTUAL TABLE logs_fts USING fts5(app_name, source_alias, message, content='', contentless_delete=1);
CREATE TABLE fts_index_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_indexed_id INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
INSERT INTO fts_index_state (id, last_indexed_id, updated_at) VALUES (1, 0, 0);
"""


class DayFileNewerThanAppError(RuntimeError):
    """A day file was written by a newer LogShed, in a layout this version cannot read."""

    def __init__(self, path: Union[str, Path], found: int):
        self.path = path
        self.found = found
        super().__init__(
            f"The log file {path} was written by a newer version of LogShed (layout {found}); this version "
            f"reads layout {LAYOUT_VERSION} at most. Start the newer version again, or restore a backup."
        )


def iso_to_us(value: str) -> int:
    """UTC microseconds since 1970 of an ISO-8601 time (a time without an offset is UTC)."""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    moment = datetime.datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_UTC)
    delta = moment - _EPOCH
    # Integer arithmetic: about twice as fast as dividing the timedelta, on every stored log
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def us_to_iso(value: int) -> str:
    """The stored ISO form (datetime.isoformat() in UTC) of UTC microseconds since 1970."""
    return (_EPOCH + datetime.timedelta(microseconds=value)).isoformat()


# The same as us_to_iso, in SQL, for a column of UTC microseconds: datetime.isoformat() leaves
# the fraction out when it is zero.
def iso_sql(column: str) -> str:
    """SQL giving us_to_iso(column) for a column of UTC microseconds."""
    # SQLite's / and % round towards zero; the fraction is taken modulo 10**6 so times before
    # 1970 come out as Python writes them too
    fraction = f"((({column}) % 1000000 + 1000000) % 1000000)"
    return (
        f"(strftime('%Y-%m-%dT%H:%M:%S', (({column}) - {fraction}) / 1000000, 'unixepoch')"
        f" || CASE WHEN {fraction} THEN printf('.%06d', {fraction}) ELSE '' END || '+00:00')"
    )


def day_of_us(value: int) -> str:
    """The UTC day ('YYYY-MM-DD') of UTC microseconds since 1970."""
    return (_EPOCH + datetime.timedelta(microseconds=value)).date().isoformat()


def day_start_us(day: str) -> int:
    """UTC microseconds of the start (00:00 UTC) of a 'YYYY-MM-DD' day."""
    start = datetime.datetime.fromisoformat(day).replace(tzinfo=_UTC)
    return (start - _EPOCH) // _ONE_US


def file_name(day: str) -> str:
    """The file name of a day's logs: logs-YYYY-MM-DD-UTC.db."""
    return f"logs-{day}-UTC.db"


def day_from_file_name(name: str) -> Optional[str]:
    """The day a day-file name stands for, or None when the name is not a day file's."""
    match = _DAY_NAME.match(name)
    return match.group(1) if match else None


def day_path(db_path: Union[str, Path], day: str) -> Path:
    """Where a day's file lives: beside the main database."""
    return Path(db_path).parent / file_name(day)


def split_raw(message: str, raw: Optional[str]) -> tuple[Optional[str], int]:
    """(stored raw, raw_kind) for a log line: only what message does not already hold."""
    if raw is None or raw == message:
        return None, RAW_IS_MESSAGE
    if message and raw.endswith(message):
        return raw[: len(raw) - len(message)], RAW_IS_PREFIX
    return raw, RAW_IS_WHOLE


def join_raw(message: str, raw: Optional[str], raw_kind: int) -> str:
    """The raw line split_raw stored."""
    if raw_kind == RAW_IS_MESSAGE:
        return message
    if raw_kind == RAW_IS_PREFIX:
        return (raw or "") + message
    return raw or ""


RAW_SQL = (
    "CASE logs.raw_kind WHEN 0 THEN logs.message WHEN 1 THEN logs.raw || logs.message ELSE logs.raw END"
)


def create_layout(conn: sqlite3.Connection) -> None:
    """Create the layout-1 tables in an empty day file and mark its layout, in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            for statement in LAYOUT_1_SCHEMA.strip().split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {LAYOUT_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def check_layout(conn: sqlite3.Connection, path: Union[str, Path]) -> int:
    """The file's layout version; raises DayFileNewerThanAppError for a layout this version cannot read."""
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found > LAYOUT_VERSION:
        raise DayFileNewerThanAppError(path, found)
    return found
