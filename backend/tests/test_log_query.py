"""
Unit tests for the shared log query helpers in app.services.log_query.
"""

import datetime
import sqlite3
from unittest.mock import MagicMock

import pytest

from fastapi import HTTPException

from app.core.migrations import run_migrations
from app.services import log_query
from app.services.fts_indexer import index_pending_logs, set_fts_worker
from app.services.log_query import (
    add_fts_filter,
    execute_with_fts_fallback,
    is_fts_syntax_error,
    run_newest_first,
    run_search_query,
    to_utc_bound,
)
from tests.db_helpers import open_db


class TestToUtcBound:

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-10-15T12:00:00.000Z", "2026-10-15T12:00:00+00:00"),
            ("2026-10-15T12:00:00Z", "2026-10-15T12:00:00+00:00"),
            ("2026-10-15T12:00:00+02:00", "2026-10-15T10:00:00+00:00"),
            ("2026-10-15T12:00:00.250-01:30", "2026-10-15T13:30:00.250000+00:00"),
            ("2026-10-15T12:00:00", "2026-10-15T12:00:00+00:00"),
        ],
    )
    def test_iso_values_use_stored_utc_layout(self, value: str, expected: str):
        assert to_utc_bound(value) == expected

    def test_relative_values_are_accepted(self):
        before = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        bound = datetime.datetime.fromisoformat(to_utc_bound("1h"))
        after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        assert before <= bound <= after
        assert bound.utcoffset() == datetime.timedelta(0)

    def test_empty_values_give_none(self):
        assert to_utc_bound(None) is None
        assert to_utc_bound("") is None

    @pytest.mark.parametrize("value", ["not-a-date", "2026-13-45T00:00:00", "the day before"])
    def test_invalid_values_raise_value_error(self, value: str):
        with pytest.raises(ValueError):
            to_utc_bound(value)


class TestExecuteWithFtsFallback:

    def test_syntax_error_retries_once_with_escaped_tokens(self):
        seen: list[str] = []

        def run(params):
            seen.append(params["fts_term"])
            if len(seen) == 1:
                raise sqlite3.OperationalError('fts5: syntax error near "="')
            return "ok"

        assert execute_with_fts_fallback(run, {"fts_term": "key=value*"}, "key=value", empty=None) == "ok"
        assert seen == ["key=value*", '"key=value"*']

    def test_lock_error_is_not_retried(self):
        calls = []

        def run(params):
            calls.append(params)
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            execute_with_fts_fallback(run, {"fts_term": "foo*"}, "foo", empty=None)
        assert len(calls) == 1

    def test_second_syntax_error_returns_empty(self):
        def run(params):
            raise sqlite3.OperationalError("fts5: syntax error near x")

        assert execute_with_fts_fallback(run, {"fts_term": "x*"}, "x y", empty=[]) == []

    def test_non_fts_queries_do_not_wake_the_indexer(self):
        worker = MagicMock()
        set_fts_worker(worker)
        try:
            execute_with_fts_fallback(lambda p: None, {}, None, empty=None)
            assert not worker.notify_new_logs.called
            execute_with_fts_fallback(lambda p: None, {"fts_term": "foo*"}, "foo", empty=None)
            assert worker.notify_new_logs.call_count == 1
        finally:
            set_fts_worker(None)

    @pytest.mark.parametrize(
        "message, expected",
        [
            ('fts5: syntax error near "@"', True),
            ("no such column: bar", True),
            ("unterminated string", True),
            ("database is locked", False),
            ("database table is locked", False),
            ("disk I/O error", False),
        ],
    )
    def test_syntax_error_detection(self, message: str, expected: bool):
        assert is_fts_syntax_error(sqlite3.OperationalError(message)) is expected


def _seed_numbered_logs(db_path, rows: list[tuple[str, str]]) -> None:
    """Insert (timestamp, message) rows in order, so ids follow the list, and index them."""
    run_migrations(db_path)
    with open_db(db_path) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility,"
            " severity, message, raw) VALUES (?, ?, '10.0.0.1', 'srv1', 'web', 1, 6, ?, ?)",
            [(ts, ts, msg, msg) for ts, msg in rows],
        )
        conn.commit()
    index_pending_logs(db_path)


