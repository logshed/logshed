# Changelog

All notable changes to LogShed will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0-beta.1] - 2026-10-09

### Added
- **Rate Limit Headers & OpenAPI Bearer Scheme**: Token-authenticated REST and ingestion responses now return `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`; `429` responses add `Retry-After` (also on MCP). The OpenAPI document declares an HTTP Bearer security scheme so `/docs` offers an Authorize button.
- **External REST API (`/api/v1/*`)**: Comprehensive programmatic REST API for automated log queries, aggregation facets, incident history inspection, maintenance window control, system metrics telemetry, and storage compaction.
- **Bearer Token Authentication & Granular Access Scopes**: Secure Bearer token authentication (`ls_live_<40 hex>`) with SHA-256 hash storage, namespace wildcard permissions (`maintenance:*`, `logs:read`, `metrics:read`, `incidents:read`, `system:admin`), sliding-window rate limiting (120 req/min), last-used tracking, and full management UI under Settings > API & MCP Access.
- **Model Context Protocol (MCP) Server**: Built-in FastMCP server enabling external AI assistants (such as Claude Desktop, Cursor, Antigravity, and Windsurf) to investigate homelab issues directly. Exposes 8 operational tools (`get_system_metrics`, `list_services`, `query_logs`, `get_log_context`, `count_logs`, `get_recent_alerts`, `enable_maintenance`, `disable_maintenance`), 3 live resources (`logshed://system/metrics`, `logshed://services`, `logshed://alerts/active`), and a guided incident investigation prompt (`investigate_incident`).
- **MCP Network SSE, Streamable HTTP, and Local Stdio Transports**: Multi-transport support featuring standard Streamable HTTP directly at `/mcp` (and `/api/v1/mcp`) for modern MCP clients, remote network Server-Sent Events (`GET /api/v1/mcp/sse` + `POST /api/v1/mcp/messages`) authenticated via Bearer tokens or `mcp-proxy`, alongside direct local Docker standard input/output transport (`python -m app.cli mcp`).
- **Log Aggregation & Counting API (`GET /api/v1/logs/count`) and `count_logs` MCP Tool**: Fast and token-efficient aggregation tool and endpoint supporting grouping by severity, application, or source host, allowing AI assistants and scripts to verify error volume and trends without fetching full log payloads. App and source groupings accept `group_limit` (default 25, max 200) and report `truncated` when more groups exist.
- **Collapsible Client Configuration Guides in Web UI**: Three collapsible accordion configuration snippets in the web dashboard (Claude Desktop via remote `mcp-proxy`, Cursor / Windsurf / Antigravity via network SSE or Streamable HTTP, and Claude Desktop via local Docker stdio) with dedicated one-click copy buttons and parenthetical deployment notes.
- **Dedicated MCP User Guide**: Added comprehensive documentation in [`docs/MCP_GUIDE.md`](docs/MCP_GUIDE.md) detailing practical conversational homelab troubleshooting examples, client configurations, and security guardrails.
- **Concurrent Maintenance Sessions & Error Dropping**: Multi-session maintenance tracking supporting overlapping API-driven and web UI windows, per-session deactivation, unified concurrent session monitoring, and dual operational modes (`silence_alerts` to suppress notifications, and `drop_errors` to filter incoming error and critical logs during volatile maintenance routines).
- **Docker Container Label Aliasing & Display Resolution**: Priority-based resolution of Docker container display names (`app_name`) inspecting custom labels (`logshed.alias`, `logshed.name`), Docker Compose service metadata (`com.docker.compose.service`), Swarm service attributes, and raw container names, alongside chunked retroactive updates on historical log records without table lock contention.
- **Configurable Rule Ordering for Drop Rules and Alert Rules**: User-configurable evaluation priority ordering for ingestion drop rules (`drop_rules`) and alert rules (`alert_rules`) backed by SQLite schema migration v3 (`display_order`), dedicated REST reordering endpoints (`PUT /api/drop-rules/reorder`, `PUT /api/alerts/rules/reorder`), and in-memory engine synchronization.
- **Accessible & Drag-and-Drop Rule Reordering UI**: Desktop drag-and-drop reordering with grip handles alongside touch-friendly single-tap Up/Down chevron controls with WCAG-compliant touch targets on mobile devices.
- **Multi-Host Docker Collection (Remote Docker Daemons)**: Supervised `DockerCollectorPool` coordinating concurrent, isolated `DockerTailer` tasks across multiple local and remote Docker daemons (Unix domain socket, plain TCP via `tecnativa/docker-socket-proxy`, or mutual TLS), with per-endpoint latency metrics, connection health monitoring, and bootstrap support via `DOCKER_ADDITIONAL_HOSTS`.
- **Docker Endpoint Management & Connectivity Testing (`/api/docker/hosts`)**: REST API endpoints for Docker collection target CRUD and ad-hoc connection validation, featuring write-only encrypted at-rest storage for TLS private keys (`client_key`), client response redaction (`has_client_key: bool`), and server-side key re-use during ad-hoc connectivity tests without transmitting keys over the wire.
- **Unified Sources Management Hub (`/settings/sources`)**: Consolidated Sources management panel bringing Syslog Host Aliases and Configured Docker Hosts into unified, protocol-labeled cards with dedicated modal forms for adding and editing aliases, alongside automatic redirects from `/settings/aliases`.
- **Protocol-Aware Host Actions in Log Details**: Context-sensitive host management buttons in the log inspection drawer that distinguish Syslog and Docker sources, dynamically offering Edit Docker Host, Add or Modify Syslog Host Alias, or a guided reconfiguration modal when inspecting historical logs from deleted Docker endpoints.
- **Protocol-Decoupled Host Filtering & Facet Scoping**: Protocol-isolated query filtering, real-time Server-Sent Events (SSE) log streaming, and database facet discovery that strictly decouple Docker container logs from Syslog host logs when both sources originate from the same host IP address.
- **Maintenance Window Audit History**: Every on-demand maintenance session (web UI, API token, MCP) now records an incident history entry with start time, end (or early-termination) time, trigger (UI, MCP, or "API - <token name>"), type (silence alerts or drop errors), target app and host, and a count of dropped logs, shown in a dedicated maintenance view in the Rules & History tab and exposed through `/api/v1/alerts/history` and the `get_recent_alerts` MCP tool. Sessions are limited to 20 concurrent windows with 255-character target filters, and ended sessions follow log retention for pruning.

