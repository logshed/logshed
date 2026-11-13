"""
Database backups from the web UI: the list, "Create backup now", download and delete.

A backup from the web UI copies with VACUUM INTO (core/backup.py), so logging carries on, and it
takes the shared operation lock, so it never overlaps a prune, a Compact or another backup. It
runs as a job: the request returns at once and the Storage page polls the job, because a backup
of a large database takes longer than a request should stay open.
"""

import asyncio
import datetime
import logging
import math
import sqlite3
import threading
from dataclasses import asdict, dataclass
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, run_db_query
from app.api.system import operation_conflict
from app.core import backup as db_backup
from app.core.config import get_db_path
from app.models import BackupItem, BackupJobResponse, BackupListResponse, MessageResponse
from app.services.operation_guard import OperationInProgressError, start_exclusive

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/backups", tags=["System & Maintenance"])


@dataclass
class _Job:
    state: str = "idle"  # idle, running, done or failed
    stage: Optional[str] = None  # copying, then compressing
    done: int = 0
    total: int = 0
    filename: Optional[str] = None
    size_bytes: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


_job_lock = threading.Lock()
_job = _Job()
_cancel = threading.Event()
_task: Optional[asyncio.Future] = None


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _update_job(**fields) -> None:
    with _job_lock:
        for key, value in fields.items():
            setattr(_job, key, value)


def _job_response() -> BackupJobResponse:
    with _job_lock:
        return BackupJobResponse(**asdict(_job))


def reset_backup_job() -> None:
    """Forget the last job (tests)."""
    global _job, _task
    with _job_lock:
        _job = _Job()
    _cancel.clear()
    _task = None


async def stop_backup_job(timeout: float = 10.0) -> None:
    """Stop a running backup at shutdown: it ends at its next chunk and deletes its unfinished files."""
    global _task
    task = _task
    if task is None or task.done():
        return
    _cancel.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except Exception:
        pass
    finally:
        _cancel.clear()
        _task = None


def _item(info: db_backup.BackupInfo, now: datetime.datetime) -> BackupItem:
    left = (info.expires_at - now).total_seconds()
    return BackupItem(
        filename=info.path.name,
        kind=info.kind,
        schema_version=info.schema_version,
        target_schema=info.target_schema,
        size_bytes=info.size_bytes,
        created_at=info.created_at.isoformat(),
        expires_at=info.expires_at.isoformat(),
        days_until_expiry=max(0, math.ceil(left / 86400)),
    )


@router.get("", response_model=BackupListResponse)
async def list_backups(user: dict = Depends(get_current_user)) -> BackupListResponse:
    """The archives in the backups folder, newest first, described from their names."""
    infos = await asyncio.to_thread(db_backup.list_backups, db_backup.backups_dir(get_db_path()))
    now = datetime.datetime.now(datetime.timezone.utc)
    return BackupListResponse(
        backups=[_item(i, now) for i in infos],
        total_size_bytes=sum(i.size_bytes for i in infos),
    )


@router.get("/current", response_model=BackupJobResponse)
async def current_backup(user: dict = Depends(get_current_user)) -> BackupJobResponse:
    """The web UI's backup job: running with its progress, or how the last one ended."""
    return _job_response()


def _schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version;").fetchone()[0])


@router.post("", response_model=BackupJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_backup_now(user: dict = Depends(get_current_user)) -> BackupJobResponse:
    """
    Start a backup (VACUUM INTO, so logging carries on). 400 with the figures when the disk has too
    little room for the copy and the archive; 409 while a prune, Compact or backup runs.
    """
    global _task
    db_path = get_db_path()
    folder = db_backup.backups_dir(db_path)

    def _check_space() -> None:
        db_backup.check_backup_space(folder, db_backup.backup_space_needed(db_path, db_backup.METHOD_VACUUM_INTO))

    try:
        await asyncio.to_thread(_check_space)
    except db_backup.BackupSpaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    schema = await run_db_query(_schema_version)

    async def _work() -> None:
        def _progress(done: int, total: int) -> None:
            _update_job(stage="compressing", done=done, total=total)

        try:
            result = await asyncio.to_thread(
                db_backup.create_backup,
                db_path,
                kind=f"manual-v{schema}",
                schema_version=schema,
                method=db_backup.METHOD_VACUUM_INTO,
                progress_cb=_progress,
                cancel=_cancel,
            )
        except db_backup.BackupCancelled:
            _update_job(state="failed", error="The backup stopped because LogShed is shutting down.", finished_at=_now_iso())
        except (db_backup.BackupError, OSError, sqlite3.Error) as e:
            logger.warning(f"The backup from the web UI failed: {e}")
            _update_job(state="failed", error=f"The backup failed: {e}", finished_at=_now_iso())
        else:
            _update_job(
                state="done", filename=result.path.name, size_bytes=result.size_bytes, finished_at=_now_iso()
            )

    try:
        task = start_exclusive("backup", _work)
    except OperationInProgressError as e:
        raise operation_conflict(e.active)
    # The task has not run yet (no await since it was created), so it finds the job already running
    _update_job(**asdict(_Job(state="running", stage="copying", started_at=_now_iso())))
    _task = task
    return _job_response()


def _find_or_404(filename: str):
    path = db_backup.find_backup(db_backup.backups_dir(get_db_path()), filename)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such backup.")
    return path


@router.get("/{filename}/download")
async def download_backup(filename: str, user: dict = Depends(get_current_user)) -> FileResponse:
    """The archive itself (also available in recovery mode)."""
    path = await asyncio.to_thread(_find_or_404, filename)
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@router.delete("/{filename}", response_model=MessageResponse)
async def delete_backup(filename: str, user: dict = Depends(get_current_user)) -> MessageResponse:
    def _delete() -> None:
        path = _find_or_404(filename)
        try:
            path.unlink()
        except FileNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such backup.")
        logger.info(f"Database backup {path.name} deleted from the web UI.")

    await asyncio.to_thread(_delete)
    return MessageResponse(status="ok")
