"""
Unit and integration tests for LogShed API tokens:
Hashing, generation, verification, scoping, throttling, and revocation.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import (
    api_token_rate_limiter,
    format_masked_identifier,
    generate_raw_token,
    hash_token,
    has_scope_permission,
    _last_used_timestamps,
)
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


class TestApiTokenUnit:
    """Unit tests for token primitives."""

    def test_token_generation_and_hashing(self):
        raw = generate_raw_token()
        assert raw.startswith("ls_live_")
        h1 = hash_token(raw)
        h2 = hash_token(raw)
        assert h1 == h2
        assert len(h1) == 64

    def test_masked_identifier_format(self):
        raw = "ls_live_0123456789abcdef01234567"
        masked = format_masked_identifier(raw)
        assert masked == "ls_live_0123...4567"

    def test_scope_permissions(self):
        # Wildcard grants everything
        assert has_scope_permission(["*"], ["system:read"]) is True
        assert has_scope_permission(["*"], ["maintenance:write"]) is True

        # Specific scopes
        scopes = ["maintenance:write", "maintenance:read"]
        assert has_scope_permission(scopes, ["maintenance:write"]) is True
        assert has_scope_permission(scopes, ["system:read"]) is False

        # Multi-scope requirements enforce all required scopes
        assert has_scope_permission(["logs:read"], ["logs:read", "maintenance:write"]) is False
        assert has_scope_permission(["logs:read", "maintenance:write"], ["logs:read", "maintenance:write"]) is True

        # Namespace wildcard supports category wildcards
        assert has_scope_permission(["maintenance:*"], ["maintenance:write"]) is True
        assert has_scope_permission(["maintenance:*"], ["maintenance:read"]) is True
        assert has_scope_permission(["maintenance:*"], ["system:read"]) is False

        # Empty requirement allows any token
        assert has_scope_permission(scopes, []) is True

    def test_rate_limiter_status_remaining_and_reset(self):
        """check_request reports remaining quota and the wait until the oldest request leaves the window."""
        from app.core.api_tokens import ApiTokenRateLimiter
        from app.core.rate_limiter import SlidingWindowLimiter

        now = [1000.0]
        limiter = ApiTokenRateLimiter(max_requests=3, window_seconds=60.0, clock=lambda: now[0])
        assert isinstance(limiter, SlidingWindowLimiter)

        first = limiter.check_request("token_1")
        assert (first.allowed, first.remaining, first.limit, first.reset_seconds) == (True, 2, 3, 60)
        now[0] += 10
        assert limiter.check_request("token_1").remaining == 1
        now[0] += 10
        assert limiter.check_request("token_1").remaining == 0

        limited = limiter.check_request("token_1")
        assert (limited.allowed, limited.remaining, limited.reset_seconds) == (False, 0, 40)
        assert limiter.count("token_1") == 3

        # The oldest request leaves the window after 60 seconds, freeing one slot
        now[0] += 40
        assert limiter.check_request("token_1").allowed is True
        assert limiter.record_request("token_1") is False

        # max_requests stays adjustable at runtime (tests and ingest tune it)
        limiter.max_requests = 10
        assert limiter.record_request("token_1") is True


class TestApiTokensApi:
    """Integration tests for token CRUD and external verification."""

    @pytest.mark.asyncio
    async def test_token_crud_lifecycle(self, client: AsyncClient, auth_headers: dict):
        # 1. Initially empty
        res = await client.get("/api/tokens", headers=auth_headers)
        assert res.status_code == 200
        assert res.json() == []

        # 2. Create token
        create_payload = {
            "name": "Home Assistant Automation",
            "scopes": ["maintenance:write", "maintenance:read"],
            "expires_days": 30,
        }
        res_create = await client.post("/api/tokens", json=create_payload, headers=auth_headers)
        assert res_create.status_code == 200
        data = res_create.json()
        assert "token" in data
        assert "raw_token" in data
        raw_token = data["raw_token"]
        assert raw_token.startswith("ls_live_")
        token_id = data["token"]["id"]
        assert data["token"]["name"] == "Home Assistant Automation"
        assert data["token"]["scopes"] == ["maintenance:write", "maintenance:read"]

        # 3. List contains newly created token
        res_list = await client.get("/api/tokens", headers=auth_headers)
        assert res_list.status_code == 200
        tokens_list = res_list.json()
        assert len(tokens_list) == 1
        assert tokens_list[0]["id"] == token_id
        # Secret is never returned in list
        assert "raw_token" not in tokens_list[0]

        # 4. Verify token via /api/v1/auth/verify
        res_verify = await client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["valid"] is True
        assert verify_data["name"] == "Home Assistant Automation"
        assert verify_data["scopes"] == ["maintenance:write", "maintenance:read"]

        # 5. Revoke token
        res_delete = await client.delete(f"/api/tokens/{token_id}", headers=auth_headers)
        assert res_delete.status_code == 200

        # 6. Verify now fails with 401
        res_verify_revoked = await client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res_verify_revoked.status_code == 401

    @pytest.mark.asyncio
    async def test_scope_enforcement_and_forbidden(self, client: AsyncClient, auth_headers: dict):
        # Create token with only maintenance:read scope
        res_create = await client.post(
            "/api/tokens",
            json={"name": "Read Only", "scopes": ["maintenance:read"]},
            headers=auth_headers,
        )
        assert res_create.status_code == 200
        raw_token = res_create.json()["raw_token"]
        bearer_headers = {"Authorization": f"Bearer {raw_token}"}

        # GET /api/v1/maintenance/status requires maintenance:read -> should succeed
        res_status = await client.get("/api/v1/maintenance/status", headers=bearer_headers)
        assert res_status.status_code == 200

        # POST /api/v1/maintenance/enable requires maintenance:write -> should return 403 Forbidden
        res_enable = await client.post(
            "/api/v1/maintenance/enable",
            json={"duration_minutes": 30},
            headers=bearer_headers,
        )
        assert res_enable.status_code == 403
        assert "Insufficient token scope" in res_enable.json()["detail"]

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, client: AsyncClient, auth_headers: dict, tmp_path: Path):
        # Insert directly an expired token into database
        db_path = tmp_path / "logs.db"
        expired_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        raw_token = "ls_live_expiredtestsecret123456789"
        thash = hash_token(raw_token)

        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO api_tokens (name, token_hash, token_prefix, scopes, created_at, expires_at)
                VALUES ('Expired Token', ?, 'ls_live_expi...6789', '["*"]', datetime('now', '-3 days'), ?)
                """,
                (thash, expired_ts),
            )
            conn.commit()

        res = await client.get(
            "/api/v1/auth/verify",
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        assert res.status_code == 401
        assert "expired" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_last_used_at_throttling(self, client: AsyncClient, auth_headers: dict, tmp_path: Path):
        res_create = await client.post(
            "/api/tokens",
            json={"name": "Throttled User", "scopes": ["*"]},
            headers=auth_headers,
        )
        raw_token = res_create.json()["raw_token"]
        token_id = res_create.json()["token"]["id"]
        db_path = tmp_path / "logs.db"

        # First request updates last_used_at
        res1 = await client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {raw_token}"})
        assert res1.status_code == 200
        # Allow async task brief execution window
        await asyncio.sleep(0.05)

        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_used_at FROM api_tokens WHERE id = ?", (token_id,))
            first_ts = cur.fetchone()[0]
        assert first_ts is not None

        # Immediate second request should be throttled (no database rewrite)
        res2 = await client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {raw_token}"})
        assert res2.status_code == 200
        await asyncio.sleep(0.05)

        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_used_at FROM api_tokens WHERE id = ?", (token_id,))
            second_ts = cur.fetchone()[0]
        assert first_ts == second_ts


class TestScopeEdgeCases:
    """Edge cases for has_scope_permission with malformed or degenerate scopes."""

    def test_bare_namespace_wildcard_grants_nothing(self):
        assert has_scope_permission([":*"], ["logs:read"]) is False

    def test_empty_string_scope_grants_nothing(self):
        assert has_scope_permission([""], ["logs:read"]) is False

    def test_trailing_colon_scope_grants_nothing(self):
        assert has_scope_permission(["logs:"], ["logs:read"]) is False

    def test_namespace_wildcard_does_not_cross_categories(self):
        assert has_scope_permission(["logs:*"], ["logsx:read"]) is False
        assert has_scope_permission(["logs:*"], ["logs:read", "alerts:read"]) is False

    def test_non_list_scopes_grant_nothing(self):
        assert has_scope_permission(None, ["logs:read"]) is False  # type: ignore[arg-type]
        assert has_scope_permission([None], ["logs:read"]) is False  # type: ignore[list-item]


class TestScopeValidation:
    """Scope allow-list enforcement on token creation."""

    def test_valid_scopes_accepted(self):
        from app.core.api_tokens import KNOWN_SCOPES, validate_token_scopes

        assert validate_token_scopes(sorted(KNOWN_SCOPES)) == sorted(KNOWN_SCOPES)
        assert validate_token_scopes(["*"]) == ["*"]
        assert validate_token_scopes(["logs:*", "system:read"]) == ["logs:*", "system:read"]

    def test_duplicates_removed_preserving_order(self):
        from app.core.api_tokens import validate_token_scopes

        assert validate_token_scopes(["logs:read", "alerts:read", "logs:read"]) == ["logs:read", "alerts:read"]

    @pytest.mark.parametrize("bad", ["logs:delete", "bogus:*", "logs:", ":*", "", "LOGS:READ", "logs:read ", "**"])
    def test_unknown_scope_rejected(self, bad):
        from app.core.api_tokens import validate_token_scopes

        with pytest.raises(ValueError):
            validate_token_scopes(["logs:read", bad])

    def test_empty_and_oversized_rejected(self):
        from app.core.api_tokens import MAX_TOKEN_SCOPES, validate_token_scopes

        with pytest.raises(ValueError):
            validate_token_scopes([])
        with pytest.raises(ValueError):
            validate_token_scopes(["logs:read"] * (MAX_TOKEN_SCOPES + 1))

    def test_ui_presets_match_backend_allow_list(self):
        from app.core.api_tokens import validate_token_scopes

        presets = [
            ["maintenance:write", "maintenance:read"],
            ["logs:write"],
            ["logs:read", "alerts:read", "system:read"],
            ["logs:read", "alerts:read", "system:read", "maintenance:write"],
            ["*"],
        ]
        for preset in presets:
            assert validate_token_scopes(preset) == preset

    @pytest.mark.asyncio
    async def test_create_token_endpoint_validates_scopes(self, client: AsyncClient, auth_headers: dict):
        for bad in ([], ["nope:read"], ["logs:read"] * 25):
            res = await client.post("/api/tokens", json={"name": "bad", "scopes": bad}, headers=auth_headers)
            assert res.status_code == 422

        res = await client.post(
            "/api/tokens",
            json={"name": "dupes", "scopes": ["logs:read", "logs:read", "alerts:*"]},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["token"]["scopes"] == ["logs:read", "alerts:*"]


class TestTokenRevocationCleanup:
    """Revoking a token purges its in-memory rate limit, throttle and ingest cache state."""

    @pytest.mark.asyncio
    async def test_revoke_purges_in_memory_state(self, client: AsyncClient, auth_headers: dict):
        from app.api.ingest import _token_cache, ingest_rate_limiter

        res = await client.post(
            "/api/tokens", json={"name": "Ephemeral", "scopes": ["logs:write", "system:read"]}, headers=auth_headers
        )
        token_id = res.json()["token"]["id"]
        bearer = {"Authorization": f"Bearer {res.json()['raw_token']}"}

        # Populate rate limiter, last_used throttle and ingest cache for this token
        assert (await client.get("/api/v1/auth/verify", headers=bearer)).status_code == 200
        assert (await client.post("/api/v1/ingest", json=[{"message": "x"}], headers=bearer)).status_code == 200
        key = f"token_{token_id}"
        assert key in api_token_rate_limiter
        assert key in ingest_rate_limiter
        assert token_id in _last_used_timestamps
        assert any(info["id"] == token_id for _, info in _token_cache.values())

        del_res = await client.delete(f"/api/tokens/{token_id}", headers=auth_headers)
        assert del_res.status_code == 200

        assert key not in api_token_rate_limiter
        assert key not in ingest_rate_limiter
        assert token_id not in _last_used_timestamps
        assert not any(info["id"] == token_id for _, info in _token_cache.values())

        # Revoked token is rejected immediately, including on the cached ingest path
        assert (await client.get("/api/v1/auth/verify", headers=bearer)).status_code == 401
        assert (await client.post("/api/v1/ingest", json=[{"message": "x"}], headers=bearer)).status_code == 401

    def test_forget_helpers(self):
        from app.core.api_tokens import ApiTokenRateLimiter, forget_last_used

        limiter = ApiTokenRateLimiter(max_requests=1, window_seconds=60.0)
        assert limiter.record_request("token_7") is True
        assert limiter.record_request("token_7") is False
        limiter.forget("token_7")
        limiter.forget("token_missing")
        assert limiter.record_request("token_7") is True

        _last_used_timestamps[7] = 1.0
        forget_last_used(7)
        forget_last_used(7)
        assert 7 not in _last_used_timestamps


class TestBackgroundLastUsedTask:
    """last_used_at fire-and-forget tasks are strongly referenced and failures are logged."""

    @pytest.mark.asyncio
    async def test_task_reference_retained_and_released(self):
        from app.api import deps

        before = len(deps._background_tasks)
        deps._schedule_last_used_update(987654)
        assert len(deps._background_tasks) == before + 1
        await asyncio.gather(*list(deps._background_tasks), return_exceptions=True)
        await asyncio.sleep(0)
        assert len(deps._background_tasks) == before

    @pytest.mark.asyncio
    async def test_task_exception_is_logged(self, caplog):
        from app.api import deps

        async def _boom():
            raise RuntimeError("db exploded")

        task = asyncio.create_task(_boom())
        deps._background_tasks.add(task)
        task.add_done_callback(deps._on_background_task_done)
        with caplog.at_level("WARNING", logger="app.api.deps"):
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        assert task not in deps._background_tasks
        assert "db exploded" in caplog.text
