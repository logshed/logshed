import React, { useCallback, useEffect, useState, useRef } from 'react';
import {
  Settings,
  AlertCircle,
  RefreshCw,
  FileText,
  ArrowUpCircle,
  Server,
  Shield,
} from 'lucide-react';
import { Key } from '../common/icons.ts';
import { Tabs } from '../common/Tabs.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { Checkbox, Field, SELECT_FIELD_CLASS } from '../common/Field.tsx';
import { VersionInfo, SettingsSubTab } from '../../types.ts';
import { fetchSettings, updateSettings, SettingsResponseData } from '../../api/settings.ts';
import { fetchVersion } from '../../api/system.ts';
import { NotificationsCard } from './NotificationsCard.tsx';
import { HostAliasManager } from '../aliases/HostAliasManager.tsx';
import { AdvancedSettingsCard } from './AdvancedSettingsCard.tsx';
import { ApiTokensTab } from './ApiTokensTab.tsx';
import { DockerHostsTab } from './DockerHostsTab.tsx';
import { UnsavedChangesModal } from '../common/UnsavedChangesModal.tsx';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { useFlash } from '../../utils/hooks.ts';
import { useAiProviderConfig, settingsAiEnabled } from './useAiProviderConfig.ts';
import { AiProviderCard } from './AiProviderCard.tsx';
import { StickySaveBar } from './StickySaveBar.tsx';
import { AboutCard } from './AboutCard.tsx';
import { ChangePasswordCard } from './ChangePasswordCard.tsx';


export const pathToSettingsSubTab = (pathname: string): SettingsSubTab => {
  const clean = pathname.replace(/\/+$/, '').toLowerCase();
  if (
    clean === '/sources' ||
    clean.startsWith('/sources/') ||
    clean === '/settings/sources' ||
    clean.startsWith('/settings/sources/') ||
    clean === '/aliases' ||
    clean.startsWith('/aliases/') ||
    clean === '/settings/aliases' ||
    clean.startsWith('/settings/aliases/')
  ) {
    return 'sources';
  }
  if (
    clean === '/settings/api-tokens' ||
    clean.startsWith('/settings/api-tokens/') ||
    clean === '/settings/tokens' ||
    clean.startsWith('/settings/tokens/')
  ) {
    return 'tokens';
  }
  if (clean === '/settings/advanced' || clean.startsWith('/settings/advanced/')) {
    return 'advanced';
  }
  return 'app';
};

export const settingsSubTabToPath = (subTab: SettingsSubTab): string => {
  switch (subTab) {
    case 'sources':
      return '/settings/sources';
    case 'tokens':
      return '/settings/api-tokens';
    case 'advanced':
      return '/settings/advanced';
    case 'app':
    default:
      return '/settings/app';
  }
};

const INTERNAL_LOG_LEVEL_OPTIONS: SelectOption[] = [
  { value: 'WARNING', label: 'WARNING (Default - warnings & errors)' },
  { value: 'ERROR', label: 'ERROR (Errors & critical failures only)' },
  { value: 'CRITICAL', label: 'CRITICAL (Fatal system emergencies only)' },
  { value: 'INFO', label: 'INFO (All standard informational notices)' },
  { value: 'DEBUG', label: 'DEBUG (Detailed diagnostic traces)' },
  { value: 'DISABLED', label: 'DISABLED (Do not ingest internal logs)' },
];

