"""
Core log ingestion pipeline components for LogShed.
Provides the shared queue, multiline assembly, and SQLite batch consumer.
"""

import asyncio
import datetime
import logging
import re
import sqlite3
import threading
import time
import traceback
from typing import Any, Optional, Union
from pathlib import Path
from collections import defaultdict, deque

from app.core.migrations import get_connection

logger = logging.getLogger(__name__)


class InternalLogHandler(logging.Handler):
    """
    Python logging handler that captures internal application warnings and errors
    and feeds them directly into the LogShed ingestion pipeline.
    Ignores noisy HTTP access logs, SSE broadcast tasks, and ingestion pipeline internals
    to prevent self-referential loops.
    """
    IGNORED_LOGGERS = {
        "uvicorn.access",
        "httpcore",
        "httpx",
        "asyncio",
        "app.core.pipeline",
        "app.core.sse",
        "app.core.migrations",
        "app.services.retention",
        "app.services.storage_metrics",
    }

    def __init__(self, level: Optional[Union[int, str]] = None):
        super().__init__()
        self._thread_local = threading.local()
        self._is_disabled = False
        if level is not None:
            self.set_internal_level(level)
        else:
            from app.core.config import get_internal_log_level
            configured_level = get_internal_log_level()
            if configured_level is None:
                self._is_disabled = True
            else:
                self.setLevel(configured_level)

    def set_internal_level(self, level: Optional[Union[int, str]]) -> None:
        """
        Dynamically update the internal log handler filter level.
        Accepts integer logging levels, string level names ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        or 'DISABLED' / 'OFF' / 'NONE' / None to disable internal log emission.
        """
        if level is None:
            self._is_disabled = True
            return

        from app.core.config import parse_internal_log_level
        parsed = parse_internal_log_level(level)
        if parsed is None:
            self._is_disabled = True
        else:
            self._is_disabled = False
            self.setLevel(parsed)

    @property
    def is_disabled(self) -> bool:
        """True if internal log emission is disabled."""
        return self._is_disabled

    def emit(self, record: logging.LogRecord) -> None:
        if self._is_disabled:
            return

        if (
            record.name in self.IGNORED_LOGGERS
            or record.name.startswith("uvicorn.access")
            or any(record.name.startswith(f"{ignored}.") for ignored in self.IGNORED_LOGGERS)
        ):
            return

        # Check handler level filter (in case emit() is called directly or via custom handler dispatch)
        if self.level and record.levelno < self.level:
            return

        # Re-entrancy guard to prevent recursive logging loops on the same thread
        if getattr(self._thread_local, "in_emit", False):
            return
        self._thread_local.in_emit = True
        try:
            # Map Python log levels to RFC 5424 severity (0-7)
            if record.levelno >= logging.CRITICAL:
                severity = 2
            elif record.levelno >= logging.ERROR:
                severity = 3
            elif record.levelno >= logging.WARNING:
                severity = 4
            elif record.levelno >= logging.INFO:
                severity = 6
            else:
                severity = 7

            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + "".join(traceback.format_exception(*record.exc_info))

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            app_subname = record.name.split(".")[-1] if "." in record.name else record.name
            log_entry = {
                "timestamp": now_iso,
                "received_at": now_iso,
                "source_ip": "127.0.0.1",
                "source_alias": "logshed",
                "app_name": app_subname,
                "facility": 1,
                "severity": severity,
                "message": msg,
                "raw": f"[{now_iso}] [{record.name}] [{record.levelname}] {msg}",
            }

            from app.services.drop_filter import get_drop_filter
            drop_filter = get_drop_filter()
            if drop_filter.should_drop(
                log_entry.get("source_alias"),
                log_entry.get("source_ip"),
                log_entry.get("app_name"),
                log_entry.get("message", ""),
                severity=log_entry.get("severity"),
            ) is not None:
                increment_dropped_by_filter_count(1)
                return

            try:
                queue = get_queue()
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    if threading.current_thread() is threading.main_thread():
                        try:
                            queue.put_nowait(log_entry)
                        except Exception:
                            pass
                    else:
                        def _threadsafe_put(q, entry):
                            try:
                                q.put_nowait(entry)
                            except Exception:
                                pass
                        loop.call_soon_threadsafe(_threadsafe_put, queue, log_entry)
                else:
                    try:
                        queue.put_nowait(log_entry)
                    except Exception:
                        pass
            except Exception:
                pass
        finally:
            self._thread_local.in_emit = False

