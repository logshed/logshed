import { describe, it, expect, vi, afterEach } from 'vitest';
import * as clientApi from '../api/client.ts';
import { fetchLogs } from '../api/logs.ts';

describe('fetchLogs', () => {
  afterEach(() => vi.restoreAllMocks());

  it('passes the abort signal to apiFetch (E-28)', async () => {
    const apiSpy = vi.spyOn(clientApi, 'apiFetch').mockResolvedValue({ logs: [], total: 0, limit: 500, offset: 0 });
    const controller = new AbortController();
    await fetchLogs({ from: '1h' }, { signal: controller.signal });
    expect(apiSpy).toHaveBeenCalledWith('/api/logs?from=1h', { signal: controller.signal });
  });

  it('sends relative time presets unchanged (B-23)', async () => {
    const apiSpy = vi.spyOn(clientApi, 'apiFetch').mockResolvedValue({ logs: [], total: 0, limit: 500, offset: 0 });
    await fetchLogs({ from: '24h', limit: 500, offset: 0 });
    expect(apiSpy).toHaveBeenCalledWith('/api/logs?from=24h&limit=500&offset=0', {});
  });
});
