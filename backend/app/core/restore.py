"""
Restoring a backup archive (core/backup.py) in place of the database, for a stopped LogShed.

One pass over the archive: the database file and any day files of logs are streamed out to
temporary files beside the database, then the manifest (written last) is read and checked against
them. Only when every copy passes `PRAGMA integrity_check` are the current database and every day
file beside it moved into a `before-restore-<time>` folder (never deleted) and the copies renamed
into their places, so a failure at any point leaves the data folder as it was. After a restore the
folder matches the archive: a day file the archive does not hold would carry log ids that the
restored database hands out again.
"""

import contextlib
import datetime
import json
import logging
import os
import shutil
import signal
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.core import backup as db_backup
from app.core import day_files, instance_lock, lifecycle
from app.core.session_epoch import bump_session_epoch, read_session_epoch

logger = logging.getLogger(__name__)

# Room a restore leaves on the disk beyond the database it writes (SQLite's own files, the log).
RESTORE_MARGIN_BYTES = 256 * 1024**2
# The folder beside the database that holds what a restore replaced
ASIDE_PREFIX = "before-restore-"


class RestoreError(RuntimeError):
    """The archive was not restored; the current database is unchanged."""


@dataclass(frozen=True)
class RestoreResult:
    archive: Path
    schema_version: int
    app_version: str
    kind: str
    omitted_tables: tuple[str, ...]
    aside: Optional[Path]
    aside_bytes: int
    database_bytes: int
    marker_removed: bool = False
    day_files: int = 0
    day_files_aside: int = 0


Say = Callable[[str], None]


def resolve_archive(name: str, db_path: Path) -> Path:
    """A path to an archive, or the name of one in the backups folder beside the database."""
    given = Path(name)
    if given.is_file():
        return given
    if given.name == name:
        candidate = db_backup.backups_dir(db_path) / name
        if candidate.is_file():
            return candidate
    raise RestoreError(f"There is no backup archive at {name}, nor one of that name in {db_backup.backups_dir(db_path)}.")


def _db_members_ok(member: tarfile.TarInfo) -> bool:
    return member.isfile() and "/" not in member.name and not member.name.startswith(".") and member.name.endswith(".db")


@dataclass
class _Extracted:
    manifest: dict
    db_name: str
    db_tmp: Path
    # day file name -> its temporary copy
    days: dict[str, Path]


def _day_tmp(db_tmp: Path, name: str) -> Path:
    return db_tmp.with_name(f"{db_tmp.name}.{name}.tmp")


def _extract(archive: Path, tmp: Path, say: Say, extracted: "_Extracted") -> None:
    """
    Stream the database and its day files out of the archive: the database into `tmp`, each day file
    beside it under a temporary name, recorded in `extracted` as they are written (so the caller can
    delete them if this stops part way), with the manifest.
    """
    written = 0
    try:
        with tarfile.open(archive, "r|gz") as tar:
            for member in tar:
                if member.name == db_backup.MANIFEST_NAME and member.isfile():
                    f = tar.extractfile(member)
                    extracted.manifest = json.loads(f.read(1024 * 1024)) if f else None
                    continue
                if not _db_members_ok(member):
                    raise RestoreError(f"{archive.name} is not a LogShed backup: it holds {member.name!r}.")
                is_day = day_files.day_from_file_name(member.name) is not None
                if (is_day and member.name in extracted.days) or (not is_day and extracted.db_name):
                    raise RestoreError(f"{archive.name} is not a LogShed backup: it holds {member.name!r} twice.")
                _check_space(tmp.parent, member.size)
                written += member.size
                target = tmp
                if is_day:
                    target = extracted.days[member.name] = _day_tmp(tmp, member.name)
                else:
                    extracted.db_name = member.name
                say(f"Extracting {member.name} ({db_backup.format_bytes(member.size)})...")
                source = tar.extractfile(member)
                if source is None:
                    raise RestoreError(f"{archive.name} is not a LogShed backup.")
                with open(target, "wb") as out:
                    shutil.copyfileobj(source, out, db_backup.CHUNK_BYTES)
                    out.flush()
                    os.fsync(out.fileno())
    except (tarfile.TarError, EOFError, OSError, ValueError) as e:
        # An OSError with an errno is the disk (full, read-only); without one it is a bad gzip stream
        if isinstance(e, OSError) and e.errno is not None:
            raise RestoreError(f"Could not read {archive.name} or write the restored copy: {e}.") from e
        raise RestoreError(f"{archive.name} is not a readable backup archive (it may be damaged or cut short): {e}.") from e
    if not extracted.db_name or not isinstance(extracted.manifest, dict):
        raise RestoreError(f"{archive.name} is not a LogShed backup: it has no database or no manifest.")


