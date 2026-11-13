"""
Log querying, streaming (SSE), and context API endpoints for LogShed.
"""

import asyncio
import time
import logging
import sqlite3
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user, is_session_revoked, run_db_query
from app.core.config import get_db_path
from app.core.sse import SSESubscriberLimitError, sse_manager
from app.models import (
    LogContextResponse,
    LogDeletePreviewResponse,
    LogDeleteRequest,
    LogDeleteResponse,
    LogFacetsResponse,
    LogListResponse,
)
from app.core.utils import AliasIndex, parse_multi_values
from app.services.log_facets import get_facets
from app.services.log_deletion import count_matching_logs_async, execute_delete_logs_async
from app.services import log_store
from app.services.log_query import (
    InvalidTimeBound,
    LogFilter,
    check_ui_filter_value_counts,
    db_busy_exception,
    row_to_log_entry,
    run_search_query,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["Logs"])

# Keep-alive comment interval for idle live log streams.
SSE_PING_INTERVAL_SECONDS = 15
# How often an open live stream checks that its session has not been ended by a logout or password change
SSE_SESSION_CHECK_SECONDS = 5.0


@router.get("", response_model=LogListResponse)
async def list_logs(
    query: Optional[str] = Query(None, description="Full-text search query (FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip (multi-value supported)"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name (multi-value supported)"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0-7, lower is more severe)"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter or relative duration (e.g. '1h')"),
    to: Optional[str] = Query(None, description="ISO datetime end filter or relative duration"),
    limit: int = Query(100, ge=1, le=1000, description="Max logs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(get_current_user),
) -> LogListResponse:
    """
    Query logs with full-text search, source/app filtering, severity range, and time bounds.
    Supports native SQLite FTS5 query syntax (e.g. column filters, AND/OR/NOT, wildcards)
    and automatic prefix matching for search-as-you-type. Time bounds accept ISO-8601
    strings with any offset or relative durations such as '1h', and are compared in UTC.
    """
    check_ui_filter_value_counts(source=source, app_name=app_name)
    try:
        log_filter = LogFilter.parse(
            query=query, sources=source, apps=app_name, severity_max=severity_max, from_=from_, to=to
        )
    except InvalidTimeBound as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # The total is capped, so a broad query does not count every row
    count_limit = max(1001, offset + limit + 1)

    def _query_db(conn):
        total, rows = log_store.search(conn, log_filter, fetch=limit, offset=offset, count_limit=count_limit)
        return [row_to_log_entry(r) for r in rows], total, bool(total >= count_limit)

    try:
        logs, total, total_capped = await run_search_query(_query_db, request)
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "listing logs")
    return LogListResponse(
        logs=logs,
        total=total,
        limit=limit,
        offset=offset,
        total_capped=total_capped,
    )


@router.get("/stream")
async def stream_logs(
    request: Request,
    severity_max: Optional[int] = Query(None, ge=0, le=7),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip (multi-value supported)"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name (multi-value supported)"),
    max_events: Optional[int] = Query(None, description="Max events to stream before closing (useful for tests/bounded streams)"),
    user: dict = Depends(get_current_user),
):
    """
    Server-Sent Events (SSE) endpoint to stream real-time incoming logs to the browser.
    """
    check_ui_filter_value_counts(source=source, app_name=app_name)
    alias_index = await run_db_query(AliasIndex.load)
    parsed_sources = set(alias_index.expand(source))
    parsed_apps = set(parse_multi_values(app_name))

    try:
        queue = await sse_manager.subscribe()
    except SSESubscriberLimitError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    async def event_generator() -> AsyncGenerator[str, None]:
        sent = 0
        session_checked_at = time.monotonic()
        try:
            while True:
                if max_events is not None and sent >= max_events:
                    break
                # A sign-out or password change elsewhere ends this stream too
                if time.monotonic() - session_checked_at >= SSE_SESSION_CHECK_SECONDS:
                    session_checked_at = time.monotonic()
                    if await is_session_revoked(user):
                        break

                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=SSE_PING_INTERVAL_SECONDS)
                    # Every entry already queued goes out in one chunk: a send per event costs
                    # far more than the event itself when logs arrive by the thousand
                    entries = [entry]
                    while not queue.empty():
                        entries.append(queue.get_nowait())

                    events = []
                    for entry in entries:
                        if max_events is not None and sent >= max_events:
                            break
                        # Apply optional stream filters
                        if severity_max is not None and entry.get("severity", 6) > severity_max:
                            continue
                        if parsed_sources:
                            entry_alias = (entry.get("source_alias") or "").strip()
                            entry_ip = (entry.get("source_ip") or "").strip()
                            if alias_index.is_docker(entry_alias, entry_ip):
                                if entry_alias not in parsed_sources:
                                    continue
                            else:
                                if entry_alias not in parsed_sources and entry_ip not in parsed_sources:
                                    continue
                        if parsed_apps and entry.get("app_name") not in parsed_apps:
                            continue

                        # Pre-encoded once per entry by the broadcaster, without private keys
                        events.append(f"event: log\ndata: {entry.data}\n\n")
                        sent += 1
                    if events:
                        yield "".join(events)
                except asyncio.TimeoutError:
                    # A disconnect while events flow surfaces as a failed send; while idle it
                    # is checked here, once per ping.
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
        finally:
            await sse_manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/facets", response_model=LogFacetsResponse)
