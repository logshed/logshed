"""
Tests for storage metrics, retention prune worker, healthcheck, settings encryption, and host aliases.
"""

import asyncio
import datetime
import os
import shutil
import stat
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.pipeline import QueueConsumer
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    decrypt_value,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.cli import seed_logs
from app.api.deps import run_db_query
from app.collectors.docker_collector import DockerTailer, _tail_container_logs
from app.main import _supervise_worker, create_app
from app.services.retention import (
    InsufficientDiskSpaceError,
    PruneWorker,
    check_vacuum_headroom,
    execute_prune,
    execute_vacuum,
)
from app.services.storage_metrics import (
    StorageMetricsWorker,
    prune_old_metrics,
    record_metrics,
    sample_storage_metrics,
)


@pytest.fixture(autouse=True)
def reset_system_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
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

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


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


def _seed_logs(db_path: Path, entries: list[dict]):
    query = """
        INSERT INTO logs (
            timestamp, received_at, source_ip, source_alias,
            app_name, facility, severity, message, raw
        ) VALUES (
            :timestamp, :received_at, :source_ip, :source_alias,
            :app_name, :facility, :severity, :message, :raw
        )
    """
    with get_connection(db_path) as conn:
        conn.executemany(query, entries)
        conn.commit()


# ===================================================================
# 1. Storage Metrics Sampling & History
# ===================================================================

