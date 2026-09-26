"""
Authentication API endpoints for LogShed.
Provides setup lockout, Argon2id verification, rate-limited login, and session cookies.
"""

import asyncio
import datetime
import ipaddress
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import (
    get_current_user,
    get_optional_user,
    invalidate_admin_auth_cache,
    run_db_query,
)
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    hash_password,
    verify_password,
)
from app.models import (
    AuthStatusResponse,
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    SetupRequest,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _clean_ip(ip_str: str) -> str:
    """Clean whitespace and optional port numbers from an IP string."""
    ip_str = ip_str.strip()
    if ip_str.startswith("["):
        end_bracket = ip_str.find("]")
        if end_bracket != -1:
            return ip_str[1:end_bracket]
    if ip_str.count(":") == 1 and "." in ip_str:
        return ip_str.split(":")[0]
    return ip_str


def _get_trusted_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """
    Parse comma-separated IPs/CIDRs from trusted_proxies setting or TRUSTED_PROXIES environment variable.
    If trusted_proxies is unset or empty, supports trust_docker_proxies (or TRUST_DOCKER_PROXIES / TRUST_DOCKER_NETWORKS)
    to trust standard Docker bridge subnets (172.16.0.0/12).
    """
    from app.core.config import get_cached_setting

    trusted_proxies = str(get_cached_setting("trusted_proxies", "")).strip()
    if trusted_proxies:
        networks = []
        for part in trusted_proxies.split(","):
            cleaned = _clean_ip(part)
            if cleaned:
                try:
                    networks.append(ipaddress.ip_network(cleaned, strict=False))
                except ValueError:
                    pass
        if networks:
            return networks

    # If trusted_proxies is unset or empty, check trust_docker_proxies (and legacy aliases)
    trust_docker = bool(get_cached_setting("trust_docker_proxies", False))
    if trust_docker:
        return [ipaddress.ip_network("172.16.0.0/12", strict=False)]

    return []



def _is_trusted_proxy(ip_str: str, trusted_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    """Check if an IP string is loopback or belongs to one of the trusted proxy networks."""
    cleaned = _clean_ip(ip_str)
    try:
        addr = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    for net in trusted_networks:
        if addr in net:
            return True
    return False


def _get_client_ip(request: Request) -> str:
    """
    Extract client IP address safely with trusted proxy validation.
    Inspects request.client.host. If TRUSTED_PROXIES is configured or the connection
    origin is loopback, parses the client IP from the rightmost untrusted hop
    in X-Forwarded-For. If TRUSTED_PROXIES is not set and the caller is not on loopback,
    falls back strictly to request.client.host.
    """
    if not request.client or not request.client.host:
        return "127.0.0.1"

    peer_ip = _clean_ip(request.client.host)
    trusted_networks = _get_trusted_networks()

    # If the direct peer is not trusted, ignore X-Forwarded-For to prevent spoofing
    if not _is_trusted_proxy(peer_ip, trusted_networks):
        return peer_ip

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer_ip

    hops = [_clean_ip(h) for h in forwarded.split(",") if _clean_ip(h)]
    if not hops:
        return peer_ip

    # Walk hops right-to-left looking for the rightmost untrusted hop
    for hop in reversed(hops):
        if not _is_trusted_proxy(hop, trusted_networks):
            return hop

    # If all hops are trusted, fall back to the leftmost hop
    return hops[0]


def _is_secure_cookie(request: Request) -> bool:
    """
    Determine whether to set the 'secure' flag on session cookies.
    Inspects effective cached setting ('cookie_secure') first.
    If cookie_secure is forced True, returns True.
    If COOKIE_SECURE environment variable is explicitly set ('true'/'false'), respects it.
    Otherwise auto-detects HTTPS request scheme or X-Forwarded-Proto header.
    """
    from app.core.config import get_cached_setting

    cookie_secure = get_cached_setting("cookie_secure", False)
    if cookie_secure is True:
        return True

    cookie_secure_env = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if cookie_secure_env in ("true", "1", "yes"):
        return True
    if cookie_secure_env in ("false", "0", "no"):
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


@router.post("/setup", response_model=MessageResponse)
async def setup_admin(req: SetupRequest, request: Request, response: Response) -> MessageResponse:
    """
    First-run setup: creates admin user and password.
    Returns 403 Forbidden once admin_auth is populated.
    """
    def _check_and_insert(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM admin_auth")
        count = cursor.fetchone()[0]
        if count > 0:
            return False

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pwd_hash = hash_password(req.password)
        cursor.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, ?, ?)",
            (pwd_hash, now, now),
        )
        conn.commit()
        return True

    success = await run_db_query(_check_and_insert)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account has already been set up.",
        )

    invalidate_admin_auth_cache()

    # Issue signed session cookie
    token = create_session_token(user_id=1)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(request),
        path="/",
        max_age=7 * 24 * 3600,
    )
    return MessageResponse(status="ok")


_DUMMY_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$gu2N9vYOMcHlRxyHZpufkw$qwwgz3w/Hzgd+c8DfOpw9Pe/YQjiKQ6G8SiO917I3M8"


@router.post("/login", response_model=MessageResponse)
async def login(req: LoginRequest, request: Request, response: Response) -> MessageResponse:
    """
    Session login endpoint.
    Enforces strict in-memory rate limiting on failed attempts per IP.
    """
    client_ip = _get_client_ip(request)

    # Check brute-force rate limit
    if login_rate_limiter.is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again in a minute.",
        )

    def _get_admin(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM admin_auth WHERE id = 1")
        row = cursor.fetchone()
        return row[0] if row else None

    stored_hash = await run_db_query(_get_admin)
    target_hash = stored_hash if stored_hash else _DUMMY_PASSWORD_HASH
    is_valid = await asyncio.to_thread(verify_password, target_hash, req.password) and bool(stored_hash)

    if not is_valid:
        login_rate_limiter.record_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password.",
        )

    # Reset failed attempts on success
    login_rate_limiter.record_success(client_ip)

    token = create_session_token(user_id=1)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(request),
        path="/",
        max_age=7 * 24 * 3600,
    )
    return MessageResponse(status="ok")


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response) -> MessageResponse:
    """Clears the session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(request),
    )
    return MessageResponse(status="ok")


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request) -> AuthStatusResponse:
    """Returns application setup status and whether current session is authenticated."""
    def _is_setup(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM admin_auth")
        return cursor.fetchone()[0] > 0

    is_setup = await run_db_query(_is_setup)
    user = await get_optional_user(request)

    return AuthStatusResponse(
        setup_required=not is_setup,
        authenticated=user is not None,
    )


@router.post("/password", response_model=MessageResponse)
async def change_password(
    req: PasswordChangeRequest,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
) -> MessageResponse:
    """Change the admin password for an authenticated session."""
    def _verify_and_update(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM admin_auth WHERE id = 1")
        row = cursor.fetchone()
        if not row or not verify_password(row[0], req.current_password):
            return False
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_hash = hash_password(req.new_password)
        cursor.execute(
            "UPDATE admin_auth SET password_hash = ?, updated_at = ? WHERE id = 1",
            (new_hash, now),
        )
        conn.commit()
        return True

    success = await run_db_query(_verify_and_update)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    invalidate_admin_auth_cache()

    # Clear session cookie so client does not retain a revoked token
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie(request),
    )
    return MessageResponse(status="ok")
