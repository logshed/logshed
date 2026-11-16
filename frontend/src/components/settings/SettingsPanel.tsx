import React, { useCallback, useEffect, useMemo, useState, useRef } from 'react';
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
import { SaveModeNote } from '../common/SaveModeNote.tsx';
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
import { ConfigTransferCard } from './ConfigTransferCard.tsx';
import { useSavedForm } from './useSavedForm.ts';


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

/** The Application tab's own fields (the AI provider has its own hook). */
interface AppFormValues {
  internal_log_level: string;
  check_for_updates: boolean;
  daily_digest_enabled: boolean;
  daily_digest_channel_id: number | null;
  daily_digest_schedule_time: string;
}

const APP_FORM_DEFAULTS: AppFormValues = {
  internal_log_level: 'WARNING',
  check_for_updates: true,
  daily_digest_enabled: false,
  daily_digest_channel_id: null,
  daily_digest_schedule_time: '09:00',
};

const appFormValues = (s: SettingsResponseData): AppFormValues => ({
  internal_log_level: s.internal_log_level || 'WARNING',
  check_for_updates: s.check_for_updates ?? true,
  daily_digest_enabled: Boolean(s.daily_digest_enabled),
  daily_digest_channel_id: s.daily_digest_channel_id ?? null,
  daily_digest_schedule_time: s.daily_digest_schedule_time || '09:00',
});

