"""The web UI's backups: list, Create backup now (a job), download and delete, and their path checks."""

import asyncio
import collections
import os
import sqlite3
import tarfile
import threading
from contextlib import closing
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import backups as backups_api
from app.core import backup as db_backup
from app.core import lifecycle
from app.core import pipeline as pipeline_mod
from app.core.migrations import run_migrations
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services.operation_guard import operation_guard

DiskUsage = collections.namedtuple("DiskUsage", "total used free")
GiB = 1024**3
CSRF = {"X-Requested-With": "XMLHttpRequest"}
MANUAL = "logshed-backup-manual-v3-20261101T120000Z.tar.gz"
PRE = "logshed-backup-pre-v2-to-v3-20261020T080000Z.tar.gz"


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    lifecycle.reset()
    backups_api.reset_backup_job()
    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(100 * GiB, 10 * GiB, 90 * GiB))
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    yield db_file
    backups_api.reset_backup_job()
    reset_crypto_cache()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test", headers=CSRF) as c:
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
        yield c


def _folder(db: Path) -> Path:
    folder = db_backup.backups_dir(db)
    folder.mkdir(exist_ok=True)
    return folder


def _fake_archive(db: Path, name: str, size: int = 1000) -> Path:
    path = _folder(db) / name
    path.write_bytes(b"x" * size)
    return path


