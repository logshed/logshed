import { MaintenanceSchedule } from '../types.ts';

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
