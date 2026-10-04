"""
Adversarial stress harness for Milestone 2:
- FTS catch-up latency verification under load (must be <= 1000ms)
- Crash resilience and worker recovery across restarts with partial batches
- Unindexed deletion safety (confirm NO database disk image is malformed)
- Mixed boundary deletions and updates across indexed and unindexed boundaries
- FTS search consistency middleware and retention coordination under stress
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


def _make_entry(i: int, message: str = "adversarial log", timestamp: str | None = None) -> dict:
    ts = timestamp or f"2026-09-01T12:{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}+00:00"
    return {
        "timestamp": ts,
        "received_at": ts,
        "source_ip": "192.168.1.100",
        "source_alias": "adversarial-host",
        "app_name": "stress-app",
        "facility": 1,
        "severity": 6,
        "message": f"{message} payload {i}",
        "raw": f"<14> {ts} adversarial-host stress-app: {message} payload {i}",
    }


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a freshly migrated v2 database."""
    p = tmp_path / "adversarial_logs.db"
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


# ===================================================================
# 1. Catch-Up Latency & Throughput Stress Tests
# ===================================================================

class TestAdversarialFTSLatency:

    @pytest.mark.asyncio
    async def test_latency_500_logs_burst_under_1000ms(self, db_path: Path):
        """Verify 500 logs burst catch-up latency is strictly <= 1000ms with notification."""
        worker = FTSIndexWorker(db_path, batch_size=1000, poll_interval=0.5)
        consumer = QueueConsumer(db_path, debounce_seconds=0.02, fts_indexer=worker)

        worker_task = asyncio.create_task(worker.run())
        consumer_task = asyncio.create_task(consumer.run())

        q = get_queue()
        unique_token = f"burst500_{int(time.time() * 1000)}"
        total_logs = 500

        start_time = time.monotonic()
        for i in range(total_logs):
            q.put_nowait(_make_entry(i, message=f"burst testing token {unique_token}"))

        # Poll database until all 500 logs appear in logs_fts
        indexed_count = 0
        deadline = start_time + 3.0
        while time.monotonic() < deadline:
            conn = get_connection(db_path)
            row = conn.execute(
                "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?",
                (unique_token,),
            ).fetchone()
            conn.close()
            if row and row[0] == total_logs:
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

        assert indexed_count == total_logs
        assert elapsed <= 1.0, f"Catch-up latency was {elapsed:.3f}s, expected <= 1.0s"

    @pytest.mark.asyncio
    async def test_polling_fallback_latency_under_1000ms(self, db_path: Path):
        """Worker with default 0.5s poll_interval catches up within 1000ms with NO notification."""
        worker = FTSIndexWorker(db_path, batch_size=500, poll_interval=0.5)
        worker_task = asyncio.create_task(worker.run())

        unique_token = f"pollfallback_{int(time.time() * 1000)}"
        total_logs = 100

        start_time = time.monotonic()
        conn = get_connection(db_path)
        with conn:
            for i in range(total_logs):
                conn.execute(
                    """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES ('2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (f"polling log {unique_token} item {i}", f"raw {i}"),
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
            if row and row[0] == total_logs:
                indexed_count = row[0]
                break
            await asyncio.sleep(0.02)

        elapsed = time.monotonic() - start_time

        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass

        assert indexed_count == total_logs
        assert elapsed <= 1.0, f"Polling fallback latency was {elapsed:.3f}s, expected <= 1.0s"

    @pytest.mark.asyncio
    async def test_high_volume_multi_batch_indexing(self, db_path: Path):
        """Ingest 2,500 logs across multiple batches; verify worker drains completely without backpressure stall."""
        worker = FTSIndexWorker(db_path, batch_size=500, poll_interval=0.2)
        consumer = QueueConsumer(db_path, debounce_seconds=0.02, fts_indexer=worker)

        worker_task = asyncio.create_task(worker.run())
        consumer_task = asyncio.create_task(consumer.run())

        q = get_queue()
        unique_token = f"multibatch_{int(time.time() * 1000)}"
        total_logs = 2500

        for i in range(total_logs):
            q.put_nowait(_make_entry(i, message=f"multibatch token {unique_token}"))

        # Poll database until all 2500 logs appear in logs_fts
        start_time = time.monotonic()
        deadline = start_time + 10.0
        indexed_count = 0
        while time.monotonic() < deadline:
            conn = get_connection(db_path)
            row = conn.execute(
                "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?",
                (unique_token,),
            ).fetchone()
            conn.close()
            if row and row[0] == total_logs:
                indexed_count = row[0]
                break
            await asyncio.sleep(0.05)

        await consumer.stop()
        await consumer_task
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass

        assert indexed_count == total_logs

        # Verify state integrity
        conn = get_connection(db_path)
        last_id = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        max_log_id = conn.execute("SELECT MAX(id) FROM logs").fetchone()[0]
        conn.close()
        assert last_id == max_log_id == total_logs


# ===================================================================
# 2. Crash Resilience & Worker Recovery Tests
# ===================================================================

class TestAdversarialCrashRecovery:

    @pytest.mark.asyncio
    async def test_abrupt_worker_cancellation_mid_backlog_resumes_cleanly(self, db_path: Path):
        """Simulate worker process crash mid-indexing: restart worker and verify zero duplicate or missing FTS rows."""
        total_logs = 1500
        conn = get_connection(db_path)
        with conn:
            for i in range(1, total_logs + 1):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"crash test item {i}", f"raw {i}"),
                )
        conn.close()

        # Step 1: Start Worker 1 with small batch_size=300 and deterministic sync on first chunk
        worker1 = FTSIndexWorker(db_path, batch_size=300, poll_interval=0.01)
        first_chunk_indexed = asyncio.Event()
        original_chunk_fn = worker1._index_pending_chunk
        test_loop = asyncio.get_running_loop()

        def intercepted_chunk(bs):
            res = original_chunk_fn(bs)
            test_loop.call_soon_threadsafe(first_chunk_indexed.set)
            return res

        worker1._index_pending_chunk = intercepted_chunk
        worker1_task = asyncio.create_task(worker1.run())

        # Wait for worker1 to index exactly 1 chunk (300 rows)
        await asyncio.wait_for(first_chunk_indexed.wait(), timeout=3.0)

        # Abruptly cancel worker1 task without calling worker1.stop() (simulates ungraceful crash)
        worker1_task.cancel()
        try:
            await worker1_task
        except (asyncio.CancelledError, Exception):
            pass

        # Inspect intermediate state
        conn = get_connection(db_path)
        inter_state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        inter_fts_count = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        conn.close()

        assert inter_state == inter_fts_count, (
            f"Desync between last_indexed_id ({inter_state}) and FTS row count ({inter_fts_count})"
        )
        assert inter_state < total_logs, f"Expected partial indexing, but reached {inter_state}"

        # Step 2: Start Worker 2 (new worker instance after simulated crash)
        worker2 = FTSIndexWorker(db_path, batch_size=400, poll_interval=0.01)
        worker2_task = asyncio.create_task(worker2.run())

        # Wait for worker2 to finish remaining logs
        deadline = time.monotonic() + 5.0
        final_state = 0
        while time.monotonic() < deadline:
            conn = get_connection(db_path)
            final_state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
            conn.close()
            if final_state == total_logs:
                break
            await asyncio.sleep(0.02)

        await worker2.stop()
        worker2_task.cancel()
        try:
            await worker2_task
        except (asyncio.CancelledError, Exception):
            pass

        assert final_state == total_logs

        # Step 3: Deep inspection of logs_fts integrity
        conn = get_connection(db_path)
        fts_count = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        fts_match_count = conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'crash'").fetchone()[0]
        doc_ids = [r[0] for r in conn.execute("SELECT id FROM logs_fts_docsize ORDER BY id ASC").fetchall()]
        conn.close()

        # Exact row count matches
        assert fts_count == total_logs, f"Expected {total_logs} in logs_fts_docsize, got {fts_count}"
        assert fts_match_count == total_logs, f"Expected {total_logs} MATCH hits, got {fts_match_count}"

        # No duplicate or missing IDs: doc_ids must exactly equal range 1..1500
        assert doc_ids == list(range(1, total_logs + 1)), "Detected gap or duplicate in FTS indexed rowids!"

    def test_repeated_index_pending_logs_is_idempotent(self, db_path: Path):
        """Calling index_pending_logs multiple times when up-to-date returns 0 and does not duplicate records."""
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 51):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"idempotent message {i}", f"raw {i}"),
                )
        conn.close()

        first_run = index_pending_logs(db_path, batch_size=20)
        assert first_run == 50

        # Run 3 more times immediately
        for _ in range(3):
            subsequent_run = index_pending_logs(db_path, batch_size=20)
            assert subsequent_run == 0

        conn = get_connection(db_path)
        state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        conn.close()

        assert state == 50
        assert fts_count == 50


# ===================================================================
# 3. Unindexed Deletion & Modification Safety Tests
# ===================================================================

class TestAdversarialUnindexedSafety:

    def test_delete_unindexed_rows_causes_no_disk_corruption(self, db_path: Path):
        """Deleting multiple unindexed rows must not trigger sqlite3.DatabaseError: database disk image is malformed."""
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 101):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"unindexed row {i}", f"raw {i}"),
                )
        conn.close()

        # Delete rows 10 to 30 while still unindexed
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM logs WHERE id BETWEEN 10 AND 30")
        except sqlite3.DatabaseError as e:
            pytest.fail(f"Deleting unindexed rows raised DatabaseError: {e}")
        conn.close()

        # Verify FTS virtual table integrity check passes
        conn = get_connection(db_path)
        integrity_check = conn.execute("PRAGMA integrity_check").fetchall()
        assert integrity_check == [("ok",)]

        # Run FTS indexer on remaining rows
        indexed = index_pending_logs(db_path)
        assert indexed == 79  # 100 - 21 deleted rows = 79

        # Verify FTS integrity again
        fts_count = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        conn.close()

        assert fts_count == 79
        assert state == 100

    def test_mixed_delete_across_indexed_and_unindexed_boundary(self, db_path: Path):
        """Deleting rows spanning both indexed and unindexed ranges executes cleanly without FTS errors."""
        # 1. Insert 100 rows
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 101):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"boundary test message {i}", f"raw {i}"),
                )
        conn.close()

        # 2. Index first 50 rows only
        conn = get_connection(db_path)
        indexed_50 = index_pending_batch(conn, batch_size=50)
        assert indexed_50 == 50
        state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        assert state == 50
        conn.close()

        # 3. Delete rows 40 to 60 (spanning indexed rows 40..50 and unindexed rows 51..60)
        conn = get_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM logs WHERE id BETWEEN 40 AND 60")
        except sqlite3.DatabaseError as e:
            pytest.fail(f"Mixed boundary delete raised DatabaseError: {e}")
        conn.close()

        # 4. Verify logs_ad correctly removed rows 40..50 from FTS, leaving rows 1..39 in FTS
        conn = get_connection(db_path)
        fts_ids = [r[0] for r in conn.execute("SELECT id FROM logs_fts_docsize ORDER BY id ASC").fetchall()]
        conn.close()
        assert fts_ids == list(range(1, 40))

        # 5. Index remaining unindexed rows (which are 61..100)
        indexed_rem = index_pending_logs(db_path)
        assert indexed_rem == 40  # 100 - 60 = 40 rows

        conn = get_connection(db_path)
        final_state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        final_fts_ids = [r[0] for r in conn.execute("SELECT id FROM logs_fts_docsize ORDER BY id ASC").fetchall()]
        fts_match_count = conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'boundary'").fetchone()[0]
        conn.close()

        assert final_state == 100
        # Remaining rows should be 1..39 and 61..100 = 79 rows total
        expected_ids = list(range(1, 40)) + list(range(61, 101))
        assert final_fts_ids == expected_ids
        assert fts_match_count == 79

    def test_mixed_update_across_indexed_and_unindexed_boundary(self, db_path: Path):
        """Updating rows spanning both indexed and unindexed ranges maintains accurate FTS data."""
        # 1. Insert 40 rows
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 41):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, f"initial content {i}", f"raw {i}"),
                )
        conn.close()

        # 2. Index first 20 rows
        conn = get_connection(db_path)
        indexed_20 = index_pending_batch(conn, batch_size=20)
        assert indexed_20 == 20
        conn.close()

        # 3. Update rows 15 to 25 (spans 15..20 indexed, 21..25 unindexed)
        conn = get_connection(db_path)
        with conn:
            conn.execute(
                "UPDATE logs SET message = 'overwritten_quantum_keyword' WHERE id BETWEEN 15 AND 25"
            )
        conn.close()

        # 4. Verify indexed rows 15..20 reflect update immediately in FTS via logs_au
        conn = get_connection(db_path)
        match_immediate = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'overwritten_quantum_keyword' ORDER BY rowid ASC"
        ).fetchall()
        conn.close()
        assert [r[0] for r in match_immediate] == list(range(15, 21))

        # 5. Run indexer for remaining rows 21..40
        indexed_rem = index_pending_logs(db_path)
        assert indexed_rem == 20

        # 6. Verify all updated rows 15..25 now match the keyword in FTS
        conn = get_connection(db_path)
        match_all = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'overwritten_quantum_keyword' ORDER BY rowid ASC"
        ).fetchall()
        conn.close()
        assert [r[0] for r in match_all] == list(range(15, 26))


# ===================================================================
# 4. Retention Pruning Edge Cases
# ===================================================================

class TestAdversarialRetentionPruning:

    def test_retention_prune_never_deletes_unindexed_logs(self, db_path: Path):
        """Even if all logs are older than retention_days, unindexed logs are preserved when index_pending=False."""
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).isoformat()
        conn = get_connection(db_path)
        with conn:
            for i in range(1, 51):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, ?, ?, '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, old_ts, old_ts, f"expired unindexed log {i}", f"raw {i}"),
                )
        conn.close()

        # Attempt retention pruning with index_pending=False
        result = execute_prune(db_path, retention_days=14, index_pending=False)
        assert result["deleted_logs"] == 0

        conn = get_connection(db_path)
        remaining = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        conn.close()
        assert remaining == 51 - 1

        # Now execute with index_pending=True (default production behavior)
        result_full = execute_prune(db_path, retention_days=14, index_pending=True)
        assert result_full["deleted_logs"] == 50

        conn = get_connection(db_path)
        final_logs = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        final_fts = conn.execute("SELECT COUNT(*) FROM logs_fts").fetchone()[0]
        final_docsize = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        conn.close()

        assert final_logs == 0
        assert final_fts == 0
        assert final_docsize == 0


# ===================================================================
# 5. Middleware, Special Tokens & Concurrency Stress
# ===================================================================

class TestAdversarialMiddlewareAndConcurrency:

    @pytest.mark.asyncio
    async def test_search_consistency_middleware_indexes_on_query(self, db_path: Path, monkeypatch):
        """HTTP GET /api/logs?query= signals running FTSIndexWorker via notify_new_logs without opening competing transactions."""
        from unittest.mock import MagicMock
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key
        from app.services.fts_indexer import set_fts_worker

        # Configure app to use the test database and secret key
        key_file = db_path.parent / ".secret_key"
        get_or_create_master_key(key_file)
        monkeypatch.setenv("DATA_DIR", str(db_path.parent))
        monkeypatch.setenv("DB_PATH", str(db_path))
        monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))

        unique_search_term = f"middlewaretest_{int(time.time() * 1000)}"

        # 1. Verify signaling with mock worker
        mock_worker = MagicMock()
        set_fts_worker(mock_worker)
        try:
            token = create_session_token(user_id=1)
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={SESSION_COOKIE_NAME: token},
                headers={"X-Requested-With": "XMLHttpRequest"},
            ) as client:
                # Query without search query should not notify
                resp = await client.get("/api/logs")
                assert resp.status_code == 200
                assert not mock_worker.notify_new_logs.called

                # Query with search query should notify background worker
                resp = await client.get(f"/api/logs?query={unique_search_term}")
                assert resp.status_code == 200
                assert mock_worker.notify_new_logs.called
        finally:
            set_fts_worker(None)

        # 2. Verify with live worker: notification wakes worker to index pending logs
        worker = FTSIndexWorker(db_path, batch_size=1000, poll_interval=10.0)
        worker_task = asyncio.create_task(worker.run())
        try:
            # Insert directly into logs table without indexing
            conn = get_connection(db_path)
            with conn:
                conn.execute(
                    """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES ('2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'host1', 'auth-srv', ?, ?)""",
                    (f"user login event {unique_search_term} successful", "raw"),
                )
            conn.close()

            # Confirm unindexed prior to HTTP request
            conn = get_connection(db_path)
            last_id_before = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
            conn.close()
            assert last_id_before == 0

            token = create_session_token(user_id=1)
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={SESSION_COOKIE_NAME: token},
                headers={"X-Requested-With": "XMLHttpRequest"},
            ) as client:
                resp = await client.get(f"/api/logs?query={unique_search_term}")
                assert resp.status_code == 200

            # Worker was signaled by middleware; wait for background indexing to complete
            last_id_after = 0
            for _ in range(50):
                conn = get_connection(db_path)
                last_id_after = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
                conn.close()
                if last_id_after >= 1:
                    break
                await asyncio.sleep(0.02)
            assert last_id_after >= 1
        finally:
            await worker.stop()
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
            set_fts_worker(None)

    def test_special_syntax_tokens_and_unicode_indexing(self, db_path: Path):
        """FTS indexing handles unicode, emojis, unbalanced quotes, and FTS syntax characters safely."""
        complex_payloads = [
            "normal message",
            'quotes: "unbalanced double quote',
            "single: 'unbalanced single quote",
            "boolean operators: AND OR NOT NEAR inside payload",
            "parentheses: (open and ((nested) unclosed",
            "wildcards: * * * and stars **",
            "unicode emojis: 🚀🔥💥⚡️ log alert from container",
            "CJK characters: 系统异常 警告 数据库连接失败",
            "sql injection payload: '; DROP TABLE logs; --",
            "null-byte representation: \\x00 and null \0 byte test",
        ]

        conn = get_connection(db_path)
        with conn:
            for i, text in enumerate(complex_payloads, start=1):
                conn.execute(
                    """INSERT INTO logs (id, timestamp, received_at, source_ip, source_alias, app_name, message, raw)
                       VALUES (?, '2026-09-01T12:00:00+00:00', '2026-09-01T12:00:00+00:00', '10.0.0.1', 'h1', 'app', ?, ?)""",
                    (i, text, text),
                )
        conn.close()

        # Run indexer
        indexed = index_pending_logs(db_path)
        assert indexed == len(complex_payloads)

        conn = get_connection(db_path)
        state = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()[0]
        doc_count = conn.execute("SELECT COUNT(*) FROM logs_fts_docsize").fetchone()[0]
        conn.close()

        assert state == len(complex_payloads)
        assert doc_count == len(complex_payloads)

    @pytest.mark.asyncio
    async def test_concurrent_ingestion_indexing_and_pruning_under_load(self, db_path: Path):
        """Stress test: concurrent queue ingestion, background FTS indexing, and retention pruning do not lock or crash."""
        worker = FTSIndexWorker(db_path, batch_size=100, poll_interval=0.05)
        consumer = QueueConsumer(db_path, debounce_seconds=0.02, fts_indexer=worker)

        worker_task = asyncio.create_task(worker.run())
        consumer_task = asyncio.create_task(consumer.run())

        q = get_queue()
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()

        # Producer task 1: Ingest 300 modern logs
        async def produce_modern():
            for i in range(300):
                q.put_nowait(_make_entry(i, message="concurrent modern log", timestamp=now_ts))
                if i % 50 == 0:
                    await asyncio.sleep(0.01)

        # Producer task 2: Ingest 200 old logs
        async def produce_old():
            for i in range(300, 500):
                q.put_nowait(_make_entry(i, message="concurrent old log", timestamp=old_ts))
                if i % 50 == 0:
                    await asyncio.sleep(0.01)

        # Prune task: Execute retention prune midway during ingestion
        async def run_prune():
            await asyncio.sleep(0.05)
            await asyncio.to_thread(execute_prune, db_path, retention_days=14)

        await asyncio.gather(produce_modern(), produce_old(), run_prune())

        # Wait for all logs to be processed
        await consumer.stop()
        await consumer_task
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass

        # Final consistency check
        conn = get_connection(db_path)
        prg = conn.execute("PRAGMA integrity_check").fetchall()
        assert prg == [("ok",)]
        conn.close()

