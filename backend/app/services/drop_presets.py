"""
Log Drop Rule Presets for LogShed.

Provides 1-click predefined drop rules for common high-volume syslog and container chatter.
Discovers presets from application built-in directory and user data directory.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_data_dir

logger = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "presets" / "drops"


def get_drop_presets() -> list[dict[str, Any]]:
    """
    Load drop rule presets from built-ins and user persistent directory.

    Merges presets: built-ins first, then /data/presets/drops/*.json.
    User presets override built-ins if they share an ID, or append if new.
    Tagged with is_custom: bool (False for built-ins, True for user presets).
    """
    presets: dict[str, dict[str, Any]] = {}

    # 1. Built-in presets
    if _BUILTIN_DIR.is_dir():
        for file_path in sorted(_BUILTIN_DIR.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "id" in data and "name" in data and "message_pattern" in data:
                    data["is_custom"] = False
                    presets[data["id"]] = data
                else:
                    logger.warning("Skipping invalid drop preset file %s: missing required fields", file_path)
            except Exception as e:
                logger.warning("Failed to load built-in drop preset from %s: %s", file_path, e)

    # 2. User persistent directory
    user_dir = Path(get_data_dir()) / "presets" / "drops"
    if user_dir.is_dir():
        for file_path in sorted(user_dir.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "id" in data and "name" in data and "message_pattern" in data:
                    data["is_custom"] = True
                    presets[data["id"]] = data
                else:
                    logger.warning("Skipping invalid user drop preset file %s: missing required fields", file_path)
            except Exception as e:
                logger.warning("Failed to load user drop preset from %s: %s", file_path, e)

    return list(presets.values())


def get_drop_preset_by_id(preset_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a specific drop preset by ID."""
    clean_id = (preset_id or "").strip().lower()
    for preset in get_drop_presets():
        if str(preset.get("id", "")).lower() == clean_id:
            return dict(preset)
    return None
