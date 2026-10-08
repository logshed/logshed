import React, { useEffect, useState, useRef, useMemo } from 'react';
import {
  Sparkles,
  Copy,
  Check,
  Shield,
  RefreshCw,
  AlertCircle,
  Info,
  RotateCcw,
  Clock,
  ArrowRight,
  ArrowUp,
  ArrowDown,
  Plus,
  X,
  ChevronDown,
  ChevronUp,
  SlidersHorizontal,
  ExternalLink,
} from 'lucide-react';
import { LogEntry, AiPreviewResponse, AiDiagnosisResponse, AiModelInfo } from '../../types.ts';
import { previewAiPrompt, diagnoseLogs, getAiModels } from '../../api/ai.ts';
import { fetchSettings, getCachedServerTimezone, getCachedServerTzOffset } from '../../api/settings.ts';
import { useClipboard } from '../../utils/hooks.ts';
import { DEFAULT_AI_MODEL, DEFAULT_SYSTEM_PROMPT, buildFullEnvelope, parseFullEnvelope, normalizePrompt, getOrdinalSuffix } from '../../utils/aiPrompt.ts';
import { Modal } from '../common/Modal.tsx';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';

const DEFAULT_PROVIDER_MODELS: Record<string, string> = {
  gemini: DEFAULT_AI_MODEL,
  openai: 'gpt-4o',
  anthropic: 'claude-sonnet-4-6',
  openai_compatible: 'llama3.2',
};

interface ProviderLocalState {
  hasKey: boolean;
  model: string;
  fallbackModels: string;
}

interface AiAnalysisModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedLogs: LogEntry[];
  onNavigateToSettings?: () => void;
}

