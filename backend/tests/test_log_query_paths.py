"""
The three ways to select logs by filter (the web UI list, REST v1 and scoped deletion) give the
same answer for the same filters.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import _last_used_timestamps, api_token_rate_limiter
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services import log_query, log_store
from app.services.alert_evaluator import init_alert_evaluator
from app.services.drop_filter import init_drop_filter
from app.services.fts_indexer import index_pending_logs
from tests.db_helpers import open_db

_BASE = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
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
    yield db_file
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest"},
    ) as ac:
        ac.cookies = {SESSION_COOKIE_NAME: create_session_token(user_id=1)}
        yield ac


@pytest_asyncio.fixture
async def token_headers(client: AsyncClient) -> dict[str, str]:
    res = await client.post("/api/tokens", json={"name": "Reader", "scopes": ["logs:read"]})
    return {"Authorization": f"Bearer {res.json()['raw_token']}"}


def _ts(i: int) -> str:
    return (_BASE + timedelta(seconds=i)).isoformat()


def _seed(db_path: Path, rows: list[tuple]) -> None:
    """rows: (seconds, source_ip, source_alias, app_name, severity, message)."""
    with open_db(db_path) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility,"
            " severity, message, raw) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            [(_ts(s), _ts(s), ip, alias, app, sev, msg, msg) for s, ip, alias, app, sev, msg in rows],
        )
        conn.commit()
    index_pending_logs(db_path)


async def _ids_by_path(client: AsyncClient, token_headers: dict, params: dict) -> dict[str, object]:
    """The ids the list routes return and the count the delete preview gives, for one filter."""
    ui = await client.get("/api/logs", params={**params, "limit": 1000})
    assert ui.status_code == 200, ui.text
    v1 = await client.get("/api/v1/logs", params={**params, "limit": 1000}, headers=token_headers)
    assert v1.status_code == 200, v1.text
    delete_body = {
        "sources": params.get("source"),
        "apps": params.get("app_name"),
        "severity_max": params.get("severity_max"),
        "from": params.get("from"),
        "to": params.get("to"),
        "query": params.get("query"),
    }
    preview = await client.post(
        "/api/logs/delete/preview", json={k: v for k, v in delete_body.items() if v is not None}
    )
    assert preview.status_code == 200, preview.text
    return {
        "ui": [log["id"] for log in ui.json()["logs"]],
        "v1": [log["id"] for log in v1.json()["logs"]],
        "preview": preview.json()["matched_count"],
    }


class TestTheThreePathsAgree:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "params",
        [
            {"source": ["10.0.0.1"]},
            {"source": ["srv1"], "app_name": ["web"]},
            {"severity_max": 3},
            {"query": "timeout"},
            {"query": "timeout", "from": _ts(3), "to": _ts(6)},
            {"from": "2026-11-01T01:00:04+01:00"},
        ],
    )
    async def test_same_filter_selects_the_same_logs(self, client, token_headers, reset_env, params):
        _seed(
            reset_env,
            [
                (1, "10.0.0.1", "srv1", "web", 6, "timeout one"),
                (2, "10.0.0.1", "srv1", "db", 3, "refused"),
                (3, "10.0.0.2", "", "web", 2, "timeout two"),
                (4, "10.0.0.1", "srv1", "web", 6, "timeout three"),
                (5, "10.0.0.2", "", "cron", 6, "ok"),
                (6, "10.0.0.1", "srv1", "web", 4, "timeout four"),
                (7, "10.0.0.3", "nas", "smb", 6, "timeout five"),
            ],
        )
        with open_db(reset_env) as conn:
            conn.execute("INSERT INTO host_aliases (ip, alias, created_at) VALUES ('10.0.0.1', 'srv1', '2026-11-01')")
            conn.commit()
        found = await _ids_by_path(client, token_headers, params)
        assert found["ui"] == found["v1"]
        assert found["preview"] == len(found["ui"])
        assert found["ui"], "the filter should select something"

    @pytest.mark.asyncio
    async def test_logs_named_docker_are_docker_output_on_every_path(self, client, token_headers, reset_env):
        # Container logs stored as "docker" by a Docker host at 10.0.0.5 that has since been
        # deleted or renamed. A syslog filter on that address leaves them out, as the live stream does.
        _seed(
            reset_env,
            [
                (1, "10.0.0.5", "", "sshd", 6, "syslog line"),
                (2, "10.0.0.5", "docker", "nginx", 6, "container line"),
            ],
        )
        with open_db(reset_env) as conn:
            conn.execute("DELETE FROM docker_endpoints")
            conn.commit()
        found = await _ids_by_path(client, token_headers, {"source": ["10.0.0.5"]})
        assert found == {"ui": [1], "v1": [1], "preview": 1}

    @pytest.mark.asyncio
    async def test_logs_named_by_the_docker_alias_setting_are_docker_output(self, client, token_headers, reset_env):
        _seed(
            reset_env,
            [
                (1, "10.0.0.5", "", "sshd", 6, "syslog line"),
                (2, "10.0.0.5", "nas-docker", "nginx", 6, "container line"),
            ],
        )
        with open_db(reset_env) as conn:
            conn.execute("DELETE FROM docker_endpoints")
            conn.execute(
                "INSERT INTO system_settings (key, value, updated_at, is_encrypted)"
                " VALUES ('docker_source_alias', 'nas-docker', '2026-11-01', 0)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )
            conn.commit()
        found = await _ids_by_path(client, token_headers, {"source": ["10.0.0.5"]})
        assert found == {"ui": [1], "v1": [1], "preview": 1}

    @pytest.mark.asyncio
    async def test_newest_first_windows_give_the_full_search_result(self, client, token_headers, reset_env, monkeypatch):
        _seed(reset_env, [(i, "10.0.0.1", "srv1", "web", 6, f"timeout {i}") for i in range(1, 401)])
        params = {"query": "timeout", "limit": 10}
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", ())
        full_ui = (await client.get("/api/logs", params=params)).json()
        full_v1 = (await client.get("/api/v1/logs", params=params, headers=token_headers)).json()

        attempts: list[int] = []
        real_run = log_store.run_newest_first

        def _counting(conn, run_params, run, oldest_needed, matches, needed):
            def _run(p):
                attempts.append(p["fts_min_rowid"])
                return run(p)
            return real_run(conn, run_params, _run, oldest_needed, matches, needed)

        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        monkeypatch.setattr(log_store, "run_newest_first", _counting)
        windowed_ui = (await client.get("/api/logs", params=params)).json()
        windowed_v1 = (await client.get("/api/v1/logs", params=params, headers=token_headers)).json()

        assert windowed_ui == full_ui
        assert windowed_v1 == full_v1
        # Both answered from the newest 50 rows: the page and the capped total fit in them only
        # when the cap does, so the web UI (cap 1,001) widens to every row and REST does too
        assert attempts and all(a in (351, 201, 0) for a in attempts)
        assert [log["id"] for log in windowed_ui["logs"]] == list(range(400, 390, -1))

    @pytest.mark.asyncio
    async def test_named_severity_on_rest_is_the_numeric_threshold(self, client, token_headers, reset_env):
        _seed(reset_env, [(1, "10.0.0.1", "srv1", "web", s, f"sev {s}") for s in range(8)])
        named = await client.get("/api/v1/logs", params={"severity": "error"}, headers=token_headers)
        numeric = await client.get("/api/v1/logs", params={"severity_max": 3}, headers=token_headers)
        assert named.json()["logs"] == numeric.json()["logs"]
        assert len(named.json()["logs"]) == 4
        bad = await client.get("/api/v1/logs", params={"severity": "loud"}, headers=token_headers)
        assert bad.status_code == 422


class TestUnreadableTimeBounds:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["from", "to"])
    async def test_every_path_refuses_an_unreadable_time_with_422(self, client, token_headers, field):
        expected = log_query.INVALID_DATETIME_DETAIL
        ui = await client.get("/api/logs", params={field: "the day before"})
        v1 = await client.get("/api/v1/logs", params={field: "the day before"}, headers=token_headers)
        count = await client.get("/api/v1/logs/count", params={field: "the day before"}, headers=token_headers)
        preview = await client.post("/api/logs/delete/preview", json={field: "the day before"})
        delete = await client.post("/api/logs/delete", json={field: "the day before"})
        for res in (ui, v1, count, preview, delete):
            assert res.status_code == 422, res.text
            assert res.json()["detail"] == expected
