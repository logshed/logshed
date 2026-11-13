import React, { useState } from 'react';
import { MaintenanceSchedule } from '../../types.ts';
import { Modal } from '../common/Modal.tsx';
import { SelectDropdown } from '../common/SelectDropdown.tsx';
import { DAYS_OF_WEEK } from '../../utils/maintenanceSchedule.ts';
import type { ScheduleFields } from './useMaintenance.ts';
import { buttonClass } from '../common/Button.tsx';
import { CHECKBOX_CLASS, controlClass, LABEL_CLASS, SELECT_FIELD_CLASS } from '../common/Field.tsx';

export interface ScheduleEditorModalProps {
  /** The schedule to edit, or null to add one. */
  scheduleToEdit: MaintenanceSchedule | null;
  onClose: () => void;
  /** Saves the schedule; the dialog closes when this resolves true. */
  onSave: (fields: ScheduleFields, scheduleToEdit: MaintenanceSchedule | null) => Promise<boolean>;
}

/** Adds or edits a recurring maintenance schedule. Mount it only while it is open. */
export const ScheduleEditorModal: React.FC<ScheduleEditorModalProps> = ({ scheduleToEdit, onClose, onSave }) => {
  const [scheduleFormName, setScheduleFormName] = useState<string>(scheduleToEdit?.name ?? '');
  const [scheduleFormRecurrence, setScheduleFormRecurrence] = useState<'daily' | 'weekly' | 'monthly'>(
    scheduleToEdit?.recurrence || 'weekly'
  );
  const [scheduleFormStartTime, setScheduleFormStartTime] = useState<string>(scheduleToEdit?.start_time || '02:00');
  const [scheduleFormDuration, setScheduleFormDuration] = useState<number>(scheduleToEdit?.duration_minutes || 60);
  const [scheduleFormDayOfWeek, setScheduleFormDayOfWeek] = useState<number>(scheduleToEdit?.day_of_week ?? 0);
  const [scheduleFormDayOfMonth, setScheduleFormDayOfMonth] = useState<number>(scheduleToEdit?.day_of_month ?? 1);
  const [scheduleFormEnabled, setScheduleFormEnabled] = useState<boolean>(scheduleToEdit?.enabled ?? true);

  const handleSaveSchedule = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const saved = await onSave(
      {
        name: scheduleFormName,
        recurrence: scheduleFormRecurrence,
        startTime: scheduleFormStartTime,
        durationMinutes: scheduleFormDuration,
        dayOfWeek: scheduleFormDayOfWeek,
        dayOfMonth: scheduleFormDayOfMonth,
        enabled: scheduleFormEnabled,
      },
      scheduleToEdit
    );
    if (saved) onClose();
  };

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={scheduleToEdit ? 'Edit Maintenance Schedule' : 'Add Maintenance Schedule'}
      maxWidth="max-w-md"
    >
      <form onSubmit={handleSaveSchedule} className="space-y-4 text-xs">
        {/* Name */}
        <div>
          <label className={LABEL_CLASS}>Schedule Name</label>
          <input
            type="text"
            required
            aria-label="Schedule Name"
            value={scheduleFormName}
            onChange={(e) => setScheduleFormName(e.target.value)}
            placeholder="e.g. Weekly Server Maintenance"
            className={controlClass()}
          />
        </div>

        {/* Recurrence Selection */}
        <div>
          <label className={LABEL_CLASS}>Recurrence</label>
          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={() => setScheduleFormRecurrence('daily')}
              className={`px-3 py-2 rounded-lg font-medium border text-center transition cursor-pointer ${
                scheduleFormRecurrence === 'daily'
                  ? 'bg-accent-700 text-on-fill border-accent-500'
                  : 'bg-canvas text-fg-muted border-line hover:text-fg'
              }`}
            >
              Daily
            </button>
            <button
              type="button"
              onClick={() => setScheduleFormRecurrence('weekly')}
              className={`px-3 py-2 rounded-lg font-medium border text-center transition cursor-pointer ${
                scheduleFormRecurrence === 'weekly'
                  ? 'bg-accent-700 text-on-fill border-accent-500'
                  : 'bg-canvas text-fg-muted border-line hover:text-fg'
              }`}
            >
              Weekly
            </button>
            <button
              type="button"
              onClick={() => setScheduleFormRecurrence('monthly')}
              className={`px-3 py-2 rounded-lg font-medium border text-center transition cursor-pointer ${
                scheduleFormRecurrence === 'monthly'
                  ? 'bg-accent-700 text-on-fill border-accent-500'
                  : 'bg-canvas text-fg-muted border-line hover:text-fg'
              }`}
            >
              Monthly
            </button>
          </div>
        </div>

        {/* Weekly Day of Week Picker */}
        {scheduleFormRecurrence === 'weekly' && (
          <div>
            <label id="schedule-day-of-week-label" htmlFor="schedule-day-of-week" className={LABEL_CLASS}>Day of the Week</label>
            <SelectDropdown
              variant="panel"
              fieldClassName={SELECT_FIELD_CLASS}
              id="schedule-day-of-week"
              labelledBy="schedule-day-of-week-label"
              label="Day of the Week"
              options={DAYS_OF_WEEK.map((d) => ({ value: String(d.value), label: d.label }))}
              value={String(scheduleFormDayOfWeek)}
              onChange={(v) => setScheduleFormDayOfWeek(Number(v))}
            />
          </div>
        )}

        {/* Monthly Day of Month Picker */}
        {scheduleFormRecurrence === 'monthly' && (
          <div>
            <label className={LABEL_CLASS}>Day of the Month (1 - 31)</label>
            <input
              type="number"
              min="1"
              max="31"
              aria-label="Day of the Month"
              value={scheduleFormDayOfMonth}
              onChange={(e) => setScheduleFormDayOfMonth(Math.max(1, Math.min(31, Number(e.target.value) || 1)))}
              className={controlClass()}
            />
          </div>
        )}

        {/* Start Time & Duration */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={LABEL_CLASS}>Start Time (24h)</label>
            <input
              type="time"
              required
              aria-label="Start Time"
              value={scheduleFormStartTime}
              onChange={(e) => setScheduleFormStartTime(e.target.value)}
              className={controlClass()}
            />
          </div>
          <div>
            <label className={LABEL_CLASS}>Duration (minutes)</label>
            <input
              type="number"
              required
              min="1"
              max="1440"
              aria-label="Duration in minutes"
              value={scheduleFormDuration}
              onChange={(e) => setScheduleFormDuration(Number(e.target.value))}
              className={controlClass()}
            />
          </div>
        </div>

        {/* Quick duration presets */}
        <div className="flex items-center gap-1.5 pt-1">
          <span className="text-[11px] text-fg-muted">Presets:</span>
          {[15, 30, 60, 120, 240].map((dur) => (
            <button
              key={dur}
              type="button"
              onClick={() => setScheduleFormDuration(dur)}
              className={`px-2 py-0.5 text-[11px] rounded border transition cursor-pointer ${
                scheduleFormDuration === dur
                  ? 'bg-accent-950 text-accent-300 border-accent-700'
                  : 'bg-canvas text-fg-muted border-line hover:text-fg'
              }`}
            >
              {dur >= 60 ? `${dur / 60}h` : `${dur}m`}
            </button>
          ))}
        </div>

        {/* Enabled Switch */}
        <div className="flex items-center gap-2 pt-2">
          <input
            type="checkbox"
            id="schedule-enabled-check"
            checked={scheduleFormEnabled}
            onChange={(e) => setScheduleFormEnabled(e.target.checked)}
            className={CHECKBOX_CLASS}
          />
          <label htmlFor="schedule-enabled-check" className="text-fg-soft select-none cursor-pointer">
            Enable this maintenance schedule
          </label>
        </div>

        {/* Modal Actions */}
        <div className="pt-3 border-t border-line flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-fg-soft hover:text-fg-max bg-raised transition cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="submit"
            className={buttonClass('primary', 'md')}
          >
            {scheduleToEdit ? 'Save Changes' : 'Create Schedule'}
          </button>
        </div>
      </form>
    </Modal>
  );
};
