"""
One address policy (app.core.net_guard) decides which outbound connections are refused:

- Docker hosts and AI provider base URLs: cloud metadata and link-local addresses;
- notification destinations: those, loopback addresses, and private addresses when
  allow_private_notification_targets is off.

Every IPv4 address is also checked in its IPv6 forms (IPv4-mapped, IPv4-compatible, NAT64).
Each policy is checked for IP literals and for a host name that resolves to the address.
The tests patch socket.getaddrinfo and anyio.connect_tcp, so no real connection is opened.
"""

import ipaddress
import json
import logging
import socket
from unittest.mock import MagicMock, patch

import anyio
import httpx
import openai
import pytest

from app.collectors.docker_collector import _build_client
from app.models import AiProviderConfig, SettingsUpdateRequest
from app.services import ai_engine
from app.services.notifier import _check_ip_address_safety, validate_notification_url

PUBLIC_IP = "93.184.215.14"

# address -> (Docker hosts, AI providers, notifications, notifications with private targets off)
ADDRESS_TABLE = {
    "169.254.169.254": (True, True, True, True),
    "::169.254.169.254": (True, True, True, True),
    "::ffff:169.254.169.254": (True, True, True, True),
    "64:ff9b::a9fe:a9fe": (True, True, True, True),
    "100.100.100.200": (True, True, True, True),
    "fd00:ec2::254": (True, True, True, True),
    "fe80::1": (True, True, True, True),
    "127.0.0.1": (False, False, True, True),
    "::1": (False, False, True, True),
    "10.0.0.5": (False, False, False, True),
    "fd12:3456::7": (False, False, False, True),
    PUBLIC_IP: (False, False, False, False),
}
ADDRESSES = list(ADDRESS_TABLE)


def _addrinfo(ip: str, port) -> tuple:
    port_num = int(port) if port not in (None, "") else 0
    if ":" in ip:
        return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port_num, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port_num))


def _install_resolver(monkeypatch, answers: dict[str, str]) -> None:
    """Patch socket.getaddrinfo: each name in answers resolves to its address."""

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        try:
            ipaddress.ip_address(host)
            return [_addrinfo(host, port)]
        except ValueError:
            pass
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [_addrinfo(answers[host], port)]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


class _FakeStream:
    """A minimal anyio byte stream that answers one HTTP request."""

    def __init__(self, response: bytes):
        self._response = response
        self._raw_socket = MagicMock()

    async def send(self, item: bytes) -> None:
        return None

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._response:
            data, self._response = self._response, b""
            return data
        raise anyio.EndOfStream

    async def aclose(self) -> None:
        return None

    def extra(self, attribute, default=None):
        return default


def _http_response(body: bytes, content_type: str = "application/json") -> bytes:
    head = f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n"
    return head.encode("ascii") + body


MODELS_RESPONSE = _http_response(
    json.dumps({"object": "list", "data": [{"id": "llama3", "object": "model", "created": 0, "owned_by": "local"}]}).encode()
)
PING_RESPONSE = _http_response(b"OK", "text/plain")


def _install_connect(monkeypatch, response: bytes) -> list[tuple[str, int]]:
    """Patch anyio.connect_tcp: record the address a socket would be opened to (resolving names as it would)."""
    connected: list[tuple[str, int]] = []

    async def _fake_connect_tcp(remote_host, remote_port, *, local_host=None, **kwargs):
        infos = socket.getaddrinfo(str(remote_host), remote_port, type=socket.SOCK_STREAM)
        connected.append((str(infos[0][4][0]), int(remote_port)))
        return _FakeStream(response)

    monkeypatch.setattr(anyio, "connect_tcp", _fake_connect_tcp)
    return connected


@pytest.fixture
def no_sdk_retry_wait(monkeypatch):
    """The OpenAI SDK retries connection errors after a backoff; tests do not wait it out."""
    monkeypatch.setattr(openai._base_client.BaseClient, "_calculate_retry_timeout", lambda *args, **kwargs: 0)


def _private_targets(allowed: bool):
    def _setting(key, default=None):
        if key == "allow_private_notification_targets":
            return allowed
        return default

    return patch("app.core.config.get_cached_setting", side_effect=_setting)


class TestAddressTable:
    """IP literals, checked with the function each caller uses."""

    @pytest.mark.parametrize("address", ADDRESSES)
    def test_docker_hosts(self, address):
        from app.core.net_guard import is_blocked_docker_address

        assert is_blocked_docker_address(address) is ADDRESS_TABLE[address][0]

    @pytest.mark.parametrize("address", ADDRESSES)
    def test_ai_providers(self, address):
        from app.core.net_guard import AI_PROVIDERS, is_blocked_address

        assert is_blocked_address(address, AI_PROVIDERS) is ADDRESS_TABLE[address][1]

    @pytest.mark.parametrize("address", ADDRESSES)
    def test_notifications(self, address):
        is_safe, err = _check_ip_address_safety(ipaddress.ip_address(address), allow_private=True)
        assert is_safe is not ADDRESS_TABLE[address][2]
        assert (err is None) is is_safe

    @pytest.mark.parametrize("address", ADDRESSES)
    def test_notifications_with_private_targets_off(self, address):
        is_safe, _ = _check_ip_address_safety(ipaddress.ip_address(address), allow_private=False)
        assert is_safe is not ADDRESS_TABLE[address][3]

    def test_metadata_addresses_name_metadata_in_the_notification_error(self):
        for address in ("100.100.100.200", "fd00:ec2::254", "169.254.169.254"):
            _, err = _check_ip_address_safety(ipaddress.ip_address(address), allow_private=True)
            assert "metadata" in err


