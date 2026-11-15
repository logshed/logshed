import React from 'react';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { LABEL_CLASS, SELECT_FIELD_CLASS } from '../common/Field.tsx';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';
import { MaintenanceLogHandling } from '../../types.ts';

/** What a maintenance window does and which logs it covers (no target: every log). */
export interface MaintenanceScope {
  logHandling: MaintenanceLogHandling;
  targetHost: string;
  targetApp: string;
}

export const EMPTY_MAINTENANCE_SCOPE: MaintenanceScope = { logHandling: 'silence_alerts', targetHost: '', targetApp: '' };

const MODE_OPTIONS: SelectOption[] = [
  { value: 'silence_alerts', label: 'Silence alerts' },
  { value: 'drop_errors', label: 'Silence alerts and drop error logs' },
];

export const hasMaintenanceTarget = (scope: { targetHost?: string | null; targetApp?: string | null }): boolean =>
  Boolean(scope.targetHost?.trim() || scope.targetApp?.trim());

/** True when the scope drops error logs from every source, which needs the confirmation below first. */
export const dropsEveryError = (scope: MaintenanceScope): boolean =>
  scope.logHandling === 'drop_errors' && !hasMaintenanceTarget(scope);

/** A short description of a window's scope, for the session and schedule rows. */
export function describeMaintenanceScope(window: {
  log_handling?: string | null;
  target_app?: string | null;
  target_host?: string | null;
}): string {
  const targets = [window.target_host && `host ${window.target_host}`, window.target_app && `app ${window.target_app}`]
    .filter(Boolean)
    .join(', ');
  if (window.log_handling === 'drop_errors') {
    return targets ? `Drops errors from ${targets}` : 'Drops errors from every source';
  }
  return targets ? `Silences ${targets}` : 'Silences every alert';
}

interface MaintenanceScopeFieldsProps {
  idPrefix: string;
  value: MaintenanceScope;
  onChange: (next: MaintenanceScope) => void;
  availableHosts?: string[];
  availableApps?: string[];
}

/** The mode and the optional host and app targets of a maintenance window. */
export const MaintenanceScopeFields: React.FC<MaintenanceScopeFieldsProps> = ({
  idPrefix,
  value,
  onChange,
  availableHosts = [],
  availableApps = [],
}) => {
  const targeted = hasMaintenanceTarget(value);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label id={`${idPrefix}-mode-label`} htmlFor={`${idPrefix}-mode`} className={LABEL_CLASS}>Mode</label>
          <SelectDropdown
            variant="panel"
            fieldClassName={SELECT_FIELD_CLASS}
            id={`${idPrefix}-mode`}
            labelledBy={`${idPrefix}-mode-label`}
            label="Mode"
            options={MODE_OPTIONS}
            value={value.logHandling}
            onChange={(v) => onChange({ ...value, logHandling: v as MaintenanceLogHandling })}
          />
        </div>
        <div>
          <span id={`${idPrefix}-host-label`} className={LABEL_CLASS}>Host (Optional)</span>
          <MultiSelectDropdown
            label="Host"
            placeholder="Every host"
            options={availableHosts}
            selected={value.targetHost.trim() ? [value.targetHost.trim()] : []}
            onChange={(next) => onChange({ ...value, targetHost: next[0] ?? '' })}
            allowCustomInput={true}
            singleSelect={true}
            variant="form"
            labelledBy={`${idPrefix}-host-label`}
          />
        </div>
        <div>
          <span id={`${idPrefix}-app-label`} className={LABEL_CLASS}>App (Optional)</span>
          <MultiSelectDropdown
            label="App"
            placeholder="Every app"
            options={availableApps}
            selected={value.targetApp.trim() ? [value.targetApp.trim()] : []}
            onChange={(next) => onChange({ ...value, targetApp: next[0] ?? '' })}
            allowCustomInput={true}
            singleSelect={true}
            variant="form"
            labelledBy={`${idPrefix}-app-label`}
          />
        </div>
      </div>
      <p className="text-[11px] text-fg-muted" data-testid={`${idPrefix}-scope-help`}>
        {targeted
          ? 'Logs from the target (a name, an IP or a pattern such as nas* or 192.168.1.*) are stored and shown but count towards no alert rule, so only its alerts are held back.' +
            (value.logHandling === 'drop_errors' ? ' Its error logs are discarded.' : '')
          : 'With no host or app, every alert notification is held back.' +
            (value.logHandling === 'drop_errors' ? ' Error logs from every source are discarded.' : '')}
      </p>
    </div>
  );
};

interface DropEveryErrorConfirmProps {
  isOpen: boolean;
  /** "For the next N minutes" for a session, or how a schedule repeats. */
  when: string;
  onCancel: () => void;
  onConfirm: () => void;
}

/** The confirmation before a window drops error logs from every source. */
export const DropEveryErrorConfirm: React.FC<DropEveryErrorConfirmProps> = ({ isOpen, when, onCancel, onConfirm }) => (
  <ConfirmDialog
    isOpen={isOpen}
    title="Drop error logs from every source?"
    onCancel={onCancel}
    onConfirm={() => onConfirm()}
    confirmLabel="Drop errors from every source"
    confirmVariant="danger"
    testId="drop-every-error-confirm"
  >
    <p>
      {when}, error, critical, alert and emergency logs from every host and app are discarded. They are not
      stored, not shown in the Console and not counted by alert rules, and cannot be recovered. Other logs are kept.
    </p>
  </ConfirmDialog>
);
