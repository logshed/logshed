"""
The Console's volume histogram: log counts per time bucket by severity group, over the Console's
range on the server's clock, from the catalogue for sealed days' whole hours and from the files
for the rest.
"""

import datetime
import zoneinfo
from pathlib import Path
from unittest import mock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.core.migrations as migrations_mod
from app.core import day_files
from app.core.migrations import MIGRATIONS, run_migrations
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services import day_store, log_histogram, log_store
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db

_UTC = datetime.timezone.utc
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")
KOLKATA = zoneinfo.ZoneInfo("Asia/Kolkata")
D1, D2 = "2026-03-27", "2026-03-28"


def _s(day: str, hour: int = 0, minute: int = 0, second: int = 0) -> int:
    """UTC epoch seconds of a time on a day."""
    return day_files.day_start_us(day) // 1_000_000 + hour * 3600 + minute * 60 + second


def _iso(epoch_s: int) -> str:
    return datetime.datetime.fromtimestamp(epoch_s, _UTC).isoformat()


def _row(day: str, hour: int, minute: int = 0, severity: int = 6, host: str = "nas", app: str = "kernel", message: str = "m") -> dict:
    at = _iso(_s(day, hour, minute))
    return {
        "timestamp": at, "received_at": at, "source_ip": "10.0.0.1", "source_alias": host,
        "app_name": app, "facility": 1, "severity": severity, "message": message, "raw": message,
    }


def _write(path: Path, rows: list[dict]) -> None:
    with open_db(path) as conn:
        day_store.write_rows(conn, [dict(r) for r in rows])


def _seal_d1(path: Path) -> None:
    index_pending_logs(path)
    with open_db(path) as conn:
        assert day_store.seal_finished_days(conn, now_us=_s(D2, 0, 5) * 1_000_000) == [D1]


def _histogram(path: Path, log_filter: LogFilter, edges: list[int]) -> list[dict[int, int]]:
    with open_db(path) as conn:
        return log_store.histogram(conn, log_filter, edges)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    day_store.forget_id_blocks()
    day_store.forget_connections()
    path = tmp_path / "logs.db"
    run_migrations(path)
    yield path
    day_store.forget_connections()


class TestBuckets:
    @pytest.mark.parametrize(
        "span, size",
        [
            (15 * 60, 15),
            (3600, 60),
            (6 * 3600, 600),
            (12 * 3600, 900),
            # Over 12 hours: whole hours, which the catalogue can count
            (13 * 3600, 3600),
            (24 * 3600, 3600),
            (7 * 86400, 3 * 3600),
            (30 * 86400, 12 * 3600),
            (300 * 86400, 7 * 86400),
            (366 * 86400, 7 * 86400),
            (5 * 365 * 86400, 91 * 86400),
            (20 * 365 * 86400, 365 * 86400),
        ],
    )
    def test_about_sixty_buckets_at_most(self, span, size):
        assert log_histogram.choose_bucket(span) == size

    def test_edges_start_at_even_times_of_the_server_clock(self):
        # 10:07:30 to 11:07:30 UTC in minutes: the first edge is 10:07, the last 11:08
        edges = log_histogram.bucket_edges(_s(D1, 10, 7, 30), _s(D1, 11, 7, 30), 60, _UTC)
        assert edges[0] == _s(D1, 10, 7) and edges[-1] == _s(D1, 11, 8)
        assert len(edges) == 62

    def test_hour_edges_follow_a_zone_half_an_hour_off_utc(self):
        edges = log_histogram.bucket_edges(_s(D1, 10, 7), _s(D1, 14), 3600, KOLKATA)
        # Kolkata is UTC+5:30, so its hours start at :30 UTC
        assert edges == [_s(D1, 9, 30), _s(D1, 10, 30), _s(D1, 11, 30), _s(D1, 12, 30), _s(D1, 13, 30), _s(D1, 14, 30)]

    @pytest.mark.parametrize("day", ["2026-03-08", "2026-11-01"])
    def test_buckets_under_a_day_keep_one_length_across_a_clock_change(self, day):
        # New York's clock goes forward on 2026-03-08 and back on 2026-11-01
        edges = log_histogram.bucket_edges(_s(day, 4), _s(day, 9), 3600, NEW_YORK)
        assert {b - a for a, b in zip(edges, edges[1:])} == {3600}
        for edge in edges:
            assert datetime.datetime.fromtimestamp(edge, NEW_YORK).minute == 0

    def test_day_buckets_start_at_the_server_midnight_across_a_clock_change(self):
        start = int(datetime.datetime(2026, 3, 5, 12, tzinfo=NEW_YORK).timestamp())
        end = int(datetime.datetime(2026, 3, 11, 12, tzinfo=NEW_YORK).timestamp())
        edges = log_histogram.bucket_edges(start, end, 86400, NEW_YORK)
        local = [datetime.datetime.fromtimestamp(e, NEW_YORK) for e in edges]
        assert [(t.day, t.hour) for t in local] == [(d, 0) for d in range(5, 13)]
        # The day the clock goes forward has 23 hours
        assert edges[4] - edges[3] == 23 * 3600

    def test_no_start_shows_the_hour_before_the_end(self):
        now = datetime.datetime.fromtimestamp(_s(D2, 12, 30), _UTC)
        assert log_histogram.histogram_range(LogFilter(), now) == (_s(D2, 11, 30), _s(D2, 12, 30))
        bounded = LogFilter(to_ts=_iso(_s(D2, 6)))
        assert log_histogram.histogram_range(bounded, now) == (_s(D2, 5), _s(D2, 6))

    def test_severity_groups(self):
        assert [log_histogram.severity_group(s) for s in range(8)] == [
            "error", "error", "error", "error", "warning", "info", "info", "debug",
        ]


