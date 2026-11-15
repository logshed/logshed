"""
"Check last 24h" for alert and drop rules: how many stored logs a rule would have matched.

The rule's own matcher (CompiledAlertRule or CompiledDropRule, with the regex per-match timeout)
runs over the stored logs of the last 24 hours, newest first, so the answer is what the rule
does, not what a Console search would find. The walk stops at ROW_CAP logs checked or after
TIME_CAP_SECONDS, whichever comes first, and then says how far back it reached. One check runs
at a time on the server.
"""

from dataclasses import dataclass, field
import datetime
import threading
import time
from typing import Any, Callable, Optional

from app.core.day_files import KIND_INTERNAL
from app.core.pipeline import InternalLogHandler
from app.models import RulePreviewResponse
from app.services import log_store
from app.services.log_query import LogFilter, row_to_log_entry, to_utc_bound

PREVIEW_HOURS = 24
# Python runs the matcher at a few microseconds per log: about one to two seconds for this many
ROW_CAP = 200_000
TIME_CAP_SECONDS = 5.0
PAGE_SIZE = 2_000
EXAMPLES_KEPT = 5

# The app names of LogShed's own logs that alert rules never see (InternalLogHandler stores them
# under the last part of the logger's name, with source_kind internal)
ALERT_EXEMPT_APPS = frozenset(name.rsplit(".", 1)[-1] for name in InternalLogHandler.ALERT_EXEMPT_LOGGERS)

_running = threading.Lock()

PREVIEW_BUSY_DETAIL = "Another check of the last 24 hours is running. Try again in a few seconds."


class PreviewBusy(Exception):
    """Another check is running."""


@dataclass
class PreviewResult:
    """What a check found. oldest_checked is the time of the oldest log checked (ISO, UTC)."""

    matched: int = 0
    checked: int = 0
    # True when every log of the window was checked; else stopped_by says why it stopped
    complete: bool = True
    stopped_by: Optional[str] = None
    window_start: str = ""
    oldest_checked: Optional[str] = None
    pattern_timeouts: int = 0
    examples: list = field(default_factory=list)


def is_alert_exempt(row: dict[str, Any]) -> bool:
    """True for a stored LogShed log written by the alert, notifier or AI code, which alert rules skip."""
    if row.get("app_name") not in ALERT_EXEMPT_APPS:
        return False
    kind = row.get("source_kind")
    if kind == KIND_INTERNAL:
        return True
    # Rows stored before logs had a kind: LogShed's own logs come from the loopback address
    return not kind and row.get("source_ip") == "127.0.0.1"


def run_preview(
    conn,
    matches: Callable[[dict[str, Any]], bool],
    *,
    severity_max: Optional[int] = None,
    skip: Optional[Callable[[dict[str, Any]], bool]] = None,
    now: Optional[datetime.datetime] = None,
    row_cap: int = ROW_CAP,
    time_cap: float = TIME_CAP_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> PreviewResult:
    """
    Run matches over the stored logs of the last PREVIEW_HOURS, newest first. severity_max narrows
    the logs read to those at that level or more severe (a filter the matcher would apply too);
    skip leaves logs out without counting them as checked. The newest EXAMPLES_KEPT matches are
    kept as rows. Raises PreviewBusy while another check runs.
    """
    if not _running.acquire(blocking=False):
        raise PreviewBusy()
    try:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        window_start = to_utc_bound((now - datetime.timedelta(hours=PREVIEW_HOURS)).isoformat())
        log_filter = LogFilter(severity_max=severity_max, from_ts=window_start)
        result = PreviewResult(window_start=window_start)
        deadline = clock() + time_cap
        for page in log_store.walk_newest(conn, log_filter, PAGE_SIZE):
            for row in page:
                if result.checked >= row_cap:
                    result.stopped_by = "rows"
                elif clock() >= deadline:
                    result.stopped_by = "time"
                if result.stopped_by:
                    result.complete = False
                    return result
                entry = dict(row)
                if skip is not None and skip(entry):
                    continue
                result.checked += 1
                result.oldest_checked = str(entry["timestamp"])
                if matches(entry):
                    result.matched += 1
                    if len(result.examples) < EXAMPLES_KEPT:
                        result.examples.append(row)
        return result
    finally:
        _running.release()


def preview_response(result: PreviewResult) -> RulePreviewResponse:
    """The web UI's form of a result, with the newest matches as stored (as the Console shows them)."""
    return RulePreviewResponse(
        matched=result.matched,
        checked=result.checked,
        complete=result.complete,
        stopped_by=result.stopped_by,
        row_cap=ROW_CAP,
        window_start=result.window_start,
        oldest_checked=result.oldest_checked,
        pattern_timeouts=result.pattern_timeouts,
        examples=[row_to_log_entry(row) for row in result.examples],
    )
