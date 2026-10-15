export interface LogEntry {
  id: number;
  timestamp: string;
  received_at: string;
  source_ip: string;
  source_alias: string;
  app_name: string;
  facility: number;
  severity: number;
  message: string;
  raw?: string | null;
}

export interface LogContextResponse {
  target_id: number;
  logs: LogEntry[];
}

export interface LogDeleteParams {
  log_ids?: number[];
  sources?: string[];
  apps?: string[];
  severity_max?: number;
  from?: string;
  to?: string;
  query?: string;
  delete_all?: boolean;
}

export interface LogDeleteResponse {
  status: string;
  deleted_count: number;
  message: string;
}

export interface LogDeletePreviewResponse {
  matched_count: number;
}


export interface HostAlias {
  ip: string;
  alias: string;
  notes?: string | null;
  created_at: string;
}

export interface AiProviderConfig {
  has_api_key: boolean;
  ai_api_key: string;
  ai_model?: string | null;
  ai_fallback_models?: string | null;
  ai_base_url?: string | null;
}

export interface SystemSettings {
  ai_enabled?: boolean;
  ai_provider: 'gemini' | 'openai' | 'anthropic' | 'openai_compatible';
  ai_model: string;
  ai_fallback_models?: string;
  ai_api_key?: string;
  has_ai_api_key?: boolean;
  ai_base_url?: string | null;
  ai_system_prompt?: string;
  ai_providers_config?: Record<string, AiProviderConfig>;
  retention_days: number;
  max_retention_days?: number;
  retention_overridden?: boolean;
  internal_log_level?: string;
  check_for_updates?: boolean;
  maintenance_until?: string | null;

  // Advanced System Settings
  ai_timeout?: number;
  ai_thinking_budget?: number;
  app_url?: string;
  allow_private_notification_targets?: boolean;
  enable_docker?: boolean;
  docker_exclude_containers?: string;
  docker_source_alias?: string;
  trusted_proxies?: string;
  trust_docker_proxies?: boolean;
  cookie_secure?: boolean;
  syslog_max_tcp_connections?: number;
  syslog_tcp_inactivity_timeout?: number;
  enable_mcp?: boolean;

  // Daily Digest Settings
  daily_digest_enabled?: boolean;
  daily_digest_channel_id?: number | null;
  daily_digest_schedule_time?: string;
  daily_digest_last_run?: string | null;

  // Timezone Settings
  server_timezone?: string;
  server_tz_name?: string;
  server_tz_offset_minutes?: number;
}

export type AppTab = 'stream' | 'storage' | 'rules' | 'settings';
export type SettingsSubTab = 'app' | 'sources' | 'tokens' | 'advanced';

export interface StorageMetricsSnapshot {
  recorded_at: string;
  db_size_bytes: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  total_logs_count: number;
}

export interface StorageMetricsResponse {
  db_size_bytes: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  total_logs_count?: number;
  history: StorageMetricsSnapshot[];
}

export interface HealthResponse {
  status: string;
  db?: string;
  /** Not sent by the backend; kept optional so older test fixtures still type-check. */
  database?: string;
  queue_depth?: number;
  dropped_logs?: number;
  ingest_rate?: number;
}

export interface PruneResponse {
  status: string;
  deleted_logs: number;
  deleted_metrics: number;
  deleted_history: number;
  deleted_ai_audit: number;
  metrics: StorageMetricsSnapshot;
}

export interface PrunePreview {
  retention_days: number;
  cutoff: string;
  logs: number;
  history_alerts: number;
  history_on_demand: number;
  history_digests: number;
  history_maintenance: number;
  history_total: number;
  metrics: number;
  ai_audit: number;
  total: number;
  last_cleanup_at?: string | null;
  next_cleanup_at?: string | null;
}

export interface VacuumResponse {
  status: string;
  previous_size_bytes: number;
  new_size_bytes: number;
  reclaimed_bytes: number;
  metrics: StorageMetricsSnapshot;
}

