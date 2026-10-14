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
- **Metadata Tagging & App Label Resolution:** Assigns `source_alias="docker"` (or the configured value of `DOCKER_SOURCE_ALIAS`) and resolves `app_name` using container labels and metadata in descending priority:
  1. `Labels["logshed.alias"]` or `Labels["logshed.name"]` (explicit user override)
  2. `Labels["com.docker.compose.service"]` (Docker Compose service name)
  3. `Labels["com.docker.swarm.service.name"]` (Docker Swarm service name)
  4. Primary container name (`Names[0]` or event `Attributes["name"]`)
  5. Fallback 12-character container ID (`cid[:12]`)
- **Retroactive App Alias Updates:** When an aliased container resolves to a display name differing from its raw container name, a background batch task updates historical log records (`source_ip='docker'` and `app_name=raw_name`) in chunked batches (1000 rows with brief pauses) to avoid table lock contention. Session deduplication prevents redundant database queries on periodic discovery loops.
- **Exclusion Matching:** `_should_ignore_container` evaluates raw container name, resolved display name, and container ID against `DOCKER_EXCLUDE_CONTAINERS`.
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

## 6. Internal Application Logs (`InternalLogHandler`)

- **Implementation Reference:** `InternalLogHandler` in [backend/app/core/pipeline.py](file:///home/ben/workspace/logshed/backend/app/core/pipeline.py), attached to the `app` logger at startup.
- **Threshold:** Records at or above the internal log level (`internal_log_level` setting or `LOGSHED_INTERNAL_LOG_LEVEL`, default `WARNING`, `DISABLED` turns ingestion off) are queued as logs from source `127.0.0.1` / `logshed` (or its host alias), with the last segment of the logger name as `app_name`. Drop rules and maintenance windows apply as for any other log.
- **Never Stored (loop and noise guards):**
  - `app.core.pipeline` and `app.core.sse`: the handler feeds the pipeline and every stored log is broadcast over SSE, so their logs would feed back into themselves.
  - `app.core.migrations`: runs at startup before the handler is attached.
  - `uvicorn.access`, `httpx`, `httpcore` and `asyncio`: outside the `app` logger, so they never reach the handler; listed as a guard against flooding if it is ever attached higher up.
- **Stored but Skipped by Alert Rules:** Logs from `app.services.alert_evaluator`, `app.services.notifier`, `app.services.ai_engine` and `app.services.ai_service` (and their child loggers) carry an internal `_skip_alert_rules` marker. `AlertEvaluator.evaluate_batch` drops marked entries before rule matching, so a rule cannot re-trigger itself through the alert, notification or AI enrichment logs its own firing writes. These logs are searchable and stream live like any other log.
- **Stored and Alertable:** All other `app.*` loggers, including retention pruning (`retention`) and storage metrics (`storage_metrics`).
- **Re-entrancy Guard:** A thread-local flag stops logging performed while emitting a record from being captured again.
