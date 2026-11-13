"""
Recovery mode after a failed upgrade (core/lifecycle.py): what went wrong, and the two ways on.

Retry runs the upgrade again, reusing the backup the failed run made when the database is still
at its schema. Migrate without a backup is for a disk with no room for one. Both answer at once;
the web UI then follows /api/health until LogShed is ready or back in recovery.
"""

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.core import backup as db_backup
from app.core import lifecycle
from app.core.config import get_db_path
from app.core.utils import get_server_timezone_name
from app.models import MessageResponse, RecoveryResponse

router = APIRouter(prefix="/system/recovery", tags=["System & Maintenance"])


def _recovery_details(db_path: Path) -> RecoveryResponse:
    marker = lifecycle.recovery_marker()
    if not marker:
        return RecoveryResponse(status=lifecycle.status())
    folder = db_backup.backups_dir(db_path)
    name = marker.get("backup_filename")
    response = RecoveryResponse(
        status=lifecycle.RECOVERY,
        app_version=marker.get("app_version"),
        stage=marker.get("stage"),
        from_schema=marker.get("from_schema"),
        target_schema=marker.get("target_schema"),
        current_schema=marker.get("current_schema"),
        failed_at=marker.get("failed_at"),
        error=marker.get("error"),
        backup_filename=name,
        backup_available=bool(name) and db_backup.find_backup(folder, name) is not None,
        server_timezone=get_server_timezone_name(),
    )
    try:
        usage = shutil.disk_usage(folder if folder.exists() else db_path.parent)
        response.disk_free_bytes = usage.free
        response.disk_total_bytes = usage.total
        response.backup_floor_bytes = db_backup.free_space_floor(usage.total)
        response.database_size_bytes = db_path.stat().st_size
        response.backup_needed_bytes = db_backup.backup_space_needed(db_path, db_backup.METHOD_LOCKED_STREAM)
    except OSError:
        pass
    return response


@router.get("", response_model=RecoveryResponse)
async def recovery_details(user: dict = Depends(get_current_user)) -> RecoveryResponse:
    """The failed upgrade: stage, error, schemas, the backup it made, and the disk figures."""
    return await asyncio.to_thread(_recovery_details, get_db_path())


async def _retry(skip_backup: bool) -> MessageResponse:
    if not await lifecycle.request_retry(skip_backup):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LogShed is not in recovery mode.")
    return MessageResponse(status="upgrading")


@router.post("/retry", response_model=MessageResponse)
async def retry_upgrade(user: dict = Depends(get_current_user)) -> MessageResponse:
    """Run the upgrade again, backing up first unless the failed run's backup can be reused."""
    return await _retry(skip_backup=False)


@router.post("/skip-backup", response_model=MessageResponse)
async def upgrade_without_backup(user: dict = Depends(get_current_user)) -> MessageResponse:
    """Run the upgrade again without a backup (when the disk has no room for one)."""
    return await _retry(skip_backup=True)
