# Changelog

All notable changes to LogShed will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Container Repository Migration Detection & Banner**: Added automated detection for container instances running from the deprecated personal registry namespace (`ghcr.io/benhornertech/logshed`). Displays a prominent, dismissible warning banner at the top of the interface and a dedicated notice card under Settings > About LogShed advising operators to update their Docker Compose or Unraid template to `ghcr.io/logshed/logshed`. Includes automatic query fallback to official releases in `check_for_updates()` and container startup log warnings.
- **Targeted Log Deletion**: Multi-tier log deletion enabling operators to remove single log entries from the inspection modal, delete multiple selected logs directly from the console stream selection bar, or execute scoped deletions from a dedicated card on the Storage panel by host, application, time range, and search query. Backed by safe chunked SQLite deletions, FTS5 index updates, and database size metric recalculation.
- **Daily Digest Rollup**: Background scheduler generating a 24-hour analytical rollup covering total logs ingested, system storage delta, top apps/hosts with errors or above (emerg, alert, crit, error) with per-severity breakdown, and top logging services. Dispatched as a formatted push notification via any configured channel and recorded in alert history. Enabled via a checkbox in App Settings (requires at least one active notification target), with target channel selection (all channels or a specific channel) and a configurable local schedule time (24-hour HH:MM text field with inline validation). Past digests are stored in `alert_history` alongside alert events for a unified history view.
- **On-Demand Database Compaction**: Added on-demand database compaction ("Compact Database") to reclaim host disk space from SQLite freelist pages after log retention pruning. Coordinates temporary ingestion write pauses while incoming syslog and container logs buffer in memory, executes safe WAL checkpoint truncation and VACUUM, and enforces a disk headroom check (database footprint plus 100 MB margin) before execution. Includes authenticated endpoint `POST /api/system/vacuum` and Storage tab UI controls with confirmation modals, live progress indicators, and reclaimed space summaries.
- **Drop Rule Names**: Added user-defined `name` column to `drop_rules` in `migrate_v2`, supporting descriptive naming for drop rules across creation modals, detail views, and JSON export/import with pattern fallback.
- **AI Feature Enablement Toggle**: Added `ai_enabled` system setting and toggle checkbox in Application Settings. Allows disabling AI enrichment entirely, gracefully skipping alert enrichment without missing API key errors and guiding unconfigured users to Settings from the log inspection modal.
- **Unconfigured Notification Channels Advisory**: Added an informative banner at the bottom of Alert Rules when rules are defined but no notification channels are configured, letting operators know incidents are recorded in History while providing a direct link to configure channels in Settings.
- **Comprehensive Configuration Guide**: Created `docs/CONFIGURATION.md` containing a full environment variable reference structured by section (Core Settings, Syslog Listener, Docker Log Tailing, Storage and Retention, AI Provider, Security, Advanced), details on the three-tier resolution hierarchy, and data persistence paths.
- **Syslog & Docker Integration Guides**: Created `docs/SENDING_LOGS.md` with step-by-step forwarding configuration for OPNsense, Proxmox VE, Synology DSM, UniFi Network, pfSense, generic Linux (rsyslog and syslog-ng), and Docker container tailing.
- **Global & Scheduled Maintenance Windows**: Added maintenance window management to silence outgoing alert notifications while continuing to evaluate alerts, record history, and ingest log streams. Supports on-demand windows with quick presets (+1h, +4h, +8h, +24h) or custom end dates, along with user-configured recurring schedules (daily, weekly, monthly) that account for midnight boundary crossings.
- **Dedicated Maintenance Sub-Tab**: Added `/rules/maintenance` deep-linked sub-tab under Rules & History for managing on-demand maintenance windows and recurring maintenance schedules.
- **Global Maintenance Banners**: Added top navigation and in-panel alert banners indicating active maintenance windows, showing expiration timestamps and 1-click deactivation or management actions.
- **Unified AI and Alert History**: Combined alert triggers, AI incident diagnoses, and on-demand AI root-cause analyses into a single, unified "History" tab under "Rules & History". Features distinct badges (`On-Demand`, `AI Alert`, `Alert`), a clean summary table, and a click-to-open detail modal displaying full diagnosis markdown, raw LLM prompts, model attribution, and token metrics.
- **AI Audit Log Trigger Source Tracking**: Added `trigger_source` ('on-demand' vs 'alert') to `ai_audit_log` and `ai_audit_id` foreign key linkage to `alert_history` within `migrate_v2`, backfilling legacy records so all historical analyses appear in the unified history timeline.
- **Responsive Sub-Tab Wrapping**: Flexible wrapping layout (`flex-wrap`) for sub-tabs in `AlertsPanel`, ensuring the History sub-tab and count badges remain fully visible and accessible on narrow mobile viewports.
- **Subtle Tab Button Outlines**: Distinct subtle borders (`border-dark-700 hover:border-dark-600`) around non-selected sub-tab buttons in `AlertsPanel` and `SettingsPanel` for clear button affordance.
- **Zero-Zip Drop Rules Export & Import**: JSON export and import for ingestion drop rules, supporting complete configuration backup bundles (`logshed-drop-rules.json`), individual rule files (`drop-rule-${id}.json`), and polymorphic imports (bundle, single-rule container, or raw rule object) with case-insensitive deduplication, regex pattern validation, and drop filter cache reloads.
- **Zero-Zip Alert Rules Export & Import**: JSON export and import for alert rules, generating clean backups (`logshed-alert-rules.json`) and individual rule files (`<rule-slug>.json`) stripped of instance-specific metadata, with polymorphic imports, case-insensitive deduplication by name, and unassigned channel defaults for community rule sharing.
- **Rule Export and Import Actions**: Added "Export All" and "Import" header buttons using browser-native Blob downloads and file picker inputs to both Alert Rules and Drop Rules tabs, alongside row-level "Export" buttons in each table actions column.
- **Standalone JSON Alert and Drop Presets**: Decoupled predefined rule definitions into standalone JSON files in `backend/app/presets/alerts/` and `backend/app/presets/drops/`, supporting dual-directory discovery from the application bundle and `/data/presets/` with `is_custom` indicator badges and automatic directory creation on startup.
- **Preset Catalogs & Symmetrical Sub-Tabs**: Added dedicated modal catalogs for browsing and 1-click activating alert presets (`AlertPresetsModal`) and drop rule presets (`DropPresetsModal`), streamlining Rules & History into 4 focused sub-tabs (`rules`, `drop-rules`, `history`, `maintenance`) with symmetrical action toolbars.
- **Ingestion Rate Spike & Log Storm Detection**: Introduced real-time rate spike alert rule type (`rate`) evaluating incoming log velocity in logs/second against a configurable window, backed by dynamic sliding-window deque bounds.
- **In-Memory Top-Talker Culprit Analysis**: Integrated zero-disk-I/O frequency analysis using `collections.Counter` to identify primary culprit services/containers, hosts, and repeated log patterns during log storms.
- **Rich Rate Alert Notifications & Incident Summaries**: Enhanced push notifications and fallback incident summaries with culprit counts, storm percentages, and observed rates, plus LLM prompt enrichment for AI root-cause analysis.
- **Built-in Log Storm Alert Preset**: Pre-configured canary alert preset (`alert_log_storm.json`) monitoring for log spikes >= 100 logs/second over a 30s window.
- **Drop Rule Severity Thresholds**: Configurable severity threshold level (`severity_threshold`) for ingestion drop rules, enabling selective dropping of noisy logs at or below a specified severity (e.g. Info and Debug) while preserving higher-priority warning, error, and critical messages. Includes schema migration in `migrate_v2`, test pattern simulation with sample severity, and severity level badges in the drop rules table.
- **Ingestion Drop Rules**: In-memory filtering engine evaluating host/source, application/container, and message patterns (substring, regex, or app-wide wildcard `*`) to discard repetitive log chatter before database persistence and FTS5 indexing. Includes in-memory drop counters with periodic SQLite flushing to eliminate disk write contention.
- **Saved Filter Views & URL Synchronization**: Saved filter views menu on the Console filter bar and mobile filter drawer with pinned view support, 1-tap activation, and bidirectional query parameter synchronization in the browser URL for bookmarking and sharing.
- **Interactive Rule Tester & Console Quick-Action**: Dry-run pattern tester in Settings and a "Create Drop Rule" shortcut directly within the log detail modal that pre-fills host, application, and message context.
- **Deletion Safety Net**: Confirmation modal protection for drop rules and saved views to prevent accidental deletion.
- **Asynchronous FTS5 Indexing Worker**: Decoupled full-text search indexing from the raw log ingestion path into a supervised background task (`FTSIndexWorker`), enabling raw ingestion rates without holding exclusive SQLite write locks during tokenization.
- **Chunked Batch Ingestion with RETURNING id**: Modernized `QueueConsumer` batch inserts using parameterized multi-row `INSERT INTO logs (...) VALUES (...) RETURNING id` queries to eliminate Python interpreter loops while accurately binding IDs for live SSE streaming.
- **Thread-Local Read Connection Reuse**: Reusable SQLite read connections for worker threads in `run_db_query`, eliminating ephemeral connection churn and per-request PRAGMA execution overhead.
- **Universal Notification Targets (Webhooks & Apprise)**: Universal alerting dispatcher supporting 80+ notification services (Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email) using standard URL formats. Includes URL encryption at rest with the master key, token masking for secure client display, and asynchronous dispatch via `asyncio.to_thread`.
- **Real-Time Alert Engine**: In-memory rule compilation and batch evaluation engine (`AlertEvaluator`) running inside the ingestion pipeline, supporting threshold sliding windows, pattern matching (regex and substring), severity thresholds, multi-application filtering, and cooldown flap dampening.
- **Pre-Packaged Security Canary Rules**: 1-click installable canary alerts for SSH brute-force attacks, reverse-proxy authentication floods, unauthorized sudo escalation, and kernel out-of-memory (OOM) killer events.
- **Automated AI Incident Diagnosis & Remediation**: Automated background analysis on alert triggers, generating structured summaries, root-cause analyses, and actionable remediation steps with model failover support.
- **Enriched Alert Notifications**: Multi-channel notification delivery via Apprise (Pushover, Discord, Telegram, webhooks, etc.) with host, application, offending IP, log context, and AI incident diagnosis summaries.
- **Alert Testing & Dry-Run Simulator**: Interactive test modal to validate alert matching rules and extract offending IP indicators against sample log payloads.
- **Alert Firing Log & Unified Incident Detail View**: Historical alert record tracking with model attribution, matching event counts, log snippets with quick copy actions, and formatted markdown rendering matching the Historical AI Root-Cause Analysis layout.
- **Database Schema Migration v2 Expansion**: Expanded Migration 2 to introduce `notification_channels`, `alert_rules`, and `alert_history` tables with covering indexes for alert routing and delivery.
- **Composite Index on Alert History**: Added `idx_alert_history_rule_time` covering `(rule_id, triggered_at DESC)` in `alert_history` to accelerate rule-specific incident history queries.
- **Alert Rule Modal Component**: Extracted `AlertRuleModal` to encapsulate alert rule creation and editing form state, validation, and multi-select handling.
- **Alert Test Dry-Run Modal**: Extracted `AlertTestModal` with signature-aware sample generation (detecting HTTP 401/403, SSH brute-force, sudo escalation, and OOM signatures) and helpful usage hints.
- **Incident Status Badge Component**: Reusable `IncidentStatusBadge` component for triggered, AI-enriched, and AI-failed status indicators across mobile cards and desktop tables.
- **Automated AI Redaction Notice**: Informative disclaimer banner displayed beneath active alert rules whenever any configured rule has AI enrichment enabled.
- **Settings Sub-Tabs**: Embedded Host Aliases into Settings as a dedicated sub-tab (`/settings/aliases`), structuring Settings into Application (`/settings/app`), Host Aliases (`/settings/aliases`), and Advanced (`/settings/advanced`) sections with browser URL history tracking and backward-compatible redirects from legacy `/aliases` bookmarks.
- **Two-Tone Sub-Tab Count Badges**: Monospace count indicators on Rules & History sub-tabs rendering active versus total figures (`active / total`) with emerald tinting for running rules and muted styling for totals without visual distraction.
- **Dynamic Host Alias Stream Refresh**: Integrated React context `AliasContext` and memoized stream canonical mapping in `LiveLogStream`, updating displayed log rows, quick filter buttons, and source filter dropdowns immediately whenever a host alias is saved or deleted without requiring a page refresh.
- **Dynamic Internal Log Alias Resolution**: Connected `InternalLogHandler` to the active `AliasCache`, allowing application logs from LogShed itself (such as `127.0.0.1` or `logshed`) to resolve to user-defined aliases and persist across container restarts.

