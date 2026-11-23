<p align="center">
  <img src="assets/logshed-logo.png" alt="LogShed Logo" width="180">
</p>

<h1 align="center">LogShed</h1>

<p align="center">
  <strong>Self-hosted homelab logging in a single container. Collect Docker and syslog logs, search them, get alerts, and optionally ask AI to explain errors - without running a multi-service logging stack.</strong>
</p>

<p align="center">
  <a href="https://github.com/logshed/logshed"><img src="https://img.shields.io/badge/version-1.4.0--beta.1-blue?style=flat-square" alt="Version 1.4.0-beta.1"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="MIT License"></a>
  <a href="https://github.com/logshed/logshed/pkgs/container/logshed"><img src="https://img.shields.io/badge/container-ghcr.io-blue?logo=docker&logoColor=white&style=flat-square" alt="GHCR Container"></a>
  <a href="#system-requirements"><img src="https://img.shields.io/badge/arch-amd64%20%7C%20arm64-blueviolet?style=flat-square" alt="Multi-Arch Support"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/python-3.14-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.14"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React 19"></a>
  <a href="#built-with"><img src="https://img.shields.io/badge/SQLite-WAL%20%2B%20FTS5-003B57?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite FTS5"></a>
</p>

<p align="center">
  <img src="assets/logshed-demo.gif" width="960" alt="The LogShed console streaming syslog and Docker logs from opnsense, truenas, pve-node1 and docker-vm, then a search for smartd sdc narrowing the logs and the log volume chart to one critical disk warning">
</p>

---

## Table of Contents

