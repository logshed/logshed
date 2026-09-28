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
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.collectors.syslog import AliasCache
from app.core.config import get_db_path
from app.core.pipeline import (
    KeyedMultilineAssembler,
    detect_severity,
    clean_log_text,
    SEVERITY_LEVEL_MAP,
)

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

MAX_TTY_BUFFER = 65536  # 64 KB limit to prevent unbounded memory growth in TTY mode


def _parse_docker_host(enable_docker: Optional[bool] = None) -> tuple[str, Optional[str]]:
    """
    Parse DOCKER_HOST env var into (base_url, uds_path).

    Returns:
        (base_url, uds_path):
           - For unix sockets: ("http://localhost/v1.43", "/var/run/docker.sock")
           - For tcp: ("http://proxy:2375/v1.43", None)
    """
    if enable_docker is None:
        try:
            from app.core.config import get_cached_setting
            enable_docker = bool(get_cached_setting("enable_docker", True))
        except Exception:
            enable_docker = os.environ.get("ENABLE_DOCKER", "true").lower() not in ("0", "false", "no", "disabled")

    if not enable_docker:
        return "", None

    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock").strip()

    if not docker_host or docker_host.lower() in ("none", "off", "disabled") or os.environ.get("ENABLE_DOCKER", "true").lower() in ("0", "false", "no", "disabled"):
        return "", None

    if docker_host.startswith("unix://"):
        socket_path = docker_host[len("unix://"):]
        return f"http://localhost/{DOCKER_API_VERSION}", socket_path

    if docker_host.startswith("tcp://"):
        parsed = urlparse(docker_host)
        host = parsed.hostname or "localhost"
        port = parsed.port or 2375
        return f"http://{host}:{port}/{DOCKER_API_VERSION}", None

    if docker_host.startswith("http://") or docker_host.startswith("https://"):
        parsed = urlparse(docker_host)
        scheme = parsed.scheme
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if scheme == "https" else 2375)
        return f"{scheme}://{host}:{port}/{DOCKER_API_VERSION}", None

    # Fallback: treat as TCP address
    return f"http://{docker_host}/{DOCKER_API_VERSION}", None


def _build_client(
    base_url: str,
    uds_path: Optional[str],
    read_timeout: Optional[float] = None,
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

        transport = httpx.AsyncHTTPTransport(socket_options=socket_options, limits=limits)
        return httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
        )


