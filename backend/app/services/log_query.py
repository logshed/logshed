"""
Shared helpers for the three log WHERE-clause builders:

- the web UI list in ``app/api/logs.py``;
- REST v1 (also used by MCP) in ``app/api/external_v1.py``;
- scoped deletion in ``app/services/log_deletion.py``.

They keep FTS5 error handling, the FTS indexer wake-up and time-bound conversion
identical across the three paths without merging the builders themselves.
"""

import logging
import sqlite3
from typing import Any, Callable, Optional, TypeVar

from fastapi import HTTPException, status

from app.core.utils import escape_fts_tokens, format_fts_query, parse_relative_or_iso_time

logger = logging.getLogger(__name__)

T = TypeVar("T")

INVALID_DATETIME_DETAIL = (
    "Invalid datetime format. Expected ISO-8601 string or relative duration (e.g. '1h', '24h')."
)

DB_BUSY_DETAIL = "The database is busy. Try again in a moment."

# Uncorrelated subquery: FTS5 evaluates the MATCH exactly once. A JOIN lets the planner
# drive from a relational index (e.g. app_name) and re-run the MATCH per candidate row,
# rebuilding the full doclist of auto-prefixed terms each time (quadratic cost).
FTS_MATCH_CLAUSE = "logs.id IN (SELECT rowid FROM logs_fts WHERE logs_fts MATCH :fts_term)"

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


def db_busy_exception(exc: BaseException, action: str) -> HTTPException:
    """Log a database OperationalError and return a generic 503 for the client."""
    logger.warning(f"Database error while {action}: {exc}")
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=DB_BUSY_DETAIL)
