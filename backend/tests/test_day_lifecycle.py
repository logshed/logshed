"""
A day file's life after its day: sealing (full-text index compacted, figures and counts in the
catalogue), a late batch opening it again, targeted deletes and relabels keeping the figures
right, whole-file retention, the legacy table's drop once retention has emptied it, the checks
at start-up, and the Storage page's figures.
"""

import datetime
import sqlite3
from pathlib import Path
from unittest import mock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.core.migrations as migrations_mod
from app.core import day_files, lifecycle
from app.core import pipeline as pipeline_mod
from app.core.migrations import MIGRATIONS, run_migrations
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services import day_store, log_store, retention, storage_metrics
from app.services.fts_indexer import index_pending_logs
from app.services.log_query import LogFilter
from tests.db_helpers import open_db, stored_logs

_UTC = datetime.timezone.utc
D1, D2, D3 = "2026-03-27", "2026-03-28", "2026-03-29"
CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _us(day: str, hour: int = 0, minute: int = 0) -> int:
    return day_files.day_start_us(day) + (hour * 60 + minute) * 60_000_000


def _row(day: str, hour: int, host: str = "nas", app: str = "kernel", severity: int = 6, message: str = "disk full") -> dict:
    at = datetime.datetime.fromisoformat(day).replace(hour=hour, tzinfo=_UTC).isoformat()
    return {
        "timestamp": at, "received_at": at, "source_ip": "10.0.0.1", "source_alias": host,
        "app_name": app, "facility": 1, "severity": severity, "message": message, "raw": message,
    }


def _write(path: Path, rows: list[dict]) -> None:
    with open_db(path) as conn:
        day_store.write_rows(conn, [dict(r) for r in rows])


def _catalogue(path: Path) -> dict[str, dict]:
    with open_db(path, sqlite3.Row) as conn:
        return {r["day"]: dict(r) for r in conn.execute("SELECT * FROM log_files")}


def _counts(path: Path, day: str) -> dict:
    with open_db(path) as conn:
        return {
            (hour, host, app, sev): cnt
            for hour, host, app, sev, cnt in conn.execute(
                "SELECT hour, host, app, severity, count FROM log_file_counts WHERE day = ?", (day,)
            )
        }


@pytest.fixture
def db(tmp_path: Path) -> Path:
    day_store.forget_id_blocks()
    day_store.forget_connections()
    storage_metrics.clear_storage_snapshot_cache()
    path = tmp_path / "logs.db"
    run_migrations(path)
    yield path
    day_store.forget_connections()


@pytest.fixture
def three_days(db: Path) -> Path:
    _write(db, [
        _row(D1, 1), _row(D1, 1), _row(D1, 2, app="smartd", severity=3), _row(D1, 23, host="pve"),
        _row(D2, 5, host="pve", app="cron"), _row(D2, 6),
        _row(D3, 9, message="timeout"),
    ])
    index_pending_logs(db)
    return db


def _seal_all(path: Path) -> list[str]:
    with open_db(path) as conn:
        return day_store.seal_finished_days(conn, now_us=_us("2026-04-01"))


