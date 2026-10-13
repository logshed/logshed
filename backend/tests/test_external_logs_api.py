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


_NULLABLE_LOGS_DDL = """
DROP TABLE logs;
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    received_at DATETIME NOT NULL,
    source_ip TEXT NOT NULL,
    source_alias TEXT,
    app_name TEXT,
    facility INTEGER NOT NULL DEFAULT 1,
    severity INTEGER NOT NULL DEFAULT 6,
    message TEXT NOT NULL,
    raw TEXT NOT NULL
);
"""

_BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_logs_nullable(db_path: Path) -> None:
    """Recreate the logs table without NOT NULL on source_alias/app_name (defensive-path testing)."""
    with get_connection(db_path) as conn:
        conn.executescript(_NULLABLE_LOGS_DDL)
        conn.commit()


def _insert_log(conn, i: int, source_ip: str, source_alias, app_name, severity: int = 6) -> None:
    ts = (_BASE_TS + timedelta(seconds=i)).isoformat()
    conn.execute(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (ts, ts, source_ip, source_alias, app_name, severity, f"msg {i}", f"raw {i}"),
    )


class TestExternalLogQueryHardening:
    """Count grouping, context matching, include_raw, and total cap behaviour."""

    @pytest.mark.asyncio
    async def test_count_group_by_app_and_source_with_null_values(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        _make_logs_nullable(db_path)
        with get_connection(db_path) as conn:
            _insert_log(conn, 1, "10.0.0.1", "host-a", "web")
            _insert_log(conn, 2, "10.0.0.1", "host-a", None)
            _insert_log(conn, 3, "10.0.0.2", None, None)
            conn.commit()

        res_app = await client.get(
            "/api/v1/logs/count", params={"group_by": "app"}, headers=logs_token_headers
        )
        assert res_app.status_code == 200
        data = res_app.json()
        assert data["total"] == 3
        assert data["breakdown"] == {"unknown": 2, "web": 1}
        assert data["truncated"] is False

        res_src = await client.get(
            "/api/v1/logs/count", params={"group_by": "source"}, headers=logs_token_headers
        )
        assert res_src.status_code == 200
        assert res_src.json()["breakdown"] == {"host-a": 2, "10.0.0.2": 1}

    @pytest.mark.asyncio
    async def test_count_group_limit_truncation(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            n = 0
            for app_idx in range(5):
                for _ in range(5 - app_idx):
                    n += 1
                    _insert_log(conn, n, "10.0.0.1", "host-a", f"app-{app_idx}")
            conn.commit()

        # Truncated: 5 groups, limit 2 -> top 2 returned, total still counts everything
        res = await client.get(
            "/api/v1/logs/count",
            params={"group_by": "app", "group_limit": 2},
            headers=logs_token_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["truncated"] is True
        assert data["breakdown"] == {"app-0": 5, "app-1": 4}
        assert data["total"] == 15

        # Exactly group_limit groups is not truncated
        res_exact = await client.get(
            "/api/v1/logs/count",
            params={"group_by": "app", "group_limit": 5},
            headers=logs_token_headers,
        )
        exact = res_exact.json()
        assert exact["truncated"] is False
        assert len(exact["breakdown"]) == 5
        assert exact["total"] == 15

        # Total respects filters when truncated
        res_filtered = await client.get(
            "/api/v1/logs/count",
            params={"group_by": "app", "group_limit": 1, "app_name": ["app-0", "app-1"]},
            headers=logs_token_headers,
        )
        filtered = res_filtered.json()
        assert filtered["truncated"] is True
        assert filtered["total"] == 9

        # Bounds validation
        for bad in (0, 201):
            res_bad = await client.get(
                "/api/v1/logs/count",
                params={"group_by": "app", "group_limit": bad},
                headers=logs_token_headers,
            )
            assert res_bad.status_code == 422

    @pytest.mark.asyncio
    async def test_context_null_alias_matches_same_ip_only(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        _make_logs_nullable(db_path)
        with get_connection(db_path) as conn:
            # ids 1..6, alternating hosts, all with NULL alias
            _insert_log(conn, 1, "10.0.0.1", None, "web")
            _insert_log(conn, 2, "10.0.0.2", None, "web")
            _insert_log(conn, 3, "10.0.0.1", None, "web")
            _insert_log(conn, 4, "10.0.0.2", None, "web")
            _insert_log(conn, 5, "10.0.0.1", None, "web")
            # Aliased row from the same IP must not be mixed in
            _insert_log(conn, 6, "10.0.0.1", "host-a", "web")
            conn.commit()

        res = await client.get(
            "/api/v1/logs/3/context", params={"lines": 5}, headers=logs_token_headers
        )
        assert res.status_code == 200
        data = res.json()
        assert [e["id"] for e in data["before_logs"]] == [1]
        assert [e["id"] for e in data["after_logs"]] == [5]
        assert [e["id"] for e in data["logs"]] == [1, 3, 5]
        assert data["target_log"]["source_alias"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_context_same_app_filters_and_handles_null_app(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        _make_logs_nullable(db_path)
        with get_connection(db_path) as conn:
            _insert_log(conn, 1, "10.0.0.1", "host-a", "web")
            _insert_log(conn, 2, "10.0.0.1", "host-a", "db")
            _insert_log(conn, 3, "10.0.0.1", "host-a", "web")
            _insert_log(conn, 4, "10.0.0.1", "host-a", "db")
            _insert_log(conn, 5, "10.0.0.1", "host-a", "web")
            _insert_log(conn, 6, "10.0.0.1", "host-a", None)
            _insert_log(conn, 7, "10.0.0.1", "host-a", None)
            conn.commit()

        res_all = await client.get(
            "/api/v1/logs/3/context", params={"lines": 10}, headers=logs_token_headers
        )
        assert [e["id"] for e in res_all.json()["logs"]] == [1, 2, 3, 4, 5, 6, 7]

        res_same = await client.get(
            "/api/v1/logs/3/context",
            params={"lines": 10, "same_app": True},
            headers=logs_token_headers,
        )
        assert [e["id"] for e in res_same.json()["logs"]] == [1, 3, 5]

        res_null_app = await client.get(
            "/api/v1/logs/6/context",
            params={"lines": 10, "same_app": True},
            headers=logs_token_headers,
        )
        assert res_null_app.status_code == 200
        assert [e["id"] for e in res_null_app.json()["logs"]] == [6, 7]

    @pytest.mark.asyncio
    async def test_include_raw_defaults_off_for_logs_and_context(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            for i in range(1, 4):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web")
            conn.commit()

        res = await client.get("/api/v1/logs", headers=logs_token_headers)
        assert res.status_code == 200
        for entry in res.json()["logs"]:
            assert "raw" not in entry
            assert "message" in entry

        res_raw = await client.get(
            "/api/v1/logs", params={"include_raw": True}, headers=logs_token_headers
        )
        assert all(e["raw"].startswith("raw ") for e in res_raw.json()["logs"])

        ctx = await client.get("/api/v1/logs/2/context", headers=logs_token_headers)
        body = ctx.json()
        assert "raw" not in body["target_log"]
        for key in ("logs", "before_logs", "after_logs"):
            assert all("raw" not in e for e in body[key])

        ctx_raw = await client.get(
            "/api/v1/logs/2/context",
            params={"include_raw": True},
            headers=logs_token_headers,
        )
        body_raw = ctx_raw.json()
        assert body_raw["target_log"]["raw"] == "raw 2"
        assert all("raw" in e for e in body_raw["logs"])

    @pytest.mark.asyncio
    async def test_internal_logs_endpoint_still_returns_raw(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            _insert_log(conn, 1, "10.0.0.1", "host-a", "web")
            conn.commit()

        res = await client.get("/api/logs", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["logs"][0]["raw"] == "raw 1"

    @pytest.mark.asyncio
    async def test_logs_total_is_capped_like_internal_endpoint(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            for i in range(1, 1006):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web")
            conn.commit()

        res = await client.get(
            "/api/v1/logs", params={"limit": 5}, headers=logs_token_headers
        )
        data = res.json()
        assert data["total"] == 1001
        assert data["total_capped"] is True
        assert len(data["logs"]) == 5

        # Selective filter stays exact and uncapped
        res_small = await client.get(
            "/api/v1/logs",
            params={"limit": 5, "severity_max": 3},
            headers=logs_token_headers,
        )
        small = res_small.json()
        assert small["total"] == 0
        assert small["total_capped"] is False

        # A deep offset raises the cap to offset + limit + 1
        res_deep = await client.get(
            "/api/v1/logs",
            params={"limit": 5, "offset": 1003},
            headers=logs_token_headers,
        )
        deep = res_deep.json()
        assert deep["total"] == 1005
        assert deep["total_capped"] is False
