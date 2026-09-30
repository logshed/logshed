"""
Unit and integration tests for decoupled asynchronous FTS5 indexing,
FTSIndexWorker lifecycle, catch-up latency, and chunked batch inserts.
"""

import asyncio
import datetime
from pathlib import Path
import sqlite3
import time
import pytest
import pytest_asyncio

from app.core import pipeline as pipeline_mod
from app.core.migrations import get_connection, run_migrations
from app.core.pipeline import QueueConsumer, get_queue
from app.core.sse import sse_manager
from app.services.fts_indexer import FTSIndexWorker, index_pending_batch, index_pending_logs
from app.services.retention import execute_prune


def _make_entry(i: int, message: str = "test message", timestamp: str | None = None) -> dict:
    ts = timestamp or f"2026-09-01T12:{i // 60:02d}:{i % 60:02d}+00:00"
    return {
        "timestamp": ts,
        "received_at": ts,
        "source_ip": "10.0.0.1",
        "source_alias": "worker-host",
        "app_name": "app-service",
        "facility": 1,
        "severity": 6,
        "message": f"{message} item {i}",
        "raw": f"<14> {ts} worker-host app-service: {message} item {i}",
    }


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a migrated v2 database and return path."""
    p = tmp_path / "logs.db"
    run_migrations(p)
    return p


@pytest.fixture(autouse=True)
def reset_pipeline():
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    sse_manager.reset()
    yield
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    sse_manager.reset()


class TestFTSIndexWorkerCatchUpLatency:

    @pytest.mark.asyncio
    async def test_catch_up_latency_with_notification_under_one_second(self, db_path: Path):
        """Ingested logs are indexed and searchable via FTS within 1000ms via QueueConsumer notification."""
        worker = FTSIndexWorker(db_path, batch_size=500, poll_interval=0.5)
        consumer = QueueConsumer(db_path, debounce_seconds=0.02, fts_indexer=worker)

        worker_task = asyncio.create_task(worker.run())
        consumer_task = asyncio.create_task(consumer.run())

        q = get_queue()
        unique_token = f"speedtoken_{int(time.time() * 1000)}"

        # Enqueue 50 logs
        start_time = time.monotonic()
        for i in range(50):
            q.put_nowait(_make_entry(i, message=f"searching for {unique_token}"))

        # Poll database until all 50 logs appear in logs_fts
        indexed_count = 0
        deadline = start_time + 2.0
        while time.monotonic() < deadline:
            conn = get_connection(db_path)
            row = conn.execute(
                "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?",
                (unique_token,),
            ).fetchone()
            conn.close()
            if row and row[0] == 50:
                indexed_count = row[0]
                break
            await asyncio.sleep(0.02)

        elapsed = time.monotonic() - start_time

        await consumer.stop()
        await consumer_task
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass

        assert indexed_count == 50
        assert elapsed <= 1.0, f"Catch-up latency was {elapsed:.3f}s, expected <= 1.0s"

    @pytest.mark.asyncio
    async def test_catch_up_latency_via_polling_fallback_under_one_second(self, db_path: Path):
        """Worker automatically catches up within 1000ms using polling fallback even without notify signal."""
        worker = FTSIndexWorker(db_path, batch_size=500, poll_interval=0.4)
        worker_task = asyncio.create_task(worker.run())

        unique_token = f"fallback_{int(time.time() * 1000)}"

        # Direct SQL insert to logs table without calling worker.notify_new_logs()
        start_time = time.monotonic()
        conn = get_connection(db_path)
        with conn:
            for i in range(20):
                conn.execute(
                    """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES ('2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'host1', 'app', ?, ?)""",
                    (f"direct insert {unique_token} {i}", f"raw {i}"),
                )
        conn.close()

        # Wait for worker polling pass
        indexed_count = 0
        deadline = start_time + 2.0
        while time.monotonic() < deadline:
            conn = get_connection(db_path)
            row = conn.execute(
                "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?",
                (unique_token,),
            ).fetchone()
            conn.close()
            if row and row[0] == 20:
                indexed_count = row[0]
                break
            await asyncio.sleep(0.05)

        elapsed = time.monotonic() - start_time

        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass

        assert indexed_count == 20
        assert elapsed <= 1.0, f"Polling catch-up latency was {elapsed:.3f}s, expected <= 1.0s"


