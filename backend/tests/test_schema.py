"""
Tests for database schema, WAL pragmas, FTS5 sync triggers, and migration runner.
"""

import datetime
from pathlib import Path
import sqlite3
import pytest

from app.core.migrations import (
    MIGRATIONS,
    get_connection,
    get_user_version,
    set_user_version,
    run_migrations,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a fresh migrated database and return its path."""
    p = tmp_path / "logs.db"
    run_migrations(p)
    return p


# ===================================================================
# 1. Schema Integrity & Baseline v1.0.0 Checks
# ===================================================================

class TestSchemaIntegrity:

    def test_user_version_set_to_two(self, db_path: Path):
        """After migration runner finishes, user_version should be exactly 2."""
        conn = get_connection(db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 2

    def test_wal_mode_enabled(self, db_path: Path):
        """WAL journal mode and performance pragmas should be active."""
        conn = get_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"
        # synchronous NORMAL is 1
        assert sync == 1
        assert busy == 5000
        assert fk == 1

    def test_all_tables_exist(self, db_path: Path):
        """All baseline v1.0.0 and v2 tables should exist."""
        conn = get_connection(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        expected = {
            "logs",
            "host_aliases",
            "storage_metrics",
            "ai_audit_log",
            "admin_auth",
            "system_settings",
            "fts_index_state",
            "drop_rules",
            "saved_views",
            "notification_channels",
            "alert_rules",
            "alert_history",
        }
        assert expected.issubset(tables)

    def test_fts_table_exists(self, db_path: Path):
        """logs_fts virtual table should exist."""
        conn = get_connection(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "logs_fts" in tables

    def test_triggers_exist(self, db_path: Path):
        """FTS sync triggers: logs_ai is removed in v2, while logs_ad and logs_au are present."""
        conn = get_connection(db_path)
        triggers = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        conn.close()
        assert "logs_ai" not in triggers
        assert {"logs_ad", "logs_au"}.issubset(triggers)

    def test_fts_index_state_table(self, db_path: Path):
        """fts_index_state table must enforce id=1 check constraint and track last_indexed_id."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(fts_index_state);")
        columns = {row[1] for row in cursor.fetchall()}
        assert {"id", "last_indexed_id", "updated_at"}.issubset(columns)

        # Confirm default initialized row
        row = conn.execute("SELECT id, last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] >= 0

        # Check constraint id=1 enforcement
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO fts_index_state (id, last_indexed_id, updated_at) "
                "VALUES (2, 0, datetime('now'))"
            )
        conn.close()

    def test_drop_rules_schema(self, db_path: Path):
        """drop_rules table should contain expected columns and defaults."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(drop_rules);")
        cols = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id",
            "name",
            "source_pattern",
            "app_pattern",
            "message_pattern",
            "is_regex",
            "is_enabled",
            "dropped_count",
            "created_at",
        }
        assert expected.issubset(set(cols.keys()))
        assert cols["message_pattern"][3] == 1  # NOT NULL

    def test_saved_views_schema(self, db_path: Path):
        """saved_views table should contain expected columns and defaults."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(saved_views);")
        cols = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id",
            "name",
            "query_params",
            "is_pinned",
            "created_at",
        }
        assert expected.issubset(set(cols.keys()))
        assert cols["name"][3] == 1  # NOT NULL
        assert cols["query_params"][3] == 1  # NOT NULL

    def test_indexes_exist(self, db_path: Path):
        """B-tree indexes should exist."""
        conn = get_connection(db_path)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()
        expected = {
            "idx_logs_time_sev",
            "idx_logs_app_time",
            "idx_logs_src_time",
            "idx_logs_source_ip",
            "idx_logs_source_app_ip",
            "idx_storage_metrics_time",
            "idx_drop_rules_enabled",
            "idx_saved_views_pinned",
            "idx_notification_channels_enabled",
            "idx_alert_rules_enabled",
            "idx_alert_history_triggered_at",
            "idx_alert_history_rule_id",
            "idx_alert_history_rule_time",
        }
        assert expected.issubset(indexes)

    def test_notification_channels_schema(self, db_path: Path):
        """notification_channels table should contain expected columns and constraints."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(notification_channels);")
        cols = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        expected = {"id", "name", "url", "is_enabled", "created_at", "updated_at"}
        assert expected.issubset(set(cols.keys()))
        assert cols["name"][3] == 1  # NOT NULL
        assert cols["url"][3] == 1  # NOT NULL

    def test_alert_rules_schema(self, db_path: Path):
        """alert_rules table should contain expected columns and constraints."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(alert_rules);")
        cols = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id",
            "name",
            "rule_type",
            "channel_id",
            "filter_app",
            "filter_severity",
            "match_pattern",
            "threshold_count",
            "window_seconds",
            "cooldown_seconds",
            "ai_enrichment",
            "is_enabled",
            "trigger_count",
            "last_triggered_at",
            "suppress_until",
            "created_at",
        }
        assert expected.issubset(set(cols.keys()))
        assert cols["name"][3] == 1  # NOT NULL
        assert cols["rule_type"][3] == 1  # NOT NULL

    def test_alert_history_schema(self, db_path: Path):
        """alert_history table should contain expected columns and constraints."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(alert_history);")
        cols = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id",
            "rule_id",
            "rule_name",
            "channel_id",
            "trigger_count",
            "sample_log",
            "incident_summary",
            "ai_enrichment",
            "triggered_at",
        }
        assert expected.issubset(set(cols.keys()))
        assert cols["rule_name"][3] == 1  # NOT NULL
        assert cols["triggered_at"][3] == 1  # NOT NULL

    def test_ai_audit_log_columns(self, db_path: Path):
        """ai_audit_log should include token metrics and system_prompt columns."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(ai_audit_log);")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id",
            "timestamp",
            "source_alias",
            "app_name",
            "log_count",
            "user_context",
            "model",
            "prompt_sent",
            "response_text",
            "tokens_in",
            "tokens_out",
            "tokens_thoughts",
            "tokens_used",
            "system_prompt",
            "trigger_source",
        }
        assert expected.issubset(columns)

    def test_host_aliases_columns(self, db_path: Path):
        """host_aliases should include notes column."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(host_aliases);")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert {"ip", "alias", "notes", "created_at"}.issubset(columns)

    def test_admin_auth_single_row_constraint(self, db_path: Path):
        """admin_auth should enforce id=1 check constraint."""
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
            "VALUES (1, 'hash', '2024-01-01', '2024-01-01')"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
                "VALUES (2, 'hash2', '2024-01-01', '2024-01-01')"
            )
        conn.close()


# ===================================================================
# 2. Migration Runner
# ===================================================================

class TestMigrationRunner:

    def test_idempotent_migration(self, tmp_path: Path):
        """Running migrations repeatedly should be idempotent."""
        p = tmp_path / "idempotent.db"
        run_migrations(p)
        run_migrations(p)
        conn = get_connection(p)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 2

    def test_sequential_future_migration_execution(self, tmp_path: Path, monkeypatch):
        """Sequential runner executes newly added migrations in ascending order."""
        p = tmp_path / "future_mig.db"
        run_migrations(p)

        # Confirm v2
        conn = get_connection(p)
        assert get_user_version(conn) == 2
        conn.close()

        # Simulate adding a future migration v3
        migration_v3_ran = False

        def mock_migrate_v3(conn: sqlite3.Connection) -> None:
            nonlocal migration_v3_ran
            migration_v3_ran = True
            conn.execute("CREATE TABLE future_test (id INTEGER PRIMARY KEY);")

        extended_migrations = list(MIGRATIONS) + [(3, mock_migrate_v3)]
        monkeypatch.setattr("app.core.migrations.MIGRATIONS", extended_migrations)

        run_migrations(p)
        assert migration_v3_ran is True

        conn = get_connection(p)
        assert get_user_version(conn) == 3
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "future_test" in tables
        conn.close()

    def test_migration_from_v1_to_v2(self, tmp_path: Path):
        """Upgrading an existing v1 database to v2 drops logs_ai and initializes fts_index_state to MAX(id)."""
        p = tmp_path / "v1_to_v2.db"
        conn = get_connection(p)
        from app.core.migrations import migrate_v1, set_user_version
        migrate_v1(conn)
        set_user_version(conn, 1)

        # In v1, logs_ai inserts into logs_fts synchronously
        conn.execute(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw) "
            "VALUES ('2024-01-01T00:00:00', '2024-01-01T00:00:00', '10.0.0.1', 'srv1', 'app1', 'msg in v1', 'raw1')"
        )
        conn.commit()
        # Verify it was indexed by logs_ai
        fts_rows = conn.execute("SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'msg'").fetchall()
        assert len(fts_rows) == 1
        max_id = conn.execute("SELECT MAX(id) FROM logs").fetchone()[0]
        assert max_id >= 1
        conn.close()

        # Run migration runner to upgrade to v2
        run_migrations(p)

        conn = get_connection(p)
        assert get_user_version(conn) == 2
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
        assert "logs_ai" not in triggers
        assert "logs_ad" in triggers
        assert "logs_au" in triggers

        # Verify fts_index_state initialized to previous MAX(id)
        state_row = conn.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1").fetchone()
        assert state_row is not None
        assert state_row[0] == max_id

        # Verify previous FTS records are still searchable
        fts_after = conn.execute("SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'msg'").fetchall()
        assert len(fts_after) == 1
        conn.close()


# ===================================================================
# 3. FTS5 External Content Sync Triggers
# ===================================================================

class TestFTS5Sync:

    def _insert_log(self, conn: sqlite3.Connection, app: str, alias: str, msg: str, index: bool = True) -> int:
        cur = conn.execute(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw) "
            "VALUES ('2024-01-01T00:00:00', '2024-01-01T00:00:00', '10.0.0.1', ?, ?, ?, ?)",
            (alias, app, msg, f"{app}: {msg}"),
        )
        conn.commit()
        row_id = cur.lastrowid
        if index:
            from app.services.fts_indexer import index_pending_batch
            index_pending_batch(conn)
        return row_id

    def test_fts5_insert_sync(self, db_path: Path):
        """Inserting into logs and running FTS indexer makes the row searchable via logs_fts."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "nginx", "web-server", "connection refused by upstream", index=False)

        # Before indexing, row should not be in logs_fts
        rows_before = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'refused'"
        ).fetchall()
        assert len(rows_before) == 0

        # Perform indexing pass
        from app.services.fts_indexer import index_pending_batch
        indexed = index_pending_batch(conn)
        assert indexed == 1

        rows = conn.execute(
            "SELECT rowid, app_name, source_alias, message FROM logs_fts WHERE logs_fts MATCH 'refused'"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == row_id
        assert rows[0][1] == "nginx"
        assert rows[0][2] == "web-server"
        assert "connection refused" in rows[0][3]

    def test_fts5_delete_sync(self, db_path: Path):
        """Deleting an indexed row from logs should remove the row from logs_fts."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "sshd", "bastion", "failed password for root", index=True)

        # Verify it exists in FTS
        rows = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'bastion'"
        ).fetchall()
        assert len(rows) == 1

        # Delete from logs
        conn.execute("DELETE FROM logs WHERE id = ?", (row_id,))
        conn.commit()

        # Should no longer appear in FTS
        rows_after = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'bastion'"
        ).fetchall()
        conn.close()

        assert len(rows_after) == 0

    def test_fts5_update_sync(self, db_path: Path):
        """Updating an indexed log row should update its FTS index entry."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "app", "srv1", "initial message text", index=True)

        # Update message
        conn.execute(
            "UPDATE logs SET message = 'updated completely new' WHERE id = ?",
            (row_id,),
        )
        conn.commit()

        # Old text should not match
        old_match = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'initial'"
        ).fetchall()
        assert len(old_match) == 0

        # New text should match
        new_match = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'completely'"
        ).fetchall()
        conn.close()

        assert len(new_match) == 1
        assert new_match[0][0] == row_id

    def test_fts5_no_orphan_records(self, db_path: Path):
        """Inserting, indexing, and deleting multiple records should leave zero FTS entries."""
        conn = get_connection(db_path)
        ids = []
        for i in range(5):
            ids.append(self._insert_log(conn, f"app{i}", f"host{i}", f"test payload {i}", index=True))

        # Verify all are indexed
        count = conn.execute(
            "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'payload'"
        ).fetchone()[0]
        assert count == 5

        # Delete all
        for log_id in ids:
            conn.execute("DELETE FROM logs WHERE id = ?", (log_id,))
        conn.commit()

        count_after = conn.execute(
            "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'payload'"
        ).fetchone()[0]
        conn.close()

        assert count_after == 0

    def test_unindexed_row_deletion_does_not_crash_fts5(self, db_path: Path):
        """Deleting an unindexed log row must NOT trigger malformed disk image error."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "unindexed_app", "test_host", "some unindexed message", index=False)

        # Delete the unindexed row - this should NOT raise database disk image is malformed
        conn.execute("DELETE FROM logs WHERE id = ?", (row_id,))
        conn.commit()

        # Verify database and FTS virtual table remain fully intact and operational
        count = conn.execute("SELECT COUNT(*) FROM logs WHERE id = ?", (row_id,)).fetchone()[0]
        assert count == 0
        fts_check = conn.execute("SELECT COUNT(*) FROM logs_fts").fetchone()[0]
        assert fts_check >= 0
        conn.close()

    def test_unindexed_row_update_does_not_crash_and_indexes_accurately(self, db_path: Path):
        """Updating an unindexed log row should not crash, and subsequent indexing should index new values."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "orig_app", "orig_alias", "original pre-indexed message", index=False)

        # Update while still unindexed
        conn.execute(
            "UPDATE logs SET app_name = 'updated_app', message = 'updated postedit message' WHERE id = ?",
            (row_id,),
        )
        conn.commit()

        # Now index the row
        from app.services.fts_indexer import index_pending_batch
        indexed = index_pending_batch(conn)
        assert indexed == 1

        # Check FTS matches updated values
        rows = conn.execute(
            "SELECT rowid, app_name, message FROM logs_fts WHERE logs_fts MATCH 'postedit'"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == row_id
        assert rows[0][1] == "updated_app"
        assert "updated postedit" in rows[0][2]

    def test_startup_sanitization_clamps_future_timestamps(self, tmp_path: Path):
        """run_migrations should sanitize any future-dated timestamps without touching past logs."""
        db_file = tmp_path / "sanitize_test.db"
        run_migrations(db_file)

        future_ts = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat()
        now_rec = datetime.datetime.now(datetime.timezone.utc).isoformat()
        past_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        past_rec = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1, seconds=5)).isoformat()

        with get_connection(db_file) as conn:
            # Insert a corrupted row with timestamp in the future relative to now
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, '192.168.1.1', 'router', 'syslog', 1, 6, 'corrupted', 'raw')""",
                (future_ts, now_rec),
            )
            # Insert a historical row in the past (timestamp > received_at, but in past)
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, '192.168.1.1', 'router', 'syslog', 1, 6, 'historical_past', 'raw')""",
                (past_ts, past_rec),
            )
            conn.commit()

        # Rerun run_migrations as happens on container restart
        run_migrations(db_file)

        with get_connection(db_file) as conn:
            row = conn.execute("SELECT timestamp, received_at FROM logs WHERE message = 'corrupted'").fetchone()
            assert row[0] == row[1]

            row_past = conn.execute("SELECT timestamp, received_at FROM logs WHERE message = 'historical_past'").fetchone()
            # Historical row in the past should NOT have been updated by bounded startup query
            assert row_past[0] == past_ts

    def test_startup_sanitization_query_uses_indexed_scan(self, db_path: Path):
        """Startup clamping query must use idx_logs_time_sev to prevent full table scans."""
        conn = get_connection(db_path)
        cur = conn.cursor()
        plan_rows = cur.execute(
            "EXPLAIN QUERY PLAN UPDATE logs SET timestamp = received_at "
            "WHERE timestamp > strftime('%Y-%m-%dT%H:%M:%S', 'now', '+1 minute') AND timestamp > received_at;"
        ).fetchall()
        conn.close()

        plan_str = " ".join(str(r) for r in plan_rows)
        assert "idx_logs_time_sev" in plan_str
        assert "SCAN logs" not in plan_str

    def test_facet_extraction_query_uses_indexes(self, db_path: Path):
        """Facet extraction query must use indexes for recent logs and configured host alias joins."""
        conn = get_connection(db_path)
        cur = conn.cursor()
        facet_plan = cur.execute(
            """EXPLAIN QUERY PLAN
            SELECT DISTINCT source_alias, source_ip, app_name
            FROM logs
            WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')
              AND (source_alias != '' OR source_ip != '')
              AND app_name != ''
            UNION
            SELECT DISTINCT l.source_alias, l.source_ip, l.app_name
            FROM host_aliases h
            JOIN logs l ON (l.source_ip = h.ip OR l.source_alias = h.alias OR l.source_alias = h.ip)
            WHERE (l.source_alias != '' OR l.source_ip != '') AND l.app_name != ''
            """
        ).fetchall()
        conn.close()

        plan_str = " ".join(str(r) for r in facet_plan)
        assert "idx_logs_time_sev" in plan_str
        assert "SEARCH l USING INDEX" in plan_str
        assert "SCAN logs" not in plan_str
