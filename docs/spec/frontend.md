# Specification: Frontend Specification (React + Vite + Tailwind)

Parent document: [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md)

## 1. Overview & Architecture
The LogShed frontend is a responsive single-page web application built with React, Vite, and Tailwind CSS. The interface is tailored for dense, real-time log observation, incident investigation, rule configuration, and system administration. Production builds are bundled directly into `backend/app/static/` and served by FastAPI with HTML5 History API fallback routing to `index.html`.

## 2. Navigation Hierarchy
The frontend implements single-page HTML5 History API routing across four top-level navigation destinations:
- **`/` (Logs / Console):** Live streaming log console with virtual scrolling, search, filters, saved views, and context inspection.
- **`/rules` (Rules & Alerts Hub):** Unified hub managing alert rules, ingestion drop rules, incident history, maintenance windows, and built-in canary presets.
- **`/storage` (Storage Metrics):** Storage metrics dashboard displaying database file sizes, host mount disk usage, 30-day volume trends, manual prune triggers, and database vacuum utilities.
- **`/settings` (Settings & Aliases):** Application settings, AI provider configuration, notification channels, advanced runtime options, and IP-to-Host alias management located at `/settings/aliases`. Top-level requests to `/aliases` automatically redirect to `/settings/aliases`.

Unsaved changes detection and browser exit guards prevent accidental loss of form inputs across settings tabs and modal dialogs.

## 3. Core Components & Views

### 3.1 Console Viewer (Live Stream - `/`)
* **Virtual List Implementation (`@tanstack/react-virtual`):**
  * Virtual list container capable of rendering 50,000+ log lines without DOM performance degradation.
  * Dynamic item measurement with parent scroll container integration.
* **Live Streaming & Flow Control:**
  * Real-time Server-Sent Events (SSE) stream consuming incoming log records.
  * Configurable auto-scroll operation with automatic pause detection when the operator scrolls upward.
* **Severity Tinting & Badges:**
  * Visual badges reflecting RFC 5424 severity priorities: Emergency / Alert / Critical / Error (Red), Warning (Yellow), Notice / Informational / Debug (Slate / Blue).
* **Selection & Batch Action Bar:**
  * Checkbox multi-select mode with a floating action bar: `"Run Analysis (N)"` or `"Delete Selected (N)"`.
* **Search & Filter Bar (Query Builder & Facet Bars):**
  * Full-text search supporting SQLite FTS5 query syntax.
  * Date and timestamp picker for bounded historical queries.
  * Multi-select dropdown facet filters for Host Alias (`source_alias`), Source IP, and Container/Application Name (`app_name`).
  * Numerical severity threshold slider and filter pills (RFC 5424 numerical priorities 0 = Emergency through 7 = Debug).
* **Saved Views Menu:**
  * Quick-load saved search filters.
  * Persist current query parameters with custom display names.
  * Pin frequently accessed views directly to the filter bar.
* **Log Detail & Context Inspector:**
  * Slide-over inspection drawer presenting parsed metadata, source IP, facility, formatted homelab timestamp (with timezone abbreviation badge), secondary database UTC reference line, and raw unparsed syslog payload.
  * One-click action to fetch surrounding context lines formatted in the configured homelab timezone (symmetric 10 entries around selected record).
* **Targeted Log Deletion Modal:**
  * Supports deletion of selected records, filtered ranges, or all historical logs with confirmation guards and dry-run count preview.

### 3.2 Rules & Alerts Hub (`/rules`)
* **Alert Rules Tab:**
  * Full CRUD management for threshold, pattern match, and spike alert rules.
  * Configure Apprise notification targets, sliding evaluation windows, cooldown suppression periods, and optional automated AI root-cause incident enrichment.
  * Dry-run pattern testing against historical logs and JSON bundle export/import.
* **Automated AI Redaction Notice:**
  * When rules with AI enrichment are enabled, the UI renders prominent notices explaining that triggering events are dispatched automatically to external AI providers without manual review, that automated scrubbing operates on a best-effort basis, and that automated triggers consume API tokens.
