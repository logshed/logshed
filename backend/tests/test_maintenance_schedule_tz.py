"""
Tests for recurring maintenance schedule evaluation across daylight saving time changes.

Schedules are wall-clock times in the server zone, so each start time must carry the offset in
force on its own date and a window must last its real duration. All datetimes here are passed in
explicitly (ZoneInfo), so the tests do not depend on the machine's TZ.
"""

import datetime
import zoneinfo
from pathlib import Path

import pytest

from app.core import utils
from app.core.migrations import get_connection, run_migrations
from app.models import MaintenanceSchedule
from app.services import maintenance_service as ms
from app.services.maintenance_service import evaluate_schedule

UTC = datetime.timezone.utc
LONDON = zoneinfo.ZoneInfo("Europe/London")
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")

# Europe/London: clocks go back 02:00 BST -> 01:00 GMT on Sun 2026-10-25 (01:00 UTC) and forward
# 01:00 GMT -> 02:00 BST on Sun 2027-03-28 (01:00 UTC).
# America/New_York: clocks go back 02:00 EDT -> 01:00 EST on Sun 2026-11-01 and forward
# 02:00 EST -> 03:00 EDT on Sun 2027-03-14.


def _local(tz, *args) -> datetime.datetime:
    """Wall-clock datetime in tz (first occurrence when a time repeats)."""
    return datetime.datetime(*args, tzinfo=tz)


def _utc_to(tz, *args) -> datetime.datetime:
    """A UTC instant shown in tz, so a time inside a repeated hour gets the right offset."""
    return datetime.datetime(*args, tzinfo=UTC).astimezone(tz)


def _sched(recurrence="weekly", start_time="02:00", duration=60, dow=0, dom=1) -> MaintenanceSchedule:
    return MaintenanceSchedule(
        id="s1",
        name="Test",
        recurrence=recurrence,
        start_time=start_time,
        duration_minutes=duration,
        day_of_week=dow,
        day_of_month=dom,
    )


