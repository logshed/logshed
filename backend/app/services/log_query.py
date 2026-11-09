"""
Shared helpers for the three log WHERE-clause builders:

- the web UI list in ``app/api/logs.py``;
- REST v1 (also used by MCP) in ``app/api/external_v1.py``;
- scoped deletion in ``app/services/log_deletion.py``.

They keep FTS5 error handling, the FTS indexer wake-up and time-bound conversion
identical across the three paths without merging the builders themselves.
"""

import asyncio
import logging
import sqlite3
import threading
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

from fastapi import HTTPException, Request, status

from app.core.utils import (
    escape_fts_tokens,
    format_fts_query,
    parse_multi_values,
    parse_relative_or_iso_time,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

INVALID_DATETIME_DETAIL = (
    "Invalid datetime format. Expected ISO-8601 string or relative duration (e.g. '1h', '24h')."
)

DB_BUSY_DETAIL = "The database is busy. Try again in a moment."

# Uncorrelated subquery: FTS5 evaluates the MATCH exactly once. A JOIN lets the planner
# drive from a relational index (e.g. app_name) and re-run the MATCH per candidate row,
# rebuilding the full doclist of auto-prefixed terms each time (quadratic cost).
# :fts_min_rowid is 0 (every row) unless run_newest_first narrows the match to the newest rows.
FTS_MATCH_CLAUSE = (
    "logs.id IN (SELECT rowid FROM logs_fts WHERE logs_fts MATCH :fts_term AND rowid >= :fts_min_rowid)"
)

# Newest-first searches first match only the newest this many rows, widening in turn and then
# to every row. FTS5 seeks a rowid range in its doclists, so a common term or phrase no longer
# looks up and sorts every match in the database to show the newest page.
NEWEST_FIRST_WINDOWS = (100_000, 1_000_000, 10_000_000)

# A search still running after this long is stopped (the client gets HTTP 504), so a client
# with no timeout of its own cannot hold a database thread indefinitely.
SEARCH_TIME_LIMIT_SECONDS = 120.0
SEARCH_TIME_LIMIT_DETAIL = (
    "The search took longer than 2 minutes and was stopped. Narrow the time range or the search terms."
)
# How often a running search checks whether its client has gone away.
DISCONNECT_POLL_SECONDS = 0.25

# Error text SQLite raises when an FTS5 MATCH expression cannot be parsed. "no such column"
# comes from FTS5 column filters: "foo-bar" parses as "foo" plus an excluded column "bar".
_FTS_SYNTAX_MARKERS = (
    "fts5: syntax error",
    "fts5: parser stack overflow",
    "unterminated string",
    "no such column",
    "unknown special query",
    "malformed match expression",
)


def to_utc_bound(value: Optional[str]) -> Optional[str]:
    """
    Convert a client time bound to the stored ``logs.timestamp`` format.

    Accepts ISO-8601 strings with any offset (or none, read as UTC), ``Z`` suffixes and
    relative values such as ``1h`` or ``since_midnight``. Returns ``datetime.isoformat()``
    in UTC (for example ``2026-10-15T12:00:00+00:00``), so plain string comparison against
    stored rows is chronological. Returns None for empty input; raises ValueError when the
    value cannot be read.
    """
    if value is None:
        return None
    try:
        return parse_relative_or_iso_time(value)
    except (TypeError, OverflowError) as exc:
        raise ValueError(str(exc)) from exc


def add_fts_filter(where_clauses: list[str], params: dict[str, Any], query: Optional[str]) -> None:
    """Append the FTS5 MATCH clause (and its ``fts_term`` parameter) for a non-empty query."""
    if not (query and query.strip()):
        return
    fts_term = format_fts_query(query)
    if not fts_term.strip():
        where_clauses.append("0")
        return
    where_clauses.append(FTS_MATCH_CLAUSE)
    params["fts_term"] = fts_term
    params["fts_min_rowid"] = 0


def run_newest_first(
    conn: sqlite3.Connection,
    params: dict[str, Any],
    run: Callable[[dict[str, Any]], T],
    oldest_needed: Callable[[T], Optional[str]],
    matches: Callable[[T], int],
    needed: int,
) -> T:
    """
    Run a newest-first search ``run(params)``, matching full-text terms in the newest rows first.

    ``run`` executes the count and page statements of one attempt. ``oldest_needed`` returns
    the timestamp of the oldest row the caller needs from that attempt (the last row of a full
    page, with the capped count reached; with too few matches, the search's lower time bound),
    or None when only a search over every row can tell. A window's result is used only when
    every row at least that new lies inside the window, so results are exactly those of a
    search over every row; otherwise a wider window is tried, and the last attempt matches
    every row. ``matches`` is an attempt's capped match count and ``needed`` the count that
    fills a page and the total's cap: windows too small to hold that many at the density just
    seen are skipped, so a rare term goes straight to every row. Queries without a full-text
    term run once, unchanged.
    """
    if "fts_term" not in params:
        return run(params)
    max_id = conn.execute("SELECT MAX(id) FROM logs").fetchone()[0]
    if max_id is not None:
        smallest_useful_span = 0.0
        for span in NEWEST_FIRST_WINDOWS:
            if span < smallest_useful_span:
                continue
            min_rowid = max_id - span + 1
            if min_rowid <= 1:
                break
            result = run({**params, "fts_min_rowid": min_rowid})
            cutoff = oldest_needed(result)
            if cutoff is not None:
                # MIN(+id) keeps SQLite on the timestamp index: rows are mostly in id order, but
                # a late or back-dated row outside the window could still belong on the page.
                oldest_id = conn.execute(
                    "SELECT MIN(+id) FROM logs WHERE timestamp >= ?", (cutoff,)
                ).fetchone()[0]
                if oldest_id is not None and oldest_id >= min_rowid:
                    return result
            found = matches(result)
            smallest_useful_span = span * needed / found if found else float("inf")
    return run({**params, "fts_min_rowid": 0})


async def run_search_query(fn: Callable[[sqlite3.Connection], T], request: Optional[Request] = None) -> T:
    """
    Run a log search with run_db_query, stopping its SQLite statements when the client
    disconnects (search as you type abandons each earlier request) or after
    SEARCH_TIME_LIMIT_SECONDS. Raises HTTP 504 at the time limit and HTTP 499 on disconnect;
    other database errors propagate unchanged.
    """
    from app.api.deps import run_db_query

    disconnected = threading.Event()
    deadline = time.monotonic() + SEARCH_TIME_LIMIT_SECONDS

    def _should_interrupt() -> bool:
        return disconnected.is_set() or time.monotonic() > deadline

    task = asyncio.ensure_future(run_db_query(fn, should_interrupt=_should_interrupt))
    try:
        if request is not None:
            await _interrupt_on_disconnect(task, request, disconnected)
        return await task
    except asyncio.CancelledError:
        disconnected.set()
        raise
    except sqlite3.OperationalError as exc:
        if "interrupted" not in str(exc).lower():
            raise
        if disconnected.is_set():
            raise HTTPException(status_code=499, detail="The client closed the request.")
        logger.warning(f"Search stopped after {SEARCH_TIME_LIMIT_SECONDS:.0f} s")
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=SEARCH_TIME_LIMIT_DETAIL)


