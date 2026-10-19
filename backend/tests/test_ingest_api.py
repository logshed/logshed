"""
Integration tests for high-throughput HTTP batch log ingestion endpoint:
POST /api/v1/ingest
"""

import asyncio
from datetime import datetime, timedelta, timezone
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
        # No Authorization header and no X-Requested-With: rejected by CSRF middleware (no blanket /api/v1 exemption)
        resp = await client.post("/api/v1/ingest", json=[{"message": "test"}])
        assert resp.status_code == 403

        # No Authorization header but with X-Requested-With: rejected by token authentication
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "test"}],
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
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


def _queued_entries() -> list[dict]:
    """Drain and return every entry currently on the shared ingestion queue."""
    q = pipeline_mod.get_queue()
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class _FakeDropFilter:
    """Drops entries whose message contains the given text."""

    def __init__(self, needle: str):
        self.needle = needle

    def should_drop(self, source_alias, source_ip, app_name, message, severity=None):
        return 11 if self.needle in (message or "") else None


@pytest.fixture
def isolated_filters(monkeypatch):
    """Run with no drop rules and no maintenance sessions unless a test installs them."""
    from app.services import drop_filter as drop_filter_mod
    from app.services import maintenance_service

    monkeypatch.setattr(drop_filter_mod, "_drop_filter", _FakeDropFilter("\x00never\x00"))
    monkeypatch.setattr(maintenance_service, "_active_maintenance_sessions", [])
    monkeypatch.setattr(maintenance_service, "_dropped_counters", {})
    return drop_filter_mod, maintenance_service


