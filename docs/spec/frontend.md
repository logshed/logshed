# Specification: Frontend Specification (React + Vite + Tailwind)

Parent document: [docs/SPEC.md](../../docs/SPEC.md)

## 1. Overview & Architecture
The LogShed frontend is a responsive single-page web application built with React, Vite, and Tailwind CSS. The interface is tailored for dense, real-time log observation, incident investigation, rule configuration, and system administration. Production builds are bundled directly into `backend/app/static/` and served by FastAPI with HTML5 History API fallback routing to `index.html`.

## 2. Navigation Hierarchy
The frontend implements single-page HTML5 History API routing across four top-level navigation destinations:
- **`/` (Logs / Console):** Live streaming log console with virtual scrolling, search, filters, saved views, and context inspection.
- **`/rules` (Rules & Alerts Hub):** Unified hub managing alert rules, ingestion drop rules, incident history, maintenance windows, and built-in canary presets.
- **`/storage` (Storage Metrics):** Storage metrics dashboard displaying database file sizes, host mount disk usage, 30-day volume trends, manual prune triggers, and database vacuum utilities. Lowering retention or purging expired data first shows a preview modal listing the logs, history items, metrics and AI audit records that would be deleted.
- **`/settings` (Settings & Aliases):** Application settings, AI provider configuration, notification channels, API & MCP access, advanced runtime options, and IP-to-Host alias management located at `/settings/aliases`. Top-level requests to `/aliases` automatically redirect to `/settings/aliases`.

Unsaved changes detection and browser exit guards prevent accidental loss of form inputs across settings tabs and modal dialogs. When the prompt holds back a link that leaves Settings (for example "View logs" on a host alias, which opens `/?source=<alias>`), Discard & Leave and Save & Continue open that exact URL, so its filters are kept.

## 3. Core Components & Views

### 3.1 Console Viewer (Live Stream - `/`)
* **Virtual List Implementation (`@tanstack/react-virtual`):**
  * Virtual list container capable of rendering 50,000+ log lines without DOM performance degradation.
  * Dynamic item measurement with parent scroll container integration.
* **Live Streaming & Flow Control:**
  * Real-time Server-Sent Events (SSE) stream consuming incoming log records.
  * Configurable auto-scroll operation with automatic pause detection when the operator scrolls upward. On phones the button reads "Scroll" (or "Paused") and the clear buffer button is left out, so the quick filters keep their room.
* **Severity Tinting & Badges:**
  * Visual badges reflecting RFC 5424 severity priorities: Emergency / Alert / Critical / Error (Red), Warning (Yellow), Notice / Informational / Debug (Slate / Blue).
* **Navbar Health Figures:** Ingest rate, and on small screens and up queue depth. **Lost: N** (logs LogShed could not queue because its ingestion queue was full; separate from drop rules) appears only when above 0, on every screen size. **UDP lost: N** appears after it only when `udp_receive_errors` is above 0: the syslog UDP messages the kernel discarded because the receive buffer was full. It is separate from Lost, hidden when 0 or unknown, and counts per network namespace (with `network_mode: host` every UDP socket on the host). TCP syslog avoids this loss.
* **AI Controls:** AI actions follow the AI setting (`ai_enabled`, or a stored API key when that field is missing; treated as off until settings load). When AI is off, rows and mobile cards have no "Explain with AI" button (the desktop table drops its Actions column), the selection bar has no "Run Analysis (N)", and the log detail drawer keeps one muted "Explain with AI" entry (tooltip "AI features are turned off in Settings") that opens the AI modal, which explains how to turn AI on.
* **Empty States:** With filters set and no results: "No logs match the current filters." and a "Clear filters" button. With no filters and an empty buffer while the database has logs (for example after Clear buffer): "Waiting for new logs...". With no logs in the database at all: a first-run card with the default syslog port (1514, UDP and TCP, inside the container), a copyable `logger` test command, a "Set up Docker hosts" button that opens Settings > Sources, and a pointer to Sending Logs in the documentation.
* **Quick Filters Bar:** Shown on every screen size. Pinned saved views come first, as chips with a bookmark icon; a click applies the view as the Views menu does, the chip of the active view (see Saved Views Menu) is highlighted, and a click on the active view clears its filters again. Selected host and app chips share one highlight style. Then up to 6 host chips and 6 app chips, each group after a small "Hosts" or "Apps" label, ranked by how many rows each has in the loaded buffer (ties alphabetical) and narrowed to the hosts of the selected apps and the apps of the selected hosts. The ranking is refreshed when the filters change and at most every 30 seconds while live, so chips do not move as logs stream in. A selected chip stays visible even when it falls out of the top 6.
* **Console URL:** `q`, `severity`, `app`, `source`, `time` (start, a relative preset or an absolute time) and `to` (absolute end time, same format as `time`). A link with `time` and `to` opens the Console on that range in custom-time mode, with the live stream paused. `focus=<log id>` is not a filter: once that log is loaded (paging up to 10 older pages of 500 to find it), the Console pauses auto-scroll, scrolls it to the middle of the list and highlights it in amber; the highlight and the parameter are dropped when the filters change.
* **Selection & Batch Action Bar:**
  * Checkbox multi-select mode (desktop table, 768px and wider) with a floating action bar: `"Run Analysis (N)"` (only when AI is on) and `"Delete (N)"`.
