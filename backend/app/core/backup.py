"""
Database backups: one compressed archive per backup in a `backups` folder beside the database.

An archive is a gzip-compressed tar (`tar -xzf` opens it) holding the database file under its own
name, then `manifest.json`, written last because the sizes are known only after streaming:

    {"format": 1, "app_version", "created_at", "kind", "schema_version", "target_schema",
     "files": [{"name": "logs.db", "role": "main", "size_bytes", "omitted_tables"?}]}

`"format": 1` is a contract: restore code must keep reading it. The secret key is never included,
because archives can be downloaded, and the key would decrypt the secrets stored in the database.

With day_files (manual backups), the archive also holds every day file of logs the catalogue lists
(`logs-YYYY-MM-DD-UTC.db`, role "day" with its "day" in the manifest), before the database:
    "files": [{"name": "logs-2026-11-12-UTC.db", "role": "day", "day": "2026-11-12", "size_bytes"}, ...,
              {"name": "logs.db", "role": "main", "size_bytes"}]
Three ways to copy the database:
- locked stream: checkpoint the WAL, take the write lock, and compress the database file straight
  into the archive. Needs only the archive's size on disk, but holds every writer off, so it runs
  only at start-up before any worker.
- VACUUM INTO: a consistent copy from a read snapshot while writers carry on, then compressed.
  Needs the database's size free while it runs.
- without logs: a new database with every table's schema and every row except those of the log
  tables, for an upgrade that does not change log rows. Small and quick whatever the log volume.
"""

import contextlib
import datetime
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from app.version import APP_VERSION

logger = logging.getLogger(__name__)

MANIFEST_FORMAT = 1
MANIFEST_NAME = "manifest.json"

# Archive size as a fraction of the database: the worst seen at gzip level 1 was 0.229
# (generated logs; real logs 0.151), plus a margin for data that compresses worse.
BACKUP_SIZE_RATIO = 0.30
BACKUP_EXPIRY_DAYS = 30
STALE_TMP_SECONDS = 24 * 3600
CHUNK_BYTES = 4 * 1024 * 1024
# Progress goes to the log only for databases this large; smaller ones take a second or two.
PROGRESS_LOG_MIN_BYTES = 256 * 1024 * 1024
GZIP_LEVEL = 1

# Free space a backup must leave on the disk: 10% of it, at least 2 GiB and at most 20 GiB.
# The data folder often sits on a small SSD shared with other containers, whose databases fail
# on a full disk; the cap stops a large, nearly full disk from refusing even a small backup.
FLOOR_MIN_BYTES = 2 * 1024**3
FLOOR_MAX_BYTES = 20 * 1024**3
FLOOR_FRACTION = 0.10

# Tables a backup without logs leaves empty (their schema is kept, so an older release still starts).
LOG_TABLES = ("logs", "logs_fts")

ARCHIVE_NAME_RE = re.compile(
    r"^logshed-backup-(?P<kind>pre-v(?P<from>\d+)-to-v(?P<to>\d+)|manual-v(?P<schema>\d+))"
    r"-(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<n>\d+))?\.tar\.gz$"
)
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

METHOD_LOCKED_STREAM = "locked_stream"
METHOD_VACUUM_INTO = "vacuum_into"
METHOD_WITHOUT_LOGS = "without_logs"

ProgressCallback = Callable[[int, int], None]


class BackupError(RuntimeError):
    """A backup could not be made. Any partial file has been removed."""


class BackupCancelled(Exception):
    """A backup was stopped part way (LogShed is shutting down). Any partial file has been removed."""


class BackupSpaceError(BackupError):
    """Too little free space for a backup without going under the free-space floor."""

    def __init__(self, folder: Path, needed: int, free: int, floor: int, leftover: int = 0):
        self.folder = folder
        self.needed = needed
        self.free = free
        self.floor = floor
        self.leftover = leftover
        message = (
            f"Not enough free space for a database backup in {folder}: it needs {format_bytes(needed)} "
            f"and must leave {format_bytes(floor)} free on the disk (10% of the disk, at least 2 GiB "
            f"and at most 20 GiB), so {format_bytes(needed + floor)} in all, but {format_bytes(free)} is free."
        )
        if leftover:
            message += (
                f" Unfinished backup files (*.tmp) in that folder take {format_bytes(leftover)}: they are left"
                " by an interrupted backup, and can be deleted when no backup is running."
            )
        super().__init__(message)