### Changed
- **Alert History Follows Retention**: The daily cleanup and manual purge now delete all alert history (alert firings, daily digests, on-demand analyses and maintenance windows) older than the configured retention, together with their linked AI audit records. Maintenance windows age out by their end time, and running windows are never deleted. Prune results report logs, history and AI audit records separately, and `GET /api/maintenance/prune/preview` returns the counts a prune would delete before anything is removed.
- **Consolidated Docker Configuration into Sources Hub**: Migrated Docker collection switches, exclude container patterns, and source alias settings from the Advanced Settings tab into the dedicated Configured Docker Hosts interface under Sources (`/settings/sources`), maintaining endpoint ID 1 synchronization with core system settings.
- **MCP Token Efficiency & Compact Payloads**: Default `include_raw: false` in `query_logs` and `get_log_context` (omitting duplicate raw syslog messages, facilities, and ingest timestamps to reduce context window usage by ~40-50%), client-configurable field projections (`fields: ["timestamp", "app_name", "message"]`), and human-readable severity label annotations (`severity_label`).
- **Ergonomic Shorthand Query Filters**: Human-friendly relative time bounds (such as `"15m"`, `"1h"`, `"24h"`, `"since_midnight"`) and named RFC 5424 severity strings (`"emergency"`, `"critical"`, `"error"`, `"warning"`, `"info"`, `"debug"`) accepted seamlessly across both the external REST API and MCP tools.
- **API Tab Renamed to "API & MCP Access"**: Consolidated API token management and Model Context Protocol controls under a renamed **API & MCP Access** tab in Settings (`/settings/api`), styled with uppercase tracking card headers and blue accent icons aligned with the Host Alias Manager design pattern.
- **Default-Off MCP Security Baseline**: Set the Model Context Protocol master runtime toggle (`enable_mcp`, env `ENABLE_MCP`) to off by default, returning `503 Service Unavailable` when disabled to prevent unintended external access without explicit operator activation.
- **Prune Vestigial Log Sampling in Daily Digest**: Pruned an unused query and dead `sample_lines` field in `compute_daily_digest_rollup()` that previously scanned up to 100 rows with a computed severity sort on every daily digest run.
- **Modular Technical Specifications**: Partitioned the monolithic `docs/SPEC.md` specification into domain-bounded modules under `docs/spec/` (`architecture.md`, `database.md`, `pipeline.md`, `ai-engine.md`, `security.md`, `api.md`, `frontend.md`, `deployment.md`) while maintaining `docs/SPEC.md` as a lightweight navigation hub. Streamlines documentation lookups and reduces token consumption when developing with AI coding assistants.

