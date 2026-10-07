"""
Unit and integration tests for Alert Presets and Drop Presets discovery and API endpoints.
"""

import json
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import run_migrations
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services.alert_evaluator import init_alert_evaluator
from app.services.alert_presets import get_alert_preset_by_id, get_alert_presets
from app.core.rate_limiter import login_rate_limiter
from app.services.drop_filter import init_drop_filter
from app.services.drop_presets import get_drop_preset_by_id, get_drop_presets


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


class TestAlertPresetsDiscovery:
    """Tests for standalone JSON alert preset discovery, merging, and overrides."""

    def test_builtin_alert_presets_loaded(self):
        presets = get_alert_presets()
        assert len(presets) >= 4
        ids = {p["id"] for p in presets}
        assert {"ssh_bruteforce", "proxy_auth_flood", "sudo_escalation", "oom_killer"}.issubset(ids)

        for p in presets:
            assert p["is_custom"] is False
            assert "name" in p
            assert "description" in p
            assert "rule_type" in p

    def test_get_alert_preset_by_id(self):
        ssh = get_alert_preset_by_id("ssh_bruteforce")
        assert ssh is not None
        assert ssh["id"] == "ssh_bruteforce"
        assert ssh["rule_type"] == "threshold"
        assert ssh["is_custom"] is False

        # Case insensitive
        ssh_upper = get_alert_preset_by_id("SSH_BRUTEFORCE")
        assert ssh_upper is not None
        assert ssh_upper["id"] == "ssh_bruteforce"

        # Unknown id
        unknown = get_alert_preset_by_id("non_existent_preset")
        assert unknown is None

    def test_user_alert_preset_discovery_and_override(self, tmp_path, monkeypatch):
        # Point DATA_DIR to tmp_path
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        user_presets_dir = tmp_path / "presets" / "alerts"
        user_presets_dir.mkdir(parents=True)

        # 1. Override an existing built-in preset
        override_data = {
            "id": "ssh_bruteforce",
            "name": "Custom SSH Protection",
            "description": "Overridden threshold for SSH brute-force.",
            "rule_type": "threshold",
            "threshold_count": 10,
            "window_seconds": 120,
            "cooldown_seconds": 600,
            "ai_enrichment": False,
        }
        (user_presets_dir / "ssh_bruteforce.json").write_text(json.dumps(override_data))

        # 2. Add a new custom community preset
        new_preset_data = {
            "id": "redis_unauthorized",
            "name": "Redis Unauthorized Access",
            "description": "Detects unauthorized attempts to access unprotected Redis instances.",
            "rule_type": "pattern",
            "filter_app": "redis",
            "match_pattern": "NOAUTH Authentication required",
            "threshold_count": 1,
            "window_seconds": 60,
            "cooldown_seconds": 60,
            "ai_enrichment": True,
        }
        (user_presets_dir / "redis_unauth.json").write_text(json.dumps(new_preset_data))

        presets = get_alert_presets()
        preset_map = {p["id"]: p for p in presets}

        # Check override
        assert "ssh_bruteforce" in preset_map
        assert preset_map["ssh_bruteforce"]["name"] == "Custom SSH Protection"
        assert preset_map["ssh_bruteforce"]["threshold_count"] == 10
        assert preset_map["ssh_bruteforce"]["is_custom"] is True

        # Check new preset
        assert "redis_unauthorized" in preset_map
        assert preset_map["redis_unauthorized"]["name"] == "Redis Unauthorized Access"
        assert preset_map["redis_unauthorized"]["is_custom"] is True

        # Check untouched built-ins remain is_custom=False
        assert preset_map["sudo_escalation"]["is_custom"] is False


