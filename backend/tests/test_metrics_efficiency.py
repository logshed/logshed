"""Tests for single-flight metrics refresh, conditional aggregation and the storage snapshot cache."""

import asyncio
import datetime
import sqlite3
from pathlib import Path

import pytest

import app.api.external_v1 as ext_v1_mod
import app.services.storage_metrics as storage_mod
from app.core.migrations import get_connection, run_migrations
from tests.db_helpers import open_db


@pytest.fixture(autouse=True)
def reset_caches():
    ext_v1_mod._cached_error_stats = None
    ext_v1_mod._cached_error_stats_time = 0.0
    ext_v1_mod._error_refresh_task = None
    storage_mod.clear_storage_snapshot_cache()
    yield
    ext_v1_mod._cached_error_stats = None
    ext_v1_mod._cached_error_stats_time = 0.0
    ext_v1_mod._error_refresh_task = None
    storage_mod.clear_storage_snapshot_cache()


def _insert(conn, minutes_ago: float, severity: int, app_name: str = "web", host: str = "host-a") -> None:
    ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES (?, ?, '10.0.0.1', ?, ?, 1, ?, 'm', 'r')
        """,
        (ts, ts, host, app_name, severity),
    )


class TestConditionalAggregation:
    def test_single_query_counts_match_24h_and_1h_windows(self, tmp_path: Path):
        db = tmp_path / "logs.db"
        run_migrations(db)
        with open_db(db) as conn:
            _insert(conn, 10, 3)            # inside 1h and 24h
            _insert(conn, 30, 2)            # inside 1h and 24h
            _insert(conn, 120, 3, "db")     # inside 24h only
            _insert(conn, 60 * 23, 0, "db")  # inside 24h only
            _insert(conn, 60 * 30, 3)       # outside both
            _insert(conn, 5, 6)             # not an error
            conn.commit()

            conn.row_factory = sqlite3.Row
            stats = ext_v1_mod._fetch_error_aggregates(conn)

        assert stats["count_24h"] == 4
        assert stats["count_1h"] == 2
        assert {e.name: e.count for e in stats["top_apps"]} == {"web": 2, "db": 2}

    def test_empty_database_yields_zero_counts(self, tmp_path: Path):
        db = tmp_path / "logs.db"
        run_migrations(db)
        with open_db(db) as conn:
            conn.row_factory = sqlite3.Row
            stats = ext_v1_mod._fetch_error_aggregates(conn)
        assert stats["count_24h"] == 0
        assert stats["count_1h"] == 0
        assert stats["top_apps"] == [] and stats["top_hosts"] == []


class TestSingleFlightRefresh:
    @pytest.mark.asyncio
    async def test_concurrent_callers_share_one_refresh(self, monkeypatch):
        calls = []

        async def _slow_query(func, *args, **kwargs):
            calls.append(1)
            await asyncio.sleep(0.05)
            return {"count_24h": 7, "count_1h": 3, "top_apps": [], "top_hosts": []}

        monkeypatch.setattr(ext_v1_mod, "run_db_query", _slow_query)

        results = await asyncio.gather(*(ext_v1_mod._get_error_stats() for _ in range(10)))

        assert len(calls) == 1
        assert all(r["count_24h"] == 7 for r in results)

        # Fresh cache is served without another query
        await ext_v1_mod._get_error_stats()
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_cancel_shared_refresh(self, monkeypatch):
        gate = asyncio.Event()
        calls = []

        async def _gated_query(func, *args, **kwargs):
            calls.append(1)
            await gate.wait()
            return {"count_24h": 1, "count_1h": 1, "top_apps": [], "top_hosts": []}

        monkeypatch.setattr(ext_v1_mod, "run_db_query", _gated_query)

        first = asyncio.create_task(ext_v1_mod._get_error_stats())
        second = asyncio.create_task(ext_v1_mod._get_error_stats())
        await asyncio.sleep(0.02)
        first.cancel()
        gate.set()

        assert (await second)["count_24h"] == 1
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_stale_cache_triggers_new_refresh(self, monkeypatch):
        calls = []

        async def _query(func, *args, **kwargs):
            calls.append(1)
            return {"count_24h": len(calls), "count_1h": 0, "top_apps": [], "top_hosts": []}

        monkeypatch.setattr(ext_v1_mod, "run_db_query", _query)
        assert (await ext_v1_mod._get_error_stats())["count_24h"] == 1

        ext_v1_mod._cached_error_stats_time -= ext_v1_mod._METRICS_CACHE_TTL + 1
        assert (await ext_v1_mod._get_error_stats())["count_24h"] == 2


class TestStorageSnapshotCache:
    def test_repeated_reads_reuse_one_sample_until_ttl(self, tmp_path: Path, monkeypatch):
        db = tmp_path / "logs.db"
        run_migrations(db)
        calls = []
        real = storage_mod.sample_storage_metrics

        def _counting(path):
            calls.append(1)
            return real(path)

        monkeypatch.setattr(storage_mod, "sample_storage_metrics", _counting)

        first = storage_mod.get_cached_storage_metrics(db)
        second = storage_mod.get_cached_storage_metrics(db)
        assert len(calls) == 1
        assert first == second

        # Callers get copies, so mutation cannot corrupt the cache
        first["total_logs_count"] = -1
        assert storage_mod.get_cached_storage_metrics(db)["total_logs_count"] != -1

        monkeypatch.setattr(storage_mod, "STORAGE_SNAPSHOT_TTL_SECONDS", -1.0)
        storage_mod.get_cached_storage_metrics(db)
        assert len(calls) == 2

    def test_record_metrics_refreshes_the_cache(self, tmp_path: Path):
        db = tmp_path / "logs.db"
        run_migrations(db)
        assert storage_mod.get_cached_storage_metrics(db)["total_logs_count"] == 0

        with open_db(db) as conn:
            _insert(conn, 1, 6)
            conn.commit()

        # Cached value is still served until a new snapshot is recorded
        assert storage_mod.get_cached_storage_metrics(db)["total_logs_count"] == 0
        storage_mod.record_metrics(db)
        assert storage_mod.get_cached_storage_metrics(db)["total_logs_count"] == 1


class TestKeptLogCount:
    """The storage snapshot's log count stays exact while counting only the rows added since the last one."""

    def _count(self, db: Path, statements: list[str]) -> int:
        conn = get_connection(db)
        try:
            conn.set_trace_callback(statements.append)
            return storage_mod.count_logs(conn, db)
        finally:
            conn.close()

    def _add(self, db: Path, n: int, minutes_ago: float = 1) -> None:
        with open_db(db) as conn:
            for _ in range(n):
                _insert(conn, minutes_ago, 6)
            conn.commit()

    def test_new_rows_are_added_without_counting_every_row(self, tmp_path: Path):
        db = tmp_path / "logs.db"
        run_migrations(db)
        self._add(db, 5)
        first: list[str] = []
        assert self._count(db, first) == 5
        assert any("SELECT COUNT(*) FROM logs" == s.strip() for s in first)
        self._add(db, 3)
        second: list[str] = []
        assert self._count(db, second) == 8
        assert not any("SELECT COUNT(*) FROM logs" == s.strip() for s in second)

    def test_targeted_delete_and_prune_recount(self, tmp_path: Path):
        from app.models import LogDeleteRequest
        from app.services.log_deletion import execute_delete_logs
        from app.services.retention import execute_prune

        db = tmp_path / "logs.db"
        run_migrations(db)
        self._add(db, 4)
        self._add(db, 2, minutes_ago=60 * 24 * 40)
        assert self._count(db, []) == 6
        execute_prune(db, retention_days=30)
        assert self._count(db, []) == 4
        with open_db(db) as conn:
            first_id = conn.execute("SELECT MIN(id) FROM logs").fetchone()[0]
        execute_delete_logs(db, LogDeleteRequest(log_ids=[first_id]))
        assert self._count(db, []) == 3

    def test_count_taken_during_a_delete_is_not_kept(self, tmp_path: Path):
        db = tmp_path / "logs.db"
        run_migrations(db)
        self._add(db, 2)
        conn = get_connection(db)
        try:
            real_execute = conn.execute

            class _DeleteMidCount:
                def execute(self, sql, *args):
                    if sql.startswith("SELECT COUNT(*)"):
                        storage_mod.note_logs_deleted()
                    return real_execute(sql, *args)

                def rollback(self):
                    conn.rollback()

            assert storage_mod.count_logs(_DeleteMidCount(), db) == 2
        finally:
            conn.close()
        assert str(db) not in storage_mod._count_state

    def test_snapshot_reports_the_kept_count(self, tmp_path: Path):
        db = tmp_path / "logs.db"
        run_migrations(db)
        self._add(db, 7)
        assert storage_mod.sample_storage_metrics(db)["total_logs_count"] == 7


class TestErrorAggregatePlans:

    def test_top_error_apps_reads_only_the_24_hour_range(self, tmp_path: Path):
        """The app ranking uses the time index, not a walk over every app's range."""
        db = tmp_path / "logs.db"
        run_migrations(db)
        statements: list[str] = []
        with open_db(db) as conn:
            for minutes, app in ((5, "a"), (10, "a"), (20, "b"), (60 * 30, "c")):
                _insert(conn, minutes, 3, app_name=app)
            conn.execute("ANALYZE")
            conn.commit()
            conn.row_factory = sqlite3.Row
            conn.set_trace_callback(statements.append)
            result = ext_v1_mod._fetch_error_aggregates(conn)
            apps_sql = next(s for s in statements if "GROUP BY +app_name" in s)
            plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + apps_sql)]
        assert [(e.name, e.count) for e in result["top_apps"]] == [("a", 2), ("b", 1)]
        assert any("idx_logs_time_sev" in step for step in plan)
