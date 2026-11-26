"""
Download of the logs a search selects, as CSV or NDJSON (the Console's Download and
GET /api/v1/logs/export).

The logs are read newest first one page at a time (keyset paging, log_store.search) and written
as each page arrives, so memory stays at one page whatever the size of the download. The first
page is read before the response starts, with the count of matches capped at EXPORT_ROW_CAP + 1:
an error, a disconnect or the search time limit there still gives an HTTP status, and the
headers can say how many logs follow and whether the cap cut them. The search time limit counts
the time spent reading pages, not the time spent sending them, so a slow connection does not cut
a download short. When the limit runs out, or the database fails, part way through, the response
is cut off without its end, so the client sees a failed download rather than a short file that
looks whole.
"""

import asyncio
import csv
import datetime
import io
import json
import logging
import sqlite3
import threading
import time
from typing import AsyncIterator, Literal, Optional

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.redactor import redact
from app.core.utils import format_severity_label, get_server_tzinfo
from app.services import log_store
from app.services.log_query import (
    SEARCH_TIME_LIMIT_SECONDS,
    LogFilter,
    db_busy_exception,
    row_to_log_entry,
    run_search_query,
)

logger = logging.getLogger(__name__)

# Most logs one download holds; the newest are kept
EXPORT_ROW_CAP = 100_000
# Rows read from the database per step
EXPORT_PAGE_SIZE = 1000

ExportFormat = Literal["csv", "ndjson"]

# Fields of each log, in this order in both formats: the API's log fields, with the severity's
# name beside its number (as MCP gives it)
EXPORT_FIELDS = (
    "id",
    "timestamp",
    "received_at",
    "source_alias",
    "source_ip",
    "app_name",
    "severity",
    "severity_label",
    "facility",
    "source_kind",
    "reported_host",
    "host_self_reported",
    "message",
    "raw",
)

# A spreadsheet reads a cell that starts with one of these as a formula
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

_MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "ndjson": "application/x-ndjson"}

ROWS_HEADER = "X-LogShed-Export-Rows"
CAPPED_HEADER = "X-LogShed-Export-Capped"


def csv_cell(value: object) -> object:
    """
    A value as a CSV cell: true or false for a boolean (as in the NDJSON file, not Python's
    True), and text made safe for a spreadsheet: one that would start a formula gets a leading '.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def export_record(row, mask_secrets: bool) -> dict:
    """One log as exported: the API's fields, with secrets in its message and raw text masked when asked."""
    entry = row_to_log_entry(row).model_dump()
    entry["severity_label"] = format_severity_label(entry.get("severity"))
    record = {field: entry.get(field) for field in EXPORT_FIELDS}
    if mask_secrets:
        for field in ("message", "raw"):
            if record[field]:
                record[field] = redact(record[field])
    return record


def format_rows(rows: list, fmt: ExportFormat, mask_secrets: bool) -> str:
    """A page of rows in the download's format, each ending in a new line."""
    records = [export_record(row, mask_secrets) for row in rows]
    if fmt == "ndjson":
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    for r in records:
        writer.writerow([csv_cell(r[f]) if r[f] is not None else "" for f in EXPORT_FIELDS])
    return out.getvalue()


def csv_heading() -> str:
    """The CSV file's first line, after a byte order mark so spreadsheets read it as UTF-8."""
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow(EXPORT_FIELDS)
    return "﻿" + out.getvalue()


def export_filename(fmt: ExportFormat, now: Optional[datetime.datetime] = None) -> str:
    """logshed-logs-YYYYMMDD-HHMMSS.<format>, in the server's time zone."""
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(get_server_tzinfo())
    return f"logshed-logs-{moment:%Y%m%d-%H%M%S}.{fmt}"


async def export_logs(
    log_filter: LogFilter,
    fmt: ExportFormat,
    mask_secrets: bool,
    request: Optional[Request] = None,
) -> StreamingResponse:
    """
    The logs log_filter selects as a download, newest first, at most EXPORT_ROW_CAP of them.
    Raises HTTP 504 when the first page passes the search time limit, 499 when the client left
    first, and 503 when the database is busy.
    """
    from app.api.deps import run_db_query

    row_cap, page_size = EXPORT_ROW_CAP, EXPORT_PAGE_SIZE

    deadline = time.monotonic() + SEARCH_TIME_LIMIT_SECONDS
    stopped = threading.Event()

    # Each page is read and written out in the database thread: masking and formatting 1,000
    # logs takes tens of milliseconds, too long to hold the event loop
    def _read_page(
        conn: sqlite3.Connection, fetch: int, count_limit: int, before: Optional[tuple[str, int]]
    ) -> tuple[int, int, Optional[tuple[str, int]], str]:
        """(count, rows read, the last row's position, the rows as text)."""
        total, rows = log_store.search(conn, log_filter, fetch=fetch, count_limit=count_limit, before=before)
        last = (str(rows[-1]["timestamp"]), rows[-1]["id"]) if rows else None
        return total, len(rows), last, format_rows(rows, fmt, mask_secrets)

    first_fetch = min(page_size, row_cap)
    try:
        total, first_count, first_last, first_text = await run_search_query(
            lambda conn: _read_page(conn, first_fetch, row_cap + 1, None), request
        )
    except sqlite3.OperationalError as exc:
        raise db_busy_exception(exc, "downloading logs")
    capped = total > row_cap
    expected = min(total, row_cap)

    # What is left of the search time limit after the first page; only database time counts
    budget = {"left": deadline - time.monotonic()}

    def _read_next(fetch: int, before: Optional[tuple[str, int]]):
        page_deadline = time.monotonic() + budget["left"]

        def _should_interrupt() -> bool:
            return stopped.is_set() or time.monotonic() > page_deadline

        async def _run():
            started = time.monotonic()
            try:
                return await run_db_query(
                    lambda conn: _read_page(conn, fetch, 0, before), should_interrupt=_should_interrupt
                )
            finally:
                budget["left"] -= time.monotonic() - started

        return _run()

    async def _body() -> AsyncIterator[str]:
        sent, count, last, text, fetch = 0, first_count, first_last, first_text, first_fetch
        try:
            if fmt == "csv":
                yield csv_heading()
            while count:
                yield text
                sent += count
                if sent >= row_cap or count < fetch:
                    return
                fetch = min(page_size, row_cap - sent)
                try:
                    _, count, last, text = await _read_next(fetch, last)
                except sqlite3.OperationalError as exc:
                    if stopped.is_set():
                        return
                    if "interrupted" in str(exc).lower():
                        logger.warning(
                            f"Log download stopped after {SEARCH_TIME_LIMIT_SECONDS:.0f} s and {sent} logs"
                        )
                    else:
                        logger.warning(f"Log download failed after {sent} logs: {exc}")
                    raise
        except asyncio.CancelledError:
            # The client went away: the page being read stops at its next check
            stopped.set()
            raise
        finally:
            stopped.set()

    headers = {
        "Content-Disposition": f'attachment; filename="{export_filename(fmt)}"',
        ROWS_HEADER: str(expected),
        CAPPED_HEADER: "true" if capped else "false",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(_body(), media_type=_MEDIA_TYPES[fmt], headers=headers)


def check_export_ids(log_ids: Optional[list[int]], has_other_filters: bool) -> None:
    """Ticked rows are downloaded alone: HTTP 422 for ids sent together with search filters."""
    if log_ids and has_other_filters:
        raise HTTPException(status_code=422, detail="Send either log_ids or search filters, not both.")
