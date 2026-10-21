<p align="center">
  <img src="assets/logshed-logo.png" alt="LogShed Logo" width="180">
</p>

<h1 align="center">LogShed</h1>

<p align="center">
  <strong>A lightweight, self-hosted homelab log aggregator and syslog server featuring real-time streaming, fast SQLite FTS5 search, and user-directed AI incident analysis.</strong>
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
- [Screenshots](#screenshots)
- [Documentation](#documentation)
- [Prerequisites & System Requirements](#prerequisites--system-requirements)
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

Most logging stacks (such as Grafana Loki or the ELK stack) target large production clusters. Running them on a home server often takes several gigabytes of RAM and significant setup time just to collect logs from a router, a couple of virtual machines, and a handful of containers.

LogShed is a compact, self-hosted log hub designed for home labs and personal servers. It ingests standard syslog traffic and Docker container output into a single local SQLite database with full-text search (FTS5), providing a quick search interface and optional AI error diagnosis when you need help reading a stack trace.

> [!NOTE]
> LogShed is intended for home networks and personal self-hosted environments. It is not built for multi-tenant companies or high-throughput enterprise infrastructure.

---

## Features

- **Live Streaming & Fast Search**: Stream incoming logs in real time without lag, and jump back through history using sub-second SQLite FTS5 search. Step chronologically through filtered results directly within the inspection modal.
- **Noise Control at the Door**: Discard repetitive cron chatter, container health checks, and debug spam in memory with ingestion drop rules before anything touches disk. Create rules with one click from log inspection or start from built-in presets.
- **Intelligent Alerting & Rate Spike Protection**: Catch critical error bursts, security patterns, or sudden runaway log storms. Real-time in-memory velocity tracking identifies top culprit containers and repeat patterns during storms and sends notifications straight to Discord, Telegram, Pushover, Gotify, or custom webhooks.
- **On-Demand & Alert AI Diagnosis**: Get human-readable root-cause explanations and practical fix commands when deciphering a cryptic stack trace. All passwords, tokens, and sensitive network addresses are automatically masked before reaching your chosen model (Gemini, OpenAI, or local Ollama).
- **Daily Digest Summaries**: Receive an automated 24-hour analytical rollup delivered to your notification channels, detailing recurring error counts, top logging containers, and storage trends.
- **Maintenance Windows & Automation**: Silence notification channels or drop error chatter during planned package updates, homelab restarts, or scheduled backups. Trigger on-demand windows programmatically via external scripts.
- **Programmatic External API**: Generate scoped Bearer tokens for external automation, Home Assistant integrations, backup runners (Proxmox VE, cron), and AI agents with fine-grained permissions and sliding rate limits. Edge shippers and scripts can also push logs over HTTP (`POST /api/v1/ingest`), and the interactive API docs at `/docs` open behind your web login.
- **Model Context Protocol (MCP) Server**: Connect external AI assistants (Claude Desktop, Cursor, Windsurf) directly to your homelab. Assistants can run full-text log searches, inspect surrounding context, review incident history, and manage maintenance mode.
- **Unified Incident & AI History**: Review past alert triggers alongside full AI diagnostic logs, token usage statistics, and prompt history in a single chronological timeline.
- **Targeted Deletion & Database Compaction**: Prune specific noisy records on demand and compact your SQLite database to reclaim disk space immediately.
- **Homelab Ready**: Assign friendly host aliases to router and switch IP addresses, save custom filter views with bidirectional URL sync, and run comfortably on modest hardware (~150 to 250 MB RAM).

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
| [Forwarding Logs Guide](docs/SENDING_LOGS.md) | Step-by-step syslog setup for OPNsense, Proxmox VE, Synology DSM, UniFi, pfSense, Linux, and Docker, UDP versus TCP guidance, and the HTTP ingest API |
| [Rules Guide](docs/RULES_GUIDE.md) | In-depth guide to real-time threshold, pattern, and rate spike alert rules, plus ingestion drop rules |
| [Model Context Protocol (MCP) Guide](docs/MCP_GUIDE.md) | Connect Claude Desktop, Cursor, and AI assistants to investigate homelab issues with conversational prompt examples |
| [API Token Scopes Guide](docs/API_SCOPES.md) | Every API token scope, the token presets and what each is for, and when to choose Custom scopes |
| [Technical Specification](docs/SPEC.md) | Architecture, SQLite schema, FTS5 triggers, and system design |
| [API Specification](docs/spec/api.md) | REST API endpoint catalog covering internal routes and external programmatic v1 endpoints |

---

## Prerequisites & System Requirements

### Hardware Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| **RAM** | 256 MB | 512 MB - 1 GB (handles burst ingestion and large browser buffers) |
| **CPU** | 1 vCPU / core | 1 - 2 cores (handles continuous FTS5 indexing and log stream parsing) |
| **Storage** | 1 GB free space | Direct SSD / NVMe cache pool (high IOPS for SQLite WAL checkpoints) |

### Performance & Storage Profile

- **Memory Footprint**: ~150 to 250 MB RAM under normal operation.
- **Search Speed**: A full-text search returning 100 results from 813,975 logs took 40 to 54 ms (median 46 ms over 5 runs, timed over the LAN) with LogShed pinned to one core (two threads) of an Intel Core i3-12100.
- **Storage Ratio**: ~650 to 750 bytes per record on disk (includes raw text, relational indexes, and FTS5 full-text search segments). 100,000 logs occupy roughly 67 MB, and 1,000,000 logs occupy roughly 670 MB.
- **Burst Tolerance**: Built with an in-memory staging queue and batching SQLite WAL writer. While normal homelab traffic is typically 10 to 100 logs/second, on direct SSD storage the pipeline can absorb brief bursts of several thousand logs/second during container restart loops or service incidents. UDP syslog can still lose messages in a burst when the kernel receive buffer fills; LogShed shows these as "UDP lost", and TCP syslog avoids that loss (see the [Forwarding Logs Guide](docs/SENDING_LOGS.md#udp-or-tcp-syslog)).

> [!WARNING]
> **Storage & Filesystem Notice for SQLite WAL Mode:**
> Always host the `/data` directory on a local filesystem (ext4, btrfs, zfs) or a direct SSD cache pool. **Do not place `/data` on network mounts (NFS, SMB) or Unraid user shares (`/mnt/user/`)**, as these do not reliably support POSIX file locking or shared memory (`mmap`) required for concurrent SQLite WAL checkpoints.

### Software Dependencies

- **Docker Engine**: 24.0+
- **Docker Compose**: v2.0+ (or Unraid OS 6.9+)
- **Supported Architectures**:
  - `linux/amd64` (Standard x86_64 servers and PCs)
  - `linux/arm64` (Raspberry Pi 4/5, Apple Silicon VMs, ARM64 homelab boards)

---

## Quick Start

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
2. On first run, choose an administrator password to complete initial setup.
3. Log in with your new password to access the dashboard.

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
| **Frontend UI** | [React 19](https://react.dev/), [Vite](https://vitejs.dev/), [Tailwind CSS](https://tailwindcss.com/), [@tanstack/react-virtual](https://tanstack.com/virtual), [Recharts](https://recharts.org/), [Lucide React](https://lucide.dev/) |
| **Collector Integrations** | [HTTPX](https://www.python-httpx.org/) (Docker Engine API over UDS and TCP), Async UDP/TCP Syslog server |
| **Alerting & Notifications** | [Apprise](https://github.com/caronc/apprise) (multi-channel alerts and webhooks) |
| **AI Integrations** | Google GenAI SDK (`google-genai`), OpenAI SDK (`openai` compatible with Ollama/vLLM/LocalAI) |
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
# Optional: end-to-end tests (live Docker build and AI provider calls using keys from .env)
.venv/bin/pytest backend/tests/ -m e2e -v

# 6. Run local development server
python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8080 --reload
```

### Updating Pinned Dependencies

Backend dependencies are pinned to exact versions in `backend/requirements.txt` for reproducible builds. Transitive dependencies are not pinned. To update one:

1. Change its version in `backend/requirements.txt` and reinstall it (`pip install -r backend/requirements.txt`).
2. Run the full backend test suite (`.venv/bin/pytest backend/tests/ -q`) and the frontend tests (`cd frontend && npm test`).
3. Build the Docker image and run the end-to-end suite (`.venv/bin/pytest backend/tests/ -m e2e -v`) before releasing. It builds the image from `Dockerfile`, so it confirms the new versions install on the Python 3.12 base image.

---

## Licensing

This project is distributed under the terms of the **MIT License**. See the [LICENSE](LICENSE) file for details.
