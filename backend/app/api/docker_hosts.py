"""
Docker endpoints management API for multi-host Docker collection.
Provides CRUD operations, live connection health metrics, and ad-hoc connection testing.
"""

import asyncio
import datetime
import logging
import os
import socket
import sqlite3
import ssl
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.api.aliases import keyset_alias_backfill
from app.api.deps import get_current_user, run_db_query
from app.collectors.docker_collector import (
    _build_client,
    _parse_docker_host,
    create_docker_ssl_context,
    effective_source_ip,
)
from app.collectors.docker_pool import TLS_HOSTNAME_HINT, is_tls_hostname_mismatch
from app.core.config import invalidate_settings_cache
from app.core.net_guard import is_blocked_docker_address, parse_ip
from app.core.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/docker/hosts", tags=["Docker Hosts"])

# Request field size limits
PEM_MAX_LENGTH = 16384
URL_MAX_LENGTH = 512
TEXT_MAX_LENGTH = 512


_BLOCKED_TARGET_MESSAGE = "Docker URL points to a link-local or cloud metadata address, which is not allowed."
_SOCKET_NOT_ALLOWED_MESSAGE = (
    "Unix socket path must end in docker.sock or be listed in the DOCKER_ALLOWED_SOCKETS environment variable."
)
_RESOLVE_TIMEOUT_SECONDS = 3.0


def _allowed_socket_paths() -> set[str]:
    paths = {
        os.path.normpath(p.strip())
        for p in os.environ.get("DOCKER_ALLOWED_SOCKETS", "").split(",")
        if p.strip()
    }
    docker_host = os.environ.get("DOCKER_HOST", "").strip()
    if docker_host.startswith("unix://") and docker_host[len("unix://"):].strip():
        paths.add(os.path.normpath(docker_host[len("unix://"):].strip()))
    return paths


def _is_allowed_socket_path(path: str) -> bool:
    return path.endswith("docker.sock") or os.path.normpath(path) in _allowed_socket_paths()


