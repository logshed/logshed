"""
Tests for DailyDigest analytical rollup, notification dispatch,
historical storage in alert_history, DailyDigestWorker background scheduler, and API endpoints.
"""

import asyncio
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.config import get_db_path
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services.daily_digest import (
    DailyDigestWorker,
    compute_daily_digest_rollup,
    format_digest_body,
    run_daily_digest,
)
from app.services.drop_filter import init_drop_filter
from app.services.notifier import encrypt_channel_url


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


def _create_channel(db_file: Path, name: str, url: str = "gotify://example.com/token", is_enabled: bool = True) -> int:
    enc_url = encrypt_channel_url(url)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection(db_file) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO notification_channels (name, url, is_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, enc_url, 1 if is_enabled else 0, now_iso, now_iso),
        )
        return cur.lastrowid


def _set_setting(db_file: Path, key: str, value: str):
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with get_connection(db_file) as conn:
        conn.execute(
            "INSERT INTO system_settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now_iso),
        )


def _seed_logs(db_file: Path):
    """Seed logs across last 24h and older than 24h."""
    now = datetime.datetime.now(datetime.timezone.utc)
    recent_ts = now - datetime.timedelta(hours=2)
    recent_str = recent_ts.strftime("%Y-%m-%d %H:%M:%S")

    old_ts = now - datetime.timedelta(hours=30)
    old_str = old_ts.strftime("%Y-%m-%d %H:%M:%S")

    with get_connection(db_file) as conn:
        # Logs within 24h:
        # Service 1: nginx (5 logs, 2 errors from host-a)
        for i in range(3):
            conn.execute(
                "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 6, 1, '10.0.0.1', 'host-a', 'nginx', ?, ?)",
                (recent_str, recent_str, f"nginx info log {i}", f"nginx info log {i}"),
            )
        for i in range(2):
            conn.execute(
                "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 3, 1, '10.0.0.1', 'host-a', 'nginx', ?, ?)",
                (recent_str, recent_str, f"nginx error log {i}", f"nginx error log {i}"),
            )

        # Service 2: backend (3 logs, all errors from docker container web-app)
        for i in range(3):
            conn.execute(
                "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 2, 1, '127.0.0.1', 'docker', 'web-app', ?, ?)",
                (recent_str, recent_str, f"web-app critical error {i}", f"web-app critical error {i}"),
            )

        # Service 3: postgres (2 logs, 1 error from host-db)
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 6, 1, '10.0.0.2', 'host-db', 'postgres', 'query ok', 'query ok')",
            (recent_str, recent_str),
        )
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 3, 1, '10.0.0.2', 'host-db', 'postgres', 'syntax error in query', 'syntax error in query')",
            (recent_str, recent_str),
        )

        # Service 4: redis (1 log, info)
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 6, 1, '10.0.0.3', 'host-cache', 'redis', 'connected', 'connected')",
            (recent_str, recent_str),
        )

        # Old log (>24h ago, must not be counted in 24h rollup)
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 1, 1, '10.0.0.9', 'old-host', 'old-service', 'ancient error', 'ancient error')",
            (old_str, old_str),
        )


