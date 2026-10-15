"""
Regex validation and ReDoS protection utilities for LogShed.

User-written patterns (alert rules, drop rules and their test endpoints) are compiled and
matched with the `regex` package, whose `search(..., timeout=...)` aborts a runaway match
and raises `TimeoutError`. A static detector still refuses obviously dangerous patterns at
save time so users get a clear message instead of a silent timeout later.

LogShed's own internal patterns (parsers, redactor) stay on the standard `re` module.
"""

import logging
import math
import os
import re
import threading
import time
from typing import Optional, Tuple, Union

import regex
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Candidate text longer than this is truncated before matching a user pattern
USER_REGEX_MAX_INPUT_CHARS = 16384

_DEFAULT_USER_REGEX_TIMEOUT_MS = 50.0
_TIMEOUT_WARNING_INTERVAL_SECONDS = 60.0
_MAX_TRACKED_TIMEOUT_LABELS = 1024

# Errors raised when a user pattern has invalid syntax
UserPatternError = regex.error


def read_user_regex_timeout() -> float:
    """
    Read the per-match timeout for user patterns, in seconds, from USER_REGEX_TIMEOUT_MS.
    Falls back to 50 ms when the variable is unset, not a number, or not positive.
    """
    raw = os.environ.get("USER_REGEX_TIMEOUT_MS")
    default = _DEFAULT_USER_REGEX_TIMEOUT_MS / 1000.0
    if raw is None or not raw.strip():
        return default
    try:
        value_ms = float(raw)
    except ValueError:
        logger.warning(
            "Ignoring USER_REGEX_TIMEOUT_MS=%r: not a number. Using %.0f ms.", raw, _DEFAULT_USER_REGEX_TIMEOUT_MS
        )
        return default
    if not math.isfinite(value_ms) or value_ms <= 0:
        logger.warning(
            "Ignoring USER_REGEX_TIMEOUT_MS=%r: must be a positive number. Using %.0f ms.",
            raw,
            _DEFAULT_USER_REGEX_TIMEOUT_MS,
        )
        return default
    return value_ms / 1000.0


# Per-match timeout (seconds) applied to every user pattern search
USER_REGEX_TIMEOUT: float = read_user_regex_timeout()

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

