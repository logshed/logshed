"""
Tests for ending sessions: logout, a password change and reset-admin end every session in every
browser, and an open live stream closes once its session has ended.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import deps as deps_mod
from app.api import logs as logs_mod
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    get_fernet,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from tests.db_helpers import open_db

PASSWORD = "first_password_123"


@pytest.fixture(autouse=True)
def session_env(tmp_path: Path, monkeypatch):
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()
    deps_mod.invalidate_session_epoch_cache()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield db_file

    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()
    deps_mod.invalidate_session_epoch_cache()


def _new_client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


@pytest_asyncio.fixture
async def browsers():
    """Two browsers signed in to the same LogShed, as on a laptop and a phone."""
    async with _new_client() as first, _new_client() as second:
        assert (await first.post("/api/auth/setup", json={"password": PASSWORD})).status_code == 200
        assert (await second.post("/api/auth/login", json={"password": PASSWORD})).status_code == 200
        assert (await first.get("/api/settings")).status_code == 200
        assert (await second.get("/api/settings")).status_code == 200
        yield first, second


def _stored_epoch(db_file: Path) -> int:
    with open_db(db_file) as conn:
        row = conn.execute("SELECT value FROM system_settings WHERE key = 'session_epoch'").fetchone()
    return int(row[0]) if row else 0


class TestLogoutEndsEverySession:

    @pytest.mark.asyncio
    async def test_logout_in_one_browser_signs_out_the_other(self, browsers):
        first, second = browsers
        copied_cookie = first.cookies.get(SESSION_COOKIE_NAME)

        assert (await first.post("/api/auth/logout")).status_code == 200

        res = await second.get("/api/settings")
        assert res.status_code == 401
        assert "sign-out or a password change" in res.json()["detail"]
        assert (await second.get("/api/auth/status")).json()["authenticated"] is False

        # The cookie the first browser held stops working too, even when someone kept a copy
        async with _new_client() as thief:
            thief.cookies.set(SESSION_COOKIE_NAME, copied_cookie)
            assert (await thief.get("/api/settings")).status_code == 401

    @pytest.mark.asyncio
    async def test_signing_in_again_after_logout_works(self, browsers):
        first, second = browsers
        await first.post("/api/auth/logout")

        assert (await second.post("/api/auth/login", json={"password": PASSWORD})).status_code == 200
        assert (await second.get("/api/settings")).status_code == 200

    @pytest.mark.asyncio
    async def test_logout_without_a_valid_session_ends_nothing(self, browsers, session_env):
        _, second = browsers
        async with _new_client() as stranger:
            assert (await stranger.post("/api/auth/logout")).status_code == 200
            stranger.cookies.set(SESSION_COOKIE_NAME, "not-a-session")
            assert (await stranger.post("/api/auth/logout")).status_code == 200

        assert _stored_epoch(session_env) == 0
        assert (await second.get("/api/settings")).status_code == 200

    @pytest.mark.asyncio
    async def test_a_second_logout_with_the_ended_cookie_ends_nothing_more(self, browsers, session_env):
        first, _ = browsers
        cookie = first.cookies.get(SESSION_COOKIE_NAME)
        await first.post("/api/auth/logout")
        assert _stored_epoch(session_env) == 1

        async with _new_client() as later:
            assert (await later.post("/api/auth/login", json={"password": PASSWORD})).status_code == 200
            async with _new_client() as replay:
                replay.cookies.set(SESSION_COOKIE_NAME, cookie)
                await replay.post("/api/auth/logout")
            assert _stored_epoch(session_env) == 1
            assert (await later.get("/api/settings")).status_code == 200


class TestPasswordChangeEndsEverySession:

    @pytest.mark.asyncio
    async def test_password_change_signs_out_every_browser_at_once(self, browsers, session_env):
        first, second = browsers
        res = await first.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "second_password_456"},
        )
        assert res.status_code == 200

        assert (await second.get("/api/settings")).status_code == 401
        assert _stored_epoch(session_env) == 1

    @pytest.mark.asyncio
    async def test_a_refused_password_change_ends_nothing(self, browsers, session_env):
        first, second = browsers
        res = await first.post(
            "/api/auth/password",
            json={"current_password": "wrong_password_000", "new_password": "second_password_456"},
        )
        assert res.status_code == 400

        assert (await second.get("/api/settings")).status_code == 200
        assert _stored_epoch(session_env) == 0


class TestResetAdminEndsEverySession:

    @pytest.mark.asyncio
    async def test_reset_admin_signs_out_a_running_instance_once_its_cache_expires(self, browsers, session_env, monkeypatch):
        from app.cli import reset_admin

        _, second = browsers
        reset_admin("reset_password_789", str(session_env))
        assert _stored_epoch(session_env) == 1

        # reset-admin is another process: the running app reads the epoch again when its cache expires
        monkeypatch.setattr(deps_mod, "_SESSION_EPOCH_CACHE_TTL", 0.0)
        assert (await second.get("/api/settings")).status_code == 401


class TestTokenEpochClaim:

    def _token(self, **claims) -> str:
        now = time.time()
        payload = {"user_id": 1, "iat": now, "exp": now + 3600, "iss": "logshed", **claims}
        return get_fernet().encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")

    @pytest.mark.asyncio
    async def test_a_token_without_an_epoch_counts_as_epoch_zero(self, browsers):
        first, _ = browsers
        async with _new_client() as old:
            old.cookies.set(SESSION_COOKIE_NAME, self._token())
            assert (await old.get("/api/settings")).status_code == 200
            await first.post("/api/auth/logout")
            assert (await old.get("/api/settings")).status_code == 401

    @pytest.mark.asyncio
    async def test_an_unreadable_epoch_is_refused(self, browsers):
        async with _new_client() as odd:
            odd.cookies.set(SESSION_COOKIE_NAME, self._token(sv="later"))
            assert (await odd.get("/api/settings")).status_code == 401

    @pytest.mark.asyncio
    async def test_an_unreadable_stored_epoch_counts_as_zero(self, browsers, session_env):
        _, second = browsers
        with open_db(session_env) as conn:
            conn.execute(
                "INSERT INTO system_settings (key, value, updated_at, is_encrypted) "
                "VALUES ('session_epoch', 'junk', datetime('now'), 0)"
            )
            conn.commit()
        deps_mod.invalidate_session_epoch_cache()
        assert (await second.get("/api/settings")).status_code == 200


class TestLiveStreamEndsWithItsSession:

    @pytest.mark.asyncio
    async def test_open_stream_closes_after_a_logout_elsewhere(self, browsers, monkeypatch):
        first, second = browsers
        monkeypatch.setattr(logs_mod, "SSE_PING_INTERVAL_SECONDS", 0.05)
        monkeypatch.setattr(logs_mod, "SSE_SESSION_CHECK_SECONDS", 0.0)

        async def _logout_once_subscribed():
            for _ in range(200):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            await first.post("/api/auth/logout")

        task = asyncio.create_task(_logout_once_subscribed())

        async def _read_until_closed() -> None:
            async with second.stream("GET", "/api/logs/stream") as response:
                assert response.status_code == 200
                async for _ in response.aiter_lines():
                    pass

        await asyncio.wait_for(_read_until_closed(), timeout=5)
        await task
        assert sse_manager.subscriber_count() == 0

    @pytest.mark.asyncio
    async def test_open_stream_keeps_running_while_its_session_is_valid(self, browsers, monkeypatch):
        first, second = browsers
        monkeypatch.setattr(logs_mod, "SSE_PING_INTERVAL_SECONDS", 0.02)
        monkeypatch.setattr(logs_mod, "SSE_SESSION_CHECK_SECONDS", 0.0)
        still_open_after_checks = None

        async def _watch_then_logout():
            nonlocal still_open_after_checks
            for _ in range(200):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            # About fifteen pings, each followed by a session check, with the session still valid
            await asyncio.sleep(0.3)
            still_open_after_checks = sse_manager.subscriber_count() == 1
            await first.post("/api/auth/logout")

        task = asyncio.create_task(_watch_then_logout())

        async def _read_until_closed() -> None:
            async with second.stream("GET", "/api/logs/stream") as response:
                async for _ in response.aiter_lines():
                    pass

        await asyncio.wait_for(_read_until_closed(), timeout=5)
        await task
        assert still_open_after_checks is True
