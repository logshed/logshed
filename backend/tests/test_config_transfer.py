"""
Whole-configuration export and import (/api/config): what a file holds with and without secrets,
moving everything to a second server with another secret key, merging with what a server already
has (the file wins, nothing else is removed), and files that cannot be read.
"""

import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.api_tokens import _last_used_timestamps, api_token_rate_limiter, hash_token
from app.core.config import invalidate_settings_cache
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    decrypt_value,
    get_or_create_master_key,
    hash_password,
    reset_crypto_cache,
)
from app.main import create_app
from app.services import config_transfer

PASSWORD = "correct horse battery"
ADMIN_PASSWORD = "admin password for tests"
TARGET_URL = "discord://123456789/" + "example_target_token"
AI_KEY = "sk-ant-" + "example-export-key"
PEM_CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
PEM_KEY = "-----BEGIN PRIVATE KEY-----\nMIID\n-----END PRIVATE KEY-----"
ALL_SECTIONS = list(config_transfer.SECTIONS)


def _use_server(tmp_path: Path, monkeypatch, name: str) -> Path:
    """Points LogShed at a fresh server (its own database and secret key) in tmp_path/name."""
    root = tmp_path / name
    root.mkdir()
    db_file = root / "logs.db"
    key_file = root / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    reset_crypto_cache()
    run_migrations(db_file)
    get_or_create_master_key(key_file)
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT OR REPLACE INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, '', '')",
        (hash_password(ADMIN_PASSWORD),),
    )
    conn.commit()
    conn.close()
    invalidate_settings_cache()
    return db_file


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    monkeypatch.delenv("MAX_RETENTION_DAYS", raising=False)
    # A cheap key for the tests; files still say which cost they used
    monkeypatch.setattr(config_transfer, "SCRYPT_N", 2**10)
    _use_server(tmp_path, monkeypatch, "a")
    yield
    pipeline_mod._log_queue = None
    reset_crypto_cache()
    invalidate_settings_cache()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as ac:
        yield ac


def _sign_in() -> dict:
    """Session headers for the server in use (each server signs sessions with its own key)."""
    token = create_session_token(user_id=1)
    return {"Cookie": f"{SESSION_COOKIE_NAME}={token}", "x-requested-with": "XMLHttpRequest"}


@pytest.fixture
def auth_headers():
    return _sign_in()