* **Search & Filter Bar (Query Builder & Facet Bars):**
  * Full-text search supporting SQLite FTS5 query syntax, including the `app:`, `host:`, `source:` and `msg:` prefixes that also filter the live stream. New live rows are filtered with the same `AND`, `OR` and `NOT` grouping as history search (`matchesSearchQuery` in `LiveLogStream.tsx`, following FTS5 precedence, tightest first: words side by side, `NOT`, `AND`, `OR`; brackets group); while the operators are incomplete (for example `error OR` as it is typed), every word must match. Placeholder: `Search logs, e.g. "disk full", error OR timeout, app:nginx`. A `?` button inside the right edge of the search box (left of the clear button) opens a popover with one example each: words (all must match, as prefixes: `time` finds `timeout`), `"exact phrase"`, `error OR timeout` and `auth NOT cron` (any case), `app:nginx`, `host:pve-node2` and `msg:"disk full"`. It closes on Esc, an outside click or when focus leaves it.
  * Filters apply as they change; the search icon button next to the box ("Search now", same as Enter) runs the search at once. In the mobile filter drawer, "Done" closes the drawer and runs the search.
  * Time selector with presets (Last 15m, 1h, 6h, 24h, 7d and Today, which starts at 00:00 in the server time zone) and a custom date and timestamp picker for bounded historical queries. Presets are saved as relative values, so a saved view keeps meaning "the last hour"; links and saved views carrying `since_midnight` show Today.
  * Multi-select dropdown facet filters for Host Alias (`source_alias`), Source IP, and Container/Application Name (`app_name`). The host and app lists hold at most 500 entries each; when a list was capped, the dropdown (and the mobile filter drawer) shows a note such as "Showing the first 500 hosts. Type a name to add others.", with the number taken from the facets response (`limit`).
  * Numerical severity threshold slider and filter pills (RFC 5424 numerical priorities 0 = Emergency through 7 = Debug).
* **Saved Views Menu:**
  * Quick-load saved search filters.
  * Persist current query parameters with custom display names.
  * Pin frequently accessed views directly to the filter bar.
  * A view is active when the current filters equal its saved filters (lists compared sorted; empty text, empty lists and missing values count as the same). The desktop button then reads `View: <name>` with an accent border, and `Views` otherwise; the active view is highlighted in the dropdown and in the mobile drawer.
  * Each view has Pin, Rename (pencil) and Delete actions, in the desktop dropdown and on the mobile drawer chips. Rename opens a "Rename Saved View" dialog with the name selected; Save is disabled while the name is empty or unchanged. The new name shows at once in the dropdown, the pinned chips and the button label.
  * Names are unique, ignoring case. The Save View and Rename dialogs warn "A saved view with this name already exists." while typing, and show the server's message if another tab took the name first. Changing only the case of a view's own name is allowed.
* **Log Detail & Context Inspector:**
  * Slide-over inspection drawer presenting parsed metadata, source IP, facility, formatted homelab timestamp (with a UTC offset badge such as `UTC+01:00`, worked out for each timestamp so logs from either side of a daylight saving change show the offset they had), secondary database UTC reference line, and raw unparsed syslog payload.
  * Protocol-aware host actions: For Docker records (`facility === 1`), links directly to "Edit Docker Host" or "Configure Docker Host" in `/settings/sources`. For records matching a deleted Docker endpoint, displays a recovery notice modal with endpoint configuration details. For Syslog records (`facility !== 1`), provides quick-add and edit actions for host IP aliases.
  * One-click action to fetch surrounding context lines formatted in the configured homelab timezone (symmetric 10 entries around selected record). The lines are listed newest first, like the log stream. Each context line shows its severity badge and app name; the app name is left out when "Same App Only" is checked.
  * "Newer" and "Older" buttons step to the row above or below in the newest-first list; Up or K and Down or J do the same while the drawer is open, except in a text field or while another dialog is open on top.
  * "Create Drop Rule" prefills the host field with the host alias (the IP when the host has no alias).
  * "Create Alert Rule" (next to "Create Drop Rule") switches to Rules & History > Alert Rules and opens the new-rule dialog with the name `Alert on <app>`, the app filter set to the log's app when it is a known app, severity and pattern empty, and a read-only "From log: <message>" line under the pattern field (truncated, full text in the tooltip). The prefill is handed over in memory (`utils/alertRuleHandoff.ts`, read once by `AlertsPanel`), never in the URL. Closing the dialog stays on Alert Rules.
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
  * Rule alerts (not Daily Digest or On-Demand items) show an "Inspect in Console" button in the detail header and a smaller "View in Console" link next to "Triggering Log Snippet". Both open the Console with `source=<source_alias>` (when set), `time=<start>` and `to=<triggered_at + 1 minute>` as UTC ISO strings, plus `focus=<sample_log_id>` when the alert recorded its triggering log; the start is `triggered_at` minus the larger of 1 minute and the rule's `window_seconds` (1 minute when the rule is not loaded). The range is host-wide; "Same app only" adds `app=<app_name>` when the alert has an app.
  * Analyses with an AI audit record (on-demand analyses and alert enrichments) show an expandable **Follow-up Discussion** section below the diagnosis. Messages load on first expand, and the thread stays mounted when collapsed so a streaming reply is not lost.
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
* **Follow-up Discussion (`AiFollowUpThread.tsx`):** Rendered below the Actionable Remediation card once a diagnosis completes (shared with Incident History). Shows the thread with Markdown replies (copyable code blocks), model and token badges per reply, a running token tally and an `X / 10 turns` counter. Replies stream in over SSE; partial text is discarded when a failover restarts the reply on a fallback model. The question box sends on Ctrl+Enter or Cmd+Enter and keeps 44px touch targets on mobile. The view follows new content unless the operator has scrolled up. After the 10th question an advisory replaces the input. "Clear Thread" asks for confirmation, then resets the conversation while keeping the diagnosis. Follow-ups in the modal reuse the provider, model and fallback order selected for the diagnosis.

