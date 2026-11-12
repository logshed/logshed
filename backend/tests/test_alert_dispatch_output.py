"""
What an alert sends and records, pinned word for word: the AI prompt, the notification and the
alert history row, for a threshold rule and a rate rule, with AI on, off and failing.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.migrations import get_connection, run_migrations
from app.services.ai_engine import AiAnalysisResult
from app.services.alert_evaluator import AlertEvaluator, CompiledAlertRule

GOLDEN = Path(__file__).parent / "data" / "alert_dispatch_golden.json"

LOGS = [
    {"id": 11, "timestamp": "2026-11-12T10:00:00+00:00", "source_alias": "web-1", "app_name": "nginx",
     "severity": 3, "message": "upstream timed out from 203.0.113.9 password=hunter2"},
    {"id": 12, "timestamp": "2026-11-12T10:00:01+00:00", "source_ip": "10.0.0.7", "container_name": "api",
     "severity": 4, "message": "upstream timed out"},
    {"id": 13, "timestamp": "2026-11-12T10:00:02+00:00", "source_alias": "web-1", "app_name": "nginx",
     "severity": 3, "message": "upstream timed out from 198.51.100.4", "raw": "<11>raw form"},
]


def _rule(rule_type: str, ai: bool) -> CompiledAlertRule:
    return CompiledAlertRule(
        id=1, name="Upstream errors", rule_type=rule_type, channel_id=None, filter_app=None,
        filter_severity=None, match_pattern="timed out", threshold_count=2, window_seconds=60,
        cooldown_seconds=0, ai_enrichment=ai, is_enabled=True,
    )


def _db(tmp_path: Path, ai_on: bool) -> Path:
    db = tmp_path / "logs.db"
    run_migrations(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO alert_rules (id, name, rule_type, match_pattern, threshold_count, window_seconds, "
        "cooldown_seconds, ai_enrichment, is_enabled, created_at) VALUES (1, 'Upstream errors', 'threshold', "
        "'timed out', 2, 60, 0, 1, 1, '2026-11-12T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_enabled', ?, datetime('now'), 0)",
        ("1" if ai_on else "0",),
    )
    conn.commit()
    conn.close()
    return db


async def _run(tmp_path, monkeypatch, rule_type: str, ai: str) -> dict:
    db = _db(tmp_path, ai_on=ai != "off")
    monkeypatch.setenv("APP_URL", "https://logshed.lan/")
    from app.core.config import invalidate_settings_cache
    invalidate_settings_cache()
    prompts: list[str] = []

    async def fake_ai(**kwargs):
        prompts.append(kwargs["prompt_override"])
        if ai == "fail":
            raise RuntimeError("504 Deadline exceeded\nsecond line")
        return AiAnalysisResult(
            summary="**Upstream** is slow.", root_cause="Backend overloaded.", remediation="Scale it.",
            raw_response="raw", prompt_sent="sent", tokens_in=1, tokens_out=2, tokens_thoughts=0,
            tokens_used=3, model_used="model-x",
        )

    monkeypatch.setattr("app.services.ai_engine.execute_ai_analysis", fake_ai)
    sent: list[dict] = []

    async def fake_send(self, title, body, channel_id=None, **kwargs):
        sent.append({"title": title, "body": body})
        return True

    from app.services.notifier import NotifierService
    monkeypatch.setattr(NotifierService, "send_notification", fake_send)

    await AlertEvaluator(db)._dispatch_alert(_rule(rule_type, ai != "none"), [dict(e) for e in LOGS])

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT trigger_count, sample_log, sample_log_id, incident_summary, ai_enrichment, ai_model, "
        "ai_audit_id IS NOT NULL FROM alert_history"
    ).fetchone()
    conn.close()
    return {"prompt": prompts, "notification": sent, "history": list(row)}


CASES = [(t, a) for t in ("threshold", "rate") for a in ("ok", "off", "fail", "none")]


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_type,ai", CASES)
async def test_alert_output_is_unchanged(tmp_path, monkeypatch, rule_type, ai):
    got = await _run(tmp_path, monkeypatch, rule_type, ai)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert got == expected[f"{rule_type}/{ai}"]
