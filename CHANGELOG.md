# Changelog

All notable changes to LogShed will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **About LogShed Card Location**: Moved the About LogShed card from the Advanced tab to the bottom of the Application Settings tab.
- **Apprise Documentation Links**: Updated Apprise notification URL documentation links in the Add/Edit Target modal to point to the new official services and URL builder documentation on appriseit.com.

### Fixed
- **FTS Prefix Search with Relational Filter Query Plan**: Evaluated full-text search match queries as an uncorrelated subquery rather than an inner join in the log query API, preventing the SQLite query planner from driving scans via relational indexes and re-evaluating FTS match doclists for every candidate row.
- **Log Stream Search Debouncing & Loading Feedback**: Added a 300ms debounce to search-as-you-type input in the live log stream to prevent dispatching superseded backend queries on every keystroke, alongside an in-flight status indicator when refreshing active log views.
- **Daily Digest Severity Priority**: Prioritize entities by minimum event severity before event count when generating 24-hour daily digest analytical rollups. Ensures critical, alert, or emergency events are highlighted even when other hosts log higher volumes of standard errors.
- **Unsaved Changes Warning in Rule Modals**: Added unsaved changes protection to alert and drop rule modals when closing via header close button, cancel button, or escape key with pending edits.
- **Symmetric Host Alias & Case Expansion**: Symmetrically resolve configured host aliases and their underlying IP addresses bidirectionally and case-insensitively across log queries, live SSE streams, and targeted deletion filters. Ensures filtering or deleting by friendly alias (e.g. `LogShed Server`) consistently matches historical rows logged with alternative casing (`logshed`) or raw IP (`127.0.0.1`) without incurring unindexed SQLite table scans.

## [1.2.0-beta.1] - 2026-10-01

### Added
- **Universal Notification Targets (80+ Services via Apprise & Webhooks)**: Universal alerting dispatcher supporting over 80 notification services (including Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email, and custom webhooks) using standard URL formats. Features at-rest URL encryption using the application master key, token masking in the UI, and asynchronous dispatch via a dedicated worker pool.
- **Real-Time Ingestion Alert Engine**: High-performance in-memory rule compilation and batch evaluation engine (`AlertEvaluator`) running directly inside the ingestion pipeline. Supports regex and substring pattern matching, severity thresholds, multi-application filtering, sliding-window event counts, and configurable cooldown suppression to dampen alert flapping.
- **Automated AI Incident Diagnosis & Remediation**: Background analysis triggered automatically on alert events, generating structured incident summaries, culprit IP indicators, and actionable remediation guidance with automatic multi-model failover. Includes an app-level toggle in Settings to cleanly bypass AI features when not required.
- **Log Storm & Velocity Spike Detection**: Real-time rate alert rule type (`rate`) monitoring incoming log velocity (logs per second) over configurable sliding-window durations. Features zero-disk-I/O in-memory frequency analysis to immediately identify primary culprit services, containers, hosts, and repetitive log patterns during sudden log spikes.
- **Ingestion Drop Rules (Log Chatter Filtering)**: In-memory pre-filtering engine evaluating source hosts, container/application names, and message patterns (substring, regex, or wildcards) to discard repetitive log noise before SQLite persistence and FTS5 indexing. Includes configurable severity thresholds to selectively drop debug and info chatter while preserving warnings and errors, alongside in-memory drop counters with periodic SQLite flushing.
- **Global & Scheduled Maintenance Windows**: Maintenance window management to silence outgoing alert notifications during planned host updates or container restarts while continuing log ingestion and incident tracking. Supports on-demand windows with quick presets (+1h, +4h, +8h, +24h) and recurring schedules (daily, weekly, monthly) that handle midnight boundary crossings, accompanied by prominent UI status banners with 1-click deactivation.
- **24-Hour Analytical Daily Digest**: Scheduled daily analytical rollup covering total logs ingested, storage deltas, top services, and error distributions across hosts and Docker containers. Dispatches formatted push notifications via configured channels with direct deep links back to filtered LogShed views and logs past digests in the unified history log.
- **Standalone Rule Presets & Zero-Zip JSON Backup**: Pre-packaged canary alerts (including SSH brute force, reverse-proxy auth floods, unauthorized sudo escalation, kernel OOM events, and log storms) ready to activate with 1 click. Features clean JSON export and import for individual rules or complete backup bundles without archive compression, enabling effortless rule sharing across homelab setups.
- **Scoped Data Deletion & On-Demand Compaction**: Multi-tier log deletion enabling operators to remove single entries from the inspection modal, purge selected streams from the console, or execute scoped deletions on the Storage panel by host, app, time range, and search query. Complemented by an on-demand "Compact Database" action that coordinates temporary write buffering, disk headroom safety checks, and SQLite VACUUM to safely reclaim host storage.
- **Decoupled Asynchronous FTS5 Indexing Worker**: Dedicated background worker (`FTSIndexWorker`) decoupling SQLite full-text search indexing from raw ingestion. Allows sustained high-volume log ingestion without holding write locks during text indexing, maintaining target catch-up latency below 1000ms.
- **Saved Filter Views & URL Sync**: Saved filter views on the Console filter bar and mobile drawer with pinned view support, 1-tap activation, and bidirectional query parameter sync in browser URLs for bookmarking and sharing.
- **Log Inspection Chronological Navigation**: Next and Previous navigation controls in the log detail inspection modal to step through the active filtered log stream chronologically without closing the modal, alongside quick shortcuts to create drop rules directly from log payloads.
- **Dynamic Host Alias Stream Updates**: Live UI updates via React context that immediately update log rows, facet dropdowns, and quick filters when host aliases are saved or removed without requiring a full page reload, backed by retroactive background log updates.
- **Registry Migration Notice**: Automated container detection for instances running from the deprecated personal registry namespace (`ghcr.io/benhornertech/logshed`), displaying an informative banner and guidance to transition to the official repository (`ghcr.io/logshed/logshed`).

