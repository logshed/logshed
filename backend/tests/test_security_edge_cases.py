"""
Security edge cases that the main suites only cover with IP literals or not at all:

- notification redirect targets given as host names (resolved through a patched resolver),
  unresolvable names, localhost names, blocked ports, other schemes and missing hosts;
- the fallbacks of mask_notification_url for URLs Apprise cannot parse;
- the Docker TLS context with a CA, a client certificate and key, host name checks off,
  and bad PEM input (certificates are generated in a fixture).
"""

import datetime
import ipaddress
import os
import socket
import ssl
from unittest.mock import patch

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.collectors import docker_collector
from app.collectors.docker_collector import create_docker_ssl_context
from app.services.notifier import _validate_redirect_url, mask_notification_url


def _install_resolver(monkeypatch, answers: dict[str, list[str]]) -> None:
    """Patch socket.getaddrinfo: each name in answers resolves to its addresses, others fail."""

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            if host not in answers:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            ips = answers[host]
        out = []
        for ip in ips:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, 0, 0, 0) if family == socket.AF_INET6 else (ip, 0)
            out.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return out

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


def _private_targets(allowed: bool):
    def _setting(key, default=None):
        if key == "allow_private_notification_targets":
            return allowed
        return default

    return patch("app.core.config.get_cached_setting", side_effect=_setting)


class TestNotificationRedirectTargets:
    """_validate_redirect_url checks where a notification request is redirected to."""

    @pytest.mark.parametrize(
        "address, word",
        [
            ("169.254.169.254", "metadata"),
            ("fd00:ec2::254", "metadata"),
            ("100.100.100.200", "metadata"),
            ("127.0.0.1", "loopback"),
            ("::1", "loopback"),
        ],
    )
    def test_host_name_resolving_to_a_blocked_address_is_refused(self, monkeypatch, address, word):
        _install_resolver(monkeypatch, {"hooks.example": [address]})
        with _private_targets(True):
            is_safe, err = _validate_redirect_url("https://hooks.example/next")
        assert is_safe is False
        assert word in err.lower()

    def test_any_blocked_address_in_the_answer_is_refused(self, monkeypatch):
        _install_resolver(monkeypatch, {"hooks.example": ["93.184.215.14", "169.254.169.254"]})
        with _private_targets(True):
            is_safe, _ = _validate_redirect_url("https://hooks.example/next")
        assert is_safe is False

    def test_private_address_follows_the_private_targets_setting(self, monkeypatch):
        _install_resolver(monkeypatch, {"gotify.lan": ["192.168.1.20"]})
        with _private_targets(True):
            assert _validate_redirect_url("http://gotify.lan/message") == (True, None)
        with _private_targets(False):
            is_safe, err = _validate_redirect_url("http://gotify.lan/message")
        assert is_safe is False
        assert "private" in err.lower()

    def test_public_host_name_is_allowed(self, monkeypatch):
        _install_resolver(monkeypatch, {"hooks.example": ["93.184.215.14"]})
        with _private_targets(False):
            assert _validate_redirect_url("https://hooks.example/next") == (True, None)

    def test_unresolvable_host_name_is_refused(self, monkeypatch):
        _install_resolver(monkeypatch, {})
        is_safe, err = _validate_redirect_url("https://missing.example/next")
        assert is_safe is False
        assert "unable to resolve" in err.lower()

    @pytest.mark.parametrize("url", ["http://localhost/admin", "http://LOCALHOST./admin", "http://api.localhost:8080/"])
    def test_localhost_names_are_refused_without_a_lookup(self, monkeypatch, url):
        _install_resolver(monkeypatch, {})
        is_safe, err = _validate_redirect_url(url)
        assert is_safe is False
        assert "loopback" in err.lower()

    @pytest.mark.parametrize("port", [2375, 2376])
    def test_docker_ports_are_refused(self, port):
        is_safe, err = _validate_redirect_url(f"http://93.184.215.14:{port}/containers/json")
        assert is_safe is False
        assert str(port) in err

    @pytest.mark.parametrize("url", ["ftp://93.184.215.14/file", "file:///etc/passwd", "gopher://hooks.example/"])
    def test_other_schemes_are_refused(self, url):
        is_safe, err = _validate_redirect_url(url)
        assert is_safe is False
        assert "scheme" in err.lower()

    @pytest.mark.parametrize("url", ["", "http:///path-only"])
    def test_empty_url_or_missing_host_is_refused(self, url):
        is_safe, err = _validate_redirect_url(url)
        assert is_safe is False
        assert err

    def test_requests_refuses_to_follow_a_blocked_redirect(self, monkeypatch):
        """The patched requests redirect hook raises before a blocked Location is followed."""
        _install_resolver(monkeypatch, {"hooks.example": ["93.184.215.14"], "evil.example": ["169.254.169.254"]})
        resp = requests.Response()
        resp.status_code = 302
        resp.url = "https://hooks.example/send"
        resp.headers["location"] = "https://evil.example/latest/meta-data"

        with _private_targets(True):
            with pytest.raises(requests.exceptions.InvalidURL, match="evil.example"):
                requests.Session().get_redirect_target(resp)

            resp.headers["location"] = "/relative/next"
            assert requests.Session().get_redirect_target(resp) == "/relative/next"


