"""
Tests for LogShed targeted log deletion endpoints and service.
Covers single-line deletion, multi-line deletion, deletion by host,
by application, by time range, combined criteria, preview endpoint,
and FTS5 synchronization.
"""

import datetime
from pathlib import Path
import sqlite3
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
from app.services.fts_indexer import index_pending_logs


@pytest.fixture(autouse=True)
def reset_logs_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


def _seed_test_logs(db_path: Path):
    """Seed sample logs across hosts, apps, and dates."""
    base_time = datetime.datetime(2026, 9, 1, 12, 0, 0)
    entries = [
        # Host pve1 - nginx
        {
            "timestamp": (base_time + datetime.timedelta(days=1)).isoformat(),
            "received_at": (base_time + datetime.timedelta(days=1)).isoformat(),
            "source_ip": "192.168.1.10",
            "source_alias": "pve1",
            "app_name": "nginx",
            "facility": 1,
            "severity": 3,
            "message": "Nginx connection timed out to backend upstream",
            "raw": "<11>Sep  2 12:00:00 pve1 nginx: Nginx connection timed out",
        },
        {
            "timestamp": (base_time + datetime.timedelta(days=2)).isoformat(),
            "received_at": (base_time + datetime.timedelta(days=2)).isoformat(),
            "source_ip": "192.168.1.10",
            "source_alias": "pve1",
            "app_name": "nginx",
            "facility": 1,
            "severity": 6,
            "message": "Nginx GET /index.html 200 OK",
            "raw": "<14>Sep  3 12:00:00 pve1 nginx: Nginx GET /index.html 200 OK",
        },
        # Host pve1 - sshd
        {
            "timestamp": (base_time + datetime.timedelta(days=3)).isoformat(),
            "received_at": (base_time + datetime.timedelta(days=3)).isoformat(),
            "source_ip": "192.168.1.10",
            "source_alias": "pve1",
            "app_name": "sshd",
            "facility": 4,
            "severity": 4,
            "message": "Failed password for root from 10.0.0.99 port 22",
            "raw": "<36>Sep  4 12:00:00 pve1 sshd: Failed password for root",
        },
        # Host pve2 - docker
        {
            "timestamp": (base_time + datetime.timedelta(days=4)).isoformat(),
            "received_at": (base_time + datetime.timedelta(days=4)).isoformat(),
            "source_ip": "192.168.1.20",
            "source_alias": "pve2",
            "app_name": "dockerd",
            "facility": 1,
            "severity": 6,
            "message": "Container started logshed-frontend",
            "raw": "<14>Sep  5 12:00:00 pve2 dockerd: Container started",
        },
        # Host pve2 - kernel
        {
            "timestamp": (base_time + datetime.timedelta(days=5)).isoformat(),
            "received_at": (base_time + datetime.timedelta(days=5)).isoformat(),
            "source_ip": "192.168.1.20",
            "source_alias": "pve2",
            "app_name": "kernel",
            "facility": 0,
            "severity": 2,
            "message": "Out of memory: Kill process 1234 (python)",
            "raw": "<2>Sep  6 12:00:00 pve2 kernel: Out of memory",
        },
    ]

    query = """
        INSERT INTO logs (
            timestamp, received_at, source_ip, source_alias,
            app_name, facility, severity, message, raw
        ) VALUES (
            :timestamp, :received_at, :source_ip, :source_alias,
            :app_name, :facility, :severity, :message, :raw
        )
    """
    with get_connection(db_path) as conn:
        conn.executemany(query, entries)
        conn.commit()

    # Index into FTS5
    index_pending_logs(db_path)


# ===================================================================
# 1. Single Log Deletion
# ===================================================================