### Fixed
- **Prune and Vacuum Safety**: Prune and vacuum now share one in-progress lock across the web UI, external API and daily `PruneWorker` (concurrent attempts return `409`; the worker retries after 60 seconds). The 5-minute external API cooldown starts only after a successful run and uses a monotonic clock. The lock stays held until the operation finishes even if the client disconnects mid-run. Vacuum and prune failures are logged server-side and return a generic error message instead of exception text.
- **Keyset Pagination for External Log Queries**: `GET /api/v1/logs` and the MCP `query_logs` tool now page with an opaque `cursor` and return `next_cursor` (`null` on the last page), replacing `offset`. Deep pages no longer scan and discard skipped rows, and results stay stable while new logs arrive. Malformed cursors return `422`.
- **Metrics Efficiency**: `GET /api/v1/system/metrics` now shares one refresh between concurrent callers, merges the 24-hour and 1-hour error counts into a single query, and the metrics and storage overview endpoints reuse a storage snapshot for up to 15 seconds instead of counting every log row on each request. Response shapes are unchanged.
- **MCP SSE Endpoint Base URL Resolution**: Resolved the MCP Server-Sent Events URL and client configuration snippets against the configured `app_url` from Advanced settings, ensuring reverse-proxy and LAN hosts point to the operator's designated instance URL.

---

## [1.2.1] - 2026-10-08

### Added
- **Container Timezone Propagation to UI & AI Prompts**: Resolves the container's configured `TZ` environment variable (defaulting to UTC) to expose the IANA timezone name, local abbreviation (such as BST, EDT, or UTC), and minute offset via the system settings API.
- **Dual UTC & Local Timeline Annotations in AI Prompts**: Formats AI analysis prompt log lines with dual timestamps `[HH:MM:SS UTC (HH:MM:SS Local)]` alongside explicit timeline context in system instructions, ensuring AI root-cause diagnosis references match both UTC database records and local homelab container clocks without operator confusion.

### Fixed
- **Log Detail Modal Timestamp Discrepancy**: Corrected the timestamp offset in the Log Detail slide-over modal where raw UTC strings were rendered without local offset conversion. Structured metadata cards now display the formatted local/homelab timestamp with timezone abbreviation badge alongside an explicit secondary UTC reference line.
- **Surrounding Context Log Timestamps**: Surrounding context log snippets in the detail view are now formatted in the configured homelab timezone instead of displaying raw UTC slices.

---

## [1.2.0] - 2026-10-07

### Added
- **Universal Notification Targets (80+ Services via Apprise & Webhooks)**: Multi-channel alerting dispatcher supporting over 80 notification services (including Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email, and custom webhooks) using standard URL formats, with at-rest encryption and asynchronous background dispatch.
- **Real-Time Ingestion Alert Engine**: High-performance in-memory rule evaluation engine (`AlertEvaluator`) running directly inside the ingestion pipeline, supporting pattern matching, severity thresholds, multi-application filters, sliding-window event counts, and cooldown suppression.
- **Automated AI Incident Diagnosis**: Automated incident inspection triggered directly on alert events, generating structured summaries, culprit IP indicators, and remediation steps with multi-model failover and an application-level toggle to bypass AI features when not needed.
- **Anthropic Claude AI Provider**: Native integration for Anthropic Claude models via the official SDK, featuring automated token usage extraction, fault-tolerant error handling, and configurable reasoning budget support for Claude 3.7+ and Claude 4/5 models.
- **Log Storm & Velocity Spike Detection**: Dedicated rate alert rule type monitoring log velocity (logs per second) over sliding windows, with in-memory frequency inspection to identify culprit services, hosts, and repetitive log patterns during volume spikes.
- **Ingestion Drop Rules (Log Chatter Filtering)**: In-memory pre-filtering engine evaluating source hosts, container/application names, and message patterns to discard repetitive log noise before SQLite storage and FTS5 indexing, with drop counters and severity thresholds.
- **Global & Scheduled Maintenance Windows**: Maintenance window management to silence outgoing alert notifications during planned host updates or container restarts while continuing log ingestion, supporting recurring schedules and on-demand duration presets.
- **24-Hour Analytical Daily Digest**: Scheduled daily analytical rollup covering total logs ingested, storage deltas, top services, and error distributions, recorded directly in Incident History even without configured notification channels, and dispatched via push notifications with deep links to filtered log views.
- **Standalone Rule Presets & JSON Backup**: Ready-to-use canary alert presets (including SSH brute force, reverse-proxy auth floods, sudo escalation, kernel OOM events, and log storms) with single-click activation, alongside clean JSON export and import for rule sharing and backups.
- **Scoped Data Deletion & Database Compaction**: Targeted log deletion by host, application, time range, and search query, alongside an on-demand database compaction tool coordinating disk safety checks and SQLite VACUUM to reclaim storage.
- **Saved Filter Views & URL Sync**: Saved filter views on the Console filter bar and mobile drawer with pinned view support, 1-tap activation, and URL query parameter sync for bookmarking and sharing.
- **Log Inspection Chronological Navigation**: Next and Previous controls in the log detail modal to navigate through the active filtered log stream chronologically, plus quick shortcuts to create drop rules directly from log payloads.
- **Decoupled Asynchronous FTS5 Indexing**: Supervised background worker (`FTSIndexWorker`) decoupling SQLite full-text search indexing from raw ingestion, maintaining high-throughput ingestion without holding write locks during text indexing.

