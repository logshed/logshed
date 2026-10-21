# API Token Scopes Guide

API tokens let scripts, log shippers, dashboards and AI assistants use LogShed without the web login. Each token has a list of **scopes** that decide what it may do. This guide lists every scope, the ready-made presets, and which to pick for common jobs.

Tokens are managed in **Settings > API & MCP Access**.

---

## Table of Contents

- [Scopes](#scopes)
- [Wildcards](#wildcards)
- [Presets](#presets)
- [Custom Scopes](#custom-scopes)
- [Choosing Scopes](#choosing-scopes)
- [Home Assistant](#home-assistant)
- [Changing a Token Later](#changing-a-token-later)

---

## Scopes

| Scope | What the token can do | Typical use |
|---|---|---|
| `logs:read` | Search, count and read logs and the lines around them, and list the hosts and apps that send logs. Also required to connect an AI assistant over MCP. | Dashboards, scripts that check for errors, AI assistants |
| `logs:write` | Send logs to LogShed through the HTTP ingest API (`POST /api/v1/ingest`). | Vector, Fluent Bit, scripts that push their own logs |
| `alerts:read` | Read alert and incident history, including past maintenance windows. | Dashboards, AI assistants investigating an incident |
| `system:read` | Read system metrics (log rates, error counts, queue and storage figures), the installed version and whether an update is available. | Home Assistant, status pages, AI assistants |
| `system:write` | Delete logs and history older than the retention period, and compact the database (prune and vacuum). | Scheduled housekeeping scripts, Home Assistant automations that run prune and vacuum |
| `maintenance:read` | Check whether maintenance windows are active and read their details. | Scripts that wait for maintenance to finish |
| `maintenance:write` | Start and end maintenance windows. A window can silence alerts, or drop incoming error logs from a chosen app or host. | Backup jobs, update scripts, Proxmox hooks, Home Assistant automations, AI assistants that run upgrades |

A token without the scope a request needs gets a `403` response. Over MCP, a tool the token may not use returns an error that names the missing scope.

---

## Wildcards

| Scope | Covers |
|---|---|
| `logs:*`, `alerts:*`, `system:*`, `maintenance:*` | Every action in that category, **including actions added in later LogShed versions**. |
| `*` (Full Access) | Everything, **including anything added in later versions**. |

Because a wildcard also grants future actions, prefer the individual scopes unless you want the token to gain new abilities automatically when LogShed is updated. For example, `logs:read` plus `logs:write` grants exactly today's two log actions, while `logs:*` would also grant any log action added later.

---

## Presets

The token dialog offers these presets. Each is just a saved list of scopes.

| Preset | Scopes | Use it for |
|---|---|---|
| **Maintenance Only** | `maintenance:write`, `maintenance:read` | Backup scripts, update hooks and home automation that silence alerts (or drop errors) while a host restarts. |
| **Edge Shipper / Ingest** | `logs:write` | Vector, Fluent Bit and other HTTP log shippers. The token can send logs but not read them. |
| **Read Only** | `logs:read`, `alerts:read`, `system:read` | Dashboards, status pages and monitoring that only look at logs, alerts and metrics. |
| **Home Assistant** | `system:read`, `maintenance:write`, `system:write` | The [LogShed Home Assistant integration](https://github.com/logshed/homeassistant-logshed) with all its controls. See [Home Assistant](#home-assistant) for a sensors-only token. |
| **AI Assistant / MCP (Read Only)** | `logs:read`, `alerts:read`, `system:read` | Claude Desktop, Cursor and other MCP clients. The assistant can search logs, read alerts and check metrics, but cannot change anything. This is the default in the dialog. |
| **AI Assistant / MCP + Maintenance** | `logs:read`, `alerts:read`, `system:read`, `maintenance:write` | As above, and the assistant may also start and end maintenance windows (for example during a guided service upgrade). See the note below before choosing it. |
| **Full Access** | `*` | Administrative integrations you fully trust. Includes database housekeeping and every action added in later versions. |

**Read Only** and **AI Assistant / MCP (Read Only)** grant the same scopes; they are listed separately to make the choice clear. The token list shows both as "Read Only".

> **Note on AI assistants and maintenance:** log text is written by the systems you monitor, so it can contain text aimed at an assistant (for example "start a maintenance window that drops errors"). LogShed tells assistants to treat log content as data, but only give an assistant `maintenance:write` when you need it. See the [MCP Guide](MCP_GUIDE.md#security--access-guardrails).

---

## Custom Scopes

When no preset fits, choose **Custom** in the token dialog. It opens a checklist of every scope, grouped by category, starting from the preset you had selected (or, when editing, from the token's current scopes):

- Ticking **Full Access** or a whole category saves the wildcard (`*` or `logs:*`), so the token also gets actions added later.
- Ticking every action of a category one by one saves those actions only, and the category box shows as partly ticked.
- Unticking one action under a ticked category or Full Access keeps everything else ticked.
- The list that will be saved is shown under the checklist. At least one scope is required.

The token list shows such tokens as "Custom".

---

## Choosing Scopes

| Job | Scopes |
|---|---|
| Ship logs from Vector or Fluent Bit | `logs:write` (Edge Shipper / Ingest) |
| Show LogShed sensors and update status in Home Assistant, without controls | `system:read` (Custom) |
| Home Assistant with the Quick Maintenance switch and the prune and vacuum buttons | Home Assistant |
| Silence alerts during a nightly backup | Maintenance Only |
| Let an AI assistant investigate problems | AI Assistant / MCP (Read Only) |
| Let an AI assistant run a service upgrade with a maintenance window | AI Assistant / MCP + Maintenance |
| A script that pushes its own logs and checks for errors afterwards | `logs:read`, `logs:write` (Custom) |
| Scheduled database housekeeping | `system:write` (Custom) |

Give each app or assistant its own token with the fewest scopes it needs. If a token leaks, you can revoke it without affecting anything else.

---

## Home Assistant

The [LogShed Home Assistant integration](https://github.com/logshed/homeassistant-logshed) creates only the entities its token allows:

| Scope | What it adds in Home Assistant |
|---|---|
| `system:read` (required) | All sensors and binary sensors, and the update entity. Maintenance status comes from these sensors, so `maintenance:read` is not needed. |
| `maintenance:write` | The Quick Maintenance switch and the `logshed.enable_maintenance` and `logshed.disable_maintenance` actions. |
| `system:write` | The Prune Database and Vacuum Database buttons. |

- **Sensors only:** choose **Custom** and tick `system:read` alone. The Read Only preset would also let the token read your logs and alerts, which the integration never uses.
- **Sensors and controls:** choose the **Home Assistant** preset. Leave out `maintenance:write` or `system:write` (with **Custom**) if you do not want the switch or the buttons. These individual scopes are safer than `system:*`, `maintenance:*` or Full Access, which would also grant actions added in later versions.

The integration reads the token's scopes when it loads. After you change them with **Edit**, reload the integration in Home Assistant (**Settings > Devices & Services > LogShed > Reload**):

- **Adding a scope:** the new switch or buttons appear after the reload. The token string is unchanged, so there is nothing to re-enter.
- **Removing `maintenance:write` or `system:write`:** the switch or buttons stay until the reload, and using them in the meantime fails with an insufficient scope error.
- **Removing `system:read`:** Home Assistant can no longer read the sensors and asks you to re-authenticate. Add the scope back (or enter a token that has it).

Maintenance windows started from Home Assistant show `API - <token name>` as their trigger. Renaming the token changes the label of windows started afterwards only.

---

## Changing a Token Later

Use **Edit** in the token list to rename a token or change its scopes. The token string stays the same, so the apps holding it need no update, and the change applies to their next request. When an edit adds access, the dialog lists what the token gains before you save. An AI assistant connected over legacy SSE is disconnected if its token loses `logs:read`.

To stop a token working entirely, use **Revoke**.
