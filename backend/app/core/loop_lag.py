"""
Event loop lag monitor for LogShed.

Everything in LogShed (syslog parsing, rule checks, live streaming and every web request)
shares one event loop. A task that sleeps for a fixed interval and measures how late each
wake-up is shows how busy that loop is: a lag near zero means the loop keeps up, and a lag in
the hundreds of milliseconds means sockets are read late and requests wait.
"""

import asyncio
import math
import threading
import time
from collections import deque
from typing import Awaitable, Callable, Optional

LOOP_LAG_INTERVAL_SECONDS = 0.1
LOOP_LAG_WINDOW_SECONDS = 60.0


class LoopLagMonitor:
    """Measures event loop wake-up lag and keeps a rolling window of samples."""

    def __init__(
        self,
        interval: float = LOOP_LAG_INTERVAL_SECONDS,
        window_seconds: float = LOOP_LAG_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._interval = interval
        self._window = window_seconds
        self._clock = clock
        self._sleep = sleep
        # (monotonic time of the wake-up, lag in seconds)
        self._samples: deque[tuple[float, float]] = deque()
        self._lock = threading.Lock()

    async def run(self) -> None:
        """Sleep for the interval in a loop and record how late each wake-up is."""
        while True:
            expected = self._clock() + self._interval
            await self._sleep(self._interval)
            now = self._clock()
            self.record(max(0.0, now - expected), now)

    def record(self, lag_seconds: float, now: Optional[float] = None) -> None:
        """Record one wake-up lag and drop samples older than the window."""
        if now is None:
            now = self._clock()
        with self._lock:
            self._samples.append((now, lag_seconds))
            self._prune(now)

    def snapshot(self) -> dict[str, Optional[float]]:
        """
        Current, 95th percentile and maximum lag in milliseconds over the window.
        All three are None until the first sample (for example before the monitor starts).
        """
        now = self._clock()
        with self._lock:
            self._prune(now)
            if not self._samples:
                return {"loop_lag_ms": None, "loop_lag_p95_ms": None, "loop_lag_max_ms": None}
            current = self._samples[-1][1]
            lags = sorted(lag for _, lag in self._samples)
        p95 = lags[max(0, math.ceil(0.95 * len(lags)) - 1)]
        return {
            "loop_lag_ms": round(current * 1000.0, 1),
            "loop_lag_p95_ms": round(p95 * 1000.0, 1),
            "loop_lag_max_ms": round(lags[-1] * 1000.0, 1),
        }

    def reset(self) -> None:
        """Clear all samples (used by tests)."""
        with self._lock:
            self._samples.clear()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


_loop_lag_monitor = LoopLagMonitor()


def get_loop_lag_monitor() -> LoopLagMonitor:
    """Return the process-wide loop lag monitor started by the application lifespan."""
    return _loop_lag_monitor


def get_loop_lag() -> dict[str, Optional[float]]:
    """Current, 95th percentile and maximum event loop lag in milliseconds (None before the first sample)."""
    return _loop_lag_monitor.snapshot()