### Changed
- **High-Throughput Chunked Batch Ingestion**: Upgraded queue batch inserts to use multi-row `INSERT INTO logs (...) VALUES (...) RETURNING id` queries, avoiding Python loop overhead while accurately binding generated IDs for live SSE streaming.
- **Thread-Local Read Connection Reuse**: Worker threads in `run_db_query` reuse SQLite read connections, eliminating ephemeral connection churn and per-request PRAGMA overhead during heavy concurrent read queries.
- **Unified Rules & History Hub**: Consolidated Alert Rules, Ingestion Drop Rules, Historical Incidents, and Maintenance Schedules into a unified 4-tab "Rules & History" hub with dedicated deep-link routes (`/rules/rules`, `/rules/drop-rules`, `/rules/history`, `/rules/maintenance`) and responsive layouts across desktop and mobile screens.
- **Frontend Code Splitting & Bundle Efficiency**: Added `React.lazy()` route-level code splitting and suspense fallbacks for heavy panels (`StoragePanel`, `AlertsPanel`, `SettingsPanel`, `AiAnalysisModal`), reducing initial bundle size to ~362 kB. Cached log search bar callbacks and throttled scroll listeners to prevent DOM re-renders during high-speed log ingestion.
- **Dedicated Notification Worker Pool**: Decoupled notification dispatching into a dedicated 4-worker thread pool (`logshed-notifier`) with clean lifecycle shutdown hooks, preventing external webhook latency or network delays from impacting core event processing.
- **Streamlined Storage Management Panel**: Refocused the Storage panel on disk metrics, retention policies, and database maintenance, moving incident and AI audit logs to the unified History view.
- **Pruned Background Memory Tracking**: Added periodic pruning for exited containers in `DockerTailer` and expired sliding-window buffers in `AlertEvaluator` to prevent memory accumulation in dynamic container environments.
- **Enhanced Alert Notification Formatting**: Refined push notification payloads with bold markdown headers, culprit counts, storm percentages, tight line spacing for mobile notification cards, and direct dashboard deep links.
- **Comprehensive Documentation Suite**: Restructured documentation into dedicated guides, including `docs/RULES_GUIDE.md` (covering rate rules, drop rules, and canary presets), `docs/SENDING_LOGS.md` (forwarding guides for OPNsense, Proxmox, Synology, pfSense, UniFi, Linux, and Docker), and `docs/CONFIGURATION.md` (environment variables and persistence paths).

### Removed
- **Orphaned Modules & Legacy Aliases**: Removed deprecated service modules (`pipeline.py`, `security_presets.py`), obsolete utility helpers, and legacy frontend aliases.

### Fixed
- **Storage Trend Graph Y-Axis Truncation**: Fixed Y-axis digit clipping in `StorageTrendChart` when log volumes exceed 1,000 MB by eliminating negative chart margins, allocating dedicated axis width, and formatting values with thousands separators.
- **Filter Facet Ghost Aliases During Updates**: Corrected index skip-scans in `get_log_facets` to resolve `source_ip` alongside `source_alias`, eliminating duplicate ghost entries from filter dropdowns when host aliases are updated.
- **AI Code Block Copy Whitespace**: Dedented common leading whitespace when copying markdown code blocks to clipboard in the AI analysis modal.
- **Host Alias Deletion Confirmation**: Replaced native browser `window.confirm` dialog with an in-app confirmation modal matching the application design.
- **CI Container Architecture Manifest**: Added `provenance: false` to the Docker build workflow to eliminate unknown multi-arch manifest entries on GHCR.

### Security
- **SSRF & DNS Rebinding Protections on Notification Targets**: Comprehensive destination validation for webhooks and notification URLs. Blocks dangerous URI schemes, Docker socket ports, loopback addresses, and cloud metadata IP ranges (`169.254.0.0/16`, `fe80::/10`). Destination hostnames are resolved and verified against blocked ranges at setup and dispatch time, with automatic blocking of outbound HTTP redirects pointing to private addresses and unwrapping of IPv4-mapped IPv6 formats.
- **ReDoS Protection for Pattern Matching**: Hardened regular expression validation against catastrophic backtracking antipatterns across alert and drop rules, complemented by execution timeout limits and length bounds during evaluation to keep worker threads responsive.
- **Automatic Secret Redaction in Outbound Alerts**: Sample log snippets, alert titles, and AI diagnosis summaries pass through the on-demand secret redactor before outbound notification dispatch to prevent leaking passwords, API keys, or auth credentials to external services.
- **Subsecond Session Revocation**: Floating-point timestamp precision when evaluating session issue times against admin password changes, ensuring immediate revocation of active sessions issued in the same second prior to a password change.
- **Trusted Proxy Verification & Secure Cookies**: Verified client peer IP against trusted proxies before accepting the `X-Forwarded-Proto` header for setting the `Secure` session cookie flag.
- **Indirect Prompt Injection Protections**: Escaped markdown code fence sequences in log payloads sent to LLMs and enforced passive boundary instructions to prevent instruction breakout during AI diagnosis prompts.

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

[Unreleased]: https://github.com/logshed/logshed/compare/v1.2.0-beta.1...HEAD
[1.2.0-beta.1]: https://github.com/logshed/logshed/compare/v1.1.0...v1.2.0-beta.1
[1.1.0]: https://github.com/logshed/logshed/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/logshed/logshed/releases/tag/v1.0.0


