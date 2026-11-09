# Specification: Database Schema & Storage Engine (SQLite + FTS5)

> Modular specification extracted from [docs/SPEC.md](../../docs/SPEC.md).

## 2. Database Schema & Storage Engine (SQLite + FTS5)
Database path: `/data/logs.db`. WAL mode enabled (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;`).
Schema migrations managed via [`backend/app/core/migrations.py`](../../backend/app/core/migrations.py) using `PRAGMA user_version`.

Before any migration step, `run_migrations` compares `user_version` with the newest version in `MIGRATIONS`. A higher value means a newer LogShed upgraded the database: the connection is closed without writing anything and `DatabaseNewerThanAppError` is raised, before `repair_v3` and before the encryption key is read or created. The web process logs the error (naming the database path, its schema and the newest one this version knows) and start-up fails, so the container exits; the `reset-admin` and `seed-logs` commands print it and exit with status 1. Going back after a schema upgrade is a restore of an older copy of `/data` (see "Going back to an older version" in [CONFIGURATION.md](../CONFIGURATION.md)).

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

   ```
   `source_pattern` and `app_pattern` each hold a comma-separated list of patterns (`*` and `?` wildcards; one pattern is a list of one), stored trimmed, without empty entries or repeats that differ only in letter case, joined with `", "`; `NULL` matches every host or app. The API's 255-character limit applies to the whole list.

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
   Since migration v3, names are unique ignoring case (`idx_saved_views_name_nocase`, see 2.3).

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
       maintenance_session_id TEXT,
       -- Added in migration 3: links a "Maintenance Window" row to maintenance_sessions.session_id
       sample_log_id INTEGER
       -- Added in migration 3: id of the log that triggered a rule alert, so the Console can open on it.
       -- Not a foreign key: the log may be pruned or deleted while the alert row stays.
   );

   CREATE INDEX IF NOT EXISTS idx_alert_history_triggered_at ON alert_history(triggered_at DESC);
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
     The digest also uses two settings that are not seeded: `daily_digest_last_run` (UTC ISO 8601, written with the `alert_history` row when a digest runs) and `daily_digest_armed_at` (UTC ISO 8601, `is_encrypted = 0`). `POST /api/settings` writes `daily_digest_armed_at` when `daily_digest_enabled` goes from off or missing to on, or when `daily_digest_schedule_time` changes (compared with the stored values in the same transaction). `DailyDigestWorker` sends no digest on a day whose schedule time is earlier than it. A missing or unparsable value means no restriction. No schema change.
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
- **Recurring Schedule Evaluation:** Schedules are stored in `system_settings` (`maintenance_schedules`) and their `start_time` is a wall-clock time in the server time zone, the IANA zone named by `TZ` (or `/etc/localtime` when `TZ` is unset; an unknown `TZ`, or a POSIX rule string such as `CET-1CEST`, falls back to the current fixed UTC offset, which is wrong across a clock change). Each start is placed on its own date with the offset in force then, so `next_run` after a daylight saving change carries the new offset (Sunday 02:00 on 25 Oct 2026 in `Europe/London` is `2026-10-25T02:00:00+00:00`). A window lasts `duration_minutes` of real time, so a 90-minute window that spans a clock change still ends 90 minutes after it starts. A start time inside the hour skipped by a spring-forward change is moved forward by the length of the gap (01:30 becomes 02:30), and a start time inside the hour repeated by an autumn change starts at its first occurrence.
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
- **Unique Saved View Names:** `dedupe_saved_view_names` runs at the end of `migrate_v3` and again in `repair_v3`, inside their transaction. It trims every `saved_views.name` (an empty name becomes `Untitled view`), then groups names with the `COLLATE NOCASE` rule (ASCII letters only). In each group the lowest `id` keeps its name and each later view, in `id` order, becomes `<its name> (2)`, `<its name> (3)` and so on, taking the next number that does not match any other name ignoring case; the base name is shortened when needed so the result stays within 100 characters. It logs `Renamed N saved views whose names were duplicates` at INFO when it changed anything, and nothing otherwise. It then creates the index that keeps names unique:
- **Retired OpenAI Default Model:** `migrate_v3` deletes `system_settings` row `ai_model_openai` when it holds `gpt-4o` (the 1.2.x default, which saving the settings page stored even when nobody chose it; OpenAI shuts it down on 2027-01-14), and `ai_model` when it holds `gpt-4o` while `ai_provider` is `openai`, and logs it, so OpenAI uses the current default model. It runs once, with the upgrade, and not in `repair_v3`, so a `gpt-4o` chosen again later is kept.
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_views_name_nocase ON saved_views(name COLLATE NOCASE);
  ```
  A second run changes nothing. The API applies the same rule (see the saved views routes in [api.md](api.md)).
- **Atomic Migrations:** Each migration step and its `user_version` bump run in one `BEGIN IMMEDIATE` transaction, so a crash during an upgrade rolls back instead of leaving rules without an order or a database that cannot start.
- **Removed Redundant Indexes:** `idx_api_tokens_hash` (the `UNIQUE` constraint on `token_hash` already indexes it), `idx_alert_history_rule_id` (covered by `idx_alert_history_rule_time`), `idx_drop_rules_enabled` and `idx_alert_rules_enabled` (covered by the `idx_*_rules_order` indexes) are no longer created and are dropped from existing databases.
- **Docker Endpoints Table:** `docker_endpoints` holds `id`, `name` (unique), `url`, `source_alias`, `enabled`, `tls_enabled`, `ca_cert`, `client_cert`, `client_key` (encrypted), `exclude_containers`, `created_at`, `updated_at` and `verify_hostname INTEGER NOT NULL DEFAULT 1`. `verify_hostname` makes the TLS handshake check the daemon certificate against the host name; existing rows get `1`.
- **Former Docker Aliases:** `system_settings` key `former_docker_aliases` holds a JSON list of the aliases of deleted Docker hosts and the old aliases of renamed ones. A log row has no column naming the collector that stored it, so a row counts as Docker output when `source_ip` is `docker` or its `source_alias` is a configured Docker host alias or one of these (`load_docker_source_aliases` and `DOCKER_ALIASES_SQL` in `backend/app/core/utils.py`, used by the host filters, facets, scoped deletes, syslog alias saves and the collector's app name updates). Without it, a deleted host's container logs would read as syslog logs from the same address and a syslog alias save would relabel them. Creating a Docker host, or renaming one, with an alias on the list takes it off. Items that are not text are ignored.
- **Alias Renames:** When a syslog host alias (`POST /api/aliases` on an IP that already had an alias) or a Docker host alias (`PUT /api/docker/hosts/{id}`) changes from one name to another, or a syslog host alias is deleted (`DELETE /api/aliases/{ip}`, where the new name is the IP, as the host's logs revert to it), the same transaction replaces the old alias with the new one in each `drop_rules.source_pattern` list (the entry that equals it, letter case and surrounding spaces ignored, as the matching does; duplicates removed, any case), sets the `target_host` of active `maintenance_sessions` (`is_active = 1`) to the new alias where it equals the old one, and replaces the old alias in each saved view's `sources` list and comma-separated `source` field (`query_params` JSON; duplicates removed; rows that fail to parse are left as they are). Patterns with `*` or `?`, ended sessions and alert rules (which have no host filter) are not changed, and nothing is changed while another `host_aliases` row or Docker host still has the old alias. Giving a host its first alias changes nothing. Afterwards the drop filter and the maintenance cache reload, so the next log is matched against the new name, and one info line gives the counts (`backend/app/services/alias_rename.py`). Deleting a Docker host changes nothing, because no new logs arrive under its alias.
- **Startup Repair (`repair_v3`):** Runs on every start and is idempotent. It re-runs `migrate_v3` when `docker_endpoints` or `ai_conversation_messages` is missing (a version 3 database left incomplete by an interrupted upgrade), adds `docker_endpoints.verify_hostname` and `alert_history.sample_log_id` when absent, adds missing `display_order` columns, numbers rules by id when a table holds more than one rule and none was ever ordered, creates the `idx_*_rules_order` indexes, drops the redundant indexes above, and makes saved view names unique (`dedupe_saved_view_names`, above).
- **System Identity Setup:**
  - `server_name` defaults to `'LogShed'` in `system_settings` table.
  - `instance_id` set to persistent UUID4 if not already present.

### 2.4 Asynchronous FTS5 Indexing Model & Worker Architecture
- **Supervised Background Task:** `FTSIndexWorker` ([`backend/app/services/fts_indexer.py`](../../backend/app/services/fts_indexer.py)) runs as a supervised task under `_supervise_worker` in [`backend/app/main.py`](../../backend/app/main.py). Worker failures are isolated, logged, and restarted with exponential backoff.
- **Decoupled Ingestion Pipeline:** Ingestion tasks (`QueueConsumer`) commit raw log rows directly to the `logs` table without waiting for FTS indexing.
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
- **Graceful Shutdown Sequencing:** Lifespan shutdown stops log collectors (`SyslogServer`, `DockerTailer`), flushes the multiline assembler, stops `QueueConsumer` (drains queue to DB), then stops `FTSIndexWorker` (flushes pending unindexed logs to `logs_fts`), followed by `PruneWorker`, `StorageMetricsWorker`, `AlertEvaluator`, `DailyDigestWorker`, notification executor, and flushes drop filter counts.

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
- **Retiring Connections (`reset_all_db_connections`):** Connections are opened with `check_same_thread=True`, so only their own thread can close them. Each cached entry records the connection generation it was opened in; `reset_all_db_connections()` (called before Compact Database runs `VACUUM`) increments the generation and closes the calling thread's connections at once. Every other thread closes its stale connection and opens a fresh one the next time it calls `get_thread_read_connection`, so no query after a compact runs on a connection from before it. An idle thread keeps its old connection open until its next use.
- **Connection Teardown Hook:** `close_thread_local_connections()` closes all cached connections on the calling thread and clears the dictionary, ensuring clean test fixture teardown and process exit.

### 2.6 Retention Pruning
Daily task runs iterative batch pruning coordinated with the FTS indexing watermark to prevent WAL expansion, lock contention, and orphaned FTS rows:
1. Drain unindexed logs via `index_pending_logs(db_path)` to ensure all historical records are searchable prior to purge.
2. Loop batch deletions until no matching rows remain, guarded by `last_indexed_id`:
   `DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE timestamp < datetime('now', '-' || :retention_days || ' days') AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1) LIMIT 5000);`
   After each chunk commits, the prune waits (at most 1 s) while a `QueueConsumer` batch is waiting for the write lock or being stored (`wait_for_ingest_write` in `backend/app/core/pipeline.py`), so ingestion writes between chunks. Without the wait, the next chunk usually took the lock again before the consumer's busy handler retried, and a large prune left the consumer waiting for seconds while the ingest queue filled.
3. Prune alert history and AI audit records older than the cutoff (all history follows the global `retention_days`):
   - Non-maintenance `alert_history` rows (alert firings, Daily Digest, On-Demand Analysis) are deleted when `triggered_at` is older than the cutoff.
   - Maintenance window rows are deleted when their session has ended and `ended_at` is older than the cutoff (a maintenance row whose session no longer exists falls back to `triggered_at`); running sessions are never deleted. Ended sessions older than the cutoff are then deleted too.
   - The `ai_audit_log` row linked by `ai_audit_id` is deleted with its history row unless a kept history row still references it. Unreferenced `ai_audit_log` rows older than the cutoff are deleted as well. Follow-up conversation threads in `ai_conversation_messages` are removed with their `ai_audit_log` row by cascade.
   - The prune result reports `deleted_logs` (logs only), `deleted_history` and `deleted_ai_audit` separately.
4. Compact the FTS5 index in short merge steps:
   `INSERT INTO logs_fts(logs_fts, rank) VALUES('merge', N);` (`merge_fts_index` in `backend/app/services/fts_indexer.py`). Each step writes 500 pages and commits on its own, with at most 200 steps per run; the first step (a negative page count) starts a full merge and later steps continue it, and an unfinished merge carries on at the next run. Between steps it waits for a pending ingest batch as in step 2. A single `optimize` is not used because it held the write lock until the whole index was rewritten, long enough to make ingestion inserts wait or fail.
5. Checkpoint and truncate the WAL:
   `PRAGMA wal_checkpoint(TRUNCATE);`

Saving a lower retention does not prune by itself. Deletion happens at the next `PruneWorker` run (at startup, then 24 hours after each run) or when the user chooses to purge immediately. `GET /api/maintenance/prune/preview` returns the counts a prune would delete using the same predicates (including the `last_indexed_id` guard), so the UI can confirm before anything is removed. The worker publishes its next scheduled run time in memory for the preview.

Deleting logs (`POST /api/logs/delete`, [`backend/app/services/log_deletion.py`](../../backend/app/services/log_deletion.py)) uses the same 5,000-row chunks (500 for a list of IDs) and waits for a pending ingest batch between them. It first indexes pending logs, so the `logs_ad` trigger removes each deleted row from `logs_fts`, and runs the merge steps above after deleting 5,000 rows or more.

### 2.7 Storage Metrics & Disk Monitoring
- **Sampling Strategy:** Background worker samples metrics hourly and immediately following any manual/automated prune event.
  - Compute total database disk footprint via `os.path.getsize()` across `/data/logs.db`, `/data/logs.db-wal`, and `/data/logs.db-shm`.
  - Read host mount capacity and free space using `shutil.disk_usage("/data")`.
  - Count logs (`count_logs` in `services/storage_metrics.py`): the count and the highest id are kept between snapshots, so a snapshot counts only the rows added since (a primary key range) instead of every row (about 1 s at 50 million logs). Pruning and log deletion call `note_logs_deleted()` before and after deleting, so the next snapshot counts every row again.
  - Record snapshot into `storage_metrics` table.
- **Metrics Retention:** Prune records from `storage_metrics` older than 30 days during the daily retention cleanup cycle.
