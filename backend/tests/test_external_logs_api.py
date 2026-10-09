"""
Integration tests for LogShed External Log & Incident Query API:
GET /api/v1/logs, GET /api/v1/logs/{id}/context, GET /api/v1/logs/facets, and GET /api/v1/alerts/history.
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
async def logs_token_headers(client: AsyncClient, auth_headers: dict):
    res = await client.post(
        "/api/tokens",
        json={"name": "Log Reader", "scopes": ["logs:read", "alerts:read"]},
        headers=auth_headers,
    )
    raw = res.json()["raw_token"]
    return {"Authorization": f"Bearer {raw}"}


class TestExternalLogsApi:
    """Tests for /api/v1/logs, context, facets, and alerts history."""

    @pytest.mark.asyncio
    async def test_search_logs_with_bearer_token(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Seed test logs
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES (?, ?, '192.168.1.10', 'server-01', 'traefik', 1, 3, 'Connection refused to backend upstream: 10.0.0.12', 'raw')
                """,
                (now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES (?, ?, '192.168.1.20', 'nas-01', 'nextcloud', 1, 6, 'User logged in successfully', 'raw')
                """,
                (now_iso, now_iso),
            )
            # Sync FTS
            conn.execute(
                "INSERT INTO logs_fts(rowid, app_name, source_alias, message) SELECT id, app_name, source_alias, message FROM logs"
            )
            conn.commit()

        # 1. Query with FTS5 search
        res = await client.get(
            "/api/v1/logs",
            params={"query": "refused"},
            headers=logs_token_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["logs"][0]["app_name"] == "traefik"

        # 2. Query with app filter
        res_app = await client.get(
            "/api/v1/logs",
            params={"app_name": "nextcloud"},
            headers=logs_token_headers,
        )
        assert res_app.status_code == 200
        assert res_app.json()["total"] == 1
        assert res_app.json()["logs"][0]["app_name"] == "nextcloud"

    @pytest.mark.asyncio
    async def test_log_context_query(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        now = datetime.now(timezone.utc)

        with get_connection(db_path) as conn:
            for i in range(5):
                ts = (now + timedelta(seconds=i)).isoformat()
                conn.execute(
                    """
                    INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                    VALUES (?, ?, '192.168.1.10', 'server-01', 'app-x', 1, 6, ?, ?)
                    """,
                    (ts, ts, f"Log message line {i}", f"Log message line {i}"),
                )
            conn.commit()

        # Target middle log (id = 3)
        res = await client.get(
            "/api/v1/logs/3/context",
            params={"lines": 2},
            headers=logs_token_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["target_id"] == 3
        assert "target_log" in data
        assert data["target_log"]["id"] == 3
        assert len(data["before_logs"]) >= 1
        assert len(data["after_logs"]) >= 1

    @pytest.mark.asyncio
    async def test_log_facets_query(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        now_iso = datetime.now(timezone.utc).isoformat()

        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES (?, ?, '192.168.1.10', 'compute-01', 'traefik', 1, 6, 'message', 'raw')
                """,
                (now_iso, now_iso),
            )
            conn.commit()

        res = await client.get("/api/v1/logs/facets", headers=logs_token_headers)
        assert res.status_code == 200
        data = res.json()
        assert "sources" in data
        assert "apps" in data
        assert "compute-01" in data["sources"]
        assert "traefik" in data["apps"]
        assert "source_app_mapping" in data
        assert data["source_app_mapping"].get("compute-01") == ["traefik"]

    @pytest.mark.asyncio
    async def test_alert_history_query(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        now_iso = datetime.now(timezone.utc).isoformat()

        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO alert_history (rule_name, trigger_count, sample_log, incident_summary, ai_enrichment, triggered_at)
                VALUES ('Disk Warning', 2, 'Disk 95% full', 'Disk volume alert', 0, ?)
                """,
                (now_iso,),
            )
            conn.commit()

        res = await client.get("/api/v1/alerts/history", headers=logs_token_headers)
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["rule_name"] == "Disk Warning"

    @pytest.mark.asyncio
    async def test_unauthorized_and_forbidden_errors(self, client: AsyncClient, auth_headers: dict):
        # 1. No Authorization header -> 401
        res_no_auth = await client.get("/api/v1/logs")
        assert res_no_auth.status_code == 401

        # 2. Token missing logs:read scope -> 403
        res_maint_token = await client.post(
            "/api/tokens",
            json={"name": "Maint Only", "scopes": ["maintenance:write"]},
            headers=auth_headers,
        )
        maint_token = res_maint_token.json()["raw_token"]
        res_forbidden = await client.get(
            "/api/v1/logs",
            headers={"Authorization": f"Bearer {maint_token}"},
        )
        assert res_forbidden.status_code == 403
