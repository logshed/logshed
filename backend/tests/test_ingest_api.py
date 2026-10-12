"""
Integration tests for high-throughput HTTP batch log ingestion endpoint:
POST /api/v1/ingest
"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.ingest import clear_ingest_token_cache, ingest_rate_limiter
from app.core import pipeline as pipeline_mod
from app.core.api_tokens import api_token_rate_limiter, _last_used_timestamps
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    ingest_rate_limiter.reset()
    clear_ingest_token_cache()
    reset_crypto_cache()
    _last_used_timestamps.clear()

    db_file = tmp_path / "ingest_test.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    ingest_rate_limiter.reset()
    clear_ingest_token_cache()
    _last_used_timestamps.clear()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
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
async def write_token(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Edge Shipper", "scopes": ["logs:write"]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    raw = res.json()["raw_token"]
    return raw


@pytest_asyncio.fixture
async def readonly_token(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Dashboard", "scopes": ["logs:read"]},
        headers=auth_headers,
    )
    assert res.status_code == 200
    raw = res.json()["raw_token"]
    return raw


class TestHttpIngestApi:
    """Tests for POST /api/v1/ingest endpoint."""

    @pytest.mark.asyncio
    async def test_authentication_required(self, client: AsyncClient):
        """Missing or invalid token returns 401."""
        # No header
        resp = await client.post("/api/v1/ingest", json=[{"message": "test"}])
        assert resp.status_code == 401

        # Invalid token
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "test"}],
            headers={"Authorization": "Bearer ls_live_invalidtoken12345"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_scope_enforcement(self, client: AsyncClient, readonly_token: str):
        """Token lacking logs:write scope returns 403 Forbidden."""
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "test log"}],
            headers={"Authorization": f"Bearer {readonly_token}"},
        )
        assert resp.status_code == 403
        assert "logs:write" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_ingest_json_array(self, client: AsyncClient, write_token: str):
        """Accepts JSON array of log records."""
        payload = [
            {
                "timestamp": "2026-10-11T12:00:00Z",
                "app_name": "nginx",
                "source_alias": "edge-proxy",
                "severity": 6,
                "message": "GET /index.html 200 OK",
            },
            {
                "app_name": "redis",
                "severity": 4,
                "message": "Warning: slow query detected",
            },
        ]
        resp = await client.post(
            "/api/v1/ingest",
            json=payload,
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ingested"] == 2

    @pytest.mark.asyncio
    async def test_ingest_ndjson_streaming_payload(self, client: AsyncClient, write_token: str):
        """Accepts newline-delimited JSON payload."""
        lines = [
            json.dumps({"message": "Line 1 from vector", "app_name": "vector"}),
            json.dumps({"message": "Line 2 from vector", "app_name": "vector"}),
            json.dumps({"message": "Line 3 from vector", "app_name": "vector"}),
        ]
        ndjson_body = "\n".join(lines) + "\n"

        resp = await client.post(
            "/api/v1/ingest",
            content=ndjson_body,
            headers={
                "Authorization": f"Bearer {write_token}",
                "Content-Type": "application/x-ndjson",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 3

    @pytest.mark.asyncio
    async def test_empty_payload_handling(self, client: AsyncClient, write_token: str):
        """Empty or whitespace-only payload returns ingested: 0 without error."""
        resp = await client.post(
            "/api/v1/ingest",
            content="",
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 0

    @pytest.mark.asyncio
    async def test_token_cache_performance(self, client: AsyncClient, write_token: str):
        """Subsequent ingest requests utilize in-memory token cache."""
        headers = {"Authorization": f"Bearer {write_token}"}
        payload = [{"message": "Cached check"}]

        # First request populates cache
        resp1 = await client.post("/api/v1/ingest", json=payload, headers=headers)
        assert resp1.status_code == 200

        # Second request hits cache
        resp2 = await client.post("/api/v1/ingest", json=payload, headers=headers)
        assert resp2.status_code == 200

    @pytest.mark.asyncio
    async def test_ingest_rate_limiter(self, client: AsyncClient, write_token: str):
        """Enforces high-throughput rate limiter when limit is hit."""
        headers = {"Authorization": f"Bearer {write_token}"}
        # Temporarily restrict rate limiter max_requests to 3 for testing
        orig_max = ingest_rate_limiter.max_requests
        try:
            ingest_rate_limiter.max_requests = 3
            for _ in range(3):
                resp = await client.post("/api/v1/ingest", json=[{"message": "ok"}], headers=headers)
                assert resp.status_code == 200

            # 4th request should be rate-limited
            resp_limited = await client.post(
                "/api/v1/ingest", json=[{"message": "blocked"}], headers=headers
            )
            assert resp_limited.status_code == 429
            assert "Ingestion rate limit exceeded" in resp_limited.json()["detail"]
        finally:
            ingest_rate_limiter.max_requests = orig_max