async def _interrupt_on_disconnect(task: Awaitable[Any], request: Request, disconnected: threading.Event) -> None:
    """Wait for task, setting disconnected as soon as the request's client goes away."""
    while True:
        done, _ = await asyncio.wait({task}, timeout=DISCONNECT_POLL_SECONDS)
        if done:
            return
        if await request.is_disconnected():
            disconnected.set()
            return


def is_fts_syntax_error(exc: BaseException) -> bool:
    """True when an OperationalError is an FTS5 query parse error (not a lock or I/O error)."""
    message = str(exc).lower()
    return any(marker in message for marker in _FTS_SYNTAX_MARKERS)


def notify_fts_worker() -> None:
    """Wake the FTS indexer so rows committed in the last poll interval are indexed promptly."""
    from app.services.fts_indexer import get_fts_worker

    worker = get_fts_worker()
    if worker is None:
        return
    try:
        worker.notify_new_logs()
    except Exception as exc:
        logger.debug(f"FTS indexer wake-up failed: {exc}")


def execute_with_fts_fallback(
    run: Callable[[dict[str, Any]], T],
    params: dict[str, Any],
    raw_query: Optional[str],
    empty: T,
) -> T:
    """
    Run ``run(params)``, retrying once with escaped tokens when FTS5 rejects the query syntax.

    ``run`` executes every statement of one attempt (for example a count and a page query,
    or a batched delete loop) with the given parameters. When ``params`` carries an
    ``fts_term``, the FTS indexer is woken first. Only FTS5 syntax errors trigger the retry;
    other OperationalErrors such as "database is locked" propagate to the caller. ``empty``
    is returned when the escaped query has no searchable tokens or is also rejected.
    """
    if "fts_term" not in params:
        return run(params)

    notify_fts_worker()
    try:
        return run(params)
    except sqlite3.OperationalError as exc:
        if not raw_query or not is_fts_syntax_error(exc):
            raise
        fallback_term = escape_fts_tokens(raw_query)
        logger.info(
            f"FTS5 query {params['fts_term']!r} failed ({exc}), falling back to escaped token search."
        )
        if not fallback_term.strip() or fallback_term == params["fts_term"]:
            return empty
        try:
            return run({**params, "fts_term": fallback_term})
        except sqlite3.OperationalError as retry_exc:
            if not is_fts_syntax_error(retry_exc):
                raise
            logger.info(f"Escaped FTS5 query {fallback_term!r} also failed ({retry_exc}).")
            return empty


# The web UI routes accept as many source or app_name values as the facets lists can hold
# (the facets cap is 500), so the UI can always send every value it can show. Longer lists
# are refused early: each value becomes SQL parameters and SQLite rejects statements with
# more than 32,766 of them. REST v1 and MCP use a stricter cap in ``api/external_v1.py``.
MAX_UI_FILTER_VALUES = 500


def check_ui_filter_value_counts(**filters: Optional[list[str]]) -> None:
    """Raise HTTP 422 when a web UI multi-value filter lists more than MAX_UI_FILTER_VALUES values."""
    for name, values in filters.items():
        if len(parse_multi_values(values)) > MAX_UI_FILTER_VALUES:
            raise HTTPException(
                status_code=422,
                detail=f"Too many '{name}' values: at most {MAX_UI_FILTER_VALUES} are allowed.",
            )


def db_busy_exception(exc: BaseException, action: str) -> HTTPException:
    """Log a database OperationalError and return a generic 503 for the client."""
    logger.warning(f"Database error while {action}: {exc}")
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=DB_BUSY_DETAIL)


def context_source_clause(source_alias: Optional[str], source_ip: Optional[str]) -> tuple[str, tuple]:
    """
    WHERE fragment (and parameters) selecting the same host as a context target row.

    ``source_alias`` is NOT NULL and an empty string means "no alias", so alias-less rows
    are matched by ``source_ip`` as well; otherwise every alias-less host would mix into
    the context. The IS NULL branch covers older databases with a nullable column.
    """
    if source_alias is None:
        return "source_alias IS NULL AND source_ip = ? ", (source_ip,)
    if source_alias == "":
        return "source_alias = '' AND source_ip = ? ", (source_ip,)
    return "source_alias = ? ", (source_alias,)
