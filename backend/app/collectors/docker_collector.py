"""
Docker container log collector for LogShed.

Connects via DOCKER_HOST (unix:///var/run/docker.sock or tcp://proxy:2375)
using httpx (with HTTPTransport(uds=...) for Unix sockets).
Monitors Docker lifecycle events (start/die) and streams container stdout/stderr.
Routes parsed logs through the shared KeyedMultilineAssembler into the shared
asyncio.Queue - no separate queue or assembler.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import shutil
import socket
import ssl
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.collectors.syslog import RE_LOGFMT_LEVEL, RE_LOGFMT_MSG, VALKEY_SEVERITY, AliasCache
from app.core.config import env_bool, env_docker_disabled, get_db_path
from app.core.migrations import get_connection
from app.core.net_guard import GuardedAsyncHTTPTransport
from app.core.pipeline import (
    MAX_MESSAGE_BYTES,
    KeyedMultilineAssembler,
    detect_severity,
    clean_log_text,
)
from app.core.utils import SEVERITY_NAME_MAP
from app.services import log_store

logger = logging.getLogger(__name__)

# Docker Engine API version to target
DOCKER_API_VERSION = "v1.43"

# Supervisor backoff parameters
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
_BACKOFF_FACTOR = 2.0

# Container log tailer backoff parameters
_CONTAINER_INITIAL_BACKOFF = 1.0
_CONTAINER_MAX_BACKOFF = 15.0
_CONTAINER_BACKOFF_FACTOR = 2.0

# Internal tuning constants (with optional environment fallbacks preserved for custom builds)
DEFAULT_DOCKER_READ_TIMEOUT: Optional[float] = None
if "DOCKER_READ_TIMEOUT" in os.environ:
    try:
        DEFAULT_DOCKER_READ_TIMEOUT = float(os.environ["DOCKER_READ_TIMEOUT"])
    except ValueError:
        pass

DEFAULT_DOCKER_HEARTBEAT_INTERVAL: float = 30.0
if "DOCKER_HEARTBEAT_INTERVAL" in os.environ:
    try:
        DEFAULT_DOCKER_HEARTBEAT_INTERVAL = float(os.environ["DOCKER_HEARTBEAT_INTERVAL"])
    except ValueError:
        pass

DEFAULT_DOCKER_SOCKET_POLL_INTERVAL: float = 5.0
if "DOCKER_SOCKET_POLL_INTERVAL" in os.environ:
    try:
        DEFAULT_DOCKER_SOCKET_POLL_INTERVAL = float(os.environ["DOCKER_SOCKET_POLL_INTERVAL"])
    except ValueError:
        pass

DEFAULT_DOCKER_SOCKET_POLL_MAX: float = 60.0
if "DOCKER_SOCKET_POLL_MAX" in os.environ:
    try:
        DEFAULT_DOCKER_SOCKET_POLL_MAX = float(os.environ["DOCKER_SOCKET_POLL_MAX"])
    except ValueError:
        pass

DEFAULT_DOCKER_DISCOVERY_INTERVAL: float = 60.0
if "DOCKER_DISCOVERY_INTERVAL" in os.environ:
    try:
        DEFAULT_DOCKER_DISCOVERY_INTERVAL = float(os.environ["DOCKER_DISCOVERY_INTERVAL"])
    except ValueError:
        pass

MAX_TTY_BUFFER = 65536  # 64 KB limit to prevent unbounded memory growth in TTY mode


def create_docker_ssl_context(
    ca_cert: Optional[str] = None,
    client_cert: Optional[str] = None,
    client_key: Optional[str] = None,
    verify_hostname: bool = True,
) -> ssl.SSLContext:
    """
    Create an ephemeral SSLContext configured for Docker mTLS.
    Decrypted client credentials are safely materialized to temporary files
    with restricted 0600 permissions solely during SSLContext generation.

    verify_hostname checks that the daemon certificate names the host in the URL.
    Turning it off still verifies the certificate chain; it exists for daemons
    reached by an IP address their certificate does not list.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = bool(verify_hostname)

    if ca_cert and ca_cert.strip():
        ctx.load_verify_locations(cadata=ca_cert.strip())

    if client_cert and client_cert.strip() and client_key and client_key.strip():
        temp_dir = tempfile.mkdtemp(prefix="logshed_tls_")
        try:
            cert_path = os.path.join(temp_dir, "client.crt")
            key_path = os.path.join(temp_dir, "client.key")

            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd_cert = os.open(cert_path, flags, 0o600)
            with open(fd_cert, "w", encoding="utf-8") as f:
                f.write(client_cert.strip())

            fd_key = os.open(key_path, flags, 0o600)
            with open(fd_key, "w", encoding="utf-8") as f:
                f.write(client_key.strip())

            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return ctx


def _parse_docker_host(
    enable_docker: Optional[bool] = None,
    docker_host: Optional[str] = None,
    tls_enabled: bool = False,
) -> tuple[str, Optional[str]]:
    """
    Parse Docker host URL or DOCKER_HOST env var into (base_url, uds_path).

    Returns:
        (base_url, uds_path):
           - For unix sockets: ("http://localhost/v1.43", "/var/run/docker.sock")
           - For tcp: ("http://proxy:2375/v1.43", None) or ("https://proxy:2376/v1.43", None)
    """
    # enable_docker (setting, then ENABLE_DOCKER) belongs to the local host from DOCKER_HOST. An
    # explicit docker_host is another endpoint, which only the caller's enable_docker can turn off.
    if enable_docker is None:
        if docker_host is not None:
            enable_docker = True
        else:
            try:
                from app.core.config import get_cached_setting
                enable_docker = bool(get_cached_setting("enable_docker", True))
            except Exception:
                enable_docker = env_bool("ENABLE_DOCKER", True)

    if not enable_docker:
        return "", None

    if docker_host is None:
        if env_docker_disabled():
            return "", None
        docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock").strip()
    else:
        docker_host = docker_host.strip()

    if not docker_host or docker_host.lower() in ("none", "off", "disabled"):
        return "", None

    if docker_host.startswith("unix://"):
        socket_path = docker_host[len("unix://"):]
        return f"http://localhost/{DOCKER_API_VERSION}", socket_path

    if docker_host.startswith("tcp://"):
        parsed = urlparse(docker_host)
        host = parsed.hostname or "localhost"
        default_port = 2376 if tls_enabled else 2375
        port = parsed.port or default_port
        scheme = "https" if tls_enabled else "http"
        return f"{scheme}://{host}:{port}/{DOCKER_API_VERSION}", None

    if docker_host.startswith("http://") or docker_host.startswith("https://"):
        parsed = urlparse(docker_host)
        scheme = "https" if tls_enabled else parsed.scheme
        host = parsed.hostname or "localhost"
        default_port = 443 if scheme == "https" else 2375
        port = parsed.port or default_port
        return f"{scheme}://{host}:{port}/{DOCKER_API_VERSION}", None

    # Fallback: treat as TCP address
    scheme = "https" if tls_enabled else "http"
    return f"{scheme}://{docker_host}/{DOCKER_API_VERSION}", None


