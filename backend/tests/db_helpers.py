"""Database helpers shared by the test modules."""

from contextlib import contextmanager

from app.core.migrations import get_connection


@contextmanager
def open_db(db_path, row_factory=None, connect=get_connection):
    """
    Open a connection that commits on success, rolls back on an error and is always closed.

    `with sqlite3.connect(...) as conn:` alone commits but leaves the connection open, and
    Python 3.13 and later warn about every connection freed without `close()`.
    """
    conn = connect(str(db_path))
    if row_factory is not None:
        conn.row_factory = row_factory
    try:
        with conn:
            yield conn
    finally:
        conn.close()

