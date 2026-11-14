import { act, renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as alertsApi from '../api/alerts.ts';
import { useMaintenance } from '../components/alerts/useMaintenance.ts';
import { EMPTY_MAINTENANCE_SCOPE } from '../components/alerts/MaintenanceScopeFields.tsx';
import type { MaintenanceWindowResponse } from '../types.ts';

const STARTED: MaintenanceWindowResponse = { active: true, until: '2030-01-01T00:00:00Z', schedules: [] };

describe('useMaintenance mode and targets', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({ active: false, until: null, schedules: [] });
  });

  const setup = () =>
    renderHook(() => useMaintenance({ serverTimezone: 'UTC', onFeedback: vi.fn(), onHistoryChanged: vi.fn() }));

  it('sends the mode and targets, then starts the next session from the default again', async () => {
    const setSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue(STARTED);
    const { result } = setup();
    act(() => result.current.setScope({ logHandling: 'drop_errors', targetHost: ' nas ', targetApp: '' }));
    await act(async () => {
      await result.current.setPreset(1);
    });
    expect(setSpy).toHaveBeenCalledWith(expect.any(String), {
      log_handling: 'drop_errors',
      target_host: 'nas',
      target_app: null,
    });
    expect(result.current.scope).toEqual(EMPTY_MAINTENANCE_SCOPE);
  });

  it('waits for the confirmation before dropping errors from every source', async () => {
    const setSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue(STARTED);
    const { result } = setup();
    act(() => result.current.setScope({ logHandling: 'drop_errors', targetHost: '', targetApp: ' ' }));
    await act(async () => {
      await result.current.setPreset(1);
    });
    expect(setSpy).not.toHaveBeenCalled();
    expect(result.current.pendingUntil).not.toBeNull();

    await act(async () => {
      await result.current.confirmPendingWindow();
    });
    expect(setSpy).toHaveBeenCalledWith(expect.any(String), {
      log_handling: 'drop_errors',
      target_host: null,
      target_app: null,
    });
    expect(result.current.pendingUntil).toBeNull();
  });

  it('starts nothing when the confirmation is cancelled', async () => {
    const setSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue(STARTED);
    const { result } = setup();
    act(() => result.current.setScope({ logHandling: 'drop_errors', targetHost: '', targetApp: '' }));
    await act(async () => {
      await result.current.setPreset(4);
    });
    act(() => result.current.cancelPendingWindow());
    expect(result.current.pendingUntil).toBeNull();
    expect(setSpy).not.toHaveBeenCalled();
  });

  it('needs no confirmation to silence every alert', async () => {
    const setSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue(STARTED);
    const { result } = setup();
    await act(async () => {
      await result.current.setPreset(1);
    });
    expect(setSpy).toHaveBeenCalledWith(expect.any(String), {
      log_handling: 'silence_alerts',
      target_host: null,
      target_app: null,
    });
  });

  it('saves a schedule with its mode and targets', async () => {
    const saveSpy = vi.spyOn(alertsApi, 'updateMaintenanceSchedules').mockResolvedValue(STARTED);
    const { result } = setup();
    await act(async () => {
      await result.current.saveSchedule(
        {
          name: 'Backup', recurrence: 'daily', startTime: '03:00', durationMinutes: 30, dayOfWeek: 0, dayOfMonth: 1,
          enabled: true, scope: { logHandling: 'drop_errors', targetHost: '', targetApp: 'restic' },
        },
        null
      );
    });
    expect(saveSpy).toHaveBeenCalledWith([
      expect.objectContaining({ name: 'Backup', log_handling: 'drop_errors', target_app: 'restic', target_host: null }),
    ]);
  });
});
