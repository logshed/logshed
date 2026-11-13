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

**Yes/no values.** Every on/off variable below accepts `true`, `1`, `yes`, `on` or `enabled` for on and `false`, `0`, `no`, `off` or `disabled` for off, in any letter case. Any other value is ignored, so the next tier (or the default) applies.

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
      # Set TZ to your own time zone (for example Europe/London or America/New_York).
      # LogShed uses it for syslog timestamps that carry no zone, maintenance schedules and the daily digest.
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
| `PUID` | `1000` | Docker Compose only | User ID used for internal execution after privilege dropping via `gosu`. Use a non-root user: `0` (also `00` and so on) makes LogShed refuse to start, with an error that names the variable. The files in `/data` are handed to this user on start. New database files are created with `umask 027`, so they are not world-readable on the host bind mount. | 1.0.0 |
| `PGID` | `1000` | Docker Compose only | Group ID used for internal execution after privilege dropping via `gosu`. Use a non-root group: `0` makes LogShed refuse to start, like `PUID=0`. | 1.0.0 |
| `TZ` | `UTC` | Docker Compose only | Container timezone identifier (for example, `UTC`, `America/New_York`, `Europe/London`). | 1.0.0 |
| `APP_URL` | *(empty)* | Web UI or Compose | Public base URL of the LogShed instance (for example, `https://logs.example.com`), used for links in push alerts that open the alert's incident record. | 1.1.0 |
| `LOGSHED_INTERNAL_LOG_LEVEL` | `WARNING` | Web UI or Compose | Log level for LogShed internal diagnostic logging (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, or `DISABLED`). Logs from alerting, notifications and AI analysis are stored but never evaluated by alert rules, so a rule cannot trigger itself. | 1.1.0 |

