"""
API token generation, hashing, verification, and rate limiting for LogShed External API.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import secrets
import threading
import time
from typing import Any, Optional

from app.core.rate_limiter import SlidingWindowLimiter

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
    if not isinstance(token_scopes, (list, tuple, set)):
        return False
    if "*" in token_scopes:
        return True
    for req in required_scopes:
        matched = False
        for tok in token_scopes:
            if not isinstance(tok, str):
                continue
            if tok == req or tok == "*":
                matched = True
                break
            if tok.endswith(":*") and len(tok) > 2 and req.startswith(tok[:-1]):
                matched = True
                break
        if not matched:
            return False
    return True


# Single source of truth for token scopes accepted at creation and edit time. Derived from the scopes enforced
# by require_api_token / require_auth_or_token in the API layer and by the MCP server: connecting to MCP
# needs only logs:read (REQUIRED_MCP_SCOPES in api/mcp.py), and each MCP tool then checks its own scope.
# Keep in step with SCOPE_CATALOG in frontend/src/components/settings/tokenScopes.ts (the custom scope picker).
KNOWN_SCOPES: frozenset[str] = frozenset(
    {
        "logs:read",
        "logs:write",
        "alerts:read",
        "system:read",
        "system:write",
        "maintenance:read",
        "maintenance:write",
    }
)
SCOPE_CATEGORIES: frozenset[str] = frozenset(s.split(":", 1)[0] for s in KNOWN_SCOPES)
MAX_TOKEN_SCOPES = 20


def validate_token_scopes(scopes: list[str]) -> list[str]:
    """
    Validate and de-duplicate a list of token scopes (order preserved).
    Accepts '*', known 'category:action' scopes, and '<known category>:*' namespace wildcards.
    Raises ValueError for empty, oversized, or unknown entries.
    """
    if not scopes:
        raise ValueError("At least one scope is required.")
    if len(scopes) > MAX_TOKEN_SCOPES:
        raise ValueError(f"A token may have at most {MAX_TOKEN_SCOPES} scopes.")

    result: list[str] = []
    for scope in scopes:
        if scope != "*" and scope not in KNOWN_SCOPES:
            category, sep, action = scope.partition(":")
            if not (sep and action == "*" and category in SCOPE_CATEGORIES):
                raise ValueError(f"Unknown scope: {scope!r}")
        if scope not in result:
            result.append(scope)
    return result


def validate_token_name(name: str) -> str:
    """Strip a token name, raising ValueError when nothing is left."""
    stripped = name.strip()
    if not stripped:
        raise ValueError("Token name must not be blank.")
    return stripped


@dataclass(frozen=True)
class RateLimitStatus:
    """Snapshot of a token's sliding window after a rate limit check."""

    limit: int
    remaining: int
    reset_seconds: int  # seconds until the oldest request leaves the window (at least 1 when limited)
    allowed: bool


class ApiTokenRateLimiter(SlidingWindowLimiter):
    """
    In-memory sliding window rate limiter for external API token calls.
    Allows up to max_requests per window_seconds per token ID.
    Called only from async handlers on the event loop, so it needs no thread lock.
    """

    def __init__(self, max_requests: int = 120, window_seconds: float = 60.0, **kwargs):
        super().__init__(max_requests, window_seconds, **kwargs)

    def check_request(self, key: str) -> RateLimitStatus:
        """
        Record a request timestamp if allowed and return the resulting window status.
        When limited, nothing is recorded and reset_seconds is the wait until a slot frees.
        """
        now = self._clock()
        window = self._prune(key, now)
        used = len(window) if window else 0
        oldest = window[0] if window else now
        reset_seconds = max(1, math.ceil(oldest + self.window_seconds - now))
        if used >= self.max_requests:
            return RateLimitStatus(self.max_requests, 0, reset_seconds, False)
        self._append(key, now)
        return RateLimitStatus(self.max_requests, self.max_requests - used - 1, reset_seconds, True)

    def record_request(self, key: str) -> bool:
        """
        Record a request timestamp if allowed.
        Returns True if request is allowed, False if rate limited.
        """
        return self.check_request(key).allowed


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


def forget_last_used(token_id: int) -> None:
    """Remove the throttling entry for a token (e.g. when the token is revoked)."""
    with _last_used_lock:
        _last_used_timestamps.pop(token_id, None)