### 3.4 Storage Management Panel (`/storage`)
* **Current Storage Card:** Dual-metric display showing active Database Footprint (MB/GB) alongside a visual progress bar for Available Mount Disk Space.
* **30-Day Storage Trend Chart:** Compact line and area chart (via `recharts`) plotting database disk footprint and total log volume over the past 30 days.
* **Maintenance Actions:** Manual trigger for log purge, FTS5 index compaction, WAL truncation, and SQLite vacuum with real-time UI updates.

### 3.5 Settings Panel (`/settings`)
* **Application Settings:** Encrypted API key management (Google Gemini, Anthropic Claude, OpenAI / custom OpenAI-compatible endpoint like Ollama/vLLM), dynamic model discovery with 24-hour cache, multi-model failover configuration, custom system prompt, retention slider (1 to 30 days, capped by `MAX_RETENTION_DAYS`), internal log level, and GHCR release update check toggle.
* **Notification Channels Card:** Configure Apprise push notification targets (Discord, Telegram, Slack, Pushover, email, webhook, etc.) with live test dispatch, and schedule daily analytical digests (`NotificationsCard.tsx`).
* **API & MCP Access (`/settings/api-tokens`):** Scoped Bearer token lifecycle management (create from a preset or a custom scope list, copy raw secret once, rename, change scopes, and revoke) alongside Model Context Protocol (MCP) server administration (`ApiTokensTab.tsx`). Features master server activation toggle (off by default), live endpoint display (Remote SSE / Streamable HTTP), and three collapsible accordion client configuration guides with one-click copy blocks:
  1. Claude Desktop (Remote via `mcp-proxy`) for remote Proxmox, NAS, or external servers.
  2. Cursor / Windsurf / Antigravity (Remote SSE / Streamable HTTP) for direct network IDE integrations.
  3. Claude Desktop (Local Docker Stdio) for local single-machine Docker deployments.
* **Sources Management (`/settings/sources`):** Unified panel coordinating log source identities across protocols (`SourcesTab.tsx`). Combines two primary views:
  * **Docker Hosts (`DockerHostsTab.tsx`):** Dedicated management panel for multi-host Docker log collection. Features live status badges (Connected, Reconnecting, Disabled, Error), active container counts, latency metrics, ad-hoc connection test buttons, and an Add/Edit host modal supporting mutual TLS (CA cert, client cert, and encrypted client key).
  * **Host Aliases (`HostAliasManager.tsx`):** Dedicated table to manage IP-to-Hostname mappings for Syslog sources (e.g., `192.168.1.1` -> `OPNsense Firewall`) with quick-add prompts for newly detected, unmapped IP addresses.
  * **Backward Compatibility:** Accessing legacy `/settings/aliases` automatically redirects to `/settings/sources`.
* **Advanced Settings (`/settings/advanced`):** Runtime controls for AI timeout, reasoning token budget, base application URL, private IP webhook targets, Docker collector toggle and exclusions, trusted proxy headers, cookie security, and Syslog TCP connection ceilings (`AdvancedSettingsCard.tsx`).

## 4. Theme & Visual Styling
* **Dark Operations Aesthetic:** Native dark canvas (`color-scheme: dark;` background `#070a10`) tailored for server operations environments.
* **Typography:** Clean sans-serif typography (`Inter`, system-ui) paired with monospace fonts for logs, timestamps, IP addresses, and code snippets.
* **Layout Stability:** Built-in `scrollbar-gutter: stable` and slim scrollbars (`::-webkit-scrollbar`) to eliminate layout shifts when virtual lists update.

## 5. Build & Asset Distribution
* **Vite Bundling:** Vite compiles production static assets directly into `backend/app/static/`.
* **FastAPI Static Serving:** FastAPI serves static assets with SPA fallback routing to `index.html`.
