# Specification: Database Schema & Storage Engine (SQLite + FTS5)

> Modular specification extracted from [docs/SPEC.md](../../docs/SPEC.md).

## 2. Database Schema & Storage Engine (SQLite + FTS5)
Database path: `/data/logs.db`. WAL mode enabled (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;`).
Schema migrations managed via [`backend/app/core/migrations.py`](../../backend/app/core/migrations.py) using `PRAGMA user_version`.

### 2.1 Core DDL (Migration v1)
```sql
PRAGMA user_version = 1;

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

-- Relational Indexes for Structured Query Filtering & Loose Index Skip-Scans
CREATE INDEX idx_logs_time_sev ON logs(timestamp DESC, severity);
CREATE INDEX idx_logs_app_time ON logs(app_name, timestamp DESC);
CREATE INDEX idx_logs_src_time ON logs(source_alias, timestamp DESC);
CREATE INDEX idx_logs_source_ip ON logs(source_ip);
CREATE INDEX idx_logs_source_app_ip ON logs(source_alias, app_name, source_ip);

-- Full-Text Search Virtual Table (External Content Table)
CREATE VIRTUAL TABLE logs_fts USING fts5(
    app_name,
    source_alias,
    message,
    content='logs',
    content_rowid='id'
);

-- FTS5 Sync Triggers (Explicit External Content Deletion Pattern)
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
    recorded_at DATETIME NOT NULL,       -- not unique: hourly + manual-prune samples can land in the same second
    db_size_bytes INTEGER NOT NULL,      -- logs.db + logs.db-wal + logs.db-shm
    disk_free_bytes INTEGER NOT NULL,    -- Free space on /data mount
    disk_total_bytes INTEGER NOT NULL,   -- Total capacity of /data mount
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
```

### 2.2 Schema Migration v2 (LogShed v1.2.0 Upgrade)
Migration v2 constitutes the complete schema upgrade from LogShed v1.1.0 to v1.2.0, advancing the schema version to `PRAGMA user_version = 2;`:

1. **Decoupled Asynchronous FTS5 Indexing:**
   - Drops the synchronous `logs_ai` AFTER INSERT trigger to decouple raw ingestion from FTS token extraction and indexing:
     ```sql
     DROP TRIGGER IF EXISTS logs_ai;
     ```
     This eliminates indexing overhead from the raw ingestion transaction path, boosting write throughput and preventing ingestion stalls during burst traffic.
   - Creates `fts_index_state` table tracking the maximum log ID indexed into `logs_fts`:
     ```sql
     CREATE TABLE IF NOT EXISTS fts_index_state (
         id INTEGER PRIMARY KEY CHECK (id = 1),
         last_indexed_id INTEGER NOT NULL DEFAULT 0,
         updated_at DATETIME NOT NULL
     );

     INSERT OR IGNORE INTO fts_index_state (id, last_indexed_id, updated_at)
     VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM logs), datetime('now'));
     ```
     For upgraded databases, `last_indexed_id` is set to the current `MAX(id)` from `logs`, ensuring historical logs already indexed under v1 are not re-indexed.
   - Replaces unconditional deletion and update triggers with conditional triggers guarded by `last_indexed_id`:
     ```sql
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
         VALUES(new.id, new.app_name, new.source_alias, new.message);
     END;
     ```
     These trigger conditions guarantee that deleting or updating unindexed logs never invokes FTS deletion instructions for non-existent FTS records, preventing SQLite disk image corruption errors.

2. **Ingestion Drop Rules (`drop_rules`):**
   Stores user-defined drop filters applied during log ingestion to discard matching noisy lines prior to database insertion:
   ```sql
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
   ```

3. **Saved Views (`saved_views`):**
   Persists custom query parameters and search filters with optional pinning:
   ```sql
   CREATE TABLE IF NOT EXISTS saved_views (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       name TEXT NOT NULL,
       query_params TEXT NOT NULL,
       is_pinned BOOLEAN NOT NULL DEFAULT 0,
       created_at DATETIME NOT NULL
   );

   CREATE INDEX IF NOT EXISTS idx_saved_views_pinned ON saved_views(is_pinned, name);
   ```

4. **Notification Channels (`notification_channels`):**
   Stores Apprise notification targets for dispatching alert notifications and analytical rollups:
   ```sql
   CREATE TABLE IF NOT EXISTS notification_channels (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       name TEXT NOT NULL,
       url TEXT NOT NULL,
       is_enabled BOOLEAN NOT NULL DEFAULT 1,
       created_at DATETIME NOT NULL,
       updated_at DATETIME NOT NULL
   );

   CREATE INDEX IF NOT EXISTS idx_notification_channels_enabled ON notification_channels(is_enabled);
   ```

5. **Alert Rules (`alert_rules`):**
   Stores alert definitions, matching criteria, sliding-window thresholds, cooldown dampening, and automated AI enrichment settings:
   ```sql
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
   ```

6. **Alert Incident History (`alert_history`):**
   Records fired alert incidents, sample logs, AI root-cause summaries, and references to the AI audit log:
   ```sql
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
       triggered_at DATETIME NOT NULL,
       maintenance_session_id TEXT
       -- Added in migration 3: links a "Maintenance Window" row to maintenance_sessions.session_id
   );

   CREATE INDEX IF NOT EXISTS idx_alert_history_triggered_at ON alert_history(triggered_at DESC);
   CREATE INDEX IF NOT EXISTS idx_alert_history_rule_id ON alert_history(rule_id);
   CREATE INDEX IF NOT EXISTS idx_alert_history_rule_time ON alert_history(rule_id, triggered_at DESC);
   CREATE INDEX IF NOT EXISTS idx_alert_history_rule_name ON alert_history(rule_name, triggered_at DESC);
   CREATE INDEX IF NOT EXISTS idx_alert_history_ai_audit_id ON alert_history(ai_audit_id);
   CREATE INDEX IF NOT EXISTS idx_alert_history_maint_session ON alert_history(maintenance_session_id);
   ```

7. **Schema Additions, Default Settings & Data Migration:**
   - Adds `trigger_source TEXT NOT NULL DEFAULT 'on-demand'` column to `ai_audit_log`.
   - Creates composite index `CREATE INDEX IF NOT EXISTS idx_logs_source_app_ip ON logs(source_alias, app_name, source_ip);`.
   - Inserts default settings for daily digest and retention:
     ```sql
     INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
     VALUES ('daily_digest_enabled', '0', datetime('now'), 0);
     INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
     VALUES ('daily_digest_schedule_time', '09:00', datetime('now'), 0);
     INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
     VALUES ('retention_days', '14', datetime('now'), 0);
     ```
   - Clamps future-dated timestamps defensively to avoid indexing skew:
     ```sql
     UPDATE logs SET timestamp = received_at
     WHERE timestamp > strftime('%Y-%m-%dT%H:%M:%S', 'now', '+1 minute') AND timestamp > received_at;
     ```
   - Backfills historical on-demand `ai_audit_log` records into `alert_history`.

### 2.3 Schema Migration v3 (Rule Ordering, API Tokens & Multi-Session Maintenance Tracking)
Migration v3 constitutes the complete schema upgrade from LogShed v1.2.0 to v1.3.0, advancing the schema version to `PRAGMA user_version = 3;`:
- **Version Stamp:** `PRAGMA user_version = 3;`.
- **Display Order Columns:** Adds `display_order INTEGER NOT NULL DEFAULT 0` to both `drop_rules` and `alert_rules`.
- **Backfill Existing Rows:** Sets `display_order = id` for existing records in both tables where `display_order = 0`.
- **Composite Indexes:**
  ```sql
  CREATE INDEX IF NOT EXISTS idx_drop_rules_order ON drop_rules(is_enabled, display_order ASC, id ASC);
  CREATE INDEX IF NOT EXISTS idx_alert_rules_order ON alert_rules(is_enabled, display_order ASC, id ASC);
  ```
- **API Tokens Table:**
  ```sql
  CREATE TABLE IF NOT EXISTS api_tokens (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,
      token_prefix TEXT NOT NULL,
      scopes TEXT NOT NULL DEFAULT '["maintenance:write"]',
      created_at DATETIME NOT NULL,
      expires_at DATETIME,
      last_used_at DATETIME,
      created_by TEXT DEFAULT 'admin'
  );
  CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);
  CREATE INDEX IF NOT EXISTS idx_api_tokens_expires ON api_tokens(expires_at);
  ```
- **Maintenance Sessions Table (Multi-Session Tracking):**
  ```sql
  CREATE TABLE IF NOT EXISTS maintenance_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT NOT NULL UNIQUE,
      token_id INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
      initiated_by TEXT NOT NULL,
      reason TEXT,
      log_handling TEXT NOT NULL DEFAULT 'silence_alerts',
      target_app TEXT,
      target_host TEXT,
      created_at DATETIME NOT NULL,
      expires_at DATETIME NOT NULL,
      is_active INTEGER NOT NULL DEFAULT 1,
      ended_at DATETIME,          -- actual end: expires_at on expiry, termination time if ended early
      end_reason TEXT,            -- 'expired' | 'terminated' (NULL while running)
      dropped_count INTEGER NOT NULL DEFAULT 0,
      trigger_label TEXT          -- 'UI', 'MCP', or 'API - <token name>' (snapshot, survives token deletion)
  );
  CREATE INDEX IF NOT EXISTS idx_maint_sessions_active ON maintenance_sessions(is_active, expires_at);
  ```
- **Maintenance Audit History:** Each on-demand session (web UI, API token, MCP) writes one `alert_history` row (`rule_name = 'Maintenance Window'`, `maintenance_session_id` set, `triggered_at` = session start) when it starts. Start, end (or early-termination time), trigger, type, targets, and dropped-log count are read from the linked `maintenance_sessions` row at query time, so clearing alert history never removes the session audit data. Recurring schedules are not recorded. Sessions that pass their planned end are closed as `expired` by the 30-second `MaintenanceSweepWorker` (or lazily on status and history reads); early termination records `terminated`. Per-session `dropped_count` is accumulated in memory (no SQLite access per log line) and flushed during the sweep and at session end. Ended sessions older than the configured log retention, and their history rows, are pruned by the daily prune (see 2.6); a running session is never pruned. At most 20 sessions can be active at once, and `target_app` and `target_host` are limited to 255 characters.
- **AI Follow-up Conversation Messages Table:**
  ```sql
  CREATE TABLE IF NOT EXISTS ai_conversation_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      audit_id INTEGER NOT NULL REFERENCES ai_audit_log(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
      content TEXT NOT NULL,           -- user rows hold the redacted question as sent to the provider
      tokens_in INTEGER NOT NULL DEFAULT 0,
      tokens_out INTEGER NOT NULL DEFAULT 0,
      tokens_thoughts INTEGER NOT NULL DEFAULT 0,
      tokens_used INTEGER NOT NULL DEFAULT 0,
      model TEXT NOT NULL,             -- model that produced the reply (after any failover)
      created_at DATETIME NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_ai_conversation_audit_id ON ai_conversation_messages(audit_id, created_at ASC);
  ```
  Each follow-up question and its reply are written in one `BEGIN IMMEDIATE` transaction that re-checks the 10-question limit, so concurrent requests cannot exceed it. Deleting an `ai_audit_log` row (single history delete, clear all history, or retention pruning) removes its thread through `ON DELETE CASCADE` (every connection enables `PRAGMA foreign_keys=ON`).
- **Pre-release v3 Databases:** On startup, a database already at `user_version = 3` that is missing `docker_endpoints` or `ai_conversation_messages` (from an earlier v1.3.0 pre-release) re-runs the idempotent `migrate_v3` to add them.
- **System Identity Setup:**
  - `server_name` defaults to `'LogShed'` in `system_settings` table.
  - `instance_id` set to persistent UUID4 if not already present.

### 2.4 Asynchronous FTS5 Indexing Model & Worker Architecture
- **Supervised Background Task:** `FTSIndexWorker` ([`backend/app/services/fts_indexer.py`](../../backend/app/services/fts_indexer.py)) runs as a supervised task under `_supervise_worker` in [`backend/app/main.py`](../../backend/app/main.py). Worker failures are isolated, logged, and restarted with exponential backoff.
- **Decoupled Ingestion Pipeline:** Ingestion tasks (`QueueConsumer`) commit raw log rows directly to the `logs` table without waiting for FTS tokenization.
- **Catch-Up Latency Target:** Catch-up latency target is <= 1000ms (empirically measured < 250ms).
- **Single-Statement Bulk Inserts with RETURNING rowid:**
  ```sql
  INSERT INTO logs_fts(rowid, app_name, source_alias, message)
  SELECT id, app_name, source_alias, message FROM logs
  WHERE id > ? ORDER BY id ASC LIMIT ?
  RETURNING rowid;
  ```
  Using `RETURNING rowid` allows the worker to atomically determine the highest rowid indexed in the batch and update `fts_index_state.last_indexed_id` within the same transaction.
- **Reactive Wake-Up & Fallback Polling:** `QueueConsumer` triggers `fts_indexer.notify_new_logs()` immediately upon committing a batch of logs to the database, waking up `FTSIndexWorker` via an internal `asyncio.Event` (`_wake_event`). A periodic polling interval (default 0.5s) serves as fallback in case of missed notifications, guaranteeing the <= 1000ms catch-up latency SLA.
- **Crash Resilience & State Tracking:** `fts_index_state.last_indexed_id` is committed atomically with each FTS insert batch. On worker crash or restart, indexing resumes from `last_indexed_id + 1`, eliminating duplicate or missing FTS rows.
- **Search Consistency Middleware:** When incoming search queries arrive at `/api/logs?query=...`, the middleware signals the supervised `FTSIndexWorker` via non-blocking event notification (`worker.notify_new_logs()`) to accelerate catch-up without stalling query execution threads. This ensures pending logs are indexed promptly while avoiding query latency bottlenecks under ingestion load.
- **Retention Coordination:** Retention pruning ([`backend/app/services/retention.py`](../../backend/app/services/retention.py)) coordinates with `fts_index_state.last_indexed_id`. During retention pruning (`execute_prune`), pending unindexed logs are indexed first (`index_pending_logs`), and deletions enforce `AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1)` to guarantee no unindexed log records are ever purged without being indexed, preventing orphaned FTS records or unindexed log deletion.
- **Graceful Shutdown Sequencing:** Lifespan shutdown stops log collectors (`SyslogServer`, `DockerTailer`), flushes the multiline assembler, stops `QueueConsumer` (drains queue to DB), then stops `FTSIndexWorker` (flushes pending unindexed logs to `logs_fts`), followed by `PruneWorker`, `StorageMetricsWorker`, `AlertEvaluator`, `DailyDigestWorker`, notification executor, regex executor, and flushes drop filter counts.

### 2.5 Thread-Local Connection Model & Concurrency
- **Thread-Local Read Connection Reuse:** In [`backend/app/api/deps.py`](../../backend/app/api/deps.py), synchronous database queries are dispatched to worker threads via `run_db_query(fn, custom_db_path)` using `asyncio.to_thread()`. `get_thread_read_connection(db_path: Path) -> sqlite3.Connection` maintains a thread-local dictionary `_thread_local.connections` keyed by resolved database path, reusing connections across sequential queries on worker threads.
- **One-Time Pragma Execution:** Pragmas and row factory are configured only once per newly opened thread connection:
  `PRAGMA journal_mode=WAL;`
  `PRAGMA synchronous=NORMAL;`
  `PRAGMA busy_timeout=5000;`
  `PRAGMA foreign_keys=ON;`
  `conn.row_factory = sqlite3.Row;`
- **Defensive Transaction Rollback Guard:** To prevent uncommitted transactions or dangling locks from leaking across thread pool invocations, `run_db_query` wraps query execution with defensive cleanup:
  ```python
  try:
      with conn:
          return fn(conn)
  finally:
      if conn.in_transaction:
          conn.rollback()
  ```
  This rollback guard prevents shared read locks on the WAL index (`-shm`) from blocking write checkpoints, allowing `PRAGMA wal_checkpoint(TRUNCATE)` to complete without contention.
- **Connection Teardown Hook:** `close_thread_local_connections()` closes all cached connections on the calling thread and clears the dictionary, ensuring clean test fixture teardown and process exit.

### 2.6 Retention Pruning
Daily task runs iterative batch pruning coordinated with the FTS indexing watermark to prevent WAL expansion, lock contention, and orphaned FTS rows:
1. Drain unindexed logs via `index_pending_logs(db_path)` to ensure all historical records are searchable prior to purge.
2. Loop batch deletions until no matching rows remain, guarded by `last_indexed_id`:
   `DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE timestamp < datetime('now', '-' || :retention_days || ' days') AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1) LIMIT 5000);`
3. Prune alert history and AI audit records older than the cutoff (all history follows the global `retention_days`):
   - Non-maintenance `alert_history` rows (alert firings, Daily Digest, On-Demand Analysis) are deleted when `triggered_at` is older than the cutoff.
   - Maintenance window rows are deleted when their session has ended and `ended_at` is older than the cutoff (a maintenance row whose session no longer exists falls back to `triggered_at`); running sessions are never deleted. Ended sessions older than the cutoff are then deleted too.
   - The `ai_audit_log` row linked by `ai_audit_id` is deleted with its history row unless a kept history row still references it. Unreferenced `ai_audit_log` rows older than the cutoff are deleted as well. Follow-up conversation threads in `ai_conversation_messages` are removed with their `ai_audit_log` row by cascade.
   - The prune result reports `deleted_logs` (logs only), `deleted_history` and `deleted_ai_audit` separately.
4. Execute FTS5 index compaction:
   `INSERT INTO logs_fts(logs_fts) VALUES('optimize');`
5. Checkpoint and truncate the WAL:
   `PRAGMA wal_checkpoint(TRUNCATE);`

Saving a lower retention does not prune by itself. Deletion happens at the next `PruneWorker` run (at startup, then 24 hours after each run) or when the user chooses to purge immediately. `GET /api/maintenance/prune/preview` returns the counts a prune would delete using the same predicates (including the `last_indexed_id` guard), so the UI can confirm before anything is removed. The worker publishes its next scheduled run time in memory for the preview.

### 2.7 Storage Metrics & Disk Monitoring
- **Sampling Strategy:** Background worker samples metrics hourly and immediately following any manual/automated prune event.
  - Compute total database disk footprint via `os.path.getsize()` across `/data/logs.db`, `/data/logs.db-wal`, and `/data/logs.db-shm`.
  - Read host mount capacity and free space using `shutil.disk_usage("/data")`.
  - Record snapshot into `storage_metrics` table.
- **Metrics Retention:** Prune records from `storage_metrics` older than 30 days during the daily retention cleanup cycle.
