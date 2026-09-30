"""
GHCR version checker service for LogShed.
Queries GitHub Container Registry for available image tags, filters for stable releases,
and checks if a newer version is available.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from app.version import APP_VERSION, SemVer, compare_semver, is_newer_stable_version, parse_semver

logger = logging.getLogger("logshed.version")

# In-memory cache configuration (1 hour TTL)
_CACHE_TTL_SECONDS = 3600
_cached_version_info: Optional[Dict[str, Any]] = None
_cached_at: float = 0.0

# Target GHCR repository (lowercase required for GHCR token service)
DEFAULT_IMAGE_REPO = os.environ.get("LOGSHED_IMAGE_REPO", "logshed/logshed").lower()


def is_deprecated_image_repo(repo: str = DEFAULT_IMAGE_REPO) -> bool:
    """Return True if image repository points to deprecated personal namespace."""
    return repo.lower().startswith("benhornertech/")


if is_deprecated_image_repo(DEFAULT_IMAGE_REPO):
    logger.warning(
        "LogShed container repository 'ghcr.io/%s' is deprecated. "
        "Please update your container configuration to 'ghcr.io/logshed/logshed'.",
        DEFAULT_IMAGE_REPO,
    )


def clear_version_cache() -> None:
    """Clear in-memory cached version check data (useful in testing or manual invalidation)."""
    global _cached_version_info, _cached_at
    _cached_version_info = None
    _cached_at = 0.0



def _extract_latest_stable_version(tags: List[str]) -> Optional[str]:
    """
    Given a list of tag strings from GHCR, find the highest stable semantic version.
    Excludes pre-release tags (beta, rc, etc.) and non-semver tags (latest, branches).
    """
    stable_versions: List[tuple[SemVer, str]] = []
    for tag in tags:
        parsed = parse_semver(tag)
        if parsed and parsed.is_stable():
            stable_versions.append((parsed, tag))

    if not stable_versions:
        return None

    # Sort using SemVer comparison
    stable_versions.sort(
        key=lambda item: (item[0].major, item[0].minor, item[0].patch),
        reverse=True,
    )
    return stable_versions[0][1]


async def fetch_ghcr_tags(image_repo: str = DEFAULT_IMAGE_REPO) -> List[str]:
    """
    Fetch all tags for a public GHCR repository using the anonymous OCI registry protocol.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Step 1: Obtain anonymous pull token
        token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{image_repo}:pull"
        token_res = await client.get(token_url)
        token_res.raise_for_status()
        token_data = token_res.json()
        token = token_data.get("token")
        if not token:
            logger.warning("No token returned by GHCR token endpoint.")
            return []

        # Step 2: List repository tags
        tags_url = f"https://ghcr.io/v2/{image_repo}/tags/list"
        headers = {"Authorization": f"Bearer {token}"}
        tags_res = await client.get(tags_url, headers=headers)
        tags_res.raise_for_status()
        tags_data = tags_res.json()
        return tags_data.get("tags", [])


def _is_update_check_enabled_in_db(conn) -> bool:
    """Check if update checks are enabled in SQLite system_settings table."""
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_settings WHERE key = 'check_for_updates'")
    row = cursor.fetchone()
    if row is None:
        return True
    return str(row[0]).strip().lower() not in ("0", "false", "no", "off")


async def check_for_updates(
    force_refresh: bool = False,
    image_repo: str = DEFAULT_IMAGE_REPO,
) -> Dict[str, Any]:
    """
    Check if a newer stable version is available on GHCR.
    Respects the check_for_updates system setting.
    Caches results in memory for 1 hour. Fails gracefully on network or registry errors.
    """
    global _cached_version_info, _cached_at

    now = time.time()
    repo_deprecated = is_deprecated_image_repo(image_repo)

    is_enabled = True
    try:
        from app.api.deps import run_db_query
        is_enabled = await run_db_query(_is_update_check_enabled_in_db)
    except Exception:
        pass

    if not is_enabled:
        return {
            "current_version": APP_VERSION,
            "latest_version": None,
            "update_available": False,
            "check_enabled": False,
            "checked_at": now,
            "repo_deprecated": repo_deprecated,
        }

    if not force_refresh and _cached_version_info and (now - _cached_at < _CACHE_TTL_SECONDS):
        return _cached_version_info

    try:
        # If the container repo is deprecated, query the official logshed/logshed repository for releases
        query_repo = "logshed/logshed" if repo_deprecated else image_repo
        tags = await fetch_ghcr_tags(image_repo=query_repo)
        latest_stable = _extract_latest_stable_version(tags)

        update_available = False
        if latest_stable:
            update_available = is_newer_stable_version(latest_stable, APP_VERSION)

        result = {
            "current_version": APP_VERSION,
            "latest_version": latest_stable,
            "update_available": update_available,
            "check_enabled": True,
            "checked_at": now,
            "repo_deprecated": repo_deprecated,
        }

        _cached_version_info = result
        _cached_at = now
        return result

    except Exception as exc:
        logger.warning(f"Failed to check GHCR for updates: {exc}")
        if _cached_version_info:
            # Return stale cache if available
            return _cached_version_info

        # Graceful fallback when unreachable
        return {
            "current_version": APP_VERSION,
            "latest_version": None,
            "update_available": False,
            "check_enabled": True,
            "checked_at": now,
            "repo_deprecated": repo_deprecated,
        }

