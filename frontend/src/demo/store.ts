/// <reference types="vite/client" />
// In-memory collections behind the demo's Rules, Settings and Storage pages, seeded with a believable set-up.
// Edits last until the page is reloaded.
import type {
  AlertHistoryItem, AlertPreset, AlertRule, ApiToken, DockerEndpoint, DropPreset, DropRule, HostAlias,
  LogEntry, MaintenanceSchedule, MaintenanceSessionDetail, NotificationChannel, SavedView, StorageMetricsSnapshot,
} from '../types.ts';
import { DEMO_HOSTS } from './generator.ts';

// The real presets, read from the backend's JSON files at build time
const alertPresetFiles = import.meta.glob('../../../backend/app/presets/alerts/*.json', { eager: true, import: 'default' });
const dropPresetFiles = import.meta.glob('../../../backend/app/presets/drops/*.json', { eager: true, import: 'default' });
export const ALERT_PRESETS: AlertPreset[] = Object.values(alertPresetFiles).map((p) => ({ ...(p as AlertPreset), is_custom: false }));
export const DROP_PRESETS: DropPreset[] = Object.values(dropPresetFiles).map((p) => ({ ...(p as DropPreset), is_custom: false }));

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const DAY = 24 * 60;

/** A list of records with numeric ids. */
export class Collection<T extends { id: number }> {
  private nextId: number;
  constructor(public items: T[]) {
    this.nextId = Math.max(0, ...items.map((i) => i.id)) + 1;
  }
  get(id: number): T | undefined {
    return this.items.find((i) => i.id === id);
  }
  add(item: Omit<T, 'id'>): T {
    const created = { ...item, id: this.nextId++ } as T;
    this.items.push(created);
    return created;
  }
  update(id: number, patch: Partial<T>): T | undefined {
    const item = this.get(id);
    if (item) Object.assign(item, patch, { id });
    return item;
  }
  remove(id: number): boolean {
    const before = this.items.length;
    this.items = this.items.filter((i) => i.id !== id);
    return this.items.length < before;
  }
  reorder(ids: number[]): T[] {
    const byId = new Map(this.items.map((i) => [i.id, i]));
    const ordered = ids.map((id) => byId.get(id)).filter((i): i is T => !!i);
    const rest = this.items.filter((i) => !ids.includes(i.id));
    this.items = [...ordered, ...rest];
    this.items.forEach((i, idx) => Object.assign(i, { display_order: idx }));
    return this.items;
  }
}

export const channels = new Collection<NotificationChannel>([
  { id: 1, name: 'Phone (ntfy)', url: 'ntfys://ntfy.home.lan/homelab-alerts', is_enabled: true, created_at: ago(40 * DAY), updated_at: ago(40 * DAY) },
  { id: 2, name: 'Discord #homelab', url: 'discord://••••••••/••••••••', is_enabled: true, created_at: ago(30 * DAY), updated_at: ago(12 * DAY) },
]);

function ruleFromPreset(id: number, presetId: string, extra: Partial<AlertRule>): AlertRule {
  const p = ALERT_PRESETS.find((x) => x.id === presetId);
  if (!p) throw new Error(`demo: no alert preset ${presetId}`);
  return {
    id, name: p.name, rule_type: p.rule_type as AlertRule['rule_type'], channel_id: 1,
    filter_app: p.filter_app, filter_severity: p.filter_severity, match_pattern: p.match_pattern,
    threshold_count: p.threshold_count, window_seconds: p.window_seconds, cooldown_seconds: p.cooldown_seconds,
    ai_enrichment: p.ai_enrichment, is_enabled: true, trigger_count: 0, display_order: id - 1,
    last_triggered_at: null, suppress_until: null, created_at: ago(35 * DAY), ...extra,
  };
}

