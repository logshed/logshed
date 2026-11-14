"""
What a maintenance window covers: a window with a target (an app, a host, or both) keeps only its
target's logs out of the alert rules and leaves every other alert alone; a window without one holds
back every notification, as before. The web UI starts either mode with targets, recurring schedules
carry a mode and targets too, and a schedule's window starts and ends on time in the in-memory
checks the ingest path and the evaluator run.
"""

import asyncio
import datetime
import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import get_connection as _raw_get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.models import MaintenanceSchedule
from app.services import maintenance_service as ms
from app.services.alert_evaluator import AlertEvaluator, init_alert_evaluator
from app.services.drop_filter import init_drop_filter
from tests.db_helpers import open_db


def _utc(**delta) -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**delta)


def _connection(path: Path) -> sqlite3.Connection:
    conn = _raw_get_connection(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    reset_crypto_cache()
    ms.update_cached_maintenance_sessions([])
    with ms._dropped_counters_lock:
        ms._dropped_counters.clear()
    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.setenv("TZ", "UTC")
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)
    yield db_file
    ms.update_cached_maintenance_sessions([])
    with ms._dropped_counters_lock:
        ms._dropped_counters.clear()
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def headers():
    return {"Cookie": f"{SESSION_COOKIE_NAME}={create_session_token(user_id=1)}", "x-requested-with": "XMLHttpRequest"}


def _window(**fields) -> dict:
    window = {"session_id": "s1", "log_handling": "silence_alerts", "expires_at": _utc(hours=1).isoformat()}
    window.update(fields)
    return window


class TestTargetCheck:
    """in_targeted_maintenance: the in-memory check of a log against windows with a target."""

    def test_no_windows_and_windows_without_a_target_target_nothing(self):
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "smartd") is False
        ms.update_cached_maintenance_sessions([_window()])
        assert ms.has_targeted_maintenance() is False
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "smartd") is False

    def test_app_target(self):
        ms.update_cached_maintenance_sessions([_window(target_app="plex*")])
        assert ms.has_targeted_maintenance() is True
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "plexmediaserver") is True
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "smartd") is False

    def test_host_target_by_name_or_address(self):
        ms.update_cached_maintenance_sessions([_window(target_host="nas")])
        assert ms.in_targeted_maintenance("NAS", "10.0.0.5", "smartd") is True
        ms.update_cached_maintenance_sessions([_window(target_host="10.0.0.*")])
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "smartd") is True
        assert ms.in_targeted_maintenance("router", "10.0.1.1", "smartd") is False

    def test_host_and_app_must_both_match(self):
        ms.update_cached_maintenance_sessions([_window(target_host="nas", target_app="smartd")])
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "smartd") is True
        assert ms.in_targeted_maintenance("nas", "10.0.0.5", "cron") is False
        assert ms.in_targeted_maintenance("router", "10.0.0.1", "smartd") is False

    def test_drop_errors_window_with_a_target_counts_too(self):
        ms.update_cached_maintenance_sessions([_window(log_handling="drop_errors", target_app="db")])
        assert ms.in_targeted_maintenance("h", "10.0.0.1", "db") is True

    def test_ended_and_not_yet_started_windows_target_nothing(self):
        ms.update_cached_maintenance_sessions([
            _window(session_id="ended", target_app="db", expires_at=_utc(seconds=-1).isoformat()),
            _window(session_id="later", target_app="db", starts_at=_utc(seconds=60).isoformat()),
        ])
        assert ms.in_targeted_maintenance("h", "10.0.0.1", "db") is False


