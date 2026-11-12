import { useEffect, useState } from 'react';
import { MaintenanceSchedule, MaintenanceWindowResponse } from '../../types.ts';
import {
  fetchMaintenanceWindow,
  setMaintenanceWindow,
  updateMaintenanceSchedules,
  deleteMaintenanceSession,
  extendMaintenanceSession,
} from '../../api/alerts.ts';
import { formatMaintenanceTime, fromLocalDatetimeInputString } from '../../utils/formatters.ts';

// The longest delay setTimeout honours (2^31-1 ms, about 24.8 days)
const MAX_TIMEOUT_MS = 2147483647;

export type Feedback = { text: string; isError: boolean };

/** The fields of the schedule editor. */
export interface ScheduleFields {
  name: string;
  recurrence: 'daily' | 'weekly' | 'monthly';
  startTime: string;
  durationMinutes: number;
  dayOfWeek: number;
  dayOfMonth: number;
  enabled: boolean;
}

/** Tells the Navbar and any other listener about the new maintenance state. */
const announce = (res: MaintenanceWindowResponse) =>
  window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));

/**
 * The maintenance state on Rules & History: on-demand sessions and recurring schedules, kept in
 * step with the Navbar's poll through the 'maintenance-updated' event, and refreshed the second a
 * window ends. Results and errors go to onFeedback; a change to the sessions, which records
 * maintenance history, calls onHistoryChanged.
 */