def _iso(dt: datetime.datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class TestWeeklyAcrossDstChange:
    def test_next_sunday_run_after_autumn_change_uses_new_offset(self):
        # The reported bug: from BST, Sunday 02:00 on the change day is 02:00 GMT (02:00 UTC),
        # not 02:00+01:00.
        active, end, nxt = evaluate_schedule(_sched(), _local(LONDON, 2026, 10, 21, 10, 0))
        assert active is False and end is None
        assert _iso(nxt) == "2026-10-25T02:00:00+00:00"
        assert nxt.astimezone(UTC) == datetime.datetime(2026, 10, 25, 2, 0, tzinfo=UTC)

    def test_next_sunday_run_after_spring_change_uses_new_offset(self):
        _, _, nxt = evaluate_schedule(_sched(), _local(LONDON, 2027, 3, 24, 10, 0))
        assert _iso(nxt) == "2027-03-28T02:00:00+01:00"
        assert nxt.astimezone(UTC) == datetime.datetime(2027, 3, 28, 1, 0, tzinfo=UTC)

    def test_run_a_week_after_the_change_keeps_new_offset(self):
        # On the change day itself, once 02:00 GMT has passed, the next run is a week later.
        _, _, nxt = evaluate_schedule(_sched(), _utc_to(LONDON, 2026, 10, 25, 3, 0))
        assert _iso(nxt) == "2026-11-01T02:00:00+00:00"

    def test_start_in_skipped_hour_moves_forward_by_the_gap(self):
        # 01:30 does not exist on 2027-03-28 (01:00 GMT jumps to 02:00 BST): 01:30 becomes 02:30 BST.
        _, _, nxt = evaluate_schedule(_sched(start_time="01:30"), _local(LONDON, 2027, 3, 24, 10, 0))
        assert _iso(nxt) == "2027-03-28T02:30:00+01:00"

    def test_start_in_repeated_hour_uses_first_occurrence(self):
        # 01:30 happens twice on 2026-10-25 (BST, then GMT): the window starts the first time.
        _, _, nxt = evaluate_schedule(_sched(start_time="01:30"), _local(LONDON, 2026, 10, 21, 10, 0))
        assert _iso(nxt) == "2026-10-25T01:30:00+01:00"
        assert nxt.astimezone(UTC) == datetime.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)

    def test_window_spanning_autumn_change_lasts_real_duration(self):
        # Sunday 00:30 BST + 120 real minutes ends at 01:30 GMT (01:30 UTC), not 02:30.
        sched = _sched(start_time="00:30", duration=120)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 25, 1, 15))
        assert active is True
        assert _iso(end) == "2026-10-25T01:30:00+00:00"

        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 25, 1, 30))
        assert active is False and end is None

    def test_window_spanning_spring_change_lasts_real_duration(self):
        # Sunday 00:30 GMT + 120 real minutes ends at 03:30 BST (02:30 UTC), not 02:30.
        sched = _sched(start_time="00:30", duration=120)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2027, 3, 28, 1, 45))
        assert active is True
        assert _iso(end) == "2027-03-28T03:30:00+01:00"

        active, _, _ = evaluate_schedule(sched, _utc_to(LONDON, 2027, 3, 28, 2, 30))
        assert active is False

    def test_us_zone_autumn_and_spring(self):
        _, _, nxt = evaluate_schedule(_sched(), _local(NEW_YORK, 2026, 10, 28, 10, 0))
        assert _iso(nxt) == "2026-11-01T02:00:00-05:00"
        # 02:30 does not exist on 2027-03-14 (02:00 EST jumps to 03:00 EDT): it becomes 03:30 EDT.
        _, _, nxt = evaluate_schedule(_sched(start_time="02:30"), _local(NEW_YORK, 2027, 3, 10, 10, 0))
        assert _iso(nxt) == "2027-03-14T03:30:00-04:00"

    def test_window_running_into_next_day_after_change_stays_active(self):
        # Sunday 25 Oct 23:00 GMT + 24 real hours: still running on Monday morning.
        sched = _sched(start_time="23:00", duration=1440)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 26, 10, 0))
        assert active is True
        assert _iso(end) == "2026-10-26T23:00:00+00:00"


class TestDailyAcrossDstChange:
    def test_next_run_on_change_day_uses_new_offset(self):
        _, _, nxt = evaluate_schedule(_sched("daily"), _local(LONDON, 2026, 10, 24, 20, 0))
        assert _iso(nxt) == "2026-10-25T02:00:00+00:00"

        _, _, nxt = evaluate_schedule(_sched("daily"), _local(LONDON, 2027, 3, 27, 20, 0))
        assert _iso(nxt) == "2027-03-28T02:00:00+01:00"

    def test_next_run_after_todays_has_passed_uses_new_offset(self):
        # Saturday 03:00 BST: today's 02:00 has passed, tomorrow is the 25 Oct change day.
        _, _, nxt = evaluate_schedule(_sched("daily"), _local(LONDON, 2026, 10, 24, 3, 0))
        assert _iso(nxt) == "2026-10-25T02:00:00+00:00"

    def test_start_in_skipped_and_repeated_hours(self):
        _, _, nxt = evaluate_schedule(_sched("daily", "01:30"), _local(LONDON, 2027, 3, 27, 20, 0))
        assert _iso(nxt) == "2027-03-28T02:30:00+01:00"
        _, _, nxt = evaluate_schedule(_sched("daily", "01:30"), _local(LONDON, 2026, 10, 24, 20, 0))
        assert _iso(nxt) == "2026-10-25T01:30:00+01:00"

    def test_ninety_minute_window_spanning_autumn_change_is_ninety_real_minutes(self):
        # 01:30 BST (00:30 UTC) + 90 real minutes ends at 02:00 UTC, which is 02:00 GMT.
        sched = _sched("daily", "01:30", duration=90)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 25, 1, 45))
        assert active is True
        assert _iso(end) == "2026-10-25T02:00:00+00:00"
        active, _, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 25, 2, 0))
        assert active is False

    def test_window_started_yesterday_runs_through_the_change(self):
        # Saturday 22:00 BST + 240 real minutes ends 01:00 UTC, which is 01:00 GMT (not 02:00).
        sched = _sched("daily", "22:00", duration=240)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 25, 0, 30))
        assert active is True
        assert _iso(end) == "2026-10-25T01:00:00+00:00"

    def test_ninety_minute_window_spanning_spring_change_is_ninety_real_minutes(self):
        # 00:45 GMT + 90 real minutes ends 02:15 UTC, which is 03:15 BST.
        sched = _sched("daily", "00:45", duration=90)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2027, 3, 28, 1, 30))
        assert active is True
        assert _iso(end) == "2027-03-28T03:15:00+01:00"

    def test_us_zone_window_spanning_autumn_change(self):
        # 00:30 EDT (04:30 UTC) + 120 real minutes ends 06:30 UTC = 01:30 EST.
        sched = _sched("daily", "00:30", duration=120)
        active, end, _ = evaluate_schedule(sched, _utc_to(NEW_YORK, 2026, 11, 1, 6, 0))
        assert active is True
        assert _iso(end) == "2026-11-01T01:30:00-05:00"


