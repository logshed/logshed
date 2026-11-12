"""
The one way to select logs by filter, used by the web UI list (``app/api/logs.py``), REST v1
and MCP (``app/api/external_v1.py``) and scoped deletion (``app/services/log_deletion.py``):
``LogFilter`` holds the filter, ``build_where`` turns it into SQL, ``row_to_log_entry`` maps the
rows and ``fetch_log_context`` reads the lines around one log. The helpers below them keep FTS5
error handling, the FTS indexer wake-up, newest-first windows and the search time limit the
same on every path.
"""

import asyncio
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

from fastapi import HTTPException, Request, status

from app.core.redactor import redact
from app.core.utils import (
    AliasIndex,
    escape_fts_tokens,
    format_fts_query,
    parse_multi_values,
    parse_relative_or_iso_time,
    source_filter_sql,
)
from app.models import LogEntry

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


class InvalidTimeBound(ValueError):
    """A from or to bound that is neither ISO-8601 nor a relative duration; its text is INVALID_DATETIME_DETAIL."""

    def __init__(self) -> None:
        super().__init__(INVALID_DATETIME_DETAIL)


@dataclass(frozen=True)
class LogFilter:
    """
    Which logs a search, count or delete selects. Build it with LogFilter.parse, which reads
    the values as the routes receive them. Every criterion narrows the selection; an empty
    filter selects every log.
    """

    query: Optional[str] = None
    sources: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    severity_max: Optional[int] = None
    # Stored UTC layout (to_utc_bound), compared as strings
    from_ts: Optional[str] = None
    to_ts: Optional[str] = None
    log_ids: tuple[int, ...] = ()

    @classmethod
    def parse(
        cls,
        *,
        query: Optional[str] = None,
        sources: Optional[list[str]] = None,
        apps: Optional[list[str]] = None,
        severity_max: Optional[int] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        log_ids: Optional[list[int]] = None,
    ) -> "LogFilter":
        """
        Read a filter from route values: repeated or comma-separated sources and apps, ISO or
        relative time bounds. A blank query or bound means no criterion. Raises InvalidTimeBound.
        """
        try:
            from_ts = to_utc_bound(from_)
            to_ts = to_utc_bound(to)
        except ValueError:
            raise InvalidTimeBound() from None
        return cls(
            query=query if query and query.strip() else None,
            sources=tuple(parse_multi_values(sources)),
            apps=tuple(parse_multi_values(apps)),
            severity_max=severity_max,
            from_ts=from_ts,
            to_ts=to_ts,
            log_ids=tuple(log_ids or ()),
        )

    @property
    def is_empty(self) -> bool:
        """True when the filter has no criterion, so it selects every log."""
        return not (
            self.query or self.sources or self.apps or self.log_ids
            or self.severity_max is not None or self.from_ts or self.to_ts
        )


def build_where(conn: Optional[sqlite3.Connection], f: LogFilter) -> tuple[str, dict[str, Any]]:
    """
    The WHERE clause (empty for an empty filter) and named parameters selecting f's logs. A host
    name or address matches through the configured host aliases (AliasIndex.expand), read from
    conn; without a connection, sources match as given.
    """
    where_clauses: list[str] = []
    params: dict[str, Any] = {}

    if f.log_ids:
        id_placeholders = ", ".join(f":id_{i}" for i in range(len(f.log_ids)))
        where_clauses.append(f"logs.id IN ({id_placeholders})")
        params.update({f"id_{i}": log_id for i, log_id in enumerate(f.log_ids)})

    add_fts_filter(where_clauses, params, f.query)

    if f.sources:
        sources = AliasIndex.load(conn).expand(list(f.sources)) if conn is not None else list(f.sources)
        where_clauses.append(source_filter_sql(", ".join(f":src_{i}" for i in range(len(sources)))))
        params.update({f"src_{i}": s for i, s in enumerate(sources)})

    if f.apps:
        app_placeholders = ", ".join(f":app_{i}" for i in range(len(f.apps)))
        where_clauses.append(f"logs.app_name IN ({app_placeholders})")
        params.update({f"app_{i}": a for i, a in enumerate(f.apps)})

    if f.severity_max is not None:
        where_clauses.append("logs.severity <= :severity_max")
        params["severity_max"] = f.severity_max

    if f.from_ts:
        where_clauses.append("logs.timestamp >= :from_ts")
        params["from_ts"] = f.from_ts

    if f.to_ts:
        where_clauses.append("logs.timestamp <= :to_ts")
        params["to_ts"] = f.to_ts

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, params


LOG_COLUMNS = (
    "logs.id, logs.timestamp, logs.received_at, logs.source_ip, "
    "logs.source_alias, logs.app_name, logs.facility, logs.severity, logs.message"
)