export function useMaintenance({
  serverTimezone,
  onFeedback,
  onHistoryChanged,
}: {
  serverTimezone?: string;
  onFeedback: (feedback: Feedback) => void;
  onHistoryChanged: () => void;
}) {
  const [maintenance, setMaintenance] = useState<MaintenanceWindowResponse>({
    active: false,
    until: null,
    schedules: [],
  });
  const [customUntil, setCustomUntil] = useState<string>('');

  const refreshMaintenance = () => {
    fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
  };

  // Real-time timer to refresh state the exact second a window expires
  useEffect(() => {
    if (!maintenance.until) return;
    const untilMs = new Date(maintenance.until).getTime();
    if (isNaN(untilMs)) return;
    const nowMs = Date.now();
    const delay = untilMs - nowMs;
    if (delay <= 0) {
      fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
      return;
    }
    // setTimeout runs a longer delay at once; the 10 second maintenance poll covers such a window
    if (delay + 250 > MAX_TIMEOUT_MS) return;
    const timer = setTimeout(() => {
      fetchMaintenanceWindow()
        .then((res) => {
          setMaintenance(res);
          announce(res);
        })
        .catch(() => {});
    }, delay + 250);
    return () => clearTimeout(timer);
  }, [maintenance.until]);

  useEffect(() => {
    const onMaintenanceUpdated = (e: Event) => {
      const customEvent = e as CustomEvent<MaintenanceWindowResponse>;
      if (customEvent.detail) {
        setMaintenance(customEvent.detail);
      } else {
        fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
      }
    };
    window.addEventListener('maintenance-updated', onMaintenanceUpdated);
    return () => window.removeEventListener('maintenance-updated', onMaintenanceUpdated);
  }, []);

  const isMaintenanceActiveNow = Boolean(maintenance.active);
  const isOnDemandActiveNow = Boolean(
    maintenance.on_demand_until || (maintenance.sessions && maintenance.sessions.length > 0)
  );

  const setWindow = async (untilIso: string) => {
    try {
      const res = await setMaintenanceWindow(untilIso);
      setMaintenance(res);
      setCustomUntil('');
      announce(res);
      onHistoryChanged();
      onFeedback({
        text: `Maintenance window active until ${formatMaintenanceTime(res.until, serverTimezone)}.`,
        isError: false,
      });
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to set maintenance window.',
        isError: true,
      });
    }
  };

  const clearAllSessions = async () => {
    try {
      const res = await setMaintenanceWindow(null);
      setMaintenance(res);
      announce(res);
      onHistoryChanged();
      onFeedback({
        text: 'All maintenance sessions cleared.',
        isError: false,
      });
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to clear maintenance sessions.',
        isError: true,
      });
    }
  };

  const disableSession = async (sessionId: string) => {
    try {
      const res = await deleteMaintenanceSession(sessionId);
      setMaintenance(res);
      announce(res);
      onHistoryChanged();
      onFeedback({
        text: 'Maintenance session terminated.',
        isError: false,
      });
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to terminate maintenance session.',
        isError: true,
      });
    }
  };

  const setPreset = async (hours: number) => {
    const targetDate = new Date(Date.now() + hours * 3600 * 1000);
    await setWindow(targetDate.toISOString());
  };

  /** Moves one session's end an hour later (from its end, or from now when that is later). */
  const extendSession = async (sessionId: string) => {
    try {
      const res = await extendMaintenanceSession(sessionId, 60);
      setMaintenance(res);
      announce(res);
      const extended = res.sessions?.find((s) => s.session_id === sessionId);
      onFeedback({
        text: extended
          ? `Session extended until ${formatMaintenanceTime(extended.expires_at, serverTimezone)}.`
          : 'Session extended.',
        isError: false,
      });
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to extend maintenance session.',
        isError: true,
      });
    }
  };

  const applyCustomWindow = async () => {
    if (!customUntil) return;
    // The picker shows the server clock, as the window's times are shown
    const iso = fromLocalDatetimeInputString(customUntil, serverTimezone);
    if (!iso) {
      onFeedback({ text: 'Please enter a valid future date and time.', isError: true });
      return;
    }
    await setWindow(iso);
  };

  /** Adds a schedule, or replaces scheduleToEdit; resolves true when it was saved. */
  const saveSchedule = async (fields: ScheduleFields, scheduleToEdit: MaintenanceSchedule | null): Promise<boolean> => {
    const cleanName = fields.name.trim();
    if (!cleanName) {
      onFeedback({ text: 'Schedule name is required.', isError: true });
      return false;
    }
    const currentList = maintenance.schedules || [];
    let updatedList: MaintenanceSchedule[];

    if (scheduleToEdit) {
      updatedList = currentList.map((s) =>
        s.id === scheduleToEdit.id
          ? {
              ...s,
              name: cleanName,
              recurrence: fields.recurrence,
              start_time: fields.startTime,
              duration_minutes: Number(fields.durationMinutes) || 60,
              day_of_week: fields.recurrence === 'weekly' ? fields.dayOfWeek : undefined,
              day_of_month: fields.recurrence === 'monthly' ? fields.dayOfMonth : undefined,
              enabled: fields.enabled,
            }
          : s
      );
    } else {
      const newSched: MaintenanceSchedule = {
        id: `sched_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
        name: cleanName,
        recurrence: fields.recurrence,
        start_time: fields.startTime,
        duration_minutes: Number(fields.durationMinutes) || 60,
        day_of_week: fields.recurrence === 'weekly' ? fields.dayOfWeek : undefined,
        day_of_month: fields.recurrence === 'monthly' ? fields.dayOfMonth : undefined,
        enabled: fields.enabled,
      };
      updatedList = [...currentList, newSched];
    }

    try {
      const res = await updateMaintenanceSchedules(updatedList);
      setMaintenance(res);
      announce(res);
      onFeedback({
        text: `Schedule "${cleanName}" ${scheduleToEdit ? 'updated' : 'added'} successfully.`,
        isError: false,
      });
      return true;
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to save maintenance schedule.',
        isError: true,
      });
      return false;
    }
  };

  const toggleScheduleEnabled = async (sched: MaintenanceSchedule) => {
    const currentList = maintenance.schedules || [];
    const updatedList = currentList.map((s) =>
      s.id === sched.id ? { ...s, enabled: !s.enabled } : s
    );
    try {
      const res = await updateMaintenanceSchedules(updatedList);
      setMaintenance(res);
      announce(res);
      onFeedback({
        text: `Schedule "${sched.name}" ${!sched.enabled ? 'enabled' : 'disabled'}.`,
        isError: false,
      });
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to update schedule status.',
        isError: true,
      });
    }
  };

  /** Removes a schedule; resolves true when it was removed. */
  const deleteSchedule = async (sched: MaintenanceSchedule): Promise<boolean> => {
    const currentList = maintenance.schedules || [];
    const updatedList = currentList.filter((s) => s.id !== sched.id);
    try {
      const res = await updateMaintenanceSchedules(updatedList);
      setMaintenance(res);
      announce(res);
      onFeedback({
        text: `Schedule "${sched.name}" removed.`,
        isError: false,
      });
      return true;
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to delete maintenance schedule.',
        isError: true,
      });
      return false;
    }
  };

  return {
    maintenance,
    setMaintenance,
    refreshMaintenance,
    customUntil,
    setCustomUntil,
    isMaintenanceActiveNow,
    isOnDemandActiveNow,
    setPreset,
    applyCustomWindow,
    clearAllSessions,
    disableSession,
    extendSession,
    saveSchedule,
    toggleScheduleEnabled,
    deleteSchedule,
  };
}
