"""
Shared address checks for outbound connections to Docker hosts.

Link-local ranges and cloud metadata service addresses are never valid Docker targets.
Private and loopback addresses stay allowed on purpose: Docker hosts and socket proxies
normally live on the LAN or on this machine.

The API checks a host when it is saved or tested, and GuardedNetworkBackend checks the
address the collector actually connects to, so a DNS answer that changes after the
save-time check (DNS rebinding) cannot reach a blocked address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import ssl
import time
import typing
from typing import Optional

import httpcore
import httpx

logger = logging.getLogger(__name__)

BLOCKED_DOCKER_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
BLOCKED_DOCKER_ADDRESSES = frozenset({
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})


def parse_ip(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Parse an IP literal (dropping any IPv6 zone and unwrapping IPv4-mapped forms)."""
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_blocked_docker_address(host: str) -> bool:
    """True when host is an IP literal in a link-local range or a cloud metadata address."""
    ip = parse_ip(host)
    if ip is None:
        return False
    return ip in BLOCKED_DOCKER_ADDRESSES or any(ip in net for net in BLOCKED_DOCKER_NETWORKS)


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that resolves, checks and then connects to the checked IP.

    connect_tcp resolves the host once, refuses the connection when any returned address
    is blocked, and connects to the checked IP addresses directly, so there is no second
    lookup. httpcore passes the original host name to start_tls separately, so TLS server
    name and certificate host name checks still use the name in the URL.
    """

    def __init__(self, inner: Optional[httpcore.AsyncNetworkBackend] = None) -> None:
        self._inner = inner if inner is not None else httpcore.AnyIOBackend()

    async def _resolve(self, host: str, port: int, timeout: Optional[float]) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"Timed out resolving Docker host {host}") from exc
        except (OSError, UnicodeError) as exc:
            raise httpcore.ConnectError(f"Could not resolve Docker host {host}: {exc}") from exc
        addresses: list[str] = []
        for info in infos:
            addr = str(info[4][0])
            if addr not in addresses:
                addresses.append(addr)
        return addresses

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[typing.Iterable[httpcore.SOCKET_OPTION]] = None,
    ) -> httpcore.AsyncNetworkStream:
        deadline = None if timeout is None else time.monotonic() + timeout
        if parse_ip(host) is not None:
            addresses = [host.strip("[]")]
        else:
            addresses = await self._resolve(host, port, timeout)
        blocked = [addr for addr in addresses if is_blocked_docker_address(addr)]
        if blocked:
            logger.error(
                "Refused to connect to Docker host %s: it resolves to %s, a link-local or cloud "
                "metadata address, which is not allowed.",
                host,
                ", ".join(blocked),
            )
            raise httpcore.ConnectError(
                f"Docker host {host} resolves to a link-local or cloud metadata address"
            )
        if not addresses:
            raise httpcore.ConnectError(f"Could not resolve Docker host {host}")

        last_exc: Optional[Exception] = None
        for addr in addresses:
            remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            try:
                return await self._inner.connect_tcp(
                    addr,
                    port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[typing.Iterable[httpcore.SOCKET_OPTION]] = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose connection pool connects through GuardedNetworkBackend.

    httpx.AsyncHTTPTransport takes no network backend, so this builds the same httpcore
    connection pool with one and keeps httpx's own request handling, exception mapping
    and response streaming.
    """

    def __init__(
        self,
        verify: ssl.SSLContext | bool = True,
        limits: httpx.Limits = httpx.Limits(),
        socket_options: Optional[typing.Iterable[httpcore.SOCKET_OPTION]] = None,
        network_backend: Optional[httpcore.AsyncNetworkBackend] = None,
    ) -> None:
        ssl_context = httpx.create_ssl_context(verify=verify)
        super().__init__(verify=ssl_context, limits=limits, socket_options=socket_options)
        if not isinstance(getattr(self, "_pool", None), httpcore.AsyncConnectionPool):
            # Never fall back to an unguarded pool if httpx changes its internals
            raise RuntimeError("httpx transport layout changed; cannot install the Docker address guard")
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            socket_options=socket_options,
            network_backend=network_backend if network_backend is not None else GuardedNetworkBackend(),
        )
