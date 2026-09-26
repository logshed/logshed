"""
Notification delivery service for LogShed.

Integrates with Apprise to provide universal webhook and alert notifications
across 80+ platforms (Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email, etc.).
Handles URL encryption at rest via LogShed master encryption key, token masking,
test deliveries, and asynchronous dispatch using asyncio.to_thread.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import logging
import os
import socket
from typing import Optional, Tuple, Union
import urllib.parse

import apprise

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
    if hostname:
        try:
            ip_obj = ipaddress.ip_address(hostname)
            is_safe, err = _check_ip_address_safety(ip_obj, allow_private)
            if not is_safe:
                return False, err
        except ValueError:
            # Hostname is not an IP literal; resolve via socket.getaddrinfo
            try:
                addr_info = socket.getaddrinfo(hostname, None)
                for addr in addr_info:
                    resolved_ip_str = addr[4][0]
                    try:
                        resolved_ip = ipaddress.ip_address(resolved_ip_str)
                        is_safe, err = _check_ip_address_safety(resolved_ip, allow_private)
                        if not is_safe:
                            return False, err
                    except ValueError:
                        continue
            except (socket.gaierror, socket.herror, OSError) as dns_err:
                logger.debug(f"Could not resolve host '{hostname}' during validation: {dns_err}")
                if not allow_private and (clean_host.endswith(".local") or clean_host.endswith(".internal") or clean_host.endswith(".lan")):
                    return False, f"Local host '{hostname}' is not permitted when private targets are disabled."

    return True, None


def encrypt_channel_url(url: str) -> str:
    """Encrypt a plaintext notification URL for persistent storage in SQLite."""
    return encrypt_value(url.strip())


def decrypt_channel_url(encrypted_url: str) -> str:
    """Decrypt a stored ciphertext notification URL into plaintext."""
    return decrypt_value(encrypted_url)


def _configure_apprise_servers(ap_obj: apprise.Apprise) -> None:
    """
    Ensure rich formatting (HTML/Markdown) is enabled on notification services
    that support it (such as Pushover, Discord, Gotify, Ntfy) when not explicitly set in the URL.
    For Pushover, enables HTML formatting so bold labels and paragraphs render natively
    rather than showing unrendered raw markdown asterisks.
    """
    for server in ap_obj:
        cls_name = server.__class__.__name__
        if cls_name == "NotifyPushover":
            if server.notify_format == apprise.NotifyFormat.TEXT:
                server.notify_format = apprise.NotifyFormat.HTML
        elif cls_name in ("NotifyDiscord", "NotifyNtfy", "NotifyGotify"):
            if server.notify_format == apprise.NotifyFormat.TEXT:
                server.notify_format = apprise.NotifyFormat.MARKDOWN


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

    try:
        ap_obj = apprise.Apprise()
        for u in urls:
            ap_obj.add(u)
        _configure_apprise_servers(ap_obj)

        success = ap_obj.notify(
            title=title,
            body=body,
            body_format=body_format,
        )
        return bool(success)
    except Exception as exc:
        logger.error(f"Failed to dispatch notification: {exc}")
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
    try:
        ap_obj = apprise.Apprise()
        added = ap_obj.add(url.strip())
        if not added:
            return False, "Failed to initialize notification target. Please check URL syntax."
        _configure_apprise_servers(ap_obj)

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
        logger.error(f"Notification delivery test failed: {exc}", exc_info=True)
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
        valid, err = validate_notification_url(url)
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
