import { describe, it, expect } from 'vitest';
import {
  DEFAULT_SYSTEM_PROMPT,
  SYSTEM_INSTRUCTIONS_HEADER,
  USER_ANALYSIS_PROMPT_HEADER,
  buildFullEnvelope,
  parseFullEnvelope,
  normalizePrompt,
  getOrdinalSuffix,
  isTextModel,
  aiModelHint,
  aiModelOption,
} from '../utils/aiPrompt.ts';
import { isOpenRouterUrl } from '../utils/aiProviders.ts';

describe('aiPrompt utilities', () => {
  it('buildFullEnvelope joins system instructions and user analysis prompt with headers', () => {
    const sys = 'You are a test assistant.';
    const user = 'Here are the logs.';
    const envelope = buildFullEnvelope(sys, user);

    expect(envelope).toContain(SYSTEM_INSTRUCTIONS_HEADER);
    expect(envelope).toContain('You are a test assistant.');
    expect(envelope).toContain(USER_ANALYSIS_PROMPT_HEADER);
    expect(envelope).toContain('Here are the logs.');
  });

  it('buildFullEnvelope falls back to DEFAULT_SYSTEM_PROMPT when system prompt is empty', () => {
    const envelope = buildFullEnvelope('', 'User logs');
    expect(envelope).toContain(DEFAULT_SYSTEM_PROMPT.trim());
    expect(envelope).toContain('User logs');
  });

  it('parseFullEnvelope correctly parses envelope with both headers', () => {
    const customSys = 'Custom instructions for debugging.';
    const customUser = 'Custom logs payload.';
    const envelope = `${SYSTEM_INSTRUCTIONS_HEADER}\n${customSys}\n\n${USER_ANALYSIS_PROMPT_HEADER}\n${customUser}`;

    const parsed = parseFullEnvelope(envelope, DEFAULT_SYSTEM_PROMPT);
    expect(parsed.systemPrompt).toBe(customSys);
    expect(parsed.userPrompt).toBe(customUser);
  });

  it('parseFullEnvelope gracefully handles edited text missing standard headers', () => {
    const rawEdited = 'Just raw user prompt text without delimiter headers';
    const parsed = parseFullEnvelope(rawEdited, 'Fallback System Prompt');
    expect(parsed.systemPrompt).toBe('Fallback System Prompt');
    expect(parsed.userPrompt).toBe(rawEdited);
  });

  it('DEFAULT_SYSTEM_PROMPT uses universal terminology without Americanisms', () => {
    expect(DEFAULT_SYSTEM_PROMPT).toContain('redacted server/container logs');
    expect(DEFAULT_SYSTEM_PROMPT).not.toContain('sanitized');
    expect(DEFAULT_SYSTEM_PROMPT).not.toContain('analyze');
  });

  it('normalizePrompt trims whitespace and normalizes CRLF to LF', () => {
    expect(normalizePrompt('  hello\r\nworld  \n')).toBe('hello\nworld');
    expect(normalizePrompt(null)).toBe('');
    expect(normalizePrompt(undefined)).toBe('');
    expect(normalizePrompt('line1\nline2')).toBe('line1\nline2');
    expect(normalizePrompt('line1\r\nline2')).toBe('line1\nline2');
    expect(normalizePrompt(DEFAULT_SYSTEM_PROMPT.replace(/\n/g, '\r\n'))).toBe(
      normalizePrompt(DEFAULT_SYSTEM_PROMPT)
    );
  });

  it('getOrdinalSuffix formats ordinal numbers correctly (1st, 2nd, 3rd, 4th, 11th, etc.)', () => {
    expect(getOrdinalSuffix(1)).toBe('1st');
    expect(getOrdinalSuffix(2)).toBe('2nd');
    expect(getOrdinalSuffix(3)).toBe('3rd');
    expect(getOrdinalSuffix(4)).toBe('4th');
    expect(getOrdinalSuffix(10)).toBe('10th');
    expect(getOrdinalSuffix(11)).toBe('11th');
    expect(getOrdinalSuffix(12)).toBe('12th');
    expect(getOrdinalSuffix(13)).toBe('13th');
    expect(getOrdinalSuffix(21)).toBe('21st');
    expect(getOrdinalSuffix(22)).toBe('22nd');
    expect(getOrdinalSuffix(23)).toBe('23rd');
    expect(getOrdinalSuffix(24)).toBe('24th');
  });

  it('isTextModel identifies text generation models and filters out non-text models', () => {
    // Valid text models
    expect(isTextModel('gemini-3.7-flash')).toBe(true);
    expect(isTextModel('gemini-3.8-flash')).toBe(true);
    expect(isTextModel('gpt-4o')).toBe(true);
    expect(isTextModel('gpt-4o-mini')).toBe(true);
    expect(isTextModel('llama3.2:3b')).toBe(true);
    expect(isTextModel('mistral-large')).toBe(true);

    // Audio / Speech / Transcribe models
    expect(isTextModel('transcribe')).toBe(false);
    expect(isTextModel('whisper-1')).toBe(false);
    expect(isTextModel('tts-1')).toBe(false);
    expect(isTextModel('speech-to-text')).toBe(false);
    expect(isTextModel('gpt-4o-realtime-preview')).toBe(false);

    // Image / Video models
    expect(isTextModel('image')).toBe(false);
    expect(isTextModel('imagen-3')).toBe(false);
    expect(isTextModel('dall-e-3')).toBe(false);
    expect(isTextModel('stable-diffusion-xl')).toBe(false);
    expect(isTextModel('veo-2')).toBe(false);

    // Embeddings / Moderation
    expect(isTextModel('text-embedding-3-small')).toBe(false);
    expect(isTextModel('text-moderation-latest')).toBe(false);

    // Computer-use models
    expect(isTextModel('gemini-2.5-computer-use-preview-10-2025')).toBe(false);
    expect(isTextModel('claude-3-7-sonnet-computer-use')).toBe(false);

    // Empty or invalid input
    expect(isTextModel('')).toBe(false);
  });
});


