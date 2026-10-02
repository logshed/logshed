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