@dataclass(frozen=True)
class BackupInfo:
    """One archive in the backups folder, described from its name (the manifest is at the end of the gzip)."""

    path: Path
    kind: str
    schema_version: int
    target_schema: Optional[int]
    size_bytes: int
    created_at: datetime.datetime

    @property
    def expires_at(self) -> datetime.datetime:
        return self.created_at + datetime.timedelta(days=BACKUP_EXPIRY_DAYS)


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size_bytes: int
    seconds: float
    method: str


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def backups_dir(db_path: Union[str, Path]) -> Path:
    """The backups folder: beside the database file."""
    return Path(db_path).parent / "backups"


def free_space_floor(total_bytes: int) -> int:
    return max(FLOOR_MIN_BYTES, min(int(total_bytes * FLOOR_FRACTION), FLOOR_MAX_BYTES))


def archive_created_at(path: Path) -> datetime.datetime:
    """When an archive was made: the time in its name, or its modification time if that does not parse."""
    match = ARCHIVE_NAME_RE.match(path.name)
    if match:
        try:
            return datetime.datetime.strptime(match["stamp"], _STAMP_FORMAT).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    return datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc)


def list_backups(folder: Path) -> list[BackupInfo]:
    """The archives in the folder whose names match the archive pattern, newest first."""
    found = []
    if not folder.is_dir():
        return found
    for entry in folder.iterdir():
        match = ARCHIVE_NAME_RE.match(entry.name)
        if not match:
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            size = entry.stat().st_size
            created = archive_created_at(entry)
        except OSError:
            continue
        if match["schema"] is not None:
            kind, schema, target = "manual", int(match["schema"]), None
        else:
            kind, schema, target = "pre-migration", int(match["from"]), int(match["to"])
        found.append(BackupInfo(entry, kind, schema, target, size, created))
    found.sort(key=lambda b: (b.created_at, b.path.name), reverse=True)
    return found


def find_backup(folder: Path, filename: str) -> Optional[Path]:
    """
    The archive called `filename` in the folder, or None. The name must match the archive pattern
    exactly, and the file must be a regular file directly inside the folder (no symlink, no path).
    """
    if not ARCHIVE_NAME_RE.match(filename):
        return None
    candidate = folder / filename
    try:
        if candidate.resolve().parent != folder.resolve():
            return None
        if candidate.is_symlink() or not candidate.is_file():
            return None
    except OSError:
        return None
    return candidate


def backup_space_needed(db_path: Path, method: str, day_files: bool = False) -> int:
    """
    Bytes a backup of the database by `method` needs on the disk while it runs (its copy and archive).
    With day_files, also their archive and a copy of the largest (today's is copied before archiving).
    """
    if day_files:
        total, largest = day_files_bytes(db_path)
        method = METHOD_VACUUM_INTO if method == METHOD_LOCKED_STREAM else method
        return backup_space_needed(db_path, method) + largest + _compressed_estimate(total)
    if method == METHOD_WITHOUT_LOGS:
        copy_size = _size_without_logs(db_path)
        return copy_size + _compressed_estimate(copy_size)
    db_size = db_path.stat().st_size
    if method == METHOD_VACUUM_INTO:
        return db_size + _compressed_estimate(db_size)
    if method == METHOD_LOCKED_STREAM:
        return _compressed_estimate(db_size)
    raise ValueError(f"Unknown backup method {method!r}")


def prune_backups(folder: Path, now: Optional[float] = None) -> tuple[int, int]:
    """
    Delete archives older than BACKUP_EXPIRY_DAYS and `*.tmp` files older than a day. Only names
    that match the archive pattern are expired; anything else in the folder is left alone. A
    recent `*.tmp` may belong to a backup still running in another process, so it is kept.
    Returns (archives deleted, temporary files deleted).
    """
    if not folder.is_dir():
        return 0, 0
    now = time.time() if now is None else now
    expiry = datetime.datetime.fromtimestamp(now, datetime.timezone.utc) - datetime.timedelta(days=BACKUP_EXPIRY_DAYS)
    archives = tmps = 0
    for entry in folder.iterdir():
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.name.endswith(".tmp"):
                if now - entry.stat().st_mtime > STALE_TMP_SECONDS:
                    entry.unlink()
                    tmps += 1
            elif ARCHIVE_NAME_RE.match(entry.name) and archive_created_at(entry) < expiry:
                entry.unlink()
                archives += 1
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning(f"Could not delete old backup file {entry}: {e}")
    if archives or tmps:
        logger.info(f"Deleted {archives} expired database backups and {tmps} stale temporary files from {folder}.")
    return archives, tmps


