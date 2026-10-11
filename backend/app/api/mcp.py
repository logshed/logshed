"""
Model Context Protocol (MCP) FastAPI routes and SSE transport endpoints.

Supports remote connections from AI assistants and IDE plugins via:
- GET  /api/v1/mcp/sse      (Server-Sent Events transport)
- POST /api/v1/mcp/messages (JSON-RPC message transport)

Enforces enable_mcp runtime toggle and API token authentication with
the 'AI Assistant / MCP' scope bundle:
['logs:read', 'alerts:read', 'system:read', 'maintenance:write']
"""

import asyncio
import datetime
import json
import logging
from typing import Any, Optional
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Receive, Scope, Send

from app.api.deps import run_db_query
from app.core.api_tokens import (
    api_token_rate_limiter,
    has_scope_permission,
    hash_token,
    should_update_last_used,
)
from app.core.config import get_cached_setting
from app.core.utils import parse_iso_to_utc_datetime
from app.mcp.server import create_mcp_server

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["Model Context Protocol"])

REQUIRED_MCP_SCOPES = ["logs:read", "alerts:read", "system:read", "maintenance:write"]

_mcp_server: Optional[FastMCP] = None
_sse_transport: Optional[SseServerTransport] = None
_session_manager: Optional[StreamableHTTPSessionManager] = None
_session_manager_task: Optional[asyncio.Task] = None
_session_manager_stop_event: Optional[asyncio.Event] = None
_session_manager_started_event: Optional[asyncio.Event] = None


