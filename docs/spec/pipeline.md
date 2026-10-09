# Specification: Ingestion & In-Memory Pipeline

> This document is part of the modular LogShed technical specification suite. For the full system specification, see [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md).

```
[Syslog Listener (UDP/TCP 1514)] ──┐
├──> [Keyed Multiline Assembler] ──> [asyncio.Queue(10000)] ──> [SQLite Batch Writer (stdlib sqlite3 + asyncio.to_thread)]
[Docker Tailer (Socket/Proxy)]  ──┘
```

The LogShed ingestion pipeline ingests high-volume syslog and Docker container logs, buffers them in an asynchronous in-memory queue, and persists them into SQLite using batched writes with sub-second full-text search indexing.

---

## 1. Syslog Ingestion (`SyslogServer`)

- **Implementation Reference:** [backend/app/collectors/syslog.py](file:///home/ben/workspace/logshed/backend/app/collectors/syslog.py)
- **Protocols & Ports:** Asynchronous UDP and TCP listeners on port `1514` (configurable via `SYSLOG_PORT`).
- **Parsing Standards:** Complete parsing for RFC 3164 (BSD syslog) and RFC 5424 (IETF syslog with structured data).
- **TCP Framing:** Implements RFC 6587 octet-counted framing and newline-delimited framing for stream separation.
- **Connection Ceilings:** Default TCP connection ceiling is 250 concurrent connections (configurable via `SYSLOG_MAX_TCP_CONNECTIONS`). TCP keepalive is enabled (`SO_KEEPALIVE`).
- **TCP Inactivity Timeout:** Defaults to disabled (`0.0`, configurable via `SYSLOG_TCP_INACTIVITY_TIMEOUT`) to prevent disconnection of persistent log forwarders during idle periods.
- **Fallback Severity:** Unparseable messages default to severity 6 (Info) while preserving the complete raw message content.

---

## 2. Docker Ingestion (`DockerTailer`)

- **Implementation Reference:** [backend/app/collectors/docker_collector.py](file:///home/ben/workspace/logshed/backend/app/collectors/docker_collector.py)
- **Endpoint Connectivity:** Connects through the Docker Engine API via `DOCKER_HOST` environment variable (`unix:///var/run/docker.sock` or `tcp://proxy:2375` for `tecnativa/docker-socket-proxy`). Connects using `httpx` with Unix domain socket transport (`httpx.HTTPTransport(uds=...)`) or standard HTTP proxy transport without external Docker SDKs.
- **Log Streaming & Event Monitoring:** Tails running container stdout and stderr streams while listening for Docker lifecycle events (`start` and `die`).
- **Metadata Tagging:** Assigns `source_alias="docker"` (or the configured value of `DOCKER_SOURCE_ALIAS`) and `app_name=container_name`.
- **Reconnection Backoff:** Implements automated exponential backoff for socket reconnects:
  - Supervisor backoff: Initial backoff of `1.0s`, doubling on consecutive connection errors up to a maximum ceiling of `60.0s` (`DOCKER_SOCKET_POLL_MAX`), resetting to `1.0s` on successful connection.
  - Container tailer backoff: Initial backoff of `1.0s`, doubling up to `15.0s`, automatically reconnecting when containers restart.

---

## 3. Keyed Multiline Assembler

- **Buffer Mapping:** Buffers continuation lines (such as lines starting with whitespace, `\t`, `Caused by:`, `Traceback`) mapped by stream key:
  - Syslog streams: `stream_key = f"{source_ip}:{app_name}"`
  - Docker streams: `stream_key = f"docker:{container_id}"`
- **Flush Triggers:** Flushes buffered lines into a single combined log entry upon either:
  - A **150ms per-stream timeout** window expiring without new continuation fragments.
  - Receipt of a new RFC-compliant timestamped header for that stream key.

---

## 4. Raw Log Storage

- Logs are committed to SQLite in their original unredacted format.
- Redaction is deliberately not applied during ingestion so that raw operational audit data remains intact in the database. Redaction occurs strictly on-demand before external LLM dispatch.

---

## 5. Bounded Buffer & In-Memory Pipeline (`QueueConsumer`)

- **Implementation Reference:** [backend/app/core/pipeline.py](file:///home/ben/workspace/logshed/backend/app/core/pipeline.py)
- **Bounded In-Memory Buffer:** `asyncio.Queue(maxsize=10000)`.
- **Backpressure & Drop Handling:** If the in-memory queue reaches saturation (10,000 items), incoming logs are rejected and the atomic `dropped_logs_total` counter increments, protecting system memory and the event loop from unbounded ingestion spikes.
- **Debounced Batch Flusher:** Flushes batches to SQLite after a **50ms debounce window** or as soon as the batch accumulates 5,000 records. Drains pending items aggressively using non-blocking `queue.get_nowait()` on each cycle to boost throughput, eliminate artificial bottlenecks, and supply low-latency real-time streaming to UI and SSE subscribers.
- **Chunked Batch Inserts with RETURNING id:**
  - Inside `QueueConsumer._insert_batch`, drained items are divided into chunks of <= 500 records to remain well within SQLite parameter boundaries (9 columns * 500 = 4,500 parameters << 32,766 limit).
  - Each chunk is inserted using bound parameters in a single multi-row `INSERT INTO logs (...) VALUES (...), ... RETURNING id` statement.
  - Database auto-increment IDs returned from SQLite are mapped sequentially to in-memory entries.
  - Drained entries are broadcast to live Server-Sent Events (SSE) subscribers (`/api/logs/stream`).
  - Immediately following transaction commit, `fts_indexer.notify_new_logs()` is triggered via an internal `asyncio.Event` to ensure sub-second FTS search catch-up (target SLA <= 1000ms).
- **Ingestion Drop Filtering Evaluation Order:**
  - Drop rules evaluate in user-configured sequence (`ORDER BY display_order ASC, id ASC`).
  - Evaluation short-circuits upon the first matching rule, immediately discarding the incoming log line and attributing the drop counter to that specific rule.
- **Alert Rules Evaluation Order:**
  - Alert rules evaluate in user-configured sequence (`ORDER BY display_order ASC, id ASC`).
  - Each rule evaluates within its own sliding window, and notification dispatch within a batch deterministically follows this configured order.
