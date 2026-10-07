"""
FastAPI dependency injectors and database execution helpers for LogShed.
All SQLite queries are dispatched via asyncio.to_thread() to keep the event loop non-blocking.
"""

import asyncio
import datetime
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_db_path
from app.core.migrations import get_connection
from app.core.security import SESSION_COOKIE_NAME, verify_session_token

T = TypeVar("T")

_cached_admin_updated_at: Optional[float] = None
_cached_admin_updated_at_time: float = 0.0
_ADMIN_CACHE_TTL: float = 5.0  # seconds


def invalidate_admin_auth_cache() -> None:
    """Invalidate cached admin password updated_at timestamp."""
    global _cached_admin_updated_at, _cached_admin_updated_at_time
    _cached_admin_updated_at = None
    _cached_admin_updated_at_time = 0.0


_thread_local = threading.local()
_all_connections: set[sqlite3.Connection] = set()
_all_connections_lock = threading.Lock()


def get_thread_read_connection(db_path: Path) -> sqlite3.Connection:
    """
    Get or open a cached thread-local read connection for the given database path.
    Configures WAL mode and performance pragmas once upon connection creation.
    """
    if not hasattr(_thread_local, "connections"):
        _thread_local.connections = {}

    resolved_key = str(Path(db_path).resolve())
    conn = _thread_local.connections.get(resolved_key)
    if conn is not None:
        try:
            _ = conn.total_changes
        except sqlite3.ProgrammingError:
            conn = None

    if conn is None:
        conn = sqlite3.connect(resolved_key, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _thread_local.connections[resolved_key] = conn
        with _all_connections_lock:
            _all_connections.add(conn)

    return conn


def close_thread_local_connections() -> None:
    """
    Close all cached SQLite connections on the calling thread and clear the dictionary.
    Useful for test fixtures, thread teardown, and process shutdown.
    """
    if hasattr(_thread_local, "connections"):
        for conn in list(_thread_local.connections.values()):
            try:
                conn.close()
            except Exception:
                pass
        _thread_local.connections.clear()


def reset_all_db_connections() -> None:
    """
    Close all open SQLite connections across all threads.
    Thread-local caches will detect the closed connection and automatically reopen on next use.
    """
    with _all_connections_lock:
        for conn in list(_all_connections):
            try:
                conn.close()
            except Exception:
                pass
        _all_connections.clear()
    close_thread_local_connections()


async def reset_all_db_connections_async() -> None:
    """Async wrapper to reset all database connections."""
    await asyncio.to_thread(reset_all_db_connections)


async def run_db_query(fn: Callable[[sqlite3.Connection], T], custom_db_path: Optional[Path] = None) -> T:
    """
    Executes a synchronous database function in a worker thread using asyncio.to_thread().
    Reuses thread-local read connections across invocations on the same thread.
    """
    db_path = custom_db_path or get_db_path()

    def _execute() -> T:
        conn = get_thread_read_connection(db_path)
        try:
            with conn:
                return fn(conn)
        finally:
            if conn.in_transaction:
                conn.rollback()

    return await asyncio.to_thread(_execute)


async def _is_session_revoked(payload: dict[str, Any]) -> bool:
    """
    Checks whether a session token was issued prior to the latest admin password update.
    Returns True if revoked, False otherwise.
    """
    iat = payload.get("iat")
    if iat is None:
        return False

    global _cached_admin_updated_at, _cached_admin_updated_at_time
    now_mono = time.monotonic()
    updated_at_epoch = _cached_admin_updated_at

    if updated_at_epoch is None or (now_mono - _cached_admin_updated_at_time) > _ADMIN_CACHE_TTL:
        def _get_admin_updated_at(conn: sqlite3.Connection) -> Optional[str]:
            cursor = conn.cursor()
            cursor.execute("SELECT updated_at FROM admin_auth WHERE id = 1")
            row = cursor.fetchone()
            return row[0] if row else None

        updated_at_str = await run_db_query(_get_admin_updated_at)
        if updated_at_str:
            try:
                dt = datetime.datetime.fromisoformat(updated_at_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                updated_at_epoch = dt.timestamp()
                _cached_admin_updated_at = updated_at_epoch
                _cached_admin_updated_at_time = now_mono
            except Exception:
                updated_at_epoch = None
        else:
            _cached_admin_updated_at = None
            _cached_admin_updated_at_time = now_mono

    if updated_at_epoch is not None:
        iat_val = float(iat)
        is_revoked = iat_val < updated_at_epoch
        return is_revoked

    return False


async def get_current_user(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency that validates the signed session cookie.
    Raises 401 Unauthorized if cookie is missing, invalid, or expired.
    Also validates that the session was not issued prior to the latest admin password update.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    payload = verify_session_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
        )

    if await _is_session_revoked(payload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to password change",
        )

    return payload


async def get_optional_user(request: Request) -> Optional[dict[str, Any]]:
    """
    FastAPI dependency that extracts user session if present, but does not raise.
    Treats user as unauthenticated if session is invalid, expired, or revoked.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    payload = verify_session_token(token)
    if not payload:
        return None
    if await _is_session_revoked(payload):
        return None
    return payload
