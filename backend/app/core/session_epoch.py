"""
The session epoch: every session token carries the epoch it was issued under, and a token under an
older epoch is refused. Logout, a password change, reset-admin and a restore raise the stored epoch,
which ends every session issued before. Plain SQL on a connection, so the CLI can use it without the
web app.
"""

import datetime
import sqlite3

SESSION_EPOCH_KEY = "session_epoch"


def read_session_epoch(conn: sqlite3.Connection) -> int:
    """The stored session epoch (0 when none was ever stored, the value is unreadable or there is no settings table)."""
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (SESSION_EPOCH_KEY,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    try:
        return int(row[0]) if row and row[0] is not None else 0
    except (TypeError, ValueError):
        return 0


def bump_session_epoch(conn: sqlite3.Connection, at_least: int = 0) -> int:
    """
    Raise the stored session epoch by one (to at least `at_least` + 1) inside the caller's
    transaction (the caller commits), and return the new value.
    """
    new_epoch = max(read_session_epoch(conn), at_least) + 1
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES (?, ?, ?, 0) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (SESSION_EPOCH_KEY, str(new_epoch), now),
    )
    return new_epoch