async def _finished_job(client: AsyncClient, timeout: float = 10.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        job = (await client.get("/api/system/backups/current")).json()
        if job["state"] != "running":
            return job
        assert asyncio.get_running_loop().time() < deadline, "backup job did not finish"
        await asyncio.sleep(0.02)


async def test_every_route_needs_a_session():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test", headers=CSRF) as c:
        assert (await c.get("/api/system/backups")).status_code == 401
        assert (await c.get("/api/system/backups/current")).status_code == 401
        assert (await c.post("/api/system/backups")).status_code == 401
        assert (await c.get(f"/api/system/backups/{MANUAL}/download")).status_code == 401
        assert (await c.delete(f"/api/system/backups/{MANUAL}")).status_code == 401


async def test_list_is_newest_first_from_the_names(env, client):
    _fake_archive(env, PRE, 2000)
    _fake_archive(env, MANUAL, 1000)
    _fake_archive(env, "notes.txt")
    _fake_archive(env, MANUAL + ".tmp")
    body = (await client.get("/api/system/backups")).json()
    assert [b["filename"] for b in body["backups"]] == [MANUAL, PRE]
    manual, pre = body["backups"]
    assert (manual["kind"], manual["schema_version"], manual["target_schema"]) == ("manual", 3, None)
    assert (pre["kind"], pre["schema_version"], pre["target_schema"]) == ("pre-migration", 2, 3)
    assert manual["created_at"].startswith("2026-11-01T12:00:00")
    assert manual["expires_at"].startswith("2026-12-01T12:00:00")
    assert body["total_size_bytes"] == 3000


async def test_create_backup_now_makes_an_archive_while_logs_are_written(env, client):
    stop = threading.Event()
    written = []

    def writer():
        with closing(sqlite3.connect(env, timeout=5)) as conn:
            while not stop.is_set():
                conn.execute(
                    "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
                    "VALUES ('2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00', '10.0.0.1', 'nas', 'app', 1, 6, 'm', 'r')"
                )
                conn.commit()
                written.append(1)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        response = await client.post("/api/system/backups")
        assert response.status_code == 202
        assert response.json()["state"] == "running" and response.json()["stage"] == "copying"
        job = await _finished_job(client)
    finally:
        stop.set()
        thread.join()
    assert job["state"] == "done", job
    assert job["filename"].startswith("logshed-backup-manual-v3-") and job["size_bytes"] > 0
    assert written
    archive = db_backup.backups_dir(env) / job["filename"]
    with tarfile.open(archive, "r:gz") as tar:
        assert tar.getnames() == ["logs.db", "manifest.json"]
    listed = (await client.get("/api/system/backups")).json()["backups"]
    assert [b["filename"] for b in listed] == [job["filename"]]
    assert listed[0]["days_until_expiry"] == 30


async def test_create_backup_now_is_refused_during_a_prune(client):
    with operation_guard("prune"):
        response = await client.post("/api/system/backups")
    assert response.status_code == 409
    assert "prune or compaction" in response.json()["detail"]
    assert (await client.get("/api/system/backups/current")).json()["state"] == "idle"


async def test_prune_and_compact_are_refused_during_a_backup(client):
    with operation_guard("backup"):
        prune = await client.post("/api/maintenance/prune")
        vacuum = await client.post("/api/system/vacuum")
        second = await client.post("/api/system/backups")
    for response in (prune, vacuum, second):
        assert response.status_code == 409
        assert "backup is in progress" in response.json()["detail"]


async def test_create_backup_now_without_room_gives_the_figures(env, client, monkeypatch):
    monkeypatch.setattr(db_backup.shutil, "disk_usage", lambda p: DiskUsage(50 * GiB, 49 * GiB, 1 * GiB))
    response = await client.post("/api/system/backups")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Not enough free space" in detail and "1.0 GiB is free" in detail and "5.0 GiB" in detail
    folder = db_backup.backups_dir(env)
    assert not folder.exists() or list(folder.iterdir()) == []


async def test_a_failed_backup_is_reported_by_the_job(env, client, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(db_backup, "_vacuum_into", fail)
    assert (await client.post("/api/system/backups")).status_code == 202
    job = await _finished_job(client)
    assert job["state"] == "failed" and "No space left on device" in job["error"]
    # The lock is free again
    with operation_guard("prune"):
        pass


async def test_stopping_a_backup_deletes_its_unfinished_files(env, client, monkeypatch):
    started = threading.Event()
    real = db_backup._vacuum_into

    def slow_vacuum(db_path, copy_path, cancel=None):
        started.set()
        assert cancel is not None and cancel.wait(5)
        real(db_path, copy_path, cancel)

    monkeypatch.setattr(db_backup, "_vacuum_into", slow_vacuum)
    assert (await client.post("/api/system/backups")).status_code == 202
    await asyncio.to_thread(started.wait, 5)
    await backups_api.stop_backup_job()
    job = (await client.get("/api/system/backups/current")).json()
    assert job["state"] == "failed" and "shutting down" in job["error"]
    assert list(db_backup.backups_dir(env).glob("*")) == []


async def test_download_returns_the_archive(env, client):
    _fake_archive(env, MANUAL, 1234)
    response = await client.get(f"/api/system/backups/{MANUAL}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    assert MANUAL in response.headers["content-disposition"]
    assert len(response.content) == 1234


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "logs.db",
        ".secret_key",
        "notes.txt",
        MANUAL + ".tmp",
        "logshed-backup-manual-v3-20261101T120000Z.tar.gz.bak",
        "logshed-backup-manual-v3-2026110T120000Z.tar.gz",
        "%2e%2e%2flogs.db",
        "..%2Flogs.db",
    ],
)
async def test_names_outside_the_archive_pattern_are_refused(env, client, name):
    _fake_archive(env, MANUAL)
    (env.parent / "notes.txt").write_text("secret")
    assert (await client.get(f"/api/system/backups/{name}/download")).status_code == 404
    assert (await client.delete(f"/api/system/backups/{name}")).status_code in (404, 405)
    assert env.exists() and (env.parent / "notes.txt").exists()


async def test_a_symlink_with_an_archive_name_is_refused(env, client):
    folder = _folder(env)
    os.symlink(env, folder / MANUAL)
    assert (await client.get(f"/api/system/backups/{MANUAL}/download")).status_code == 404
    assert (await client.delete(f"/api/system/backups/{MANUAL}")).status_code == 404
    assert (await client.get("/api/system/backups")).json()["backups"] == []
    assert env.exists() and (folder / MANUAL).is_symlink()


async def test_delete_removes_one_archive(env, client):
    _fake_archive(env, MANUAL)
    _fake_archive(env, PRE)
    response = await client.delete(f"/api/system/backups/{MANUAL}")
    assert response.status_code == 200
    assert [p.name for p in _folder(env).iterdir()] == [PRE]
    assert (await client.delete(f"/api/system/backups/{MANUAL}")).status_code == 404


async def test_storage_overview_counts_the_backups(env, client):
    _fake_archive(env, MANUAL, 1000)
    _fake_archive(env, PRE, 500)
    body = (await client.get("/api/system/storage")).json()
    assert body["backups_size_bytes"] == 1500 and body["backups_count"] == 2


async def test_only_one_retry_runs_when_two_arrive_together():
    calls = []

    async def handler(skip_backup, marker):
        calls.append((skip_backup, marker))
        await asyncio.sleep(0.01)

    lifecycle.set_recovery({"stage": "migration", "from_schema": 3, "target_schema": 4})
    lifecycle.set_retry_handler(handler)
    try:
        results = await asyncio.gather(lifecycle.request_retry(False), lifecycle.request_retry(True))
    finally:
        lifecycle.set_retry_handler(None)
        lifecycle.reset()
    assert sorted(results) == [False, True]
    assert len(calls) == 1 and calls[0][1]["target_schema"] == 4
