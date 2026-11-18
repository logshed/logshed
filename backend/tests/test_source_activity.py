"""
When each source last sent a log: ingest notes every stored batch in memory, the times are saved
to source_last_seen and read back at start-up, and Settings -> Sources shows them on alias and
Docker host rows.
"""

import asyncio
import datetime
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.day_files import KIND_DOCKER, KIND_INGEST, KIND_INTERNAL, KIND_SYSLOG
from app.core.migrations import run_migrations
from app.core.pipeline import QueueConsumer, get_queue
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services import source_activity
from app.services.source_activity import SourceActivity, get_source_activity, init_source_activity, us_to_iso
from tests.db_helpers import open_db

NAS_IP = "192.168.10.20"
T0 = 1_763_200_000_000_000  # 2025-11-15 09:46:40 UTC, in microseconds


@pytest.fixture
def db_file(tmp_path: Path, monkeypatch) -> Path:
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    path = tmp_path / "activity.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(path)
    get_or_create_master_key(tmp_path / ".secret_key")
    yield path
    pipeline_mod._log_queue = None


def _saved(db_file: Path) -> dict[tuple[str, int], int]:
    with open_db(db_file) as conn:
        rows = conn.execute("SELECT source_ip, source_kind, last_seen_us FROM source_last_seen").fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


class TestMap:
    def test_a_batch_marks_each_source_as_seen_once(self):
        activity = SourceActivity()
        activity.note(
            [
                {"source_ip": NAS_IP, "source_kind": KIND_SYSLOG},
                {"source_ip": NAS_IP, "source_kind": KIND_SYSLOG},
                {"source_ip": NAS_IP, "source_kind": KIND_INGEST},
                {"source_ip": "docker", "source_kind": KIND_DOCKER},
            ],
            now_us=T0,
        )
        assert activity.last_seen(NAS_IP, KIND_SYSLOG) == T0
        assert activity.last_seen(NAS_IP, KIND_INGEST) == T0
        assert activity.last_seen("docker", KIND_DOCKER) == T0
        assert activity.last_seen(NAS_IP, KIND_DOCKER) is None

    def test_an_address_shows_its_newest_log_of_any_kind(self):
        activity = SourceActivity()
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0)
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_INGEST}], now_us=T0 + 5)
        assert activity.by_address() == {NAS_IP: T0 + 5}
        assert activity.last_seen(NAS_IP) == T0 + 5
        assert activity.last_seen("10.9.9.9") is None

    def test_logshed_own_logs_count_for_its_own_address(self):
        activity = SourceActivity()
        activity.note([{"source_ip": "127.0.0.1", "source_kind": KIND_INTERNAL}], now_us=T0)
        assert activity.by_address() == {"127.0.0.1": T0}

    def test_made_up_addresses_cannot_grow_the_map_without_end(self, monkeypatch):
        monkeypatch.setattr(source_activity, "MAX_SOURCES", 10)
        monkeypatch.setattr(source_activity, "KEEP_AFTER_EVICTION", 6)
        activity = SourceActivity()
        for i in range(11):
            activity.note([{"source_ip": f"10.0.0.{i}", "source_kind": KIND_SYSLOG}], now_us=T0 + i)
        kept = activity.by_address()
        # The newest six stay, the ones heard from longest ago go
        assert set(kept) == {f"10.0.0.{i}" for i in range(5, 11)}


    def test_configured_sources_are_never_forgotten(self, db_file: Path, monkeypatch):
        """A flood of made-up addresses does not push out a quiet alias or Docker host."""
        monkeypatch.setattr(source_activity, "MAX_SOURCES", 10)
        monkeypatch.setattr(source_activity, "KEEP_AFTER_EVICTION", 6)
        with open_db(db_file) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, 'nas', CURRENT_TIMESTAMP)", (NAS_IP,)
            )
            conn.execute(
                "INSERT INTO docker_endpoints (name, url, source_alias, enabled, created_at, updated_at) VALUES"
                " ('Media', 'tcp://10.0.5.20:2375', 'media', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        activity = SourceActivity(db_file)
        activity.load()
        # The configured sources are the quietest
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0 - 100)
        activity.note([{"source_ip": "10.0.5.20", "source_kind": KIND_DOCKER}], now_us=T0 - 99)
        for i in range(11):
            activity.note([{"source_ip": f"10.0.0.{i}", "source_kind": KIND_SYSLOG}], now_us=T0 + i)
        kept = activity.by_address()
        assert kept[NAS_IP] == T0 - 100
        assert kept["10.0.5.20"] == T0 - 99
        # The flood's oldest go: past 10 sources (at 10.0.0.8) the map was cut to 6, the two configured
        # ones and the four newest others, and 10.0.0.9 and 10.0.0.10 came after
        assert set(kept) - {NAS_IP, "10.0.5.20"} == {f"10.0.0.{i}" for i in range(5, 11)}