class TestStatusSilencesAll:
    """silences_all_alerts says whether a running window holds back every notification."""

    def test_window_without_a_target_silences_all(self, env):
        with _connection(env) as conn:
            ms.create_maintenance_session(conn, duration_minutes=30)
            status = ms.get_maintenance_status(conn)
        assert status.active is True
        assert status.silences_all_alerts is True

    def test_window_with_a_target_silences_only_its_target(self, env):
        with _connection(env) as conn:
            ms.create_maintenance_session(conn, duration_minutes=30, target_host="nas")
            status = ms.get_maintenance_status(conn)
        assert status.active is True
        assert status.silences_all_alerts is False

    def test_untargeted_schedule_silences_all_and_targeted_does_not(self, env):
        now = datetime.datetime.now(datetime.timezone.utc)
        running = dict(recurrence="daily", start_time=(now - datetime.timedelta(minutes=5)).strftime("%H:%M"), duration_minutes=60)
        with _connection(env) as conn:
            ms.save_schedules(conn, [MaintenanceSchedule(id="a", name="Backup", target_app="restic", **running)])
            assert ms.get_maintenance_status(conn).silences_all_alerts is False
            ms.save_schedules(conn, [MaintenanceSchedule(id="b", name="Patch night", **running)])
            assert ms.get_maintenance_status(conn).silences_all_alerts is True

    def test_drop_errors_schedule_shows_its_mode(self, env):
        now = datetime.datetime.now(datetime.timezone.utc)
        with _connection(env) as conn:
            ms.save_schedules(conn, [MaintenanceSchedule(
                id="a", name="Backup", recurrence="daily", start_time=(now - datetime.timedelta(minutes=5)).strftime("%H:%M"),
                duration_minutes=60, log_handling="drop_errors", target_app="restic",
            )])
            status = ms.get_maintenance_status(conn)
        assert status.log_handling == "drop_errors"
        assert status.schedules[0].log_handling == "drop_errors"
        assert status.schedules[0].target_app == "restic"


class TestScheduleWindowsInTheCache:
    """A schedule's mode and targets reach the in-memory checks, on time."""

    def _schedule(self, start: datetime.datetime, **fields) -> MaintenanceSchedule:
        return MaintenanceSchedule(
            id="s", name="Backup", recurrence="daily", start_time=start.strftime("%H:%M"), duration_minutes=30, **fields
        )

    def test_running_drop_errors_schedule_drops_its_targets_errors(self, env):
        with _connection(env) as conn:
            ms.save_schedules(conn, [self._schedule(_utc(minutes=-5), log_handling="drop_errors", target_app="restic")])
            ms.refresh_maintenance_cache(conn)
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "restic", 3) == "schedule:s"
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "restic", 6) is None
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "smartd", 3) is None
        # A schedule's drops are not counted against any session
        ms.record_maintenance_drop("schedule:s")
        assert ms.get_pending_dropped_counts() == {}

    def test_schedule_without_a_target_drops_every_error(self, env):
        with _connection(env) as conn:
            ms.save_schedules(conn, [self._schedule(_utc(minutes=-5), log_handling="drop_errors")])
            ms.refresh_maintenance_cache(conn)
        assert ms.should_drop_maintenance_error("any", "10.0.0.9", "anything", 2) == "schedule:s"

    def test_schedule_starting_soon_is_cached_ahead_and_waits_for_its_start(self, env, monkeypatch):
        start = (_utc(minutes=1) + datetime.timedelta(seconds=60)).replace(second=0, microsecond=0)
        with _connection(env) as conn:
            ms.save_schedules(conn, [self._schedule(start, log_handling="drop_errors", target_app="restic")])
            ms.refresh_maintenance_cache(conn)
        (window,) = ms.get_cached_maintenance_sessions()
        assert window["starts_at"] == start.isoformat()
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "restic", 3) is None

        class _Later(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return start + datetime.timedelta(seconds=1)

        monkeypatch.setattr(ms.datetime, "datetime", _Later)
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "restic", 3) == "schedule:s"

    def test_schedule_far_off_is_not_cached(self, env):
        with _connection(env) as conn:
            ms.save_schedules(conn, [self._schedule(_utc(hours=3), log_handling="drop_errors")])
            ms.refresh_maintenance_cache(conn)
        assert ms.get_cached_maintenance_sessions() == []

    def test_starting_a_session_keeps_the_schedule_windows(self, env):
        with _connection(env) as conn:
            ms.save_schedules(conn, [self._schedule(_utc(minutes=-5), target_app="restic")])
            ms.refresh_maintenance_cache(conn)
            ms.create_maintenance_session(conn, duration_minutes=10, target_host="nas")
            ids = {w["session_id"] for w in ms.get_cached_maintenance_sessions()}
            assert "schedule:s" in ids and len(ids) == 2
            ms.disable_maintenance_sessions(conn)
            assert {w["session_id"] for w in ms.get_cached_maintenance_sessions()} == {"schedule:s"}

    def test_schedules_saved_before_1_4_0_silence_every_alert(self, env):
        with _connection(env) as conn:
            conn.execute(
                "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('maintenance_schedules', ?, '', 0)",
                (json.dumps([{"id": "old", "name": "Old", "recurrence": "daily", "start_time": "02:00", "duration_minutes": 60}]),),
            )
            conn.commit()
            (sched,) = ms.load_schedules(conn)
        assert sched.log_handling == "silence_alerts"
        assert sched.target_app is None and sched.target_host is None