class TestSealing:
    def test_a_finished_fully_indexed_day_is_sealed_with_its_figures(self, db):
        _write(db, [_row(D1, 1), _row(D1, 1), _row(D1, 2, app="smartd", severity=3), _row(D1, 23, host="pve")])
        with open_db(db) as conn:
            # Not yet indexed: not sealed
            assert day_store.seal_finished_days(conn, now_us=_us(D2, 1)) == []
        index_pending_logs(db)
        with open_db(db) as conn:
            # Indexed, but the day ended only a minute ago: the queue may still be writing it
            assert day_store.seal_finished_days(conn, now_us=_us(D2, 0, 1)) == []
            assert day_store.seal_finished_days(conn, now_us=_us(D2, 0, 3)) == [D1]
        entry = _catalogue(db)[D1]
        assert entry["state"] == "sealed" and entry["sealed_at"]
        assert entry["row_count"] == 4
        assert (entry["min_ts"], entry["max_ts"]) == (_us(D1, 1), _us(D1, 23))
        assert entry["size_bytes"] > 0
        hour = lambda h: _us(D1, h) // 1_000_000  # noqa: E731
        assert _counts(db, D1) == {
            (hour(1), "nas", "kernel", 6): 2,
            (hour(2), "nas", "smartd", 3): 1,
            (hour(23), "pve", "kernel", 6): 1,
        }
        # Still searchable by word after the index was compacted
        with open_db(db, sqlite3.Row) as conn:
            total, _rows = log_store.search(conn, LogFilter(query="disk"), fetch=10, count_limit=10)
        assert total == 4

    def test_today_is_never_sealed(self, db):
        today = datetime.datetime.now(_UTC).date().isoformat()
        _write(db, [_row(today, 0)])
        index_pending_logs(db)
        with open_db(db) as conn:
            assert day_store.seal_finished_days(conn) == []
        assert _catalogue(db)[today]["state"] == "open"

    def test_a_late_batch_opens_a_sealed_day_again_until_it_is_sealed_again(self, three_days):
        _seal_all(three_days)
        _write(three_days, [_row(D1, 22, app="late")])
        entry = _catalogue(three_days)[D1]
        assert entry["state"] == "open" and entry["sealed_at"] is None
        index_pending_logs(three_days)
        assert _seal_all(three_days) == [D1]
        entry = _catalogue(three_days)[D1]
        assert entry["state"] == "sealed" and entry["row_count"] == 5
        assert sum(_counts(three_days, D1).values()) == 5

    def test_sealed_files_are_not_opened_for_ranges_or_whole_file_counts(self, three_days):
        _seal_all(three_days)
        day_store.forget_connections()
        opened = []
        real = day_store.connection

        def spy(path, create=False):
            opened.append(Path(path).name)
            return real(path, create)

        with mock.patch.object(day_store, "connection", spy), open_db(three_days) as conn:
            assert [p.day for p in day_store.parts(conn)] == [D3, D2, D1]
            assert log_store.count_rows(conn) == 7
            assert log_store.max_id(conn) == 7
            # Every row has a host name, so hosts come from the catalogue too
            assert log_store.count(conn, LogFilter(), "source") == (7, "source", {"nas": 5, "pve": 2}, False)
            assert log_store.count(conn, LogFilter(), "app")[2] == {"kernel": 5, "smartd": 1, "cron": 1}
            assert log_store.count(conn, LogFilter(), "severity")[0] == 7
            # A range that cuts a day reads that day's file
            cut = LogFilter(from_ts=datetime.datetime.fromisoformat(D1).replace(hour=2, tzinfo=_UTC).isoformat())
            assert log_store.count(conn, cut, None)[0] == 5
        assert opened == [day_files.file_name(D1)]

    def test_a_host_without_a_name_is_counted_by_address_from_the_file(self, db):
        _write(db, [_row(D1, 1, host=""), _row(D1, 2)])
        index_pending_logs(db)
        _seal_all(db)
        with open_db(db) as conn:
            assert log_store.count(conn, LogFilter(), "source")[2] == {"10.0.0.1": 1, "nas": 1}

    def test_a_targeted_delete_or_relabel_rewrites_a_sealed_days_figures(self, three_days):
        _seal_all(three_days)
        with open_db(three_days) as conn:
            deleted = log_store.delete(conn, LogFilter(apps=("smartd",)), 100, lambda: None)
        assert deleted == 1
        assert _catalogue(three_days)[D1]["row_count"] == 3
        assert {k[2] for k in _counts(three_days, D1)} == {"kernel"}
        with open_db(three_days) as conn:
            log_store.relabel_host(conn, "10.0.0.1", "storage")
        assert {k[1] for k in _counts(three_days, D1)} == {"storage"}
        assert _catalogue(three_days)[D1]["state"] == "sealed"
        with open_db(three_days) as conn:
            assert log_store.count(conn, LogFilter(), "source")[2] == {"storage": 6}


