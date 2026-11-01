"""
Drop and alert rules are only checked against entries they could match (by exact app or source).
These tests check that the results are exactly those of checking every rule against every entry:
the same winning drop rule and counts, the same alert matches in the same order, and that rules
for other apps are never evaluated.
"""

import asyncio
import datetime
import itertools
from collections import Counter
from pathlib import Path

import pytest

from app.core.migrations import get_connection, run_migrations
from app.core.rule_index import RuleIndex, entry_key, exact_match_key
from app.services.alert_evaluator import AlertEvaluator, CompiledAlertRule
from app.services.drop_filter import CompiledDropRule, DropFilter

NOW_ISO = datetime.datetime.now(datetime.timezone.utc).isoformat()

# (source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold), in display order
DROP_RULES = [
    (None, "nginx", "health", False, 1, None),
    (None, "NGINX", "*GET*", False, 1, None),          # case variant of an exact app
    (None, " sshd ", r"session \w+ for", True, 1, None),  # padded exact app, regex
    (None, "ngin*", "timeout", False, 1, None),        # wildcard app: always checked
    ("router", None, "DHCP", False, 1, None),          # exact source alias, no app
    ("192.168.1.9", None, "*", False, 1, 4),           # exact source IP with a severity threshold
    ("rout?r", "kernel", "eth0", False, 1, None),      # exact app wins the index over a wildcard source
    ("nas-*", None, "scrub", False, 1, None),          # wildcard source: always checked
    (None, None, r"(?i)panic: \d+", True, 1, None),    # no app or source: always checked
    (None, "dnsmasq", "DHCP", False, 0, None),         # disabled: never loaded
    (None, "dnsmasq", "query", False, 1, None),
    ("ROUTER", "dnsmasq", "*", False, 1, None),        # exact app and source
]

APPS = ["nginx", "Nginx", " NGINX ", "sshd", "kernel", "dnsmasq", "nginx-proxy", "postgres", "", None]
SOURCES = [("router", "192.168.1.1"), ("Router", "192.168.1.9"), ("nas-01", "10.0.0.5"), (None, "192.168.1.9"), (None, None)]
MESSAGES = [
    "GET /health 200",
    "upstream timeout while reading",
    "session opened for user root",
    "DHCPACK on eth0",
    "query[A] example.com",
    "pool scrub completed",
    "PANIC: 42 kernel fault",
    "link eth0 up",
    "nothing to see",
]


def _drop_filter(tmp_path: Path) -> DropFilter:
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    conn = get_connection(db_file)
    for order, (src, app, msg, is_regex, enabled, sev) in enumerate(DROP_RULES, start=1):
        conn.execute(
            "INSERT INTO drop_rules (name, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, "
            "severity_threshold, dropped_count, display_order, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (f"Rule {order}", src, app, msg, int(is_regex), enabled, sev, order, NOW_ISO),
        )
    conn.commit()
    conn.close()
    return DropFilter(db_file)


def _drop_cases():
    for app, (alias, ip), message, severity in itertools.product(APPS, SOURCES, MESSAGES, (2, 6)):
        yield alias, ip, app, message, severity


class TestDropRuleCandidates:
    def test_same_winning_rule_and_counts_as_a_full_scan(self, tmp_path: Path):
        flt = _drop_filter(tmp_path)
        assert len(flt._rules) == len(DROP_RULES) - 1

        expected_counts: Counter[int] = Counter()
        cases = list(_drop_cases())
        for alias, ip, app, message, severity in cases:
            expected = next(
                (rule.id for rule in flt._rules if rule.matches(alias, ip, app, message, severity)),
                None,
            )
            assert flt.should_drop(alias, ip, app, message, severity) == expected, (alias, ip, app, message, severity)
            if expected is not None:
                expected_counts[expected] += 1

        assert flt.get_all_pending_counts() == dict(expected_counts)
        # The case set reaches most rules, including first-match wins between rules for the same app
        assert len(expected_counts) >= 9

    def test_rules_for_other_apps_and_sources_are_never_evaluated(self, tmp_path: Path, monkeypatch):
        flt = _drop_filter(tmp_path)
        names = {rule.id: (rule.app_pattern, rule.source_pattern) for rule in flt._rules}
        evaluated: list[int] = []
        original = CompiledDropRule.matches

        def counting_matches(self, *args, **kwargs):
            evaluated.append(self.id)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(CompiledDropRule, "matches", counting_matches)

        assert flt.should_drop("web-01", "10.0.0.7", "postgres", "nothing to see", 6) is None
        # Only the rules with a wildcard or no app or source filter: ngin*, nas-*, the bare regex
        assert [names[i] for i in evaluated] == [("ngin*", None), (None, "nas-*"), (None, None)]

        evaluated.clear()
        flt.should_drop("router", "10.0.0.7", "Nginx", "nothing to see", 6)
        assert [names[i] for i in evaluated] == [
            ("nginx", None),
            ("NGINX", None),
            ("ngin*", None),
            (None, "router"),
            (None, "nas-*"),
            (None, None),
        ]

    def test_rule_disabled_after_timeouts_leaves_the_lookup(self, tmp_path: Path):
        flt = _drop_filter(tmp_path)
        health_rule = flt._rules[0]
        assert flt.should_drop(None, None, "nginx", "GET /health 200") == health_rule.id

        for _ in range(100):
            flt._note_pattern_timeout(health_rule)
        assert health_rule.id not in [r.id for r in flt._rules]
        assert flt.should_drop(None, None, "nginx", "GET /health 200") == flt._rules[0].id  # the NGINX *GET* rule