def _insert_rule(db_file: Path, name: str = "Disk errors") -> None:
    with open_db(db_file) as conn:
        conn.execute(
            "INSERT INTO alert_rules (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, created_at) "
            "VALUES (?, 'threshold', 'disk', 2, 60, 300, datetime('now'))",
            (name,),
        )
        conn.commit()


def _log(host: str, ip: str, app: str = "smartd") -> dict:
    return {"source_alias": host, "source_ip": ip, "app_name": app, "message": "disk error", "timestamp": _utc().isoformat()}


class TestEvaluatorWithTargetedMaintenance:
    """Logs from a target count towards no rule; a rule fed by several hosts fires on the others."""

    def test_targets_logs_do_not_count(self, env):
        _insert_rule(env)
        ms.update_cached_maintenance_sessions([_window(target_host="nas")])
        evaluator = AlertEvaluator(env)
        assert evaluator._match_batch([_log("nas", "10.0.0.5"), _log("nas", "10.0.0.5")]) == []

    def test_other_hosts_still_fire(self, env):
        _insert_rule(env)
        ms.update_cached_maintenance_sessions([_window(target_host="nas")])
        evaluator = AlertEvaluator(env)
        fired = evaluator._match_batch(
            [_log("nas", "10.0.0.5"), _log("router", "10.0.0.1"), _log("nas", "10.0.0.5"), _log("router", "10.0.0.1")]
        )
        ((rule, logs, _rate),) = fired
        assert [e["source_alias"] for e in logs] == ["router", "router"]

    def test_logs_count_again_once_the_window_ends(self, env):
        _insert_rule(env)
        ms.update_cached_maintenance_sessions([_window(target_host="nas", expires_at=_utc(seconds=-1).isoformat())])
        evaluator = AlertEvaluator(env)
        assert len(evaluator._match_batch([_log("nas", "10.0.0.5"), _log("nas", "10.0.0.5")])) == 1

    @pytest.mark.asyncio
    async def test_targeted_session_does_not_hold_back_other_notifications(self, env, monkeypatch):
        _insert_rule(env)
        with _connection(env) as conn:
            ms.create_maintenance_session(conn, duration_minutes=30, target_host="nas")
        sent = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent.append(title)
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)
        evaluator = AlertEvaluator(env)
        await evaluator.evaluate_batch([_log("router", "10.0.0.1"), _log("router", "10.0.0.1")])
        await asyncio.sleep(0.1)
        await evaluator.stop()
        assert len(sent) == 1

    @pytest.mark.asyncio
    async def test_untargeted_session_still_holds_back_every_notification(self, env, monkeypatch):
        _insert_rule(env)
        with _connection(env) as conn:
            ms.create_maintenance_session(conn, duration_minutes=30)
        sent = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent.append(title)
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)
        evaluator = AlertEvaluator(env)
        await evaluator.evaluate_batch([_log("router", "10.0.0.1"), _log("router", "10.0.0.1")])
        await asyncio.sleep(0.1)
        await evaluator.stop()
        assert sent == []


