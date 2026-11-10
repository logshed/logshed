// The browser demo (src/demo) must answer every /api path the web UI calls, or a page breaks in the demo
// without anyone noticing. This reads the paths from src/api/*.ts and asks the demo's router about each.
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import fs from 'fs';
import path from 'path';

const apiDir = path.resolve(__dirname, '../api');

// Paths built in a variable, with the method the call uses
const EXTRA: Array<[string, string]> = [
  ['POST', '/api/ai/audit/1/follow-up'],
  ['POST', '/api/ai/audit/1/follow-up/stream'],
  ['POST', '/api/ai/diagnose/stream'],
];

function calledPaths(): Array<[string, string]> {
  const found: Array<[string, string]> = [];
  for (const file of fs.readdirSync(apiDir).filter((f) => f.endsWith('.ts'))) {
    const lines = fs.readFileSync(path.join(apiDir, file), 'utf8').split('\n');
    lines.forEach((line, i) => {
      for (const m of line.matchAll(/(apiFetch\w*|postStream)\s*(?:<[^(]*>)?\(\s*(['`])(\/api\/[^'`]*)\2(\s*\+)?/g)) {
        // `/${id}` is a path segment; any other `${...}` starts a query string
        let p = m[3].replace(/\/\$\{[^}]*\}/g, '/1').replace(/\$\{.*$/, '').split('?')[0];
        if (m[4] || p.endsWith('/')) p = p.replace(/\/?$/, '/1');
        // A path built after the literal (`... + '/install'`)
        const tail = line.slice((m.index ?? 0) + m[0].length).match(/^\s*[\w.]+(?:\([^)]*\))?\s*\+\s*'([^']*)'/);
        if (tail) p += tail[1];
        // The call's options, up to the end of the function
        const end = lines.findIndex((l, j) => j > i && /^}/.test(l));
        const call = lines.slice(i, end < 0 ? undefined : end).join('\n');
        const method = call.match(/method:\s*'(\w+)'/)?.[1] ?? (m[1] === 'postStream' ? 'POST' : 'GET');
        found.push([method, p]);
      }
    });
  }
  return [...found, ...EXTRA];
}

describe('browser demo routes', () => {
  let hasRoute: (method: string, path: string) => boolean;
  beforeAll(async () => {
    vi.useFakeTimers();
    ({ hasRoute } = await import('../demo/server.ts'));
  });
  afterAll(() => {
    vi.useRealTimers();
  });

  it('reads a believable number of paths from src/api', () => {
    expect(calledPaths().length).toBeGreaterThan(60);
  });

  it('answers every method and path the web UI calls', () => {
    const missing = calledPaths().filter(([m, p]) => !hasRoute(m, p)).map(([m, p]) => `${m} ${p}`);
    expect(missing).toEqual([]);
  });
});
