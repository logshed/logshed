"""
Reading logs from day files gives the same results as reading them from the legacy table.

The same logs are stored three ways: all in the legacy logs table (a 1.3.x database), all in
day files (a new 1.4.0 database), and split, older logs in the legacy table and newer ones in
day files (an upgraded database). Every read of the log store must give the same answer on the
three, so day files change no API or MCP response.
"""

import datetime
import random
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

import app.core.migrations as migrations_mod
from app.core import day_files
from app.core.day_files import iso_to_us
from app.core.migrations import MIGRATIONS, run_migrations
from app.services import day_store, log_store
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db

_UTC = datetime.timezone.utc
START = datetime.datetime(2026, 3, 27, 20, 0, tzinfo=_UTC)

# (address, host name, app) of each sender; "dock1" is the Docker host name
SENDERS = [
    ("10.0.0.1", "nas", "kernel"),
    ("10.0.0.1", "nas", "smartd"),
    ("10.0.0.2", "", "dnsmasq"),
    ("10.0.0.3", "", "sshd"),
    ("10.0.0.3", "", "kernel"),
    ("docker", "dock1", "nginx"),
    ("10.0.0.5", "dock1", "postgres"),
    ("10.0.0.5", "", "cron"),
    ("10.0.0.6", "pve", ""),
]
WORDS = ["timeout", "disk", "full", "connection", "refused", "started", "stopped", "nginx", "499", "error"]


def _rows(count: int = 900, seed: int = 7) -> list[dict]:
    """Logs received over about three UTC days, in received order, some with late or early device times."""
    rng = random.Random(seed)
    rows = []
    for i in range(count):
        received = START + datetime.timedelta(minutes=5 * i, microseconds=rng.choice([0, 0, 1, 250_000, 999_999]))
        own = received
        if i % 37 == 0:
            own = received - datetime.timedelta(hours=rng.choice([1, 30, 50]))
        elif i % 41 == 0:
            own = received - datetime.timedelta(seconds=rng.randint(0, 50))
        ip, alias, app = SENDERS[i % len(SENDERS) if i % 13 else (i // 13) % len(SENDERS)]
        message = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 5))) + f" #{i}"
        raw = rng.choice([message, f"<13>1 {own.isoformat()} {alias or ip} {app} - - - {message}", f"other {i}"])
        rows.append({
            "timestamp": own.isoformat(),
            "received_at": received.isoformat(),
            "source_ip": ip,
            "source_alias": alias,
            "app_name": app,
            "facility": rng.choice([1, 3, 16]),
            "severity": rng.choice([0, 2, 3, 3, 4, 5, 6, 6, 6, 7]),
            "message": message,
            "raw": raw,
        })
    return rows


ROWS = _rows()
# The split database's switch-over: the first 300 logs are legacy rows (25 hours of them)
SPLIT_AT = 300


def _configure(path: Path) -> None:
    with open_db(path) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('docker_source_alias', 'dock1', datetime('now'))")
        conn.execute("INSERT INTO host_aliases (ip, alias, created_at) VALUES ('10.0.0.2', 'router', datetime('now'))")


def _insert_legacy(path: Path, rows: list[dict]) -> None:
    with open_db(path) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
            "VALUES (:timestamp, :received_at, :source_ip, :source_alias, :app_name, :facility, :severity, :message, :raw)",
            rows,
        )
    index_pending_logs(path)


def _write_days(path: Path, rows: list[dict]) -> None:
    with open_db(path) as conn:
        day_store.write_rows(conn, [dict(r) for r in rows])
        for day, _state in day_store.catalogue_days(conn):
            if day != day_files.LEGACY_DAY:
                day_conn = day_store.connection(day_files.day_path(path, day))
                while day_store.index_pending(day_conn, 100):
                    pass


@pytest.fixture(scope="module")
def layouts(tmp_path_factory) -> dict[str, Path]:
    """The same logs as a legacy table, as day files, and split between the two."""
    day_store.forget_id_blocks()
    base = tmp_path_factory.mktemp("day-store")
    paths = {}
    legacy = base / "legacy" / "logs.db"
    legacy.parent.mkdir()
    with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
        run_migrations(legacy)
    _configure(legacy)
    _insert_legacy(legacy, ROWS)
    paths["legacy"] = legacy

    days = base / "days" / "logs.db"
    days.parent.mkdir()
    run_migrations(days)
    _configure(days)
    _write_days(days, ROWS)
    paths["days"] = days

    split = base / "split" / "logs.db"
    split.parent.mkdir()
    with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
        run_migrations(split)
    _configure(split)
    _insert_legacy(split, ROWS[:SPLIT_AT])
    run_migrations(split, backup=False)
    _write_days(split, ROWS[SPLIT_AT:])
    paths["split"] = split

    # The same two with every day sealed: ranges and whole-file counts come from the catalogue
    for name, source in (("sealed", days), ("split_sealed", split)):
        copy = base / name / "logs.db"
        copy.parent.mkdir()
        for f in source.parent.iterdir():
            copy.parent.joinpath(f.name).write_bytes(f.read_bytes())
        day_store.forget_connections()
        with open_db(copy) as conn:
            sealed = day_store.seal_finished_days(conn)
            assert sealed and all(state == "sealed" for day, state in day_store.catalogue_days(conn) if day != "legacy")
        paths[name] = copy
    yield paths
    day_store.forget_connections()