# Detect repeated alternations susceptible to catastrophic backtracking.
# Safe disjoint alternations without repetitions like (GET|POST)+ or (a|b)+ are permitted.
# Only flag alternations containing nested quantifiers or overlapping repetitions:
# e.g. (a|b+)+, (a+|b)+, (a|aa)+, ([a-z]|[a-z][a-z])+, (foo|foobar)*
_ALTERNATION_REPETITION_RE = re.compile(
    r"""
    \( (?:\?[:=!<=])?        # Group start
    (?:
        # Branch 1: Inner alternation branch containing inner repetition quantifiers
        [^\(\)]*? (?:[+*]|\{\d+,\d*\}) [^\(\)]*? \| [^\(\)]*?
        |
        [^\(\)]*? \| [^\(\)]*? (?:[+*]|\{\d+,\d*\}) [^\(\)]*?
        |
        # Branch 2: Alternations with identical prefixes inducing overlap backtracking,
        # e.g. (a|aa)+, (foo|foobar)+, ([a-z]|[a-z][a-z])+
        ([^\|\(\)]+) \| \1 [^\|\(\)]+
        |
        ([^\|\(\)]+) [^\|\(\)]+ \| \2
    )
    \)                       # Group end
    (?:\+|\*|\{\d+,\d*\})    # Outer quantifier +, *, or {n,m}
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

_NESTED_REPETITION_MESSAGE = (
    "Regular expression contains nested repetitions susceptible to catastrophic backtracking."
)
_REPEATED_WILDCARD_GROUP_MESSAGE = (
    "Regular expression repeats a group containing .* or .+ ten or more times, "
    "which is susceptible to catastrophic backtracking."
)
_OVERLAPPING_ALTERNATION_MESSAGE = (
    "Regular expression repeats an alternation whose branches can match the same text, "
    "which is susceptible to catastrophic backtracking."
)
_CONSECUTIVE_WILDCARDS_MESSAGE = (
    "Regular expression contains three or more .* or .+ wildcards in a row, "
    "which is susceptible to catastrophic backtracking."
)

# Group prefixes such as (?:, (?P<name>, (?<name>, (?=, (?!, (?<=, (?<!, (?>, (?i:
_GROUP_PREFIX_RE = re.compile(r"^\?(?:P?<[A-Za-z_]\w*>|<[=!]|[:=!>]|[a-zA-Z]*(?:-[a-zA-Z]+)?:)")
_QUANTIFIER_RE = re.compile(r"[+*?]|\{(\d*)(,?)(\d*)\}")
# A branch made of exactly one single-character atom: escape, character class, dot or literal
_SINGLE_ATOM_RE = re.compile(r"^(?:\\[^0-9]|\[\^?\]?(?:\\.|[^\]\\])*\]|[^\\\[\]()*+?{}|^$])$")
_WILDCARD_RUN_RE = re.compile(r"(?:\.[*+][?+]?){3,}")
_OVERLAP_SAMPLE_CHARS = [chr(c) for c in range(0x250)] + [
    "é", "٣", " ", "　", "中", "Ж", " ",
]


def _mask_escapes_and_classes(pattern: str) -> str:
    """
    Return a same-length copy of the pattern where escaped characters and character class
    contents are replaced with neutral placeholders, so structural checks only see real
    regex operators.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            out.append("E" * len(pattern[i:i + 2]))
            i += 2
            continue
        if ch == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            end = min(j + 1, n)
            out.append("C" * (end - i))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _iter_quantified_groups(pattern: str):
    """
    Yield (content, quantifier_match, has_nested_group) for each parenthesised group that is
    followed by a quantifier. `content` is the original text without a prefix such as `?:`.
    """
    masked = _mask_escapes_and_classes(pattern)
    stack: list[list] = []
    for idx, ch in enumerate(masked):
        if ch == "(":
            if stack:
                stack[-1][1] = True
            stack.append([idx, False])
        elif ch == ")" and stack:
            start, has_nested = stack.pop()
            quant_match = _QUANTIFIER_RE.match(masked, idx + 1)
            if not quant_match:
                continue
            content = pattern[start + 1:idx]
            prefix = _GROUP_PREFIX_RE.match(content)
            if prefix:
                content = content[prefix.end():]
            yield content, quant_match, has_nested


def _split_top_level_branches(content: str) -> list[str]:
    """Split group content on alternation bars that are not escaped or inside a class or subgroup."""
    masked = _mask_escapes_and_classes(content)
    branches: list[str] = []
    depth = 0
    last = 0
    for idx, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "|" and depth == 0:
            branches.append(content[last:idx])
            last = idx + 1
    branches.append(content[last:])
    return branches


def _single_atom_sample_matches(atom: str) -> frozenset[str]:
    """Return the sample characters a single-character atom matches (ignoring case)."""
    try:
        atom_re = re.compile(atom, re.IGNORECASE)
    except re.error:
        return frozenset()
    return frozenset(c for c in _OVERLAP_SAMPLE_CHARS if atom_re.fullmatch(c))


def _branches_overlap(branches: list[str]) -> bool:
    """
    Return True when two alternation branches can match the same text: identical branches,
    or two single-character atoms (such as \\w and \\d) that share a character.
    """
    seen_branches: set[str] = set()
    seen_chars: set[str] = set()
    for branch in branches:
        if branch in seen_branches:
            return True
        seen_branches.add(branch)
        if _SINGLE_ATOM_RE.match(branch):
            matched = _single_atom_sample_matches(branch)
            if not seen_chars.isdisjoint(matched):
                return True
            seen_chars.update(matched)
    return False


def _is_unbounded(quant_match: "re.Match[str]") -> bool:
    """Return True for +, * and {n,} / {n,m} quantifiers (anything that repeats a variable count)."""
    token = quant_match.group(0)
    if token in ("+", "*"):
        return True
    if token == "?":
        return False
    return bool(quant_match.group(2))


def _find_backtracking_risk(pattern: str) -> Optional[str]:
    """Return a message describing why the pattern is dangerous, or None when it looks safe."""
    # Strip escaped characters so literal escaped characters like \\+ or \\( do not trigger false matches
    stripped = re.sub(r"\\.", "", pattern)
    if (
        _NESTED_REPETITION_RE.search(stripped)
        or _ALTERNATION_REPETITION_RE.search(stripped)
        or _NESTED_GROUPS_RE.search(stripped)
        or _max_group_nesting_depth(stripped) >= 4
    ):
        return _NESTED_REPETITION_MESSAGE

    for content, quant_match, has_nested in _iter_quantified_groups(pattern):
        masked_content = _mask_escapes_and_classes(content)

        # (.*a){20}: an exact repeat count of 10 or more over a group holding a wildcard
        exact_count = quant_match.group(1)
        if (
            exact_count
            and not quant_match.group(2)
            and int(exact_count) >= 10
            and re.search(r"\.[*+]", masked_content)
        ):
            return _REPEATED_WILDCARD_GROUP_MESSAGE

        # (a|a)+, (\w|\d)+, (\s|\W)+: repeated alternation whose branches can match the same text
        if (
            not has_nested
            and _is_unbounded(quant_match)
            and "|" in masked_content
            and _branches_overlap(_split_top_level_branches(content))
        ):
            return _OVERLAPPING_ALTERNATION_MESSAGE

    # .*.*.*= : three or more unbounded wildcards in a row (grouping parentheses do not separate them)
    masked_pattern = _mask_escapes_and_classes(pattern)
    ungrouped = re.sub(r"\(\?(?:P?<[A-Za-z_]\w*>|:)|[()]", "", masked_pattern)
    if _WILDCARD_RUN_RE.search(ungrouped):
        return _CONSECUTIVE_WILDCARDS_MESSAGE

    return None


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
    Check if a regex pattern contains nested repetition structures, overlapping alternations
    in repeated groups, wildcard-heavy repeats, or deeply nested groups vulnerable to
    catastrophic polynomial or exponential backtracking.
    """
    if not pattern:
        return False
    return _find_backtracking_risk(pattern) is not None


def validate_pattern_complexity(pattern: str) -> None:
    """
    Validate pattern complexity and safety against ReDoS attacks.
    Raises ValueError if the pattern is vulnerable or excessively complex.
    """
    if len(pattern) > 1000:
        raise ValueError("Regular expression pattern exceeds maximum length limit of 1000 characters.")
    if is_catastrophic_backtracking_pattern(pattern):
        # Only rejected patterns pay for the second pass that picks the message
        raise ValueError(_find_backtracking_risk(pattern) or _NESTED_REPETITION_MESSAGE)


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

    # 2. Validate syntax with the engine that runs user patterns
    try:
        regex.compile(trimmed, regex.VERSION0)
    except regex.error as exc:
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


_FLAG_MAP = (
    (re.IGNORECASE, regex.IGNORECASE),
    (re.MULTILINE, regex.MULTILINE),
    (re.DOTALL, regex.DOTALL),
    (re.VERBOSE, regex.VERBOSE),
    (re.ASCII, regex.ASCII),
)


def _to_regex_flags(flags: int) -> int:
    """Translate standard `re` flags into `regex` flags, in the re-compatible VERSION0 mode."""
    translated = regex.VERSION0
    for re_flag, regex_flag in _FLAG_MAP:
        if flags & re_flag:
            translated |= regex_flag
    return translated


def compile_user_pattern(pattern: str, flags: int = 0) -> "regex.Pattern":
    """
    Validate a user-written pattern with the static detector and compile it with the `regex` engine.
    `flags` takes standard `re` flags (for example re.IGNORECASE).
    Raises ValueError if the pattern is unsafe, or UserPatternError if the syntax is invalid.
    """
    validate_pattern_complexity(pattern)
    return regex.compile(pattern, _to_regex_flags(flags))


_timeout_lock = threading.Lock()
_timeout_counts: dict[str, int] = {}
_timeout_last_warned: dict[str, float] = {}


def _record_timeout(rule_label: str, timeout: float) -> None:
    """Count a timeout for the rule and log a WARNING at most once per rule per minute."""
    now = time.monotonic()
    with _timeout_lock:
        if rule_label not in _timeout_counts and len(_timeout_counts) >= _MAX_TRACKED_TIMEOUT_LABELS:
            oldest = next(iter(_timeout_counts))
            _timeout_counts.pop(oldest, None)
            _timeout_last_warned.pop(oldest, None)
        count = _timeout_counts.get(rule_label, 0) + 1
        _timeout_counts[rule_label] = count
        last_warned = _timeout_last_warned.get(rule_label)
        should_warn = last_warned is None or (now - last_warned) >= _TIMEOUT_WARNING_INTERVAL_SECONDS
        if should_warn:
            _timeout_last_warned[rule_label] = now
    if should_warn:
        logger.warning(
            "Regex for %s exceeded the %.0f ms match limit and was treated as no match "
            "(%d timeout(s) so far). Simplify the pattern if this keeps happening.",
            rule_label,
            timeout * 1000,
            count,
        )


def get_user_regex_timeout_counts() -> dict[str, int]:
    """Return a copy of the per-rule user regex timeout counters."""
    with _timeout_lock:
        return dict(_timeout_counts)


def reset_user_regex_timeout_stats() -> None:
    """Clear the per-rule timeout counters and warning timestamps."""
    with _timeout_lock:
        _timeout_counts.clear()
        _timeout_last_warned.clear()


def user_pattern_search(
    compiled: "regex.Pattern",
    text: Optional[str],
    rule_label: str = "user pattern",
    timeout: Optional[float] = None,
) -> bool:
    """
    Search `text` (truncated to 16,384 characters) with a compiled user pattern under a real
    per-match timeout. A timeout counts against `rule_label`, logs a rate-limited WARNING and
    is treated as no match.
    """
    if text is None:
        return False
    effective_timeout = USER_REGEX_TIMEOUT if timeout is None else timeout
    candidate = text[:USER_REGEX_MAX_INPUT_CHARS]
    try:
        return compiled.search(candidate, timeout=effective_timeout) is not None
    except TimeoutError:
        _record_timeout(rule_label, effective_timeout)
        return False


def safe_regex_search(
    pattern: Union["regex.Pattern", re.Pattern, str],
    string: str,
    timeout: Optional[float] = None,
    rule_label: str = "user pattern",
) -> bool:
    """
    Match a user pattern (string, `re` or `regex` pattern) against a candidate string under
    the user pattern timeout. String patterns are validated and compiled case-insensitively.
    Returns False for empty input, invalid patterns and timeouts.
    """
    if not string:
        return False

    try:
        if isinstance(pattern, str):
            compiled = compile_user_pattern(pattern, re.IGNORECASE)
        elif isinstance(pattern, re.Pattern):
            compiled = regex.compile(pattern.pattern, _to_regex_flags(pattern.flags))
        else:
            compiled = pattern
    except Exception as exc:
        logger.debug("Regular expression compilation or validation error: %s", exc)
        return False

    return user_pattern_search(compiled, string, rule_label=rule_label, timeout=timeout)


def shutdown_regex_executor(wait: bool = True) -> None:
    """
    No-op kept for the lifespan shutdown hook. User patterns now run inline under the
    `regex` engine's own timeout, so there is no regex thread pool to stop.
    """
    return None