export const AiAnalysisModal: React.FC<AiAnalysisModalProps> = ({
  isOpen,
  onClose,
  selectedLogs,
  onNavigateToSettings,
}) => {
  const [preview, setPreview] = useState<AiPreviewResponse | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState<boolean>(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [promptText, setPromptText] = useState<string>('');
  const [systemPrompt, setSystemPrompt] = useState<string>(DEFAULT_SYSTEM_PROMPT);
  const [fullPromptText, setFullPromptText] = useState<string>('');
  const [promptViewMode, setPromptViewMode] = useState<'analysis' | 'full'>('analysis');

  const [userContext, setUserContext] = useState<string>('');
  const [provider, setProvider] = useState<string>('gemini');
  const [model, setModel] = useState<string>(DEFAULT_AI_MODEL);
  const [fallbackModels, setFallbackModels] = useState<string>('');

  const [providerConfigs, setProviderConfigs] = useState<Record<string, ProviderLocalState>>({
    gemini: { hasKey: false, model: DEFAULT_AI_MODEL, fallbackModels: '' },
    openai: { hasKey: false, model: 'gpt-4o', fallbackModels: '' },
    anthropic: { hasKey: false, model: 'claude-sonnet-4-6', fallbackModels: '' },
    openai_compatible: { hasKey: true, model: 'llama3.2', fallbackModels: '' },
  });

  // Model discovery states
  const [availableModels, setAvailableModels] = useState<AiModelInfo[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState<boolean>(false);
  const [hasApiKeyForProvider, setHasApiKeyForProvider] = useState<boolean>(true);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [isCustomModel, setIsCustomModel] = useState<boolean>(false);
  const [selectedFallbackToAdd, setSelectedFallbackToAdd] = useState<string>('');
  const [customFallbackInput, setCustomFallbackInput] = useState<string>('');
  const [showCustomFallbackInput, setShowCustomFallbackInput] = useState<boolean>(false);
  const [isAiSettingsOpen, setIsAiSettingsOpen] = useState<boolean>(false);

  const [isDiagnosing, setIsDiagnosing] = useState<boolean>(false);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const [streamProgress, setStreamProgress] = useState<{
    stage: 'init' | 'calling' | 'failover' | 'complete' | 'error';
    currentModel?: string;
    failedModel?: string;
    nextModel?: string;
    message?: string;
    failovers: Array<{ failedModel: string; nextModel: string; error?: string }>;
  }>({
    stage: 'init',
    failovers: [],
  });
  const [analysisResult, setAnalysisResult] = useState<AiDiagnosisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const { copied: copiedPrompt, copy: copyPrompt } = useClipboard();
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  const buildCombinedPrompt = (basePrompt: string, ctx: string) => {
    if (!ctx.trim()) return basePrompt;
    const parts = basePrompt.split('### Redacted Log Stream');
    if (parts.length === 2) {
      return `${parts[0]}### Situational Context from Operator\n${ctx.trim()}\n\n### Redacted Log Stream${parts[1]}`;
    }
    return `${basePrompt}\n\n### Situational Context from Operator\n${ctx.trim()}`;
  };

  const defaultPrompt = useMemo(() => {
    if (!preview) return '';
    return buildCombinedPrompt(preview.redacted_prompt, userContext);
  }, [preview, userContext]);

  const defaultFullPrompt = useMemo(() => {
    if (!preview) return '';
    return buildFullEnvelope(preview.system_prompt || DEFAULT_SYSTEM_PROMPT, defaultPrompt);
  }, [preview, defaultPrompt]);

  const hasEditedPrompt = Boolean(
    preview && normalizePrompt(promptText) !== normalizePrompt(defaultPrompt)
  );
  const hasEditedSystem = Boolean(
    preview &&
      normalizePrompt(systemPrompt) !==
        normalizePrompt(preview.system_prompt || DEFAULT_SYSTEM_PROMPT)
  );
  const hasEditedFull = Boolean(
    preview && normalizePrompt(fullPromptText) !== normalizePrompt(defaultFullPrompt)
  );
  const isModified = promptViewMode === 'full' ? hasEditedFull : (hasEditedPrompt || hasEditedSystem);

  const handleGoToSettings = () => {
    onClose();
    if (onNavigateToSettings) {
      onNavigateToSettings();
    } else {
      window.history.pushState(null, '', '/settings');
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  };

  const isAiDisabled = preview?.ai_enabled === false;
  const isAiMissingKey = !hasApiKeyForProvider && provider !== 'openai_compatible';
  const isAiUnavailable = Boolean(isAiDisabled || isAiMissingKey);

  useEffect(() => {
    if (isOpen && selectedLogs.length > 0) {
      loadPreview();
    } else {
      setPreview(null);
      setAnalysisResult(null);
      setPreviewError(null);
      setAnalysisError(null);
      setUserContext('');
      setPromptText('');
      setSystemPrompt(DEFAULT_SYSTEM_PROMPT);
      setFullPromptText('');
      setPromptViewMode('analysis');
      setFallbackModels('');
      setStreamProgress({ stage: 'init', failovers: [] });
      setElapsedSeconds(0);
      setIsAiSettingsOpen(false);
    }
  }, [isOpen, selectedLogs]);

  // Timer effect to track elapsed seconds while diagnosing
  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;
    if (isDiagnosing) {
      setElapsedSeconds(0);
      interval = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      setElapsedSeconds(0);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isDiagnosing]);

  const formatElapsed = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  // Auto-expand prompt textarea to avoid premature CSS height clipping
  useEffect(() => {
    if (promptTextareaRef.current) {
      promptTextareaRef.current.style.height = 'auto';
      promptTextareaRef.current.style.height = `${Math.max(160, promptTextareaRef.current.scrollHeight)}px`;
    }
  }, [promptText, fullPromptText, systemPrompt, promptViewMode, preview]);

  const loadPreview = async () => {
    const validLogIds = selectedLogs
      .map((l) => l.id)
      .filter((id): id is number => typeof id === 'number' && !isNaN(id));

    if (validLogIds.length === 0) {
      setPreviewError('No valid log IDs selected for AI analysis.');
      return;
    }

    if (validLogIds.length > 200) {
      setPreviewError(
        `Cannot analyze more than 200 logs at once (${validLogIds.length} logs selected). Please reduce your selection to 200 logs or fewer.`
      );
      return;
    }

    try {
      setIsLoadingPreview(true);
      setPreviewError(null);

      const client_timezone = getCachedServerTimezone() || Intl.DateTimeFormat().resolvedOptions().timeZone;
      const client_utc_offset_minutes = getCachedServerTzOffset() ?? -new Date().getTimezoneOffset();

      // Fetch preview and system settings in parallel to initialize provider configs
      const [res, settRes] = await Promise.all([
        previewAiPrompt({
          log_ids: validLogIds,
          client_timezone,
          client_utc_offset_minutes,
        }),
        fetchSettings().catch(() => null),
      ]);

      setPreview(res);
      const activeP = res.provider || 'gemini';
      setProvider(activeP);

      const initialFallbacks = res.fallback_models && res.fallback_models.length > 0
        ? res.fallback_models.join(', ')
        : '';
      setFallbackModels(initialFallbacks);
      setModel(res.model || DEFAULT_PROVIDER_MODELS[activeP] || DEFAULT_AI_MODEL);

      // Initialize provider configs from system settings
      const configs: Record<string, ProviderLocalState> = {
        gemini: { hasKey: false, model: DEFAULT_AI_MODEL, fallbackModels: '' },
        openai: { hasKey: false, model: 'gpt-4o', fallbackModels: '' },
        anthropic: { hasKey: false, model: 'claude-sonnet-4-6', fallbackModels: '' },
        openai_compatible: { hasKey: true, model: 'llama3.2', fallbackModels: '' },
      };

      if (settRes?.ai_providers_config) {
        for (const [p, pCfg] of Object.entries(settRes.ai_providers_config)) {
          if (configs[p]) {
            configs[p] = {
              hasKey: pCfg.has_api_key,
              model: pCfg.ai_model || DEFAULT_PROVIDER_MODELS[p] || DEFAULT_AI_MODEL,
              fallbackModels: pCfg.ai_fallback_models || '',
            };
          }
        }
      }

      // Ensure active provider matches the preview values
      configs[activeP] = {
        hasKey: res.has_ai_api_key ?? configs[activeP]?.hasKey ?? true,
        model: res.model || configs[activeP]?.model || DEFAULT_PROVIDER_MODELS[activeP] || DEFAULT_AI_MODEL,
        fallbackModels: initialFallbacks,
      };

      setProviderConfigs(configs);

      const initialSys = res.system_prompt || DEFAULT_SYSTEM_PROMPT;
      setSystemPrompt(initialSys);
      const initialUser = buildCombinedPrompt(res.redacted_prompt, userContext);
      setPromptText(initialUser);
      setFullPromptText(buildFullEnvelope(initialSys, initialUser));
      loadModels(activeP, res);
    } catch (err: any) {
      setPreviewError(err.message || 'Failed to generate redacted AI preview.');
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const loadModels = async (prov: string, currentPreview?: AiPreviewResponse) => {
    try {
      setIsLoadingModels(true);
      setModelsError(null);
      const res = await getAiModels(prov);
      const discovered = res.models || [];
      setAvailableModels(discovered);
      const activePrev = currentPreview || preview;
      const resKey = (activePrev && prov === activePrev.provider && activePrev.has_ai_api_key !== undefined)
        ? activePrev.has_ai_api_key
        : res.has_api_key;
      const effectiveHasKey = resKey !== undefined ? resKey : true;
      setHasApiKeyForProvider(effectiveHasKey);
      if (res.error) setModelsError(res.error);

      // Keep providerConfigs updated with live key status
      setProviderConfigs((prev) => ({
        ...prev,
        [prov]: {
          ...prev[prov],
          hasKey: effectiveHasKey,
        },
      }));
    } catch (err: any) {
      setModelsError(err.message || 'Failed to load models.');
      setAvailableModels([]);
    } finally {
      setIsLoadingModels(false);
    }
  };

  const handleProviderChange = (newProvider: string) => {
    // 1. Save current provider's model & fallback state
    const updatedConfigs: Record<string, ProviderLocalState> = {
      ...providerConfigs,
      [provider]: {
        hasKey: hasApiKeyForProvider,
        model,
        fallbackModels,
      },
    };
    setProviderConfigs(updatedConfigs);

    // 2. Load target provider's settings
    const target = updatedConfigs[newProvider] || {
      hasKey: newProvider === 'openai_compatible',
      model: DEFAULT_PROVIDER_MODELS[newProvider] || DEFAULT_AI_MODEL,
      fallbackModels: '',
    };

    setProvider(newProvider);
    setModel(target.model || DEFAULT_PROVIDER_MODELS[newProvider] || DEFAULT_AI_MODEL);
    setFallbackModels(target.fallbackModels || '');
    setHasApiKeyForProvider(target.hasKey);
    setIsCustomModel(false);
    setSelectedFallbackToAdd('');
    setShowCustomFallbackInput(false);
    setModelsError(null);
    setAvailableModels([]);

    loadModels(newProvider);
  };

  const fallbackList = fallbackModels
    ? fallbackModels.split(',').map((s) => s.trim()).filter(Boolean)
    : [];

  const moveFallback = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= fallbackList.length) return;
    const copy = [...fallbackList];
    const [removed] = copy.splice(index, 1);
    copy.splice(target, 0, removed);
    setFallbackModels(copy.join(', '));
  };

  const removeFallback = (index: number) => {
    const copy = fallbackList.filter((_, i) => i !== index);
    setFallbackModels(copy.join(', '));
  };

  const addFallback = (modelName: string) => {
    const trimmed = modelName.trim();
    if (!trimmed || fallbackList.includes(trimmed)) return;
    setFallbackModels([...fallbackList, trimmed].join(', '));
  };

  const handlePrimaryModelChange = (newModel: string) => {
    setModel(newModel);
    if (fallbackList.includes(newModel)) {
      const updated = fallbackList.filter((m) => m !== newModel);
      setFallbackModels(updated.join(', '));
    }
  };


  const handleUserContextChange = (newContext: string) => {
    const prevDefault = buildCombinedPrompt(preview?.redacted_prompt || '', userContext);
    setUserContext(newContext);
    const newDefault = buildCombinedPrompt(preview?.redacted_prompt || '', newContext);
    // If promptText currently matches previous default, keep it in sync with newContext
    if (preview && normalizePrompt(promptText) === normalizePrompt(prevDefault)) {
      setPromptText(newDefault);
      if (normalizePrompt(fullPromptText) === normalizePrompt(buildFullEnvelope(systemPrompt, prevDefault))) {
        setFullPromptText(buildFullEnvelope(systemPrompt, newDefault));
      }
    }
  };

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    if (promptViewMode === 'full') {
      setFullPromptText(e.target.value);
    } else {
      setPromptText(e.target.value);
    }
  };

  const handleResetPrompt = () => {
    if (preview) {
      setPromptText(defaultPrompt);
      const defaultSys = preview.system_prompt || DEFAULT_SYSTEM_PROMPT;
      setSystemPrompt(defaultSys);
      setFullPromptText(buildFullEnvelope(defaultSys, defaultPrompt));
    }
  };

  const handleRunAnalysis = async () => {
    const validLogIds = selectedLogs
      .map((l) => l.id)
      .filter((id): id is number => typeof id === 'number' && !isNaN(id));

    if (validLogIds.length === 0) {
      setAnalysisError('No valid log IDs selected for AI analysis.');
      return;
    }

    if (validLogIds.length > 200) {
      setAnalysisError(
        `Cannot analyze more than 200 logs at once (${validLogIds.length} logs selected). Please reduce your selection to 200 logs or fewer.`
      );
      return;
    }

    if (isAiUnavailable) {
      setAnalysisError(
        isAiDisabled
          ? 'AI features are disabled in Settings. Please enable an AI provider to inspect logs.'
          : 'AI provider is not configured. Please configure an API key in Settings to inspect logs.'
      );
      return;
    }

    const parsedFallbacks = fallbackModels
      .split(',')
      .map((m) => m.trim())
      .filter(Boolean);

    try {
      setIsDiagnosing(true);
      setAnalysisError(null);
      setStreamProgress({
        stage: 'init',
        currentModel: model,
        message: `Initiating diagnosis with ${model}...`,
        failovers: [],
      });

      let promptOverride: string | undefined = undefined;
      let systemPromptOverride: string | undefined = undefined;

      if (promptViewMode === 'full') {
        if (hasEditedFull) {
          const parsed = parseFullEnvelope(fullPromptText, systemPrompt);
          const defaultSys = preview?.system_prompt || DEFAULT_SYSTEM_PROMPT;
          if (normalizePrompt(parsed.userPrompt) !== normalizePrompt(defaultPrompt)) {
            promptOverride = parsed.userPrompt.trim();
          }
          if (normalizePrompt(parsed.systemPrompt) !== normalizePrompt(defaultSys)) {
            systemPromptOverride = parsed.systemPrompt.trim();
          }
        }
      } else {
        if (hasEditedPrompt) {
          promptOverride = promptText.trim();
        }
        if (hasEditedSystem) {
          systemPromptOverride = systemPrompt.trim();
        }
      }

      const client_timezone = getCachedServerTimezone() || Intl.DateTimeFormat().resolvedOptions().timeZone;
      const client_utc_offset_minutes = getCachedServerTzOffset() ?? -new Date().getTimezoneOffset();

      const res = await diagnoseLogs({
        log_ids: validLogIds,
        user_context: userContext.trim() || undefined,
        prompt_override: promptOverride,
        system_prompt_override: systemPromptOverride,
        provider,
        model,
        fallback_models: parsedFallbacks.length > 0 ? parsedFallbacks : undefined,
        client_timezone,
        client_utc_offset_minutes,
        onEvent: (evt) => {
          if (evt.stage === 'calling') {
            setStreamProgress((prev) => ({
              ...prev,
              stage: 'calling',
              currentModel: evt.model || prev.currentModel,
              message: evt.is_fallback
                ? `Querying fallback model (${evt.model})...`
                : `Querying primary model (${evt.model})...`,
            }));
          } else if (evt.stage === 'failover') {
            setStreamProgress((prev) => ({
              ...prev,
              stage: 'failover',
              failedModel: evt.failed_model,
              nextModel: evt.next_model,
              currentModel: evt.next_model,
              message: `Model ${evt.failed_model} overloaded. Failing over to ${evt.next_model}...`,
              failovers: [
                ...prev.failovers,
                {
                  failedModel: evt.failed_model || '',
                  nextModel: evt.next_model || '',
                  error: evt.error,
                },
              ],
            }));
          } else if (evt.stage === 'error') {
            setStreamProgress((prev) => ({
              ...prev,
              stage: 'error',
              message: evt.message,
            }));
          }
        },
      });
      setAnalysisResult(res);
    } catch (err: any) {
      setAnalysisError(err.message || 'AI diagnosis request failed.');
    } finally {
      setIsDiagnosing(false);
    }
  };

  const estimatedTokens = useMemo(() => {
    if (!promptText) {
      return preview ? preview.estimated_tokens : 0;
    }
    const currentSysLen = systemPrompt.length;
    return Math.max(1, Math.floor(promptText.length / 3.5) + Math.floor(currentSysLen / 3.5) + 50);
  }, [promptText, systemPrompt, preview]);

  const handleCopyPrompt = async () => {
    const textToCopy =
      promptViewMode === 'full'
        ? fullPromptText
        : promptText;
    if (textToCopy) {
      await copyPrompt(textToCopy);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="On-Demand AI Root-Cause Analysis"
      maxWidth="max-w-3xl"
    >
      <div className="space-y-4 text-xs font-sans">
        {/* Host & Target Scope Header */}
        <div className="p-3 bg-dark-950 rounded-lg border border-dark-700 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Shield className={`w-4 h-4 ${selectedLogs.length > 200 ? 'text-amber-400' : 'text-emerald-400'}`} />
            <span className="font-semibold text-slate-200">
              {selectedLogs.length} Log{selectedLogs.length === 1 ? '' : 's'} Selected
            </span>
            {selectedLogs.length > 200 && (
              <span className="text-[11px] text-amber-400 font-mono">
                (Max 200 logs allowed)
              </span>
            )}
            {preview && (
              <span className="text-slate-400 font-mono">
                ({preview.source_alias} • {preview.app_name})
              </span>
            )}
          </div>

          {preview && (
            <div className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
              <span
                className="px-2 py-0.5 bg-dark-800 rounded border border-dark-700 flex items-center gap-1.5 cursor-help"
                title="Estimated prompt/input tokens only (includes system instructions and metadata). Does not include model thinking or response output tokens."
              >
                <span>~{estimatedTokens.toLocaleString()} tokens</span>
                <Info className="w-3.5 h-3.5 text-slate-400 hover:text-slate-200 transition" />
              </span>
            </div>
          )}
        </div>

        {previewError && (
          <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{previewError}</span>
          </div>
        )}

        {/* Loading state for preview */}
        {isLoadingPreview && (
          <div className="p-6 text-center text-slate-400 font-mono flex items-center justify-center gap-2">
            <RefreshCw className="w-4 h-4 animate-spin text-accent-400" />
            <span>Scrubbing sensitive tokens & generating AI preview...</span>
          </div>
        )}

        {preview && !analysisResult && (
          <div className="space-y-3">
            {/* AI Disabled / Unconfigured Advisory Banner */}
            {isAiUnavailable && (
              <div className="p-3.5 bg-amber-950/40 border border-amber-800/60 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-amber-200 shadow-xs">
                <div className="flex items-start gap-2.5">
                  <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <p className="font-semibold text-amber-200">
                      {isAiDisabled ? 'AI Provider Disabled' : 'AI Provider Not Configured'}
                    </p>
                    <p className="text-amber-300/80 text-[11px] mt-0.5">
                      {isAiDisabled
                        ? 'AI features are disabled in system configuration. Enable an AI provider in Settings to inspect logs.'
                        : 'An API key is required to query models. Configure your AI provider in Settings to inspect logs.'}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleGoToSettings}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-dark-900 hover:bg-dark-850 border border-amber-800/60 rounded-lg text-xs font-medium text-amber-200 hover:text-white transition cursor-pointer shrink-0 self-start sm:self-auto"
                >
                  <span>Configure in Settings</span>
                  <ExternalLink className="w-3.5 h-3.5 text-accent-400" />
                </button>
              </div>
            )}

            {/* Redacted Preview & Prompt Editor Block */}
            <div>
              <div className="flex flex-wrap items-center justify-between mb-1.5 gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1">
                    <Shield className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Prompt (Editable)</span>
                  </span>
                  {isModified && (
                    <span className="text-[10px] text-accent-400 font-mono">
                      (modified)
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2.5">
                  {/* View mode toggle pill */}
                  <div className="flex items-center bg-dark-950 border border-dark-700 rounded p-0.5 text-[10px] font-mono">
                    <button
                      type="button"
                      onClick={() => {
                        if (promptViewMode === 'full') {
                          const parsed = parseFullEnvelope(fullPromptText, systemPrompt);
                          setSystemPrompt(parsed.systemPrompt);
                          setPromptText(parsed.userPrompt);
                          setPromptViewMode('analysis');
                        }
                      }}
                      className={`px-2 py-0.5 rounded transition cursor-pointer ${
                        promptViewMode === 'analysis'
                          ? 'bg-accent-600 text-white font-medium shadow-xs'
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Analysis Prompt
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (promptViewMode !== 'full') {
                          setFullPromptText(buildFullEnvelope(systemPrompt, promptText));
                          setPromptViewMode('full');
                        }
                      }}
                      className={`px-2 py-0.5 rounded transition cursor-pointer ${
                        promptViewMode === 'full'
                          ? 'bg-accent-600 text-white font-medium shadow-xs'
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      Full LLM Prompt
                    </button>
                  </div>

                  {isModified && (
                    <button
                      type="button"
                      onClick={handleResetPrompt}
                      className="flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300 transition cursor-pointer"
                      title="Reset prompt to original generated text"
                      aria-label="Reset prompt to default"
                    >
                      <RotateCcw className="w-3 h-3" />
                      <span>Reset Prompt</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleCopyPrompt}
                    className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer min-h-[32px] touch-manipulation select-none active:bg-dark-750"
                    title="Copy prompt"
                  >
                    {copiedPrompt ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedPrompt ? 'Copied' : 'Copy Prompt'}</span>
                  </button>
                </div>
              </div>
              <textarea
                ref={promptTextareaRef}
                value={promptViewMode === 'full' ? fullPromptText : promptText}
                onChange={handlePromptChange}
                placeholder={promptViewMode === 'full' ? 'Full LLM prompt envelope...' : 'Redacted prompt...'}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg p-3 font-mono text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 leading-relaxed whitespace-pre-wrap resize-y overflow-y-hidden"
              />

              {/* Log Redaction Notice */}
              <div className="mt-2 p-2.5 bg-amber-950/30 border border-amber-800/40 rounded-lg flex items-start gap-2 text-amber-200/90 text-[11px] leading-relaxed">
                <Shield className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  <strong className="font-semibold text-amber-300">Redaction Notice:</strong> Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Please review the prompt above before sending - you are responsible for the contents and sensitive data you transmit to external AI providers.
                </span>
              </div>
            </div>

            {/* Collapsible Provider & Model Configuration Section */}
            <div className="border border-dark-700/80 rounded-lg bg-dark-900/60 overflow-hidden transition-colors">
              <button
                type="button"
                onClick={() => setIsAiSettingsOpen(!isAiSettingsOpen)}
                className="w-full p-2.5 flex flex-wrap items-center justify-between gap-2 hover:bg-dark-800/60 transition cursor-pointer text-left select-none"
                aria-expanded={isAiSettingsOpen}
                aria-controls="ai-model-settings-content"
              >
                <div className="flex flex-wrap items-center gap-2 min-w-0">
                  <SlidersHorizontal className="w-3.5 h-3.5 text-accent-400 shrink-0" />
                  <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                    Model & Provider Settings
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-mono">
                    <span className="px-1.5 py-0.5 rounded bg-dark-950 border border-dark-700 text-slate-300 truncate max-w-[150px] sm:max-w-[220px]">
                      {model || DEFAULT_AI_MODEL}
                    </span>
                    {fallbackList.length > 0 ? (
                      <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20 text-[10px]">
                        {fallbackList.length} fallback{fallbackList.length === 1 ? '' : 's'}
                      </span>
                    ) : (
                      <span className="hidden sm:inline text-[10px] text-slate-500">
                        no fallbacks
                      </span>
                    )}
                    {!hasApiKeyForProvider && provider !== 'openai_compatible' && (
                      <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px] flex items-center gap-1">
                        <AlertCircle className="w-3 h-3 text-amber-400" />
                        Key required
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1.5 text-slate-400 text-[11px] font-mono shrink-0 ml-auto">
                  <span className="text-[10px] text-slate-400 hover:text-slate-200">
                    {isAiSettingsOpen ? 'Hide' : 'Configure'}
                  </span>
                  {isAiSettingsOpen ? (
                    <ChevronUp className="w-4 h-4 text-slate-400" />
                  ) : (
                    <ChevronDown className="w-4 h-4 text-slate-400" />
                  )}
                </div>
              </button>

              {/* Collapsible Content */}
              <div
                id="ai-model-settings-content"
                className={isAiSettingsOpen ? 'p-3 pt-2.5 space-y-3 border-t border-dark-800' : 'hidden'}
              >
                {/* Missing API Key Warning */}
                {!hasApiKeyForProvider && provider !== 'openai_compatible' && (
                  <div className="p-2.5 bg-amber-950/40 border border-amber-800/60 rounded-lg flex items-start gap-2 text-amber-300 text-xs font-mono">
                    <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                    <span>
                      No API key configured for {provider === 'gemini' ? 'Google Gemini' : provider === 'openai' ? 'OpenAI' : provider === 'anthropic' ? 'Anthropic Claude' : 'your endpoint'}. Please configure your API key in Settings to load models and run AI analysis.
                    </span>
                  </div>
                )}

                {/* Models Loading Error Notice */}
                {modelsError && (hasApiKeyForProvider || provider === 'openai_compatible') && (
                  <div className="p-2.5 bg-red-950/40 border border-red-800/60 rounded-lg flex items-start gap-2 text-red-300 text-xs font-mono">
                    <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                    <span>{modelsError}</span>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                      AI Provider
                    </label>
                    <select
                      value={provider}
                      onChange={(e) => handleProviderChange(e.target.value)}
                      className="w-full bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                    >
                      <option value="gemini">Google Gemini</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic Claude</option>
                      <option value="openai_compatible">OpenAI-Compatible (Ollama / LocalAI)</option>
                    </select>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase">
                        <span>Primary Model</span>
                        {isLoadingModels && (
                          <RefreshCw className="w-3 h-3 text-accent-400 animate-spin" />
                        )}
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
                          Use dropdown
                        </button>
                      )}
                    </div>

                    {availableModels.length > 0 && !isCustomModel ? (
                      <select
                        value={availableModels.some((m) => m.id === model) ? model : '__custom__'}
                        onChange={(e) => {
                          if (e.target.value === '__custom__') {
                            setIsCustomModel(true);
                          } else {
                            handlePrimaryModelChange(e.target.value);
                          }
                        }}
                        className="w-full bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                      >
                        {availableModels
                          .filter((m) => m.id === model || !fallbackList.includes(m.id))
                          .map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.id} {m.supports_thinking ? ' [Reasoning]' : ''}
                            </option>
                          ))}
                        <option value="__custom__">Custom model name...</option>
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={model}
                        onChange={(e) => handlePrimaryModelChange(e.target.value)}
                        placeholder={provider === 'gemini' ? DEFAULT_AI_MODEL : provider === 'openai' ? 'gpt-4o' : provider === 'anthropic' ? 'claude-sonnet-4-6' : 'llama3.2'}
                        className="w-full bg-dark-950 border border-dark-700 rounded px-2.5 py-1.5 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                      />
                    )}
                  </div>
                </div>

                {/* Fallback Models Ordered Chain */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="block text-[11px] font-semibold text-slate-400 uppercase">
                      Fallback Models (Sequential Failover Chain)
                    </label>
                    <span className="text-[10px] text-slate-500 font-mono">
                      Queried in order on 503, 504, or timeout
                    </span>
                  </div>

                  {fallbackList.length === 0 ? (
                    <div className="p-2 bg-dark-950 border border-dark-800 rounded text-[11px] text-slate-500 font-mono">
                      No fallback models configured for this analysis.
                    </div>
                  ) : (
                    <div className="space-y-1 max-h-36 overflow-y-auto">
                      {fallbackList.map((fb, idx) => {
                        const isThinking = availableModels.find((m) => m.id === fb)?.supports_thinking;
                        return (
                          <div
                            key={idx}
                            className="flex items-center justify-between p-1.5 bg-dark-950 border border-dark-800 rounded text-xs font-mono"
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
                                className="p-0.5 text-slate-400 hover:text-slate-200 disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer"
                                title="Move up in priority"
                                aria-label="Move fallback up"
                              >
                                <ArrowUp className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                disabled={idx === fallbackList.length - 1}
                                onClick={() => moveFallback(idx, 1)}
                                className="p-0.5 text-slate-400 hover:text-slate-200 disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer"
                                title="Move down in priority"
                                aria-label="Move fallback down"
                              >
                                <ArrowDown className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={() => removeFallback(idx)}
                                className="p-0.5 text-red-400 hover:text-red-300 cursor-pointer"
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
                  <div className="pt-1">
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
                          className="flex-1 min-w-0 bg-dark-950 border border-dark-700 rounded px-2 py-1 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono truncate"
                        >
                          <option value="">-- Add Fallback Model --</option>
                          {availableModels
                            .filter((m) => m.id !== model && !fallbackList.includes(m.id))
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
                          className="shrink-0 px-2.5 py-1 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded text-xs text-slate-200 font-mono flex items-center gap-1 disabled:opacity-40 cursor-pointer"
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
                          className="flex-1 min-w-0 bg-dark-950 border border-dark-700 rounded px-2 py-1 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono"
                        />
                        <button
                          type="button"
                          disabled={!customFallbackInput.trim()}
                          onClick={() => {
                            addFallback(customFallbackInput);
                            setCustomFallbackInput('');
                            setShowCustomFallbackInput(false);
                          }}
                          className="shrink-0 px-2.5 py-1 bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded text-xs text-slate-200 font-mono flex items-center gap-1 disabled:opacity-40 cursor-pointer"
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
                          className="shrink-0 p-1 text-slate-400 hover:text-slate-200 cursor-pointer"
                          title="Cancel custom fallback"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Free-text user context input */}
            <div>
              <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Optional Situational Context
              </label>
              <textarea
                value={userContext}
                onChange={(e) => handleUserContextChange(e.target.value)}
                placeholder="e.g. Occurred immediately following network switch firmware upgrade, or after container image pull..."
                rows={2}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg p-2.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-sans"
              />
            </div>

            {analysisError && (
              <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg text-red-300">
                {analysisError}
              </div>
            )}

            {/* In-Flight Live Progress & Failover Pipeline Card */}
            {isDiagnosing && (
              <div className="p-3.5 bg-dark-950 border border-accent-800/60 rounded-lg space-y-2.5 animate-in fade-in">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <RefreshCw className="w-4 h-4 text-accent-400 animate-spin" />
                    <span className="font-semibold text-slate-200 text-xs">
                      {streamProgress.message || `Analyzing logs with ${streamProgress.currentModel || model}...`}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 font-mono text-[11px] bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                    <Clock className="w-3.5 h-3.5 text-accent-400" />
                    <span>Elapsed: {formatElapsed(elapsedSeconds)}</span>
                  </div>
                </div>

                {/* Real-time Failover Alert Banner */}
                {streamProgress.failovers.length > 0 && (
                  <div className="space-y-1.5 pt-1">
                    {streamProgress.failovers.map((fo, idx) => (
                      <div
                        key={idx}
                        className="p-2 bg-amber-950/50 border border-amber-800/70 rounded text-[11px] text-amber-200 flex items-start gap-2"
                      >
                        <AlertCircle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
                        <div>
                          <span className="font-semibold text-amber-300">Failover Active:</span>{' '}
                          Model <span className="font-mono text-amber-100">{fo.failedModel}</span> encountered an overload or timeout error. Failing over to <span className="font-mono text-amber-100">{fo.nextModel}</span>...
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Live Chain Status Pills */}
                <div className="flex flex-wrap items-center gap-1.5 pt-1 text-[10px] font-mono">
                  <span className="text-slate-500 uppercase font-semibold">Chain Status:</span>
                  <span
                    className={`px-2 py-0.5 rounded border ${
                      streamProgress.failovers.some((f) => f.failedModel === model)
                        ? 'bg-amber-950/60 border-amber-800 text-amber-400 line-through'
                        : streamProgress.currentModel === model
                        ? 'bg-accent-950/80 border-accent-600 text-accent-300 animate-pulse'
                        : 'bg-dark-900 border-dark-700 text-slate-400'
                    }`}
                  >
                    {model}
                  </span>
                  {fallbackModels
                    .split(',')
                    .map((m) => m.trim())
                    .filter(Boolean)
                    .map((fb, idx) => (
                      <React.Fragment key={idx}>
                        <ArrowRight className="w-3 h-3 text-slate-600 shrink-0" />
                        <span
                          className={`px-2 py-0.5 rounded border ${
                            streamProgress.failovers.some((f) => f.failedModel === fb)
                              ? 'bg-amber-950/60 border-amber-800 text-amber-400 line-through'
                              : streamProgress.currentModel === fb
                              ? 'bg-amber-950/80 border-amber-600 text-amber-300 animate-pulse'
                              : 'bg-dark-900 border-dark-700 text-slate-400'
                          }`}
                        >
                          {fb}
                        </span>
                      </React.Fragment>
                    ))}
                </div>
              </div>
            )}

            {/* AI Advisory Disclaimer */}
            <div className="p-3 bg-dark-950/80 border border-dark-700/80 rounded-lg flex items-start gap-2.5 text-slate-400 text-[11px] leading-relaxed">
              <AlertCircle className="w-4 h-4 text-amber-400/90 shrink-0 mt-0.5" />
              <span>
                AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.
              </span>
            </div>

            {/* Run Analysis Action */}
            <div className="pt-2 flex justify-end">
              <button
                onClick={handleRunAnalysis}
                disabled={isDiagnosing || isAiUnavailable}
                className="bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium px-4 py-2 rounded-lg text-xs flex items-center gap-2 transition shadow-md cursor-pointer disabled:cursor-not-allowed"
              >
                {isDiagnosing ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Running analysis ({formatElapsed(elapsedSeconds)})...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Run AI Analysis</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {/* Structured AI Analysis Result */}
        {analysisResult && (
          <div className="space-y-4 animate-in fade-in">
            {/* Header info - 2-row layout */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Shield className="w-4 h-4 text-emerald-400" />
                  <span className="font-semibold text-slate-200">
                    {selectedLogs.length} Log{selectedLogs.length === 1 ? '' : 's'} Inspected
                  </span>
                  {preview && (
                    <span className="text-slate-400 font-mono text-[11px]">
                      ({preview.source_alias} • {preview.app_name})
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 font-mono text-[11px] bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                  <span className="text-slate-400 text-[10px] uppercase font-semibold">Model:</span>
                  <span className="text-accent-400 font-medium">{analysisResult.model_used}</span>
                  {analysisResult.fallback_used && (
                    <span className="bg-amber-950/80 border border-amber-700 text-amber-300 px-1.5 py-0.5 rounded text-[9px] font-semibold tracking-wide uppercase">
                      Fallback
                    </span>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between pt-1.5 border-t border-dark-800 text-[11px] font-mono text-slate-400 gap-2">
                <span>
                  Total Tokens: <span className="text-slate-100 font-semibold">{analysisResult.tokens_used.toLocaleString()}</span>
                </span>
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  {analysisResult.tokens_in !== undefined && (
                    <span className="bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 px-1.5 py-0.5 rounded">
                      ↓ {analysisResult.tokens_in.toLocaleString()} in
                    </span>
                  )}
                  {analysisResult.tokens_out !== undefined && (
                    <span className="bg-sky-950/60 border border-sky-800/80 text-sky-300 px-1.5 py-0.5 rounded">
                      ↑ {analysisResult.tokens_out.toLocaleString()} out
                    </span>
                  )}
                  {Boolean(analysisResult.tokens_thoughts) && (
                    <span className="bg-purple-950/60 border border-purple-800/80 text-purple-300 px-1.5 py-0.5 rounded">
                      ⚡ {analysisResult.tokens_thoughts!.toLocaleString()} thinking
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Fallback notification banner if failover occurred */}
            {analysisResult.fallback_used && (
              <div className="p-3 bg-amber-950/40 border border-amber-800/70 rounded-lg flex items-start gap-2.5 text-amber-200 text-xs leading-relaxed">
                <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <div className="font-semibold text-amber-300">
                    Model Failover Active
                  </div>
                  <div className="text-[11px] text-amber-200/90">
                    Primary model encountered a temporary 503 overload or timeout. Diagnosis was successfully generated using fallback model <strong className="font-mono text-amber-100">{analysisResult.model_used}</strong>.
                  </div>
                  {analysisResult.fallback_attempts && analysisResult.fallback_attempts.length > 0 && (
                    <div className="pt-1 space-y-0.5">
                      {analysisResult.fallback_attempts.map((att, idx) => (
                        <div key={idx} className="font-mono text-[10px] text-amber-300/80">
                          - {att}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Summary */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-accent-400 uppercase tracking-wider mb-1">
                Summary
              </h4>
              <MarkdownRenderer content={analysisResult.summary} />
            </div>

            {/* Root Cause */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-amber-400 uppercase tracking-wider mb-1">
                Root Cause Analysis
              </h4>
              <MarkdownRenderer content={analysisResult.root_cause} />
            </div>

            {/* Actionable Remediation */}
            <div className="bg-dark-950 p-3 rounded-lg border border-dark-700">
              <h4 className="text-[11px] font-semibold text-emerald-400 uppercase tracking-wider mb-1">
                Actionable Remediation
              </h4>
              <MarkdownRenderer content={analysisResult.remediation} />
            </div>

            {/* AI Advisory Disclaimer */}
            <div className="p-3 bg-dark-950/80 border border-dark-700/80 rounded-lg flex items-start gap-2.5 text-slate-400 text-[11px] leading-relaxed">
              <AlertCircle className="w-4 h-4 text-amber-400/90 shrink-0 mt-0.5" />
              <span>
                AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.
              </span>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => setAnalysisResult(null)}
                className="text-xs text-slate-400 hover:text-slate-200 underline cursor-pointer"
              >
                Back to Preview & Edit
              </button>

              <button
                type="button"
                onClick={onClose}
                className="px-3.5 py-2 text-xs bg-dark-700 hover:bg-dark-600 text-slate-200 rounded-lg transition cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};

export default AiAnalysisModal;