# Module-level shared state
_log_queue: Optional[asyncio.Queue] = None
_dropped_logs_total: int = 0
_dropped_logs_lock = threading.Lock()
_dropped_by_filter_total: int = 0
_dropped_by_filter_lock = threading.Lock()
_QUEUE_MAXSIZE = 10000

def get_queue() -> asyncio.Queue:
    """Returns the singleton log queue, creating it if needed."""
    global _log_queue
    if _log_queue is None:
        _log_queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    return _log_queue

def increment_dropped_count(amount: int = 1) -> None:
    """Thread-safe increment of the dropped logs counter."""
    global _dropped_logs_total
    with _dropped_logs_lock:
        _dropped_logs_total += amount

def get_dropped_count() -> int:
    """Returns the total number of logs dropped due to queue overflow."""
    with _dropped_logs_lock:
        return _dropped_logs_total

def increment_dropped_by_filter_count(amount: int = 1) -> None:
    """Thread-safe increment of the filtered drop counter."""
    global _dropped_by_filter_total
    with _dropped_by_filter_lock:
        _dropped_by_filter_total += amount

def get_dropped_by_filter_count() -> int:
    """Returns the total number of logs discarded by drop rules."""
    with _dropped_by_filter_lock:
        return _dropped_by_filter_total


class IngestionRateTracker:
    """
    Thread-safe throughput counter calculating instantaneous ingestion rate
    (logs per second) over a rolling window (default 5.0 seconds).
    """
    def __init__(self, window_seconds: float = 5.0):
        self._window = window_seconds
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def record(self, count: int = 1) -> None:
        """Record ingested log count at current monotonic timestamp."""
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, count))
            self._prune(now)

    def get_rate(self) -> float:
        """Calculate logs per second over the rolling window."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            total = sum(c for _, c in self._samples)
            return round(total / self._window, 2)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def reset(self) -> None:
        """Clear all recorded samples."""
        with self._lock:
            self._samples.clear()


_rate_tracker = IngestionRateTracker(window_seconds=5.0)


def record_ingest(count: int = 1) -> None:
    """Thread-safe recording of ingested logs."""
    _rate_tracker.record(count)


def get_ingest_rate() -> float:
    """Returns the instantaneous ingestion rate (logs/sec) over the rolling 5-second window."""
    return _rate_tracker.get_rate()


def reset_ingest_rate() -> None:
    """Resets the ingestion rate tracker (useful for tests)."""
    _rate_tracker.reset()


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\)|[@-Z\\-_])")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_log_text(text: str) -> str:
    """Strip ANSI color/formatting codes and non-printable control characters."""
    if not text:
        return text
    return _CONTROL_CHARS_RE.sub("", _ANSI_ESCAPE_RE.sub("", text))


SEVERITY_LEVEL_MAP = {
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
    "log": 6,
    "info": 6,
    "informational": 6,
    "debug": 7,
    "trace": 7,
    "verbose": 7,
}

_RE_KV = re.compile(r"""\b(?:level|lvl|severity)\s*=\s*["']?([a-zA-Z]+)["']?""")
_RE_JSON = re.compile(r"""["'](?:level|severity)["']\s*:\s*["']([a-zA-Z]+)["']""")
_RE_BRACKET = re.compile(r"""\[\s*([a-zA-Z]+)\s*\]""")
_RE_COLON = re.compile(r"""(?:^|[\s\]])([a-zA-Z]+):(?:\s|$)""")
_RE_AFTER_TS = re.compile(
    r"""^(?:[0-9T:.,Z+-]{8,}|\w{3}\s+\d+\s+[0-9:]{8})(?:\s+[0-9:.,Z+-]+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T))?\s+\[?([a-zA-Z]+)\]?\b"""
)


def detect_severity(raw_line: str) -> int:
    """
    Detect log severity from line content, falling back to RFC Info (severity 6).
    Inspects common log formats (logfmt, JSON, brackets, prefix: colon, timestamps)
    without misinterpreting informational messages emitted on Docker stderr.
    """
    clean = clean_log_text(raw_line).strip()
    if not clean:
        return 6

    if clean.startswith("Traceback (most recent call last):") or clean.startswith("Exception:"):
        return 3
    if clean.startswith("panic:"):
        return 2

    header = clean[:200]

    for regex in (_RE_KV, _RE_JSON, _RE_BRACKET, _RE_COLON, _RE_AFTER_TS):
        for m in regex.finditer(header):
            lvl = m.group(1).lower()
            if lvl in SEVERITY_LEVEL_MAP:
                return SEVERITY_LEVEL_MAP[lvl]

    return 6


_RE_PYTHON_EXCEPTION = re.compile(
    r"^(?:[a-zA-Z_]\w*\.)*[a-zA-Z_]\w*(?:Error|Exception|Warning|Interrupt|Exit|Fault|Notice|Iteration)(?::\s*.*)?$"
)
_CHAINED_EXCEPTION_PHRASES = (
    "during handling of the above exception",
    "the above exception was the direct cause",
    "exception group traceback",
)


def _is_continuation(line: str, buffered_text: Optional[str] = None) -> bool:
    """
    Checks if a line is a continuation line for multi-line logs.
    """
    if not line:
        return bool(buffered_text and "traceback" in buffered_text.lower())
        
    if line[0] in (' ', '\t'):
        return True
        
    lower_line = line.lower().strip()
    if lower_line.startswith("caused by:"):
        return True
    if lower_line.startswith("traceback"):
        return True
    if line.startswith("at "):
        return True
    if line.startswith("..."):
        return True
    if lower_line.startswith("goroutine "):
        return True
    if lower_line.startswith("stack backtrace:"):
        return True
    if any(phrase in lower_line for phrase in _CHAINED_EXCEPTION_PHRASES):
        return True

    stripped = line.strip()
    if _RE_PYTHON_EXCEPTION.match(stripped):
        return True

    if buffered_text:
        lower_buf = buffered_text.lower()
        if "traceback (most recent call last):" in lower_buf or 'file "' in lower_buf:
            m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_.]*):(?:\s|$)", stripped)
            if m and m.group(1).lower() not in SEVERITY_LEVEL_MAP:
                return True
        
    return False

