"""
Lightweight SQLite migration runner for LogShed.

This module provides a simple mechanism to manage SQLite database schema versions
using `PRAGMA user_version`. It ensures that database schemas are set up
and upgraded sequentially during application startup.

It uses the standard library `sqlite3` module exclusively and is designed to
be executed synchronously (e.g., via `asyncio.to_thread` from an async context).
All database connections opened through this module will automatically have WAL
mode and other performance pragmas enabled.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

def get_connection(db_path: Union[str, Path], check_same_thread: bool = False) -> sqlite3.Connection:
    """
    Open a SQLite database connection and configure WAL mode pragmas.

    Args:
        db_path: Path to the SQLite database file.
        check_same_thread: If False, allow SQLite connection reuse across threads.

    Returns:
        A configured sqlite3.Connection instance.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    # Ensure foreign keys are enforced
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def get_user_version(conn: sqlite3.Connection) -> int:
    """
    Read the current `PRAGMA user_version` from the database.

    Args:
        conn: The active sqlite3 connection.

    Returns:
        The current schema version as an integer.
    """
    cursor = conn.execute("PRAGMA user_version;")
    result = cursor.fetchone()
    return result[0] if result else 0

def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """
    Set the `PRAGMA user_version` in the database.

    Args:
        conn: The active sqlite3 connection.
        version: The new version integer to set.
    """
    conn.execute(f"PRAGMA user_version = {version};")

def migrate_v1(conn: sqlite3.Connection) -> None:
    """
    Execute the full v1 DDL schema. This is Migration 1.

    Args:
        conn: The active sqlite3 connection.
    """
    logger.info("Running migration v1...")
    conn.executescript('''
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    received_at DATETIME NOT NULL,
    source_ip TEXT NOT NULL,
    source_alias TEXT NOT NULL,
    app_name TEXT NOT NULL,
    facility INTEGER NOT NULL DEFAULT 1,
    severity INTEGER NOT NULL DEFAULT 6,
    message TEXT NOT NULL,
    raw TEXT NOT NULL
);

CREATE INDEX idx_logs_time_sev ON logs(timestamp DESC, severity);
CREATE INDEX idx_logs_app_time ON logs(app_name, timestamp DESC);
CREATE INDEX idx_logs_src_time ON logs(source_alias, timestamp DESC);
CREATE INDEX idx_logs_source_ip ON logs(source_ip);
CREATE INDEX idx_logs_source_app_ip ON logs(source_alias, app_name, source_ip);

CREATE VIRTUAL TABLE logs_fts USING fts5(
    app_name,
    source_alias,
    message,
    content='logs',
    content_rowid='id'
);

CREATE TRIGGER logs_ai AFTER INSERT ON logs BEGIN
    INSERT INTO logs_fts(rowid, app_name, source_alias, message)
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TRIGGER logs_ad AFTER DELETE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message)
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
END;

CREATE TRIGGER logs_au AFTER UPDATE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message)
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
    INSERT INTO logs_fts(rowid, app_name, source_alias, message)
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TABLE host_aliases (
    ip TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    notes TEXT,
    created_at DATETIME NOT NULL
);

CREATE TABLE storage_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at DATETIME NOT NULL,
    db_size_bytes INTEGER NOT NULL,
    disk_free_bytes INTEGER NOT NULL,
    disk_total_bytes INTEGER NOT NULL,
    total_logs_count INTEGER NOT NULL
);

CREATE INDEX idx_storage_metrics_time ON storage_metrics(recorded_at DESC);

CREATE TABLE ai_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    source_alias TEXT NOT NULL,
    app_name TEXT NOT NULL,
    log_count INTEGER NOT NULL,
    user_context TEXT,
    model TEXT NOT NULL,
    prompt_sent TEXT NOT NULL,
    response_text TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_thoughts INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    system_prompt TEXT
);

CREATE TABLE admin_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME NOT NULL,
    is_encrypted BOOLEAN DEFAULT 0
);

INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
VALUES ('retention_days', '14', datetime('now'), 0);
''')


