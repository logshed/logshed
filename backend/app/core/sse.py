"""
Server-Sent Events (SSE) broadcaster for real-time log streaming.
"""

import asyncio
import json
import logging
from typing import Callable, Optional

from app.core.day_files import KIND_NAMES, REPORTED_HIDDEN_KEY

logger = logging.getLogger(__name__)

# Each stream holds a queue of up to 500 entries and its own HTTP connection.
MAX_SSE_SUBSCRIBERS = 20

# Shared by every event: json.dumps(default=str) would build a new encoder for each one
_EVENT_ENCODER = json.JSONEncoder(default=str)


class SSESubscriberLimitError(Exception):
    """Raised when a new stream would exceed MAX_SSE_SUBSCRIBERS."""


class SSELogEvent(dict):
    """
    Public view of one log entry, shared by every subscriber queue.

    It is a dict of the entry's public keys, so subscriber filters read fields as before,
    and ``data`` holds the JSON encoding of exactly those keys, computed once per entry.
    """

    __slots__ = ("data",)

    @classmethod
    def from_entry(cls, entry: dict) -> "SSELogEvent":
        event = cls(entry)
        reported = entry.get("reported_host")
        if reported:
            event["host_self_reported"] = reported == entry.get("source_alias") and not entry.get(REPORTED_HIDDEN_KEY)
        for key in [k for k in event if isinstance(k, str) and k.startswith("_")]:
            del event[key]
        # Stored as a number; the browser gets its name, as the log API gives it
        if "source_kind" in event:
            event["source_kind"] = KIND_NAMES.get(event["source_kind"])
        event.data = _EVENT_ENCODER.encode(event)
        return event


class SubscriberQueue(asyncio.Queue):
    """
    One live stream's queue. ``accept`` is the stream's filter (None takes every entry), applied
    by the broadcaster, so entries the stream would not show never take its room. ``skipped``
    counts the accepted entries dropped because the stream fell behind, until the stream reads
    and clears it to tell the browser.
    """

    def __init__(self, maxsize: int = 0, accept: Optional[Callable[[dict], bool]] = None):
        super().__init__(maxsize=maxsize)
        self.accept = accept
        self.skipped = 0


def _put_newest(q: asyncio.Queue, events: list[SSELogEvent]) -> None:
    """
    Queue the events, dropping the oldest queued ones when the queue is full: a slow subscriber
    keeps the newest entries, the same as putting each event and dropping the oldest when full,
    without moving the entries that would be dropped again within the same batch. The number
    dropped is added to the queue's ``skipped`` count, when it keeps one.
    """
    dropped = 0
    if q.maxsize > 0:
        dropped = max(0, len(events) - q.maxsize)
        events = events[-q.maxsize:]
        for _ in range(q.qsize() + len(events) - q.maxsize):
            q.get_nowait()
            dropped += 1
    for event in events:
        q.put_nowait(event)
    if dropped and isinstance(q, SubscriberQueue):
        q.skipped += dropped


def _events_per_subscriber(
    batch: list[dict], subscribers: list[asyncio.Queue]
) -> list[tuple[asyncio.Queue, list[SSELogEvent]]]:
    """
    Blocking part of broadcast_batch: encode each entry once and pick each subscriber's entries
    with its filter. Runs in a worker thread, because for a full batch of thousands of entries
    it takes tens of milliseconds that every web request would otherwise wait.
    """
    events = [SSELogEvent.from_entry(log_entry) for log_entry in batch]
    picked = []
    for q in subscribers:
        accept = getattr(q, "accept", None)
        picked.append((q, events if accept is None else [e for e in events if accept(e)]))
    return picked


class SSEBroadcaster:
    """
    Manages active SSE client subscriber queues and broadcasts incoming log entries.
    """

    def __init__(self, max_subscribers: int = MAX_SSE_SUBSCRIBERS):
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._max_subscribers = max_subscribers

    async def subscribe(
        self, maxsize: int = 500, accept: Optional[Callable[[dict], bool]] = None
    ) -> SubscriberQueue:
        """
        Register a new client subscriber queue, refusing it when the stream limit is reached.
        ``accept`` filters the entries the queue receives; it runs in a worker thread, so it
        must only read the entry and data that does not change while the stream is open.
        """
        q = SubscriberQueue(maxsize=maxsize, accept=accept)
        async with self._lock:
            if len(self._subscribers) >= self._max_subscribers:
                raise SSESubscriberLimitError(
                    f"At most {self._max_subscribers} live log streams can be open at once."
                )
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(q)

    async def broadcast_batch(self, batch: list[dict]) -> None:
        """
        Push a batch of log entries to every subscriber queue that accepts them.
        Returns at once when there are no subscribers.

        Each entry is copied without its private keys (names starting with '_', such as the
        alert evaluator's _epoch_ts) and encoded to JSON once, so later changes to the shared
        pipeline dict never reach the browser and subscribers do not re-encode it. Encoding and
        filtering run in a worker thread, and only the queue puts on the event loop; entries must
        not change until this returns (the queue consumer runs the alert checks after it).
        """
        if not self._subscribers or not batch:
            return

        async with self._lock:
            subscribers = list(self._subscribers)
        picked = await asyncio.to_thread(_events_per_subscriber, batch, subscribers)

        async with self._lock:
            for q, events in picked:
                # A stream that closed while the events were built gets nothing
                if q not in self._subscribers or not events:
                    continue
                try:
                    _put_newest(q, events)
                except Exception as e:
                    logger.debug(f"Error broadcasting batch to subscriber: {e}")

    async def broadcast(self, log_entry: dict) -> None:
        """
        Async broadcast call safely pushing a copy of log_entry to all active queues.
        """
        await self.broadcast_batch([log_entry])

    def subscriber_count(self) -> int:
        """Return number of active SSE subscribers."""
        return len(self._subscribers)

    def reset(self) -> None:
        """Reset all subscribers (for testing)."""
        self._subscribers.clear()


# Global SSE manager singleton
sse_manager = SSEBroadcaster()
