"""
"Check last 24h" for alert and drop rules (services/rule_preview.py and the two preview routes):
the rule's own matcher over the stored logs of the last 24 hours, newest first, with a row cap,
a time cap and one check at a time.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.day_files import KIND_INTERNAL
from app.core.migrations import run_migrations
from app.core.security import SESSION_COOKIE_NAME, create_session_token, get_or_create_master_key, reset_crypto_cache
from app.main import create_app
from app.services import rule_preview
from app.services.alert_evaluator import CompiledAlertRule
from tests.db_helpers import open_db, store_logs

NOW = datetime.now(timezone.utc)


def _log(minutes_ago: float, message: str = "all good", *, app="sshd", host="nas", ip="10.0.0.5", severity=6, kind=None) -> dict:
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    entry = {
        "timestamp": ts,
        "source_ip": ip,
        "source_alias": host,
        "app_name": app,
        "severity": severity,
        "message": message,
        "raw": f"<30>{app}: {message}",
    }
    if kind is not None:
        entry["source_kind"] = kind
    return entry


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    reset_crypto_cache()
    db_file = tmp_path / "logs.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(tmp_path / ".secret_key"))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(tmp_path / ".secret_key")
    yield db_file
    reset_crypto_cache()


@pytest_asyncio.fixture
async def client(db):
    transport = ASGITransport(app=create_app())
    token = create_session_token(user_id=1)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-requested-with": "XMLHttpRequest", "Cookie": f"{SESSION_COOKIE_NAME}={token}"},
    ) as ac:
        yield ac


def _alert_rule(**kwargs) -> CompiledAlertRule:
    fields = dict(
        id=0, name="t", rule_type="pattern", channel_id=None, filter_app=None, filter_severity=None,
        match_pattern=None, threshold_count=1, window_seconds=60, cooldown_seconds=0, ai_enrichment=False,
        is_enabled=True,
    )
    fields.update(kwargs)
    return CompiledAlertRule(**fields)


def _preview(db, matches, **kwargs) -> rule_preview.PreviewResult:
    with open_db(db, sqlite3.Row) as conn:
        return rule_preview.run_preview(conn, matches, now=NOW, **kwargs)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


def test_counts_what_the_rule_matches_in_the_last_24_hours_newest_examples_first(db):
    store_logs(
        db,
        _log(60 * 25, "Failed password for root"),  # older than the window
        *[_log(60 - i, f"Failed password for user{i}") for i in range(7)],
        _log(5, "Accepted password for ben"),
        _log(4, "failed PASSWORD for admin", app="dropbear"),
    )
    rule = _alert_rule(match_pattern="failed password", is_regex=False, filter_app="sshd")
    result = _preview(db, rule.matches)
    assert (result.matched, result.checked, result.complete, result.stopped_by) == (7, 9, True, None)
    assert [r["message"] for r in result.examples] == [f"Failed password for user{i}" for i in (6, 5, 4, 3, 2)]
    assert result.window_start == (NOW - timedelta(hours=24)).isoformat()


def test_the_result_is_what_the_live_matcher_decides_for_each_log(db):
    logs = [
        _log(10, "disk sda1 error 5", host="nas", severity=3),
        _log(9, "disk sdb2 error", host="router", ip="10.0.0.1", severity=3),
        _log(8, "disk sda1 error 7", host="nas", severity=6),
        _log(7, "DISK SDC3 ERROR 9", host="", ip="10.0.0.5", severity=2),
    ]
    store_logs(db, *[dict(entry) for entry in logs])
    rule = _alert_rule(match_pattern=r"disk sd[a-z]\d error \d", is_regex=True, filter_host="nas,10.0.0.5", filter_severity=4)
    expected = sum(1 for entry in logs if rule.matches(entry))
    result = _preview(db, rule.matches, severity_max=4)
    assert result.matched == expected == 2
    # The severity filter is applied while reading, so the less severe log is not even checked
    assert result.checked == 3


def test_logshed_logs_that_alert_rules_skip_are_left_out(db):
    store_logs(
        db,
        _log(3, "rule fired", app="alert_evaluator", host="logshed", ip="127.0.0.1", kind=KIND_INTERNAL),
        _log(2, "rule fired", app="notifier", host="logshed", ip="127.0.0.1", kind=KIND_INTERNAL),
        _log(1, "rule fired", app="retention", host="logshed", ip="127.0.0.1", kind=KIND_INTERNAL),
        _log(1, "rule fired", app="notifier", host="nas"),  # another host's app of that name counts
    )
    rule = _alert_rule(match_pattern="rule fired", is_regex=False)
    alert = _preview(db, rule.matches, skip=rule_preview.is_alert_exempt)
    everything = _preview(db, rule.matches)
    assert (alert.matched, alert.checked) == (2, 2)
    assert (everything.matched, everything.checked) == (4, 4)


def test_stops_at_the_row_cap_and_says_how_far_back_it_reached(db, monkeypatch):
    monkeypatch.setattr(rule_preview, "PAGE_SIZE", 2)
    store_logs(db, *[_log(10 - i, "hit") for i in range(6)])
    result = _preview(db, lambda entry: True, row_cap=3)
    assert (result.matched, result.checked, result.complete, result.stopped_by) == (3, 3, False, "rows")
    assert result.oldest_checked == (NOW - timedelta(minutes=7)).isoformat()


def test_a_window_with_exactly_the_cap_is_complete(db):
    store_logs(db, *[_log(10 - i) for i in range(3)])
    result = _preview(db, lambda entry: False, row_cap=3)
    assert (result.checked, result.complete, result.stopped_by) == (3, True, None)


def test_stops_at_the_time_cap(db):
    store_logs(db, *[_log(10 - i) for i in range(5)])
    ticks = iter([0.0, 0.1, 0.2, 5.0, 5.1, 5.2, 5.3])
    result = _preview(db, lambda entry: True, time_cap=5.0, clock=lambda: next(ticks))
    assert (result.checked, result.complete, result.stopped_by) == (2, False, "time")


def test_one_check_at_a_time(db):
    assert rule_preview._running.acquire(blocking=False)
    try:
        with pytest.raises(rule_preview.PreviewBusy):
            _preview(db, lambda entry: True)
    finally:
        rule_preview._running.release()
    # A check that raised lets the next one run
    with pytest.raises(ZeroDivisionError):
        store_logs(db, _log(1))
        _preview(db, lambda entry: 1 / 0)
    assert _preview(db, lambda entry: True).matched == 1


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_rule_route_counts_with_the_rules_filters(client, db):
    store_logs(
        db,
        _log(30, "Out of memory: Killed process 4242", app="kernel", severity=3),
        _log(20, "Out of memory: Killed process 77", app="kernel", severity=3),
        _log(10, "Out of memory: Killed process 1", app="kernel", severity=6),
        _log(5, "Out of memory: Killed process 9", app="kernel", host="router", ip="10.0.0.1", severity=3),
    )
    res = await client.post(
        "/api/alerts/preview",
        json={"match_pattern": r"killed process \d+", "is_regex": True, "filter_app": "kern*", "filter_host": "nas", "filter_severity": 4},
    )
    assert res.status_code == 200
    body = res.json()
    assert (body["matched"], body["checked"], body["complete"], body["error"]) == (2, 3, True, None)
    assert body["row_cap"] == rule_preview.ROW_CAP
    assert [e["message"] for e in body["examples"]] == [
        "Out of memory: Killed process 77", "Out of memory: Killed process 4242",
    ]
    assert body["examples"][0]["source_alias"] == "nas" and body["examples"][0]["raw"]


@pytest.mark.asyncio
async def test_alert_rule_route_reads_a_pattern_without_the_choice_as_rules_do(client, db):
    store_logs(db, _log(3, "error: a|b"), _log(2, "error: a"))
    res = await client.post("/api/alerts/preview", json={"match_pattern": "error: a|b"})
    # Left out, a pattern with an operator is read as a regular expression, as when it is saved
    assert res.json()["matched"] == 2
    res = await client.post("/api/alerts/preview", json={"match_pattern": "error: a|b", "is_regex": False})
    assert res.json()["matched"] == 1


@pytest.mark.asyncio
async def test_alert_rule_route_refuses_an_unsafe_pattern_without_reading(client, db, monkeypatch):
    def _no_read(*args, **kwargs):
        raise AssertionError("read logs")

    monkeypatch.setattr(rule_preview, "run_preview", _no_read)
    for pattern in ["(a+)+$", "unclosed("]:
        res = await client.post("/api/alerts/preview", json={"match_pattern": pattern, "is_regex": True})
        assert res.status_code == 200
        assert res.json()["error"] and res.json()["checked"] == 0


@pytest.mark.parametrize("path, body", [
    ("/api/alerts/preview", {"match_pattern": "x.*y", "is_regex": True}),
    ("/api/drop-rules/preview", {"message_pattern": "x.*y", "is_regex": True}),
])
@pytest.mark.asyncio
async def test_routes_count_logs_whose_pattern_timed_out_without_logging_a_warning(client, db, monkeypatch, caplog, path, body):
    from app.core import regex_validator

    # A limit no search of a long line can meet, so every search times out for real
    monkeypatch.setattr(regex_validator, "USER_REGEX_TIMEOUT", 1e-9)
    long_line = "x" + "a" * 200_000
    store_logs(db, {**_log(2, long_line), "raw": "<30>" + long_line}, {**_log(1, long_line), "raw": "<30>" + long_line})
    before = regex_validator.get_user_regex_timeout_counts()
    with caplog.at_level("WARNING"):
        res = await client.post(path, json=body)
    # Message and raw both time out on each log: counted once per log
    assert (res.json()["pattern_timeouts"], res.json()["matched"]) == (2, 0)
    assert not [r for r in caplog.records if "match limit" in r.getMessage()]
    assert regex_validator.get_user_regex_timeout_counts() == before


@pytest.mark.asyncio
async def test_routes_say_when_another_check_runs(client, db):
    assert rule_preview._running.acquire(blocking=False)
    try:
        for path in ["/api/alerts/preview", "/api/drop-rules/preview"]:
            res = await client.post(path, json={"match_pattern": "x"} if "alerts" in path else {"message_pattern": "x"})
            assert res.status_code == 409
            assert res.json()["detail"] == rule_preview.PREVIEW_BUSY_DETAIL
    finally:
        rule_preview._running.release()


@pytest.mark.asyncio
async def test_drop_rule_route_counts_what_the_rule_would_have_dropped(client, db):
    store_logs(
        db,
        _log(9, "DHCPACK on 10.0.0.20 to aa:bb", app="dnsmasq", severity=6),
        _log(8, "DHCPACK on 10.0.0.21 to cc:dd", app="dnsmasq-dhcp", severity=7),
        _log(7, "DHCPACK on 10.0.0.22 to ee:ff", app="dnsmasq", severity=3),  # more severe: kept
        _log(6, "DHCPREQUEST on 10.0.0.23", app="dnsmasq", severity=6),
        _log(5, "DHCPACK on 10.0.0.24", app="dnsmasq", host="other", ip="10.0.0.9", severity=6),
        # LogShed's own logs go through drop rules too
        _log(4, "dhcpack on 10.0.0.25", app="dnsmasq", host="logshed", ip="127.0.0.1", kind=KIND_INTERNAL),
    )
    res = await client.post(
        "/api/drop-rules/preview",
        json={"message_pattern": "dhcpack on 10.0.0.*", "app_pattern": "dnsmasq*", "source_pattern": "nas,logshed", "severity_threshold": 5},
    )
    assert res.status_code == 200
    body = res.json()
    assert (body["matched"], body["checked"], body["error"]) == (3, 6, None)
    assert [e["message"] for e in body["examples"]] == [
        "dhcpack on 10.0.0.25", "DHCPACK on 10.0.0.21 to cc:dd", "DHCPACK on 10.0.0.20 to aa:bb",
    ]


@pytest.mark.asyncio
async def test_drop_rule_route_with_a_regex_and_an_unsafe_one(client, db):
    store_logs(db, _log(2, "GET /health 200"), _log(1, "GET /healthz 200"))
    res = await client.post("/api/drop-rules/preview", json={"message_pattern": r"/health\b", "is_regex": True})
    assert res.json()["matched"] == 1
    res = await client.post("/api/drop-rules/preview", json={"message_pattern": "(a*)*$", "is_regex": True})
    assert res.json()["error"] and res.json()["checked"] == 0


@pytest.mark.asyncio
async def test_preview_routes_need_a_signed_in_user(db):
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver", headers={"x-requested-with": "XMLHttpRequest"}
    ) as anonymous:
        for path in ["/api/alerts/preview", "/api/drop-rules/preview"]:
            assert (await anonymous.post(path, json={})).status_code == 401
