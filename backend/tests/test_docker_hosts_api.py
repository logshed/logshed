"""
Integration tests for Docker endpoints management REST API:
/api/docker/hosts CRUD operations, primary endpoint protection, and connectivity testing.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import api_token_rate_limiter, _last_used_timestamps
from app.core.migrations import get_connection, run_migrations
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
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    reset_crypto_cache()
    _last_used_timestamps.clear()

    db_file = tmp_path / "docker_hosts_test.db"
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
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


class TestDockerHostsApi:
    """Tests for /api/docker/hosts CRUD and test connectivity endpoints."""

    @pytest.mark.asyncio
    async def test_list_docker_endpoints(self, client: AsyncClient, auth_headers: dict):
        """Returns seeded primary Docker endpoint on fresh migration."""
        resp = await client.get("/api/docker/hosts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        primary = data[0]
        assert primary["id"] == 1
        assert primary["name"] == "Primary Docker"
        assert primary["source_alias"] == "docker"
        assert primary["enabled"] is True

    @pytest.mark.asyncio
    async def test_create_and_duplicate_guard(self, client: AsyncClient, auth_headers: dict):
        """Allows creating new endpoint and rejects duplicate names."""
        payload = {
            "name": "Remote Docker 1",
            "url": "tcp://192.168.1.50:2375",
            "source_alias": "remote-1",
            "enabled": True,
            "tls_enabled": False,
            "exclude_containers": "gluetun",
        }
        resp = await client.post("/api/docker/hosts", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        created = resp.json()
        assert created["name"] == "Remote Docker 1"
        assert created["source_alias"] == "remote-1"
        assert created["id"] > 1

        # Attempt creating duplicate name
        dup_resp = await client.post("/api/docker/hosts", json=payload, headers=auth_headers)
        assert dup_resp.status_code == 409
        assert "already exists" in dup_resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_endpoint_and_primary_sync(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """Updating primary endpoint synchronizes system_settings and prevents rename."""
        # Renaming primary endpoint is blocked
        rename_resp = await client.put(
            "/api/docker/hosts/1",
            json={"name": "Custom Name"},
            headers=auth_headers,
        )
        assert rename_resp.status_code == 400
        assert "Cannot rename the primary Docker endpoint" in rename_resp.json()["detail"]

        # Updating alias and exclusion synchronizes to system_settings
        upd_resp = await client.put(
            "/api/docker/hosts/1",
            json={
                "source_alias": "my-docker",
                "exclude_containers": "containerA,containerB",
            },
            headers=auth_headers,
        )
        assert upd_resp.status_code == 200
        updated = upd_resp.json()
        assert updated["source_alias"] == "my-docker"
        assert updated["exclude_containers"] == "containerA,containerB"

        # Check system_settings sync
        db_file = tmp_path / "docker_hosts_test.db"
        with get_connection(db_file) as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM system_settings WHERE key = 'docker_source_alias'")
            assert cur.fetchone()[0] == "my-docker"

    @pytest.mark.asyncio
    async def test_update_endpoint_retroactively_updates_historical_logs(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """Updating a Docker endpoint alias updates historical logs for that source without clobbering syslog logs."""
        db_file = tmp_path / "docker_hosts_test.db"
        with get_connection(db_file) as conn:
            cur = conn.cursor()
            # Seed logs for primary docker
            cur.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES ('2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z', 'docker', 'docker', 'nginx', 1, 6, 'Log 1', 'Log 1'),
                       ('2026-10-12T00:01:00Z', '2026-10-12T00:01:00Z', 'docker', 'docker', 'plex', 1, 6, 'Log 2', 'Log 2')
                """
            )
            # Create a secondary endpoint in db
            cur.execute(
                """
                INSERT INTO docker_endpoints (id, name, url, source_alias, enabled, tls_enabled, created_at, updated_at)
                VALUES (2, 'Remote Node', 'tcp://10.0.0.5:2375', 'node-old', 1, 0, '2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z')
                """
            )
            # Seed logs for remote node and syslog logs from same IP
            cur.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES ('2026-10-12T00:02:00Z', '2026-10-12T00:02:00Z', '10.0.0.5', 'node-old', 'redis', 1, 6, 'Docker Log', 'Docker Log'),
                       ('2026-10-12T00:03:00Z', '2026-10-12T00:03:00Z', '10.0.0.5', 'syslog-box', 'kernel', 1, 6, 'Syslog Log', 'Syslog Log')
                """
            )
            conn.commit()

        # Update primary endpoint alias to 'unraid-main'
        resp_pri = await client.put(
            "/api/docker/hosts/1",
            json={"source_alias": "unraid-main"},
            headers=auth_headers,
        )
        assert resp_pri.status_code == 200

        # Update remote endpoint alias to 'node-new'
        resp_sec = await client.put(
            "/api/docker/hosts/2",
            json={"source_alias": "node-new"},
            headers=auth_headers,
        )
        assert resp_sec.status_code == 200

        with get_connection(db_file) as conn:
            cur = conn.cursor()
            cur.execute("SELECT source_alias FROM logs WHERE source_ip = 'docker' ORDER BY id")
            pri_aliases = [row[0] for row in cur.fetchall()]
            assert pri_aliases == ["unraid-main", "unraid-main"]

            cur.execute("SELECT source_alias, message FROM logs WHERE source_ip = '10.0.0.5' ORDER BY id")
            sec_rows = cur.fetchall()
            assert sec_rows[0][0] == "node-new"
            assert sec_rows[0][1] == "Docker Log"
            # Syslog log is untouched
            assert sec_rows[1][0] == "syslog-box"
            assert sec_rows[1][1] == "Syslog Log"

    @pytest.mark.asyncio
    async def test_delete_endpoint_protection(self, client: AsyncClient, auth_headers: dict):
        """Primary endpoint cannot be deleted; secondary endpoint deletes successfully."""
        # Primary delete blocked
        del_pri = await client.delete("/api/docker/hosts/1", headers=auth_headers)
        assert del_pri.status_code == 400
        assert "Cannot delete the primary Docker endpoint" in del_pri.json()["detail"]

        # Create secondary endpoint
        res = await client.post(
            "/api/docker/hosts",
            json={"name": "Temporary", "url": "tcp://1.2.3.4:2375"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        sec_id = res.json()["id"]

        # Delete secondary endpoint
        del_sec = await client.delete(f"/api/docker/hosts/{sec_id}", headers=auth_headers)
        assert del_sec.status_code == 200
        assert del_sec.json()["status"] == "ok"

        # Verify not found after deletion
        del_again = await client.delete(f"/api/docker/hosts/{sec_id}", headers=auth_headers)
        assert del_again.status_code == 404

    @pytest.mark.asyncio
    async def test_connectivity_tester(self, client: AsyncClient, auth_headers: dict):
        """Test connectivity endpoint handles socket failures gracefully."""
        # Non-existent socket path
        resp = await client.post(
            "/api/docker/hosts/test",
            json={"url": "unix:///nonexistent/docker.sock"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["success"] is False
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_url_scheme_validation(self, client: AsyncClient, auth_headers: dict):
        """Reject invalid schemes or malformed URLs in endpoint create and test requests."""
        for bad_url in ["ftp://docker.local:2375", "file:///var/run/docker.sock", "gopher://bad", "invalid-url"]:
            res_create = await client.post(
                "/api/docker/hosts",
                json={"name": "Bad Scheme", "url": bad_url},
                headers=auth_headers,
            )
            assert res_create.status_code == 422

            res_test = await client.post(
                "/api/docker/hosts/test",
                json={"url": bad_url},
                headers=auth_headers,
            )
            assert res_test.status_code == 422

    @pytest.mark.asyncio
    async def test_csrf_enforcement_and_no_v1_bypass(self, client: AsyncClient, auth_headers: dict):
        """Mutating Docker host routes enforce CSRF headers and reject /api/v1 bypass attempts."""
        # Without X-Requested-With header, session authenticated request is rejected with 403
        no_csrf_headers = {k: v for k, v in auth_headers.items() if k.lower() != "x-requested-with"}
        resp = await client.post(
            "/api/docker/hosts",
            json={"name": "No CSRF", "url": "tcp://1.2.3.4:2375"},
            headers=no_csrf_headers,
        )
        assert resp.status_code == 403

        # Confirm /api/v1 mount is not available (returns 404 or 405 Method Not Allowed from SPA route)
        v1_resp = await client.post(
            "/api/v1/docker/hosts",
            json={"name": "V1 Bypass", "url": "tcp://1.2.3.4:2375"},
            headers=no_csrf_headers,
        )
        assert v1_resp.status_code in (404, 405)

    @pytest.mark.asyncio
    async def test_test_endpoint_mtls_key_lookup(self, client: AsyncClient, auth_headers: dict):
        """Test connection looks up and decrypts stored client_key when endpoint_id is passed."""
        # Create an endpoint with TLS and client key
        res = await client.post(
            "/api/docker/hosts",
            json={
                "name": "TLS Host",
                "url": "tcp://127.0.0.1:2376",
                "tls_enabled": True,
                "ca_cert": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
                "client_cert": "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----",
                "client_key": "-----BEGIN PRIVATE KEY-----\nMIID\n-----END PRIVATE KEY-----",
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        ep_id = res.json()["id"]

        # Call /api/docker/hosts/test with endpoint_id but without client_key
        test_resp = await client.post(
            "/api/docker/hosts/test",
            json={
                "endpoint_id": ep_id,
                "url": "tcp://127.0.0.1:2376",
                "tls_enabled": True,
            },
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        # It attempts connection using decrypted key (which fails cleanly since daemon isn't running)
        result = test_resp.json()
        assert result["success"] is False
        assert result["error"] is not None
