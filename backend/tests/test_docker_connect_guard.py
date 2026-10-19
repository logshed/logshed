"""
N05-1: Docker host addresses are checked when the collector (and the "Test connection"
client) actually connects, not only when a host is saved, so a DNS answer that changes
after the save-time check (DNS rebinding) cannot reach a link-local or metadata address.

The tests patch socket.getaddrinfo (the resolver both the save-time check and httpx use)
and anyio.connect_tcp (the call httpcore's network backend makes to open a socket), so no
real network connection is opened.
"""

import ipaddress
import logging
import socket
import ssl
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import anyio.streams.tls
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.collectors.docker_collector import _build_client, create_docker_ssl_context
from app.core import pipeline as pipeline_mod
from app.core.api_tokens import _last_used_timestamps, api_token_rate_limiter
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.main import create_app

PING_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
METADATA_IP = "169.254.169.254"


class _FakeAnyioStream:
    """A minimal anyio byte stream that answers one HTTP request."""

    def __init__(self, response: bytes):
        self._response = response
        self.sent = b""
        self._raw_socket = MagicMock()

    async def send(self, item: bytes) -> None:
        self.sent += item

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._response:
            data, self._response = self._response, b""
            return data
        raise anyio.EndOfStream

    async def aclose(self) -> None:
        return None

    def extra(self, attribute, default=None):
        return default


def _install_resolver(monkeypatch, answers: dict[str, list[list[str]]]) -> dict[str, int]:
    """Patch socket.getaddrinfo: each name returns the next answer in its list."""
    calls: dict[str, int] = {}

    def _fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        port_num = int(port) if port not in (None, "") else 0
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            if host not in answers:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            n = calls.get(host, 0)
            calls[host] = n + 1
            seq = answers[host]
            ips = seq[min(n, len(seq) - 1)]
        out = []
        for ip in ips:
            if ":" in ip:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port_num, 0, 0)))
            else:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port_num)))
        return out

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    return calls


def _install_connect(monkeypatch) -> list[tuple[str, int]]:
    """Patch anyio.connect_tcp: record the address a socket would be opened to.

    Like the real call, a host name is resolved here, so code that hands httpcore a
    name (and lets it resolve again) shows up with the address it would really reach.
    """
    connected: list[tuple[str, int]] = []

    async def _fake_connect_tcp(remote_host, remote_port, *, local_host=None, **kwargs):
        infos = socket.getaddrinfo(str(remote_host), remote_port, type=socket.SOCK_STREAM)
        ip = str(infos[0][4][0])
        connected.append((ip, int(remote_port)))
        return _FakeAnyioStream(PING_RESPONSE)

    monkeypatch.setattr(anyio, "connect_tcp", _fake_connect_tcp)
    return connected


class TestDockerConnectTimeGuard:
    """The collector's client checks the address it actually connects to."""

    async def test_rebinding_name_to_metadata_is_refused_at_connect(self, monkeypatch, caplog):
        caplog.set_level(logging.ERROR)
        _install_resolver(monkeypatch, {"docker.example": [[METADATA_IP]]})
        connected = _install_connect(monkeypatch)

        async with _build_client("http://docker.example:2375/v1.43", None) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("/_ping")

        assert all(ip != METADATA_IP for ip, _ in connected)
        assert connected == []
        assert any(
            "docker.example" in rec.getMessage() and METADATA_IP in rec.getMessage()
            for rec in caplog.records
        )

    async def test_any_blocked_address_in_the_answer_is_refused(self, monkeypatch):
        _install_resolver(monkeypatch, {"docker.example": [["203.0.113.10", METADATA_IP]]})
        connected = _install_connect(monkeypatch)

        async with _build_client("http://docker.example:2375/v1.43", None) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("/_ping")
        assert connected == []

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254:2375/v1.43",
            "http://[fe80::1]:2375/v1.43",
            "http://100.100.100.200:2375/v1.43",
            "http://[::ffff:169.254.169.254]:2375/v1.43",
        ],
    )
    async def test_blocked_ip_literal_is_refused_at_connect(self, monkeypatch, url):
        """DOCKER_HOST from the environment never passes the API check, so the connect check covers it."""
        _install_resolver(monkeypatch, {})
        connected = _install_connect(monkeypatch)

        async with _build_client(url, None) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("/_ping")
        assert connected == []

    @pytest.mark.parametrize(
        "host, ip",
        [("nas.lan", "192.168.1.20"), ("localhost", "127.0.0.1"), ("10.0.0.5", "10.0.0.5")],
    )
    async def test_private_and_loopback_hosts_still_connect(self, monkeypatch, host, ip):
        _install_resolver(monkeypatch, {"nas.lan": [["192.168.1.20"]], "localhost": [["127.0.0.1"]]})
        connected = _install_connect(monkeypatch)

        async with _build_client(f"http://{host}:2375/v1.43", None) as client:
            resp = await client.get("/_ping")
        assert resp.status_code == 200
        assert resp.text == "OK"
        assert connected == [(ip, 2375)]

    async def test_tls_uses_the_original_host_name(self, monkeypatch):
        _install_resolver(monkeypatch, {"docker.lan": [["10.0.0.7"]]})
        connected = _install_connect(monkeypatch)
        seen: dict = {}

        async def _fake_wrap(stream, *, ssl_context=None, hostname=None, **kwargs):
            seen["hostname"] = hostname
            seen["ssl_context"] = ssl_context
            raise ssl.SSLError("handshake refused by test")

        monkeypatch.setattr(anyio.streams.tls.TLSStream, "wrap", _fake_wrap)
        ctx = create_docker_ssl_context(verify_hostname=True)

        async with _build_client("https://docker.lan:2376/v1.43", None, ssl_context=ctx) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("/_ping")

        assert connected == [("10.0.0.7", 2376)]
        assert seen["hostname"] == "docker.lan"
        assert seen["ssl_context"] is ctx
        assert ctx.check_hostname is True

    async def test_unresolvable_name_is_a_connect_error(self, monkeypatch):
        _install_resolver(monkeypatch, {})
        connected = _install_connect(monkeypatch)

        async with _build_client("http://missing.lan:2375/v1.43", None) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("/_ping")
        assert connected == []


