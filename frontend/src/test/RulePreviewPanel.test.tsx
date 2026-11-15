import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RulePreviewPanel, formatSpan, previewSummary } from '../components/alerts/RulePreviewPanel.tsx';
import { AlertRuleModal } from '../components/alerts/AlertRuleModal.tsx';
import { CreateDropRuleModal } from '../components/alerts/CreateDropRuleModal.tsx';
import * as alertsApi from '../api/alerts.ts';
import * as dropRulesApi from '../api/dropRules.ts';
import * as settingsApi from '../api/settings.ts';
import * as aliasesApi from '../api/aliases.ts';
import { ApiError } from '../api/client.ts';
import { LogEntry, RulePreviewResponse } from '../types.ts';

const NOW = Date.parse('2026-11-15T18:00:00Z');

function log(id: number, timestamp: string, message: string): LogEntry {
  return {
    id,
    timestamp,
    received_at: timestamp,
    source_ip: '10.0.0.5',
    source_alias: 'nas',
    app_name: 'sshd',
    facility: 4,
    severity: 6,
    message,
    raw: message,
  } as LogEntry;
}

function result(overrides: Partial<RulePreviewResponse> = {}): RulePreviewResponse {
  return {
    matched: 1234,
    checked: 52100,
    complete: true,
    stopped_by: null,
    row_cap: 200000,
    window_start: '2026-11-14T18:00:00+00:00',
    oldest_checked: '2026-11-14T18:00:05+00:00',
    pattern_timeouts: 0,
    examples: [],
    error: null,
    ...overrides,
  };
}

describe('previewSummary', () => {
  it('counts the whole day when every log was checked', () => {
    expect(previewSummary(result(), 'matched', 'UTC', NOW)).toBe(
      `Would have matched ${(1234).toLocaleString()} of ${(52100).toLocaleString()} logs from the last 24 hours.`
    );
  });

  it('says how far back a check stopped by the row cap reached, in the server time zone', () => {
    const capped = result({ complete: false, stopped_by: 'rows', checked: 200000, oldest_checked: '2026-11-15T14:40:00+00:00' });
    expect(previewSummary(capped, 'dropped', 'Europe/Paris', NOW)).toBe(
      `Would have dropped ${(1234).toLocaleString()} of the newest ${(200000).toLocaleString()} logs, back to 2026-11-15 15:40 (the last 3 h 20 min). A check reads at most ${(200000).toLocaleString()} logs.`
    );
  });

  it('names the time cap when the check ran out of time', () => {
    const timed = result({ complete: false, stopped_by: 'time', checked: 80000, oldest_checked: '2026-11-15T17:15:00+00:00' });
    expect(previewSummary(timed, 'matched', 'UTC', NOW)).toMatch(/back to 2026-11-15 17:15 \(the last 45 min\)\. A check stops after 5 seconds\.$/);
  });

  it('says when there was nothing to check', () => {
    expect(previewSummary(result({ checked: 0, matched: 0 }), 'matched', 'UTC', NOW)).toBe(
      'No stored logs from the last 24 hours to check.'
    );
  });

  it('formats spans in minutes and hours', () => {
    expect([formatSpan(10_000), formatSpan(45 * 60000), formatSpan(60 * 60000), formatSpan(200 * 60000)]).toEqual([
      '1 min', '45 min', '1 h', '3 h 20 min',
    ]);
  });
});

describe('RulePreviewPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: 'UTC' } as any);
  });

  it('shows the count, the timeouts, the newest matches and the note, and hides them once the rule changes', async () => {
    const run = vi.fn().mockResolvedValue(
      result({ pattern_timeouts: 1, examples: [log(7, '2026-11-15T17:59:00+00:00', 'Failed password for root')] })
    );
    const { rerender } = render(<RulePreviewPanel request={{ p: 'a' }} run={run} verb="matched" note="Counts single logs." />);
    fireEvent.click(screen.getByRole('button', { name: 'Check last 24h' }));

    const status = screen.getByRole('status');
    await waitFor(() => expect(within(status).getByTestId('rule-preview-summary')).toBeTruthy());
    expect(run).toHaveBeenCalledWith({ p: 'a' });
    expect(within(status).getByText(/The pattern timed out on 1 log, counted as no match/)).toBeTruthy();
    const examples = within(status).getByRole('list', { name: 'Newest matching logs' });
    expect(examples.textContent).toContain('Failed password for root');
    expect(examples.textContent).toContain('nas');
    expect(within(status).getByText('Counts single logs.')).toBeTruthy();

    rerender(<RulePreviewPanel request={{ p: 'b' }} run={run} verb="matched" note="Counts single logs." />);
    expect(screen.queryByTestId('rule-preview-summary')).toBeNull();
    rerender(<RulePreviewPanel request={{ p: 'a' }} run={run} verb="matched" note="Counts single logs." />);
    expect(screen.getByTestId('rule-preview-summary')).toBeTruthy();
  });

  it('shows the server message when another check is running, and a pattern error', async () => {
    const run = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, 'Another check of the last 24 hours is running. Try again in a few seconds.'))
      .mockResolvedValueOnce(result({ error: 'Pattern has nested quantifiers.', checked: 0, matched: 0 }));
    render(<RulePreviewPanel request={{}} run={run} verb="dropped" note="n" />);
    fireEvent.click(screen.getByRole('button', { name: 'Check last 24h' }));
    expect(await screen.findByText(/Another check of the last 24 hours is running/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Check last 24h' }));
    expect(await screen.findByText('Pattern has nested quantifiers.')).toBeTruthy();
    expect(screen.queryByTestId('rule-preview-summary')).toBeNull();
  });
});

describe('Check last 24h in the rule dialogs', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: 'UTC' } as any);
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
  });

  it('the alert rule dialog checks the rule as it would be saved', async () => {
    const preview = vi.spyOn(alertsApi, 'previewAlertRule').mockResolvedValue(result());
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={[]}
        availableApps={['sshd']}
        prefill={{ filterApps: ['sshd'], filterHosts: ['nas'] }}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />
    );
    fireEvent.change(screen.getByLabelText('Match Pattern'), { target: { value: '  Failed password  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check last 24h' }));
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls[0][0]).toEqual({
      filter_app: 'sshd',
      filter_host: 'nas',
      filter_severity: null,
      match_pattern: 'Failed password',
      is_regex: false,
    });
    expect(await screen.findByText(/the threshold, cooldown and maintenance windows are not applied/)).toBeTruthy();
  });

  it('the drop rule dialog needs a criterion and checks the rule as it would be saved', async () => {
    const preview = vi.spyOn(dropRulesApi, 'previewDropRule').mockResolvedValue(result());
    render(<CreateDropRuleModal isOpen={true} onClose={vi.fn()} />);
    const button = screen.getByRole('button', { name: 'Check last 24h' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText(/DHCPACK/), { target: { value: 'GET /healthz' } });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(preview).toHaveBeenCalled());
    expect(preview.mock.calls[0][0]).toEqual({
      message_pattern: 'GET /healthz',
      is_regex: false,
      source_pattern: undefined,
      app_pattern: undefined,
      severity_threshold: null,
    });
    expect(await screen.findByText(/Would have dropped/)).toBeTruthy();
    expect(screen.getByText(/logs that drop rules already discarded/)).toBeTruthy();
  });
});