export const alertRules = new Collection<AlertRule>([
  ruleFromPreset(1, 'backup_failure', {}),
  ruleFromPreset(2, 'disk_smart_failure', { channel_id: 1 }),
  ruleFromPreset(3, 'critical_severity', { channel_id: 2 }),
  {
    id: 4, name: 'UPS on battery', rule_type: 'pattern', channel_id: 1, filter_app: 'upsmon', filter_severity: null,
    match_pattern: 'on battery', threshold_count: 1, window_seconds: 60, cooldown_seconds: 900, ai_enrichment: false,
    is_enabled: true, trigger_count: 0, display_order: 3, last_triggered_at: null, suppress_until: null, created_at: ago(20 * DAY),
  },
  {
    id: 5, name: 'SSH login storm on the router', rule_type: 'threshold', channel_id: 2, filter_app: 'sshd', filter_severity: null,
    match_pattern: 'Invalid user', threshold_count: 20, window_seconds: 300, cooldown_seconds: 3600, ai_enrichment: true,
    is_enabled: true, trigger_count: 0, display_order: 4, last_triggered_at: null, suppress_until: null, created_at: ago(9 * DAY),
  },
  {
    id: 6, name: 'Jellyfin transcoding errors', rule_type: 'pattern', channel_id: 2, filter_app: 'jellyfin', filter_severity: 3,
    match_pattern: 'FFmpeg exited', threshold_count: 1, window_seconds: 60, cooldown_seconds: 1800, ai_enrichment: false,
    is_enabled: false, trigger_count: 0, display_order: 5, last_triggered_at: null, suppress_until: null, created_at: ago(5 * DAY),
  },
]);

export const dropRules = new Collection<DropRule>([
  { id: 1, name: 'Firewall blocks from the internet', source_pattern: 'opnsense', app_pattern: 'filterlog', message_pattern: 'match,block,in', is_regex: false, is_enabled: true, severity_threshold: 6, dropped_count: 48213, display_order: 0, created_at: ago(38 * DAY) },
  { id: 2, name: 'Home Assistant state changes', source_pattern: null, app_pattern: 'homeassistant', message_pattern: 'Bus:Handling <Event state_changed', is_regex: false, is_enabled: true, severity_threshold: 7, dropped_count: 12907, display_order: 1, created_at: ago(21 * DAY) },
  { id: 3, name: 'Proxmox proxy worker restarts', source_pattern: 'pve-node1,pve-node2', app_pattern: 'pveproxy', message_pattern: '^worker \\d+ (?:finished|started)$', is_regex: true, is_enabled: false, severity_threshold: 6, dropped_count: 0, display_order: 2, created_at: ago(3 * DAY) },
]);

export const savedViews = new Collection<SavedView>([
  { id: 1, name: 'Errors, last 24 hours', query_params: { severity_max: 3, from: '-24h' }, is_pinned: true, created_at: ago(30 * DAY) },
  { id: 2, name: 'Storage box', query_params: { sources: ['truenas'], severity_max: 4 }, is_pinned: true, created_at: ago(25 * DAY) },
  { id: 3, name: 'Backups', query_params: { query: 'backup OR vzdump' }, is_pinned: false, created_at: ago(10 * DAY) },
]);

export const tokens = new Collection<ApiToken>([
  { id: 1, name: 'Home Assistant', token_prefix: 'lsk_3f9a', scopes: ['system:read', 'alerts:read', 'maintenance:write'], created_at: ago(28 * DAY), expires_at: null, last_used_at: ago(1), created_by: 'admin' },
  { id: 2, name: 'Claude (MCP)', token_prefix: 'lsk_b72c', scopes: ['logs:read', 'system:read', 'alerts:read'], created_at: ago(14 * DAY), expires_at: ago(-76 * DAY), last_used_at: ago(3 * 60), created_by: 'admin' },
]);

export const dockerHosts = new Collection<DockerEndpoint>([
  { id: 1, name: 'docker-vm', url: 'tcp://docker-proxy:2375', source_alias: 'docker-vm', enabled: true, tls_enabled: false, verify_hostname: true, exclude_containers: 'logshed', created_at: ago(40 * DAY), updated_at: ago(40 * DAY), is_connected: true, active_containers: 9, last_error: null, last_activity: ago(0), latency_ms: 3 },
]);

