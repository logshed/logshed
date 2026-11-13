"""
The upgrade path at start-up (status page, 503s, health) and recovery mode after a failed upgrade:
the marker, the notification, the recovery routes, Retry and Migrate without a backup.

The lifespan runs for real; the services it starts afterwards are replaced by mocks, so a test sees
whether they started without running the workers.
"""

import asyncio
import collections
import json
import sqlite3
import threading
from contextlib import asynccontextmanager, closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.core.migrations as migrations_mod
import app.main as main_mod
from app.api import backups as backups_api
from app.core import backup as db_backup
from app.core import lifecycle
from app.core import pipeline as pipeline_mod
from app.core.migrations import MIGRATIONS, get_user_version, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.services.notifier import NotifierService
from app.version import APP_VERSION

DiskUsage = collections.namedtuple("DiskUsage", "total used free")
GiB = 1024**3
LATEST = MIGRATIONS[-1][0]
NEXT = LATEST + 1
CSRF = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def env(tmp_path: Path, monkeypatch, free_syslog_port):
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    login_rate_limiter.reset()
    lifecycle.reset()
    backups_api.reset_backup_job()
    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.delenv("AUTO_BACKUP_ON_MIGRATION", raising=False)
    monkeypatch.setenv("ENABLE_DOCKER", "false")
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(100 * GiB, 10 * GiB, 90 * GiB))
    run_migrations(db_file)
    get_or_create_master_key(key_file)
    started, stopped = AsyncMock(), AsyncMock()
    monkeypatch.setattr(main_mod, "_start_services", started)
    monkeypatch.setattr(main_mod, "_stop_services", stopped)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(NotifierService, "send_notification", send)
    yield SimpleNamespace(db=db_file, started=started, stopped=stopped, send=send, folder=db_backup.backups_dir(db_file))
    lifecycle.reset()
    pipeline_mod._log_queue = None
    reset_crypto_cache()


def _with_next_step(monkeypatch, step) -> None:
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", list(MIGRATIONS) + [(NEXT, step)])


def _create_next(conn) -> None:
    conn.execute("CREATE TABLE next_marker (id INTEGER PRIMARY KEY)")


def _failing_next(conn) -> None:
    conn.execute("CREATE TABLE next_marker (id INTEGER PRIMARY KEY)")
    raise RuntimeError("next step failed on purpose")


def _schema(db: Path) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return get_user_version(conn)


def _has_table(db: Path, name: str) -> bool:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def _archives(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.tar.gz")) if folder.exists() else []


async def _wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@asynccontextmanager
async def _running():
    app = main_mod.create_app()
    async with main_mod.lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _signed_in(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))


async def test_new_install_and_current_database_take_the_fast_path(env):
    async with _running() as client:
        assert lifecycle.is_ready()
        env.started.assert_awaited_once()
        assert (await client.get("/api/health")).json()["status"] == "ok"
    assert _archives(env.folder) == []