class TestSaved:
    def test_flush_saves_and_load_reads_back_after_a_restart(self, db_file: Path):
        activity = SourceActivity(db_file)
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0)
        assert activity.flush() == 1
        assert _saved(db_file) == {(NAS_IP, KIND_SYSLOG): T0}
        # Nothing changed since: nothing to write
        assert activity.flush() == 0

        restarted = SourceActivity(db_file)
        assert restarted.load() == 1
        assert restarted.last_seen(NAS_IP, KIND_SYSLOG) == T0

    def test_a_saved_time_never_moves_back(self, db_file: Path):
        with open_db(db_file) as conn:
            conn.execute("INSERT INTO source_last_seen VALUES (?, ?, ?)", (NAS_IP, KIND_SYSLOG, T0 + 100))
        activity = SourceActivity(db_file)
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0)
        activity.flush()
        assert _saved(db_file) == {(NAS_IP, KIND_SYSLOG): T0 + 100}
        # And loading keeps a newer time already in memory
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0 + 200)
        activity.load()
        assert activity.last_seen(NAS_IP, KIND_SYSLOG) == T0 + 200

    def test_forgotten_sources_are_deleted_from_the_table(self, db_file: Path, monkeypatch):
        monkeypatch.setattr(source_activity, "MAX_SOURCES", 2)
        monkeypatch.setattr(source_activity, "KEEP_AFTER_EVICTION", 2)
        activity = SourceActivity(db_file)
        activity.note([{"source_ip": "10.0.0.1", "source_kind": KIND_SYSLOG}], now_us=T0)
        activity.flush()
        activity.note([{"source_ip": "10.0.0.2", "source_kind": KIND_SYSLOG}], now_us=T0 + 1)
        activity.note([{"source_ip": "10.0.0.3", "source_kind": KIND_SYSLOG}], now_us=T0 + 2)
        activity.flush()
        assert sorted(_saved(db_file)) == [("10.0.0.2", KIND_SYSLOG), ("10.0.0.3", KIND_SYSLOG)]

    def test_a_failed_save_is_tried_again_at_the_next_flush(self, db_file: Path):
        activity = SourceActivity(db_file)
        activity.note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0)
        with open_db(db_file) as conn:
            conn.execute("ALTER TABLE source_last_seen RENAME TO moved")
        with pytest.raises(sqlite3.Error):
            activity.flush()
        with open_db(db_file) as conn:
            conn.execute("ALTER TABLE moved RENAME TO source_last_seen")
        assert activity.flush() == 1
        assert _saved(db_file) == {(NAS_IP, KIND_SYSLOG): T0}

    def test_a_database_without_the_table_loads_nothing(self, tmp_path: Path):
        path = tmp_path / "old.db"
        sqlite3.connect(path).close()
        assert SourceActivity(path).load() == 0

    def test_start_up_reads_the_saved_times_into_the_process_map(self, db_file: Path):
        with open_db(db_file) as conn:
            conn.execute("INSERT INTO source_last_seen VALUES (?, ?, ?)", (NAS_IP, KIND_SYSLOG, T0))
        init_source_activity(db_file)
        assert get_source_activity().last_seen(NAS_IP) == T0

    def test_the_upgrade_to_schema_4_adds_the_table(self, db_file: Path):
        with open_db(db_file) as conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(source_last_seen)")]
        assert columns == ["source_ip", "source_kind", "last_seen_us"]


