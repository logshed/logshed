# LogShed Configuration Reference

This document provides a comprehensive reference for all environment variables supported by LogShed, along with details on runtime settings, data persistence paths, and configuration resolution.

---

## Configuration Hierarchy

LogShed resolves configuration values through a three-tier hierarchy:

1. **Web UI Database Setting**: Settings saved via the web dashboard (**Settings > Application** or **Settings > Advanced**) take highest precedence and are encrypted at rest with Fernet cryptography.
2. **Environment Variable**: Container environment variables set in `docker-compose.yml` or `docker run` commands act as fallbacks if no database setting exists.
3. **Hardcoded Default**: If neither the database nor the environment specifies a value, LogShed uses its built-in default.

> [!NOTE]
> Bootstrap variables (such as `PORT`, `DATA_DIR`, `PUID`, `PGID`, `SYSLOG_PORT`, and `LOGSHED_SECRET_KEY`) must be configured via environment variables because they are required before the database or application runtime starts.

---

## Table of Contents

- [Core Settings](#core-settings)
- [Syslog Listener](#syslog-listener)
- [Docker Log Tailing](#docker-log-tailing)
- [Storage and Retention](#storage-and-retention)
- [AI Provider](#ai-provider)
- [Security](#security)
- [Advanced](#advanced)
- [Data Persistence Paths](#data-persistence-paths)

---

## Core Settings

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `PORT` | `8080` | HTTP listening port for the web dashboard and REST API. | 1.0.0 |
| `DATA_DIR` | `/data` | Path to persistent storage directory for the SQLite database, encryption key, and custom rule presets. | 1.0.0 |
| `PUID` | `1000` | User ID used for internal execution after privilege dropping via `gosu`. | 1.0.0 |
| `PGID` | `1000` | Group ID used for internal execution after privilege dropping via `gosu`. | 1.0.0 |
| `TZ` | `UTC` | Container timezone identifier (for example, `UTC`, `America/New_York`, `Europe/London`). | 1.0.0 |
| `APP_URL` | *(empty)* | Public base URL of the LogShed instance (for example, `https://logs.example.com`), used to build direct links in push notifications. | 1.1.0 |
| `LOGSHED_INTERNAL_LOG_LEVEL` | `WARNING` | Log level for LogShed internal diagnostic logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, or `DISABLED`). | 1.1.0 |

---

## Syslog Listener

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `SYSLOG_PORT` | `1514` | Port for both UDP and TCP syslog ingestion (valid range 1 to 65535). | 1.0.0 |
| `SYSLOG_MAX_TCP_CONNECTIONS` | `250` | Maximum number of concurrent Syslog TCP client connections. | 1.1.0 |
| `SYSLOG_TCP_INACTIVITY_TIMEOUT` | `0.0` | Inactivity timeout in seconds for Syslog TCP connections (`0.0` disables timeout, keeping persistent streams connected). | 1.1.0 |

---

## Docker Log Tailing

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker daemon endpoint (`unix:///path/to/socket` or `tcp://host:port`). Set to `none` or `disabled` to deactivate Docker log tailing. | 1.0.0 |
| `ENABLE_DOCKER` | `true` | Enable or disable Docker container log collection (`true` or `false`). | 1.1.0 |
| `DOCKER_SOURCE_ALIAS` | `docker` | Host or source name assigned to logs collected from Docker containers. | 1.1.0 |
| `DOCKER_EXCLUDE_CONTAINERS` | *(empty)* | Comma-separated list of container names or container IDs to exclude from log tailing. | 1.1.0 |
| `DOCKER_READ_TIMEOUT` | `60.0` | Read timeout in seconds for streaming container log output from the Docker API. | 1.1.0 |
| `DOCKER_HEARTBEAT_INTERVAL` | `30.0` | Interval in seconds between Docker API connection health checks. | 1.1.0 |
| `DOCKER_SOCKET_POLL_INTERVAL` | `5.0` | Initial delay in seconds when polling for socket availability during startup or reconnect. | 1.1.0 |
| `DOCKER_SOCKET_POLL_MAX` | `60.0` | Maximum backoff duration in seconds for Docker socket reconnect attempts. | 1.1.0 |

---

## Storage and Retention

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `MAX_RETENTION_DAYS` | `30` | Maximum log retention period in days allowed by the retention slider in the web interface (minimum `1`). | 1.0.0 |
| `DB_PATH` | *(empty)* | Custom SQLite database file path. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |
| `SECRET_KEY_PATH` | *(empty)* | Custom path to the persisted encryption key file. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |

---

## AI Provider

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `LOGSHED_AI_TIMEOUT` | `45.0` | Outbound HTTP request timeout in seconds for AI analysis requests to Gemini, OpenAI, or Ollama/vLLM endpoints. | 1.1.0 |
| `LOGSHED_AI_THINKING_BUDGET` | `1024` | Reasoning token budget for extended thinking models (for example, `gemini-2.5-pro` or compatible models; `0` disables). | 1.1.0 |

---

## Security

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `LOGSHED_SECRET_KEY` | *(auto-generated)* | 32-byte URL-safe base64 string for Fernet encryption of runtime settings at rest. Generated automatically at `/data/.secret_key` if unset. | 1.0.0 |
| `COOKIE_SECURE` | `false` | Enforces the `Secure` flag on HTTP session cookies (`true` or `false`). Enable when running behind an HTTPS reverse proxy. | 1.1.0 |
| `TRUSTED_PROXIES` | *(empty)* | Comma-separated list of reverse proxy IP addresses or CIDR blocks for client IP resolution via `X-Forwarded-For`. | 1.1.0 |
| `TRUST_DOCKER_PROXIES` | `false` | Automatically trust Docker bridge subnets (`172.16.0.0/12`) as reverse proxies (`true` or `false`). | 1.1.0 |
| `ALLOW_PRIVATE_NOTIFICATION_TARGETS` | `true` | Allow webhook and notification dispatches to RFC 1918 private subnets and local domain names (`true` or `false`). Set to `false` to restrict webhooks to public Internet addresses. | 1.1.0 |

---

## Advanced

| Variable | Default | Description | Since |
|---|---|---|:---:|
| `ENVIRONMENT` | `production` | Runtime mode (`production` or `development`). Enables debug logging and development CORS origins when set to `development`. | 1.0.0 |
| `DEBUG` | `false` | Enable debug mode and development settings (`true` or `false`). | 1.0.0 |
| `CORS_ORIGINS` | *(empty)* | Comma-separated list of permitted origins for cross-origin resource sharing requests. | 1.0.0 |
| `LOGSHED_VERSION` | *(runtime build)* | Override the version string displayed in the web dashboard header. | 1.0.0 |
| `LOGSHED_IMAGE_REPO` | `benhornertech/logshed` | Container image repository checked for version updates. | 1.1.0 |

---

## Data Persistence Paths

All persistent application data is stored under the `/data` directory:

| Path | Description |
|---|---|
| `/data/logs.db` | Primary SQLite database holding ingested logs, alert rules, drop rules, and settings. |
| `/data/logs.db-wal` | SQLite Write-Ahead Log file for concurrent writes and non-blocking reads. |
| `/data/logs.db-shm` | SQLite shared-memory index file for WAL checkpointing. |
| `/data/.secret_key` | Auto-generated 256-bit encryption key used to encrypt sensitive settings at rest (file mode `0600`). |
| `/data/presets/alerts/` | Directory for custom and community alert rule JSON definitions. |
| `/data/presets/drops/` | Directory for custom and community ingestion drop rule JSON definitions. |

> [!WARNING]
> Always host the `/data` directory on a local filesystem (such as ext4, btrfs, or zfs) or an SSD cache pool. Network mounts (NFS, SMB) and Unraid FUSE user shares (`/mnt/user/`) do not reliably support POSIX shared memory (`mmap`) or file locking required for SQLite WAL mode.