### Changed
- **Docker Container Tracking Memory Pruning**: In `DockerTailer`, prune container tracking entries (`_container_last_seen` and `_container_last_messages`) for exited or untracked containers during periodic discovery passes to prevent memory accumulation in dynamic environments.
- **Sliding Window Buffer Pruning & Lightweight Rate Tracking**: Added periodic window buffer pruning (`prune_expired_windows`) to `AlertEvaluator` to evict expired records when alert traffic becomes idle, and transitioned rate spike rule windows to store lightweight timestamp records instead of full log dictionaries.
- **Resolved System Settings Caching**: Added thread-safe in-memory caching for fully resolved system settings dictionaries in `get_cached_system_settings()`, refreshing only on database cache expiry or explicit cache clearing to eliminate redundant resolution overhead.
- **Alert Push Notification Formatting & Line Spacing**: Formatted alert push notification headings in bold Markdown (`**Host:**`, `**App:**`, `**Log:**`, `**Rate:**`, etc.), updated the title format to `LogShed: <Rule Name>`, converted HTML break tags into native newlines for tight single-line spacing in Pushover mobile clients without blank line gaps while preserving lock screen line breaks, and made the direct `/rules/history` dashboard link available to all alert notifications whenever `app_url` is configured.
- **Drop Rules Table Layout Alignment**: Redesigned the Ingestion Drop Rules table on desktop and mobile viewports to match the layout, typography, status indicators, and action column structure of the Alert Rules table. Rule patterns are neatly tucked into the detail modal, highlighting the rule name in the main list.
- **AI Disabled Notice Restyling**: Updated the "AI Features Disabled" advisory notice in Settings to match the subtle dark card design of the notification channels advisory banner.
- **Storage Panel Header Streamlining**: Removed the redundant duplicate "Storage & Retention" section subtitle preceding the Current Storage Footprint card on the Storage tab.
- **Streamlined README Documentation**: Slimmed down `README.md` to core project overview, feature highlights, quick-start commands, and a dedicated documentation table linking to specialized guides in `docs/`.
- **Notification Targets Card Layout**: Removed the collapse chevron from the Notification Targets card on the Application Settings tab to keep it permanently visible, and moved the card above Version Updates.
- **Portaled Modal Mounting**: Mounted modal dialogs to `document.body` via `createPortal` to prevent nested form interference when cards containing modals are placed inside settings forms.
- **Maintenance Table Layout**: Balanced column widths in the recurring maintenance schedule table for consistent alignment across viewports.
- **Rules & History Navigation Alignment**: Renamed the top-level navigation item, route path (`/rules`), and panel heading to "Rules & History" across desktop and mobile navigation bars, establishing a unified home for Alert Rules, Ingestion Drop Rules, Historical Incidents, and Maintenance Schedules.
- **Storage Panel Streamlining**: Streamlined the Storage panel to focus solely on disk footprint metrics and retention policies, removing redundant AI audit log tables in place of the unified History view.
- **Drop Rules Subtitle**: Simplified ingestion drop rules subtitle to "Discard repetitive syslog or container chatter".
- **Alert Preset Terminology Alignment**: Renamed `SecurityPreset` to `AlertPreset` across backend models, service modules (`alert_presets.py`), and frontend types.
- **Rate Threshold Display Formatting**: Formatted rate rules and presets across alert rule listings and preset cards to clearly indicate logs/second over window duration.
- **Symmetrical Alert and Drop Rule Toolbars**: Aligned action toolbars on both Alert Rules and Drop Rules tabs with Presets and New Rule buttons.
- **Navigation Restructure to 4 Core Tabs**: Restructured top-level navigation to Stream, Rules & History, Storage, and Settings. Moved Ingestion Drop Rules into Rules & History as a dedicated sub-tab (`/rules/drop-rules`).
- **Alerts and Rules Layout Alignment**: Scoped the New Alert Rule action directly to the Alert Rules tab header bar, unified all sub-tab headers in uppercase with accent icons and divider lines matching Settings sections, and aligned Host Alias card presentation.
- **Rules & History Tab Layout Alignment**: Standardized container width and header styling in the Rules & History tab to match Storage and Settings, including seamless Alert Firing Log table headers.
- **Rules Sub-Tab Deep-Linking**: Added unique browser URL routes for Alert Rules (`/rules/rules`), Drop Rules (`/rules/drop-rules`), Incident History (`/rules/history`), and Maintenance (`/rules/maintenance`) with bidirectional browser navigation.
- **Incident History Modal Presentation**: Replaced inline accordion rows in Incident History with responsive desktop table and mobile card layouts that open a dedicated modal overlay for log analysis and diagnosis details.
- **Table Column Header Typography**: Harmonized table headers in Incident History and Storage AI Audit Log with Settings tables, adopting title case, standard sans-serif font (`font-sans`), medium weight (`font-medium`), and matching border styling (`border-dark-700`).
- **Channel Map Optimization in Alerts Panel**: Pre-indexed notification channels into a memoized Map for O(1) channel lookups during rendering.
- **Alert Rule Documentation & Channel Status**: Unified documentation link styling with syntax documentation in Settings, and added real-time inactive and deleted channel warnings on rule cards.
- **Alert Push Notification Formatting**: Streamlined push notification payload to avoid repeating the alert title in message bodies, appends AI diagnosis details, and generates direct history links when `APP_URL` is configured.
- **Dedicated Notification Worker Pool**: Dedicated `ThreadPoolExecutor(max_workers=4, thread_name_prefix="logshed-notifier")` for Apprise notification dispatching via `loop.run_in_executor`, decoupled from the general asyncio thread pool, with graceful shutdown hooks during application lifecycle termination.
- **Streamlined Alert Evaluation Loop**: Pre-parsed normalized float epoch timestamps on batch entries, pre-split multi-application filter tuples, pre-lowercased substring match patterns, and replaced full deque sorting with backwards linear search for timestamp jitter insertion.
- **Decoupled Alert Trigger State Persistence**: Offloaded rule trigger database updates (`last_triggered_at`, `suppress_until`, `trigger_count`) from the synchronous batch loop into background alert dispatch tasks, maintaining instantaneous in-memory cooldown dampening for subsequent batches.
- **Non-Blocking Background Workers & Rule Reloads**: Offloaded periodic drop count flushing in `_drop_filter_flush_worker()` and synchronous rule reloads across alert rules and drop rules endpoints using `asyncio.to_thread`, preventing SQLite disk I/O from stalling the main event loop.
- **Single Snapshot Lock Acquisition for Drop Rules**: Consolidated pending drop count retrieval in `list_drop_rules` to a single snapshot read (`drop_filter.get_all_pending_counts()`), eliminating per-rule lock reacquisition during endpoint queries.
- **Consolidated Wildcard & Timestamp Utilities**: Centralized case-insensitive wildcard matching (`match_wildcard`) and resilient ISO-8601 parsing (`parse_iso_to_epoch`) into `backend/app/core/utils.py`, eliminating redundant parsing logic across the alert evaluator, drop filter, and API route handlers.
- **Shared UTC Datetime Parsing**: Unified ISO-8601 and numeric timestamp parsing into `parse_iso_to_utc_datetime()` in `app.core.utils`, removing repeated manual parsing routines across maintenance schedules, alert rules, system settings, and AI service audit handlers.
- **Consolidated Full-Text Search Query Formatting**: Consolidated FTS5 query parsing, operator validation, and token escaping into `app.core.utils`, removing duplicate helper functions and regex patterns from the logs API.
- **Unified Database Migration v2 Runner**: Consolidated startup database statements (including covering index creation, system setting defaults, timestamp clamping, and drop rule name backfills) into migration step `v2`, keeping `PRAGMA user_version = 2` aligned across versions.
- **Consolidated Alert Response Mapping**: Centralized database row unpacking in `alerts.py` via `_row_to_alert_rule_response()`, eliminating repeated positional tuple unpacking across rule listing, retrieval, and update endpoints.
- **Non-Blocking Background Alias Updates**: Offloaded bulk retroactive log batch updates to FastAPI `BackgroundTasks` with tuned 1,000-row chunks and 5ms sleep intervals, keeping host alias save and delete responses instantaneous (<20ms) even across hundreds of thousands of historical logs.
- **Processing Newest Logs First During Alias Updates**: Updated `_batch_update_log_aliases` to process rows ordered by `id DESC` so the most recent logs visible in the live stream reflect alias modifications first.

