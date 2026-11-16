import { useMemo, useState } from 'react';
import { AiModelInfo } from '../../types.ts';
import { SettingsResponseData, MASKED_SECRET } from '../../api/settings.ts';
import { getAiModels } from '../../api/ai.ts';
import { DEFAULT_AI_MODEL, DEFAULT_PROVIDER_MODELS, DEFAULT_SYSTEM_PROMPT, normalizePrompt } from '../../utils/aiPrompt.ts';
import { DEFAULT_AI_PROVIDER, providerNeedsApiKey, type AiProviderId } from '../../utils/aiProviders.ts';
import { parseFallbackModels, promoteFirstFallback as swapFirstFallback } from '../../utils/modelChain.ts';

export interface ProviderLocalState {
  apiKey: string;
  hasKey: boolean;
  model: string;
  fallbackModels: string;
  baseUrl: string;
}

const defaultProviderStates = (): Record<string, ProviderLocalState> => ({
  gemini: { apiKey: '', hasKey: false, model: DEFAULT_PROVIDER_MODELS.gemini, fallbackModels: '', baseUrl: '' },
  openai: { apiKey: '', hasKey: false, model: DEFAULT_PROVIDER_MODELS.openai, fallbackModels: '', baseUrl: '' },
  anthropic: { apiKey: '', hasKey: false, model: DEFAULT_PROVIDER_MODELS.anthropic, fallbackModels: '', baseUrl: '' },
  openai_compatible: { apiKey: '', hasKey: true, model: DEFAULT_PROVIDER_MODELS.openai_compatible, fallbackModels: '', baseUrl: '' },
});

/**
 * Builds the per-provider form state from a settings response. The same function produces the
 * baseline the dirty check compares against, so loading and comparing can never disagree.
 * The MASKED_SECRET placeholder only appears when the server says a key is stored: a keyless
 * OpenAI-compatible provider has an empty key field and an empty baseline.
 */
export const buildProviderStates = (
  settRes: SettingsResponseData,
): { configs: Record<string, ProviderLocalState>; activeProvider: AiProviderId } => {
  const configs = defaultProviderStates();

  if (settRes.ai_providers_config) {
    for (const [p, pCfg] of Object.entries(settRes.ai_providers_config)) {
      if (configs[p]) {
        configs[p] = {
          apiKey: pCfg.has_api_key ? (pCfg.ai_api_key || '') : '',
          hasKey: pCfg.has_api_key,
          model: pCfg.ai_model || DEFAULT_PROVIDER_MODELS[p] || DEFAULT_AI_MODEL,
          fallbackModels: pCfg.ai_fallback_models || '',
          baseUrl: pCfg.ai_base_url || '',
        };
      }
    }
  }

  const activeProvider = (settRes.ai_provider || DEFAULT_AI_PROVIDER) as AiProviderId;
  if (configs[activeProvider]) {
    configs[activeProvider] = {
      apiKey: settRes.has_ai_api_key ? (settRes.ai_api_key || '') : '',
      hasKey: settRes.has_ai_api_key,
      model: settRes.ai_model || configs[activeProvider].model || DEFAULT_PROVIDER_MODELS[activeProvider] || DEFAULT_AI_MODEL,
      fallbackModels: settRes.ai_fallback_models !== undefined ? settRes.ai_fallback_models : configs[activeProvider].fallbackModels,
      baseUrl: settRes.ai_base_url || configs[activeProvider].baseUrl,
    };
  }
  return { configs, activeProvider };
};

/** Whether AI is on in a settings response (older servers have no ai_enabled: a stored key means on). */
export const settingsAiEnabled = (settRes: SettingsResponseData): boolean =>
  settRes.ai_enabled !== undefined ? settRes.ai_enabled : Boolean(settRes.has_ai_api_key);

/**
 * The AI provider form on Settings > Application: the provider, its key, base URL, primary and
 * fallback models (kept per provider while the user switches between them), the model list
 * discovered from the provider, and the system instructions.
 */