def _tmp_bytes(folder: Path) -> int:
    """Bytes held by `*.tmp` files in the folder (recent ones are kept by the expiry)."""
    total = 0
    if folder.is_dir():
        for entry in folder.glob("*.tmp"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    return total


def check_backup_space(folder: Path, needed: int) -> tuple[int, int]:
    """Raise BackupSpaceError unless `needed` bytes fit with the floor left free. Returns (free, floor)."""
    probe = folder if folder.exists() else folder.parent
    usage = shutil.disk_usage(probe)
    floor = free_space_floor(usage.total)
    if usage.free < needed + floor:
        raise BackupSpaceError(folder, needed, usage.free, floor, _tmp_bytes(folder))
    return usage.free, floor


def _compressed_estimate(size: int) -> int:
    return math.ceil(size * BACKUP_SIZE_RATIO)


def _owner(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_uid, st.st_gid


def _give_to(path: Path, owner: Optional[tuple[int, int]]) -> None:
    """
    Hand a file or folder to the database's owner. `docker exec ... app.cli backup` runs as root,
    and a root-owned backups folder or archive would stop the app (which runs as PUID) from writing
    its next backup or expiring this one.
    """
    if owner is None:
        return
    try:
        os.chown(path, owner[0], owner[1])
    except OSError as e:
        logger.warning(f"Could not give {path} to the database's owner: {e}")


def _owner_to_apply(db_path: Path) -> Optional[tuple[int, int]]:
    """The database file's owner when this process runs as root and someone else owns it."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    owner = _owner(db_path)
    return None if owner == (0, 0) else owner


def _ensure_dir(folder: Path, owner: Optional[tuple[int, int]]) -> None:
    if folder.is_dir():
        return
    folder.mkdir(parents=True, exist_ok=True)
    # Set the mode outright: `docker exec` runs with umask 0, which would leave the folder writable by anyone.
    os.chmod(folder, 0o750)
    _give_to(folder, owner)


def archive_name(kind: str, now: datetime.datetime) -> str:
    return f"logshed-backup-{kind}-{now.strftime(_STAMP_FORMAT)}.tar.gz"


def _free_name(folder: Path, kind: str, now: datetime.datetime) -> str:
    base = archive_name(kind, now)
    if not (folder / base).exists() and not (folder / f"{base}.tmp").exists():
        return base
    stem = base[: -len(".tar.gz")]
    n = 2
    while (folder / f"{stem}-{n}.tar.gz").exists() or (folder / f"{stem}-{n}.tar.gz.tmp").exists():
        n += 1
    return f"{stem}-{n}.tar.gz"


class _ProgressReader:
    """A file wrapper for tarfile that reports progress and logs about every 10%."""

    def __init__(
        self,
        fileobj,
        total: int,
        label: str,
        progress_cb: Optional[ProgressCallback],
        cancel: Optional[threading.Event] = None,
        log_total: Optional[int] = None,
        log_offset: int = 0,
    ):
        self._f = fileobj
        self._cancel = cancel
        self._total = total
        self._done = 0
        # The figures logged: the whole archive's, when this file is one of several
        self._log_total = log_total or total
        self._log_offset = log_offset
        self._label = label
        self._cb = progress_cb
        self._next_log = (log_offset * 100 // self._log_total // 10 + 1) * 10

    def read(self, size: int = -1) -> bytes:
        if self._cancel is not None and self._cancel.is_set():
            raise BackupCancelled()
        data = self._f.read(CHUNK_BYTES if size is None or size < 0 else min(size, CHUNK_BYTES))
        self._done += len(data)
        if self._cb is not None:
            self._cb(self._done, self._total)
        if self._log_total >= PROGRESS_LOG_MIN_BYTES:
            done = self._log_offset + self._done
            percent = done * 100 // self._log_total
            if self._next_log <= percent < 100:
                logger.info(f"{self._label}: {percent}% ({format_bytes(done)} of {format_bytes(self._log_total)})")
                self._next_log = (percent // 10 + 1) * 10
        return data


class _ArchiveWriter:
    """
    A gzip tar written file by file (each streamed with no uncompressed copy), then the manifest.
    Progress is counted over every file: `total` is the bytes of all of them, as known at the start.
    """

    def __init__(self, tmp_path: Path, manifest: dict, label: str, total: int,
                 progress_cb: Optional[ProgressCallback], cancel: Optional[threading.Event] = None):
        self._tar = tarfile.open(tmp_path, "w:gz", compresslevel=GZIP_LEVEL, copybufsize=CHUNK_BYTES)
        self._manifest = manifest
        self._label = label
        self._total = max(total, 1)
        self._done = 0
        self._cb = progress_cb
        self._cancel = cancel
        self._created = int(time.time())
        self.entries: list[dict] = []

    def add(self, source: Path, entry: dict) -> None:
        size = source.stat().st_size
        info = tarfile.TarInfo(entry["name"])
        info.size = size
        info.mtime = self._created
        info.mode = 0o640
        offset = self._done
        self._total = max(self._total, offset + size)
        cb = (lambda done, _total: self._cb(offset + done, self._total)) if self._cb else None
        with open(source, "rb") as f:
            self._tar.addfile(info, fileobj=_ProgressReader(f, size, self._label, cb, self._cancel, self._total, offset))
        self._done += size
        entry["size_bytes"] = size
        self.entries.append(entry)

    def finish(self) -> None:
        self._manifest["files"] = self.entries
        body = json.dumps(self._manifest, indent=2).encode("utf-8")
        minfo = tarfile.TarInfo(MANIFEST_NAME)
        minfo.size = len(body)
        minfo.mtime = self._created
        minfo.mode = 0o640
        self._tar.addfile(minfo, fileobj=io.BytesIO(body))
        self._tar.close()

    def abandon(self) -> None:
        with contextlib.suppress(Exception):
            self._tar.close()


def _write_archive(
    tmp_path: Path,
    db_file: Path,
    member_name: str,
    manifest: dict,
    file_entry: dict,
    label: str,
    progress_cb: Optional[ProgressCallback],
    cancel: Optional[threading.Event] = None,
) -> None:
    """Stream `db_file` into a new gzip tar at `tmp_path`, then the manifest, with no uncompressed copy."""
    writer = _ArchiveWriter(tmp_path, manifest, label, db_file.stat().st_size, progress_cb, cancel)
    try:
        writer.add(db_file, {**file_entry, "name": member_name})
        writer.finish()
    except BaseException:
        writer.abandon()
        raise


def _wal_is_empty(db_path: Path) -> bool:
    wal = db_path.with_name(db_path.name + "-wal")
    try:
        return wal.stat().st_size == 0
    except FileNotFoundError:
        return True


class _WalNotEmpty(Exception):
    pass


def _locked_stream(db_path: Path, tmp_path: Path, manifest: dict, label: str, progress_cb, cancel=None) -> None:
    """
    Checkpoint, then take the write lock, then check the WAL is empty, then stream the file. The
    checkpoint comes before the lock (it cannot run inside a transaction), so another connection can
    commit in between; under the lock nothing else can, so an empty WAL means the file is complete.
    """
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("BEGIN IMMEDIATE;")
        try:
            if not _wal_is_empty(db_path):
                raise _WalNotEmpty()
            _write_archive(
                tmp_path, db_path, db_path.name, manifest, {"name": db_path.name, "role": "main"}, label, progress_cb, cancel
            )
        finally:
            conn.execute("ROLLBACK;")


def _stop_when_set(conn: sqlite3.Connection, cancel: Optional[threading.Event]) -> None:
    """Make a long statement on `conn` stop (raising sqlite3.OperationalError) once `cancel` is set."""
    if cancel is not None:
        conn.set_progress_handler(lambda: 1 if cancel.is_set() else 0, 100_000)


def _vacuum_into(db_path: Path, copy_path: Path, cancel: Optional[threading.Event] = None) -> None:
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)) as conn:
        _stop_when_set(conn, cancel)
        conn.execute("VACUUM INTO ?;", (str(copy_path),))


def _copy_without_logs(
    db_path: Path, copy_path: Path, omitted: tuple[str, ...] = LOG_TABLES, cancel: Optional[threading.Event] = None
) -> None:
    """
    Write a new database with the source's full schema and every row except those of the omitted
    tables (and their full-text shadow tables), from one read snapshot of the source. Rows of
    sqlite_sequence are kept, so ids carry on where they were and the full-text indexer's position
    stays valid. Writers on the source carry on.
    """
    src_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with contextlib.closing(sqlite3.connect(str(copy_path), isolation_level=None, uri=True)) as conn:
        conn.execute("ATTACH DATABASE ? AS src;", (src_uri,))
        _stop_when_set(conn, cancel)
        conn.execute("BEGIN;")
        try:
            kinds = {name: kind for name, kind in conn.execute("SELECT name, type FROM pragma_table_list WHERE schema = 'src';")}
            objects = conn.execute(
                "SELECT type, name, tbl_name, sql FROM src.sqlite_master WHERE sql IS NOT NULL ORDER BY rowid;"
            ).fetchall()
            omitted_set = set(omitted)
            shadow_owner = {}
            for _, name, _, _ in objects:
                if kinds.get(name) == "shadow":
                    owners = [v for v in kinds if kinds[v] == "virtual" and name.startswith(v + "_")]
                    shadow_owner[name] = max(owners, key=len) if owners else None

            tables = [o for o in objects if o[0] == "table" and kinds.get(o[1]) != "shadow" and not o[1].startswith("sqlite_")]
            for _, _, _, sql in tables:
                conn.execute(sql)
            for _, name, _, _ in tables:
                if name in omitted_set or kinds.get(name) == "virtual":
                    continue
                conn.execute(f'INSERT INTO main."{name}" SELECT * FROM src."{name}";')
            # Full-text tables that are kept: their shadow tables hold the index itself.
            for name, owner in shadow_owner.items():
                if owner is not None and owner not in omitted_set:
                    conn.execute(f'INSERT OR REPLACE INTO main."{name}" SELECT * FROM src."{name}";')
            if kinds.get("sqlite_sequence"):
                conn.execute("DELETE FROM main.sqlite_sequence;")
                conn.execute("INSERT INTO main.sqlite_sequence SELECT name, seq FROM src.sqlite_sequence;")
            for kind in ("index", "view", "trigger"):
                for _, _, _, sql in (o for o in objects if o[0] == kind):
                    conn.execute(sql)
            version = conn.execute("PRAGMA src.user_version;").fetchone()[0]
            conn.execute(f"PRAGMA main.user_version = {int(version)};")
            conn.execute("COMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        conn.execute("DETACH DATABASE src;")


def _listed_day_files(db_copy: Path) -> list[str]:
    """The days whose files a database's catalogue lists as holding logs ('open' or 'sealed'), oldest first."""
    with contextlib.closing(sqlite3.connect(f"{db_copy.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'log_files'").fetchone() is None:
            return []
        return [
            r[0] for r in conn.execute(
                "SELECT day FROM log_files WHERE state IN ('open', 'sealed') AND day != 'legacy' ORDER BY day"
            )
        ]


@contextlib.contextmanager
def _day_file_copy(path: Path, scratch: Path, cancel: Optional[threading.Event], owner: Optional[tuple[int, int]] = None):
    """
    A consistent copy of a day file to archive, while the app may write it. When its WAL is empty
    (a sealed file, checkpointed when sealed), the file itself inside a read transaction: a checkpoint
    cannot write into a database file while a reader reads it without the WAL, so the file stays as it
    was until the copy is done. Otherwise (today's file) a VACUUM INTO copy at `scratch`, deleted after.
    Yields (path to stream, its highest log id), or None when the file is gone. Opening a file in
    WAL mode makes its -wal and -shm when the app has none open; run as root (`docker exec`), those
    are given to `owner`, or the app could no longer open the file.
    """
    if not path.exists():
        yield None
        return
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None, timeout=5.0)
    try:
        conn.execute("BEGIN;")
        conn.execute("SELECT COUNT(*) FROM sqlite_master;").fetchone()
        if _wal_is_empty(path):
            yield path, conn.execute("SELECT MAX(id) FROM logs;").fetchone()[0]
            return
        conn.execute("COMMIT;")
        _stop_when_set(conn, cancel)
        try:
            conn.execute("VACUUM INTO ?;", (str(scratch),))
        except sqlite3.OperationalError:
            if cancel is not None and cancel.is_set():
                raise BackupCancelled() from None
            raise
        with contextlib.closing(sqlite3.connect(f"{scratch.resolve().as_uri()}?mode=ro", uri=True)) as copy:
            top = copy.execute("SELECT MAX(id) FROM logs;").fetchone()[0]
        yield scratch, top
    finally:
        with contextlib.suppress(sqlite3.Error):
            if conn.in_transaction:
                conn.execute("COMMIT;")
        conn.close()
        scratch.unlink(missing_ok=True)
        for name in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            if name.exists():
                _give_to(name, owner)


def _match_catalogue(db_copy: Path, archived: list[str], top_id: Optional[int]) -> None:
    """
    Make the copied database's catalogue list exactly the day files archived beside it, and its next
    log id follow every id in them: ids given out after the database was copied may be in a day file
    copied later, and a restore must not give them out again.
    """
    with contextlib.closing(sqlite3.connect(str(db_copy), isolation_level=None)) as conn:
        conn.execute("BEGIN;")
        placeholders = ",".join("?" * len(archived))
        keep = f"AND day NOT IN ({placeholders})" if archived else ""
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(f"DELETE FROM log_files WHERE day != 'legacy' {keep};", archived)
        if top_id is not None:
            conn.execute("UPDATE log_id_state SET next_id = MAX(next_id, ?) WHERE id = 1;", (top_id + 1,))
        conn.execute("COMMIT;")


def day_files_bytes(db_path: Path) -> tuple[int, int]:
    """(total, largest) bytes of the day files beside a database, each with its -wal."""
    total = largest = 0
    for f in db_path.parent.glob("logs-*-UTC.db"):
        size = f.stat().st_size
        with contextlib.suppress(FileNotFoundError):
            size += f.with_name(f.name + "-wal").stat().st_size
        total += size
        largest = max(largest, size)
    return total, largest


def _size_without_logs(db_path: Path, omitted: tuple[str, ...] = LOG_TABLES) -> int:
    """Bytes of every table and index outside the omitted tables, from dbstat (the database size if unavailable)."""
    with contextlib.closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        try:
            skip = set(omitted) | {
                name
                for (name,) in conn.execute("SELECT name FROM pragma_table_list WHERE type = 'shadow';")
                if any(name.startswith(t + "_") for t in omitted)
            }
            total = 0
            for name, table in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type IN ('table', 'index');").fetchall():
                if table in skip:
                    continue
                size = conn.execute("SELECT SUM(pgsize) FROM dbstat WHERE name = ? AND aggregate = 1;", (name,)).fetchone()[0]
                total += size or 0
            return total
        except sqlite3.Error:
            return db_path.stat().st_size


def _write_with_day_files(
    db_path: Path, tmp: Path, db_copy: Path, manifest: dict, entry: dict, label: str,
    progress_cb: Optional[ProgressCallback], cancel: Optional[threading.Event], scratch: Path,
    owner: Optional[tuple[int, int]] = None,
) -> None:
    """The archive of a database copy and the day files its catalogue lists: day files first, then the database."""
    from app.core import day_files as day_files_mod

    days = _listed_day_files(db_copy)
    total = db_copy.stat().st_size + day_files_bytes(db_path)[0]
    writer = _ArchiveWriter(tmp, manifest, label, total, progress_cb, cancel)
    try:
        archived: list[str] = []
        top_id: Optional[int] = None
        for day in days:
            path = day_files_mod.day_path(db_path, day)
            with _day_file_copy(path, scratch, cancel, owner) as found:
                if found is None:
                    continue
                source, file_top = found
                writer.add(source, {"name": path.name, "role": "day", "day": day})
            archived.append(day)
            if file_top is not None:
                top_id = file_top if top_id is None else max(top_id, file_top)
        if days:
            _match_catalogue(db_copy, archived, top_id)
        writer.add(db_copy, {**entry, "name": db_path.name})
        writer.finish()
    except BaseException:
        writer.abandon()
        raise


def create_backup(
    db_path: Union[str, Path],
    kind: str,
    schema_version: int,
    target_schema: Optional[int] = None,
    method: str = METHOD_VACUUM_INTO,
    extra_space: int = 0,
    progress_cb: Optional[ProgressCallback] = None,
    now: Optional[datetime.datetime] = None,
    cancel: Optional[threading.Event] = None,
    day_files: bool = False,
) -> BackupResult:
    """
    Make one archive in the backups folder and return it.

    With `day_files`, the archive also holds every day file the database's catalogue lists, copied
    while logging carries on (_day_file_copy); the database is then copied by VACUUM INTO (or without
    logs) even when `method` is the locked stream, so its catalogue can be made to match them.

    `kind` is `pre-v<from>-to-v<to>` or `manual-v<schema>`. `extra_space` is space the caller needs
    afterwards (a migration's own needs), kept out of what the backup may use. Expired archives and
    stale temporary files are deleted first; no other archive is deleted to make room.
    `progress_cb(done, total)` gets the bytes compressed so far (a VACUUM INTO copy comes before the
    first call). Setting `cancel` stops the backup at its next chunk and raises BackupCancelled.
    Raises BackupSpaceError when there is not enough room, BackupError or OSError when writing fails;
    no `.tmp` file is left behind either way.
    """
    db_path = Path(db_path)
    folder = backups_dir(db_path)
    owner = _owner_to_apply(db_path)
    prune_backups(folder)
    if day_files and method == METHOD_LOCKED_STREAM:
        method = METHOD_VACUUM_INTO

    if method == METHOD_LOCKED_STREAM:
        # Checkpoint first, so the size is the main file's full size.
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=5.0)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    db_size = db_path.stat().st_size
    check_backup_space(folder, backup_space_needed(db_path, method, day_files) + extra_space)

    _ensure_dir(folder, owner)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    name = _free_name(folder, kind, now)
    final = folder / name
    tmp = folder / f"{name}.tmp"
    copy = folder / f"{name}.db.tmp"
    manifest = {
        "format": MANIFEST_FORMAT,
        "app_version": APP_VERSION,
        "created_at": now.isoformat(),
        "kind": "manual" if kind.startswith("manual-") else "pre-migration",
        "schema_version": schema_version,
        "target_schema": target_schema,
    }
    label = f"Backing up the database to {name}"
    started = time.monotonic()
    used = method
    logger.info(f"{label} ({format_bytes(db_size)})...")
    try:
        if method == METHOD_LOCKED_STREAM:
            try:
                _locked_stream(db_path, tmp, manifest, label, progress_cb, cancel)
            except _WalNotEmpty:
                # Another process committed between the checkpoint and the lock: take a snapshot instead.
                tmp.unlink(missing_ok=True)
                logger.info("The write-ahead log was not empty under the lock; copying with VACUUM INTO instead.")
                check_backup_space(folder, db_size + _compressed_estimate(db_size) + extra_space)
                used = METHOD_VACUUM_INTO
        if used in (METHOD_VACUUM_INTO, METHOD_WITHOUT_LOGS):
            try:
                if used == METHOD_VACUUM_INTO:
                    _vacuum_into(db_path, copy, cancel)
                    entry = {"name": db_path.name, "role": "main"}
                else:
                    _copy_without_logs(db_path, copy, cancel=cancel)
                    entry = {"name": db_path.name, "role": "main", "omitted_tables": list(LOG_TABLES)}
            except sqlite3.OperationalError:
                if cancel is not None and cancel.is_set():
                    raise BackupCancelled() from None
                raise
            os.chmod(copy, 0o640)
            _give_to(copy, owner)
            if day_files:
                _write_with_day_files(
                    db_path, tmp, copy, manifest, entry, label, progress_cb, cancel, folder / f"{name}.day.tmp", owner
                )
            else:
                _write_archive(tmp, copy, db_path.name, manifest, entry, label, progress_cb, cancel)
        os.chmod(tmp, 0o640)
        _give_to(tmp, owner)
        os.replace(tmp, final)
    except BaseException as e:
        tmp.unlink(missing_ok=True)
        if isinstance(e, BackupCancelled):
            logger.info(f"Database backup {name} stopped part way; its unfinished files are deleted.")
        raise
    finally:
        copy.unlink(missing_ok=True)

    result = BackupResult(final, final.stat().st_size, time.monotonic() - started, used)
    logger.info(f"Database backup written: {final.name}, {format_bytes(result.size_bytes)} in {result.seconds:.1f} s.")
    return result
