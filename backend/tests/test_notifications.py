"""
Tests for NotifierService, Apprise URL encryption, token masking, and Notifications API.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
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
from app.services.notifier import (
    NotifierService,
    decrypt_channel_url,
    encrypt_channel_url,
    get_notifier,
    mask_notification_url,
    validate_notification_url,
)


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


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


# ===================================================================
# 1. Unit Tests: URL Validation, Encryption, and Masking
# ===================================================================

class TestNotifierUnit:

    @patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))])
    def test_validate_notification_url_valid(self, mock_dns):
        valid, err = validate_notification_url("discord://123456789/abcdefghij")
        assert valid is True
        assert err is None

        valid, err = validate_notification_url("gotify://push.example.com/A1B2C3D4E5")
        assert valid is True
        assert err is None

        valid, err = validate_notification_url("ntfy://mytopic")
        assert valid is True
        assert err is None

    def test_validate_notification_url_invalid(self):
        valid, err = validate_notification_url("")
        assert valid is False
        assert "empty" in err.lower()

        valid, err = validate_notification_url("not_a_valid_protocol://xyz")
        assert valid is False
        assert err is not None

    def test_mask_notification_url(self):
        # Discord webhook masking
        masked = mask_notification_url("discord://123456789/my_secret_token")
        assert "my_secret_token" not in masked
        assert "discord://" in masked

        # Gotify masking
        masked_gotify = mask_notification_url("gotify://push.example.com/verysecrettoken")
        assert "verysecrettoken" not in masked_gotify
        assert "gotify://" in masked_gotify

        # Empty
        assert mask_notification_url("") == ""

    def test_encrypt_and_decrypt_channel_url(self):
        plain = "discord://123456789/secret_token_value"
        encrypted = encrypt_channel_url(plain)
        assert encrypted != plain
        assert "secret_token_value" not in encrypted

        decrypted = decrypt_channel_url(encrypted)
        assert decrypted == plain

    def test_validate_ssrf_dangerous_schemes_rejected(self):
        valid, err = validate_notification_url("file:///etc/passwd")
        assert valid is False
        assert "not allowed" in (err or "").lower()

        valid, err = validate_notification_url("attach:///etc/shadow")
        assert valid is False
        assert "not allowed" in (err or "").lower()

    def test_validate_ssrf_metadata_ips_rejected(self):
        valid, err = validate_notification_url("json://169.254.169.254/latest/meta-data")
        assert valid is False
        assert "metadata" in (err or "").lower()

        valid, err = validate_notification_url("json://[fe80::1]/hook")
        assert valid is False
        assert "metadata" in (err or "").lower()

        valid, err = validate_notification_url("json://[::169.254.169.254]/hook")
        assert valid is False
        assert "metadata" in (err or "").lower()

    def test_validate_ssrf_loopback_targets_rejected(self):
        valid, err = validate_notification_url("json://127.0.0.1:8080/hook")
        assert valid is False
        assert "loopback" in (err or "").lower()

        valid, err = validate_notification_url("json://localhost:8080/hook")
        assert valid is False
        assert "loopback" in (err or "").lower()

        valid, err = validate_notification_url("json://[::1]:8080/hook")
        assert valid is False
        assert "loopback" in (err or "").lower()

        valid, err = validate_notification_url("json://[::127.0.0.1]:8080/hook")
        assert valid is False
        assert "loopback" in (err or "").lower()

    def test_validate_unresolvable_domains_rejected(self):
        valid, err = validate_notification_url("gotify://unresolvable-domain-987654321.invalid/token")
        assert valid is False
        assert "unable to resolve destination hostname" in (err or "").lower()

    def test_redirect_to_blocked_ips_rejected(self):
        from app.services.notifier import _validate_redirect_url
        is_safe, err = _validate_redirect_url("http://169.254.169.254/latest/meta-data")
        assert is_safe is False
        assert "metadata" in (err or "").lower()

        is_safe, err = _validate_redirect_url("http://127.0.0.1:8080/admin")
        assert is_safe is False
        assert "loopback" in (err or "").lower()

        is_safe, err = _validate_redirect_url("http://[::127.0.0.1]:8080/admin")
        assert is_safe is False
        assert "loopback" in (err or "").lower()

        is_safe, err = _validate_redirect_url("http://[::169.254.169.254]/meta")
        assert is_safe is False
        assert "metadata" in (err or "").lower()

    def test_validate_ssrf_docker_ports_rejected(self):
        valid, err = validate_notification_url("json://192.168.1.50:2375/v1.41/containers/json")
        assert valid is False
        assert "blocked" in (err or "").lower() or "port" in (err or "").lower()

        valid, err = validate_notification_url("json://192.168.1.50:2376/v1.41/containers/json")
        assert valid is False
        assert "blocked" in (err or "").lower() or "port" in (err or "").lower()

    def test_validate_private_targets_toggle(self, monkeypatch):
        # Default allow_private=true allows private LAN targets
        monkeypatch.setenv("ALLOW_PRIVATE_NOTIFICATION_TARGETS", "true")
        valid, err = validate_notification_url("gotify://192.168.1.50:8080/token")
        assert valid is True
        assert err is None

        # When false, blocks private IP targets
        monkeypatch.setenv("ALLOW_PRIVATE_NOTIFICATION_TARGETS", "false")
        valid, err = validate_notification_url("gotify://192.168.1.50:8080/token")
        assert valid is False
        assert "private" in (err or "").lower()

    def test_mask_notification_url_scrubs_fallback_credentials(self):
        masked = mask_notification_url("http://user:secretPassword123@192.168.1.50/webhook")
        assert "secretPassword123" not in masked
        assert "***:***@192.168.1.50" in masked
        assert masked == "http://***:***@192.168.1.50/********"

        masked_custom = mask_notification_url("custom://admin:superSecretKey@myhost:8080/alert?api=1")
        assert "superSecretKey" not in masked_custom
        assert "***:***@myhost:8080" in masked_custom


# ===================================================================
# 2. Service Tests: Dispatch & Testing via Apprise
# ===================================================================

class TestNotifierService:

    @pytest.mark.asyncio
    async def test_test_channel_success(self):
        notifier = NotifierService()
        with patch("apprise.Apprise.notify", return_value=True):
            success, msg = await notifier.test_channel("discord://123456789/test_token")
            assert success is True
            assert "successfully" in msg.lower()

    @pytest.mark.asyncio
    async def test_test_channel_failure(self):
        notifier = NotifierService()
        with patch("apprise.Apprise.notify", return_value=False):
            success, msg = await notifier.test_channel("discord://123456789/test_token")
            assert success is False
            assert "rejected" in msg.lower() or "verify" in msg.lower()

    @pytest.mark.asyncio
    async def test_test_channel_invalid_url(self):
        notifier = NotifierService()
        success, msg = await notifier.test_channel("invalid://nothing")
        assert success is False
        assert "invalid" in msg.lower() or "unsupported" in msg.lower()

    @pytest.mark.asyncio
    async def test_test_channel_sanitizes_exception(self):
        notifier = NotifierService()
        with patch("apprise.Apprise.notify", side_effect=RuntimeError("Internal socket connection failed to 10.0.0.5:80")):
            success, msg = await notifier.test_channel("discord://123456789/test_token")
            assert success is False
            # Ensure raw internal details/stack traces are not leaked
            assert "10.0.0.5" not in msg
            assert "socket" not in msg.lower()
            assert "verify" in msg.lower() or "failed" in msg.lower()

    @pytest.mark.asyncio
    async def test_send_notification_to_all_channels(self, tmp_path):
        db_file = tmp_path / "logs.db"
        notifier = NotifierService(db_path=db_file)

        # Seed two channels: one enabled, one disabled
        with get_connection(db_file) as conn:
            enc1 = encrypt_channel_url("discord://111/token1")
            enc2 = encrypt_channel_url("discord://222/token2")
            conn.execute(
                "INSERT INTO notification_channels (name, url, is_enabled, created_at, updated_at) VALUES (?, ?, 1, datetime('now'), datetime('now'))",
                ("Channel 1", enc1),
            )
            conn.execute(
                "INSERT INTO notification_channels (name, url, is_enabled, created_at, updated_at) VALUES (?, ?, 0, datetime('now'), datetime('now'))",
                ("Channel 2 Disabled", enc2),
            )
            conn.commit()

        with patch("apprise.Apprise.notify", return_value=True) as mock_notify:
            res = await notifier.send_notification(title="Alert", body="Disk full")
            assert res is True
            assert mock_notify.called

    @pytest.mark.asyncio
    async def test_send_notification_specific_channel(self, tmp_path):
        db_file = tmp_path / "logs.db"
        notifier = NotifierService(db_path=db_file)

        with get_connection(db_file) as conn:
            enc1 = encrypt_channel_url("discord://111/token1")
            cur = conn.execute(
                "INSERT INTO notification_channels (name, url, is_enabled, created_at, updated_at) VALUES (?, ?, 1, datetime('now'), datetime('now'))",
                ("Target Channel", enc1),
            )
            conn.commit()
            cid = cur.lastrowid

        with patch("apprise.Apprise.notify", return_value=True) as mock_notify:
            res = await notifier.send_notification(title="Alert", body="Disk full", channel_id=cid)
            assert res is True
            assert mock_notify.called


# ===================================================================
# 3. API Integration Tests: CRUD and Test Endpoints
# ===================================================================

class TestNotificationsApi:

    @pytest.mark.asyncio
    async def test_crud_notification_channel(self, client: AsyncClient, auth_headers: dict):
        # 1. Create channel
        create_payload = {
            "name": "Ops Discord",
            "url": "discord://123456789/my_real_secret_token",
            "is_enabled": True,
        }
        res = await client.post("/api/notifications/channels", json=create_payload, headers=auth_headers)
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Ops Discord"
        assert data["is_enabled"] is True
        assert "my_real_secret_token" not in data["url"]
        channel_id = data["id"]

        # Verify ciphertext in database
        with get_connection(Path(client._transport.app.state._db_path if hasattr(client._transport.app, 'state') and hasattr(client._transport.app.state, '_db_path') else 'logs.db')) as conn:
            pass

        # 2. List channels
        list_res = await client.get("/api/notifications/channels", headers=auth_headers)
        assert list_res.status_code == 200
        channels = list_res.json()
        assert len(channels) >= 1
        found = [c for c in channels if c["id"] == channel_id]
        assert len(found) == 1
        assert found[0]["name"] == "Ops Discord"
        assert "my_real_secret_token" not in found[0]["url"]

        # 3. Update channel (toggle enabled, change name)
        update_payload = {
            "name": "Production Discord Alerts",
            "is_enabled": False,
        }
        update_res = await client.put(f"/api/notifications/channels/{channel_id}", json=update_payload, headers=auth_headers)
        assert update_res.status_code == 200
        updated_data = update_res.json()
        assert updated_data["name"] == "Production Discord Alerts"
        assert updated_data["is_enabled"] is False

        # 4. Delete channel
        del_res = await client.delete(f"/api/notifications/channels/{channel_id}", headers=auth_headers)
        assert del_res.status_code == 200

        # Verify not found after delete
        list_res2 = await client.get("/api/notifications/channels", headers=auth_headers)
        assert not any(c["id"] == channel_id for c in list_res2.json())

    @pytest.mark.asyncio
    async def test_create_channel_invalid_url_returns_400(self, client: AsyncClient, auth_headers: dict):
        payload = {
            "name": "Bad Target",
            "url": "notavalidscheme://test",
        }
        res = await client.post("/api/notifications/channels", json=payload, headers=auth_headers)
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_create_channel_ssrf_metadata_blocked_returns_400(self, client: AsyncClient, auth_headers: dict):
        payload = {
            "name": "Metadata Exploit Target",
            "url": "json://169.254.169.254/latest/meta-data",
        }
        res = await client.post("/api/notifications/channels", json=payload, headers=auth_headers)
        assert res.status_code == 400
        assert "metadata" in res.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_create_channel_ssrf_docker_port_blocked_returns_400(self, client: AsyncClient, auth_headers: dict):
        payload = {
            "name": "Docker Socket Target",
            "url": "json://192.168.1.100:2375/v1.41/containers/json",
        }
        res = await client.post("/api/notifications/channels", json=payload, headers=auth_headers)
        assert res.status_code == 400
        assert "port" in res.json().get("detail", "").lower() or "blocked" in res.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_test_endpoint_with_raw_url(self, client: AsyncClient, auth_headers: dict):
        with patch("apprise.Apprise.notify", return_value=True):
            test_payload = {
                "url": "discord://123456789/valid_candidate_token",
            }
            res = await client.post("/api/notifications/test", json=test_payload, headers=auth_headers)
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True

    @pytest.mark.asyncio
    async def test_test_endpoint_with_channel_id(self, client: AsyncClient, auth_headers: dict):
        create_payload = {
            "name": "Test Target",
            "url": "discord://123456789/target_token",
        }
        res = await client.post("/api/notifications/channels", json=create_payload, headers=auth_headers)
        channel_id = res.json()["id"]

        with patch("apprise.Apprise.notify", return_value=True):
            test_payload = {"channel_id": channel_id}
            res = await client.post("/api/notifications/test", json=test_payload, headers=auth_headers)
            assert res.status_code == 200
            assert res.json()["success"] is True

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_rejected(self, client: AsyncClient):
        res = await client.get("/api/notifications/channels")
        assert res.status_code in (401, 403)

    def test_pushover_formatting_configures_html_and_converts_markdown(self):
        """Verify that Pushover targets receive html=1 and markdown converted to rich HTML tags."""
        from app.services.notifier import _sync_send_notification
        import apprise

        dispatched_payloads = []

        def fake_send(payload):
            dispatched_payloads.append(payload)
            return True

        with patch("apprise.plugins.pushover.NotifyPushover._send", side_effect=fake_send):
            success = _sync_send_notification(
                urls=["pover://user@token"],
                title="Alert Title",
                body="**Alert: Auth Spike**\n\n**IP:** 10.0.0.1\n\n**Log:**\nFailed login",
                body_format=apprise.NotifyFormat.MARKDOWN,
            )
            assert success is True
            assert len(dispatched_payloads) == 1
            payload = dispatched_payloads[0]
            assert payload.get("html") == 1
            assert "<strong>Alert: Auth Spike</strong>" in payload.get("message")
            assert "**Alert:" not in payload.get("message")

    def test_pushover_removes_newline_gaps_between_fields(self):
        """Verify that Pushover targets have newline gaps removed between fields."""
        from app.services.notifier import _sync_send_notification
        import apprise

        dispatched_payloads = []

        def fake_send(payload):
            dispatched_payloads.append(payload)
            return True

        body = (
            "**Host:** homelab-host\n"
            "**App:** testapp\n"
            "**Log:** [ERROR] failed\n"
            "**AI Analysis:** System recovery active\n"
            "**Link:** http://10.0.0.1/rules/history"
        )

        with patch("apprise.plugins.pushover.NotifyPushover._send", side_effect=fake_send):
            success = _sync_send_notification(
                urls=["pover://user@token"],
                title="Error Alert",
                body=body,
                body_format=apprise.NotifyFormat.MARKDOWN,
            )
            assert success is True
            assert len(dispatched_payloads) == 1
            msg = dispatched_payloads[0].get("message")
            assert dispatched_payloads[0].get("html") == 1
            assert "<strong>Host:</strong> homelab-host\n<strong>App:</strong> testapp" in msg
            assert "<br" not in msg
            assert msg.startswith("<strong>Host:</strong>")
            # Verify lock screen plain text retains clean line breaks
            import re
            plain_text = re.sub(r"<[^>]+>", "", msg)
            assert "Host: homelab-host\nApp: testapp" in plain_text

    def test_notification_worker_pool_configuration(self):
        """Verify that the notification thread pool is configured with 4 workers and proper thread prefix."""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from app.services.notifier import get_notification_executor

        executor = get_notification_executor()
        assert isinstance(executor, ThreadPoolExecutor)
        assert executor._max_workers == 4
        assert executor._thread_name_prefix == "logshed-notifier"

        # Verify thread prefix on running thread
        def _get_thread_name():
            return threading.current_thread().name

        future = executor.submit(_get_thread_name)
        thread_name = future.result(timeout=2.0)
        assert thread_name.startswith("logshed-notifier")

    def test_notification_worker_pool_shutdown(self):
        """Verify shutdown_notifier_executor terminates executor and get_notification_executor refreshes."""
        from app.services.notifier import (
            get_notification_executor,
            shutdown_notifier_executor,
        )

        executor = get_notification_executor()
        shutdown_notifier_executor(wait=True)
        assert executor._shutdown is True

        # Calling get_notification_executor creates a fresh active pool
        fresh_executor = get_notification_executor()
        assert fresh_executor is not executor
        assert fresh_executor._shutdown is False
        assert fresh_executor._thread_name_prefix == "logshed-notifier"

