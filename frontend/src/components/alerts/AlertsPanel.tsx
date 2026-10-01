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
  AlertHistoryItem,
  AlertPreset,
  AlertRule,
  DropRule,
  NotificationChannel,
  MaintenanceSchedule,
  MaintenanceWindowResponse,
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
  fetchMaintenanceWindow,
  setMaintenanceWindow,
  updateMaintenanceSchedules,
} from '../../api/alerts.ts';
import { fetchNotificationChannels } from '../../api/notifications.ts';
import { fetchDropRules } from '../../api/dropRules.ts';
import { fetchLogFacets } from '../../api/logs.ts';
import { fetchSettings } from '../../api/settings.ts';
import {
  downloadBlob,
  slugify,
  formatMaintenanceTime,
  fromLocalDatetimeInputString,
} from '../../utils/formatters.ts';
import { Modal } from '../common/Modal.tsx';
import { IncidentHistoryDetail } from './IncidentHistoryDetail.tsx';
import { AlertRuleModal } from './AlertRuleModal.tsx';
import { AlertTestModal } from './AlertTestModal.tsx';
import { AlertPresetsModal } from './AlertPresetsModal.tsx';
import { useMediaQuery } from '../../utils/hooks.ts';
import { AlertRulesTab } from './tabs/AlertRulesTab.tsx';
import { DropRulesTab } from './tabs/DropRulesTab.tsx';
import { AlertHistoryTab } from './tabs/AlertHistoryTab.tsx';
import { MaintenanceWindowTab, DAYS_OF_WEEK } from './tabs/MaintenanceWindowTab.tsx';

export type AlertViewTab = 'rules' | 'drop-rules' | 'history' | 'maintenance';

export const pathToAlertSubTab = (pathname: string): AlertViewTab => {
  const clean = pathname.replace(/\/+$/, '').toLowerCase();
  if (clean === '/rules/history') {
    return 'history';
  }
  if (clean === '/rules/drop-rules' || clean === '/rules/drop') {
    return 'drop-rules';
  }
  if (clean === '/rules/maintenance') {
    return 'maintenance';
  }
  return 'rules';
};