describe('model rows', () => {
  it('give the price per million tokens and the context size where the provider reports them', () => {
    const m = { id: 'openai/gpt-6-luna', name: 'GPT-6 Luna', supports_thinking: true, context_length: 1050000, prompt_price: 0.1, completion_price: 0.5 };
    expect(aiModelOption(m)).toEqual({
      value: 'openai/gpt-6-luna',
      label: 'openai/gpt-6-luna [Reasoning]',
      hint: '$0.1 in / $0.5 out per 1M tokens, 1.05M context',
    });
    expect(aiModelHint({ ...m, prompt_price: 0.075, completion_price: 15, context_length: 128000 })).toBe(
      '$0.075 in / $15 out per 1M tokens, 128K context',
    );
    expect(aiModelHint({ ...m, prompt_price: 0, completion_price: 0, context_length: 2000000 })).toBe('Free, 2M context');
  });

  it('leave out a price that is unknown or varies (a router) and say when a model is being retired', () => {
    expect(aiModelHint({ id: 'openrouter/auto', name: 'Auto', prompt_price: null, completion_price: null, context_length: 2000000 })).toBe('2M context');
    expect(aiModelHint({ id: 'z-ai/glm-5.1', name: 'GLM', is_deprecated: true })).toBe('being retired');
  });

  it('have no hint for providers that report neither (Gemini, OpenAI, Anthropic)', () => {
    expect(aiModelOption({ id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: false })).toEqual({
      value: 'gemini-3.7-flash',
      label: 'gemini-3.7-flash',
    });
  });
});

describe('isOpenRouterUrl', () => {
  it('is true for openrouter.ai and its subdomains only, as the server decides', () => {
    expect(isOpenRouterUrl('https://openrouter.ai/api/v1')).toBe(true);
    expect(isOpenRouterUrl(' https://eu.OpenRouter.ai/api/v1/ ')).toBe(true);
    expect(isOpenRouterUrl('https://notopenrouter.ai/api/v1')).toBe(false);
    expect(isOpenRouterUrl('https://openrouter.ai.example.com/v1')).toBe(false);
    expect(isOpenRouterUrl('')).toBe(false);
    expect(isOpenRouterUrl('not a url')).toBe(false);
  });
});
