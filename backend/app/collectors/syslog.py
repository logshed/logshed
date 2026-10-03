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
import threading
import weakref
from pathlib import Path
from typing import Any, Optional

from app.core.migrations import get_connection
from app.core.pipeline import (
    KeyedMultilineAssembler,
    detect_severity,
    SEVERITY_LEVEL_MAP,
    _is_continuation,
    _RE_PYTHON_EXCEPTION,
)

logger = logging.getLogger(__name__)

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

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
        day = int(day_str)
        month = _MONTH_MAP.get(mon_str.lower(), 1)
        year = int(year_str)
        time_clean = time_str.replace(",", ".")
        time_parts = time_clean.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        sec_parts = time_parts[2].split(".")
        second = int(sec_parts[0])
        microsecond = int(float("0." + sec_parts[1]) * 1_000_000) if len(sec_parts) > 1 else 0
        dt = datetime.datetime(year, month, day, hour, minute, second, microsecond)
        candidate_a = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
        candidate_b = dt.replace(tzinfo=datetime.timezone.utc)
        if abs((candidate_b - now).total_seconds()) < abs((candidate_a - now).total_seconds()):
            dt_utc = candidate_b
        else:
            dt_utc = candidate_a
        return dt_utc.isoformat()
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
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2])
        time_clean = time_str.replace(",", ".")
        time_parts = time_clean.split(":")
        hour = int(time_parts[0])
        minute = int(time_parts[1])
        sec_parts = time_parts[2].split(".")
        second = int(sec_parts[0])
        microsecond = int(float("0." + sec_parts[1]) * 1_000_000) if len(sec_parts) > 1 else 0
        dt = datetime.datetime(year, month, day, hour, minute, second, microsecond)
        candidate_a = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
        candidate_b = dt.replace(tzinfo=datetime.timezone.utc)
        if abs((candidate_b - now).total_seconds()) < abs((candidate_a - now).total_seconds()):
            dt_utc = candidate_b
        else:
            dt_utc = candidate_a
        return dt_utc.isoformat()
    except Exception:
        return f"{dmy_str}T{time_str}"

