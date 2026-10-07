"""
Log querying, streaming (SSE), and context API endpoints for LogShed.
"""

import asyncio
import datetime
import json
import logging
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user, run_db_query
from app.core.config import get_db_path
from app.core.sse import sse_manager
from app.models import (
    LogContextResponse,
    LogDeletePreviewResponse,
    LogDeleteRequest,
    LogDeleteResponse,
    LogEntry,
    LogFacetsResponse,
    LogListResponse,
)
from app.core.utils import (
    escape_fts_tokens,
    expand_source_aliases,
    format_fts_query,
    parse_multi_values,
)
from app.services.log_deletion import count_matching_logs_async, execute_delete_logs_async

import sqlite3

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get("", response_model=LogListResponse)
async def list_logs(
    query: Optional[str] = Query(None, description="Full-text search query (FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip (multi-value supported)"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name (multi-value supported)"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0-7, lower is more severe)"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter"),
    to: Optional[str] = Query(None, description="ISO datetime end filter"),
    limit: int = Query(100, ge=1, le=1000, description="Max logs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    user: dict = Depends(get_current_user),
) -> LogListResponse:
    """
    Query logs with full-text search, source/app filtering, severity range, and time bounds.
    Supports native SQLite FTS5 query syntax (e.g. column filters, AND/OR/NOT, wildcards)
    and automatic prefix matching for search-as-you-type.
    """
    if from_:
        try:
            datetime.datetime.fromisoformat(from_)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="Invalid datetime format. Expected ISO-8601 string.",
            )

    if to:
        try:
            datetime.datetime.fromisoformat(to)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="Invalid datetime format. Expected ISO-8601 string.",
            )

    def _query_db(conn):
        where_clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        is_fts = bool(query and query.strip())

        if is_fts:
            fts_term = format_fts_query(query)
            if not fts_term.strip():
                where_clauses.append("0")
            else:
                # Uncorrelated subquery: FTS5 evaluates the MATCH exactly once. A JOIN lets the planner
                # drive from a relational index (e.g. app_name) and re-run the MATCH per candidate row,
                # rebuilding the full doclist of auto-prefixed terms each time (quadratic cost).
                where_clauses.append("logs.id IN (SELECT rowid FROM logs_fts WHERE logs_fts MATCH :fts_term)")
                params["fts_term"] = fts_term

        parsed_sources = expand_source_aliases(source, conn=conn)
        if parsed_sources:
            src_placeholders = ", ".join(f":src_{i}" for i in range(len(parsed_sources)))
            where_clauses.append(f"(logs.source_alias IN ({src_placeholders}) OR logs.source_ip IN ({src_placeholders}))")
            for i, s in enumerate(parsed_sources):
                params[f"src_{i}"] = s

        parsed_apps = parse_multi_values(app_name)
        if parsed_apps:
            app_placeholders = ", ".join(f":app_{i}" for i in range(len(parsed_apps)))
            where_clauses.append(f"logs.app_name IN ({app_placeholders})")
            for i, a in enumerate(parsed_apps):
                params[f"app_{i}"] = a

        if severity_max is not None:
            where_clauses.append("logs.severity <= :severity_max")
            params["severity_max"] = severity_max

        if from_:
            where_clauses.append("logs.timestamp >= :from_time")
            params["from_time"] = from_

        if to:
            where_clauses.append("logs.timestamp <= :to_time")
            params["to_time"] = to

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total count query: cap count evaluation to avoid full table scans on broad queries
        count_limit = max(1001, offset + limit + 1)
        params["count_limit"] = count_limit
        count_sql = f"SELECT COUNT(*) FROM (SELECT 1 FROM logs {where_sql} LIMIT :count_limit)"
        # Log selection query
        select_sql = f"""
            SELECT logs.id, logs.timestamp, logs.received_at, logs.source_ip, logs.source_alias,
                   logs.app_name, logs.facility, logs.severity, logs.message, logs.raw
            FROM logs
            {where_sql}
            ORDER BY logs.timestamp DESC, logs.id DESC
            LIMIT :limit OFFSET :offset
        """

        cursor = conn.cursor()
        try:
            cursor.execute(count_sql, params)
            total = cursor.fetchone()[0]
            cursor.execute(select_sql, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if is_fts:
                logger.info(f"FTS5 query '{params.get('fts_term')}' failed ({e}), falling back to escaped token search.")
                fallback_term = escape_fts_tokens(query)
                if not fallback_term.strip():
                    total = 0
                    rows = []
                else:
                    params["fts_term"] = fallback_term
                    try:
                        cursor.execute(count_sql, params)
                        total = cursor.fetchone()[0]
                        cursor.execute(select_sql, params)
                        rows = cursor.fetchall()
                    except sqlite3.OperationalError:
                        total = 0
                        rows = []
            else:
                raise

        logs = [
            LogEntry(
                id=r["id"],
                timestamp=str(r["timestamp"]),
                received_at=str(r["received_at"]),
                source_ip=r["source_ip"],
                source_alias=r["source_alias"],
                app_name=r["app_name"],
                facility=r["facility"],
                severity=r["severity"],
                message=r["message"],
                raw=r["raw"],
            )
            for r in rows
        ]

        total_capped = bool(total >= count_limit)
        return logs, total, total_capped

    logs, total, total_capped = await run_db_query(_query_db)
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
    parsed_sources = set(
        await run_db_query(lambda conn: expand_source_aliases(source, conn=conn))
    )
    parsed_apps = set(parse_multi_values(app_name))

    queue = await sse_manager.subscribe()

    async def event_generator() -> AsyncGenerator[str, None]:
        sent = 0
        try:
            while True:
                if max_events is not None and sent >= max_events:
                    break
                if await request.is_disconnected():
                    break

                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=0.5)

                    # Apply optional stream filters
                    if severity_max is not None and entry.get("severity", 6) > severity_max:
                        continue
                    if (
                        parsed_sources
                        and entry.get("source_alias") not in parsed_sources
                        and entry.get("source_ip") not in parsed_sources
                    ):
                        continue
                    if parsed_apps and entry.get("app_name") not in parsed_apps:
                        continue

                    data = json.dumps(entry)
                    sent += 1
                    yield f"event: log\ndata: {data}\n\n"
                except asyncio.TimeoutError:
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
    """
    Fetch all distinct sources, apps, and their bidirectional mappings
    across the entire database, including configured host aliases.
    """
    def _fetch_facets(conn):
        cursor = conn.cursor()
        # Fast loose index skip-scans (O(K log N) B-Tree seeks) over covering index.
        # Eliminates the 7-day cutoff so that any host or app within retention (even with only 1 log)
        # is retained, executing in single-digit milliseconds without scanning millions of rows.

        # 1. Distinct sources
        cursor.execute(
            """
            WITH RECURSIVE distinct_sources AS (
                SELECT MIN(source_alias) AS val FROM logs WHERE source_alias != ''
                UNION ALL
                SELECT (SELECT MIN(source_alias) FROM logs WHERE source_alias > s.val AND source_alias != '')
                FROM distinct_sources s
                WHERE s.val IS NOT NULL
            )
            SELECT s.val, (SELECT source_ip FROM logs WHERE source_alias = s.val LIMIT 1)
            FROM distinct_sources s
            WHERE s.val IS NOT NULL;
            """
        )
        sources = cursor.fetchall()

        # 2. Distinct fallback IPs for logs where source_alias is empty
        cursor.execute(
            """
            WITH RECURSIVE distinct_ips AS (
                SELECT MIN(source_ip) AS val FROM logs WHERE source_alias = '' AND source_ip != ''
                UNION ALL
                SELECT (SELECT MIN(source_ip) FROM logs WHERE source_alias = '' AND source_ip > s.val AND source_ip != '')
                FROM distinct_ips s
                WHERE s.val IS NOT NULL
            )
            SELECT val FROM distinct_ips WHERE val IS NOT NULL;
            """
        )
        fallback_ips = [r[0] for r in cursor.fetchall()]

        # 3. Distinct apps
        cursor.execute(
            """
            WITH RECURSIVE distinct_apps AS (
                SELECT MIN(app_name) AS val FROM logs WHERE app_name != ''
                UNION ALL
                SELECT (SELECT MIN(app_name) FROM logs WHERE app_name > a.val AND app_name != '')
                FROM distinct_apps a
                WHERE a.val IS NOT NULL
            )
            SELECT val FROM distinct_apps WHERE val IS NOT NULL;
            """
        )
        apps_res = [r[0] for r in cursor.fetchall()]

        # 4. Distinct (source_alias, app_name, source_ip) pairs via covering index skip-scan
        cursor.execute(
            """
            WITH RECURSIVE cte(s, a, ip) AS (
                SELECT s.source_alias, s.app_name, s.source_ip
                FROM (
                    SELECT source_alias, app_name, source_ip
                    FROM logs
                    WHERE source_alias != '' AND app_name != ''
                    ORDER BY source_alias ASC, app_name ASC
                    LIMIT 1
                ) s
                UNION ALL
                SELECT nxt.source_alias, nxt.app_name, nxt.source_ip
                FROM cte
                JOIN logs nxt ON nxt.id = COALESCE(
                    (SELECT id FROM logs WHERE source_alias = cte.s AND app_name > cte.a AND app_name != '' ORDER BY app_name ASC LIMIT 1),
                    (SELECT id FROM logs WHERE source_alias > cte.s AND source_alias != '' AND app_name != '' ORDER BY source_alias ASC, app_name ASC LIMIT 1)
                )
            )
            SELECT s AS source_alias, a AS app_name, ip AS source_ip FROM cte;
            """
        )
        pairs = cursor.fetchall()

        # Edge case: Mappings for fallback IPs where source_alias was empty
        if fallback_ips:
            for ip in fallback_ips:
                cursor.execute(
                    "SELECT DISTINCT app_name FROM logs WHERE source_alias = '' AND source_ip = ? AND app_name != ''",
                    (ip,),
                )
                for r in cursor.fetchall():
                    pairs.append((ip, r[0], ip))

        cursor.execute("SELECT ip, alias FROM host_aliases")
        alias_rows = cursor.fetchall()
        aliases_map = {r["ip"]: r["alias"] for r in alias_rows if r["alias"]}

        sources_set = set()
        apps_set = set(apps_res)
        host_to_apps: dict[str, set[str]] = {}
        app_to_hosts: dict[str, set[str]] = {}

        # Add all configured aliases
        for alias in aliases_map.values():
            if alias:
                sources_set.add(alias)
                if alias not in host_to_apps:
                    host_to_apps[alias] = set()

        # Add all distinct sources from logs (even if they logged without app_name)
        for row in sources:
            s = row[0]
            ip = row[1]
            canonical = (ip and aliases_map.get(ip)) or aliases_map.get(s) or s or ip
            sources_set.add(canonical)
            if canonical not in host_to_apps:
                host_to_apps[canonical] = set()

        for ip in fallback_ips:
            canonical = aliases_map.get(ip, ip)
            sources_set.add(canonical)
            if canonical not in host_to_apps:
                host_to_apps[canonical] = set()

        for r in pairs:
            raw_alias = r[0]
            app = r[1]
            ip = r[2]

            # Canonical host resolution:
            # If the IP or raw_alias matches a configured host alias, use the alias.
            # Otherwise use raw_alias (which is already the IP for unaliased hosts).
            canonical_host = aliases_map.get(ip) or aliases_map.get(raw_alias) or raw_alias or ip
            if not canonical_host:
                continue

            sources_set.add(canonical_host)

            if app:
                apps_set.add(app)
                if canonical_host not in host_to_apps:
                    host_to_apps[canonical_host] = set()
                host_to_apps[canonical_host].add(app)

                if app not in app_to_hosts:
                    app_to_hosts[app] = set()
                app_to_hosts[app].add(canonical_host)

        # Safety: Ensure no IP that has a distinct alias remains in sources_set or host_to_apps
        for ip, alias in aliases_map.items():
            if ip != alias:
                if ip in sources_set:
                    sources_set.remove(ip)
                if ip in host_to_apps:
                    if alias in host_to_apps:
                        host_to_apps[alias].update(host_to_apps[ip])
                    else:
                        host_to_apps[alias] = set(host_to_apps[ip])
                    del host_to_apps[ip]
                for app, hosts in app_to_hosts.items():
                    if ip in hosts:
                        hosts.remove(ip)
                        hosts.add(alias)

        return {
            "sources": sorted(sources_set),
            "apps": sorted(apps_set),
            "host_to_apps": {h: sorted(apps) for h, apps in sorted(host_to_apps.items())},
            "app_to_hosts": {a: sorted(hosts) for a, hosts in sorted(app_to_hosts.items())},
        }

    data = await run_db_query(_fetch_facets)
    return LogFacetsResponse(**data)


