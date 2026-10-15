"""
Tests for the chunked retroactive alias backfills (host aliases and Docker endpoint aliases).

Each batch must start below the rows the previous batch already updated (keyset batching),
so the work per batch stays flat instead of growing with every batch.
"""

from pathlib import Path

import pytest

from app.api.aliases import _batch_update_log_aliases
from app.api.docker_hosts import _batch_update_docker_aliases
from app.core.migrations import get_connection, run_migrations

ROWS = 5000
BATCH = 100


@pytest.fixture
def conn(tmp_path: Path):
    db_file = tmp_path / "alias_backfill.db"
    run_migrations(db_file)
    c = get_connection(db_file)
    try:
        yield c
    finally:
        c.close()


def _seed(conn, source_ip: str, source_alias: str, count: int = ROWS) -> None:
    conn.executemany(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES ('2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z', ?, ?, 'app', 1, 6, 'm', 'm')
        """,
        [(source_ip, source_alias)] * count,
    )
    # Unrelated rows interleaved at the end so the target rows are not the newest ones only
    conn.executemany(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES ('2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z', '10.1.1.1', '10.1.1.1', 'other', 1, 6, 'm', 'm')
        """,
        [()] * 50,
    )
    conn.commit()


def _per_batch_work(conn, monkeypatch, run) -> list[int]:
    """Run a backfill and return the SQLite VM steps spent in each full batch."""
    steps = [0]

    def _tick() -> int:
        steps[0] += 1
        return 0

    marks: list[int] = []
    monkeypatch.setattr("time.sleep", lambda _s: marks.append(steps[0]))
    conn.set_progress_handler(_tick, 100)
    try:
        run()
    finally:
        conn.set_progress_handler(None, 0)
    marks.append(steps[0])
    return [b - a for a, b in zip([0] + marks[:-1], marks)]


def _assert_flat(work: list[int]) -> None:
    full_batches = work[: ROWS // BATCH]
    assert len(full_batches) == ROWS // BATCH
    # With rescans, batch k walks past (k - 1) * BATCH already-updated rows, so the
    # last full batch costs several times the first one. Keyset batches stay flat.
    assert max(full_batches) < 1.5 * min(full_batches), work


def _aliases(conn, source_ip: str) -> set:
    return {r[0] for r in conn.execute("SELECT DISTINCT source_alias FROM logs WHERE source_ip = ?", (source_ip,))}


def test_host_alias_backfill_does_not_rescan_updated_rows(conn, monkeypatch):
    _seed(conn, "192.168.1.50", "192.168.1.50")
    work = _per_batch_work(
        conn, monkeypatch, lambda: _batch_update_log_aliases(conn, "192.168.1.50", "nas", batch_size=BATCH)
    )
    assert _aliases(conn, "192.168.1.50") == {"nas"}
    assert _aliases(conn, "10.1.1.1") == {"10.1.1.1"}
    _assert_flat(work)


def test_primary_docker_alias_backfill_does_not_rescan_updated_rows(conn, monkeypatch):
    _seed(conn, "docker", "docker")
    work = _per_batch_work(
        conn,
        monkeypatch,
        lambda: _batch_update_docker_aliases(conn, "docker", "unraid", old_alias="docker", batch_size=BATCH),
    )
    assert _aliases(conn, "docker") == {"unraid"}
    _assert_flat(work)


def test_remote_docker_alias_backfill_does_not_rescan_updated_rows(conn, monkeypatch):
    _seed(conn, "10.0.0.5", "node-old")
    work = _per_batch_work(
        conn,
        monkeypatch,
        lambda: _batch_update_docker_aliases(conn, "10.0.0.5", "node-new", old_alias="node-old", batch_size=BATCH),
    )
    assert _aliases(conn, "10.0.0.5") == {"node-new"}
    _assert_flat(work)


def test_backfill_handles_exact_multiple_and_partial_batches(conn, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    _seed(conn, "192.168.1.60", "192.168.1.60", count=7)
    _batch_update_log_aliases(conn, "192.168.1.60", "box", batch_size=7)
    assert _aliases(conn, "192.168.1.60") == {"box"}

    _seed(conn, "10.0.0.6", "a", count=5)
    _batch_update_docker_aliases(conn, "10.0.0.6", "b", old_alias="a", batch_size=2)
    assert _aliases(conn, "10.0.0.6") == {"b"}
