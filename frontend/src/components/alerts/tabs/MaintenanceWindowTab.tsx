import React from 'react';
import {
  Zap,
  Clock,
  Plus,
  Edit2,
  Trash2,
} from 'lucide-react';
import { MaintenanceSchedule, MaintenanceWindowResponse } from '../../../types.ts';
import { formatMaintenanceTime, toLocalDatetimeInputString } from '../../../utils/formatters.ts';

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

export interface MaintenanceWindowTabProps {
  maintenance: MaintenanceWindowResponse;
  isOnDemandActiveNow: boolean;
  customUntil: string;
  setCustomUntil: (val: string) => void;
  onClearMaintenance: () => void;
  onSetPreset: (hours: number) => void;
  onApplyCustomWindow: () => void;
  onOpenAddScheduleModal: () => void;
  onOpenEditScheduleModal: (sched: MaintenanceSchedule) => void;
  onToggleScheduleEnabled: (sched: MaintenanceSchedule) => void;
  onDeleteSchedule: (sched: MaintenanceSchedule) => void;
}

export const MaintenanceWindowTab: React.FC<MaintenanceWindowTabProps> = ({
  maintenance,
  isOnDemandActiveNow,
  customUntil,
  setCustomUntil,
  onClearMaintenance,
  onSetPreset,
  onApplyCustomWindow,
  onOpenAddScheduleModal,
  onOpenEditScheduleModal,
  onToggleScheduleEnabled,
  onDeleteSchedule,
}) => {
  return (
    <div className="space-y-6">
      {/* Card 1: On-Demand Maintenance */}
      <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
        <div className="px-5 py-4 border-b border-dark-700 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Zap className="w-4 h-4 text-accent-500" />
              <span>On-Demand Maintenance</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Instantly silence external notifications for ad-hoc maintenance or testing without changing alert rules.
            </p>
          </div>
          <div>
            {isOnDemandActiveNow ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
                On-Demand Active
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium bg-dark-800 text-slate-400 border border-dark-700">
                <span className="w-2 h-2 rounded-full bg-slate-600"></span>
                On-Demand Inactive
              </span>
            )}
          </div>
        </div>

        <div className="p-5 space-y-4">
          {isOnDemandActiveNow ? (
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-lg bg-amber-950/20 border border-amber-800/40">
              <div>
                <p className="text-xs font-medium text-amber-200">
                  Alert notifications are currently silenced
                </p>
                <p className="text-xs text-slate-300 mt-0.5">
                  Active until <span className="font-semibold text-white">{formatMaintenanceTime(maintenance.on_demand_until)}</span>
                </p>
              </div>
              <button
                type="button"
                onClick={onClearMaintenance}
                className="px-4 py-2 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-amber-300 hover:text-white border border-dark-600 rounded-lg transition cursor-pointer self-start sm:self-auto"
              >
                Clear On-Demand Window
              </button>
            </div>
          ) : (
            <p className="text-xs text-slate-300">
              Select a preset duration or set a specific end time to immediately silence outgoing alert notifications.
            </p>
          )}

          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pt-2">
            {/* Quick Presets */}
            <div className="space-y-1.5">
              <span className="text-xs font-medium text-slate-400">Quick Duration Presets:</span>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => onSetPreset(1)}
                  className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                >
                  +1 Hour
                </button>
                <button
                  type="button"
                  onClick={() => onSetPreset(4)}
                  className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                >
                  +4 Hours
                </button>
                <button
                  type="button"
                  onClick={() => onSetPreset(8)}
                  className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                >
                  +8 Hours
                </button>
                <button
                  type="button"
                  onClick={() => onSetPreset(24)}
                  className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                >
                  +24 Hours
                </button>
              </div>
            </div>

            {/* Custom Time */}
            <div className="space-y-1.5">
              <span className="text-xs font-medium text-slate-400">Specific End Time:</span>
              <div className="flex items-center gap-2">
                <input
                  type="datetime-local"
                  aria-label="Maintenance window end time"
                  value={customUntil}
                  onChange={(e) => setCustomUntil(e.target.value)}
                  min={toLocalDatetimeInputString(new Date().toISOString())}
                  className="bg-dark-950 border border-dark-700 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-accent-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={onApplyCustomWindow}
                  disabled={!customUntil}
                  className="px-3.5 py-1.5 text-xs font-medium bg-accent-600 hover:bg-accent-500 text-white rounded-lg transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
                >
                  Set Window
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Card 2: Recurring Scheduled Windows */}
      <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
        <div className="px-5 py-4 border-b border-dark-700 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Clock className="w-4 h-4 text-accent-500" />
              <span>Scheduled Maintenance Windows</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Define daily, weekly, or monthly recurring windows to automatically silence alerts during planned maintenance.
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenAddScheduleModal}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-dark-950 bg-accent-500 hover:bg-accent-400 transition cursor-pointer self-start sm:self-auto shrink-0"
          >
            <Plus className="w-4 h-4" />
            <span>Add Schedule</span>
          </button>
        </div>

        <div className="p-0">
          {(!maintenance.schedules || maintenance.schedules.length === 0) ? (
            <div className="p-8 text-center text-xs text-slate-400">
              <Clock className="w-8 h-8 mx-auto text-slate-600 mb-2" />
              <p className="font-medium text-slate-300">No scheduled windows configured</p>
              <p className="text-slate-500 mt-1 max-w-sm mx-auto">
                Add recurring daily, weekly, or monthly schedules to automatically suppress notifications during routine maintenance.
              </p>
              <button
                type="button"
                onClick={onOpenAddScheduleModal}
                className="mt-4 inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 border border-dark-700 rounded-lg transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Create First Schedule</span>
              </button>
            </div>
          ) : (
            <div className="divide-y divide-dark-800 overflow-x-auto">
              {/* Table Header */}
              <div className="grid grid-cols-[1.2fr_1.5fr_90px_100px_70px] gap-x-4 min-w-[640px] px-5 py-2.5 bg-dark-950/40 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                <div>Schedule Name</div>
                <div>Recurrence</div>
                <div>Duration</div>
                <div>Status</div>
                <div className="text-right">Actions</div>
              </div>

              {maintenance.schedules.map((sched) => (
                <div
                  key={sched.id}
                  className="grid grid-cols-[1.2fr_1.5fr_90px_100px_70px] gap-x-4 min-w-[640px] px-5 py-3 items-center hover:bg-dark-850/50 transition text-xs"
                >
                  <div className="pr-2 min-w-0">
                    <div className="font-medium text-slate-200 truncate">{sched.name}</div>
                    {sched.next_run && sched.enabled && !sched.is_active && (
                      <div className="text-[11px] text-slate-500 mt-0.5">
                        Next: {formatMaintenanceTime(sched.next_run)}
                      </div>
                    )}
                  </div>

                  <div className="text-slate-300 text-xs min-w-0 truncate">
                    {formatScheduleRecurrence(sched)}
                  </div>

                  <div className="text-slate-300 font-mono text-xs whitespace-nowrap">
                    {sched.duration_minutes} min
                  </div>

                  <div>
                    {sched.is_active ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse">
                        Active Now
                      </span>
                    ) : sched.enabled ? (
                      <button
                        type="button"
                        onClick={() => onToggleScheduleEnabled(sched)}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-950/60 text-emerald-400 border border-emerald-800/60 hover:bg-emerald-900/60 transition cursor-pointer"
                        title="Click to disable"
                      >
                        Enabled
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onToggleScheduleEnabled(sched)}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-dark-800 text-slate-400 border border-dark-700 hover:text-slate-300 transition cursor-pointer"
                        title="Click to enable"
                      >
                        Disabled
                      </button>
                    )}
                  </div>

                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      type="button"
                      onClick={() => onOpenEditScheduleModal(sched)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Edit schedule"
                      aria-label={`Edit ${sched.name}`}
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeleteSchedule(sched)}
                      className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-950/40 rounded transition cursor-pointer"
                      title="Delete schedule"
                      aria-label={`Delete ${sched.name}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MaintenanceWindowTab;
