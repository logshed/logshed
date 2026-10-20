# Specification: System Architecture & Process Model

> Modular specification extracted from [docs/SPEC.md](../../docs/SPEC.md).

## 1. System Architecture & Process Model
Single Docker container running Python 3.12 (`asyncio`) + FastAPI backend serving a pre-built React SPA, with background ingestion workers managed under isolated supervisors.

- **Process Boundaries:** Main thread runs `uvicorn` and supervised background tasks (`SyslogServer`, `DockerCollectorPool`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`, `DropFilterWorker`, and `DailyDigestWorker`). SQLite operations must use stdlib `sqlite3` and offload synchronous queries via `asyncio.to_thread()` (per AGENTS.md, `aiosqlite` is not used). Thread-local read connection reuse (`get_thread_read_connection` in [`backend/app/api/deps.py`](../../backend/app/api/deps.py)) eliminates connection setup overhead across queries in worker threads with defensive rollback guards in `finally` blocks preventing WAL lock leaks. Lifespan shutdown coordinates termination across background supervisors and active Model Context Protocol (MCP) client sessions (`stop_mcp_session_manager`).
- **Supervised Background Workers:** The application maintains supervised background tasks isolated by supervisor wrappers (`_supervise_worker` and `_supervise_tailer`) that catch exceptions, log diagnostics, and restart failing tasks with exponential backoff without disrupting the event loop:
  1. `SyslogServer`: Asynchronous UDP and TCP syslog listener on port 1514 (supporting RFC 3164, RFC 5424, and RFC 6587 framing).
  2. `DockerCollectorPool`: Supervises multiple concurrent `DockerTailer` workers streaming container logs across local Unix sockets, remote TCP proxies, and mutual TLS endpoints.
  3. `QueueConsumer`: Drains the in-memory buffer queue, applies active drop rules, evaluates alert rules, persists batch chunks to SQLite, broadcasts live SSE events, and signals the FTS indexing worker.
  4. `FTSIndexWorker`: Asynchronously batches unindexed log entries into the `logs_fts` external content table with durable state tracking.
  5. `PruneWorker`: Runs automated daily retention purging of logs, alert history and linked AI audit records coordinated with the FTS watermark, followed by index compaction and WAL truncation.
  6. `StorageMetricsWorker`: Samples database file footprint and host mount disk metrics hourly and immediately following pruning operations.
  7. `ModelRefreshWorker`: Discovers and caches available AI models from the configured provider every 12 hours.
  8. `DropFilterWorker`: Flushes in-memory drop rule match counts to SQLite every 30 seconds to prevent database write contention.
  9. `DailyDigestWorker`: Compiles and delivers scheduled 24-hour analytical rollup digest notifications across configured channels. This worker performs deterministic SQL metric aggregations (ingestion volume, error counts by entity, noisy services, storage footprint delta) and operates independently without AI models or prompt synthesis.
- **Failure Isolation:** Worker exceptions must be caught, logged, and restarted with exponential backoff without crashing the event loop.
- **Security & Privileges:** Container starts as root to allow [`entrypoint.sh`](../../entrypoint.sh) to configure permissions. It reads `PUID` and `PGID` environment variables (defaulting to 1000:1000; `0` still starts, as in 1.2.x, but logs a warning, and a later release will refuse it), maps the `appuser` to match, dynamically detects the Docker socket GID and adds the user to that group (for a socket owned by group `0` that is the root group, as in 1.2.x, and a warning recommends a Docker socket proxy), changes ownership of `/data`, and drops privileges via `gosu appuser`.
- **Docker Endpoints:** Primary daemon connects via `DOCKER_HOST` (`unix:///var/run/docker.sock` or `tcp://proxy:2375` for `tecnativa/docker-socket-proxy`). Additional remote Docker hosts can be pre-seeded via `DOCKER_ADDITIONAL_HOSTS` and managed dynamically through the Web UI and REST API.

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
- `DOCKER_ADDITIONAL_HOSTS` - JSON array of remote Docker host endpoints to pre-seed on container boot.
- `MAX_RETENTION_DAYS` - Ceiling in days for the log retention slider in the UI (defaults to `30`, minimum `1`). While it is set it is also saved as the retention at startup and the slider is fixed to it; removing it keeps the saved value.
- `ENVIRONMENT` / `DEBUG` - `ENVIRONMENT=development` adds the localhost development CORS origins and debug logging (`production` by default). `DEBUG=true` alone no longer trusts localhost origins.
- `CORS_ORIGINS` - Comma-separated list of allowed cross-origin hosts (defaults to empty in production). A `*` entry is ignored and logged as an error.
- `ENABLE_API_DOCS` - Serves `/docs`, `/redoc` and `/openapi.json` (default `true`).
- `DOCKER_ALLOWED_SOCKETS` - Comma-separated extra Unix socket paths accepted for Docker hosts whose path does not end in `docker.sock`.
- `INGEST_MAX_BODY_BYTES` - Largest `POST /api/v1/ingest` body in bytes (default 5 MiB).
- `USER_REGEX_TIMEOUT_MS` - Per-match time limit for user alert and drop rule patterns (default `50`).
- `MCP_ALLOW_QUERY_TOKEN` - Accepts MCP API tokens in the URL query string (default `false`; a supported opt-in for MCP clients that cannot send headers).
- `SYSLOG_MAX_TCP_CONNECTIONS_PER_IP` - Concurrent Syslog TCP connections allowed from one source IP (default `20`; environment only, not a runtime setting).
- `SYSLOG_UDP_RECEIVE_BUFFER_BYTES` - Receive buffer requested for the UDP syslog socket, in bytes (default 8 MiB, `0` keeps the OS default; environment only). The kernel caps it at `net.core.rmem_max`.

*Storage Compatibility Note:* `DB_PATH` and `SECRET_KEY_PATH` remain supported as backward-compatible path overrides.

#### Three-Tier Resolution Hierarchy
Runtime operational settings follow a resilient three-tier resolution hierarchy:
1. **Tier 1 (Highest Priority):** Value persisted in SQLite (`system_settings` table) via Web UI.
2. **Tier 2 (Fallback):** Environment variable set on container (including silent legacy aliases).
3. **Tier 3 (Default):** Built-in hardcoded constant.

Settings updates apply dynamically to in-memory workers (`SyslogServer`, `DockerCollectorPool` (which manages `DockerTailer` tasks per host), `AlertEvaluator`, `NotifierService`, `Auth`, and `MCP`) without requiring container recreation. An in-memory thread-safe cache (`get_cached_system_settings` with 10-second TTL fallback) ensures high-throughput request evaluation without redundant SQLite I/O.

The runtime operational and advanced settings are:
- `enable_mcp`: Master toggle for the embedded Model Context Protocol (MCP) server (Env: `LOGSHED_ENABLE_MCP`, default: `False`). Managed via the API & MCP Access tab.
- `ai_timeout`: Outbound AI request timeout in seconds (Env: `LOGSHED_AI_TIMEOUT`, default: `45.0`).
- `ai_thinking_budget`: Reasoning token budget for extended thinking models (Env: `LOGSHED_AI_THINKING_BUDGET`, default: `1024`, 0 disables).
- `app_url`: Public base instance URL for action links in push notifications (Env: `APP_URL`, legacy alias: `app_url`, default: `""`).
- `allow_private_notification_targets`: Permit notification webhooks to target LAN/RFC1918 IPs and `.local`/`.internal`/`.lan` hosts (Env: `ALLOW_PRIVATE_NOTIFICATION_TARGETS`, default: `True`).
- `enable_docker`: Switch for the local Docker host (endpoint 1, "Primary Docker"), kept in step with that endpoint's `enabled` flag (Env: `ENABLE_DOCKER`, default: `True`; `false` in the environment keeps endpoint 1 off whatever is stored). It does not affect other Docker hosts, which each follow their own `enabled` flag.
- `docker_exclude_containers`: Comma-separated container names/IDs to ignore (Env: `DOCKER_EXCLUDE_CONTAINERS`, default: `""`).
- `docker_source_alias`: Source attribution alias for Docker logs (Env: `DOCKER_SOURCE_ALIAS`, default: `"docker"`).
- `trusted_proxies`: Comma-separated proxy IPs or CIDR blocks for client IP resolution (Env: `TRUSTED_PROXIES`, default: `""`). Recommended over `trust_docker_proxies`.
- `trust_docker_proxies`: Trusts every address in `172.16.0.0/12` to set `X-Forwarded-For` (Env: `TRUST_DOCKER_PROXIES`, legacy aliases: `TRUST_DOCKER_NETWORKS`, `TRUST_DOCKER_GATEWAY`, default: `False`). If clients can reach LogShed through the Docker userland proxy or hairpin NAT, they appear as the bridge gateway and can spoof their address to avoid the per-address login limit. Prefer `TRUSTED_PROXIES=<reverse proxy container IP>`. LogShed logs a warning at startup while this option is in effect.
- `cookie_secure`: Force `Secure` flag on session cookies behind SSL proxies stripping `X-Forwarded-Proto` (Env: `COOKIE_SECURE`, default: `False`).
- `syslog_max_tcp_connections`: Maximum concurrent Syslog TCP connections (Env: `SYSLOG_MAX_TCP_CONNECTIONS`, default: `250`).
- `syslog_tcp_inactivity_timeout`: Syslog TCP client inactivity timeout in seconds (Env: `SYSLOG_TCP_INACTIVITY_TIMEOUT`, default: `0.0` - disabled).

Sensitive configuration - AI provider, AI API key (Fernet-encrypted), AI base URL, AI model, AI fallback models, custom system prompt, internal log level, version update check toggle, and active retention days - is managed through the primary Application Settings tab.
