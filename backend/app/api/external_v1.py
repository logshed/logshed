"""
External REST API v1 endpoints for LogShed.
Supports Bearer token authentication, scoped permissions, multi-session maintenance tracking,
system telemetry and metrics, and log and incident queries.
"""

import datetime
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import require_api_token, require_auth_or_token, run_db_query
from app.api.system import run_prune_operation, run_vacuum_operation
from app.models import (
    AlertHistoryItem,
    ApiTokenVerifyResponse,
    ExternalMaintenanceDisableRequest,
    ExternalMaintenanceDisableResponse,
    ExternalMaintenanceEnableRequest,
    ExternalMaintenanceEnableResponse,
    ExternalMaintenanceStatusResponse,
    ExternalLogContextResponse,
    LogCountResponse,
    LogFacetsResponse,
    ExternalLogListResponse,
    PruneResponse,
    SystemMetricsResponse,
    VacuumResponse,
    VersionResponse,
)
from app.services.maintenance_service import (
    MaintenanceSessionLimitError,
    create_maintenance_session,
    disable_maintenance_sessions,
    get_maintenance_status,
)
from app.services import system_metrics
from app.services.alert_history import list_alert_history, redact_alert_item
from app.services.log_facets import get_facets
from app.services.log_search import (
    MAX_CURSOR_LENGTH,
    count_logs,
    log_context,
    parse_api_log_filter,
    search_logs,
)
from app.services.version_service import check_for_updates

from app.api.ingest import router as ingest_router

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["External API v1"])
router.include_router(ingest_router)


# ---------------------------------------------------------------------------
# Auth Verification
# ---------------------------------------------------------------------------

@router.get("/auth/verify", response_model=ApiTokenVerifyResponse)
async def verify_token(
    token: dict[str, Any] = Depends(require_api_token([])),
) -> ApiTokenVerifyResponse:
    """Verify API token validity and retrieve its assigned scopes and metadata."""
    return ApiTokenVerifyResponse(
        valid=True,
        name=token["name"],
        scopes=token["scopes"],
        expires_at=token.get("expires_at"),
    )


# ---------------------------------------------------------------------------
# Multi-Session Maintenance Control
# ---------------------------------------------------------------------------

@router.post("/maintenance/enable", response_model=ExternalMaintenanceEnableResponse)
async def enable_maintenance_window(
    payload: ExternalMaintenanceEnableRequest,
    auth: dict[str, Any] = Depends(require_auth_or_token(["maintenance:write"])),
) -> ExternalMaintenanceEnableResponse:
    """
    Start an on-demand maintenance session.
    Silences alert rules and optionally filters incoming errors for the specified duration.
    """
    initiated_by = (
        f"api_token: {auth.get('name', 'token')}"
        if auth.get("auth_type") == "api_token"
        else f"admin: {auth.get('username') or 'session'}"
    )
    token_id = auth.get("id") if auth.get("auth_type") == "api_token" else None

    def _create(conn):
        return create_maintenance_session(
            conn,
            duration_minutes=payload.duration_minutes,
            reason=payload.reason,
            log_handling=payload.log_handling,
            target_app=payload.target_app,
            target_host=payload.target_host,
            initiated_by=initiated_by,
            token_id=token_id,
            trigger_label=(
                f"API - {auth.get('name') or 'token'}"
                if auth.get("auth_type") == "api_token"
                else "UI"
            ),
        )

    try:
        session_data = await run_db_query(_create)
    except MaintenanceSessionLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ExternalMaintenanceEnableResponse(**session_data)


@router.post("/maintenance/disable", response_model=ExternalMaintenanceDisableResponse)
async def disable_maintenance_window(
    payload: Optional[ExternalMaintenanceDisableRequest] = None,
    auth: dict[str, Any] = Depends(require_auth_or_token(["maintenance:write"])),
) -> ExternalMaintenanceDisableResponse:
    """
    Stop an on-demand maintenance window.
    Terminates the specified session, or clears all active sessions if session_id is omitted.
    An unknown or already ended session_id returns 404, like the MCP disable_maintenance tool.
    """
    session_id = payload.session_id if payload else None

    def _disable(conn):
        return disable_maintenance_sessions(conn, session_id=session_id)

    terminated_count, remaining_active = await run_db_query(_disable)
    # Raise after the query so the expired-session sweep inside disable_maintenance_sessions still commits
    if session_id and session_id.strip() and terminated_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active maintenance session with ID {session_id.strip()}.",
        )
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return ExternalMaintenanceDisableResponse(
        active=remaining_active > 0,
        terminated_sessions=terminated_count,
        remaining_active_sessions=remaining_active,
        server_time=now_iso,
    )


@router.get("/maintenance/status", response_model=ExternalMaintenanceStatusResponse)
async def get_maintenance_status_v1(
    auth: dict[str, Any] = Depends(require_auth_or_token(["maintenance:read"])),
) -> ExternalMaintenanceStatusResponse:
    """Query current maintenance window status including all active concurrent sessions."""
    status_obj = await run_db_query(get_maintenance_status)
    return ExternalMaintenanceStatusResponse(
        active=status_obj.active,
        until=status_obj.until,
        remaining_seconds=status_obj.remaining_seconds,
        log_handling=status_obj.log_handling,
        active_sessions_count=status_obj.active_sessions_count,
        sessions=status_obj.sessions,
    )


# ---------------------------------------------------------------------------
# System Telemetry & Metrics
# ---------------------------------------------------------------------------

