"""
Tests for logs API: FTS5 queries, multi-source/app filters, facets, surrounding context, and SSE streaming.
"""

import asyncio
import datetime
from pathlib import Path
import sqlite3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
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


def _seed_logs(db_path: Path, entries: list[dict]):
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
    index_pending_logs(db_path)


# ===================================================================
# 1. Log Querying & Native FTS5 Filtering
# ===================================================================

class TestLogQuerying:

    @pytest.mark.asyncio
    async def test_logs_filtering_and_fts(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        test_entries = [
            {
                "timestamp": "2026-08-29T10:00:00Z",
                "received_at": "2026-08-29T10:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "homelab-host",
                "app_name": "nginx",
                "facility": 1,
                "severity": 3,
                "message": "Connection refused upstream failure on backend pool",
                "raw": "<11>1 2026-08-29T10:00:00Z homelab-host nginx - - - Connection refused upstream failure",
            },
            {
                "timestamp": "2026-08-29T11:00:00Z",
                "received_at": "2026-08-29T11:00:01Z",
                "source_ip": "192.168.1.60",
                "source_alias": "pve-node1",
                "app_name": "kernel",
                "facility": 0,
                "severity": 2,
                "message": "Out of Memory: Killed process 412 (mysqld)",
                "raw": "<10>1 2026-08-29T11:00:00Z pve-node1 kernel - - - Out of Memory: Killed process",
            },
            {
                "timestamp": "2026-08-29T12:00:00Z",
                "received_at": "2026-08-29T12:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "homelab-host",
                "app_name": "nextcloud",
                "facility": 1,
                "severity": 6,
                "message": "User admin logged in successfully from 192.168.1.100",
                "raw": "<14>1 2026-08-29T12:00:00Z homelab-host nextcloud - - - User admin logged in",
            },
        ]
        _seed_logs(db_file, test_entries)

        # 1. Unfiltered query
        res = await client.get("/api/logs")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert len(data["logs"]) == 3
        assert data["logs"][0]["app_name"] == "nextcloud"

        # 2. FTS query for 'refused'
        res_fts = await client.get("/api/logs", params={"query": "refused"})
        assert res_fts.status_code == 200
        data_fts = res_fts.json()
        assert data_fts["total"] == 1
        assert data_fts["logs"][0]["app_name"] == "nginx"

        # 3. Source filter (matches alias or IP)
        res_src = await client.get("/api/logs", params={"source": "homelab-host"})
        assert res_src.json()["total"] == 2

        res_src_ip = await client.get("/api/logs", params={"source": "192.168.1.60"})
        assert res_src_ip.json()["total"] == 1
        assert res_src_ip.json()["logs"][0]["source_alias"] == "pve-node1"

        # 4. App name filter
        res_app = await client.get("/api/logs", params={"app_name": "kernel"})
        assert res_app.json()["total"] == 1

        # 5. Severity max filter
        res_sev = await client.get("/api/logs", params={"severity_max": 3})
        assert res_sev.json()["total"] == 2
        for log in res_sev.json()["logs"]:
            assert log["severity"] <= 3

        # 6. Time bounds
        res_time = await client.get(
            "/api/logs",
            params={
                "from": "2026-08-29T10:30:00Z",
                "to": "2026-08-29T11:30:00Z",
            },
        )
        assert res_time.json()["total"] == 1
        assert res_time.json()["logs"][0]["app_name"] == "kernel"

        # 7. Multi-source filtering: comma-separated and repeated params
        res_multi_src_comma = await client.get("/api/logs", params={"source": "homelab-host,pve-node1"})
        assert res_multi_src_comma.status_code == 200
        assert res_multi_src_comma.json()["total"] == 3

        res_multi_src_repeat = await client.get("/api/logs?source=homelab-host&source=pve-node1")
        assert res_multi_src_repeat.status_code == 200
        assert res_multi_src_repeat.json()["total"] == 3

        # 8. Multi-app filtering: comma-separated and repeated params
        res_multi_app_comma = await client.get("/api/logs", params={"app_name": "nginx,kernel"})
        assert res_multi_app_comma.status_code == 200
        assert res_multi_app_comma.json()["total"] == 2
        app_names = {l["app_name"] for l in res_multi_app_comma.json()["logs"]}
        assert app_names == {"nginx", "kernel"}

        res_multi_app_repeat = await client.get("/api/logs?app_name=nginx&app_name=nextcloud")
        assert res_multi_app_repeat.status_code == 200
        assert res_multi_app_repeat.json()["total"] == 2
        app_names_repeat = {l["app_name"] for l in res_multi_app_repeat.json()["logs"]}
        assert app_names_repeat == {"nginx", "nextcloud"}

        # 9. Multi-host and multi-app combined
        res_combined = await client.get("/api/logs?source=homelab-host&app_name=nginx,kernel")
        assert res_combined.status_code == 200
        assert res_combined.json()["total"] == 1
        assert res_combined.json()["logs"][0]["app_name"] == "nginx"
        assert res_combined.json()["logs"][0]["source_alias"] == "homelab-host"

    @pytest.mark.asyncio
    async def test_native_fts5_syntax_and_syntax_error_fallback(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            ("2026-08-30T10:00:00Z", "2026-08-30T10:00:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection refused to backend upstream", "<11>nginx: Connection refused to backend upstream"),
            ("2026-08-30T10:01:00Z", "2026-08-30T10:01:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection timeout to redis", "<11>nginx: Connection timeout to redis"),
            ("2026-08-30T10:02:00Z", "2026-08-30T10:02:01Z", "10.0.0.2", "server2", "auth", 1, 2, "Authentication error: password mismatch", "<10>auth: Authentication error: password mismatch"),
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries,
            )
            conn.commit()
        index_pending_logs(db_file)

        # 1. Column filter syntax: app_name:auth
        res_col = await client.get("/api/logs", params={"query": "app_name:auth"})
        assert res_col.status_code == 200
        data_col = res_col.json()
        assert data_col["total"] == 1
        assert data_col["logs"][0]["app_name"] == "auth"

        # 2. Boolean operators: "Connection NOT timeout"
        res_bool = await client.get("/api/logs", params={"query": "Connection NOT timeout"})
        assert res_bool.status_code == 200
        data_bool = res_bool.json()
        assert data_bool["total"] == 1
        assert "refused" in data_bool["logs"][0]["message"]

        # 3. Wildcard prefix search: "refus*"
        res_prefix = await client.get("/api/logs", params={"query": "refus*"})
        assert res_prefix.status_code == 200
        assert res_prefix.json()["total"] == 1

        # 4. Partial word search as you type (auto prefix matching)
        res_partial = await client.get("/api/logs", params={"query": "passwor"})
        assert res_partial.status_code == 200
        assert res_partial.json()["total"] == 1
        assert "password mismatch" in res_partial.json()["logs"][0]["message"]

        # 5. Malformed syntax (unbalanced quote): fallback should execute without 500 error
        res_bad = await client.get("/api/logs", params={"query": 'Connection "refused'})
        assert res_bad.status_code == 200
        assert res_bad.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_total_capped_indication(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Verify that total_capped is True when row count reaches or exceeds count_limit."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        # Insert 1005 log entries
        bulk_entries = [
            (
                f"2026-08-30T10:00:{i % 60:02d}Z",
                f"2026-08-30T10:00:{i % 60:02d}Z",
                "10.0.0.1",
                "srv1",
                "app1",
                1,
                6,
                f"Log message {i}",
                f"raw {i}",
            )
            for i in range(1005)
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                bulk_entries,
            )

        # Query with default limit=100, offset=0 -> count_limit = max(1001, 101) = 1001
        res = await client.get("/api/logs", params={"limit": 100, "offset": 0})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1001
        assert data["total_capped"] is True

        # Query with offset=1000, limit=50 -> count_limit = max(1001, 1000 + 50 + 1) = 1051
        # DB has 1005 entries, so total is 1005 (< 1051) and total_capped is False
        res_page = await client.get("/api/logs", params={"limit": 50, "offset": 1000})
        assert res_page.status_code == 200
        data_page = res_page.json()
        assert data_page["total"] == 1005
        assert data_page["total_capped"] is False

    @pytest.mark.asyncio
    async def test_malformed_fts5_queries_safety(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Verify that malformed FTS5 search queries fall back cleanly and never raise OperationalError."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            ("2026-08-30T10:00:00Z", "2026-08-30T10:00:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection refused to backend upstream", "<11>nginx: Connection refused to backend upstream"),
            ("2026-08-30T10:01:00Z", "2026-08-30T10:01:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection timeout to redis", "<11>nginx: Connection timeout to redis"),
            ("2026-08-30T10:02:00Z", "2026-08-30T10:02:01Z", "10.0.0.2", "server2", "auth", 1, 2, "Authentication error: password mismatch", "<10>auth: Authentication error: password mismatch"),
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries,
            )
            conn.commit()
        index_pending_logs(db_file)

        malformed_queries = [
            # 1. Unallowlisted or incomplete column prefix
            "bad_col:nginx",
            "foo:bar",
            "status:500",
            "app_name:",
            "app_name:*",
            "source_alias:",
            "source_alias:*",
            "message:",
            "message:*",
            # 2. Dangling boolean operators
            "AND",
            "OR",
            "NOT",
            "NEAR",
            "Connection AND",
            "AND Connection",
            "Connection AND AND refused",
            "Connection OR",
            "Connection NOT",
            # 3. Unbalanced parentheses
            "(Connection refused",
            "Connection refused)",
            "()",
            ")(",
            "Connection (refused",
            # 4. Asterisks and special queries
            "*",
            "**",
            "***",
            "*refused",
            # 5. Unbalanced quotes
            '"',
            '"""',
            '"refused',
            'refused"',
            # 6. Punctuation / URLs
            ":::",
            "http://example.com/api",
            "   ",
        ]

        for q in malformed_queries:
            res = await client.get("/api/logs", params={"query": q})
            assert res.status_code == 200, f"Query {q!r} failed with status {res.status_code}: {res.text}"
            data = res.json()
            assert "logs" in data
            assert "total" in data

    @pytest.mark.asyncio
    async def test_fts_queries_with_dots_and_ip_addresses(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            ("2026-08-30T10:00:00Z", "2026-08-30T10:00:01Z", "192.168.1.1", "gw-router", "nginx", 1, 3, "Incoming request from 192.168.1.1 accepted", "<11>nginx: Incoming request from 192.168.1.1 accepted"),
            ("2026-08-30T10:01:00Z", "2026-08-30T10:01:01Z", "10.0.0.1", "backend-srv", "nginx", 1, 3, "Proxy pass to 10.0.0.1 failed", "<11>nginx: Proxy pass to 10.0.0.1 failed"),
            ("2026-08-30T10:02:00Z", "2026-08-30T10:02:01Z", "10.0.0.2", "auth-srv", "auth", 1, 2, "User login from 10.0.0.1 authenticated", "<10>auth: User login from 10.0.0.1 authenticated"),
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries,
            )
            conn.commit()
        index_pending_logs(db_file)

        # 1. Search for IP address with dots (e.g. 192.168.1.1)
        res_ip = await client.get("/api/logs", params={"query": "192.168.1.1"})
        assert res_ip.status_code == 200
        data_ip = res_ip.json()
        assert data_ip["total"] == 1
        assert "192.168.1.1" in data_ip["logs"][0]["message"]

        # 2. Search for column filter and IP address (e.g. app_name:nginx AND 10.0.0.1)
        res_col_and_ip = await client.get("/api/logs", params={"query": "app_name:nginx AND 10.0.0.1"})
        assert res_col_and_ip.status_code == 200
        data_col_and_ip = res_col_and_ip.json()
        assert data_col_and_ip["total"] == 1
        assert data_col_and_ip["logs"][0]["app_name"] == "nginx"
        assert "10.0.0.1" in data_col_and_ip["logs"][0]["message"]

    @pytest.mark.asyncio
    async def test_fts_prefix_query_combined_with_app_filter_is_evaluated_once(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """
        Regression: combining an app filter with an auto-prefixed search term (e.g. 'existing' -> existing*)
        must not let the planner drive the join from the app index and re-evaluate the FTS5 MATCH
        (rebuilding the full prefix doclist) once per candidate row.
        """
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = []
        for i in range(6000):
            # Many rows outside the app filter contain the search term (large prefix doclist)
            entries.append((
                f"2026-09-01T10:{(i // 60) % 60:02d}:{i % 60:02d}Z", "2026-09-01T10:00:00Z",
                "10.0.0.2", "media-host", "sonarr", 1, 6,
                f"Skipping existing file episode{i}.mkv", "raw",
            ))
            # Many rows inside the app filter that do not contain the term (large scan candidate set)
            entries.append((
                f"2026-09-01T11:{(i // 60) % 60:02d}:{i % 60:02d}Z", "2026-09-01T11:00:00Z",
                "10.0.0.3", "media-host", "radarr", 1, 6,
                f"Refreshing movie{i} metadata", "raw",
            ))
        for i in range(5):
            entries.append((
                f"2026-09-01T09:00:0{i}Z", "2026-09-01T09:00:00Z",
                "10.0.0.3", "media-host", "radarr", 1, 6,
                f"Not importing movie{i}, existing file is better", "raw",
            ))
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries,
            )
            conn.commit()
        index_pending_logs(db_file)

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        res = await client.get("/api/logs", params={"query": "existing", "app_name": "radarr"})
        duration = loop.time() - t0

        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 5
        assert all(log["app_name"] == "radarr" for log in data["logs"])
        assert all("existing" in log["message"] for log in data["logs"])
        assert duration < 0.5, f"Prefix FTS query with app filter took {duration:.2f}s"


# ===================================================================
# 2. Surrounding Context
# ===================================================================

class TestLogContext:

    @pytest.mark.asyncio
    async def test_log_context_scoped_to_same_source_and_app(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            {"timestamp": f"2026-08-29T10:0{i}:00Z", "received_at": f"2026-08-29T10:0{i}:01Z",
             "source_ip": "10.0.0.1", "source_alias": "srv1", "app_name": "web", "facility": 1, "severity": 6,
             "message": f"web line {i}", "raw": f"raw web {i}"}
            for i in range(1, 6)
        ]
        entries.append({
            "timestamp": "2026-08-29T10:02:30Z", "received_at": "2026-08-29T10:02:30Z",
            "source_ip": "10.0.0.1", "source_alias": "srv1", "app_name": "cron", "facility": 1, "severity": 6,
            "message": "cron job ran", "raw": "raw cron",
        })
        entries.append({
            "timestamp": "2026-08-29T10:03:30Z", "received_at": "2026-08-29T10:03:30Z",
            "source_ip": "10.0.0.2", "source_alias": "srv2", "app_name": "database", "facility": 1, "severity": 3,
            "message": "other host message", "raw": "other raw",
        })
        _seed_logs(db_file, entries)

        target_id = 3

        # Default same_app=False
        res = await client.get(f"/api/logs/{target_id}/context", params={"lines": 10, "same_app": "false"})
        assert res.status_code == 200
        data = res.json()
        assert data["target_id"] == target_id
        context_logs = data["logs"]
        for log in context_logs:
            assert log["source_alias"] == "srv1"
        apps = {log["app_name"] for log in context_logs}
        assert "web" in apps
        assert "cron" in apps
        assert all(log["source_alias"] != "srv2" for log in context_logs)

        # same_app=True
        res_app = await client.get(f"/api/logs/{target_id}/context", params={"lines": 4, "same_app": "true"})
        assert res_app.status_code == 200
        app_logs = res_app.json()["logs"]
        assert len(app_logs) == 5
        for log in app_logs:
            assert log["source_alias"] == "srv1"
            assert log["app_name"] == "web"

        # lines=2 -> 1 before + target + 1 after = 3 logs
        res_small = await client.get(f"/api/logs/{target_id}/context", params={"lines": 2, "same_app": "true"})
        assert res_small.status_code == 200
        assert len(res_small.json()["logs"]) == 3

        # Earliest log boundary
        res_first = await client.get("/api/logs/1/context", params={"lines": 10})
        assert res_first.status_code == 200
        assert res_first.json()["logs"][0]["id"] == 1

        # Latest log boundary
        latest_id = max(context_logs, key=lambda l: (l["timestamp"], l["id"]))["id"]
        res_last = await client.get(f"/api/logs/{latest_id}/context", params={"lines": 10})
        assert res_last.status_code == 200
        assert res_last.json()["logs"][-1]["id"] == latest_id

    @pytest.mark.asyncio
    async def test_log_context_404_on_missing_id(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.get("/api/logs/99999/context")
        assert res.status_code == 404


# ===================================================================
# 3. Real-Time SSE Streaming & Facets
# ===================================================================

class TestLogStreamAndFacets:

    @pytest.mark.asyncio
    async def test_log_stream_sse(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        test_entry = {
            "id": 999,
            "timestamp": "2026-08-29T15:00:00Z",
            "received_at": "2026-08-29T15:00:01Z",
            "source_ip": "192.168.1.50",
            "source_alias": "homelab-host",
            "app_name": "nginx",
            "facility": 1,
            "severity": 3,
            "message": "SSE test log message",
            "raw": "raw sse",
        }

        async def _trigger_broadcast():
            for _ in range(50):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            await sse_manager.broadcast(test_entry)

        broadcast_task = asyncio.create_task(_trigger_broadcast())

        async with client.stream("GET", "/api/logs/stream", params={"max_events": 1}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = []
            async for line in response.aiter_lines():
                if line.strip():
                    lines.append(line.strip())

            assert any("event: log" in l for l in lines)
            assert any("SSE test log message" in l for l in lines)

        await broadcast_task

    @pytest.mark.asyncio
    async def test_log_stream_sse_multi_filter(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        entry1 = {
            "id": 1001,
            "timestamp": "2026-08-29T15:01:00Z",
            "received_at": "2026-08-29T15:01:01Z",
            "source_ip": "192.168.1.50",
            "source_alias": "homelab-host",
            "app_name": "nginx",
            "facility": 1,
            "severity": 3,
            "message": "Filtered log 1",
            "raw": "raw 1",
        }
        entry2 = {
            "id": 1002,
            "timestamp": "2026-08-29T15:01:02Z",
            "received_at": "2026-08-29T15:01:03Z",
            "source_ip": "192.168.1.60",
            "source_alias": "pve-node1",
            "app_name": "corosync",
            "facility": 1,
            "severity": 3,
            "message": "Filtered log 2",
            "raw": "raw 2",
        }
        entry3 = {
            "id": 1003,
            "timestamp": "2026-08-29T15:01:04Z",
            "received_at": "2026-08-29T15:01:05Z",
            "source_ip": "192.168.1.70",
            "source_alias": "other-node",
            "app_name": "other-app",
            "facility": 1,
            "severity": 3,
            "message": "Ignored log 3",
            "raw": "raw 3",
        }

        async def _trigger_broadcast():
            for _ in range(50):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            await sse_manager.broadcast(entry3)
            await sse_manager.broadcast(entry1)
            await sse_manager.broadcast(entry2)

        broadcast_task = asyncio.create_task(_trigger_broadcast())

        async with client.stream("GET", "/api/logs/stream?source=homelab-host,pve-node1&max_events=2") as response:
            assert response.status_code == 200
            lines = []
            async for line in response.aiter_lines():
                if line.strip():
                    lines.append(line.strip())

            assert not any("Ignored log 3" in l for l in lines)
            assert any("Filtered log 1" in l for l in lines)
            assert any("Filtered log 2" in l for l in lines)

        await broadcast_task

    @pytest.mark.asyncio
    async def test_log_facets_returns_full_database_distinct_items_and_mappings(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        ts1 = (now_utc - datetime.timedelta(hours=3)).isoformat()
        ts2 = (now_utc - datetime.timedelta(hours=2)).isoformat()
        ts3 = (now_utc - datetime.timedelta(hours=1)).isoformat()

        test_entries = [
            {
                "timestamp": ts1,
                "received_at": ts1,
                "source_ip": "192.168.1.50",
                "source_alias": "homelab-host",
                "app_name": "nginx",
                "facility": 1,
                "severity": 3,
                "message": "Nginx upstream error",
                "raw": f"<11>1 {ts1} homelab-host nginx - - - error",
            },
            {
                "timestamp": ts2,
                "received_at": ts2,
                "source_ip": "192.168.1.60",
                "source_alias": "pve-node1",
                "app_name": "corosync",
                "facility": 0,
                "severity": 2,
                "message": "Corosync quorum lost",
                "raw": f"<10>1 {ts2} pve-node1 corosync - - - quorum lost",
            },
            {
                "timestamp": ts3,
                "received_at": ts3,
                "source_ip": "172.22.2.4",
                "source_alias": "NPM",
                "app_name": "nginx-proxy",
                "facility": 1,
                "severity": 6,
                "message": "GET 200 OK",
                "raw": "raw npm",
            },
        ]
        _seed_logs(db_file, test_entries)

        with sqlite3.connect(str(db_file)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                ("172.22.2.4", "NPM", ts1),
            )

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        assert "sources" in data
        assert "apps" in data
        assert "host_to_apps" in data
        assert "app_to_hosts" in data

        assert "homelab-host" in data["sources"]
        assert "pve-node1" in data["sources"]
        assert "NPM" in data["sources"]
        assert "172.22.2.4" not in data["sources"]
        assert "nginx" in data["apps"]
        assert "corosync" in data["apps"]
        assert "nginx-proxy" in data["apps"]

        assert "nginx" in data["host_to_apps"]["homelab-host"]
        assert "corosync" in data["host_to_apps"]["pve-node1"]
        assert "nginx-proxy" in data["host_to_apps"]["NPM"]
        assert "172.22.2.4" not in data["host_to_apps"]

        assert "homelab-host" in data["app_to_hosts"]["nginx"]
        assert "pve-node1" in data["app_to_hosts"]["corosync"]
        assert "NPM" in data["app_to_hosts"]["nginx-proxy"]

    @pytest.mark.asyncio
    async def test_log_facets_unions_configured_host_aliases_with_recent_facets(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Active configured host aliases (and their historical logs/apps) must union with recent 7-day facets."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        recent_ts = (now_utc - datetime.timedelta(hours=1)).isoformat()
        old_ts = (now_utc - datetime.timedelta(days=20)).isoformat()

        test_entries = [
            # Recent log within 7 days from an unaliased active container
            {
                "timestamp": recent_ts,
                "received_at": recent_ts,
                "source_ip": "10.0.0.5",
                "source_alias": "active-k8s",
                "app_name": "coredns",
                "facility": 1,
                "severity": 6,
                "message": "dns query answered",
                "raw": "dns query answered",
            },
            # Historical log (> 7 days ago, e.g. 20 days ago) from a configured host alias that logs infrequently (e.g. monthly backup)
            {
                "timestamp": old_ts,
                "received_at": old_ts,
                "source_ip": "192.168.1.99",
                "source_alias": "truenas-backup",
                "app_name": "zfs-scrub",
                "facility": 1,
                "severity": 4,
                "message": "Scrub finished with 0 errors",
                "raw": "Scrub finished",
            },
        ]
        _seed_logs(db_file, test_entries)

        with sqlite3.connect(str(db_file)) as conn:
            # 1. Configured host alias with historical logs (> 7 days old)
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                ("192.168.1.99", "truenas-backup", recent_ts),
            )
            # 2. Configured host alias that has NEVER logged anything yet
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                ("192.168.1.254", "switch-core", recent_ts),
            )
            conn.commit()

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        # All 3 systems must be in sources
        assert "active-k8s" in data["sources"]
        assert "truenas-backup" in data["sources"]
        assert "switch-core" in data["sources"]

        # Apps should include both recent coredns and historical zfs-scrub
        assert "coredns" in data["apps"]
        assert "zfs-scrub" in data["apps"]

        # host_to_apps mappings
        assert "coredns" in data["host_to_apps"]["active-k8s"]
        assert "zfs-scrub" in data["host_to_apps"]["truenas-backup"]
        assert data["host_to_apps"]["switch-core"] == []

        # app_to_hosts mappings
        assert "active-k8s" in data["app_to_hosts"]["coredns"]
        assert "truenas-backup" in data["app_to_hosts"]["zfs-scrub"]

    @pytest.mark.asyncio
    async def test_log_facets_canonicalizes_historical_raw_ip_logs(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Historical logs containing raw IP in source_alias or source_ip must resolve to configured alias."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        recent_ts = (now_utc - datetime.timedelta(hours=1)).isoformat()
        old_ts = (now_utc - datetime.timedelta(days=30)).isoformat()

        test_entries = [
            # Recent log for an unrelated host
            {
                "timestamp": recent_ts,
                "received_at": recent_ts,
                "source_ip": "10.0.0.1",
                "source_alias": "gateway",
                "app_name": "dnsmasq",
                "facility": 1,
                "severity": 6,
                "message": "query",
                "raw": "query",
            },
            # Historical log where source_alias was saved as raw IP (before alias was configured)
            {
                "timestamp": old_ts,
                "received_at": old_ts,
                "source_ip": "192.168.1.50",
                "source_alias": "192.168.1.50",
                "app_name": "smartd",
                "facility": 1,
                "severity": 4,
                "message": "Disk healthy",
                "raw": "Disk healthy",
            },
            # Historical log where source_ip is empty but source_alias is raw IP
            {
                "timestamp": old_ts,
                "received_at": old_ts,
                "source_ip": "",
                "source_alias": "192.168.1.50",
                "app_name": "zfs-scrub",
                "facility": 1,
                "severity": 4,
                "message": "Scrub done",
                "raw": "Scrub done",
            },
        ]
        _seed_logs(db_file, test_entries)

        with sqlite3.connect(str(db_file)) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                ("192.168.1.50", "nas-box", recent_ts),
            )
            conn.commit()

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        # "nas-box" must be in sources; raw IP "192.168.1.50" must NOT be in sources
        assert "nas-box" in data["sources"]
        assert "192.168.1.50" not in data["sources"]
        assert "gateway" in data["sources"]

        # Both apps from historical logs must appear and be mapped to "nas-box"
        assert "smartd" in data["apps"]
        assert "zfs-scrub" in data["apps"]
        assert "smartd" in data["host_to_apps"]["nas-box"]
        assert "zfs-scrub" in data["host_to_apps"]["nas-box"]
        assert "192.168.1.50" not in data["host_to_apps"]

        # app_to_hosts should map to "nas-box", not the raw IP
        assert "nas-box" in data["app_to_hosts"]["smartd"]
        assert "nas-box" in data["app_to_hosts"]["zfs-scrub"]
        assert "192.168.1.50" not in data["app_to_hosts"]["smartd"]

    @pytest.mark.asyncio
    async def test_log_facets_resolves_stale_source_alias_during_background_update(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """
        When an alias is modified while logs still contain an older source_alias in SQLite,
        facets must resolve the host to the active alias and omit the stale alias.
        """
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        recent_ts = (now_utc - datetime.timedelta(hours=1)).isoformat()

        test_entries = [
            {
                "timestamp": recent_ts,
                "received_at": recent_ts,
                "source_ip": "192.168.1.50",
                "source_alias": "nas-legacy-name",
                "app_name": "samba",
                "facility": 1,
                "severity": 6,
                "message": "SMB connection established",
                "raw": "SMB connection established",
            },
        ]
        _seed_logs(db_file, test_entries)

        with sqlite3.connect(str(db_file)) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                ("192.168.1.50", "nas-renamed", recent_ts),
            )
            conn.commit()

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        assert "nas-renamed" in data["sources"]
        assert "nas-legacy-name" not in data["sources"]
        assert "192.168.1.50" not in data["sources"]
        assert "samba" in data["host_to_apps"].get("nas-renamed", [])
        assert "nas-legacy-name" not in data["host_to_apps"]
        assert "nas-renamed" in data["app_to_hosts"].get("samba", [])
        assert "nas-legacy-name" not in data["app_to_hosts"].get("samba", [])


    @pytest.mark.asyncio
    async def test_log_facets_preserves_infrequent_unaliased_hosts_beyond_seven_days(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Issue 6: An unaliased host with only 1 log beyond 7 days (e.g. 25 days old) within retention must be discovered."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        recent_ts = (now_utc - datetime.timedelta(hours=1)).isoformat()
        old_ts = (now_utc - datetime.timedelta(days=25)).isoformat()

        test_entries = [
            # Active host logging recently
            {
                "timestamp": recent_ts,
                "received_at": recent_ts,
                "source_ip": "10.0.0.1",
                "source_alias": "primary-server",
                "app_name": "nginx",
                "facility": 1,
                "severity": 6,
                "message": "request handled",
                "raw": "request handled",
            },
            # Infrequent UNALIASED host that logged only once 25 days ago
            {
                "timestamp": old_ts,
                "received_at": old_ts,
                "source_ip": "192.168.1.200",
                "source_alias": "quarterly-backup-vm",
                "app_name": "borgbackup",
                "facility": 1,
                "severity": 4,
                "message": "backup completed",
                "raw": "backup completed",
            },
            # Host that logged only with empty app_name
            {
                "timestamp": old_ts,
                "received_at": old_ts,
                "source_ip": "192.168.1.201",
                "source_alias": "sensor-device",
                "app_name": "",
                "facility": 1,
                "severity": 5,
                "message": "temperature 22C",
                "raw": "temperature 22C",
            },
        ]
        _seed_logs(db_file, test_entries)

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        # Both unaliased historical host and sensor device must be present in sources
        assert "primary-server" in data["sources"]
        assert "quarterly-backup-vm" in data["sources"]
        assert "sensor-device" in data["sources"]

        # Borgbackup app must be present
        assert "nginx" in data["apps"]
        assert "borgbackup" in data["apps"]

        # Mappings
        assert "nginx" in data["host_to_apps"]["primary-server"]
        assert "borgbackup" in data["host_to_apps"]["quarterly-backup-vm"]
        assert data["host_to_apps"]["sensor-device"] == []

        assert "primary-server" in data["app_to_hosts"]["nginx"]
        assert "quarterly-backup-vm" in data["app_to_hosts"]["borgbackup"]

    @pytest.mark.asyncio
    async def test_log_facets_preserves_host_when_alias_equals_ip(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Edge case: When an alias in host_aliases equals the IP address, it must not be pruned from sources or host_to_apps."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        ts = (now_utc - datetime.timedelta(days=5)).isoformat()

        test_entries = [
            {
                "timestamp": ts,
                "received_at": ts,
                "source_ip": "10.0.0.99",
                "source_alias": "10.0.0.99",
                "app_name": "coredns",
                "facility": 1,
                "severity": 6,
                "message": "dns query",
                "raw": "dns query",
            }
        ]
        _seed_logs(db_file, test_entries)

        with sqlite3.connect(str(db_file)) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, notes, created_at) VALUES (?, ?, ?, ?)",
                ("10.0.0.99", "10.0.0.99", "Self-referential alias note", ts),
            )
            conn.commit()

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        assert "10.0.0.99" in data["sources"]
        assert "coredns" in data["apps"]
        assert "coredns" in data["host_to_apps"]["10.0.0.99"]
        assert "10.0.0.99" in data["app_to_hosts"]["coredns"]

    @pytest.mark.asyncio
    async def test_logs_datetime_parameters_validation(self, client: AsyncClient, auth_cookie: dict):
        """Validates ISO-8601 formatting for from and to query params on /api/logs."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # Valid ISO datetime
        res_valid = await client.get(
            "/api/logs",
            params={"from": "2026-09-08T00:00:00Z", "to": "2026-09-09T00:00:00+00:00"},
        )
        assert res_valid.status_code == 200

        # Malformed 'from' parameter
        res_bad_from = await client.get("/api/logs", params={"from": "invalid-datetime"})
        assert res_bad_from.status_code == 422
        assert "Invalid datetime format" in res_bad_from.json()["detail"]

        # Malformed 'to' parameter
        res_bad_to = await client.get("/api/logs", params={"to": "yesterday"})
        assert res_bad_to.status_code == 422
        assert "Invalid datetime format" in res_bad_to.json()["detail"]



