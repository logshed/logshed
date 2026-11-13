import React from 'react';
import {
  Bell,
  AlertTriangle,
  Zap,
  Download,
  Upload,
  Plus,
  Sparkles,
  FlaskConical,
  Edit2,
  Trash2,
  Shield,
  ExternalLink,
} from 'lucide-react';
import { AlertRule, NotificationChannel, MaintenanceWindowResponse } from '../../../types.ts';
import { formatLocalTimestamp, formatMaintenanceTime } from '../../../utils/formatters.ts';
import { useServerTimezone } from '../../../utils/hooks.ts';
import { useReorderableList } from '../../../utils/useReorderableList.ts';
import { RuleOrderControls } from '../RuleOrderControls.tsx';

export interface AlertRulesTabProps {
  rules: AlertRule[];
  channels: NotificationChannel[];
  isMaintenanceActiveNow: boolean;
  maintenance: MaintenanceWindowResponse;
  onClearMaintenance: () => void;
  onNavigateToMaintenance: () => void;
  onOpenPresetsModal: () => void;
  onExportAll: () => void;
  onFileImport: (e: React.ChangeEvent<HTMLInputElement>) => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onOpenCreateModal: () => void;
  onToggleRule: (rule: AlertRule) => void;
  onOpenTestModal: (rule: AlertRule) => void;
  onExportSingle: (rule: AlertRule) => void;
  onOpenEditModal: (rule: AlertRule) => void;
  onDeleteRule: (rule: AlertRule) => void;
  onNavigateToSettings: () => void;
  onReorderRules?: (rules: AlertRule[]) => void;
  getChannelStatus: (channelId?: number | null) => {
    name: string;
    warning: string | null;
    isInvalid: boolean;
  };
}