@router.get("/system/metrics", response_model=SystemMetricsResponse)
async def get_system_metrics(
    token: dict[str, Any] = Depends(require_api_token(["system:read"])),
) -> SystemMetricsResponse:
    """Retrieve system metrics, throughput, error breakdown, and alert state (cached error stats)."""
    return await system_metrics.get_system_metrics()


@router.get("/system/version", response_model=VersionResponse)
async def get_version_v1(
    token: dict[str, Any] = Depends(require_api_token(["system:read"])),
) -> VersionResponse:
    """
    Return the running version and the latest stable release on GHCR.
    Serves the shared 1-hour update check cache and has no refresh option, so a token
    cannot force registry requests. Respects the check_for_updates setting.
    """
    info = await check_for_updates()
    return VersionResponse(**info)


# ---------------------------------------------------------------------------
# External Maintenance Operations (Compaction & Pruning)
# ---------------------------------------------------------------------------

@router.post("/system/prune", response_model=PruneResponse)
async def trigger_prune_v1(
    token: dict[str, Any] = Depends(require_api_token(["system:write"])),
) -> PruneResponse:
    """
    Trigger retention pruning and database compaction.
    Returns 409 while another prune or compaction is running. A 5-minute cooldown starts
    only after a successful run to prevent database lock disruption.
    """
    return await run_prune_operation(cooldown=True)


@router.post("/system/vacuum", response_model=VacuumResponse)
async def trigger_vacuum_v1(
    token: dict[str, Any] = Depends(require_api_token(["system:write"])),
) -> VacuumResponse:
    """
    Execute database VACUUM to reclaim host disk space from freelist pages.
    Returns 409 while another prune or compaction is running. A 5-minute cooldown starts
    only after a successful run.
    """
    return await run_vacuum_operation(cooldown=True)


# ---------------------------------------------------------------------------
# Log & Incident Query Endpoints
# ---------------------------------------------------------------------------

@router.get("/logs", response_model=ExternalLogListResponse, response_model_exclude_none=True)
async def search_logs_v1(
    query: Optional[str] = Query(None, description="Full-text search query (SQLite FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip (up to 50 values)"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name (up to 50 values)"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0 - 7)"),
    severity: Optional[str] = Query(None, description="Named severity (e.g. 'error', 'warning', 'info') or numeric code"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter or relative duration (e.g. '1h', '24h')"),
    to: Optional[str] = Query(None, description="ISO datetime end filter or relative duration"),
    limit: int = Query(50, ge=1, le=1000, description="Max logs to return"),
    cursor: Optional[str] = Query(
        None,
        max_length=MAX_CURSOR_LENGTH,
        description="Opaque pagination cursor from the previous page's next_cursor (newest first)",
    ),
    include_raw: bool = Query(False, description="Include the raw syslog packet in each entry"),
    request: Request = None,  # type: ignore[assignment]
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> ExternalLogListResponse:
    """Search and filter logs (newest first) with keyset pagination via next_cursor."""
    log_filter = parse_api_log_filter(query, source, app_name, severity_max, severity, from_, to)
    return await search_logs(log_filter, limit=limit, cursor=cursor, include_raw=include_raw, request=request)


@router.get("/logs/count", response_model=LogCountResponse)
async def count_logs_v1(
    query: Optional[str] = Query(None, description="Full-text search query (SQLite FTS5)"),
    source: Optional[list[str]] = Query(None, description="Filter by source_alias or source_ip (up to 50 values)"),
    app_name: Optional[list[str]] = Query(None, description="Filter by application name (up to 50 values)"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0 - 7)"),
    severity: Optional[str] = Query(None, description="Named severity (e.g. 'error', 'warning', 'info') or numeric code"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter or relative duration (e.g. '1h', '24h')"),
    to: Optional[str] = Query(None, description="ISO datetime end filter or relative duration"),
    group_by: Optional[str] = Query(None, description="Group breakdown by 'severity', 'app', or 'source'"),
    group_limit: int = Query(25, ge=1, le=200, description="Max groups returned for 'app' and 'source' grouping"),
    request: Request = None,  # type: ignore[assignment]
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogCountResponse:
    """Count logs matching filters with optional grouping by severity, app, or source."""
    log_filter = parse_api_log_filter(query, source, app_name, severity_max, severity, from_, to)

    return await count_logs(log_filter, group_by=group_by, group_limit=group_limit, request=request)


@router.get("/logs/{log_id}/context", response_model=ExternalLogContextResponse, response_model_exclude_none=True)
async def get_log_context_v1(
    log_id: int,
    lines: int = Query(10, ge=1, le=50, description="Context lines before and after target log"),
    same_app: bool = Query(False, description="Restrict context to logs sharing the target's app_name"),
    include_raw: bool = Query(False, description="Include the raw syslog packet in each entry"),
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> ExternalLogContextResponse:
    """Fetch symmetrical surrounding context logs before and after a specific log entry."""
    return await log_context(log_id, lines=lines, same_app=same_app, include_raw=include_raw)


@router.get("/logs/facets", response_model=LogFacetsResponse)
async def get_log_facets_v1(
    token: dict[str, Any] = Depends(require_api_token(["logs:read"])),
) -> LogFacetsResponse:
    """Retrieve distinct sources, applications, and source-to-application relationships."""
    return await get_facets()


@router.get("/alerts/history", response_model=list[AlertHistoryItem])
async def list_alert_history_v1(
    rule_id: Optional[int] = Query(None, description="Optional alert rule ID filter"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    token: dict[str, Any] = Depends(require_api_token(["alerts:read"])),
) -> list[AlertHistoryItem]:
    """Retrieve historical alert firing incidents and AI diagnosis summaries."""
    history = await list_alert_history(limit, offset, rule_id)
    return [redact_alert_item(item) for item in history.items]
