"""
A syslog sender whose address has no alias is shown by the HOSTNAME it reports, marked as
self-reported, unless a configured host (a syslog host alias, a Docker host or LogShed itself)
already uses that name: then it is shown by its address, so it cannot pose as that host. Stored
rows follow when aliases and Docker hosts change. Logs stored before 1.4.0 have no reported name.
"""

import datetime
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.collectors.syslog import AliasCache, name_syslog_host
from app.core import pipeline as pipeline_mod
from app.core.day_files import KIND_SYSLOG
from app.core.migrations import run_migrations
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import SSELogEvent
from app.main import create_app
from app.services import day_store, log_store
from tests.db_helpers import open_db, store_logs, stored_logs

PRINTER_IP = "172.17.0.4"
NAS_IP = "192.168.10.20"
TS = "2026-11-14T10:00:00+00:00"


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    day_store._reported_written.clear()
    db_file = tmp_path / "reported.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    with open_db(db_file) as conn:
        conn.execute(
            "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, 'truenas', ?)",
            (NAS_IP, datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
    yield db_file
    pipeline_mod._log_queue = None
    day_store._reported_written.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def headers():
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={create_session_token(user_id=1)}",
        "x-requested-with": "XMLHttpRequest",
    }


def _named(env: Path, hostname: str, ip: str = PRINTER_IP) -> dict:
    parsed = {"hostname": hostname}
    name_syslog_host(parsed, ip, AliasCache(env, preload=True))
    return parsed


def _row(message: str, hostname: str, ip: str = PRINTER_IP, env: Path | None = None) -> dict:
    """A syslog row named as the collector names it."""
    named = _named(env, hostname, ip)
    return {
        "timestamp": TS, "source_ip": ip, "source_kind": KIND_SYSLOG, "app_name": "app", "facility": 1,
        "severity": 6, "message": message, "raw": message,
        **{k: v for k, v in named.items() if k != "hostname"},
    }


async def _logs(client: AsyncClient, headers: dict, **params) -> dict[str, dict]:
    resp = await client.get("/api/logs", params=params, headers=headers)
    assert resp.status_code == 200
    return {log["message"]: log for log in resp.json()["logs"]}


async def _facets(client: AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/logs/facets", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def test_a_free_name_is_shown_and_a_taken_one_is_not(env):
    assert _named(env, "printer-hp") == {"hostname": "printer-hp", "reported_host": "printer-hp", "source_alias": "printer-hp"}
    # Another address's alias (any letter case), a Docker host and LogShed's own name are refused
    assert _named(env, "TrueNAS")["source_alias"] == PRINTER_IP
    assert _named(env, "TrueNAS")["reported_host"] == "TrueNAS"
    assert _named(env, "docker")["source_alias"] == PRINTER_IP
    assert _named(env, "logshed")["source_alias"] == PRINTER_IP
    # An address with an alias is named by it, and keeps the reported name hidden
    assert _named(env, "printer-hp", ip=NAS_IP) == {
        "hostname": "printer-hp", "reported_host": "printer-hp", "_reported_hidden": True, "source_alias": "truenas",
    }


@pytest.mark.asyncio
async def test_logs_and_host_lists_say_which_names_are_self_reported(client, headers, env):
    store_logs(env, _row("job done", "printer-hp", env=env), _row("root login", "truenas", env=env))

    logs = await _logs(client, headers)
    assert logs["job done"]["source_alias"] == "printer-hp"
    assert logs["job done"]["reported_host"] == "printer-hp"
    assert logs["job done"]["host_self_reported"] is True
    assert logs["root login"]["source_alias"] == PRINTER_IP
    assert logs["root login"]["reported_host"] == "truenas"
    assert logs["root login"]["host_self_reported"] is False

    # The impostor's logs are not the real host's
    assert list(await _logs(client, headers, source="truenas")) == []
    assert list(await _logs(client, headers, source="printer-hp")) == ["job done"]

    facets = await _facets(client, headers)
    assert facets["self_reported_sources"] == {"printer-hp": [PRINTER_IP]}


@pytest.mark.asyncio
async def test_a_name_another_host_starts_using_is_withdrawn_and_comes_back_when_freed(client, headers, env):
    store_logs(env, _row("job done", "printer-hp", env=env))

    resp = await client.post("/api/aliases", json={"ip": "10.0.0.9", "alias": "Printer-HP"}, headers=headers)
    assert resp.status_code == 200
    assert {r["message"]: r["source_alias"] for r in stored_logs(env)} == {"job done": PRINTER_IP}
    assert (await _facets(client, headers))["self_reported_sources"] == {}

    resp = await client.delete("/api/aliases/10.0.0.9", headers=headers)
    assert resp.status_code == 200
    rows = stored_logs(env)
    assert [(r["source_alias"], r["host_self_reported"]) for r in rows] == [("printer-hp", 1)]


@pytest.mark.asyncio
async def test_making_the_reported_name_the_alias_ends_the_self_reported_mark(client, headers, env):
    store_logs(env, _row("job done", "printer-hp", env=env))

    resp = await client.post("/api/aliases", json={"ip": PRINTER_IP, "alias": "printer-hp"}, headers=headers)
    assert resp.status_code == 200

    log = (await _logs(client, headers))["job done"]
    assert (log["source_alias"], log["reported_host"], log["host_self_reported"]) == ("printer-hp", "printer-hp", False)
    assert (await _facets(client, headers))["self_reported_sources"] == {}


@pytest.mark.asyncio
async def test_deleting_the_alias_shows_the_reported_names_again(client, headers, env):
    store_logs(env, _row("job done", "printer-hp", env=env))
    resp = await client.post("/api/aliases", json={"ip": PRINTER_IP, "alias": "office-printer"}, headers=headers)
    assert resp.status_code == 200
    # Logs that arrive while the alias exists keep the reported name too, hidden
    later = _row("paper low", "printer-hp", env=env)
    assert later["source_alias"] == "office-printer"
    store_logs(env, later)
    logs = await _logs(client, headers)
    assert [(logs[m]["source_alias"], logs[m]["host_self_reported"]) for m in ("job done", "paper low")] == [
        ("office-printer", False)
    ] * 2

    resp = await client.delete(f"/api/aliases/{PRINTER_IP}", headers=headers)
    assert resp.status_code == 200
    logs = await _logs(client, headers)
    # As new logs from the address will be: shown by the reported name, marked as self-reported
    assert [(logs[m]["source_alias"], logs[m]["host_self_reported"]) for m in ("job done", "paper low")] == [
        ("printer-hp", True)
    ] * 2
    assert _named(env, "printer-hp")["source_alias"] == "printer-hp"
    assert (await _facets(client, headers))["self_reported_sources"] == {"printer-hp": [PRINTER_IP]}


@pytest.mark.asyncio
async def test_a_hidden_name_another_host_took_meanwhile_stays_on_the_address(client, headers, env):
    store_logs(env, _row("job done", "printer-hp", env=env))
    assert (await client.post("/api/aliases", json={"ip": PRINTER_IP, "alias": "office"}, headers=headers)).status_code == 200
    assert (await client.post("/api/aliases", json={"ip": "10.0.0.9", "alias": "printer-hp"}, headers=headers)).status_code == 200
    assert (await client.delete(f"/api/aliases/{PRINTER_IP}", headers=headers)).status_code == 200
    log = (await _logs(client, headers))["job done"]
    assert (log["source_alias"], log["reported_host"], log["host_self_reported"]) == (PRINTER_IP, "printer-hp", False)


@pytest.mark.asyncio
async def test_a_new_docker_host_takes_its_name_from_reported_rows(client, headers, env, monkeypatch):
    store_logs(env, _row("job done", "tower", env=env))
    monkeypatch.setattr("app.api.docker_hosts._ensure_url_target_allowed", _allow, raising=False)

    resp = await client.post(
        "/api/docker/hosts",
        json={"name": "Tower", "url": "tcp://10.0.0.7:2375", "source_alias": "tower", "enabled": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [r["source_alias"] for r in stored_logs(env)] == [PRINTER_IP]


async def _allow(*_args, **_kwargs):
    return None


def test_the_live_stream_marks_self_reported_names():
    shown = SSELogEvent.from_entry({"id": 1, "source_alias": "printer-hp", "reported_host": "printer-hp"})
    refused = SSELogEvent.from_entry({"id": 2, "source_alias": PRINTER_IP, "reported_host": "truenas"})
    assert shown["host_self_reported"] is True
    assert refused["host_self_reported"] is False


def test_retention_forgets_names_no_stored_log_carries(env):
    store_logs(env, _row("job done", "printer-hp", env=env))
    with open_db(env, sqlite3.Row) as conn:
        assert [tuple(r) for r in conn.execute("SELECT name, source_ip FROM reported_hosts")] == [("printer-hp", PRINTER_IP)]
        log_store.prune(conn, "2099-01-01T00:00:00+00:00", lambda: None)
        assert conn.execute("SELECT COUNT(*) FROM reported_hosts").fetchone()[0] == 0


def test_an_address_is_never_a_reported_name(env):
    # A filter by the alias truenas also matches its address, so a device must not take it
    assert _named(env, NAS_IP)["source_alias"] == PRINTER_IP
    assert _named(env, "fe80::1")["source_alias"] == PRINTER_IP
    assert _named(env, "10printer")["source_alias"] == "10printer"


@pytest.mark.asyncio
async def test_names_are_matched_whatever_their_letter_case_beyond_ascii(client, headers, env):
    store_logs(env, _row("light on", "Küche", env=env))

    resp = await client.post("/api/aliases", json={"ip": "10.0.0.9", "alias": "KÜCHE"}, headers=headers)
    assert resp.status_code == 200
    assert [r["source_alias"] for r in stored_logs(env)] == [PRINTER_IP]

    resp = await client.delete("/api/aliases/10.0.0.9", headers=headers)
    assert resp.status_code == 200
    assert [r["source_alias"] for r in stored_logs(env)] == ["Küche"]


@pytest.mark.asyncio
async def test_a_docker_host_rename_and_delete_free_its_name(client, headers, env):
    store_logs(env, _row("job done", "tower", env=env), _row("other", "tower-old", env=env))
    with open_db(env) as conn:
        conn.execute(
            "INSERT INTO docker_endpoints (id, name, url, source_alias, enabled, created_at, updated_at)"
            " VALUES (2, 'Tower', 'tcp://10.0.0.7:2375', 'tower', 0, ?, ?)",
            (TS, TS),
        )
    with open_db(env) as conn:
        log_store.withdraw_reported_name(conn, "tower")
    assert {r["message"]: r["source_alias"] for r in stored_logs(env)} == {"job done": PRINTER_IP, "other": "tower-old"}

    # Renamed: tower-old is taken; tower stays reserved for the host's old container logs while
    # logs from before 1.4.0 remain (a former Docker alias), as the collector treats it
    resp = await client.put("/api/docker/hosts/2", json={"source_alias": "tower-old"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert {r["message"]: r["source_alias"] for r in stored_logs(env)} == {"job done": PRINTER_IP, "other": PRINTER_IP}

    # Deleted, then the legacy table goes: tower is free, tower-old was the host's and is reserved no more
    resp = await client.delete("/api/docker/hosts/2", headers=headers)
    assert resp.status_code == 200
    with open_db(env) as conn:
        assert log_store.drop_empty_legacy(conn)
    assert {r["message"]: r["source_alias"] for r in stored_logs(env)} == {"job done": "tower", "other": "tower-old"}


@pytest.mark.asyncio
async def test_continuation_lines_keep_the_reported_name(env):
    from app.collectors.syslog import dispatch_syslog_message

    fed: list[dict] = []

    class Assembler:
        """Every line after the first continues the first one's stream."""

        def get_active_stream_key_for_source(self, source_ip):
            return "stream" if fed else None

        def continues_stream(self, key, text):
            return True

        def get_stream_parent_entry(self, key):
            return fed[0]

        async def feed(self, stream_key, entry):
            fed.append(entry)

    cache = AliasCache(env)
    await dispatch_syslog_message(b"<14>Nov 14 10:00:00 printer-hp app[1]: Traceback (most recent call last):", PRINTER_IP, cache, Assembler())
    # A line with no header: it would read as host "-" and app "unknown" on its own
    await dispatch_syslog_message(b'  File "x.py", line 1, in <module>', PRINTER_IP, cache, Assembler())
    assert [(e["source_alias"], e.get("reported_host")) for e in fed] == [("printer-hp", "printer-hp")] * 2


async def _self_reported(client: AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/aliases/self-reported", headers=headers)
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_sources_lists_each_address_shown_by_its_own_name_until_it_gets_an_alias(client, headers, env):
    from app.services.source_activity import get_source_activity, us_to_iso

    store_logs(env, _row("job done", "printer-hp", env=env), _row("root login", "truenas", env=env))
    # A second name from the same address, seen later
    with open_db(env) as conn:
        conn.execute("INSERT INTO reported_hosts VALUES ('hp-m404', ?, 9999999999999999)", (PRINTER_IP,))
    get_source_activity().note([{"source_ip": PRINTER_IP, "source_kind": KIND_SYSLOG}], now_us=1_000)

    listed = await _self_reported(client, headers)

    # The name a configured host uses ("truenas") is not self-reported, so it is not listed
    assert listed["truncated"] is False
    assert [(h["ip"], h["names"]) for h in listed["hosts"]] == [(PRINTER_IP, ["hp-m404", "printer-hp"])]
    # The address's own newest log, not the time a name was recorded
    assert listed["hosts"][0]["last_seen"] == us_to_iso(1_000)

    resp = await client.post("/api/aliases", json={"ip": PRINTER_IP, "alias": "printer-hp"}, headers=headers)
    assert resp.status_code == 200
    assert (await _self_reported(client, headers))["hosts"] == []


@pytest.mark.asyncio
async def test_sources_shows_when_the_address_last_sent_after_its_alias_is_deleted(client, headers, env):
    from app.services.source_activity import get_source_activity, us_to_iso

    store_logs(env, _row("job done", "printer-hp", env=env))
    get_source_activity().note([{"source_ip": PRINTER_IP, "source_kind": KIND_SYSLOG}], now_us=1_000)
    assert (await client.post("/api/aliases", json={"ip": PRINTER_IP, "alias": "office"}, headers=headers)).status_code == 200
    # Deleting the alias records the name again as seen now; the printer last sent at 1_000
    assert (await client.delete(f"/api/aliases/{PRINTER_IP}", headers=headers)).status_code == 200
    [host] = (await _self_reported(client, headers))["hosts"]
    assert (host["names"], host["last_seen"]) == (["printer-hp"], us_to_iso(1_000))


@pytest.mark.asyncio
async def test_sources_falls_back_to_when_a_name_was_recorded(client, headers, env):
    from app.services.source_activity import us_to_iso

    with open_db(env) as conn:
        conn.execute("INSERT INTO reported_hosts VALUES ('printer-hp', ?, 5000)", (PRINTER_IP,))
    [host] = (await _self_reported(client, headers))["hosts"]
    assert host["last_seen"] == us_to_iso(5000)


@pytest.mark.asyncio
async def test_sources_lists_at_most_the_limit_newest_first(client, headers, env, monkeypatch):
    from app.api import aliases as aliases_api

    monkeypatch.setattr(aliases_api, "SELF_REPORTED_LIMIT", 2)
    with open_db(env) as conn:
        conn.executemany(
            "INSERT INTO reported_hosts VALUES (?, ?, ?)",
            [(f"dev-{i}", f"10.0.0.{i}", 1_000 + i) for i in range(3)],
        )
    listed = await _self_reported(client, headers)
    assert [h["ip"] for h in listed["hosts"]] == ["10.0.0.2", "10.0.0.1"]
    assert (listed["truncated"], listed["limit"]) == (True, 2)


@pytest.mark.asyncio
async def test_sources_leaves_out_a_name_a_configured_host_now_uses(client, headers, env):
    store_logs(env, _row("job done", "printer-hp", env=env))
    # Another address takes the name as its alias: the Console shows the printer by its address
    resp = await client.post("/api/aliases", json={"ip": "10.0.0.9", "alias": "Printer-HP"}, headers=headers)
    assert resp.status_code == 200
    assert (await _self_reported(client, headers))["hosts"] == []


@pytest.mark.asyncio
async def test_sources_lists_at_most_ten_names_per_address_newest_first(client, headers, env):
    with open_db(env) as conn:
        conn.executemany(
            "INSERT INTO reported_hosts VALUES (?, ?, ?)",
            [(f"name-{i:02d}", PRINTER_IP, 1_000 + i) for i in range(25)],
        )
    [host] = (await _self_reported(client, headers))["hosts"]
    assert host["names"] == [f"name-{i:02d}" for i in range(24, 14, -1)]
