"""
The Storage page's top sources: logs per host and per app in the 24 whole hours up to now, from
the catalogue's hourly counts for sealed days, the open day files' indexes and the legacy table,
worked out in the background and served from memory.
"""

import datetime
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
from app.services import day_store, log_store, top_sources
from app.services.fts_indexer import index_pending_logs
from tests.db_helpers import open_db

_UTC = datetime.timezone.utc
D1, D2 = "2026-03-27", "2026-03-28"


def _us(day: str, hour: int = 0, minute: int = 0) -> int:
    return day_files.day_start_us(day) + (hour * 60 + minute) * 60_000_000


def _row(day: str, hour: int, host: str = "nas", app: str = "kernel", ip: str = "10.0.0.1") -> dict:
    at = datetime.datetime.fromisoformat(day).replace(hour=hour, tzinfo=_UTC).isoformat()
    return {
        "timestamp": at, "received_at": at, "source_ip": ip, "source_alias": host,
        "app_name": app, "facility": 1, "severity": 6, "message": "m", "raw": "m",
    }


def _write(path: Path, rows: list[dict]) -> None:
    with open_db(path) as conn:
        day_store.write_rows(conn, [dict(r) for r in rows])


@pytest.fixture
def db(tmp_path: Path) -> Path:
    day_store.forget_id_blocks()
    day_store.forget_connections()
    path = tmp_path / "logs.db"
    run_migrations(path)
    yield path
    day_store.forget_connections()


def _counts(path: Path, since_us: int) -> log_store.SourceCounts:
    with open_db(path) as conn:
        return log_store.source_counts(conn, since_us)


class TestCounts:
    def test_a_sealed_day_comes_from_the_catalogue_and_an_open_one_from_its_file(self, db):
        _write(db, [
            _row(D1, 3), _row(D1, 10), _row(D1, 22, host="pve"), _row(D1, 23, host="pve", app="cron"),
            _row(D2, 1, host="pve", app="cron"), _row(D2, 2),
        ])
        index_pending_logs(db)
        with open_db(db) as conn:
            assert day_store.seal_finished_days(conn, now_us=_us(D2, 0, 5)) == [D1]

        # From D1 10:00: D1's 03:00 log is before the window
        found = _counts(db, _us(D1, 10))

        assert found.hosts == {"nas": 2, "pve": 3}
        assert found.apps == {"kernel": 3, "cron": 2}
        assert found.total == 5

    def test_a_sealed_day_is_counted_from_the_catalogue_not_its_file(self, db):
        _write(db, [_row(D1, 10), _row(D1, 11, host="pve")])
        index_pending_logs(db)
        with open_db(db) as conn:
            day_store.seal_finished_days(conn, now_us=_us(D2, 0, 5))
            # Only the catalogue says ten: the file holds one log per host
            conn.execute("UPDATE log_file_counts SET count = 10 WHERE day = ? AND host = 'nas'", (D1,))
        assert _counts(db, _us(D1, 0)).hosts == {"nas": 10, "pve": 1}

    def test_logs_without_a_host_name_are_counted_by_address(self, db):
        _write(db, [_row(D1, 10, host="", ip="10.0.0.7"), _row(D1, 11, host="", ip="10.0.0.8"), _row(D2, 1, host="", ip="10.0.0.7")])
        index_pending_logs(db)
        with open_db(db) as conn:
            day_store.seal_finished_days(conn, now_us=_us(D2, 0, 5))
        assert _counts(db, _us(D1, 0)).hosts == {"10.0.0.7": 2, "10.0.0.8": 1}

    def test_logs_kept_in_the_legacy_table_are_counted(self, tmp_path):
        day_store.forget_id_blocks()
        day_store.forget_connections()
        path = tmp_path / "logs.db"
        with mock.patch.object(migrations_mod, "MIGRATIONS", MIGRATIONS[:3]):
            run_migrations(path)
        with open_db(path) as conn:
            conn.executemany(
                "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw) "
                "VALUES (?, ?, '10.0.0.1', ?, ?, 1, 6, 'm', 'm')",
                [
                    ("2026-03-27T09:00:00+00:00", "2026-03-27T09:00:00+00:00", "nas", "kernel"),
                    ("2026-03-27T11:00:00+00:00", "2026-03-27T11:00:00+00:00", "nas", "kernel"),
                    ("2026-03-27T12:00:00+00:00", "2026-03-27T12:00:00+00:00", "", ""),
                ],
            )
        run_migrations(path, backup=False)
        _write(path, [_row(D1, 13, host="pve")])
        try:
            found = _counts(path, _us(D1, 10))
        finally:
            day_store.forget_connections()
        assert found.hosts == {"nas": 1, "10.0.0.1": 1, "pve": 1}
        assert found.apps == {"kernel": 2, "unknown": 1}