def _parse_iso_or_datetime(
    ts_str: str,
    now: datetime.datetime,
    local_tz: datetime.tzinfo,
) -> str:
    """Parse an ISO 8601, RFC 3339, or standard datetime string to UTC ISO format."""
    ts_clean = ts_str.strip().replace("Z", "+00:00").replace("/", "-")
    ts_clean = re.sub(r"\s+(?:UTC|GMT)\b", "+00:00", ts_clean, flags=re.IGNORECASE)
    # Strip any remaining timezone abbreviations like BST, EST, PDT
    ts_clean = re.sub(r"\s+[A-Z]{2,5}(?=$|[+-])", "", ts_clean)
    if "," in ts_clean:
        ts_clean = ts_clean.replace(",", ".")
    if " " in ts_clean and "T" not in ts_clean:
        ts_clean = ts_clean.replace(" ", "T", 1)
    try:
        dt = datetime.datetime.fromisoformat(ts_clean)
        if dt.tzinfo is None:
            candidate_a = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
            candidate_b = dt.replace(tzinfo=datetime.timezone.utc)
            if abs((candidate_b - now).total_seconds()) < abs((candidate_a - now).total_seconds()):
                dt_utc = candidate_b
            else:
                dt_utc = candidate_a
        else:
            dt_utc = dt.astimezone(datetime.timezone.utc)
        diff_seconds = (dt_utc - now).total_seconds()
        if diff_seconds > 60:
            offset_hours = round(diff_seconds / 3600)
            if offset_hours > 0:
                dt_utc -= datetime.timedelta(hours=offset_hours)
            if (dt_utc - now).total_seconds() > 60:
                dt_utc = now
        return dt_utc.isoformat()
    except Exception:
        return ts_str


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
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now = now.astimezone(datetime.timezone.utc)

    if local_tz is None:
        local_tz = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
    
    result = {
        "timestamp": now.isoformat(),
        "received_at": now.isoformat(),
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
    pri_match = re.match(r"^<(\d{1,3})>", content)
    if pri_match:
        has_pri = True
        pri = int(pri_match.group(1))
        result["facility"] = pri // 8
        result["severity"] = pri % 8
        content = content[pri_match.end():]
        
    def _parse_msg_into_app_and_content(msg_part: str) -> None:
        # 1. TAG[PID]: CONTENT or TAG: CONTENT
        m = re.match(r"^([^:\s\[]+?)(?:\[\d+\])?:\s*(.*)", msg_part)
        if m:
            result["app_name"] = m.group(1)
            result["message"] = m.group(2)
            return
        # 2. TAG[PID] CONTENT (PID without colon)
        m = re.match(r"^([^:\s\[]+)\[\d+\]\s+(.*)", msg_part)
        if m:
            result["app_name"] = m.group(1)
            result["message"] = m.group(2)
            return
        # 3. [TAG]: CONTENT or [TAG] CONTENT (bracketed process tag, e.g. [kernel])
        m = re.match(r"^\[([^\]]+)\]:?\s*(.*)", msg_part)
        if m:
            if m.group(1).lower() not in SEVERITY_LEVEL_MAP:
                result["app_name"] = m.group(1)
                result["message"] = m.group(2)
                return
        # 4. TAG CONTENT (space-separated, no colon)
        m = re.match(r"^([^:\s]+)\s+(.*)", msg_part)
        if m:
            if m.group(1).lower() not in SEVERITY_LEVEL_MAP:
                result["app_name"] = m.group(1)
                result["message"] = m.group(2)
                return
        # 5. Fallback: single word or unparsed
        result["message"] = msg_part

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
                sd = "-"
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
                    sd = ""
                    msg = remainder
                else:
                    sd = remainder[:sd_end]
                    msg = remainder[sd_end:].lstrip(" ")
            else:
                # Malformed SD - treat entire remainder as message
                sd = ""
                msg = remainder

            if timestamp != "-":
                try:
                    ts_clean = timestamp.replace("Z", "+00:00")
                    dt = datetime.datetime.fromisoformat(ts_clean)
                    if dt.tzinfo is None:
                        candidate_a = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
                        candidate_b = dt.replace(tzinfo=datetime.timezone.utc)
                        if abs((candidate_b - now).total_seconds()) < abs((candidate_a - now).total_seconds()):
                            dt_utc = candidate_b
                        else:
                            dt_utc = candidate_a
                    else:
                        dt_utc = dt.astimezone(datetime.timezone.utc)
                    diff_seconds = (dt_utc - now).total_seconds()
                    if diff_seconds > 60:
                        offset_hours = round(diff_seconds / 3600)
                        if offset_hours > 0:
                            dt_utc -= datetime.timedelta(hours=offset_hours)
                        if (dt_utc - now).total_seconds() > 60:
                            dt_utc = now
                    result["timestamp"] = dt_utc.isoformat()
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
    elif (ts_match := re.match(r"^([A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+", content)):
        # RFC 3164: Mmm dd HH:MM:SS or Mmm  d HH:MM:SS
        ts_str = ts_match.group(1)
        content = content[ts_match.end():]
        
        # Try to parse timestamp
        try:
            # Add current year since RFC 3164 doesn't include it
            # Handle space-padded day
            ts_str_clean = re.sub(r'\s+', ' ', ts_str)
            dt = datetime.datetime.strptime(f"{now.year} {ts_str_clean}", "%Y %b %d %H:%M:%S")
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
            candidate_a = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
            candidate_b = dt.replace(tzinfo=datetime.timezone.utc)

            diff_a = abs((candidate_a - now).total_seconds())
            diff_b = abs((candidate_b - now).total_seconds())

            if diff_b < diff_a:
                dt_utc = candidate_b
            else:
                dt_utc = candidate_a

            diff_seconds = (dt_utc - now).total_seconds()
            if diff_seconds > 60:
                offset_hours = round(diff_seconds / 3600)
                if offset_hours > 0:
                    dt_utc -= datetime.timedelta(hours=offset_hours)
                if (dt_utc - now).total_seconds() > 60:
                    dt_utc = now
            
            # Preserve microsecond arrival precision for proper sub-second ordering
            dt_utc = dt_utc.replace(microsecond=now.microsecond)
            result["timestamp"] = dt_utc.isoformat()
        except ValueError:
            pass

        # RFC 3164 Section 4.1.2 / 4.1.3: The HOSTNAME field is optional.
        parts = content.split(" ", 1)
        first_token = parts[0]
        is_nil_hostname = (len(parts) == 2 and first_token == "-")
        is_valid_hostname = (
            len(parts) == 2
            and bool(first_token)
            and first_token != "-"
            and "[" not in first_token
            and "]" not in first_token
            and first_token.lower() not in SEVERITY_LEVEL_MAP
            and not (first_token.endswith(":") and not first_token.endswith("::"))
        )

        if is_valid_hostname:
            result["hostname"] = first_token
            _parse_msg_into_app_and_content(parts[1])
        elif is_nil_hostname:
            _parse_msg_into_app_and_content(parts[1])
        else:
            _parse_msg_into_app_and_content(content)
    elif (m_bracket := re.match(r"^\[(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?)\]\s*", content)):
        # Bracketed timestamp envelope: [TIMESTAMP] [HOST] [APP] MSG or [TIMESTAMP] MSG
        result["timestamp"] = _parse_iso_or_datetime(m_bracket.group(1), now, local_tz)
        rem = content[m_bracket.end():]
        m_env = re.match(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s*(.*)", rem)
        if m_env:
            result["hostname"] = m_env.group(1)
            result["app_name"] = m_env.group(2)
            result["message"] = m_env.group(3)
        else:
            m_host = re.match(r"^\[([^\]]+)\]\s*(.*)", rem)
            if m_host and m_host.group(1).lower() not in SEVERITY_LEVEL_MAP:
                result["hostname"] = m_host.group(1)
                _parse_msg_into_app_and_content(m_host.group(2))
            else:
                _parse_msg_into_app_and_content(rem)
    elif (m_iso := re.match(r"^(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?)\s+", content)):
        # Unbracketed ISO 8601 / RFC 3339 / standard datetime: TIMESTAMP HOSTNAME TAG: MSG
        result["timestamp"] = _parse_iso_or_datetime(m_iso.group(1), now, local_tz)
        rem = content[m_iso.end():]
        parts = rem.split(" ", 1)
        first_token = parts[0]
        is_valid_hostname = (
            len(parts) == 2
            and bool(first_token)
            and first_token != "-"
            and "[" not in first_token
            and "]" not in first_token
            and first_token.lower() not in SEVERITY_LEVEL_MAP
            and not (first_token.endswith(":") and not first_token.endswith("::"))
        )
        if is_valid_hostname:
            result["hostname"] = first_token
            _parse_msg_into_app_and_content(parts[1])
        elif len(parts) == 2 and first_token == "-":
            _parse_msg_into_app_and_content(parts[1])
        else:
            _parse_msg_into_app_and_content(rem)
    elif (m_valkey := re.match(r"^(\d+:[a-zA-Z])\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+([#*.-])\s+(.*)", content)):
        # Valkey / Redis server format: <pid>:<role> <day> <mon> <year> <time> <level_char> <msg>
        result["timestamp"] = _parse_valkey_datetime(
            m_valkey.group(2), m_valkey.group(3), m_valkey.group(4), m_valkey.group(5), now, local_tz
        )
        level_char = m_valkey.group(6)
        valkey_sev_map = {"#": 4, "*": 5, ".": 7, "-": 7}
        result["severity"] = valkey_sev_map.get(level_char, 6)
        explicit_severity = True
        result["message"] = m_valkey.group(7)
    elif (m_app_pipe := re.match(r"^\[([^\]]+)\]\s*\|\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\s+(.*)", content)):
        # Application-prefixed pipe format: [maintainerr] | 03/10/2026 16:00:33 [INFO] ...
        result["app_name"] = m_app_pipe.group(1)
        result["timestamp"] = _parse_dmy_or_datetime(
            m_app_pipe.group(2), m_app_pipe.group(3), now, local_tz
        )
        rem = m_app_pipe.group(4)
        m_level = re.match(r"^\[([a-zA-Z]+)\]\s*(.*)", rem)
        if m_level and m_level.group(1).lower() in SEVERITY_LEVEL_MAP:
            result["severity"] = SEVERITY_LEVEL_MAP[m_level.group(1).lower()]
            explicit_severity = True
            result["message"] = m_level.group(2)
        else:
            result["message"] = rem
    elif (m_logfmt := re.match(r"""^(?:time|ts)=["']?([^"'\s]+)["']?\s+(.*)""", content)):
        # Logfmt format: time="2026-10-03T14:56:31Z" level=info msg="..."
        result["timestamp"] = _parse_iso_or_datetime(m_logfmt.group(1), now, local_tz)
        rem = m_logfmt.group(2)
        m_level = re.search(r"""\b(?:level|lvl|severity)=["']?([a-zA-Z]+)["']?""", rem)
        if m_level and m_level.group(1).lower() in SEVERITY_LEVEL_MAP:
            result["severity"] = SEVERITY_LEVEL_MAP[m_level.group(1).lower()]
            explicit_severity = True
        m_msg = re.search(r"""\bmsg=(?:"([^"]*)"|'([^']*)'|(\S+))""", rem)
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
        self._lock = threading.Lock()
        self._refresh_task: asyncio.Task | None = None
        if preload:
            self.load_aliases()
        _active_caches.add(self)

    def resolve(self, source_ip: str) -> str:
        """Look up source_ip in the preloaded alias map. O(1) dict lookup."""
        with self._lock:
            return self._aliases.get(source_ip, source_ip)

    def get_alias(self, source_ip: str) -> str:
        """Alias for resolve() to support get_alias interface."""
        return self.resolve(source_ip)

    def load_aliases(self) -> None:
        """Synchronous: bulk-load all aliases from host_aliases table."""
        new_aliases: dict[str, str] = {}
        try:
            conn = get_connection(self._db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT ip, alias FROM host_aliases")
                for row in cursor.fetchall():
                    new_aliases[row[0]] = row[1]
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

    async def start(self) -> None:
        """Start the periodic refresh background task."""
        # Initial synchronous load
        await asyncio.to_thread(self.load_aliases)
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


NUM_PARSER_WORKERS = 4


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
            if hostname and hostname not in ("-", "unknown"):
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
                buf_text = assembler.get_buffered_text(active_key)
                check_text = parsed.get("message", "")
                raw_text = parsed.get("raw", "")
                if _is_continuation(check_text, buf_text) or _is_continuation(raw_text, buf_text):
                    stream_key = active_key
                    if not _is_continuation(check_text, buf_text) and _is_continuation(raw_text, buf_text):
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
        max_queue_size: int = 5000,
        num_workers: int = NUM_PARSER_WORKERS,
    ):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.transport = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self.num_workers = num_workers
        self._workers: list[asyncio.Task] = []

    def _ensure_workers(self) -> None:
        if not self._workers:
            for _ in range(self.num_workers):
                self._workers.append(asyncio.create_task(self._worker_loop()))

    async def _worker_loop(self) -> None:
        while True:
            try:
                item = await self.queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                self.queue.task_done()
                break

            try:
                data, source_ip = item
                await self.process_message(data, source_ip)
            except asyncio.CancelledError:
                self.queue.task_done()
                break
            except Exception as e:
                logger.error(f"Error in Syslog UDP worker: {e}")
                self.queue.task_done()
            else:
                self.queue.task_done()

    def connection_made(self, transport):
        self.transport = transport
        self._ensure_workers()
        logger.info("Syslog UDP Server started")

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        source_ip = addr[0]
        self._ensure_workers()
        try:
            self.queue.put_nowait((data, source_ip))
        except asyncio.QueueFull:
            from app.core.pipeline import increment_dropped_count
            increment_dropped_count(1)
            logger.warning("Syslog UDP queue full (5000 items). Packet dropped.")
        
    async def process_message(self, data: bytes, source_ip: str):
        await dispatch_syslog_message(data, source_ip, self.alias_cache, self.assembler)

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()


MAX_TCP_BUFFER = 65536  # 64 KB limit to prevent unbounded memory growth / OOM DoS
MAX_TCP_CONNECTIONS = 250
TCP_INACTIVITY_TIMEOUT = 0.0


class SyslogTCPProtocol(asyncio.Protocol):
    def __init__(
        self,
        assembler: KeyedMultilineAssembler,
        alias_cache: AliasCache,
        max_queue_size: int = 5000,
        num_workers: int = NUM_PARSER_WORKERS,
        on_close=None,
        inactivity_timeout: float = TCP_INACTIVITY_TIMEOUT,
        reject_on_connect: bool = False,
    ):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.buffer = b""
        self.peername = None
        self.transport = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self.num_workers = num_workers
        self._workers: list[asyncio.Task] = []
        self.on_close = on_close
        self.inactivity_timeout = inactivity_timeout
        self.reject_on_connect = reject_on_connect
        self._inactivity_handle: Optional[asyncio.TimerHandle] = None

    def _ensure_workers(self) -> None:
        if not self._workers:
            for _ in range(self.num_workers):
                self._workers.append(asyncio.create_task(self._worker_loop()))

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

    async def _worker_loop(self) -> None:
        while True:
            try:
                item = await self.queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                self.queue.task_done()
                break

            try:
                data, source_ip = item
                await self.process_message(data, source_ip)
            except asyncio.CancelledError:
                self.queue.task_done()
                break
            except Exception as e:
                logger.error(f"Error in Syslog TCP worker: {e}")
                self.queue.task_done()
            else:
                self.queue.task_done()

    def connection_made(self, transport):
        self.transport = transport
        self.peername = transport.get_extra_info('peername')
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
        self._ensure_workers()
        self._reset_inactivity_timer()
        logger.debug(f"Syslog TCP connection from {self.peername}")

    def data_received(self, data: bytes):
        self._reset_inactivity_timer()
        self.buffer += data
        while self.buffer:
            # Strip leading carriage returns or newlines between frames
            if self.buffer.startswith(b"\r") or self.buffer.startswith(b"\n"):
                self.buffer = self.buffer.lstrip(b"\r\n")
                if not self.buffer:
                    break

            # RFC 6587 octet counting: <MSG-LEN> SP <MSG>
            match = re.match(rb"^(\d+) ", self.buffer)
            if match:
                msg_len = int(match.group(1))
                header_len = match.end()

                if msg_len > MAX_TCP_BUFFER:
                    logger.warning(
                        f"Syslog TCP octet-counted frame length {msg_len} exceeds limit {MAX_TCP_BUFFER} from {self.peername}. Closing connection."
                    )
                    self.buffer = b""
                    if self.transport:
                        self.transport.close()
                    break

                if len(self.buffer) < header_len + msg_len:
                    # Incomplete frame; wait for subsequent chunks
                    break

                frame = self.buffer[header_len : header_len + msg_len]
                self.buffer = self.buffer[header_len + msg_len :]

                if frame:
                    source_ip = self.peername[0] if self.peername else "unknown"
                    self._ensure_workers()
                    try:
                        self.queue.put_nowait((frame, source_ip))
                    except asyncio.QueueFull:
                        from app.core.pipeline import increment_dropped_count
                        increment_dropped_count(1)
                        logger.warning("Syslog TCP queue full (5000 items). Message dropped.")
            else:
                # Non-transparent framing: trailer/newline delimitation (\n)
                if b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    line = line.rstrip(b"\r")
                    if line:
                        source_ip = self.peername[0] if self.peername else "unknown"
                        self._ensure_workers()
                        try:
                            self.queue.put_nowait((line, source_ip))
                        except asyncio.QueueFull:
                            from app.core.pipeline import increment_dropped_count
                            increment_dropped_count(1)
                            logger.warning("Syslog TCP queue full (5000 items). Message dropped.")
                else:
                    # No newline found and does not match octet framing; wait for more data
                    break

        if len(self.buffer) > MAX_TCP_BUFFER:
            logger.warning(
                f"Syslog TCP buffer exceeded {MAX_TCP_BUFFER} bytes without frame boundary from {self.peername}. Closing connection."
            )
            self.buffer = b""
            if self.transport:
                self.transport.close()
                
    async def process_message(self, data: bytes, source_ip: str):
        await dispatch_syslog_message(data, source_ip, self.alias_cache, self.assembler)

    def connection_lost(self, exc):
        if self._inactivity_handle:
            self._inactivity_handle.cancel()
            self._inactivity_handle = None
        logger.debug(f"Syslog TCP connection lost from {self.peername}")
        for task in self._workers:
            task.cancel()
        self._workers.clear()
        if self.on_close:
            self.on_close(self)

    async def stop(self) -> None:
        if self._inactivity_handle:
            self._inactivity_handle.cancel()
            self._inactivity_handle = None
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()


# Alias for backward compatibility / explicit naming
SyslogTCPServerProtocol = SyslogTCPProtocol


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
    ):
        self.assembler = assembler
        self.db_path = db_path
        self.host = host
        self.port = port
        self.max_tcp_connections = max_tcp_connections
        self.tcp_inactivity_timeout = tcp_inactivity_timeout
        self.udp_transport = None
        self.udp_protocol: SyslogUDPProtocol | None = None
        self.tcp_server = None
        self.tcp_protocols: set[SyslogTCPProtocol] = set()
        self.alias_cache = AliasCache(db_path, preload=False)

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

    async def start(self) -> None:
        """Create and start alias cache refresh, then both UDP and TCP transports."""
        loop = asyncio.get_running_loop()

        # Pre-load aliases before starting listeners
        await self.alias_cache.start()
        
        # Start UDP
        try:
            self.udp_protocol = SyslogUDPProtocol(self.assembler, self.alias_cache)
            self.udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: self.udp_protocol,
                local_addr=(self.host, self.port)
            )
            logger.info(f"Started Syslog UDP server on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start Syslog UDP server: {e}")

        # Start TCP
        def _remove_tcp_protocol(proto: SyslogTCPProtocol):
            self.tcp_protocols.discard(proto)

        def _create_tcp_protocol():
            if len(self.tcp_protocols) >= self.max_tcp_connections:
                logger.warning(
                    f"Syslog TCP connection limit reached ({self.max_tcp_connections}). Rejecting new connection."
                )
                return SyslogTCPProtocol(
                    self.assembler,
                    self.alias_cache,
                    on_close=_remove_tcp_protocol,
                    reject_on_connect=True,
                )

            proto = SyslogTCPProtocol(
                self.assembler,
                self.alias_cache,
                on_close=_remove_tcp_protocol,
                inactivity_timeout=self.tcp_inactivity_timeout,
            )
            self.tcp_protocols.add(proto)
            return proto

        try:
            self.tcp_server = await loop.create_server(
                _create_tcp_protocol,
                self.host, self.port
            )
            logger.info(f"Started Syslog TCP server on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start Syslog TCP server: {e}")

    async def stop(self) -> None:
        """Close transports and stop alias cache refresh."""
        await self.alias_cache.stop()

        if self.udp_protocol:
            await self.udp_protocol.stop()

        if self.udp_transport:
            self.udp_transport.close()

        for proto in list(self.tcp_protocols):
            await proto.stop()
        self.tcp_protocols.clear()

        if self.tcp_server:
            self.tcp_server.close()
            await self.tcp_server.wait_closed()
            
        logger.info("Syslog servers stopped")