class TestRetention:
    def test_day_files_go_whole_once_their_whole_day_is_past_the_cutoff(self, three_days):
        _seal_all(three_days)
        with open_db(three_days) as conn:
            # The cutoff is inside D2: D2 stays (a log is kept up to a day longer)
            assert log_store.prune(conn, datetime.datetime.fromisoformat(D2).replace(hour=12, tzinfo=_UTC).isoformat(), lambda: None) == 4
        assert set(_catalogue(three_days)) == {"legacy", D2, D3}
        assert not day_files.day_path(three_days, D1).exists()
        assert _counts(three_days, D1) == {}

    def test_a_day_file_with_no_logs_left_still_expires(self, three_days):
        with open_db(three_days) as conn:
            log_store.delete(conn, LogFilter(to_ts=f"{D1}T23:59:59+00:00"), 100, lambda: None)
            assert log_store.prune(conn, f"{D2}T00:00:00+00:00", lambda: None) == 0
        assert D1 not in _catalogue(three_days)
        assert not day_files.day_path(three_days, D1).exists()

    def test_a_crash_while_deleting_a_file_is_finished_by_the_next_prune(self, three_days):
        with open_db(three_days) as conn:
            conn.execute("UPDATE log_files SET state = 'deleting' WHERE day = ?", (D1,))
        with open_db(three_days) as conn:
            assert [p.day for p in day_store.parts(conn)] == [D3, D2]
            log_store.prune(conn, f"{D2}T00:00:00+00:00", lambda: None)
        assert D1 not in _catalogue(three_days)
        assert not day_files.day_path(three_days, D1).exists()


def _upgraded_with_legacy(path: Path, rows: list[tuple[str, str]]) -> None:
    with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
        run_migrations(path)
    with open_db(path) as conn:
        conn.executemany(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
            "VALUES (?, ?, '10.0.0.1', 'nas', 'kernel', 1, 6, ?, ?)",
            [(ts, ts, msg + " " + "x" * 2000, msg) for ts, msg in rows],
        )
    run_migrations(path, backup=False)


