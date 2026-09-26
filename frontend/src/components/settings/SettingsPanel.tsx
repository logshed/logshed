import React, { useEffect, useState, useRef } from 'react';
import {
  Settings,
  Brain,
  Lock,
  Check,
  Save,
  AlertCircle,
  RefreshCw,
  RotateCcw,
  FileText,
  ArrowUp,
  ArrowDown,
  Plus,
  X,
  Info,
  ExternalLink,
  ArrowUpCircle,
  Server,
  Shield,
} from 'lucide-react';
import { AiModelInfo, VersionInfo, SettingsSubTab } from '../../types.ts';
import { fetchSettings, updateSettings, SettingsResponseData } from '../../api/settings.ts';
import { fetchVersion } from '../../api/system.ts';
import { getAiModels } from '../../api/ai.ts';
import { changePassword } from '../../api/auth.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import { DEFAULT_AI_MODEL, DEFAULT_SYSTEM_PROMPT, normalizePrompt, getOrdinalSuffix } from '../../utils/aiPrompt.ts';
import { NotificationsCard } from './NotificationsCard.tsx';
import { HostAliasManager } from '../aliases/HostAliasManager.tsx';

export type { SettingsSubTab };

export const pathToSettingsSubTab = (pathname: string): SettingsSubTab => {
  const clean = pathname.replace(/\/+$/, '').toLowerCase();
  if (
    clean === '/aliases' ||
    clean.startsWith('/aliases/') ||
    clean === '/settings/aliases' ||
    clean.startsWith('/settings/aliases/')
  ) {
    return 'aliases';
  }
  if (clean === '/settings/advanced' || clean.startsWith('/settings/advanced/')) {
    return 'advanced';
  }
  return 'app';
};

export const settingsSubTabToPath = (subTab: SettingsSubTab): string => {
  switch (subTab) {
    case 'aliases':
      return '/settings/aliases';
    case 'advanced':
      return '/settings/advanced';
    case 'app':
    default:
      return '/settings/app';
  }
};

