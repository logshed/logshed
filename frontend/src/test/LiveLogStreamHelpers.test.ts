import { describe, it, expect } from 'vitest';
import {
  matchesSearchQuery,
  compileSearchQuery,
  rankQuickFilterChips,
  countQuickFilterVolumes,
  hasActiveLogFilters,
} from '../components/logs/LiveLogStream.tsx';
import { LogEntry } from '../types.ts';

const sampleLog: LogEntry = {
  id: 101,
  timestamp: '2026-09-03T14:30:15.123Z',
  received_at: '2026-09-03T14:30:15.150Z',
  source_ip: '192.168.1.50',
  source_alias: 'homelab-host',
  app_name: 'nginx',
  facility: 1,
  severity: 3,
  message: 'Nginx upstream connection timeout',
  raw: '<11>1 2026-09-03T14:30:15.123Z homelab-host nginx - - - Nginx upstream connection timeout',
};

describe('matchesSearchQuery Helper Function', () => {
  const baseLog: LogEntry = {
    id: 999,
    timestamp: '2026-09-03T12:00:00.000Z',
    received_at: '2026-09-03T12:00:00.010Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: 'Upstream connection timeout to microservice',
    raw: 'raw log',
  };

  it('matches all logs when query is empty or whitespace', () => {
    expect(matchesSearchQuery(baseLog, '')).toBe(true);
    expect(matchesSearchQuery(baseLog, '   ')).toBe(true);
    expect(matchesSearchQuery(baseLog, undefined)).toBe(true);
  });

  it('performs case-insensitive substring matching on message, app_name, source_alias, and source_ip', () => {
    expect(matchesSearchQuery(baseLog, 'timeout')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'TIMEOUT')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'NGINX')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'homelab')).toBe(true);
    expect(matchesSearchQuery(baseLog, '192.168.1.50')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'unrelated-keyword')).toBe(false);
  });

  it('matches multiple space-separated terms', () => {
    expect(matchesSearchQuery(baseLog, 'nginx timeout')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'homelab microservice')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'nginx missing_term')).toBe(false);
  });

  it('supports precompiled regex patterns when explicitly supplied', () => {
    expect(matchesSearchQuery(baseLog, '^Upstream.*timeout', /^Upstream.*timeout/i)).toBe(true);
    expect(matchesSearchQuery(baseLog, 'microservice$', /microservice$/i)).toBe(true);
    expect(matchesSearchQuery(baseLog, '^microservice', /^microservice/i)).toBe(false);
  });

  it('does not dynamically compile arbitrary regex queries to prevent ReDoS', () => {
    expect(matchesSearchQuery(baseLog, '^Upstream.*timeout')).toBe(false);
  });

  it('supports column-specific queries like app_name: and source:', () => {
    expect(matchesSearchQuery(baseLog, 'app_name:nginx')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'app:nginx')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'app_name:postgres')).toBe(false);
    expect(matchesSearchQuery(baseLog, 'source:homelab-host')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'source:router')).toBe(false);
    expect(matchesSearchQuery(baseLog, 'message:timeout')).toBe(true);
  });
});