def test_compute_daily_digest_rollup_empty(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    with get_connection(db_file) as conn:
        rollup = compute_daily_digest_rollup(conn, window_hours=24, db_path=db_file)

    assert rollup["total_logs"] == 0
    assert rollup["total_errors"] == 0
    assert rollup["top_errors"] == []
    assert rollup["top_services"] == []
    assert rollup["sample_lines"] == []
    assert isinstance(rollup["storage_delta_str"], str)


def test_compute_daily_digest_rollup_with_data(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    _seed_logs(db_file)

    with get_connection(db_file) as conn:
        rollup = compute_daily_digest_rollup(conn, window_hours=24, db_path=db_file)

    # Total recent logs = 5 (nginx) + 3 (web-app) + 2 (postgres) + 1 (redis) = 11 (old log excluded)
    assert rollup["total_logs"] == 11

    # Total errors (severity <= 3): 2 (host-a) + 3 (web-app) + 1 (host-db) = 6
    assert rollup["total_errors"] == 6

    # Top errors: top 3 entities
    # web-app (docker): 3 errors
    # host-a: 2 errors
    # host-db: 1 error
    top_entities = [e["entity"] for e in rollup["top_errors"]]
    assert top_entities == ["web-app", "host-a", "host-db"]
    assert rollup["top_errors"][0]["error_count"] == 3
    assert rollup["top_errors"][0]["crit_count"] == 3
    assert rollup["top_errors"][0]["err_count"] == 0
    assert rollup["top_errors"][1]["error_count"] == 2
    assert rollup["top_errors"][1]["err_count"] == 2
    assert rollup["top_errors"][2]["error_count"] == 1
    assert rollup["top_errors"][2]["err_count"] == 1

    # Top noisy services (app_name):
    # nginx: 5
    # web-app: 3
    # postgres: 2
    top_services = [s["service"] for s in rollup["top_services"]]
    assert top_services == ["nginx", "web-app", "postgres"]
    assert rollup["top_services"][0]["log_count"] == 5
    assert rollup["top_services"][1]["log_count"] == 3
    assert rollup["top_services"][2]["log_count"] == 2


def test_compute_daily_digest_rollup_docker_custom_alias_shows_app(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    now = datetime.datetime.now(datetime.timezone.utc)
    recent_str = (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection(db_file) as conn:
        # Ingest error from docker container with custom source_alias (e.g. docker-unraid) and source_ip='docker'
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, severity, facility, source_ip, source_alias, app_name, message, raw) VALUES (?, ?, 3, 1, 'docker', 'docker-unraid', 'seer', 'seer error occurred', 'seer error occurred')",
            (recent_str, recent_str),
        )
        rollup = compute_daily_digest_rollup(conn, window_hours=24, db_path=db_file)

    assert rollup["top_errors"][0]["entity"] == "seer"
    assert rollup["top_errors"][0]["error_count"] == 1



def test_format_digest_body_no_em_dashes():
    top_errors = [{
        "entity": "api-service",
        "error_count": 42,
        "emerg_count": 2,
        "alert_count": 0,
        "crit_count": 10,
        "err_count": 30,
    }]
    top_services = [{"service": "frontend", "log_count": 1000}]

    body = format_digest_body(
        total_logs=5000,
        storage_delta_str="+1.2 MB",
        top_errors=top_errors,
        top_services=top_services,
        app_url="http://localhost:8000",
        since_iso="2026-09-30T08:18:32.480Z",
    )

    # Typography check: absolutely no em dashes allowed
    assert "\u2014" not in body
    assert "24-Hour Analytical Rollup" in body
    assert "5,000" in body
    assert "api-service" in body
    # 42 events with breakdown (2 emergs, 10 crits, 30 errors)
    assert "42 events (2 emergs, 10 crits, 30 errors)" in body
    assert "Top Apps / Hosts with Errors or Above" in body
    assert "Top Logging Services" in body
    assert "Executive Summary" not in body
    assert "[Link to LogShed (digest filters applied)](http://localhost:8000/?time=2026-09-30T08%3A18%3A32.480Z&severity=3)" in body


@pytest.mark.asyncio
async def test_run_daily_digest_dispatches_and_records_history(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    _seed_logs(db_file)

    channel_id = _create_channel(db_file, "Admin Webhook", is_enabled=True)
    _set_setting(db_file, "daily_digest_enabled", "1")
    _set_setting(db_file, "daily_digest_channel_id", str(channel_id))

    mock_notifier = MagicMock()
    mock_notifier.send_notification = AsyncMock(return_value=True)

    with patch("app.services.daily_digest.get_notifier", return_value=mock_notifier):
        res = await run_daily_digest(db_file, force=False)

    assert res["status"] == "ok"
    assert res["total_logs"] == 11
    assert res["error_count"] == 6
    assert res["channel_id"] == channel_id
    assert res["notification_sent"] is True

    # Verify alert_history table contains the digest
    with get_connection(db_file) as conn:
        row = conn.execute(
            "SELECT rule_name, trigger_count, channel_id FROM alert_history WHERE id = ?",
            (res["history_id"],),
        ).fetchone()
        assert row is not None
        assert row[0] == "Daily Digest"
        assert row[1] == 11
        assert row[2] == channel_id

        # Verify daily_digest_last_run was updated in system_settings
        last_run = conn.execute("SELECT value FROM system_settings WHERE key = 'daily_digest_last_run'").fetchone()
        assert last_run is not None
        assert last_run[0] == res["triggered_at"]


@pytest.mark.asyncio
async def test_run_daily_digest_skipped_when_disabled(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    _create_channel(db_file, "Active Channel", is_enabled=True)
    _set_setting(db_file, "daily_digest_enabled", "0")

    res = await run_daily_digest(db_file, force=False)
    assert res["status"] == "skipped"
    assert res["reason"] == "daily_digest_disabled"


@pytest.mark.asyncio
async def test_daily_digest_worker_trigger(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    _create_channel(db_file, "Active Channel", is_enabled=True)
    _set_setting(db_file, "daily_digest_enabled", "1")

    worker = DailyDigestWorker(db_file)

    run_mock = AsyncMock(return_value={"status": "ok"})
    with patch("app.services.daily_digest.run_daily_digest", side_effect=run_mock):
        # Trigger worker immediately
        worker.trigger()

        # Start worker and run for one cycle
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await task

    assert run_mock.called


@pytest.mark.asyncio
async def test_daily_digest_api_endpoints(tmp_path: Path, auth_headers: dict):
    db_file = tmp_path / "logs.db"
    channel_id = _create_channel(db_file, "Digest Slack", is_enabled=True)

    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # 1. Update daily digest settings via POST /api/settings
        update_res = await client.post(
            "/api/settings",
            json={
                "daily_digest_enabled": True,
                "daily_digest_channel_id": channel_id,
                "daily_digest_schedule_time": "14:30",
            },
            headers=auth_headers,
        )
        assert update_res.status_code == 200

        # Invalid schedule time format
        invalid_res = await client.post(
            "/api/settings",
            json={"daily_digest_schedule_time": "invalid-time"},
            headers=auth_headers,
        )
        assert invalid_res.status_code == 422

        # 2. Verify settings in GET /api/settings
        get_res = await client.get("/api/settings", headers=auth_headers)
        assert get_res.status_code == 200
        settings_data = get_res.json()
        assert settings_data["daily_digest_enabled"] is True
        assert settings_data["daily_digest_channel_id"] == channel_id
        assert settings_data["daily_digest_schedule_time"] == "14:30"

        # 3. Trigger manual digest via POST /api/notifications/digest/send
        mock_notifier = MagicMock()
        mock_notifier.send_notification = AsyncMock(return_value=True)

        with patch("app.services.daily_digest.get_notifier", return_value=mock_notifier):
            send_res = await client.post("/api/notifications/digest/send", headers=auth_headers)

        assert send_res.status_code == 200
        digest_data = send_res.json()
        assert digest_data["status"] == "ok"
        assert digest_data["total_logs"] == 0
        assert digest_data["channel_id"] == channel_id
        assert digest_data["notification_sent"] is True
        assert "triggered_at" in digest_data


@pytest.mark.asyncio
async def test_daily_digest_worker_scheduled_trigger(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    _create_channel(db_file, "Active Channel", is_enabled=True)
    _set_setting(db_file, "daily_digest_enabled", "1")
    _set_setting(db_file, "daily_digest_schedule_time", "08:00")
    # Last run yesterday
    yesterday_iso = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
    _set_setting(db_file, "daily_digest_last_run", yesterday_iso)

    worker = DailyDigestWorker(db_file)
    run_mock = AsyncMock(return_value={"status": "ok"})

    # Mock time so that local time is 08:30 (after 08:00)
    fake_now = datetime.datetime.now().astimezone().replace(hour=8, minute=30, second=0)

    with patch("datetime.datetime") as mock_dt, patch("app.services.daily_digest.run_daily_digest", side_effect=run_mock):
        mock_dt.now.return_value = fake_now
        mock_dt.fromisoformat = datetime.datetime.fromisoformat
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)

        # Worker check
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await task

    assert run_mock.called
