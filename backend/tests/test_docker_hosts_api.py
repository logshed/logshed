"""
Integration tests for Docker endpoints management REST API:
/api/docker/hosts CRUD operations, primary endpoint protection, and connectivity testing.
"""

import asyncio
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

        # Confirm /api/v1 mount is not available even with the CSRF header (404, or 405 from SPA route)
        v1_resp = await client.post(
            "/api/v1/docker/hosts",
            json={"name": "V1 Bypass", "url": "tcp://1.2.3.4:2375"},
            headers=auth_headers,
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


async def _idle_until_cancelled(*args, **kwargs):
    """Stand-in for DockerTailer.run that idles until the pool cancels it."""
    await asyncio.sleep(10)


def _seed_endpoint(db_file: Path, **cols) -> None:
    """Insert a docker_endpoints row directly."""
    base = {
        "enabled": 1,
        "tls_enabled": 0,
        "created_at": "2026-10-12T00:00:00Z",
        "updated_at": "2026-10-12T00:00:00Z",
    }
    base.update(cols)
    names = ", ".join(base.keys())
    marks = ", ".join("?" for _ in base)
    with get_connection(db_file) as conn:
        conn.execute(f"INSERT INTO docker_endpoints ({names}) VALUES ({marks})", tuple(base.values()))
        conn.commit()


def _seed_logs(db_file: Path, rows: list[tuple[str, str, str]]) -> None:
    """Insert logs as (source_ip, source_alias, message) tuples."""
    with get_connection(db_file) as conn:
        conn.executemany(
            """
            INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
            VALUES ('2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z', ?, ?, 'app', 1, 6, ?, ?)
            """,
            [(ip, alias, msg, msg) for ip, alias, msg in rows],
        )
        conn.commit()


def _log_aliases(db_file: Path, source_ip: str) -> list[str]:
    with get_connection(db_file) as conn:
        cur = conn.execute("SELECT source_alias FROM logs WHERE source_ip = ? ORDER BY id", (source_ip,))
        return [r[0] for r in cur.fetchall()]


_PEM_CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
_PEM_KEY = "-----BEGIN PRIVATE KEY-----\nMIID\n-----END PRIVATE KEY-----"


class TestDockerHostsFixes:
    """Regression tests for the 1.3.0 Docker host fixes."""

    @pytest.mark.asyncio
    async def test_toggling_primary_host_updates_pool_master_state(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """B-10: PUT host 1 enabled flips the pool master switch and stops or starts remote tailers."""
        from app.collectors.docker_collector import DockerTailer
        from app.collectors.docker_pool import DockerCollectorPool
        from app.core.pipeline import KeyedMultilineAssembler

        db_file = tmp_path / "docker_hosts_test.db"
        _seed_endpoint(db_file, id=2, name="Remote", url="tcp://10.0.0.5:2375", source_alias="remote")

        pool = DockerCollectorPool(assembler=KeyedMultilineAssembler(asyncio.Queue()), db_path=db_file)
        with patch.object(DockerTailer, "run", new_callable=AsyncMock) as mock_run, \
                patch("app.main.get_docker_pool", return_value=pool):
            mock_run.side_effect = _idle_until_cancelled
            await pool.start()
            try:
                assert set(pool.get_tailers()) == {1, 2}

                resp = await client.put("/api/docker/hosts/1", json={"enabled": False}, headers=auth_headers)
                assert resp.status_code == 200
                assert pool.pool_enabled is False
                assert pool.get_tailers() == {}

                resp = await client.put("/api/docker/hosts/1", json={"enabled": True}, headers=auth_headers)
                assert resp.status_code == 200
                assert pool.pool_enabled is True
                assert set(pool.get_tailers()) == {1, 2}
            finally:
                await pool.stop()

    @pytest.mark.asyncio
    async def test_rename_to_existing_name_returns_409(self, client: AsyncClient, auth_headers: dict):
        """B-11: renaming an endpoint onto another endpoint's name is a 409, not a 500."""
        for name, url in (("Host A", "tcp://10.0.0.6:2375"), ("Host B", "tcp://10.0.0.7:2375")):
            res = await client.post("/api/docker/hosts", json={"name": name, "url": url}, headers=auth_headers)
            assert res.status_code == 200
        hosts = (await client.get("/api/docker/hosts", headers=auth_headers)).json()
        host_b = next(h for h in hosts if h["name"] == "Host B")

        resp = await client.put(f"/api/docker/hosts/{host_b['id']}", json={"name": "Host A"}, headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "A Docker endpoint named 'Host A' already exists."

        # Keeping its own name is still fine
        resp = await client.put(f"/api/docker/hosts/{host_b['id']}", json={"name": "Host B"}, headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_with_empty_certs_clears_them(self, client: AsyncClient, auth_headers: dict):
        """B-08: an empty string for ca_cert or client_cert clears the stored value; null keeps it."""
        res = await client.post(
            "/api/docker/hosts",
            json={
                "name": "TLS Local",
                "url": "tcp://127.0.0.1:2376",
                "tls_enabled": True,
                "ca_cert": _PEM_CERT,
                "client_cert": _PEM_CERT,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        ep_id = res.json()["id"]

        keep = await client.put(f"/api/docker/hosts/{ep_id}", json={"ca_cert": None}, headers=auth_headers)
        assert keep.status_code == 200
        assert keep.json()["ca_cert"] == _PEM_CERT

        cleared = await client.put(
            f"/api/docker/hosts/{ep_id}", json={"ca_cert": "", "client_cert": ""}, headers=auth_headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["ca_cert"] is None
        assert cleared.json()["client_cert"] is None

    @pytest.mark.asyncio
    async def test_connection_test_without_stored_secrets_never_decrypts_key(
        self, client: AsyncClient, auth_headers: dict
    ):
        """B-08: use_stored_secrets false never loads the stored client key or certs."""
        res = await client.post(
            "/api/docker/hosts",
            json={
                "name": "TLS Host",
                "url": "tcp://127.0.0.1:2376",
                "tls_enabled": True,
                "ca_cert": _PEM_CERT,
                "client_cert": _PEM_CERT,
                "client_key": _PEM_KEY,
            },
            headers=auth_headers,
        )
        ep_id = res.json()["id"]

        with patch("app.api.docker_hosts.decrypt_value") as mock_decrypt, \
                patch("app.api.docker_hosts.create_docker_ssl_context", side_effect=ValueError("bad pem")) as mock_ctx:
            resp = await client.post(
                "/api/docker/hosts/test",
                json={
                    "endpoint_id": ep_id,
                    "url": "tcp://127.0.0.1:2376",
                    "tls_enabled": True,
                    "use_stored_secrets": False,
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        mock_decrypt.assert_not_called()
        kwargs = mock_ctx.call_args.kwargs
        assert kwargs["client_key"] is None
        assert kwargs["client_cert"] is None
        assert kwargs["ca_cert"] is None

    @pytest.mark.asyncio
    async def test_connection_test_with_stored_secrets_uses_them_for_same_url(
        self, client: AsyncClient, auth_headers: dict
    ):
        """The stored key is still used when the test targets the stored URL."""
        res = await client.post(
            "/api/docker/hosts",
            json={
                "name": "TLS Host",
                "url": "tcp://127.0.0.1:2376",
                "tls_enabled": True,
                "ca_cert": _PEM_CERT,
                "client_cert": _PEM_CERT,
                "client_key": _PEM_KEY,
            },
            headers=auth_headers,
        )
        ep_id = res.json()["id"]

        with patch("app.api.docker_hosts.create_docker_ssl_context", side_effect=ValueError("bad pem")) as mock_ctx:
            resp = await client.post(
                "/api/docker/hosts/test",
                json={"endpoint_id": ep_id, "url": "tcp://127.0.0.1:2376", "tls_enabled": True},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        kwargs = mock_ctx.call_args.kwargs
        assert kwargs["client_key"] == _PEM_KEY
        assert kwargs["ca_cert"] == _PEM_CERT

    @pytest.mark.asyncio
    async def test_connection_test_to_other_url_does_not_load_stored_key(
        self, client: AsyncClient, auth_headers: dict
    ):
        """S-14: an endpoint_id combined with a different URL must not send the stored key elsewhere."""
        res = await client.post(
            "/api/docker/hosts",
            json={
                "name": "TLS Host",
                "url": "tcp://127.0.0.1:2376",
                "tls_enabled": True,
                "ca_cert": _PEM_CERT,
                "client_cert": _PEM_CERT,
                "client_key": _PEM_KEY,
            },
            headers=auth_headers,
        )
        ep_id = res.json()["id"]

        with patch("app.api.docker_hosts.decrypt_value") as mock_decrypt, \
                patch("app.api.docker_hosts.create_docker_ssl_context", side_effect=ValueError("bad pem")) as mock_ctx:
            resp = await client.post(
                "/api/docker/hosts/test",
                json={
                    "endpoint_id": ep_id,
                    "url": "tcp://127.0.0.2:2376",
                    "tls_enabled": True,
                    "ca_cert": _PEM_CERT,
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        mock_decrypt.assert_not_called()
        assert mock_ctx.call_args.kwargs["client_key"] is None

    @pytest.mark.asyncio
    async def test_primary_alias_backfill_via_hosts_api_for_socket_proxy(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """B-12: a primary host reached through a socket proxy backfills rows stamped with the proxy host name."""
        db_file = tmp_path / "docker_hosts_test.db"
        with get_connection(db_file) as conn:
            conn.execute("UPDATE docker_endpoints SET url = 'tcp://socket-proxy:2375', source_alias = 'old-main' WHERE id = 1")
            conn.commit()
        _seed_logs(db_file, [("socket-proxy", "old-main", "a"), ("socket-proxy", "old-main", "b")])

        resp = await client.put("/api/docker/hosts/1", json={"source_alias": "new-main"}, headers=auth_headers)
        assert resp.status_code == 200
        assert _log_aliases(db_file, "socket-proxy") == ["new-main", "new-main"]

    @pytest.mark.asyncio
    async def test_primary_alias_backfill_via_settings_for_socket_proxy(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """B-12: the settings page alias change backfills rows from a socket-proxy primary host."""
        db_file = tmp_path / "docker_hosts_test.db"
        with get_connection(db_file) as conn:
            conn.execute("UPDATE docker_endpoints SET url = 'tcp://socket-proxy:2375', source_alias = 'old-main' WHERE id = 1")
            conn.commit()
        _seed_logs(db_file, [("socket-proxy", "old-main", "a"), ("10.9.9.9", "old-main", "syslog")])

        resp = await client.post("/api/settings", json={"docker_source_alias": "new-main"}, headers=auth_headers)
        assert resp.status_code == 200
        assert _log_aliases(db_file, "socket-proxy") == ["new-main"]
        # Unrelated syslog rows are left alone
        assert _log_aliases(db_file, "10.9.9.9") == ["old-main"]

    @pytest.mark.asyncio
    async def test_alias_backfill_for_second_unix_socket_endpoint(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """B-12: a second Unix-socket endpoint stamps rows with its old alias as source_ip; they are backfilled."""
        db_file = tmp_path / "docker_hosts_test.db"
        _seed_endpoint(db_file, id=2, name="Rootless", url="unix:///run/user/1000/docker.sock", source_alias="rootless-old")
        _seed_logs(db_file, [("rootless-old", "rootless-old", "a"), ("rootless-old", "rootless-old", "b")])

        resp = await client.put("/api/docker/hosts/2", json={"source_alias": "rootless-new"}, headers=auth_headers)
        assert resp.status_code == 200
        assert _log_aliases(db_file, "rootless-old") == ["rootless-new", "rootless-new"]

    @pytest.mark.asyncio
    async def test_syslog_and_docker_filters_stay_apart_on_one_machine(
        self, client: AsyncClient, auth_headers: dict, tmp_path: Path
    ):
        """A Docker host named like the machine's syslog alias filters, and lists, only its own rows."""
        db_file = tmp_path / "docker_hosts_test.db"
        _seed_endpoint(db_file, id=2, name="Tower", url="tcp://192.168.1.50:2375", source_alias="Tower-Docker")
        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.50', 'Tower', '2026-10-12T00:00:00Z')"
            )
            conn.commit()
        _seed_logs(db_file, [("192.168.1.50", "Tower", "syslog line"), ("192.168.1.50", "Tower-Docker", "container line")])

        for source, expected in (("Tower-Docker", ["container line"]), ("Tower", ["syslog line"])):
            resp = await client.get("/api/logs", params={"source": source}, headers=auth_headers)
            assert resp.status_code == 200
            assert [log["message"] for log in resp.json()["logs"]] == expected

        facets = (await client.get("/api/logs/facets", headers=auth_headers)).json()
        assert {"Tower", "Tower-Docker"} <= set(facets["sources"])
        assert "tower-docker" not in facets["sources"]

    @pytest.mark.asyncio
    async def test_syslog_and_docker_aliases_must_differ(self, client: AsyncClient, auth_headers: dict):
        """A syslog host alias and a Docker host alias with the same name (any letter case) are refused with 409."""
        resp = await client.post("/api/aliases", json={"ip": "192.168.1.50", "alias": "Tower"}, headers=auth_headers)
        assert resp.status_code == 200

        resp = await client.post(
            "/api/docker/hosts",
            json={"name": "Tower", "url": "tcp://192.168.1.50:2375", "source_alias": "tower"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "already used by a syslog host alias" in resp.json()["detail"]

        resp = await client.post(
            "/api/docker/hosts",
            json={"name": "Tower", "url": "tcp://192.168.1.50:2375", "source_alias": "tower-docker"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        host_id = resp.json()["id"]

        resp = await client.put(f"/api/docker/hosts/{host_id}", json={"source_alias": "TOWER"}, headers=auth_headers)
        assert resp.status_code == 409
        resp = await client.post("/api/settings", json={"docker_source_alias": "Tower"}, headers=auth_headers)
        assert resp.status_code == 409
        # Requests that leave the alias unchanged still work
        resp = await client.put(f"/api/docker/hosts/{host_id}", json={"enabled": False}, headers=auth_headers)
        assert resp.status_code == 200

        for alias in ("Tower-Docker", "docker"):
            resp = await client.post("/api/aliases", json={"ip": "192.168.1.60", "alias": alias}, headers=auth_headers)
            assert resp.status_code == 409
            assert "already used by a Docker host" in resp.json()["detail"]
        resp = await client.post(
            "/api/aliases", json={"ip": "192.168.1.50", "alias": "Tower", "notes": "rack 1"}, headers=auth_headers
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_blank_alias_on_update_falls_back_to_docker(self, client: AsyncClient, auth_headers: dict):
        """Clearing a host's alias stores "docker", the label its collector uses, as on create."""
        resp = await client.post(
            "/api/docker/hosts",
            json={"name": "Spare", "url": "tcp://10.0.0.9:2375", "source_alias": "spare"},
            headers=auth_headers,
        )
        host_id = resp.json()["id"]
        resp = await client.put(f"/api/docker/hosts/{host_id}", json={"source_alias": "  "}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["source_alias"] == "docker"

    @pytest.mark.asyncio
    async def test_oversize_fields_rejected(self, client: AsyncClient, auth_headers: dict):
        """S-30: oversize URL, exclusion list and PEM fields are rejected with 422."""
        big_pem = "A" * 16385
        cases = [
            {"name": "Big", "url": "tcp://10.0.0.8:2375/" + "a" * 600},
            {"name": "Big", "url": "tcp://10.0.0.8:2375", "exclude_containers": "x" * 513},
            {"name": "Big", "url": "tcp://127.0.0.1:2376", "tls_enabled": True, "ca_cert": big_pem},
            {"name": "Big", "url": "tcp://127.0.0.1:2376", "client_cert": big_pem},
            {"name": "Big", "url": "tcp://127.0.0.1:2376", "client_key": big_pem},
        ]
        for payload in cases:
            res = await client.post("/api/docker/hosts", json=payload, headers=auth_headers)
            assert res.status_code == 422, payload

        upd = await client.put("/api/docker/hosts/1", json={"source_alias": "s" * 600}, headers=auth_headers)
        assert upd.status_code == 422
        upd = await client.put("/api/docker/hosts/1", json={"client_key": big_pem}, headers=auth_headers)
        assert upd.status_code == 422

        test_res = await client.post(
            "/api/docker/hosts/test",
            json={"url": "tcp://127.0.0.1:2376", "ca_cert": big_pem},
            headers=auth_headers,
        )
        assert test_res.status_code == 422

    @pytest.mark.asyncio
    async def test_link_local_and_metadata_targets_rejected(self, client: AsyncClient, auth_headers: dict):
        """S-17: link-local and cloud metadata addresses are refused for create and test."""
        for bad_url in (
            "tcp://169.254.169.254:2375",
            "http://169.254.10.1:2375",
            "tcp://[fe80::1]:2375",
            "tcp://100.100.100.200:2375",
            "tcp://[fd00:ec2::254]:2375",
            "tcp://[::ffff:169.254.169.254]:2375",
        ):
            res = await client.post(
                "/api/docker/hosts/test", json={"url": bad_url}, headers=auth_headers
            )
            assert res.status_code == 422, bad_url
            res = await client.post(
                "/api/docker/hosts", json={"name": "Meta", "url": bad_url}, headers=auth_headers
            )
            assert res.status_code == 422, bad_url

    @pytest.mark.asyncio
    async def test_names_resolving_to_metadata_targets_rejected(self, client: AsyncClient, auth_headers: dict):
        """S-17: a host name that resolves to a link-local address is refused."""
        async def _fake_resolve(host: str) -> list[str]:
            return ["169.254.169.254"] if host == "metadata.internal" else []

        with patch("app.api.docker_hosts._resolve_host_addresses", side_effect=_fake_resolve):
            res = await client.post(
                "/api/docker/hosts/test", json={"url": "tcp://metadata.internal:2375"}, headers=auth_headers
            )
            assert res.status_code == 422
            res = await client.post(
                "/api/docker/hosts", json={"name": "Meta", "url": "tcp://metadata.internal:2375"}, headers=auth_headers
            )
            assert res.status_code == 422
            res = await client.put(
                "/api/docker/hosts/1", json={"url": "tcp://metadata.internal:2375"}, headers=auth_headers
            )
            assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_unix_socket_paths_restricted(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        """S-17: only Docker socket paths (or ones listed in DOCKER_ALLOWED_SOCKETS) are accepted."""
        monkeypatch.delenv("DOCKER_ALLOWED_SOCKETS", raising=False)
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        for bad_url in ("unix:///etc/passwd", "unix:///run/postgresql/.s.PGSQL.5432", "unix:///tmp/docker.sock.bak"):
            res = await client.post("/api/docker/hosts/test", json={"url": bad_url}, headers=auth_headers)
            assert res.status_code == 422, bad_url

        monkeypatch.setenv("DOCKER_ALLOWED_SOCKETS", "/run/podman/podman.sock, /run/other.sock")
        res = await client.post(
            "/api/docker/hosts", json={"name": "Podman", "url": "unix:///run/podman/podman.sock"}, headers=auth_headers
        )
        assert res.status_code == 200

        monkeypatch.setenv("DOCKER_HOST", "unix:///run/custom/engine.sock")
        res = await client.post(
            "/api/docker/hosts", json={"name": "Custom", "url": "unix:///run/custom/engine.sock"}, headers=auth_headers
        )
        assert res.status_code == 200

    @pytest.mark.asyncio
    async def test_connection_test_errors_are_generic(self, client: AsyncClient, auth_headers: dict):
        """S-17: probe failures return generic text and keep the details in the server log."""
        import httpx

        class _FailingClient:
            def __init__(self, exc):
                self._exc = exc

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *args, **kwargs):
                raise self._exc

        cases = [
            (httpx.ConnectError("[Errno 111] Connection refused to secret-internal:2375"), "connection refused"),
            (httpx.ConnectTimeout("timed out reaching secret-internal"), "timeout"),
            (httpx.ReadTimeout("read timed out secret-internal"), "timeout"),
            (
                httpx.ConnectError(
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate secret-internal"
                ),
                "TLS error",
            ),
            (httpx.RemoteProtocolError("illegal status line secret-internal"), "not a Docker API"),
        ]
        for exc, expected in cases:
            with patch("app.api.docker_hosts._build_client", return_value=_FailingClient(exc)):
                resp = await client.post(
                    "/api/docker/hosts/test", json={"url": "tcp://10.0.0.12:2375"}, headers=auth_headers
                )
            body = resp.json()
            assert body["success"] is False
            assert expected in body["error"], (exc, body)
            assert "secret-internal" not in body["error"]

    @pytest.mark.asyncio
    async def test_connection_test_non_docker_reply_is_generic(self, client: AsyncClient, auth_headers: dict):
        """S-17: a reachable service that is not Docker reports 'not a Docker API' without echoing the reply."""
        class _Resp:
            status_code = 404

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *args, **kwargs):
                return _Resp()

        with patch("app.api.docker_hosts._build_client", return_value=_Client()):
            resp = await client.post(
                "/api/docker/hosts/test", json={"url": "tcp://10.0.0.14:2375"}, headers=auth_headers
            )
        body = resp.json()
        assert body["success"] is False
        assert "not a Docker API" in body["error"]

    @pytest.mark.asyncio
    async def test_tls_without_ca_rejected_for_remote_hosts(self, client: AsyncClient, auth_headers: dict):
        """S-16: TLS to a non-loopback host requires a CA certificate."""
        res = await client.post(
            "/api/docker/hosts",
            json={"name": "No CA", "url": "tcp://10.0.0.9:2376", "tls_enabled": True},
            headers=auth_headers,
        )
        assert res.status_code == 422
        assert "CA certificate" in res.json()["detail"]

        test_res = await client.post(
            "/api/docker/hosts/test",
            json={"url": "tcp://10.0.0.9:2376", "tls_enabled": True},
            headers=auth_headers,
        )
        assert test_res.status_code == 422
        assert "CA certificate" in test_res.json()["detail"]

        # Loopback TLS without a CA is still allowed
        ok = await client.post(
            "/api/docker/hosts",
            json={"name": "Loopback TLS", "url": "tcp://127.0.0.1:2376", "tls_enabled": True},
            headers=auth_headers,
        )
        assert ok.status_code == 200

        # Turning TLS on for an existing remote host without a CA is refused too
        plain = await client.post(
            "/api/docker/hosts",
            json={"name": "Plain", "url": "tcp://10.0.0.10:2375"},
            headers=auth_headers,
        )
        upd = await client.put(
            f"/api/docker/hosts/{plain.json()['id']}", json={"tls_enabled": True}, headers=auth_headers
        )
        assert upd.status_code == 422

    @pytest.mark.asyncio
    async def test_verify_hostname_defaults_on_and_round_trips(self, client: AsyncClient, auth_headers: dict):
        """S-16: verify_hostname defaults to on and can be turned off per endpoint."""
        res = await client.post(
            "/api/docker/hosts",
            json={"name": "Verify", "url": "tcp://10.0.0.11:2376", "tls_enabled": True, "ca_cert": _PEM_CERT},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["verify_hostname"] is True
        ep_id = res.json()["id"]

        upd = await client.put(f"/api/docker/hosts/{ep_id}", json={"verify_hostname": False}, headers=auth_headers)
        assert upd.status_code == 200
        assert upd.json()["verify_hostname"] is False

        hosts = (await client.get("/api/docker/hosts", headers=auth_headers)).json()
        assert next(h for h in hosts if h["id"] == ep_id)["verify_hostname"] is False

    @pytest.mark.asyncio
    async def test_connection_test_passes_verify_hostname(self, client: AsyncClient, auth_headers: dict):
        """S-16: the connection test builds its TLS context with the requested hostname check."""
        with patch("app.api.docker_hosts.create_docker_ssl_context", side_effect=ValueError("bad pem")) as mock_ctx:
            await client.post(
                "/api/docker/hosts/test",
                json={"url": "tcp://127.0.0.1:2376", "tls_enabled": True, "verify_hostname": False},
                headers=auth_headers,
            )
        assert mock_ctx.call_args.kwargs["verify_hostname"] is False

    @pytest.mark.asyncio
    async def test_connection_test_hostname_mismatch_mentions_toggle(self, client: AsyncClient, auth_headers: dict):
        """S-16: a certificate host name mismatch points at the verify host name toggle."""
        import httpx

        class _FailingClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *args, **kwargs):
                raise httpx.ConnectError(
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                    "IP address mismatch, certificate is not valid for '10.0.0.13'. (_ssl.c:1000)"
                )

        with patch("app.api.docker_hosts._build_client", return_value=_FailingClient()), \
                patch("app.api.docker_hosts.create_docker_ssl_context"):
            resp = await client.post(
                "/api/docker/hosts/test",
                json={"url": "tcp://10.0.0.13:2376", "tls_enabled": True, "ca_cert": _PEM_CERT},
                headers=auth_headers,
            )
        body = resp.json()
        assert body["success"] is False
        assert "Verify TLS host name" in body["error"]


class TestAdditionalHostsEnvParsing:
    """N01-9: bad DOCKER_ADDITIONAL_HOSTS values log a warning and give the empty default."""

    @pytest.mark.parametrize("raw", ["{not json", '{"name": "a", "url": "tcp://h:2375"}', '"just a string"', "42"])
    def test_invalid_values_warn_and_return_empty_list(self, monkeypatch, caplog, raw):
        from app.core.config import get_docker_additional_hosts

        monkeypatch.setenv("DOCKER_ADDITIONAL_HOSTS", raw)
        with caplog.at_level("WARNING", logger="app.core.config"):
            assert get_docker_additional_hosts() == []
        assert any("DOCKER_ADDITIONAL_HOSTS" in r.getMessage() for r in caplog.records)
