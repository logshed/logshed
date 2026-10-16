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
from app.services.fts_indexer import index_pending_logs


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


def _context_ids(body: dict) -> list[int]:
    return [e["id"] for e in body["before_logs"]] + [body["target_log"]["id"]] + [
        e["id"] for e in body["after_logs"]
    ]


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
        assert "logs" not in data
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
        assert _context_ids(res_all.json()) == [1, 2, 3, 4, 5, 6, 7]

        res_same = await client.get(
            "/api/v1/logs/3/context",
            params={"lines": 10, "same_app": True},
            headers=logs_token_headers,
        )
        assert _context_ids(res_same.json()) == [1, 3, 5]

        res_null_app = await client.get(
            "/api/v1/logs/6/context",
            params={"lines": 10, "same_app": True},
            headers=logs_token_headers,
        )
        assert res_null_app.status_code == 200
        assert _context_ids(res_null_app.json()) == [6, 7]

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
        for key in ("before_logs", "after_logs"):
            assert all("raw" not in e for e in body[key])

        ctx_raw = await client.get(
            "/api/v1/logs/2/context",
            params={"include_raw": True},
            headers=logs_token_headers,
        )
        body_raw = ctx_raw.json()
        assert body_raw["target_log"]["raw"] == "raw 2"
        assert all("raw" in e for e in body_raw["before_logs"] + body_raw["after_logs"])

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

        # Paging with a cursor keeps the same filter-wide capped total
        assert data["next_cursor"]
        res_page2 = await client.get(
            "/api/v1/logs",
            params={"limit": 5, "cursor": data["next_cursor"]},
            headers=logs_token_headers,
        )
        assert res_page2.json()["total"] == 1001
        assert res_page2.json()["total_capped"] is True

    @pytest.mark.asyncio
    async def test_context_missing_log_returns_404(
        self, client: AsyncClient, logs_token_headers: dict
    ):
        res = await client.get("/api/v1/logs/999999999/context", headers=logs_token_headers)
        assert res.status_code == 404
        assert "not found" in res.json()["detail"]


_SECRET_MSG = "login failed password=hunter2secret for admin"


