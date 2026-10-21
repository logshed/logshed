"""
Unit and integration tests for LogShed API tokens:
Hashing, generation, verification, scoping, throttling, and revocation.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOKEN_SCOPES_TS = _REPO_ROOT / "frontend/src/components/settings/tokenScopes.ts"


def _ui_scope_presets() -> dict[str, list[str]]:
    """Preset name -> scopes, read from SCOPE_PRESETS in the web UI's tokenScopes.ts."""
    source = _TOKEN_SCOPES_TS.read_text(encoding="utf-8")
    block = source.split("export const SCOPE_PRESETS", 1)[1].split("\n];", 1)[0]
    names = re.findall(r"^    name: '([^']+)',$", block, re.MULTILINE)
    scope_lists = re.findall(r"^    scopes: \[([^\]]*)\],$", block, re.MULTILINE)
    assert names and len(names) == len(scope_lists)
    return {
        name: [item.strip().strip("'") if item.strip() != "FULL_ACCESS_SCOPE" else "*" for item in scopes.split(",")]
        for name, scopes in zip(names, scope_lists)
    }


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

        presets = _ui_scope_presets()
        assert len(presets) == 7
        for name, scopes in presets.items():
            assert validate_token_scopes(scopes) == scopes, name
        assert presets["Home Assistant"] == ["system:read", "maintenance:write", "system:write"]

    def test_ui_scope_catalog_matches_known_scopes(self):
        from app.core.api_tokens import KNOWN_SCOPES

        catalog = re.findall(r"scope: '([a-z]+:[a-z]+)'", _TOKEN_SCOPES_TS.read_text(encoding="utf-8"))
        assert sorted(catalog) == sorted(KNOWN_SCOPES)

    def test_scopes_guide_covers_every_scope_and_preset(self):
        from app.core.api_tokens import KNOWN_SCOPES

        guide = (_REPO_ROOT / "docs/API_SCOPES.md").read_text(encoding="utf-8")
        for scope in KNOWN_SCOPES:
            assert f"| `{scope}` |" in guide, scope
        for name in _ui_scope_presets():
            assert f"| **{name}** |" in guide, name

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


