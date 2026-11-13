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



def store_logs(db_path, *entries: dict) -> list[int]:
    """
    Store log rows through the log store, as ingest does, and return their ids. Each entry
    holds the row fields (timestamp, received_at, source_ip, source_alias, app_name, facility,
    severity, message, raw); a missing received_at is the timestamp.
    """
    from app.services import log_store

    rows = [{"received_at": e.get("timestamp"), **e} for e in entries]
    with open_db(db_path) as conn:
        log_store.insert_rows(conn, rows)
    return [row["id"] for row in rows]


def stored_logs(db_path) -> list[dict]:
    """Every stored log (in day files and the legacy table) as a dict of its row fields, by id."""
    import sqlite3

    from app.services import log_store
    from app.services.log_query import LogFilter

    with open_db(db_path, sqlite3.Row) as conn:
        total = log_store.count_rows(conn)
        _, rows = log_store.search(conn, LogFilter(), fetch=max(total, 1), count_limit=max(total, 1))
    return sorted((dict(r) for r in rows), key=lambda r: r["id"])


def count_logs(db_path) -> int:
    """How many logs are stored, in day files and the legacy table."""
    from app.services import log_store

    with open_db(db_path) as conn:
        return log_store.count_rows(conn)


def fts_matches(db_path, term: str) -> int:
    """
    How many rows match a full-text term in the legacy logs_fts and every day file's, read
    directly (a search through the store would wake the indexer itself).
    """
    import sqlite3
    from pathlib import Path

    total = 0
    with open_db(db_path) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'logs_fts'").fetchone():
            total += conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?", (term,)).fetchone()[0]
    for day_file in sorted(Path(db_path).parent.glob("logs-*-UTC.db")):
        with open_db(day_file, connect=lambda p: sqlite3.connect(p)) as conn:
            total += conn.execute("SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH ?", (term,)).fetchone()[0]
    return total
