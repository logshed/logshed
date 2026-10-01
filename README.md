<p align="center">
  <img src="assets/logshed-logo.png" alt="LogShed Logo" width="180">
</p>

<h1 align="center">LogShed</h1>

<p align="center">
  <strong>A lightweight, self-hosted homelab log aggregator and syslog server featuring real-time streaming, fast SQLite FTS5 search, and user-directed AI incident analysis.</strong>
</p>

<p align="center">
  <a href="https://github.com/logshed/logshed"><img src="https://img.shields.io/badge/version-1.2.0-blue?style=flat-square" alt="Version 1.2.0"></a>
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
- **Maintenance Windows**: Silence notification channels with one click during planned package updates, homelab restarts, or scheduled system backups.
- **Unified Incident & AI History**: Review past alert triggers alongside full AI diagnostic logs, token usage statistics, and prompt history in a single chronological timeline.
- **Targeted Deletion & Database Compaction**: Prune specific noisy records on demand and compact your SQLite database to reclaim disk space immediately.
- **Homelab Ready**: Assign friendly host aliases to router and switch IP addresses, save custom filter views with bidirectional URL sync, and run comfortably on modest hardware (~150 to 250 MB RAM).

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

## Documentation

Detailed documentation and step-by-step setup guides are available in the [`docs/`](docs/) directory:

| Guide | Description |
|---|---|
| [Configuration Reference](docs/CONFIGURATION.md) | Complete environment variable reference, settings hierarchy, and storage paths |
| [Forwarding Logs Guide](docs/SENDING_LOGS.md) | Step-by-step syslog setup for OPNsense, Proxmox VE, Synology DSM, UniFi, pfSense, Linux, and Docker |
| [Rules Guide](docs/RULES_GUIDE.md) | In-depth guide to real-time threshold, pattern, and rate spike alert rules, plus ingestion drop rules |
| [Technical Specification](docs/SPEC.md) | Architecture, SQLite schema, FTS5 triggers, and API specifications |

---

## Prerequisites & System Requirements

### Hardware Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| **RAM** | 256 MB | 512 MB - 1 GB (handles burst ingestion and large browser buffers) |
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
docker exec -it logshed python -m app.cli reset-admin --password "your_new_secure_password"
```

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

# 6. Run local development server
python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8080 --reload
```

---

## Licensing

This project is distributed under the terms of the **MIT License**. See the [LICENSE](LICENSE) file for details.