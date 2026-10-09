"""
Alert Engine Service for LogShed.

Provides in-memory sliding-window timestamp deques for threshold rules,
regex and keyword pattern matching, and cooldown flap dampening (suppress_until).
Integrates with QueueConsumer batch processing, redactor, AI diagnosis, and notifier.
"""

import asyncio
from collections import Counter, deque
import datetime
import fnmatch
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Optional, Union

from app.core.redactor import redact
from app.core.regex_validator import validate_pattern_complexity
from app.core.utils import match_wildcard, parse_iso_to_epoch
from app.services.notifier import get_notifier
from app.services.alert_presets import extract_ip_from_message

logger = logging.getLogger(__name__)

_REGEX_METACHARS = frozenset("*+?[](){}^$|")


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


class CompiledAlertRule:
    """Compiled alert rule representation with cached regex pattern."""

    def __init__(
        self,
        id: int,
        name: str,
        rule_type: str,
        channel_id: Optional[int],
        filter_app: Optional[str],
        filter_severity: Optional[int],
        match_pattern: Optional[str],
        threshold_count: int,
        window_seconds: int,
        cooldown_seconds: int,
        ai_enrichment: bool,
        is_enabled: bool,
        trigger_count: int = 0,
        last_triggered_at: Optional[str] = None,
        suppress_until: Optional[str] = None,
        display_order: int = 0,
    ):
        self.id = id
        self.name = name
        self.rule_type = rule_type
        self.channel_id = channel_id
        self.filter_app = filter_app
        self.filter_severity = filter_severity
        self.match_pattern = match_pattern
        self.threshold_count = max(1, threshold_count)
        self.window_seconds = max(1, window_seconds)
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.ai_enrichment = bool(ai_enrichment)
        self.is_enabled = bool(is_enabled)
        self.trigger_count = trigger_count
        self.last_triggered_at = last_triggered_at
        self.suppress_until = suppress_until
        self.display_order = display_order
        self.suppress_until_epoch: Optional[float] = None
        self._recompute_suppress_epoch()

        # Pre-split comma-delimited filter_app into a tuple of stripped, lowercased strings (PERF-02)
        if self.filter_app and self.filter_app.strip():
            self._filter_apps = tuple(
                p.strip().lower() for p in self.filter_app.split(",") if p.strip()
            )
        else:
            self._filter_apps = ()

        self.compiled_regex: Optional[re.Pattern] = None
        self._match_pattern_lower: Optional[str] = None
        self._has_regex_metachars: bool = False
        self._regex_verified: bool = False
        if self.match_pattern and self.match_pattern.strip() and self.match_pattern.strip() != "*":
            clean_pat = self.match_pattern.strip()
            self._match_pattern_lower = clean_pat.lower()
            self._has_regex_metachars = any(c in _REGEX_METACHARS for c in clean_pat)
            if self._has_regex_metachars:
                try:
                    validate_pattern_complexity(clean_pat)
                    self._regex_verified = True
                    self.compiled_regex = re.compile(clean_pat, re.IGNORECASE)
                except Exception as exc:
                    logger.warning(
                        "Alert rule %s pattern '%s' failed regex validation: %s",
                        self.id,
                        clean_pat,
                        exc,
                    )
                    self.compiled_regex = None

    def _recompute_suppress_epoch(self) -> None:
        """Parse suppress_until ISO string into a UTC epoch timestamp."""
        if not self.suppress_until:
            self.suppress_until_epoch = None
            return
        epoch = parse_iso_to_epoch(self.suppress_until, fallback=0.0)
        if epoch <= 0.0:
            self.suppress_until_epoch = None
            return
        now_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if epoch <= now_epoch:
            self.suppress_until = None
            self.suppress_until_epoch = None
        else:
            self.suppress_until_epoch = epoch

    def matches(self, entry: dict[str, Any]) -> bool:
        """Check if an incoming log entry satisfies this rule's match criteria."""
        if not self.is_enabled:
            return False

        # 1. App filter check (supports pre-split comma-separated apps from multi-select)
        if self._filter_apps:
            app_name = entry.get("app_name") or ""
            if not any(match_wildcard(p, app_name) for p in self._filter_apps):
                return False

        # 2. Severity filter check (syslog 0-7, lower is more severe)
        if self.filter_severity is not None:
            sev = entry.get("severity")
            actual_sev = 6 if sev is None else sev
            if actual_sev > self.filter_severity:
                return False

        # 3. Message pattern check (prioritize substring matching when no regex metacharacters)
        if self._match_pattern_lower:
            message = str(entry.get("message") or "")
            raw = str(entry.get("raw") or "")

            has_metachars = getattr(self, "_has_regex_metachars", None)
            if has_metachars is None:
                has_metachars = bool(self.match_pattern and any(c in _REGEX_METACHARS for c in self.match_pattern))

            if not has_metachars:
                pat_lower = self._match_pattern_lower
                if pat_lower not in message.lower() and (not raw or pat_lower not in raw.lower()):
                    return False
            else:
                if not getattr(self, "_regex_verified", False):
                    if self.match_pattern and self.match_pattern.strip() != "*":
                        clean_pat = self.match_pattern.strip()
                        try:
                            validate_pattern_complexity(clean_pat)
                            self._regex_verified = True
                            if self.compiled_regex is None:
                                self.compiled_regex = re.compile(clean_pat, re.IGNORECASE)
                        except Exception:
                            return False
                    else:
                        return False

                if self.compiled_regex is not None:
                    msg_cand = message[:16384]
                    raw_cand = raw[:16384] if raw else ""
                    if not (self.compiled_regex.search(msg_cand) or (raw_cand and self.compiled_regex.search(raw_cand))):
                        return False
                else:
                    return False

        return True


