"""
The whole journey of a log through the running app: the real lifespan starts every worker,
a syslog message arrives on a real UDP or TCP socket (or through POST /api/v1/ingest), the
QueueConsumer stores it, the FTSIndexWorker indexes it, GET /api/logs finds it by a search
term, and the live stream delivers it. Each stage has its own tests; this one catches a
broken hand-off between them (worker start order, queue hand-off, indexer wake-up).

Offline and fast: the syslog listener binds a free port on 127.0.0.1, Docker is off, and the
test waits on conditions, not fixed sleeps.
"""

import asyncio
import json
import socket
import time
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.ingest import clear_ingest_token_cache, ingest_rate_limiter
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
from app.main import create_app, lifespan

WAIT_SECONDS = 10.0


def _free_port() -> int:
    """A port number that is free for both TCP and UDP on 127.0.0.1."""
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                try:
                    udp.bind(("127.0.0.1", port))
                except OSError:
                    continue
        return port
    raise RuntimeError("no free port for both TCP and UDP")


@pytest.fixture
def journey_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    ingest_rate_limiter.reset()
    clear_ingest_token_cache()
    reset_crypto_cache()
    _last_used_timestamps.clear()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    port = _free_port()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.setenv("SYSLOG_PORT", str(port))
    monkeypatch.setenv("ENABLE_DOCKER", "false")
    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield port

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    ingest_rate_limiter.reset()
    clear_ingest_token_cache()
    _last_used_timestamps.clear()


@pytest_asyncio.fixture
async def running_app(journey_env):
    """The app with its lifespan running, an HTTP client, the syslog port and a live stream subscriber."""
    app = create_app.uncached()
    async with lifespan(app):
        await _wait_for_listener(journey_env)
        stream = await sse_manager.subscribe()
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
                client.headers["x-requested-with"] = "XMLHttpRequest"
                yield client, journey_env, stream
        finally:
            await sse_manager.unsubscribe(stream)


async def _wait_for_listener(port: int) -> None:
    """The SyslogServer starts in a supervised task; wait until its TCP listener accepts."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.02)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise AssertionError(f"syslog listener did not start on port {port}")


async def _search(client: AsyncClient, term: str) -> list[dict]:
    """Wait until GET /api/logs finds the term (stored and indexed), then return the matching logs."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        res = await client.get("/api/logs", params={"query": term})
        assert res.status_code == 200
        logs = res.json()["logs"]
        if logs:
            return logs
        await asyncio.sleep(0.05)
    raise AssertionError(f"no log matching {term!r} became searchable within {WAIT_SECONDS}s")


async def _streamed(stream: asyncio.Queue, term: str) -> dict:
    """Wait until the live stream delivers a log whose message holds the term."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            event = await asyncio.wait_for(stream.get(), timeout=max(deadline - time.monotonic(), 0.01))
        except asyncio.TimeoutError:
            break
        entry = json.loads(event.data)
        if term in entry.get("message", ""):
            return entry
    raise AssertionError(f"the live stream did not deliver a log matching {term!r}")


class TestLogJourney:

    async def test_udp_syslog_message_is_stored_searchable_and_streamed(self, running_app):
        client, port, stream = running_app
        term = "journeyudpalpha"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(f"<11>Jan 15 10:00:00 nas01 backupd[42]: {term} snapshot failed".encode(), ("127.0.0.1", port))

        logs = await _search(client, term)
        assert len(logs) == 1
        assert logs[0]["app_name"] == "backupd"
        assert logs[0]["severity"] == 3
        assert logs[0]["message"] == f"{term} snapshot failed"
        streamed = await _streamed(stream, term)
        assert streamed["app_name"] == "backupd"

    async def test_tcp_syslog_message_is_stored_searchable_and_streamed(self, running_app):
        client, port, stream = running_app
        term = "journeytcpbravo"
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"<14>Jan 15 10:00:01 router01 dnsmasq[7]: {term} lease renewed\n".encode())
        await writer.drain()

        logs = await _search(client, term)
        writer.close()
        await writer.wait_closed()
        assert [log["app_name"] for log in logs] == ["dnsmasq"]
        assert logs[0]["message"] == f"{term} lease renewed"
        streamed = await _streamed(stream, term)
        assert streamed["severity"] == 6

    async def test_ingested_log_is_stored_searchable_and_streamed(self, running_app):
        client, _, stream = running_app
        term = "journeyingestcharlie"
        res = await client.post("/api/tokens", json={"name": "Shipper", "scopes": ["logs:write"]})
        assert res.status_code == 200
        token = res.json()["raw_token"]

        res = await client.post(
            "/api/v1/ingest",
            json={"message": f"{term} job finished", "app_name": "cron-runner", "severity": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code in (200, 202)

        logs = await _search(client, term)
        assert [log["app_name"] for log in logs] == ["cron-runner"]
        assert logs[0]["severity"] == 5
        streamed = await _streamed(stream, term)
        assert streamed["message"] == f"{term} job finished"
