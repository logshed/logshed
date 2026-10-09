# Specification: Deployment & Container Specification

Parent document: [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md)

## 1. Process Model & Runtime Architecture
LogShed executes as a single container, single OS process application running multiple concurrent `asyncio` tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`) under supervisor isolation. The application runs strictly with a single Uvicorn worker (`--workers 1`) because in-memory ingestion queues, rate limiters, and drop counters are process-local and would silently desync across separate OS processes.

## 2. Dockerfile Requirements

### 2.1 Multi-Stage Build Stages
* **Stage 1 (Frontend Builder):**
  * Base Image: `node:20-alpine` (`--platform=$BUILDPLATFORM`)
  * Working Directory: `/frontend`
  * Action: Installs npm dependencies and compiles React SPA (`npm run build`), emitting output directly to static assets directory.
* **Stage 2 (Runtime Environment):**
  * Base Image: `python:3.12-slim`
  * System Packages: `tini` (init process), `curl` (container health checks), `gosu` (privilege dropping), and `procps` (process table queries).
  * Backend Dependencies: Installed via `pip install --no-cache-dir -r /app/backend/requirements.txt`.
  * Asset Integration: Compiled static assets from Stage 1 copied into `/app/backend/app/static`.

### 2.2 Healthcheck
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/api/health || exit 1
```

### 2.3 Network Ports
* `8080/tcp`: Web UI and REST API (configurable via `PORT` environment variable).
* `1514/udp` and `1514/tcp`: Syslog ingestion listeners (configurable via `SYSLOG_PORT` environment variable).

### 2.4 Volume Mount Points
* `/data`: SQLite database (`logs.db`, WAL, SHM), master secret key (`.secret_key`), and preset directories.
* `/var/run/docker.sock`: Optional host Docker daemon socket for local container log collection (omitted when connecting via socket proxy or remote syslog).

## 3. Container Startup & Privilege Dropping (`entrypoint.sh`)

### 3.1 Non-Root User & Group Resolution
* LogShed drops root privileges via `gosu` to run as an unprivileged user defined by `PUID` and `PGID` environment variables (defaulting to `1000:1000`).
* `entrypoint.sh` checks for existing users or groups matching `PUID`/`PGID` and dynamically maps or creates the `appuser` account and group.

### 3.2 Dynamic Docker Socket Permissions
* When `/var/run/docker.sock` is mounted into the container:
  1. The entrypoint detects the socket filesystem path from `DOCKER_HOST` (defaulting to `/var/run/docker.sock`).
  2. The entrypoint inspects the numeric GID of the socket via `stat`.
  3. If the socket GID is `0`, `appuser` is added to the `root` group.
  4. If the socket GID belongs to another group, the entrypoint dynamically creates `docker-sock-group` with that GID if missing and appends `appuser` to that group.
  5. This guarantees that `appuser` can stream Docker events without requiring root privileges.

### 3.3 Storage Ownership Management
* The entrypoint creates preset storage directories (`/data/presets/alerts` and `/data/presets/drops`).
* Ownership of `/data` is checked against `$PUID:$PGID`. If unaligned, recursive ownership correction (`chown -R appuser:appuser /data`) is applied.

### 3.4 Process Execution Handoff
* Process handoff executes via `gosu`:
```sh
if [ $# -gt 0 ]; then
    exec gosu appuser tini -- "$@"
else
    exec gosu appuser tini -- uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log
fi
```

## 4. Unraid Template Directives
* Map `/data` to direct cache-pool appdata: `/mnt/cache/appdata/logshed` (avoids Unraid FUSE `shfs` locking/mmap issues on SQLite WAL and prevents spinning up array parity disks).
* Map host port `1514` (UDP/TCP) to container `1514`.
* Template defaults to `PUID=99` and `PGID=100` (`nobody:users`) to match standard Unraid share permission conventions. The container `entrypoint.sh` automatically maps these IDs and adjusts `/data` ownership on startup.

## 5. Multi-Host & Remote Docker Deployment Architecture
LogShed supports heterogeneous, multi-host homelab configurations across bare metal, virtual machines, and multiple Docker hosts:

1. **Local Host (Direct Socket Mount):**
   Mount `/var/run/docker.sock:/var/run/docker.sock:ro` into the LogShed container. Default `DOCKER_HOST=unix:///var/run/docker.sock` and `DOCKER_SOURCE_ALIAS=docker`.

2. **Single Remote Docker Host (Socket Proxy):**
   Connect directly to a remote Docker daemon or Docker socket proxy (such as `tecnativa/docker-socket-proxy`) without mounting any local socket:
   ```env
   DOCKER_HOST=tcp://192.168.1.50:2375
   DOCKER_SOURCE_ALIAS=remote-docker
   ```

3. **Multi-Host Docker Environments (Syslog Forwarding):**
   For environments running containers across multiple nodes (e.g. Proxmox LXC/VMs, multiple physical servers), configure each remote Docker daemon's native syslog log driver in `/etc/docker/daemon.json` to forward container logs to LogShed on port `1514`:
   UDP:
   ```json
   {
     "log-driver": "syslog",
     "log-opts": {
       "syslog-address": "udp://<logshed-ip>:1514",
       "tag": "{{.Name}}"
     }
   }
   ```
   TCP:
   ```json
   {
     "log-driver": "syslog",
     "log-opts": {
       "syslog-address": "tcp://<logshed-ip>:1514",
       "tag": "{{.Name}}"
     }
   }
   ```
   The remote host's IP or hostname is automatically attributed by LogShed's Syslog collector, and the container name is mapped to `app_name` via the tag.
