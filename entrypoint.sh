#!/bin/sh
set -e

# Read PUID and PGID, default to 1000
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Refuse to run the app as root unless explicitly allowed
if [ "$PUID" = "0" ] || [ "$PGID" = "0" ]; then
    if [ "$ALLOW_ROOT" != "true" ]; then
        [ "$PUID" = "0" ] && echo "ERROR: PUID=0 would run LogShed as root. Set PUID to a non-root user id, or set ALLOW_ROOT=true to override." >&2
        [ "$PGID" = "0" ] && echo "ERROR: PGID=0 would run LogShed as root. Set PGID to a non-root group id, or set ALLOW_ROOT=true to override." >&2
        exit 1
    fi
    echo "WARNING: ALLOW_ROOT=true, running LogShed with root ids." >&2
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
        echo "WARNING: Docker socket is owned by group 0 (root); not adding appuser to the root group. Use a Docker socket proxy (such as tecnativa/docker-socket-proxy) via DOCKER_HOST instead." >&2
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