def _build_client(
    base_url: str,
    uds_path: Optional[str],
    read_timeout: Optional[float] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient configured for the Docker endpoint."""
    if read_timeout is None:
        env_rt = os.environ.get("DOCKER_READ_TIMEOUT")
        if env_rt is not None:
            try:
                read_timeout = float(env_rt)
            except ValueError:
                read_timeout = DEFAULT_DOCKER_READ_TIMEOUT
        else:
            read_timeout = DEFAULT_DOCKER_READ_TIMEOUT

    timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0)
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)

    if uds_path:
        transport = httpx.AsyncHTTPTransport(uds=uds_path, limits=limits)
        return httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
        )
    else:
        # Remote TCP host (tcp:// or docker-socket-proxy): configure socket TCP keepalives
        socket_options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
        if hasattr(socket, "IPPROTO_TCP"):
            if hasattr(socket, "TCP_KEEPIDLE"):
                socket_options.append((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30))
            elif hasattr(socket, "TCP_KEEPALIVE"):
                socket_options.append((socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 30))
            if hasattr(socket, "TCP_KEEPINTVL"):
                socket_options.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10))
            if hasattr(socket, "TCP_KEEPCNT"):
                socket_options.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3))

        verify_param = ssl_context if ssl_context is not None else True
        # The transport checks the address it actually connects to (DNS rebinding)
        transport = GuardedAsyncHTTPTransport(verify=verify_param, socket_options=socket_options, limits=limits)
        return httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
        )


# Largest payload a frame header may announce before it is treated as corrupt (avoids OOM)
_MAX_DEMUX_PAYLOAD = 16 * 1024 * 1024


def _is_frame_header(buffer: bytes, pos: int) -> bool:
    """True when an 8-byte Docker frame header can start at pos (stdout or stderr, zero padding, sane size)."""
    return (
        buffer[pos] in (1, 2)
        and buffer[pos + 1:pos + 4] == b"\x00\x00\x00"
        and int.from_bytes(buffer[pos + 4:pos + 8], "big") <= _MAX_DEMUX_PAYLOAD
    )


def _demux_stream(buffer: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """
    Parse multiplexed Docker log stream frames from a byte buffer.

    Header format: [stream_type(1)][padding(3)][payload_size(4 big-endian)][payload]
    Stream types: 1=stdout, 2=stderr.

    On an invalid header, scans forward to the next byte that can start a valid header and logs
    one warning with the number of bytes skipped, instead of permanently mutating is_tty.
    The buffer is read by offset and cut once, so a chunk of many frames costs linear time.

    Returns:
        (frames, remaining_buffer)
        where frames is a list of (stream_type, payload_bytes).
    """
    frames: list[tuple[int, bytes]] = []
    size = len(buffer)
    pos = 0
    while size - pos >= 8:
        payload_size = int.from_bytes(buffer[pos + 4:pos + 8], "big")
        if buffer[pos] not in (1, 2) or buffer[pos + 1:pos + 4] != b"\x00\x00\x00" or payload_size > _MAX_DEMUX_PAYLOAD:
            skip_from = pos
            pos += 1
            while True:
                found = [i for i in (buffer.find(b"\x01\x00\x00\x00", pos), buffer.find(b"\x02\x00\x00\x00", pos)) if i >= 0]
                if not found:
                    # The last 3 bytes could still begin a header that the next chunk completes
                    pos = max(pos, size - 3)
                    break
                pos = min(found)
                if size - pos < 8 or _is_frame_header(buffer, pos):
                    break
                pos += 1
            logger.warning(
                "Docker stream demux skipped %d bytes that were not a frame header (first byte %s)",
                pos - skip_from,
                buffer[skip_from],
            )
            continue

        frame_total = 8 + payload_size
        if size - pos < frame_total:
            # Incomplete frame, wait for more data from stream
            break
        frames.append((buffer[pos], buffer[pos + 8:pos + frame_total]))
        pos += frame_total

    return frames, buffer[pos:]


_clean_text = clean_log_text
_detect_severity = detect_severity


_DOCKER_TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))(?:\s(.*))?$"
)


def _extract_docker_timestamp(line: str) -> tuple[Optional[str], str]:
    """
    Extract and strip Docker RFC3339 timestamp prefix if present.

    Returns:
        (timestamp_str, stripped_message)
        If no timestamp prefix matches, returns (None, line).
    """
    match = _DOCKER_TIMESTAMP_RE.match(line)
    if match:
        ts = match.group(1)
        msg = match.group(2) if match.group(2) is not None else ""
        return ts, msg
    return None, line


def _canonical_utc_timestamp(ts: str) -> str:
    """
    Docker's UTC timestamp (2026-11-09T12:00:00.123456789Z) in the form the queue consumer
    stores, which datetime.isoformat() gives: microseconds (cut, not rounded; left out when zero)
    and +00:00. The consumer then stores it without parsing it again. Other forms pass unchanged.
    """
    if not ts.endswith("Z"):
        return ts
    head, dot, fraction = ts[:-1].partition(".")
    if not dot:
        return head + "+00:00"
    if not fraction.isdigit():
        return ts
    micro = fraction[:6].ljust(6, "0")
    return f"{head}+00:00" if micro == "000000" else f"{head}.{micro}+00:00"


def _iso_to_unix_timestamp(ts_str: str) -> str:
    """
    Convert an RFC 3339 / ISO 8601 timestamp string to Unix epoch seconds string for Docker API.
    Docker Engine's /containers/{id}/logs parameter 'since' expects an integer Unix timestamp.
    """
    if ts_str.isdigit():
        return ts_str
    try:
        clean_ts = ts_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_ts)
        return str(int(dt.timestamp()))
    except Exception:
        return ts_str


def _event_since(event: dict) -> Optional[str]:
    """
    Build a /containers/{id}/logs 'since' value from a Docker event's own time.
    Prefers 'timeNano' as seconds.nanoseconds (the daemon parses fractional Unix timestamps),
    so a restarted container does not replay the last second of its previous run.
    Falls back to the integer 'time' in seconds.
    """
    time_nano = event.get("timeNano")
    if isinstance(time_nano, int) and time_nano > 0:
        return f"{time_nano // 1_000_000_000}.{time_nano % 1_000_000_000:09d}"
    time_s = event.get("time")
    if isinstance(time_s, int) and time_s > 0:
        return str(time_s)
    return None


def _started_at_since(started_at: Any) -> Optional[str]:
    """
    Build a /containers/{id}/logs 'since' value from a container's State.StartedAt, which Docker
    reports in UTC with up to nanoseconds (2026-11-04T17:00:05.123456789Z). None when it cannot be
    read, or for a container that never started (0001-01-01T00:00:00Z).
    """
    if not isinstance(started_at, str) or not started_at.endswith("Z"):
        return None
    base, _, fraction = started_at[:-1].partition(".")
    try:
        seconds = int(datetime.datetime.fromisoformat(base).replace(tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        return None
    if seconds <= 0:
        return None
    if not fraction:
        return str(seconds)
    if not fraction.isdigit():
        return None
    return f"{seconds}.{fraction[:9].ljust(9, '0')}"


# Valkey / Redis server line: <pid>:<role> <day> <mon> <year> <time> <level_char> <msg>
_VALKEY_LINE_RE = re.compile(
    r"^(\d+:[a-zA-Z])\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+([#*.-])\s+(.*)"
)
# Maintainerr pipe format: [app] | DD/MM/YYYY HH:MM:SS [LEVEL] ...
_APP_PIPE_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*\|\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+(.*)")
_BRACKET_LEVEL_RE = re.compile(r"^\[([a-zA-Z]+)\]\s*(.*)")


def _parse_docker_message_content(message: str, default_severity: int) -> tuple[str, int]:
    """
    Parse container message content to extract message payload and explicit severity
    from common formats (logfmt msg="...", Valkey/Redis, Maintainerr app pipe)
    while keeping the raw line intact.
    """
    # 1. Valkey / Redis server line (starts with a digit)
    m_valkey = _VALKEY_LINE_RE.match(message)
    if m_valkey:
        return m_valkey.group(7), VALKEY_SEVERITY.get(m_valkey.group(6), default_severity)

    # 2. Maintainerr pipe format
    m_app_pipe = _APP_PIPE_LINE_RE.match(message)
    if m_app_pipe:
        rem = m_app_pipe.group(4)
        m_level = _BRACKET_LEVEL_RE.match(rem)
        if m_level and m_level.group(1).lower() in SEVERITY_NAME_MAP:
            return m_level.group(2), SEVERITY_NAME_MAP[m_level.group(1).lower()]
        return rem, default_severity

    # 3. Logfmt format (searched once: the match that decides is the one captured)
    m_msg = RE_LOGFMT_MSG.search(message)
    if m_msg:
        extracted_msg = m_msg.group(1) or m_msg.group(2) or m_msg.group(3) or ""
        m_level = RE_LOGFMT_LEVEL.search(message)
        sev = default_severity
        if m_level and m_level.group(1).lower() in SEVERITY_NAME_MAP:
            sev = SEVERITY_NAME_MAP[m_level.group(1).lower()]
        return extracted_msg, sev

    return message, default_severity


def _make_log_entry(
    container_name: str,
    container_id: str,
    message: str,
    severity: int = 6,
    timestamp: Optional[str] = None,
    alias_cache: Optional[AliasCache] = None,
    source_ip: str = "docker",
    source_alias: str = "docker",
    raw: Optional[str] = None,
) -> dict:
    """Build a log entry dict compatible with the shared pipeline."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    default_alias = source_alias or source_ip
    resolved_alias = default_alias
    if alias_cache is not None:
        if source_ip == "docker":
            if default_alias != "docker":
                resolved_default = alias_cache.resolve(default_alias)
                if resolved_default != default_alias:
                    resolved_alias = resolved_default
                else:
                    resolved_source = alias_cache.resolve("docker")
                    if resolved_source != "docker":
                        resolved_alias = resolved_source
            else:
                resolved_alias = alias_cache.resolve("docker")

    clean_message = _clean_text(message)[:MAX_MESSAGE_BYTES]
    # Most lines are stored unchanged, so raw is the message itself: clean it once
    if raw is None or raw is message or raw == message:
        clean_raw = clean_message
    else:
        clean_raw = _clean_text(raw)[:MAX_MESSAGE_BYTES]

    return {
        "timestamp": _canonical_utc_timestamp(timestamp) if timestamp else now,
        "received_at": now,
        "source_ip": source_ip,
        "source_alias": resolved_alias,
        "app_name": container_name,
        "facility": 1,
        "severity": severity,
        "message": clean_message,
        "raw": clean_raw,
    }


async def _get_running_containers(client: httpx.AsyncClient) -> list[dict]:
    """Fetch currently running containers from the Docker API."""
    try:
        resp = await client.get("/containers/json")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to list Docker containers: {e}")
        raise


def resolve_container_display_name(
    cid: str,
    raw_name: Optional[str] = None,
    labels: Optional[dict[str, str]] = None,
) -> str:
    """
    Resolve human-friendly container display name using metadata and labels hierarchy:
    1. Labels.get("logshed.alias") or Labels.get("logshed.name")
    2. Labels.get("com.docker.compose.service")
    3. Labels.get("com.docker.swarm.service.name")
    4. Primary Docker container name (raw_name)
    5. Fallback 12-character container ID (cid[:12])
    """
    labels = labels or {}
    # 1. Custom LogShed labels
    custom_alias = labels.get("logshed.alias") or labels.get("logshed.name")
    if custom_alias and custom_alias.strip():
        return custom_alias.strip().lstrip("/")

    # 2. Docker Compose service label
    compose_service = labels.get("com.docker.compose.service")
    if compose_service and compose_service.strip():
        return compose_service.strip().lstrip("/")

    # 3. Docker Swarm service label
    swarm_service = labels.get("com.docker.swarm.service.name")
    if swarm_service and swarm_service.strip():
        return swarm_service.strip().lstrip("/")

    # 4. Primary Docker container name
    if raw_name and raw_name.strip():
        return raw_name.strip().lstrip("/")

    # 5. Fallback container ID
    return cid[:12] if cid else "unknown"


def _batch_update_app_aliases(
    conn,
    raw_name: str,
    target_alias: str,
    source_ip: str = "docker",
    source_alias: Optional[str] = None,
    batch_size: int = 1000,
) -> None:
    """
    Retroactively update app_name for existing logs in chunked batches.
    Prevents long table locks on large datasets while interleaving concurrent writes.
    Protects Syslog logs on shared IP hosts by scoping by Docker source_alias.
    """
    log_store.relabel_docker_app(conn, raw_name, target_alias, source_ip, source_alias, batch_size)


def _should_ignore_container(
    container_id: str,
    container_name: str,
    display_name: Optional[str] = None,
    exclude_containers: str = "",
) -> bool:
    """
    Determine if a container should be excluded from log tailing.
    Prevents self-tailing loops for LogShed itself.
    """
    # 1. Explicitly configured excluded container names/IDs
    excluded_names = {n.strip().lower() for n in exclude_containers.split(",") if n.strip()}
    # Always exclude default container names for this app
    excluded_names.update({
        "logshed",
        "log-shed",
        "log_shed",
    })

    name_clean = container_name.lower().lstrip("/")
    disp_clean = display_name.lower().lstrip("/") if display_name else ""
    if (
        name_clean in excluded_names
        or (disp_clean and disp_clean in excluded_names)
        or container_id.lower() in excluded_names
        or container_id[:12].lower() in excluded_names
    ):
        return True

    # 2. Check container short ID against HOSTNAME (Docker sets container short ID as hostname inside container)
    hostname = os.environ.get("HOSTNAME", "").strip().lower()
    if hostname and (container_id.lower().startswith(hostname) or hostname.startswith(container_id[:12].lower())):
        return True

    return False


def _heartbeat_interval() -> float:
    """DOCKER_HEARTBEAT_INTERVAL in seconds, read on each connect; 0 or less turns the keepalive off."""
    try:
        return float(os.environ.get("DOCKER_HEARTBEAT_INTERVAL", DEFAULT_DOCKER_HEARTBEAT_INTERVAL))
    except ValueError:
        return DEFAULT_DOCKER_HEARTBEAT_INTERVAL


class _StreamKeepalive:
    """
    Keepalive for one Docker stream. Its reader calls touch() for each chunk, and run() pings the
    daemon when the stream has been quiet for the interval. A failed ping marks the stream failed
    and cancels the reader, so the caller reconnects instead of waiting on a half-open connection.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        interval: float,
        cancel_event: asyncio.Event,
        failed_text: str,
        reconnect_text: str,
    ):
        self._client = client
        self._interval = interval
        self._cancel_event = cancel_event
        self._failed_text = failed_text
        self._reconnect_text = reconnect_text
        self.failed = False
        self.last_activity = asyncio.get_running_loop().time()

    def touch(self) -> None:
        self.last_activity = asyncio.get_running_loop().time()

    def timeout_error(self) -> httpx.ReadTimeout:
        return httpx.ReadTimeout(f"{self._failed_text}; half-open connection detected")

    async def run(self, reader: asyncio.Task) -> None:
        if self._interval <= 0:
            return
        while not self._cancel_event.is_set():
            try:
                await asyncio.sleep(min(self._interval / 2.0, 5.0))
            except asyncio.CancelledError:
                return
            if asyncio.get_running_loop().time() - self.last_activity < self._interval:
                continue
            try:
                ping_resp = await self._client.get("/_ping", timeout=5.0)
                if ping_resp.status_code != 200:
                    raise httpx.ConnectError(f"Ping returned status {ping_resp.status_code}")
                self.touch()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"{self._failed_text}: {e}. {self._reconnect_text}")
                self.failed = True
                reader.cancel()
                return


async def _cancel_and_wait(*tasks: asyncio.Task) -> None:
    """Cancel the tasks still running and wait for each to end, however it ends."""
    for task in tasks:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


class ContainerLogStream:
    """
    The log stream of one container, fed line by line through the assembler.

    Requests logs with timestamps=true, tracks the latest timestamp seen and resumes with
    since=<timestamp> on reconnect, dropping the lines Docker replays at that timestamp. A
    container that was already running when LogShed attached has no history to resume from, so
    it opens with tail=0. A container started while LogShed is running passes initial_since (its
    start event time) so the lines it wrote before the stream opened are read too. A container
    without a TTY sends multiplexed frames with 8-byte headers; one with a TTY sends raw text.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        container_id: str,
        container_name: str,
        assembler: KeyedMultilineAssembler,
        cancel_event: asyncio.Event,
        container_last_seen: Optional[dict[str, str]] = None,
        heartbeat_interval: Optional[float] = None,
        container_last_messages: Optional[dict[str, set[str]]] = None,
        alias_cache: Optional[AliasCache] = None,
        endpoint_id: int = 1,
        source_ip: str = "docker",
        source_alias: str = "docker",
        initial_since: Optional[str] = None,
    ):
        self.client = client
        self.container_id = container_id
        self.container_name = container_name
        self.assembler = assembler
        self.cancel_event = cancel_event
        self.container_last_seen = container_last_seen
        self.heartbeat_interval = _heartbeat_interval() if heartbeat_interval is None else heartbeat_interval
        self.container_last_messages = container_last_messages
        self.alias_cache = alias_cache
        self.source_ip = source_ip
        self.source_alias = source_alias
        self.initial_since = initial_since
        self.stream_key = f"docker:{endpoint_id}:{container_id}"
        self.url = f"/containers/{container_id}/logs"
        self.label = f"{container_name} ({container_id[:12]})"

        self.last_seen_timestamp: Optional[str] = (
            container_last_seen.get(container_id) if container_last_seen is not None else None
        )
        self.seen_at_last_timestamp: set[str] = (
            container_last_messages.get(container_id, set()).copy()
            if container_last_messages is not None
            else set()
        )
        self.is_tty = False
        # Bytes not yet split into lines (TTY) or into frames (multiplexed)
        self.buffer = b""
        # Multiplexed mode: text of a line whose newline has not arrived yet
        self.line_buffer = ""

    async def run(self) -> None:
        """Stream until the container is gone or the cancel event is set, reconnecting with backoff."""
        await self._detect_tty()
        backoff = _CONTAINER_INITIAL_BACKOFF
        while not self.cancel_event.is_set():
            params = self._request_params()
            try:
                async with self.client.stream("GET", self.url, params=params) as resp:
                    if getattr(resp, "status_code", None) == 404:
                        logger.debug(f"Container {self.label} not found (404); stopping tailer.")
                        return
                    if getattr(resp, "status_code", None) == 400 and "since" in params:
                        logger.warning(
                            f"Docker rejected 'since' parameter for {self.label}: 400. "
                            f"Resetting last seen timestamp and falling back to tail='0'."
                        )
                        self._forget_resume_point()
                        continue
                    resp.raise_for_status()
                    backoff = _CONTAINER_INITIAL_BACKOFF
                    if await self._follow(resp):
                        return

                # Stream ended normally (EOF)
                if self.cancel_event.is_set():
                    return
                logger.debug(f"Log stream ended for container {self.label}; reconnecting in {backoff:.1f}s...")
            except asyncio.CancelledError:
                raise
            except (httpx.RemoteProtocolError, httpx.HTTPError, Exception) as e:
                if self.cancel_event.is_set():
                    return
                if isinstance(e, httpx.HTTPStatusError) and getattr(e.response, "status_code", None) == 404:
                    logger.debug(f"Container {self.label} returned 404; stopping tailer task.")
                    return
                logger.debug(f"Log stream disconnected for {self.label}: {e}. Reconnecting in {backoff:.1f}s...")

            try:
                await asyncio.wait_for(self.cancel_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass

            backoff = min(backoff * _CONTAINER_BACKOFF_FACTOR, _CONTAINER_MAX_BACKOFF)

    async def _detect_tty(self) -> None:
        """TTY mode sends raw text; without a TTY the stream is multiplexed. Multiplexed if inspect fails."""
        try:
            inspect_resp = await self.client.get(f"/containers/{self.container_id}/json")
            if inspect_resp.status_code == 200:
                config = inspect_resp.json().get("Config", {})
                self.is_tty = bool(config.get("Tty", False))
        except Exception:
            pass

    def _request_params(self) -> dict[str, str]:
        params = {
            "stdout": "true",
            "stderr": "true",
            "follow": "true",
            "timestamps": "true",
        }
        if self.last_seen_timestamp:
            params["since"] = _iso_to_unix_timestamp(self.last_seen_timestamp)
        elif self.initial_since:
            params["since"] = self.initial_since
        else:
            params["tail"] = "0"
        return params

    def _forget_resume_point(self) -> None:
        self.last_seen_timestamp = None
        self.initial_since = None
        if self.container_last_seen is not None:
            self.container_last_seen.pop(self.container_id, None)

    async def _follow(self, resp: httpx.Response) -> bool:
        """
        Read one open stream with its keepalive until it ends. True when the cancel event stopped
        it; raises the keepalive's timeout when the daemon stopped answering pings.
        """
        self.buffer = b""
        self.line_buffer = ""
        keepalive = _StreamKeepalive(
            self.client,
            self.heartbeat_interval,
            self.cancel_event,
            f"Docker keepalive heartbeat failed for {self.label}",
            "Triggering reconnect.",
        )
        stream_task = asyncio.create_task(self._consume(resp, keepalive))
        monitor_task = asyncio.create_task(keepalive.run(stream_task))
        cancel_task = asyncio.create_task(self.cancel_event.wait())
        try:
            done, _pending = await asyncio.wait([stream_task, cancel_task], return_when=asyncio.FIRST_COMPLETED)
            if cancel_task in done:
                return True
            if keepalive.failed:
                raise keepalive.timeout_error()
            await stream_task
            return False
        except asyncio.CancelledError:
            if keepalive.failed:
                raise keepalive.timeout_error()
            raise
        finally:
            await _cancel_and_wait(cancel_task, stream_task, monitor_task)

    async def _consume(self, resp: httpx.Response, keepalive: _StreamKeepalive) -> None:
        async for chunk in resp.aiter_bytes():
            if self.cancel_event.is_set():
                return
            keepalive.touch()
            self.buffer += chunk
            if self.is_tty:
                await self._feed_tty_lines()
            else:
                await self._feed_frames()
        # Flush any trailing TTY or multiplexed bytes on clean stream completion
        await self._flush()

    async def _feed_tty_lines(self) -> None:
        """TTY mode: raw text, split by newline; a line longer than the cap is cut into pieces."""
        while b"\n" in self.buffer:
            line_bytes, self.buffer = self.buffer.split(b"\n", 1)
            if not line_bytes:
                continue
            await self._emit(line_bytes.decode("utf-8", errors="replace").rstrip("\r"))

        while len(self.buffer) > MAX_TTY_BUFFER:
            logger.warning(
                f"Docker TTY buffer exceeded {MAX_TTY_BUFFER} bytes without newline for container {self.container_name}. Truncating chunk."
            )
            chunk_bytes = self.buffer[:MAX_TTY_BUFFER]
            self.buffer = self.buffer[MAX_TTY_BUFFER:]
            await self._emit(chunk_bytes.decode("utf-8", errors="replace").rstrip("\r"))

    async def _feed_frames(self) -> None:
        """Multiplexed mode: demux frames with automatic header resynchronization, then split lines."""
        frames, self.buffer = _demux_stream(self.buffer)
        for _stream_type, payload in frames:
            text = self.line_buffer + payload.decode("utf-8", errors="replace")
            lines = text.split("\n")
            self.line_buffer = lines.pop()
            for line in lines:
                line = line.rstrip("\r")
                if line:
                    await self._emit(line)

            # Same cap as TTY mode: a container writing without "\n" must not
            # grow the pending line (and the per-frame copy of it) without limit.
            while len(self.line_buffer) > MAX_TTY_BUFFER:
                logger.warning(
                    f"Docker log line buffer exceeded {MAX_TTY_BUFFER} bytes without newline for container {self.container_name}. Truncating chunk."
                )
                line = self.line_buffer[:MAX_TTY_BUFFER].rstrip("\r")
                self.line_buffer = self.line_buffer[MAX_TTY_BUFFER:]
                if line:
                    await self._emit(line)

    async def _flush(self) -> None:
        if self.is_tty and self.buffer:
            while len(self.buffer) > MAX_TTY_BUFFER:
                logger.warning(
                    f"Docker TTY trailing buffer exceeded {MAX_TTY_BUFFER} bytes for container {self.container_name}. Truncating chunk."
                )
                chunk_bytes = self.buffer[:MAX_TTY_BUFFER]
                self.buffer = self.buffer[MAX_TTY_BUFFER:]
                line = chunk_bytes.decode("utf-8", errors="replace").rstrip("\r")
                if line:
                    await self._emit(line)
            line = self.buffer.decode("utf-8", errors="replace").rstrip("\r")
            self.buffer = b""
            if line:
                await self._emit(line)
        elif not self.is_tty and self.line_buffer:
            line = self.line_buffer.rstrip("\r")
            self.line_buffer = ""
            if line:
                await self._emit(line)

    async def _emit(self, line: str) -> None:
        entry = self._process_line(line)
        if entry:
            await self.assembler.feed(self.stream_key, entry)

    def _process_line(self, line: str) -> Optional[dict]:
        """The log entry for one timestamped line, or None for a replay or an empty line."""
        ts, message = _extract_docker_timestamp(line)
        if ts and not self._note_timestamp(ts, message):
            return None
        if message or ts:
            severity = _detect_severity(message)
            parsed_message, parsed_sev = _parse_docker_message_content(message, severity)
            return _make_log_entry(
                self.container_name, self.container_id, parsed_message,
                severity=parsed_sev, timestamp=ts,
                alias_cache=self.alias_cache,
                source_ip=self.source_ip,
                source_alias=self.source_alias,
                raw=message,
            )
        return None

    def _note_timestamp(self, ts: str, message: str) -> bool:
        """
        Move the resume point to ts. False for a line Docker replays: older than the resume point,
        or already seen at it (its 'since' filter is inclusive).
        """
        last = self.last_seen_timestamp
        if last and ts == last:
            if message in self.seen_at_last_timestamp:
                return False
            self.seen_at_last_timestamp.add(message)
            if self.container_last_messages is not None:
                self.container_last_messages[self.container_id] = self.seen_at_last_timestamp
        elif last and ts < last:
            return False
        else:
            self.last_seen_timestamp = ts
            self.seen_at_last_timestamp = {message}
            if self.container_last_seen is not None:
                self.container_last_seen[self.container_id] = ts
            if self.container_last_messages is not None:
                self.container_last_messages[self.container_id] = self.seen_at_last_timestamp
        return True


async def _tail_container_logs(
    client: httpx.AsyncClient,
    container_id: str,
    container_name: str,
    assembler: KeyedMultilineAssembler,
    cancel_event: asyncio.Event,
    container_last_seen: Optional[dict[str, str]] = None,
    heartbeat_interval: Optional[float] = None,
    container_last_messages: Optional[dict[str, set[str]]] = None,
    alias_cache: Optional[AliasCache] = None,
    endpoint_id: int = 1,
    source_ip: str = "docker",
    source_alias: str = "docker",
    initial_since: Optional[str] = None,
) -> None:
    """Stream logs for a single container until it is gone or cancel_event is set (see ContainerLogStream)."""
    await ContainerLogStream(
        client, container_id, container_name, assembler, cancel_event,
        container_last_seen=container_last_seen,
        heartbeat_interval=heartbeat_interval,
        container_last_messages=container_last_messages,
        alias_cache=alias_cache,
        endpoint_id=endpoint_id,
        source_ip=source_ip,
        source_alias=source_alias,
        initial_since=initial_since,
    ).run()


def effective_source_ip(url: Optional[str], source_alias: Optional[str], endpoint_id: int) -> str:
    """
    Return the source_ip stamped on container logs from a Docker endpoint.

    Network endpoints use the URL host name. Unix-socket endpoints use "docker" for the
    primary endpoint (id 1) and the endpoint alias for any other endpoint.
    """
    url = (url or "").strip()
    if url.startswith(("tcp://", "http://", "https://")):
        parsed = urlparse(url)
        if parsed.hostname:
            return parsed.hostname
    if endpoint_id == 1:
        return "docker"
    return source_alias or f"docker-{endpoint_id}"


_ALIAS_DEDUP_MAX_ENTRIES = 2000


class _AliasDedupSet:
    """
    Bounded LRU of (raw_name, display_name, source_ip) triples already backfilled.

    Short-lived containers with random names would otherwise grow it forever. When full,
    the least recently seen triple is dropped; a dropped triple at worst triggers one more
    (idempotent) backfill if that container shows up again.
    """

    def __init__(self, max_entries: int = _ALIAS_DEDUP_MAX_ENTRIES):
        self._max_entries = max_entries
        # dict keeps insertion order: the first key is the least recently seen
        self._items: dict[tuple, None] = {}

    def __contains__(self, item: Any) -> bool:
        return item in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"_AliasDedupSet(size={len(self._items)}, max_entries={self._max_entries})"

    def add(self, item: tuple) -> None:
        """Insert item, or mark it most recently seen if present, evicting the oldest when full."""
        self._items.pop(item, None)
        self._items[item] = None
        while len(self._items) > self._max_entries:
            del self._items[next(iter(self._items))]


class DockerTailer:
    """
    Supervisor that monitors Docker events and tails container logs.

    Manages lifecycle:
    - On start: enumerate running containers and begin tailing each one.
    - On 'start' event: begin tailing the newly started container.
    - On 'die' event: cancel the tailer task for that container.
    - On disconnect: exponential backoff reconnect.
    """

    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        db_path: Optional[str | Path] = None,
        alias_cache: Optional[AliasCache] = None,
        socket_poll_interval: Optional[float] = None,
        socket_poll_max: Optional[float] = None,
        enable_docker: Optional[bool] = None,
        exclude_containers: Optional[str] = None,
        source_alias: Optional[str] = None,
        discovery_interval: Optional[float] = None,
        endpoint_id: int = 1,
        name: str = "Primary Docker",
        url: Optional[str] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ):
        self._assembler = assembler
        self._running = False
        self._cancel_event = asyncio.Event()
        self._settings_event = asyncio.Event()
        # container_id -> (task, cancel_event)
        self._tailers: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}
        # container_id -> latest seen RFC3339 log timestamp string
        self._container_last_seen: dict[str, str] = {}
        # container_id -> set of message contents seen at the latest timestamp (for deduplication on reconnect)
        self._container_last_messages: dict[str, set[str]] = {}
        # container_id -> 'since' value from its latest start event (cleared on die)
        self._container_started_since: dict[str, str] = {}
        # Containers being tailed when the connection dropped, until the next attach. A running
        # container not among them started while the events stream was down, so its start event was lost.
        self._tailed_before_drop: Optional[set[str]] = None
        self._socket_poll_interval = (
            socket_poll_interval
            if socket_poll_interval is not None
            else DEFAULT_DOCKER_SOCKET_POLL_INTERVAL
        )
        self._socket_poll_max = (
            socket_poll_max
            if socket_poll_max is not None
            else DEFAULT_DOCKER_SOCKET_POLL_MAX
        )
        self._discovery_interval = (
            discovery_interval
            if discovery_interval is not None
            else DEFAULT_DOCKER_DISCOVERY_INTERVAL
        )
        self._db_path = Path(db_path) if db_path else None
        if alias_cache is not None:
            self.alias_cache = alias_cache
            self._owns_alias_cache = False
        else:
            effective_db_path = self._db_path or get_db_path()
            self.alias_cache = AliasCache(effective_db_path)
            self._owns_alias_cache = True

        from app.core.config import get_cached_setting, get_docker_host
        self.enable_docker = (
            enable_docker
            if enable_docker is not None
            else bool(get_cached_setting("enable_docker", True))
        )
        self.exclude_containers = (
            exclude_containers
            if exclude_containers is not None
            else str(get_cached_setting("docker_exclude_containers", ""))
        )
        self.source_alias = (
            source_alias
            if source_alias is not None
            else str(get_cached_setting("docker_source_alias", "docker"))
        )
        self.endpoint_id = endpoint_id
        self.name = name
        self.url = url.strip() if (url and url.strip()) else get_docker_host()
        self.ssl_context = ssl_context
        self.tls_enabled = ssl_context is not None

        self._current_client: Optional[httpx.AsyncClient] = None
        self._updated_app_aliases = _AliasDedupSet()
        self._bg_tasks: set[asyncio.Task] = set()

        # Connection health and latency status
        self._is_connected: bool = False
        self._last_error: Optional[str] = None
        self._last_activity_iso: Optional[str] = None
        self._latency_ms: Optional[float] = None

    def _effective_source_ip(self) -> str:
        """Derive the source IP/hostname for container logs from the endpoint URL or alias."""
        return effective_source_ip(self.url, self.source_alias, self.endpoint_id)

    def get_status(self) -> dict[str, Any]:
        """Return runtime connection health and container metrics for this host."""
        return {
            "endpoint_id": self.endpoint_id,
            "name": self.name,
            "url": self.url,
            "source_alias": self.source_alias,
            "enabled": self.enable_docker,
            "is_connected": self._is_connected,
            "active_containers": len(self._tailers),
            "last_error": self._last_error,
            "last_activity": self._last_activity_iso,
            "latency_ms": self._latency_ms,
        }

    def _schedule_app_alias_update(
        self,
        raw_name: str,
        display_name: str,
        source_ip: Optional[str] = None,
    ) -> None:
        """
        Schedule chunked background database update for app_name if alias differs from raw name.
        Uses in-memory deduplication set to avoid redundant batch queries across discovery cycles.
        """
        if not raw_name or not display_name or raw_name == display_name:
            return

        effective_ip = source_ip or self._effective_source_ip()
        triple = (raw_name, display_name, effective_ip)
        if triple in self._updated_app_aliases:
            self._updated_app_aliases.add(triple)  # refresh recency
            return
        self._updated_app_aliases.add(triple)

        effective_db_path = self._db_path or get_db_path()

        def _run_batch() -> None:
            try:
                conn = get_connection(effective_db_path)
                try:
                    _batch_update_app_aliases(
                        conn,
                        raw_name,
                        display_name,
                        source_ip=effective_ip,
                        source_alias=self.source_alias,
                    )
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(
                    f"Background retroactive app alias update failed for {raw_name} -> {display_name} ({effective_ip}): {e}"
                )

        task = asyncio.create_task(asyncio.to_thread(_run_batch))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def update_settings(
        self,
        enable_docker: Optional[bool] = None,
        exclude_containers: Optional[str] = None,
        source_alias: Optional[str] = None,
    ) -> None:
        """
        Dynamically update Docker collector runtime configuration.
        Stops or attaches container tailers according to enable_docker and exclusions.
        """
        changed = False

        if enable_docker is not None and enable_docker != self.enable_docker:
            changed = True
            prev_enabled = self.enable_docker
            self.enable_docker = enable_docker
            if not enable_docker and prev_enabled:
                logger.info("Docker collector disabled via settings. Stopping container tailers.")
                await self._cleanup_tailers()
            elif enable_docker and not prev_enabled:
                logger.info("Docker collector enabled via settings. Resuming container tailing.")
                self._settings_event.set()
                if self._current_client and not self._current_client.is_closed:
                    try:
                        await self._attach_running_containers(self._current_client)
                    except Exception as e:
                        logger.warning(f"Error re-attaching containers on enable: {e}")

        if exclude_containers is not None and exclude_containers != self.exclude_containers:
            changed = True
            self.exclude_containers = exclude_containers
            for cid, (task, cancel) in list(self._tailers.items()):
                cname = task.get_name().replace("docker-tail-", "")
                if _should_ignore_container(cid, cname, exclude_containers=self.exclude_containers):
                    logger.info(f"Stopping container tailer {cname} ({cid[:12]}) due to updated exclusions.")
                    await self._stop_tailer(cid)
            if self.enable_docker and self._current_client and not self._current_client.is_closed:
                try:
                    await self._attach_running_containers(self._current_client)
                except Exception as e:
                    logger.debug(f"Error attaching newly included containers: {e}")

        if source_alias is not None and source_alias != self.source_alias:
            changed = True
            self.source_alias = source_alias

        if changed:
            logger.info(
                f"DockerTailer settings updated: enable_docker={self.enable_docker}, "
                f"exclude_containers='{self.exclude_containers}', source_alias='{self.source_alias}'"
            )

    async def run(self) -> None:
        """
        Main supervisor loop with auto-reconnect backoff.
        """
        self._running = True
        self._cancel_event.clear()

        if self._owns_alias_cache and not self.alias_cache._refresh_task:
            await self.alias_cache.start()

        base_url, uds_path = _parse_docker_host(
            enable_docker=self.enable_docker,
            docker_host=self.url,
            tls_enabled=self.tls_enabled,
        )
        name_suffix = f" for {self.name}" if self.name != "Primary Docker" else ""
        if not base_url or not self.enable_docker:
            logger.info(
                f"Docker container tailing disabled by configuration{name_suffix}; operating in syslog-only mode."
            )
            while self._running and not self.enable_docker:
                self._settings_event.clear()
                try:
                    wait_tasks = [
                        asyncio.create_task(self._cancel_event.wait()),
                        asyncio.create_task(self._settings_event.wait()),
                    ]
                    done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    if self._cancel_event.is_set():
                        return
                except asyncio.CancelledError:
                    return

            base_url, uds_path = _parse_docker_host(
                enable_docker=self.enable_docker,
                docker_host=self.url,
                tls_enabled=self.tls_enabled,
            )
            if not base_url or not self.enable_docker:
                return

        if uds_path and not os.path.exists(uds_path):
            logger.warning(
                f"Docker socket not found at {uds_path}{name_suffix}. Polling for socket before fallback..."
            )
            elapsed = 0.0
            while elapsed < self._socket_poll_max and not os.path.exists(uds_path):
                wait_time = min(self._socket_poll_interval, self._socket_poll_max - elapsed)
                if wait_time <= 0:
                    break
                try:
                    await asyncio.wait_for(self._cancel_event.wait(), timeout=wait_time)
                    return
                except asyncio.TimeoutError:
                    elapsed += wait_time
                logger.debug(f"Polling for Docker socket at {uds_path} ({elapsed:.1f}s / {self._socket_poll_max:.1f}s)...")

            if not os.path.exists(uds_path):
                logger.info(
                    f"Docker socket not found at {uds_path}{name_suffix}. "
                    "Docker container tailing disabled; operating in syslog-only mode."
                )
                try:
                    await self._cancel_event.wait()
                except asyncio.CancelledError:
                    pass
                return

            logger.info(f"Docker socket found at {uds_path} after {elapsed:.1f}s.")

        backoff = _INITIAL_BACKOFF

        while self._running:
            if not self.enable_docker:
                self._settings_event.clear()
                try:
                    wait_tasks = [
                        asyncio.create_task(self._cancel_event.wait()),
                        asyncio.create_task(self._settings_event.wait()),
                    ]
                    done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    if self._cancel_event.is_set():
                        return
                    continue
                except asyncio.CancelledError:
                    return

            base_url, uds_path = _parse_docker_host(
                enable_docker=self.enable_docker,
                docker_host=self.url,
                tls_enabled=self.tls_enabled,
            )
            if not base_url:
                await asyncio.sleep(1.0)
                continue

            target_desc = uds_path if uds_path else base_url
            attached = False
            try:
                async with _build_client(base_url, uds_path, ssl_context=self.ssl_context) as client:
                    self._current_client = client
                    try:
                        # Measure initial latency via ping
                        t0 = asyncio.get_running_loop().time()
                        try:
                            ping_resp = await client.get("/_ping", timeout=5.0)
                            if ping_resp.status_code == 200:
                                self._latency_ms = round((asyncio.get_running_loop().time() - t0) * 1000, 2)
                        except Exception:
                            pass

                        # Start tailing all currently running containers
                        await self._attach_running_containers(client)
                        attached = True
                        self._is_connected = True
                        self._last_error = None
                        self._last_activity_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        logger.info(f"Connected to Docker daemon at {target_desc} ({self.name})")
                        backoff = _INITIAL_BACKOFF  # Reset on successful connect

                        # Stream Docker events for start/die
                        await self._watch_events(client)
                    finally:
                        self._current_client = None
                        self._is_connected = False

            except asyncio.CancelledError:
                self._is_connected = False
                raise
            except Exception as e:
                self._is_connected = False
                self._last_error = str(e)
                if not self._running:
                    break
                logger.warning(
                    f"Docker connection error at {target_desc} ({self.name}): {e}. Reconnecting in {backoff:.0f}s..."
                )
                # Only after a connection that attached: otherwise (Docker unreachable since start-up)
                # every container would look new and be read from its start
                if attached and self._tailed_before_drop is None:
                    self._tailed_before_drop = set(self._tailers)
                await self._cleanup_tailers()
                # Wait before reconnecting, but end at once when stop() is called
                try:
                    await asyncio.wait_for(self._cancel_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._is_connected = False
        self._cancel_event.set()
        await self._cleanup_tailers()
        for task in list(self._bg_tasks):
            task.cancel()
        if self._owns_alias_cache:
            await self.alias_cache.stop()
        logger.info(f"Docker tailer stopped ({self.name})")

    async def _discover_containers(
        self, client: Optional[httpx.AsyncClient] = None
    ) -> list[dict]:
        """
        Enumerate running containers and purge tracking state for containers
        that are no longer running or discovered.
        """
        target_client = client or self._current_client
        if target_client is None:
            return []

        containers = await _get_running_containers(target_client)
        active_ids: set[str] = set()
        for c in containers:
            cid = c.get("Id", "")
            if cid:
                active_ids.add(cid)
                if len(cid) >= 12:
                    active_ids.add(cid[:12])

        # Purge removed container IDs from tracking dictionaries
        for cid in list(self._container_last_seen.keys()):
            if cid not in active_ids:
                self._container_last_seen.pop(cid, None)

        for cid in list(self._container_last_messages.keys()):
            if cid not in active_ids:
                self._container_last_messages.pop(cid, None)

        for cid in list(self._container_started_since.keys()):
            if cid not in active_ids:
                self._container_started_since.pop(cid, None)

        for cid in list(self._tailers.keys()):
            if cid not in active_ids:
                await self._stop_tailer(cid)

        return containers

    async def _attach_running_containers(self, client: httpx.AsyncClient) -> None:
        """
        Enumerate running containers and start a log tailer for each, skipping excluded containers.

        After a dropped connection, a container that was not being tailed and has no last seen line
        started during the gap: it is read from its State.StartedAt. Others open at the end of the log.
        """
        containers = await self._discover_containers(client)
        tailed_before_drop = self._tailed_before_drop
        for c in containers:
            cid = c.get("Id", "")
            names = c.get("Names", [])
            raw_name = names[0].lstrip("/") if names else ""
            labels = c.get("Labels", {})
            display_name = resolve_container_display_name(cid, raw_name=raw_name, labels=labels)
            if _should_ignore_container(
                cid, raw_name, display_name=display_name, exclude_containers=self.exclude_containers
            ):
                logger.debug(f"Skipping tailing self/excluded container {display_name} ({cid[:12]})")
                continue
            self._schedule_app_alias_update(raw_name, display_name, source_ip=self._effective_source_ip())
            since = None
            if tailed_before_drop is not None and cid not in tailed_before_drop and cid not in self._container_last_seen:
                since = await self._container_start_since(client, cid)
                if since:
                    self._container_started_since[cid] = since
            self._start_tailer(client, cid, display_name, since=since)
        self._tailed_before_drop = None

    async def _container_start_since(self, client: httpx.AsyncClient, container_id: str) -> Optional[str]:
        """The 'since' value for a container's latest start, from its inspect result (None if unknown)."""
        try:
            resp = await client.get(f"/containers/{container_id}/json")
            resp.raise_for_status()
            return _started_at_since(resp.json().get("State", {}).get("StartedAt"))
        except Exception as e:
            logger.debug(f"Could not read the start time of container {container_id[:12]}: {e}")
            return None

    def _start_tailer(
        self,
        client: httpx.AsyncClient,
        container_id: str,
        container_name: str,
        since: Optional[str] = None,
    ) -> None:
        """
        Start a log tailer task for a container if not already active and not excluded.

        since is the Docker logs 'since' value for a container started while LogShed is running;
        without it the tailer opens at the end of the log (tail=0).
        """
        if _should_ignore_container(container_id, container_name, exclude_containers=self.exclude_containers):
            return

        if container_id in self._tailers:
            task, _ = self._tailers[container_id]
            if not task.done():
                return
            self._tailers.pop(container_id, None)

        cancel = asyncio.Event()
        task = asyncio.create_task(
            _tail_container_logs(
                client, container_id, container_name,
                self._assembler, cancel,
                container_last_seen=self._container_last_seen,
                container_last_messages=self._container_last_messages,
                alias_cache=self.alias_cache,
                endpoint_id=self.endpoint_id,
                source_ip=self._effective_source_ip(),
                source_alias=self.source_alias,
                initial_since=since,
            ),
            name=f"docker-tail-{container_name}",
        )
        self._tailers[container_id] = (task, cancel)
        task.add_done_callback(lambda t: self._tailers.pop(container_id, None))
        logger.info(f"Started tailing container {container_name} ({container_id[:12]}) on {self.name}")

    async def _stop_tailer(self, container_id: str) -> None:
        """Stop and clean up a single container tailer."""
        if container_id not in self._tailers:
            return

        task, cancel = self._tailers.pop(container_id)
        cancel.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _cleanup_tailers(self) -> None:
        """Stop all active tailer tasks."""
        ids = list(self._tailers.keys())
        for cid in ids:
            await self._stop_tailer(cid)

    async def _watch_events(self, client: httpx.AsyncClient) -> None:
        """
        Stream Docker events, reacting to 'start' and 'die' container events.

        Uses the /events endpoint with server-sent JSON lines.
        """
        params = {
            "filters": json.dumps({"event": ["start", "die", "restart"], "type": ["container"]}),
        }

        async with client.stream("GET", "/events", params=params) as resp:
            resp.raise_for_status()
            keepalive = _StreamKeepalive(
                client,
                _heartbeat_interval(),
                self._cancel_event,
                "Docker events keepalive heartbeat failed",
                "Reconnecting supervisor.",
            )

            async def _consume_events() -> None:
                async for line in resp.aiter_lines():
                    if self._cancel_event.is_set():
                        return
                    keepalive.touch()
                    if not line.strip():
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    action = event.get("Action", "")
                    actor = event.get("Actor", {})
                    cid = actor.get("ID", event.get("id", ""))
                    attrs = actor.get("Attributes", {})
                    raw_name = attrs.get("name", "")
                    display_name = resolve_container_display_name(cid, raw_name=raw_name, labels=attrs)

                    if action in ("start", "restart"):
                        if _should_ignore_container(
                            cid, raw_name, display_name=display_name, exclude_containers=self.exclude_containers
                        ):
                            continue
                        self._schedule_app_alias_update(raw_name, display_name, source_ip=self._effective_source_ip())
                        logger.info(f"Container {action}: {display_name} ({cid[:12]}) on {self.name}")
                        since = _event_since(event)
                        if action == "restart":
                            await self._stop_tailer(cid)
                            # Docker emits 'start' just before 'restart'; keep its earlier time so lines
                            # written between the two events are not skipped
                            since = self._container_started_since.get(cid, since)
                        if since:
                            self._container_started_since[cid] = since
                        self._start_tailer(client, cid, display_name, since=since)
                    elif action == "die":
                        logger.info(f"Container died: {display_name} ({cid[:12]})")
                        self._container_started_since.pop(cid, None)
                        await self._stop_tailer(cid)

            events_task = asyncio.create_task(_consume_events())

            async def _periodic_discovery() -> None:
                if self._discovery_interval <= 0:
                    return
                while not self._cancel_event.is_set():
                    try:
                        await asyncio.sleep(self._discovery_interval)
                    except asyncio.CancelledError:
                        return
                    try:
                        await self._discover_containers(client)
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        logger.debug(f"Periodic container discovery error: {e}")

            discovery_task = asyncio.create_task(_periodic_discovery())
            monitor_task = asyncio.create_task(keepalive.run(events_task))
            cancel_task = asyncio.create_task(self._cancel_event.wait())
            try:
                done, pending = await asyncio.wait(
                    [events_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done:
                    return
                if keepalive.failed:
                    raise keepalive.timeout_error()
                await events_task
                if not self._cancel_event.is_set():
                    # The daemon closed the stream cleanly (for example while restarting). Reconnect like
                    # after an error: the tailers hold this client, which closes when the caller returns.
                    raise httpx.RemoteProtocolError("Docker events stream ended")
            except asyncio.CancelledError:
                if keepalive.failed:
                    raise keepalive.timeout_error()
                raise
            finally:
                await _cancel_and_wait(cancel_task, events_task, discovery_task, monitor_task)