async def test_upgrade_serves_the_status_page_and_503_until_ready(env, monkeypatch):
    gate = threading.Event()

    def slow_next(conn):
        assert gate.wait(5)
        _create_next(conn)

    _with_next_step(monkeypatch, slow_next)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.snapshot().get("stage") == "migrate")

        page = await client.get("/storage", headers={"Accept": "text/html"})
        assert page.status_code == 503
        assert "LogShed is upgrading its database" in page.text
        assert f"from schema {LATEST} to {NEXT}" in page.text and "Do not restart" in page.text
        assert '<meta http-equiv="refresh" content="3">' in page.text
        assert "<script" not in page.text
        assert page.headers["Retry-After"] == "5" and page.headers["Cache-Control"] == "no-store"

        api = await client.get("/api/logs")
        assert api.status_code == 503 and api.headers["Retry-After"] == "5"
        assert "upgrading" in api.json()["detail"]
        assert (await client.post("/api/v1/mcp", json={})).status_code == 503

        health = await client.get("/api/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "upgrading"
        assert body["upgrade"]["stage"] == "migrate"
        assert (body["upgrade"]["from_schema"], body["upgrade"]["target_schema"]) == (LATEST, NEXT)
        env.started.assert_not_called()

        gate.set()
        await _wait_for(lifecycle.is_ready)
        env.started.assert_awaited_once()
        assert (await client.get("/api/health")).json()["status"] == "ok"
    assert _schema(env.db) == NEXT
    assert [n.startswith(f"logshed-backup-pre-v{LATEST}-to-v{NEXT}-") for n in _archives(env.folder)] == [True]


async def test_status_page_shows_backup_progress():
    page = main_mod.upgrade_status_page(
        {"status": "upgrading", "stage": "backup", "done": 512 * 1024**2, "total": 2 * GiB, "from_schema": 2, "target_schema": 3}
    )
    assert "Backing up the database: 512.0 MiB of 2.0 GiB (25%)" in page


async def test_failed_step_enters_recovery_with_marker_and_one_notification(env, monkeypatch):
    _with_next_step(monkeypatch, _failing_next)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        marker = json.loads(lifecycle.marker_path(env.db).read_text())
        archives = _archives(env.folder)
        assert len(archives) == 1
        assert marker["app_version"] == APP_VERSION
        assert (marker["from_schema"], marker["target_schema"], marker["current_schema"]) == (LATEST, NEXT, LATEST)
        assert marker["stage"] == "migration" and marker["backup_filename"] == archives[0]
        assert "next step failed on purpose" in marker["error"]
        assert marker["failed_at"]
        # The step's transaction rolled back
        assert _schema(env.db) == LATEST and not _has_table(env.db, "next_marker")
        env.started.assert_not_called()

        env.send.assert_awaited_once()
        title, body = env.send.await_args.args[:2]
        assert title == "LogShed upgrade stopped"
        assert f"from schema {LATEST} to {NEXT}" in body and f"unchanged at schema {LATEST}" in body

        # Anyone sees the state, never the error
        health = (await client.get("/api/health")).json()
        assert health["status"] == "migration_failed" and health["upgrade"]["stage"] == "migration"
        assert "failed on purpose" not in json.dumps(health)
        assert (await client.get("/api/logs")).status_code == 503
        assert (await client.get("/api/auth/status")).status_code == 200
        assert (await client.get("/storage")).status_code != 503
        assert (await client.get("/api/system/recovery")).status_code == 401

        _signed_in(client)
        details = (await client.get("/api/system/recovery")).json()
        assert details["status"] == "recovery" and "failed on purpose" in details["error"]
        assert details["backup_available"] is True and details["backup_filename"] == archives[0]
        assert details["disk_free_bytes"] == 90 * GiB and details["database_size_bytes"] > 0
        download = await client.get(f"/api/system/backups/{archives[0]}/download")
        assert download.status_code == 200 and download.headers["content-type"] == "application/gzip"
        # Other routes stay closed even when signed in
        assert (await client.get("/api/system/backups")).status_code == 503
        assert (await client.get("/api/settings")).status_code == 503

    # The next start goes straight to recovery: no second backup, no second notification
    env.send.reset_mock()
    async with _running():
        assert lifecycle.status() == lifecycle.RECOVERY
        await asyncio.sleep(0.05)
        assert _archives(env.folder) == archives
        env.send.assert_not_called()
        env.started.assert_not_called()
    assert lifecycle.marker_path(env.db).exists()


async def test_recovery_actions_need_a_session_and_the_csrf_header(env, monkeypatch):
    _with_next_step(monkeypatch, _failing_next)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        for path in ("/api/system/recovery/retry", "/api/system/recovery/skip-backup"):
            assert (await client.post(path, headers=CSRF)).status_code == 401
        _signed_in(client)
        for path in ("/api/system/recovery/retry", "/api/system/recovery/skip-backup"):
            assert (await client.post(path)).status_code == 403
        assert lifecycle.status() == lifecycle.RECOVERY


async def test_retry_reuses_the_backup_of_the_failed_run(env, monkeypatch):
    attempts = []

    def flaky_next(conn):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first try fails")
        _create_next(conn)

    _with_next_step(monkeypatch, flaky_next)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        archives = _archives(env.folder)
        _signed_in(client)
        response = await client.post("/api/system/recovery/retry", headers=CSRF)
        assert response.status_code == 200 and response.json()["status"] == "upgrading"
        await _wait_for(lifecycle.is_ready)
        env.started.assert_awaited_once()
        # A click once the upgrade has finished is refused
        assert (await client.post("/api/system/recovery/retry", headers=CSRF)).status_code == 409
    assert _archives(env.folder) == archives
    assert _schema(env.db) == NEXT
    assert not lifecycle.marker_path(env.db).exists()


async def test_retry_backs_up_again_when_the_failed_run_moved_the_schema(env, tmp_path, monkeypatch):
    # A database at v2: the run reaches the latest schema, then the next step fails, so the v2 backup no longer matches
    env.db.unlink()
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", MIGRATIONS[:2])
    run_migrations(env.db)
    attempts = []

    def flaky_next(conn):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first try fails")
        _create_next(conn)

    _with_next_step(monkeypatch, flaky_next)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        marker = lifecycle.recovery_marker()
        assert (marker["from_schema"], marker["current_schema"]) == (2, LATEST)
        _signed_in(client)
        assert (await client.post("/api/system/recovery/retry", headers=CSRF)).status_code == 200
        await _wait_for(lifecycle.is_ready)
    title, body = env.send.await_args.args[:2]
    assert f"now at schema {LATEST}" in body and "restore the backup" in body and "unchanged" not in body
    names = _archives(env.folder)
    assert len(names) == 2
    assert any(n.startswith(f"logshed-backup-pre-v2-to-v{NEXT}-") for n in names)
    assert any(n.startswith(f"logshed-backup-pre-v{LATEST}-to-v{NEXT}-") for n in names)
    assert _schema(env.db) == NEXT


async def test_no_room_for_the_backup_stops_in_recovery_and_skip_backup_upgrades(env, monkeypatch, caplog):
    _with_next_step(monkeypatch, _create_next)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(50 * GiB, 50 * GiB, 0))
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        marker = lifecycle.recovery_marker()
        assert marker["stage"] == "backup" and marker["backup_filename"] is None
        assert "Not enough free space" in marker["error"]
        assert _schema(env.db) == LATEST and _archives(env.folder) == []
        env.send.assert_awaited_once()
        _signed_in(client)
        details = (await client.get("/api/system/recovery")).json()
        assert details["backup_available"] is False and details["disk_free_bytes"] == 0
        assert details["backup_needed_bytes"] > 0 and details["backup_floor_bytes"] == 5 * GiB

        assert (await client.post("/api/system/recovery/skip-backup", headers=CSRF)).status_code == 200
        await _wait_for(lifecycle.is_ready)
        env.started.assert_awaited_once()
    assert _schema(env.db) == NEXT and _archives(env.folder) == []
    assert not lifecycle.marker_path(env.db).exists()


