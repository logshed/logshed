"""
Renaming a host alias (a syslog host alias or a Docker host alias), or deleting a syslog host alias:
drop rules, active maintenance sessions and saved views that name the host by its old alias follow
the new name (on a delete, the IP), so they keep matching the host's new logs. Wildcard patterns, ended sessions and names still used by another
host are left alone.
"""

import datetime
import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services import drop_filter as drop_filter_mod
from app.services import maintenance_service
from app.services.drop_filter import get_drop_filter

IP = "192.168.1.20"
OTHER_IP = "192.168.1.21"


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    reset_crypto_cache()
    db_file = tmp_path / "alias_rename.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    # A drop filter and maintenance cache of this database only
    monkeypatch.setattr(drop_filter_mod, "_drop_filter", None)
    maintenance_service.update_cached_maintenance_sessions([])
    yield db_file
    maintenance_service.update_cached_maintenance_sessions([])
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()


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


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _add_alias(db_file: Path, ip: str, alias: str) -> None:
    with get_connection(db_file) as conn:
        conn.execute("INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)", (ip, alias, _now()))
        conn.commit()


def _add_drop_rule(db_file: Path, source_pattern: str) -> int:
    with get_connection(db_file) as conn:
        cur = conn.execute(
            "INSERT INTO drop_rules (name, source_pattern, message_pattern, created_at) VALUES ('r', ?, 'noise', ?)",
            (source_pattern, _now()),
        )
        conn.commit()
        return cur.lastrowid


def _pattern(db_file: Path, rule_id: int) -> str:
    with get_connection(db_file) as conn:
        return conn.execute("SELECT source_pattern FROM drop_rules WHERE id = ?", (rule_id,)).fetchone()[0]


def _add_docker_host(db_file: Path, endpoint_id: int, alias: str) -> None:
    with get_connection(db_file) as conn:
        conn.execute(
            """
            INSERT INTO docker_endpoints (id, name, url, source_alias, enabled, tls_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, 0, ?, ?)
            """,
            (endpoint_id, f"Node {endpoint_id}", f"tcp://10.0.0.{endpoint_id}:2375", alias, _now(), _now()),
        )
        conn.commit()


def _add_session(db_file: Path, session_id: str, target_host: str, active: bool) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    with get_connection(db_file) as conn:
        conn.execute(
            """
            INSERT INTO maintenance_sessions
                (session_id, initiated_by, log_handling, target_host, created_at, expires_at, is_active)
            VALUES (?, 'test', 'drop_errors', ?, ?, ?, ?)
            """,
            (session_id, target_host, now.isoformat(), (now + datetime.timedelta(hours=1)).isoformat(), int(active)),
        )
        conn.commit()


