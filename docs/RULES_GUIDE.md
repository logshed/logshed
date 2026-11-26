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
  - [Resetting Counters](#resetting-counters)
  - [Creating an Alert Rule from a Log](#creating-an-alert-rule-from-a-log)
  - [Inspecting an Incident in the Console](#inspecting-an-incident-in-the-console)
- [Ingestion Drop Rules](#drop-rules)
  - [Purpose & Architecture](#purpose--architecture)
  - [Drop Rule Fields & Filtering](#drop-rule-fields--filtering)
  - [Renaming a Host](#renaming-a-host)
  - [Severity Range Handling](#severity-range-handling)
  - [1-Click Creation & Testing](#1-click-creation--testing)
  - [Practical Drop Rule Examples](#practical-drop-rule-examples)
  - [Built-in Presets](#built-in-presets)
  - [Preset Bundles & Sharing](#preset-bundles--sharing)
- [Maintenance Windows](#maintenance-windows)

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
  - Designed for high-severity, critical alarms where even a single event demands immediate attention (for example, kernel panics, system halts, or unexpected root logins).

- **Rate Spike (Velocity Detection)**:
  - Detects sudden surges in log velocity measured in logs per second over rolling time windows.
  - Designed for runaway debug loops, broken network equipment packet flooding, and unexpected log storms across your infrastructure.
  - Calculates moving log velocity over the configured window duration without waiting for full window expiration.
  - Fires when the window holds threshold times window duration matching logs (for example, 100 logs/second over 300 seconds fires at 30,000 logs). The matches are counted rather than kept, so every threshold and window the form allows works and memory use does not grow with them.
  - Automatically activates in-memory top-culprit analysis during storm events.

### Form Fields & Configuration

When creating or editing an alert rule, you configure the following fields:

#### 1. Rule Name
A clear, descriptive name identifying the alert (for example, `SSH Auth Failure Spike` or `Kernel Panic Detected`). This name appears in notification titles and incident logs.

#### 2. Rule Type
Select between `Threshold (Sliding Window)`, `Pattern (Immediate Match)`, or `Rate Spike (Velocity Detection)`.

#### 3. Target Channel
Select a specific notification target or choose **All Enabled Channels** to broadcast alerts across all active webhooks and endpoints configured in Settings.

#### 4. Host Filter (Optional)
A multi-select dropdown listing the hosts in your logs, as the Console's host list does.
- Pick one or more hosts, or type a name, an IP address or a wildcard pattern such as `192.168.1.*` or `nas*`. Any one of them must match.
- A host matches by its name as the Console shows it (its alias, a Docker host's name, or the name a device sent) or by its IP address, the same way drop rules and maintenance targets match hosts.
- A name that a device reported for itself (shown with a question mark in the Console, and marked "self-reported" in this list) can be sent by any device until you make it that address's alias. The dialog says so under the field. To be sure a rule watches one device, use its IP address, or click **Make it the alias** in the log drawer first.
- If left empty, logs from every host are evaluated.

#### 5. App Filter (Optional)
A multi-select dropdown listing known applications and daemons discovered across your ingested logs.
- You can select one or more specific apps (for example, `sshd`, `nginx`, `docker`).
- You can also enter custom expressions or wildcards if needed.
- If left empty, all ingested logs from any application or service are evaluated.

With both a host filter and an app filter, a log must match both.

#### 6. Severity Filter (Optional)
Restricts rule evaluation to logs at the chosen syslog severity level or more severe (syslog levels 0 to 7, where a lower number is more severe). The dropdown shows each level as, for example, `Warning (4) and more severe`, with `Emergency (0) only` and `Debug (7): every level` at the ends:
- `0 - Emergency`: System unusable
- `1 - Alert`: Action must be taken immediately
- `2 - Critical`: Critical conditions
- `3 - Error`: Error conditions
- `4 - Warning`: Warning conditions
- `5 - Notice`: Normal but significant condition
- `6 - Info`: Informational messages
- `7 - Debug`: Debug-level messages

Choosing `Error (3) and more severe` matches Emergency (0), Alert (1), Critical (2), and Error (3). If left empty, all severity levels are permitted.

#### 7. Match Pattern and "Match as a regular expression"
Text matched against the log message and the raw line, ignoring letter case. The checkbox under the field says how it is read:
- **Plain text** (the checkbox off, the start for a new rule): the text is found anywhere in the message, and every character means itself. `failed password`, `C:\Temp` and `disk (sda)` match exactly those characters.
- **Regular expression** (the checkbox on): `Failed (password|publickey) for .* from (?P<ip>\d+\.\d+\.\d+\.\d+)`. When plain text looks like a regular expression (it has one of `* + ? [ ] ( ) { } ^ $ |`, or a code such as `\d` or `\b`), the dialog offers **Match as a regular expression** under the field.
- Leave the pattern empty or enter `*` to match all log entries satisfying the host, app and severity filters, either way. Saving such a rule asks first (**Drop every log that matches?**, naming the hosts, apps and severity), because it discards everything from them; editing a rule that already had no pattern does not ask again.

**Regular expressions:**
- Backslash codes work as in any regular expression: `\bKilled` matches "Killed" at the start of a word, `error \d+` matches "error " followed by digits, and `foo\.bar` matches a literal dot.
- **To match a literal backslash, write `\\`** (for example `C:\\Temp` to match `C:\Temp`), or match the text as plain text. A single backslash starts an escape code, so `C:\temp` means "C:", a tab and "emp".
- A regular expression is checked when you save or import the rule: `C:\Temp` as a regular expression is refused because `\T` is not a valid escape. Plain text is never run as a regular expression, so it is not checked.
- Patterns that are prone to catastrophic backtracking, such as `(a|a)+` or `(.*a){20}`, are refused when you save or import a rule as a regular expression. Rules saved earlier keep running under the time limit below.

**Rules made before 1.4.0** keep matching as they did: the upgrade sets the checkbox on for each pattern LogShed read as a regular expression (one with any of `* + ? [ ] ( ) { } ^ $ | \`), and off for the rest, including a stored `C:\Temp` that was matched as plain text because it is not a valid regular expression. Rule files exported from 1.3.x, and API requests that leave `is_regex` out, are read the same way.

**Time limit and automatic disabling:** each match attempt is stopped after 50 ms (`USER_REGEX_TIMEOUT_MS`) and counts as no match, with a warning in the LogShed log that names the rule. A rule whose pattern times out 20 times within 5 minutes is **disabled automatically**: it is saved as disabled (it shows as off in the rules list), and LogShed logs an error naming the rule and the reason. Simplify the pattern (for example, anchor it or replace `.*` with a narrower class), then switch the rule on again. This applies to alert rules and to drop rules alike.

#### 8. Threshold Count & Window Duration (seconds)
*Appears when Rule Type is set to Threshold or Rate Spike.*
- **Threshold Count**: Minimum number of matching events (for Threshold rules) or target logs per second (for Rate Spike rules).
- **Window Duration**: Duration in seconds for the sliding time window (for example, `30` or `60` seconds).

#### 9. Cooldown Flap Dampening (seconds)
Defines how long LogShed must suppress duplicate notifications after an alert fires.
- Prevents alert fatigue and notification flood during ongoing incidents.
- For example, a cooldown of `300` seconds ensures you receive one notification immediately when the incident begins, without receiving hundreds of repeat messages while the issue continues.

#### 10. AI Root-Cause Incident Enrichment
When enabled, LogShed automatically masks sensitive data (passwords, tokens, API keys, IPs) from triggering logs and requests root-cause diagnosis from your configured Large Language Model (Gemini, OpenAI, or local Ollama).
- Appends diagnosis summary and root cause insights to the incident record.
- Each log line sent to the model gives its time in UTC and in the server's time zone (`TZ`), so the diagnosis can name both.
- The model gets the AI System Instructions saved in **Settings**, as Analyze with AI does.
- In notification messages, a concise summary is included along with a link to review the full remediation steps in the LogShed UI.
- If AI providers encounter transient timeouts, or a Claude model declines the request, LogShed automatically attempts configured fallback models before dispatching the alert.

### Rate Spike & Culprit Analysis

When a rate spike or log storm occurs, diagnosing which container, virtual machine, or script went wild can be tedious. LogShed solves this with zero disk I/O overhead using in-memory culprit frequency analysis.

#### How It Works
- While counting a rate rule's matches, the alert engine keeps frequency counters for their apps, hosts and message starts, plus the newest 50 matches. A storm of any size uses the same small amount of memory.
- When the rule fires, it calculates:
  - **Top Service / Container**: Identifies the primary offending service and its percentage share of total burst volume (for example, `unbound (1820/2000, 91%)`).
  - **Top Originating Host**: Pinpoints the host IP or alias producing the surge.
  - **Repeated Message Signature**: Isolates the most frequent repeating message pattern causing the storm.
- The figures cover every match in the window. Each span of a sixtieth of the window counts at most 100 different apps, hosts and message starts (further ones in that span are left out of the figures, but not out of the rate), and they may include matches from up to a sixtieth of a window before it. The notification sample is the newest match of the top service, and AI enrichment reads the newest 50 matches.
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
- **Severity Filter**: `Critical (2) and more severe`
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
- **Use Case**: Security audit alarm for unexpected root escalation attempts or sudo use that was not approved.

---

### Testing Alert Rules

Use the **Test Rule** button on any configured rule to open the dry-run tester. Enter sample log messages to verify that your regular expression or substring matches as expected and confirm that offending IP addresses are parsed correctly before activating rules in production.

To see how often a rule would have matched real logs, click **Check last 24h** in the rule dialog while you write it. LogShed runs the rule's own matching (its filters and pattern, exactly as a saved rule matches) over the logs stored in the last 24 hours, newest first, and shows how many matched with the newest five. It counts single logs: a threshold rule fires only when enough of them fall inside its window, and cooldowns and maintenance windows are not applied, so the rule alerts less often than the count suggests. On a busy server the check stops after 200,000 logs or 5 seconds and says how far back it reached. One check runs at a time.

### Resetting Counters

Each alert rule counts how often it fired ("Fired 3x" in the list), and each drop rule how many logs it dropped. To start a count again:

- **One rule:** open it with Edit and click **Reset counter to 0**. The editor stays open, and the count shows 0 at once.
- **Every rule:** click **Reset all counters** at the top of the Alert Rules or Drop Rules tab. A dialog says how many rules and what total it clears before anything changes.

A reset sets only the count to 0: an alert rule keeps its "Last triggered" time and its incidents in Alert History, and the next firing (or dropped log) counts from 1. Changing a drop rule's host, app, pattern or severity also resets its counter, since the old count no longer describes it.

### Creating an Alert Rule from a Log

In the Console, click a log entry to open the Log Detail drawer, then click **Create Alert Rule** (next to **Create Drop Rule**). A new rule dialog opens on top of the drawer, starting with:
- the name `Alert on <app>` (or `Alert on <host>` for a log with no app), the log's host in the Host Filter (its name as the Console shows it, or its IP address when it has none), and the log's app in the App Filter (when LogShed has seen that app before);
- an empty Severity Filter and Match Pattern;
- a **From log** box under the Match Pattern field with the log message as the log drawer shows it (a copy: editing it changes nothing). Select the part you want to match and click **Use selection as pattern**: it becomes the Match Pattern, as written for plain text, or escaped (so every character means itself) when **Match as a regular expression** is ticked. Ticking or unticking the box afterwards rewrites the pattern to match the same text, until you edit it. The drawer leaves out colour codes, so select text that has none inside it.

Remove the host from the Host Filter to watch the app on every host. Saving or closing the dialog takes you back to the same log in the drawer; the new rule is listed in **Rules & History > Alert Rules**.

### Inspecting an Incident in the Console

In **Rules & History > History**, open an alert raised by a rule and click **Inspect in Console** (or **View in Console** next to the triggering log snippet). The Console opens on the alert's host, from 1 minute before the alert (or the rule's window, when that is longer and the rule still exists) to 1 minute after it, scrolled to the log that triggered the alert, which is highlighted in amber. Alerts recorded before LogShed kept that log open the same range without a highlight. You can widen the range with the time pickers. **Same app only** also filters to the alert's app. Daily Digest and On-Demand Analysis items have no Console link.

---

<a id="drop-rules"></a>
## Ingestion Drop Rules

### Purpose & Architecture

In any homelab environment, certain daemons and containers produce overwhelming chatter: routine cron triggers every minute, periodic container health check pings every 10 seconds, or web crawler vulnerability scans hitting 404s.

Ingestion Drop Rules evaluate incoming syslog and container logs **in memory** before SQLite database persistence and FTS5 full-text indexing:
- **Zero Disk Write Overhead**: Dropped logs never hit disk, saving SSD write cycles and keeping SQLite database files lean.
- **Index Protection**: Prevents full-text search indexes from bloating with repetitive noise.
- **Accurate Live Counters**: LogShed maintains in-memory drop counters for each active rule, flushing aggregate counts periodically to SQLite so you can see exactly how many log lines each rule has filtered. See [Resetting Counters](#resetting-counters) to start a count again.

### Drop Rule Fields & Filtering

When creating or editing a drop rule, you configure:

| Field | Description | Purpose |
|---|---|---|
| **Rule Name** | Friendly identifier for the rule | Appears in the Drop Rules table and statistics cards |
| **Hosts or IPs** | Filter by originating hosts or IPs | Pick hosts from the list or type patterns with wildcards (for example, `192.168.1.*` or `router-*`). A log matches when any one of them matches its alias or IP. Optional if App or Message is set |
| **Apps or Containers** | Filter by application or container names | Pick apps from the list or type patterns with wildcards (for example, `CRON`, `systemd*`, or `traefik`). A log matches when any one of them matches. Optional if Host or Message is set |
| **Message Pattern** | Keyword substring or regular expression | Matches log payload. Can be a case-insensitive substring or regex. Enter `*` to match all messages |
| **Regex Toggle** | Regular expression parsing toggle | Enables safe regex evaluation when checked (with a 50 ms limit per match; a rule that times out 20 times in 5 minutes is disabled automatically, see the alert rule notes above), or fast case-insensitive substring matching when unchecked. With the toggle on, write `\\` for a literal backslash |
| **Severity Range** | Drop only logs at this level or less severe | Preserves critical entries while discarding routine chatter (detailed below) |

When a rule has both hosts and apps, a log must match one of the hosts and one of the apps. One rule for `cron, sshd, systemd-logind` replaces three rules with the same message pattern. The rule stores each list as one comma-separated field (`cron, sshd, systemd-logind`, up to 255 characters), which is also how it appears in exports and in the API. Leave a list empty to match every host or app.

LogShed 1.3.0 and earlier read such a list as one name, so after going back to 1.3.0 a rule with several hosts or apps matches nothing until you edit it.

### Renaming a Host

When you rename a syslog host alias or a Docker host alias under Settings > Sources, LogShed updates what names the host by its old alias, so it keeps matching the host's new logs. Deleting a syslog host alias works the same way, with the host's IP address as the new name, because its logs show the IP again:
- drop rules and alert rules that list the old alias exactly (any letter case) among their hosts; only that entry changes,
- running maintenance windows and maintenance schedules aimed at the host,
- saved views that filter on the host.

Some things are not changed:
- **Wildcard patterns** such as `nas*`: check that they still fit the new name.
- **A name another host still uses:** while another syslog host or Docker host has the old alias, the rules and views keep it, because it still matches that host.
- **A host's first alias:** until a host has an alias, its logs carry the host name it sends (or its IP), and a rule written against that name stops matching once you give the host an alias. Write such rules with the host's IP address, which an alias does not change, or edit them after adding the alias. A rule that names the IP keeps matching after you add an alias, and the drop rule dialog shows the link: in the host list each alias shows its IP, and an IP that has an alias shows that alias, with a note under the field.

Links and bookmarks with the old name in them are not changed.

### Severity Range Handling

The **Severity Range** allows conditional filtering based on standard syslog severity levels (0 = Emergency to 7 = Debug).

LogShed drops matching logs only if their severity is the selected level or less severe (a higher number is less severe). The dropdown shows each level as, for example, `Info (6) and less severe`, and `Debug (7) only` for debug logs alone:
- If Severity Range is set to `Info (6) and less severe`, LogShed will drop logs matching the criteria if they are `6 - Info` or `7 - Debug`.
- If an application generates an unexpected `3 - Error` or `4 - Warning` matching the same pattern, LogShed **preserves** the log and writes it to SQLite, ensuring critical failures are never missed.
- If Severity Range is left unset, all logs matching the pattern are dropped regardless of severity.

### 1-Click Creation & Testing

LogShed makes rule creation effortless:

1. **1-Click Creation from Log Detail**:
   - While browsing the live stream or search results, click any log entry to open the Log Detail modal.
   - Click the **Create Drop Rule** button in the action bar.
   - LogShed fills in a rule name, the host (its alias, or the IP when the host has no alias), the app name and the whole log message. Shorten the message to the part that repeats: while the pattern is still the whole line, a tip suggests removing the numbers, times or IDs that change between lines. Or select that part in the tester's **Sample Log Message** box and click **Use selection as pattern**. A drop rule's plain text reads `*` and `?` as wildcards, so a selection holding either is put in as an escaped regular expression (the Regular Expression box is ticked), and it matches only that text.

2. **Dry-Run Test Simulation**:
   - Inside the drop rule creation modal, use the integrated test simulator before saving.
   - Enter a sample log message, test against candidate app names or severities, and receive instant feedback confirming whether the entry would be dropped or kept.
   - Click **Check last 24h** to see how many logs stored in the last 24 hours the rule would have dropped, with the newest five (on a busy server, the newest 200,000 logs or what 5 seconds allow). Only stored logs can be counted: logs that a drop rule already discarded are not, so after you save a rule its own matches no longer appear.

### Practical Drop Rule Examples

#### Example 1: Docker Container Healthcheck Noise
- **Rule Name**: `Docker Healthcheck Noise`
- **App Pattern**: `*` (or leave empty)
- **Message Pattern**: `healthcheck|health_status`
- **Regex**: Enabled
- **Severity Range**: None
- **Use Case**: Eliminates repetitive internal healthcheck pings from containers like Pi-hole, Vaultwarden, or custom web services that pollute logs every few seconds.

#### Example 2: Routine CRON Chatter
- **Rule Name**: `Routine CRON Noise`
- **App Pattern**: `CRON`
- **Message Pattern**: `CMD|RELOAD|STARTUP`
- **Regex**: Enabled
- **Severity Range**: `Info (6) and less severe`
- **Use Case**: Silences routine minute-by-minute cron job execution records while preserving any cron error outputs (`3 - Error`).

#### Example 3: Systemd User Session Chatter
- **Rule Name**: `Systemd Session Chatter`
- **App Pattern**: `systemd*`
- **Message Pattern**: `pam_unix(systemd-user:session): session opened|session closed`
- **Regex**: Enabled
- **Severity Range**: None
- **Use Case**: Strips out Linux PAM session open and close messages triggered by monitoring scripts and automated background jobs.

#### Example 4: Web Crawler 404 Probes
- **Rule Name**: `Web Crawler 404 Probes`
- **App Pattern**: `nginx` (or `traefik`, `caddy`)
- **Message Pattern**: `"(GET|POST) /(wp-admin|\.env|xmlrpc\.php|phpmyadmin).* 404`
- **Regex**: Enabled
- **Severity Range**: None
- **Use Case**: Prevents public-facing reverse proxy search indexes from being inundated by automated bot vulnerability scans.

### Built-in Presets

**Presets** on the Alert Rules and Drop Rules tabs opens a library of ready-made rules. Choose a category to show only its presets, then **Install Rule** to add one as a normal rule that you can edit, test or delete. Installing a preset again adds a second copy. Each preset is tested against sample lines in its tools' own formats, and a drop preset with a severity range never drops a warning or an error.

Most presets match on the message, not the app name, because app and container names differ between installs. Check a preset with **Test rule** on your own logs before you rely on it.

#### Alert presets

| Preset | Category | What it catches | What to adjust |
|---|---|---|---|
| **Certificate Renewal Failures** (`cert_renewal_failure`) | Security | Catches failed certificate renewals from certbot, acme.sh, Traefik and Caddy. These tools retry for days before a certificate expires, so it alerts at most every 6 hours. | None usually. |
| **Fail2ban Ban Wave** (`fail2ban_bans`) | Security | Alerts when fail2ban bans 5 or more addresses within 10 minutes, in any jail. Bans restored after a fail2ban restart are not counted. Lower the threshold to 1 to hear about every ban. | Set the threshold to 1 for every ban, or raise it on a busy host. |
| **Web Proxy 401/403 Auth Flood** (`proxy_auth_flood`) | Security | Detects high-volume bursts of 401 Unauthorized and 403 Forbidden responses from proxies. | Raise the threshold if a login page sees many failed logins. |
| **SSH Brute-Force Detection** (`ssh_bruteforce`) | Security | Detects repeated failed SSH authentication attempts from offending IP addresses. | Raise the threshold on a host exposed to the internet. |
| **Sudo Privilege Escalation** (`sudo_escalation`) | Security | Detects execution of commands run with elevated root privileges via sudo. | Set an app filter or a longer cooldown if scripts run sudo often. |
| **Disk Health Warning (SMART)** (`disk_smart_failure`) | Storage and disks | Catches smartd reports of a failing disk: a failed health self-check, a failed attribute, pending or offline uncorrectable sectors, a change in the reallocated or pending sector attributes, and new self-test or ATA errors. Needs smartd running on the host. Disks behind some RAID controllers report nothing to smartd. | Pending sectors repeat at every smartd check, so the cooldown is 6 hours. |
| **Filesystem and Disk I/O Errors** (`filesystem_errors`) | Storage and disks | Catches kernel errors from EXT4, XFS and Btrfs and block layer I/O errors (Buffer I/O error, I/O error on a sector). A failing disk logs many of these at once, so it alerts at most every 30 minutes. | None usually. |
| **Software RAID Degraded (mdadm)** (`mdadm_degraded`) | Storage and disks | Catches mdadm monitor events for a degraded array, a failed member or spare and a vanished device, and the kernel's disk failure notice for md arrays. Needs mdadm --monitor (enabled by most distributions). | None usually. |
| **ZFS Pool Errors** (`zfs_pool_errors`) | Storage and disks | Catches ZFS event daemon (zed) lines for checksum, I/O and data errors and for a device going DEGRADED, FAULTED, REMOVED or UNAVAIL. Needs zed with its syslog script (the default). Repeated errors on one pool alert once an hour. | None usually; a scrub that finds errors alerts too. |
| **Backup Failures** (`backup_failure`) | Backups | Catches failed Proxmox VE backups (vzdump, including jobs to Proxmox Backup Server), borg runs that end with an error (only with --show-rc), and common restic failures (wrong password, repository not found or locked). Tools that do not write to syslog or Docker logs, such as Duplicati, are not covered. | Add your backup tool's own failure text to the pattern. |
| **UPS On Battery or Low Battery** (`ups_power`) | Power | Catches apcupsd and NUT (upsmon) notices for a power failure, running on battery, a low or exhausted battery, a battery that needs replacing and lost communication with the UPS. Power coming back is not matched. | Lengthen the cooldown if the power flickers often. |
| **Gateway Down or Packet Loss** (`wan_gateway_alarm`) | Network | Catches dpinger gateway alarms on OPNsense and pfSense (high latency or packet loss) and gateways dropped from a gateway group. A gateway coming back is not matched. | Raise dpinger's alarm thresholds on the firewall if a busy link alarms often. |
| **Proxmox Cluster Trouble** (`proxmox_cluster`) | VMs and containers | Catches corosync link loss, a node losing quorum, a lost cluster member and HA fencing in a Proxmox VE cluster. A single-node install never logs these. | None usually. |
| **Critical or Worse** (`critical_severity`) | System | Alerts on any log at Critical (2), Alert (1) or Emergency (0) severity, from any app. Some apps log routine messages as critical: add a drop rule for those, or raise the bar to Alert (1). | Raise the severity to Alert (1), or drop the apps that log routine lines as critical. |
| **Hardware Errors (MCE, ECC, PCIe)** (`hardware_errors`) | System | Catches machine check exceptions, corrected and uncorrected ECC memory errors (EDAC) and uncorrected PCIe errors (AER). Corrected PCIe errors are not matched. | None usually. |
| **Log Storm & Rate Spike Detection** (`log_storm_detection`) | System | Alerts when incoming log volume exceeds 100 logs/second over 30s and identifies culprit services. | Raise the rate on a busy install. |
| **Kernel Out-Of-Memory (OOM) Kill** (`oom_killer`) | System | Detects Linux kernel out-of-memory killer invocations and terminated container tasks. | None usually. |
| **Program Crashes (Segfaults)** (`segfault`) | System | Catches kernel reports of a program crashing with a segmentation fault or general protection fault. A crash loop alerts at most every 30 minutes. | Add an app filter to watch only some programs. |
| **Systemd Unit Failed** (`systemd_unit_failed`) | System | Catches systemd units (services, mounts, timers, sockets) entering the failed state. Containers without systemd never log this. | None usually. |
| **CPU or Disk Overheating** (`temperature_warning`) | System | Catches the kernel's CPU throttling notices and smartd disk temperature limits (smartd needs a limit set with -W). Alerts at most once an hour. | None usually. |

#### Drop presets

| Preset | Category | What it catches | What to adjust |
|---|---|---|---|
| **SMART Attribute Changes** (`smartd_attribute_chatter`) | Storage and disks | Drops smartd notices of routine SMART attributes changing: temperature, read and seek error rates, ECC recovered, spin-up time, hours, load cycles and wear. Changes to every other attribute, including reallocated, pending and uncorrectable sectors, are kept. | None usually. |
| **DNS Query Chatter** (`dns_query_chatter`) | Network | Drops the per-query lines of dnsmasq and Pi-hole (query, forwarded, reply, cached). Blocked queries (gravity blocked) and warnings are kept. | None usually. |
| **Firewall Pass Entries** (`firewall_pass`) | Network | Drops OPNsense and pfSense firewall log (filterlog) lines for passed traffic. Blocked and rejected traffic is kept. | None usually. |
| **Health and Metrics Endpoint Requests** (`health_endpoint_access`) | Network | Drops successful (2xx) GET and HEAD requests to /health, /healthz, /healthcheck, /ping, /metrics, /ready, /readyz and /livez in nginx, Apache, Traefik (common log format) and Caddy (JSON) access logs. Failing health checks are kept. | Add your own health paths to the pattern. |
| **Docker Healthcheck Noise** (`docker_healthcheck`) | VMs and containers | Filters repetitive container health checks and status probe messages. | None usually. |
| **Proxmox Worker and Status Chatter** (`proxmox_status_chatter`) | VMs and containers | Drops Proxmox VE daemon worker restarts (worker started, finished, starting workers) and pvestatd's status update time notices. A status update time line means a status cycle took over 5 seconds, often slow storage: leave this preset off while you look into that. | Leave it off while you look into slow storage. |
| **Routine CRON Noise** (`cron_noise`) | System | Filters routine CRON execution and service reload notices. | None usually. |
| **Rsyslog MARK Lines** (`rsyslog_mark`) | System | Drops the -- MARK -- lines that rsyslog and syslogd write at a fixed interval to show they are alive. | None usually. |
| **Systemd Session Chatter** (`systemd_session`) | System | Filters routine systemd PAM user session opened and closed logs. | None usually. |
| **Systemd Timer and Slice Chatter** (`systemd_timer_chatter`) | System | Drops systemd's routine lines for user slices and sessions being created and removed, timer jobs finishing, units stopping cleanly (Deactivated successfully) and CPU time used. Failures and units starting are kept. | None usually. |
| **Radarr and Sonarr Refresh Chatter** (`arr_chatter`) | Apps | Drops the routine library refresh lines of Radarr, Sonarr, Lidarr and Readarr: updating info, scanning disk, and finding no existing metadata, subtitle or extra files. Matched on the message, so it works whatever the containers are called. | None usually. |
| **Home Assistant Custom Integration Warnings** (`ha_custom_integration`) | Apps | Drops the warning Home Assistant logs at every start for each custom integration (for example those installed with HACS): "We found a custom integration ... which has not been tested by Home Assistant". | None usually. |

#### Your own presets

JSON files in `/data/presets/alerts/` and `/data/presets/drops/` appear in the same libraries, marked **Custom**. A file with the `id` of a built-in preset replaces it. Give a file a `category` to list it under one of the categories above, or a new one; without it, the preset is listed under **Other**.

### Preset Bundles & Sharing

- **JSON Export & Import**: Export all configured drop rules as a clean, human-readable JSON bundle. Import rules across multiple LogShed instances with automatic conflict resolution and deduplication.

<a id="maintenance-windows"></a>
## Maintenance Windows

**Rules & History > Maintenance** holds back alerts while you work on a machine: start a window on demand (for 1, 4, 8 or 24 hours, or until a time you choose) or on a daily, weekly or monthly schedule. Home Assistant, the REST API (`POST /api/v1/maintenance/enable`) and MCP start the same windows.

Each window has a **Mode** and, optionally, a **Host** and an **App**:
- **Silence alerts**: with no host or app, every alert notification is held back while the window runs (alerts are still recorded in History). With a host, an app or both, only the target is covered: its logs are stored and shown as usual but count towards no alert rule, and every other alert is sent. A rule that watches several hosts then fires only on what the other hosts send.
- **Silence alerts and drop error logs**: the same, and error, critical, alert and emergency logs from the target are discarded before they are stored (not shown, not counted, not recoverable). Use it for the burst of errors a restart or a backup causes. Other logs are kept.

The host matches a log's name as the Console shows it or its IP address, and the host and app can be patterns such as `nas*` or `192.168.1.*`. With both, a log must match both. Dropping errors from every source (that mode with no host or app) asks you to confirm first, for a session when you start it and for a schedule when you save it; the schedule list marks such a schedule. MCP clients cannot start it: over MCP, dropping errors needs a target.

While any window without a target runs, every notification is held back, whatever other windows run beside it. Schedules saved before LogShed 1.4.0 have no mode or target, so they silence every alert notification as they did. A session started with a target before the upgrade, and still running after it, covers only its target from then on.
