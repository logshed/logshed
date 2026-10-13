"""
Shared guard for heavy database operations (retention prune and VACUUM compaction).

A single process-wide in-progress lock keeps prune and vacuum mutually exclusive across the
external API, the internal UI endpoints and the PruneWorker. The lock is acquired without
waiting, so contention never blocks the event loop; callers either report a conflict (409) or
skip and retry later. The cooldown tracker records the monotonic time of the last successful
run of each operation and is only updated after success.
"""

import asyncio
import math
import threading
import time
from contextlib import contextmanager
from typing import Awaitable, Callable, Iterator, Optional, TypeVar

T = TypeVar("T")

OPERATION_COOLDOWN_SECONDS: float = 300.0  # 5 minutes

_in_progress_lock = threading.Lock()
_active_operation: Optional[str] = None
_state_lock = threading.Lock()
_last_success: dict[str, float] = {}


class OperationInProgressError(Exception):
    """Raised when a prune or vacuum is already running."""

    def __init__(self, active: Optional[str]):
        self.active = active
        super().__init__("A prune or compaction operation is already in progress.")


@contextmanager
def operation_guard(name: str) -> Iterator[None]:
    """Hold the shared in-progress lock for the duration of the block, or raise immediately."""
    global _active_operation
    if not _in_progress_lock.acquire(blocking=False):
        raise OperationInProgressError(_active_operation)
    with _state_lock:
        _active_operation = name
    try:
        yield
    finally:
        with _state_lock:
            _active_operation = None
        _in_progress_lock.release()


def cooldown_remaining_seconds(name: str) -> int:
    """Whole seconds left before the named operation may run again (0 when allowed)."""
    with _state_lock:
        last = _last_success.get(name)
    if last is None:
        return 0
    remaining = OPERATION_COOLDOWN_SECONDS - (time.monotonic() - last)
    return max(0, math.ceil(remaining))


def mark_operation_success(name: str) -> None:
    """Start the cooldown for the named operation. Call only after a successful run."""
    with _state_lock:
        _last_success[name] = time.monotonic()


def reset_operation_state() -> None:
    """Clear cooldowns (used by tests)."""
    with _state_lock:
        _last_success.clear()


async def run_exclusive(name: str, work: Callable[[], Awaitable[T]]) -> T:
    """
    Run ``work`` while holding the shared in-progress lock, or raise immediately when busy.

    The work runs in its own task behind ``asyncio.shield``. If the caller is cancelled (for
    example the HTTP client disconnects mid-prune), the work and its worker thread keep running
    and the lock is released only once the work has really finished, so a second prune or
    vacuum can never overlap with one still running in a thread.
    """
    global _active_operation
    if not _in_progress_lock.acquire(blocking=False):
        raise OperationInProgressError(_active_operation)
    with _state_lock:
        _active_operation = name

    async def _runner() -> T:
        global _active_operation
        try:
            return await work()
        finally:
            with _state_lock:
                _active_operation = None
            _in_progress_lock.release()

    task = asyncio.ensure_future(_runner())
    # Retrieve any exception from an orphaned task (caller cancelled) so it is not reported as unretrieved
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return await asyncio.shield(task)
