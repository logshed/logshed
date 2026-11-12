import React from 'react';
import { AlertCircle, Brain, Info, Lock, RefreshCw, RotateCcw } from 'lucide-react';
import { MASKED_SECRET } from '../../api/settings.ts';
import { DEFAULT_PROVIDER_MODELS, DEFAULT_SYSTEM_PROMPT, aiModelOption } from '../../utils/aiPrompt.ts';
import { aiProviderOptions, getAiProvider, providerNeedsApiKey } from '../../utils/aiProviders.ts';
import { formatLocalTimestamp } from '../../utils/formatters.ts';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { ModelChainEditor } from '../ai/ModelChainEditor.tsx';
import type { AiProviderConfig } from './useAiProviderConfig.ts';

// Base URLs of common servers that accept OpenAI-style requests. Ollama and LM Studio run on the
// Docker host, which the LogShed container reaches as host.docker.internal.
const OPENAI_COMPATIBLE_PRESETS: { label: string; url: string }[] = [
  { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  { label: 'Ollama (Docker host)', url: 'http://host.docker.internal:11434/v1' },
  { label: 'LM Studio (Docker host)', url: 'http://host.docker.internal:1234/v1' },
  { label: 'Groq', url: 'https://api.groq.com/openai/v1' },
  { label: 'Mistral', url: 'https://api.mistral.ai/v1' },
  { label: 'DeepSeek', url: 'https://api.deepseek.com' },
];

/** The preset URL the base URL matches (ignoring a trailing slash), or '' for a custom URL. */
const BASE_URL_PRESET_OPTIONS: SelectOption[] = [
  { value: '', label: 'Custom' },
  ...OPENAI_COMPATIBLE_PRESETS.map((p) => ({ value: p.url, label: p.label })),
];

const AI_PROVIDER_OPTIONS: SelectOption[] = aiProviderOptions(4);

function matchBaseUrlPreset(baseUrl: string): string {
  const clean = baseUrl.trim().replace(/\/+$/, '');
  return OPENAI_COMPATIBLE_PRESETS.find((p) => p.url === clean)?.url ?? '';
}

export interface AiProviderCardProps {
  ai: AiProviderConfig;
  /** Enter in the API key field saves the whole form. */
  onApiKeyEnter: () => void;
  serverTimezone?: string;
}

/** Settings > Application: AI on or off, the provider, its key and base URL, models and system instructions. */
export const AiProviderCard: React.FC<AiProviderCardProps> = ({ ai, onApiKeyEnter, serverTimezone }) => {
  const {
    aiEnabled,
    setAiEnabled,
    aiProvider,
    aiModel,
    aiApiKey,
    setAiApiKey,
    aiBaseUrl,
    setAiBaseUrl,
    aiSystemPrompt,
    setAiSystemPrompt,
    isAiSystemPromptModified,
    availableModels,
    isLoadingModels,
    modelsError,
    hasApiKeyForProvider,
    modelsCachedAt,
    isCustomModel,
    setIsCustomModel,
    chainEditorKey,
    fallbackList,
    loadModels,
    handleProviderChange,
    updateFallbackModels,
    handlePrimaryModelChange,
    promoteFirstFallback,
    refreshModels: handleRefreshModels,
  } = ai;

  return (
    <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Brain className="w-4 h-4 text-accent-500" />
            <span>On-Demand AI Provider Configuration</span>
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Fernet encryption secures API keys against exposure in database exports, disk clones, and backups.
          </p>
        </div>
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-950/60 border border-emerald-800 text-[11px] font-mono text-emerald-400 shrink-0 self-start sm:self-auto">
          <Lock className="w-3 h-3 text-emerald-400" />
          <span>Keys encrypted at rest</span>
        </div>
      </div>

      {/* AI Enablement Checkbox */}
      <div className="p-3 bg-dark-950/80 border border-dark-800 rounded-lg flex items-start gap-3">
        <input
          type="checkbox"
          id="ai-enabled-checkbox"
          checked={aiEnabled}
          onChange={(e) => {
            const nextVal = e.target.checked;
            setAiEnabled(nextVal);
            if (nextVal && availableModels.length === 0) {
              loadModels(aiProvider);
            }
          }}
          className="mt-0.5 w-4 h-4 rounded border-dark-700 bg-dark-900 text-accent-500 focus:ring-accent-500 focus:ring-offset-dark-900 cursor-pointer"
        />
        <div className="flex-1">
          <label htmlFor="ai-enabled-checkbox" className="text-xs font-semibold text-slate-200 cursor-pointer select-none">
            Enable AI Features
          </label>
          <p className="text-[11px] text-slate-400 mt-0.5">
            Enable log inspection, incident summaries, and alert enrichment via AI models.
          </p>
        </div>
      </div>

      {!aiEnabled ? (
        <div className="p-3.5 bg-dark-900 border border-dark-700 rounded-xl flex items-start gap-2.5 text-xs shadow-xs">
          <Info className="w-4 h-4 text-accent-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-slate-200">AI Features Disabled</p>
            <p className="text-slate-400 mt-0.5">
              AI settings are hidden while disabled. Existing alert rules configured with AI enrichment will continue to trigger and dispatch notifications, but will not be enriched.
            </p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label id="ai-provider-label" htmlFor="ai-provider" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
            AI Provider
          </label>
          <SelectDropdown
            variant="panel"
            fieldClassName="bg-dark-950 border-dark-700 rounded px-3 py-2 font-mono text-slate-200"
            id="ai-provider"
            labelledBy="ai-provider-label"
            label="AI Provider"
            options={AI_PROVIDER_OPTIONS}
            value={aiProvider}
            onChange={(v) => handleProviderChange(v as any)}
          />
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
            API Key (Masked)
          </label>
          <input
            type="password"
            value={aiApiKey}
            onChange={(e) => setAiApiKey(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                onApiKeyEnter();
              }
            }}
            placeholder={hasApiKeyForProvider ? `Enter new API key or leave ${MASKED_SECRET} to preserve` : 'Enter API key'}
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono"
          />
        </div>

        {getAiProvider(aiProvider)?.customBaseUrl && (
          <div className="sm:col-span-2">
            <label htmlFor="ai-base-url" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Custom Base URL (Optional)
            </label>
            <div className="flex flex-col sm:flex-row gap-2">
              <div className="sm:w-56 shrink-0">
                <SelectDropdown
                  variant="panel"
                  fieldClassName="bg-dark-950 border-dark-700 rounded px-3 py-2 font-mono text-slate-200"
                  label="Base URL preset"
                  options={BASE_URL_PRESET_OPTIONS}
                  value={matchBaseUrlPreset(aiBaseUrl)}
                  onChange={(v) => {
                    const preset = OPENAI_COMPATIBLE_PRESETS.find((p) => p.url === v);
                    if (preset) setAiBaseUrl(preset.url);
                  }}
                />
              </div>
              <input
                id="ai-base-url"
                type="text"
                value={aiBaseUrl}
                onChange={(e) => setAiBaseUrl(e.target.value)}
                placeholder="http://host.docker.internal:11434/v1"
                className="w-full min-w-0 bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono"
              />
            </div>
            <p className="text-[11px] text-slate-500 mt-1">
              Any server that accepts OpenAI-style requests works. Leave empty to use http://localhost:11434/v1.
            </p>
          </div>
        )}

        {/* API Key Missing Notice */}
        {!hasApiKeyForProvider && providerNeedsApiKey(aiProvider) && (
          <div className="sm:col-span-2 p-3 bg-amber-950/40 border border-amber-800/60 rounded-lg flex items-start justify-between gap-3 text-amber-300 text-xs font-mono">
            <div className="flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold text-amber-200">API Key Required to Discover Models:</span>
                <p className="text-[11px] text-amber-300/80 mt-0.5">
                  Enter and save your API key above to query {getAiProvider(aiProvider)?.label ?? 'your endpoint'} and populate available text models.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleRefreshModels}
              disabled={isLoadingModels}
              className="shrink-0 text-xs text-amber-200 hover:text-amber-100 underline cursor-pointer disabled:opacity-50 mt-0.5"
            >
              Retry
            </button>
          </div>
        )}

        {/* Models Error Banner */}
        {modelsError && (hasApiKeyForProvider || !providerNeedsApiKey(aiProvider)) && (
          <div className="sm:col-span-2 p-2.5 bg-dark-950 border border-dark-700 rounded-lg flex items-center justify-between text-xs font-mono text-slate-400">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              <span className="text-[11px] text-amber-300/90">{modelsError}</span>
            </div>
            <button
              type="button"
              onClick={handleRefreshModels}
              disabled={isLoadingModels}
              className="text-[10px] text-accent-400 hover:text-accent-300 underline cursor-pointer disabled:opacity-50"
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
            <p className="text-[11px] text-slate-400">
              Select your default primary model and configure fallback models in priority order.{modelsCachedAt ? ` (Cache refreshed: ${formatLocalTimestamp(modelsCachedAt, true, serverTimezone)})` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={handleRefreshModels}
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
            <label id="ai-model-label" htmlFor="ai-model" className="block text-[11px] font-semibold text-slate-400 uppercase">
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
            <SelectDropdown
              variant="panel"
              fieldClassName="bg-dark-950 border-dark-700 rounded px-3 py-2 font-mono text-slate-100"
              id="ai-model"
              labelledBy="ai-model-label"
              label="Default Primary Model"
              options={[
                ...availableModels.filter((m) => m.id === aiModel || !fallbackList.includes(m.id)).map(aiModelOption),
                { value: '__custom__', label: 'Custom model name...' },
              ]}
              value={availableModels.some((m) => m.id === aiModel) ? aiModel : '__custom__'}
              onChange={(v) => {
                if (v === '__custom__') {
                  setIsCustomModel(true);
                } else {
                  handlePrimaryModelChange(v);
                }
              }}
            />
          ) : (
            <input
              id="ai-model"
              type="text"
              value={aiModel}
              onChange={(e) => handlePrimaryModelChange(e.target.value)}
              placeholder={DEFAULT_PROVIDER_MODELS[aiProvider] ?? DEFAULT_PROVIDER_MODELS.openai_compatible}
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono"
            />
          )}

          <p className="text-[11px] text-slate-400 mt-1">
            Queried first for all log inspection requests.
          </p>
        </div>

        <ModelChainEditor
          key={chainEditorKey}
          size="roomy"
          heading="Fallback Models (Sequential Order)"
          emptyText="No fallback models configured. Add a model below to enable automatic failover."
          fallbacks={fallbackList}
          primaryModel={aiModel}
          availableModels={availableModels}
          onChange={(next) => updateFallbackModels(next.join(', '))}
          onPromoteFirst={promoteFirstFallback}
        />

        {/* AI System Instructions Card */}
        <div className="sm:col-span-2 pt-3 border-t border-dark-800 space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <label className="block text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                AI System Instructions (LLM Persona)
              </label>
              <p className="text-[11px] text-slate-400">
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
            className="w-full bg-dark-950 border border-dark-700 rounded-lg p-3 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-accent-500 font-mono leading-relaxed resize-y"
          />
        </div>
      </div>
      )}
    </section>
  );
};
