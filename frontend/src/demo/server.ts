// The fake backend: answers the app's /api requests from in-memory data, in the browser.
import type {
  LogEntry, LogFacetsResponse, AiDiagnosisRequest, AiDiagnosisResponse, AiConversationMessage,
  AiDiagnosisStreamEvent, AiFollowUpStreamEvent, AlertRule, MaintenanceSchedule,
} from '../types.ts';
import { matchesSearchQuery } from '../components/logs/LiveLogStream.tsx';
import { makeHistory, makeLog } from './generator.ts';
import * as store from './store.ts';
import { FOLLOW_UPS, pickDiagnosis } from './aiAnswers.ts';

export const DEMO_VERSION = '1.3.1';
const MAX_LOGS = 20000;

const logs: LogEntry[] = makeHistory();
const listeners = new Set<(log: LogEntry) => void>();
const conversations = new Map<number, AiConversationMessage[]>();
let nextAuditId = 1;
let nextMessageId = 1;
let settings: Record<string, unknown> = {
  ai_enabled: true,
  ai_provider: 'gemini',
  ai_model: 'gemini-flash-latest',
  ai_fallback_models: '',
  has_ai_api_key: true,
  ai_api_key: '********',
  ai_base_url: null,
  ai_system_prompt: '',
  ai_providers_config: {},
  retention_days: 30,
  max_retention_days: 365,
  internal_log_level: 'INFO',
  check_for_updates: false,
  maintenance_until: null,
  ai_timeout: 60,
  app_url: 'https://demo.logshed.dev',
  enable_docker: true,
  docker_source_alias: 'docker-vm',
  server_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  server_tz_name: Intl.DateTimeFormat().resolvedOptions().timeZone,
  server_tz_offset_minutes: -new Date().getTimezoneOffset(),
};

/** Add one new log line every 0.3 to 1.2 seconds and hand it to the open live streams. */
function trickle() {
  const log = makeLog(new Date());
  logs.push(log);
  if (logs.length > MAX_LOGS) logs.splice(0, logs.length - MAX_LOGS);
  listeners.forEach((fn) => fn(log));
  checkAlerts(log);
  setTimeout(trickle, 300 + Math.random() * 900);
}
setTimeout(trickle, 1000);

export function subscribe(fn: (log: LogEntry) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const list = (v: string | null) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : []);

export function logFilter(params: URLSearchParams): (log: LogEntry) => boolean {
  const sevMax = params.get('severity_max');
  const sources = list(params.get('source'));
  const apps = list(params.get('app_name'));
  const from = params.get('from') ? Date.parse(params.get('from')!) : null;
  const to = params.get('to') ? Date.parse(params.get('to')!) : null;
  const query = params.get('query') || undefined;
  return (log) => {
    if (sevMax !== null && log.severity > Number(sevMax)) return false;
    if (sources.length && !sources.includes(log.source_alias) && !sources.includes(log.source_ip)) return false;
    if (apps.length && !apps.includes(log.app_name)) return false;
    const t = Date.parse(log.timestamp);
    if (from !== null && t < from) return false;
    if (to !== null && t > to) return false;
    return !query || matchesSearchQuery(log, query);
  };
}

export class DemoHttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const notInDemo = (what: string) => new DemoHttpError(403, `${what} is not available in the demo. Install LogShed to try it on your own logs.`);

type Body = Record<string, any> | null;
type Handler = (m: RegExpMatchArray, q: URLSearchParams, body: Body) => unknown;

function facets(): LogFacetsResponse {
  const hostToApps: Record<string, Set<string>> = {};
  const appToHosts: Record<string, Set<string>> = {};
  for (const l of logs) {
    (hostToApps[l.source_alias] ??= new Set()).add(l.app_name);
    (appToHosts[l.app_name] ??= new Set()).add(l.source_alias);
  }
  const asLists = (r: Record<string, Set<string>>) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, [...v].sort()]));
  return {
    sources: Object.keys(hostToApps).sort(),
    apps: Object.keys(appToHosts).sort(),
    host_to_apps: asLists(hostToApps),
    app_to_hosts: asLists(appToHosts),
    truncated: false,
  };
}

function logContext(id: number, q: URLSearchParams) {
  const lines = Number(q.get('lines') || 10);
  const idx = logs.findIndex((l) => l.id === id);
  if (idx < 0) throw new DemoHttpError(404, 'Log not found');
  const target = logs[idx];
  const sameApp = q.get('same_app') === 'true';
  const pool = logs.filter((l) => l.source_alias === target.source_alias && (!sameApp || l.app_name === target.app_name));
  const at = pool.indexOf(target);
  return { target_id: id, logs: pool.slice(Math.max(0, at - lines), at + lines + 1) };
}

