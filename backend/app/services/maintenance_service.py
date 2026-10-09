"""
Maintenance Window Service for LogShed.

Provides business logic for checking, creating, and updating both on-demand
alert maintenance windows and recurring maintenance schedules (daily, weekly, monthly).
Stored in SQLite system_settings key-value store.
"""

import calendar
import datetime
import json
import logging
import sqlite3
import uuid
import threading
from typing import Any, Optional

from app.core.utils import match_wildcard, parse_iso_to_utc_datetime
from app.models import MaintenanceSchedule, MaintenanceSessionDetail, MaintenanceWindowResponse

logger = logging.getLogger(__name__)

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _get_server_time_info() -> tuple[datetime.datetime, str]:
    """Get current local server datetime and timezone abbreviation."""
    now_local = datetime.datetime.now().astimezone()
    tz_name = now_local.tzname() or "UTC"
    return now_local, tz_name


def _clamp_day_of_month(year: int, month: int, day: int) -> int:
    """Clamp day of month to maximum days in given year/month (e.g. Feb 28/29)."""
    max_days = calendar.monthrange(year, month)[1]
    return min(max(1, day), max_days)


def evaluate_schedule(
    schedule: MaintenanceSchedule,
    now_local: datetime.datetime,
) -> tuple[bool, Optional[datetime.datetime], Optional[datetime.datetime]]:
    """
    Evaluate whether a maintenance schedule is currently active, and compute
    its active end time (if active) and next occurrence start time.

    Returns:
        (is_active, active_end_dt, next_start_dt)
    """
    if not schedule.enabled:
        return False, None, None

    try:
        time_parts = schedule.start_time.split(":")
        start_hour = int(time_parts[0])
        start_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
    except (ValueError, IndexError):
        start_hour = 2
        start_minute = 0

    duration = datetime.timedelta(minutes=max(1, schedule.duration_minutes))
    recurrence = (schedule.recurrence or "weekly").lower()

    is_active = False
    active_end_dt: Optional[datetime.datetime] = None
    next_start_dt: Optional[datetime.datetime] = None

    if recurrence == "daily":
        # Today's window
        start_today = now_local.replace(
            hour=start_hour, minute=start_minute, second=0, microsecond=0
        )
        end_today = start_today + duration

        # Yesterday's window (in case it crossed midnight into today)
        start_yesterday = start_today - datetime.timedelta(days=1)
        end_yesterday = start_yesterday + duration

        if start_yesterday <= now_local < end_yesterday:
            is_active = True
            active_end_dt = end_yesterday
        elif start_today <= now_local < end_today:
            is_active = True
            active_end_dt = end_today

        if now_local < start_today:
            next_start_dt = start_today
        else:
            next_start_dt = start_today + datetime.timedelta(days=1)

    elif recurrence == "weekly":
        # Target day of week: 0=Sunday, 1=Monday, ..., 6=Saturday
        target_dow = (schedule.day_of_week if schedule.day_of_week is not None else 0) % 7
        # Convert Python weekday (0=Monday..6=Sunday) to 0=Sunday..6=Saturday
        current_dow = (now_local.weekday() + 1) % 7

        days_since = (current_dow - target_dow) % 7
        start_this_week = (now_local - datetime.timedelta(days=days_since)).replace(
            hour=start_hour, minute=start_minute, second=0, microsecond=0
        )
        end_this_week = start_this_week + duration

        # Check previous week in case window crossed week boundary
        start_prev_week = start_this_week - datetime.timedelta(days=7)
        end_prev_week = start_prev_week + duration

        if start_prev_week <= now_local < end_prev_week:
            is_active = True
            active_end_dt = end_prev_week
        elif start_this_week <= now_local < end_this_week:
            is_active = True
            active_end_dt = end_this_week

        days_until = (target_dow - current_dow) % 7
        if days_until == 0:
            if now_local < start_this_week:
                next_start_dt = start_this_week
            else:
                next_start_dt = start_this_week + datetime.timedelta(days=7)
        else:
            next_start_dt = (now_local + datetime.timedelta(days=days_until)).replace(
                hour=start_hour, minute=start_minute, second=0, microsecond=0
            )

    elif recurrence == "monthly":
        target_dom = schedule.day_of_month if schedule.day_of_month is not None else 1

        # Check current month
        day_curr = _clamp_day_of_month(now_local.year, now_local.month, target_dom)
        start_curr = now_local.replace(
            day=day_curr, hour=start_hour, minute=start_minute, second=0, microsecond=0
        )
        end_curr = start_curr + duration

        # Check previous month in case window crossed month boundary
        prev_year = now_local.year if now_local.month > 1 else now_local.year - 1
        prev_month = now_local.month - 1 if now_local.month > 1 else 12
        day_prev = _clamp_day_of_month(prev_year, prev_month, target_dom)
        start_prev = now_local.replace(
            year=prev_year,
            month=prev_month,
            day=day_prev,
            hour=start_hour,
            minute=start_minute,
            second=0,
            microsecond=0,
        )
        end_prev = start_prev + duration

        if start_prev <= now_local < end_prev:
            is_active = True
            active_end_dt = end_prev
        elif start_curr <= now_local < end_curr:
            is_active = True
            active_end_dt = end_curr

        if now_local < start_curr:
            next_start_dt = start_curr
        else:
            next_year = now_local.year if now_local.month < 12 else now_local.year + 1
            next_month = now_local.month + 1 if now_local.month < 12 else 1
            day_next = _clamp_day_of_month(next_year, next_month, target_dom)
            next_start_dt = now_local.replace(
                year=next_year,
                month=next_month,
                day=day_next,
                hour=start_hour,
                minute=start_minute,
                second=0,
                microsecond=0,
            )

    return is_active, active_end_dt, next_start_dt