def _check_space(folder: Path, size: int) -> None:
    free = shutil.disk_usage(folder).free
    if free < size + RESTORE_MARGIN_BYTES:
        raise RestoreError(
            f"Not enough free space in {folder} to restore: the next file in the archive is {db_backup.format_bytes(size)} "
            f"and {db_backup.format_bytes(free)} is free (it needs {db_backup.format_bytes(size + RESTORE_MARGIN_BYTES)})."
        )


def _check_day_files(extracted: _Extracted) -> None:
    """Every day file in the archive is in the manifest with its size and day, and the other way round."""
    entries = [f for f in extracted.manifest.get("files") or [] if isinstance(f, dict) and f.get("role") == "day"]
    named = {f.get("name"): f for f in entries}
    if set(named) != set(extracted.days) or len(named) != len(entries):
        raise RestoreError("The archive's manifest does not list the day files it holds: the archive is damaged.")
    for name, entry in named.items():
        if entry.get("day") != day_files.day_from_file_name(name) or entry.get("size_bytes") != extracted.days[name].stat().st_size:
            raise RestoreError(f"{name} in the archive is not what its manifest records: the archive is damaged.")


def _check_manifest(manifest: dict, db_name: str, extracted: Path, latest_schema: int) -> dict:
    fmt = manifest.get("format")
    if not isinstance(fmt, int) or fmt < 1:
        raise RestoreError("The archive's manifest is not one LogShed wrote.")
    if fmt > db_backup.MANIFEST_FORMAT:
        raise RestoreError(
            f"The archive was made by a newer LogShed (archive format {fmt}); restore it with that version's image."
        )
    files = manifest.get("files") or []
    entry = next((f for f in files if isinstance(f, dict) and f.get("role") == "main"), None)
    if entry is None or entry.get("name") != db_name:
        raise RestoreError("The archive's manifest does not describe the database it holds.")
    if entry.get("size_bytes") != extracted.stat().st_size:
        raise RestoreError("The database in the archive is not the size its manifest records: the archive is damaged.")
    schema = manifest.get("schema_version")
    if not isinstance(schema, int) or schema < 1:
        raise RestoreError("The archive's manifest has no schema version.")
    if schema > latest_schema:
        raise RestoreError(
            f"The backup is of a schema {schema} database, made by a newer LogShed ({manifest.get('app_version')}); "
            f"this image understands schema {latest_schema} at most. Restore it with that version's image."
        )
    return entry


