"""
Integration tests for Zero-Zip JSON Export and Import API endpoints.
Tests export/import of drop rules and alert rules across bundle, single-rule, and raw formats.
"""

from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services.alert_evaluator import init_alert_evaluator
from app.services.drop_filter import init_drop_filter


@pytest.fixture(autouse=True)
def reset_test_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)
    init_drop_filter(db_file)
    init_alert_evaluator(db_file)

    yield

    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers():
    token = create_session_token(user_id=1)
    return {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "x-requested-with": "XMLHttpRequest",
    }


class TestDropRulesExportImport:
    """Test drop rules export and import endpoints."""

    @pytest.mark.asyncio
    async def test_export_all_empty(self, client: AsyncClient, auth_headers: dict):
        res = await client.get("/api/drop-rules/export", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == "1"
        assert "exported_at" in data
        assert data["drop_rules"] == []

    @pytest.mark.asyncio
    async def test_export_all_and_single(self, client: AsyncClient, auth_headers: dict):
        # Create a rule
        create_res = await client.post(
            "/api/drop-rules",
            headers=auth_headers,
            json={
                "source_pattern": "192.168.1.*",
                "app_pattern": "noisy-daemon",
                "message_pattern": "heartbeat*",
                "is_regex": False,
                "is_enabled": True,
                "severity_threshold": 6,
            },
        )
        assert create_res.status_code == 201
        created = create_res.json()
        rule_id = created["id"]

        # Export all via /api and /api/v1
        res_all = await client.get("/api/drop-rules/export", headers=auth_headers)
        assert res_all.status_code == 200
        all_data = res_all.json()
        assert all_data["version"] == "1"
        assert len(all_data["drop_rules"]) == 1
        exported_rule = all_data["drop_rules"][0]
        assert exported_rule["source_pattern"] == "192.168.1.*"
        assert exported_rule["app_pattern"] == "noisy-daemon"
        assert exported_rule["message_pattern"] == "heartbeat*"
        assert exported_rule["is_regex"] is False
        assert exported_rule["is_enabled"] is True
        assert exported_rule["severity_threshold"] == 6
        assert "id" not in exported_rule
        assert "dropped_count" not in exported_rule
        assert "created_at" not in exported_rule

        # Export all via /api/v1
        v1_all = await client.get("/api/v1/drop-rules/export", headers=auth_headers)
        assert v1_all.status_code == 200
        assert len(v1_all.json()["drop_rules"]) == 1

        # Export single rule via /api and /api/v1
        res_single = await client.get(f"/api/drop-rules/{rule_id}/export", headers=auth_headers)
        assert res_single.status_code == 200
        single_data = res_single.json()
        assert single_data["version"] == "1"
        assert single_data["drop_rule"]["app_pattern"] == "noisy-daemon"
        assert "id" not in single_data["drop_rule"]

        res_v1_single = await client.get(f"/api/v1/drop-rules/{rule_id}/export", headers=auth_headers)
        assert res_v1_single.status_code == 200
        assert res_v1_single.json()["drop_rule"]["app_pattern"] == "noisy-daemon"

        # 404 for non-existent rule
        res_404 = await client.get("/api/drop-rules/9999/export", headers=auth_headers)
        assert res_404.status_code == 404

    @pytest.mark.asyncio
    async def test_import_polymorphic_payloads(self, client: AsyncClient, auth_headers: dict):
        # 1. Import bundle format
        bundle_payload = {
            "version": "1",
            "exported_at": "2026-09-25T12:00:00Z",
            "drop_rules": [
                {
                    "source_pattern": "10.0.0.1",
                    "app_pattern": "dhcpd",
                    "message_pattern": "ACK",
                    "is_regex": False,
                    "is_enabled": True,
                    "severity_threshold": 6,
                },
                {
                    "source_pattern": None,
                    "app_pattern": "docker",
                    "message_pattern": "healthcheck",
                    "is_regex": False,
                    "is_enabled": True,
                    "severity_threshold": None,
                },
            ],
        }
        res_bundle = await client.post("/api/drop-rules/import", headers=auth_headers, json=bundle_payload)
        assert res_bundle.status_code == 200
        data = res_bundle.json()
        assert data["imported"] == 2
        assert data["skipped"] == 0
        assert data["errors"] == []

        # 2. Deduplication check: re-importing the same bundle should skip all
        res_dedup = await client.post("/api/drop-rules/import", headers=auth_headers, json=bundle_payload)
        assert res_dedup.status_code == 200
        assert res_dedup.json()["imported"] == 0
        assert res_dedup.json()["skipped"] == 2

        # 3. Import single-rule container format { "drop_rule": { ... } }
        single_payload = {
            "version": "1",
            "drop_rule": {
                "source_pattern": "10.0.0.2",
                "app_pattern": "cron",
                "message_pattern": "*",
                "is_regex": False,
                "is_enabled": True,
            },
        }
        res_single = await client.post("/api/drop-rules/import", headers=auth_headers, json=single_payload)
        assert res_single.status_code == 200
        assert res_single.json()["imported"] == 1
        assert res_single.json()["skipped"] == 0

        # 4. Import raw single rule dictionary
        raw_payload = {
            "source_pattern": "10.0.0.3",
            "app_pattern": "kernel",
            "message_pattern": "TCP: drop",
            "is_regex": False,
        }
        res_raw = await client.post("/api/drop-rules/import", headers=auth_headers, json=raw_payload)
        assert res_raw.status_code == 200
        assert res_raw.json()["imported"] == 1

        # 5. Import via /api/v1/drop-rules/import
        v1_payload = {
            "source_pattern": "10.0.0.4",
            "app_pattern": "nginx",
            "message_pattern": "test",
        }
        res_v1 = await client.post("/api/v1/drop-rules/import", headers=auth_headers, json=v1_payload)
        assert res_v1.status_code == 200
        assert res_v1.json()["imported"] == 1

    @pytest.mark.asyncio
    async def test_import_validation_and_errors(self, client: AsyncClient, auth_headers: dict):
        bad_payload = {
            "drop_rules": [
                # Invalid regex syntax
                {
                    "app_pattern": "app1",
                    "message_pattern": "[invalid(regex",
                    "is_regex": True,
                },
                # Invalid severity threshold
                {
                    "app_pattern": "app2",
                    "message_pattern": "foo",
                    "severity_threshold": 99,
                },
                # Empty criteria
                {
                    "source_pattern": None,
                    "app_pattern": None,
                    "message_pattern": "*",
                    "severity_threshold": None,
                },
                # Valid rule
                {
                    "app_pattern": "valid-app",
                    "message_pattern": "valid-pattern",
                    "is_regex": False,
                },
            ]
        }
        res = await client.post("/api/drop-rules/import", headers=auth_headers, json=bad_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["imported"] == 1
        assert len(data["errors"]) == 3


class TestAlertRulesExportImport:
    """Test alert rules export and import endpoints."""

    @pytest.mark.asyncio
    async def test_export_all_empty(self, client: AsyncClient, auth_headers: dict):
        res = await client.get("/api/alerts/export", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == "1"
        assert "exported_at" in data
        assert data["alert_rules"] == []

        # Also via /api/alerts/rules/export
        res2 = await client.get("/api/alerts/rules/export", headers=auth_headers)
        assert res2.status_code == 200
        assert res2.json()["alert_rules"] == []

    @pytest.mark.asyncio
    async def test_export_all_and_single(self, client: AsyncClient, auth_headers: dict):
        # Create an alert rule
        create_res = await client.post(
            "/api/alerts/rules",
            headers=auth_headers,
            json={
                "name": "Nginx Error Spike",
                "rule_type": "threshold",
                "channel_id": None,
                "filter_app": "nginx",
                "filter_severity": 3,
                "match_pattern": "502 Bad Gateway",
                "threshold_count": 5,
                "window_seconds": 60,
                "cooldown_seconds": 300,
                "ai_enrichment": True,
                "is_enabled": True,
            },
        )
        assert create_res.status_code == 201
        created = create_res.json()
        rule_id = created["id"]

        # Export all via /api/alerts/export, /api/alerts/rules/export, and /api/v1/alerts/export
        res_all = await client.get("/api/alerts/export", headers=auth_headers)
        assert res_all.status_code == 200
        all_data = res_all.json()
        assert all_data["version"] == "1"
        assert len(all_data["alert_rules"]) == 1
        exported = all_data["alert_rules"][0]
        assert exported["name"] == "Nginx Error Spike"
        assert exported["rule_type"] == "threshold"
        assert exported["filter_app"] == "nginx"
        assert exported["filter_severity"] == 3
        assert exported["match_pattern"] == "502 Bad Gateway"
        assert exported["threshold_count"] == 5
        assert exported["window_seconds"] == 60
        assert exported["cooldown_seconds"] == 300
        assert exported["ai_enrichment"] is True
        assert exported["is_enabled"] is True
        assert "id" not in exported
        assert "channel_id" not in exported
        assert "trigger_count" not in exported
        assert "created_at" not in exported

        res_v1_all = await client.get("/api/v1/alerts/export", headers=auth_headers)
        assert res_v1_all.status_code == 200
        assert len(res_v1_all.json()["alert_rules"]) == 1

        # Export single rule via /api/alerts/{id}/export, /api/alerts/rules/{id}/export, and /api/v1
        res_single = await client.get(f"/api/alerts/{rule_id}/export", headers=auth_headers)
        assert res_single.status_code == 200
        assert res_single.json()["alert_rule"]["name"] == "Nginx Error Spike"

        res_single_rules = await client.get(f"/api/alerts/rules/{rule_id}/export", headers=auth_headers)
        assert res_single_rules.status_code == 200
        assert res_single_rules.json()["alert_rule"]["name"] == "Nginx Error Spike"

        res_v1_single = await client.get(f"/api/v1/alerts/{rule_id}/export", headers=auth_headers)
        assert res_v1_single.status_code == 200
        assert res_v1_single.json()["alert_rule"]["name"] == "Nginx Error Spike"

        # 404 for non-existent rule
        res_404 = await client.get("/api/alerts/9999/export", headers=auth_headers)
        assert res_404.status_code == 404

    @pytest.mark.asyncio
    async def test_import_polymorphic_payloads_and_deduplication(self, client: AsyncClient, auth_headers: dict):
        # 1. Import bundle format
        bundle_payload = {
            "version": "1",
            "exported_at": "2026-09-25T12:00:00Z",
            "alert_rules": [
                {
                    "name": "Database Connection Lost",
                    "rule_type": "pattern",
                    "filter_app": "postgres",
                    "filter_severity": None,
                    "match_pattern": "database connection lost",
                    "threshold_count": 1,
                    "window_seconds": 60,
                    "cooldown_seconds": 300,
                    "ai_enrichment": False,
                    "is_enabled": True,
                },
                {
                    "name": "Redis OOM",
                    "rule_type": "threshold",
                    "filter_app": "redis",
                    "filter_severity": 3,
                    "match_pattern": "OOM command not allowed",
                    "threshold_count": 3,
                    "window_seconds": 120,
                    "cooldown_seconds": 600,
                    "ai_enrichment": True,
                    "is_enabled": True,
                },
            ],
        }
        res = await client.post("/api/alerts/import", headers=auth_headers, json=bundle_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["imported"] == 2
        assert data["skipped"] == 0
        assert data["errors"] == []

        # Verify channel_id is null on imported rules
        rules_res = await client.get("/api/alerts/rules", headers=auth_headers)
        rules = rules_res.json()
        assert len(rules) == 2
        assert all(r["channel_id"] is None for r in rules)

        # 2. Re-import should skip duplicate names case-insensitively
        dupe_payload = {
            "alert_rules": [
                {
                    "name": "database connection lost",  # lower-case duplicate
                    "rule_type": "threshold",
                }
            ]
        }
        res_dupe = await client.post("/api/alerts/rules/import", headers=auth_headers, json=dupe_payload)
        assert res_dupe.status_code == 200
        assert res_dupe.json()["imported"] == 0
        assert res_dupe.json()["skipped"] == 1

        # 3. Single-rule container format { "alert_rule": { ... } }
        single_payload = {
            "alert_rule": {
                "name": "SSH Brute-Force",
                "rule_type": "pattern",
                "match_pattern": "Failed password for",
            }
        }
        res_single = await client.post("/api/alerts/import", headers=auth_headers, json=single_payload)
        assert res_single.status_code == 200
        assert res_single.json()["imported"] == 1

        # 4. Raw single rule format { "name": ... }
        raw_payload = {
            "name": "Kernel Panic",
            "rule_type": "pattern",
            "match_pattern": "Kernel panic",
        }
        res_raw = await client.post("/api/v1/alerts/import", headers=auth_headers, json=raw_payload)
        assert res_raw.status_code == 200
        assert res_raw.json()["imported"] == 1

    @pytest.mark.asyncio
    async def test_import_validation_and_errors(self, client: AsyncClient, auth_headers: dict):
        bad_payload = {
            "alert_rules": [
                # Missing name
                {
                    "rule_type": "threshold",
                    "match_pattern": "foo",
                },
                # Invalid regex
                {
                    "name": "Bad Regex Rule",
                    "match_pattern": "[a-z(invalid",
                },
                # Invalid severity
                {
                    "name": "Bad Severity Rule",
                    "filter_severity": 10,
                },
                # Valid rule
                {
                    "name": "Valid Rule",
                    "match_pattern": "healthy",
                },
            ]
        }
        res = await client.post("/api/alerts/import", headers=auth_headers, json=bad_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["imported"] == 1
        assert len(data["errors"]) == 3
