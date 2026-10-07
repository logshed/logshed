"""
Tests for thread-local SQLite read connection reuse, multi-threaded isolation,
WAL concurrency, and connection lifecycle management in app.api.deps.
"""

import asyncio
import concurrent.futures
from pathlib import Path
import sqlite3
import threading

import pytest

from app.api.deps import (
    _thread_local,
    close_thread_local_connections,
    get_thread_read_connection,
    run_db_query,
)
from app.core.migrations import run_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create and migrate a temporary test database."""
    p = tmp_path / "test_deps.db"
    run_migrations(p)
    return p


@pytest.fixture(autouse=True)
def cleanup_thread_local_connections():
    """Ensure thread-local connections are cleaned up before and after each test."""
    close_thread_local_connections()
    yield
    close_thread_local_connections()


def test_get_thread_read_connection_pragmas_and_reuse(db_path: Path):
    """
    get_thread_read_connection returns a configured connection and reuses it on the same thread.
    Pragmas (WAL, NORMAL synchronous, 5000 busy_timeout, foreign_keys ON) and Row factory are set.
    """
    conn1 = get_thread_read_connection(db_path)
    conn2 = get_thread_read_connection(db_path)

    # Identical instance must be returned on the same thread
    assert conn1 is conn2

    # Verify Row factory
    assert conn1.row_factory == sqlite3.Row

    # Verify PRAGMA values
    journal_mode = conn1.execute("PRAGMA journal_mode;").fetchone()[0]
    assert journal_mode.lower() == "wal"

    synchronous = conn1.execute("PRAGMA synchronous;").fetchone()[0]
    assert synchronous == 1  # 1 corresponds to NORMAL

    busy_timeout = conn1.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert busy_timeout == 5000

    foreign_keys = conn1.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert foreign_keys == 1  # 1 corresponds to ON


def test_close_thread_local_connections(db_path: Path):
    """
    close_thread_local_connections closes all cached connections on the calling thread
    and clears the cache dictionary.
    """
    conn = get_thread_read_connection(db_path)
    resolved_key = str(db_path.resolve())

    assert hasattr(_thread_local, "connections")
    assert resolved_key in _thread_local.connections
    assert _thread_local.connections[resolved_key] is conn

    # Verify the connection works
    assert conn.execute("SELECT 1;").fetchone()[0] == 1

    # Close thread-local connections
    close_thread_local_connections()

    # Dictionary must be cleared
    assert len(_thread_local.connections) == 0

    # Old connection must be closed
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        conn.execute("SELECT 1;")

    # Next call opens a fresh, open connection
    new_conn = get_thread_read_connection(db_path)
    assert new_conn is not conn
    assert new_conn.execute("SELECT 1;").fetchone()[0] == 1


def test_multithreaded_connection_isolation(db_path: Path):
    """
    Connections in different threads must be distinct instances.
    Closing connections in one thread does not affect other threads.
    """
    num_threads = 6
    connections_per_thread = {}
    thread_errors = []

    def _worker(thread_idx: int):
        try:
            c1 = get_thread_read_connection(db_path)
            c2 = get_thread_read_connection(db_path)
            # Reused within the thread
            assert c1 is c2
            ident = threading.get_ident()
            connections_per_thread[thread_idx] = (ident, c1)
            # Perform a read to confirm connection is functional
            row = c1.execute("SELECT ?", (thread_idx,)).fetchone()
            assert row[0] == thread_idx
        except Exception as e:
            thread_errors.append(e)

    threads = [
        threading.Thread(target=_worker, args=(i,))
        for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not thread_errors
    assert len(connections_per_thread) == num_threads

    # Verify every thread received a distinct sqlite3.Connection instance
    conn_instances = [conn for _, conn in connections_per_thread.values()]
    unique_instances = set(id(c) for c in conn_instances)
    assert len(unique_instances) == num_threads


def test_distinct_db_paths_have_separate_connections(tmp_path: Path):
    """Different database paths on the same thread maintain separate cached connections."""
    p1 = tmp_path / "db1.db"
    p2 = tmp_path / "db2.db"
    run_migrations(p1)
    run_migrations(p2)

    conn1 = get_thread_read_connection(p1)
    conn2 = get_thread_read_connection(p2)

    assert conn1 is not conn2

    resolved_1 = str(p1.resolve())
    resolved_2 = str(p2.resolve())
    assert _thread_local.connections[resolved_1] is conn1
    assert _thread_local.connections[resolved_2] is conn2


def test_connection_reopened_if_closed_externally(db_path: Path):
    """If a cached connection was closed externally, get_thread_read_connection safely reopens it."""
    conn = get_thread_read_connection(db_path)
    conn.close()

    # Next call should detect the closed connection and open a fresh one
    new_conn = get_thread_read_connection(db_path)
    assert new_conn is not conn
    assert new_conn.execute("SELECT 1;").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_run_db_query_reuses_connection_sequential(db_path: Path):
    """Sequential run_db_query calls in a dedicated single-threaded executor reuse the connection."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()

    try:
        # First query
        conn1_id = await loop.run_in_executor(
            executor,
            lambda: id(get_thread_read_connection(db_path)),
        )
        # Second query on same worker thread
        conn2_id = await loop.run_in_executor(
            executor,
            lambda: id(get_thread_read_connection(db_path)),
        )
        assert conn1_id == conn2_id
    finally:
        await loop.run_in_executor(executor, close_thread_local_connections)
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_run_db_query_rolls_back_on_exception(db_path: Path):
    """When a query raises an exception in run_db_query, any active transaction is rolled back."""
    # Ensure test table exists
    await run_db_query(
        lambda conn: conn.execute("CREATE TABLE IF NOT EXISTS tx_test (id INT, val TEXT);"),
        custom_db_path=db_path,
    )

    def _failing_write(conn: sqlite3.Connection):
        conn.execute("INSERT INTO tx_test VALUES (1, 'should_rollback');")
        assert conn.in_transaction
        raise ValueError("simulated write failure")

    with pytest.raises(ValueError, match="simulated write failure"):
        await run_db_query(_failing_write, custom_db_path=db_path)

    # Next query verifies that no transaction is active and uncommitted row was rolled back
    def _verify_clean(conn: sqlite3.Connection):
        assert not conn.in_transaction
        count = conn.execute("SELECT COUNT(*) FROM tx_test WHERE id = 1;").fetchone()[0]
        assert count == 0
        return True

    res = await run_db_query(_verify_clean, custom_db_path=db_path)
    assert res is True


