"""
Start-up states while the database is backed up and upgraded, and the recovery state after a failure.

A new install or a database already at the newest schema starts as it always has: the lifespan
migrates and starts the workers before the web server accepts requests ("ready" throughout). An
existing database with steps pending, or one left with a failure marker, takes the upgrade path:
the web server starts at once, answers with a status page or 503 while the backup and migration
run in a background task ("upgrading"), and starts the workers when they finish. If the backup
or a step fails, LogShed stays in "recovery" until the admin retries from the web UI; nothing is
received or stored in the meantime.

The failure marker (`.migration_failed` beside the database) keeps a restart from running the same
failing upgrade again (and making another backup each time): with it, start-up goes straight to
recovery. It is removed when a different version of LogShed starts (a fixed release, or a
reverted tag), or when the database already reached the schema the marker names.

The state lives in this module, never on `app.state`: tests share one cached app.
"""

import datetime
import json
import logging
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

from app.version import APP_VERSION

logger = logging.getLogger(__name__)

READY = "ready"
UPGRADING = "upgrading"
RECOVERY = "recovery"

MARKER_NAME = ".migration_failed"
# The error text kept in the marker and shown to the admin; a long SQL error is cut here.
MAX_ERROR_CHARS = 2000

# What start-up does with the database (start_up_plan)
PLAN_FAST = "fast"
PLAN_UPGRADE = "upgrade"
PLAN_RECOVERY = "recovery"


@dataclass
class _State:
    status: str = READY
    stage: str = ""  # "backup", "migrate" or "starting" while upgrading
    done: int = 0
    total: int = 0
    from_schema: int = 0
    target_schema: int = 0
    marker: dict[str, Any] = field(default_factory=dict)


_lock = threading.Lock()
_state = _State()
_cancel = threading.Event()


def status() -> str:
    return _state.status


def is_ready() -> bool:
    return _state.status == READY


def cancel_event() -> threading.Event:
    """Set at shutdown: a running backup stops at its next chunk and no further migration step starts."""
    return _cancel


def snapshot() -> dict[str, Any]:
    """The status and progress, safe to show anyone who can reach the web port (no error text)."""
    with _lock:
        s = _state
        if s.status == UPGRADING:
            return {
                "status": UPGRADING,
                "stage": s.stage,
                "done": s.done,
                "total": s.total,
                "from_schema": s.from_schema,
                "target_schema": s.target_schema,
            }
        if s.status == RECOVERY:
            return {
                "status": RECOVERY,
                "stage": s.marker.get("stage", ""),
                "from_schema": s.marker.get("from_schema", 0),
                "target_schema": s.marker.get("target_schema", 0),
                "failed_at": s.marker.get("failed_at"),
            }
        return {"status": READY}


def recovery_marker() -> dict[str, Any]:
    """The failure marker while in recovery (empty otherwise), error text included: for signed-in admins."""
    with _lock:
        return dict(_state.marker) if _state.status == RECOVERY else {}


def set_upgrading(from_schema: int, target_schema: int) -> None:
    global _state
    with _lock:
        _state = _State(status=UPGRADING, stage="backup", from_schema=from_schema, target_schema=target_schema)


def report_progress(stage: str, done: int, total: int) -> None:
    """Progress from the migration thread (run_migrations' `progress` callback)."""
    with _lock:
        if _state.status == UPGRADING:
            _state.stage, _state.done, _state.total = stage, done, total


def set_starting() -> None:
    with _lock:
        if _state.status == UPGRADING:
            _state.stage, _state.done, _state.total = "starting", 0, 0


def set_recovery(marker: dict[str, Any]) -> None:
    global _state
    with _lock:
        _state = _State(
            status=RECOVERY,
            stage=marker.get("stage", ""),
            from_schema=marker.get("from_schema", 0),
            target_schema=marker.get("target_schema", 0),
            marker=dict(marker),
        )


def set_ready() -> None:
    global _state
    with _lock:
        _state = _State()


