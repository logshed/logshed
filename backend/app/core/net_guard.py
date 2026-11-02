"""
Shared address checks for outbound connections (SSRF protection).

classify_address knows every address class LogShed treats as special: cloud metadata service
addresses, link-local, loopback and private ranges, also when an IPv4 address is wrapped in an
IPv6 form (IPv4-mapped, IPv4-compatible or NAT64). Each kind of outbound connection has an
AddressPolicy naming the classes it refuses:

- Docker hosts and AI provider base URLs refuse cloud metadata and link-local addresses.
  Private and loopback addresses stay allowed on purpose: Docker hosts, socket proxies and
  local models (Ollama, LM Studio) normally live on the LAN or on this machine.
- Notification destinations also refuse loopback addresses and Docker's ports, and private
  addresses when allow_private_notification_targets is off (services/notifier.py).

GuardedNetworkBackend checks the address the Docker collector and the OpenAI SDK client
actually connect to, so a DNS answer that changes after a save-time check (DNS rebinding)
cannot reach a blocked address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import ssl
import time
import typing
from dataclasses import dataclass
from types import ModuleType
from typing import Optional

import httpcore
import httpcore2
import httpx
import httpx2
from openai import DefaultAsyncHttpxClient

logger = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

CLOUD_METADATA = "cloud metadata"
LINK_LOCAL = "link-local"
LOOPBACK = "loopback"
PRIVATE = "private"

# 169.254.169.254: AWS, Google Cloud, Azure, Oracle Cloud, DigitalOcean and others. The other
# two are outside the link-local ranges: fd00:ec2::254 is AWS over IPv6 (inside the private
# fc00::/7) and 100.100.100.200 is Alibaba Cloud.
CLOUD_METADATA_ADDRESSES = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
})
_THIS_NETWORK = ipaddress.ip_network("0.0.0.0/8")
_IPV4_COMPATIBLE_NETWORK = ipaddress.ip_network("::/96")
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")


@dataclass(frozen=True)
class AddressPolicy:
    """The address classes and ports one kind of outbound connection refuses."""

    target: str
    blocked_classes: frozenset[str]
    blocked_ports: frozenset[int] = frozenset()


DOCKER_HOSTS = AddressPolicy("Docker host", frozenset({CLOUD_METADATA, LINK_LOCAL}))
AI_PROVIDERS = AddressPolicy("AI provider", frozenset({CLOUD_METADATA, LINK_LOCAL}))
NOTIFICATION_TARGETS = AddressPolicy(
    "notification destination",
    frozenset({CLOUD_METADATA, LINK_LOCAL, LOOPBACK}),
    frozenset({2375, 2376}),
)
PUBLIC_NOTIFICATION_TARGETS = AddressPolicy(
    "notification destination",
    NOTIFICATION_TARGETS.blocked_classes | {PRIVATE},
    NOTIFICATION_TARGETS.blocked_ports,
)


def notification_policy(allow_private: bool) -> AddressPolicy:
    """The notification policy for the allow_private_notification_targets setting."""
    return NOTIFICATION_TARGETS if allow_private else PUBLIC_NOTIFICATION_TARGETS


def unwrap_ipv4(ip: IPAddress) -> IPAddress:
    """The IPv4 address inside an IPv4-mapped, IPv4-compatible or NAT64 IPv6 address; ip otherwise."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return ip
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    # :: and ::1 are the IPv6 unspecified and loopback addresses, not IPv4-compatible forms
    if (ip in _IPV4_COMPATIBLE_NETWORK and int(ip) > 1) or ip in _NAT64_NETWORK:
        return ipaddress.IPv4Address(ip.packed[-4:])
    return ip


def parse_ip(host: str) -> Optional[IPAddress]:
    """Parse an IP literal (dropping brackets and any IPv6 zone, unwrapping IPv4 in IPv6 forms)."""
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    return unwrap_ipv4(ip)


def classify_address(ip: IPAddress) -> Optional[str]:
    """CLOUD_METADATA, LINK_LOCAL, LOOPBACK or PRIVATE (the first that applies), or None for other addresses."""
    ip = unwrap_ipv4(ip)
    if ip in CLOUD_METADATA_ADDRESSES:
        return CLOUD_METADATA
    if ip.is_link_local:
        return LINK_LOCAL
    if ip.is_loopback or ip.is_unspecified or ip in _THIS_NETWORK:
        return LOOPBACK
    if ip.is_private:
        return PRIVATE
    return None