class TestHostNameResolvingToEachAddress:
    """A host name is checked by the addresses it resolves to."""

    @pytest.mark.parametrize("address", ADDRESSES)
    async def test_docker_host_connect(self, monkeypatch, address):
        _install_resolver(monkeypatch, {"docker.example": address})
        connected = _install_connect(monkeypatch, PING_RESPONSE)

        async with _build_client("http://docker.example:2375/v1.43", None) as client:
            if ADDRESS_TABLE[address][0]:
                with pytest.raises(httpx.ConnectError):
                    await client.get("/_ping")
                assert connected == []
            else:
                resp = await client.get("/_ping")
                assert resp.status_code == 200
                assert connected == [(address, 2375)]

    @pytest.mark.parametrize("address", ADDRESSES)
    async def test_ai_base_url_connect(self, monkeypatch, no_sdk_retry_wait, address):
        _install_resolver(monkeypatch, {"ai.example": address})
        connected = _install_connect(monkeypatch, MODELS_RESPONSE)

        if ADDRESS_TABLE[address][1]:
            with pytest.raises(Exception):
                await ai_engine.fetch_available_models("openai_compatible", base_url="http://ai.example:11434/v1")
            assert connected == []
        else:
            models = await ai_engine.fetch_available_models("openai_compatible", base_url="http://ai.example:11434/v1")
            assert [m["id"] for m in models] == ["llama3"]
            assert connected == [(address, 11434)]

    @pytest.mark.parametrize("address", ADDRESSES)
    def test_notification_url(self, monkeypatch, address):
        _install_resolver(monkeypatch, {"hooks.example": address})
        with _private_targets(True):
            valid, _ = validate_notification_url("json://hooks.example/hook")
        assert valid is not ADDRESS_TABLE[address][2]

    @pytest.mark.parametrize("address", ADDRESSES)
    def test_notification_url_with_private_targets_off(self, monkeypatch, address):
        _install_resolver(monkeypatch, {"hooks.example": address})
        with _private_targets(False):
            valid, _ = validate_notification_url("json://hooks.example/hook")
        assert valid is not ADDRESS_TABLE[address][3]


class TestAiBaseUrl:
    """AI base URLs: refused when saved as a metadata address, checked when connecting."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest",
            "http://[::ffff:169.254.169.254]/v1",
            "http://[fd00:ec2::254]/v1",
            "https://100.100.100.200:8443/v1",
            "http://[fe80::1]:11434/v1",
        ],
    )
    def test_metadata_and_link_local_base_urls_are_refused_when_saved(self, url):
        with pytest.raises(ValueError, match="link-local or cloud metadata"):
            AiProviderConfig(ai_base_url=url)
        with pytest.raises(ValueError, match="link-local or cloud metadata"):
            SettingsUpdateRequest(ai_base_url=url)

    @pytest.mark.parametrize(
        "url",
        ["http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://192.168.1.5:11434/v1", "https://openrouter.ai/api/v1"],
    )
    def test_local_private_and_public_base_urls_are_accepted(self, url):
        assert AiProviderConfig(ai_base_url=url).ai_base_url == url
        assert SettingsUpdateRequest(ai_base_url=url).ai_base_url == url

    async def test_blocked_analysis_names_the_reason_and_logs_the_host(self, monkeypatch, no_sdk_retry_wait, caplog):
        caplog.set_level(logging.ERROR)
        _install_resolver(monkeypatch, {"ai.example": "169.254.169.254"})
        connected = _install_connect(monkeypatch, MODELS_RESPONSE)

        with pytest.raises(Exception, match="link-local or cloud metadata"):
            await ai_engine.call_openai(
                api_key="sk-test", model="llama3", prompt="hello", base_url="http://ai.example:11434/v1"
            )
        assert connected == []
        assert any(
            "ai.example" in rec.getMessage() and "169.254.169.254" in rec.getMessage() for rec in caplog.records
        )

    async def test_follow_up_questions_are_checked_too(self, monkeypatch, no_sdk_retry_wait):
        _install_resolver(monkeypatch, {"ai.example": "fd00:ec2::254"})
        connected = _install_connect(monkeypatch, MODELS_RESPONSE)

        with pytest.raises(Exception):
            await ai_engine.dispatch_multi_turn_openai(
                api_key="sk-test",
                model="llama3",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system",
                base_url="http://ai.example:11434/v1",
            )
        assert connected == []

    async def test_requests_still_go_through_a_configured_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.lan:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        _install_resolver(monkeypatch, {"proxy.lan": "192.168.1.2", "ai.example": PUBLIC_IP})
        connected = _install_connect(monkeypatch, MODELS_RESPONSE)

        models = await ai_engine.fetch_available_models("openai_compatible", base_url="http://ai.example:11434/v1")
        assert [m["id"] for m in models] == ["llama3"]
        assert connected == [("192.168.1.2", 3128)]

    async def test_hosts_in_no_proxy_connect_directly_and_are_checked(self, monkeypatch, no_sdk_retry_wait):
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.lan:3128")
        monkeypatch.setenv("NO_PROXY", "ai.example")
        _install_resolver(monkeypatch, {"proxy.lan": "192.168.1.2", "ai.example": "169.254.169.254"})
        connected = _install_connect(monkeypatch, MODELS_RESPONSE)

        with pytest.raises(Exception):
            await ai_engine.fetch_available_models("openai_compatible", base_url="http://ai.example:11434/v1")
        assert connected == []