# Registry of migrations to run. Must be ordered by version ascending.
def migrate_v2(conn: sqlite3.Connection) -> None:
    """
    Execute Migration 2: Decoupled asynchronous FTS5 indexing, drop rules, and saved views.
    - Drop synchronous logs_ai trigger on logs.
    - Create fts_index_state tracking table.
    - Initialize last_indexed_id to MAX(id) of existing logs.
    - Recreate logs_ad and logs_au with WHEN condition guarding against unindexed rows.
    - Create drop_rules table and idx_drop_rules_enabled index.
    - Create saved_views table and idx_saved_views_pinned index.
    """
    logger.info("Running migration v2...")
    conn.executescript('''
DROP TRIGGER IF EXISTS logs_ai;

CREATE TABLE IF NOT EXISTS fts_index_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_indexed_id INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL
);

INSERT OR IGNORE INTO fts_index_state (id, last_indexed_id, updated_at)
VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM logs), datetime('now'));

DROP TRIGGER IF EXISTS logs_ad;
CREATE TRIGGER logs_ad AFTER DELETE ON logs
WHEN old.id <= (SELECT last_indexed_id FROM fts_index_state WHERE id = 1)
BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message)
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
END;

DROP TRIGGER IF EXISTS logs_au;
CREATE TRIGGER logs_au AFTER UPDATE ON logs
WHEN old.id <= (SELECT last_indexed_id FROM fts_index_state WHERE id = 1)
BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message)
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
    INSERT INTO logs_fts(rowid, app_name, source_alias, message)
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TABLE IF NOT EXISTS drop_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    source_pattern TEXT,
    app_pattern TEXT,
    message_pattern TEXT NOT NULL,
    is_regex BOOLEAN NOT NULL DEFAULT 0,
    is_enabled BOOLEAN NOT NULL DEFAULT 1,
    severity_threshold INTEGER,
    dropped_count INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drop_rules_enabled ON drop_rules(is_enabled);

CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query_params TEXT NOT NULL,
    is_pinned BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_views_pinned ON saved_views(is_pinned, name);

CREATE TABLE IF NOT EXISTS notification_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_channels_enabled ON notification_channels(is_enabled);

CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    channel_id INTEGER REFERENCES notification_channels(id) ON DELETE SET NULL,
    filter_app TEXT,
    filter_severity INTEGER,
    match_pattern TEXT,
    threshold_count INTEGER DEFAULT 1,
    window_seconds INTEGER DEFAULT 60,
    cooldown_seconds INTEGER DEFAULT 300,
    ai_enrichment BOOLEAN DEFAULT 0,
    is_enabled BOOLEAN NOT NULL DEFAULT 1,
    trigger_count INTEGER NOT NULL DEFAULT 0,
    last_triggered_at DATETIME,
    suppress_until DATETIME,
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON alert_rules(is_enabled);

CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
    rule_name TEXT NOT NULL,
    channel_id INTEGER,
    trigger_count INTEGER NOT NULL DEFAULT 1,
    sample_log TEXT,
    incident_summary TEXT,
    ai_enrichment BOOLEAN DEFAULT 0,
    ai_model TEXT,
    ai_audit_id INTEGER REFERENCES ai_audit_log(id) ON DELETE SET NULL,
    triggered_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alert_history_triggered_at ON alert_history(triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_rule_id ON alert_history(rule_id);
CREATE INDEX IF NOT EXISTS idx_alert_history_rule_time ON alert_history(rule_id, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_rule_name ON alert_history(rule_name, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_ai_audit_id ON alert_history(ai_audit_id);

INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
VALUES ('daily_digest_enabled', '0', datetime('now'), 0);
INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
VALUES ('daily_digest_schedule_time', '09:00', datetime('now'), 0);
INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
VALUES ('retention_days', '14', datetime('now'), 0);

CREATE INDEX IF NOT EXISTS idx_logs_source_app_ip ON logs(source_alias, app_name, source_ip);
''')

    try:
        conn.execute("ALTER TABLE ai_audit_log ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'on-demand'")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE drop_rules ADD COLUMN name TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE alert_history ADD COLUMN ai_model TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE alert_history ADD COLUMN ai_audit_id INTEGER REFERENCES ai_audit_log(id) ON DELETE SET NULL")
    except sqlite3.OperationalError:
        pass

    # Defensively clamp future-dated timestamps using indexed timestamp bounds
    # to avoid expensive full-table scans across historical logs on boot.
    try:
        conn.execute(
            "UPDATE logs SET timestamp = received_at "
            "WHERE timestamp > strftime('%Y-%m-%dT%H:%M:%S', 'now', '+1 minute') AND timestamp > received_at;"
        )
    except sqlite3.OperationalError:
        pass

    # Backfill drop_rules name if empty
    try:
        conn.execute(
            "UPDATE drop_rules SET name = COALESCE(NULLIF(TRIM(name), ''), NULLIF(TRIM(app_pattern), ''), NULLIF(TRIM(source_pattern), ''), 'Drop Rule') WHERE name IS NULL OR TRIM(name) = '';"
        )
    except sqlite3.OperationalError:
        pass

    # Backfill on-demand ai_audit_log entries into alert_history
    try:
        conn.execute("""
            INSERT INTO alert_history (
                rule_id, rule_name, channel_id, trigger_count, sample_log,
                incident_summary, ai_enrichment, ai_model, ai_audit_id, triggered_at
            )
            SELECT
                NULL,
                'On-Demand Analysis',
                NULL,
                a.log_count,
                NULL,
                a.response_text,
                1,
                a.model,
                a.id,
                a.timestamp
            FROM ai_audit_log a
            WHERE (a.trigger_source = 'on-demand' OR a.trigger_source IS NULL)
              AND NOT EXISTS (
                  SELECT 1 FROM alert_history h WHERE h.ai_audit_id = a.id
              );
        """)
    except sqlite3.OperationalError:
        pass


MIGRATIONS = [
    (1, migrate_v1),
    (2, migrate_v2),
]

def run_migrations(db_path: Union[str, Path]) -> None:
    """
    Main entry point for database migrations.

    Opens the database, enables WAL mode pragmas, and runs all pending migrations
    in order based on the current PRAGMA user_version.

    Args:
        db_path: Path to the SQLite database file.
    """
    db_path = Path(db_path)
    # Ensure the parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_path)
    try:
        current_version = get_user_version(conn)
        logger.info(f"Current database version: {current_version}")

        for target_version, migration_func in MIGRATIONS:
            if current_version < target_version:
                logger.info(f"Migrating from {current_version} to {target_version}")
                try:
                    migration_func(conn)
                    set_user_version(conn, target_version)
                    conn.commit()
                    current_version = target_version
                    logger.info(f"Successfully migrated to version {target_version}")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Migration to version {target_version} failed: {e}")
                    raise
            else:
                logger.debug(f"Skipping migration {target_version}, already applied.")
    finally:
        conn.close()