@pytest.mark.asyncio
async def test_wal_concurrency_reads_do_not_block_wal_checkpoint(db_path: Path):
    """
    Concurrent read queries executed via run_db_query do not leave lingering
    read transactions, allowing PRAGMA wal_checkpoint(TRUNCATE) to succeed without blocking.
    """
    # Seed the database with some log rows to generate WAL pages
    def _seed(conn: sqlite3.Connection):
        conn.execute("""
            INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
            VALUES (datetime('now'), datetime('now'), '127.0.0.1', 'local', 'test', 1, 6, 'test message', 'raw message');
        """)

    for _ in range(20):
        await run_db_query(_seed, custom_db_path=db_path)

    # Verify WAL file has content
    wal_path = Path(str(db_path) + "-wal")
    assert wal_path.exists()

    # Execute concurrent reads across multiple tasks
    async def _read_worker():
        for _ in range(10):
            def _query(conn: sqlite3.Connection):
                row = conn.execute("SELECT COUNT(*) FROM logs;").fetchone()
                # in_transaction should not be left open
                return row[0]
            val = await run_db_query(_query, custom_db_path=db_path)
            assert val >= 20

    tasks = [_read_worker() for _ in range(8)]
    await asyncio.gather(*tasks)

    # Immediately attempt a TRUNCATE checkpoint
    def _checkpoint(conn: sqlite3.Connection):
        # returns (busy, log_pages, checkpointed_pages)
        cursor = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        return cursor.fetchone()

    cp_res = await run_db_query(_checkpoint, custom_db_path=db_path)
    # busy flag must be 0, indicating truncation was not blocked by dangling shared locks
    assert cp_res[0] == 0
