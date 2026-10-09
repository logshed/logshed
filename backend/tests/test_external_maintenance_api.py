"""
Integration tests for LogShed External Maintenance API and multi-session tracking:
Session overlapping, cancellation by session_id, clear all, drop_errors mode, and alert suppression.
"""

import asyncio
from datetime import datetime, timedelta, timezone
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
from app.services.maintenance_service import should_drop_maintenance_error, get_maintenance_status


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
        json={"name": "Automation Runner", "scopes": ["*"]},
        headers=auth_headers,
    )
    raw_token = res.json()["raw_token"]
    return {"Authorization": f"Bearer {raw_token}"}


class TestExternalMaintenanceApi:
    """Tests for /api/v1/maintenance/* endpoints."""

    @pytest.mark.asyncio
    async def test_single_session_lifecycle(self, client: AsyncClient, api_token_headers: dict):
        # 1. Initially inactive
        res_status = await client.get("/api/v1/maintenance/status", headers=api_token_headers)
        assert res_status.status_code == 200
        assert res_status.json()["active"] is False
        assert res_status.json()["active_sessions_count"] == 0

        # 2. Enable maintenance session
        res_enable = await client.post(
            "/api/v1/maintenance/enable",
            json={
                "duration_minutes": 30,
                "reason": "Database migration routine",
                "log_handling": "drop_errors",
                "target_app": "postgres",
            },
            headers=api_token_headers,
        )
        assert res_enable.status_code == 200
        enable_data = res_enable.json()
        session_id = enable_data["session_id"]
        assert session_id is not None
        assert enable_data["active"] is True
        assert enable_data["duration_minutes"] == 30
        assert enable_data["log_handling"] == "drop_errors"
        assert enable_data["target_app"] == "postgres"

        # 3. Status reflects active session
        res_status2 = await client.get("/api/v1/maintenance/status", headers=api_token_headers)
        assert res_status2.status_code == 200
        status_data = res_status2.json()
        assert status_data["active"] is True
        assert status_data["active_sessions_count"] == 1
        assert status_data["log_handling"] == "drop_errors"
        assert len(status_data["sessions"]) == 1
        assert status_data["sessions"][0]["session_id"] == session_id

        # 4. Disable session by session_id
        res_disable = await client.post(
            "/api/v1/maintenance/disable",
            json={"session_id": session_id},
            headers=api_token_headers,
        )
        assert res_disable.status_code == 200
        disable_data = res_disable.json()
        assert disable_data["active"] is False
        assert disable_data["terminated_sessions"] == 1
        assert disable_data["remaining_active_sessions"] == 0

        # 5. Final status check inactive
        res_status3 = await client.get("/api/v1/maintenance/status", headers=api_token_headers)
        assert res_status3.status_code == 200
        assert res_status3.json()["active"] is False

    @pytest.mark.asyncio
    async def test_multi_session_overlapping_and_clear_all(
        self, client: AsyncClient, api_token_headers: dict
    ):
        # Start Session 1: 30 minutes
        res1 = await client.post(
            "/api/v1/maintenance/enable",
            json={"duration_minutes": 30, "reason": "Backup Job 1"},
            headers=api_token_headers,
        )
        assert res1.status_code == 200
        s1_id = res1.json()["session_id"]

        # Start Session 2: 60 minutes
        res2 = await client.post(
            "/api/v1/maintenance/enable",
            json={"duration_minutes": 60, "reason": "Backup Job 2"},
            headers=api_token_headers,
        )
        assert res2.status_code == 200
        s2_id = res2.json()["session_id"]

        # Both active
        res_status = await client.get("/api/v1/maintenance/status", headers=api_token_headers)
        assert res_status.json()["active_sessions_count"] == 2
        assert res_status.json()["active"] is True

        # Cancel Session 1: Session 2 must remain active
        res_cancel1 = await client.post(
            "/api/v1/maintenance/disable",
            json={"session_id": s1_id},
            headers=api_token_headers,
        )
        assert res_cancel1.json()["terminated_sessions"] == 1
        assert res_cancel1.json()["remaining_active_sessions"] == 1
        assert res_cancel1.json()["active"] is True

        # Verify Session 2 is still reported
        res_status2 = await client.get("/api/v1/maintenance/status", headers=api_token_headers)
        assert res_status2.json()["active_sessions_count"] == 1
        assert res_status2.json()["sessions"][0]["session_id"] == s2_id

        # Cancel all with no session_id
        res_clear_all = await client.post(
            "/api/v1/maintenance/disable",
            json={},
            headers=api_token_headers,
        )
        assert res_clear_all.json()["terminated_sessions"] == 1
        assert res_clear_all.json()["remaining_active_sessions"] == 0
        assert res_clear_all.json()["active"] is False

    @pytest.mark.asyncio
    async def test_in_memory_error_drop_filter(
        self, client: AsyncClient, api_token_headers: dict, tmp_path: Path
    ):
        # Enable session with drop_errors scoped to app 'backup-runner'
        res = await client.post(
            "/api/v1/maintenance/enable",
            json={
                "duration_minutes": 45,
                "reason": "Scoped maintenance",
                "log_handling": "drop_errors",
                "target_app": "backup-runner",
            },
            headers=api_token_headers,
        )
        assert res.status_code == 200

        # Fast in-memory check without database lookup
        # Error from backup-runner (severity 3 = ERROR) should be dropped
        assert should_drop_maintenance_error("compute-01", "192.168.1.10", "backup-runner", 3) is True

        # Critical error from backup-runner (severity 2 = CRITICAL) should be dropped
        assert should_drop_maintenance_error("compute-01", "192.168.1.10", "backup-runner", 2) is True

        # Error from postgres (different app) should NOT be dropped
        assert should_drop_maintenance_error("compute-01", "192.168.1.10", "postgres", 3) is False

        # Informational log from backup-runner (severity 6 = INFO) should NOT be dropped
        assert should_drop_maintenance_error("compute-01", "192.168.1.10", "backup-runner", 6) is False

        # Case-insensitive match for backup-runner
        assert should_drop_maintenance_error("compute-01", "192.168.1.10", "Backup-Runner", 3) is True

        # Clear session
        await client.post("/api/v1/maintenance/disable", json={}, headers=api_token_headers)

        # After clearing, error from backup-runner is no longer dropped
        assert should_drop_maintenance_error("compute-01", "192.168.1.10", "backup-runner", 3) is False

    @pytest.mark.asyncio
    async def test_invalid_log_handling_rejected(
        self, client: AsyncClient, api_token_headers: dict
    ):
        res = await client.post(
            "/api/v1/maintenance/enable",
            json={
                "duration_minutes": 30,
                "log_handling": "unsupported_handling_mode",
            },
            headers=api_token_headers,
        )
        assert res.status_code == 422
