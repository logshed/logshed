"""
Tests for Argon2id hashing, session cookies, login rate limiting, admin password change, and admin CLI.
"""

from pathlib import Path
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient

from app.cli import reset_admin
from app.core.config import get_cors_origins
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
    verify_session_token,
)
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_auth_env(tmp_path: Path, monkeypatch):
    """Reset rate limiter, crypto cache, and database paths for each test."""
    login_rate_limiter.reset()
    reset_crypto_cache()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    login_rate_limiter.reset()
    reset_crypto_cache()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


# ===================================================================
# 1. Authentication Endpoints & Session Management
# ===================================================================

class TestAuthentication:

    @pytest.mark.asyncio
    async def test_auth_setup_succeeds_once_and_locks_out(self, client: AsyncClient):
        # First setup should succeed
        res = await client.post("/api/auth/setup", json={"password": "initial_password_123"})
        assert res.status_code == 200
        assert res.json() == {"status": "ok", "detail": None}
        assert SESSION_COOKIE_NAME in res.cookies

        # Verify cookie attributes
        set_cookie = res.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()

        # Second setup attempt must return 403 Forbidden
        res2 = await client.post("/api/auth/setup", json={"password": "another_password_456"})
        assert res2.status_code == 403

    @pytest.mark.asyncio
    async def test_login_success_and_logout(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "valid_password_123"})

        # Successful login
        res = await client.post("/api/auth/login", json={"password": "valid_password_123"})
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        assert SESSION_COOKIE_NAME in res.cookies

        # Logout
        res_logout = await client.post("/api/auth/logout")
        assert res_logout.status_code == 200

    @pytest.mark.asyncio
    async def test_login_failed_password(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        res = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res.status_code == 401
        assert "Invalid password" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_rate_limiting_sliding_window(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        for _ in range(5):
            res = await client.post("/api/auth/login", json={"password": "wrong_password"})
            assert res.status_code == 401

        # 6th attempt must trigger 429 Too Many Requests
        res_blocked = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res_blocked.status_code == 429
        assert "Too many failed login attempts" in res_blocked.json()["detail"]

        # Even with correct password, blocked until window clears
        res_blocked_correct = await client.post("/api/auth/login", json={"password": "correct_password"})
        assert res_blocked_correct.status_code == 429

    @pytest.mark.asyncio
    async def test_login_rate_limiting_untrusted_spoofed_xff_does_not_bypass(self):
        """
        Verify that an attacker on an untrusted IP cycling X-Forwarded-For cannot
        bypass login rate limits. The server must use the peer IP and block on the 6th attempt.
        """
        app = create_app()
        # Untrusted origin connecting directly
        untrusted_transport = ASGITransport(app=app, client=("198.51.100.5", 50000))
        async with AsyncClient(
            transport=untrusted_transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as untrusted_client:
            await untrusted_client.post("/api/auth/setup", json={"password": "real_password"})

            # Attacker cycles different X-Forwarded-For headers to try to bypass rate limiting
            for i in range(5):
                res = await untrusted_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": f"10.0.0.{i+1}"},
                )
                assert res.status_code == 401

            # 6th attempt with yet another X-Forwarded-For must be blocked by rate limit
            res_blocked = await untrusted_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "10.0.0.99"},
            )
            assert res_blocked.status_code == 429
            assert "Too many failed login attempts" in res_blocked.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_rate_limiting_untrusted_spoofed_xff_does_not_lockout_victim(self):
        """
        Verify that an attacker on an untrusted IP spoofing an admin/victim IP in X-Forwarded-For
        does not lock out the victim.
        """
        app = create_app()
        untrusted_transport = ASGITransport(app=app, client=("198.51.100.5", 50000))
        victim_transport = ASGITransport(app=app, client=("203.0.113.50", 50000))

        async with AsyncClient(transport=untrusted_transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}) as attacker_client, \
                   AsyncClient(transport=victim_transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}) as victim_client:
            await attacker_client.post("/api/auth/setup", json={"password": "secure_admin_pass"})

            # Attacker sends failed attempts claiming to be the victim in X-Forwarded-For
            for _ in range(5):
                res = await attacker_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": "203.0.113.50"},
                )
                assert res.status_code == 401

            # Attacker should be rate limited on their own IP
            attacker_res = await attacker_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.50"},
            )
            assert attacker_res.status_code == 429

            # Victim logging in with their actual credentials must NOT be locked out
            victim_res = await victim_client.post(
                "/api/auth/login",
                json={"password": "secure_admin_pass"},
            )
            assert victim_res.status_code == 200

    @pytest.mark.asyncio
    async def test_login_trusted_proxies_parses_rightmost_untrusted_hop(self, monkeypatch):
        """
        When TRUSTED_PROXIES is configured, the server parses the rightmost untrusted hop
        from X-Forwarded-For to attribute rate limiting.
        """
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2")
        app = create_app()
        # Direct connection comes from trusted proxy 10.0.0.1
        proxy_transport = ASGITransport(app=app, client=("10.0.0.1", 50000))
        async with AsyncClient(
            transport=proxy_transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as proxy_client:
            await proxy_client.post("/api/auth/setup", json={"password": "proxy_password"})

            # XFF chain: [spoofed_ip, untrusted_client_ip, trusted_proxy_ip]
            # Rightmost untrusted hop is 203.0.113.88
            xff = "192.0.2.1, 203.0.113.88, 10.0.0.2"

            for _ in range(5):
                res = await proxy_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": xff},
                )
                assert res.status_code == 401

            # 6th attempt from same untrusted client IP is rate limited
            res_blocked = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": xff},
            )
            assert res_blocked.status_code == 429

            # A different client coming through the same proxy chain is NOT rate limited
            res_other = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.99, 10.0.0.2"},
            )
            assert res_other.status_code == 401

    @pytest.mark.asyncio
    async def test_login_docker_bridge_trusted_proxy_gateway(self, monkeypatch):
        """
        When TRUSTED_PROXIES is unset and TRUST_DOCKER_PROXIES is enabled,
        Docker bridge proxy gateways (e.g. 172.17.0.1 in 172.16.0.0/12) are trusted
        so they do not mask all incoming user traffic under one IP.
        """
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        monkeypatch.setenv("TRUST_DOCKER_PROXIES", "true")

        app = create_app()
        # Direct connection comes from Docker gateway 172.17.0.1
        proxy_transport = ASGITransport(app=app, client=("172.17.0.1", 50000))
        async with AsyncClient(
            transport=proxy_transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as proxy_client:
            await proxy_client.post("/api/auth/setup", json={"password": "gateway_password"})

            # Client 203.0.113.88 sends 5 failed attempts through 172.17.0.1
            for _ in range(5):
                res = await proxy_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": "203.0.113.88"},
                )
                assert res.status_code == 401

            # 6th attempt from 203.0.113.88 is rate-limited
            res_blocked = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.88"},
            )
            assert res_blocked.status_code == 429

            # A different client coming through the exact same gateway is NOT rate limited
            res_other = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.99"},
            )
            assert res_other.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_status_endpoint(self, client: AsyncClient, auth_cookie: dict):
        # Before setup
        res = await client.get("/api/auth/status")
        assert res.status_code == 200
        assert res.json() == {"setup_required": True, "authenticated": False}

        # After setup
        await client.post("/api/auth/setup", json={"password": "secure_password"})
        res2 = await client.get("/api/auth/status")
        assert res2.status_code == 200
        assert res2.json()["setup_required"] is False
        assert res2.json()["authenticated"] is True

        # Fresh client without cookie
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as clean_client:
            res3 = await clean_client.get("/api/auth/status")
            assert res3.json() == {"setup_required": False, "authenticated": False}

            clean_client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
            res4 = await clean_client.get("/api/auth/status")
            assert res4.json() == {"setup_required": False, "authenticated": True}

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_rejected_on_protected_endpoints(self, client: AsyncClient):
        endpoints = [
            ("GET", "/api/logs"),
            ("GET", "/api/logs/1/context"),
            ("GET", "/api/settings"),
            ("POST", "/api/settings"),
            ("GET", "/api/aliases"),
            ("POST", "/api/aliases"),
            ("POST", "/api/maintenance/prune"),
            ("GET", "/api/system/storage"),
        ]

        for method, endpoint in endpoints:
            if method == "GET":
                res = await client.get(endpoint)
            else:
                res = await client.post(endpoint, json={})
            assert res.status_code == 401, f"{method} {endpoint} did not return 401"

    @pytest.mark.asyncio
    async def test_cookie_secure_auto_detection_and_env_override(self, client: AsyncClient, monkeypatch):
        # Plain HTTP without proxy header
        res_http = await client.post("/api/auth/setup", json={"password": "secure_pwd_123"})
        assert res_http.status_code == 200
        set_cookie_http = res_http.headers.get("set-cookie", "").lower()
        cookie_parts_http = [p.strip() for p in set_cookie_http.split(";")]
        assert "secure" not in cookie_parts_http

        # Proxy with x-forwarded-proto: https -> secure=True
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as https_client:
            res_https = await https_client.post(
                "/api/auth/login",
                json={"password": "secure_pwd_123"},
                headers={"x-forwarded-proto": "https"},
            )
            assert res_https.status_code == 200
            set_cookie_https = res_https.headers.get("set-cookie", "").lower()
            cookie_parts_https = [p.strip() for p in set_cookie_https.split(";")]
            assert "secure" in cookie_parts_https

        # Untrusted peer IP sending x-forwarded-proto: https -> secure=False
        untrusted_transport = ASGITransport(app=app, client=("198.51.100.5", 50000))
        async with AsyncClient(
            transport=untrusted_transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as untrusted_client:
            res_untrusted = await untrusted_client.post(
                "/api/auth/login",
                json={"password": "secure_pwd_123"},
                headers={"x-forwarded-proto": "https"},
            )
            assert res_untrusted.status_code == 200
            set_cookie_untrusted = res_untrusted.headers.get("set-cookie", "").lower()
            assert "secure" not in [p.strip() for p in set_cookie_untrusted.split(";")]

        # COOKIE_SECURE environment variable override
        monkeypatch.setenv("COOKIE_SECURE", "true")
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as env_client:
            res_env = await env_client.post("/api/auth/login", json={"password": "secure_pwd_123"})
            assert res_env.status_code == 200
            set_cookie_env = res_env.headers.get("set-cookie", "").lower()
            cookie_parts_env = [p.strip() for p in set_cookie_env.split(";")]
            assert "secure" in cookie_parts_env


# ===================================================================
# 2. CORS Configuration
# ===================================================================

class TestCorsConfiguration:

    def test_default_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DEBUG", raising=False)
        assert get_cors_origins() == []

    def test_development_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        origins = get_cors_origins()
        assert "http://localhost:5173" in origins
        assert "http://localhost:8080" in origins

    def test_custom_cors_origins(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://192.168.1.50:8080, https://logs.example.com")
        origins = get_cors_origins()
        assert origins == ["http://192.168.1.50:8080", "https://logs.example.com"]


# ===================================================================
# 3. Authenticated Admin Password Change
# ===================================================================

class TestAdminPasswordChange:

    @pytest.mark.asyncio
    async def test_change_password_success_and_login_with_new_pwd(self, client: AsyncClient):
        # Setup initial admin
        await client.post("/api/auth/setup", json={"password": "SuperSecretAdminPassword123!"})

        # Change password while authenticated
        change_res = await client.post(
            "/api/auth/password",
            json={
                "current_password": "SuperSecretAdminPassword123!",
                "new_password": "BrandNewPassword987!",
            },
        )
        assert change_res.status_code == 200
        assert change_res.json()["status"] == "ok"

        # Try logging in with old password (must fail)
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as unauth_client:
            old_login = await unauth_client.post(
                "/api/auth/login",
                json={"password": "SuperSecretAdminPassword123!"},
            )
            assert old_login.status_code == 401

            # Try logging in with new password (must succeed)
            new_login = await unauth_client.post(
                "/api/auth/login",
                json={"password": "BrandNewPassword987!"},
            )
            assert new_login.status_code == 200
            assert SESSION_COOKIE_NAME in new_login.cookies

    @pytest.mark.asyncio
    async def test_session_issued_prior_to_password_change_rejected(self, client: AsyncClient):
        # 1. Setup admin
        await client.post("/api/auth/setup", json={"password": "InitialSecurePass123!"})
        initial_token = client.cookies.get(SESSION_COOKIE_NAME)
        assert initial_token is not None

        # Verify initial session token works on protected endpoint
        res_before = await client.get("/api/settings")
        assert res_before.status_code == 200

        # Wait briefly so that updated_at timestamp in admin_auth is strictly greater
        import asyncio
        await asyncio.sleep(1.05)

        # 2. Change password while authenticated
        change_res = await client.post(
            "/api/auth/password",
            json={
                "current_password": "InitialSecurePass123!",
                "new_password": "UpdatedPassword456!",
            },
        )
        assert change_res.status_code == 200

        # 3. Request using session token issued prior to password change must be rejected
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as old_client:
            old_client.cookies.set(SESSION_COOKIE_NAME, initial_token)
            res_after = await old_client.get("/api/settings")
            assert res_after.status_code == 401
            assert "Session expired due to password change" in res_after.json()["detail"]

            # Verify /api/auth/status returns authenticated=false after password change
            res_status = await old_client.get("/api/auth/status")
            assert res_status.status_code == 200
            assert res_status.json()["authenticated"] is False

        # 4. Request using a newly created session (logged in after password update) must succeed
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as new_client:
            login_res = await new_client.post(
                "/api/auth/login",
                json={"password": "UpdatedPassword456!"},
            )
            assert login_res.status_code == 200
            res_new_session = await new_client.get("/api/settings")
            assert res_new_session.status_code == 200

    @pytest.mark.asyncio
    async def test_session_tokens_created_in_same_second_as_password_change_properly_revoked(self, client: AsyncClient):
        """Tokens created in the same second before password change are properly revoked due to subsecond precision."""
        await client.post("/api/auth/setup", json={"password": "InitialSecurePass123!"})
        initial_token = client.cookies.get(SESSION_COOKIE_NAME)
        assert initial_token is not None

        # Verify initial session works
        res_before = await client.get("/api/settings")
        assert res_before.status_code == 200

        # Subsecond sleep to stay strictly within the same second
        import asyncio
        from app.api.deps import invalidate_admin_auth_cache
        invalidate_admin_auth_cache()
        await asyncio.sleep(0.05)

        # Change password while authenticated within the same second
        change_res = await client.post(
            "/api/auth/password",
            json={
                "current_password": "InitialSecurePass123!",
                "new_password": "UpdatedPassword456!",
            },
        )
        assert change_res.status_code == 200
        invalidate_admin_auth_cache()

        # Token issued earlier in that same second must now be rejected
        transport = ASGITransport(app=create_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as old_client:
            old_client.cookies.set(SESSION_COOKIE_NAME, initial_token)
            res_after = await old_client.get("/api/settings")
            assert res_after.status_code == 401
            assert "Session expired due to password change" in res_after.json()["detail"]


# ===================================================================
# 4. Admin CLI Password Reset
# ===================================================================

class TestCLIPasswordReset:

    def test_reset_admin_on_empty_table(self, tmp_path: Path):
        db_path = tmp_path / "cli_empty.db"
        run_migrations(db_path)
        reset_admin("newpassword123", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT id, password_hash, created_at, updated_at FROM admin_auth"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 1
        assert row[1].startswith("$argon2id$")

        ph = PasswordHasher()
        assert ph.verify(row[1], "newpassword123")

    def test_reset_admin_on_existing_row(self, tmp_path: Path):
        db_path = tmp_path / "cli_existing.db"
        run_migrations(db_path)

        ph = PasswordHasher()
        old_hash = ph.hash("oldpassword")
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
            "VALUES (1, ?, '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (old_hash,),
        )
        conn.commit()
        conn.close()

        reset_admin("newpassword456", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT password_hash, created_at, updated_at FROM admin_auth WHERE id=1"
        ).fetchone()
        conn.close()

        assert row is not None
        new_hash = row[0]
        assert new_hash.startswith("$argon2id$")
        with pytest.raises(Exception):
            ph.verify(new_hash, "oldpassword")
        assert ph.verify(new_hash, "newpassword456")
        assert row[1] == "2024-01-01T00:00:00"
        assert row[2] != "2024-01-01T00:00:00"

    def test_reset_admin_argon2id_algorithm(self, tmp_path: Path):
        db_path = tmp_path / "cli_algo.db"
        run_migrations(db_path)
        reset_admin("testpass", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT password_hash FROM admin_auth WHERE id=1"
        ).fetchone()
        conn.close()

        assert row is not None
        assert "$argon2id$" in row[0]

    def test_reset_admin_only_one_row(self, tmp_path: Path):
        db_path = tmp_path / "cli_onerow.db"
        run_migrations(db_path)
        reset_admin("pass1", str(db_path))
        reset_admin("pass2", str(db_path))
        reset_admin("pass3", str(db_path))

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM admin_auth").fetchone()[0]
        conn.close()

        assert count == 1

    @pytest.mark.asyncio
    async def test_password_max_length_enforced(self, client: AsyncClient):
        """Passwords exceeding 128 characters are rejected with 422 Unprocessable Entity."""
        too_long = "A" * 129

        # Setup
        res_setup = await client.post("/api/auth/setup", json={"password": too_long})
        assert res_setup.status_code == 422

        # Login
        res_login = await client.post("/api/auth/login", json={"password": too_long})
        assert res_login.status_code == 422

        # Setup valid password to authenticate for password change
        await client.post("/api/auth/setup", json={"password": "valid_initial_pwd"})

        # Password Change
        res_change = await client.post(
            "/api/auth/password",
            json={"current_password": "valid_initial_pwd", "new_password": too_long},
        )
        assert res_change.status_code == 422

    @pytest.mark.asyncio
    async def test_csrf_header_protection(self):
        """Mutating API requests without X-Requested-With return 403 Forbidden."""
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as no_header_client:
            # POST without header must fail with 403
            res_post = await no_header_client.post("/api/auth/login", json={"password": "any"})
            assert res_post.status_code == 403
            assert "X-Requested-With" in res_post.json()["detail"]

            # GET requests without header must succeed
            res_get = await no_header_client.get("/api/auth/status")
            assert res_get.status_code == 200

            # Healthcheck (GET) without header must succeed
            res_health = await no_header_client.get("/api/health")
            assert res_health.status_code == 200

        # POST with X-Requested-With header passes CSRF check
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as header_client:
            res_post = await header_client.post("/api/auth/login", json={"password": "wrong"})
            # Reaches route handler and returns 401 instead of 403
            assert res_post.status_code == 401

    def test_token_issuer_claim_validation(self):
        """Tokens without iss='logshed' or with invalid iss are rejected."""
        import json
        import time
        from app.core.security import get_fernet

        now = int(time.time())
        fernet = get_fernet()

        # Token with valid iss
        valid_payload = {"user_id": 1, "iat": now, "exp": now + 3600, "iss": "logshed"}
        valid_token = fernet.encrypt(json.dumps(valid_payload).encode()).decode()
        assert verify_session_token(valid_token) is not None

        # Token with missing iss
        missing_iss_payload = {"user_id": 1, "iat": now, "exp": now + 3600}
        missing_iss_token = fernet.encrypt(json.dumps(missing_iss_payload).encode()).decode()
        assert verify_session_token(missing_iss_token) is None

        # Token with wrong iss
        wrong_iss_payload = {"user_id": 1, "iat": now, "exp": now + 3600, "iss": "other_app"}
        wrong_iss_token = fernet.encrypt(json.dumps(wrong_iss_payload).encode()).decode()
        assert verify_session_token(wrong_iss_token) is None

    def test_cli_reset_admin_prompts_when_password_omitted(self, tmp_path: Path, monkeypatch):
        """CLI reset-admin prompts for password using getpass when --password is omitted."""
        import app.cli as cli_mod
        db_path = tmp_path / "cli_prompt.db"

        monkeypatch.setattr(
            "sys.argv",
            ["app.cli", "reset-admin", "--db-path", str(db_path)],
        )
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "PromptedSecretPassword123")

        cli_mod.main()

        conn = get_connection(str(db_path))
        row = conn.execute("SELECT password_hash FROM admin_auth WHERE id=1").fetchone()
        conn.close()

        assert row is not None
        ph = PasswordHasher()
        assert ph.verify(row[0], "PromptedSecretPassword123")




# ===================================================================
# Login limiter: concurrency, IPv6 buckets, Argon2 pool
# ===================================================================

def _slow_counting_verify(calls: list[str], result: bool = False, delay: float = 0.05):
    """Replacement for verify_password that records the calling thread and holds it briefly."""
    import threading
    import time as _time

    def _verify(_hash: str, _password: str) -> bool:
        calls.append(threading.current_thread().name)
        _time.sleep(delay)
        return result

    return _verify


class TestLoginLimiterHardening:

    @pytest.mark.asyncio
    async def test_concurrent_bad_logins_from_one_ip_cannot_exceed_limit(self, client: AsyncClient, monkeypatch):
        """20 concurrent bad guesses from one IP: at most 5 reach password verification."""
        import asyncio

        await client.post("/api/auth/setup", json={"password": "correct_password"})
        calls: list[str] = []
        monkeypatch.setattr("app.api.auth.verify_password", _slow_counting_verify(calls))

        responses = await asyncio.gather(
            *(client.post("/api/auth/login", json={"password": f"guess-{i}"}) for i in range(20))
        )

        assert len(calls) <= 5
        statuses = [r.status_code for r in responses]
        assert set(statuses) <= {401, 429}
        assert statuses.count(401) == len(calls)

        # Later sequential guesses from the same IP stay within the 5 per minute budget
        for i in range(10):
            await client.post("/api/auth/login", json={"password": f"late-{i}"})
        assert len(calls) <= 5

    @pytest.mark.asyncio
    async def test_login_in_flight_cap_per_ip(self, client: AsyncClient, monkeypatch):
        """While 2 attempts from one IP are being verified, a third is rejected without verifying."""
        import asyncio

        await client.post("/api/auth/setup", json={"password": "correct_password"})
        calls: list[str] = []
        monkeypatch.setattr("app.api.auth.verify_password", _slow_counting_verify(calls))

        responses = await asyncio.gather(
            *(client.post("/api/auth/login", json={"password": f"guess-{i}"}) for i in range(3))
        )

        assert len(calls) == 2
        assert sorted(r.status_code for r in responses) == [401, 401, 429]

    @pytest.mark.asyncio
    async def test_successful_logins_from_many_addresses_all_succeed(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        for i in range(40):
            res = await client.post(
                "/api/auth/login",
                json={"password": "correct_password"},
                headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
            )
            assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_ipv6_addresses_in_one_slash_64_share_a_bucket(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        for i in range(5):
            res = await client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": f"2001:db8:1:2::{i + 1:x}"},
            )
            assert res.status_code == 401

        # Another address in the same /64 is limited
        res_same_64 = await client.post(
            "/api/auth/login",
            json={"password": "wrong_password"},
            headers={"X-Forwarded-For": "2001:db8:1:2:ffff:ffff:ffff:1"},
        )
        assert res_same_64.status_code == 429

        # An address in an adjacent /64 has its own bucket
        res_other_64 = await client.post(
            "/api/auth/login",
            json={"password": "wrong_password"},
            headers={"X-Forwarded-For": "2001:db8:1:3::1"},
        )
        assert res_other_64.status_code == 401

    @pytest.mark.asyncio
    async def test_password_verification_runs_on_dedicated_executor(self, client: AsyncClient, monkeypatch):
        """Argon2 runs on its own small pool so a login burst cannot starve DB reads."""
        await client.post("/api/auth/setup", json={"password": "correct_password"})
        calls: list[str] = []
        monkeypatch.setattr("app.api.auth.verify_password", _slow_counting_verify(calls, delay=0.0))

        res = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res.status_code == 401
        assert len(calls) == 1
        assert calls[0].startswith("logshed-argon2")


class TestTrustDockerProxiesWarning:

    def test_warning_logged_once_when_trust_docker_proxies_in_effect(self, monkeypatch, caplog):
        import logging

        import app.api.auth as auth_mod
        from app.core.config import invalidate_settings_cache

        monkeypatch.setattr(auth_mod, "_docker_proxy_warning_logged", False)
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        monkeypatch.setenv("TRUST_DOCKER_PROXIES", "true")
        invalidate_settings_cache()

        with caplog.at_level(logging.WARNING, logger="app.api.auth"):
            networks = auth_mod._get_trusted_networks()
            auth_mod._get_trusted_networks()

        assert [str(n) for n in networks] == ["172.16.0.0/12"]
        warnings = [r for r in caplog.records if r.name == "app.api.auth" and r.levelno == logging.WARNING]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "trust_docker_proxies" in message
        assert "TRUSTED_PROXIES=" in message

    @pytest.mark.parametrize(
        "env",
        [
            {"TRUST_DOCKER_PROXIES": "false"},
            # An explicit TRUSTED_PROXIES list takes precedence, so the Docker range is not trusted
            {"TRUST_DOCKER_PROXIES": "true", "TRUSTED_PROXIES": "10.0.0.5"},
        ],
    )
    def test_no_warning_when_docker_range_not_trusted(self, monkeypatch, caplog, env):
        import logging

        import app.api.auth as auth_mod
        from app.core.config import invalidate_settings_cache

        monkeypatch.setattr(auth_mod, "_docker_proxy_warning_logged", False)
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        invalidate_settings_cache()

        with caplog.at_level(logging.WARNING, logger="app.api.auth"):
            auth_mod._get_trusted_networks()

        assert not [r for r in caplog.records if r.name == "app.api.auth"]

    @pytest.mark.asyncio
    async def test_warning_logged_at_app_startup(self, monkeypatch, caplog):
        """The auth router lifespan is merged into the app lifespan and logs the warning on startup."""
        import logging
        from contextlib import asynccontextmanager

        import app.api.auth as auth_mod
        import app.main as main_mod
        from app.core.config import invalidate_settings_cache

        @asynccontextmanager
        async def _no_workers_lifespan(_app):
            yield

        # Skip the real workers; only the merged router lifespans run
        monkeypatch.setattr(main_mod, "lifespan", _no_workers_lifespan)
        monkeypatch.setattr(auth_mod, "_docker_proxy_warning_logged", False)
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        monkeypatch.setenv("TRUST_DOCKER_PROXIES", "true")
        invalidate_settings_cache()

        app = create_app.uncached()
        with caplog.at_level(logging.WARNING, logger="app.api.auth"):
            async with app.router.lifespan_context(app):
                warnings = [r for r in caplog.records if r.name == "app.api.auth"]
                assert len(warnings) == 1
                assert "trust_docker_proxies" in warnings[0].getMessage()


# ===================================================================
# CORS and Bearer scheme hardening (S-09)
# ===================================================================

def _plain_client(app) -> AsyncClient:
    """Client without the X-Requested-With default header, like a cross-site form or fetch."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestCorsAndBearerHardening:

    def test_wildcard_cors_origin_is_dropped_with_error_log(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("CORS_ORIGINS", "*")
        with caplog.at_level(logging.ERROR, logger="app.core.config"):
            assert get_cors_origins() == []
        errors = [r for r in caplog.records if r.levelno == logging.ERROR and "CORS_ORIGINS" in r.getMessage()]
        assert len(errors) == 1

    def test_wildcard_is_dropped_but_explicit_origins_are_kept(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://logs.example.com, *")
        assert get_cors_origins() == ["https://logs.example.com"]

    def test_debug_alone_does_not_trust_development_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("DEBUG", "true")
        assert get_cors_origins() == []

    @pytest.mark.asyncio
    async def test_wildcard_cors_origin_does_not_echo_arbitrary_origins(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "*")
        app = create_app.uncached()
        async with _plain_client(app) as ac:
            res = await ac.get("/api/auth/status", headers={"Origin": "https://evil.example"})
            assert "access-control-allow-origin" not in res.headers
            pre = await ac.options(
                "/api/v1/maintenance/disable",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "x-requested-with",
                },
            )
            assert "access-control-allow-origin" not in pre.headers

    @pytest.mark.asyncio
    async def test_cors_preflight_only_allows_known_request_headers(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://logs.example.com")
        app = create_app.uncached()
        async with _plain_client(app) as ac:
            base = {"Origin": "https://logs.example.com", "Access-Control-Request-Method": "POST"}
            ok = await ac.options(
                "/api/v1/maintenance/disable",
                headers={**base, "Access-Control-Request-Headers": "content-type, x-requested-with, authorization"},
            )
            assert ok.status_code == 200
            assert ok.headers.get("access-control-allow-origin") == "https://logs.example.com"
            bad = await ac.options(
                "/api/v1/maintenance/disable",
                headers={**base, "Access-Control-Request-Headers": "x-custom-header"},
            )
            assert bad.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_value", ["bearer junk", "BEARER junk", "Basic YWRtaW46eA==", "Bearer"])
    async def test_authorization_header_never_falls_back_to_session_cookie(self, auth_cookie, auth_value):
        app = create_app()
        async with _plain_client(app) as ac:
            ac.cookies.update(auth_cookie)
            res = await ac.post(
                "/api/v1/maintenance/disable",
                headers={"Authorization": auth_value, "X-Requested-With": "XMLHttpRequest"},
            )
            assert res.status_code == 401
            status_res = await ac.get("/api/v1/maintenance/status", headers={"Authorization": auth_value})
            assert status_res.status_code == 401

    @pytest.mark.asyncio
    async def test_lowercase_bearer_junk_without_csrf_header_cannot_ride_the_cookie(self, auth_cookie):
        app = create_app()
        async with _plain_client(app) as ac:
            ac.cookies.update(auth_cookie)
            res = await ac.post("/api/v1/maintenance/disable", headers={"Authorization": "bearer junk"})
            assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_scheme_is_case_insensitive_for_valid_tokens(self, client: AsyncClient, auth_cookie):
        client.cookies.update(auth_cookie)
        created = await client.post("/api/tokens", json={"name": "Runner", "scopes": ["*"]})
        assert created.status_code in (200, 201)
        raw_token = created.json()["raw_token"]
        client.cookies.clear()

        async with _plain_client(create_app()) as ac:
            res = await ac.get("/api/v1/maintenance/status", headers={"Authorization": f"bearer {raw_token}"})
            assert res.status_code == 200
            assert "X-RateLimit-Limit" in res.headers

    @pytest.mark.asyncio
    async def test_session_cookie_still_works_without_authorization_header(self, auth_cookie):
        async with _plain_client(create_app()) as ac:
            ac.cookies.update(auth_cookie)
            res = await ac.get("/api/v1/maintenance/status")
            assert res.status_code == 200


class TestLoginHasNoGlobalFailureLimit:

    @pytest.mark.asyncio
    async def test_failures_from_many_addresses_do_not_trip_a_limit(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        # Loopback peer is a trusted proxy, so each X-Forwarded-For value is a distinct client
        for i in range(40):
            res = await client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": f"203.0.113.{i + 1}"},
            )
            assert res.status_code == 401

        res_new_ip = await client.post(
            "/api/auth/login",
            json={"password": "correct_password"},
            headers={"X-Forwarded-For": "198.51.100.200"},
        )
        assert res_new_ip.status_code == 200

    @pytest.mark.asyncio
    async def test_per_address_limit_still_applies(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        for _ in range(5):
            res = await client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.9"},
            )
            assert res.status_code == 401
        res = await client.post(
            "/api/auth/login",
            json={"password": "wrong_password"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert res.status_code == 429


class TestLoginRetryAfter:

    @pytest.mark.asyncio
    async def test_per_address_429_has_retry_after(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})
        for _ in range(5):
            await client.post("/api/auth/login", json={"password": "wrong_password"})

        res = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res.status_code == 429
        assert 1 <= int(res.headers["Retry-After"]) <= 60

    @pytest.mark.asyncio
    async def test_in_flight_429_has_retry_after_of_one_second(self, client: AsyncClient, monkeypatch):
        import asyncio

        await client.post("/api/auth/setup", json={"password": "correct_password"})
        calls: list[str] = []
        monkeypatch.setattr("app.api.auth.verify_password", _slow_counting_verify(calls))

        responses = await asyncio.gather(
            *(client.post("/api/auth/login", json={"password": f"guess-{i}"}) for i in range(3))
        )

        limited = [r for r in responses if r.status_code == 429]
        assert len(limited) == 1
        assert limited[0].headers["Retry-After"] == "1"


class TestArgon2ExecutorLifecycle:

    @pytest.mark.asyncio
    async def test_login_works_again_after_the_pool_is_shut_down(self, client: AsyncClient):
        from app.api import auth as auth_mod

        await client.post("/api/auth/setup", json={"password": "correct_password"})
        first = await client.post("/api/auth/login", json={"password": "correct_password"})
        assert first.status_code == 200
        pool_before = auth_mod._get_argon2_executor()

        auth_mod.shutdown_argon2_executor(wait=True)

        second = await client.post("/api/auth/login", json={"password": "correct_password"})
        assert second.status_code == 200
        assert auth_mod._get_argon2_executor() is not pool_before

    def test_shutdown_without_a_pool_is_a_no_op(self):
        from app.api import auth as auth_mod

        auth_mod.shutdown_argon2_executor()
        auth_mod.shutdown_argon2_executor()

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_stops_the_argon2_pool(self, monkeypatch):
        from unittest.mock import MagicMock

        from app.main import lifespan

        shutdown = MagicMock()
        monkeypatch.setattr("app.api.auth.shutdown_argon2_executor", shutdown)

        async with lifespan(create_app()):
            shutdown.assert_not_called()

        shutdown.assert_called_once()


class TestPasswordChangeUsesArgon2Pool:

    @pytest.mark.asyncio
    async def test_verify_and_hash_run_on_the_argon2_pool(self, client: AsyncClient, monkeypatch):
        import threading

        from app.api import auth as auth_mod

        await client.post("/api/auth/setup", json={"password": "InitialSecurePass123!"})

        verify_threads: list[str] = []
        hash_threads: list[str] = []
        real_verify = auth_mod.verify_password
        real_hash = auth_mod.hash_password

        def _verify(password_hash: str, password: str) -> bool:
            verify_threads.append(threading.current_thread().name)
            return real_verify(password_hash, password)

        def _hash(password: str) -> str:
            hash_threads.append(threading.current_thread().name)
            return real_hash(password)

        monkeypatch.setattr(auth_mod, "verify_password", _verify)
        monkeypatch.setattr(auth_mod, "hash_password", _hash)

        res = await client.post(
            "/api/auth/password",
            json={"current_password": "InitialSecurePass123!", "new_password": "BrandNewPassword987!"},
        )

        assert res.status_code == 200
        assert len(verify_threads) == 1 and verify_threads[0].startswith("logshed-argon2")
        assert len(hash_threads) == 1 and hash_threads[0].startswith("logshed-argon2")

        login = await client.post("/api/auth/login", json={"password": "BrandNewPassword987!"})
        assert login.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_current_password_is_rejected_without_hashing(self, client: AsyncClient, monkeypatch):
        from app.api import auth as auth_mod

        await client.post("/api/auth/setup", json={"password": "InitialSecurePass123!"})
        hashed: list[str] = []
        real_hash = auth_mod.hash_password
        monkeypatch.setattr(auth_mod, "hash_password", lambda p: hashed.append(p) or real_hash(p))

        res = await client.post(
            "/api/auth/password",
            json={"current_password": "wrong-password-123", "new_password": "BrandNewPassword987!"},
        )

        assert res.status_code == 400
        assert hashed == []
        assert (await client.post("/api/auth/login", json={"password": "InitialSecurePass123!"})).status_code == 200

    @pytest.mark.asyncio
    async def test_concurrent_change_is_not_overwritten(self, client: AsyncClient, monkeypatch):
        import os
        import sqlite3

        from app.api import auth as auth_mod
        from app.core.security import hash_password as real_hash

        await client.post("/api/auth/setup", json={"password": "InitialSecurePass123!"})
        other_hash = real_hash("ChangedByAnotherRequest1!")

        def _hash_and_race(password: str) -> str:
            conn = sqlite3.connect(os.environ["DB_PATH"])
            conn.execute("UPDATE admin_auth SET password_hash = ? WHERE id = 1", (other_hash,))
            conn.commit()
            conn.close()
            return real_hash(password)

        monkeypatch.setattr(auth_mod, "hash_password", _hash_and_race)

        res = await client.post(
            "/api/auth/password",
            json={"current_password": "InitialSecurePass123!", "new_password": "BrandNewPassword987!"},
        )

        assert res.status_code == 409
        monkeypatch.setattr(auth_mod, "hash_password", real_hash)
        assert (await client.post("/api/auth/login", json={"password": "ChangedByAnotherRequest1!"})).status_code == 200