class TestLegacyTableEnd:
    @pytest.fixture(autouse=True)
    def _fresh(self):
        day_store.forget_id_blocks()
        day_store.forget_connections()
        storage_metrics.clear_storage_snapshot_cache()
        yield
        day_store.forget_connections()

    @pytest.mark.asyncio
    async def test_the_emptied_legacy_table_is_dropped_and_logs_db_compacted(self, tmp_path):
        path = tmp_path / "logs.db"
        old = (datetime.datetime.now(_UTC) - datetime.timedelta(days=40)).isoformat()
        _upgraded_with_legacy(path, [(old, f"old {i}") for i in range(2000)])
        today = datetime.datetime.now(_UTC).replace(microsecond=0)
        _write(path, [{**_row(today.date().isoformat(), today.hour), "message": "new one"}])
        size_before = path.stat().st_size

        result = await retention.execute_prune_async(path, retention_days=14)

        assert result["legacy_dropped"] is True and result["deleted_logs"] == 2000
        with open_db(path) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert not {"logs", "logs_fts", "fts_index_state", "logs_ai", "logs_ad", "logs_au"} & names
            assert "legacy" not in {d for d, _ in day_store.catalogue_days(conn)}
            assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
        assert path.stat().st_size < size_before / 4
        # The day files are untouched, and reads and writes go on without the table
        assert [r["message"] for r in stored_logs(path)] == ["new one"]
        _write(path, [{**_row(today.date().isoformat(), today.hour), "message": "after"}])
        index_pending_logs(path)
        with open_db(path, sqlite3.Row) as conn:
            assert log_store.search(conn, LogFilter(query="after"), fetch=5, count_limit=5)[0] == 1

    @pytest.mark.asyncio
    async def test_no_compaction_without_room_and_the_drop_still_happens(self, tmp_path):
        path = tmp_path / "logs.db"
        old = (datetime.datetime.now(_UTC) - datetime.timedelta(days=40)).isoformat()
        _upgraded_with_legacy(path, [(old, "old")])
        with mock.patch.object(retention, "check_vacuum_headroom", side_effect=retention.InsufficientDiskSpaceError("full")):
            result = await retention.execute_prune_async(path, retention_days=14)
        assert result["legacy_dropped"] is True
        with open_db(path) as conn:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'logs'").fetchone() is None

    def test_the_legacy_table_stays_while_it_holds_logs(self, tmp_path):
        path = tmp_path / "logs.db"
        now = datetime.datetime.now(_UTC)
        _upgraded_with_legacy(path, [((now - datetime.timedelta(days=40)).isoformat(), "old"), ((now - datetime.timedelta(days=2)).isoformat(), "kept")])
        result = retention.execute_prune(path, retention_days=14)
        assert result["legacy_dropped"] is False and result["deleted_logs"] == 1
        assert [r["message"].split()[0] for r in stored_logs(path)] == ["kept"]

    def test_a_database_before_schema_4_keeps_its_logs_table(self, tmp_path):
        path = tmp_path / "logs.db"
        with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
            run_migrations(path)
            result = retention.execute_prune(path, retention_days=14)
        assert result["legacy_dropped"] is False
        with open_db(path) as conn:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'logs'").fetchone() is not None


class TestStartUpCheck:
    def test_unfinished_deletes_are_finished_and_unlisted_files_named_not_used(self, three_days):
        folder = three_days.parent
        with open_db(three_days) as conn:
            conn.execute("UPDATE log_files SET state = 'deleting' WHERE day = ?", (D1,))
        stray = folder / "logs-2020-01-01-UTC.db"
        stray.write_bytes(b"not ours")
        half_built = folder / ".logs-2026-03-30-UTC.db.123.456.tmp"
        half_built.write_bytes(b"")
        with open_db(three_days) as conn:
            found = day_store.check_files(conn)
            assert found.finished_deletes == [D1]
            assert found.unlisted == ["logs-2020-01-01-UTC.db"]
            assert "2020-01-01" not in {p.day for p in day_store.parts(conn)}
        assert not day_files.day_path(three_days, D1).exists() and D1 not in _catalogue(three_days)
        assert stray.read_bytes() == b"not ours"
        assert not half_built.exists()


class TestStorageFigures:
    def test_the_snapshot_counts_day_files_and_lists_them(self, tmp_path):
        day_store.forget_id_blocks()
        storage_metrics.clear_storage_snapshot_cache()
        path = tmp_path / "logs.db"
        _upgraded_with_legacy(path, [("2026-03-20T10:00:00+00:00", "old a"), ("2026-03-21T10:00:00+00:00", "old b")])
        _write(path, [_row(D1, 1), _row(D1, 2), _row(D2, 3)])
        index_pending_logs(path)
        _seal_all(path)
        _write(path, [_row(D2, 4)])  # D2 is open again with a late log

        snap = storage_metrics.sample_storage_metrics(path)

        main = day_store.file_bytes(path)
        days = day_store.file_bytes(day_files.day_path(path, D1)) + day_store.file_bytes(day_files.day_path(path, D2))
        assert snap["main_db_size_bytes"] == main
        assert snap["day_files_size_bytes"] == days
        assert snap["db_size_bytes"] == main + days
        assert snap["total_logs_count"] == 6
        assert [(f["day"], f["state"], f["logs"]) for f in snap["log_files"]] == [
            (D2, "open", 2), (D1, "sealed", 2), ("legacy", "legacy", 2),
        ]
        assert snap["log_files"][-1]["newest_log_at"] == "2026-03-21T10:00:00+00:00"
        assert snap["log_files"][-1]["size_bytes"] is None
        day_store.forget_connections()


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    lifecycle.reset()
    day_store.forget_id_blocks()
    storage_metrics.clear_storage_snapshot_cache()
    path = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    _upgraded_with_legacy(path, [("2026-03-21T10:00:00+00:00", "old")])
    get_or_create_master_key(tmp_path / ".secret_key")
    yield path
    reset_crypto_cache()
    day_store.forget_connections()
    storage_metrics.clear_storage_snapshot_cache()


