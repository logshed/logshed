import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MaintenanceWindowTab, formatScheduleRecurrence } from '../components/alerts/tabs/MaintenanceWindowTab.tsx';
import type { MaintenanceSchedule, MaintenanceWindowResponse } from '../types.ts';
import { copyToClipboard } from '../utils/clipboard.ts';

vi.mock('../api/settings.ts', () => ({
  fetchSettings: vi.fn().mockResolvedValue({ server_timezone: 'UTC' }),
  getCachedServerTimezone: () => 'UTC',
}));

vi.mock('../utils/clipboard.ts', () => ({
  copyToClipboard: vi.fn().mockResolvedValue(true),
}));

const schedule = (overrides: Partial<MaintenanceSchedule>): MaintenanceSchedule => ({
  id: 'sched',
  name: 'Schedule',
  enabled: true,
  recurrence: 'daily',
  start_time: '02:00',
  duration_minutes: 30,
  ...overrides,
});

const SCHEDULES: MaintenanceSchedule[] = [
  schedule({ id: 's1', name: 'Nightly backup', next_run: '2026-11-03T02:00:00Z' }),
  schedule({ id: 's2', name: 'Patch Tuesday', recurrence: 'weekly', day_of_week: 2, is_active: true, next_run: '2026-11-10T02:00:00Z' }),
  schedule({ id: 's3', name: 'Paused scrub', recurrence: 'monthly', day_of_month: 15, enabled: false, next_run: '2026-11-15T02:00:00Z' }),
];

const MAINTENANCE: MaintenanceWindowResponse = {
  active: true,
  until: '2026-11-02T12:00:00Z',
  schedules: SCHEDULES,
  sessions: [
    { session_id: 'aaaa1111bbbb', reason: 'Nightly backup', log_handling: 'drop_errors', target_app: 'restic',
      target_host: 'nas01', initiated_by: 'api', expires_at: '2026-11-02T12:00:00Z' },
    { session_id: 'cccc2222dddd', reason: null, log_handling: 'silence_alerts', initiated_by: 'ui',
      expires_at: '2026-11-02T11:00:00Z' },
  ],
};

function renderTab(overrides: Partial<React.ComponentProps<typeof MaintenanceWindowTab>> = {}) {
  const handlers = {
    setCustomUntil: vi.fn(),
    onClearMaintenance: vi.fn(),
    onSetPreset: vi.fn(),
    onExtendSession: vi.fn(),
    onApplyCustomWindow: vi.fn(),
    onOpenAddScheduleModal: vi.fn(),
    onOpenEditScheduleModal: vi.fn(),
    onToggleScheduleEnabled: vi.fn(),
    onDeleteSchedule: vi.fn(),
    onDisableSession: vi.fn(),
  };
  render(
    <MaintenanceWindowTab maintenance={MAINTENANCE} isOnDemandActiveNow={false} customUntil="" {...handlers} {...overrides} />
  );
  return handlers;
}

describe('formatScheduleRecurrence', () => {
  it.each([
    [schedule({ recurrence: 'daily' }), 'Daily at 02:00'],
    [schedule({ recurrence: 'weekly', day_of_week: 5 }), 'Weekly on Friday at 02:00'],
    [schedule({ recurrence: 'weekly', day_of_week: null }), 'Weekly on Sunday at 02:00'],
    [schedule({ recurrence: 'weekly', day_of_week: 9 }), 'Weekly on Sunday at 02:00'],
    [schedule({ recurrence: 'monthly', day_of_month: 28 }), 'Monthly on day 28 at 02:00'],
    [schedule({ recurrence: 'monthly', day_of_month: null }), 'Monthly on day 1 at 02:00'],
    [schedule({ recurrence: 'yearly' as MaintenanceSchedule['recurrence'] }), 'At 02:00'],
  ])('%#: %s', (sched, text) => {
    expect(formatScheduleRecurrence(sched)).toBe(text);
  });
});