export const AlertRulesTab: React.FC<AlertRulesTabProps> = ({
  rules,
  channels,
  isMaintenanceActiveNow,
  maintenance,
  onClearMaintenance,
  onNavigateToMaintenance,
  onOpenPresetsModal,
  onExportAll,
  onFileImport,
  fileInputRef,
  onOpenCreateModal,
  onToggleRule,
  onOpenTestModal,
  onExportSingle,
  onOpenEditModal,
  onDeleteRule,
  onNavigateToSettings,
  onReorderRules,
  getChannelStatus,
}) => {
  const { draggedIndex, moveItem, dragProps } = useReorderableList(rules, (newOrder) => onReorderRules?.(newOrder));
  const serverTimezone = useServerTimezone();

  return (
    <div className="space-y-4">
      {/* Active Maintenance Banner on Rules Tab */}
      {isMaintenanceActiveNow && (
        <div className="bg-warning-950/40 border border-warning-800/60 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs shadow-xs">
          <div className="flex items-start sm:items-center gap-3">
            <span className="p-2 rounded-lg bg-warning-500/20 text-warning-400 shrink-0">
              <AlertTriangle className="w-4 h-4 animate-pulse" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-warning-200">Maintenance Window Active</span>
                {maintenance.schedule_name ? (
                  <span className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-warning-900/60 text-warning-300 border border-warning-700/60">
                    {maintenance.schedule_name}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded-sm text-[10px] font-medium bg-warning-900/60 text-warning-300 border border-warning-700/60">
                    On-Demand
                  </span>
                )}
              </div>
              <p className="text-fg-soft mt-0.5">
                Alert notifications are silenced
                {maintenance.until ? (
                  <> until <span className="font-semibold text-fg-max">{formatMaintenanceTime(maintenance.until, serverTimezone)}</span></>
                ) : null}. Rule evaluation and incident recording continue normally.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 self-end sm:self-auto">
            {(maintenance.on_demand_until || (maintenance.sessions && maintenance.sessions.length > 0)) && (
              <button
                type="button"
                onClick={onClearMaintenance}
                className="px-3 py-1.5 text-xs font-medium bg-raised hover:bg-raised-strong text-warning-300 hover:text-fg-max border border-line-strong rounded-lg transition cursor-pointer"
              >
                Clear
              </button>
            )}
            <button
              type="button"
              onClick={onNavigateToMaintenance}
              className="px-3 py-1.5 text-xs font-semibold bg-warning-500 hover:bg-warning-400 text-on-warning rounded-lg transition cursor-pointer"
            >
              Manage Maintenance
            </button>
          </div>
        </div>
      )}

      <div className="bg-surface border border-line rounded-xl overflow-hidden shadow-xs">
        <div className="px-5 py-4 border-b border-line flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-2">
              <Bell className="w-4 h-4 text-accent-500" />
              <span>Configured Alert Rules</span>
            </h3>
            <p className="text-xs text-fg-muted mt-0.5">
              Rules evaluated continuously against ingested log batches.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:shrink-0 self-start sm:self-auto">
            <button
              type="button"
              onClick={onOpenPresetsModal}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer whitespace-nowrap"
              title="Browse and install pre-configured alert presets"
            >
              <Zap className="w-3.5 h-3.5 text-accent-500" />
              <span>Presets</span>
            </button>

            <button
              type="button"
              onClick={onExportAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer whitespace-nowrap"
              title="Export all alert rules"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export All</span>
            </button>

            <input
              type="file"
              ref={fileInputRef}
              onChange={onFileImport}
              accept=".json,application/json"
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer whitespace-nowrap"
              title="Import alert rules from JSON file"
            >
              <Upload className="w-3.5 h-3.5" />
              <span>Import</span>
            </button>

            <button
              type="button"
              onClick={onOpenCreateModal}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-on-fill bg-accent-700 hover:bg-accent-600 transition shadow-xs cursor-pointer whitespace-nowrap"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>New Alert Rule</span>
            </button>
          </div>
        </div>

        {rules.length === 0 ? (
          <div className="p-10 text-center space-y-3">
            <div className="p-3 bg-raised text-fg-muted rounded-full w-12 h-12 mx-auto flex items-center justify-center">
              <Bell className="w-6 h-6" />
            </div>
            <p className="text-xs text-fg-soft font-medium">No alert rules configured yet.</p>
            <p className="text-xs text-fg-muted max-w-md mx-auto">
              Set up real-time threshold and pattern alerts to get notified of critical system events and errors.
            </p>
            <div className="flex flex-wrap justify-center gap-2 pt-2">
              <button
                type="button"
                onClick={onOpenPresetsModal}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer"
              >
                <Zap className="w-3.5 h-3.5 text-accent-500" />
                <span>Browse Presets</span>
              </button>
              <button
                type="button"
                onClick={onOpenCreateModal}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-on-fill bg-accent-700 hover:bg-accent-600 transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Create Alert Rule</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="divide-y divide-line-subtle">
            {rules.map((rule, index) => {
              const channelStatus = getChannelStatus(rule.channel_id);

              return (
                <div
                  key={rule.id}
                  {...dragProps(index)}
                  className={`p-4 transition flex flex-col md:flex-row md:items-center justify-between gap-4 ${
                    draggedIndex === index ? 'opacity-40 bg-raised/60' : ''
                  } ${
                    rule.is_enabled ? 'hover:bg-surface-hover/40' : 'opacity-60 bg-canvas/20'
                  }`}
                >
                  <div className="flex items-start gap-3 min-w-0 flex-1">
                    <RuleOrderControls ruleId={rule.id} index={index} count={rules.length} onMove={moveItem} />

                    <div className="space-y-1.5 min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-medium text-fg">{rule.name}</span>
                      <span className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono uppercase bg-raised text-fg-soft border border-line-hover">
                        {rule.rule_type}
                      </span>
                      {rule.ai_enrichment && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[10px] font-medium bg-highlight-950/40 text-highlight-300 border border-highlight-800/50">
                          <Sparkles className="w-2.5 h-2.5" />
                          <span>AI Enriched</span>
                        </span>
                      )}
                      {rule.trigger_count > 0 && (
                        <span className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono bg-warning-950/30 text-warning-300 border border-warning-800/40">
                          Fired {rule.trigger_count}x
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
                      {rule.filter_app && (
                        <span>
                          App: <span className="font-mono text-fg-soft">{rule.filter_app}</span>
                        </span>
                      )}
                      {rule.match_pattern && (
                        <span className="truncate max-w-xs">
                          Pattern: <code className="font-mono text-accent-400 text-[11px]">{rule.match_pattern}</code>
                        </span>
                      )}
                      <span>
                        {rule.rule_type === 'rate' ? (
                          <>Rate: <span className="text-fg-soft">&ge; {rule.threshold_count} logs/s in {rule.window_seconds}s</span></>
                        ) : (
                          <>Threshold: <span className="text-fg-soft">&ge; {rule.threshold_count} in {rule.window_seconds}s</span></>
                        )}
                      </span>
                      <span>
                        Cooldown: <span className="text-fg-soft">{rule.cooldown_seconds}s</span>
                      </span>
                      <span>
                        Target: <span className="text-fg-soft">{channelStatus.name}</span>
                        {channelStatus.isInvalid && (
                          <span
                            className="inline-flex items-center gap-1 text-warning-400 ml-1.5 font-medium"
                            title={channelStatus.warning || undefined}
                          >
                            <AlertTriangle className="w-3 h-3 inline" />
                            <span className="text-[11px] text-warning-300">({channelStatus.warning})</span>
                          </span>
                        )}
                      </span>
                    </div>

                    {rule.last_triggered_at && (
                      <div className="text-[11px] text-fg-muted">
                        Last triggered: {formatLocalTimestamp(rule.last_triggered_at, true, serverTimezone)}
                      </div>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2.5 shrink-0">
                    {/* Status Pill Switch */}
                    <button
                      type="button"
                      onClick={() => onToggleRule(rule)}
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer ${
                        rule.is_enabled
                          ? 'bg-success-950 text-success-400 border border-success-800'
                          : 'bg-raised text-fg-muted border border-line'
                      }`}
                      title={rule.is_enabled ? 'Click to disable' : 'Click to enable'}
                    >
                      {rule.is_enabled ? 'Active' : 'Disabled'}
                    </button>

                    <button
                      type="button"
                      onClick={() => onOpenTestModal(rule)}
                      className="p-1.5 text-fg-muted hover:text-fg hover:bg-raised rounded-sm transition cursor-pointer"
                      title="Test Rule"
                      aria-label={`Test rule ${rule.name}`}
                    >
                      <FlaskConical className="w-4 h-4" />
                    </button>

                    <button
                      type="button"
                      onClick={() => onExportSingle(rule)}
                      className="p-1.5 text-fg-muted hover:text-fg hover:bg-raised rounded-sm transition cursor-pointer"
                      title="Export rule"
                      aria-label={`Export rule ${rule.name}`}
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      onClick={() => onOpenEditModal(rule)}
                      className="p-1.5 text-fg-muted hover:text-fg hover:bg-raised rounded-sm transition cursor-pointer"
                      title="Edit rule"
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      onClick={() => onDeleteRule(rule)}
                      className="p-1.5 text-fg-muted hover:text-danger-400 hover:bg-danger-950/20 rounded-sm transition cursor-pointer"
                      title="Delete rule"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {rules.some((r) => r.ai_enrichment) && (
        <div className="p-3 bg-warning-950/30 border border-warning-800/40 rounded-xl flex items-start gap-2.5 text-warning-200/90 text-xs leading-relaxed">
          <Shield className="w-4 h-4 text-warning-400 shrink-0 mt-0.5" />
          <span>
            <strong className="font-semibold text-warning-300">Automated AI Redaction Notice:</strong> Log events triggering AI-enabled alert rules are automatically dispatched to external AI providers for root-cause diagnosis without prior review. Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Ensure log streams evaluated by AI-enabled rules do not contain unredacted secrets.
          </span>
        </div>
      )}

      {rules.length > 0 && channels.length === 0 && (
        <div className="p-3.5 bg-surface border border-line rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs shadow-xs">
          <div className="flex items-start gap-2.5">
            <Bell className="w-4 h-4 text-accent-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-fg">No Notification Channels Configured</p>
              <p className="text-fg-muted mt-0.5">
                Alert rules will record incidents in the History tab. To receive push notifications, configure a notification channel in Settings.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onNavigateToSettings}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-raised hover:bg-raised-hover border border-line rounded-lg text-xs font-medium text-fg hover:text-fg-max transition cursor-pointer shrink-0 self-start sm:self-auto"
          >
            <span>Configure in Settings</span>
            <ExternalLink className="w-3.5 h-3.5 text-accent-400" />
          </button>
        </div>
      )}
    </div>
  );
};

export default AlertRulesTab;