@pytest_asyncio.fixture
async def client(api_env):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test", headers=CSRF) as c:
        c.cookies.set(SESSION_COOKIE_NAME, create_session_token(user_id=1))
        yield c


@pytest.mark.asyncio
async def test_the_storage_overview_lists_each_file_with_when_it_expires(api_env, client):
    _write(api_env, [_row(D1, 1)])
    with open_db(api_env) as conn:
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('retention_days', '7', datetime('now'))")
    body = (await client.get("/api/system/storage")).json()
    assert body["retention_days"] == 7
    assert body["db_size_bytes"] == body["main_db_size_bytes"] + body["day_files_size_bytes"]
    files = {f["day"]: f for f in body["log_files"]}
    assert files[D1]["logs"] == 1 and files[D1]["file_name"] == "logs-2026-03-27-UTC.db"
    # Deleted from the first prune once its whole day is 7 days old: 00:00 UTC, 8 days after its start
    assert files[D1]["expires_at"] == "2026-04-04T00:00:00+00:00"
    # Its newest log is 7 days old at 10:00 on 28 March; the prune after that midnight removes it
    assert files["legacy"]["expires_at"] == "2026-03-29T00:00:00+00:00"
    assert files["legacy"]["logs"] == 1 and files["legacy"]["size_bytes"] is None


def test_the_indexer_seals_finished_days_when_idle_at_most_once_a_minute(db):
    from app.services.fts_indexer import FTSIndexWorker

    _write(db, [_row(D1, 1)])
    worker = FTSIndexWorker(db, batch_size=100)
    try:
        # The pass that leaves the day fully indexed seals it
        assert worker._index_pending_chunk(100) == 1
        assert _catalogue(db)[D1]["state"] == "sealed"
        # A late log opens it again; the next idle pass inside the minute does not look
        _write(db, [_row(D1, 2)])
        worker._index_pending_chunk(100)
        worker._index_pending_chunk(100)
        assert _catalogue(db)[D1]["state"] == "open"
        worker._next_seal_check = 0.0
        worker._index_pending_chunk(100)
        assert _catalogue(db)[D1]["state"] == "sealed" and _catalogue(db)[D1]["row_count"] == 2
    finally:
        worker.close()