class TestStorageMetrics:

    def test_sample_returns_correct_keys(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert "recorded_at" in metrics
        assert "db_size_bytes" in metrics
        assert "disk_free_bytes" in metrics
        assert "disk_total_bytes" in metrics
        assert "total_logs_count" in metrics

    def test_db_size_bytes_positive(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert metrics["db_size_bytes"] > 0

    def test_disk_values_sane(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert metrics["disk_total_bytes"] > 0
        assert metrics["disk_free_bytes"] >= 0
        assert metrics["disk_total_bytes"] >= metrics["disk_free_bytes"]

    def test_total_logs_count_zero_initially(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert metrics["total_logs_count"] == 0

    def test_total_logs_count_after_inserts(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        for i in range(5):
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                 "10.0.0.1", "myhost", "app", 1, 6, f"msg{i}", "raw"),
            )
        conn.commit()
        conn.close()

        metrics = sample_storage_metrics(db_file)
        assert metrics["total_logs_count"] == 5

    def test_record_metrics_writes_row(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = record_metrics(db_file)

        conn = get_connection(db_file)
        rows = conn.execute("SELECT * FROM storage_metrics").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row[1] == metrics["recorded_at"]
        assert row[2] == metrics["db_size_bytes"]
        assert row[3] == metrics["disk_free_bytes"]
        assert row[4] == metrics["disk_total_bytes"]
        assert row[5] == metrics["total_logs_count"]

    def test_prune_old_metrics(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        old_time = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=31)
        ).isoformat()
        conn.execute(
            """INSERT INTO storage_metrics 
            (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
            VALUES (?, ?, ?, ?, ?)""",
            (old_time, 1000, 2000, 3000, 0),
        )
        recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO storage_metrics 
            (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
            VALUES (?, ?, ?, ?, ?)""",
            (recent_time, 1000, 2000, 3000, 0),
        )
        conn.commit()
        conn.close()

        deleted = prune_old_metrics(db_file)
        assert deleted == 1

        conn = get_connection(db_file)
        remaining = conn.execute("SELECT COUNT(*) FROM storage_metrics").fetchone()[0]
        conn.close()
        assert remaining == 1


# ===================================================================
# 2. Retention Prune Worker & Maintenance
# ===================================================================

class TestRetentionAndPruneWorker:

    @pytest.mark.asyncio
    async def test_maintenance_prune_and_storage_metrics(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        old_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).isoformat()
        fresh_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

        entries = [
            {
                "timestamp": old_time,
                "received_at": old_time,
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": "old log to prune",
                "raw": "raw old",
            },
            {
                "timestamp": fresh_time,
                "received_at": fresh_time,
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": "fresh log to keep",
                "raw": "raw fresh",
            },
        ]
        _seed_logs(db_file, entries)

        res_prune = await client.post("/api/maintenance/prune")
        assert res_prune.status_code == 200
        prune_data = res_prune.json()
        assert prune_data["status"] == "ok"
        assert prune_data["deleted_logs"] == 1
        assert "metrics" in prune_data
        assert prune_data["metrics"]["total_logs_count"] == 1

        res_storage = await client.get("/api/system/storage")
        assert res_storage.status_code == 200
        storage_data = res_storage.json()
        assert storage_data["total_logs_count"] == 1
        assert len(storage_data["history"]) >= 1

    @pytest.mark.asyncio
    async def test_prune_worker_daily_task(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        old_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).isoformat()
        entries = [
            {
                "timestamp": old_time,
                "received_at": old_time,
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": "old log pruned by worker",
                "raw": "raw old",
            }
        ]
        _seed_logs(db_file, entries)

        worker = PruneWorker(db_file)
        task = asyncio.create_task(worker.run())

        await asyncio.sleep(0.1)
        await worker.stop()
        await task

        with get_connection(db_file) as conn:
            count = conn.execute("SELECT COUNT(*) FROM logs WHERE message = 'old log pruned by worker'").fetchone()[0]
            assert count == 0

    def test_prune_retains_freelist_for_reuse(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        now = datetime.datetime.now(datetime.timezone.utc)
        old_time = (now - datetime.timedelta(days=40)).isoformat()

        # Insert 1500 logs older than retention period
        entries = [
            (old_time, old_time, f"192.168.1.{i % 250}", f"host-{i}", "app", 1, 6, f"large log payload with lots of text {i} " * 10, "raw")
            for i in range(1500)
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries
            )
            conn.commit()

        # Run execute_prune without VACUUM (retention_days=14)
        res = execute_prune(db_file, retention_days=14)
        assert res["deleted_logs"] == 1500
        assert res["status"] == "ok"

        with get_connection(db_file) as conn:
            # In SQLite WAL mode without offline VACUUM, freelist pages exist for reuse by new rows
            freelist = conn.execute("PRAGMA freelist_count;").fetchone()[0]
            assert freelist > 0

    def test_execute_prune_safety_clamps_zero_and_negative_days(self, tmp_path: Path):
        """execute_prune must clamp retention_days <= 0 to at least 1 day so recent logs are preserved."""
        db_file = tmp_path / "logs.db"
        run_migrations(db_file)

        # Seed recent logs (from today)
        with get_connection(db_file) as conn:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for i in range(10):
                conn.execute(
                    """
                    INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                    VALUES (?, ?, '192.168.1.1', 'gw', 'app', 1, 6, 'recent log', 'recent log')
                    """,
                    (now, now),
                )
            conn.commit()

        # Calling prune with 0 or negative days must clamp to 1 and NOT delete today's logs
        res_zero = execute_prune(db_file, retention_days=0)
        assert res_zero["deleted_logs"] == 0

        res_neg = execute_prune(db_file, retention_days=-5)
        assert res_neg["deleted_logs"] == 0

        with get_connection(db_file) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            assert remaining == 10

    def test_execute_prune_iso_datetime_boundary_comparison(self, tmp_path: Path):
        """execute_prune accurately compares ISO-8601 timestamps containing 'T' against cutoff_iso."""
        db_file = tmp_path / "logs.db"
        run_migrations(db_file)

        now = datetime.datetime.now(datetime.timezone.utc)
        # 15 days ago (older than 14 days retention)
        old_ts = (now - datetime.timedelta(days=15)).isoformat()
        # 13 days ago (younger than 14 days retention)
        fresh_ts = (now - datetime.timedelta(days=13)).isoformat()

        with get_connection(db_file) as conn:
            conn.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES (?, ?, '192.168.1.1', 'gw', 'app', 1, 6, 'old iso log', 'old iso log')
                """,
                (old_ts, old_ts),
            )
            conn.execute(
                """
                INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                VALUES (?, ?, '192.168.1.1', 'gw', 'app', 1, 6, 'fresh iso log', 'fresh iso log')
                """,
                (fresh_ts, fresh_ts),
            )
            conn.commit()

        res = execute_prune(db_file, retention_days=14)
        assert res["deleted_logs"] == 1

        with get_connection(db_file) as conn:
            remaining = conn.execute("SELECT message FROM logs").fetchall()
            assert len(remaining) == 1
            assert remaining[0][0] == "fresh iso log"


    @pytest.mark.asyncio
    async def test_storage_metrics_worker_manual_trigger_single_row(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        worker = StorageMetricsWorker(db_file)
        task = asyncio.create_task(worker.run())

        # Allow worker to run its initial sample
        await asyncio.sleep(0.05)

        with get_connection(db_file) as conn:
            initial_count = conn.execute("SELECT COUNT(*) FROM storage_metrics").fetchone()[0]
            assert initial_count == 1

        # Trigger manual sample
        metrics = await worker.trigger_sample()
        assert metrics is not None
        assert "recorded_at" in metrics

        # Allow background loop time to execute if it were incorrectly woken
        await asyncio.sleep(0.1)

        with get_connection(db_file) as conn:
            after_count = conn.execute("SELECT COUNT(*) FROM storage_metrics").fetchone()[0]
            # Must be exactly 2 (1 initial + 1 manual trigger), not 3 (no duplicate background sample)
            assert after_count == 2

        await worker.stop()
        await task

    def test_seed_logs_utility(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        inserted = seed_logs(count=100, days=45, db_path=str(db_file))
        assert inserted == 100

        with get_connection(db_file) as conn:
            count = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            assert count == 100
            min_ts, max_ts = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM logs").fetchone()
            assert min_ts < max_ts

    @pytest.mark.asyncio
    async def test_storage_metrics_worker_exception_backoff(self, tmp_path: Path):
        invalid_path = tmp_path / "non_existent" / "db.sqlite"
        worker = StorageMetricsWorker(invalid_path)

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await task
        assert not worker._running

    @pytest.mark.asyncio
    async def test_prune_worker_exception_backoff(self, tmp_path: Path):
        invalid_path = tmp_path / "non_existent" / "db.sqlite"
        worker = PruneWorker(invalid_path)

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await task
        assert not worker._running

    @pytest.mark.asyncio
    async def test_supervise_worker_restarts_on_error(self):
        attempts = 0

        async def failing_worker():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Simulated transient worker crash")
            await asyncio.sleep(0.5)

        task = asyncio.create_task(_supervise_worker(failing_worker, "TestWorker"))
        await asyncio.sleep(1.2)
        assert attempts >= 2
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def test_wal_checkpoint_busy_falls_back_to_passive(self, tmp_path: Path, monkeypatch, caplog):
        """
        When PRAGMA wal_checkpoint(TRUNCATE) returns busy=1, execute_prune logs a warning
        and falls back to PRAGMA wal_checkpoint(PASSIVE) without raising unhandled errors.
        """
        import logging
        import app.services.retention as ret_mod

        db_file = tmp_path / "logs.db"
        orig_get_conn = ret_mod.get_connection
        executed_statements = []

        class MockCursor:
            def __init__(self, real_cursor):
                self._real = real_cursor
                self.last_query = None

            def execute(self, sql, params=None):
                executed_statements.append(sql)
                self.last_query = sql
                if "PRAGMA wal_checkpoint(TRUNCATE)" in sql:
                    return self
                elif "PRAGMA wal_checkpoint(PASSIVE)" in sql:
                    return self
                if params is not None:
                    return self._real.execute(sql, params)
                return self._real.execute(sql)

            def fetchone(self):
                if self.last_query and "PRAGMA wal_checkpoint(TRUNCATE)" in self.last_query:
                    # Return (busy=1, log_pages=15, checkpointed_pages=5)
                    return (1, 15, 5)
                elif self.last_query and "PRAGMA wal_checkpoint(PASSIVE)" in self.last_query:
                    return (0, 15, 10)
                return self._real.fetchone()

            def __getattr__(self, name):
                return getattr(self._real, name)

        class MockConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def cursor(self):
                return MockCursor(self._real.cursor())

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(ret_mod, "get_connection", lambda p: MockConn(orig_get_conn(p)))

        with caplog.at_level(logging.WARNING):
            result = ret_mod.execute_prune(db_file, retention_days=14)

        assert result["status"] == "ok"
        # Must have attempted TRUNCATE and then fallen back to PASSIVE
        assert any("PRAGMA wal_checkpoint(TRUNCATE)" in s for s in executed_statements)
        assert any("PRAGMA wal_checkpoint(PASSIVE)" in s for s in executed_statements)
        assert any("blocked by active readers" in record.message for record in caplog.records)

    def test_wal_checkpoint_non_busy_does_not_fall_back(self, tmp_path: Path, monkeypatch):
        """
        When PRAGMA wal_checkpoint(TRUNCATE) returns busy=0, PASSIVE fallback is not triggered.
        """
        import app.services.retention as ret_mod

        db_file = tmp_path / "logs.db"
        orig_get_conn = ret_mod.get_connection
        executed_statements = []

        class MockCursor:
            def __init__(self, real_cursor):
                self._real = real_cursor
                self.last_query = None

            def execute(self, sql, params=None):
                executed_statements.append(sql)
                self.last_query = sql
                if "PRAGMA wal_checkpoint" in sql:
                    return self
                if params is not None:
                    return self._real.execute(sql, params)
                return self._real.execute(sql)

            def fetchone(self):
                if self.last_query and "PRAGMA wal_checkpoint(TRUNCATE)" in self.last_query:
                    # Succeeded without busy
                    return (0, 10, 10)
                return self._real.fetchone()

            def __getattr__(self, name):
                return getattr(self._real, name)

        class MockConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def cursor(self):
                return MockCursor(self._real.cursor())

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(ret_mod, "get_connection", lambda p: MockConn(orig_get_conn(p)))

        result = ret_mod.execute_prune(db_file, retention_days=14)
        assert result["status"] == "ok"
        assert any("PRAGMA wal_checkpoint(TRUNCATE)" in s for s in executed_statements)
        assert not any("PRAGMA wal_checkpoint(PASSIVE)" in s for s in executed_statements)

    def test_wal_checkpoint_active_reader_lock_real_sqlite(self, tmp_path: Path):
        """
        Real SQLite test: an active reader holding a read transaction during execute_prune
        is handled gracefully without raising sqlite3.OperationalError or crashing.
        """
        db_file = tmp_path / "logs.db"
        run_migrations(db_file)

        # Seed log
        with get_connection(db_file) as conn:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
                "VALUES (?, ?, '10.0.0.1', 'host', 'app', 1, 6, 'test log', 'test raw')",
                (now, now),
            )
            conn.commit()

        # Hold a read lock in a separate connection
        reader_conn = get_connection(db_file)
        try:
            reader_conn.execute("BEGIN DEFERRED;")
            reader_conn.execute("SELECT COUNT(*) FROM logs;").fetchone()

            # Run execute_prune in another connection while reader holds read transaction
            res = execute_prune(db_file, retention_days=14)
            assert res["status"] == "ok"
        finally:
            reader_conn.close()

    @pytest.mark.asyncio
    async def test_lifespan_graceful_shutdown_drains_uncommitted_logs(self, tmp_path: Path):
        """
        Integration test: When FastAPI application shuts down via lifespan context manager,
        any pending uncommitted logs in the queue are drained and committed to SQLite.
        """
        from app.main import create_app, lifespan
        from app.core.pipeline import get_queue

        db_file = tmp_path / "logs.db"
        app = create_app()

        async with lifespan(app):
            # Enqueue 15 logs directly into the pipeline queue while app is running
            q = get_queue()
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for i in range(15):
                q.put_nowait({
                    "timestamp": now,
                    "received_at": now,
                    "source_ip": "10.0.0.1",
                    "source_alias": "srv1",
                    "app_name": "lifespan_test",
                    "facility": 1,
                    "severity": 6,
                    "message": f"shutdown_lifespan_msg_{i}",
                    "raw": f"shutdown_lifespan_msg_{i}",
                })

        # Exiting lifespan triggers the shutdown sequence including queue draining
        with get_connection(db_file) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE app_name = 'lifespan_test'"
            ).fetchone()[0]
            assert count == 15

    @pytest.mark.asyncio
    async def test_lifespan_graceful_shutdown_drains_even_if_collector_stop_raises(
        self, tmp_path: Path, monkeypatch
    ):
        """
        If a collector raises an unexpected exception during stop(), lifespan shutdown
        isolates the error and guarantees that QueueConsumer.stop() drains uncommitted logs.
        """
        import app.main as main_mod
        from app.main import create_app, lifespan
        from app.core.pipeline import get_queue

        db_file = tmp_path / "logs.db"
        app = create_app()

        class FaultyTailer:
            async def stop(self):
                raise RuntimeError("Docker socket severed")

        async with lifespan(app):
            # Inject faulty collector
            monkeypatch.setattr(main_mod, "_docker_tailer", FaultyTailer())

            q = get_queue()
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for i in range(8):
                q.put_nowait({
                    "timestamp": now,
                    "received_at": now,
                    "source_ip": "10.0.0.1",
                    "source_alias": "srv1",
                    "app_name": "faulty_collector_test",
                    "facility": 1,
                    "severity": 6,
                    "message": f"faulty_collector_msg_{i}",
                    "raw": f"faulty_collector_msg_{i}",
                })

        with get_connection(db_file) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE app_name = 'faulty_collector_test'"
            ).fetchone()[0]
            assert count == 8


# ===================================================================
# 3. System Healthcheck
# ===================================================================

class TestSystemHealthcheck:

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient, auth_cookie: dict):
        # Unauthenticated request returns minimal {"status": "ok"}
        res = await client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data == {"status": "ok"}

        # Authenticated request returns detailed queue and ingest metrics
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res_auth = await client.get("/api/health")
        assert res_auth.status_code == 200
        data_auth = res_auth.json()
        assert data_auth["status"] == "ok"
        assert data_auth["db"] == "ok"
        assert isinstance(data_auth["queue_depth"], int)
        assert isinstance(data_auth["dropped_logs"], int)
        assert "ingest_rate" in data_auth
        assert isinstance(data_auth["ingest_rate"], (int, float))
        assert data_auth["ingest_rate"] >= 0.0

    @pytest.mark.asyncio
    async def test_health_check_database_failure_returns_503(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch
    ):
        from app.api import system as system_mod

        async def failing_db_query(func):
            raise sqlite3.OperationalError("database is locked or disk error")

        monkeypatch.setattr(system_mod, "run_db_query", failing_db_query)

        # Unauthenticated request returns minimal {"status": "degraded"}
        res = await client.get("/api/health")
        assert res.status_code == 503
        data = res.json()
        assert data == {"status": "degraded"}

        # Authenticated request returns detailed metrics with db error
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res_auth = await client.get("/api/health")
        assert res_auth.status_code == 503
        data_auth = res_auth.json()
        assert data_auth["status"] == "degraded"
        assert data_auth["db"] == "error"


# ===================================================================
# 4. Settings Encryption & Key Management
# ===================================================================

class TestSettingsEncryptionAndKeyManagement:

    def test_secret_key_generated_with_0600_permissions(self, tmp_path: Path, monkeypatch):
        reset_crypto_cache()
        key_file = tmp_path / "perms_test" / ".secret_key"
        monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))

        key = get_or_create_master_key(key_file)
        assert len(key) > 0
        assert key_file.exists()

        mode = stat.S_IMODE(os.stat(key_file).st_mode)
        assert mode == 0o600

    def test_secret_key_environment_variable_override(self, monkeypatch):
        reset_crypto_cache()
        override_key = "custom_super_secret_override_key_12345"
        monkeypatch.setenv("LOGSHED_SECRET_KEY", override_key)

        key = get_or_create_master_key()
        assert key is not None
        reset_crypto_cache()
        key2 = get_or_create_master_key()
        assert key == key2

    @pytest.mark.asyncio
    async def test_settings_encrypted_at_rest_and_never_exposed(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        payload = {
            "ai_provider": "openai",
            "ai_model": "gpt-4o",
            "ai_api_key": "sk-1234567890abcdef1234567890",
            "ai_base_url": "https://api.openai.com/v1",
            "retention_days": 14,
        }
        res_post = await client.post("/api/settings", json=payload)
        assert res_post.status_code == 200

        db_file = tmp_path / "logs.db"
        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, is_encrypted FROM system_settings")
            rows = {r[0]: (r[1], bool(r[2])) for r in cursor.fetchall()}

        assert rows["ai_api_key"][1] is True
        assert rows["ai_api_key"][0] != "sk-1234567890abcdef1234567890"
        assert decrypt_value(rows["ai_api_key"][0]) == "sk-1234567890abcdef1234567890"

        assert rows["retention_days"][1] is False
        assert rows["retention_days"][0] == "14"

        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        data = res_get.json()

        assert data["ai_api_key"] == "********"
        assert data["has_ai_api_key"] is True
        assert data["retention_days"] == 14
        assert data["max_retention_days"] == 30
        assert data["ai_provider"] == "openai"

        update2 = {
            "ai_provider": "gemini",
            "ai_api_key": "********",
            "retention_days": 28,
        }
        res_post2 = await client.post("/api/settings", json=update2)
        assert res_post2.status_code == 200

        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = 'ai_api_key'")
            val = cursor.fetchone()[0]
            assert decrypt_value(val) == "sk-1234567890abcdef1234567890"

        # By default, MAX_RETENTION_DAYS is 30
        res_valid = await client.post("/api/settings", json={"retention_days": 30})
        assert res_valid.status_code == 200

        res_invalid = await client.post("/api/settings", json={"retention_days": 31})
        assert res_invalid.status_code == 422

        res_invalid_low = await client.post("/api/settings", json={"retention_days": 0})
        assert res_invalid_low.status_code == 422

        res_invalid2 = await client.post("/api/settings", json={"retention_days": 365})
        assert res_invalid2.status_code == 422

    @pytest.mark.asyncio
    async def test_max_retention_days_env_override(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch: pytest.MonkeyPatch
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        monkeypatch.setenv("MAX_RETENTION_DAYS", "90")

        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        assert res_get.json()["max_retention_days"] == 90

        # With MAX_RETENTION_DAYS=90, 90 is valid but 91 is invalid
        res_valid = await client.post("/api/settings", json={"retention_days": 90})
        assert res_valid.status_code == 200

        res_invalid = await client.post("/api/settings", json={"retention_days": 91})
        assert res_invalid.status_code == 422

    @pytest.mark.asyncio
    async def test_default_retention_days_is_14(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        assert res_get.json()["retention_days"] == 14
        assert res_get.json()["max_retention_days"] == 30

    @pytest.mark.asyncio
    async def test_max_retention_days_clamps_active_retention_when_lower(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch: pytest.MonkeyPatch
    ):
        """When MAX_RETENTION_DAYS is set below default (e.g. 7), GET /api/settings must clamp retention_days to 7."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        monkeypatch.setenv("MAX_RETENTION_DAYS", "7")

        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        data = res_get.json()
        assert data["max_retention_days"] == 7
        assert data["retention_days"] == 7

        # Updating to 7 succeeds, 8 fails
        res_ok = await client.post("/api/settings", json={"retention_days": 7})
        assert res_ok.status_code == 200

        res_fail = await client.post("/api/settings", json={"retention_days": 8})
        assert res_fail.status_code == 422

    @pytest.mark.asyncio
    async def test_max_retention_days_invalid_and_boundary_values(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch: pytest.MonkeyPatch
    ):
        """Invalid strings fall back to default 30; values <= 0 are clamped to 1."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # Non-integer falls back to 30
        monkeypatch.setenv("MAX_RETENTION_DAYS", "invalid-str")
        res = await client.get("/api/settings")
        assert res.json()["max_retention_days"] == 30

        # Empty string falls back to 30
        monkeypatch.setenv("MAX_RETENTION_DAYS", "")
        res = await client.get("/api/settings")
        assert res.json()["max_retention_days"] == 30

        # Zero is clamped to 1
        monkeypatch.setenv("MAX_RETENTION_DAYS", "0")
        res = await client.get("/api/settings")
        assert res.json()["max_retention_days"] == 1
        assert res.json()["retention_days"] == 1

        # Negative is clamped to 1
        monkeypatch.setenv("MAX_RETENTION_DAYS", "-10")
        res = await client.get("/api/settings")
        assert res.json()["max_retention_days"] == 1
        assert res.json()["retention_days"] == 1

    @pytest.mark.asyncio
    async def test_retention_overridden_flag_and_reboot_fallback(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch: pytest.MonkeyPatch
    ):
        """
        Verify retention_overridden flag is True when MAX_RETENTION_DAYS is set,
        and cleanly clamps back down to 30 when the variable is removed upon reboot.
        """
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # Step 1: User previously had 7 days saved in database
        monkeypatch.delenv("MAX_RETENTION_DAYS", raising=False)
        res_set_7 = await client.post("/api/settings", json={"retention_days": 7})
        assert res_set_7.status_code == 200

        # Step 2: Override set to 60 days (slider locked in UI, so no manual POST)
        monkeypatch.setenv("MAX_RETENTION_DAYS", "60")
        res_override = await client.get("/api/settings")
        assert res_override.status_code == 200
        data_override = res_override.json()
        assert data_override["retention_overridden"] is True
        assert data_override["max_retention_days"] == 60
        assert data_override["retention_days"] == 60

        # Step 3: Remove MAX_RETENTION_DAYS (simulating container restart without env var)
        monkeypatch.delenv("MAX_RETENTION_DAYS", raising=False)
        res_reboot = await client.get("/api/settings")
        assert res_reboot.status_code == 200
        data_reboot = res_reboot.json()
        assert data_reboot["retention_overridden"] is False
        assert data_reboot["max_retention_days"] == 30
        # Automatically clamped from 60 down to 30, NOT reverting to the old 7 days
        assert data_reboot["retention_days"] == 30

    @pytest.mark.asyncio
    async def test_ai_enabled_setting_toggle_and_persistence(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # Fresh install without API key should default ai_enabled to False
        res_initial = await client.get("/api/settings")
        assert res_initial.status_code == 200
        assert res_initial.json()["ai_enabled"] is False

        # Enable AI and configure an API key
        res_enable = await client.post(
            "/api/settings",
            json={
                "ai_enabled": True,
                "ai_provider": "openai",
                "ai_api_key": "sk-test-key-12345",
            },
        )
        assert res_enable.status_code == 200

        res_check = await client.get("/api/settings")
        assert res_check.status_code == 200
        assert res_check.json()["ai_enabled"] is True

        # Disable AI
        res_disable = await client.post(
            "/api/settings",
            json={"ai_enabled": False},
        )
        assert res_disable.status_code == 200

        res_check_disabled = await client.get("/api/settings")
        assert res_check_disabled.status_code == 200
        assert res_check_disabled.json()["ai_enabled"] is False

        # Verify DB value directly
        db_file = tmp_path / "logs.db"
        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = 'ai_enabled'")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "0"

        # Re-enable AI
        res_re_enable = await client.post(
            "/api/settings",
            json={"ai_enabled": True},
        )
        assert res_re_enable.status_code == 200

        res_check_re_enabled = await client.get("/api/settings")
        assert res_check_re_enabled.status_code == 200
        assert res_check_re_enabled.json()["ai_enabled"] is True

        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = 'ai_enabled'")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "1"



# ===================================================================
# 5. Host Aliases CRUD & Retroactive Updates
# ===================================================================

class TestHostAliases:

    @pytest.mark.asyncio
    async def test_host_alias_crud_workflow(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        create_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.10", "alias": "router-primary", "notes": "Main OPNsense router"},
        )
        assert create_res.status_code == 200
        assert create_res.json()["alias"] == "router-primary"

        list_res = await client.get("/api/aliases")
        assert list_res.status_code == 200
        aliases = list_res.json()
        assert len(aliases) == 1
        assert aliases[0]["ip"] == "192.168.1.10"

        update_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.10", "alias": "router-gateway", "notes": "Updated note"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["alias"] == "router-gateway"

        del_res = await client.delete("/api/aliases/192.168.1.10")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        del_404 = await client.delete("/api/aliases/192.168.1.10")
        assert del_404.status_code == 404

    @pytest.mark.asyncio
    async def test_host_alias_whitespace_trimming(self, client: AsyncClient, auth_cookie: dict):
        """Host alias creation/update trims leading and trailing whitespace on IP and alias."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        res = await client.post(
            "/api/aliases",
            json={"ip": "  192.168.1.55  ", "alias": "  nas-backup  ", "notes": "Trim test"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ip"] == "192.168.1.55"
        assert data["alias"] == "nas-backup"

        list_res = await client.get("/api/aliases")
        assert list_res.status_code == 200
        aliases = {a["ip"]: a["alias"] for a in list_res.json()}
        assert "192.168.1.55" in aliases
        assert aliases["192.168.1.55"] == "nas-backup"

        del_res = await client.delete("/api/aliases/192.168.1.55")
        assert del_res.status_code == 200

    @pytest.mark.asyncio
    async def test_host_alias_retroactively_updates_existing_logs(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_file) as conn:
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, '192.168.1.99', '192.168.1.99', 'kernel', 1, 3, 'Link down on eth0', 'raw log 1')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, '192.168.1.99', '192.168.1.99', 'dhcp', 1, 6, 'Assigned 192.168.1.105', 'raw log 2')""",
                (now, now),
            )
            conn.commit()

        res_before = await client.get("/api/logs", params={"source": "192.168.1.99"})
        assert res_before.status_code == 200
        assert res_before.json()["total"] == 2

        create_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.99", "alias": "switch-core", "notes": "Core Managed Switch"},
        )
        assert create_res.status_code == 200

        res_alias = await client.get("/api/logs", params={"source": "switch-core"})
        assert res_alias.status_code == 200
        assert res_alias.json()["total"] == 2
        for log in res_alias.json()["logs"]:
            assert log["source_alias"] == "switch-core"
            assert log["source_ip"] == "192.168.1.99"

        update_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.99", "alias": "switch-aggregation", "notes": "Renamed switch"},
        )
        assert update_res.status_code == 200

        res_updated = await client.get("/api/logs", params={"source": "switch-aggregation"})
        assert res_updated.status_code == 200
        assert res_updated.json()["total"] == 2
        for log in res_updated.json()["logs"]:
            assert log["source_alias"] == "switch-aggregation"

        del_res = await client.delete("/api/aliases/192.168.1.99")
        assert del_res.status_code == 200

        res_reverted = await client.get("/api/logs", params={"source": "192.168.1.99"})
        assert res_reverted.status_code == 200
        assert res_reverted.json()["total"] == 2
        for log in res_reverted.json()["logs"]:
            assert log["source_alias"] == "192.168.1.99"

    @pytest.mark.asyncio
    async def test_chunked_alias_update_interleaves_with_concurrent_writes(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"
        target_ip = "192.168.10.200"

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Seed 1200 logs (more than 2 batches of 500) for target_ip
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, 'app', 1, 6, ?, 'raw')""",
                [(now, now, target_ip, target_ip, f"log msg {i}") for i in range(1200)],
            )
            conn.commit()

        concurrent_inserted = []
        stop_concurrent = asyncio.Event()

        # Concurrent background writer inserting logs while alias update runs
        async def concurrent_writer():
            writer_idx = 0
            while not stop_concurrent.is_set():
                def _write(conn):
                    c = conn.cursor()
                    c.execute(
                        """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                           VALUES (?, ?, '10.99.99.99', 'other-host', 'writer', 1, 6, ?, 'raw')""",
                        (now, now, f"concurrent msg {writer_idx}"),
                    )
                    conn.commit()
                await run_db_query(_write)
                concurrent_inserted.append(writer_idx)
                writer_idx += 1
                await asyncio.sleep(0.002)

        writer_task = asyncio.create_task(concurrent_writer())

        try:
            # Upsert alias: retroactively updates 1200 rows in chunks of 500
            res = await client.post(
                "/api/aliases",
                json={"ip": target_ip, "alias": "chunked-switch", "notes": "Chunked test"},
            )
            assert res.status_code == 200
        finally:
            stop_concurrent.set()
            await writer_task

        # Ensure concurrent writer succeeded without lock errors and inserted records
        assert len(concurrent_inserted) > 0

        # Verify all 1200 logs were updated to chunked-switch
        with get_connection(db_file) as conn:
            updated_count = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE source_ip = ? AND source_alias = 'chunked-switch'",
                (target_ip,),
            ).fetchone()[0]
            assert updated_count == 1200

        # Test delete alias chunked update with concurrent writes
        stop_concurrent.clear()
        concurrent_deleted = []

        async def concurrent_writer_2():
            w_idx = 0
            while not stop_concurrent.is_set():
                def _write(conn):
                    c = conn.cursor()
                    c.execute(
                        """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                           VALUES (?, ?, '10.99.99.99', 'other-host', 'writer', 1, 6, ?, 'raw')""",
                        (now, now, f"concurrent delete msg {w_idx}"),
                    )
                    conn.commit()
                await run_db_query(_write)
                concurrent_deleted.append(w_idx)
                w_idx += 1
                await asyncio.sleep(0.002)

        writer_task_2 = asyncio.create_task(concurrent_writer_2())
        try:
            del_res = await client.delete(f"/api/aliases/{target_ip}")
            assert del_res.status_code == 200
        finally:
            stop_concurrent.set()
            await writer_task_2

        assert len(concurrent_deleted) > 0

        # Verify all 1200 logs reverted to target_ip
        with get_connection(db_file) as conn:
            reverted_count = conn.execute(
                "SELECT COUNT(*) FROM logs WHERE source_ip = ? AND source_alias = ?",
                (target_ip, target_ip),
            ).fetchone()[0]
            assert reverted_count == 1200

    @pytest.mark.asyncio
    async def test_docker_host_alias_crud_retroactive_and_collector_sync(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """End-to-end: Docker alias created via API retroactively updates DB and synchronizes DockerTailer."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Seed initial Docker logs
        with get_connection(db_file) as conn:
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, 'docker', 'docker', 'caddy', 1, 6, 'Serving HTTP traffic', 'raw 1')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, 'docker', 'docker', 'nextcloud', 1, 6, 'User login', 'raw 2')""",
                (now, now),
            )
            conn.commit()

        # Instantiate DockerTailer pointing to this db
        assembler = pipeline_mod.KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, db_path=db_file)
        assert tailer.alias_cache.resolve("docker") == "docker"

        # 1. Verify before alias creation: source=docker finds both
        res_before = await client.get("/api/logs", params={"source": "docker"})
        assert res_before.status_code == 200
        assert res_before.json()["total"] == 2

        # 2. Create alias via API: 'docker' -> 'docker-unraid'
        create_res = await client.post(
            "/api/aliases",
            json={"ip": "docker", "alias": "docker-unraid", "notes": "Unraid Docker Host"},
        )
        assert create_res.status_code == 200
        assert create_res.json()["alias"] == "docker-unraid"

        # 3. Verify existing logs in DB were retroactively updated to docker-unraid
        res_alias = await client.get("/api/logs", params={"source": "docker-unraid"})
        assert res_alias.status_code == 200
        assert res_alias.json()["total"] == 2
        for item in res_alias.json()["logs"]:
            assert item["source_alias"] == "docker-unraid"
            assert item["source_ip"] == "docker"

        # 4. Verify DockerTailer's alias cache was immediately reloaded
        assert tailer.alias_cache.resolve("docker") == "docker-unraid"

        # 5. Ingest a new Docker log line: verify it adopts 'docker-unraid'
        captured_entries = []
        async def capture_feed(key, entry):
            captured_entries.append(entry)
        assembler.feed = capture_feed

        cancel_event = asyncio.Event()
        ts = "2026-09-08T08:52:35.000000000Z"
        msg = f"{ts} Live incoming log line\n".encode("utf-8")
        payload = b"\x01\x00\x00\x00" + len(msg).to_bytes(4, "big") + msg

        class MockResp:
            def raise_for_status(self):
                pass
            async def aiter_bytes(self):
                yield payload
                cancel_event.set()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()
            def stream(self, method, url, **kwargs):
                return MockResp()

        await _tail_container_logs(
            MockClient(), "cid_live", "app_live",
            assembler, cancel_event,
            alias_cache=tailer.alias_cache,
            heartbeat_interval=0,
        )

        assert len(captured_entries) == 1
        assert captured_entries[0]["source_ip"] == "docker"
        assert captured_entries[0]["source_alias"] == "docker-unraid"

        # 6. Delete the alias via API
        del_res = await client.delete("/api/aliases/docker")
        assert del_res.status_code == 200

        # Verify logs in DB reverted to 'docker'
        res_reverted = await client.get("/api/logs", params={"source": "docker"})
        assert res_reverted.status_code == 200
        assert res_reverted.json()["total"] == 2
        for item in res_reverted.json()["logs"]:
            assert item["source_alias"] == "docker"

        # Verify DockerTailer's cache reverted to 'docker'
        assert tailer.alias_cache.resolve("docker") == "docker"

        # 7. Test container restart persistence:
        # Create alias again
        await client.post(
            "/api/aliases",
            json={"ip": "docker", "alias": "docker-unraid"},
        )
        await tailer.stop()

        # Restart LogShed: new DockerTailer instance
        tailer_restarted = DockerTailer(assembler, db_path=db_file)
        assert tailer_restarted.alias_cache.resolve("docker") == "docker-unraid"
        await tailer_restarted.stop()

    @pytest.mark.asyncio
    async def test_host_alias_ip_validation(self, client: AsyncClient, auth_cookie: dict):
        """HostAliasCreate validates IP address format and rejects malformed values with 422."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # Valid IPv4
        res_ipv4 = await client.post(
            "/api/aliases",
            json={"ip": "192.168.10.50", "alias": "valid-ipv4"},
        )
        assert res_ipv4.status_code == 200
        assert res_ipv4.json()["ip"] == "192.168.10.50"

        # Valid IPv6
        res_ipv6 = await client.post(
            "/api/aliases",
            json={"ip": "2001:db8::1", "alias": "valid-ipv6"},
        )
        assert res_ipv6.status_code == 200
        assert res_ipv6.json()["ip"] == "2001:db8::1"

        # Valid Docker key
        res_docker = await client.post(
            "/api/aliases",
            json={"ip": "docker", "alias": "docker-host"},
        )
        assert res_docker.status_code == 200
        assert res_docker.json()["ip"] == "docker"

        # Invalid formats must return 422
        invalid_ips = ["not-an-ip", "999.999.999.999", "1.2.3.4.5", "http://bad"]
        for bad_ip in invalid_ips:
            res_bad = await client.post(
                "/api/aliases",
                json={"ip": bad_ip, "alias": "should-fail"},
            )
            assert res_bad.status_code == 422
            assert "Invalid IP address format" in res_bad.json()["detail"]

    def test_batch_update_log_aliases_orders_by_id_desc(self, tmp_path: Path, monkeypatch):
        """_batch_update_log_aliases processes the newest logs (highest id) first."""
        from app.api.aliases import _batch_update_log_aliases
        db_file = tmp_path / "logs.db"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, '192.168.1.50', '192.168.1.50', 'app', 1, 6, 'msg', 'raw')""",
                [(i, now, now) for i in [10, 20, 30, 40]],
            )
            conn.commit()

            sleep_called = False

            def mock_sleep(_duration):
                nonlocal sleep_called
                if not sleep_called:
                    sleep_called = True
                    # After first batch of 2 rows, IDs 40 and 30 should be updated, 10 and 20 not yet
                    c = conn.cursor()
                    c.execute("SELECT id, source_alias FROM logs ORDER BY id ASC")
                    rows = c.fetchall()
                    row_map = {r[0]: r[1] for r in rows}
                    assert row_map[40] == "target-alias"
                    assert row_map[30] == "target-alias"
                    assert row_map[20] == "192.168.1.50"
                    assert row_map[10] == "192.168.1.50"

            monkeypatch.setattr("time.sleep", mock_sleep)
            _batch_update_log_aliases(conn, "192.168.1.50", "target-alias", batch_size=2)
            assert sleep_called is True

            # All 4 rows should now be updated
            c = conn.cursor()
            c.execute("SELECT id, source_alias FROM logs ORDER BY id ASC")
            rows = c.fetchall()
            for r in rows:
                assert r[1] == "target-alias"

    @pytest.mark.asyncio
    async def test_background_alias_update_logs_cleanly_on_db_error(self, client: AsyncClient, auth_cookie: dict, caplog):
        from unittest.mock import patch
        import logging
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        with patch("app.api.aliases._batch_update_log_aliases", side_effect=Exception("Simulated SQLite lock error")):
            with caplog.at_level(logging.ERROR):
                create_res = await client.post(
                    "/api/aliases",
                    json={"ip": "192.168.1.100", "alias": "failing-alias", "notes": "Test fail"},
                )
                assert create_res.status_code == 200
                assert "Background retroactive alias update failed for 192.168.1.100 -> failing-alias: Simulated SQLite lock error" in caplog.text

    @pytest.mark.asyncio
    async def test_background_alias_delete_logs_cleanly_on_db_error(self, client: AsyncClient, auth_cookie: dict, caplog):
        from unittest.mock import patch
        import logging
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        create_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.101", "alias": "delete-fail-alias"},
        )
        assert create_res.status_code == 200

        with patch("app.api.aliases._batch_update_log_aliases", side_effect=Exception("Simulated disk I/O failure")):
            with caplog.at_level(logging.ERROR):
                del_res = await client.delete("/api/aliases/192.168.1.101")
                assert del_res.status_code == 200
                assert "Background retroactive alias reversion failed for 192.168.1.101: Simulated disk I/O failure" in caplog.text


# ===================================================================
# 6. Database Compaction (Vacuum)
# ===================================================================

class TestDatabaseVacuum:
    """Tests for on-demand database vacuum compaction and headroom protection."""

    @pytest.mark.asyncio
    async def test_vacuum_requires_authentication(self, client: AsyncClient):
        """Unauthenticated requests to /api/system/vacuum are rejected with 401."""
        res = await client.post("/api/system/vacuum")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_vacuum_insufficient_disk_space_guard(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch
    ):
        """Vacuum fails with 400 when free disk headroom is below database size plus safe margin."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # Mock shutil.disk_usage to return a small amount of free bytes (e.g. 10 MB)
        from collections import namedtuple
        Usage = namedtuple("Usage", ["total", "used", "free"])
        monkeypatch.setattr(
            "shutil.disk_usage",
            lambda path: Usage(total=10**10, used=10**10 - 10 * 1024 * 1024, free=10 * 1024 * 1024),
        )

        res = await client.post("/api/system/vacuum")
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "Insufficient temporary disk headroom" in detail

    @pytest.mark.asyncio
    async def test_vacuum_endpoint_success_and_metrics_update(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        """Vacuum successfully repacks the database file and records fresh storage metrics."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        # Seed 1000 logs, then delete 900 of them to generate freelist pages
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        entries = [
            {
                "timestamp": now,
                "received_at": now,
                "source_ip": "192.168.1.100",
                "source_alias": "test-host",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": f"Log entry {i} padding " + ("x" * 500),
                "raw": f"raw {i}",
            }
            for i in range(1000)
        ]
        _seed_logs(db_file, entries)

        # Delete most logs to create empty freelist pages
        with get_connection(db_file) as conn:
            conn.execute("DELETE FROM logs WHERE id > 100")
            conn.commit()

        res = await client.post("/api/system/vacuum")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["previous_size_bytes"] > 0
        assert data["new_size_bytes"] > 0
        assert data["reclaimed_bytes"] >= 0
        assert "metrics" in data
        metrics = data["metrics"]
        assert metrics["db_size_bytes"] == data["new_size_bytes"]
        assert metrics["total_logs_count"] == 100

    @pytest.mark.asyncio
    async def test_vacuum_pauses_and_resumes_queue_consumer(self, tmp_path: Path):
        """Vacuum pauses QueueConsumer writes while buffering incoming logs in memory."""
        db_file = tmp_path / "logs.db"
        consumer = QueueConsumer(db_file)
        assert not consumer.is_paused

        # Run consumer in background task
        task = asyncio.create_task(consumer.run())
        try:
            # Pause writes
            await consumer.pause_writes()
            assert consumer.is_paused

            # Put logs into queue while paused
            queue = pipeline_mod.get_queue()
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            test_entry = {
                "timestamp": now,
                "received_at": now,
                "source_ip": "10.0.0.1",
                "source_alias": "buffered-host",
                "app_name": "test-app",
                "facility": 1,
                "severity": 6,
                "message": "Buffered message during vacuum",
                "raw": "Buffered message during vacuum",
            }
            queue.put_nowait(test_entry)

            # Check that log was NOT inserted into db while paused
            await asyncio.sleep(0.1)
            with get_connection(db_file) as conn:
                count = conn.execute("SELECT COUNT(*) FROM logs WHERE app_name = 'test-app'").fetchone()[0]
                assert count == 0
                assert queue.qsize() == 1

            # Resume writes
            consumer.resume_writes()
            assert not consumer.is_paused

            # Wait for consumer to drain buffered log to database
            for _ in range(20):
                await asyncio.sleep(0.05)
                with get_connection(db_file) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM logs WHERE app_name = 'test-app'").fetchone()[0]
                    if count == 1:
                        break

            with get_connection(db_file) as conn:
                count = conn.execute("SELECT COUNT(*) FROM logs WHERE app_name = 'test-app'").fetchone()[0]
                assert count == 1
        finally:
            await consumer.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_vacuum_conflict_when_already_running(
        self, client: AsyncClient, auth_cookie: dict, monkeypatch
    ):
        """Concurrent vacuum requests return 409 Conflict."""
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        async def _delayed_vacuum(*args, **kwargs):
            await asyncio.sleep(0.3)
            return {
                "status": "ok",
                "previous_size_bytes": 1000,
                "new_size_bytes": 800,
                "reclaimed_bytes": 200,
                "metrics": {
                    "recorded_at": "2026-09-28T00:00:00Z",
                    "db_size_bytes": 800,
                    "disk_free_bytes": 1000000000,
                    "disk_total_bytes": 2000000000,
                    "total_logs_count": 0,
                },
            }

        monkeypatch.setattr("app.api.system.execute_vacuum", _delayed_vacuum)

        res1, res2 = await asyncio.gather(
            client.post("/api/system/vacuum"),
            client.post("/api/system/vacuum"),
        )

        statuses = {res1.status_code, res2.status_code}
        assert 200 in statuses
        assert 409 in statuses

    def test_check_vacuum_headroom_helper(self, tmp_path: Path, monkeypatch):
        """check_vacuum_headroom verifies disk headroom against safe margin."""
        from collections import namedtuple
        db_file = tmp_path / "logs.db"
        Usage = namedtuple("Usage", ["total", "used", "free"])

        # Sufficient headroom
        monkeypatch.setattr(
            "shutil.disk_usage",
            lambda p: Usage(total=10**10, used=10**9, free=9 * 10**9),
        )
        db_size, free, required = check_vacuum_headroom(db_file, safe_margin_bytes=100 * 1024 * 1024)
        assert free >= required

        # Insufficient headroom
        monkeypatch.setattr(
            "shutil.disk_usage",
            lambda p: Usage(total=10**10, used=10**10 - 1000, free=1000),
        )
        with pytest.raises(InsufficientDiskSpaceError) as exc_info:
            check_vacuum_headroom(db_file, safe_margin_bytes=100 * 1024 * 1024)
        assert "Insufficient temporary disk headroom" in str(exc_info.value)