@router.get("/{id}/context", response_model=LogContextResponse)
async def get_log_context(
    id: int,
    lines: int = Query(10, ge=1, le=100, description="Surrounding lines before and after"),
    same_app: bool = Query(False, description="Restrict surrounding logs to the same application name"),
    user: dict = Depends(get_current_user),
) -> LogContextResponse:
    """
    Fetch surrounding context lines symmetrically before and after target log.
    Defaults to all host activity (same source_alias). If same_app=True, restricts to the same app_name.
    """
    def _fetch_context(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs WHERE id = ?", (id,))
        target_row = cursor.fetchone()
        if not target_row:
            return None, []

        source_alias = target_row["source_alias"]
        app_name = target_row["app_name"]
        t_time = target_row["timestamp"]
        t_id = target_row["id"]
        half = lines // 2

        app_clause = "AND app_name = ? " if same_app else ""
        app_params = (app_name,) if same_app else ()

        # Fetch lines before (chronologically earlier)
        before_sql = f"""
            SELECT * FROM logs 
            WHERE source_alias = ? {app_clause}AND (timestamp < ? OR (timestamp = ? AND id < ?))
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        """
        cursor.execute(
            before_sql,
            (source_alias, *app_params, t_time, t_time, t_id, half),
        )
        before_rows = cursor.fetchall()
        before_rows.reverse()  # Chronological order

        # Fetch lines after (chronologically later)
        after_sql = f"""
            SELECT * FROM logs 
            WHERE source_alias = ? {app_clause}AND (timestamp > ? OR (timestamp = ? AND id > ?))
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
        """
        cursor.execute(
            after_sql,
            (source_alias, *app_params, t_time, t_time, t_id, half),
        )
        after_rows = cursor.fetchall()

        all_rows = before_rows + [target_row] + after_rows
        logs = [
            LogEntry(
                id=r["id"],
                timestamp=str(r["timestamp"]),
                received_at=str(r["received_at"]),
                source_ip=r["source_ip"],
                source_alias=r["source_alias"],
                app_name=r["app_name"],
                facility=r["facility"],
                severity=r["severity"],
                message=r["message"],
                raw=r["raw"],
            )
            for r in all_rows
        ]
        return target_row, logs

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
    result = await execute_delete_logs_async(db_path, req)
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
    """
    db_path = get_db_path()
    try:
        result = await execute_delete_logs_async(db_path, request)
        return LogDeleteResponse(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


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
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

