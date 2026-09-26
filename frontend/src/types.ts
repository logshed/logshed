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
  raw: string;
}

export interface LogContextResponse {
  target_id: number;
  logs: LogEntry[];
}

export interface HostAlias {
  ip: string;
  alias: string;
  notes?: string | null;
  created_at: string;
}

export interface SystemSettings {
  ai_provider: 'gemini' | 'openai' | 'openai_compatible';
  ai_model: string;
  ai_fallback_models?: string;
  ai_api_key?: string;
  ai_base_url?: string | null;
  ai_system_prompt?: string;
  retention_days: number;
  max_retention_days?: number;
  retention_overridden?: boolean;
  internal_log_level?: string;
  check_for_updates?: boolean;
  maintenance_until?: string | null;
}

export type AppTab = 'stream' | 'storage' | 'alerts' | 'settings';
export type SettingsSubTab = 'app' | 'aliases' | 'advanced';

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
  db: string;
  database?: string;
  queue_depth: number;
  dropped_logs: number;
  ingest_rate: number;
}

export interface PruneResponse {
  status: string;
  deleted_logs: number;
  deleted_metrics: number;
  metrics: StorageMetricsSnapshot;
}

export interface AiPreviewRequest {
  log_ids: number[];
  user_context?: string;
  prompt_override?: string;
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

export interface AiAuditEntry {
  id: number;
  timestamp: string;
  source_alias: string;
  app_name: string;
  log_count: number;
  user_context?: string | null;
  model: string;
  prompt_sent: string;
  response_text: string;
  tokens_in?: number;
  tokens_out?: number;
  tokens_thoughts?: number;
  tokens_used: number;
  system_prompt?: string | null;
  trigger_source?: 'on-demand' | 'alert';
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
}

export interface DropRule {
  id: number;
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex: boolean;
  is_enabled: boolean;
  severity_threshold?: number | null;
  dropped_count: number;
  created_at: string;
}

export interface DropRuleCreate {
  source_pattern?: string | null;
  app_pattern?: string | null;
  message_pattern: string;
  is_regex?: boolean;
  is_enabled?: boolean;
  severity_threshold?: number | null;
}

export interface DropRuleUpdate {
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

export type SecurityPreset = AlertPreset;

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
  day_of_week?: number;
  day_of_month?: number;
  is_active?: boolean;
  next_run?: string | null;
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
}