**What `docker logs` shows, and what is stored.** LogShed prints its own messages at `INFO` and above (database migrations, listeners and workers starting and stopping, warnings and errors) to the container's console, beside uvicorn's lines. Each line has the time in the server's time zone (`TZ`), the level and the part of LogShed that wrote it, for example `2026-11-09 15:04:05 INFO     app.core.migrations: Migrating from 2 to 3`. `LOGSHED_INTERNAL_LOG_LEVEL` does not change what is printed: it sets which of these messages are also stored as logs (host `logshed`). The Docker collector never reads LogShed's own container (one named `logshed`, `log-shed` or `log_shed` in any letter case, or whose ID matches the container's host name, which Docker sets by default), so console lines are not stored a second time.

---

## Syslog Listener

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `SYSLOG_PORT` | `1514` | Docker Compose only | Port for both UDP and TCP syslog ingestion (valid range 1 to 65535). | 1.0.0 |
| `SYSLOG_MAX_TCP_CONNECTIONS` | `250` | Web UI or Compose | Maximum number of concurrent Syslog TCP client connections. | 1.1.0 |
| `SYSLOG_MAX_TCP_CONNECTIONS_PER_IP` | `20` | Docker Compose only | Maximum number of concurrent Syslog TCP connections from one source IP, so a single host cannot use every slot. Extra connections are refused with a warning. Values that are not a whole number of at least `1` fall back to `20`. Not shown in the web interface. | 1.3.0 |
| `SYSLOG_TCP_INACTIVITY_TIMEOUT` | `0.0` | Web UI or Compose | Inactivity timeout in seconds for Syslog TCP connections (`0.0` disables timeout, keeping persistent streams connected). | 1.1.0 |
| `SYSLOG_UDP_RECEIVE_BUFFER_BYTES` | `8388608` (8 MiB) | Docker Compose only | Receive buffer size requested for the UDP syslog socket, in bytes. `0` keeps the operating system default. The kernel caps the request at `net.core.rmem_max`; the startup log reports the size actually granted and logs a warning when it is smaller than requested. A value that is not a whole number from `0` to `2147483647` falls back to the default with a warning. Not shown in the web interface. | 1.3.0 |

**UDP can lose messages in bursts; TCP does not lose them in the kernel.** UDP syslog has no delivery guarantee. When a burst arrives faster than LogShed reads it, the kernel discards the datagrams that do not fit in the socket receive buffer, and neither the sender nor LogShed's own queue ever sees them. LogShed reports those as **UDP lost** in the navbar and as `udp_receive_errors` in `GET /api/health` and `GET /api/v1/system/metrics` (see [Sending Logs](SENDING_LOGS.md#udp-or-tcp-syslog)). TCP syslog does not lose messages this way, because TCP resends and slows the sender down instead of discarding. (If LogShed's own in-memory queue ever fills, new logs are dropped and counted in **Lost**; TCP syslog senders are first slowed down for up to 2 seconds.) Prefer TCP for sources that must not lose lines, and raise the buffer on the host for busy UDP sources:

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
| `ENABLE_DOCKER` | `true` | Web UI or Compose | Enable or disable log collection from the local Docker host (`DOCKER_HOST`, shown as "Primary Docker" in Settings > Sources; `true` or `false`). `false` keeps it off even if it is switched on in the Web UI. Other Docker hosts have their own switches and keep collecting. | 1.1.0 |
| `DOCKER_SOURCE_ALIAS` | `docker` | Web UI or Compose | Host or source name assigned to logs collected from Docker containers. | 1.1.0 |
| `DOCKER_EXCLUDE_CONTAINERS` | *(empty)* | Web UI or Compose | Comma-separated list of container names or container IDs to exclude from log tailing. | 1.1.0 |
| `DOCKER_READ_TIMEOUT` | `60.0` | Docker Compose only | Read timeout in seconds for streaming container log output from the Docker API. | 1.1.0 |
| `DOCKER_HEARTBEAT_INTERVAL` | `30.0` | Docker Compose only | Interval in seconds between Docker API connection health checks. | 1.1.0 |
| `DOCKER_SOCKET_POLL_INTERVAL` | `5.0` | Docker Compose only | Initial delay in seconds when polling for socket availability during startup or reconnect. | 1.1.0 |
| `DOCKER_SOCKET_POLL_MAX` | `60.0` | Docker Compose only | Maximum backoff duration in seconds for Docker socket reconnect attempts. | 1.1.0 |
| `DOCKER_DISCOVERY_INTERVAL` | `60.0` | Docker Compose only | Interval in seconds between periodic container discovery scans to detect newly started or stopped containers. | 1.2.0 |
| `DOCKER_ALLOWED_SOCKETS` | *(empty)* | Docker Compose only | Comma-separated list of extra Unix socket paths that Docker hosts may use. By default a `unix://` host URL must end in `docker.sock` or be the `DOCKER_HOST` socket; any other path is refused with `422` when you save the host. Stored hosts are not re-checked, so collection from an already saved host keeps running. Example: `/run/podman/podman.sock`. | 1.3.0 |
| `DOCKER_ADDITIONAL_HOSTS` | *(empty)* | Docker Compose only | JSON-encoded array of remote Docker endpoints to pre-seed on startup (for example, `[{"name": "remote-node", "url": "tcp://192.168.1.50:2375", "source_alias": "remote-docker"}]`). | 1.3.0 |

> [!WARNING]
> **Docker socket owned by group 0:** when the mounted Docker socket belongs to the `root` group (some Docker Desktop and rootful setups), the container adds its app user to the root group so it can read the socket, as in 1.2.x, and logs a warning at startup. Any process that can use the Docker socket can already control the host, so prefer a Docker socket proxy (such as `tecnativa/docker-socket-proxy`) with `DOCKER_HOST=tcp://proxy:2375` instead.