class TestCounting:
    def test_an_open_day_is_counted_from_its_file_by_bucket_and_severity(self, db):
        _write(db, [_row(D2, 1, 5, severity=3), _row(D2, 1, 50), _row(D2, 1, 55), _row(D2, 2, 10, severity=7)])
        found = _histogram(db, LogFilter(), [_s(D2, 1), _s(D2, 1, 30), _s(D2, 2), _s(D2, 2, 30)])
        assert found == [{3: 1}, {6: 2}, {7: 1}]

    def test_a_sealed_day_s_whole_hours_come_from_the_catalogue(self, db):
        _write(db, [_row(D1, 10), _row(D1, 11, severity=4), _row(D2, 1)])
        _seal_d1(db)
        with open_db(db) as conn:
            # Only the catalogue says ten: the file holds one log in that hour
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ? AND hour = ?", (D1, _s(D1, 10)))
        found = _histogram(db, LogFilter(), [_s(D1, 10), _s(D1, 11), _s(D1, 12), _s(D2, 1), _s(D2, 2)])
        assert found == [{6: 10}, {4: 1}, {}, {6: 1}]

    def test_the_catalogue_keeps_to_the_severity_filter(self, db):
        _write(db, [_row(D1, 10, severity=3), _row(D1, 10, 5, severity=6)])
        _seal_d1(db)
        found = _histogram(db, LogFilter(severity_max=4), [_s(D1, 10), _s(D1, 11)])
        assert found == [{3: 1}]

    def test_a_part_hour_at_the_start_of_the_range_is_read_from_the_file(self, db):
        _write(db, [_row(D1, 10, 10), _row(D1, 10, 40), _row(D1, 11, 10)])
        _seal_d1(db)
        with open_db(db) as conn:
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ? AND hour = ?", (D1, _s(D1, 10)))
        # The range starts at 10:30: the catalogue's 10:00 hour would count the 10:10 log too
        log_filter = LogFilter(from_ts=_iso(_s(D1, 10, 30)))
        found = _histogram(db, log_filter, [_s(D1, 10), _s(D1, 11), _s(D1, 12)])
        assert found == [{6: 1}, {6: 1}]

    def test_search_words_read_the_files(self, db):
        _write(db, [_row(D1, 10, message="disk full"), _row(D1, 10, 5, message="all good")])
        _seal_d1(db)
        with open_db(db) as conn:
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ?", (D1,))
        assert _histogram(db, LogFilter(query="disk"), [_s(D1, 10), _s(D1, 11)]) == [{6: 1}]

    def test_app_and_host_name_filters_use_the_catalogue(self, db):
        _write(db, [_row(D1, 10), _row(D1, 10, 5, host="pve", app="cron"), _row(D1, 10, 6, host="pve")])
        _seal_d1(db)
        with open_db(db) as conn:
            conn.execute("UPDATE log_file_counts SET count = count * 10 WHERE day = ?", (D1,))
        edges = [_s(D1, 10), _s(D1, 11)]
        assert _histogram(db, LogFilter(apps=("cron",)), edges) == [{6: 10}]
        # No address in the filter: the host name alone selects the rows
        assert _histogram(db, LogFilter(sources=("pve",)), edges) == [{6: 20}]
        assert _histogram(db, LogFilter(sources=("pve",), apps=("kernel",)), edges) == [{6: 10}]

    def test_an_address_filter_reads_the_file_when_its_rows_carry_other_names(self, db):
        # The rows from 10.0.0.1 are stored under "nas" and "pve": the catalogue cannot see the address
        _write(db, [_row(D1, 10), _row(D1, 10, 5, host="pve")])
        _seal_d1(db)
        with open_db(db) as conn:
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ?", (D1,))
        assert _histogram(db, LogFilter(sources=("10.0.0.1",)), [_s(D1, 10), _s(D1, 11)]) == [{6: 2}]

    def test_a_host_alias_filter_uses_the_catalogue_when_its_address_rows_carry_its_name(self, db):
        _write(db, [_row(D1, 10), _row(D1, 10, 5)])
        _seal_d1(db)
        with open_db(db) as conn:
            conn.execute("INSERT INTO host_aliases (ip, alias, created_at) VALUES ('10.0.0.1', 'nas', '2026-03-27')")
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ?", (D1,))
        # The filter expands to the alias and its address; every row from the address is "nas"
        assert _histogram(db, LogFilter(sources=("nas",)), [_s(D1, 10), _s(D1, 11)]) == [{6: 10}]

    def test_buckets_under_an_hour_read_the_files(self, db):
        _write(db, [_row(D1, 10, 10), _row(D1, 10, 40)])
        _seal_d1(db)
        with open_db(db) as conn:
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ?", (D1,))
        assert _histogram(db, LogFilter(), [_s(D1, 10), _s(D1, 10, 30), _s(D1, 11)]) == [{6: 1}, {6: 1}]

    def test_logs_outside_the_edges_or_the_filter_s_bounds_are_left_out(self, db):
        _write(db, [_row(D2, 0, 59), _row(D2, 1), _row(D2, 1, 20), _row(D2, 2)])
        found = _histogram(db, LogFilter(to_ts=_iso(_s(D2, 1, 10))), [_s(D2, 1), _s(D2, 2)])
        assert found == [{6: 1}]

    def test_logs_kept_in_the_legacy_table_are_counted(self, tmp_path):
        day_store.forget_id_blocks()
        day_store.forget_connections()
        path = tmp_path / "logs.db"
        with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
            run_migrations(path)
        with open_db(path) as conn:
            conn.executemany(
                "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
                "VALUES (?, ?, '10.0.0.1', 'nas', 'kernel', 1, ?, 'm', 'm')",
                [
                    ("2026-03-27T09:10:00.250000+00:00", "2026-03-27T09:10:00+00:00", 3),
                    ("2026-03-27T10:20:00+00:00", "2026-03-27T10:20:00+00:00", 6),
                ],
            )
        run_migrations(path, backup=False)
        _write(path, [_row(D1, 10, 40, severity=4)])
        try:
            found = _histogram(path, LogFilter(), [_s(D1, 9), _s(D1, 10), _s(D1, 11)])
        finally:
            day_store.forget_connections()
        assert found == [{3: 1}, {6: 1, 4: 1}]


