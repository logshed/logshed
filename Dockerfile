# ==========================================
# Stage 1: Build React Frontend
# ==========================================
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-builder
WORKDIR /frontend

# Install dependencies
COPY frontend/package*.json ./
RUN npm ci

# Copy frontend source and compile SPA (outputs to ../backend/app/static)
COPY frontend/ ./
RUN npm run build

# ==========================================
# Stage 2: Python 3.12 Runtime
# ==========================================
FROM python:3.12-slim

# Install system utilities: tini (init), curl (healthchecks), gosu (privilege drop), procps
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    gosu \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Set runtime environment variables
ARG LOGSHED_IMAGE_REPO=logshed/logshed
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend \
    PORT=8080 \
    PUID=1000 \
    PGID=1000 \
    LOGSHED_IMAGE_REPO=${LOGSHED_IMAGE_REPO}

# Set application directory
WORKDIR /app/backend

# Install Python backend dependencies
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy backend source code
COPY backend/ /app/backend/

# Copy compiled frontend assets from Stage 1 into FastAPI static directory
COPY --from=frontend-builder /backend/app/static /app/backend/app/static

# Setup default appuser and data mount point
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -d /app -s /bin/sh appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /app /data

# Copy and setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Persistence volume mount point
VOLUME ["/data"]

# Expose Web UI (8080/tcp) and Syslog ingestion (1514/tcp and 1514/udp)
EXPOSE 8080/tcp 1514/tcp 1514/udp

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/api/health || exit 1

# Start container through privilege-dropping entrypoint
ENTRYPOINT ["/entrypoint.sh"]
