<p align="center">
  <img src="assets/logshed-logo.png" alt="LogShed Logo" width="180">
</p>

<h1 align="center">LogShed</h1>

<p align="center">
  <strong>A lightweight, self-hosted homelab log aggregator and syslog server featuring real-time streaming, fast SQLite FTS5 search, and user-directed AI incident analysis.</strong>
</p>

<p align="center">
  <a href="https://github.com/BenHornerTech/logshed"><img src="https://img.shields.io/badge/version-1.2.0-blue?style=flat-square" alt="Version 1.2.0"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/BenHornerTech/logshed/pkgs/container/logshed"><img src="https://img.shields.io/badge/container-ghcr.io-blue?logo=docker&logoColor=white&style=flat-square" alt="GHCR Container"></a>
  <a href="#prerequisites--system-requirements"><img src="https://img.shields.io/badge/arch-amd64%20%7C%20arm64-blueviolet?style=flat-square" alt="Multi-Arch Support"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React 19"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-003B57?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite FTS5"></a>
</p>

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Screenshots](#screenshots)
- [Built With](#built-with)
- [Prerequisites & System Requirements](#prerequisites--system-requirements)
- [Installation & Deployment](#installation--deployment)
 - [Docker Compose](#docker-compose-recommended)
 - [Unraid Setup](#unraid-installation)
 - [Docker Run](#generic-docker-run)
 - [Initial Setup](#initial-setup--authentication)
- [Configuration Reference](#configuration-reference)
  - [Environment Variables](#environment-variables)
  - [Runtime Settings (Web UI)](#runtime-settings-web-ui)
  - [Data Persistence & Storage Paths](#data-persistence--storage-paths)
  - [Internal Diagnostic Logging](#internal-diagnostic-logging)
  - [Secret Redaction & Raw Log Fidelity](#secret-redaction--raw-log-fidelity)
- [Real-Time Alerts & Security Canaries](#real-time-alerts--security-canaries)
- [Universal Notification Targets](#universal-notification-targets)
- [Ingestion Drop Rules & Saved Views](#ingestion-drop-rules--saved-views)
- [AI Incident Diagnosis](#ai-incident-diagnosis)
- [Local Development](#local-development)
- [Licensing](#licensing)

---

## Overview

Most logging stacks (such as Grafana Loki or the ELK stack) target large production clusters. Running them on a home server often takes several gigabytes of RAM and significant setup time just to collect logs from a router, a couple of virtual machines, and a handful of containers.

LogShed is a compact, self-hosted log hub designed for home labs and personal servers. It ingests standard syslog traffic and Docker container output into a single local SQLite database with full-text search (FTS5), providing a quick search interface and optional AI error diagnosis when you need help reading a stack trace.

> [!NOTE]
> LogShed is intended for home networks and personal self-hosted environments. It is not built for multi-tenant companies or high-throughput enterprise infrastructure. It started as a personal tool and is shared here in case others find it helpful.

---

## Features

- **Single container, single process**: The main thread runs FastAPI and monitored async workers. There is no separate database process, Redis instance, or message broker to run or maintain.
- **Low memory usage**: Idles at roughly 150 MB to 250 MB of RAM under normal home lab traffic.
- **Dual ingestion**:
  - **Syslog**: Listens on port 1514 (UDP and TCP) for RFC 3164 and RFC 5424 formats, supporting both octet-counted and newline-delimited TCP framing.
  - **Docker Engine API**: Tails local containers via `/var/run/docker.sock` or remote hosts via TCP proxy without extra dependencies.
- **Multiline stream assembly**: Groups multi-line exceptions (such as Python tracebacks or Java stack traces) by source stream within a short buffer window so related lines stay together.
- **Real-time alert engine**: Evaluates incoming logs in memory with sliding window threshold rules or immediate pattern matches, cooldown flap dampening, and optional automated AI incident diagnosis.
- **Pre-packaged security canary rules**: 1-click rules to catch SSH brute-force attempts, reverse-proxy authentication floods, sudo privilege escalation, and kernel out-of-memory (OOM) events.
- **Universal notification dispatcher**: Native delivery across 80+ notification services via Apprise and webhooks (Pushover, Discord, Telegram, Gotify, Ntfy, Slack, Email) with SSRF safeguards.
- **Ingestion drop rules**: Discard repetitive chatter and noisy log patterns in memory before database insertion and FTS5 indexing, complete with live drop counters and 1-click rule generation from log detail modals.
- **Saved filter views & URL sync**: Save and pin filter views directly on the console and mobile filter drawer with bidirectional URL query parameter sync for easy bookmarking and sharing.
- **Full-text search (SQLite FTS5)**: Fast prefix search across hosts, container names, log content, and severity tags, decoupled into a background indexing worker with sub-second catch-up latency.
- **Optional AI diagnosis**: Select log rows in the web UI to request an explanation and suggested fixes from Google Gemini, OpenAI, or a local model (Ollama / vLLM). API requests are strictly manual, and sensitive values (passwords, tokens, keys) are stripped before dispatch.
- **Host aliases**: Map IP addresses to friendly names (for example, `192.168.1.1` to `router`), which automatically apply across existing records.
- **Automatic retention**: Purges older logs in the background on a schedule (default: 14 days) and reclaims SQLite storage space without taking the database offline.
- **Security**: Runs as a non-root user (`PUID`/`PGID`), hashes passwords with Argon2id, encrypts stored settings with Fernet, uses secure session cookies, and includes a command-line password reset script.

---

## Screenshots

| Live Log Stream | Filtered Search & Quick Filters |
| :---: | :---: |
| <a href="assets/logshed-logstream.png"><img src="assets/logshed-logstream.png" width="450" alt="Live Console View"/></a> | <a href="assets/logshed-filter.png"><img src="assets/logshed-filter.png" width="450" alt="Filtered Search View"/></a> |
| *Real-time streaming console with smooth scrolling and pause controls* | *Quick multi-host, container, severity, and regex search filters* |
| **Log Detail & Surrounding Context** | **Host Alias Manager** |
| <a href="assets/logshed-log-detail.png"><img src="assets/logshed-log-detail.png" width="450" alt="Log Detail"/></a> | <a href="assets/logshed-host-aliases.png"><img src="assets/logshed-host-aliases.png" width="450" alt="Host Alias Manager"/></a> |
| *Structured field inspection, raw payloads, and adjacent log lines* | *Friendly hostname mappings for routers, switches, and bare-metal nodes* |
| **Targeted AI Investigation** | **AI Root-Cause Diagnosis** |
| <a href="assets/logshed-ai-ondemand.png"><img src="assets/logshed-ai-ondemand.png" width="450" alt="Targeted AI Investigation"/></a> | <a href="assets/logshed-ai-analysis.png"><img src="assets/logshed-ai-analysis.png" width="450" alt="AI Root-Cause Diagnosis"/></a> |
| *Select logs, add context, and ask the AI questions with automatic secret masking* | *Get a clear breakdown of what went wrong with step-by-step fix commands* |
| **AI Provider & Model Settings** | **Mobile Responsive Console** |
| <a href="assets/logshed-ai-config.png"><img src="assets/logshed-ai-config.png" width="450" alt="AI Provider & Model Settings"/></a> | <a href="assets/logshed-mobile.png"><img src="assets/logshed-mobile.png" height="467" alt="Mobile Responsive Console"/></a> |
| *Configure Gemini, OpenAI, or local Ollama with token and rate limits* | *Touch-friendly console interface built for monitoring on phones and tablets* |

---

## Built With

LogShed was designed and built using AI models from **Google Gemini** and **Anthropic Claude**.

### Architecture & Tech Stack

| Layer | Technologies |
|---|---|
| **Backend Runtime** | Python 3.12 (`asyncio`), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/) (single-worker process model) |
| **Storage & Search** | Standard Library `sqlite3` + `asyncio.to_thread()`, WAL mode, FTS5 external content virtual table |
| **Frontend UI** | [React 19](https://react.dev/), [Vite](https://vitejs.dev/), [Tailwind CSS](https://tailwindcss.com/), [@tanstack/react-virtual](https://tanstack.com/virtual), [Recharts](https://recharts.org/), [Lucide React](https://lucide.dev/) |
| **Collector Integrations** | [HTTPX](https://www.python-httpx.org/) (Docker Engine API over UDS and TCP), Async UDP/TCP Syslog server |
| **Alerting & Notifications** | [Apprise](https://github.com/caronc/apprise) (multi-channel alerts and webhooks) |
| **AI Integrations** | Google GenAI SDK (`google-genai`), OpenAI SDK (`openai` compatible with Ollama/vLLM/LocalAI) |
| **Security & Cryptography** | `argon2-cffi` (password hashing), `cryptography.fernet` (runtime settings encryption) |
| **Packaging & Base** | Multi-stage Docker build, `python:3.12-slim`, `tini` init, `gosu` privilege dropping |

For complete technical schemas, database structures, FTS5 triggers, and REST/SSE API specifications, see [docs/SPEC.md](docs/SPEC.md).

---

## Prerequisites & System Requirements

### Hardware Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| **RAM** | 256 MB | 512 MB - 1 GB (handles heavy burst ingestion and large browser buffers) |
| **CPU** | 1 vCPU / core | 1 - 2 cores (handles continuous FTS5 indexing and log stream parsing) |
| **Storage** | 1 GB free space | Direct SSD / NVMe cache pool (high IOPS for SQLite WAL checkpoints) |

> [!WARNING]
> **Storage & Filesystem Notice for SQLite WAL Mode:**
> Always host the `/data` directory on a local filesystem (ext4, btrfs, zfs) or a direct SSD cache pool. **Do not place `/data` on network mounts (NFS, SMB) or Unraid user shares (`/mnt/user/`)**, as these do not reliably support POSIX file locking or shared memory (`mmap`) required for concurrent SQLite WAL checkpoints.

### Software Dependencies

- **Docker Engine**: 20.10+
- **Docker Compose**: v2.0+ (or Unraid OS 6.9+)
- **Supported Architectures**:
 - `linux/amd64` (Standard x86_64 servers and PCs)
 - `linux/arm64` (Raspberry Pi 4/5, Apple Silicon VMs, ARM64 homelab boards)

---

## Installation & Deployment

### Docker Compose (Recommended)

Save the following as `docker-compose.yml`:

```yaml
services:
  logshed:
    image: ghcr.io/benhornertech/logshed:latest
    container_name: logshed
    restart: unless-stopped
    ports:
     - "8080:8080"        # Web Dashboard and REST API
     - "1514:1514/udp"    # Syslog UDP Ingestion
     - "1514:1514/tcp"    # Syslog TCP Ingestion
    environment:
     - PUID=1000
     - PGID=1000
     - TZ=UTC
     - PORT=8080
     - SYSLOG_PORT=1514
     - DOCKER_HOST=unix:///var/run/docker.sock
    volumes:
     - ./data:/data
     - /var/run/docker.sock:/var/run/docker.sock:ro
```

Deploy and start the service:

```bash
docker compose up -d
```

---

### Unraid Installation

LogShed provides an official Unraid Community Applications template in [`unraid-template.xml`](unraid-template.xml).

#### Setup Steps on Unraid:

1. **Add Template**:
  - Copy `unraid-template.xml` to `/boot/config/plugins/dockerMan/templates-user/my-LogShed.xml` on your Unraid flash drive, or add it via Community Applications when published.
2. **Configure Storage Path (`/data`)**:
  - Set container path `/data` to your SSD cache pool:
     ```text
     /mnt/cache/appdata/logshed
     ```
     *(or `/mnt/<pool_name>/appdata/logshed`)*
   
   > [!CAUTION]
   > **Avoid `/mnt/user/appdata/logshed`**:
   > Unraid `/mnt/user/` paths route through the `shfs` FUSE layer. FUSE does not reliably support POSIX shared memory (`mmap`) or SQLite advisory locking during WAL checkpoints. Pointing directly to your cache pool (`/mnt/cache/...`) guarantees native POSIX locking, protects your database from corruption, and prevents spinning up parity array disks.

3. **Configure Docker Socket**:
  - Map `/var/run/docker.sock` to `/var/run/docker.sock` with **Read-Only (`:ro`)** access to automatically discover and tail containers running on your Unraid server.
4. **Ports**:
  - Ensure `8080` (Web UI), `1514/udp` (Syslog UDP), and `1514/tcp` (Syslog TCP) are mapped to available host ports.
5. **Permissions**:
  - Unraid default user permissions are typically `PUID=99` and `PGID=100` (`nobody:users`). LogShed entrypoint will automatically adjust file ownership on `/data`.

---

### Generic Docker Run

For a quick standalone deployment without Docker Compose:

```bash
docker run -d \
  --name logshed \
  --restart unless-stopped \
  -p 8080:8080 \
  -p 1514:1514/udp \
  -p 1514:1514/tcp \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=UTC \
  -e DOCKER_HOST=unix:///var/run/docker.sock \
  -v /path/to/appdata:/data \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/benhornertech/logshed:latest
```

---

### Initial Setup & Authentication

1. Open your browser and navigate to `http://<YOUR_SERVER_IP>:8080`.
2. On first run, LogShed prompts you to set an **Administrator Password**.
3. Passwords are saved with **Argon2id** hashing. Once created, the setup endpoint locks permanently (`403 Forbidden`).
4. Sessions authenticate using cryptographically signed, HTTP-only, `SameSite=Lax` cookies.

#### Emergency Password Reset (CLI)
If you forget your administrator password, run the built-in password reset CLI tool inside the running container:

```bash
docker exec -it logshed python -m app.cli reset-admin --password "your_new_secure_password"
```

---

## Configuration Reference

### Environment Variables (Bootstrap Primitives)

Environment variables are strictly reserved for bootstrap primitives required before the SQLite database unlocks or privilege reduction occurs. Operational limits, collector exclusions, reverse proxy subnets, AI thinking budget/timeouts, and webhook URLs are configured directly within the web UI (**Settings > Advanced**) with instant live updates:

| Variable | Description | Default | Required? |
|---|---|---|:---:|
| `PORT` | Listening HTTP port for the web dashboard and REST API. | `8080` | No |
| `SYSLOG_PORT` | Listening port for both UDP and TCP syslog ingestion (1-65535). | `1514` | No |
| `DATA_DIR` | Mount path for persistent SQLite database, master key, and storage. | `/data` | No |
| `PUID` | User ID for internal non-root execution via `gosu`. | `1000` | No |
| `PGID` | Group ID for internal non-root execution via `gosu`. | `1000` | No |
| `TZ` | Container timezone (for example: `UTC`, `America/New_York`, `Europe/London`). | `UTC` | No |
| `DOCKER_HOST` | Docker daemon endpoint (`unix:///var/run/docker.sock` or `tcp://host:port`). Set to `none` or `disabled` to skip Docker collection. | `unix:///var/run/docker.sock` | No |
| `LOGSHED_SECRET_KEY` | Optional 32-byte URL-safe base64 key for encrypting runtime settings at rest. If unset, one is created at `/data/.secret_key`. | *(auto-generated)* | No |
| `MAX_RETENTION_DAYS` | Hard ceiling in days for the log retention slider in settings (minimum `1`). | `30` | No |
| `ENVIRONMENT` / `DEBUG` | Development origin and debug mode toggle (`production` / `false`). | `production` / `false` | No |
| `CORS_ORIGINS` | Comma-separated origins permitted for cross-origin requests (empty in production). | *(empty)* | No |

*Storage Compatibility Note:* `DB_PATH` and `SECRET_KEY_PATH` remain supported for backward compatibility, though standard deployments rely on `DATA_DIR`.

---

### Runtime Settings (Web UI)

To protect credentials from leaking into environment dumps or process listings, sensitive runtime settings and operational limits are configured through the **Settings** view in the web interface. Credentials are encrypted at rest using **AES-128-CBC / HMAC-SHA256 (Fernet)**, and all settings resolve through a three-tier hierarchy (Web UI database setting > Container environment variable fallback > Hardcoded default).

#### Application Settings (**Settings > Application**)
- **AI Provider**: `Google Gemini` or `OpenAI / Custom OpenAI-Compatible`
- **AI API Key**: Stored encrypted; masked in the UI
- **AI Model**: e.g., `gemini-3.7-flash`, `gpt-4o`, or local model tag like `llama3.2`
- **AI Fallback Models**: Comma-separated secondary models for automatic failover during rate limits or timeouts
- **Custom AI Base URL**: Optional endpoint for self-hosted LLMs (e.g., `http://192.168.1.50:11434/v1` for Ollama or vLLM)
- **AI System Instructions**: Editable instructions guiding root-cause analysis role and structure
- **Notification Channels**: Universal notification endpoints and webhooks via Apprise URL schemes, with live delivery testing, token masking, and encrypted URL storage
- **Ingestion Drop Rules**: Filtering criteria (host, application, pattern) to discard noise before database persistence, with live drop counters and interactive test modal
- **Active Log Retention**: Slider ranging from 1 to `MAX_RETENTION_DAYS` (default: 14 days)
- **Internal Log Level**: Runtime dropdown to configure LogShed diagnostic log capture without restart
- **Automated Update Checks**: Toggle to check GitHub Container Registry for new releases

#### Advanced System Settings (**Settings > Advanced**)
Located directly above the Change Admin Password section, these operational settings apply dynamically to in-memory workers without container recreation:
- **AI Engine Limits**: Outbound AI request timeout (seconds) and reasoning token budget for extended thinking models.
- **Notifications & Webhooks**: Public instance URL (`app_url`) used for direct incident history links in push alerts, and toggle for private / LAN webhook targets.
- **Docker Collector**: Toggle container log tailing, specify excluded container names/IDs, and configure source attribution alias.
- **Network & Reverse Proxy**: Comma-separated list of trusted proxy IPs/CIDRs, toggle to automatically trust Docker bridge networks (`172.16.0.0/12`), and toggle to force secure session cookies behind SSL proxies.
- **Syslog TCP Limits**: Maximum concurrent TCP connections and client inactivity timeout (seconds).
- **Host Aliases**: IP-to-name mappings to give readable names to homelab devices (managed in the **Host Aliases** tab).

---

### Data Persistence & Storage Paths

All persistent state resides in the `/data` volume:

| File Path | Description |
|---|---|
| `/data/logs.db` | Primary SQLite database containing log entries, host aliases, storage metrics, and encrypted settings. |
| `/data/logs.db-wal` | SQLite Write-Ahead Log (WAL) for high-concurrency ingestion and non-blocking reads. |
| `/data/logs.db-shm` | SQLite shared memory index for WAL tracking. |
| `/data/.secret_key` | 256-bit encryption key used to encrypt and decrypt sensitive runtime settings at rest (mode `0600`). |

---

### Internal Diagnostic Logging

LogShed monitors its own health by recording internal warnings and errors into its database under source alias `logshed` (such as `logshed/syslog` or `logshed/main`).
- By default, events at or above `LOGSHED_INTERNAL_LOG_LEVEL=WARNING` are recorded.
- Ingestion pipeline workers, database writers, and SSE subscribers use re-entrancy protection to eliminate self-logging feedback loops.
- Set `LOGSHED_INTERNAL_LOG_LEVEL=DISABLED` if you wish to deactivate internal logging.

---

### Secret Redaction & Raw Log Fidelity

- **Sensitive Token Redaction**: Before log lines are sent to an external AI provider for diagnosis or dispatched in outbound alert notifications, LogShed passes the text through `redactor.py` to scrub API keys, JWTs, cloud credentials, passwords, and connection strings. When initiating on-demand AI analysis in the console, you can review the redacted preview before confirming dispatch.
- **Raw Log Fidelity**: In the database and live stream viewer, LogShed stores and displays original, unaltered log payloads. We avoid destructive regex stripping of message bodies so that stack traces, structured JSON payloads, and embedded application timestamps remain intact and verifiable.

---

## Real-Time Alerts & Security Canaries

LogShed includes an in-memory alert evaluation engine that monitors incoming syslog messages and container logs in real time.

Access alerts through the dedicated **Alerts** tab in the top navigation bar:

- **Active Rules (`/alerts/rules`)**: Review, toggle, create, edit, and test custom alert rules.
- **Quick Rules (`/alerts/presets`)**: 1-click installable security canaries for common homelab incidents:
  - **SSH Brute-Force Attacks**: Detects repeated SSH authentication failures within a sliding window.
  - **Reverse-Proxy Auth Floods**: Catches HTTP 401/403 status code bursts from Nginx, Traefik, or Caddy.
  - **Sudo Privilege Escalation**: Immediate pattern alert when an unapproved user attempts `sudo` or command elevation.
  - **Kernel Out-of-Memory (OOM) Killer**: Catches kernel memory panics and process terminations.
- **Incident History (`/alerts/history`)**: Audit trail of triggered incidents with event counts, log excerpts, and full AI diagnosis reports.

### Rule Evaluation Types

1. **Threshold (Sliding Window)**:
   - Tracks matching events within a rolling time window (for example, at least 5 events within 60 seconds).
   - Ideal for burst detection, repeated failure spikes, and rate anomalies.
2. **Pattern (Immediate Match)**:
   - Triggers immediately on a single matching log line.
   - Ideal for critical system alarms (such as kernel panics or hardware errors).

### Cooldown Flap Dampening

Every rule includes a configurable cooldown period (minimum 5 seconds, default 300 seconds). Once an alert fires, duplicate notifications are suppressed during the cooldown window to prevent notification floods while ongoing incidents are active.

### Automated AI Incident Diagnosis

When AI enrichment is enabled on a rule, LogShed automatically scrubs sensitive tokens from triggering logs and requests root-cause diagnosis from your configured AI provider. The resulting incident report includes a concise summary, root cause, and remediation commands directly in the notification and incident log.

For full configuration details, syntax documentation, and practical examples, see the [Alert Rules Guide](docs/ALERT_RULES.md).

---

## Universal Notification Targets

LogShed integrates [Apprise](https://github.com/caronc/apprise) to dispatch alerts across more than 80 notification services and custom webhook endpoints, including:

- **Mobile Push & Messaging**: Pushover, Telegram, Discord, Gotify, Ntfy, Slack, Matrix, Signal
- **Email & Webhooks**: Custom JSON webhooks, SMTP, SendGrid, Mailgun

### Managing Notification Channels

Configure notification endpoints in the **Settings** panel under **Notification Targets**:
- Enter a standard Apprise URL (for example, `discord://webhook_id/webhook_token` or `pover://user_key@token`).
- Click **Send Test Notification** to confirm network connectivity and payload delivery.
- URLs are encrypted at rest with the master key, and credentials are automatically masked in the web interface.

### Security & SSRF Safeguards

Outbound notifications include built-in safeguards:
- **SSRF Protection**: Blocks requests to Docker daemon control ports (`2375`, `2376`), container loopback addresses, and cloud instance metadata ranges (`169.254.169.254`).
- **Private Subnet Control**: By default (`ALLOW_PRIVATE_NOTIFICATION_TARGETS=true`), webhooks can contact local homelab services and LAN IPs. Setting this to `false` blocks RFC 1918 private subnets and local domain names (`.local`, `.internal`, `.lan`).
- **Credential & Secret Scrubbing**: All notification titles, log context lines, and AI summaries are processed through the secret redactor before leaving the server.

---

## Ingestion Drop Rules & Saved Views

### Ingestion Drop Rules

Homelab environments often generate repetitive log noise (such as periodic health checks or debug chatter) that clutters search views and consumes disk space.

Ingestion drop rules evaluate incoming logs in memory before database insertion and FTS5 indexing:
- **Rule Criteria**: Match against host/source, application/container, and message patterns (substring, regular expression, or wildcard `*`).
- **Zero Disk Overhead**: Dropped logs are discarded immediately. Drop counters are held in memory and periodically flushed to SQLite without lock contention.
- **Console Quick-Action**: Open any log entry in the Console, click **Create Drop Rule**, and LogShed pre-fills the rule creation modal with the host, application, and message context.
- **Dry-Run Testing**: Test regular expressions and pattern logic against sample log payloads in the Settings panel before saving.

### Saved Filter Views & URL Sync

Save frequently used search queries and filter combinations for rapid recall:
- Access saved views from the dropdown menu on the Console filter bar or the mobile navigation drawer.
- Pin your preferred views for 1-tap activation.
- Filter criteria automatically sync bidirectionally with browser URL query parameters, enabling direct bookmarking and link sharing across devices.

---

## AI Incident Diagnosis

When an error or panic occurs, you can send selected log lines to an LLM directly from the web interface:

1. **Select Logs**: Click individual log rows or check multiple logs across one or multiple hosts in the live viewer.
2. **Review & Redact**: Click **Inspect Selected Logs with AI**. The modal opens showing the exact, redacted prompt - all API keys, bearer tokens, passwords, and private certificates are scrubbed server-side.
3. **Add Situational Context**: Enter notes (e.g., *"Just updated Proxmox kernel from 6.8 to 6.11 before this panic"*).
4. **Execute**: Choose your preferred model and click **Run Analysis**. LogShed contacts your configured provider and streams back:
  - **Summary**: Concise description of the issue.
  - **Root Cause**: Explanation of why the event occurred based on the log sequence.
  - **Remediation**: Suggested shell commands and configuration file adjustments to fix it.
5. **Audit History**: All AI analyses are stored locally in the **AI Audit Log** so you can review previous diagnoses and token usage at any time.

---

## Local Development

Ensure you have **Python 3.12+** and **Node.js 20+** installed:

```bash
# 1. Clone the repository
git clone https://github.com/BenHornerTech/logshed.git
cd logshed

# 2. Setup Python virtual environment & install backend dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# 3. Install frontend dependencies & run frontend tests
cd frontend
npm install
npm test

# 4. Build frontend SPA (outputs to backend/app/static)
npm run build
cd ..

# 5. Run backend unit & integration tests
.venv/bin/pytest backend/tests/ -v

# 6. Run local development server
python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8080 --reload
```

---

## Licensing

This project is distributed under the terms of the **MIT License**. See the [LICENSE](LICENSE) file for details.