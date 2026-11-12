"""
Alert Engine Service for LogShed.

Provides in-memory sliding-window timestamp deques for threshold rules, match counts for
rate rules (rate_window.py), regex and keyword pattern matching, and cooldown flap dampening (suppress_until).
Integrates with QueueConsumer batch processing, redactor, AI diagnosis, and notifier.
"""

import asyncio
from collections import deque
from contextlib import nullcontext
import datetime
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Optional, Union

import regex

from app.core.pipeline import MAX_FUTURE_TIMESTAMP_SKEW_SECONDS, SKIP_ALERT_RULES_KEY
from app.core.redactor import redact
from app.core.regex_validator import (
    RuleTimeoutTracker,
    compile_user_pattern,
    describe_rule_timeout_limit,
    user_pattern_search,
)
from app.core.rule_index import RuleIndex, entry_key, exact_match_key
from app.core.utils import compile_wildcard, parse_iso_to_epoch
from app.services.notifier import get_notifier
from app.services.alert_incident import (
    AiEnrichment,
    AlertFacts,
    ai_incident_summary,
    build_alert_prompt,
    collect_alert_facts,
    format_alert_notification,
    plain_incident_summary,
    short_error_note,
)
from app.services.rate_window import RateSnapshot, RateWindow
from app.services.rule_auto_disable import AutoDisableQueue

logger = logging.getLogger(__name__)

# Characters that make an alert pattern a regular expression; anything else is plain text
_REGEX_METACHARS_BEFORE_BACKSLASH = frozenset("*+?[](){}^$|")
_REGEX_METACHARS = _REGEX_METACHARS_BEFORE_BACKSLASH | {"\\"}


# A threshold window keeps whole entries only for its newest matches: the alert dispatch reads the
# newest as the notification sample and at most this many as AI context; older ones keep the light record.
FULL_ENTRIES_KEPT = 50


def _rate_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """The light record a rate rule (and a threshold rule, past its newest matches) keeps of a match."""
    return {
        "id": entry.get("id"),
        "timestamp": entry.get("timestamp"),
        "app_name": entry.get("app_name"),
        "source_alias": entry.get("source_alias"),
        "source_ip": entry.get("source_ip"),
        "message": str(entry.get("message") or entry.get("raw") or "")[:200],
    }


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

        # Pre-split comma-delimited filter_app into a tuple of stripped, lowercased strings
        if self.filter_app and self.filter_app.strip():
            self._filter_apps = tuple(
                p.strip().lower() for p in self.filter_app.split(",") if p.strip()
            )
        else:
            self._filter_apps = ()
        self._filter_app_matches = tuple(compile_wildcard(p) for p in self._filter_apps)

        self.compiled_regex: Optional[regex.Pattern] = None
        self._match_pattern_lower: Optional[str] = None
        self._has_regex_metachars: bool = False
        # Set once when the pattern fails validation or compilation; such a rule never matches
        self._invalid: bool = False
        # Set by AlertEvaluator so pattern timeouts count towards disabling the rule automatically
        self.on_pattern_timeout: Optional[Callable[[], None]] = None
        self._timeout_label = f"alert rule {self.id} ({self.name})"
        if self.match_pattern and self.match_pattern.strip() and self.match_pattern.strip() != "*":
            clean_pat = self.match_pattern.strip()
            self._match_pattern_lower = clean_pat.lower()
            self._has_regex_metachars = any(c in _REGEX_METACHARS for c in clean_pat)
            if self._has_regex_metachars:
                try:
                    self.compiled_regex = compile_user_pattern(clean_pat, re.IGNORECASE)
                except Exception as exc:
                    self.compiled_regex = None
                    if not any(c in _REGEX_METACHARS_BEFORE_BACKSLASH for c in clean_pat):
                        # Only a backslash made this a regex (such as C:\Temp): keep matching it as text
                        logger.warning(
                            "Alert rule %s (%s) pattern '%s' is not a valid regular expression; "
                            "matching it as plain text (%s)",
                            self.id,
                            self.name,
                            clean_pat,
                            exc,
                        )
                        self._has_regex_metachars = False
                    else:
                        logger.warning(
                            "Alert rule %s pattern '%s' failed regex validation: %s",
                            self.id,
                            clean_pat,
                            exc,
                        )
                        self._invalid = True

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
            app_name = entry.get("app_name")
            if not app_name:
                return False
            app_key = app_name.lower().strip()
            if not any(match(app_key) for match in self._filter_app_matches):
                return False

        # 2. Severity filter check (syslog 0-7, lower is more severe)
        if self.filter_severity is not None:
            sev = entry.get("severity")
            actual_sev = 6 if sev is None else sev
            if actual_sev > self.filter_severity:
                return False

        # 3. Message pattern check (prioritize substring matching when no regex metacharacters)
        if self._match_pattern_lower:
            if self._invalid:
                return False

            message = str(entry.get("message") or "")
            raw = str(entry.get("raw") or "")

            if not self._has_regex_metachars:
                pat_lower = self._match_pattern_lower
                if pat_lower not in message.lower() and (
                    # Scanning identical text again cannot change the result
                    not raw or raw == message or pat_lower not in raw.lower()
                ):
                    return False
            else:
                if self.compiled_regex is None:
                    return False
                label = self._timeout_label
                on_timeout = self.on_pattern_timeout
                if user_pattern_search(self.compiled_regex, message, rule_label=label, on_timeout=on_timeout):
                    return True
                # Searching identical text again cannot change the result
                if not raw or raw == message:
                    return False
                if not user_pattern_search(self.compiled_regex, raw, rule_label=label, on_timeout=on_timeout):
                    return False

        return True


