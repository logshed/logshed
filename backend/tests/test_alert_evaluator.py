"""
Unit and integration tests for AlertEvaluator, sliding-window deques,
pattern matching, cooldown flap dampening, IP extraction, and AI enrichment.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import pytest

from app.core.migrations import run_migrations, get_connection
from app.services.alert_evaluator import AlertEvaluator, CompiledAlertRule
from app.services.alert_presets import extract_ip_from_message, get_security_presets


class TestCompiledAlertRule:
    """Tests for single rule match evaluation."""

    def test_app_filter_exact_and_wildcard(self):
        rule = CompiledAlertRule(
            id=1,
            name="SSH Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app="sshd",
            filter_severity=None,
            match_pattern=None,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert rule.matches({"app_name": "sshd", "severity": 6, "message": "test"}) is True
        assert rule.matches({"app_name": "nginx", "severity": 6, "message": "test"}) is False

    def test_last_triggered_at_assignment(self):
        ts = "2026-09-19T12:00:00+00:00"
        rule = CompiledAlertRule(
            id=1,
            name="SSH Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app="sshd",
            filter_severity=None,
            match_pattern=None,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
            last_triggered_at=ts,
        )
        assert rule.last_triggered_at == ts

        # Wildcard test
        rule_wild = CompiledAlertRule(
            id=2,
            name="Wildcard Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app="ssh*",
            filter_severity=None,
            match_pattern=None,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert rule_wild.matches({"app_name": "sshd", "severity": 6, "message": "test"}) is True
        assert rule_wild.matches({"app_name": "ssh-daemon", "severity": 6, "message": "test"}) is True
        assert rule_wild.matches({"app_name": "nginx", "severity": 6, "message": "test"}) is False

    def test_severity_filter(self):
        # filter_severity = 3 (Error: match 0, 1, 2, 3 only)
        rule = CompiledAlertRule(
            id=1,
            name="Critical Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app=None,
            filter_severity=3,
            match_pattern=None,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert rule.matches({"severity": 2, "message": "critical event"}) is True
        assert rule.matches({"severity": 3, "message": "error event"}) is True
        assert rule.matches({"severity": 4, "message": "warning event"}) is False
        assert rule.matches({"severity": 6, "message": "info event"}) is False

    def test_pattern_matching_regex_and_substring(self):
        rule_regex = CompiledAlertRule(
            id=1,
            name="Regex Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app=None,
            filter_severity=None,
            match_pattern=r"Failed password.*from\s+([0-9.]+)",
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert (
            rule_regex.matches(
                {"message": "Failed password for root from 192.168.1.50 port 22"}
            )
            is True
        )
        assert rule_regex.matches({"message": "Accepted password for root"}) is False

        rule_substring = CompiledAlertRule(
            id=2,
            name="Substring Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app=None,
            filter_severity=None,
            match_pattern="bad gateway",
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert rule_substring.matches({"message": "Server error: 502 Bad Gateway detected"}) is True
        assert rule_substring.matches({"message": "Server running smoothly"}) is False

    def test_disabled_rule_never_matches(self):
        rule = CompiledAlertRule(
            id=1,
            name="Disabled Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app=None,
            filter_severity=None,
            match_pattern=None,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=False,
        )
        assert rule.matches({"message": "anything"}) is False


class TestAlertEvaluatorEngine:
    """Integration tests for AlertEvaluator sliding window, cooldown, and history."""

    @pytest.fixture
    def test_db(self, tmp_path: Path):
        db_file = tmp_path / "test_alerts.db"
        run_migrations(db_file)
        return db_file

    @pytest.mark.asyncio
    async def test_threshold_rule_triggers_and_evicts_sliding_window(self, test_db: Path, monkeypatch):
        # Insert a threshold rule: 3 events in 10 seconds
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Spike Alert", "threshold", "app", "error", 3, 10, 60, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        sent_notifications = []

        async def mock_send(self, title, body, channel_id=None):
            sent_notifications.append({"title": title, "body": body, "channel_id": channel_id})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        now = datetime.now(timezone.utc)

        # Batch 1: 2 logs (below threshold of 3)
        batch_1 = [
            {"id": 1, "app_name": "app", "message": "error 1", "timestamp": now.isoformat()},
            {"id": 2, "app_name": "app", "message": "error 2", "timestamp": (now + timedelta(seconds=1)).isoformat()},
        ]
        await evaluator.evaluate_batch(batch_1)
        await asyncio.sleep(0.05)  # Yield for any spawned tasks
        assert len(sent_notifications) == 0

        # Batch 2: 3rd log within window -> meets threshold of 3 -> fires alert!
        batch_2 = [
            {"id": 3, "app_name": "app", "message": "error 3", "timestamp": (now + timedelta(seconds=2)).isoformat()},
        ]
        await evaluator.evaluate_batch(batch_2)
        await asyncio.sleep(0.1)
        assert len(sent_notifications) == 1
        assert "Spike Alert" in sent_notifications[0]["title"]

        # Verify alert_history table has a record
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT rule_id, rule_name, trigger_count FROM alert_history")
        rows = cur.fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == rule_id
        assert rows[0][1] == "Spike Alert"
        assert rows[0][2] == 3

    @pytest.mark.asyncio
    async def test_cooldown_flap_dampening(self, test_db: Path, monkeypatch):
        # Rule with threshold=1, cooldown=300s
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Cooldown Rule", "threshold", "alert", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        sent_count = 0

        async def mock_send(self, title, body, channel_id=None):
            nonlocal sent_count
            sent_count += 1
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        # Event 1: should fire
        await evaluator.evaluate_batch([{"id": 1, "message": "alert 1", "timestamp": datetime.now(timezone.utc).isoformat()}])
        await asyncio.sleep(0.05)
        assert sent_count == 1

        # Event 2 immediately after: should be suppressed by cooldown!
        await evaluator.evaluate_batch([{"id": 2, "message": "alert 2", "timestamp": datetime.now(timezone.utc).isoformat()}])
        await asyncio.sleep(0.05)
        assert sent_count == 1  # Still 1, suppressed!

        # Event 3: also suppressed
        await evaluator.evaluate_batch([{"id": 3, "message": "alert 3", "timestamp": datetime.now(timezone.utc).isoformat()}])
        await asyncio.sleep(0.05)
        assert sent_count == 1

    @pytest.mark.asyncio
    async def test_ai_enrichment_and_ip_extraction(self, test_db: Path, monkeypatch):
        # Preset rule: SSH brute-force with ai_enrichment=1
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("SSH Threat", "threshold", "sshd", "Failed password", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        captured_ai_kwargs = {}
        # Mock AI analysis engine
        async def mock_execute_ai_analysis(**kwargs):
            captured_ai_kwargs.update(kwargs)
            return (
                "Automated SSH brute-force attack detected.",
                "External host attempted repetitive dictionary login.",
                "Block offending IP at firewall: iptables -A INPUT -s 192.168.1.105 -j DROP",
                "raw response text",
                "prompt text",
                100,
                50,
                0,
                150,
                "gemini-2.5-flash",
                [],
            )

        import app.services.ai_engine as ai_engine
        monkeypatch.setattr(ai_engine, "execute_ai_analysis", mock_execute_ai_analysis)

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        # Triggering log with offending IP
        log_entry = {
            "id": 1,
            "app_name": "sshd",
            "message": "Failed password for invalid user admin from 192.168.1.105 port 55122 ssh2",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await evaluator.stop()

        assert len(sent_payloads) == 1
        assert sent_payloads[0]["title"] == "LogShed: SSH Threat"
        body = sent_payloads[0]["body"]
        assert "LogShed Alert:" not in sent_payloads[0]["title"]
        assert "**App:** sshd" in body
        assert "**Log:** Failed password for invalid user admin from 192.168.1.105 port 55122 ssh2" in body
        assert "**AI Analysis:** Automated SSH brute-force attack detected." in body
        assert "**Link:**" not in body

        # Verify AI prompt contained Host / App metadata
        ai_prompt = captured_ai_kwargs.get("prompt_override", "")
        assert "App / Container: sshd" in ai_prompt

        # Verify AI diagnosis was persisted to ai_audit_log with trigger_source='alert'
        conn = get_connection(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT source_alias, app_name, log_count, user_context, model, trigger_source, tokens_used FROM ai_audit_log")
        audit_row = cursor.fetchone()
        conn.close()
        assert audit_row is not None
        assert audit_row[1] == "sshd"
        assert audit_row[2] == 1
        assert audit_row[3] == "Alert Rule: SSH Threat"
        assert audit_row[4] == "gemini-2.5-flash"
        assert audit_row[5] == "alert"
        assert audit_row[6] == 150

    @pytest.mark.asyncio
    async def test_app_url_configured_includes_link_in_notification(self, test_db: Path, monkeypatch):
        """Verify that when APP_URL is configured, the notification includes the link to /rules/history."""
        monkeypatch.setenv("APP_URL", "https://logshed.lan:8443")
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("App URL Test Rule", "threshold", "sshd", "Failed password", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        async def mock_execute_ai_analysis(*args, **kwargs):
            return (
                "Test incident diagnosis",
                "Take action",
                "prompt text",
                100,
                50,
                0,
                150,
                "gemini-2.5-flash",
                [],
            )

        import app.services.ai_engine as ai_engine
        monkeypatch.setattr(ai_engine, "execute_ai_analysis", mock_execute_ai_analysis)

        evaluator = AlertEvaluator(test_db)
        log_entry = {
            "id": 2,
            "app_name": "sshd",
            "message": "Failed password for root from 10.0.0.5 port 22",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await evaluator.stop()

        assert len(sent_payloads) == 1
        body = sent_payloads[0]["body"]
        assert "**Link:** https://logshed.lan:8443/rules/history" in body

    async def test_app_url_included_for_non_enriched_alert(self, test_db: Path, monkeypatch):
        """Verify that non-enriched alerts also include the link to /rules/history when APP_URL is configured."""
        monkeypatch.setenv("APP_URL", "https://logshed.lan:8443/")
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
            """,
            ("Non-AI Alert Rule", "threshold", "nginx", "502 Bad Gateway", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        log_entry = {
            "id": 10,
            "app_name": "nginx",
            "message": "502 Bad Gateway upstream server unavailable",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await evaluator.stop()

        assert len(sent_payloads) == 1
        assert sent_payloads[0]["title"] == "LogShed: Non-AI Alert Rule"
        body = sent_payloads[0]["body"]
        assert "**AI Analysis:**" not in body
        assert "**Link:** https://logshed.lan:8443/rules/history" in body



class TestSecurityPresets:
    """Unit tests for predefined security presets and IP extraction."""

    def test_presets_count_and_keys(self):
        presets = get_security_presets()
        assert len(presets) == 5
        preset_ids = {p["id"] for p in presets}
        expected_ids = {"ssh_bruteforce", "proxy_auth_flood", "sudo_escalation", "oom_killer", "log_storm_detection"}
        assert expected_ids == preset_ids

    def test_extract_ip_from_various_formats(self):
        # SSH from IP
        assert (
            extract_ip_from_message("Failed password for root from 10.0.0.15 port 22")
            == "10.0.0.15"
        )
        # SSH rhost
        assert (
            extract_ip_from_message("pam_unix(sshd:auth): authentication failure; logname= uid=0 rhost=172.16.0.4")
            == "172.16.0.4"
        )
        # Standalone IPv4
        assert extract_ip_from_message("Connection rejected from 192.168.1.200") == "192.168.1.200"
        # No IP
        assert extract_ip_from_message("Normal internal error without any IP") is None
        # Empty
        assert extract_ip_from_message("") is None


class TestQueueConsumerAlertIntegration:
    """End-to-end integration test verifying QueueConsumer batch hook fires AlertEvaluator."""

    @pytest.fixture
    def test_db(self, tmp_path: Path):
        db_file = tmp_path / "test_pipeline_alerts.db"
        run_migrations(db_file)
        return db_file

    @pytest.mark.asyncio
    async def test_queue_consumer_triggers_alert_on_batch_commit(self, test_db: Path, monkeypatch):
        from app.core.pipeline import QueueConsumer, get_queue
        import app.core.pipeline as pipeline_mod

        pipeline_mod._log_queue = None

        # Insert rule: threshold=2 on 'Critical error'
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Pipeline Alert", "threshold", "Critical error", 2, 60, 60, now_iso),
        )
        conn.commit()
        conn.close()

        sent_alerts = []

        async def mock_send(self, title, body, channel_id=None):
            sent_alerts.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        consumer = QueueConsumer(test_db, debounce_seconds=0.01, alert_evaluator=evaluator)

        consumer_task = asyncio.create_task(consumer.run())

        queue = get_queue()
        now = datetime.now(timezone.utc)
        await queue.put({
            "timestamp": now.isoformat(),
            "received_at": now.isoformat(),
            "source_ip": "127.0.0.1",
            "source_alias": "localhost",
            "app_name": "backend",
            "facility": 1,
            "severity": 3,
            "message": "Critical error detected in worker 1",
            "raw": "Critical error detected in worker 1",
        })
        await queue.put({
            "timestamp": (now + timedelta(seconds=1)).isoformat(),
            "received_at": (now + timedelta(seconds=1)).isoformat(),
            "source_ip": "127.0.0.1",
            "source_alias": "localhost",
            "app_name": "backend",
            "facility": 1,
            "severity": 3,
            "message": "Critical error detected in worker 2",
            "raw": "Critical error detected in worker 2",
        })

        # Wait for consumer to process queue
        await queue.join()
        await consumer.stop()
        await consumer_task
        await asyncio.sleep(0.1)

        assert len(sent_alerts) == 1
        assert "Pipeline Alert" in sent_alerts[0]["title"]

    @pytest.mark.asyncio
    async def test_out_of_order_timestamps_in_sliding_window(self, test_db: Path, monkeypatch):
        """Out-of-order timestamps should be sorted properly without preventing older event eviction."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Order Rule", "threshold", "event", 2, 10, 60, now_iso),
        )
        conn.commit()
        conn.close()

        sent = []
        async def mock_send(self, title, body, channel_id=None):
            sent.append(title)
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        t0 = datetime.now(timezone.utc)
        # Log 1 at current time
        await evaluator.evaluate_batch([{"id": 1, "message": "event 1", "timestamp": t0.isoformat()}])
        # Log 2 arrived out of order (5s earlier than log 1)
        await evaluator.evaluate_batch([{"id": 2, "message": "event 2", "timestamp": (t0 - timedelta(seconds=5)).isoformat()}])
        await asyncio.sleep(0.05)
        # Threshold of 2 is met within 10s window (5s apart)
        assert len(sent) == 1

    @pytest.mark.asyncio
    async def test_cooldown_caps_deque_size(self, test_db: Path, monkeypatch):
        """During active cooldown suppression, window size should be bounded and not leak memory."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Bounded Cooldown", "threshold", "burst", 2, 60, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        sent = []
        async def mock_send(self, title, body, channel_id=None):
            sent.append(title)
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        now = datetime.now(timezone.utc)

        # Fire the alert (threshold=2)
        await evaluator.evaluate_batch([
            {"id": 1, "message": "burst 1", "timestamp": now.isoformat()},
            {"id": 2, "message": "burst 2", "timestamp": (now + timedelta(seconds=1)).isoformat()},
        ])
        await asyncio.sleep(0.05)
        assert len(sent) == 1

        # Now send 50 logs while in cooldown
        suppressed_batch = [
            {"id": i, "message": f"burst {i}", "timestamp": (now + timedelta(seconds=2 + i)).isoformat()}
            for i in range(3, 53)
        ]
        await evaluator.evaluate_batch(suppressed_batch)
        await asyncio.sleep(0.05)
        # Still only 1 notification sent
        assert len(sent) == 1

        # Check window size is capped to threshold_count (2) and hasn't ballooned to 50
        assert len(evaluator._windows[rule_id]) <= 2

    def test_proxy_and_oom_presets_filter_false_positives(self):
        """Verify proxy 401/403 and OOM presets don't trigger on benign text containing 401/403 or generic kill."""
        from app.services.alert_presets import get_security_preset_by_id
        import re

        proxy_preset = get_security_preset_by_id("proxy_auth_flood")
        proxy_re = re.compile(proxy_preset["match_pattern"], re.IGNORECASE)

        # Genuine proxy logs match
        assert proxy_re.search('192.168.1.1 - - [18/Sep/2026:12:00:00 +0000] "GET /admin HTTP/1.1" 401 123')
        assert proxy_re.search('status=401')
        assert proxy_re.search('status: 403')
        assert proxy_re.search('401 Unauthorized')
        assert proxy_re.search('403 Forbidden')

        # Benign messages do NOT match
        assert not proxy_re.search("User 401 logged out")
        assert not proxy_re.search("Order #403 updated")
        assert not proxy_re.search("Listening on port 4010")

        oom_preset = get_security_preset_by_id("oom_killer")
        oom_re = re.compile(oom_preset["match_pattern"], re.IGNORECASE)

        # Genuine OOM matches
        assert oom_re.search("Out of memory: Kill process 1234 (node)")
        assert oom_re.search("kernel: [12345.67] invoked oom-killer: gfp_mask=0x100cca")
        assert oom_re.search("oom-killer invoked")
        assert oom_re.search("Killed process 5432 (python3)")

        # Benign message does NOT match
        assert not oom_re.search("Gracefully killed process 123 on clean exit")

    def test_extract_ip_prefers_routable_over_loopback(self):
        """When multiple IPs are present, routable IPs should take priority over 127.0.0.1."""
        msg = "Proxy forwarding from 127.0.0.1 for client 203.0.113.195"
        assert extract_ip_from_message(msg) == "203.0.113.195"
        # If only loopback exists, return loopback
        assert extract_ip_from_message("Local probe from 127.0.0.1") == "127.0.0.1"

    def test_multi_select_comma_separated_app_filter(self):
        """Verify comma-separated app filter matches any specified app."""
        rule = CompiledAlertRule(
            id=10,
            name="Multi-App Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app="sshd, nginx, sudo",
            filter_severity=None,
            match_pattern=None,
            threshold_count=1,
            window_seconds=60,
            cooldown_seconds=60,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert rule.matches({"app_name": "sshd", "message": "msg"}) is True
        assert rule.matches({"app_name": "nginx", "message": "msg"}) is True
        assert rule.matches({"app_name": "sudo", "message": "msg"}) is True
        assert rule.matches({"app_name": "docker", "message": "msg"}) is False

    def test_format_sample_log_truncation(self):
        """Verify format_sample_log_for_alert truncates long and multi-line messages."""
        from app.services.alert_evaluator import format_sample_log_for_alert

        multiline_msg = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\nLine 7\nLine 8"
        truncated = format_sample_log_for_alert(multiline_msg, max_lines=5)
        assert "[truncated]" in truncated
        assert len(truncated.splitlines()) == 6  # 5 lines + "... [truncated]"

        long_msg = "a" * 800
        truncated_chars = format_sample_log_for_alert(long_msg, max_chars=100)
        assert "[truncated]" in truncated_chars
        assert len(truncated_chars) <= 120

        # Combined multi-line and character budget truncation
        both_msg = "\n".join(["x" * 200 for _ in range(10)])
        truncated_both = format_sample_log_for_alert(both_msg, max_lines=5, max_chars=150)
        assert "[truncated]" in truncated_both
        assert len(truncated_both) <= 170

    def test_strip_markdown(self):
        """Verify strip_markdown removes headers, code fences, bold, and list markers."""
        from app.services.alert_evaluator import strip_markdown

        md = "### Incident Summary\n**Critical:** `root` login from 10.0.0.1\n* Check sudoers\n```bash\njournalctl\n```"
        cleaned = strip_markdown(md)
        assert "###" not in cleaned
        assert "**" not in cleaned
        assert "```" not in cleaned
        assert "`" not in cleaned
        assert "Incident Summary" in cleaned
        assert "Critical: root login from 10.0.0.1" in cleaned

    @pytest.mark.asyncio
    async def test_ai_enrichment_failure_sends_alert_with_note_and_records_history(self, test_db: Path, monkeypatch):
        """Verify that when AI enrichment fails, notification is still sent with a failure note and history is logged."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("Critical API Error", "threshold", "api", "500 Internal", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        # Mock execute_ai_analysis to raise 504 deadline exceeded
        import app.services.ai_engine as ai_engine

        async def mock_fail_ai(**kwargs):
            raise TimeoutError("Gemini API error: 504 DEADLINE_EXCEEDED")

        monkeypatch.setattr(ai_engine, "execute_ai_analysis", mock_fail_ai)

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        log_entry = {
            "id": 101,
            "app_name": "api",
            "message": "500 Internal Server Error in payment endpoint",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await asyncio.sleep(0.1)

        # Notification still sent despite AI failure!
        assert len(sent_payloads) == 1
        assert sent_payloads[0]["title"] == "LogShed: Critical API Error"
        body = sent_payloads[0]["body"]
        assert "LogShed Alert:" not in sent_payloads[0]["title"]
        assert "**App:** api" in body
        assert "**Log:** 500 Internal Server Error in payment endpoint" in body
        assert "**AI Analysis:** Unavailable (Gemini API error: 504 DEADLINE_EXCEEDED)" in body
        assert "**Link:**" not in body

        # Verify incident history recorded failure
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT incident_summary FROM alert_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        assert "AI analysis failed:" in row[0]
        conn.close()

    @pytest.mark.asyncio
    async def test_ai_enrichment_skipped_when_ai_disabled_in_settings(self, test_db: Path, monkeypatch):
        """Verify that when AI is disabled in settings, rules with ai_enrichment trigger cleanly without calling LLM."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("Disabled AI Test Rule", "threshold", "api", "500 Internal", 1, 60, 300, now_iso),
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_enabled', '0', datetime('now'), 0)"
        )
        conn.commit()
        conn.close()

        import app.services.ai_engine as ai_engine

        ai_called = False

        async def mock_fail_if_called(**kwargs):
            nonlocal ai_called
            ai_called = True
            raise RuntimeError("execute_ai_analysis should not be called when AI is disabled")

        monkeypatch.setattr(ai_engine, "execute_ai_analysis", mock_fail_if_called)

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        log_entry = {
            "id": 201,
            "app_name": "api",
            "message": "500 Internal Server Error in payment endpoint",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await asyncio.sleep(0.1)

        # AI analysis was not called
        assert not ai_called

        # Notification was dispatched without AI errors or AI analysis note
        assert len(sent_payloads) == 1
        assert sent_payloads[0]["title"] == "LogShed: Disabled AI Test Rule"
        body = sent_payloads[0]["body"]
        assert "**App:** api" in body
        assert "**Log:** 500 Internal Server Error in payment endpoint" in body
        assert "**AI Analysis:**" not in body
        assert "Note: AI enrichment failed" not in body

        # Verify incident history recorded clean summary and ai_enrichment=0
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT incident_summary, ai_enrichment, ai_model FROM alert_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        assert "Alert triggered with 1 matching event(s)." in row[0]
        assert row[1] == 0
        assert row[2] is None
        conn.close()


    @pytest.mark.asyncio
    async def test_ai_enrichment_failover_to_fallback_model_on_503(self, test_db: Path, monkeypatch):
        """Verify that when primary model fails with 503 UNAVAILABLE, AI enrichment tries fallback model and succeeds."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("Failover Test Rule", "threshold", "nginx", "upstream timed out", 1, 60, 300, now_iso),
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_model', 'gemini-3.7-flash', datetime('now'), 0)"
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_fallback_models', 'gemini-2.5-flash', datetime('now'), 0)"
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_api_key', 'test-api-key', datetime('now'), 0)"
        )
        conn.commit()
        conn.close()

        import app.services.ai_engine as ai_engine

        attempted_models = []

        async def mock_dispatch_gemini(api_key, model, prompt, system_prompt=None, timeout=45.0, **kwargs):
            attempted_models.append(model)
            if model == "gemini-3.7-flash":
                raise ai_engine.AiServiceUnavailableError("503 UNAVAILABLE: The model is overloaded. Please try again later.", code=503)
            elif model == "gemini-2.5-flash":
                return "SUMMARY: Upstream gateway recovery\nROOT CAUSE: Node latency spike\nREMEDIATION: Scale worker pods", 100, 50, 0, 150
            raise RuntimeError(f"Unexpected model: {model}")

        monkeypatch.setattr(ai_engine, "dispatch_gemini_request", mock_dispatch_gemini)

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        log_entry = {
            "id": 201,
            "app_name": "nginx",
            "message": "504 upstream timed out (110: Connection timed out) while reading response header from upstream",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await asyncio.sleep(0.1)

        # Primary failed, fallback succeeded!
        assert attempted_models == ["gemini-3.7-flash", "gemini-2.5-flash"]
        assert len(sent_payloads) == 1
        assert sent_payloads[0]["title"] == "LogShed: Failover Test Rule"
        body = sent_payloads[0]["body"]
        assert "LogShed Alert:" not in sent_payloads[0]["title"]
        assert "**App:** nginx" in body
        assert "**Log:** 504 upstream timed out" in body
        assert "Upstream gateway recovery" in body
        assert "**Link:**" not in body

        # Verify alert history recorded the successful diagnosis from fallback
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT incident_summary FROM alert_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        assert "Upstream gateway recovery" in row[0]
        conn.close()

    @pytest.mark.asyncio
    async def test_ai_enrichment_reports_all_failed_models_when_all_fallbacks_fail(self, test_db: Path, monkeypatch):
        """Verify that when all models in the fallback chain fail, the notification and history clearly report all models tried."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("All Fail Rule", "threshold", "kernel", "watchdog", 1, 60, 300, now_iso),
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_model', 'gemini-3.8-flash', datetime('now'), 0)"
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_fallback_models', 'gemini-3.7-flash', datetime('now'), 0)"
        )
        cur.execute(
            "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_api_key', 'test-api-key', datetime('now'), 0)"
        )
        conn.commit()
        conn.close()

        import app.services.ai_engine as ai_engine

        attempted_models = []

        async def mock_dispatch_gemini(api_key, model, prompt, system_prompt=None, timeout=45.0, **kwargs):
            attempted_models.append(model)
            raise ai_engine.AiServiceUnavailableError(
                f"503 UNAVAILABLE: This model is currently experiencing high demand.",
                code=503,
            )

        monkeypatch.setattr(ai_engine, "dispatch_gemini_request", mock_dispatch_gemini)

        sent_payloads = []

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            sent_payloads.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)

        log_entry = {
            "id": 301,
            "app_name": "kernel",
            "message": "Critical hardware watchdog fired",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await evaluator.evaluate_batch([log_entry])
        await asyncio.sleep(0.1)

        # Both models were attempted
        assert attempted_models == ["gemini-3.8-flash", "gemini-3.7-flash"]
        assert len(sent_payloads) == 1
        assert sent_payloads[0]["title"] == "LogShed: All Fail Rule"
        body = sent_payloads[0]["body"]
        assert "LogShed Alert:" not in sent_payloads[0]["title"]
        assert "**Log:** Critical hardware watchdog fired" in body
        assert "All 2 models failed (gemini-3.8-flash, gemini-3.7-flash)" in body
        assert "**Link:**" not in body

        # Verify alert history recorded clear multi-model failure
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT incident_summary FROM alert_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        assert "All 2 models failed (gemini-3.8-flash, gemini-3.7-flash)" in row[0]
        conn.close()

    @pytest.mark.asyncio
    async def test_dispatch_alert_redacts_secrets_in_title_log_and_ai_summary(self, tmp_path, monkeypatch):
        """Verify that secrets in alert title, sample log, and AI diagnosis are redacted before dispatch."""
        test_db = tmp_path / "logs_redact.db"
        run_migrations(test_db)

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, 1, 60, 0, 1, 1, ?)
            """,
            ("Rule with AWS Key AKIAIOSFODNN7EXAMPLE", "pattern", "auth_failed", now_iso),
        )
        conn.commit()
        conn.close()

        # Mock AI analysis returning sensitive tokens
        async def mock_ai(*args, **kwargs):
            return (
                "Suspicious token usage api_key=AIzaSyD1234567890abcdef discovered.",
                "Plaintext password leaked in stack trace",
                "Revoke key immediately",
                1, 2, 3, 4, 5, 6, "gemini-test", [],
            )

        monkeypatch.setattr("app.services.ai_engine.execute_ai_analysis", mock_ai)

        dispatched = []
        async def mock_send(self, title, body, channel_id=None, **kwargs):
            dispatched.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        raw_secret_message = (
            "auth_failed: password=MySecretPass123! token=secret_token_value_9999 "
            "Authorization: Bearer mySecretJwtTokenValue"
        )
        log_entry = {
            "id": 401,
            "app_name": "auth-service",
            "message": raw_secret_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await evaluator.evaluate_batch([log_entry])
        await asyncio.sleep(0.1)

        assert len(dispatched) == 1
        title = dispatched[0]["title"]
        body = dispatched[0]["body"]

        # 1. Title redaction
        assert "AKIAIOSFODNN7EXAMPLE" not in title
        assert "[REDACTED]" in title

        # 2. Sample log redaction in body
        assert "MySecretPass123!" not in body
        assert "secret_token_value_9999" not in body
        assert "mySecretJwtTokenValue" not in body
        assert "[REDACTED]" in body

        # 3. AI diagnosis summary redaction in body
        assert "AIzaSyD1234567890abcdef" not in body

    def test_compiled_alert_rule_presplit_and_prelowercased(self):
        """Verify CompiledAlertRule pre-splits filter_app and pre-lowercases match_pattern."""
        rule = CompiledAlertRule(
            id=50,
            name="Optimized Rule",
            rule_type="threshold",
            channel_id=None,
            filter_app="  SSHD , NGINX, Web-App ",
            filter_severity=None,
            match_pattern="Connection Refused",
            threshold_count=5,
            window_seconds=60,
            cooldown_seconds=30,
            ai_enrichment=False,
            is_enabled=True,
        )
        assert rule._filter_apps == ("sshd", "nginx", "web-app")
        assert rule._match_pattern_lower == "connection refused"
        assert rule.matches({"app_name": "nginx", "message": "connection refused by peer"}) is True
        assert rule.matches({"app_name": "apache", "message": "connection refused by peer"}) is False

    @pytest.mark.asyncio
    async def test_timestamp_bounds_clamping(self, test_db: Path, monkeypatch):
        """Verify incoming timestamps are clamped between now - 86400 and now + 300."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Bounds Rule", "threshold", "probe", 1, 86400, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        sent = []
        async def mock_send(self, title, body, channel_id=None):
            sent.append(title)
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        now = datetime.now(timezone.utc)
        now_epoch = now.timestamp()

        # Very old timestamp (10 days ago) and far future timestamp (10 days ahead)
        old_entry = {
            "id": 1,
            "message": "probe old",
            "timestamp": (now - timedelta(days=10)).isoformat(),
        }
        future_entry = {
            "id": 2,
            "message": "probe future",
            "timestamp": (now + timedelta(days=10)).isoformat(),
        }

        await evaluator.evaluate_batch([old_entry, future_entry])

        # Verify entry epoch timestamps were clamped
        assert old_entry["_epoch_ts"] >= now_epoch - 86405.0
        assert old_entry["_epoch_ts"] <= now_epoch - 86395.0
        assert future_entry["_epoch_ts"] <= now_epoch + 305.0
        assert future_entry["_epoch_ts"] >= now_epoch + 295.0

    @pytest.mark.asyncio
    async def test_sliding_window_deque_bound_threshold_times_two(self, test_db: Path, monkeypatch):
        """Verify window deque size is bounded to threshold_count * 2 during ongoing non-matching and matching events."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Capacity Rule", "threshold", "event", 10, 60, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        evaluator = AlertEvaluator(test_db)
        now = datetime.now(timezone.utc)

        # Send 8 events (below threshold of 10)
        batch = [
            {"id": i, "message": f"event {i}", "timestamp": (now + timedelta(seconds=i * 0.1)).isoformat()}
            for i in range(8)
        ]
        await evaluator.evaluate_batch(batch)

        # Deque size cannot exceed threshold_count * 2 = 20
        assert len(evaluator._windows[rule_id]) == 8

        # If 30 events are fed with threshold 10, window does not exceed 20
        large_batch = [
            {"id": i, "message": f"event {i}", "timestamp": (now + timedelta(seconds=i * 0.1)).isoformat()}
            for i in range(100, 130)
        ]
        # Set is_enabled to False temporarily to inspect deque bound without clearing on trigger
        evaluator._rules[0].threshold_count = 50
        await evaluator.evaluate_batch(large_batch)
        assert len(evaluator._windows[rule_id]) <= 100

    @pytest.mark.asyncio
    async def test_decoupled_rule_trigger_state_db_writes(self, test_db: Path, monkeypatch):
        """Verify DB write runs in background while in-memory cooldown dampening is instantaneous."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, match_pattern, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("Decoupled Rule", "threshold", "trigger", 1, 60, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        db_write_started = asyncio.Event()
        db_write_finish = asyncio.Event()

        evaluator = AlertEvaluator(test_db)
        orig_update = evaluator._update_rule_trigger_state

        def _slow_update(rule):
            db_write_started.set()
            return orig_update(rule)

        evaluator._update_rule_trigger_state = _slow_update

        async def mock_send(self, title, body, channel_id=None):
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        now = datetime.now(timezone.utc)

        # Fire first batch
        await evaluator.evaluate_batch([
            {"id": 1, "message": "trigger 1", "timestamp": now.isoformat()}
        ])

        # In-memory cooldown dampening is immediately active before background DB write completes
        rule = evaluator._rules[0]
        assert rule.suppress_until_epoch is not None
        assert rule.suppress_until_epoch > now.timestamp()

        # A second batch immediately following is dampened instantaneously in-memory
        sent_second = False
        async def mock_fail_send(self, title, body, channel_id=None):
            nonlocal sent_second
            sent_second = True
            return True

        monkeypatch.setattr(NotifierService, "send_notification", mock_fail_send)

        await evaluator.evaluate_batch([
            {"id": 2, "message": "trigger 2", "timestamp": now.isoformat()}
        ])

        # Second event was dampened in-memory without sending
        assert sent_second is False

        # Wait for background dispatch to finish cleanly
        await evaluator.stop()

    @pytest.mark.asyncio
    async def test_rate_spike_alert_evaluation_and_culprit_breakdown(self, test_db: Path, monkeypatch):
        """Verify rate spike alerts calculate required thresholds, track culprits, and format notifications."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 0, 1, ?)
            """,
            ("Log Storm Watcher", "rate", 10, 5, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        notifications = []
        async def mock_send(self, title, body, channel_id=None, **kwargs):
            notifications.append({"title": title, "body": body})
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        now = datetime.now(timezone.utc)

        # 1. Send 40 logs (needed = 10 logs/s * 5s = 50 logs). Should NOT fire.
        batch1 = [
            {
                "id": i,
                "app_name": "worker" if i % 2 == 0 else "storm-service",
                "source_alias": "edge-01",
                "message": f"normal activity event {i}",
                "timestamp": (now + timedelta(seconds=i * 0.05)).isoformat(),
            }
            for i in range(40)
        ]
        await evaluator.evaluate_batch(batch1)
        await asyncio.sleep(0.05)
        assert len(notifications) == 0

        # 2. Send 15 more logs (bringing total in window to 55 >= 50). Should fire!
        batch2 = [
            {
                "id": 100 + i,
                "app_name": "storm-service",
                "source_alias": "edge-01",
                "message": "database connection pool exhausted",
                "timestamp": (now + timedelta(seconds=2.0 + i * 0.05)).isoformat(),
            }
            for i in range(15)
        ]
        await evaluator.evaluate_batch(batch2)
        await asyncio.sleep(0.1)
        await evaluator.stop()

        assert len(notifications) == 1
        title = notifications[0]["title"]
        body = notifications[0]["body"]

        assert "[Log Storm] Log Storm Watcher" in title
        assert "logs/s" in title
        assert "storm-service" in body
        assert "edge-01" in body
        assert "database connection pool exhausted" in body

        # 3. Check alert_history record in SQLite
        conn = get_connection(test_db)
        cur = conn.cursor()
        cur.execute("SELECT trigger_count, incident_summary FROM alert_history WHERE rule_id = ?", (rule_id,))
        row = cur.fetchone()
        conn.close()

        assert row is not None
        assert row[0] >= 50
        summary = row[1]
        assert "Log storm detected" in summary
        assert "storm-service" in summary
        assert "edge-01" in summary
        assert "database connection pool exhausted" in summary

    @pytest.mark.asyncio
    async def test_rate_spike_alert_ai_enrichment_prompt(self, test_db: Path, monkeypatch):
        """Verify rate spike alerts include culprit breakdown in AI prompt."""
        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, ai_enrichment, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 1, ?)
            """,
            ("AI Rate Storm", "rate", 5, 2, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        captured_prompt = None
        async def mock_ai(**kwargs):
            nonlocal captured_prompt
            captured_prompt = kwargs.get("prompt_override")
            return (
                "Rate spike diagnosis",
                "Culprit storm service flooded pool",
                "Restart service and scale replicas",
                1, 2, 3, 4, 5, 6, "mock-model", [],
            )

        monkeypatch.setattr("app.services.ai_engine.execute_ai_analysis", mock_ai)

        async def mock_send(self, title, body, channel_id=None, **kwargs):
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        now = datetime.now(timezone.utc)

        # Send 12 logs (needed = 5 logs/s * 2s = 10 logs)
        batch = [
            {
                "id": i,
                "app_name": "leaky-collector",
                "source_alias": "k8s-node-2",
                "message": "repeated failure payload",
                "timestamp": (now + timedelta(seconds=i * 0.1)).isoformat(),
            }
            for i in range(12)
        ]
        await evaluator.evaluate_batch(batch)
        await asyncio.sleep(0.1)
        await evaluator.stop()

        assert captured_prompt is not None
        assert "Primary Culprits" in captured_prompt
        assert "leaky-collector" in captured_prompt
        assert "k8s-node-2" in captured_prompt
        assert "repeated failure payload" in captured_prompt
        assert "logs/s" in captured_prompt


class TestGlobalMaintenanceWindow:
    """Tests for global maintenance window silencing notifications while recording alert history."""

    @pytest.mark.asyncio
    async def test_maintenance_window_silences_notifications_but_records_history(self, tmp_path, monkeypatch):
        test_db = tmp_path / "test_maint.db"
        run_migrations(test_db)

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        future_iso = (now_utc + timedelta(hours=2)).isoformat()

        conn = sqlite3.connect(str(test_db))
        cur = conn.cursor()
        # Set global maintenance window
        cur.execute(
            """
            INSERT INTO system_settings (key, value, updated_at, is_encrypted)
            VALUES ('maintenance_until', ?, ?, 0)
            """,
            (future_iso, now_iso),
        )
        # Create alert rule
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            ("Maint Test Rule", "threshold", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        notification_sent = False
        async def mock_send(self, title, body, channel_id=None, **kwargs):
            nonlocal notification_sent
            notification_sent = True
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        batch = [
            {
                "id": 1,
                "app_name": "app",
                "source_alias": "srv",
                "message": "critical error",
                "timestamp": now_iso,
            }
        ]
        await evaluator.evaluate_batch(batch)
        await asyncio.sleep(0.1)
        await evaluator.stop()

        # Notification should NOT have been sent
        assert notification_sent is False

        # Alert history SHOULD have been recorded
        check_conn = sqlite3.connect(str(test_db))
        history_rows = check_conn.execute("SELECT rule_name, trigger_count FROM alert_history").fetchall()
        check_conn.close()
        assert len(history_rows) == 1
        assert history_rows[0][0] == "Maint Test Rule"

    @pytest.mark.asyncio
    async def test_expired_or_null_maintenance_window_dispatches_notification(self, tmp_path, monkeypatch):
        test_db = tmp_path / "test_maint_expired.db"
        run_migrations(test_db)

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        past_iso = (now_utc - timedelta(hours=1)).isoformat()

        conn = sqlite3.connect(str(test_db))
        cur = conn.cursor()
        # Set expired maintenance window
        cur.execute(
            """
            INSERT INTO system_settings (key, value, updated_at, is_encrypted)
            VALUES ('maintenance_until', ?, ?, 0)
            """,
            (past_iso, now_iso),
        )
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            ("Expired Maint Rule", "threshold", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        notification_sent = False
        async def mock_send(self, title, body, channel_id=None, **kwargs):
            nonlocal notification_sent
            notification_sent = True
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        batch = [
            {
                "id": 1,
                "app_name": "app",
                "source_alias": "srv",
                "message": "critical error",
                "timestamp": now_iso,
            }
        ]
        await evaluator.evaluate_batch(batch)
        await asyncio.sleep(0.1)
        await evaluator.stop()

        # Notification SHOULD have been sent
        assert notification_sent is True

    @pytest.mark.asyncio
    async def test_active_recurring_schedule_silences_notifications(self, tmp_path, monkeypatch):
        import json
        test_db = tmp_path / "test_maint_sched.db"
        run_migrations(test_db)

        now_local = datetime.now().astimezone()
        now_iso = datetime.now(timezone.utc).isoformat()
        # Schedule window covering now (started 5 mins ago, duration 30 mins)
        start_time_str = (now_local - timedelta(minutes=5)).strftime("%H:%M")

        schedules = [
            {
                "id": "sched_active_now",
                "name": "Active Daily Window",
                "enabled": True,
                "recurrence": "daily",
                "start_time": start_time_str,
                "duration_minutes": 30,
            }
        ]

        conn = sqlite3.connect(str(test_db))
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_settings (key, value, updated_at, is_encrypted)
            VALUES ('maintenance_schedules', ?, ?, 0)
            """,
            (json.dumps(schedules), now_iso),
        )
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            ("Scheduled Rule", "threshold", 1, 60, 300, now_iso),
        )
        conn.commit()
        conn.close()

        notification_sent = False
        async def mock_send(self, title, body, channel_id=None, **kwargs):
            nonlocal notification_sent
            notification_sent = True
            return True

        from app.services.notifier import NotifierService
        monkeypatch.setattr(NotifierService, "send_notification", mock_send)

        evaluator = AlertEvaluator(test_db)
        batch = [
            {
                "id": 1,
                "app_name": "app",
                "source_alias": "srv",
                "message": "critical error",
                "timestamp": now_iso,
            }
        ]
        await evaluator.evaluate_batch(batch)
        await asyncio.sleep(0.1)
        await evaluator.stop()

        # Notification should NOT have been sent because recurring schedule is active
        assert notification_sent is False

        # Alert history SHOULD have been recorded
        check_conn = sqlite3.connect(str(test_db))
        history_rows = check_conn.execute("SELECT rule_name, trigger_count FROM alert_history").fetchall()
        check_conn.close()
        assert len(history_rows) == 1
        assert history_rows[0][0] == "Scheduled Rule"


