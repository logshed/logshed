"""
Tests for Advanced System Settings:
- Three-tier resolution hierarchy (DB -> Env -> Default)
- Silent legacy aliases (TRUST_DOCKER_NETWORKS, app_url)
- In-memory settings cache and invalidation
- API endpoints (GET /api/settings, POST /api/settings)
- Strict validation rules (timeouts, budgets, URLs, CIDRs)
- Dynamic worker and subsystem dispatch (SyslogServer, DockerTailer, Notifier, AlertEvaluator, Auth)
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.pipeline import KeyedMultilineAssembler
from app.core.config import (
    invalidate_settings_cache,
    get_cached_setting,
    get_cached_system_settings,
    resolve_setting,
    resolve_all_system_settings,
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
from app.services.notifier import validate_notification_url
from app.services.alert_evaluator import AlertEvaluator
from app.collectors.syslog import SyslogServer
from app.collectors.docker_collector import DockerTailer


@pytest.fixture(autouse=True)
def reset_advanced_settings_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()
    invalidate_settings_cache()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    # Clear relevant env vars
    env_keys = [
        "LOGSHED_AI_TIMEOUT",
        "LOGSHED_AI_THINKING_BUDGET",
        "APP_URL",
        "app_url",
        "ALLOW_PRIVATE_NOTIFICATION_TARGETS",
        "ENABLE_DOCKER",
        "DOCKER_EXCLUDE_CONTAINERS",
        "DOCKER_SOURCE_ALIAS",
        "TRUSTED_PROXIES",
        "TRUST_DOCKER_PROXIES",
        "TRUST_DOCKER_NETWORKS",
        "TRUST_DOCKER_GATEWAY",
        "COOKIE_SECURE",
        "SYSLOG_MAX_TCP_CONNECTIONS",
        "SYSLOG_TCP_INACTIVITY_TIMEOUT",
    ]
    for k in env_keys:
        monkeypatch.delenv(k, raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    invalidate_settings_cache()


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
# 1. Three-Tier Resolution Hierarchy & Legacy Aliases
# ===================================================================

class TestThreeTierResolution:

    def test_tier_3_hardcoded_defaults(self):
        """When neither database nor environment variables are set, defaults return."""
        db_settings: dict[str, str] = {}
        assert resolve_setting(db_settings, "ai_timeout", "LOGSHED_AI_TIMEOUT", 45.0, float) == 45.0
        assert resolve_setting(db_settings, "ai_thinking_budget", "LOGSHED_AI_THINKING_BUDGET", 1024, int) == 1024
        assert resolve_setting(db_settings, "app_url", "APP_URL", "", str) == ""
        assert resolve_setting(db_settings, "allow_private_notification_targets", "ALLOW_PRIVATE_NOTIFICATION_TARGETS", True, lambda v: v.lower() in ("true", "1", "yes")) is True
        assert resolve_setting(db_settings, "docker_source_alias", "DOCKER_SOURCE_ALIAS", "docker", str) == "docker"
        assert resolve_setting(db_settings, "cookie_secure", "COOKIE_SECURE", False, lambda v: v.lower() in ("true", "1", "yes")) is False

    def test_tier_2_env_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch):
        """When environment variables are defined and DB is empty, env values take precedence."""
        monkeypatch.setenv("LOGSHED_AI_TIMEOUT", "60.0")
        monkeypatch.setenv("LOGSHED_AI_THINKING_BUDGET", "2048")
        monkeypatch.setenv("APP_URL", "https://logshed.example.com")
        monkeypatch.setenv("ALLOW_PRIVATE_NOTIFICATION_TARGETS", "false")
        monkeypatch.setenv("DOCKER_SOURCE_ALIAS", "dockernode")
        monkeypatch.setenv("COOKIE_SECURE", "true")

        db_settings: dict[str, str] = {}
        assert resolve_setting(db_settings, "ai_timeout", "LOGSHED_AI_TIMEOUT", 45.0, float) == 60.0
        assert resolve_setting(db_settings, "ai_thinking_budget", "LOGSHED_AI_THINKING_BUDGET", 1024, int) == 2048
        assert resolve_setting(db_settings, "app_url", "APP_URL", "", str) == "https://logshed.example.com"
        assert resolve_setting(db_settings, "allow_private_notification_targets", "ALLOW_PRIVATE_NOTIFICATION_TARGETS", True, lambda v: v.lower() in ("true", "1", "yes")) is False
        assert resolve_setting(db_settings, "docker_source_alias", "DOCKER_SOURCE_ALIAS", "docker", str) == "dockernode"
        assert resolve_setting(db_settings, "cookie_secure", "COOKIE_SECURE", False, lambda v: v.lower() in ("true", "1", "yes")) is True

    def test_tier_1_db_overrides_env_and_defaults(self, monkeypatch: pytest.MonkeyPatch):
        """Database settings take absolute precedence over environment variables."""
        monkeypatch.setenv("LOGSHED_AI_TIMEOUT", "60.0")
        monkeypatch.setenv("DOCKER_SOURCE_ALIAS", "from_env")

        db_settings = {
            "ai_timeout": "90.0",
            "docker_source_alias": "from_db",
        }
        assert resolve_setting(db_settings, "ai_timeout", "LOGSHED_AI_TIMEOUT", 45.0, float) == 90.0
        assert resolve_setting(db_settings, "docker_source_alias", "DOCKER_SOURCE_ALIAS", "docker", str) == "from_db"

    def test_silent_legacy_aliases(self, monkeypatch: pytest.MonkeyPatch):
        """Legacy aliases resolve when the primary variable is absent."""
        # 1. Reverse proxy alias: TRUST_DOCKER_NETWORKS
        monkeypatch.setenv("TRUST_DOCKER_NETWORKS", "true")
        db_settings: dict[str, str] = {}
        res = resolve_setting(
            db_settings,
            "trust_docker_proxies",
            "TRUST_DOCKER_PROXIES",
            False,
            lambda v: v.lower() in ("true", "1", "yes"),
            env_aliases=["TRUST_DOCKER_NETWORKS", "TRUST_DOCKER_GATEWAY"],
        )
        assert res is True

        # 2. Reverse proxy alias: TRUST_DOCKER_GATEWAY
        monkeypatch.delenv("TRUST_DOCKER_NETWORKS", raising=False)
        monkeypatch.setenv("TRUST_DOCKER_GATEWAY", "1")
        res2 = resolve_setting(
            db_settings,
            "trust_docker_proxies",
            "TRUST_DOCKER_PROXIES",
            False,
            lambda v: v.lower() in ("true", "1", "yes"),
            env_aliases=["TRUST_DOCKER_NETWORKS", "TRUST_DOCKER_GATEWAY"],
        )
        assert res2 is True

        # 3. Lowercase app_url alias
        monkeypatch.setenv("app_url", "https://alias.example.com")
        res_url = resolve_setting(
            db_settings,
            "app_url",
            "APP_URL",
            "",
            str,
            env_aliases=["app_url"],
        )
        assert res_url == "https://alias.example.com"

    def test_resolve_all_system_settings_dictionary(self, monkeypatch: pytest.MonkeyPatch):
        """resolve_all_system_settings returns all 12 resolved keys."""
        monkeypatch.setenv("LOGSHED_AI_TIMEOUT", "30.0")
        monkeypatch.setenv("SYSLOG_MAX_TCP_CONNECTIONS", "500")

        db_settings = {"docker_source_alias": "custom_docker"}
        resolved = resolve_all_system_settings(db_settings)

        assert resolved["ai_timeout"] == 30.0
        assert resolved["syslog_max_tcp_connections"] == 500
        assert resolved["docker_source_alias"] == "custom_docker"
        assert resolved["ai_thinking_budget"] == 1024
        assert resolved["app_url"] == ""
        assert resolved["allow_private_notification_targets"] is True
        assert resolved["enable_docker"] is True
        assert resolved["docker_exclude_containers"] == ""
        assert resolved["trusted_proxies"] == ""
        assert resolved["trust_docker_proxies"] is False
        assert resolved["cookie_secure"] is False
        assert resolved["syslog_tcp_inactivity_timeout"] == 0.0


# ===================================================================
# 2. In-Memory Cache & Invalidation
# ===================================================================

class TestSettingsCache:

    def test_cache_hits_and_invalidation(self, tmp_path: Path):
        """Cached settings return immediately and refresh upon invalidation."""
        db_file = tmp_path / "logs.db"

        # Initially default
        assert get_cached_setting("ai_timeout") == 45.0

        # Update SQLite table directly
        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('ai_timeout', '120.0', CURRENT_TIMESTAMP)"
            )
            conn.commit()

        # Cache still holds old value before invalidation
        assert get_cached_setting("ai_timeout") == 45.0

        # Invalidate cache
        invalidate_settings_cache()

        # Cache now yields new value
        assert get_cached_setting("ai_timeout") == 120.0

    def test_settings_cache_returns_cached_resolved_dict_without_recomputing(self):
        """Settings cache returns cached resolved dictionary without calling resolve_all_system_settings on each access."""
        from unittest.mock import patch
        import app.core.config as config_mod

        config_mod.invalidate_settings_cache()

        with patch("app.core.config.resolve_all_system_settings", wraps=config_mod.resolve_all_system_settings) as mock_resolve:
            # First access should compute and cache resolved dictionary
            settings_1 = config_mod.get_cached_system_settings()
            assert mock_resolve.call_count == 1
            assert isinstance(settings_1, dict)

            # Subsequent accesses within TTL should return cached dictionary without re-calling resolve_all_system_settings
            settings_2 = config_mod.get_cached_system_settings()
            assert mock_resolve.call_count == 1
            assert settings_2 == settings_1

            setting_val = config_mod.get_cached_setting("ai_timeout")
            assert mock_resolve.call_count == 1
            assert setting_val == 45.0

            # Invalidation forces re-resolution
            config_mod.invalidate_settings_cache()
            settings_3 = config_mod.get_cached_system_settings()
            assert mock_resolve.call_count == 2
            assert settings_3 == settings_1

    @pytest.mark.asyncio
    async def test_settings_includes_server_timezone(self, client: AsyncClient, auth_cookie: dict):
        """GET /api/settings includes container server_timezone, server_tz_name, and offset."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()
        assert "server_timezone" in data
        assert "server_tz_name" in data
        assert "server_tz_offset_minutes" in data
        assert isinstance(data["server_timezone"], str)
        assert isinstance(data["server_tz_offset_minutes"], int)