# handler(skip_backup, marker of the failed run)
RetryHandler = Callable[[bool, dict[str, Any]], Awaitable[None]]
_retry_handler: Optional[RetryHandler] = None


def set_retry_handler(handler: Optional[RetryHandler]) -> None:
    """The lifespan's retry (set while it runs on the upgrade path)."""
    global _retry_handler
    _retry_handler = handler


async def request_retry(skip_backup: bool) -> bool:
    """
    Leave recovery and run the upgrade again (without a backup when `skip_backup`). False when
    LogShed is not in recovery (another request already retried, for example).
    """
    with _lock:
        if _state.status != RECOVERY or _retry_handler is None:
            return False
        # Claim the retry under the lock, so two clicks cannot start two upgrades
        marker = dict(_state.marker)
        _state.status = UPGRADING
        _state.stage = "backup"
    try:
        await _retry_handler(skip_backup, marker)
    except BaseException:
        set_recovery(marker)
        raise
    return True


def reset() -> None:
    """Back to "ready" with no cancel request (the end of each lifespan, and tests)."""
    set_ready()
    _cancel.clear()


# --- The failure marker ---

def marker_path(db_path: Union[str, Path]) -> Path:
    return Path(db_path).parent / MARKER_NAME


def read_marker(db_path: Union[str, Path]) -> Optional[dict[str, Any]]:
    """The marker's contents, or None when there is none. An unreadable marker counts as one from another version."""
    path = marker_path(db_path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning(f"Could not read the failed upgrade marker {path}: {e}")
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def write_marker(db_path: Union[str, Path], marker: dict[str, Any]) -> None:
    """Write the marker atomically, readable only by LogShed's user and group (the error may name paths)."""
    path = marker_path(db_path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def clear_marker(db_path: Union[str, Path]) -> None:
    try:
        marker_path(db_path).unlink()
    except FileNotFoundError:
        pass


def new_marker(
    stage: str,
    from_schema: int,
    target_schema: int,
    current_schema: int,
    error: BaseException,
    backup_filename: Optional[str],
) -> dict[str, Any]:
    return {
        "app_version": APP_VERSION,
        "from_schema": from_schema,
        "target_schema": target_schema,
        "current_schema": current_schema,
        "failed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stage": stage,
        "backup_filename": backup_filename,
        "error": str(error)[:MAX_ERROR_CHARS] or type(error).__name__,
    }


def read_user_version(db_path: Path) -> int:
    """PRAGMA user_version without changing the file (0 when there is no database yet)."""
    if not db_path.exists():
        return 0
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        return int(conn.execute("PRAGMA user_version;").fetchone()[0])


def start_up_plan(db_path: Union[str, Path], latest_schema: int) -> tuple[str, int, Optional[dict[str, Any]]]:
    """
    Decide how start-up treats the database, changing nothing but a stale marker.
    Returns (plan, user_version, marker): PLAN_FAST (new install, current schema, or newer than this
    app, which run_migrations refuses), PLAN_UPGRADE (steps pending), or PLAN_RECOVERY (a marker from
    this version for a schema not yet reached).
    """
    db_path = Path(db_path)
    version = read_user_version(db_path)
    marker = read_marker(db_path)
    if marker is not None:
        stale = marker.get("app_version") != APP_VERSION or version >= int(marker.get("target_schema") or 0)
        if stale or version > latest_schema:
            logger.info(
                f"Removing the failed upgrade marker left by LogShed {marker.get('app_version') or 'unknown'}: "
                + ("this is a different version, so the upgrade is tried again." if marker.get("app_version") != APP_VERSION
                   else "the database has reached the schema it was upgrading to.")
            )
            clear_marker(db_path)
            marker = None
    if version > latest_schema:
        return PLAN_FAST, version, None
    if marker is not None:
        return PLAN_RECOVERY, version, marker
    if 0 < version < latest_schema:
        return PLAN_UPGRADE, version, None
    return PLAN_FAST, version, None
