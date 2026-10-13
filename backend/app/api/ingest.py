"""
High-throughput authenticated HTTP batch log ingestion endpoint.
Supports JSON arrays and newline-delimited JSON (NDJSON) from edge collectors (Vector, Fluent Bit).
"""

import datetime
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import verify_api_token
from app.core.api_tokens import ApiTokenRateLimiter
from app.core.pipeline import (
    clean_log_text,
    get_queue,
)
from app.core.utils import parse_iso_to_utc_datetime, resolve_severity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Log Ingestion"])

# High-throughput ingestion rate limiter (6,000 requests per minute)
ingest_rate_limiter = ApiTokenRateLimiter(max_requests=6000, window_seconds=60.0)

# In-memory token validation cache with 60s TTL: token_hash -> (cached_at, token_dict)
_TOKEN_CACHE_TTL = 60.0
_token_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_token_cache_lock = threading.Lock()


def clear_ingest_token_cache() -> None:
    """Clear in-memory ingestion token cache (primarily for tests)."""
    with _token_cache_lock:
        _token_cache.clear()


def forget_ingest_token(token_id: int) -> None:
    """Drop cached ingestion state (token cache entries and rate limit window) for a revoked token."""
    with _token_cache_lock:
        for key in [k for k, (_, info) in _token_cache.items() if info.get("id") == token_id]:
            _token_cache.pop(key, None)
    ingest_rate_limiter.forget(f"token_{token_id}")


async def _authenticate_ingest_token(request: Request) -> dict[str, Any]:
    """
    Authenticate Bearer API token with dedicated 60-second caching and logs:write scope verification.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = auth_header[7:].strip()
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await verify_api_token(
        raw_token,
        ["logs:write"],
        rate_limiter=ingest_rate_limiter,
        rate_limit_detail="Ingestion rate limit exceeded (maximum 6,000 requests per minute).",
        cache=_token_cache,
        cache_lock=_token_cache_lock,
        cache_ttl=_TOKEN_CACHE_TTL,
    )


@router.post("/ingest")
async def ingest_logs(request: Request) -> dict[str, Any]:
    """
    High-throughput authenticated HTTP batch log ingestion endpoint.
    Accepts JSON arrays or newline-delimited JSON (NDJSON).
    """
    token = await _authenticate_ingest_token(request)
    token_id = token["id"]

    body_bytes = await request.body()
    if not body_bytes or not body_bytes.strip():
        return {"status": "ok", "ingested": 0}

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
        return {"status": "ok", "ingested": 0}

    from app.main import get_assembler
    assembler = get_assembler()

    client_ip = request.client.host if request.client else "external"
    now_utc_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

    alias_cache = None
    try:
        from app.main import get_syslog_server
        syslog = get_syslog_server()
        if syslog:
            alias_cache = syslog.alias_cache
    except Exception:
        pass

    touched_streams: set[str] = set()

    for item in raw_entries:
        raw_msg = item.get("message")
        if raw_msg is None:
            raw_msg = item.get("msg", "")
        msg = clean_log_text(str(raw_msg))

        ts_val = item.get("timestamp") or item.get("time") or item.get("ts")
        if ts_val:
            parsed_dt = parse_iso_to_utc_datetime(str(ts_val))
            ts_str = parsed_dt.isoformat() if parsed_dt else now_utc_str
        else:
            ts_str = now_utc_str

        src_ip = str(item.get("source_ip", client_ip)).strip() or client_ip
        raw_alias = str(item.get("source_alias") or src_ip).strip() or src_ip
        if alias_cache:
            src_alias = alias_cache.resolve(raw_alias)
        else:
            src_alias = raw_alias

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
            sev = 6

        clean_raw = str(item.get("raw") if item.get("raw") is not None else msg)

        entry = {
            "timestamp": ts_str,
            "received_at": now_utc_str,
            "source_ip": src_ip,
            "source_alias": src_alias,
            "app_name": app_name,
            "facility": fac,
            "severity": sev,
            "message": msg,
            "raw": clean_raw,
        }

        stream_key = f"http:{token_id}:{src_ip}:{app_name}"
        touched_streams.add(stream_key)

        if assembler is not None:
            await assembler.feed(stream_key, entry)
        else:
            await get_queue().put(entry)

    if assembler is not None:
        for sk in touched_streams:
            assembler.flush_stream(sk)

    return {"status": "ok", "ingested": len(raw_entries)}