export const alertSubTabToPath = (subTab: AlertViewTab): string => {
  switch (subTab) {
    case 'history':
      return '/rules/history';
    case 'drop-rules':
      return '/rules/drop-rules';
    case 'maintenance':
      return '/rules/maintenance';
    case 'rules':
    default:
      return '/rules/rules';
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

export interface AlertsPanelProps {
  onNavigateToSettings?: () => void;
}

export const AlertsPanel: React.FC<AlertsPanelProps> = ({ onNavigateToSettings }) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [activeSubTab, setActiveSubTab] = useState<AlertViewTab>(() =>
    pathToAlertSubTab(window.location.pathname)
  );
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [presets, setPresets] = useState<AlertPreset[]>([]);
  const [historyItems, setHistoryItems] = useState<AlertHistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState<number>(0);
  const [historyLimit] = useState<number>(50);
  const [historyOffset, setHistoryOffset] = useState<number>(0);
  const [dropRules, setDropRules] = useState<DropRule[]>([]);
  const [availableApps, setAvailableApps] = useState<string[]>([]);
  const [isAiConfigured, setIsAiConfigured] = useState<boolean>(true);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; isError: boolean } | null>(null);
  const [maintenance, setMaintenance] = useState<MaintenanceWindowResponse>({
    active: false,
    until: null,
    schedules: [],
  });
  const [customUntil, setCustomUntil] = useState<string>('');

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
  const [ruleToDelete, setRuleToDelete] = useState<AlertRule | null>(null);
  const [historyItemToDelete, setHistoryItemToDelete] = useState<AlertHistoryItem | null>(null);
  const [isTestModalOpen, setIsTestModalOpen] = useState<boolean>(false);
  const [ruleToTest, setRuleToTest] = useState<AlertRule | null>(null);
  const [isClearHistoryModalOpen, setIsClearHistoryModalOpen] = useState<boolean>(false);
  const [isPresetsModalOpen, setIsPresetsModalOpen] = useState<boolean>(false);

  // Scheduled maintenance window state
  const [isScheduleModalOpen, setIsScheduleModalOpen] = useState<boolean>(false);
  const [scheduleToEdit, setScheduleToEdit] = useState<MaintenanceSchedule | null>(null);
  const [scheduleToDelete, setScheduleToDelete] = useState<MaintenanceSchedule | null>(null);
  const [scheduleFormName, setScheduleFormName] = useState<string>('');
  const [scheduleFormRecurrence, setScheduleFormRecurrence] = useState<'daily' | 'weekly' | 'monthly'>('weekly');
  const [scheduleFormStartTime, setScheduleFormStartTime] = useState<string>('02:00');
  const [scheduleFormDuration, setScheduleFormDuration] = useState<number>(60);
  const [scheduleFormDayOfWeek, setScheduleFormDayOfWeek] = useState<number>(0);
  const [scheduleFormDayOfMonth, setScheduleFormDayOfMonth] = useState<number>(1);
  const [scheduleFormEnabled, setScheduleFormEnabled] = useState<boolean>(true);

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
    fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
  };

  useEffect(() => {
    const handlePopState = () => {
      setActiveSubTab(pathToAlertSubTab(window.location.pathname));
      fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

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
      setHistoryItems(historyData.items);
      setHistoryTotal(historyData.total);
      setHistoryOffset(0);
      setDropRules(dropRulesData);
      setMaintenance(maintData);
      if (facetsData?.apps) {
        setAvailableApps(facetsData.apps);
      }
      if (settingsData) {
        setIsAiConfigured(
          Boolean(
            settingsData.ai_enabled &&
            (settingsData.has_ai_api_key || settingsData.ai_provider === 'openai_compatible')
          )
        );
      }
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load alert configuration.', isError: true });
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Periodic poll every 10 seconds to keep maintenance status synchronized
  useEffect(() => {
    const interval = setInterval(() => {
      fetchMaintenanceWindow().then(setMaintenance).catch(() => {});
    }, 10000);
    return () => clearInterval(interval);
  }, []);

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
    const timer = setTimeout(() => {
      fetchMaintenanceWindow()
        .then((res) => {
          setMaintenance(res);
          window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
        })
        .catch(() => {});
    }, delay + 250);
    return () => clearTimeout(timer);
  }, [maintenance.until]);

  const isMaintenanceActiveNow = Boolean(maintenance.active);
  const isOnDemandActiveNow = Boolean(maintenance.on_demand_until);

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

  const handleSetWindow = async (untilIso: string) => {
    try {
      const res = await setMaintenanceWindow(untilIso);
      setMaintenance(res);
      setCustomUntil('');
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
      setFeedbackMsg({
        text: `Maintenance window active until ${formatMaintenanceTime(res.until)}.`,
        isError: false,
      });
    } catch (err: any) {
      setFeedbackMsg({
        text: err.message || 'Failed to set maintenance window.',
        isError: true,
      });
    }
  };

  const handleClearMaintenance = async () => {
    try {
      const res = await setMaintenanceWindow(null);
      setMaintenance(res);
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
      setFeedbackMsg({
        text: 'Maintenance window cleared.',
        isError: false,
      });
    } catch (err: any) {
      setFeedbackMsg({
        text: err.message || 'Failed to clear maintenance window.',
        isError: true,
      });
    }
  };

  const handleSetPreset = async (hours: number) => {
    const targetDate = new Date(Date.now() + hours * 3600 * 1000);
    await handleSetWindow(targetDate.toISOString());
  };

  const handleApplyCustomWindow = async () => {
    if (!customUntil) return;
    const iso = fromLocalDatetimeInputString(customUntil);
    if (!iso) {
      setFeedbackMsg({ text: 'Please enter a valid future date and time.', isError: true });
      return;
    }
    await handleSetWindow(iso);
  };

  const handleOpenAddScheduleModal = () => {
    setScheduleToEdit(null);
    setScheduleFormName('');
    setScheduleFormRecurrence('weekly');
    setScheduleFormStartTime('02:00');
    setScheduleFormDuration(60);
    setScheduleFormDayOfWeek(0);
    setScheduleFormDayOfMonth(1);
    setScheduleFormEnabled(true);
    setIsScheduleModalOpen(true);
  };

  const handleOpenEditScheduleModal = (sched: MaintenanceSchedule) => {
    setScheduleToEdit(sched);
    setScheduleFormName(sched.name);
    setScheduleFormRecurrence(sched.recurrence || 'weekly');
    setScheduleFormStartTime(sched.start_time || '02:00');
    setScheduleFormDuration(sched.duration_minutes || 60);
    setScheduleFormDayOfWeek(sched.day_of_week ?? 0);
    setScheduleFormDayOfMonth(sched.day_of_month ?? 1);
    setScheduleFormEnabled(sched.enabled ?? true);
    setIsScheduleModalOpen(true);
  };

  const handleSaveSchedule = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const cleanName = scheduleFormName.trim();
    if (!cleanName) {
      setFeedbackMsg({ text: 'Schedule name is required.', isError: true });
      return;
    }
    const currentList = maintenance.schedules || [];
    let updatedList: MaintenanceSchedule[];

    if (scheduleToEdit) {
      updatedList = currentList.map((s) =>
        s.id === scheduleToEdit.id
          ? {
              ...s,
              name: cleanName,
              recurrence: scheduleFormRecurrence,
              start_time: scheduleFormStartTime,
              duration_minutes: Number(scheduleFormDuration) || 60,
              day_of_week: scheduleFormRecurrence === 'weekly' ? scheduleFormDayOfWeek : undefined,
              day_of_month: scheduleFormRecurrence === 'monthly' ? scheduleFormDayOfMonth : undefined,
              enabled: scheduleFormEnabled,
            }
          : s
      );
    } else {
      const newSched: MaintenanceSchedule = {
        id: `sched_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
        name: cleanName,
        recurrence: scheduleFormRecurrence,
        start_time: scheduleFormStartTime,
        duration_minutes: Number(scheduleFormDuration) || 60,
        day_of_week: scheduleFormRecurrence === 'weekly' ? scheduleFormDayOfWeek : undefined,
        day_of_month: scheduleFormRecurrence === 'monthly' ? scheduleFormDayOfMonth : undefined,
        enabled: scheduleFormEnabled,
      };
      updatedList = [...currentList, newSched];
    }

    try {
      const res = await updateMaintenanceSchedules(updatedList);
      setMaintenance(res);
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
      setIsScheduleModalOpen(false);
      setFeedbackMsg({
        text: `Schedule "${cleanName}" ${scheduleToEdit ? 'updated' : 'added'} successfully.`,
        isError: false,
      });
    } catch (err: any) {
      setFeedbackMsg({
        text: err.message || 'Failed to save maintenance schedule.',
        isError: true,
      });
    }
  };

  const handleToggleScheduleEnabled = async (sched: MaintenanceSchedule) => {
    const currentList = maintenance.schedules || [];
    const updatedList = currentList.map((s) =>
      s.id === sched.id ? { ...s, enabled: !s.enabled } : s
    );
    try {
      const res = await updateMaintenanceSchedules(updatedList);
      setMaintenance(res);
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
      setFeedbackMsg({
        text: `Schedule "${sched.name}" ${!sched.enabled ? 'enabled' : 'disabled'}.`,
        isError: false,
      });
    } catch (err: any) {
      setFeedbackMsg({
        text: err.message || 'Failed to update schedule status.',
        isError: true,
      });
    }
  };

  const handleConfirmDeleteSchedule = async () => {
    if (!scheduleToDelete) return;
    const currentList = maintenance.schedules || [];
    const updatedList = currentList.filter((s) => s.id !== scheduleToDelete.id);
    try {
      const res = await updateMaintenanceSchedules(updatedList);
      setMaintenance(res);
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
      const deletedName = scheduleToDelete.name;
      setScheduleToDelete(null);
      setFeedbackMsg({
        text: `Schedule "${deletedName}" removed.`,
        isError: false,
      });
    } catch (err: any) {
      setFeedbackMsg({
        text: err.message || 'Failed to delete maintenance schedule.',
        isError: true,
      });
    }
  };

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

  const handleHistoryPageChange = async (newOffset: number) => {
    try {
      const data = await fetchAlertHistory(historyLimit, newOffset);
      setHistoryItems(data.items);
      setHistoryTotal(data.total);
      setHistoryOffset(newOffset);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load history page.', isError: true });
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
          <span>Rules & History</span>
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Configure real-time threshold and pattern alert rules, manage ingestion drop rules, deploy 1-click quick rules, and review past incidents and analyses.
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
      <div className="flex flex-wrap items-center gap-2 border-b border-dark-700 pb-2">
        <button
          type="button"
          onClick={() => handleSubTabChange('rules')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 border ${
            activeSubTab === 'rules'
              ? 'bg-dark-800 text-accent-400 border-dark-600 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
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
              ? 'bg-dark-800 text-accent-400 border-dark-600 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
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
              ? 'bg-dark-800 text-accent-400 border-dark-600 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
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
              ? 'bg-dark-800 text-accent-400 border-dark-600 shadow-xs'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
          }`}
        >
          <Clock className="w-3.5 h-3.5 shrink-0" />
          <span>Maintenance</span>
          {isMaintenanceActiveNow ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse">
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
          onClearMaintenance={handleClearMaintenance}
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
          onPageChange={handleHistoryPageChange}
        />
      )}

      {/* TAB 4: Maintenance */}
      {activeSubTab === 'maintenance' && (
        <MaintenanceWindowTab
          maintenance={maintenance}
          isOnDemandActiveNow={isOnDemandActiveNow}
          customUntil={customUntil}
          setCustomUntil={setCustomUntil}
          onClearMaintenance={handleClearMaintenance}
          onSetPreset={handleSetPreset}
          onApplyCustomWindow={handleApplyCustomWindow}
          onOpenAddScheduleModal={handleOpenAddScheduleModal}
          onOpenEditScheduleModal={handleOpenEditScheduleModal}
          onToggleScheduleEnabled={handleToggleScheduleEnabled}
          onDeleteSchedule={(sched) => setScheduleToDelete(sched)}
        />
      )}

      {/* CREATE / EDIT RULE MODAL */}
      <AlertRuleModal
        isOpen={isRuleModalOpen}
        ruleToEdit={ruleToEdit}
        channels={channels}
        availableApps={availableApps}
        isAiConfigured={isAiConfigured}
        onNavigateToSettings={handleNavigateToSettings}
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
            Are you sure you want to delete alert rule <strong className="text-white">"{ruleToDelete?.name}"</strong>?
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

      {/* HISTORICAL INCIDENT DETAIL MODAL */}
      {selectedHistoryItem && (
        <Modal
          isOpen={!!selectedHistoryItem}
          onClose={() => setSelectedHistoryItem(null)}
          title={
            selectedHistoryItem.rule_name === 'Daily Digest'
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
      <Modal
        isOpen={isClearHistoryModalOpen}
        onClose={() => setIsClearHistoryModalOpen(false)}
        title="Clear All Alert History"
        maxWidth="max-w-md"
      >
        <div className="space-y-4 text-xs">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 shrink-0">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <p className="text-slate-300 leading-relaxed">
              Are you sure you want to permanently clear all alert and analysis history records? This action cannot be undone.
            </p>
          </div>
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
              Clear All History
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
              onClick={() => {
                if (historyItemToDelete) {
                  handleDeleteHistory(historyItemToDelete.id);
                }
              }}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition cursor-pointer"
            >
              Delete Record
            </button>
          </div>
        </div>
      </Modal>

      {/* ADD / EDIT SCHEDULE MODAL */}
      <Modal
        isOpen={isScheduleModalOpen}
        onClose={() => setIsScheduleModalOpen(false)}
        title={scheduleToEdit ? 'Edit Maintenance Schedule' : 'Add Maintenance Schedule'}
        maxWidth="max-w-md"
      >
        <form onSubmit={handleSaveSchedule} className="space-y-4 text-xs">
          {/* Name */}
          <div>
            <label className="block text-slate-300 font-medium mb-1">Schedule Name</label>
            <input
              type="text"
              required
              aria-label="Schedule Name"
              value={scheduleFormName}
              onChange={(e) => setScheduleFormName(e.target.value)}
              placeholder="e.g. Weekly Server Maintenance"
              className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-slate-200 focus:border-accent-500 focus:outline-none"
            />
          </div>

          {/* Recurrence Selection */}
          <div>
            <label className="block text-slate-300 font-medium mb-1">Recurrence</label>
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => setScheduleFormRecurrence('daily')}
                className={`px-3 py-2 rounded-lg font-medium border text-center transition cursor-pointer ${
                  scheduleFormRecurrence === 'daily'
                    ? 'bg-accent-600 text-white border-accent-500'
                    : 'bg-dark-950 text-slate-400 border-dark-700 hover:text-slate-200'
                }`}
              >
                Daily
              </button>
              <button
                type="button"
                onClick={() => setScheduleFormRecurrence('weekly')}
                className={`px-3 py-2 rounded-lg font-medium border text-center transition cursor-pointer ${
                  scheduleFormRecurrence === 'weekly'
                    ? 'bg-accent-600 text-white border-accent-500'
                    : 'bg-dark-950 text-slate-400 border-dark-700 hover:text-slate-200'
                }`}
              >
                Weekly
              </button>
              <button
                type="button"
                onClick={() => setScheduleFormRecurrence('monthly')}
                className={`px-3 py-2 rounded-lg font-medium border text-center transition cursor-pointer ${
                  scheduleFormRecurrence === 'monthly'
                    ? 'bg-accent-600 text-white border-accent-500'
                    : 'bg-dark-950 text-slate-400 border-dark-700 hover:text-slate-200'
                }`}
              >
                Monthly
              </button>
            </div>
          </div>

          {/* Weekly Day of Week Picker */}
          {scheduleFormRecurrence === 'weekly' && (
            <div>
              <label className="block text-slate-300 font-medium mb-1">Day of the Week</label>
              <select
                aria-label="Day of the Week"
                value={scheduleFormDayOfWeek}
                onChange={(e) => setScheduleFormDayOfWeek(Number(e.target.value))}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-slate-200 focus:border-accent-500 focus:outline-none"
              >
                {DAYS_OF_WEEK.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Monthly Day of Month Picker */}
          {scheduleFormRecurrence === 'monthly' && (
            <div>
              <label className="block text-slate-300 font-medium mb-1">Day of the Month (1 - 31)</label>
              <input
                type="number"
                min="1"
                max="31"
                aria-label="Day of the Month"
                value={scheduleFormDayOfMonth}
                onChange={(e) => setScheduleFormDayOfMonth(Math.max(1, Math.min(31, Number(e.target.value) || 1)))}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-slate-200 focus:border-accent-500 focus:outline-none"
              />
            </div>
          )}

          {/* Start Time & Duration */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Start Time (24h)</label>
              <input
                type="time"
                required
                aria-label="Start Time"
                value={scheduleFormStartTime}
                onChange={(e) => setScheduleFormStartTime(e.target.value)}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-slate-200 focus:border-accent-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-slate-300 font-medium mb-1">Duration (minutes)</label>
              <input
                type="number"
                required
                min="1"
                max="1440"
                aria-label="Duration in minutes"
                value={scheduleFormDuration}
                onChange={(e) => setScheduleFormDuration(Number(e.target.value))}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-slate-200 focus:border-accent-500 focus:outline-none"
              />
            </div>
          </div>

          {/* Quick duration presets */}
          <div className="flex items-center gap-1.5 pt-1">
            <span className="text-[11px] text-slate-400">Presets:</span>
            {[15, 30, 60, 120, 240].map((dur) => (
              <button
                key={dur}
                type="button"
                onClick={() => setScheduleFormDuration(dur)}
                className={`px-2 py-0.5 text-[11px] rounded border transition cursor-pointer ${
                  scheduleFormDuration === dur
                    ? 'bg-accent-950 text-accent-300 border-accent-700'
                    : 'bg-dark-950 text-slate-400 border-dark-700 hover:text-slate-200'
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
              className="rounded border-dark-700 bg-dark-950 text-accent-500 focus:ring-accent-500 focus:ring-offset-dark-900"
            />
            <label htmlFor="schedule-enabled-check" className="text-slate-300 select-none cursor-pointer">
              Enable this maintenance schedule
            </label>
          </div>

          {/* Modal Actions */}
          <div className="pt-3 border-t border-dark-700 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setIsScheduleModalOpen(false)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-1.5 rounded-lg text-xs font-semibold text-white bg-accent-600 hover:bg-accent-500 transition cursor-pointer"
            >
              {scheduleToEdit ? 'Save Changes' : 'Create Schedule'}
            </button>
          </div>
        </form>
      </Modal>

      {/* DELETE SCHEDULE CONFIRMATION MODAL */}
      <Modal
        isOpen={scheduleToDelete !== null}
        onClose={() => setScheduleToDelete(null)}
        title="Delete Maintenance Schedule"
        maxWidth="max-w-md"
      >
        <div className="space-y-4 text-xs">
          <p className="text-slate-300 leading-relaxed">
            Are you sure you want to delete maintenance schedule <strong className="text-white">"{scheduleToDelete?.name}"</strong>?
          </p>
          <div className="pt-3 border-t border-dark-700 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setScheduleToDelete(null)}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmDeleteSchedule}
              className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition cursor-pointer"
            >
              Delete Schedule
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default AlertsPanel;