describe('matchesSearchQuery AND, OR and NOT like history search', () => {
  const MESSAGES = ['alpha', 'bravo', 'charlie', 'alpha charlie', 'bravo charlie', 'alpha bravo', 'alpha bravo charlie'];
  const makeLog = (message: string, extra: Partial<LogEntry> = {}): LogEntry => ({
    id: 1,
    timestamp: '2026-10-20T10:00:00.000Z',
    received_at: '2026-10-20T10:00:00.010Z',
    source_ip: '10.0.0.9',
    source_alias: 'h1',
    app_name: 'svc',
    facility: 1,
    severity: 4,
    message,
    raw: message,
    ...extra,
  });
  const matching = (query: string) =>
    MESSAGES.filter((m) => matchesSearchQuery(makeLog(m), compileSearchQuery(query))).sort();
  const sorted = (list: string[]) => [...list].sort();

  // Same cases as TestSearchBooleanOperators in backend/tests/test_logs_api.py
  it.each([
    ['alpha OR bravo', ['alpha', 'bravo', 'alpha charlie', 'bravo charlie', 'alpha bravo', 'alpha bravo charlie']],
    ['alpha or bravo', ['alpha', 'bravo', 'alpha charlie', 'bravo charlie', 'alpha bravo', 'alpha bravo charlie']],
    ['charlie NOT alpha', ['charlie', 'bravo charlie']],
    ['charlie not alpha', ['charlie', 'bravo charlie']],
    ['alpha OR bravo charlie', ['alpha', 'alpha charlie', 'alpha bravo', 'alpha bravo charlie', 'bravo charlie']],
    ['alpha OR bravo AND charlie', ['alpha', 'alpha charlie', 'alpha bravo', 'alpha bravo charlie', 'bravo charlie']],
    ['alpha NOT bravo AND charlie', ['alpha charlie']],
    ['alpha NOT bravo charlie', ['alpha', 'alpha charlie', 'alpha bravo']],
    ['alpha charlie NOT bravo', ['alpha charlie']],
    ['alpha bravo OR charlie', ['alpha bravo', 'alpha bravo charlie', 'charlie', 'alpha charlie', 'bravo charlie']],
    ['alpha NOT bravo OR charlie', ['alpha', 'alpha charlie', 'charlie', 'bravo charlie', 'alpha bravo charlie']],
    ['alpha OR bravo NOT charlie', ['alpha', 'alpha charlie', 'alpha bravo', 'alpha bravo charlie', 'bravo']],
    ['(alpha OR bravo) AND charlie', ['alpha charlie', 'bravo charlie', 'alpha bravo charlie']],
    ['"alpha bravo"', ['alpha bravo', 'alpha bravo charlie']],
    ['"alpha bravo" OR charlie', ['alpha bravo', 'alpha bravo charlie', 'charlie', 'alpha charlie', 'bravo charlie']],
  ])('%s', (query, expected) => {
    expect(matching(query)).toEqual(sorted(expected));
  });

  // Same cases as test_terms_match_word_starts_like_fts5 in backend/tests/test_logs_api.py
  const WORD_MESSAGES = [
    'cron job started',
    'crontab updated',
    'microns measured',
    'acronym list',
    'disk full on node',
    'full disk',
    'key=value pair',
    'pve-node2 up',
    'caf\u00e9 ouvert',
  ];
  it.each([
    ['cron', ['cron job started', 'crontab updated']],
    ['rons', []],
    ['microns OR acronym NOT cron', ['microns measured', 'acronym list']],
    ['msg:cron', ['cron job started', 'crontab updated']],
    ['disk full', ['disk full on node', 'full disk']],
    ['"disk full"', ['disk full on node']],
    ['"disk fu"', []],
    ['pve-node', ['pve-node2 up']],
    ['key=val', ['key=value pair']],
    ['cafe', ['caf\u00e9 ouvert']],
  ])('matches word starts like FTS5: %s', (query, expected) => {
    const matched = WORD_MESSAGES.filter((m) => matchesSearchQuery(makeLog(m), compileSearchQuery(query)));
    expect(sorted(matched)).toEqual(sorted(expected));
  });

  it('keeps the popover examples right: error OR timeout and auth NOT cron', () => {
    const q1 = compileSearchQuery('error OR timeout');
    expect(matchesSearchQuery(makeLog('disk error'), q1)).toBe(true);
    expect(matchesSearchQuery(makeLog('request timeout'), q1)).toBe(true);
    expect(matchesSearchQuery(makeLog('all good'), q1)).toBe(false);

    const q2 = compileSearchQuery('auth NOT cron');
    expect(matchesSearchQuery(makeLog('auth failure for root'), q2)).toBe(true);
    expect(matchesSearchQuery(makeLog('auth session opened by cron'), q2)).toBe(false);
    // The whole query appearing as text does not bypass NOT
    expect(matchesSearchQuery(makeLog('auth not cron'), q2)).toBe(false);
  });

  it('combines column filters with OR and NOT', () => {
    const nginxOnNas = makeLog('worker started', { app_name: 'nginx', source_alias: 'nas' });
    const sshdOnPve = makeLog('session opened', { app_name: 'sshd', source_alias: 'pve-node2' });
    expect(matchesSearchQuery(nginxOnNas, compileSearchQuery('app:nginx OR app:sshd'))).toBe(true);
    expect(matchesSearchQuery(sshdOnPve, compileSearchQuery('app:nginx OR app:sshd'))).toBe(true);
    expect(matchesSearchQuery(sshdOnPve, compileSearchQuery('host:pve-node2 NOT app:sshd'))).toBe(false);
    expect(matchesSearchQuery(nginxOnNas, compileSearchQuery('msg:"worker started" NOT host:pve-node2'))).toBe(true);
  });

  it('falls back to requiring every word when the operators are incomplete, as while typing', () => {
    // Trailing or leading operators and empty brackets are not valid expressions
    expect(matchesSearchQuery(makeLog('disk error'), compileSearchQuery('error OR'))).toBe(true);
    expect(matchesSearchQuery(makeLog('cron job ran'), compileSearchQuery('NOT cron'))).toBe(true);
    expect(matchesSearchQuery(makeLog('disk error'), compileSearchQuery('(error'))).toBe(true);
    expect(matchesSearchQuery(makeLog('disk error'), compileSearchQuery('error OR OR timeout'))).toBe(false);
  });
});

