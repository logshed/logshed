"""
Notification delivery service for LogShed.

Integrates with Apprise to provide universal webhook and alert notifications
across 80+ platforms (Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email, etc.).
Handles URL encryption at rest via LogShed master encryption key, token masking,
test deliveries, and asynchronous dispatch using asyncio.to_thread.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ipaddress
import logging
import os
import re
import socket
import threading
from typing import Optional, Tuple, Union
import urllib.parse
import urllib.request

import apprise

import requests
import requests.sessions

from app.core.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

# Dedicated notification worker pool
_notification_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="logshed-notifier",
)


def get_notification_executor() -> ThreadPoolExecutor:
    """Return the dedicated notification ThreadPoolExecutor instance."""
    global _notification_executor
    if _notification_executor is None or getattr(_notification_executor, "_shutdown", False):
        _notification_executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="logshed-notifier",
        )
    return _notification_executor


def shutdown_notifier_executor(wait: bool = True) -> None:
    """Terminate the dedicated notification ThreadPoolExecutor gracefully."""
    global _notification_executor
    if _notification_executor is not None:
        _notification_executor.shutdown(wait=wait)


# Blocked metadata, loopback, and private IP networks
_METADATA_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
]
_LOOPBACK_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
]
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
]
_IPV4_COMPATIBLE_NETWORK = ipaddress.ip_network("::/96")
_BLOCKED_PORTS = {2375, 2376}
_DANGEROUS_SCHEMES = {"file", "attach"}


def _check_ip_address_safety(
    ip_addr: Union[ipaddress.IPv4Address, ipaddress.IPv6Address],
    allow_private: bool,
) -> Tuple[bool, Optional[str]]:
    """Verify an IP address against blocked metadata, loopback, and private ranges."""
    # Unwrap IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
    if isinstance(ip_addr, ipaddress.IPv6Address) and ip_addr.ipv4_mapped:
        ip_addr = ip_addr.ipv4_mapped

    # Check for IPv4-compatible IPv6 addresses (e.g., ::127.0.0.1, ::169.254.169.254)
    if isinstance(ip_addr, ipaddress.IPv6Address) and ip_addr in _IPV4_COMPATIBLE_NETWORK:
        unwrapped_v4 = ipaddress.IPv4Address(ip_addr.packed[-4:])
        is_safe, err = _check_ip_address_safety(unwrapped_v4, allow_private)
        if not is_safe:
            return False, err

    # 1. Cloud instance metadata
    for net in _METADATA_NETWORKS:
        if ip_addr in net:
            return False, f"Target IP {ip_addr} is within blocked cloud metadata ranges (169.254.0.0/16, fe80::/10)."
    if ip_addr.is_link_local:
        return False, f"Target IP {ip_addr} is within link-local metadata ranges."

    # 2. Container loopback
    for net in _LOOPBACK_NETWORKS:
        if ip_addr in net:
            return False, f"Target IP {ip_addr} is within blocked container loopback ranges (127.0.0.0/8, ::1)."
    if ip_addr.is_loopback or ip_addr.is_unspecified:
        return False, f"Target IP {ip_addr} is a blocked loopback address."

    # 3. Private subnets (blocked if ALLOW_PRIVATE_NOTIFICATION_TARGETS is false)
    if not allow_private:
        for net in _PRIVATE_NETWORKS:
            if ip_addr in net:
                return False, f"Target IP {ip_addr} is within private network ranges and private targets are disabled."
        if ip_addr.is_private:
            return False, f"Target IP {ip_addr} is a private network target and private targets are disabled."

    return True, None


def mask_notification_url(raw_url: str) -> str:
    """
    Mask sensitive secrets, tokens, and passwords in a notification URL for safe client display.

    Uses Apprise's native privacy mode if parseable, with fallback redaction
    and credential scrubbing for arbitrary URLs.
    """
    if not raw_url:
        return ""

    stripped = raw_url.strip()
    try:
        ap_obj = apprise.Apprise()
        if ap_obj.add(stripped):
            masked_urls = [server.url(privacy=True) for server in ap_obj]
            if masked_urls:
                clean_url = masked_urls[0].split("/?")[0] if "/?" in masked_urls[0] else masked_urls[0].split("?")[0]
                return clean_url
    except Exception as e:
        logger.debug(f"Apprise privacy mask fallback: {e}")

    try:
        parsed = urllib.parse.urlsplit(stripped)
        if parsed.scheme:
            netloc = parsed.netloc
            if "@" in netloc:
                _, host_part = netloc.rsplit("@", 1)
                netloc = f"***:***@{host_part}"
            if parsed.path and parsed.path != "/":
                return f"{parsed.scheme}://{netloc}/********"
            elif "/" in stripped.split("://", 1)[-1]:
                return f"{parsed.scheme}://{netloc}/********"
            return f"{parsed.scheme}://{netloc}"
    except Exception as e:
        logger.debug(f"urlsplit mask fallback: {e}")

    if "://" in stripped:
        scheme, remainder = stripped.split("://", 1)
        if "/" in remainder:
            host_part, _ = remainder.split("/", 1)
            if "@" in host_part:
                _, h = host_part.rsplit("@", 1)
                host_part = f"***:***@{h}"
            return f"{scheme}://{host_part}/********"
        if "@" in remainder:
            _, h = remainder.rsplit("@", 1)
            remainder = f"***:***@{h}"
        return f"{scheme}://{remainder}"
    return "********"


def validate_notification_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that a notification URL is supported, syntax-valid according to Apprise,
    and adheres to SSRF protection policies.

    Returns:
        (is_valid, error_message)
    """
    if not url or not url.strip():
        return False, "Notification URL cannot be empty."

    trimmed = url.strip()

    # 1. Scheme checks (disallow dangerous schemes like file:// and attach://)
    try:
        parsed = urllib.parse.urlsplit(trimmed)
    except Exception as exc:
        return False, f"Invalid notification URL syntax: {exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme in _DANGEROUS_SCHEMES or trimmed.lower().startswith(("file://", "attach://", "file:", "attach:")):
        return False, f"Scheme '{scheme}' is not allowed for notification targets."

    # 2. Port checks on URL (standard Docker daemon ports 2375, 2376)
    if parsed.port in _BLOCKED_PORTS:
        return False, f"Port {parsed.port} is blocked to protect container control sockets."

    # 3. Hostname loopback checks
    hostname = (parsed.hostname or "").strip()
    clean_host = hostname.lower().rstrip(".")
    if clean_host == "localhost" or clean_host.endswith(".localhost"):
        return False, "Container loopback target 'localhost' is not allowed."

    # 4. Apprise syntax validation
    try:
        ap_obj = apprise.Apprise()
        added = ap_obj.add(trimmed)
        if not added:
            return False, "Unsupported notification URL schema or invalid format."
    except Exception as exc:
        return False, f"Invalid notification URL: {exc}"

    # Verify server ports on configured Apprise plugin instances
    for server in ap_obj:
        server_port = getattr(server, "port", None)
        if server_port in _BLOCKED_PORTS:
            return False, f"Port {server_port} is blocked to protect container control sockets."

    # 5. Dynamic configuration for private notification targets
    from app.core.config import get_cached_setting
    allow_private = bool(get_cached_setting("allow_private_notification_targets", True))

    # 6. Verify destination IPs against blocked ranges
    hosts_to_verify: list[str] = []
    if hostname:
        clean_ip_str = hostname.strip("[]")
        try:
            ipaddress.ip_address(clean_ip_str)
            hosts_to_verify.append(hostname)
        except ValueError:
            pass

    # Hosts of fixed-endpoint plugins. Many such plugins keep a token or ID in the URL host (for
    # example Discord webhook IDs), but some switch to a self-hosted mode and post to that host
    # (for example notica://host/token). These hosts are checked when they look like a network
    # host, and an unresolvable one is not an error. The connect-time check in
    # _pinned_destination_guard covers whatever the plugin actually connects to.
    fixed_plugin_hosts: list[str] = []

    for server in ap_obj:
        shost = getattr(server, "host", None)
        shost = shost.strip() if isinstance(shost, str) else None

        # If the plugin uses a fixed public cloud endpoint, verify that endpoint host
        cloud_url = getattr(server, "notify_url", None)
        if (
            isinstance(cloud_url, str)
            and (cloud_url.startswith("http://") or cloud_url.startswith("https://"))
            and "{host}" not in cloud_url
        ):
            cloud_host = urllib.parse.urlsplit(cloud_url).hostname
            if cloud_host and cloud_host not in hosts_to_verify:
                hosts_to_verify.append(cloud_host)
            if shost and _looks_like_network_host(shost) and shost not in fixed_plugin_hosts:
                fixed_plugin_hosts.append(shost)
            continue

        if shost and shost not in hosts_to_verify:
            hosts_to_verify.append(shost)

    if not hosts_to_verify and hostname:
        hosts_to_verify.append(hostname)

    for host_target in fixed_plugin_hosts:
        if host_target in hosts_to_verify:
            continue
        is_safe, err = _check_host_target(host_target, allow_private, require_resolution=False)
        if not is_safe:
            return False, err

    for host_target in hosts_to_verify:
        is_safe, err = _check_host_target(host_target, allow_private, require_resolution=True)
        if not is_safe:
            return False, err

    return True, None


