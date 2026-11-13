"""
Log searches, counts and context for REST v1 and MCP: both call these functions, each with its
own argument checks, and get the same results and the same errors (HTTPException, as the routes
raise them). Results are in the form REST returns to outside clients (row_to_log_entry with
external=True: redacted text, filled-in host and app names).
"""

import base64
import json
import sqlite3
from typing import Optional

from fastapi import HTTPException, Request, status

from app.api.deps import run_db_query
from app.core.utils import resolve_severity
from app.models import ExternalLogContextResponse, ExternalLogListResponse, LogCountResponse
from app.services import log_store
from app.services.log_query import (
    MAX_API_FILTER_VALUES,
    InvalidTimeBound,
    LogFilter,
    check_filter_value_counts,
    db_busy_exception,
    row_to_log_entry,
    run_search_query,
)


def parse_api_log_filter(
    query: Optional[str],
    source: Optional[list[str]],
    app_name: Optional[list[str]],
    severity_max: Optional[int],
    severity: Optional[str],
    from_: Optional[str],
    to: Optional[str],
) -> LogFilter:
    """
    The filter of a REST or MCP log search or count, with REST's cap on filter values; a named
    severity wins over severity_max. HTTP 422 on invalid input.
    """
    check_filter_value_counts(MAX_API_FILTER_VALUES, source=source, app_name=app_name)
    if severity is not None:
        try:
            severity_max = resolve_severity(severity)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    try:
        return LogFilter.parse(
            query=query, sources=source, apps=app_name, severity_max=severity_max, from_=from_, to=to
        )
    except InvalidTimeBound as exc:
        raise HTTPException(status_code=422, detail=str(exc))


MAX_CURSOR_LENGTH = 200
_INVALID_CURSOR_DETAIL = "Invalid cursor. Use the next_cursor value returned by the previous page."
TOTAL_CAP = 1001
# Characters of message plus raw one page of GET /api/v1/logs redacts and returns at most (about
# 8 MB); a page that reaches it ends early with a next_cursor. It always holds at least one row.
LOG_PAGE_TEXT_BUDGET = 8 * 1024 * 1024


def encode_log_cursor(timestamp: str, log_id: int) -> str:
    """Encode a (timestamp, id) position as an opaque URL-safe cursor."""
    raw = json.dumps([timestamp, log_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_log_cursor(cursor: str) -> tuple[str, int]:
    """Decode a cursor produced by encode_log_cursor, raising HTTP 422 when malformed."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        timestamp, log_id = decoded
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError("bad timestamp")
        if isinstance(log_id, bool) or not isinstance(log_id, int):
            raise ValueError("bad id")
        return timestamp, log_id
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(status_code=422, detail=_INVALID_CURSOR_DETAIL)


async def search_logs(
    log_filter: LogFilter,
    *,
    limit: int,
    cursor: Optional[str] = None,
    include_raw: bool = False,
    request: Optional[Request] = None,
) -> ExternalLogListResponse:
    """
    Search logs (newest first) with keyset pagination via next_cursor. With request, the search
    stops when that client disconnects.
    """
    cursor_pos = decode_log_cursor(cursor) if cursor else None

    def _query_db(conn):
        # The total counts every match whatever the cursor, capped so broad queries do not scan every row
        total, rows = log_store.search(
            conn, log_filter, fetch=limit + 1, count_limit=TOTAL_CAP, before=cursor_pos, include_raw=include_raw
        )

        # One extra row signals another page exists
        has_more = len(rows) > limit
        rows = rows[:limit]
        # Stop at the text budget, so one request cannot redact up to limit times two 64 KB fields
        text_chars = 0
        for kept, r in enumerate(rows, start=1):
            text_chars += len(r["message"] or "") + (len(r["raw"] or "") if include_raw else 0)
            if text_chars >= LOG_PAGE_TEXT_BUDGET and kept < len(rows):
                rows = rows[:kept]
                has_more = True
                break
        next_cursor = encode_log_cursor(str(rows[-1]["timestamp"]), rows[-1]["id"]) if has_more else None
        logs_list = [row_to_log_entry(r, external=True, include_raw=include_raw) for r in rows]
        return logs_list, total, next_cursor

    try:
        logs_out, total_count, next_cursor = await run_search_query(_query_db, request)
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "searching logs")
    return ExternalLogListResponse(
        logs=logs_out,
        total=total_count,
        limit=limit,
        total_capped=bool(total_count >= TOTAL_CAP),
        next_cursor=next_cursor,
    )


async def count_logs(
    log_filter: LogFilter,
    *,
    group_by: Optional[str] = None,
    group_limit: int = 25,
    request: Optional[Request] = None,
) -> LogCountResponse:
    """Count logs matching the filter, optionally grouped by severity, app or source (at most group_limit groups)."""
    def _count_db(conn):
        return log_store.count(conn, log_filter, group_by, group_limit)

    try:
        total, grouped_by_out, breakdown_out, truncated_out = await run_search_query(_count_db, request)
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "counting logs")
    return LogCountResponse(
        total=total,
        grouped_by=grouped_by_out,
        breakdown=breakdown_out,
        truncated=truncated_out,
        time_range={"from": log_filter.from_ts, "to": log_filter.to_ts},
    )


async def log_context(
    log_id: int,
    *,
    lines: int,
    same_app: bool = False,
    include_raw: bool = False,
) -> ExternalLogContextResponse:
    """Up to lines logs before and after one log, from the same host. HTTP 404 when the log does not exist."""
    def _fetch(conn):
        found = log_store.context(conn, log_id, before=lines, after=lines, same_app=same_app, include_raw=include_raw)
        if found is None:
            return None, [], []
        target_row, before_rows, after_rows = found

        def _entries(rows):
            return [row_to_log_entry(r, external=True, include_raw=include_raw) for r in rows]

        return _entries([target_row])[0], _entries(before_rows), _entries(after_rows)

    target_entry, before_entries, after_entries = await run_db_query(_fetch)
    if not target_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log entry {log_id} not found.",
        )

    return ExternalLogContextResponse(
        target_id=log_id,
        target_log=target_entry,
        before_logs=before_entries,
        after_logs=after_entries,
    )
