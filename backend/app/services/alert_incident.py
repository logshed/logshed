"""
What a fired alert says: the facts drawn from its triggering logs (sample, indicators, culprits),
the AI prompt, the incident summary stored in alert history and the notification text.

Everything here is pure: `AlertEvaluator._dispatch_alert` does the I/O (AI call, history
insert, sending) and calls these to build each piece.
"""

from collections import Counter
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, Optional

from app.core.redactor import redact
from app.services.alert_presets import extract_ip_from_message
from app.services.rate_window import RateSnapshot, culprit_values

if TYPE_CHECKING:
    from app.services.ai_engine import AiAnalysisResult
    from app.services.alert_evaluator import CompiledAlertRule


def _log_id(entry: dict[str, Any]) -> Optional[int]:
    """The stored id of a log entry (set by the queue consumer after its insert), or None."""
    value = entry.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def format_sample_log_for_alert(raw_msg: str, max_lines: int = 5, max_chars: int = 500) -> str:
    """
    Format and truncate log message for clean mobile/webhook push notifications.
    Truncates long multi-line or excessively long strings to preserve readability.
    """
    if not raw_msg:
        return ""
    cleaned = raw_msg.strip()
    lines = cleaned.splitlines()
    if len(lines) > max_lines:
        cleaned = "\n".join(lines[:max_lines]) + "\n... [truncated]"
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "... [truncated]"
    return cleaned


def strip_markdown(text: str) -> str:
    """Strip markdown formatting syntax for clean plain-text push notifications."""
    if not text:
        return ""
    t = re.sub(r"```[a-zA-Z]*\n?", "", text)
    t = t.replace("```", "").replace("`", "")
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", t)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)
    return t.strip()


@dataclass
class AlertFacts:
    """What the triggering logs of one firing say."""
    # Matches in the window (a rate rule counts them all and keeps only the newest as logs)
    log_count: int
    sample_log: str = ""
    sample_log_id: Optional[int] = None
    ip: Optional[str] = None
    host: Optional[str] = None
    app: Optional[str] = None
    # "value (count/total, pct%)" for the most frequent app, host and message
    culprit_app: Optional[str] = None
    culprit_host: Optional[str] = None
    culprit_msg: Optional[str] = None
    measured_rate: Optional[float] = None


@dataclass
class AiEnrichment:
    """The outcome of AI enrichment for one firing."""
    # Whether AI was switched on in Settings when the alert fired
    enabled: bool = False
    success: bool = False
    # The diagnosis summary on its own, for the notification
    summary: Optional[str] = None
    incident_summary: Optional[str] = None
    error_note: Optional[str] = None
    model: Optional[str] = None
    audit_id: Optional[int] = None


def _sample_and_indicators(facts: AlertFacts, triggering_logs: list[dict[str, Any]]) -> None:
    """The newest log is the sample; IP, host and app come from the newest log that has each."""
    sample_entry = triggering_logs[-1]
    if isinstance(sample_entry, dict):
        facts.sample_log = str(sample_entry.get("message") or sample_entry.get("raw") or "")
        facts.sample_log_id = _log_id(sample_entry)
    for log_entry in reversed(triggering_logs):
        if not isinstance(log_entry, dict):
            continue
        if not facts.ip:
            cand_ip = extract_ip_from_message(str(log_entry.get("message") or ""))
            if cand_ip:
                facts.ip = cand_ip
        if not facts.host:
            cand_host = log_entry.get("source_alias") or log_entry.get("source_ip") or log_entry.get("host")
            if cand_host:
                facts.host = str(cand_host).strip()
        if not facts.app:
            cand_app = log_entry.get("app_name") or log_entry.get("container_name") or log_entry.get("tag")
            if cand_app:
                facts.app = str(cand_app).strip()


