"""
Model Context Protocol (MCP) FastAPI routes and SSE transport endpoints.

Supports remote connections from AI assistants and IDE plugins via:
- /mcp and /api/v1/mcp      (Streamable HTTP transport; the token is checked on every request)
- GET  /api/v1/mcp/sse      (legacy Server-Sent Events transport)
- POST /api/v1/mcp/messages (legacy SSE JSON-RPC message transport)

Enforces the enable_mcp runtime toggle and API token authentication. Connecting needs only
the logs:read scope; each tool and resource checks its own scope (see app.mcp.server), using
the verified caller that these routes attach to every request (MCP_ACCESS_SCOPE_KEY).
"""

import asyncio
import datetime
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

import anyio
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Message, Receive, Scope, Send

from app.api.deps import parse_bearer_token, rate_limit_headers, run_db_query, verify_api_token
from app.core.api_tokens import api_token_rate_limiter, has_scope_permission, should_update_last_used
from app.core.config import get_cached_setting
from app.core.utils import parse_iso_to_utc_datetime
from app.mcp.server import MCP_ACCESS_SCOPE_KEY, McpAccess, create_mcp_server

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["Model Context Protocol"])

# Scope needed to connect at all; tools and resources check their own scopes on top of this.
REQUIRED_MCP_SCOPES = ["logs:read"]

_mcp_server: Optional[FastMCP] = None
_sse_transport: Optional[SseServerTransport] = None
_session_manager: Optional[StreamableHTTPSessionManager] = None
_session_manager_task: Optional[asyncio.Task] = None
_session_manager_stop_event: Optional[asyncio.Event] = None
_session_manager_started_event: Optional[asyncio.Event] = None

# How long a legacy SSE session may reuse its token record before re-reading it from SQLite.
# Expiry and the rate limit are checked on every message regardless of the cache.
SSE_TOKEN_RECHECK_SECONDS = 5.0


@dataclass
class _SseSession:
    """A live legacy SSE connection and the API token that opened it."""

    token_id: int
    session_id: Optional[UUID] = None
    cancel_scope: Optional[anyio.CancelScope] = None
    closed: bool = False

    def close(self) -> None:
        """Stop the session's server loop, which ends its event stream."""
        self.closed = True
        if self.cancel_scope is not None:
            self.cancel_scope.cancel()


# Legacy SSE session id -> owning token, filled when the endpoint event is sent and emptied on disconnect.
_sse_sessions: dict[UUID, _SseSession] = {}
# Token id -> (monotonic time cached, token record) for session-only /messages posts.
_sse_token_cache: dict[int, tuple[float, dict[str, Any]]] = {}

QUERY_TOKEN_WARNING_INTERVAL_SECONDS = 600.0
_last_query_token_warning = 0.0

QUERY_TOKEN_DESCRIPTION = (
    "Refused by default; accepted only when MCP_ALLOW_QUERY_TOKEN=true, for clients that cannot send headers. "
    "URLs can end up in proxy logs, so prefer the Authorization: Bearer header."
)

_ENDPOINT_EVENT_RE = re.compile(rb"event:\s*endpoint")
_ENDPOINT_SESSION_RE = re.compile(rb"session_id=([0-9a-fA-F]{32})")


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
    Enforces expiration, required MCP scopes, and rate limiting via the shared verifier.
    """
    return await verify_api_token(raw_token, REQUIRED_MCP_SCOPES)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def query_token_allowed() -> bool:
    """?token= is off by default because URLs (and so the token) are written to reverse proxy access logs."""
    return os.environ.get("MCP_ALLOW_QUERY_TOKEN", "").strip().lower() in ("1", "true", "yes", "on")


def _warn_query_token_used() -> None:
    global _last_query_token_warning
    now = time.monotonic()
    if _last_query_token_warning and (now - _last_query_token_warning) < QUERY_TOKEN_WARNING_INTERVAL_SECONDS:
        return
    _last_query_token_warning = now
    logger.warning(
        "MCP accepted an API token from the URL query string (MCP_ALLOW_QUERY_TOKEN is on); URLs can end up "
        "in proxy logs, so prefer the Authorization header."
    )


def _extract_raw_token(request: Request, query_token: Optional[str]) -> Optional[str]:
    """Token from the Authorization header (Bearer or raw value), else from ?token= when allowed."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.strip():
        bearer_token = parse_bearer_token(auth_header)
        if bearer_token is not None:
            return bearer_token or None
        return auth_header.strip()
    if query_token and query_token.strip():
        if not query_token_allowed():
            raise _unauthorized(
                "API tokens in the URL query string are disabled because they are written to proxy access logs. "
                "Send the token in the Authorization: Bearer header (MCP_ALLOW_QUERY_TOKEN=true accepts "
                "query string tokens again)."
            )
        _warn_query_token_used()
        return query_token.strip()
    return None