const hostNotes: Record<string, string> = {
  'pve-node1': 'Proxmox, rack shelf 1', 'pve-node2': 'Proxmox, runs the backups', truenas: 'NAS, pool tank (4 x 8 TB RAIDZ1)',
  opnsense: 'Router and firewall', 'core-switch': '24-port managed switch', homeassistant: 'HA OS on a NUC', ups: 'APC 1500VA via NUT',
};
export let aliases: HostAlias[] = DEMO_HOSTS
  .filter((h) => h.alias !== 'docker-vm')
  .map((h) => ({ ip: h.ip, alias: h.alias, notes: hostNotes[h.alias] ?? null, created_at: ago(40 * DAY) }));
export function setAliases(next: HostAlias[]) { aliases = next; }

export const schedules: MaintenanceSchedule[] = [
  { id: 'sched-1', name: 'Sunday updates', enabled: true, recurrence: 'weekly', day_of_week: 6, start_time: '03:00', duration_minutes: 60, is_active: false },
];
export const sessions: MaintenanceSessionDetail[] = [];

export const history = new Collection<AlertHistoryItem>([]);

/** Alert history for the incidents already in the log backfill, and counts on their rules. */
export function seedHistory(logs: LogEntry[], recordAnalysis: (summary: string) => number) {
  const fired = (rule: AlertRule, log: LogEntry, extra: Partial<AlertHistoryItem> = {}) => {
    rule.trigger_count += 1;
    rule.last_triggered_at = log.timestamp;
    history.add({
      rule_id: rule.id, rule_name: rule.name, channel_id: rule.channel_id, trigger_count: 1,
      sample_log: log.message, sample_log_id: log.id, ai_enrichment: false, triggered_at: log.timestamp,
      source_alias: log.source_alias, app_name: log.app_name, ...extra,
    });
  };
  for (const log of logs) {
    for (const rule of alertRules.items) {
      if (!rule.is_enabled || !matchesRule(rule, log)) continue;
      if (rule.last_triggered_at && Date.parse(log.timestamp) - Date.parse(rule.last_triggered_at) < rule.cooldown_seconds * 1000) continue;
      if (rule.ai_enrichment && /Backup of VM \d+ failed/.test(log.message)) {
        const summary = 'The nightly backup of VM 102 on pve-node2 failed with "Stale file handle" on the truenas-nfs storage. truenas logged NFS retries for pve-node2 at the same moment, so the NFS share was most likely remounted during the backup. Remount the share on pve-node2 and rerun the backup.';
        fired(rule, log, { ai_enrichment: true, ai_model: 'gemini-flash-latest', incident_summary: summary, ai_audit_id: recordAnalysis(summary), tokens_in: 1620, tokens_out: 188, tokens_used: 1808 });
      } else {
        fired(rule, log);
      }
    }
  }
  // Newest first, as the server returns them
  history.items.reverse();
}

export function matchesRule(rule: AlertRule, log: LogEntry): boolean {
  if (rule.filter_app && rule.filter_app !== log.app_name) return false;
  if (rule.filter_severity !== null && rule.filter_severity !== undefined && log.severity > rule.filter_severity) return false;
  if (!rule.match_pattern) return rule.filter_severity !== null && rule.filter_severity !== undefined && rule.threshold_count <= 1;
  if (rule.rule_type === 'threshold' && rule.threshold_count > 1) return false;
  try {
    return new RegExp(rule.match_pattern, 'i').test(log.message);
  } catch {
    return log.message.toLowerCase().includes(rule.match_pattern.toLowerCase());
  }
}

/** 30 days of hourly storage samples: the database grows slowly and settles at the retention limit. */
export function storageHistory(currentLogs: number): StorageMetricsSnapshot[] {
  const out: StorageMetricsSnapshot[] = [];
  const hours = 30 * 24;
  for (let h = hours; h >= 0; h--) {
    const growth = Math.min(1, (hours - h) / (22 * 24));
    const count = Math.round(currentLogs * (0.35 + 0.65 * growth) * (0.995 + Math.random() * 0.01));
    out.push({
      recorded_at: ago(h * 60), db_size_bytes: count * 640, disk_free_bytes: 412e9 - ((hours - h) / 24) * 0.6e9,
      disk_total_bytes: 1e12, total_logs_count: count,
    });
  }
  return out;
}
