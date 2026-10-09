"""
API token generation, hashing, verification, and rate limiting for LogShed External API.
"""

import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict
from typing import Any, DefaultDict, Optional

TOKEN_PREFIX_LIVE = "ls_live_"


def generate_raw_token() -> str:
    """Generate a high-entropy secret token string starting with 'ls_live_'."""
    return f"{TOKEN_PREFIX_LIVE}{secrets.token_hex(20)}"


def hash_token(raw_token: str) -> str:
    """Compute the SHA-256 hex digest of a raw API token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def format_masked_identifier(raw_token: str) -> str:
    """
    Format a masked token identifier for safe UI display and identification.
    Example: ls_live_9a7b...1c4d
    """
    if not raw_token.startswith(TOKEN_PREFIX_LIVE):
        return raw_token[:12] + "..."
    secret_part = raw_token[len(TOKEN_PREFIX_LIVE):]
    if len(secret_part) <= 8:
        return f"{TOKEN_PREFIX_LIVE}{secret_part[:4]}..."
    return f"{TOKEN_PREFIX_LIVE}{secret_part[:4]}...{secret_part[-4:]}"


def has_scope_permission(token_scopes: list[str], required_scopes: list[str]) -> bool:
    """
    Validate that the token grants all required scopes.
    Wildcard '*' grants all scopes. Namespace wildcard 'category:*' grants all scopes in that category.
    An empty required_scopes list allows any valid token.
    """
    if not required_scopes:
        return True
    if "*" in token_scopes:
        return True
    for req in required_scopes:
        matched = False
        for tok in token_scopes:
            if tok == req or tok == "*":
                matched = True
                break
            if tok.endswith(":*") and req.startswith(tok[:-1]):
                matched = True
                break
        if not matched:
            return False
    return True


class ApiTokenRateLimiter:
    """
    In-memory sliding window rate limiter for external API token calls.
    Allows up to max_requests per window_seconds per token ID.
    """

    def __init__(self, max_requests: int = 120, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: DefaultDict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _cleanup_key(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        if not self._requests[key]:
            self._requests.pop(key, None)

    def is_rate_limited(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            self._cleanup_key(key, now)
            return len(self._requests.get(key, [])) >= self.max_requests

    def record_request(self, key: str) -> bool:
        """
        Record a request timestamp if allowed.
        Returns True if request is allowed, False if rate limited.
        """
        with self._lock:
            now = time.time()
            self._cleanup_key(key, now)
            timestamps = self._requests.get(key, [])
            if len(timestamps) >= self.max_requests:
                return False
            self._requests[key].append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()


# Global rate limiter instance for API tokens (120 req/min)
api_token_rate_limiter = ApiTokenRateLimiter(max_requests=120, window_seconds=60.0)


# In-memory tracking for throttled last_used_at database writes (5-minute cooldown)
_last_used_timestamps: dict[int, float] = {}
_last_used_lock = threading.Lock()
_LAST_USED_WRITE_INTERVAL: float = 300.0  # 5 minutes in seconds


def should_update_last_used(token_id: int) -> bool:
    """Check if token last_used_at should be written to SQLite (throttled to once per 5 minutes)."""
    now = time.time()
    with _last_used_lock:
        last_recorded = _last_used_timestamps.get(token_id, 0.0)
        if (now - last_recorded) >= _LAST_USED_WRITE_INTERVAL:
            _last_used_timestamps[token_id] = now
            return True
        return False
