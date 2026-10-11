"""
Shared core utility functions for string matching, normalization, and timestamp parsing.
"""

import datetime
import fnmatch
from typing import Any, Optional


def match_wildcard(pattern: Optional[str], text: Optional[str]) -> bool:
    """
    Case-insensitive wildcard matching supporting '*' and '?'.
    If no wildcard characters exist in pattern, performs an exact case-insensitive match.
    """
    if not pattern or not pattern.strip():
        return True
    if not text:
        return False

    p = pattern.lower().strip()
    t = text.lower().strip()
    if "*" in p or "?" in p:
        return fnmatch.fnmatchcase(t, p)
    return p == t


def parse_iso_to_utc_datetime(val: Any) -> Optional[datetime.datetime]:
    """Parse ISO-8601 string or numeric timestamp to timezone-aware UTC datetime."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(val), tz=datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=datetime.timezone.utc)
        return val.astimezone(datetime.timezone.utc)
    try:
        clean_str = str(val).strip().replace("Z", "+00:00")
        if not clean_str:
            return None
        dt = datetime.datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def parse_iso_to_epoch(ts_val: Any, fallback: Optional[float] = None) -> float:
    """
    Parse an ISO-8601 timestamp string or datetime object into a UTC epoch timestamp.
    Returns fallback (defaulting to 0.0 if None) when parsing fails or input is empty.
    """
    default_fallback = 0.0 if fallback is None else fallback
    dt = parse_iso_to_utc_datetime(ts_val)
    if dt is not None:
        return dt.timestamp()
    return default_fallback


def parse_multi_values(values: Optional[list[str]]) -> list[str]:
    """
    Parse query parameters that may be passed repeatedly or comma-separated.
    e.g. ['pve1,pve2', 'pve3'] -> ['pve1', 'pve2', 'pve3']
    """
    if not values:
        return []
    result: list[str] = []
    for item in values:
        if not item:
            continue
        for part in item.split(","):
            part = part.strip()
            if part and part not in result:
                result.append(part)
    return result


def expand_source_aliases(
    sources: Optional[list[str]],
    conn: Optional[Any] = None,
) -> list[str]:
    """
    Symmetrically expand a list of source names or IPs using configured host aliases.
    For each source, adds its corresponding IP and alias equivalents (bidirectionally and
    case-insensitively), ensuring queries and deletion filters match both raw IPs and
    historical alias spellings without requiring costly unindexed SQL functions.
    """
    parsed = parse_multi_values(sources)
    if not parsed:
        return []

    # If no database connection is supplied, return parsed inputs deduplicated
    if conn is None:
        return parsed

    expanded: set[str] = set(parsed)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ip, alias FROM host_aliases")
        rows = cursor.fetchall()
    except Exception:
        return sorted(expanded)

    # Build bidirectional lookup maps keyed by lowercase string
    lookup: dict[str, set[str]] = {}
    for row in rows:
        ip = str(row[0]).strip() if row[0] is not None else ""
        alias = str(row[1]).strip() if row[1] is not None else ""
        if not ip and not alias:
            continue

        pair_items = {item for item in (ip, alias) if item}
        for item in pair_items:
            key = item.lower()
            if key not in lookup:
                lookup[key] = set()
            lookup[key].update(pair_items)

    for item in parsed:
        matches = lookup.get(item.lower())
        if matches:
            expanded.update(matches)

    return sorted(expanded)


import re

# Reserved FTS5 syntax operators
_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}
# Strict allowlist of searchable columns in logs_fts virtual table
_FTS_ALLOWED_COLUMNS = {"app_name", "source_alias", "message"}


def escape_fts_tokens(query_str: str) -> str:
    """
    Fallback token cleaner that wraps words in quotes with wildcard suffix
    to handle malformed user input without causing FTS5 syntax errors.
    """
    q = query_str.strip()
    if not q:
        return ""
    words = q.split()
    tokens = []
    for w in words:
        clean = w.replace('"', '').replace("'", '').replace('*', '').replace('\x00', '').strip()
        if clean:
            tokens.append(f'"{clean}"*')
    return " ".join(tokens)


def format_fts_query(query_str: str) -> str:
    """
    Format user query for FTS5 search with prefix matching.
    - Strict allowlist for valid column prefixes (app_name, source_alias, message) and operators.
    - If search query contains unbalanced quotes or syntax errors, fall back cleanly
      to escaped token prefix queries so queries never trigger an unhandled OperationalError.
    - If user entered quoted phrases (e.g. "exact phrase"), preserve them.
    - If terms do not end in '*' and are not boolean operators (AND, OR, NOT, NEAR),
      automatically append '*' for search-as-you-type prefix matching.
    - If column filters are used (e.g. app_name:nginx), apply wildcard to the value.
    """
    q = query_str.strip()
    if not q:
        return ""

    # Special FTS5 query syntax: queries starting with '*' trigger unknown special query errors
    if q.startswith("*"):
        return escape_fts_tokens(q)

    # Check for unbalanced double or single quotes
    if q.count('"') % 2 != 0 or q.count("'") % 2 != 0:
        return escape_fts_tokens(q)

    # Check for unbalanced or malformed parentheses
    depth = 0
    for char in q:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return escape_fts_tokens(q)
    if depth != 0:
        return escape_fts_tokens(q)

    pattern = re.compile(r'("[^"]*"|\'[^\']*\'|[a-zA-Z_]+:(?:"[^"]*"|[^\s()]+)|\(|\)|[^\s()]+)')
    tokens = pattern.findall(q)
    if not tokens:
        return escape_fts_tokens(q)

    formatted_tokens = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue

        if token.startswith(('"', "'")) or token in ("(", ")"):
            formatted_tokens.append(token)
            continue

        if token.upper() in _FTS_OPERATORS:
            formatted_tokens.append(token.upper())
            continue

        if ":" in token:
            col, val = token.split(":", 1)
            col_lower = col.lower()
            if col_lower in _FTS_ALLOWED_COLUMNS:
                if not val or val == "*":
                    # Incomplete column filter while user is typing (e.g. "app_name:" or "app_name:*")
                    # Fall back cleanly to escaped token search to prevent FTS5 syntax errors
                    return escape_fts_tokens(q)
                if val.startswith(('"', "'")):
                    formatted_tokens.append(f"{col_lower}:{val}")
                elif val.startswith("*"):
                    clean_val = val.replace('"', '""')
                    formatted_tokens.append(f'{col_lower}:"{clean_val}"*')
                elif val.endswith("*"):
                    core = val[:-1]
                    clean_core = core.replace('"', '""')
                    if "." in clean_core:
                        formatted_tokens.append(f'{col_lower}:"{clean_core}"*')
                    else:
                        formatted_tokens.append(f"{col_lower}:{clean_core}*")
                else:
                    clean_val = val.replace('"', '""')
                    if "." in clean_val:
                        formatted_tokens.append(f'{col_lower}:"{clean_val}"*')
                    else:
                        formatted_tokens.append(f"{col_lower}:{clean_val}*")
                continue
            else:
                # Column is not allowlisted - treat as literal token wrapped in quotes
                clean_token = token.replace('"', '""')
                if clean_token.endswith("*") and not clean_token.startswith("*"):
                    formatted_tokens.append(f'"{clean_token[:-1]}"*')
                else:
                    formatted_tokens.append(f'"{clean_token}"*')
                continue

        if token.startswith("*"):
            clean_token = token.replace('"', '""')
            formatted_tokens.append(f'"{clean_token}"*')
        elif token.endswith("*"):
            core = token[:-1]
            clean_core = core.replace('"', '""')
            if "." in clean_core:
                formatted_tokens.append(f'"{clean_core}"*')
            else:
                formatted_tokens.append(f"{clean_core}*")
        else:
            clean_token = token.replace('"', '""')
            if "." in clean_token:
                formatted_tokens.append(f'"{clean_token}"*')
            else:
                formatted_tokens.append(f"{clean_token}*")

    if not formatted_tokens:
        return escape_fts_tokens(q)

    # Validate token sequence for FTS5 syntax correctness
    non_parens = [t for t in formatted_tokens if t not in ("(", ")")]
    if not non_parens:
        return escape_fts_tokens(q)

    # First and last non-paren tokens cannot be operators
    if non_parens[0] in _FTS_OPERATORS or non_parens[-1] in _FTS_OPERATORS:
        return escape_fts_tokens(q)

    for i in range(len(formatted_tokens) - 1):
        t1, t2 = formatted_tokens[i], formatted_tokens[i + 1]
        # Consecutive operators
        if t1 in _FTS_OPERATORS and t2 in _FTS_OPERATORS:
            return escape_fts_tokens(q)
        # Operator immediately after '('
        if t1 == "(" and t2 in _FTS_OPERATORS:
            return escape_fts_tokens(q)
        # Operator immediately before ')'
        if t1 in _FTS_OPERATORS and t2 == ")":
            return escape_fts_tokens(q)
        # Empty parens
        if t1 == "(" and t2 == ")":
            return escape_fts_tokens(q)
        # Term directly before '(' without operator
        if t1 not in _FTS_OPERATORS and t1 != "(" and t2 == "(":
            return escape_fts_tokens(q)
        # Term directly after ')' without operator
        if t1 == ")" and t2 not in _FTS_OPERATORS and t2 != ")":
            return escape_fts_tokens(q)

    return " ".join(formatted_tokens)


SEVERITY_NAME_MAP: dict[str, int] = {
    "emerg": 0,
    "emergency": 0,
    "alert": 1,
    "crit": 2,
    "critical": 2,
    "fatal": 2,
    "panic": 2,
    "err": 3,
    "error": 3,
    "warn": 4,
    "warning": 4,
    "notice": 5,
    "info": 6,
    "informational": 6,
    "log": 6,
    "debug": 7,
    "trace": 7,
    "verbose": 7,
}

SEVERITY_LABELS: dict[int, str] = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


def resolve_severity(val: Any) -> Optional[int]:
    """
    Resolve a severity parameter (integer 0-7, numeric string, or name such as
    'error', 'warning', 'info', 'crit', 'debug') to an RFC 5424 integer level (0-7).
    Returns None if val is None or empty. Raises ValueError if invalid.
    """
    if val is None or type(val).__name__ == "Query":
        return None
    if isinstance(val, int):
        if 0 <= val <= 7:
            return val
        raise ValueError(f"Severity integer must be between 0 and 7, got {val}")
    s = str(val).strip().lower()
    if not s:
        return None
    if s.isdigit():
        iv = int(s)
        if 0 <= iv <= 7:
            return iv
        raise ValueError(f"Severity integer must be between 0 and 7, got {iv}")
    if s in SEVERITY_NAME_MAP:
        return SEVERITY_NAME_MAP[s]
    raise ValueError(
        f"Unrecognized severity name '{val}'. Valid options: emergency, alert, critical, error, warning, notice, info, debug"
    )


def format_severity_label(severity: Optional[int]) -> str:
    """Format numeric RFC 5424 severity (0-7) to a standard human-readable lowercase label."""
    if severity is None or type(severity).__name__ == "Query":
        return "unknown"
    return SEVERITY_LABELS.get(severity, "unknown")


_RE_RELATIVE_TIME = re.compile(
    r"^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$",
    re.IGNORECASE,
)


def parse_relative_or_iso_time(val: Any) -> Optional[str]:
    """
    Parse a relative time string (e.g. '15m', '1h', '24h', '7d', 'since_midnight', 'today', 'yesterday')
    or an ISO-8601 string into an ISO-8601 UTC string.
    Returns None if val is None or empty. Raises ValueError if format is unrecognized.
    """
    if val is None or type(val).__name__ == "Query":
        return None
    if isinstance(val, (int, float)):
        dt = datetime.datetime.fromtimestamp(float(val), tz=datetime.timezone.utc)
        return dt.isoformat()
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=datetime.timezone.utc)
        return val.astimezone(datetime.timezone.utc).isoformat()

    clean = str(val).strip()
    if not clean:
        return None

    clean_lower = clean.lower()
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Human relative keywords
    if clean_lower in ("since_midnight", "today"):
        midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.isoformat()
    if clean_lower == "yesterday":
        yesterday_midnight = (now_utc - datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return yesterday_midnight.isoformat()

    # Relative duration pattern (e.g., "1h", "30m", "24h", "7d")
    m = _RE_RELATIVE_TIME.match(clean)
    if m:
        num = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("s"):
            delta = datetime.timedelta(seconds=num)
        elif unit.startswith("m"):
            delta = datetime.timedelta(minutes=num)
        elif unit.startswith("h"):
            delta = datetime.timedelta(hours=num)
        elif unit.startswith("d"):
            delta = datetime.timedelta(days=num)
        elif unit.startswith("w"):
            delta = datetime.timedelta(weeks=num)
        else:
            delta = datetime.timedelta(minutes=num)
        return (now_utc - delta).isoformat()

    # Check ISO-8601 parsing
    parsed_dt = parse_iso_to_utc_datetime(clean)
    if parsed_dt is not None:
        return parsed_dt.isoformat()

    try:
        clean_std = clean.replace(" ", "T").replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_std)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat()
    except Exception:
        raise ValueError(
            f"Invalid time format '{val}'. Expected relative duration (e.g. '1h', '24h', 'since_midnight') "
            "or ISO-8601 timestamp."
        )