class TestIngest:
    @pytest.mark.asyncio
    async def test_a_stored_batch_marks_its_sources_seen(self, db_file: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        entry = {
            "timestamp": now, "received_at": now, "source_ip": NAS_IP, "source_alias": NAS_IP,
            "source_kind": KIND_SYSLOG, "app_name": "smartd", "facility": 1, "severity": 6,
            "message": "disk ok", "raw": "disk ok",
        }
        q = get_queue()
        q.put_nowait(entry)
        batch = [q.get_nowait()]
        consumer = QueueConsumer(db_file)
        consumer._running = True
        assert await asyncio.wait_for(consumer._flush_batch(batch, q), timeout=5) is True
        await asyncio.to_thread(consumer._close_conn)
        assert get_source_activity().last_seen(NAS_IP, KIND_SYSLOG) is not None


@pytest_asyncio.fixture
async def client(db_file: Path):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def headers():
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={create_session_token(user_id=1)}",
        "x-requested-with": "XMLHttpRequest",
    }


class TestSourcesPage:
    @pytest.mark.asyncio
    async def test_alias_rows_carry_when_their_address_last_sent_a_log(self, client, headers, db_file: Path):
        with open_db(db_file) as conn:
            for ip, alias in ((NAS_IP, "truenas"), ("10.1.1.1", "quiet")):
                conn.execute(
                    "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, '2026-11-01T00:00:00+00:00')",
                    (ip, alias),
                )
        get_source_activity().note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0)

        resp = await client.get("/api/aliases", headers=headers)

        assert resp.status_code == 200
        by_ip = {a["ip"]: a for a in resp.json()}
        assert by_ip[NAS_IP]["last_seen"] == us_to_iso(T0)
        assert by_ip["10.1.1.1"]["last_seen"] is None

    @pytest.mark.asyncio
    async def test_an_alias_for_logshed_itself_shows_when_it_last_logged(self, client, headers, db_file: Path):
        with open_db(db_file) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('127.0.0.1', 'LogShed', '2026-11-01T00:00:00+00:00')"
            )
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # A log LogShed wrote about itself, as InternalLogHandler queues it
        entry = {
            "timestamp": now, "received_at": now, "source_ip": "127.0.0.1", "source_alias": "LogShed",
            "source_kind": KIND_INTERNAL, "app_name": "backup", "facility": 1, "severity": 6,
            "message": "Database backup written", "raw": "Database backup written",
        }
        q = get_queue()
        q.put_nowait(entry)
        consumer = QueueConsumer(db_file)
        consumer._running = True
        assert await asyncio.wait_for(consumer._flush_batch([q.get_nowait()], q), timeout=5) is True
        await asyncio.to_thread(consumer._close_conn)

        resp = await client.get("/api/aliases", headers=headers)

        by_ip = {a["ip"]: a for a in resp.json()}
        assert by_ip["127.0.0.1"]["last_seen"] is not None

    @pytest.mark.asyncio
    async def test_docker_host_rows_carry_their_containers_newest_log(self, client, headers, db_file: Path):
        with open_db(db_file) as conn:
            conn.execute(
                "INSERT INTO docker_endpoints (name, url, source_alias, enabled, tls_enabled, created_at, updated_at)"
                " VALUES ('nas', 'tcp://nas.lan:2375', 'nas-docker', 0, 0, '2026-11-01', '2026-11-01')"
            )
        # A syslog log from the same name is not the Docker host's
        get_source_activity().note([{"source_ip": "nas.lan", "source_kind": KIND_SYSLOG}], now_us=T0 + 9)
        get_source_activity().note([{"source_ip": "nas.lan", "source_kind": KIND_DOCKER}], now_us=T0)

        resp = await client.get("/api/docker/hosts", headers=headers)

        assert resp.status_code == 200
        [nas] = [h for h in resp.json() if h["name"] == "nas"]
        assert nas["last_seen"] == us_to_iso(T0)


class TestAliasRowsAndDocker:
    @pytest.mark.asyncio
    async def test_an_alias_row_ignores_container_logs_from_its_address(self, client, headers, db_file: Path):
        with open_db(db_file) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, 'nas', '2026-11-01T00:00:00+00:00')",
                (NAS_IP,),
            )
        get_source_activity().note([{"source_ip": NAS_IP, "source_kind": KIND_SYSLOG}], now_us=T0)
        # A Docker host at the same address keeps sending; the syslog sender has gone quiet
        get_source_activity().note([{"source_ip": NAS_IP, "source_kind": KIND_DOCKER}], now_us=T0 + 500)

        resp = await client.get("/api/aliases", headers=headers)

        [nas] = resp.json()
        assert nas["last_seen"] == us_to_iso(T0)