class TestNotificationUrlMaskingFallbacks:
    """URLs Apprise cannot parse still have their credentials and paths hidden."""

    def test_unknown_scheme_hides_credentials_and_path(self):
        masked = mask_notification_url("customhook://alice:s3cret@hooks.example:8443/path/token-abc123")
        assert masked == "customhook://***:***@hooks.example:8443/********"
        assert "s3cret" not in masked and "token-abc123" not in masked

    def test_unknown_scheme_without_a_path_keeps_the_host(self):
        assert mask_notification_url("customhook://hooks.example") == "customhook://hooks.example"
        assert mask_notification_url("customhook://bob:pw@hooks.example") == "customhook://***:***@hooks.example"

    def test_url_that_urlsplit_cannot_parse_still_hides_secrets(self):
        masked = mask_notification_url("customhook://user:pw@[broken/secret-path")
        assert masked == "customhook://***:***@[broken/********"
        assert "user:pw" not in masked
        assert "secret-path" not in masked

    def test_text_without_a_scheme_is_fully_masked(self):
        assert mask_notification_url("just-a-bare-token-123") == "********"

    def test_empty_url_masks_to_empty(self):
        assert mask_notification_url("") == ""


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(subject: str, issuer: str, public_key, signing_key, is_ca: bool) -> x509.Certificate:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(issuer))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
        .sign(signing_key, hashes.SHA256())
    )


def _pem_key(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture(scope="module")
def tls_material() -> dict[str, str]:
    """A test CA, a client certificate it signed, the client key, and an unrelated key."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _certificate("LogShed Test CA", "LogShed Test CA", ca_key.public_key(), ca_key, is_ca=True)
    client_key = ec.generate_private_key(ec.SECP256R1())
    client_cert = _certificate("logshed-client", "LogShed Test CA", client_key.public_key(), ca_key, is_ca=False)
    other_key = ec.generate_private_key(ec.SECP256R1())
    return {
        "ca": ca_cert.public_bytes(serialization.Encoding.PEM).decode(),
        "client_cert": client_cert.public_bytes(serialization.Encoding.PEM).decode(),
        "client_key": _pem_key(client_key),
        "other_key": _pem_key(other_key),
    }


@pytest.fixture
def temp_dirs(monkeypatch) -> list[str]:
    """Record the temporary directories create_docker_ssl_context writes the client files to."""
    made: list[str] = []
    real_mkdtemp = docker_collector.tempfile.mkdtemp

    def _recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        made.append(path)
        return path

    monkeypatch.setattr(docker_collector.tempfile, "mkdtemp", _recording_mkdtemp)
    return made


class TestDockerTlsContext:

    def test_ca_only_verifies_the_chain_and_host_name(self, tls_material, temp_dirs):
        ctx = create_docker_ssl_context(ca_cert=tls_material["ca"])
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
        subjects = [dict(item[0] for item in c["subject"]) for c in ctx.get_ca_certs()]
        assert {"commonName": "LogShed Test CA"} in subjects
        assert temp_dirs == []

    def test_host_name_check_off_still_verifies_the_chain(self, tls_material):
        ctx = create_docker_ssl_context(ca_cert=tls_material["ca"], verify_hostname=False)
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_client_certificate_is_loaded_from_private_files_that_are_removed(self, tls_material, temp_dirs, monkeypatch):
        modes: dict[str, int] = {}
        real_load = ssl.SSLContext.load_cert_chain

        def _recording_load(self, certfile, keyfile=None, password=None):
            for path in (certfile, keyfile):
                modes[os.path.basename(path)] = os.stat(path).st_mode & 0o777
            return real_load(self, certfile, keyfile, password)

        monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", _recording_load)

        ctx = create_docker_ssl_context(
            ca_cert=tls_material["ca"],
            client_cert=tls_material["client_cert"],
            client_key=tls_material["client_key"],
        )

        assert isinstance(ctx, ssl.SSLContext)
        assert modes == {"client.crt": 0o600, "client.key": 0o600}
        assert len(temp_dirs) == 1 and not os.path.exists(temp_dirs[0])

    def test_a_certificate_without_its_key_is_not_loaded(self, tls_material, temp_dirs):
        create_docker_ssl_context(ca_cert=tls_material["ca"], client_cert=tls_material["client_cert"], client_key="  ")
        assert temp_dirs == []

    @pytest.mark.parametrize("ca", ["not a certificate", "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"])
    def test_bad_ca_pem_is_an_ssl_error(self, ca):
        with pytest.raises(ssl.SSLError):
            create_docker_ssl_context(ca_cert=ca)

    @pytest.mark.parametrize(
        "cert_field, key_field",
        [("garbage", "client_key"), ("client_cert", "garbage"), ("client_cert", "other_key")],
        ids=["bad certificate", "bad key", "key of another certificate"],
    )
    def test_bad_client_pem_is_an_ssl_error_and_leaves_no_files(self, tls_material, temp_dirs, cert_field, key_field):
        material = {**tls_material, "garbage": "-----BEGIN PRIVATE KEY-----\nnot base64\n-----END PRIVATE KEY-----"}
        with pytest.raises(ssl.SSLError):
            create_docker_ssl_context(client_cert=material[cert_field], client_key=material[key_field])
        assert len(temp_dirs) == 1 and not os.path.exists(temp_dirs[0])
