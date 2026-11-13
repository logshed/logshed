"""
Day files and the server's time zone. Files are named and rolled over by UTC day, whatever TZ
says; user-facing days (Today) stay in the server's zone and map to files through their time
ranges; retention cutoffs are UTC. Europe/London changes its clocks at 01:00 UTC on
2026-03-29 (forward: a 23-hour local day) and 2026-10-25 (back: a 25-hour local day).
"""

import datetime
import sqlite3
import time
import types
import zoneinfo

import pytest

from app.core import day_files
from app.core import utils as utils_mod
from app.core.migrations import run_migrations
from app.services import day_store, log_store
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db

_UTC = datetime.timezone.utc
LONDON = zoneinfo.ZoneInfo("Europe/London")


@pytest.fixture
def tz(monkeypatch):
    """Set the server's TZ (and the process's local zone) for one test."""
    def set_zone(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    set_zone("Europe/London")
    yield set_zone
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def db(tmp_path):
    day_store.forget_id_blocks()
    day_store.forget_connections()
    path = tmp_path / "logs.db"
    run_migrations(path)
    yield path
    day_store.forget_connections()


def _log(at: datetime.datetime, message: str) -> dict:
    iso = at.astimezone(_UTC).isoformat()
    return {"timestamp": iso, "received_at": iso, "source_ip": "10.0.0.1", "source_alias": "nas",
            "app_name": "kernel", "facility": 1, "severity": 6, "message": message, "raw": message}


def _write(path, rows) -> None:
    with open_db(path) as conn:
        day_store.write_rows(conn, rows)
    index_pending_logs(path)


def _local(y, mo, d, h=0, mi=0, s=0, us=0, fold=0) -> datetime.datetime:
    return datetime.datetime(y, mo, d, h, mi, s, us, tzinfo=LONDON, fold=fold)


def _files(path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if day_files.day_from_file_name(p.name))


def _pin_now(monkeypatch, at: datetime.datetime) -> None:
    """Make "now" in app.core.utils (which Today and Yesterday read) the given moment."""
    class Pinned(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            moment = at.astimezone(_UTC)
            return moment.astimezone(tz) if tz else moment.astimezone().replace(tzinfo=None)

    fake = types.SimpleNamespace(**{k: getattr(datetime, k) for k in dir(datetime) if not k.startswith("_")})
    fake.datetime = Pinned
    monkeypatch.setattr(utils_mod, "datetime", fake)


@pytest.mark.parametrize("change_day", ["2026-03-29", "2026-10-25"])
def test_files_roll_over_at_utc_midnight_across_a_clock_change(tz, db, change_day):
    day = datetime.date.fromisoformat(change_day)
    before = datetime.datetime.combine(day, datetime.time(), tzinfo=_UTC) - datetime.timedelta(microseconds=1)
    rows = [
        _log(before, "last of the day before"),
        _log(before + datetime.timedelta(microseconds=1), "first of the change day"),
        # 01:00 UTC is the change itself; 00:59 and 01:01 UTC are an hour apart in British time
        _log(before + datetime.timedelta(minutes=59, microseconds=1), "just before the change"),
        _log(before + datetime.timedelta(minutes=61, microseconds=1), "just after the change"),
        _log(before + datetime.timedelta(days=1), "last of the change day"),
        _log(before + datetime.timedelta(days=1, microseconds=1), "first of the day after"),
    ]
    _write(db, rows)
    prev, nxt = (day - datetime.timedelta(days=1)).isoformat(), (day + datetime.timedelta(days=1)).isoformat()
    assert _files(db) == [day_files.file_name(prev), day_files.file_name(change_day), day_files.file_name(nxt)]
    with open_db(db, sqlite3.Row) as conn:
        by_file = {
            p.day: sorted(r["message"] for r in p.conn.execute("SELECT message FROM logs"))
            for p in day_store.parts(conn)
        }
    assert by_file == {
        prev: ["last of the day before"],
        change_day: ["first of the change day", "just after the change", "just before the change", "last of the change day"],
        nxt: ["first of the day after"],
    }


@pytest.mark.parametrize(
    "local_day,hours",
    [(datetime.date(2026, 3, 29), 23), (datetime.date(2026, 10, 25), 25)],
)
def test_today_on_a_clock_change_day_is_that_local_day_across_two_files(tz, db, monkeypatch, local_day, hours):
    start = _local(local_day.year, local_day.month, local_day.day)
    end = start.astimezone(_UTC) + datetime.timedelta(hours=hours)  # the next local midnight
    assert end.astimezone(LONDON).time() == datetime.time()
    rows = [
        _log(start.astimezone(_UTC) - datetime.timedelta(microseconds=1), "yesterday's last"),
        _log(start, "today's first"),
        _log(start.astimezone(_UTC) + datetime.timedelta(hours=hours // 2), "today's middle"),
        _log(end - datetime.timedelta(microseconds=1), "today's last"),
        _log(end, "tomorrow's first"),
    ]
    if hours == 25:
        # 01:30 happens twice in British time on the 25-hour day: both are today
        rows += [_log(_local(2026, 10, 25, 1, 30, fold=0), "first 01:30"), _log(_local(2026, 10, 25, 1, 30, fold=1), "second 01:30")]
    _write(db, rows)
    # Local midnight in Britain is 23:00 UTC in summer: a local day spans two UTC files
    with open_db(db) as conn:
        assert len({d for d, _ in day_store.catalogue_days(conn)} - {"legacy"}) >= 2

    _pin_now(monkeypatch, end - datetime.timedelta(minutes=30))
    today = LogFilter.parse(from_="today", to=(end - datetime.timedelta(microseconds=1)).isoformat())
    yesterday = LogFilter.parse(from_="yesterday", to=(start.astimezone(_UTC) - datetime.timedelta(microseconds=1)).isoformat())
    with open_db(db, sqlite3.Row) as conn:
        _total, found = log_store.search(conn, today, fetch=50, count_limit=50)
        got = sorted(r["message"] for r in found)
        _total, before = log_store.search(conn, yesterday, fetch=50, count_limit=50)
    expected = ["today's first", "today's last", "today's middle"]
    if hours == 25:
        expected = sorted(expected + ["first 01:30", "second 01:30"])
    assert got == expected
    assert [r["message"] for r in before] == ["yesterday's last"]


def test_retention_drops_the_right_files_across_a_clock_change(tz, db):
    # Logs on each UTC day around the October change, at local noon
    days = ["2026-10-24", "2026-10-25", "2026-10-26"]
    _write(db, [_log(datetime.datetime.fromisoformat(d).replace(hour=12, tzinfo=LONDON), d) for d in days])
    with open_db(db) as conn:
        # Cutoff 00:30 UTC on the 26th (00:30 GMT): the 24th and the 25-hour 25th are wholly past it
        log_store.prune(conn, "2026-10-26T00:30:00+00:00", lambda: None)
    assert _files(db) == [day_files.file_name("2026-10-26")]
    with open_db(db) as conn:
        # 00:30 British summer time on the 25th is 23:30 UTC on the 24th: only the 23rd and before would go
        assert log_store.count_prunable(conn, _local(2026, 10, 25, 0, 30).astimezone(_UTC).isoformat()) == 0


def test_changing_tz_between_restarts_changes_no_file_names(tz, db):
    at = datetime.datetime(2026, 6, 30, 22, 30, tzinfo=_UTC)  # 23:30 in London, 18:30 in New York
    _write(db, [_log(at, "under London")])
    named = _files(db)
    day_store.forget_id_blocks()
    day_store.forget_connections()
    tz("America/New_York")
    run_migrations(db)  # the next start
    _write(db, [_log(at + datetime.timedelta(minutes=10), "under New York")])
    assert _files(db) == named == [day_files.file_name("2026-06-30")]
    with open_db(db) as conn:
        assert [d for d, _ in day_store.catalogue_days(conn) if d != "legacy"] == ["2026-06-30"]
    tz("Asia/Tokyo")
    with open_db(db, sqlite3.Row) as conn:
        found = log_store.search(conn, LogFilter(), fetch=10, count_limit=10)[1]
    assert [r["timestamp"] for r in found] == [
        (at + datetime.timedelta(minutes=10)).isoformat(), at.isoformat(),
    ]
