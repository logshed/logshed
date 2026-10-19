"""
High-throughput authenticated HTTP batch log ingestion endpoint.
Supports JSON arrays and newline-delimited JSON (NDJSON) from edge collectors (Vector, Fluent Bit).
"""

import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import bearer_scheme, parse_bearer_token, rate_limit_headers, verify_api_token
from app.core.api_tokens import ApiTokenRateLimiter
from app.core.config import get_max_retention_days
from app.core.pipeline import (
    MAX_FUTURE_TIMESTAMP_SKEW_SECONDS,
    MAX_MESSAGE_BYTES,
    clean_log_text,
    filter_and_enqueue,
)
from app.core.utils import parse_iso_to_utc_datetime, resolve_severity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Log Ingestion"])

# High-throughput ingestion rate limiter (6,000 requests per minute)
ingest_rate_limiter = ApiTokenRateLimiter(max_requests=6000, window_seconds=60.0)

# Request limits: the body is read into memory, so it is capped (INGEST_MAX_BODY_BYTES overrides the size).
DEFAULT_INGEST_MAX_BODY_BYTES = 5 * 1024 * 1024
INGEST_MAX_ENTRIES = 10_000

# In-memory token validation cache with 60s TTL: token_hash -> (cached_at, token_dict)
_TOKEN_CACHE_TTL = 60.0

# Ids of revoked tokens, kept for twice the cache TTL: token_id -> monotonic time of revocation.
# An ingest request whose database lookup finished just before the revocation committed must not
# put the token back into the cache afterwards.
_REVOKED_TOKEN_RETENTION = 2 * _TOKEN_CACHE_TTL
_revoked_token_ids: dict[int, float] = {}
_revoked_token_lock = threading.Lock()


def is_ingest_token_revoked(token_id: Any) -> bool:
    """True if the token id was revoked within the last 2x cache TTL."""
    now = time.monotonic()
    with _revoked_token_lock:
        for tid in [t for t, revoked_at in _revoked_token_ids.items() if now - revoked_at > _REVOKED_TOKEN_RETENTION]:
            _revoked_token_ids.pop(tid, None)
        return token_id in _revoked_token_ids


class _RevocationAwareTokenCache(dict):
    """
    Token cache that ignores and refuses entries for recently revoked tokens.
    verify_api_token reads it with get() and writes it with item assignment, both under _token_cache_lock.
    """

    def get(self, key: Any, default: Any = None) -> Any:
        entry = super().get(key, default)
        if entry is not default and entry is not None and is_ingest_token_revoked(entry[1].get("id")):
            super().pop(key, None)
            return default
        return entry

    def __setitem__(self, key: Any, value: Any) -> None:
        if is_ingest_token_revoked(value[1].get("id")):
            return
        super().__setitem__(key, value)


_token_cache: dict[str, tuple[float, dict[str, Any]]] = _RevocationAwareTokenCache()
_token_cache_lock = threading.Lock()


def clear_ingest_token_cache() -> None:
    """Clear in-memory ingestion token cache and revoked token ids (primarily for tests)."""
    with _token_cache_lock:
        _token_cache.clear()
    with _revoked_token_lock:
        _revoked_token_ids.clear()


def forget_ingest_token(token_id: int) -> None:
    """Drop cached ingestion state (token cache entries and rate limit window) for a revoked token."""
    # Record the revocation before purging, so a concurrent cache write is either purged here or refused.
    with _revoked_token_lock:
        _revoked_token_ids[token_id] = time.monotonic()
    with _token_cache_lock:
        for key in [k for k, (_, info) in _token_cache.items() if info.get("id") == token_id]:
            _token_cache.pop(key, None)
    ingest_rate_limiter.forget(f"token_{token_id}")


def get_ingest_max_body_bytes() -> int:
    """Largest accepted request body in bytes (INGEST_MAX_BODY_BYTES, default 5 MiB)."""
    raw = os.environ.get("INGEST_MAX_BODY_BYTES")
    if raw is not None:
        try:
            val = int(raw.strip())
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_INGEST_MAX_BODY_BYTES


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"Request body exceeds the ingestion limit of {limit} bytes (INGEST_MAX_BODY_BYTES).",
    )


async def _read_body_limited(request: Request) -> bytes:
    """Read the request body, refusing it (413) once it is larger than the configured limit."""
    limit = get_ingest_max_body_bytes()
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_len = int(declared)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length header.")
        if declared_len > limit:
            raise _too_large(limit)

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise _too_large(limit)
    return bytes(body)


def _reserved_identity_reason(source_ip: str, source_alias: str) -> Optional[str]:
    """Why source_ip/source_alias may not be used by ingest, or None. They belong to LogShed's own collectors."""
    ip_lower = source_ip.lower()
    if ip_lower == "docker" or ip_lower.startswith("docker:"):
        return "source_ip 'docker' is reserved for the Docker log collector"
    if source_ip == "127.0.0.1" and source_alias.lower() == "logshed":
        return "source_ip 127.0.0.1 with source_alias 'logshed' is reserved for LogShed's own logs"
    return None