async def get_log_facets(
    user: dict = Depends(get_current_user),
) -> LogFacetsResponse:
    """Distinct sources, apps and their mappings for the Console filters (get_facets)."""
    return await get_facets()


@router.get("/{id}/context", response_model=LogContextResponse)
async def get_log_context(
    id: int,
    lines: int = Query(10, ge=1, le=100, description="Total surrounding lines, split evenly before and after (at least 1 on each side)"),
    same_app: bool = Query(False, description="Restrict surrounding logs to the same application name"),
    user: dict = Depends(get_current_user),
) -> LogContextResponse:
    """
    Fetch surrounding context lines symmetrically before and after target log.
    `lines` is the total window: lines // 2 (at least 1) are returned on each side, so the
    UI asking for 10 shows 5 before and 5 after.
    Defaults to all host activity (same source_alias, or same source_ip for logs without an
    alias). If same_app=True, restricts to the same app_name.
    """
    def _fetch_context(conn):
        half = max(1, lines // 2)
        found = log_store.context(conn, id, before=half, after=half, same_app=same_app)
        if found is None:
            return None, []
        target_row, before_rows, after_rows = found
        return target_row, [row_to_log_entry(r) for r in (*before_rows, target_row, *after_rows)]

    target_row, logs = await run_db_query(_fetch_context)
    if not target_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log entry {id} not found.",
        )

    return LogContextResponse(target_id=id, logs=logs)


@router.delete("/{id}", response_model=LogDeleteResponse)
async def delete_single_log(
    id: int,
    user: dict = Depends(get_current_user),
) -> LogDeleteResponse:
    """
    Delete a single log entry by its primary key ID.
    """
    db_path = get_db_path()
    req = LogDeleteRequest(log_ids=[id])
    try:
        result = await execute_delete_logs_async(db_path, req)
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "deleting a log entry")
    if result["deleted_count"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log entry {id} not found.",
        )
    return LogDeleteResponse(**result)


@router.post("/delete", response_model=LogDeleteResponse)
async def delete_logs_batch(
    request: LogDeleteRequest,
    user: dict = Depends(get_current_user),
) -> LogDeleteResponse:
    """
    Delete logs matching criteria or specific IDs.
    Guards against unconstrained requests unless delete_all=True.
    A locked database (for example during VACUUM) returns 503 so the client can retry.
    """
    db_path = get_db_path()
    try:
        result = await execute_delete_logs_async(db_path, request)
        return LogDeleteResponse(**result)
    except InvalidTimeBound as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "deleting logs")


@router.post("/delete/preview", response_model=LogDeletePreviewResponse)
async def preview_delete_logs(
    request: LogDeleteRequest,
    user: dict = Depends(get_current_user),
) -> LogDeletePreviewResponse:
    """
    Calculate count of logs matching deletion criteria without deleting them.
    """
    db_path = get_db_path()
    try:
        matched = await count_matching_logs_async(db_path, request)
        return LogDeletePreviewResponse(matched_count=matched)
    except InvalidTimeBound as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "counting logs to delete")
