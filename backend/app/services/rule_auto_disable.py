"""
Storing the automatic disabling of alert and drop rules whose pattern keeps timing out.

The rule engines switch a rule off in memory at once, on the ingest path, and queue the
database write here; the write then runs in a worker thread.
"""

import logging
import sqlite3
import threading
from contextlib import AbstractContextManager
from typing import Callable, Protocol, TypeVar

# Failed attempts to store one rule's automatic disable before giving up until the next start
AUTO_DISABLE_MAX_ATTEMPTS = 5

_RULE_TABLES = frozenset({"alert_rules", "drop_rules"})


class _HasId(Protocol):
    id: int


_Rule = TypeVar("_Rule", bound=_HasId)


class AutoDisableQueue:
    """
    Rules switched off in memory whose `is_enabled = 0` still has to be written to their table.
    Until the write lands, `without_pending` keeps them out of reloaded rule caches. A failed
    write is queued again, up to AUTO_DISABLE_MAX_ATTEMPTS per rule; after that the rule stays
    off in memory and runs again after a restart. Thread safe.
    """

    def __init__(self, table: str, label: str, log: logging.Logger, retry_when: str):
        if table not in _RULE_TABLES:
            raise ValueError(f"Not a rule table: {table!r}")
        self._table = table
        self._label = label
        self._log = log
        self._retry_when = retry_when
        self._lock = threading.Lock()
        # Switched off in memory, write not landed yet
        self._pending: set[int] = set()
        # Write still to be done
        self._queue: list[int] = []
        # Failed writes so far
        self._attempts: dict[int, int] = {}

    def add(self, rule_id: int) -> None:
        """Queue the write for a rule just switched off in memory."""
        with self._lock:
            if rule_id not in self._pending:
                self._pending.add(rule_id)
                self._queue.append(rule_id)

    def has_queued(self) -> bool:
        with self._lock:
            return bool(self._queue)

    def queued(self) -> list[int]:
        with self._lock:
            return list(self._queue)

    def pending(self) -> set[int]:
        with self._lock:
            return set(self._pending)

    def without_pending(self, rules: list[_Rule]) -> list[_Rule]:
        """The rules, less those switched off whose write has not landed."""
        with self._lock:
            if not self._pending:
                return rules
            return [rule for rule in rules if rule.id not in self._pending]

    def store(self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]) -> bool:
        """
        Write the queued disables in one transaction on the connection `connect` provides
        (blocking; call it from a worker thread). Returns True when they were written, so the
        caller should reload its rules.
        """
        with self._lock:
            rule_ids, self._queue = self._queue, []
        if not rule_ids:
            return False
        try:
            with connect() as conn:
                with conn:
                    for rule_id in rule_ids:
                        conn.execute(
                            f"UPDATE {self._table} SET is_enabled = 0 WHERE id = ? AND is_enabled = 1",
                            (rule_id,),
                        )
        except Exception as exc:
            self._retry_or_give_up(rule_ids, exc)
            return False
        with self._lock:
            self._pending.difference_update(rule_ids)
            for rule_id in rule_ids:
                self._attempts.pop(rule_id, None)
        return True

    def _retry_or_give_up(self, rule_ids: list[int], exc: Exception) -> None:
        for rule_id in rule_ids:
            with self._lock:
                attempts = self._attempts.get(rule_id, 0) + 1
                self._attempts[rule_id] = attempts
                retry = attempts < AUTO_DISABLE_MAX_ATTEMPTS
                if retry:
                    self._queue.append(rule_id)
            if retry:
                self._log.warning(
                    f"Failed to store the automatic disable of {self._label} {rule_id} "
                    f"(attempt {attempts} of {AUTO_DISABLE_MAX_ATTEMPTS}), retrying {self._retry_when}: {exc}"
                )
            else:
                self._log.error(
                    f"Failed to store the automatic disable of {self._label} {rule_id} after {attempts} attempts; "
                    f"it stays off until LogShed restarts, then runs again: {exc}"
                )
