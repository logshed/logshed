import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  ShieldAlert,
  Bell,
  FilterX,
  History,
  Clock,
  AlertTriangle,
  CheckCircle2,
} from 'lucide-react';
import {
  AlertPreset,
  AlertRule,
  DropRule,
  NotificationChannel,
  MaintenanceSchedule,
} from '../../types.ts';
import {
  fetchAlertRules,
  updateAlertRule,
  deleteAlertRule,
  fetchAlertPresets,
  installAlertPreset,
  fetchAlertHistory,
  exportAllAlertRules,
  exportSingleAlertRule,
  importAlertRules,
  reorderAlertRules,
  fetchMaintenanceWindow,
} from '../../api/alerts.ts';
import { fetchNotificationChannels } from '../../api/notifications.ts';
import { fetchDropRules } from '../../api/dropRules.ts';
import { fetchLogFacets } from '../../api/logs.ts';
import { fetchSettings } from '../../api/settings.ts';
import { providerNeedsApiKey } from '../../utils/aiProviders.ts';
import {
  slugify,
  formatLocalTimestamp,
} from '../../utils/formatters.ts';
import { Modal } from '../common/Modal.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { IncidentHistoryDetail } from './IncidentHistoryDetail.tsx';
import { AlertRuleModal, AlertRulePrefill } from './AlertRuleModal.tsx';
import { AlertRuleHandoff, takeAlertRuleHandoff } from '../../utils/alertRuleHandoff.ts';
import { AlertTestModal } from './AlertTestModal.tsx';
import { AlertPresetsModal } from './AlertPresetsModal.tsx';
import { useMediaQuery, useServerTimezone } from '../../utils/hooks.ts';
import { isMaintenanceItem } from '../../utils/maintenanceHistory.ts';
import { useSerialReorder } from '../../utils/useSerialReorder.ts';
import { downloadRuleExport, importRuleFile } from '../../utils/ruleTransfer.ts';
import { AlertRulesTab } from './tabs/AlertRulesTab.tsx';
import { DropRulesTab } from './tabs/DropRulesTab.tsx';
import { AlertHistoryTab } from './tabs/AlertHistoryTab.tsx';
import { MaintenanceWindowTab } from './tabs/MaintenanceWindowTab.tsx';
import { useMaintenance } from './useMaintenance.ts';
import { useAlertHistory } from './useAlertHistory.ts';

import { type AlertViewTab, pathToAlertSubTab, alertSubTabToPath } from './alertRoutes.ts';
export { type AlertViewTab, pathToAlertSubTab, alertSubTabToPath, parseIncidentIdFromUrl } from './alertRoutes.ts';

const SplitCountBadge: React.FC<{ active: number; total: number; title?: string }> = ({ active, total, title }) => (
  <span
    title={title}
    className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-sm text-[10px] font-mono bg-surface/90 border border-line leading-none select-none shrink-0"
  >
    <span className={active > 0 ? 'text-success-400 font-semibold' : 'text-fg-muted'}>
      {active}
    </span>
    <span className="text-fg-faint">/</span>
    <span className="text-fg-muted">{total}</span>
  </span>
);

const SimpleCountBadge: React.FC<{ count: number; title?: string }> = ({ count, title }) => (
  <span
    title={title}
    className="inline-flex items-center px-1.5 py-0.5 rounded-sm text-[10px] font-mono bg-surface/90 border border-line leading-none text-fg-muted select-none shrink-0"
  >
    {count}
  </span>
);

export interface AlertsPanelProps {
  onNavigateToSettings?: () => void;
}