def _session_target(db_file: Path, session_id: str) -> str:
    with get_connection(db_file) as conn:
        return conn.execute(
            "SELECT target_host FROM maintenance_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()[0]


def _add_view(db_file: Path, name: str, params) -> int:
    raw = params if isinstance(params, str) else json.dumps(params)
    with get_connection(db_file) as conn:
        cur = conn.execute(
            "INSERT INTO saved_views (name, query_params, is_pinned, created_at) VALUES (?, ?, 0, ?)",
            (name, raw, _now()),
        )
        conn.commit()
        return cur.lastrowid


def _view_params(db_file: Path, view_id: int) -> str:
    with get_connection(db_file) as conn:
        return conn.execute("SELECT query_params FROM saved_views WHERE id = ?", (view_id,)).fetchone()[0]


async def _rename_syslog(client, headers, ip: str, alias: str) -> None:
    resp = await client.post("/api/aliases", json={"ip": ip, "alias": alias}, headers=headers)
    assert resp.status_code == 200, resp.text


async def _rename_docker(client, headers, endpoint_id: int, alias: str) -> None:
    resp = await client.put(f"/api/docker/hosts/{endpoint_id}", json={"source_alias": alias}, headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", ["nas", "NAS", "  nas "])
async def test_syslog_rename_moves_a_drop_rule_and_the_filter_drops_the_new_name(client, headers, env, pattern):
    _add_alias(env, IP, "nas")
    rule_id = _add_drop_rule(env, pattern)
    get_drop_filter().reload_rules()
    assert get_drop_filter().should_drop("nas", IP, "app", "noise here") == rule_id

    await _rename_syslog(client, headers, IP, "storage")

    assert _pattern(env, rule_id) == "storage"
    # Applied to the next log at once, not after a cache timeout
    assert get_drop_filter().should_drop("storage", IP, "app", "noise here") == rule_id


@pytest.mark.asyncio
async def test_rule_stays_when_another_host_still_has_the_old_alias(client, headers, env):
    _add_alias(env, IP, "nas")
    _add_alias(env, OTHER_IP, "NAS")
    rule_id = _add_drop_rule(env, "nas")

    await _rename_syslog(client, headers, IP, "storage")

    assert _pattern(env, rule_id) == "nas"


@pytest.mark.asyncio
async def test_rule_stays_when_a_docker_host_still_has_the_old_alias(client, headers, env):
    _add_docker_host(env, 2, "shared")
    _add_docker_host(env, 3, "shared")
    rule_id = _add_drop_rule(env, "shared")

    await _rename_docker(client, headers, 2, "node-two")

    assert _pattern(env, rule_id) == "shared"


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", ["nas*", "na?", "*"])
async def test_wildcard_patterns_are_left_alone(client, headers, env, pattern):
    _add_alias(env, IP, "nas")
    rule_id = _add_drop_rule(env, pattern)

    await _rename_syslog(client, headers, IP, "storage")

    assert _pattern(env, rule_id) == pattern


@pytest.mark.asyncio
async def test_first_alias_for_a_host_rewrites_nothing(client, headers, env):
    # Only alias-to-alias renames: a rule written against the host's syslog HOSTNAME stays as it is
    rule_id = _add_drop_rule(env, "nas-hostname")

    await _rename_syslog(client, headers, IP, "storage")

    assert _pattern(env, rule_id) == "nas-hostname"


@pytest.mark.asyncio
async def test_saving_the_same_alias_again_rewrites_nothing(client, headers, env):
    _add_alias(env, IP, "nas")
    rule_id = _add_drop_rule(env, "NAS")

    await _rename_syslog(client, headers, IP, "nas")

    assert _pattern(env, rule_id) == "NAS"


@pytest.mark.asyncio
async def test_docker_host_rename_moves_a_drop_rule(client, headers, env):
    _add_docker_host(env, 2, "node-old")
    rule_id = _add_drop_rule(env, "node-old")
    other_id = _add_drop_rule(env, "something-else")

    await _rename_docker(client, headers, 2, "node-new")

    assert _pattern(env, rule_id) == "node-new"
    assert _pattern(env, other_id) == "something-else"
    assert get_drop_filter().should_drop("node-new", "10.0.0.2", "redis", "noise") == rule_id


@pytest.mark.asyncio
async def test_active_maintenance_session_follows_and_an_ended_one_does_not(client, headers, env):
    _add_alias(env, IP, "nas")
    _add_session(env, "running", "nas", active=True)
    _add_session(env, "ended", "nas", active=False)
    _add_session(env, "wild", "nas*", active=True)
    with get_connection(env) as conn:
        conn.row_factory = sqlite3.Row
        maintenance_service.refresh_maintenance_cache(conn)

    await _rename_syslog(client, headers, IP, "storage")

    assert _session_target(env, "running") == "storage"
    assert _session_target(env, "ended") == "nas"
    assert _session_target(env, "wild") == "nas*"
    # The in-memory cache follows too, so the next error log from the host is dropped
    cached = {s["session_id"]: s["target_host"] for s in maintenance_service.get_cached_maintenance_sessions()}
    assert cached["running"] == "storage"
    assert maintenance_service.should_drop_maintenance_error("storage", IP, "app", 3) in {"running", "wild"}


@pytest.mark.asyncio
async def test_docker_rename_moves_an_active_maintenance_session(client, headers, env):
    _add_docker_host(env, 2, "node-old")
    _add_session(env, "running", "node-old", active=True)

    await _rename_docker(client, headers, 2, "node-new")

    assert _session_target(env, "running") == "node-new"


@pytest.mark.asyncio
async def test_saved_view_sources_follow(client, headers, env):
    _add_alias(env, IP, "nas")
    both = _add_view(env, "both", {"sources": ["nas", "router"], "source": "nas,router", "severity_max": 3})
    single = _add_view(env, "single", {"sources": ["NAS"], "source": "NAS"})
    dup = _add_view(env, "dup", {"sources": ["nas", "storage"], "source": "nas,storage"})
    other = _add_view(env, "other", {"sources": ["router"], "source": "router"})
    other_raw = _view_params(env, other)
    broken = _add_view(env, "broken", "{not json")

    await _rename_syslog(client, headers, IP, "storage")

    assert json.loads(_view_params(env, both)) == {
        "sources": ["storage", "router"], "source": "storage,router", "severity_max": 3,
    }
    assert json.loads(_view_params(env, single)) == {"sources": ["storage"], "source": "storage"}
    assert json.loads(_view_params(env, dup)) == {"sources": ["storage"], "source": "storage"}
    assert _view_params(env, other) == other_raw
    assert _view_params(env, broken) == "{not json"


@pytest.mark.asyncio
async def test_rename_logs_one_line_with_counts(client, headers, env, caplog):
    _add_alias(env, IP, "nas")
    _add_drop_rule(env, "nas")
    _add_drop_rule(env, "NAS")
    _add_session(env, "running", "nas", active=True)
    _add_view(env, "v", {"sources": ["nas"]})

    with caplog.at_level("INFO", logger="app.services.alias_rename"):
        await _rename_syslog(client, headers, IP, "storage")

    lines = [r.getMessage() for r in caplog.records if r.name == "app.services.alias_rename"]
    assert lines == ["Host alias changed: 2 drop rules, 1 maintenance session and 1 saved view now use the new name"]


async def _delete_alias(client, headers, ip: str) -> None:
    resp = await client.delete(f"/api/aliases/{ip}", headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_deleting_an_alias_moves_rules_sessions_and_views_to_the_ip(client, headers, env):
    # The host's logs go back to its IP, so what named the alias now names the IP
    _add_alias(env, IP, "nas")
    rule_id = _add_drop_rule(env, "NAS")
    wild_id = _add_drop_rule(env, "nas*")
    _add_session(env, "running", "nas", active=True)
    _add_session(env, "ended", "nas", active=False)
    view_id = _add_view(env, "v", {"sources": ["nas", "router"], "source": "nas,router"})

    await _delete_alias(client, headers, IP)

    assert _pattern(env, rule_id) == IP
    assert _pattern(env, wild_id) == "nas*"
    assert _session_target(env, "running") == IP
    assert _session_target(env, "ended") == "nas"
    assert json.loads(_view_params(env, view_id)) == {"sources": [IP, "router"], "source": f"{IP},router"}
    # A log from the host after the delete carries its IP as the alias
    assert get_drop_filter().should_drop(IP, IP, "app", "noise here") == rule_id
    cached = {s["session_id"]: s["target_host"] for s in maintenance_service.get_cached_maintenance_sessions()}
    assert cached["running"] == IP


@pytest.mark.asyncio
async def test_deleting_an_alias_another_host_still_has_changes_nothing(client, headers, env):
    _add_alias(env, IP, "nas")
    _add_alias(env, OTHER_IP, "nas")
    rule_id = _add_drop_rule(env, "nas")

    await _delete_alias(client, headers, IP)

    assert _pattern(env, rule_id) == "nas"
