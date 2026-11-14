"""
Each stored log records which collector stored it (syslog, Docker, HTTP ingest or LogShed
itself). In day files that kind, not the host name, tells Docker rows from syslog rows sent by
the same address: in host filters, the live stream, alias relabels and Docker app renames.
Logs stored before 1.4.0 have no kind and keep the rule by Docker host name.
"""

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.collectors.docker_collector import _make_log_entry
from app.collectors.syslog import parse_syslog_message
from app.core import pipeline as pipeline_mod
from app.core.day_files import KIND_DOCKER, KIND_INGEST, KIND_SYSLOG
from app.core.migrations import run_migrations
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import SSELogEvent
from app.core.utils import AliasIndex
from app.main import create_app
from app.services import log_store
from tests.db_helpers import open_db, store_logs, stored_logs

IP = "192.168.1.60"
TS = "2026-11-14T10:00:00+00:00"


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    db_file = tmp_path / "kind.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    yield db_file
    pipeline_mod._log_queue = None


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


def _row(message: str, alias: str, kind: int, app: str = "app", ip: str = IP) -> dict:
    return {
        "timestamp": TS, "source_ip": ip, "source_alias": alias, "source_kind": kind,
        "app_name": app, "facility": 1, "severity": 6, "message": message, "raw": message,
    }


def _legacy_row(db_file: Path, message: str, alias: str) -> None:
    with open_db(db_file) as conn:
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)"
            " VALUES (?, ?, ?, ?, 'app', 1, 6, ?, ?)",
            (TS, TS, IP, alias, message, message),
        )


async def _messages(client: AsyncClient, headers: dict, **params) -> list[str]:
    resp = await client.get("/api/logs", params=params, headers=headers)
    assert resp.status_code == 200
    return sorted(log["message"] for log in resp.json()["logs"])


def test_each_collector_marks_its_logs():
    assert parse_syslog_message(b"<13>Nov 14 10:00:00 nas sshd: hi", IP)["source_kind"] == KIND_SYSLOG
    assert _make_log_entry("web", "abc", "hello")["source_kind"] == KIND_DOCKER


@pytest.mark.asyncio
async def test_a_docker_row_stays_out_of_its_address_filter_whatever_its_host_name(client, headers, env):
    # "tower" is on no Docker host list (for example a host deleted before the list existed)
    store_logs(env, _row("container line", "tower", KIND_DOCKER), _row("syslog line", IP, KIND_SYSLOG))

    assert await _messages(client, headers, source=IP) == ["syslog line"]
    assert await _messages(client, headers, source="tower") == ["container line"]


@pytest.mark.asyncio
async def test_a_syslog_row_named_like_a_docker_host_is_found_by_its_address(client, headers, env):
    # The built-in Docker host is called "docker"; a syslog row may carry that name too
    store_logs(env, _row("syslog line", "docker", KIND_SYSLOG))

    assert await _messages(client, headers, source=IP) == ["syslog line"]


@pytest.mark.asyncio
async def test_logs_say_which_collector_stored_them(client, headers, env):
    store_logs(env, _row("container line", "tower", KIND_DOCKER), _row("api line", IP, KIND_INGEST))
    _legacy_row(env, "old line", IP)

    resp = await client.get("/api/logs", headers=headers)
    kinds = {log["message"]: log["source_kind"] for log in resp.json()["logs"]}
    assert kinds == {"container line": "docker", "api line": "ingest", "old line": None}


@pytest.mark.asyncio
async def test_a_syslog_alias_relabels_only_the_syslog_rows_of_its_address(client, headers, env):
    store_logs(env, _row("container line", "tower", KIND_DOCKER), _row("syslog line", IP, KIND_SYSLOG))

    resp = await client.post("/api/aliases", json={"ip": IP, "alias": "nas"}, headers=headers)
    assert resp.status_code == 200

    names = {r["message"]: r["source_alias"] for r in stored_logs(env)}
    assert names == {"container line": "tower", "syslog line": "nas"}


def test_a_docker_app_rename_leaves_syslog_rows_of_the_same_name(env):
    store_logs(
        env,
        _row("container line", "docker", KIND_DOCKER, app="abc123", ip="docker"),
        _row("syslog line", "docker", KIND_SYSLOG, app="abc123", ip="docker"),
    )
    with open_db(env, sqlite3.Row) as conn:
        log_store.relabel_docker_app(conn, "abc123", "web")

    apps = {r["message"]: r["app_name"] for r in stored_logs(env)}
    assert apps == {"container line": "web", "syslog line": "abc123"}


def test_the_live_stream_names_the_kind_and_classes_rows_by_it():
    event = SSELogEvent.from_entry({"id": 1, "source_alias": "tower", "source_ip": IP, "source_kind": KIND_DOCKER})
    assert event["source_kind"] == "docker"
    assert '"source_kind": "docker"' in event.data or '"source_kind":"docker"' in event.data

    index = AliasIndex()
    assert index.is_docker("tower", IP, "docker")
    assert not index.is_docker("docker", IP, "syslog")
    # No kind (a log stored before 1.4.0): the Docker host name decides, as before
    assert index.is_docker("docker", IP, None)


@pytest.mark.asyncio
async def test_former_docker_aliases_go_with_the_legacy_table(client, headers, env):
    from app.core.utils import load_former_docker_aliases, note_docker_alias_change

    # While logs from before 1.4.0 remain, a deleted Docker host's alias stays reserved for them
    with open_db(env) as conn:
        note_docker_alias_change(conn, "old-nas", None)
    resp = await client.post("/api/aliases", json={"ip": IP, "alias": "old-nas"}, headers=headers)
    assert resp.status_code == 409

    with open_db(env) as conn:
        assert log_store.drop_empty_legacy(conn)
        assert load_former_docker_aliases(conn) == []
        # Day-file rows record their kind, so a later deletion reserves nothing
        note_docker_alias_change(conn, "tower", None)
        assert load_former_docker_aliases(conn) == []
    resp = await client.post("/api/aliases", json={"ip": IP, "alias": "old-nas"}, headers=headers)
    assert resp.status_code == 200
