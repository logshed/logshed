# Technical Specification: LogShed

## 1. System Architecture & Process Model
Single Docker container running Python 3.12 (`asyncio`) + FastAPI backend serving a pre-built React SPA, with background ingestion workers managed under isolated supervisors.

- **Process Boundaries:** Main thread runs `uvicorn` and supervised background tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`, `DropFilterWorker`, and `DailyDigestWorker`). SQLite operations must use stdlib `sqlite3` and offload synchronous queries via `asyncio.to_thread()` (per AGENTS.md, `aiosqlite` is not used). Thread-local read connection reuse (`get_thread_read_connection` in `backend/app/api/deps.py`) eliminates connection setup overhead across queries in worker threads with defensive rollback guards in `finally` blocks preventing WAL lock leaks.
- **Supervised Background Workers:** The application maintains 9 supervised background tasks isolated by supervisor wrappers (`_supervise_worker`) that catch exceptions, log diagnostics, and restart failing tasks with exponential backoff without disrupting the event loop:
  1. `SyslogServer`: Asynchronous UDP and TCP syslog listener on port 1514 (supporting RFC 3164, RFC 5424, and RFC 6587 framing).
  2. `DockerTailer`: Streams logs from container stdout/stderr sockets via local Unix domain socket or remote Docker HTTP socket proxy.
  3. `QueueConsumer`: Drains the in-memory buffer queue, applies active drop rules, evaluates alert rules, persists batch chunks to SQLite, broadcasts live SSE events, and signals the FTS indexing worker.
  4. `FTSIndexWorker`: Asynchronously batches unindexed log entries into the `logs_fts` external content table with durable state tracking.
  5. `PruneWorker`: Runs automated daily retention purging coordinated with the FTS watermark, followed by index compaction and WAL truncation.
  6. `StorageMetricsWorker`: Samples database file footprint and host mount disk metrics hourly and immediately following pruning operations.
  7. `ModelRefreshWorker`: Discovers and caches available AI models from the configured provider every 12 hours.
  8. `DropFilterWorker`: Flushes in-memory drop rule match counts to SQLite every 30 seconds to prevent database write contention.
  9. `DailyDigestWorker`: Compiles and delivers scheduled 24-hour analytical rollup digest notifications across configured channels.
- **Failure Isolation:** Worker exceptions must be caught, logged, and restarted with exponential backoff without crashing the event loop.
- **Security & Privileges:** Container starts as root to allow `entrypoint.sh` to configure permissions. It reads `PUID` and `PGID` environment variables (defaulting to 1000:1000), maps the `appuser` to match, dynamically detects `/var/run/docker.sock` GID and adds the user to that group, changes ownership of `/data`, and drops privileges via `gosu appuser`.
- **Docker Endpoint:** Connects via `DOCKER_HOST` environment variable (`unix:///var/run/docker.sock` or `tcp://proxy:2375` for `tecnativa/docker-socket-proxy`).

### 1.1 Configuration Architecture: Bootstrap Primitives vs. Runtime Settings
LogShed separates configuration into **bootstrap primitives** (required before the SQLite database unlocks or privilege reduction occurs) and **runtime settings** (configurable through the Web UI and persisted in `system_settings`).

#### Bootstrap Primitives (Container Environment Only)
The following environment variables are supplied at container start and are strictly container-level primitives:
- `PUID` and `PGID` - User and group IDs for the application to run as via `gosu` privilege reduction (defaults to `1000:1000`).
- `TZ` - Container runtime timezone (defaults to `UTC`).
- `PORT` - Web/API HTTP port (defaults to `8080`).
- `SYSLOG_PORT` - Syslog listening port for UDP and TCP (defaults to `1514`).
- `DATA_DIR` - Mount path for persistent SQLite database, master key, and presets (defaults to `/data`).
- `LOGSHED_SECRET_KEY` - Optional 32-byte URL-safe base64 key for encrypting runtime settings at rest; if unset, auto-generated at `/data/.secret_key`.
- `DOCKER_HOST` - Docker endpoint socket or proxy address (defaults to `unix:///var/run/docker.sock`).
- `MAX_RETENTION_DAYS` - Hard ceiling in days for the log retention slider in the UI (defaults to `30`, minimum `1`).
- `ENVIRONMENT` / `DEBUG` - Development origin and debug mode toggle (`production` / `false`).
- `CORS_ORIGINS` - Comma-separated list of allowed cross-origin hosts (defaults to empty in production).

*Storage Compatibility Note:* `DB_PATH` and `SECRET_KEY_PATH` remain supported as backward-compatible path overrides.

#### Three-Tier Resolution Hierarchy
Runtime operational settings follow a resilient three-tier resolution hierarchy:
1. **Tier 1 (Highest Priority):** Value persisted in SQLite (`system_settings` table) via Web UI.
2. **Tier 2 (Fallback):** Environment variable set on container (including silent legacy aliases).
3. **Tier 3 (Default):** Built-in hardcoded constant.