def _rows(db_file: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


async def _seed(client: AsyncClient, h: dict) -> str:
    """Fills server A with one of everything; returns the raw API token."""
    res = await client.post("/api/notifications/channels", json={"name": "Ops", "url": TARGET_URL}, headers=h)
    assert res.status_code == 201, res.text
    channel_id = res.json()["id"]
    res = await client.post("/api/settings", json={
        "ai_enabled": True,
        "ai_provider": "anthropic",
        "ai_providers_config": {"anthropic": {"ai_api_key": AI_KEY, "ai_model": "claude-x", "ai_fallback_models": "claude-y"}},
        "retention_days": 7,
        "internal_log_level": "ERROR",
        "app_url": "https://logs.example.com",
        "daily_digest_enabled": True,
        "daily_digest_channel_id": channel_id,
        "daily_digest_schedule_time": "07:15",
    }, headers=h)
    assert res.status_code == 200, res.text
    res = await client.post("/api/docker/hosts", json={
        "name": "nas", "url": "tcp://192.168.1.60:2376", "source_alias": "nas-docker",
        "tls_enabled": True, "ca_cert": PEM_CERT, "client_cert": PEM_CERT, "client_key": PEM_KEY,
    }, headers=h)
    assert res.status_code == 200, res.text
    res = await client.post("/api/aliases", json={"ip": "192.168.1.1", "alias": "router", "notes": "the gateway"}, headers=h)
    assert res.status_code == 200, res.text
    res = await client.post("/api/alerts/rules", json={
        "name": "Disk errors", "rule_type": "pattern", "match_pattern": "I/O error", "is_regex": False,
        "filter_host": "router", "channel_id": channel_id, "threshold_count": 2,
    }, headers=h)
    assert res.status_code == 201, res.text
    res = await client.post("/api/drop-rules", json={"name": "Health checks", "message_pattern": "GET /health"}, headers=h)
    assert res.status_code == 201, res.text
    res = await client.post("/api/drop-rules", json={"app_pattern": "noisy", "message_pattern": "tick"}, headers=h)
    assert res.status_code == 201, res.text
    res = await client.post("/api/saved-views", json={"name": "Router errors", "query_params": {"source": "router", "severity": "3"}}, headers=h)
    assert res.status_code == 201, res.text
    res = await client.post("/api/alerts/maintenance/schedules", json={"schedules": [{
        "id": "sched_1", "name": "Nightly backup", "recurrence": "daily", "start_time": "02:00",
        "duration_minutes": 30, "log_handling": "drop_errors", "target_host": "nas",
    }]}, headers=h)
    assert res.status_code == 200, res.text
    res = await client.post("/api/tokens", json={"name": "Home Assistant", "scopes": ["maintenance:write", "logs:read"]}, headers=h)
    assert res.status_code == 200, res.text
    return res.json()["raw_token"]


async def _export(client: AsyncClient, h: dict, **body) -> dict:
    res = await client.post("/api/config/export", json=body, headers=h)
    assert res.status_code == 200, res.text
    assert res.headers["content-disposition"].startswith('attachment; filename="logshed-config-')
    return res.json()


class TestExport:
    @pytest.mark.asyncio
    async def test_without_secrets_the_file_holds_no_secret(self, client, auth_headers):
        await _seed(client, auth_headers)
        doc = await _export(client, auth_headers)
        text = json.dumps(doc)
        assert doc["format"] == "logshed-config" and doc["version"] == 1
        assert doc["secrets_included"] is False and "secrets" not in doc
        for secret in (TARGET_URL, AI_KEY, PEM_KEY, "123456789"):
            assert secret not in text
        sections = doc["sections"]
        assert sections["notification_targets"] == [{"name": "Ops", "is_enabled": True}]
        assert sections["settings"]["daily_digest_target"] == "Ops"
        assert sections["settings"]["ai_providers"]["anthropic"] == {"ai_model": "claude-x", "ai_fallback_models": "claude-y"}
        nas = next(d for d in sections["docker_hosts"] if d["name"] == "nas")
        assert nas["has_client_key"] is True and nas["ca_cert"] == PEM_CERT
        assert sections["alert_rules"][0]["target"] == "Ops"
        assert [s["name"] for s in sections["maintenance_schedules"]] == ["Nightly backup"]
        assert "id" not in sections["maintenance_schedules"][0]
        assert "api_tokens" not in sections

    @pytest.mark.asyncio
    async def test_secrets_need_a_password_of_eight_characters(self, client, auth_headers):
        res = await client.post("/api/config/export", json={"include_secrets": True, "password": "short"}, headers=auth_headers)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_with_secrets_they_are_encrypted_with_the_password(self, client, auth_headers):
        await _seed(client, auth_headers)
        doc = await _export(client, auth_headers, include_secrets=True, password=PASSWORD, admin_password=ADMIN_PASSWORD)
        text = json.dumps(doc)
        assert doc["secrets_included"] is True
        assert doc["secrets"]["kdf"] == "scrypt" and doc["secrets"]["n"] == 2**10
        for secret in (TARGET_URL, AI_KEY, PEM_KEY):
            assert secret not in text
        opened = config_transfer.decrypt_secrets(doc["secrets"], PASSWORD)
        assert opened["notification_urls"] == {"Ops": TARGET_URL}
        assert opened["ai_api_keys"] == {"anthropic": AI_KEY}
        assert opened["docker_client_keys"] == {"nas": PEM_KEY}
        assert [t["name"] for t in opened["api_tokens"]] == ["Home Assistant"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("admin_password", [None, "", "not the admin password"])
    async def test_secrets_need_the_admin_password(self, client, auth_headers, admin_password):
        body = {"include_secrets": True, "password": PASSWORD, "admin_password": admin_password}
        res = await client.post("/api/config/export", json=body, headers=auth_headers)
        assert res.status_code == 400
        assert res.json()["detail"] == "The admin password is incorrect."

    @pytest.mark.asyncio
    async def test_needs_sign_in(self, client):
        res = await client.post("/api/config/export", json={}, headers={"x-requested-with": "XMLHttpRequest"})
        assert res.status_code == 401


class TestMoveToAnotherServer:
    @pytest.mark.asyncio
    async def test_everything_arrives_with_its_secrets(self, client, auth_headers, tmp_path, monkeypatch):
        raw_token = await _seed(client, auth_headers)
        doc = await _export(client, auth_headers, include_secrets=True, password=PASSWORD, admin_password=ADMIN_PASSWORD)

        db_b = _use_server(tmp_path, monkeypatch, "b")
        auth_headers = _sign_in()
        res = await client.post("/api/config/import/preview", json={"file": doc, "password": PASSWORD}, headers=auth_headers)
        assert res.status_code == 200, res.text
        preview = res.json()
        assert preview["has_secrets"] is True and preview["secrets_read"] is True
        counts = {s["name"]: (s["add"], s["replace"], s["skip"]) for s in preview["sections"]}
        assert counts["notification_targets"] == (1, 0, 0)
        assert counts["docker_hosts"] == (1, 0, 0)  # nas added; the local host is the same on both
        assert counts["drop_rules"] == (2, 0, 0)
        assert counts["api_tokens"] == (1, 0, 0)
        # The preview changes nothing
        assert _rows(db_b, "SELECT COUNT(*) FROM alert_rules")[0][0] == 0

        res = await client.post("/api/config/import", json={"file": doc, "password": PASSWORD, "sections": ALL_SECTIONS}, headers=auth_headers)
        assert res.status_code == 200, res.text
        results = {s["name"]: s for s in res.json()["sections"]}
        assert all(not s["failed"] for s in results.values()), results

        channel = _rows(db_b, "SELECT id, url FROM notification_channels WHERE name = 'Ops'")[0]
        assert decrypt_value(channel["url"]) == TARGET_URL
        rule = _rows(db_b, "SELECT * FROM alert_rules")[0]
        assert (rule["name"], rule["channel_id"], rule["filter_host"], rule["is_enabled"]) == ("Disk errors", channel["id"], "router", 1)
        nas = _rows(db_b, "SELECT * FROM docker_endpoints WHERE name = 'nas'")[0]
        assert decrypt_value(nas["client_key"]) == PEM_KEY and nas["enabled"] == 1 and nas["source_alias"] == "nas-docker"
        assert [tuple(r) for r in _rows(db_b, "SELECT ip, alias, notes FROM host_aliases")] == [("192.168.1.1", "router", "the gateway")]
        assert sorted(r["message_pattern"] for r in _rows(db_b, "SELECT * FROM drop_rules")) == ["GET /health", "tick"]
        assert json.loads(_rows(db_b, "SELECT query_params FROM saved_views")[0][0]) == {"source": "router", "severity": "3"}
        token = _rows(db_b, "SELECT * FROM api_tokens")[0]
        assert token["token_hash"] == hash_token(raw_token)
        assert json.loads(token["scopes"]) == ["maintenance:write", "logs:read"]

        settings = (await client.get("/api/settings", headers=auth_headers)).json()
        assert settings["ai_provider"] == "anthropic" and settings["has_ai_api_key"] is True
        assert settings["ai_model"] == "claude-x" and settings["ai_fallback_models"] == "claude-y"
        assert (settings["retention_days"], settings["internal_log_level"], settings["app_url"]) == (7, "ERROR", "https://logs.example.com")
        assert settings["daily_digest_channel_id"] == channel["id"]
        assert settings["daily_digest_schedule_time"] == "07:15"
        stored = {r[0]: r[1] for r in _rows(db_b, "SELECT key, value FROM system_settings")}
        assert decrypt_value(stored["ai_api_key_anthropic"]) == AI_KEY
        schedules = json.loads(stored["maintenance_schedules"])
        assert [(s["name"], s["log_handling"], s["target_host"]) for s in schedules] == [("Nightly backup", "drop_errors", "nas")]

        # The moved token signs in on the new server
        res = await client.get("/api/v1/logs?limit=1", headers={"Authorization": f"Bearer {raw_token}"})
        assert res.status_code == 200, res.text

    @pytest.mark.asyncio
    async def test_openrouter_travels_with_its_key_and_model_and_no_base_url(self, client, auth_headers, tmp_path, monkeypatch):
        or_key = "openrouter-test-" + "key-1234"
        res = await client.post("/api/settings", json={
            "ai_enabled": True,
            "ai_provider": "openrouter",
            "ai_providers_config": {"openrouter": {
                "ai_api_key": or_key, "ai_model": "openai/gpt-6-luna", "ai_fallback_models": "google/gemini-3.7-flash",
            }},
        }, headers=auth_headers)
        assert res.status_code == 200, res.text
        doc = await _export(client, auth_headers, include_secrets=True, password=PASSWORD, admin_password=ADMIN_PASSWORD)
        assert doc["sections"]["settings"]["ai_providers"]["openrouter"] == {
            "ai_model": "openai/gpt-6-luna", "ai_fallback_models": "google/gemini-3.7-flash",
        }
        assert or_key not in json.dumps(doc)

        db_b = _use_server(tmp_path, monkeypatch, "b")
        auth_headers = _sign_in()
        res = await client.post(
            "/api/config/import", json={"file": doc, "password": PASSWORD, "sections": ["settings"]}, headers=auth_headers
        )
        assert res.status_code == 200, res.text
        settings = (await client.get("/api/settings", headers=auth_headers)).json()
        assert settings["ai_provider"] == "openrouter" and settings["has_ai_api_key"] is True
        assert settings["ai_model"] == "openai/gpt-6-luna"
        stored = {r[0]: r[1] for r in _rows(db_b, "SELECT key, value FROM system_settings")}
        assert decrypt_value(stored["ai_api_key_openrouter"]) == or_key
        assert not stored.get("ai_base_url_openrouter")

    @pytest.mark.asyncio
    async def test_a_wrong_password_is_refused(self, client, auth_headers, tmp_path, monkeypatch):
        await _seed(client, auth_headers)
        doc = await _export(client, auth_headers, include_secrets=True, password=PASSWORD, admin_password=ADMIN_PASSWORD)
        _use_server(tmp_path, monkeypatch, "b")
        auth_headers = _sign_in()
        res = await client.post("/api/config/import/preview", json={"file": doc, "password": "not the password"}, headers=auth_headers)
        assert res.status_code == 400
        assert "password" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_without_the_secrets_a_target_is_skipped_and_a_tls_host_added_off(self, client, auth_headers, tmp_path, monkeypatch):
        await _seed(client, auth_headers)
        doc = await _export(client, auth_headers, include_secrets=True, password=PASSWORD, admin_password=ADMIN_PASSWORD)
        db_b = _use_server(tmp_path, monkeypatch, "b")
        auth_headers = _sign_in()
        # No password: the file is read without its secrets
        res = await client.post("/api/config/import/preview", json={"file": doc}, headers=auth_headers)
        preview = {s["name"]: s for s in res.json()["sections"]}
        assert res.json()["secrets_read"] is False
        assert preview["notification_targets"]["skip"] == 1
        assert "api_tokens" not in preview
        assert "TLS client key" in next(i["note"] for i in preview["docker_hosts"]["items"] if i["label"] == "nas")
        assert "will not be on this server" in preview["alert_rules"]["items"][0]["note"]

        res = await client.post("/api/config/import", json={"file": doc, "sections": ALL_SECTIONS}, headers=auth_headers)
        assert res.status_code == 200, res.text
        assert _rows(db_b, "SELECT COUNT(*) FROM notification_channels")[0][0] == 0
        nas = _rows(db_b, "SELECT enabled, client_key FROM docker_endpoints WHERE name = 'nas'")[0]
        assert (nas["enabled"], nas["client_key"]) == (0, None)
        # The rule's target did not come, so it waits turned off instead of going to every target
        rule = _rows(db_b, "SELECT channel_id, is_enabled FROM alert_rules")[0]
        assert (rule["channel_id"], rule["is_enabled"]) == (None, 0)
        stored = {r[0]: r[1] for r in _rows(db_b, "SELECT key, value FROM system_settings")}
        assert not stored.get("ai_api_key_anthropic")
        assert stored["daily_digest_channel_id"] == ""


class TestMerge:
    @pytest.mark.asyncio
    async def test_the_file_wins_and_nothing_else_is_removed(self, client, auth_headers, tmp_path, monkeypatch):
        await _seed(client, auth_headers)
        doc = await _export(client, auth_headers)

        db_b = _use_server(tmp_path, monkeypatch, "b")
        h = auth_headers = _sign_in()
        await client.post("/api/alerts/rules", json={"name": "disk ERRORS", "match_pattern": "x", "threshold_count": 50}, headers=h)
        await client.post("/api/alerts/rules", json={"name": "Only here", "match_pattern": "y"}, headers=h)
        await client.post("/api/aliases", json={"ip": "192.168.1.1", "alias": "old-router"}, headers=h)
        await client.post("/api/saved-views", json={"name": "Mine", "query_params": {"q": "a"}}, headers=h)

        res = await client.post("/api/config/import/preview", json={"file": doc}, headers=h)
        preview = {s["name"]: s for s in res.json()["sections"]}
        assert (preview["alert_rules"]["add"], preview["alert_rules"]["replace"]) == (0, 1)
        assert (preview["host_aliases"]["add"], preview["host_aliases"]["replace"]) == (0, 1)

        res = await client.post("/api/config/import", json={"file": doc, "sections": ["alert_rules", "host_aliases"]}, headers=h)
        results = {s["name"]: s for s in res.json()["sections"]}
        assert set(results) == {"alert_rules", "host_aliases"}
        assert results["alert_rules"]["replaced"] == 1 and results["alert_rules"]["added"] == 0

        rules = {r["name"]: r for r in _rows(db_b, "SELECT * FROM alert_rules")}
        assert set(rules) == {"Disk errors", "Only here"}
        assert (rules["Disk errors"]["threshold_count"], rules["Disk errors"]["match_pattern"]) == (2, "I/O error")
        assert [tuple(r) for r in _rows(db_b, "SELECT ip, alias FROM host_aliases")] == [("192.168.1.1", "router")]
        # Sections not ticked are not touched
        assert [r[0] for r in _rows(db_b, "SELECT name FROM saved_views")] == ["Mine"]
        assert _rows(db_b, "SELECT COUNT(*) FROM drop_rules")[0][0] == 0

    @pytest.mark.asyncio
    async def test_importing_a_server_into_itself_changes_nothing(self, client, auth_headers, tmp_path):
        await _seed(client, auth_headers)
        doc = await _export(client, auth_headers, include_secrets=True, password=PASSWORD, admin_password=ADMIN_PASSWORD)
        res = await client.post("/api/config/import/preview", json={"file": doc, "password": PASSWORD}, headers=auth_headers)
        sections = res.json()["sections"]
        # Every match is already the same: nothing to add or replace, secrets included
        assert all(s["add"] == 0 and s["replace"] == 0 for s in sections), sections
        assert next(s for s in sections if s["name"] == "alert_rules")["unchanged"] == 1
        assert next(s for s in sections if s["name"] == "settings")["items"] == []

        res = await client.post("/api/config/import", json={"file": doc, "password": PASSWORD, "sections": ALL_SECTIONS}, headers=auth_headers)
        results = res.json()["sections"]
        assert all(not s["failed"] and s["added"] == 0 and s["replaced"] == 0 for s in results), results
        assert next(s for s in results if s["name"] == "drop_rules")["unchanged"] == 2
        again = await _export(client, auth_headers)
        plain = await _export(client, auth_headers)
        assert again["sections"] == plain["sections"]
        assert again["sections"]["alert_rules"] == doc["sections"]["alert_rules"]
        assert again["sections"]["drop_rules"] == doc["sections"]["drop_rules"]


class TestFilesRefused:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("document, detail", [
        ({"alert_rules": []}, "not a LogShed configuration"),
        ({"format": "logshed-config", "version": 99, "sections": {}}, "newer version"),
        ({"format": "logshed-config", "version": 1, "sections": {"alert_rules": {}}}, "not a list"),
    ])
    async def test_unreadable_files(self, client, auth_headers, document, detail):
        res = await client.post("/api/config/import/preview", json={"file": document}, headers=auth_headers)
        assert res.status_code == 422
        assert detail in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_an_unknown_section_name_is_refused(self, client, auth_headers):
        doc = {"format": "logshed-config", "version": 1, "sections": {}}
        res = await client.post("/api/config/import", json={"file": doc, "sections": ["logs"]}, headers=auth_headers)
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_a_bad_item_fails_alone(self, client, auth_headers):
        doc = {"format": "logshed-config", "version": 1, "sections": {"drop_rules": [
            {"name": "bad", "message_pattern": "(", "is_regex": True},
            {"name": "good", "message_pattern": "fine"},
        ]}}
        res = await client.post("/api/config/import", json={"file": doc, "sections": ["drop_rules"]}, headers=auth_headers)
        result = res.json()["sections"][0]
        assert result["added"] == 1
        assert [f["label"] for f in result["failed"]] == ["bad"]


def _file(sections: dict, secrets: dict | None = None) -> dict:
    doc = {"format": "logshed-config", "version": 1, "sections": sections}
    if secrets is not None:
        doc["secrets"] = config_transfer.encrypt_secrets(secrets, PASSWORD)
    return doc


def _store_schedules(db_file: Path, schedules: list) -> None:
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('maintenance_schedules', ?, '', 0) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(schedules),),
    )
    conn.commit()
    conn.close()


