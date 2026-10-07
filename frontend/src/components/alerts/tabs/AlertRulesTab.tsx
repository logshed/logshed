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
import { formatMaintenanceTime } from '../../../utils/formatters.ts';

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
  getChannelStatus,
}) => {
  return (
    <div className="space-y-4">
      {/* Active Maintenance Banner on Rules Tab */}
      {isMaintenanceActiveNow && (
        <div className="bg-amber-950/40 border border-amber-800/60 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs shadow-xs">
          <div className="flex items-start sm:items-center gap-3">
            <span className="p-2 rounded-lg bg-amber-500/20 text-amber-400 shrink-0">
              <AlertTriangle className="w-4 h-4 animate-pulse" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-amber-200">Maintenance Window Active</span>
                {maintenance.schedule_name ? (
                  <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-900/60 text-amber-300 border border-amber-700/60">
                    {maintenance.schedule_name}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-900/60 text-amber-300 border border-amber-700/60">
                    On-Demand
                  </span>
                )}
              </div>
              <p className="text-slate-300 mt-0.5">
                Alert notifications are silenced
                {maintenance.until ? (
                  <> until <span className="font-semibold text-white">{formatMaintenanceTime(maintenance.until)}</span></>
                ) : null}. Rule evaluation and incident recording continue normally.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0 self-end sm:self-auto">
            {maintenance.on_demand_until && (
              <button
                type="button"
                onClick={onClearMaintenance}
                className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-amber-300 hover:text-white border border-dark-600 rounded-lg transition cursor-pointer"
              >
                Clear
              </button>
            )}
            <button
              type="button"
              onClick={onNavigateToMaintenance}
              className="px-3 py-1.5 text-xs font-semibold bg-amber-500 hover:bg-amber-400 text-dark-950 rounded-lg transition cursor-pointer"
            >
              Manage Maintenance
            </button>
          </div>
        </div>
      )}

      <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
        <div className="px-5 py-4 border-b border-dark-700 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Bell className="w-4 h-4 text-accent-500" />
              <span>Configured Alert Rules</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Rules evaluated continuously against ingested log batches.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:shrink-0 self-start sm:self-auto">
            <button
              type="button"
              onClick={onOpenPresetsModal}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
              title="Browse and install pre-configured alert presets"
            >
              <Zap className="w-3.5 h-3.5 text-accent-500" />
              <span>Presets</span>
            </button>

            <button
              type="button"
              onClick={onExportAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
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
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
              title="Import alert rules from JSON file"
            >
              <Upload className="w-3.5 h-3.5" />
              <span>Import</span>
            </button>

            <button
              type="button"
              onClick={onOpenCreateModal}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition shadow-xs cursor-pointer whitespace-nowrap"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>New Alert Rule</span>
            </button>
          </div>
        </div>

        {rules.length === 0 ? (
          <div className="p-10 text-center space-y-3">
            <div className="p-3 bg-dark-800 text-slate-400 rounded-full w-12 h-12 mx-auto flex items-center justify-center">
              <Bell className="w-6 h-6" />
            </div>
            <p className="text-xs text-slate-300 font-medium">No alert rules configured yet.</p>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              Set up real-time threshold and pattern alerts to get notified of critical system events and errors.
            </p>
            <div className="flex flex-wrap justify-center gap-2 pt-2">
              <button
                type="button"
                onClick={onOpenPresetsModal}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer"
              >
                <Zap className="w-3.5 h-3.5 text-accent-500" />
                <span>Browse Presets</span>
              </button>
              <button
                type="button"
                onClick={onOpenCreateModal}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Create Alert Rule</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="divide-y divide-dark-800">
            {rules.map((rule) => {
              const channelStatus = getChannelStatus(rule.channel_id);

              return (
                <div
                  key={rule.id}
                  className={`p-4 transition flex flex-col md:flex-row md:items-center justify-between gap-4 ${
                    rule.is_enabled ? 'hover:bg-dark-850/40' : 'opacity-60 bg-dark-950/20'
                  }`}
                >
                  <div className="space-y-1.5 min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-slate-200">{rule.name}</span>
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-dark-800 text-slate-300 border border-dark-650">
                        {rule.rule_type}
                      </span>
                      {rule.ai_enrichment && (
                        <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-950/40 text-purple-300 border border-purple-800/50">
                          <Sparkles className="w-2.5 h-2.5" />
                          <span>AI Enriched</span>
                        </span>
                      )}
                      {rule.trigger_count > 0 && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-950/30 text-amber-300 border border-amber-800/40">
                          Fired {rule.trigger_count}x
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                      {rule.filter_app && (
                        <span>
                          App: <span className="font-mono text-slate-300">{rule.filter_app}</span>
                        </span>
                      )}
                      {rule.match_pattern && (
                        <span className="truncate max-w-xs">
                          Pattern: <code className="font-mono text-accent-400 text-[11px]">{rule.match_pattern}</code>
                        </span>
                      )}
                      <span>
                        {rule.rule_type === 'rate' ? (
                          <>Rate: <span className="text-slate-300">&ge; {rule.threshold_count} logs/s in {rule.window_seconds}s</span></>
                        ) : (
                          <>Threshold: <span className="text-slate-300">&ge; {rule.threshold_count} in {rule.window_seconds}s</span></>
                        )}
                      </span>
                      <span>
                        Cooldown: <span className="text-slate-300">{rule.cooldown_seconds}s</span>
                      </span>
                      <span>
                        Target: <span className="text-slate-300">{channelStatus.name}</span>
                        {channelStatus.isInvalid && (
                          <span
                            className="inline-flex items-center gap-1 text-amber-400 ml-1.5 font-medium"
                            title={channelStatus.warning || undefined}
                          >
                            <AlertTriangle className="w-3 h-3 inline" />
                            <span className="text-[11px] text-amber-300">({channelStatus.warning})</span>
                          </span>
                        )}
                      </span>
                    </div>

                    {rule.last_triggered_at && (
                      <div className="text-[11px] text-slate-500">
                        Last triggered: {new Date(rule.last_triggered_at).toLocaleString()}
                      </div>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2.5 shrink-0">
                    {/* Status Pill Switch */}
                    <button
                      type="button"
                      onClick={() => onToggleRule(rule)}
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer ${
                        rule.is_enabled
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : 'bg-dark-800 text-slate-400 border border-dark-700'
                      }`}
                      title={rule.is_enabled ? 'Click to disable' : 'Click to enable'}
                    >
                      {rule.is_enabled ? 'Active' : 'Disabled'}
                    </button>

                    <button
                      type="button"
                      onClick={() => onOpenTestModal(rule)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Test Rule"
                      aria-label={`Test rule ${rule.name}`}
                    >
                      <FlaskConical className="w-4 h-4" />
                    </button>

                    <button
                      type="button"
                      onClick={() => onExportSingle(rule)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Export rule"
                      aria-label={`Export rule ${rule.name}`}
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      onClick={() => onOpenEditModal(rule)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Edit rule"
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      onClick={() => onDeleteRule(rule)}
                      className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-950/20 rounded transition cursor-pointer"
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
        <div className="p-3 bg-amber-950/30 border border-amber-800/40 rounded-xl flex items-start gap-2.5 text-amber-200/90 text-xs leading-relaxed">
          <Shield className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <span>
            <strong className="font-semibold text-amber-300">Automated AI Redaction Notice:</strong> Log events triggering AI-enabled alert rules are automatically dispatched to external AI providers for root-cause diagnosis without prior review. Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Ensure log streams evaluated by AI-enabled rules do not contain unredacted secrets.
          </span>
        </div>
      )}

      {rules.length > 0 && channels.length === 0 && (
        <div className="p-3.5 bg-dark-900 border border-dark-700 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs shadow-xs">
          <div className="flex items-start gap-2.5">
            <Bell className="w-4 h-4 text-accent-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-slate-200">No Notification Channels Configured</p>
              <p className="text-slate-400 mt-0.5">
                Alert rules will record incidents in the History tab. To receive push notifications, configure a notification channel in Settings.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onNavigateToSettings}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded-lg text-xs font-medium text-slate-200 hover:text-white transition cursor-pointer shrink-0 self-start sm:self-auto"
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