Settings updates apply dynamically to in-memory workers (`SyslogServer`, `DockerTailer`, `AlertEvaluator`, `NotifierService`, and `Auth`) without requiring container recreation. An in-memory thread-safe cache (`get_cached_system_settings` with 10-second TTL fallback) ensures high-throughput request evaluation without redundant SQLite I/O.

The 12 runtime advanced settings are:
- `ai_timeout`: Outbound AI request timeout in seconds (Env: `LOGSHED_AI_TIMEOUT`, default: `45.0`).
- `ai_thinking_budget`: Reasoning token budget for extended thinking models (Env: `LOGSHED_AI_THINKING_BUDGET`, default: `1024`, 0 disables).
- `app_url`: Public base instance URL for action links in push notifications (Env: `APP_URL`, legacy alias: `app_url`, default: `""`).
- `allow_private_notification_targets`: Permit notification webhooks to target LAN/RFC1918 IPs and `.local`/`.internal`/`.lan` hosts (Env: `ALLOW_PRIVATE_NOTIFICATION_TARGETS`, default: `True`).
- `enable_docker`: Toggle Docker log collector (Env: `ENABLE_DOCKER`, default: `True`).
- `docker_exclude_containers`: Comma-separated container names/IDs to ignore (Env: `DOCKER_EXCLUDE_CONTAINERS`, default: `""`).
- `docker_source_alias`: Source attribution alias for Docker logs (Env: `DOCKER_SOURCE_ALIAS`, default: `"docker"`).
- `trusted_proxies`: Comma-separated proxy IPs or CIDR blocks for client IP resolution (Env: `TRUSTED_PROXIES`, default: `""`).
- `trust_docker_proxies`: Automatically trust standard Docker bridge networks `172.16.0.0/12` (Env: `TRUST_DOCKER_PROXIES`, legacy aliases: `TRUST_DOCKER_NETWORKS`, `TRUST_DOCKER_GATEWAY`, default: `False`).
- `cookie_secure`: Force `Secure` flag on session cookies behind SSL proxies stripping `X-Forwarded-Proto` (Env: `COOKIE_SECURE`, default: `False`).
- `syslog_max_tcp_connections`: Maximum concurrent Syslog TCP connections (Env: `SYSLOG_MAX_TCP_CONNECTIONS`, default: `250`).
- `syslog_tcp_inactivity_timeout`: Syslog TCP client inactivity timeout in seconds (Env: `SYSLOG_TCP_INACTIVITY_TIMEOUT`, default: `0.0` - disabled).

Sensitive configuration - AI provider, AI API key (Fernet-encrypted), AI base URL, AI model, AI fallback models, custom system prompt, internal log level, version update check toggle, and active retention days - is managed through the primary Application Settings tab.


---

## 2. Database Schema & Storage Engine (SQLite + FTS5)
Database path: `/data/logs.db`. WAL mode enabled (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;`).
Schema migrations managed via `backend/app/core/migrations.py` using `PRAGMA user_version`.

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

-- FTS5 Synchronization Triggers (Explicit External Content Deletion Pattern)
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
   - Drops the synchronous `logs_ai` AFTER INSERT trigger to decouple raw ingestion from FTS tokenization and indexing:
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
       triggered_at DATETIME NOT NULL
   );

   CREATE INDEX IF NOT EXISTS idx_alert_history_triggered_at ON alert_history(triggered_at DESC);
   CREATE INDEX IF NOT EXISTS idx_alert_history_rule_id ON alert_history(rule_id);
   CREATE INDEX IF NOT EXISTS idx_alert_history_rule_time ON alert_history(rule_id, triggered_at DESC);
   CREATE INDEX IF NOT EXISTS idx_alert_history_rule_name ON alert_history(rule_name, triggered_at DESC);
   CREATE INDEX IF NOT EXISTS idx_alert_history_ai_audit_id ON alert_history(ai_audit_id);
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

### 2.3 Asynchronous FTS5 Indexing Model & Worker Architecture
- **Supervised Background Task:** `FTSIndexWorker` (`backend/app/services/fts_indexer.py`) runs as a supervised task under `_supervise_worker` in `backend/app/main.py`. Worker failures are isolated, logged, and restarted with exponential backoff.
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
- **Retention Coordination:** Retention pruning (`backend/app/services/retention.py`) coordinates with `fts_index_state.last_indexed_id`. During retention pruning (`execute_prune`), pending unindexed logs are indexed first (`index_pending_logs`), and deletions enforce `AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1)` to guarantee no unindexed log records are ever purged without being indexed, preventing orphaned FTS records or unindexed log deletion.
- **Graceful Shutdown Sequencing:** Lifespan shutdown stops log collectors (`SyslogServer`, `DockerTailer`), flushes the multiline assembler, stops `QueueConsumer` (drains queue to DB), then stops `FTSIndexWorker` (flushes pending unindexed logs to `logs_fts`), followed by `PruneWorker`, `StorageMetricsWorker`, `AlertEvaluator`, `DailyDigestWorker`, notification executor, regex executor, and flushes drop filter counts.

### 2.4 Thread-Local Connection Model & Concurrency
- **Thread-Local Read Connection Reuse:** In `backend/app/api/deps.py`, synchronous database queries are dispatched to worker threads via `run_db_query(fn, custom_db_path)` using `asyncio.to_thread()`. `get_thread_read_connection(db_path: Path) -> sqlite3.Connection` maintains a thread-local dictionary `_thread_local.connections` keyed by resolved database path, reusing connections across sequential queries on worker threads.
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

### 2.5 Retention Pruning
Daily task runs iterative batch pruning coordinated with the FTS indexing watermark to prevent WAL expansion, lock contention, and orphaned FTS rows:
1. Drain unindexed logs via `index_pending_logs(db_path)` to ensure all historical records are searchable prior to purge.
2. Loop batch deletions until no matching rows remain, guarded by `last_indexed_id`:
   `DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE timestamp < datetime('now', '-' || :retention_days || ' days') AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1) LIMIT 5000);`
3. Execute FTS5 index compaction:
   `INSERT INTO logs_fts(logs_fts) VALUES('optimize');`
4. Checkpoint and truncate the WAL:
   `PRAGMA wal_checkpoint(TRUNCATE);`

### 2.6 Storage Metrics & Disk Monitoring
- **Sampling Strategy:** Background worker samples metrics hourly and immediately following any manual/automated prune event.
 - Compute total database disk footprint via `os.path.getsize()` across `/data/logs.db`, `/data/logs.db-wal`, and `/data/logs.db-shm`.
 - Read host mount capacity and free space using `shutil.disk_usage("/data")`.
 - Record snapshot into `storage_metrics` table.
- **Metrics Retention:** Prune records from `storage_metrics` older than 30 days during the daily retention cleanup cycle.

---

## 3. Ingestion & In-Memory Pipeline

```
[Syslog Listener (UDP/TCP 1514)] ──┐
├──> [Keyed Multiline Assembler] ──> [asyncio.Queue(10000)] ──> [SQLite Batch Writer (stdlib sqlite3 + asyncio.to_thread)]
[Docker Tailer (Socket/Proxy)]  ──┘