// Sub-tabs with a form of their own (the Save Changes bar). They unmount when another sub-tab is
// shown, so their unsaved edits are asked about first.
const OWN_FORM_SUBTABS: Partial<Record<SettingsSubTab, string>> = {
  tokens: 'You have an unsaved change to the MCP setting. Switching tabs now will discard it.',
  advanced: 'You have unsaved changes in Advanced System Settings. Switching tabs now will discard those edits.',
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
  onDirtyChange?: (isDirty: boolean) => void;
  saveTriggerRef?: React.MutableRefObject<(() => Promise<boolean>) | null>;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  initialSubTab,
  onDirtyChange,
  saveTriggerRef,
}) => {
  const [settings, setSettings] = useState<SettingsResponseData | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Sub-tab state
  const [activeSubTab, setActiveSubTab] = useState<SettingsSubTab>(() => {
    if (initialSubTab) return initialSubTab;
    return pathToSettingsSubTab(window.location.pathname);
  });

  // The API & MCP and Advanced tabs unmount when another sub-tab is shown, so their unsaved edits
  // are guarded here (OWN_FORM_SUBTABS). App tab edits live in this component and survive
  // sub-tab switches.
  const [subTabDirty, setSubTabDirty] = useState<boolean>(false);
  const subTabDirtyRef = useRef<boolean>(false);
  const subTabSaveRef = useRef<(() => Promise<boolean>) | null>(null);
  const [pendingSubTab, setPendingSubTab] = useState<SettingsSubTab | null>(null);
  const activeSubTabRef = useRef<SettingsSubTab>(activeSubTab);
  activeSubTabRef.current = activeSubTab;

  const commitSubTabChange = (nextSubTab: SettingsSubTab) => {
    setActiveSubTab(nextSubTab);
    if (nextSubTab !== activeSubTabRef.current) {
      subTabDirtyRef.current = false;
      setSubTabDirty(false);
    }
    const targetPath = settingsSubTabToPath(nextSubTab);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  };

  const handleSubTabChange = (nextSubTab: SettingsSubTab) => {
    if (nextSubTab !== activeSubTab && subTabDirtyRef.current) {
      setPendingSubTab(nextSubTab);
      return;
    }
    commitSubTabChange(nextSubTab);
  };

  const handleSubTabDirtyChange = useCallback((dirty: boolean) => {
    subTabDirtyRef.current = dirty;
    setSubTabDirty(dirty);
  }, []);

  // Form states
  const ai = useAiProviderConfig(settings);
  const savedAppValues = useMemo(() => (settings ? appFormValues(settings) : null), [settings]);
  const appForm = useSavedForm<AppFormValues>(savedAppValues, APP_FORM_DEFAULTS, {
    daily_digest_schedule_time: (v) => v.trim() || '09:00',
  });
  const {
    internal_log_level: internalLogLevel,
    check_for_updates: checkForUpdates,
    daily_digest_enabled: digestEnabled,
    daily_digest_channel_id: digestChannelId,
    daily_digest_schedule_time: digestScheduleTime,
  } = appForm.values;
  const setAppField = appForm.set;
  const serverTimezone = settings?.server_timezone || undefined;

  // Save feedback state
  const [isSavingSettings, setIsSavingSettings] = useState<boolean>(false);
  // The "saved" mark, with a notice shown beside it when the save changed something else
  const [savedFlash, flashSaved, setSavedFlash] = useFlash<{ notice: string | null }>(3000);
  const saveInlineSuccess = savedFlash !== null;
  const saveNotice = savedFlash?.notice ?? null;
  const [saveInlineError, setSaveInlineError] = useState<string | null>(null);

  const isDirty = Boolean(settings && (ai.isDirty || appForm.isDirty));
  const isDirtyRef = useRef<boolean>(false);
  isDirtyRef.current = isDirty;

  // Application version & update state
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);

  // The App tab's own fields follow the saved settings wherever they are not edited (useSavedForm);
  // the AI provider form is replaced as a whole.
  const applySettingsResponse = (settRes: SettingsResponseData) => {
    setSettings(settRes);
    ai.applySettings(settRes);
  };

  // Child tabs (API & MCP, Advanced, notifications) save their own settings. They only refresh
  // the baseline: an App tab field keeps an unsaved edit, and the AI provider form is replaced
  // only when it has no edits of its own.
  const aiDirtyRef = useRef<boolean>(false);
  aiDirtyRef.current = ai.isDirty;
  const refreshBaseline = async () => {
    try {
      const settRes = await fetchSettings(true);
      if (aiDirtyRef.current) {
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
      const shown = activeSubTabRef.current;
      if (nextSubTab !== shown && subTabDirtyRef.current) {
        // Stay on the shown tab and ask first, like a click on another sub-tab would.
        window.history.pushState(null, '', settingsSubTabToPath(shown));
        setPendingSubTab(nextSubTab);
        return;
      }
      setActiveSubTab(nextSubTab);
      if (nextSubTab !== shown) {
        subTabDirtyRef.current = false;
        setSubTabDirty(false);
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
  // other sub-tabs are shown, and the API & MCP and Advanced tabs report their own edits.
  const hasUnsavedChanges = isDirty || (OWN_FORM_SUBTABS[activeSubTab] !== undefined && subTabDirty);
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
    appForm.reset();
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

      // Only what changed is sent, so a save never writes back a value changed elsewhere since
      // this page loaded (another tab, or another browser).
      const appChanges = appForm.changes();
      const saveRes = await updateSettings({
        ...ai.buildChangedPayload(),
        ...appChanges,
      });

      // Reload updated settings as baseline
      const settRes = await fetchSettings();
      applySettingsResponse(settRes);

      // Refresh model list with newly saved configuration if AI is enabled
      if (settingsAiEnabled(settRes)) {
        ai.loadModels(settRes.ai_provider);
      }

      if (appChanges.check_for_updates !== undefined) {
        loadVersionData(true);
      }

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

  // Saves every set of unsaved edits (Application tab form and the shown API & MCP or Advanced tab).
  // Resolves false as soon as one save fails so the caller does not leave the page.
  const saveAllChanges = async (): Promise<boolean> => {
    if (isDirtyRef.current) {
      const appSaved = await executeSave();
      if (!appSaved) return false;
    }
    if (subTabDirtyRef.current && subTabSaveRef.current) {
      return subTabSaveRef.current();
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
            note={<SaveModeNote mode="bar" />}
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
                  onChange={(v) => setAppField('internal_log_level', v)}
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
          onDigestEnabledChange={(v) => setAppField('daily_digest_enabled', v)}
          digestChannelId={digestChannelId}
          onDigestChannelIdChange={(v) => setAppField('daily_digest_channel_id', v)}
          digestScheduleTime={digestScheduleTime}
          onDigestScheduleTimeChange={(v) => setAppField('daily_digest_schedule_time', v)}
          isSavingSettings={isSavingSettings}
        />

        {/* Version Updates Section */}
        <Card>
          <CardHeader
            icon={<ArrowUpCircle />}
            title="Version Updates"
            description="Periodically check GitHub Container Registry for new stable releases and display notification badges"
            note={<SaveModeNote mode="bar" />}
          />

          <div className="pt-1">
            <Checkbox
              label="Check for new versions"
              checked={checkForUpdates}
              onChange={(e) => setAppField('check_for_updates', e.target.checked)}
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
          <HostAliasManager />
          <DockerHostsTab />
        </div>
      )}

      {/* Sub-tab 3: API & MCP Access */}
      {activeSubTab === 'tokens' && (
        <ApiTokensTab
          settings={settings}
          onSettingsSaved={refreshBaseline}
          onDirtyChange={handleSubTabDirtyChange}
          saveTriggerRef={subTabSaveRef}
        />
      )}

      {/* Sub-tab 4: Advanced */}
      {activeSubTab === 'advanced' && (
        <div className="space-y-6">
          {/* Advanced System Settings */}
          <AdvancedSettingsCard
            settings={settings}
            onSettingsSaved={refreshBaseline}
            onDirtyChange={handleSubTabDirtyChange}
            saveTriggerRef={subTabSaveRef}
          />

          {/* The whole configuration to a file and back */}
          <ConfigTransferCard serverTimezone={serverTimezone} onImported={refreshBaseline} />

          {/* Admin Password Reset Section */}
          <ChangePasswordCard />
        </div>
      )}

      {/* Unsaved edits on the API & MCP or Advanced tab: asked before another sub-tab replaces it */}
      <UnsavedChangesModal
        isOpen={pendingSubTab !== null}
        onKeepEditing={() => setPendingSubTab(null)}
        onDiscard={() => {
          const target = pendingSubTab;
          setPendingSubTab(null);
          if (target) commitSubTabChange(target);
        }}
        onSave={async () => {
          const saved = subTabSaveRef.current ? await subTabSaveRef.current() : true;
          if (saved) {
            const target = pendingSubTab;
            setPendingSubTab(null);
            if (target) commitSubTabChange(target);
          }
        }}
        message={OWN_FORM_SUBTABS[activeSubTab] ?? ''}
        discardLabel="Discard & Leave"
        zIndex="z-50"
      />
    </div>
  );
};

export default SettingsPanel;
