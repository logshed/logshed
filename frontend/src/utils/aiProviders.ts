/**
 * The AI providers LogShed can call, as the web UI sees them. The backend's list is
 * AI_PROVIDERS in backend/app/core/ai_providers.py; test_frontend_limits.py keeps the ids,
 * labels, key requirement and default models of the two in step. Model lists are not kept
 * here: they are fetched live from each provider.
 */

export type AiProviderId = 'gemini' | 'openai' | 'anthropic' | 'openai_compatible';

export interface AiProviderEntry {
  id: AiProviderId;
  label: string;
  // False for servers that may run without a key (a local model server, for example)
  needsApiKey: boolean;
  // The model used when none is chosen
  defaultModel: string;
  // Whether the user enters the server's base URL
  customBaseUrl: boolean;
  // Services the provider covers, shown after the label in the provider picker
  examples: string[];
}

export const AI_PROVIDERS: AiProviderEntry[] = [
  { id: 'gemini', label: 'Google Gemini', needsApiKey: true, defaultModel: 'gemini-3.7-flash', customBaseUrl: false, examples: [] },
  { id: 'openai', label: 'OpenAI', needsApiKey: true, defaultModel: 'gpt-6.1-sol', customBaseUrl: false, examples: [] },
  { id: 'anthropic', label: 'Anthropic Claude', needsApiKey: true, defaultModel: 'claude-sonnet-5-5', customBaseUrl: false, examples: [] },
  {
    id: 'openai_compatible',
    label: 'OpenAI-Compatible',
    needsApiKey: false,
    defaultModel: 'llama3.2',
    customBaseUrl: true,
    examples: ['OpenRouter', 'Ollama', 'LM Studio', 'Groq'],
  },
];

// The provider used when none was ever saved
export const DEFAULT_AI_PROVIDER: AiProviderId = 'gemini';

export function getAiProvider(id: string | null | undefined): AiProviderEntry | undefined {
  return AI_PROVIDERS.find((p) => p.id === id);
}

/** Whether calls to this provider need an API key. Unknown ids count as needing one. */
export function providerNeedsApiKey(id: string | null | undefined): boolean {
  return getAiProvider(id)?.needsApiKey ?? true;
}

/** The provider picker's options; maxExamples limits the services named after a label. */
export function aiProviderOptions(maxExamples: number): { value: AiProviderId; label: string }[] {
  return AI_PROVIDERS.map((p) => {
    const shown = p.examples.slice(0, maxExamples);
    const suffix = shown.length ? ` (${shown.join(', ')}, ...)` : '';
    return { value: p.id, label: `${p.label}${suffix}` };
  });
}