@pytest.mark.usefixtures("isolated_filters")
class TestIngestPipelinePath:
    """HTTP records are complete entries: no multiline merging, and the shared filters apply (B-21)."""

    @pytest.mark.asyncio
    async def test_indented_record_is_not_merged_into_previous(self, client, write_token, monkeypatch):
        import app.main as main_mod
        from app.core.pipeline import KeyedMultilineAssembler

        assembler = KeyedMultilineAssembler()
        monkeypatch.setattr(main_mod, "get_assembler", lambda: assembler)

        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"app_name": "svc", "severity": 6, "message": "first record"},
                {"app_name": "svc", "severity": 3, "message": "    indented second record"},
                {"app_name": "svc", "severity": 6, "message": "at com.example.Main"},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        await assembler.flush_all()
        entries = _queued_entries()
        assert [e["message"] for e in entries] == [
            "first record",
            "    indented second record",
            "at com.example.Main",
        ]
        assert [e["severity"] for e in entries] == [6, 3, 6]

    @pytest.mark.asyncio
    async def test_drop_rule_drops_http_records(self, client, write_token, isolated_filters, monkeypatch):
        drop_filter_mod, _ = isolated_filters
        monkeypatch.setattr(drop_filter_mod, "_drop_filter", _FakeDropFilter("healthcheck"))

        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "GET /healthcheck 200"}, {"message": "real event"}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 1
        assert data["dropped"] == 1
        assert data["ingested"] == 1
        assert [e["message"] for e in _queued_entries()] == ["real event"]
        assert pipeline_mod.get_dropped_by_filter_count() == 1

    @pytest.mark.asyncio
    async def test_drop_errors_session_drops_http_errors(self, client, write_token, isolated_filters, monkeypatch):
        _, maintenance_service = isolated_filters
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        monkeypatch.setattr(
            maintenance_service,
            "_active_maintenance_sessions",
            [{"session_id": "maint-1", "log_handling": "drop_errors", "expires_at": future, "target_app": "db"}],
        )

        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"app_name": "db", "severity": "error", "message": "connection refused"},
                {"app_name": "db", "severity": "info", "message": "restarted"},
                {"app_name": "web", "severity": "error", "message": "upstream down"},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["dropped"] == 1
        assert [e["message"] for e in _queued_entries()] == ["restarted", "upstream down"]
        assert maintenance_service.get_pending_dropped_counts() == {"maint-1": 1}

    @pytest.mark.asyncio
    async def test_full_queue_counts_as_dropped_without_blocking(self, client, write_token):
        q = pipeline_mod.get_queue()
        while not q.full():
            q.put_nowait({"message": "filler"})

        resp = await asyncio.wait_for(
            client.post(
                "/api/v1/ingest",
                json=[{"message": "no room"}],
                headers={"Authorization": f"Bearer {write_token}"},
            ),
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 0
        assert resp.json()["dropped"] == 1
        assert pipeline_mod.get_dropped_count() == 1


@pytest.mark.usefixtures("isolated_filters")
class TestIngestLimitsAndFields:
    """Body, entry and field limits on POST /api/v1/ingest (S-06)."""

    @pytest.mark.asyncio
    async def test_oversize_content_length_is_rejected(self, client, write_token, monkeypatch):
        monkeypatch.setenv("INGEST_MAX_BODY_BYTES", "1000")
        body = json.dumps([{"message": "x" * 2000}])
        resp = await client.post(
            "/api/v1/ingest",
            content=body,
            headers={"Authorization": f"Bearer {write_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert _queued_entries() == []

    @pytest.mark.asyncio
    async def test_oversize_chunked_body_is_rejected(self, client, write_token, monkeypatch):
        monkeypatch.setenv("INGEST_MAX_BODY_BYTES", "1000")
        chunks_sent = []

        async def body_chunks():
            yield b"["
            for i in range(50):
                chunks_sent.append(i)
                yield json.dumps({"message": "y" * 100}).encode() + b","
            yield b"{}]"

        resp = await client.post(
            "/api/v1/ingest",
            content=body_chunks(),
            headers={"Authorization": f"Bearer {write_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert _queued_entries() == []

    @pytest.mark.asyncio
    async def test_default_body_limit_is_five_mib(self, client, write_token, monkeypatch):
        monkeypatch.delenv("INGEST_MAX_BODY_BYTES", raising=False)
        from app.api import ingest as ingest_mod

        assert ingest_mod.get_ingest_max_body_bytes() == 5 * 1024 * 1024
        monkeypatch.setenv("INGEST_MAX_BODY_BYTES", "not-a-number")
        assert ingest_mod.get_ingest_max_body_bytes() == 5 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_too_many_entries_is_rejected(self, client, write_token):
        body = "[" + ",".join(["{}"] * 10_001) + "]"
        resp = await client.post(
            "/api/v1/ingest",
            content=body,
            headers={"Authorization": f"Bearer {write_token}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert "10000" in resp.json()["detail"].replace(",", "")
        assert _queued_entries() == []

    @pytest.mark.asyncio
    async def test_exactly_max_entries_is_accepted(self, client, write_token):
        body = "\n".join(['{"message": "m"}'] * 10_000)
        resp = await client.post(
            "/api/v1/ingest",
            content=body,
            headers={"Authorization": f"Bearer {write_token}", "Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 10_000
        assert len(_queued_entries()) == 10_000

    @pytest.mark.asyncio
    async def test_empty_severity_is_stored_as_info(self, client, write_token):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "a", "severity": ""}, {"message": "b", "level": "  "}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        assert [e["severity"] for e in _queued_entries()] == [6, 6]

    @pytest.mark.asyncio
    async def test_message_and_raw_are_capped_and_raw_is_cleaned(self, client, write_token):
        from app.core.pipeline import MAX_MESSAGE_BYTES

        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "M" * (MAX_MESSAGE_BYTES + 10), "raw": "R" * (MAX_MESSAGE_BYTES * 2)},
                {"message": "colored", "raw": "\x1b[31mred\x1b[0m text\x07"},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        big, colored = _queued_entries()
        assert big["message"] == "M" * MAX_MESSAGE_BYTES
        assert big["raw"] == "R" * MAX_MESSAGE_BYTES
        assert colored["raw"] == "red text"

    @pytest.mark.asyncio
    async def test_response_reports_accepted_and_dropped(self, client, write_token):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "one"}, {"message": "two"}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "ingested": 2, "accepted": 2, "dropped": 0}


@pytest.mark.usefixtures("isolated_filters")
class TestIngestReservedIdentitiesAndTimestamps:
    """Ingest cannot forge LogShed's own or Docker identities, and timestamps are kept in range (S-19)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "item",
        [
            {"source_ip": "127.0.0.1", "source_alias": "logshed"},
            {"source_ip": "127.0.0.1", "source_alias": "LogShed"},
            {"source_ip": "docker"},
            {"source_ip": "Docker", "source_alias": "my-host"},
            {"source_ip": "docker:2"},
        ],
    )
    async def test_reserved_identity_is_rejected(self, client, write_token, item):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "fine"}, {"message": "forged", **item}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 422
        assert "entry 2" in resp.json()["detail"]
        assert _queued_entries() == []

    @pytest.mark.asyncio
    async def test_forwarded_hosts_keep_their_source_fields(self, client, write_token):
        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "a", "source_ip": "10.0.0.7", "source_alias": "nas"},
                {"message": "b", "source_ip": "127.0.0.1", "source_alias": "local-vector"},
                {"message": "c", "source_ip": "docker-host-2"},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        entries = _queued_entries()
        assert [(e["source_ip"], e["source_alias"]) for e in entries] == [
            ("10.0.0.7", "nas"),
            ("127.0.0.1", "local-vector"),
            ("docker-host-2", "docker-host-2"),
        ]

    @pytest.mark.asyncio
    async def test_out_of_range_timestamps_use_received_time(self, client, write_token):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).replace(microsecond=0)
        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "future", "timestamp": (now + timedelta(days=2)).isoformat()},
                {"message": "ancient", "timestamp": "1990-01-01T00:00:00Z"},
                {"message": "recent", "timestamp": recent.isoformat()},
                {"message": "slight skew", "timestamp": (now + timedelta(minutes=2)).isoformat()},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        future, ancient, recent_e, skew = _queued_entries()
        assert future["timestamp"] == future["received_at"]
        assert ancient["timestamp"] == ancient["received_at"]
        assert datetime.fromisoformat(recent_e["timestamp"]) == recent
        assert skew["timestamp"] != skew["received_at"]

    @pytest.mark.asyncio
    async def test_timestamp_lower_bound_follows_max_retention(self, client, write_token, monkeypatch):
        monkeypatch.setenv("MAX_RETENTION_DAYS", "3")
        now = datetime.now(timezone.utc)
        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "too old", "timestamp": (now - timedelta(days=4)).isoformat()},
                {"message": "in range", "timestamp": (now - timedelta(days=2)).isoformat()},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        too_old, in_range = _queued_entries()
        assert too_old["timestamp"] == too_old["received_at"]
        assert in_range["timestamp"] != in_range["received_at"]


class TestIngestTokenRevocationRace:
    """A token revoked while an ingest request is between its lookup and its cache write stays revoked (S-32)."""

    @pytest.mark.asyncio
    async def test_revocation_between_lookup_and_cache_write(
        self, client, write_token, auth_headers, monkeypatch, isolated_filters
    ):
        import sqlite3

        from app.api import deps as deps_mod
        from app.api.ingest import forget_ingest_token
        from app.core.config import get_db_path

        list_resp = await client.get("/api/tokens", headers=auth_headers)
        token_id = next(t["id"] for t in list_resp.json() if t["name"] == "Edge Shipper")

        original_run_db_query = deps_mod.run_db_query
        raced = {"done": False}

        async def racing_run_db_query(fn, *args, **kwargs):
            result = await original_run_db_query(fn, *args, **kwargs)
            if not raced["done"] and getattr(fn, "__name__", "") == "_lookup":
                raced["done"] = True
                # DELETE /api/tokens/{id} commits and calls forget_ingest_token after the
                # lookup has read the row, but before the request writes the token cache.
                conn = sqlite3.connect(str(get_db_path()))
                try:
                    conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
                    conn.commit()
                finally:
                    conn.close()
                forget_ingest_token(token_id)
            return result

        monkeypatch.setattr(deps_mod, "run_db_query", racing_run_db_query)
        headers = {"Authorization": f"Bearer {write_token}"}

        first = await client.post("/api/v1/ingest", json=[{"message": "in flight"}], headers=headers)
        assert raced["done"] is True
        assert first.status_code in (200, 401)

        second = await client.post("/api/v1/ingest", json=[{"message": "after revoke"}], headers=headers)
        assert second.status_code == 401

    def test_revoked_ids_expire_after_twice_the_cache_ttl(self, monkeypatch):
        from types import SimpleNamespace

        from app.api import ingest as ingest_mod

        clock = {"now": 1000.0}
        # Replace only the ingest module's clock, not the process-wide time.monotonic.
        monkeypatch.setattr(ingest_mod, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
        ingest_mod.forget_ingest_token(4242)
        assert ingest_mod.is_ingest_token_revoked(4242)
        clock["now"] += 2 * ingest_mod._TOKEN_CACHE_TTL - 1
        assert ingest_mod.is_ingest_token_revoked(4242)
        clock["now"] += 2
        assert not ingest_mod.is_ingest_token_revoked(4242)


@pytest.mark.usefixtures("isolated_filters")
class TestIngestBearerSchemeCase:
    """The Bearer scheme name is case-insensitive (RFC 7235) on the ingest endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scheme", ["bearer", "BEARER"])
    async def test_scheme_in_any_case_is_accepted(self, client: AsyncClient, write_token: str, scheme: str):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "case test"}],
            headers={"Authorization": f"{scheme} {write_token}", "X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_basic_scheme_is_refused(self, client: AsyncClient, write_token: str):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "case test"}],
            headers={"Authorization": f"Basic {write_token}", "X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
    async def test_revoked_token_is_refused_immediately_in_any_case(
        self, client: AsyncClient, write_token: str, auth_headers: dict, scheme: str
    ):
        headers = {"Authorization": f"{scheme} {write_token}", "X-Requested-With": "XMLHttpRequest"}
        assert (await client.post("/api/v1/ingest", json=[{"message": "a"}], headers=headers)).status_code == 200

        list_resp = await client.get("/api/tokens", headers=auth_headers)
        token_id = next(t["id"] for t in list_resp.json() if t["name"] == "Edge Shipper")
        assert (await client.delete(f"/api/tokens/{token_id}", headers=auth_headers)).status_code == 200

        assert (await client.post("/api/v1/ingest", json=[{"message": "b"}], headers=headers)).status_code == 401


def _store_through_consumer(entries: list[dict]) -> dict[str, dict]:
    """Write queued entries with the real QueueConsumer insert and read them back by message."""
    import sqlite3

    from app.core.config import get_db_path
    from app.core.pipeline import QueueConsumer

    consumer = QueueConsumer(get_db_path())
    try:
        consumer._insert_batch(entries)
    finally:
        consumer._close_conn()
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        return {r["message"]: dict(r) for r in conn.execute("SELECT * FROM logs")}
    finally:
        conn.close()


@pytest.mark.usefixtures("isolated_filters")
class TestIngestFutureTimestampWindow:
    """N03-1: an ingest timestamp up to 5 minutes ahead is stored as sent, through the queue consumer too."""

    @pytest.mark.asyncio
    async def test_five_minute_window_holds_end_to_end(self, client, write_token):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        inside = now + timedelta(minutes=4)
        outside = now + timedelta(minutes=6)
        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "inside", "timestamp": inside.isoformat()},
                {"message": "outside", "timestamp": outside.isoformat()},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        rows = _store_through_consumer(_queued_entries())
        assert datetime.fromisoformat(rows["inside"]["timestamp"]) == inside
        assert rows["outside"]["timestamp"] == rows["outside"]["received_at"]


class TestIngestEpochTimestamps:
    """N03-2: numeric Unix times (seconds or milliseconds) are accepted as entry timestamps."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            (1700000000, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
            (1700000000123, datetime(2023, 11, 14, 22, 13, 20, 123000, tzinfo=timezone.utc)),
            ("1700000000", datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
            ("1700000000123", datetime(2023, 11, 14, 22, 13, 20, 123000, tzinfo=timezone.utc)),
            (1700000000.5, datetime(2023, 11, 14, 22, 13, 20, 500000, tzinfo=timezone.utc)),
            ("2023-11-14T22:13:20Z", datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)),
        ],
    )
    def test_parse_entry_timestamp(self, value, expected):
        from app.api.ingest import _parse_entry_timestamp

        assert _parse_entry_timestamp(value) == expected

    @pytest.mark.parametrize(
        "value", [True, False, "12abc", "17000000001", float("nan"), float("inf"), 1e300, [1700000000], {"t": 1}]
    )
    def test_unusable_values_are_rejected(self, value):
        from app.api.ingest import _parse_entry_timestamp

        assert _parse_entry_timestamp(value) is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolated_filters")
    async def test_epoch_timestamps_are_stored(self, client, write_token):
        base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
        secs = int(base.timestamp())
        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "seconds", "timestamp": secs},
                {"message": "milliseconds", "ts": secs * 1000 + 250},
                {"message": "digit string", "time": str(secs)},
                {"message": "float", "timestamp": secs + 0.5},
                {"message": "boolean", "timestamp": True},
                {"message": "junk", "timestamp": "12abc"},
            ],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        by_msg = {e["message"]: e for e in _queued_entries()}
        assert datetime.fromisoformat(by_msg["seconds"]["timestamp"]) == base
        assert datetime.fromisoformat(by_msg["milliseconds"]["timestamp"]) == base + timedelta(milliseconds=250)
        assert datetime.fromisoformat(by_msg["digit string"]["timestamp"]) == base
        assert datetime.fromisoformat(by_msg["float"]["timestamp"]) == base + timedelta(milliseconds=500)
        assert by_msg["boolean"]["timestamp"] == by_msg["boolean"]["received_at"]
        assert by_msg["junk"]["timestamp"] == by_msg["junk"]["received_at"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("isolated_filters")
    async def test_epoch_outside_the_window_uses_received_time(self, client, write_token):
        future = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "old", "timestamp": 1700000000}, {"message": "ahead", "timestamp": future}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        for entry in _queued_entries():
            assert entry["timestamp"] == entry["received_at"]


@pytest.mark.usefixtures("isolated_filters")
class TestIngestFieldLengths:
    """N03-3: source_ip, source_alias and app_name are cut to 255 characters, and the entry is stored."""

    @pytest.mark.asyncio
    async def test_long_fields_are_cut_to_255_characters(self, client, write_token):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{
                "message": "long fields",
                "app_name": "a" * 1000,
                "source_ip": "  " + "i" * 1000,
                "source_alias": "s" * 1000,
            }],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 1
        (entry,) = _queued_entries()
        assert entry["app_name"] == "a" * 255
        assert entry["source_ip"] == "i" * 255
        assert entry["source_alias"] == "s" * 255

    @pytest.mark.asyncio
    async def test_default_alias_follows_the_cut_source_ip(self, client, write_token):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "m", "source_ip": "h" * 300, "app": "x" * 255}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        (entry,) = _queued_entries()
        assert entry["source_ip"] == "h" * 255
        assert entry["source_alias"] == "h" * 255
        assert entry["app_name"] == "x" * 255

    @pytest.mark.asyncio
    async def test_long_fields_are_stored_in_the_database(self, client, write_token):
        resp = await client.post(
            "/api/v1/ingest",
            json=[{"message": "stored", "app_name": "b" * 1000}],
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert resp.status_code == 200
        rows = _store_through_consumer(_queued_entries())
        assert rows["stored"]["app_name"] == "b" * 255