def log_columns(include_raw: bool = True) -> str:
    """The SELECT column list for log rows, with the raw packet only when it is needed."""
    return f"{LOG_COLUMNS}, logs.raw" if include_raw else LOG_COLUMNS


def row_to_log_entry(row: Any, *, external: bool = False, include_raw: bool = True) -> LogEntry:
    """
    Map a logs row to a LogEntry. The web UI gets the row as stored. external is the form REST
    and MCP return to outside clients and AI assistants: message and raw pass through the
    redactor, an empty host alias reads as the address and a missing app name as "unknown".
    raw is None (and left out of REST responses) unless include_raw.
    """
    raw = row["raw"] if include_raw else None
    if not external:
        return LogEntry(
            id=row["id"],
            timestamp=str(row["timestamp"]),
            received_at=str(row["received_at"]),
            source_ip=row["source_ip"],
            source_alias=row["source_alias"],
            app_name=row["app_name"],
            facility=row["facility"],
            severity=row["severity"],
            message=row["message"],
            raw=raw,
        )
    return LogEntry(
        id=row["id"],
        timestamp=str(row["timestamp"]),
        received_at=str(row["received_at"]),
        source_ip=row["source_ip"],
        source_alias=row["source_alias"] or row["source_ip"],
        app_name=row["app_name"] if row["app_name"] is not None else "unknown",
        facility=row["facility"],
        severity=row["severity"],
        message=redact(row["message"]),
        raw=redact(raw) if raw is not None else None,
    )


def fetch_log_context(
    conn: sqlite3.Connection,
    log_id: int,
    before: int,
    after: int,
    same_app: bool = False,
    include_raw: bool = True,
) -> Optional[tuple[Any, list[Any], list[Any]]]:
    """
    The rows around one log, from the same host (context_source_clause) and, with same_app, the
    same app: (target, up to before earlier rows, up to after later rows), each list in time
    order. None when the log does not exist.
    """
    columns = log_columns(include_raw)
    target = conn.execute(f"SELECT {columns} FROM logs WHERE id = ?", (log_id,)).fetchone()
    if target is None:
        return None

    source_clause, source_params = context_source_clause(target["source_alias"], target["source_ip"])
    if not same_app:
        app_clause, app_params = "", ()
    elif target["app_name"] is None:
        app_clause, app_params = "AND app_name IS NULL ", ()
    else:
        app_clause, app_params = "AND app_name = ? ", (target["app_name"],)
    t_time, t_id = target["timestamp"], target["id"]

    before_rows = conn.execute(
        f"""
        SELECT {columns} FROM logs
        WHERE {source_clause}{app_clause}AND (timestamp < ? OR (timestamp = ? AND id < ?))
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
        """,
        (*source_params, *app_params, t_time, t_time, t_id, before),
    ).fetchall()
    before_rows.reverse()
    after_rows = conn.execute(
        f"""
        SELECT {columns} FROM logs
        WHERE {source_clause}{app_clause}AND (timestamp > ? OR (timestamp = ? AND id > ?))
        ORDER BY timestamp ASC, id ASC
        LIMIT ?
        """,
        (*source_params, *app_params, t_time, t_time, t_id, after),
    ).fetchall()
    return target, before_rows, after_rows


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
                # Rows are mostly in id order, but a late or back-dated row outside the window
                # could still belong on the page. +id keeps SQLite on the timestamp index, read
                # from the cutoff up: the first row older than the window ends the scan, so a
                # window that does not cover the time range fails at once. With no row at or
                # after the cutoff, the window is accepted, which is still exact: no row outside
                # it can belong on the page.
                outside = conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM logs WHERE timestamp >= ? AND +id < ?)", (cutoff, min_rowid)
                ).fetchone()[0]
                if not outside:
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
# more than 32,766 of them. REST v1 and MCP use the stricter MAX_API_FILTER_VALUES (each source
# becomes parameters twice, plus its alias expansion).
MAX_UI_FILTER_VALUES = 500
MAX_API_FILTER_VALUES = 50


def check_filter_value_counts(limit: int, **filters: Optional[list[str]]) -> None:
    """Raise HTTP 422 when a multi-value filter lists more than limit values."""
    for name, values in filters.items():
        if len(parse_multi_values(values)) > limit:
            raise HTTPException(
                status_code=422,
                detail=f"Too many '{name}' values: at most {limit} are allowed.",
            )


def check_ui_filter_value_counts(**filters: Optional[list[str]]) -> None:
    """Raise HTTP 422 when a web UI multi-value filter lists more than MAX_UI_FILTER_VALUES values."""
    check_filter_value_counts(MAX_UI_FILTER_VALUES, **filters)


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