def _write_marker(db: Path, **fields) -> None:
    marker = {"app_version": APP_VERSION, "from_schema": LATEST, "target_schema": NEXT, "current_schema": LATEST,
              "failed_at": "2026-11-13T00:00:00+00:00", "stage": "migration", "backup_filename": None, "error": "old"}
    marker.update(fields)
    lifecycle.marker_path(db).write_text(json.dumps(marker))


async def test_a_marker_from_another_version_is_removed_and_the_upgrade_runs(env, monkeypatch):
    _with_next_step(monkeypatch, _create_next)
    _write_marker(env.db, app_version="1.3.9")
    async with _running():
        await _wait_for(lifecycle.is_ready)
    assert _schema(env.db) == NEXT
    assert not lifecycle.marker_path(env.db).exists()
    env.send.assert_not_called()


async def test_a_marker_for_a_schema_already_reached_is_removed(env):
    _write_marker(env.db, from_schema=2, target_schema=3)
    async with _running():
        assert lifecycle.is_ready()
        env.started.assert_awaited_once()
    assert not lifecycle.marker_path(env.db).exists()


async def test_stopping_during_the_backup_leaves_no_partial_file_and_no_marker(env, monkeypatch):
    with closing(sqlite3.connect(env.db)) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
            "VALUES ('2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00', '10.0.0.1', 'nas', 'app', 1, 6, ?, ?)",
            [(f"message {i} " * 20, "raw") for i in range(2000)],
        )
        conn.commit()
    _with_next_step(monkeypatch, _create_next)
    monkeypatch.setattr(db_backup, "CHUNK_BYTES", 4096)
    reached = threading.Event()

    def blocking_progress(stage, done, total):
        if stage == "backup":
            reached.set()
            lifecycle.cancel_event().wait(5)

    monkeypatch.setattr(lifecycle, "report_progress", blocking_progress)
    async with _running():
        await _wait_for(reached.is_set)
    assert list(env.folder.glob("*")) == []
    assert not lifecycle.marker_path(env.db).exists()
    assert _schema(env.db) == LATEST
    env.started.assert_not_called()
    env.send.assert_not_called()
    assert lifecycle.is_ready() and not lifecycle.cancel_event().is_set()


# --- The setting ---

def test_stored_setting_off_upgrades_without_a_backup(env, monkeypatch):
    with closing(sqlite3.connect(env.db)) as conn:
        conn.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('backup_before_migration', '0', datetime('now'), 0)")
        conn.commit()
    _with_next_step(monkeypatch, _create_next)
    run_migrations(env.db)
    assert _schema(env.db) == NEXT and _archives(env.folder) == []


