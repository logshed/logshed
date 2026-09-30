"""
In-memory compiled drop rule filter service for LogShed log ingestion.

Evaluates incoming log records against configured drop rules before SQLite insertion
and FTS5 indexing. Tracks dropped counts in memory and flushes periodically to SQLite.
"""

import fnmatch
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from app.core.config import get_db_path
from app.core.regex_validator import compile_safe_regex, safe_regex_search
from app.core.utils import match_wildcard

logger = logging.getLogger(__name__)


@dataclass
class CompiledDropRule:
    """Compiled drop rule representation with pre-compiled regex."""
    id: int
    source_pattern: Optional[str]
    app_pattern: Optional[str]
    message_pattern: str
    is_regex: bool
    is_enabled: bool
    severity_threshold: Optional[int] = None
    compiled_regex: Optional[re.Pattern] = None

    def match_message(self, message: str) -> bool:
        """
        Evaluate if candidate message matches this rule's message pattern.
        Ensures regular expression matching handles candidate strings safely
        without hanging the asyncio event loop thread.
        """
        if not self.message_pattern or self.message_pattern.strip() == "*":
            return True

        if self.is_regex:
            if self.compiled_regex is None:
                return False
            return safe_regex_search(self.compiled_regex, message)
        else:
            pattern = self.message_pattern.strip()
            if "*" in pattern or "?" in pattern:
                wildcard_pat = pattern.lower()
                if not wildcard_pat.startswith("*"):
                    wildcard_pat = f"*{wildcard_pat}"
                if not wildcard_pat.endswith("*"):
                    wildcard_pat = f"{wildcard_pat}*"
                return fnmatch.fnmatchcase(message.lower(), wildcard_pat)
            return pattern.lower() in message.lower()

    def matches(
        self,
        source_alias: Optional[str],
        source_ip: Optional[str],
        app_name: Optional[str],
        message: str,
        severity: Optional[int] = None,
    ) -> bool:
        """Check if incoming log fields match this rule's criteria."""
        if not self.is_enabled:
            return False

        # 1. Source matching (matches either source_alias or source_ip)
        if self.source_pattern and self.source_pattern.strip():
            matched_source = False
            if source_alias and match_wildcard(self.source_pattern, source_alias):
                matched_source = True
            elif source_ip and match_wildcard(self.source_pattern, source_ip):
                matched_source = True
            if not matched_source:
                return False

        # 2. App name matching
        if self.app_pattern and self.app_pattern.strip():
            if not app_name or not match_wildcard(self.app_pattern, app_name):
                return False

        # 3. Message pattern matching
        if not self.match_message(message):
            return False

        # 4. Severity threshold check
        if self.severity_threshold is not None and severity is not None:
            if severity < self.severity_threshold:
                return False  # log is more critical than threshold - keep it

        return True


def match_message(
    candidate: Union[CompiledDropRule, str],
    message: Optional[str] = None,
    pattern: Optional[str] = None,
    is_regex: bool = False,
    compiled_regex: Optional[re.Pattern] = None,
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
        self._pending_counts: dict[int, int] = {}
        self._lock = threading.Lock()
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

        for rule in rules:
            try:
                if rule.matches(source_alias, source_ip, app_name, message, severity):
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
        """
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
        elif effective_db:
            c = sqlite3.connect(str(effective_db), timeout=5.0)
            try:
                _do_update(c)
            finally:
                c.close()

        return total_flushed

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
                "SELECT id, source_pattern, app_pattern, message_pattern, is_regex, is_enabled, severity_threshold "
                "FROM drop_rules WHERE is_enabled = 1 ORDER BY id ASC"
            )
            return cur.fetchall()

        if conn is not None:
            rows = _load(conn)
        elif effective_db:
            c = sqlite3.connect(str(effective_db), timeout=5.0)
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

            compiled_re = None
            if is_regex:
                try:
                    compiled_re = compile_safe_regex(msg_pat, re.IGNORECASE)
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
                )
            )

        with self._lock:
            self._rules = compiled


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