class TestReviewFixes:
    def test_compaction_headroom_counts_the_live_data_not_the_file(self, tmp_path):
        day_store.forget_id_blocks()
        path = tmp_path / "logs.db"
        old = (datetime.datetime.now(_UTC) - datetime.timedelta(days=40)).isoformat()
        _upgraded_with_legacy(path, [(old, f"old {i}") for i in range(2000)])
        retention.execute_prune(path, retention_days=14)  # drops the table; the file keeps its size
        footprint = retention.get_db_footprint_bytes(path)
        live = retention.get_db_live_bytes(path)
        assert live < footprint / 4
        free = 2 * live + 10 * 1024**2  # room for the live data twice, not for the file twice
        with mock.patch.object(retention, "_free_bytes", return_value=free):
            assert retention.check_vacuum_headroom(path, safe_margin_bytes=0)[0] == live

    def test_the_snapshot_keeps_its_figures_while_logs_db_cannot_be_written(self, three_days):
        storage_metrics.clear_storage_snapshot_cache()
        with mock.patch.object(day_store, "refresh_open_figures", side_effect=sqlite3.OperationalError("database is locked")):
            snap = storage_metrics.sample_storage_metrics(three_days)
        assert snap["total_logs_count"] == 7
        assert {f["day"] for f in snap["log_files"]} - {"legacy"} == {D1, D2, D3}
        assert snap["day_files_size_bytes"] > 0

    @pytest.mark.parametrize("how", ["delete", "relabel"])
    def test_a_change_to_a_sealed_file_that_stops_part_way_still_rewrites_its_figures(self, db, how):
        _write(db, [_row(D1, h % 24, message=f"disk {h}") for h in range(30)])
        index_pending_logs(db)
        _seal_all(db)

        def stop(*_a):
            raise RuntimeError("stopped")

        with open_db(db) as conn, pytest.raises(RuntimeError):
            if how == "delete":
                log_store.delete(conn, LogFilter(query="disk"), 10, stop)
            else:
                with mock.patch.object(log_store, "_pause", stop):
                    log_store.relabel_host(conn, "10.0.0.1", "storage", batch_size=10)
        with open_db(db) as conn:
            day_conn = day_store.connection(day_files.day_path(db, D1))
            left = day_conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            renamed = day_conn.execute("SELECT COUNT(*) FROM logs JOIN hosts ON hosts.id = host_id WHERE hosts.name = 'storage'").fetchone()[0]
        entry = _catalogue(db)[D1]
        counts = _counts(db, D1)
        assert entry["row_count"] == left == sum(counts.values())
        assert sum(c for k, c in counts.items() if k[1] == "storage") == renamed
        assert (left, renamed) != (30, 0)  # one batch went through before the stop

    def test_a_reader_keeps_its_connections_when_another_thread_forgets_them(self, three_days):
        _seal_all(three_days)
        with open_db(three_days) as conn:
            parts = day_store.parts(conn)
            newest = parts[0].conn  # opened
            # Another thread deletes a file: only the generation moves for this one
            with day_store._generation_lock:
                day_store._generation += 1
            assert parts[2].conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 4  # opens another file
            assert newest.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 1  # still usable
            # Set aside, then closed a few minutes later
            later = day_store.time.monotonic() + day_store.RETIRED_CLOSE_SECONDS + 1
            with mock.patch.object(day_store.time, "monotonic", return_value=later):
                day_store.connection(day_files.day_path(three_days, D2))
            with pytest.raises(sqlite3.ProgrammingError):
                newest.execute("SELECT 1")

    def test_connections_unused_for_a_while_are_closed(self, three_days):
        first = day_store.connection(day_files.day_path(three_days, D1))
        start = day_store.time.monotonic()
        with mock.patch.object(day_store.time, "monotonic", return_value=start + day_store.IDLE_CLOSE_SECONDS + 1):
            day_store.connection(day_files.day_path(three_days, D2))
        first.execute("SELECT 1")  # set aside, not yet closed: a caller may still hold it
        with mock.patch.object(
            day_store.time, "monotonic",
            return_value=start + day_store.IDLE_CLOSE_SECONDS + day_store.RETIRED_CLOSE_SECONDS + 2,
        ):
            day_store.connection(day_files.day_path(three_days, D2))
        with pytest.raises(sqlite3.ProgrammingError):
            first.execute("SELECT 1")
        assert day_store.connection(day_files.day_path(three_days, D1)) is not first

    def test_a_days_first_write_never_adopts_a_file_with_no_record(self, db):
        today = datetime.datetime.now(_UTC).date().isoformat()
        stray = day_files.day_path(db, today)
        stray.write_bytes(b"not ours")
        _write(db, [_row(today, 0)])
        assert [r["message"] for r in stored_logs(db)] == ["disk full"]
        (aside,) = db.parent.glob(f"{stray.name}.unlisted-*")
        assert aside.read_bytes() == b"not ours"