class TestExternalRedaction:
    """Secrets are scrubbed on external paths while the internal UI API stays unredacted."""

    def _seed(self, db_path: Path) -> None:
        with get_connection(db_path) as conn:
            for i in range(1, 4):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web")
            conn.execute(
                "UPDATE logs SET message = ?, raw = ? WHERE id = 2", (_SECRET_MSG, _SECRET_MSG)
            )
            conn.commit()

    @pytest.mark.asyncio
    async def test_logs_and_context_redact_message_and_raw(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        self._seed(tmp_path / "logs.db")

        res = await client.get(
            "/api/v1/logs", params={"include_raw": True}, headers=logs_token_headers
        )
        entry = next(e for e in res.json()["logs"] if e["id"] == 2)
        assert "hunter2secret" not in entry["message"]
        assert "hunter2secret" not in entry["raw"]
        assert "[REDACTED]" in entry["message"]

        ctx = await client.get(
            "/api/v1/logs/2/context", params={"include_raw": True}, headers=logs_token_headers
        )
        target = ctx.json()["target_log"]
        assert "hunter2secret" not in target["message"]
        assert "hunter2secret" not in target["raw"]

        ctx_around = await client.get("/api/v1/logs/1/context", headers=logs_token_headers)
        assert all(
            "hunter2secret" not in e["message"] for e in ctx_around.json()["after_logs"]
        )

    @pytest.mark.asyncio
    async def test_internal_logs_endpoint_is_not_redacted(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        self._seed(tmp_path / "logs.db")
        res = await client.get("/api/logs", headers=auth_headers)
        entry = next(e for e in res.json()["logs"] if e["id"] == 2)
        assert "hunter2secret" in entry["message"]

    @pytest.mark.asyncio
    async def test_alert_history_redacts_text_fields(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        with get_connection(tmp_path / "logs.db") as conn:
            conn.execute(
                """
                INSERT INTO alert_history (rule_name, trigger_count, sample_log, incident_summary, ai_enrichment, triggered_at)
                VALUES ('R', 1, ?, ?, 1, '2026-01-01T00:00:00+00:00')
                """,
                (_SECRET_MSG, "Cause: " + _SECRET_MSG),
            )
            conn.commit()

        res = await client.get("/api/v1/alerts/history", headers=logs_token_headers)
        assert res.status_code == 200
        item = res.json()[0]
        assert "hunter2secret" not in item["sample_log"]
        assert "hunter2secret" not in item["incident_summary"]
        assert "[REDACTED]" in item["sample_log"]


def _insert_log_at(conn, ts: str, app_name: str = "web", message: str = "msg", severity: int = 6, raw: str = "raw") -> int:
    cur = conn.execute(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES (?, ?, '10.0.0.1', 'host-a', ?, 1, ?, ?, ?)
        """,
        (ts, ts, app_name, severity, message, raw),
    )
    return cur.lastrowid


class TestKeysetPagination:
    async def _collect(self, client: AsyncClient, headers: dict, **params) -> tuple[list[dict], int]:
        entries: list[dict] = []
        pages = 0
        cursor = None
        while True:
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            res = await client.get("/api/v1/logs", params=query, headers=headers)
            assert res.status_code == 200, res.text
            body = res.json()
            assert "next_cursor" in body
            entries.extend(body["logs"])
            pages += 1
            cursor = body["next_cursor"]
            if cursor is None:
                return entries, pages
            assert pages < 100

    @pytest.mark.asyncio
    async def test_pages_cover_all_rows_once_in_order_including_timestamp_ties(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            # Groups of rows share a timestamp so the id tie-break is exercised across page edges
            for group in range(5):
                ts = (_BASE_TS + timedelta(seconds=group)).isoformat()
                for _ in range(5):
                    _insert_log_at(conn, ts)
            conn.commit()
            expected = [
                r[0] for r in conn.execute("SELECT id FROM logs ORDER BY timestamp DESC, id DESC")
            ]

        entries, pages = await self._collect(client, logs_token_headers, limit=4)
        assert [e["id"] for e in entries] == expected
        assert pages == 7  # 25 rows / 4 per page

    @pytest.mark.asyncio
    async def test_last_page_has_explicit_null_cursor_and_exact_page_boundary(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            for i in range(1, 7):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web")
            conn.commit()

        # Exactly one full page: no further page, so next_cursor is null (not absent)
        res = await client.get("/api/v1/logs", params={"limit": 6}, headers=logs_token_headers)
        body = res.json()
        assert len(body["logs"]) == 6
        assert "next_cursor" in body and body["next_cursor"] is None

        res = await client.get("/api/v1/logs", params={"limit": 5}, headers=logs_token_headers)
        assert res.json()["next_cursor"]

        # Empty result
        res = await client.get(
            "/api/v1/logs", params={"app_name": "nope"}, headers=logs_token_headers
        )
        assert res.json()["logs"] == []
        assert res.json()["next_cursor"] is None
        assert "offset" not in res.json()

    @pytest.mark.asyncio
    async def test_cursor_works_with_filters_include_raw_and_redaction(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            for i in range(1, 7):
                ts = (_BASE_TS + timedelta(seconds=i)).isoformat()
                _insert_log_at(
                    conn,
                    ts,
                    app_name="web" if i % 2 else "db",
                    message=f"connect with password=hunter{i}",
                    raw=f"<13> connect with password=hunter{i}",
                )
            conn.commit()

        entries, _ = await self._collect(
            client, logs_token_headers, app_name="web", limit=2, include_raw=True
        )
        assert len(entries) == 3
        assert {e["app_name"] for e in entries} == {"web"}
        for e in entries:
            assert "raw" in e
            assert "hunter" not in e["message"]
            assert "hunter" not in e["raw"]

    @pytest.mark.asyncio
    async def test_newer_rows_do_not_shift_later_pages(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        with get_connection(db_path) as conn:
            for i in range(1, 11):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web")
            conn.commit()

        first = (await client.get("/api/v1/logs", params={"limit": 4}, headers=logs_token_headers)).json()
        with get_connection(db_path) as conn:
            for i in range(100, 105):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web")
            conn.commit()

        second = (
            await client.get(
                "/api/v1/logs",
                params={"limit": 4, "cursor": first["next_cursor"]},
                headers=logs_token_headers,
            )
        ).json()
        assert [e["id"] for e in first["logs"]] == [10, 9, 8, 7]
        assert [e["id"] for e in second["logs"]] == [6, 5, 4, 3]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cursor",
        [
            "!!!not-base64!!!",
            "bm90LWpzb24",                       # "not-json"
            "eyJhIjoxfQ",                        # {"a":1}
            "WyIyMDI2LTAxLTAxIl0",               # ["2026-01-01"]
            "WyIyMDI2LTAxLTAxIiwidHdvIl0",       # ["2026-01-01","two"]
            "WyIyMDI2LTAxLTAxIix0cnVlXQ",        # ["2026-01-01",true]
            "WzEsMl0",                           # [1,2]
            "x" * 201,
        ],
    )
    async def test_malformed_cursor_returns_422(
        self, client: AsyncClient, logs_token_headers: dict, cursor: str
    ):
        res = await client.get("/api/v1/logs", params={"cursor": cursor}, headers=logs_token_headers)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_offset_is_not_part_of_the_external_api(self, client: AsyncClient):
        res = await client.get("/openapi.json")
        params = {p["name"] for p in res.json()["paths"]["/api/v1/logs"]["get"]["parameters"]}
        assert "cursor" in params
        assert "offset" not in params

    def test_keyset_query_uses_an_index_without_a_full_sort(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        run_migrations(db_path)
        with get_connection(db_path) as conn:
            for i in range(1, 200):
                _insert_log(conn, i, "10.0.0.1", "host-a", "web", severity=i % 8)
            conn.commit()
            ts = (_BASE_TS + timedelta(seconds=100)).isoformat()
            plan = conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT id FROM logs
                WHERE (logs.timestamp < :ts OR (logs.timestamp = :ts AND logs.id < :id))
                ORDER BY logs.timestamp DESC, logs.id DESC
                LIMIT 51
                """,
                {"ts": ts, "id": 100},
            ).fetchall()
        details = " | ".join(str(r[-1]) for r in plan)
        assert "SEARCH logs USING" in details and "idx_logs_time_sev" in details
        assert "SCAN logs" not in details
        # Only ties on the same timestamp are sorted ("LAST TERM"), never the whole result set
        assert "TEMP B-TREE FOR ORDER BY" not in details


class TestFtsFallbackAndConsistency:
    """FTS5 syntax error fallback, busy mapping and indexer wake-up on the v1 log endpoints."""

    def _seed(self, db_path: Path) -> None:
        with get_connection(db_path) as conn:
            for i, message in enumerate(
                ["key=value pair seen", "foo-bar happened", "unbalanced quote", "a:b ratio", "plain row"], start=1
            ):
                ts = (_BASE_TS + timedelta(seconds=i)).isoformat()
                _insert_log_at(conn, ts, message=message)
            conn.commit()
        index_pending_logs(db_path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query", ['"unbalanced', "foo AND", "a:b", "foo-bar", "key=value", "-foo", "user@host"]
    )
    async def test_unusual_queries_never_return_500(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path, query: str
    ):
        self._seed(tmp_path / "logs.db")
        res = await client.get("/api/v1/logs", params={"query": query}, headers=logs_token_headers)
        assert res.status_code == 200, res.text
        res_count = await client.get(
            "/api/v1/logs/count", params={"query": query}, headers=logs_token_headers
        )
        assert res_count.status_code == 200, res_count.text
        assert res_count.json()["total"] == res.json()["total"]

    @pytest.mark.asyncio
    async def test_fallback_still_finds_punctuated_terms(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        self._seed(tmp_path / "logs.db")
        res = await client.get("/api/v1/logs", params={"query": "foo-bar"}, headers=logs_token_headers)
        assert res.status_code == 200
        assert [e["message"] for e in res.json()["logs"]] == ["foo-bar happened"]

    @pytest.mark.asyncio
    async def test_lock_error_returns_503(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path, monkeypatch
    ):
        from app.api import external_v1 as v1_mod
        from tests.test_logs_api import _patch_locked_fts

        self._seed(tmp_path / "logs.db")
        _patch_locked_fts(monkeypatch, v1_mod)
        for path in ("/api/v1/logs", "/api/v1/logs/count"):
            res = await client.get(path, params={"query": "plain"}, headers=logs_token_headers)
            assert res.status_code == 503, path
            assert "locked" not in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_fts_query_wakes_the_indexer(
        self, client: AsyncClient, logs_token_headers: dict, tmp_path: Path
    ):
        from unittest.mock import MagicMock
        from app.services.fts_indexer import set_fts_worker

        self._seed(tmp_path / "logs.db")
        worker = MagicMock()
        set_fts_worker(worker)
        try:
            res = await client.get("/api/v1/logs", headers=logs_token_headers)
            assert res.status_code == 200
            assert not worker.notify_new_logs.called

            res = await client.get("/api/v1/logs", params={"query": "plain"}, headers=logs_token_headers)
            assert res.status_code == 200
            assert worker.notify_new_logs.call_count == 1

            res = await client.get("/api/v1/logs/count", params={"query": "plain"}, headers=logs_token_headers)
            assert res.status_code == 200
            assert worker.notify_new_logs.call_count == 2
        finally:
            set_fts_worker(None)

    @pytest.mark.asyncio
    async def test_v1_and_ui_agree_on_time_bounds(
        self, client: AsyncClient, logs_token_headers: dict, auth_headers: dict, tmp_path: Path
    ):
        self._seed(tmp_path / "logs.db")
        bounds = {"from": "2026-01-01T02:00:02.000+02:00", "to": "2026-01-01T00:00:04.000Z"}
        res_v1 = await client.get("/api/v1/logs/count", params=bounds, headers=logs_token_headers)
        res_ui = await client.get("/api/logs", params=bounds, headers=auth_headers)
        assert res_v1.json()["total"] == res_ui.json()["total"] == 3