describe('compileSearchQuery', () => {
  const log: LogEntry = { ...sampleLog };

  it('compiles the query once and matches like the string form', () => {
    const compiled = compileSearchQuery('app:nginx timeout');
    expect(compiled.terms).toEqual(['app:nginx', 'timeout']);
    expect(matchesSearchQuery(log, compiled)).toBe(true);
    expect(matchesSearchQuery(log, 'app:nginx timeout')).toBe(true);
    expect(matchesSearchQuery(log, compileSearchQuery('app:postgres'))).toBe(false);
    expect(matchesSearchQuery(log, compileSearchQuery('  '))).toBe(true);
  });
});

describe('Quick Filter and empty-state helpers', () => {
  it('rankQuickFilterChips orders by count, then alphabetically, and caps the list', () => {
    const counts = { b: 5, a: 5, c: 9, d: 1 };
    expect(rankQuickFilterChips(['d', 'c', 'b', 'a', 'e'], counts, [], 3)).toEqual(['c', 'a', 'b']);
    expect(rankQuickFilterChips(['x', 'w'], {}, [])).toEqual(['w', 'x']);
  });

  it('rankQuickFilterChips appends selected values that fell outside the cap, once', () => {
    expect(rankQuickFilterChips(['a', 'b', 'c'], { a: 3, b: 2, c: 1 }, ['c', 'a', 'c'], 2)).toEqual(['a', 'b', 'c']);
    expect(rankQuickFilterChips(['a'], {}, ['zz'], 6)).toEqual(['a', 'zz']);
  });

  it('countQuickFilterVolumes counts hosts by alias (falling back to IP) and apps', () => {
    const base = sampleLog;
    const counts = countQuickFilterVolumes([
      base,
      { ...base, id: 2 },
      { ...base, id: 3, source_alias: '', source_ip: '10.0.0.9', app_name: 'sshd' },
    ]);
    expect(counts.hosts).toEqual({ 'homelab-host': 2, '10.0.0.9': 1 });
    expect(counts.apps).toEqual({ nginx: 2, sshd: 1 });
  });

  it('counts and ranks names that clash with object properties like any other name', () => {
    const base = sampleLog;
    const counts = countQuickFilterVolumes([
      { ...base, id: 1, app_name: 'constructor' },
      { ...base, id: 2, app_name: 'constructor' },
      { ...base, id: 3, app_name: 'toString' },
    ]);
    expect(counts.apps.constructor).toBe(2);
    expect(rankQuickFilterChips(['toString', 'constructor', 'valueOf'], counts.apps, [])).toEqual([
      'constructor',
      'toString',
      'valueOf',
    ]);
  });

  it('hasActiveLogFilters spots any search, severity, host, app or time filter', () => {
    expect(hasActiveLogFilters({})).toBe(false);
    expect(hasActiveLogFilters({ query: '  ', sources: [], apps: [] })).toBe(false);
    expect(hasActiveLogFilters({ query: 'x' })).toBe(true);
    expect(hasActiveLogFilters({ severity_max: 0 })).toBe(true);
    expect(hasActiveLogFilters({ sources: ['h'] })).toBe(true);
    expect(hasActiveLogFilters({ source: 'h' })).toBe(true);
    expect(hasActiveLogFilters({ app_name: ['a'] })).toBe(true);
    expect(hasActiveLogFilters({ from: '1h' })).toBe(true);
    expect(hasActiveLogFilters({ to: '2026-10-13T12:00:00Z' })).toBe(true);
  });
});
