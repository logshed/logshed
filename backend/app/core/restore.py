"""
Restoring a backup archive (core/backup.py) in place of the database, for a stopped LogShed.

One pass over the archive: the database file is streamed out to a temporary file beside the
database, then the manifest (written last) is read and checked against it. Only when the copy
passes `PRAGMA integrity_check` is the current database moved aside (never deleted) and the copy
renamed into its place, so a failure at any point leaves the current database as it was.
"""

import contextlib
import datetime
import json
import logging
import os
import shutil
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.core import backup as db_backup
from app.core import instance_lock

logger = logging.getLogger(__name__)

# Room a restore leaves on the disk beyond the database it writes (SQLite's own files, the log).
RESTORE_MARGIN_BYTES = 256 * 1024**2
ASIDE_SUFFIX = ".before-restore-"


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


def _extract(archive: Path, tmp: Path, say: Say) -> tuple[dict, str]:
    """Stream the database out of the archive into `tmp`; return the manifest and the database's name."""
    db_name: Optional[str] = None
    manifest: Optional[dict] = None
    try:
        with tarfile.open(archive, "r|gz") as tar:
            for member in tar:
                if member.name == db_backup.MANIFEST_NAME and member.isfile():
                    f = tar.extractfile(member)
                    manifest = json.loads(f.read(1024 * 1024)) if f else None
                    continue
                if db_name is not None or not _db_members_ok(member):
                    raise RestoreError(f"{archive.name} is not a LogShed backup: it holds {member.name!r}.")
                _check_space(tmp.parent, member.size)
                db_name = member.name
                say(f"Extracting {member.name} ({db_backup.format_bytes(member.size)})...")
                source = tar.extractfile(member)
                if source is None:
                    raise RestoreError(f"{archive.name} is not a LogShed backup.")
                with open(tmp, "wb") as out:
                    shutil.copyfileobj(source, out, db_backup.CHUNK_BYTES)
                    out.flush()
                    os.fsync(out.fileno())
    except (tarfile.TarError, EOFError, OSError, ValueError) as e:
        # An OSError with an errno is the disk (full, read-only); without one it is a bad gzip stream
        if isinstance(e, OSError) and e.errno is not None:
            raise RestoreError(f"Could not read {archive.name} or write the restored copy: {e}.") from e
        raise RestoreError(f"{archive.name} is not a readable backup archive (it may be damaged or cut short): {e}.") from e
    if db_name is None or not isinstance(manifest, dict):
        raise RestoreError(f"{archive.name} is not a LogShed backup: it has no database or no manifest.")
    return manifest, db_name


def _check_space(folder: Path, size: int) -> None:
    free = shutil.disk_usage(folder).free
    if free < size + RESTORE_MARGIN_BYTES:
        raise RestoreError(
            f"Not enough free space in {folder} to restore: the database is {db_backup.format_bytes(size)} "
            f"and {db_backup.format_bytes(free)} is free (it needs {db_backup.format_bytes(size + RESTORE_MARGIN_BYTES)})."
        )


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


def _move_aside(db_path: Path, stamp: str) -> tuple[Optional[Path], int]:
    """Move the current database (with its WAL) aside, keeping every byte of it. Returns its new path and size."""
    if not db_path.exists():
        return None, 0
    with contextlib.suppress(sqlite3.Error):
        # Fold the WAL into the main file, so the copy kept aside is one complete file
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=5, isolation_level=None)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    aside = db_path.with_name(f"{db_path.name}{ASIDE_SUFFIX}{stamp}")
    size = db_path.stat().st_size
    os.replace(db_path, aside)
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists():
        size += wal.stat().st_size
        os.replace(wal, aside.with_name(aside.name + "-wal"))
    db_path.with_name(db_path.name + "-shm").unlink(missing_ok=True)
    return aside, size


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
        owner = db_backup._owner_to_apply(db_path if db_path.exists() else db_path.parent)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tmp = db_path.with_name(f".restore-{stamp}.db.tmp")
        try:
            manifest, db_name = _extract(archive, tmp, say)
            entry = _check_manifest(manifest, db_name, tmp, latest_schema)
            schema = manifest["schema_version"]
            _check_database(tmp, schema, say)
            os.chmod(tmp, 0o640)
            db_backup._give_to(tmp, owner)
            aside, aside_bytes = _move_aside(db_path, stamp)
            os.replace(tmp, db_path)
        finally:
            tmp.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                tmp.with_name(tmp.name + suffix).unlink(missing_ok=True)
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
        )
    finally:
        instance_lock.release(lock_fd)