def _culprit_counters(
    triggering_logs: list[dict[str, Any]], rate: Optional[RateSnapshot]
) -> tuple[Counter, Counter, Counter]:
    """App, host and message counts: the rate window's own, or counted from the logs."""
    if rate is not None:
        return rate.apps, rate.hosts, rate.messages
    app_counter: Counter = Counter()
    host_counter: Counter = Counter()
    msg_counter: Counter = Counter()
    for log_entry in triggering_logs:
        if not isinstance(log_entry, dict):
            continue
        app_val, host_val, msg_val = culprit_values(log_entry)
        app_counter[app_val] += 1
        host_counter[host_val] += 1
        if msg_val:
            msg_counter[msg_val] += 1
    return app_counter, host_counter, msg_counter


def _top_share(counter: Counter, total: int) -> tuple[Any, int, int]:
    """The most frequent value, its count and its percentage of total."""
    top, count = counter.most_common(1)[0]
    # Counters of a rate window may include a little of the time just before the window
    count = min(count, total)
    return top, count, round((count / total) * 100)


def _culprits(
    facts: AlertFacts,
    rule: "CompiledAlertRule",
    triggering_logs: list[dict[str, Any]],
    rate: Optional[RateSnapshot],
) -> None:
    """
    The most frequent app, host and message. For a rate rule the top app and host also become
    the alert's app and host, and the sample is the newest log of the top app.
    """
    total = facts.log_count
    app_counter, host_counter, msg_counter = _culprit_counters(triggering_logs, rate)

    if app_counter:
        top_app, app_count, app_pct = _top_share(app_counter, total)
        facts.culprit_app = f"{top_app} ({app_count}/{total}, {app_pct}%)"
        if rule.rule_type == "rate":
            facts.app = top_app
            top_app_entry = rate.newest_by_app.get(top_app) if rate is not None else next(
                (
                    log_entry
                    for log_entry in reversed(triggering_logs)
                    if isinstance(log_entry, dict) and culprit_values(log_entry)[0] == top_app
                ),
                None,
            )
            if top_app_entry is not None:
                facts.sample_log = str(top_app_entry.get("message") or top_app_entry.get("raw") or "")
                facts.sample_log_id = _log_id(top_app_entry)
    if host_counter:
        top_host, host_count, host_pct = _top_share(host_counter, total)
        facts.culprit_host = f"{top_host} ({host_count}/{total}, {host_pct}%)"
        if rule.rule_type == "rate":
            facts.host = top_host
    if msg_counter:
        top_msg, msg_count, msg_pct = _top_share(msg_counter, total)
        # Notifications and the AI prompt show this message, so it is redacted like the sample log
        facts.culprit_msg = f"{redact(top_msg)} (x{msg_count}, {msg_pct}%)"

    facts.measured_rate = round(total / max(1.0, float(rule.window_seconds)), 1)


def collect_alert_facts(
    rule: "CompiledAlertRule",
    triggering_logs: list[dict[str, Any]],
    rate: Optional[RateSnapshot] = None,
) -> AlertFacts:
    """The sample log, indicators and culprits of one firing."""
    facts = AlertFacts(log_count=rate.total if rate is not None else len(triggering_logs))
    if triggering_logs:
        _sample_and_indicators(facts, triggering_logs)
        _culprits(facts, rule, triggering_logs, rate)
    return facts


def _culprit_lines(facts: AlertFacts) -> str:
    return (
        f"### Primary Culprits\n"
        f"- Top Service / App: {facts.culprit_app or 'Unknown'}\n"
        f"- Top Host / Source: {facts.culprit_host or 'Unknown'}\n"
        f"- Top Pattern: {facts.culprit_msg or 'N/A'}"
    )


def plain_incident_summary(rule: "CompiledAlertRule", facts: AlertFacts) -> str:
    """The incident summary when no AI diagnosis is asked for or AI is off."""
    if rule.rule_type == "rate":
        return (
            f"Log storm detected: {facts.log_count} logs in {rule.window_seconds}s ({facts.measured_rate} logs/s).\n\n"
            + _culprit_lines(facts)
        )
    return f"Alert triggered with {facts.log_count} matching event(s)."