export function useAiProviderConfig(settings: SettingsResponseData | null) {
  const [providerConfigs, setProviderConfigs] = useState<Record<string, ProviderLocalState>>(defaultProviderStates);

  const [aiEnabled, setAiEnabled] = useState<boolean>(false);
  const [aiProvider, setAiProvider] = useState<AiProviderId>(DEFAULT_AI_PROVIDER);
  const [aiModel, setAiModel] = useState<string>(DEFAULT_AI_MODEL);
  const [aiFallbackModels, setAiFallbackModels] = useState<string>('');
  const [aiApiKey, setAiApiKey] = useState<string>('');
  const [aiBaseUrl, setAiBaseUrl] = useState<string>('');
  const [aiSystemPrompt, setAiSystemPrompt] = useState<string>(DEFAULT_SYSTEM_PROMPT);

  // Model discovery states
  const [availableModels, setAvailableModels] = useState<AiModelInfo[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState<boolean>(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [hasApiKeyForProvider, setHasApiKeyForProvider] = useState<boolean>(true);
  const [modelsCachedAt, setModelsCachedAt] = useState<string | null>(null);
  const [isCustomModel, setIsCustomModel] = useState<boolean>(false);
  // A new key clears the fallback model picker (after a provider change or a reset)
  const [chainEditorKey, setChainEditorKey] = useState<number>(0);

  // Every provider is compared with its own baseline, not just the active one, so an edit made to
  // a provider that is no longer selected still counts as unsaved. The active provider's live
  // fields are held in the individual states; the others in providerConfigs.
  const baselineProviders = useMemo(() => (settings ? buildProviderStates(settings) : null), [settings]);
  const providersDirty = (() => {
    if (!baselineProviders) return false;
    const live: Record<string, ProviderLocalState> = {
      ...providerConfigs,
      [aiProvider]: {
        apiKey: aiApiKey,
        hasKey: hasApiKeyForProvider,
        model: aiModel,
        fallbackModels: aiFallbackModels,
        baseUrl: aiBaseUrl,
      },
    };
    return Object.entries(live).some(([p, cfg]) => {
      const base = baselineProviders.configs[p];
      if (!base) return false;
      return (
        cfg.apiKey !== base.apiKey ||
        cfg.model !== base.model ||
        cfg.fallbackModels !== base.fallbackModels ||
        cfg.baseUrl !== base.baseUrl
      );
    });
  })();

  const isDirty = Boolean(
    settings &&
    (aiEnabled !== settingsAiEnabled(settings) ||
      aiProvider !== settings.ai_provider ||
      providersDirty ||
      normalizePrompt(aiSystemPrompt) !== normalizePrompt(settings.ai_system_prompt || DEFAULT_SYSTEM_PROMPT))
  );

  // Track if AI system instructions differ from system default
  const isAiSystemPromptModified = normalizePrompt(aiSystemPrompt) !== normalizePrompt(DEFAULT_SYSTEM_PROMPT);

  /** Replaces the form with a settings response. */
  const applySettings = (settRes: SettingsResponseData) => {
    const { configs: initialConfigs, activeProvider: activeP } = buildProviderStates(settRes);
    setProviderConfigs(initialConfigs);

    const curActive = initialConfigs[activeP];
    setAiEnabled(settingsAiEnabled(settRes));
    setAiProvider(activeP);
    setAiModel(curActive.model);
    setAiFallbackModels(curActive.fallbackModels);
    setAiApiKey(curActive.apiKey);
    setHasApiKeyForProvider(curActive.hasKey);
    setAiBaseUrl(curActive.baseUrl);
    setAiSystemPrompt(settRes.ai_system_prompt || DEFAULT_SYSTEM_PROMPT);
  };

  /** Back to the dropdown for the primary model, with the fallback picker cleared. */
  const resetModelPickers = () => {
    setIsCustomModel(false);
    setChainEditorKey((k) => k + 1);
  };

  const loadModels = async (
    provider: string,
    forceRefresh: boolean = false,
    customApiKey?: string,
    customBaseUrl?: string,
  ) => {
    try {
      setIsLoadingModels(true);
      setModelsError(null);
      const res = (customApiKey !== undefined || customBaseUrl !== undefined)
        ? await getAiModels(provider, forceRefresh, customApiKey, customBaseUrl)
        : await getAiModels(provider, forceRefresh);
      const discovered = res.models || [];
      setAvailableModels(discovered);
      setHasApiKeyForProvider(res.has_api_key);
      setModelsCachedAt(res.cached_at || null);

      if (res.has_api_key && customApiKey) {
        setAiApiKey(MASKED_SECRET);
        setProviderConfigs((prev) => ({
          ...prev,
          [provider]: {
            ...prev[provider],
            apiKey: MASKED_SECRET,
            hasKey: true,
          },
        }));
      }

      if (discovered.length > 0) {
        setAiModel((currentModel) => {
          if (!isCustomModel && !discovered.some((m) => m.id === currentModel)) {
            const defaultForProvider = DEFAULT_PROVIDER_MODELS[provider];
            const hasDefault = discovered.some((m) => m.id === defaultForProvider);
            const chosen = hasDefault ? defaultForProvider : discovered[0].id;
            setProviderConfigs((prev) => ({
              ...prev,
              [provider]: {
                ...prev[provider],
                model: chosen,
              },
            }));
            return chosen;
          }
          return currentModel;
        });
      }

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

  const handleProviderChange = (newProvider: AiProviderId) => {
    const updatedConfigs: Record<string, ProviderLocalState> = {
      ...providerConfigs,
      [aiProvider]: {
        apiKey: aiApiKey,
        hasKey: hasApiKeyForProvider,
        model: aiModel,
        fallbackModels: aiFallbackModels,
        baseUrl: aiBaseUrl,
      },
    };
    setProviderConfigs(updatedConfigs);

    const target = updatedConfigs[newProvider] || {
      apiKey: '',
      hasKey: !providerNeedsApiKey(newProvider),
      model: DEFAULT_PROVIDER_MODELS[newProvider] || DEFAULT_AI_MODEL,
      fallbackModels: '',
      baseUrl: '',
    };

    setAiProvider(newProvider);
    setAiApiKey(target.apiKey);
    setHasApiKeyForProvider(target.hasKey);
    setAiModel(target.model || DEFAULT_PROVIDER_MODELS[newProvider] || DEFAULT_AI_MODEL);
    setAiFallbackModels(target.fallbackModels || '');
    setAiBaseUrl(target.baseUrl || '');
    resetModelPickers();
    setModelsError(null);
    setAvailableModels([]);

    loadModels(newProvider);
  };

  const updateFallbackModels = (newFallbacks: string) => {
    setAiFallbackModels(newFallbacks);
    setProviderConfigs((prev) => ({
      ...prev,
      [aiProvider]: {
        ...prev[aiProvider],
        fallbackModels: newFallbacks,
      },
    }));
  };

  const fallbackList = parseFallbackModels(aiFallbackModels);

  const handlePrimaryModelChange = (newModel: string) => {
    setAiModel(newModel);
    setProviderConfigs((prev) => ({
      ...prev,
      [aiProvider]: {
        ...prev[aiProvider],
        model: newModel,
      },
    }));
    if (fallbackList.includes(newModel)) {
      const updated = fallbackList.filter((m) => m !== newModel);
      updateFallbackModels(updated.join(', '));
    }
  };

  // Moving the first fallback up swaps it with the primary model.
  const promoteFirstFallback = () => {
    const swapped = swapFirstFallback(fallbackList, aiModel);
    if (!swapped) return;
    setIsCustomModel(availableModels.length > 0 && !availableModels.some((m) => m.id === swapped.primary));
    setAiModel(swapped.primary);
    setProviderConfigs((prev) => ({
      ...prev,
      [aiProvider]: {
        ...prev[aiProvider],
        model: swapped.primary,
      },
    }));
    updateFallbackModels(swapped.fallbacks.join(', '));
  };

  const refreshModels = async () => {
    const keyToSend = (aiApiKey && aiApiKey !== MASKED_SECRET) ? aiApiKey.trim() : undefined;
    const urlToSend = aiBaseUrl ? aiBaseUrl.trim() : undefined;
    await loadModels(aiProvider, true, keyToSend, urlToSend);
  };

  /**
   * The AI fields of a settings save: only what differs from the saved settings. The selected
   * provider's fields go at the top level, which the server also stores as the single values older
   * versions read; when the provider itself changes, all four go, so those values follow it. Other
   * providers go in ai_providers_config with their changed fields only. An emptied base URL is
   * sent as '' so the server clears it; a key left as the masked placeholder is not sent at all.
   */
  const buildChangedPayload = (): Record<string, unknown> => {
    if (!settings || !baselineProviders) return {};
    const out: Record<string, unknown> = {};
    if (aiEnabled !== settingsAiEnabled(settings)) out.ai_enabled = aiEnabled;
    if (normalizePrompt(aiSystemPrompt) !== normalizePrompt(settings.ai_system_prompt || DEFAULT_SYSTEM_PROMPT)) {
      out.ai_system_prompt = aiSystemPrompt;
    }

    const live: Record<string, ProviderLocalState> = {
      ...providerConfigs,
      [aiProvider]: { apiKey: aiApiKey, hasKey: hasApiKeyForProvider, model: aiModel, fallbackModels: aiFallbackModels, baseUrl: aiBaseUrl },
    };
    const providerChanged = aiProvider !== settings.ai_provider;
    const others: Record<string, Record<string, string>> = {};
    for (const [p, cfg] of Object.entries(live)) {
      const base = baselineProviders.configs[p];
      if (!base) continue;
      const fields: Record<string, string> = {};
      const allFields = p === aiProvider && providerChanged;
      if (allFields || cfg.model !== base.model) fields.ai_model = cfg.model;
      if (allFields || cfg.fallbackModels !== base.fallbackModels) fields.ai_fallback_models = cfg.fallbackModels;
      if (allFields || cfg.baseUrl.trim() !== base.baseUrl.trim()) fields.ai_base_url = cfg.baseUrl.trim();
      if ((allFields || cfg.apiKey !== base.apiKey) && cfg.apiKey !== MASKED_SECRET) fields.ai_api_key = cfg.apiKey;
      if (Object.keys(fields).length === 0) continue;
      if (p === aiProvider) Object.assign(out, fields);
      else others[p] = fields;
    }
    if (providerChanged) out.ai_provider = aiProvider;
    if (Object.keys(others).length > 0) out.ai_providers_config = others;
    return out;
  };

  return {
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
    isDirty,
    applySettings,
    resetModelPickers,
    loadModels,
    handleProviderChange,
    updateFallbackModels,
    handlePrimaryModelChange,
    promoteFirstFallback,
    refreshModels,
    buildChangedPayload,
  };
}

export type AiProviderConfig = ReturnType<typeof useAiProviderConfig>;