class TestFTSIndexWorkerDurableRecovery:

    @pytest.mark.asyncio
    async def test_durable_last_indexed_id_recovery_across_worker_restarts(self, db_path: Path):
        """Worker recovers progress cleanly from fts_index_state across restarts without duplicate or omitted index rows."""
        # 1. Insert 40 logs and index them with Worker 1
        worker1 = FTSIndexWorker(db_path, batch_size=100)
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 41):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"batch1 message {i}", f"raw {i}"),
                )
        conn.close()

        indexed1 = index_pending_logs(db_path)
        assert indexed1 == 40

        # Verify state
        conn = get_connection(db_path)
        state1 = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        fts_docsize1 = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        batch1_match = conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'batch1'").fetchone()[0]
        conn.close()
        assert state1 == 40
        assert fts_docsize1 == 40
        assert batch1_match == 40

        # 2. Worker 1 is stopped. Ingest 30 more logs (IDs 41 to 70) while worker is offline.
        conn = get_connection(db_path)
        with conn:
            for i in range(41, 71):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"batch2 message {i}", f"raw {i}"),
                )
        conn.close()

        # Confirm state still at 40 and newly inserted rows are not yet in FTS index
        conn = get_connection(db_path)
        assert conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0] == 40
        assert conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0] == 40
        assert conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'batch2'").fetchone()[0] == 0
        conn.close()

        # 3. Start Worker 2 (new instance). It should pick up from last_indexed_id=40 and index only 41..70.
        worker2 = FTSIndexWorker(db_path, batch_size=100)
        indexed2 = index_pending_logs(db_path)
        assert indexed2 == 30

        conn = get_connection(db_path)
        state2 = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        fts_docsize2 = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        batch2_match = conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'batch2'").fetchone()[0]
        conn.close()

        assert state2 == 70
        assert fts_docsize2 == 70
        assert batch2_match == 30

        # Verify exact row IDs in logs_fts match 1..70
        conn = get_connection(db_path)
        fts_ids = [r[0] for r in conn.execute("SELECT id FROM logs_fts_docsize ORDER BY id ASC").fetchall()]
        conn.close()
        assert fts_ids == list(range(1, 71))

    @pytest.mark.asyncio
    async def test_worker_stop_drains_pending_logs(self, db_path: Path):
        """Calling worker.stop() drains all remaining unindexed logs to logs_fts before returning."""
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 26):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"shutdown drain message {i}", f"raw {i}"),
                )
        conn.close()

        worker = FTSIndexWorker(db_path, batch_size=100)
        # Without starting worker.run(), call stop()
        await worker.stop()

        conn = get_connection(db_path)
        last_id = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        conn.close()

        assert last_id == 25
        assert fts_count == 25