describe('MaintenanceWindowTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists active sessions with their mode, scope and actions', () => {
    const handlers = renderTab();

    expect(screen.getByText('Active Concurrent Sessions (2)')).toBeInTheDocument();
    expect(screen.getByText('Filtering Errors')).toBeInTheDocument();
    expect(screen.getByText('Silencing Alerts')).toBeInTheDocument();
    expect(screen.getByText('App: restic')).toBeInTheDocument();
    expect(screen.getByText('Host: nas01')).toBeInTheDocument();
    expect(screen.getByText('On-Demand Session')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Extend session On-Demand Session by one hour' }));
    expect(handlers.onExtendSession).toHaveBeenCalledWith('cccc2222dddd');
    fireEvent.click(screen.getAllByRole('button', { name: 'Stop Session' })[0]);
    expect(handlers.onDisableSession).toHaveBeenCalledWith('aaaa1111bbbb');
  });

  it('offers no Stop Session without a handler', () => {
    renderTab({ onDisableSession: undefined });
    expect(screen.queryByRole('button', { name: 'Stop Session' })).toBeNull();
  });

  it('shows each schedule with its recurrence, state and next run, and its actions', () => {
    const handlers = renderTab();

    expect(screen.getByText('Daily at 02:00')).toBeInTheDocument();
    expect(screen.getByText('Weekly on Tuesday at 02:00')).toBeInTheDocument();
    expect(screen.getByText('Monthly on day 15 at 02:00')).toBeInTheDocument();
    // An active schedule shows Active Now twice (name and status) and no on/off switch
    expect(screen.getAllByText('Active Now')).toHaveLength(2);
    // Only the enabled schedule that is not running shows its next run
    expect(screen.getAllByText(/^Next:/)).toHaveLength(1);

    const toggles = [...screen.getAllByTitle(/Click to (disable|enable)/)];
    expect(toggles.map((t) => t.getAttribute('title'))).toEqual(['Click to disable', 'Click to enable']);
    fireEvent.click(toggles[1]);
    expect(handlers.onToggleScheduleEnabled).toHaveBeenCalledWith(SCHEDULES[2]);

    fireEvent.click(screen.getByRole('button', { name: 'Edit Nightly backup' }));
    expect(handlers.onOpenEditScheduleModal).toHaveBeenCalledWith(SCHEDULES[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Delete Patch Tuesday' }));
    expect(handlers.onDeleteSchedule).toHaveBeenCalledWith(SCHEDULES[1]);
    fireEvent.click(screen.getByRole('button', { name: /Add Schedule/ }));
    expect(handlers.onOpenAddScheduleModal).toHaveBeenCalledTimes(1);
  });

  it('shows the empty schedule state with a create button', () => {
    const handlers = renderTab({ maintenance: { active: false, until: null, schedules: [], sessions: [] } });
    expect(screen.queryByText(/Active Concurrent Sessions/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Create First Schedule/ }));
    expect(handlers.onOpenAddScheduleModal).toHaveBeenCalledTimes(1);
  });

  it('starts a window from a preset or a custom end time, and clears an active one', () => {
    const handlers = renderTab({ isOnDemandActiveNow: false });
    expect(screen.getByText('Inactive')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set Window' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '4 Hours' }));
    expect(handlers.onSetPreset).toHaveBeenCalledWith(4);
    fireEvent.change(screen.getByLabelText(/Maintenance window end time/), { target: { value: '2026-11-02T18:00' } });
    expect(handlers.setCustomUntil).toHaveBeenCalledWith('2026-11-02T18:00');
  });

  it('an active on-demand window can be cleared, and a custom end time applied', () => {
    const handlers = renderTab({ isOnDemandActiveNow: true, customUntil: '2026-11-02T18:00' });
    expect(screen.getByText('Alert notifications are currently silenced')).toBeInTheDocument();
    expect(screen.getByText('Start a new window')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear All' }));
    expect(handlers.onClearMaintenance).toHaveBeenCalledTimes(1);
    const apply = screen.getByRole('button', { name: 'Set Window' });
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    expect(handlers.onApplyCustomWindow).toHaveBeenCalledTimes(1);
  });
});

describe('MaintenanceWindowTab API snippets', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it('use the configured app URL without its trailing slash', () => {
    renderTab({ appUrl: ' https://logs.example.com/ ' });
    expect(screen.getByText(/curl -X POST https:\/\/logs\.example\.com\/api\/v1\/maintenance\/enable/)).toBeInTheDocument();
    expect(screen.getByText(/curl -X POST https:\/\/logs\.example\.com\/api\/v1\/maintenance\/disable/)).toBeInTheDocument();
  });

  it('fall back to the page address when no app URL is set', () => {
    renderTab({ appUrl: '   ' });
    expect(screen.getByText(new RegExp(`curl -X POST ${window.location.origin}/api/v1/maintenance/enable`))).toBeInTheDocument();
  });

  it('copy a snippet and show Copied for a moment', async () => {
    vi.useFakeTimers();
    renderTab({ appUrl: 'https://logs.example.com' });
    const [startCopy, stopCopy] = screen.getAllByRole('button', { name: /Copy cURL/ });

    await act(async () => {
      fireEvent.click(stopCopy);
    });
    expect(copyToClipboard).toHaveBeenCalledWith(expect.stringContaining('https://logs.example.com/api/v1/maintenance/disable'));
    expect(screen.getAllByRole('button', { name: /Copied/ })).toHaveLength(1);
    expect(startCopy).toHaveTextContent('Copy cURL');

    await act(async () => {
      vi.advanceTimersByTime(2500);
    });
    expect(screen.queryByRole('button', { name: /Copied/ })).toBeNull();
  });

  it('keep showing Copy cURL when copying fails', async () => {
    vi.mocked(copyToClipboard).mockResolvedValueOnce(false);
    renderTab();
    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: /Copy cURL/ })[0]);
    });
    expect(screen.queryByRole('button', { name: /Copied/ })).toBeNull();
  });
});
