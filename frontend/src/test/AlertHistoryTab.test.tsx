import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AlertHistoryTab } from '../components/alerts/tabs/AlertHistoryTab.tsx';
import type { AlertHistoryItem } from '../types.ts';

vi.mock('../api/settings.ts', () => ({
  fetchSettings: vi.fn().mockResolvedValue({ server_timezone: 'UTC' }),
  getCachedServerTimezone: () => 'UTC',
}));

const base = { channel_id: null, ai_enrichment: false, triggered_at: '2026-11-02T10:15:00Z' };

const ITEMS: AlertHistoryItem[] = [
  { ...base, id: 1, rule_id: 7, rule_name: 'Disk full', trigger_count: 1, sample_log: 'ERROR disk /data is full' },
  { ...base, id: 2, rule_id: 8, rule_name: 'Auth failures', trigger_count: 12, ai_enrichment: true, ai_model: 'gpt-6.1-sol',
    incident_summary: '## Summary\nBrute force from one address' },
  { ...base, id: 3, rule_id: null, rule_name: 'Daily Digest', trigger_count: 4500, incident_summary: 'Quiet day' },
  { ...base, id: 4, rule_id: null, rule_name: 'On-Demand Analysis', trigger_count: 1, ai_audit_id: 41,
    source_alias: 'nas01', app_name: 'backupd', incident_summary: 'Snapshot job failed' },
  { ...base, id: 5, rule_id: null, rule_name: 'On-Demand Analysis', trigger_count: 3, ai_audit_id: 42, app_name: 'nginx' },
  { ...base, id: 6, rule_id: null, rule_name: 'Maintenance Window', trigger_count: 0, maintenance_session_id: 'abc',
    maintenance_type: 'silence_alerts', maintenance_target_app: 'plex' } as AlertHistoryItem,
];

function renderTab(overrides: Partial<React.ComponentProps<typeof AlertHistoryTab>> = {}) {
  const handlers = { onClearAllHistory: vi.fn(), onSelectHistoryItem: vi.fn(), onDeleteHistoryItem: vi.fn() };
  render(
    <AlertHistoryTab historyItems={ITEMS} historyTotal={ITEMS.length} isMobile={false} {...handlers} {...overrides} />
  );
  return handlers;
}

/** The row or card holding a target name, found from its text. */
function rowOf(text: string): HTMLElement {
  return screen.getByText(text).closest('[class*="cursor-pointer"]') as HTMLElement;
}

describe.each([
  ['desktop table', false],
  ['phone cards', true],
])('AlertHistoryTab (%s)', (_label, isMobile) => {
  beforeEach(() => vi.clearAllMocks());

  it('labels each kind of record and names its target', () => {
    renderTab({ isMobile });

    expect(within(rowOf('Disk full')).getByText('Alert')).toBeInTheDocument();
    expect(within(rowOf('Auth failures')).getByText('AI Alert')).toBeInTheDocument();
    expect(within(rowOf('Quiet day')).getAllByText('Daily Digest').length).toBeGreaterThan(0);
    expect(within(rowOf('nas01 • backupd')).getByText('On-Demand')).toBeInTheDocument();
    expect(within(rowOf('nginx')).getByText('On-Demand')).toBeInTheDocument();
    expect(within(rowOf('Maintenance Window')).getByText('Maintenance')).toBeInTheDocument();
  });

  it('shows the clean summary, or the sample log when there is none', () => {
    renderTab({ isMobile });
    expect(screen.getByText('Brute force from one address')).toBeInTheDocument();
    expect(screen.getByText('ERROR disk /data is full')).toBeInTheDocument();
    expect(within(rowOf('Maintenance Window')).getByText('Silence alerts - app plex - in progress')).toBeInTheDocument();
  });

  it('opens a record from its row and from View, and deletes without opening it', () => {
    const props = renderTab({ isMobile });

    fireEvent.click(rowOf('Disk full'));
    expect(props.onSelectHistoryItem).toHaveBeenLastCalledWith(ITEMS[0]);

    fireEvent.click(within(rowOf('Auth failures')).getByRole('button', { name: 'View' }));
    expect(props.onSelectHistoryItem).toHaveBeenLastCalledWith(ITEMS[1]);
    const selectCalls = props.onSelectHistoryItem.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: 'Delete record 3' }));
    expect(props.onDeleteHistoryItem).toHaveBeenCalledWith(ITEMS[2]);
    expect(props.onSelectHistoryItem).toHaveBeenCalledTimes(selectCalls);
  });
});

describe('AlertHistoryTab empty state and paging', () => {
  it('shows the empty state without Clear All', () => {
    renderTab({ historyItems: [], historyTotal: 0 });
    expect(screen.getByText('No history recorded yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Clear All/i })).toBeNull();
  });

  it('Clear All asks the parent to clear the history', () => {
    const props = renderTab();
    fireEvent.click(screen.getByRole('button', { name: /Clear All/i }));
    expect(props.onClearAllHistory).toHaveBeenCalledTimes(1);
  });

  it('pages through the history and disables the ends', () => {
    const onPageChange = vi.fn();
    renderTab({ historyTotal: 120, limit: 50, offset: 50, onPageChange });
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument();

    const buttons = screen.getByText('Page 2 of 3').parentElement!.querySelectorAll('button');
    fireEvent.click(buttons[0]);
    expect(onPageChange).toHaveBeenLastCalledWith(0);
    fireEvent.click(buttons[1]);
    expect(onPageChange).toHaveBeenLastCalledWith(100);
  });

  it('hides paging when everything fits on one page', () => {
    renderTab({ historyTotal: 6, limit: 50, onPageChange: vi.fn() });
    expect(screen.queryByText(/Page 1 of/)).toBeNull();
  });
});

describe('AlertHistoryTab phone cards', () => {
  it('show the AI model and count events, logs or analysed logs as fits the record', () => {
    renderTab({ isMobile: true });
    expect(within(rowOf('Auth failures')).getByText('gpt-6.1-sol')).toBeInTheDocument();
    expect(within(rowOf('Disk full')).getByText('1 event')).toBeInTheDocument();
    expect(within(rowOf('Auth failures')).getByText('12 events')).toBeInTheDocument();
    expect(within(rowOf('Quiet day')).getByText('4500 logs')).toBeInTheDocument();
    expect(within(rowOf('nas01 • backupd')).getByText('1 log')).toBeInTheDocument();
    expect(within(rowOf('nginx')).getByText('3 logs')).toBeInTheDocument();
    expect(within(rowOf('Maintenance Window')).getByText('Silence alerts')).toBeInTheDocument();
  });
});
