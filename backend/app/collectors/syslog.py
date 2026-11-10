"""
Syslog collector module for LogShed.
Supports UDP and TCP on port 1514, parsing RFC 3164 and RFC 5424.
"""

import asyncio
import datetime
import logging
import re
import socket
import sqlite3
import sys
import threading
import time
import weakref
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.config import get_syslog_udp_receive_buffer_bytes
from app.core.migrations import get_connection
from app.core.pipeline import (
    MAX_FUTURE_TIMESTAMP_SKEW_SECONDS,
    MAX_MESSAGE_BYTES,
    KeyedMultilineAssembler,
    detect_severity,
    _RE_PYTHON_EXCEPTION,
    get_queue,
    increment_dropped_count,
    ingest_room,
    wait_for_ingest_room,
)
from app.core.utils import SEVERITY_NAME_MAP, load_docker_source_aliases

logger = logging.getLogger(__name__)

# Appended to a syslog message cut down to MAX_MESSAGE_BYTES characters.
TRUNCATION_MARKER = " [truncated]"

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Patterns of the syslog parser, compiled once
_RE_PRI = re.compile(r"^<(\d{1,3})>")
# Patterns that end in (.*) take the rest of the text with re.DOTALL, so a datagram
# with several lines keeps all of them in the message, as RFC 5424 messages do.
_RE_TAG_PID_COLON = re.compile(r"^([^:\s\[]+?)(?:\[\d+\])?:\s*(.*)", re.DOTALL)
_RE_TAG_PID_SPACE = re.compile(r"^([^:\s\[]+)\[\d+\]\s+(.*)", re.DOTALL)
_RE_BRACKETED_TAG = re.compile(r"^\[([^\]]+)\]:?\s*(.*)", re.DOTALL)
_RE_TAG_SPACE = re.compile(r"^([^:\s]+)\s+(.*)", re.DOTALL)
_RE_RFC3164_TIMESTAMP = re.compile(r"^([A-Z][a-z]{2})\s+(\d+)\s+(\d{2}):(\d{2}):(\d{2})\s+")
_ISO_TIMESTAMP = r"(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?)"
_RE_BRACKETED_ISO_TIMESTAMP = re.compile(r"^\[" + _ISO_TIMESTAMP + r"\]\s*")
_RE_ISO_TIMESTAMP = re.compile(r"^" + _ISO_TIMESTAMP + r"\s+")
_RE_HOST_APP_ENVELOPE = re.compile(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s*(.*)", re.DOTALL)
_RE_BRACKETED_HOST = re.compile(r"^\[([^\]]+)\]\s*(.*)", re.DOTALL)
_RE_VALKEY = re.compile(r"^(\d+:[a-zA-Z])\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+([#*.-])\s+(.*)", re.DOTALL)
VALKEY_SEVERITY = {"#": 4, "*": 5, ".": 7, "-": 7}
_RE_APP_PIPE = re.compile(r"^\[([^\]]+)\]\s*\|\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+(.*)", re.DOTALL)
_RE_BRACKETED_LEVEL = re.compile(r"^\[([a-zA-Z]+)\]\s*(.*)", re.DOTALL)
_RE_LOGFMT = re.compile(r"""^(?:time|ts)=["']?([^"'\s]+)["']?\s+(.*)""", re.DOTALL)
RE_LOGFMT_LEVEL = re.compile(r"""\b(?:level|lvl|severity)=["']?([a-zA-Z]+)["']?""")
RE_LOGFMT_MSG = re.compile(r"""\bmsg=(?:"([^"]*)"|'([^']*)'|(\S+))""")
_RE_UTC_SUFFIX = re.compile(r"\s+(?:UTC|GMT)\b", flags=re.IGNORECASE)
_RE_ZONE_ABBREVIATION = re.compile(r"\s+[A-Z]{2,5}(?=$|[+-])")
# Matched at an offset with .match(buffer, pos), so no "^" anchor
_RE_OCTET_COUNTED_FRAME = re.compile(rb"(\d+) <\d+>")

def _correct_future_timestamp(dt_utc: datetime.datetime, now: datetime.datetime) -> datetime.datetime:
    """
    A timestamp more than MAX_FUTURE_TIMESTAMP_SKEW_SECONDS ahead of now is pulled back by whole
    hours (a sender reporting the wrong time zone). If it is still too far ahead, now is used.
    """
    diff_seconds = (dt_utc - now).total_seconds()
    if diff_seconds > MAX_FUTURE_TIMESTAMP_SKEW_SECONDS:
        offset_hours = round(diff_seconds / 3600)
        if offset_hours > 0:
            dt_utc -= datetime.timedelta(hours=offset_hours)
        if (dt_utc - now).total_seconds() > MAX_FUTURE_TIMESTAMP_SKEW_SECONDS:
            dt_utc = now
    return dt_utc


def _resolve_naive(dt: datetime.datetime, now: datetime.datetime, local_tz: datetime.tzinfo) -> datetime.datetime:
    """
    A timestamp without an offset, in UTC. Senders write either their local time or UTC, so
    the reading nearer the received time wins (local time on a tie).
    """
    as_local = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
    as_utc = dt.replace(tzinfo=datetime.timezone.utc)
    if abs((as_utc - now).total_seconds()) < abs((as_local - now).total_seconds()):
        return as_utc
    return as_local


def _naive_datetime(year: int, month: int, day: int, time_str: str) -> datetime.datetime:
    """A datetime from a date and HH:MM:SS with an optional fraction after "." or ","."""
    time_parts = time_str.replace(",", ".").split(":")
    sec_parts = time_parts[2].split(".")
    microsecond = int(float("0." + sec_parts[1]) * 1_000_000) if len(sec_parts) > 1 else 0
    return datetime.datetime(
        year, month, day, int(time_parts[0]), int(time_parts[1]), int(sec_parts[0]), microsecond
    )


def _parse_valkey_datetime(
    day_str: str,
    mon_str: str,
    year_str: str,
    time_str: str,
    now: datetime.datetime,
    local_tz: datetime.tzinfo,
) -> str:
    """Parse Valkey/Redis style datetime (e.g. 02 Oct 2026 11:47:57.745) to UTC ISO format."""
    try:
        dt = _naive_datetime(int(year_str), _MONTH_MAP.get(mon_str.lower(), 1), int(day_str), time_str)
        return _resolve_naive(dt, now, local_tz).isoformat()
    except Exception:
        return f"{year_str}-{mon_str}-{day_str}T{time_str}"

def _parse_dmy_or_datetime(
    dmy_str: str,
    time_str: str,
    now: datetime.datetime,
    local_tz: datetime.tzinfo,
) -> str:
    """Parse day-first slash date (DD/MM/YYYY HH:MM:SS) to UTC ISO format."""
    try:
        parts = dmy_str.split("/")
        dt = _naive_datetime(int(parts[2]), int(parts[1]), int(parts[0]), time_str)
        return _resolve_naive(dt, now, local_tz).isoformat()
    except Exception:
        return f"{dmy_str}T{time_str}"

def _parse_iso_or_datetime(
    ts_str: str,
    now: datetime.datetime,
    local_tz: datetime.tzinfo,
) -> str:
    """Parse an ISO 8601, RFC 3339, or standard datetime string to UTC ISO format."""
    ts_clean = ts_str.strip().replace("Z", "+00:00").replace("/", "-")
    ts_clean = _RE_UTC_SUFFIX.sub("+00:00", ts_clean)
    # Strip any remaining timezone abbreviations like BST, EST, PDT
    ts_clean = _RE_ZONE_ABBREVIATION.sub("", ts_clean)
    if "," in ts_clean:
        ts_clean = ts_clean.replace(",", ".")
    if " " in ts_clean and "T" not in ts_clean:
        ts_clean = ts_clean.replace(" ", "T", 1)
    try:
        dt = datetime.datetime.fromisoformat(ts_clean)
        dt_utc = _resolve_naive(dt, now, local_tz) if dt.tzinfo is None else dt.astimezone(datetime.timezone.utc)
        return _correct_future_timestamp(dt_utc, now).isoformat()
    except Exception:
        return ts_str


def _is_valid_hostname(token: str) -> bool:
    """Whether the token after a timestamp is a hostname rather than the app tag or the message."""
    return (
        bool(token)
        and token != "-"
        and "[" not in token
        and "]" not in token
        and token.lower() not in SEVERITY_NAME_MAP
        and not (token.endswith(":") and not token.endswith("::"))
    )


def _parse_hostname_and_msg(result: dict[str, Any], content: str) -> None:
    """Read an optional HOSTNAME (or "-") before the syslog MSG part, then the app and the message."""
    parts = content.split(" ", 1)
    if len(parts) == 2 and _is_valid_hostname(parts[0]):
        result["hostname"] = parts[0]
        _parse_msg_into_app_and_content(result, parts[1])
    elif len(parts) == 2 and parts[0] == "-":
        _parse_msg_into_app_and_content(result, parts[1])
    else:
        _parse_msg_into_app_and_content(result, content)


def _parse_msg_into_app_and_content(result: dict[str, Any], msg_part: str) -> None:
    """Split a syslog MSG part into the app (tag) and the message text, setting both on result."""
    # 1. TAG[PID]: CONTENT or TAG: CONTENT
    m = _RE_TAG_PID_COLON.match(msg_part)
    if m:
        result["app_name"] = m.group(1)
        result["message"] = m.group(2)
        return
    # 2. TAG[PID] CONTENT (PID without colon)
    m = _RE_TAG_PID_SPACE.match(msg_part)
    if m:
        result["app_name"] = m.group(1)
        result["message"] = m.group(2)
        return
    # 3. [TAG]: CONTENT or [TAG] CONTENT (bracketed process tag, e.g. [kernel])
    m = _RE_BRACKETED_TAG.match(msg_part)
    if m:
        if m.group(1).lower() not in SEVERITY_NAME_MAP:
            result["app_name"] = m.group(1)
            result["message"] = m.group(2)
            return
    # 4. TAG CONTENT (space-separated, no colon)
    m = _RE_TAG_SPACE.match(msg_part)
    if m:
        if m.group(1).lower() not in SEVERITY_NAME_MAP:
            result["app_name"] = m.group(1)
            result["message"] = m.group(2)
            return
    # 5. Fallback: single word or unparsed
    result["message"] = msg_part


# The local time zone for timestamps that carry no offset, read again at most once a second (it is a
# fixed UTC offset, so a clock change applies within a second) or when time.tzset() changes the zone.
_local_tz_cache: Optional[tuple[float, tuple[str, str], datetime.tzinfo]] = None


def _current_local_tz() -> datetime.tzinfo:
    global _local_tz_cache
    now = time.monotonic()
    cached = _local_tz_cache
    if cached is not None and now < cached[0] and cached[1] == time.tzname:
        return cached[2]
    local_tz = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
    _local_tz_cache = (now + 1.0, time.tzname, local_tz)
    return local_tz


def _rfc3164_datetime(
    year: int, mon_str: str, day_str: str, hour_str: str, minute_str: str, second_str: str
) -> datetime.datetime:
    """
    The naive datetime of an RFC 3164 timestamp (Mmm dd HH:MM:SS) in the given year, as
    strptime("%Y %b %d %H:%M:%S") reads it. Raises ValueError for an unknown month or an
    impossible date or time.
    """
    month = _MONTH_MAP.get(mon_str.lower())
    if month is None or len(day_str) > 2:
        raise ValueError(f"not an RFC 3164 timestamp: {mon_str} {day_str}")
    return datetime.datetime(year, month, int(day_str), int(hour_str), int(minute_str), int(second_str))


def parse_syslog_message(
    data: bytes,
    source_ip: str,
    now: datetime.datetime | None = None,
    local_tz: datetime.tzinfo | None = None,
) -> dict[str, Any]:
    """
    Parse a raw syslog message (bytes) into a structured dict.
    """
    raw_str = data.decode("utf-8", errors="replace").strip("\r\n")
    # Cap the stored text before parsing. The parsed message is always a slice of
    # raw_str, so it inherits the cap (and the marker when the tail was cut off).
    if len(raw_str) > MAX_MESSAGE_BYTES:
        raw_str = raw_str[: MAX_MESSAGE_BYTES - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now = now.astimezone(datetime.timezone.utc)

    if local_tz is None:
        local_tz = _current_local_tz()

    now_iso = now.isoformat()
    result = {
        "timestamp": now_iso,
        "received_at": now_iso,
        "source_ip": source_ip,
        "source_alias": source_ip,
        "app_name": "unknown",
        "facility": 1,
        "severity": 6,
        "message": raw_str,
        "raw": raw_str
    }
    
    content = raw_str
    
    # Parse PRI
    has_pri = False
    explicit_severity = False
    pri_match = _RE_PRI.match(content)
    if pri_match:
        has_pri = True
        pri = int(pri_match.group(1))
        result["facility"] = pri // 8
        result["severity"] = pri % 8
        content = content[pri_match.end():]
    # Check RFC 5424: version MUST be exactly '1' followed by a space, followed by a valid timestamp or '-'
    # This prevents misidentifying Valkey/Redis messages like '1:M ...' or other numbers.
    if content.startswith("1 ") and not content.startswith("1:"):
        content = content[2:]  # skip "1 "
        # RFC 5424 format after version:
        # TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP STRUCTURED-DATA [SP MSG]
        # Parse TIMESTAMP HOSTNAME APP-NAME PROCID MSGID by splitting on first 5 spaces
        header_parts = content.split(" ", 5)
        if len(header_parts) >= 6:
            timestamp, hostname, app_name, procid, msgid = header_parts[:5]
            remainder = header_parts[5]
            
            # Parse structured data (bracket-aware)
            # SD is either "-" (NILVALUE) or one or more [sdid ...] blocks
            if remainder.startswith("-"):
                # NILVALUE structured data
                msg = remainder[1:].lstrip(" ")
            elif remainder.startswith("["):
                # Bracket-aware extraction: consume all [...] blocks
                sd_end = 0
                i = 0
                while i < len(remainder) and remainder[i] == "[":
                    # Find matching closing bracket (not escaped)
                    j = i + 1
                    found_close = False
                    while j < len(remainder):
                        if remainder[j] == "]":
                            sd_end = j + 1
                            found_close = True
                            break
                        if remainder[j] == "\\" and j + 1 < len(remainder):
                            j += 1  # skip escaped char
                        j += 1
                    if not found_close:
                        break
                    i = sd_end
                    # skip optional space between SD elements
                    if i < len(remainder) and remainder[i] == " " and i + 1 < len(remainder) and remainder[i + 1] == "[":
                        i += 1
                if sd_end == 0:
                    msg = remainder
                else:
                    msg = remainder[sd_end:].lstrip(" ")
            else:
                # Malformed SD - treat entire remainder as message
                msg = remainder

            if timestamp != "-":
                try:
                    ts_clean = timestamp.replace("Z", "+00:00")
                    dt = datetime.datetime.fromisoformat(ts_clean)
                    if dt.tzinfo is None:
                        dt_utc = _resolve_naive(dt, now, local_tz)
                    else:
                        dt_utc = dt.astimezone(datetime.timezone.utc)
                    result["timestamp"] = _correct_future_timestamp(dt_utc, now).isoformat()
                except Exception:
                    result["timestamp"] = timestamp
            if hostname != "-":
                result["hostname"] = hostname
            if app_name != "-":
                result["app_name"] = app_name
            result["message"] = msg
        else:
            # Not enough fields for valid 5424 - treat as unparsed
            pass
    elif (ts_match := _RE_RFC3164_TIMESTAMP.match(content)):
        # RFC 3164: Mmm dd HH:MM:SS or Mmm  d HH:MM:SS
        content = content[ts_match.end():]

        # Try to parse timestamp
        try:
            # Add current year since RFC 3164 doesn't include it
            dt = _rfc3164_datetime(now.year, *ts_match.groups())
            parsed_month = dt.month
            # Year boundary heuristic: if parsed month is ahead of current month,
            # the message likely came from the previous year.
            # Conversely, if receiver is in December and sender in positive timezone emitted January,
            # the message belongs to the next year.
            if parsed_month > now.month:
                dt = dt.replace(year=now.year - 1)
            elif now.month == 12 and parsed_month == 1:
                dt = dt.replace(year=now.year + 1)

            # RFC 3164 timestamps lack timezone information and are emitted either in
            # the sender's local time or in UTC.
            dt_utc = _correct_future_timestamp(_resolve_naive(dt, now, local_tz), now)

            # Preserve microsecond arrival precision for proper sub-second ordering
            dt_utc = dt_utc.replace(microsecond=now.microsecond)
            result["timestamp"] = dt_utc.isoformat()
        except ValueError:
            pass

        # RFC 3164 Section 4.1.2 / 4.1.3: The HOSTNAME field is optional.
        _parse_hostname_and_msg(result, content)
    elif (m_bracket := _RE_BRACKETED_ISO_TIMESTAMP.match(content)):
        # Bracketed timestamp envelope: [TIMESTAMP] [HOST] [APP] MSG or [TIMESTAMP] MSG
        result["timestamp"] = _parse_iso_or_datetime(m_bracket.group(1), now, local_tz)
        rem = content[m_bracket.end():]
        m_env = _RE_HOST_APP_ENVELOPE.match(rem)
        if m_env:
            result["hostname"] = m_env.group(1)
            result["app_name"] = m_env.group(2)
            result["message"] = m_env.group(3)
        else:
            m_host = _RE_BRACKETED_HOST.match(rem)
            if m_host and m_host.group(1).lower() not in SEVERITY_NAME_MAP:
                result["hostname"] = m_host.group(1)
                _parse_msg_into_app_and_content(result, m_host.group(2))
            else:
                _parse_msg_into_app_and_content(result, rem)
    elif (m_iso := _RE_ISO_TIMESTAMP.match(content)):
        # Unbracketed ISO 8601 / RFC 3339 / standard datetime: TIMESTAMP HOSTNAME TAG: MSG
        result["timestamp"] = _parse_iso_or_datetime(m_iso.group(1), now, local_tz)
        _parse_hostname_and_msg(result, content[m_iso.end():])
    elif (m_valkey := _RE_VALKEY.match(content)):
        # Valkey / Redis server format: <pid>:<role> <day> <mon> <year> <time> <level_char> <msg>
        result["timestamp"] = _parse_valkey_datetime(
            m_valkey.group(2), m_valkey.group(3), m_valkey.group(4), m_valkey.group(5), now, local_tz
        )
        level_char = m_valkey.group(6)
        result["severity"] = VALKEY_SEVERITY.get(level_char, 6)
        explicit_severity = True
        result["message"] = m_valkey.group(7)
    elif (m_app_pipe := _RE_APP_PIPE.match(content)):
        # Application-prefixed pipe format: [maintainerr] | 03/10/2026 16:00:33 [INFO] ...
        result["app_name"] = m_app_pipe.group(1)
        result["timestamp"] = _parse_dmy_or_datetime(
            m_app_pipe.group(2), m_app_pipe.group(3), now, local_tz
        )
        rem = m_app_pipe.group(4)
        m_level = _RE_BRACKETED_LEVEL.match(rem)
        if m_level and m_level.group(1).lower() in SEVERITY_NAME_MAP:
            result["severity"] = SEVERITY_NAME_MAP[m_level.group(1).lower()]
            explicit_severity = True
            result["message"] = m_level.group(2)
        else:
            result["message"] = rem
    elif (m_logfmt := _RE_LOGFMT.match(content)):
        # Logfmt format: time="2026-10-03T14:56:31Z" level=info msg="..."
        result["timestamp"] = _parse_iso_or_datetime(m_logfmt.group(1), now, local_tz)
        rem = m_logfmt.group(2)
        m_level = RE_LOGFMT_LEVEL.search(rem)
        if m_level and m_level.group(1).lower() in SEVERITY_NAME_MAP:
            result["severity"] = SEVERITY_NAME_MAP[m_level.group(1).lower()]
            explicit_severity = True
        m_msg = RE_LOGFMT_MSG.search(rem)
        if m_msg:
            result["message"] = m_msg.group(1) or m_msg.group(2) or m_msg.group(3) or ""
        else:
            result["message"] = rem
    else:
        # Fallback: unparsed message without recognized timestamp
        result["message"] = content

    # Content-based severity fallback inspection:
    # If PRI was missing or has a generic default/notice priority (>= 5),
    # inspect message content for explicit severity keywords (e.g. error, warn, panic)
    if (not has_pri and not explicit_severity) or result["severity"] >= 5:
        content_sev = detect_severity(result["message"] if not content.startswith(('time=', 'ts=')) else content)
        if not has_pri and not explicit_severity:
            result["severity"] = content_sev
        elif content_sev < result["severity"]:
            result["severity"] = content_sev

    return result


_active_caches: weakref.WeakSet["AliasCache"] = weakref.WeakSet()

def reload_active_alias_caches() -> None:
    """Reload all active in-memory alias caches immediately."""
    for cache in list(_active_caches):
        cache.load_aliases()


class AliasCache:
    """
    Preloaded in-memory alias cache. Bulk-loads all host_aliases from the database
    on startup, then refreshes every refresh_interval seconds via a background task.
    Lookups are zero-cost dict reads - no DB I/O per message.
    """

    def __init__(self, db_path: str | Path, refresh_interval: float = 60.0, preload: bool = True):
        self._db_path = Path(db_path)
        self._refresh_interval = refresh_interval
        self._aliases: dict[str, str] = {}
        # Docker host aliases, lowercased: a syslog hostname equal to one is never used as an alias
        self._docker_aliases: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._refresh_task: asyncio.Task | None = None
        if preload:
            self.load_aliases()
        _active_caches.add(self)

    def resolve(self, source_ip: str) -> str:
        """Look up source_ip in the preloaded alias map. O(1) dict lookup."""
        with self._lock:
            return self._aliases.get(source_ip, source_ip)

    def is_docker_alias(self, name: str) -> bool:
        """True when name matches a Docker host alias, ignoring letter case."""
        with self._lock:
            return name.lower() in self._docker_aliases

    def load_aliases(self) -> None:
        """Synchronous: bulk-load all aliases from host_aliases, and the Docker host aliases."""
        new_aliases: dict[str, str] = {}
        new_docker_aliases: frozenset[str] = frozenset()
        try:
            conn = get_connection(self._db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT ip, alias FROM host_aliases")
                for row in cursor.fetchall():
                    new_aliases[row[0]] = row[1]
                new_docker_aliases = frozenset(a.lower() for a in load_docker_source_aliases(conn))
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # Table might not exist yet during early startup
            pass
        except Exception as e:
            logger.error(f"Error loading alias cache: {e}")
            return  # Keep existing cache on error

        with self._lock:
            self._aliases = new_aliases
            self._docker_aliases = new_docker_aliases

    async def start(self) -> None:
        """Start the periodic refresh background task. Safe to call again while running."""
        _active_caches.add(self)
        # Initial synchronous load
        await asyncio.to_thread(self.load_aliases)
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Stop the periodic refresh task."""
        _active_caches.discard(self)

        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            self._refresh_task = None

    async def _refresh_loop(self) -> None:
        """Background loop that reloads aliases every refresh_interval seconds."""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                await asyncio.to_thread(self.load_aliases)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error refreshing alias cache: {e}")


# Linux stores (and getsockopt reports) double the SO_RCVBUF value requested, keeping half for overhead.
_KERNEL_DOUBLES_RCVBUF = sys.platform.startswith("linux")


def configure_udp_receive_buffer(sock: Any, requested: int) -> Optional[int]:
    """
    Ask for a `requested`-byte receive buffer on the UDP syslog socket (0 leaves the OS default),
    then log the size the kernel granted, with a hint when net.core.rmem_max capped it.
    Never raises; returns the granted size in the same units as `requested` (on Linux, half of
    what getsockopt reports), or None if it is unknown.
    """
    if sock is None:
        return None
    if requested > 0:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, requested)
        except (OSError, OverflowError, ValueError) as e:
            logger.warning(
                f"Could not set the Syslog UDP receive buffer to {requested} bytes "
                f"(SYSLOG_UDP_RECEIVE_BUFFER_BYTES): {e}"
            )
    try:
        granted = int(sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
    except (OSError, TypeError, ValueError) as e:
        logger.warning(f"Could not read the Syslog UDP receive buffer size: {e}")
        return None

    # Compare and report in the units the user set, not the doubled figure Linux reports
    size = granted // 2 if _KERNEL_DOUBLES_RCVBUF else granted

    if requested <= 0:
        logger.info(f"Syslog UDP receive buffer: {size} bytes (OS default)")
        return size

    if size < requested:
        logger.warning(
            f"Syslog UDP receive buffer is {size} bytes, less than the {requested} bytes requested "
            f"(SYSLOG_UDP_RECEIVE_BUFFER_BYTES), because the kernel limit net.core.rmem_max is lower. "
            f"UDP syslog messages can be lost during bursts. To allow the full size, run "
            f"'sysctl -w net.core.rmem_max={requested}' on the host (add it to /etc/sysctl.conf to keep it "
            f"after a reboot), or set SYSLOG_UDP_RECEIVE_BUFFER_BYTES={size} to use the current limit "
            f"without this warning."
        )
    else:
        logger.info(f"Syslog UDP receive buffer: {size} bytes")
    return size


# Kernel UDP counters for this network namespace (Linux only).
PROC_NET_SNMP_PATH = "/proc/net/snmp"

# RcvbufErrors when the syslog listener first started in this process; None until then.
_udp_receive_errors_baseline: Optional[int] = None


def read_udp_snmp_counters(path: str | Path | None = None) -> Optional[dict[str, int]]:
    """
    The `Udp:` counters from /proc/net/snmp (a header line, then a value line), by name.
    None when the file is missing or unreadable (such as on a system other than Linux) or malformed.
    """
    try:
        text = Path(path or PROC_NET_SNMP_PATH).read_text()
    except (OSError, ValueError):
        return None
    udp_lines = [line.split()[1:] for line in text.splitlines() if line.startswith("Udp:")]
    if len(udp_lines) < 2 or len(udp_lines[0]) != len(udp_lines[1]):
        return None
    try:
        return {name: int(value) for name, value in zip(udp_lines[0], udp_lines[1])}
    except ValueError:
        return None


def read_udp_receive_buffer_errors(path: str | Path | None = None) -> Optional[int]:
    """UDP datagrams the kernel dropped because a socket receive buffer was full (RcvbufErrors), or None."""
    counters = read_udp_snmp_counters(path)
    if counters is None:
        return None
    return counters.get("RcvbufErrors")


def record_udp_receive_errors_baseline() -> None:
    """Remember the kernel counter when the syslog listener first starts; later restarts keep it."""
    global _udp_receive_errors_baseline
    if _udp_receive_errors_baseline is None:
        _udp_receive_errors_baseline = read_udp_receive_buffer_errors()


def get_udp_receive_errors() -> Optional[int]:
    """
    UDP datagrams dropped by the kernel because a receive buffer was full since the syslog
    listener started, or None when unknown (listener not started in this process, or the
    counter cannot be read). Counts every UDP socket in the network namespace.
    """
    baseline = _udp_receive_errors_baseline
    if baseline is None:
        return None
    current = read_udp_receive_buffer_errors()
    if current is None:
        return None
    return max(0, current - baseline)


# How long stop() waits for the parse worker to finish the queued messages before cancelling it.
WORKER_DRAIN_TIMEOUT = 2.0
# The parse queue shared by the UDP listener and every TCP connection: at most this many frames
# or bytes. UDP datagrams beyond either limit are dropped and counted as lost.
PARSE_QUEUE_MAX_ITEMS = 10000
PARSE_QUEUE_MAX_BYTES = 16 * 1024 * 1024
# A TCP connection that adds to a queue holding this much stops reading (the sender waits), so
# TCP never takes the room UDP needs. Paused connections read again below the resume marks.
TCP_PAUSE_ITEMS = 2000
TCP_PAUSE_BYTES = 4 * 1024 * 1024
TCP_RESUME_ITEMS = 500
TCP_RESUME_BYTES = 1024 * 1024
# Frames parsed before the worker lets other tasks run (the ingest consumer, the API, the listeners)
PARSE_SLICE = 500
# When the ingest queue is full, the worker waits this long for room (TCP senders are paused
# meanwhile). If none comes, entries that do not fit are lost as before, and the worker waits
# again only once the ingest queue is below half full, so a long stall blocks senders once.
INGEST_ROOM_WAIT = 2.0
# Free ingest places the worker waits for: one message can complete up to two entries
INGEST_ROOM = 2


class SyslogParseQueue:
    """
    The frames received by the syslog listeners, waiting to be parsed, and the one worker that
    parses them. Parsing never waits, so more workers would add no concurrency.

    UDP cannot be paused, so a datagram that does not fit is dropped and counted as lost. A TCP
    frame always goes in; once the queue passes the TCP pause marks, the connection that added it
    stops reading until the worker has brought the queue down to the resume marks.
    """

    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        alias_cache: "AliasCache",
        max_items: int = PARSE_QUEUE_MAX_ITEMS,
        max_bytes: int = PARSE_QUEUE_MAX_BYTES,
    ):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._items: deque[tuple[bytes, str]] = deque()
        self._bytes = 0
        # Paused TCP connections in the order they paused (a dict keeps that order)
        self._paused: dict["SyslogTCPProtocol", None] = {}
        self._worker: Optional[asyncio.Task] = None
        self._wakeup: Optional[asyncio.Future] = None
        self._closing = False
        self._close_task: Optional[asyncio.Task] = None
        # Set when the ingest queue stayed full for INGEST_ROOM_WAIT: entries are lost until it has room again
        self._ingest_overflowing = False

    def qsize(self) -> int:
        return len(self._items)

    @property
    def queued_bytes(self) -> int:
        return self._bytes

    @property
    def paused_connections(self) -> int:
        return len(self._paused)

    def put_udp(self, data: bytes, source_ip: str) -> bool:
        """Queue a datagram, or drop and count it when the queue is full. Returns True if queued."""
        if len(self._items) >= self.max_items or self._bytes + len(data) > self.max_bytes:
            increment_dropped_count(1)
            logger.warning(
                f"Syslog parse queue full ({len(self._items)} messages, {self._bytes} bytes). UDP packet dropped."
            )
            return False
        self._put(data, source_ip)
        return True

    def put_tcp(self, frame: bytes, source_ip: str) -> bool:
        """
        Queue a TCP frame. It always fits; returns True once the queue has reached the TCP pause
        marks, so the connection stops framing and pauses.
        """
        self._put(frame, source_ip)
        return len(self._items) >= TCP_PAUSE_ITEMS or self._bytes >= TCP_PAUSE_BYTES

    def _put(self, data: bytes, source_ip: str) -> None:
        self._items.append((data, source_ip))
        self._bytes += len(data)
        wakeup = self._wakeup
        if wakeup is not None:
            self._wakeup = None
            if not wakeup.done():
                wakeup.set_result(None)
        elif self._worker is None and not self._closing:
            self._worker = asyncio.get_running_loop().create_task(self._run())

    def pause(self, proto: "SyslogTCPProtocol") -> None:
        """Pause a TCP connection until the queue is below the resume marks."""
        if proto not in self._paused:
            self._paused[proto] = None
            proto.pause_reading()

    def forget(self, proto: "SyslogTCPProtocol") -> None:
        """A closed connection is never resumed."""
        self._paused.pop(proto, None)

    def _resume_paused(self) -> None:
        """
        Below the resume marks, resume paused connections in the order they paused. Each first
        queues the rest of its last read, so once the queue is back at the pause marks the others
        stay paused and go first next time: every busy sender gets its turn, whatever order the
        sockets are read in.
        """
        if len(self._items) >= TCP_RESUME_ITEMS or self._bytes >= TCP_RESUME_BYTES:
            return
        paused = self._paused
        while paused:
            proto = next(iter(paused))
            del paused[proto]
            proto.resume_reading()
            if len(self._items) >= TCP_PAUSE_ITEMS or self._bytes >= TCP_PAUSE_BYTES:
                break

    async def _wait_for_ingest_room(self) -> None:
        if ingest_room(INGEST_ROOM):
            if self._ingest_overflowing and ingest_room(get_queue().maxsize // 2):
                self._ingest_overflowing = False
            return
        if self._ingest_overflowing or self._closing:
            return
        if not await wait_for_ingest_room(INGEST_ROOM, INGEST_ROOM_WAIT):
            self._ingest_overflowing = True
            logger.warning(
                f"Ingest queue stayed full for {INGEST_ROOM_WAIT:g}s; syslog messages are lost until it has room again."
            )

    async def _run(self) -> None:
        items = self._items
        loop = asyncio.get_running_loop()
        while True:
            if not items:
                if self._closing:
                    break
                self._wakeup = loop.create_future()
                await self._wakeup
                continue
            for _ in range(PARSE_SLICE):
                if not items:
                    break
                data, source_ip = items.popleft()
                self._bytes -= len(data)
                await self._wait_for_ingest_room()
                try:
                    await dispatch_syslog_message(data, source_ip, self.alias_cache, self.assembler)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error in syslog parse worker: {e}")
            if self._paused:
                self._resume_paused()
            # Let the ingest consumer, the API and the listeners run between slices
            await asyncio.sleep(0)

    async def close(self) -> None:
        """
        Let the worker parse every frame already queued, then end it; after WORKER_DRAIN_TIMEOUT
        seconds it is cancelled and the rest discarded. The queue can be used again afterwards.
        Calls made while a close is running wait for that one.
        """
        if self._close_task is None:
            self._close_task = asyncio.get_running_loop().create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self._closing = True
        self._paused.clear()
        worker, self._worker = self._worker, None
        try:
            if worker is not None:
                wakeup, self._wakeup = self._wakeup, None
                if wakeup is not None and not wakeup.done():
                    wakeup.set_result(None)
                try:
                    await asyncio.wait_for(worker, timeout=WORKER_DRAIN_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Syslog parse worker did not finish within {WORKER_DRAIN_TIMEOUT}s; "
                        f"discarding {len(self._items)} queued messages."
                    )
                except Exception as e:
                    logger.error(f"Syslog parse worker failed while draining: {e}")
        finally:
            self._items.clear()
            self._bytes = 0
            self._wakeup = None
            self._closing = False
            self._ingest_overflowing = False
            self._close_task = None


async def dispatch_syslog_message(
    data: bytes,
    source_ip: str,
    alias_cache: AliasCache,
    assembler: KeyedMultilineAssembler,
) -> None:
    """
    Shared dispatch logic for UDP and TCP syslog messages:
    parsing, alias lookup, continuation/multiline stream key resolution,
    and assembler feeding.
    """
    try:
        parsed = parse_syslog_message(data, source_ip)
        # Zero-cost in-memory lookup - no DB I/O
        source_alias = alias_cache.resolve(source_ip)
        if source_alias == source_ip:
            hostname = parsed.get("hostname")
            # A hostname that is also a Docker host alias would make these rows read as container logs
            if hostname and hostname not in ("-", "unknown") and not alias_cache.is_docker_alias(hostname):
                source_alias = hostname
        parsed["source_alias"] = source_alias
        stream_key = f"{source_ip}:{parsed['app_name']}"

        if (
            parsed.get("app_name") in ("unknown", "-", "")
            or not parsed.get("app_name")
            or _RE_PYTHON_EXCEPTION.match(parsed.get("app_name", ""))
        ):
            active_key = assembler.get_active_stream_key_for_source(source_ip)
            if active_key:
                check_text = parsed.get("message", "")
                raw_text = parsed.get("raw", "")
                message_continues = assembler.continues_stream(active_key, check_text)
                if message_continues or assembler.continues_stream(active_key, raw_text):
                    stream_key = active_key
                    if not message_continues:
                        parsed["message"] = raw_text
                    parent = assembler.get_stream_parent_entry(active_key)
                    if parent:
                        parsed["app_name"] = parent.get("app_name", parsed["app_name"])
                        if "hostname" in parent:
                            parsed["hostname"] = parent["hostname"]
                        if "source_alias" in parent:
                            parsed["source_alias"] = parent["source_alias"]

        await assembler.feed(stream_key, parsed)
    except Exception as e:
        logger.error(f"Error processing syslog message: {e}")


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        alias_cache: AliasCache,
        max_queue_size: int = PARSE_QUEUE_MAX_ITEMS,
        on_lost: Optional[Callable[["SyslogUDPProtocol", Optional[Exception]], None]] = None,
        parse_queue: Optional[SyslogParseQueue] = None,
    ):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.transport = None
        # The server passes its shared queue; a protocol made on its own keeps one of its own
        self._owns_queue = parse_queue is None
        self.queue = parse_queue or SyslogParseQueue(assembler, alias_cache, max_items=max_queue_size)
        self.on_lost = on_lost
        self._stopping = False

    def connection_made(self, transport):
        self.transport = transport
        logger.info("Syslog UDP Server started")

    def connection_lost(self, exc):
        if self.on_lost:
            self.on_lost(self, exc)

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        if self._stopping:
            return
        self.queue.put_udp(data, addr[0])

    async def process_message(self, data: bytes, source_ip: str):
        await dispatch_syslog_message(data, source_ip, self.alias_cache, self.assembler)

    async def stop(self) -> None:
        """Stop receiving; a queue of its own is drained (the server drains the shared one)."""
        self._stopping = True
        if self.transport is not None:
            self.transport.close()
        if self._owns_queue:
            await self.queue.close()


MAX_TCP_BUFFER = 65536  # 64 KB limit to prevent unbounded memory growth / OOM DoS
MAX_TCP_CONNECTIONS = 250
MAX_TCP_CONNECTIONS_PER_IP = 20
TCP_INACTIVITY_TIMEOUT = 0.0


class SyslogTCPProtocol(asyncio.Protocol):
    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        alias_cache: AliasCache,
        on_close=None,
        inactivity_timeout: float = TCP_INACTIVITY_TIMEOUT,
        reject_on_connect: bool = False,
        admit: Optional[Callable[["SyslogTCPProtocol"], bool]] = None,
        parse_queue: Optional[SyslogParseQueue] = None,
    ):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.buffer = b""
        self.peername = None
        self._source_ip = "unknown"
        self.transport = None
        # The server passes its shared queue; a protocol made on its own keeps one of its own
        self._owns_queue = parse_queue is None
        self.queue = parse_queue or SyslogParseQueue(assembler, alias_cache)
        # Set while the parse queue is too full for this connection to read more
        self.paused = False
        self.on_close = on_close
        self.inactivity_timeout = inactivity_timeout
        self.reject_on_connect = reject_on_connect
        # Called once the peer address is known; returning False refuses the connection.
        self.admit = admit
        self._inactivity_handle: Optional[asyncio.TimerHandle] = None
        self._stopping = False
        # Set by connection_lost on a normal disconnect when the queue is its own: finishes the frames already queued.
        self.drain_task: Optional[asyncio.Task] = None

    @property
    def peer_ip(self) -> Optional[str]:
        return self.peername[0] if self.peername else None

    def _reset_inactivity_timer(self) -> None:
        if self._inactivity_handle:
            self._inactivity_handle.cancel()
            self._inactivity_handle = None
        if self.inactivity_timeout and self.inactivity_timeout > 0:
            try:
                loop = asyncio.get_running_loop()
                self._inactivity_handle = loop.call_later(
                    self.inactivity_timeout,
                    self._handle_inactivity_timeout,
                )
            except RuntimeError:
                pass

    def _handle_inactivity_timeout(self) -> None:
        logger.warning(
            f"Syslog TCP connection from {self.peername} timed out after {self.inactivity_timeout}s of inactivity. Closing."
        )
        if self.transport and not self.transport.is_closing():
            self.transport.close()

    def pause_reading(self) -> None:
        """Stop reading while the parse queue is full; the idle timeout waits too, as the silence is ours."""
        self.paused = True
        if self._inactivity_handle:
            self._inactivity_handle.cancel()
            self._inactivity_handle = None
        if self.transport is not None and not self.transport.is_closing():
            self.transport.pause_reading()

    def resume_reading(self) -> None:
        self.paused = False
        if self.transport is None or self.transport.is_closing():
            return
        # Frame the rest of the last read first; that can fill the queue and pause again
        if self.buffer:
            self._frame(self.buffer)
            if self.paused:
                return
        self.transport.resume_reading()
        self._reset_inactivity_timer()

    def connection_made(self, transport):
        self.transport = transport
        self.peername = transport.get_extra_info('peername')
        if self.peername:
            self._source_ip = self.peername[0]
        sock = transport.get_extra_info('socket')
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
        if self.reject_on_connect:
            logger.warning(
                f"Syslog TCP connection limit reached. Rejecting connection from {self.peername}."
            )
            transport.close()
            return
        if self.admit is not None and not self.admit(self):
            transport.close()
            return
        self._reset_inactivity_timer()
        logger.debug(f"Syslog TCP connection from {self.peername}")

    def data_received(self, data: bytes):
        if self._stopping:
            return
        if not self.paused:
            self._reset_inactivity_timer()
        self._frame(self.buffer + data if self.buffer else data)

    def _frame(self, buffer: bytes, until_full: bool = True) -> None:
        """
        Queue every complete frame in buffer and keep the rest in self.buffer. With until_full,
        framing stops once the parse queue reaches the TCP pause marks: the connection pauses and
        the rest of the read waits in self.buffer, as bytes, until it resumes.
        """
        put = self.queue.put_tcp
        source_ip = self._source_ip
        full = False
        # Frames are read by offset and the buffer is cut once at the end, so a chunk holding
        # many frames costs linear time instead of a copy of the rest per frame
        size = len(buffer)
        pos = 0
        while pos < size:
            # Skip carriage returns or newlines between frames
            if buffer[pos] in (13, 10):
                pos += 1
                continue

            # RFC 6587 octet counting: <MSG-LEN> SP <MSG> (verifying priority header prefix)
            match = _RE_OCTET_COUNTED_FRAME.match(buffer, pos)
            if match:
                msg_len = int(match.group(1))
                header_len = len(match.group(1)) + 1

                if msg_len > MAX_TCP_BUFFER:
                    logger.warning(
                        f"Syslog TCP octet-counted frame length {msg_len} exceeds limit {MAX_TCP_BUFFER} from {self.peername}. Closing connection."
                    )
                    buffer, pos, size = b"", 0, 0
                    if self.transport:
                        self.transport.close()
                    break

                frame_end = pos + header_len + msg_len
                if size < frame_end:
                    # Incomplete frame; wait for subsequent chunks
                    break

                frame = buffer[pos + header_len:frame_end]
                pos = frame_end
                if frame and put(frame, source_ip) and until_full:
                    full = True
                    break
            else:
                # Non-transparent framing: trailer/newline delimitation (\n)
                newline = buffer.find(b"\n", pos)
                if newline < 0:
                    # No newline found and does not match octet framing; wait for more data
                    break
                line = buffer[pos:newline].rstrip(b"\r")
                pos = newline + 1
                if line and put(line, source_ip) and until_full:
                    full = True
                    break

        self.buffer = buffer[pos:] if pos else buffer
        if full:
            # The rest may hold whole frames, so the unframed size limit does not apply yet
            self.queue.pause(self)
            return
        if len(self.buffer) > MAX_TCP_BUFFER:
            logger.warning(
                f"Syslog TCP buffer exceeded {MAX_TCP_BUFFER} bytes without frame boundary from {self.peername}. Closing connection."
            )
            self.buffer = b""
            if self.transport:
                self.transport.close()

    def _queue_rest(self) -> None:
        """On close: queue the whole frames still waiting in the buffer of a paused connection."""
        if self.paused and self.buffer:
            self._frame(self.buffer, until_full=False)

    async def process_message(self, data: bytes, source_ip: str):
        await dispatch_syslog_message(data, source_ip, self.alias_cache, self.assembler)

    def connection_lost(self, exc):
        if self._inactivity_handle:
            self._inactivity_handle.cancel()
            self._inactivity_handle = None
        logger.debug(f"Syslog TCP connection lost from {self.peername}")
        self._queue_rest()
        self.queue.forget(self)
        if not self._stopping:
            # A normal disconnect (during stop() the queue is drained there instead). Frames
            # already read stay queued and are parsed; a queue of its own is drained in a
            # background task, bounded by WORKER_DRAIN_TIMEOUT, so this callback does not block.
            self._stopping = True
            if self._owns_queue:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    self.drain_task = loop.create_task(self.queue.close())
        if self.on_close:
            self.on_close(self)

    async def stop(self) -> None:
        """Close the connection; a queue of its own is drained (the server drains the shared one)."""
        self._stopping = True
        if self._inactivity_handle:
            self._inactivity_handle.cancel()
            self._inactivity_handle = None
        self._queue_rest()
        if self.transport is not None:
            self.transport.close()
        self.queue.forget(self)
        if self._owns_queue:
            await self.queue.close()



class SyslogServer:
    MAX_TCP_CONNECTIONS = MAX_TCP_CONNECTIONS

    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        db_path: str | Path,
        host: str = '0.0.0.0',
        port: int = 1514,
        max_tcp_connections: int = MAX_TCP_CONNECTIONS,
        tcp_inactivity_timeout: float = TCP_INACTIVITY_TIMEOUT,
        max_tcp_connections_per_ip: int = MAX_TCP_CONNECTIONS_PER_IP,
    ):
        self.assembler = assembler
        self.db_path = db_path
        self.host = host
        self.port = port
        self.max_tcp_connections = max_tcp_connections
        self.tcp_inactivity_timeout = tcp_inactivity_timeout
        self.max_tcp_connections_per_ip = max(1, max_tcp_connections_per_ip)
        self.udp_transport = None
        self.udp_protocol: SyslogUDPProtocol | None = None
        self.tcp_server = None
        self.tcp_protocols: set[SyslogTCPProtocol] = set()
        self.alias_cache = AliasCache(db_path, preload=False)
        # One parse queue and worker for the UDP listener and every TCP connection
        self.parse_queue = SyslogParseQueue(assembler, self.alias_cache)
        # Set once by stop(); a stopped server never binds again.
        self._stopped = False
        # Resolved by stop() (normal end) or failed by an unexpected listener loss.
        self._run_future: Optional[asyncio.Future] = None

    @property
    def active_tcp_connections(self) -> int:
        return len(self.tcp_protocols)

    def update_limits(
        self,
        max_connections: Optional[int] = None,
        inactivity_timeout: Optional[float] = None,
    ) -> None:
        """
        Dynamically update concurrent TCP connection limit and client inactivity timeout.
        Live connections update their timers immediately; new connection ceilings apply
        to subsequent inbound sockets.
        """
        changed = False
        if max_connections is not None:
            new_max = max(1, max_connections)
            if new_max != self.max_tcp_connections:
                self.max_tcp_connections = new_max
                changed = True

        if inactivity_timeout is not None:
            new_timeout = max(0.0, inactivity_timeout)
            if new_timeout != self.tcp_inactivity_timeout:
                self.tcp_inactivity_timeout = new_timeout
                for proto in list(self.tcp_protocols):
                    proto.inactivity_timeout = self.tcp_inactivity_timeout
                    proto._reset_inactivity_timer()
                changed = True

        if changed:
            logger.info(
                f"SyslogServer limits updated: max_tcp_connections={self.max_tcp_connections}, "
                f"tcp_inactivity_timeout={self.tcp_inactivity_timeout}s"
            )

    def _remove_tcp_protocol(self, proto: SyslogTCPProtocol) -> None:
        self.tcp_protocols.discard(proto)

    def _create_tcp_protocol(self) -> SyslogTCPProtocol:
        if len(self.tcp_protocols) >= self.max_tcp_connections:
            logger.warning(
                f"Syslog TCP connection limit reached ({self.max_tcp_connections}). Rejecting new connection."
            )
            return SyslogTCPProtocol(
                self.assembler,
                self.alias_cache,
                on_close=self._remove_tcp_protocol,
                reject_on_connect=True,
                parse_queue=self.parse_queue,
            )

        proto = SyslogTCPProtocol(
            self.assembler,
            self.alias_cache,
            on_close=self._remove_tcp_protocol,
            inactivity_timeout=self.tcp_inactivity_timeout,
            admit=self._admit_tcp_connection,
            parse_queue=self.parse_queue,
        )
        self.tcp_protocols.add(proto)
        return proto

    def _admit_tcp_connection(self, proto: SyslogTCPProtocol) -> bool:
        """Accept-time check once the peer address is known: refuse while stopping or over the per-IP cap."""
        if self._stopped:
            self.tcp_protocols.discard(proto)
            return False
        ip = proto.peer_ip
        if ip is None:
            return True
        same_ip = sum(1 for p in self.tcp_protocols if p is not proto and p.peer_ip == ip)
        if same_ip >= self.max_tcp_connections_per_ip:
            self.tcp_protocols.discard(proto)
            logger.warning(
                f"Syslog TCP per-IP connection limit reached ({self.max_tcp_connections_per_ip}) "
                f"for {ip}. Rejecting new connection."
            )
            return False
        return True

    def _on_udp_lost(self, proto: SyslogUDPProtocol, exc: Optional[Exception]) -> None:
        """A UDP listener closed by anything other than stop() fails the running start()."""
        if self._stopped or proto is not self.udp_protocol:
            return
        fut = self._run_future
        if fut is not None and not fut.done():
            fut.set_exception(OSError(f"Syslog UDP listener closed unexpectedly: {exc}"))

    async def _close_listeners(self) -> None:
        """Close both listeners and every TCP connection, then drain the parse queue. Idempotent."""
        # Detach everything first, so a concurrent caller finds nothing left to close.
        tcp_server, self.tcp_server = self.tcp_server, None
        udp_protocol, self.udp_protocol = self.udp_protocol, None
        udp_transport, self.udp_transport = self.udp_transport, None
        protocols = list(self.tcp_protocols)
        self.tcp_protocols.clear()

        if tcp_server is not None:
            tcp_server.close()  # stop accepting new connections
        if udp_transport is not None:
            udp_transport.close()

        closes: list[Any] = [proto.stop() for proto in protocols]
        if udp_protocol is not None:
            closes.append(udp_protocol.stop())
        if closes:
            await asyncio.gather(*closes, return_exceptions=True)
        # Bounded by WORKER_DRAIN_TIMEOUT
        await self.parse_queue.close()

        if tcp_server is not None:
            # Every client transport is closed above, so this returns once they detach.
            try:
                await asyncio.wait_for(tcp_server.wait_closed(), timeout=WORKER_DRAIN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Syslog TCP server did not close all client connections in time.")

    async def start(self) -> None:
        """
        Start the alias cache refresh, bind the UDP and TCP listeners, then run until stop().

        A bind failure (such as the port being in use) raises after closing whatever was bound,
        so the worker supervisor retries with backoff. An unexpected loss of the UDP listener
        also raises. A normal return means stop() was called.
        """
        if self._stopped:
            return
        loop = asyncio.get_running_loop()

        # Pre-load aliases before starting listeners (no-op restart while already running)
        await self.alias_cache.start()
        if self._stopped:  # stop() ran during the alias load
            await self.alias_cache.stop()
            return

        self._run_future = loop.create_future()
        try:
            try:
                udp_protocol = SyslogUDPProtocol(
                    self.assembler, self.alias_cache, on_lost=self._on_udp_lost,
                    parse_queue=self.parse_queue,
                )
                self.udp_protocol = udp_protocol
                self.udp_transport, _ = await loop.create_datagram_endpoint(
                    lambda: udp_protocol,
                    local_addr=(self.host, self.port)
                )
            except Exception as e:
                logger.error(f"Failed to start Syslog UDP server on {self.host}:{self.port}: {e}")
                raise
            logger.info(f"Started Syslog UDP server on {self.host}:{self.port}")
            configure_udp_receive_buffer(
                self.udp_transport.get_extra_info("socket"), get_syslog_udp_receive_buffer_bytes()
            )
            record_udp_receive_errors_baseline()

            try:
                self.tcp_server = await loop.create_server(
                    self._create_tcp_protocol,
                    self.host, self.port
                )
            except Exception as e:
                logger.error(f"Failed to start Syslog TCP server on {self.host}:{self.port}: {e}")
                raise
            logger.info(f"Started Syslog TCP server on {self.host}:{self.port}")

            if not self._stopped:  # stop() may have run while a bind was in progress
                await self._run_future
        finally:
            self._run_future = None
            await self._close_listeners()

    async def stop(self) -> None:
        """Stop for good: end start(), close listeners and connections after draining, stop alias refresh."""
        self._stopped = True
        fut = self._run_future
        if fut is not None and not fut.done():
            fut.set_result(None)

        await self._close_listeners()
        await self.alias_cache.stop()

        logger.info("Syslog servers stopped")