class TestImportEdges:
    @pytest.mark.asyncio
    async def test_the_local_docker_host_keeps_this_servers_address(self, client, auth_headers, tmp_path):
        db = tmp_path / "a" / "logs.db"
        before = _rows(db, "SELECT url FROM docker_endpoints WHERE id = 1")[0][0]
        doc = _file({"docker_hosts": [{"name": "Primary Docker", "local": True, "url": "tcp://192.168.1.70:2375",
                                       "source_alias": "docker", "enabled": True, "exclude_containers": "noisy"}]})
        res = await client.post("/api/config/import", json={"file": doc, "sections": ["docker_hosts"]}, headers=auth_headers)
        assert res.json()["sections"][0]["replaced"] == 1, res.text
        row = _rows(db, "SELECT url, exclude_containers FROM docker_endpoints WHERE id = 1")[0]
        assert (row["url"], row["exclude_containers"]) == (before, "noisy")

    @pytest.mark.asyncio
    async def test_a_schedule_stored_before_its_fields_existed_is_not_the_same(self, client, auth_headers, tmp_path):
        old = {"id": "s1", "name": "Nightly", "enabled": True, "recurrence": "daily", "start_time": "02:00",
               "duration_minutes": 60, "day_of_week": 0, "day_of_month": 1}
        _store_schedules(tmp_path / "a" / "logs.db", [old])
        doc = _file({"maintenance_schedules": [{**{k: v for k, v in old.items() if k != "id"},
                                                "log_handling": "drop_errors", "target_host": "nas"}]})
        res = await client.post("/api/config/import/preview", json={"file": doc}, headers=auth_headers)
        assert res.json()["sections"][0]["replace"] == 1

    @pytest.mark.asyncio
    async def test_one_schedule_save_refused_is_reported_and_the_rest_goes_on(self, client, auth_headers, tmp_path):
        db = tmp_path / "a" / "logs.db"
        # A row saved before start times were checked
        _store_schedules(db, [{"id": "s1", "name": "Old", "recurrence": "daily", "start_time": "25:00", "duration_minutes": 60}])
        doc = _file({
            "maintenance_schedules": [{"name": "New", "recurrence": "daily", "start_time": "03:00", "duration_minutes": 30}],
            "saved_views": [{"name": "After", "query_params": {"q": "x"}}],
        })
        res = await client.post("/api/config/import", json={"file": doc, "sections": ["maintenance_schedules", "saved_views"]}, headers=auth_headers)
        assert res.status_code == 200, res.text
        results = {s["name"]: s for s in res.json()["sections"]}
        assert results["maintenance_schedules"]["added"] == 0
        assert results["maintenance_schedules"]["failed"][0]["error"].startswith("Nothing saved")
        assert results["saved_views"]["added"] == 1

    @pytest.mark.asyncio
    async def test_the_same_token_twice_fails_once(self, client, auth_headers):
        token = {"name": "HA", "token_hash": "a" * 64, "token_prefix": "ls_live_a...", "scopes": ["logs:read"], "expires_at": None}
        doc = _file({}, {"api_tokens": [token, {**token, "name": "HA again"}]})
        res = await client.post("/api/config/import", json={"file": doc, "password": PASSWORD, "sections": ["api_tokens"]}, headers=auth_headers)
        assert res.status_code == 200, res.text
        result = res.json()["sections"][0]
        assert (result["added"], len(result["failed"])) == (1, 1)

    @pytest.mark.asyncio
    async def test_a_token_whose_expiry_cannot_be_read_is_skipped(self, client, auth_headers):
        doc = _file({}, {"api_tokens": [{"name": "HA", "token_hash": "b" * 64, "scopes": ["logs:read"], "expires_at": "soon"}]})
        res = await client.post("/api/config/import/preview", json={"file": doc, "password": PASSWORD}, headers=auth_headers)
        assert res.json()["sections"][0]["skip"] == 1

    @pytest.mark.asyncio
    async def test_a_rule_whose_target_is_left_out_says_it_was_turned_off(self, client, auth_headers):
        doc = _file({
            "notification_targets": [{"name": "Pager", "is_enabled": True}],
            "alert_rules": [{"name": "Fan", "rule_type": "pattern", "match_pattern": "fan", "target": "Pager", "is_enabled": True}],
        }, {"notification_urls": {"Pager": TARGET_URL}})
        res = await client.post("/api/config/import/preview", json={"file": doc, "password": PASSWORD}, headers=auth_headers)
        rules = next(s for s in res.json()["sections"] if s["name"] == "alert_rules")
        assert "import Notification targets too" in rules["items"][0]["note"]
        res = await client.post("/api/config/import", json={"file": doc, "password": PASSWORD, "sections": ["alert_rules"]}, headers=auth_headers)
        assert any("Turned off" in n for n in res.json()["sections"][0]["notes"])

    @pytest.mark.asyncio
    async def test_settings_about_how_the_server_is_reached_are_named(self, client, auth_headers):
        doc = _file({"settings": {"cookie_secure": True, "internal_log_level": "ERROR"}})
        res = await client.post("/api/config/import/preview", json={"file": doc}, headers=auth_headers)
        notes = res.json()["sections"][0]["notes"]
        assert any(n.startswith("cookie_secure describe") and "plain HTTP" in n for n in notes)

    @pytest.mark.asyncio
    async def test_a_changed_base_url_that_clears_a_key_is_reported(self, client, auth_headers):
        await client.post("/api/settings", json={"ai_providers_config": {"openai": {"ai_api_key": "sk-" + "kept", "ai_base_url": "https://a.example.com/v1"}}}, headers=auth_headers)
        doc = _file({"settings": {"ai_providers": {"openai": {"ai_base_url": "https://b.example.com/v1"}}}})
        res = await client.post("/api/config/import", json={"file": doc, "sections": ["settings"]}, headers=auth_headers)
        assert any("openai API key was cleared" in n for n in res.json()["sections"][0]["notes"])

    @pytest.mark.asyncio
    async def test_key_settings_beyond_the_bounds_are_refused(self, client, auth_headers):
        doc = _file({}, {"api_tokens": []})
        doc["secrets"]["r"] = 16
        res = await client.post("/api/config/import/preview", json={"file": doc, "password": PASSWORD}, headers=auth_headers)
        assert res.status_code == 422