def build_alert_prompt(rule: "CompiledAlertRule", facts: AlertFacts, redacted_text: str) -> str:
    """The AI prompt for an alert, around its already redacted and fenced log lines."""
    if rule.rule_type == "rate":
        threshold_line = (
            f"- Threshold: {rule.threshold_count} logs/s in {rule.window_seconds}s "
            f"(Measured: {facts.measured_rate} logs/s)"
        )
        culprit_section = _culprit_lines(facts) + "\n\n"
    else:
        threshold_line = f"- Threshold: {rule.threshold_count} matches in {rule.window_seconds}s"
        culprit_section = ""

    return (
        f"### Security / Operations Incident Alert\n"
        f"- Alert Rule: {rule.name}\n"
        f"- Rule Type: {rule.rule_type}\n"
        f"- Host / Source: {facts.host or 'Unknown'}\n"
        f"- App / Container: {facts.app or 'Unknown'}\n"
        f"{threshold_line}\n"
        f"- Offending IP: {facts.ip or 'None detected'}\n\n"
        f"{culprit_section}"
        f"### Redacted Log Stream (Chronological)\n"
        f"```\n{redacted_text}\n```\n\n"
        f"Review this incident and provide structured Summary, Root Cause, and Actionable Remediation."
    )


def ai_incident_summary(ai_result: "AiAnalysisResult") -> str:
    """The stored incident summary from an AI diagnosis: summary, root cause and remediation."""
    summary = ai_result.summary
    parts = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if ai_result.root_cause and ai_result.root_cause.strip():
        parts.append(f"### Root Cause\n{ai_result.root_cause.strip()}")
    if ai_result.remediation and ai_result.remediation.strip():
        parts.append(f"### Remediation\n{ai_result.remediation.strip()}")
    return "\n\n".join(parts) if parts else (summary or "Incident review complete.")


def short_error_note(err: Exception) -> str:
    """The first line of an AI error, at most 250 characters."""
    clean_err = str(err).splitlines()[0]
    if len(clean_err) > 250:
        clean_err = clean_err[:247] + "..."
    return clean_err


def format_alert_notification(
    rule: "CompiledAlertRule",
    facts: AlertFacts,
    ai: AiEnrichment,
    history_id: Optional[int],
    app_url: str,
) -> tuple[str, str]:
    """The notification title and body, with secrets redacted."""
    if rule.rule_type == "rate":
        title = str(redact(f"LogShed: [Log Storm] {rule.name} ({facts.measured_rate} logs/s)"))
    else:
        title = str(redact(f"LogShed: {rule.name}"))

    redacted_sample_log = str(redact(facts.sample_log)) if facts.sample_log else ""
    clean_log = format_sample_log_for_alert(redacted_sample_log).replace("```", "").replace("`", "").strip()

    if rule.rule_type == "rate":
        body_lines = [
            f"**Host:** {facts.culprit_host or facts.host or 'Unknown'}",
            f"**App:** {facts.culprit_app or facts.app or 'Unknown'}",
            f"**Rate:** {facts.measured_rate} logs/s (Threshold: {rule.threshold_count} logs/s in {rule.window_seconds}s)",
        ]
        if facts.culprit_msg:
            body_lines.append(f"**Top Pattern:** {facts.culprit_msg}")
        if clean_log:
            body_lines.append(f"**Sample Log:** {clean_log}")
    else:
        body_lines = [
            f"**Host:** {facts.host or 'Unknown'}",
            f"**App:** {facts.app or 'Unknown'}",
            f"**Log:** {clean_log}",
        ]

    if rule.ai_enrichment and ai.enabled:
        if ai.success and ai.summary:
            clean_summary = strip_markdown(str(redact(ai.summary)))
            if len(clean_summary) > 200:
                clean_summary = clean_summary[:197] + "..."
            body_lines.append(f"**AI Analysis:** {clean_summary}")
        elif ai.error_note:
            body_lines.append(f"**AI Analysis:** Unavailable ({redact(ai.error_note)})")

    if app_url:
        link = f"{app_url}/rules/history"
        if history_id:
            link += f"?id={history_id}"
        body_lines.append(f"**Link:** {link}")

    return title, "\n".join(body_lines)