class TestWindow:
    def test_the_window_is_the_24_whole_hours_up_to_now(self):
        now = _us(D2, 14, 37) / 1_000_000
        assert top_sources.window_start_us(now) == _us(D1, 15)

    def test_the_top_five_are_kept_most_logs_first(self, db):
        _write(db, [_row(D2, 1, host=f"h{i}", app=f"a{i}") for i in range(7) for _ in range(i + 1)])
        found = top_sources.work_out(db, now=_us(D2, 12) / 1_000_000)
        assert [h for h, _ in found.hosts] == ["h6", "h5", "h4", "h3", "h2"]
        assert found.apps[0] == ("a6", 7)
        assert found.total == sum(range(1, 8))
        assert found.since == "2026-03-27T13:00:00+00:00"


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
    async def test_before_the_first_working_out_the_page_gets_no_figures(self, client, headers, monkeypatch):
        monkeypatch.setattr(top_sources, "_latest", None)
        resp = await client.get("/api/system/storage/top-sources", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"as_of": None, "since": None, "total": 0, "hosts": [], "apps": []}

    @pytest.mark.asyncio
    async def test_the_page_gets_the_latest_working_out(self, client, headers, db, monkeypatch):
        _write(db, [_row(D2, 1), _row(D2, 2, host="pve", app="cron")])
        monkeypatch.setattr(top_sources, "_latest", top_sources.work_out(db, now=_us(D2, 12) / 1_000_000))
        resp = await client.get("/api/system/storage/top-sources", headers=headers)
        body = resp.json()
        assert body["total"] == 2
        assert body["hosts"] == [{"name": "nas", "count": 1}, {"name": "pve", "count": 1}]
        assert body["as_of"] == "2026-03-28T12:00:00+00:00"

    @pytest.mark.asyncio
    async def test_the_worker_works_out_after_start_up_and_again_after_its_interval(self, db, monkeypatch):
        import asyncio

        monkeypatch.setattr(top_sources, "_latest", None)
        calls = []
        monkeypatch.setattr(top_sources, "work_out", lambda path: calls.append(path) or top_sources.TopSources("a", "b", len(calls)))
        worker = top_sources.TopSourcesWorker(db)
        monkeypatch.setattr(worker, "REFRESH_INTERVAL", 0.01)
        monkeypatch.setattr(worker, "STARTUP_DELAY", 0.01)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.1)
        await worker.stop()
        await asyncio.wait_for(task, timeout=1)
        assert len(calls) >= 2
        assert top_sources.get_top_sources().total == len(calls)

    @pytest.mark.asyncio
    async def test_the_worker_stops_during_its_start_up_wait_without_working_out(self, db, monkeypatch):
        import asyncio

        calls = []
        monkeypatch.setattr(top_sources, "work_out", lambda path: calls.append(path))
        worker = top_sources.TopSourcesWorker(db)
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.01)
        await worker.stop()
        await asyncio.wait_for(task, timeout=1)
        assert calls == []