# (filter_app, match_pattern), in display order; thresholds out of reach so every match stays in the window
ALERT_RULES = [
    ("nginx", "error"),
    ("NGINX, sshd", "fail"),            # several exact apps, case variant
    ("ngin*", "error"),                 # wildcard: always checked
    (None, "panic"),                    # no app filter: always checked
    ("postgres", r"FATAL:\s+\w+"),      # regex
    ("sshd,ngin?", "accepted"),         # one wildcard makes the whole rule always checked
    ("haproxy", "DOWN"),                # an app that never sends
]

ALERT_APPS = ["nginx", "Nginx ", "sshd", "postgres", "nginx-proxy", "ngin1", "", None]
ALERT_MESSAGES = ["error 1", "login fail", "kernel panic", "FATAL: role missing", "accepted key", "fine"]


def _alert_evaluator(tmp_path: Path) -> AlertEvaluator:
    db_file = tmp_path / "logs.db"
    run_migrations(db_file)
    conn = get_connection(db_file)
    for order, (apps, pattern) in enumerate(ALERT_RULES, start=1):
        conn.execute(
            "INSERT INTO alert_rules (name, rule_type, filter_app, match_pattern, threshold_count, window_seconds, "
            "cooldown_seconds, is_enabled, display_order, created_at) VALUES (?, 'threshold', ?, ?, 100000, 600, 300, 1, ?, ?)",
            (f"Alert {order}", apps, pattern, order, NOW_ISO),
        )
    conn.commit()
    conn.close()
    return AlertEvaluator(db_file)


def _alert_batch() -> list[dict]:
    batch = []
    for i, (app, message) in enumerate(itertools.product(ALERT_APPS, ALERT_MESSAGES), start=1):
        batch.append({"id": i, "app_name": app, "message": message, "raw": message, "timestamp": NOW_ISO})
    return batch


class TestAlertRuleCandidates:
    @pytest.mark.asyncio
    async def test_windows_hold_the_same_entries_as_a_full_scan(self, tmp_path: Path):
        ev = _alert_evaluator(tmp_path)
        batch = _alert_batch()
        expected = {rule.id: [e["id"] for e in batch if rule.matches(e)] for rule in ev._rules}

        await ev.evaluate_batch(batch)

        actual = {rule.id: [payload["id"] for _, payload in ev._windows[rule.id]] for rule in ev._rules}
        assert actual == expected
        assert sum(1 for ids in expected.values() if ids) >= 5

    @pytest.mark.asyncio
    async def test_rules_for_other_apps_are_never_evaluated(self, tmp_path: Path, monkeypatch):
        ev = _alert_evaluator(tmp_path)
        names = {rule.id: rule.filter_app for rule in ev._rules}
        evaluated: list[int] = []
        original = CompiledAlertRule.matches

        def counting_matches(self, entry):
            evaluated.append(self.id)
            return original(self, entry)

        monkeypatch.setattr(CompiledAlertRule, "matches", counting_matches)

        await ev.evaluate_batch([{"id": 1, "app_name": "postgres", "message": "fine", "timestamp": NOW_ISO}])
        assert [names[i] for i in evaluated] == ["ngin*", None, "postgres", "sshd,ngin?"]

    @pytest.mark.asyncio
    async def test_a_reload_rebuilds_the_lookup(self, tmp_path: Path):
        ev = _alert_evaluator(tmp_path)
        conn = get_connection(tmp_path / "logs.db")
        conn.execute("UPDATE alert_rules SET filter_app = 'redis' WHERE name = 'Alert 7'")
        conn.commit()
        conn.close()
        await ev.evaluate_batch([{"id": 1, "app_name": "redis", "message": "DOWN", "timestamp": NOW_ISO}])
        assert all(not window for window in ev._windows.values())

        ev.reload_rules()
        await ev.evaluate_batch([{"id": 2, "app_name": "Redis", "message": "replica DOWN", "timestamp": NOW_ISO}])
        rule_id = next(r.id for r in ev._rules if r.name == "Alert 7")
        assert [payload["id"] for _, payload in ev._windows[rule_id]] == [2]


class TestRuleIndex:
    def test_exact_match_key(self):
        assert exact_match_key(" Nginx ") == "nginx"
        assert exact_match_key("ngin*") is None
        assert exact_match_key("ngin?") is None
        assert exact_match_key("   ") is None
        assert exact_match_key(None) is None
        # Brackets are not wildcards for match_wildcard, so they stay exact
        assert exact_match_key("app[1]") == "app[1]"

    def test_entry_key(self):
        assert entry_key("  NGINX ") == "nginx"
        assert entry_key(None) == ""

    def test_candidates_keep_rule_order_and_cache_stays_bounded(self, monkeypatch):
        import app.core.rule_index as rule_index_mod

        rules = ["a1", "*", "b1", "a2", "*2"]
        index = RuleIndex(rules, lambda r: None if r.startswith("*") else ("app", (r[0],)))
        assert index.candidates((("app", "a"),)) == ["a1", "*", "a2", "*2"]
        assert index.candidates((("app", "b"), ("app", "a"))) == ["a1", "*", "b1", "a2", "*2"]
        assert index.candidates((("app", "z"),)) == ["*", "*2"]
        assert index.keyed_candidates((("app", "a"),)) == ["a1", "a2"]
        assert index.keyed_candidates((("app", "z"),)) == []
        assert index.is_always("*") and not index.is_always("a1")

        monkeypatch.setattr(rule_index_mod, "_CANDIDATE_CACHE_LIMIT", 3)
        for value in "abcdefg":
            index.candidates((("app", value),))
        assert len(index._candidate_cache) <= 3