function selectedLogs(ids: number[]): LogEntry[] {
  const set = new Set(ids);
  return logs.filter((l) => set.has(l.id));
}

function startConversation(result: AiDiagnosisResponse, req: AiDiagnosisRequest): AiDiagnosisResponse {
  const auditId = nextAuditId++;
  const now = new Date().toISOString();
  conversations.set(auditId, [{
    id: nextMessageId++, role: 'assistant', model: result.model_used, tokens_used: result.tokens_used, created_at: now,
    content: `**Summary**\n${result.summary}\n\n**Root cause**\n${result.root_cause}\n\n**Remediation**\n${result.remediation}`,
  }]);
  void req;
  return { ...result, audit_id: auditId };
}

function diagnose(body: Body): AiDiagnosisResponse {
  const req = (body || {}) as AiDiagnosisRequest;
  const chosen = selectedLogs(req.log_ids || []);
  return startConversation({ ...pickDiagnosis(chosen), model_used: String(settings.ai_model) }, req);
}

const nowIso = () => new Date().toISOString();
function found<T>(item: T | undefined): T {
  if (item === undefined) throw new DemoHttpError(404, 'Not found');
  return item;
}

/** List, create, update and delete routes for one collection. */
function crud<T extends { id: number }>(base: string, coll: store.Collection<T>, make: (body: Record<string, any>) => Omit<T, 'id'>): Array<[string, RegExp, Handler]> {
  const esc = base.replace(/\//g, '\\/');
  const one = new RegExp(`^${esc}\\/(\\d+)$`);
  return [
    ['GET', new RegExp(`^${esc}$`), () => coll.items],
    ['POST', new RegExp(`^${esc}$`), (_m, _q, b) => coll.add(make(b || {}))],
    ['GET', one, (m) => found(coll.get(Number(m[1])))],
    ['PUT', one, (m, _q, b) => found(coll.update(Number(m[1]), { ...(b || {}), updated_at: nowIso() } as unknown as Partial<T>))],
    ['DELETE', one, (m) => { coll.remove(Number(m[1])); return { status: 'ok' }; }],
  ];
}

function importRules(body: Body, add: (rule: any) => unknown) {
  const rules = (Array.isArray(body) ? body : (body?.rules as unknown[])) || [];
  rules.forEach((r) => { const { id: _id, ...rest } = r as Record<string, unknown>; add(rest); });
  return { imported: rules.length, skipped: 0, errors: [] };
}

function maintenanceState() {
  const now = Date.now();
  const live = store.sessions.filter((x) => Date.parse(x.expires_at) > now);
  store.sessions.splice(0, store.sessions.length, ...live);
  const until = live.length ? live.map((x) => x.expires_at).sort().slice(-1)[0] : null;
  return {
    active: live.length > 0, until, on_demand_until: until, reason: live[0]?.reason ?? null, schedule_name: null,
    schedules: store.schedules, server_time: new Date().toISOString(),
    server_timezone: String(settings.server_timezone), remaining_seconds: until ? Math.round((Date.parse(until) - now) / 1000) : 0,
    active_sessions_count: live.length, sessions: live, log_handling: 'silence_alerts',
  };
}

/** Fire the alert rules a new line matches, as the real alert engine would, with each rule's cooldown. */
function checkAlerts(log: LogEntry) {
  if (maintenanceState().active) return;
  for (const rule of store.alertRules.items) {
    if (!rule.is_enabled || !store.matchesRule(rule, log)) continue;
    if (rule.last_triggered_at && Date.parse(log.timestamp) - Date.parse(rule.last_triggered_at) < rule.cooldown_seconds * 1000) continue;
    rule.trigger_count += 1;
    rule.last_triggered_at = log.timestamp;
    const item = store.history.add({
      rule_id: rule.id, rule_name: rule.name, channel_id: rule.channel_id, trigger_count: 1, sample_log: log.message,
      sample_log_id: log.id, ai_enrichment: false, triggered_at: log.timestamp, source_alias: log.source_alias, app_name: log.app_name,
    });
    // Newest first
    store.history.items.pop();
    store.history.items.unshift(item);
  }
}

store.seedHistory(logs, (summary) => {
  const auditId = nextAuditId++;
  conversations.set(auditId, [{ id: nextMessageId++, role: 'assistant', content: summary, tokens_used: 1808, model: 'gemini-flash-latest', created_at: nowIso() }]);
  return auditId;
});
const storage = store.storageHistory(logs.length * 40);

const ROUTES: Array<[string, RegExp, Handler]> = [
  ['GET', /^\/api\/auth\/status$/, () => ({ setup_required: false, authenticated: true })],
  ['POST', /^\/api\/auth\/(login|logout|setup)$/, () => ({ status: 'ok' })],
  ['POST', /^\/api\/auth\/password$/, () => { throw notInDemo('Changing the password'); }],
  ['GET', /^\/api\/health$/, () => ({ status: 'ok', db: 'ok', queue_depth: 0, dropped_logs: 0, udp_receive_errors: 0, ingest_rate: 1.4, loop_lag_ms: 0.4, loop_lag_p95_ms: 1.1, loop_lag_max_ms: 2.3 })],
  ['GET', /^\/api\/system\/version$/, () => ({ current_version: DEMO_VERSION, latest_version: DEMO_VERSION, update_available: false, check_enabled: false })],
  ['GET', /^\/api\/settings$/, () => settings],
  ['POST', /^\/api\/settings$/, (_m, _q, body) => { settings = { ...settings, ...(body || {}), ai_api_key: '********' }; return { status: 'ok' }; }],
  ['GET', /^\/api\/logs$/, (_m, q) => {
    const match = logFilter(q);
    const limit = Number(q.get('limit') || 100);
    const offset = Number(q.get('offset') || 0);
    const found: LogEntry[] = [];
    for (let i = logs.length - 1; i >= 0; i--) if (match(logs[i])) found.push(logs[i]);
    return { logs: found.slice(offset, offset + limit), total: found.length, limit, offset, total_capped: false };
  }],
  ['GET', /^\/api\/logs\/facets$/, () => facets()],
  ['GET', /^\/api\/logs\/(\d+)\/context$/, (m, q) => logContext(Number(m[1]), q)],
  ['DELETE', /^\/api\/logs\/\d+$/, () => { throw notInDemo('Deleting logs'); }],
  ['POST', /^\/api\/logs\/delete(\/preview)?$/, () => { throw notInDemo('Deleting logs'); }],
  ['GET', /^\/api\/ai\/models$/, () => ({
    provider: settings.ai_provider, has_api_key: true, is_live: false, cached_at: null,
    models: [
      { id: 'gemini-flash-latest', name: 'Gemini Flash (latest)' },
      { id: 'gemini-flash-lite-latest', name: 'Gemini Flash-Lite (latest)' },
      { id: 'gemini-pro-latest', name: 'Gemini Pro (latest)' },
    ],
  })],
  ['POST', /^\/api\/ai\/models\/refresh$/, () => { throw notInDemo('Refreshing the model list'); }],
  ['POST', /^\/api\/ai\/preview$/, (_m, _q, body) => {
    const chosen = selectedLogs((body?.log_ids as number[]) || []);
    const lines = chosen.map((l) => `${l.timestamp} ${l.source_alias} ${l.app_name}[${l.severity}]: ${l.message}`).join('\n');
    return {
      redacted_prompt: `Diagnose these log lines from a homelab.\n\n${lines}`,
      estimated_tokens: 180 + lines.length / 4,
      provider: settings.ai_provider, model: settings.ai_model, fallback_models: [],
      log_count: chosen.length, source_alias: chosen[0]?.source_alias || '', app_name: chosen[0]?.app_name || '',
      system_prompt: 'You are a homelab systems expert. Explain what went wrong, the likely root cause and how to fix it.',
      ai_enabled: true, has_ai_api_key: true,
    };
  }],
  ['POST', /^\/api\/ai\/diagnose$/, (_m, _q, body) => diagnose(body)],
  ['GET', /^\/api\/ai\/audit\/(\d+)\/messages$/, (m) => {
    const messages = conversations.get(Number(m[1])) || [];
    return { audit_id: Number(m[1]), messages, turn_count: Math.floor(messages.length / 2), max_turns: 10 };
  }],
  ['POST', /^\/api\/ai\/audit\/(\d+)\/follow-up$/, () => { throw notInDemo('A follow-up without streaming'); }],
  ['DELETE', /^\/api\/ai\/audit\/(\d+)\/messages$/, (m) => { conversations.set(Number(m[1]), (conversations.get(Number(m[1])) || []).slice(0, 1)); return { status: 'ok' }; }],
  ...crud('/api/alerts/rules', store.alertRules, (b) => ({
    name: 'New rule', rule_type: 'pattern' as AlertRule['rule_type'], channel_id: null, filter_app: null, filter_severity: null, match_pattern: null, threshold_count: 1,
    window_seconds: 60, cooldown_seconds: 300, ai_enrichment: false, is_enabled: true, ...b,
    trigger_count: 0, last_triggered_at: null, suppress_until: null, created_at: nowIso(), display_order: store.alertRules.items.length,
  })),
  ['PUT', /^\/api\/alerts\/rules\/reorder$/, (_m, _q, b) => store.alertRules.reorder((b?.rule_ids as number[]) || [])],
  ['GET', /^\/api\/alerts\/rules\/export$/, () => ({ rules: store.alertRules.items })],
  ['GET', /^\/api\/alerts\/rules\/(\d+)\/export$/, (m) => ({ rules: [found(store.alertRules.get(Number(m[1])))] })],
  ['POST', /^\/api\/alerts\/rules\/import$/, (_m, _q, b) => importRules(b, (r) => store.alertRules.add({ ...r, trigger_count: 0, last_triggered_at: null, created_at: nowIso() }))],
  ['GET', /^\/api\/alerts\/presets$/, () => store.ALERT_PRESETS],
  ['POST', /^\/api\/alerts\/presets\/([^/]+)\/install$/, (m, _q, b) => {
    const { id: _id, category: _c, description: _d, is_custom: _x, ...p } = found(store.ALERT_PRESETS.find((x) => x.id === decodeURIComponent(m[1])));
    return store.alertRules.add({
      ...p, rule_type: p.rule_type as AlertRule['rule_type'], channel_id: (b?.channel_id as number | null) ?? null, is_enabled: true,
      trigger_count: 0, last_triggered_at: null, suppress_until: null, created_at: nowIso(), display_order: store.alertRules.items.length,
    });
  }],
  ['POST', /^\/api\/alerts\/test$/, (_m, _q, b) => {
    try {
      return { matched: new RegExp(String(b?.match_pattern || ''), 'i').test(String(b?.sample_log || b?.test_message || '')), extracted_ip: null, error: null };
    } catch (e) {
      return { matched: false, error: String((e as Error).message) };
    }
  }],
  ['GET', /^\/api\/alerts\/history$/, (_m, q) => {
    const ruleId = q.get('rule_id');
    const items = store.history.items.filter((h) => !ruleId || h.rule_id === Number(ruleId));
    const limit = Number(q.get('limit') || 50);
    const offset = Number(q.get('offset') || 0);
    return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
  }],
  ['GET', /^\/api\/alerts\/history\/(\d+)$/, (m) => found(store.history.get(Number(m[1])))],
  ['DELETE', /^\/api\/alerts\/history\/(\d+)$/, (m) => { store.history.remove(Number(m[1])); return { status: 'ok' }; }],
  ['DELETE', /^\/api\/alerts\/history$/, () => { store.history.items = []; return { status: 'ok' }; }],
  ['GET', /^\/api\/alerts\/maintenance$/, () => maintenanceState()],
  ['POST', /^\/api\/alerts\/maintenance$/, (_m, _q, b) => {
    if (b?.until) store.sessions.push({ session_id: `demo-${Date.now()}`, reason: 'Started from the web UI', log_handling: 'silence_alerts', initiated_by: 'web', expires_at: String(b.until) });
    else store.sessions.splice(0);
    return maintenanceState();
  }],
  ['POST', /^\/api\/alerts\/maintenance\/schedules$/, (_m, _q, b) => {
    store.schedules.splice(0, store.schedules.length, ...((b?.schedules as MaintenanceSchedule[]) || []));
    return maintenanceState();
  }],
  ['DELETE', /^\/api\/alerts\/maintenance\/sessions\/([^/]+)$/, (m) => {
    const i = store.sessions.findIndex((x) => x.session_id === decodeURIComponent(m[1]));
    if (i >= 0) store.sessions.splice(i, 1);
    return maintenanceState();
  }],
  ['POST', /^\/api\/alerts\/maintenance\/sessions\/([^/]+)\/extend$/, (m, _q, b) => {
    const sess = found(store.sessions.find((x) => x.session_id === decodeURIComponent(m[1])));
    sess.expires_at = new Date(Date.parse(sess.expires_at) + Number(b?.minutes || 60) * 60_000).toISOString();
    return maintenanceState();
  }],
  ...crud('/api/drop-rules', store.dropRules, (b) => ({
    name: null, source_pattern: null, app_pattern: null, message_pattern: '', is_regex: false, is_enabled: true, severity_threshold: null,
    ...b, dropped_count: 0, created_at: nowIso(), display_order: store.dropRules.items.length,
  })),
  ['PUT', /^\/api\/drop-rules\/reorder$/, (_m, _q, b) => store.dropRules.reorder((b?.rule_ids as number[]) || [])],
  ['POST', /^\/api\/drop-rules\/(\d+)\/reset$/, (m) => found(store.dropRules.update(Number(m[1]), { dropped_count: 0 }))],
  ['GET', /^\/api\/drop-rules\/export$/, () => ({ rules: store.dropRules.items })],
  ['GET', /^\/api\/drop-rules\/(\d+)\/export$/, (m) => ({ rules: [found(store.dropRules.get(Number(m[1])))] })],
  ['POST', /^\/api\/drop-rules\/import$/, (_m, _q, b) => importRules(b, (r) => store.dropRules.add({ ...r, dropped_count: 0, created_at: nowIso() }))],
  ['GET', /^\/api\/drop-rules\/presets$/, () => store.DROP_PRESETS],
  ['POST', /^\/api\/drop-rules\/presets\/([^/]+)\/install$/, (m) => {
    const { id: _id, category: _c, description: _d, is_custom: _x, ...p } = found(store.DROP_PRESETS.find((x) => x.id === decodeURIComponent(m[1])));
    return store.dropRules.add({ ...p, is_enabled: true, dropped_count: 0, created_at: nowIso(), display_order: store.dropRules.items.length });
  }],
  ['POST', /^\/api\/drop-rules\/test$/, (_m, _q, b) => {
    const pattern = String(b?.message_pattern || '');
    const sample = String(b?.sample_message || b?.test_message || b?.message || '');
    try {
      return { matched: b?.is_regex ? new RegExp(pattern).test(sample) : sample.toLowerCase().includes(pattern.toLowerCase()), error: null };
    } catch (e) {
      return { matched: false, error: String((e as Error).message) };
    }
  }],
  ...crud('/api/saved-views', store.savedViews, (b) => ({ name: '', query_params: {}, is_pinned: false, ...b, created_at: nowIso() })),
  ...crud('/api/notifications/channels', store.channels, (b) => ({ name: '', url: '', is_enabled: true, ...b, created_at: nowIso(), updated_at: nowIso() })),
  ['POST', /^\/api\/notifications\/(test|digest\/send)$/, () => { throw notInDemo('Sending notifications'); }],
  ['GET', /^\/api\/tokens$/, () => store.tokens.items],
  ['POST', /^\/api\/tokens$/, (_m, _q, b) => {
    const prefix = `lsk_${Math.random().toString(16).slice(2, 6)}`;
    const token = store.tokens.add({ name: String(b?.name || 'New token'), scopes: (b?.scopes as string[]) || [], token_prefix: prefix, created_at: nowIso(), expires_at: (b?.expires_at as string) ?? null, last_used_at: null, created_by: 'admin' });
    return { token, raw_token: `${prefix}_demo-token-not-valid-anywhere` };
  }],
  ['PATCH', /^\/api\/tokens\/(\d+)$/, (m, _q, b) => found(store.tokens.update(Number(m[1]), b || {}))],
  ['DELETE', /^\/api\/tokens\/(\d+)$/, (m) => { store.tokens.remove(Number(m[1])); return { status: 'ok' }; }],
  ...crud('/api/docker/hosts', store.dockerHosts, (b) => ({
    name: '', url: '', source_alias: '', enabled: true, tls_enabled: false, exclude_containers: '', ...b,
    created_at: nowIso(), updated_at: nowIso(), is_connected: false, active_containers: 0, last_error: 'The demo cannot connect to Docker hosts.', last_activity: null, latency_ms: null,
  })),
  ['POST', /^\/api\/docker\/hosts\/test$/, () => { throw notInDemo('Testing a Docker connection'); }],
  ['GET', /^\/api\/aliases$/, () => store.aliases],
  ['POST', /^\/api\/aliases$/, (_m, _q, b) => {
    const entry = { ip: String(b?.ip || ''), alias: String(b?.alias || ''), notes: (b?.notes as string) ?? null, created_at: nowIso() };
    store.setAliases([...store.aliases.filter((a) => a.ip !== entry.ip), entry]);
    return entry;
  }],
  ['DELETE', /^\/api\/aliases\/(.+)$/, (m) => { store.setAliases(store.aliases.filter((a) => a.ip !== decodeURIComponent(m[1]))); return { status: 'ok' }; }],
  ['GET', /^\/api\/system\/storage$/, () => {
    const count = logs.length * 40;
    return { db_size_bytes: count * 640, disk_free_bytes: 394e9, disk_total_bytes: 1e12, total_logs_count: count, history: storage };
  }],
  ['GET', /^\/api\/maintenance\/prune\/preview$/, (_m, q) => ({
    retention_days: Number(q.get('retention_days') || settings.retention_days), cutoff: new Date(Date.now() - 30 * 86400_000).toISOString(),
    logs: 0, history_alerts: 0, history_on_demand: 0, history_digests: 0, history_maintenance: 0, history_total: 0, metrics: 0, ai_audit: 0, total: 0,
    last_cleanup_at: new Date(Date.now() - 5 * 3600_000).toISOString(), next_cleanup_at: new Date(Date.now() + 19 * 3600_000).toISOString(),
  })],
  ['POST', /^\/api\/(maintenance\/prune|system\/vacuum)$/, () => { throw notInDemo('Database maintenance'); }],
];

/** Whether the demo answers this method and path (used by the drift test). */
export function hasRoute(method: string, path: string): boolean {
  if (method === 'POST' && (path === '/api/ai/diagnose/stream' || /^\/api\/ai\/audit\/\d+\/follow-up\/stream$/.test(path))) return true;
  return ROUTES.some(([m, re]) => m === method && re.test(path));
}

export function handle(method: string, url: URL, body: Body): unknown {
  for (const [m, re, fn] of ROUTES) {
    if (m !== method) continue;
    const match = url.pathname.match(re);
    if (match) return fn(match, url.searchParams, body);
  }
  if (method === 'GET') throw new DemoHttpError(404, 'Not found in the demo');
  throw notInDemo('This action');
}

// ---- Streamed AI answers (SSE bodies) ----

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const frame = (event: unknown) => `data: ${JSON.stringify(event)}\n\n`;

function sseResponse(produce: (send: (event: unknown) => void) => Promise<void>): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      await produce((event) => controller.enqueue(encoder.encode(frame(event))));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

export function streamDiagnosis(body: Body): Response {
  const model = String(settings.ai_model);
  return sseResponse(async (send) => {
    send({ stage: 'init', model, total_models: 1, fallback_models: [] } satisfies AiDiagnosisStreamEvent);
    await sleep(300);
    send({ stage: 'calling', model, attempt: 1, total_models: 1 } satisfies AiDiagnosisStreamEvent);
    await sleep(1800 + Math.random() * 1200);
    send({ stage: 'complete', result: diagnose(body) } satisfies AiDiagnosisStreamEvent);
  });
}

export function streamFollowUp(auditId: number, body: Body): Response {
  const model = String(settings.ai_model);
  const thread = conversations.get(auditId) || [];
  const now = () => new Date().toISOString();
  thread.push({ id: nextMessageId++, role: 'user', content: String(body?.message || ''), tokens_used: 0, model, created_at: now() });
  const answer = FOLLOW_UPS[Math.floor(thread.length / 2) % FOLLOW_UPS.length];
  return sseResponse(async (send) => {
    send({ stage: 'init', model, total_models: 1 } satisfies AiFollowUpStreamEvent);
    send({ stage: 'calling', model, attempt: 1, total_models: 1 } satisfies AiFollowUpStreamEvent);
    await sleep(700);
    for (const word of answer.match(/\S+\s*/g) || []) {
      send({ stage: 'delta', content: word } satisfies AiFollowUpStreamEvent);
      await sleep(25 + Math.random() * 40);
    }
    const message: AiConversationMessage = { id: nextMessageId++, role: 'assistant', content: answer, tokens_used: 420, model, created_at: now() };
    thread.push(message);
    conversations.set(auditId, thread);
    send({
      stage: 'complete',
      result: { message, tokens_in: 1210, tokens_out: 260, tokens_thoughts: 0, tokens_used: 1470, model_used: model, fallback_used: false, fallback_attempts: [], turn_count: Math.floor(thread.length / 2), max_turns: 10 },
    } satisfies AiFollowUpStreamEvent);
  });
}