class TestDropPresetsDiscovery:
    """Tests for standalone JSON drop rule preset discovery, merging, and overrides."""

    def test_builtin_drop_presets_loaded(self):
        presets = get_drop_presets()
        assert len(presets) >= 3
        ids = {p["id"] for p in presets}
        assert {"docker_healthcheck", "systemd_session", "cron_noise"}.issubset(ids)

        for p in presets:
            assert p["is_custom"] is False
            assert "name" in p
            assert "description" in p
            assert "message_pattern" in p

    def test_get_drop_preset_by_id(self):
        preset = get_drop_preset_by_id("docker_healthcheck")
        assert preset is not None
        assert preset["id"] == "docker_healthcheck"
        assert preset["is_regex"] is True
        assert preset["is_custom"] is False

        # Unknown id
        assert get_drop_preset_by_id("invalid_id") is None

    def test_user_drop_preset_discovery_and_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        user_presets_dir = tmp_path / "presets" / "drops"
        user_presets_dir.mkdir(parents=True)

        # 1. Override built-in drop preset
        override_data = {
            "id": "docker_healthcheck",
            "name": "Custom Healthcheck Filter",
            "description": "Customized docker health check filter.",
            "source_pattern": "docker*",
            "app_pattern": None,
            "message_pattern": "healthcheck|status_check",
            "is_regex": True,
            "severity_threshold": 6,
        }
        (user_presets_dir / "docker_healthcheck.json").write_text(json.dumps(override_data))

        # 2. Add new user drop preset
        new_data = {
            "id": "nginx_ping",
            "name": "Nginx Ping Noise",
            "description": "Drops frequent ALB health check pings.",
            "source_pattern": None,
            "app_pattern": "nginx",
            "message_pattern": "GET /healthz HTTP/1.1",
            "is_regex": False,
            "severity_threshold": None,
        }
        (user_presets_dir / "nginx_ping.json").write_text(json.dumps(new_data))

        presets = get_drop_presets()
        preset_map = {p["id"]: p for p in presets}

        assert preset_map["docker_healthcheck"]["name"] == "Custom Healthcheck Filter"
        assert preset_map["docker_healthcheck"]["is_custom"] is True
        assert preset_map["docker_healthcheck"]["severity_threshold"] == 6

        assert "nginx_ping" in preset_map
        assert preset_map["nginx_ping"]["is_custom"] is True

        assert preset_map["cron_noise"]["is_custom"] is False


class TestDropPresetsApi:
    """API endpoint tests for drop presets."""

    @pytest.mark.asyncio
    async def test_list_drop_presets_endpoint(self, client: AsyncClient, auth_headers: dict):
        res = await client.get("/api/drop-rules/presets", headers=auth_headers)
        assert res.status_code == 200
        presets = res.json()
        assert len(presets) >= 3
        ids = [p["id"] for p in presets]
        assert "docker_healthcheck" in ids
        assert "systemd_session" in ids
        assert "cron_noise" in ids

    @pytest.mark.asyncio
    async def test_install_drop_preset_success_and_404(self, client: AsyncClient, auth_headers: dict):
        # 1. Install built-in drop preset
        res = await client.post(
            "/api/drop-rules/presets/docker_healthcheck/install",
            headers=auth_headers,
        )
        assert res.status_code == 201
        data = res.json()
        assert data["message_pattern"] == "healthcheck|health_status"
        assert data["is_regex"] is True
        assert data["is_enabled"] is True
        assert data["dropped_count"] == 0

        # Verify it appears in active drop rules list
        list_res = await client.get("/api/drop-rules", headers=auth_headers)
        assert list_res.status_code == 200
        active_rules = list_res.json()
        assert any(r["id"] == data["id"] for r in active_rules)

        # 2. Unknown preset 404
        bad_res = await client.post(
            "/api/drop-rules/presets/non_existent_preset/install",
            headers=auth_headers,
        )
        assert bad_res.status_code == 404
        assert "not found" in bad_res.json()["detail"].lower()