def _demux_stream(buffer: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """
    Parse multiplexed Docker log stream frames from a byte buffer.

    Header format: [stream_type(1)][padding(3)][payload_size(4 big-endian)][payload]
    Stream types: 1=stdout, 2=stderr.

    On unexpected stream type or invalid header, advances byte-by-byte to resynchronize
    to the next valid frame header instead of permanently mutating is_tty.

    Returns:
        (frames, remaining_buffer)
        where frames is a list of (stream_type, payload_bytes).
    """
    frames: list[tuple[int, bytes]] = []
    while len(buffer) >= 8:
        stream_type = buffer[0]
        # Docker stream header: stream_type must be 1 (stdout) or 2 (stderr),
        # and bytes 1..3 must be 0x00 padding.
        if stream_type not in (1, 2) or buffer[1:4] != b"\x00\x00\x00":
            logger.warning(
                "Docker stream demux encountered unexpected header (stream_type=%s); "
                "advancing to resynchronize",
                stream_type,
            )
            buffer = buffer[1:]
            continue

        payload_size = int.from_bytes(buffer[4:8], "big")
        # Sanity-check payload size to avoid OOM on corrupt frames
        if payload_size > 16 * 1024 * 1024:  # 16 MB max
            logger.warning(
                "Docker stream demux encountered invalid payload size %d; "
                "advancing to resynchronize",
                payload_size,
            )
            buffer = buffer[1:]
            continue

        frame_total = 8 + payload_size
        if len(buffer) < frame_total:
            # Incomplete frame, wait for more data from stream
            break

        payload = buffer[8:frame_total]
        buffer = buffer[frame_total:]
        frames.append((stream_type, payload))

    return frames, buffer


def _parse_docker_log_line(raw_bytes: bytes) -> str:
    """
    Parse a Docker log stream frame.

    Docker multiplexed stream format (when tty=false):
    [8-byte header][payload]
    Header: stream_type(1) + padding(3) + size(4 big-endian)

    When tty=true, the stream is raw text with no framing.
    """
    if len(raw_bytes) >= 8:
        stream_type = raw_bytes[0]
        # stream_type: 0=stdin, 1=stdout, 2=stderr
        if stream_type in (0, 1, 2):
            payload_size = int.from_bytes(raw_bytes[4:8], "big")
            payload = raw_bytes[8:8 + payload_size]
            return payload.decode("utf-8", errors="replace").rstrip("\n\r")

    # Fallback: treat entire bytes as text (tty mode)
    return raw_bytes.decode("utf-8", errors="replace").rstrip("\n\r")



_clean_text = clean_log_text
_SEVERITY_LEVEL_MAP = SEVERITY_LEVEL_MAP
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


def _make_log_entry(
    container_name: str,
    container_id: str,
    message: str,
    severity: int = 6,
    timestamp: Optional[str] = None,
    alias_cache: Optional[AliasCache] = None,
    source_ip: str = "docker",
    source_alias: Optional[str] = None,
) -> dict:
    """Build a log entry dict compatible with the shared pipeline."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if source_alias is None:
        try:
            from app.core.config import get_cached_setting
            source_alias = get_cached_setting("docker_source_alias", "docker")
        except Exception:
            source_alias = os.environ.get("DOCKER_SOURCE_ALIAS", source_ip)

    default_alias = source_alias or source_ip
    resolved_alias = default_alias
    if alias_cache is not None:
        if default_alias != source_ip:
            resolved_default = alias_cache.resolve(default_alias)
            if resolved_default != default_alias:
                resolved_alias = resolved_default
            else:
                resolved_source = alias_cache.resolve(source_ip)
                if resolved_source != source_ip:
                    resolved_alias = resolved_source
        else:
            resolved_alias = alias_cache.resolve(source_ip)

    clean_message = _clean_text(message)

    return {
        "timestamp": timestamp or now,
        "received_at": now,
        "source_ip": source_ip,
        "source_alias": resolved_alias,
        "app_name": container_name,
        "facility": 1,
        "severity": severity,
        "message": clean_message,
        "raw": clean_message,
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


def _should_ignore_container(
    container_id: str,
    container_name: str,
    exclude_containers: Optional[str] = None,
) -> bool:
    """
    Determine if a container should be excluded from log tailing.
    Prevents self-tailing loops for LogShed itself.
    """
    # 1. Explicitly configured excluded container names/IDs
    if exclude_containers is None:
        try:
            from app.core.config import get_cached_setting
            exclude_containers = get_cached_setting("docker_exclude_containers", "")
        except Exception:
            exclude_containers = os.environ.get("DOCKER_EXCLUDE_CONTAINERS", "")

    excluded_names = {n.strip().lower() for n in exclude_containers.split(",") if n.strip()}
    # Always exclude default container names for this app
    excluded_names.update({
        "logshed",
        "log-shed",
        "log_shed",
    })

    name_clean = container_name.lower().lstrip("/")
    if name_clean in excluded_names or container_id.lower() in excluded_names or container_id[:12].lower() in excluded_names:
        return True

    # 2. Check container short ID against HOSTNAME (Docker sets container short ID as hostname inside container)
    hostname = os.environ.get("HOSTNAME", "").strip().lower()
    if hostname and (container_id.lower().startswith(hostname) or hostname.startswith(container_id[:12].lower())):
        return True

    return False


async def _get_container_name(client: httpx.AsyncClient, container_id: str) -> str:
    """Inspect a container to get its name."""
    try:
        resp = await client.get(f"/containers/{container_id}/json")
        resp.raise_for_status()
        info = resp.json()
        name = info.get("Name", container_id)
        # Docker names start with '/'
        return name.lstrip("/")
    except Exception:
        return container_id[:12]


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
) -> None:
    """
    Stream logs for a single container, feeding lines through the assembler.

    Requests logs with timestamps=true.
    Tracks latest seen log timestamp and resumes via since=<timestamp> on reconnect.
    Uses proper Docker multiplexed stream frame parsing when tty=false,
    and newline-delimited raw text when tty=true.
    """
    stream_key = f"docker:{container_id}"
    url = f"/containers/{container_id}/logs"

    if heartbeat_interval is None:
        env_hb = os.environ.get("DOCKER_HEARTBEAT_INTERVAL")
        if env_hb is not None:
            try:
                heartbeat_interval = float(env_hb)
            except ValueError:
                heartbeat_interval = 30.0
        else:
            heartbeat_interval = 30.0

    last_seen_timestamp: Optional[str] = (
        container_last_seen.get(container_id) if container_last_seen is not None else None
    )
    seen_at_last_timestamp: set[str] = (
        container_last_messages.get(container_id, set()).copy()
        if container_last_messages is not None
        else set()
    )

    # Detect whether the container is running in TTY mode.
    # TTY mode sends raw text; non-TTY sends multiplexed frames with 8-byte headers.
    is_tty = False
    try:
        inspect_resp = await client.get(f"/containers/{container_id}/json")
        if inspect_resp.status_code == 200:
            config = inspect_resp.json().get("Config", {})
            is_tty = bool(config.get("Tty", False))
    except Exception:
        pass  # Default to multiplexed if inspect fails

    def _process_line(line: str) -> Optional[dict]:
        nonlocal last_seen_timestamp, seen_at_last_timestamp
        ts, message = _extract_docker_timestamp(line)
        if ts:
            if last_seen_timestamp and ts == last_seen_timestamp:
                if message in seen_at_last_timestamp:
                    # Replayed duplicate from Docker due to inclusive 'since' filter
                    return None
                seen_at_last_timestamp.add(message)
                if container_last_messages is not None:
                    container_last_messages[container_id] = seen_at_last_timestamp
            elif last_seen_timestamp and ts < last_seen_timestamp:
                # Older than last seen timestamp
                return None
            else:
                last_seen_timestamp = ts
                seen_at_last_timestamp = {message}
                if container_last_seen is not None:
                    container_last_seen[container_id] = ts
                if container_last_messages is not None:
                    container_last_messages[container_id] = seen_at_last_timestamp

        if message or ts:
            severity = _detect_severity(message)
            return _make_log_entry(
                container_name, container_id, message,
                severity=severity, timestamp=ts,
                alias_cache=alias_cache,
            )
        return None

    backoff = _CONTAINER_INITIAL_BACKOFF
    while not cancel_event.is_set():
        params = {
            "stdout": "true",
            "stderr": "true",
            "follow": "true",
            "timestamps": "true",
        }
        if last_seen_timestamp:
            params["since"] = _iso_to_unix_timestamp(last_seen_timestamp)
        else:
            params["tail"] = "0"

        try:
            async with client.stream("GET", url, params=params) as resp:
                if getattr(resp, "status_code", None) == 400 and "since" in params:
                    logger.warning(
                        f"Docker rejected 'since' parameter for {container_name} ({container_id[:12]}): 400. "
                        f"Resetting last seen timestamp and falling back to tail='0'."
                    )
                    last_seen_timestamp = None
                    if container_last_seen is not None:
                        container_last_seen.pop(container_id, None)
                    continue
                resp.raise_for_status()
                backoff = _CONTAINER_INITIAL_BACKOFF
                buffer = b""

                last_activity = asyncio.get_running_loop().time()
                heartbeat_failed = False

                async def _consume_stream() -> None:
                    nonlocal buffer, last_activity
                    async for chunk in resp.aiter_bytes():
                        if cancel_event.is_set():
                            return
                        last_activity = asyncio.get_running_loop().time()
                        buffer += chunk

                        if is_tty:
                            # TTY mode: raw text, split by newline
                            while b"\n" in buffer:
                                line_bytes, buffer = buffer.split(b"\n", 1)
                                if not line_bytes:
                                    continue
                                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
                                entry = _process_line(line)
                                if entry:
                                    await assembler.feed(stream_key, entry)

                            while len(buffer) > MAX_TTY_BUFFER:
                                logger.warning(
                                    f"Docker TTY buffer exceeded {MAX_TTY_BUFFER} bytes without newline for container {container_name}. Truncating chunk."
                                )
                                chunk_bytes = buffer[:MAX_TTY_BUFFER]
                                buffer = buffer[MAX_TTY_BUFFER:]
                                line = chunk_bytes.decode("utf-8", errors="replace").rstrip("\r")
                                entry = _process_line(line)
                                if entry:
                                    await assembler.feed(stream_key, entry)
                        else:
                            # Multiplexed mode: demux frames with automatic header resynchronization
                            frames, buffer = _demux_stream(buffer)
                            for stream_type, payload in frames:
                                text = payload.decode("utf-8", errors="replace")
                                for line in text.splitlines():
                                    line = line.rstrip("\r")
                                    if not line:
                                        continue
                                    entry = _process_line(line)
                                    if entry:
                                        await assembler.feed(stream_key, entry)

                    # Flush any trailing TTY bytes on clean stream completion
                    if is_tty and buffer:
                        while len(buffer) > MAX_TTY_BUFFER:
                            logger.warning(
                                f"Docker TTY trailing buffer exceeded {MAX_TTY_BUFFER} bytes for container {container_name}. Truncating chunk."
                            )
                            chunk_bytes = buffer[:MAX_TTY_BUFFER]
                            buffer = buffer[MAX_TTY_BUFFER:]
                            line = chunk_bytes.decode("utf-8", errors="replace").rstrip("\r")
                            if line:
                                entry = _process_line(line)
                                if entry:
                                    await assembler.feed(stream_key, entry)
                        line = buffer.decode("utf-8", errors="replace").rstrip("\r")
                        buffer = b""
                        if line:
                            entry = _process_line(line)
                            if entry:
                                await assembler.feed(stream_key, entry)

                stream_task = asyncio.create_task(_consume_stream())

                async def _heartbeat_monitor() -> None:
                    nonlocal heartbeat_failed, last_activity
                    if heartbeat_interval is None or heartbeat_interval <= 0:
                        return
                    while not cancel_event.is_set():
                        try:
                            await asyncio.sleep(min(heartbeat_interval / 2.0, 5.0))
                        except asyncio.CancelledError:
                            return
                        now = asyncio.get_running_loop().time()
                        if now - last_activity >= heartbeat_interval:
                            try:
                                ping_resp = await client.get("/_ping", timeout=5.0)
                                if ping_resp.status_code != 200:
                                    raise httpx.ConnectError(f"Ping returned status {ping_resp.status_code}")
                                last_activity = asyncio.get_running_loop().time()
                            except asyncio.CancelledError:
                                return
                            except Exception as ping_err:
                                logger.warning(
                                    f"Docker keepalive heartbeat failed for {container_name} "
                                    f"({container_id[:12]}): {ping_err}. Triggering reconnect."
                                )
                                heartbeat_failed = True
                                stream_task.cancel()
                                return

                monitor_task = asyncio.create_task(_heartbeat_monitor())
                cancel_task = asyncio.create_task(cancel_event.wait())
                try:
                    done, pending = await asyncio.wait(
                        [stream_task, cancel_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task in done:
                        return
                    if heartbeat_failed:
                        raise httpx.ReadTimeout(
                            f"Docker keepalive heartbeat failed for {container_name}; half-open connection detected"
                        )
                    await stream_task
                except asyncio.CancelledError:
                    if heartbeat_failed:
                        raise httpx.ReadTimeout(
                            f"Docker keepalive heartbeat failed for {container_name}; half-open connection detected"
                        )
                    raise
                finally:
                    if not cancel_task.done():
                        cancel_task.cancel()
                        try:
                            await cancel_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    if not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    monitor_task.cancel()
                    try:
                        await monitor_task
                    except (asyncio.CancelledError, Exception):
                        pass

            # Stream ended normally (EOF)
            if cancel_event.is_set():
                return
            logger.debug(
                f"Log stream ended for container {container_name} ({container_id[:12]}); "
                f"reconnecting in {backoff:.1f}s..."
            )
        except asyncio.CancelledError:
            raise
        except (httpx.RemoteProtocolError, httpx.HTTPError, Exception) as e:
            if cancel_event.is_set():
                return
            logger.debug(
                f"Log stream disconnected for {container_name} ({container_id[:12]}): {e}. "
                f"Reconnecting in {backoff:.1f}s..."
            )

        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=backoff)
            return
        except asyncio.TimeoutError:
            pass

        backoff = min(backoff * _CONTAINER_BACKOFF_FACTOR, _CONTAINER_MAX_BACKOFF)


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
        self._db_path = Path(db_path) if db_path else None
        if alias_cache is not None:
            self.alias_cache = alias_cache
            self._owns_alias_cache = False
        else:
            effective_db_path = self._db_path or get_db_path()
            self.alias_cache = AliasCache(effective_db_path)
            self._owns_alias_cache = True

        from app.core.config import get_cached_setting
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
        self._current_client: Optional[httpx.AsyncClient] = None

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

        base_url, uds_path = _parse_docker_host(self.enable_docker)
        if not base_url or not self.enable_docker:
            logger.info(
                "Docker container tailing disabled by configuration; operating in syslog-only mode."
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

            base_url, uds_path = _parse_docker_host(self.enable_docker)
            if not base_url or not self.enable_docker:
                return

        if uds_path and not os.path.exists(uds_path):
            logger.warning(
                f"Docker socket not found at {uds_path}. Polling for socket before fallback..."
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
                    f"Docker socket not found at {uds_path}. "
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

            base_url, uds_path = _parse_docker_host(self.enable_docker)
            if not base_url:
                await asyncio.sleep(1.0)
                continue

            target_desc = uds_path if uds_path else base_url
            try:
                base_url, uds_path = _parse_docker_host(self.enable_docker)
                target_desc = uds_path if uds_path else base_url
                async with _build_client(base_url, uds_path) as client:
                    self._current_client = client
                    try:
                        # Start tailing all currently running containers
                        await self._attach_running_containers(client)
                        logger.info(f"Connected to Docker daemon at {target_desc}")
                        backoff = _INITIAL_BACKOFF  # Reset on successful connect

                        # Stream Docker events for start/die
                        await self._watch_events(client)
                    finally:
                        self._current_client = None

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    f"Docker connection error at {target_desc}: {e}. Reconnecting in {backoff:.0f}s..."
                )
                await self._cleanup_tailers()
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._cancel_event.set()
        await self._cleanup_tailers()
        if self._owns_alias_cache:
            await self.alias_cache.stop()
        logger.info("Docker tailer stopped")

    async def _attach_running_containers(self, client: httpx.AsyncClient) -> None:
        """Enumerate running containers and start a log tailer for each, skipping excluded containers."""
        containers = await _get_running_containers(client)
        for c in containers:
            cid = c.get("Id", "")
            names = c.get("Names", [])
            name = names[0].lstrip("/") if names else cid[:12]
            if _should_ignore_container(cid, name, exclude_containers=self.exclude_containers):
                logger.debug(f"Skipping tailing self/excluded container {name} ({cid[:12]})")
                continue
            self._start_tailer(client, cid, name)

    def _start_tailer(
        self,
        client: httpx.AsyncClient,
        container_id: str,
        container_name: str,
    ) -> None:
        """Start a log tailer task for a container if not already active and not excluded."""
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
            ),
            name=f"docker-tail-{container_name}",
        )
        self._tailers[container_id] = (task, cancel)
        task.add_done_callback(lambda t: self._tailers.pop(container_id, None))
        logger.info(f"Started tailing container {container_name} ({container_id[:12]})")

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

        try:
            heartbeat_interval = float(os.environ.get("DOCKER_HEARTBEAT_INTERVAL", str(DEFAULT_DOCKER_HEARTBEAT_INTERVAL)))
        except ValueError:
            heartbeat_interval = DEFAULT_DOCKER_HEARTBEAT_INTERVAL

        async with client.stream("GET", "/events", params=params) as resp:
            resp.raise_for_status()
            last_activity = asyncio.get_running_loop().time()
            heartbeat_failed = False

            async def _consume_events() -> None:
                nonlocal last_activity
                async for line in resp.aiter_lines():
                    if self._cancel_event.is_set():
                        return
                    last_activity = asyncio.get_running_loop().time()
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
                    name = attrs.get("name", cid[:12])

                    if action in ("start", "restart"):
                        if _should_ignore_container(cid, name):
                            continue
                        logger.info(f"Container {action}: {name} ({cid[:12]})")
                        if action == "restart":
                            await self._stop_tailer(cid)
                        self._start_tailer(client, cid, name)
                    elif action == "die":
                        logger.info(f"Container died: {name} ({cid[:12]})")
                        await self._stop_tailer(cid)

            events_task = asyncio.create_task(_consume_events())

            async def _events_heartbeat() -> None:
                nonlocal heartbeat_failed, last_activity
                if heartbeat_interval <= 0:
                    return
                while not self._cancel_event.is_set():
                    try:
                        await asyncio.sleep(min(heartbeat_interval / 2.0, 5.0))
                    except asyncio.CancelledError:
                        return
                    now = asyncio.get_running_loop().time()
                    if now - last_activity >= heartbeat_interval:
                        try:
                            ping_resp = await client.get("/_ping", timeout=5.0)
                            if ping_resp.status_code != 200:
                                raise httpx.ConnectError(f"Ping returned status {ping_resp.status_code}")
                            last_activity = asyncio.get_running_loop().time()
                        except asyncio.CancelledError:
                            return
                        except Exception as e:
                            logger.warning(
                                f"Docker events keepalive heartbeat failed: {e}. Reconnecting supervisor."
                            )
                            heartbeat_failed = True
                            events_task.cancel()
                            return

            monitor_task = asyncio.create_task(_events_heartbeat())
            cancel_task = asyncio.create_task(self._cancel_event.wait())
            try:
                done, pending = await asyncio.wait(
                    [events_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done:
                    return
                if heartbeat_failed:
                    raise httpx.ReadTimeout(
                        "Docker events keepalive heartbeat failed; half-open connection detected"
                    )
                await events_task
            except asyncio.CancelledError:
                if heartbeat_failed:
                    raise httpx.ReadTimeout(
                        "Docker events keepalive heartbeat failed; half-open connection detected"
                    )
                raise
            finally:
                if not cancel_task.done():
                    cancel_task.cancel()
                    try:
                        await cancel_task
                    except (asyncio.CancelledError, Exception):
                        pass
                if not events_task.done():
                    events_task.cancel()
                    try:
                        await events_task
                    except (asyncio.CancelledError, Exception):
                        pass
                monitor_task.cancel()
                try:
                    await monitor_task
                except (asyncio.CancelledError, Exception):
                    pass