def _newest_page(conn, query: str, limit: int, cap: int, attempts: list[int], from_ts=None):
    """A /api/logs style page: capped count plus the newest rows, via run_newest_first."""
    where: list[str] = []
    params: dict = {}
    add_fts_filter(where, params, query)
    if from_ts:
        where.append("logs.timestamp >= :from_ts")
        params["from_ts"] = from_ts
    where_sql = "WHERE " + " AND ".join(where)

    def _run(p):
        attempts.append(p["fts_min_rowid"])
        total = conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM logs {where_sql} LIMIT {cap})", p).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, timestamp FROM logs {where_sql} ORDER BY timestamp DESC, id DESC LIMIT {limit}", p
        ).fetchall()
        return total, [tuple(r) for r in rows]

    def _oldest(result):
        total, rows = result
        return rows[-1][1] if total >= cap and len(rows) >= limit else from_ts

    return run_newest_first(conn, params, _run, _oldest, lambda r: r[0], cap)


def _ts(i: int) -> str:
    return (datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=i)).isoformat()


class TestRunNewestFirst:

    def test_common_term_is_answered_from_the_newest_window(self, tmp_path, monkeypatch):
        db = tmp_path / "logs.db"
        _seed_numbered_logs(db, [(_ts(i), f"timeout {i}") for i in range(1, 501)])
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        with open_db(db) as conn:
            attempts: list[int] = []
            windowed = _newest_page(conn, "timeout", limit=10, cap=21, attempts=attempts)
            monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", ())
            full = _newest_page(conn, "timeout", limit=10, cap=21, attempts=[])
        assert attempts == [451]
        assert windowed == full
        assert [row[0] for row in windowed[1]] == list(range(500, 490, -1))

    def test_rare_term_widens_to_every_row_with_an_exact_total(self, tmp_path, monkeypatch):
        db = tmp_path / "logs.db"
        rows = [(_ts(i), "stale handle" if i % 100 == 0 else f"noise {i}") for i in range(1, 501)]
        _seed_numbered_logs(db, rows)
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        with open_db(db) as conn:
            attempts: list[int] = []
            total, page = _newest_page(conn, "stale", limit=10, cap=21, attempts=attempts)
        # One match in the 50-row window: 1,050 rows would be needed, so the 200-row one is skipped
        assert attempts == [451, 0]
        assert total == 5
        assert [row[0] for row in page] == [500, 400, 300, 200, 100]

    def test_few_matches_inside_the_time_range_need_no_wider_window(self, tmp_path, monkeypatch):
        db = tmp_path / "logs.db"
        rows = [(_ts(i), "stale handle" if i % 100 == 0 else f"noise {i}") for i in range(1, 501)]
        _seed_numbered_logs(db, rows)
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        with open_db(db) as conn:
            attempts: list[int] = []
            recent = _newest_page(conn, "stale", limit=10, cap=21, attempts=attempts, from_ts=_ts(460))
            # The range starts before the window, so only every row is complete
            wide_attempts: list[int] = []
            wide = _newest_page(conn, "stale", limit=10, cap=21, attempts=wide_attempts, from_ts=_ts(250))
        assert attempts == [451]
        assert recent == (1, [(500, _ts(500))])
        assert wide_attempts == [451, 0]
        assert wide[0] == 3

    def test_medium_density_skips_to_the_window_that_can_hold_enough(self, tmp_path, monkeypatch):
        db = tmp_path / "logs.db"
        rows = [(_ts(i), "stale handle" if i % 5 == 0 else f"noise {i}") for i in range(1, 2001)]
        _seed_numbered_logs(db, rows)
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 100, 400))
        with open_db(db) as conn:
            attempts: list[int] = []
            windowed = _newest_page(conn, "stale", limit=10, cap=21, attempts=attempts)
            monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", ())
            full = _newest_page(conn, "stale", limit=10, cap=21, attempts=[])
        # 10 matches in 50 rows: 105 rows are needed, so the 100-row window is skipped
        assert attempts == [1951, 1601]
        assert windowed == full

    def test_back_dated_row_outside_the_window_still_comes_first(self, tmp_path, monkeypatch):
        # Row 1 arrived first but carries the newest timestamp (a device clock ahead), so a
        # window of the newest ids would miss it: the search must fall back to every row.
        db = tmp_path / "logs.db"
        rows = [(_ts(10_000), "timeout early")] + [(_ts(i), f"timeout {i}") for i in range(2, 501)]
        _seed_numbered_logs(db, rows)
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        with open_db(db) as conn:
            attempts: list[int] = []
            total, page = _newest_page(conn, "timeout", limit=10, cap=21, attempts=attempts)
        assert attempts == [451, 301, 0]
        assert total == 21
        assert [row[0] for row in page][:3] == [1, 500, 499]

    def test_back_dated_rare_match_inside_the_time_range_widens_the_search(self, tmp_path, monkeypatch):
        # Row 1 is older than every window but its timestamp falls inside the searched range
        db = tmp_path / "logs.db"
        rows = [(_ts(470), "stale early")] + [(_ts(i), "stale handle" if i == 480 else f"noise {i}") for i in range(2, 501)]
        _seed_numbered_logs(db, rows)
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        with open_db(db) as conn:
            attempts: list[int] = []
            total, page = _newest_page(conn, "stale", limit=10, cap=21, attempts=attempts, from_ts=_ts(460))
        assert attempts == [451, 0]
        assert total == 2
        assert [row[0] for row in page] == [480, 1]

    def test_time_range_after_every_row_is_answered_from_the_newest_window(self, tmp_path, monkeypatch):
        db = tmp_path / "logs.db"
        _seed_numbered_logs(db, [(_ts(i), f"stale {i}") for i in range(1, 501)])
        monkeypatch.setattr(log_query, "NEWEST_FIRST_WINDOWS", (50, 200))
        with open_db(db) as conn:
            attempts: list[int] = []
            result = _newest_page(conn, "stale", limit=10, cap=21, attempts=attempts, from_ts=_ts(1000))
        assert attempts == [451]
        assert result == (0, [])

    def test_query_without_full_text_term_runs_once(self):
        calls = []
        result = run_newest_first(MagicMock(), {"limit": 5}, lambda p: calls.append(p) or "done", lambda r: None, len, 1)
        assert result == "done"
        assert calls == [{"limit": 5}]