**TLS to remote Docker hosts:** each Docker host has a "Verify TLS host name" toggle (on by default). With it on, the daemon certificate must match the host name in the URL, so a host reached by IP address needs a certificate that lists that IP (an IP SAN). Turn the toggle off for such hosts. TLS to a host that is not on this machine also requires a CA certificate. Docker host URLs (and `DOCKER_HOST`) that point at link-local or cloud metadata addresses are refused, both when a host is saved and each time LogShed connects, so a host name that later resolves to such an address (DNS rebinding) is refused and an error naming the host is logged. A `DOCKER_ADDITIONAL_HOSTS` value that is not a valid JSON array of objects is ignored with a warning in the log, and so is each entry that is not an object or has no `name` or `url`. Hosts are added at start-up only when no Docker host of that name exists, so later changes in the web UI are kept.

---

## Storage and Retention

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `MAX_RETENTION_DAYS` | `30` | Docker Compose only | Maximum log retention in days (minimum `1`), and the ceiling of the retention slider in the web interface. While the variable is set, LogShed uses it as the retention itself: it is saved at startup and the slider is fixed to it. If you remove the variable later, the saved value stays until you change it on the **Storage** page, but retention never exceeds the default ceiling of 30 days. | 1.0.0 |
| `AUTO_BACKUP_ON_MIGRATION` | `true` | Docker Compose or Unraid template | Back up the database before an upgrade changes its schema (see [Database Backups](#database-backups)). Set to `false` to upgrade without a backup, for example when the disk has no room for one. | 1.4.0 |
| `DB_PATH` | *(empty)* | Docker Compose only | Custom SQLite database file path. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |
| `SECRET_KEY_PATH` | *(empty)* | Docker Compose only | Custom path to the persisted encryption key file. Supported for backward compatibility; prefer `DATA_DIR`. | 1.0.0 |

---

## AI Provider

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_AI_TIMEOUT` | `45.0` | Web UI or Compose | Outbound HTTP request timeout in seconds for AI analysis requests to Gemini, OpenAI, Anthropic, or OpenAI-compatible endpoints such as Ollama and vLLM. | 1.1.0 |
| `LOGSHED_AI_THINKING_LEVEL` | `low` | Web UI or Compose | How much reasoning models think before they answer: `off`, `low`, `medium` or `high`. Applies to Gemini 2.5 and later, Claude 3.7 and later, and OpenAI o-series and GPT-5 and later models; the Web UI setting (Settings > Advanced) wins over it. Claude 5 and later, Gemini 2.5 Pro and Gemini 3 and later still think a little at `off`, which asks them for their lowest level; and a model that refuses a level is asked again at `low` (or at its own default). | 1.3.0 |
| `LOGSHED_AI_THINKING_BUDGET` | *(empty)* | Compose | The token budget used up to 1.2.x, still read as a level when `LOGSHED_AI_THINKING_LEVEL` is unset: `0` is `off`, up to 2,048 `low`, up to 8,192 `medium`, above that `high`. | 1.1.0 |

### OpenAI-Compatible Providers

Choose **OpenAI-Compatible** in **Settings > Application** for any service or server that accepts OpenAI-style requests, then set its base URL (the preset list next to the field fills in the common ones) and its API key, if it needs one. With no base URL, LogShed uses `http://localhost:11434/v1`.

| Provider | Base URL | Notes |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | One key for models from many providers. Model IDs carry the provider name, for example `meta-llama/llama-3.3-70b-instruct`. LogShed identifies itself to OpenRouter as `LogShed` (`https://logshed.dev`); your own LogShed address is not sent. |
| Ollama | `http://host.docker.internal:11434/v1` | Ollama on the Docker host. No key needed. |
| LM Studio | `http://host.docker.internal:1234/v1` | LM Studio's server on the Docker host. No key needed. |
| Groq | `https://api.groq.com/openai/v1` | |
| Mistral | `https://api.mistral.ai/v1` | |
| DeepSeek | `https://api.deepseek.com` | |
| vLLM, LocalAI, llama.cpp | `http://<host>:<port>/v1` | Self-hosted servers on your network. |

On Linux, `host.docker.internal` works only when the LogShed service has `extra_hosts: ["host.docker.internal:host-gateway"]`; otherwise use the host's LAN address. Cloud metadata and link-local addresses are refused as base URLs; private and loopback addresses are allowed.

**Privacy with routers:** OpenRouter and similar routers pass each prompt (logs redacted by LogShed) on to whichever provider serves the chosen model, and that provider's data policy then applies. Check the router's privacy and data retention settings (on OpenRouter, in your account settings) before sending logs.

---

## Model Context Protocol (MCP)

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_ENABLE_MCP` | `false` | Web UI or Compose | Master switch to activate the integrated Model Context Protocol (MCP) server endpoints and tools (`true` or `false`). | 1.3.0 |
| `MCP_ALLOW_QUERY_TOKEN` | `false` | Docker Compose only | Accept the API token in the URL query string (`?token=`) on MCP routes (`true` or `false`). Off by default because URLs are written to reverse proxy access logs. Prefer the `Authorization: Bearer` header; turn this on only for MCP clients that cannot send headers and can only be given a URL. A warning is logged while a token in the URL is used. | 1.3.0 |

---

## Security

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `LOGSHED_SECRET_KEY` | *(auto-generated)* | Docker Compose only | 32-byte URL-safe base64 string for Fernet encryption of runtime settings at rest. Generated automatically at `/data/.secret_key` if unset. | 1.0.0 |
| `COOKIE_SECURE` | `false` | Web UI or Compose | Enforces the `Secure` flag on HTTP session cookies (`true` or `false`). Enable when running behind an HTTPS reverse proxy. | 1.1.0 |
| `TRUSTED_PROXIES` | *(empty)* | Web UI or Compose | Comma-separated list of reverse proxy IP addresses or CIDR blocks for client IP resolution via `X-Forwarded-For`. Recommended when LogShed runs behind a reverse proxy: set it to the proxy container IP (for example `TRUSTED_PROXIES=172.18.0.5`). A connection from loopback (`127.0.0.1` or `::1`) is always trusted, whatever this says. Only a proxy inside LogShed's network namespace connects from loopback: a sidecar sharing the container's network, or a proxy or tunnel on the host with `network_mode: host`. Such a proxy must add the client's address to `X-Forwarded-For` (as nginx's `$proxy_add_x_forwarded_for` does) or replace the header, never pass the client's own header on unchanged; otherwise a client can send a different address with each attempt to get past the per-address login limit. If yours cannot, have it connect to LogShed's LAN or bridge address instead of loopback, and list that address here. | 1.1.0 |
| `TRUST_DOCKER_PROXIES` | `false` | Web UI or Compose | Trusts every address in `172.16.0.0/12` to set `X-Forwarded-For` (`true` or `false`). If clients can reach LogShed through the Docker userland proxy or hairpin NAT, they appear as the bridge gateway and can spoof their address to avoid the per-address login limit. Prefer `TRUSTED_PROXIES=<reverse proxy container IP>`. LogShed logs a warning at startup while this option is in effect. | 1.1.0 |
| `ALLOW_PRIVATE_NOTIFICATION_TARGETS` | `true` | Web UI or Compose | Allow webhook and notification dispatches to RFC 1918 private subnets and local domain names (`true` or `false`). Set to `false` to restrict webhooks to public Internet addresses. Cloud metadata, link-local and loopback addresses are refused either way. LogShed checks the address it actually connects to, which stops DNS rebinding. With `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` set (any letter case), notifications go through that proxy, which may be on a loopback or private address. Destinations reached through the proxy are checked when the channel is saved and before each send, but DNS rebinding protection for them then depends on the proxy (LogShed logs this once at startup). Destinations listed in `NO_PROXY` keep the connect-time check. | 1.1.0 |

Login attempts are limited to 5 failures per client address per minute (IPv6 clients share one limit per /64), and at most 2 attempts from one address are checked at the same time. Each attempt is counted before the password is checked, so parallel guesses cannot get around the limit. Exceeding either limit returns `429` with a `Retry-After` header. A successful login clears the address's failure count.

**Behind a reverse proxy:** LogShed sends the web interface's files already compressed (Brotli or gzip, whichever the browser accepts) and lets browsers keep the versioned files under `/assets/` for a year, so the proxy does not need to compress them. API responses are not compressed. If your proxy compresses responses, it should leave the live streams (`text/event-stream`) alone; LogShed sends `X-Accel-Buffering: no` on them so nginx does not buffer them.

---

## Advanced

| Variable | Default | Where to Configure | Description | Since |
|---|---|---|---|:---:|
| `ENVIRONMENT` | `production` | Docker Compose only | Runtime mode (`production` or `development`). `development` adds the localhost development CORS origins (ports 5173, 8080 and 3000) and includes tracebacks in AI failure logs. Use this, not `DEBUG`, for a local frontend dev server. | 1.0.0 |
| `DEBUG` | `false` | Docker Compose only | Includes tracebacks in AI failure logs (`true` or `false`). Since 1.3.0 it no longer trusts localhost development origins; use `ENVIRONMENT=development` for that. | 1.0.0 |
| `CORS_ORIGINS` | *(empty)* | Docker Compose only | Comma-separated list of permitted origins for cross-origin resource sharing requests. List each trusted origin. A `*` entry is ignored and an error is logged, because with credentials it would let any website send logged-in requests. Cross-origin requests may send only the `Content-Type`, `X-Requested-With` and `Authorization` headers. | 1.0.0 |
| `ENABLE_API_DOCS` | `true` | Docker Compose only | Serves the interactive API documentation (`/docs`, `/redoc`) and `/openapi.json`. The pages need the web login (without one, `/docs` and `/redoc` redirect to the login page and `/openapi.json` returns `401`); `/openapi.json` also accepts any valid API token in `Authorization: Bearer`. Swagger UI and ReDoc are served by LogShed itself, so they work offline. Set to `false` to turn them off. | 1.3.0 |
| `LOGSHED_VERSION` | *(runtime build)* | Docker Compose only | Override the version string displayed in the web dashboard header. | 1.0.0 |
| `LOGSHED_IMAGE_REPO` | `logshed/logshed` | Docker Compose only | Container image repository checked for version updates. | 1.1.0 |
| `SQLITE_TMPDIR` | `/data/.tmp` | Docker Compose only | Folder for SQLite's temporary files (see [Data Persistence Paths](#data-persistence-paths)). The image sets it, and the container moves it beside the database when `DATA_DIR` or `DB_PATH` change. Set it only to put these files on another disk; the folder must exist and be writable by `PUID`, or SQLite falls back to `/var/tmp` inside the container. | 1.4.0 |

---

## Settings Configured Strictly in the Web UI

To safeguard tokens and credentials, the following features are managed exclusively through the web dashboard and cannot be populated via environment variables:

| Feature | Where to Find in Web UI | Details |
|---|---|---|
| **AI Provider & API Key** | **Settings > Application** | Select provider (Google Gemini, OpenAI, Anthropic Claude or OpenAI-Compatible, see [OpenAI-Compatible Providers](#openai-compatible-providers)), enter API key, and configure primary/fallback model tags. Stored encrypted at rest. |
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
| `/data/.secret_key` | Auto-generated 256-bit encryption key used to encrypt sensitive settings at rest (file mode `0600`). Anyone holding a backup of `/data` also holds this key. To keep the key out of `/data` backups, set `LOGSHED_SECRET_KEY` instead (see above) and store the value separately. It may be a symbolic link to a key file (a mounted secret, for example): LogShed reads the key through it. If the file exists but cannot be read, or is not a regular file (a dangling link or a directory, for example), LogShed stops at start-up with an error naming it rather than replace the key. An empty file is replaced, unless it is reached through a link: then LogShed stops with an error and leaves the link alone. |
| `/data/presets/alerts/` | Directory for custom and community alert rule JSON definitions. |
| `/data/presets/drops/` | Directory for custom and community ingestion drop rule JSON definitions. |
| `/data/backups/` | Database backups: one `.tar.gz` archive per backup, made before an upgrade changes the schema or by the `backup` command, and deleted after 30 days (see [Database Backups](#database-backups)). It sits beside the database (it follows `DATA_DIR` and `DB_PATH`). |
| `/data/.tmp/` | SQLite's temporary files: the copy of the database that **Compact Database** builds, and large sorts. They are deleted as soon as they are created, so the folder looks empty while they take space. It sits beside the database (it follows `DATA_DIR` and `DB_PATH`), so this space comes from the `/data` disk and not from the container's own filesystem (on Unraid, `docker.img`). |

**Free space for Compact Database:** compacting writes a copy of the database and then the whole database again into `logs.db-wal`, so it needs free space of about **twice the database's size** on the `/data` disk. LogShed checks before it starts and refuses with the figures if there is not enough. The extra space is released when it finishes.

> [!WARNING]
> Always host the `/data` directory on a local filesystem (such as ext4, btrfs, or zfs) or an SSD cache pool. Network mounts (NFS, SMB) and Unraid FUSE user shares (`/mnt/user/`) do not reliably support POSIX shared memory (`mmap`) or file locking required for SQLite WAL mode.

### Memory Shown for the Container

`docker stats`, the Unraid Docker page and similar tools count the file cache of `/data/logs.db` as the container's memory. After LogShed reads or writes much of a large database (a search over many days, a large prune, or deleting millions of logs), they can show several GiB while LogShed itself holds a few hundred MiB. In a test that deleted 4 million logs, LogShed's own memory stayed under 250 MiB while the figure shown rose to about 3 GiB. The kernel frees the cache as soon as anything else needs the memory, so it does not take memory from other containers. Compact Database also releases it (to under 400 MiB in that test). To see LogShed's own share, run `docker exec <container> grep -E '^(anon|file) ' /sys/fs/cgroup/memory.stat`: `anon` is LogShed's memory and `file` the cache, both in bytes.

### Backing Up `/data`

LogShed creates its files with `umask 027`: the `PUID` user can read and write them, the `PGID` group can read them, and other users on the host cannot. `.secret_key` is readable only by the `PUID` user.

- Backup tools that run as root, such as Unraid's Appdata Backup plugin or a root `rsync` or cron job, are not affected.
- A backup tool that runs as another user can read the database if that user is in the `PGID` group, or if the tool runs as the `PUID` user. To include `.secret_key`, run it as `PUID` or as root.
- Stop the container before copying, so `logs.db` and `logs.db-wal` are copied in a consistent state.

### Database Backups

LogShed backs up the database by itself before an upgrade changes its schema, and you can make a backup at any time with a command. Each backup is one file in `/data/backups/`:

- `logshed-backup-pre-v3-to-v4-20261113T120000Z.tar.gz`: made before upgrading from schema 3 to schema 4, at that time (UTC).
- `logshed-backup-manual-v4-20261113T120000Z.tar.gz`: made by the `backup` command, on schema 4.

An archive is a gzip-compressed tar that any archive tool opens (`tar -xzf <file>`). It holds `logs.db` and `manifest.json`, which records the LogShed version, the time, the schema the database was on, and for a backup before an upgrade the schema it was upgraded to. Backups are deleted 30 days after they were made, by the daily cleanup. Files in the folder with other names are left alone.

**Before an upgrade.** When a new version starts on a database with an older schema, it backs the database up first, then upgrades it. A large database takes a while: about 2 minutes for 20 GiB, with progress in the container log (`docker logs`) about every 10%. Do not restart the container while it runs. Upgrades that leave the stored logs as they are (the schema 4 upgrade in 1.4.0) back up everything except the logs, which takes a second or two. To upgrade without a backup, set `AUTO_BACKUP_ON_MIGRATION=false`.

**Free space.** A backup must leave at least 10% of the disk free (at least 2 GiB and at most 20 GiB), because `/data` often sits on a disk shared with other containers, whose databases fail when it fills. A backup before an upgrade needs about 30% of the database's size on top of that; a manual backup needs the database's size plus 30% while it runs. When there is not enough room, LogShed does not upgrade and does not start: the log says how much space it needs and how much is free. Free some space, or set `AUTO_BACKUP_ON_MIGRATION=false`, and start it again. A backup that was interrupted (the container stopped while it ran) leaves a `.tmp` file in `/data/backups/`, which is deleted after a day; the message names their size, and you can delete them when no backup is running. LogShed never deletes a backup to make room. Each backup keeps its space for 30 days: delete archives you no longer need from `/data/backups/`.

**Manual backup.** Run this while LogShed runs:

```
docker exec <container> python -m app.cli backup
```

It copies the database from a consistent snapshot while logging carries on, then compresses the copy and prints the archive's path, size and when it will be deleted. It is safe to run during a cleanup or a Compact, which only costs disk space. If there is not enough free space it stops and says so: stop the container and copy `/data` instead (see [Backing Up `/data`](#backing-up-data)). Run as root by `docker exec`, it hands the folder and the archive to the owner of `logs.db`, so LogShed can still manage them.

**What an archive holds.** Every stored log line, including anything in them that LogShed never redacts (redaction applies only to what is sent to an AI provider), and every setting and rule. Treat an archive like the database itself. The encryption key (`.secret_key`) is never in an archive: a backup restored into the same `/data` uses the key already there. To move to a new host, copy `.secret_key` yourself.

**Appdata backup tools** such as Unraid's Appdata Backup plugin copy `/data/backups/` too, which can double the size of that backup for a month. Exclude the `backups` folder in the tool if you do not need it twice.

**Going back to 1.3.x** leaves the folder as it is: 1.3.x does not delete old backups, so delete them by hand.

### Going back to an older version

An upgrade can change the database's schema (its structure). The schema version is stored in `logs.db`, and each LogShed release understands the schemas up to its own. LogShed 1.3.1 and later refuse to start on a database upgraded by a newer version, because running on it could fail in odd places or write data the newer version misreads. Nothing in the database is changed. The log shows an error such as:

```
The database at /data/logs.db was upgraded by a newer version of LogShed (schema 4); this version understands schema 3 at most. LogShed will not start, ...
```

With a restart policy (such as Docker's `unless-stopped`), the container starts, logs this error and exits again in a loop. That is expected: nothing is written while it does. To recover, do one of these:

- **Start the newer version again** (set the image tag back). Its data is all there.
- **Restore the backup LogShed made before the upgrade** (1.4.0 and later; see [Database Backups](#database-backups)). Stop the container. In `/data`, move `logs.db`, `logs.db-wal` and `logs.db-shm` aside together (into another folder), so the current database stays whole. Extract `logs.db` from the archive into `/data` (`tar -xzf backups/<archive> logs.db`). Then start the image tag whose schema matches the archive's `schema_version` in `manifest.json` (the `pre-v3-to-v4` in its name: schema 3 is LogShed 1.3.x). Keep `.secret_key` as it is. Logs and settings changed since the backup are lost; a backup made without the logs (its manifest lists `omitted_tables`) brings back the settings, rules and history, with no logs.
- **Restore a copy of `/data` made before the upgrade.** Stop the container, put the old files back (`logs.db` and, if you copied them, `logs.db-wal` and `logs.db-shm`; delete any newer `-wal` and `-shm` files), then start the older version. Logs and settings changed after that copy was made are lost. Keep `.secret_key` with the database: the stored secrets can only be read with the key they were encrypted with.

LogShed 1.4.0 and later make that backup for you. With an older version, or with `AUTO_BACKUP_ON_MIGRATION=false`, take a copy of `/data` before an upgrade you might want to undo (see [Backing Up `/data`](#backing-up-data)). An upgrade that does not change the schema (a patch release such as 1.3.0 to 1.3.1) can be undone by switching the image tag back.
