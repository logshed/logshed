import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MaintenanceWindowTab, formatScheduleRecurrence } from '../components/alerts/tabs/MaintenanceWindowTab.tsx';
import type { MaintenanceSchedule, MaintenanceWindowResponse } from '../types.ts';
import { copyToClipboard } from '../utils/clipboard.ts';
import { EMPTY_MAINTENANCE_SCOPE } from '../components/alerts/MaintenanceScopeFields.tsx';
import { ScheduleEditorModal } from '../components/alerts/ScheduleEditorModal.tsx';
import { chooseOption } from './selectDropdownHelpers.ts';

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
    onSaveSchedule: vi.fn().mockResolvedValue(true),
    onToggleScheduleEnabled: vi.fn(),
    onDeleteSchedule: vi.fn(),
    onDisableSession: vi.fn(),
    onScopeChange: vi.fn(),
    onConfirmPending: vi.fn(),
    onCancelPending: vi.fn(),
  };
  render(
    <MaintenanceWindowTab
      maintenance={MAINTENANCE}
      isOnDemandActiveNow={false}
      customUntil=""
      scope={EMPTY_MAINTENANCE_SCOPE}
      {...handlers}
      {...overrides}
    />
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

    fireEvent.click(screen.getByRole('button', { name: '+1h: extend session On-Demand Session by one hour' }));
    expect(handlers.onExtendSession).toHaveBeenCalledWith('cccc2222dddd');
    fireEvent.click(screen.getAllByRole('button', { name: 'Stop Session' })[0]);
    expect(handlers.onDisableSession).toHaveBeenCalledWith('aaaa1111bbbb');
  });

  it('lets a long schedule name and session reason with no spaces wrap instead of running past the card', () => {
    const name = 'Nightly_backup_window_for_my_very_long_container_name_on_the_storage_host';
    const reason = 'Deploying_upgrade_for_my_very_long_container_name_on_the_storage_host';
    renderTab({
      maintenance: {
        ...MAINTENANCE,
        schedules: [schedule({ id: 'long', name })],
        sessions: [{ ...MAINTENANCE.sessions![0], reason }],
      },
    });
    for (const text of [name, reason]) {
      const el = screen.getByText(text);
      expect(el.className).toContain('wrap-anywhere');
      expect(el.className).toContain('min-w-0');
    }
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
    expect(screen.getByRole('dialog', { name: 'Edit Maintenance Schedule' })).toBeInTheDocument();
    expect(screen.getByLabelText('Schedule Name')).toHaveValue('Nightly backup');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Delete Patch Tuesday' }));
    expect(handlers.onDeleteSchedule).toHaveBeenCalledWith(SCHEDULES[1]);
    fireEvent.click(screen.getByRole('button', { name: /Add Schedule/ }));
    expect(screen.getByRole('dialog', { name: 'Add Maintenance Schedule' })).toBeInTheDocument();
    expect(screen.getByLabelText('Schedule Name')).toHaveValue('');
  });

  it('shows the empty schedule state with a create button', () => {
    renderTab({ maintenance: { active: false, until: null, schedules: [], sessions: [] } });
    expect(screen.queryByText(/Active Concurrent Sessions/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Create First Schedule/ }));
    expect(screen.getByRole('dialog', { name: 'Add Maintenance Schedule' })).toBeInTheDocument();
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

describe('MaintenanceWindowTab mode and targets', () => {
  it('changes the mode and targets of the next session', () => {
    const handlers = renderTab({ availableHosts: ['nas01'], availableApps: ['restic'] });
    chooseOption(screen.getByLabelText('Mode'), 'Silence alerts and drop error logs');
    expect(handlers.onScopeChange).toHaveBeenLastCalledWith({ ...EMPTY_MAINTENANCE_SCOPE, logHandling: 'drop_errors' });
    // Picked from the list: the list closes
    fireEvent.click(screen.getByRole('button', { name: /^Host \(Optional\)/ }));
    fireEvent.click(screen.getByRole('button', { name: /^nas01/, pressed: false }));
    expect(handlers.onScopeChange).toHaveBeenLastCalledWith({ ...EMPTY_MAINTENANCE_SCOPE, targetHost: 'nas01' });
    expect(screen.queryByPlaceholderText('Search or add host...')).toBeNull();
    // Typed as a pattern
    fireEvent.click(screen.getByRole('button', { name: /^App \(Optional\)/ }));
    const search = screen.getByPlaceholderText('Search or add app...');
    fireEvent.change(search, { target: { value: 'rest*' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(handlers.onScopeChange).toHaveBeenLastCalledWith({ ...EMPTY_MAINTENANCE_SCOPE, targetApp: 'rest*' });
  });

  it('picks a listed host when its name is typed and Enter pressed', () => {
    const handlers = renderTab({ availableHosts: ['nas01', 'router'] });
    fireEvent.click(screen.getByRole('button', { name: /^Host \(Optional\)/ }));
    const search = screen.getByPlaceholderText('Search or add host...');
    fireEvent.change(search, { target: { value: 'ROUTER' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(handlers.onScopeChange).toHaveBeenLastCalledWith({ ...EMPTY_MAINTENANCE_SCOPE, targetHost: 'router' });
    expect(screen.queryByPlaceholderText('Search or add host...')).toBeNull();
  });

  it('replaces the chosen host rather than adding a second one', () => {
    const handlers = renderTab({
      availableHosts: ['nas01', 'router'],
      scope: { ...EMPTY_MAINTENANCE_SCOPE, targetHost: 'nas01' },
    });
    const trigger = screen.getByRole('button', { name: /^Host \(Optional\) nas01/ });
    fireEvent.click(trigger);
    expect(screen.queryByRole('button', { name: 'Select All' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^router/, pressed: false }));
    expect(handlers.onScopeChange).toHaveBeenLastCalledWith({ ...EMPTY_MAINTENANCE_SCOPE, targetHost: 'router' });
  });

  it('says what a window without a target does, and what one with a target does', () => {
    renderTab();
    expect(screen.getByTestId('on-demand-scope-help')).toHaveTextContent('With no host or app, every alert notification is held back.');
  });

  it('says a target only holds back its own alerts', () => {
    renderTab({ scope: { logHandling: 'drop_errors', targetHost: 'nas01', targetApp: '' } });
    expect(screen.getByTestId('on-demand-scope-help')).toHaveTextContent('count towards no alert rule');
    expect(screen.getByTestId('on-demand-scope-help')).toHaveTextContent('Its error logs are discarded.');
  });

  it('asks before a session drops error logs from every source', () => {
    const until = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    const handlers = renderTab({ pendingUntil: until });
    const dialog = screen.getByTestId('drop-every-error-confirm');
    expect(screen.getByRole('heading', { name: 'Drop error logs from every source?' })).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      'For the next 60 minutes, error, critical, alert and emergency logs from every host and app are discarded. ' +
        'They are not stored, not shown in the Console and not counted by alert rules, and cannot be recovered. Other logs are kept.'
    );
    fireEvent.click(screen.getByRole('button', { name: 'Drop errors from every source' }));
    expect(handlers.onConfirmPending).toHaveBeenCalled();
  });

  it('cancels the pending session', () => {
    const handlers = renderTab({ pendingUntil: new Date(Date.now() + 600000).toISOString() });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(handlers.onCancelPending).toHaveBeenCalled();
  });

  it('shows each schedule\'s scope, and marks one that drops errors from every source', () => {
    renderTab({
      maintenance: {
        ...MAINTENANCE,
        schedules: [
          schedule({ id: 'a', name: 'Backup', log_handling: 'drop_errors', target_app: 'restic' }),
          schedule({ id: 'b', name: 'Patch night', log_handling: 'drop_errors' }),
          schedule({ id: 'c', name: 'Old one' }),
        ],
      },
    });
    const scopes = screen.getAllByTestId('schedule-scope').map((el) => el.textContent);
    expect(scopes).toEqual(['Drops errors from app restic', 'Drops errors from every source', 'Silences every alert']);
    expect(screen.getAllByTestId('schedule-scope')[1].className).toContain('text-warning-300');
  });

  it('says only the targets are held back when no running window covers every source', () => {
    renderTab({
      isOnDemandActiveNow: true,
      maintenance: { ...MAINTENANCE, silences_all_alerts: false, sessions: [MAINTENANCE.sessions![0]] },
    });
    expect(screen.getByText('Logs from the targets below count towards no alert rule')).toBeInTheDocument();
    expect(screen.queryByText('All sources')).toBeNull();
  });

  it('marks a session without a target as covering all sources', () => {
    renderTab({ isOnDemandActiveNow: true, maintenance: { ...MAINTENANCE, silences_all_alerts: true } });
    expect(screen.getByText('Alert notifications are currently silenced')).toBeInTheDocument();
    expect(screen.getByText('All sources')).toBeInTheDocument();
  });
});

describe('ScheduleEditorModal mode and targets', () => {
  const fill = () => {
    fireEvent.change(screen.getByLabelText('Schedule Name'), { target: { value: 'Backup' } });
  };

  it('saves the mode and targets with the schedule', async () => {
    const onSave = vi.fn().mockResolvedValue(true);
    render(<ScheduleEditorModal scheduleToEdit={null} onClose={vi.fn()} onSave={onSave} />);
    fill();
    chooseOption(screen.getByLabelText('Mode'), 'Silence alerts and drop error logs');
    fireEvent.click(screen.getByRole('button', { name: /^App \(Optional\)/ }));
    const search = screen.getByPlaceholderText('Search or add app...');
    fireEvent.change(search, { target: { value: 'restic' } });
    fireEvent.keyDown(search, { key: 'Enter' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Create Schedule' }));
    });
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ scope: { logHandling: 'drop_errors', targetHost: '', targetApp: 'restic' } }),
      null
    );
  });

  it('asks before saving a schedule that drops errors from every source', async () => {
    const onSave = vi.fn().mockResolvedValue(true);
    render(<ScheduleEditorModal scheduleToEdit={null} onClose={vi.fn()} onSave={onSave} />);
    fill();
    chooseOption(screen.getByLabelText('Mode'), 'Silence alerts and drop error logs');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Create Schedule' }));
    });
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId('drop-every-error-confirm')).toHaveTextContent(
      'Each time this schedule runs (Weekly on Sunday at 02:00), for 60 minutes, error, critical, alert and emergency logs'
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Drop errors from every source' }));
    });
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ scope: { logHandling: 'drop_errors', targetHost: '', targetApp: '' } }), null);
  });

  it('opens a schedule with the mode and targets it has', () => {
    render(
      <ScheduleEditorModal
        scheduleToEdit={schedule({ name: 'Backup', log_handling: 'drop_errors', target_host: 'nas01' })}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />
    );
    expect(screen.getByRole('button', { name: /^Host \(Optional\) nas01/ })).toBeInTheDocument();
    expect(screen.getByTestId('schedule-scope-help')).toHaveTextContent('Its error logs are discarded.');
  });
});

describe('MaintenanceWindowTab extend button name', () => {
  it('starts with the text it shows, so voice control can reach it by saying it', () => {
    renderTab({ isOnDemandActiveNow: true });
    const button = screen.getByRole('button', { name: '+1h: extend session Nightly backup by one hour' });
    expect(button).toHaveTextContent('+1h');
  });
});