export interface AiPreviewRequest {
  log_ids: number[];
  user_context?: string;
  prompt_override?: string;
  client_timezone?: string;
  client_utc_offset_minutes?: number;
}

export interface AiPreviewResponse {
  redacted_prompt: string;
  estimated_tokens: number;
  provider: string;
  model: string;
  fallback_models?: string[];
  log_count: number;
  source_alias: string;
  app_name: string;
  system_prompt: string;
  ai_enabled?: boolean;
  has_ai_api_key?: boolean;
}

export interface AiDiagnosisStreamEvent {
  stage: 'init' | 'calling' | 'failover' | 'complete' | 'error';
  model?: string;
  next_model?: string;
  failed_model?: string;
  error?: string;
  message?: string;
  result?: AiDiagnosisResponse;
  fallback_models?: string[];
  attempt?: number;
  total_models?: number;
  is_fallback?: boolean;
}

export interface AiDiagnosisRequest {
  log_ids: number[];
  user_context?: string;
  prompt_override?: string;
  system_prompt_override?: string;
  provider?: string;
  model?: string;
  fallback_models?: string[];
  client_timezone?: string;
  client_utc_offset_minutes?: number;
  onEvent?: (event: AiDiagnosisStreamEvent) => void;
}

export interface AiDiagnosisResponse {
  summary: string;
  root_cause: string;
  remediation: string;
  model_used: string;
  fallback_used?: boolean;
  fallback_attempts?: string[];
  tokens_in?: number;
  tokens_out?: number;
  tokens_thoughts?: number;
  tokens_used: number;
  audit_id?: number;
}

export interface AiConversationMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  tokens_used: number;
  model: string;
  created_at: string;
}

export interface AiConversationHistoryResponse {
  audit_id: number;
  messages: AiConversationMessage[];
  turn_count: number;
  max_turns: number;
}

export interface AiFollowUpRequest {
  message: string;
  provider?: string;
  model?: string;
}

export interface AiFollowUpResponse {
  message: AiConversationMessage;
  tokens_in: number;
  tokens_out: number;
  tokens_thoughts: number;
  tokens_used: number;
  model_used: string;
  fallback_used: boolean;
  fallback_attempts: string[];
  turn_count: number;
  max_turns: number;
}

export interface AiFollowUpStreamEvent {
  stage: 'init' | 'calling' | 'failover' | 'delta' | 'complete' | 'error';
  model?: string;
  next_model?: string;
  failed_model?: string;
  error?: string;
  message?: string;
  content?: string;
  result?: AiFollowUpResponse;
  is_fallback?: boolean;
  attempt?: number;
  total_models?: number;
  fallback_models?: string[];
}

export interface AuthStatusResponse {
  setup_required: boolean;
  authenticated: boolean;
}