@pytest.mark.parametrize("env_value, stored, expected", [("true", "0", 1), ("false", "1", 0), ("", "0", 0), ("", None, 1)])
def test_the_variable_wins_over_the_stored_setting(env, monkeypatch, env_value, stored, expected):
    monkeypatch.setenv("AUTO_BACKUP_ON_MIGRATION", env_value)
    if stored is not None:
        with closing(sqlite3.connect(env.db)) as conn:
            conn.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('backup_before_migration', ?, datetime('now'), 0)", (stored,))
            conn.commit()
    _with_next_step(monkeypatch, _create_next)
    run_migrations(env.db)
    assert len(_archives(env.folder)) == expected


async def test_settings_show_and_store_the_backup_setting(env, monkeypatch):
    async with _running() as client:
        _signed_in(client)
        body = (await client.get("/api/settings")).json()
        assert body["backup_before_migration"] is True and body["backup_before_migration_overridden"] is False
        assert (await client.post("/api/settings", json={"backup_before_migration": False}, headers=CSRF)).status_code == 200
        assert (await client.get("/api/settings")).json()["backup_before_migration"] is False

        monkeypatch.setenv("AUTO_BACKUP_ON_MIGRATION", "true")
        body = (await client.get("/api/settings")).json()
        assert body["backup_before_migration"] is True and body["backup_before_migration_overridden"] is True


def test_the_log_says_why_no_backup_was_made(env, monkeypatch, caplog):
    import logging

    _with_next_step(monkeypatch, _create_next)
    with caplog.at_level(logging.INFO, logger="app.core.migrations"):
        run_migrations(env.db, backup=False)
    assert "without a new backup, as asked" in caplog.text
    assert "turned off" not in caplog.text


async def test_a_failed_service_start_after_the_upgrade_stops_logshed(env, monkeypatch):
    _with_next_step(monkeypatch, _create_next)
    env.started.side_effect = RuntimeError("could not create the presets folder")
    kills = []
    monkeypatch.setattr(main_mod.os, "kill", lambda pid, sig: kills.append(sig))
    async with _running():
        await _wait_for(lambda: bool(kills))
        assert not lifecycle.is_ready()
    assert kills == [main_mod.signal.SIGTERM]


async def test_recovery_works_when_the_marker_cannot_be_written(env, monkeypatch):
    _with_next_step(monkeypatch, _failing_next)

    def unwritable(*args):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(lifecycle, "write_marker", unwritable)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        env.send.assert_awaited_once()
        _signed_in(client)
        assert "failed on purpose" in (await client.get("/api/system/recovery")).json()["error"]
    assert not lifecycle.marker_path(env.db).exists()


async def test_recovery_lets_through_only_what_the_screen_needs(env, monkeypatch):
    _with_next_step(monkeypatch, _failing_next)
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        _signed_in(client)
        closed = [
            ("GET", "/api/logs"), ("HEAD", "/api/logs"), ("GET", "/api/logs/stream"), ("POST", "/mcp"),
            ("POST", "/api/v1/mcp"), ("GET", "/api/v1/logs"), ("GET", "/api/system/recovery/"),
            ("POST", "/api/system/backups"), ("GET", "/api/system/storage"), ("POST", "/api/auth/setup"),
        ]
        for method, path in closed:
            response = await client.request(method, path, headers=CSRF)
            assert response.status_code == 503, (method, path, response.status_code)
        for method, path in [("GET", "/api/health"), ("GET", "/api/auth/status"), ("GET", "/api/system/recovery")]:
            assert (await client.request(method, path)).status_code == 200, path


async def test_the_space_figure_counts_what_the_pending_steps_need(env, monkeypatch):
    _with_next_step(monkeypatch, _failing_next)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(50 * GiB, 50 * GiB, 0))
    monkeypatch.setattr(
        migrations_mod, "MIGRATION_NEEDS", {NEXT: migrations_mod.MigrationNeeds(extra_space=lambda size: 7 * GiB)}
    )
    async with _running() as client:
        await _wait_for(lambda: lifecycle.status() == lifecycle.RECOVERY)
        _signed_in(client)
        details = (await client.get("/api/system/recovery")).json()
    plain = db_backup.backup_space_needed(env.db, db_backup.METHOD_LOCKED_STREAM)
    assert details["backup_needed_bytes"] == plain + 7 * GiB
