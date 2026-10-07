# LogShed Configuration Reference

This guide explains how to configure LogShed, detailing the difference between container environment variables (in `docker-compose.yml`) and runtime settings managed through the web interface.

---

## Where Should I Configure Settings?

Configuration is divided into three distinct groups:

| Configuration Group | Where to Configure | Examples | Persisted In |
|---|---|---|---|
| **1. Container Bootstrap** | `docker-compose.yml` only | `PORT`, `SYSLOG_PORT`, `DATA_DIR`, `PUID`, `PGID`, `TZ`, `DOCKER_HOST` | Container environment |
| **2. Advanced System Settings** | Web UI (**Settings > Advanced**) OR `docker-compose.yml` | `LOGSHED_AI_TIMEOUT`, `TRUSTED_PROXIES`, `APP_URL`, `SYSLOG_MAX_TCP_CONNECTIONS` | SQLite database (UI) or environment fallback |
| **3. Secrets, Rules & Integrations** | Web UI only | AI API Keys, Notification Targets, Alert Rules, Ingestion Drop Rules, Host Aliases | SQLite database (encrypted at rest) |

---

## The Three-Tier Configuration Hierarchy

When LogShed reads an operational setting (such as timeouts, reverse proxy subnets, or connection limits), it checks values in this order:

1. **Web UI Database Setting (Tier 1)**: Settings saved in the web dashboard (**Settings > Application** or **Settings > Advanced**) take highest precedence. These apply immediately without container restarts.
2. **Environment Variable Fallback (Tier 2)**: If no database setting exists, LogShed falls back to the environment variable defined in your `docker-compose.yml`.
3. **Hardcoded Default (Tier 3)**: If neither the web UI nor the environment specifies a value, LogShed uses its built-in default.

> [!TIP]
> **Can all UI settings be set in Docker Compose?**
> **No.** Sensitive credentials (such as your Google Gemini or OpenAI API keys and Apprise notification webhook URLs) are intentionally excluded from environment variables. This prevents API tokens and secrets from leaking into `docker inspect`, container process tables, or shared compose files. Enter these once in the web UI where they are encrypted at rest with Fernet cryptography.

---

## Example docker-compose.yml

Here is a typical `docker-compose.yml` showing the essential bootstrap variables and optional environment overrides:

```yaml
services:
  logshed:
    image: ghcr.io/logshed/logshed:latest
    container_name: logshed
    restart: unless-stopped
    ports:
      - "8080:8080"        # Web Dashboard and REST API
      - "1514:1514/udp"    # Syslog UDP Ingestion (or 514:1514/udp)
      - "1514:1514/tcp"    # Syslog TCP Ingestion (or 514:1514/tcp)
    environment:
      # --- Required Bootstrap Settings ---
      - PUID=1000
      - PGID=1000
      - TZ=UTC
      - DOCKER_HOST=unix:///var/run/docker.sock

      # --- Optional Defaults (also configurable in Web UI) ---
      # - APP_URL=https://logs.example.com
      # - LOGSHED_INTERNAL_LOG_LEVEL=WARNING
      # - LOGSHED_AI_TIMEOUT=45.0
      # - TRUST_DOCKER_PROXIES=false
    volumes:
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

---

## Table of Contents

- [Core Settings](#core-settings)
- [Syslog Listener](#syslog-listener)
- [Docker Log Tailing](#docker-log-tailing)
- [Storage and Retention](#storage-and-retention)
- [AI Provider](#ai-provider)
- [Security](#security)
- [Advanced](#advanced)
- [Settings Configured Strictly in the Web UI](#settings-configured-strictly-in-the-web-ui)
- [Data Persistence Paths](#data-persistence-paths)

---

## Core Settings

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `PORT` | `8080` | Docker Compose only | HTTP listening port for the web dashboard and REST API. | 1.0.0 |
| `DATA_DIR` | `/data` | Docker Compose only | Path to persistent storage directory for the SQLite database, encryption key, and custom rule presets. | 1.0.0 |
| `PUID` | `1000` | Docker Compose only | User ID used for internal execution after privilege dropping via `gosu`. | 1.0.0 |
| `PGID` | `1000` | Docker Compose only | Group ID used for internal execution after privilege dropping via `gosu`. | 1.0.0 |
| `TZ` | `UTC` | Docker Compose only | Container timezone identifier (for example, `UTC`, `America/New_York`, `Europe/London`). | 1.0.0 |
| `APP_URL` | *(empty)* | Web UI or Compose | Public base URL of the LogShed instance (for example, `https://logs.example.com`), used for direct links in push alerts. | 1.1.0 |
| `LOGSHED_INTERNAL_LOG_LEVEL` | `WARNING` | Web UI or Compose | Log level for LogShed internal diagnostic logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, or `DISABLED`). | 1.1.0 |

---

## Syslog Listener

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `SYSLOG_PORT` | `1514` | Docker Compose only | Port for both UDP and TCP syslog ingestion (valid range 1 to 65535). | 1.0.0 |
| `SYSLOG_MAX_TCP_CONNECTIONS` | `250` | Web UI or Compose | Maximum number of concurrent Syslog TCP client connections. | 1.1.0 |
| `SYSLOG_TCP_INACTIVITY_TIMEOUT` | `0.0` | Web UI or Compose | Inactivity timeout in seconds for Syslog TCP connections (`0.0` disables timeout, keeping persistent streams connected). | 1.1.0 |

---