class TestBuild:
    def test_counts_by_group_with_the_first_and_last_bucket_cut_to_the_range(self, db):
        _write(db, [_row(D2, 11, 45, severity=2), _row(D2, 11, 46, severity=6), _row(D2, 12, 10, severity=4)])
        now = datetime.datetime.fromtimestamp(_s(D2, 12, 30, 30), _UTC)
        with open_db(db) as conn:
            result = log_histogram.build_histogram(conn, LogFilter(), _UTC, now=now)
        assert (result.range_from, result.range_to, result.bucket_seconds) == (_s(D2, 11, 30, 30), _s(D2, 12, 30, 30), 60)
        assert len(result.buckets) == 61
        assert (result.buckets[0].start, result.buckets[0].end) == (_s(D2, 11, 30, 30), _s(D2, 11, 31))
        assert (result.buckets[-1].start, result.buckets[-1].end) == (_s(D2, 12, 30), _s(D2, 12, 30, 30))
        by_start = {b.start: b.counts for b in result.buckets}
        assert by_start[_s(D2, 11, 45)] == {"error": 1, "warning": 0, "info": 0, "debug": 0}
        assert by_start[_s(D2, 11, 46)]["info"] == 1
        assert by_start[_s(D2, 12, 10)]["warning"] == 1
        assert sum(sum(b.counts.values()) for b in result.buckets) == 3

    def test_a_range_ending_on_an_edge_counts_its_last_moment_in_the_last_bucket(self, db):
        # The filter's end is inclusive, as the Console's list reads it: a log stamped at 11:00 is in it
        _write(db, [_row(D2, 10, 59), _row(D2, 11)])
        log_filter = LogFilter(from_ts=_iso(_s(D2, 10)), to_ts=_iso(_s(D2, 11)))
        with open_db(db) as conn:
            result = log_histogram.build_histogram(conn, log_filter, _UTC)
        assert len(result.buckets) == 60
        assert result.buckets[-1].end == _s(D2, 11)
        assert result.buckets[-1].counts["info"] == 2