def _each(layouts: dict[str, Path], fn):
    """fn(conn) on each layout, as plain values."""
    results = {}
    for name, path in layouts.items():
        with open_db(path, sqlite3.Row) as conn:
            results[name] = _plain(fn(conn))
    return results


def _plain(value):
    if isinstance(value, sqlite3.Row):
        return dict(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_plain(v) for v in value)
    if hasattr(value, "__dataclass_fields__"):
        return {k: _plain(getattr(value, k)) for k in value.__dataclass_fields__}
    return value


def _assert_same(results: dict) -> None:
    reference = results["legacy"]
    for name in ("days", "split", "sealed", "split_sealed"):
        assert results[name] == reference, name


def test_the_layouts_hold_what_they_should(layouts):
    with open_db(layouts["days"], sqlite3.Row) as conn:
        files = {day for day, _ in day_store.catalogue_days(conn)}
        assert files == {"legacy", "2026-03-27", "2026-03-28", "2026-03-29", "2026-03-30"}
        assert [p.legacy for p in day_store.parts(conn)] == [False] * 4
    with open_db(layouts["split"], sqlite3.Row) as conn:
        assert sum(p.legacy for p in day_store.parts(conn)) == 1
        assert len(day_store.parts(conn)) > 2
    # Ids are the same in every layout
    results = _each(layouts, lambda c: [r["id"] for r in log_store.get_many(c, list(range(1, 1000)))])
    _assert_same(results)
    assert len(results["legacy"]) == len(ROWS)


def test_rows_come_back_exactly_as_they_were_stored(layouts):
    with open_db(layouts["days"], sqlite3.Row) as conn:
        _, rows = log_store.search(conn, LogFilter(), fetch=len(ROWS), count_limit=len(ROWS))
    by_id = {r["id"]: dict(r) for r in rows}
    for i, stored in enumerate(ROWS, start=1):
        assert by_id[i] == {"id": i, **stored}


FILTERS = [
    LogFilter(),
    LogFilter(query="timeout"),
    LogFilter(query='"connection refused"'),
    LogFilter(query="nginx 499"),
    LogFilter(query="host:nas"),
    LogFilter(query="app:kernel OR disk"),
    LogFilter(query="err"),
    LogFilter(query="zzznotthere"),
    LogFilter(query='bad"syntax('),
    LogFilter(sources=("nas",)),
    LogFilter(sources=("10.0.0.5",)),
    LogFilter(sources=("router",)),
    LogFilter(sources=("10.0.0.2",)),
    LogFilter(sources=("dock1", "10.0.0.3")),
    LogFilter(sources=("docker",)),
    LogFilter(apps=("kernel",)),
    LogFilter(apps=("kernel", "nginx", "")),
    LogFilter(apps=("nothere",)),
    LogFilter(severity_max=3),
    LogFilter(from_ts="2026-03-28T00:00:00+00:00"),
    LogFilter(to_ts="2026-03-28T12:00:00+00:00"),
    LogFilter(from_ts="2026-03-28T23:00:00+00:00", to_ts="2026-03-29T01:00:00+00:00"),
    LogFilter(from_ts="2026-03-28T23:00:00+00:00", to_ts="2026-03-29T01:00:00+00:00", query="disk"),
    LogFilter(from_ts="2027-01-01T00:00:00+00:00"),
    LogFilter(log_ids=(1, 2, 300, 301, 899, 5000)),
    LogFilter(log_ids=(1, 2, 300, 301, 899), sources=("nas",)),
    LogFilter(query="disk", sources=("nas", "10.0.0.2"), apps=("kernel", "dnsmasq"), severity_max=6),
]


@pytest.mark.parametrize("log_filter", FILTERS, ids=lambda f: repr(f)[10:70])
@pytest.mark.parametrize("fetch, count_limit, offset", [(50, 1001, 0), (10, 5, 0), (20, 300, 30), (900, 2000, 0), (7, 0, 3)])
def test_search_gives_the_same_page_and_count(layouts, log_filter, fetch, count_limit, offset):
    _assert_same(_each(layouts, lambda c: log_store.search(c, log_filter, fetch=fetch, count_limit=count_limit, offset=offset)))


