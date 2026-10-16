"""
Integration tests for LogShed Model Context Protocol (MCP) server.
Verifies FastMCP tools, resources, prompts, network endpoints, authentication,
scope enforcement, settings toggle, and CLI subcommand.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import asyncio
import json
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import api_token_rate_limiter, _last_used_timestamps, hash_token
from app.core.config import invalidate_settings_cache
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.services.operation_guard import reset_operation_state
from app.main import create_app
from app.mcp.server import create_mcp_server
from app.services.drop_filter import init_drop_filter
from app.services.alert_evaluator import init_alert_evaluator
from app.services.fts_indexer import index_pending_logs
import app.api.external_v1 as ext_v1_mod
import app.api.mcp as mcp_api_mod
import app.cli as cli_mod


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
    invalidate_settings_cache()
    reset_operation_state()
    ext_v1_mod._cached_error_stats = None
    ext_v1_mod._cached_error_stats_time = 0.0
    # Token ids restart at 1 in every test database, so per-token MCP state must not leak between tests
    mcp_api_mod._sse_sessions.clear()
    mcp_api_mod._sse_token_cache.clear()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.delenv("LOGSHED_ENABLE_MCP", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()
    invalidate_settings_cache()
    mcp_api_mod._sse_sessions.clear()
    mcp_api_mod._sse_token_cache.clear()


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
    from app.api.mcp import stop_mcp_session_manager
    await stop_mcp_session_manager()


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


def seed_sample_data(db_path: Path) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    with get_connection(db_path) as conn:
        # Seed logs
        conn.execute(
            """
            INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
            VALUES (1, ?, ?, '192.168.1.50', 'k3s-master', 'traefik', 1, 3, 'Router connection failed to upstream', 'raw1')
            """,
            (past_iso, past_iso),
        )
        conn.execute(
            """
            INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
            VALUES (2, ?, ?, '192.168.1.50', 'k3s-master', 'traefik', 1, 6, 'HTTP GET /api/v1/health 200 OK', 'raw2')
            """,
            (now_iso, now_iso),
        )
        conn.execute(
            """
            INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
            VALUES (3, ?, ?, '192.168.1.20', 'truenas', 'zfs', 1, 3, 'Pool degraded due to read checksum errors', 'raw3')
            """,
            (now_iso, now_iso),
        )

        # Seed alert rule & incident
        conn.execute(
            """
            INSERT INTO alert_rules (id, name, rule_type, match_pattern, is_enabled, created_at)
            VALUES (1, 'Critical Storage Alert', 'match', 'severity <= 3', 1, ?)
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT INTO alert_history (rule_id, rule_name, triggered_at, trigger_count, sample_log, incident_summary)
            VALUES (1, 'Critical Storage Alert', ?, 1, 'Pool degraded due to read checksum errors', 'ZFS storage pool reported degraded state.')
            """,
            (now_iso,),
        )
        conn.commit()

    index_pending_logs(db_path)

    return {"now": now_iso, "past": past_iso}


FULL_MCP_SCOPES = ["logs:read", "alerts:read", "system:read", "maintenance:write"]

INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest-client", "version": "1.0.0"},
    },
}
INITIALIZED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def tool_call_payload(request_id: int, name: str, arguments: Optional[dict] = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


async def create_token(client: AsyncClient, auth_headers: dict, scopes: list[str], name: str = "MCP Test") -> dict:
    res = await client.post("/api/tokens", json={"name": name, "scopes": scopes}, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    return {"id": body["token"]["id"], "raw": body["raw_token"], "name": name}


async def enable_mcp(client: AsyncClient, auth_headers: dict) -> None:
    res = await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
    assert res.status_code == 200


def bearer(raw_token: str) -> dict:
    return {"Authorization": f"Bearer {raw_token}"}


class LegacySseClient:
    """
    Drives GET /api/v1/mcp/sse directly over ASGI so the event stream stays open while the
    test posts JSON-RPC messages to /api/v1/mcp/messages through the regular httpx client.
    """

    def __init__(self, raw_token: Optional[str] = None, query: str = ""):
        self.headers = [(b"host", b"testserver")]
        if raw_token:
            self.headers.append((b"authorization", f"Bearer {raw_token}".encode()))
        self.query = query
        self.status: Optional[int] = None
        self.endpoint: Optional[str] = None
        self.ended = asyncio.Event()
        self._events: asyncio.Queue = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._started = asyncio.Event()
        self._buffer = b""
        self._task: Optional[asyncio.Task] = None

    async def _receive(self):
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self._started.set()
        elif message["type"] == "http.response.body":
            self._buffer += message.get("body", b"")
            while b"\r\n\r\n" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
                event: dict[str, str] = {}
                for line in raw.decode().splitlines():
                    if line.startswith("event:"):
                        event["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        event["data"] = line[5:].strip()
                if event:
                    self._events.put_nowait(event)
            if not message.get("more_body", False):
                self.ended.set()

    async def connect(self) -> "LegacySseClient":
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/mcp/sse",
            "raw_path": b"/api/v1/mcp/sse",
            "query_string": self.query.encode(),
            "headers": self.headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        self._task = asyncio.create_task(create_app()(scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), timeout=5.0)
        if self.status == 200:
            first = await self.next_event()
            assert first["event"] == "endpoint"
            self.endpoint = first["data"]
        return self

    async def next_event(self, timeout: float = 5.0) -> dict:
        return await asyncio.wait_for(self._events.get(), timeout=timeout)

    async def next_message(self) -> dict:
        while True:
            event = await self.next_event()
            if event.get("event") == "message":
                return json.loads(event["data"])

    async def close(self) -> None:
        self._disconnect.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()


async def sse_initialize(client: AsyncClient, sse: LegacySseClient, headers: Optional[dict] = None) -> None:
    res = await client.post(sse.endpoint, json=INIT_PAYLOAD, headers=headers or {})
    assert res.status_code == 202, res.text
    reply = await sse.next_message()
    assert reply["id"] == 1 and "serverInfo" in reply["result"]
    res = await client.post(sse.endpoint, json=INITIALIZED_NOTIFICATION, headers=headers or {})
    assert res.status_code == 202, res.text


async def sse_call_tool(
    client: AsyncClient, sse: LegacySseClient, request_id: int, name: str, arguments: Optional[dict] = None
) -> dict:
    res = await client.post(sse.endpoint, json=tool_call_payload(request_id, name, arguments))
    assert res.status_code == 202, res.text
    reply = await sse.next_message()
    assert reply["id"] == request_id
    return reply["result"]


class TestLegacySseSessionAuth:
    """S-05: legacy SSE sessions are bound to the token that opened them and re-verified on every post."""

    @pytest.mark.asyncio
    async def test_session_only_post_rejected_after_revocation_and_stream_closes(
        self, client: AsyncClient, auth_headers: dict
    ):
        await enable_mcp(client, auth_headers)
        token = await create_token(client, auth_headers, FULL_MCP_SCOPES)
        sse = await LegacySseClient(token["raw"]).connect()
        try:
            await sse_initialize(client, sse)
            result = await sse_call_tool(client, sse, 2, "list_services")
            assert result["isError"] is False

            assert (await client.delete(f"/api/tokens/{token['id']}", headers=auth_headers)).status_code == 200

            res = await client.post(sse.endpoint, json=tool_call_payload(3, "list_services"))
            assert res.status_code in (401, 403)
            await asyncio.wait_for(sse.ended.wait(), timeout=5.0)

            # Presenting the revoked token itself is rejected too
            res = await client.post(sse.endpoint, json=tool_call_payload(4, "list_services"), headers=bearer(token["raw"]))
            assert res.status_code == 401
        finally:
            await sse.close()

    @pytest.mark.asyncio
    async def test_session_only_post_rejected_after_token_expiry(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        await enable_mcp(client, auth_headers)
        token = await create_token(client, auth_headers, FULL_MCP_SCOPES)
        sse = await LegacySseClient(token["raw"]).connect()
        try:
            await sse_initialize(client, sse)
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            with get_connection(tmp_path / "logs.db") as conn:
                conn.execute("UPDATE api_tokens SET expires_at = ? WHERE id = ?", (past, token["id"]))
                conn.commit()
            mcp_api_mod._sse_token_cache.clear()

            res = await client.post(sse.endpoint, json=tool_call_payload(2, "list_services"))
            assert res.status_code == 401
            assert "expired" in res.json()["detail"]
            await asyncio.wait_for(sse.ended.wait(), timeout=5.0)
        finally:
            await sse.close()

    @pytest.mark.asyncio
    async def test_expiry_is_checked_even_while_the_token_record_is_cached(
        self, client: AsyncClient, auth_headers: dict
    ):
        await enable_mcp(client, auth_headers)
        token = await create_token(client, auth_headers, FULL_MCP_SCOPES)
        sse = await LegacySseClient(token["raw"]).connect()
        try:
            await sse_initialize(client, sse)
            # Simulate a cached record whose expiry time has since passed
            cached_at, info = mcp_api_mod._sse_token_cache[token["id"]]
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            mcp_api_mod._sse_token_cache[token["id"]] = (cached_at, {**info, "expires_at": past})

            res = await client.post(sse.endpoint, json=tool_call_payload(2, "list_services"))
            assert res.status_code == 401
        finally:
            await sse.close()

    @pytest.mark.asyncio
    async def test_session_only_posts_count_toward_rate_limit(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        await enable_mcp(client, auth_headers)
        token = await create_token(client, auth_headers, FULL_MCP_SCOPES)
        monkeypatch.setattr(api_token_rate_limiter, "max_requests", 3)
        sse = await LegacySseClient(token["raw"]).connect()  # request 1
        try:
            await sse_initialize(client, sse)  # requests 2 and 3
            res = await client.post(sse.endpoint, json=tool_call_payload(2, "list_services"))
            assert res.status_code == 429
            assert int(res.headers["Retry-After"]) >= 1
            assert res.headers["X-RateLimit-Remaining"] == "0"
            # A rate limited session stays open
            assert not sse.ended.is_set()
        finally:
            await sse.close()

    @pytest.mark.asyncio
    async def test_post_with_a_different_token_is_rejected(self, client: AsyncClient, auth_headers: dict):
        await enable_mcp(client, auth_headers)
        owner = await create_token(client, auth_headers, FULL_MCP_SCOPES, name="Owner")
        other = await create_token(client, auth_headers, FULL_MCP_SCOPES, name="Other")
        sse = await LegacySseClient(owner["raw"]).connect()
        try:
            res = await client.post(sse.endpoint, json=INIT_PAYLOAD, headers=bearer(other["raw"]))
            assert res.status_code == 403
            # The owner's own token is accepted
            await sse_initialize(client, sse, headers=bearer(owner["raw"]))
        finally:
            await sse.close()

    @pytest.mark.asyncio
    async def test_unknown_session_rejected(self, client: AsyncClient, auth_headers: dict):
        await enable_mcp(client, auth_headers)
        token = await create_token(client, auth_headers, FULL_MCP_SCOPES)
        unknown = "/api/v1/mcp/messages?session_id=deadbeefdeadbeefdeadbeefdeadbeef"
        assert (await client.post(unknown, json=INIT_PAYLOAD)).status_code == 401
        assert (await client.post(unknown, json=INIT_PAYLOAD, headers=bearer(token["raw"]))).status_code == 404

    @pytest.mark.asyncio
    async def test_session_binding_removed_on_disconnect(self, client: AsyncClient, auth_headers: dict):
        await enable_mcp(client, auth_headers)
        token = await create_token(client, auth_headers, FULL_MCP_SCOPES)
        sse = await LegacySseClient(token["raw"]).connect()
        assert len(mcp_api_mod._sse_sessions) == 1
        await sse.close()
        assert mcp_api_mod._sse_sessions == {}
        res = await client.post(sse.endpoint, json=INIT_PAYLOAD)
        assert res.status_code == 401


class TestMCPServer:
    """Tests for in-process FastMCP server tools, resources, and prompts."""

    @pytest.mark.asyncio
    async def test_mcp_tool_execution(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)

        server = create_mcp_server()

        # 1. get_system_metrics
        metrics_tool = server._tool_manager._tools.get("get_system_metrics")
        assert metrics_tool is not None
        metrics_res = await metrics_tool.fn()
        assert metrics_res["status"] == "ok"
        assert metrics_res["error_count_24h"] == 2
        assert "maintenance" in metrics_res

        # 2. list_services
        services_tool = server._tool_manager._tools.get("list_services")
        assert services_tool is not None
        services_res = await services_tool.fn()
        assert "traefik" in services_res["apps"]
        assert "zfs" in services_res["apps"]
        assert "k3s-master" in services_res["sources"]

        # 3. query_logs
        query_tool = server._tool_manager._tools.get("query_logs")
        assert query_tool is not None
        query_res = await query_tool.fn(query="upstream", limit=10)
        assert query_res["total"] >= 1
        assert len(query_res["logs"]) >= 1
        assert query_res["logs"][0]["app_name"] == "traefik"

        # 4. get_log_context
        context_tool = server._tool_manager._tools.get("get_log_context")
        assert context_tool is not None
        context_res = await context_tool.fn(log_id=2, lines=5)
        assert context_res["target_id"] == 2
        assert "logs" not in context_res
        assert context_res["target_log"]["id"] == 2

        # 5. get_recent_alerts
        alerts_tool = server._tool_manager._tools.get("get_recent_alerts")
        assert alerts_tool is not None
        alerts_res = await alerts_tool.fn(limit=5)
        assert len(alerts_res) >= 1
        assert alerts_res[0]["rule_name"] == "Critical Storage Alert"

    @pytest.mark.asyncio
    async def test_mcp_maintenance_lifecycle(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)

        server = create_mcp_server()

        enable_tool = server._tool_manager._tools.get("enable_maintenance")
        disable_tool = server._tool_manager._tools.get("disable_maintenance")
        assert enable_tool is not None
        assert disable_tool is not None

        # Enable maintenance window
        start_res = await enable_tool.fn(
            duration_minutes=25,
            reason="Upgrading traefik ingress",
            log_handling="drop_errors",
            target_app="traefik",
        )
        assert start_res["active"] is True
        assert start_res["log_handling"] == "drop_errors"
        assert start_res["target_app"] == "traefik"
        session_id = start_res["session_id"]
        assert session_id

        # Verify active maintenance metrics reflect it
        metrics_tool = server._tool_manager._tools.get("get_system_metrics")
        metrics_res = await metrics_tool.fn()
        assert metrics_res["maintenance"]["active"] is True
        assert metrics_res["maintenance"]["active_sessions_count"] == 1

        # Disable maintenance window using session_id
        end_res = await disable_tool.fn(session_id=session_id)
        assert end_res["status"] == "ok"
        assert end_res["terminated_count"] == 1
        assert end_res["remaining_active_count"] == 0

        # Verify deactivated
        metrics_res_after = await metrics_tool.fn()
        assert metrics_res_after["maintenance"]["active"] is False

    @pytest.mark.asyncio
    async def test_mcp_token_efficiency_and_projections(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()

        query_tool = server._tool_manager._tools.get("query_logs")
        context_tool = server._tool_manager._tools.get("get_log_context")

        # 1. Verify default compact projection omits raw, facility, received_at
        res_default = await query_tool.fn(limit=5)
        assert len(res_default["logs"]) >= 1
        entry = res_default["logs"][0]
        assert "raw" not in entry
        assert "facility" not in entry
        assert "received_at" not in entry
        assert "message" in entry
        assert "severity" in entry
        assert "severity_label" in entry
        assert entry["severity_label"] in ("error", "info", "warning")

        # 2. Verify include_raw=True restores raw packet
        res_raw = await query_tool.fn(limit=5, include_raw=True)
        assert len(res_raw["logs"]) >= 1
        assert "raw" in res_raw["logs"][0]

        # 3. Verify fields projection returns only requested attributes
        res_fields = await query_tool.fn(
            limit=5, fields=["app_name", "message", "severity_label"]
        )
        assert len(res_fields["logs"]) >= 1
        first_log = res_fields["logs"][0]
        assert set(first_log.keys()) == {"app_name", "message", "severity_label"}

        # 4. Verify get_log_context compact projection
        res_ctx = await context_tool.fn(log_id=2, lines=2)
        assert "target_log" in res_ctx
        assert "raw" not in res_ctx["target_log"]
        assert "severity_label" in res_ctx["target_log"]
        for b_log in res_ctx["before_logs"]:
            assert "raw" not in b_log
            assert "severity_label" in b_log

    @pytest.mark.asyncio
    async def test_mcp_count_logs_tool(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()

        count_tool = server._tool_manager._tools.get("count_logs")
        assert count_tool is not None

        # 1. Total count without grouping
        res_total = await count_tool.fn()
        assert res_total["total"] == 3
        assert res_total["grouped_by"] is None

        # 2. Group by severity
        res_sev = await count_tool.fn(group_by="severity")
        assert res_sev["total"] == 3
        assert res_sev["grouped_by"] == "severity"
        assert res_sev["breakdown"]["error"] == 2
        assert res_sev["breakdown"]["info"] == 1

        # 3. Group by app
        res_app = await count_tool.fn(group_by="app")
        assert res_app["total"] == 3
        assert res_app["grouped_by"] == "app"
        assert res_app["breakdown"]["traefik"] == 2
        assert res_app["breakdown"]["zfs"] == 1

        # 4. Group by source
        res_src = await count_tool.fn(group_by="source")
        assert res_src["total"] == 3
        assert res_src["grouped_by"] == "source"
        assert res_src["breakdown"]["k3s-master"] == 2
        assert res_src["breakdown"]["truenas"] == 1

        # 5. Filter by app and named severity
        res_filtered = await count_tool.fn(apps=["traefik"], severity="error")
        assert res_filtered["total"] == 1

    @pytest.mark.asyncio
    async def test_mcp_count_logs_group_limit_and_truncated(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()
        count_tool = server._tool_manager._tools.get("count_logs")

        # Seeded data has 2 apps: limit 1 truncates, total still counts all logs
        res_cut = await count_tool.fn(group_by="app", group_limit=1)
        assert res_cut["truncated"] is True
        assert res_cut["breakdown"] == {"traefik": 2}
        assert res_cut["total"] == 3

        res_full = await count_tool.fn(group_by="app")
        assert res_full["truncated"] is False
        assert res_full["total"] == 3

        # Out-of-range values are clamped rather than rejected
        res_clamped = await count_tool.fn(group_by="source", group_limit=0)
        assert len(res_clamped["breakdown"]) == 1
        assert res_clamped["truncated"] is True

    @pytest.mark.asyncio
    async def test_mcp_raw_is_fetched_only_when_requested(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()
        query_tool = server._tool_manager._tools.get("query_logs")
        context_tool = server._tool_manager._tools.get("get_log_context")

        res_default = await query_tool.fn(limit=5)
        assert all("raw" not in e for e in res_default["logs"])

        res_raw = await query_tool.fn(limit=5, include_raw=True)
        assert all(e["raw"].startswith("raw") for e in res_raw["logs"])

        # Requesting raw via fields also fetches it
        res_fields = await query_tool.fn(limit=5, fields=["id", "raw"])
        assert all(set(e.keys()) == {"id", "raw"} for e in res_fields["logs"])
        assert all(e["raw"] for e in res_fields["logs"])

        with pytest.raises(Exception, match="not found"):
            await context_tool.fn(log_id=999999999)

        ctx_raw = await context_tool.fn(log_id=2, lines=2, include_raw=True)
        assert ctx_raw["target_log"]["raw"] == "raw2"
        ctx_default = await context_tool.fn(log_id=2, lines=2)
        assert "raw" not in ctx_default["target_log"]

    @pytest.mark.asyncio
    async def test_mcp_ergonomics_named_severity_and_relative_time(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()

        query_tool = server._tool_manager._tools.get("query_logs")

        # 1. Named severity filter ("error" matches severity <= 3)
        res_error = await query_tool.fn(severity="error")
        assert res_error["total"] == 2
        for log in res_error["logs"]:
            assert log["severity"] <= 3

        # 2. Named severity filter ("warning" matches severity <= 4)
        res_warn = await query_tool.fn(severity="warning")
        assert res_warn["total"] == 2

        # 3. Relative time bounds ("1h", "24h", "since_midnight")
        res_1h = await query_tool.fn(from_time="1h")
        assert res_1h["total"] == 3

        res_midnight = await query_tool.fn(from_time="since_midnight")
        assert res_midnight["total"] == 3

    @pytest.mark.asyncio
    async def test_mcp_resources(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)

        server = create_mcp_server()

        # logshed://system/metrics
        res_metrics = server._resource_manager._resources.get("logshed://system/metrics")
        assert res_metrics is not None
        metrics_json_str = await res_metrics.fn()
        metrics_data = json.loads(metrics_json_str)
        assert metrics_data["status"] == "ok"

        # logshed://services
        res_services = server._resource_manager._resources.get("logshed://services")
        assert res_services is not None
        services_json_str = await res_services.fn()
        services_data = json.loads(services_json_str)
        assert "apps" in services_data

        # logshed://alerts/active
        res_alerts = server._resource_manager._resources.get("logshed://alerts/active")
        assert res_alerts is not None
        alerts_json_str = await res_alerts.fn()
        alerts_data = json.loads(alerts_json_str)
        assert "recent_incidents" in alerts_data
        assert len(alerts_data["recent_incidents"]) >= 1

    def test_mcp_prompts(self):
        server = create_mcp_server()

        investigate = server._prompt_manager._prompts.get("investigate_incident")
        assert investigate is not None
        prompt1 = investigate.fn(app_name="nginx", time_window="2h")
        assert "nginx" in prompt1
        assert "2h" in prompt1
        assert "get_system_metrics" in prompt1

        safe_upgrade = server._prompt_manager._prompts.get("safe_service_upgrade")
        assert safe_upgrade is not None
        prompt2 = safe_upgrade.fn(service_name="traefik", duration_minutes=45)
        assert "traefik" in prompt2
        assert "enable_maintenance" in prompt2
        assert "disable_maintenance" in prompt2


class TestMCPApiAndTransport:
    """Tests for HTTP SSE endpoint, authentication, scopes, and enable_mcp toggle."""

    @pytest.mark.asyncio
    async def test_mcp_disabled_by_default_returns_403(self, client: AsyncClient):
        # 1. MCP is off by default
        res_sse = await client.get("/api/v1/mcp/sse")
        assert res_sse.status_code == 403
        assert "Model Context Protocol is disabled in system settings" in res_sse.json()["detail"]

        res_root = await client.post("/api/v1/mcp")
        assert res_root.status_code == 403
        assert "Model Context Protocol is disabled in system settings" in res_root.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_served_only_at_root_and_v1(self, client: AsyncClient, auth_headers: dict):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        bearer = {"Authorization": "Bearer ls_live_unknown"}
        # The unversioned /api/mcp alias is not mounted (POSTs get 405 instead of 404 when the built
        # frontend is present, because the GET-only SPA fallback route then matches the path)
        assert (await client.get("/api/mcp/sse")).status_code == 404
        assert (await client.post("/api/mcp", headers=bearer, json={})).status_code in (404, 405)
        assert (await client.post("/api/mcp/messages", headers=bearer, json={})).status_code in (404, 405)
        # The supported transports still reach token validation
        assert (await client.post("/mcp", headers=bearer, json={})).status_code == 401
        assert (await client.post("/api/v1/mcp", headers=bearer, json={})).status_code == 401
        assert (await client.get("/api/v1/mcp/sse", headers=bearer)).status_code == 401

    @pytest.mark.asyncio
    async def test_mcp_sse_unauthorized_without_token(self, client: AsyncClient, auth_headers: dict):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        res = await client.get("/api/v1/mcp/sse")
        assert res.status_code == 401
        assert "Authorization Bearer token required" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_sse_insufficient_scopes(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        # Create token with only logs:read
        token_res = await client.post(
            "/api/tokens",
            json={"name": "Log Viewer", "scopes": ["logs:read"]},
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        res = await client.get(
            "/api/v1/mcp/sse",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code == 403
        assert "Insufficient token scope" in res.json()["detail"]

        # Also verify raw token without "Bearer " prefix is accepted
        res_raw = await client.get(
            "/api/v1/mcp/sse",
            headers={"Authorization": raw_token},
        )
        assert res_raw.status_code == 403
        assert "Insufficient token scope" in res_raw.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_sse_disabled_setting_returns_403(
        self, client: AsyncClient, auth_headers: dict
    ):
        # Create token with full MCP scopes
        token_res = await client.post(
            "/api/tokens",
            json={
                "name": "AI Agent Token",
                "scopes": ["logs:read", "alerts:read", "system:read", "maintenance:write"],
            },
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        # Disable MCP in settings
        update_res = await client.post(
            "/api/settings",
            json={"enable_mcp": False},
            headers=auth_headers,
        )
        assert update_res.status_code == 200

        # SSE request should return 403
        res = await client.get(
            "/api/v1/mcp/sse",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code == 403
        assert "Model Context Protocol is disabled in system settings" in res.json()["detail"]

        # POST /messages should also return 403
        post_res = await client.post(
            "/api/v1/mcp/messages",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert post_res.status_code == 403
        assert "Model Context Protocol is disabled in system settings" in post_res.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_sse_accepts_query_token_parameter(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        token_res = await client.post(
            "/api/tokens",
            json={"name": "Cursor Agent", "scopes": ["*"]},
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        app = create_app()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/mcp/sse",
            "raw_path": b"/api/v1/mcp/sse",
            "query_string": f"token={raw_token}".encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        status_code = None
        headers = []
        body_chunks = []
        app_task = None

        async def fake_receive():
            return {"type": "http.disconnect"}

        async def fake_send(message):
            nonlocal status_code, headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
                if b"event: endpoint" in b"".join(body_chunks):
                    if app_task and not app_task.done():
                        app_task.cancel()

        app_task = asyncio.create_task(app(scope, fake_receive, fake_send))
        try:
            await asyncio.wait_for(app_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert status_code == 200
        combined = b"".join(body_chunks)
        assert b"event: endpoint" in combined
        assert b"/api/v1/mcp/messages" in combined

    @pytest.mark.asyncio
    async def test_mcp_messages_unauthorized_without_session_or_token(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        res = await client.post("/api/v1/mcp/messages?session_id=deadbeefdeadbeefdeadbeefdeadbeef")
        assert res.status_code == 401
        assert "Authorization Bearer token required" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_streamable_http_unauthorized_without_token(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        res_root = await client.post("/api/v1/mcp")
        assert res_root.status_code == 401
        assert "Authorization Bearer token required" in res_root.json()["detail"]

        res_sse = await client.post("/api/v1/mcp/sse")
        assert res_sse.status_code == 401
        assert "Authorization Bearer token required" in res_sse.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_streamable_http_disabled_setting_returns_403(
        self, client: AsyncClient, auth_headers: dict
    ):
        token_res = await client.post(
            "/api/tokens",
            json={
                "name": "Streamable MCP Token",
                "scopes": ["logs:read", "alerts:read", "system:read", "maintenance:write"],
            },
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        await client.post(
            "/api/settings",
            json={"enable_mcp": False},
            headers=auth_headers,
        )

        res = await client.post(
            "/api/v1/mcp/sse",
            headers={"Authorization": f"Bearer {raw_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert res.status_code == 403
        assert "Model Context Protocol is disabled in system settings" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_mcp_streamable_http_initialize_handshake(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        token_res = await client.post(
            "/api/tokens",
            json={
                "name": "Antigravity Token",
                "scopes": ["logs:read", "alerts:read", "system:read", "maintenance:write"],
            },
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "antigravity-cli", "version": "1.0.0"},
            },
        }

        # 1. Test POST /api/v1/mcp/sse (the exact path Antigravity CLI uses)
        res_sse = await client.post(
            "/api/v1/mcp/sse",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=init_payload,
        )
        assert res_sse.status_code == 200
        assert "mcp-session-id" in res_sse.headers
        session_id = res_sse.headers["mcp-session-id"]
        assert len(session_id) > 0

        # Body contains initialize result
        sse_text = res_sse.text
        assert "LogShed" in sse_text
        assert "serverInfo" in sse_text

        # 2. Test POST /api/v1/mcp root endpoint
        res_root = await client.post(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=init_payload,
        )
        assert res_root.status_code == 200
        assert "mcp-session-id" in res_root.headers
        assert "LogShed" in res_root.text

        # 3. Test calling tools/call over the initialized session
        root_session_id = res_root.headers["mcp-session-id"]
        tool_call_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_system_metrics",
                "arguments": {},
            },
        }
        res_tool = await client.post(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "mcp-session-id": root_session_id,
            },
            json=tool_call_payload,
        )
        assert res_tool.status_code == 200
        assert "db_size_bytes" in res_tool.text

    @pytest.mark.asyncio
    async def test_mcp_root_endpoint_streamable_http(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        token_res = await client.post(
            "/api/tokens",
            json={
                "name": "Root MCP Token",
                "scopes": ["logs:read", "alerts:read", "system:read", "maintenance:write"],
            },
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        # Directly hit root /mcp (Streamable HTTP spec)
        res = await client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=init_payload,
        )
        assert res.status_code == 200
        assert "mcp-session-id" in res.headers
        assert "LogShed" in res.text

    @pytest.mark.asyncio
    async def test_rest_api_logs_count_endpoint(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)

        token_res = await client.post(
            "/api/tokens",
            json={
                "name": "Count API Token",
                "scopes": ["logs:read"],
            },
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        # Call /api/v1/logs/count
        res = await client.get(
            "/api/v1/logs/count?group_by=severity",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert data["grouped_by"] == "severity"
        assert data["breakdown"]["error"] == 2



class TestMCPCli:
    """Tests for CLI subcommand integration."""

    def test_cli_parser_recognizes_mcp_command(self):
        import argparse
        parser = argparse.ArgumentParser(prog="app.cli")
        subparsers = parser.add_subparsers(dest="command")
        mcp_parser = subparsers.add_parser("mcp")
        mcp_parser.add_argument("--db-path", default="/data/logs.db")

        args = parser.parse_args(["mcp", "--db-path", "/tmp/test.db"])
        assert args.command == "mcp"
        assert args.db_path == "/tmp/test.db"

    def test_run_mcp_stdio_aborts_when_mcp_disabled(self, monkeypatch):
        monkeypatch.setenv("LOGSHED_ENABLE_MCP", "false")
        invalidate_settings_cache()

        with pytest.raises(SystemExit) as exc_info:
            cli_mod.run_mcp_stdio()
        assert exc_info.value.code == 1


class TestMCPCsrfExemption:
    """MCP routes under /api/v1/mcp authenticate by token only and are exempt from the X-Requested-With check."""

    @staticmethod
    def _bare_client() -> AsyncClient:
        """Client without a default X-Requested-With header."""
        return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver")

    @pytest.mark.asyncio
    async def test_raw_token_post_without_csrf_header_not_rejected_by_csrf(
        self, client: AsyncClient, auth_headers: dict
    ):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        token_res = await client.post(
            "/api/tokens",
            json={"name": "Log Viewer", "scopes": ["logs:read"]},
            headers=auth_headers,
        )
        raw_token = token_res.json()["raw_token"]

        async with self._bare_client() as bare:
            # Raw Authorization value (no Bearer prefix) and ?token= both reach token validation (scope 403, not CSRF 403)
            res_raw = await bare.post("/api/v1/mcp/sse", headers={"Authorization": raw_token}, json={})
            assert res_raw.status_code == 403
            assert "Insufficient token scope" in res_raw.json()["detail"]

            res_query = await bare.post(f"/api/v1/mcp/sse?token={raw_token}", json={})
            assert res_query.status_code == 403
            assert "Insufficient token scope" in res_query.json()["detail"]

    @pytest.mark.asyncio
    async def test_cookie_only_requests_are_rejected(self, client: AsyncClient, auth_headers: dict):
        await client.post("/api/settings", json={"enable_mcp": True}, headers=auth_headers)
        cookie_only = {"Cookie": auth_headers["Cookie"]}

        async with self._bare_client() as bare:
            for method, path in (
                ("POST", "/api/v1/mcp/sse"),
                ("GET", "/api/v1/mcp/sse"),
                ("POST", "/api/v1/mcp"),
                ("POST", "/api/v1/mcp/messages"),
            ):
                res = await bare.request(method, path, headers=cookie_only)
                assert res.status_code == 401, (method, path)
                assert "Authorization Bearer token required" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_other_v1_paths_still_require_csrf_header_for_cookie_auth(
        self, client: AsyncClient, auth_headers: dict
    ):
        cookie_only = {"Cookie": auth_headers["Cookie"]}
        async with self._bare_client() as bare:
            res = await bare.post(
                "/api/v1/maintenance/enable", json={"duration_minutes": 5}, headers=cookie_only
            )
            assert res.status_code == 403
            assert "X-Requested-With" in res.json()["detail"]


class TestMCPRedaction:
    """MCP tools never return secrets that appear in stored logs or alert history."""

    @pytest.mark.asyncio
    async def test_mcp_tools_redact_secrets(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        secret = "token leaked password=hunter2secret here"
        with get_connection(db_path) as conn:
            conn.execute("UPDATE logs SET message = ?, raw = ? WHERE id = 2", (secret, secret))
            conn.execute("UPDATE alert_history SET sample_log = ?, incident_summary = ?", (secret, secret))
            conn.commit()

        server = create_mcp_server()
        tools = server._tool_manager._tools

        queried = await tools["query_logs"].fn(limit=10, include_raw=True)
        assert "hunter2secret" not in json.dumps(queried)

        context = await tools["get_log_context"].fn(log_id=2, lines=5, include_raw=True)
        assert "hunter2secret" not in json.dumps(context)
        assert "[REDACTED]" in context["target_log"]["message"]

        alerts = await tools["get_recent_alerts"].fn(limit=5)
        assert "hunter2secret" not in json.dumps(alerts)
        assert "[REDACTED]" in alerts[0]["sample_log"]

    def test_count_logs_description_lists_group_by_aliases(self):
        server = create_mcp_server()
        desc = server._tool_manager._tools["count_logs"].description
        for token in ("app_name", "level", "source_alias", "host"):
            assert token in desc


class TestMCPQueryLogsCursor:
    @pytest.mark.asyncio
    async def test_query_logs_pages_with_cursor(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()
        query_tool = server._tool_manager._tools.get("query_logs")

        seen: list[int] = []
        cursor = None
        for _ in range(50):
            res = await query_tool.fn(limit=1, cursor=cursor)
            assert "offset" not in res
            assert "next_cursor" in res
            seen.extend(e["id"] for e in res["logs"])
            cursor = res["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) >= 2
        assert seen == sorted(seen, reverse=True)

    @pytest.mark.asyncio
    async def test_query_logs_rejects_malformed_cursor(self, tmp_path: Path):
        db_path = tmp_path / "logs.db"
        seed_sample_data(db_path)
        server = create_mcp_server()
        query_tool = server._tool_manager._tools.get("query_logs")
        with pytest.raises(Exception, match="cursor"):
            await query_tool.fn(limit=5, cursor="garbage!!")
