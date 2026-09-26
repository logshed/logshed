# Technical Specification: LogShed

## 1. System Architecture & Process Model
Single Docker container running Python 3.12 (`asyncio`) + FastAPI backend serving a pre-built React SPA, with background ingestion workers managed under isolated supervisors.

- **Process Boundaries:** Main thread runs `uvicorn` and supervised background tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`). SQLite operations must use stdlib `sqlite3` and offload synchronous queries via `asyncio.to_thread()` (per AGENTS.md, `aiosqlite` is not used). Thread-local read connection reuse (`get_thread_read_connection` in `backend/app/api/deps.py`) eliminates connection setup overhead across queries in worker threads with defensive rollback guards in `finally` blocks preventing WAL lock leaks.
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

### 2.2 Schema Migration v2 (Decoupled Asynchronous FTS5 Indexing)
Migration v2 upgrades the database schema to `PRAGMA user_version = 2;`:

1. **Drop Synchronous Trigger:**
   Removes the synchronous `logs_ai` AFTER INSERT trigger to decouple raw ingestion from FTS tokenization and indexing:
   ```sql
   DROP TRIGGER IF EXISTS logs_ai;
   ```
   This eliminates indexing overhead from the raw ingestion transaction path, boosting write throughput and preventing ingestion stalls during burst traffic.

2. **Durable Index State Tracking:**
   Creates `fts_index_state` table tracking the maximum log ID indexed into `logs_fts`:
   ```sql
   CREATE TABLE IF NOT EXISTS fts_index_state (
       id INTEGER PRIMARY KEY CHECK (id = 1),
       last_indexed_id INTEGER NOT NULL DEFAULT 0,
       updated_at DATETIME NOT NULL
   );

   INSERT OR IGNORE INTO fts_index_state (id, last_indexed_id, updated_at)
   VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM logs), datetime('now'));
   ```
   For upgraded databases, `last_indexed_id` is initialized to the current `MAX(id)` from `logs`, ensuring historical logs already indexed under v1 are not re-indexed.

3. **Conditional Deletion and Update Triggers:**
   In SQLite FTS5 external content tables (`content='logs', content_rowid='id'`), issuing an FTS delete record (`INSERT INTO logs_fts(logs_fts, rowid, ...) VALUES('delete', ...)`) against a row ID that has not yet been indexed into `logs_fts` raises fatal error `sqlite3.DatabaseError: database disk image is malformed`.
   Migration v2 replaces unconditional `logs_ad` and `logs_au` triggers with conditional triggers guarded by `last_indexed_id`:
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
   These trigger conditions guarantee that deleting or updating unindexed logs never invokes FTS deletion instructions for non-existent FTS records.

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
- **Search Consistency Middleware:** When search queries arrive at `/api/logs?query=...`, middleware triggers `index_pending_logs(db_path)` prior to executing the FTS search query, providing read-your-own-writes consistency.
- **Retention Coordination:** Retention pruning (`backend/app/services/retention.py`) coordinates with `fts_index_state.last_indexed_id`. During retention pruning (`execute_prune`), pending unindexed logs are indexed first (`index_pending_logs`), and deletions enforce `AND id <= (SELECT COALESCE(last_indexed_id, 0) FROM fts_index_state WHERE id = 1)` to guarantee no unindexed log records are ever purged without being indexed, preventing orphaned FTS records or unindexed log deletion.
- **Graceful Shutdown Sequencing:** Lifespan shutdown stops log collectors (`SyslogServer`, `DockerTailer`), flushes the multiline assembler, stops `QueueConsumer` (drains queue to DB), then stops `FTSIndexWorker` (flushes all pending unindexed logs to `logs_fts`) before shutting down `PruneWorker` and `StorageMetricsWorker`.

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
* **Bounded Buffer & Batch Flusher:** `asyncio.Queue(maxsize=10000)`. If saturated, increment atomic `dropped_logs_total` counter. Flusher commits batches to SQLite after a **50ms debounce window** or when the batch reaches 5000 records. The flusher aggressively drains pending items using `queue.get_nowait()` during each cycle to maximize throughput, prevent artificial bottlenecks, and provide low-latency real-time streaming to UI and SSE subscribers.
* **Chunked Batch Inserts with RETURNING id:** In `QueueConsumer._insert_batch`, drained items are partitioned into chunks of <= 500 records to respect SQLite parameter limits (9 columns * 500 = 4,500 parameters << 32,766 limit). Each chunk is inserted using a single multi-row parameterized `INSERT INTO logs (...) VALUES (...), ... RETURNING id` statement. The returned database auto-increment IDs are sequentially mapped to in-memory entries, broadcast to live SSE subscribers (`/api/logs/stream`), and `fts_indexer.notify_new_logs()` is signaled immediately upon transaction commit for sub-second FTS search catch-up.

---

## 4. On-Demand AI Analysis Engine

AI interactions are strictly user-initiated. No background workers or automated pipelines dispatch logs to external LLMs.

### 4.1 Log Selection & Context Enrichment Workflow
1. **Selection:** User selects one or multiple log entries in the UI across single or multiple hosts. Cross-host log selection is supported. The prompt builder annotates each dispatched log line with its originating host/source alias (`[{timestamp}] [{source_alias}] [{app_name}] {message}`) and aggregates notes for all unique hosts present in the batch.
2. **On-Demand Redaction Pass:** The backend filters selected logs through `redactor.py` (scrubbing tokens, passwords, JWTs, AWS keys) before returning the preview payload to the UI.
3. **Payload Inspection & User Enrichment:**
  - UI opens an analysis modal displaying:
     * The scrubbed, redacted text preview exactly as it will be dispatched to the LLM.
     * Active provider and model name.
     * Estimated token count.
     * Free-text user context input field.
4. **Execution Gate:** Outbound API calls occur **only** when the user explicitly clicks **"Run Analysis"**.

### 4.2 AI Provider Abstraction
Unified client supporting Google Gemini (`google-genai` SDK) and OpenAI-compatible endpoints (`openai` SDK, configurable `base_url` for Ollama/vLLM/LocalAI).
- **Model Configuration:** Configurable default model per provider (e.g., `gemini-3.7-flash`, `gpt-4o`, `llama3.2`), with an optional per-request override in the UI modal.

- **Prompt Construction:**
 - System prompt establishes role as an expert systems engineer and Linux/Docker administrator.
 - Context includes:
    * System metadata (Host alias, container/app name).
    * Sequenced log block in chronological order.
    * User-provided notes/context (if present).
 - Model generates a structured Markdown response containing:
    1. **Summary:** 1–2 sentence overview of the issue.
    2. **Root Cause Analysis:** Explanation of why the event occurred.
    3. **Actionable Remediation:** Step-by-step commands, configuration fixes, or debugging steps.
 - **Audit Logging:** Every manual request is recorded in `ai_audit_log` (prompt, user context, response and tokens used).


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

## 6. API Contracts

| Method | Endpoint | Purpose | Payload / Parameters |
| --- | --- | --- | --- |
| **Authentication** |  |  |  |
| `POST` | `/api/auth/setup` | First-time admin creation (returns `403` if already configured) | `{"password": "..."}` |
| `POST` | `/api/auth/login` | Session login (rate-limited) | `{"password": "..."}` |
| `POST` | `/api/auth/logout` | Invalidate session cookie | None |
| `GET` | `/api/auth/status` | Read setup status and current session authentication state | None (returns `{"setup_required": bool, "authenticated": bool}`) |
| `POST` | `/api/auth/password` | Update admin password for authenticated session | `{"current_password": "...", "new_password": "..."}` |
| **Log Management** |  |  |  |
| `GET` | `/api/logs` | Search & filter logs. Severity filter follows RFC 5424 numeric ordering directly, where lower numbers are more severe (`WHERE severity <= :severity_max`, e.g., `severity_max=3` returns Emergency(0) through Error(3)) | `query`, `severity_max` (0-7), `app_name`, `source`, `from`, `to`, `limit`, `offset` |
| `GET` | `/api/logs/stream` | Real-time Server-Sent Events (SSE) | `severity_max`, `source`, `app_name` |
| `GET` | `/api/logs/facets` | Fetch all distinct sources, apps, and their mappings | None |
| `GET` | `/api/logs/{id}/context` | Fetch surrounding context lines symmetrically around target log. Defaults to host activity (`same_app=false`). When `same_app=true`, restricts to matching application | Query params: `lines=10`, `same_app: bool` (default `false`) |
| **On-Demand AI Engine** |  |  |  |
| `POST` | `/api/ai/preview` | Generate redacted preview and token estimate | `{"log_ids": [101, 102], "user_context": "...", "prompt_override": "..."}` |
| `POST` | `/api/ai/diagnose` | Execute user-confirmed AI diagnosis (rate-limited) | `{"log_ids": [101, 102], "user_context": "...", "prompt_override": "...", "system_prompt_override": "...", "provider": "...", "model": "...", "fallback_models": ["..."]}` |
| `POST` | `/api/ai/diagnose/stream` | Stream live diagnosis stages, failover events, and tokens via SSE | Same payload as `/api/ai/diagnose` |
| `GET` | `/api/ai/models` | Discover available models from configured or requested provider (cached in SQLite for 24h) | Query params: `provider`, `refresh: bool` |
| `GET` | `/api/ai/audit` | Fetch historical AI queries & token usage | Query params: `limit`, `offset` |
| `DELETE` | `/api/ai/audit/{audit_id}` | Delete a single AI audit record | None |
| `DELETE` | `/api/ai/audit` | Clear all AI audit records | None |
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
| `GET` | `/api/system/storage` | Fetch live disk usage & 30-day history | `{"db_size_bytes": ..., "disk_free_bytes": ..., "disk_total_bytes": ..., "history": [...]}` |
| `GET` | `/api/system/version` | Read installed version and check GHCR for stable release updates | Query params: `refresh: bool` |


---

## 7. Frontend Specification (React + Vite + Tailwind)

* **Console Viewer (Live Stream):**
  * Virtualized log list (`@tanstack/react-virtual`) capable of handling 50k+ lines without DOM lag.
  * Real-time Server-Sent Events (SSE) stream with auto-scroll and pause-on-scroll-up detection.
  * Severity color-coded badges (Emergency/Alert/Crit/Error = Red, Warning = Yellow, Notice/Info/Debug = Slate/Blue).
  * Checkbox multi-select mode with a floating action bar: `"Run Analysis (N)"` or `"Inspect (N) Selected Logs with AI"`.

* **Selection & Previews:**
  * Multi-selection supports entries across single or multiple hosts with clear host attribution.
  * Buffer selection controls ("Select All" / "Deselect All") in the UI to quickly select all logs currently loaded in the client-side browser buffer (capped at the 200-log AI analysis ceiling) or clear selection.
  * AI analysis modal displays the scrubbed/redacted text returned by /api/ai/preview.
  * Add a Model Selection dropdown inside the AI modal and Settings panel.
  * Document that severity sliders/pills map to RFC 5424 numerical priorities (0 = Emergency … 7 = Debug).

* **Search & Filter Bar:**
  * Full-text search input with SQLite FTS5 syntax support.
  * Timestamp / date-range picker.
  * Dropdown filters for Host Alias (`source_alias`), Source IP, and Container/App Name (`app_name`).
  * Severity threshold filter slider/pills (e.g., `<= Error`).


* **Log Detail & Context Inspector:**
  * Slide-over panel displaying parsed metadata, source IP, facility, exact timestamp, and raw unparsed syslog payload.
  * One-click action to load surrounding context logs (previous/subsequent 10 entries around the selected record).
  * Direct action buttons: `"Add Host Alias"` (if unmapped) and `"Select for AI Analysis"`.


* **On-Demand AI Analysis Modal / Slide-Over:**
  * Redacted log preview displaying the exact text to be dispatched (with one-click copy).
  * Real-time token counter.
  * Free-text user context textarea to provide situational background (e.g., recent system updates, topology changes).
  * Markdown-rendered analysis display (Summary, Root Cause, Remediation steps with copyable code/command blocks).


* **Host Alias Manager (`/aliases`):**
  * Dedicated table to manage IP-to-Hostname mappings (e.g., `192.168.1.1` $\rightarrow$ `OPNsense Firewall`).
  * Quick-add prompts for newly detected, unmapped IP addresses.


* **Storage Management Panel (`/storage`):**
  * **Current Storage Card:** Dual-metric display showing active Database Footprint (MB/GB) alongside a visual progress bar for Available Mount Disk Space.
  * **30-Day Storage Trend Chart:** Compact line/area chart (via `recharts`) plotting DB disk footprint and total log volume over the past 30 days.
  * **Maintenance Actions:** Manual trigger for log purge, FTS5 optimization, and WAL truncation with real-time optimistic UI update.


* **Settings & Audit Panel (`/settings`):**
  * **Navigation & State Protection:** HTML5 History API routing across `/`, `/aliases`, `/storage`, and `/settings` with unsaved changes detection and browser exit guards.
  * **AI Provider & Model Configuration:** Encrypted API key management (Google Gemini, OpenAI / custom OpenAI-compatible endpoint like Ollama/vLLM), dynamic model discovery with 24-hour cache, multi-model failover configuration, and custom system prompt.
  * **Storage Retention:** Log retention slider (1 to 30 days by default, configurable up to `MAX_RETENTION_DAYS`, default 14 days) with override indicator when capped by environment variable.
  * **Internal Log Level:** Dynamic selector for LogShed internal diagnostic logging severity.
  * **About & Updates:** Card displaying current version, copyright, documentation links, and GHCR stable release update notifications.
  * **Interactive AI Audit Log:** Historical dispatches, user notes, model responses, token consumption breakdown (in, out, thoughts, total), and single/bulk deletion.



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