### Changed
- **Unified Rules & History Hub**: Consolidated Alert Rules, Ingestion Drop Rules, Historical Incidents, and Maintenance Schedules into a unified 4-tab hub with dedicated URL routes, responsive mobile layouts, local time formatting, and detailed incident history inspect views presenting prompt envelopes, model badges, and token consumption breakdowns.
- **Multi-Provider AI State Preservation & Live Model Refresh**: Isolated configuration states across AI providers (Google Gemini, OpenAI, Anthropic Claude, and OpenAI-Compatible), persisting each provider's encrypted credentials, primary model, custom base URL, and fallback sequence independently in database storage. Preserves configurations across provider switches without mixing model lists, supports live model refreshes with unsaved keys directly from the input, and consolidates missing API key alerts with inline retry actions.
- **Dynamic AI Model Discovery & Selection**: Broadened model family discovery for Google Gemini (including 4.x) and OpenAI (such as o-series reasoning models), excluded point-in-time dated snapshots and dedicated search API variants to present clean canonical model catalogs, and filtered non-text preview variants.
- **High-Throughput Chunked Batch Ingestion**: Upgraded queue batch inserts to use multi-row insert statements with returned generated IDs and thread-local read connection reuse to reduce SQLite lock contention and query overhead.
- **Frontend Code Splitting & Performance**: Route-level lazy loading for settings, storage, and rules panels (~362 kB initial bundle), alongside debounced search-as-you-type and throttled scroll listeners for smooth high-speed streaming.
- **Dynamic Host Alias Resolution**: Host aliases and IP addresses resolve dynamically across the UI, live SSE streams, and targeted deletion without requiring page reloads or unindexed table scans.
- **Streamlined Storage Management Panel**: Focused the Storage panel on disk metrics, retention policies, and database maintenance, moving incident and audit logs to the unified History hub.
- **AI Diagnostics & Structured Data Context**: Preserves RFC 5424 structured data blocks (such as OpenTelemetry context) and includes severity codes in AI prompt streams for accurate diagnostic context, while retaining raw model outputs in summary views when section headers are absent.
- **Database Migration Consolidation**: Streamlined SQLite `migrate_v2` upgrade path from v1.1.0 to v1.2.0 by defining final table schemas directly across `drop_rules`, `saved_views`, `notification_channels`, `alert_rules`, `alert_history`, and `fts_index_state`, removing redundant incremental schema alterations while preserving historical AI audit log backfill.
- **Comprehensive Documentation Suite**: Restructured documentation into dedicated guides covering alerting rules, noise drop rules, configuration variables, and syslog forwarding for Linux, Docker, OPNsense, Proxmox, pfSense, UniFi, Synology, and Home Assistant.

### Fixed
- **Search Performance & ReDoS Protection**: Evaluated FTS queries via subquery to prevent quadratic table scans, enforced bounded regex search execution timeout (default 0.1s) and 16,384-character bounds, permitted safe disjoint alternations, and added substring pre-matching to guard against catastrophic backtracking.
- **Collector Stream Integrity**: Syslog TCP octet-counting framing verification, persistent line buffering across Docker multiplexed stream chunks, and parsing support for logfmt, Redis/Valkey, and Maintainerr structured payloads.
- **Mobile & UI Polish**: Added mobile clipboard fallbacks, expanded touch targets, unsaved changes warnings in modal dialogs, fixed storage trend chart Y-axis formatting, and retained consistent left borders across log stream rows and desktop headers to eliminate horizontal shifts during checkbox selection.

