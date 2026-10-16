"""
Unit and integration tests for DockerCollectorPool and multi-instance Docker collection.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.collectors.docker_collector import DockerTailer
from app.collectors.docker_pool import DockerCollectorPool, _supervise_tailer
from app.core.migrations import get_connection, run_migrations
from app.core.pipeline import KeyedMultilineAssembler


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    db = tmp_path / "docker_pool_test.db"
    run_migrations(db)
    return db


@pytest.fixture
def assembler() -> KeyedMultilineAssembler:
    queue = asyncio.Queue()
    return KeyedMultilineAssembler(queue)


async def _run_until_cancelled(*args, **kwargs):
    """Stand-in for DockerTailer.run that idles until the pool cancels it."""
    await asyncio.sleep(10)


class TestDockerCollectorPool:
    """Test suite for DockerCollectorPool lifecycle and management."""

    @pytest.mark.asyncio
    async def test_pool_start_and_stop(self, test_db: Path, assembler: KeyedMultilineAssembler):
        """Pool starts tailers for enabled endpoints and stops cleanly."""
        pool = DockerCollectorPool(assembler=assembler, db_path=test_db)
        assert not pool.is_running
        assert pool.pool_enabled

        with patch.object(DockerTailer, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = asyncio.CancelledError
            await pool.start()
            assert pool.is_running
            assert 1 in pool.get_tailers()

            # Retrieve primary tailer
            primary = pool.get_tailer(1)
            assert primary is not None
            assert primary.name == "Primary Docker"
            assert primary.endpoint_id == 1

            await pool.stop()
            assert not pool.is_running
            assert len(pool.get_tailers()) == 0

    @pytest.mark.asyncio
    async def test_pool_error_isolation(self, test_db: Path, assembler: KeyedMultilineAssembler):
        """Crash or error in one endpoint does not prevent other endpoints from operating."""
        # Insert a secondary endpoint into the database
        with get_connection(test_db) as conn:
            conn.execute(
                """
                INSERT INTO docker_endpoints (name, url, source_alias, enabled, tls_enabled, created_at, updated_at)
                VALUES ('Secondary Docker', 'tcp://192.168.1.100:2375', 'remote', 1, 0, datetime('now'), datetime('now'))
                """
            )
            conn.commit()

        pool = DockerCollectorPool(assembler=assembler, db_path=test_db)

        # Endpoint 1 will run fine, Endpoint 2 raises an exception then cancels
        ep2_crashed = False

        async def run_mock_self(self):
            nonlocal ep2_crashed
            if self.endpoint_id == 2:
                ep2_crashed = True
                raise RuntimeError("Connection dropped by remote daemon")
            # Endpoint 1 sleeps until cancelled
            await asyncio.sleep(10)

        with patch.object(DockerTailer, "run", side_effect=run_mock_self, autospec=True):
            await pool.start()
            # Give supervisor time to cycle
            await asyncio.sleep(0.1)

            assert 1 in pool.get_tailers()
            assert 2 in pool.get_tailers()
            assert ep2_crashed

            statuses = await pool.get_statuses()
            assert len(statuses) == 2
            ep_names = {s["name"] for s in statuses}
            assert "Primary Docker" in ep_names
            assert "Secondary Docker" in ep_names

            await pool.stop()

    @pytest.mark.asyncio
    async def test_dynamic_add_reload_remove_endpoint(
        self, test_db: Path, assembler: KeyedMultilineAssembler
    ):
        """Endpoints can be dynamically added, reloaded, and removed from the running pool."""
        pool = DockerCollectorPool(assembler=assembler, db_path=test_db)

        with patch.object(DockerTailer, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = _run_until_cancelled
            await pool.start()
            assert len(pool.get_tailers()) == 1

            # Dynamically add endpoint 3
            ep3_data = {
                "id": 3,
                "name": "Edge Node",
                "url": "tcp://10.0.0.3:2375",
                "source_alias": "edge-3",
                "enabled": True,
                "exclude_containers": "",
            }
            await pool.add_endpoint(ep3_data)
            assert 3 in pool.get_tailers()
            tailer3 = pool.get_tailer(3)
            assert tailer3.name == "Edge Node"
            assert tailer3.source_alias == "edge-3"

            # Reload endpoint 3 with new alias
            ep3_updated = dict(ep3_data, source_alias="edge-node-alpha")
            await pool.reload_endpoint(3, ep3_updated)
            assert pool.get_tailer(3).source_alias == "edge-node-alpha"

            # Remove endpoint 3
            await pool.remove_endpoint(3)
            assert 3 not in pool.get_tailers()

            await pool.stop()

    @pytest.mark.asyncio
    async def test_set_pool_enabled_toggle(
        self, test_db: Path, assembler: KeyedMultilineAssembler
    ):
        """Toggling pool enablement controls tailers across all endpoints."""
        pool = DockerCollectorPool(assembler=assembler, db_path=test_db)

        with patch.object(DockerTailer, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = _run_until_cancelled
            await pool.start()
            assert len(pool.get_tailers()) == 1

            # Disable pool
            await pool.set_pool_enabled(False)
            assert not pool.pool_enabled
            assert len(pool.get_tailers()) == 0

            # Re-enable pool
            await pool.set_pool_enabled(True)
            assert pool.pool_enabled
            assert len(pool.get_tailers()) == 1

            await pool.stop()

    def test_docker_tailer_backward_compatibility(self, test_db: Path, assembler: KeyedMultilineAssembler):
        """DockerTailer constructor without new endpoint parameters defaults sensibly."""
        tailer = DockerTailer(assembler=assembler, db_path=test_db)
        assert tailer.endpoint_id == 1
        assert tailer.name == "Primary Docker"
        assert tailer.source_alias == "docker"
        status = tailer.get_status()
        assert status["endpoint_id"] == 1
        assert status["name"] == "Primary Docker"
        assert status["is_connected"] is False


class TestDockerTlsHostnameVerification:
    """S-16: per-endpoint TLS host name verification, on by default."""

    def test_ssl_context_checks_hostname_by_default(self):
        from app.collectors.docker_collector import create_docker_ssl_context

        ctx = create_docker_ssl_context()
        assert ctx.check_hostname is True

    def test_ssl_context_toggle_turns_hostname_check_off(self):
        import ssl
        from app.collectors.docker_collector import create_docker_ssl_context

        ctx = create_docker_ssl_context(verify_hostname=False)
        assert ctx.check_hostname is False
        # The certificate chain is still verified
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    @pytest.mark.parametrize("stored, expected", [(1, True), (0, False)])
    def test_pool_builds_tailer_with_endpoint_setting(
        self, test_db: Path, assembler: KeyedMultilineAssembler, stored: int, expected: bool
    ):
        pool = DockerCollectorPool(assembler=assembler, db_path=test_db)
        row = {
            "id": 7,
            "name": "TLS Node",
            "url": "tcp://10.0.0.7:2376",
            "source_alias": "tls-node",
            "enabled": 1,
            "tls_enabled": 1,
            "verify_hostname": stored,
        }
        with patch("app.collectors.docker_pool.create_docker_ssl_context") as mock_ctx:
            pool._build_tailer(row)
        assert mock_ctx.call_args.kwargs["verify_hostname"] is expected

    @pytest.mark.asyncio
    async def test_status_points_at_toggle_on_hostname_mismatch(
        self, test_db: Path, assembler: KeyedMultilineAssembler
    ):
        pool = DockerCollectorPool(assembler=assembler, db_path=test_db)
        with patch.object(DockerTailer, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = _run_until_cancelled
            await pool.start()
            try:
                pool.get_tailer(1)._last_error = (
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                    "IP address mismatch, certificate is not valid for '10.0.0.7'. (_ssl.c:1000)"
                )
                statuses = await pool.get_statuses()
            finally:
                await pool.stop()
        primary = next(s for s in statuses if s["id"] == 1)
        assert "Verify TLS host name" in primary["last_error"]
        assert primary["verify_hostname"] is True

    def test_migration_repair_adds_verify_hostname_once(self, tmp_path: Path):
        db = tmp_path / "beta.db"
        run_migrations(db)
        # Simulate a 1.3.0 beta database created before the column existed
        with get_connection(db) as conn:
            conn.execute("ALTER TABLE docker_endpoints DROP COLUMN verify_hostname")
            conn.execute(
                """
                INSERT INTO docker_endpoints (name, url, source_alias, enabled, tls_enabled, created_at, updated_at)
                VALUES ('Beta TLS', 'tcp://10.0.0.8:2376', 'beta', 1, 1, datetime('now'), datetime('now'))
                """
            )
            conn.commit()

        run_migrations(db)
        run_migrations(db)

        with get_connection(db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(docker_endpoints)")]
            assert cols.count("verify_hostname") == 1
            values = {r[0] for r in conn.execute("SELECT verify_hostname FROM docker_endpoints")}
            assert values == {1}