def blocked_address_class(ip: IPAddress, policy: AddressPolicy) -> Optional[str]:
    """The class of ip when policy refuses it, otherwise None."""
    address_class = classify_address(ip)
    return address_class if address_class in policy.blocked_classes else None


def is_blocked_address(host: str, policy: AddressPolicy) -> bool:
    """True when host is an IP literal that policy refuses (host names are not resolved)."""
    ip = parse_ip(host)
    return ip is not None and blocked_address_class(ip, policy) is not None


def is_blocked_docker_address(host: str) -> bool:
    """True when host is an IP literal in a link-local range or a cloud metadata address."""
    return is_blocked_address(host, DOCKER_HOSTS)


class GuardedNetworkBackend:
    """httpcore network backend that resolves, checks and then connects to the checked IP.

    connect_tcp resolves the host once, refuses the connection when policy blocks any
    returned address, and connects to the checked IP addresses directly, so there is no
    second lookup. httpcore passes the original host name to start_tls separately, so TLS
    server name and certificate host name checks still use the name in the URL.

    core is the httpcore module of the HTTP client using the backend: httpcore for httpx,
    httpcore2 for httpx2 (which the OpenAI SDK uses). Its errors are raised, so the client
    maps them to its own connection errors.
    """

    def __init__(
        self,
        policy: AddressPolicy = DOCKER_HOSTS,
        inner: Optional[typing.Any] = None,
        core: ModuleType = httpcore,
    ) -> None:
        self._policy = policy
        self._core = core
        self._inner = inner if inner is not None else core.AnyIOBackend()

    async def _resolve(self, host: str, port: int, timeout: Optional[float]) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise self._core.ConnectTimeout(f"Timed out resolving {self._policy.target} {host}") from exc
        except (OSError, UnicodeError) as exc:
            raise self._core.ConnectError(f"Could not resolve {self._policy.target} {host}: {exc}") from exc
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
        socket_options: Optional[typing.Iterable[typing.Any]] = None,
    ) -> typing.Any:
        deadline = None if timeout is None else time.monotonic() + timeout
        if parse_ip(host) is not None:
            addresses = [host.strip("[]")]
        else:
            addresses = await self._resolve(host, port, timeout)
        blocked = [addr for addr in addresses if is_blocked_address(addr, self._policy)]
        if blocked:
            logger.error(
                "Refused to connect to %s %s: it resolves to %s, a link-local or cloud "
                "metadata address, which is not allowed.",
                self._policy.target,
                host,
                ", ".join(blocked),
            )
            raise self._core.ConnectError(
                f"{self._policy.target} {host} resolves to a link-local or cloud metadata address"
            )
        if not addresses:
            raise self._core.ConnectError(f"Could not resolve {self._policy.target} {host}")

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
            except (self._core.ConnectError, self._core.ConnectTimeout) as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[typing.Iterable[typing.Any]] = None,
    ) -> typing.Any:
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


class GuardedOpenAIHttpClient(DefaultAsyncHttpxClient):
    """The OpenAI SDK's default HTTP client, with direct connections checked against AI_PROVIDERS.

    Only the direct transport is replaced. Proxies from HTTP_PROXY, HTTPS_PROXY and ALL_PROXY
    keep working as before; a request sent through a proxy is not checked at connect time,
    because the proxy resolves the name. Hosts listed in NO_PROXY connect directly and are checked.
    """

    def _init_transport(
        self,
        verify: ssl.SSLContext | str | bool = True,
        cert: typing.Any = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx2.Limits = httpx2.Limits(),
        transport: Optional[httpx2.AsyncBaseTransport] = None,
    ) -> httpx2.AsyncBaseTransport:
        if transport is not None:
            return transport
        ssl_context = httpx2.create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        guarded = httpx2.AsyncHTTPTransport(verify=ssl_context, http1=http1, http2=http2, limits=limits)
        if not isinstance(getattr(guarded, "_pool", None), httpcore2.AsyncConnectionPool):
            # Never fall back to an unguarded pool if httpx2 changes its internals
            raise RuntimeError("httpx2 transport layout changed; cannot install the AI provider address guard")
        guarded._pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            network_backend=GuardedNetworkBackend(AI_PROVIDERS, core=httpcore2),
        )
        return guarded