class TestChunkedBatchInsertsWithReturningID:

    def test_chunked_inserts_assigns_sequential_returning_ids(self, db_path: Path):
        """QueueConsumer._insert_batch assigns valid RETURNING IDs across multiple chunk boundaries."""
        consumer = QueueConsumer(db_path)

        # Batch size 1,200 entries (will be split into chunks: 500, 500, 200)
        total_records = 1200
        batch = [_make_entry(i, message="chunked_insert_test") for i in range(total_records)]

        consumer._insert_batch(batch)

        # 1. Verify every entry in memory now has a valid integer id
        for i, entry in enumerate(batch):
            assert "id" in entry, f"Entry {i} missing 'id'"
            assert isinstance(entry["id"], int), f"Entry {i} id is not int"
            assert entry["id"] == i + 1, f"Expected id {i + 1}, got {entry['id']}"

        # 2. Verify all records exist in SQLite logs table
        conn = get_connection(db_path)
        rows = conn.execute("SELECT id, message FROM logs ORDER BY id ASC").fetchall()
        conn.close()

        assert len(rows) == total_records
        for i, row in enumerate(rows):
            assert row[0] == i + 1
            assert f"chunked_insert_test item {i}" in row[1]

    @pytest.mark.asyncio
    async def test_chunked_inserts_via_queue_and_sse_broadcast(self, db_path: Path):
        """End-to-end ingestion via QueueConsumer broadcasts entries with valid assigned IDs over SSE."""
        worker = FTSIndexWorker(db_path)
        consumer = QueueConsumer(db_path, debounce_seconds=0.02, fts_indexer=worker)

        # Subscribe to SSE broadcasts with sufficient capacity
        sse_q = await sse_manager.subscribe(maxsize=1000)

        q = get_queue()
        entry_count = 600
        for i in range(entry_count):
            q.put_nowait(_make_entry(i, message="sse_broadcast_test"))

        consumer_task = asyncio.create_task(consumer.run())

        # Collect broadcast entries from SSE queue
        received_ids = []
        try:
            while len(received_ids) < entry_count:
                item = await asyncio.wait_for(sse_q.get(), timeout=3.0)
                assert "id" in item
                assert isinstance(item["id"], int)
                received_ids.append(item["id"])
        finally:
            await sse_manager.unsubscribe(sse_q)
            await consumer.stop()
            await consumer_task

        assert len(received_ids) == entry_count
        assert received_ids == list(range(1, entry_count + 1))


class TestRetentionPruningCoordination:

    def test_retention_pruning_skips_unindexed_rows(self, db_path: Path):
        """Retention prune must skip rows that have not yet been indexed by FTSIndexWorker."""
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()

        # Insert 10 old rows directly with id 1..10, without running FTSIndexWorker
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 11):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, ?, ?, '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, old_ts, old_ts, f"old message {i}", f"raw {i}"),
                )
        conn.close()

        # Confirm fts_index_state is 0
        conn = get_connection(db_path)
        assert conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0] == 0
        conn.close()

        # Execute retention prune for > 14 days with index_pending=False to test unindexed row guard
        res1 = execute_prune(db_path, retention_days=14, index_pending=False)
        # Because id <= last_indexed_id (0), none of rows 1..10 should be deleted
        assert res1["deleted_logs"] == 0

        conn = get_connection(db_path)
        count_logs = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        conn.close()
        assert count_logs == 10

        # Now index them into FTS
        indexed = index_pending_logs(db_path)
        assert indexed == 10

        conn = get_connection(db_path)
        assert conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM logs_fts").fetchone()[0] == 10
        conn.close()

        # Now execute retention prune again
        res2 = execute_prune(db_path, retention_days=14)
        # Rows 1..10 should now be deleted cleanly
        assert res2["deleted_logs"] == 10

        conn = get_connection(db_path)
        count_after = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        fts_after = conn.execute("SELECT COUNT(*) FROM logs_fts").fetchone()[0]
        conn.close()

        assert count_after == 0
        assert fts_after == 0


class TestFTSIndexWorkerPersistentConnection:

    @pytest.mark.asyncio
    async def test_worker_reuses_persistent_connection_without_descriptor_leaks(self, db_path: Path):
        """Verify that FTSIndexWorker reuses persistent connection across iterations and cleans up on pause/close."""
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 11):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"message {i}", f"raw {i}"),
                )
        conn.close()

        worker = FTSIndexWorker(db_path, batch_size=5)
        assert worker._conn is None

        # First chunk
        c1 = worker._index_pending_chunk(5)
        assert c1 == 5
        conn1 = worker._conn
        assert conn1 is not None

        # Second chunk reuses the exact same persistent connection instance
        c2 = worker._index_pending_chunk(5)
        assert c2 == 5
        conn2 = worker._conn
        assert conn2 is conn1

        # Pausing indexing closes and clears the persistent connection for database maintenance
        await worker.pause_indexing()
        assert worker._conn is None

        # Resuming and indexing next chunk re-opens connection lazily
        worker.resume_indexing()
        c3 = worker._index_pending_chunk(5)
        assert c3 == 0
        conn3 = worker._conn
        assert conn3 is not None
        assert conn3 is not conn1

        # Stop closes connection cleanly without descriptor leaks
        await worker.stop()
        assert worker._conn is None