MAX_STREAM_LINES = 500
MAX_STREAM_BYTES = 256 * 1024
MAX_TOTAL_STREAMS = 2000
MAX_STREAM_LIFETIME = 5.0  # 5.0 seconds hard timeout


class KeyedMultilineAssembler:
    """
    Buffers continuation lines keyed by stream_key, flushing assembled
    multi-line entries into the shared queue.
    """
    MAX_STREAM_LINES = MAX_STREAM_LINES
    MAX_STREAM_BYTES = MAX_STREAM_BYTES
    MAX_TOTAL_STREAMS = MAX_TOTAL_STREAMS
    MAX_STREAM_LIFETIME = MAX_STREAM_LIFETIME

    def __init__(
        self,
        flush_timeout: float = 0.150,
        max_stream_lines: int = MAX_STREAM_LINES,
        max_stream_bytes: int = MAX_STREAM_BYTES,
        max_total_streams: int = MAX_TOTAL_STREAMS,
        max_stream_lifetime: float = MAX_STREAM_LIFETIME,
    ):
        # Maps stream_key to a list of entry dicts buffered so far
        self._buffers: dict[str, list[dict]] = {}
        # Maps stream_key to its flush timer handle
        self._timers: dict[str, asyncio.TimerHandle] = {}
        # Maps stream_key to buffer creation monotonic timestamp
        self._stream_start_times: dict[str, float] = {}
        # Maps stream_key to cumulative byte size
        self._stream_bytes: dict[str, int] = {}
        self._flush_timeout = flush_timeout
        self.max_stream_lines = max_stream_lines
        self.max_stream_bytes = max_stream_bytes
        self.max_total_streams = max_total_streams
        self.max_stream_lifetime = max_stream_lifetime

    def get_buffered_text(self, stream_key: str) -> str:
        """Returns concatenated messages in the current buffer for stream_key."""
        buffered = self._buffers.get(stream_key)
        if not buffered:
            return ""
        return "\n".join(e.get("message", "") for e in buffered)

    def get_stream_parent_entry(self, stream_key: str) -> Optional[dict]:
        """Returns the first (parent) entry for the active stream, if present."""
        buffered = self._buffers.get(stream_key)
        if buffered:
            return buffered[0]
        return None

    def get_active_stream_key_for_source(self, source_ip: str) -> Optional[str]:
        """
        Check if there is an active (unflushed) multiline buffer originating from source_ip.
        Prefers active named application streams over ':unknown' streams.
        """
        named_candidates = [
            k for k in self._buffers
            if k.startswith(f"{source_ip}:") and not k.endswith(":unknown") and self._buffers[k]
        ]
        if named_candidates:
            if len(named_candidates) == 1:
                return named_candidates[0]
            return max(
                named_candidates,
                key=lambda k: self._buffers[k][-1].get("received_at", "")
            )

        unknown_key = f"{source_ip}:unknown"
        if self._buffers.get(unknown_key):
            return unknown_key

        return None

    async def feed(self, stream_key: str, entry: dict) -> None:
        """
        Feed a parsed log entry. Entry dict has keys:
        timestamp, received_at, source_ip, source_alias, app_name,
        facility, severity, message, raw
        """
        message = entry.get('message', '')
        buffered_text = self.get_buffered_text(stream_key)
        is_cont = _is_continuation(message, buffered_text)

        # If it's NOT a continuation, but we have buffered content for this stream,
        # we should flush the existing buffer before starting a new one.
        if not is_cont and self._buffers.get(stream_key):
            self._flush_stream_internal(stream_key)

        now = time.monotonic()

        # If stream buffer exists, check maximum lifetime
        if stream_key in self._buffers:
            start_time = self._stream_start_times.get(stream_key, now)
            if now - start_time >= self.max_stream_lifetime:
                self._flush_stream_internal(stream_key)

        # If stream buffer is not active, enforce stream capacity and initialize
        if stream_key not in self._buffers:
            while len(self._buffers) >= self.max_total_streams:
                if self._stream_start_times:
                    oldest_key = min(self._stream_start_times, key=self._stream_start_times.get)
                elif self._buffers:
                    oldest_key = next(iter(self._buffers))
                else:
                    break
                self._flush_stream_internal(oldest_key)
            self._buffers[stream_key] = []
            self._stream_start_times[stream_key] = now
            self._stream_bytes[stream_key] = 0

        # Calculate entry bytes
        msg_bytes = len(message.encode("utf-8"))
        raw_str = entry.get("raw") or ""
        raw_bytes = len(raw_str.encode("utf-8"))
        entry_bytes = max(msg_bytes, raw_bytes)

        # Add to buffer
        self._buffers[stream_key].append(entry)
        self._stream_bytes[stream_key] = self._stream_bytes.get(stream_key, 0) + entry_bytes

        # Check line count and byte length limits
        if (
            len(self._buffers[stream_key]) >= self.max_stream_lines
            or self._stream_bytes[stream_key] >= self.max_stream_bytes
        ):
            self._flush_stream_internal(stream_key)
            return

        # Reset timer
        if stream_key in self._timers:
            self._timers[stream_key].cancel()

        elapsed = now - self._stream_start_times.get(stream_key, now)
        remaining_lifetime = self.max_stream_lifetime - elapsed
        timeout = min(self._flush_timeout, max(0.0, remaining_lifetime))

        if timeout <= 0.0:
            self._flush_stream_internal(stream_key)
            return

        loop = asyncio.get_running_loop()
        self._timers[stream_key] = loop.call_later(
            timeout,
            self._flush_stream_internal,
            stream_key
        )

    def _flush_stream_internal(self, stream_key: str) -> None:
        """
        Internal flush logic.
        Takes buffered lines, merges them, and puts to the shared queue.
        """
        if stream_key in self._timers:
            self._timers[stream_key].cancel()
            del self._timers[stream_key]

        self._stream_start_times.pop(stream_key, None)
        self._stream_bytes.pop(stream_key, None)

        buffered = self._buffers.pop(stream_key, None)
        if not buffered:
            return

        # Merge buffered entries
        first_entry = buffered[0]
        if len(buffered) == 1:
            merged_entry = first_entry
        else:
            merged_message = "\n".join(e.get('message', '') for e in buffered)
            merged_raw = "\n".join(e.get('raw', '') for e in buffered)
            # Minimum severity is the most severe
            min_severity = min((e.get('severity', 7) for e in buffered))

            merged_entry = first_entry.copy()
            merged_entry['message'] = merged_message
            merged_entry['raw'] = merged_raw
            merged_entry['severity'] = min_severity

        # Check drop rules before enqueuing
        from app.services.drop_filter import get_drop_filter
        drop_filter = get_drop_filter()
        if drop_filter.should_drop(
            merged_entry.get('source_alias'),
            merged_entry.get('source_ip'),
            merged_entry.get('app_name'),
            merged_entry.get('message', ''),
            severity=merged_entry.get('severity'),
        ) is not None:
            increment_dropped_by_filter_count(1)
            return

        queue = get_queue()
        try:
            queue.put_nowait(merged_entry)
        except asyncio.QueueFull:
            increment_dropped_count(1)

    async def flush_all(self) -> None:
        """Flush all streams. Called on shutdown."""
        keys = list(self._buffers.keys())
        for k in keys:
            self._flush_stream_internal(k)

