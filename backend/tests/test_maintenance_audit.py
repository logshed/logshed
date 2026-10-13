"""
Tests for maintenance window audit history: session lifecycle columns, incident history rows,
per-session drop counting, input limits, retention pruning, and external/MCP exposure.
"""

import datetime
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import _last_used_timestamps, api_token_rate_limiter
from app.core.migrations import get_connection as _raw_get_connection, run_migrations
from app.core.pipeline import KeyedMultilineAssembler
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.mcp.server import create_mcp_server
from app.services import maintenance_service as ms
from app.services.alert_evaluator import init_alert_evaluator
from app.services.drop_filter import init_drop_filter
from app.services.retention import execute_prune


def get_connection(path):
    """Connection with dict-style rows, matching run_db_query connections."""
    conn = _raw_get_connection(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()
    _last_used_timestamps.clear()
    ms.update_cached_maintenance_sessions([])
    with ms._dropped_counters_lock:
        ms._dropped_counters.clear()

    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    ms.update_cached_maintenance_sessions([])
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "logs.db"


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


@pytest_asyncio.fixture
async def api_token_headers(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Deploy Bot", "scopes": ["*"]},
        headers=auth_headers,
    )
    return {"Authorization": f"Bearer {res.json()['raw_token']}"}


def _session_row(db_path: Path, session_id: str):
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT * FROM maintenance_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()


def _history_rows(db_path: Path, session_id: str):
    with get_connection(db_path) as conn:
        return conn.execute(
            "SELECT * FROM alert_history WHERE maintenance_session_id = ?", (session_id,)
        ).fetchall()


class TestSchema:
    def test_audit_columns_exist(self, db_path: Path):
        with get_connection(db_path) as conn:
            sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(maintenance_sessions)")}
            hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(alert_history)")}
        assert {"ended_at", "end_reason", "dropped_count", "trigger_label"} <= sess_cols
        assert "maintenance_session_id" in hist_cols


class TestSessionLifecycle:
    def test_create_records_history_row_and_trigger(self, db_path: Path):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(
                conn, duration_minutes=30, trigger_label="UI", initiated_by="web_ui"
            )
        row = _session_row(db_path, data["session_id"])
        assert row["trigger_label"] == "UI"
        assert row["ended_at"] is None
        assert row["end_reason"] is None
        assert row["dropped_count"] == 0

        history = _history_rows(db_path, data["session_id"])
        assert len(history) == 1
        assert history[0]["rule_name"] == ms.MAINTENANCE_HISTORY_RULE_NAME
        assert history[0]["triggered_at"] == row["created_at"]

    def test_terminate_records_end(self, db_path: Path):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(conn, duration_minutes=30)
            terminated, remaining = ms.disable_maintenance_sessions(conn, session_id=data["session_id"])
        assert (terminated, remaining) == (1, 0)
        row = _session_row(db_path, data["session_id"])
        assert row["is_active"] == 0
        assert row["end_reason"] == "terminated"
        assert row["ended_at"] is not None
        assert row["ended_at"] < row["expires_at"]

    def test_expiry_sweep_records_planned_end(self, db_path: Path):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(conn, duration_minutes=30)
            past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
            conn.execute(
                "UPDATE maintenance_sessions SET expires_at = ? WHERE session_id = ?",
                (past, data["session_id"]),
            )
            conn.commit()
            ms.sweep_maintenance_sessions(conn)
        row = _session_row(db_path, data["session_id"])
        assert row["is_active"] == 0
        assert row["end_reason"] == "expired"
        assert row["ended_at"] == past

    def test_disable_after_expiry_is_expired_not_terminated(self, db_path: Path):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(conn, duration_minutes=30)
            past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat()
            conn.execute(
                "UPDATE maintenance_sessions SET expires_at = ? WHERE session_id = ?",
                (past, data["session_id"]),
            )
            conn.commit()
            terminated, _ = ms.disable_maintenance_sessions(conn, session_id=data["session_id"])
        assert terminated == 0
        assert _session_row(db_path, data["session_id"])["end_reason"] == "expired"


class TestDropCounting:
    def test_pipeline_drop_is_counted_per_session(self, db_path: Path):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(
                conn, duration_minutes=30, log_handling="drop_errors", target_app="batch"
            )
            session_id = data["session_id"]

        assembler = KeyedMultilineAssembler()
        for i in range(3):
            assembler._buffers[f"s{i}"] = [
                {"source_alias": "h1", "source_ip": "10.0.0.1", "app_name": "batch",
                 "severity": 3, "message": "boom", "raw": "boom"}
            ]
            assembler._flush_stream_internal(f"s{i}")
        # Non-matching app and non-error severity are kept
        for key, app, sev in (("k1", "other", 3), ("k2", "batch", 6)):
            assembler._buffers[key] = [
                {"source_alias": "h1", "source_ip": "10.0.0.1", "app_name": app,
                 "severity": sev, "message": "x", "raw": "x"}
            ]
            assembler._flush_stream_internal(key)

        assert pipeline_mod.get_dropped_by_filter_count() == 3
        assert ms.get_pending_dropped_counts() == {session_id: 3}

        with get_connection(db_path) as conn:
            assert ms.flush_dropped_counts(conn) == 1
        assert _session_row(db_path, session_id)["dropped_count"] == 3
        assert _history_rows(db_path, session_id)[0]["trigger_count"] == 3
        assert ms.get_pending_dropped_counts() == {}

    def test_termination_persists_pending_counts(self, db_path: Path):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(conn, duration_minutes=30, log_handling="drop_errors")
            for _ in range(4):
                ms.record_maintenance_drop(data["session_id"])
            ms.disable_maintenance_sessions(conn, session_id=data["session_id"])
        assert _session_row(db_path, data["session_id"])["dropped_count"] == 4


class TestLimits:
    def test_concurrent_session_cap(self, db_path: Path):
        with get_connection(db_path) as conn:
            for _ in range(ms.MAX_ACTIVE_MAINTENANCE_SESSIONS):
                ms.create_maintenance_session(conn, duration_minutes=30)
            with pytest.raises(ms.MaintenanceSessionLimitError):
                ms.create_maintenance_session(conn, duration_minutes=30)
            ms.disable_maintenance_sessions(conn)
            ms.create_maintenance_session(conn, duration_minutes=30)

    @pytest.mark.asyncio
    async def test_api_cap_returns_409(self, client: AsyncClient, api_token_headers: dict):
        for _ in range(ms.MAX_ACTIVE_MAINTENANCE_SESSIONS):
            res = await client.post(
                "/api/v1/maintenance/enable", json={"duration_minutes": 5}, headers=api_token_headers
            )
            assert res.status_code == 200
        res = await client.post(
            "/api/v1/maintenance/enable", json={"duration_minutes": 5}, headers=api_token_headers
        )
        assert res.status_code == 409
        assert "maximum" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_ui_cap_returns_409(self, client: AsyncClient, auth_headers: dict, db_path: Path):
        with get_connection(db_path) as conn:
            for _ in range(ms.MAX_ACTIVE_MAINTENANCE_SESSIONS):
                ms.create_maintenance_session(conn, duration_minutes=30)
        until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        res = await client.post("/api/alerts/maintenance", json={"until": until}, headers=auth_headers)
        assert res.status_code == 409

    @pytest.mark.asyncio
    async def test_target_length_limits(self, client: AsyncClient, api_token_headers: dict):
        for field in ("target_app", "target_host"):
            res = await client.post(
                "/api/v1/maintenance/enable",
                json={"duration_minutes": 5, field: "a" * 256},
                headers=api_token_headers,
            )
            assert res.status_code == 422
        res = await client.post(
            "/api/v1/maintenance/enable",
            json={"duration_minutes": 5, "target_app": "a" * 255},
            headers=api_token_headers,
        )
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_mcp_target_length_limit(self):
        tool = create_mcp_server()._tool_manager._tools["enable_maintenance"]
        with pytest.raises(Exception, match="255"):
            await tool.fn(duration_minutes=5, target_app="a" * 256)


class TestRetention:
    def test_prune_removes_old_ended_sessions_and_rows_only(self, db_path: Path):
        with get_connection(db_path) as conn:
            old = ms.create_maintenance_session(conn, duration_minutes=30)
            recent = ms.create_maintenance_session(conn, duration_minutes=30)
            running = ms.create_maintenance_session(conn, duration_minutes=30)
            ms.disable_maintenance_sessions(conn, session_id=old["session_id"])
            ms.disable_maintenance_sessions(conn, session_id=recent["session_id"])
            very_old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)).isoformat()
            conn.execute(
                "UPDATE maintenance_sessions SET ended_at = ? WHERE session_id = ?",
                (very_old, old["session_id"]),
            )
            conn.commit()

        execute_prune(db_path, retention_days=14)

        assert _session_row(db_path, old["session_id"]) is None
        assert _history_rows(db_path, old["session_id"]) == []
        assert _session_row(db_path, recent["session_id"]) is not None
        assert len(_history_rows(db_path, recent["session_id"])) == 1
        assert _session_row(db_path, running["session_id"]) is not None


