"""
Integration tests for LogShed Telemetry API and system operations:
GET /api/v1/system/metrics, error caching, alert_state sliding window, and operation cooldowns.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import api_token_rate_limiter, _last_used_timestamps
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
from app.services.drop_filter import init_drop_filter
from app.services.alert_evaluator import init_alert_evaluator
from app.api.external_v1 import _last_prune_time, _last_vacuum_time
import app.api.external_v1 as ext_v1_mod


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
    ext_v1_mod._last_prune_time = 0.0
    ext_v1_mod._last_vacuum_time = 0.0
    ext_v1_mod._cached_error_stats = None
    ext_v1_mod._cached_error_stats_time = 0.0

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()
    ext_v1_mod._last_prune_time = 0.0
    ext_v1_mod._last_vacuum_time = 0.0


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
async def full_token_headers(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Full Access Automation", "scopes": ["*"]},
        headers=auth_headers,
    )
    raw = res.json()["raw_token"]
    return {"Authorization": f"Bearer {raw}"}


class TestSystemMetricsApi:
    """Tests for GET /api/v1/system/metrics and system operations."""

    @pytest.mark.asyncio
    async def test_metrics_payload_structure(
        self, client: AsyncClient, full_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Seed sample logs: 3 recent errors (now), and 2 errors from 2 hours ago
        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with get_connection(db_path) as conn:
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                    VALUES (?, ?, '192.168.1.50', 'compute-01', 'postgres', 1, 3, 'Fatal DB error', 'raw')
                    """,
                    (now_iso, now_iso),
                )
            for i in range(2):
                conn.execute(
                    """
                    INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                    VALUES (?, ?, '192.168.1.50', 'compute-01', 'postgres', 1, 3, 'Old DB error', 'raw')
                    """,
                    (two_hours_ago, two_hours_ago),
                )
            conn.commit()

        res = await client.get("/api/v1/system/metrics", headers=full_token_headers)
        assert res.status_code == 200
        data = res.json()

        assert data["status"] == "ok"
        assert "version" in data
        assert "instance_id" in data
        assert data["server_name"] == "LogShed"
        assert isinstance(data["ingest_rate"], (int, float))
        # 24h count includes both recent and 2h-old errors (5 total)
        assert data["error_count_24h"] == 5
        # 1h count strictly includes only recent errors (3 total), not 2h-old errors
        assert data["error_count_1h"] == 3

        # Breakdown
        breakdown = data["top_error_breakdown"]
        assert len(breakdown["top_apps"]) >= 1
        assert breakdown["top_apps"][0]["name"] == "postgres"
        assert breakdown["top_apps"][0]["count"] == 5

        # Storage & telemetry
        assert data["total_logs_count"] >= 5
        assert data["db_size_bytes"] > 0
        assert data["db_size_mb"] >= 0.0
        assert "maintenance" in data
        assert data["maintenance"]["active"] is False

    @pytest.mark.asyncio
    async def test_alert_state_sliding_window(
        self, client: AsyncClient, full_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        # 1. Alert fired 2 hours ago (outside 15-minute sliding window)
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO alert_history (rule_name, trigger_count, sample_log, incident_summary, ai_enrichment, triggered_at)
                VALUES ('Old Fired Rule', 1, 'error msg', 'old alert', 0, ?)
                """,
                (old_iso,),
            )
            conn.commit()

        res = await client.get("/api/v1/system/metrics", headers=full_token_headers)
        assert res.status_code == 200
        state = res.json()["alert_state"]
        # Inactive because older than 15 minutes, but last_rule_name is retained
        assert state["active"] is False
        assert state["recent_firing_count"] == 0
        assert state["last_rule_name"] == "Old Fired Rule"

        # 2. Add an alert fired 5 minutes ago and an alert fired 1 minute ago
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        one_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO alert_history (rule_name, trigger_count, sample_log, incident_summary, ai_enrichment, triggered_at)
                VALUES ('Earlier Rule', 3, 'earlier msg', 'earlier alert', 0, ?)
                """,
                (five_min_ago,),
            )
            conn.execute(
                """
                INSERT INTO alert_history (rule_name, trigger_count, sample_log, incident_summary, ai_enrichment, triggered_at)
                VALUES ('Latest Rule', 5, 'latest msg', 'latest alert', 0, ?)
                """,
                (one_min_ago,),
            )
            conn.commit()

        res2 = await client.get("/api/v1/system/metrics", headers=full_token_headers)
        assert res2.status_code == 200
        state2 = res2.json()["alert_state"]
        assert state2["active"] is True
        assert state2["recent_firing_count"] == 2
        # Deterministically the latest alert
        assert state2["last_rule_name"] == "Latest Rule"

    @pytest.mark.asyncio
    async def test_operation_cooldown_enforcement(
        self, client: AsyncClient, full_token_headers: dict
    ):
        # 1. First prune execution succeeds
        res_prune1 = await client.post("/api/v1/system/prune", headers=full_token_headers)
        assert res_prune1.status_code == 200

        # 2. Immediate second prune execution rejected by cooldown (429)
        res_prune2 = await client.post("/api/v1/system/prune", headers=full_token_headers)
        assert res_prune2.status_code == 429
        assert "cooldown active" in res_prune2.json()["detail"].lower()

        # 3. First vacuum execution succeeds
        res_vac1 = await client.post("/api/v1/system/vacuum", headers=full_token_headers)
        assert res_vac1.status_code == 200

        # 4. Immediate second vacuum execution rejected by cooldown (429)
        res_vac2 = await client.post("/api/v1/system/vacuum", headers=full_token_headers)
        assert res_vac2.status_code == 429
        assert "cooldown active" in res_vac2.json()["detail"].lower()