# ===================================================================
# 3. Settings API Endpoints
# ===================================================================

class TestSettingsApi:

    @pytest.mark.asyncio
    async def test_get_settings_contains_all_12_advanced_fields(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """GET /api/settings must return all 12 resolved advanced settings."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()

        expected_keys = [
            "ai_timeout",
            "ai_thinking_budget",
            "app_url",
            "allow_private_notification_targets",
            "enable_docker",
            "docker_exclude_containers",
            "docker_source_alias",
            "trusted_proxies",
            "trust_docker_proxies",
            "cookie_secure",
            "syslog_max_tcp_connections",
            "syslog_tcp_inactivity_timeout",
        ]
        for key in expected_keys:
            assert key in data, f"Key {key} missing from GET /api/settings response"

    @pytest.mark.asyncio
    async def test_post_settings_updates_advanced_settings(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """POST /api/settings persists advanced settings and invalidates cache."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        payload = {
            "ai_timeout": 65.5,
            "ai_thinking_budget": 512,
            "app_url": "https://logs.internal.net",
            "allow_private_notification_targets": False,
            "enable_docker": False,
            "docker_exclude_containers": "cadvisor,promtail",
            "docker_source_alias": "prod_docker",
            "trusted_proxies": "10.10.0.0/16, 172.20.0.1",
            "trust_docker_proxies": True,
            "cookie_secure": True,
            "syslog_max_tcp_connections": 100,
            "syslog_tcp_inactivity_timeout": 30.0,
        }

        res = await client.post("/api/settings", json=payload)
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Fetch settings via GET to verify returned values
        get_res = await client.get("/api/settings")
        assert get_res.status_code == 200
        data = get_res.json()
        assert data["ai_timeout"] == 65.5
        assert data["ai_thinking_budget"] == 512
        assert data["app_url"] == "https://logs.internal.net"
        assert data["allow_private_notification_targets"] is False
        assert data["enable_docker"] is False
        assert data["docker_exclude_containers"] == "cadvisor,promtail"
        assert data["docker_source_alias"] == "prod_docker"
        assert data["trusted_proxies"] == "10.10.0.0/16, 172.20.0.1"
        assert data["trust_docker_proxies"] is True
        assert data["cookie_secure"] is True
        assert data["syslog_max_tcp_connections"] == 100
        assert data["syslog_tcp_inactivity_timeout"] == 30.0

        # Verify persisted in SQLite
        with get_connection(db_file) as conn:
            rows = dict(conn.execute("SELECT key, value FROM system_settings").fetchall())
            assert rows["ai_timeout"] == "65.5"
            assert rows["syslog_max_tcp_connections"] == "100"
            assert rows["app_url"] == "https://logs.internal.net"

        # Verify cached settings updated immediately
        assert get_cached_setting("ai_timeout") == 65.5
        assert get_cached_setting("cookie_secure") is True

    @pytest.mark.asyncio
    async def test_post_settings_partial_update_only_alters_specified_keys(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """POST /api/settings with partial payload only updates specified keys."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        # Update only enable_docker
        res = await client.post("/api/settings", json={"enable_docker": False})
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Verify only enable_docker was written to SQLite system_settings
        with get_connection(db_file) as conn:
            rows = dict(conn.execute("SELECT key, value FROM system_settings").fetchall())
            assert "enable_docker" in rows
            assert rows["enable_docker"] == "0"
            assert "syslog_max_tcp_connections" not in rows
            assert "syslog_tcp_inactivity_timeout" not in rows

        # Verify cached setting updated
        assert get_cached_setting("enable_docker") is False
        assert get_cached_setting("syslog_max_tcp_connections") == 250

    @pytest.mark.asyncio
    async def test_validation_errors(self, client: AsyncClient, auth_cookie: dict):
        """Invalid inputs return 422 Unprocessable Entity."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # ai_timeout must be > 0
        res = await client.post("/api/settings", json={"ai_timeout": 0.0})
        assert res.status_code == 422

        res = await client.post("/api/settings", json={"ai_timeout": -5.0})
        assert res.status_code == 422

        # ai_thinking_budget must be >= 0
        res = await client.post("/api/settings", json={"ai_thinking_budget": -1})
        assert res.status_code == 422

        # syslog_max_tcp_connections must be >= 1
        res = await client.post("/api/settings", json={"syslog_max_tcp_connections": 0})
        assert res.status_code == 422

        # syslog_tcp_inactivity_timeout must be >= 0
        res = await client.post("/api/settings", json={"syslog_tcp_inactivity_timeout": -0.5})
        assert res.status_code == 422

        # app_url must be valid http or https
        res = await client.post("/api/settings", json={"app_url": "ftp://bad-url.com"})
        assert res.status_code == 422

        res = await client.post("/api/settings", json={"app_url": "not-a-valid-url"})
        assert res.status_code == 422

        # trusted_proxies must be valid IPs/CIDRs
        res = await client.post("/api/settings", json={"trusted_proxies": "999.999.999.999"})
        assert res.status_code == 422

        res = await client.post("/api/settings", json={"trusted_proxies": "192.168.1.0/35"})
        assert res.status_code == 422

        # docker_source_alias must be 1-64 chars
        res = await client.post("/api/settings", json={"docker_source_alias": ""})
        assert res.status_code == 422

        res = await client.post("/api/settings", json={"docker_source_alias": "a" * 65})
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_app_url_trailing_slash_normalization(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """app_url strips trailing slashes on save."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.post("/api/settings", json={"app_url": "https://logshed.local///"})
        assert res.status_code == 200
        get_res = await client.get("/api/settings")
        assert get_res.status_code == 200
        assert get_res.json()["app_url"] == "https://logshed.local"


# ===================================================================
# 4. Dynamic Subsystem Integrations
# ===================================================================

class TestSubsystemIntegrations:

    @pytest.mark.asyncio
    async def test_alert_evaluator_uses_app_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """AlertEvaluator uses cached app_url from system_settings to format action links."""
        import datetime
        db_file = tmp_path / "logs.db"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('app_url', 'https://logshed.corp.lan', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                """
                INSERT INTO alert_rules
                (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
                """,
                ("App URL Setting Rule", "threshold", "sshd", "Failed password", 1, 60, 300, now_iso),
            )
            conn.commit()
        invalidate_settings_cache()

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        async def mock_execute_ai_analysis(*args, **kwargs):
            return (
                "Incident diagnosis",
                "Take action",
                "prompt text",
                100,
                50,
                0,
                150,
                "gemini-2.5-flash",
                [],
            )

        import app.services.ai_engine as ai_engine
        monkeypatch.setattr(ai_engine, "execute_ai_analysis", mock_execute_ai_analysis)

        evaluator = AlertEvaluator(db_file)
        log_entry = {
            "id": 1,
            "app_name": "sshd",
            "message": "Failed password for admin from 10.0.0.1 port 22",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await evaluator.stop()

        assert len(sent_payloads) == 1
        assert "**Link:** https://logshed.corp.lan/rules/history" in sent_payloads[0]["body"]

    def test_notifier_private_target_validation(self, tmp_path: Path):
        """validate_notification_url respects allow_private_notification_targets setting."""
        db_file = tmp_path / "logs.db"

        # By default, private targets are allowed
        invalidate_settings_cache()
        valid, err = validate_notification_url("json://192.168.1.50:8000/hook")
        assert valid is True

        # Now set allow_private_notification_targets to False
        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('allow_private_notification_targets', 'false', CURRENT_TIMESTAMP)"
            )
            conn.commit()
        invalidate_settings_cache()

        # Should now be rejected
        valid2, err2 = validate_notification_url("json://192.168.1.50:8000/hook")
        assert valid2 is False
        assert "private" in (err2 or "").lower()

        # Public IP should still be accepted
        valid3, _ = validate_notification_url("discord://123456789/valid_candidate_token")
        assert valid3 is True

    @pytest.mark.asyncio
    async def test_auth_client_ip_and_proxy_trust(
        self, client: AsyncClient, tmp_path: Path
    ):
        """Auth client IP extraction trusts proxies configured via cached settings."""
        from app.api.auth import _get_client_ip

        # Mock request with X-Forwarded-For
        class MockRequest:
            def __init__(self, client_ip: str, forwarded_for: str):
                self.client = type("Client", (), {"host": client_ip})()
                self.headers = {"x-forwarded-for": forwarded_for}

        # Case 1: Untrusted proxy -> uses direct client IP
        invalidate_settings_cache()
        req1 = MockRequest(client_ip="198.51.100.5", forwarded_for="203.0.113.10")
        assert _get_client_ip(req1) == "198.51.100.5"

        # Case 2: Configure trusted proxy in settings
        db_file = tmp_path / "logs.db"
        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('trusted_proxies', '198.51.100.0/24', CURRENT_TIMESTAMP)"
            )
            conn.commit()
        invalidate_settings_cache()

        req2 = MockRequest(client_ip="198.51.100.5", forwarded_for="203.0.113.10")
        assert _get_client_ip(req2) == "203.0.113.10"

    def test_syslog_server_dynamic_limit_update(self, tmp_path: Path):
        """SyslogServer dynamically updates max connections and inactivity timeout."""
        server = SyslogServer(
            assembler=KeyedMultilineAssembler(),
            db_path=tmp_path / "logs.db",
            port=1514,
        )
        assert server.max_tcp_connections == 250
        assert server.tcp_inactivity_timeout == 0.0

        server.update_limits(max_connections=500, inactivity_timeout=15.0)
        assert server.max_tcp_connections == 500
        assert server.tcp_inactivity_timeout == 15.0

    @pytest.mark.asyncio
    async def test_docker_tailer_dynamic_settings_update(self, tmp_path: Path):
        """DockerTailer dynamically updates enable state, exclusions, and alias."""
        tailer = DockerTailer(
            assembler=KeyedMultilineAssembler(),
            db_path=tmp_path / "logs.db",
        )
        assert tailer.enable_docker is True
        assert tailer.source_alias == "docker"
        assert tailer.exclude_containers == ""

        await tailer.update_settings(
            enable_docker=False,
            exclude_containers="nginx,redis",
            source_alias="worker_node",
        )
        assert tailer.enable_docker is False
        assert tailer.source_alias == "worker_node"
        assert tailer.exclude_containers == "nginx,redis"

    def test_syslog_server_noop_update_skips_reset(self, tmp_path: Path):
        """SyslogServer skips protocol resets and logging if limits are unchanged."""
        server = SyslogServer(
            assembler=KeyedMultilineAssembler(),
            db_path=tmp_path / "logs.db",
            port=1514,
        )
        # Calling with existing limits should be a no-op
        server.update_limits(max_connections=250, inactivity_timeout=0.0)
        assert server.max_tcp_connections == 250
        assert server.tcp_inactivity_timeout == 0.0

        # Partial update with only max_connections
        server.update_limits(max_connections=400)
        assert server.max_tcp_connections == 400
        assert server.tcp_inactivity_timeout == 0.0

        # Partial update with only inactivity_timeout
        server.update_limits(inactivity_timeout=20.0)
        assert server.max_tcp_connections == 400
        assert server.tcp_inactivity_timeout == 20.0

    @pytest.mark.asyncio
    async def test_docker_tailer_noop_update(self, tmp_path: Path):
        """DockerTailer skips re-attaching and log output if settings are unchanged."""
        tailer = DockerTailer(
            assembler=KeyedMultilineAssembler(),
            db_path=tmp_path / "logs.db",
        )
        # Calling with existing values is a no-op
        await tailer.update_settings(
            enable_docker=True,
            exclude_containers="",
            source_alias="docker",
        )
        assert tailer.enable_docker is True
        assert tailer.exclude_containers == ""
        assert tailer.source_alias == "docker"


# ===================================================================
# 5. AI provider base URL / key handling, reload errors, read-only GET (B08)
# ===================================================================

def _stored_settings(tmp_path: Path) -> dict[str, str]:
    with get_connection(tmp_path / "logs.db") as conn:
        return dict(conn.execute("SELECT key, value FROM system_settings").fetchall())


class TestSettingsAiBaseUrlAndKey:

    @pytest.mark.asyncio
    async def test_per_provider_base_url_must_be_http_or_https(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """B-07: the per-provider base URL gets the same http/https validation as the top-level field."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.post(
            "/api/settings",
            json={"ai_providers_config": {"openai_compatible": {"ai_base_url": "ftp://x"}}},
        )
        assert res.status_code == 422
        res = await client.post(
            "/api/settings",
            json={"ai_providers_config": {"openai_compatible": {"ai_base_url": "not-a-url"}}},
        )
        assert res.status_code == 422
        res = await client.post(
            "/api/settings",
            json={"ai_providers_config": {"openai_compatible": {"ai_base_url": "http://ok:11434/v1"}}},
        )
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_base_url_clears_stored_value(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """B-07: an empty string clears the base URL at both the top level and per provider."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.post(
            "/api/settings",
            json={
                "ai_provider": "openai_compatible",
                "ai_base_url": "http://ollama:11434/v1",
                "ai_providers_config": {"openai_compatible": {"ai_base_url": "http://ollama:11434/v1"}},
            },
        )
        assert res.status_code == 200
        data = (await client.get("/api/settings")).json()
        assert data["ai_base_url"] == "http://ollama:11434/v1"

        res = await client.post(
            "/api/settings",
            json={
                "ai_base_url": "",
                "ai_providers_config": {"openai_compatible": {"ai_base_url": ""}},
            },
        )
        assert res.status_code == 200
        data = (await client.get("/api/settings")).json()
        assert not data["ai_base_url"]
        assert not data["ai_providers_config"]["openai_compatible"]["ai_base_url"]

    @pytest.mark.asyncio
    async def test_empty_per_provider_base_url_alone_clears_active_provider(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """B-07: clearing only through ai_providers_config also clears the legacy top-level value."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        await client.post(
            "/api/settings",
            json={"ai_provider": "openai_compatible", "ai_base_url": "http://ollama:11434/v1"},
        )
        res = await client.post(
            "/api/settings",
            json={"ai_providers_config": {"openai_compatible": {"ai_base_url": ""}}},
        )
        assert res.status_code == 200
        data = (await client.get("/api/settings")).json()
        assert not data["ai_base_url"]
        assert not data["ai_providers_config"]["openai_compatible"]["ai_base_url"]

    @pytest.mark.asyncio
    async def test_changing_base_url_with_masked_key_clears_stored_key(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """S-14: a new base URL without a new key must not keep sending the stored key to the new host."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.post(
            "/api/settings",
            json={
                "ai_provider": "openai",
                "ai_api_key": "sk-original-key-12345",
                "ai_base_url": "https://proxy.example.com/v1",
            },
        )
        assert res.status_code == 200
        assert res.json().get("ai_api_key_cleared") in (None, False)
        assert _stored_settings(tmp_path)["ai_api_key_openai"] != ""

        res = await client.post(
            "/api/settings",
            json={"ai_api_key": "********", "ai_base_url": "https://evil.example.net/v1"},
        )
        assert res.status_code == 200
        assert res.json()["ai_api_key_cleared"] is True
        stored = _stored_settings(tmp_path)
        assert stored["ai_api_key_openai"] == ""
        assert stored["ai_api_key"] == ""
        data = (await client.get("/api/settings")).json()
        assert data["has_ai_api_key"] is False
        assert data["ai_base_url"] == "https://evil.example.net/v1"

    @pytest.mark.asyncio
    async def test_changing_provider_config_base_url_with_masked_key_clears_that_provider_key(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """S-14: same rule through ai_providers_config, and other providers keep their keys."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        await client.post(
            "/api/settings",
            json={
                "ai_provider": "gemini",
                "ai_providers_config": {
                    "gemini": {"ai_api_key": "gem-key-12345"},
                    "openai": {"ai_api_key": "sk-openai-key", "ai_base_url": "https://a.example.com/v1"},
                },
            },
        )
        res = await client.post(
            "/api/settings",
            json={
                "ai_providers_config": {
                    "gemini": {"ai_api_key": "********"},
                    "openai": {"ai_api_key": "********", "ai_base_url": "https://b.example.com/v1"},
                },
            },
        )
        assert res.status_code == 200
        assert res.json()["ai_api_key_cleared"] is True
        stored = _stored_settings(tmp_path)
        assert stored["ai_api_key_openai"] == ""
        assert stored["ai_api_key_gemini"] != ""

    @pytest.mark.asyncio
    async def test_unchanged_base_url_or_new_key_keeps_stored_key(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """S-14: an unchanged base URL keeps the key; a changed one with a fresh key stores the fresh key."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        await client.post(
            "/api/settings",
            json={
                "ai_provider": "openai",
                "ai_api_key": "sk-original-key-12345",
                "ai_base_url": "https://proxy.example.com/v1",
            },
        )
        before = _stored_settings(tmp_path)["ai_api_key_openai"]

        res = await client.post(
            "/api/settings",
            json={"ai_api_key": "********", "ai_base_url": "https://proxy.example.com/v1/"},
        )
        assert res.status_code == 200
        assert res.json().get("ai_api_key_cleared") in (None, False)
        assert _stored_settings(tmp_path)["ai_api_key_openai"] == before

        res = await client.post(
            "/api/settings",
            json={"ai_api_key": "sk-brand-new-key-999", "ai_base_url": "https://other.example.com/v1"},
        )
        assert res.status_code == 200
        assert res.json().get("ai_api_key_cleared") in (None, False)
        stored = _stored_settings(tmp_path)
        assert stored["ai_api_key_openai"] not in ("", before)

    @pytest.mark.asyncio
    async def test_docker_source_alias_is_stored_without_surrounding_whitespace(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """B06 follow-up: the stored alias equals the stripped value the Docker backfill uses."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.post("/api/settings", json={"docker_source_alias": "  myalias  "})
        assert res.status_code == 200
        assert _stored_settings(tmp_path)["docker_source_alias"] == "myalias"
        with get_connection(tmp_path / "logs.db") as conn:
            row = conn.execute("SELECT source_alias FROM docker_endpoints WHERE id = 1").fetchone()
        assert row[0] == "myalias"
        assert (await client.get("/api/settings")).json()["docker_source_alias"] == "myalias"


class TestSettingsReloadErrorsAndReads:

    @pytest.mark.asyncio
    async def test_syslog_reload_error_is_logged(
        self, client: AsyncClient, auth_cookie: dict, caplog
    ):
        """B-62: a failing syslog limit update is logged with its traceback, not swallowed."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        server = MagicMock()
        server.update_limits.side_effect = RuntimeError("syslog limit boom")
        with patch("app.main.get_syslog_server", return_value=server), \
             caplog.at_level(logging.ERROR, logger="app.api.settings"):
            res = await client.post("/api/settings", json={"syslog_max_tcp_connections": 50})
        assert res.status_code == 200
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "syslog limit boom" in r.getMessage() + str(r.exc_text or r.exc_info)]
        assert errors, caplog.text

    @pytest.mark.asyncio
    async def test_docker_pool_reload_error_is_logged(
        self, client: AsyncClient, auth_cookie: dict, caplog
    ):
        """B-62: a failing Docker pool reload is logged with its traceback, not swallowed."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        pool = MagicMock()
        pool.set_primary_enabled = AsyncMock()
        pool.reload_endpoint = AsyncMock(side_effect=RuntimeError("pool reload boom"))
        with patch("app.main.get_docker_pool", return_value=pool), \
             caplog.at_level(logging.ERROR, logger="app.api.settings"):
            res = await client.post("/api/settings", json={"docker_exclude_containers": "a,b"})
        assert res.status_code == 200
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "pool reload boom" in r.getMessage() + str(r.exc_text or r.exc_info)]
        assert errors, caplog.text

    @pytest.mark.asyncio
    async def test_enabling_docker_starts_endpoint_one_only_once(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """B-62: enabling Docker records the local host switch and starts endpoint 1 exactly once."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        pool = MagicMock()
        pool.set_primary_enabled = AsyncMock()
        pool.reload_endpoint = AsyncMock()
        with patch("app.main.get_docker_pool", return_value=pool):
            res = await client.post("/api/settings", json={"enable_docker": True})
        assert res.status_code == 200
        pool.set_primary_enabled.assert_awaited_once_with(True)
        pool.reload_endpoint.assert_awaited_once()
        assert pool.reload_endpoint.await_args.args[0] == 1

    @pytest.mark.asyncio
    async def test_exclude_change_with_pool_already_enabled_still_reloads_endpoint_one(
        self, client: AsyncClient, auth_cookie: dict
    ):
        """B-62: a settings change that does not toggle Docker still reloads endpoint 1."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        pool = MagicMock()
        pool.set_primary_enabled = AsyncMock()
        pool.reload_endpoint = AsyncMock()
        with patch("app.main.get_docker_pool", return_value=pool):
            res = await client.post(
                "/api/settings", json={"enable_docker": True, "docker_exclude_containers": "x"}
            )
        assert res.status_code == 200
        pool.reload_endpoint.assert_awaited_once()
        assert pool.reload_endpoint.await_args.args[0] == 1

    @pytest.mark.asyncio
    async def test_get_settings_does_not_write_clamped_retention(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """B-78: GET clamps retention in the response only and never writes system_settings."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        with get_connection(tmp_path / "logs.db") as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('retention_days', '25', CURRENT_TIMESTAMP)"
            )
            conn.commit()
        monkeypatch.setenv("MAX_RETENTION_DAYS", "7")
        data = (await client.get("/api/settings")).json()
        assert data["retention_days"] == 7
        assert _stored_settings(tmp_path)["retention_days"] == "25"

        monkeypatch.setenv("MAX_RETENTION_DAYS", "60")
        data = (await client.get("/api/settings")).json()
        assert data["retention_days"] == 60
        assert _stored_settings(tmp_path)["retention_days"] == "25"