def _parse_session_id(session_id: Optional[str]) -> Optional[UUID]:
    if not session_id or not session_id.strip():
        return None
    try:
        return UUID(hex=session_id.strip())
    except ValueError:
        return None


def _bind_sse_session_on_endpoint_event(send: Send, session: _SseSession) -> Send:
    """
    Wrap the SSE response's send so the session id the transport generates is bound to its token.
    The client learns the id only from the first 'endpoint' event, so binding it just before
    that event is written leaves no window in which the session is usable but unbound.
    """

    async def _send(message: Message) -> None:
        if session.session_id is None and message.get("type") == "http.response.body":
            body = message.get("body", b"") or b""
            if _ENDPOINT_EVENT_RE.search(body):
                match = _ENDPOINT_SESSION_RE.search(body)
                if match:
                    session_uuid = UUID(hex=match.group(1).decode("ascii"))
                    session.session_id = session_uuid
                    if not session.closed:
                        _sse_sessions[session_uuid] = session
        await send(message)

    return _send


def _forget_sse_session(session: _SseSession) -> None:
    if session.session_id is not None and _sse_sessions.get(session.session_id) is session:
        _sse_sessions.pop(session.session_id, None)
    if not any(s.token_id == session.token_id for s in _sse_sessions.values()):
        _sse_token_cache.pop(session.token_id, None)


def _close_token_sse_sessions(token_id: int) -> int:
    """Unbind and close every live legacy SSE session opened with the given token."""
    closed = 0
    for session_uuid, session in list(_sse_sessions.items()):
        if session.token_id == token_id:
            _sse_sessions.pop(session_uuid, None)
            session.close()
            closed += 1
    _sse_token_cache.pop(token_id, None)
    return closed


def on_token_revoked(token_id: int) -> None:
    """Called when an API token is deleted: its live SSE streams are closed and its cached record dropped."""
    closed = _close_token_sse_sessions(token_id)
    if closed:
        logger.info("Closed %d MCP SSE session(s) for revoked API token %d", closed, token_id)


async def _load_token_record(token_id: int) -> Optional[dict[str, Any]]:
    now = time.monotonic()
    cached = _sse_token_cache.get(token_id)
    if cached is not None and (now - cached[0]) < SSE_TOKEN_RECHECK_SECONDS:
        return cached[1]

    def _lookup(conn):
        return conn.execute(
            "SELECT id, name, scopes, expires_at FROM api_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()

    row = await run_db_query(_lookup)
    if row is None:
        _sse_token_cache.pop(token_id, None)
        return None
    try:
        scopes = json.loads(row["scopes"]) if isinstance(row["scopes"], str) else row["scopes"]
    except (TypeError, ValueError):
        scopes = []
    record = {
        "id": int(row["id"]),
        "name": row["name"],
        "scopes": scopes if isinstance(scopes, list) else [],
        "expires_at": row["expires_at"],
        "auth_type": "api_token",
    }
    _sse_token_cache[token_id] = (now, record)
    return record


async def _touch_last_used(token_id: int) -> None:
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _update(conn):
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now_iso, token_id))
        conn.commit()

    try:
        await run_db_query(_update)
    except Exception as exc:
        logger.warning("API token last_used_at update failed: %s", exc)


async def _reverify_sse_session_token(session: _SseSession) -> dict[str, Any]:
    """
    Re-check the token that opened a legacy SSE session for a message posted without a token.
    Same order as verify_api_token: existence -> expiry -> scope -> rate limit -> last_used_at.
    A revoked, expired or no longer sufficient token also closes the session's stream.
    """
    token_id = session.token_id
    record = await _load_token_record(token_id)
    if record is None:
        _close_token_sse_sessions(token_id)
        raise _unauthorized("API token has been revoked.")

    expires_at = parse_iso_to_utc_datetime(record["expires_at"]) if record["expires_at"] else None
    if expires_at is not None and expires_at <= datetime.datetime.now(datetime.timezone.utc):
        _close_token_sse_sessions(token_id)
        raise _unauthorized("API token has expired.")

    if not has_scope_permission(record["scopes"], REQUIRED_MCP_SCOPES):
        _close_token_sse_sessions(token_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient token scope. Required: {', '.join(REQUIRED_MCP_SCOPES)}",
        )

    rate_status = api_token_rate_limiter.check_request(f"token_{token_id}")
    if not rate_status.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"API token rate limit exceeded (maximum {api_token_rate_limiter.max_requests} "
                "requests per minute)."
            ),
            headers={**rate_limit_headers(rate_status), "Retry-After": str(rate_status.reset_seconds)},
        )

    if should_update_last_used(token_id):
        await _touch_last_used(token_id)

    return dict(record)


