"""
High-throughput authenticated HTTP batch log ingestion endpoint.
Supports JSON arrays and newline-delimited JSON (NDJSON) from edge collectors (Vector, Fluent Bit).
"""

import asyncio
import datetime
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import run_db_query
from app.core.api_tokens import (
    ApiTokenRateLimiter,
    has_scope_permission,
    hash_token,
    should_update_last_used,
)
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

    token_hash_val = hash_token(raw_token)
    now_mono = time.monotonic()

    cached_entry: Optional[dict[str, Any]] = None
    with _token_cache_lock:
        if token_hash_val in _token_cache:
            ts, data = _token_cache[token_hash_val]
            if (now_mono - ts) < _TOKEN_CACHE_TTL:
                cached_entry = data
            else:
                _token_cache.pop(token_hash_val, None)

    if cached_entry is not None:
        token_info = cached_entry
    else:
        def _lookup(conn: sqlite3.Connection):
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, name, token_prefix, scopes, created_at, expires_at, last_used_at, created_by
                FROM api_tokens
                WHERE token_hash = ?
                """,
                (token_hash_val,),
            )
            return cur.fetchone()

        row = await run_db_query(_lookup)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or unrecognized API token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        expires_at_str = row["expires_at"]
        if expires_at_str:
            exp_dt = parse_iso_to_utc_datetime(expires_at_str)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if exp_dt and exp_dt <= now_utc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API token has expired.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        try:
            scopes_list = json.loads(row["scopes"]) if isinstance(row["scopes"], str) else row["scopes"]
        except Exception:
            scopes_list = []

        if not has_scope_permission(scopes_list, ["logs:write"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient token scope. Required: logs:write",
            )

        token_info = {
            "id": int(row["id"]),
            "name": row["name"],
            "token_prefix": row["token_prefix"],
            "scopes": scopes_list,
            "created_at": row["created_at"],
            "expires_at": expires_at_str,
            "created_by": row["created_by"],
        }
        with _token_cache_lock:
            _token_cache[token_hash_val] = (now_mono, token_info)

    token_id = token_info["id"]
    if not ingest_rate_limiter.record_request(f"token_{token_id}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ingestion rate limit exceeded (maximum 6,000 requests per minute).",
        )

    if should_update_last_used(token_id):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def _update_last_used(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                (now_iso, token_id),
            )
            conn.commit()

        asyncio.create_task(run_db_query(_update_last_used))

    return token_info


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