class TestTokenEdit:
    """PATCH /api/tokens/{id}: rename a token, change its scopes, or both."""

    @staticmethod
    async def _create(client: AsyncClient, auth_headers: dict, name: str, scopes: list[str]) -> dict:
        res = await client.post("/api/tokens", json={"name": name, "scopes": scopes}, headers=auth_headers)
        assert res.status_code == 200
        return res.json()

    @staticmethod
    def _row(tmp_path: Path, token_id: int) -> dict:
        conn = get_connection(tmp_path / "logs.db")
        try:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone())
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_scopes_change_is_stored_and_returned(self, client: AsyncClient, auth_headers: dict, tmp_path: Path):
        created = await self._create(client, auth_headers, "Vector", ["logs:read"])
        token_id = created["token"]["id"]

        res = await client.patch(f"/api/tokens/{token_id}", json={"scopes": ["logs:write"]}, headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == token_id
        assert body["name"] == "Vector"
        assert body["scopes"] == ["logs:write"]
        assert body["token_prefix"] == created["token"]["token_prefix"]
        assert body["created_at"] == created["token"]["created_at"]
        assert self._row(tmp_path, token_id)["scopes"] == '["logs:write"]'

    @pytest.mark.asyncio
    async def test_rename_only_keeps_scopes_secret_and_last_used(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        created = await self._create(client, auth_headers, "Claude Read-Only", ["logs:read", "system:read"])
        token_id = created["token"]["id"]
        bearer = {"Authorization": f"Bearer {created['raw_token']}"}
        assert (await client.get("/api/v1/auth/verify", headers=bearer)).status_code == 200
        await asyncio.sleep(0.05)
        before = self._row(tmp_path, token_id)
        assert before["last_used_at"] is not None

        res = await client.patch(
            f"/api/tokens/{token_id}", json={"name": "  Claude Maintenance  "}, headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["name"] == "Claude Maintenance"
        assert res.json()["scopes"] == ["logs:read", "system:read"]

        after = self._row(tmp_path, token_id)
        assert after["name"] == "Claude Maintenance"
        for column in ("scopes", "token_hash", "token_prefix", "last_used_at", "expires_at", "created_at", "created_by"):
            assert after[column] == before[column]

        # The same raw token still authenticates, now under its new name
        verify = await client.get("/api/v1/auth/verify", headers=bearer)
        assert verify.status_code == 200
        assert verify.json()["name"] == "Claude Maintenance"

    @pytest.mark.asyncio
    async def test_name_and_scopes_together(self, client: AsyncClient, auth_headers: dict):
        token_id = (await self._create(client, auth_headers, "Old", ["logs:read"]))["token"]["id"]
        res = await client.patch(
            f"/api/tokens/{token_id}",
            json={"name": "New", "scopes": ["logs:read", "maintenance:write"]},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["name"] == "New"
        assert res.json()["scopes"] == ["logs:read", "maintenance:write"]
        listed = (await client.get("/api/tokens", headers=auth_headers)).json()
        assert listed[0]["name"] == "New"
        assert listed[0]["scopes"] == ["logs:read", "maintenance:write"]

    @pytest.mark.asyncio
    async def test_custom_list_is_stored_as_sent(self, client: AsyncClient, auth_headers: dict):
        token_id = (await self._create(client, auth_headers, "Custom", ["logs:read"]))["token"]["id"]
        res = await client.patch(
            f"/api/tokens/{token_id}", json={"scopes": ["logs:*", "alerts:read"]}, headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["scopes"] == ["logs:*", "alerts:read"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"scopes": []},
            {"scopes": ["logs:delete"]},
            {"scopes": ["logs:read"] * 21},
            {"scopes": ["foo:*"]},
            {"name": "   "},
            {"name": "x" * 101},
            {},
            {"name": None},
            {"scopes": None},
            {"name": "ok", "scopes": None},
        ],
    )
    async def test_invalid_payloads_are_rejected(self, client: AsyncClient, auth_headers: dict, payload: dict):
        token_id = (await self._create(client, auth_headers, "Target", ["logs:read"]))["token"]["id"]
        res = await client.patch(f"/api/tokens/{token_id}", json=payload, headers=auth_headers)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_create_refuses_a_blank_name(self, client: AsyncClient, auth_headers: dict):
        res = await client.post("/api/tokens", json={"name": "   ", "scopes": ["logs:read"]}, headers=auth_headers)
        assert res.status_code == 422
        res = await client.post("/api/tokens", json={"name": "  Padded  ", "scopes": ["logs:read"]}, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["token"]["name"] == "Padded"

    @pytest.mark.asyncio
    async def test_missing_token_returns_404(self, client: AsyncClient, auth_headers: dict):
        res = await client.patch("/api/tokens/9999", json={"name": "Nobody"}, headers=auth_headers)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_needs_the_session_cookie(self, client: AsyncClient, auth_headers: dict):
        created = await self._create(client, auth_headers, "Full", ["*"])
        token_id = created["token"]["id"]

        res = await client.patch(f"/api/tokens/{token_id}", json={"name": "Anon"})
        assert res.status_code == 401
        # An API token cannot edit tokens, not even its own and not even with full access
        res = await client.patch(
            f"/api/tokens/{token_id}",
            json={"scopes": ["logs:read"]},
            headers={"Authorization": f"Bearer {created['raw_token']}"},
        )
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_needs_the_csrf_header(self, client: AsyncClient, auth_headers: dict):
        token_id = (await self._create(client, auth_headers, "Target", ["logs:read"]))["token"]["id"]
        cookie_only = {"Cookie": auth_headers["Cookie"]}
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as bare:
            res = await bare.patch(f"/api/tokens/{token_id}", json={"name": "Forged"}, headers=cookie_only)
        assert res.status_code == 403

    @pytest.mark.asyncio
    async def test_unchanged_payload_writes_nothing(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path, monkeypatch
    ):
        from unittest.mock import MagicMock

        from app.api import tokens as tokens_api

        created = await self._create(client, auth_headers, "Same", ["logs:read", "alerts:*"])
        token_id = created["token"]["id"]
        assert (
            await client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {created['raw_token']}"})
        ).status_code == 200
        await asyncio.sleep(0.05)
        before = self._row(tmp_path, token_id)

        refresh_ingest = MagicMock()
        refresh_mcp = MagicMock()
        monkeypatch.setattr(tokens_api, "refresh_ingest_token", refresh_ingest)
        monkeypatch.setattr(tokens_api, "refresh_mcp_sessions_for_token", refresh_mcp)

        # Duplicates are removed by validation, so this is the stored list
        res = await client.patch(
            f"/api/tokens/{token_id}",
            json={"name": " Same ", "scopes": ["logs:read", "alerts:*", "logs:read"]},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["name"] == "Same"
        assert res.json()["scopes"] == ["logs:read", "alerts:*"]
        assert self._row(tmp_path, token_id) == before
        refresh_ingest.assert_not_called()
        refresh_mcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_changes_refresh_caches_without_resetting_rate_limits(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        from unittest.mock import MagicMock

        from app.api import tokens as tokens_api

        created = await self._create(client, auth_headers, "Watcher", ["logs:read"])
        token_id = created["token"]["id"]
        assert (
            await client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {created['raw_token']}"})
        ).status_code == 200
        assert f"token_{token_id}" in api_token_rate_limiter
        assert token_id in _last_used_timestamps

        refresh_ingest = MagicMock()
        refresh_mcp = MagicMock()
        monkeypatch.setattr(tokens_api, "refresh_ingest_token", refresh_ingest)
        monkeypatch.setattr(tokens_api, "refresh_mcp_sessions_for_token", refresh_mcp)

        assert (await client.patch(f"/api/tokens/{token_id}", json={"name": "Renamed"}, headers=auth_headers)).status_code == 200
        refresh_ingest.assert_called_once_with(token_id)
        refresh_mcp.assert_called_once_with(token_id, None)

        assert (
            await client.patch(f"/api/tokens/{token_id}", json={"scopes": ["system:read"]}, headers=auth_headers)
        ).status_code == 200
        refresh_mcp.assert_called_with(token_id, ["system:read"])

        assert f"token_{token_id}" in api_token_rate_limiter
        assert token_id in _last_used_timestamps

    @pytest.mark.asyncio
    async def test_changes_are_logged_without_secrets(self, client: AsyncClient, auth_headers: dict, caplog):
        created = await self._create(client, auth_headers, "Vector", ["logs:read"])
        token_id = created["token"]["id"]
        with caplog.at_level("INFO", logger="app.api.tokens"):
            res = await client.patch(
                f"/api/tokens/{token_id}",
                json={"name": "Vector Shipper", "scopes": ["logs:write"]},
                headers=auth_headers,
            )
        assert res.status_code == 200
        assert f"API token {token_id} renamed from 'Vector' to 'Vector Shipper'" in caplog.text
        assert (
            f"API token {token_id} ('Vector Shipper') scopes changed from ['logs:read'] to ['logs:write']"
            in caplog.text
        )
        assert created["raw_token"] not in caplog.text
        assert hash_token(created["raw_token"]) not in caplog.text

    @pytest.mark.asyncio
    async def test_expired_token_can_be_edited_and_stays_expired(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        raw_token = "ls_live_expirededitsecret123456789"
        expired_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with get_connection(tmp_path / "logs.db") as conn:
            cur = conn.execute(
                """
                INSERT INTO api_tokens (name, token_hash, token_prefix, scopes, created_at, expires_at)
                VALUES ('Expired', ?, 'ls_live_expi...6789', '["logs:read"]', datetime('now', '-3 days'), ?)
                """,
                (hash_token(raw_token), expired_ts),
            )
            token_id = cur.lastrowid
            conn.commit()

        res = await client.patch(f"/api/tokens/{token_id}", json={"scopes": ["*"]}, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["expires_at"] == expired_ts
        verify = await client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {raw_token}"})
        assert verify.status_code == 401

    @pytest.mark.asyncio
    async def test_narrowing_applies_to_the_next_rest_request(self, client: AsyncClient, auth_headers: dict):
        created = await self._create(client, auth_headers, "Dashboard", ["logs:read"])
        token_id = created["token"]["id"]
        bearer = {"Authorization": f"Bearer {created['raw_token']}"}
        assert (await client.get("/api/v1/logs", headers=bearer)).status_code == 200

        res = await client.patch(f"/api/tokens/{token_id}", json={"scopes": ["logs:write"]}, headers=auth_headers)
        assert res.status_code == 200
        assert (await client.get("/api/v1/logs", headers=bearer)).status_code == 403

    @pytest.mark.asyncio
    async def test_rename_labels_later_rest_maintenance_sessions(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path, monkeypatch
    ):
        from app.services import maintenance_service

        # Starting a session updates the in-memory session list; keep that out of other tests
        monkeypatch.setattr(maintenance_service, "_active_maintenance_sessions", [])
        created = await self._create(client, auth_headers, "Old Runner", ["maintenance:write"])
        token_id = created["token"]["id"]
        bearer = {"Authorization": f"Bearer {created['raw_token']}"}

        res = await client.patch(f"/api/tokens/{token_id}", json={"name": "New Runner"}, headers=auth_headers)
        assert res.status_code == 200

        started = await client.post("/api/v1/maintenance/enable", json={"duration_minutes": 5}, headers=bearer)
        assert started.status_code == 200
        with get_connection(tmp_path / "logs.db") as conn:
            label = conn.execute(
                "SELECT trigger_label FROM maintenance_sessions WHERE session_id = ?",
                (started.json()["session_id"],),
            ).fetchone()[0]
        assert label == "API - New Runner"


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

    @pytest.mark.asyncio
    async def test_revoke_closes_the_tokens_mcp_sse_sessions(self, client: AsyncClient, auth_headers: dict):
        from uuid import uuid4

        import app.api.mcp as mcp_api

        revoked = (await client.post("/api/tokens", json={"name": "A", "scopes": ["logs:read"]}, headers=auth_headers)).json()
        kept = (await client.post("/api/tokens", json={"name": "B", "scopes": ["logs:read"]}, headers=auth_headers)).json()
        revoked_id, kept_id = revoked["token"]["id"], kept["token"]["id"]

        revoked_session = mcp_api._SseSession(token_id=revoked_id, session_id=uuid4())
        kept_session = mcp_api._SseSession(token_id=kept_id, session_id=uuid4())
        mcp_api._sse_sessions.clear()
        mcp_api._sse_token_cache.clear()
        mcp_api._sse_sessions[revoked_session.session_id] = revoked_session
        mcp_api._sse_sessions[kept_session.session_id] = kept_session
        mcp_api._sse_token_cache[revoked_id] = (0.0, {"id": revoked_id})
        mcp_api._sse_token_cache[kept_id] = (0.0, {"id": kept_id})
        try:
            assert (await client.delete(f"/api/tokens/{revoked_id}", headers=auth_headers)).status_code == 200

            assert revoked_session.closed is True
            assert revoked_session.session_id not in mcp_api._sse_sessions
            assert revoked_id not in mcp_api._sse_token_cache
            # Other tokens' sessions are untouched
            assert kept_session.closed is False
            assert mcp_api._sse_sessions[kept_session.session_id] is kept_session
            assert kept_id in mcp_api._sse_token_cache
        finally:
            mcp_api._sse_sessions.clear()
            mcp_api._sse_token_cache.clear()

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
