"""
Alert Canary Presets for LogShed.

Provides 1-click predefined alert rules for common critical security and system events:
- SSH brute-force attacks (with automated offending IP extraction)
- Web proxy 401/403 authorization floods
- Sudo privilege escalations
- Kernel Out-Of-Memory (OOM) killer terminations

Discovers presets from built-ins and user persistent data directory.
"""

import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_data_dir

logger = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "presets" / "alerts"

# Regex patterns for extracting client and offending IP addresses from log messages
_CONTEXTUAL_IP_REGEX = re.compile(
    r"(?:from|rhost=|client[:=]|source[_-]?ip[:=]|host\s+)\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3}|[a-fA-F0-9:]{3,})",
    re.IGNORECASE,
)
_STANDALONE_IPV4_REGEX = re.compile(r"\b([0-9]{1,3}(?:\.[0-9]{1,3}){3})\b")


def extract_ip_from_message(message: str) -> Optional[str]:
    """
    Extract offending or source IP address from a log message string.

    Evaluates contextual indicators first (e.g., 'from 192.168.1.5', 'client: 10.0.0.2', 'rhost=172.16.0.4')
    preferring routable external IPs over loopback/unspecified addresses.
    """
    if not message:
        return None

    loopback_fallback = None

    # 1. Look for contextual prefix
    for match in _CONTEXTUAL_IP_REGEX.finditer(message):
        candidate = match.group(1).strip()
        try:
            ip_obj = ipaddress.ip_address(candidate)
            if ip_obj.is_loopback or ip_obj.is_unspecified:
                if loopback_fallback is None:
                    loopback_fallback = candidate
                continue
            return candidate
        except ValueError:
            continue

    # 2. Look for standalone IPv4 address (preferring non-loopback/non-unspecified)
    for candidate in _STANDALONE_IPV4_REGEX.findall(message):
        try:
            ip_obj = ipaddress.ip_address(candidate)
            if ip_obj.is_loopback or ip_obj.is_unspecified:
                if loopback_fallback is None:
                    loopback_fallback = candidate
                continue
            return candidate
        except ValueError:
            continue

    return loopback_fallback


def get_alert_presets() -> list[dict[str, Any]]:
    """
    Load alert presets from built-ins and user persistent directory.

    Merges presets: built-ins first, then /data/presets/alerts/*.json.
    User presets override built-ins if they share an ID, or append if new.
    Tagged with is_custom: bool (False for built-ins, True for user presets).
    """
    presets: dict[str, dict[str, Any]] = {}

    # 1. Built-in presets
    if _BUILTIN_DIR.is_dir():
        for file_path in sorted(_BUILTIN_DIR.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "id" in data and "name" in data and "rule_type" in data:
                    data["is_custom"] = False
                    presets[data["id"]] = data
                else:
                    logger.warning("Skipping invalid alert preset file %s: missing required fields", file_path)
            except Exception as e:
                logger.warning("Failed to load built-in alert preset from %s: %s", file_path, e)

    # 2. User persistent directory
    user_dir = Path(get_data_dir()) / "presets" / "alerts"
    if user_dir.is_dir():
        for file_path in sorted(user_dir.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "id" in data and "name" in data and "rule_type" in data:
                    data["is_custom"] = True
                    presets[data["id"]] = data
                else:
                    logger.warning("Skipping invalid user alert preset file %s: missing required fields", file_path)
            except Exception as e:
                logger.warning("Failed to load user alert preset from %s: %s", file_path, e)

    return list(presets.values())


def get_alert_preset_by_id(preset_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a specific alert preset by ID."""
    clean_id = (preset_id or "").strip().lower()
    for preset in get_alert_presets():
        if str(preset.get("id", "")).lower() == clean_id:
            return dict(preset)
    return None


# Backward compatibility aliases
get_security_presets = get_alert_presets
get_security_preset_by_id = get_alert_preset_by_id