_QUEUE_SENTINEL = object()


class QueueConsumer:
    """
    Background task that drains the shared queue and batch-inserts into SQLite.
    """
    def __init__(
        self,
        db_path: str | Path,
        debounce_seconds: float = 0.05,
        fts_indexer: Optional[Any] = None,
        alert_evaluator: Optional[Any] = None,
    ):
        self._db_path = Path(db_path)
        self._debounce_seconds = debounce_seconds
        self._fts_indexer = fts_indexer
        self._alert_evaluator = alert_evaluator
        self._started = False
        self._running = False
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._drain_done = asyncio.Event()
        self._drain_lock = asyncio.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_lock = threading.Lock()

    def set_fts_indexer(self, fts_indexer: Any) -> None:
        """Register FTSIndexWorker instance for immediate post-commit notification."""
        self._fts_indexer = fts_indexer

    def set_alert_evaluator(self, alert_evaluator: Any) -> None:
        """Register AlertEvaluator instance for post-commit batch evaluation."""
        self._alert_evaluator = alert_evaluator

    def _get_connection(self) -> sqlite3.Connection:
        """Returns or opens a persistent connection configured with WAL and performance PRAGMAs."""
        with self._conn_lock:
            if self._conn is None:
                conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute("PRAGMA foreign_keys=ON;")
                self._conn = conn
            return self._conn

    def _close_conn(self) -> None:
        """Cleanly close the persistent SQLite connection."""
        with self._conn_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    async def run(self) -> None:
        """Main loop: drain queue with debounce-based batching (up to 5000 records or debounce window)."""
        self._started = True
        self._running = True
        self._stop_event.clear()
        self._drain_done.clear()
        queue = get_queue()

        # Open persistent connection on consumer start
        await asyncio.to_thread(self._get_connection)

        try:
            while self._running and not self._stopping:
                # Wait for the first item
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                if item is _QUEUE_SENTINEL:
                    queue.task_done()
                    break

                batch = [item]
                batch_start = time.monotonic()

                # Drain up to 5000 items
                while len(batch) < 5000 and self._running and not self._stopping:
                    # First try immediate drain of available items
                    try:
                        next_item = queue.get_nowait()
                        if next_item is _QUEUE_SENTINEL:
                            queue.task_done()
                            self._running = False
                            break
                        batch.append(next_item)
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    # If queue is empty, wait for next item up to remaining debounce window
                    elapsed = time.monotonic() - batch_start
                    remaining = self._debounce_seconds - elapsed
                    if remaining <= 0:
                        break

                    try:
                        next_item = await asyncio.wait_for(queue.get(), timeout=remaining)
                        if next_item is _QUEUE_SENTINEL:
                            queue.task_done()
                            self._running = False
                            break
                        batch.append(next_item)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        break

                if batch:
                    success = await self._flush_batch(batch, queue)
                    if not success and (not self._running or self._stopping):
                        break
        finally:
            self._running = False
            try:
                async with self._drain_lock:
                    await self._drain_queue(queue)
            finally:
                self._drain_done.set()

    async def _flush_batch(self, batch: list[dict], queue: asyncio.Queue) -> bool:
        """Helper to insert batch with retry backoff and broadcast to SSE."""
        if not batch:
            return True
        inserted = False
        backoff = 0.05 if (not self._running or self._stopping) else 0.5
        shutdown_retries = 0
        max_shutdown_retries = 5

        while True:
            try:
                await asyncio.to_thread(self._insert_batch, batch)
                inserted = True
                break
            except asyncio.CancelledError:
                # If cancelled during flush, attempt a shielded insert so in-flight logs are preserved
                try:
                    await asyncio.shield(asyncio.to_thread(self._insert_batch, batch))
                    inserted = True
                    break
                except Exception:
                    raise
            except Exception as e:
                if not self._running or self._stopping:
                    shutdown_retries += 1
                    if shutdown_retries >= max_shutdown_retries:
                        logger.error(
                            f"Shutdown drain failed to insert batch of {len(batch)} logs after {max_shutdown_retries} attempts: {e}."
                        )
                        break
                    logger.warning(
                        f"Retry {shutdown_retries}/{max_shutdown_retries} during shutdown for batch of {len(batch)} logs: {e}. Retrying in {backoff:.2f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 1.0)
                else:
                    logger.warning(
                        f"Error inserting batch of {len(batch)} logs: {e}. Retrying in {backoff:.1f}s..."
                    )
                    try:
                        await asyncio.sleep(backoff)
                    except asyncio.CancelledError:
                        try:
                            await asyncio.shield(asyncio.to_thread(self._insert_batch, batch))
                            inserted = True
                            break
                        except Exception:
                            raise
                    backoff = min(backoff * 2.0, 10.0)

        if inserted:
            record_ingest(len(batch))
            # Broadcast to SSE subscribers on the event loop thread
            from app.core.sse import sse_manager
            await sse_manager.broadcast_batch(batch)

            # Evaluate alert rules on ingested batch
            if self._alert_evaluator is not None:
                try:
                    await self._alert_evaluator.evaluate_batch(batch)
                except Exception as e:
                    logger.warning(f"Error evaluating alert rules on batch: {e}")

            # Mark as done
            for _ in batch:
                queue.task_done()
            return True
        else:
            # Mark failed batch items as done so queue is not stuck
            for _ in batch:
                queue.task_done()
            return False

    async def _drain_queue(self, queue: asyncio.Queue) -> None:
        """Drain all remaining items in queue in batches of up to 5000 and commit to SQLite."""
        while not queue.empty():
            batch = []
            while len(batch) < 5000:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is _QUEUE_SENTINEL:
                    queue.task_done()
                    continue
                batch.append(item)
            if batch:
                success = await self._flush_batch(batch, queue)
                if not success:
                    # If flush failed after all retries, clean up remaining queue items
                    while not queue.empty():
                        try:
                            rem = queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                    break

    async def stop(self) -> None:
        """
        Signal graceful shutdown, drain all remaining items in _log_queue,
        commit them to SQLite in final batch(es), and exit only once the queue is empty.
        """
        logger.info("QueueConsumer stopping: draining pending logs...")
        self._stopping = True
        self._stop_event.set()
        queue = get_queue()

        try:
            queue.put_nowait(_QUEUE_SENTINEL)
        except asyncio.QueueFull:
            pass

        # Yield control briefly so any scheduled consumer.run() task can start/react
        await asyncio.sleep(0)

        if self._started:
            try:
                await asyncio.shield(self._drain_done.wait())
            except asyncio.CancelledError:
                await self._drain_done.wait()
                raise
        else:
            async with self._drain_lock:
                await self._drain_queue(queue)

        if not queue.empty():
            async with self._drain_lock:
                await self._drain_queue(queue)

        await asyncio.to_thread(self._close_conn)
        logger.info("QueueConsumer stopped: all pending logs drained and committed.")

    def _insert_batch(self, batch: list[dict]) -> None:
        """Synchronous: insert batch into SQLite using chunked multi-row queries and assign generated row IDs."""
        if not batch:
            return

        conn = self._get_connection()
        utc = datetime.timezone.utc
        local_tz = None

        try:
            cursor = conn.cursor()
            for entry in batch:
                # Defensive clamp & UTC normalization: ensure timestamp is in canonical UTC
                # and no entry is saved with a timestamp in the future compared to received_at
                try:
                    ts_val = entry.get("timestamp")
                    rec_val = entry.get("received_at")
                    if ts_val:
                        # Fast-path: if both timestamp and received_at are canonical UTC ISO strings and not in the future
                        if (
                            isinstance(ts_val, str)
                            and ts_val.endswith("+00:00")
                            and isinstance(rec_val, str)
                            and rec_val.endswith("+00:00")
                            and ts_val <= rec_val
                        ):
                            pass
                        else:
                            clean_ts = ts_val[:-1] + "+00:00" if isinstance(ts_val, str) and ts_val.endswith("Z") else str(ts_val)
                            dt_ts = datetime.datetime.fromisoformat(clean_ts)
                            if dt_ts.tzinfo is None:
                                if local_tz is None:
                                    local_tz = datetime.datetime.now().astimezone().tzinfo or utc
                                dt_ts = dt_ts.replace(tzinfo=local_tz).astimezone(utc)
                            else:
                                dt_ts = dt_ts.astimezone(utc)

                            if rec_val:
                                clean_rec = rec_val[:-1] + "+00:00" if isinstance(rec_val, str) and rec_val.endswith("Z") else str(rec_val)
                                dt_rec = datetime.datetime.fromisoformat(clean_rec)
                                if dt_rec.tzinfo is None:
                                    dt_rec = dt_rec.replace(tzinfo=utc)
                                else:
                                    dt_rec = dt_rec.astimezone(utc)

                                if (dt_ts - dt_rec).total_seconds() > 60:
                                    dt_ts = dt_rec

                            entry["timestamp"] = dt_ts.isoformat()
                except Exception:
                    if entry.get("received_at"):
                        entry["timestamp"] = entry["received_at"]

            # Chunked multi-row parameterized insert with RETURNING id (chunk size <= 500)
            chunk_size = 500
            row_placeholder = "(?, ?, ?, ?, ?, ?, ?, ?, ?)"
            for i in range(0, len(batch), chunk_size):
                chunk = batch[i:i + chunk_size]
                placeholders = ", ".join([row_placeholder] * len(chunk))
                sql = f"""
                    INSERT INTO logs (
                        timestamp, received_at, source_ip, source_alias,
                        app_name, facility, severity, message, raw
                    ) VALUES {placeholders} RETURNING id
                """
                params = []
                for entry in chunk:
                    params.extend([
                        entry.get("timestamp"),
                        entry.get("received_at"),
                        entry.get("source_ip", ""),
                        entry.get("source_alias", ""),
                        entry.get("app_name", ""),
                        entry.get("facility", 1),
                        entry.get("severity", 6),
                        entry.get("message", ""),
                        entry.get("raw", ""),
                    ])
                cursor.execute(sql, params)
                returned_rows = cursor.fetchall()
                if len(returned_rows) != len(chunk):
                    raise RuntimeError(
                        f"Returned ID count ({len(returned_rows)}) does not match chunk size ({len(chunk)})"
                    )
                for entry, (row_id,) in zip(chunk, returned_rows):
                    entry["id"] = row_id

            conn.commit()
            if self._fts_indexer is not None:
                try:
                    self._fts_indexer.notify_new_logs()
                except Exception as e:
                    logger.warning(f"Failed to notify FTS indexer: {e}")
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                self._close_conn()
            raise e