export interface SettingsPanelProps {
  initialSubTab?: SettingsSubTab;
  initialAddIp?: string | null;
  initialEditDockerHostId?: number | null;
  initialDeletedDockerHostInfo?: { name: string; ip: string } | null;
  onAliasSaved?: () => void;
  onDockerHostSaved?: () => void;
  onClearSourcesState?: () => void;
  onDirtyChange?: (isDirty: boolean) => void;
  saveTriggerRef?: React.MutableRefObject<(() => Promise<boolean>) | null>;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  initialSubTab,
  initialAddIp,
  initialEditDockerHostId,
  initialDeletedDockerHostInfo,
  onAliasSaved,
  onDockerHostSaved,
  onClearSourcesState,
  onDirtyChange,
  saveTriggerRef,
}) => {
  const [activeAddIp, setActiveAddIp] = useState<string | null>(initialAddIp || null);

  useEffect(() => {
    if (initialAddIp) {
      setActiveAddIp(initialAddIp);
    }
  }, [initialAddIp]);
  const [settings, setSettings] = useState<SettingsResponseData | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Sub-tab state
  const [activeSubTab, setActiveSubTab] = useState<SettingsSubTab>(() => {
    if (initialSubTab) return initialSubTab;
    return pathToSettingsSubTab(window.location.pathname);
  });

  // The Advanced tab unmounts when another sub-tab is shown, so its unsaved edits are guarded
  // here. App tab edits live in this component and survive sub-tab switches.
  const [advancedDirty, setAdvancedDirty] = useState<boolean>(false);
  const advancedDirtyRef = useRef<boolean>(false);
  const advancedSaveRef = useRef<(() => Promise<boolean>) | null>(null);
  const [pendingSubTab, setPendingSubTab] = useState<SettingsSubTab | null>(null);
  const activeSubTabRef = useRef<SettingsSubTab>(activeSubTab);
  activeSubTabRef.current = activeSubTab;

  const commitSubTabChange = (nextSubTab: SettingsSubTab) => {
    setActiveSubTab(nextSubTab);
    if (nextSubTab !== 'advanced') {
      advancedDirtyRef.current = false;
      setAdvancedDirty(false);
    }
    const targetPath = settingsSubTabToPath(nextSubTab);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  };

  const handleSubTabChange = (nextSubTab: SettingsSubTab) => {
    if (activeSubTab === 'advanced' && nextSubTab !== 'advanced' && advancedDirtyRef.current) {
      setPendingSubTab(nextSubTab);
      return;
    }
    commitSubTabChange(nextSubTab);
  };

  const handleAdvancedDirtyChange = useCallback((dirty: boolean) => {
    advancedDirtyRef.current = dirty;
    setAdvancedDirty(dirty);
  }, []);

  // Form states
  const ai = useAiProviderConfig(settings);
  const [internalLogLevel, setInternalLogLevel] = useState<string>('WARNING');
  const [checkForUpdates, setCheckForUpdates] = useState<boolean>(true);
  const [digestEnabled, setDigestEnabled] = useState<boolean>(false);
  const [digestChannelId, setDigestChannelId] = useState<number | null>(null);
  const [digestScheduleTime, setDigestScheduleTime] = useState<string>('09:00');
  const serverTimezone = settings?.server_timezone || undefined;

  // Save feedback state
  const [isSavingSettings, setIsSavingSettings] = useState<boolean>(false);
  // The "saved" mark, with a notice shown beside it when the save changed something else
  const [savedFlash, flashSaved, setSavedFlash] = useFlash<{ notice: string | null }>(3000);
  const saveInlineSuccess = savedFlash !== null;
  const saveNotice = savedFlash?.notice ?? null;
  const [saveInlineError, setSaveInlineError] = useState<string | null>(null);

  // Track dirty state against loaded baseline settings
  const baselineDigestEnabled = settings ? Boolean(settings.daily_digest_enabled) : false;
  const baselineDigestChannelId = settings ? (settings.daily_digest_channel_id ?? null) : null;
  const baselineDigestScheduleTime = settings ? (settings.daily_digest_schedule_time || '09:00') : '09:00';

  const isDirty = Boolean(
    settings &&
    (ai.isDirty ||
      internalLogLevel !== (settings.internal_log_level || 'WARNING') ||
      checkForUpdates !== (settings.check_for_updates ?? true) ||
      digestEnabled !== baselineDigestEnabled ||
      digestChannelId !== baselineDigestChannelId ||
      digestScheduleTime !== baselineDigestScheduleTime)
  );
  const isDirtyRef = useRef<boolean>(false);
  isDirtyRef.current = isDirty;

  // Application version & update state
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);

  const applySettingsResponse = (settRes: SettingsResponseData) => {
    setSettings(settRes);
    ai.applySettings(settRes);
    setInternalLogLevel(settRes.internal_log_level || 'WARNING');
    setCheckForUpdates(settRes.check_for_updates ?? true);
    setDigestEnabled(Boolean(settRes.daily_digest_enabled));
    setDigestChannelId(settRes.daily_digest_channel_id ?? null);
    setDigestScheduleTime(settRes.daily_digest_schedule_time || '09:00');
  };

  // Child tabs (API & MCP, Advanced, notifications) save their own settings. They only refresh
  // the baseline: the App tab form is replaced from the server only when it has no edits of its own.
  const refreshBaseline = async () => {
    try {
      const settRes = await fetchSettings(true);
      if (isDirtyRef.current) {
        setSettings(settRes);
      } else {
        applySettingsResponse(settRes);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to refresh system settings.');
    }
  };

  // Emptying the key field is a pending change like any other edit: it is sent as an empty
  // key by the normal Save, so leaving the field never saves (or discards) anything by itself.
  const handleApiKeyEnter = async () => {
    await executeSave();
  };

  // Always read the server state on mount: the module-level cache would hide changes made elsewhere
  // (digest last run, Docker host 1 sync) while this panel was unmounted.
  const loadAllData = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);

      const settRes = await fetchSettings(true);
      applySettingsResponse(settRes);

      // Load models for provider if enabled
      if (settingsAiEnabled(settRes)) {
        ai.loadModels(settRes.ai_provider);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load system settings.');
    } finally {
      setIsLoading(false);
    }
  };

  const loadVersionData = async (refresh: boolean = false) => {
    try {
      const v = await fetchVersion(refresh);
      setVersionInfo(v);
    } catch {
      // Graceful fallback
    }
  };

  useEffect(() => {
    loadAllData();
    loadVersionData();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loads once when Settings opens; the loaders are new functions each render
  }, []);

  useEffect(() => {
    if (initialSubTab) {
      setActiveSubTab(initialSubTab);
      const target = settingsSubTabToPath(initialSubTab);
      if (window.location.pathname !== target) {
        window.history.pushState(null, '', target);
      }
    }
  }, [initialSubTab]);

  useEffect(() => {
    const handlePopState = () => {
      const nextSubTab = pathToSettingsSubTab(window.location.pathname);
      if (
        activeSubTabRef.current === 'advanced' &&
        nextSubTab !== 'advanced' &&
        advancedDirtyRef.current
      ) {
        // Stay on the Advanced tab and ask first, like a click on another sub-tab would.
        window.history.pushState(null, '', settingsSubTabToPath('advanced'));
        setPendingSubTab(nextSubTab);
        return;
      }
      setActiveSubTab(nextSubTab);
      if (nextSubTab !== 'advanced') {
        advancedDirtyRef.current = false;
        setAdvancedDirty(false);
      }
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    const clean = window.location.pathname.replace(/\/+$/, '').toLowerCase();
    if (clean === '/aliases' || clean === '/settings/aliases') {
      window.history.replaceState(null, '', '/settings/sources');
    }
  }, []);

  // Notify the parent of unsaved edits on any sub-tab: the Application tab form stays alive while
  // other sub-tabs are shown, and the Advanced tab reports its own edits.
  const hasUnsavedChanges = isDirty || (activeSubTab === 'advanced' && advancedDirty);
  useEffect(() => {
    onDirtyChange?.(hasUnsavedChanges);
  }, [hasUnsavedChanges, onDirtyChange]);

  // Warn on page unload/refresh when unsaved changes exist
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
        return '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  const handleResetChanges = () => {
    if (!settings) return;
    applySettingsResponse(settings);
    ai.resetModelPickers();

    if (settingsAiEnabled(settings) && ai.aiProvider !== settings.ai_provider) {
      ai.loadModels(settings.ai_provider);
    }
  };

  const executeSave = async (): Promise<boolean> => {
    if (!isDirty || isSavingSettings) return false;

    try {
      setIsSavingSettings(true);
      setErrorMsg(null);
      setSaveInlineError(null);
      setSavedFlash(null);

      if (digestEnabled && digestScheduleTime && !/^([01]\d|2[0-3]):([0-5]\d)$/.test(digestScheduleTime.trim())) {
        const msg = 'Enter a valid 24-hour time for the daily digest schedule, e.g. 09:00 or 23:30.';
        setErrorMsg(msg);
        setSaveInlineError(msg);
        return false;
      }

      const saveRes = await updateSettings({
        ...ai.buildSavePayload(),
        internal_log_level: internalLogLevel,
        check_for_updates: checkForUpdates,
        daily_digest_enabled: digestEnabled,
        daily_digest_channel_id: digestChannelId,
        daily_digest_schedule_time: digestScheduleTime ? digestScheduleTime.trim() : '09:00',
      });

      // Reload updated settings as baseline
      const settRes = await fetchSettings();
      applySettingsResponse(settRes);

      // Refresh model list with newly saved configuration if AI is enabled
      if (settingsAiEnabled(settRes)) {
        ai.loadModels(settRes.ai_provider);
      }

      // Refresh version info to reflect updated check_for_updates setting
      loadVersionData(true);

      const keyCleared = Boolean(saveRes && saveRes.ai_api_key_cleared);
      flashSaved(
        {
          notice: keyCleared
            ? 'API key cleared because the endpoint changed. Enter the key again if the new endpoint needs one.'
            : null,
        },
        keyCleared ? 10000 : 3000
      );
      return true;
    } catch (err: any) {
      const msg = err.message || 'Failed to update settings.';
      setErrorMsg(msg);
      setSaveInlineError(msg);
      return false;
    } finally {
      setIsSavingSettings(false);
    }
  };

  const handleSaveSettings = async (e?: React.FormEvent) => {
    if (e && e.preventDefault) {
      e.preventDefault();
    }
    await executeSave();
  };

  // Saves every set of unsaved edits (Application tab form and the Advanced tab card).
  // Resolves false as soon as one save fails so the caller does not leave the page.
  const saveAllChanges = async (): Promise<boolean> => {
    if (isDirtyRef.current) {
      const appSaved = await executeSave();
      if (!appSaved) return false;
    }
    if (advancedDirtyRef.current && advancedSaveRef.current) {
      return advancedSaveRef.current();
    }
    return true;
  };

  // Expose programmatic save function for navigation guards (Save & Continue).
  useEffect(() => {
    if (saveTriggerRef) {
      saveTriggerRef.current = saveAllChanges;
    }
    return () => {
      if (saveTriggerRef) {
        saveTriggerRef.current = null;
      }
    };
  });

  if (isLoading && !settings) {
    return (
      <div className="p-8 text-center text-fg-muted font-mono text-xs flex items-center justify-center gap-2">
        <RefreshCw className="w-4 h-4 animate-spin text-accent-500" />
        <span>Loading system settings...</span>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-3 sm:p-6 space-y-6 sm:space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-base font-bold text-fg-strong flex items-center gap-2">
          <Settings className="w-5 h-5 text-accent-500" />
          <span>System Configuration</span>
        </h2>
        <p className="text-xs text-fg-muted mt-0.5">
          Configure application logging levels, on-demand AI root-cause diagnosis providers, and account security.
        </p>
      </div>

      {errorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      <Tabs
        label="Settings sections"
        active={activeSubTab}
        onSelect={handleSubTabChange}
        items={[
          { id: 'app', label: 'Application', icon: <Settings /> },
          { id: 'sources', label: 'Sources', icon: <Server /> },
          { id: 'tokens', label: 'API & MCP Access', icon: <Key /> },
          { id: 'advanced', label: 'Advanced', icon: <Shield /> },
        ]}
      />

      {/* Sub-tab 1: Application */}
      {activeSubTab === 'app' && (
        <div className="space-y-6">
          {/* Settings Form: Logging & AI */}
          <form id="settings-form" onSubmit={handleSaveSettings} className="space-y-6">
        {/* Application Self-Logging Section */}
        <Card>
          <CardHeader
            icon={<FileText />}
            title="Internal Application Logging"
            description="Configure minimum severity level for LogShed operational diagnostics captured into its database and stream."
            actions={
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-sm bg-canvas border border-line-subtle text-[11px] font-mono text-fg-muted">
                <span>Source: <code className="text-accent-400">logshed</code></span>
              </div>
            }
          />

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Internal Log Severity Threshold" id="internal-log-level">
              {({ id, labelId }) => (
                <SelectDropdown
                  variant="panel"
                  fieldClassName={SELECT_FIELD_CLASS}
                  id={id}
                  labelledBy={labelId}
                  label="Internal Log Severity Threshold"
                  options={INTERNAL_LOG_LEVEL_OPTIONS}
                  value={internalLogLevel}
                  onChange={setInternalLogLevel}
                />
              )}
            </Field>
            <div className="flex items-center text-xs text-fg-muted sm:pt-4">
              <span>
                Logs at or above this level are captured into LogShed. Logs from alerting, notifications and AI analysis are stored but never trigger alert rules, so a rule cannot fire itself. Ingestion pipeline and live stream logs are not captured.
              </span>
            </div>
          </div>
        </Card>

        {/* AI Provider Section */}
        <AiProviderCard ai={ai} onApiKeyEnter={handleApiKeyEnter} serverTimezone={serverTimezone} />

        {/* Notification Channels & Webhooks Section */}
        <NotificationsCard
          settings={settings}
          onSettingsSaved={refreshBaseline}
          digestEnabled={digestEnabled}
          onDigestEnabledChange={setDigestEnabled}
          digestChannelId={digestChannelId}
          onDigestChannelIdChange={setDigestChannelId}
          digestScheduleTime={digestScheduleTime}
          onDigestScheduleTimeChange={setDigestScheduleTime}
          isSavingSettings={isSavingSettings}
        />

        {/* Version Updates Section */}
        <Card>
          <CardHeader
            icon={<ArrowUpCircle />}
            title="Version Updates"
            description="Periodically check GitHub Container Registry for new stable releases and display notification badges"
          />

          <div className="pt-1">
            <Checkbox
              label="Check for new versions"
              checked={checkForUpdates}
              onChange={(e) => setCheckForUpdates(e.target.checked)}
            />
            <p className="text-xs text-fg-muted mt-1 pl-6">
              When disabled, LogShed will not query external registries or display update notifications.
            </p>
          </div>
        </Card>

        {/* Sticky Action Bar: floats while scrolling form, locks into place above password section */}
        <StickySaveBar
          isDirty={isDirty}
          isSavingSettings={isSavingSettings}
          saveInlineSuccess={saveInlineSuccess}
          saveNotice={saveNotice}
          saveInlineError={saveInlineError}
          onDiscard={handleResetChanges}
        />
          </form>

          {/* About LogShed Section */}
          <AboutCard versionInfo={versionInfo} checkForUpdates={checkForUpdates} />
        </div>
      )}

      {/* Sub-tab 2: Sources (Host Aliases + Docker Hosts) */}
      {activeSubTab === 'sources' && (
        <div className="space-y-6">
          <HostAliasManager
            initialAddIp={activeAddIp}
            onCancel={() => {
              setActiveAddIp(null);
              if (onClearSourcesState) onClearSourcesState();
            }}
            onAliasSaved={() => {
              setActiveAddIp(null);
              if (onAliasSaved) onAliasSaved();
            }}
          />
          <DockerHostsTab
            initialEditEndpointId={initialEditDockerHostId}
            initialDeletedHostInfo={initialDeletedDockerHostInfo}
            onHostSaved={onDockerHostSaved}
            onAddSyslogAlias={(targetIp) => setActiveAddIp(targetIp)}
            onClearSourcesState={onClearSourcesState}
          />
        </div>
      )}

      {/* Sub-tab 3: API & MCP Access */}
      {activeSubTab === 'tokens' && (
        <ApiTokensTab
          settings={settings}
          onSettingsSaved={refreshBaseline}
        />
      )}

      {/* Sub-tab 4: Advanced */}
      {activeSubTab === 'advanced' && (
        <div className="space-y-6">
          {/* Advanced System Settings */}
          <AdvancedSettingsCard
            settings={settings}
            onSettingsSaved={refreshBaseline}
            onDirtyChange={handleAdvancedDirtyChange}
            saveTriggerRef={advancedSaveRef}
          />

          {/* Admin Password Reset Section */}
          <ChangePasswordCard />
        </div>
      )}

      {/* Unsaved Advanced edits: asked before another sub-tab replaces the Advanced tab */}
      <UnsavedChangesModal
        isOpen={pendingSubTab !== null}
        onKeepEditing={() => setPendingSubTab(null)}
        onDiscard={() => {
          const target = pendingSubTab;
          setPendingSubTab(null);
          if (target) commitSubTabChange(target);
        }}
        onSave={async () => {
          const saved = advancedSaveRef.current ? await advancedSaveRef.current() : true;
          if (saved) {
            const target = pendingSubTab;
            setPendingSubTab(null);
            if (target) commitSubTabChange(target);
          }
        }}
        message="You have unsaved changes in Advanced System Settings. Switching tabs now will discard those edits."
        discardLabel="Discard & Leave"
        zIndex="z-50"
      />
    </div>
  );
};

export default SettingsPanel;