```

* **Syslog Ingestion:** Async UDP and TCP on port `1514` (configurable via `SYSLOG_PORT`). RFC 3164 and RFC 5424 parsing with RFC 6587 octet-counted and newline-delimited TCP framing. Default TCP connection ceiling is 250 (configurable via `SYSLOG_MAX_TCP_CONNECTIONS`) with TCP keepalive enabled (`SO_KEEPALIVE`). TCP inactivity timeout defaults to disabled (`0.0`, configurable via `SYSLOG_TCP_INACTIVITY_TIMEOUT`) to prevent disconnection of persistent log forwarders during idle periods. Unparseable messages default to severity 6 (Info) preserving raw content.
* **Docker Ingestion:** Connects via `DOCKER_HOST`. Tails running containers and listens for Docker lifecycle events (`start`/`die`). Sets `source_alias="docker"` (or value of `DOCKER_SOURCE_ALIAS`) and `app_name=container_name`.
* **Keyed Multiline Assembler:** Buffers continuation lines (e.g., lines starting with whitespace, `\t`, `Caused by:`, `Traceback`) mapped by stream key:
  * Syslog streams: `stream_key = f"{source_ip}:{app_name}"`
  * Docker streams: `stream_key = f"docker:{container_id}"`
  * Flushes buffered lines into a single log entry on a **150ms per-stream timeout** or upon receiving a new RFC-compliant timestamped header for that stream.
* **Raw Log Storage:** Logs are committed to SQLite in their original unredacted format. Redaction is not applied at ingestion.
* **Bounded Buffer & Batch Flusher:** `asyncio.Queue(maxsize=10000)`. If saturated, increment atomic `dropped_logs_total` counter. Flusher commits batches to SQLite after a **50ms debounce window** or when the batch reaches 5000 records. The flusher aggressively drains pending items using `queue.get_nowait()` during each cycle to boost throughput, prevent artificial bottlenecks, and provide low-latency real-time streaming to UI and SSE subscribers.
* **Chunked Batch Inserts with RETURNING id:** In `QueueConsumer._insert_batch`, drained items are partitioned into chunks of <= 500 records to respect SQLite parameter limits (9 columns * 500 = 4,500 parameters << 32,766 limit). Each chunk is inserted using a single multi-row parameterized `INSERT INTO logs (...) VALUES (...), ... RETURNING id` statement. The returned database auto-increment IDs are sequentially mapped to in-memory entries, broadcast to live SSE subscribers (`/api/logs/stream`), and `fts_indexer.notify_new_logs()` is signaled immediately upon transaction commit for sub-second FTS search catch-up.

---

## 4. AI Analysis Engine

LogShed provides two distinct AI analysis modes: user-initiated on-demand log analysis and automated alert incident enrichment.

### 4.1 Analysis Modes

#### 1. On-Demand Analysis (User-Initiated)
- **Selection:** User selects one or multiple log entries in the UI across single or multiple hosts. Cross-host log selection is supported. The prompt builder annotates each dispatched log line with its originating host/source alias (`[{timestamp}] [{source_alias}] [{app_name}] {message}`) and aggregates notes for all unique hosts present in the batch.
- **On-Demand Redaction Pass:** The backend filters selected logs through `redactor.py` (scrubbing tokens, passwords, JWTs, cloud API keys, and credentials) before returning the preview payload to the UI.
- **Payload Inspection & User Context:**
  - UI opens an analysis modal displaying:
    * The scrubbed, redacted text preview exactly as it will be dispatched to the LLM.
    * Active provider and model name.
    * Estimated token count.
    * Free-text user context input field.
- **Execution Gate:** Outbound API calls occur **only** when the user explicitly clicks **"Run Analysis"** (or starts streaming via `/api/ai/diagnose/stream`).
- **Audit Logging:** Every on-demand request is recorded in `ai_audit_log` with `trigger_source = 'on-demand'` (prompt, user context, response, tokens used, and thoughts).

#### 2. Automated Alert Enrichment (Background Incident Enrichment)
- **Rule Configuration:** Alert rules can optionally enable automated background incident enrichment (`ai_enrichment = 1` in `alert_rules`).
- **Trigger Execution:** When an enabled alert rule with AI enrichment fires, `AlertEvaluator` redacts the triggering log sample through `redactor.py` and dispatches it to the configured LLM.
- **Incident Summary & Remediation:** The model generates a concise incident summary, root-cause diagnosis, and actionable remediation steps. This structured analysis is embedded directly into outbound notification payloads (dispatched via configured Apprise notification channels) and saved in `alert_history`.
- **Multi-Model Failover:** If the primary model encounters a transient error, timeout, or rate limit, the engine automatically attempts evaluation against configured fallback models.
- **Audit Tracking:** Automated alert analysis runs are recorded in `ai_audit_log` with `trigger_source = 'alert'` and linked to `alert_history.ai_audit_id`.
- **Privacy and Token Safeguards:** Clear privacy, credential redaction, and token usage warnings are presented in the UI when enabling this option (inside `AlertRuleModal` and notice banners on the Rules & Alerts Hub). The UI explicitly informs administrators that triggering logs are automatically dispatched to external AI providers without manual pre-screening, that automated scrubbing operates on a best-effort basis, and that automated triggers consume API tokens. If an AI provider is not configured in Settings, AI enrichment cannot be enabled and displays a direct link to Settings.

### 4.2 AI Provider Abstraction
Unified client supporting Google Gemini (`google-genai` SDK), Anthropic Claude (`anthropic` SDK), and OpenAI-compatible endpoints (`openai` SDK, configurable `base_url` for Ollama/vLLM/LocalAI).
- **Model Configuration:** Configurable default model per provider (e.g., `gemini-3.7-flash`, `gpt-4o`, `claude-sonnet-4-6`, `llama3.2`), with fallback model lists and optional per-request overrides in the UI modal.
- **Dynamic Model Discovery:** Discovers available models from the configured provider, cached in SQLite for 24 hours. A background worker (`ModelRefreshWorker`) refreshes the cache every 12 hours, and manual refresh can be triggered via `/api/ai/models/refresh`.
- **Prompt Construction:**
  - System prompt establishes role as an expert systems engineer and Linux/Docker administrator.
  - Context includes:
    * System metadata (Host alias, container/app name).
    * Sequenced log block in chronological order.
    * User-provided notes/context (or alert incident parameters).
  - Model generates a structured Markdown response containing:
    1. **Summary:** 1 - 2 sentence overview of the issue.
    2. **Root Cause Analysis:** Explanation of why the event occurred.
    3. **Actionable Remediation:** Step-by-step commands, configuration fixes, or debugging steps.
- **Audit Logging:** Every AI request (on-demand or alert-driven) is recorded in `ai_audit_log` (prompt, user context, response, tokens used, and trigger source).


---

## 5. Security & Authentication

* **Password Hashing:** `argon2id` via `argon2-cffi` (single library - do not also add `pwdlib`). Verification is offloaded to worker threads via `asyncio.to_thread` to keep authentication from blocking the event loop.
* **First-Run Setup Lockout:** `/api/auth/setup` is only accessible when the `admin_auth` table is empty. If an admin record exists, `/api/auth/setup` immediately returns `403 Forbidden`.
* **Session Security:** Cryptographically signed, HTTP-only, `SameSite=Lax` session cookies. No JWTs in browser storage.
* **CSRF Request Protection:** Mutating API endpoints (`POST`, `PUT`, `DELETE`, `PATCH`) require the custom `X-Requested-With` header to prevent cross-site request forgery.
* **Rate Limiting:**
  * In-memory sliding window on `/api/auth/login` (5 failed attempts per IP per minute).
  * In-memory sliding window on `/api/ai/diagnose` and `/api/ai/diagnose/stream` (10 requests per minute per user/session) to prevent runaway LLM consumption.
* **Secrets at Rest:** API keys encrypted with `cryptography.fernet`. Master encryption key stored at `/data/.secret_key` (generated automatically on first boot with `0600` permissions).
* **CLI Password Recovery:** Single-command rescue executable inside container (accepts `--password` or prompts securely via terminal):
```bash
python -m app.cli reset-admin [--password <new_password>]
```



---

## 6. API Specifications & Contracts

### 6.1 Route Versioning & Mirror Router
The internal Web UI uses the unversioned `/api/*` prefix. For external tools, third-party integrations, and script automation, a mirror router is mounted at `/api/v1/*`, guaranteeing backwards-compatible route stability. All endpoints documented below are available identically under both `/api/*` and `/api/v1/*`.

### 6.2 Query Pagination & Evaluation Capping
When searching or listing log records via `GET /api/logs`, the response returns a `LogListResponse` payload containing:
- `logs`: Array of `LogEntry` records matching query filters.
- `total`: Total count of matching records up to the evaluation ceiling.
- `total_capped`: Boolean flag indicating whether the total count was capped at the evaluation limit (`max(1001, offset + limit + 1)`). To prevent full-table scan bottlenecks on vast datasets under broad filters, SQLite limits the count calculation. When `total_capped` is true, the user interface displays a count indicator like `1,000+` or `(capped)` rather than executing expensive exhaustive table scans.

### 6.3 Endpoint Directory

| Method | Endpoint | Purpose | Payload / Parameters |
| --- | --- | --- | --- |
| **Authentication** |  |  |  |
| `POST` | `/api/auth/setup` | First-time admin creation (returns `403` if already configured) | `{"password": "..."}` |
| `POST` | `/api/auth/login` | Session login (rate-limited) | `{"password": "..."}` |
| `POST` | `/api/auth/logout` | Invalidate session cookie | None |
| `GET` | `/api/auth/status` | Read setup status and current session authentication state | None (returns `{"setup_required": bool, "authenticated": bool}`) |
| `POST` | `/api/auth/password` | Update admin password for authenticated session | `{"current_password": "...", "new_password": "..."}` |
| **Log Management & Deletion** |  |  |  |
| `GET` | `/api/logs` | Search & filter logs. Supports FTS5 query syntax, source/app filtering, severity threshold (`severity_max` 0-7), ISO datetime range, limit, offset. Returns `logs`, `total`, and `total_capped` | `query`, `source` (multi), `app_name` (multi), `severity_max`, `from`, `to`, `limit`, `offset` |
| `GET` | `/api/logs/stream` | Real-time Server-Sent Events (SSE) streaming incoming log entries | `severity_max`, `source`, `app_name` |
| `GET` | `/api/logs/facets` | Fetch all distinct sources, apps, and their mappings | None |
| `GET` | `/api/logs/{id}/context` | Fetch surrounding context lines symmetrically around target log | `lines` (default 10), `same_app: bool` (default `false`) |
| `DELETE` | `/api/logs/{id}` | Delete a single log record by primary key ID | None |
| `POST` | `/api/logs/delete` | Batch targeted deletion of logs matching filter criteria or specific IDs (requires confirmation parameters or `delete_all: true`) | `{"log_ids": [...], "query": "...", "source": [...], "app_name": [...], "severity_max": ..., "from": "...", "to": "...", "delete_all": bool}` |
| `POST` | `/api/logs/delete/preview` | Calculate count of logs matching deletion criteria without deleting them | Same payload as `/api/logs/delete` |
| **Alert Rules & Incident History** |  |  |  |
| `GET` | `/api/alerts/rules` | List all configured alert rules | None |
| `POST` | `/api/alerts/rules` | Create a new alert rule | `{"name": "...", "rule_type": "...", "channel_id": ..., "filter_app": "...", "filter_severity": ..., "match_pattern": "...", "threshold_count": 1, "window_seconds": 60, "cooldown_seconds": 300, "ai_enrichment": false, "is_enabled": true}` |
| `GET` | `/api/alerts/rules/{rule_id}` | Retrieve a single alert rule by ID | None |
| `PUT` | `/api/alerts/rules/{rule_id}` | Update an existing alert rule | Partial or complete rule fields |
| `DELETE` | `/api/alerts/rules/{rule_id}` | Delete an alert rule | None |
| `POST` | `/api/alerts/test` | Test alert rule match pattern against historical logs (dry-run) | `{"rule_type": "...", "match_pattern": "...", "filter_app": "...", "filter_severity": ..., "window_seconds": ..., "threshold_count": ..., "sample_size": ...}` |
| `GET` | `/api/alerts/export` | Export all alert rules as JSON bundle | None |
| `GET` | `/api/alerts/{rule_id}/export` | Export a single alert rule as JSON | None |
| `POST` | `/api/alerts/import` | Import alert rules from JSON payload | `{"rules": [...], "collision_strategy": "skip" \| "overwrite" \| "rename"}` |
| `GET` | `/api/alerts/presets` | List built-in security canary alert presets (e.g., SSH brute force, OOM killer, sudo escalation) | None |
| `POST` | `/api/alerts/presets/{preset_id}/install` | Install a built-in alert preset | `{"channel_id": ..., "is_enabled": true}` |
| `GET` | `/api/alerts/history` | Fetch paginated incident firing history with AI summaries, full prompt envelopes, model identifiers, and token breakdowns | Query params: `rule_id`, `limit`, `offset` |
| `DELETE` | `/api/alerts/history/{history_id}` | Delete a single incident history record | None |
| `DELETE` | `/api/alerts/history` | Clear all incident firing history records | None |
| `GET` | `/api/alerts/maintenance` | Retrieve current alert maintenance window status and recurring schedules | None |
| `POST` | `/api/alerts/maintenance` | Activate or clear active on-demand maintenance window | `{"until": "<ISO-8601 or null>"}` |
| `POST` | `/api/alerts/maintenance/schedules` | Update recurring maintenance schedules | `{"schedules": [{"day_of_week": 0, "start_time": "02:00", "end_time": "04:00", "is_enabled": true}]}` |
| **Ingestion Drop Rules** |  |  |  |
| `GET` | `/api/drop_rules` | List all configured drop rules with drop counters | None |
| `POST` | `/api/drop_rules` | Create a new drop rule | `{"name": "...", "source_pattern": "...", "app_pattern": "...", "message_pattern": "...", "is_regex": false, "severity_threshold": ..., "is_enabled": true}` |
| `PUT` | `/api/drop_rules/{rule_id}` | Update an existing drop rule | Partial or complete drop rule fields |
| `DELETE` | `/api/drop_rules/{rule_id}` | Delete a drop rule | None |
| `POST` | `/api/drop_rules/{rule_id}/reset` | Reset dropped counter for a specific drop rule to zero | None |
| `POST` | `/api/drop_rules/test` | Dry-run test a drop rule pattern against recent logs | `{"source_pattern": "...", "app_pattern": "...", "message_pattern": "...", "is_regex": false, "severity_threshold": ..., "sample_size": ...}` |
| `GET` | `/api/drop_rules/export` | Export all drop rules as JSON bundle | None |
| `GET` | `/api/drop_rules/{rule_id}/export` | Export a single drop rule as JSON | None |
| `POST` | `/api/drop_rules/import` | Import drop rules from JSON payload | `{"rules": [...], "collision_strategy": "skip" \| "overwrite" \| "rename"}` |
| `GET` | `/api/drop_rules/presets` | List built-in drop rule presets | None |
| `POST` | `/api/drop_rules/presets/{preset_id}/install` | Install a built-in drop preset | None |
| **Notification Channels & Daily Digest** |  |  |  |
| `GET` | `/api/notifications/channels` | List configured Apprise notification channels | None |
| `POST` | `/api/notifications/channels` | Create a new notification channel | `{"name": "...", "url": "...", "is_enabled": true}` |
| `PUT` | `/api/notifications/channels/{channel_id}` | Update an existing notification channel | `{"name": "...", "url": "...", "is_enabled": true}` |
| `DELETE` | `/api/notifications/channels/{channel_id}` | Delete a notification channel | None |
| `POST` | `/api/notifications/test` | Test dispatch a notification to a specific URL or channel ID | `{"url": "..."}` or `{"channel_id": ...}` |
| `POST` | `/api/notifications/digest/send` | Trigger on-demand dispatch of the 24-hour daily analytical digest | None |
| **Saved Views** |  |  |  |
| `GET` | `/api/saved_views` | List all saved search and filter views | None |
| `POST` | `/api/saved_views` | Create a new saved view | `{"name": "...", "query_params": "...", "is_pinned": false}` |
| `PUT` | `/api/saved_views/{view_id}` | Update a saved view (rename, update query parameters, pin/unpin) | `{"name": "...", "query_params": "...", "is_pinned": bool}` |
| `DELETE` | `/api/saved_views/{view_id}` | Delete a saved view | None |
| **AI Analysis Engine** |  |  |  |
| `POST` | `/api/ai/preview` | Generate redacted preview and token estimate | `{"log_ids": [101, 102], "user_context": "...", "prompt_override": "..."}` |
| `POST` | `/api/ai/diagnose` | Execute user-confirmed AI diagnosis (rate-limited) | `{"log_ids": [101, 102], "user_context": "...", "prompt_override": "...", "system_prompt_override": "...", "provider": "...", "model": "...", "fallback_models": ["..."]}` |
| `POST` | `/api/ai/diagnose/stream` | Stream live diagnosis stages, failover events, and tokens via SSE | Same payload as `/api/ai/diagnose` |
| `GET` | `/api/ai/models` | Discover available models from configured or requested provider (cached in SQLite for 24h) | Query params: `provider`, `refresh: bool` |
| `POST` | `/api/ai/models/refresh` | Force immediate live refresh and cache update of available AI models from the provider | None |
| **Host Aliases** |  |  |  |
| `GET` | `/api/aliases` | List IP-to-Host mappings | None |
| `POST` | `/api/aliases` | Upsert host alias mapping (retroactively updates existing logs) | `{"ip": "...", "alias": "...", "notes": "..."}` |
| `DELETE` | `/api/aliases/{ip}` | Remove host alias (reverts existing logs to raw IP) | None |
| **Settings** |  |  |  |
| `GET` | `/api/settings` | Read application configuration (keys masked) | None |
| `POST` | `/api/settings` | Update settings (encrypted at rest) | `{"ai_provider": "...", "ai_model": "...", "ai_fallback_models": "...", "ai_api_key": "...", "ai_base_url": "...", "ai_system_prompt": "...", "retention_days": 14, "internal_log_level": "WARNING", "check_for_updates": true}` |
| **System & Maintenance** |  |  |  |
| `GET` | `/api/health` | Container healthcheck (unauthenticated returns minimal `{"status": "ok"}`; authenticated returns DB status, queue depth, dropped count, ingest rate) | Returns HTTP 503 if database check fails |
| `POST` | `/api/maintenance/prune` | Trigger manual retention purge, compaction, and metrics snapshot | None |
| `POST` | `/api/system/vacuum` | Execute SQLite database VACUUM to reclaim disk space after large deletions (guarded by lock, requires 2x free disk space) | None |
| `GET` | `/api/system/storage` | Fetch live disk usage & 30-day history | `{"db_size_bytes": ..., "disk_free_bytes": ..., "disk_total_bytes": ..., "history": [...]}` |
| `GET` | `/api/system/version` | Read installed version and check GHCR for stable release updates | Query params: `refresh: bool` |
 
 
---
 
## 7. Frontend Specification (React + Vite + Tailwind)

### 7.1 Navigation Hierarchy
The frontend implements single-page HTML5 History API routing across four top-level navigation destinations:
- **`/` (Logs / Console):** Live streaming log console with virtual scrolling, search, filters, saved views, and context inspection.
- **`/rules` (Rules & Alerts Hub):** Unified hub managing alert rules, ingestion drop rules, incident history, maintenance windows, and built-in canary presets.
- **`/storage` (Storage Metrics):** Storage metrics dashboard displaying database file sizes, host mount disk usage, 30-day volume trends, manual prune triggers, and database vacuum utilities.
- **`/settings` (Settings & Aliases):** Application settings, AI provider configuration, notification channels, advanced runtime options, and IP-to-Host alias management located at `/settings/aliases`. Top-level requests to `/aliases` automatically redirect to `/settings/aliases`.

Unsaved changes detection and browser exit guards prevent accidental loss of form inputs across settings tabs and modal dialogs.

### 7.2 Core Components & Views

* **Console Viewer (Live Stream - `/`):**
  * Virtual list (`@tanstack/react-virtual`) capable of handling 50k+ lines without DOM lag.
  * Real-time Server-Sent Events (SSE) stream with auto-scroll and pause-on-scroll-up detection.
  * Severity tinted visual badges (Emergency/Alert/Crit/Error = Red, Warning = Yellow, Notice/Info/Debug = Slate/Blue).
  * Checkbox multi-select mode with a floating action bar: `"Run Analysis (N)"` or `"Delete Selected (N)"`.
  * Search & Filter Bar: Full-text search with SQLite FTS5 syntax, timestamp and date-range picker, multi-select dropdown filters for Host Alias (`source_alias`), Source IP, and Container/App Name (`app_name`), and severity threshold filter slider/pills (RFC 5424 numerical priorities 0 = Emergency through 7 = Debug).
  * Saved Views Menu: Quick-load saved search filters, save current query parameters with custom names, and pin frequently used views directly to the filter bar.
  * Log Detail & Context Inspector: Slide-over panel displaying parsed metadata, source IP, facility, exact timestamp, and raw unparsed syslog payload, with one-click action to load surrounding context logs (symmetric 10 entries around selected record).
  * Targeted Log Deletion Modal: Allows deletion of selected records, filtered ranges, or all historical logs with confirmation guards and count preview.

* **Rules & Alerts Hub (`/rules`):**
  * **Alert Rules Tab:** Full CRUD management for threshold, pattern match, and spike alert rules. Configure Apprise notification targets, sliding evaluation windows, cooldown suppression periods, and optional automated AI root-cause incident enrichment. Includes dry-run pattern testing against historical logs and JSON bundle export/import.
  * **Automated AI Redaction Notice:** When rules with AI enrichment are enabled, the UI renders prominent warnings explaining that triggering events are dispatched automatically to external AI providers without manual review, that automated scrubbing operates on a best-effort basis, and that automated triggers consume API tokens.
  * **Drop Rules Tab:** Configure pre-storage discard filters (keyword or regex) matching message content, application name, or source, with severity thresholds. Displays live dropped counters per rule, counter reset actions, dry-run testing against recent logs, and JSON export/import.
  * **Alert History Tab:** Unified incident history and AI audit records covering on-demand log analyses, automated rule enrichments, and daily analytical digests. Detail inspection drawer (rendered via `IncidentHistoryDetail.tsx`) presents full prompt envelopes, model badges, incident summaries, and token consumption breakdowns (input, output, reasoning/thought, and total). Supports single-item and bulk history deletion.
  * **Maintenance Window Tab:** Configure active on-demand alert suppression periods or recurring schedules by day of week and time window to silence alerts during routine maintenance.
  * **Security Canary Presets Modal:** 1-click installation of production-tested security canary alerts (SSH brute force, OOM killer, sudo privilege escalation, proxy authentication floods) and drop rule presets (Docker healthchecks, noisy background daemons).

* **On-Demand AI Analysis Modal:**
  * Redacted log preview displaying the exact scrubbed text to be dispatched (with one-click copy).
  * Real-time token counter and active model selection.
  * Free-text user context textarea to provide situational background (e.g., recent system updates, topology changes).
  * Markdown-rendered analysis display (Summary, Root Cause, Remediation steps with copyable code/command blocks).

* **Storage Management Panel (`/storage`):**
  * **Current Storage Card:** Dual-metric display showing active Database Footprint (MB/GB) alongside a visual progress bar for Available Mount Disk Space.
  * **30-Day Storage Trend Chart:** Compact line/area chart (via `recharts`) plotting DB disk footprint and total log volume over the past 30 days.
  * **Maintenance Actions:** Manual trigger for log purge, FTS5 index compaction, WAL truncation, and SQLite vacuum with real-time UI updates.

* **Settings Panel (`/settings`):**
  * **Application Settings:** Encrypted API key management (Google Gemini, Anthropic Claude, OpenAI / custom OpenAI-compatible endpoint like Ollama/vLLM), dynamic model discovery with 24-hour cache, multi-model failover configuration, custom system prompt, retention slider (1 to 30 days, capped by `MAX_RETENTION_DAYS`), internal log level, and GHCR release update check toggle.
  * **Notification Channels Card:** Configure Apprise push notification targets (Discord, Telegram, Slack, Pushover, email, webhook, etc.) with live test dispatch, and schedule daily analytical digests (`NotificationsCard.tsx`).
  * **Host Alias Manager (`/settings/aliases`):** Dedicated table to manage IP-to-Hostname mappings (e.g., `192.168.1.1` -> `OPNsense Firewall`) with quick-add prompts for newly detected, unmapped IP addresses.
  * **Advanced Settings (`/settings/advanced`):** Runtime controls for AI timeout, reasoning token budget, base application URL, private IP webhook targets, Docker collector toggle and exclusions, trusted proxy headers, cookie security, and Syslog TCP connection ceilings (`AdvancedSettingsCard.tsx`).

* **Build & Asset Distribution:**
* Vite configured to build production static assets directly into `backend/app/static/`.
* FastAPI configured to serve static assets with an SPA fallback to `index.html`.

---

## 8. Deployment & Container Specification

### 8.1 Dockerfile Requirements

* **Stage 1 (Frontend):** Node 20 alpine builds React SPA (`npm run build`).
* **Stage 2 (Runtime):** Python 3.12-slim with `tini`, `curl`, `gosu`. Copies backend and frontend build.
* **Healthcheck:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/api/health || exit 1

```


* **Ports:** `8080/tcp` (Web UI & API), `1514/udp` and `1514/tcp` (Syslog, configurable via `SYSLOG_PORT`).
* **Volumes:** `/data` (Storage), `/var/run/docker.sock` (Host socket or omitted if using socket-proxy).

### 8.2 Unraid Template Directives

* Map `/data` to direct cache-pool appdata: `/mnt/cache/appdata/logshed` (avoids Unraid FUSE `shfs` locking/mmap issues on SQLite WAL and prevents spinning up array parity disks).
* Map host port `1514` (UDP/TCP) to container `1514`.
* Template defaults to `PUID=99` and `PGID=100` (`nobody:users`) to match standard Unraid share permission conventions. The container `entrypoint.sh` automatically maps these IDs and adjusts `/data` ownership on startup.

### 8.3 Multi-Host & Remote Docker Deployment Architecture

LogShed supports heterogeneous, multi-host homelab configurations across bare metal, virtual machines, and multiple Docker hosts:

1. **Local Host (Direct Socket Mount):**
   Mount `/var/run/docker.sock:/var/run/docker.sock:ro` into the LogShed container. Default `DOCKER_HOST=unix:///var/run/docker.sock` and `DOCKER_SOURCE_ALIAS=docker`.

2. **Single Remote Docker Host (Socket Proxy):**
   Connect directly to a remote Docker daemon or Docker socket proxy (such as `tecnativa/docker-socket-proxy`) without mounting any local socket:
   ```env
   DOCKER_HOST=tcp://192.168.1.50:2375
   DOCKER_SOURCE_ALIAS=remote-docker
   ```

3. **Multi-Host Docker Environments (Syslog Forwarding):**
   For environments running containers across multiple nodes (e.g. Proxmox LXC/VMs, multiple physical servers), configure each remote Docker daemon's native syslog log driver in `/etc/docker/daemon.json` to forward container logs to LogShed on port `1514`:
   ```json
   {
     "log-driver": "syslog",
     "log-opts": {
       "syslog-address": "udp://<logshed-ip>:1514",
       "tag": "{{.Name}}"
     }
   }
   ```
   Or via TCP:
   ```json
   {
     "log-driver": "syslog",
     "log-opts": {
       "syslog-address": "tcp://<logshed-ip>:1514",
       "tag": "{{.Name}}"
     }
   }
   ```
   The remote host's IP or hostname is automatically attributed by LogShed's Syslog collector, and the container name is mapped to `app_name` via the tag.