async def _dispatch_streamable_http(request: Request, token: Optional[str] = None) -> Response:
    """Validate token and dispatch request to the Streamable HTTP session manager."""
    # 1. Verify enable_mcp setting
    if not get_cached_setting("enable_mcp", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Model Context Protocol is disabled in system settings",
        )

    # 2. Extract and validate Bearer token
    raw_token = _extract_raw_token(request, token)
    if not raw_token:
        raise _unauthorized("Authorization Bearer token required.")

    token_info = await validate_mcp_token(raw_token)

    # 3. Ensure Accept header includes application/json and text/event-stream for streaming compatibility
    scope = dict(request.scope)
    # Tools read the caller of each message from the request that carries it (see app.mcp.server)
    scope[MCP_ACCESS_SCOPE_KEY] = McpAccess.from_token(token_info)
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
    token: Optional[str] = Query(None, description=QUERY_TOKEN_DESCRIPTION),
):
    """Handle Streamable HTTP transport requests on the root MCP endpoint."""
    return await _dispatch_streamable_http(request, token)


# Streamable HTTP routes on /mcp/sse (POST and DELETE)
@router.post("/sse")
@router.delete("/sse")
async def handle_mcp_streamable_http_sse(
    request: Request,
    token: Optional[str] = Query(None, description=QUERY_TOKEN_DESCRIPTION),
):
    """Handle Streamable HTTP requests directed at the SSE sub-path."""
    return await _dispatch_streamable_http(request, token)


@router.get("/sse")
async def handle_mcp_sse(
    request: Request,
    token: Optional[str] = Query(None, description=QUERY_TOKEN_DESCRIPTION),
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
    raw_token = _extract_raw_token(request, token)
    if not raw_token:
        raise _unauthorized("Authorization Bearer token required.")

    token_info = await validate_mcp_token(raw_token)

    # 3. If mcp-session-id header is present, dispatch to Streamable HTTP standalone GET stream
    if "mcp-session-id" in request.headers:
        request.scope[MCP_ACCESS_SCOPE_KEY] = McpAccess.from_token(token_info)
        sm = await ensure_session_manager_started()
        await sm.handle_request(request.scope, request.receive, request._send)
        return AlreadySentResponse()

    # 4. Otherwise connect legacy FastMCP SSE stream, bound to the token that opened it
    server = get_mcp_server()
    sse = get_sse_transport()
    session = _SseSession(token_id=int(token_info["id"]))
    send = _bind_sse_session_on_endpoint_event(request._send, session)

    try:
        async with sse.connect_sse(request.scope, request.receive, send) as streams:
            with anyio.CancelScope() as cancel_scope:
                session.cancel_scope = cancel_scope
                if session.closed:
                    cancel_scope.cancel()
                await server._mcp_server.run(
                    streams[0],
                    streams[1],
                    server._mcp_server.create_initialization_options(),
                )
    finally:
        _forget_sse_session(session)
    return AlreadySentResponse()


@router.post("/messages")
@router.post("/messages/")
async def handle_mcp_messages(
    request: Request,
    token: Optional[str] = Query(None, description=QUERY_TOKEN_DESCRIPTION),
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
    raw_token = _extract_raw_token(request, token)

    session_uuid = _parse_session_id(session_id)
    session = _sse_sessions.get(session_uuid) if session_uuid is not None else None

    if raw_token:
        # A presented token must be valid and must be the one that opened the session
        token_info = await validate_mcp_token(raw_token)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Could not find session")
        if int(token_info["id"]) != session.token_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This API token did not open the MCP session.",
            )
    else:
        # The session id alone is accepted only while the token that opened it is still valid
        if session is None:
            raise _unauthorized("Authorization Bearer token required.")
        token_info = await _reverify_sse_session_token(session)

    # 3. Deliver message to SSE session writer. The server loop runs in the GET request's task, but the
    # library passes this POST request along with the message, so tools see the freshly verified token.
    request.scope[MCP_ACCESS_SCOPE_KEY] = McpAccess.from_token(token_info)
    await sse.handle_post_message(request.scope, request.receive, request._send)
    return AlreadySentResponse()