class TestWebUiStart:
    """POST /api/alerts/maintenance starts either mode, with targets."""

    @pytest.mark.asyncio
    async def test_drop_errors_with_targets_is_stored(self, client, headers, env):
        res = await client.post(
            "/api/alerts/maintenance",
            json={"until": _utc(minutes=30).isoformat(), "log_handling": "drop_errors", "target_app": " db ", "target_host": "nas"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        (session,) = body["sessions"]
        assert (session["log_handling"], session["target_app"], session["target_host"]) == ("drop_errors", "db", "nas")
        assert body["silences_all_alerts"] is False
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "db", 3) == session["session_id"]

    @pytest.mark.asyncio
    async def test_drop_errors_without_a_target_is_allowed_from_the_web_ui(self, client, headers):
        res = await client.post(
            "/api/alerts/maintenance",
            json={"until": _utc(minutes=30).isoformat(), "log_handling": "drop_errors", "target_app": "  "},
            headers=headers,
        )
        assert res.status_code == 200
        (session,) = res.json()["sessions"]
        assert session["target_app"] is None and session["target_host"] is None
        assert res.json()["silences_all_alerts"] is True
        assert ms.should_drop_maintenance_error("any", "10.0.0.9", "anything", 3) == session["session_id"]

    @pytest.mark.asyncio
    async def test_a_start_without_the_new_fields_silences_everything_as_before(self, client, headers):
        res = await client.post("/api/alerts/maintenance", json={"until": _utc(minutes=30).isoformat()}, headers=headers)
        (session,) = res.json()["sessions"]
        assert session["log_handling"] == "silence_alerts"
        assert res.json()["silences_all_alerts"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "extra", [{"log_handling": "drop_everything"}, {"target_app": "a" * 256}, {"target_host": "h" * 256}]
    )
    async def test_bad_fields_are_refused(self, client, headers, env, extra):
        res = await client.post(
            "/api/alerts/maintenance", json={"until": _utc(minutes=30).isoformat(), **extra}, headers=headers
        )
        assert res.status_code == 422
        with open_db(env) as conn:
            assert conn.execute("SELECT COUNT(*) FROM maintenance_sessions").fetchone()[0] == 0


class TestScheduleSave:
    """POST /api/alerts/maintenance/schedules stores the mode and targets and applies them at once."""

    def _payload(self, **fields) -> dict:
        start = _utc(minutes=-5).strftime("%H:%M")
        sched = {"id": "s", "name": "Backup", "recurrence": "daily", "start_time": start, "duration_minutes": 30}
        sched.update(fields)
        return {"schedules": [sched]}

    @pytest.mark.asyncio
    async def test_mode_and_targets_are_stored_and_applied(self, client, headers):
        res = await client.post(
            "/api/alerts/maintenance/schedules",
            json=self._payload(log_handling="drop_errors", target_app="restic", target_host=""),
            headers=headers,
        )
        assert res.status_code == 200, res.text
        (sched,) = res.json()["schedules"]
        assert (sched["log_handling"], sched["target_app"], sched["target_host"]) == ("drop_errors", "restic", None)
        assert ms.should_drop_maintenance_error("nas", "10.0.0.5", "restic", 3) == "schedule:s"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [{"log_handling": "nope"}, {"target_host": "h" * 256}])
    async def test_bad_fields_are_refused(self, client, headers, extra):
        res = await client.post("/api/alerts/maintenance/schedules", json=self._payload(**extra), headers=headers)
        assert res.status_code == 422


class TestScheduleWindowTiming:
    """Back-to-back windows leave no gap, and a window lasts its duration in real time across a clock change."""

    def test_back_to_back_daily_window_caches_the_next_one_too(self, env):
        start = _utc(minutes=1).replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        with _connection(env) as conn:
            ms.save_schedules(conn, [MaintenanceSchedule(
                id="d", name="All day", recurrence="daily", start_time=start.strftime("%H:%M"),
                duration_minutes=1440, log_handling="drop_errors",
            )])
            ms.refresh_maintenance_cache(conn)
        windows = ms.get_cached_maintenance_sessions()
        assert [w["starts_at"] is None for w in windows] == [True, False]
        assert windows[1]["starts_at"] == start.isoformat()

    def test_window_across_the_spring_clock_change_ends_in_real_time(self, env, monkeypatch):
        from zoneinfo import ZoneInfo

        london = ZoneInfo("Europe/London")
        # 2026-03-29: clocks go from 01:00 GMT to 02:00 BST
        now_local = datetime.datetime(2026, 3, 29, 0, 29, tzinfo=london)
        monkeypatch.setattr(ms, "_get_server_time_info", lambda: (now_local, "GMT"))
        with _connection(env) as conn:
            ms.save_schedules(conn, [MaintenanceSchedule(
                id="n", name="Night", recurrence="daily", start_time="00:30", duration_minutes=120,
            )])
            ms.refresh_maintenance_cache(conn)
        (window,) = ms.get_cached_maintenance_sessions()
        assert window["starts_at"] == "2026-03-29T00:30:00+00:00"
        assert window["expires_at"] == "2026-03-29T02:30:00+00:00"

    def test_extending_a_session_keeps_the_schedule_windows(self, env):
        with _connection(env) as conn:
            ms.save_schedules(conn, [MaintenanceSchedule(
                id="s", name="Backup", recurrence="daily", start_time=_utc(minutes=-5).strftime("%H:%M"),
                duration_minutes=30, target_app="restic",
            )])
            created = ms.create_maintenance_session(conn, duration_minutes=10, target_host="nas")
            ms.extend_maintenance_session(conn, created["session_id"], 30)
        assert "schedule:s" in {w["session_id"] for w in ms.get_cached_maintenance_sessions()}