### Security
- **SSRF & Webhook Hardening**: Comprehensive destination validation for webhook targets, blocking loopback, Docker socket, private ranges, cloud metadata IPs, and outbound HTTP redirects to internal hosts.
- **Automatic Secret Redaction in Outbound Alerts**: Sample log snippets, alert titles, and AI diagnosis summaries pass through the secret redactor before outbound notification dispatch to prevent credential leaks.
- **Subsecond Session Revocation**: Immediate session revocation upon credential updates and verified client IP checking against trusted proxies for secure cookie flags.

### Removed
- **Legacy Modules & Deprecated Endpoints**: Removed deprecated service module `security_presets.py`, superseded standalone AI audit API wrappers and interfaces in favor of unified history, and added migration notices for instances running from the deprecated personal container registry.

---

## [1.1.0] - 2026-09-16

### Added
- **AI Fast Failover**: Automatic multi-provider failover across secondary models when encountering rate limits, timeouts, or downtime.
- **Dynamic AI Model Discovery**: Dynamic discovery and caching of available models for Google Gemini, OpenAI, and Ollama/compatible endpoints.
- **Reasoning / Thinking Budget**: Configurable reasoning token budget for extended thinking models (e.g. Gemini Thinking, OpenAI reasoning).
- **Live Streaming AI Diagnosis**: Real-time SSE streaming for AI diagnosis tokens and progress updates in the analysis modal.
- **AI Diagnosis Rate Limiting**: In-memory sliding-window rate limiter (10 req/min per session) on AI diagnosis endpoints to prevent runaway API usage.
- **Storage Management Panel**: Dedicated tab for database metrics, WAL checkpoint status, manual VACUUM, and retention pruning settings.
- **Responsive Mobile Layout**: Mobile navigation drawer, responsive search/filter controls, touch-friendly card layouts, and pull-to-refresh.
- **Browser History & URL Routing**: Deep-linking and back/forward navigation across Console, Host Aliases, Storage, and Settings tabs via HTML5 History API.
- **Update Checks & Notifications**: Background checks against GitHub Container Registry for new stable releases, with a toggle in Settings and a navbar update notification badge.
- **Settings "About LogShed"**: Added an About card in Settings displaying the installed version, copyright information, documentation links, and update status.
- **Unsaved Changes Guard**: Confirmation prompt when navigating away from Settings with unsaved changes, plus `beforeunload` browser tab protection.
- **Sticky Settings Action Bar**: Floating action bar in Settings with save/discard controls and real-time status feedback.
- **Expanded Ingestion Formats**: Added parser support for ISO 8601, slash dates, comma-separated milliseconds, named timezones, and content-based severity promotion.
- **Expanded Secret Redactor Patterns**: Added detection and scrubbing for Slack webhooks, generic secret assignments, `sshpass` flags, and Docker registry auth tokens.
- **CSRF Request Protection**: Custom request header validation middleware protecting mutating API endpoints against Cross-Site Request Forgery.
- **Secure CLI Password Prompt**: Made the `--password` flag optional in `reset-admin` CLI, falling back to secure terminal prompting via `getpass`.
- **Automated Release Publishing**: Workflow integration to extract release notes from CHANGELOG.md and publish GitHub releases upon stable tag pushes.

### Changed
- **Log Stream Rendering Performance**: Precompiled search patterns, cached virtual row rendering, and precomputed timestamp formatting to eliminate lag during high-frequency log streams.
- **Log Stream Display Cleaning**: Stripped redundant timestamps, duplicate severity prefixes, and timezone noise from log view while preserving raw payloads.
- **Database & Query Improvements**: Switched queue consumer to a persistent SQLite connection and streamlined recursive CTE queries for facet filtering.
- **Decoupled AI Service Architecture**: Extracted a dedicated AI service layer handling context preparation, token accounting, and multi-provider dispatching.
- **Navigation Structure**: Separated Settings and Storage into dedicated navigation tabs.
- **AI Modal Compact Settings**: Collapsed model selection into an accordion to give more screen space to log context and analysis.
- **Retention Override Safeguards**: Locked retention slider with an advisory badge when `MAX_RETENTION_DAYS` is set via environment variable, automatically clamping down to 30 days if the variable is removed.
- **Internal Log Ingestion**: Stored internal application logs unredacted in SQLite per specification, preserving raw data while redacting on-demand for AI prompts.
- **Terminal Escape Sequence Stripping**: Stripped ANSI escape codes and formatting sequences from Docker log streams.
- **Typography Consistency**: Replaced em dashes with standard hyphens across all documentation and comments.
- **Documentation & Architecture Alignment**: Aligned technical specifications, LLM instructions, environment configuration template, and README with the v1.1.0 application architecture.

