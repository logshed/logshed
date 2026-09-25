import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  Bell,
  Shield,
  ShieldAlert,
  History,
  Plus,
  Trash2,
  Edit2,
  CheckCircle2,
  AlertTriangle,
  Sparkles,
  Zap,
  FlaskConical,
  FilterX,
  Download,
  Upload,
} from 'lucide-react';
import {
  AlertHistoryItem,
  AlertPreset,
  AlertRule,
  DropRule,
  NotificationChannel,
} from '../../types.ts';
import {
  fetchAlertRules,
  updateAlertRule,
  deleteAlertRule,
  fetchAlertPresets,
  installAlertPreset,
  fetchAlertHistory,
  deleteAlertHistoryItem,
  clearAlertHistory,
  exportAllAlertRules,
  exportSingleAlertRule,
  importAlertRules,
} from '../../api/alerts.ts';
import { fetchNotificationChannels } from '../../api/notifications.ts';
import { fetchDropRules } from '../../api/dropRules.ts';
import { fetchLogFacets } from '../../api/logs.ts';
import { downloadBlob, slugify } from '../../utils/formatters.ts';
import { Modal } from '../common/Modal.tsx';
import { IncidentHistoryDetail } from './IncidentHistoryDetail.tsx';
import { AlertRuleModal } from './AlertRuleModal.tsx';
import { AlertTestModal } from './AlertTestModal.tsx';
import { AlertPresetsModal } from './AlertPresetsModal.tsx';
import { IncidentStatusBadge } from './IncidentStatusBadge.tsx';
import { DropRulesCard } from '../settings/DropRulesCard.tsx';
import { useMediaQuery } from '../../utils/hooks.ts';

export type AlertViewTab = 'rules' | 'history' | 'drop-rules';

export const pathToAlertSubTab = (pathname: string): AlertViewTab => {
  const clean = pathname.replace(/\/+$/, '').toLowerCase();
  if (clean === '/alerts/history') {
    return 'history';
  }
  if (clean === '/alerts/drop-rules' || clean === '/alerts/drop') {
    return 'drop-rules';
  }
  return 'rules';
};

export const alertSubTabToPath = (subTab: AlertViewTab): string => {
  switch (subTab) {
    case 'history':
      return '/alerts/history';
    case 'drop-rules':
      return '/alerts/drop-rules';
    case 'rules':
    default:
      return '/alerts/rules';
  }
};

const SplitCountBadge: React.FC<{ active: number; total: number; title?: string }> = ({ active, total, title }) => (
  <span
    title={title}
    className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-dark-900/90 border border-dark-700 leading-none select-none shrink-0"
  >
    <span className={active > 0 ? 'text-emerald-400 font-semibold' : 'text-slate-500'}>
      {active}
    </span>
    <span className="text-slate-600">/</span>
    <span className="text-slate-400">{total}</span>
  </span>
);

const SimpleCountBadge: React.FC<{ count: number; title?: string }> = ({ count, title }) => (
  <span
    title={title}
    className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-dark-900/90 border border-dark-700 leading-none text-slate-400 select-none shrink-0"
  >
    {count}
  </span>
);

