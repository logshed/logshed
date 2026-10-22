<p align="center">
  <img src="assets/logshed-logo.png" alt="LogShed Logo" width="180">
</p>

<h1 align="center">LogShed</h1>

<p align="center">
  <strong>A self-hosted log aggregator and syslog server for homelabs. One container collects Docker and syslog logs, streams them live, searches them with SQLite FTS5, alerts you, and can explain errors with an AI model you choose.</strong>
</p>

<p align="center">
  <a href="https://github.com/logshed/logshed"><img src="https://img.shields.io/badge/version-1.3.0--dev-blue?style=flat-square" alt="Version 1.3.0-dev"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/logshed/logshed/pkgs/container/logshed"><img src="https://img.shields.io/badge/container-ghcr.io-blue?logo=docker&logoColor=white&style=flat-square" alt="GHCR Container"></a>
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
- [Home Assistant Integration](#home-assistant-integration)
- [Screenshots](#screenshots)
- [Documentation](#documentation)
- [Prerequisites & System Requirements](#prerequisites--system-requirements)
  - [Software Requirements](#software-requirements)
  - [Resource Use](#resource-use)
- [Quick Start](#quick-start)
  - [Docker Compose (Recommended)](#docker-compose-recommended)
  - [Docker Run](#docker-run)
  - [Unraid Installation](#unraid-installation)
  - [Initial Setup & Authentication](#initial-setup--authentication)
- [Built With](#built-with)
- [Local Development](#local-development)
- [Licensing](#licensing)

---

## Overview

Most logging stacks (such as Grafana Loki, Graylog or the ELK stack) are built for large production clusters. Each runs several services: Loki needs Grafana and a collector beside it, and Graylog and ELK need a search cluster such as OpenSearch or Elasticsearch, each with its own configuration. That is a lot to run just to collect logs from a router, a couple of virtual machines, and a handful of containers.

LogShed is a compact, self-hosted log hub for home labs and personal servers. It receives syslog and Docker container output into one local SQLite database with full-text search (FTS5), and shows it all in a live web console. It runs as a single container. If you want help reading a stack trace, you can ask an AI model of your choice; AI is optional and stays off until you add a provider.

Version 1.3.0 adds several Docker hosts, a Sources hub, HTTP log ingest, scoped API tokens, an MCP server, a Home Assistant integration, AI follow-up questions and maintenance windows you can start from many places. See the [changelog](CHANGELOG.md) for everything that changed.

> [!NOTE]
> LogShed is intended for home networks and personal self-hosted environments. It is not built for multi-tenant companies or high-throughput enterprise infrastructure.

---

## Features

- **Syslog, Docker and HTTP in one place**: A built-in syslog server (UDP and TCP on port 1514, RFC 3164 and RFC 5424) takes logs from routers, firewalls, NAS boxes and hypervisors. LogShed also tails Docker containers on the local host and on other Docker hosts you add under Settings > Sources (local socket, a Docker socket proxy over TCP, or mutual TLS). Vector, Fluent Bit and scripts can push logs over HTTP with `POST /api/v1/ingest`. If the kernel drops UDP syslog datagrams in a burst, the console shows "UDP lost"; TCP syslog avoids that loss.
- **Readable source names**: Containers are named from their `logshed.alias` or `logshed.name` label, then the Compose service name, then the Swarm service name. Syslog devices get friendly aliases and notes in the Sources hub.
- **Live stream and full-text search**: Watch logs arrive in real time, then search the history with SQLite FTS5 (a search returning 100 results from about 814,000 logs took a median of 46 ms; see [Resource Use](#resource-use)). Shorthand filters such as `15m`, `24h`, `7d`, `today` and named severities (`error`, `warning`) work in the console, the REST API and MCP. Save filter views, and read the lines before and after any record.
- **Noise control at the door**: Discard repetitive cron chatter, container health checks and debug spam in memory with ingestion drop rules, before anything is written to disk. Create a rule in one click from a log line or start from a built-in preset, and drag rules into the order you want.
- **Alerts and rate spike detection**: Threshold, pattern and rate spike rules catch error bursts, security patterns and runaway log storms, and name the top service, host and repeated message behind a spike. Notifications go to 100+ services through Apprise, such as Discord, Telegram, Pushover, Gotify, ntfy, Slack, email and custom webhooks. Rule patterns are limited to 50 ms per match so a bad pattern cannot freeze ingestion.
- **Maintenance windows**: Silence alert notifications, or drop incoming error logs, during planned upgrades and backups. Start a window from the web console, a recurring schedule, Home Assistant, the REST API or MCP. Several windows can overlap, and each one is recorded in incident history.
- **Optional AI diagnosis**: Off until you add a provider, and every other feature works without it. Choose Google Gemini, OpenAI, Anthropic Claude, or an OpenAI-compatible server such as Ollama, vLLM or LocalAI on your own network. Ask for a root-cause explanation and fix commands when you request an analysis of selected logs or when an alert with AI enrichment fires, then ask follow-up questions on the same logs. As a safety net, LogShed masks common secret patterns (passwords, tokens, API keys, private keys and similar, as listed in the [changelog](CHANGELOG.md)) before a prompt leaves the server. This is best-effort and cannot catch every secret. On-demand requests show you the exact prompt first, so review it before you send; an alert with AI enrichment sends its prompt without a preview.
- **Incident history and daily digest**: Review alert firings, AI diagnoses with their follow-up threads and token usage, and maintenance windows in one timeline. An optional daily digest sends a 24-hour summary of error counts, noisy services and storage change to your notification channels.
- **REST API and scoped tokens**: Create Bearer tokens with only the scopes a script needs, such as a Home Assistant or edge shipper preset, and change a token's scopes later without issuing a new one. The interactive API docs (Swagger UI and ReDoc) are served by LogShed at `/docs` and `/redoc`, behind your web login.
- **MCP server for AI assistants**: Connect Claude Desktop, Cursor, Windsurf or Antigravity so an assistant can search logs, count errors, read the context around an incident, check alerts and metrics, and start or end maintenance windows. It is off by default, and tool output is masked with the same best-effort redaction.
- **Storage you control**: Retention is 14 days by default and up to 30 days unless you raise the limit. Lowering retention shows what would be deleted before you confirm. You can also delete specific noisy records and compact the database to reclaim disk space.
- **Home Assistant**: An integration for sensors, alert state and a maintenance switch. See [Home Assistant Integration](#home-assistant-integration).
- **No telemetry**: LogShed sends no telemetry. Its only built-in outbound call is an optional release check against ghcr.io that tells the console when a newer stable release exists (turn it off with "Check for new versions" in Settings > Application). AI providers, notification targets and extra Docker hosts are contacted only if you configure them.

---

## Home Assistant Integration

The [LogShed integration for Home Assistant](https://github.com/logshed/homeassistant-logshed) connects Home Assistant to your LogShed server. Install it through HACS as a custom repository. It provides:

- Sensors for the ingestion rate, error counts (1 hour, 24 hours, and 24 hours per severity) and database and disk use, plus an Alert State problem sensor and a Maintenance Active sensor.
- A Quick Maintenance switch and the `logshed.enable_maintenance` and `logshed.disable_maintenance` actions, so a backup automation can silence alerts or drop errors while it runs.
- Prune and Vacuum buttons, and an update entity that shows new LogShed releases.

It needs Home Assistant 2024.12.0 or later and LogShed 1.3.0 or later, and it connects with an API token made from the **Home Assistant** preset in Settings > API & MCP Access. See the integration's README for installation, entities and example automations, and the [API Token Scopes Guide](docs/API_SCOPES.md#home-assistant) for the scopes involved.

---

## Screenshots

| Live Log Stream | Filtered Search |
| :---: | :---: |
| <a href="assets/logshed-logstream.png"><img src="assets/logshed-logstream.png" width="450" alt="Live console with syslog and Docker logs from several hosts"/></a> | <a href="assets/logshed-filter.png"><img src="assets/logshed-filter.png" width="450" alt="Console filtered to one host, warnings and worse, last 24 hours"/></a> |
| *Syslog hosts and Docker containers from several machines in one real-time console* | *One host, warnings and worse, last 24 hours: disk, UPS and sync problems at a glance* |
| **Log Detail & Surrounding Context** | **Sources: Syslog and Docker Hosts** |
| <a href="assets/logshed-log-detail.png"><img src="assets/logshed-log-detail.png" width="450" alt="Log detail drawer for a failed Proxmox backup"/></a> | <a href="assets/logshed-sources.png"><img src="assets/logshed-sources.png" width="450" alt="Sources page with syslog host aliases and Docker hosts"/></a> |
| *Structured fields, the raw log text, and the surrounding lines on demand* | *Friendly names and notes for syslog senders, plus Docker hosts over a socket proxy or mutual TLS* |
| **AI Redaction Preview** | **AI Diagnosis with Follow-up** |
| <a href="assets/logshed-ai-redaction.png"><img src="assets/logshed-ai-redaction.png" width="450" alt="AI prompt preview with a password and a token shown as REDACTED"/></a> | <a href="assets/logshed-ai-analysis.png"><img src="assets/logshed-ai-analysis.png" width="450" alt="AI diagnosis with fix commands and a follow-up question"/></a> |
| *Check the exact prompt before it is sent: passwords and tokens already read `[REDACTED]`* | *Root cause and fix commands from the model you choose, then follow-up questions on the same logs* |
| **Alert Rules** | **Maintenance Windows** |
| <a href="assets/logshed-alert-rules.png"><img src="assets/logshed-alert-rules.png" width="450" alt="Alert rules list with threshold and pattern rules"/></a> | <a href="assets/logshed-maintenance.png"><img src="assets/logshed-maintenance.png" width="450" alt="Active maintenance sessions from the web console and Home Assistant"/></a> |
| *Threshold and pattern rules with AI enrichment, firing counts and drag-and-drop ordering* | *Overlapping sessions from the web console, Home Assistant, the API or MCP, plus recurring schedules* |
| **Home Assistant** | **Mobile Console** |
| <a href="assets/logshed-ha-dashboard.png"><img src="assets/logshed-ha-dashboard.png" height="467" alt="Home Assistant dashboard with LogShed sensors"/></a> | <a href="assets/logshed-mobile.png"><img src="assets/logshed-mobile.png" height="467" alt="Mobile console"/></a> |
| *Error counts, alert state and a maintenance switch with the [LogShed Home Assistant integration](https://github.com/logshed/homeassistant-logshed)* | *Touch-friendly console for checking on things from a phone* |

---

## Documentation

Detailed documentation and step-by-step setup guides are available in the [`docs/`](docs/) directory:

| Guide | Description |
|---|---|
| [Configuration Reference](docs/CONFIGURATION.md) | Complete environment variable reference, settings hierarchy, and storage paths |
| [Forwarding Logs Guide](docs/SENDING_LOGS.md) | Step-by-step syslog setup for OPNsense, Proxmox VE, Synology DSM, UniFi, pfSense, Home Assistant, Linux, and Docker, UDP versus TCP guidance, and the [HTTP ingest API](docs/SENDING_LOGS.md#http-ingest-api) for Vector, Fluent Bit and scripts |
| [Rules Guide](docs/RULES_GUIDE.md) | In-depth guide to real-time threshold, pattern, and rate spike alert rules, plus ingestion drop rules |
| [Model Context Protocol (MCP) Guide](docs/MCP_GUIDE.md) | Connect Claude Desktop, Cursor, Windsurf, Antigravity and other AI assistants to investigate homelab issues, with conversational prompt examples |
| [API Token Scopes Guide](docs/API_SCOPES.md) | Every API token scope, the token presets and what each is for, and when to choose Custom scopes |
| [Home Assistant Integration](https://github.com/logshed/homeassistant-logshed) | Installation through HACS, entities, service actions, and example automations (separate repository) |
| [Changelog](CHANGELOG.md) | What changed in each release, with [upgrade notes](CHANGELOG.md#upgrade-notes) for moving from 1.2.x to 1.3.0 |
| [Technical Specification](docs/SPEC.md) | Architecture, SQLite schema, FTS5 triggers, and system design |
| [API Specification](docs/spec/api.md) | REST API endpoint list covering internal routes and external programmatic v1 endpoints |

---

## Prerequisites & System Requirements

### Software Requirements

- **Docker Engine**: 24.0+ (LogShed talks to the Docker Engine API v1.43)
- **Docker Compose**: v2 for the Compose example below (the `docker run` command and the Unraid template do not need it)
- **Architectures**: `linux/amd64` and `linux/arm64`. The container image is published for both.
- **Storage for `/data`**: a local filesystem, as described in the notice below.

> [!WARNING]
> **Storage & Filesystem Notice for SQLite WAL Mode:**
> Always host the `/data` directory on a local filesystem (ext4, btrfs, zfs) or a direct SSD cache pool. **Do not place `/data` on network mounts (NFS, SMB) or Unraid user shares (`/mnt/user/`)**, as these do not reliably support POSIX file locking or shared memory (`mmap`) required for concurrent SQLite WAL checkpoints.

### Resource Use

No minimum hardware has been tested. These figures were measured on one test setup, an Intel Core i3-12100 server with LogShed pinned to one core (two threads), so your numbers will differ:

- **Memory**: About 240 MB of RAM for the container (236 MB idle and 238 MB while receiving 20 logs/second, with about 814,000 logs stored). If you set a container memory limit, leave headroom above this figure.
- **CPU**: 0.04% when idle.
- **Search Speed**: A full-text search returning 100 results from 813,975 logs took 40 to 54 ms (median 46 ms over 5 runs, timed over the LAN).
- **Storage Ratio**: About 650 to 750 bytes per record on disk (includes raw text, relational indexes, and FTS5 full-text search segments). 100,000 logs occupy roughly 67 MB, and 1,000,000 logs occupy roughly 670 MB. Retention is 14 days by default.
- **Bursts**: Incoming logs wait in a 10,000-entry in-memory queue and are written to SQLite in batches. If the queue fills, new logs are dropped and counted as "Dropped" in the console. UDP syslog can lose messages in a burst when the kernel receive buffer fills, and TCP syslog avoids that loss (see [UDP or TCP syslog](docs/SENDING_LOGS.md#udp-or-tcp-syslog)).

---

## Quick Start

> [!IMPORTANT]
> **Updating from 1.2.x?** The first start of 1.3.0 migrates the database, so back up `/data` first if you may want to return to 1.2.x. Read the [upgrade notes](CHANGELOG.md#upgrade-notes) too: among other changes, the API docs now need a login and `PUID` or `PGID` set to `0` will stop working in a later release.

### Docker Compose (Recommended)

Save the following as `docker-compose.yml`:

```yaml
services:
  logshed:
    image: ghcr.io/logshed/logshed:latest
    container_name: logshed
    restart: unless-stopped
    ports:
      - "8080:8080"        # Web Dashboard and REST API
      - "1514:1514/udp"    # Syslog UDP Ingestion
      - "1514:1514/tcp"    # Syslog TCP Ingestion
    environment:
      - PUID=1000
      - PGID=1000
      # Set TZ to your own time zone (for example Europe/London or America/New_York).
      # LogShed uses it for syslog timestamps that carry no zone, maintenance schedules and the daily digest.
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

To upgrade on your own schedule, replace `latest` with a version tag such as `1.3.0`.

If you would rather not mount the Docker socket into LogShed, run a Docker socket proxy such as `tecnativa/docker-socket-proxy` with `CONTAINERS=1` and set `DOCKER_HOST=tcp://<proxy host>:2375` instead. LogShed only reads container lists, details, events and logs. See [Docker Log Tailing](docs/CONFIGURATION.md#docker-log-tailing) in the Configuration Reference. You can add more Docker hosts later under Settings > Sources.

---

### Docker Run

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
  ghcr.io/logshed/logshed:latest
```

---

### Unraid Installation

LogShed provides an official Unraid Community Applications template in [`unraid-template.xml`](unraid-template.xml).

1. Copy `unraid-template.xml` to `/boot/config/plugins/dockerMan/templates-user/my-LogShed.xml` on your Unraid flash drive, or install it via Community Applications when published.
2. Set `/data` directly to your SSD cache pool (for example, `/mnt/cache/appdata/logshed`). Avoid `/mnt/user/` paths due to FUSE file locking constraints.
3. Map `/var/run/docker.sock` to `/var/run/docker.sock` with read-only (`:ro`) access to tail local containers.

---

### Initial Setup & Authentication

1. Open your browser and navigate to `http://<YOUR_SERVER_IP>:8080`.
2. On first run, the "First-Run Admin Setup" screen asks for an administrator password only (8 to 128 characters, entered twice). There is no user name. Click **Set Up Admin & Log In**.
3. You are signed in to the console. Containers on the Docker host appear in the live stream on their own. Later visits ask for the same password on the login page.
4. Next steps: point a router, NAS or Proxmox host at `<YOUR_SERVER_IP>:1514` (see the [Forwarding Logs Guide](docs/SENDING_LOGS.md)), add other Docker hosts and syslog host aliases under Settings > Sources, and optionally add an alert rule, a notification target, or an AI provider.

*(For detailed architectural and security specifications, see [docs/SPEC.md](docs/SPEC.md).)*

#### Emergency Password Reset (CLI)

If you forget your administrator password, run the built-in password reset CLI tool inside the running container:

```bash
docker exec -it <container> python -m app.cli reset-admin
```

The tool prompts twice for the new password (8 to 128 characters). The `--password` option still works, but it prints a warning because the password is then visible in your shell history and in process listings.

---

## Built With

| Layer | Technologies |
|---|---|
| **Backend Runtime** | Python 3.12 (`asyncio`), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/) (single-worker process model) |
| **Storage & Search** | Standard Library `sqlite3` + `asyncio.to_thread()`, WAL mode, FTS5 external content virtual table |
| **Frontend UI** | [React 19](https://react.dev/), [Vite](https://vitejs.dev/), [Tailwind CSS](https://tailwindcss.com/), [@tanstack/react-virtual](https://tanstack.com/virtual), [Recharts](https://recharts.org/), [Lucide React](https://lucide.dev/); [Swagger UI](https://github.com/swagger-api/swagger-ui) and [ReDoc](https://github.com/Redocly/redoc) bundles for the built-in API docs |
| **Collector Integrations** | [HTTPX](https://www.python-httpx.org/) (Docker Engine API over UDS and TCP), Async UDP/TCP Syslog server |
| **Alerting & Notifications** | [Apprise](https://github.com/caronc/apprise) (100+ notification services and webhooks), [`regex`](https://pypi.org/project/regex/) (time-limited matching for user-written rule patterns) |
| **AI Integrations** | Google GenAI SDK (`google-genai`), OpenAI SDK (`openai`, also used for OpenAI-compatible servers such as Ollama, vLLM and LocalAI), Anthropic SDK (`anthropic`) |
| **Model Context Protocol** | MCP Python SDK (`mcp`): Streamable HTTP, SSE and stdio transports |
| **Security & Cryptography** | `argon2-cffi` (password hashing), `cryptography.fernet` (runtime settings encryption) |
| **Packaging & Base** | Multi-stage Docker build, `python:3.12-slim`, `tini` init, `gosu` privilege dropping |

For complete technical schemas, database structures, FTS5 triggers, and REST/SSE API specifications, see [docs/SPEC.md](docs/SPEC.md).

---

## Local Development

Ensure you have **Python 3.12+** and **Node.js 20+** installed:

```bash
# 1. Clone the repository
git clone https://github.com/logshed/logshed.git
cd logshed

# 2. Setup Python virtual environment & install backend dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt   # app and test dependencies

# 3. Install frontend dependencies & run frontend tests
cd frontend
npm install
npm test

# 4. Build frontend SPA (outputs to backend/app/static)
npm run build
cd ..

# 5. Run backend unit & integration tests
.venv/bin/pytest backend/tests/ -v
# Optional: end-to-end tests (live Docker build and AI provider calls using keys from .env)
.venv/bin/pytest backend/tests/ -m e2e -v

# 6. Run local development server
python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8080 --reload
```

### Updating Pinned Dependencies

Backend dependencies are pinned to exact versions in `backend/requirements.txt` for reproducible builds, and the test tools in `backend/requirements-dev.txt`. Transitive dependencies are not pinned. To update one:

1. Change its version in `backend/requirements.txt` (or `backend/requirements-dev.txt`) and reinstall (`pip install -r backend/requirements.txt -r backend/requirements-dev.txt`).
2. Run the full backend test suite (`.venv/bin/pytest backend/tests/ -q`) and the frontend tests (`cd frontend && npm test`).
3. Build the Docker image and run the end-to-end suite (`.venv/bin/pytest backend/tests/ -m e2e -v`) before releasing. It builds the image from `Dockerfile`, so it confirms the new versions install on the Python 3.12 base image.

---

## Licensing

This project is distributed under the terms of the **MIT License**. See the [LICENSE](LICENSE) file for details.