async def _authenticate_ingest_token(request: Request, response: Response) -> dict[str, Any]:
    """
    Authenticate Bearer API token with dedicated 60-second caching and logs:write scope verification.
    """
    raw_token = parse_bearer_token(request.headers.get("Authorization"))
    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = await verify_api_token(
        raw_token,
        ["logs:write"],
        rate_limiter=ingest_rate_limiter,
        rate_limit_detail="Ingestion rate limit exceeded (maximum 6,000 requests per minute).",
        cache=_token_cache,
        cache_lock=_token_cache_lock,
        cache_ttl=_TOKEN_CACHE_TTL,
        rate_status_sink=lambda info: response.headers.update(rate_limit_headers(info)),
    )
    # The token may have been revoked while this request was being authenticated.
    if is_ingest_token_revoked(token.get("id")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unrecognized API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


@router.post("/ingest")
async def ingest_logs(
    request: Request,
    response: Response,
    _bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> dict[str, Any]:
    """
    High-throughput authenticated HTTP batch log ingestion endpoint.
    Accepts JSON arrays or newline-delimited JSON (NDJSON).

    Limits: the body may be at most INGEST_MAX_BODY_BYTES (default 5 MiB) and hold at most
    10,000 entries (413 otherwise); message and raw are cut to 64 KB each. Each record is stored
    as one log entry (no multiline merging). Drop rules and maintenance drop_errors sessions apply.
    A request with an entry using a reserved identity (source_ip "docker" or "docker:*", or
    source_ip 127.0.0.1 with source_alias "logshed") is rejected with 422 and nothing is stored.
    Timestamps older than MAX_RETENTION_DAYS or more than 5 minutes ahead are replaced with the
    received time. The response reports accepted and dropped counts; ingested equals accepted.
    """
    await _authenticate_ingest_token(request, response)

    body_bytes = await _read_body_limited(request)
    if not body_bytes or not body_bytes.strip():
        return {"status": "ok", "ingested": 0, "accepted": 0, "dropped": 0}

    body_str = body_bytes.decode("utf-8", errors="replace").strip()
    raw_entries: list[dict[str, Any]] = []

    # Attempt parsing as standard JSON
    if body_str.startswith("["):
        try:
            parsed = json.loads(body_str)
            if isinstance(parsed, list):
                raw_entries = [p for p in parsed if isinstance(p, dict)]
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON array.")
        except json.JSONDecodeError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON payload: {err}")
    elif body_str.startswith("{") and "\n" not in body_str:
        try:
            parsed = json.loads(body_str)
            if isinstance(parsed, dict):
                raw_entries = [parsed]
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON object.")
        except json.JSONDecodeError as err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON payload: {err}")
    else:
        # Parse newline-delimited JSON (NDJSON) or mixed split lines
        for idx, line in enumerate(body_str.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    raw_entries.append(item)
                elif isinstance(item, list):
                    raw_entries.extend([x for x in item if isinstance(x, dict)])
            except json.JSONDecodeError as err:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid JSON payload on line {idx + 1}: {err}",
                )

    if not raw_entries:
        return {"status": "ok", "ingested": 0, "accepted": 0, "dropped": 0}

    if len(raw_entries) > INGEST_MAX_ENTRIES:
        raise HTTPException(
            status_code=413,
            detail=f"Too many log entries in one request ({len(raw_entries)}); the maximum is {INGEST_MAX_ENTRIES}.",
        )

    client_ip = request.client.host if request.client else "external"
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    now_utc_str = now_dt.isoformat()
    # Entry timestamps outside this range are replaced with the received time.
    earliest_ts = now_dt - datetime.timedelta(days=get_max_retention_days())
    latest_ts = now_dt + datetime.timedelta(seconds=MAX_FUTURE_TIMESTAMP_SKEW_SECONDS)

    alias_cache = None
    try:
        from app.main import get_syslog_server
        syslog = get_syslog_server()
        if syslog:
            alias_cache = syslog.alias_cache
    except Exception:
        pass

    # Build and validate every entry first, so a rejected request stores nothing.
    entries: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_entries):
        raw_msg = item.get("message")
        if raw_msg is None:
            raw_msg = item.get("msg", "")
        msg = clean_log_text(str(raw_msg))[:MAX_MESSAGE_BYTES]

        ts_str = now_utc_str
        ts_val = item.get("timestamp") or item.get("time") or item.get("ts")
        if ts_val:
            parsed_dt = parse_iso_to_utc_datetime(str(ts_val))
            if parsed_dt and earliest_ts <= parsed_dt <= latest_ts:
                ts_str = parsed_dt.isoformat()

        src_ip = str(item.get("source_ip") or client_ip).strip() or client_ip
        raw_alias = str(item.get("source_alias") or src_ip).strip() or src_ip
        reserved = _reserved_identity_reason(src_ip, raw_alias)
        if reserved:
            raise HTTPException(
                status_code=422,
                detail=f"Log entry {idx + 1} uses a reserved identity: {reserved}. Nothing was ingested.",
            )
        src_alias = alias_cache.resolve(raw_alias) if alias_cache else raw_alias

        app_name = str(item.get("app_name") or item.get("app") or "app").strip() or "app"

        try:
            fac = int(item.get("facility", 1))
            fac = max(0, min(23, fac))
        except (ValueError, TypeError):
            fac = 1

        sev_val = item.get("severity")
        if sev_val is None:
            sev_val = item.get("level", 6)
        try:
            sev = resolve_severity(sev_val)
        except Exception:
            sev = None
        if sev is None:
            sev = 6

        raw_val = item.get("raw")
        clean_raw = clean_log_text(str(raw_val))[:MAX_MESSAGE_BYTES] if raw_val is not None else msg

        entries.append({
            "timestamp": ts_str,
            "received_at": now_utc_str,
            "source_ip": src_ip,
            "source_alias": src_alias,
            "app_name": app_name,
            "facility": fac,
            "severity": sev,
            "message": msg,
            "raw": clean_raw,
        })

    # Records are complete entries: they skip the syslog multiline assembler and go straight
    # through the drop rules, maintenance drop_errors sessions and the non-blocking queue.
    accepted = sum(1 for entry in entries if filter_and_enqueue(entry))

    return {
        "status": "ok",
        "ingested": accepted,
        "accepted": accepted,
        "dropped": len(entries) - accepted,
    }