export const AlertsPanel: React.FC = () => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [activeSubTab, setActiveSubTab] = useState<AlertViewTab>(() =>
    pathToAlertSubTab(window.location.pathname)
  );
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [presets, setPresets] = useState<AlertPreset[]>([]);
  const [historyItems, setHistoryItems] = useState<AlertHistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState<number>(0);
  const [dropRules, setDropRules] = useState<DropRule[]>([]);
  const [availableApps, setAvailableApps] = useState<string[]>([]);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; isError: boolean } | null>(null);

  // Modals state
  const [isRuleModalOpen, setIsRuleModalOpen] = useState<boolean>(false);
  const [ruleToEdit, setRuleToEdit] = useState<AlertRule | null>(null);
  const [ruleToDelete, setRuleToDelete] = useState<AlertRule | null>(null);
  const [historyItemToDelete, setHistoryItemToDelete] = useState<AlertHistoryItem | null>(null);
  const [isTestModalOpen, setIsTestModalOpen] = useState<boolean>(false);
  const [ruleToTest, setRuleToTest] = useState<AlertRule | null>(null);
  const [isClearHistoryModalOpen, setIsClearHistoryModalOpen] = useState<boolean>(false);
  const [isPresetsModalOpen, setIsPresetsModalOpen] = useState<boolean>(false);

  // Presets installation state
  const [installingPresetId, setInstallingPresetId] = useState<string | null>(null);
  const [presetChannelId, setPresetChannelId] = useState<number | null>(null);

  // History selected item state for modal overlay
  const [selectedHistoryItem, setSelectedHistoryItem] = useState<AlertHistoryItem | null>(null);

  // Pre-index notification channels into a lookup Map using useMemo for O(1) lookups
  const channelMap = useMemo(() => new Map(channels.map((c) => [c.id, c])), [channels]);

  const enabledRulesCount = useMemo(
    () => rules.filter((r) => r.is_enabled).length,
    [rules]
  );

  const enabledDropRulesCount = useMemo(
    () => dropRules.filter((r) => r.is_enabled).length,
    [dropRules]
  );

  const handleSubTabChange = (nextSubTab: AlertViewTab) => {
    setActiveSubTab(nextSubTab);
    const targetPath = alertSubTabToPath(nextSubTab);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  };

  useEffect(() => {
    const handlePopState = () => {
      setActiveSubTab(pathToAlertSubTab(window.location.pathname));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const loadAll = useCallback(async () => {
    try {
      const [rulesData, channelsData, presetsData, historyData, facetsData, dropRulesData] = await Promise.all([
        fetchAlertRules(),
        fetchNotificationChannels(),
        fetchAlertPresets(),
        fetchAlertHistory(50, 0),
        fetchLogFacets().catch(() => ({ sources: [], apps: [], host_to_apps: {}, app_to_hosts: {} })),
        fetchDropRules().catch(() => []),
      ]);
      setRules(rulesData);
      setChannels(channelsData);
      setPresets(presetsData);
      setHistoryItems(historyData.items);
      setHistoryTotal(historyData.total);
      setDropRules(dropRulesData);
      if (facetsData?.apps) {
        setAvailableApps(facetsData.apps);
      }
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load alert configuration.', isError: true });
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Open create modal
  const handleOpenCreateModal = () => {
    setRuleToEdit(null);
    setIsRuleModalOpen(true);
  };

  // Open edit modal
  const handleOpenEditModal = (rule: AlertRule) => {
    setRuleToEdit(rule);
    setIsRuleModalOpen(true);
  };

  // Handle saved rule from AlertRuleModal
  const handleRuleSuccess = (savedRule: AlertRule) => {
    if (ruleToEdit) {
      setRules((prev) => prev.map((r) => (r.id === savedRule.id ? savedRule : r)));
      setFeedbackMsg({ text: `Alert rule "${savedRule.name}" updated successfully.`, isError: false });
    } else {
      setRules((prev) => [...prev, savedRule]);
      setFeedbackMsg({ text: `Alert rule "${savedRule.name}" created successfully.`, isError: false });
    }
  };

  // Toggle rule status
  const handleToggleRule = async (rule: AlertRule) => {
    try {
      const updated = await updateAlertRule(rule.id, { is_enabled: !rule.is_enabled });
      setRules((prev) => prev.map((r) => (r.id === rule.id ? updated : r)));
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to update rule status.', isError: true });
    }
  };

  // Confirm delete rule
  const handleConfirmDeleteRule = async () => {
    if (!ruleToDelete) return;
    try {
      await deleteAlertRule(ruleToDelete.id);
      setRules((prev) => prev.filter((r) => r.id !== ruleToDelete.id));
      setFeedbackMsg({ text: `Alert rule "${ruleToDelete.name}" deleted.`, isError: false });
      setRuleToDelete(null);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to delete alert rule.', isError: true });
    }
  };

  // Open test pattern modal
  const handleOpenTestModal = (rule: AlertRule) => {
    setRuleToTest(rule);
    setIsTestModalOpen(true);
  };

  // 1-Click Install Preset
  const handleInstallPreset = async (preset: AlertPreset) => {
    setInstallingPresetId(preset.id);
    try {
      const created = await installAlertPreset(preset.id, presetChannelId);
      setRules((prev) => [...prev, created]);
      setFeedbackMsg({ text: `Alert preset "${preset.name}" installed and active.`, isError: false });
      setActiveSubTab('rules');
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to install preset.', isError: true });
    } finally {
      setInstallingPresetId(null);
    }
  };

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleExportAll = async () => {
    try {
      const blob = await exportAllAlertRules();
      downloadBlob(blob, 'logshed-alert-rules.json');
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to export alert rules.', isError: true });
    }
  };

  const handleExportSingle = async (rule: AlertRule) => {
    try {
      const blob = await exportSingleAlertRule(rule.id);
      downloadBlob(blob, `${slugify(rule.name)}.json`);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to export alert rule.', isError: true });
    }
  };

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';

    const reader = new FileReader();
    reader.onload = async (event) => {
      try {
        const text = event.target?.result as string;
        const json = JSON.parse(text);
        const res = await importAlertRules(json);
        if (res.errors && res.errors.length > 0) {
          setFeedbackMsg({
            text: `Imported ${res.imported}, skipped ${res.skipped}. Errors: ${res.errors.join('; ')}`,
            isError: res.imported === 0,
          });
        } else {
          setFeedbackMsg({
            text: `Imported ${res.imported} rule${res.imported === 1 ? '' : 's'} (${res.skipped} skipped).`,
            isError: false,
          });
        }
        loadAll();
      } catch (err: any) {
        setFeedbackMsg({
          text: err.message || 'Failed to import rules. Invalid JSON file.',
          isError: true,
        });
      }
    };
    reader.onerror = () => {
      setFeedbackMsg({
        text: 'Failed to read file.',
        isError: true,
      });
    };
    reader.readAsText(file);
  };

  // Delete history item
  const handleDeleteHistory = async (id: number) => {
    try {
      await deleteAlertHistoryItem(id);
      setHistoryItems((prev) => prev.filter((h) => h.id !== id));
      setHistoryTotal((prev) => Math.max(0, prev - 1));
      if (selectedHistoryItem?.id === id) {
        setSelectedHistoryItem(null);
      }
      setHistoryItemToDelete(null);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to delete history record.', isError: true });
    }
  };

  // Clear all history
  const handleClearAllHistory = async () => {
    try {
      await clearAlertHistory();
      setHistoryItems([]);
      setHistoryTotal(0);
      setSelectedHistoryItem(null);
      setIsClearHistoryModalOpen(false);
      setFeedbackMsg({ text: 'All alert history records cleared.', isError: false });
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to clear alert history.', isError: true });
    }
  };

  const getChannelStatus = (channelId?: number | null) => {
    if (channelId == null) {
      const anyDisabled = channels.some((c) => !c.is_enabled);
      if (channels.length > 0 && anyDisabled) {
        return {
          name: 'All Enabled Channels',
          warning: '1 or more target channels disabled',
          isInvalid: true,
        };
      }
      return {
        name: 'All Enabled Channels',
        warning: null,
        isInvalid: false,
      };
    }
    const found = channelMap.get(channelId);
    if (!found) {
      return {
        name: `Channel #${channelId}`,
        warning: 'Channel not found or deleted',
        isInvalid: true,
      };
    }
    if (!found.is_enabled) {
      return {
        name: found.name,
        warning: 'Target channel is disabled',
        isInvalid: true,
      };
    }
    return {
      name: found.name,
      warning: null,
      isInvalid: false,
    };
  };

  const getChannelName = (channelId?: number | null) => {
    return getChannelStatus(channelId).name;
  };

  return (
    <div className="w-full max-w-5xl mx-auto p-3 sm:p-6 space-y-6 sm:space-y-8 min-w-0">
      {/* Header */}
      <div>
        <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
          <ShieldAlert className="w-5 h-5 text-accent-500" />
          <span>Alerts and Rules</span>
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Configure real-time threshold and pattern alert rules, manage ingestion drop rules, deploy 1-click quick rules, and review past incidents.
        </p>
      </div>

      {/* Global Toast Feedback */}
      {feedbackMsg && (
        <div
          className={`flex items-center justify-between px-4 py-2.5 rounded-lg text-xs border ${
            feedbackMsg.isError
              ? 'bg-red-950/40 border-red-800/60 text-red-300'
              : 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {feedbackMsg.isError ? <AlertTriangle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
            <span>{feedbackMsg.text}</span>
          </div>
          <button
            onClick={() => setFeedbackMsg(null)}
            className="text-slate-400 hover:text-slate-200 cursor-pointer ml-4"
          >
            &times;
          </button>
        </div>
      )}

      {/* Section Navigation Tabs */}
      <div className="flex items-center gap-2 border-b border-dark-700 pb-2 overflow-x-auto no-scrollbar">
        <button
          type="button"
          onClick={() => handleSubTabChange('rules')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 ${
            activeSubTab === 'rules'
              ? 'bg-dark-800 text-accent-400 border border-dark-650 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border border-transparent'
          }`}
        >
          <Bell className="w-3.5 h-3.5 shrink-0" />
          <span>Alert Rules</span>
          <SplitCountBadge
            active={enabledRulesCount}
            total={rules.length}
            title={`${enabledRulesCount} of ${rules.length} rules active`}
          />
        </button>

        <button
          type="button"
          onClick={() => handleSubTabChange('history')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 ${
            activeSubTab === 'history'
              ? 'bg-dark-800 text-accent-400 border border-dark-650 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border border-transparent'
          }`}
        >
          <History className="w-3.5 h-3.5 shrink-0" />
          <span>Alert History</span>
          <SimpleCountBadge
            count={historyTotal}
            title={`${historyTotal} total incidents recorded`}
          />
        </button>

        <button
          type="button"
          onClick={() => handleSubTabChange('drop-rules')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 ${
            activeSubTab === 'drop-rules'
              ? 'bg-dark-800 text-accent-400 border border-dark-650 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border border-transparent'
          }`}
        >
          <FilterX className="w-3.5 h-3.5 shrink-0" />
          <span>Drop Rules</span>
          <SplitCountBadge
            active={enabledDropRulesCount}
            total={dropRules.length}
            title={`${enabledDropRulesCount} of ${dropRules.length} drop rules active`}
          />
        </button>
      </div>


      {/* TAB 1: Alert Rules */}
      {activeSubTab === 'rules' && (
        <div className="space-y-4">
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
                onClick={() => setIsPresetsModalOpen(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
                title="Browse and install pre-configured alert presets"
              >
                <Zap className="w-3.5 h-3.5 text-accent-500" />
                <span>Presets</span>
              </button>

              <button
                type="button"
                onClick={handleExportAll}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
                title="Export all alert rules"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Export All</span>
              </button>

              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileImport}
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
                onClick={handleOpenCreateModal}
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
                  onClick={() => setIsPresetsModalOpen(true)}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer"
                >
                  <Zap className="w-3.5 h-3.5 text-accent-500" />
                  <span>Browse Presets</span>
                </button>
                <button
                  type="button"
                  onClick={handleOpenCreateModal}
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
                      onClick={() => handleToggleRule(rule)}
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
                      onClick={() => handleOpenTestModal(rule)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Test Rule"
                      aria-label={`Test rule ${rule.name}`}
                    >
                      <FlaskConical className="w-4 h-4" />
                    </button>

                    <button
                      type="button"
                      onClick={() => handleExportSingle(rule)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Export rule"
                      aria-label={`Export rule ${rule.name}`}
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      onClick={() => handleOpenEditModal(rule)}
                      className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                      title="Edit rule"
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>

                    <button
                      type="button"
                      onClick={() => setRuleToDelete(rule)}
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
      </div>
    )}

      {/* TAB 2: Alert History */}
      {activeSubTab === 'history' && (
        <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
          <div className="px-5 py-4 border-b border-dark-700 flex items-center justify-between">
            <div>
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <History className="w-4 h-4 text-accent-500" />
                <span>Alert Firing Log</span>
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Audited record of recent alert triggers, log messages, and AI incident diagnoses.
              </p>
            </div>
            {historyItems.length > 0 && (
              <button
                type="button"
                onClick={() => setIsClearHistoryModalOpen(true)}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-red-400 hover:text-red-300 bg-red-950/30 hover:bg-red-900/30 rounded border border-red-800/40 transition cursor-pointer"
              >
                <Trash2 className="w-3 h-3" />
                <span>Clear All</span>
              </button>
            )}
          </div>

          {historyItems.length === 0 ? (
            <div className="p-10 text-center space-y-2">
              <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto" />
              <p className="text-xs text-slate-300 font-medium">No alerts have fired yet.</p>
              <p className="text-xs text-slate-500">
                When alert thresholds are exceeded, firing records and AI summaries will be logged here.
              </p>
            </div>
          ) : isMobile ? (
            /* Mobile Card View */
            <div className="divide-y divide-dark-800">
              {historyItems.map((item) => {
                return (
                  <div
                    key={item.id}
                    onClick={() => setSelectedHistoryItem(item)}
                    className="p-3.5 space-y-2 hover:bg-dark-800/40 transition cursor-pointer select-none"
                  >
                    {/* Line 1: Rule Name & Timestamp */}
                    <div className="flex items-center justify-between text-xs gap-2">
                      <div className="truncate font-sans font-semibold text-slate-100">
                        {item.rule_name}
                      </div>
                      <span className="text-[10px] text-slate-500 font-mono shrink-0">
                        {new Date(item.triggered_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                    </div>

                    {/* Line 2: Log message */}
                    <div
                      className="text-slate-300 font-mono text-[11px] line-clamp-2 leading-relaxed"
                      title={item.sample_log || ''}
                    >
                      {item.sample_log || '-'}
                    </div>

                    {/* Line 3: Events count + AI status + Actions */}
                    <div className="flex items-center justify-between pt-1 text-[11px] text-slate-400">
                      <div className="flex items-center gap-2">
                        <span className="bg-dark-950 border border-dark-700 px-1.5 py-0.5 rounded text-[10px] text-slate-300 font-mono">
                          {item.trigger_count} event{item.trigger_count === 1 ? '' : 's'}
                        </span>
                        <IncidentStatusBadge
                          aiEnrichment={item.ai_enrichment}
                          incidentSummary={item.incident_summary}
                        />
                      </div>
                      <div className="flex items-center gap-2 font-sans shrink-0" onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          onClick={() => setSelectedHistoryItem(item)}
                          className="px-2 py-0.5 text-[11px] font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                        >
                          View
                        </button>
                        <button
                          type="button"
                          onClick={() => setHistoryItemToDelete(item)}
                          className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded transition cursor-pointer"
                          title="Delete incident record"
                          aria-label={`Delete record ${item.id}`}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            /* Desktop Table View */
            <div className="divide-y divide-dark-800 font-mono text-xs">
              <div className="grid grid-cols-[135px_170px_65px_105px_1fr_95px] px-4 py-2 text-slate-400 font-medium text-xs font-sans bg-dark-950/60 border-b border-dark-700 select-none">
                <div>Time</div>
                <div>Rule Name</div>
                <div>Events</div>
                <div>AI Diagnosis</div>
                <div>
                  <span>Log Message</span>
                  <span className="sr-only">LOG MESSAGE</span>
                </div>
                <div className="text-right">Actions</div>
              </div>

              {historyItems.map((item) => {
                return (
                  <div
                    key={item.id}
                    onClick={() => setSelectedHistoryItem(item)}
                    className="grid grid-cols-[135px_170px_65px_105px_1fr_95px] px-4 py-2.5 items-center hover:bg-dark-800 transition text-[11px] cursor-pointer group select-none"
                  >
                    <div className="text-slate-400 group-hover:text-slate-300">
                      {new Date(item.triggered_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </div>
                    <div className="font-medium text-slate-200 truncate pr-2 font-sans text-xs">
                      {item.rule_name}
                    </div>
                    <div className="text-slate-300">
                      <span className="px-1.5 py-0.5 rounded bg-dark-950 border border-dark-800 text-[10px]">
                        {item.trigger_count}
                      </span>
                    </div>
                    <div>
                      <IncidentStatusBadge
                        aiEnrichment={item.ai_enrichment}
                        incidentSummary={item.incident_summary}
                      />
                    </div>
                    <div
                      className="text-slate-300 truncate pr-2 group-hover:text-white"
                      title={item.sample_log || ''}
                    >
                      {item.sample_log || '-'}
                    </div>
                    <div className="flex items-center justify-end gap-1.5 font-sans" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => setSelectedHistoryItem(item)}
                        className="px-2 py-0.5 text-[11px] font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                        title="View incident details"
                      >
                        View
                      </button>
                      <button
                        type="button"
                        onClick={() => setHistoryItemToDelete(item)}
                        className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded border border-transparent hover:border-red-900/50 transition cursor-pointer"
                        title="Delete incident record"
                        aria-label={`Delete record ${item.id}`}
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
      )}

      {/* TAB 4: Ingestion Drop Rules */}
      {activeSubTab === 'drop-rules' && <DropRulesCard onRulesChange={setDropRules} />}

      {/* CREATE / EDIT RULE MODAL */}
      <AlertRuleModal
        isOpen={isRuleModalOpen}
        ruleToEdit={ruleToEdit}
        channels={channels}
        availableApps={availableApps}
        onClose={() => setIsRuleModalOpen(false)}
        onSuccess={handleRuleSuccess}
      />

      {/* TEST PATTERN MODAL */}
      <AlertTestModal
        isOpen={isTestModalOpen}
        rule={ruleToTest}
        availableApps={availableApps}
        onClose={() => setIsTestModalOpen(false)}
      />

      {/* CONFIRM DELETE RULE MODAL */}
      <Modal
        isOpen={ruleToDelete !== null}
        onClose={() => setRuleToDelete(null)}
        title="Delete Alert Rule"
        maxWidth="max-w-md"
      >
        <div className="space-y-4 text-xs">
          <p className="text-slate-300 leading-relaxed">
            Are you sure you want to delete alert rule <strong className="text-white">"{ruleToDelete?.name}"</strong>? This will stop all monitoring for this rule.
          </p>
          <div className="pt-3 border-t border-dark-700 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setRuleToDelete(null)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmDeleteRule}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition cursor-pointer"
            >
              Delete Rule
            </button>
          </div>
        </div>
      </Modal>

      {/* CONFIRM DELETE SINGLE HISTORY RECORD MODAL */}
      <Modal
        isOpen={historyItemToDelete !== null}
        onClose={() => setHistoryItemToDelete(null)}
        title="Delete Incident Record"
        maxWidth="max-w-md"
      >
        <div className="space-y-4 text-xs">
          <p className="text-slate-300 leading-relaxed">
            Are you sure you want to delete this incident record for rule <strong className="text-white">"{historyItemToDelete?.rule_name}"</strong> from <span className="font-mono text-slate-200">{historyItemToDelete?.triggered_at ? new Date(historyItemToDelete.triggered_at).toLocaleString() : ''}</span>? This action cannot be undone.
          </p>
          <div className="pt-3 border-t border-dark-700 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setHistoryItemToDelete(null)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => historyItemToDelete && handleDeleteHistory(historyItemToDelete.id)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition cursor-pointer"
            >
              Delete Record
            </button>
          </div>
        </div>
      </Modal>

      {/* CONFIRM CLEAR HISTORY MODAL */}
      <Modal
        isOpen={isClearHistoryModalOpen}
        onClose={() => setIsClearHistoryModalOpen(false)}
        title="Clear Alert History"
        maxWidth="max-w-md"
      >
        <div className="space-y-4 text-xs">
          <p className="text-slate-300 leading-relaxed">
            Are you sure you want to clear all {historyTotal} historical alert firing records? This action cannot be undone.
          </p>
          <div className="pt-3 border-t border-dark-700 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setIsClearHistoryModalOpen(false)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleClearAllHistory}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition cursor-pointer"
            >
              Clear All Records
            </button>
          </div>
        </div>
      </Modal>

      {/* HISTORICAL INCIDENT DETAIL MODAL */}
      {selectedHistoryItem && (
        <Modal
          isOpen={!!selectedHistoryItem}
          onClose={() => setSelectedHistoryItem(null)}
          title="Incident Analysis & Log Details"
          maxWidth="max-w-3xl"
        >
          <IncidentHistoryDetail
            item={selectedHistoryItem}
            channelName={selectedHistoryItem.channel_id ? getChannelName(selectedHistoryItem.channel_id) : undefined}
          />
        </Modal>
      )}

      {/* ALERT PRESETS MODAL */}
      <AlertPresetsModal
        isOpen={isPresetsModalOpen}
        onClose={() => setIsPresetsModalOpen(false)}
        presets={presets}
        rules={rules}
        channels={channels}
        presetChannelId={presetChannelId}
        onSelectChannelId={setPresetChannelId}
        onInstallPreset={handleInstallPreset}
        installingPresetId={installingPresetId}
      />
    </div>
  );
};
