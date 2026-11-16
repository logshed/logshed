"""
When each log source last sent a log, for "Last seen" on Settings -> Sources.

Ingest notes each stored batch in an in-memory map keyed by (address, source kind). The map is
saved to logs.db's source_last_seen once a minute and at shutdown, and read back at start-up, so
a source keeps its last-seen time across a restart, and after its logs expire.
"""

import datetime
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, Optional, Union

from app.core.config import get_db_path
from app.core.day_files import KIND_INTERNAL
from app.core.migrations import get_connection

logger = logging.getLogger(__name__)

# Syslog is unauthenticated, so a sender can make up addresses without end: past this many
# sources the ones heard from longest ago are forgotten (KEEP_AFTER_EVICTION remain), except the
# addresses of host aliases and Docker hosts, which are quiet longest exactly when it matters
MAX_SOURCES = 10_000
KEEP_AFTER_EVICTION = 9_000


class SourceActivity:
    """The newest receive time (microseconds since 1970, UTC) of each (address, source kind)."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        self.db_path = Path(db_path) if db_path else None
        self._lock = threading.Lock()
        self._seen: dict[tuple[str, int], int] = {}
        self._dirty: set[tuple[str, int]] = set()
        self._evicted: set[tuple[str, int]] = set()
        # Addresses of host aliases and Docker hosts, never forgotten (read by refresh_configured)
        self._configured: frozenset[str] = frozenset()

    def note(self, entries: Iterable[dict], now_us: Optional[int] = None) -> None:
        """Record the sources of stored entries as seen now. Runs on the event loop for every batch."""
        keys = {(e.get("source_ip") or "", e.get("source_kind") or 0) for e in entries}
        if not keys:
            return
        now = now_us if now_us is not None else int(time.time() * 1_000_000)
        with self._lock:
            for key in keys:
                if key[1] == KIND_INTERNAL:
                    continue
                self._seen[key] = now
                self._dirty.add(key)
                self._evicted.discard(key)
            if len(self._seen) > MAX_SOURCES:
                self._evict()

    def _evict(self) -> None:
        """Forget the sources heard from longest ago, but no configured one (the lock is held)."""
        others = [kv for kv in self._seen.items() if kv[0][0] not in self._configured]
        keep = max(0, KEEP_AFTER_EVICTION - (len(self._seen) - len(others)))
        newest_first = sorted(others, key=lambda kv: kv[1], reverse=True)
        for key, _ in newest_first[keep:]:
            del self._seen[key]
            self._dirty.discard(key)
            self._evicted.add(key)

    def by_address(self, exclude_kinds: frozenset[int] | set[int] = frozenset()) -> dict[str, int]:
        """Each address's newest receive time over every source kind but exclude_kinds."""
        newest: dict[str, int] = {}
        with self._lock:
            for (address, kind), seen in self._seen.items():
                if kind not in exclude_kinds and seen > newest.get(address, 0):
                    newest[address] = seen
        return newest

    def last_seen(self, address: str, kind: Optional[int] = None) -> Optional[int]:
        """An address's newest receive time, of one source kind or of any; None when never seen."""
        with self._lock:
            if kind is not None:
                return self._seen.get((address, kind))
            times = [seen for (a, _k), seen in self._seen.items() if a == address]
        return max(times) if times else None

    def refresh_configured(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Read the addresses of the host aliases and Docker hosts, which eviction keeps. Returns how many."""
        from app.collectors.docker_collector import effective_source_ip

        def _read(c: sqlite3.Connection) -> set[str]:
            try:
                addresses = {r[0] for r in c.execute("SELECT ip FROM host_aliases WHERE ip != 'docker'")}
                for endpoint_id, url, alias in c.execute("SELECT id, url, source_alias FROM docker_endpoints"):
                    addresses.add(effective_source_ip(url, alias, endpoint_id))
            except sqlite3.OperationalError:
                return set()  # a database still to be upgraded
            return addresses
        addresses = self._with_conn(conn, _read) or set()
        with self._lock:
            self._configured = frozenset(addresses)
        return len(addresses)

    def load(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Read the saved times (kept where a newer one is already in memory). Returns how many."""
        def _read(c: sqlite3.Connection) -> list[tuple]:
            try:
                return c.execute("SELECT source_ip, source_kind, last_seen_us FROM source_last_seen").fetchall()
            except sqlite3.OperationalError:
                return []  # before schema 4, or a database still to be upgraded
        rows = self._with_conn(conn, _read) or []
        self.refresh_configured(conn)
        with self._lock:
            for address, kind, seen in rows:
                key = (str(address), int(kind))
                if seen > self._seen.get(key, 0):
                    self._seen[key] = int(seen)
            if len(self._seen) > MAX_SOURCES:
                self._evict()
        return len(rows)

    def flush(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Save the times changed since the last flush. Returns how many rows were written."""
        with self._lock:
            changed = [(a, k, self._seen[(a, k)]) for a, k in self._dirty]
            evicted = list(self._evicted)
            self._dirty = set()
            self._evicted = set()
        if not changed and not evicted:
            return 0

        def _write(c: sqlite3.Connection) -> int:
            with c:
                c.executemany(
                    "INSERT INTO source_last_seen (source_ip, source_kind, last_seen_us) VALUES (?, ?, ?)"
                    " ON CONFLICT(source_ip, source_kind) DO UPDATE SET"
                    " last_seen_us = MAX(last_seen_us, excluded.last_seen_us)",
                    changed,
                )
                c.executemany("DELETE FROM source_last_seen WHERE source_ip = ? AND source_kind = ?", evicted)
            return len(changed)

        try:
            written = self._with_conn(conn, _write)
        except sqlite3.Error:
            # Kept for the next flush (unless newer times or an eviction came since)
            with self._lock:
                for address, kind, _seen in changed:
                    if (address, kind) in self._seen:
                        self._dirty.add((address, kind))
                self._evicted.update(k for k in evicted if k not in self._seen)
            raise
        return written or 0

    def _with_conn(self, conn: Optional[sqlite3.Connection], fn):
        if conn is not None:
            return fn(conn)
        db_path = self.db_path or (Path(get_db_path()) if get_db_path() else None)
        if db_path is None or not db_path.exists():
            return None
        c = get_connection(db_path)
        try:
            return fn(c)
        finally:
            c.close()


_activity: Optional[SourceActivity] = None
_activity_lock = threading.Lock()


def get_source_activity() -> SourceActivity:
    """The process's SourceActivity, made on first use with the configured database."""
    global _activity
    with _activity_lock:
        if _activity is None:
            _activity = SourceActivity(get_db_path())
        return _activity


def init_source_activity(db_path: Union[str, Path]) -> SourceActivity:
    """Point the process's SourceActivity at the database and read its saved times."""
    activity = get_source_activity()
    activity.db_path = Path(db_path)
    try:
        activity.load()
    except sqlite3.Error as exc:
        logger.warning(f"Could not read when each source last sent a log: {exc}")
    return activity


def us_to_iso(value: Optional[int]) -> Optional[str]:
    """A receive time in microseconds as an ISO 8601 UTC string, for API responses."""
    if value is None:
        return None
    return datetime.datetime.fromtimestamp(value / 1_000_000, tz=datetime.timezone.utc).isoformat()