* **Drop Rules Tab:**
  * Pre-storage discard filters (keyword or regex) matching message content, application name, or source, with severity thresholds.
  * Displays live dropped counters per rule, counter reset actions, dry-run testing against recent logs, and JSON export/import.
* **Alert History Tab:**
  * Unified incident history covering triggered alert rules, automated AI rule enrichments, and scheduled daily analytical digest dispatches.
  * Detail inspection drawer (rendered via `IncidentHistoryDetail.tsx`) presents full prompt envelopes, model badges, incident summaries, and token consumption breakdowns (input, output, reasoning/thought, and total).
  * Supports single-item and bulk history deletion.
* **Maintenance Window Tab:**
  * Configure active on-demand alert suppression periods or recurring schedules by day of week and time window to silence alerts during routine maintenance.
* **Security Canary Presets Modal:**
  * 1-click installation of production-tested security canary alerts (SSH brute force, OOM killer, sudo privilege escalation, proxy authentication floods) and drop rule presets (Docker healthchecks, noisy background daemons).

### 3.3 On-Demand AI Analysis Modal
* **Scrubbed Preview:** Redacted log preview displaying the exact scrubbed text to be dispatched (with one-click clipboard copy).
* **Token Estimator & Model Picker:** Live token counter and active model selection.
* **Situational Context:** Free-text user context textarea to provide situational background (e.g., recent system updates, topology changes).
* **Structured Markdown Display:** Formatted analysis presentation including Summary, Root Cause, and Remediation steps with copyable command and configuration blocks.

### 3.4 Storage Management Panel (`/storage`)
* **Current Storage Card:** Dual-metric display showing active Database Footprint (MB/GB) alongside a visual progress bar for Available Mount Disk Space.
* **30-Day Storage Trend Chart:** Compact line and area chart (via `recharts`) plotting database disk footprint and total log volume over the past 30 days.
* **Maintenance Actions:** Manual trigger for log purge, FTS5 index compaction, WAL truncation, and SQLite vacuum with real-time UI updates.

### 3.5 Settings Panel (`/settings`)
* **Application Settings:** Encrypted API key management (Google Gemini, Anthropic Claude, OpenAI / custom OpenAI-compatible endpoint like Ollama/vLLM), dynamic model discovery with 24-hour cache, multi-model failover configuration, custom system prompt, retention slider (1 to 30 days, capped by `MAX_RETENTION_DAYS`), internal log level, and GHCR release update check toggle.
* **Notification Channels Card:** Configure Apprise push notification targets (Discord, Telegram, Slack, Pushover, email, webhook, etc.) with live test dispatch, and schedule daily analytical digests (`NotificationsCard.tsx`).
* **Host Alias Manager (`/settings/aliases`):** Dedicated table to manage IP-to-Hostname mappings (e.g., `192.168.1.1` -> `OPNsense Firewall`) with quick-add prompts for newly detected, unmapped IP addresses.
* **Advanced Settings (`/settings/advanced`):** Runtime controls for AI timeout, reasoning token budget, base application URL, private IP webhook targets, Docker collector toggle and exclusions, trusted proxy headers, cookie security, and Syslog TCP connection ceilings (`AdvancedSettingsCard.tsx`).

## 4. Theme & Visual Styling
* **Dark Operations Aesthetic:** Native dark canvas (`color-scheme: dark;` background `#070a10`) tailored for server operations environments.
* **Typography:** Clean sans-serif typography (`Inter`, system-ui) paired with monospace fonts for logs, timestamps, IP addresses, and code snippets.
* **Layout Stability:** Built-in `scrollbar-gutter: stable` and slim scrollbars (`::-webkit-scrollbar`) to eliminate layout shifts when virtual lists update.

## 5. Build & Asset Distribution
* **Vite Bundling:** Vite compiles production static assets directly into `backend/app/static/`.
* **FastAPI Static Serving:** FastAPI serves static assets with SPA fallback routing to `index.html`.