def _looks_like_network_host(host: str) -> bool:
    """True for an IP literal, localhost or a dotted name with letters; False for bare tokens and IDs."""
    clean = host.lower().rstrip(".").strip("[]")
    if not clean:
        return False
    if clean == "localhost" or clean.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(clean)
        return True
    except ValueError:
        pass
    return "." in clean and any(ch.isalpha() for ch in clean)


def _check_host_target(
    host_target: str,
    allow_private: bool,
    require_resolution: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Check one destination host (IP literal or resolved name) against the blocked ranges.
    With require_resolution False, a name that does not resolve passes (it cannot be reached).
    """
    clean_target = host_target.lower().rstrip(".").strip("[]")
    if clean_target == "localhost" or clean_target.endswith(".localhost"):
        return False, f"Container loopback target '{host_target}' is not allowed."

    try:
        ip_obj = ipaddress.ip_address(clean_target)
    except ValueError:
        ip_obj = None
    if ip_obj is not None:
        return _check_ip_address_safety(ip_obj, allow_private)

    # Hostname is not an IP literal; resolve via socket.getaddrinfo
    try:
        addr_info = socket.getaddrinfo(clean_target, None)
    except (socket.gaierror, socket.herror, OSError, Exception) as dns_err:
        logger.debug(f"Could not resolve host '{host_target}' during validation: {dns_err}")
        if not require_resolution:
            return True, None
        return False, f"Unable to resolve destination hostname '{host_target}'"
    if not addr_info:
        if not require_resolution:
            return True, None
        return False, f"Unable to resolve destination hostname '{host_target}'"
    for addr in addr_info:
        resolved_ip_str = addr[4][0]
        try:
            resolved_ip = ipaddress.ip_address(resolved_ip_str.strip("[]"))
        except ValueError:
            continue
        is_safe, err = _check_ip_address_safety(resolved_ip, allow_private)
        if not is_safe:
            return False, err
    return True, None


def encrypt_channel_url(url: str) -> str:
    """Encrypt a plaintext notification URL for persistent storage in SQLite."""
    return encrypt_value(url.strip())


def decrypt_channel_url(encrypted_url: str) -> str:
    """Decrypt a stored ciphertext notification URL into plaintext."""
    return decrypt_value(encrypted_url)


def _clean_pushover_message(msg: str) -> str:
    """
    Remove unsupported container tags and redundant line break tags to ensure Pushover
    clients render single-line spacing without blank newline gaps in the app,
    while preserving newline characters so lock screen notifications retain line breaks.
    """
    if not msg:
        return msg

    # Convert headers <h1..6>...</h1..6> to <b>...</b>
    cleaned = re.sub(r"<h[1-6]>(.*?)</h[1-6]>", r"<b>\1</b>\n", msg)

    # Convert <li> to bullet points with single newline
    cleaned = re.sub(r"\s*<li>(.*?)</li>\s*", r"• \1\n", cleaned)

    # Remove container block tags: <ul>, </ul>, <ol>, </ol>, <p>, </p>
    cleaned = re.sub(r"</?(?:ul|ol|p)[^>]*>", "", cleaned)

    # Replace <br />\n, <br>\n, or standalone <br />, <br> with \n
    cleaned = re.sub(r"<br\s*/?>\s*", "\n", cleaned)

    # Collapse 3 or more consecutive newlines down to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def _configure_apprise_servers(ap_obj: apprise.Apprise) -> None:
    """
    Ensure rich formatting (HTML/Markdown) is enabled on notification services
    that support it (such as Pushover, Discord, Gotify, Ntfy) when not explicitly set in the URL.
    For Pushover, enables HTML formatting so bold labels and paragraphs render natively
    rather than showing unrendered raw markdown asterisks, and strips redundant newline gaps.
    """
    for server in ap_obj:
        cls_name = server.__class__.__name__
        if cls_name == "NotifyPushover":
            if server.notify_format == apprise.NotifyFormat.TEXT:
                server.notify_format = apprise.NotifyFormat.HTML
            orig_send = server._send

            def _wrapped_pushover_send(payload, *args, _orig=orig_send, **kwargs):
                if (
                    isinstance(payload, dict)
                    and payload.get("html") == 1
                    and not payload.get("encrypted")
                    and "message" in payload
                ):
                    payload["message"] = _clean_pushover_message(payload["message"])
                return _orig(payload, *args, **kwargs)

            server._send = _wrapped_pushover_send
        elif cls_name in ("NotifyDiscord", "NotifyNtfy", "NotifyGotify"):
            if server.notify_format == apprise.NotifyFormat.TEXT:
                server.notify_format = apprise.NotifyFormat.MARKDOWN


def _validate_redirect_url(redirect_url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that an HTTP redirect target URL adheres to SSRF protection policies.
    Blocks redirects to loopback, private, or cloud metadata addresses.
    """
    if not redirect_url:
        return False, "Redirect destination URL cannot be empty."

    try:
        parsed = urllib.parse.urlsplit(redirect_url.strip())
    except Exception as exc:
        return False, f"Invalid redirect destination URL syntax: {exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Redirect to scheme '{scheme}' is forbidden."

    if parsed.port in _BLOCKED_PORTS:
        return False, f"Redirect destination port {parsed.port} is blocked."

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return False, "Redirect destination URL is missing a hostname."

    clean_host = hostname.lower().rstrip(".").strip("[]")
    if clean_host == "localhost" or clean_host.endswith(".localhost"):
        return False, f"Redirect to loopback target '{hostname}' is not permitted."

    from app.core.config import get_cached_setting
    allow_private = bool(get_cached_setting("allow_private_notification_targets", True))

    try:
        ip_obj = ipaddress.ip_address(clean_host)
        is_safe, err = _check_ip_address_safety(ip_obj, allow_private)
        if not is_safe:
            return False, err
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(clean_host, None)
            if not addr_info:
                return False, f"Unable to resolve redirect destination hostname '{hostname}'"
            for addr in addr_info:
                resolved_ip_str = addr[4][0]
                try:
                    resolved_ip = ipaddress.ip_address(resolved_ip_str.strip("[]"))
                    is_safe, err = _check_ip_address_safety(resolved_ip, allow_private)
                    if not is_safe:
                        return False, err
                except ValueError:
                    continue
        except (socket.gaierror, socket.herror, OSError, Exception) as dns_err:
            return False, f"Unable to resolve redirect destination hostname '{hostname}': {dns_err}"

    return True, None


_orig_get_redirect_target = requests.sessions.SessionRedirectMixin.get_redirect_target


def _safe_get_redirect_target(self, resp):
    location = _orig_get_redirect_target(self, resp)
    if location is None:
        return None

    resolved_target = urllib.parse.urljoin(resp.url, location)
    is_safe, err = _validate_redirect_url(resolved_target)
    if not is_safe:
        raise requests.exceptions.InvalidURL(
            f"Redirect to blocked or private destination '{resolved_target}' is forbidden: {err}"
        )
    return location


# Patch SessionRedirectMixin.get_redirect_target to prevent unsafe outbound redirects
requests.sessions.SessionRedirectMixin.get_redirect_target = _safe_get_redirect_target


# ---------------------------------------------------------------------------
# Connect-time destination pinning (DNS rebinding protection)
# ---------------------------------------------------------------------------
#
# validate_notification_url resolves names once, but Apprise (through requests/urllib3, smtplib
# and similar) resolves them again when it connects, so a DNS answer can change in between.
# socket.getaddrinfo is wrapped once, process-wide. The wrapper only acts in a thread that is
# inside _pinned_destination_guard (a notification dispatch or channel test): there every
# address a lookup returns, which is the address the client then connects to, is checked
# against the same blocklist and the lookup fails if any address is blocked. Other threads,
# including the event loop's resolver threads, are not affected. Apprise runs inside the guard
# with async_mode off so that every send happens in the guarded thread.

_dispatch_guard = threading.local()


class BlockedNotificationDestinationError(OSError):
    """A notification connection resolved to a blocked address at connect time."""


def _port_number(port) -> Optional[int]:
    if isinstance(port, int):
        return port
    if isinstance(port, (str, bytes)):
        try:
            return int(port)
        except ValueError:
            return None
    return None


# HTTP proxies configured by the administrator (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY and their
# lower case forms). requests connects to the proxy instead of the destination, so the lookup
# inside the guard is for the proxy, which whoever writes a notification URL does not control.
# Lookups for a configured proxy's host and port are not blocked. Destinations then rely on the
# static check in validate_notification_url, because the proxy resolves their names. Destinations
# listed in NO_PROXY connect directly, so requests resolves them here and they keep the full
# connect-time check.
_DEFAULT_PROXY_PORTS = {"http": 80, "https": 443}
_SOCKS_DEFAULT_PORT = 1080
_proxy_notice_logged = False
_proxy_notice_lock = threading.Lock()


def _configured_proxy_endpoints() -> frozenset:
    """(host, port) pairs of the proxies requests would use, read from the environment."""
    endpoints = set()
    for scheme, proxy_url in urllib.request.getproxies().items():
        if scheme == "no" or not proxy_url:
            continue
        proxy_url = str(proxy_url).strip()
        if "://" not in proxy_url:
            # requests treats a proxy without a scheme as http://
            proxy_url = f"http://{proxy_url}"
        try:
            parsed = urllib.parse.urlsplit(proxy_url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            continue
        if not host:
            continue
        if port is None:
            proxy_scheme = parsed.scheme.lower()
            port = _DEFAULT_PROXY_PORTS.get(proxy_scheme, _SOCKS_DEFAULT_PORT if proxy_scheme.startswith("socks") else 80)
        endpoints.add((host.lower().strip("[]"), port))
    return frozenset(endpoints)


def _log_proxy_notice_once(endpoints: frozenset) -> None:
    global _proxy_notice_logged
    with _proxy_notice_lock:
        if _proxy_notice_logged:
            return
        _proxy_notice_logged = True
    proxies = ", ".join(sorted(f"{host}:{port}" for host, port in endpoints))
    logger.info(
        f"Notifications use the HTTP proxy configured in the environment ({proxies}). Destinations "
        "reached through the proxy are checked when the channel is saved and before each send, but "
        "DNS rebinding protection for them then depends on the proxy. Destinations listed in NO_PROXY "
        "keep the connect-time check."
    )


def _is_proxy_lookup(host, port_num: Optional[int], endpoints: frozenset) -> bool:
    if not endpoints or port_num is None:
        return False
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    return (host.lower().strip("[]"), port_num) in endpoints


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    results = _system_getaddrinfo(host, port, *args, **kwargs)
    allow_private = getattr(_dispatch_guard, "allow_private", None)
    if allow_private is None:
        return results

    port_num = _port_number(port)
    if _is_proxy_lookup(host, port_num, getattr(_dispatch_guard, "proxy_endpoints", frozenset())):
        # The administrator's proxy, not a destination taken from a notification URL
        return results
    if port_num in _BLOCKED_PORTS:
        logger.warning(f"Blocked notification connection to port {port_num} at connect time.")
        raise BlockedNotificationDestinationError(
            f"Notification destination port {port_num} is blocked to protect container control sockets."
        )
    for res in results:
        ip_str = str(res[4][0]).split("%", 1)[0].strip("[]")
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            is_safe, err = False, f"Notification destination address '{ip_str}' is not an IP address."
        else:
            is_safe, err = _check_ip_address_safety(ip_obj, allow_private)
        if not is_safe:
            logger.warning(f"Blocked notification connection to '{host}' at connect time: {err}")
            raise BlockedNotificationDestinationError(f"Notification destination '{host}' is blocked: {err}")
    return results


_guarded_getaddrinfo._logshed_notification_guard = True  # type: ignore[attr-defined]

if getattr(socket.getaddrinfo, "_logshed_notification_guard", False):
    # Module reloaded: keep the original resolver captured by the first import
    _system_getaddrinfo = socket.getaddrinfo._logshed_system_getaddrinfo  # type: ignore[attr-defined]
else:
    _system_getaddrinfo = socket.getaddrinfo
_guarded_getaddrinfo._logshed_system_getaddrinfo = _system_getaddrinfo  # type: ignore[attr-defined]
socket.getaddrinfo = _guarded_getaddrinfo


@contextmanager
def _pinned_destination_guard():
    """Check every address resolved in this thread against the notification blocklist."""
    from app.core.config import get_cached_setting

    allow_private = bool(get_cached_setting("allow_private_notification_targets", True))
    # Read once per send, as requests reads them for the request it makes
    proxy_endpoints = _configured_proxy_endpoints()
    if proxy_endpoints:
        _log_proxy_notice_once(proxy_endpoints)
    previous = getattr(_dispatch_guard, "allow_private", None)
    previous_proxies = getattr(_dispatch_guard, "proxy_endpoints", frozenset())
    _dispatch_guard.allow_private = allow_private
    _dispatch_guard.proxy_endpoints = proxy_endpoints
    try:
        yield
    finally:
        _dispatch_guard.allow_private = previous
        _dispatch_guard.proxy_endpoints = previous_proxies


def _new_dispatch_apprise() -> apprise.Apprise:
    """Apprise instance that sends from the calling thread, so _pinned_destination_guard applies."""
    return apprise.Apprise(asset=apprise.AppriseAsset(async_mode=False))


# ---------------------------------------------------------------------------
# Log scrubbing for Apprise errors
# ---------------------------------------------------------------------------

_URL_IN_TEXT_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>]+")
# urllib3 and requests errors name the request path without the scheme ("with url: /path?query")
_URL_PATH_IN_TEXT_RE = re.compile(r"(\burl: )\S+", re.IGNORECASE)


def _scrub_error_text(exc: BaseException) -> str:
    """Exception text safe to log: URLs masked, request paths hidden, secrets redacted."""
    from app.core.redactor import redact

    text = f"{type(exc).__name__}: {exc}"
    text = _URL_IN_TEXT_RE.sub(lambda m: mask_notification_url(m.group(0)), text)
    text = _URL_PATH_IN_TEXT_RE.sub(r"\1/********", text)
    return redact(text)


def _sync_send_notification(
    urls: list[str],
    title: str,
    body: str,
    body_format: str = apprise.NotifyFormat.MARKDOWN,
) -> bool:
    """
    Synchronous Apprise dispatch function executed in a worker thread via asyncio.to_thread.
    Passes body_format (defaults to NotifyFormat.MARKDOWN) so services like Pushover, Slack,
    Discord, and Telegram receive rich formatted text (HTML/Markdown) rather than raw text.
    """
    if not urls:
        logger.debug("No notification URLs provided to dispatch.")
        return False

    # Enforce current security policies at dispatch time
    safe_urls: list[str] = []
    for u in urls:
        is_valid, err = validate_notification_url(u)
        if is_valid:
            safe_urls.append(u)
        else:
            logger.warning(
                f"Skipping notification URL '{mask_notification_url(u)}' at dispatch time: {err}"
            )

    if not safe_urls:
        logger.warning("No notification URLs passed security validation at dispatch time.")
        return False

    try:
        ap_obj = _new_dispatch_apprise()
        for u in safe_urls:
            ap_obj.add(u)
        _configure_apprise_servers(ap_obj)

        with _pinned_destination_guard():
            success = ap_obj.notify(
                title=title,
                body=body,
                body_format=body_format,
            )
        return bool(success)
    except Exception as exc:
        logger.error(f"Failed to dispatch notification: {_scrub_error_text(exc)}")
        return False


def _sync_test_channel(
    url: str,
    title: str,
    body: str,
    body_format: str = apprise.NotifyFormat.MARKDOWN,
) -> Tuple[bool, str]:
    """
    Synchronously test a single notification URL via Apprise in a worker thread.
    """
    # Enforce current security policies at dispatch time
    is_valid, err = validate_notification_url(url)
    if not is_valid:
        return False, err or "Invalid or disallowed notification URL."

    try:
        ap_obj = _new_dispatch_apprise()
        added = ap_obj.add(url.strip())
        if not added:
            return False, "Failed to initialize notification target. Please check URL syntax."
        _configure_apprise_servers(ap_obj)

        with _pinned_destination_guard():
            success = ap_obj.notify(
                title=title,
                body=body,
                body_format=body_format,
            )
        if success:
            return True, "Notification sent successfully."
        else:
            return False, "Notification service rejected delivery. Please verify webhook credentials or token permissions."
    except Exception as exc:
        # No exc_info: the stored internal logs would include the raw traceback text
        logger.error(f"Notification delivery test failed: {_scrub_error_text(exc)}")
        return False, "Notification test delivery failed due to a connection or server error. Please verify host connectivity and credentials."




class NotifierService:
    """
    Notification service managing channel decryption, testing, and async dispatches.
    """

    def __init__(self, db_path=None):
        self._db_path = db_path

    async def test_channel(
        self,
        url: str,
        title: str = "LogShed Notification Test",
        body: str = "**LogShed Test:** Connection successful! LogShed is configured to send alerts to this channel.",
        body_format: str = apprise.NotifyFormat.MARKDOWN,
    ) -> Tuple[bool, str]:
        """
        Deliver an immediate test notification to verify connectivity.
        """
        # validate_notification_url resolves DNS, so it must not run on the event loop
        valid, err = await asyncio.to_thread(validate_notification_url, url)
        if not valid:
            return False, err or "Invalid notification URL."

        return await asyncio.to_thread(_sync_test_channel, url, title, body, body_format)

    async def send_notification(
        self,
        title: str,
        body: str,
        channel_id: Optional[int] = None,
        body_format: str = apprise.NotifyFormat.MARKDOWN,
    ) -> bool:
        """
        Dispatch a notification to enabled channels (or a specific channel ID).
        """
        from pathlib import Path
        from app.api.deps import run_db_query
        from app.core.config import get_db_path

        db_path = self._db_path or get_db_path()

        def _fetch_channel_urls(conn) -> list[str]:
            cur = conn.cursor()
            if channel_id is not None:
                cur.execute(
                    "SELECT url FROM notification_channels WHERE id = ? AND is_enabled = 1",
                    (channel_id,),
                )
            else:
                cur.execute(
                    "SELECT url FROM notification_channels WHERE is_enabled = 1"
                )
            rows = cur.fetchall()
            decrypted_urls = []
            for r in rows:
                encrypted_url = r["url"] if hasattr(r, "keys") else r[0]
                try:
                    plain_url = decrypt_channel_url(encrypted_url)
                    if plain_url:
                        decrypted_urls.append(plain_url)
                except Exception as e:
                    logger.error(f"Failed to decrypt notification channel URL: {e}")
            return decrypted_urls

        try:
            urls = await run_db_query(_fetch_channel_urls, custom_db_path=Path(db_path))
        except Exception as exc:
            logger.error(f"Error querying notification channels: {exc}")
            return False

        if not urls:
            logger.debug("No active notification channels found for dispatch.")
            return False

        loop = asyncio.get_running_loop()
        executor = _notification_executor if (_notification_executor is not None and not getattr(_notification_executor, "_shutdown", False)) else get_notification_executor()
        return await loop.run_in_executor(
            executor,
            _sync_send_notification,
            urls,
            title,
            body,
            body_format,
        )

    def shutdown(self, wait: bool = True) -> None:
        """Terminate the notification worker pool gracefully."""
        shutdown_notifier_executor(wait=wait)


_notifier_instance: Optional[NotifierService] = None


def get_notifier() -> NotifierService:
    """Return the global NotifierService singleton instance."""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = NotifierService()
    return _notifier_instance
