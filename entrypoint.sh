#!/bin/sh
set -e

# Read PUID and PGID, default to 1000
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Warn loudly when the app would run as root. It still starts, as in 1.2.x, but a later release
# will refuse to start with PUID or PGID set to 0.
if [ "$PUID" = "0" ]; then
    echo "WARNING: PUID=0 runs LogShed as root inside the container. Set PUID and PGID to a non-root user (for example 1000). A later release will refuse to start with PUID or PGID set to 0." >&2
fi
if [ "$PGID" = "0" ]; then
    echo "WARNING: PGID=0 runs LogShed with the root group inside the container. Set PUID and PGID to a non-root user (for example 1000). A later release will refuse to start with PUID or PGID set to 0." >&2
fi

# Update or create appuser group with PGID
if getent group appuser >/dev/null 2>&1; then
    groupmod -o -g "$PGID" appuser
elif getent group "$PGID" >/dev/null 2>&1; then
    groupmod -o -g "$PGID" appuser 2>/dev/null || true
else
    groupadd -o -g "$PGID" appuser
fi

# Update or create appuser user with PUID and PGID
if getent passwd appuser >/dev/null 2>&1; then
    usermod -o -u "$PUID" -g "$PGID" appuser
elif getent passwd "$PUID" >/dev/null 2>&1; then
    usermod -o -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
else
    useradd -o -u "$PUID" -g "$PGID" -d /app -s /bin/sh appuser
fi

# Dynamically detect Docker socket path from DOCKER_HOST (fallback to /var/run/docker.sock)
DOCKER_SOCKET_PATH="/var/run/docker.sock"
if [ -n "$DOCKER_HOST" ]; then
    case "$DOCKER_HOST" in
        unix://*)
            DOCKER_SOCKET_PATH="${DOCKER_HOST#unix://}"
            ;;
    esac
fi

# Detect socket GID and grant appuser access
if [ -S "$DOCKER_SOCKET_PATH" ] || [ -e "$DOCKER_SOCKET_PATH" ]; then
    DOCKER_GID=$(stat -c '%g' "$DOCKER_SOCKET_PATH" 2>/dev/null || stat -f '%g' "$DOCKER_SOCKET_PATH" 2>/dev/null)
    if [ "$DOCKER_GID" = "0" ]; then
        usermod -aG root appuser 2>/dev/null || true
        echo "WARNING: Docker socket is owned by group 0 (root), so appuser was added to the root group to read it. Prefer a Docker socket proxy (such as tecnativa/docker-socket-proxy) via DOCKER_HOST." >&2
    elif [ -n "$DOCKER_GID" ]; then
        if ! getent group "$DOCKER_GID" >/dev/null 2>&1; then
            groupadd -o -g "$DOCKER_GID" docker-sock-group 2>/dev/null || true
        fi
        DOCKER_GROUP=$(getent group "$DOCKER_GID" | cut -d: -f1)
        if [ -n "$DOCKER_GROUP" ]; then
            usermod -aG "$DOCKER_GROUP" appuser 2>/dev/null || true
        fi
    fi
fi

# Ensure /data and preset directories exist and are owned recursively by appuser
mkdir -p /data/presets/alerts /data/presets/drops
CURRENT_OWNER=$(stat -c '%u:%g' /data 2>/dev/null || stat -f '%u:%g' /data 2>/dev/null)
if [ "$CURRENT_OWNER" != "$PUID:$PGID" ]; then
    chown -R appuser:appuser /data 2>/dev/null || true
fi
chown -R appuser:appuser /data 2>/dev/null || true

# Keep new files (logs.db, -wal, -shm) unreadable by other users on the host
umask 027

# Default port
PORT=${PORT:-8080}

# Execute custom command or default single-worker uvicorn process with gosu privilege drop
if [ $# -gt 0 ]; then
    exec gosu appuser tini -- "$@"
else
    exec gosu appuser tini -- uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log
fi