### Removed
- **Orphaned Service Modules & Deprecated Aliases**: Removed unused `pipeline.py` and `security_presets.py` service modules, obsolete private utility aliases (`_escape_fts_tokens`, `_format_fts_query`, `_parse_multi_values`), and deprecated frontend API aliases (`fetchSecurityPresets`, `installSecurityPreset`, `diagnoseLogsStream`, `SecurityPreset`).

### Fixed
- **Advanced Settings Targeted Updates & Worker No-Op Guards**: Updated the Advanced Settings form to send only modified settings rather than dumping all fields on every save, preventing unrelated settings (such as syslog connection limits) from being written and logged when toggling container tailing. Added no-op safeguards to `SyslogServer.update_limits` and `DockerTailer.update_settings` to prevent unnecessary connection timer resets, container re-scanning, and redundant log output when limits or settings remain unchanged.
- **Maintenance Banner Expiration Auto-Clear**: Automatically cleared expired maintenance window banners on the Maintenance sub-tab in real-time without requiring a page refresh.
- **Storage Trend Graph Y-Axis Truncation**: Fixed Y-axis leading digit clipping in `StorageTrendChart` when log volumes exceed 1,000MB by resetting negative chart margins, allocating dedicated axis width, and formatting large values with localized thousands separators.
- **Sub-Tab Layout Shift on Height Changes**: Eliminated horizontal content jumping when switching between short and tall sub-tabs by configuring `scrollbar-gutter: stable` on the main scroll container.
- **Alert Processing Self-Referential Logging Loops**: Suppressed `app.services.alert_evaluator`, `app.services.notifier`, `app.services.ai_engine`, and `app.services.ai_service` in `InternalLogHandler.IGNORED_LOGGERS` to prevent alert processing logs from being ingested and triggering cascading alerts.
- **Mobile Drop Rule Action Clipping**: Fixed button clipping and column alignment in the drop rules table on mobile and desktop viewports.
- **Rate Rule Sample Log Selection**: Selected sample logs from the primary culprit service over arbitrary trailing batch entries.
- **Setting Section Header Consistency**: Unified section title styling to consistent white for both Rate Spike and Threshold settings in `AlertRuleModal`.
- **CompiledAlertRule Last Trigger Timestamp Assignment**: Fixed an initialization bug in `CompiledAlertRule.__init__` where `last_triggered_at` was received as an argument but never assigned to the instance attribute.
- **Numeric Input Backspacing in Alert Rules**: Resolved an issue where clearing numeric input fields in the alert rule modal forced a leading zero, ensuring fields can be completely cleared and typed into smoothly.
- **AI Code Block Copy Whitespace**: Dedented common leading whitespace from fenced markdown code blocks when copying to clipboard, preventing unwanted indentation in copied snippets.
- **Host Alias Deletion Confirmation**: Replaced native browser `window.confirm` dialog with an in-app confirmation modal matching the design of other destructive actions.
- **CI Container Architecture Manifest**: Added `provenance: false` to the Docker build-and-push GitHub Action workflow to prevent unknown/unknown multi-arch manifest entries on GHCR.
- **Dynamic Beta Browser Title**: Added dynamic document title updating on mount to display prerelease versions (e.g. `LogShed [1.2.0-beta.1]`) while retaining `LogShed` for stable releases.
- **Rules & History Mobile Sub-Tabs Layout**: Replaced the unconstrained horizontal tab row in Rules & History with a responsive 2x2 grid on mobile viewports, ensuring all 4 sub-tabs and count badges are visible without horizontal scrolling or viewport blowout.
- **Filter Facet Ghost Aliases During Updates**: Updated covering index skip-scans in `get_log_facets` to look up `source_ip` alongside `source_alias`, resolving stale historical aliases to their active alias name and eliminating duplicate ghost entries from filter dropdowns and quick filter buttons during in-flight updates.
- **Notification Targets & Daily Digest Save Workflow**: Integrated Daily Digest settings into the sticky Save Changes bar and unsaved change tracking on Application Settings, keeping save workflows consistent with adjacent configuration cards instead of updating immediately on input change or blur.
- **Drop Rule Creation Form Input Preservation**: Preserved user inputs (rule name, regex toggle, severity threshold, and custom message patterns) in the Create Drop Rule modal when incoming background log stream updates arrive, preventing form resets during active configuration.
- **Persistent FTSIndexWorker Connection Management**: Replaced per-chunk connection creation in `FTSIndexWorker` with a dedicated persistent connection instance lazily created via `get_connection()`, aligning with `QueueConsumer`. Safely releases the connection on pause or termination to prevent descriptor leaks and eliminate SQLite locking conflicts during maintenance operations.
- **DropFilter Singleton Counter Retention & Database Fallback**: Retained active in-memory counters in `DropFilter._pending_counts` across `init_drop_filter()` calls, and added fallback to `get_db_path()` when `self.db_path` is not explicitly set in `flush_counts()` and `reload_rules()`.
- **Asynchronous Notification Target Validation**: Offloaded synchronous `validate_notification_url()` calls in channel creation and update API handlers to worker threads via `asyncio.to_thread` to prevent DNS resolution delays from blocking the event loop.
- **Drop Rule Counter Reset Schema Consistency**: Included the `name` column in the database query and response schema when resetting drop rule counters via `POST /api/drop-rules/{id}/reset`.
- **Structured Error Handling for Retroactive Alias Tasks**: Wrapped background retroactive log alias update and reversion tasks in structured exception handling, logging detailed error context on database failure.