- [Overview](#overview)
- [What LogShed Solves](#what-logshed-solves)
- [New in 1.4.0](#new-in-140)
- [Quick Start](#quick-start)
  - [Docker Compose (Recommended)](#docker-compose-recommended)
  - [Docker Run](#docker-run)
  - [Unraid Installation](#unraid-installation)
  - [First-Run Setup](#first-run-setup)
- [Home Assistant Integration](#home-assistant-integration)
- [Screenshots](#screenshots)
- [Documentation](#documentation)
- [System Requirements](#system-requirements)
- [Performance](#performance)
- [Built With](#built-with)
- [Local Development](#local-development)
- [Support the Project](#support-the-project)
- [Licensing](#licensing)

---

## Overview

Most logging stacks (Grafana Loki, Graylog, or ELK) are built for large production clusters. Running them means deploying and maintaining separate collectors, indexers, databases, and dashboards - a lot of overhead just to read logs from a router, a NAS, a couple of virtual machines, and your Docker containers.

LogShed gives you a central place for your homelab logs in a **single container** using about 240 MB of RAM. Start it with Docker Compose, set an admin password, and your local container logs start streaming right away. Point your router or NAS at port `1514` for syslog, and everything lands in one searchable web console backed by local SQLite storage.

> [!NOTE]
> LogShed is built for home networks and personal servers, not multi-tenant businesses or high-volume enterprise infrastructure.

---

## What LogShed Solves

- **All your homelab logs in one place**: A built-in syslog server (UDP and TCP on port `1514`) receives logs from firewalls, routers, NAS appliances, and hypervisors (OPNsense, pfSense, UniFi, Synology, TrueNAS, Proxmox). LogShed also tails Docker containers on the local machine and on remote Docker hosts, and accepts logs over HTTP (`POST /api/v1/ingest`) from scripts, Vector, or Fluent Bit.
- **Readable device and app names**: Map syslog IP addresses to friendly names and notes in the Sources hub. Container names automatically use their `logshed.alias` or `logshed.name` label, or their Docker Compose service name.
- **Live streaming and fast search**: Watch logs arrive in real time or search across hundreds of thousands of stored records in milliseconds. Filter by host, application, severity, and relative time (`15m`, `24h`, `today`), pin saved views to the toolbar, and inspect the lines immediately before and after any log entry.
- **Drop repetitive noise before it hits disk**: Homelab services produce plenty of cron chatter, health checks, and debug spam. Ingestion drop rules discard matching lines in memory before they are stored. Create a drop rule in one click from any log line or enable a built-in preset.
- **Alerts, rate-spike detection, and daily digests**: Trigger alerts on error thresholds, specific patterns, or sudden log storms (identifying which service, host, and message caused the spike). Jump straight from an alert to the surrounding logs in the console, and send notifications to 100+ services via Apprise (Discord, Telegram, Pushover, Gotify, ntfy, Slack, email, and webhooks). An optional daily digest sends a 24-hour summary of errors, noisy services, and storage use.
- **Planned maintenance without alert fatigue**: Silence notifications - or drop incoming error logs altogether - during backups and server upgrades. Start maintenance windows on demand, on a recurring schedule, or automatically from Home Assistant, the REST API, or MCP.
- **Optional AI error explanations**: Off by default until you add a provider. Connect Google Gemini, OpenAI, Anthropic Claude, OpenRouter (each model's price and context size shown in a searchable list), or any OpenAI-compatible endpoint (including Ollama, LM Studio, or Groq). Select a stack trace or failure to get a plain-English explanation and suggested fix commands, then ask follow-up questions on the same logs. Common secrets (passwords, API keys, tokens, private keys) are masked before prompts leave your server, and on-demand checks show you the exact prompt before sending.
- **Predictable disk use and privacy**: Retention is 14 days by default, with a preview of what will be deleted before you lower it, and targeted deletion for noisy apps; retention deletes each expired day's file, so its disk space comes back at once. LogShed sends no telemetry; its only built-in outbound request is an optional release check against ghcr.io that you can turn off in Settings.

---

## New in 1.4.0

- **Logs in one file per day**: Logs take a little over half the disk space they did, retention deletes a whole day's file so the space comes back at once (no Compact needed), and searches and counts over many days are much faster.
- **Safer upgrades**: Every upgrade that changes the database backs it up first and shows its progress in the web interface. If it cannot finish, LogShed waits in recovery mode instead of exiting. Make, download and delete backups on the Storage page, and put one back with the `restore` command. (The backup made before the 1.4.0 upgrade leaves out the stored logs: to go back to 1.3.x with your logs, copy `/data` yourself first.)
- **A Console for busy networks**: A log volume chart you can click to zoom in, logs downloaded as CSV or NDJSON, JSON logs shown as a tree, resizable columns, and a live view that keeps up during a log flood.
- **Sharper alert rules and maintenance**: Limit an alert rule to some hosts, choose regex or plain text, check a rule against the last 24 hours before saving it, and start targeted or error-dropping maintenance from the web interface.
- **Sources at a glance**: When each host alias and Docker host last sent a log, the five busiest hosts and apps on the Storage page, and devices that name themselves marked as self-reported.
- **Settings and AI**: Export and import the whole configuration (secrets under a password you choose), Settings saves only what you changed, and OpenRouter joins the AI providers, with a searchable model list showing each model's price.
- **Security**: Signing out ends every session, cookies and stored secrets use separate keys, a proxy on loopback is trusted only when listed, and LogShed refuses to run as root.
- **Home Assistant**: Integration 1.1.0 adds the longest silent source, the last backup and the server's status.

See the [changelog](CHANGELOG.md) for the full list of changes and [upgrade notes](CHANGELOG.md#upgrade-notes).

---

## Quick Start

> [!IMPORTANT]
> **Updating from 1.3.x?** The first start of 1.4.0 backs up the database (without the stored logs), then upgrades its schema and stores new logs in one file per day, and every browser signs in again once. If you back up `/data` with your own script, copy every file in it. See the [upgrade notes](CHANGELOG.md#upgrade-notes) before updating, in particular going back to 1.3.x, `PUID`/`PGID` of `0`, and proxies on loopback.

### Docker Compose (Recommended)

Save the following as `docker-compose.yml`:

```yaml
services:
  logshed:
    image: ghcr.io/logshed/logshed:latest
    container_name: logshed
    restart: unless-stopped
    ports:
      - "8080:8080"        # Web Console and REST API
      - "1514:1514/udp"    # Syslog UDP
      - "1514:1514/tcp"    # Syslog TCP
    environment:
      - PUID=1000
      - PGID=1000
      # Set TZ to your local time zone (for example Europe/London or America/New_York)
      - TZ=UTC
      - PORT=8080
      - SYSLOG_PORT=1514
      - DOCKER_HOST=unix:///var/run/docker.sock
    volumes:
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

Start the container:

```bash
docker compose up -d
```

To pin a specific release instead of following `latest`, use a version tag such as `ghcr.io/logshed/logshed:1.3.1`.

If you prefer not to mount `/var/run/docker.sock` directly, you can point `DOCKER_HOST` at a socket proxy such as `tecnativa/docker-socket-proxy` (with `CONTAINERS=1`). See [Docker Log Tailing](docs/CONFIGURATION.md#docker-log-tailing) for details. Additional Docker hosts can be added in the web UI under **Settings > Sources**.

### Docker Run

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

### Unraid Installation

An Unraid template is included in [`unraid-template.xml`](unraid-template.xml):

1. Copy `unraid-template.xml` to `/boot/config/plugins/dockerMan/templates-user/my-LogShed.xml` on your Unraid flash drive.
2. Map `/data` directly to your cache pool (for example `/mnt/cache/appdata/logshed`), not `/mnt/user/`.
3. Map `/var/run/docker.sock` read-only (`:ro`) to collect local container logs.

### First-Run Setup

1. Open `http://<YOUR_SERVER_IP>:8080` in your browser.
2. Choose an administrator password (8 to 128 characters). There is no username.
3. Local Docker container logs will begin streaming immediately.
4. From here, you can point routers, NAS boxes, or Proxmox hosts at `<YOUR_SERVER_IP>:1514` (see the [Forwarding Logs Guide](docs/SENDING_LOGS.md)), add remote Docker hosts and friendly host aliases under **Settings > Sources**, and set up alert rules or notifications.

If you ever forget your password, reset it from the host terminal:

```bash
docker exec -it logshed python -m app.cli reset-admin
```

---

## Home Assistant Integration

The [LogShed integration for Home Assistant](https://github.com/logshed/homeassistant-logshed) connects your LogShed server to Home Assistant. Install it through the [Home Assistant Marketplace](https://www.home-assistant.io/integrations/marketplace/) (**Settings > Marketplace**, search for **LogShed**, on Home Assistant 2026.11 and later) or through HACS on older versions:

- **Sensors** for ingestion rate, error counts (1h, 24h, and by severity), database size, disk usage, alert state, and active maintenance.
- **Source and server health** (integration 1.1.0 with LogShed 1.4.0): when the longest-silent configured source last sent a log, the newest backup, and whether LogShed is running, upgrading its database or waiting in recovery.
- **Maintenance switch and actions** (`logshed.enable_maintenance` and `logshed.disable_maintenance`) so backup or update automations can silence alerts automatically.
- **Database maintenance buttons** (Prune and Vacuum) and an **update entity** for new LogShed releases.

Create a token using the **Home Assistant** preset in **Settings > API & MCP Access** to connect. Requires Home Assistant 2024.12.0+ and LogShed 1.3.0+.

---

## Screenshots

| Live Log Stream | Filtered Search |
| :---: | :---: |
| <a href="assets/logshed-logstream.png"><img src="assets/logshed-logstream.png" width="450" alt="Live console with the log volume chart over a week, and syslog and Docker logs from several hosts"/></a> | <a href="assets/logshed-filter.png"><img src="assets/logshed-filter.png" width="450" alt="Console filtered to one host, warnings and worse, last 24 hours"/></a> |
| *Syslog hosts and Docker containers from several machines in one real-time console, with a week of log volume above it* | *One host, warnings and worse, last 24 hours: disk, UPS and sync problems at a glance* |
| **Log Detail & Surrounding Context** | **Sources: Syslog and Docker Hosts** |
| <a href="assets/logshed-log-detail.png"><img src="assets/logshed-log-detail.png" width="450" alt="Log detail drawer with a JSON log shown as a tree"/></a> | <a href="assets/logshed-sources.png"><img src="assets/logshed-sources.png" width="450" alt="Sources page with syslog host aliases and Docker hosts"/></a> |
| *JSON logs as a tree you can open and copy from, beside the fields and the raw log text* | *Friendly names and notes for syslog senders, plus Docker hosts over a socket proxy or mutual TLS* |
| **AI Redaction Preview** | **AI Diagnosis with Follow-up** |
| <a href="assets/logshed-ai-redaction.png"><img src="assets/logshed-ai-redaction.png" width="450" alt="AI prompt preview with a password and a token shown as REDACTED"/></a> | <a href="assets/logshed-ai-analysis.png"><img src="assets/logshed-ai-analysis.png" width="450" alt="AI diagnosis with fix commands and a follow-up question"/></a> |
| *Check the exact prompt before it is sent: passwords and tokens already read `[REDACTED]`* | *Root cause and fix commands from the model you choose, then follow-up questions on the same logs* |
| **Alert Rules** | **Maintenance Windows** |
| <a href="assets/logshed-alert-rules.png"><img src="assets/logshed-alert-rules.png" width="450" alt="Alert rules list with threshold and pattern rules"/></a> | <a href="assets/logshed-maintenance.png"><img src="assets/logshed-maintenance.png" width="450" alt="Two active maintenance sessions, one limited to a single host, and the recurring schedules"/></a> |
| *Threshold and pattern rules with AI enrichment, firing counts and drag-and-drop ordering* | *Overlapping sessions from the web console, Home Assistant, the API or MCP, for every source or one host or app, plus recurring schedules* |
| **Home Assistant** | **Mobile Console** |
| <a href="assets/logshed-ha-dashboard.png"><img src="assets/logshed-ha-dashboard.png" height="467" alt="Home Assistant dashboard with LogShed sensors"/></a> | <a href="assets/logshed-mobile.png"><img src="assets/logshed-mobile.png" height="467" alt="Mobile console"/></a> |
| *Error counts, alert state and a maintenance switch with the [LogShed Home Assistant integration](https://github.com/logshed/homeassistant-logshed)* | *Touch-friendly console for checking on things from a phone* |
| **Storage & Retention** | **Drop Rules** |
| <a href="assets/logshed-storage.png"><img src="assets/logshed-storage.png" width="450" alt="Storage page with the daily log files and their expiry dates, and two database backups"/></a> | <a href="assets/logshed-drop-rules.png"><img src="assets/logshed-drop-rules.png" width="450" alt="Drop rules with the number of logs each one has dropped"/></a> |
| *One file per day of logs, deleted whole when it expires, and database backups before upgrades or on demand* | *Drop noisy lines before they are stored, with a count for each rule* |

---

## Documentation

Step-by-step setup guides and references live in [`docs/`](docs/):

| Guide | Description |
|---|---|
| [Forwarding Logs Guide](docs/SENDING_LOGS.md) | Syslog setup for OPNsense, Proxmox VE, Synology DSM, UniFi, pfSense, Home Assistant, Linux, and Docker, plus the [HTTP ingest API](docs/SENDING_LOGS.md#http-ingest-api) and [what the navbar health figures mean](docs/SENDING_LOGS.md#reading-the-navbar-health-figures) |
| [Configuration Reference](docs/CONFIGURATION.md) | Environment variables, Docker socket proxy setup, and storage paths |
| [Rules Guide](docs/RULES_GUIDE.md) | Threshold, pattern, and rate-spike alert rules, plus ingestion drop rules |
| [Model Context Protocol (MCP) Guide](docs/MCP_GUIDE.md) | Connect Claude Desktop, Cursor, Windsurf, or Antigravity to query logs and manage maintenance |
| [API Token Scopes Guide](docs/API_SCOPES.md) | Token presets, scopes, and permissions for the `/api/v1` REST API |
| [Home Assistant Integration](https://github.com/logshed/homeassistant-logshed) | Marketplace (or HACS) installation, entities, actions, and automation examples |
| [Changelog](CHANGELOG.md) | Release history and [upgrade notes](CHANGELOG.md#upgrade-notes) |
| [Technical Specification](docs/SPEC.md) | Architecture, database schema, and [REST API specification](docs/spec/api.md) |

---

## System Requirements

- **Docker Engine**: 24.0+ (`linux/amd64` or `linux/arm64`)
- **Storage for `/data`**: A local filesystem (ext4, btrfs, zfs, or an Unraid SSD cache pool).

> [!WARNING]
> **Keep `/data` on local storage:** Do not place `/data` on network shares (NFS, SMB) or Unraid FUSE user shares (`/mnt/user/`). SQLite WAL mode requires local filesystem locking and shared memory (`mmap`).

---

## Performance

Measured on LogShed 1.4.0 development builds, running on one core (two threads) of an Intel Core i3-12100 on Unraid:

| Measure | Result |
|---|---|
| **Receiving logs** (UDP or TCP) | 10,000 logs per second for 5 minutes with none lost, while the web interface returned the newest logs in a median of 10 to 11 ms (95% within 35 ms) |
| **50 million logs over 30 days** | 21.7 GiB on disk; full-text searches across all of them in about 40 to 50 ms; a count of every log in under 30 ms |
| **Retention** | A day of 1.7 million logs deleted in 0.35 s, with its disk space back at once |
| **Upgrade to 1.4.0** | A 50-million-log database upgraded in about 8 s |
| **Disk** | About 52 MB per 100,000 real homelab logs, full-text search index included |
| **Memory** | About 240 MB |

The load tests sent from another machine on the same network. The 50-million-log figures use generated logs; the disk figure comes from a copy of a real homelab's logs. Searches over 50 million logs are the median of three warm runs. 10,000 logs per second is close to the most one core stores, so it is the rate that was tested, not a limit with room to spare.

---

## Built With

LogShed runs as a single process with Python 3.14, FastAPI, Uvicorn, and SQLite (WAL mode + FTS5 full-text search) on the backend, and React 19, Vite, and Tailwind CSS on the frontend. Notifications use [Apprise](https://github.com/caronc/apprise). See [docs/SPEC.md](docs/SPEC.md) for architecture details.

---

## Local Development

Requires **Python 3.14+** and **Node.js 24+**:

```bash
# 1. Clone and set up the Python virtual environment
git clone https://github.com/logshed/logshed.git
cd logshed
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt

# 2. Install frontend dependencies, lint, run tests, and build static assets
cd frontend
npm install
npm run lint
npm test
npm run build
cd ..

# 3. Run backend tests and start the dev server
.venv/bin/pytest backend/tests/ -v
DATA_DIR=./data python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8080 --reload --no-proxy-headers
```

---

## Support the Project

LogShed is free and maintained in spare time. If it is useful to you, you can support it through [GitHub Sponsors](https://github.com/sponsors/BenHornerTech) or [Buy Me a Coffee](https://buymeacoffee.com/benhornertech).

---

## Licensing

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.