class TestPresetEdgeCasesAndConventions:
    """Tests covering preset file naming conventions, non-parseable files, and deleted preset files."""

    def test_file_naming_conventions(self):
        builtin_alerts_dir = Path(__file__).resolve().parent.parent / "app" / "presets" / "alerts"
        alert_files = [f.name for f in builtin_alerts_dir.glob("*.json")]
        assert len(alert_files) >= 4
        for fname in alert_files:
            assert fname.startswith("alert_"), f"Alert preset file {fname} should start with 'alert_'"

        builtin_drops_dir = Path(__file__).resolve().parent.parent / "app" / "presets" / "drops"
        drop_files = [f.name for f in builtin_drops_dir.glob("*.json")]
        assert len(drop_files) >= 3
        for fname in drop_files:
            assert fname.startswith("drop_"), f"Drop preset file {fname} should start with 'drop_'"

    def test_non_parseable_alert_preset_files_are_gracefully_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        user_alerts_dir = tmp_path / "presets" / "alerts"
        user_alerts_dir.mkdir(parents=True)

        # 1. Corrupted / invalid JSON
        (user_alerts_dir / "alert_broken.json").write_text("{ this is not valid json :(")

        # 2. JSON array instead of dict
        (user_alerts_dir / "alert_array.json").write_text("[\"item1\", \"item2\"]")

        # 3. Missing required fields (e.g. no 'id' or 'rule_type')
        (user_alerts_dir / "alert_incomplete.json").write_text(json.dumps({"name": "No ID Rule"}))

        # 4. Valid custom rule
        valid_custom = {
            "id": "valid_user_rule",
            "name": "Valid User Rule",
            "rule_type": "pattern",
            "match_pattern": "FATAL",
        }
        (user_alerts_dir / "alert_valid.json").write_text(json.dumps(valid_custom))

        # Loading must not raise any exceptions
        presets = get_alert_presets()
        preset_ids = {p["id"] for p in presets}

        # Built-ins still loaded
        assert "ssh_bruteforce" in preset_ids
        # Valid custom rule loaded
        assert "valid_user_rule" in preset_ids
        # Bad rules not included
        assert "alert_broken" not in preset_ids

    def test_non_parseable_drop_preset_files_are_gracefully_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        user_drops_dir = tmp_path / "presets" / "drops"
        user_drops_dir.mkdir(parents=True)

        # Corrupted JSON
        (user_drops_dir / "drop_corrupt.json").write_text("NOT JSON CONTENT")

        # Missing required field 'message_pattern'
        (user_drops_dir / "drop_incomplete.json").write_text(json.dumps({"id": "no_msg", "name": "No Pattern"}))

        # Loading must not raise any exceptions
        presets = get_drop_presets()
        preset_ids = {p["id"] for p in presets}

        assert "docker_healthcheck" in preset_ids
        assert "no_msg" not in preset_ids

    @pytest.mark.asyncio
    async def test_installed_rule_continues_functioning_when_preset_file_deleted(
        self, client: AsyncClient, auth_headers: dict, tmp_path, monkeypatch
    ):
        """
        Verify that installing a rule creates an independent database record.
        If the original preset file is deleted or removed from disk, the installed rule
        remains active, evaluated, and completely functional.
        """
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        user_drops_dir = tmp_path / "presets" / "drops"
        user_drops_dir.mkdir(parents=True)

        temp_preset_file = user_drops_dir / "drop_temporary.json"
        temp_preset_file.write_text(
            json.dumps({
                "id": "temporary_service_drop",
                "name": "Temporary Service Noise",
                "description": "Noise from a temporary decommissioned service.",
                "source_pattern": "10.0.0.99",
                "app_pattern": "temp-service",
                "message_pattern": "periodic heartbeat",
                "is_regex": False,
                "severity_threshold": None,
            })
        )

        # 1. Verify preset is discovered
        assert get_drop_preset_by_id("temporary_service_drop") is not None

        # 2. Install the preset into active drop rules
        install_res = await client.post(
            "/api/drop-rules/presets/temporary_service_drop/install",
            headers=auth_headers,
        )
        assert install_res.status_code == 201
        installed_rule = install_res.json()
        rule_id = installed_rule["id"]

        # 3. Delete the preset file from disk
        temp_preset_file.unlink()
        assert not temp_preset_file.exists()

        # Preset is no longer in preset catalog
        assert get_drop_preset_by_id("temporary_service_drop") is None

        # 4. Verify installed rule is still present, active, and functioning in drop_rules
        list_res = await client.get("/api/drop-rules", headers=auth_headers)
        assert list_res.status_code == 200
        active_rules = {r["id"]: r for r in list_res.json()}
        assert rule_id in active_rules
        assert active_rules[rule_id]["app_pattern"] == "temp-service"
        assert active_rules[rule_id]["is_enabled"] is True

        # Test evaluation still matches
        from app.services.drop_filter import get_drop_filter
        filter_engine = get_drop_filter()
        assert filter_engine.should_drop(
            source_alias=None,
            source_ip="10.0.0.99",
            app_name="temp-service",
            message="periodic heartbeat 123",
        ) == rule_id

    @pytest.mark.asyncio
    async def test_lifespan_creates_presets_directories(self, tmp_path, monkeypatch):
        """Verify that application lifespan startup automatically creates presets/alerts and presets/drops."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(tmp_path / "logs.db"))
        monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))

        alerts_dir = tmp_path / "presets" / "alerts"
        drops_dir = tmp_path / "presets" / "drops"

        assert not alerts_dir.exists()
        assert not drops_dir.exists()

        from app.main import create_app, lifespan
        test_app = create_app()

        async with lifespan(test_app):
            assert alerts_dir.is_dir()
            assert drops_dir.is_dir()


