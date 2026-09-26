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
from typing import Any, Optional

from app.models import MaintenanceSchedule, MaintenanceWindowResponse

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


def get_maintenance_status(conn: sqlite3.Connection) -> MaintenanceWindowResponse:
    """Compute overall maintenance window status including on-demand and recurring schedules."""
    now_local, tz_name = _get_server_time_info()
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 1. Check on-demand maintenance_until
    cur = conn.cursor()
    cur.execute("SELECT value FROM system_settings WHERE key = 'maintenance_until'")
    row = cur.fetchone()
    raw_on_demand = str(row[0]).strip() if row and row[0] else None

    on_demand_active = False
    on_demand_until_dt: Optional[datetime.datetime] = None

    if raw_on_demand:
        try:
            dt = datetime.datetime.fromisoformat(raw_on_demand.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            if now_utc < dt:
                on_demand_active = True
                on_demand_until_dt = dt
        except Exception:
            pass

    # 2. Check scheduled maintenance windows
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

    # Determine overall status
    is_overall_active = on_demand_active or scheduled_active

    until_iso: Optional[str] = None
    reason: Optional[str] = None
    schedule_name: Optional[str] = None

    if on_demand_active and scheduled_active:
        # Both active: take whichever ends later
        sched_end_utc = active_schedule_end_dt.astimezone(datetime.timezone.utc) if active_schedule_end_dt else now_utc
        if on_demand_until_dt and on_demand_until_dt >= sched_end_utc:
            until_iso = on_demand_until_dt.isoformat()
            reason = "on_demand"
        else:
            until_iso = active_schedule_end_dt.isoformat() if active_schedule_end_dt else None
            reason = "scheduled"
            schedule_name = active_schedule_name
    elif on_demand_active:
        until_iso = on_demand_until_dt.isoformat() if on_demand_until_dt else None
        reason = "on_demand"
    elif scheduled_active:
        until_iso = active_schedule_end_dt.isoformat() if active_schedule_end_dt else None
        reason = "scheduled"
        schedule_name = active_schedule_name

    return MaintenanceWindowResponse(
        active=is_overall_active,
        until=until_iso,
        reason=reason,
        schedule_name=schedule_name,
        on_demand_until=on_demand_until_dt.isoformat() if on_demand_active and on_demand_until_dt else None,
        schedules=evaluated_schedules,
        server_time=now_local.isoformat(),
        server_timezone=tz_name,
    )