@pytest.fixture
def api_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    reset_crypto_cache()
    _last_used_timestamps.clear()
    db_file = tmp_path / "docker_guard_test.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)
    run_migrations(db_file)
    get_or_create_master_key(key_file)
    yield
    pipeline_mod._log_queue = None
    login_rate_limiter.reset()
    api_token_rate_limiter.reset()
    _last_used_timestamps.clear()


@pytest_asyncio.fixture
async def api_client(api_env):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def session_headers():
    token = create_session_token(user_id=1)
    return {"Cookie": f"{SESSION_COOKIE_NAME}={token}", "x-requested-with": "XMLHttpRequest"}


class TestDockerTestConnectionRebinding:
    """The "Test connection" client gets the same connect-time check."""

    async def test_name_that_rebinds_after_the_check_is_refused(self, monkeypatch, api_client, session_headers):
        # First answer (save-time check) is public, the second (connect) is the metadata address
        _install_resolver(monkeypatch, {"rebind.example": [["203.0.113.10"], [METADATA_IP]]})
        connected = _install_connect(monkeypatch)

        res = await api_client.post(
            "/api/docker/hosts/test", json={"url": "tcp://rebind.example:2375"}, headers=session_headers
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert body["error"] == "Connection test failed: connection refused."
        assert all(ip != METADATA_IP for ip, _ in connected)

    async def test_private_lan_host_test_connection_succeeds(self, monkeypatch, api_client, session_headers):
        _install_resolver(monkeypatch, {"nas.lan": [["192.168.1.20"]]})
        connected = _install_connect(monkeypatch)

        res = await api_client.post(
            "/api/docker/hosts/test", json={"url": "tcp://nas.lan:2375"}, headers=session_headers
        )
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert ("192.168.1.20", 2375) in connected


class TestSharedDockerAddressCheck:
    """The save-time API check and the connect-time check use one shared function."""

    @pytest.mark.parametrize(
        "host",
        ["169.254.169.254", "169.254.1.1", "fe80::1", "fe80::1%eth0", "100.100.100.200",
         "fd00:ec2::254", "::ffff:169.254.169.254", "[fe80::2]"],
    )
    def test_blocked(self, host):
        from app.core.net_guard import is_blocked_docker_address

        assert is_blocked_docker_address(host) is True

    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "::1", "192.168.1.20", "10.0.0.5", "172.17.0.1", "203.0.113.10", "docker.lan"]
    )
    def test_allowed(self, host):
        from app.core.net_guard import is_blocked_docker_address

        assert is_blocked_docker_address(host) is False

    def test_api_module_uses_the_shared_function(self):
        from app.api import docker_hosts
        from app.core import net_guard

        assert docker_hosts.is_blocked_docker_address is net_guard.is_blocked_docker_address
