"""
Integration tests for Alerts API: CRUD endpoints, security presets, pattern testing, and alert history.
"""

from datetime import datetime, timedelta, timezone
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


class TestAlertsApi:
    """Tests for Alert Rules, Presets, and History API endpoints."""

    @pytest.mark.asyncio
    async def test_crud_alert_rule(self, client: AsyncClient, auth_headers: dict):
        # 1. Initial list should be empty
        list_res = await client.get("/api/alerts/rules", headers=auth_headers)
        assert list_res.status_code == 200
        assert list_res.json() == []

        # 2. Create an alert rule
        create_payload = {
            "name": "Nginx 5xx Error Spike",
            "rule_type": "threshold",
            "channel_id": None,
            "filter_app": "nginx",
            "filter_severity": 3,
            "match_pattern": r"HTTP/\d\.\d\"\s+5\d\d",
            "threshold_count": 10,
            "window_seconds": 60,
            "cooldown_seconds": 300,
            "ai_enrichment": True,
            "is_enabled": True,
        }
        create_res = await client.post("/api/alerts/rules", json=create_payload, headers=auth_headers)
        assert create_res.status_code == 201
        created = create_res.json()
        rule_id = created["id"]
        assert created["name"] == "Nginx 5xx Error Spike"
        assert created["threshold_count"] == 10
        assert created["ai_enrichment"] is True

        # 3. Get single rule
        get_res = await client.get(f"/api/alerts/rules/{rule_id}", headers=auth_headers)
        assert get_res.status_code == 200
        assert get_res.json()["id"] == rule_id

        # 4. Update rule
        update_payload = {
            "name": "Nginx Critical 5xx Spike",
            "threshold_count": 15,
            "is_enabled": False,
            "reset_cooldown": True,
        }
        update_res = await client.put(f"/api/alerts/rules/{rule_id}", json=update_payload, headers=auth_headers)
        assert update_res.status_code == 200
        updated = update_res.json()
        assert updated["name"] == "Nginx Critical 5xx Spike"
        assert updated["threshold_count"] == 15
        assert updated["is_enabled"] is False

        # 4b. Update rule to unset optional fields (channel_id, filter_app, filter_severity)
        clear_payload = {
            "channel_id": None,
            "filter_app": None,
            "filter_severity": None,
        }
        clear_res = await client.put(f"/api/alerts/rules/{rule_id}", json=clear_payload, headers=auth_headers)
        assert clear_res.status_code == 200
        cleared = clear_res.json()
        assert cleared["channel_id"] is None
        assert cleared["filter_app"] is None
        assert cleared["filter_severity"] is None

        # 5. Delete rule
        del_res = await client.delete(f"/api/alerts/rules/{rule_id}", headers=auth_headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        # 6. Verify 404 on deleted rule
        get_deleted = await client.get(f"/api/alerts/rules/{rule_id}", headers=auth_headers)
        assert get_deleted.status_code == 404

    @pytest.mark.asyncio
    async def test_create_rule_invalid_channel_returns_400(self, client: AsyncClient, auth_headers: dict):
        payload = {
            "name": "Invalid Channel Rule",
            "rule_type": "threshold",
            "channel_id": 99999,
            "threshold_count": 1,
            "window_seconds": 60,
            "cooldown_seconds": 60,
        }
        res = await client.post("/api/alerts/rules", json=payload, headers=auth_headers)
        assert res.status_code == 400
        assert "does not exist" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_test_pattern_endpoint(self, client: AsyncClient, auth_headers: dict):
        # 1. Match with IP extraction
        payload = {
            "rule_type": "threshold",
            "filter_app": "sshd",
            "filter_severity": 6,
            "match_pattern": r"Failed password.*from",
            "sample_message": "Failed password for root from 192.168.1.45 port 22 ssh2",
            "sample_app": "sshd",
            "sample_severity": 5,
        }
        res = await client.post("/api/alerts/test", json=payload, headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["matched"] is True
        assert data["extracted_ip"] == "192.168.1.45"
        assert data["error"] is None

        # 2. Non-matching sample
        payload_no_match = {
            "rule_type": "threshold",
            "filter_app": "sshd",
            "match_pattern": "Failed password",
            "sample_message": "Accepted publickey for ubuntu",
            "sample_app": "sshd",
        }
        res_no = await client.post("/api/alerts/test", json=payload_no_match, headers=auth_headers)
        assert res_no.status_code == 200
        assert res_no.json()["matched"] is False

        # 3. Invalid regex syntax returns error message
        payload_bad_regex = {
            "rule_type": "threshold",
            "match_pattern": "[invalid(regex",
            "sample_message": "test line",
        }
        res_bad = await client.post("/api/alerts/test", json=payload_bad_regex, headers=auth_headers)
        assert res_bad.status_code == 200
        assert "Invalid regex syntax" in (res_bad.json()["error"] or "")

    @pytest.mark.asyncio
    async def test_security_presets_endpoints(self, client: AsyncClient, auth_headers: dict):
        # 1. List presets
        res = await client.get("/api/alerts/presets", headers=auth_headers)
        assert res.status_code == 200
        presets = res.json()
        assert len(presets) == 5
        preset_ids = [p["id"] for p in presets]
        assert "ssh_bruteforce" in preset_ids
        assert "proxy_auth_flood" in preset_ids
        assert "sudo_escalation" in preset_ids
        assert "oom_killer" in preset_ids
        assert "log_storm_detection" in preset_ids

        # 2. 1-Click install preset
        install_res = await client.post(
            "/api/alerts/presets/ssh_bruteforce/install",
            json={},
            headers=auth_headers,
        )
        assert install_res.status_code == 201
        installed_rule = install_res.json()
        assert installed_rule["name"] == "SSH Brute-Force Detection"
        assert installed_rule["is_enabled"] is True
        assert installed_rule["ai_enrichment"] is True

        # 3. Installing unknown preset returns 404
        bad_install = await client.post(
            "/api/alerts/presets/unknown_preset/install",
            json={},
            headers=auth_headers,
        )
        assert bad_install.status_code == 404

    @pytest.mark.asyncio
    async def test_alert_history_endpoints(self, client: AsyncClient, auth_headers: dict):
        # Insert sample history into database
        from app.core.config import get_db_path
        db_path = get_db_path()
        conn = get_connection(db_path)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_history
            (rule_id, rule_name, channel_id, trigger_count, sample_log, incident_summary, ai_enrichment, triggered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                "Test Security Spike",
                None,
                5,
                "Failed password from 10.0.0.1",
                "SSH brute-force attack detected.",
                1,
                now_iso,
            ),
        )
        history_id = cur.lastrowid
        conn.commit()
        conn.close()

        # 1. List history
        res = await client.get("/api/alerts/history", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == history_id
        assert data["items"][0]["rule_name"] == "Test Security Spike"

        # 2. Delete single history record
        del_item = await client.delete(f"/api/alerts/history/{history_id}", headers=auth_headers)
        assert del_item.status_code == 200

        # 3. Verify empty
        res_after = await client.get("/api/alerts/history", headers=auth_headers)
        assert res_after.json()["total"] == 0

        # 4. Clear all history
        clear_res = await client.delete("/api/alerts/history", headers=auth_headers)
        assert clear_res.status_code == 200

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_rejected(self, client: AsyncClient):
        # Unauthenticated request without session cookie
        res = await client.get("/api/alerts/rules")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_update_rule_cooldown_recomputes_suppress_until(self, client: AsyncClient, auth_headers: dict):
        # Create rule with 300s cooldown
        create_res = await client.post(
            "/api/alerts/rules",
            json={"name": "Cooldown Rule", "rule_type": "threshold", "cooldown_seconds": 300},
            headers=auth_headers,
        )
        assert create_res.status_code == 201
        rule_id = create_res.json()["id"]

        # Simulate that it fired 10 seconds ago and was suppressed for 300s
        from app.core.config import get_db_path
        db_path = get_db_path()
        conn = get_connection(db_path)
        past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        future_suppress = past_time + timedelta(seconds=300)
        cur = conn.cursor()
        cur.execute(
            "UPDATE alert_rules SET last_triggered_at = ?, suppress_until = ? WHERE id = ?",
            (past_time.isoformat(), future_suppress.isoformat(), rule_id),
        )
        conn.commit()
        conn.close()

        # Update cooldown from 300s to 5s
        update_res = await client.put(
            f"/api/alerts/rules/{rule_id}",
            json={"cooldown_seconds": 5},
            headers=auth_headers,
        )
        assert update_res.status_code == 200
        # Since last trigger was 10s ago, 5s cooldown is already over, so suppress_until must be None!
        assert update_res.json()["suppress_until"] is None
        assert update_res.json()["cooldown_seconds"] == 5

        # Verify cooldown_seconds < 5 is rejected by validation (422)
        invalid_res = await client.put(
            f"/api/alerts/rules/{rule_id}",
            json={"cooldown_seconds": 4},
            headers=auth_headers,
        )
        assert invalid_res.status_code == 422

    @pytest.mark.asyncio
    async def test_create_alert_rule_redos_patterns_rejected(self, client: AsyncClient, auth_headers: dict):
        # 1. Pathological nested repetition (a+)+
        res1 = await client.post(
            "/api/alerts/rules",
            json={"name": "ReDoS Rule 1", "rule_type": "pattern", "match_pattern": "(a+)+"},
            headers=auth_headers,
        )
        assert res1.status_code == 400
        assert "backtracking" in res1.json().get("detail", "").lower()

        # 2. Pathological nested repetition ([a-z]+)*
        res2 = await client.post(
            "/api/alerts/rules",
            json={"name": "ReDoS Rule 2", "rule_type": "pattern", "match_pattern": "([a-z]+)*"},
            headers=auth_headers,
        )
        assert res2.status_code == 400
        assert "backtracking" in res2.json().get("detail", "").lower()

        # 3. Invalid regex syntax
        res3 = await client.post(
            "/api/alerts/rules",
            json={"name": "Bad Syntax Rule", "rule_type": "pattern", "match_pattern": "[invalid(regex"},
            headers=auth_headers,
        )
        assert res3.status_code == 400
        assert "invalid" in res3.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_update_alert_rule_redos_rejected(self, client: AsyncClient, auth_headers: dict):
        # Create safe rule
        create_res = await client.post(
            "/api/alerts/rules",
            json={"name": "Safe Rule", "rule_type": "pattern", "match_pattern": "Failed login"},
            headers=auth_headers,
        )
        assert create_res.status_code == 201
        rule_id = create_res.json()["id"]

        # Attempt to update with nested repetition ReDoS pattern
        update_res = await client.put(
            f"/api/alerts/rules/{rule_id}",
            json={"match_pattern": r"(\w+)*"},
            headers=auth_headers,
        )
        assert update_res.status_code == 400
        assert "backtracking" in update_res.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_test_pattern_endpoint_redos_detected(self, client: AsyncClient, auth_headers: dict):
        payload = {
            "rule_type": "pattern",
            "match_pattern": r"((a+)+)+",
            "sample_message": "aaaaaaaaaaaaaaaaaaaa!",
        }
        res = await client.post("/api/alerts/test", json=payload, headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["matched"] is False
        assert "backtracking" in (data["error"] or "").lower()


class TestMaintenanceWindowApi:
    """Tests for GET and POST /api/v1/alerts/maintenance and /api/alerts/maintenance."""

    @pytest.mark.asyncio
    async def test_get_maintenance_initial_inactive(self, client: AsyncClient, auth_headers: dict):
        res = await client.get("/api/v1/alerts/maintenance", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["active"] is False
        assert data["until"] is None

        # Verify dual route
        res2 = await client.get("/api/alerts/maintenance", headers=auth_headers)
        assert res2.status_code == 200
        assert res2.json()["active"] is False

    @pytest.mark.asyncio
    async def test_set_and_clear_maintenance_window(self, client: AsyncClient, auth_headers: dict):
        future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

        # Set maintenance window
        res = await client.post(
            "/api/v1/alerts/maintenance",
            json={"until": future_iso},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["active"] is True
        assert data["until"] is not None

        # Verify GET returns active
        get_res = await client.get("/api/v1/alerts/maintenance", headers=auth_headers)
        assert get_res.status_code == 200
        assert get_res.json()["active"] is True

        # Verify settings GET reflects maintenance_until
        settings_res = await client.get("/api/settings", headers=auth_headers)
        assert settings_res.status_code == 200
        assert settings_res.json()["maintenance_until"] is not None

        # Clear maintenance window
        clear_res = await client.post(
            "/api/v1/alerts/maintenance",
            json={"until": None},
            headers=auth_headers,
        )
        assert clear_res.status_code == 200
        clear_data = clear_res.json()
        assert clear_data["active"] is False
        assert clear_data["until"] is None

        # Verify GET now returns inactive
        get_res2 = await client.get("/api/v1/alerts/maintenance", headers=auth_headers)
        assert get_res2.status_code == 200
        assert get_res2.json()["active"] is False
        assert get_res2.json()["until"] is None

    @pytest.mark.asyncio
    async def test_clear_maintenance_and_session_deactivation(self, client: AsyncClient, auth_headers: dict):
        from app.services.maintenance_service import create_maintenance_session

        # Create active sessions
        def _add_sessions(conn):
            create_maintenance_session(conn, duration_minutes=60, reason="Session 1", initiated_by="test-user")
            create_maintenance_session(conn, duration_minutes=60, reason="Session 2", initiated_by="test-user")

        from app.api.deps import run_db_query
        await run_db_query(_add_sessions)

        # Confirm active
        get_res = await client.get("/api/alerts/maintenance", headers=auth_headers)
        assert get_res.status_code == 200
        sessions = get_res.json()["sessions"]
        assert len(sessions) == 2

        # Terminate single session via DELETE endpoint
        target_id = sessions[0]["session_id"]
        del_res = await client.delete(f"/api/alerts/maintenance/sessions/{target_id}", headers=auth_headers)
        assert del_res.status_code == 200
        remaining_sessions = del_res.json()["sessions"]
        assert len(remaining_sessions) == 1
        assert remaining_sessions[0]["session_id"] != target_id

        # Clear on-demand window should terminate all remaining sessions
        clear_res = await client.post("/api/alerts/maintenance", json={"until": None}, headers=auth_headers)
        assert clear_res.status_code == 200
        assert clear_res.json()["active"] is False
        assert len(clear_res.json()["sessions"]) == 0


    @pytest.mark.asyncio
    async def test_set_maintenance_invalid_format(self, client: AsyncClient, auth_headers: dict):
        res = await client.post(
            "/api/v1/alerts/maintenance",
            json={"until": "not-a-datetime"},
            headers=auth_headers,
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_maintenance_schedules_crud_and_evaluation(self, client: AsyncClient, auth_headers: dict):
        schedules_payload = {
            "schedules": [
                {
                    "id": "sched_daily",
                    "name": "Daily Early Updates",
                    "enabled": True,
                    "recurrence": "daily",
                    "start_time": "03:00",
                    "duration_minutes": 60,
                },
                {
                    "id": "sched_weekly",
                    "name": "Sunday Night Scrub",
                    "enabled": False,
                    "recurrence": "weekly",
                    "start_time": "02:00",
                    "duration_minutes": 120,
                    "day_of_week": 0,
                },
                {
                    "id": "sched_monthly",
                    "name": "First of Month Backup",
                    "enabled": True,
                    "recurrence": "monthly",
                    "start_time": "04:00",
                    "duration_minutes": 180,
                    "day_of_month": 1,
                },
            ]
        }

        # Save schedules
        save_res = await client.post(
            "/api/v1/alerts/maintenance/schedules",
            json=schedules_payload,
            headers=auth_headers,
        )
        assert save_res.status_code == 200
        data = save_res.json()
        assert len(data["schedules"]) == 3
        assert data["schedules"][0]["name"] == "Daily Early Updates"
        assert data["schedules"][0]["next_run"] is not None

        # Verify GET returns the saved schedules
        get_res = await client.get("/api/v1/alerts/maintenance", headers=auth_headers)
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert len(get_data["schedules"]) == 3
        assert get_data["server_time"] is not None
        assert get_data["server_timezone"] is not None

    @pytest.mark.asyncio
    async def test_reorder_alert_rules(self, client: AsyncClient, auth_headers: dict):
        # 1. Create three alert rules
        r1 = (await client.post("/api/alerts/rules", json={"name": "Alert Alpha", "rule_type": "threshold"}, headers=auth_headers)).json()
        r2 = (await client.post("/api/alerts/rules", json={"name": "Alert Beta", "rule_type": "threshold"}, headers=auth_headers)).json()
        r3 = (await client.post("/api/alerts/rules", json={"name": "Alert Gamma", "rule_type": "threshold"}, headers=auth_headers)).json()
        r1_id, r2_id, r3_id = r1["id"], r2["id"], r3["id"]

        cur_rules = (await client.get("/api/alerts/rules", headers=auth_headers)).json()
        all_ids = [r["id"] for r in cur_rules]

        # 2. Duplicate rule IDs rejected with 400
        bad_dup = [r1_id, r1_id] + [i for i in all_ids if i not in (r1_id,)]
        res_dup = await client.put("/api/alerts/rules/reorder", json={"rule_ids": bad_dup}, headers=auth_headers)
        assert res_dup.status_code == 400

        # 3. Partial rule IDs rejected with 400
        res_partial = await client.put("/api/alerts/rules/reorder", json={"rule_ids": [r1_id, r2_id]}, headers=auth_headers)
        assert res_partial.status_code == 400

        # 4. Unknown rule ID rejected with 400
        bad_unk = all_ids[:-1] + [999999]
        res_unk = await client.put("/api/alerts/rules/reorder", json={"rule_ids": bad_unk}, headers=auth_headers)
        assert res_unk.status_code == 400

        # 5. Successful reorder: move r3 to front
        other_ids = [i for i in all_ids if i not in (r1_id, r2_id, r3_id)]
        new_order = [r3_id, r1_id, r2_id] + other_ids
        reorder_res = await client.put("/api/alerts/rules/reorder", json={"rule_ids": new_order}, headers=auth_headers)
        assert reorder_res.status_code == 200
        reordered_items = reorder_res.json()
        assert [r["id"] for r in reordered_items] == new_order

        # Verify display_order values are sequential 1, 2, 3...
        for idx, item in enumerate(reordered_items, start=1):
            assert item["display_order"] == idx

        # 6. GET /api/alerts/rules returns the updated order
        get_res = await client.get("/api/alerts/rules", headers=auth_headers)
        assert get_res.status_code == 200
        assert [r["id"] for r in get_res.json()] == new_order

        # 7. In-memory alert evaluator rules are synchronized
        from app.services.alert_evaluator import get_alert_evaluator
        evaluator_rules = get_alert_evaluator()._rules
        eval_ids = [r.id for r in evaluator_rules]
        assert eval_ids.index(r3_id) < eval_ids.index(r1_id) < eval_ids.index(r2_id)