@pytest.mark.parametrize("log_filter", FILTERS[:12], ids=lambda f: repr(f)[10:70])
def test_keyset_pages_give_the_same_rows(layouts, log_filter):
    def _pages(conn):
        pages = []
        before = None
        for _ in range(6):
            _, rows = log_store.search(conn, log_filter, fetch=40, count_limit=100, before=before, include_raw=False)
            pages.append(rows)
            if not rows:
                break
            before = (rows[-1]["timestamp"], rows[-1]["id"])
        return pages

    _assert_same(_each(layouts, _pages))


@pytest.mark.parametrize("log_filter", FILTERS, ids=lambda f: repr(f)[10:70])
@pytest.mark.parametrize("group_by", [None, "severity", "app", "source"])
def test_counts_are_the_same(layouts, log_filter, group_by):
    _assert_same(_each(layouts, lambda c: log_store.count(c, log_filter, group_by)))


@pytest.mark.parametrize("group_by", ["app", "source"])
def test_truncated_groups_are_the_same(layouts, group_by):
    _assert_same(_each(layouts, lambda c: log_store.count(c, LogFilter(), group_by, group_limit=3)))


@pytest.mark.parametrize("log_id", [1, 2, 150, 299, 300, 301, 302, 450, 880, 900])
@pytest.mark.parametrize("same_app", [False, True])
def test_context_is_the_same_across_midnight_and_the_switch_over(layouts, log_id, same_app):
    _assert_same(_each(layouts, lambda c: log_store.context(c, log_id, 12, 12, same_app=same_app)))


def test_context_of_a_missing_log_is_none(layouts):
    _assert_same(_each(layouts, lambda c: log_store.context(c, 99999, 5, 5)))


def test_get_many_is_the_same(layouts):
    _assert_same(_each(layouts, lambda c: log_store.get_many(c, [900, 5, 299, 300, 301, 77, 12345])))


def test_seen_names_are_the_same(layouts):
    _assert_same(_each(layouts, lambda c: log_store.seen_names(c, 502, 10000)))


def test_seen_names_are_capped(layouts):
    _assert_same(_each(layouts, lambda c: log_store.seen_names(c, 2, 3)))


def test_figures_are_the_same(layouts):
    _assert_same(_each(layouts, lambda c: (log_store.max_id(c), log_store.count_rows(c), log_store.count_rows(c, after_id=250))))
    for since in ("2026-03-27T00:00:00+00:00", "2026-03-28T22:00:00+00:00", "2026-03-30T00:00:00+00:00"):
        hour = (datetime.datetime.fromisoformat(since) + datetime.timedelta(hours=1)).isoformat()
        _assert_same(_each(layouts, lambda c: log_store.error_figures(c, hour, since)))
        _assert_same(_each(layouts, lambda c: log_store.digest_figures(c, since)))


def test_a_search_does_not_open_older_files_once_its_page_is_full(layouts, monkeypatch):
    opened = []
    real_search_part = log_store._search_part

    def _spy(table_conn, day_file, where_sql, params, log_filter, fetch, *args):
        opened.append(fetch)
        return real_search_part(table_conn, day_file, where_sql, params, log_filter, fetch, *args)

    monkeypatch.setattr(log_store, "_search_part", _spy)
    with open_db(layouts["days"], sqlite3.Row) as conn:
        total, rows = log_store.search(conn, LogFilter(), fetch=10, count_limit=10)
    assert total == 10 and len(rows) == 10
    # The newest file fills the page and the count; the older files are not searched
    assert opened == [10]


def test_a_search_stops_when_its_client_goes_away(layouts):
    """run_db_query passes its interrupt check (the 2-minute limit, a client gone) to the day files."""
    calls = []

    def _stop() -> bool:
        calls.append(1)
        return True

    with open_db(layouts["days"], sqlite3.Row) as conn, day_store.use_interrupt(_stop):
        with pytest.raises(sqlite3.OperationalError, match="interrupt"):
            log_store.search(conn, LogFilter(query="timeout OR disk OR full"), fetch=900, count_limit=100000)
    assert calls


def test_a_new_day_file_appears_whole_in_wal_mode(tmp_path):
    """The file is built under a temporary name, so a reader never opens it without its tables."""
    path = tmp_path / "logs-2026-11-13-UTC.db"
    conn = day_store.connection(path, create=True)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []
    # A second builder finds the file there and keeps it
    day_store._build(path)
    assert conn.execute("SELECT COUNT(*) FROM fts_index_state").fetchone()[0] == 1
    day_store.close_thread_connections()