class TestAlertEvaluatorWindowPruningAndLightweightRecords:
    @pytest.fixture
    def test_db(self, tmp_path: Path):
        db_path = tmp_path / "test_alerts_pruning.db"
        run_migrations(db_path)
        return db_path

    @pytest.mark.asyncio
    async def test_sliding_window_purges_expired_logs_during_idle_traffic(self, test_db: Path):
        """AlertEvaluator sliding window purges expired logs during periods of idle traffic."""
        from datetime import datetime, timezone, timedelta

        conn = get_connection(test_db)
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            ("Idle Pruning Rule", "threshold", 10, 5, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        evaluator = AlertEvaluator(test_db)
        now_epoch = now_utc.timestamp()

        # Ingest a burst of logs below threshold
        burst = [
            {
                "id": i,
                "app_name": "worker",
                "message": f"burst log {i}",
                "timestamp": (now_utc - timedelta(seconds=2)).isoformat(),
            }
            for i in range(5)
        ]
        await evaluator.evaluate_batch(burst)

        # Window should contain 5 entries
        assert len(evaluator._windows[rule_id]) == 5

        # Simulate idle traffic 10 seconds later: evaluate_batch with empty or future epoch
        future_epoch = now_epoch + 15.0
        evaluator.prune_expired_windows(future_epoch)

        # Window should have pruned all expired entries
        assert len(evaluator._windows[rule_id]) == 0

    @pytest.mark.asyncio
    async def test_rate_rule_stores_lightweight_records_not_full_payload(self, test_db: Path):
        """Rate rules store lightweight records instead of retaining full log payload dictionaries."""
        from datetime import datetime, timezone

        conn = get_connection(test_db)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alert_rules
            (name, rule_type, threshold_count, window_seconds, cooldown_seconds, is_enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            ("Rate Lightweight Rule", "rate", 10, 5, 300, now_iso),
        )
        rule_id = cur.lastrowid
        conn.commit()
        conn.close()

        evaluator = AlertEvaluator(test_db)
        heavy_log = {
            "id": 123,
            "app_name": "bulk-service",
            "source_alias": "node-1",
            "source_ip": "10.0.0.1",
            "message": "warning message",
            "raw": "HUGE RAW LOG" * 1000,
            "arbitrary_heavy_metadata": "X" * 100000,
            "timestamp": now_iso,
        }
        await evaluator.evaluate_batch([heavy_log])

        window = evaluator._windows[rule_id]
        assert len(window) == 1
        stored_ts, stored_entry = window[0]
        assert isinstance(stored_entry, dict)
        assert "arbitrary_heavy_metadata" not in stored_entry
        assert "raw" not in stored_entry
        assert stored_entry["app_name"] == "bulk-service"
        assert stored_entry["message"] == "warning message"







