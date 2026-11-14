import { MaintenanceSchedule, MaintenanceWindowResponse } from '../types.ts';

export const DAYS_OF_WEEK = [
  { value: 0, label: 'Sunday' },
  { value: 1, label: 'Monday' },
  { value: 2, label: 'Tuesday' },
  { value: 3, label: 'Wednesday' },
  { value: 4, label: 'Thursday' },
  { value: 5, label: 'Friday' },
  { value: 6, label: 'Saturday' },
];

export const formatScheduleRecurrence = (s: MaintenanceSchedule): string => {
  if (s.recurrence === 'daily') {
    return `Daily at ${s.start_time}`;
  }
  if (s.recurrence === 'weekly') {
    const day = DAYS_OF_WEEK.find((d) => d.value === (s.day_of_week ?? 0))?.label || 'Sunday';
    return `Weekly on ${day} at ${s.start_time}`;
  }
  if (s.recurrence === 'monthly') {
    const day = s.day_of_month ?? 1;
    return `Monthly on day ${day} at ${s.start_time}`;
  }
  return `At ${s.start_time}`;
};

/** One window's target in words ("host nas, app restic"), or '' for a window without a target. */
export const describeTarget = (w: { target_app?: string | null; target_host?: string | null }): string =>
  [w.target_host && `host ${w.target_host}`, w.target_app && `app ${w.target_app}`].filter(Boolean).join(', ');

/**
 * The targets of the running windows (sessions and schedules) when none of them covers every source
 * (silences_all_alerts false), joined with "; ", or null when a window holds back every alert.
 */
export function runningTargets(maintenance: MaintenanceWindowResponse): string | null {
  if (maintenance.silences_all_alerts !== false) return null;
  const windows = [...(maintenance.sessions || []), ...(maintenance.schedules || []).filter((s) => s.is_active)];
  const targets = [...new Set(windows.map(describeTarget).filter(Boolean))];
  return targets.length ? targets.join('; ') : null;
}
