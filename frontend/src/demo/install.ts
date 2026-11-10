// Replace fetch and EventSource with the in-browser fake backend before the app starts.
import { DemoHttpError, handle, logFilter, streamDiagnosis, streamFollowUp, subscribe } from './server.ts';

const realFetch = window.fetch.bind(window);
const json = (status: number, data: unknown) =>
  new Response(status === 204 ? null : JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

async function demoFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, window.location.origin);
  if (!url.pathname.startsWith('/api/')) return realFetch(input, init);
  if (init.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
  const method = (init.method || 'GET').toUpperCase();
  let body: Record<string, any> | null = null;
  if (typeof init.body === 'string' && init.body) {
    try { body = JSON.parse(init.body); } catch { body = null; }
  }
  // A short delay, so loading states look as they do on a real server
  await new Promise((r) => setTimeout(r, 40 + Math.random() * 120));
  try {
    if (method === 'POST' && url.pathname === '/api/ai/diagnose/stream') return streamDiagnosis(body);
    const followUp = url.pathname.match(/^\/api\/ai\/audit\/(\d+)\/follow-up\/stream$/);
    if (method === 'POST' && followUp) return streamFollowUp(Number(followUp[1]), body);
    return json(200, handle(method, url, body));
  } catch (err) {
    if (err instanceof DemoHttpError) return json(err.status, { detail: err.message });
    console.error('[demo]', method, url.pathname, err);
    return json(500, { detail: 'The demo could not answer this request.' });
  }
}

/** The live log stream: log events for new demo lines that pass the stream's filters. */
class DemoEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readonly url: string;
  readonly withCredentials = false;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private unsubscribe: (() => void) | null = null;

  constructor(url: string | URL) {
    super();
    const parsed = new URL(String(url), window.location.origin);
    this.url = parsed.href;
    const match = logFilter(parsed.searchParams);
    setTimeout(() => {
      if (this.readyState === 2) return;
      this.readyState = 1;
      const open = new Event('open');
      this.onopen?.(open);
      this.dispatchEvent(open);
      this.unsubscribe = subscribe((log) => {
        if (match(log)) this.dispatchEvent(new MessageEvent('log', { data: JSON.stringify(log) }));
      });
    }, 150);
  }

  close() {
    this.readyState = 2;
    this.unsubscribe?.();
    this.unsubscribe = null;
  }
}

window.fetch = demoFetch as typeof window.fetch;
window.EventSource = DemoEventSource as unknown as typeof EventSource;