class AlreadySentResponse(Response):
    """Sentinel response indicating ASGI response was already streamed to the client."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        pass


def get_mcp_server() -> FastMCP:
    """Retrieve or initialize the singleton FastMCP server instance."""
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = create_mcp_server()
    return _mcp_server


def get_sse_transport() -> SseServerTransport:
    """Retrieve or initialize the singleton SSE server transport."""
    global _sse_transport
    if _sse_transport is None:
        _sse_transport = SseServerTransport(
            endpoint="/api/v1/mcp/messages",
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    return _sse_transport


def get_mcp_session_manager() -> StreamableHTTPSessionManager:
    """Retrieve or initialize the singleton StreamableHTTPSessionManager instance."""
    global _session_manager
    if _session_manager is None:
        server = get_mcp_server()
        _session_manager = StreamableHTTPSessionManager(
            app=server._mcp_server,
            event_store=None,
            json_response=False,
            stateless=False,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    return _session_manager


async def _run_session_manager_worker(
    sm: StreamableHTTPSessionManager,
    stop_event: asyncio.Event,
    started_event: asyncio.Event,
) -> None:
    try:
        async with sm.run():
            started_event.set()
            await stop_event.wait()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"StreamableHTTP session manager terminated: {e}")


async def ensure_session_manager_started() -> StreamableHTTPSessionManager:
    """Ensure the StreamableHTTP session manager task group is running and ready."""
    global _session_manager, _session_manager_task, _session_manager_stop_event, _session_manager_started_event
    current_loop = asyncio.get_running_loop()
    if _session_manager_task is not None:
        try:
            task_loop = _session_manager_task.get_loop()
            if task_loop is not current_loop or task_loop.is_closed():
                _session_manager = None
                _session_manager_task = None
                _session_manager_stop_event = None
                _session_manager_started_event = None
        except Exception:
            _session_manager = None
            _session_manager_task = None
            _session_manager_stop_event = None
            _session_manager_started_event = None

    sm = get_mcp_session_manager()
    if sm._task_group is None:
        if _session_manager_task is None or _session_manager_task.done():
            _session_manager_stop_event = asyncio.Event()
            _session_manager_started_event = asyncio.Event()
            _session_manager_task = asyncio.create_task(
                _run_session_manager_worker(sm, _session_manager_stop_event, _session_manager_started_event)
            )
        await _session_manager_started_event.wait()
    return sm


async def stop_mcp_session_manager() -> None:
    """Gracefully stop the StreamableHTTP session manager and cancel background tasks."""
    global _session_manager, _session_manager_task, _session_manager_stop_event, _session_manager_started_event
    if _session_manager_stop_event is not None:
        _session_manager_stop_event.set()
    if _session_manager_task is not None and not _session_manager_task.done():
        try:
            await asyncio.wait_for(_session_manager_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _session_manager_task.cancel()
    _session_manager = None
    _session_manager_task = None
    _session_manager_stop_event = None
    _session_manager_started_event = None


async def validate_mcp_token(raw_token: str) -> dict[str, Any]:
    """
    Validate a raw API token against the SQLite api_tokens table.
    Enforces expiration, rate limiting, and required MCP scopes.
    """
    token_hash_val = hash_token(raw_token)

    def _lookup(conn):
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

    token_id = int(row["id"])
    if not api_token_rate_limiter.record_request(f"token_{token_id}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API token rate limit exceeded (maximum 120 requests per minute).",
        )

    try:
        scopes_list = json.loads(row["scopes"]) if isinstance(row["scopes"], str) else row["scopes"]
    except Exception:
        scopes_list = []

    if not has_scope_permission(scopes_list, REQUIRED_MCP_SCOPES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient token scope. Required: {', '.join(REQUIRED_MCP_SCOPES)}",
        )

    if should_update_last_used(token_id):
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def _update_last_used(conn):
            conn.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                (now_iso, token_id),
            )
            conn.commit()

        asyncio.create_task(run_db_query(_update_last_used))

    return {
        "id": token_id,
        "name": row["name"],
        "token_prefix": row["token_prefix"],
        "scopes": scopes_list,
    }


def extract_raw_token(headers: list[tuple[bytes, bytes]], query_string: bytes) -> Optional[str]:
    """Extract raw token string from Authorization header or ?token= query parameter."""
    for name, value in headers:
        if name.lower() == b"authorization":
            val_str = value.decode("latin1", errors="replace").strip()
            if val_str.startswith("Bearer "):
                return val_str[7:].strip()

    if query_string:
        parsed = parse_qs(query_string.decode("latin1", errors="replace"))
        token_vals = parsed.get("token")
        if token_vals and token_vals[0].strip():
            return token_vals[0].strip()

    return None


async def _dispatch_streamable_http(request: Request, token: Optional[str] = None) -> Response:
    """Validate token and dispatch request to the Streamable HTTP session manager."""
    # 1. Verify enable_mcp setting
    if not get_cached_setting("enable_mcp", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model Context Protocol is disabled in system settings",
        )

    # 2. Extract and validate Bearer token
    auth_header = request.headers.get("Authorization")
    raw_token = None
    if auth_header:
        auth_header_clean = auth_header.strip()
        if auth_header_clean.startswith("Bearer "):
            raw_token = auth_header_clean[7:].strip()
        elif auth_header_clean:
            raw_token = auth_header_clean
    elif token and token.strip():
        raw_token = token.strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await validate_mcp_token(raw_token)

    # 3. Ensure Accept header includes application/json and text/event-stream for streaming compatibility
    scope = dict(request.scope)
    headers = list(scope.get("headers", []))
    accept_val = None
    accept_idx = -1
    for idx, (hname, hval) in enumerate(headers):
        if hname.lower() == b"accept":
            accept_val = hval
            accept_idx = idx
            break

    if accept_val is None or accept_val == b"*/*" or (b"application/json" in accept_val and b"text/event-stream" not in accept_val):
        new_accept = b"application/json, text/event-stream"
        if accept_idx >= 0:
            headers[accept_idx] = (b"accept", new_accept)
        else:
            headers.append((b"accept", new_accept))
        scope["headers"] = headers

    # 4. Dispatch to Streamable HTTP session manager
    sm = await ensure_session_manager_started()
    await sm.handle_request(scope, request.receive, request._send)
    return AlreadySentResponse()


# Streamable HTTP routes on /mcp and /mcp/
@router.post("")
@router.post("/")
@router.get("")
@router.get("/")
@router.delete("")
@router.delete("/")
async def handle_mcp_streamable_http_root(
    request: Request,
    token: Optional[str] = Query(None),
):
    """Handle Streamable HTTP transport requests on the root MCP endpoint."""
    return await _dispatch_streamable_http(request, token)


# Streamable HTTP routes on /mcp/sse (POST and DELETE)
@router.post("/sse")
@router.delete("/sse")
async def handle_mcp_streamable_http_sse(
    request: Request,
    token: Optional[str] = Query(None),
):
    """Handle Streamable HTTP requests directed at the SSE sub-path."""
    return await _dispatch_streamable_http(request, token)


@router.get("/sse")
async def handle_mcp_sse(
    request: Request,
    token: Optional[str] = Query(None),
):
    """
    Handle incoming SSE transport stream for Model Context Protocol.
    Supports both Streamable HTTP standalone GET event streams (mcp-session-id present)
    and legacy SSE transport connections (mcp-session-id absent).
    """
    # 1. Verify enable_mcp setting
    if not get_cached_setting("enable_mcp", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model Context Protocol is disabled in system settings",
        )

    # 2. Extract and validate Bearer token
    auth_header = request.headers.get("Authorization")
    raw_token = None
    if auth_header:
        auth_header_clean = auth_header.strip()
        if auth_header_clean.startswith("Bearer "):
            raw_token = auth_header_clean[7:].strip()
        elif auth_header_clean:
            raw_token = auth_header_clean
    elif token and token.strip():
        raw_token = token.strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await validate_mcp_token(raw_token)

    # 3. If mcp-session-id header is present, dispatch to Streamable HTTP standalone GET stream
    if "mcp-session-id" in request.headers:
        sm = await ensure_session_manager_started()
        await sm.handle_request(request.scope, request.receive, request._send)
        return AlreadySentResponse()

    # 4. Otherwise connect legacy FastMCP SSE stream
    server = get_mcp_server()
    sse = get_sse_transport()

    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server._mcp_server.run(
            streams[0],
            streams[1],
            server._mcp_server.create_initialization_options(),
        )
    return AlreadySentResponse()


@router.post("/messages")
@router.post("/messages/")
async def handle_mcp_messages(
    request: Request,
    token: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
):
    """Handle incoming JSON-RPC client messages linked to active SSE transport sessions."""
    # 1. Verify enable_mcp setting
    if not get_cached_setting("enable_mcp", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model Context Protocol is disabled in system settings",
        )

    sse = get_sse_transport()

    # 2. Check token authentication if presented
    auth_header = request.headers.get("Authorization")
    raw_token = None
    if auth_header:
        auth_header_clean = auth_header.strip()
        if auth_header_clean.startswith("Bearer "):
            raw_token = auth_header_clean[7:].strip()
        elif auth_header_clean:
            raw_token = auth_header_clean
    elif token and token.strip():
        raw_token = token.strip()

    if raw_token:
        await validate_mcp_token(raw_token)
    else:
        is_valid_session = False
        if session_id and session_id.strip():
            try:
                session_uuid = UUID(hex=session_id.strip())
                is_valid_session = session_uuid in sse._read_stream_writers
            except ValueError:
                is_valid_session = False

        if not is_valid_session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization Bearer token required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 3. Deliver message to SSE session writer
    await sse.handle_post_message(request.scope, request.receive, request._send)
    return AlreadySentResponse()
