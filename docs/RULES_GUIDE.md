# LogShed Rules Guide

This guide explains how to configure real-time alert rules and ingestion drop rules in LogShed, details evaluation logic across rule types, and provides practical examples for common homelab monitoring scenarios.

---

## Table of Contents

- [Alert Rules](#alert-rules)
  - [Overview](#overview)
  - [Rule Types](#rule-types)
  - [Form Fields & Configuration](#form-fields--configuration)
  - [Rate Spike & Culprit Analysis](#rate-spike--culprit-analysis)
  - [Practical Alert Rule Examples](#practical-alert-rule-examples)
  - [Testing Alert Rules](#testing-alert-rules)
- [Ingestion Drop Rules](#drop-rules)
  - [Purpose & Architecture](#purpose--architecture)
  - [Drop Rule Fields & Filtering](#drop-rule-fields--filtering)
  - [Severity Threshold Handling](#severity-threshold-handling)
  - [1-Click Creation & Testing](#1-click-creation--testing)
  - [Practical Drop Rule Examples](#practical-drop-rule-examples)
  - [Preset Bundles & Sharing](#preset-bundles--sharing)

---

<a id="alert-rules"></a>
## Alert Rules

### Overview

The LogShed Alert Engine evaluates incoming syslog and container log batches in memory as logs arrive. When log events match your rule criteria and cross configured thresholds, LogShed records an incident in the audit history and dispatches notifications via your configured notification targets (such as Pushover, Discord, Gotify, Telegram, Ntfy, or custom webhooks).

### Rule Types

LogShed provides three distinct rule evaluation types:

- **Threshold (Sliding Window)**:
  - Tracks matching events within a rolling time window (for example, at least 5 events within 60 seconds).
  - Designed for burst detection, repeated failures, denial of service attempts, and rate anomalies.
  - Keeps an in-memory chronological sliding window for evaluation.
  - Displays linked **Threshold Count** and **Window Duration** options directly below the Rule Type selector.

- **Pattern (Immediate Match)**:
  - Triggers immediately upon a single occurrence of a matching log line (threshold of 1).
  - Designed for high-severity, critical alarms where even a single event demands immediate attention (for example, kernel panics, system halts, or unauthorized root logins).

- **Rate Spike (Velocity Detection)**:
  - Detects sudden surges in log velocity measured in logs per second over rolling time windows.
  - Designed for runaway debug loops, broken network equipment packet flooding, and unexpected log storms across your infrastructure.
  - Calculates moving log velocity over the configured window duration without waiting for full window expiration.
  - Automatically activates in-memory top-culprit analysis during storm events.

### Form Fields & Configuration

When creating or editing an alert rule, you configure the following fields:

#### 1. Rule Name
A clear, descriptive name identifying the alert (for example, `SSH Auth Failure Spike` or `Kernel Panic Detected`). This name appears in notification titles and incident logs.

#### 2. Rule Type
Select between `Threshold (Sliding Window)`, `Pattern (Immediate Match)`, or `Rate Spike (Velocity Detection)`.

#### 3. Target Channel
Select a specific notification target or choose **All Enabled Channels** to broadcast alerts across all active webhooks and endpoints configured in Settings.

#### 4. App Filter (Optional)
A multi-select dropdown listing known applications and daemons discovered across your ingested logs.
- You can select one or more specific apps (for example, `sshd`, `nginx`, `docker`).
- You can also enter custom expressions or wildcards if needed.
- If left empty, all ingested logs from any application or service are evaluated.

#### 5. Max Severity Filter (Optional)
Restricts rule evaluation to logs at or above a specific syslog severity level (syslog levels 0 to 7):
- `0 - Emergency`: System unusable
- `1 - Alert`: Action must be taken immediately
- `2 - Critical`: Critical conditions
- `3 - Error`: Error conditions
- `4 - Warning`: Warning conditions
- `5 - Notice`: Normal but significant condition
- `6 - Info`: Informational messages
- `7 - Debug`: Debug-level messages

Choosing `3 - Error` matches Emergency (0), Alert (1), Critical (2), and Error (3). If left empty, all severity levels are permitted.

#### 6. Match Pattern (Regex or Substring)
A case-insensitive keyword substring or regular expression pattern matched against the log message text and raw payload.
- Case-insensitive substring matching: `failed password`
- Regular expression matching: `Failed (password|publickey) for .* from (?P<ip>\d+\.\d+\.\d+\.\d+)`
- Leave empty or enter `*` to match all log entries satisfying the app and severity filters.

#### 7. Threshold Count & Window Duration (seconds)
*Appears when Rule Type is set to Threshold or Rate Spike.*
- **Threshold Count**: Minimum number of matching events (for Threshold rules) or target logs per second (for Rate Spike rules).
- **Window Duration**: Duration in seconds for the sliding time window (for example, `30` or `60` seconds).

#### 8. Cooldown Flap Dampening (seconds)
Defines how long LogShed must suppress duplicate notifications after an alert fires.
- Prevents alert fatigue and notification flood during ongoing incidents.
- For example, a cooldown of `300` seconds ensures you receive one notification immediately when the incident begins, without receiving hundreds of repeat messages while the issue continues.

#### 9. AI Root-Cause Incident Enrichment
When enabled, LogShed automatically masks sensitive data (passwords, tokens, API keys, IPs) from triggering logs and requests root-cause diagnosis from your configured Large Language Model (Gemini, OpenAI, or local Ollama).
- Appends diagnosis summary and root cause insights to the incident record.
- In notification messages, a concise summary is included along with a link to review the full remediation steps in the LogShed UI.
- If AI providers encounter transient timeouts, LogShed automatically attempts configured fallback models before dispatching the alert.

### Rate Spike & Culprit Analysis

When a rate spike or log storm occurs, diagnosing which container, virtual machine, or script went wild can be tedious. LogShed solves this with zero disk I/O overhead using in-memory culprit frequency analysis.

#### How It Works
- During an active storm, the alert engine inspects the collected in-memory window using frequency counters.
- It calculates:
  - **Top Service / Container**: Identifies the primary offending service and its percentage share of total burst volume (for example, `unbound (1820/2000, 91%)`).
  - **Top Originating Host**: Pinpoints the host IP or alias producing the surge.
  - **Repeated Message Signature**: Isolates the most frequent repeating message pattern causing the storm.
- This attribution is immediately embedded into the alert notification payload sent to your mobile phone or chat channel, giving you immediate clarity on which container or daemon to check without having to query the database.

#### Canary Preset Example: Log Storm Detection
LogShed includes a built-in canary alert preset based on `alert_log_storm.json`:
- **Preset Identifier**: `log_storm_detection`
- **Rule Type**: `Rate Spike (Velocity Detection)`
- **Threshold**: `100` logs/second
- **Window Duration**: `30` seconds
- **Cooldown**: `900` seconds (15 minutes)
- **AI Enrichment**: Enabled
- **Action**: Fires when overall incoming log volume crosses 100 logs/second over a 30-second window, reporting culprit services, volume percentages, and recommended diagnostic steps.

---

### Practical Alert Rule Examples

#### Example 1: SSH Brute Force Infiltration Spike
- **Rule Type**: `Threshold (Sliding Window)`
- **Threshold Count**: `5`
- **Window Duration**: `60` seconds
- **App Filter**: `sshd`
- **Match Pattern**: `Failed password|authentication failure`
- **Cooldown**: `300` seconds
- **AI Enrichment**: Enabled
- **Use Case**: Detects credential stuffing or brute force SSH password attacks from external IP addresses.

#### Example 2: Web Server 5xx Outage Burst
- **Rule Type**: `Threshold (Sliding Window)`
- **Threshold Count**: `10`
- **Window Duration**: `30` seconds
- **App Filter**: `nginx`, `caddy`, `traefik`
- **Match Pattern**: `HTTP/[12]\.[01]" 50[0-9]`
- **Cooldown**: `180` seconds
- **AI Enrichment**: Enabled
- **Use Case**: Alerts when backend microservices or database connections begin failing and serving 500-series server errors to clients.

#### Example 3: Kernel Panic or Hardware Failure
- **Rule Type**: `Pattern (Immediate Match)`
- **Max Severity Filter**: `2 - Critical`
- **Match Pattern**: `Kernel panic|Out of memory|Hardware Error|machine check`
- **Cooldown**: `60` seconds
- **AI Enrichment**: Enabled
- **Use Case**: Instantly notifies systems administrators of operating system kernel crashes, hardware faults, or out-of-memory kernel kills.

#### Example 4: Sudo Elevation Alarm
- **Rule Type**: `Pattern (Immediate Match)`
- **App Filter**: `sudo`
- **Match Pattern**: `COMMAND=/bin/su|NOT in sudoers`
- **Cooldown**: `30` seconds
- **AI Enrichment**: Disabled
- **Use Case**: Security audit alarm for unexpected root escalation attempts or unauthorized sudo invocations.

---

### Testing Alert Rules

Use the **Test Rule** button on any configured rule to open the dry-run tester. Enter sample log messages to verify that your regular expression or substring matches as expected and confirm that offending IP addresses are parsed correctly before activating rules in production.

---

<a id="drop-rules"></a>
## Ingestion Drop Rules

### Purpose & Architecture

In any homelab environment, certain daemons and containers produce overwhelming chatter: routine cron triggers every minute, periodic container health check pings every 10 seconds, or web crawler vulnerability scans hitting 404s.

Ingestion Drop Rules evaluate incoming syslog and container logs **in memory** before SQLite database persistence and FTS5 full-text indexing:
- **Zero Disk Write Overhead**: Dropped logs never hit disk, saving SSD write cycles and keeping SQLite database files lean.
- **Index Protection**: Prevents full-text search indexes from bloating with repetitive noise.
- **Accurate Live Counters**: LogShed maintains in-memory drop counters for each active rule, flushing aggregate counts periodically to SQLite so you can see exactly how many log lines each rule has filtered.

### Drop Rule Fields & Filtering

When creating or editing a drop rule, you configure:

| Field | Description | Purpose |
|---|---|---|
| **Rule Name** | Friendly identifier for the rule | Appears in the Drop Rules table and statistics cards |
| **Host / Source Pattern** | Filter by originating host or IP | Supports wildcards (for example, `192.168.1.*` or `router-*`). Optional if App or Message is set |
| **App / Container Pattern** | Filter by application or container name | Supports wildcards (for example, `CRON`, `systemd*`, or `traefik`). Optional if Host or Message is set |
| **Message Pattern** | Keyword substring or regular expression | Matches log payload. Can be a case-insensitive substring or regex. Enter `*` to match all messages |
| **Regex Toggle** | Regular expression parsing toggle | Enables safe regex evaluation when checked, or fast case-insensitive substring matching when unchecked |
| **Severity Threshold** | Minimum syslog severity level to drop | Preserves critical entries while discarding routine chatter (detailed below) |

### Severity Threshold Handling

The **Severity Threshold** allows conditional filtering based on standard syslog severity levels (0 = Emergency to 7 = Debug).

LogShed drops matching logs only if their severity is at or below the selected severity value (where higher numerical values represent lower severity):
- If Severity Threshold is set to `6 - Info`, LogShed will drop logs matching the criteria if they are `6 - Info` or `7 - Debug`.
- If an application generates an unexpected `3 - Error` or `4 - Warning` matching the same pattern, LogShed **preserves** the log and writes it to SQLite, ensuring critical failures are never missed.
- If Severity Threshold is left unset, all logs matching the pattern are dropped regardless of severity.

### 1-Click Creation & Testing

LogShed makes rule creation effortless:

1. **1-Click Creation from Log Detail**:
   - While browsing the live stream or search results, click any log entry to open the Log Detail modal.
   - Click the **Create Drop Rule** button in the action bar.
   - LogShed pre-populates the rule name, originating host, app name, and message snippet automatically, ready for saving with a single click.

2. **Dry-Run Test Simulation**:
   - Inside the drop rule creation modal, use the integrated test simulator before saving.
   - Enter a sample log message, test against candidate app names or severities, and receive instant feedback confirming whether the entry would be dropped or kept.

### Practical Drop Rule Examples

#### Example 1: Docker Container Healthcheck Noise
- **Rule Name**: `Docker Healthcheck Noise`
- **App Pattern**: `*` (or leave empty)
- **Message Pattern**: `healthcheck|health_status`
- **Regex**: Enabled
- **Severity Threshold**: None
- **Use Case**: Eliminates repetitive internal healthcheck pings from containers like Pi-hole, Vaultwarden, or custom web services that pollute logs every few seconds.

#### Example 2: Routine CRON Chatter
- **Rule Name**: `Routine CRON Noise`
- **App Pattern**: `CRON`
- **Message Pattern**: `CMD|RELOAD|STARTUP`
- **Regex**: Enabled
- **Severity Threshold**: `6 - Info`
- **Use Case**: Silences routine minute-by-minute cron job execution records while preserving any cron error outputs (`3 - Error`).

#### Example 3: Systemd User Session Chatter
- **Rule Name**: `Systemd Session Chatter`
- **App Pattern**: `systemd*`
- **Message Pattern**: `pam_unix(systemd-user:session): session opened|session closed`
- **Regex**: Enabled
- **Severity Threshold**: None
- **Use Case**: Strips out Linux PAM session open and close messages triggered by monitoring scripts and automated background jobs.

#### Example 4: Web Crawler 404 Probes
- **Rule Name**: `Web Crawler 404 Probes`
- **App Pattern**: `nginx` (or `traefik`, `caddy`)
- **Message Pattern**: `"(GET|POST) /(wp-admin|\.env|xmlrpc\.php|phpmyadmin).* 404`
- **Regex**: Enabled
- **Severity Threshold**: None
- **Use Case**: Prevents public-facing reverse proxy search indexes from being inundated by automated bot vulnerability scans.

### Preset Bundles & Sharing

- **1-Click Presets**: Choose from built-in community presets directly in the UI to immediately filter common Linux and Docker noise patterns.
- **JSON Export & Import**: Export all configured drop rules as a clean, human-readable JSON bundle. Import rules across multiple LogShed instances with automatic conflict resolution and deduplication.
