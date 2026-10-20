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
      # - TRUSTED_PROXIES=172.18.0.5   # your reverse proxy container IP (preferred over TRUST_DOCKER_PROXIES)
      # - TRUST_DOCKER_PROXIES=false
    volumes:
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

---

## Table of Contents

- [Core Settings](#core-settings)
- [Syslog Listener](#syslog-listener)
- [HTTP Ingest and Rule Patterns](#http-ingest-and-rule-patterns)
- [Docker Log Tailing](#docker-log-tailing)
- [Storage and Retention](#storage-and-retention)
- [AI Provider](#ai-provider)
- [Model Context Protocol (MCP)](#model-context-protocol-mcp)
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
| `PUID` | `1000` | Docker Compose only | User ID used for internal execution after privilege dropping via `gosu`. Use a non-root user: `0` still starts (LogShed then runs as root inside the container) and logs a warning at startup, but a later release will refuse to start with it. New database files are created with `umask 027`, so they are not world-readable on the host bind mount. | 1.0.0 |
| `PGID` | `1000` | Docker Compose only | Group ID used for internal execution after privilege dropping via `gosu`. Use a non-root group: `0` still starts and logs a warning at startup, but a later release will refuse to start with it. | 1.0.0 |
| `TZ` | `UTC` | Docker Compose only | Container timezone identifier (for example, `UTC`, `America/New_York`, `Europe/London`). | 1.0.0 |
| `APP_URL` | *(empty)* | Web UI or Compose | Public base URL of the LogShed instance (for example, `https://logs.example.com`), used for direct links in push alerts. | 1.1.0 |
| `LOGSHED_INTERNAL_LOG_LEVEL` | `WARNING` | Web UI or Compose | Log level for LogShed internal diagnostic logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, or `DISABLED`). Logs from alerting, notifications and AI analysis are stored but never evaluated by alert rules, so a rule cannot trigger itself. | 1.1.0 |

---

## Syslog Listener

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `SYSLOG_PORT` | `1514` | Docker Compose only | Port for both UDP and TCP syslog ingestion (valid range 1 to 65535). | 1.0.0 |
| `SYSLOG_MAX_TCP_CONNECTIONS` | `250` | Web UI or Compose | Maximum number of concurrent Syslog TCP client connections. | 1.1.0 |
| `SYSLOG_MAX_TCP_CONNECTIONS_PER_IP` | `20` | Docker Compose only | Maximum number of concurrent Syslog TCP connections from one source IP, so a single host cannot use every slot. Extra connections are refused with a warning. Values that are not a whole number of at least `1` fall back to `20`. Not shown in the web interface. | 1.3.0 |
| `SYSLOG_TCP_INACTIVITY_TIMEOUT` | `0.0` | Web UI or Compose | Inactivity timeout in seconds for Syslog TCP connections (`0.0` disables timeout, keeping persistent streams connected). | 1.1.0 |
| `SYSLOG_UDP_RECEIVE_BUFFER_BYTES` | `8388608` (8 MiB) | Docker Compose only | Receive buffer size requested for the UDP syslog socket, in bytes. `0` keeps the operating system default. The kernel caps the request at `net.core.rmem_max`; the startup log reports the size actually granted and logs a warning when it is smaller than requested. A value that is not a whole number from `0` to `2147483647` falls back to the default with a warning. Not shown in the web interface. | 1.3.0 |

**UDP can lose messages in bursts; TCP does not lose them in the kernel.** UDP syslog has no delivery guarantee. When a burst arrives faster than LogShed reads it, the kernel discards the datagrams that do not fit in the socket receive buffer, and neither the sender nor LogShed's own queue ever sees them. LogShed reports those as **UDP lost** in the navbar and as `udp_receive_errors` in `GET /api/health` and `GET /api/v1/system/metrics` (see [Sending Logs](SENDING_LOGS.md#udp-or-tcp-syslog)). TCP syslog does not lose messages this way, because TCP resends and slows the sender down instead of discarding. (If LogShed's own in-memory queue ever fills, new logs from any source are dropped and counted in **Dropped**.) Prefer TCP for sources that must not lose lines, and raise the buffer on the host for busy UDP sources:

```bash
sudo sysctl -w net.core.rmem_max=8388608                           # takes effect at once
echo "net.core.rmem_max=8388608" | sudo tee -a /etc/sysctl.conf    # keeps it after a reboot
```

Set this on the Docker host, not in the Compose file: Docker refuses `net.core.rmem_max` under `sysctls:` because it is not a per-container setting. On a default Linux host (`rmem_max` of 212992) LogShed logs a warning at every start that names the size it got; either raise `rmem_max` as above or set `SYSLOG_UDP_RECEIVE_BUFFER_BYTES` to that size to silence it.

---

## HTTP Ingest and Rule Patterns

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `INGEST_MAX_BODY_BYTES` | `5242880` (5 MiB) | Docker Compose only | Largest request body accepted by `POST /api/v1/ingest`, in bytes. Larger bodies are refused with `413`. A request may also hold at most 10,000 entries (`413`), `message` and `raw` are cut to 64 KB each, and `source_ip`, `source_alias` and `app_name` to 255 characters. Invalid or non-positive values fall back to the default. See [Sending Logs](SENDING_LOGS.md#http-ingest-api) for the full list of limits. | 1.3.0 |
| `USER_REGEX_TIMEOUT_MS` | `50` | Docker Compose only | Time limit in milliseconds for one match of a user-written alert or drop rule regular expression. A pattern that runs longer counts as no match and logs a warning naming the rule, so one bad pattern cannot freeze ingestion. A rule whose pattern times out 20 times within 5 minutes is switched off automatically (see the [Rules Guide](RULES_GUIDE.md)). Invalid or non-positive values fall back to `50`. | 1.3.0 |

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
| `DOCKER_DISCOVERY_INTERVAL` | `60.0` | Docker Compose only | Interval in seconds between periodic container discovery scans to detect newly started or stopped containers. | 1.3.0 |
| `DOCKER_ALLOWED_SOCKETS` | *(empty)* | Docker Compose only | Comma-separated list of extra Unix socket paths that Docker hosts may use. By default a `unix://` host URL must end in `docker.sock` or be the `DOCKER_HOST` socket; any other path is refused with `422` when you save the host. Stored hosts are not re-checked, so collection from an already saved host keeps running. Example: `/run/podman/podman.sock`. | 1.3.0 |
| `DOCKER_ADDITIONAL_HOSTS` | *(empty)* | Docker Compose only | JSON-encoded array of remote Docker endpoints to pre-seed on startup (for example, `[{"name": "remote-node", "url": "tcp://192.168.1.50:2375", "source_alias": "remote-docker"}]`). | 1.3.0 |

> [!WARNING]
> **Docker socket owned by group 0:** when the mounted Docker socket belongs to the `root` group (some Docker Desktop and rootful setups), the container adds its app user to the root group so it can read the socket, as in 1.2.x, and logs a warning at startup. Any process that can use the Docker socket can already control the host, so prefer a Docker socket proxy (such as `tecnativa/docker-socket-proxy`) with `DOCKER_HOST=tcp://proxy:2375` instead.

**TLS to remote Docker hosts:** each Docker host has a "Verify TLS host name" toggle (on by default). With it on, the daemon certificate must match the host name in the URL, so a host reached by IP address needs a certificate that lists that IP (an IP SAN). Turn the toggle off for such hosts. TLS to a host that is not on this machine also requires a CA certificate. Docker host URLs (and `DOCKER_HOST`) that point at link-local or cloud metadata addresses are refused, both when a host is saved and each time LogShed connects, so a host name that later resolves to such an address (DNS rebinding) is refused and an error naming the host is logged. A `DOCKER_ADDITIONAL_HOSTS` value that is not a valid JSON array of objects is ignored with a warning in the log.

---

## Storage and Retention

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `MAX_RETENTION_DAYS` | `30` | Docker Compose only | Maximum log retention in days (minimum `1`), and the ceiling of the retention slider in the web interface. While the variable is set, LogShed uses it as the retention itself: it is saved at startup and the slider is fixed to it. If you remove the variable later, the saved value stays until you change it on the **Storage** page, but retention never exceeds the default ceiling of 30 days. | 1.0.0 |
| `DB_PATH` | *(empty)* | Docker Compose only | Custom SQLite database file path. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |
| `SECRET_KEY_PATH` | *(empty)* | Docker Compose only | Custom path to the persisted encryption key file. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |

---

## AI Provider

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_AI_TIMEOUT` | `45.0` | Web UI or Compose | Outbound HTTP request timeout in seconds for AI analysis requests to Gemini, OpenAI, or Ollama/vLLM endpoints. | 1.1.0 |
| `LOGSHED_AI_THINKING_BUDGET` | `1024` | Web UI or Compose | Reasoning token budget for extended thinking models (for example, `gemini-2.5-pro` or compatible models; `0` disables). | 1.1.0 |

---

## Model Context Protocol (MCP)

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_ENABLE_MCP` | `false` | Web UI or Compose | Master switch to activate the integrated Model Context Protocol (MCP) server endpoints and tools (`true` or `false`). | 1.3.0 |
| `MCP_ALLOW_QUERY_TOKEN` | `false` | Docker Compose only | Accept the API token in the URL query string (`?token=`) on MCP routes (`true`, `1`, `yes` or `on`). Off by default because URLs are written to reverse proxy access logs. Prefer the `Authorization: Bearer` header; turn this on only for MCP clients that cannot send headers and can only be given a URL. A warning is logged while a token in the URL is used. | 1.3.0 |

---

## Security

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_SECRET_KEY` | *(auto-generated)* | Docker Compose only | 32-byte URL-safe base64 string for Fernet encryption of runtime settings at rest. Generated automatically at `/data/.secret_key` if unset. | 1.0.0 |
| `COOKIE_SECURE` | `false` | Web UI or Compose | Enforces the `Secure` flag on HTTP session cookies (`true` or `false`). Enable when running behind an HTTPS reverse proxy. | 1.1.0 |
| `TRUSTED_PROXIES` | *(empty)* | Web UI or Compose | Comma-separated list of reverse proxy IP addresses or CIDR blocks for client IP resolution via `X-Forwarded-For`. Recommended when LogShed runs behind a reverse proxy: set it to the proxy container IP (for example `TRUSTED_PROXIES=172.18.0.5`). | 1.1.0 |
| `TRUST_DOCKER_PROXIES` | `false` | Web UI or Compose | Trusts every address in `172.16.0.0/12` to set `X-Forwarded-For` (`true` or `false`). If clients can reach LogShed through the Docker userland proxy or hairpin NAT, they appear as the bridge gateway and can spoof their address to avoid the per-address login limit. Prefer `TRUSTED_PROXIES=<reverse proxy container IP>`. LogShed logs a warning at startup while this option is in effect. | 1.1.0 |
| `ALLOW_PRIVATE_NOTIFICATION_TARGETS` | `true` | Web UI or Compose | Allow webhook and notification dispatches to RFC 1918 private subnets and local domain names (`true` or `false`). Set to `false` to restrict webhooks to public Internet addresses. LogShed checks the address it actually connects to, which stops DNS rebinding. With `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` set (any letter case), notifications go through that proxy, which may be on a loopback or private address. Destinations reached through the proxy are checked when the channel is saved and before each send, but DNS rebinding protection for them then depends on the proxy (LogShed logs this once). Destinations listed in `NO_PROXY` keep the connect-time check. | 1.1.0 |

Login attempts are limited to 5 failures per client address per minute (IPv6 clients share one limit per /64), and at most 2 attempts from one address are checked at the same time. Each attempt is counted before the password is checked, so parallel guesses cannot get around the limit. Exceeding either limit returns `429` with a `Retry-After` header. A successful login clears the address's failure count.

---

## Advanced

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `ENVIRONMENT` | `production` | Docker Compose only | Runtime mode (`production` or `development`). `development` adds the localhost development CORS origins (ports 5173, 8080 and 3000) and includes tracebacks in AI failure logs. Use this, not `DEBUG`, for a local frontend dev server. | 1.0.0 |
| `DEBUG` | `false` | Docker Compose only | Includes tracebacks in AI failure logs (`true` or `false`). Since 1.3.0 it no longer trusts localhost development origins; use `ENVIRONMENT=development` for that. | 1.0.0 |
| `CORS_ORIGINS` | *(empty)* | Docker Compose only | Comma-separated list of permitted origins for cross-origin resource sharing requests. List each trusted origin. A `*` entry is ignored and an error is logged, because with credentials it would let any website send logged-in requests. Cross-origin requests may send only the `Content-Type`, `X-Requested-With` and `Authorization` headers. | 1.0.0 |
| `ENABLE_API_DOCS` | `true` | Docker Compose only | Serves the interactive API documentation (`/docs`, `/redoc`) and `/openapi.json`. The pages need the web login (without one, `/docs` and `/redoc` redirect to the login page and `/openapi.json` returns `401`); `/openapi.json` also accepts any valid API token in `Authorization: Bearer`. Swagger UI and ReDoc are served by LogShed itself, so they work offline. Set to `false` (or `0`, `no`, `off`) to turn them off. | 1.3.0 |
| `LOGSHED_VERSION` | *(runtime build)* | Docker Compose only | Override the version string displayed in the web dashboard header. | 1.0.0 |
| `LOGSHED_IMAGE_REPO` | `logshed/logshed` | Docker Compose only | Container image repository checked for version updates. | 1.1.0 |

---

## Settings Configured Strictly in the Web UI

To safeguard tokens and credentials, the following features are managed exclusively through the web dashboard and cannot be populated via environment variables:

| Feature | Where to Find in Web UI | Details |
|---|---|---|
| **AI Provider & API Key** | **Settings > Application** | Select provider (`Google Gemini` or `OpenAI / Custom`), enter API key, and configure primary/fallback model tags. Stored encrypted at rest. |
| **Notification Targets** | **Settings > Application** | Add Apprise webhook URLs (Discord, Pushover, Gotify, Telegram, Slack, Email). URLs with sensitive tokens are masked and stored encrypted. |
| **Active Retention Slider** | **Storage** page | Adjust the retention window for logs and alert history (1 to `MAX_RETENTION_DAYS`, default 14 days; fixed while `MAX_RETENTION_DAYS` is set). Lowering it shows what would be deleted and lets you delete now or at the next daily cleanup. |
| **Host Aliases** | **Settings > Host Aliases** | Map IP addresses (e.g. `192.168.1.1`) to friendly names (`router`). Applies dynamically to log streams. |
| **Alert Rules & Presets** | **Rules & History > Alert Rules** | Define threshold, pattern, or rate spike detection rules and link them to notification channels. |
| **Ingestion Drop Rules** | **Rules & History > Drop Rules** | Filter repetitive log noise in memory before database insertion and indexing. |
| **Maintenance Windows** | **Rules & History > Maintenance** | Set on-demand or recurring schedules to silence notifications during planned downtime. |
| **API Tokens & MCP Access** | **Settings > API & MCP Access** | Generate scoped Bearer tokens, toggle the Model Context Protocol (MCP) server, and inspect client configuration guides (Claude Desktop, Cursor, Antigravity). |

---

## Data Persistence Paths

All persistent application data is stored under the `/data` directory:

| Path | Description |
|---|---|
| `/data/logs.db` | Primary SQLite database holding ingested logs, alert rules, drop rules, and settings. |
| `/data/logs.db-wal` | SQLite Write-Ahead Log file for concurrent writes and non-blocking reads. |
| `/data/logs.db-shm` | SQLite shared-memory index file for WAL checkpointing. |
| `/data/.secret_key` | Auto-generated 256-bit encryption key used to encrypt sensitive settings at rest (file mode `0600`). Anyone holding a backup of `/data` also holds this key. To keep the key out of `/data` backups, set `LOGSHED_SECRET_KEY` instead (see above) and store the value separately. |
| `/data/presets/alerts/` | Directory for custom and community alert rule JSON definitions. |
| `/data/presets/drops/` | Directory for custom and community ingestion drop rule JSON definitions. |

> [!WARNING]
> Always host the `/data` directory on a local filesystem (such as ext4, btrfs, or zfs) or an SSD cache pool. Network mounts (NFS, SMB) and Unraid FUSE user shares (`/mnt/user/`) do not reliably support POSIX shared memory (`mmap`) or file locking required for SQLite WAL mode.