class TestExposure:
    @pytest.mark.asyncio
    async def test_api_token_session_is_labelled_and_listed(
        self, client: AsyncClient, api_token_headers: dict
    ):
        res = await client.post(
            "/api/v1/maintenance/enable",
            json={
                "duration_minutes": 30,
                "log_handling": "drop_errors",
                "target_app": "batch",
                "target_host": "node-1",
            },
            headers=api_token_headers,
        )
        session_id = res.json()["session_id"]
        ms.record_maintenance_drop(session_id)

        res = await client.get("/api/v1/alerts/history", headers=api_token_headers)
        assert res.status_code == 200
        item = next(i for i in res.json() if i["maintenance_session_id"] == session_id)
        assert item["rule_name"] == "Maintenance Window"
        assert item["maintenance_trigger"] == "Deploy Bot"
        assert item["maintenance_type"] == "drop_errors"
        assert item["maintenance_target_app"] == "batch"
        assert item["maintenance_target_host"] == "node-1"
        assert item["maintenance_ended_at"] is None
        # Unflushed in-memory drops are included in the displayed count
        assert item["maintenance_dropped_count"] == 1

        await client.post(
            "/api/v1/maintenance/disable", json={"session_id": session_id}, headers=api_token_headers
        )
        res = await client.get("/api/v1/alerts/history", headers=api_token_headers)
        item = next(i for i in res.json() if i["maintenance_session_id"] == session_id)
        assert item["maintenance_end_reason"] == "terminated"
        assert item["maintenance_ended_at"] is not None
        assert item["maintenance_dropped_count"] == 1

    @pytest.mark.asyncio
    async def test_ui_and_mcp_trigger_labels(self, client: AsyncClient, auth_headers: dict, db_path: Path):
        until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
        res = await client.post("/api/alerts/maintenance", json={"until": until}, headers=auth_headers)
        assert res.status_code == 200

        mcp_tool = create_mcp_server()._tool_manager._tools["enable_maintenance"]
        mcp_res = await mcp_tool.fn(duration_minutes=5)

        res = await client.get("/api/alerts/history", headers=auth_headers)
        triggers = {
            i["maintenance_trigger"] for i in res.json()["items"] if i["maintenance_session_id"]
        }
        assert triggers == {"UI", "MCP"}
        assert _session_row(db_path, mcp_res["session_id"])["trigger_label"] == "MCP"

    @pytest.mark.asyncio
    async def test_mcp_get_recent_alerts_includes_maintenance_fields(
        self, client: AsyncClient, api_token_headers: dict
    ):
        res = await client.post(
            "/api/v1/maintenance/enable", json={"duration_minutes": 5}, headers=api_token_headers
        )
        session_id = res.json()["session_id"]
        tool = create_mcp_server()._tool_manager._tools["get_recent_alerts"]
        out = await tool.fn()
        text = str(out)
        assert session_id in text
        assert "maintenance_trigger" in text

    @pytest.mark.asyncio
    async def test_clear_history_keeps_session_audit_data(
        self, client: AsyncClient, auth_headers: dict, db_path: Path
    ):
        with get_connection(db_path) as conn:
            data = ms.create_maintenance_session(conn, duration_minutes=30)
        res = await client.delete("/api/alerts/history", headers=auth_headers)
        assert res.status_code == 200
        assert _history_rows(db_path, data["session_id"]) == []
        assert _session_row(db_path, data["session_id"]) is not None
        # Finalizing a session whose history row was cleared must not fail
        with get_connection(db_path) as conn:
            ms.disable_maintenance_sessions(conn, session_id=data["session_id"])

    @pytest.mark.asyncio
    async def test_maintenance_rows_excluded_from_alert_state(
        self, client: AsyncClient, api_token_headers: dict
    ):
        await client.post(
            "/api/v1/maintenance/enable", json={"duration_minutes": 5}, headers=api_token_headers
        )
        res = await client.get("/api/v1/system/metrics", headers=api_token_headers)
        assert res.status_code == 200
        alert_state = res.json()["alert_state"]
        assert alert_state["active"] is False
        assert alert_state["recent_firing_count"] == 0
        assert alert_state["last_rule_name"] is None
