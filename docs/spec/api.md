# Specification: API Specifications & Contracts

Parent document: [docs/SPEC.md](file:///home/ben/workspace/logshed/docs/SPEC.md)

## 1. Route Versioning & Mirror Router
The internal Web UI uses the unversioned `/api/*` prefix. For external tools, third-party integrations, and script automation, a mirror router is mounted at `/api/v1/*`, guaranteeing backwards-compatible route stability. All endpoints documented below are available identically under both `/api/*` and `/api/v1/*`.

## 2. Query Pagination & Evaluation Capping
When searching or listing log records via `GET /api/logs`, the response returns a `LogListResponse` payload containing:
- `logs`: Array of `LogEntry` records matching query filters.
- `total`: Total count of matching records up to the evaluation ceiling.
- `total_capped`: Boolean flag indicating whether the total count was capped at the evaluation limit (`max(1001, offset + limit + 1)`). To prevent full-table scan bottlenecks on vast datasets under broad filters, SQLite limits the count calculation. When `total_capped` is true, the user interface displays a count indicator like `1,000+` or `(capped)` rather than executing expensive exhaustive table scans.

## 3. Endpoint Directory

| Method | Endpoint | Purpose | Payload / Parameters | Response Model | Status Codes |
| --- | --- | --- | --- | --- | --- |
| **Authentication** | | | | | |
| `POST` | `/api/auth/setup` | First-time admin creation (returns `403` if already configured) | `{"password": "..."}` | `MessageResponse` (`{"status": "ok"}`) | `200`, `403` |
| `POST` | `/api/auth/login` | Session login (rate-limited) | `{"password": "..."}` | `MessageResponse` (`{"status": "ok"}`) | `200`, `401`, `429` |
| `POST` | `/api/auth/logout` | Invalidate session cookie | None | `MessageResponse` (`{"status": "ok"}`) | `200` |
| `GET` | `/api/auth/status` | Read setup status and current session authentication state | None | `AuthStatusResponse` (`{"setup_required": bool, "authenticated": bool}`) | `200` |
| `POST` | `/api/auth/password` | Update admin password for authenticated session | `{"current_password": "...", "new_password": "..."}` | `MessageResponse` (`{"status": "ok"}`) | `200`, `400`, `401` |
| **Log Management & Deletion** | | | | | |
| `GET` | `/api/logs` | Search & filter logs. Supports FTS5 query syntax, source/app filtering, severity threshold (`severity_max` 0-7), ISO datetime range, limit, offset. Returns `logs`, `total`, and `total_capped` | `query`, `source` (multi), `app_name` (multi), `severity_max`, `from`, `to`, `limit`, `offset` | `LogListResponse` | `200` |
| `GET` | `/api/logs/stream` | Real-time Server-Sent Events (SSE) streaming incoming log entries | `severity_max`, `source`, `app_name` | Server-Sent Events (`text/event-stream`) | `200` |
| `GET` | `/api/logs/facets` | Fetch all distinct sources, apps, and their mappings | None | `FacetsResponse` | `200` |
| `GET` | `/api/logs/{id}/context` | Fetch surrounding context lines symmetrically around target log | `lines` (default 10), `same_app: bool` (default `false`) | `ContextResponse` | `200`, `404` |
| `DELETE` | `/api/logs/{id}` | Delete a single log record by primary key ID | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| `POST` | `/api/logs/delete` | Batch targeted deletion of logs matching filter criteria or specific IDs (requires confirmation parameters or `delete_all: true`) | `{"log_ids": [...], "query": "...", "source": [...], "app_name": [...], "severity_max": ..., "from": "...", "to": "...", "delete_all": bool}` | `LogDeleteResponse` (`{"deleted_count": int}`) | `200`, `400` |
| `POST` | `/api/logs/delete/preview` | Calculate count of logs matching deletion criteria without deleting them | Same payload as `/api/logs/delete` | `LogDeletePreviewResponse` (`{"match_count": int}`) | `200`, `400` |
| **Alert Rules & Incident History** | | | | | |
| `GET` | `/api/alerts/rules` | List all configured alert rules | None | `list[AlertRule]` | `200` |
| `POST` | `/api/alerts/rules` | Create a new alert rule | `{"name": "...", "rule_type": "...", "channel_id": ..., "filter_app": "...", "filter_severity": ..., "match_pattern": "...", "threshold_count": 1, "window_seconds": 60, "cooldown_seconds": 300, "ai_enrichment": false, "is_enabled": true}` | `AlertRule` | `201`, `400` |
| `GET` | `/api/alerts/rules/{rule_id}` | Retrieve a single alert rule by ID | None | `AlertRule` | `200`, `404` |
| `PUT` | `/api/alerts/rules/{rule_id}` | Update an existing alert rule | Partial or complete rule fields | `AlertRule` | `200`, `404` |
| `PUT` | `/api/alerts/rules/reorder` | Atomically update evaluation and display ordering of alert rules | `{"rule_ids": [...]}` | `MessageResponse` (`{"status": "ok"}`) | `200`, `400` |
| `DELETE` | `/api/alerts/rules/{rule_id}` | Delete an alert rule | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| `POST` | `/api/alerts/test` | Test alert rule match pattern against historical logs (dry-run) | `{"rule_type": "...", "match_pattern": "...", "filter_app": "...", "filter_severity": ..., "window_seconds": ..., "threshold_count": ..., "sample_size": ...}` | `AlertTestResponse` | `200` |
| `GET` | `/api/alerts/export` | Export all alert rules as JSON bundle | None | `AlertRulesExportResponse` (`{"rules": [...]}`) | `200` |
| `GET` | `/api/alerts/{rule_id}/export` | Export a single alert rule as JSON | None | `AlertRule` | `200`, `404` |
| `POST` | `/api/alerts/import` | Import alert rules from JSON payload | `{"rules": [...], "collision_strategy": "skip" \| "overwrite" \| "rename"}` | `AlertRulesImportResponse` | `200`, `400` |
| `GET` | `/api/alerts/presets` | List built-in security canary alert presets (e.g., SSH brute force, OOM killer, sudo escalation) | None | `list[AlertPreset]` | `200` |
| `POST` | `/api/alerts/presets/{preset_id}/install` | Install a built-in alert preset | `{"channel_id": ..., "is_enabled": true}` | `AlertRule` | `200`, `404` |
| `GET` | `/api/alerts/history` | Fetch paginated incident firing history with AI summaries, full prompt envelopes, model identifiers, and token breakdowns | Query params: `rule_id`, `limit`, `offset` | `IncidentHistoryResponse` | `200` |
| `DELETE` | `/api/alerts/history/{history_id}` | Delete a single incident history record | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| `DELETE` | `/api/alerts/history` | Clear all incident firing history records | None | `MessageResponse` (`{"status": "ok"}`) | `200` |
| `GET` | `/api/alerts/maintenance` | Retrieve current alert maintenance window status and recurring schedules | None | `MaintenanceStatusResponse` | `200` |
| `POST` | `/api/alerts/maintenance` | Activate or clear active on-demand maintenance window | `{"until": "<ISO-8601 or null>"}` | `MaintenanceStatusResponse` | `200` |
| `POST` | `/api/alerts/maintenance/schedules` | Update recurring maintenance schedules | `{"schedules": [{"day_of_week": 0, "start_time": "02:00", "end_time": "04:00", "is_enabled": true}]}` | `MaintenanceStatusResponse` | `200`, `400` |
| **Ingestion Drop Rules** | | | | | |
| `GET` | `/api/drop_rules` | List all configured drop rules with drop counters | None | `list[DropRule]` | `200` |
| `POST` | `/api/drop_rules` | Create a new drop rule | `{"name": "...", "source_pattern": "...", "app_pattern": "...", "message_pattern": "...", "is_regex": false, "severity_threshold": ..., "is_enabled": true}` | `DropRule` | `201`, `400` |
| `PUT` | `/api/drop_rules/{rule_id}` | Update an existing drop rule | Partial or complete drop rule fields | `DropRule` | `200`, `404` |
| `PUT` | `/api/drop_rules/reorder` | Atomically update evaluation and display ordering of drop rules | `{"rule_ids": [...]}` | `MessageResponse` (`{"status": "ok"}`) | `200`, `400` |
| `DELETE` | `/api/drop_rules/{rule_id}` | Delete a drop rule | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| `POST` | `/api/drop_rules/{rule_id}/reset` | Reset dropped counter for a specific drop rule to zero | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| `POST` | `/api/drop_rules/test` | Dry-run test a drop rule pattern against recent logs | `{"source_pattern": "...", "app_pattern": "...", "message_pattern": "...", "is_regex": false, "severity_threshold": ..., "sample_size": ...}` | `DropRuleTestResponse` | `200` |
| `GET` | `/api/drop_rules/export` | Export all drop rules as JSON bundle | None | `DropRulesExportResponse` (`{"rules": [...]}`) | `200` |
| `GET` | `/api/drop_rules/{rule_id}/export` | Export a single drop rule as JSON | None | `DropRule` | `200`, `404` |
| `POST` | `/api/drop_rules/import` | Import drop rules from JSON payload | `{"rules": [...], "collision_strategy": "skip" \| "overwrite" \| "rename"}` | `DropRulesImportResponse` | `200`, `400` |
| `GET` | `/api/drop_rules/presets` | List built-in drop rule presets | None | `list[DropRulePreset]` | `200` |
| `POST` | `/api/drop_rules/presets/{preset_id}/install` | Install a built-in drop preset | None | `DropRule` | `200`, `404` |
| **Notification Channels & Daily Digest** | | | | | |
| `GET` | `/api/notifications/channels` | List configured Apprise notification channels | None | `list[NotificationChannel]` | `200` |
| `POST` | `/api/notifications/channels` | Create a new notification channel | `{"name": "...", "url": "...", "is_enabled": true}` | `NotificationChannel` | `201`, `400` |
| `PUT` | `/api/notifications/channels/{channel_id}` | Update an existing notification channel | `{"name": "...", "url": "...", "is_enabled": true}` | `NotificationChannel` | `200`, `404` |
| `DELETE` | `/api/notifications/channels/{channel_id}` | Delete a notification channel | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| `POST` | `/api/notifications/test` | Test dispatch a notification to a specific URL or channel ID | `{"url": "..."}` or `{"channel_id": ...}` | `MessageResponse` (`{"status": "ok"}`) | `200`, `400` |
| `POST` | `/api/notifications/digest/send` | Trigger on-demand dispatch of the 24-hour daily analytical digest | None | `MessageResponse` (`{"status": "ok"}`) | `200` |
| **Saved Views** | | | | | |
| `GET` | `/api/saved_views` | List all saved search and filter views | None | `list[SavedView]` | `200` |
| `POST` | `/api/saved_views` | Create a new saved view | `{"name": "...", "query_params": "...", "is_pinned": false}` | `SavedView` | `201`, `400` |
| `PUT` | `/api/saved_views/{view_id}` | Update a saved view (rename, update query parameters, pin/unpin) | `{"name": "...", "query_params": "...", "is_pinned": bool}` | `SavedView` | `200`, `404` |
| `DELETE` | `/api/saved_views/{view_id}` | Delete a saved view | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| **AI Analysis Engine** | | | | | |
| `POST` | `/api/ai/preview` | Generate redacted preview and token estimate | `{"log_ids": [101, 102], "user_context": "...", "prompt_override": "...", "client_timezone": "...", "client_utc_offset_minutes": ...}` | `AIPreviewResponse` | `200`, `400` |
| `POST` | `/api/ai/diagnose` | Execute user-confirmed AI diagnosis (rate-limited) | `{"log_ids": [101, 102], "user_context": "...", "prompt_override": "...", "system_prompt_override": "...", "provider": "...", "model": "...", "fallback_models": ["..."], "client_timezone": "...", "client_utc_offset_minutes": ...}` | `AIDiagnoseResponse` | `200`, `400`, `429`, `500` |
| `POST` | `/api/ai/diagnose/stream` | Stream live diagnosis stages, failover events, and tokens via SSE | Same payload as `/api/ai/diagnose` | Server-Sent Events (`text/event-stream`) | `200`, `400`, `429` |
| `GET` | `/api/ai/models` | Discover available models from configured or requested provider (cached in SQLite for 24h) | Query params: `provider`, `refresh: bool` | `AIModelsResponse` | `200` |
| `POST` | `/api/ai/models/refresh` | Force immediate live refresh and cache update of available AI models from the provider | None | `AIModelsResponse` | `200` |
| **Host Aliases** | | | | | |
| `GET` | `/api/aliases` | List IP-to-Host mappings | None | `list[HostAlias]` | `200` |
| `POST` | `/api/aliases` | Upsert host alias mapping (retroactively updates existing logs) | `{"ip": "...", "alias": "...", "notes": "..."}` | `HostAlias` | `200`, `400` |
| `DELETE` | `/api/aliases/{ip}` | Remove host alias (reverts existing logs to raw IP) | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `404` |
| **Settings** | | | | | |
| `GET` | `/api/settings` | Read application configuration (keys masked) and container timezone metadata | None | `SettingsResponse` | `200` |
| `POST` | `/api/settings` | Update settings (encrypted at rest) | `{"ai_provider": "...", "ai_model": "...", "ai_fallback_models": "...", "ai_api_key": "...", "ai_base_url": "...", "ai_system_prompt": "...", "retention_days": 14, "internal_log_level": "WARNING", "check_for_updates": true}` | `SettingsResponse` | `200`, `400` |
| **System & Maintenance** | | | | | |
| `GET` | `/api/health` | Container healthcheck (unauthenticated returns minimal `{"status": "ok"}`; authenticated returns DB status, queue depth, dropped count, ingest rate) | None | `HealthResponse` | `200`, `503` |
| `POST` | `/api/maintenance/prune` | Trigger manual retention purge, compaction, and metrics snapshot | None | `MessageResponse` (`{"status": "ok"}`) | `200` |
| `POST` | `/api/system/vacuum` | Execute SQLite database VACUUM to reclaim disk space after large deletions (guarded by lock, requires 2x free disk space) | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `409`, `507` |
| `GET` | `/api/system/storage` | Fetch live disk usage & 30-day history | None | `StorageMetricsResponse` (`{"db_size_bytes": ..., "disk_free_bytes": ..., "disk_total_bytes": ..., "history": [...]}`) | `200` |
| `GET` | `/api/system/version` | Read installed version and check GHCR for stable release updates | Query params: `refresh: bool` | `VersionResponse` | `200` |
| **API Token Management (Web UI)** | | | | | |
| `GET` | `/api/tokens` | List all created API tokens with masked identifiers, scopes, and expiration | None | `list[ApiTokenListItem]` | `200`, `401` |
| `POST` | `/api/tokens` | Create a new API token and receive one-time raw Bearer secret | `{"name": "...", "scopes": [...], "expires_days": 90}` | `ApiTokenCreateResponse` | `201`, `400`, `401` |
| `DELETE` | `/api/tokens/{id}` | Revoke an API token immediately | None | `MessageResponse` (`{"status": "ok"}`) | `200`, `401`, `404` |
| **External Programmatic API v1 (`Authorization: Bearer ls_live_...`)** | | | | | |
| `GET` | `/api/v1/auth/verify` | Verify token validity and inspect granted scopes | None (any valid token) | `ApiTokenVerifyResponse` (`{"valid": true, "name": "...", "scopes": [...]}`) | `200`, `401` |
| `POST` | `/api/v1/maintenance/enable` | Start on-demand multi-session maintenance window | `{"duration_minutes": 30, "reason": "...", "log_handling": "silence_alerts" \| "drop_errors", "target_app": "...", "target_host": "..."}` | `ExternalMaintenanceEnableResponse` | `200`, `400`, `401`, `403` |
| `POST` | `/api/v1/maintenance/disable` | End specific maintenance session or clear all active sessions | `{"session_id": "..."}` (optional) | `ExternalMaintenanceDisableResponse` | `200`, `401`, `403` |
| `GET` | `/api/v1/maintenance/status` | Read active maintenance status, remaining seconds, and session breakdown | None (`maintenance:read`) | `ExternalMaintenanceStatusResponse` | `200`, `401`, `403` |
| `GET` | `/api/v1/system/metrics` | Read system telemetry, ingest rates, sliding alert state, and cached error breakdown | None (`system:read`) | `SystemMetricsResponse` | `200`, `401`, `403`, `429` |
| `POST` | `/api/v1/system/prune` | Trigger retention pruning and compaction (5-minute cooldown) | None (`system:write`) | `PruneResponse` | `200`, `401`, `403`, `429` |
| `POST` | `/api/v1/system/vacuum` | Trigger SQLite VACUUM compaction (5-minute cooldown) | None (`system:write`) | `VacuumResponse` | `200`, `400`, `401`, `403`, `429` |
| `GET` | `/api/v1/logs` | Query logs with FTS5, source/app filters, and datetime bounds | `query`, `source`, `app_name`, `severity_max`, `from`, `to`, `limit`, `offset` (`logs:read`) | `LogListResponse` | `200`, `400`, `401`, `403` |
| `GET` | `/api/v1/logs/{id}/context` | Fetch surrounding log lines before and after target log | `lines` (1-50), `same_app: bool` (`logs:read`) | `LogContextResponse` | `200`, `401`, `403`, `404` |
| `GET` | `/api/v1/logs/facets` | Fetch host aliases, applications, and source-app mapping | None (`logs:read`) | `LogFacetsResponse` | `200`, `401`, `403` |
| `GET` | `/api/v1/alerts/history` | Fetch incident history firings and trigger summaries | Query params: `rule_id`, `limit`, `offset` (`alerts:read`) | `list[IncidentHistoryItem]` | `200`, `401`, `403` |