class _Request:
    def __init__(self, disconnected: bool):
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


# A statement that runs for minutes unless interrupted
_ENDLESS_SQL = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"


class TestRunSearchQuery:

    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        db = tmp_path / "logs.db"
        run_migrations(db)
        monkeypatch.setattr("app.api.deps.get_db_path", lambda: db)
        monkeypatch.setattr(log_query, "DISCONNECT_POLL_SECONDS", 0.01)

    @pytest.mark.asyncio
    async def test_returns_the_result(self):
        assert await run_search_query(lambda conn: conn.execute("SELECT 7").fetchone()[0], _Request(False)) == 7

    @pytest.mark.asyncio
    async def test_disconnected_client_stops_the_statement(self):
        with pytest.raises(HTTPException) as exc:
            await run_search_query(lambda conn: conn.execute(_ENDLESS_SQL).fetchone(), _Request(True))
        assert exc.value.status_code == 499
        # The interrupt check is removed, so the thread's connection serves the next query
        assert await run_search_query(lambda conn: conn.execute("SELECT 1").fetchone()[0]) == 1

    @pytest.mark.asyncio
    async def test_time_limit_stops_the_statement_with_504(self, monkeypatch):
        monkeypatch.setattr(log_query, "SEARCH_TIME_LIMIT_SECONDS", 0.05)
        with pytest.raises(HTTPException) as exc:
            await run_search_query(lambda conn: conn.execute(_ENDLESS_SQL).fetchone())
        assert exc.value.status_code == 504
        assert "narrow" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_other_database_errors_propagate(self):
        def _locked(conn):
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await run_search_query(_locked, _Request(False))
