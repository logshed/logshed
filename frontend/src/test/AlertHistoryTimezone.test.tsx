import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AlertHistoryTab } from '../components/alerts/tabs/AlertHistoryTab.tsx';
import * as settingsApi from '../api/settings.ts';
import { AlertHistoryItem } from '../types.ts';

const item: AlertHistoryItem = {
  id: 1,
  rule_id: 3,
  rule_name: 'Disk errors',
  trigger_count: 2,
  ai_enrichment: false,
  triggered_at: '2026-10-13T10:00:00Z',
};

describe('AlertHistoryTab time zone', () => {
  afterEach(() => vi.restoreAllMocks());

  it('shows trigger times in the server time zone, not browser time (E-06)', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: 'Pacific/Kiritimati' } as any);
    render(
      <AlertHistoryTab
        historyItems={[item]}
        historyTotal={1}
        isMobile={false}
        onClearAllHistory={vi.fn()}
        onSelectHistoryItem={vi.fn()}
        onDeleteHistoryItem={vi.fn()}
      />
    );
    // 10:00 UTC is 00:00 the next day at UTC+14
    expect(await screen.findByText('2026-10-14 00:00:00')).toBeInTheDocument();
  });
});