## Docker Log Tailing

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker Compose only | Docker daemon endpoint (`unix:///path/to/socket` or `tcp://host:port`). Set to `none` or `disabled` to deactivate Docker log tailing. | 1.0.0 |
| `ENABLE_DOCKER` | `true` | Web UI or Compose | Enable or disable Docker container log collection (`true` or `false`). | 1.1.0 |
| `DOCKER_SOURCE_ALIAS` | `docker` | Web UI or Compose | Host or source name assigned to logs collected from Docker containers. | 1.1.0 |
| `DOCKER_EXCLUDE_CONTAINERS` | *(empty)* | Web UI or Compose | Comma-separated list of container names or container IDs to exclude from log tailing. | 1.1.0 |
| `DOCKER_READ_TIMEOUT` | `60.0` | Docker Compose only | Read timeout in seconds for streaming container log output from the Docker API. | 1.1.0 |
| `DOCKER_HEARTBEAT_INTERVAL` | `30.0` | Docker Compose only | Interval in seconds between Docker API connection health checks. | 1.1.0 |
| `DOCKER_SOCKET_POLL_INTERVAL` | `5.0` | Docker Compose only | Initial delay in seconds when polling for socket availability during startup or reconnect. | 1.1.0 |
| `DOCKER_SOCKET_POLL_MAX` | `60.0` | Docker Compose only | Maximum backoff duration in seconds for Docker socket reconnect attempts. | 1.1.0 |

---

## Storage and Retention

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `MAX_RETENTION_DAYS` | `30` | Docker Compose only | Hard ceiling in days for the log retention slider in the web interface (minimum `1`). | 1.0.0 |
| `DB_PATH` | *(empty)* | Docker Compose only | Custom SQLite database file path. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |
| `SECRET_KEY_PATH` | *(empty)* | Docker Compose only | Custom path to the persisted encryption key file. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |

---

## AI Provider

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_AI_TIMEOUT` | `45.0` | Web UI or Compose | Outbound HTTP request timeout in seconds for AI analysis requests to Gemini, OpenAI, or Ollama/vLLM endpoints. | 1.1.0 |
| `LOGSHED_AI_THINKING_BUDGET` | `1024` | Web UI or Compose | Reasoning token budget for extended thinking models (for example, `gemini-2.5-pro` or compatible models; `0` disables). | 1.1.0 |

---

## Security

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_SECRET_KEY` | *(auto-generated)* | Docker Compose only | 32-byte URL-safe base64 string for Fernet encryption of runtime settings at rest. Generated automatically at `/data/.secret_key` if unset. | 1.0.0 |
| `COOKIE_SECURE` | `false` | Web UI or Compose | Enforces the `Secure` flag on HTTP session cookies (`true` or `false`). Enable when running behind an HTTPS reverse proxy. | 1.1.0 |
| `TRUSTED_PROXIES` | *(empty)* | Web UI or Compose | Comma-separated list of reverse proxy IP addresses or CIDR blocks for client IP resolution via `X-Forwarded-For`. | 1.1.0 |
| `TRUST_DOCKER_PROXIES` | `false` | Web UI or Compose | Automatically trust Docker bridge subnets (`172.16.0.0/12`) as reverse proxies (`true` or `false`). | 1.1.0 |
| `ALLOW_PRIVATE_NOTIFICATION_TARGETS` | `true` | Web UI or Compose | Allow webhook and notification dispatches to RFC 1918 private subnets and local domain names (`true` or `false`). Set to `false` to restrict webhooks to public Internet addresses. | 1.1.0 |

---

## Advanced

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `ENVIRONMENT` | `production` | Docker Compose only | Runtime mode (`production` or `development`). Enables debug logging and development CORS origins when set to `development`. | 1.0.0 |
| `DEBUG` | `false` | Docker Compose only | Enable debug mode and development settings (`true` or `false`). | 1.0.0 |
| `CORS_ORIGINS` | *(empty)* | Docker Compose only | Comma-separated list of permitted origins for cross-origin resource sharing requests. | 1.0.0 |
| `LOGSHED_VERSION` | *(runtime build)* | Docker Compose only | Override the version string displayed in the web dashboard header. | 1.0.0 |
| `LOGSHED_IMAGE_REPO` | `logshed/logshed` | Docker Compose only | Container image repository checked for version updates. | 1.1.0 |

---

## Settings Configured Strictly in the Web UI

To safeguard tokens and credentials, the following features are managed exclusively through the web dashboard and cannot be populated via environment variables:

| Feature | Where to Find in Web UI | Details |
|---|---|---|
| **AI Provider & API Key** | **Settings > Application** | Select provider (`Google Gemini` or `OpenAI / Custom`), enter API key, and configure primary/fallback model tags. Stored encrypted at rest. |
| **Notification Targets** | **Settings > Application** | Add Apprise webhook URLs (Discord, Pushover, Gotify, Telegram, Slack, Email). URLs with sensitive tokens are masked and stored encrypted. |
| **Active Retention Slider** | **Settings > Application** | Adjust retained log window (1 to `MAX_RETENTION_DAYS`, default 14 days). |
| **Host Aliases** | **Settings > Host Aliases** | Map IP addresses (e.g. `192.168.1.1`) to friendly names (`router`). Applies dynamically to log streams. |
| **Alert Rules & Presets** | **Rules & History > Alert Rules** | Define threshold, pattern, or rate spike detection rules and link them to notification channels. |
| **Ingestion Drop Rules** | **Rules & History > Drop Rules** | Filter repetitive log noise in memory before database insertion and indexing. |
| **Maintenance Windows** | **Rules & History > Maintenance** | Set on-demand or recurring schedules to silence notifications during planned downtime. |

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