def test_the_facet_walk_seeks_each_distinct_source_once(tmp_path):
    """
    The facet walk reads one index entry per distinct (address, app, host), not every row of an
    address: on a busy file from few addresses, a row-value range read most of the file per step.
    SQLite's instruction count stands in for time, so the check does not depend on the machine.
    """
    day_store.forget_id_blocks()
    path = tmp_path / "logs.db"
    run_migrations(path)
    received = datetime.datetime(2026, 11, 13, 1, tzinfo=datetime.timezone.utc)
    senders = [("10.0.0.1", "nas", "smartd"), ("10.0.0.1", "nas", "nginx"), ("10.0.0.1", "", "cron"),
               ("10.0.0.2", "router", "filterlog"), ("10.0.0.2", "router", "dhcpd"), ("10.0.0.2", "ap", "hostapd")]
    rows = []
    for i in range(20_000):
        ip, alias, app = senders[i % len(senders)]
        stamp = (received + datetime.timedelta(seconds=i)).isoformat()
        rows.append({"timestamp": stamp, "received_at": stamp, "source_ip": ip, "source_alias": alias,
                     "app_name": app, "message": f"line {i}", "raw": f"line {i}"})
    with open_db(path) as conn:
        day_store.write_rows(conn, rows)
    day_conn = day_store.connection(day_files.day_path(path, "2026-11-13"))
    steps = []
    day_conn.set_progress_handler(lambda: steps.append(1), 1000)
    try:
        seen, first_pair = log_store._day_seen(day_conn, 502, 10000)
    finally:
        day_conn.set_progress_handler(None, 0)
    assert first_pair is not None
    assert sorted((ip, app, host) for host, app, ip in seen.alias_apps) == sorted(
        (ip, app, alias) for ip, alias, app in senders if alias
    )
    assert seen.bare_ip_apps == [("cron", "10.0.0.1")]
    # Six seeks and their name lookups take a few thousand instructions; reading the rows took hundreds of thousands
    assert len(steps) < 20, f"{len(steps) * 1000} instructions"
    day_store.close_thread_connections()


def test_error_figures_and_the_digest_read_only_the_time_range_of_a_sealed_day(tmp_path):
    """
    The top error hosts of the last 24 hours read the time index, which holds the severity: grouped
    by the bare host column, the planner skip-scanned the host index and read the table row of every
    log in the range (0.7 s on a 1.7M-row day). The plan is checked, because in a small file both
    plans take about the same instructions; the cost is the table pages read.
    """
    day_store.forget_id_blocks()
    path = tmp_path / "logs.db"
    run_migrations(path)
    start = datetime.datetime(2026, 11, 13, tzinfo=datetime.timezone.utc)
    rows = []
    for i in range(20_000):
        stamp = (start + datetime.timedelta(seconds=4 * i)).isoformat()
        rows.append({"timestamp": stamp, "received_at": stamp, "source_ip": f"10.0.0.{i % 40}",
                     "source_alias": f"host{i % 40}", "app_name": f"app{i % 7}", "severity": 3 if i % 5 == 0 else 6,
                     "message": f"line {i}", "raw": f"line {i}"})
    with open_db(path) as conn:
        day_store.write_rows(conn, rows)
        day_conn = day_store.connection(day_files.day_path(path, "2026-11-13"))
        while day_store.index_pending(day_conn, 5000):
            pass
        assert day_store.seal(conn, "2026-11-13")
    # The second half of the day: 10,000 rows, 2,000 of them errors
    since = (start + datetime.timedelta(seconds=4 * 10_000)).isoformat()
    with open_db(path, sqlite3.Row) as conn:
        found = log_store.error_figures(conn, since[:19], since[:19])
    assert found.severity_counts["count_24h"] == 2000
    assert sum(r["cnt"] for r in found.top_hosts) == 5 * 250  # errors are every fifth row: 8 hosts of 250
    day_conn = sqlite3.connect(day_files.day_path(path, "2026-11-13"))
    try:
        # The daily digest's errors per host, address and app had the same plan
        plans = [
            [r[3] for r in day_conn.execute("EXPLAIN QUERY PLAN " + sql, (iso_to_us(since),))]
            for sql in (log_store._DAY_ERROR_HOSTS_SQL, log_store._DAY_DIGEST_ERRORS_SQL)
        ]
    finally:
        day_conn.close()
    for plan in plans:
        assert any("idx_logs_time_sev" in step for step in plan), plan
    day_store.close_thread_connections()
