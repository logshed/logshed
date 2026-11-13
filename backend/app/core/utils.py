"""
Shared core utility functions for string matching, normalization, and timestamp parsing.
"""

import datetime
import fnmatch
import functools
import json
import os
import re
import zoneinfo
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@functools.lru_cache(maxsize=8)
def _load_zone(tz_env: str) -> Optional[datetime.tzinfo]:
    """Resolve the process time zone rules from the TZ value ('' means unset), or None."""
    try:
        if tz_env:
            # Accept the POSIX ':Europe/London' spelling as well as 'Europe/London'.
            return zoneinfo.ZoneInfo(tz_env.lstrip(":"))
        with open("/etc/localtime", "rb") as fh:
            return zoneinfo.ZoneInfo.from_file(fh)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return None


def get_server_tzinfo() -> datetime.tzinfo:
    """
    Return the server's local time zone with its daylight saving rules.

    The zone comes from the TZ environment variable (an IANA name such as Europe/London),
    or from /etc/localtime when TZ is unset. If neither can be read, it falls back to
    today's fixed UTC offset, which is correct today but not across a clock change.
    """
    zone = _load_zone(os.environ.get("TZ", "").strip())
    if zone is not None:
        return zone
    return datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc


def get_server_timezone_name() -> Optional[str]:
    """
    The IANA name of the server time zone (such as Europe/London), or None when it has none.

    The name comes from TZ (a leading ':' is dropped). A zone read from /etc/localtime carries no
    name, so it is reported as UTC only when it never moves off UTC, and otherwise as None.
    Abbreviations such as BST are never returned: they are not zone names, and their offset
    depends on the date.
    """
    zone = _load_zone(os.environ.get("TZ", "").strip())
    if zone is None:
        return None
    key = getattr(zone, "key", None)
    if key:
        return key
    january = datetime.datetime(2026, 1, 15, tzinfo=zone)
    july = datetime.datetime(2026, 7, 15, tzinfo=zone)
    if not january.utcoffset() and not july.utcoffset():
        return "UTC"
    return None


def zone_from_name(name: Optional[str]) -> Optional[datetime.tzinfo]:
    """The IANA time zone for a name such as Europe/London (a leading ':' is allowed), or None."""
    clean = (name or "").strip().lstrip(":")
    if not clean:
        return None
    return _load_zone(clean)


def format_utc_offset(minutes: int) -> str:
    """A UTC offset in minutes as 'UTC+01:00' or 'UTC-05:30'."""
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{mins:02d}"


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


def wildcard_text_key(text: Optional[str]) -> Optional[str]:
    """The form of text that match_wildcard compares (lowercased, stripped), or None for empty text."""
    return text.lower().strip() if text else None


def compile_wildcard(pattern: Optional[str]) -> Optional[Callable[[str], bool]]:
    """
    match_wildcard for one pattern, prepared once: a function of wildcard_text_key(text) (never
    None) that gives the same result, or None when the pattern is empty and matches any text.
    """
    if not pattern or not pattern.strip():
        return None
    p = pattern.lower().strip()
    if "*" in p or "?" in p:
        match = re.compile(fnmatch.translate(p)).match
        return lambda t: match(t) is not None
    return lambda t: t == p


def split_pattern_list(text: Optional[str]) -> list[str]:
    """
    The patterns in a comma-separated list such as a drop rule's host or app field: trimmed,
    without empty parts, and with later duplicates that differ only in letter case left out.
    """
    if not text:
        return []
    parts: list[str] = []
    seen: set[str] = set()
    for part in text.split(","):
        part = part.strip()
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            parts.append(part)
    return parts


def normalize_pattern_list(text: Optional[str]) -> Optional[str]:
    """A comma-separated pattern list in its stored form ("a, b"), or None when it holds no pattern."""
    parts = split_pattern_list(text)
    return ", ".join(parts) if parts else None


def compile_wildcard_list(text: Optional[str]) -> Optional[Callable[[str], bool]]:
    """
    compile_wildcard for a comma-separated list: a function of wildcard_text_key(text) that is
    true when any pattern in the list matches, or None when the list is empty and matches any text.
    """
    parts = split_pattern_list(text)
    if not parts:
        return None
    if len(parts) == 1:
        return compile_wildcard(parts[0])
    exact = frozenset(p.lower() for p in parts if "*" not in p and "?" not in p)
    wild = [p.lower() for p in parts if "*" in p or "?" in p]
    if not wild:
        return exact.__contains__
    match = re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in wild)).match
    if not exact:
        return lambda t: match(t) is not None
    return lambda t: t in exact or match(t) is not None


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


# system_settings key holding a JSON list of the aliases of deleted Docker hosts and the old
# aliases of renamed ones. Rows have no column that says which collector stored them, so this
# list is what keeps those hosts' old container logs classed as Docker.
FORMER_DOCKER_ALIASES_KEY = "former_docker_aliases"

