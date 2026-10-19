"""
In-memory sliding window rate limiters.

All limiters here are used only from async handlers on the single event-loop thread
(LogShed runs one process with one loop), so they hold no thread locks. Every method
is synchronous and never awaits, which makes each call atomic with respect to other
requests on the loop.

- SlidingWindowLimiter: the shared deque-based building block (also used by the
  external API token limiters in app.core.api_tokens).
- LoginRateLimiter: brute-force protection for POST /api/auth/login.
- AiRateLimiter: per-session limit for AI diagnosis requests.
"""

import ipaddress
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

# Above this many tracked keys, keys whose newest event has left the window are swept.
DEFAULT_SWEEP_THRESHOLD = 5000


class SlidingWindowLimiter:
    """
    Allows up to `max_requests` events per `window_seconds` per key.

    Each key holds a deque of event timestamps, oldest first, so expired events are
    dropped with amortized O(1) popleft. Keys that go idle are removed when they are
    next touched; keys that are never touched again are removed by a sweep that runs
    once the number of tracked keys goes over `sweep_threshold`. After a sweep the
    next one is deferred until the key count doubles, so a flood of distinct active
    keys costs amortized O(1) per new key rather than a full sweep each time.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        *,
        sweep_threshold: int = DEFAULT_SWEEP_THRESHOLD,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.sweep_threshold = sweep_threshold
        self._clock = clock
        self._windows: dict[str, deque[float]] = {}
        self._next_sweep_at = sweep_threshold

    def __contains__(self, key: object) -> bool:
        return key in self._windows

    def __len__(self) -> int:
        return len(self._windows)

    def _prune(self, key: str, now: float) -> Optional[deque[float]]:
        """Drop expired timestamps for one key; return its window, or None when empty."""
        window = self._windows.get(key)
        if window is None:
            return None
        cutoff = now - self.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()
        if not window:
            del self._windows[key]
            return None
        return window

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window_seconds
        idle = [key for key, window in self._windows.items() if not window or window[-1] <= cutoff]
        for key in idle:
            del self._windows[key]
        self._next_sweep_at = max(self.sweep_threshold, 2 * len(self._windows))

    def _append(self, key: str, now: float) -> None:
        window = self._windows.get(key)
        if window is None:
            if len(self._windows) >= self._next_sweep_at:
                self._sweep(now)
            window = self._windows[key] = deque()
        window.append(now)

    def count(self, key: str) -> int:
        """Number of events for `key` still inside the window."""
        window = self._prune(key, self._clock())
        return len(window) if window else 0

    def is_rate_limited(self, key: str) -> bool:
        return self.count(key) >= self.max_requests

    def check_and_record(self, key: str) -> bool:
        """
        Record an event for `key` if it is under the limit.
        Returns True if the event was allowed and recorded, False if the limit is reached.
        """
        now = self._clock()
        window = self._prune(key, now)
        if window is not None and len(window) >= self.max_requests:
            return False
        self._append(key, now)
        return True

    def record(self, key: str, now: Optional[float] = None) -> float:
        """Record an event unconditionally (at `now`, default the current time) and return its timestamp."""
        if now is None:
            now = self._clock()
        self._prune(key, now)
        self._append(key, now)
        return now

    def discard(self, key: str, timestamp: float) -> None:
        """Remove one previously recorded event (no-op if it already expired)."""
        window = self._windows.get(key)
        if window is None:
            return
        try:
            window.remove(timestamp)
        except ValueError:
            return
        if not window:
            del self._windows[key]

    def forget(self, key: str) -> None:
        """Drop all state for one key."""
        self._windows.pop(key, None)

    def reset(self) -> None:
        """Clear all state (useful for testing)."""
        self._windows.clear()
        self._next_sweep_at = self.sweep_threshold


def ip_bucket_key(ip: str) -> str:
    """
    Rate limit key for a client IP.
    IPv6 clients are keyed by their /64, since one host or LAN usually owns a whole /64
    and could otherwise rotate addresses to get a fresh bucket per request.
    IPv4-mapped IPv6 addresses are keyed as the IPv4 address. Unparseable input is used as is.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(addr), 64), strict=False))
    return str(addr)


@dataclass(frozen=True)
class LoginAttempt:
    """A login attempt reserved by LoginRateLimiter.begin(); pass it to finish()."""

    key: str


class LoginRateLimiter:
    """
    Brute-force protection for the login endpoint.

    - Per key (client IP, IPv6 by /64): up to `max_attempts` failed attempts per window.
    - Per key: at most `max_in_flight` attempts being verified at the same time.

    begin() reserves the attempt before the caller awaits anything, so
    concurrent requests see each other's attempts. finish(success=True) releases the
    reservation and clears the key's history so a legitimate user is not locked out by
    earlier typos; finish(success=False) leaves the attempt counted as a failure.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: float = 60.0,
        *,
        max_in_flight: int = 2,
        sweep_threshold: int = DEFAULT_SWEEP_THRESHOLD,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_in_flight = max_in_flight
        self._per_key = SlidingWindowLimiter(
            max_attempts, window_seconds, sweep_threshold=sweep_threshold, clock=clock
        )
        self._in_flight: dict[str, int] = {}

    def __len__(self) -> int:
        """Number of keys with tracked attempts."""
        return len(self._per_key)

    def begin(self, key: str) -> Optional[LoginAttempt]:
        """
        Reserve a login attempt for `key`. Returns None when the attempt must be rejected
        (per-key limit or per-key in-flight cap reached); nothing is recorded then.
        """
        if self._in_flight.get(key, 0) >= self.max_in_flight:
            return None
        if self._per_key.is_rate_limited(key):
            return None
        self._per_key.record(key)
        self._in_flight[key] = self._in_flight.get(key, 0) + 1
        return LoginAttempt(key=key)

    def finish(self, attempt: LoginAttempt, *, success: bool) -> None:
        """Release the in-flight slot; on success also undo the reservation and clear history."""
        remaining = self._in_flight.get(attempt.key, 0) - 1
        if remaining > 0:
            self._in_flight[attempt.key] = remaining
        else:
            self._in_flight.pop(attempt.key, None)
        if success:
            self._per_key.forget(attempt.key)

    def reset(self) -> None:
        """Clear all rate limit state (useful for testing)."""
        self._per_key.reset()
        self._in_flight.clear()


# Global singleton instance
login_rate_limiter = LoginRateLimiter(max_attempts=5, window_seconds=60.0)


class AiRateLimiter(SlidingWindowLimiter):
    """
    Sliding-window rate limiter for AI diagnosis requests.
    Allows up to `max_requests` per `window_seconds` per user/session key.
    """

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0, **kwargs):
        super().__init__(max_requests, window_seconds, **kwargs)


# Global singleton instance for AI diagnosis rate limiting (10 req/min per user/session)
ai_rate_limiter = AiRateLimiter(max_requests=10, window_seconds=60.0)
