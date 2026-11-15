"""
The hosts and apps that sent the most logs in the last 24 hours, for the Storage page.

Worked out in the background every few minutes and kept in memory, so opening the Storage page
reads no logs. The window is the 24 whole hours up to now (the current hour counted so far), by
each log's own time, as the catalogue keeps its hourly counts.
"""

import asyncio
import datetime
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from app.core.migrations import get_connection
from app.services import log_store

logger = logging.getLogger(__name__)

TOP_N = 5
WINDOW_HOURS = 24


@dataclass(frozen=True)
class TopSources:
    """One working-out of the top sources."""

    as_of: str  # when it was worked out (ISO 8601, UTC)
    since: str  # the start of the window (ISO 8601, UTC, on the hour)
    total: int
    hosts: list[tuple[str, int]] = field(default_factory=list)
    apps: list[tuple[str, int]] = field(default_factory=list)
    took_ms: int = 0


def window_start_us(now: float) -> int:
    """The start of the 24 whole hours up to now: the current hour's start, 23 hours back."""
    hour = int(now) - int(now) % 3600
    return (hour - (WINDOW_HOURS - 1) * 3600) * 1_000_000


def _top(counts: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:TOP_N]


def work_out(db_path: Union[str, Path], now: Optional[float] = None) -> TopSources:
    """Count the window's logs per host and per app (in a worker thread) and keep the top ones."""
    now = time.time() if now is None else now
    since_us = window_start_us(now)
    started = time.monotonic()
    conn = get_connection(Path(db_path))
    try:
        counts = log_store.source_counts(conn, since_us)
    finally:
        conn.close()
    return TopSources(
        as_of=datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).isoformat(),
        since=datetime.datetime.fromtimestamp(since_us / 1_000_000, tz=datetime.timezone.utc).isoformat(),
        total=counts.total,
        hosts=_top(counts.hosts),
        apps=_top(counts.apps),
        took_ms=int((time.monotonic() - started) * 1000),
    )


_latest: Optional[TopSources] = None


def get_top_sources() -> Optional[TopSources]:
    """The latest working-out, or None until the first one is done."""
    return _latest


class TopSourcesWorker:
    """Works out the top sources a minute after start-up and every REFRESH_INTERVAL seconds after."""

    REFRESH_INTERVAL = 300.0
    ERROR_BACKOFF = 30.0
    # The first working-out waits for start-up to finish: a cold count of an 8M-row day took 9 s then, 0.9 s after
    STARTUP_DELAY = 60.0

    def __init__(self, db_path: Union[str, Path]):
        self._db_path = Path(db_path)
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        global _latest
        if await self._stopped_within(self.STARTUP_DELAY):
            return
        while not self._stop_event.is_set():
            wait = self.REFRESH_INTERVAL
            try:
                _latest = await asyncio.to_thread(work_out, self._db_path)
                if _latest.took_ms > 2000:
                    logger.info(f"Working out the top sources of the last 24 hours took {_latest.took_ms} ms.")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Could not work out the top sources of the last 24 hours: {e}")
                wait = self.ERROR_BACKOFF
            if await self._stopped_within(wait):
                return

    async def _stopped_within(self, seconds: float) -> bool:
        """Wait up to seconds; True when stop() was called meanwhile."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self) -> None:
        self._stop_event.set()
