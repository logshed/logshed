"""
Application version constants and semantic version comparison utilities for LogShed.
"""

import os
import re
from typing import NamedTuple, Optional

# Default installed version if not set via environment variable
APP_VERSION = os.environ.get("LOGSHED_VERSION", "1.3.0-beta.1")

# Regular expression matching semantic versions (e.g. 1.0.0, v1.1.0, 1.2.0-beta.1)
_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)


class SemVer(NamedTuple):
    major: int
    minor: int
    patch: int
    prerelease: Optional[str] = None

    def is_stable(self) -> bool:
        """Return True if this version has no prerelease identifier."""
        return self.prerelease is None


def parse_semver(version_str: str) -> Optional[SemVer]:
    """
    Parse a version string into a SemVer tuple.
    Returns None if the string does not conform to standard semver.
    """
    if not version_str or not isinstance(version_str, str):
        return None
    match = _SEMVER_RE.match(version_str.strip())
    if not match:
        return None
    return SemVer(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=match.group("prerelease"),
    )


def compare_semver(v1: SemVer, v2: SemVer) -> int:
    """
    Compare two SemVer instances.
    Returns:
        1 if v1 > v2
       -1 if v1 < v2
        0 if v1 == v2
    Per SemVer 2.0.0:
    1. Compare major, minor, patch numerically.
    2. A version without a prerelease identifier has higher precedence
       than a version with a prerelease identifier of the same major.minor.patch.
    """
    t1 = (v1.major, v1.minor, v1.patch)
    t2 = (v2.major, v2.minor, v2.patch)
    if t1 > t2:
        return 1
    if t1 < t2:
        return -1

    # Major, minor, patch are identical:
    # A stable version has higher precedence than a prerelease version.
    if v1.prerelease is None and v2.prerelease is not None:
        return 1
    if v1.prerelease is not None and v2.prerelease is None:
        return -1
    if v1.prerelease == v2.prerelease:
        return 0

    # Both have prerelease strings: compare lexicographically/numerically
    p1_parts = v1.prerelease.split(".") if v1.prerelease else []
    p2_parts = v2.prerelease.split(".") if v2.prerelease else []
    for part1, part2 in zip(p1_parts, p2_parts):
        is_num1 = part1.isdigit()
        is_num2 = part2.isdigit()
        if is_num1 and is_num2:
            n1, n2 = int(part1), int(part2)
            if n1 > n2:
                return 1
            if n1 < n2:
                return -1
        elif is_num1:
            return -1  # Numeric identifiers have lower precedence than non-numeric
        elif is_num2:
            return 1
        else:
            if part1 > part2:
                return 1
            if part1 < part2:
                return -1

    if len(p1_parts) > len(p2_parts):
        return 1
    if len(p1_parts) < len(p2_parts):
        return -1

    return 0


def is_newer_stable_version(candidate_version_str: str, current_version_str: str = APP_VERSION) -> bool:
    """
    Determine if candidate_version_str is a stable release and strictly newer than current_version_str.
    """
    candidate = parse_semver(candidate_version_str)
    if not candidate or not candidate.is_stable():
        return False

    current = parse_semver(current_version_str)
    if not current:
        return True

    return compare_semver(candidate, current) > 0
