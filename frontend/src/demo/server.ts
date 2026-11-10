// The fake backend: answers the app's /api requests from in-memory data, in the browser.
import type {
  LogEntry, LogFacetsResponse, AiDiagnosisRequest, AiDiagnosisResponse, AiConversationMessage,
  AiDiagnosisStreamEvent, AiFollowUpStreamEvent,
} from '../types.ts';
import { matchesSearchQuery } from '../components/logs/LiveLogStream.tsx';
import { makeHistory, makeLog } from './generator.ts';
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
  ['DELETE', /^\/api\/ai\/audit\/(\d+)\/messages$/, (m) => { conversations.set(Number(m[1]), (conversations.get(Number(m[1])) || []).slice(0, 1)); return { status: 'ok' }; }],
  // Sections the spike does not fill yet: empty lists, so the pages still open
  ['GET', /^\/api\/(alerts\/rules|alerts\/presets|aliases|drop-rules|drop-rules\/presets|saved-views|notifications\/channels|tokens|docker\/hosts)$/, () => []],
  ['GET', /^\/api\/alerts\/history$/, (_m, q) => ({ items: [], total: 0, limit: Number(q.get('limit') || 50), offset: 0 })],
  ['GET', /^\/api\/alerts\/maintenance$/, () => ({ active: false, until: null, schedules: [], sessions: [], active_sessions_count: 0 })],
  ['GET', /^\/api\/system\/storage$/, () => ({ db_size_bytes: logs.length * 650, disk_free_bytes: 412e9, disk_total_bytes: 1e12, total_logs_count: logs.length, history: [] })],
];

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