export const AlertsPanel: React.FC<AlertsPanelProps> = ({ onNavigateToSettings }) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const serverTimezone = useServerTimezone();
  const [activeSubTab, setActiveSubTab] = useState<AlertViewTab>(() =>
    pathToAlertSubTab(window.location.pathname)
  );
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [presets, setPresets] = useState<AlertPreset[]>([]);
  const [dropRules, setDropRules] = useState<DropRule[]>([]);
  const [availableApps, setAvailableApps] = useState<string[]>([]);
  const [isAiConfigured, setIsAiConfigured] = useState<boolean>(true);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; isError: boolean } | null>(null);

  // Auto-dismiss success messages after 5 seconds; errors stay until replaced
  useEffect(() => {
    if (!feedbackMsg || feedbackMsg.isError) return;
    const timer = setTimeout(() => setFeedbackMsg(null), 5000);
    return () => clearTimeout(timer);
  }, [feedbackMsg]);

  const history = useAlertHistory({ isShown: activeSubTab === 'history', onFeedback: setFeedbackMsg });
  const {
    historyItems,
    historyTotal,
    historyLimit,
    historyOffset,
    selectedHistoryItem,
    setSelectedHistoryItem,
    closeHistoryItem,
    historyItemToDelete,
    setHistoryItemToDelete,
    isClearHistoryModalOpen,
    setIsClearHistoryModalOpen,
    refreshHistory,
  } = history;
  const maint = useMaintenance({ serverTimezone, onFeedback: setFeedbackMsg, onHistoryChanged: refreshHistory });
  const { maintenance, setMaintenance, isMaintenanceActiveNow, isOnDemandActiveNow } = maint;
  const [appUrl, setAppUrl] = useState<string>('');

  const handleNavigateToSettings = () => {
    if (onNavigateToSettings) {
      onNavigateToSettings();
    } else {
      window.history.pushState(null, '', '/settings');
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  };

  // Modals state
  const [isRuleModalOpen, setIsRuleModalOpen] = useState<boolean>(false);
  const [ruleToEdit, setRuleToEdit] = useState<AlertRule | null>(null);
  const [rulePrefill, setRulePrefill] = useState<AlertRulePrefill | null>(null);
  const pendingRuleHandoffRef = useRef<AlertRuleHandoff | null>(null);
  const [initialLoadDone, setInitialLoadDone] = useState<boolean>(false);
  const [ruleToDelete, setRuleToDelete] = useState<AlertRule | null>(null);
  const [isTestModalOpen, setIsTestModalOpen] = useState<boolean>(false);
  const [ruleToTest, setRuleToTest] = useState<AlertRule | null>(null);
  const [isPresetsModalOpen, setIsPresetsModalOpen] = useState<boolean>(false);

  // Maintenance confirmation questions
  const [scheduleToDelete, setScheduleToDelete] = useState<MaintenanceSchedule | null>(null);
  const [isClearMaintenanceModalOpen, setIsClearMaintenanceModalOpen] = useState<boolean>(false);

  // Presets installation state
  const [installingPresetId, setInstallingPresetId] = useState<string | null>(null);
  const [presetChannelId, setPresetChannelId] = useState<number | null>(null);

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
    maint.refreshMaintenance();
    if (nextSubTab === 'history') {
      refreshHistory();
    }
  };

  useEffect(() => {
    const handlePopState = () => {
      setActiveSubTab(pathToAlertSubTab(window.location.pathname));
      fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [setMaintenance]);

  const loadAll = useCallback(async () => {
    try {
      const [rulesData, channelsData, presetsData, historyData, facetsData, dropRulesData, maintData, settingsData] = await Promise.all([
        fetchAlertRules(),
        fetchNotificationChannels(),
        fetchAlertPresets(),
        fetchAlertHistory(50, 0),
        fetchLogFacets().catch(() => ({ sources: [], apps: [], host_to_apps: {}, app_to_hosts: {} })),
        fetchDropRules().catch(() => []),
        fetchMaintenanceWindow().catch(() => ({ active: false, until: null, schedules: [] })),
        fetchSettings().catch(() => null),
      ]);
      setRules(rulesData);
      setChannels(channelsData);
      setPresets(presetsData);
      history.showFirstPage(historyData);
      setDropRules(dropRulesData);
      setMaintenance(maintData);
      if (facetsData?.apps) {
        setAvailableApps(facetsData.apps);
      }
      if (settingsData) {
        if (settingsData.app_url) {
          setAppUrl(settingsData.app_url);
        }
        setIsAiConfigured(
          Boolean(
            settingsData.ai_enabled &&
            (settingsData.has_ai_api_key || !providerNeedsApiKey(settingsData.ai_provider))
          )
        );
      }
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load alert configuration.', isError: true });
    } finally {
      setInitialLoadDone(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the setters it calls are stable; showFirstPage only calls state setters
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Open create modal
  const handleOpenCreateModal = () => {
    setRuleToEdit(null);
    setRulePrefill(null);
    setIsRuleModalOpen(true);
  };

  // Open edit modal
  const handleOpenEditModal = (rule: AlertRule) => {
    setRuleToEdit(rule);
    setRulePrefill(null);
    setIsRuleModalOpen(true);
  };

  // "Create Alert Rule" from a log: take the hand-off once on mount, then open the prefilled dialog
  // after the first load, so the channels are in and the app can be checked against the known apps.
  useEffect(() => {
    const handoff = takeAlertRuleHandoff();
    if (handoff) pendingRuleHandoffRef.current = handoff;
  }, []);

  useEffect(() => {
    if (!initialLoadDone) return;
    const handoff = pendingRuleHandoffRef.current;
    if (!handoff) return;
    pendingRuleHandoffRef.current = null;
    const appName = handoff.appName?.trim() || '';
    setRulePrefill({
      name: appName ? `Alert on ${appName}` : '',
      filterApps: appName && availableApps.includes(appName) ? [appName] : [],
      referenceMessage: handoff.message || '',
    });
    setRuleToEdit(null);
    setActiveSubTab('rules');
    setIsRuleModalOpen(true);
  }, [initialLoadDone, availableApps]);

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

  const handleReorderRules = useSerialReorder<AlertRule>(
    rules,
    setRules,
    reorderAlertRules,
    (message) => setFeedbackMsg({ text: message, isError: true })
  );

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

  const handleExportAll = () =>
    downloadRuleExport(exportAllAlertRules, 'logshed-alert-rules.json', setFeedbackMsg, 'Failed to export alert rules.');

  const handleExportSingle = (rule: AlertRule) =>
    downloadRuleExport(
      () => exportSingleAlertRule(rule.id),
      `${slugify(rule.name)}.json`,
      setFeedbackMsg,
      'Failed to export alert rule.'
    );

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) =>
    importRuleFile(e, importAlertRules, setFeedbackMsg, loadAll);

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
        <h2 className="text-base font-bold text-fg-strong flex items-center gap-2">
          <ShieldAlert className="w-5 h-5 text-accent-500" />
          <span>Rules & History</span>
        </h2>
        <p className="text-xs text-fg-muted mt-0.5">
          Configure real-time threshold and pattern alert rules, manage ingestion drop rules, deploy 1-click quick rules, and review past incidents and analyses.
        </p>
      </div>

      {/* Global Toast Feedback */}
      {feedbackMsg && (
        <div
          className={`flex items-center justify-between px-4 py-2.5 rounded-lg text-xs border ${
            feedbackMsg.isError
              ? 'bg-danger-950/40 border-danger-800/60 text-danger-300'
              : 'bg-success-950/40 border-success-800/60 text-success-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {feedbackMsg.isError ? <AlertTriangle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
            <span>{feedbackMsg.text}</span>
          </div>
          <button
            onClick={() => setFeedbackMsg(null)}
            className="text-fg-muted hover:text-fg cursor-pointer ml-4"
          >
            &times;
          </button>
        </div>
      )}

      {/* Section Navigation Tabs */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line pb-2">
        <button
          type="button"
          onClick={() => handleSubTabChange('rules')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 border ${
            activeSubTab === 'rules'
              ? 'bg-raised text-accent-400 border-line-strong shadow-xs'
              : 'text-fg-muted hover:text-fg hover:bg-surface border-line hover:border-line-strong'
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
          onClick={() => handleSubTabChange('drop-rules')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 border ${
            activeSubTab === 'drop-rules'
              ? 'bg-raised text-accent-400 border-line-strong shadow-xs'
              : 'text-fg-muted hover:text-fg hover:bg-surface border-line hover:border-line-strong'
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

        <button
          type="button"
          onClick={() => handleSubTabChange('history')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 border ${
            activeSubTab === 'history'
              ? 'bg-raised text-accent-400 border-line-strong shadow-xs'
              : 'text-fg-muted hover:text-fg hover:bg-surface border-line hover:border-line-strong'
          }`}
        >
          <History className="w-3.5 h-3.5 shrink-0" />
          <span>History</span>
          <SimpleCountBadge
            count={historyTotal}
            title={`${historyTotal} total history records`}
          />
        </button>

        <button
          type="button"
          onClick={() => handleSubTabChange('maintenance')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 border ${
            activeSubTab === 'maintenance'
              ? 'bg-raised text-accent-400 border-line-strong shadow-xs'
              : 'text-fg-muted hover:text-fg hover:bg-surface border-line hover:border-line-strong'
          }`}
        >
          <Clock className="w-3.5 h-3.5 shrink-0" />
          <span>Maintenance</span>
          {isMaintenanceActiveNow ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[10px] font-semibold bg-warning-500/20 text-warning-300 border border-warning-500/40 animate-pulse">
              Active
            </span>
          ) : (
            <SimpleCountBadge
              count={maintenance.schedules?.length || 0}
              title={`${maintenance.schedules?.length || 0} recurring schedules`}
            />
          )}
        </button>
      </div>

      {/* TAB 1: Alert Rules */}
      {activeSubTab === 'rules' && (
        <AlertRulesTab
          rules={rules}
          channels={channels}
          isMaintenanceActiveNow={isMaintenanceActiveNow}
          maintenance={maintenance}
          onClearMaintenance={() => setIsClearMaintenanceModalOpen(true)}
          onNavigateToMaintenance={() => handleSubTabChange('maintenance')}
          onOpenPresetsModal={() => setIsPresetsModalOpen(true)}
          onExportAll={handleExportAll}
          onFileImport={handleFileImport}
          fileInputRef={fileInputRef}
          onOpenCreateModal={handleOpenCreateModal}
          onToggleRule={handleToggleRule}
          onOpenTestModal={handleOpenTestModal}
          onExportSingle={handleExportSingle}
          onOpenEditModal={handleOpenEditModal}
          onDeleteRule={(rule) => setRuleToDelete(rule)}
          onNavigateToSettings={handleNavigateToSettings}
          onReorderRules={handleReorderRules}
          getChannelStatus={getChannelStatus}
        />
      )}

      {/* TAB 2: Ingestion Drop Rules */}
      {activeSubTab === 'drop-rules' && <DropRulesTab onRulesChange={setDropRules} />}

      {/* TAB 3: History */}
      {activeSubTab === 'history' && (
        <AlertHistoryTab
          historyItems={historyItems}
          historyTotal={historyTotal}
          isMobile={isMobile}
          onClearAllHistory={() => setIsClearHistoryModalOpen(true)}
          onSelectHistoryItem={(item) => setSelectedHistoryItem(item)}
          onDeleteHistoryItem={(item) => setHistoryItemToDelete(item)}
          limit={historyLimit}
          offset={historyOffset}
          onPageChange={history.changePage}
        />
      )}

      {/* TAB 4: Maintenance */}
      {activeSubTab === 'maintenance' && (
        <MaintenanceWindowTab
          appUrl={appUrl}
          maintenance={maintenance}
          isOnDemandActiveNow={isOnDemandActiveNow}
          customUntil={maint.customUntil}
          setCustomUntil={maint.setCustomUntil}
          onClearMaintenance={() => setIsClearMaintenanceModalOpen(true)}
          onSetPreset={maint.setPreset}
          onExtendSession={maint.extendSession}
          onApplyCustomWindow={maint.applyCustomWindow}
          onSaveSchedule={maint.saveSchedule}
          onToggleScheduleEnabled={maint.toggleScheduleEnabled}
          onDeleteSchedule={(sched) => setScheduleToDelete(sched)}
          onDisableSession={maint.disableSession}
        />
      )}

      {/* CREATE / EDIT RULE MODAL */}
      <AlertRuleModal
        isOpen={isRuleModalOpen}
        ruleToEdit={ruleToEdit}
        channels={channels}
        availableApps={availableApps}
        isAiConfigured={isAiConfigured}
        prefill={rulePrefill}
        onNavigateToSettings={handleNavigateToSettings}
        onClose={() => {
          setIsRuleModalOpen(false);
          setRulePrefill(null);
        }}
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
      <ConfirmDialog
        isOpen={ruleToDelete !== null}
        title="Delete Alert Rule"
        onCancel={() => setRuleToDelete(null)}
        onConfirm={handleConfirmDeleteRule}
        confirmLabel="Delete Rule"
      >
        <p className="text-fg-soft leading-relaxed">
          Are you sure you want to delete alert rule <strong className="text-fg-max">"{ruleToDelete?.name}"</strong>?
        </p>
      </ConfirmDialog>

      {/* HISTORICAL INCIDENT DETAIL MODAL */}
      {selectedHistoryItem && (
        <Modal
          isOpen={!!selectedHistoryItem}
          onClose={closeHistoryItem}
          title={
            isMaintenanceItem(selectedHistoryItem)
              ? 'Maintenance Window Details'
              : selectedHistoryItem.rule_name === 'Daily Digest'
              ? 'Daily Digest Report'
              : !selectedHistoryItem.rule_id || selectedHistoryItem.rule_name === 'On-Demand Analysis'
              ? 'Historical AI Analysis'
              : 'Incident Alert Details'
          }
          maxWidth="max-w-3xl"
        >
          <IncidentHistoryDetail
            item={selectedHistoryItem}
            channelName={selectedHistoryItem.channel_id ? getChannelName(selectedHistoryItem.channel_id) : undefined}
            ruleWindowSeconds={rules.find((r) => r.id === selectedHistoryItem.rule_id)?.window_seconds}
          />
        </Modal>
      )}

      {/* PRESETS INSTALL MODAL */}
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

      {/* CLEAR ALL HISTORY MODAL */}
      <ConfirmDialog
        isOpen={isClearHistoryModalOpen}
        title="Clear All Alert History"
        onCancel={() => setIsClearHistoryModalOpen(false)}
        onConfirm={history.clearAllHistory}
        confirmLabel="Clear All History"
      >
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-danger-500/10 border border-danger-500/20 text-danger-400 shrink-0">
            <AlertTriangle className="w-5 h-5" />
          </div>
          <p className="text-fg-soft leading-relaxed">
            Are you sure you want to permanently clear all alert and analysis history records? This action cannot be undone.
          </p>
        </div>
      </ConfirmDialog>

      {/* CONFIRM DELETE SINGLE HISTORY RECORD MODAL */}
      <ConfirmDialog
        isOpen={historyItemToDelete !== null}
        title="Delete Incident Record"
        onCancel={() => setHistoryItemToDelete(null)}
        onConfirm={() => {
          if (historyItemToDelete) {
            history.deleteHistory(historyItemToDelete.id);
          }
        }}
        confirmLabel="Delete Record"
      >
        <p className="text-fg-soft leading-relaxed">
          Are you sure you want to delete this incident record for rule <strong className="text-fg-max">"{historyItemToDelete?.rule_name}"</strong> from <span className="font-mono text-fg">{formatLocalTimestamp(historyItemToDelete?.triggered_at, true, serverTimezone)}</span>? This action cannot be undone.
        </p>
      </ConfirmDialog>

      {/* DELETE SCHEDULE CONFIRMATION MODAL */}
      <ConfirmDialog
        isOpen={scheduleToDelete !== null}
        title="Delete Maintenance Schedule"
        onCancel={() => setScheduleToDelete(null)}
        onConfirm={async () => {
          if (scheduleToDelete && (await maint.deleteSchedule(scheduleToDelete))) {
            setScheduleToDelete(null);
          }
        }}
        confirmLabel="Delete Schedule"
      >
        <p className="text-fg-soft leading-relaxed">
          Are you sure you want to delete maintenance schedule <strong className="text-fg-max">"{scheduleToDelete?.name}"</strong>?
        </p>
      </ConfirmDialog>

      {/* CLEAR ALL MAINTENANCE SESSIONS CONFIRMATION MODAL */}
      <ConfirmDialog
        isOpen={isClearMaintenanceModalOpen}
        title="Clear All Maintenance Sessions"
        onCancel={() => setIsClearMaintenanceModalOpen(false)}
        onConfirm={() => {
          setIsClearMaintenanceModalOpen(false);
          maint.clearAllSessions();
        }}
        confirmLabel="Clear All Sessions"
        confirmClassName="px-3.5 py-1.5 rounded-lg text-xs font-semibold text-on-fill bg-danger-600 hover:bg-danger-500 transition cursor-pointer"
      >
        <p className="text-fg-soft leading-relaxed">
          Are you sure you want to terminate all active on-demand and API maintenance sessions? Alert notifications will immediately resume.
        </p>
      </ConfirmDialog>
    </div>
  );
};

export default AlertsPanel;