export interface LogFilterParams {
  query?: string;
  severity_max?: number;
  source?: string | string[];
  sources?: string[];
  app_name?: string | string[];
  apps?: string[];
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface LogFacetsResponse {
  sources: string[];
  apps: string[];
  host_to_apps: Record<string, string[]>;
  app_to_hosts: Record<string, string[]>;
}

export interface AiModelInfo {
  id: string;
  name: string;
  description?: string | null;
  supports_thinking?: boolean;
  is_deprecated?: boolean;
}

export interface AiModelsResponse {
  provider: string;
  models: AiModelInfo[];
  has_api_key: boolean;
  cached_at?: string | null;
  is_live: boolean;
  error?: string | null;
}

export interface VersionInfo {
  current_version: string;
  latest_version?: string | null;
  update_available: boolean;
  check_enabled?: boolean;
  checked_at?: number | null;
  repo_deprecated?: boolean;
}

export interface RuleReorderRequest {
  rule_ids: number[];
}

export interface DropRule {
  id: number;
  name?: string | null;
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex: boolean;
  is_enabled: boolean;
  severity_threshold?: number | null;
  dropped_count: number;
  display_order?: number;
  created_at: string;
}

export interface DropRuleCreate {
  name?: string | null;
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex?: boolean;
  is_enabled?: boolean;
  severity_threshold?: number | null;
}

export interface DropRuleUpdate {
  name?: string | null;
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern?: string;
  is_regex?: boolean;
  is_enabled?: boolean;
  severity_threshold?: number | null;
  reset_counter?: boolean;
}

export interface DropRuleTestRequest {
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex?: boolean;
  severity_threshold?: number | null;
  sample_message: string;
  sample_source?: string | null;
  sample_app?: string | null;
  sample_severity?: number | null;
}

export interface DropRuleTestResponse {
  matched: boolean;
  error?: string | null;
}

export interface SavedView {
  id: number;
  name: string;
  query_params: {
    query?: string;
    severity_max?: number;
    source?: string | string[];
    sources?: string[];
    app_name?: string | string[];
    apps?: string[];
    from?: string;
    to?: string;
    [key: string]: any;
  };
  is_pinned: boolean;
  created_at: string;
}

export interface SavedViewCreate {
  name: string;
  query_params: Record<string, any>;
  is_pinned?: boolean;
}

export interface SavedViewUpdate {
  name?: string;
  query_params?: Record<string, any>;
  is_pinned?: boolean;
}

export interface NotificationChannel {
  id: number;
  name: string;
  url: string;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface NotificationChannelCreate {
  name: string;
  url: string;
  is_enabled?: boolean;
}

export interface NotificationChannelUpdate {
  name?: string;
  url?: string;
  is_enabled?: boolean;
}

export interface NotificationTestRequest {
  channel_id?: number;
  url?: string;
}

export interface NotificationTestResponse {
  success: boolean;
  message: string;
}

export interface DailyDigestRunResponse {
  status: string;
  history_id?: number | null;
  total_logs: number;
  error_count: number;
  top_errors: Array<{ entity: string; error_count: number }>;
  top_services: Array<{ service: string; log_count: number }>;
  storage_delta: string;
  ai_summary?: string | null;
  channel_id?: number | null;
  notification_sent: boolean;
  triggered_at?: string | null;
}

// Alert rule types accepted by the backend (create and update return 422 for anything else)
export type AlertRuleType = 'threshold' | 'pattern' | 'rate';

export interface AlertRule {
  id: number;
  name: string;
  rule_type: string;
  channel_id?: number | null;
  filter_app?: string | null;
  filter_severity?: number | null;
  match_pattern?: string | null;
  threshold_count: number;
  window_seconds: number;
  cooldown_seconds: number;
  ai_enrichment: boolean;
  is_enabled: boolean;
  trigger_count: number;
  display_order?: number;
  last_triggered_at?: string | null;
  suppress_until?: string | null;
  created_at: string;
}

export interface AlertRuleCreate {
  name: string;
  rule_type: string;
  channel_id?: number | null;
  filter_app?: string | null;
  filter_severity?: number | null;
  match_pattern?: string | null;
  threshold_count?: number;
  window_seconds?: number;
  cooldown_seconds?: number;
  ai_enrichment?: boolean;
  is_enabled?: boolean;
}

export interface AlertRuleUpdate {
  name?: string;
  rule_type?: string;
  channel_id?: number | null;
  filter_app?: string | null;
  filter_severity?: number | null;
  match_pattern?: string | null;
  threshold_count?: number;
  window_seconds?: number;
  cooldown_seconds?: number;
  ai_enrichment?: boolean;
  is_enabled?: boolean;
  reset_cooldown?: boolean;
}

export interface AlertTestRequest {
  rule_type: string;
  filter_app?: string | null;
  filter_severity?: number | null;
  match_pattern?: string | null;
  sample_message: string;
  sample_app?: string | null;
  sample_severity?: number | null;
}

export interface AlertTestResponse {
  matched: boolean;
  extracted_ip?: string | null;
  error?: string | null;
}

export interface AlertPreset {
  id: string;
  name: string;
  description: string;
  rule_type: string;
  filter_app?: string | null;
  filter_severity?: number | null;
  match_pattern?: string | null;
  threshold_count: number;
  window_seconds: number;
  cooldown_seconds: number;
  ai_enrichment: boolean;
  is_custom?: boolean;
}

export interface DropPreset {
  id: string;
  name: string;
  description: string;
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex: boolean;
  severity_threshold?: number | null;
  is_custom?: boolean;
}


export interface AlertHistoryItem {
  id: number;
  rule_id?: number | null;
  rule_name: string;
  channel_id?: number | null;
  trigger_count: number;
  sample_log?: string | null;
  incident_summary?: string | null;
  ai_enrichment: boolean;
  ai_model?: string | null;
  ai_audit_id?: number | null;
  triggered_at: string;
  tokens_in?: number | null;
  tokens_out?: number | null;
  tokens_thoughts?: number | null;
  tokens_used?: number | null;
  prompt_sent?: string | null;
  system_prompt?: string | null;
  response_text?: string | null;
  source_alias?: string | null;
  app_name?: string | null;
  user_context?: string | null;
  maintenance_session_id?: string | null;
  maintenance_type?: string | null;
  maintenance_started_at?: string | null;
  maintenance_ended_at?: string | null;
  maintenance_end_reason?: string | null;
  maintenance_trigger?: string | null;
  maintenance_target_app?: string | null;
  maintenance_target_host?: string | null;
  maintenance_dropped_count?: number | null;
}

export interface AlertHistoryResponse {
  items: AlertHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface MaintenanceSchedule {
  id: string;
  name: string;
  enabled: boolean;
  recurrence: 'daily' | 'weekly' | 'monthly';
  start_time: string;
  duration_minutes: number;
  day_of_week?: number | null;
  day_of_month?: number | null;
  is_active?: boolean;
  next_run?: string | null;
}

export interface MaintenanceSessionDetail {
  session_id: string;
  reason?: string | null;
  log_handling: string;
  target_app?: string | null;
  target_host?: string | null;
  initiated_by: string;
  expires_at: string;
}

export interface MaintenanceWindowResponse {
  active: boolean;
  until: string | null;
  reason?: string | null;
  schedule_name?: string | null;
  on_demand_until?: string | null;
  schedules?: MaintenanceSchedule[];
  server_time?: string | null;
  server_timezone?: string | null;
  remaining_seconds?: number;
  active_sessions_count?: number;
  sessions?: MaintenanceSessionDetail[];
  log_handling?: string;
}

export interface ApiToken {
  id: number;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  created_by?: string | null;
}

export interface ApiTokenCreateRequest {
  name: string;
  scopes: string[];
  expires_days?: number | null;
}

export interface ApiTokenCreateResponse {
  token: ApiToken;
  raw_token: string;
}

export interface DockerEndpoint {
  id: number;
  name: string;
  url: string;
  source_alias: string;
  enabled: boolean;
  tls_enabled: boolean;
  ca_cert?: string | null;
  client_cert?: string | null;
  has_client_key?: boolean;
  exclude_containers: string;
  created_at: string;
  updated_at: string;
  is_connected?: boolean;
  active_containers?: number;
  last_error?: string | null;
  last_activity?: string | null;
  latency_ms?: number | null;
}

export interface DockerEndpointCreate {
  name: string;
  url: string;
  source_alias?: string;
  enabled?: boolean;
  tls_enabled?: boolean;
  ca_cert?: string | null;
  client_cert?: string | null;
  client_key?: string | null;
  exclude_containers?: string;
}

export interface DockerEndpointUpdate {
  name?: string;
  url?: string;
  source_alias?: string;
  enabled?: boolean;
  tls_enabled?: boolean;
  ca_cert?: string | null;
  client_cert?: string | null;
  client_key?: string | null;
  exclude_containers?: string;
}

export interface DockerEndpointTestRequest {
  endpoint_id?: number;
  url: string;
  tls_enabled?: boolean;
  ca_cert?: string | null;
  client_cert?: string | null;
  client_key?: string | null;
}

export interface DockerEndpointTestResult {
  success: boolean;
  version?: string | null;
  latency_ms?: number | null;
  error?: string | null;
}
