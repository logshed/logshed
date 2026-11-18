import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DropRulesTab } from '../components/alerts/tabs/DropRulesTab.tsx';
import { AlertRulesTab } from '../components/alerts/tabs/AlertRulesTab.tsx';
import { AlertRuleModal } from '../components/alerts/AlertRuleModal.tsx';
import * as dropRulesApi from '../api/dropRules.ts';
import * as alertsApi from '../api/alerts.ts';
import { AlertRule, DropRule } from '../types.ts';

vi.mock('../api/settings.ts', () => ({
  fetchSettings: vi.fn().mockResolvedValue({ server_timezone: 'UTC' }),
  getCachedServerTimezone: () => 'UTC',
}));

const dropRule = (id: number, dropped_count: number): DropRule => ({
  id,
  name: `Drop ${id}`,
  source_pattern: null,
  app_pattern: null,
  message_pattern: `noise ${id}`,
  is_regex: false,
  is_enabled: true,
  severity_threshold: null,
  dropped_count,
  created_at: '2026-11-18T10:00:00Z',
});

const alertRule = (id: number, trigger_count: number): AlertRule => ({
  id,
  name: `Alert ${id}`,
  rule_type: 'threshold',
  channel_id: null,
  filter_app: null,
  filter_severity: null,
  match_pattern: `boom ${id}`,
  threshold_count: 1,
  window_seconds: 60,
  cooldown_seconds: 300,
  ai_enrichment: false,
  is_enabled: true,
  trigger_count,
  last_triggered_at: trigger_count ? '2026-11-18T09:00:00Z' : null,
  suppress_until: null,
  created_at: '2026-11-18T08:00:00Z',
});

function renderAlertTab(rules: AlertRule[], onResetAllCounters = vi.fn().mockResolvedValue(undefined)) {
  render(
    <AlertRulesTab
      rules={rules}
      channels={[]}
      isMaintenanceActiveNow={false}
      maintenance={{ active: false, until: null }}
      onClearMaintenance={vi.fn()}
      onNavigateToMaintenance={vi.fn()}
      onOpenPresetsModal={vi.fn()}
      onExportAll={vi.fn()}
      onFileImport={vi.fn()}
      fileInputRef={{ current: null }}
      onOpenCreateModal={vi.fn()}
      onToggleRule={vi.fn()}
      onOpenTestModal={vi.fn()}
      onExportSingle={vi.fn()}
      onOpenEditModal={vi.fn()}
      onDeleteRule={vi.fn()}
      onNavigateToSettings={vi.fn()}
      onResetAllCounters={onResetAllCounters}
      getChannelStatus={() => ({ name: 'None', warning: null, isInvalid: false })}
    />
  );
  return onResetAllCounters;
}

describe('Reset counters', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('resets every drop rule counter after a dialog naming the rules and the total', async () => {
    const fetchRules = vi
      .spyOn(dropRulesApi, 'fetchDropRules')
      .mockResolvedValueOnce([dropRule(1, 1_000_000), dropRule(2, 234), dropRule(3, 0)])
      .mockResolvedValue([dropRule(1, 0), dropRule(2, 0), dropRule(3, 0)]);
    const reset = vi.spyOn(dropRulesApi, 'resetAllDropRuleCounters').mockResolvedValue({ status: 'ok' });
    render(<DropRulesTab />);
    await screen.findByText('1,000,234 dropped');

    fireEvent.click(screen.getByRole('button', { name: 'Reset all counters' }));
    const dialog = await screen.findByTestId('reset-counters-dialog');
    expect(
      within(dialog).getByText('Sets the counters of 2 drop rules back to 0, clearing 1,000,234 dropped logs. Logs dropped from now on count from 1.')
    ).toBeInTheDocument();
    expect(reset).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset counters' }));

    await waitFor(() => expect(reset).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId('reset-counters-dialog')).not.toBeInTheDocument());
    expect(fetchRules).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/dropped$/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reset all counters' })).toBeDisabled();
  });

  it('leaves the drop counters alone on Cancel', async () => {
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue([dropRule(1, 1)]);
    const reset = vi.spyOn(dropRulesApi, 'resetAllDropRuleCounters').mockResolvedValue({ status: 'ok' });
    render(<DropRulesTab />);
    await screen.findByText('Drop 1');
    fireEvent.click(screen.getByRole('button', { name: 'Reset all counters' }));
    const dialog = await screen.findByTestId('reset-counters-dialog');
    expect(within(dialog).getByText(/Sets the counters of 1 drop rule back to 0, clearing 1 dropped log\./)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByTestId('reset-counters-dialog')).not.toBeInTheDocument());
    expect(reset).not.toHaveBeenCalled();
  });

  it('resets every alert rule counter after a dialog that says Last triggered stays', async () => {
    const onReset = renderAlertTab([alertRule(1, 3), alertRule(2, 2), alertRule(3, 0)]);
    fireEvent.click(screen.getByRole('button', { name: 'Reset all counters' }));
    const dialog = await screen.findByTestId('reset-counters-dialog');
    expect(
      within(dialog).getByText('Sets the counters of 2 alert rules back to 0, clearing 5 firings. Last triggered times and the alert history stay.')
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset counters' }));
    await waitFor(() => expect(onReset).toHaveBeenCalledTimes(1));
  });

  it('has nothing to reset when every alert counter is 0', () => {
    renderAlertTab([alertRule(1, 0)]);
    expect(screen.getByRole('button', { name: 'Reset all counters' })).toBeDisabled();
  });

  it("resets one alert rule's counter from its editor and keeps the editor open", async () => {
    const reset = vi.spyOn(alertsApi, 'resetAlertRuleCounter').mockResolvedValue(alertRule(7, 0));
    const onCounterReset = vi.fn();
    render(
      <AlertRuleModal
        isOpen
        ruleToEdit={alertRule(7, 4)}
        channels={[]}
        availableApps={[]}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        onCounterReset={onCounterReset}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Reset counter to 0 (4)' }));
    await waitFor(() => expect(reset).toHaveBeenCalledWith(7));
    expect(onCounterReset).toHaveBeenCalledWith(alertRule(7, 0));
    expect(await screen.findByRole('button', { name: 'Reset counter to 0 (0)' })).toBeDisabled();
  });

  it('shows a failed counter reset beside its button', async () => {
    vi.spyOn(alertsApi, 'resetAlertRuleCounter').mockRejectedValue(new Error('LogShed is not reachable'));
    render(
      <AlertRuleModal isOpen ruleToEdit={alertRule(7, 4)} channels={[]} availableApps={[]} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Reset counter to 0 (4)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('LogShed is not reachable');
    expect(screen.getByRole('button', { name: 'Reset counter to 0 (4)' })).toBeEnabled();
  });

  it('has no counter reset when creating an alert rule', () => {
    render(<AlertRuleModal isOpen ruleToEdit={null} channels={[]} availableApps={[]} onClose={vi.fn()} onSuccess={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Reset counter/ })).not.toBeInTheDocument();
  });
});