def _check_day_file(name: str, extracted: Path, say: Say) -> None:
    say(f"Checking {name}...")
    try:
        with contextlib.closing(sqlite3.connect(f"{extracted.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            layout = conn.execute("PRAGMA user_version;").fetchone()[0]
            result = [row[0] for row in conn.execute("PRAGMA integrity_check;").fetchmany(5)]
    except sqlite3.Error as e:
        raise RestoreError(f"{name} in the archive is damaged: {e}.") from e
    if result != ["ok"]:
        raise RestoreError(f"{name} in the archive is damaged: " + "; ".join(result) + ".")
    if not 1 <= layout <= day_files.LAYOUT_VERSION:
        raise RestoreError(
            f"{name} in the archive is in layout {layout}, which this image does not read; restore it with the version that made it."
        )


def _check_database(extracted: Path, schema: int, say: Say) -> None:
    say("Checking the restored copy (this reads all of it)...")
    try:
        with contextlib.closing(sqlite3.connect(f"{extracted.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            found = conn.execute("PRAGMA user_version;").fetchone()[0]
            result = [row[0] for row in conn.execute("PRAGMA integrity_check;").fetchmany(5)]
    except sqlite3.Error as e:
        raise RestoreError(f"The database in the archive is damaged: {e}.") from e
    if result != ["ok"]:
        raise RestoreError("The database in the archive is damaged: " + "; ".join(result) + ".")
    if found != schema:
        raise RestoreError(f"The database in the archive is at schema {found}, but its manifest says {schema}.")


def _app_is_using(db_path: Path) -> bool:
    """True when another connection holds a lock on the database (a writer, or a reader mid-transaction)."""
    if not db_path.exists():
        return False
    try:
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=0, isolation_level=None)) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("ROLLBACK;")
    except sqlite3.OperationalError as e:
        return "locked" in str(e) or "busy" in str(e)
    except sqlite3.DatabaseError:
        return False
    return False


def _move_file(path: Path, folder: Path) -> int:
    """Move a database file (checkpointed first, with its WAL) into `folder`; returns the bytes moved."""
    if not path.exists():
        return 0
    with contextlib.suppress(sqlite3.Error):
        # Fold the WAL into the main file, so the copy kept aside is one complete file
        with contextlib.closing(sqlite3.connect(str(path), timeout=5, isolation_level=None)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    folder.mkdir(mode=0o750, exist_ok=True)
    size = path.stat().st_size
    os.replace(path, folder / path.name)
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        size += wal.stat().st_size
        os.replace(wal, folder / wal.name)
    path.with_name(path.name + "-shm").unlink(missing_ok=True)
    return size


def _current_day_files(db_path: Path) -> list[Path]:
    return sorted(p for p in db_path.parent.iterdir() if p.is_file() and day_files.day_from_file_name(p.name))


def _move_aside(db_path: Path, stamp: str) -> tuple[Optional[Path], int, int]:
    """
    Move the current database and every day file beside it into `before-restore-<stamp>`, keeping
    every byte. Returns the folder (None when there was nothing to move), its size and the day files moved.
    """
    folder = db_path.with_name(f"{ASIDE_PREFIX}{stamp}")
    size = _move_file(db_path, folder)
    days = _current_day_files(db_path)
    for path in days:
        size += _move_file(path, folder)
    return (folder if folder.exists() else None), size, len(days)


def _end_sessions(restored: Path, current: Path) -> None:
    """
    Raise the restored copy's session epoch above both its own and the current database's, so every
    browser signs in again: the backup's older epoch would otherwise bring back sessions ended since.
    """
    current_epoch = 0
    if current.exists():
        # The database being replaced may be the broken one: if it cannot be read, the restored
        # copy's own epoch is raised, which still ends every session issued under the backup
        try:
            with contextlib.closing(sqlite3.connect(f"file:{current}?mode=ro", uri=True)) as conn:
                current_epoch = read_session_epoch(conn)
        except sqlite3.Error:
            current_epoch = 0
    with contextlib.closing(sqlite3.connect(restored)) as conn:
        bump_session_epoch(conn, at_least=current_epoch)
        conn.commit()


def restore_backup(archive: Path, db_path: Path, latest_schema: int, say: Say = print) -> RestoreResult:
    """
    Put the database from `archive` in place of `db_path`. Raises RestoreError, with the current
    database unchanged, when LogShed is running on it, the archive is not a valid backup, its schema
    is newer than `latest_schema`, the copy fails `integrity_check`, or the disk is too full.
    """
    db_path = Path(db_path)
    try:
        lock_fd = instance_lock.acquire(db_path, exclusive=True)
    except instance_lock.InstanceLockBusy:
        raise RestoreError(
            f"LogShed is running on {db_path.parent}. Stop the container first, then run the restore in a new one."
        ) from None
    try:
        if _app_is_using(db_path):
            raise RestoreError(f"Another program is using {db_path}. Stop LogShed first, then run the restore again.")
        # Copies left by a restore that was killed (SIGKILL, or a container removed part way)
        for stale in db_path.parent.glob(".restore-*.tmp*"):
            stale.unlink(missing_ok=True)
            say(f"Deleted {stale.name}, left by an interrupted restore.")
        owner = db_backup._owner_to_apply(db_path if db_path.exists() else db_path.parent)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tmp = db_path.with_name(f".restore-{stamp}.db.tmp")
        extracted = _Extracted({}, "", tmp, {})
        try:
            _extract(archive, tmp, say, extracted)
            manifest = extracted.manifest
            entry = _check_manifest(manifest, extracted.db_name, tmp, latest_schema)
            _check_day_files(extracted)
            schema = manifest["schema_version"]
            _check_database(tmp, schema, say)
            for name, path in sorted(extracted.days.items()):
                _check_day_file(name, path, say)
            _end_sessions(tmp, db_path)
            for path in (tmp, *extracted.days.values()):
                os.chmod(path, 0o640)
                db_backup._give_to(path, owner)
            # The renames happen together: a stop request in between would leave the folder half restored
            blocked = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
            try:
                aside, aside_bytes, days_aside = _move_aside(db_path, stamp)
                if aside is not None:
                    db_backup._give_to(aside, owner)
                for name, path in extracted.days.items():
                    os.replace(path, db_path.with_name(name))
                os.replace(tmp, db_path)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
            # A process that restores and then reads (tests, or a future in-process restore) reopens every file
            from app.services import day_store
            day_store.forget_connections()
            day_store.forget_id_blocks()
            # A failed upgrade's marker described the database just replaced
            marker_removed = lifecycle.marker_path(db_path).exists()
            lifecycle.clear_marker(db_path)
        finally:
            for path in (tmp, *extracted.days.values()):
                path.unlink(missing_ok=True)
                for suffix in ("-wal", "-shm"):
                    path.with_name(path.name + suffix).unlink(missing_ok=True)
        logger.info(f"Restored {archive.name} into {db_path}.")
        return RestoreResult(
            archive=archive,
            schema_version=schema,
            app_version=str(manifest.get("app_version") or "unknown"),
            kind=str(manifest.get("kind") or ""),
            omitted_tables=tuple(entry.get("omitted_tables") or ()),
            aside=aside,
            aside_bytes=aside_bytes,
            database_bytes=db_path.stat().st_size,
            marker_removed=marker_removed,
            day_files=len(extracted.days),
            day_files_aside=days_aside,
        )
    finally:
        instance_lock.release(lock_fd)