class TestMonthlyAcrossDstChange:
    def test_next_month_run_after_autumn_change_uses_new_offset(self):
        _, _, nxt = evaluate_schedule(_sched("monthly", dom=1), _local(LONDON, 2026, 10, 10, 10, 0))
        assert _iso(nxt) == "2026-11-01T02:00:00+00:00"

    def test_this_month_run_after_autumn_change_uses_new_offset(self):
        _, _, nxt = evaluate_schedule(_sched("monthly", dom=28), _local(LONDON, 2026, 10, 21, 10, 0))
        assert _iso(nxt) == "2026-10-28T02:00:00+00:00"

    def test_this_month_run_after_spring_change_uses_new_offset(self):
        _, _, nxt = evaluate_schedule(_sched("monthly", dom=28), _local(LONDON, 2027, 3, 10, 10, 0))
        assert _iso(nxt) == "2027-03-28T02:00:00+01:00"

    def test_next_year_rollover_keeps_zone_rules(self):
        _, _, nxt = evaluate_schedule(_sched("monthly", dom=1), _local(LONDON, 2026, 12, 15, 10, 0))
        assert _iso(nxt) == "2027-01-01T02:00:00+00:00"

    def test_window_on_change_day_lasts_real_duration(self):
        # Monthly on the 25th at 00:30 for 120 real minutes: 2026-10-25 is the autumn change day.
        sched = _sched("monthly", "00:30", duration=120, dom=25)
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 10, 25, 1, 15))
        assert active is True
        assert _iso(end) == "2026-10-25T01:30:00+00:00"

    def test_window_started_last_month_still_active(self):
        sched = _sched("monthly", "23:00", duration=180, dom=31)
        # 31 Oct 23:00 GMT + 3h ends 02:00 on 1 Nov
        active, end, _ = evaluate_schedule(sched, _utc_to(LONDON, 2026, 11, 1, 1, 0))
        assert active is True
        assert _iso(end) == "2026-11-01T02:00:00+00:00"