def load_schedules(conn: sqlite3.Connection) -> list[MaintenanceSchedule]:
    """Load and parse maintenance schedules from system_settings."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_settings WHERE key = 'maintenance_schedules'")
    row = cur.fetchone()
    if not row or not row[0]:
        return []

    try:
        raw_list = json.loads(row[0])
        if not isinstance(raw_list, list):
            return []
        schedules = []
        for item in raw_list:
            if isinstance(item, dict):
                # Ensure valid id
                if "id" not in item or not item["id"]:
                    item["id"] = f"sched_{uuid.uuid4().hex[:8]}"
                schedules.append(MaintenanceSchedule(**item))
        return schedules
    except Exception as exc:
        logger.warning(f"Error loading maintenance schedules: {exc}")
        return []


def save_schedules(conn: sqlite3.Connection, schedules: list[MaintenanceSchedule]) -> None:
    """Save maintenance schedules to system_settings."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    raw_json = json.dumps([s.model_dump() for s in schedules])
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO system_settings (key, value, updated_at, is_encrypted)
        VALUES ('maintenance_schedules', ?, ?, 0)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (raw_json, now_iso),
    )
    conn.commit()


_active_maintenance_sessions: list[dict[str, Any]] = []
_active_sessions_lock = threading.Lock()


def _has_maintenance_sessions_table(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='maintenance_sessions'")
    return cur.fetchone() is not None


def get_cached_maintenance_sessions() -> list[dict[str, Any]]:
    """Return a copy of the in-memory active maintenance sessions list."""
    with _active_sessions_lock:
        return list(_active_maintenance_sessions)


def update_cached_maintenance_sessions(sessions: list[dict[str, Any]]) -> None:
    """Update the in-memory active maintenance sessions list."""
    global _active_maintenance_sessions
    with _active_sessions_lock:
        _active_maintenance_sessions = list(sessions)


def refresh_maintenance_cache(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Query active and unexpired maintenance sessions from SQLite and refresh in-memory cache.
    Also sweeps expired sessions to keep database state current.
    """
    if not _has_maintenance_sessions_table(conn):
        update_cached_maintenance_sessions([])
        return []

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_utc.isoformat()

    conn.execute(
        "UPDATE maintenance_sessions SET is_active = 0 WHERE is_active = 1 AND expires_at <= ?",
        (now_iso,),
    )
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        """
        SELECT session_id, token_id, initiated_by, reason, log_handling,
               target_app, target_host, created_at, expires_at
        FROM maintenance_sessions
        WHERE is_active = 1 AND expires_at > ?
        ORDER BY expires_at DESC
        """,
        (now_iso,),
    )
    rows = cur.fetchall()
    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r["session_id"],
            "token_id": r["token_id"],
            "initiated_by": r["initiated_by"],
            "reason": r["reason"],
            "log_handling": r["log_handling"] or "silence_alerts",
            "target_app": r["target_app"],
            "target_host": r["target_host"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
        })
    update_cached_maintenance_sessions(sessions)
    return sessions


def should_drop_maintenance_error(
    source_alias: Optional[str],
    source_ip: Optional[str],
    app_name: Optional[str],
    severity: Optional[int],
) -> bool:
    """
    Fast in-memory evaluation for error log dropping during maintenance windows.
    Zero SQLite lookups per log line.
    Drops if severity <= 3 (Emergency, Alert, Critical, Error) and matches active session filters.
    """
    if severity is None or severity > 3:
        return False

    with _active_sessions_lock:
        active_sessions = _active_maintenance_sessions

    if not active_sessions:
        return False

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for s in active_sessions:
        if s.get("log_handling") != "drop_errors":
            continue

        exp_str = s.get("expires_at")
        if exp_str:
            exp_dt = parse_iso_to_utc_datetime(exp_str)
            if exp_dt and exp_dt <= now_utc:
                continue

        target_app = s.get("target_app")
        target_host = s.get("target_host")

        if target_app and not match_wildcard(target_app, app_name):
            continue

        if target_host:
            matched_host = False
            if source_alias and match_wildcard(target_host, source_alias):
                matched_host = True
            elif source_ip and match_wildcard(target_host, source_ip):
                matched_host = True
            if not matched_host:
                continue

        return True

    return False


def create_maintenance_session(
    conn: sqlite3.Connection,
    duration_minutes: Optional[int] = None,
    expires_at: Optional[datetime.datetime] = None,
    reason: Optional[str] = None,
    log_handling: str = "silence_alerts",
    target_app: Optional[str] = None,
    target_host: Optional[str] = None,
    initiated_by: str = "api_token",
    token_id: Optional[int] = None,
) -> dict[str, Any]:
    """Start a new on-demand maintenance session and update in-memory cache."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if expires_at is not None:
        expires_dt = expires_at.astimezone(datetime.timezone.utc)
        computed_duration = max(1, int((expires_dt - now_utc).total_seconds() / 60))
    else:
        duration_mins = duration_minutes if duration_minutes is not None else 60
        expires_dt = now_utc + datetime.timedelta(minutes=duration_mins)
        computed_duration = duration_mins
    session_id = str(uuid.uuid4())

    now_iso = now_utc.isoformat()
    expires_iso = expires_dt.isoformat()

    conn.execute(
        """
        INSERT INTO maintenance_sessions (
            session_id, token_id, initiated_by, reason, log_handling,
            target_app, target_host, created_at, expires_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            session_id,
            token_id,
            initiated_by,
            reason,
            log_handling,
            target_app,
            target_host,
            now_iso,
            expires_iso,
        ),
    )
    conn.commit()
    refresh_maintenance_cache(conn)

    return {
        "session_id": session_id,
        "active": True,
        "until": expires_iso,
        "duration_minutes": computed_duration,
        "reason": reason,
        "log_handling": log_handling,
        "target_app": target_app,
        "target_host": target_host,
        "initiated_by": initiated_by,
        "server_time": now_iso,
    }


def disable_maintenance_sessions(
    conn: sqlite3.Connection,
    session_id: Optional[str] = None,
) -> tuple[int, int]:
    """
    Terminate one or all active maintenance sessions.
    Returns (terminated_count, remaining_active_count).
    """
    if not _has_maintenance_sessions_table(conn):
        return 0, 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur = conn.cursor()

    if session_id and session_id.strip():
        cur.execute(
            "UPDATE maintenance_sessions SET is_active = 0 WHERE session_id = ? AND is_active = 1",
            (session_id.strip(),),
        )
        terminated = cur.rowcount
    else:
        cur.execute("UPDATE maintenance_sessions SET is_active = 0 WHERE is_active = 1")
        terminated = cur.rowcount
        cur.execute(
            """
            INSERT INTO system_settings (key, value, updated_at, is_encrypted)
            VALUES ('maintenance_until', '', ?, 0)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (now_iso,),
        )

    conn.commit()
    refresh_maintenance_cache(conn)

    cur.execute(
        "SELECT COUNT(*) FROM maintenance_sessions WHERE is_active = 1 AND expires_at > ?",
        (now_iso,),
    )
    remaining = cur.fetchone()[0]
    return terminated, remaining


def get_maintenance_status(conn: sqlite3.Connection) -> MaintenanceWindowResponse:
    """Compute overall maintenance window status including on-demand, external API sessions, and recurring schedules."""
    now_local, tz_name = _get_server_time_info()
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 1. Sweep and load active on-demand API sessions
    active_sessions = refresh_maintenance_cache(conn)

    # 2. Check legacy on-demand maintenance_until
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_settings WHERE key = 'maintenance_until'")
    row = cur.fetchone()
    raw_on_demand = str(row[0]).strip() if row and row[0] else None

    on_demand_active = False
    on_demand_until_dt: Optional[datetime.datetime] = None

    if raw_on_demand:
        dt = parse_iso_to_utc_datetime(raw_on_demand)
        if dt and now_utc < dt:
            on_demand_active = True
            on_demand_until_dt = dt

    # 3. Check scheduled maintenance windows
    schedules = load_schedules(conn)
    evaluated_schedules: list[MaintenanceSchedule] = []
    scheduled_active = False
    active_schedule_end_dt: Optional[datetime.datetime] = None
    active_schedule_name: Optional[str] = None

    for sched in schedules:
        sched_active, sched_end_dt, next_start_dt = evaluate_schedule(sched, now_local)
        sched_copy = sched.model_copy()
        sched_copy.is_active = sched_active
        sched_copy.next_run = next_start_dt.isoformat() if next_start_dt else None
        evaluated_schedules.append(sched_copy)

        if sched_active and sched_end_dt:
            scheduled_active = True
            if active_schedule_end_dt is None or sched_end_dt > active_schedule_end_dt:
                active_schedule_end_dt = sched_end_dt
                active_schedule_name = sched.name

    # Determine latest expiration timestamp across all active sources
    all_end_times: list[datetime.datetime] = []
    if on_demand_active and on_demand_until_dt:
        all_end_times.append(on_demand_until_dt)
    if scheduled_active and active_schedule_end_dt:
        all_end_times.append(active_schedule_end_dt.astimezone(datetime.timezone.utc))

    session_details: list[MaintenanceSessionDetail] = []
    has_drop_errors = False

    for s in active_sessions:
        exp_dt = parse_iso_to_utc_datetime(s["expires_at"])
        if exp_dt and exp_dt > now_utc:
            all_end_times.append(exp_dt)
            if s.get("log_handling") == "drop_errors":
                has_drop_errors = True
            session_details.append(
                MaintenanceSessionDetail(
                    session_id=s["session_id"],
                    reason=s.get("reason"),
                    log_handling=s.get("log_handling", "silence_alerts"),
                    target_app=s.get("target_app"),
                    target_host=s.get("target_host"),
                    initiated_by=s.get("initiated_by", "api_token"),
                    expires_at=s["expires_at"],
                )
            )

    is_overall_active = bool(all_end_times)
    latest_end_dt: Optional[datetime.datetime] = max(all_end_times) if all_end_times else None
    remaining_seconds: int = max(0, int((latest_end_dt - now_utc).total_seconds())) if latest_end_dt else 0

    until_iso: Optional[str] = latest_end_dt.isoformat() if latest_end_dt else None
    reason: Optional[str] = None
    schedule_name: Optional[str] = None

    if on_demand_active:
        reason = "on_demand"
    elif session_details:
        reason = "api_session"
    elif scheduled_active:
        reason = "scheduled"
        schedule_name = active_schedule_name

    log_handling = "drop_errors" if has_drop_errors else "silence_alerts"

    return MaintenanceWindowResponse(
        active=is_overall_active,
        until=until_iso,
        reason=reason,
        schedule_name=schedule_name,
        on_demand_until=on_demand_until_dt.isoformat() if on_demand_active and on_demand_until_dt else None,
        schedules=evaluated_schedules,
        server_time=now_local.isoformat(),
        server_timezone=tz_name,
        remaining_seconds=remaining_seconds,
        active_sessions_count=len(session_details),
        sessions=session_details,
        log_handling=log_handling,
    )