### Fixed
- **Custom Time Filter Timezone Alignment**: Formatted datetime-local input values in client-local time instead of UTC slicing, ensuring time values selected in the browser's native picker match the displayed input text across non-UTC client timezones.
- **Syslog TCP Persistent Connections & Resource Limits**: Enabled TCP keepalive (`SO_KEEPALIVE`) on accepted client sockets, disabled TCP inactivity timeout by default (`SYSLOG_TCP_INACTIVITY_TIMEOUT=0`), and raised concurrent Syslog TCP connection limit to 250 (`SYSLOG_MAX_TCP_CONNECTIONS`).
- **Syslog Multiline Traceback Assembly**: Fixed multi-line Python tracebacks over UDP and TCP splitting into separate records or being misattributed to `unknown`.
- **Multiline Assembler Buffer Bounds**: Enforced stream memory caps (500 lines / 256 KB) and a 5-second lifetime limit to prevent unbounded buffer growth on unclosed multiline streams.
- **Docker TTY Buffer Limit**: Capped TTY stream buffers to 64 KB with warning truncation when container output lacks newlines.
- **RFC 5424 Syslog Parser Loop**: Fixed infinite loop when syslog structured data contains unclosed brackets.
- **FTS5 Search Query Validation**: Robust parsing, quoting, and syntax fallback for search-as-you-type and column filters, eliminating SQLite operational errors on partial queries.
- **Password Change Re-Authentication**: Cleared session cookies on admin password change and triggered an immediate login redirect with notice banner instead of an orphaned session.
- **Mobile Layout & Form Usability**: Prevented mobile browsers from auto-zooming on input focus, prevented quick filter pill wrapping on narrow screens, and auto-reset single-log selection on mobile modal close.
- **AI Modal Prompt Cursor Jumping**: Fixed cursor jumping to the end of the textarea when editing the first line in Full Prompt mode.
- **Login Form Password Selection**: Automatically highlights and selects password field contents on failed login for quick retyping.
- **Secret Redactor Special Characters**: Fixed connection string regex to handle passwords containing `@` symbols.
- **API Parameter Validation**: Added ISO-8601 validation for log query datetimes and IP format validation for host alias creation.
- **Password Verification Concurrency**: Offloaded Argon2id verification to worker threads to keep authentication from blocking the asyncio event loop.
- **Model Filtering**: Filtered out non-text models from AI model selection dropdowns.

---

## [1.0.0] - 2026-09-08

### Added
- Initial release of LogShed: lightweight, single-process log aggregator tailored for homelab environments.
- Dual ingestion engine:
  - Syslog server (UDP & TCP on port 1514) supporting RFC 3164 (BSD) and RFC 5424 (IETF) standards with transparent and octet-counted framing.
  - Docker Engine API collector streaming logs directly from `/var/run/docker.sock` (or TCP proxy).
- SQLite storage backend running in Write-Ahead Logging (WAL) mode with external-content FTS5 full-text search indexing.
- Keyed multi-line stream assembler grouping Python tracebacks and Java exceptions by source stream into single coherent log rows.
- Fast real-time log dashboard with virtual scrolling, built with React 18, Vite, and Tailwind CSS.
- Multi-facet host and application filtering with full-text search syntax (`severity:error`, prefix wildcard queries).
- On-demand AI root-cause analysis supporting Google Gemini, OpenAI, and local LLMs (Ollama / vLLM / LocalAI).
- Automatic client-side and server-side credential/secret redaction (passwords, tokens, API keys, IP addresses) before AI dispatch.
- Dynamic Host Alias Manager allowing friendly IP-to-hostname mappings with retroactive updates across existing database rows.
- Automated daily database pruning worker with configurable log retention periods (default: 14 days, up to 365 days) and freelist reuse.
- Native multi-architecture container images (`linux/amd64` and `linux/arm64`).
- Unraid Community Applications template (`unraid-template.xml`) with cache-pool POSIX locking recommendations.

[1.3.0-beta.1]: https://github.com/logshed/logshed/compare/v1.2.1...v1.3.0-beta.1
[1.2.1]: https://github.com/logshed/logshed/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/logshed/logshed/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/logshed/logshed/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/logshed/logshed/releases/tag/v1.0.0


