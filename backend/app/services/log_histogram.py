"""
Log counts per time bucket for the Console's volume histogram.

The range is the Console's own (its From and To, or the last hour when it has no From), cut into
about 60 buckets on the server's clock. Ranges over 12 hours use whole hours or more, so a sealed
day's counts come from the catalogue instead of its file (log_store.histogram).
"""

import datetime
import math
import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.services import log_store
from app.services.log_query import LogFilter

# The window shown when the Console has no start time (All Time)
DEFAULT_WINDOW = datetime.timedelta(hours=1)
# About this many bars at most; the bucket is the smallest step that keeps within it
MAX_BUCKETS = 60
# Bucket sizes in seconds. Every size under a day divides 30 minutes or a day, so buckets line up
# with the server clock's hours and midnights in every zone (offsets are whole quarter hours,
# and clock changes move by 30 or 60 minutes).
BUCKET_SECONDS = (
    1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
    3600, 7200, 10800, 21600, 43200, 86400, 2 * 86400, 7 * 86400,
    14 * 86400, 30 * 86400, 91 * 86400, 365 * 86400,
)
HOUR = 3600
DAY = 86400
# Ranges longer than this are cut to the stored logs' times (clamp_to_stored)
CLAMP_SPAN = 366 * 86400
# Above this span, buckets are whole hours or more, which the catalogue can count
CATALOGUE_SPAN = 12 * HOUR

SEVERITY_GROUPS = ("error", "warning", "info", "debug")


def severity_group(severity: int) -> str:
    """The histogram's group for a syslog severity: emergency to error, warning, notice and info, debug."""
    if severity <= 3:
        return "error"
    if severity == 4:
        return "warning"
    if severity <= 6:
        return "info"
    return "debug"


@dataclass(frozen=True)
class Bucket:
    # UTC epoch seconds; the first and last bucket are cut to the range
    start: int
    end: int
    counts: dict[str, int]


@dataclass(frozen=True)
class Histogram:
    range_from: int
    range_to: int
    bucket_seconds: int
    buckets: list[Bucket]


def choose_bucket(span_s: float) -> int:
    """The smallest bucket size that gives at most MAX_BUCKETS buckets (whole hours above 12 hours)."""
    for size in BUCKET_SECONDS:
        if size < HOUR and span_s > CATALOGUE_SPAN:
            continue
        if span_s / size <= MAX_BUCKETS:
            return size
    return BUCKET_SECONDS[-1]


def bucket_edges(range_from: int, range_to: int, size: int, tz: datetime.tzinfo) -> list[int]:
    """
    Bucket edges (UTC epoch seconds) from the edge at or before range_from to the first edge after
    range_to. Buckets under a day are all the same length, placed so they start at even times of
    the server clock (the hour, or a multiple of the size since midnight) at range_from; across a
    clock change, buckets of 2 hours or more then start an hour off. Day buckets start at the
    server's midnight and last a calendar day (23 or 25 hours on a clock change).
    """
    start = datetime.datetime.fromtimestamp(range_from, tz)
    if size < DAY:
        offset = int(start.utcoffset().total_seconds()) if start.utcoffset() else 0
        # An edge e satisfies (e + offset) % size == 0: a multiple of the size on the zone's clock
        first = range_from - (range_from + offset) % size
        edges = list(range(first, range_to + 1, size))
        if edges[-1] <= range_to:
            edges.append(edges[-1] + size)
        return edges

    days = size // DAY
    wall = start.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    edges = []
    while True:
        edge = _wall_to_epoch(wall, tz)
        edges.append(edge)
        if edge > range_to:
            return edges
        wall += datetime.timedelta(days=days)


def _wall_to_epoch(wall: datetime.datetime, tz: datetime.tzinfo) -> int:
    """The moment the zone's clock shows wall (its first time, if shown twice; just after a skipped time)."""
    return int(wall.replace(tzinfo=tz, fold=0).timestamp())


