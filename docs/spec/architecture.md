# Specification: System Architecture & Process Model

> Modular specification extracted from [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md).

## 1. System Architecture & Process Model
Single Docker container running Python 3.12 (`asyncio`) + FastAPI backend serving a pre-built React SPA, with background ingestion workers managed under isolated supervisors.

- **Process Boundaries:** Main thread runs `uvicorn` and supervised background tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`, `DropFilterWorker`, and `DailyDigestWorker`). SQLite operations must use stdlib `sqlite3` and offload synchronous queries via `asyncio.to_thread()` (per AGENTS.md, `aiosqlite` is not used). Thread-local read connection reuse (`get_thread_read_connection` in [`backend/app/api/deps.py`](file:///home/ben/workspace/logshed/backend/app/api/deps.py)) eliminates connection setup overhead across queries in worker threads with defensive rollback guards in `finally` blocks preventing WAL lock leaks.
- **Supervised Background Workers:** The application maintains 9 supervised background tasks isolated by supervisor wrappers (`_supervise_worker`) that catch exceptions, log diagnostics, and restart failing tasks with exponential backoff without disrupting the event loop:
  1. `SyslogServer`: Asynchronous UDP and TCP syslog listener on port 1514 (supporting RFC 3164, RFC 5424, and RFC 6587 framing).
  2. `DockerTailer`: Streams logs from container stdout/stderr sockets via local Unix domain socket or remote Docker HTTP socket proxy.
  3. `QueueConsumer`: Drains the in-memory buffer queue, applies active drop rules, evaluates alert rules, persists batch chunks to SQLite, broadcasts live SSE events, and signals the FTS indexing worker.
  4. `FTSIndexWorker`: Asynchronously batches unindexed log entries into the `logs_fts` external content table with durable state tracking.
  5. `PruneWorker`: Runs automated daily retention purging coordinated with the FTS watermark, followed by index compaction and WAL truncation.
  6. `StorageMetricsWorker`: Samples database file footprint and host mount disk metrics hourly and immediately following pruning operations.
  7. `ModelRefreshWorker`: Discovers and caches available AI models from the configured provider every 12 hours.
  8. `DropFilterWorker`: Flushes in-memory drop rule match counts to SQLite every 30 seconds to prevent database write contention.
  9. `DailyDigestWorker`: Compiles and delivers scheduled 24-hour analytical rollup digest notifications across configured channels. This worker performs deterministic SQL metric aggregations (ingestion volume, error counts by entity, noisy services, storage footprint delta) and operates independently without AI models or prompt synthesis.
- **Failure Isolation:** Worker exceptions must be caught, logged, and restarted with exponential backoff without crashing the event loop.
- **Security & Privileges:** Container starts as root to allow [`entrypoint.sh`](file:///home/ben/workspace/logshed/entrypoint.sh) to configure permissions. It reads `PUID` and `PGID` environment variables (defaulting to 1000:1000), maps the `appuser` to match, dynamically detects `/var/run/docker.sock` GID and adds the user to that group, changes ownership of `/data`, and drops privileges via `gosu appuser`.
- **Docker Endpoint:** Connects via `DOCKER_HOST` environment variable (`unix:///var/run/docker.sock` or `tcp://proxy:2375` for `tecnativa/docker-socket-proxy`).

### 1.1 Configuration Architecture: Bootstrap Primitives vs. Runtime Settings
LogShed separates configuration into **bootstrap primitives** (required before the SQLite database unlocks or privilege reduction occurs) and **runtime settings** (configurable through the Web UI and persisted in `system_settings`).

#### Bootstrap Primitives (Container Environment Only)
The following environment variables are supplied at container start and are strictly container-level primitives:
- `PUID` and `PGID` - User and group IDs for the application to run as via `gosu` privilege reduction (defaults to `1000:1000`).
- `TZ` - Container runtime timezone (defaults to `UTC`). Resolved at runtime and exposed through `/api/settings` to align UI presentation and AI timeline analysis with the homelab host or container clock.
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