export interface SettingsPanelProps {
  initialSubTab?: SettingsSubTab;
  initialAddIp?: string | null;
  onAliasSaved?: () => void;
  onDirtyChange?: (isDirty: boolean) => void;
  saveTriggerRef?: React.MutableRefObject<(() => Promise<boolean>) | null>;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  initialSubTab,
  initialAddIp,
  onAliasSaved,
  onDirtyChange,
  saveTriggerRef,
}) => {
  const { logout } = useAuth();
  const [settings, setSettings] = useState<SettingsResponseData | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Sub-tab state
  const [activeSubTab, setActiveSubTab] = useState<SettingsSubTab>(() => {
    if (initialSubTab) return initialSubTab;
    return pathToSettingsSubTab(window.location.pathname);
  });

  const handleSubTabChange = (nextSubTab: SettingsSubTab) => {
    setActiveSubTab(nextSubTab);
    const targetPath = settingsSubTabToPath(nextSubTab);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  };

  // Form states
  const [aiProvider, setAiProvider] = useState<'gemini' | 'openai' | 'openai_compatible'>('gemini');
  const [aiModel, setAiModel] = useState<string>(DEFAULT_AI_MODEL);
  const [aiFallbackModels, setAiFallbackModels] = useState<string>('');
  const [aiApiKey, setAiApiKey] = useState<string>('');
  const [aiBaseUrl, setAiBaseUrl] = useState<string>('');
  const [aiSystemPrompt, setAiSystemPrompt] = useState<string>(DEFAULT_SYSTEM_PROMPT);
  const [internalLogLevel, setInternalLogLevel] = useState<string>('WARNING');
  const [checkForUpdates, setCheckForUpdates] = useState<boolean>(true);

  // Model discovery states
  const [availableModels, setAvailableModels] = useState<AiModelInfo[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState<boolean>(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [hasApiKeyForProvider, setHasApiKeyForProvider] = useState<boolean>(true);
  const [modelsCachedAt, setModelsCachedAt] = useState<string | null>(null);
  const [isCustomModel, setIsCustomModel] = useState<boolean>(false);
  const [selectedFallbackToAdd, setSelectedFallbackToAdd] = useState<string>('');
  const [customFallbackInput, setCustomFallbackInput] = useState<string>('');
  const [showCustomFallbackInput, setShowCustomFallbackInput] = useState<boolean>(false);

  // Save feedback state
  const [isSavingSettings, setIsSavingSettings] = useState<boolean>(false);
  const [saveInlineSuccess, setSaveInlineSuccess] = useState<boolean>(false);
  const [saveInlineError, setSaveInlineError] = useState<string | null>(null);
  const saveSuccessTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (saveSuccessTimeoutRef.current) {
        clearTimeout(saveSuccessTimeoutRef.current);
      }
    };
  }, []);

  // Track dirty state against loaded baseline settings
  const isDirty = Boolean(
    settings &&
    (aiProvider !== settings.ai_provider ||
      aiModel !== (settings.ai_model || DEFAULT_AI_MODEL) ||
      aiFallbackModels !== (settings.ai_fallback_models || '') ||
      aiApiKey !== (settings.ai_api_key || '') ||
      aiBaseUrl !== (settings.ai_base_url || '') ||
      normalizePrompt(aiSystemPrompt) !== normalizePrompt(settings.ai_system_prompt || DEFAULT_SYSTEM_PROMPT) ||
      internalLogLevel !== (settings.internal_log_level || 'WARNING') ||
      checkForUpdates !== (settings.check_for_updates ?? true))
  );

  // Track if AI system instructions differ from system default
  const isAiSystemPromptModified = normalizePrompt(aiSystemPrompt) !== normalizePrompt(DEFAULT_SYSTEM_PROMPT);

  // Password reset state
  const [currentPwd, setCurrentPwd] = useState<string>('');
  const [newPwd, setNewPwd] = useState<string>('');
  const [confirmPwd, setConfirmPwd] = useState<string>('');
  const [isChangingPwd, setIsChangingPwd] = useState<boolean>(false);
  const [pwdMsg, setPwdMsg] = useState<{ text: string; isError: boolean } | null>(null);

  // Application version & update state
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);

  const loadModels = async (provider: string, forceRefresh: boolean = false) => {
    try {
      setIsLoadingModels(true);
      setModelsError(null);
      const res = await getAiModels(provider, forceRefresh);
      setAvailableModels(res.models || []);
      setHasApiKeyForProvider(res.has_api_key);
      setModelsCachedAt(res.cached_at || null);
      if (res.error) {
        setModelsError(res.error);
      }
    } catch (err: any) {
      setModelsError(err.message || 'Failed to fetch available models.');
      setAvailableModels([]);
    } finally {
      setIsLoadingModels(false);
    }
  };

  const handleProviderChange = (newProvider: 'gemini' | 'openai' | 'openai_compatible') => {
    setAiProvider(newProvider);
    setIsCustomModel(false);
    setSelectedFallbackToAdd('');
    setShowCustomFallbackInput(false);
    loadModels(newProvider);
  };

  // Fallback ordering helpers
  const fallbackList = aiFallbackModels
    ? aiFallbackModels.split(',').map((s) => s.trim()).filter(Boolean)
    : [];

  const moveFallback = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= fallbackList.length) return;
    const copy = [...fallbackList];
    const [removed] = copy.splice(index, 1);
    copy.splice(target, 0, removed);
    setAiFallbackModels(copy.join(', '));
  };

  const removeFallback = (index: number) => {
    const copy = fallbackList.filter((_, i) => i !== index);
    setAiFallbackModels(copy.join(', '));
  };

  const addFallback = (modelName: string) => {
    const trimmed = modelName.trim();
    if (!trimmed || fallbackList.includes(trimmed)) return;
    setAiFallbackModels([...fallbackList, trimmed].join(', '));
  };

  const handlePrimaryModelChange = (newModel: string) => {
    setAiModel(newModel);
    if (fallbackList.includes(newModel)) {
      const updated = fallbackList.filter((m) => m !== newModel);
      setAiFallbackModels(updated.join(', '));
    }
  };

  const loadAllData = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);

      const settRes = await fetchSettings();
      setSettings(settRes);

      // Populate form
      setAiProvider(settRes.ai_provider);
      setAiModel(settRes.ai_model || DEFAULT_AI_MODEL);
      setAiFallbackModels(settRes.ai_fallback_models || '');
      setAiApiKey(settRes.ai_api_key || '');
      setAiBaseUrl(settRes.ai_base_url || '');
      setAiSystemPrompt(settRes.ai_system_prompt || DEFAULT_SYSTEM_PROMPT);
      setInternalLogLevel(settRes.internal_log_level || 'WARNING');
      setCheckForUpdates(settRes.check_for_updates ?? true);

      // Load models for provider
      loadModels(settRes.ai_provider);
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
      setActiveSubTab(pathToSettingsSubTab(window.location.pathname));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    const clean = window.location.pathname.replace(/\/+$/, '').toLowerCase();
    if (clean === '/aliases') {
      window.history.replaceState(null, '', '/settings/aliases');
    }
  }, []);

  // Notify parent component of dirty state changes - only applies when App sub-tab is active
  useEffect(() => {
    if (activeSubTab === 'app') {
      onDirtyChange?.(isDirty);
    } else {
      onDirtyChange?.(false);
    }
  }, [activeSubTab, isDirty, onDirtyChange]);

  // Warn on page unload/refresh when unsaved changes exist
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (activeSubTab === 'app' && isDirty) {
        e.preventDefault();
        e.returnValue = '';
        return '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [activeSubTab, isDirty]);

  const handleResetChanges = () => {
    if (!settings) return;
    setAiProvider(settings.ai_provider);
    setAiModel(settings.ai_model || DEFAULT_AI_MODEL);
    setAiFallbackModels(settings.ai_fallback_models || '');
    setAiApiKey(settings.ai_api_key || '');
    setAiBaseUrl(settings.ai_base_url || '');
    setAiSystemPrompt(settings.ai_system_prompt || DEFAULT_SYSTEM_PROMPT);
    setInternalLogLevel(settings.internal_log_level || 'WARNING');
    setCheckForUpdates(settings.check_for_updates ?? true);
    setIsCustomModel(false);
    setSelectedFallbackToAdd('');
    setCustomFallbackInput('');
    setShowCustomFallbackInput(false);

    if (aiProvider !== settings.ai_provider) {
      loadModels(settings.ai_provider);
    }
  };

  const executeSave = async (): Promise<boolean> => {
    if (!isDirty || isSavingSettings) return false;

    try {
      setIsSavingSettings(true);
      setErrorMsg(null);
      setSaveInlineError(null);
      setSaveInlineSuccess(false);

      await updateSettings({
        ai_provider: aiProvider,
        ai_model: aiModel,
        ai_fallback_models: aiFallbackModels,
        ai_api_key: aiApiKey,
        ai_base_url: aiBaseUrl || null,
        ai_system_prompt: aiSystemPrompt,
        internal_log_level: internalLogLevel,
        check_for_updates: checkForUpdates,
      });

      // Reload updated settings as baseline
      const settRes = await fetchSettings();
      setSettings(settRes);
      setAiProvider(settRes.ai_provider);
      setAiModel(settRes.ai_model || DEFAULT_AI_MODEL);
      setAiFallbackModels(settRes.ai_fallback_models || '');
      setAiApiKey(settRes.ai_api_key || '');
      setAiBaseUrl(settRes.ai_base_url || '');
      setAiSystemPrompt(settRes.ai_system_prompt || DEFAULT_SYSTEM_PROMPT);
      setInternalLogLevel(settRes.internal_log_level || 'WARNING');
      setCheckForUpdates(settRes.check_for_updates ?? true);

      // Refresh model list with newly saved configuration
      loadModels(settRes.ai_provider);

      // Refresh version info to reflect updated check_for_updates setting
      loadVersionData(true);

      setSaveInlineSuccess(true);
      if (saveSuccessTimeoutRef.current) clearTimeout(saveSuccessTimeoutRef.current);
      saveSuccessTimeoutRef.current = setTimeout(() => {
        setSaveInlineSuccess(false);
      }, 3000);
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

  // Expose programmatic save function for navigation guards - only applies when App sub-tab is active
  useEffect(() => {
    if (saveTriggerRef) {
      if (activeSubTab === 'app') {
        saveTriggerRef.current = executeSave;
      } else {
        saveTriggerRef.current = null;
      }
    }
    return () => {
      if (saveTriggerRef) {
        saveTriggerRef.current = null;
      }
    };
  });

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPwd.length < 8) {
      setPwdMsg({ text: 'New password must be at least 8 characters long.', isError: true });
      return;
    }
    if (newPwd !== confirmPwd) {
      setPwdMsg({ text: 'New passwords do not match.', isError: true });
      return;
    }

    try {
      setIsChangingPwd(true);
      setPwdMsg(null);
      await changePassword(currentPwd, newPwd);
      sessionStorage.setItem('login_notice', 'Admin password changed successfully. Please sign in with your new password.');
      setPwdMsg({ text: 'Admin password changed successfully. Redirecting to sign in...', isError: false });
      setCurrentPwd('');
      setNewPwd('');
      setConfirmPwd('');
      setTimeout(() => {
        logout();
      }, 1000);
    } catch (err: any) {
      setPwdMsg({ text: err.message || 'Failed to update password.', isError: true });
    } finally {
      setIsChangingPwd(false);
    }
  };

  if (isLoading && !settings) {
    return (
      <div className="p-8 text-center text-slate-500 font-mono text-xs flex items-center justify-center gap-2">
        <RefreshCw className="w-4 h-4 animate-spin text-accent-500" />
        <span>Loading system settings...</span>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-3 sm:p-6 space-y-6 sm:space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
          <Settings className="w-5 h-5 text-accent-500" />
          <span>System Configuration</span>
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Configure application logging levels, on-demand AI root-cause analysis providers, and account security.
        </p>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Section Navigation Tabs */}
      <div className="flex items-center gap-2 border-b border-dark-700 pb-2">
        <button
          type="button"
          onClick={() => handleSubTabChange('app')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer border ${
            activeSubTab === 'app'
              ? 'bg-dark-800 text-accent-400 border-dark-600'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
          }`}
        >
          <Settings className="w-3.5 h-3.5" />
          <span>Application</span>
        </button>

        <button
          type="button"
          onClick={() => handleSubTabChange('aliases')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer border ${
            activeSubTab === 'aliases'
              ? 'bg-dark-800 text-accent-400 border-dark-600'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
          }`}
        >
          <Server className="w-3.5 h-3.5" />
          <span>Host Aliases</span>
        </button>

        <button
          type="button"
          onClick={() => handleSubTabChange('advanced')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer border ${
            activeSubTab === 'advanced'
              ? 'bg-dark-800 text-accent-400 border-dark-600'
              : 'text-slate-400 hover:text-slate-200 hover:bg-dark-900 border-dark-700 hover:border-dark-600'
          }`}
        >
          <Shield className="w-3.5 h-3.5" />
          <span>Advanced</span>
        </button>
      </div>

      {/* Sub-tab 1: Application */}
      {activeSubTab === 'app' && (
        <div className="space-y-6">
          {/* Settings Form: Logging & AI */}
          <form id="settings-form" onSubmit={handleSaveSettings} className="space-y-6">
        {/* Application Self-Logging Section */}
        <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <FileText className="w-4 h-4 text-accent-500" />
                <span>Internal Application Logging</span>
              </h3>
              <p className="text-[11px] text-slate-400 mt-1">
                Configure minimum severity level for LogShed operational diagnostics captured into its database and stream.
              </p>
            </div>
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-dark-950 border border-dark-800 text-[11px] font-mono text-slate-400 shrink-0 self-start sm:self-auto">
              <span>Source: <code className="text-accent-400">logshed</code></span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Internal Log Severity Threshold
              </label>
              <select
                aria-label="Internal Log Severity Threshold"
                value={internalLogLevel}
                onChange={(e) => setInternalLogLevel(e.target.value)}
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
              >
                <option value="WARNING">WARNING (Default - warnings &amp; errors)</option>
                <option value="ERROR">ERROR (Errors &amp; critical failures only)</option>
                <option value="CRITICAL">CRITICAL (Fatal system emergencies only)</option>
                <option value="INFO">INFO (All standard informational notices)</option>
                <option value="DEBUG">DEBUG (Detailed diagnostic traces)</option>
                <option value="DISABLED">DISABLED (Do not ingest internal logs)</option>
              </select>
            </div>
            <div className="flex items-center text-[11px] text-slate-400 sm:pt-4">
              <span>
                Logs at or above this level are captured into LogShed. Ingestion pipelines, database tasks, and SSE streams include recursion suppression to prevent loops.
              </span>
            </div>
          </div>
        </section>

        {/* AI Provider Section */}
        <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Brain className="w-4 h-4 text-accent-500" />
                <span>On-Demand AI Provider Configuration</span>
              </h3>
              <p className="text-[11px] text-slate-400 mt-1">
                Fernet encryption secures API keys against exposure in database exports, disk clones, and backups.
              </p>
            </div>
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-950/60 border border-emerald-800 text-[11px] font-mono text-emerald-400 shrink-0 self-start sm:self-auto">
              <Lock className="w-3 h-3 text-emerald-400" />
              <span>Keys encrypted at rest</span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                AI Provider
              </label>
              <select
                value={aiProvider}
                onChange={(e) => handleProviderChange(e.target.value as any)}
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
              >
                <option value="gemini">Google Gemini</option>
                <option value="openai">OpenAI</option>
                <option value="openai_compatible">OpenAI-Compatible (Ollama / vLLM / LocalAI)</option>
              </select>
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                API Key (Masked)
              </label>
              <input
                type="password"
                value={aiApiKey}
                onChange={(e) => setAiApiKey(e.target.value)}
                placeholder="Enter API key or leave ******** to preserve"
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>

            {aiProvider === 'openai_compatible' && (
              <div className="sm:col-span-2">
                <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  Custom Base URL (Optional)
                </label>
                <input
                  type="text"
                  value={aiBaseUrl}
                  onChange={(e) => setAiBaseUrl(e.target.value)}
                  placeholder="http://host.docker.internal:11434/v1"
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
              </div>
            )}

            {/* API Key Missing Notice */}
            {!hasApiKeyForProvider && aiProvider !== 'openai_compatible' && (
              <div className="sm:col-span-2 p-3 bg-amber-950/40 border border-amber-800/60 rounded-lg flex items-start gap-2.5 text-amber-300 text-xs font-mono">
                <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <span className="font-semibold text-amber-200">API Key Required to Discover Models:</span>
                  <p className="text-[11px] text-amber-300/80 mt-0.5">
                    Enter and save your API key above to query {aiProvider === 'gemini' ? 'Google Gemini' : 'OpenAI'} and populate available text models.
                  </p>
                </div>
              </div>
            )}

            {/* Models Error Banner */}
            {modelsError && (
              <div className="sm:col-span-2 p-2.5 bg-dark-950 border border-dark-700 rounded-lg flex items-center justify-between text-xs font-mono text-slate-400">
                <div className="flex items-center gap-2">
                  <AlertCircle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  <span className="text-[11px] text-amber-300/90">{modelsError}</span>
                </div>
                <button
                  type="button"
                  onClick={() => loadModels(aiProvider, true)}
                  className="text-[10px] text-accent-400 hover:text-accent-300 underline cursor-pointer"
                >
                  Retry
                </button>
              </div>
            )}

            {/* Model Discovery & Ordering Section Header */}
            <div className="sm:col-span-2 pt-2 border-t border-dark-800 flex items-center justify-between">
              <div>
                <label className="block text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                  Model Selection & Failover Ordering
                </label>
                <p className="text-[11px] text-slate-500">
                  Select your default primary model and configure fallback models in priority order.{modelsCachedAt ? ` (Cache refreshed: ${new Date(modelsCachedAt).toLocaleTimeString()})` : ''}
                </p>
              </div>
              <button
                type="button"
                onClick={() => loadModels(aiProvider, true)}
                disabled={isLoadingModels}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-dark-800 hover:bg-dark-750 border border-dark-700 text-xs text-slate-300 transition cursor-pointer disabled:opacity-50"
                title="Query provider API for active models"
              >
                <RefreshCw className={`w-3.5 h-3.5 text-accent-400 ${isLoadingModels ? 'animate-spin' : ''}`} />
                <span>{isLoadingModels ? 'Fetching Models...' : 'Refresh Models'}</span>
              </button>
            </div>

            {/* Default Primary Model */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="block text-[11px] font-semibold text-slate-400 uppercase">
                  Default Primary Model
                </label>
                {isCustomModel && (
                  <button
                    type="button"
                    onClick={() => {
                      setIsCustomModel(false);
                      const validModels = availableModels.filter(
                        (m) => !fallbackList.includes(m.id)
                      );
                      if (validModels.length > 0) {
                        handlePrimaryModelChange(validModels[0].id);
                      } else if (availableModels.length > 0) {
                        handlePrimaryModelChange(availableModels[0].id);
                      }
                    }}
                    className="text-[10px] text-accent-400 hover:text-accent-300 underline cursor-pointer"
                  >
                    Use dropdown instead
                  </button>
                )}
              </div>

              {availableModels.length > 0 && !isCustomModel ? (
                <select
                  value={availableModels.some((m) => m.id === aiModel) ? aiModel : '__custom__'}
                  onChange={(e) => {
                    if (e.target.value === '__custom__') {
                      setIsCustomModel(true);
                    } else {
                      handlePrimaryModelChange(e.target.value);
                    }
                  }}
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 focus:outline-hidden focus:border-accent-500 font-mono"
                >
                  {availableModels
                    .filter((m) => m.id === aiModel || !fallbackList.includes(m.id))
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id} {m.supports_thinking ? ' [Reasoning]' : ''}
                      </option>
                    ))}
                  {!availableModels.some((m) => m.id === aiModel) && aiModel && (
                    <option value={aiModel}>{aiModel} (Current / Custom)</option>
                  )}
                  <option value="__custom__">Custom model name...</option>
                </select>
              ) : (
                <input
                  type="text"
                  value={aiModel}
                  onChange={(e) => handlePrimaryModelChange(e.target.value)}
                  placeholder={aiProvider === 'gemini' ? DEFAULT_AI_MODEL : aiProvider === 'openai' ? 'gpt-4o' : 'llama3.2'}
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
              )}

              <p className="text-[10px] text-slate-500 mt-1">
                Queried first for all log analysis requests.
              </p>
            </div>

            {/* Fallback Models Ordered List */}
            <div className="space-y-2">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase">
                Fallback Models (Sequential Order)
              </label>

              {fallbackList.length === 0 ? (
                <div className="p-3 bg-dark-950 border border-dark-800 rounded-lg text-[11px] text-slate-500 font-mono">
                  No fallback models configured. Add a model below to enable automatic failover.
                </div>
              ) : (
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {fallbackList.map((fb, idx) => {
                    const modelInfo = availableModels.find((m) => m.id === fb);
                    const isThinking = modelInfo?.supports_thinking;
                    return (
                      <div
                        key={idx}
                        className="flex items-center justify-between p-2 bg-dark-950 border border-dark-800 rounded text-xs font-mono"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] font-semibold shrink-0">
                            {`${getOrdinalSuffix(idx + 1)} Fallback`}
                          </span>
                          <span className="text-slate-200 font-medium truncate">{fb}</span>
                          {isThinking && (
                            <span className="px-1 py-0.2 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 text-[9px] shrink-0">
                              Reasoning
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          <button
                            type="button"
                            disabled={idx === 0}
                            onClick={() => moveFallback(idx, -1)}
                            className="p-1 text-slate-400 hover:text-slate-200 disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer"
                            title="Move up in priority"
                            aria-label="Move fallback up"
                          >
                            <ArrowUp className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            disabled={idx === fallbackList.length - 1}
                            onClick={() => moveFallback(idx, 1)}
                            className="p-1 text-slate-400 hover:text-slate-200 disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer"
                            title="Move down in priority"
                            aria-label="Move fallback down"
                          >
                            <ArrowDown className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => removeFallback(idx)}
                            className="p-1 text-red-400 hover:text-red-300 cursor-pointer"
                            title="Remove fallback model"
                            aria-label="Remove fallback"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Add Fallback Model Selector */}
              <div className="space-y-1.5 pt-1">
                {!showCustomFallbackInput ? (
                  <div className="flex items-center gap-2 w-full">
                    <select
                      value={selectedFallbackToAdd}
                      onChange={(e) => {
                        if (e.target.value === '__custom_fallback__') {
                          setShowCustomFallbackInput(true);
                          setSelectedFallbackToAdd('');
                        } else {
                          setSelectedFallbackToAdd(e.target.value);
                        }
                      }}
                      className="flex-1 min-w-0 bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono truncate"
                    >
                      <option value="">-- Add Fallback Model --</option>
                      {availableModels
                        .filter((m) => m.id !== aiModel && !fallbackList.includes(m.id))
                        .map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.id} {m.supports_thinking ? ' [Reasoning]' : ''}
                          </option>
                        ))}
                      <option value="__custom_fallback__">Custom fallback model...</option>
                    </select>
                    <button
                      type="button"
                      disabled={!selectedFallbackToAdd}
                      onClick={() => {
                        addFallback(selectedFallbackToAdd);
                        setSelectedFallbackToAdd('');
                      }}
                      className="shrink-0 px-3 py-1.5 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded text-xs text-slate-200 font-mono flex items-center gap-1 disabled:opacity-40 cursor-pointer"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>Add</span>
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 w-full">
                    <input
                      type="text"
                      value={customFallbackInput}
                      onChange={(e) => setCustomFallbackInput(e.target.value)}
                      placeholder="Enter custom model ID"
                      className="flex-1 min-w-0 bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                    />
                    <button
                      type="button"
                      disabled={!customFallbackInput.trim()}
                      onClick={() => {
                        addFallback(customFallbackInput);
                        setCustomFallbackInput('');
                        setShowCustomFallbackInput(false);
                      }}
                      className="shrink-0 px-3 py-1.5 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded text-xs text-slate-200 font-mono flex items-center gap-1 disabled:opacity-40 cursor-pointer"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>Add</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setShowCustomFallbackInput(false);
                        setCustomFallbackInput('');
                      }}
                      className="shrink-0 p-1.5 text-slate-400 hover:text-slate-200 cursor-pointer"
                      title="Cancel custom fallback"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* AI System Instructions Card */}
            <div className="sm:col-span-2 pt-3 border-t border-dark-800 space-y-2">
              <div className="flex items-center justify-between">
                <div>
                  <label className="block text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                    AI System Instructions (LLM Persona)
                  </label>
                  <p className="text-[11px] text-slate-500">
                    System instructions that establish the LLM's diagnostic persona, reasoning guidelines, and response structure.
                  </p>
                </div>
                {isAiSystemPromptModified && (
                  <button
                    type="button"
                    onClick={() => setAiSystemPrompt(DEFAULT_SYSTEM_PROMPT)}
                    className="flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300 transition cursor-pointer"
                    title="Reset instructions to system default"
                  >
                    <RotateCcw className="w-3 h-3" />
                    <span>Reset to Default</span>
                  </button>
                )}
              </div>
              <textarea
                value={aiSystemPrompt}
                onChange={(e) => setAiSystemPrompt(e.target.value)}
                rows={7}
                placeholder="Enter system instructions..."
                className="w-full bg-dark-950 border border-dark-700 rounded-lg p-3 text-xs text-slate-200 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono leading-relaxed resize-y"
              />
            </div>
          </div>
        </section>

        {/* Notification Channels & Webhooks Section */}
        <NotificationsCard />

        {/* Version Updates Section */}
        <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <ArrowUpCircle className="w-4 h-4 text-accent-500" />
                <span>Version Updates</span>
              </h3>
              <p className="text-[11px] text-slate-400 mt-1">
                Periodically check GitHub Container Registry for new stable releases and display notification badges
              </p>
            </div>
          </div>

          <div className="pt-1">
            <label className="flex items-center gap-2.5 text-xs text-slate-200 cursor-pointer select-none">
              <input
                type="checkbox"
                aria-label="Check for new versions"
                checked={checkForUpdates}
                onChange={(e) => setCheckForUpdates(e.target.checked)}
                className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
              />
              <span className="font-medium">Check for new versions</span>
            </label>
            <p className="text-[11px] text-slate-400 mt-1 pl-6.5">
              When disabled, LogShed will not query external registries or display update notifications.
            </p>
          </div>
        </section>

        {/* Sticky Action Bar: floats while scrolling form, locks into place above password section */}
        {(isDirty || saveInlineSuccess || saveInlineError) && (
          <aside
            aria-label="Unsaved changes bar"
            className={`sticky bottom-4 z-30 bg-dark-900/95 backdrop-blur-md border rounded-xl p-3 sm:px-5 sm:py-3 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-3 animate-in fade-in slide-in-from-bottom-2 ${saveInlineSuccess
                ? 'border-emerald-500/40'
                : saveInlineError
                  ? 'border-red-500/40'
                  : 'border-amber-500/40'
              }`}
          >
            <div className="flex items-center gap-2 text-xs font-medium">
              {saveInlineSuccess ? (
                <div className="flex items-center gap-2 text-emerald-400 font-mono">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Settings saved successfully!</span>
                </div>
              ) : saveInlineError ? (
                <div className="flex items-center gap-2 text-red-400 font-mono">
                  <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{saveInlineError}</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 text-amber-300">
                  <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
                  <span>You have unsaved changes</span>
                </div>
              )}
            </div>

            {isDirty && (
              <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
                {isSavingSettings && (
                  <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-400 font-mono mr-1">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin text-accent-500" />
                    <span>Saving...</span>
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleResetChanges}
                  disabled={isSavingSettings}
                  className="px-3 py-1.5 text-xs text-slate-300 hover:text-white bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded-lg transition cursor-pointer disabled:opacity-50"
                >
                  Discard
                </button>
                <button
                  type="submit"
                  disabled={isSavingSettings}
                  className="px-4 py-1.5 text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 rounded-lg transition cursor-pointer flex items-center justify-center gap-1.5 shadow-md disabled:opacity-50"
                >
                  {isSavingSettings ? (
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Save className="w-3.5 h-3.5" />
                  )}
                  <span>Save Changes</span>
                </button>
              </div>
            )}
          </aside>
        )}
          </form>
        </div>
      )}

      {/* Sub-tab 2: Host Aliases */}
      {activeSubTab === 'aliases' && (
        <HostAliasManager
          initialAddIp={initialAddIp}
          onAliasSaved={onAliasSaved}
        />
      )}

      {/* Sub-tab 3: Advanced */}
      {activeSubTab === 'advanced' && (
        <div className="space-y-6">
          {/* Admin Password Reset Section */}
          <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Lock className="w-4 h-4 text-accent-500" />
              <span>Change Admin Password</span>
            </h3>

            {pwdMsg && (
              <div
                className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${pwdMsg.isError
                    ? 'bg-red-950/60 border-red-800 text-red-300'
                    : 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                  }`}
              >
                {pwdMsg.isError ? (
                  <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                ) : (
                  <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                )}
                <span>{pwdMsg.text}</span>
              </div>
            )}

            <form onSubmit={handleChangePassword} className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
              <div>
                <label htmlFor="current-password" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  Current Password
                </label>
                <input
                  id="current-password"
                  type="password"
                  value={currentPwd}
                  onChange={(e) => setCurrentPwd(e.target.value)}
                  required
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
              </div>

              <div>
                <label htmlFor="new-password" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  New Password (min 8 chars)
                </label>
                <input
                  id="new-password"
                  type="password"
                  value={newPwd}
                  onChange={(e) => setNewPwd(e.target.value)}
                  minLength={8}
                  required
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
              </div>

              <div>
                <label htmlFor="confirm-password" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  Confirm New Password
                </label>
                <input
                  id="confirm-password"
                  type="password"
                  value={confirmPwd}
                  onChange={(e) => setConfirmPwd(e.target.value)}
                  minLength={8}
                  required
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
              </div>

              <div className="sm:col-span-3 flex justify-end pt-1">
                <button
                  type="submit"
                  disabled={isChangingPwd || !currentPwd || newPwd.length < 8 || newPwd !== confirmPwd}
                  className="w-full sm:w-auto justify-center bg-dark-800 hover:bg-dark-700 disabled:opacity-50 text-slate-200 border border-dark-600 font-medium px-4 py-2.5 sm:py-2 rounded-lg text-xs flex items-center gap-1.5 transition cursor-pointer min-h-[40px] sm:min-h-0"
                >
                  <Lock className="w-3.5 h-3.5" />
                  <span>{isChangingPwd ? 'Updating...' : 'Update Password'}</span>
                </button>
              </div>
            </form>
          </section>

          {/* About LogShed Section */}
          <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Info className="w-4 h-4 text-accent-500" />
                <span>About LogShed</span>
              </h3>
              {versionInfo && (
                <span className="font-mono text-xs px-2 py-0.5 rounded bg-dark-950 border border-dark-700 text-slate-300">
                  v{versionInfo.current_version}
                </span>
              )}
            </div>

            {/* Version & Update Status */}
            {versionInfo && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {versionInfo.update_available && versionInfo.check_enabled !== false && checkForUpdates ? (
                  <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-950/40 border border-amber-800/60 text-amber-300">
                    <ArrowUpCircle className="w-4 h-4 text-amber-400 shrink-0" />
                    <span>
                      App update available: <strong className="font-semibold font-mono">v{versionInfo.latest_version}</strong>
                    </span>
                    <a
                      href="https://github.com/BenHornerTech/logshed/releases"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="ml-1 inline-flex items-center gap-1 text-amber-400 hover:text-amber-200 underline text-[11px]"
                    >
                      <span>Release Notes</span>
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                ) : versionInfo.check_enabled === false || !checkForUpdates ? (
                  <div className="flex items-center gap-1.5 text-slate-400 text-xs">
                    <span>Update checks are disabled</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-1.5 text-slate-400 text-xs">
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span>LogShed is up to date</span>
                  </div>
                )}
              </div>
            )}

            {/* License & Copyright */}
            <p className="text-xs text-slate-400">
              MIT License - Copyright (c) 2026 LogShed Contributors
            </p>

            {/* Links */}
            <div className="flex flex-wrap items-center gap-4 text-xs pt-1 border-t border-dark-800">
              <a
                href="https://github.com/BenHornerTech/logshed"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-accent-400 hover:text-accent-300 hover:underline transition"
              >
                <span>GitHub Repository</span>
                <ExternalLink className="w-3 h-3" />
              </a>
              <a
                href="https://github.com/BenHornerTech/logshed/blob/main/CHANGELOG.md"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-accent-400 hover:text-accent-300 hover:underline transition"
              >
                <span>Changelog</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </section>
        </div>
      )}
    </div>
  );
};