@pytest.mark.asyncio
async def test_delete_single_log_success(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    # Fetch initial logs
    list_res = await client.get("/api/logs", cookies=auth_cookie)
    assert list_res.status_code == 200
    logs = list_res.json()["logs"]
    assert len(logs) == 5

    target_id = logs[0]["id"]

    # Delete single log
    del_res = await client.delete(f"/api/logs/{target_id}", cookies=auth_cookie)
    assert del_res.status_code == 200
    data = del_res.json()
    assert data["status"] == "ok"
    assert data["deleted_count"] == 1

    # Verify deleted from logs
    check_res = await client.get("/api/logs", cookies=auth_cookie)
    remaining = check_res.json()["logs"]
    assert len(remaining) == 4
    assert not any(l["id"] == target_id for l in remaining)

    # Verify deleted from FTS5
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM logs_fts WHERE rowid = ?", (target_id,))
    assert cursor.fetchone()[0] == 0
    conn.close()


@pytest.mark.asyncio
async def test_delete_single_log_not_found(client: AsyncClient, auth_cookie: dict):
    del_res = await client.delete("/api/logs/999999", cookies=auth_cookie)
    assert del_res.status_code == 404
    assert "not found" in del_res.json()["detail"].lower()


# ===================================================================
# 2. Multi-Log Deletion by IDs
# ===================================================================

@pytest.mark.asyncio
async def test_delete_multiple_logs_by_id(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    list_res = await client.get("/api/logs", cookies=auth_cookie)
    logs = list_res.json()["logs"]
    ids_to_delete = [logs[0]["id"], logs[1]["id"]]

    del_res = await client.post(
        "/api/logs/delete",
        json={"log_ids": ids_to_delete},
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2

    check_res = await client.get("/api/logs", cookies=auth_cookie)
    remaining = check_res.json()["logs"]
    assert len(remaining) == 3
    for deleted_id in ids_to_delete:
        assert not any(l["id"] == deleted_id for l in remaining)


# ===================================================================
# 3. Scoped Deletions: Host, App, Time Range
# ===================================================================

@pytest.mark.asyncio
async def test_delete_all_logs_for_host(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    # Delete all logs for host pve1 (should be 3 logs)
    del_res = await client.post(
        "/api/logs/delete",
        json={"sources": ["pve1"]},
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 3

    # Check remaining: only pve2 logs remain
    check_res = await client.get("/api/logs", cookies=auth_cookie)
    remaining = check_res.json()["logs"]
    assert len(remaining) == 2
    assert all(l["source_alias"] == "pve2" for l in remaining)


@pytest.mark.asyncio
async def test_delete_and_filter_symmetric_alias_expansion(client: AsyncClient, auth_cookie: dict):
    """
    Verify symmetric alias expansion:
    Deleting or filtering by an alias (e.g. 'LogShed Server') matches logs that were recorded
    with historical alias casing ('logshed') or raw IP ('127.0.0.1').
    """
    db_path = get_db_path()
    base_time = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # Insert logs with variations of source_alias and raw source_ip
    entries = [
        # Log 1: source_ip = 127.0.0.1, source_alias = 'logshed' (lowercase historical)
        {
            "timestamp": (base_time + datetime.timedelta(minutes=1)).isoformat(),
            "received_at": (base_time + datetime.timedelta(minutes=1)).isoformat(),
            "source_ip": "127.0.0.1",
            "source_alias": "logshed",
            "app_name": "backend",
            "facility": 1,
            "severity": 6,
            "message": "Starting server",
            "raw": "Starting server",
        },
        # Log 2: source_ip = 127.0.0.1, source_alias = 'LogShed' (mixed case)
        {
            "timestamp": (base_time + datetime.timedelta(minutes=2)).isoformat(),
            "received_at": (base_time + datetime.timedelta(minutes=2)).isoformat(),
            "source_ip": "127.0.0.1",
            "source_alias": "LogShed",
            "app_name": "backend",
            "facility": 1,
            "severity": 6,
            "message": "Ready to receive connections",
            "raw": "Ready to receive connections",
        },
        # Log 3: source_ip = 172.22.2.11, source_alias = 'proxmox'
        {
            "timestamp": (base_time + datetime.timedelta(minutes=3)).isoformat(),
            "received_at": (base_time + datetime.timedelta(minutes=3)).isoformat(),
            "source_ip": "172.22.2.11",
            "source_alias": "proxmox",
            "app_name": "pvedaemon",
            "facility": 1,
            "severity": 5,
            "message": "VM status update",
            "raw": "VM status update",
        },
    ]

    query = """
        INSERT INTO logs (
            timestamp, received_at, source_ip, source_alias,
            app_name, facility, severity, message, raw
        ) VALUES (
            :timestamp, :received_at, :source_ip, :source_alias,
            :app_name, :facility, :severity, :message, :raw
        )
    """
    with get_connection(db_path) as conn:
        conn.executemany(query, entries)
        # Configure host alias mapping 127.0.0.1 -> 'LogShed Server'
        conn.execute(
            "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
            ("127.0.0.1", "LogShed Server", base_time.isoformat()),
        )
        conn.commit()

    # 1. Test preview count using alias 'LogShed Server': should match both 127.0.0.1 logs
    preview_res = await client.post(
        "/api/logs/delete/preview",
        json={"sources": ["LogShed Server"]},
        cookies=auth_cookie,
    )
    assert preview_res.status_code == 200
    assert preview_res.json()["matched_count"] == 2

    # 2. Test querying /api/logs with source='LogShed Server'
    query_res = await client.get("/api/logs?source=LogShed+Server", cookies=auth_cookie)
    assert query_res.status_code == 200
    assert query_res.json()["total"] == 2

    # 3. Delete by alias 'LogShed Server'
    del_res = await client.post(
        "/api/logs/delete",
        json={"sources": ["LogShed Server"]},
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2

    # 4. Only proxmox log remains
    remaining_res = await client.get("/api/logs", cookies=auth_cookie)
    assert remaining_res.status_code == 200
    remaining = remaining_res.json()["logs"]
    assert len(remaining) == 1
    assert remaining[0]["source_ip"] == "172.22.2.11"


@pytest.mark.asyncio
async def test_delete_all_logs_for_app(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    # Delete all logs for app nginx (should be 2 logs)
    del_res = await client.post(
        "/api/logs/delete",
        json={"apps": ["nginx"]},
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2

    # Check remaining: no nginx logs
    check_res = await client.get("/api/logs", cookies=auth_cookie)
    remaining = check_res.json()["logs"]
    assert len(remaining) == 3
    assert not any(l["app_name"] == "nginx" for l in remaining)


@pytest.mark.asyncio
async def test_delete_logs_by_time_range(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    # Delete logs from Sep 3 to Sep 5 (inclusive)
    del_res = await client.post(
        "/api/logs/delete",
        json={
            "from": "2026-09-03T00:00:00",
            "to": "2026-09-05T23:59:59",
        },
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    # Sep 3 (nginx), Sep 4 (sshd), Sep 5 (dockerd) -> 3 logs deleted
    assert del_res.json()["deleted_count"] == 3

    check_res = await client.get("/api/logs", cookies=auth_cookie)
    remaining = check_res.json()["logs"]
    assert len(remaining) == 2
    # Sep 2 (nginx) and Sep 6 (kernel) remain
    apps = [l["app_name"] for l in remaining]
    assert "kernel" in apps
    assert "nginx" in apps


@pytest.mark.asyncio
async def test_delete_logs_combined_criteria(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    # Delete logs matching host pve1 AND app nginx
    del_res = await client.post(
        "/api/logs/delete",
        json={
            "sources": ["pve1"],
            "apps": ["nginx"],
        },
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 2

    # pve1 sshd should still be intact
    check_res = await client.get("/api/logs?source=pve1", cookies=auth_cookie)
    remaining_pve1 = check_res.json()["logs"]
    assert len(remaining_pve1) == 1
    assert remaining_pve1[0]["app_name"] == "sshd"


# ===================================================================
# 4. Preview Endpoint
# ===================================================================

@pytest.mark.asyncio
async def test_preview_delete_logs(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    preview_res = await client.post(
        "/api/logs/delete/preview",
        json={"apps": ["nginx"]},
        cookies=auth_cookie,
    )
    assert preview_res.status_code == 200
    assert preview_res.json()["matched_count"] == 2

    # Verify no logs were actually deleted
    check_res = await client.get("/api/logs", cookies=auth_cookie)
    assert len(check_res.json()["logs"]) == 5


# ===================================================================
# 5. Safety Validation & Guardrails
# ===================================================================

@pytest.mark.asyncio
async def test_empty_delete_request_rejected(client: AsyncClient, auth_cookie: dict):
    # Empty request without delete_all flag must return 400
    del_res = await client.post(
        "/api/logs/delete",
        json={},
        cookies=auth_cookie,
    )
    assert del_res.status_code == 400
    assert "must specify at least one filter criterion" in del_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_all_with_explicit_flag(client: AsyncClient, auth_cookie: dict):
    db_path = get_db_path()
    _seed_test_logs(db_path)

    del_res = await client.post(
        "/api/logs/delete",
        json={"delete_all": True},
        cookies=auth_cookie,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted_count"] == 5

    check_res = await client.get("/api/logs", cookies=auth_cookie)
    assert len(check_res.json()["logs"]) == 0
