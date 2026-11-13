"""
Long deletes and prunes commit in chunks; between chunks they let a waiting ingest write take
the database write lock, so the ingest queue does not fill while they run.
"""

import datetime
import threading
import time
from pathlib import Path

import pytest

from app.core import pipeline
from app.core.migrations import run_migrations
from app.models import LogDeleteRequest
from app.services.fts_indexer import index_pending_logs
from app.services.log_deletion import execute_delete_logs
from app.services.retention import execute_prune
from tests.db_helpers import count_logs, open_db

ROWS = 12_000  # three chunks of 5,000 at most


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)).isoformat()
    with open_db(db_file) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)"
            " VALUES (?, ?, '10.0.0.1', 'host', 'app', 1, 6, ?, ?)",
            [(old, old, f"message {i}", f"raw {i}") for i in range(ROWS)],
        )
    index_pending_logs(db_file)
    # Long enough that only the test releases a waiting delete, short enough to fail fast
    monkeypatch.setattr(pipeline, "INGEST_TURN_TIMEOUT", 30.0)
    yield db_file
    pipeline._ingest_write_idle.set()


def _count(db_file: Path) -> int:
    return count_logs(db_file)


def _wait_for(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


@pytest.mark.parametrize(
    "operation",
    [
        lambda path: execute_prune(path, retention_days=7, index_pending=False),
        lambda path: execute_delete_logs(path, LogDeleteRequest(apps=["app"]), index_pending=False),
    ],
    ids=["prune", "delete"],
)
def test_long_delete_waits_for_a_pending_ingest_write_between_chunks(db: Path, operation):
    # An ingest batch is waiting for the write lock
    pipeline._ingest_write_idle.clear()
    worker = threading.Thread(target=operation, args=(db,), daemon=True)
    worker.start()

    # The first chunk goes, then the delete waits for the ingest write instead of taking the lock again
    _wait_for(lambda: _count(db) == ROWS - 5000)
    time.sleep(0.2)
    assert worker.is_alive()
    assert _count(db) == ROWS - 5000

    # Once the ingest write has stored, the delete carries on to the end
    pipeline._ingest_write_idle.set()
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert _count(db) == 0


def test_long_delete_does_not_wait_without_ingest(db: Path):
    started = time.monotonic()
    execute_prune(db, retention_days=7, index_pending=False)
    assert _count(db) == 0
    assert time.monotonic() - started < 10.0  # far below the 30 s turn timeout


def test_queue_consumer_marks_its_write_pending_while_storing(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    consumer = pipeline.QueueConsumer(db_file)
    seen = []
    original = consumer._normalize_timestamps

    def _record(batch):
        seen.append(pipeline._ingest_write_idle.is_set())
        return original(batch)

    monkeypatch.setattr(consumer, "_normalize_timestamps", _record)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        consumer._insert_batch([{
            "timestamp": now, "received_at": now, "source_ip": "10.0.0.1", "source_alias": "host",
            "app_name": "app", "facility": 1, "severity": 6, "message": "hello", "raw": "hello",
        }])
    finally:
        consumer._close_conn()
        pipeline.set_queue_consumer(None)
    assert seen == [False]
    assert pipeline._ingest_write_idle.is_set()
    assert _count(db_file) == 1
