"""
Regex validation and ReDoS protection utilities for LogShed.

Provides security checks against pathological nested repetition antipatterns
susceptible to catastrophic backtracking (Regular Expression Denial of Service).
"""

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import threading
from typing import Optional, Tuple, Union
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Detect nested repetition antipatterns:
# e.g. (a+)+, ([a-z]+)*, (a*)+, (a+){2,}, (?:a+)+, (\w+)*
_NESTED_REPETITION_RE = re.compile(
    r"""
    (
        \((?:\?[:=!<=])?        # Group start: (, (?:, (?=, (?!, (?<=, (?<!
        (?:[^\(\)]*?            # Inner content without unescaped parenthesis
            (?:
                [+*]            # Inner quantifier + or *
                |\{\d+,\d*\}    # Inner range quantifier {n,} or {n,m}
            )
        [^\(\)]*?)
        \)                      # Group end
        (?:\+|\*|\{\d+,\d*\})   # Outer quantifier +, *, or {n,m}
    )
    """,
    re.VERBOSE,
)

# Detect repeated alternations susceptible to catastrophic backtracking:
# e.g. (a|aa)+, ([a-z]|[a-z][a-z])+, (foo|foobar)*
_ALTERNATION_REPETITION_RE = re.compile(
    r"""
    (
        \((?:\?[:=!<=])?        # Group start
        (?:[^\(\)]*?\|[^\(\)]*?) # Inner content containing alternation |
        \)                      # Group end
        (?:\+|\*|\{\d+,\d*\})   # Outer quantifier +, *, or {n,m}
    )
    """,
    re.VERBOSE,
)

# Detect deeply nested groups with repetition:
# e.g. ((a)+)+, (((a+))+)+, ((a+))+
_NESTED_GROUPS_RE = re.compile(
    r"""
    (
        \(                      # Outer group start
        [^\(\)]*
        \((?:\?[:=!<=])?        # Inner group start
        [^\(\)]+?
        \)                      # Inner group end
        (?:[+*]|\{\d+,\d*\})?   # Optional inner quantifier
        [^\(\)]*
        \)                      # Outer group end
        (?:\+|\*|\{\d+,\d*\})   # Outer quantifier +, *, or {n,m}
    )
    """,
    re.VERBOSE,
)


def _max_group_nesting_depth(pattern: str) -> int:
    """Calculate maximum nesting depth of parentheses, ignoring character classes."""
    max_depth = 0
    current_depth = 0
    in_class = False
    for ch in pattern:
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        elif not in_class:
            if ch == "(":
                current_depth += 1
                if current_depth > max_depth:
                    max_depth = current_depth
            elif ch == ")":
                current_depth = max(0, current_depth - 1)
    return max_depth


def is_catastrophic_backtracking_pattern(pattern: str) -> bool:
    """
    Check if a regex pattern contains nested repetition structures,
    alternations in repeated groups, or deeply nested groups
    vulnerable to catastrophic polynomial or exponential backtracking.
    """
    if not pattern:
        return False
    # Strip escaped characters so literal escaped characters like \\+ or \\( do not trigger false matches
    stripped = re.sub(r"\\.", "", pattern)
    if _NESTED_REPETITION_RE.search(stripped):
        return True
    if _ALTERNATION_REPETITION_RE.search(stripped):
        return True
    if _NESTED_GROUPS_RE.search(stripped):
        return True
    if _max_group_nesting_depth(stripped) >= 4:
        return True
    return False


def validate_pattern_complexity(pattern: str) -> None:
    """
    Validate pattern complexity and safety against ReDoS attacks.
    Raises ValueError if the pattern is vulnerable or excessively complex.
    """
    if len(pattern) > 1000:
        raise ValueError("Regular expression pattern exceeds maximum length limit of 1000 characters.")
    if is_catastrophic_backtracking_pattern(pattern):
        raise ValueError(
            "Regular expression contains nested repetitions susceptible to catastrophic backtracking."
        )


def check_regex_safety(pattern: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Inspect a user-supplied regular expression pattern for validity and safety.

    Returns:
        (is_safe, error_message)
    """
    if not pattern or not pattern.strip() or pattern.strip() == "*":
        return True, None

    trimmed = pattern.strip()

    # 1. Check for pathological nested repetition antipatterns and complexity
    try:
        validate_pattern_complexity(trimmed)
    except ValueError as exc:
        return False, str(exc)

    # 2. Validate regex compilation
    try:
        re.compile(trimmed)
    except re.error as exc:
        return False, f"Invalid regex syntax (Invalid regular expression): {exc}"

    return True, None


def validate_regex_pattern(pattern: Optional[str]) -> None:
    """
    Validate a user-supplied regular expression pattern.
    Raises HTTPException(400) if syntax is invalid or vulnerable to catastrophic backtracking.
    """
    is_safe, error_msg = check_regex_safety(pattern)
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg or "Invalid regular expression pattern.",
        )


def compile_safe_regex(pattern: str, flags: int = 0) -> re.Pattern:
    """
    Validate pattern complexity and compile a safe regular expression.
    Raises ValueError if the pattern is unsafe, or re.error if syntax is invalid.
    """
    validate_pattern_complexity(pattern)
    return re.compile(pattern, flags)


_REGEX_POOL: Optional[ThreadPoolExecutor] = None
_REGEX_POOL_LOCK = threading.Lock()


def get_regex_executor() -> ThreadPoolExecutor:
    """Return dedicated ThreadPoolExecutor for isolated regex matching."""
    global _REGEX_POOL
    with _REGEX_POOL_LOCK:
        if _REGEX_POOL is None or getattr(_REGEX_POOL, "_shutdown", False):
            _REGEX_POOL = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="logshed-regex",
            )
        return _REGEX_POOL


def safe_regex_search(
    pattern: Union[re.Pattern, str],
    string: str,
    timeout: float = 0.1,
) -> bool:
    """
    Safely execute regular expression search with timeout protection
    to prevent hanging worker threads or event loops.
    """
    if not string:
        return False

    try:
        compiled = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.IGNORECASE)
    except Exception:
        return False

    # Bound candidate string length to prevent massive buffer scanning
    candidate = string[:65536]

    executor = get_regex_executor()
    future = executor.submit(compiled.search, candidate)
    try:
        match = future.result(timeout=timeout)
        return match is not None
    except (concurrent.futures.TimeoutError, TimeoutError):
        logger.warning(f"Regular expression matching timed out after {timeout}s.")
        return False
    except Exception as exc:
        logger.debug(f"Regular expression execution error: {exc}")
        return False