class TestLongRanges:
    def test_a_year_typed_by_mistake_is_cut_to_the_stored_logs(self, db):
        # The Console applies each edit of From, so "0202" can be sent while a year is typed
        _write(db, [_row(D1, 10), _row(D2, 1)])
        now = datetime.datetime.fromtimestamp(_s(D2, 2), _UTC)
        log_filter = LogFilter(from_ts="0202-03-27T10:00:00+00:00")
        with open_db(db) as conn:
            result = log_histogram.build_histogram(conn, log_filter, _UTC, now=now)
        assert result.range_from == _s(D1, 10)
        assert result.range_to == _s(D2, 2)
        assert len(result.buckets) <= 60
        assert sum(sum(b.counts.values()) for b in result.buckets) == 2

    def test_a_long_range_with_no_stored_log_keeps_its_last_year(self, db):
        log_filter = LogFilter(from_ts="0202-01-01T00:00:00+00:00", to_ts="0900-01-01T00:00:00+00:00")
        with open_db(db) as conn:
            result = log_histogram.build_histogram(conn, log_filter, _UTC)
        assert result.range_to - result.range_from == log_histogram.CLAMP_SPAN
        assert len(result.buckets) <= 60

    def test_a_year_or_less_is_kept_whatever_is_stored(self, db):
        _write(db, [_row(D2, 1)])
        now = datetime.datetime.fromtimestamp(_s(D2, 2), _UTC)
        log_filter = LogFilter(from_ts=_iso(_s(D2, 2) - 7 * 86400))
        with open_db(db) as conn:
            result = log_histogram.build_histogram(conn, log_filter, _UTC, now=now)
        assert result.range_from == _s(D2, 2) - 7 * 86400


@pytest_asyncio.fixture
async def client(db: Path, tmp_path: Path, monkeypatch):
    reset_crypto_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    get_or_create_master_key(tmp_path / ".secret_key")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def headers(client):
    return {"Cookie": f"{SESSION_COOKIE_NAME}={create_session_token(user_id=1)}"}


class TestRoute:
    @pytest.mark.asyncio
    async def test_returns_buckets_for_the_console_filters(self, client, headers, db):
        _write(db, [_row(D2, 1, 5, severity=3, app="cron"), _row(D2, 1, 6), _row(D2, 1, 7, severity=3)])
        resp = await client.get(
            "/api/logs/histogram",
            params={"from": _iso(_s(D2, 1)), "to": _iso(_s(D2, 2)), "app_name": "cron"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bucket_seconds"] == 60
        assert body["range_from"] == "2026-03-28T01:00:00+00:00"
        assert body["range_to"] == "2026-03-28T02:00:00+00:00"
        assert len(body["buckets"]) == 60
        assert body["buckets"][5] == {
            "start": "2026-03-28T01:05:00+00:00", "end": "2026-03-28T01:06:00+00:00",
            "error": 1, "warning": 0, "info": 0, "debug": 0,
        }
        assert sum(b["error"] + b["info"] for b in body["buckets"]) == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_time_is_refused(self, client, headers):
        resp = await client.get("/api/logs/histogram", params={"from": "yesterday-ish"}, headers=headers)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_needs_a_session(self, client):
        resp = await client.get("/api/logs/histogram")
        assert resp.status_code == 401