class TestOrdinaryDays:
    """Away from a clock change the offsets and results are the same as with a fixed offset."""

    def test_summer_weekly_daily_monthly(self):
        now = _local(LONDON, 2026, 7, 8, 10, 0)  # Wednesday, BST
        assert _iso(evaluate_schedule(_sched(), now)[2]) == "2026-07-12T02:00:00+01:00"
        assert _iso(evaluate_schedule(_sched("daily"), now)[2]) == "2026-07-09T02:00:00+01:00"
        assert _iso(evaluate_schedule(_sched("monthly", dom=15), now)[2]) == "2026-07-15T02:00:00+01:00"
        assert _iso(evaluate_schedule(_sched("monthly", dom=1), now)[2]) == "2026-08-01T02:00:00+01:00"

    def test_winter_active_window_and_end(self):
        sched = _sched("daily", "02:00", duration=60)
        active, end, nxt = evaluate_schedule(sched, _local(LONDON, 2026, 12, 3, 2, 30))
        assert active is True
        assert _iso(end) == "2026-12-03T03:00:00+00:00"
        assert _iso(nxt) == "2026-12-04T02:00:00+00:00"

    def test_before_and_after_window(self):
        sched = _sched("daily", "02:00", duration=60)
        active, end, nxt = evaluate_schedule(sched, _local(LONDON, 2026, 7, 8, 1, 59))
        assert (active, end) == (False, None)
        assert _iso(nxt) == "2026-07-08T02:00:00+01:00"
        active, _, nxt = evaluate_schedule(sched, _local(LONDON, 2026, 7, 8, 3, 0))
        assert active is False
        assert _iso(nxt) == "2026-07-09T02:00:00+01:00"

    def test_disabled_schedule(self):
        sched = _sched()
        sched.enabled = False
        assert evaluate_schedule(sched, _local(LONDON, 2026, 7, 8, 10, 0)) == (False, None, None)

    def test_fixed_offset_input_still_works(self):
        # The fallback when no zone can be resolved: a plain fixed offset behaves as before.
        tz = datetime.timezone(datetime.timedelta(hours=1))
        active, end, nxt = evaluate_schedule(_sched("daily", duration=90), datetime.datetime(2026, 10, 21, 2, 30, tzinfo=tz))
        assert active is True
        assert _iso(end) == "2026-10-21T03:30:00+01:00"
        assert _iso(nxt) == "2026-10-22T02:00:00+01:00"


class TestServerTimezoneResolution:
    def test_tz_env_gives_real_zone_rules(self, monkeypatch):
        monkeypatch.setenv("TZ", "Europe/London")
        tz = utils.get_server_tzinfo()
        assert isinstance(tz, zoneinfo.ZoneInfo) and tz.key == "Europe/London"
        assert datetime.datetime(2026, 7, 1, 12, tzinfo=tz).utcoffset() == datetime.timedelta(hours=1)
        assert datetime.datetime(2026, 12, 1, 12, tzinfo=tz).utcoffset() == datetime.timedelta(0)

    def test_leading_colon_is_accepted(self, monkeypatch):
        monkeypatch.setenv("TZ", ":America/New_York")
        assert utils.get_server_tzinfo().key == "America/New_York"

    @pytest.mark.parametrize("value", ["Not/AZone", "../etc/passwd", "UTC+3 nonsense"])
    def test_invalid_tz_falls_back_to_fixed_offset(self, monkeypatch, value):
        monkeypatch.setenv("TZ", value)
        tz = utils.get_server_tzinfo()
        assert not isinstance(tz, zoneinfo.ZoneInfo)
        assert tz.utcoffset(None) is not None

    def test_unset_tz_returns_a_usable_zone(self, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        tz = utils.get_server_tzinfo()
        assert datetime.datetime.now(tz).utcoffset() is not None

    def test_server_time_info_uses_zone_rules(self, monkeypatch):
        monkeypatch.setenv("TZ", "Europe/London")
        now_local, tz_name = ms._get_server_time_info()
        assert now_local.tzinfo.key == "Europe/London"
        assert tz_name in ("GMT", "BST")


class TestStatusNextRun:
    def test_status_next_run_reflects_dst_change(self, tmp_path: Path, monkeypatch):
        """The reported symptom: on 2026-10-21 (BST) 'Weekly on Sunday at 02:00' is 02:00 GMT."""
        db_path = tmp_path / "logs.db"
        run_migrations(db_path)
        monkeypatch.setattr(ms, "_get_server_time_info", lambda: (_local(LONDON, 2026, 10, 21, 12, 0), "BST"))

        conn = get_connection(db_path)
        try:
            ms.save_schedules(conn, [_sched("weekly", "02:00", 60, dow=0)])
            status = ms.get_maintenance_status(conn)
        finally:
            conn.close()

        assert status.schedules[0].next_run == "2026-10-25T02:00:00+00:00"
        assert status.schedules[0].is_active is False