# Every source_alias that marks a row as Docker output: the alias of each Docker host (enabled or
# not), the primary Docker alias setting, each former Docker alias and the built-in "docker". This
# one statement is the definition: SQL uses it in IN and NOT IN, and load_docker_source_aliases runs
# it. Only non-empty text is returned, so a damaged setting can never put NULL into NOT IN.
DOCKER_ALIASES_SQL = (
    "SELECT trim(source_alias) FROM docker_endpoints WHERE trim(source_alias) != ''"
    " UNION SELECT trim(value) FROM system_settings WHERE key = 'docker_source_alias' AND trim(value) != ''"
    " UNION SELECT trim(value) FROM json_each(COALESCE((SELECT value FROM system_settings"
    f" WHERE key = '{FORMER_DOCKER_ALIASES_KEY}' AND json_valid(value) AND json_type(value) = 'array'), '[]'))"
    " WHERE type = 'text' AND trim(value) != ''"
    " UNION SELECT 'docker'"
)


def source_filter_sql(placeholders: str) -> str:
    """
    The WHERE fragment for a host filter: rows whose alias is one of the names, or syslog and
    ingest rows from one of the addresses (never Docker rows, which share the address).
    """
    return (
        f"(logs.source_alias IN ({placeholders}) OR (logs.source_ip IN ({placeholders})"
        f" AND (logs.source_alias IS NULL OR logs.source_alias NOT IN ({DOCKER_ALIASES_SQL}))"
        " AND logs.source_ip != 'docker'))"
    )


