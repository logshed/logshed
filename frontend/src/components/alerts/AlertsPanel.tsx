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
  Brain,
  Clock,
  ExternalLink,
  Calendar,
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
  toLocalDatetimeInputString,
  fromLocalDatetimeInputString,
} from '../../utils/formatters.ts';
import { extractCleanSummary } from '../../utils/summary.ts';
import { Modal } from '../common/Modal.tsx';
import { IncidentHistoryDetail } from './IncidentHistoryDetail.tsx';
import { AlertRuleModal } from './AlertRuleModal.tsx';
import { AlertTestModal } from './AlertTestModal.tsx';
import { AlertPresetsModal } from './AlertPresetsModal.tsx';
import { DropRulesCard } from '../settings/DropRulesCard.tsx';
import { useMediaQuery } from '../../utils/hooks.ts';

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

const DAYS_OF_WEEK = [
  { value: 0, label: 'Sunday' },
  { value: 1, label: 'Monday' },
  { value: 2, label: 'Tuesday' },
  { value: 3, label: 'Wednesday' },
  { value: 4, label: 'Thursday' },
  { value: 5, label: 'Friday' },
  { value: 6, label: 'Saturday' },
];

const formatScheduleRecurrence = (s: MaintenanceSchedule): string => {
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
                    onClick={handleClearMaintenance}
                    className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-amber-300 hover:text-white border border-dark-600 rounded-lg transition cursor-pointer"
                  >
                    Clear
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => handleSubTabChange('maintenance')}
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
              onClick={handleNavigateToSettings}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded-lg text-xs font-medium text-slate-200 hover:text-white transition cursor-pointer shrink-0 self-start sm:self-auto"
            >
              <span>Configure in Settings</span>
              <ExternalLink className="w-3.5 h-3.5 text-accent-400" />
            </button>
          </div>
        )}
      </div>
    )}

      {/* TAB 2: Ingestion Drop Rules */}
      {activeSubTab === 'drop-rules' && <DropRulesCard onRulesChange={setDropRules} />}

      {/* TAB 3: History */}
      {activeSubTab === 'history' && (
        <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
          <div className="px-5 py-4 border-b border-dark-700 flex items-center justify-between">
            <div>
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <History className="w-4 h-4 text-accent-500" />
                <span>History</span>
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Previous alerts and on-demand analyses
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
              <p className="text-xs text-slate-300 font-medium">No history recorded yet.</p>
              <p className="text-xs text-slate-500">
                When alert rules trigger or AI analyses run, records will appear here.
              </p>
            </div>
          ) : isMobile ? (
            /* Mobile Card View */
            <div className="divide-y divide-dark-800">
              {historyItems.map((item) => {
                const isDigest = item.rule_name === 'Daily Digest';
                const isOnDemand = !isDigest && !item.rule_id && (item.rule_name === 'On-Demand Analysis' || Boolean(item.ai_audit_id && !item.sample_log));
                const isAiAlert = !isOnDemand && !isDigest && Boolean(item.ai_enrichment);
                const displayTarget = isDigest
                  ? 'Daily Digest'
                  : isOnDemand
                  ? (item.source_alias && item.app_name ? `${item.source_alias} • ${item.app_name}` : item.source_alias || item.app_name || 'On-Demand')
                  : item.rule_name;
                const summaryText = extractCleanSummary(item.incident_summary || item.sample_log || '');

                return (
                  <div
                    key={item.id}
                    onClick={() => setSelectedHistoryItem(item)}
                    className="p-3.5 space-y-2 hover:bg-dark-800/40 transition cursor-pointer select-none"
                  >
                    {/* Line 1: Type Badge + Target & Timestamp */}
                    <div className="flex items-center justify-between text-xs gap-2">
                      <div className="flex items-center gap-1.5 truncate">
                        {isDigest ? (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-purple-400 bg-purple-950/60 border border-purple-800/60 shrink-0">
                            <Calendar className="w-2.5 h-2.5 shrink-0" />
                            Daily Digest
                          </span>
                        ) : isOnDemand ? (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-accent-400 bg-accent-950/60 border border-accent-800/60 shrink-0">
                            <Brain className="w-2.5 h-2.5 shrink-0" />
                            On-Demand
                          </span>
                        ) : isAiAlert ? (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-amber-400 bg-amber-950/60 border border-amber-800/60 shrink-0">
                            <Zap className="w-2.5 h-2.5 shrink-0" />
                            AI Alert
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-slate-300 bg-dark-800 border border-dark-650 shrink-0">
                            <Bell className="w-2.5 h-2.5 text-slate-400 shrink-0" />
                            Alert
                          </span>
                        )}
                        <span className="truncate font-sans font-semibold text-slate-100">
                          {displayTarget}
                        </span>
                      </div>
                      <span className="text-[10px] text-slate-500 font-mono shrink-0">
                        {item.triggered_at.slice(0, 16).replace('T', ' ')}
                      </span>
                    </div>

                    {/* Line 2: Clean Summary */}
                    <div
                      className="text-slate-300 text-xs line-clamp-2 leading-relaxed font-sans"
                      title={summaryText}
                    >
                      {summaryText || '-'}
                    </div>

                    {/* Line 3: Model / Count + Actions */}
                    <div className="flex items-center justify-between pt-1 text-[11px] text-slate-400">
                      <div className="flex items-center gap-2">
                        {item.ai_model && (
                          <span className="bg-dark-950 border border-dark-700 px-1.5 py-0.5 rounded text-[10px] text-slate-300 font-mono truncate max-w-[140px]">
                            {item.ai_model}
                          </span>
                        )}
                        <span className="bg-dark-950 border border-dark-700 px-1.5 py-0.5 rounded text-[10px] text-slate-300 font-mono">
                          {item.trigger_count} {isDigest ? 'logs' : isOnDemand ? `log${item.trigger_count === 1 ? '' : 's'}` : `event${item.trigger_count === 1 ? '' : 's'}`}
                        </span>
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
                          title="Delete history record"
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
              <div className="grid grid-cols-[135px_110px_200px_1fr_95px] px-4 py-2 text-slate-400 font-medium text-xs font-sans bg-dark-950/60 border-b border-dark-700 select-none">
                <div>Time</div>
                <div>Type</div>
                <div>Target / Rule</div>
                <div>Summary</div>
                <div className="text-right">Actions</div>
              </div>

              {historyItems.map((item) => {
                const isDigest = item.rule_name === 'Daily Digest';
                const isOnDemand = !isDigest && !item.rule_id && (item.rule_name === 'On-Demand Analysis' || Boolean(item.ai_audit_id && !item.sample_log));
                const isAiAlert = !isOnDemand && !isDigest && Boolean(item.ai_enrichment);
                const displayTarget = isDigest
                  ? 'Daily Digest'
                  : isOnDemand
                  ? (item.source_alias && item.app_name ? `${item.source_alias} • ${item.app_name}` : item.source_alias || item.app_name || 'On-Demand')
                  : item.rule_name;
                const summaryText = extractCleanSummary(item.incident_summary || item.sample_log || '');

                return (
                  <div
                    key={item.id}
                    onClick={() => setSelectedHistoryItem(item)}
                    className="grid grid-cols-[135px_110px_200px_1fr_95px] px-4 py-2.5 items-center hover:bg-dark-800 transition text-[11px] cursor-pointer group select-none"
                  >
                    <div className="text-slate-400 group-hover:text-slate-300 font-mono">
                      {item.triggered_at.slice(0, 16).replace('T', ' ')}
                    </div>
                    <div>
                      {isDigest ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-purple-400 bg-purple-950/60 border border-purple-800/60">
                          <Calendar className="w-2.5 h-2.5 shrink-0" />
                          Daily Digest
                        </span>
                      ) : isOnDemand ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-accent-400 bg-accent-950/60 border border-accent-800/60">
                          <Brain className="w-2.5 h-2.5 shrink-0" />
                          On-Demand
                        </span>
                      ) : isAiAlert ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-amber-400 bg-amber-950/60 border border-amber-800/60">
                          <Zap className="w-2.5 h-2.5 shrink-0" />
                          AI Alert
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-slate-300 bg-dark-800 border border-dark-650">
                          <Bell className="w-2.5 h-2.5 text-slate-400 shrink-0" />
                          Alert
                        </span>
                      )}
                    </div>
                    <div className="truncate pr-2 font-sans font-medium text-slate-200" title={displayTarget}>
                      {displayTarget}
                    </div>
                    <div
                      className="text-slate-300 truncate pr-2 group-hover:text-white font-sans text-xs"
                      title={summaryText}
                    >
                      {summaryText || '-'}
                    </div>
                    <div className="flex items-center justify-end gap-1.5 font-sans" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => setSelectedHistoryItem(item)}
                        className="px-2 py-0.5 text-[11px] font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                        title="View details"
                      >
                        View
                      </button>
                      <button
                        type="button"
                        onClick={() => setHistoryItemToDelete(item)}
                        className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded border border-transparent hover:border-red-900/50 transition cursor-pointer"
                        title="Delete history record"
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

      {/* TAB 4: Maintenance */}
      {activeSubTab === 'maintenance' && (
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
                    onClick={handleClearMaintenance}
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
                      onClick={() => handleSetPreset(1)}
                      className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                    >
                      +1 Hour
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSetPreset(4)}
                      className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                    >
                      +4 Hours
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSetPreset(8)}
                      className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 hover:text-white border border-dark-700 hover:border-dark-600 rounded-lg transition cursor-pointer"
                    >
                      +8 Hours
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSetPreset(24)}
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
                      onClick={handleApplyCustomWindow}
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
                onClick={handleOpenAddScheduleModal}
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
                    onClick={handleOpenAddScheduleModal}
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
                            onClick={() => handleToggleScheduleEnabled(sched)}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-950/60 text-emerald-400 border border-emerald-800/60 hover:bg-emerald-900/60 transition cursor-pointer"
                            title="Click to disable"
                          >
                            Enabled
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleToggleScheduleEnabled(sched)}
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
                          onClick={() => handleOpenEditScheduleModal(sched)}
                          className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                          title="Edit schedule"
                          aria-label={`Edit ${sched.name}`}
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setScheduleToDelete(sched)}
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

      {/* ADD / EDIT SCHEDULE MODAL */}
      <Modal
        isOpen={isScheduleModalOpen}
        onClose={() => setIsScheduleModalOpen(false)}
        title={scheduleToEdit ? 'Edit Maintenance Schedule' : 'Add Maintenance Schedule'}
        maxWidth="max-w-lg"
      >
        <form onSubmit={handleSaveSchedule} className="space-y-4 text-xs">
          {/* Name */}
          <div>
            <label className="block text-slate-300 font-medium mb-1">
              Schedule Name <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              required
              aria-label="Schedule Name"
              value={scheduleFormName}
              onChange={(e) => setScheduleFormName(e.target.value)}
              placeholder="e.g. Weekly Server Maintenance"
              className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-slate-200 placeholder-slate-500 focus:border-accent-500 focus:outline-none"
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