### Security
- **Subsecond Session Revocation**: Eliminated integer truncation when comparing session token issue times and admin password update timestamps, ensuring microsecond float precision so tokens issued in the same second prior to a password change are properly revoked.
- **Trusted Proxy Validation for Secure Cookie Flag**: Verified client peer IP against trusted proxies before accepting the `X-Forwarded-Proto` header for setting the `Secure` session cookie flag.
- **Version Update Refresh Protection & Rate Limiting**: Required authentication for `refresh=True` requests on `/api/system/version` and enforced a 60-second minimum interval between live GitHub Container Registry queries to prevent socket exhaustion and external rate limits.
- **REST Semantics for AI Model Discovery**: Separated read-only model listing (`GET /api/ai/models`) from on-demand provider querying (`POST /api/ai/models/refresh`), requiring authentication and CSRF protection for external queries while updating SQLite cached models.
- **Indirect Prompt Injection Protection**: Escaped triple backticks in redacted log payloads sent to LLMs and added explicit passive text boundary instructions to prevent instruction breakout in AI diagnosis prompts.
- **SSRF & DNS Rebinding Safeguards for Notifications**: Extended notification IP validation to detect and unwrap IPv4-compatible IPv6 addresses (`::/96`, e.g. `[::127.0.0.1]` and `[::169.254.169.254]`) against loopback, metadata, and private network ranges. Strictly reject unresolvable hostnames during destination validation, re-validate targets at dispatch time, and block outbound HTTP redirects (301/302) pointing to loopback, private, or metadata endpoints.
- **Enhanced ReDoS Protection on Ingestion Loop**: Expanded `is_catastrophic_backtracking_pattern()` to detect nested groups with alternations (e.g. `(a|aa)+`, `([a-z]|[a-z][a-z])+`) and deeply nested repetition structures (e.g. `((a)+)+`). Added safe regex search execution with timeout protection and input length bounds in `match_message()` to ensure regular expression matching cannot stall worker threads or the event loop.
- **Sliding Window Bounds & Memory Caps**: Clamped incoming log timestamps between `now_epoch - 86400` and `now_epoch + 300` to prevent future timestamp spoofing, computed sliding window cutoffs relative to current epoch time, and bounded maximum sliding window deques to `threshold_count * 2` (capped to `threshold_count` during cooldown suppression) to prevent memory expansion.
- **Alert Rule Cooldown Flood Protection**: Enforced a minimum cooldown duration of 5 seconds (`ge=5`) on `AlertRuleCreate` and `AlertRuleUpdate` models to prevent notification flood loops.
- **SSRF Protection in Webhook Targets**: Hardened notification target validation against Server-Side Request Forgery. Blocks dangerous schemes (`file://`, `attach://`), Docker daemon control ports (`2375`, `2376`), container loopback destinations (`localhost`, `127.0.0.0/8`, `::1`), and cloud instance metadata IP ranges (`169.254.0.0/16`, `fe80::/10`). Destination hostnames are resolved via `socket.getaddrinfo()` to verify destination IPs against blocked ranges, while preserving local container hostnames and LAN subnets via configurable `ALLOW_PRIVATE_NOTIFICATION_TARGETS`.
- **ReDoS Protection in Pattern Rules**: Added validation against pathological nested repetition antipatterns (such as `(a+)+`, `([a-z]+)*`, `((a+)+)+`) across alert rules and drop rules to prevent regular expression denial-of-service backtracking. Rejects vulnerable patterns with HTTP 400.
- **Secret Redaction in Outbound Notifications**: Passed sample logs, alert titles, and AI diagnosis summaries through the on-demand secret redactor before outbound notification dispatch to prevent leaking sensitive tokens, passwords, and authorization headers to webhook endpoints.
- **Credential Scrubbing in URL Masking**: Enhanced fallback notification URL masking via `urllib.parse.urlsplit` to scrub plain usernames and passwords in the netloc to `***:***@<host>`.
- **Channel Testing Exception Cleansing**: Suppressed raw socket and connection error tracebacks in channel connectivity tests, returning a clean user-facing error message while recording full exception details in server logs.

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

[Unreleased]: https://github.com/logshed/logshed/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/logshed/logshed/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/logshed/logshed/releases/tag/v1.0.0