def load_former_docker_aliases(conn: Any) -> list[str]:
    """The aliases of deleted Docker hosts and the old aliases of renamed ones, oldest first."""
    try:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", (FORMER_DOCKER_ALIASES_KEY,)
        ).fetchone()
        values = json.loads(row[0]) if row and row[0] else []
    except Exception:
        return []
    if not isinstance(values, list):
        return []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def note_docker_alias_change(conn: Any, old_alias: Optional[str], new_alias: Optional[str]) -> None:
    """
    Record that a Docker host stopped using old_alias (deleted, or renamed to new_alias) and
    started using new_alias (created or renamed). The old alias joins the former Docker aliases,
    so the container logs that carry it stay Docker; the new one leaves that list. The caller
    commits.
    """
    old_alias = (old_alias or "").strip()
    new_alias = (new_alias or "").strip()
    former = load_former_docker_aliases(conn)
    updated = [a for a in former if a != new_alias]
    if old_alias and old_alias != new_alias and old_alias != "docker" and old_alias not in updated:
        updated.append(old_alias)
    if updated == former:
        return
    conn.execute(
        """
        INSERT INTO system_settings (key, value, updated_at, is_encrypted)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (
            FORMER_DOCKER_ALIASES_KEY,
            json.dumps(updated),
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
    )


def load_docker_source_aliases(conn: Any) -> set[str]:
    """
    Return the source_alias values that mark a log row as Docker container output (the rows of
    DOCKER_ALIASES_SQL): every Docker host alias, the primary Docker alias setting, the former
    Docker aliases and "docker".

    Syslog and Docker rows from the same machine share a source_ip, so this set is what
    keeps them apart. Matching is exact (as in SQL `IN`), never case-insensitive.
    """
    try:
        return {"docker"} | {str(r[0]) for r in conn.execute(DOCKER_ALIASES_SQL).fetchall() if r[0]}
    except Exception:
        return {"docker"}


@dataclass(frozen=True)
class AliasIndex:
    """
    The host names that log filters, the live stream and the facets read: each configured syslog
    host alias with its address, and the names that mark a row as Docker output
    (load_docker_source_aliases). Load it once per request with AliasIndex.load(conn).
    """

    alias_by_ip: dict[str, str] = field(default_factory=dict)
    docker_aliases: frozenset[str] = frozenset({"docker"})
    # Each name or address, exact and folded to lower case, mapped to its alias pair
    _exact: dict[str, frozenset[str]] = field(default_factory=dict)
    _folded: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, conn: Any) -> "AliasIndex":
        """Read host_aliases and the Docker names; an unreadable host_aliases table gives an empty index."""
        try:
            rows = conn.execute("SELECT ip, alias FROM host_aliases").fetchall()
        except Exception:
            return cls()
        alias_by_ip: dict[str, str] = {}
        exact: dict[str, set[str]] = {}
        folded: dict[str, set[str]] = {}
        for row in rows:
            ip = str(row[0]).strip() if row[0] is not None else ""
            alias = str(row[1]).strip() if row[1] is not None else ""
            if ip and alias:
                alias_by_ip[ip] = alias
            pair_items = {item for item in (ip, alias) if item}
            for item in pair_items:
                folded.setdefault(item.lower(), set()).update(pair_items)
                exact.setdefault(item, set()).update(pair_items)
        return cls(
            alias_by_ip=alias_by_ip,
            docker_aliases=frozenset(load_docker_source_aliases(conn)),
            _exact={k: frozenset(v) for k, v in exact.items()},
            _folded={k: frozenset(v) for k, v in folded.items()},
        )

    def is_docker(self, source_alias: Optional[str], source_ip: Optional[str]) -> bool:
        """True when a row with this alias and address is Docker container output."""
        return source_ip == "docker" or bool(source_alias and source_alias in self.docker_aliases)

    def expand(self, sources: Optional[list[str]]) -> list[str]:
        """
        Symmetrically expand source names or IPs using the configured host aliases.
        For each source, adds its corresponding IP and alias equivalents (bidirectionally and
        case-insensitively), ensuring queries and deletion filters match both raw IPs and
        historical alias spellings without requiring costly unindexed SQL functions.

        A source that is exactly a Docker host alias only expands through an exact host alias
        match, so a syslog alias spelled the same apart from letter case (for the same machine)
        never pulls that host's syslog logs into a Docker host filter.
        """
        parsed = parse_multi_values(sources)
        if not parsed:
            return []
        expanded: set[str] = set(parsed)
        for item in parsed:
            matches = self._exact.get(item) if item in self.docker_aliases else self._folded.get(item.lower())
            if matches:
                expanded.update(matches)
        return sorted(expanded)


def expand_source_aliases(
    sources: Optional[list[str]],
    conn: Optional[Any] = None,
) -> list[str]:
    """
    AliasIndex.expand with the index read from conn. Without a connection, the parsed sources
    are returned deduplicated and unexpanded.
    """
    if conn is None:
        return parse_multi_values(sources)
    if not parse_multi_values(sources):
        return []
    return AliasIndex.load(conn).expand(sources)


# Reserved FTS5 syntax operators
_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}
# Strict allowlist of searchable columns in logs_fts virtual table
_FTS_ALLOWED_COLUMNS = {"app_name", "source_alias", "message"}
# Shorthand prefixes the live stream matcher also accepts, mapped to their FTS column
_FTS_COLUMN_ALIASES = {
    "app": "app_name",
    "host": "source_alias",
    "source": "source_alias",
    "msg": "message",
}


# Search as you type matches a term as the start of a word only from this many characters on.
# A one or two letter prefix (e*, er*) merges the doclists of thousands of words on every
# keystroke, so shorter terms match whole words unless the user types the * themselves.
MIN_AUTO_PREFIX_LENGTH = 3


def _auto_prefix(fts_term: str, typed: str) -> str:
    """Append the search-as-you-type '*' to fts_term when the typed term is long enough."""
    return f"{fts_term}*" if len(typed) >= MIN_AUTO_PREFIX_LENGTH else fts_term


def _needs_fts_quoting(value: str) -> bool:
    """True when an unquoted FTS5 term would be a syntax error (dots and hyphens split tokens)."""
    return "." in value or "-" in value


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
            tokens.append(_auto_prefix(f'"{clean}"', clean))
    return " ".join(tokens)


# A quoted phrase, a column filter (its value may be quoted), a parenthesis or a bare term
_FTS_TOKEN_PATTERN = re.compile(r'("[^"]*"|\'[^\']*\'|[a-zA-Z_]+:(?:"[^"]*"|[^\s()]+)|\(|\)|[^\s()]+)')


def format_fts_query(query_str: str) -> str:
    """
    Format user query for FTS5 search with prefix matching.
    - Strict allowlist for valid column prefixes (app_name, source_alias, message) and operators.
    - If search query contains unbalanced quotes or syntax errors, fall back cleanly
      to escaped token prefix queries so queries never trigger an unhandled OperationalError.
    - If user entered quoted phrases (e.g. "exact phrase"), preserve them.
    - If terms do not end in '*' and are not boolean operators (AND, OR, NOT, NEAR),
      automatically append '*' for search-as-you-type prefix matching, from
      MIN_AUTO_PREFIX_LENGTH characters on (shorter terms match whole words).
    - If column filters are used (e.g. app_name:nginx), apply wildcard to the value.
    - The shorthand prefixes app:, host:, source: and msg: (any case) map to app_name,
      source_alias, source_alias and message, matching the live stream filter.
    - Terms containing '.' or '-' (IP addresses, host names such as pve-node2) are quoted.
    """
    q = query_str.strip()
    if not q:
        return ""

    # Special FTS5 query syntax: queries starting with '*' trigger unknown special query errors
    if q.startswith("*") or not _fts_brackets_balanced(q):
        return escape_fts_tokens(q)

    formatted_tokens = []
    for token in _FTS_TOKEN_PATTERN.findall(q):
        token = token.strip()
        if not token:
            continue
        formatted = _format_fts_token(token)
        if formatted is None:
            return escape_fts_tokens(q)
        formatted_tokens.append(formatted)

    if not _is_valid_fts_sequence(formatted_tokens):
        return escape_fts_tokens(q)
    return " ".join(formatted_tokens)


def _fts_brackets_balanced(q: str) -> bool:
    """True when q's double and single quotes pair up and its parentheses nest correctly."""
    if q.count('"') % 2 != 0 or q.count("'") % 2 != 0:
        return False
    depth = 0
    for char in q:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _format_fts_token(token: str) -> Optional[str]:
    """
    One token of a user query in FTS5 syntax, or None when the query must fall back to
    escaped tokens (an incomplete column filter while the user is still typing).
    """
    if token.startswith(('"', "'")) or token in ("(", ")"):
        return token
    if token.upper() in _FTS_OPERATORS:
        return token.upper()
    if ":" not in token:
        return _format_fts_term(token)

    col, val = token.split(":", 1)
    col_lower = col.lower()
    col_lower = _FTS_COLUMN_ALIASES.get(col_lower, col_lower)
    if col_lower not in _FTS_ALLOWED_COLUMNS:
        # Column is not allowlisted - treat as literal token wrapped in quotes
        clean_token = token.replace('"', '""')
        if clean_token.endswith("*") and not clean_token.startswith("*"):
            return f'"{clean_token[:-1]}"*'
        return _auto_prefix(f'"{clean_token}"', token)
    if not val or val == "*":
        # Incomplete column filter while user is typing (e.g. "app_name:" or "app_name:*")
        return None
    if val.startswith(('"', "'")):
        return f"{col_lower}:{val}"
    return _format_fts_term(val, f"{col_lower}:")


def _format_fts_term(term: str, column: str = "") -> str:
    """
    A bare term (or a column filter's value, after column, such as "app_name:") as an FTS5
    prefix query: quoted when it has a leading '*' or characters FTS5 splits on, with the
    search-as-you-type '*' from MIN_AUTO_PREFIX_LENGTH characters on.
    """
    if term.startswith("*"):
        clean = term.replace('"', '""')
        return f'{column}"{clean}"*'
    if term.endswith("*"):
        core = term[:-1].replace('"', '""')
        return f'{column}"{core}"*' if _needs_fts_quoting(core) else f"{column}{core}*"
    clean = term.replace('"', '""')
    if _needs_fts_quoting(clean):
        return _auto_prefix(f'{column}"{clean}"', term)
    return _auto_prefix(f"{column}{clean}", term)


def _is_valid_fts_sequence(tokens: list[str]) -> bool:
    """
    True when formatted tokens make a well-formed FTS5 expression: at least one term, no
    operator at either end, beside another operator or inside a parenthesis edge, no empty
    parentheses, and an operator between a term and a parenthesised group.
    """
    non_parens = [t for t in tokens if t not in ("(", ")")]
    if not non_parens:
        return False
    if non_parens[0] in _FTS_OPERATORS or non_parens[-1] in _FTS_OPERATORS:
        return False
    for t1, t2 in zip(tokens, tokens[1:]):
        if t1 in _FTS_OPERATORS and t2 in _FTS_OPERATORS:
            return False
        if t1 == "(" and t2 in _FTS_OPERATORS:
            return False
        if t1 in _FTS_OPERATORS and t2 == ")":
            return False
        if t1 == "(" and t2 == ")":
            return False
        # Term directly before '(' without operator
        if t1 not in _FTS_OPERATORS and t1 != "(" and t2 == "(":
            return False
        # Term directly after ')' without operator
        if t1 == ")" and t2 not in _FTS_OPERATORS and t2 != ")":
            return False
    return True


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
        f"Unknown severity name '{val}'. Valid options: emergency, alert, critical, error, warning, notice, info, debug"
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


def _local_midnight_utc(days_back: int = 0) -> datetime.datetime:
    """Return midnight (in the process local time zone) of the date ``days_back`` days ago, in UTC."""
    local_date = datetime.datetime.now().astimezone().date() - datetime.timedelta(days=days_back)
    # A naive datetime is read as local time, so astimezone() applies the zone's rules for
    # that date (including daylight saving time).
    local_midnight = datetime.datetime.combine(local_date, datetime.time.min)
    return local_midnight.astimezone(datetime.timezone.utc)


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

    # Human relative keywords: "today" starts at midnight in the server's local time zone
    # (the zone the UI reports as server_tz_name), converted to UTC. Midnight is built from
    # the local date, not by subtracting hours, so days with a DST change stay correct.
    if clean_lower in ("since_midnight", "today"):
        return _local_midnight_utc(days_back=0).isoformat()
    if clean_lower == "yesterday":
        return _local_midnight_utc(days_back=1).isoformat()

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
