"""
Built-in Model Context Protocol (MCP) server for LogShed.

Provides FastMCP server implementation exposing tools, resources, and prompts
for AI assistants to inspect telemetry, query logs, examine incident context,
and manage maintenance windows.
"""

import json
import logging
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.api.deps import run_db_query
from app.api.external_v1 import (
    count_logs_v1,
    get_log_context_v1,
    get_system_metrics as fetch_system_metrics_v1,
    list_alert_history_v1,
    search_logs_v1,
)
from app.api.logs import get_log_facets
from app.core.utils import format_severity_label, resolve_severity
from app.services.maintenance_service import (
    create_maintenance_session,
    disable_maintenance_sessions,
    get_maintenance_status,
)

logger = logging.getLogger(__name__)


def _project_log_entry(
    entry: dict[str, Any],
    include_raw: bool = False,
    fields: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Project and format a log entry for token-efficient MCP responses.
    Adds human-readable severity_label, omits raw syslog packets by default,
    and filters to requested fields when specified.
    """
    out = dict(entry)
    sev = out.get("severity")
    out["severity_label"] = format_severity_label(sev)

    if fields:
        allowed = set(fields)
        return {k: v for k, v in out.items() if k in allowed}

    if not include_raw:
        out.pop("raw", None)
        out.pop("facility", None)
        out.pop("received_at", None)

    return out


def _needs_raw(include_raw: bool, fields: Optional[list[str]]) -> bool:
    """Return True when the raw packet must be fetched (explicit include_raw or requested via fields)."""
    return bool(include_raw or (fields and "raw" in fields))


def create_mcp_server() -> FastMCP:
    """
    Factory creating and configuring the LogShed FastMCP server instance.
    Registers all operational tools, resources, and investigation prompts.
    """
    server = FastMCP(
        name="LogShed",
        instructions=(
            "LogShed MCP Server. Use this server to inspect homelab system metrics, "
            "search logs using FTS5, retrieve surrounding incident context, "
            "inspect alert firing history, and manage maintenance sessions."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    # -------------------------------------------------------------------------
    # MCP Tools
    # -------------------------------------------------------------------------

    @server.tool(
        name="get_system_metrics",
        description=(
            "Inspect homelab log rates, error counts (1h/24h), top error apps/hosts, "
            "active alert states, disk usage, and maintenance status."
        ),
    )
    async def get_system_metrics() -> dict[str, Any]:
        """Fetch system metrics, error distribution, and operational state."""
        resp = await fetch_system_metrics_v1(token={"scopes": ["system:read"]})
        return resp.model_dump()

    @server.tool(
        name="list_services",
        description=(
            "Discover all distinct applications (app_name) and host sources "
            "(source_alias / source_ip) currently sending logs to LogShed."
        ),
    )
    async def list_services() -> dict[str, Any]:
        """Discover distinct application names and host sources."""
        facets = await get_log_facets(user={"auth_type": "api_token"})
        return {
            "apps": facets.apps,
            "sources": facets.sources,
            "host_to_apps": facets.host_to_apps,
            "app_to_hosts": facets.app_to_hosts,
        }

    @server.tool(
        name="count_logs",
        description=(
            "Count homelab logs matching filters with optional grouping. "
            "Very fast and token-efficient for checking error volume, trends, or verifying issue presence. "
            "Accepts relative time ('1h', '24h', 'since_midnight') and named severity ('error', 'warning', 'info'). "
            "group_by supports 'severity' (alias 'level'), 'app' (alias 'app_name'), or "
            "'source' (aliases 'source_alias', 'host'). "
            "For 'app' and 'source' grouping at most group_limit groups (default 25, max 200) are returned; "
            "the response sets truncated=true when more groups exist, while total still counts all matching logs."
        ),
    )
    async def count_logs(
        query: Optional[str] = None,
        apps: Optional[list[str]] = None,
        sources: Optional[list[str]] = None,
        severity: Optional[str | int] = None,
        severity_max: Optional[int] = None,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        group_by: Optional[str] = None,
        group_limit: int = 25,
    ) -> dict[str, Any]:
        """Count logs matching filters with optional grouping by severity, app, or source."""
        effective_sev = None
        if severity is not None:
            effective_sev = resolve_severity(severity)
        elif severity_max is not None:
            effective_sev = resolve_severity(severity_max)

        res = await count_logs_v1(
            query=query,
            source=sources,
            app_name=apps,
            severity_max=effective_sev,
            severity=None,
            from_=from_time,
            to=to_time,
            group_by=group_by,
            group_limit=min(max(1, group_limit), 200),
            token={"scopes": ["logs:read"]},
        )
        return res.model_dump()

    @server.tool(
        name="query_logs",
        description=(
            "Search homelab logs using full-text search (FTS5 syntax), application filters, "
            "host filters, severity thresholds, and time bounds. "
            "Returns compact log entries by default (omits redundant raw syslog packets to minimize token usage). "
            "Accepts relative time ('1h', '24h', 'since_midnight') and named severities ('error', 'warning', 'info'). "
            "Set include_raw=True or specify fields list to customize output. "
            "Results are newest first; when next_cursor is present, pass it back as cursor to fetch the next page."
        ),
    )
    async def query_logs(
        query: Optional[str] = None,
        apps: Optional[list[str]] = None,
        sources: Optional[list[str]] = None,
        severity: Optional[str | int] = None,
        severity_max: Optional[int] = None,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
        include_raw: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Search logs matching query filters, returning structured log entries and totals."""
        clamped_limit = min(max(1, limit), 200)
        effective_sev = None
        if severity is not None:
            effective_sev = resolve_severity(severity)
        elif severity_max is not None:
            effective_sev = resolve_severity(severity_max)

        res = await search_logs_v1(
            query=query,
            source=sources,
            app_name=apps,
            severity_max=effective_sev,
            severity=None,
            from_=from_time,
            to=to_time,
            limit=clamped_limit,
            cursor=cursor,
            include_raw=_needs_raw(include_raw, fields),
            token={"scopes": ["logs:read"]},
        )
        data = res.model_dump()
        projected_logs = [
            _project_log_entry(l, include_raw=include_raw, fields=fields)
            for l in data.get("logs", [])
        ]
        data["logs"] = projected_logs
        return data

    @server.tool(
        name="get_log_context",
        description=(
            "Fetch surrounding log lines before and after a specific log ID "
            "to inspect full stack trace and incident context. "
            "By default returns compact log entries without redundant raw syslog packets."
        ),
    )
    async def get_log_context(
        log_id: int,
        lines: int = 10,
        same_app: bool = False,
        include_raw: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Fetch chronological log lines surrounding a target log identifier."""
        clamped_lines = min(max(1, lines), 50)
        res = await get_log_context_v1(
            log_id=log_id,
            lines=clamped_lines,
            same_app=same_app,
            include_raw=_needs_raw(include_raw, fields),
            token={"scopes": ["logs:read"]},
        )
        data = res.model_dump()
        if data.get("target_log"):
            data["target_log"] = _project_log_entry(
                data["target_log"], include_raw=include_raw, fields=fields
            )
        data["before_logs"] = [
            _project_log_entry(l, include_raw=include_raw, fields=fields)
            for l in data.get("before_logs", [])
        ]
        data["after_logs"] = [
            _project_log_entry(l, include_raw=include_raw, fields=fields)
            for l in data.get("after_logs", [])
        ]
        return data

    @server.tool(
        name="get_recent_alerts",
        description=(
            "Retrieve recent alert incident firing records, triggering sample logs, "
            "and previous AI summaries."
        ),
    )
    async def get_recent_alerts(limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve historical alert firing incidents."""
        clamped_limit = min(max(1, limit), 50)
        items = await list_alert_history_v1(
            rule_id=None,
            limit=clamped_limit,
            offset=0,
            token={"scopes": ["alerts:read"]},
        )
        return [item.model_dump() for item in items]

    @server.tool(
        name="enable_maintenance",
        description=(
            "Start an on-demand maintenance window to silence alert notifications "
            "and optionally drop error logs during planned operations. Returns a session_id."
        ),
    )
    async def enable_maintenance(
        duration_minutes: int = 30,
        reason: Optional[str] = None,
        log_handling: str = "silence_alerts",
        target_app: Optional[str] = None,
        target_host: Optional[str] = None,
    ) -> dict[str, Any]:
        """Activate a scoped maintenance session."""
        valid_handling = log_handling if log_handling in ("silence_alerts", "drop_errors") else "silence_alerts"
        if len(target_app or "") > 255 or len(target_host or "") > 255:
            raise ValueError("target_app and target_host must be at most 255 characters.")
        clamped_duration = max(1, duration_minutes)

        def _create(conn):
            return create_maintenance_session(
                conn,
                duration_minutes=clamped_duration,
                reason=reason,
                log_handling=valid_handling,
                target_app=target_app,
                target_host=target_host,
                initiated_by="mcp",
                trigger_label="MCP",
            )

        return await run_db_query(_create)

    @server.tool(
        name="disable_maintenance",
        description=(
            "Deactivate an active maintenance window using its session_id, "
            "or deactivate all active windows if omitted."
        ),
    )
    async def disable_maintenance(
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """End one or all active maintenance sessions."""
        def _disable(conn):
            return disable_maintenance_sessions(conn, session_id=session_id)

        terminated, remaining = await run_db_query(_disable)
        return {
            "status": "ok",
            "terminated_count": terminated,
            "remaining_active_count": remaining,
        }

    # -------------------------------------------------------------------------
    # MCP Resources
    # -------------------------------------------------------------------------

    @server.resource(
        uri="logshed://system/metrics",
        name="System Metrics",
        mime_type="application/json",
        description="Real-time snapshot of server telemetry, error rates, and maintenance state.",
    )
    async def resource_system_metrics() -> str:
        """Provide current system telemetry as JSON."""
        metrics = await get_system_metrics()
        return json.dumps(metrics, indent=2)

    @server.resource(
        uri="logshed://services",
        name="Service Directory",
        mime_type="application/json",
        description="Available apps and host sources in LogShed.",
    )
    async def resource_services() -> str:
        """Provide current distinct services and host sources as JSON."""
        services = await list_services()
        return json.dumps(services, indent=2)

    @server.resource(
        uri="logshed://alerts/active",
        name="Active Alert State",
        mime_type="application/json",
        description="Current alert trigger state and recent incidents.",
    )
    async def resource_active_alerts() -> str:
        """Provide active alert status and recent firing incidents as JSON."""
        def _fetch(conn):
            status = get_maintenance_status(conn)
            return status

        maint_status = await run_db_query(_fetch)
        recent_alerts = await get_recent_alerts(limit=5)
        payload = {
            "maintenance_active": maint_status.active,
            "maintenance_until": maint_status.until,
            "log_handling": maint_status.log_handling,
            "recent_incidents": recent_alerts,
        }
        return json.dumps(payload, indent=2)

    # -------------------------------------------------------------------------
    # MCP Prompts
    # -------------------------------------------------------------------------

    @server.prompt(
        name="investigate_incident",
        description=(
            "Guides the AI agent through a systematic investigation of recent homelab errors, "
            "retrieving system metrics, querying error logs, and retrieving context for root cause diagnosis."
        ),
    )
    def investigate_incident(
        app_name: Optional[str] = None,
        time_window: str = "1h",
    ) -> str:
        """Investigation prompt template for incident troubleshooting."""
        target_clause = f" for application '{app_name}'" if app_name else ""
        return (
            f"You are investigating a homelab incident{target_clause} over the last {time_window}.\n\n"
            "Please follow these systematic troubleshooting steps:\n"
            "1. Inspect overall health and error rates by calling `get_system_metrics`.\n"
            f"2. Query recent error logs using `query_logs(severity_max=3, apps={json.dumps([app_name] if app_name else None)}, limit=50)`.\n"
            "3. For the most critical or recurring errors identified, inspect surrounding context via `get_log_context(log_id=<id>, lines=15)` to review stack traces.\n"
            "4. Check for related alert trigger records via `get_recent_alerts(limit=5)`.\n"
            "5. Synthesize your findings into a concise root cause analysis with recommended remediation steps."
        )

    @server.prompt(
        name="safe_service_upgrade",
        description=(
            "Standard workflow for external deployment agents: checks service health, enables maintenance "
            "window with error filtering, directs deployment steps, verifies log startup, and closes maintenance."
        ),
    )
    def safe_service_upgrade(
        service_name: str,
        duration_minutes: int = 30,
    ) -> str:
        """Safe service upgrade workflow prompt template."""
        return (
            f"You are executing a safe service upgrade workflow for service '{service_name}'.\n\n"
            "Execute the upgrade in the following sequential order:\n"
            f"1. Check pre-upgrade logs and health for '{service_name}' using `query_logs(apps=['{service_name}'], limit=20)`.\n"
            f"2. Silence alerts and prevent noisy deployment errors by starting a maintenance session:\n"
            f"   `enable_maintenance(duration_minutes={duration_minutes}, target_app='{service_name}', log_handling='drop_errors', reason='Deploying upgrade for {service_name}')`\n"
            "   Save the returned `session_id`.\n"
            f"3. Proceed with deploying or restarting the '{service_name}' container or service.\n"
            f"4. Verify clean startup logs with `query_logs(apps=['{service_name}'], limit=30)` to ensure absence of fatal errors.\n"
            "5. Close the maintenance session immediately after verifying success:\n"
            "   `disable_maintenance(session_id=<session_id>)`.\n"
            f"6. Provide a final summary confirming that '{service_name}' is healthy."
        )

    return server