class AlertEvaluator:
    """
    In-memory evaluation engine for alert rules.
    Maintains sliding-window timestamp deques and evaluates logs in real time.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path:
            self.db_path = Path(db_path)
        else:
            try:
                from app.core.config import get_db_path
                self.db_path = get_db_path()
            except Exception:
                self.db_path = None
        self._rules: list[CompiledAlertRule] = []
        self._windows: dict[int, deque[tuple[float, dict[str, Any]]]] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        self._lock = threading.Lock()
        if self.db_path:
            self.reload_rules()

    def reload_rules(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """Reload active alert rules from SQLite and update compiled cache."""
        from app.core.config import get_db_path
        effective_db = self.db_path or get_db_path()
        if not effective_db and conn is None:
            return

        def _load(c: sqlite3.Connection):
            cur = c.cursor()
            cur.execute(
                """
                SELECT id, name, rule_type, channel_id, filter_app, filter_severity,
                       match_pattern, threshold_count, window_seconds, cooldown_seconds,
                       ai_enrichment, is_enabled, trigger_count, last_triggered_at, suppress_until,
                       display_order
                FROM alert_rules
                WHERE is_enabled = 1
                ORDER BY display_order ASC, id ASC
                """
            )
            return cur.fetchall()

        if conn is not None:
            rows = _load(conn)
        elif effective_db:
            from app.api.deps import get_thread_read_connection
            c = get_thread_read_connection(effective_db)
            rows = _load(c)
        else:
            rows = []

        compiled: list[CompiledAlertRule] = []
        active_ids: set[int] = set()

        for r in rows:
            rule = CompiledAlertRule(
                id=r[0],
                name=r[1],
                rule_type=r[2],
                channel_id=r[3],
                filter_app=r[4],
                filter_severity=r[5],
                match_pattern=r[6],
                threshold_count=r[7],
                window_seconds=r[8],
                cooldown_seconds=r[9],
                ai_enrichment=bool(r[10]),
                is_enabled=bool(r[11]),
                trigger_count=r[12] or 0,
                last_triggered_at=r[13],
                suppress_until=r[14],
                display_order=r[15] if len(r) > 15 else 0,
            )
            compiled.append(rule)
            active_ids.add(rule.id)

        with self._lock:
            self._rules = compiled
            # Clean up windows for rules that are no longer active
            stale_ids = [rid for rid in self._windows if rid not in active_ids]
            for rid in stale_ids:
                del self._windows[rid]

            for rule in compiled:
                if rule.id not in self._windows:
                    self._windows[rule.id] = deque()

    def prune_expired_windows(self, now_epoch: float) -> None:
        """
        Iterate through all rule windows in self._windows and pop left entries
        where entry_epoch < (now_epoch - rule.window_seconds).
        """
        with self._lock:
            self._prune_expired_windows_locked(now_epoch)

    def _prune_expired_windows_locked(self, now_epoch: float) -> None:
        """Prune expired window entries while holding self._lock."""
        rules_by_id = {rule.id: rule for rule in self._rules}
        for rule_id, window in self._windows.items():
            rule = rules_by_id.get(rule_id)
            if not rule:
                continue
            window_cutoff = now_epoch - rule.window_seconds
            while window:
                first = window[0]
                entry_epoch = first[0] if isinstance(first, (tuple, list)) else first
                if entry_epoch < window_cutoff:
                    window.popleft()
                else:
                    break

    async def evaluate_batch(self, batch: list[dict[str, Any]]) -> None:
        """
        Evaluate an ingested batch of logs against active alert rules.
        Schedules background alert dispatch tasks when thresholds are reached.
        """
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_epoch = now_utc.timestamp()

        # Prune expired window entries for all rules (PERF-04)
        self.prune_expired_windows(now_epoch)

        if not batch:
            return

        with self._lock:
            rules = list(self._rules)

        if not rules:
            return

        now_iso = now_utc.isoformat()
        min_epoch = now_epoch - 86400.0
        max_epoch = now_epoch + 300.0

        # Pre-parse and store normalized float epoch timestamps on incoming log entries (PERF-02, SEC-05)
        for entry in batch:
            entry_epoch = parse_iso_to_epoch(entry.get("timestamp"), fallback=now_epoch)

            # Clamp incoming entry epoch timestamps between now_epoch - 86400 and now_epoch + 300 (SEC-05)
            if entry_epoch < min_epoch:
                entry_epoch = min_epoch
            elif entry_epoch > max_epoch:
                entry_epoch = max_epoch
            entry["_epoch_ts"] = entry_epoch

        fired_events: list[tuple[CompiledAlertRule, list[dict[str, Any]]]] = []

        with self._lock:
            for rule in rules:
                window = self._windows.setdefault(rule.id, deque())
                # Compute window cutoff using now_epoch - rule.window_seconds (SEC-05)
                window_cutoff = now_epoch - rule.window_seconds
                needed = (rule.threshold_count * rule.window_seconds) if rule.rule_type == "rate" else rule.threshold_count
                max_window_size = max(500, min(20000, needed * 2)) if rule.rule_type == "rate" else rule.threshold_count * 2

                for entry in batch:
                    try:
                        if not rule.matches(entry):
                            continue

                        entry_epoch = entry.get("_epoch_ts", now_epoch)

                        # Evict expired entries outside the sliding window
                        while window:
                            first = window[0]
                            first_epoch = first[0] if isinstance(first, (tuple, list)) else first
                            if first_epoch < window_cutoff:
                                window.popleft()
                            else:
                                break

                        # Skip entries that fall outside the active sliding window
                        if entry_epoch < window_cutoff:
                            continue

                        if rule.rule_type == "rate":
                            # For rate rules that only track log counts, store lightweight records
                            # rather than holding entire log payload dictionaries in memory.
                            payload = {
                                "timestamp": entry.get("timestamp"),
                                "app_name": entry.get("app_name"),
                                "source_alias": entry.get("source_alias"),
                                "source_ip": entry.get("source_ip"),
                                "message": str(entry.get("message") or entry.get("raw") or "")[:200],
                            }
                        else:
                            payload = entry

                        # Append entry preserving chronological order under timestamp jitter (PERF-02)
                        if not window or entry_epoch >= (window[-1][0] if isinstance(window[-1], (tuple, list)) else window[-1]):
                            window.append((entry_epoch, payload))
                        else:
                            # Backwards linear search for insertion index to avoid full deque sorting
                            idx = len(window)
                            while idx > 0 and (window[idx - 1][0] if isinstance(window[idx - 1], (tuple, list)) else window[idx - 1]) > entry_epoch:
                                idx -= 1
                            window.insert(idx, (entry_epoch, payload))

                        # Bound maximum deque size
                        while len(window) > max_window_size:
                            window.popleft()

                        # Check threshold condition
                        if len(window) >= needed:
                            # Verify cooldown dampening via cached epoch
                            is_suppressed = bool(
                                rule.suppress_until_epoch and now_epoch < rule.suppress_until_epoch
                            )

                            if is_suppressed:
                                # Cap window to needed during cooldown to avoid memory expansion
                                while len(window) > needed:
                                    window.popleft()
                            else:
                                # Trigger alert firing
                                triggering_logs = [item[1] for item in list(window)]
                                window.clear()

                                # Update cooldown dampening in memory
                                rule.trigger_count += 1
                                rule.last_triggered_at = now_iso
                                suppress_end = now_utc + datetime.timedelta(seconds=rule.cooldown_seconds)
                                rule.suppress_until = suppress_end.isoformat()
                                rule.suppress_until_epoch = suppress_end.timestamp()

                                fired_events.append((rule, triggering_logs))
                                break  # Break out of batch loop for this rule

                    except Exception as e:
                        logger.debug(f"Error evaluating rule {rule.id}: {e}")
                        continue

        # Process fired alert events outside lock (PERF-04)
        for rule, logs in fired_events:
            # Dispatch alert notification asynchronously in background with lifecycle tracking
            task = asyncio.create_task(self._dispatch_alert(rule, logs))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def _update_rule_trigger_state(self, rule: CompiledAlertRule) -> None:
        """Persist last_triggered_at, suppress_until, and trigger_count to SQLite."""
        from app.core.config import get_db_path
        from app.api.deps import get_thread_read_connection
        effective_db = self.db_path or get_db_path()
        if not effective_db:
            return

        try:
            conn = get_thread_read_connection(effective_db)
            with conn:
                conn.execute(
                    """
                    UPDATE alert_rules
                    SET last_triggered_at = ?, suppress_until = ?, trigger_count = ?
                    WHERE id = ?
                    """,
                    (rule.last_triggered_at, rule.suppress_until, rule.trigger_count, rule.id),
                )
        except Exception as exc:
            logger.warning(f"Failed to persist trigger state for rule {rule.id}: {exc}")

    def _is_maintenance_active(self) -> bool:
        """Check if global maintenance window (on-demand or scheduled) is active in system_settings."""
        from app.core.config import get_db_path
        effective_db = self.db_path or get_db_path()
        if not effective_db:
            return False

        try:
            from app.api.deps import get_thread_read_connection
            from app.services.maintenance_service import get_maintenance_status
            conn = get_thread_read_connection(effective_db)
            status = get_maintenance_status(conn)
            return status.active
        except Exception as exc:
            logger.warning(f"Error checking maintenance window in alert evaluator: {exc}")
            return False

    async def _dispatch_alert(
        self,
        rule: CompiledAlertRule,
        triggering_logs: list[dict[str, Any]],
    ) -> None:
        """
        Process alert firing:
        1. Persist rule trigger state to SQLite non-blocking via asyncio.to_thread (PERF-04).
        2. Extract IP indicators (for security canary alerts).
        3. Perform AI diagnosis enrichment if enabled.
        4. Persist record into alert_history table non-blocking via asyncio.to_thread.
        5. Send notification via NotifierService.
        """
        # Persist rule trigger state to SQLite non-blocking in worker thread (PERF-04)
        await asyncio.to_thread(self._update_rule_trigger_state, rule)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now_utc.isoformat()

        # Extract offending IP, host, and app/container if present in triggering logs
        sample_log = ""
        extracted_ip = None
        extracted_host = None
        extracted_app = None
        if triggering_logs:
            sample_entry = triggering_logs[-1]
            if isinstance(sample_entry, dict):
                sample_log = str(sample_entry.get("message") or sample_entry.get("raw") or "")
            for log_entry in reversed(triggering_logs):
                if not isinstance(log_entry, dict):
                    continue
                if not extracted_ip:
                    msg = str(log_entry.get("message") or "")
                    cand_ip = extract_ip_from_message(msg)
                    if cand_ip:
                        extracted_ip = cand_ip
                if not extracted_host:
                    cand_host = log_entry.get("source_alias") or log_entry.get("source_ip") or log_entry.get("host")
                    if cand_host:
                        extracted_host = str(cand_host).strip()
                if not extracted_app:
                    cand_app = log_entry.get("app_name") or log_entry.get("container_name") or log_entry.get("tag")
                    if cand_app:
                        extracted_app = str(cand_app).strip()

        # Culprit frequency analysis using collections.Counter
        culprit_app: Optional[str] = None
        culprit_host: Optional[str] = None
        culprit_msg: Optional[str] = None
        measured_rate: Optional[float] = None

        if triggering_logs:
            total_logs = len(triggering_logs)
            app_counter: Counter[str] = Counter()
            host_counter: Counter[str] = Counter()
            msg_counter: Counter[str] = Counter()

            for log_entry in triggering_logs:
                if not isinstance(log_entry, dict):
                    continue
                app_val = log_entry.get("app_name") or log_entry.get("container_name") or log_entry.get("tag") or "unknown"
                host_val = log_entry.get("source_alias") or log_entry.get("source_ip") or log_entry.get("host") or "unknown"
                msg_val = str(log_entry.get("message") or log_entry.get("raw") or "")[:150].strip()

                app_counter[str(app_val)] += 1
                host_counter[str(host_val)] += 1
                if msg_val:
                    msg_counter[msg_val] += 1

            if app_counter:
                top_app, app_count = app_counter.most_common(1)[0]
                app_pct = round((app_count / total_logs) * 100)
                culprit_app = f"{top_app} ({app_count}/{total_logs}, {app_pct}%)"
                if rule.rule_type == "rate":
                    extracted_app = top_app
                    for log_entry in reversed(triggering_logs):
                        if not isinstance(log_entry, dict):
                            continue
                        cand_app = log_entry.get("app_name") or log_entry.get("container_name") or log_entry.get("tag") or "unknown"
                        if str(cand_app) == str(top_app):
                            sample_log = str(log_entry.get("message") or log_entry.get("raw") or "")
                            break
            if host_counter:
                top_host, host_count = host_counter.most_common(1)[0]
                host_pct = round((host_count / total_logs) * 100)
                culprit_host = f"{top_host} ({host_count}/{total_logs}, {host_pct}%)"
                if rule.rule_type == "rate":
                    extracted_host = top_host
            if msg_counter:
                top_msg, msg_count = msg_counter.most_common(1)[0]
                msg_pct = round((msg_count / total_logs) * 100)
                culprit_msg = f"{top_msg} (x{msg_count}, {msg_pct}%)"

            measured_rate = round(total_logs / max(1.0, float(rule.window_seconds)), 1)

        incident_summary = None
        ai_success = False
        ai_error_note = None
        actual_ai_model = None
        saved_audit_id = None
        ai_enabled = False

        # Perform AI enrichment if enabled on the rule
        if rule.ai_enrichment and triggering_logs:
            try:
                from app.core.config import get_db_path
                from app.api.deps import run_db_query
                from app.core.config import DEFAULT_AI_MODEL
                from app.services.ai_service import read_ai_settings
                from app.services.ai_engine import execute_ai_analysis, format_prompt_log_line

                ai_settings, _ = await run_db_query(read_ai_settings, custom_db_path=self.db_path)

                ai_enabled_raw = ai_settings.get("ai_enabled")
                if ai_enabled_raw is not None:
                    ai_enabled = ai_enabled_raw.strip().lower() not in ("0", "false", "no", "off")
                else:
                    ai_enabled = True

                if not ai_enabled:
                    logger.info(
                        f"AI enrichment skipped for alert rule '{rule.name}' because AI features are disabled."
                    )
                    ai_success = False
                    if rule.rule_type == "rate":
                        incident_summary = (
                            f"Log storm detected: {len(triggering_logs)} logs in {rule.window_seconds}s ({measured_rate} logs/s).\n\n"
                            f"### Primary Culprits\n"
                            f"- Top Service / App: {culprit_app or 'Unknown'}\n"
                            f"- Top Host / Source: {culprit_host or 'Unknown'}\n"
                            f"- Top Pattern: {culprit_msg or 'N/A'}"
                        )
                    else:
                        incident_summary = f"Alert triggered with {len(triggering_logs)} matching event(s)."
                else:
                    ai_provider = (ai_settings.get("ai_provider") or "gemini").lower()
                    default_model = DEFAULT_AI_MODEL if ai_provider == "gemini" else ("gpt-4o" if ai_provider == "openai" else ("claude-sonnet-4-6" if ai_provider == "anthropic" else "llama3.2"))
                    ai_model = ai_settings.get(f"ai_model_{ai_provider}") or ai_settings.get("ai_model") or default_model
                    ai_key = ai_settings.get(f"ai_api_key_{ai_provider}") or ai_settings.get("ai_api_key", "")
                    ai_base = ai_settings.get(f"ai_base_url_{ai_provider}") or ai_settings.get("ai_base_url")
                    fallback_str = ai_settings.get(f"ai_fallback_models_{ai_provider}") or ai_settings.get("ai_fallback_models", "")
                    fallback_models = [m.strip() for m in fallback_str.split(",") if m.strip()]

                    # Format and redact log window
                    raw_lines = [
                        format_prompt_log_line(
                            timestamp=l.get("timestamp"),
                            source=l.get("source_alias") or l.get("source_ip"),
                            app_name=l.get("app_name"),
                            message=l.get("message", ""),
                            severity=l.get("severity"),
                            raw=l.get("raw"),
                        )
                        for l in triggering_logs[-50:]  # Cap at recent 50 logs
                    ]
                    redacted_lines = redact(raw_lines)
                    redacted_text = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)

                    if rule.rule_type == "rate":
                        threshold_line = f"- Threshold: {rule.threshold_count} logs/s in {rule.window_seconds}s (Measured: {measured_rate} logs/s)"
                        culprit_section = (
                            f"### Primary Culprits\n"
                            f"- Top Service / App: {culprit_app or 'Unknown'}\n"
                            f"- Top Host / Source: {culprit_host or 'Unknown'}\n"
                            f"- Top Pattern: {culprit_msg or 'N/A'}\n\n"
                        )
                    else:
                        threshold_line = f"- Threshold: {rule.threshold_count} matches in {rule.window_seconds}s"
                        culprit_section = ""

                    prompt = (
                        f"### Security / Operations Incident Alert\n"
                        f"- Alert Rule: {rule.name}\n"
                        f"- Rule Type: {rule.rule_type}\n"
                        f"- Host / Source: {extracted_host or 'Unknown'}\n"
                        f"- App / Container: {extracted_app or 'Unknown'}\n"
                        f"{threshold_line}\n"
                        f"- Offending IP: {extracted_ip or 'None detected'}\n\n"
                        f"{culprit_section}"
                        f"### Redacted Log Stream (Chronological)\n"
                        f"```\n{redacted_text}\n```\n\n"
                        f"Review this incident and provide structured Summary, Root Cause, and Actionable Remediation."
                    )

                    logger.info(
                        f"Starting AI incident analysis for rule '{rule.name}' with primary model '{ai_model or 'default'}' "
                        f"and fallback models {fallback_models}"
                    )

                    ai_res = await execute_ai_analysis(
                        provider=ai_provider,
                        model=ai_model,
                        api_key=ai_key,
                        base_url=ai_base,
                        source_alias=triggering_logs[0].get("source_alias") or "alert",
                        app_name=triggering_logs[0].get("app_name") or "alert",
                        redacted_logs=redacted_text,
                        log_count=len(triggering_logs),
                        prompt_override=prompt,
                        fallback_models=fallback_models,
                    )
                    if len(ai_res) == 11:
                        summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used, actual_model, fallback_attempts = ai_res
                    else:
                        summary, root_cause, remediation = ai_res[:3]
                        raw_response = ""
                        prompt_sent = prompt
                        tokens_in = 0
                        tokens_out = 0
                        tokens_thoughts = 0
                        tokens_used = 0
                        actual_model = ai_model
                        fallback_attempts = []

                    actual_ai_model = actual_model

                    if fallback_attempts:
                        logger.info(
                            f"AI enrichment for alert '{rule.name}' succeeded via fallback model '{actual_model}' "
                            f"after failovers: {fallback_attempts}"
                        )

                    diag_parts = []
                    if summary and summary.strip():
                        diag_parts.append(summary.strip())
                    if root_cause and root_cause.strip():
                        diag_parts.append(f"### Root Cause\n{root_cause.strip()}")
                    if remediation and remediation.strip():
                        diag_parts.append(f"### Remediation\n{remediation.strip()}")
                    incident_summary = "\n\n".join(diag_parts) if diag_parts else (summary or "Incident review complete.")
                    ai_success = True

                    from app.services.ai_service import save_diagnosis_audit

                    audit_source_alias = (
                        extracted_host
                        or (triggering_logs[0].get("source_alias") if triggering_logs else None)
                        or (triggering_logs[0].get("source_ip") if triggering_logs else None)
                        or "alert"
                    )
                    audit_app_name = (
                        extracted_app
                        or (triggering_logs[0].get("app_name") if triggering_logs else None)
                        or "alert"
                    )

                    try:
                        saved_audit_id = await save_diagnosis_audit(
                            source_alias=audit_source_alias,
                            app_name=audit_app_name,
                            log_count=len(triggering_logs),
                            user_context=f"Alert Rule: {rule.name}",
                            actual_model=actual_ai_model,
                            prompt_sent=prompt_sent or prompt,
                            raw_response=incident_summary or raw_response,
                            tokens_in=tokens_in or 0,
                            tokens_out=tokens_out or 0,
                            tokens_thoughts=tokens_thoughts or 0,
                            tokens_used=tokens_used or 0,
                            system_prompt=ai_settings.get("ai_system_prompt") or None,
                            trigger_source="alert",
                            custom_db_path=self.db_path,
                        )
                    except Exception as audit_err:
                        logger.warning(
                            f"Failed to record AI audit log for alert rule '{rule.name}': {audit_err}"
                        )

            except Exception as ai_err:
                logger.warning(
                    f"AI enrichment failed for alert rule '{rule.name}' after attempting candidate models: {ai_err}"
                )
                ai_success = False
                clean_err = str(ai_err).splitlines()[0]
                if len(clean_err) > 250:
                    clean_err = clean_err[:247] + "..."
                incident_summary = f"AI analysis failed: {clean_err}"
                ai_error_note = clean_err
        else:
            if rule.rule_type == "rate":
                incident_summary = (
                    f"Log storm detected: {len(triggering_logs)} logs in {rule.window_seconds}s ({measured_rate} logs/s).\n\n"
                    f"### Primary Culprits\n"
                    f"- Top Service / App: {culprit_app or 'Unknown'}\n"
                    f"- Top Host / Source: {culprit_host or 'Unknown'}\n"
                    f"- Top Pattern: {culprit_msg or 'N/A'}"
                )
            else:
                incident_summary = f"Alert triggered with {len(triggering_logs)} matching event(s)."

        # Record event in alert_history table via worker thread
        def _execute_insert(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO alert_history
                (rule_id, rule_name, channel_id, trigger_count, sample_log, incident_summary, ai_enrichment, ai_model, ai_audit_id, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    rule.name,
                    rule.channel_id,
                    len(triggering_logs),
                    sample_log[:1000],
                    incident_summary,
                    int(rule.ai_enrichment and ai_enabled),
                    actual_ai_model,
                    saved_audit_id,
                    now_iso,
                ),
            )

        try:
            from app.api.deps import run_db_query
            await run_db_query(_execute_insert, custom_db_path=self.db_path)
        except Exception as db_err:
            logger.error(f"Failed to record alert history for rule {rule.id}: {db_err}")

        # Check global maintenance window before dispatching external notification
        in_maintenance = await asyncio.to_thread(self._is_maintenance_active)
        if in_maintenance:
            logger.info(
                f"Global maintenance window active: skipping notification dispatch for alert rule '{rule.name}'"
            )
            return

        # Construct clean push notification payload with secrets redacted
        if rule.rule_type == "rate":
            notification_title = str(redact(f"LogShed: [Log Storm] {rule.name} ({measured_rate} logs/s)"))
        else:
            notification_title = str(redact(f"LogShed: {rule.name}"))

        redacted_sample_log = str(redact(sample_log)) if sample_log else ""
        truncated_log = format_sample_log_for_alert(redacted_sample_log)
        clean_log = truncated_log.replace("```", "").replace("`", "").strip()

        if rule.rule_type == "rate":
            body_lines = [
                f"**Host:** {culprit_host or extracted_host or 'Unknown'}",
                f"**App:** {culprit_app or extracted_app or 'Unknown'}",
                f"**Rate:** {measured_rate} logs/s (Threshold: {rule.threshold_count} logs/s in {rule.window_seconds}s)",
            ]
            if culprit_msg:
                body_lines.append(f"**Top Pattern:** {culprit_msg}")
            if clean_log:
                body_lines.append(f"**Sample Log:** {clean_log}")
        else:
            body_lines = [
                f"**Host:** {extracted_host or 'Unknown'}",
                f"**App:** {extracted_app or 'Unknown'}",
                f"**Log:** {clean_log}",
            ]

        if rule.ai_enrichment and ai_enabled:
            if ai_success and summary:
                redacted_summary = str(redact(summary))
                clean_summary = strip_markdown(redacted_summary)
                if len(clean_summary) > 200:
                    clean_summary = clean_summary[:197] + "..."
                body_lines.append(f"**AI Analysis:** {clean_summary}")
            elif ai_error_note:
                redacted_error = str(redact(ai_error_note))
                body_lines.append(f"**AI Analysis:** Unavailable ({redacted_error})")

        from app.core.config import get_cached_setting
        app_url = str(get_cached_setting("app_url", "")).strip().rstrip("/")
        if app_url:
            body_lines.append(f"**Link:** {app_url}/rules/history")

        notification_body = "\n".join(body_lines)

        notifier = get_notifier()
        try:
            await notifier.send_notification(
                title=notification_title,
                body=notification_body,
                channel_id=rule.channel_id,
            )
        except Exception as notify_err:
            logger.error(f"Failed to dispatch alert notification for {rule.name}: {notify_err}")

    async def stop(self) -> None:
        """Wait for any in-flight alert dispatch tasks to complete."""
        if self._pending_tasks:
            tasks = list(self._pending_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)


# Module-level singleton
_alert_evaluator: Optional[AlertEvaluator] = None
_evaluator_lock = threading.Lock()


def get_alert_evaluator() -> AlertEvaluator:
    """Return the global AlertEvaluator singleton instance."""
    global _alert_evaluator
    with _evaluator_lock:
        if _alert_evaluator is None:
            try:
                from app.core.config import get_db_path
                _alert_evaluator = AlertEvaluator(get_db_path())
            except Exception:
                _alert_evaluator = AlertEvaluator()
        elif not _alert_evaluator.db_path:
            try:
                from app.core.config import get_db_path
                _alert_evaluator.db_path = get_db_path()
            except Exception:
                pass
        return _alert_evaluator


def init_alert_evaluator(db_path: Union[str, Path]) -> AlertEvaluator:
    """Initialize or update the global AlertEvaluator singleton with database path."""
    global _alert_evaluator
    with _evaluator_lock:
        if _alert_evaluator is None:
            _alert_evaluator = AlertEvaluator(db_path)
        else:
            _alert_evaluator.db_path = Path(db_path)
            _alert_evaluator.reload_rules()
        return _alert_evaluator
