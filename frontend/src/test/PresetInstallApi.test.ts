import { describe, it, expect, vi, afterEach } from 'vitest';
import * as clientApi from '../api/client.ts';
import { installAlertPreset } from '../api/alerts.ts';
import { installDropPreset } from '../api/dropRules.ts';

// Preset ids come from files an admin can add under /data/presets, so they may hold any character
describe('preset install requests', () => {
  afterEach(() => vi.restoreAllMocks());

  it('encodes the alert preset id in the path', async () => {
    const apiSpy = vi.spyOn(clientApi, 'apiFetch').mockResolvedValue({});
    await installAlertPreset('my preset/../x?y#z', null);
    expect(apiSpy.mock.calls[0][0]).toBe('/api/alerts/presets/my%20preset%2F..%2Fx%3Fy%23z/install');
  });

  it('encodes the drop preset id in the path', async () => {
    const apiSpy = vi.spyOn(clientApi, 'apiFetch').mockResolvedValue({});
    await installDropPreset('my preset/../x?y#z');
    expect(apiSpy.mock.calls[0][0]).toBe('/api/drop-rules/presets/my%20preset%2F..%2Fx%3Fy%23z/install');
  });
});