def histogram_range(log_filter: LogFilter, now: datetime.datetime) -> tuple[int, int]:
    """
    The time range (UTC epoch seconds) the histogram shows for a filter: its From and To, with
    now for a missing To, and the hour before the end for a missing From.
    """
    # Rounded up: a To inside a second (a zoomed bar ends a millisecond before the next) keeps
    # that second's bucket whole
    end = math.ceil(_epoch_exact(log_filter.to_ts)) if log_filter.to_ts else int(now.timestamp())
    start = _epoch(log_filter.from_ts) if log_filter.from_ts else end - int(DEFAULT_WINDOW.total_seconds())
    return start, max(end, start + 1)


def clamp_to_stored(conn: sqlite3.Connection, range_from: int, range_to: int, now_s: int) -> tuple[int, int]:
    """
    A range over a year cut to the stored logs' own times (the oldest log's, and the later of now
    and the newest log's), so a year typed by mistake into From or To (the Console applies each
    edit) does not ask for thousands of buckets; nothing outside that span is stored. Shorter
    ranges are kept as they are; a long range with no stored log, or still over a year, keeps
    its last year.
    """
    if range_to - range_from <= CLAMP_SPAN:
        return range_from, range_to
    oldest_us, newest_us = log_store.stored_time_span(conn)
    if oldest_us is not None and newest_us is not None:
        oldest, latest = oldest_us // 1_000_000, max(now_s, -(-newest_us // 1_000_000))
        start, end = max(range_from, oldest), min(range_to, latest)
        if end - start > 0 and end - start <= CLAMP_SPAN:
            return start, end
    # No stored log in the range (or more than a year of them): its last year
    return max(range_from, range_to - CLAMP_SPAN), range_to


def _epoch_exact(stored_iso: str) -> float:
    return datetime.datetime.fromisoformat(stored_iso).timestamp()


def _epoch(stored_iso: str) -> int:
    return int(datetime.datetime.fromisoformat(stored_iso).timestamp())


def build_histogram(
    conn: sqlite3.Connection,
    log_filter: LogFilter,
    tz: datetime.tzinfo,
    now: Optional[datetime.datetime] = None,
) -> Histogram:
    """Counts by severity group in each bucket of the filter's range (see the module docstring)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    range_from, range_to = clamp_to_stored(conn, *histogram_range(log_filter, now), int(now.timestamp()))
    size = choose_bucket(range_to - range_from)
    edges = bucket_edges(range_from, range_to, size, tz)
    # The range's own bounds (the hour before now for All Time) apply as well as the buckets'
    bounded = LogFilter(
        query=log_filter.query,
        sources=log_filter.sources,
        apps=log_filter.apps,
        severity_max=log_filter.severity_max,
        from_ts=log_filter.from_ts or datetime.datetime.fromtimestamp(range_from, datetime.timezone.utc).isoformat(),
        to_ts=log_filter.to_ts,
    )
    by_severity = log_store.histogram(conn, bounded, edges)
    buckets = []
    for i, counts in enumerate(by_severity):
        grouped = dict.fromkeys(SEVERITY_GROUPS, 0)
        for severity, cnt in counts.items():
            grouped[severity_group(severity)] += cnt
        buckets.append(
            Bucket(start=max(edges[i], range_from), end=min(edges[i + 1], range_to), counts=grouped)
        )
    # Edges come from range_from's bucket to the one holding range_to. When range_to is an edge,
    # the last bucket has no width: the logs stamped exactly at range_to (the filter's end is
    # inclusive) go into the bucket before it
    while len(buckets) > 1 and buckets[-1].start >= buckets[-1].end:
        empty = buckets.pop()
        for group, cnt in empty.counts.items():
            buckets[-1].counts[group] += cnt
    return Histogram(range_from=range_from, range_to=range_to, bucket_seconds=size, buckets=buckets)
