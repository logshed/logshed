"""
Backward-compatibility bridge for alert presets.

Re-exports alert preset utilities under legacy security preset identifiers.
"""

from app.services.alert_presets import (
    extract_ip_from_message,
    get_alert_preset_by_id,
    get_alert_presets,
    get_security_preset_by_id,
    get_security_presets,
)

__all__ = [
    "extract_ip_from_message",
    "get_alert_preset_by_id",
    "get_alert_presets",
    "get_security_preset_by_id",
    "get_security_presets",
]