def _validate_docker_url_str(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("Docker URL cannot be empty.")
    if v.startswith("unix://"):
        sock_path = v[len("unix://"):].strip()
        if not sock_path:
            raise ValueError("Unix socket path cannot be empty.")
        if not _is_allowed_socket_path(sock_path):
            raise ValueError(_SOCKET_NOT_ALLOWED_MESSAGE)
        return v
    if v.startswith(("tcp://", "http://", "https://")):
        parsed = urlparse(v)
        if not parsed.hostname:
            raise ValueError("Network Docker URL must specify a valid host.")
        if is_blocked_docker_address(parsed.hostname):
            raise ValueError(_BLOCKED_TARGET_MESSAGE)
        return v
    raise ValueError("Docker URL scheme must be unix://, tcp://, http://, or https://.")


async def _resolve_host_addresses(host: str) -> list[str]:
    """Resolve a host name off the event loop; an unresolvable name yields no addresses."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=_RESOLVE_TIMEOUT_SECONDS,
        )
    except (OSError, UnicodeError, asyncio.TimeoutError):
        return []
    return [str(info[4][0]) for info in infos]


async def _ensure_url_target_allowed(url: Optional[str]) -> None:
    """Reject network URLs whose host name resolves to a link-local or metadata address."""
    if not url or not url.startswith(("tcp://", "http://", "https://")):
        return
    host = urlparse(url).hostname
    if not host or parse_ip(host) is not None:
        # IP literals are already checked by _validate_docker_url_str
        return
    addresses = await _resolve_host_addresses(host)
    if any(is_blocked_docker_address(addr) for addr in addresses):
        raise HTTPException(status_code=422, detail=_BLOCKED_TARGET_MESSAGE)


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    ip = parse_ip(host)
    return ip is not None and ip.is_loopback


def _require_ca_for_remote_tls(url: str, tls_enabled: bool, ca_cert: Optional[str]) -> None:
    """TLS to a non-loopback network host must pin a CA certificate (422 otherwise)."""
    if not tls_enabled or not url.startswith(("tcp://", "http://", "https://")):
        return
    if ca_cert and ca_cert.strip():
        return
    host = urlparse(url).hostname or ""
    if _is_loopback_host(host):
        return
    raise HTTPException(
        status_code=422,
        detail="A CA certificate is required when TLS is on for a Docker host that is not on this machine.",
    )


def _iter_exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _describe_connection_error(exc: BaseException) -> str:
    """Map a connection test failure to generic text that does not echo the probed service."""
    chain = list(_iter_exception_chain(exc))
    text = " ".join(str(e) for e in chain)
    if any(isinstance(e, ssl.SSLError) for e in chain) or "SSL" in text or "CERTIFICATE_VERIFY_FAILED" in text:
        if is_tls_hostname_mismatch(text):
            return f"Connection test failed: TLS error. {TLS_HOSTNAME_HINT}"
        return "Connection test failed: TLS error."
    if any(isinstance(e, (httpx.TimeoutException, TimeoutError)) for e in chain):
        return "Connection test failed: timeout."
    if any(isinstance(e, (httpx.ConnectError, ConnectionError, FileNotFoundError)) for e in chain):
        return "Connection test failed: connection refused."
    return "Connection test failed: not a Docker API."


class DockerEndpointResponse(BaseModel):
    id: int
    name: str
    url: str
    source_alias: str
    enabled: bool
    tls_enabled: bool
    ca_cert: Optional[str] = None
    client_cert: Optional[str] = None
    has_client_key: bool = False
    verify_hostname: bool = True
    exclude_containers: str = ""
    created_at: str
    updated_at: str
    is_connected: bool = False
    active_containers: int = 0
    last_error: Optional[str] = None
    last_activity: Optional[str] = None
    latency_ms: Optional[float] = None


class DockerEndpointCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1, max_length=URL_MAX_LENGTH)
    source_alias: str = Field(default="docker", max_length=64)
    enabled: bool = True
    tls_enabled: bool = False
    verify_hostname: bool = True
    ca_cert: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    client_cert: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    client_key: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    exclude_containers: Optional[str] = Field("", max_length=TEXT_MAX_LENGTH)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        res = _validate_docker_url_str(v)
        assert res is not None
        return res


class DockerEndpointUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    url: Optional[str] = Field(None, max_length=URL_MAX_LENGTH)
    source_alias: Optional[str] = Field(None, max_length=64)
    enabled: Optional[bool] = None
    tls_enabled: Optional[bool] = None
    verify_hostname: Optional[bool] = None
    ca_cert: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    client_cert: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    client_key: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    exclude_containers: Optional[str] = Field(None, max_length=TEXT_MAX_LENGTH)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_docker_url_str(v)


class DockerEndpointTestRequest(BaseModel):
    endpoint_id: Optional[int] = None
    url: str = Field(..., max_length=URL_MAX_LENGTH)
    tls_enabled: bool = False
    verify_hostname: bool = True
    ca_cert: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    client_cert: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    client_key: Optional[str] = Field(None, max_length=PEM_MAX_LENGTH)
    # When false, never fall back to the certificates and key stored for endpoint_id
    use_stored_secrets: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        res = _validate_docker_url_str(v)
        assert res is not None
        return res


class DockerEndpointTestResponse(BaseModel):
    success: bool
    version: Optional[str] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None


@router.get("", response_model=list[DockerEndpointResponse])
async def list_docker_endpoints(
    user: dict = Depends(get_current_user),
) -> list[DockerEndpointResponse]:
    """List all configured Docker endpoints with live connection status and metrics."""
    from app.main import get_docker_pool
    pool = get_docker_pool()

    if pool:
        statuses = await pool.get_statuses()
        return [DockerEndpointResponse(**s) for s in statuses]

    def _query(conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, url, source_alias, enabled, tls_enabled,
                   ca_cert, client_cert, client_key, exclude_containers, verify_hostname,
                   created_at, updated_at
            FROM docker_endpoints
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "name": r["name"],
                "url": r["url"],
                "source_alias": r["source_alias"],
                "enabled": bool(r["enabled"]),
                "tls_enabled": bool(r["tls_enabled"]),
                "ca_cert": r["ca_cert"],
                "client_cert": r["client_cert"],
                "has_client_key": bool(r["client_key"] and str(r["client_key"]).strip()),
                "verify_hostname": bool(r["verify_hostname"]),
                "exclude_containers": r["exclude_containers"] or "",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "is_connected": False,
                "active_containers": 0,
                "last_error": None,
                "last_activity": None,
                "latency_ms": None,
            })
        return results

    rows = await run_db_query(_query)
    return [DockerEndpointResponse(**r) for r in rows]


@router.post("", response_model=DockerEndpointResponse)
async def create_docker_endpoint(
    payload: DockerEndpointCreateRequest,
    user: dict = Depends(get_current_user),
) -> DockerEndpointResponse:
    """Create a new Docker endpoint and spawn its worker task if enabled."""
    await _ensure_url_target_allowed(payload.url)
    _require_ca_for_remote_tls(payload.url, payload.tls_enabled, payload.ca_cert)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    enc_key = None
    if payload.client_key and payload.client_key.strip():
        enc_key = encrypt_value(payload.client_key.strip())

    def _insert(conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute("SELECT id FROM docker_endpoints WHERE name = ?", (payload.name.strip(),))
        if cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A Docker endpoint named '{payload.name.strip()}' already exists.",
            )

        cur.execute(
            """
            INSERT INTO docker_endpoints (
                name, url, source_alias, enabled, tls_enabled, verify_hostname,
                ca_cert, client_cert, client_key, exclude_containers,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name.strip(),
                payload.url.strip(),
                payload.source_alias.strip() or "docker",
                1 if payload.enabled else 0,
                1 if payload.tls_enabled else 0,
                1 if payload.verify_hostname else 0,
                payload.ca_cert.strip() if payload.ca_cert else None,
                payload.client_cert.strip() if payload.client_cert else None,
                enc_key,
                payload.exclude_containers.strip() if payload.exclude_containers else "",
                now_iso,
                now_iso,
            ),
        )
        new_id = cur.lastrowid
        conn.commit()
        return new_id

    new_id = await run_db_query(_insert)

    ep_data = {
        "id": new_id,
        "name": payload.name.strip(),
        "url": payload.url.strip(),
        "source_alias": payload.source_alias.strip() or "docker",
        "enabled": payload.enabled,
        "tls_enabled": payload.tls_enabled,
        "verify_hostname": payload.verify_hostname,
        "ca_cert": payload.ca_cert.strip() if payload.ca_cert else None,
        "client_cert": payload.client_cert.strip() if payload.client_cert else None,
        "client_key": enc_key,
        "exclude_containers": payload.exclude_containers.strip() if payload.exclude_containers else "",
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    from app.main import get_docker_pool
    pool = get_docker_pool()
    if pool:
        await pool.add_endpoint(ep_data)

    return DockerEndpointResponse(
        id=new_id,
        name=payload.name.strip(),
        url=payload.url.strip(),
        source_alias=payload.source_alias.strip() or "docker",
        enabled=payload.enabled,
        tls_enabled=payload.tls_enabled,
        ca_cert=payload.ca_cert,
        client_cert=payload.client_cert,
        has_client_key=bool(enc_key),
        verify_hostname=payload.verify_hostname,
        exclude_containers=payload.exclude_containers or "",
        created_at=now_iso,
        updated_at=now_iso,
        is_connected=False,
        active_containers=0,
    )


def _batch_update_docker_aliases(
    conn: sqlite3.Connection,
    source_ip: str,
    target_alias: str,
    old_alias: Optional[str] = None,
    batch_size: int = 1000,
) -> None:
    """
    Retroactively update source_alias for existing logs from a Docker endpoint in chunked batches.
    Prevents long table locks on large datasets while interleaving concurrent writes.
    """
    if source_ip != "docker":
        if not old_alias or old_alias == target_alias:
            return
        keyset_alias_backfill(conn, source_ip, "+source_alias = ?", (old_alias,), target_alias, batch_size)
    else:
        keyset_alias_backfill(
            conn,
            "docker",
            "+source_alias != ? OR +source_alias IS NULL",
            (target_alias,),
            target_alias,
            batch_size,
        )


@router.put("/{endpoint_id}", response_model=DockerEndpointResponse)
async def update_docker_endpoint(
    endpoint_id: int,
    payload: DockerEndpointUpdateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
) -> DockerEndpointResponse:
    """Update configuration for a Docker endpoint and reload its worker."""
    await _ensure_url_target_allowed(payload.url)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    old_alias_box: list[Optional[str]] = [None]
    effective_ip_box: list[str] = ["docker"]

    def _update(conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute("SELECT * FROM docker_endpoints WHERE id = ?", (endpoint_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Docker endpoint not found.",
            )

        name_val = row["name"]
        if payload.name is not None and payload.name.strip():
            if endpoint_id == 1 and payload.name.strip() != "Primary Docker":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot rename the primary Docker endpoint.",
                )
            name_val = payload.name.strip()
            if name_val != row["name"]:
                cur.execute(
                    "SELECT id FROM docker_endpoints WHERE name = ? AND id != ?",
                    (name_val, endpoint_id),
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"A Docker endpoint named '{name_val}' already exists.",
                    )

        url_val = payload.url.strip() if payload.url is not None else row["url"]
        alias_val = payload.source_alias.strip() if payload.source_alias is not None else row["source_alias"]
        enabled_val = (1 if payload.enabled else 0) if payload.enabled is not None else row["enabled"]
        tls_val = (1 if payload.tls_enabled else 0) if payload.tls_enabled is not None else row["tls_enabled"]
        # None keeps the stored certificate; an empty string clears it (as for client_key)
        ca_val = (payload.ca_cert.strip() or None) if payload.ca_cert is not None else row["ca_cert"]
        cert_val = (payload.client_cert.strip() or None) if payload.client_cert is not None else row["client_cert"]
        excl_val = payload.exclude_containers.strip() if payload.exclude_containers is not None else row["exclude_containers"]
        verify_val = (1 if payload.verify_hostname else 0) if payload.verify_hostname is not None else row["verify_hostname"]
        # Checked when the request touches TLS, the URL or the CA, so a plain enable toggle still works
        if payload.tls_enabled is not None or payload.url is not None or payload.ca_cert is not None:
            _require_ca_for_remote_tls(url_val, bool(tls_val), ca_val)

        key_val = row["client_key"]
        if payload.client_key is not None:
            if payload.client_key.strip():
                key_val = encrypt_value(payload.client_key.strip())
            else:
                key_val = None

        old_alias_box[0] = row["source_alias"]
        # Historical rows carry the source_ip derived from the endpoint's previous URL and alias
        effective_ip_box[0] = effective_source_ip(row["url"], row["source_alias"], endpoint_id)

        cur.execute(
            """
            UPDATE docker_endpoints SET
                name = ?,
                url = ?,
                source_alias = ?,
                enabled = ?,
                tls_enabled = ?,
                verify_hostname = ?,
                ca_cert = ?,
                client_cert = ?,
                client_key = ?,
                exclude_containers = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name_val,
                url_val,
                alias_val,
                enabled_val,
                tls_val,
                verify_val,
                ca_val,
                cert_val,
                key_val,
                excl_val,
                now_iso,
                endpoint_id,
            ),
        )

        # Synchronize primary endpoint updates back to system_settings
        if endpoint_id == 1:
            cur.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES ('enable_docker', ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(enabled_val), now_iso),
            )
            cur.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES ('docker_source_alias', ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (alias_val, now_iso),
            )
            cur.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES ('docker_exclude_containers', ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (excl_val, now_iso),
            )

        conn.commit()

        return {
            "id": endpoint_id,
            "name": name_val,
            "url": url_val,
            "source_alias": alias_val,
            "enabled": bool(enabled_val),
            "tls_enabled": bool(tls_val),
            "verify_hostname": bool(verify_val),
            "ca_cert": ca_val,
            "client_cert": cert_val,
            "client_key": key_val,
            "has_client_key": bool(key_val and str(key_val).strip()),
            "exclude_containers": excl_val,
            "created_at": row["created_at"],
            "updated_at": now_iso,
        }

    updated = await run_db_query(_update)
    if endpoint_id == 1:
        invalidate_settings_cache()

    from app.main import get_docker_pool
    pool = get_docker_pool()
    if pool:
        # Host 1 enabled is the pool master switch (enable_docker), as in POST /api/settings
        if endpoint_id == 1 and payload.enabled is not None:
            await pool.set_pool_enabled(bool(updated["enabled"]))
        await pool.reload_endpoint(endpoint_id, updated)

    # If source_alias was modified, retroactively update matching historical logs
    prev_alias = old_alias_box[0]
    new_alias = updated["source_alias"]
    target_ip = effective_ip_box[0]
    if prev_alias and new_alias and prev_alias != new_alias:
        def _run_docker_batch(conn: sqlite3.Connection):
            try:
                _batch_update_docker_aliases(
                    conn,
                    source_ip=target_ip,
                    target_alias=new_alias,
                    old_alias=prev_alias,
                    batch_size=1000,
                )
            except Exception as exc:
                logger.error(
                    f"Background retroactive Docker alias update failed for {target_ip} ({prev_alias} -> {new_alias}): {exc}"
                )

        background_tasks.add_task(run_db_query, _run_docker_batch)

    return DockerEndpointResponse(**updated)


@router.delete("/{endpoint_id}")
async def delete_docker_endpoint(
    endpoint_id: int,
    user: dict = Depends(get_current_user),
) -> dict[str, str]:
    """Delete a Docker endpoint and stop its worker (primary socket ID 1 protected)."""
    if endpoint_id == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the primary Docker endpoint.",
        )

    def _delete(conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute("SELECT id FROM docker_endpoints WHERE id = ?", (endpoint_id,))
        if not cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Docker endpoint not found.",
            )
        cur.execute("DELETE FROM docker_endpoints WHERE id = ?", (endpoint_id,))
        conn.commit()

    await run_db_query(_delete)

    from app.main import get_docker_pool
    pool = get_docker_pool()
    if pool:
        await pool.remove_endpoint(endpoint_id)

    return {"status": "ok", "message": f"Docker endpoint {endpoint_id} deleted."}


@router.post("/test", response_model=DockerEndpointTestResponse)
async def test_docker_connection(
    payload: DockerEndpointTestRequest,
    user: dict = Depends(get_current_user),
) -> DockerEndpointTestResponse:
    """Test connectivity to a remote or local Docker daemon without saving."""
    await _ensure_url_target_allowed(payload.url)
    ca_cert = payload.ca_cert
    client_cert = payload.client_cert
    client_key = payload.client_key

    if payload.endpoint_id and payload.tls_enabled and payload.use_stored_secrets:
        def _get_stored_tls(conn: sqlite3.Connection):
            cur = conn.cursor()
            cur.execute(
                "SELECT url, ca_cert, client_cert, client_key FROM docker_endpoints WHERE id = ?",
                (payload.endpoint_id,),
            )
            return cur.fetchone()

        db_row = await run_db_query(_get_stored_tls)
        # Stored credentials only ever go to the URL they were saved for
        if db_row and str(db_row["url"] or "").strip() == payload.url.strip():
            if not (ca_cert and ca_cert.strip()):
                ca_cert = db_row["ca_cert"]
            if not (client_cert and client_cert.strip()):
                client_cert = db_row["client_cert"]
            stored_enc_key = db_row["client_key"]
            if not (client_key and client_key.strip()) and stored_enc_key and str(stored_enc_key).strip():
                try:
                    client_key = decrypt_value(stored_enc_key.strip())
                except Exception as e:
                    logger.warning("Failed to decrypt stored client key for endpoint %s: %s", payload.endpoint_id, e)
        elif db_row:
            logger.info(
                "Docker connection test for endpoint %s targets a different URL; stored TLS credentials not used.",
                payload.endpoint_id,
            )

    _require_ca_for_remote_tls(payload.url, payload.tls_enabled, ca_cert)

    ssl_ctx = None
    if payload.tls_enabled:
        try:
            ssl_ctx = create_docker_ssl_context(
                ca_cert=ca_cert,
                client_cert=client_cert,
                client_key=client_key,
                verify_hostname=payload.verify_hostname,
            )
        except Exception as exc:
            logger.info("Docker connection test TLS setup failed: %r", exc)
            return DockerEndpointTestResponse(
                success=False,
                error="TLS configuration error: check the CA certificate, client certificate and client key.",
            )

    base_url, uds_path = _parse_docker_host(
        enable_docker=True,
        docker_host=payload.url,
        tls_enabled=payload.tls_enabled,
    )
    if not base_url:
        return DockerEndpointTestResponse(
            success=False,
            error="Invalid Docker host URL or socket path.",
        )

    t0 = time.monotonic()
    try:
        async with _build_client(base_url, uds_path, read_timeout=5.0, ssl_context=ssl_ctx) as client:
            ping_resp = await client.get("/_ping", timeout=5.0)
            if ping_resp.status_code != 200:
                logger.info(
                    "Docker connection test to %s: /_ping returned HTTP %s",
                    uds_path or base_url,
                    ping_resp.status_code,
                )
                return DockerEndpointTestResponse(
                    success=False,
                    error="Connection test failed: not a Docker API.",
                )

            latency = round((time.monotonic() - t0) * 1000, 2)
            version_str = "Docker Engine"
            try:
                ver_resp = await client.get("/version", timeout=5.0)
                if ver_resp.status_code == 200:
                    v_data = ver_resp.json()
                    reported = v_data.get("Version") if isinstance(v_data, dict) else None
                    if isinstance(reported, str) and 0 < len(reported) <= 64:
                        version_str = reported
            except Exception:
                pass

            return DockerEndpointTestResponse(
                success=True,
                version=version_str,
                latency_ms=latency,
            )
    except Exception as exc:
        # Keep probe details in the server log only
        logger.info("Docker connection test to %s failed: %r", uds_path or base_url, exc)
        return DockerEndpointTestResponse(
            success=False,
            error=_describe_connection_error(exc),
        )
