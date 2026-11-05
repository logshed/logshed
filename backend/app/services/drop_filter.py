"""
In-memory compiled drop rule filter service for LogShed log ingestion.

Evaluates incoming log records against configured drop rules before SQLite insertion
and FTS5 indexing. Tracks dropped counts in memory and flushes periodically to SQLite.
"""

import asyncio
from contextlib import closing, nullcontext
import fnmatch
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

import regex

from app.core.config import get_db_path
from app.core.migrations import get_connection
from app.core.regex_validator import (
    RuleTimeoutTracker,
    compile_user_pattern,
    describe_rule_timeout_limit,
    safe_regex_search,
    user_pattern_search,
)
from app.core.rule_index import RuleIndex, exact_match_key
from app.core.utils import compile_wildcard_list, split_pattern_list, wildcard_text_key
from app.services.rule_auto_disable import AutoDisableQueue

logger = logging.getLogger(__name__)


@dataclass
class CompiledDropRule:
    """
    Compiled drop rule representation with a pre-compiled user regex (`regex` engine).

    source_pattern and app_pattern each hold a comma-separated list of patterns (one pattern is a
    list of one); the rule matches when any host pattern and any app pattern match.
    """
    id: int
    source_pattern: Optional[str]
    app_pattern: Optional[str]
    message_pattern: str
    is_regex: bool
    is_enabled: bool
    severity_threshold: Optional[int] = None
    compiled_regex: Optional[regex.Pattern] = None
    display_order: int = 0
    # Set by DropFilter so pattern timeouts count towards disabling the rule automatically
    on_pattern_timeout: Optional[Callable[[], None]] = field(default=None, repr=False, compare=False)
    # Prepared from the patterns once, so each log is not lowercased and compared per rule
    _source_match: Optional[Callable[[str], bool]] = field(init=False, default=None, repr=False, compare=False)
    _app_match: Optional[Callable[[str], bool]] = field(init=False, default=None, repr=False, compare=False)
    _message_text_match: Optional[Callable[[str], bool]] = field(init=False, default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._source_match = compile_wildcard_list(self.source_pattern)
        self._app_match = compile_wildcard_list(self.app_pattern)
        if self.message_pattern and not self.is_regex:
            pattern = self.message_pattern.strip().lower()
            if "*" in pattern or "?" in pattern:
                if not pattern.startswith("*"):
                    pattern = f"*{pattern}"
                if not pattern.endswith("*"):
                    pattern = f"{pattern}*"
                match = re.compile(fnmatch.translate(pattern)).match
                self._message_text_match = lambda text: match(text) is not None
            else:
                self._message_text_match = lambda text: pattern in text

    def match_message(self, message: str) -> bool:
        """
        Evaluate if candidate message matches this rule's message pattern.
        Ensures regular expression matching handles candidate strings safely
        without hanging the asyncio event loop thread.
        """
        return self._match_message(message, message.lower() if not self.is_regex and message is not None else None)

    def _match_message(self, message: str, message_lower: Optional[str]) -> bool:
        """match_message with the message already lowercased (only read by plain text patterns)."""
        if not self.message_pattern or self.message_pattern.strip() == "*":
            return True

        if self.is_regex:
            if self.compiled_regex is None:
                return False
            if not message:
                return False
            return user_pattern_search(
                self.compiled_regex, message, rule_label=f"drop rule {self.id}", on_timeout=self.on_pattern_timeout
            )
        if message_lower is None:
            return False
        return self._message_text_match(message_lower)

    def matches(
        self,
        source_alias: Optional[str],
        source_ip: Optional[str],
        app_name: Optional[str],
        message: str,
        severity: Optional[int] = None,
    ) -> bool:
        """Check if incoming log fields match this rule's criteria."""
        return self._matches_keys(
            wildcard_text_key(source_alias),
            wildcard_text_key(source_ip),
            wildcard_text_key(app_name),
            message,
            message.lower() if isinstance(message, str) else None,
            severity,
        )

    def _matches_keys(
        self,
        alias_key: Optional[str],
        ip_key: Optional[str],
        app_key: Optional[str],
        message: str,
        message_lower: Optional[str],
        severity: Optional[int],
    ) -> bool:
        """matches() with the fields already in wildcard_text_key form and the message lowercased."""
        if not self.is_enabled:
            return False

        # 1. Source matching (matches either source_alias or source_ip)
        if self._source_match is not None:
            if not (
                (alias_key is not None and self._source_match(alias_key))
                or (ip_key is not None and self._source_match(ip_key))
            ):
                return False

        # 2. App name matching
        if self._app_match is not None:
            if app_key is None or not self._app_match(app_key):
                return False

        # 3. Message pattern matching
        if not self._match_message(message, message_lower):
            return False

        # 4. Severity threshold check
        if self.severity_threshold is not None and severity is not None:
            if severity < self.severity_threshold:
                return False  # log is more critical than threshold - keep it

        return True


def _exact_keys(pattern_list: Optional[str]) -> Optional[tuple[str, ...]]:
    """The exact values a pattern list matches, or None when it is empty or any entry has a wildcard."""
    keys = tuple(exact_match_key(p) for p in split_pattern_list(pattern_list))
    return keys if keys and all(keys) else None


def _drop_rule_exact_keys(rule: CompiledDropRule) -> Optional[tuple[str, tuple[str, ...]]]:
    """The exact apps (or else sources) a drop rule requires, or None when it must see every entry."""
    apps = _exact_keys(rule.app_pattern)
    if apps:
        return ("app", apps)
    sources = _exact_keys(rule.source_pattern)
    if sources:
        return ("source", sources)
    return None


def match_message(
    candidate: Union[CompiledDropRule, str],
    message: Optional[str] = None,
    pattern: Optional[str] = None,
    is_regex: bool = False,
    compiled_regex: Optional[regex.Pattern] = None,
) -> bool:
    """
    Safely evaluate a message against a CompiledDropRule or pattern string.
    Ensures regex matching cannot hang caller threads.
    """
    if isinstance(candidate, CompiledDropRule):
        return candidate.match_message(message or "")
    if compiled_regex is not None:
        return safe_regex_search(compiled_regex, candidate)
    if is_regex and pattern:
        return safe_regex_search(pattern, candidate)
    if pattern:
        rule = CompiledDropRule(
            id=0,
            source_pattern=None,
            app_pattern=None,
            message_pattern=pattern,
            is_regex=is_regex,
            is_enabled=True,
        )
        return rule.match_message(candidate)
    return True


class DropFilter:
    """
    In-memory rule cache and evaluation engine for dropping noise during ingestion.
    """
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        self.db_path = Path(db_path) if db_path else None
        self._rules: list[CompiledDropRule] = []
        # Built from self._rules on first use after each change of the list
        self._index: Optional[RuleIndex[CompiledDropRule]] = None
        self._pending_counts: dict[int, int] = {}
        self._lock = threading.Lock()
        # Automatic disabling of rules whose pattern keeps timing out.
        # Lock order: self._lock, then the queue's own lock.
        self._timeout_tracker = RuleTimeoutTracker()
        self._auto_disables = AutoDisableQueue("drop_rules", "drop rule", logger, "with the next count flush")
        self._auto_disable_tasks: set[asyncio.Task] = set()
        if self.db_path:
            self.reload_rules()

    def should_drop(
        self,
        source_alias: Optional[str],
        source_ip: Optional[str],
        app_name: Optional[str],
        message: str,
        severity: Optional[int] = None,
    ) -> Optional[int]:
        """
        Evaluate if a log entry matches any active drop rule.
        Returns the matched rule ID if dropped, or None if kept.
        """
        with self._lock:
            rules = self._rules
            if not rules:
                return None
            index = self._index
            if index is None or index.source is not rules:
                index = self._index = RuleIndex(rules, _drop_rule_exact_keys)

        # Only the rules for this app or source, and those without an exact app or source, can match
        alias_key = wildcard_text_key(source_alias)
        ip_key = wildcard_text_key(source_ip)
        app_key = wildcard_text_key(app_name)
        lookups = (("app", app_key or ""), ("source", alias_key or ""), ("source", ip_key or ""))
        candidates = index.candidates(lookups)
        if not candidates:
            return None
        message_lower = message.lower() if isinstance(message, str) else None
        for rule in candidates:
            try:
                if rule._matches_keys(alias_key, ip_key, app_key, message, message_lower, severity):
                    with self._lock:
                        self._pending_counts[rule.id] = self._pending_counts.get(rule.id, 0) + 1
                    return rule.id
            except Exception as e:
                logger.debug(f"Error evaluating drop rule {rule.id}: {e}")
                continue

        return None

    def get_pending_count(self, rule_id: int) -> int:
        """Get accumulated drop count for a specific rule not yet flushed to database."""
        with self._lock:
            return self._pending_counts.get(rule_id, 0)

    def get_all_pending_counts(self) -> dict[int, int]:
        """Get a copy of all pending in-memory drop counts."""
        with self._lock:
            return dict(self._pending_counts)

    def reset_rule_count(self, rule_id: int) -> None:
        """Clear pending in-memory counts for a specific rule."""
        with self._lock:
            self._pending_counts.pop(rule_id, None)

    def flush_counts(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """
        Flush accumulated drop counts to SQLite.
        Returns the total count of drops written.
        Also stores any automatic rule disables still waiting.
        """
        self.store_auto_disables(conn=conn)
        with self._lock:
            if not self._pending_counts:
                return 0
            to_flush = self._pending_counts
            self._pending_counts = {}

        total_flushed = sum(to_flush.values())
        effective_db = self.db_path or get_db_path()
        if not effective_db and conn is None:
            return total_flushed

        def _do_update(c: sqlite3.Connection):
            for rule_id, count in to_flush.items():
                c.execute(
                    "UPDATE drop_rules SET dropped_count = dropped_count + ? WHERE id = ?",
                    (count, rule_id),
                )
            c.commit()

        if conn is not None:
            _do_update(conn)
        elif effective_db and Path(effective_db).exists():
            c = get_connection(effective_db)
            try:
                _do_update(c)
            finally:
                c.close()

        return total_flushed

    def _note_pattern_timeout(self, rule: CompiledDropRule) -> None:
        """
        Count a pattern timeout for the rule. On reaching the limit (once per rule per process),
        take the rule out of the in-memory cache at once and queue the database write. Runs on
        the ingest path, so the write happens in a worker thread: scheduled now when an event
        loop is running here, and in any case with the next count flush.
        """
        if not self._timeout_tracker.record(rule.id):
            return
        rule.is_enabled = False
        with self._lock:
            self._rules = [r for r in self._rules if r.id != rule.id]
            self._auto_disables.add(rule.id)
        logger.error(
            "Drop rule %s (pattern '%s') disabled automatically: %s. Simplify the pattern, then enable the rule again.",
            rule.id,
            rule.message_pattern,
            describe_rule_timeout_limit(),
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(asyncio.to_thread(self.store_auto_disables))
        self._auto_disable_tasks.add(task)
        task.add_done_callback(self._auto_disable_tasks.discard)

    def store_auto_disables(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """
        Store is_enabled = 0 for rules disabled automatically and not yet written, then reload
        the rule cache. Blocking; call it from a worker thread. A failed write is retried with
        the next count flush, and the rule stays off in memory meanwhile.
        """
        if not self._auto_disables.has_queued():
            return
        if conn is not None:
            connect = lambda: nullcontext(conn)
        else:
            effective_db = self.db_path or get_db_path()
            if not (effective_db and Path(effective_db).exists()):
                return
            connect = lambda: closing(get_connection(effective_db))
        if not self._auto_disables.store(connect):
            return
        try:
            self.reload_rules(conn=conn)
        except Exception as exc:
            logger.warning(f"Failed to reload drop rules after automatic disables: {exc}")

    def _flush_pending_counts(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """
        Flush accumulated in-memory drop counts to SQLite.
        Falls back to get_db_path() if self.db_path is not explicitly configured.
        """
        return self.flush_counts(conn=conn)

    def reload_rules(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """
        Reload active drop rules from SQLite and compile regex patterns.
        """
        effective_db = self.db_path or get_db_path()
        if not effective_db and conn is None:
            return

        def _load(c: sqlite3.Connection):
            cur = c.cursor()
            cur.execute(
                "SELECT id, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold, display_order "
                "FROM drop_rules WHERE is_enabled = 1 ORDER BY display_order ASC, id ASC"
            )
            return cur.fetchall()

        if conn is not None:
            rows = _load(conn)
        elif effective_db and Path(effective_db).exists():
            c = get_connection(effective_db)
            try:
                rows = _load(c)
            finally:
                c.close()
        else:
            rows = []

        compiled: list[CompiledDropRule] = []
        for r in rows:
            rule_id = r[0]
            src_pat = r[1]
            app_pat = r[2]
            msg_pat = r[3]
            is_regex = bool(r[4])
            is_enabled = bool(r[5])
            sev_thresh = r[6] if len(r) > 6 else None
            disp_ord = r[7] if len(r) > 7 else 0

            compiled_re = None
            if is_regex:
                try:
                    compiled_re = compile_user_pattern(msg_pat, re.IGNORECASE)
                except Exception as e:
                    logger.warning(f"Drop rule {rule_id} has invalid regex '{msg_pat}': {e}")
                    continue

            compiled.append(
                CompiledDropRule(
                    id=rule_id,
                    source_pattern=src_pat,
                    app_pattern=app_pat,
                    message_pattern=msg_pat,
                    is_regex=is_regex,
                    is_enabled=is_enabled,
                    severity_threshold=sev_thresh,
                    compiled_regex=compiled_re,
                    display_order=disp_ord,
                )
            )
            if compiled_re is not None:
                compiled[-1].on_pattern_timeout = self._make_timeout_callback(compiled[-1])

        with self._lock:
            self._rules = self._auto_disables.without_pending(compiled)

    def _make_timeout_callback(self, rule: CompiledDropRule) -> Callable[[], None]:
        """Return the callback a rule calls each time its pattern times out."""
        def _on_timeout() -> None:
            self._note_pattern_timeout(rule)
        return _on_timeout


# Module-level singleton instance
_drop_filter: Optional[DropFilter] = None
_filter_lock = threading.Lock()


def get_drop_filter() -> DropFilter:
    """Return the global DropFilter singleton instance, lazily instantiated with get_db_path()."""
    global _drop_filter
    with _filter_lock:
        if _drop_filter is None:
            _drop_filter = DropFilter(get_db_path())
        return _drop_filter


def init_drop_filter(db_path: Union[str, Path]) -> DropFilter:
    """Initialize or update the global DropFilter singleton with database path, preserving counters."""
    global _drop_filter
    with _filter_lock:
        if _drop_filter is not None:
            _drop_filter.db_path = Path(db_path)
            _drop_filter.reload_rules()
            return _drop_filter
        _drop_filter = DropFilter(db_path)
        return _drop_filter