def _alert_rule_exact_keys(rule: CompiledAlertRule) -> Optional[tuple[str, tuple[str, ...]]]:
    """The exact apps an alert rule is limited to, or None when it must see every entry."""
    if not rule._filter_apps:
        return None
    apps = tuple(exact_match_key(app) for app in rule._filter_apps)
    if not all(apps):
        return None
    return ("app", apps)


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
        # Built from self._rules on first use after each change of the list
        self._index: Optional[RuleIndex[CompiledAlertRule]] = None
        # A deque of (epoch, entry) per threshold rule (whole entries only for the newest
        # FULL_ENTRIES_KEPT, light records before them), a RateWindow per rate rule
        self._windows: dict[int, Union[deque[tuple[float, dict[str, Any]]], RateWindow]] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        self._lock = threading.Lock()
        # Automatic disabling of rules whose pattern keeps timing out.
        # Lock order: self._lock, then the queue's own lock.
        self._timeout_tracker = RuleTimeoutTracker()
        self._auto_disables = AutoDisableQueue("alert_rules", "alert rule", logger, "with the next batch")
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
            from app.api.deps import get_thread_connection
            c = get_thread_connection(effective_db)
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
                display_order=r[15],
            )
            if rule.compiled_regex is not None:
                rule.on_pattern_timeout = self._make_timeout_callback(rule)
            compiled.append(rule)
            active_ids.add(rule.id)

        with self._lock:
            compiled = self._auto_disables.without_pending(compiled)
            active_ids = {rule.id for rule in compiled}
            self._rules = compiled
            # Clean up windows for rules that are no longer active
            stale_ids = [rid for rid in self._windows if rid not in active_ids]
            for rid in stale_ids:
                del self._windows[rid]

            for rule in compiled:
                self._window_for(rule)

    def _window_for(self, rule: CompiledAlertRule) -> Union[deque[tuple[float, dict[str, Any]]], RateWindow]:
        """The rule's window, new when the rule is new or its type or rate window length changed."""
        window = self._windows.get(rule.id)
        if rule.rule_type == "rate":
            if not isinstance(window, RateWindow) or window.window_seconds != rule.window_seconds:
                window = self._windows[rule.id] = RateWindow(rule.window_seconds)
        elif not isinstance(window, deque):
            window = self._windows[rule.id] = deque()
        return window

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
            if isinstance(window, RateWindow):
                window.prune(window_cutoff)
                continue
            while window and window[0][0] < window_cutoff:
                window.popleft()

    async def evaluate_batch(self, batch: list[dict[str, Any]]) -> None:
        """
        Evaluate an ingested batch of logs against active alert rules.
        Schedules background alert dispatch tasks when thresholds are reached.
        Internal LogShed logs marked with SKIP_ALERT_RULES_KEY (alerting, notification and AI logs)
        are never evaluated, so a rule cannot re-trigger itself through the logs its own actions write.

        The matching runs in a worker thread, so the event loop keeps reading syslog sockets and
        serving requests meanwhile. Entries must not change while it runs (the queue consumer
        broadcasts them before calling this); alert dispatch then starts on the event loop.
        """
        fired_events = await asyncio.to_thread(self._match_batch, batch)

        # Store automatic disables off the hot path
        if self._auto_disables.has_queued():
            store_task = asyncio.create_task(asyncio.to_thread(self._store_auto_disabled_rules))
            self._pending_tasks.add(store_task)
            store_task.add_done_callback(self._pending_tasks.discard)

        # Process fired alert events outside lock
        for rule, logs, rate in fired_events:
            # Dispatch alert notification asynchronously in background with lifecycle tracking
            task = asyncio.create_task(self._dispatch_alert(rule, logs, rate))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def _match_batch(
        self, batch: list[dict[str, Any]]
    ) -> list[tuple[CompiledAlertRule, list[dict[str, Any]], Optional[RateSnapshot]]]:
        """
        Blocking part of evaluate_batch: update the rule windows with the batch and return the
        rules that fired, in rule order, each with its triggering logs (for a rate rule, the
        newest matches) and, for a rate rule, the counts its window held.
        """
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_epoch = now_utc.timestamp()

        # Prune expired window entries for all rules
        self.prune_expired_windows(now_epoch)

        batch = [entry for entry in batch if not entry.get(SKIP_ALERT_RULES_KEY)]
        if not batch:
            return []

        with self._lock:
            rules = list(self._rules)
            index = self._index
            if index is None or index.source is not self._rules:
                index = self._index = RuleIndex(self._rules, _alert_rule_exact_keys)

        if not rules:
            return []

        # Entries per rule with an exact app filter, in batch order; other rules see the whole batch
        entries_by_rule: dict[int, list[dict[str, Any]]] = {}
        for entry in batch:
            for candidate in index.keyed_candidates((("app", entry_key(entry.get("app_name"))),)):
                entries_by_rule.setdefault(id(candidate), []).append(entry)

        now_iso = now_utc.isoformat()
        min_epoch = now_epoch - 86400.0
        max_epoch = now_epoch + MAX_FUTURE_TIMESTAMP_SKEW_SECONDS

        # Pre-parse and store normalized float epoch timestamps on incoming log entries
        for entry in batch:
            entry_epoch = parse_iso_to_epoch(entry.get("timestamp"), fallback=now_epoch)

            # Clamp entry timestamps to between a day ago and the future skew the ingest paths allow
            if entry_epoch < min_epoch:
                entry_epoch = min_epoch
            elif entry_epoch > max_epoch:
                entry_epoch = max_epoch
            entry["_epoch_ts"] = entry_epoch

        fired_events: list[tuple[CompiledAlertRule, list[dict[str, Any]], Optional[RateSnapshot]]] = []

        with self._lock:
            for rule in rules:
                window = self._window_for(rule)
                # Compute window cutoff using now_epoch - rule.window_seconds
                window_cutoff = now_epoch - rule.window_seconds
                rule_entries = batch if index.is_always(rule) else entries_by_rule.get(id(rule), ())

                if isinstance(window, RateWindow):
                    snapshot = self._count_rate_matches(rule, window, rule_entries, window_cutoff, now_epoch)
                    if snapshot is not None:
                        self._mark_fired(rule, now_utc, now_iso)
                        fired_events.append((rule, snapshot.samples, snapshot))
                    continue

                needed = rule.threshold_count
                max_window_size = rule.threshold_count * 2

                for entry in rule_entries:
                    try:
                        if not rule.matches(entry):
                            continue

                        entry_epoch = entry.get("_epoch_ts", now_epoch)

                        # Evict expired entries outside the sliding window
                        while window and window[0][0] < window_cutoff:
                            window.popleft()

                        # Skip entries that fall outside the active sliding window
                        if entry_epoch < window_cutoff:
                            continue

                        # Append entry preserving chronological order under timestamp jitter
                        if not window or entry_epoch >= window[-1][0]:
                            window.append((entry_epoch, entry))
                        else:
                            # Backwards linear search for insertion index to avoid full deque sorting
                            idx = len(window)
                            while idx > 0 and window[idx - 1][0] > entry_epoch:
                                idx -= 1
                            if len(window) - idx >= FULL_ENTRIES_KEPT:
                                window.insert(idx, (entry_epoch, _rate_payload(entry)))
                            else:
                                window.insert(idx, (entry_epoch, entry))

                        # The match that just left the newest FULL_ENTRIES_KEPT keeps only the light record
                        if len(window) > FULL_ENTRIES_KEPT:
                            older_epoch, older_entry = window[-FULL_ENTRIES_KEPT - 1]
                            window[-FULL_ENTRIES_KEPT - 1] = (older_epoch, _rate_payload(older_entry))

                        # Bound maximum deque size
                        while len(window) > max_window_size:
                            window.popleft()

                        # Check threshold condition
                        if len(window) >= needed:
                            if self._is_suppressed(rule, now_epoch):
                                # Cap window to needed during cooldown to avoid memory expansion
                                while len(window) > needed:
                                    window.popleft()
                            else:
                                # Trigger alert firing
                                triggering_logs = [item[1] for item in list(window)]
                                window.clear()
                                self._mark_fired(rule, now_utc, now_iso)
                                fired_events.append((rule, triggering_logs, None))
                                break  # Break out of batch loop for this rule

                    except Exception as e:
                        logger.debug(f"Error evaluating rule {rule.id}: {e}")
                        continue

        return fired_events

    def _count_rate_matches(
        self,
        rule: CompiledAlertRule,
        window: RateWindow,
        entries: Any,
        window_cutoff: float,
        now_epoch: float,
    ) -> Optional[RateSnapshot]:
        """
        Count the rule's matches among entries; when the window reaches threshold_count logs/s
        outside the cooldown, return what it held and empty it. The rest of the batch is then
        not counted, as for threshold rules. During the cooldown the window keeps counting, so
        the rule fires again at the first match after it while the rate stays high.
        """
        needed = rule.threshold_count * rule.window_seconds
        for entry in entries:
            try:
                if not rule.matches(entry):
                    continue
                entry_epoch = entry.get("_epoch_ts", now_epoch)
                # Skip entries that fall outside the active sliding window
                if entry_epoch < window_cutoff:
                    continue
                window.add(entry_epoch, _rate_payload(entry))
                if window.total >= needed and not self._is_suppressed(rule, now_epoch):
                    snapshot = window.snapshot()
                    window.clear()
                    return snapshot
            except Exception as e:
                logger.debug(f"Error evaluating rule {rule.id}: {e}")
        return None

    @staticmethod
    def _is_suppressed(rule: CompiledAlertRule, now_epoch: float) -> bool:
        """True during the rule's cooldown (cached epoch)."""
        return bool(rule.suppress_until_epoch and now_epoch < rule.suppress_until_epoch)

    @staticmethod
    def _mark_fired(rule: CompiledAlertRule, now_utc: datetime.datetime, now_iso: str) -> None:
        """Count the trigger and start the cooldown in memory (stored by the dispatch)."""
        rule.trigger_count += 1
        rule.last_triggered_at = now_iso
        suppress_end = now_utc + datetime.timedelta(seconds=rule.cooldown_seconds)
        rule.suppress_until = suppress_end.isoformat()
        rule.suppress_until_epoch = suppress_end.timestamp()

    def _make_timeout_callback(self, rule: CompiledAlertRule) -> Callable[[], None]:
        """Return the callback a rule calls each time its pattern times out."""
        def _on_timeout() -> None:
            self._note_pattern_timeout(rule)
        return _on_timeout

    def _note_pattern_timeout(self, rule: CompiledAlertRule) -> None:
        """
        Count a pattern timeout for the rule. On reaching the limit (once per rule per process),
        switch the rule off in memory at once and queue the database write, which evaluate_batch
        schedules in a worker thread. Runs on the ingest path, so it never touches the database
        and never takes self._lock (evaluate_batch may already hold it).
        """
        if not self._timeout_tracker.record(rule.id):
            return
        rule.is_enabled = False
        self._auto_disables.add(rule.id)
        logger.error(
            "Alert rule %s (%s) disabled automatically: %s. Simplify the pattern, then enable the rule again.",
            rule.id,
            rule.name,
            describe_rule_timeout_limit(),
        )

    def _store_auto_disabled_rules(self) -> None:
        """Store is_enabled = 0 for automatically disabled rules, then reload the rule cache (worker thread)."""
        from app.core.config import get_db_path
        from app.api.deps import get_thread_connection
        effective_db = self.db_path or get_db_path()
        if not effective_db:
            return
        # A failed write is queued again for the next batch. The rule stays off in memory meanwhile;
        # if every attempt fails, the next start runs it again.
        if not self._auto_disables.store(lambda: nullcontext(get_thread_connection(effective_db))):
            return
        try:
            self.reload_rules()
        except Exception as exc:
            logger.warning(f"Failed to reload alert rules after automatic disables: {exc}")

    def _update_rule_trigger_state(self, rule: CompiledAlertRule) -> None:
        """Persist last_triggered_at, suppress_until, and trigger_count to SQLite."""
        from app.core.config import get_db_path
        from app.api.deps import get_thread_connection
        effective_db = self.db_path or get_db_path()
        if not effective_db:
            return

        try:
            conn = get_thread_connection(effective_db)
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
            from app.api.deps import get_thread_connection
            from app.services.maintenance_service import get_maintenance_status
            conn = get_thread_connection(effective_db)
            status = get_maintenance_status(conn)
            return status.active
        except Exception as exc:
            logger.warning(f"Error checking maintenance window in alert evaluator: {exc}")
            return False

    async def _dispatch_alert(
        self,
        rule: CompiledAlertRule,
        triggering_logs: list[dict[str, Any]],
        rate: Optional[RateSnapshot] = None,
    ) -> None:
        """
        Process alert firing: store the rule's trigger state, read the facts of the triggering
        logs, ask the AI for a diagnosis if the rule wants one, record the alert in history and,
        outside maintenance, send the notification.
        """
        await asyncio.to_thread(self._update_rule_trigger_state, rule)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        facts = collect_alert_facts(rule, triggering_logs, rate)
        if rule.ai_enrichment and triggering_logs:
            ai = await self._enrich_with_ai(rule, triggering_logs, facts)
        else:
            ai = AiEnrichment(incident_summary=plain_incident_summary(rule, facts))

        # The notification links to this record, or to the history list when it was not saved
        history_id = await self._record_alert_history(rule, facts, ai, now_iso)

        if await asyncio.to_thread(self._is_maintenance_active):
            logger.info(
                f"Global maintenance window active: skipping notification dispatch for alert rule '{rule.name}'"
            )
            return

        from app.core.config import get_cached_setting
        app_url = str(get_cached_setting("app_url", "")).strip().rstrip("/")
        title, body = format_alert_notification(rule, facts, ai, history_id, app_url)

        notifier = get_notifier()
        try:
            await notifier.send_notification(title=title, body=body, channel_id=rule.channel_id)
        except Exception as notify_err:
            logger.error(f"Failed to dispatch alert notification for {rule.name}: {notify_err}")

    async def _enrich_with_ai(
        self, rule: CompiledAlertRule, triggering_logs: list[dict[str, Any]], facts: AlertFacts
    ) -> AiEnrichment:
        """Ask the configured AI provider for a diagnosis and record it in the AI audit log."""
        ai = AiEnrichment()
        try:
            from app.api.deps import run_db_query
            from app.services.ai_service import read_ai_settings, resolve_ai_provider_settings

            ai_settings, _ = await run_db_query(read_ai_settings, custom_db_path=self.db_path)
            provider_settings = resolve_ai_provider_settings(ai_settings)
            ai.enabled = provider_settings["ai_enabled"]

            if not ai.enabled:
                logger.info(
                    f"AI enrichment skipped for alert rule '{rule.name}' because AI features are disabled."
                )
                ai.incident_summary = plain_incident_summary(rule, facts)
                return ai

            from app.services.ai_engine import execute_ai_analysis

            redacted_text = await self._redacted_prompt_logs(triggering_logs)
            prompt = build_alert_prompt(rule, facts, redacted_text)
            fallback_models = provider_settings["fallback_models"]
            logger.info(
                f"Starting AI incident analysis for rule '{rule.name}' with primary model "
                f"'{provider_settings['model'] or 'default'}' and fallback models {fallback_models}"
            )

            ai_result = await execute_ai_analysis(
                provider=provider_settings["provider"],
                model=provider_settings["model"],
                api_key=provider_settings["api_key"],
                base_url=provider_settings["base_url"],
                source_alias=triggering_logs[0].get("source_alias") or "alert",
                app_name=triggering_logs[0].get("app_name") or "alert",
                redacted_logs=redacted_text,
                log_count=facts.log_count,
                prompt_override=prompt,
                fallback_models=fallback_models,
                timeout=provider_settings["ai_timeout"],
                thinking_level=provider_settings["ai_thinking_level"],
            )
            ai.model = ai_result.model_used
            if ai_result.fallback_attempts:
                logger.info(
                    f"AI enrichment for alert '{rule.name}' succeeded via fallback model '{ai.model}' "
                    f"after failovers: {ai_result.fallback_attempts}"
                )

            ai.summary = ai_result.summary
            ai.incident_summary = ai_incident_summary(ai_result)
            ai.success = True
            ai.audit_id = await self._save_alert_audit(rule, triggering_logs, facts, ai, ai_result, prompt, ai_settings)

        except Exception as ai_err:
            logger.warning(
                f"AI enrichment failed for alert rule '{rule.name}' after attempting candidate models: {ai_err}"
            )
            ai.success = False
            ai.error_note = short_error_note(ai_err)
            ai.incident_summary = f"AI analysis failed: {ai.error_note}"
        return ai

    @staticmethod
    async def _redacted_prompt_logs(triggering_logs: list[dict[str, Any]]) -> str:
        """The newest whole entries as prompt lines, redacted, so no line can close the prompt's code fence."""
        from app.services.ai_engine import escape_code_fences, format_prompt_log_line

        raw_lines = [
            format_prompt_log_line(
                timestamp=entry.get("timestamp"),
                source=entry.get("source_alias") or entry.get("source_ip"),
                app_name=entry.get("app_name"),
                message=entry.get("message", ""),
                severity=entry.get("severity"),
                raw=entry.get("raw"),
            )
            for entry in triggering_logs[-FULL_ENTRIES_KEPT:]  # Only the newest are whole entries
        ]
        redacted_lines = await asyncio.to_thread(redact, raw_lines)
        redacted_text = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)
        return escape_code_fences(redacted_text)

    async def _save_alert_audit(
        self,
        rule: CompiledAlertRule,
        triggering_logs: list[dict[str, Any]],
        facts: AlertFacts,
        ai: AiEnrichment,
        ai_result: Any,
        prompt: str,
        ai_settings: dict[str, str],
    ) -> Optional[int]:
        """Record the diagnosis in the AI audit log; None when that fails (the alert still goes out)."""
        from app.services.ai_service import save_diagnosis_audit

        first = triggering_logs[0]
        try:
            return await save_diagnosis_audit(
                source_alias=facts.host or first.get("source_alias") or first.get("source_ip") or "alert",
                app_name=facts.app or first.get("app_name") or "alert",
                log_count=facts.log_count,
                user_context=f"Alert Rule: {rule.name}",
                actual_model=ai.model,
                prompt_sent=ai_result.prompt_sent or prompt,
                raw_response=ai.incident_summary or ai_result.raw_response,
                tokens_in=ai_result.tokens_in or 0,
                tokens_out=ai_result.tokens_out or 0,
                tokens_thoughts=ai_result.tokens_thoughts or 0,
                tokens_used=ai_result.tokens_used or 0,
                system_prompt=ai_settings.get("ai_system_prompt") or None,
                trigger_source="alert",
                custom_db_path=self.db_path,
            )
        except Exception as audit_err:
            logger.warning(f"Failed to record AI audit log for alert rule '{rule.name}': {audit_err}")
            return None

    async def _record_alert_history(
        self, rule: CompiledAlertRule, facts: AlertFacts, ai: AiEnrichment, now_iso: str
    ) -> Optional[int]:
        """Insert the alert_history row; its id, or None when the insert failed."""

        def _execute_insert(conn: sqlite3.Connection) -> Optional[int]:
            cur = conn.execute(
                """
                INSERT INTO alert_history
                (rule_id, rule_name, channel_id, trigger_count, sample_log, sample_log_id, incident_summary, ai_enrichment, ai_model, ai_audit_id, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    rule.name,
                    rule.channel_id,
                    facts.log_count,
                    facts.sample_log[:1000],
                    facts.sample_log_id,
                    ai.incident_summary,
                    int(rule.ai_enrichment and ai.enabled),
                    ai.model,
                    ai.audit_id,
                    now_iso,
                ),
            )
            return cur.lastrowid

        try:
            from app.api.deps import run_db_query
            return await run_db_query(_execute_insert, custom_db_path=self.db_path)
        except Exception as db_err:
            logger.error(f"Failed to record alert history for rule {rule.id}: {db_err}")
            return None

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
