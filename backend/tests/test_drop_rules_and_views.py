"""
Tests for Ingestion Drop Filter, Drop Rules API, and Saved Views API.
"""

import asyncio
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.core.regex_validator import safe_regex_search
from app.services.drop_filter import CompiledDropRule, DropFilter, get_drop_filter, init_drop_filter


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

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    pipeline_mod._dropped_by_filter_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ) as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


# ===================================================================
# 1. DropFilter Service Unit Tests
# ===================================================================

class TestDropFilterUnit:

    def test_substring_matching(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, NULL, 'DHCPACK', 0, 1, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = DropFilter(db_file)
        assert flt.should_drop("router", "192.168.1.1", "dnsmasq", "DHCPACK(eth0) 192.168.1.50") is not None
        assert flt.should_drop("router", "192.168.1.1", "dnsmasq", "dhcpack lowercase match") is not None
        assert flt.should_drop("router", "192.168.1.1", "dnsmasq", "DHCPDISCOVER on eth0") is None

    def test_wildcard_source_and_app_matching(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES ('192.168.1.*', 'smartbulb*', 'ping', 0, 1, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = DropFilter(db_file)
        # Matches both IP pattern and app pattern
        assert flt.should_drop("bulb-kitchen", "192.168.1.42", "smartbulb_kitchen", "periodic ping packet") is not None
        # Wrong IP
        assert flt.should_drop("bulb-kitchen", "10.0.0.42", "smartbulb_kitchen", "periodic ping packet") is None
        # Wrong App
        assert flt.should_drop("bulb-kitchen", "192.168.1.42", "smartswitch", "periodic ping packet") is None
        # Wrong message
        assert flt.should_drop("bulb-kitchen", "192.168.1.42", "smartbulb_kitchen", "error state") is None

    def test_regex_matching(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            r"""
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, NULL, 'GET /health\b.*200', 1, 1, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = DropFilter(db_file)
        assert flt.should_drop(None, None, "nginx", "GET /health HTTP/1.1 200") is not None
        assert flt.should_drop(None, None, "nginx", "GET /healthy HTTP/1.1 200") is None

    def test_disabled_rules_ignored(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, NULL, 'noise', 0, 0, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = DropFilter(db_file)
        assert flt.should_drop("srv", "127.0.0.1", "app", "noise message") is None

    def test_severity_threshold_matching(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at)
            VALUES (NULL, 'sshd', '*', 0, 1, 6, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = DropFilter(db_file)
        # Severity 7 (Debug >= 6) and 6 (Info >= 6) are at or below threshold -> dropped
        assert flt.should_drop(None, None, "sshd", "debug details", severity=7) is not None
        assert flt.should_drop(None, None, "sshd", "info details", severity=6) is not None

        # Severity 5 (Notice < 6), 4 (Warning < 6), 3 (Error < 6) are more critical -> kept (not dropped)
        assert flt.should_drop(None, None, "sshd", "notice details", severity=5) is None
        assert flt.should_drop(None, None, "sshd", "warning details", severity=4) is None
        assert flt.should_drop(None, None, "sshd", "error details", severity=3) is None

        # Missing severity parameter defaults to None -> not blocked by threshold check
        assert flt.should_drop(None, None, "sshd", "unspecified severity") is not None

    def test_in_memory_counter_and_flush(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, NULL, 'dropme', 0, 1, 5, datetime('now'))
            """
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        flt = DropFilter(db_file)
        assert flt.get_pending_count(rule_id) == 0

        # Drop 3 entries
        for _ in range(3):
            matched = flt.should_drop("srv", "127.0.0.1", "app", "dropme now")
            assert matched == rule_id

        assert flt.get_pending_count(rule_id) == 3

        # Flush to DB
        flushed = flt.flush_counts()
        assert flushed == 3
        assert flt.get_pending_count(rule_id) == 0

        conn = get_connection(db_file)
        row = conn.execute("SELECT dropped_count FROM drop_rules WHERE id = ?", (rule_id,)).fetchone()
        conn.close()
        assert row[0] == 8  # 5 initial + 3 flushed

    def test_init_drop_filter_preserves_in_memory_counters(self, tmp_path):
        from app.services.drop_filter import init_drop_filter
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, NULL, 'dropme', 0, 1, 0, datetime('now'))
            """
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        flt1 = init_drop_filter(db_file)
        matched = flt1.should_drop("srv", "127.0.0.1", "app", "dropme now")
        assert matched == rule_id
        assert flt1.get_pending_count(rule_id) == 1

        # Re-initializing drop filter with init_drop_filter preserves active in-memory counters
        flt2 = init_drop_filter(db_file)
        assert flt2 is flt1
        assert flt2.get_pending_count(rule_id) == 1


# ===================================================================
# 2. Drop Rules API Endpoints Tests
# ===================================================================

class TestDropRulesApi:

    @pytest.mark.asyncio
    async def test_auth_required(self, client: AsyncClient):
        res = await client.get("/api/drop-rules")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_csrf_protection(self, client: AsyncClient, auth_cookie: dict):
        # Missing X-Requested-With header
        res = await client.post(
            "/api/drop-rules",
            headers={"X-Requested-With": ""},
            cookies=auth_cookie,
            json={"message_pattern": "test"},
        )
        assert res.status_code == 403

    @pytest.mark.asyncio
    async def test_crud_drop_rule(self, client: AsyncClient, auth_cookie: dict):
        # 1. Create rule
        create_res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={
                "source_pattern": "192.168.1.*",
                "app_pattern": "dnsmasq",
                "message_pattern": "DHCPACK",
                "is_regex": False,
                "is_enabled": True,
            },
        )
        assert create_res.status_code == 201
        data = create_res.json()
        rule_id = data["id"]
        assert data["source_pattern"] == "192.168.1.*"
        assert data["app_pattern"] == "dnsmasq"
        assert data["message_pattern"] == "DHCPACK"
        assert data["dropped_count"] == 0

        # 2. List rules
        list_res = await client.get("/api/drop-rules", cookies=auth_cookie)
        assert list_res.status_code == 200
        rules = list_res.json()
        assert len(rules) >= 1
        assert any(r["id"] == rule_id for r in rules)

        # 3. Update rule
        update_res = await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={
                "is_enabled": False,
                "message_pattern": "DHCPNAK",
            },
        )
        assert update_res.status_code == 200
        updated = update_res.json()
        assert updated["is_enabled"] is False
        assert updated["message_pattern"] == "DHCPNAK"
        assert updated["source_pattern"] == "192.168.1.*"

        # 4. Delete rule
        del_res = await client.delete(f"/api/drop-rules/{rule_id}", cookies=auth_cookie)
        assert del_res.status_code == 200

        # Verify gone
        del_res2 = await client.delete(f"/api/drop-rules/{rule_id}", cookies=auth_cookie)
        assert del_res2.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_regex_rejected(self, client: AsyncClient, auth_cookie: dict):
        res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={
                "message_pattern": "[invalid(regex",
                "is_regex": True,
            },
        )
        assert res.status_code == 400
        assert "Invalid regular expression" in res.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("redos_pattern", [
        "(a|aa)+",
        "([a-z]|[a-z][a-z])+",
        "((a)+)+",
        "(a+)+",
        "([a-z]+)*",
    ])
    async def test_catastrophic_backtracking_rejected(self, client: AsyncClient, auth_cookie: dict, redos_pattern: str):
        res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={
                "message_pattern": redos_pattern,
                "is_regex": True,
            },
        )
        assert res.status_code == 400
        assert "catastrophic backtracking" in res.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_dry_run_test_endpoint(self, client: AsyncClient, auth_cookie: dict):
        # Match case
        res1 = await client.post(
            "/api/drop-rules/test",
            cookies=auth_cookie,
            json={
                "source_pattern": "192.168.1.*",
                "app_pattern": "traefik*",
                "message_pattern": "probe request",
                "is_regex": False,
                "sample_source": "192.168.1.100",
                "sample_app": "traefik-proxy",
                "sample_message": "Incoming probe request from scanner",
            },
        )
        assert res1.status_code == 200
        assert res1.json()["matched"] is True
        assert res1.json()["error"] is None

        # Non-match case
        res2 = await client.post(
            "/api/drop-rules/test",
            cookies=auth_cookie,
            json={
                "source_pattern": "10.0.0.*",
                "app_pattern": "traefik*",
                "message_pattern": "probe request",
                "is_regex": False,
                "sample_source": "192.168.1.100",
                "sample_app": "traefik-proxy",
                "sample_message": "Incoming probe request",
            },
        )
        assert res2.status_code == 200
        assert res2.json()["matched"] is False

        # Invalid regex in test returns error instead of crashing
        res3 = await client.post(
            "/api/drop-rules/test",
            cookies=auth_cookie,
            json={
                "message_pattern": "(unclosed parenthesis",
                "is_regex": True,
                "sample_message": "test",
            },
        )
        assert res3.status_code == 200
        assert res3.json()["matched"] is False
        assert res3.json()["error"] is not None

        # 4. Wildcard '*' message pattern with specific app matches any message
        res4 = await client.post(
            "/api/drop-rules/test",
            cookies=auth_cookie,
            json={
                "app_pattern": "pvedaemon",
                "message_pattern": "*",
                "is_regex": False,
                "sample_app": "pvedaemon",
                "sample_message": "VM 104 backup completed successfully in 12.4s",
            },
        )
        assert res4.status_code == 200
        assert res4.json()["matched"] is True

        # 5. Severity threshold dry-run testing
        # Threshold 6 (Info and below): Debug (7) matches
        res_sev_debug = await client.post(
            "/api/drop-rules/test",
            cookies=auth_cookie,
            json={
                "message_pattern": "*",
                "severity_threshold": 6,
                "sample_message": "debug level noise",
                "sample_severity": 7,
            },
        )
        assert res_sev_debug.status_code == 200
        assert res_sev_debug.json()["matched"] is True

        # Threshold 6: Warning (4) is more critical -> does not match
        res_sev_warn = await client.post(
            "/api/drop-rules/test",
            cookies=auth_cookie,
            json={
                "message_pattern": "*",
                "severity_threshold": 6,
                "sample_message": "warning level event",
                "sample_severity": 4,
            },
        )
        assert res_sev_warn.status_code == 200
        assert res_sev_warn.json()["matched"] is False

        # 6. Empty criteria rejected
        empty_res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"message_pattern": "*"},
        )
        assert empty_res.status_code == 400

        # 7. Rule created with app only (message_pattern defaults to '*')
        app_only_res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"app_pattern": "pvedaemon"},
        )
        assert app_only_res.status_code == 201
        assert app_only_res.json()["message_pattern"] == "*"
        assert app_only_res.json()["app_pattern"] == "pvedaemon"

    @pytest.mark.asyncio
    async def test_reset_counter(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        create_res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"name": "Custom Drop Rule", "message_pattern": "count_reset_test"},
        )
        rule_id = create_res.json()["id"]
        assert create_res.json()["name"] == "Custom Drop Rule"

        # Simulate dropped logs
        flt = get_drop_filter()
        flt.should_drop(None, None, None, "this is a count_reset_test line")
        flt.flush_counts()

        # Reset counter
        reset_res = await client.post(f"/api/drop-rules/{rule_id}/reset", cookies=auth_cookie)
        assert reset_res.status_code == 200
        assert reset_res.json()["dropped_count"] == 0
        assert reset_res.json()["name"] == "Custom Drop Rule"

    @pytest.mark.asyncio
    async def test_update_rule_resets_counter_on_criteria_change(self, client: AsyncClient, auth_cookie: dict):
        create_res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"message_pattern": "initial_pattern"},
        )
        rule_id = create_res.json()["id"]

        flt = get_drop_filter()
        flt.should_drop(None, None, None, "log with initial_pattern")
        flt.flush_counts()

        # Check count > 0
        get_res = await client.get("/api/drop-rules", cookies=auth_cookie)
        rule = next(r for r in get_res.json() if r["id"] == rule_id)
        assert rule["dropped_count"] == 1

        # Update only is_enabled - should NOT reset counter
        toggle_res = await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={"is_enabled": False},
        )
        assert toggle_res.status_code == 200
        assert toggle_res.json()["dropped_count"] == 1

        # Update message_pattern (criteria change) - SHOULD reset counter to 0
        update_res = await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={"message_pattern": "new_pattern"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["dropped_count"] == 0

        # Re-enable rule
        await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={"is_enabled": True},
        )

        # Simulate another drop
        flt.should_drop(None, None, None, "new_pattern")
        flt.flush_counts()
        get_res2 = await client.get("/api/drop-rules", cookies=auth_cookie)
        rule2 = next(r for r in get_res2.json() if r["id"] == rule_id)
        assert rule2["dropped_count"] == 1

        # Update severity_threshold (criteria change) - SHOULD reset counter to 0
        update_sev_res = await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={"severity_threshold": 6},
        )
        assert update_sev_res.status_code == 200
        assert update_sev_res.json()["dropped_count"] == 0
        assert update_sev_res.json()["severity_threshold"] == 6

    @pytest.mark.asyncio
    async def test_severity_threshold_validation_and_crud(self, client: AsyncClient, auth_cookie: dict):
        # 1. Invalid severity_threshold (< 0 or > 7) rejected
        invalid_high = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"message_pattern": "test", "severity_threshold": 8},
        )
        assert invalid_high.status_code == 422

        invalid_low = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"message_pattern": "test", "severity_threshold": -1},
        )
        assert invalid_low.status_code == 422

        # 2. Rule created with severity_threshold only (message defaults to '*')
        sev_only = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={"severity_threshold": 7},
        )
        assert sev_only.status_code == 201
        assert sev_only.json()["severity_threshold"] == 7
        assert sev_only.json()["message_pattern"] == "*"
        rule_id = sev_only.json()["id"]

        # 3. Update clearing severity_threshold back to None (null)
        clear_res = await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={"severity_threshold": None, "app_pattern": "sshd"},
        )
        assert clear_res.status_code == 200
        assert clear_res.json()["severity_threshold"] is None
        assert clear_res.json()["app_pattern"] == "sshd"

    @pytest.mark.asyncio
    async def test_drop_rule_name_crud_and_fallback(self, client: AsyncClient, auth_cookie: dict):
        # 1. Create rule with explicit name
        res = await client.post(
            "/api/drop-rules",
            cookies=auth_cookie,
            json={
                "name": "Drop Noisy Nginx Health",
                "app_pattern": "nginx",
                "message_pattern": "GET /health 200",
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "Drop Noisy Nginx Health"
        rule_id = data["id"]

        # 2. Update name
        up_res = await client.put(
            f"/api/drop-rules/{rule_id}",
            cookies=auth_cookie,
            json={"name": "Renamed Filter Rule"},
        )
        assert up_res.status_code == 200
        assert up_res.json()["name"] == "Renamed Filter Rule"

        # 3. Verify in list
        list_res = await client.get("/api/drop-rules", cookies=auth_cookie)
        assert list_res.status_code == 200
        matched = [r for r in list_res.json() if r["id"] == rule_id]
        assert len(matched) == 1
        assert matched[0]["name"] == "Renamed Filter Rule"

        # 4. Clean up
        await client.delete(f"/api/drop-rules/{rule_id}", cookies=auth_cookie)


# ===================================================================
# 3. Saved Views API Endpoints Tests
# ===================================================================

class TestSavedViewsApi:

    @pytest.mark.asyncio
    async def test_auth_required(self, client: AsyncClient):
        res = await client.get("/api/saved-views")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_crud_and_pin_ordering(self, client: AsyncClient, auth_cookie: dict):
        # 1. Create two views: one unpinned 'Zebra', one pinned 'Alpha'
        v1_res = await client.post(
            "/api/saved-views",
            cookies=auth_cookie,
            json={
                "name": "Zebra Unpinned",
                "query_params": {"q": "error", "severity": 3},
                "is_pinned": False,
            },
        )
        assert v1_res.status_code == 201
        v1_id = v1_res.json()["id"]
        assert v1_res.json()["name"] == "Zebra Unpinned"
        assert v1_res.json()["query_params"] == {"q": "error", "severity": 3}

        v2_res = await client.post(
            "/api/saved-views",
            cookies=auth_cookie,
            json={
                "name": "Alpha Pinned",
                "query_params": {"app": "traefik"},
                "is_pinned": True,
            },
        )
        assert v2_res.status_code == 201
        v2_id = v2_res.json()["id"]

        # 2. List views: pinned views come first
        list_res = await client.get("/api/saved-views", cookies=auth_cookie)
        assert list_res.status_code == 200
        views = list_res.json()
        assert len(views) >= 2
        # Alpha Pinned should be first because is_pinned = 1
        assert views[0]["name"] == "Alpha Pinned"
        assert views[0]["is_pinned"] is True

        # 3. Update view: toggle pin on Zebra Unpinned
        up_res = await client.put(
            f"/api/saved-views/{v1_id}",
            cookies=auth_cookie,
            json={"is_pinned": True, "name": "A-Zebra Pinned"},
        )
        assert up_res.status_code == 200
        assert up_res.json()["is_pinned"] is True

        # 4. Delete view
        del_res = await client.delete(f"/api/saved-views/{v1_id}", cookies=auth_cookie)
        assert del_res.status_code == 200
        del_res_404 = await client.delete(f"/api/saved-views/{v1_id}", cookies=auth_cookie)
        assert del_res_404.status_code == 404

        # Clean up v2
        await client.delete(f"/api/saved-views/{v2_id}", cookies=auth_cookie)


# ===================================================================
# 4. End-to-End Ingestion Pipeline Filtering Tests
# ===================================================================

class TestDropRulesIngestionPipeline:

    @pytest.mark.asyncio
    async def test_pipeline_discards_matching_log(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, 'noisy-app', 'heartbeat', 0, 1, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = get_drop_filter()
        flt.db_path = db_file
        flt.reload_rules()

        asm = pipeline_mod.KeyedMultilineAssembler()
        initial_filter_drops = pipeline_mod.get_dropped_by_filter_count()

        # Feed a matching log
        await asm.feed(
            "stream1",
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "received_at": "2024-01-01T00:00:00Z",
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "noisy-app",
                "facility": 1,
                "severity": 6,
                "message": "routine heartbeat check",
                "raw": "raw heartbeat",
            },
        )
        await asm.flush_all()

        # Queue should be empty because matching log was discarded
        queue = pipeline_mod.get_queue()
        assert queue.qsize() == 0

        # Global metric and rule-level metric incremented
        assert pipeline_mod.get_dropped_by_filter_count() == initial_filter_drops + 1
        assert sum(flt.get_all_pending_counts().values()) == 1

        # Feed a non-matching log
        await asm.feed(
            "stream2",
            {
                "timestamp": "2024-01-01T00:00:01Z",
                "received_at": "2024-01-01T00:00:01Z",
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "noisy-app",
                "facility": 1,
                "severity": 3,
                "message": "critical disk failure",
                "raw": "raw failure",
            },
        )
        await asm.flush_all()

        # Queue should now have 1 item
        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert item["message"] == "critical disk failure"

    @pytest.mark.asyncio
    async def test_internal_log_handler_filtering(self, tmp_path: Path):
        import logging
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, dropped_count, created_at)
            VALUES (NULL, NULL, 'suppress_internal_token', 0, 1, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = get_drop_filter()
        flt.db_path = db_file
        flt.reload_rules()

        handler = pipeline_mod.InternalLogHandler(level=logging.INFO)
        queue = pipeline_mod.get_queue()
        while not queue.empty():
            queue.get_nowait()

        record = logging.LogRecord(
            name="app.test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="This contains suppress_internal_token test",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        # Should be dropped and not enqueued
        assert queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_pipeline_severity_threshold_filtering(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        conn.execute(
            """
            INSERT INTO drop_rules (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, dropped_count, created_at)
            VALUES (NULL, 'api-worker', '*', 0, 1, 6, 0, datetime('now'))
            """
        )
        conn.commit()
        conn.close()

        flt = get_drop_filter()
        flt.db_path = db_file
        flt.reload_rules()

        asm = pipeline_mod.KeyedMultilineAssembler()
        queue = pipeline_mod.get_queue()
        while not queue.empty():
            queue.get_nowait()

        # 1. Info log (severity 6 >= threshold 6) -> dropped
        await asm.feed(
            "stream_info",
            {
                "timestamp": "2024-01-01T00:00:00Z",
                "received_at": "2024-01-01T00:00:00Z",
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "api-worker",
                "facility": 1,
                "severity": 6,
                "message": "Routine status ping",
                "raw": "raw ping",
            },
        )
        await asm.flush_all()
        assert queue.qsize() == 0

        # 2. Warning log (severity 4 < threshold 6) -> kept and enqueued
        await asm.feed(
            "stream_warn",
            {
                "timestamp": "2024-01-01T00:00:01Z",
                "received_at": "2024-01-01T00:00:01Z",
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "api-worker",
                "facility": 1,
                "severity": 4,
                "message": "High memory consumption warning",
                "raw": "raw warning",
            },
        )
        await asm.flush_all()
        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert item["message"] == "High memory consumption warning"
        assert item["severity"] == 4


def test_safe_regex_search_in_thread_and_bounded():
    """Verify safe_regex_search runs in-thread and bounds candidate strings to 16,384 chars."""
    import re

    # 1. Matching precompiled regex
    pat = re.compile(r"error:\s*\d+", re.IGNORECASE)
    assert safe_regex_search(pat, "System error: 500 occurred") is True
    assert safe_regex_search(pat, "All systems operational") is False

    # 2. String pattern auto-validation and matching
    assert safe_regex_search(r"warning:\s*\w+", "System warning: high_load") is True

    # 3. Pathological backtracking pattern rejected when string is passed
    assert safe_regex_search(r"(a+)+$", "aaaaaaaaaaaaaaaaaaaaa!") is False

    # 4. Long string bounded to 16,384 characters
    # If the match only occurs after 16,384 chars, it should NOT match
    long_prefix = "x" * 16384
    long_msg = long_prefix + "target_word"
    assert safe_regex_search(r"target_word", long_msg) is False
    assert safe_regex_search(r"^x+", long_msg) is True
